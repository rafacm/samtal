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

from samtal_server import events
from samtal_server.events import (
    GUARD_MESSAGE,
    REFUSAL_MESSAGE,
    UNDECLARED_LABEL,
    Emission,
    EventSchemaError,
    ServerEvents,
    attach_server_tap,
    detach_server_tap,
)
from samtal_server.events_schema import (
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
from samtal_server.logs import TEXT_FORMAT, JsonFormatter
from tests.support.events import fields_of
from tests.support.schema import scratch_registry

# One spelling for every branch: printable, so it is an ordinary string
# rather than something a type check would catch anyway, and dotted, so
# it satisfies no declared `ID` syntax and no token set.
SENTINEL = "sk.leak.4a7d2f1e.never-a-real-credential"

# What a bound lets through whole, for the descriptor half.
ADMISSIBLE = "sk-adm-6c1e9a4f-never-a-real-credential"

# What a bound cuts away: a newline is the character that would turn one
# retained record into two.
REJECTED = "sk-rej-8b2d7e0c\nnever-a-real-credential"

CHANNEL = "samtal_server.ota"

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
        if record.name.startswith("samtal_server")
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
