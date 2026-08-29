"""The live hub at the device edge: when it attaches, and when it lets
go.

A session publishes no event accessor, so the hub cannot find its own
way in: the composition root hands it to `ws.py`, which hands it to
`DeviceSession`, which attaches it to the events object it has just
built. The attach point is the subject of this file, because it is the
whole reason the wiring exists at all. Every refusal a session can
answer with happens before the hello, and the conversation store's tap
attaches after it: a hub attached where the store's tap attaches would
miss exactly the lines an operator opens a tail to watch.

The other half is the letting go. A tap left on a session that has
ended is a consumer being written to for a conversation that is over,
so it comes off in an outer `finally` over the whole of `run`, and on
the one branch where a session that was built never runs at all: the
capacity rejection in `ws.py`, between construction and `run`.
"""

import asyncio
from typing import Any, cast

import pytest
from starlette.websockets import WebSocketDisconnect

from tests.support.apps import entered_app
from tests.support.configs import DEVICE_MAC, DEVICE_UUID, config_with_agent, world
from tests.support.providers import built_world
from tests.support.sessions import attached_taps
from tests.support.sockets import LoopingSocket
from tests.support.wire import device_headers, handshake
from vinga_server.config import Config
from vinga_server.device.session import DeviceSession
from vinga_server.events.catalog import (
    PromptAssembled,
    RejectedAtCapacity,
    RejectedBadDeviceId,
    SessionOpen,
    declaration_of,
)
from vinga_server.events.live import Dropped, LiveEvents, Streamed
from vinga_server.registry import SessionRegistry
from vinga_server.runtime.pipeline import bespoke_runtime_factory
from vinga_server.tools.mcp import McpServers


class BadIdSocket(LoopingSocket):
    """A device whose Device-Id is not a MAC, which is the first
    rejection a session can answer with and the earliest event it can
    emit."""

    def __init__(self) -> None:
        super().__init__()
        self.headers = {"device-id": "not-a-mac", "client-id": DEVICE_UUID}


def session_watched_by(
    hub: LiveEvents, websocket: LoopingSocket, config: Config | None = None
) -> DeviceSession:
    """A session built the way `ws.py` builds one, with the hub it hands
    over."""
    settings = config if config is not None else config_with_agent()
    generations = world(settings, providers=built_world(settings))
    factory = bespoke_runtime_factory(generations, McpServers({}), None, None)
    return DeviceSession(
        cast(Any, websocket), generations, factory, live=hub
    )


async def drained(subscription: Any) -> list[str]:
    """The event names one reader was handed, in order."""
    names: list[str] = []
    while True:
        item = await subscription.next(timeout=0)
        if item is None:
            return names
        assert not isinstance(item, Dropped), "the reader fell behind in a test"
        assert isinstance(item, Streamed)
        names.append(item.fields["event"])


async def test_a_rejection_before_the_hello_reaches_the_stream() -> None:
    """The Device-Id refusal is emitted before the accept has led
    anywhere: no hello, no manifest, no store tap. A hub attached later
    would never see it, which is why it attaches at construction."""
    hub = LiveEvents()
    subscription = hub.subscribe()
    session = session_watched_by(hub, BadIdSocket())

    await session.run()

    assert declaration_of(RejectedBadDeviceId).name in await drained(subscription)


async def test_the_session_lets_go_of_the_hub_when_the_connection_ends() -> None:
    hub = LiveEvents()
    session = session_watched_by(hub, BadIdSocket())
    assert hub in attached_taps(session)

    await session.run()

    assert hub not in attached_taps(session)


async def test_detaching_stops_the_stream_from_hearing_this_session() -> None:
    """What `ws.py` calls on the one branch that never runs, asserted as
    what it does: the tap comes off, and it is the same call the
    `finally` makes."""
    hub = LiveEvents()
    session = session_watched_by(hub, LoopingSocket())
    assert hub in attached_taps(session)

    session.detach_live()

    assert hub not in attached_taps(session)
    # And twice is not an error, which is what lets the `finally` run
    # after the branch has already detached.
    session.detach_live()
    assert hub not in attached_taps(session)


async def test_an_event_emitted_while_the_runtime_is_built_reaches_the_stream() -> None:
    """The runtime's constructor activates the first agent, which
    assembles a prompt and says so, and all of that happens before the
    hello. The ordering is the assertion: `prompt_assembled` arrives
    ahead of `session_open`, and the store's tap attaches between the
    two."""
    hub = LiveEvents()
    subscription = hub.subscribe()
    websocket = LoopingSocket()
    session = session_watched_by(hub, websocket)
    running = asyncio.create_task(session.run())
    try:
        for _ in range(200):
            await asyncio.sleep(0.01)
            if session.runtime is not None and websocket.inbox.empty():
                break
        else:  # pragma: no cover - the session never opened
            raise AssertionError("the session never opened")
        seen = await drained(subscription)
    finally:
        await websocket.close()
        await asyncio.wait_for(running, timeout=5)

    assembled = declaration_of(PromptAssembled).name
    opened = declaration_of(SessionOpen).name
    assert assembled in seen
    assert seen.index(assembled) < seen.index(opened)


async def test_a_session_rejected_at_capacity_gives_the_hub_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The rejection is emitted by `ws.py` on a session it built and
    will never run, so the tap it attached at construction has to come
    off on that branch. Both halves are asserted: the operator sees the
    rejection, and nothing is left holding the session afterwards."""
    detached: list[DeviceSession] = []
    real = DeviceSession.detach_live

    def spy(self: DeviceSession) -> None:
        detached.append(self)
        real(self)

    monkeypatch.setattr(DeviceSession, "detach_live", spy)
    monkeypatch.setattr(SessionRegistry, "try_add", lambda self, session: False)

    with entered_app(config_with_agent()) as (app, client):
        hub = app.state.composition.live
        subscription = hub.subscribe()
        token = app.state.composition.device_auth.issue(DEVICE_UUID, DEVICE_MAC.lower())
        # The rejection closes the socket, so the connect itself is what
        # raises, exactly as it does for a refused token.
        with pytest.raises(WebSocketDisconnect):
            with handshake(client, device_headers(token)):
                pass
        seen = await drained(subscription)

    assert declaration_of(RejectedAtCapacity).name in seen
    assert detached, "the capacity branch never gave the hub back"
    assert all(
        hub not in attached_taps(session) for session in detached
    ), "a session rejected at capacity is still feeding the stream"


