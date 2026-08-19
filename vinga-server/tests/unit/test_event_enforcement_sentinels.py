"""What a refused emission is allowed to say about itself.

Enforcement reads values, and a machine that reads values is a machine
that can repeat them. That is the failure this suite exists to refuse:
the whole point of #155 is keeping far-side bytes off the retained log,
and a validator whose complaint quoted the value it rejected would put
them there through the door it was built to close. An undeclared event
name and an undeclared field key are caller-supplied strings too, since
a dict built out of far-side data carries far-side bytes in its keys.

So the diagnostics render registry-owned identifiers only: a declared
event or field name, a fixed violation code, a count, an argument
position. Nothing else, on any surface. A credential-shaped sentinel
therefore goes through every distinct diagnostic branch, in both modes,
and is hunted in all six places a value could surface: the exception's
`str`, `repr` and `args`, the forgiving complaint, both shipped log
formats, `Emission.args`, and an attached tap. The complaint's `msg` and
`args` and the exception's `args` are asserted by EQUALITY rather than
by substring absence, because a substring hunt proves only that this
spelling did not appear.

The sentinel is shaped so that it satisfies no `ID` syntax and no token
set (the dots see to that) while still being an ordinary printable
string, which is what lets one spelling drive every branch.

Descriptors are the other half, and their model has two classes rather
than one, because a lawful descriptor necessarily reaches the argument
positions that render it: an ADMISSIBLE credential-shaped value appears
in exactly its declared field and argument position on every intended
consumer and nowhere else, and a REJECTED one appears nowhere at all.
"""

import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import pytest

from tests.support.events import fields_of
from tests.support.schema import scratch_registry
from vinga_server import events
from vinga_server.events import (
    GUARD_MESSAGE,
    REFUSAL_MESSAGE,
    SESSION_LOGGER,
    UNBUILT_LABEL,
    UNDECLARED_LABEL,
    UNSTATED_SESSION,
    Emission,
    EventSchemaError,
    ServerEvents,
    SessionEvents,
    attach_server_tap,
    detach_server_tap,
)
from vinga_server.events.catalog import ConversationsDropped, Heard, Variant
from vinga_server.events.values import Identifier, LanguageTag, Real, SessionId
from vinga_server.events_schema import (
    MAC,
    REGISTRY,
    SCHEMA_VIOLATION,
    SESSION_ID,
    Bounds,
    EventSpec,
    EventVariant,
    arg_count,
    arg_descriptor,
    arg_id,
    arg_identifier,
    arg_whole,
    descriptor,
    id_list,
    identifier,
    identifier_list,
    machine_id,
    server_payload,
    sources,
    token,
    whole,
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

BOARD = Bounds(64)

MEASURED = EventSpec(
    "measured",
    variants=(
        EventVariant(
            channel=CHANNEL,
            level=logging.INFO,
            message="measured %s in %d ms",
            args=(arg_identifier(), arg_whole()),
            fields=server_payload(
                stage=identifier(), duration_ms=whole(), reason=token(("fast", "slow"))
            ),
        ),
    ),
)

LISTED = EventSpec(
    "listed",
    variants=(
        EventVariant(
            channel=CHANNEL,
            level=logging.INFO,
            message="listed %d",
            args=(arg_count(),),
            fields=server_payload(
                agents=identifier_list(),
                sessions=id_list(SESSION_ID),
                sources=sources(),
                device=machine_id(MAC),
            ),
        ),
    ),
)

CHECKED = EventSpec(
    "checked",
    variants=(
        EventVariant(
            channel=CHANNEL,
            level=logging.INFO,
            message="device %s reported %s",
            args=(arg_id(MAC), arg_descriptor(BOARD)),
            fields=server_payload(device=machine_id(MAC), board=descriptor(BOARD)),
        ),
    ),
)

SPECS = (MEASURED, LISTED, CHECKED, REGISTRY[SCHEMA_VIOLATION])

DEVICE = "aa:bb:cc:dd:ee:ff"


@pytest.fixture(autouse=True)
def _scratch_schema() -> Iterator[None]:
    with scratch_registry(SPECS):
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


def lawful(events_of: ServerEvents, **replaced: object) -> None:
    fields: dict[str, object] = {"stage": "asr", "duration_ms": 12, "reason": "fast"}
    fields.update(replaced)
    events_of.info("measured %s in %d ms", "asr", 12, event="measured", **fields)


def listing(events_of: ServerEvents, **replaced: object) -> None:
    fields: dict[str, object] = {
        "agents": ["one"],
        "sessions": ["sess01"],
        "sources": {"persona": 40},
        "device": DEVICE,
    }
    fields.update(replaced)
    events_of.info("listed %d", 1, event="listed", **fields)


# --- one hostile value, every diagnostic branch -----------------------


@dataclass(frozen=True)
class Branch:
    """One way of getting the sentinel in front of the validator, and
    the exact refusal it earns."""

    name: str
    emit: Callable[[ServerEvents], None]
    label: str
    summary: str


BRANCHES = (
    Branch(
        "wrong-kind field value",
        lambda one: lawful(one, duration_ms=SENTINEL),
        "measured",
        "wrong_kind (duration_ms)",
    ),
    Branch(
        "undeclared event name",
        lambda one: one.info("something", event=SENTINEL),
        UNDECLARED_LABEL,
        "undeclared_event",
    ),
    Branch(
        "undeclared spread key",
        lambda one: lawful(one, **{SENTINEL: 1}),
        "measured",
        "undeclared_fields (1)",
    ),
    Branch(
        "sentinel as the message",
        lambda one: one.info(
            SENTINEL, event="measured", stage="asr", duration_ms=12, reason="fast"
        ),
        "measured",
        "wrong_template",
    ),
    Branch(
        "wrong-kind template argument",
        lambda one: one.info(
            "measured %s in %d ms",
            "asr",
            SENTINEL,
            event="measured",
            stage="asr",
            duration_ms=12,
            reason="fast",
        ),
        "measured",
        "wrong_kind (argument 1)",
    ),
    Branch(
        "unlisted token value",
        lambda one: lawful(one, reason=SENTINEL),
        "measured",
        "unlisted_token (reason)",
    ),
    Branch(
        "malformed id syntax",
        lambda one: listing(one, device=SENTINEL),
        "listed",
        "bad_syntax (device)",
    ),
    Branch(
        "bad list element",
        lambda one: listing(one, sessions=[SENTINEL]),
        "listed",
        "bad_element (sessions)",
    ),
    Branch(
        "bad provenance key",
        lambda one: listing(one, sources={SENTINEL: 1}),
        "listed",
        "bad_source_key (sources)",
    ),
)

CASES = pytest.mark.parametrize(
    "branch", BRANCHES, ids=[branch.name for branch in BRANCHES]
)


@CASES
def test_strict_says_only_what_the_registry_owns(
    branch: Branch, caplog: pytest.LogCaptureFixture, tap: Tap
) -> None:
    """The exception's `args` are asserted whole. `str` and `repr` are
    built from `args`, so pinning the tuple pins all three, and the
    equality is what makes this a proof rather than the observation that
    one spelling is missing."""
    events.set_enforcement(events.STRICT)
    expected = REFUSAL_MESSAGE % (branch.label, branch.summary)

    with caplog.at_level("DEBUG"), pytest.raises(EventSchemaError) as raised:
        branch.emit(ServerEvents(CHANNEL))

    assert raised.value.args == (expected,)
    assert str(raised.value) == expected
    assert SENTINEL not in repr(raised.value)
    # And nothing was written or dispatched on the way to the refusal.
    assert caplog.records == []
    assert tap.seen == []


@CASES
def test_forgiving_says_only_what_the_registry_owns(
    branch: Branch, caplog: pytest.LogCaptureFixture, tap: Tap
) -> None:
    """The complaint by equality, then the sentinel hunted through every
    surface a value could reach: both shipped formats, the arguments
    behind them, and what the taps were handed."""
    events.set_enforcement(events.FORGIVING)

    with caplog.at_level("DEBUG"):
        branch.emit(ServerEvents(CHANNEL))

    (complaint,) = [one for one in caplog.records if not hasattr(one, "event")]
    assert complaint.levelno == logging.ERROR
    assert complaint.msg == REFUSAL_MESSAGE
    assert complaint.args == (branch.label, branch.summary)
    assert SENTINEL not in both_formats(caplog)
    assert SENTINEL not in tap.rendered()
    assert not any(SENTINEL in str(one.args) for one in tap.seen)


def test_one_value_in_two_places_is_dropped_from_both(
    caplog: pytest.LogCaptureFixture, tap: Tap
) -> None:
    """Aliasing, which is what makes the sentence's replacement
    unconditional (PR #169's review).

    The same credential is an undeclared field key, that field's value,
    and a perfectly lawful `IDENTIFIER` argument of the same call.
    Judging the two halves independently would drop it from the payload
    for being undeclared and render it into the sentence for being a
    valid identifier, printing on one line what was refused on the
    other. So an invalid emission loses its arguments whatever was wrong
    with it, and the event rides on its field shape alone."""
    events.set_enforcement(events.FORGIVING)

    with caplog.at_level("DEBUG"):
        ServerEvents(CHANNEL).info(
            "measured %s in %d ms",
            SENTINEL,
            12,
            event="measured",
            stage="asr",
            duration_ms=12,
            reason="fast",
            **{SENTINEL: SENTINEL},
        )

    (complaint,) = [one for one in caplog.records if not hasattr(one, "event")]
    assert complaint.args == ("measured", "undeclared_fields (1)")
    # The event survives, which is the half a complaint is not allowed
    # to cost, and carries its declared fields and nothing else.
    (record,) = [one for one in caplog.records if hasattr(one, "event")]
    assert fields_of(record) == {
        "event": "measured",
        "stage": "asr",
        "duration_ms": 12,
        "reason": "fast",
    }
    assert record.args == ()
    assert SENTINEL not in both_formats(caplog)
    assert SENTINEL not in tap.rendered()


def test_the_recovery_keeps_no_part_of_a_wholly_hostile_call(
    caplog: pytest.LogCaptureFixture, tap: Tap
) -> None:
    """A hostile key, value, message and argument together, through the
    ordinary forgiving path with nothing injected. The algorithm has one
    defined outcome and this is it: the selection cannot get past the
    template, so the emission becomes the recovery event, built fresh
    from the fixed token, the emitter's own identity, the fixed sentence
    and no arguments."""
    events.set_enforcement(events.FORGIVING)

    with caplog.at_level("DEBUG"):
        ServerEvents(CHANNEL).info(
            SENTINEL,
            SENTINEL,
            event="measured",
            stage=SENTINEL,
            duration_ms=12,
            reason="fast",
            **{SENTINEL: SENTINEL},
        )

    (complaint,) = [one for one in caplog.records if not hasattr(one, "event")]
    assert complaint.args == ("measured", "wrong_template")
    (record,) = [one for one in caplog.records if hasattr(one, "event")]
    assert fields_of(record) == {"event": SCHEMA_VIOLATION}
    assert record.args == ()
    assert SENTINEL not in both_formats(caplog)
    assert SENTINEL not in tap.rendered()


# --- the same, with the validator itself broken -----------------------


def raising_judgement(*args: object, **kwargs: object) -> None:
    """A bug in the machinery, carrying the sentinel the way a real one
    would: an exception's text is whatever the failing code interpolated
    into it."""
    raise RuntimeError(f"the validator broke on {SENTINEL}")


HOSTILE = {
    "event name": lambda one: one.info("something", event=SENTINEL),
    "spread key": lambda one: lawful(one, **{SENTINEL: 1}),
    "field value": lambda one: lawful(one, duration_ms=SENTINEL),
    "message": lambda one: one.info(SENTINEL, event="measured"),
    "arguments": lambda one: one.info(
        "measured %s in %d ms", SENTINEL, SENTINEL, event="measured"
    ),
}


@pytest.mark.parametrize("shape", list(HOSTILE), ids=list(HOSTILE))
def test_a_broken_validator_leaks_nothing_and_costs_no_reply(
    shape: str,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    tap: Tap,
) -> None:
    """The last-resort guard, crossed with every hostile shape. The
    guard cannot judge the caller's payload, since judging it is exactly
    what failed, so it does not degrade it: it builds a fresh emission
    and reports the failure by class name alone."""
    events.set_enforcement(events.FORGIVING)
    monkeypatch.setattr(events, "_judge", raising_judgement)

    with caplog.at_level("DEBUG"):
        HOSTILE[shape](ServerEvents(CHANNEL))

    (complaint,) = [one for one in caplog.records if not hasattr(one, "event")]
    assert complaint.msg == GUARD_MESSAGE
    assert complaint.args == ("RuntimeError",)
    (record,) = [one for one in caplog.records if hasattr(one, "event")]
    assert fields_of(record) == {"event": SCHEMA_VIOLATION}
    assert SENTINEL not in both_formats(caplog)
    assert SENTINEL not in tap.rendered()


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
        ServerEvents(CHANNEL).info(
            "device %s reported %s",
            DEVICE,
            ADMISSIBLE,
            event="checked",
            device=DEVICE,
            board=ADMISSIBLE,
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
    """The negative half, and the reason the bound is restated at emit
    time rather than trusted: a newline in a retained record is one line
    becoming two, and a terminal escape is whoever sent it painting an
    operator's screen."""
    events.set_enforcement(events.FORGIVING)

    with caplog.at_level("DEBUG"):
        ServerEvents(CHANNEL).info(
            "device %s reported %s",
            DEVICE,
            REJECTED,
            event="checked",
            device=DEVICE,
            board=REJECTED,
        )

    (complaint,) = [one for one in caplog.records if not hasattr(one, "event")]
    assert complaint.args == (
        "checked",
        "bad_bounds (argument 1); bad_bounds (board)",
    )
    assert carrying(caplog, REJECTED) == set()
    assert REJECTED not in both_formats(caplog)
    assert REJECTED not in tap.rendered()


def test_strict_refuses_a_descriptor_past_its_declared_length() -> None:
    """The bound is per field, and it is enforced again here whatever
    the decision site did."""
    events.set_enforcement(events.STRICT)

    with pytest.raises(EventSchemaError) as raised:
        ServerEvents(CHANNEL).info(
            "device %s reported %s",
            DEVICE,
            "b" * 65,
            event="checked",
            device=DEVICE,
            board="b" * 65,
        )

    assert raised.value.args == (
        "the event schema refused an emission of checked: "
        "bad_bounds (argument 1); bad_bounds (board)",
    )


# --- and the typed path's construction guard --------------------------
#
# The same rule, one layer earlier. A converted site hands the emitter a
# thunk, so the value that would have been a field is now an argument to
# a value type's constructor, and a refusal happens there rather than in
# the validator above. What reaches a surface from that refusal is a
# fixed label and a fixed code, and nothing else at all.
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
