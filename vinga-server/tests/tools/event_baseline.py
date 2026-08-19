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
import datetime as dt
import json
import logging
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import vinga_server
from tests.support.sessions import Gate
from vinga_server.conversations import store as store_module
from vinga_server.conversations.records import ToolInvocation, TurnRecord
from vinga_server.conversations.store import ConversationStore
from vinga_server.events import catalog as catalog_module
from vinga_server.events.catalog import declaration_of
from vinga_server.logs import _STANDARD_ATTRIBUTES

# The channels this baseline covers, and therefore the modules whose
# statically known emit sites it must claim. One entry today; M2 and M3
# widen it as they convert.
SCOPE: tuple[str, ...] = ("vinga_server.conversations.store",)

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


def _event_of(module: str, enclosing: str, node: ast.Call) -> str:
    """Which event one call emits.

    An untyped call says so in its `event=` keyword. A typed one says so
    by the variant it constructs, which is what the declaration behind
    that variant is named. A shape neither of those reads is refused
    rather than skipped: a path the walk cannot read is a path the
    inventory would silently lose.
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
    variant = getattr(catalog_module, body.func.id, None)
    if variant is None:
        raise AssertionError(f"{where}: {body.func.id} is not a catalog variant")
    return declaration_of(variant).name


class _Sites(ast.NodeVisitor):
    """Every emit path in one module, in source order, numbered within
    its enclosing scope across BOTH shapes.

    Across both deliberately. Numbering the shapes separately would give
    two paths in one function the same ordinal while a module was half
    converted, which is the one moment the identity has to stay stable.
    """

    def __init__(self, module: str, tree: ast.AST) -> None:
        self.module = module
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
                    event=_event_of(self.module, enclosing, node),
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
    for module in SCOPE:
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
    """

    identity: tuple[str, str, int]
    drive: Callable[[Path], None]

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

DRIVERS: tuple[Driver, ...] = (
    Driver((MODULE, "ConversationStore.start", 1), drive_enabled),
    Driver((MODULE, "ConversationStore.record_event", 1), drive_dropped),
    Driver((MODULE, "ConversationStore._failed", 1), drive_write_failed),
    Driver((MODULE, "ConversationStore._prune", 1), drive_prune_failed),
    Driver((MODULE, "ConversationStore._prune", 2), drive_pruned),
)


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
    """Every driver run, in declaration order, with what it produced."""
    baseline: dict[str, list[dict[str, Any]]] = {}
    for driver in DRIVERS:
        with tempfile.TemporaryDirectory(prefix="vinga-baseline-") as directory:
            with listening() as collector:
                driver.drive(Path(directory))
            baseline[driver.key] = [shape(one) for one in collector.records]
    return baseline


def rendered(baseline: dict[str, list[dict[str, Any]]]) -> str:
    return json.dumps(baseline, indent=2) + "\n"


def committed() -> dict[str, list[dict[str, Any]]]:
    return json.loads(COMMITTED.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


if __name__ == "__main__":  # pragma: no cover - the regeneration path
    COMMITTED.parent.mkdir(parents=True, exist_ok=True)
    COMMITTED.write_text(rendered(captured()), encoding="utf-8")
    print(f"wrote {COMMITTED}")
