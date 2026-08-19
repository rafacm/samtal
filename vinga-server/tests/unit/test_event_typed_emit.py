"""What the typed emit path produces, and what its guard does.

Two claims. The first is that a typed emission IS the untyped one: same
channel, same level, same unrendered template, same arguments, same
payload keys and values, on the log tap and on every attached tap. It is
asserted side by side in one process, on scratch declarations written
into both sources, because the property belongs to the machinery rather
than to any four events and because a claim resting on a committed file
alone is a claim resting on that file having been generated correctly.
What the store's own five paths produce is the record baseline's.

The second is the construction guard's, and it needs its own pins
because no caller can prove it: a site hands the emitter a thunk and
never learns whether it ran. So both of its refusals (a thunk that
raised, a variant handed to an emitter on another channel) are driven
in both modes, and both modes' behavior is asserted: strict refuses,
forgiving says so once and dispatches the declared `schema_violation`
in the emission's place.

What a refusal is allowed to SAY is the sentinel suite's, next door.
"""

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

import pytest

from tests.support.catalog import scratch_catalog
from tests.support.schema import scratch_registry
from vinga_server import events as events_module
from vinga_server.events import (
    UNBUILT_LABEL,
    Emission,
    EventSchemaError,
    ServerEvents,
    attach_server_tap,
    detach_server_tap,
)
from vinga_server.events.catalog import (
    ConversationsDropped,
    ConversationsPruned,
    Variant,
    declare,
)
from vinga_server.events.values import ConfiguredPath, Count, Identifier, SessionId
from vinga_server.events_schema import (
    SCHEMA_VIOLATION,
    SCHEMA_VIOLATION_MESSAGE,
    EventSpec,
    EventVariant,
    arg_count,
    arg_identifier,
    arg_path,
    count,
    identifier,
    server_payload,
)

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


# --- the same record, either way --------------------------------------
#
# One scratch event, declared twice: once as a typed variant and once as
# the registry declaration an unconverted site emits against. Scratch
# rather than one of the store's own, because the store's have finished
# converting and only one source declares them now, and because what is
# under test is the machinery rather than those four events. Two of
# them, so the asymmetric value type is covered: `ConfiguredPath` is the
# one whose payload field and sentence argument differ.


@dataclass(frozen=True)
class Measured(Variant):
    CHANNEL: ClassVar[str] = SCRATCH_CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = "measured %s in %d ms"
    ARGS: ClassVar[tuple[str, ...]] = ("stage", "duration_ms")

    stage: Identifier
    duration_ms: Count


@dataclass(frozen=True)
class Recording(Variant):
    CHANNEL: ClassVar[str] = SCRATCH_CHANNEL
    LEVEL: ClassVar[int] = logging.WARNING
    TEMPLATE: ClassVar[str] = "recording to %s"
    ARGS: ClassVar[tuple[str, ...]] = ("path",)

    path: ConfiguredPath


SPECS = (
    EventSpec(
        "measured",
        variants=(
            EventVariant(
                channel=SCRATCH_CHANNEL,
                level=logging.INFO,
                message="measured %s in %d ms",
                args=(arg_identifier(), arg_count()),
                fields=server_payload(stage=identifier(), duration_ms=count()),
            ),
        ),
    ),
    EventSpec(
        "recording",
        variants=(
            EventVariant(
                channel=SCRATCH_CHANNEL,
                level=logging.WARNING,
                message="recording to %s",
                args=(arg_path(),),
                fields=server_payload(path=identifier()),
            ),
        ),
    ),
)

PAIRS = (
    (
        "an identifier and a count",
        lambda: Measured(stage=Identifier("asr"), duration_ms=Count(12)),
        lambda one: one.info(
            "measured %s in %d ms",
            "asr",
            12,
            event="measured",
            stage="asr",
            duration_ms=12,
        ),
    ),
    (
        "a configured path",
        lambda: Recording(path=ConfiguredPath(DIRECTORY)),
        lambda one: one.warning(
            "recording to %s",
            DIRECTORY,
            event="recording",
            path=str(DIRECTORY),
        ),
    ),
)


@pytest.mark.parametrize(
    "build, untyped", [(one, two) for _, one, two in PAIRS], ids=[one for one, _, _ in PAIRS]
)
def test_a_typed_emission_is_the_untyped_one(
    build: object, untyped: object, tap: Tap
) -> None:
    """Side by side, in one process, through one emitter: what every
    conversion has to preserve, proved on the machinery rather than
    re-proved per event."""
    with scratch_registry(SPECS):
        emitter = ServerEvents(SCRATCH_CHANNEL)
        emitter.emit(build)  # type: ignore[arg-type]
        untyped(emitter)  # type: ignore[operator]

    typed_emission, untyped_emission = tap.seen

    assert shape(typed_emission) == shape(untyped_emission)


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


# --- the guard, which no caller can prove -----------------------------


@dataclass(frozen=True)
class Elsewhere(Variant):
    """A variant declared on another channel, so that handing it to
    this emitter is the mismatch and nothing else is."""

    CHANNEL: ClassVar[str] = SCRATCH_CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = "said %s"
    ARGS: ClassVar[tuple[str, ...]] = ("stage",)

    stage: Identifier


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
    complaint's two halves are a fixed label and a fixed code with a
    class name, and there is no third thing it may say."""
    events_module.set_enforcement(events_module.FORGIVING)

    with caplog.at_level(logging.INFO):
        emitter.emit(a_failing_thunk)

    (complaint,) = [
        one
        for one in caplog.records
        if one.name == CHANNEL and getattr(one, "event", None) is None
    ]
    assert complaint.args == (UNBUILT_LABEL, "construction_failed (EventValueError)")
