"""What a refused emission is allowed to say about itself.

A value type reads what it is handed, and a machine that reads values is
a machine that can repeat them. That is the failure this suite exists to
refuse: the whole point of #155 is keeping far-side bytes off the
retained log, and a refusal that quoted the value it rejected would put
them there through the door it was built to close.

So the diagnostics render catalog-owned identifiers only: a fixed label,
a fixed violation code, and on a value type's own refusal the type and
the constraint it failed. Nothing else, on any surface. A
credential-shaped sentinel therefore goes through every distinct refusal
branch, in both modes, and is hunted in all six places a value could
surface: the exception's `str`, `repr` and `args`, the forgiving
complaint, both shipped log formats, `Emission.args`, and an attached
tap. The complaint's `msg` and `args` and the exception's `args` are
asserted by EQUALITY rather than by substring absence, because a
substring hunt proves only that this spelling did not appear.

The sentinel is shaped so that it satisfies no `ID` syntax and no token
set (the dots see to that) while still being an ordinary printable
string, which is what lets one spelling drive every branch.

Descriptors are the other half, and their model has two classes rather
than one, because a lawful descriptor necessarily reaches the argument
positions that render it: an ADMISSIBLE credential-shaped value appears
in exactly its declared field and argument position on every intended
consumer and nowhere else, and a REJECTED one appears nowhere at all.
The bound is the value type's, applied again at construction whatever
the decision site did, which is what the four sanitization sites next
door are held to from the other end.
"""

import contextlib
import logging
import traceback
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, cast

import pytest

from tests.support.catalog import scratch_catalog
from tests.support.events import fields_of
from vinga_server import events
from vinga_server.events import (
    REFUSAL_MESSAGE,
    SESSION_LOGGER,
    UNBUILT_LABEL,
    UNSTATED_SESSION,
    Emission,
    EventSchemaError,
    ServerEvents,
    SessionEvents,
    attach_server_tap,
    detach_server_tap,
)
from vinga_server.events.catalog import (
    SCHEMA_VIOLATION,
    ConversationsDropped,
    Heard,
    Variant,
    declare,
    value,
)
from vinga_server.events.values import (
    BoardName,
    DeviceId,
    Identifier,
    LanguageTag,
    Real,
    SessionId,
)
from vinga_server.logs import TEXT_FORMAT, JsonFormatter

# One spelling for every branch: printable, so it is an ordinary string
# rather than something a type check would catch anyway, and dotted, so
# it satisfies no declared `ID` syntax and no token set.
SENTINEL = "sk.leak.4a7d2f1e.never-a-real-credential"

# What a bound lets through whole, for the descriptor half.
ADMISSIBLE = "sk-adm-6c1e9a4f-never-a-real-credential"

# What a bound cuts away: a newline is the character that would turn one
# retained record into two.
REJECTED = "sk-rej-8b2d7e0c\nnever-a-real-credential"

CHANNEL = "vinga_server.ota"

DEVICE = "aa:bb:cc:dd:ee:ff"


@dataclass(frozen=True)
class CheckedIn(Variant):
    """A scratch shape carrying the one kind that admits far-side bytes,
    so the descriptor model is driven on the machinery rather than on
    whichever production event happens to have one."""

    CHANNEL: ClassVar[str] = CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = "device %s reported %s"
    ARGS: ClassVar[tuple[str, ...]] = ("device", "board")

    device: DeviceId = value()
    board: BoardName = value()


@pytest.fixture(autouse=True)
def _scratch() -> Iterator[None]:
    """Declared into a catalog of this file's own, so a scratch event
    reaches neither the generated reference nor the golden inventory."""
    with scratch_catalog():
        declare("checked", variants=(CheckedIn,))
        yield


@pytest.fixture(autouse=True)
def _mode() -> Iterator[None]:
    restored = events.enforcement()
    try:
        yield
    finally:
        events.set_enforcement(restored)


class Tap:
    def __init__(self) -> None:
        self.seen: list[Emission] = []

    def emit(self, emission: Emission) -> None:
        self.seen.append(emission)

    def rendered(self) -> str:
        """Everything a consumer was handed, payload and unrendered
        sentence and arguments alike: `Emission.args` reaches a tap as
        the objects themselves, so a claim that a value reaches nobody
        has to be asserted here as well as at the log."""
        return "\n".join(
            f"{one.payload!r}\n{one.message}\n{one.args!r}"
            for one in self.seen
        )


@pytest.fixture
def tap() -> Iterator[Tap]:
    consumer = Tap()
    attach_server_tap(consumer)
    try:
        yield consumer
    finally:
        detach_server_tap(consumer)


def both_formats(caplog: pytest.LogCaptureFixture) -> str:
    """Every record this server wrote, in the human format and in the
    JSON one, with the arguments behind both."""
    human = logging.Formatter(TEXT_FORMAT)
    machine = JsonFormatter()
    return "\n".join(
        f"{record.getMessage()}\n{record.args!r}\n"
        f"{human.format(record)}\n{machine.format(record)}"
        for record in caplog.records
        if record.name.startswith("vinga_server")
    )


def carrying(caplog: pytest.LogCaptureFixture, value: str) -> set[tuple[str, str]]:
    """Every (event, field) pair whose value holds `value`. The positive
    half of the descriptor model asserts this set exactly rather than
    asserting one field and hoping."""
    return {
        (str(fields.get("event")), key)
        for fields in (fields_of(record) for record in caplog.records)
        for key, held in fields.items()
        if isinstance(held, str) and value in held
    }


# --- descriptors, which are the one kind that carries far-side bytes --


def test_an_admissible_descriptor_reaches_exactly_where_it_is_declared(
    caplog: pytest.LogCaptureFixture, tap: Tap
) -> None:
    """The positive half. A lawful descriptor necessarily reaches its
    declared argument position, the rendered sentence and every tap, so
    the claim is containment rather than absence: exactly its own field
    and its own argument, and no other field of any record."""
    events.set_enforcement(events.STRICT)

    with caplog.at_level("DEBUG"):
        ServerEvents(CHANNEL).emit(
            lambda: CheckedIn(device=DeviceId(DEVICE), board=BoardName(ADMISSIBLE))
        )

    assert carrying(caplog, ADMISSIBLE) == {("checked", "board")}
    (record,) = caplog.records
    assert record.args == (DEVICE, ADMISSIBLE)
    assert ADMISSIBLE in both_formats(caplog)
    assert ADMISSIBLE in tap.rendered()
    assert tap.seen[0].args == (DEVICE, ADMISSIBLE)


def test_a_rejected_descriptor_reaches_nothing_at_all(
    caplog: pytest.LogCaptureFixture, tap: Tap
) -> None:
    """The negative half, and the reason the bound is restated on the
    value type rather than trusted to its decision site: a newline in a
    retained record is one line becoming two, and a terminal escape is
    whoever sent it painting an operator's screen."""
    events.set_enforcement(events.FORGIVING)

    with caplog.at_level("DEBUG"):
        ServerEvents(CHANNEL).emit(
            lambda: CheckedIn(device=DeviceId(DEVICE), board=BoardName(REJECTED))
        )

    (complaint,) = [one for one in caplog.records if not hasattr(one, "event")]
    assert complaint.args == (UNBUILT_LABEL, "construction_failed")
    assert carrying(caplog, REJECTED) == set()
    assert REJECTED not in both_formats(caplog)
    assert REJECTED not in tap.rendered()
    (recovered,) = [one for one in caplog.records if hasattr(one, "event")]
    assert fields_of(recovered) == {"event": SCHEMA_VIOLATION}


def test_strict_refuses_a_descriptor_past_its_declared_length() -> None:
    """The bound is per value type, and it is enforced at construction
    whatever the decision site did."""
    events.set_enforcement(events.STRICT)

    with pytest.raises(EventSchemaError) as raised:
        ServerEvents(CHANNEL).emit(
            lambda: CheckedIn(device=DeviceId(DEVICE), board=BoardName("b" * 65))
        )

    assert raised.value.args == (
        REFUSAL_MESSAGE % (UNBUILT_LABEL, "construction_failed"),
    )


# --- the construction guard, on a server channel ----------------------
#
# The refusal every hostile value now earns. A site hands the emitter a
# thunk, so a value that will not do is an argument to a value type's
# constructor and the refusal happens there. What reaches a surface from
# it is a fixed label and a fixed code, and nothing else at all.
#
# Not even the exception's class name, which is the correction PR #217's
# review forced and the case worth stating: a class name looks like the
# safest string in Python, and `type(name, (Exception,), {})` accepts
# any string as one, name validation included. So a thunk that builds
# its exception out of far-side bytes carries them in its class name,
# and a refusal naming the class would print exactly what this suite
# hunts. Both branches below are driven twice for that reason: once with
# the sentinel as a value the construction refuses, and once with the
# sentinel as the NAME of the class the construction raises.

STORE_CHANNEL = "vinga_server.conversations.store"


def a_refused_construction() -> Variant:
    """The sentinel planted where a converted site would put a session
    id, which no `SessionId` admits: the dots see to that."""
    return ConversationsDropped(session=SessionId(SENTINEL))


def a_hostile_construction() -> Variant:
    """The sentinel planted as the exception's own class name.

    Nothing exotic is needed to reach this: `type()` does not validate
    the name it is given, so any code that derives an exception class
    from data it was handed produces one of these. A guard that reported
    the class would report the data."""
    raise type(SENTINEL, (Exception,), {})()


CONSTRUCTIONS = (
    ("the sentinel as a refused value", a_refused_construction),
    ("the sentinel as the exception's class name", a_hostile_construction),
)

REFUSING_CONSTRUCTIONS = pytest.mark.parametrize(
    "build", [one for _, one in CONSTRUCTIONS], ids=[one for one, _ in CONSTRUCTIONS]
)


@REFUSING_CONSTRUCTIONS
def test_strict_refuses_a_construction_without_repeating_its_value(
    build: Callable[[], Variant], caplog: pytest.LogCaptureFixture, tap: Tap
) -> None:
    """`args` whole, so `str` and `repr` are pinned with it, and nothing
    written or dispatched on the way to the refusal."""
    events.set_enforcement(events.STRICT)

    with caplog.at_level("DEBUG"), pytest.raises(EventSchemaError) as raised:
        ServerEvents(STORE_CHANNEL).emit(build)

    assert raised.value.args == (
        "the event schema refused an emission of an event that could not "
        "be built: construction_failed",
    )
    assert SENTINEL not in str(raised.value)
    assert SENTINEL not in repr(raised.value)
    assert SENTINEL not in repr(raised.value.args)
    # The chains as well: an exception raised inside a handler carries
    # the one it was handling, and the refusal is raised outside the
    # handler for exactly this reason.
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert caplog.records == []
    assert tap.seen == []


@REFUSING_CONSTRUCTIONS
def test_forgiving_recovers_a_construction_without_repeating_its_value(
    build: Callable[[], Variant], caplog: pytest.LogCaptureFixture, tap: Tap
) -> None:
    """The complaint by equality, then the sentinel hunted through every
    surface a value could reach: both shipped formats, the arguments
    behind them, and what the taps were handed."""
    events.set_enforcement(events.FORGIVING)

    with caplog.at_level("DEBUG"):
        ServerEvents(STORE_CHANNEL).emit(build)

    (complaint,) = [one for one in caplog.records if not hasattr(one, "event")]
    assert complaint.levelno == logging.ERROR
    assert complaint.msg == REFUSAL_MESSAGE
    assert complaint.args == (UNBUILT_LABEL, "construction_failed")
    # The declared recovery event rode in the emission's place, carrying
    # the fixed token and nothing the thunk was holding.
    (recovered,) = [one for one in caplog.records if hasattr(one, "event")]
    assert fields_of(recovered) == {"event": SCHEMA_VIOLATION}
    assert recovered.args == ()
    assert carrying(caplog, SENTINEL) == set()
    assert SENTINEL not in both_formats(caplog)
    assert SENTINEL not in tap.rendered()

# --- the same guard on the session channel ----------------------------
#
# Where the far side is nearest the telemetry. A conversation's events
# ride beside an utterance, a provider's answer and a device's own
# bytes, and its emitter has two consumers no server channel has: taps a
# session attaches for itself, and a capture writing the decision track
# to disk beside the room audio. So the construction guard is driven
# again here with both attached, because a value that reached either
# would be a value written where nobody is looking for it.
#
# The two branches are the same two: the sentinel as a value a
# construction refuses, and the sentinel as the NAME of the class a
# construction raises.


class Recording:
    """A capture, as the emitter sees one: the three methods, and
    everything the decision track was handed kept."""

    def __init__(self) -> None:
        self.payloads: list[dict[str, object]] = []

    def event(self, payload: dict[str, object], now: float) -> None:
        self.payloads.append(payload)

    def vad(self, speech_ms: float, listening: bool, replying: bool, now: float) -> None:
        return None

    def dropped(self, reason: str, now: float) -> None:
        return None


def a_refused_conversation() -> Variant:
    """The sentinel planted where an engine's detected language goes.

    Far-side by provenance and bounded in shape, which is exactly the
    combination a value type is for: what an ASR answers with is a code,
    and `LanguageTag` admits nothing else.
    """
    return Heard(
        agent=Identifier("poet"),
        duration_s=Real(0.5),
        language=LanguageTag(SENTINEL),
    )


def a_hostile_conversation() -> Variant:
    """The sentinel as the exception's own class name, on the channel
    where a provider's exception is one frame away."""
    raise type(SENTINEL, (Exception,), {})()


CONVERSATIONS = (
    ("the sentinel as a refused value", a_refused_conversation),
    ("the sentinel as the exception's class name", a_hostile_conversation),
)

REFUSING_CONVERSATIONS = pytest.mark.parametrize(
    "build", [one for _, one in CONVERSATIONS], ids=[one for one, _ in CONVERSATIONS]
)


def a_session() -> tuple[SessionEvents, Tap, Recording]:
    """One session's emitter with both of its consumers attached."""
    emitter = SessionEvents("alpha", clock=lambda: 1.0)
    emitter.device = "aa:bb:cc:dd:ee:ff"
    consumer = Tap()
    emitter.attach(consumer)
    capture = Recording()
    emitter.attach_capture(capture)
    return emitter, consumer, capture


@REFUSING_CONVERSATIONS
def test_strict_refuses_a_conversation_construction_without_repeating_it(
    build: Callable[[], Variant], caplog: pytest.LogCaptureFixture
) -> None:
    """`args` whole, so `str` and `repr` are pinned with it, and nothing
    written, dispatched or recorded on the way to the refusal."""
    events.set_enforcement(events.STRICT)
    emitter, consumer, capture = a_session()

    with caplog.at_level("DEBUG"), pytest.raises(EventSchemaError) as raised:
        emitter.emit(build)

    assert raised.value.args == (
        "the event schema refused an emission of an event that could not "
        "be built: construction_failed",
    )
    assert SENTINEL not in str(raised.value)
    assert SENTINEL not in repr(raised.value)
    assert SENTINEL not in repr(raised.value.args)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert caplog.records == []
    assert consumer.seen == []
    assert capture.payloads == []


@REFUSING_CONVERSATIONS
def test_forgiving_recovers_a_conversation_construction_without_repeating_it(
    build: Callable[[], Variant], caplog: pytest.LogCaptureFixture
) -> None:
    """The complaint by equality, then the sentinel hunted through every
    surface it could reach: both shipped formats, the arguments behind
    them, what the session's own tap was handed, and what the capture
    wrote to the decision track.

    The recovery keeps the session's identity, which is this server's
    own minted value rather than anything a far side chose, and carries
    nothing the thunk was holding.
    """
    events.set_enforcement(events.FORGIVING)
    emitter, consumer, capture = a_session()

    with caplog.at_level("DEBUG"):
        emitter.emit(build)

    said = [one for one in caplog.records if one.name == SESSION_LOGGER]
    (complaint,) = [one for one in said if not hasattr(one, "event")]
    assert complaint.levelno == logging.ERROR
    assert complaint.msg == REFUSAL_MESSAGE
    assert complaint.args == (UNBUILT_LABEL, "construction_failed")
    (recovered,) = [one for one in said if hasattr(one, "event")]
    assert fields_of(recovered) == {
        "event": SCHEMA_VIOLATION,
        "session": "alpha",
        "device": "aa:bb:cc:dd:ee:ff",
    }
    assert recovered.args == ()
    assert carrying(caplog, SENTINEL) == set()
    assert SENTINEL not in both_formats(caplog)
    assert SENTINEL not in consumer.rendered()
    assert SENTINEL not in repr(capture.payloads)
    assert capture.payloads, "the capture recorded nothing, so this proves nothing"

# --- and the identity the emitter contributes -------------------------
#
# The half of a conversation record no variant declares. A session id
# and a device MAC are value types like any other, so a session opened
# under a name no `SessionId` admits, or a device id no `normalize_mac`
# would have answered with, refuses inside the guard exactly as a
# variant's own value does.
#
# What a recovery may say about it is the question this covers, and the
# rule is the one the whole surface keeps: a recovery is built from the
# identities that VALIDATED, never from what the emitter was handed.
# Where one did not, the session gets this module's own word and the
# device gets the null the surface already uses for "none was
# understood".

# The sentinel in the two shapes an identity can hold it: dotted, so no
# `session_id` syntax admits it, and hyphenated, so no MAC does.
SESSION_SENTINEL = SENTINEL
DEVICE_SENTINEL = "sk-leak-4a7d2f1e-never-a-real-credential"


def a_lawful_conversation() -> Variant:
    """A variant with nothing wrong with it, so the refusal under test
    is the identity's and nothing else."""
    return Heard(agent=Identifier("poet"), duration_s=Real(0.5))


def a_session_of(session: str, device: str | None) -> tuple[SessionEvents, Tap, Recording]:
    emitter = SessionEvents(session, clock=lambda: 1.0)
    emitter.device = device
    consumer = Tap()
    emitter.attach(consumer)
    capture = Recording()
    emitter.attach_capture(capture)
    return emitter, consumer, capture


UNUSABLE = (
    ("an unusable session id", SESSION_SENTINEL, "aa:bb:cc:dd:ee:ff"),
    ("an unusable device id", "alpha", DEVICE_SENTINEL),
    ("both unusable", SESSION_SENTINEL, DEVICE_SENTINEL),
)

UNUSABLE_IDENTITIES = pytest.mark.parametrize(
    "session, device",
    [(one, two) for _, one, two in UNUSABLE],
    ids=[one for one, _, _ in UNUSABLE],
)


@UNUSABLE_IDENTITIES
def test_strict_refuses_an_unusable_identity_without_repeating_it(
    session: str, device: str, caplog: pytest.LogCaptureFixture
) -> None:
    events.set_enforcement(events.STRICT)
    emitter, consumer, capture = a_session_of(session, device)

    with caplog.at_level("DEBUG"), pytest.raises(EventSchemaError) as raised:
        emitter.emit(a_lawful_conversation)

    assert raised.value.args == (
        "the event schema refused an emission of an event that could not "
        "be built: construction_failed",
    )
    for planted in (SESSION_SENTINEL, DEVICE_SENTINEL):
        assert planted not in str(raised.value)
        assert planted not in repr(raised.value)
        assert planted not in repr(raised.value.args)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert caplog.records == []
    assert consumer.seen == []
    assert capture.payloads == []


@UNUSABLE_IDENTITIES
def test_forgiving_recovers_an_unusable_identity_without_repeating_it(
    session: str, device: str, caplog: pytest.LogCaptureFixture
) -> None:
    """The recovery by equality, so what it DOES carry is pinned rather
    than merely absent, and then the sentinel hunted through every
    surface it could reach: both shipped formats, the arguments behind
    them, the session's own tap and the capture's decision track."""
    events.set_enforcement(events.FORGIVING)
    emitter, consumer, capture = a_session_of(session, device)

    with caplog.at_level("DEBUG"):
        emitter.emit(a_lawful_conversation)

    said = [one for one in caplog.records if one.name == SESSION_LOGGER]
    (complaint,) = [one for one in said if not hasattr(one, "event")]
    assert complaint.args == (UNBUILT_LABEL, "construction_failed")
    (recovered,) = [one for one in said if hasattr(one, "event")]
    assert fields_of(recovered) == {
        "event": SCHEMA_VIOLATION,
        "session": "alpha" if session == "alpha" else UNSTATED_SESSION,
        "device": "aa:bb:cc:dd:ee:ff" if device == "aa:bb:cc:dd:ee:ff" else None,
    }
    assert recovered.args == ()
    for planted in (SESSION_SENTINEL, DEVICE_SENTINEL):
        assert carrying(caplog, planted) == set()
        assert planted not in both_formats(caplog)
        assert planted not in consumer.rendered()
        assert planted not in repr(capture.payloads)
    assert capture.payloads, "the capture recorded nothing, so this proves nothing"

# --- and a value in the wrong field -----------------------------------
#
# The gap a value type alone does not close. A value type is a claim
# about provenance only while the field holding it is the one that
# declared it, and nothing outside this package typechecks a
# construction: mypy runs strict over `events/` and no further, and a
# frozen dataclass takes whatever it is handed.
#
# `Identifier` is the permissive one, deliberately: it admits any
# non-blank string, because a configured name may be anything at all. So
# an `Identifier` handed to a field declared `LanguageTag` is a far
# side's answer arriving under a name that promises a bounded code, and
# `carried()` would have serialized it without a word. The emitter
# verifies every value against its declared type inside the guard, and
# this is the sentinel for it.


def a_misplaced_value() -> Variant:
    """The sentinel in a field that declares something else. Nothing
    exotic: this is what one wrong import or one copied line looks
    like."""
    return Heard(
        agent=Identifier("poet"),
        duration_s=Real(0.5),
        language=Identifier(SENTINEL),  # type: ignore[arg-type]
    )


def test_strict_refuses_a_misplaced_value_without_repeating_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    events.set_enforcement(events.STRICT)
    emitter, consumer, capture = a_session()

    with caplog.at_level("DEBUG"), pytest.raises(EventSchemaError) as raised:
        emitter.emit(a_misplaced_value)

    assert raised.value.args == (
        "the event schema refused an emission of an event that could not "
        "be built: construction_failed",
    )
    assert SENTINEL not in str(raised.value)
    assert SENTINEL not in repr(raised.value)
    assert SENTINEL not in repr(raised.value.args)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert caplog.records == []
    assert consumer.seen == []
    assert capture.payloads == []


def test_forgiving_recovers_a_misplaced_value_without_repeating_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """And the field it was misplaced into reaches nothing either: the
    recovery carries the fixed token and the session's own identity, so
    there is no `language` key for the value to have survived in."""
    events.set_enforcement(events.FORGIVING)
    emitter, consumer, capture = a_session()

    with caplog.at_level("DEBUG"):
        emitter.emit(a_misplaced_value)

    said = [one for one in caplog.records if one.name == SESSION_LOGGER]
    (complaint,) = [one for one in said if not hasattr(one, "event")]
    assert complaint.args == (UNBUILT_LABEL, "construction_failed")
    (recovered,) = [one for one in said if hasattr(one, "event")]
    assert fields_of(recovered) == {
        "event": SCHEMA_VIOLATION,
        "session": "alpha",
        "device": "aa:bb:cc:dd:ee:ff",
    }
    assert carrying(caplog, SENTINEL) == set()
    assert SENTINEL not in both_formats(caplog)
    assert SENTINEL not in consumer.rendered()
    assert SENTINEL not in repr(capture.payloads)
    assert capture.payloads, "the capture recorded nothing, so this proves nothing"


# --- and the one report that is not about a construction --------------
#
# A tap that raises is reported on the emitter's own channel, and that
# report used to name the exception's class. PR #217's review found the
# hole and parked the fix here: a class name looks like the safest
# string in Python, and `type(name, (Exception,), {})` accepts any
# string as one. A tap is by definition code this module does not own,
# and an exporter holding whatever a far side answered it with can raise
# an exception whose NAME is those bytes.


class Hostile:
    """A consumer that fails with an exception built out of the bytes it
    was holding, which is what a real exporter's failure looks like when
    the far side chose them."""

    def emit(self, emission: Emission) -> None:
        raise type(SENTINEL, (Exception,), {})()


def test_a_failing_taps_exception_names_nothing_at_all(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """By equality on the report's own `args`, so the claim is what the
    line says rather than that one spelling is missing from it. The
    tap's class name survives and the exception's does not, which is the
    asymmetry: a tap is an object this server's composition attached,
    and which consumer is broken is the whole of what makes the line
    actionable."""
    events.set_enforcement(events.STRICT)
    hostile = Hostile()
    attach_server_tap(hostile)

    try:
        with caplog.at_level("DEBUG"):
            ServerEvents(CHANNEL).emit(
                lambda: CheckedIn(device=DeviceId(DEVICE), board=BoardName("board"))
            )
    finally:
        detach_server_tap(hostile)

    (report,) = [one for one in caplog.records if not hasattr(one, "event")]
    assert report.args == ("Hostile",)
    assert report.getMessage() == "an event tap (Hostile) failed and was skipped"
    assert SENTINEL not in both_formats(caplog)


def test_a_failing_log_taps_exception_names_nothing_either(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The sharpest case, since the report goes back onto the channel
    that just failed: the emission still reaches the taps ahead of the
    log, and nothing the broken handler raised is written anywhere."""

    class HostileHandler(logging.Handler):
        def handle(self, record: logging.LogRecord) -> bool:
            raise type(SENTINEL, (Exception,), {})()

    events.set_enforcement(events.STRICT)
    channel = logging.getLogger(CHANNEL)
    handler = HostileHandler()
    channel.addHandler(handler)
    consumer = Tap()
    attach_server_tap(consumer)

    try:
        ServerEvents(CHANNEL).emit(
            lambda: CheckedIn(device=DeviceId(DEVICE), board=BoardName("board"))
        )
    finally:
        channel.removeHandler(handler)
        detach_server_tap(consumer)

    assert consumer.seen[0].payload["event"] == "checked"
    assert SENTINEL not in consumer.rendered()
    assert SENTINEL not in both_formats(caplog)


# --- and what the refusal itself carries as an object ------------------
#
# Strict mode is the one place anything leaves this module, and half the
# converted sites emit from inside an `except` arm, because that is
# where a failure is known. Python attaches whatever exception is being
# handled to any exception raised while it is, so a refusal about a
# thunk this module deliberately never looked at used to arrive holding
# the original under `__context__`: its message, its own chain, and
# everything they render as.
#
# The tests below assert the object rather than the text. `raise ...
# from None` would pass a text-only check and fail these, which is the
# distinction that matters: it suppresses the default traceback's
# printing of a context that is still attached and still reachable from
# anything that walks the chain.


def raising_variant() -> Variant:
    """A construction that refuses because the class name it is handed
    is not one: `ClassName.of` is what every converted catch site calls,
    and `type()` accepts any string as a class name."""
    return ConversationsDropped(session=SessionId(SENTINEL))


def chained() -> None:
    """One emit, made from inside a handler for an exception carrying
    the sentinel in its message, its class name and its own cause."""
    try:
        try:
            raise ValueError(f"while reading {SENTINEL}")
        except ValueError as cause:
            raise type(SENTINEL, (Exception,), {})(SENTINEL) from cause
    except Exception:
        ServerEvents(STORE_CHANNEL).emit(raising_variant)


def unchained(error: BaseException) -> str:
    """Everything an exception carries as an object, walked: both chain
    links to the bottom, and the traceback as it would be printed."""
    parts = [str(error), repr(error), repr(error.args)]
    seen = error
    while True:
        following = seen.__cause__ or seen.__context__
        if following is None:
            break
        parts += [str(following), repr(following)]
        seen = following
    parts.append("".join(traceback.format_exception(error)))
    return "\n".join(parts)


def test_a_strict_refusal_carries_neither_a_cause_nor_a_context() -> None:
    """Both, by identity. A context that is merely suppressed from
    rendering is still an object a consumer, a reporter or a debugger
    reaches through one attribute."""
    events.set_enforcement(events.STRICT)

    with pytest.raises(EventSchemaError) as raised:
        chained()

    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert SENTINEL not in unchained(raised.value)


def test_a_forgiving_recovery_from_inside_a_handler_says_nothing_either(
    caplog: pytest.LogCaptureFixture, tap: Tap
) -> None:
    """The other mode on the same path: nothing is raised, so nothing
    can carry a context, and the recovery event and its complaint are
    the fixed pair."""
    events.set_enforcement(events.FORGIVING)

    with caplog.at_level("DEBUG"):
        chained()

    (complaint,) = [one for one in caplog.records if not hasattr(one, "event")]
    assert complaint.args == (UNBUILT_LABEL, "construction_failed")
    assert SENTINEL not in both_formats(caplog)
    assert SENTINEL not in tap.rendered()


class TrackSpy:
    """A capture's own surface, which is the third place a value could
    surface: the decision track is written from the payload before the
    record exists."""

    def __init__(self) -> None:
        self.seen: list[dict[str, object]] = []

    def event(self, payload: dict[str, object], now: float) -> None:
        self.seen.append(payload)

    def vad(self, speech_ms: float, listening: bool, replying: bool, now: float) -> None:
        return None

    def dropped(self, reason: str, now: float) -> None:
        return None


def test_a_conversations_refusal_reaches_no_capture_either(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The session scope, where a capture is attached: forgiving, since
    strict dispatches nothing at all, so what is asserted is that the
    recovery reached the track and the sentinel did not."""
    events.set_enforcement(events.FORGIVING)
    session = SessionEvents("alpha", clock=lambda: 1.0)
    track = TrackSpy()
    session.attach_capture(track)

    with caplog.at_level("DEBUG"):
        try:
            raise type(SENTINEL, (Exception,), {})(SENTINEL)
        except Exception:
            session.emit(
                lambda: Heard(
                    agent=Identifier("assistant"),
                    duration_s=Real(1.0),
                    language=LanguageTag(SENTINEL),
                )
            )

    assert [one["event"] for one in track.seen] == [SCHEMA_VIOLATION]
    assert SENTINEL not in repr(track.seen)
    assert SENTINEL not in both_formats(caplog)


# --- the same claim, on the production paths that make it -------------
#
# The emitter's property is asserted above on a scratch declaration,
# which is where a property of the machinery belongs. What is asserted
# here is that real converted sites have it, because the shape that
# leaks is a site's rather than the emitter's: an emit made while an
# exception is being handled. Twenty-nine sites in the package can do
# it, in two shapes, and one of each is driven.
#
# Every one plants an exception whose class name IS the sentinel, which
# is what makes the refusal happen at all: `ClassName.of` is what a
# catch site hands the failure to, and a name that is not a Python
# identifier is one it refuses.


def hostile() -> BaseException:
    """A failure carrying the sentinel in every place one can: its class
    name, its message, and the cause behind it."""
    try:
        try:
            raise ValueError(f"while reading {SENTINEL}")
        except ValueError as cause:
            raise type(SENTINEL, (Exception,), {})(SENTINEL) from cause
    except Exception as raised:
        return raised


def carries_nothing(raised: BaseException, caplog: pytest.LogCaptureFixture,
                    consumer: Tap) -> None:
    """One escaping refusal, held to the whole claim: no cause, no
    context, and the sentinel in no record, no argument, no shipped
    format and no consumer's copy."""
    assert isinstance(raised, EventSchemaError)
    assert raised.__cause__ is None
    assert raised.__context__ is None
    assert SENTINEL not in unchained(raised)
    assert SENTINEL not in both_formats(caplog)
    assert SENTINEL not in consumer.rendered()


async def test_a_filler_that_will_not_synthesize_refuses_carrying_nothing(
    caplog: pytest.LogCaptureFixture, tap: Tap
) -> None:
    """The first shape: an emit lexically inside the `except` arm, in a
    library the boot calls. `build_agent_fillers` catches around a whole
    synthesis, so what it holds is whatever a voice provider or its
    transport raised."""
    from dataclasses import replace as replace_field

    from tests.support.configs import masked_config
    from vinga_server.filler import build_agent_fillers
    from vinga_server.providers import build_agent_providers

    class HostileTts:
        sample_rate = 24000

        def synthesize(self, text: str) -> object:
            raise hostile()

    events.set_enforcement(events.STRICT)
    config = masked_config()
    providers = build_agent_providers(config)
    providers["poet"] = replace_field(providers["poet"], tts=cast(Any, HostileTts()))

    with caplog.at_level("DEBUG"), pytest.raises(EventSchemaError) as raised:
        await build_agent_fillers(config, providers)

    carries_nothing(raised.value, caplog, tap)


def test_a_failing_api_request_refuses_carrying_nothing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, tap: Tap
) -> None:
    """The same shape on a request path, where what the handler caught
    is whatever a request put in front of it."""
    from fastapi.testclient import TestClient

    from vinga_server.config.api import build_api

    token = "test-api-token-" + "0123456789abcdef" * 2
    api = build_api(token, tmp_path / "db")

    @api.get("/boom")
    def endpoint() -> dict[str, str]:
        raise hostile()

    events.set_enforcement(events.STRICT)

    with caplog.at_level("DEBUG"), pytest.raises(EventSchemaError) as raised:
        TestClient(api).get("/boom", headers={"Authorization": f"Bearer {token}"})

    carries_nothing(raised.value, caplog, tap)


def test_a_failed_capture_write_refuses_carrying_nothing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, tap: Tap
) -> None:
    """The second shape: the emit is not in the arm at all. `_add`
    catches and calls `_disable`, which emits, so the exception is being
    handled a frame further up than the site that reports it."""
    from tests.support.stores import CAPTURE_MANIFEST, store, tone

    events.set_enforcement(events.STRICT)
    opened = 100.0
    capture = store(tmp_path).open("s1", opened, CAPTURE_MANIFEST)
    assert capture is not None

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise hostile()

    # White-box, same reason as the capture pins: the failure under
    # test is the audio writer raising an exception whose class name is
    # the sentinel, and no public call can make a healthy WAV writer do
    # that.
    capture._mic.add = refuse  # type: ignore[method-assign]

    try:
        with caplog.at_level("DEBUG"), pytest.raises(EventSchemaError) as raised:
            capture.microphone(tone(100, 1000), opened)
    finally:
        # `_disable` raises before it closes, since the emit is the first
        # thing it does; the files are this test's to shut.
        with contextlib.suppress(Exception):
            capture.close()

    carries_nothing(raised.value, caplog, tap)
