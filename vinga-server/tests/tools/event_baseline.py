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
what it executes, so the obligation comes from outside it: while the
conformance suite's static walk still exists, every emit site it finds
in scope must be claimed by a driver here, and every variant the catalog
declares on a scoped channel must be produced by one. The first
obligation retires with the last conversion, and the second survives it,
which is what the plan means by claiming exhaustiveness over the
catalog's legal variants rather than over arbitrary call sites.

`tests/unit/test_event_baseline.py` holds both obligations and compares
the capture with the committed file. Regenerate it deliberately:

    uv run python -m tests.tools.event_baseline

The drivers reach into the store the way the pin suite they replace
does: a writer parked on its gate, an engine that raises, a clock the
harness chose. Those reach-ins are the price of driving a failure path
deterministically, and they are the same ones `test_conversations_store.py`
pays.
"""

import datetime as dt
import json
import logging
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tests.support.sessions import Gate
from vinga_server.conversations import store as store_module
from vinga_server.conversations.records import ToolInvocation, TurnRecord
from vinga_server.conversations.store import ConversationStore
from vinga_server.logs import _STANDARD_ATTRIBUTES

# The channels this baseline covers, and therefore the modules whose
# statically known emit sites it must claim. One entry today; M2 and M3
# widen it as they convert.
SCOPE: tuple[str, ...] = ("vinga_server.conversations.store",)

COMMITTED = (
    Path(__file__).resolve().parent.parent / "unit" / "data" / "event-baseline.json"
)

# The clock these stores keep, so "recorded two hundred days ago" is a
# number the harness chose rather than a sleep.
NOW = dt.datetime(2026, 8, 15, 12, 0, tzinfo=dt.UTC)


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
