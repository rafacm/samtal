"""Which world a connection ends up holding, and when it lets go.

A conversation is built from one generation and speaks through that
generation's engines until it ends, so "which world is this connection
holding" is what decides when a world an apply replaced may release what
it holds (#191). The accounting has two states rather than one, and this
file is the four cases that prove why: a session is admitted before it
has proved anything, and the ones below are turned away without a
conversation ever being built for them.

The edge is driven through `run` rather than through a helper, because
what is under test is the control flow: which statement the binding sits
after, and which paths reach it. The `finally` that gives a slot back is
the websocket endpoint's, so each case does what that endpoint does, in
its order.
"""

import asyncio
from typing import Any, cast

import pytest

from tests.support.configs import DEVICE_MAC, DEVICE_UUID, config_with, world
from tests.support.providers import built_world
from vinga_server.device.session import DeviceSession
from vinga_server.registry import SessionRegistry
from vinga_server.runtime.pipeline import bespoke_runtime_factory
from vinga_server.tools.mcp import McpServers

BOUND = DEVICE_MAC.lower()

UNBOUND = "aa:bb:cc:dd:ee:02"


def served() -> Any:
    """One agent, reachable by one device, with engines built for it."""
    return config_with(
        agents={"assistant": {"prompt": "A"}}, devices={BOUND: ["assistant"]}
    )


class Turned:
    """Just enough websocket for a connection that is refused: the
    handshake headers, the accept, and the close."""

    def __init__(self, device_id: str) -> None:
        self.headers = {"device-id": device_id, "client-id": DEVICE_UUID}
        self.closed: tuple[int, str] | None = None

    async def accept(self) -> None:
        return None

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = (code, reason)


class Vanishing(Turned):
    """A device that opens a socket and disappears before its hello.

    The case round 3 reclassified: the runtime is built before the hello
    is read, so this connection is holding a world although nothing of
    the conversation's own cleanup ever ran.
    """

    async def receive(self) -> dict[str, Any]:
        return {"type": "websocket.disconnect"}

    async def send_text(self, text: str) -> None:
        return None


class Bound:
    """A bindings view whose answer is written down, so a device with no
    agent is driven without a database behind it."""

    def __init__(self, agents: list[str]) -> None:
        self._agents = agents

    async def resolve(self, mac: str) -> Any:
        return type("Resolved", (), {"agents": self._agents, "unloaded": ()})()


class Waiting:
    """A bindings view that answers only when a test lets it, which is
    the window an apply lands in."""

    def __init__(self, agents: list[str]) -> None:
        self._agents = agents
        self.asked = asyncio.Event()
        self.answer = asyncio.Event()

    async def resolve(self, mac: str) -> Any:
        self.asked.set()
        await self.answer.wait()
        return type("Resolved", (), {"agents": self._agents, "unloaded": ()})()


def connection(
    config: Any, socket: Any, registry: SessionRegistry, generations: Any, **over: Any
) -> DeviceSession:
    """A session wired the way the websocket endpoint wires one."""
    return DeviceSession(
        cast(Any, socket),
        generations,
        bespoke_runtime_factory(generations, McpServers({}), None),
        sessions=registry,
        **over,
    )


def serving() -> tuple[Any, SessionRegistry]:
    config = served()
    generations = world(config, providers=built_world(config))
    return generations, SessionRegistry(max_sessions=4, generations=generations)


# The connections that never build a conversation


async def test_a_device_id_that_is_not_a_mac_holds_nothing() -> None:
    """Rejected before the bindings are even asked, so there is no world
    to hold and nothing for its removal to release."""
    generations, registry = serving()
    session = connection(served(), Turned("not-a-mac"), registry, generations)
    registry.try_add(session)

    await session.run()
    registry.remove(session)

    assert session.runtime is None
    assert registry.held() == []


async def test_a_device_bound_to_nothing_holds_nothing() -> None:
    """The second never-bound case: the bindings answered, and answered
    with no agent this device may talk to."""
    generations, registry = serving()
    session = connection(
        served(), Turned(UNBOUND), registry, generations, bindings=Bound([])
    )
    registry.try_add(session)

    await session.run()
    registry.remove(session)

    assert session.runtime is None
    assert registry.held() == []


async def test_a_connection_cancelled_while_the_bindings_resolve_holds_nothing() -> None:
    """The third: the lookup is awaited, so a client that goes away
    during it leaves a session that was admitted and never built
    anything."""
    generations, registry = serving()
    bindings = Waiting(["assistant"])
    session = connection(
        served(), Turned(BOUND), registry, generations, bindings=bindings
    )
    registry.try_add(session)

    running = asyncio.create_task(session.run())
    await asyncio.wait_for(bindings.asked.wait(), timeout=5)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    registry.remove(session)

    assert session.runtime is None
    assert registry.held() == []


# The connections that do


async def test_a_disconnect_before_the_hello_releases_the_world_it_bound() -> None:
    """The case that reads backwards until the control flow is followed.

    The runtime is constructed after the bindings resolve and before the
    hello is read, so a device that vanishes in between has held a world
    for the whole of its connection. Nothing of the conversation's own
    cleanup runs on that path (`run` returns before the guard), and the
    release still happens, because it is the endpoint's `finally` that
    does it.
    """
    generations, registry = serving()
    socket = Vanishing(BOUND)
    session = connection(served(), socket, registry, generations)
    registry.try_add(session)

    await session.run()

    assert session.runtime is not None
    assert registry.held() == [generations.current()]

    registry.remove(session)
    assert registry.held() == []
    # The letting-go that removal started is this registry's to see out.
    await registry.drain(timeout_s=1.0)


async def test_a_world_installed_during_the_lookup_is_the_one_that_is_bound() -> None:
    """The barrier. The bindings lookup is awaited, so an apply can land
    inside it; the world is read after that await and the registry is
    told about the same object in the same step, so what a conversation
    holds is what it was built from and never a world that was current a
    moment earlier.
    """
    config = served()
    generations = world(config, providers=built_world(config))
    registry = SessionRegistry(max_sessions=4, generations=generations)
    booted = generations.current()
    bindings = Waiting(["assistant"])
    session = connection(config, Vanishing(BOUND), registry, generations, bindings=bindings)
    registry.try_add(session)

    running = asyncio.create_task(session.run())
    await asyncio.wait_for(bindings.asked.wait(), timeout=5)
    with generations.applying() as install:
        install(
            type(booted)(
                booted.config, booted.secrets, booted.fillers, booted.providers
            )
        )
    applied = generations.current()
    bindings.answer.set()
    await running

    assert applied is not booted
    assert registry.held() == [applied]
