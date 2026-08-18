"""Draining conversations on the way out.

Uvicorn cannot do this part. Verified in its source: it fail-closes
every open websocket with 1012 the moment its shutdown begins, so
`timeout_graceful_shutdown` alone would cut every reply off mid-word.
The drain therefore runs first, from the signal handler, and uvicorn's
shutdown is what happens after it, its 1012 acting as the backstop for
anything the drain could not finish.
"""

import asyncio
import signal
from typing import Any, cast

import pytest
import uvicorn

from samtal_server.config import Config
from samtal_server.main import (
    PING_INTERVAL_S,
    PING_TIMEOUT_S,
    UVICORN_GRACEFUL_SHUTDOWN_S,
    DrainingServer,
    serve,
)
from samtal_server.registry import SessionRegistry
from tests.support.registry import FakeSession, registry_with


async def test_draining_asks_every_session_to_stop() -> None:
    first, second = FakeSession(), FakeSession()
    await registry_with(first, second).drain(timeout_s=5)
    assert first.shutdown == (1001, "server shutting down")
    assert second.shutdown == (1001, "server shutting down")
    # And says why, so the record of each conversation names the drain
    # rather than whatever arrived behind it.
    assert first.close_reason == second.close_reason == "drain"


async def test_a_reply_in_flight_finishes_before_its_socket_closes() -> None:
    speaking = FakeSession(speaking_for=0.15)
    await registry_with(speaking).drain(timeout_s=5)
    # It was allowed to reach the end of what it was saying.
    assert speaking.shutdown is not None


async def test_draining_refuses_new_sessions() -> None:
    registry = registry_with()
    assert not registry.draining
    await registry.drain(timeout_s=1)
    assert registry.draining
    # A server on its way out does not want the next conversation, even
    # though every slot is now free.
    assert not registry.try_add(cast(Any, FakeSession()))


async def test_draining_an_idle_server_is_immediate() -> None:
    registry = registry_with()
    await asyncio.wait_for(registry.drain(timeout_s=30), timeout=1)


async def test_a_reply_that_outlasts_the_budget_is_still_closed_politely() -> None:
    """The grace expiring is not a reason to leave a socket hanging: the
    device is told "server shutting down" with 1001, which is a better
    answer than uvicorn's eventual 1012."""
    long_reply = FakeSession(speaking_for=30)
    quick = FakeSession()
    registry = registry_with(long_reply, quick)
    await asyncio.wait_for(registry.drain(timeout_s=0.2), timeout=5)
    assert quick.shutdown == (1001, "server shutting down")
    assert long_reply.shutdown == (1001, "server shutting down")


async def test_a_session_stuck_in_its_own_shutdown_is_left_to_uvicorn(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The outer bound is the backstop for a session stuck somewhere the
    reply grace cannot reach. Those are cancelled and left to uvicorn's
    1012 fail-close."""

    class StuckSession(FakeSession):
        async def request_shutdown(
            self, code=1001, reason="", grace_s=10.0, close_reason=None
        ) -> bool:
            await asyncio.sleep(60)
            return True

    registry = registry_with(StuckSession(), FakeSession())
    with caplog.at_level("INFO"):
        await asyncio.wait_for(registry.drain(timeout_s=0.3), timeout=5)

    (incomplete,) = [
        r for r in caplog.records if getattr(r, "event", None) == "drain_incomplete"
    ]
    assert incomplete.unfinished == 1


async def test_the_drain_budget_is_what_a_reply_is_given() -> None:
    """The defect the M7 device checkpoint caught: a constant inside the
    session capped the wait at ten seconds, so raising server.drain_s
    bought a long reply nothing and it was still cut mid-sentence."""
    session = FakeSession()
    await registry_with(session).drain(timeout_s=45)
    assert session.granted_s is not None
    # Nearly all of the budget, less the slice held back for the close.
    assert 43 <= session.granted_s <= 45


async def test_a_reply_cut_mid_sentence_is_reported_as_such(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Reporting this as a clean drain would hide the one signal that
    says drain_s is too short for the replies this server gives."""
    with caplog.at_level("INFO"):
        await registry_with(FakeSession(speaking_for=30)).drain(timeout_s=1.2)

    (incomplete,) = [
        r for r in caplog.records if getattr(r, "event", None) == "drain_incomplete"
    ]
    assert incomplete.cut_mid_reply == 1
    assert incomplete.unfinished == 0
    assert incomplete.levelname == "WARNING"
    assert "drain_finished" not in {getattr(r, "event", None) for r in caplog.records}


async def test_the_drain_reports_what_it_could_not_finish(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("INFO"):
        await registry_with(FakeSession(speaking_for=30)).drain(timeout_s=0.1)
    events = {getattr(record, "event", None) for record in caplog.records}
    assert "drain_started" in events
    assert "drain_incomplete" in events


async def test_the_drain_reports_a_clean_finish(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("INFO"):
        await registry_with(FakeSession()).drain(timeout_s=5)
    events = {getattr(record, "event", None) for record in caplog.records}
    assert "drain_finished" in events
    assert "drain_timeout" not in events


class FakeApp:
    def __init__(self, registry: SessionRegistry) -> None:
        # The registry where the drain reads it: on the composition, which
        # is the one thing a served app's state carries.
        composition = type("Composition", (), {"sessions": registry})()
        self.state = type("State", (), {"composition": composition})()


def draining_server(registry: SessionRegistry, drain_s: float = 5.0) -> DrainingServer:
    app = cast(Any, FakeApp(registry))
    return DrainingServer(uvicorn.Config(app), app, drain_s)


async def test_the_first_signal_drains_before_uvicorn_exits() -> None:
    session = FakeSession(speaking_for=0.1)
    registry = registry_with(session)
    server = draining_server(registry)

    server.handle_exit(signal.SIGTERM, None)
    # The signal did not stop the server on the spot: the conversation
    # gets its sentence first.
    assert not server.should_exit
    for _ in range(100):
        await asyncio.sleep(0.02)
        if server.should_exit:
            break
    assert server.should_exit
    assert session.shutdown is not None


async def test_a_second_signal_forces_the_exit() -> None:
    server = draining_server(registry_with(FakeSession(speaking_for=30)))
    server.handle_exit(signal.SIGTERM, None)
    assert not server.should_exit
    # An operator in a hurry: the second signal is passed straight to
    # uvicorn rather than starting another drain.
    server.handle_exit(signal.SIGTERM, None)
    assert server.should_exit


async def test_a_zero_drain_period_is_an_ordinary_uvicorn_exit() -> None:
    server = draining_server(registry_with(FakeSession()), drain_s=0)
    server.handle_exit(signal.SIGTERM, None)
    assert server.should_exit


def test_the_server_is_built_with_explicit_pings_and_a_short_grace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pings are what settle the per-path idle timeout question: a
    proxy in front needs only a read timeout above the interval."""
    built: dict[str, Any] = {}
    monkeypatch.setattr(DrainingServer, "run", lambda self: built.update(config=self.config))

    config = Config(server={"port": 9001, "drain_s": 12})
    serve(cast(Any, FakeApp(registry_with())), config)

    uvicorn_config = built["config"]
    assert uvicorn_config.ws_ping_interval == PING_INTERVAL_S == 20.0
    assert uvicorn_config.ws_ping_timeout == PING_TIMEOUT_S == 20.0
    assert uvicorn_config.timeout_graceful_shutdown == UVICORN_GRACEFUL_SHUTDOWN_S
    assert uvicorn_config.port == 9001
    # Uvicorn's own loggers propagate into the root handler instead of
    # printing in a second, fixed format.
    assert uvicorn_config.log_config is None
