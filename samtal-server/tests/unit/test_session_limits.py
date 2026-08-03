"""The cap on how long one session lives, and the polite close.

There is no separate idle timeout: a session's total life bounds an idle
one too, which is the whole reason the cap exists (a device that stopped
talking hours ago still holds a slot). The firmware reads the close as
the end of a conversation and reconnects on the next wake word, so a cap
set sensibly is invisible in normal use.

request_shutdown is the shared way to end a session politely, used both
by the cap here and by the shutdown drain: a reply already speaking
finishes its sentence first, because somebody is listening to it.
"""

import asyncio
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import samtal_server.session as session_module
from samtal_server.app import create_app
from samtal_server.providers import build_agent_providers
from samtal_server.session import GOING_AWAY, NORMAL_CLOSURE, Session
from tests.unit.test_session import (
    config_with_agent,
    connect,
    say_something,
    sentences,
    shake_hands,
)


def capped_config(seconds: float):
    config = config_with_agent()
    config.server.limits.max_session_s = seconds
    return config


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
    await session.request_shutdown()
    assert websocket.closed == (GOING_AWAY, "server shutting down")


async def test_request_shutdown_waits_for_a_reply_to_finish_speaking() -> None:
    spoke = False

    async def reply() -> None:
        nonlocal spoke
        await asyncio.sleep(0.1)
        spoke = True

    session, websocket = session_with(asyncio.create_task(reply()))
    await session.request_shutdown()
    # The sentence finished before the socket closed: somebody was
    # listening to it.
    assert spoke
    assert websocket.closed == (GOING_AWAY, "server shutting down")


async def test_a_reply_that_will_not_finish_is_abandoned_not_waited_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(session_module, "SHUTDOWN_REPLY_GRACE_S", 0.05)

    async def forever() -> None:
        await asyncio.sleep(30)

    task = asyncio.create_task(forever())
    session, websocket = session_with(task)
    await session.request_shutdown()
    assert websocket.closed is not None
    task.cancel()


async def test_a_failed_reply_does_not_become_this_methods_exception() -> None:
    async def fails() -> None:
        raise RuntimeError("the provider went away")

    session, websocket = session_with(asyncio.create_task(fails()))
    await session.request_shutdown()
    assert websocket.closed is not None
