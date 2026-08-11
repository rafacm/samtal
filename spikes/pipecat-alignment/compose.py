"""Builds the samtal-format capture pair from one run's raw logs.

Measurement harness. It turns what `drive.py` recorded into the two
files the repository's own analysis reads unmodified: `<session>.wav`
(stereo 16 kHz s16le, channel 0 the microphone as received, channel 1
the reply reference) and `<session>.jsonl` (the event track).

The mapping from recorded timestamps to sample positions is the plan's,
fixed before any correlation result was seen, and this file implements
it literally:

- One monotonic epoch, read once by `Recorder` before anything was
  recorded. Every timestamp here is seconds since it, and t = 0 is
  sample 0 of both channels.
- A buffer delivery of N samples observed at time t occupies
  (t - N/rate, t]. Nothing is placed by how much was written before it.
- A tap packet is placed *starting* at its send timestamp and never
  overwrites the packet before it: it occupies
  `start = max(previous_end, round(send_t * rate))` onwards, so a gap
  appears only when a send arrives later than the previous packet's
  playout would have ended. The plan pinned an ending-at rule first
  and the PR review round corrected it: pipecat sends a chunk and only
  then sleeps, so the timestamp taken when the awaited send returns
  opens that packet's 60 ms playout slot rather than closing it, and
  samtal's own capture, which this mirrors, places decoded audio
  starting at the send time and keeps packets contiguous when sends
  arrive early (`capture.py`, `at = max(channel.next_frame,
  self._frame_of(now), self._start_frame)`).
- Gaps are silence and leading silence is kept. There is no onset
  detection and no correlation-based shifting anywhere in this file:
  either would erase the fixed latency the spike exists to measure.

Two audit figures are printed and belong in the findings, because they
are the only way a reader can tell whether the mapping was clean:
overlap (deliveries whose placements collide, where the later one wins)
and the resampler's input and output sample counts.

    uv run python compose.py runs/<session>
"""

import argparse
import ctypes.util
import json
import struct
import wave
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly

if ctypes.util.find_library("opus") is None:
    # Same shim as the serializer's, repeated rather than imported: this
    # file needs the Opus decoder and nothing else pipecat drags in.
    from xiaozhi_sdk.utils import setup_opus

    setup_opus()

import opuslib  # noqa: E402

# The rate the pair is written at, as samtal's capture does.
CAPTURE_RATE = 16000

# The rate everything is recorded and placed at: the output rate the
# server hello announced, which is what the tap's packets decode to and
# what the buffer processor is asked to record natively. Every track is
# laid out here and converted to the capture rate exactly once, at the
# end, through the same call.
TAP_RATE = 24000
TAP_FRAME_MS = 60

# 24 kHz to 16 kHz.
DOWN_UP, DOWN_DOWN = 2, 3


def to_capture_rate(track: np.ndarray) -> np.ndarray:
    """The one resampler every track goes through, once, whole."""
    out = resample_poly(track.astype(np.float64), DOWN_UP, DOWN_DOWN)
    return np.clip(out, -32768, 32767).astype(np.int16)


def resampler_delay_samples() -> float:
    """The group delay `to_capture_rate` introduces, measured.

    `resample_poly` centres its FIR filter and compensates internally,
    so the expected answer is zero within a sample, but the plan
    requires the figure recorded rather than assumed. An impulse in,
    and the distance of the output peak from where an ideal converter
    would put it, out.
    """
    n = 4096
    impulse = np.zeros(n)
    impulse[n // 2] = 1.0
    out = resample_poly(impulse, DOWN_UP, DOWN_DOWN)
    ideal = (n // 2) * DOWN_UP / DOWN_DOWN
    return float(np.argmax(np.abs(out)) - ideal)


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.open() if line.strip()]


def read_packets(path: Path) -> list[bytes]:
    """The length-prefixed Opus packets the tap wrote."""
    raw = path.read_bytes()
    packets = []
    at = 0
    while at < len(raw):
        (n,) = struct.unpack_from("<I", raw, at)
        at += 4
        packets.append(raw[at : at + n])
        at += n
    return packets


def place(
    track: np.ndarray,
    written: np.ndarray,
    samples: np.ndarray,
    end_t: float,
    rate: int,
) -> int:
    """Write `samples` so that they end at `end_t`. Returns how many of
    those positions had already been written, which is the audit figure
    for whether the placement rule collided with itself."""
    end = int(round(end_t * rate))
    start = end - len(samples)
    if start < 0:  # a placement that would precede the epoch is clipped
        samples = samples[-end:] if end > 0 else samples[:0]
        start = 0
    end = start + len(samples)
    if end > len(track):
        samples = samples[: len(track) - start]
        end = start + len(samples)
    overlap = int(np.count_nonzero(written[start:end]))
    track[start:end] = samples
    written[start:end] = True
    return overlap


def place_contiguous(
    track: np.ndarray,
    samples: np.ndarray,
    start_t: float,
    rate: int,
    previous_end: int,
) -> tuple[int, int]:
    """Write `samples` starting at `start_t`, never before `previous_end`.

    This is samtal's own capture rule. Returns the new end position and
    how many samples of silent gap preceded this write, which is the
    audit figure for how often a send arrived later than the previous
    packet's playout would have finished. Nothing is ever overwritten,
    so no audio is lost to a collision.
    """
    start = max(previous_end, int(round(start_t * rate)))
    gap = start - previous_end
    end = min(start + len(samples), len(track))
    if end > start:
        track[start:end] = samples[: end - start]
    return end, gap


def write_pair(
    captures: Path, session: str, mic: np.ndarray, ref: np.ndarray
) -> None:
    """One stereo 16 kHz capture: microphone left, reference right."""
    interleaved = np.empty(2 * len(mic), dtype=np.int16)
    interleaved[0::2] = mic
    interleaved[1::2] = ref
    with wave.open(str(captures / f"{session}.wav"), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(CAPTURE_RATE)
        w.writeframes(interleaved.tobytes())


def compose(run: Path) -> None:
    session = (run / "session").read_text().strip()
    deliveries = read_jsonl(run / "buffer.jsonl")
    sends = read_jsonl(run / "tap.jsonl")
    events = json.loads((run / "events.json").read_text())
    user_raw = np.frombuffer((run / "buffer_user.raw").read_bytes(), dtype=np.int16)
    bot_raw = np.frombuffer((run / "buffer_bot.raw").read_bytes(), dtype=np.int16)
    packets = read_packets(run / "tap.opus")

    rates = {d["rate"] for d in deliveries}
    if rates != {TAP_RATE}:
        raise SystemExit(
            f"buffer delivered {sorted(rates)} Hz; every track is laid out at "
            f"{TAP_RATE} Hz and the spike asks the processor to record natively"
        )

    span_s = max(
        [d["t"] for d in deliveries] + [s["t"] for s in sends] + [0.0]
    ) + 1.0
    n = int(round(span_s * TAP_RATE))
    mic = np.zeros(n, dtype=np.int16)
    ref = np.zeros(n, dtype=np.int16)
    mic_written = np.zeros(n, dtype=bool)
    ref_written = np.zeros(n, dtype=bool)

    # The two buffer tracks, each delivery ending at its arrival stamp.
    at = 0
    overlap = 0
    for d in deliveries:
        count = d["samples"]
        overlap += place(
            mic, mic_written, user_raw[at : at + count], d["t"], TAP_RATE
        )
        place(ref, ref_written, bot_raw[at : at + count], d["t"], TAP_RATE)
        at += count

    # The tap, on its own 24 kHz timeline, then resampled once over the
    # whole continuous track. Per packet resampling would reset the
    # filter state at every boundary and inject the artefacts under test.
    decoder = opuslib.Decoder(fs=TAP_RATE, channels=1)
    frame = TAP_RATE * TAP_FRAME_MS // 1000
    tap = np.zeros(n, dtype=np.int16)
    tap_end = 0
    tap_gaps = 0
    tap_gap_samples = 0
    for index, (send, payload) in enumerate(zip(sends, packets)):
        pcm = np.frombuffer(decoder.decode(payload, frame_size=frame), dtype=np.int16)
        tap_end, gap = place_contiguous(tap, pcm, send["t"], TAP_RATE, tap_end)
        # The first packet's "gap" is the session's leading silence,
        # which the mapping keeps on purpose, not a late send.
        if gap and index:
            tap_gaps += 1
            tap_gap_samples += gap

    # The other reference pipecat offers: the bot *turn* track, which is
    # extended only with the bot's own audio and delivered once, when the
    # bot stops speaking. It is placed by exactly the same rule as a
    # delivery, ending at the timestamp its handler observed, so the
    # comparison grants it no freedom the delivered track did not have.
    turn = np.zeros(n, dtype=np.int16)
    turn_written = np.zeros(n, dtype=bool)
    turn_overlap = 0
    turns = read_jsonl(run / "turn.jsonl") if (run / "turn.jsonl").exists() else []
    turn_raw = (
        np.frombuffer((run / "turn_bot.raw").read_bytes(), dtype=np.int16)
        if (run / "turn_bot.raw").exists()
        else np.zeros(0, dtype=np.int16)
    )
    at_turn = 0
    for t in turns:
        count = t["samples"]
        turn_overlap += place(
            turn, turn_written, turn_raw[at_turn : at_turn + count], t["t"], TAP_RATE
        )
        at_turn += count

    # Every track has now been laid out at the native 24 kHz on one
    # timeline. Each is converted to the capture rate exactly once here,
    # whole and through the same call, which is what the plan requires
    # and what putting pipecat's own streaming resampler in the path
    # broke: two implementations, and one of them never flushed.
    mic16, ref16, turn16, tap16 = (
        to_capture_rate(track) for track in (mic, ref, turn, tap)
    )
    n16 = min(len(mic16), len(ref16), len(turn16), len(tap16))
    mic16, ref16, turn16, tap16 = (
        track[:n16] for track in (mic16, ref16, turn16, tap16)
    )

    captures = run / "captures"
    captures.mkdir(exist_ok=True)
    write_pair(captures, session, mic16, ref16)
    turn_captures = run / "captures-turn"
    turn_captures.mkdir(exist_ok=True)
    write_pair(turn_captures, session, mic16, turn16)
    (run / "tap16k.raw").write_bytes(tap16.tobytes())

    # The event track. `heard` carries the utterance duration so the
    # analysis masks the user's speech out of its candidate windows.
    utterance_s = next(
        (e["duration_s"] for e in events if e["event"] == "utterance"), 4.0
    )
    track = [
        {"event": "session_open", "t_ms": 0.0, "agent": "spike"},
    ]
    for e in events:
        if e["event"] == "heard":
            track.append(
                {
                    "event": "heard",
                    "t_ms": e["t"] * 1000,
                    "duration_s": utterance_s,
                }
            )
    if sends:
        track.append(
            {
                "event": "speaking_started",
                "t_ms": sends[0]["t"] * 1000,
                "agent": "spike",
            }
        )
    track.sort(key=lambda e: e["t_ms"])
    for directory in (captures, turn_captures):
        with (directory / f"{session}.jsonl").open("w") as f:
            for e in track:
                f.write(json.dumps(e) + "\n")

    print(f"composed {session} -> {captures}")
    print(f"  span            : {n / TAP_RATE:.1f} s, laid out at {TAP_RATE} Hz")
    print(f"  buffer          : {len(deliveries)} deliveries, {at} samples")
    print(f"  buffer overlap  : {overlap} samples ({overlap / at:.4%})")
    print(f"  tap             : {len(packets)} packets at {TAP_RATE} Hz")
    print(
        f"  tap gaps        : {tap_gaps} late sends, {tap_gap_samples} samples "
        f"of silence between packets (no overwrites by construction)"
    )
    print(
        f"  resample        : {n} in -> {n16} out per track "
        f"({TAP_RATE} -> {CAPTURE_RATE} Hz), 4 tracks, one call each, "
        f"filter delay {resampler_delay_samples():+.2f} samples"
    )
    print(f"  turn track      : {len(turns)} turns, {at_turn} samples "
          f"({at_turn / TAP_RATE:.1f} s), overlap {turn_overlap}")
    print(f"  events          : {[e['event'] for e in track]}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run", type=Path, help="a runs/<session> directory")
    compose(parser.parse_args().run)


if __name__ == "__main__":
    main()
