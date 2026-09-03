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
from tests.support.stores import memory as lane_memory
from vinga_server.device.bindings import BoundNames
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
    """A bindings view whose answer is written down, so a device is
    driven without a database behind it.

    The answer is the raw names, which is what the real view returns:
    which of them can be served is the session's question, asked of the
    one world it captures.
    """

    def __init__(self, agents: list[str]) -> None:
        self._names = tuple(agents)

    async def resolve(self, mac: str) -> BoundNames:
        return BoundNames(self._names)


class Waiting(Bound):
    """A bindings view that answers only when a test lets it, which is
    the window an apply lands in."""

    def __init__(self, agents: list[str]) -> None:
        super().__init__(agents)
        self.asked = asyncio.Event()
        self.answer = asyncio.Event()

    async def resolve(self, mac: str) -> BoundNames:
        self.asked.set()
        await self.answer.wait()
        return await super().resolve(mac)


def connection(
    config: Any, socket: Any, registry: SessionRegistry, generations: Any, **over: Any
) -> DeviceSession:
    """A session wired the way the websocket endpoint wires one."""
    return DeviceSession(
        cast(Any, socket),
        generations,
        bespoke_runtime_factory(generations, McpServers({}), lane_memory()),
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
    registry.admit(session)

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
    registry.admit(session)

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
    registry.admit(session)

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
    registry.admit(session)

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
    registry.admit(session)

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


# The barrier, in both directions
#
# The bindings lookup is awaited and a reload can land inside it, so the
# agent set the session classifies against can be one the binding was
# never read against. Both directions are here because they fail
# differently: a deletion could index a world that has never heard of
# the agent, and an addition could turn away a device the world being
# installed can serve perfectly well. Neither may happen; what may is
# that the session serves the world it captured, whichever that is.


def two_agents() -> Any:
    """Two agents, one device bound to the second of them."""
    return config_with(
        agents={"assistant": {"prompt": "A"}, "helper": {"prompt": "H"}},
        devices={BOUND: ["helper"]},
    )


def installed(generations: Any, config: Any) -> Any:
    """Put a world built from this configuration in front of new work,
    the way an apply does."""
    candidate = type(generations.current())(
        config,
        generations.current().secrets,
        {},
        built_world(config),
    )
    with generations.applying() as install:
        install(candidate)
    return candidate


async def test_an_agent_deleted_during_the_lookup_turns_the_device_away() -> None:
    """The deletion direction. The binding resolves to an agent, an
    apply removes it before the session captures a world, and the
    session classifies against the world it captured: the name is one
    this server cannot serve, so the connection is closed with the
    sentence a device gets for that, and no conversation is built.

    What must not happen is an index error inside the runtime, which is
    what a session that classified against the world it read the binding
    in and then built from a different one would have got.
    """
    config = two_agents()
    generations = world(config, providers=built_world(config))
    registry = SessionRegistry(max_sessions=4, generations=generations)
    bindings = Waiting(["helper"])
    socket = Turned(BOUND)
    session = connection(config, socket, registry, generations, bindings=bindings)
    registry.admit(session)

    running = asyncio.create_task(session.run())
    await asyncio.wait_for(bindings.asked.wait(), timeout=5)
    # The stored world that deleted the agent dropped the binding with
    # it, which is the only shape a valid configuration can have; the
    # session is holding the name the lookup read a moment before that.
    installed(generations, served())
    bindings.answer.set()
    await running
    registry.remove(session)

    assert session.runtime is None
    assert socket.closed is not None
    assert socket.closed[0] == 1008
    assert registry.held() == []


async def test_an_agent_added_during_the_lookup_is_served_at_once() -> None:
    """The addition direction, and the one a stricter rule would have
    got wrong. The binding names an agent the world had no idea about
    when the lookup started; the apply installs it while the lookup is
    in flight, and the session captures that world and builds a
    conversation as that agent, holding exactly the generation it was
    built from.
    """
    before = served()
    generations = world(before, providers=built_world(before))
    registry = SessionRegistry(max_sessions=4, generations=generations)
    bindings = Waiting(["helper"])
    session = connection(before, Vanishing(BOUND), registry, generations, bindings=bindings)
    registry.admit(session)

    running = asyncio.create_task(session.run())
    await asyncio.wait_for(bindings.asked.wait(), timeout=5)
    applied = installed(generations, two_agents())
    bindings.answer.set()
    await running

    assert session.runtime is not None
    assert registry.held() == [applied]

    registry.remove(session)
    await registry.drain(timeout_s=1.0)
