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

The declarations themselves are pinned by the golden inventory
(`test_event_golden.py`), which is where names, channels, levels, field
names, types, requiredness and nullability live. What is here is the
machinery those declarations are written with.
"""

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

import pytest

from tests.support.catalog import scratch_catalog
from vinga_server.events.catalog import (
    CatalogError,
    ConversationsDropped,
    ConversationsEnabled,
    ConversationsPruned,
    PruneFailed,
    Variant,
    WriteFailed,
    declaration_of,
    declare,
    described,
    payload_shape,
)
from vinga_server.events.values import (
    ABSENT,
    Absent,
    ClassName,
    ConfiguredPath,
    Count,
    Identifier,
    SessionId,
)
from vinga_server.events_schema import REGISTRY

CHANNEL = "vinga_server.ota"


@pytest.fixture(autouse=True)
def _scratch() -> Iterator[None]:
    """Every declaration this file makes is its own. A scratch event
    that reached the production catalog would reach the generated
    reference and the golden inventory with it."""
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
    they met before the value types existed."""
    built = ConversationsEnabled(path=ConfiguredPath(Path("/var/lib/vinga")))

    assert built.payload() == {"path": "/var/lib/vinga"}
    assert type(built.payload()["path"]) is str
    assert built.logged().args == (Path("/var/lib/vinga"),)


def test_a_rendered_value_the_payload_does_not_keep_stays_out_of_it() -> None:
    """The retention window is said and not stored: a record repeating
    it on every prune would be storing a setting."""
    built = ConversationsPruned(sessions=Count(2), days=Count(90))

    assert built.payload() == {"sessions": 2}
    assert built.logged().args == (2, 90)


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
    ("the session channel", {"CHANNEL": "vinga_server.session"}, "converts in M2"),
    ("a level no method emits at", {"LEVEL": logging.CRITICAL}, "no emitter method"),
    ("one field rendered twice", {"ARGS": ("stage", "stage")}, "renders one field twice"),
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
)


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


def test_an_event_declared_twice_is_refused() -> None:
    with pytest.raises(CatalogError):
        declare("conversations_enabled", variants=(ConversationsEnabled,))


def test_a_variant_belonging_to_two_events_is_refused() -> None:
    with pytest.raises(CatalogError):
        declare("scratch_second_owner", variants=(ConversationsEnabled,))


def test_an_event_with_no_variant_is_refused() -> None:
    with pytest.raises(CatalogError):
        declare("scratch_empty", variants=())


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


# --- and the catalog describes what the registry described ------------


def test_the_catalog_describes_its_events_exactly_as_the_registry_does() -> None:
    """The conversion's proof, for as long as both sources exist: every
    declaration the catalog carries derives the same `EventSpec` the
    untyped registry declares by hand, field notes and argument kinds
    included. It retires with the registry entries themselves, at which
    point the regenerated reference is what carries the claim."""
    shared = {spec.name: spec for spec in described() if spec.name in REGISTRY}

    assert set(shared) == {
        "conversations_enabled",
        "conversations_dropped",
        "conversations_failed",
        "conversations_pruned",
    }
    for name, spec in shared.items():
        assert spec == REGISTRY[name], name
