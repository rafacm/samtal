"""What the emit path produces, and what its guard does.

Three claims. The first is what a constructed variant becomes: the
record a deployment keeps and the emission every tap is handed, on both
scopes, with the session emitter's own identity contributed inside the
guard beside the variant's own values.

The second is the construction guard's, and it needs its own pins
because no caller can prove it: a site hands the emitter a thunk and
never learns whether it ran. So both of its refusals (a thunk that
raised, a variant handed to an emitter on another channel) are driven,
and what a refusal does is asserted whole: one report on the emitter's
own channel, and the emission dropped rather than replaced.

The third is what happens when saying so is itself what breaks. Every
report this module makes is made from inside a guard, and a logging call
is not the inert operation it looks like: a handler, a filter and a
formatter are code somebody else installed, and the sharpest case is a
failing LOG tap reported back onto the same broken channel.

That every converted path still produces a record its declaration
describes is `test_event_baseline.py`'s claim, not this file's:
eighty-one paths driven, and each record matched against the variants of
its event.

What a refusal is allowed to SAY is the sentinel suite's, next door.
"""

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

import pytest

from tests.support.catalog import scratch_catalog
from vinga_server import events as events_module
from vinga_server.events import (
    REFUSAL_MESSAGE,
    SESSION_LOGGER,
    UNBUILT_LABEL,
    Emission,
    ServerEvents,
    SessionEvents,
    attach_server_tap,
    detach_server_tap,
)
from vinga_server.events.catalog import (
    ConversationsDropped,
    ConversationsPruned,
    Variant,
    declare,
    value,
)
from vinga_server.events.values import ConfiguredPath, Count, Identifier, SessionId

CHANNEL = "vinga_server.conversations.store"

# Where the scratch declarations below ride, so that neither half of the
# equivalence borrows the store's own channel or its events.
SCRATCH_CHANNEL = "vinga_server.ota"

DIRECTORY = Path("/var/lib/vinga/conversations")


class Tap:
    """A consumer, keeping every emission whole: the payload, the
    unrendered sentence and the arguments behind it."""

    def __init__(self) -> None:
        self.seen: list[Emission] = []

    def emit(self, emission: Emission) -> None:
        self.seen.append(emission)


@pytest.fixture
def tap() -> Iterator[Tap]:
    consumer = Tap()
    attach_server_tap(consumer)
    try:
        yield consumer
    finally:
        detach_server_tap(consumer)


@pytest.fixture
def emitter() -> ServerEvents:
    return ServerEvents(CHANNEL)


@pytest.fixture(autouse=True)
def _scratch() -> Iterator[None]:
    """A catalog of this file's own, holding one extra declaration: the
    variant on another channel that the mismatch branch needs. Declared
    in a scratch catalog so it cannot reach the generated reference."""
    with scratch_catalog():
        declare("scratch_elsewhere", variants=(Elsewhere,))
        declare("measured", variants=(Measured,))
        declare("recording", variants=(Recording,))
        declare("conversational", variants=(Conversational,))
        yield


def refused(caplog: pytest.LogCaptureFixture, channel: str) -> logging.LogRecord:
    """The one report a refused emission leaves on `channel`, whole.

    An event carries an `event` field and a report does not, which is
    what separates the two on a channel carrying both."""
    said = [
        one
        for one in caplog.records
        if one.name == channel and not hasattr(one, "event")
    ]
    assert len(said) == 1, f"expected one report, got {len(said)}"
    return said[0]


def only(tap: Tap) -> Emission:
    assert len(tap.seen) == 1, f"expected one emission, got {len(tap.seen)}"
    return tap.seen[0]


def shape(emission: Emission) -> tuple[object, ...]:
    """An emission in the dimensions a consumer sees, argument types
    included: a `PosixPath` rendered where a `str` used to be is a
    difference the values alone would hide."""
    return (
        emission.level,
        emission.message,
        emission.args,
        tuple(type(one).__name__ for one in emission.args),
        emission.payload,
    )


# --- what a constructed variant becomes -------------------------------
#
# Two scratch declarations, so the asymmetric value type is covered:
# `ConfiguredPath` is the one whose payload field and sentence argument
# differ, the field carrying the path as text and the sentence rendering
# the object.


@dataclass(frozen=True)
class Measured(Variant):
    CHANNEL: ClassVar[str] = SCRATCH_CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = "measured %s in %d ms"
    ARGS: ClassVar[tuple[str, ...]] = ("stage", "duration_ms")

    stage: Identifier = value()
    duration_ms: Count = value()


@dataclass(frozen=True)
class Recording(Variant):
    CHANNEL: ClassVar[str] = SCRATCH_CHANNEL
    LEVEL: ClassVar[int] = logging.WARNING
    TEMPLATE: ClassVar[str] = "recording to %s"
    ARGS: ClassVar[tuple[str, ...]] = ("path",)

    path: ConfiguredPath = value()


def test_a_typed_emission_reaches_the_log_on_its_own_channel(
    emitter: ServerEvents, caplog: pytest.LogCaptureFixture
) -> None:
    """A converted path, all the way to the record a deployment keeps.
    The log tap is a consumer like any other, and it is the last one."""
    with caplog.at_level(logging.INFO):
        emitter.emit(
            lambda: ConversationsPruned(
                conversations=Count(1), sessions=Count(2), days=Count(90)
            )
        )

    (record,) = [one for one in caplog.records if one.name == CHANNEL]
    assert record.levelno == logging.INFO
    assert record.msg == (
        "conversations: pruned %d conversation(s) and %d session record(s) older "
        "than %d days"
    )
    assert record.args == (1, 2, 90)
    assert record.event == "conversations_pruned"  # type: ignore[attr-defined]
    assert record.sessions == 2  # type: ignore[attr-defined]


# --- the session emitter, whose base is an identity -------------------
#
# The difference the session channel makes: the emitter contributes two
# values as well as the event's name, both of them value types, and the
# sentence renders one of them. Both are built inside the guard, because
# a session id is a value like any other and a malformed one has to
# refuse where everything else refuses.


@dataclass(frozen=True)
class Conversational(Variant):
    CHANNEL: ClassVar[str] = SESSION_LOGGER
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = "session %s: measured %s"
    ARGS: ClassVar[tuple[str, ...]] = ("session", "stage")

    stage: Identifier = value()


def test_a_session_emission_carries_the_identity_the_emitter_owns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The identity is the emitter's to know rather than a value thirty
    sites restate, and it reaches both halves of the record: the payload
    under the base's own keys, and the sentence's first `%` position,
    which every conversation sentence opens with."""
    events = SessionEvents("alpha", clock=lambda: 1.0)
    events.device = "aa:bb:cc:dd:ee:ff"
    consumer = Tap()
    events.attach(consumer)

    with caplog.at_level(logging.INFO):
        events.emit(lambda: Conversational(stage=Identifier("asr")))

    typed = only(consumer)
    assert typed.payload == {
        "event": "conversational",
        "session": "alpha",
        "device": "aa:bb:cc:dd:ee:ff",
        "stage": "asr",
    }
    assert typed.args == ("alpha", "asr")


def test_a_session_event_before_the_mac_is_known_names_no_device() -> None:
    """The nullability of the base is a fact rather than a hedge: the
    events a session emits before its Device-Id is normalized name no
    device, and the record says so with a key rather than by dropping
    one."""
    events = SessionEvents("alpha", clock=lambda: 1.0)
    consumer = Tap()
    events.attach(consumer)

    events.emit(lambda: Conversational(stage=Identifier("asr")))

    assert only(consumer).payload["device"] is None


def test_a_session_emission_answers_the_reading_it_was_stamped_with() -> None:
    """The one caller that reads the answer is the turn record, whose
    offset has to equal its event's rather than a second reading taken
    beside it."""
    events = SessionEvents("alpha", clock=lambda: 41.5)

    assert events.emit(lambda: Conversational(stage=Identifier("asr"))) == 41.5


def test_an_unusable_identity_is_refused_whole(
    caplog: pytest.LogCaptureFixture, refusals_are_expected: None
) -> None:
    """A session id is a value type, so constructing one can refuse, and
    it must refuse where the variant's own values refuse rather than on
    whatever path was emitting.

    Whole, and that is the rule the one guard around the identities
    encodes: a conversation record missing the session it belongs to is
    a shape the declaration denies exists, so a lawful device id buys a
    refusing session id nothing and the emission is dropped."""
    events = SessionEvents("has a space", clock=lambda: 1.0)
    events.device = "aa:bb:cc:dd:ee:ff"
    consumer = Tap()
    events.attach(consumer)

    with caplog.at_level(logging.INFO):
        events.emit(lambda: Conversational(stage=Identifier("asr")))

    assert refused(caplog, SESSION_LOGGER).args == (
        UNBUILT_LABEL,
        "construction_failed",
    )
    assert consumer.seen == []


def test_a_refused_identity_still_answers_the_reading_it_was_stamped_with(
    refusals_are_expected: None,
) -> None:
    """The clock is read before the guard runs, so the one caller that
    reads the answer (#120's turn record) has an instant to place its
    row at whether or not the event beside it survived."""
    events = SessionEvents("has a space", clock=lambda: 41.5)

    assert events.emit(lambda: Conversational(stage=Identifier("asr"))) == 41.5


# --- the guard, which no caller can prove -----------------------------


@dataclass(frozen=True)
class Elsewhere(Variant):
    """A variant declared on another channel, so that handing it to
    this emitter is the mismatch and nothing else is."""

    CHANNEL: ClassVar[str] = SCRATCH_CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = "said %s"
    ARGS: ClassVar[tuple[str, ...]] = ("stage",)

    stage: Identifier = value()


def a_failing_thunk() -> Variant:
    """A construction that raises, which is what the thunk exists to
    keep off the reply path."""
    return ConversationsDropped(session=SessionId("has a space"))


def a_mismatched_thunk() -> Variant:
    return Elsewhere(stage=Identifier("asr"))


# The two branches, each with the pair its report has to carry. The
# pair is part of the parametrization rather than asserted once for
# both, because the two branches say DIFFERENT things and that is the
# whole of what the code distinguishes: a thunk that raised names
# nothing, since it is opaque until it returns, while a variant handed
# to the wrong emitter is a declared event whose own name the report may
# state and whose code says which of the two faults it was.
REFUSING = (
    (
        "a construction that raised",
        a_failing_thunk,
        (UNBUILT_LABEL, "construction_failed"),
    ),
    (
        "a variant from another channel",
        a_mismatched_thunk,
        ("scratch_elsewhere", "wrong_channel"),
    ),
)


@pytest.mark.parametrize(
    "build, expected",
    [(one, two) for _, one, two in REFUSING],
    ids=[one for one, _, _ in REFUSING],
)
def test_an_emission_the_guard_could_not_build_is_dropped(
    build: object,
    expected: tuple[str, str],
    emitter: ServerEvents,
    tap: Tap,
    caplog: pytest.LogCaptureFixture,
    refusals_are_expected: None,
) -> None:
    """A telemetry bug costs a log line, never a reply. Nothing rides in
    the emission's place: what the site meant to say is exactly what
    could not be built, so the report is the whole of what is said and
    no consumer is handed anything at all.

    The report by equality on both halves, unrendered, which is what
    makes the refusal vocabulary a closed set with a test behind each
    member rather than behind one of them."""
    with caplog.at_level(logging.INFO):
        emitter.emit(build)  # type: ignore[arg-type]

    report = refused(caplog, CHANNEL)
    assert report.levelno == logging.ERROR
    assert report.msg == REFUSAL_MESSAGE
    assert report.args == expected
    assert tap.seen == []


def test_the_refusal_report_names_the_fault_and_nothing_else(
    emitter: ServerEvents,
    caplog: pytest.LogCaptureFixture,
    refusals_are_expected: None,
) -> None:
    """Unrendered and by equality rather than hunted by substring: the
    report's two halves are a fixed label and a fixed code, and there is
    no third thing it may say. Not even the exception's class, which
    `type()` lets a caller name whatever it likes."""
    with caplog.at_level(logging.INFO):
        emitter.emit(a_failing_thunk)

    report = refused(caplog, CHANNEL)
    assert report.msg == REFUSAL_MESSAGE
    assert report.args == (UNBUILT_LABEL, "construction_failed")


# --- when saying so is itself what breaks -----------------------------
#
# Every report this module makes is made from inside a guard, and a
# logging call is not the inert operation it looks like: a filter and a
# handler are code somebody else installed, `handle` and `filter` are
# called unwrapped, and a formatter meets whatever the record carries.
# The sharpest case is a failing LOG tap, since reporting its failure
# back onto the same broken channel is the recursion the guard exists to
# stop.


class BrokenHandler(logging.Handler):
    """A logging handler that raises where `logging` does not catch it.

    `handleError` swallows a failure inside `emit`, so a realistic
    broken handler has to fail in `handle`, which `callHandlers` calls
    unwrapped."""

    def handle(self, record: logging.LogRecord) -> bool:
        raise RuntimeError("the log handler is broken")


@pytest.fixture
def broken_log() -> Iterator[None]:
    channel = logging.getLogger(CHANNEL)
    handler = BrokenHandler()
    channel.addHandler(handler)
    try:
        yield
    finally:
        channel.removeHandler(handler)


class BrokenTap:
    """A consumer with a bug in it, which is the only kind the guards
    exist for."""

    def emit(self, emission: Emission) -> None:
        raise RuntimeError("this consumer is broken")


def test_a_broken_log_does_not_cost_the_reply_a_refusal_was_reported_on(
    broken_log: None,
    emitter: ServerEvents,
    tap: Tap,
    refusals_are_expected: None,
) -> None:
    """Reporting a refusal is the last thing in the path, and it is on
    the channel the emission was going to. If that channel throws, the
    report is lost and nothing else is: the emit returns, and the next
    event the same emitter builds still reaches every tap.

    The observable is that next event, deliberately. A refused emission
    dispatches nothing, so a broken report has nothing of its own left
    to be observed by, and what the guard is being held to is that it
    cost the emitter nothing."""
    emitter.emit(a_failing_thunk)

    emitter.emit(
            lambda: ConversationsPruned(
                conversations=Count(1), sessions=Count(2), days=Count(90)
            )
        )

    assert only(tap).payload["event"] == "conversations_pruned"


def test_a_broken_tap_reported_on_a_broken_log_still_costs_nothing(
    broken_log: None, emitter: ServerEvents, tap: Tap
) -> None:
    """The oldest guard in the module, hardened the same way. A tap that
    raises is reported on the emitter's own channel, and that channel is
    where the emission was going: if the log is what broke, the report
    goes straight back onto it. The taps after the broken one still saw
    the event."""
    broken = BrokenTap()
    attach_server_tap(broken)
    try:
        emitter.emit(
            lambda: ConversationsPruned(
                conversations=Count(1), sessions=Count(2), days=Count(90)
            )
        )
    finally:
        detach_server_tap(broken)

    assert only(tap).payload["event"] == "conversations_pruned"


def test_reporting_swallows_whatever_the_channel_does(broken_log: None) -> None:
    """The helper itself, since everything above depends on it: what is
    being protected is a reply, and what is being lost is one diagnostic
    line about a diagnostic line."""
    # White-box: this is the last-resort report, the one that runs when
    # the logging channel itself is broken. Everything public above
    # depends on it swallowing, and a swallow that worked is by
    # definition a swallow that left no record to observe.
    events_module._report(
        logging.getLogger(CHANNEL), logging.ERROR, "anything %s", "at all"
    )
