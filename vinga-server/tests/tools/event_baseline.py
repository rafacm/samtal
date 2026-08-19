"""What a converted emit path produced before it was converted.

The #143 wire baseline, applied to log records. A conversion milestone's
whole claim is that the surface did not move, and the honest way to make
that claim is to record what every path produces, convert, record again,
and show the two are the same file. So this drives each emit path in
scope and captures the five dimensions a consumer sees: the channel, the
numeric level, the unrendered template, the TYPES of the arguments
behind it, and the payload's keys.

Types rather than values for the arguments, and keys rather than values
for the payload, because a baseline is about shape: a temporary
directory and a class name move between runs, and a file that changed
every run would be a file nobody reads. What the values are is the
golden inventory's question and the behavioral suites'.

**The path list is not self-claimed.** A runtime harness proves only
what it executes, so the obligation comes from a static reading of the
source instead. `sites()` below walks the scoped modules and answers
every emit path in them, in BOTH of the shapes a path can have: the
untyped `events.warning(..., event=...)` call and the typed
`events.emit(lambda: Variant(...))` thunk. The drivers' identities must
equal that walk's exactly, in both directions, so a sixth path with no
driver and a driver naming no path each fail the same way, before a
conversion and after it.

Reading both shapes is the correction PR #217's review forced. The first
version of this borrowed the conformance suite's walk, which recognizes
only the untyped shape; once the store converted, that walk found zero
sites in scope while the harness claimed five, and an obligation of the
form "every one of nothing is claimed" is no obligation at all.

The walk also reads which event each path emits, which is the `event=`
keyword for an untyped site and the declaration behind the constructed
variant for a typed one, so the test can hold each path to producing its
own record rather than to producing something.

`tests/unit/test_event_baseline.py` holds these obligations, proves the
walk on planted sources, and compares the capture with the committed
file. Regenerate it deliberately:

    uv run python -m tests.tools.event_baseline

The drivers reach into the store the way the pin suite they replace
does: a writer parked on its gate, an engine that raises, a clock the
harness chose. Those reach-ins are the price of driving a failure path
deterministically, and they are the same ones `test_conversations_store.py`
pays.
"""

import ast
import asyncio
import datetime as dt
import inspect
import json
import logging
import sys
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient

import vinga_server
from tests.support.configs import (
    BOTH_MAC,
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
)
from tests.support.providers import (
    STALL_S,
    ConfirmingAsr,
    GatedAsr,
    ScriptedEndpointer,
    ScriptedLlm,
    StallingLlm,
    Unreachable,
)
from tests.support.sessions import (
    Gate,
    _nothing,
    call,
    device_session,
    masked_session,
    realtime_session,
    run_reply,
    session_for,
)
from tests.support.sockets import RecordingSocket
from tests.support.wire import (
    connect,
    listen_realtime,
    say_something,
    shake_hands,
    speech_pcm,
    wait_for_close,
)
from vinga_server.app import create_app
from vinga_server.config import Config
from vinga_server.conversations import store as store_module
from vinga_server.conversations.records import ToolInvocation, TurnRecord
from vinga_server.conversations.store import ConversationStore
from vinga_server.device.bindings import DeviceAgents
from vinga_server.device.session import DeviceSession
from vinga_server.events import catalog as catalog_module
from vinga_server.events.catalog import declaration_of
from vinga_server.logs import _STANDARD_ATTRIBUTES
from vinga_server.providers import AsrResult, Usage, build_agent_providers
from vinga_server.runtime.pipeline import bespoke_runtime_factory
from vinga_server.tools.mcp import McpServers
from vinga_server.tools.memory import MemoryStore

# The channels this baseline covers: what a record has to ride to be
# captured at all.
SCOPE: tuple[str, ...] = (
    "vinga_server.conversations.store",
    "vinga_server.session",
)

# And the modules whose statically known emit sites it must claim, which
# is a different list because a channel is not a file: four modules emit
# on the one session channel, which is the whole reason that channel is
# named rather than derived from `__name__`.
#
# M3 widens both as the remaining server channels convert. The one
# `session_rejected` variant that rides `vinga_server.ws` is outside
# this scope deliberately: its module's other path is still untyped, and
# its record stays pinned by `test_server_event_pins.py` until M3
# converts that channel and brings it in here.
MODULES: tuple[str, ...] = (
    "vinga_server.conversations.store",
    "vinga_server.device.session",
    "vinga_server.runtime.pipeline",
    "vinga_server.runtime.turntaking",
    "vinga_server.runtime.filler_runner",
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


# --- the static reading, which is what makes the drivers a claim ------


@dataclass(frozen=True)
class Site:
    """One emit path as the source shows it, and the event it emits.

    `module`, `function` and `ordinal` are the conformance walk's own
    identity, so a driver's key means the same thing before and after
    its path converts. Deliberately not a line number, for the reason
    that walk gives: a line number churns with every edit above it.
    """

    module: str
    function: str
    ordinal: int
    event: str

    @property
    def identity(self) -> tuple[str, str, int]:
        return (self.module, self.function, self.ordinal)

    def __str__(self) -> str:
        return f"{self.module}:{self.function} #{self.ordinal} ({self.event})"


def emitter_names(tree: ast.AST) -> frozenset[str]:
    """What this module reaches its emitter through.

    A module builds its own with `events = ServerEvents(__name__)`, or a
    submodule takes its package's with `from . import events`. Reading
    the binding rather than accepting any `.emit(` is what keeps a tap's
    own `emit(emission)` out of the inventory: a tap is not the module's
    emitter, whatever it is called.
    """
    names: set[str] = {SESSION_RECEIVER}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            called = node.value.func
            if isinstance(called, ast.Name) and called.id == "ServerEvents":
                names |= {
                    target.id
                    for target in node.targets
                    if isinstance(target, ast.Name)
                }
        if isinstance(node, ast.ImportFrom) and node.level and node.module is None:
            names |= {alias.asname or alias.name for alias in node.names}
    return frozenset(names)


def _declared_by(name: str) -> str | None:
    """The event a catalog variant belongs to, or None where the name is
    not a variant at all."""
    found = getattr(catalog_module, name, None)
    if isinstance(found, type) and issubclass(found, catalog_module.Variant):
        return declaration_of(found).name
    return None


def _chosen_in(tree: ast.AST, name: str) -> frozenset[str]:
    """The events a variant-choosing function in this module can emit.

    Some paths pick their shape rather than knowing it: `tool_call` says
    one thing about a builtin, another about an MCP entry and a third
    about a call this surface may not name, and the choice is a branch
    the emitting module owns. So the thunk names a function, and the
    function's own body is read for the variants it constructs.

    One level and no further, deliberately: a chooser that reached
    through another chooser would be a path whose event this walk could
    only guess at, and guessing is what the assertion exists to
    prevent.
    """
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if node.name != name:
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name):
                declared = _declared_by(inner.func.id)
                if declared is not None:
                    found.add(declared)
    return frozenset(found)


def _event_of(module: str, enclosing: str, tree: ast.AST, node: ast.Call) -> str:
    """Which event one call emits.

    An untyped call says so in its `event=` keyword. A typed one says so
    by the variant it constructs, or, where it constructs one of
    several, by the function that chooses between them. A shape none of
    those reads is refused rather than skipped: a path the walk cannot
    read is a path the inventory would silently lose.
    """
    where = f"{module}:{enclosing}"
    named = [keyword for keyword in node.keywords if keyword.arg == "event"]
    if named:
        return str(ast.literal_eval(named[0].value))
    if len(node.args) != 1 or isinstance(node.args[0], ast.Lambda) is False:
        raise AssertionError(f"{where}: an emit that is not a construction thunk")
    body = node.args[0].body  # type: ignore[attr-defined]
    if not isinstance(body, ast.Call) or not isinstance(body.func, ast.Name):
        raise AssertionError(f"{where}: a thunk that does not construct one variant")
    declared = _declared_by(body.func.id)
    if declared is not None:
        return declared
    chosen = _chosen_in(tree, body.func.id)
    if len(chosen) != 1:
        raise AssertionError(
            f"{where}: {body.func.id} constructs {len(chosen)} events rather than one"
        )
    return next(iter(chosen))


class _Sites(ast.NodeVisitor):
    """Every emit path in one module, in source order, numbered within
    its enclosing scope across BOTH shapes.

    Across both deliberately. Numbering the shapes separately would give
    two paths in one function the same ordinal while a module was half
    converted, which is the one moment the identity has to stay stable.
    """

    def __init__(self, module: str, tree: ast.AST) -> None:
        self.module = module
        self.tree = tree
        self.receivers = emitter_names(tree)
        self.stack: list[str] = []
        self.ordinals: dict[str, int] = {}
        self.found: list[Site] = []

    def visit_FunctionDef(self, node: ast.AST) -> None:
        self.stack.append(node.name)  # type: ignore[attr-defined]
        self.generic_visit(node)
        self.stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef
    visit_ClassDef = visit_FunctionDef

    def visit_Call(self, node: ast.Call) -> None:
        function = node.func
        if isinstance(function, ast.Attribute) and self._is_emit(function, node):
            enclosing = ".".join(self.stack)
            self.ordinals[enclosing] = self.ordinals.get(enclosing, 0) + 1
            self.found.append(
                Site(
                    module=self.module,
                    function=enclosing,
                    ordinal=self.ordinals[enclosing],
                    event=_event_of(self.module, enclosing, self.tree, node),
                )
            )
        self.generic_visit(node)

    def _is_emit(self, function: ast.Attribute, node: ast.Call) -> bool:
        if ast.unparse(function.value) not in self.receivers:
            return False
        if function.attr == TYPED_METHOD:
            return True
        return function.attr in LEVEL_METHODS and any(
            keyword.arg == "event" for keyword in node.keywords
        )


def sites_in(module: str, source: str) -> tuple[Site, ...]:
    """Every emit path in one module's text. Separate from `sites()` so
    the walk can be run over a planted source and proved."""
    tree = ast.parse(source)
    walk = _Sites(module, tree)
    walk.visit(tree)
    return tuple(walk.found)


def sites() -> tuple[Site, ...]:
    """Every emit path in the scoped modules, in source order."""
    found: list[Site] = []
    for module in MODULES:
        path = PACKAGE.parent / f"{module.replace('.', '/')}.py"
        if not path.exists():
            path = PACKAGE.parent / module.replace(".", "/") / "__init__.py"
        found += sites_in(module, path.read_text(encoding="utf-8"))
    return tuple(found)


class Raising:
    """An engine whose every transaction fails, so a write and a prune
    can be made to fail on purpose."""

    def begin(self) -> Any:
        raise RuntimeError("a failure the harness planted")

    def dispose(self) -> None:
        return None


@dataclass(frozen=True)
class Driver:
    """One emit path, and what makes it fire.

    `identity` is the conformance walk's own: module, enclosing
    function, and which emit call within it. Deliberately not a line
    number, for the reason that walk gives: a line number churns with
    every edit above it.

    `drive` may be a coroutine function. A conversation only exists
    inside a loop, so most of the session channel's paths are reached
    through one; `captured()` runs those in a loop of their own.
    """

    identity: tuple[str, str, int]
    drive: Callable[[Path], Any]

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
    Driver((MODULE, "ConversationStore.start", 1), drive_enabled),
    Driver((MODULE, "ConversationStore.record_event", 1), drive_dropped),
    Driver((MODULE, "ConversationStore._failed", 1), drive_write_failed),
    Driver((MODULE, "ConversationStore._prune", 1), drive_prune_failed),
    Driver((MODULE, "ConversationStore._prune", 2), drive_pruned),
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
    factory = bespoke_runtime_factory(
        config, build_agent_providers(config), McpServers({}), None, {}
    )
    session = DeviceSession(
        cast(Any, TurnedAwaySocket(device_id)),
        config,
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

    session = session_for(base_config(), POET_MAC, {"poet": ScriptedLlm(["One sentence."])})
    session.runtime._providers = replace(
        session.runtime._providers, **{stage: cast(Any, provider)}
    )
    session.websocket = cast(Any, TextSink())
    session._mac = POET_MAC
    session.send_audio = _nothing  # type: ignore[method-assign]
    if watch is not None:
        session._events.attach(watch)
    await session.runtime._reply(UTTERANCE)
    return session


async def speaking_reply(config: Config, asr: Any) -> Any:
    """A session whose reply is past its own ASR and already speaking,
    which is where the last two barge-in gates are reached from."""
    session, socket = realtime_session(config, asr)
    session.runtime._turntaking.endpointer = ScriptedEndpointer(speech_ms=600)
    session.runtime._reply_task = asyncio.create_task(
        session.runtime._reply(speech_pcm(600))
    )
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
    await session.runtime._reply(UTTERANCE)
    await unregistered(ScriptedLlm(["Two words."])).runtime._reply(UTTERANCE)


async def drive_provider_failed(_: Path) -> None:
    await failing_reply("asr", Unreachable("asr", ConnectionRefusedError("no route")))
    await failing_reply("asr", Failing(ConnectionRefusedError("no route")))


def drive_prompt_assembled(_: Path) -> None:
    session_for(base_config(), POET_MAC)


async def drive_heard(_: Path) -> None:
    await speaking_session({"poet": ScriptedLlm(["Two words."])}).runtime._reply(UTTERANCE)


async def drive_replied(_: Path) -> None:
    await speaking_session({"poet": ScriptedLlm(["Two words."])}).runtime._reply(UTTERANCE)


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
    session.runtime._turntaking.endpointer = ScriptedEndpointer(speech_ms=600)
    session.runtime._turntaking._utterance = bytearray(speech_pcm(320))
    await session.runtime._turntaking.finish_utterance(endpointed=True)
    await asyncio.sleep(0.05)
    session.runtime._turntaking._utterance = bytearray(speech_pcm(320))
    await session.runtime._turntaking.finish_utterance()
    asr.release.set()
    await session.runtime._reply_task


async def drive_barge_in_under_the_floor(_: Path) -> None:
    asr = GatedAsr()
    session, _socket = realtime_session(config_with_agent(), asr)
    session.runtime._turntaking.endpointer = ScriptedEndpointer(speech_ms=600)
    session.runtime._turntaking._utterance = bytearray(speech_pcm(320))
    await session.runtime._turntaking.finish_utterance(endpointed=True)
    await asyncio.sleep(0.05)
    session.runtime._turntaking.endpointer = ScriptedEndpointer(speech_ms=100)
    session.runtime._turntaking._utterance = bytearray(speech_pcm(320))
    await session.runtime._turntaking.finish_utterance(endpointed=True)
    asr.release.set()
    await session.runtime._reply_task


async def drive_barge_in_merged(_: Path) -> None:
    asr = GatedAsr()
    session, _socket = realtime_session(config_with_agent(), asr)
    session.runtime._turntaking.endpointer = ScriptedEndpointer(speech_ms=600)
    session.runtime._turntaking._utterance = bytearray(speech_pcm(320))
    await session.runtime._turntaking.finish_utterance(endpointed=True)
    await asyncio.sleep(0.05)
    session.runtime._turntaking._utterance = bytearray(speech_pcm(480))
    await session.runtime._turntaking.finish_utterance(endpointed=True)
    asr.release.set()
    await session.runtime._reply_task


async def drive_barge_in_in_the_refractory_window(_: Path) -> None:
    config = config_with_agent(
        llm_reply="Hold the thought while this sentence finishes playing out loud.",
        server={"barge_in_refractory_ms": 100_000},
    )
    asr = ConfirmingAsr(AsrResult(text="stop"))
    asr.release.set()
    session = await speaking_reply(config, asr)
    session.runtime._turntaking._utterance = bytearray(speech_pcm(600))
    await session.runtime._turntaking.finish_utterance(endpointed=True)
    await session.runtime._reply_task


async def drive_barge_in_without_a_transcript(_: Path) -> None:
    config = config_with_agent(
        llm_reply="Hold the thought while this sentence finishes playing out loud.",
        server={"barge_in_refractory_ms": 0},
    )
    asr = ConfirmingAsr(AsrResult(text=""))
    asr.release.set()
    session = await speaking_reply(config, asr)
    session.runtime._turntaking._utterance = bytearray(speech_pcm(600))
    await session.runtime._turntaking.finish_utterance(endpointed=True)
    await session.runtime._reply_task


async def drive_barge_in_confirmed(_: Path) -> None:
    """The gate's own cancel, which unlike the manual one fires while
    the reply is speaking and therefore carries speaking_ms."""
    config = config_with_agent(
        llm_reply="Answering {text}.", server={"barge_in_refractory_ms": 0}
    )
    asr = ConfirmingAsr(AsrResult(text="stop and listen"))
    asr.release.set()
    session = await speaking_reply(config, asr)
    session.runtime._turntaking._utterance = bytearray(speech_pcm(600))
    await session.runtime._turntaking.finish_utterance(endpointed=True)
    await session.runtime._reply_task


# runtime/filler_runner.py


async def drive_filler_skipped_for_speech(_: Path) -> None:
    session = await masked_session(masked_config(), POET_MAC, {"poet": StallingLlm([STALL_S])})
    session.runtime._reply_task = asyncio.create_task(session.runtime._reply(UTTERANCE))
    await asyncio.sleep(DELAY_MS / 1000 / 3)
    session.runtime._turntaking.endpointer.feed(SPEECH)
    await session.runtime._reply_task


async def drive_filler_skipped_for_a_barge_in(_: Path) -> None:
    session = await masked_session(masked_config(), POET_MAC, {"poet": StallingLlm([STALL_S])})
    session.runtime._reply_task = asyncio.create_task(session.runtime._reply(UTTERANCE))
    await asyncio.sleep(DELAY_MS / 1000 / 3)
    session.runtime._turntaking._pause_output()
    await asyncio.sleep(DELAY_MS / 1000)
    session.runtime._turntaking._resume_output()
    await session.runtime._reply_task


async def drive_filler_played(_: Path) -> None:
    session = await masked_session(masked_config(), POET_MAC, {"poet": StallingLlm([STALL_S])})
    await session.runtime._reply(UTTERANCE)


EDGE = "vinga_server.device.session"
PIPELINE = "vinga_server.runtime.pipeline"
TURNTAKING = "vinga_server.runtime.turntaking"
FILLER = "vinga_server.runtime.filler_runner"

SESSION_DRIVERS: tuple[Driver, ...] = (
    Driver((EDGE, "DeviceSession._watch_for_idle", 1), drive_session_idle),
    Driver((EDGE, "DeviceSession.run", 1), drive_bad_device_id),
    Driver((EDGE, "DeviceSession.run", 2), drive_agent_not_loaded),
    Driver((EDGE, "DeviceSession.run", 3), drive_no_agent),
    Driver((EDGE, "DeviceSession.run", 4), drive_session_open),
    Driver((EDGE, "DeviceSession.run", 5), drive_session_limit),
    Driver((EDGE, "DeviceSession.run", 6), drive_session_closed),
    Driver((EDGE, "DeviceSession.send_audio", 1), drive_speaking_started),
    Driver((PIPELINE, "PipelineRuntime._watchdog_stream", 1), drive_llm_retry),
    Driver((PIPELINE, "PipelineRuntime._llm_round_done", 1), drive_llm_round),
    Driver((PIPELINE, "PipelineRuntime._provider_failed", 1), drive_provider_failed),
    Driver((PIPELINE, "PipelineRuntime._prompt_assembled", 1), drive_prompt_assembled),
    Driver((PIPELINE, "PipelineRuntime._reply", 1), drive_heard),
    Driver((PIPELINE, "PipelineRuntime._reply", 2), drive_replied),
    Driver((PIPELINE, "PipelineRuntime._speak_reply", 1), drive_agent_said),
    Driver((PIPELINE, "PipelineRuntime._speak_reply", 2), drive_handover),
    Driver((PIPELINE, "PipelineRuntime._run_one", 1), drive_tool_call),
    Driver((TURNTAKING, "TurnTaking.finish_utterance", 1), drive_barge_in_manual),
    Driver((TURNTAKING, "TurnTaking._gate_barge_in", 1), drive_barge_in_under_the_floor),
    Driver((TURNTAKING, "TurnTaking._gate_barge_in", 2), drive_barge_in_merged),
    Driver(
        (TURNTAKING, "TurnTaking._gate_barge_in", 3),
        drive_barge_in_in_the_refractory_window,
    ),
    Driver(
        (TURNTAKING, "TurnTaking._gate_barge_in", 4), drive_barge_in_without_a_transcript
    ),
    Driver((TURNTAKING, "TurnTaking._gate_barge_in", 5), drive_barge_in_confirmed),
    Driver((FILLER, "FillerRunner._fire", 1), drive_filler_skipped_for_speech),
    Driver((FILLER, "FillerRunner._fire", 2), drive_filler_skipped_for_a_barge_in),
    Driver((FILLER, "FillerRunner._fire", 3), drive_filler_played),
)

DRIVERS: tuple[Driver, ...] = STORE_DRIVERS + SESSION_DRIVERS


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

    Filtered to the event the walk says that path emits, and the filter
    is the point rather than a tidiness. A session driver reaches its
    decision by holding a whole conversation, so its run emits every
    neighbouring path's records too; keeping them would record the same
    shapes several times over and make this file move whenever an
    unrelated path's timing did. Every neighbour has a driver of its
    own, which is what the exhaustiveness obligations above are for.
    """
    emitted = {site.identity: site.event for site in sites()}
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
                if getattr(one, "event", None) == emitted[driver.identity]
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
