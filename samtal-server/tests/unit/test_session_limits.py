"""The two bounds on how long one session lives, and the polite close.

`max_session_s` caps a session's total life. `idle_timeout_s` is the
shorter one users actually meet: a realtime device streams its mic
continuously and nothing in the firmware ever closes the channel, so
walking away used to leave a mic running until the hour was up (#20).
Both end the same way, and the firmware reads the close as the end of a
conversation and reconnects on the next wake word, so both are invisible
in normal use.

request_shutdown is the shared way to end a session politely, used by
the cap, the idle timeout, and the shutdown drain alike: a reply already
speaking finishes its sentence first, because somebody is listening
to it.
"""

import asyncio
import json
import time
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import samtal_server.session as session_module
from samtal_server.app import create_app
from samtal_server.audio.opus import OpusEncoder
from samtal_server.providers import build_agent_providers
from samtal_server.session import GOING_AWAY, NORMAL_CLOSURE, Session
from tests.unit.test_session import (
    collect_reply,
    config_with_agent,
    connect,
    endpoint_silence,
    say_something,
    send_pcm,
    sentences,
    shake_hands,
    speech_pcm,
)


def capped_config(seconds: float):
    config = config_with_agent()
    config.server.limits.max_session_s = seconds
    return config


# Far enough above any idle timeout used here that it never fires
# first, near enough that a broken idle timeout ends the test in
# seconds. wait_for_close blocks until something closes the socket, so
# without a second bound a regression would hang the lane rather than
# fail it, and the close reason is what tells the two apart.
BACKSTOP_S = 10.0


def idle_config(seconds: float, **kwargs: Any):
    """A config whose idle timeout is the bound under test."""
    config = config_with_agent(**kwargs)
    config.server.limits.idle_timeout_s = seconds
    config.server.limits.max_session_s = BACKSTOP_S
    return config


def listen_realtime(websocket) -> None:
    """What a realtime device sends once and never again. It is what
    makes the idle timeout apply at all."""
    websocket.send_text(json.dumps({"type": "listen", "state": "start", "mode": "realtime"}))


def wait_for_close(websocket) -> WebSocketDisconnect:
    """Read past whatever the server has to say (the MCP handshake a
    device that advertised tools gets) until the socket closes."""
    with pytest.raises(WebSocketDisconnect) as excinfo:
        while True:
            websocket.receive_text()
    return excinfo.value


def test_an_idle_session_is_closed_when_it_runs_out_of_time() -> None:
    with TestClient(create_app(capped_config(0.3))) as client:
        with connect(client) as websocket:
            shake_hands(websocket)
            # Nothing else is sent: sitting still is what times out.
            closed = wait_for_close(websocket)
    assert closed.code == NORMAL_CLOSURE
    assert closed.reason == "session time limit reached"


def test_the_cap_also_ends_a_session_that_has_been_talking() -> None:
    with TestClient(create_app(capped_config(1.5))) as client:
        with connect(client) as websocket:
            shake_hands(websocket)
            texts, _ = say_something(websocket)
            # The reply arrived in full before the cap took the session.
            assert sentences(texts) == ["You said hello."]
            assert wait_for_close(websocket).code == NORMAL_CLOSURE


def test_a_generous_cap_leaves_an_ordinary_conversation_alone() -> None:
    with TestClient(create_app(capped_config(3600))) as client:
        with connect(client) as websocket:
            shake_hands(websocket)
            texts, _ = say_something(websocket)
            assert sentences(texts) == ["You said hello."]


def test_the_session_is_logged_as_having_hit_the_limit(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("INFO"):
        with TestClient(create_app(capped_config(0.3))) as client:
            with connect(client) as websocket:
                shake_hands(websocket)
                wait_for_close(websocket)

    (limited,) = [r for r in caplog.records if getattr(r, "event", None) == "session_limit"]
    assert limited.duration_s >= 0.3


def test_a_realtime_session_that_stops_talking_is_hung_up_on() -> None:
    # #20: the device asks to listen once and streams its mic for the
    # rest of the connection, and nothing in the firmware ever closes
    # that channel. Walking away is exactly this: the listen start, and
    # then nothing.
    with TestClient(create_app(idle_config(0.3))) as client:
        with connect(client) as websocket:
            shake_hands(websocket)
            listen_realtime(websocket)
            closed = wait_for_close(websocket)
    assert closed.code == NORMAL_CLOSURE
    assert closed.reason == "idle timeout"


def test_the_idle_timeout_leaves_a_session_that_never_went_realtime_alone() -> None:
    # An auto or manual device stops listening after each reply and
    # re-arms per turn, so it is not streaming a room to anybody and the
    # timeout deliberately does not apply. Its bound is max_session_s, as
    # before. The sleep is several times the timeout: if this applied,
    # the socket would be long gone before the turn is attempted.
    with TestClient(create_app(idle_config(0.2))) as client:
        with connect(client) as websocket:
            shake_hands(websocket)
            time.sleep(1.0)
            texts, _ = say_something(websocket)
            assert sentences(texts) == ["You said hello."]


def test_talking_resets_the_idle_clock() -> None:
    # The timeout counts from the end of the last utterance, not from
    # the start of the session, so a conversation that keeps going is
    # never interrupted by it. Both turns here land well inside the
    # window, and the second only happens because the first moved it.
    config = idle_config(1.5, asr_text="{ms}")
    with TestClient(create_app(config)) as client:
        with connect(client) as websocket:
            shake_hands(websocket)
            listen_realtime(websocket)
            encoder = OpusEncoder()
            send_pcm(websocket, speech_pcm(240), encoder)
            endpoint_silence(websocket, encoder)
            first, _ = collect_reply(websocket)
            send_pcm(websocket, speech_pcm(240), encoder)
            endpoint_silence(websocket, encoder)
            second, _ = collect_reply(websocket)
    assert sentences(first) and sentences(second)


# A reply the server takes longer to speak than the idle timeout, so
# the timer comes due in the middle of it. Mock TTS speaks at about
# 0.04 s per character and the frames are paced in real time, so this
# is roughly 1.8 s against the 1 s timeout below: comfortably over it,
# and two turns of it still comfortably under BACKSTOP_S.
LONG_REPLY = "One. Two. Three. Four. Five. Six. Seven. Eight."


def test_a_reply_still_speaking_is_not_an_idle_session() -> None:
    # A reply has not ended while it is still streaming, so it counts as
    # activity in its own right.
    #
    # The failure this guards against is not a reply cut off mid-word:
    # request_shutdown politely waits for one to finish speaking. It is
    # what happens next. A timer that came due during the reply has
    # already decided to hang up, so the user is given no window at all
    # to answer what they just heard: the socket closes the instant the
    # reply ends, and the second turn below never happens.
    config = idle_config(1.0, llm_reply=LONG_REPLY)
    with TestClient(create_app(config)) as client:
        with connect(client) as websocket:
            shake_hands(websocket)
            listen_realtime(websocket)
            encoder = OpusEncoder()
            send_pcm(websocket, speech_pcm(240), encoder)
            endpoint_silence(websocket, encoder)
            first, _ = collect_reply(websocket)
            # Answering the reply, inside the window it should have left.
            send_pcm(websocket, speech_pcm(240), encoder)
            endpoint_silence(websocket, encoder)
            second, _ = collect_reply(websocket)
    assert sentences(first) == LONG_REPLY.split()
    assert sentences(second) == LONG_REPLY.split()


def test_going_realtime_late_gets_a_full_window_not_what_was_left() -> None:
    # While a session is not realtime the timeout does not apply, which
    # is implemented by pushing the deadline forward each time round. A
    # session that turns realtime part-way through one of those rounds
    # must not inherit the remainder: it would be hung up on seconds
    # after the user started talking.
    #
    # Both sleeps are load-bearing. The first lands mid-round, so the
    # inherited deadline is nearly up. The second is the gap between
    # asking to listen and actually saying something, which is where the
    # bug lives: speaking straight away would hide it, because an
    # utterance ending pushes the deadline out on its own.
    with TestClient(create_app(idle_config(1.0))) as client:
        with connect(client) as websocket:
            shake_hands(websocket)
            time.sleep(1.8)
            listen_realtime(websocket)
            time.sleep(0.4)
            encoder = OpusEncoder()
            send_pcm(websocket, speech_pcm(240), encoder)
            endpoint_silence(websocket, encoder)
            texts, _ = collect_reply(websocket)
    assert sentences(texts) == ["You said hello."]


def test_the_idle_close_is_logged_as_its_own_event(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # A distinct event from session_limit: an operator reading the logs
    # should be able to tell a conversation that was abandoned from one
    # that ran out of its hour.
    with caplog.at_level("INFO"):
        with TestClient(create_app(idle_config(0.3))) as client:
            with connect(client) as websocket:
                shake_hands(websocket)
                listen_realtime(websocket)
                wait_for_close(websocket)

    (idled,) = [r for r in caplog.records if getattr(r, "event", None) == "session_idle"]
    assert idled.idle_s == 0.3
    assert idled.duration_s >= 0.3
    assert not [r for r in caplog.records if getattr(r, "event", None) == "session_limit"]


class FakeWebsocket:
    """Just enough websocket to watch a close happen."""

    def __init__(self) -> None:
        self.closed: tuple[int, str] | None = None

    async def close(self, code: int, reason: str) -> None:
        self.closed = (code, reason)


def session_with(reply: asyncio.Task[None] | None = None) -> tuple[Session, FakeWebsocket]:
    config = config_with_agent()
    websocket = FakeWebsocket()
    session = Session(cast(Any, websocket), config, build_agent_providers(config))
    session._reply_task = reply
    return session, websocket


async def test_request_shutdown_closes_going_away_by_default() -> None:
    session, websocket = session_with()
    assert await session.request_shutdown() is True
    assert websocket.closed == (GOING_AWAY, "server shutting down")


async def test_request_shutdown_waits_for_a_reply_to_finish_speaking() -> None:
    spoke = False

    async def reply() -> None:
        nonlocal spoke
        await asyncio.sleep(0.1)
        spoke = True

    session, websocket = session_with(asyncio.create_task(reply()))
    # True: the sentence finished before the socket closed, because
    # somebody was listening to it.
    assert await session.request_shutdown() is True
    assert spoke
    assert websocket.closed == (GOING_AWAY, "server shutting down")


async def test_a_reply_that_outlasts_the_grace_is_abandoned_and_reported() -> None:
    async def forever() -> None:
        await asyncio.sleep(30)

    task = asyncio.create_task(forever())
    session, websocket = session_with(task)
    # False is what lets the drain say a reply was cut mid-sentence
    # rather than report a clean drain over the top of it.
    assert await session.request_shutdown(grace_s=0.05) is False
    assert websocket.closed is not None
    task.cancel()


async def test_the_caller_decides_how_long_a_reply_is_worth_waiting_for() -> None:
    """The drain passes its own budget, so server.drain_s lengthens what
    a reply actually gets. Without this the module constant capped it."""

    async def reply() -> None:
        await asyncio.sleep(0.2)

    session, _ = session_with(asyncio.create_task(reply()))
    assert await session.request_shutdown(grace_s=5.0) is True


async def test_the_default_grace_applies_when_no_caller_names_one() -> None:
    """The duration cap has no budget of its own, so it gets the module
    default rather than an unbounded wait."""
    assert session_module.SHUTDOWN_REPLY_GRACE_S == 10.0
    session, websocket = session_with()
    await session.request_shutdown(NORMAL_CLOSURE, "session time limit reached")
    assert websocket.closed == (NORMAL_CLOSURE, "session time limit reached")


async def test_a_failed_reply_counts_as_a_finished_one() -> None:
    async def fails() -> None:
        raise RuntimeError("the provider went away")

    session, websocket = session_with(asyncio.create_task(fails()))
    # It is not speaking any more, which is what the caller asked about.
    assert await session.request_shutdown() is True
    assert websocket.closed is not None
