"""The session registry, and the cap it enforces.

A slot is what a conversation holds while it is live. The registry is
deliberately dumb: a count and a set, with no queue behind it, because a
device refused a slot reconnects on its next wake word while a
conversation waiting in line would leave a user talking to nothing.
"""

from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from tests.support.apps import entered_app
from tests.support.configs import config_with_agent
from tests.support.wire import connect, shake_hands
from vinga_server.app import create_app
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
    config = config_with_agent()
    config.server.limits.max_sessions = 3
    with entered_app(config) as (app, _):
        # White-box: the cap is a number a registry refuses at, and
        # what a configured one does is observable only by opening that
        # many sessions plus one. The refusal itself is driven above
        # against the default; this says the configured value is the
        # one the registry got.
        assert app.state.composition.sessions._max_sessions == 3
