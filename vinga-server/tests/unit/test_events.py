"""The contract between an emitter and the consumers of what it emits.

The suites next door prove the events themselves: their channel, their
sentences, their fields. This one proves the machinery under them, which
is what #120's conversation store and the #66/#67 exporters will attach
to without touching a single emit site.

Four promises, and each one is a decision rather than an accident:

- a tap sees every event while it is attached, and none after it
  detaches, while the log carries on either way;
- the log is told **last**, so "an event that is logged is an event that
  is recorded" survives literally: the capture is offered the payload
  before the record exists;
- a tap that raises costs its own event and nothing else, and says so in
  a plain sentence rather than in an event that would come back through
  the taps;
- a server-scope consumer attaches once, to the hub, and reaches
  emitters built before and after it attached.

Plus the clocks, which are the reason there are two emitters at all: a
session event is stamped by the session loop, because the capture's
audio is; a server event is stamped by `time.monotonic`, because
`create_app` emits before any loop is running.

This suite is about the machinery rather than the surface, so it emits
shapes the production surface does not have: a handful of synthetic
events whose whole purpose is to be dispatched, copied, and refused by a
broken consumer. They are declared through the public declaration
interface into a catalog of this file's own, which is what keeps them
out of the generated reference and out of the driver suite's
obligations. The
declarations are therefore a second reading of this file: an emission
this suite adds and does not declare fails at import rather than passing
quietly.
"""

import asyncio
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from typing import ClassVar

import pytest

from tests.support.catalog import scratch_catalog
from vinga_server.events import (
    SESSION_LOGGER,
    Emission,
    ServerEvents,
    SessionEvents,
    attach_server_tap,
    detach_server_tap,
    server_emitters,
    session_clock,
)
from vinga_server.events.catalog import Variant, declare, value
from vinga_server.events.values import Identifier, PromptSources, Whole

# A channel this server really speaks on, because a variant names one:
# what makes these emissions synthetic is their shapes, not somewhere
# nothing listens.
CHANNEL = "vinga_server.ota"

SESSION_CHANNEL = "vinga_server.session"


@dataclass(frozen=True)
class Said(Variant):
    CHANNEL: ClassVar[str] = SESSION_CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = "said %s"
    ARGS: ClassVar[tuple[str, ...]] = ("what",)

    what: Identifier = value(carried=False)


@dataclass(frozen=True)
class AlsoSaid(Variant):
    CHANNEL: ClassVar[str] = SESSION_CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = "also said %s"
    ARGS: ClassVar[tuple[str, ...]] = ("what",)

    extra_field: Whole = value()
    # Said and not stored, which is what leaves this variant one field
    # of its own: the payload order a tap sees is the base, then that.
    what: Identifier = value(carried=False)


@dataclass(frozen=True)
class LastSaid(Variant):
    CHANNEL: ClassVar[str] = SESSION_CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = "last said %s"
    ARGS: ClassVar[tuple[str, ...]] = ("what",)

    what: Identifier = value(carried=False)


@dataclass(frozen=True)
class Nested(Variant):
    """The one shape carrying a nested value, which is what a shallow
    copy would share between a tap and the retained record."""

    CHANNEL: ClassVar[str] = SESSION_CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = "something happened"

    sources: PromptSources = value()


@dataclass(frozen=True)
class CheckedIn(Variant):
    CHANNEL: ClassVar[str] = CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = "checked in %s"
    ARGS: ClassVar[tuple[str, ...]] = ("device",)

    device: Identifier = value()


@dataclass(frozen=True)
class CheckedInAgain(Variant):
    CHANNEL: ClassVar[str] = CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = "checked in again"


@dataclass(frozen=True)
class SaidAtDebug(Variant):
    CHANNEL: ClassVar[str] = CHANNEL
    LEVEL: ClassVar[int] = logging.DEBUG
    TEMPLATE: ClassVar[str] = "a"


@dataclass(frozen=True)
class SaidAtInfo(Variant):
    CHANNEL: ClassVar[str] = CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = "b"


@dataclass(frozen=True)
class SaidAtWarning(Variant):
    CHANNEL: ClassVar[str] = CHANNEL
    LEVEL: ClassVar[int] = logging.WARNING
    TEMPLATE: ClassVar[str] = "c"


@dataclass(frozen=True)
class SaidAtError(Variant):
    CHANNEL: ClassVar[str] = CHANNEL
    LEVEL: ClassVar[int] = logging.ERROR
    TEMPLATE: ClassVar[str] = "d"


@pytest.fixture(autouse=True)
def _scratch() -> Iterator[None]:
    """Every declaration this file makes is its own. A scratch event
    that reached the production catalog would reach the generated
    reference with it, and would be a declaration no driver
    produces.

    Declared inside the fixture rather than at import, because
    `declare()` registers into whichever catalog is installed and the
    production one is installed at import.
    """
    with scratch_catalog():
        declare("one", variants=(Said, CheckedIn))
        declare("two", variants=(AlsoSaid, CheckedInAgain))
        declare("three", variants=(LastSaid,))
        declare("nested", variants=(Nested,))
        declare(
            "levels", variants=(SaidAtDebug, SaidAtInfo, SaidAtWarning, SaidAtError)
        )
        yield


class Recorder:
    """A tap that keeps what it was told, in the order it was told."""

    def __init__(self) -> None:
        self.seen: list[Emission] = []

    def emit(self, emission: Emission) -> None:
        self.seen.append(emission)


class Broken:
    """A consumer with a bug in it, which is the only kind the guards
    exist for."""

    def __init__(self) -> None:
        self.calls = 0

    def emit(self, emission: Emission) -> None:
        self.calls += 1
        raise RuntimeError("this consumer is broken")


class Vandal:
    """A consumer that writes to what it was handed. Not malice
    necessarily: a consumer that normalizes a payload before shipping it
    is one edit away from this."""

    def emit(self, emission: Emission) -> None:
        emission.payload["event"] = "rewritten"
        emission.payload["sources"]["persona"] = 999
        # `message` is a name `logging` puts on every record itself, so
        # a payload carrying it makes the logging call raise rather than
        # write the line at all.
        emission.payload["message"] = "not what was said"


class CaptureSpy:
    """The capture's own surface and nothing else, which is what
    `SessionRecording` describes: a real `SessionCapture` is not needed
    here, and the protocol is what says so. It also reads the log as it
    is being told, which is what turns "before the record exists" into
    an assertion rather than a claim about source order."""

    def __init__(self, caplog: pytest.LogCaptureFixture) -> None:
        self._caplog = caplog
        self.seen: list[tuple[dict, float, int]] = []
        self.samples: list[tuple[float, bool, bool, float]] = []
        self.drops: list[tuple[str, float]] = []

    def event(self, payload: dict, at: float) -> None:
        self.seen.append((payload, at, len(self._caplog.records)))

    def vad(self, speech_ms: float, listening: bool, replying: bool, now: float) -> None:
        self.samples.append((speech_ms, listening, replying, now))

    def dropped(self, reason: str, now: float) -> None:
        self.drops.append((reason, now))


def payload_of(record: logging.LogRecord) -> dict:
    standard = vars(logging.LogRecord("", logging.INFO, "", 0, "", None, None))
    return {
        key: value
        for key, value in vars(record).items()
        if key not in standard and key not in ("taskName", "message", "asctime")
    }


# --- the tap, attached, fanned out to, and detached -------------------


def test_a_tap_sees_every_event_until_it_detaches(caplog: pytest.LogCaptureFixture) -> None:
    events = SessionEvents("s1")
    events.device = "aa:bb:cc:dd:ee:ff"
    tap = Recorder()

    with caplog.at_level("INFO"):
        events.emit(lambda: Said(what=Identifier("attaching")))
        events.attach(tap)
        events.emit(
            lambda: AlsoSaid(what=Identifier("attached"), extra_field=Whole(7))
        )
        events.detach(tap)
        events.emit(lambda: LastSaid(what=Identifier("detaching")))

    assert [emission.payload["event"] for emission in tap.seen] == ["two"]
    # The log never stopped, which is the half a detach must not touch.
    assert [record.event for record in caplog.records] == ["one", "two", "three"]
    # The tap gets the finished payload: what every event carries, then
    # this event's own fields, in that order.
    assert list(tap.seen[0].payload.items()) == [
        ("event", "two"),
        ("session", "s1"),
        ("device", "aa:bb:cc:dd:ee:ff"),
        ("extra_field", 7),
    ]
    # And the sentence unrendered, so a consumer can render it the way
    # the log does or ignore it.
    assert tap.seen[0].message == "also said %s"
    assert tap.seen[0].args == ("attached",)
    assert tap.seen[0].level == logging.INFO


def test_detaching_a_tap_that_was_never_attached_is_not_an_error() -> None:
    """A caller unwinding does not have to remember how far it got."""
    events = SessionEvents("s1")
    events.detach(Recorder())


def test_the_log_is_the_last_consumer_told(caplog: pytest.LogCaptureFixture) -> None:
    """The invariant the capture was written inside the payload builder
    for: every logged event was first offered to the capture."""
    events = SessionEvents("s1")
    spy = CaptureSpy(caplog)

    with caplog.at_level("INFO"):
        events.attach_capture(spy)
        events.emit(lambda: Nested(sources=PromptSources({"persona": 4})))

    (payload, _, records_at_the_time) = spy.seen[0]
    assert records_at_the_time == 0, "the capture was told after the record existed"
    assert len(caplog.records) == 1
    # And it is the same event: a tap's copy is isolated from the log
    # (see below), never different from it.
    assert payload == payload_of(caplog.records[0])


def test_the_capture_detaches_and_the_events_carry_on(caplog: pytest.LogCaptureFixture) -> None:
    events = SessionEvents("s1")
    spy = CaptureSpy(caplog)

    with caplog.at_level("INFO"):
        events.attach_capture(spy)
        events.emit(lambda: Said(what=Identifier("recorded")))
        events.detach_capture()
        events.emit(lambda: AlsoSaid(what=Identifier("not"), extra_field=Whole(1)))

    assert [payload["event"] for payload, _, _ in spy.seen] == ["one"]
    assert [record.event for record in caplog.records] == ["one", "two"]


def test_a_second_capture_replaces_the_first(caplog: pytest.LogCaptureFixture) -> None:
    """One session records once. A second attach that only overwrote
    the handle would leave the first adapter in the tap list, writing to
    a recording nobody is going to close, while `vad` and `dropped` went
    to the second: one conversation split down the middle of two
    files."""
    events = SessionEvents("s1")
    first, second = CaptureSpy(caplog), CaptureSpy(caplog)

    with caplog.at_level("INFO"):
        events.attach_capture(first)
        events.attach_capture(second)
        events.emit(lambda: Said(what=Identifier("recorded")))
        events.vad(120.0, True, False)
        events.detach_capture()
        events.emit(lambda: AlsoSaid(what=Identifier("nowhere"), extra_field=Whole(1)))

    assert [payload["event"] for payload, _, _ in first.seen] == []
    assert [payload["event"] for payload, _, _ in second.seen] == ["one"]
    assert first.samples == [] and len(second.samples) == 1
    # And one detach is enough: the first attach left nothing behind to
    # keep the events flowing to.
    assert [record.event for record in caplog.records] == ["one", "two"]


# --- a consumer with a bug in it --------------------------------------


def test_a_tap_that_raises_starves_neither_the_log_nor_the_taps_after_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    events = SessionEvents("s1")
    broken, after = Broken(), Recorder()

    with caplog.at_level("INFO"):
        events.attach(broken)
        events.attach(after)
        events.emit(lambda: Said(what=Identifier("said")))
        events.emit(lambda: AlsoSaid(what=Identifier("said"), extra_field=Whole(1)))

    # It was not detached by its own failure: a consumer that fails once
    # is not a consumer that is gone.
    assert broken.calls == 2
    assert [emission.payload["event"] for emission in after.seen] == ["one", "two"]
    assert [record.event for record in caplog.records if hasattr(record, "event")] == [
        "one",
        "two",
    ]


def test_the_tap_failure_is_a_plain_sentence_and_not_an_event(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Not an event, deliberately: an event would go back through the
    taps, and a broken tap would then recurse into itself."""
    events = SessionEvents("s1")

    with caplog.at_level("INFO"):
        events.attach(Broken())
        events.emit(lambda: Said(what=Identifier("something")))

    (report,) = [record for record in caplog.records if not hasattr(record, "event")]
    assert report.name == SESSION_LOGGER
    assert report.levelno == logging.WARNING
    # The tap's own class name and nothing else. What the tap raised is
    # deliberately unsaid: the sentinel suite drives that half.
    assert report.args == ("Broken",)
    assert report.getMessage() == "an event tap (Broken) failed and was skipped"
    assert payload_of(report) == {}


def test_a_tap_cannot_rewrite_what_the_log_keeps(caplog: pytest.LogCaptureFixture) -> None:
    """The dataclass is frozen; the dict behind `payload` is not. Every
    non-log tap therefore gets its own deep copy, so an edit reaches
    neither the retained line nor the tap after it."""
    events = SessionEvents("s1")
    after = Recorder()
    events.attach(Vandal())
    events.attach(after)

    with caplog.at_level("INFO"):
        events.emit(lambda: Nested(sources=PromptSources({"persona": 4})))

    (record,) = caplog.records
    assert payload_of(record) == {
        "event": "nested",
        "session": "s1",
        "device": None,
        "sources": {"persona": 4},
    }
    assert record.getMessage() == "something happened"
    # Nested as well as top level, and the tap after it is shown the
    # event rather than the edit.
    assert after.seen[0].payload["event"] == "nested"
    assert after.seen[0].payload["sources"] == {"persona": 4}


# --- the server scope, and its one attachment point -------------------


def test_a_server_tap_reaches_an_emitter_built_after_it_attached(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The hub exists so a future exporter attaches once instead of
    hunting through every module's private emitter, including the
    modules imported after it attached."""
    tap = Recorder()
    attach_server_tap(tap)
    try:
        with caplog.at_level("INFO"):
            later = ServerEvents(CHANNEL)
            later.emit(lambda: CheckedIn(device=Identifier("aa:bb")))
    finally:
        detach_server_tap(tap)

    assert [emission.payload["event"] for emission in tap.seen] == ["one"]
    # No session and no device default: a server event names what it is
    # about explicitly.
    assert tap.seen[0].payload == {"event": "one", "device": "aa:bb"}
    assert caplog.records[0].name == CHANNEL
    assert later in server_emitters()

    # And nothing after the detach.
    with caplog.at_level("INFO"):
        later.emit(lambda: CheckedInAgain())
    assert len(tap.seen) == 1


def test_a_server_emitter_carries_every_level(caplog: pytest.LogCaptureFixture) -> None:
    """`.debug` is not a nicety: `device_bindings_snapshot_only` is a
    structured debug event today, and its level is part of the retained
    surface."""
    events = ServerEvents(CHANNEL)
    with caplog.at_level("DEBUG"):
        events.emit(lambda: SaidAtDebug())
        events.emit(lambda: SaidAtInfo())
        events.emit(lambda: SaidAtWarning())
        events.emit(lambda: SaidAtError())

    assert [record.levelno for record in caplog.records] == [
        logging.DEBUG,
        logging.INFO,
        logging.WARNING,
        logging.ERROR,
    ]


def test_a_broken_server_tap_reports_on_the_emitters_own_channel(
    caplog: pytest.LogCaptureFixture,
) -> None:
    attach_server_tap(Broken())
    try:
        with caplog.at_level("INFO"):
            ServerEvents(CHANNEL).emit(lambda: SaidAtInfo())
    finally:
        detach_server_tap(_only_broken_tap())

    (report,) = [record for record in caplog.records if not hasattr(record, "event")]
    assert report.name == CHANNEL
    assert report.levelno == logging.WARNING


def _only_broken_tap():
    """The tap the test above attached, found again so the hub is left
    the way it was found; the hub is module state and every other test
    shares it."""
    from vinga_server.events import _hub

    return next(tap for tap in _hub.taps if isinstance(tap, Broken))


# --- the two clocks ---------------------------------------------------


def test_a_server_event_is_emitted_where_no_loop_is_running(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`create_app` reports its capture directory before the server
    serves anything, and `onboarding` logs its banner before that, so a
    mandatory loop clock would raise at both."""
    with pytest.raises(RuntimeError):
        asyncio.get_running_loop()

    tap = Recorder()
    attach_server_tap(tap)
    try:
        with caplog.at_level("INFO"):
            ServerEvents(CHANNEL).emit(lambda: SaidAtInfo())
    finally:
        detach_server_tap(tap)

    assert tap.seen[0].at > 0


async def test_a_session_event_is_stamped_with_the_session_loop(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The capture's tracks are aligned by the loop's clock, so an
    event's offset into the recorded audio is only meaningful against
    that one."""
    loop = asyncio.get_running_loop()
    assert session_clock() == pytest.approx(loop.time(), abs=0.05)

    events = SessionEvents("s1")
    tap = Recorder()
    events.attach(tap)
    with caplog.at_level("INFO"):
        events.emit(lambda: Said(what=Identifier("something")))

    assert tap.seen[0].at == pytest.approx(loop.time(), abs=0.05)


def test_the_clock_is_a_dependency_rather_than_an_assumption(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Stated at construction and swappable, which is what keeps the
    two scopes' clocks a decision instead of a surprise."""
    session = SessionEvents("s1", clock=lambda: 12.5)
    server = ServerEvents(CHANNEL, clock=lambda: 99.5)
    taps = (Recorder(), Recorder())
    session.attach(taps[0])
    attach_server_tap(taps[1])
    try:
        with caplog.at_level("INFO"):
            session.emit(lambda: Said(what=Identifier("a")))
            server.emit(lambda: SaidAtInfo())
    finally:
        detach_server_tap(taps[1])

    assert taps[0].seen[0].at == 12.5
    assert taps[1].seen[0].at == 99.5
