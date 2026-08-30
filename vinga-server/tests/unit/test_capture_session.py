"""A capture taken from a real session, rather than from calls to the
writer.

The unit tests for `capture.py` prove the file format. These prove the
wiring: that the microphone reaches channel 0 before the guards drop it,
that what was paced out reaches channel 1, that the events the session
already logs land in the decision track with offsets that index into the
audio, and that the manifest says what the capture was made against.
"""

import asyncio
import json
import struct
import wave
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from tests.support.configs import (
    DEVICE_MAC,
    DEVICE_UUID,
    FRAME_BYTES,
    FRAME_MS,
    config_with_agent,
    world,
)
from tests.support.events import both_formats
from tests.support.providers import built_world
from tests.support.sessions import attached_capture, drive_reply
from tests.support.sockets import LoopingSocket
from tests.support.stores import memory as lane_memory
from tests.support.wire import (
    collect_reply,
    connect,
    endpoint_silence,
    say_something,
    send_pcm,
    shake_hands,
    speech_pcm,
)
from vinga_server.app import create_app
from vinga_server.audio.opus import OpusEncoder
from vinga_server.capture import CAPTURE_RATE, CaptureStore
from vinga_server.device import session as session_module
from vinga_server.device.session import DeviceSession
from vinga_server.protocol import framing
from vinga_server.runtime.pipeline import bespoke_runtime_factory
from vinga_server.tools.mcp import McpServers


def capturing_config(tmp_path: Path, **kwargs: object):
    server: dict[str, object] = {
        "capture": {"enabled": True, "dir": str(tmp_path / "captures")}
    }
    server.update(kwargs)
    return config_with_agent(server=server)


def only_capture(tmp_path: Path) -> tuple[Path, Path, Path]:
    directory = tmp_path / "captures"
    wavs = list(directory.glob("*.wav"))
    assert len(wavs) == 1, f"expected one capture, found {wavs}"
    wav = wavs[0]
    return wav, wav.with_suffix(".jsonl"), wav.with_suffix(".json")


def channels(path: Path) -> tuple[list[int], list[int]]:
    with wave.open(str(path), "rb") as handle:
        raw = handle.readframes(handle.getnframes())
    samples = struct.unpack(f"<{len(raw) // 2}h", raw)
    return list(samples[0::2]), list(samples[1::2])


def events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def loudest(samples: list[int]) -> int:
    return max((abs(sample) for sample in samples), default=0)


def test_nothing_is_recorded_without_a_capture_section(tmp_path: Path) -> None:
    # Recording room audio is the opposite of what the rest of the
    # project promises, so it has to be asked for.
    with TestClient(create_app(config_with_agent())) as client:
        with connect(client) as websocket:
            shake_hands(websocket)
            say_something(websocket)
    assert not (tmp_path / "captures").exists()


def test_a_configured_section_records_nothing_until_it_is_enabled(
    tmp_path: Path,
) -> None:
    # The switch is the flag, not the section, so that turning capture
    # off does not mean deleting the directory and the budgets with it.
    # A section left in a config file must record nothing.
    config = config_with_agent(
        server={"capture": {"dir": str(tmp_path / "captures")}}
    )
    with TestClient(create_app(config)) as client:
        with connect(client) as websocket:
            shake_hands(websocket)
            texts, _ = say_something(websocket)
    assert texts, "the conversation did not run"
    assert not (tmp_path / "captures").exists(), "a disabled section still recorded"


def test_a_session_records_the_microphone_and_the_reply(tmp_path: Path) -> None:
    with TestClient(create_app(capturing_config(tmp_path))) as client:
        with connect(client) as websocket:
            shake_hands(websocket)
            say_something(websocket)

    wav, _, _ = only_capture(tmp_path)
    mic, reply = channels(wav)
    assert loudest(mic) > 0, "the microphone channel is silent"
    assert loudest(reply) > 0, "the reply channel is silent"


def test_the_reply_channel_holds_what_was_paced_out(tmp_path: Path) -> None:
    # Channel 1 is decoded back from the Opus that actually went to the
    # device, so it is what the speaker played rather than what was
    # synthesized. The mock TTS speaks a tone, so its presence is
    # visible as amplitude in the right channel and its absence in the
    # left while nothing is being said into the microphone.
    with TestClient(create_app(capturing_config(tmp_path))) as client:
        with connect(client) as websocket:
            shake_hands(websocket)
            say_something(websocket)

    wav, jsonl, _ = only_capture(tmp_path)
    mic, reply = channels(wav)
    started = next(e for e in events(jsonl) if e["event"] == "speaking_started")
    at = int(started["t_ms"] / 1000 * CAPTURE_RATE)
    # A tenth of a second after the first frame went out, the reply
    # channel is carrying audio.
    window = reply[at : at + CAPTURE_RATE // 10]
    assert loudest(window) > 0, "no reply audio where speaking_started says it began"


def test_the_microphone_is_recorded_even_when_the_guards_drop_it(tmp_path: Path) -> None:
    # The reason capture is hooked before the guards. With barge_in off
    # the session drops mic frames outright while it is speaking, and
    # those are precisely the frames that would explain a misfire.
    asked_ms, over_ms = 300, 900
    config = capturing_config(tmp_path, barge_in=False)
    with TestClient(create_app(config)) as client:
        with connect(client) as websocket:
            shake_hands(websocket)
            encoder = OpusEncoder()
            websocket.send_text(
                json.dumps({"type": "listen", "state": "start", "mode": "realtime"})
            )
            send_pcm(websocket, speech_pcm(asked_ms), encoder)
            endpoint_silence(websocket, encoder)
            # Talking over the reply while it streams, which this
            # configuration discards before it is even decoded.
            send_pcm(websocket, speech_pcm(over_ms), encoder)
            collect_reply(websocket)

    wav, jsonl, _ = only_capture(tmp_path)
    mic, _ = channels(wav)
    dropped = [e for e in events(jsonl) if e["event"] == "frames_dropped"]
    guarded = [e for e in dropped if "barge_in_off" in e["reasons"]]
    assert guarded, f"nothing was dropped by the guard; reasons seen: {dropped}"

    # The claim, and the reason capture is hooked before the guards: the
    # audio the session threw away is in the file anyway. Counted as
    # loud samples, because the frames arrive faster than realtime here
    # and so do not sit where a wall clock would put them; what matters
    # is that they are not missing.
    loud_ms = sum(1 for sample in mic if abs(sample) > 100) / CAPTURE_RATE * 1000
    assert loud_ms > (asked_ms + over_ms) * 0.7, (
        f"only {loud_ms:.0f} ms of microphone audio was recorded out of "
        f"{asked_ms + over_ms} ms spoken; the frames the guard dropped are missing"
    )


def test_the_decision_track_carries_the_events_with_offsets(tmp_path: Path) -> None:
    with TestClient(create_app(capturing_config(tmp_path))) as client:
        with connect(client) as websocket:
            shake_hands(websocket)
            say_something(websocket)

    wav, jsonl, _ = only_capture(tmp_path)
    recorded = events(jsonl)
    names = [record["event"] for record in recorded]
    for expected in ("session_open", "heard", "speaking_started", "replied"):
        assert expected in names, f"{expected} is missing from the decision track"

    mic, _ = channels(wav)
    audio_ms = len(mic) / CAPTURE_RATE * 1000
    for record in recorded:
        assert record["t_ms"] >= 0
        # Every event indexes into the audio, give or take the frame the
        # capture was closed on.
        assert record["t_ms"] <= audio_ms + FRAME_MS * 2, (
            f"{record['event']} at {record['t_ms']} ms is past {audio_ms:.0f} ms of audio"
        )


def test_the_endpointers_opinion_is_in_the_track(tmp_path: Path) -> None:
    with TestClient(create_app(capturing_config(tmp_path))) as client:
        with connect(client) as websocket:
            shake_hands(websocket)
            say_something(websocket)

    _, jsonl, _ = only_capture(tmp_path)
    vad = [record for record in events(jsonl) if record["event"] == "vad"]
    assert len(vad) > 3, "the endpointer was sampled at decision points only"
    assert max(record["speech_ms"] for record in vad) > 0


def test_the_manifest_says_what_the_capture_was_made_against(tmp_path: Path) -> None:
    config = capturing_config(tmp_path, barge_in_min_speech_ms=321.0)
    with TestClient(create_app(config)) as client:
        with connect(client) as websocket:
            shake_hands(websocket)
            say_something(websocket)

    _, _, manifest_path = only_capture(tmp_path)
    manifest = json.loads(manifest_path.read_text())
    # The thresholds verbatim, not a hash: an old capture analysed after
    # they change is misleading unless it states its own.
    assert manifest["barge_in"]["min_speech_ms"] == 321.0
    assert manifest["barge_in"]["enabled"] is True
    assert manifest["server"]["revision"]
    assert manifest["device"]["mac"] == DEVICE_MAC.lower()
    assert manifest["device"]["client"] == DEVICE_UUID
    assert manifest["agent"] == "assistant"
    # Provider entries verbatim, so an exact model string survives.
    assert manifest["providers"]["tts"]["type"] == "mock"
    assert manifest["providers"]["asr"]["name"] == "mock"
    assert manifest["capture"]["complete"] is True
    assert manifest["capture"]["sample_rate"] == CAPTURE_RATE
    assert manifest["audio"]["frame_duration_ms"] == FRAME_MS


def test_the_manifest_carries_the_firmware_the_device_reported(tmp_path: Path) -> None:
    # The firmware version is the most load-bearing field in the
    # manifest, because echo cancellation is firmware-side, and the OTA
    # check-in is the only place a device ever states it.
    from tests.support.checkin import SYSTEM_INFO
    from vinga_server.ota import OTA_PATH

    with TestClient(create_app(capturing_config(tmp_path))) as client:
        client.post(
            OTA_PATH,
            json=SYSTEM_INFO,
            headers={"Device-Id": DEVICE_MAC, "Client-Id": DEVICE_UUID},
        )
        with connect(client) as websocket:
            shake_hands(websocket)
            say_something(websocket)

    _, _, manifest_path = only_capture(tmp_path)
    manifest = json.loads(manifest_path.read_text())
    assert manifest["device"]["firmware"] == "2.4.0"
    assert manifest["device"]["board"] == "waveshare-esp32-s3-touch-lcd-1.54"


def test_a_device_that_never_checked_in_still_gets_a_capture(tmp_path: Path) -> None:
    # A restarted server has no record of a device that checked in
    # before it, and that must not cost the recording.
    with TestClient(create_app(capturing_config(tmp_path))) as client:
        with connect(client) as websocket:
            shake_hands(websocket)
            say_something(websocket)

    _, _, manifest_path = only_capture(tmp_path)
    manifest = json.loads(manifest_path.read_text())
    assert "firmware" not in manifest["device"]
    assert manifest["device"]["mac"] == DEVICE_MAC.lower()


def test_a_conversation_survives_a_capture_directory_it_cannot_use(
    tmp_path: Path,
) -> None:
    # A recording is worth less than the conversation it is of.
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory")
    config = config_with_agent(
        server={"capture": {"enabled": True, "dir": str(blocked / "captures")}}
    )
    with TestClient(create_app(config)) as client:
        with connect(client) as websocket:
            shake_hands(websocket)
            texts, _ = say_something(websocket)
    assert texts, "the conversation did not survive an unusable capture directory"


@pytest.mark.parametrize("frames", [FRAME_BYTES])
def test_the_two_channels_share_one_timeline(tmp_path: Path, frames: int) -> None:
    # The property the whole file exists for. The microphone is fed for
    # a known stretch before anything is said back, so the reply must
    # start later in the file than the speech did, by about the time it
    # actually took.
    with TestClient(create_app(capturing_config(tmp_path))) as client:
        with connect(client) as websocket:
            shake_hands(websocket)
            say_something(websocket)

    wav, jsonl, _ = only_capture(tmp_path)
    mic, reply = channels(wav)
    assert len(mic) == len(reply), "the channels are different lengths"

    def first_loud(samples: list[int]) -> int:
        for index, sample in enumerate(samples):
            if abs(sample) > 100:
                return index
        raise AssertionError("channel never carried audio")

    speech_at = first_loud(mic) / CAPTURE_RATE * 1000
    reply_at = first_loud(reply) / CAPTURE_RATE * 1000
    assert reply_at > speech_at, "the reply appears before the speech that prompted it"
    started = next(e for e in events(jsonl) if e["event"] == "speaking_started")
    assert abs(reply_at - started["t_ms"]) < 200, (
        f"the reply audio starts at {reply_at:.0f} ms but speaking_started "
        f"says {started['t_ms']:.0f} ms"
    )


# Shaped like something an operator would be horrified to find in a log,
# and planted on both links of an exception chain: what must not reach a
# retained surface is the whole chain, not only its outermost message.
CODEC_SENTINEL = "sk-live-3f9a21c7-never-a-real-credential"

UTTERANCE = b"\x00\x00" * 320


class CodecUnavailable(RuntimeError):
    """Stands in for what a media library raises when it cannot open a
    codec: a class name worth logging, wrapped around a message that is
    not."""


def unopenable_codecs(*args: object, **kwargs: object) -> object:
    """A `CaptureAudio` that will not build.

    Three codec objects open here, and opening one runs PyAV. The
    constructor is the only step of starting a capture that can raise
    for a reason nothing on this side chose, which is what makes it the
    step worth a regression test.
    """
    try:
        raise OSError(f"libopus: no encoder for {CODEC_SENTINEL}")
    except OSError as unopenable:
        raise CodecUnavailable(
            f"could not build the capture codecs for {CODEC_SENTINEL}"
        ) from unopenable


def capturing_session(tmp_path: Path) -> tuple[DeviceSession, LoopingSocket]:
    """A session with a capture store, driven through `run`.

    Through `run` rather than through a test client because what is
    under test is a step inside the guard and what the session holds
    afterwards, and a client hands back no session to ask.
    """
    config = capturing_config(tmp_path)
    captures = CaptureStore(tmp_path / "captures", 900.0, 2000.0, 0.0)
    generations = world(config, providers=built_world(config))
    factory = bespoke_runtime_factory(generations, McpServers({}), lane_memory(), None)
    websocket = LoopingSocket()
    session = DeviceSession(cast(Any, websocket), generations, factory, captures)
    return session, websocket


def manifest_of(tmp_path: Path) -> dict | None:
    found = list((tmp_path / "captures").glob("*.json"))
    return json.loads(found[0].read_text()) if found else None


async def test_a_capture_whose_codecs_will_not_open_is_released_and_the_session_lives(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Starting a capture runs a media library, and a library that
    cannot open a codec raises.

    Two things must then be true, and neither was. The capture is
    attached to the session's events before its codecs are built and the
    field the close path releases is assigned only after they are, so a
    failure between the two used to leave an open recording and an
    attached consumer that nothing ever closed. And the exception left
    through `run` untouched, which puts a library's own prose, and
    whatever a chained one carries, in front of whoever is reading the
    process's output.

    So the capture is released where it failed and the conversation goes
    on without a recording. That last part is a deliberate change rather
    than a restoration: a codec failure used to end the session, and
    recording is best-effort everywhere else in this module, including
    in the capture store's own decline ("a conversation is worth more
    than a recording of it").
    """
    session, websocket = capturing_session(tmp_path)
    monkeypatch.setattr(session_module, "CaptureAudio", unopenable_codecs)

    with caplog.at_level("INFO"):
        task = asyncio.create_task(session.run())
        for _ in range(500):
            await asyncio.sleep(0.01)
            # `_start_capture` has no awaits in it, so a manifest on disk
            # means the whole of it has run: the capture opened, wrote
            # this file, and the construction after it either returned or
            # was handled.
            if manifest_of(tmp_path) is not None and session.runtime is not None:
                break
        else:
            raise AssertionError("the capture never opened")

        # Both hot paths the capture used to sit in, driven after the
        # failure: a mic frame in, and a whole reply out.
        session.listening = True
        encoder = OpusEncoder()
        for packet in encoder.encode(speech_pcm(120)):
            await session._handle_audio(framing.wrap(session.protocol_version, packet))
        await drive_reply(session, UTTERANCE)

        await websocket.close(1000, "goodbye")
        await asyncio.wait_for(task, timeout=5)

    # White-box for both reads, per the note on `attached_capture`: what
    # a released collaborator looks like is that there is nothing left to
    # ask about it.
    assert session._capture_audio is None
    assert attached_capture(session) is None, "the events capture was left attached"

    manifest = manifest_of(tmp_path)
    assert manifest is not None
    # The half that says the file was closed rather than abandoned: a
    # capture stranded at the failure keeps the `False` its start wrote.
    assert manifest["capture"]["complete"] is True

    written = both_formats(caplog)
    assert "recording could not start (CodecUnavailable)" in written
    assert CODEC_SENTINEL not in written
    assert "Traceback" not in written
    printed = capsys.readouterr()
    assert CODEC_SENTINEL not in printed.out + printed.err
