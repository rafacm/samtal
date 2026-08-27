"""What a typed declaration is, and what it refuses to be.

The catalog's whole claim is that one declaration owns an event: its
code, its variants, each variant's channel, level, payload shape and
rendering. This suite holds it to that from both sides. From the inside:
a variant derives its payload and its logging specification from its own
fields, absence and null stay different answers, and a value the
sentence renders but the payload does not keep stays out of the record.
From the outside: a declaration that could not describe an emission is
refused at import rather than at the first emit, because a catalog is
read by a lane, a REPL and a server alike and all three should refuse
the same one.

The declarations themselves are pinned by `docs/reference/events.md`,
which is where names, channels, levels, argument order, field names,
types, requiredness and nullability are written down and diffed, and
they are held to being emitted by `test_event_baseline.py`, which drives
every path and matches what it produced against them. What is here is
the machinery those declarations are written with.
"""

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, get_args

import pytest

from tests.support.catalog import scratch_catalog
from vinga_server.events.catalog import (
    CaptureStarted,
    CatalogError,
    ConversationsDropped,
    ConversationsEnabled,
    ConversationsPruned,
    PruneFailed,
    Variant,
    WriteFailed,
    carried_values,
    declaration_of,
    declare,
    payload_shape,
    tokens_of,
    value,
)
from vinga_server.events.values import (
    ABSENT,
    Absent,
    AgentNames,
    ClassName,
    CloseReason,
    ConfiguredPath,
    Count,
    DeviceId,
    Identifier,
    LanguageTag,
    Nothing,
    SessionId,
    ToolSource,
    UnnamedToolSource,
)

CHANNEL = "vinga_server.ota"

SESSION_CHANNEL = "vinga_server.session"


@pytest.fixture(autouse=True)
def _scratch() -> Iterator[None]:
    """Every declaration this file makes is its own. A scratch event
    that reached the production catalog would reach the generated
    reference with it, and would be a declaration no driver
    produces."""
    with scratch_catalog():
        yield


# --- what a variant derives from itself -------------------------------


def test_a_variant_derives_its_payload_from_its_own_fields() -> None:
    built = ConversationsDropped(session=SessionId("alpha"))

    assert built.payload() == {"session": "alpha"}


def test_a_variant_derives_its_sentence_and_its_arguments() -> None:
    """The unrendered template and the ordered arguments `Emission`
    already carries, so `LogTap` and every tap are untouched."""
    built = WriteFailed(failure=ClassName.of(RuntimeError("never repeated")))

    assert built.logged().template == (
        "the conversation store dropped a batch after a write failed (%s)"
    )
    assert built.logged().args == ("RuntimeError",)


def test_the_payload_carries_plain_builtins_rather_than_the_wrappers() -> None:
    """A tap, a JSON formatter and a `%` rendering meet exactly what
    they met before the value types existed.

    Through a capture event since #283, which is where a configured
    path still lives: `conversations_enabled` used to carry one and
    stopped when the store's file became a connection."""
    built = CaptureStarted(
        session=SessionId("a" * 32), path=ConfiguredPath(Path("/var/lib/vinga"))
    )

    assert built.payload() == {"session": "a" * 32, "path": "/var/lib/vinga"}
    assert type(built.payload()["path"]) is str
    assert built.logged().args == ("a" * 32, Path("/var/lib/vinga"))


def test_a_rendered_value_the_payload_does_not_keep_stays_out_of_it() -> None:
    """The retention window is said and not stored: a record repeating
    it on every prune would be storing a setting."""
    built = ConversationsPruned(
        conversations=Count(1), sessions=Count(2), days=Count(90)
    )

    assert built.payload() == {"conversations": 1, "sessions": 2}
    assert built.logged().args == (1, 2, 90)


# --- absence and null are different answers ---------------------------


@dataclass(frozen=True)
class Optional(Variant):
    """A scratch variant, so the two answers are exercised where no
    production declaration needs them yet."""

    CHANNEL: ClassVar[str] = CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = "scratch"

    kept: Identifier
    maybe_null: Identifier | None
    maybe_absent: Identifier | Absent


def test_a_nullable_field_keeps_its_key_and_a_missing_one_does_not() -> None:
    """A field that is present and null is a fact the record states; a
    field that is absent is a key the JSON object does not have."""
    declare("scratch_optional", variants=(Optional,))
    both = Optional(
        kept=Identifier("here"), maybe_null=None, maybe_absent=Identifier("also")
    )
    neither = Optional(kept=Identifier("here"), maybe_null=None, maybe_absent=ABSENT)

    assert both.payload() == {"kept": "here", "maybe_null": None, "maybe_absent": "also"}
    assert neither.payload() == {"kept": "here", "maybe_null": None}


def test_the_shape_records_requiredness_and_nullability_separately() -> None:
    declare("scratch_optional", variants=(Optional,))

    shape = {one.name: (one.required, one.nullable) for one in payload_shape(Optional)}

    assert shape == {
        "event": (True, False),
        "kept": (True, False),
        "maybe_null": (True, True),
        "maybe_absent": (False, False),
    }


# --- a declaration that cannot describe an emission is refused --------


def a_variant(**overrides: object) -> type[Variant]:
    """One scratch variant class, built with whatever is wrong with
    it."""
    namespace: dict[str, object] = {
        "CHANNEL": CHANNEL,
        "LEVEL": logging.INFO,
        "TEMPLATE": "said %s",
        "ARGS": ("stage",),
        "__annotations__": {"stage": Identifier},
    }
    namespace.update(overrides)
    return dataclass(frozen=True)(type("Scratch", (Variant,), namespace))  # type: ignore[return-value]


REFUSED = (
    ("an unknown channel", {"CHANNEL": "vinga_server.invented"}, "does not speak on"),
    ("a level no method emits at", {"LEVEL": logging.CRITICAL}, "no emitter method"),
    ("fewer arguments than positions", {"ARGS": ()}, "0 argument"),
    ("an argument it does not declare", {"ARGS": ("missing",)}, "does not declare"),
    (
        "a field the emitter owns",
        {"__annotations__": {"event": Identifier}, "ARGS": ()},
        "the emitter owns",
    ),
    (
        "a value type the vocabulary does not have",
        {"__annotations__": {"stage": str}},
        "declares one value type",
    ),
    (
        "a carried value with no field kind",
        {"__annotations__": {"stage": Nothing}, "ARGS": ()},
        "no field kind",
    ),
    (
        "a rendered value with no argument kind",
        {"__annotations__": {"stage": AgentNames}},
        "no argument kind",
    ),
)


def test_a_value_may_be_rendered_in_two_positions() -> None:
    """Two sentences on this surface say one value twice: an activation
    code is shown and then repeated inside the command an operator is
    told to type, and so is the MAC beside it. Refusing that would make
    a site pass the same value under two names to say one thing."""
    twice = a_variant(ARGS=("stage", "stage"), TEMPLATE="a %s stage, and %s again")

    with scratch_catalog():
        declare("said_twice", variants=(twice,))
        rendered = twice(stage=Identifier("asr")).logged()

    assert rendered.args == ("asr", "asr")


@pytest.mark.parametrize(
    "overrides, says", [(one, two) for _, one, two in REFUSED], ids=[one for one, _, _ in REFUSED]
)
def test_a_declaration_that_could_not_describe_an_emission_is_refused(
    overrides: dict[str, object], says: str
) -> None:
    """Refused for the stated reason rather than merely refused: a
    check that fired for the wrong one would pass this and guard
    nothing."""
    with pytest.raises(CatalogError, match=says):
        declare("scratch_refused", variants=(a_variant(**overrides),))


def test_an_argument_that_may_be_absent_is_refused() -> None:
    """A sentence position has to be filled. An omittable field is a
    field, never a `%`."""
    with pytest.raises(CatalogError):
        declare(
            "scratch_absent_argument",
            variants=(a_variant(__annotations__={"stage": Identifier | Absent}),),
        )


# One spelling for the name refusals, printable and dotted so that it
# satisfies no `event_name` syntax while still being an ordinary string.
SENTINEL = "sk.leak.4a7d2f1e.never-a-real-credential"


@pytest.mark.parametrize(
    "refused",
    [
        SENTINEL,
        "Conversations",
        "conversations enabled",
        "1_leading_digit",
        "a" * 65,
        "",
        7,
    ],
    ids=[
        "a credential-shaped name",
        "an uppercase name",
        "a name with a space",
        "a name starting with a digit",
        "a name past its length",
        "the empty name",
        "a name that is not a string",
    ],
)
def test_a_name_the_payload_could_not_carry_is_refused(refused: object) -> None:
    """The payload carries the event as an `EventName`, so a catalog
    admitting a name that field would refuse would declare an event
    nothing could emit."""
    with pytest.raises(CatalogError, match="event_name syntax"):
        declare(refused, variants=(a_variant(),))  # type: ignore[arg-type]


def test_a_refused_name_is_not_echoed_back() -> None:
    """The rule the whole surface keeps: a name that did not pass is
    caller-supplied bytes, so the refusal says what the rule is and
    never what was passed. By equality, since absence alone proves only
    that this spelling did not appear."""
    with pytest.raises(CatalogError) as raised:
        declare(SENTINEL, variants=(a_variant(),))

    assert raised.value.args == ("an event name has to match the event_name syntax",)
    assert SENTINEL not in repr(raised.value)


def test_a_variant_that_is_not_frozen_is_refused() -> None:
    """A variant is a value. The emitter constructs it inside the guard
    and derives the payload and the arguments from its fields, so a
    mutable one could be rewritten between the derivation and the
    dispatch."""
    mutable = dataclass(frozen=False)(
        type(
            "Mutable",
            (Variant,),
            {
                "CHANNEL": CHANNEL,
                "LEVEL": logging.INFO,
                "TEMPLATE": "said %s",
                "ARGS": ("stage",),
                "__annotations__": {"stage": Identifier},
            },
        )
    )

    with pytest.raises(CatalogError, match="frozen dataclass"):
        declare("scratch_mutable", variants=(mutable,))


def test_a_variant_that_is_not_a_dataclass_at_all_is_refused() -> None:
    class Plain(Variant):
        CHANNEL = CHANNEL
        LEVEL = logging.INFO
        TEMPLATE = "said nothing"

    with pytest.raises(CatalogError, match="frozen dataclass"):
        declare("scratch_plain", variants=(Plain,))


def test_an_event_declared_twice_is_refused() -> None:
    with pytest.raises(CatalogError):
        declare("conversations_enabled", variants=(ConversationsEnabled,))


def test_a_variant_belonging_to_two_events_is_refused() -> None:
    with pytest.raises(CatalogError):
        declare("scratch_second_owner", variants=(ConversationsEnabled,))


def test_an_event_with_no_variant_is_refused() -> None:
    with pytest.raises(CatalogError):
        declare("scratch_empty", variants=())


# --- the session channel, whose base is three values ------------------


@dataclass(frozen=True)
class Conversational(Variant):
    """A scratch session-channel variant. Its sentence opens the way
    every real one does, by rendering a value the emitter owns."""

    CHANNEL: ClassVar[str] = SESSION_CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = "session %s: %s"
    ARGS: ClassVar[tuple[str, ...]] = ("session", "stage")

    stage: Identifier


def test_the_session_base_is_the_three_values_a_conversation_carries() -> None:
    """The event's name, the session it belongs to, and the device it is
    with; the last nullable, because the bad-Device-Id rejection names
    no device."""
    declare("scratch_conversational", variants=(Conversational,))

    shape = {
        one.name: (one.type.__name__, one.required, one.nullable)
        for one in payload_shape(Conversational)
    }

    assert shape == {
        "event": ("EventName", True, False),
        "session": ("SessionId", True, False),
        "device": ("DeviceId", True, True),
        "stage": ("Identifier", True, False),
    }


def test_a_sentence_may_render_a_value_the_emitter_owns() -> None:
    """Every session sentence opens with the session id, and the session
    id is the emitter's to know rather than a value thirty sites
    restate. The variant's own fields fill the rest."""
    declare("scratch_conversational", variants=(Conversational,))
    built = Conversational(stage=Identifier("asr"))

    logged = built.logged(
        {"session": SessionId("alpha"), "device": DeviceId("aa:bb:cc:dd:ee:ff")}
    )

    assert logged.args == ("alpha", "asr")
    assert built.payload() == {"stage": "asr"}


def test_a_variant_that_renders_a_base_value_still_may_not_declare_one() -> None:
    """Rendering is not owning. A variant declaring `session` would be a
    site choosing the identity the emitter contributes."""
    with pytest.raises(CatalogError, match="the emitter owns"):
        declare(
            "scratch_owned",
            variants=(
                a_variant(
                    CHANNEL=SESSION_CHANNEL,
                    __annotations__={"session": SessionId},
                    ARGS=("session",),
                ),
            ),
        )


# --- a value the variant IS ------------------------------------------


@dataclass(frozen=True)
class Latched(Variant):
    """A scratch variant whose token is not a parameter: this shape says
    one reason and no other."""

    CHANNEL: ClassVar[str] = CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = "closed"

    reason: CloseReason = value(fixed=CloseReason.DRAIN)


def test_a_fixed_value_is_carried_and_cannot_be_passed() -> None:
    """A caller that cannot pass it cannot pass the wrong one, which is
    stronger than the registry's per-variant token set was."""
    declare("scratch_latched", variants=(Latched,))

    assert Latched().payload() == {"reason": "drain"}
    with pytest.raises(TypeError):
        Latched(reason=CloseReason.IDLE)  # type: ignore[call-arg]


def test_a_fixed_token_narrows_the_declared_set_to_the_one_it_says() -> None:
    """A shared enumeration would have widened every variant's declared
    set to the whole of it; a fixed value declares the member the
    variant actually carries, which is what the registry spelled out."""
    declaration = declare("scratch_latched", variants=(Latched,))
    described = declaration_of(Latched)

    assert described is declaration

    (reason,) = [one for one in carried_values(Latched) if one.name == "reason"]

    assert tokens_of(reason) == frozenset({"drain"})
    assert len(frozenset(CloseReason)) > 1


# --- and every value is the type its field declares -------------------
#
# The annotations are the contract and nothing enforces them where a
# variant is built: mypy runs strict over the events package only, so
# every emit site outside it is unchecked, and a frozen dataclass takes
# whatever it is handed. `verify()` is what closes that, and the
# emitter calls it inside the guard before anything is rendered.


@dataclass(frozen=True)
class Coded(Variant):
    """A scratch variant whose one field declares a bounded machine
    form, so a permissive value type handed to it is a mismatch and
    nothing else is."""

    CHANNEL: ClassVar[str] = CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = "heard %s"
    ARGS: ClassVar[tuple[str, ...]] = ("language",)

    language: LanguageTag


def test_a_variant_holding_the_type_it_declares_verifies() -> None:
    declare("scratch_coded", variants=(Coded,))

    Coded(language=LanguageTag("en-US")).verify()


def test_a_value_type_the_field_did_not_declare_is_refused() -> None:
    """A value type is only a claim about provenance while the field
    holding it is the one that declared it. `Identifier` admits any
    non-blank string, so one handed to a field declared `LanguageTag`
    would put whatever an engine answered with onto the surface under a
    name that promises a bounded code."""
    declare("scratch_coded", variants=(Coded,))

    with pytest.raises(CatalogError, match="Coded.language is a LanguageTag"):
        Coded(language=Identifier("whatever the engine said")).verify()


def test_a_refusal_names_the_field_and_the_type_and_not_the_value() -> None:
    """By equality, because absence alone proves only that this
    spelling did not appear. All three of the words it says are this
    module's own."""
    declare("scratch_coded", variants=(Coded,))

    with pytest.raises(CatalogError) as raised:
        Coded(language=Identifier(SENTINEL)).verify()

    assert raised.value.args == ("Coded.language is a LanguageTag",)


def test_a_null_in_a_field_that_is_not_nullable_is_refused() -> None:
    declare("scratch_coded", variants=(Coded,))

    with pytest.raises(CatalogError, match="not nullable"):
        Coded(language=None).verify()  # type: ignore[arg-type]


def test_an_absence_in_a_field_that_is_required_is_refused() -> None:
    """`Absent` is a value like any other at runtime, so a site that
    passed one where the declaration requires a value would otherwise
    drop a key the declaration says is always there."""
    declare("scratch_coded", variants=(Coded,))

    with pytest.raises(CatalogError, match="is required"):
        Coded(language=ABSENT).verify()  # type: ignore[arg-type]


@dataclass(frozen=True)
class Sourced(Variant):
    """A scratch variant declaring the wider of a narrowing pair."""

    CHANNEL: ClassVar[str] = CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = "called %s"
    ARGS: ClassVar[tuple[str, ...]] = ("source",)

    source: ToolSource


def test_a_narrowed_member_still_satisfies_the_field_it_narrows() -> None:
    """A narrowing names the parent's own members rather than restating
    their values, so a field declaring the whole enumeration takes every
    member the narrowed annotation admits, and the narrowing is what
    refuses the members its own variant may not say."""
    declare("scratch_sourced", variants=(Sourced,))

    for member in get_args(UnnamedToolSource):
        Sourced(source=member).verify()
    Sourced(source=ToolSource.BUILTIN).verify()


# --- the declaration is what names the event --------------------------


def test_a_variant_answers_which_event_it_is_a_shape_of() -> None:
    """The lookup the emitter makes, and the reason a caller never
    spells an event name."""
    assert declaration_of(WriteFailed).name == "conversations_failed"
    assert declaration_of(PruneFailed).name == "conversations_failed"
    assert declaration_of(ConversationsPruned).name == "conversations_pruned"


def test_a_type_that_is_not_a_declared_variant_is_refused() -> None:
    with pytest.raises(CatalogError):
        declaration_of(Variant)
