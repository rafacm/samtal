"""Why a session ended, as `session_closed` says it.

The reason used to be inferable only from whichever line came before the
close: a `session_limit` above it meant the cap, a `session_idle` meant
the watchdog, and nothing above it meant anything at all. The field makes
it a token from a closed set, decided at the site that decides, which is
what the conversation store's `sessions.close_reason` is copied from.

Two properties beyond the five sites are what make the token worth
storing. It is latched by the first cause to fire, so competing
terminations do not rewrite what started the close; and the close path
reaches the event whatever happened on the way, so a cleanup step that
raises is reported rather than being able to swallow the whole record of
the session ending.
"""

import asyncio
import json
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from samtal_server.app import create_app
from samtal_server.config import Config
from samtal_server.conversations.schema import CLOSE_REASONS as STORED_REASONS
from samtal_server.device.session import CLOSE_REASONS, DeviceSession
from samtal_server.providers import build_agent_providers
from samtal_server.registry import SessionRegistry
from samtal_server.runtime.pipeline import bespoke_runtime_factory
from samtal_server.tools.mcp import McpServers
from tests.unit.test_session import (
    DEVICE_HELLO,
    DEVICE_MAC,
    DEVICE_UUID,
    config_with_agent,
    connect,
    say_something,
    shake_hands,
)
from tests.unit.test_session_limits import (
    capped_config,
    idle_config,
    listen_realtime,
    wait_for_close,
)


def closed(caplog: pytest.LogCaptureFixture) -> Any:
    (record,) = [r for r in caplog.records if getattr(r, "event", None) == "session_closed"]
    return record


class LoopingSocket:
    """Enough websocket for `run`: the hello, then a receive that waits
    until something closes the session.

    The tests below that drive `run` directly are the ones about what
    ends a session from the server side, where a test client's own close
    would be a sixth cause racing the one under test.
    """

    def __init__(self) -> None:
        self.headers = {"device-id": DEVICE_MAC, "client-id": DEVICE_UUID}
        self.closed: tuple[int, str] | None = None
        self.inbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.inbox.put_nowait(
            {"type": "websocket.receive", "text": json.dumps(DEVICE_HELLO)}
        )

    async def accept(self) -> None:
        return None

    async def receive(self) -> dict[str, Any]:
        return await self.inbox.get()

    async def send_text(self, text: str) -> None:
        return None

    async def send_bytes(self, data: bytes) -> None:
        return None

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = (code, reason)
        self.inbox.put_nowait({"type": "websocket.disconnect"})


def served(
    config: Config, websocket: LoopingSocket, conversations: Any = None
) -> DeviceSession:
    """A session built the way `ws.py` builds one, so `run` is what the
    test drives rather than a hand-assembled close path. `conversations`
    is the store, which reaches a session twice over: through the factory
    that binds its turn recorder, and as the collaborator the session
    opens and closes."""
    factory = bespoke_runtime_factory(
        config, build_agent_providers(config), McpServers({}), None, {}, conversations
    )
    return DeviceSession(
        cast(Any, websocket), config, factory, conversations=conversations
    )


async def open_session(
    config: Config, conversations: Any = None
) -> tuple[DeviceSession, LoopingSocket, Any]:
    """A live session with its hello exchanged, its `run` in flight."""
    websocket = LoopingSocket()
    session = served(config, websocket, conversations)
    task = asyncio.create_task(session.run())
    for _ in range(200):
        await asyncio.sleep(0.01)
        if session.runtime is not None and session._opened_at is not None:
            if websocket.inbox.empty():
                return session, websocket, task
    raise AssertionError("the session never opened")


def test_the_tokens_are_the_ones_the_store_records() -> None:
    # Two copies of one closed set, one at the sites that decide and one
    # in the column comment, so a sixth token added to either is a
    # failure here rather than a row nobody can interpret.
    assert CLOSE_REASONS == STORED_REASONS


def test_a_device_that_hangs_up_closes_as_client(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("INFO"):
        with TestClient(create_app(config_with_agent())) as client:
            with connect(client) as websocket:
                shake_hands(websocket)
                say_something(websocket)

    assert closed(caplog).reason == "client"


def test_the_duration_cap_closes_as_limit(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("INFO"):
        with TestClient(create_app(capped_config(0.3))) as client:
            with connect(client) as websocket:
                shake_hands(websocket)
                wait_for_close(websocket)

    assert closed(caplog).reason == "limit"


def test_the_idle_watchdog_closes_as_idle(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("INFO"):
        with TestClient(create_app(idle_config(0.3))) as client:
            with connect(client) as websocket:
                shake_hands(websocket)
                listen_realtime(websocket)
                wait_for_close(websocket)

    assert closed(caplog).reason == "idle"


async def test_the_shutdown_drain_closes_as_drain(
    caplog: pytest.LogCaptureFixture,
) -> None:
    session, _, task = await open_session(config_with_agent())
    registry = SessionRegistry(max_sessions=8)
    assert registry.try_add(session)

    with caplog.at_level("INFO"):
        await registry.drain(timeout_s=5)
        await asyncio.wait_for(task, timeout=5)

    assert closed(caplog).reason == "drain"


async def test_a_failure_on_the_way_out_closes_as_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Anything leaving through the close path that is not a
    conversation ending is an error, and the event still lands: the
    exception is re-raised past it, not instead of it."""
    session, websocket, task = await open_session(config_with_agent())

    async def refuse() -> dict[str, Any]:
        raise RuntimeError("the socket went away in a way nobody predicted")

    websocket.receive = refuse  # type: ignore[method-assign]
    websocket.inbox.put_nowait({"type": "websocket.receive", "bytes": b""})

    with caplog.at_level("INFO"):
        with pytest.raises(RuntimeError):
            await asyncio.wait_for(task, timeout=5)

    assert closed(caplog).reason == "error"


async def test_a_cleanup_step_that_raises_neither_hides_nor_survives_the_close(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The runtime's close is one of three steps ahead of the event. Each
    is guarded on its own, so one that raises is reported by class and
    the event, the store's close and the capture's close all still
    happen. With nothing else latched, a session that would not shut down
    cleanly did not end in a conversation, so it reads `error`."""
    session, websocket, task = await open_session(config_with_agent())
    assert session.runtime is not None

    async def refuse() -> None:
        raise ValueError("the runtime would not let go")

    session.runtime.close = refuse  # type: ignore[method-assign]

    with caplog.at_level("INFO"):
        await websocket.close(1000, "goodbye")
        await asyncio.wait_for(task, timeout=5)

    assert closed(caplog).reason == "error"
    # By class, and never the message: an exception on the way out of a
    # session is one of the places a far side's bytes could reach the
    # retained surface.
    assert "ValueError" in caplog.text
    assert "would not let go" not in caplog.text


async def test_the_first_cause_wins(caplog: pytest.LogCaptureFixture) -> None:
    """Competing terminations are ordinary: an idle timer comes due while
    a drain is already closing the same session. What is recorded is what
    initiated the close, so the reason is deterministic rather than
    whichever site happened to run last."""
    session, websocket, task = await open_session(idle_config(0.2))
    registry = SessionRegistry(max_sessions=8)
    assert registry.try_add(session)

    with caplog.at_level("INFO"):
        drain = asyncio.create_task(registry.drain(timeout_s=5))
        # Long enough for the watchdog to come due behind the drain.
        await asyncio.sleep(0.4)
        await drain
        await asyncio.wait_for(task, timeout=5)

    assert closed(caplog).reason == "drain"


async def test_a_latched_token_is_not_rewritten_by_a_later_one() -> None:
    """The latch itself, at the one place it is written, since a race is
    only ever probabilistically reproducible from outside."""
    websocket = LoopingSocket()
    session = served(config_with_agent(), websocket)
    session._latch_close("idle")
    session._latch_close("drain")
    assert session._closed_reason() == "idle"


async def test_nothing_latched_reads_as_client() -> None:
    websocket = LoopingSocket()
    session = served(config_with_agent(), websocket)
    assert session._closed_reason() == "client"
