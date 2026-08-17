"""Every conversation-store event, pinned exactly as it is emitted.

The third of the pin suites, and the youngest. `test_event_surface_pins.py`
and `test_server_event_pins.py` were written at #138's baseline and pin
the surface as it stood then; the `conversations_*` events postdate them
(#120's store landed after), so they appear in neither file, and the
suites that do exercise them assert selected attributes rather than the
whole record.

That gap is exactly what #155 cannot afford. M2 makes the emitters
enforce the registry at emit time, and the argument that enforcement
changed nothing is the pin suites passing unmodified. A path with no pin
behind it is a path enforcement could quietly reshape, so this file
closes the five that had none, committed green BEFORE any enforcement
exists.

The style is the two contract files', deliberately down to the helper
names, because these three files answer one question between them. Per
emit path it pins the same five things: `record.name`, `record.levelno`,
`record.msg` unrendered, `record.args` by value and by type, and the
exact set of nonstandard record attributes, read through `logs.py`'s own
standard-attribute set so this suite and the JSON formatter cannot come
to disagree about what an event field is. `sentence` rides alongside as
the rendering a person reads in a review diff, with numbers normalized,
and is the weaker of the two.

Everything here is driven through the store's own seams rather than
through timing, the way `test_conversations_store.py` does it: a writer
parked on its gate, an engine that raises, a clock the test chose.
"""

import datetime as dt
import logging
import re
from pathlib import Path
from typing import Any

import pytest

from samtal_server.conversations import store as store_module
from samtal_server.conversations.records import ToolInvocation, TurnRecord
from samtal_server.conversations.store import ConversationStore
from samtal_server.logs import _STANDARD_ATTRIBUTES
from tests.support.events import events, only
from tests.support.sessions import Gate

# What a value that moves between runs is replaced by, so that the key
# is pinned and the value deliberately is not. The same spelling both
# contract pin suites use.
DYNAMIC = "<dynamic>"

_NUMBER = re.compile(r"\d+(?:\.\d+)?")

# The clock these stores keep, so "recorded two hundred days ago" is a
# number the test chose rather than a sleep.
NOW = dt.datetime(2026, 8, 15, 12, 0, tzinfo=dt.UTC)


def payload_of(record: logging.LogRecord) -> dict[str, Any]:
    """The structured half of a record: exactly the attributes the JSON
    formatter emits as top-level keys."""
    return {key: value for key, value in vars(record).items() if key not in _STANDARD_ATTRIBUTES}


def args_of(record: logging.LogRecord, dynamic_args: tuple[int, ...]) -> tuple[Any, ...]:
    """The values substituted into the template, in order. A
    declared-dynamic position keeps its type rather than its value."""
    return tuple(
        f"<{type(value).__name__}>" if index in dynamic_args else value
        for index, value in enumerate(record.args or ())
    )


def pinned(
    record: logging.LogRecord,
    *,
    dynamic: tuple[str, ...] = (),
    dynamic_args: tuple[int, ...] = (),
    scrub: tuple[str, ...] = (),
) -> dict[str, Any]:
    """What one emit path produces, in the dimensions a consumer sees."""
    fields = {
        key: DYNAMIC if key in dynamic else value for key, value in payload_of(record).items()
    }
    sentence = record.getMessage()
    for text in scrub:
        sentence = sentence.replace(text, DYNAMIC)
    return {
        "logger": record.name,
        "level": record.levelno,
        "template": record.msg,
        "args": args_of(record, dynamic_args),
        "sentence": _NUMBER.sub("<n>", sentence),
        "fields": fields,
    }


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
        tools=(ToolInvocation(position=0, source="builtin", name="remember", result="ok"),),
    )


@pytest.fixture
def stores(tmp_path: Path):
    """Stores that are always stopped, so no test leaves a writer thread
    or an open engine behind."""
    built: list[ConversationStore] = []

    def _build(directory: Path | None = None, **options: Any) -> ConversationStore:
        store = ConversationStore(directory or tmp_path, **options)
        built.append(store)
        return store

    yield _build
    for store in built:
        store.stop()


class Raising:
    """An engine whose every transaction fails. The same shape
    `test_conversations_store.py` plants, so the failure the event
    reports is one this suite chose the class of."""

    def __init__(self, message: str) -> None:
        self.message = message

    def begin(self):
        raise RuntimeError(self.message)

    def dispose(self) -> None:
        return None


# --- conversations/store.py: the store's own five lines ---------------


def test_conversations_enabled(tmp_path: Path, stores, caplog: pytest.LogCaptureFixture) -> None:
    """Said once, before anything connects, and at WARNING for the
    reason `capture_enabled` is: it means this server is keeping what is
    said to it."""
    store = stores()

    with caplog.at_level(logging.INFO):
        store.start()

    assert pinned(
        only(caplog, "conversations_enabled"),
        dynamic=("path",),
        dynamic_args=(0,),
        scrub=(str(store.path),),
    ) == {
        "logger": "samtal_server.conversations.store",
        "level": logging.WARNING,
        "template": "recording conversations to %s",
        "args": ("<PosixPath>",),
        "sentence": f"recording conversations to {DYNAMIC}",
        "fields": {"event": "conversations_enabled", "path": DYNAMIC},
    }


def test_conversations_dropped(
    stores, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Said once per session at its first drop, so a session that is
    dropping steadily costs one line rather than one per record."""
    monkeypatch.setattr(store_module, "MAX_EVENTS_IN_FLIGHT", 4)
    gate = Gate()
    store = stores(gate=gate)
    store.start()
    store.open_session("alpha", 100.0, a_manifest(NOW))
    gate.wait()

    with caplog.at_level(logging.INFO):
        for index in range(10):
            store.record_event("alpha", "vad_edge", logging.DEBUG, {"n": index}, 101.0)

    gate.open_forever()

    assert pinned(only(caplog, "conversations_dropped")) == {
        "logger": "samtal_server.conversations.store",
        "level": logging.WARNING,
        "template": "session %s: the conversation store is behind, dropping events",
        "args": ("alpha",),
        "sentence": "session alpha: the conversation store is behind, dropping events",
        "fields": {"event": "conversations_dropped", "session": "alpha"},
    }


def test_conversations_failed_on_a_write(
    stores, caplog: pytest.LogCaptureFixture
) -> None:
    """A write that failed took its batch with it. The class name and
    nothing else: an exception raised near a database quotes the row it
    choked on."""
    gate = Gate()
    store = stores(gate=gate, retention_days=0)
    store.start()
    store.open_session("alpha", 100.0, a_manifest(NOW))
    gate.wait()
    gate.let_through()
    store.record_turn("alpha", a_turn())
    # Parked in front of the turn's own transaction, which is what makes
    # the swap below hit exactly that one and no other.
    gate.wait()
    store._engine = Raising("near a value nothing may repeat: syntax error")

    with caplog.at_level(logging.INFO):
        gate.open_forever()
        store.stop()

    assert pinned(only(caplog, "conversations_failed")) == {
        "logger": "samtal_server.conversations.store",
        "level": logging.WARNING,
        "template": "the conversation store dropped a batch after a write failed (%s)",
        "args": ("RuntimeError",),
        "sentence": "the conversation store dropped a batch after a write failed (RuntimeError)",
        "fields": {"event": "conversations_failed", "failure": "RuntimeError"},
    }


def test_conversations_failed_on_a_prune(
    stores, caplog: pytest.LogCaptureFixture
) -> None:
    """The store's second failure line, and a different sentence under
    the same event name: a store that could not delete still records,
    and the next close tries again."""
    store = stores(retention_days=90, now=lambda: NOW)
    store._engine = Raising("near a value nothing may repeat: syntax error")

    with caplog.at_level(logging.INFO):
        store._prune()

    assert pinned(only(caplog, "conversations_failed")) == {
        "logger": "samtal_server.conversations.store",
        "level": logging.WARNING,
        "template": "the conversation store could not prune (%s)",
        "args": ("RuntimeError",),
        "sentence": "the conversation store could not prune (RuntimeError)",
        "fields": {"event": "conversations_failed", "failure": "RuntimeError"},
    }


def test_conversations_pruned(tmp_path: Path, stores, caplog: pytest.LogCaptureFixture) -> None:
    """A count and nothing else: which sessions were pruned is a
    question for the store, not for the log."""
    seeding = stores(retention_days=0, now=lambda: NOW)
    seeding.start()
    for name, age in (("old-one", 200), ("old-two", 300)):
        started = NOW - dt.timedelta(days=age)
        seeding.open_session(name, 100.0, a_manifest(started))
        seeding.record_turn(name, a_turn())
        seeding.close_session(name, duration_s=5.0, reason="client")
    seeding.stop()

    with caplog.at_level(logging.INFO):
        pruning = stores(retention_days=90, now=lambda: NOW)
        pruning.start()
        pruning.stop()

    assert pinned(only(caplog, "conversations_pruned")) == {
        "logger": "samtal_server.conversations.store",
        "level": logging.INFO,
        "template": "conversations: pruned %d session(s) older than %d days",
        "args": (2, 90),
        "sentence": "conversations: pruned <n> session(s) older than <n> days",
        "fields": {"event": "conversations_pruned", "sessions": 2},
    }


def test_the_store_says_nothing_else(
    tmp_path: Path, stores, caplog: pytest.LogCaptureFixture
) -> None:
    """The count these five pins are complete against. An ordinary
    session start to close emits no store event at all beyond the
    opening line, which is what makes the four failure and retention
    paths above the whole of the rest."""
    store = stores(retention_days=0)

    with caplog.at_level(logging.DEBUG):
        store.start()
        store.open_session("alpha", 100.0, a_manifest(NOW))
        store.record_turn("alpha", a_turn())
        store.close_session("alpha", duration_s=5.0, reason="client")
        store.stop()

    named = {
        record.event
        for record in caplog.records
        if record.name == "samtal_server.conversations.store"
    }
    assert named == {"conversations_enabled"}
    assert len(events(caplog, "conversations_enabled")) == 1
