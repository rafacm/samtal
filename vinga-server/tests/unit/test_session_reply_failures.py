"""What a failure inside a reply is taken for.

The reply body's outer catch reads the same moment in one of two ways: the
device went away, which is ordinary and stays silent, or something here is
broken, which is a bug and belongs on the record. Issue #137 separated them.
The device edge translates both of the transport's vanished-device shapes into
`DeviceGone`, so the body can catch that type alone, and everything else, a
provider's request failure included, reaches the reporting arm instead of being
read as a disconnect.

Two halves, per the plan. The first pins that a provider call failing mid-reply
is reported as a provider failure and never swallowed. The second is the half
that changed: a bare `RuntimeError` raised while speaking is now a bug on the
record, while a `DeviceGone` raised in the same place is still nothing at all.

What that record may say is the third thing pinned here. The arm that reports
now catches everything a provider raises, so it names the exception's class and
stops there: no traceback and no message text, neither of which is ours to
trust once it has been anywhere near a response body.

The fourth is what the user hears, which used to be nothing at all. The last
section drives the phrase that arm now says (#384), the two arms that still say
nothing, and the switch that puts a deployment back where it was.
"""

import asyncio
import contextlib
import json
import logging
from dataclasses import replace
from typing import Any, cast

import pytest

from tests.support.configs import POET_MAC, base_config
from tests.support.events import events, only
from tests.support.providers import BrokenTts, StallingLlm, Unreachable, built_world
from tests.support.sessions import (
    drive_reply,
    reply_with,
    session_for,
    start_reply,
)
from tests.support.sockets import FRAME, CancellingSocket, OrderedSocket, QuietSocket
from vinga_server.config import Config
from vinga_server.device.boundary import DeviceGone
from vinga_server.filler import FallbackClip, build_agent_fillers
from vinga_server.logs import TEXT_FORMAT, JsonFormatter
from vinga_server.providers import ProviderCallError, ProviderCallTimeout
from vinga_server.providers.base import TtsProvider
from vinga_server.providers.mock import MockTts

# One frame of silence, which the mock ASR answers with "hello" whatever it
# holds: these tests are about how the reply ends, not what was said.
UTTERANCE = b"\x00\x00" * 320

# Planted where a real secret plausibly ends up: in the message of a failure
# that came back from a network, and in the message of the failure behind it.
SENTINEL = "sk-test-1d0c7a6b-never-a-real-credential"


# The arm's own line, matched with its punctuation. The phrase the arm
# now says has a record of its own whose sentence also contains "reply
# failed", and a looser match would count that as a second report.
REPORTED = ": reply failed: "


def reply_failure(caplog: pytest.LogCaptureFixture) -> logging.LogRecord:
    """The one record the generic arm emitted."""
    matching = [record for record in caplog.records if REPORTED in record.getMessage()]
    assert len(matching) == 1, f"expected one reply failure, got {len(matching)}"
    return matching[0]


def rendered(record: logging.LogRecord) -> str:
    """One record through both formats a deployment can be running, since
    what a secret must stay out of is whatever is written down. The JSON
    one is the container default and the retained surface; the text one
    is what a terminal shows."""
    return JsonFormatter().format(record) + logging.Formatter(TEXT_FORMAT).format(record)


def a_bug_carrying_a_secret() -> RuntimeError:
    """A local failure with the sentinel in its own message and another
    copy in the failure behind it, which is the shape a wrapped vendor
    error has: `raise` inside an `except` leaves the first reachable
    from the second."""
    behind = ValueError(f"the endpoint answered {SENTINEL}")
    bug = RuntimeError(f"the encoder is wedged on {SENTINEL}")
    bug.__cause__ = behind
    return bug


async def reply_broken_while_speaking(
    exc: BaseException, caplog: pytest.LogCaptureFixture
) -> None:
    """One whole reply whose speaking step raises `exc`.

    Speaking is where a local bug in a reply actually lives (a resampler,
    an encoder, a framing helper), it sits inside the reply body's `try`,
    and it is past every provider, so nothing reports the failure before
    the outer catch decides what to make of it."""
    session = session_for(base_config(), POET_MAC, websocket=cast(Any, QuietSocket()))

    async def speak(synthesis: Any, resampler: Any, into: list[str]) -> None:
        synthesis.cancel()
        await synthesis.wait_cancelled()
        raise exc

    # White-box: the failure under test is a device that vanishes while
    # a sentence is being spoken, and both shapes of it are raised by
    # the transport underneath. Standing in for the speaking step is
    # what puts the exception at that instant rather than somewhere a
    # socket happened to fail.
    session.runtime._speak = speak  # type: ignore[method-assign]
    with caplog.at_level("INFO"):
        await drive_reply(session, UTTERANCE)


async def test_a_provider_call_error_from_the_tts_is_reported_as_one(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The taxonomy half. A provider's request failure is not a
    `RuntimeError` and never was a vanished device; it reaches the
    operator as the provider failure it is, with the stage and the
    taxonomy class in the record."""
    failed = await reply_with("tts", ProviderCallError("elevenlabs request failed"), caplog)
    assert failed.stage == "tts"
    assert failed.error == "ProviderCallError"
    assert "failed" in failed.getMessage()
    # And it was not swallowed on the way out: the reply ended in the
    # arm that reports, not in the one that returns.
    assert "reply failed" in caplog.text


async def test_a_provider_call_timeout_is_worded_as_a_wait(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The taxonomy's timeout is a `TimeoutError`, which is the whole
    reason it inherits one, and the sentence says the symptom was a
    wait."""
    failed = await reply_with("tts", ProviderCallTimeout("elevenlabs timed out"), caplog)
    assert failed.error == "ProviderCallTimeout"
    assert "timed out" in failed.getMessage()


class ApiTimeoutError(Exception):
    """A name that says timeout, on a class that is not one. The shape
    is real: `openai.APITimeoutError` is an `APIConnectionError`, and
    `httpx.TimeoutException` inherits from neither `TimeoutError` nor
    that."""


class DeadlineExceeded(TimeoutError):
    """The other way round: a wait whose name says nothing of the kind.
    `FirstTokenTimeout` happens to be named for what it is, but nothing
    makes that a rule."""


async def test_the_wording_follows_the_type_and_not_the_class_name(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """What the two tests above cannot show, because both taxonomy
    classes are named after what they are: the sentence is chosen by
    `isinstance` and the deleted substring match would disagree with
    it on both of these.

    A vendor class named for a timeout that is not one now reads
    "failed". Nothing regresses by it, because no provider hands the
    pipeline an SDK exception any more: the five wrap their SDKs'
    timeouts into `ProviderCallTimeout`, which is a `TimeoutError` by
    inheritance rather than by spelling."""
    named = await reply_with("tts", ApiTimeoutError("the name says timeout"), caplog)
    assert named.error == "ApiTimeoutError"
    assert "failed" in named.getMessage()
    assert "timed out" not in named.getMessage()

    caplog.clear()
    typed = await reply_with("tts", DeadlineExceeded("the type says timeout"), caplog)
    assert typed.error == "DeadlineExceeded"
    assert "timed out" in typed.getMessage()


async def test_a_bug_while_speaking_is_reported_rather_than_swallowed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The catch half, and the one that changed. A bare `RuntimeError`
    reaching the reply body can only be a local bug now that the edge
    translates a vanished device, so it lands under "reply failed"
    instead of returning silently."""
    await reply_broken_while_speaking(RuntimeError("the encoder is wedged"), caplog)
    assert reply_failure(caplog).getMessage().endswith("reply failed: RuntimeError")


async def test_a_reported_failure_says_the_class_and_nothing_else(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The report is the class name. Everything else a failure carries
    reached this arm from somewhere, and since the catch narrowed, that
    somewhere includes every provider on the far side of a network: a
    message that quotes a response body, and a chain of causes behind
    it that a traceback would print in full. Neither is written down,
    in either format."""
    await reply_broken_while_speaking(a_bug_carrying_a_secret(), caplog)

    failed = reply_failure(caplog)
    assert failed.getMessage().endswith("reply failed: RuntimeError")
    assert failed.exc_info is None
    assert SENTINEL not in caplog.text
    assert all(SENTINEL not in str(record.__dict__) for record in caplog.records)
    assert all(SENTINEL not in rendered(record) for record in caplog.records)


async def test_a_vanished_device_while_speaking_still_says_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The other side of the same catch. A device that goes away is not
    a failure of anything, and an operator reading "reply failed" with a
    traceback would go looking for a bug that is not there."""
    await reply_broken_while_speaking(DeviceGone("the device disconnected"), caplog)
    assert "reply failed" not in caplog.text


# --- what the failure arm says ----------------------------------------
#
# The fourth thing pinned here, and the one that changed the experience:
# a terminally failed reply used to be silence, so a broken pipeline and
# a slow one were the same turn from the couch (#384). The arm that
# reports now speaks too, from a phrase cached in the agent's own voice
# when the world was built.
#
# What speaks is deliberately only this arm. The two cases above stay
# exactly as quiet as they were, each for its own reason, and both are
# asserted below rather than left to the arms' shapes.


async def cached(config: Config, tts: Any = None) -> dict[str, Any]:
    """The failure phrases a boot would have synthesized for this
    configuration, through the voice it configured or the one a case
    hands in."""
    providers = built_world(config).agents
    if tts is not None:
        providers = {
            name: replace(entry, tts=cast(Any, tts)) for name, entry in providers.items()
        }
    return (await build_agent_fillers(config, providers)).fallbacks


async def failing_session(
    config: Config | None = None, socket: Any = None, tts: Any = None
) -> Any:
    """A session whose next reply fails on its model, with the failure
    phrases its world would hold.

    The model rather than a voice, so nothing about the failure is also
    the thing that would have spoken the phrase: what is under test is
    that a cached clip survives a provider going down, and a case that
    broke the TTS could not tell that from a phrase that never existed.
    """
    settled = config if config is not None else base_config()
    session = session_for(
        settled,
        POET_MAC,
        stages={"llm": cast(Any, Unreachable("llm", ConnectionRefusedError("no route")))},
        fallbacks=await cached(settled, tts),
    )
    session.websocket = cast(Any, socket if socket is not None else OrderedSocket())
    return session


def wire(socket: OrderedSocket) -> list[object]:
    """What one turn put on the wire, minus the one field two runs of it
    cannot share: the session id, which is minted per connection."""
    return [
        FRAME
        if one == FRAME
        else {key: held for key, held in json.loads(one).items() if key != "session_id"}
        for one in socket.sent
    ]


def with_fallback(**section: object) -> Config:
    """The two-agent world with the poet's fallback section written out,
    which is what a deployment that thought about this has."""
    return base_config(
        agents={
            "poet": {"prompt": "POET", "tts": "tenor", "fallback": section},
            "tutor": {"prompt": "TUTOR", "tts": "alto"},
        }
    )


async def test_a_failed_reply_says_the_phrase_and_plays_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The whole of what #384 asks for, at the surface a device sees: the
    sentence is announced, so it renders, and audio follows it, so it is
    heard."""
    config = with_fallback(enabled=True, phrase="I could not answer that one.")
    session = await failing_session(config)

    with caplog.at_level("INFO"):
        await drive_reply(session, UTTERANCE)

    socket = cast(OrderedSocket, session.websocket)
    assert socket.announced() == ["I could not answer that one."]
    assert socket.frames > 0
    # And it says so on the record, with the reason and without the
    # words.
    said = only(caplog, "reply_fallback")
    assert (said.reason, said.audio, said.agent) == ("reply_failed", True, "poet")
    assert "I could not answer" not in caplog.text
    # The reply still failed, and still says so once.
    assert reply_failure(caplog).getMessage().endswith("reply failed: ConnectionRefusedError")


async def test_a_phrase_whose_audio_was_lost_is_still_shown(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The degradation the display half exists for. A voice that would
    not speak the phrase at build time costs the audio and nothing else:
    the sentence still renders, the turn still closes with its `tts
    stop`, and the record says the difference."""
    config = with_fallback(enabled=True, phrase="I could not answer that one.")
    session = await failing_session(config, tts=BrokenTts())

    with caplog.at_level("INFO"):
        await drive_reply(session, UTTERANCE)

    socket = cast(OrderedSocket, session.websocket)
    assert socket.announced() == ["I could not answer that one."]
    assert socket.frames == 0
    assert only(caplog, "reply_fallback").audio is False
    assert socket.closing_stop()


async def test_a_switched_off_fallback_leaves_the_turn_as_silent_as_before(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The off switch, held to the whole of what it means: not a quieter
    turn but the same turn, message for message, that a deployment had
    before the phrase existed.

    Compared against a world with no phrases at all rather than against
    a list written down here, because a list is a claim about today's
    silence and this is the silence itself: the same failure, on the
    same configuration, through a world nothing was synthesized for.
    """
    off = await failing_session(with_fallback(enabled=False))
    before = session_for(
        with_fallback(enabled=False),
        POET_MAC,
        stages={"llm": cast(Any, Unreachable("llm", ConnectionRefusedError("no route")))},
    )
    before.websocket = cast(Any, OrderedSocket())

    with caplog.at_level("INFO"):
        await drive_reply(off, UTTERANCE)
        await drive_reply(before, UTTERANCE)

    silent = cast(OrderedSocket, off.websocket)
    assert wire(silent) == wire(cast(OrderedSocket, before.websocket))
    assert silent.announced() == []
    assert silent.frames == 0
    assert events(caplog, "reply_fallback") == []


async def test_a_vanished_device_is_told_nothing_even_with_a_phrase_cached(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The constraint #384 states in its own words: `DeviceGone` cannot
    speak, because there is nobody on the other end to speak to, and the
    configuration does not get a say in it."""
    session = session_for(
        with_fallback(enabled=True),
        POET_MAC,
        websocket=cast(Any, QuietSocket()),
        fallbacks=await cached(with_fallback(enabled=True)),
    )

    async def speak(synthesis: Any, resampler: Any, into: list[str]) -> None:
        synthesis.cancel()
        await synthesis.wait_cancelled()
        raise DeviceGone("the device disconnected")

    session.runtime._speak = speak  # type: ignore[method-assign]
    with caplog.at_level("INFO"):
        await drive_reply(session, UTTERANCE)

    assert events(caplog, "reply_fallback") == []
    assert "reply failed" not in caplog.text


async def test_a_barge_in_hears_nothing_even_with_a_phrase_cached(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The other constraint, and the one with teeth: a cancellation
    means the user is talking, and a notice played into that would be
    the assistant talking over them."""
    config = with_fallback(enabled=True)
    session = session_for(
        config,
        POET_MAC,
        {"poet": cast(Any, StallingLlm([30.0]))},
        websocket=cast(Any, OrderedSocket()),
        fallbacks=await cached(config),
    )

    with caplog.at_level("INFO"):
        start_reply(session, UTTERANCE)
        await asyncio.sleep(0.05)
        await session.runtime.cancel_reply()

    assert events(caplog, "reply_fallback") == []
    assert cast(OrderedSocket, session.websocket).announced() == []


class CountingTts(TtsProvider):
    """A voice with every synthesis written down, which is what makes
    "cached, and not spoken now" a countable claim."""

    def __init__(self) -> None:
        self._inner = MockTts(sample_rate=24000, ms_per_char=1.0, min_ms=60.0)
        self.sample_rate = self._inner.sample_rate
        self.calls = 0

    def synthesize(self, text: str) -> Any:
        self.calls += 1
        return self._inner.synthesize(text)


async def test_the_phrase_costs_no_synthesis_when_the_turn_fails() -> None:
    """Why the clip is cached at all. Synthesis at failure time would
    add latency to a turn that has already gone wrong, and would ask the
    TTS provider for a favour at the moment it may itself be what
    failed."""
    voice = CountingTts()
    config = with_fallback(enabled=True)
    session = session_for(
        config,
        POET_MAC,
        stages={"llm": cast(Any, Unreachable("llm", ConnectionRefusedError("no route")))},
        fallbacks=await cached(config, voice),
    )
    session.websocket = cast(Any, OrderedSocket())
    built = voice.calls
    assert built > 0, "the phrase was supposed to be synthesized when the world was built"

    await drive_reply(session, UTTERANCE)

    assert voice.calls == built
    assert cast(OrderedSocket, session.websocket).announced()


async def test_a_fallback_whose_send_explodes_still_closes_the_turn(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The risk the arm is written against. Whatever goes wrong while
    the notice is being said, the device still gets the `tts stop` that
    re-arms its listening in auto mode, the failure is named by class
    alone, and the reply is still reported exactly once."""
    config = with_fallback(enabled=True)
    session = session_for(
        config,
        POET_MAC,
        stages={"llm": cast(Any, Unreachable("llm", ConnectionRefusedError("no route")))},
        # A clip at a rate nothing can resample from, which is the bug
        # class this arm exists for: not a disconnect, not a provider,
        # a fault in this process while the notice is going out.
        fallbacks={"poet": FallbackClip(phrase="Sorry.", clip=b"\x00\x00" * 160, sample_rate=0)},
    )
    session.websocket = cast(Any, OrderedSocket())

    with caplog.at_level("INFO"):
        await drive_reply(session, UTTERANCE)

    socket = cast(OrderedSocket, session.websocket)
    assert socket.closing_stop()
    reply_failure(caplog)
    broken = [one for one in caplog.records if "fallback playback failed" in one.getMessage()]
    assert len(broken) == 1
    assert broken[0].getMessage().endswith("ArgumentError")
    assert broken[0].exc_info is None


async def test_a_cancellation_mid_phrase_still_attempts_the_closing_stop_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A barge-in that lands while the notice is already going out. The
    cancellation is not swallowed, so it ends the notice at once; the
    reply's own `finally` runs under it, so the closing `tts stop` is
    still attempted, and exactly once."""
    config = with_fallback(enabled=True)
    session = session_for(
        config,
        POET_MAC,
        stages={"llm": cast(Any, Unreachable("llm", ConnectionRefusedError("no route")))},
        fallbacks=await cached(config),
    )
    socket = CancellingSocket()
    session.websocket = cast(Any, socket)

    with caplog.at_level("INFO"):
        with contextlib.suppress(asyncio.CancelledError):
            await drive_reply(session, UTTERANCE)

    # The notice was announced and then cut: the cancellation reached
    # the send rather than being turned into a swallowed failure.
    assert socket.announced() == ["I ran into a problem and could not answer. The server "
                                 "log has the details."]
    assert "fallback playback failed" not in caplog.text
    assert socket.stops == 1
