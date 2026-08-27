"""Every emit path, and what makes it fire.

A driver inventory, and nothing more than that. "Baseline" was once a
committed capture of what these paths produced, kept so a conversion
could show the file did not move; that file is gone (#241), and with it
the regeneration command and the byte comparison it fed. What is left is
the machinery: eighty-one drivers, one per emit path, and the two
functions that run them and reduce what they produced to the dimensions
a consumer sees (`driven()` and `captured()`). Nothing here is written
to disk, and there is nothing to regenerate.

What the drivers are held to is `tests/unit/test_event_baseline.py`,
live and against the declarations rather than against a file: every
produced record conforms to the variant its event declares, every driver
carries the fields its path is supposed to carry, and the payload values
are builtins. `docs/reference/events.md` is the committed artifact where
a catalog change is a reviewed diff.

**The completeness claim comes from the catalog, not from this file.** A
runtime harness proves only what it executes, so on its own a set of
drivers proves whatever it happens to run. Every variant the catalog
declares is constructible, and therefore directly drivable, so the suite
holds every one of them to being produced by some driver's run, and a
declaration nothing can produce fails the lane. Beside it, the smaller
claim these drivers can give themselves: one driver per identity,
eighty-one of them, and every record a driver keeps is the event that
driver names.

There used to be a static walk here instead, reading the scoped modules
for emit sites and holding the drivers equal to what it found. It
existed because an untyped emit site was invisible to anything but a
reading of the source: the only way to know a path had no driver was to
find the path in the code. It retired with the last conversion (#210),
along with its chooser-reading, its emitter-binding reading and the
planted sources it was proved on.

`identity` is where a path is, and `event` is what it emits, which is
what its capture is filtered to: a session driver reaches its decision
by holding a whole conversation, so its run emits every neighbouring
path's records too.

The drivers reach into the store and the capture the way the pin suites
they came from do: a writer parked on its gate, an engine that raises, a
free-space reading that refuses, a clock the harness chose. Those
reach-ins are the price of driving a failure path deterministically, and
they are the same ones `test_conversations_store.py` pays.
"""

import asyncio
import datetime as dt
import inspect
import logging
import os
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from dataclasses import replace as dataclass_replace
from pathlib import Path
from typing import Any, cast

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openai import AsyncOpenAI
from sqlalchemy.exc import OperationalError
from starlette.websockets import WebSocketDisconnect

from tests.conftest import clear_store
from tests.support.apps import entered_client
from tests.support.checkin import (
    MOCK_AGENT,
    MOCK_PROVIDERS,
    NORMALIZED,
    activate,
    activation_client,
    check_in,
    ota_client,
    post_system_info,
    unbound_config,
)
from tests.support.configs import (
    BOTH_MAC,
    BOUND_MAC,
    DELAY_MS,
    DEVICE_MAC,
    DEVICE_UUID,
    POET_MAC,
    SPEECH,
    STDIO_SERVER,
    base_config,
    capped_config,
    config_with_agent,
    idle_config,
    masked_config,
    watchdog_config,
    world,
)
from tests.support.providers import (
    STALL_S,
    BrokenTts,
    ConfirmingAsr,
    GatedAsr,
    ScriptedEndpointer,
    ScriptedLlm,
    StallingLlm,
    Unreachable,
    built_world,
)
from tests.support.registry import (
    BINDINGS_DEVICE_MAC,
    FakeSession,
    booted,
    registry_with,
)
from tests.support.registry import check_in as bindings_check_in
from tests.support.sessions import (
    Gate,
    _nothing,
    call,
    device_session,
    drive_reply,
    end_utterance,
    events_of,
    masked_session,
    plant_utterance,
    realtime_session,
    reply_in_flight,
    run_reply,
    session_for,
    start_reply,
    turn_taking,
    with_device,
)
from tests.support.sockets import RecordingSocket
from tests.support.stores import CAPTURE_MANIFEST, corrupt, tone
from tests.support.stores import store as capture_store
from tests.support.tools_mcp import Applying as McpApplying
from tests.support.tools_mcp import config_granting as mcp_granting
from tests.support.tools_mcp import entry_data as mcp_entry_data
from tests.support.tools_mcp import reading as mcp_reading
from tests.support.tools_mcp import reload_config as mcp_config
from tests.support.tools_mcp import running as mcp_running
from tests.support.tools_mcp import started as mcp_started
from tests.support.tools_mcp import stdio_entry as mcp_entry
from tests.support.wire import (
    connect,
    device_headers,
    handshake,
    listen_realtime,
    say_something,
    shake_hands,
    speech_pcm,
    wait_for_close,
)
from vinga_server import onboarding
from vinga_server.app import create_app
from vinga_server.capture import CaptureStore, SessionCapture
from vinga_server.config import Config
from vinga_server.config.api import build_api
from vinga_server.config.loader import StorageError
from vinga_server.config.models import DatabaseConfig
from vinga_server.conversations import store as store_module
from vinga_server.conversations.records import ToolInvocation, TurnRecord
from vinga_server.conversations.store import ConversationStore
from vinga_server.device.bindings import BoundNames, DeviceBindings
from vinga_server.device.session import DeviceSession
from vinga_server.events.catalog import CHANNELS
from vinga_server.filler import build_agent_fillers
from vinga_server.logs import _STANDARD_ATTRIBUTES
from vinga_server.ota import ACTIVATE_SEGMENT, OTA_PATH
from vinga_server.providers import AsrResult, Usage
from vinga_server.providers.openai_asr import OpenAiAsr
from vinga_server.runtime.pipeline import bespoke_runtime_factory
from vinga_server.tools.mcp import McpServers
from vinga_server.tools.mcp.reload import ReloadInProgressError
from vinga_server.tools.memory import MemoryStore

# The channels this harness covers: what a record has to ride to be
# captured at all.
SCOPE: tuple[str, ...] = CHANNELS

# The clock these stores keep, so "recorded two hundred days ago" is a
# number the harness chose rather than a sleep.
NOW = dt.datetime(2026, 8, 15, 12, 0, tzinfo=dt.UTC)


# --- one driver per emit path -----------------------------------------
#
# `identity` is where the path is: its module, its enclosing function,
# and which emit call within it. Deliberately not a line number, for the
# reason the walk that used to read these gave: a line number churns
# with every edit above it.
#
# The static walk itself retired with the last conversion. It existed to
# hold the drivers to the source, because a runtime harness proves only
# what it executes and an untyped emit site was invisible to anything
# but a reading of the code. A variant is a type now, so what the
# drivers are held to instead is the catalog: every variant it declares
# is constructible, and therefore drivable, and
# `tests/unit/test_event_baseline.py` fails if one of them is produced
# by no driver's run. That is a claim about what this server may say
# rather than about which lines happen to say it, which is what the plan
# means by claiming exhaustiveness over variants rather than over call
# sites.


class Raising:
    """An engine whose every transaction fails, so a write and a prune
    can be made to fail on purpose."""

    def begin(self) -> Any:
        raise RuntimeError("a failure the harness planted")

    def dispose(self) -> None:
        return None


@dataclass(frozen=True)
class Driver:
    """One emit path, what makes it fire, and the event it emits.

    `drive` may be a coroutine function. A conversation only exists
    inside a loop, so most of the session channel's paths are reached
    through one; `captured()` runs those in a loop of their own.

    `event` is what its run is filtered to, and the filter is the point
    rather than a tidiness: a session driver reaches its decision by
    holding a whole conversation, so its run emits every neighbouring
    path's records too. Keeping them would record the same shapes
    several times over and make the committed file move whenever an
    unrelated path's timing did.
    """

    identity: tuple[str, str, int]
    drive: Callable[[Path], Any]
    event: str

    @property
    def key(self) -> str:
        module, function, ordinal = self.identity
        return f"{module}:{function} #{ordinal}"


def a_manifest(started_at: dt.datetime) -> dict[str, Any]:
    return {
        "started_at": started_at.isoformat(),
        "server": {"version": "0.1.0", "revision": "abc1234"},
        "device": {"mac": "aa:bb:cc:dd:ee:ff", "client": "test"},
        "protocol": "1",
        "agent": "sam",
        "agents": ["sam"],
        "providers": {},
    }


def a_turn(conversation: str = "9f0c1d2e3a4b5c6d7e8f90a1b2c3d4e5") -> TurnRecord:
    return TurnRecord(
        at=101.0,
        conversation=conversation,
        agent="sam",
        heard="hello there",
        reply="Hi.",
        tools=(
            ToolInvocation(position=0, source="builtin", name="remember", result="ok"),
        ),
    )


def drive_enabled(directory: Path) -> None:
    """`start()` says this server is recording."""
    store = ConversationStore(DatabaseConfig())
    try:
        store.start()
    finally:
        store.stop()


def drive_dropped(directory: Path) -> None:
    """The in-flight bound reached, with the writer parked so the queue
    fills deterministically."""
    ceiling = store_module.MAX_EVENTS_IN_FLIGHT
    store_module.MAX_EVENTS_IN_FLIGHT = 4
    gate = Gate()
    store = ConversationStore(DatabaseConfig(), gate=gate)
    try:
        store.start()
        store.open_session("alpha", 100.0, a_manifest(NOW))
        gate.wait()
        for index in range(10):
            store.record_event("alpha", "vad_edge", logging.DEBUG, {"n": index}, 101.0)
        gate.open_forever()
    finally:
        store.stop()
        store_module.MAX_EVENTS_IN_FLIGHT = ceiling


def drive_write_failed(directory: Path) -> None:
    """A batch that did not commit: the writer is parked in front of the
    turn's own transaction, which is what makes the swap hit exactly
    that one."""
    gate = Gate()
    store = ConversationStore(DatabaseConfig(), gate=gate, retention_days=0)
    try:
        store.start()
        store.open_session("alpha", 100.0, a_manifest(NOW))
        gate.wait()
        gate.let_through()
        store.record_turn("alpha", a_turn())
        gate.wait()
        # White-box, as in `test_conversations_store.py`: an accepted
        # write that the database then refuses is only reachable with a
        # broken engine. The real one is let go of first, or its pool
        # outlives this driver.
        store._engine.dispose()
        store._engine = Raising()  # type: ignore[assignment]
        gate.open_forever()
    finally:
        store.stop()


def drive_prune_failed(directory: Path) -> None:
    """Retention that could not delete."""
    store = ConversationStore(DatabaseConfig(), retention_days=90, now=lambda: NOW)
    try:
        store._engine.dispose()
        store._engine = Raising()  # type: ignore[assignment]
        store._prune()
    finally:
        store.stop()


def _seed_old_conversation(name: str, then: dt.datetime) -> None:
    """One whole session recorded as it would have been at `then`: the
    session row, its thread and its events all that old."""
    seeding = ConversationStore(DatabaseConfig(), retention_days=0, now=lambda: then)
    seeding.start()
    seeding.open_session(name, 100.0, a_manifest(then))
    seeding.record_turn(name, a_turn(f"{name.replace('-', '0'):0<32}"))
    seeding.close_session(name, duration_s=5.0, reason="client")
    seeding.stop()


def drive_pruned(directory: Path) -> None:
    """Retention that did: two conversations seeded old enough to go.

    Each seeded through a store whose clock is that old, because what
    retention measures is a thread's last activity: a session stamped
    two hundred days ago whose turns landed now holds a live
    conversation, which is the case the ruleset protects rather than
    the one this driver needs.
    """
    for name, age in (("old-one", 200), ("old-two", 300)):
        _seed_old_conversation(name, NOW - dt.timedelta(days=age))

    pruning = ConversationStore(DatabaseConfig(), retention_days=90, now=lambda: NOW)
    try:
        pruning.start()
    finally:
        pruning.stop()


MODULE = "vinga_server.conversations.store"

STORE_DRIVERS: tuple[Driver, ...] = (
    Driver((MODULE, "ConversationStore.start", 1), drive_enabled, "conversations_enabled"),
    Driver((MODULE, "ConversationStore.record_event", 1), drive_dropped, "conversations_dropped"),
    Driver((MODULE, "ConversationStore._failed", 1), drive_write_failed, "conversations_failed"),
    Driver((MODULE, "ConversationStore._prune", 1), drive_prune_failed, "conversations_failed"),
    Driver((MODULE, "ConversationStore._prune", 2), drive_pruned, "conversations_pruned"),
)


# --- the session channel's drivers ------------------------------------
#
# Ported from the prose pin suite #210 retired: those tests drove every
# one of these paths onto its own decision, and driving is exactly what
# these checks need. What they asserted about the record is the
# declarations' now; how they reached the record is here.
#
# Some drivers run more than one scenario, because a site can emit more
# than one record shape: `llm_round` names the configured entry behind
# a provider the registry built, and says nothing about a provider it
# never built, from the same call and in one variant. One driver per
# PATH is the harness's identity rule; how many shapes that path can
# produce is the path's business.

# What the direct drivers hand a reply: 20 ms of silence, which the mock
# ASR answers whatever it holds.
UTTERANCE = b"\x00\x00" * 320

# The model a provider entry is configured with, planted on the identity
# a script borrows from the mock it stands in for.
MODEL = "qwen3:8b"


class TurnedAwaySocket:
    """Just enough websocket for a connection that is refused: the
    handshake headers, the accept, and the close."""

    def __init__(self, device_id: str) -> None:
        self.headers = {"device-id": device_id, "client-id": DEVICE_UUID}

    async def accept(self) -> None:
        return None

    async def close(self, code: int, reason: str) -> None:
        return None


class ScriptedBindings:
    """A bindings view whose answer is written down, so the two no-agent
    rejections are driven without a database behind them.

    The answer is the raw names, as the real view's is: which of them
    this server can serve is the session's question, and a name no
    configuration here defines is what drives the not-loaded rejection.
    """

    def __init__(self, bound: BoundNames) -> None:
        self._bound = bound

    async def resolve(self, mac: str) -> BoundNames:
        return self._bound


class Failing:
    """A provider that raises for every stage and names no configured
    entry, which is what a provider the registry never built looks
    like."""

    sample_rate = 16000

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def transcribe(self, *args: object, **kwargs: object) -> Any:
        raise self._exc

    async def stream(self, *args: object, **kwargs: object) -> Any:
        raise self._exc
        yield  # pragma: no cover - never reached, makes this a generator


async def turned_away(
    config: Config, device_id: str, resolution: BoundNames | None = None
) -> None:
    """One connection that never becomes a session."""
    generations = world(config, providers=built_world(config))
    factory = bespoke_runtime_factory(generations, McpServers({}), None)
    session = DeviceSession(
        cast(Any, TurnedAwaySocket(device_id)),
        generations,
        factory,
        bindings=None if resolution is None else cast(Any, ScriptedBindings(resolution)),
    )
    await session.run()


def apart(config: Config, directory: Path) -> Config:
    """This driver's configuration, unchanged.

    It used to point the app at a database directory of its own,
    because a driver that built an app migrated one and the next app to
    find a migrated database resolved its bindings from it rather than
    from the configuration it was built with. Neither half of that is
    true any more (#283): an app composed from a `Config` handed to it
    is snapshot-authoritative and reads no store at all, and what a
    driver writes into the conversation record is cleared between
    drivers by `driven()`. Kept as a name rather than deleted at every
    call site, so the reason survives where a reader will meet it.
    """
    return config


def hold_a_conversation(config: Config) -> None:
    """One session over a real socket, opened, spoken to and closed."""
    with TestClient(create_app(config)) as client:
        with connect(client) as websocket:
            shake_hands(websocket)
            say_something(websocket)


def speaking_session(scripts: dict[str, Any] | None = None, mac: str = POET_MAC) -> Any:
    """A session on a recording socket, which is what makes a reply run
    all the way through speaking."""
    session = session_for(base_config(), mac, cast(Any, scripts))
    session.websocket = cast(Any, RecordingSocket())
    return session


def unregistered(
    llm: Any, agent: str = "poet", mac: str = POET_MAC, config: Config | None = None
) -> Any:
    """A session whose LLM the provider registry never built, so the
    events it emits name no configured entry: the same variant every
    provider event has, with the four fields it cannot fill left absent
    rather than guessed at.

    `config` defaults to the two-agent base, which is what every caller
    but one wants. The one is the watchdog driver: a bound is a server
    setting, so a scenario about a timeout has to be able to say which
    configuration the unregistered session runs under, or it silently
    waits out the production default.
    """
    config = base_config() if config is None else config
    engines = built_world(config)
    built = engines.agents[agent]
    agents = dict(engines.agents)
    agents[agent] = type(built)(llm=llm, asr=built.asr, tts=built.tts, vad=built.vad)
    session = device_session(config, mac, dataclass_replace(engines, agents=agents))
    session.websocket = cast(Any, RecordingSocket())
    return session


async def failing_reply(stage: str, provider: Any, watch: Any = None) -> Any:
    """One reply against a provider that fails, and the session it ran
    in.

    `watch` attaches a consumer before the reply starts, which is what
    the privacy suite next door needs and what a driver here has no use
    for: a claim about what reaches a tap has to be asserted at the tap
    rather than inferred from the log.
    """

    class TextSink:
        async def send_text(self, text: str) -> None:
            return None

    session = session_for(
        base_config(),
        POET_MAC,
        {"poet": ScriptedLlm(["One sentence."])},
        stages={stage: cast(Any, provider)},
    )
    session.websocket = cast(Any, TextSink())
    with_device(session, POET_MAC)
    session.send_audio = _nothing  # type: ignore[method-assign]
    if watch is not None:
        events_of(session).attach(watch)
    await drive_reply(session, UTTERANCE)
    return session


async def speaking_reply(config: Config, asr: Any) -> Any:
    """A session whose reply is past its own ASR and already speaking,
    which is where the last two barge-in gates are reached from."""
    session, socket = realtime_session(config, asr)
    turn_taking(session).endpointer = ScriptedEndpointer(speech_ms=600)
    start_reply(session, speech_pcm(600))
    while socket.frames < 3:
        await asyncio.sleep(0.02)
    return session


# device/session.py


def drive_session_idle(directory: Path) -> None:
    with TestClient(create_app(apart(idle_config(0.3), directory))) as client:
        with connect(client) as websocket:
            shake_hands(websocket)
            listen_realtime(websocket)
            wait_for_close(websocket)


async def drive_bad_device_id(_: Path) -> None:
    await turned_away(config_with_agent(), "not-a-mac")


async def drive_agent_not_loaded(_: Path) -> None:
    await turned_away(config_with_agent(), DEVICE_MAC, BoundNames(names=("poet",)))


async def drive_no_agent(_: Path) -> None:
    await turned_away(config_with_agent(), DEVICE_MAC, BoundNames(names=()))


def drive_session_open(directory: Path) -> None:
    hold_a_conversation(apart(config_with_agent(), directory))


def drive_session_limit(directory: Path) -> None:
    with TestClient(create_app(apart(capped_config(0.3), directory))) as client:
        with connect(client) as websocket:
            shake_hands(websocket)
            wait_for_close(websocket)


def drive_session_closed(directory: Path) -> None:
    hold_a_conversation(apart(config_with_agent(), directory))


def drive_speaking_started(directory: Path) -> None:
    hold_a_conversation(apart(config_with_agent(), directory))


# runtime/pipeline.py


async def drive_llm_retry(_: Path) -> None:
    """Both halves of the retry, under the same shrunk bound.

    What must stay true here is the opposite of the filler drivers'
    invariant: the first stall has to EXCEED the bound, or the watchdog
    never fires and the path emits nothing. `watchdog_config()`'s bound
    is 0.05 s and `STALL_S` is 30 s, six hundred times it, so each half
    times out once and retries; the retry's own delay is 0.0, under the
    bound, so the round recovers rather than being given up, which is
    what keeps this one `llm_retry` per half rather than a
    `provider_failed`. The 30 s is never waited out: the watchdog
    cancels the sleep at its deadline.

    The second half runs unregistered, and passes the same config for
    that reason: it used to take `base_config()` and so ran against the
    production default of 10 s, waiting out a real timeout for a record
    identical to the one 0.05 s produces.
    """
    llm = StallingLlm(delays=[STALL_S, 0.0])
    session = session_for(watchdog_config(), POET_MAC, {"poet": cast(Any, llm)})
    llm.identity = replace(llm.identity, model=MODEL)  # type: ignore[attr-defined]
    await run_reply(session, "are you there")
    await run_reply(
        unregistered(
            StallingLlm(delays=[STALL_S, 0.0]), mac=POET_MAC, config=watchdog_config()
        ),
        "again",
    )


async def drive_llm_round(_: Path) -> None:
    script = ScriptedLlm([["Two words.", Usage(prompt_tokens=140, completion_tokens=12)]])
    session = speaking_session({"poet": script})
    script.identity = replace(script.identity, model=MODEL)  # type: ignore[attr-defined]
    await drive_reply(session, UTTERANCE)
    await drive_reply(unregistered(ScriptedLlm(["Two words."])), UTTERANCE)


async def drive_provider_failed(_: Path) -> None:
    await failing_reply("asr", Unreachable("asr", ConnectionRefusedError("no route")))
    await failing_reply("asr", Failing(ConnectionRefusedError("no route")))


def drive_prompt_assembled(_: Path) -> None:
    session_for(base_config(), POET_MAC)


async def drive_heard(_: Path) -> None:
    await drive_reply(speaking_session({"poet": ScriptedLlm(["Two words."])}), UTTERANCE)


async def drive_replied(_: Path) -> None:
    await drive_reply(speaking_session({"poet": ScriptedLlm(["Two words."])}), UTTERANCE)


async def drive_tool_call(directory: Path) -> None:
    builtin = ScriptedLlm([[call("remember", text="I like tea")], "Noted."])
    await run_reply(
        session_for(
            base_config(), POET_MAC, {"poet": builtin}, memory=MemoryStore(directory)
        ),
        "remember that I like tea",
    )
    invented = ScriptedLlm([[call("nothing_publishes_this")], "I could not do that."])
    await run_reply(session_for(base_config(), POET_MAC, {"poet": invented}), "do it")

    config = base_config(
        mcp_servers={
            "tools": {
                "transport": "stdio",
                "command": sys.executable,
                "args": [str(STDIO_SERVER)],
            }
        },
        agents={
            "poet": {"prompt": "POET", "tts": "tenor", "mcp": ["tools"]},
            "tutor": {"prompt": "TUTOR", "tts": "alto"},
        },
    )
    servers = McpServers.build(config)
    await servers.start_all()
    try:
        asking = ScriptedLlm([[call("tools__secret_word")], "Done."])
        await run_reply(
            session_for(base_config(), POET_MAC, {"poet": asking}, mcp_servers=servers),
            "ask the server",
        )
    finally:
        await servers.stop_all()


async def drive_agent_said(_: Path) -> None:
    await run_reply(handing_over(), "get me the tutor")


async def drive_handover(_: Path) -> None:
    await run_reply(handing_over(), "get me the tutor")


def handing_over() -> Any:
    scripts = {
        "poet": ScriptedLlm([["Handing you over.", call("switch_agent", agent="tutor")]]),
        "tutor": ScriptedLlm(["Hello, I am the tutor."]),
    }
    return session_for(base_config(), BOTH_MAC, scripts)


# runtime/turntaking.py


async def drive_barge_in_manual(_: Path) -> None:
    """The unconditional cancel in `finish_utterance`: no gate ran, and
    the reply had not spoken, so no speaking_ms is carried."""
    asr = GatedAsr()
    session, _socket = realtime_session(config_with_agent(), asr)
    turn_taking(session).endpointer = ScriptedEndpointer(speech_ms=600)
    plant_utterance(session, speech_pcm(320))
    await end_utterance(session)
    await asyncio.sleep(0.05)
    plant_utterance(session, speech_pcm(320))
    await end_utterance(session, endpointed=False)
    asr.release.set()
    await reply_in_flight(session)


async def drive_barge_in_under_the_floor(_: Path) -> None:
    asr = GatedAsr()
    session, _socket = realtime_session(config_with_agent(), asr)
    turn_taking(session).endpointer = ScriptedEndpointer(speech_ms=600)
    plant_utterance(session, speech_pcm(320))
    await end_utterance(session)
    await asyncio.sleep(0.05)
    turn_taking(session).endpointer = ScriptedEndpointer(speech_ms=100)
    plant_utterance(session, speech_pcm(320))
    await end_utterance(session)
    asr.release.set()
    await reply_in_flight(session)


async def drive_barge_in_merged(_: Path) -> None:
    asr = GatedAsr()
    session, _socket = realtime_session(config_with_agent(), asr)
    turn_taking(session).endpointer = ScriptedEndpointer(speech_ms=600)
    plant_utterance(session, speech_pcm(320))
    await end_utterance(session)
    await asyncio.sleep(0.05)
    plant_utterance(session, speech_pcm(480))
    await end_utterance(session)
    asr.release.set()
    await reply_in_flight(session)


async def drive_barge_in_in_the_refractory_window(_: Path) -> None:
    config = config_with_agent(
        llm_reply="Hold the thought while this sentence finishes playing out loud.",
        server={"barge_in_refractory_ms": 100_000},
    )
    asr = ConfirmingAsr(AsrResult(text="stop"))
    asr.release.set()
    session = await speaking_reply(config, asr)
    plant_utterance(session, speech_pcm(600))
    await end_utterance(session)
    await reply_in_flight(session)


async def drive_barge_in_without_a_transcript(_: Path) -> None:
    config = config_with_agent(
        llm_reply="Hold the thought while this sentence finishes playing out loud.",
        server={"barge_in_refractory_ms": 0},
    )
    asr = ConfirmingAsr(AsrResult(text=""))
    asr.release.set()
    session = await speaking_reply(config, asr)
    plant_utterance(session, speech_pcm(600))
    await end_utterance(session)
    await reply_in_flight(session)


async def drive_barge_in_confirmed(_: Path) -> None:
    """The gate's own cancel, which unlike the manual one fires while
    the reply is speaking and therefore carries speaking_ms."""
    config = config_with_agent(
        llm_reply="Answering {text}.", server={"barge_in_refractory_ms": 0}
    )
    asr = ConfirmingAsr(AsrResult(text="stop and listen"))
    asr.release.set()
    session = await speaking_reply(config, asr)
    plant_utterance(session, speech_pcm(600))
    await end_utterance(session)
    await reply_in_flight(session)


# runtime/filler_runner.py
#
# A stall of this milliseconds' own scale, the way
# `tests/unit/test_session_filler.py` keeps a local one: the shared
# `STALL_S` is 30 s, which the watchdog suites never wait out because
# the watchdog cancels the sleep, but no filler driver shrinks the
# watchdog's bound, so here the 10 s bound was waited out in full, twice
# per driver (a 10 s production-default window, one retry, a second
# window, and the round given up).
#
# What each of the three drivers below needs is only that the filler's
# timer expires while the reply is still silent, which is a race between
# the 60 ms `DELAY_MS` and this: 0.5 s is over eight times it, and the
# reply now simply succeeds at 0.5 s instead of being given up at 20 s.
# The three `filler_*` records the drivers keep are emitted before
# `FillerRunner._fire` touches the reply at all: the two skips emit and
# return, and `filler_played` is emitted before `begin_speaking()` is
# awaited. So what the stalled round eventually does reaches none of
# them.
FILLER_STALL_S = 0.5


async def drive_filler_skipped_for_speech(_: Path) -> None:
    """Speech held at fire time, so the mask stands down.

    Still true at 0.5 s: the endpointer is fed 20 ms in, the timer fires
    at 60 ms, and both are inside a stall that keeps the reply's first
    audio away until 500 ms.
    """
    session = await masked_session(
        masked_config(), POET_MAC, {"poet": StallingLlm([FILLER_STALL_S])}
    )
    start_reply(session, UTTERANCE)
    await asyncio.sleep(DELAY_MS / 1000 / 3)
    turn_taking(session).endpointer.feed(SPEECH)
    await reply_in_flight(session)


async def drive_filler_skipped_for_a_barge_in(_: Path) -> None:
    """The outgoing frames paused at fire time, so the mask stands down.

    Still true at 0.5 s: the pause goes on at 20 ms and comes off at
    80 ms, the timer fires at 60 ms between them, and the reply's first
    audio cannot arrive before 500 ms.
    """
    session = await masked_session(
        masked_config(), POET_MAC, {"poet": StallingLlm([FILLER_STALL_S])}
    )
    start_reply(session, UTTERANCE)
    await asyncio.sleep(DELAY_MS / 1000 / 3)
    # White-box: the pause the confirmation ladder holds, put on at the
    # instant the fire rule reads it, which three real clocks would have
    # to agree on.
    turn_taking(session)._pause_output()
    await asyncio.sleep(DELAY_MS / 1000)
    turn_taking(session)._resume_output()
    await reply_in_flight(session)


async def drive_filler_played(_: Path) -> None:
    """Nothing in the way at fire time, so the clip plays.

    Still true at 0.5 s: the timer fires at 60 ms with the floor free
    and the reply still silent, which is the whole condition the clip
    needs. The reply then arrives and queues behind the clip's tail,
    where before it was given up two watchdog windows later; both are
    after the `filler_played` this driver keeps.
    """
    session = await masked_session(
        masked_config(), POET_MAC, {"poet": StallingLlm([FILLER_STALL_S])}
    )
    await drive_reply(session, UTTERANCE)


EDGE = "vinga_server.device.session"
PIPELINE = "vinga_server.runtime.pipeline"
TURNTAKING = "vinga_server.runtime.turntaking"
FILLER = "vinga_server.runtime.filler_runner"

SESSION_DRIVERS: tuple[Driver, ...] = (
    Driver((EDGE, "DeviceSession._idle_expired", 1), drive_session_idle, "session_idle"),
    Driver((EDGE, "DeviceSession.run", 1), drive_bad_device_id, "session_rejected"),
    Driver((EDGE, "DeviceSession.run", 2), drive_agent_not_loaded, "session_rejected"),
    Driver((EDGE, "DeviceSession.run", 3), drive_no_agent, "session_rejected"),
    Driver((EDGE, "DeviceSession.run", 4), drive_session_open, "session_open"),
    Driver((EDGE, "DeviceSession.run", 5), drive_session_limit, "session_limit"),
    Driver((EDGE, "DeviceSession.run", 6), drive_session_closed, "session_closed"),
    Driver((EDGE, "DeviceSession.send_audio", 1), drive_speaking_started, "speaking_started"),
    Driver((PIPELINE, "PipelineRuntime._watchdog_stream", 1), drive_llm_retry, "llm_retry"),
    Driver((PIPELINE, "PipelineRuntime._llm_round_done", 1), drive_llm_round, "llm_round"),
    Driver(
        (PIPELINE, "PipelineRuntime._provider_failed", 1),
        drive_provider_failed,
        "provider_failed",
    ),
    Driver(
        (PIPELINE, "PipelineRuntime._prompt_assembled", 1),
        drive_prompt_assembled,
        "prompt_assembled",
    ),
    Driver((PIPELINE, "PipelineRuntime._reply", 1), drive_heard, "heard"),
    Driver((PIPELINE, "PipelineRuntime._reply", 2), drive_replied, "replied"),
    Driver((PIPELINE, "PipelineRuntime._speak_reply", 1), drive_agent_said, "agent_said"),
    Driver((PIPELINE, "PipelineRuntime._speak_reply", 2), drive_handover, "handover"),
    Driver((PIPELINE, "PipelineRuntime._run_one", 1), drive_tool_call, "tool_call"),
    Driver((TURNTAKING, "TurnTaking.finish_utterance", 1), drive_barge_in_manual, "barge_in"),
    Driver(
        (TURNTAKING, "TurnTaking._gate_barge_in", 1),
        drive_barge_in_under_the_floor,
        "barge_in_suppressed",
    ),
    Driver((TURNTAKING, "TurnTaking._gate_barge_in", 2), drive_barge_in_merged, "barge_in_merged"),
    Driver(
        (TURNTAKING, "TurnTaking._gate_barge_in", 3),
        drive_barge_in_in_the_refractory_window,
        "barge_in_suppressed",
    ),
    Driver(
        (TURNTAKING, "TurnTaking._gate_barge_in", 4),
        drive_barge_in_without_a_transcript,
        "barge_in_suppressed",
    ),
    Driver((TURNTAKING, "TurnTaking._gate_barge_in", 5), drive_barge_in_confirmed, "barge_in"),
    Driver((FILLER, "FillerRunner._fire", 1), drive_filler_skipped_for_speech, "filler_skipped"),
    Driver(
        (FILLER, "FillerRunner._fire", 2),
        drive_filler_skipped_for_a_barge_in,
        "filler_skipped",
    ),
    Driver((FILLER, "FillerRunner._fire", 3), drive_filler_played, "filler_played"),
)


# --- the server channels ----------------------------------------------
#
# Ported from `test_server_event_pins.py`, which drove every one of these
# paths onto its own decision. Driving is exactly what these checks
# need, so the drivers come from there rather than being invented beside
# it.
#
# The monkeypatching those tests do with a fixture is done by hand here,
# saved and restored, for the reason `patched()` gives.

# What a value that has to move between runs is planted as: an API token
# long enough for the configuration API to accept, and the prompt the
# ASR echo guard trips on.
API_TOKEN = "test-api-token-" + "0123456789abcdef" * 2

ECHO_PROMPT = "vinga, Oliver"

# One 16 kHz second of s16le silence, which the echo guard's five paths
# are driven with.
ONE_SECOND = b"\x00\x00" * 16000

CAPTURE_DIR = "/var/lib/vinga/captures"

PINNED_KEY = "ABCDEFGH"


@contextmanager
def patched(owner: object, name: str, replacement: object) -> Iterator[None]:
    """One attribute swapped for the length of a block.

    `monkeypatch` is a fixture, and a driver is an ordinary function
    that anything may call: `tests/tools/driver_times.py` runs the whole
    inventory outside pytest.
    """
    original = getattr(owner, name)
    setattr(owner, name, replacement)
    try:
        yield
    finally:
        setattr(owner, name, original)


def raising(exc: BaseException) -> Callable[..., Any]:
    def refuse(*_args: object, **_kwargs: object) -> Any:
        raise exc

    return refuse


def banner_config(**onboarding_options: object) -> Config:
    return Config(
        server={
            "public_url": "https://voice.example",
            "onboarding": {"key": PINNED_KEY, **onboarding_options},
        }
    )


def recorded(directory: Path, sessions: int) -> CaptureStore:
    """A directory with finished captures in it, each older than the
    next, so a budget below their total has an unambiguous oldest to
    drop."""
    roomy = capture_store(directory)
    opened = time.monotonic()
    for index in range(sessions):
        capture = roomy.open(f"s{index}", opened, CAPTURE_MANIFEST)
        assert capture is not None
        capture.microphone(tone(3000), opened)
        capture.close()
        for suffix in (".wav", ".jsonl", ".json"):
            path = capture.wav_path.with_suffix(suffix)
            if path.exists():
                os.utime(path, (opened + index, opened + index))
    return roomy


def drive_capture_enabled(directory: Path) -> None:
    config = config_with_agent(
        server={"capture": {"enabled": True, "dir": CAPTURE_DIR}}
    )
    with entered_client(apart(config, directory)):
        pass


def drive_capture_disabled(directory: Path) -> None:
    config = config_with_agent(
        server={"capture": {"enabled": False, "dir": CAPTURE_DIR}}
    )
    with entered_client(apart(config, directory)):
        pass


def drive_capture_failed(directory: Path) -> None:
    """A write onto a closed file, which is what a real failure looks
    like from inside the capture."""
    opened = time.monotonic()
    capture = capture_store(directory).open("s1", opened, CAPTURE_MANIFEST)
    assert capture is not None
    # White-box: a real failed write needs a file that cannot be
    # written to, and nothing public makes one.
    capture._wav.close()  # type: ignore[union-attr]
    capture.microphone(tone(100, 1000), opened)
    capture.microphone(tone(100, 1000), opened + 3.0)


def drive_capture_limit(directory: Path) -> None:
    opened = time.monotonic()
    capture = capture_store(directory, max_session_s=1.0).open(
        "s1", opened, CAPTURE_MANIFEST
    )
    assert capture is not None
    capture.microphone(tone(500, 1000), opened + 2.0)


def drive_capture_pruned(directory: Path) -> None:
    recorded(directory, sessions=2)
    # A second store over the same directory, so the prune happens where
    # the harness is listening rather than inside an earlier close.
    assert capture_store(directory, max_total_mb=0.3).prune() == ["s0"]


def drive_capture_over_budget(directory: Path) -> None:
    """Over the budget with nothing left to drop: the newest capture is
    never pruned."""
    keeper = recorded(directory, sessions=1)
    # White-box: the case is a store already over its budget with only
    # the newest capture left, and the public route there is recording
    # gigabytes.
    keeper._max_total_mb = 0.01
    assert keeper.prune() == []


def drive_capture_declined_unusable(directory: Path) -> None:
    keeper = capture_store(directory)
    with patched(CaptureStore, "_free_mb", raising(OSError("the volume said no"))):
        assert keeper.open("s1", time.monotonic(), CAPTURE_MANIFEST) is None


def drive_capture_declined_below_floor(directory: Path) -> None:
    keeper = capture_store(directory, min_free_mb=10_000_000.0)
    assert keeper.open("s1", time.monotonic(), CAPTURE_MANIFEST) is None


def drive_capture_declined_unopenable(directory: Path) -> None:
    keeper = capture_store(directory)
    with patched(SessionCapture, "start", raising(OSError("no room for the files"))):
        assert keeper.open("s1", time.monotonic(), CAPTURE_MANIFEST) is None


def drive_capture_started(directory: Path) -> None:
    capture = capture_store(directory).open("s1", time.monotonic(), CAPTURE_MANIFEST)
    assert capture is not None
    capture.close()


def api_raising(directory: Path, exc: Exception) -> FastAPI:
    api = build_api(API_TOKEN, DatabaseConfig())

    @api.get("/boom")
    def endpoint() -> dict[str, str]:
        raise exc

    return api


def drive_api_error(directory: Path) -> None:
    api = api_raising(directory, RuntimeError("nothing a log may repeat"))
    answer = TestClient(api).get("/boom", headers={"Authorization": f"Bearer {API_TOKEN}"})
    assert answer.status_code == 500


def drive_api_storage_error(directory: Path) -> None:
    api = api_raising(directory, StorageError("the options column does not hold an object"))
    answer = TestClient(api).get("/boom", headers={"Authorization": f"Bearer {API_TOKEN}"})
    assert answer.status_code == 500


def drive_bindings_unreadable(_: Path) -> None:
    """A lookup whose read fails, which is loud and not fatal.

    Its SQLite-era form overwrote the database file with rubbish. There
    is no file, so the engine is replaced with one whose every connect
    raises, which is the same thing from the view's side: a read it
    cannot make, answered from the generation being served.
    """
    config = booted(devices={BINDINGS_DEVICE_MAC: ["assistant"]})
    bindings = DeviceBindings.open(world(config))
    try:
        bindings._engine.dispose()
        bindings._engine = _Unreadable()  # type: ignore[assignment]
        bindings.names_for(BINDINGS_DEVICE_MAC)
    finally:
        bindings.dispose()


class _Unreadable:
    """An engine whose every connection fails, which is what a database
    a lookup cannot reach looks like from the bindings view."""

    def connect(self) -> object:
        raise OperationalError("select 1", None, Exception("the instance went away"))

    def dispose(self) -> None:
        return None


async def drive_filler_disabled(_: Path) -> None:
    config = masked_config()
    providers = dict(built_world(config).agents)
    providers["poet"] = dataclass_replace(providers["poet"], tts=cast(Any, BrokenTts()))
    await build_agent_fillers(config, providers)


def drive_onboarding_key_mismatch(directory: Path) -> None:
    with entered_client(apart(banner_config(), directory)) as client:
        assert client.get(f"/x/{PINNED_KEY[:-1]}X/").status_code == 404


def drive_onboarding_key_unshaped(directory: Path) -> None:
    with entered_client(apart(banner_config(), directory)) as client:
        assert client.get(f"/x/{'A' * 500}/").status_code == 404


def drive_onboarding_banner_off(_: Path) -> None:
    onboarding.log_banner(banner_config(enabled=False).server)


def drive_onboarding_banner_on(_: Path) -> None:
    onboarding.log_banner(banner_config().server)


def drive_activation_complete(directory: Path) -> None:
    with activation_client(apart(unbound_config(), directory)) as client:
        assert activate(client, mac=BOUND_MAC).status_code == 200


def drive_activation_pending(directory: Path) -> None:
    with activation_client(apart(unbound_config(), directory)) as client:
        check_in(client)
        assert activate(client).status_code == 202


def drive_activation_refused_unreadable_body(directory: Path) -> None:
    with activation_client(apart(unbound_config(), directory)) as client:
        check_in(client)
        client.post(
            f"{OTA_PATH}{ACTIVATE_SEGMENT}",
            content=b"not json at all",
            headers={"Device-Id": DEVICE_MAC, "Activation-Version": "2"},
        )


def drive_activation_refused_unknown_algorithm(directory: Path) -> None:
    with activation_client(apart(unbound_config(), directory)) as client:
        challenge = check_in(client)["activation"]["challenge"]
        activate(
            client,
            body={"algorithm": "rot13", "challenge": challenge, "hmac": "00"},
            version="2",
        )


def drive_activation_refused_challenge_mismatch(directory: Path) -> None:
    with activation_client(apart(unbound_config(), directory)) as client:
        check_in(client)
        activate(
            client,
            body={
                "algorithm": "hmac-sha256",
                "challenge": "11:22:33:44:55:66",
                "hmac": "00",
            },
            version="2",
        )


def drive_ota_check_activating(directory: Path) -> None:
    with activation_client(apart(unbound_config(), directory)) as client:
        check_in(client)


def drive_ota_check_agent_not_loaded(directory: Path) -> None:
    config = unbound_config()
    config.devices[NORMALIZED] = ["written-since-boot"]
    with entered_client(apart(config, directory)) as client:
        check_in(client)


def drive_ota_check_no_agent(directory: Path) -> None:
    config = Config(server={"onboarding": {"enabled": False}})
    with ota_client(apart(config, directory)) as client:
        post_system_info(client)


def drive_ota_check_resolved(directory: Path) -> None:
    config = Config(
        providers=MOCK_PROVIDERS,
        agents={"assistant": MOCK_AGENT},
        default_agent="assistant",
    )
    with ota_client(apart(config, directory)) as client:
        post_system_info(client)


def drive_activation_not_offered_unreadable(_: Path) -> None:
    """An unbound device whose bindings answer is a fallback rather than
    an answer, so minting would offer a ticket for a bound board.

    The engine is replaced with one whose every connection fails, which
    is what a database a lookup cannot reach looks like from the view's
    side. Its SQLite-era form overwrote the file with rubbish (#283).
    """
    config = booted(devices={BOUND_MAC: ["assistant"]})
    with TestClient(create_app(config, from_store=True)) as client:
        view = client.app.state.composition.bindings
        view._engine.dispose()
        view._engine = _Unreadable()  # type: ignore[assignment]
        bindings_check_in(client)


def drive_activation_not_offered_refused(directory: Path) -> None:
    """The mint budget lowered to nothing rather than thirty check-ins
    run through the endpoint: what is being driven is the line."""
    config = apart(unbound_config(), directory)
    with patched(onboarding, "MINT_BUDGET", 0), activation_client(config) as client:
        check_in(client)


def drive_ota_request_rejected(directory: Path) -> None:
    with ota_client(apart(Config(), directory)) as client:
        assert post_system_info(client, device_id=None).status_code == 400


def echo_provider(handler: object, **overrides: object) -> OpenAiAsr:
    """The provider on a mock transport, wired as the ASR suite wires
    it."""
    client = AsyncOpenAI(
        api_key="test-key",
        max_retries=0,
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
        ),
    )
    options: dict[str, object] = {
        "model": "gpt-4o-mini-transcribe",
        "api_key": "test-key",
        "client": client,
        "prompt": ECHO_PROMPT,
    }
    options.update(overrides)
    return OpenAiAsr(**options)  # type: ignore[arg-type]


def answering(*texts: str) -> object:
    """A transport that answers each request with the next transcript."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"text": texts[min(len(seen), len(texts)) - 1]})

    return handler


async def drive_echo_skipped(_: Path) -> None:
    asr = echo_provider(answering(ECHO_PROMPT), timeout_s=0.5)
    assert (await asr.transcribe(ONE_SECOND, 16000)).text == ""


async def drive_echo_timed_out(_: Path) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if not seen:
            seen.append(request)
            return httpx.Response(200, json={"text": ECHO_PROMPT})
        raise httpx.ReadTimeout("the deadline came first", request=request)

    assert (await echo_provider(handler).transcribe(ONE_SECOND, 16000)).text == ""


async def drive_echo_confirmed(_: Path) -> None:
    asr = echo_provider(answering(ECHO_PROMPT, ECHO_PROMPT))
    assert (await asr.transcribe(ONE_SECOND, 16000)).text == ""


async def drive_echo_confirmed_empty(_: Path) -> None:
    asr = echo_provider(answering(ECHO_PROMPT, ""))
    assert (await asr.transcribe(ONE_SECOND, 16000)).text == ""


async def drive_echo_recovered(_: Path) -> None:
    asr = echo_provider(answering(ECHO_PROMPT, "Yes, please."))
    assert (await asr.transcribe(ONE_SECOND, 16000)).text == "Yes, please."


async def drive_drain_started(_: Path) -> None:
    await registry_with(FakeSession(), FakeSession()).drain(timeout_s=5)


async def drive_drain_incomplete(_: Path) -> None:
    await registry_with(FakeSession(speaking_for=30)).drain(timeout_s=1.2)


async def drive_drain_finished(_: Path) -> None:
    await registry_with(FakeSession()).drain(timeout_s=5)


async def drive_mcp_connected(_: Path) -> None:
    manager = await mcp_running(mcp_entry())
    await manager.stop()


async def drive_mcp_connect_failed(_: Path) -> None:
    manager = await mcp_running(mcp_entry(command="/nonexistent/mcp-server", args=[]))
    await manager.stop()


async def drive_mcp_stopped(_: Path) -> None:
    """The one way down that is not a warning: a connection that came up
    and was asked to go."""
    manager = await mcp_running(mcp_entry())
    await manager.stop()


async def drive_mcp_call_dropped(_: Path) -> None:
    manager = await mcp_running(mcp_entry())
    try:
        # White-box: the dropped answer is the MCP session's own call
        # raising after the tool was dispatched, which a cooperating
        # server does not do.
        with patched(
            manager._session,
            "call_tool",
            raising(RuntimeError("a message from nowhere near this line")),
        ):
            try:
                await manager.call("tools__secret_word", {})
            except RuntimeError:
                pass
    finally:
        await manager.stop()


async def drive_mcp_dropped(_: Path) -> None:
    """The `mcp_down` beside the dropped call, which is the second half
    of one failure's two stories."""
    await drive_mcp_call_dropped(_)


async def drive_mcp_tool_shadowed(_: Path) -> None:
    servers = McpServers.build(
        mcp_granting(
            {"home": mcp_entry_data(), "home__inside": mcp_entry_data()},
            {"assistant": ["home", "home__inside"]},
        )
    )
    try:
        await servers.start_all()
        servers.tools_for_agent("assistant")
    finally:
        await servers.stop_all()


async def drive_mcp_reload_refused(_: Path) -> None:
    """A reload asked for while one is already running, which is the
    refusal that needs no broken database to provoke."""
    before = mcp_config({"tools": mcp_entry_data()}, {"assistant": ["tools"]})
    servers = await mcp_started(before)
    reloads = McpApplying(servers, before)
    # White-box: a reload refused because one is already running needs
    # two applies overlapping, and the public overlap is a race.
    reloads._applying._running = True
    try:
        await reloads.apply(mcp_reading(before))
    except ReloadInProgressError:
        pass
    finally:
        reloads._applying._running = False
        await servers.stop_all()


async def drive_mcp_reload_applied(_: Path) -> None:
    before = mcp_config({"tools": mcp_entry_data()}, {"assistant": ["tools"]})
    after = mcp_config(
        {"tools": mcp_entry_data(), "extra": mcp_entry_data()},
        {"assistant": ["tools", "extra"]},
    )
    servers = await mcp_started(before)
    try:
        await McpApplying(servers, before).apply(mcp_reading(after))
    finally:
        await servers.stop_all()


def drive_memory_unreadable(directory: Path) -> None:
    memories = MemoryStore(directory)
    corrupt(memories, "poet")
    assert memories.read("poet") == ""


def drive_auth_rejected(directory: Path) -> None:
    with TestClient(create_app(apart(config_with_agent(), directory))) as client:
        try:
            with handshake(client, device_headers(None, DEVICE_MAC)):
                pass
        except WebSocketDisconnect:
            pass


def drive_session_rejected_at_capacity(directory: Path) -> None:
    """The one `session_rejected` the endpoint makes before a session can
    run at all, on the websocket router's own channel."""
    config = apart(config_with_agent(), directory)
    config.server.limits.max_sessions = 1
    with TestClient(create_app(config)) as client:
        with connect(client) as first:
            shake_hands(first)
            try:
                with connect(client):
                    pass
            except WebSocketDisconnect:
                pass


APP = "vinga_server.app"
CAPTURE = "vinga_server.capture"
CONFIG_API = "vinga_server.config.api"
BINDINGS = "vinga_server.device.bindings"
FILLER_BUILD = "vinga_server.filler"
KEYS = "vinga_server.onboarding.keys"
ORIGIN = "vinga_server.onboarding.origin"
POLL = "vinga_server.ota.poll"
REPLY = "vinga_server.ota.reply"
ASR = "vinga_server.providers.openai_asr"
REGISTRY = "vinga_server.registry"
MANAGER = "vinga_server.tools.mcp.manager"
MCP_REGISTRY = "vinga_server.tools.mcp.registry"
RELOAD = "vinga_server.tools.mcp.reload"
MEMORY = "vinga_server.tools.memory"
WS = "vinga_server.ws"

SERVER_DRIVERS: tuple[Driver, ...] = (
    Driver((APP, "_build_composition", 1), drive_capture_enabled, "capture_enabled"),
    Driver((APP, "_build_composition", 2), drive_capture_disabled, "capture_disabled"),
    Driver((CAPTURE, "SessionCapture._disable", 1), drive_capture_failed, "capture_failed"),
    Driver((CAPTURE, "SessionCapture._finish_at_limit", 1), drive_capture_limit, "capture_limit"),
    Driver((CAPTURE, "CaptureStore.prune", 1), drive_capture_pruned, "capture_pruned"),
    Driver((CAPTURE, "CaptureStore.prune", 2), drive_capture_over_budget, "capture_over_budget"),
    Driver((CAPTURE, "CaptureStore.open", 1), drive_capture_declined_unusable, "capture_declined"),
    Driver(
        (CAPTURE, "CaptureStore.open", 2),
        drive_capture_declined_below_floor,
        "capture_declined",
    ),
    Driver(
        (CAPTURE, "CaptureStore.open", 3),
        drive_capture_declined_unopenable,
        "capture_declined",
    ),
    Driver((CAPTURE, "CaptureStore.open", 4), drive_capture_started, "capture_started"),
    Driver((CONFIG_API, "_SanitizedErrors.__call__", 1), drive_api_error, "api_error"),
    Driver((CONFIG_API, "_refusal.handler", 1), drive_api_storage_error, "api_storage_error"),
    Driver(
        (BINDINGS, "DeviceBindings._warn", 1),
        drive_bindings_unreadable,
        "device_bindings_unreadable",
    ),
    Driver((FILLER_BUILD, "build_agent_fillers", 1), drive_filler_disabled, "filler_disabled"),
    Driver((KEYS, "_log_mismatch", 1), drive_onboarding_key_mismatch, "onboarding_key_mismatch"),
    Driver((KEYS, "_log_mismatch", 2), drive_onboarding_key_unshaped, "onboarding_key_unshaped"),
    Driver((ORIGIN, "log_banner", 1), drive_onboarding_banner_off, "onboarding_banner"),
    Driver((ORIGIN, "log_banner", 2), drive_onboarding_banner_on, "onboarding_banner"),
    Driver((POLL, "activate", 1), drive_activation_complete, "activation_complete"),
    Driver((POLL, "activate", 2), drive_activation_pending, "activation_pending"),
    Driver(
        (POLL, "_version_two", 1),
        drive_activation_refused_unreadable_body,
        "activation_refused",
    ),
    Driver(
        (POLL, "_version_two", 2),
        drive_activation_refused_unknown_algorithm,
        "activation_refused",
    ),
    Driver(
        (POLL, "_version_two", 3),
        drive_activation_refused_challenge_mismatch,
        "activation_refused",
    ),
    Driver((REPLY, "check_version", 1), drive_ota_check_activating, "ota_check"),
    Driver((REPLY, "check_version", 2), drive_ota_check_agent_not_loaded, "ota_check"),
    Driver((REPLY, "check_version", 3), drive_ota_check_no_agent, "ota_check"),
    Driver((REPLY, "check_version", 4), drive_ota_check_resolved, "ota_check"),
    Driver(
        (REPLY, "_activation", 1),
        drive_activation_not_offered_unreadable,
        "activation_not_offered",
    ),
    Driver(
        (REPLY, "_activation", 2),
        drive_activation_not_offered_refused,
        "activation_not_offered",
    ),
    Driver((REPLY, "_bad_request", 1), drive_ota_request_rejected, "ota_request_rejected"),
    Driver((ASR, "OpenAiAsr._retry_without_prompt", 1), drive_echo_skipped, "asr_prompt_echo"),
    Driver((ASR, "OpenAiAsr._retry_without_prompt", 2), drive_echo_timed_out, "asr_prompt_echo"),
    Driver((ASR, "OpenAiAsr._retry_without_prompt", 3), drive_echo_confirmed, "asr_prompt_echo"),
    Driver(
        (ASR, "OpenAiAsr._retry_without_prompt", 4),
        drive_echo_confirmed_empty,
        "asr_prompt_echo",
    ),
    Driver((ASR, "OpenAiAsr._retry_without_prompt", 5), drive_echo_recovered, "asr_prompt_echo"),
    Driver((REGISTRY, "SessionRegistry.drain", 1), drive_drain_started, "drain_started"),
    Driver((REGISTRY, "SessionRegistry.drain", 2), drive_drain_incomplete, "drain_incomplete"),
    Driver((REGISTRY, "SessionRegistry.drain", 3), drive_drain_finished, "drain_finished"),
    Driver((MANAGER, "McpServerManager._run", 1), drive_mcp_connected, "mcp_connected"),
    Driver((MANAGER, "McpServerManager._run", 2), drive_mcp_connect_failed, "mcp_down"),
    Driver((MANAGER, "McpServerManager._run", 3), drive_mcp_stopped, "mcp_down"),
    Driver((MANAGER, "McpServerManager._mark_down", 1), drive_mcp_call_dropped, "mcp_call_dropped"),
    Driver((MANAGER, "McpServerManager._mark_down", 2), drive_mcp_dropped, "mcp_down"),
    Driver(
        (MCP_REGISTRY, "McpServers._reachable", 1),
        drive_mcp_tool_shadowed,
        "mcp_tool_shadowed",
    ),
    Driver((RELOAD, "_refused", 1), drive_mcp_reload_refused, "mcp_reload"),
    Driver((RELOAD, "_apply", 1), drive_mcp_reload_applied, "mcp_reload"),
    Driver((MEMORY, "MemoryStore.read", 1), drive_memory_unreadable, "memory_unreadable"),
    Driver((WS, "conversation", 1), drive_auth_rejected, "auth_rejected"),
    Driver((WS, "conversation", 2), drive_session_rejected_at_capacity, "session_rejected"),
)


DRIVERS: tuple[Driver, ...] = STORE_DRIVERS + SESSION_DRIVERS + SERVER_DRIVERS


class Collector(logging.Handler):
    """Every record written on a scoped channel, kept whole."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@contextmanager
def listening() -> Iterator[Collector]:
    """Attached to the scoped channels themselves rather than to the
    root, so a driver's incidental logging elsewhere cannot reach the
    capture."""
    collector = Collector()
    channels = [logging.getLogger(one) for one in SCOPE]
    levels = [channel.level for channel in channels]
    for channel in channels:
        channel.addHandler(collector)
        channel.setLevel(logging.DEBUG)
    try:
        yield collector
    finally:
        for channel, level in zip(channels, levels, strict=True):
            channel.removeHandler(collector)
            channel.setLevel(level)


def payload(record: logging.LogRecord) -> dict[str, Any]:
    """One record's own fields: what the JSON formatter writes beside
    the standard ones, which is what a tap reads."""
    return {
        key: held for key, held in vars(record).items() if key not in _STANDARD_ATTRIBUTES
    }


def shape(record: logging.LogRecord) -> dict[str, Any]:
    """One record in the dimensions a consumer sees.

    Values are deliberately absent: the drivers work with real material,
    a planted API token among it, and what these dimensions are for is
    holding a record to its declaration. Argument types went with the
    committed capture (#241): the suite compares the template itself,
    which the declaration derives its argument order from, so arity is
    subsumed, and which field each position renders is
    `docs/reference/events.md`'s pin now.
    """
    return {
        "channel": record.name,
        "level": record.levelno,
        "template": record.msg,
        "fields": sorted(payload(record)),
        "event": getattr(record, "event", None),
    }


@dataclass(frozen=True)
class Run:
    """One drive of every path: everything the scoped channels said
    while each driver ran, and the half of it that driver owns.

    `said` is filtered by nothing, which is what a claim about anything
    OTHER than the eighty-one typed paths has to be made from. It holds
    two populations `kept` does not: the neighbouring paths a driver
    crosses on its way to its own decision, three times as many records
    as the drivers keep, and the untyped records, which carry no `event`
    attribute at all and are therefore exactly what the filter removes.
    """

    said: dict[str, list[logging.LogRecord]]

    @property
    def kept(self) -> dict[str, list[logging.LogRecord]]:
        """Each driver's own records: the ones carrying the event that
        driver names, for the reason `Driver` gives.

        Derived from `said` rather than collected beside it, so the two
        cannot come to disagree about what one run produced.
        """
        wanted = {driver.key: driver.event for driver in DRIVERS}
        return {
            key: [one for one in records if getattr(one, "event", None) == wanted[key]]
            for key, records in self.said.items()
        }


def driven() -> Run:
    """Every driver run, in declaration order, with everything the
    scoped channels said while it ran.

    Whole records rather than shapes, because a claim about what a
    payload HOLDS cannot be made from the keys `shape()` keeps;
    `captured()` takes a run already made, so a suite wanting both pays
    for one.
    """
    said: dict[str, list[logging.LogRecord]] = {}
    for driver in DRIVERS:
        with tempfile.TemporaryDirectory(prefix="vinga-drivers-") as directory:
            with listening() as collector:
                answer = driver.drive(Path(directory))
                if inspect.isawaitable(answer):
                    asyncio.run(answer)
            said[driver.key] = list(collector.records)
            # The store, emptied between drivers. Two of them open a
            # session of the same name, which one database cannot hold
            # at once; the throwaway directory above used to keep them
            # apart and no longer holds a database (#283).
            clear_store()
    return Run(said=said)


def captured(
    produced: dict[str, list[logging.LogRecord]],
) -> dict[str, list[dict[str, Any]]]:
    """What every path produced, in the dimensions `shape()` keeps.

    Takes a run rather than making one: a suite that also wants the
    whole records pays for a single drive and reduces it here.
    """
    return {key: [shape(one) for one in records] for key, records in produced.items()}
