"""Recording a session to disk, and what makes a recording usable.

The point of a capture is alignment: without lining up what the
microphone heard against what the speaker was playing against what the
server decided, an echo-triggered barge-in cannot be told from a genuine
one, and that distinction is the whole question in #28. So the
assertions here are mostly about time: that a sample index means the
same instant in both channels, that an event's `t_ms` indexes into the
audio, and that a gap in one channel becomes silence rather than sliding
everything after it.
"""

import json
import struct
import time
import wave
from pathlib import Path

import pytest

from tests.support.stores import CAPTURE_MANIFEST as MANIFEST
from tests.support.stores import store, tone
from vinga_server.capture import (
    CAPTURE_RATE,
    FRAME_BYTES,
    WAV_HEADER_BYTES,
    DeviceFacts,
    SessionCapture,
    interleave,
)


def read_channels(path: Path) -> tuple[list[int], list[int]]:
    """The two channels, as sample lists."""
    with wave.open(str(path), "rb") as handle:
        assert handle.getnchannels() == 2
        assert handle.getframerate() == CAPTURE_RATE
        assert handle.getsampwidth() == 2
        raw = handle.readframes(handle.getnframes())
    samples = struct.unpack(f"<{len(raw) // 2}h", raw)
    return list(samples[0::2]), list(samples[1::2])


def test_interleave_puts_the_channels_in_the_right_order() -> None:
    left = struct.pack("<hh", 1, 2)
    right = struct.pack("<hh", -1, -2)
    assert struct.unpack("<hhhh", interleave(left, right)) == (1, -1, 2, -2)


def test_interleave_refuses_channels_of_different_lengths() -> None:
    # The failure would be a file where one channel is silently shifted
    # against the other, which is the one thing a capture must not be.
    with pytest.raises(ValueError, match="same length"):
        interleave(b"\x00\x00", b"\x00\x00\x00\x00")


def test_the_microphone_and_the_reply_land_on_their_own_channels(tmp_path: Path) -> None:
    opened = time.monotonic()
    capture = store(tmp_path).open("s1", opened, MANIFEST)
    assert capture is not None
    capture.microphone(tone(200, 1000), opened)
    capture.reply(tone(200, -1000), opened)
    capture.close()

    mic, reply = read_channels(capture.wav_path)
    assert set(mic[: CAPTURE_RATE // 10]) == {1000}
    assert set(reply[: CAPTURE_RATE // 10]) == {-1000}


def test_a_sample_index_is_the_same_instant_in_both_channels(tmp_path: Path) -> None:
    # The whole reason for stereo. The reply starts half a second after
    # the microphone does, and that offset has to survive to disk as
    # exactly half a second, because echo leakage is read off the delay
    # between the channels.
    opened = time.monotonic()
    capture = store(tmp_path).open("s1", opened, MANIFEST)
    assert capture is not None
    capture.microphone(tone(1000, 1000), opened)
    capture.reply(tone(500, -1000), opened + 0.5)
    capture.close()

    mic, reply = read_channels(capture.wav_path)
    assert set(mic[:CAPTURE_RATE]) == {1000}
    # Silence until the half second mark, then the reply, to within a
    # sample or two of rounding.
    assert set(reply[: CAPTURE_RATE // 2 - 2]) == {0}
    assert set(reply[CAPTURE_RATE // 2 + 2 : CAPTURE_RATE - 2]) == {-1000}


def test_a_gap_becomes_silence_rather_than_sliding_the_timeline(tmp_path: Path) -> None:
    # A channel that goes quiet must not compress: if the audio after a
    # gap moved earlier, every event after it would point at the wrong
    # place, which is worse than a missing recording.
    opened = time.monotonic()
    capture = store(tmp_path).open("s1", opened, MANIFEST)
    assert capture is not None
    capture.microphone(tone(100, 1000), opened)
    capture.microphone(tone(100, 2000), opened + 1.0)
    capture.close()

    mic, _ = read_channels(capture.wav_path)
    assert set(mic[: CAPTURE_RATE // 10]) == {1000}
    assert set(mic[CAPTURE_RATE // 5 : CAPTURE_RATE - 2]) == {0}
    assert set(mic[CAPTURE_RATE + 2 : CAPTURE_RATE + CAPTURE_RATE // 10 - 2]) == {2000}


def test_an_events_offset_indexes_into_the_audio(tmp_path: Path) -> None:
    opened = time.monotonic()
    capture = store(tmp_path).open("s1", opened, MANIFEST)
    assert capture is not None
    capture.microphone(tone(2000, 1000), opened)
    capture.event({"event": "heard", "duration_s": 1.0}, opened + 1.25)
    capture.close()

    lines = [json.loads(line) for line in capture.jsonl_path.read_text().splitlines()]
    heard = next(line for line in lines if line["event"] == "heard")
    assert heard["t_ms"] == pytest.approx(1250, abs=1)
    # And that offset is inside the audio that was recorded.
    mic, _ = read_channels(capture.wav_path)
    assert len(mic) > int(heard["t_ms"] / 1000 * CAPTURE_RATE)


def test_the_endpointers_opinion_is_sampled_not_just_its_decisions(tmp_path: Path) -> None:
    # What turns "barge-in fired wrongly" into "the endpointer
    # classified 340 ms of the assistant's own voice as speech".
    opened = time.monotonic()
    capture = store(tmp_path).open("s1", opened, MANIFEST)
    assert capture is not None
    for i in range(5):
        capture.vad(speech_ms=i * 60, listening=True, replying=i > 2, now=opened + i * 0.06)
    capture.close()

    vad = [
        json.loads(line)
        for line in capture.jsonl_path.read_text().splitlines()
        if json.loads(line)["event"] == "vad"
    ]
    assert [record["speech_ms"] for record in vad] == [0, 60, 120, 180, 240]
    assert [record["replying"] for record in vad] == [False, False, False, True, True]


def test_dropped_frames_are_counted_per_second_with_their_reason(tmp_path: Path) -> None:
    # Per second rather than per frame: the guards drop whole seconds at
    # a time, and what explains a misfire is the rate.
    opened = time.monotonic()
    capture = store(tmp_path).open("s1", opened, MANIFEST)
    assert capture is not None
    for i in range(10):
        capture.dropped("barge_in_off", opened + 0.1 * i)
    for i in range(3):
        capture.dropped("not_listening", opened + 1.1 + 0.1 * i)
    capture.close()

    dropped = [
        json.loads(line)
        for line in capture.jsonl_path.read_text().splitlines()
        if json.loads(line)["event"] == "frames_dropped"
    ]
    assert dropped[0]["second"] == 0
    assert dropped[0]["reasons"] == {"barge_in_off": 10}
    assert dropped[-1]["reasons"] == {"not_listening": 3}


def test_the_manifest_exists_before_the_session_ends(tmp_path: Path) -> None:
    # A pod stopped mid-session is plausibly the session most worth
    # looking at, and a capture with no manifest cannot be interpreted
    # at all.
    opened = time.monotonic()
    capture = store(tmp_path).open("s1", opened, MANIFEST)
    assert capture is not None
    capture.microphone(tone(100), opened)

    manifest = json.loads(capture.manifest_path.read_text())
    assert manifest["barge_in"] == {"enabled": True}
    assert manifest["capture"]["complete"] is False
    assert manifest["capture"]["sample_rate"] == CAPTURE_RATE

    capture.close()
    assert json.loads(capture.manifest_path.read_text())["capture"]["complete"] is True


def test_a_capture_cut_off_mid_session_is_still_readable(tmp_path: Path) -> None:
    # The deploy strategy detaches the volume and stops the pod, so a
    # capture in progress is simply cut. The header then still claims
    # zero, and everything after the 44 bytes is raw PCM, so the audio
    # is recoverable from the file size.
    opened = time.monotonic()
    capture = store(tmp_path).open("s1", opened, MANIFEST)
    assert capture is not None
    capture.microphone(tone(2000, 1234), opened)
    capture.microphone(tone(500, 1234), opened + 2.0)
    # The pod goes: the descriptors are gone and nothing patches the
    # header. Whatever reached the file is all there is, which is why
    # the writer flushes as it goes rather than buffering.
    # White-box for both files this capture holds: the failure under
    # test is the process going away with its descriptors, which is what
    # leaves a stale WAV header behind, and no public call closes a file
    # without also finishing the recording properly. Closing them out
    # from under the writer is the pod being killed.
    capture._wav.close()  # type: ignore[union-attr]
    capture._events.close()  # type: ignore[union-attr]

    raw = capture.wav_path.read_bytes()
    assert raw[:4] == b"RIFF"
    # The header is stale, which is what the manifest's complete: false
    # tells the analysis side to expect.
    assert struct.unpack("<I", raw[40:44])[0] == 0
    assert json.loads(capture.manifest_path.read_text())["capture"]["complete"] is False

    audio = raw[WAV_HEADER_BYTES:]
    assert len(audio) > 0
    assert len(audio) % FRAME_BYTES == 0
    samples = struct.unpack(f"<{len(audio) // 2}h", audio)
    assert set(samples[0::2]) == {1234}


def test_capture_declines_when_the_volume_is_nearly_full(tmp_path: Path) -> None:
    # Declining is the point: /data also holds agent memory and the
    # model caches, and capture must not be the thing that fills it.
    huge = store(tmp_path, min_free_mb=10_000_000.0)
    assert huge.open("s1", time.monotonic(), MANIFEST) is None


def test_the_directory_is_pruned_oldest_first_to_stay_inside_its_budget(
    tmp_path: Path,
) -> None:
    keeper = store(tmp_path, max_total_mb=0.5)
    opened = time.monotonic()
    for index in range(4):
        capture = keeper.open(f"s{index}", opened, MANIFEST)
        assert capture is not None
        # A quarter megabyte each, so the budget holds about two.
        capture.microphone(tone(4000), opened)
        capture.close()
        # Distinct mtimes, so "oldest" is well defined on a fast disk.
        for suffix in (".wav", ".jsonl", ".json"):
            path = capture.wav_path.with_suffix(suffix)
            if path.exists():
                import os

                os.utime(path, (opened + index, opened + index))

    keeper.prune()
    left = sorted(path.stem for path in keeper.directory.glob("*.wav"))
    assert left, "pruning must not empty the directory"
    assert "s0" not in left, "the oldest capture should have gone first"
    assert "s3" in left, "the newest capture should have survived"
    # All three files of a pruned capture go together.
    for stem in ("s0",):
        assert not (keeper.directory / f"{stem}.jsonl").exists()
        assert not (keeper.directory / f"{stem}.json").exists()


def test_a_capture_stops_at_its_own_time_limit(tmp_path: Path) -> None:
    opened = time.monotonic()
    capture = store(tmp_path, max_session_s=1.0).open("s1", opened, MANIFEST)
    assert capture is not None
    capture.microphone(tone(500, 1000), opened)
    capture.microphone(tone(500, 1000), opened + 2.0)
    capture.close()

    mic, _ = read_channels(capture.wav_path)
    assert len(mic) < CAPTURE_RATE * 2, "audio past the limit was still recorded"


def test_a_write_that_fails_does_not_take_the_session_with_it(tmp_path: Path) -> None:
    opened = time.monotonic()
    capture = store(tmp_path).open("s1", opened, MANIFEST)
    assert capture is not None
    # White-box, same reason: a write that fails needs a file that
    # cannot be written to, and nothing public makes one.
    capture._wav.close()  # type: ignore[union-attr]
    # No exception: a conversation is worth more than a recording of it.
    capture.microphone(tone(100), opened)
    capture.event({"event": "heard"}, opened)
    capture.close()


def test_device_facts_survive_from_the_ota_check_to_the_session() -> None:
    facts = DeviceFacts()
    facts.record("aa:bb:cc:dd:ee:ff", "2.4.0", "waveshare")
    assert facts.get("aa:bb:cc:dd:ee:ff") == {"firmware": "2.4.0", "board": "waveshare"}
    assert facts.get("00:00:00:00:00:00") == {}
    assert facts.get(None) == {}


def test_device_facts_do_not_grow_without_bound() -> None:
    facts = DeviceFacts(limit=3)
    for index in range(5):
        facts.record(f"aa:bb:cc:dd:ee:{index:02x}", "2.4.0", "board")
    assert facts.get("aa:bb:cc:dd:ee:00") == {}
    assert facts.get("aa:bb:cc:dd:ee:04") != {}


def test_the_audio_covers_the_last_event_even_with_nothing_to_record(
    tmp_path: Path,
) -> None:
    # A review finding. An offset that points past the end of the file
    # is not an index into it, and a session can be open through
    # stretches with no decodable audio at all: a device that connects,
    # says nothing, and drops has only its own open and close events.
    opened = time.monotonic()
    capture = store(tmp_path).open("s1", opened, MANIFEST)
    assert capture is not None
    capture.event({"event": "session_open"}, opened)
    capture.event({"event": "session_closed"}, opened + 3.0)
    capture.close()

    mic, reply = read_channels(capture.wav_path)
    assert len(mic) == len(reply)
    assert len(mic) >= 3 * CAPTURE_RATE - 2, "the audio stops before the last event"
    assert set(mic) == {0}, "silence is what a session with no audio recorded"


def test_padding_to_the_last_event_stops_at_the_session_limit(tmp_path: Path) -> None:
    opened = time.monotonic()
    capture = store(tmp_path, max_session_s=1.0).open("s1", opened, MANIFEST)
    assert capture is not None
    capture.event({"event": "session_open"}, opened)
    capture.event({"event": "late"}, opened + 60.0)
    capture.close()

    mic, _ = read_channels(capture.wav_path)
    assert len(mic) <= CAPTURE_RATE + 2, "a stray late event stretched the file"


def test_events_stop_at_the_limit_too(tmp_path: Path) -> None:
    # A review finding. The audio is clamped to the limit on close, so
    # an event written past it would be an offset with no audio under
    # it, which is the one thing the decision track promises not to be.
    opened = time.monotonic()
    capture = store(tmp_path, max_session_s=1.0).open("s1", opened, MANIFEST)
    assert capture is not None
    capture.event({"event": "session_open"}, opened)
    capture.event({"event": "much_later"}, opened + 3600.0)
    capture.close()

    recorded = [json.loads(line) for line in capture.jsonl_path.read_text().splitlines()]
    assert [record["event"] for record in recorded] == ["session_open"]

    mic, _ = read_channels(capture.wav_path)
    audio_ms = len(mic) / CAPTURE_RATE * 1000
    for record in recorded:
        assert record["t_ms"] <= audio_ms


def test_every_offset_indexes_into_the_audio_even_at_the_limit(tmp_path: Path) -> None:
    # A review finding, and the general form of the previous one: the
    # aggregate that close() flushes was stamped with the clock, which
    # can be past the limit the audio was clamped to. Derived from a
    # clamped frame index now, so the guarantee holds for every record
    # by construction rather than by each caller remembering.
    opened = time.monotonic()
    capture = store(tmp_path, max_session_s=0.05).open("s1", opened, MANIFEST)
    assert capture is not None
    capture.dropped("barge_in_off", opened + 0.01)
    capture.event({"event": "late"}, opened + 0.075)
    capture.close()

    mic, _ = read_channels(capture.wav_path)
    audio_ms = len(mic) / CAPTURE_RATE * 1000
    recorded = [json.loads(line) for line in capture.jsonl_path.read_text().splitlines()]
    assert recorded, "nothing was recorded at all"
    for record in recorded:
        assert record["t_ms"] <= audio_ms, (
            f"{record['event']} at {record['t_ms']} ms is past {audio_ms:.1f} ms of audio"
        )


def test_the_audio_covers_the_frame_an_event_lands_on(tmp_path: Path) -> None:
    """The off-by-one under the guarantee, pinned without a clock.

    A sample at index N only exists once N+1 frames are written, and
    `t_ms` is rounded to a tenth of a millisecond, which can round up.
    An event landing on what was the last frame therefore pointed one
    sample past the end. This drives `now` explicitly and writes no
    dropped-frame aggregate, so it does not depend on how long the test
    itself took to run, which is how the original slipped through.
    """
    # The origin is zero rather than a clock reading on purpose. Adding
    # a fraction of a millisecond to a monotonic value in the tens of
    # thousands loses the last bit, so `now - opened` comes back a
    # hair short and the frame floors one low. That wobble is harmless
    # in a 16 kHz recording and fatal to an exact assertion, and it is
    # what made the first version of this test pass or fail on what the
    # machine clock happened to read.
    opened = 0.0
    at_frame = 7
    capture = store(tmp_path).open("s1", opened, MANIFEST)
    assert capture is not None
    capture.event({"event": "only"}, at_frame / CAPTURE_RATE)
    capture.close()

    mic, reply = read_channels(capture.wav_path)
    assert len(mic) == at_frame + 1, "the audio stops on the frame the event is at"
    assert len(mic) == len(reply)

    (record,) = [json.loads(line) for line in capture.jsonl_path.read_text().splitlines()]
    audio_ms = len(mic) / CAPTURE_RATE * 1000
    assert record["t_ms"] <= audio_ms


def test_a_capture_with_no_events_is_not_padded(tmp_path: Path) -> None:
    # The other side of it: covering the last event must not invent a
    # frame for a capture that never had one.
    opened = time.monotonic()
    capture = store(tmp_path).open("s1", opened, MANIFEST)
    assert capture is not None
    capture.close()
    mic, _ = read_channels(capture.wav_path)
    assert mic == []


def test_a_capture_still_recording_is_never_pruned(tmp_path: Path) -> None:
    # A review finding. Unlinking underneath an open descriptor leaves
    # the session writing to a file nobody can find.
    keeper = store(tmp_path, max_total_mb=0.01)
    opened = time.monotonic()
    live = keeper.open("live", opened, MANIFEST)
    assert live is not None
    # Two writes, the second past the flush lag, so there is really
    # audio on disk for a prune to find.
    live.microphone(tone(2000), opened)
    live.microphone(tone(100), opened + 3.0)
    assert live.wav_path.stat().st_size > 10_000, "nothing was flushed to prune"

    later = keeper.open("later", opened, MANIFEST)
    assert later is not None
    assert live.wav_path.exists(), "a capture still recording was pruned"
    live.close()
    later.close()


def test_the_budget_is_checked_again_when_a_capture_closes(tmp_path: Path) -> None:
    # A review finding. Checking only at open leaves a session that
    # overran sitting there until some later session happens to start.
    keeper = store(tmp_path, max_total_mb=0.2)
    opened = time.monotonic()
    for index in range(3):
        capture = keeper.open(f"s{index}", opened, MANIFEST)
        assert capture is not None
        capture.microphone(tone(3000), opened)
        capture.close()
        import os

        for suffix in (".wav", ".jsonl", ".json"):
            path = capture.wav_path.with_suffix(suffix)
            if path.exists():
                os.utime(path, (opened + index, opened + index))

    # No new session opened after the last one, and the directory is
    # still inside its budget.
    left = sorted(path.stem for path in keeper.directory.glob("*.wav"))
    assert left == ["s2"], f"the budget was not enforced on close: {left}"


def test_the_newest_capture_survives_a_budget_smaller_than_a_session(
    tmp_path: Path,
) -> None:
    # Deleting the recording somebody just went out to make, because it
    # is bigger than a misconfigured budget, is the worst thing this
    # could do.
    keeper = store(tmp_path, max_total_mb=0.001)
    opened = time.monotonic()
    capture = keeper.open("only", opened, MANIFEST)
    assert capture is not None
    capture.microphone(tone(3000), opened)
    capture.close()
    assert capture.wav_path.exists(), "the only capture was pruned away"


def test_a_capture_that_never_started_is_not_a_capture(tmp_path: Path) -> None:
    # SessionCapture is inert until start(), so a failure to open leaves
    # no half-written files behind.
    capture = SessionCapture(tmp_path, "s1", time.monotonic(), MANIFEST, 900.0)
    capture.microphone(tone(100), time.monotonic())
    capture.close()
    assert not capture.wav_path.exists()
