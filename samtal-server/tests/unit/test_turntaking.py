"""The floor, decided without a pipeline behind it.

`TurnTaking` reaches the orchestrator through `ReplyControl` and the
device through `DeviceOutput`, so both can be scripted and the gate
ladder driven straight onto each of its rungs: the speech floor, the
mid-transcription merge, the playback-onset refractory window, a
confirmation that heard nothing, and a confirmation that heard
something. No session, no socket, no provider.

The second half is the arithmetic in front of the gates, which nothing
used to exercise directly at all: the tail cap that bounds a
continuously listening session's buffer, and the pre-roll trim that
maps the endpointer's speech-start offset onto what is left of the
buffer after the cap has cut at it.
"""

from typing import cast

import pytest

import samtal_server.runtime.turntaking as turntaking_module
from samtal_server.config import ServerConfig
from samtal_server.device.boundary import PIPELINE_SAMPLE_RATE, DeviceOutput, PlayableAudio
from samtal_server.events import SessionEvents
from samtal_server.providers import AsrResult
from samtal_server.runtime.turntaking import TurnTaking
from tests.support.boundary import FakeDevice
from tests.support.events import both_formats, events, only
from tests.support.providers import ScriptedEndpointer

SESSION = "turn-taking"

# A confirmation the ladder can act on, and one it cannot.
HEARD = AsrResult(text="stop and listen")
NOTHING = AsrResult(text="   ")

# A credential-shaped value, planted in a failed confirmation's own
# message and again in the failure behind it, because a rendered
# traceback prints the whole chain and not just the exception caught.
SENTINEL = "sk-live-3f0a91c4-never-a-real-credential"


class FakeReply:
    """The orchestrator as the gate ladder sees it: whether a reply is
    in flight, the cancels and starts it is asked for, and the
    confirmation it answers with.

    `start_reply` flips it into replying, because that is what creating
    the task does on the real one, and the ladder's next rung turns on
    the answer."""

    def __init__(self, confirmation: AsrResult = HEARD) -> None:
        self._confirmation = confirmation
        self.confirmation_fails: BaseException | None = None
        self.started: list[tuple[bytes, AsrResult | None]] = []
        self.confirmed: list[bytes] = []
        self.cancels = 0

    def replying(self) -> bool:
        return bool(self.started)

    def start_reply(self, pcm: bytes, result: AsrResult | None) -> None:
        self.started.append((pcm, result))

    async def cancel_reply(self) -> None:
        self.cancels += 1
        self.started.clear()

    async def confirm_transcript(self, pcm: bytes) -> AsrResult:
        self.confirmed.append(pcm)
        if self.confirmation_fails is not None:
            raise self.confirmation_fails
        return self._confirmation


class UnresumableDevice(FakeDevice):
    """A device whose `resume_output` fails, which is what puts a second
    failure in front of the first one's cleanup."""

    def resume_output(self) -> None:
        raise RuntimeError("the pacing clock is wedged")


class SpeechAt(ScriptedEndpointer):
    """A scripted endpointer that also says where in the fed stream the
    speech began, which is the one number the pre-roll trim is
    arithmetic over."""

    def __init__(self, speech_ms: float, speech_start: int) -> None:
        super().__init__(speech_ms)
        self._speech_start = speech_start

    def speech_start(self) -> int | None:
        return self._speech_start


def chained(exc: BaseException) -> str:
    """Every exception reachable from one, rendered. A `raise` inside an
    active `except` suite attaches the exception being handled as
    `__context__`, so this is what an escaping failure hands to whoever
    catches it, whatever the line that failed chose to print."""
    seen: list[str] = []
    current: BaseException | None = exc
    while current is not None and len(seen) < 20:
        seen.append(f"{type(current).__name__}: {current}")
        current = current.__cause__ or current.__context__
    return "\n".join(seen)


def turn_taking(
    reply: FakeReply,
    server: ServerConfig | None = None,
    speech_ms: float = 600.0,
    device: FakeDevice | None = None,
) -> tuple[TurnTaking, FakeDevice]:
    """One `TurnTaking` on a recording device, with the endpointer
    already seeded the way an activation seeds it."""
    device = device if device is not None else FakeDevice()
    taking = TurnTaking(
        SessionEvents(SESSION),
        cast(DeviceOutput, device),
        server if server is not None else ServerConfig(),
        reply,
    )
    taking.endpointer = ScriptedEndpointer(speech_ms=speech_ms)
    return taking, device


async def replying_about(taking: TurnTaking, reply: FakeReply, pcm: bytes) -> None:
    """Get a reply in flight the way one gets there: an utterance is fed
    and finished, which hands it over and leaves the merge source set,
    and then the reply's own ASR returns and takes the marker down."""
    await taking.feed(pcm)
    await taking.finish_utterance()
    taking.clear_pending()


# --- the gate ladder, one test per rung -------------------------------


async def test_too_little_speech_is_a_noise_blip_and_never_reaches_the_reply(
    caplog: pytest.LogCaptureFixture,
) -> None:
    reply = FakeReply()
    taking, device = turn_taking(reply, speech_ms=100.0)

    with caplog.at_level("INFO"):
        await replying_about(taking, reply, b"\x01\x02" * 800)
        await taking.feed(b"\x03\x04" * 800)
        await taking.finish_utterance(endpointed=True)

    suppressed = only(caplog, "barge_in_suppressed")
    assert suppressed.reason == "min_speech"
    assert suppressed.speech_ms == 100
    # The reply lives, nothing was asked of ASR, and the utterance that
    # was dropped is still reported as a turn the user took.
    assert reply.cancels == 0
    assert reply.confirmed == []
    assert len(reply.started) == 1
    assert device.turn_ends == 2


async def test_a_barge_in_inside_the_replys_own_asr_merges_the_two_halves(
    caplog: pytest.LogCaptureFixture,
) -> None:
    reply = FakeReply()
    taking, _ = turn_taking(reply)
    head = b"\x01\x02" * 800
    tail = b"\x03\x04" * 800

    with caplog.at_level("INFO"):
        # No clear_pending: this reply is still inside its ASR, which is
        # what makes the head of the sentence still destroyable.
        await taking.feed(head)
        await taking.finish_utterance()
        await taking.feed(tail)
        await taking.finish_utterance(endpointed=True)

    assert only(caplog, "barge_in_merged").speech_ms == 600
    assert reply.cancels == 1
    # One reply answering the whole sentence, and no confirmation: the
    # merge is decided on the marker alone.
    assert reply.confirmed == []
    assert reply.started == [(head + tail, None)]


async def test_the_playback_onset_transient_is_swallowed_by_the_refractory_window(
    caplog: pytest.LogCaptureFixture,
) -> None:
    reply = FakeReply()
    taking, device = turn_taking(reply, ServerConfig(barge_in_refractory_ms=100_000))

    with caplog.at_level("INFO"):
        await replying_about(taking, reply, b"\x01\x02" * 800)
        # The reply is speaking, which is what starts the window.
        await device.send_audio(PlayableAudio([b"frame"]))
        await taking.feed(b"\x03\x04" * 800)
        await taking.finish_utterance(endpointed=True)

    suppressed = only(caplog, "barge_in_suppressed")
    assert suppressed.reason == "refractory"
    assert suppressed.speech_ms == 600
    assert reply.cancels == 0
    assert reply.confirmed == []
    assert len(reply.started) == 1


async def test_a_confirmation_that_heard_nothing_resumes_the_reply_it_paused(
    caplog: pytest.LogCaptureFixture,
) -> None:
    reply = FakeReply(NOTHING)
    taking, device = turn_taking(reply, ServerConfig(barge_in_refractory_ms=0))
    interruption = b"\x03\x04" * 800

    with caplog.at_level("INFO"):
        await replying_about(taking, reply, b"\x01\x02" * 800)
        await device.send_audio(PlayableAudio([b"frame"]))
        await taking.feed(interruption)
        await taking.finish_utterance(endpointed=True)

    suppressed = only(caplog, "barge_in_suppressed")
    assert suppressed.reason == "no_transcript"
    assert suppressed.speech_ms == 600
    # The pause cost one ASR latency and nothing else: the frames flow
    # again, the reply was never cancelled, and no second one started.
    assert reply.confirmed == [interruption]
    assert reply.cancels == 0
    assert len(reply.started) == 1
    assert device.paused is False
    assert taking.output_paused is False


async def test_a_confirmed_barge_in_cancels_and_hands_its_transcript_on(
    caplog: pytest.LogCaptureFixture,
) -> None:
    reply = FakeReply(HEARD)
    taking, device = turn_taking(reply, ServerConfig(barge_in_refractory_ms=0), speech_ms=700.0)
    interruption = b"\x03\x04" * 800

    with caplog.at_level("INFO"):
        await replying_about(taking, reply, b"\x01\x02" * 800)
        await device.send_audio(PlayableAudio([b"frame"]))
        await taking.feed(interruption)
        await taking.finish_utterance(endpointed=True)

    barged = only(caplog, "barge_in")
    assert barged.speech_ms == 700
    # The reply was speaking, so the cancel decision is timed from it.
    assert barged.speaking_ms >= 0
    assert events(caplog, "barge_in_suppressed") == []
    assert reply.cancels == 1
    # The confirmation is the new reply's ASR too, which is what keeps
    # one interruption at one transcription.
    assert reply.confirmed == [interruption]
    assert reply.started == [(interruption, HEARD)]
    assert device.paused is False


async def test_a_confirmation_that_could_not_be_run_leaves_the_reply_alone(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The catch that keeps a broken ear from cancelling replies: the
    frames resume and the utterance is dropped. What it logs is the
    next test's claim."""
    reply = FakeReply()
    reply.confirmation_fails = TimeoutError()
    taking, device = turn_taking(reply, ServerConfig(barge_in_refractory_ms=0))

    with caplog.at_level("INFO"):
        await replying_about(taking, reply, b"\x01\x02" * 800)
        await taking.feed(b"\x03\x04" * 800)
        await taking.finish_utterance(endpointed=True)

    assert events(caplog, "barge_in") == []
    assert events(caplog, "barge_in_suppressed") == []
    assert reply.cancels == 0
    assert len(reply.started) == 1
    assert device.paused is False
    assert taking.output_paused is False


async def test_a_failed_confirmation_names_its_class_and_not_what_it_said(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """#183: this arm used to be a `logger.exception`, which put the
    provider's own message, the chain behind it and a traceback of both
    onto the retained log. What an ASR client raises is far-side text:
    an SDK that cannot authenticate quotes what it was given, and one
    that cannot reach its endpoint quotes the URL. The stage, the
    provider and the host stay diagnosable through `provider_failed`,
    which the runtime's own watch emits around this call."""
    reply = FakeReply()
    failure = TimeoutError(f"transcribe timed out, key {SENTINEL}")
    failure.__cause__ = ConnectionError(f"401 from the endpoint, key {SENTINEL}")
    reply.confirmation_fails = failure
    taking, device = turn_taking(reply, ServerConfig(barge_in_refractory_ms=0))

    with caplog.at_level("INFO"):
        await replying_about(taking, reply, b"\x01\x02" * 800)
        await taking.feed(b"\x03\x04" * 800)
        await taking.finish_utterance(endpointed=True)

    written = both_formats(caplog)
    assert "barge-in confirmation failed: TimeoutError" in written
    assert SENTINEL not in written
    # And the resume-and-drop the line reports is still what happened.
    assert reply.cancels == 0
    assert taking.output_paused is False


async def test_a_failure_during_the_cleanup_carries_nothing_of_the_first(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Printing the class name is only half of not printing the message.
    Inside an active `except` suite the provider's exception is still
    the one being handled, so a second failure raised there, from the
    resume or from the logging call itself, escapes with the first
    attached as its `__context__` and hands the message to whoever
    catches it. The report and the cleanup therefore run after the
    suite has been left, which is the discipline the device edge
    follows when it raises `DeviceGone`."""
    reply = FakeReply()
    failure = TimeoutError(f"transcribe timed out, key {SENTINEL}")
    failure.__cause__ = ConnectionError(f"401 from the endpoint, key {SENTINEL}")
    reply.confirmation_fails = failure
    taking, _ = turn_taking(
        reply, ServerConfig(barge_in_refractory_ms=0), device=UnresumableDevice()
    )

    with caplog.at_level("INFO"):
        await replying_about(taking, reply, b"\x01\x02" * 800)
        await taking.feed(b"\x03\x04" * 800)
        with pytest.raises(RuntimeError) as caught:
            await taking.finish_utterance(endpointed=True)

    assert SENTINEL not in chained(caught.value)
    assert SENTINEL not in both_formats(caplog)


# --- the arithmetic in front of them ----------------------------------


async def test_the_buffer_keeps_only_a_bounded_tail_of_what_was_fed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A realtime session listens through the silences too, so the buffer
    # is a bounded tail of recent audio rather than the whole session.
    monkeypatch.setattr(turntaking_module, "UTTERANCE_TAIL_BYTES", 2000)
    reply = FakeReply()
    taking, _ = turn_taking(reply)

    for mark in (b"\x01", b"\x02", b"\x03"):
        await taking.feed(mark * 1000)
    await taking.finish_utterance()

    (pcm, _result), = reply.started
    # Exactly the cap, and exactly the newest of what was fed.
    assert pcm == b"\x02" * 1000 + b"\x03" * 1000


async def test_the_pre_roll_is_all_that_survives_in_front_of_the_speech() -> None:
    # A second of audio whose speech began half a second in: the default
    # 300 ms pre-roll keeps the first phoneme intact and the 200 ms of
    # silence before it does not ride along to ASR (#14).
    reply = FakeReply()
    taking, _ = turn_taking(reply)
    second = PIPELINE_SAMPLE_RATE * 2
    taking.endpointer = SpeechAt(speech_ms=600.0, speech_start=second // 2)

    await taking.feed(b"\x00" * second)
    await taking.finish_utterance()

    (pcm, _result), = reply.started
    pre_roll = int(300 / 1000 * PIPELINE_SAMPLE_RATE) * 2
    assert len(pcm) == second - (second // 2 - pre_roll)


async def test_the_trim_maps_the_speech_start_through_what_the_cap_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The endpointer counts its speech-start offset over everything it
    # was fed; the buffer no longer holds all of it. The drop accounting
    # is what makes the two the same position.
    monkeypatch.setattr(turntaking_module, "UTTERANCE_TAIL_BYTES", 20_000)
    reply = FakeReply()
    taking, _ = turn_taking(reply)
    pre_roll = int(300 / 1000 * PIPELINE_SAMPLE_RATE) * 2
    taking.endpointer = SpeechAt(speech_ms=600.0, speech_start=25_000)

    for _ in range(3):
        await taking.feed(b"\x07" * 10_000)
    await taking.finish_utterance()

    # 30,000 fed, 10,000 dropped, so the speech begins 15,000 into what
    # is left, and the pre-roll backs up from there.
    (pcm, _result), = reply.started
    assert len(pcm) == 20_000 - (15_000 - pre_roll)


async def test_a_session_with_no_endpointer_yet_buffers_nothing() -> None:
    """The guard `audio` used to carry: a runtime whose agent has not
    been activated has nowhere to put a frame, and dropping it is what
    keeps the buffer from filling with audio no endpointer ever saw."""
    reply = FakeReply()
    taking, _ = turn_taking(reply)
    taking.endpointer = None

    await taking.feed(b"\x01\x02" * 800)
    await taking.manual_stop()

    assert reply.started == []
