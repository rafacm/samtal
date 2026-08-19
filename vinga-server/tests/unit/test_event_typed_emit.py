"""What the typed emit path produces, and what its guard does.

Two claims. The first is that a converted site produces the record its
unconverted self produced: same channel, same level, same unrendered
template, same arguments, same payload keys and values, on the log tap
and on every attached tap. It is asserted here against the untyped call
directly, side by side in one test, because the whole conversion rests
on it and a claim that rests on a committed file alone is a claim that
rests on the file having been generated correctly.

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
    ConversationsEnabled,
    ConversationsPruned,
    Variant,
    WriteFailed,
    declare,
)
from vinga_server.events.values import (
    ClassName,
    ConfiguredPath,
    Count,
    Identifier,
    SessionId,
)
from vinga_server.events_schema import SCHEMA_VIOLATION, SCHEMA_VIOLATION_MESSAGE

CHANNEL = "vinga_server.conversations.store"

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


TYPED = (
    (
        "conversations_enabled",
        lambda: ConversationsEnabled(path=ConfiguredPath(DIRECTORY)),
        lambda one: one.warning(
            "recording conversations to %s",
            DIRECTORY,
            event="conversations_enabled",
            path=str(DIRECTORY),
        ),
    ),
    (
        "conversations_dropped",
        lambda: ConversationsDropped(session=SessionId("alpha")),
        lambda one: one.warning(
            "session %s: the conversation store is behind, dropping events",
            "alpha",
            event="conversations_dropped",
            session="alpha",
        ),
    ),
    (
        "conversations_failed",
        lambda: WriteFailed(failure=ClassName("RuntimeError")),
        lambda one: one.warning(
            "the conversation store dropped a batch after a write failed (%s)",
            "RuntimeError",
            event="conversations_failed",
            failure="RuntimeError",
        ),
    ),
    (
        "conversations_pruned",
        lambda: ConversationsPruned(sessions=Count(2), days=Count(90)),
        lambda one: one.info(
            "conversations: pruned %d session(s) older than %d days",
            2,
            90,
            event="conversations_pruned",
            sessions=2,
        ),
    ),
)


@pytest.mark.parametrize(
    "build, untyped", [(one, two) for _, one, two in TYPED], ids=[one for one, _, _ in TYPED]
)
def test_a_typed_emission_is_the_untyped_one(
    build: object, untyped: object, emitter: ServerEvents, tap: Tap
) -> None:
    """Side by side, in one process, through one emitter: what the
    conversion has to preserve, proved rather than committed."""
    emitter.emit(build)  # type: ignore[arg-type]
    untyped(emitter)  # type: ignore[operator]

    typed_emission, untyped_emission = tap.seen

    assert shape(typed_emission) == shape(untyped_emission)


def test_a_typed_emission_reaches_the_log_on_its_own_channel(
    emitter: ServerEvents, caplog: pytest.LogCaptureFixture
) -> None:
    """The log tap is a consumer like any other, and the record it
    writes is what a deployment keeps."""
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

    CHANNEL: ClassVar[str] = "vinga_server.ota"
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
