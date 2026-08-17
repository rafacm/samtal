"""What happens to an emission that is not what the registry says.

M1 declared every event; this is the milestone where the emitters read
the declarations, so the whole matrix lives here: one violation class at
a time, in both modes, because the two modes are two different promises.

Strict is the lanes' promise, and it is that a violation stops the run.
Forgiving is production's, and it is the opposite: a telemetry bug never
costs a reply. Forgiving is not a list of special cases either, it is
one algorithm. Select the variant by registry-owned dimensions (the
emitter's channel, then the declared templates, then the level), drop
the caller's sentence and arguments whatever was wrong, rebuild the
payload field by field against the variant, then hold the rebuilt
payload to that variant's field table again. What survives that is
dispatched as its own event with recovery's sentence; what cannot get
there becomes the declared `schema_violation` event, built fresh, so a
hostile name, key, value, message or argument in the refused call
reaches nothing.

The sentence goes unconditionally, and the case that says why is here:
one credential can be an undeclared field key and a lawful argument of
the same call at once, so a recovery that kept the sentence whenever
the arguments happened to validate would drop the value from the
payload and print it anyway.

The declarations below are this suite's own, installed through the
schema seam, because a matrix keyed to production events would be a
matrix that changes whenever the surface does. The channels are real
ones, so the recovery event's own declaration covers them.
"""

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from samtal_server import events
from samtal_server.events import (
    GUARD_MESSAGE,
    REFUSAL_MESSAGE,
    SAFE_MESSAGE,
    UNDECLARED_LABEL,
    Emission,
    EventSchemaError,
    ServerEvents,
    SessionEvents,
    attach_server_tap,
    detach_server_tap,
)
from samtal_server.events_schema import (
    MAC,
    QUOTED_TOOL_NAME,
    REGISTRY,
    SCHEMA_VIOLATION,
    SCHEMA_VIOLATION_MESSAGE,
    SESSION_CHANNEL,
    SESSION_ID,
    EventSpec,
    EventVariant,
    arg_composed,
    arg_count,
    arg_id,
    arg_identifier,
    arg_path,
    arg_whole,
    id_list,
    identifier,
    identifier_list,
    machine_id,
    server_payload,
    session_payload,
    sources,
    token,
    whole,
)
from tests.support.events import fields_of
from tests.support.schema import scratch_registry

# Real channels, both of them, so `schema_violation`'s own declaration
# (one variant per channel) covers whatever the recovery emits.
CHANNEL = "samtal_server.ota"
OTHER_CHANNEL = "samtal_server.ws"

SESSION = "sess01"

REASONS = ("fast", "slow")

MEASURED = EventSpec(
    "measured",
    variants=(
        EventVariant(
            channel=CHANNEL,
            level=logging.INFO,
            message="measured %s in %d ms",
            args=(arg_identifier(), arg_whole()),
            fields=server_payload(
                stage=identifier(),
                duration_ms=whole(),
                reason=token(REASONS),
                note=identifier(required=False),
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
                device=machine_id(MAC, nullable=True),
            ),
        ),
    ),
)

OPENED = EventSpec(
    "opened",
    variants=(
        EventVariant(
            channel=SESSION_CHANNEL,
            level=logging.INFO,
            message="session %s opened",
            args=(arg_id(SESSION_ID),),
            fields=session_payload(agent=identifier()),
        ),
    ),
)

WROTE = EventSpec(
    "wrote",
    variants=(
        EventVariant(
            channel=CHANNEL,
            level=logging.INFO,
            message="wrote %s under %s",
            args=(arg_path(), arg_composed(QUOTED_TOOL_NAME)),
            fields=server_payload(),
        ),
    ),
)

SPECS = (MEASURED, LISTED, OPENED, WROTE, REGISTRY[SCHEMA_VIOLATION])


@pytest.fixture(autouse=True)
def _scratch_schema() -> Iterator[None]:
    with scratch_registry(SPECS):
        yield


@pytest.fixture(autouse=True)
def _mode() -> Iterator[None]:
    """Whatever a test chooses, the next one starts where the module
    does. The mode is process-wide state, and the lanes are strict."""
    restored = events.enforcement()
    try:
        yield
    finally:
        events.set_enforcement(restored)


def strictly() -> None:
    events.set_enforcement(events.STRICT)


def forgivingly() -> None:
    events.set_enforcement(events.FORGIVING)


class Tap:
    """A consumer, so a claim about what reached the taps is asserted
    rather than inferred from the log."""

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


def emitter(channel: str = CHANNEL) -> ServerEvents:
    return ServerEvents(channel)


def conversation() -> SessionEvents:
    events_of = SessionEvents(SESSION)
    events_of.device = "aa:bb:cc:dd:ee:ff"
    return events_of


def lawful(events_of: ServerEvents, **replaced: object) -> None:
    """The `measured` emission as its declaration has it, with the named
    parts replaced. Every refusal below is one edit away from a line
    that passes, which is what makes the edit the thing under test."""
    fields: dict[str, object] = {
        "stage": "asr",
        "duration_ms": 12,
        "reason": "fast",
    }
    fields.update(replaced)
    events_of.info("measured %s in %d ms", "asr", 12, event="measured", **fields)


def complaints(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    """The refusals, which are plain sentences rather than events: an
    event would go back through the taps, and the taps are what a
    refusal is about."""
    return [record for record in caplog.records if not hasattr(record, "event")]


def emitted(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if hasattr(record, "event")]


def only_complaint(caplog: pytest.LogCaptureFixture) -> logging.LogRecord:
    (record,) = complaints(caplog)
    return record


def refused(caplog: pytest.LogCaptureFixture, label: str, summary: str) -> None:
    """One complaint, at ERROR, on the emitter's own channel, saying
    exactly this. Asserted by equality rather than by substring: what a
    complaint may say is the whole point of the exercise."""
    record = only_complaint(caplog)
    assert record.levelno == logging.ERROR
    assert record.msg == REFUSAL_MESSAGE
    assert record.args == (label, summary)


def replaced_by_the_recovery_event(
    caplog: pytest.LogCaptureFixture, **base: object
) -> logging.LogRecord:
    """The declared recovery event, whole: the fixed token, the
    emitter's own identity and nothing else, the fixed sentence, no
    arguments, at ERROR."""
    (record,) = emitted(caplog)
    assert fields_of(record) == {"event": SCHEMA_VIOLATION, **base}
    assert record.msg == SCHEMA_VIOLATION_MESSAGE
    assert record.args == ()
    assert record.levelno == logging.ERROR
    return record


# --- a lawful emission is untouched -----------------------------------


def test_an_emission_that_matches_its_variant_passes_through_unchanged(
    caplog: pytest.LogCaptureFixture, tap: Tap
) -> None:
    """The baseline every case below is an edit of. Enforcement is not
    allowed to be visible in the ordinary case."""
    strictly()
    with caplog.at_level("DEBUG"):
        lawful(emitter(), note="from-cache")

    (record,) = caplog.records
    assert fields_of(record) == {
        "event": "measured",
        "stage": "asr",
        "duration_ms": 12,
        "reason": "fast",
        "note": "from-cache",
    }
    assert record.getMessage() == "measured asr in 12 ms"
    assert tap.seen[0].payload == fields_of(record)


def test_an_optional_field_may_be_absent_and_a_nullable_one_null(
    caplog: pytest.LogCaptureFixture,
) -> None:
    strictly()
    with caplog.at_level("DEBUG"):
        lawful(emitter())
        emitter().info(
            "listed %d",
            2,
            event="listed",
            agents=["one", "two"],
            sessions=["sess01"],
            sources={"persona": 40},
            device=None,
        )

    assert [record.event for record in caplog.records] == ["measured", "listed"]


@pytest.mark.parametrize(
    "name",
    ['secondary"agent', "control\x07bearing", "n" * 4000, " padded "],
    ids=["quoted", "control character", "overlong", "padded"],
)
def test_a_configured_name_is_whatever_configuration_admits(
    caplog: pytest.LogCaptureFixture, name: str
) -> None:
    """`IDENTIFIER` is trusted by PROVENANCE, not by shape. `NonBlankStr`
    is `strip_whitespace=True, min_length=1` and nothing else, so all
    four of these are lawful configuration today and the emitters
    interpolate them into sentences. A length or a character class here
    would turn such a deployment's ordinary traffic into violations, and
    forgiving mode would drop the field and replace the sentence on
    account of a claim configuration never made. Narrowing belongs at
    configuration semantics (#168), not here."""
    strictly()
    with caplog.at_level("DEBUG"):
        lawful(emitter(), stage=name)

    assert fields_of(caplog.records[0])["stage"] == name


@pytest.mark.parametrize("blank", ["", "   ", "\n\t"])
def test_the_one_thing_a_configured_name_may_not_be_is_blank(blank: str) -> None:
    """The floor `NonBlankStr` does state: non-empty once stripped."""
    strictly()
    with pytest.raises(EventSchemaError) as raised:
        lawful(emitter(), stage=blank)

    assert raised.value.args == (
        "the event schema refused an emission of measured: bad_bounds (stage)",
    )


# --- an undeclared event ----------------------------------------------


def test_strict_refuses_an_undeclared_event() -> None:
    strictly()
    with pytest.raises(EventSchemaError) as raised:
        emitter().info("something", event="invented")

    assert raised.value.args == (
        f"the event schema refused an emission of {UNDECLARED_LABEL}: undeclared_event",
    )


def test_forgiving_replaces_an_undeclared_event_with_the_recovery_event(
    caplog: pytest.LogCaptureFixture, tap: Tap
) -> None:
    """The raw name is not retained either: an undeclared name is a
    caller-supplied string like any field value, so the line survives
    without laundering it into the log."""
    forgivingly()
    with caplog.at_level("DEBUG"):
        emitter().info("something", event="invented")

    refused(caplog, UNDECLARED_LABEL, "undeclared_event")
    replaced_by_the_recovery_event(caplog)
    assert tap.seen[0].payload["event"] == SCHEMA_VIOLATION


# --- an undeclared field ----------------------------------------------


def test_strict_refuses_an_undeclared_field() -> None:
    strictly()
    with pytest.raises(EventSchemaError) as raised:
        lawful(emitter(), invented=1)

    assert raised.value.args == (
        "the event schema refused an emission of measured: undeclared_fields (1)",
    )


def test_forgiving_drops_an_undeclared_field_and_keeps_the_event(
    caplog: pytest.LogCaptureFixture, tap: Tap
) -> None:
    """What the rebuild can carry all the way: nothing else was wrong,
    so what is left after the drop is a declared field shape and the
    event rides.

    Its sentence does not. An invalid emission loses the caller's
    message and arguments whatever was wrong with it, because the
    payload and the sentence are not independent: the same value can sit
    in a dropped field and in a lawful argument at once."""
    forgivingly()
    with caplog.at_level("DEBUG"):
        lawful(emitter(), invented=1)

    refused(caplog, "measured", "undeclared_fields (1)")
    (record,) = emitted(caplog)
    assert fields_of(record) == {
        "event": "measured",
        "stage": "asr",
        "duration_ms": 12,
        "reason": "fast",
    }
    assert record.msg == SAFE_MESSAGE
    assert record.args == ()
    assert tap.seen[0].payload == fields_of(record)
    assert tap.seen[0].message == SAFE_MESSAGE
    assert tap.seen[0].args == ()


def test_forgiving_drops_every_offender_rather_than_the_first(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A fail-fast that dropped one field and retained the next would
    make the outcome depend on dict order."""
    forgivingly()
    with caplog.at_level("DEBUG"):
        lawful(emitter(), first=1, second=2, third=3)

    refused(caplog, "measured", "undeclared_fields (3)")
    (record,) = emitted(caplog)
    assert set(fields_of(record)) == {"event", "stage", "duration_ms", "reason"}


# --- a value that is not its kind -------------------------------------


def test_strict_refuses_a_wrong_kind() -> None:
    strictly()
    with pytest.raises(EventSchemaError) as raised:
        lawful(emitter(), duration_ms="twelve")

    assert raised.value.args == (
        "the event schema refused an emission of measured: wrong_kind (duration_ms)",
    )


def test_strict_refuses_a_boolean_where_a_number_belongs() -> None:
    """`True` is an `int` to `isinstance`, which is why the check asks
    about `bool` first: a boolean in a duration field is a bug."""
    strictly()
    with pytest.raises(EventSchemaError):
        lawful(emitter(), duration_ms=True)


def test_strict_refuses_a_null_in_a_field_that_is_not_nullable() -> None:
    strictly()
    with pytest.raises(EventSchemaError) as raised:
        lawful(emitter(), stage=None)

    assert raised.value.args == (
        "the event schema refused an emission of measured: not_nullable (stage)",
    )


def test_forgiving_cannot_rebuild_around_a_required_field_it_dropped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The rebuild drops the offending value; that leaves a required
    field missing, so the final whole-variant check refuses the result
    and the recovery event goes out instead. Nothing dispatched ever has
    a shape the registry denies exists."""
    forgivingly()
    with caplog.at_level("DEBUG"):
        lawful(emitter(), duration_ms="twelve")

    refused(caplog, "measured", "wrong_kind (duration_ms)")
    replaced_by_the_recovery_event(caplog)


# --- a token outside its set ------------------------------------------


def test_strict_refuses_an_unlisted_token() -> None:
    strictly()
    with pytest.raises(EventSchemaError) as raised:
        lawful(emitter(), reason="sideways")

    assert raised.value.args == (
        "the event schema refused an emission of measured: unlisted_token (reason)",
    )


def test_forgiving_replaces_an_emission_whose_token_is_unlisted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    forgivingly()
    with caplog.at_level("DEBUG"):
        lawful(emitter(), reason="sideways")

    refused(caplog, "measured", "unlisted_token (reason)")
    replaced_by_the_recovery_event(caplog)


# --- a required field that is not there -------------------------------


def test_strict_refuses_a_missing_required_field() -> None:
    strictly()
    with pytest.raises(EventSchemaError) as raised:
        emitter().info("measured %s in %d ms", "asr", 12, event="measured", stage="asr")

    assert raised.value.args == (
        "the event schema refused an emission of measured: "
        "missing_field (duration_ms); missing_field (reason)",
    )


def test_forgiving_replaces_an_emission_that_is_missing_a_required_field(
    caplog: pytest.LogCaptureFixture,
) -> None:
    forgivingly()
    with caplog.at_level("DEBUG"):
        emitter().info("measured %s in %d ms", "asr", 12, event="measured", stage="asr")

    refused(
        caplog, "measured", "missing_field (duration_ms); missing_field (reason)"
    )
    replaced_by_the_recovery_event(caplog)


# --- the wrong level and the wrong channel ----------------------------


def test_strict_refuses_the_wrong_level() -> None:
    """A level is part of the compatibility surface: a filter set to
    INFO decides what a collector keeps."""
    strictly()
    with pytest.raises(EventSchemaError) as raised:
        emitter().warning(
            "measured %s in %d ms",
            "asr",
            12,
            event="measured",
            stage="asr",
            duration_ms=12,
            reason="fast",
        )

    assert raised.value.args == (
        "the event schema refused an emission of measured: wrong_level",
    )


def test_forgiving_replaces_an_emission_at_the_wrong_level(
    caplog: pytest.LogCaptureFixture,
) -> None:
    forgivingly()
    with caplog.at_level("DEBUG"):
        emitter().warning(
            "measured %s in %d ms",
            "asr",
            12,
            event="measured",
            stage="asr",
            duration_ms=12,
            reason="fast",
        )

    refused(caplog, "measured", "wrong_level")
    replaced_by_the_recovery_event(caplog)


def test_strict_refuses_an_event_emitted_from_the_wrong_module() -> None:
    """Lawful fields are not enough: the channel is the scope, and an
    event emitted from a module that does not declare it is a record a
    collector would filter into the wrong place."""
    strictly()
    with pytest.raises(EventSchemaError) as raised:
        lawful(emitter(OTHER_CHANNEL))

    assert raised.value.args == (
        "the event schema refused an emission of measured: wrong_channel",
    )


def test_forgiving_replaces_an_emission_on_the_wrong_channel(
    caplog: pytest.LogCaptureFixture,
) -> None:
    forgivingly()
    with caplog.at_level("DEBUG"):
        lawful(emitter(OTHER_CHANNEL))

    refused(caplog, "measured", "wrong_channel")
    (record,) = emitted(caplog)
    assert record.name == OTHER_CHANNEL
    assert fields_of(record) == {"event": SCHEMA_VIOLATION}


# --- the sentence, which is half the record ---------------------------


def test_strict_refuses_a_message_that_is_not_the_declared_template() -> None:
    """Without this, an emission with a lawful payload could say
    anything at all in the sentence a person reads."""
    strictly()
    with pytest.raises(EventSchemaError) as raised:
        emitter().info(
            "measured something else",
            event="measured",
            stage="asr",
            duration_ms=12,
            reason="fast",
        )

    assert raised.value.args == (
        "the event schema refused an emission of measured: wrong_template",
    )


def test_forgiving_replaces_an_emission_whose_template_is_unknown(
    caplog: pytest.LogCaptureFixture,
) -> None:
    forgivingly()
    with caplog.at_level("DEBUG"):
        emitter().info(
            "measured something else",
            event="measured",
            stage="asr",
            duration_ms=12,
            reason="fast",
        )

    refused(caplog, "measured", "wrong_template")
    replaced_by_the_recovery_event(caplog)


# --- the argument tuple -----------------------------------------------


def test_strict_refuses_the_wrong_arity() -> None:
    strictly()
    with pytest.raises(EventSchemaError) as raised:
        emitter().info(
            "measured %s in %d ms",
            "asr",
            event="measured",
            stage="asr",
            duration_ms=12,
            reason="fast",
        )

    assert raised.value.args == (
        "the event schema refused an emission of measured: wrong_arity (2 declared)",
    )


def test_strict_refuses_an_argument_of_the_wrong_kind() -> None:
    """`Emission.args` reaches every tap and the formatter renders them,
    so the tuple is inside the machinery rather than beside it."""
    strictly()
    with pytest.raises(EventSchemaError) as raised:
        emitter().info(
            "measured %s in %d ms",
            "asr",
            "twelve",
            event="measured",
            stage="asr",
            duration_ms=12,
            reason="fast",
        )

    assert raised.value.args == (
        "the event schema refused an emission of measured: wrong_kind (argument 1)",
    )


def test_forgiving_drops_the_arguments_and_keeps_the_event(
    caplog: pytest.LogCaptureFixture, tap: Tap
) -> None:
    """The fields were a declared shape, so the event rides; the
    sentence and the arguments are what went wrong, and they go
    wholesale rather than by position. Nothing the caller wrote is
    rendered."""
    forgivingly()
    with caplog.at_level("DEBUG"):
        emitter().info(
            "measured %s in %d ms",
            "asr",
            "twelve",
            event="measured",
            stage="asr",
            duration_ms=12,
            reason="fast",
        )

    refused(caplog, "measured", "wrong_kind (argument 1)")
    (record,) = emitted(caplog)
    assert fields_of(record)["event"] == "measured"
    assert record.msg == SAFE_MESSAGE
    assert record.args == ()
    assert tap.seen[0].args == ()


def test_a_value_in_a_dropped_field_is_not_rendered_from_an_argument(
    caplog: pytest.LogCaptureFixture, tap: Tap
) -> None:
    """The case that makes the rule unconditional (PR #169's review).

    One value, used as an undeclared field key AND as its value AND as a
    lawful `IDENTIFIER` argument of the same call. Every part of the
    payload half is dropped; keeping the sentence because the argument
    independently validated would render it anyway, out of the same call
    that was refused for carrying it."""
    planted = "sk-alias-3e8f1c2b-never-a-real-credential"
    forgivingly()
    with caplog.at_level("DEBUG"):
        emitter().info(
            "measured %s in %d ms",
            planted,
            12,
            event="measured",
            stage="asr",
            duration_ms=12,
            reason="fast",
            **{planted: planted},
        )

    refused(caplog, "measured", "undeclared_fields (1)")
    (record,) = emitted(caplog)
    assert fields_of(record)["event"] == "measured"
    assert planted not in str(vars(record))
    assert planted not in record.getMessage()
    assert not any(planted in str(one.args) for one in tap.seen)
    assert not any(planted in str(one.payload) for one in tap.seen)


# --- the base keys, checked before the merge --------------------------


@pytest.mark.parametrize("key", ["session", "device"])
def test_strict_refuses_a_session_caller_supplying_a_base_key(key: str) -> None:
    """The check is before the merge on purpose: `**fields` used to
    merge after the base fields, so a spread carrying `session=` would
    have replaced the emitter's own identity and still typechecked."""
    strictly()
    with pytest.raises(EventSchemaError) as raised:
        conversation().info(
            "session %s opened",
            SESSION,
            event="opened",
            agent="assistant",
            **{key: "sk-planted-0f1e2d3c-never-a-real-credential"},
        )

    assert raised.value.args == (
        f"the event schema refused an emission of opened: base_key_collision ({key})",
    )


@pytest.mark.parametrize("key", ["session", "device"])
def test_forgiving_keeps_the_emitters_own_identity(
    caplog: pytest.LogCaptureFixture, key: str
) -> None:
    """The emitter's base fields win, so the planted value reaches
    neither the payload nor the sentence; the event itself survives,
    since nothing else about it was wrong."""
    planted = "sk-planted-0f1e2d3c-never-a-real-credential"
    forgivingly()
    with caplog.at_level("DEBUG"):
        conversation().info(
            "session %s opened",
            SESSION,
            event="opened",
            agent="assistant",
            **{key: planted},
        )

    refused(caplog, "opened", f"base_key_collision ({key})")
    (record,) = emitted(caplog)
    assert fields_of(record) == {
        "event": "opened",
        "session": SESSION,
        "device": "aa:bb:cc:dd:ee:ff",
        "agent": "assistant",
    }
    assert planted not in str(vars(record))


def test_a_server_channel_keeps_session_and_device_as_ordinary_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A server emitter has no identity to protect, so those two names
    are declarable there like any other."""
    strictly()
    with caplog.at_level("DEBUG"):
        emitter().info(
            "listed %d",
            1,
            event="listed",
            agents=["one"],
            sessions=["sess01"],
            sources={},
            device="aa:bb:cc:dd:ee:ff",
        )

    (record,) = caplog.records
    assert fields_of(record)["device"] == "aa:bb:cc:dd:ee:ff"


# --- the list kinds and the one structured kind -----------------------


def test_strict_refuses_a_bad_list_element() -> None:
    strictly()
    with pytest.raises(EventSchemaError) as raised:
        emitter().info(
            "listed %d",
            2,
            event="listed",
            agents=["one", 2],
            sessions=[],
            sources={},
            device=None,
        )

    assert raised.value.args == (
        "the event schema refused an emission of listed: bad_element (agents)",
    )


def test_strict_refuses_an_id_list_element_that_misses_its_syntax() -> None:
    strictly()
    with pytest.raises(EventSchemaError) as raised:
        emitter().info(
            "listed %d",
            1,
            event="listed",
            agents=[],
            sessions=["not a session id"],
            sources={},
            device=None,
        )

    assert raised.value.args == (
        "the event schema refused an emission of listed: bad_element (sessions)",
    )


def listing(**replaced: object) -> dict[str, object]:
    return {
        "agents": [],
        "sessions": [],
        "sources": {},
        "device": None,
        **replaced,
    }


SOURCE_KEYS = [
    "persona",
    "fragment:house-style",
    "instructions:assistant",
    "server_instructions:assistant",
    "server_prompt:assistant:1",
    "server_prompt:assistant:12",
]


@pytest.mark.parametrize("key", SOURCE_KEYS)
def test_every_allowed_provenance_form_is_accepted(
    caplog: pytest.LogCaptureFixture, key: str
) -> None:
    strictly()
    with caplog.at_level("DEBUG"):
        emitter().info("listed %d", 1, event="listed", **listing(sources={key: 12}))

    assert fields_of(caplog.records[0])["sources"] == {key: 12}


def test_an_empty_and_a_populated_provenance_mapping_are_both_lawful(
    caplog: pytest.LogCaptureFixture,
) -> None:
    strictly()
    with caplog.at_level("DEBUG"):
        emitter().info("listed %d", 0, event="listed", **listing(sources={}))
        emitter().info(
            "listed %d",
            2,
            event="listed",
            **listing(sources={"persona": 40, "fragment:tone": 12}),
        )

    assert [fields_of(record)["sources"] for record in caplog.records] == [
        {},
        {"persona": 40, "fragment:tone": 12},
    ]


@pytest.mark.parametrize("key", ["memory", "invented:thing", "server_prompt:a:0"])
def test_strict_refuses_a_provenance_key_outside_the_grammar(key: str) -> None:
    """`memory` is the interesting one: it is a provenance token
    elsewhere in the prompt assembly, and `prompt_assembled` reports the
    cached know-how half and excludes the per-round memory read, so a
    `memory` key here is a violation like any unknown prefix."""
    strictly()
    with pytest.raises(EventSchemaError) as raised:
        emitter().info("listed %d", 1, event="listed", **listing(sources={key: 1}))

    assert raised.value.args == (
        "the event schema refused an emission of listed: bad_source_key (sources)",
    )


def test_strict_refuses_a_provenance_value_that_is_not_a_count() -> None:
    strictly()
    with pytest.raises(EventSchemaError) as raised:
        emitter().info(
            "listed %d", 1, event="listed", **listing(sources={"persona": -1})
        )

    assert raised.value.args == (
        "the event schema refused an emission of listed: bad_source_value (sources)",
    )


# --- the two argument kinds no field has ------------------------------


def test_a_configured_path_is_lawful_as_a_path_or_as_a_string(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`PATHLIKE` exists because the capture events render a `Path`
    object, and widening `IDENTIFIER` to admit it would have made the
    tightest kind the loosest one."""
    strictly()
    with caplog.at_level("DEBUG"):
        emitter().info(
            "wrote %s under %s", Path("/data/captures"), ' "remember"', event="wrote"
        )
        emitter().info(
            "wrote %s under %s", "/data/captures", ' "remember"', event="wrote"
        )

    assert [record.args[0] for record in caplog.records] == [
        Path("/data/captures"),
        "/data/captures",
    ]


def test_strict_refuses_a_path_argument_that_is_not_one() -> None:
    strictly()
    with pytest.raises(EventSchemaError) as raised:
        emitter().info("wrote %s under %s", 12, ' "remember"', event="wrote")

    assert raised.value.args == (
        "the event schema refused an emission of wrote: wrong_kind (argument 0)",
    )


@pytest.mark.parametrize(
    "fragment",
    [' "remember"', ' "secondary\\"agent"', ' "remem\nber"', f' "{"n" * 4000}"'],
    ids=["as the builder assembles it", "quoted", "control character", "overlong"],
)
def test_a_composed_fragment_is_held_to_its_structure_and_no_further(
    caplog: pytest.LogCaptureFixture, fragment: str
) -> None:
    """The quoted tool name, exactly as `_tool_named` assembles it,
    leading space and all.

    The last three are the point. A configured name's domain is
    `NonBlankStr`, which admits quotes, control characters and any
    length, so a grammar over one bounds by STRUCTURE (a quoted name, a
    parenthesized tail, a prefix) and never by character class or
    length. Claiming otherwise would refuse a deployment that named an
    agent something unusual, which is lawful configuration today."""
    strictly()
    with caplog.at_level("DEBUG"):
        emitter().info("wrote %s under %s", Path("/data"), fragment, event="wrote")

    assert caplog.records[0].args[1] == fragment


@pytest.mark.parametrize(
    "fragment",
    [" remember", '"remember"', "", ' "remember" and more'],
    ids=["unquoted", "no leading space", "empty", "trailing text"],
)
def test_strict_refuses_a_fragment_outside_its_grammar(fragment: str) -> None:
    """The structure, which is what is left of the claim and is the
    whole of what the builder promises: this fragment is a quoted name
    behind a space, and nothing else at all."""
    strictly()
    with pytest.raises(EventSchemaError) as raised:
        emitter().info("wrote %s under %s", Path("/data"), fragment, event="wrote")

    assert raised.value.args == (
        "the event schema refused an emission of wrote: bad_syntax (argument 1)",
    )


# --- what the complaint is, and where it survives ---------------------


def test_the_complaint_survives_a_root_of_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """ERROR rather than WARNING because `log_level` admits roots above
    WARNING, and a complaint that vanishes under one is no complaint. A
    root of CRITICAL suppresses it along with every other ERROR-class
    diagnostic, which is that operator's explicit choice."""
    forgivingly()
    with caplog.at_level("ERROR"):
        lawful(emitter(), invented=1)

    assert [record.msg for record in complaints(caplog)] == [REFUSAL_MESSAGE]


def test_the_complaint_is_a_plain_sentence_and_not_an_event(
    caplog: pytest.LogCaptureFixture, tap: Tap
) -> None:
    """Not an event, for the reason a failed tap's report is not one: a
    complaint that went back through validation could recurse."""
    forgivingly()
    with caplog.at_level("DEBUG"):
        lawful(emitter(), invented=1)

    complaint = only_complaint(caplog)
    assert complaint.name == CHANNEL
    assert not hasattr(complaint, "event")
    assert [emission.payload["event"] for emission in tap.seen] == ["measured"]


def test_one_complaint_reports_every_violation_of_one_emission(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Multiple simultaneous violations have one defined outcome,
    reached the same way every time, and one line about it."""
    forgivingly()
    with caplog.at_level("DEBUG"):
        emitter().info(
            "measured %s in %d ms",
            "asr",
            "twelve",
            event="measured",
            stage=None,
            reason="sideways",
            invented=1,
        )

    refused(
        caplog,
        "measured",
        "wrong_kind (argument 1); not_nullable (stage); "
        "missing_field (duration_ms); unlisted_token (reason); undeclared_fields (1)",
    )
    replaced_by_the_recovery_event(caplog)


# --- when saying so is itself what breaks -----------------------------


class BrokenHandler(logging.Handler):
    """A logging handler that raises where `logging` does not catch it.

    `handleError` swallows a failure inside `emit`, so a realistic
    broken handler has to fail in `handle`, which `callHandlers` calls
    unwrapped. Which is the point: a handler, a filter and a formatter
    are all code somebody else installed, and a logging call is not the
    inert operation it looks like."""

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


def test_a_broken_log_does_not_cost_the_reply_that_was_refused(
    broken_log: None, tap: Tap
) -> None:
    """Reporting a refusal is the last thing in the path, and it is on
    the channel the emission was going to. If that channel throws, the
    complaint is lost and the reply is not: the emission still comes
    back recovered, which the tap ahead of the log tap can see."""
    forgivingly()
    lawful(emitter(), invented=1)

    assert [emission.payload["event"] for emission in tap.seen] == ["measured"]
    assert tap.seen[0].message == SAFE_MESSAGE


def test_a_broken_log_does_not_cost_the_reply_the_guard_saved(
    broken_log: None, monkeypatch: pytest.MonkeyPatch, tap: Tap
) -> None:
    """The same, one layer further down: the enforcement broke AND the
    channel it would report that on is broken too. The guard's own
    handler is the last one there is, so a logging call that raised
    inside it would leave nothing behind it at all."""
    forgivingly()
    monkeypatch.setattr(events, "_judge", raising_judgement)
    lawful(emitter())

    assert [emission.payload["event"] for emission in tap.seen] == [SCHEMA_VIOLATION]


class BrokenTap:
    """A consumer with a bug in it, which is the only kind the guards
    exist for."""

    def emit(self, emission: Emission) -> None:
        raise RuntimeError("this consumer is broken")


def test_a_broken_tap_reported_on_a_broken_log_still_costs_nothing(
    broken_log: None, tap: Tap
) -> None:
    """The oldest guard in the module, hardened the same way. A tap that
    raises is reported on the emitter's own channel, and that channel is
    where the emission was going: if the log is what broke, the report
    goes straight back onto it. The taps after the broken one still saw
    the event."""
    forgivingly()
    broken = BrokenTap()
    attach_server_tap(broken)
    try:
        lawful(emitter())
    finally:
        detach_server_tap(broken)

    assert [emission.payload["event"] for emission in tap.seen] == ["measured"]


def test_reporting_swallows_whatever_the_channel_does(broken_log: None) -> None:
    """The helper itself, since everything above depends on it."""
    events._report(logging.getLogger(CHANNEL), logging.ERROR, "anything %s", "at all")


# --- the last-resort guard --------------------------------------------


def raising_judgement(*args: object, **kwargs: object) -> None:
    raise RuntimeError("the validator itself is broken")


def test_a_bug_in_the_validator_does_not_raise_on_a_reply_path(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch, tap: Tap
) -> None:
    """The whole enforcement-and-recovery path runs under one guard in
    forgiving mode, candidate selection, validation and rebuild alike.
    The guard does not degrade the caller's payload, because the
    caller's payload is exactly what could not be judged: it builds the
    replacement fresh."""
    forgivingly()
    monkeypatch.setattr(events, "_judge", raising_judgement)
    with caplog.at_level("DEBUG"):
        lawful(emitter())

    complaint = only_complaint(caplog)
    assert complaint.msg == GUARD_MESSAGE
    assert complaint.args == ("RuntimeError",)
    replaced_by_the_recovery_event(caplog)
    assert tap.seen[0].payload == {"event": SCHEMA_VIOLATION}


def test_the_guard_keeps_the_sessions_own_identity_and_nothing_else(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    forgivingly()
    monkeypatch.setattr(events, "_judge", raising_judgement)
    with caplog.at_level("DEBUG"):
        conversation().info(
            "session %s opened", SESSION, event="opened", agent="assistant"
        )

    replaced_by_the_recovery_event(
        caplog, session=SESSION, device="aa:bb:cc:dd:ee:ff"
    )


def test_strict_mode_has_no_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard exists because a reply must not die of telemetry. A
    lane has no reply to lose, and a swallowed bug there is a bug
    nobody finds."""
    strictly()
    monkeypatch.setattr(events, "_judge", raising_judgement)
    with pytest.raises(RuntimeError):
        lawful(emitter())


def test_the_two_internal_paths_are_told_apart_by_what_they_say(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`schema_violation` has exactly two producers, and no ordinary
    emit site among them (the conformance walk asserts that half). Both
    are exercised here, and they are distinguishable: the ordinary
    fallback names the violation, the guard names the failure that
    stopped it being named."""
    forgivingly()
    with caplog.at_level("DEBUG"):
        emitter().info("something", event="invented")
    ordinary = only_complaint(caplog)
    caplog.clear()

    monkeypatch.setattr(events, "_judge", raising_judgement)
    with caplog.at_level("DEBUG"):
        emitter().info("something", event="invented")
    guarded = only_complaint(caplog)

    assert ordinary.msg == REFUSAL_MESSAGE
    assert guarded.msg == GUARD_MESSAGE
    assert ordinary.msg != guarded.msg
