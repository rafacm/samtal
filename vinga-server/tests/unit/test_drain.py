"""Draining conversations on the way out.

Uvicorn cannot do this part. Verified in its source: it fail-closes
every open websocket with 1012 the moment its shutdown begins, so
`timeout_graceful_shutdown` alone would cut every reply off mid-word.
The drain therefore runs first, from the signal handler, and uvicorn's
shutdown is what happens after it, its 1012 acting as the backstop for
anything the drain could not finish.
"""

import asyncio
import gc
import signal
import time
import weakref
from typing import Any, cast

import pytest
import uvicorn

from tests.support.configs import config_with_agent
from tests.support.registry import FakeSession, registry_with
from vinga_server import serving
from vinga_server.app import create_app, lifespan
from vinga_server.config import Config
from vinga_server.events.live import LiveEvents
from vinga_server.registry import SessionRegistry
from vinga_server.serving import (
    PING_INTERVAL_S,
    PING_TIMEOUT_S,
    UVICORN_GRACEFUL_SHUTDOWN_S,
    DrainingServer,
    serve,
)


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
    assert registry.admit(cast(Any, FakeSession())) != "admitting"


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
    def __init__(self, registry: SessionRegistry, live: LiveEvents | None = None) -> None:
        # The registry where the drain reads it: on the composition, which
        # is the one thing a served app's state carries. The event hub
        # rides beside it for the same reason: the shutdown closes it
        # through the composition, so a stand-in that had none would let
        # the close read something a served app never carries.
        composition = type(
            "Composition",
            (),
            {"sessions": registry, "live": live if live is not None else LiveEvents()},
        )()
        self.state = type("State", (), {"composition": composition})()


class StartingApp:
    """An app whose lifespan has not finished building. Its state bag is
    empty, which is exactly what a served app's is until the composition
    is installed."""

    def __init__(self) -> None:
        self.state = type("State", (), {})()


def draining_server(
    registry: SessionRegistry,
    drain_s: float = 5.0,
    live: LiveEvents | None = None,
) -> DrainingServer:
    app = cast(Any, FakeApp(registry, live))
    return DrainingServer(uvicorn.Config(app), app, drain_s)


def readers_when_uvicorn_stopped(
    monkeypatch: pytest.MonkeyPatch, hub: LiveEvents
) -> list[int]:
    """How many event tails were still open each time uvicorn was told
    to stop.

    The ordering is the claim, and this is what makes it observable: the
    hub is closed before uvicorn's shutdown, so the count read at that
    moment is zero while it was one a line earlier. Patched on uvicorn's
    own class, which is what `super().handle_exit` reaches.
    """
    counts: list[int] = []
    real = uvicorn.Server.handle_exit

    def spy(self: uvicorn.Server, sig: int, frame: Any) -> None:
        counts.append(hub.subscribers)
        real(self, sig, frame)

    monkeypatch.setattr(uvicorn.Server, "handle_exit", spy)
    return counts


async def test_the_event_tails_end_after_the_drain_and_before_uvicorn_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shutdown ordering, against the real draining server rather
    than inferred from a client that went away: the conversations get
    their say, then every open tail is ended, then uvicorn is told to
    stop. A stream left open would be a response uvicorn's graceful
    shutdown waits out."""
    hub = LiveEvents()
    session = FakeSession(speaking_for=0.1)
    server = draining_server(registry_with(session), live=hub)
    watching = hub.subscribe()
    assert hub.subscribers == 1
    counts = readers_when_uvicorn_stopped(monkeypatch, hub)

    server.handle_exit(signal.SIGTERM, None)
    for _ in range(100):
        await asyncio.sleep(0.02)
        if server.should_exit:
            break

    assert server.should_exit
    assert session.shutdown is not None, "the conversations were not drained"
    assert counts == [0], "uvicorn was told to stop with a tail still open"
    assert await anext(aiter(watching), None) is None, "the tail was not ended"


async def test_a_zero_drain_still_ends_the_tails_and_drains_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`drain_s = 0` is a server that does not drain, and this changes
    nothing about that: no conversation is asked to stop, uvicorn is
    called directly, and the only thing between the signal and that call
    is the close every open tail needs in order not to outlive the
    process."""
    hub = LiveEvents()
    session = FakeSession()
    server = draining_server(registry_with(session), drain_s=0.0, live=hub)
    watching = hub.subscribe()
    counts = readers_when_uvicorn_stopped(monkeypatch, hub)

    server.handle_exit(signal.SIGTERM, None)

    assert server.should_exit
    assert session.shutdown is None, "a zero drain asked a conversation to finish"
    assert counts == [0], "uvicorn was told to stop with a tail still open"
    assert await anext(aiter(watching), None) is None, "the tail was not ended"


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


async def test_the_signal_shuts_the_door_before_the_drain_gets_its_turn() -> None:
    """The window this closes. `handle_exit` runs in a signal handler, so
    it schedules the drain onto the loop rather than running it, and
    between the signal and that task's first statement the registry used
    to go on admitting conversations to a process already on its way
    out."""
    registry = registry_with(FakeSession(speaking_for=0.1))
    server = draining_server(registry)

    server.handle_exit(signal.SIGTERM, None)

    # Nothing has run on the loop yet: the drain task has not been
    # created, let alone reached its own first statement.
    assert registry.admission == "draining"
    assert registry.admit(cast(Any, FakeSession())) != "admitting"
    # And the drain still happens, so the door is not all that shut.
    for _ in range(100):
        await asyncio.sleep(0.02)
        if server.should_exit:
            break
    assert server.should_exit


async def test_a_zero_drain_shuts_the_door_it_never_drains_behind() -> None:
    """`drain_s = 0` never calls `drain()` at all, so a flag that only
    turned in there would never turn on this path: the server would go on
    admitting for the whole of uvicorn's own shutdown."""
    registry = registry_with()
    server = draining_server(registry, drain_s=0.0)

    server.handle_exit(signal.SIGTERM, None)

    assert server.should_exit
    assert registry.admission == "draining"
    assert registry.admit(cast(Any, FakeSession())) != "admitting"


def test_a_signal_with_no_loop_to_schedule_on_still_shuts_the_door() -> None:
    """Nothing can be scheduled, so nothing drains. The door shuts
    anyway, because setting one bool needs no loop, which is also what
    makes it safe to do from a signal handler."""
    registry = registry_with()
    server = draining_server(registry)

    server.handle_exit(signal.SIGTERM, None)

    assert server.should_exit
    assert registry.admission == "draining"


async def test_a_second_signal_forces_the_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    hub = LiveEvents()
    registry = registry_with(FakeSession(speaking_for=30))
    server = draining_server(registry, live=hub)
    watching = hub.subscribe()
    counts = readers_when_uvicorn_stopped(monkeypatch, hub)

    server.handle_exit(signal.SIGTERM, None)
    assert not server.should_exit
    # An operator in a hurry: the second signal is passed straight to
    # uvicorn rather than starting another drain.
    server.handle_exit(signal.SIGTERM, None)

    assert server.should_exit
    # And it is still a path out of this process, so the tails end on it
    # too. The drain it interrupted may never reach its own close, and a
    # stream left open is a response uvicorn's shutdown waits out.
    assert counts == [0], "uvicorn was told to stop with a tail still open"
    assert await anext(aiter(watching), None) is None, "the tail was not ended"
    # And it does not reopen the door the first signal shut, which is
    # what it would have had to shut itself had it arrived first.
    assert registry.admission == "draining"


async def test_a_signal_before_the_composition_exists_is_passed_straight_through() -> None:
    """Construction is the lifespan's (#142), and it can spend minutes in
    a provider loading a model, so a redeploy landing on a pod that is
    still starting is ordinary. There are no sessions to drain yet and no
    composition to read them from, so the signal goes to uvicorn the same
    way a second one does, rather than raising inside a signal handler.
    """
    starting = cast(Any, StartingApp())
    server = DrainingServer(uvicorn.Config(starting), starting, 5.0)

    server.handle_exit(signal.SIGTERM, None)

    assert server.should_exit


async def test_a_signal_during_a_build_reaches_the_registry_it_publishes() -> None:
    """Passed through is not forgotten.

    Uvicorn runs the lifespan's startup first and binds its listener the
    moment that returns, and only then notices it was told to stop, so a
    signal that arrived during a long build is followed by a bound socket
    and a fresh registry. A registry published admitting there would take
    a conversation for a process whose shutdown had already begun.

    The signal has nothing to shut when it lands, so it leaves the intent
    where the build reads it, and the build applies it in the same step
    as the publication.
    """
    app = create_app(config_with_agent())
    server = DrainingServer(uvicorn.Config(app), app, 5.0)

    server.handle_exit(signal.SIGTERM, None)

    async with lifespan(app):
        registry = app.state.composition.sessions
        assert registry.admission == "draining"
        assert registry.admit(cast(Any, FakeSession())) != "admitting"


def uvicorn_that_stops_mid_drain(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stand in for uvicorn's own serving, in the shape that makes the
    settle matter: a signal arrives, the drain is scheduled, and serving
    ends while the drain is still in flight, which is what an operator's
    second signal does to it.
    """

    async def _serve(server: uvicorn.Server, sockets: Any = None) -> None:
        server.handle_exit(signal.SIGTERM, None)
        # One turn of the loop is what the scheduled `_start_drain` needs
        # to run and create the task.
        await asyncio.sleep(0)

    monkeypatch.setattr(uvicorn.Server, "_serve", _serve)


async def test_the_drain_task_is_owned_and_finished_before_serving_ends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The task used to be created and dropped (#142), so nothing held
    it, nothing joined it and whatever it raised went to the collector.
    Serving now ends after it, not alongside it."""
    uvicorn_that_stops_mid_drain(monkeypatch)
    session = FakeSession(speaking_for=0.1)
    server = draining_server(registry_with(session))

    # White-box for this file. `serve()` is the public entry and it
    # runs uvicorn against a real socket for the life of a process; what
    # is under test is the shutdown path inside it, driven with a
    # uvicorn that stops mid-drain. And the claim is task ownership:
    # that serving ends after the drain rather than beside it, and that
    # nothing is left for the loop's collector to report. A task nobody
    # holds is exactly the bug, so holding it is how it is checked.
    await server._serve()

    assert server._drain_task is not None
    assert server._drain_task.done()
    # Nothing else drove the loop, so the conversation reached its end
    # because serving waited for it.
    assert session.shutdown == (1001, "server shutting down")


def uvicorn_that_waits_out_the_drain(monkeypatch: pytest.MonkeyPatch) -> None:
    """The other shape: serving that stays up until the drain task is
    over, so the settle meets a task that has already ended."""

    async def _serve(server: uvicorn.Server, sockets: Any = None) -> None:
        server.handle_exit(signal.SIGTERM, None)
        await asyncio.sleep(0)
        task = getattr(server, "_drain_task", None)
        if task is not None:
            # Waited on rather than awaited: waiting does not take what
            # the task ended with off it, which is the whole of what the
            # settle has to be seen doing.
            await asyncio.wait({task}, timeout=5)

    monkeypatch.setattr(uvicorn.Server, "_serve", _serve)


# A credential-shaped string, in the message of an exception raised where
# a client under the drain would raise one.
DRAIN_SENTINEL = "sk-live-drain-9f3c2a"


class ScriptedRegistry:
    """A registry whose drain is scripted, for the paths where what the
    drain does is the whole point.

    The door is here rather than in each of them: the shutdown latches it
    before anything else, so every stand-in for a registry has to be able
    to be shut, and what these scripts vary is the drain behind it.
    """

    def stop_admitting(self) -> None:
        pass


class LeakyDrainFailure(Exception):
    """Stands in for a library exception from under the drain, quoting in
    its message what it was handed."""


def logged_text(caplog: pytest.LogCaptureFixture) -> str:
    return "\n".join(
        [record.getMessage() for record in caplog.records]
        + [str(record.exc_info) for record in caplog.records]
    )


async def assert_the_task_left_nothing_behind(server: DrainingServer) -> None:
    """Drop the drain task and collect it, which proves both that it
    really went and that it left nothing for the loop's own handler to
    print: an exception nobody took off a task is reported there, in
    full, when the collector reaches it."""
    loop = asyncio.get_running_loop()
    reported: list[dict[str, Any]] = []
    loop.set_exception_handler(lambda _loop, context: reported.append(context))
    try:
        # White-box, per the note at the first `_serve` above: task
        # ownership is what is under test, and here the task is taken
        # away deliberately to prove nothing else was holding it.
        task = server._drain_task
        assert task is not None
        server._drain_task = None
        gone = weakref.ref(task)
        del task
        # More than one pass, and a turn of the loop between them: the
        # task, its coroutine and the traceback of whatever it raised
        # reference each other, so what frees them is the cycle
        # collector rather than the last reference going.
        for _ in range(3):
            gc.collect()
            await asyncio.sleep(0)
        assert gone() is None, "the task outlived the check, which proves nothing"
        assert reported == []
    finally:
        loop.set_exception_handler(None)


async def test_a_drain_that_failed_before_the_settle_says_only_its_class(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The path with nothing to wait for is still the path that has to
    ask. A drain runs through provider clients and a database, so what it
    raises can quote a credential, and an exception left on a task is
    printed in full by the collector."""

    class FailingRegistry(ScriptedRegistry):
        async def drain(self, timeout_s: float) -> None:
            raise LeakyDrainFailure(f"the endpoint refused: key={DRAIN_SENTINEL}")

    uvicorn_that_waits_out_the_drain(monkeypatch)
    server = draining_server(cast(Any, FailingRegistry()))

    # White-box, per the note at the first `_serve` above.
    with caplog.at_level("WARNING"):
        await server._serve()

    text = logged_text(caplog)
    assert "LeakyDrainFailure" in text
    assert DRAIN_SENTINEL not in text
    assert DRAIN_SENTINEL not in capsys.readouterr().err
    # No traceback either: a chain is the other way the message travels.
    assert all(record.exc_info is None for record in caplog.records)
    await assert_the_task_left_nothing_behind(server)


async def test_a_drain_that_fails_during_the_settle_says_only_its_class(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """And the waited path says the same thing, rather than the
    exception it was handed while waiting."""

    class SlowlyFailingRegistry(ScriptedRegistry):
        async def drain(self, timeout_s: float) -> None:
            await asyncio.sleep(0.02)
            raise LeakyDrainFailure(f"the endpoint refused: key={DRAIN_SENTINEL}")

    uvicorn_that_stops_mid_drain(monkeypatch)
    server = draining_server(cast(Any, SlowlyFailingRegistry()))

    # White-box, per the note at the first `_serve` above.
    with caplog.at_level("WARNING"):
        await server._serve()

    text = logged_text(caplog)
    assert "LeakyDrainFailure" in text
    assert DRAIN_SENTINEL not in text
    assert DRAIN_SENTINEL not in capsys.readouterr().err
    assert all(record.exc_info is None for record in caplog.records)
    await assert_the_task_left_nothing_behind(server)


async def test_a_drain_that_outlives_its_bound_is_abandoned(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A drain that cannot end must not be able to wedge the exit: the
    bound is the drain budget plus the registry's close margin, and past
    it the process goes anyway, saying so.

    The drain here does not cooperate with being cancelled, which is the
    case a bound has to survive to be one: a `finally` doing cleanup of
    its own, or a client that swallows the cancellation, decides for
    itself when it is done, and serving must not be waiting on that
    decision."""

    class UncooperativeRegistry(ScriptedRegistry):
        def __init__(self) -> None:
            self.released = asyncio.Event()

        async def drain(self, timeout_s: float) -> None:
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                await self.released.wait()

    uvicorn_that_stops_mid_drain(monkeypatch)
    monkeypatch.setattr(serving, "CLOSE_MARGIN_S", 0.05)
    registry = UncooperativeRegistry()
    server = draining_server(cast(Any, registry), drain_s=0.05)

    started = time.monotonic()
    with caplog.at_level("WARNING"):
        # The outer bound is the test's backstop; the assertion below is
        # what says the server's own bound is what let go.
        # White-box, per the note at the first `_serve` above.
        await asyncio.wait_for(server._serve(), timeout=5)
    elapsed = time.monotonic() - started

    assert elapsed < 1
    assert any("drain did not finish" in record.getMessage() for record in caplog.records)
    task = server._drain_task
    assert task is not None
    # Serving let go while the drain was still going, rather than waiting
    # to see what it made of the cancellation.
    assert not task.done()

    # And when it does end, it still delivers the exit, and what it ended
    # with is still taken off it.
    registry.released.set()
    await asyncio.wait({task}, timeout=1)
    assert task.done()
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
