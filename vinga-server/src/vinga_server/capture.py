"""Recording a session to disk so it can be analysed offline.

`barge_in` misfiring (#28) is an acoustic defect. Nothing in the test
lanes can reproduce it: the unit lane feeds synthetic frames and the
integration lane drives a simulator over a websocket, and both bypass
the microphone, the board's echo cancellation, and the room. The
parameter that decides whether the assistant interrupts itself is how
much of its own voice survives the board's echo cancellation and reaches
the endpointer, and that number is unknown. Tuning against an invented
figure gives a fix that tests clean and fails on the street.

What was missing was not another test but the recording that lets the
tests be written against reality. Three files per session:

- `<session>.wav`, stereo 16 kHz s16le. Channel 0 is the decoded
  microphone as received, channel 1 is what was actually paced out to
  the speaker. Stereo rather than two files because alignment then
  costs nothing: sample N in both channels is the same instant, so echo
  leakage becomes a measurement (cross-correlate the channels, read off
  gain and delay) rather than a guess, and the overlap is directly
  audible in any audio editor.
- `<session>.jsonl`, the decision track: every structured event the
  session already emits, plus a `t_ms` offset that indexes into the WAV,
  plus two things the logs do not carry (frames dropped before decode,
  and the endpointer's opinion sampled continuously rather than only
  where it decided something).
- `<session>.json`, the manifest: what the capture was made against,
  because a capture outlives the code that made it.

Everything is stamped against the session's monotonic origin, so the
three files share one timeline.

This writes room audio to disk, which is the opposite of what the rest
of the project promises. It is off unless a directory is configured, and
says so on every session it records.
"""

import contextlib
import json
import shutil
import struct
import time
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import Any, BinaryIO, TextIO

from vinga_server.events import ServerEvents

events = ServerEvents(__name__)

# The rate both channels are written at, which is the rate the input
# side of the pipeline runs at. The reply is resampled down to it so
# that one sample index means one instant in both channels.
CAPTURE_RATE = 16000
CAPTURE_CHANNELS = 2
SAMPLE_BYTES = 2
FRAME_BYTES = CAPTURE_CHANNELS * SAMPLE_BYTES

# A canonical PCM WAV header is 44 bytes, and everything after it is raw
# interleaved samples. Two of its fields are byte counts that can only
# be known at the end, so they are written as placeholders and patched
# on a clean close.
WAV_HEADER_BYTES = 44

# How far behind the present the interleaved writer stays before
# committing samples to the file. Both channels are stamped when their
# audio arrives, so anything still to come belongs after the cursor;
# this is slack for the two arriving in either order within one turn of
# the event loop.
FLUSH_LAG_S = 0.25

MB = 1024 * 1024


def _wav_header(data_bytes: int) -> bytes:
    """A 44 byte canonical PCM header. `data_bytes` is a placeholder
    when the length is not known yet."""
    byte_rate = CAPTURE_RATE * FRAME_BYTES
    return struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_bytes,
        b"WAVE",
        b"fmt ",
        16,
        1,  # PCM
        CAPTURE_CHANNELS,
        CAPTURE_RATE,
        byte_rate,
        FRAME_BYTES,
        8 * SAMPLE_BYTES,
        b"data",
        data_bytes,
    )


def interleave(left: bytes, right: bytes) -> bytes:
    """Two equal-length s16le mono buffers as one stereo buffer.

    Byte slice assignment rather than a per-sample loop: a quarter of an
    hour is fourteen million samples, and this runs inside a session.
    """
    if len(left) != len(right):
        raise ValueError("channels must be the same length to interleave")
    out = bytearray(len(left) * 2)
    out[0::4] = left[0::2]
    out[1::4] = left[1::2]
    out[2::4] = right[0::2]
    out[3::4] = right[1::2]
    return bytes(out)


class _Channel:
    """One side of the stereo file, buffered until the writer commits.

    Audio is placed by when it arrived rather than by how much has been
    written before it, so a gap in one channel becomes silence rather
    than sliding everything after it out of time with the other channel
    and with the events.
    """

    def __init__(self) -> None:
        self.pending = bytearray()
        # The frame index just past the last audio placed in this
        # channel. Where contiguous audio continues from.
        self.next_frame = 0

    def add(self, pcm: bytes, at_frame: int, start_frame: int) -> None:
        """Place `pcm` at `at_frame`, padding any gap with silence.
        `start_frame` is what index the pending buffer begins at."""
        offset = (at_frame - start_frame) * SAMPLE_BYTES
        end = offset + len(pcm)
        if len(self.pending) < end:
            self.pending.extend(bytes(end - len(self.pending)))
        self.pending[offset:end] = pcm
        self.next_frame = at_frame + len(pcm) // SAMPLE_BYTES

    def take(self, frames: int) -> bytes:
        """The first `frames` frames, silence-padded, removed."""
        want = frames * SAMPLE_BYTES
        if len(self.pending) < want:
            self.pending.extend(bytes(want - len(self.pending)))
        out = bytes(self.pending[:want])
        del self.pending[:want]
        return out


class SessionCapture:
    """One session's three files.

    Writes are best effort by construction: a capture that fails must
    never take a conversation down with it, so every I/O path here
    catches, logs once, and disables itself.
    """

    def __init__(
        self,
        directory: Path,
        session_id: str,
        opened_at: float,
        manifest: dict[str, Any],
        max_session_s: float,
        on_close: "Callable[[str], None] | None" = None,
    ) -> None:
        self._session_id = session_id
        self._opened_at = opened_at
        self._max_session_s = max_session_s
        self.wav_path = directory / f"{session_id}.wav"
        self.jsonl_path = directory / f"{session_id}.jsonl"
        self.manifest_path = directory / f"{session_id}.json"
        self._manifest = dict(manifest)
        self._wav: BinaryIO | None = None
        self._events: TextIO | None = None
        self._mic = _Channel()
        self._reply = _Channel()
        # Where the pending buffers begin, and how much is on disk.
        self._start_frame = 0
        self._data_bytes = 0
        self._stopped = False
        # Dropped frames are counted per second rather than logged per
        # frame: a misfire is explained by a rate, and per-frame records
        # would swamp the decision track.
        self._dropped: dict[str, int] = {}
        self._dropped_second = -1
        # The furthest an event has landed, so the audio can be padded
        # out to cover it rather than leaving offsets past the end.
        # Starts below zero to mean "no events yet", so that a capture
        # with none is not padded to cover an event it never had.
        self._event_frame = -1
        self._on_close = on_close
        # Guards close() against being re-entered from an event written
        # while it is closing.
        self._closing = False

    def start(self) -> None:
        self._wav = self.wav_path.open("wb")
        self._wav.write(_wav_header(0))
        self._events = self.jsonl_path.open("w", encoding="utf-8")
        # Written now rather than at close, because the pod being stopped
        # mid-session is plausibly the session most worth looking at, and
        # a capture with no manifest cannot be interpreted at all.
        self._write_manifest(complete=False)

    def _write_manifest(self, complete: bool, duration_s: float | None = None) -> None:
        payload = dict(self._manifest)
        payload["capture"] = {
            "audio": self.wav_path.name,
            "events": self.jsonl_path.name,
            "sample_rate": CAPTURE_RATE,
            "channels": ["microphone", "reply"],
            # False in a file left behind by a pod that was stopped, so
            # the analysis side knows the WAV header is stale and the
            # length has to come from the file size.
            "complete": complete,
        }
        if duration_s is not None:
            payload["capture"]["duration_s"] = round(duration_s, 3)
        self.manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

    def _at(self, now: float) -> float:
        return now - self._opened_at

    def _frame_of(self, now: float) -> int:
        return max(0, int(self._at(now) * CAPTURE_RATE))

    def _expired(self, now: float) -> bool:
        return self._at(now) >= self._max_session_s

    def _disable(self, doing: str, exc: BaseException) -> None:
        # The class name and never the exception (the PR #153 review).
        # Every caller here catches a bare `Exception` around a write,
        # so what arrives is whatever the filesystem, the wave module or
        # a JSON encoder raised, and those messages carry the path they
        # tripped on, the bytes they choked on, or the value they could
        # not encode. Handing the object itself as a `%` argument was
        # worse than rendering it: `Emission.args` is deliberately not
        # copied for a tap, so a consumer was given the live exception,
        # its chain and everything the chain closes over.
        events.warning(
            "session %s: capture stopped after failing to %s (%s)",
            self._session_id,
            doing,
            type(exc).__name__,
            event="capture_failed",
            session=self._session_id,
            reason=doing,
            failure=type(exc).__name__,
        )
        self._stopped = True
        with contextlib.suppress(Exception):
            self.close()

    def microphone(self, pcm: bytes, now: float) -> None:
        """Mic audio as decoded, before any of the session's guards."""
        self._add(self._mic, pcm, now)

    def reply(self, pcm: bytes, now: float) -> None:
        """Audio as paced out to the device, at the capture rate."""
        self._add(self._reply, pcm, now)

    def _add(self, channel: _Channel, pcm: bytes, now: float) -> None:
        if self._stopped or self._wav is None or not pcm:
            return
        if self._expired(now):
            self._finish_at_limit()
            return
        at = max(channel.next_frame, self._frame_of(now), self._start_frame)
        try:
            channel.add(pcm, at, self._start_frame)
            self._flush(self._frame_of(now) - int(FLUSH_LAG_S * CAPTURE_RATE))
        except Exception as exc:  # noqa: BLE001 - capture never breaks a session
            self._disable("write audio", exc)

    def _flush(self, up_to_frame: int) -> None:
        assert self._wav is not None
        frames = up_to_frame - self._start_frame
        if frames <= 0:
            return
        block = interleave(self._mic.take(frames), self._reply.take(frames))
        self._wav.write(block)
        # Flushed as it goes, because the whole recovery story depends on
        # it: a pod stopped mid-session leaves whatever reached the file,
        # and anything still in a userspace buffer is simply lost. At
        # four writes a second this costs nothing worth counting.
        self._wav.flush()
        self._data_bytes += len(block)
        self._start_frame = up_to_frame

    def _finish_at_limit(self) -> None:
        """End a capture that has run as long as it is allowed to. The
        conversation carries on; only the recording stops."""
        if self._closing:
            return
        events.info(
            "session %s: capture reached its %.0f s limit",
            self._session_id,
            self._max_session_s,
            event="capture_limit",
            session=self._session_id,
        )
        self.close()

    def event(self, payload: dict[str, Any], now: float) -> None:
        """One line of the decision track: the event as logged, plus
        where it lands in the audio.

        Subject to the same limit as the audio. The audio is clamped to
        it on close, so an event written past it would be an offset with
        no audio under it, which is the one thing the decision track
        promises not to be."""
        if self._stopped or self._events is None:
            return
        if self._expired(now):
            self._finish_at_limit()
            return
        self._write_event(payload, now)

    def _write_event(self, payload: dict[str, Any], now: float) -> None:
        """The write itself, without the limit check, so the last
        records a closing capture emits are not turned away by the very
        limit that is closing it.

        The offset is derived from a frame index rather than from the
        clock, and that index is clamped to the limit the audio is
        clamped to. Both halves of the guarantee then come from one
        number: every offset in the track indexes into the WAV, by
        construction rather than by every caller remembering to.
        """
        if self._events is None:
            return
        frame = min(self._frame_of(now), int(self._max_session_s * CAPTURE_RATE))
        record = {"t_ms": round(frame / CAPTURE_RATE * 1000, 1), **payload}
        # An event's offset is only useful if it indexes into the audio,
        # and a session can be open through stretches with no decodable
        # audio at all. Remembering where the last one landed is what
        # lets close() pad the file out to cover it.
        self._event_frame = max(self._event_frame, frame)
        try:
            self._events.write(json.dumps(record, default=str) + "\n")
            # For the same reason the audio is flushed: the events
            # leading up to a hard stop are the ones worth having.
            self._events.flush()
        except Exception as exc:  # noqa: BLE001 - capture never breaks a session
            self._disable("write an event", exc)

    def vad(self, speech_ms: float, listening: bool, replying: bool, now: float) -> None:
        """The endpointer's opinion, sampled every frame rather than only
        where it decided something. This is what turns "barge-in fired
        wrongly" into "the endpointer classified 340 ms of the
        assistant's own voice as speech starting at 12.7 s"."""
        self.event(
            {
                "event": "vad",
                "session": self._session_id,
                "speech_ms": round(speech_ms, 1),
                "listening": listening,
                "replying": replying,
            },
            now,
        )

    def dropped(self, reason: str, now: float) -> None:
        """A frame discarded before it could be decoded. Aggregated per
        second: the guards drop whole seconds of audio at a time, and
        what explains a misfire is the rate, not the individual frame."""
        if self._stopped:
            return
        second = int(self._at(now))
        if second != self._dropped_second:
            self._emit_dropped(now)
            self._dropped_second = second
        self._dropped[reason] = self._dropped.get(reason, 0) + 1

    def _emit_dropped(self, now: float) -> None:
        if not self._dropped:
            return
        self._write_event(
            {
                "event": "frames_dropped",
                "session": self._session_id,
                "second": self._dropped_second,
                "reasons": dict(self._dropped),
            },
            now,
        )
        self._dropped = {}

    def close(self) -> None:
        """Finish the files. Patching the WAV header is what makes a
        capture that ended cleanly self-describing; one that did not is
        still every byte of audio it managed to write, and the manifest
        says which it is."""
        if self._wav is None and self._events is None:
            self._closing = True
            if self._on_close is not None:
                self._on_close(self._session_id)
                self._on_close = None
            return
        self._closing = True
        now = time.monotonic()
        with contextlib.suppress(Exception):
            self._emit_dropped(now)
        wav, events = self._wav, self._events
        self._wav = self._events = None
        if wav is not None:
            with contextlib.suppress(Exception):
                # Out to the furthest of the audio and the last event.
                # An event's offset has to index into the file, and a
                # session can be open through stretches with no
                # decodable audio at all, so the quiet is written as
                # silence rather than left as a timeline the events
                # point past the end of. Bounded by the session limit,
                # which is what already bounds one capture's size.
                # `_event_frame + 1`, not `_event_frame`: a sample at
                # index N only exists once N+1 frames are written, and
                # `t_ms` is rounded to a tenth of a millisecond, which
                # can round up. One frame at this rate is 0.0625 ms
                # against a worst-case rounding of 0.05 ms, so the extra
                # frame covers both. Without it an event landing on the
                # last frame pointed one sample past the end.
                end = max(
                    self._mic.next_frame, self._reply.next_frame, self._event_frame + 1
                )
                self._start_frame = min(end, int(self._max_session_s * CAPTURE_RATE))
                frames = self._start_frame - (self._data_bytes // FRAME_BYTES)
                if frames > 0:
                    block = interleave(self._mic.take(frames), self._reply.take(frames))
                    wav.write(block)
                    self._data_bytes += len(block)
                wav.seek(0)
                wav.write(_wav_header(self._data_bytes))
            with contextlib.suppress(Exception):
                wav.close()
        if events is not None:
            with contextlib.suppress(Exception):
                events.close()
        with contextlib.suppress(Exception):
            self._write_manifest(
                complete=not self._stopped,
                duration_s=self._data_bytes / FRAME_BYTES / CAPTURE_RATE,
            )
        # The store stops protecting this capture from pruning, and
        # checks the budget now that its final size is known.
        if self._on_close is not None:
            with contextlib.suppress(Exception):
                self._on_close(self._session_id)
            self._on_close = None


class CaptureStore:
    """The capture directory: what may be started, and what is kept.

    `/data` is the only writable path in a deployment and it also holds
    agent memory and the model caches, so filling it does not degrade
    capture, it breaks those. Hence both a budget for this directory and
    a floor on free space: a total-size cap does not protect against the
    caches growing underneath it.
    """

    def __init__(
        self,
        directory: Path,
        max_session_s: float,
        max_total_mb: float,
        min_free_mb: float,
    ) -> None:
        self.directory = directory
        self._max_session_s = max_session_s
        self._max_total_mb = max_total_mb
        self._min_free_mb = min_free_mb
        # Sessions recording right now. Pruning must not unlink a file
        # that still has a writer behind it.
        self._active: set[str] = set()

    def _free_mb(self) -> float:
        return shutil.disk_usage(self.directory).free / MB

    def _captures(self) -> list[Path]:
        return sorted(self.directory.glob("*.wav"), key=lambda p: p.stat().st_mtime)

    def _total_mb(self) -> float:
        total = 0
        for path in self.directory.glob("*"):
            with contextlib.suppress(OSError):
                total += path.stat().st_size
        return total / MB

    def prune(self) -> list[str]:
        """Drop whole captures, oldest first, until the directory is
        inside its budget. All three files go together: two thirds of a
        capture is not a capture.

        Two are never dropped. A session still recording, because its
        descriptors are open and unlinking underneath it would leave the
        session writing to a file nobody can find. And the newest
        finished one, because a budget smaller than a single session
        would otherwise delete the recording that was just taken, which
        is the one somebody went out to make.
        """
        removed: list[str] = []
        captures = self._captures()
        protected = set(self._active)
        if captures:
            protected.add(captures[-1].stem)
        candidates = [path for path in captures if path.stem not in protected]
        while candidates and self._total_mb() > self._max_total_mb:
            oldest = candidates.pop(0)
            for path in (oldest, oldest.with_suffix(".jsonl"), oldest.with_suffix(".json")):
                with contextlib.suppress(OSError):
                    path.unlink()
            removed.append(oldest.stem)
        if removed:
            events.info(
                "capture: pruned %d session(s) to stay under %.0f MB: %s",
                len(removed),
                self._max_total_mb,
                ", ".join(removed),
                event="capture_pruned",
                sessions=removed,
            )
        over = self._total_mb()
        if over > self._max_total_mb:
            events.warning(
                "capture: %.0f MB on disk is over the %.0f MB budget and "
                "nothing more can be pruned; raise max_total_mb or lower "
                "max_session_s",
                over,
                self._max_total_mb,
                event="capture_over_budget",
                total_mb=round(over),
            )
        return removed

    def finished(self, session_id: str) -> None:
        """A capture closed. It stops being protected, and the budget is
        checked now that its final size is known: without this a single
        session that overran would sit there until some later session
        happened to start."""
        self._active.discard(session_id)
        with contextlib.suppress(OSError):
            self.prune()

    def open(
        self, session_id: str, opened_at: float, manifest: dict[str, Any]
    ) -> SessionCapture | None:
        """Begin a capture, or answer None having said why not.

        Declining is a warning rather than a failure: an operator who
        turned capture on wants to know it is not recording, and a
        conversation is worth more than a recording of it.
        """
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            self.prune()
            free_mb = self._free_mb()
        except OSError as exc:
            events.warning(
                "session %s: not capturing, %s is unusable (%s)",
                session_id,
                self.directory,
                type(exc).__name__,
                event="capture_declined",
                session=session_id,
                reason="unusable",
                failure=type(exc).__name__,
            )
            return None
        if free_mb < self._min_free_mb:
            events.warning(
                "session %s: not capturing, %.0f MB free is below the %.0f MB floor",
                session_id,
                free_mb,
                self._min_free_mb,
                event="capture_declined",
                session=session_id,
                reason="min_free_mb",
                free_mb=round(free_mb),
            )
            return None
        capture = SessionCapture(
            self.directory,
            session_id,
            opened_at,
            manifest,
            self._max_session_s,
            on_close=self.finished,
        )
        self._active.add(session_id)
        try:
            capture.start()
        except OSError as exc:
            self._active.discard(session_id)
            events.warning(
                "session %s: not capturing, could not open the files (%s)",
                session_id,
                type(exc).__name__,
                event="capture_declined",
                session=session_id,
                reason="open",
                failure=type(exc).__name__,
            )
            return None
        events.info(
            "session %s: capturing to %s",
            session_id,
            capture.wav_path,
            event="capture_started",
            session=session_id,
            path=str(capture.wav_path),
        )
        return capture


class DeviceFacts:
    """What a device told the OTA endpoint about itself, kept until the
    session it is about to open asks for it.

    The firmware version is arguably the most load-bearing field in a
    capture manifest, because echo cancellation is firmware-side, and
    `ota.reply.check_version` is the only place the device ever states
    it. Bounded, so a server that many devices check in with does not
    accumulate them forever.
    """

    def __init__(self, limit: int = 256) -> None:
        self._limit = limit
        self._facts: OrderedDict[str, dict[str, str]] = OrderedDict()

    def record(self, mac: str, firmware: str, board: str) -> None:
        self._facts[mac] = {"firmware": firmware, "board": board}
        self._facts.move_to_end(mac)
        while len(self._facts) > self._limit:
            self._facts.popitem(last=False)

    def get(self, mac: str | None) -> dict[str, str]:
        if mac is None:
            return {}
        return dict(self._facts.get(mac, {}))
