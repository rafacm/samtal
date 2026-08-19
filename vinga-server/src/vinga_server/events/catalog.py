"""What this server may say, declared once per event as typed variants.

The registry in `events_schema.py` describes an emission; a catalog
entry IS one. That is the whole difference, and everything else follows
from it. A site used to restate the template, the argument order, the
event name and the field set at every call, and five structures had to
agree for one record to be lawful: the declaration, the call, the pin,
the sidecar entry and the conformance walk's reading of the source.
Here the call constructs the declaration, so there is nothing left for
it to disagree with.

**One declaration per event code, holding a discriminated set of typed
variants.** An event is not one shape: `conversations_failed` says two
different sentences about two different failures under one name, and
the surface has events with variants across channels and levels. So a
declaration names the event and owns its variants, and a caller
constructs the specific variant named after the thing that happened.
Documentation and the golden inventory derive from the enclosing
declaration.

**A variant owns its whole emission.** Its channel, its level, its
exact payload shape (names, types, requiredness, nullability), and its
rendering. `Emission` and `LogTap` are untouched: every variant derives
its logging specification, the unrendered template and the ordered
argument tuple those two already carry, from its own fields. The record
a tap or a log reader sees is therefore the record it saw before, which
is what the committed baseline proves rather than claims.

**Absence and null are different answers.** A field that is present and
null is a fact the record states; a field that is absent is a key the
JSON object does not have. A bare `| None` cannot say which a site
meant, so a variant annotates an omittable field with `Absent` and the
payload builder drops it, while a nullable one keeps its key.

**Documentation facts are declaration metadata.** Notes, the constraint
a value type carries, the syntaxes and the bounds are declared here and
on the value types, never introspected from prose. `events_docgen.py`
renders them through `described()`, which answers the same `EventSpec`
shape the untyped registry answers, so one generator serves both while
the conversion is in flight.

The module imports the value vocabulary and the standard library, and
imports no subsystem: the arrows keep pointing downward.
"""

import logging
import re
from dataclasses import dataclass, field, fields, is_dataclass
from types import UnionType
from typing import Any, ClassVar, Union, get_args, get_origin, get_type_hints

from vinga_server.events.values import (
    Absent,
    ClassName,
    ConfiguredPath,
    Count,
    EventName,
    EventValue,
    EventValueError,
    SessionId,
)
from vinga_server.events_schema import (
    CHANNELS,
    SESSION_CHANNEL,
    ArgSpec,
    EventField,
    EventSpec,
    EventVariant,
)

# The levels an event may be emitted at, which are the four the emitters
# expose as methods.
LEVELS: frozenset[int] = frozenset(
    {logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR}
)

# One `%` conversion in a template. `%%` is the escape and takes no
# argument, which is why it is matched and then discarded rather than
# left to be miscounted.
_CONVERSION = re.compile(r"%(?:%|[-#0 +]*\d*(?:\.\d+)?[hlL]?[a-zA-Z])")


class CatalogError(Exception):
    """A declaration that cannot describe an emission, refused at import.

    Every check this raises is one a reviewer would otherwise have to
    make by eye: an argument list that does not fit its template, a
    field named after one the emitter owns, a variant declared twice.
    Raised at import so a lane, a REPL and a server all refuse the same
    catalog rather than discovering it at the first emission.
    """


@dataclass(frozen=True)
class Logged:
    """One variant's logging specification, unrendered.

    Exactly what `Emission` carries and what `LogTap` hands to
    `Logger.log`: the template with its `%` positions intact and the
    arguments in order. Unrendered because the formatter renders it, and
    because a consumer that only wants the structure must not pay for a
    sentence nobody reads.
    """

    template: str
    args: tuple[Any, ...]


@dataclass(frozen=True)
class Declared:
    """One value a variant declares, as the catalog reads it.

    Derived from the dataclass field and its annotation rather than
    restated beside it: `required` is false where the annotation admits
    `Absent`, `nullable` is true where it admits `None`, and `carried`
    is false for a value the sentence renders that the payload does not
    keep (a retention window is said and not stored).
    """

    name: str
    type: type[EventValue]
    required: bool
    nullable: bool
    carried: bool
    note: str
    rendered_note: str


def value(*, carried: bool = True, note: str = "", rendered_note: str = "") -> Any:
    """Declare one of a variant's values.

    `carried=False` marks a value the sentence renders and the payload
    does not keep, which is the only reason the two lists are not the
    same list. The two notes are the reference's two columns: what the
    field means, and what its `%` position means where the sentence
    needs saying something the field does not.
    """
    return field(
        metadata={"carried": carried, "note": note, "rendered_note": rendered_note}
    )


class Variant:
    """Base for every typed variant.

    A subclass is a frozen dataclass whose fields ARE the emission's
    values, named exactly as the payload's keys: that identity is what
    removes the field table as a separate structure. The class-level
    facts are the ones a value cannot carry, and `ARGS` is the ordered
    subset of the fields the template renders, which is the one thing
    field order cannot say.
    """

    CHANNEL: ClassVar[str]
    LEVEL: ClassVar[int]
    TEMPLATE: ClassVar[str]
    ARGS: ClassVar[tuple[str, ...]] = ()
    NOTE: ClassVar[str] = ""

    def payload(self) -> dict[str, Any]:
        """This variant's own fields, as the plain builtins a record
        carries. The emitter puts the base fields in front of them."""
        built: dict[str, Any] = {}
        for declared in declared_values(type(self)):
            if not declared.carried:
                continue
            held = getattr(self, declared.name)
            if isinstance(held, Absent):
                continue
            built[declared.name] = None if held is None else held.carried()
        return built

    def logged(self) -> Logged:
        """The template and the ordered arguments, derived from the
        fields `ARGS` names."""
        return Logged(
            self.TEMPLATE,
            tuple(
                None if held is None else held.rendered()
                for held in (getattr(self, name) for name in self.ARGS)
            ),
        )


@dataclass(frozen=True)
class Declaration:
    """One event code and every shape it may be emitted in."""

    name: str
    variants: tuple[type[Variant], ...]
    note: str = ""


@dataclass(frozen=True)
class CatalogState:
    """Everything declared so far, as one object.

    One object rather than three module globals, because the three are
    always read and always replaced together: the declarations by name,
    which event owns each variant, and what each variant declares. That
    is also what makes a scratch catalog a swap of one value, which is
    the seam the model's own suite needs and which the untyped
    registry's `set_declared_events` already established.
    """

    declarations: dict[str, Declaration] = field(default_factory=dict)
    owner: dict[type[Variant], Declaration] = field(default_factory=dict)
    values: dict[type[Variant], tuple[Declared, ...]] = field(default_factory=dict)

    def copy(self) -> "CatalogState":
        return CatalogState(
            declarations=dict(self.declarations),
            owner=dict(self.owner),
            values=dict(self.values),
        )


_state = CatalogState()


def installed() -> CatalogState:
    """The catalog the emitters and the reference are reading."""
    return _state


def install(state: CatalogState) -> None:
    """Read from this one instead. Production installs one at import;
    the model's own suite installs a copy around itself, so a scratch
    declaration cannot reach the generated reference."""
    global _state
    _state = state


def declared_values(variant: type[Variant]) -> tuple[Declared, ...]:
    """What one variant declares, in declaration order."""
    return _state.values[variant]


def _read(variant: type[Variant]) -> tuple[Declared, ...]:
    hints = get_type_hints(variant)
    read: list[Declared] = []
    for one in fields(variant):  # type: ignore[arg-type]
        annotation = hints[one.name]
        members = (
            list(get_args(annotation))
            if get_origin(annotation) in (Union, UnionType)
            else [annotation]
        )
        nullable = type(None) in members
        required = Absent not in members
        carried_types = [
            member
            for member in members
            if member is not type(None) and member is not Absent
        ]
        if len(carried_types) != 1 or not (
            isinstance(carried_types[0], type)
            and issubclass(carried_types[0], EventValue)
        ):
            raise CatalogError(
                f"{variant.__name__}.{one.name} declares one value type, "
                f"optionally with None or Absent"
            )
        read.append(
            Declared(
                name=one.name,
                type=carried_types[0],
                required=required,
                nullable=nullable,
                carried=bool(one.metadata.get("carried", True)),
                note=str(one.metadata.get("note", "")),
                rendered_note=str(one.metadata.get("rendered_note", "")),
            )
        )
    return tuple(read)


def _frozen(variant: type[Variant]) -> None:
    """Frozen, and not merely a dataclass.

    A variant is a value: the emitter constructs it inside the guard,
    derives the payload and the arguments from its fields, and hands
    those on. A mutable one could be changed between the derivation and
    the dispatch, and a caller holding a reference to what it just
    emitted could rewrite the record.

    First of all the checks, and before the fields are read at all,
    because reading them is what needs a dataclass: `dataclasses.fields`
    answers a `TypeError` for anything else, which is not the error a
    declaration is told to expect.
    """
    params = getattr(variant, "__dataclass_params__", None)
    if not is_dataclass(variant) or params is None or not params.frozen:
        raise CatalogError(f"{variant.__name__} is a frozen dataclass")


def _check(variant: type[Variant], declared: tuple[Declared, ...]) -> None:
    """Everything about one variant a reviewer would otherwise check by
    eye."""
    where = variant.__name__
    if variant.CHANNEL == SESSION_CHANNEL:
        # The session base carries `session` and `device` as well, and
        # the device id's value type arrives with the session channel's
        # own conversion. Refused rather than half-supported: a base
        # this module cannot describe is a payload shape the golden
        # inventory would have to guess at.
        raise CatalogError(f"{where} rides the session channel, which converts in M2")
    if variant.CHANNEL not in CHANNELS:
        raise CatalogError(f"{where} names a channel this server does not speak on")
    if variant.LEVEL not in LEVELS:
        raise CatalogError(f"{where} names a level no emitter method emits at")
    # The base fields the emitter contributes are the emitter's own, and
    # a variant that declared one would be a site choosing its own
    # identity. On a server channel that is `event` alone: `session` and
    # `device` are ordinary fields there, declared where they are
    # carried, exactly as the untyped registry has them.
    owned = {one.name for one in base_of(variant.CHANNEL)}.intersection(
        one.name for one in declared
    )
    if owned:
        raise CatalogError(f"{where} declares a field the emitter owns: {sorted(owned)}")
    names = {one.name: one for one in declared}
    if len(set(variant.ARGS)) != len(variant.ARGS):
        raise CatalogError(f"{where} renders one field twice")
    for name in variant.ARGS:
        if name not in names:
            raise CatalogError(f"{where} renders {name}, which it does not declare")
        if not names[name].required:
            raise CatalogError(f"{where} renders {name}, which it may not carry at all")
    conversions = sum(
        1 for found in _CONVERSION.findall(variant.TEMPLATE) if found != "%%"
    )
    if conversions != len(variant.ARGS):
        raise CatalogError(
            f"{where} renders {len(variant.ARGS)} argument(s) into a template "
            f"with {conversions} position(s)"
        )


def _named(name: object) -> bool:
    """Whether one string is a lawful event name.

    Asked of `EventName` rather than of a pattern written here, because
    the payload carries the event as an `EventName` and a catalog that
    admitted a name its own payload field would refuse would declare an
    event nothing could emit. The refusal is built after the handler
    ends, so no chain reaches the caller.
    """
    lawful = True
    try:
        EventName(name)  # type: ignore[arg-type]
    except EventValueError:
        lawful = False
    return lawful


def declare(
    name: str, *, variants: tuple[type[Variant], ...], note: str = ""
) -> Declaration:
    """Declare one event and the variants it may be emitted in.

    Registers them, so the emitter can answer "which event is this
    variant" without a site ever naming one, and refuses at import
    anything that could not describe an emission.
    """
    # The syntax first, before anything echoes the name. Every refusal
    # below prints it, which is safe precisely because a name that got
    # past this point is one the `event_name` syntax admits: lowercase,
    # bounded, and this repository's own word. A name that did not is
    # caller-supplied bytes like any other, so its refusal says what the
    # rule is and never what was passed.
    if not _named(name):
        raise CatalogError("an event name has to match the event_name syntax")
    if name in _state.declarations:
        raise CatalogError(f"{name} is declared twice")
    if not variants:
        raise CatalogError(f"{name} declares no variant")
    declaration = Declaration(name=name, variants=variants, note=note)
    for variant in variants:
        if variant in _state.owner:
            raise CatalogError(f"{variant.__name__} belongs to two events")
        _frozen(variant)
        declared = _read(variant)
        _check(variant, declared)
        _state.values[variant] = declared
        _state.owner[variant] = declaration
    _state.declarations[name] = declaration
    return declaration


def catalog() -> dict[str, Declaration]:
    """Every declared event, in declaration order."""
    return dict(_state.declarations)


def declaration_of(variant: type[Variant]) -> Declaration:
    """Which event a variant is a shape of.

    The lookup the emitter makes, and the reason a caller never spells
    an event name: the name is the declaration's, and the declaration is
    reached from the type the caller constructed.
    """
    found = _state.owner.get(variant)
    if found is None:
        raise CatalogError(f"{variant.__name__} is not a declared variant")
    return found


# --- the payload shape, base fields included --------------------------
#
# A variant declares its own fields; the emitter puts the channel's base
# in front of them. The golden inventory and the generated reference
# both need the whole payload, so the base is described here, once,
# rather than by each of them.

_SERVER_BASE: tuple[Declared, ...] = (
    Declared(
        name="event",
        type=EventName,
        required=True,
        nullable=False,
        carried=True,
        note="",
        rendered_note="",
    ),
)


def base_of(channel: str) -> tuple[Declared, ...]:
    """The fields the emitter contributes on one channel."""
    if channel == SESSION_CHANNEL:  # pragma: no cover - refused at declaration
        raise CatalogError("the session channel's base converts in M2")
    return _SERVER_BASE


def payload_shape(variant: type[Variant]) -> tuple[Declared, ...]:
    """The whole payload one variant produces: the base first, in the
    order a record carries it, then the variant's own."""
    return base_of(variant.CHANNEL) + declared_values(variant)


# --- what the generated reference reads -------------------------------
#
# The same `EventSpec` shape the untyped registry answers, derived from
# the declarations rather than restated beside them. Transitional: one
# generator serves both sources while the conversion is in flight, and
# it goes when the registry does.


def _field_of(declared: Declared) -> EventField:
    return EventField(
        kind=declared.type.KIND,
        required=declared.required,
        nullable=declared.nullable,
        tokens=declared.type.TOKENS,
        syntax=declared.type.SYNTAX,
        bounds=declared.type.BOUNDS,
        note=declared.note,
    )


def _arg_of(declared: Declared) -> ArgSpec:
    return ArgSpec(
        kind=declared.type.ARG_KIND,
        nullable=declared.nullable,
        tokens=declared.type.TOKENS,
        syntax=declared.type.SYNTAX,
        bounds=declared.type.BOUNDS,
        note=declared.rendered_note,
    )


def _variant_of(variant: type[Variant]) -> EventVariant:
    by_name = {one.name: one for one in declared_values(variant)}
    return EventVariant(
        channel=variant.CHANNEL,
        level=variant.LEVEL,
        message=variant.TEMPLATE,
        note=variant.NOTE,
        args=tuple(_arg_of(by_name[name]) for name in variant.ARGS),
        fields={
            one.name: _field_of(one) for one in payload_shape(variant) if one.carried
        },
    )


def described() -> tuple[EventSpec, ...]:
    """Every declared event, as the documentation generator reads it."""
    return tuple(
        EventSpec(
            name=declaration.name,
            note=declaration.note,
            variants=tuple(_variant_of(one) for one in declaration.variants),
        )
        for declaration in _state.declarations.values()
    )


# --- conversations/store.py: the system of record for content ---------
#
# The store's own five lines, and the first area to convert: the
# smallest channel on the surface, which is what makes it the one that
# proves the machinery rather than exercises it.

CONVERSATIONS_CHANNEL = "vinga_server.conversations.store"


@dataclass(frozen=True)
class ConversationsEnabled(Variant):
    """The store opened, which means this server is recording."""

    CHANNEL: ClassVar[str] = CONVERSATIONS_CHANNEL
    LEVEL: ClassVar[int] = logging.WARNING
    TEMPLATE: ClassVar[str] = "recording conversations to %s"
    ARGS: ClassVar[tuple[str, ...]] = ("path",)

    path: ConfiguredPath


@dataclass(frozen=True)
class ConversationsDropped(Variant):
    """One session's events are being refused because the writer is
    behind."""

    CHANNEL: ClassVar[str] = CONVERSATIONS_CHANNEL
    LEVEL: ClassVar[int] = logging.WARNING
    TEMPLATE: ClassVar[str] = (
        "session %s: the conversation store is behind, dropping events"
    )
    ARGS: ClassVar[tuple[str, ...]] = ("session",)

    session: SessionId


@dataclass(frozen=True)
class WriteFailed(Variant):
    """A batch that did not commit."""

    CHANNEL: ClassVar[str] = CONVERSATIONS_CHANNEL
    LEVEL: ClassVar[int] = logging.WARNING
    TEMPLATE: ClassVar[str] = (
        "the conversation store dropped a batch after a write failed (%s)"
    )
    ARGS: ClassVar[tuple[str, ...]] = ("failure",)

    failure: ClassName = value(note="The exception's class name, never its message.")


@dataclass(frozen=True)
class PruneFailed(Variant):
    """Retention could not delete. The store still records, and the next
    close tries again."""

    CHANNEL: ClassVar[str] = CONVERSATIONS_CHANNEL
    LEVEL: ClassVar[int] = logging.WARNING
    TEMPLATE: ClassVar[str] = "the conversation store could not prune (%s)"
    ARGS: ClassVar[tuple[str, ...]] = ("failure",)

    failure: ClassName


@dataclass(frozen=True)
class ConversationsPruned(Variant):
    """Retention deleted the sessions older than the window."""

    CHANNEL: ClassVar[str] = CONVERSATIONS_CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = "conversations: pruned %d session(s) older than %d days"
    ARGS: ClassVar[tuple[str, ...]] = ("sessions", "days")

    sessions: Count = value(note="A count, not a list.")
    # Said and not stored: the window is the configuration's, and a
    # record that repeated it on every prune would be storing a setting.
    days: Count = value(carried=False)


CONVERSATIONS_ENABLED = declare(
    "conversations_enabled",
    note=(
        "The store opens at startup, which means this server is "
        "recording what is said to it. Said once, before anything "
        "connects, and at WARNING for the reason `capture_enabled` is."
    ),
    variants=(ConversationsEnabled,),
)

CONVERSATIONS_DROPPED = declare(
    "conversations_dropped",
    note=(
        "The store is behind and events for one session are being "
        "dropped. Said once per session at its first drop; the total "
        "lands on that session's row."
    ),
    variants=(ConversationsDropped,),
)

CONVERSATIONS_FAILED = declare(
    "conversations_failed",
    note="A write to the store failed and its batch was dropped, or a prune could not run.",
    variants=(WriteFailed, PruneFailed),
)

CONVERSATIONS_PRUNED = declare(
    "conversations_pruned",
    note="Retention deleted sessions older than the window. At INFO: a policy doing its job.",
    variants=(ConversationsPruned,),
)


__all__ = [
    "CONVERSATIONS_CHANNEL",
    "CONVERSATIONS_DROPPED",
    "CONVERSATIONS_ENABLED",
    "CONVERSATIONS_FAILED",
    "CONVERSATIONS_PRUNED",
    "CatalogError",
    "CatalogState",
    "ConversationsDropped",
    "ConversationsEnabled",
    "ConversationsPruned",
    "Declaration",
    "Declared",
    "Logged",
    "PruneFailed",
    "Variant",
    "WriteFailed",
    "base_of",
    "catalog",
    "declaration_of",
    "declare",
    "declared_values",
    "described",
    "install",
    "installed",
    "payload_shape",
    "value",
]
