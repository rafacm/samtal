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
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.support.configs import capped_config, config_with_agent, idle_config
from tests.support.sessions import open_session, served
from tests.support.sockets import LoopingSocket
from tests.support.wire import connect, listen_realtime, say_something, shake_hands, wait_for_close
from vinga_server.app import create_app
from vinga_server.conversations.schema import CLOSE_REASONS as STORED_REASONS
from vinga_server.device.session import CLOSE_REASONS
from vinga_server.registry import SessionRegistry


def closed(caplog: pytest.LogCaptureFixture) -> Any:
    (record,) = [r for r in caplog.records if getattr(r, "event", None) == "session_closed"]
    return record


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
    assert registry.admit(session) == "admitting"

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


async def test_a_cleanup_step_that_raises_does_not_hide_the_close(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The runtime's close is one of three steps ahead of the event. Each
    is guarded on its own, so one that raises is reported by class and
    the event, the store's close and the capture's close all still
    happen.

    The reason stays the device's hang-up: what ended this conversation
    was decided before the cleanup ran, and a step that would not finish
    afterwards is a defect to report rather than a different ending."""
    session, websocket, task = await open_session(config_with_agent())
    assert session.runtime is not None

    async def refuse() -> None:
        raise ValueError("the runtime would not let go")

    session.runtime.close = refuse  # type: ignore[method-assign]

    with caplog.at_level("INFO"):
        await websocket.close(1000, "goodbye")
        await asyncio.wait_for(task, timeout=5)

    assert closed(caplog).reason == "client"
    # By class, and never the message: an exception on the way out of a
    # session is one of the places a far side's bytes could reach the
    # retained surface.
    assert "ValueError" in caplog.text
    assert "would not let go" not in caplog.text


async def test_a_cleanup_failure_is_the_reason_when_nothing_else_is(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The backstop, at its one site. Every path into the close latches
    before the cleanup runs, so this is what would answer for a path that
    did not: a session that could not shut down cleanly did not end in a
    conversation."""
    session = served(config_with_agent(), LoopingSocket())

    async def refuse() -> None:
        raise ValueError("the step would not finish")

    # White-box for this file's four reaches. What a session says ended
    # it is a token on the closing event, which the tests above read
    # from the log; these three are about the latch underneath it, and a
    # latch is only ever the right answer when two causes race. A race
    # driven from outside is reproducible with a probability, so the
    # rule (first cause wins, nothing rewrites it, an unlatched close
    # reads as the device hanging up) is asserted at the one place it is
    # written.
    with caplog.at_level("INFO"):
        await session._cleanly("the conversation", refuse())

    assert session._closed_reason() == "error"
    assert "ValueError" in caplog.text
    assert "would not finish" not in caplog.text


async def test_the_first_cause_wins(caplog: pytest.LogCaptureFixture) -> None:
    """Competing terminations are ordinary: an idle timer comes due while
    a drain is already closing the same session. What is recorded is what
    initiated the close, so the reason is deterministic rather than
    whichever site happened to run last."""
    session, websocket, task = await open_session(idle_config(0.2))
    registry = SessionRegistry(max_sessions=8)
    assert registry.admit(session) == "admitting"

    with caplog.at_level("INFO"):
        drain = asyncio.create_task(registry.drain(timeout_s=5))
        # Long enough for the watchdog to come due behind the drain.
        await asyncio.sleep(0.4)
        await drain
        await asyncio.wait_for(task, timeout=5)

    assert closed(caplog).reason == "drain"


async def test_a_disconnect_a_drain_arrives_behind_is_still_a_client_close(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The race the other way round, and the reason `client` is latched
    where the serve loop returns rather than rendered at the end: a
    shutdown reaching a session whose close is already under way must
    not take a cause that was decided before the drain existed.

    The cleanup is held open here so the drain lands inside the window
    that used to be wide enough to lose."""
    session, websocket, task = await open_session(config_with_agent())
    reached = asyncio.Event()
    release = asyncio.Event()

    async def held() -> None:
        reached.set()
        await release.wait()

    # White-box, same file, different half: the window under test is
    # inside one cleanup step, and holding the close there is how a
    # drain arriving mid-close becomes a fact rather than a race.
    session._watchdog.stop = held  # type: ignore[method-assign]
    registry = SessionRegistry(max_sessions=8)
    assert registry.admit(session) == "admitting"

    with caplog.at_level("INFO"):
        # The device hangs up, and the close gets as far as its first
        # cleanup step.
        await websocket.close(1000, "goodbye")
        await asyncio.wait_for(reached.wait(), timeout=5)
        drain = asyncio.create_task(registry.drain(timeout_s=5))
        await asyncio.sleep(0.05)
        release.set()
        await drain
        await asyncio.wait_for(task, timeout=5)

    assert closed(caplog).reason == "client"


async def test_a_latched_token_is_not_rewritten_by_a_later_one() -> None:
    """The latch itself, at the one place it is written, since a race is
    only ever probabilistically reproducible from outside."""
    websocket = LoopingSocket()
    session = served(config_with_agent(), websocket)
    # White-box, per the note at the first of these above.
    session._latch_close("idle")
    session._latch_close("drain")
    assert session._closed_reason() == "idle"


# White-box, per the note at the first latch assertion above.
async def test_nothing_latched_reads_as_client() -> None:
    websocket = LoopingSocket()
    session = served(config_with_agent(), websocket)
    assert session._closed_reason() == "client"
