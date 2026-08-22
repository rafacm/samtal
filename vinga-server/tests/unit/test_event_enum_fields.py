"""A field declared as the closed set it carries.

A variant used to say "one of these tokens" by naming the `TokenValue`
subclass built for that enumeration, so a set that already existed was
represented twice: once as the members, once as a class holding a string
checked against them. A field annotated with the enumeration itself says
the same thing with the vocabulary a reader already has, and #238 is
that change. This suite is what holds the two shapes to being the same
declaration.

The claims are the ones the wrapper made and one it could not. What a
record carries is a plain `str` and never the member, because a member
is a `str` subclass and a record holding one would put the subclass into
a baseline's argument types. The declared set is whole for a field
annotated with the enumeration and narrowed for one annotated with a
`Literal` over some of its members. `verify()` refuses at emit what the
wrapper's constructor refused at construction, and `_check` refuses at
declaration a fixed member outside the set, which is the part a bare
member cannot check for itself: it is inert data until something looks
at it.

Everything here declares into a catalog of its own, so no scratch event
reaches the generated reference or the golden inventory.
"""

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from typing import ClassVar, Literal

import pytest

from tests.support.catalog import scratch_catalog
from vinga_server import events_docgen
from vinga_server.events.catalog import (
    CatalogError,
    Variant,
    carried_values,
    declare,
    tokens_of,
    value,
)
from vinga_server.events.values import (
    CloseReason,
    Identifier,
    Rejection,
    ToolSource,
    ToolSourceToken,
)

CHANNEL = "vinga_server.ota"

# The narrowing, spelled as a `Literal` over the parent enumeration's
# members rather than as a second enumeration: the members stay the
# parent's, so there is no second list of values to drift.
Unnamed = Literal[ToolSource.DEVICE, ToolSource.UNKNOWN]

# One spelling for the values a refusal may not repeat, printable and
# unmistakable so that its absence from a message is evidence.
SENTINEL = "sk.leak.4a7d2f1e.never-a-real-token"


@pytest.fixture(autouse=True)
def _scratch() -> Iterator[None]:
    with scratch_catalog():
        yield


@dataclass(frozen=True)
class Called(Variant):
    """A scratch variant whose source is the whole enumeration."""

    CHANNEL: ClassVar[str] = CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = "called a %s tool"
    ARGS: ClassVar[tuple[str, ...]] = ("source",)

    source: ToolSource = value(note="Which namespace the call reached into.")


@dataclass(frozen=True)
class CalledThroughTheWrapper(Variant):
    """The same field, declared the way it was before: the wrapper for
    that enumeration, holding a string it checks at construction."""

    CHANNEL: ClassVar[str] = CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = "called a %s tool"
    ARGS: ClassVar[tuple[str, ...]] = ("source",)

    source: ToolSourceToken = value(note="Which namespace the call reached into.")


@dataclass(frozen=True)
class CalledUnnamed(Variant):
    """A scratch variant admitting fewer members than its enumeration
    holds: a call this surface may not name is a device's or an invented
    one, never a builtin."""

    CHANNEL: ClassVar[str] = CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = "called a %s tool"
    ARGS: ClassVar[tuple[str, ...]] = ("source",)

    source: Unnamed = value()


@dataclass(frozen=True)
class Rejected(Variant):
    """A scratch variant that IS its member: this shape says one reason
    and no other, so the field is not a parameter at all."""

    CHANNEL: ClassVar[str] = CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = "rejected"

    reason: Rejection = value(fixed=Rejection.NO_AGENT)


def declared(variant: type[Variant], name: str) -> object:
    (one,) = [held for held in carried_values(variant) if held.name == name]
    return one


# --- what a record carries ---------------------------------------------


def test_a_carried_member_rides_the_payload_as_a_plain_string() -> None:
    """A member is a `str` subclass, so a payload holding one would put
    the subclass's name into a baseline's argument types and its `repr`
    into anything that renders it."""
    declare("scratch_called", variants=(Called,))

    carried = Called(source=ToolSource.MCP).payload()

    assert carried == {"source": "mcp"}
    assert type(carried["source"]) is str


def test_a_rendered_member_reaches_the_sentence_as_a_plain_string() -> None:
    declare("scratch_called", variants=(Called,))

    (rendered,) = Called(source=ToolSource.DEVICE).logged().args

    assert rendered == "device"
    assert type(rendered) is str


def test_a_fixed_member_is_carried_as_a_plain_string_too() -> None:
    declare("scratch_rejected", variants=(Rejected,))

    carried = Rejected().payload()

    assert carried == {"reason": "no_agent"}
    assert type(carried["reason"]) is str


# --- what the declaration admits ---------------------------------------


def test_an_enum_field_declares_its_whole_enumeration() -> None:
    declare("scratch_called", variants=(Called,))

    assert tokens_of(declared(Called, "source")) == frozenset(
        {"builtin", "device", "mcp", "unknown"}
    )


def test_a_literal_narrows_the_declared_set_to_the_members_it_names() -> None:
    declare("scratch_unnamed", variants=(CalledUnnamed,))

    assert tokens_of(declared(CalledUnnamed, "source")) == frozenset(
        {"device", "unknown"}
    )


def test_a_fixed_member_narrows_the_declared_set_to_the_one_it_says() -> None:
    """A shared enumeration would have widened the set to the whole of
    it; a fixed member declares what the variant actually carries."""
    declare("scratch_rejected", variants=(Rejected,))

    assert tokens_of(declared(Rejected, "reason")) == frozenset({"no_agent"})


def test_the_reference_renders_an_enum_field_as_it_rendered_the_wrapper() -> None:
    """The reader-facing claim of the whole change: the two shapes are
    one declaration, so the document cannot tell them apart.

    Whole sections rather than one cell, since a kind, a constraint, a
    requiredness and an argument row all come from the type a field
    names. A scratch declaration is the last the catalog holds, so its
    section runs to the end of the document, where the generator strips
    the blank line every other section ends with.
    """
    declare("scratch_two_shapes", variants=(Called, CalledThroughTheWrapper))

    _, section = events_docgen.reference().split("### `scratch_two_shapes`\n", 1)
    enum_typed, wrapped = section.split("#### Variant ")[1:]

    assert "`TOKEN`" in enum_typed
    assert "one of: `builtin`, `device`, `mcp`, `unknown`" in enum_typed
    assert enum_typed.split("\n", 1)[1].rstrip("\n") == (
        wrapped.split("\n", 1)[1].rstrip("\n")
    )


# --- and what it refuses -----------------------------------------------


def test_a_value_the_field_did_not_declare_is_refused() -> None:
    """`verify()` runs inside the emitter's guard, which is what holds
    an emit site outside the type checker's scope to the annotation."""
    declare("scratch_called", variants=(Called,))

    with pytest.raises(CatalogError) as raised:
        Called(source=Identifier(SENTINEL)).verify()  # type: ignore[arg-type]

    assert raised.value.args == ("Called.source is a ToolSource",)


def test_a_bare_string_is_refused_even_where_it_names_a_member() -> None:
    """A member's value is a string a site could write out, and writing
    it out is what the type is here to stop."""
    declare("scratch_called", variants=(Called,))

    with pytest.raises(CatalogError) as raised:
        Called(source="mcp").verify()  # type: ignore[arg-type]

    assert raised.value.args == ("Called.source is a ToolSource",)


def test_a_member_outside_the_narrowing_is_refused() -> None:
    """The claim the narrowed wrapper made at construction, made at
    emit: the variant that names nothing cannot be built for a
    builtin."""
    declare("scratch_unnamed", variants=(CalledUnnamed,))

    with pytest.raises(CatalogError) as raised:
        CalledUnnamed(source=ToolSource.BUILTIN).verify()  # type: ignore[arg-type]

    assert raised.value.args == ("CalledUnnamed.source is a narrowed ToolSource",)


def test_no_refusal_repeats_what_it_was_holding() -> None:
    """By equality above, and once more over the three together: a
    refusal names the variant, the field and the declared type, all of
    which are this module's own words."""
    declare("scratch_called", variants=(Called,))
    declare("scratch_unnamed", variants=(CalledUnnamed,))
    refused = []
    for built in (
        Called(source=Identifier(SENTINEL)),  # type: ignore[arg-type]
        Called(source=SENTINEL),  # type: ignore[arg-type]
        CalledUnnamed(source=ToolSource.BUILTIN),  # type: ignore[arg-type]
    ):
        with pytest.raises(CatalogError) as raised:
            built.verify()
        refused.append(str(raised.value))

    assert refused
    for said in refused:
        assert SENTINEL not in said
        assert "builtin" not in said


def a_variant(**overrides: object) -> type[Variant]:
    """One scratch variant class, built with whatever is wrong with
    it."""
    namespace: dict[str, object] = {
        "CHANNEL": CHANNEL,
        "LEVEL": logging.INFO,
        "TEMPLATE": "said nothing",
        "ARGS": (),
        "__annotations__": {"reason": Rejection},
    }
    namespace.update(overrides)
    return dataclass(frozen=True)(type("Scratch", (Variant,), namespace))  # type: ignore[return-value]


def test_a_literal_mixing_two_enumerations_is_refused() -> None:
    """It names no closed set: answering with either of them would make
    the reference print a set the field does not admit."""
    with pytest.raises(CatalogError, match="one StrEnum"):
        declare(
            "scratch_mixed",
            variants=(
                a_variant(
                    __annotations__={
                        "reason": Literal[Rejection.NO_AGENT, CloseReason.DRAIN]
                    }
                ),
            ),
        )


def test_a_fixed_member_of_another_enumeration_is_refused_at_declaration() -> None:
    """The wrapper's constructor used to raise at import for a member
    outside its set. A bare member is inert data, so the check moved
    here: without it the first evidence would be a detail-free refusal
    at emit, in a running deployment."""
    with pytest.raises(CatalogError, match="outside its declared tokens"):
        declare(
            "scratch_foreign",
            variants=(a_variant(reason=value(fixed=CloseReason.DRAIN)),),
        )


def test_a_fixed_member_outside_the_narrowing_is_refused_at_declaration() -> None:
    with pytest.raises(CatalogError, match="outside its declared tokens"):
        declare(
            "scratch_outside",
            variants=(
                a_variant(
                    __annotations__={"source": Unnamed},
                    source=value(fixed=ToolSource.BUILTIN),
                ),
            ),
        )
