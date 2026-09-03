"""The session registry, and the cap it enforces.

A slot is what a conversation holds while it is live. The registry is
deliberately dumb: a count and a set, with no queue behind it, because a
device refused a slot reconnects on its next wake word while a
conversation waiting in line would leave a user talking to nothing.
"""

from collections.abc import Collection
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from tests.support.configs import config_with, config_with_agent
from tests.support.wire import connect, shake_hands
from vinga_server.app import create_app
from vinga_server.config.secrets import SecretStore
from vinga_server.generation import Generation, Generations
from vinga_server.registry import SessionRegistry


def fake_session() -> Any:
    """Anything hashable will do: the registry never looks inside."""
    return cast(Any, object())


def test_sessions_are_admitted_up_to_the_cap() -> None:
    registry = SessionRegistry(max_sessions=2)
    first, second, third = fake_session(), fake_session(), fake_session()
    assert registry.try_add(first)
    assert registry.try_add(second)
    assert not registry.try_add(third)
    assert len(registry) == 2


def test_removing_a_session_frees_its_slot() -> None:
    registry = SessionRegistry(max_sessions=1)
    first, second = fake_session(), fake_session()
    assert registry.try_add(first)
    assert not registry.try_add(second)
    registry.remove(first)
    assert registry.try_add(second)


def test_removing_is_idempotent() -> None:
    """It runs in a session's finally, and nothing promises it ran once."""
    registry = SessionRegistry(max_sessions=1)
    session = fake_session()
    registry.try_add(session)
    registry.remove(session)
    registry.remove(session)
    registry.remove(fake_session())
    assert len(registry) == 0


def test_a_full_registry_says_which_of_the_two_reasons_it_is() -> None:
    """The classifier `try_add` refuses through, and the one a readiness
    probe reports: one answer over both facts, so the door and anything
    reporting on the door cannot come to disagree."""
    registry = SessionRegistry(max_sessions=1)
    assert registry.admission == "admitting"
    session = fake_session()
    registry.try_add(session)

    assert registry.admission == "full"

    registry.remove(session)
    assert registry.admission == "admitting"


def test_a_server_on_its_way_out_is_draining_rather_than_full() -> None:
    """Both hold, and the terminal one is the answer: a full server has a
    slot again when a conversation ends, a draining one never admits
    another."""
    registry = SessionRegistry(max_sessions=1)
    registry.try_add(fake_session())

    registry.stop_admitting()

    assert registry.admission == "draining"


def test_shutting_the_door_latches_and_costs_nothing_to_repeat() -> None:
    """The shutdown calls this on every path out and from a signal
    handler, so it has to be idempotent to be free, and it has to latch
    because a server that has started refusing conversations is not going
    to want them again."""
    registry = SessionRegistry(max_sessions=1)

    registry.stop_admitting()
    registry.stop_admitting()

    assert registry.draining
    assert registry.admission == "draining"
    assert not registry.try_add(fake_session())


async def test_the_drain_shuts_the_door_before_it_waits_for_anything() -> None:
    """Latched at `drain`'s entry rather than at its return: the grace
    period is spent on the conversations already in flight, and a device
    arriving during it must not be let in."""
    registry = SessionRegistry(max_sessions=2)
    seen: list[str] = []

    class Watching:
        async def request_shutdown(
            self, code=1001, reason="", grace_s=10.0, close_reason=None
        ) -> bool:
            seen.append(registry.admission)
            return True

    assert registry.try_add(cast(Any, Watching()))

    await registry.drain(timeout_s=1.0)

    assert seen == ["draining"]


def test_the_same_session_does_not_take_two_slots() -> None:
    registry = SessionRegistry(max_sessions=2)
    session = fake_session()
    assert registry.try_add(session)
    assert registry.try_add(session)
    assert len(registry) == 1


def test_a_conversation_takes_a_slot_and_gives_it_back() -> None:
    with TestClient(create_app(config_with_agent())) as client:
        registry = client.app.state.composition.sessions
        assert len(registry) == 0
        with connect(client) as websocket:
            shake_hands(websocket)
            assert len(registry) == 1
        assert len(registry) == 0


def test_a_second_device_is_refused_at_a_cap_of_one() -> None:
    config = config_with_agent()
    config.server.limits.max_sessions = 1
    with TestClient(create_app(config)) as client:
        with connect(client) as first:
            shake_hands(first)
            # Refused on the upgrade, like a bad token: nothing is accepted,
            # so the device never gets a socket it could speak down.
            with pytest.raises(WebSocketDisconnect):
                with connect(client):
                    pass
        # And the slot comes back when the first conversation ends.
        with connect(client) as third:
            assert shake_hands(third)["type"] == "hello"


def test_a_capacity_refusal_is_logged_as_such(caplog: pytest.LogCaptureFixture) -> None:
    config = config_with_agent()
    config.server.limits.max_sessions = 1
    with caplog.at_level("WARNING"):
        with TestClient(create_app(config)) as client:
            with connect(client) as first:
                shake_hands(first)
                with pytest.raises(WebSocketDisconnect):
                    with connect(client):
                        pass

    (rejected,) = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "session_rejected"
    ]
    assert rejected.reason == "capacity"


def test_a_bad_token_is_refused_before_capacity_is_even_considered(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Order matters for what an operator sees: a full server answering
    a forged token with "at capacity" would send them chasing load."""
    config = config_with_agent()
    config.server.limits.max_sessions = 1
    with caplog.at_level("WARNING"):
        with TestClient(create_app(config)) as client:
            with connect(client) as first:
                shake_hands(first)
                headers = {
                    "Device-Id": "aa:bb:cc:dd:ee:ff",
                    "Client-Id": "6f1a2b3c-4d5e-6f70-8192-a3b4c5d6e7f8",
                    "Authorization": "Bearer forged.1700000000",
                }
                with pytest.raises(WebSocketDisconnect):
                    with client.websocket_connect("/xiaozhi/v1/", headers=headers):
                        pass

    reasons = {getattr(record, "reason", None) for record in caplog.records}
    assert "bad_token" in reasons
    assert "capacity" not in reasons


def test_the_cap_is_configurable() -> None:
    """A cap of one is the default's neighbour and proves the refusal
    exists; a configured three has to be three conversations and a
    fourth turned away, which is what the number means to whoever set
    it."""
    config = config_with_agent()
    config.server.limits.max_sessions = 3
    with TestClient(create_app(config)) as client:
        with connect(client) as first:
            shake_hands(first)
            with connect(client) as second:
                shake_hands(second)
                with connect(client) as third:
                    shake_hands(third)
                    with pytest.raises(WebSocketDisconnect):
                        with connect(client):
                            pass


# Which world each conversation is holding
#
# The second thing this registry knows, and the reason it knows it: a
# conversation speaks through the world it was built from, so a world an
# apply replaced may not let go of its engines until the last session
# holding it has ended. Admission is not holding: many admitted sessions
# are turned away before a conversation is ever built.


class Recording(Generations):
    """A holder that records what it was asked to let go of, so a test
    can see the trigger rather than the closing behind it."""

    def __init__(self, first: Generation) -> None:
        super().__init__(first)
        self.asked: list[list[Generation]] = []

    async def dispose(self, held: Collection[Generation] = ()) -> None:
        self.asked.append(list(held))
        await super().dispose(held)


def one_world() -> Generation:
    return Generation(config_with(agents={"assistant": {"prompt": "A"}}), SecretStore())


def test_an_admitted_session_holds_no_world_until_it_binds() -> None:
    """Two states, not one. A device whose id will not parse, or that is
    bound to nothing, is admitted and removed without a conversation
    ever being built for it, and it was never holding anything."""
    registry = SessionRegistry(max_sessions=2, generations=Recording(one_world()))
    admitted = fake_session()

    assert registry.try_add(admitted)

    assert registry.held() == []


async def test_a_bound_session_is_holding_its_world_until_it_is_removed() -> None:
    world = one_world()
    holder = Recording(world)
    registry = SessionRegistry(max_sessions=2, generations=holder)
    session = fake_session()
    registry.try_add(session)

    registry.bound(session, world)
    assert registry.held() == [world]

    registry.remove(session)
    assert registry.held() == []
    # The letting-go it started is this registry's to see out, which the
    # drain is what waits for.
    await registry.drain(timeout_s=1.0)


async def test_removing_a_bound_session_asks_the_holder_to_let_go() -> None:
    """The trigger, which is what makes the end of the last conversation
    on a retired world the moment its engines are released."""
    world = one_world()
    holder = Recording(world)
    registry = SessionRegistry(max_sessions=2, generations=holder)
    session = fake_session()
    registry.try_add(session)
    registry.bound(session, world)

    registry.remove(session)
    await registry.drain(timeout_s=1.0)

    assert holder.asked == [[]]


async def test_removing_a_session_that_never_bound_asks_nothing() -> None:
    """A no-op on that axis, which is what keeps a rejected device from
    triggering a disposal it had nothing to do with."""
    holder = Recording(one_world())
    registry = SessionRegistry(max_sessions=2, generations=holder)
    session = fake_session()
    registry.try_add(session)

    registry.remove(session)
    await registry.drain(timeout_s=1.0)

    assert holder.asked == []
