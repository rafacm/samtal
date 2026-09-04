"""The first-token watchdog on the LLM round.

A provider that stalls before its first token used to freeze the
pipeline for as long as it cared to take: nothing bounded the gap
between sending the request and the first byte of the answer, so a 17 s
stall held the session in replying, deaf to a user who politely waits,
until a barge-in rescued it (#68). The watchdog bounds only that gap:
one timeout cancels the request and retries the round once, a second
gives the round up, and a generation that is already streaming is never
touched, however long it runs.

What a given-up round costs the user is the last case in the file. It
was a silent turn, which is what the middle cases still drive, on a
world with no failure phrase cached; a served world has one, and the
turn says so out loud instead (#384).

These tests drive the reply directly against an LLM whose first-token
delay is written down per call, with the timeout shrunk to test scale.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from typing import Any, cast

import pytest

from tests.support.configs import POET_MAC, TIMEOUT_S, watchdog_config
from tests.support.events import events, only
from tests.support.providers import STALL_S, StallingLlm, built_world
from tests.support.sessions import (
    listening_in_realtime,
    run_reply,
    session_for,
    start_reply,
    wait_for_reply,
)
from tests.support.sockets import OrderedSocket, RecordingSocket
from vinga_server.config.models import FallbackConfig
from vinga_server.filler import build_agent_fillers
from vinga_server.providers import (
    LlmEvent,
    LlmProvider,
    StreamStarted,
    TextDelta,
    ToolCall,
    ToolChoice,
    ToolDef,
    Turn,
)


class DribblingLlm(LlmProvider):
    """A model whose first token is instant and whose later tokens each
    take several watchdog windows: the healthy long generation the
    timeout must not kill."""

    def __init__(self, words: Sequence[str], gap_s: float) -> None:
        self._words = list(words)
        self._gap_s = gap_s
        self.calls = 0

    async def stream(
        self,
        system: str,
        turns: Sequence[Turn],
        tools: Sequence[ToolDef] = (),
        tool_choice: ToolChoice = "auto",
    ) -> AsyncIterator[LlmEvent]:
        self.calls += 1
        yield TextDelta(self._words[0])
        for word in self._words[1:]:
            await asyncio.sleep(self._gap_s)
            yield TextDelta(" " + word)


async def test_a_stalled_first_token_is_retried_and_the_retry_answers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The gap A shape: the first request stalls, the retry answers with
    a healthy first token, and the user hears the reply late rather
    than never."""
    llm = StallingLlm(delays=[STALL_S, 0.0])
    session = session_for(watchdog_config(), POET_MAC, {"poet": cast(Any, llm)})
    with caplog.at_level("INFO"):
        spoken = await run_reply(session, "are you there")

    assert spoken == ["Recovered now."]
    assert llm.calls == 2
    retried = only(caplog, "llm_retry")
    assert retried.agent == "poet"
    assert retried.round == 1
    assert retried.stage == "llm"
    assert retried.provider == "mock"
    assert retried.duration_ms >= TIMEOUT_S * 1000
    assert events(caplog, "provider_failed") == []
    # One llm_round for the whole retried round, its duration carrying
    # the wasted first attempt.
    assert only(caplog, "llm_round").round == 1


async def test_a_second_stall_gives_the_round_up_and_the_session_keeps_listening(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Both attempts stall: the round is given up as the provider's
    failure, the reply still closes with its tts stop (which is what
    re-arms an auto-mode device), and a realtime session is still
    listening. Not a wedged session, which is the claim here; whether
    the turn is silent is the world's own, and the last case in this
    file drives the world that speaks."""
    llm = StallingLlm(delays=[STALL_S])
    session = session_for(watchdog_config(), POET_MAC, {"poet": cast(Any, llm)})
    socket = RecordingSocket()
    session.websocket = cast(Any, socket)
    listening_in_realtime(session)

    with caplog.at_level("INFO"):
        start_reply(session, b"\x00\x00" * 320)
        await wait_for_reply(session)

    assert llm.calls == 2
    assert only(caplog, "llm_retry").round == 1
    failed = only(caplog, "provider_failed")
    assert failed.stage == "llm"
    assert failed.error == "FirstTokenTimeout"
    assert failed.agent == "poet"
    assert "timed out" in failed.getMessage()
    # The reply ended cleanly: not replying, still listening, and the
    # closing tts stop went out for the device that waits on it.
    assert not session.runtime.replying()
    assert session.listening is True
    last = json.loads(socket.texts[-1])
    assert (last["type"], last["state"]) == ("tts", "stop")


async def test_a_slow_generation_that_is_streaming_is_not_killed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The timeout covers only time to first token. A reply whose every
    later token takes several watchdog windows is healthy (the observed
    17.7 s story round had a 635 ms first token) and runs to the end."""
    llm = DribblingLlm(["A", "slow", "but", "healthy", "story."], gap_s=TIMEOUT_S * 4)
    session = session_for(watchdog_config(), POET_MAC, {"poet": cast(Any, llm)})
    with caplog.at_level("INFO"):
        spoken = await run_reply(session, "tell me a story")

    assert spoken == ["A slow but healthy story."]
    assert llm.calls == 1
    assert events(caplog, "llm_retry") == []
    assert events(caplog, "provider_failed") == []


class ToolOnlyLlm(LlmProvider):
    """A first round shaped like a handover: the first chunk off the
    wire is announced promptly, then nothing but a buffered tool call
    until several watchdog windows later. The second round speaks."""

    def __init__(self, gap_s: float) -> None:
        self._gap_s = gap_s
        self.calls = 0

    async def stream(
        self,
        system: str,
        turns: Sequence[Turn],
        tools: Sequence[ToolDef] = (),
        tool_choice: ToolChoice = "auto",
    ) -> AsyncIterator[LlmEvent]:
        self.calls += 1
        yield StreamStarted()
        if self.calls == 1:
            await asyncio.sleep(self._gap_s)
            yield ToolCall(id="c-1", name="ghost_tool", arguments={})
        else:
            yield TextDelta("Done anyway.")


async def test_a_tool_only_round_that_announced_the_wire_is_not_killed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Both adapters buffer tool-call fragments until the stream ends,
    so a round streaming only a tool call yields no content while
    healthily delivering. Its StreamStarted is what the watchdog
    accepts as the first token; without it the round would be cancelled
    at the timeout and the tool dropped on a slow second attempt."""
    llm = ToolOnlyLlm(gap_s=TIMEOUT_S * 4)
    session = session_for(watchdog_config(), POET_MAC, {"poet": cast(Any, llm)})
    with caplog.at_level("INFO"):
        spoken = await run_reply(session, "do the thing")

    assert spoken == ["Done anyway."]
    # Two rounds of one reply, not a retry of the first.
    assert llm.calls == 2
    assert events(caplog, "llm_retry") == []
    assert events(caplog, "provider_failed") == []


async def test_a_cancel_during_the_watchdog_window_still_lands(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Barge-in is the rescue path the field incident actually used, and
    it must keep working while the watchdog waits: cancelling the reply
    task ends the wait at once, with no retry and no failure event."""
    llm = StallingLlm(delays=[STALL_S])
    session = session_for(watchdog_config(timeout_s=30.0), POET_MAC, {"poet": cast(Any, llm)})
    socket = RecordingSocket()
    session.websocket = cast(Any, socket)

    with caplog.at_level("INFO"):
        start_reply(session, b"\x00\x00" * 320)
        await asyncio.sleep(0.05)
        await session.runtime.cancel_reply()

    assert llm.calls == 1
    assert not session.runtime.replying()
    assert events(caplog, "llm_retry") == []
    assert events(caplog, "provider_failed") == []


async def test_a_given_up_round_is_heard_rather_than_only_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The issue's own trigger, end to end (#384).

    A cold local model that never answers is exactly the shape above:
    both attempts stall, the round is given up as `FirstTokenTimeout`,
    and until now the user's whole experience of it was a turn where
    nothing happened. Diagnosing one took an hour and a container log.
    The same turn now says so, from a phrase cached in the agent's own
    voice, and the closing `tts stop` still goes out behind it.
    """
    config = watchdog_config()
    fallbacks = (await build_agent_fillers(config, built_world(config).agents)).fallbacks
    session = session_for(
        config, POET_MAC, {"poet": cast(Any, StallingLlm([STALL_S]))}, fallbacks=fallbacks
    )
    socket = OrderedSocket()
    session.websocket = cast(Any, socket)
    listening_in_realtime(session)

    with caplog.at_level("INFO"):
        start_reply(session, b"\x00\x00" * 320)
        await wait_for_reply(session)

    assert only(caplog, "provider_failed").error == "FirstTokenTimeout"
    said = only(caplog, "reply_fallback")
    assert (said.reason, said.audio) == ("reply_failed", True)
    assert socket.announced() == [FallbackConfig().phrase]
    assert socket.frames > 0
    # And the turn still ends the way the device needs it to, which is
    # what makes this a spoken failure rather than a broken one.
    assert socket.closing_stop()
    assert not session.runtime.replying()
    assert session.listening is True
