"""What the emit path produces, and what its guard does.

Three claims. The first is what a constructed variant becomes: the
record a deployment keeps and the emission every tap is handed, on both
scopes, with the session emitter's own identity contributed inside the
guard beside the variant's own values.

The second is the construction guard's, and it needs its own pins
because no caller can prove it: a site hands the emitter a thunk and
never learns whether it ran. So both of its refusals (a thunk that
raised, a variant handed to an emitter on another channel) are driven
in both modes, and both modes' behavior is asserted: strict refuses,
forgiving says so once and dispatches the declared `schema_violation`
in the emission's place.

The third is what happens when saying so is itself what breaks. Every
report this module makes is made from inside a guard, and a logging call
is not the inert operation it looks like: a handler, a filter and a
formatter are code somebody else installed, and the sharpest case is a
failing LOG tap reported back onto the same broken channel.

That the record a converted path produces is the record it produced
before is the committed baseline's claim, not this file's: eighty-one
paths, captured before the conversion and unmoved by it.

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
    SESSION_LOGGER,
    UNBUILT_LABEL,
    Emission,
    EventSchemaError,
    ServerEvents,
    SessionEvents,
    attach_server_tap,
    detach_server_tap,
)
from vinga_server.events.catalog import (
    SCHEMA_VIOLATION,
    SCHEMA_VIOLATION_MESSAGE,
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
def _mode() -> Iterator[None]:
    restored = events_module.enforcement()
    try:
        yield
    finally:
        events_module.set_enforcement(restored)


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
        emitter.emit(lambda: ConversationsPruned(sessions=Count(2), days=Count(90)))

    (record,) = [one for one in caplog.records if one.name == CHANNEL]
    assert record.levelno == logging.INFO
    assert record.msg == "conversations: pruned %d session(s) older than %d days"
    assert record.args == (2, 90)
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


def test_an_unusable_identity_is_refused_inside_the_guard() -> None:
    """A session id is a value type, so constructing one can refuse, and
    it must refuse where the variant's own values refuse rather than on
    whatever path was emitting.

    What the recovery carries is the registry's own word for a session
    it cannot state, never the string it was handed: what a caller
    opened a session under is a caller's, and the recovery event is the
    retained surface like any other record. The device answers the same
    question with the null the surface already uses for "none was
    understood"."""
    events = SessionEvents("has a space", clock=lambda: 1.0)
    events.device = "not-a-mac"
    consumer = Tap()
    events.attach(consumer)
    events_module.set_enforcement(events_module.FORGIVING)

    events.emit(lambda: Conversational(stage=Identifier("asr")))

    assert only(consumer).payload == {
        "event": SCHEMA_VIOLATION,
        "session": events_module.UNSTATED_SESSION,
        "device": None,
    }


def test_the_identity_that_did_validate_survives_the_one_that_did_not() -> None:
    """One guard each rather than one around the pair. A recovery is a
    record an operator reads, and the identity it can still state is the
    half that makes it readable, so a device id that refuses must not
    take a lawful session id down with it."""
    events = SessionEvents("alpha", clock=lambda: 1.0)
    events.device = "not-a-mac"
    consumer = Tap()
    events.attach(consumer)
    events_module.set_enforcement(events_module.FORGIVING)

    events.emit(lambda: Conversational(stage=Identifier("asr")))

    assert only(consumer).payload == {
        "event": SCHEMA_VIOLATION,
        "session": "alpha",
        "device": None,
    }


def test_an_unusable_identity_is_refused_whole_in_strict_mode() -> None:
    """A conversation record missing the session it belongs to is a
    shape the declaration denies exists, so an emission whose identity
    could not be built is refused rather than half-emitted."""
    events = SessionEvents("has a space", clock=lambda: 1.0)
    consumer = Tap()
    events.attach(consumer)
    events_module.set_enforcement(events_module.STRICT)

    with pytest.raises(EventSchemaError):
        events.emit(lambda: Conversational(stage=Identifier("asr")))

    assert consumer.seen == []


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


REFUSING = (
    ("a construction that raised", a_failing_thunk),
    ("a variant from another channel", a_mismatched_thunk),
)


@pytest.mark.parametrize("build", [one for _, one in REFUSING], ids=[one for one, _ in REFUSING])
def test_strict_refuses_an_emission_the_guard_could_not_build(
    build: object, emitter: ServerEvents, tap: Tap
) -> None:
    events_module.set_enforcement(events_module.STRICT)

    with pytest.raises(EventSchemaError):
        emitter.emit(build)  # type: ignore[arg-type]

    assert tap.seen == []


@pytest.mark.parametrize("build", [one for _, one in REFUSING], ids=[one for one, _ in REFUSING])
def test_forgiving_answers_the_declared_recovery_event(
    build: object,
    emitter: ServerEvents,
    tap: Tap,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A telemetry bug costs a log line, never a reply: the emission is
    replaced by the one event declared for exactly this, and the
    complaint is said once on the emitter's own channel."""
    events_module.set_enforcement(events_module.FORGIVING)

    with caplog.at_level(logging.INFO):
        emitter.emit(build)  # type: ignore[arg-type]

    emission = only(tap)
    assert emission.payload == {"event": SCHEMA_VIOLATION}
    assert emission.level == logging.ERROR
    assert emission.message == SCHEMA_VIOLATION_MESSAGE
    assert emission.args == ()


def test_the_forgiving_complaint_names_the_fault_and_nothing_else(
    emitter: ServerEvents, caplog: pytest.LogCaptureFixture
) -> None:
    """Rendered by equality rather than hunted by substring: the
    complaint's two halves are a fixed label and a fixed code, and there
    is no third thing it may say. Not even the exception's class, which
    `type()` lets a caller name whatever it likes."""
    events_module.set_enforcement(events_module.FORGIVING)

    with caplog.at_level(logging.INFO):
        emitter.emit(a_failing_thunk)

    (complaint,) = [
        one
        for one in caplog.records
        if one.name == CHANNEL and getattr(one, "event", None) is None
    ]
    assert complaint.args == (UNBUILT_LABEL, "construction_failed")


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
    broken_log: None, emitter: ServerEvents, tap: Tap
) -> None:
    """Reporting a refusal is the last thing in the path, and it is on
    the channel the emission was going to. If that channel throws, the
    complaint is lost and the reply is not: the recovery event still
    reaches the taps ahead of the log tap."""
    events_module.set_enforcement(events_module.FORGIVING)

    emitter.emit(a_failing_thunk)

    assert only(tap).payload == {"event": SCHEMA_VIOLATION}


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
        emitter.emit(lambda: ConversationsPruned(sessions=Count(2), days=Count(90)))
    finally:
        detach_server_tap(broken)

    assert only(tap).payload["event"] == "conversations_pruned"


def test_reporting_swallows_whatever_the_channel_does(broken_log: None) -> None:
    """The helper itself, since everything above depends on it: what is
    being protected is a reply, and what is being lost is one diagnostic
    line about a diagnostic line."""
    events_module._report(
        logging.getLogger(CHANNEL), logging.ERROR, "anything %s", "at all"
    )
