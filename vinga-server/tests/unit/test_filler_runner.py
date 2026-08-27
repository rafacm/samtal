"""The latency mask, decided without a pipeline behind it.

`FillerRunner` reaches the device through `DeviceOutput` and whoever
holds the floor with two reads, so both can be scripted and the
timer driven onto each of its outcomes: the fire that masks a slow
reply, the reply that spoke first, an agent with no clip of its own,
the two stand-downs that yield to the user, and the arbitration between
a clip already sounding and the reply's own audio arriving. No session,
no socket, no synthesis.

The clips here are PCM this module made up, at the device's own output
rate, because what a clip contains is `filler.py`'s business and what
this runner does with one is the same whatever it holds.
"""

import asyncio
import logging
from collections.abc import Sequence
from typing import cast

import pytest

from tests.support.boundary import FakeDevice
from tests.support.configs import OUTPUT_RATE
from tests.support.events import both_formats, events, only
from vinga_server.device.boundary import DeviceGone, DeviceOutput, PlayableAudio
from vinga_server.events import SessionEvents
from vinga_server.filler import FillerClips
from vinga_server.runtime.filler_runner import FillerRunner

SESSION = "filler-runner"

# The thread the agent below is talking on, in the shape the runtime
# mints. Every event a runner emits names it beside the agent.
THREAD = "9f0c1d2e3a4b5c6d7e8f90a1b2c3d4e5"

# A credential-shaped value, planted in the message of whatever the
# playback path fails with.
SENTINEL = "sk-live-6d17b3e0-never-a-real-credential"

# A quarter second of clip, which the device's encoder makes several
# frames of, so "the clip went out" is a claim about audio rather than
# about one packet.
CLIP = b"\x11\x22" * (OUTPUT_RATE // 4)

DELAY_MS = 10.0
# Comfortably past the delay: a fire that was going to happen has.
FIRED_S = 0.05


def clips_for(*phrases: str) -> FillerClips:
    """One agent's cache, the shape the boot builds, at the device's own
    rate so the resampling in the middle changes nothing."""
    return FillerClips(
        delay_ms=DELAY_MS,
        phrases=phrases,
        clips=tuple(CLIP for _ in phrases),
        sample_rate=OUTPUT_RATE,
    )


class FakeTurn:
    """The floor as the fire-time stand-down sees it: how much speech
    the endpointer is holding, and whether the outgoing frames are
    paused for a barge-in confirmation. Both are set by the test and
    neither is written by the runner, which is the whole of what the
    runner asks of a `TurnTaking`, the class the annotation names."""

    def __init__(self, speech_ms: int = 0, paused: bool = False) -> None:
        self.speech = speech_ms
        self.paused = paused

    def speech_ms(self) -> int:
        return self.speech

    @property
    def output_paused(self) -> bool:
        return self.paused


class HeldDevice(FakeDevice):
    """A device that holds a send open until it is released, which is
    what lets a clip be caught mid-flight."""

    def __init__(self) -> None:
        super().__init__()
        self.release = asyncio.Event()

    async def send_audio(self, batch: PlayableAudio) -> None:
        await self.release.wait()
        await super().send_audio(batch)


class BrokenDevice(FakeDevice):
    """A device one of whose calls fails, which is what tells the
    playback path's two arms apart: a send raising `DeviceGone` is the
    device leaving, and anything else, from the send or from the
    encoder, is a bug in this process."""

    def __init__(
        self, send: BaseException | None = None, encode: BaseException | None = None
    ) -> None:
        super().__init__()
        self._send = send
        self._encode = encode

    def encode_audio(self, pcm: bytes) -> PlayableAudio:
        if self._encode is not None:
            raise self._encode
        return super().encode_audio(pcm)

    async def send_audio(self, batch: PlayableAudio) -> None:
        if self._send is not None:
            raise self._send
        await super().send_audio(batch)


def runner_for(
    fillers: dict[str, FillerClips],
    turn: FakeTurn | None = None,
    device: FakeDevice | None = None,
    agent: str = "poet",
    agents: Sequence[str] = ("poet",),
) -> tuple[FillerRunner, FakeDevice]:
    """One runner on a recording device, talking as `agent` on `THREAD`
    the way an activation leaves the events object: it writes both, and
    every event the runner emits names both."""
    session_events = SessionEvents(SESSION)
    session_events.agent = agent
    session_events.conversation = THREAD
    device = device if device is not None else FakeDevice()
    runner = FillerRunner(
        session_events,
        cast(DeviceOutput, device),
        fillers,
        agents,
        turn if turn is not None else FakeTurn(),
    )
    return runner, device


# --- the timer and what it finds when it expires ----------------------


async def test_a_reply_that_has_not_spoken_by_the_threshold_is_masked(
    caplog: pytest.LogCaptureFixture,
) -> None:
    runner, device = runner_for({"poet": clips_for("Hmm, let me see...")})

    with caplog.at_level("INFO"):
        runner.arm()
        await asyncio.sleep(FIRED_S)
        await runner.settle()

    played = only(caplog, "filler_played")
    assert played.agent == "poet"
    assert played.phrase_index == 0
    assert played.delay_ms >= DELAY_MS
    # It went out through the device's normal speaking path.
    assert ("begin",) in device.calls
    assert len(device.sent) > 1
    assert runner.fires == 1
    # The turn settled, so nothing of its filler is left over.
    assert runner.armed is False
    assert runner.sounding is False


async def test_a_reply_that_spoke_first_leaves_the_timer_nothing_to_mask(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The silence the timer was armed against is over: the reply's own
    audio started, and a clip now would talk over it."""
    runner, device = runner_for({"poet": clips_for("Hmm, let me see...")})
    await device.send_audio(PlayableAudio([b"reply-frame"]))

    with caplog.at_level("INFO"):
        runner.arm()
        await asyncio.sleep(FIRED_S)
        await runner.settle()

    assert events(caplog, "filler_played") == []
    assert device.sent == [b"reply-frame"]
    assert runner.fires == 0


async def test_a_session_bound_only_to_fillerless_agents_arms_nothing() -> None:
    runner, device = runner_for({})

    runner.arm()
    assert runner.armed is False
    await runner.settle()

    assert device.sent == []
    assert runner.fires == 0


async def test_an_agent_the_conversation_could_become_arms_the_timer(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The arming rule asks the bound agents, not just the one talking,
    because a handover mid-turn can reach a masked agent before the
    reply speaks. The fire then resolves the agent active at that
    moment, and one with no clip of its own plays nothing, quietly: no
    audio, no event, no state left behind."""
    runner, device = runner_for(
        {"tutor": clips_for("Hmm, mal überlegen...")},
        agent="poet",
        agents=("poet", "tutor"),
    )

    with caplog.at_level("INFO"):
        runner.arm()
        assert runner.armed is True
        await asyncio.sleep(FIRED_S)
        await runner.settle()

    assert events(caplog, "filler_played") == []
    assert events(caplog, "filler_skipped") == []
    assert device.sent == []
    assert runner.fires == 0
    assert runner.sounding is False


# --- the two stand-downs ----------------------------------------------


async def test_a_fire_into_live_user_speech_is_skipped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The endpointer holds unresolved speech at fire time, which is a
    user mid-continuation after a premature endpoint: the timer stands
    down rather than talking over them."""
    runner, device = runner_for(
        {"poet": clips_for("Hmm, let me see...")}, turn=FakeTurn(speech_ms=420)
    )

    with caplog.at_level("INFO"):
        runner.arm()
        await asyncio.sleep(FIRED_S)
        await runner.settle()

    skipped = only(caplog, "filler_skipped")
    assert skipped.reason == "user_speaking"
    assert skipped.speech_ms == 420
    assert skipped.agent == "poet"
    assert events(caplog, "filler_played") == []
    # The skip consumed no phrase and left no state behind.
    assert device.sent == []
    assert runner.fires == 0
    assert runner.sounding is False


async def test_a_fire_during_a_barge_in_confirmation_is_skipped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The frames are held while a barge-in is confirmed, so the reply
    in flight is about to be cancelled: masking a doomed turn is worse
    than not masking it."""
    runner, device = runner_for(
        {"poet": clips_for("Hmm, let me see...")}, turn=FakeTurn(paused=True)
    )

    with caplog.at_level("INFO"):
        runner.arm()
        await asyncio.sleep(FIRED_S)
        await runner.settle()

    skipped = only(caplog, "filler_skipped")
    assert skipped.reason == "barge_in_pending"
    assert skipped.agent == "poet"
    assert not hasattr(skipped, "speech_ms")
    assert events(caplog, "filler_played") == []
    assert device.sent == []
    assert runner.fires == 0


# --- the arbitration against the reply's own audio --------------------


async def test_the_replys_own_audio_stands_an_unfired_timer_down(
    caplog: pytest.LogCaptureFixture,
) -> None:
    runner, device = runner_for({"poet": clips_for("Hmm, let me see...")})

    with caplog.at_level("INFO"):
        runner.arm()
        await runner.tail()
        # Well past the delay: the timer is gone rather than deferred.
        await asyncio.sleep(FIRED_S)
        await runner.settle()

    assert events(caplog, "filler_played") == []
    assert device.sent == []
    assert runner.fires == 0


async def test_the_replys_own_audio_waits_out_a_clip_already_sounding(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Once a clip is claimed it is not cut mid-word: the first real
    sentence queues behind its tail instead."""
    device = HeldDevice()
    runner, _ = runner_for({"poet": clips_for("Hmm, let me see...")}, device=device)

    with caplog.at_level("INFO"):
        runner.arm()
        await asyncio.sleep(FIRED_S)
        # Past the checks and into the send, which is what claiming it
        # synchronously before the first await is for.
        assert runner.sounding is True
        waiting = asyncio.create_task(runner.tail())
        await asyncio.sleep(0.01)
        assert not waiting.done()
        device.release.set()
        await waiting
        await runner.settle()

    only(caplog, "filler_played")
    assert len(device.sent) > 1


async def test_a_cancelled_reply_takes_even_a_sounding_filler_with_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A barge-in or an abort kills the reply, and the filler is reply
    audio, so it dies with the reply rather than being waited out. That
    is the whole difference between abandoning and settling: a clip
    mid-send survives a settle and does not survive this."""
    device = HeldDevice()
    runner, _ = runner_for({"poet": clips_for("Hmm, let me see...")}, device=device)

    with caplog.at_level("INFO"):
        runner.arm()
        await asyncio.sleep(FIRED_S)
        # Mid-send and still held, which is exactly the state `tail`
        # waits out.
        assert runner.sounding is True
        runner.abandon()
        settling = asyncio.create_task(runner.settle())
        await asyncio.sleep(0.01)
        # The send is still held, so a settle that had to see this clip
        # out would still be sitting here. Asked as "already finished"
        # rather than as a timeout, because the settle suppresses the
        # cancellation a timeout would deliver and would look like it
        # had returned on its own.
        assert settling.done(), "the settle waited out a clip it was told to abandon"
        await settling

    # The clip announced itself and then went nowhere: the send it was
    # cancelled inside of never delivered a frame.
    only(caplog, "filler_played")
    assert device.sent == []
    assert runner.armed is False
    assert runner.sounding is False


async def test_one_clip_per_turn_and_the_variants_rotate(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The fire counter is what rotates the phrases, and it is per
    session rather than per turn, so the next slow turn is masked with
    the next thing to say."""
    runner, device = runner_for({"poet": clips_for("Hmm...", "One moment...")})

    with caplog.at_level("INFO"):
        for _ in range(2):
            device.reply_started()
            runner.arm()
            await asyncio.sleep(FIRED_S)
            await runner.settle()

    assert [record.phrase_index for record in events(caplog, "filler_played")] == [0, 1]
    assert runner.fires == 2


# --- what a clip that could not be played may cost --------------------


async def test_a_device_that_left_mid_clip_ends_the_clip_and_says_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The documented outcome: a device that went away mid-clip ends the
    clip, not the session, and quietly, because the disconnect is not a
    failure of the mask and the session it belongs to is already on its
    way down."""
    device = BrokenDevice(send=DeviceGone("the device disconnected"))
    runner, _ = runner_for({"poet": clips_for("Hmm, let me see...")}, device=device)

    with caplog.at_level("INFO"):
        runner.arm()
        await asyncio.sleep(FIRED_S)
        await runner.settle()

    # It announced itself and then went nowhere, with nothing said about
    # it beyond that announcement.
    only(caplog, "filler_played")
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
    assert device.sent == []
    assert runner.armed is False
    assert runner.sounding is False


async def test_a_clip_that_would_not_encode_is_named_by_class_and_swallowed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """#182's decision: resampling, encoding and the encoder flush are
    no longer covered by the disconnect arm, so a failure in any of them
    is reported as the bug it is. The class name and nothing else: this
    runs a codec over provider-synthesized audio, and a `logger.exception`
    here would put whatever it raised, and the chain behind it, onto the
    retained log."""
    device = BrokenDevice(encode=ValueError(f"the encoder refused, near {SENTINEL}"))
    runner, _ = runner_for({"poet": clips_for("Hmm, let me see...")}, device=device)

    with caplog.at_level("INFO"):
        runner.arm()
        await asyncio.sleep(FIRED_S)
        await runner.settle()

    written = both_formats(caplog)
    assert "filler playback failed: ValueError" in written
    assert SENTINEL not in written
    # The mask stood down exactly as it always did: swallowed, nothing
    # sent, nothing left armed, and a settle that ended cleanly, which
    # is what "the reply it masks is unharmed" means here.
    assert device.sent == []
    assert runner.armed is False
    assert runner.sounding is False


async def test_a_local_bug_in_the_send_is_no_longer_read_as_a_disconnect(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The one deliberate behavior change of #182. `DeviceGone`
    subclasses `RuntimeError` and this arm used to catch the base class,
    so a bare `RuntimeError` from anywhere in the block was returned on
    in silence as though the device had left. The edge translates every
    shape of a vanished device into `DeviceGone` (#137), so what is left
    is a bug in this process, and it now says so instead of hiding
    behind a disconnect."""
    device = BrokenDevice(send=RuntimeError(f"a local bug, near {SENTINEL}"))
    runner, _ = runner_for({"poet": clips_for("Hmm, let me see...")}, device=device)

    with caplog.at_level("INFO"):
        runner.arm()
        await asyncio.sleep(FIRED_S)
        await runner.settle()

    written = both_formats(caplog)
    assert "filler playback failed: RuntimeError" in written
    assert SENTINEL not in written
    assert device.sent == []
    assert runner.armed is False
    assert runner.sounding is False
