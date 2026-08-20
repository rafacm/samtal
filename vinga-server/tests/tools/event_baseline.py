"""What every emit path produces, recorded so a change to it is loud.

The #143 wire baseline, applied to log records. A conversion milestone's
whole claim is that the surface did not move, and the honest way to make
that claim is to record what every path produces, convert, record again,
and show the two are the same file. So this drives each of the
eighty-one paths and captures the five dimensions a consumer sees: the
channel, the numeric level, the unrendered template, the TYPES of the
arguments behind it, and the payload's keys.

Types rather than values for the arguments, and keys rather than values
for the payload, because a baseline is about shape: a temporary
directory and a class name move between runs, and a file that changed
every run would be a file nobody reads. What the values are is the
golden inventory's question and the behavioral suites'.

**The completeness claim comes from the catalog, not from this file.** A
runtime harness proves only what it executes, so on its own a set of
drivers proves whatever it happens to run. Every variant the catalog
declares is constructible, and therefore directly drivable, so
`tests/unit/test_event_baseline.py` holds all eighty-five of them to
being produced by some driver's run, and a declaration nothing can
produce fails the lane. Beside it, the smaller claim these drivers can
give themselves: one driver per identity, eighty-one of them, and every
record a driver keeps is the event that driver names.

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
path's records too. Regenerate the committed file deliberately:

    uv run python -m tests.tools.event_baseline

The drivers reach into the store and the capture the way the pin suites
they came from do: a writer parked on its gate, an engine that raises, a
free-space reading that refuses, a clock the harness chose. Those
reach-ins are the price of driving a failure path deterministically, and
they are the same ones `test_conversations_store.py` pays.
"""

import asyncio
import datetime as dt
import inspect
import json
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
from starlette.websockets import WebSocketDisconnect

import vinga_server
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
)
from tests.support.registry import (
    AGENT,
    BINDINGS_DEVICE_MAC,
    STAGES,
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
from vinga_server.conversations import store as store_module
from vinga_server.conversations.records import ToolInvocation, TurnRecord
from vinga_server.conversations.store import ConversationStore
from vinga_server.device.bindings import DeviceAgents, DeviceBindings
from vinga_server.device.session import DeviceSession
from vinga_server.events.catalog import CHANNELS
from vinga_server.filler import build_agent_fillers
from vinga_server.logs import _STANDARD_ATTRIBUTES
from vinga_server.ota import ACTIVATE_SEGMENT, OTA_PATH
from vinga_server.providers import AsrResult, Usage, build_agent_providers
from vinga_server.providers.openai_asr import OpenAiAsr
from vinga_server.runtime.pipeline import bespoke_runtime_factory
from vinga_server.tools.mcp import McpServers
from vinga_server.tools.mcp.reload import ReloadInProgressError
from vinga_server.tools.memory import MemoryStore

# The channels this baseline covers: what a record has to ride to be
# captured at all.
SCOPE: tuple[str, ...] = CHANNELS

# And the modules whose statically known emit sites it must claim, which
# is a different list because a channel is not a file: four modules emit
# on the one session channel, which is the whole reason that channel is
# named rather than derived from `__name__`.
#
# M3 widened both to the whole surface: every channel this server
# speaks on, and every module that emits on one.
MODULES: tuple[str, ...] = (
    "vinga_server.conversations.store",
    "vinga_server.device.session",
    "vinga_server.runtime.pipeline",
    "vinga_server.runtime.turntaking",
    "vinga_server.runtime.filler_runner",
    "vinga_server.app",
    "vinga_server.capture",
    "vinga_server.config.api",
    "vinga_server.device.bindings",
    "vinga_server.filler",
    "vinga_server.onboarding.keys",
    "vinga_server.onboarding.origin",
    "vinga_server.ota.poll",
    "vinga_server.ota.reply",
    "vinga_server.providers.openai_asr",
    "vinga_server.registry",
    "vinga_server.tools.mcp.manager",
    "vinga_server.tools.mcp.registry",
    "vinga_server.tools.mcp.reload",
    "vinga_server.tools.memory",
    "vinga_server.ws",
)

COMMITTED = (
    Path(__file__).resolve().parent.parent / "unit" / "data" / "event-baseline.json"
)

PACKAGE = Path(vinga_server.__file__).parent

# The four emitter methods an untyped site calls, which is how that
# shape is recognized; the typed shape is `emit` and is recognized by
# name alone.
LEVEL_METHODS = frozenset({"debug", "info", "warning", "error"})

TYPED_METHOD = "emit"

# How a session-scoped emitter is reached, spelled as the conformance
# walk spells it. Nothing in scope uses it yet; M2 is where it starts
# to.
SESSION_RECEIVER = "self._events"

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


def a_turn() -> TurnRecord:
    return TurnRecord(
        at=101.0,
        agent="sam",
        heard="hello there",
        reply="Hi.",
        tools=(
            ToolInvocation(position=0, source="builtin", name="remember", result="ok"),
        ),
    )


def drive_enabled(directory: Path) -> None:
    """`start()` says this server is recording."""
    store = ConversationStore(directory)
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
    store = ConversationStore(directory, gate=gate)
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
    store = ConversationStore(directory, gate=gate, retention_days=0)
    try:
        store.start()
        store.open_session("alpha", 100.0, a_manifest(NOW))
        gate.wait()
        gate.let_through()
        store.record_turn("alpha", a_turn())
        gate.wait()
        # White-box, as in `test_conversations_store.py`: an accepted
        # write that the database then refuses is only reachable with a
        # broken engine.
        store._engine = Raising()  # type: ignore[assignment]
        gate.open_forever()
    finally:
        store.stop()


def drive_prune_failed(directory: Path) -> None:
    """Retention that could not delete."""
    store = ConversationStore(directory, retention_days=90, now=lambda: NOW)
    try:
        store._engine = Raising()  # type: ignore[assignment]
        store._prune()
    finally:
        store.stop()


def drive_pruned(directory: Path) -> None:
    """Retention that did: two sessions seeded old enough to go."""
    seeding = ConversationStore(directory, retention_days=0, now=lambda: NOW)
    seeding.start()
    for name, age in (("old-one", 200), ("old-two", 300)):
        seeding.open_session(name, 100.0, a_manifest(NOW - dt.timedelta(days=age)))
        seeding.record_turn(name, a_turn())
        seeding.close_session(name, duration_s=5.0, reason="client")
    seeding.stop()

    pruning = ConversationStore(directory, retention_days=90, now=lambda: NOW)
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
# Ported from the prose pin suite this milestone retires: those tests
# drove every one of these paths onto its own decision, and driving is
# exactly what a baseline needs. What they asserted about the record
# moves to the golden inventory and to the capture below; how they
# reached the record is here.
#
# Some drivers run more than one scenario, because a site can emit more
# than one variant: `llm_round` says one thing about a provider the
# registry built out of a configured entry and another about a provider
# it never built, from the same call. One driver per PATH is the
# harness's identity rule; how many shapes that path can produce is the
# path's business.

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
    rejections are driven without a database behind them."""

    def __init__(self, resolution: DeviceAgents) -> None:
        self._resolution = resolution

    async def resolve(self, mac: str) -> DeviceAgents:
        return self._resolution


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
    config: Config, device_id: str, resolution: DeviceAgents | None = None
) -> None:
    """One connection that never becomes a session."""
    generations = world(config)
    factory = bespoke_runtime_factory(
        generations, build_agent_providers(config), McpServers({}), None, {}
    )
    session = DeviceSession(
        cast(Any, TurnedAwaySocket(device_id)),
        generations,
        factory,
        bindings=None if resolution is None else cast(Any, ScriptedBindings(resolution)),
    )
    await session.run()


def apart(config: Config, directory: Path) -> Config:
    """Where this driver's app keeps its configuration database.

    A driver that builds an app migrates one, and the next app to find a
    migrated database resolves its device bindings from it rather than
    from the configuration it was built with, which turns the session
    after into a rejection. One directory per driver is what keeps the
    drivers independent of the order they run in.
    """
    config.server.database.dir = directory
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


def unregistered(llm: Any, agent: str = "poet", mac: str = POET_MAC) -> Any:
    """A session whose LLM the provider registry never built, so the
    events it emits name no configured entry: the variant beside every
    provider event that says less rather than guessing."""
    config = base_config()
    providers = build_agent_providers(config)
    built = providers[agent]
    providers[agent] = type(built)(llm=llm, asr=built.asr, tts=built.tts, vad=built.vad)
    session = device_session(config, mac, providers)
    session.websocket = cast(Any, RecordingSocket())
    return session


async def failing_reply(stage: str, provider: Any, watch: Any = None) -> Any:
    """One reply against a provider that fails, and the session it ran
    in.

    `watch` attaches a consumer before the reply starts, which is what
    the privacy suite next door needs and what a baseline driver has no
    use for: a claim about what reaches a tap has to be asserted at the
    tap rather than inferred from the log.
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
    await turned_away(
        config_with_agent(), DEVICE_MAC, DeviceAgents(agents=(), unloaded=("poet",))
    )


async def drive_no_agent(_: Path) -> None:
    await turned_away(config_with_agent(), DEVICE_MAC, DeviceAgents(agents=()))


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
    llm = StallingLlm(delays=[STALL_S, 0.0])
    session = session_for(watchdog_config(), POET_MAC, {"poet": cast(Any, llm)})
    llm.identity = replace(llm.identity, model=MODEL)  # type: ignore[attr-defined]
    await run_reply(session, "are you there")
    await run_reply(
        unregistered(StallingLlm(delays=[STALL_S, 0.0]), mac=POET_MAC), "again"
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


async def drive_filler_skipped_for_speech(_: Path) -> None:
    session = await masked_session(masked_config(), POET_MAC, {"poet": StallingLlm([STALL_S])})
    start_reply(session, UTTERANCE)
    await asyncio.sleep(DELAY_MS / 1000 / 3)
    turn_taking(session).endpointer.feed(SPEECH)
    await reply_in_flight(session)


async def drive_filler_skipped_for_a_barge_in(_: Path) -> None:
    session = await masked_session(masked_config(), POET_MAC, {"poet": StallingLlm([STALL_S])})
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
    session = await masked_session(masked_config(), POET_MAC, {"poet": StallingLlm([STALL_S])})
    await drive_reply(session, UTTERANCE)


EDGE = "vinga_server.device.session"
PIPELINE = "vinga_server.runtime.pipeline"
TURNTAKING = "vinga_server.runtime.turntaking"
FILLER = "vinga_server.runtime.filler_runner"

SESSION_DRIVERS: tuple[Driver, ...] = (
    Driver((EDGE, "DeviceSession._watch_for_idle", 1), drive_session_idle, "session_idle"),
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
# paths onto its own decision. Driving is exactly what a baseline needs,
# so the drivers come from there rather than being invented beside it.
#
# The monkeypatching those tests do with a fixture is done by hand here,
# saved and restored, because a driver runs outside pytest when the
# baseline is regenerated.

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

    `monkeypatch` is a fixture, and half of these drivers run outside
    pytest when the file is regenerated.
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
    api = build_api(API_TOKEN, directory / "db")

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


def drive_bindings_snapshot_only(directory: Path) -> None:
    config = Config(
        server={"database": {"dir": str(directory / "nothing")}},
        providers={stage: {"mock": {"type": "mock"}} for stage in STAGES},
        agents={"assistant": dict(AGENT)},
        devices={BINDINGS_DEVICE_MAC: ["assistant"]},
    )
    DeviceBindings.open(config).dispose()


def drive_bindings_unreadable(directory: Path) -> None:
    config = booted(directory, devices={BINDINGS_DEVICE_MAC: ["assistant"]})
    bindings = DeviceBindings.open(config)
    try:
        (directory / "vinga.db").write_bytes(b"this is not a database")
        bindings.agents_for(BINDINGS_DEVICE_MAC)
    finally:
        bindings.dispose()


async def drive_filler_disabled(_: Path) -> None:
    config = masked_config()
    providers = build_agent_providers(config)
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


def drive_activation_not_offered_unreadable(directory: Path) -> None:
    """An unbound device whose bindings answer is a fallback rather than
    an answer, so minting would offer a ticket for a bound board."""
    config = booted(directory, devices={BOUND_MAC: ["assistant"]})
    with TestClient(create_app(config)) as client:
        (directory / "vinga.db").write_bytes(b"this is not a database")
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
        (BINDINGS, "DeviceBindings.open", 1),
        drive_bindings_snapshot_only,
        "device_bindings_snapshot_only",
    ),
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


def shape(record: logging.LogRecord) -> dict[str, Any]:
    """One record in the dimensions a consumer sees."""
    return {
        "channel": record.name,
        "level": record.levelno,
        "template": record.msg,
        "argument_types": [type(one).__name__ for one in (record.args or ())],
        "fields": sorted(
            key for key in vars(record) if key not in _STANDARD_ATTRIBUTES
        ),
        "event": getattr(record, "event", None),
    }


def captured() -> dict[str, list[dict[str, Any]]]:
    """Every driver run, in declaration order, with what its own path
    produced.

    Filtered to the event each driver says its path emits, for the
    reason `Driver` gives.
    """
    baseline: dict[str, list[dict[str, Any]]] = {}
    for driver in DRIVERS:
        with tempfile.TemporaryDirectory(prefix="vinga-baseline-") as directory:
            with listening() as collector:
                answer = driver.drive(Path(directory))
                if inspect.isawaitable(answer):
                    asyncio.run(answer)
            baseline[driver.key] = [
                shape(one)
                for one in collector.records
                if getattr(one, "event", None) == driver.event
            ]
    return baseline


def rendered(baseline: dict[str, list[dict[str, Any]]]) -> str:
    return json.dumps(baseline, indent=2) + "\n"


def committed() -> dict[str, list[dict[str, Any]]]:
    return json.loads(COMMITTED.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


if __name__ == "__main__":  # pragma: no cover - the regeneration path
    # The run's environment, set the way a lane sets it: an app refuses
    # to boot without its two secrets, the emitters have to stay strict,
    # and a database needs somewhere writable. `conftest.py` is where all
    # of that is decided, so it is imported rather than restated.
    import tests.conftest  # noqa: F401

    COMMITTED.parent.mkdir(parents=True, exist_ok=True)
    COMMITTED.write_text(rendered(captured()), encoding="utf-8")
    print(f"wrote {COMMITTED}")
