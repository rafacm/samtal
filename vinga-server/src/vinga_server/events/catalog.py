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
from collections.abc import Mapping
from dataclasses import MISSING, dataclass, field, fields, is_dataclass
from types import MappingProxyType, UnionType
from typing import Any, ClassVar, Union, get_args, get_origin, get_type_hints

from vinga_server.events.values import (
    ABSENT,
    Absent,
    AgentList,
    AgentNames,
    AlsoBoundTo,
    ClassName,
    ClientId,
    CloseReasonToken,
    ConfiguredPath,
    Count,
    DeviceId,
    DeviceOrUnidentified,
    EventName,
    EventValue,
    EventValueError,
    FillerSkip,
    FillerSkipToken,
    Flag,
    FromEntry,
    Identifier,
    LanguageTag,
    Nothing,
    PromptSources,
    ProviderOutcomeToken,
    QuotedProvider,
    QuotedToolName,
    ReachingHost,
    Real,
    Rejection,
    RejectionToken,
    SessionId,
    Suppression,
    SuppressionToken,
    ToolOutcomeToken,
    ToolSource,
    ToolSourceToken,
    UnnamedToolSource,
    Whole,
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

# The base a variant is rendered against where there is none: a server
# channel contributes only the event name, which no sentence renders.
_NO_BASE: Mapping[str, EventValue | None] = MappingProxyType({})


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

    `fixed` holds the value where the variant IS the value: a
    `session_rejected` that says the Device-Id was not a MAC carries no
    other reason, so the field is not a parameter at all. The declared
    token set is that one member, which is what the untyped registry
    spelled out per variant and what a shared enumeration would have
    widened.
    """

    name: str
    type: type[EventValue]
    required: bool
    nullable: bool
    carried: bool
    note: str
    rendered_note: str
    fixed: EventValue | None = None


def value(
    *,
    carried: bool = True,
    note: str = "",
    rendered_note: str = "",
    fixed: EventValue | None = None,
    default: Any = MISSING,
) -> Any:
    """Declare one of a variant's values.

    `carried=False` marks a value the sentence renders and the payload
    does not keep, which is the only reason the two lists are not the
    same list. The two notes are the reference's two columns: what the
    field means, and what its `%` position means where the sentence
    needs saying something the field does not.

    `fixed=` states a value the variant always carries, and takes it out
    of the constructor entirely: a caller that cannot pass it cannot
    pass the wrong one. `default=ABSENT` is the other half of the same
    idea for a field a variant MAY omit, so a site says nothing where it
    has nothing.
    """
    metadata = {
        "carried": carried,
        "note": note,
        "rendered_note": rendered_note,
        "fixed": fixed,
    }
    if fixed is not None:
        return field(init=False, default=fixed, metadata=metadata)
    if default is not MISSING:
        return field(default=default, metadata=metadata)
    return field(metadata=metadata)


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

    def verify(self) -> None:
        """Every value this variant holds is the type its field
        declares.

        The annotations are the contract and nothing enforces them where
        a variant is built. `mypy` runs strict over this package only,
        so every emit site outside it is unchecked, and a frozen
        dataclass takes whatever it is handed; the checks in
        `declare()` read the ANNOTATIONS, which is a different question
        from what a caller passed.

        That gap is a leak rather than an untidiness. A value type is
        only a claim about provenance while the field holding it is the
        one that declared it: `Identifier` admits any non-blank string,
        because a configured name may be anything, so an `Identifier`
        handed to a field declared `LanguageTag` would put whatever an
        engine answered with onto the surface under a name that promises
        a bounded code. `carried()` would serialize it without a word.

        Called inside the emitter's guard, before anything is rendered
        or serialized, so a mismatch is refused exactly the way a
        refused value is. The refusal names the variant, the field and
        the declared type, all three of which are this module's own, and
        never what it was holding.
        """
        for declared in declared_values(type(self)):
            held = getattr(self, declared.name)
            where = f"{type(self).__name__}.{declared.name}"
            if held is None:
                if not declared.nullable:
                    raise CatalogError(f"{where} is not nullable")
                continue
            if isinstance(held, Absent):
                if declared.required:
                    raise CatalogError(f"{where} is required")
                continue
            if not isinstance(held, declared.type):
                raise CatalogError(f"{where} is a {declared.type.__name__}")

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

    def logged(self, base: Mapping[str, EventValue | None] = _NO_BASE) -> Logged:
        """The template and the ordered arguments, derived from the
        values `ARGS` names.

        `base` is what the emitter contributes, and `ARGS` may name one
        of those as well as one of the variant's own: every session
        sentence opens with "session %s", and the session id is the
        emitter's to know rather than a value each of thirty sites
        restates. Nothing can be ambiguous, because a variant that
        declared a base name is refused at declaration.
        """
        return Logged(
            self.TEMPLATE,
            tuple(
                None if held is None else held.rendered()
                for held in (
                    base[name] if name in base else getattr(self, name)
                    for name in self.ARGS
                )
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
                fixed=one.metadata.get("fixed"),
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
    if variant.CHANNEL not in CHANNELS:
        raise CatalogError(f"{where} names a channel this server does not speak on")
    if variant.LEVEL not in LEVELS:
        raise CatalogError(f"{where} names a level no emitter method emits at")
    # The base fields the emitter contributes are the emitter's own, and
    # a variant that declared one would be a site choosing its own
    # identity. On a server channel that is `event` alone: `session` and
    # `device` are ordinary fields there, declared where they are
    # carried, exactly as the untyped registry has them.
    base = base_of(variant.CHANNEL)
    owned = {one.name for one in base}.intersection(one.name for one in declared)
    if owned:
        raise CatalogError(f"{where} declares a field the emitter owns: {sorted(owned)}")
    for one in declared:
        # A value the payload keeps has to have a field kind, which is
        # what a reference prints and what says the value is metadata. A
        # formatted fragment has none: it is a shape a sentence renders,
        # never a key a record carries.
        if one.carried and getattr(one.type, "KIND", None) is None:
            raise CatalogError(f"{where}.{one.name} carries a value with no field kind")
    # A sentence may render one of the emitter's own values as well as
    # one of the variant's: every session sentence opens with the
    # session id, and a base name cannot collide with a declared one
    # because the check above already refused that.
    names = {one.name: one for one in (*base, *declared)}
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

def _base(
    name: str,
    type_: type[EventValue],
    *,
    nullable: bool = False,
    note: str = "",
) -> Declared:
    return Declared(
        name=name,
        type=type_,
        required=True,
        nullable=nullable,
        carried=True,
        note=note,
        rendered_note="",
    )


_EVENT = _base("event", EventName)

_SERVER_BASE: tuple[Declared, ...] = (_EVENT,)

# What every conversation event carries whatever it says: the event's
# name, the session it belongs to, and the device it is with. The last
# is nullable and the nullability is a fact rather than a hedge: the
# bad-Device-Id rejection names no device because none was understood.
_SESSION_BASE: tuple[Declared, ...] = (
    _EVENT,
    _base("session", SessionId),
    # Null until the edge has normalized the MAC, which is why the
    # bad-Device-Id rejection names no device.
    _base("device", DeviceId, nullable=True),
)


def base_of(channel: str) -> tuple[Declared, ...]:
    """The fields the emitter contributes on one channel."""
    return _SESSION_BASE if channel == SESSION_CHANNEL else _SERVER_BASE


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


def _tokens_of(declared: Declared) -> frozenset[str] | None:
    """The closed set one declared value admits.

    The type's own, or the single member where the variant fixes it: a
    variant that always says `no_agent` declares that one reason and not
    the four its enumeration holds, which is what the untyped registry
    spelled out variant by variant.
    """
    if declared.fixed is not None and declared.type.TOKENS is not None:
        return frozenset({str(declared.fixed.carried())})
    return declared.type.TOKENS


def _field_of(declared: Declared) -> EventField:
    return EventField(
        kind=declared.type.KIND,
        required=declared.required,
        nullable=declared.nullable,
        tokens=_tokens_of(declared),
        syntax=declared.type.SYNTAX,
        bounds=declared.type.BOUNDS,
        note=declared.note,
    )


def _arg_of(declared: Declared) -> ArgSpec:
    return ArgSpec(
        kind=declared.type.ARG_KIND,
        nullable=declared.nullable,
        tokens=_tokens_of(declared),
        syntax=declared.type.SYNTAX,
        bounds=declared.type.BOUNDS,
        grammar=declared.type.GRAMMAR,
        note=declared.rendered_note,
    )


def _variant_of(variant: type[Variant]) -> EventVariant:
    # The base as well as the variant's own, because `ARGS` may render a
    # base value and a reference has to describe the position it lands
    # in.
    by_name = {one.name: one for one in payload_shape(variant)}
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


# --- device/session.py: the conversation's own edge --------------------
#
# The session channel, which is the one every conversation record rides.
# Its base is three values rather than one (`values.py`'s `SessionId`
# and `DeviceId` beside the event's name), and every sentence here opens
# by rendering the first of them, which is why `ARGS` may name a base
# value at all.

WS_CHANNEL = "vinga_server.ws"


@dataclass(frozen=True)
class RejectedBadDeviceId(Variant):
    """A Device-Id header that is not a MAC.

    The header is bytes an unauthenticated caller chose, so neither the
    sentence nor any field repeats it: the reason says which rejection
    this is, the device is null because none was understood, and the
    sentence still says what the header has to hold.
    """

    CHANNEL: ClassVar[str] = SESSION_CHANNEL
    LEVEL: ClassVar[int] = logging.WARNING
    TEMPLATE: ClassVar[str] = (
        "session %s rejected: the Device-Id header is not a device MAC "
        "(six colon-separated hex pairs)"
    )
    ARGS: ClassVar[tuple[str, ...]] = ("session",)

    reason: RejectionToken = value(fixed=RejectionToken(Rejection.BAD_DEVICE_ID))


@dataclass(frozen=True)
class RejectedAgentNotLoaded(Variant):
    """A device bound to an agent this server booted without."""

    CHANNEL: ClassVar[str] = SESSION_CHANNEL
    LEVEL: ClassVar[int] = logging.WARNING
    TEMPLATE: ClassVar[str] = (
        "session %s rejected: device %s is bound to agent %s, which this "
        "server has not loaded; restart to load it"
    )
    ARGS: ClassVar[tuple[str, ...]] = ("session", "mac", "unloaded")

    reason: RejectionToken = value(fixed=RejectionToken(Rejection.AGENT_NOT_LOADED))
    # Said and not stored: the base already carries the device this
    # session is with, and a second copy under another name would be the
    # same fact twice on one record.
    mac: DeviceId = value(carried=False)
    unloaded: AgentList = value(carried=False)


@dataclass(frozen=True)
class RejectedNoAgent(Variant):
    """A device bound to nothing, with no default to fall back on."""

    CHANNEL: ClassVar[str] = SESSION_CHANNEL
    LEVEL: ClassVar[int] = logging.WARNING
    TEMPLATE: ClassVar[str] = (
        "session %s rejected: device %s has no agent: bind it under devices "
        "or set default_agent"
    )
    ARGS: ClassVar[tuple[str, ...]] = ("session", "mac")

    reason: RejectionToken = value(fixed=RejectionToken(Rejection.NO_AGENT))
    mac: DeviceId = value(carried=False)


@dataclass(frozen=True)
class RejectedAtCapacity(Variant):
    """The refusal the endpoint makes before a session can run at all.

    On the server channel, where `session` and `device` are ordinary
    declarable fields: there is no conversation yet whose identity an
    emitter could own.
    """

    CHANNEL: ClassVar[str] = WS_CHANNEL
    LEVEL: ClassVar[int] = logging.WARNING
    TEMPLATE: ClassVar[str] = (
        "refused a websocket handshake from %s: the server is at capacity"
    )
    ARGS: ClassVar[tuple[str, ...]] = ("shown",)

    device: DeviceId | None
    session: SessionId
    reason: RejectionToken = value(fixed=RejectionToken(Rejection.CAPACITY))
    shown: DeviceOrUnidentified = value(carried=False)


@dataclass(frozen=True)
class SessionOpen(Variant):
    """A conversation starts."""

    CHANNEL: ClassVar[str] = SESSION_CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = (
        "session %s open: device %s (client %s) agent %s%s, protocol v%d, "
        "%d Hz %d ms frames in"
    )
    ARGS: ClassVar[tuple[str, ...]] = (
        "session",
        "mac",
        "said_client",
        "agent",
        "bound_tail",
        "protocol",
        "sample_rate",
        "frame_ms",
    )

    client: ClientId | None = value(
        note=(
            "The device UUID, bounded for the event only: the capture "
            "manifest and the conversation store keep the header as it "
            "arrived."
        )
    )
    agent: Identifier = value()
    agents: AgentNames = value()
    protocol: Whole = value()
    revision: Identifier = value(
        note=(
            "Which build this server is, so every session from here on "
            "is attributable to one."
        )
    )
    mac: DeviceId = value(carried=False)
    # The same bounded copy the field carries, or the fixed word where
    # nothing printable survived: dropping a field would not un-render
    # an argument, so the sentence says what the record keeps.
    said_client: ClientId = value(carried=False)
    bound_tail: AlsoBoundTo = value(carried=False)
    sample_rate: Whole = value(carried=False)
    frame_ms: Whole = value(carried=False)


@dataclass(frozen=True)
class SessionLimit(Variant):
    """The duration cap fires."""

    CHANNEL: ClassVar[str] = SESSION_CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = "session %s reached the %.0f s time limit"
    ARGS: ClassVar[tuple[str, ...]] = ("session", "limit_s")

    duration_s: Real = value()
    # The cap, which is the configuration's; the field beside it is how
    # long this session actually ran.
    limit_s: Real = value(carried=False)


@dataclass(frozen=True)
class SessionIdle(Variant):
    """The idle timeout hangs up on a realtime session."""

    CHANNEL: ClassVar[str] = SESSION_CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = "session %s idle for %.0f s, hanging up"
    ARGS: ClassVar[tuple[str, ...]] = ("session", "idle_s")

    idle_s: Real = value()
    duration_s: Real = value()


@dataclass(frozen=True)
class SessionClosed(Variant):
    """A conversation ends."""

    CHANNEL: ClassVar[str] = SESSION_CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = "session %s closed (device %s)"
    ARGS: ClassVar[tuple[str, ...]] = ("session", "mac")

    duration_s: Real = value()
    reason: CloseReasonToken = value(
        note=(
            "The first cause to fire, so a drain closing a session an "
            "idle timer was about to hang up on reads `drain`."
        )
    )
    mac: DeviceId = value(carried=False)


@dataclass(frozen=True)
class SpeakingStarted(Variant):
    """The reply's first audio frame goes out."""

    CHANNEL: ClassVar[str] = SESSION_CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = "session %s: speaking started"
    ARGS: ClassVar[tuple[str, ...]] = ("session",)

    agent: Identifier = value()


# --- runtime/pipeline.py: what happens inside a conversation ----------


@dataclass(frozen=True)
class Heard(Variant):
    """An utterance is transcribed.

    No transcript, and the type is what says so: there is no value in
    this vocabulary that a spoken sentence could be constructed as.
    """

    CHANNEL: ClassVar[str] = SESSION_CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = "session %s: heard %.2f s of speech"
    ARGS: ClassVar[tuple[str, ...]] = ("session", "duration_s")

    agent: Identifier = value()
    duration_s: Real = value()
    language: LanguageTag | Absent = value(
        default=ABSENT, note="Only engines that detected carry this."
    )
    language_confidence: Real | Absent = value(default=ABSENT)


@dataclass(frozen=True)
class Replied(Variant):
    """A reply finishes."""

    CHANNEL: ClassVar[str] = SESSION_CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = "session %s: %s replied in %d sentences"
    ARGS: ClassVar[tuple[str, ...]] = ("session", "agent", "sentences")

    agent: Identifier = value()
    sentences: Count = value(
        note=(
            "How many of them the user heard, so a reply a barge-in cut "
            "short reports what went out."
        )
    )


@dataclass(frozen=True)
class AgentSaid(Variant):
    """One agent's part of a reply."""

    CHANNEL: ClassVar[str] = SESSION_CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = "session %s: %s said %d sentences"
    ARGS: ClassVar[tuple[str, ...]] = ("session", "agent", "sentences")

    agent: Identifier = value()
    sentences: Count = value()


@dataclass(frozen=True)
class Handover(Variant):
    """`switch_agent` succeeds."""

    CHANNEL: ClassVar[str] = SESSION_CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = "session %s: handed over from agent %s to %s"
    ARGS: ClassVar[tuple[str, ...]] = ("session", "from_agent", "to_agent")

    from_agent: Identifier = value()
    to_agent: Identifier = value()


@dataclass(frozen=True)
class PromptAssembled(Variant):
    """The know-how half of a prompt is assembled and cached."""

    CHANNEL: ClassVar[str] = SESSION_CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = "session %s: assembled %d characters of prompt for %s"
    ARGS: ClassVar[tuple[str, ...]] = ("session", "characters", "agent")

    agent: Identifier = value()
    characters: Count = value()
    sources: PromptSources = value(
        note=(
            "Each block's size by provenance: how much of the prompt "
            "came from where, never any of the prompt itself."
        )
    )


@dataclass(frozen=True)
class LlmRetry(Variant):
    """The first-token watchdog retries a round, for a provider whose
    identity the registry never built."""

    CHANNEL: ClassVar[str] = SESSION_CHANNEL
    LEVEL: ClassVar[int] = logging.WARNING
    TEMPLATE: ClassVar[str] = "session %s: no first token after %.1f s, retrying round %d"
    ARGS: ClassVar[tuple[str, ...]] = ("session", "duration_s", "round")
    NOTE: ClassVar[str] = "A provider the registry did not build names no entry."

    agent: Identifier = value()
    round: Whole = value()
    duration_ms: Whole = value()
    stage: Identifier = value()
    duration_s: Real = value(carried=False)


@dataclass(frozen=True)
class LlmRetryOfEntry(Variant):
    """The same retry, for a provider the registry built out of a
    configured entry."""

    CHANNEL: ClassVar[str] = SESSION_CHANNEL
    LEVEL: ClassVar[int] = logging.WARNING
    TEMPLATE: ClassVar[str] = "session %s: no first token after %.1f s, retrying round %d"
    ARGS: ClassVar[tuple[str, ...]] = ("session", "duration_s", "round")
    NOTE: ClassVar[str] = (
        "`provider` and `type` are atomic: a provider with an identity "
        "carries both. `host` is absent for an engine that runs in this "
        "process and `model` for a type that has none to name."
    )

    agent: Identifier = value()
    round: Whole = value()
    duration_ms: Whole = value()
    stage: Identifier = value()
    provider: Identifier = value()
    type: Identifier = value()
    duration_s: Real = value(carried=False)
    host: Identifier | Absent = value(default=ABSENT)
    model: Identifier | Absent = value(
        default=ABSENT, note="The GenAI conventions' `gen_ai.request.model`."
    )


@dataclass(frozen=True)
class LlmRound(Variant):
    """A generation call finishes, on a provider with no configured
    identity."""

    CHANNEL: ClassVar[str] = SESSION_CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = "session %s: %s round %d took %.2f s over %d turns"
    ARGS: ClassVar[tuple[str, ...]] = ("session", "agent", "round", "duration_s", "turns")

    agent: Identifier = value()
    round: Whole = value(
        note=(
            "Counts the whole reply rather than one agent's leg, so the "
            "generation after a handover is a round of its own."
        )
    )
    turns: Count = value(note="The cheap proxy for payload size.")
    duration_ms: Whole = value()
    stage: Identifier = value()
    duration_s: Real = value(carried=False)
    input_tokens: Count | Absent = value(default=ABSENT)
    output_tokens: Count | Absent = value(default=ABSENT)
    first_token_ms: Whole | Absent = value(
        default=ABSENT,
        note=(
            "Times the first spoken token, so a round that only asked "
            "for a tool carries none."
        ),
    )


@dataclass(frozen=True)
class LlmRoundOfEntry(Variant):
    """The same round, on a provider the registry built."""

    CHANNEL: ClassVar[str] = SESSION_CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = "session %s: %s round %d took %.2f s over %d turns"
    ARGS: ClassVar[tuple[str, ...]] = ("session", "agent", "round", "duration_s", "turns")

    agent: Identifier = value()
    round: Whole = value()
    turns: Count = value()
    duration_ms: Whole = value()
    stage: Identifier = value()
    provider: Identifier = value()
    type: Identifier = value()
    duration_s: Real = value(carried=False)
    host: Identifier | Absent = value(default=ABSENT)
    model: Identifier | Absent = value(
        default=ABSENT,
        note=(
            "Present where the configured entry names one. The GenAI "
            "conventions' `gen_ai.request.model`."
        ),
    )
    input_tokens: Count | Absent = value(
        default=ABSENT,
        note=(
            "Present where the provider reported usage; their "
            "absence is a fact about the endpoint."
        ),
    )
    output_tokens: Count | Absent = value(default=ABSENT)
    first_token_ms: Whole | Absent = value(default=ABSENT)


@dataclass(frozen=True)
class ProviderFailed(Variant):
    """An ASR, LLM or TTS call fails, on a provider with no configured
    identity."""

    CHANNEL: ClassVar[str] = SESSION_CHANNEL
    LEVEL: ClassVar[int] = logging.WARNING
    TEMPLATE: ClassVar[str] = "session %s: %s provider%s %s after %.2f s%s: %s"
    ARGS: ClassVar[tuple[str, ...]] = (
        "session",
        "stage",
        "named",
        "outcome",
        "duration_s",
        "where",
        "error",
    )
    NOTE: ClassVar[str] = (
        "A provider the registry did not build names no entry and no host."
    )

    agent: Identifier = value()
    error: ClassName = value()
    duration_ms: Whole = value()
    stage: Identifier = value()
    named: Nothing = value(carried=False)
    outcome: ProviderOutcomeToken = value(carried=False)
    duration_s: Real = value(carried=False)
    where: Nothing = value(carried=False)


@dataclass(frozen=True)
class ProviderOfEntryFailed(Variant):
    """The same failure, on a provider the registry built out of a
    configured entry."""

    CHANNEL: ClassVar[str] = SESSION_CHANNEL
    LEVEL: ClassVar[int] = logging.WARNING
    TEMPLATE: ClassVar[str] = "session %s: %s provider%s %s after %.2f s%s: %s"
    ARGS: ClassVar[tuple[str, ...]] = (
        "session",
        "stage",
        "named",
        "outcome",
        "duration_s",
        "where",
        "error",
    )

    agent: Identifier = value()
    error: ClassName = value(
        note="A round whose retry also stalled carries `FirstTokenTimeout`."
    )
    duration_ms: Whole = value()
    stage: Identifier = value()
    provider: Identifier = value()
    type: Identifier = value()
    named: QuotedProvider = value(carried=False)
    outcome: ProviderOutcomeToken = value(carried=False)
    duration_s: Real = value(carried=False)
    where: ReachingHost = value(carried=False)
    host: Identifier | Absent = value(default=ABSENT)
    model: Identifier | Absent = value(default=ABSENT)


@dataclass(frozen=True)
class BuiltinToolCall(Variant):
    """A builtin returns. The one branch that names its tool, because a
    builtin's name is this server's own word."""

    CHANNEL: ClassVar[str] = SESSION_CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = "session %s: %s tool%s took %.2f s%s"
    ARGS: ClassVar[tuple[str, ...]] = (
        "session",
        "source",
        "named",
        "duration_s",
        "outcome",
    )

    agent: Identifier = value()
    source: ToolSourceToken = value(fixed=ToolSourceToken(ToolSource.BUILTIN))
    tool: Identifier = value(note="The only tool names this server authors.")
    duration_ms: Whole = value()
    is_error: Flag = value()
    named: QuotedToolName = value(carried=False)
    duration_s: Real = value(carried=False)
    outcome: ToolOutcomeToken = value(carried=False)


@dataclass(frozen=True)
class McpToolCall(Variant):
    """An MCP call returns, named by the entry an operator configured."""

    CHANNEL: ClassVar[str] = SESSION_CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = "session %s: %s tool%s took %.2f s%s"
    ARGS: ClassVar[tuple[str, ...]] = (
        "session",
        "source",
        "named",
        "duration_s",
        "outcome",
    )

    agent: Identifier = value()
    source: ToolSourceToken = value(fixed=ToolSourceToken(ToolSource.MCP))
    entry: Identifier = value(
        note="The configured entry, never the far side's tool name."
    )
    duration_ms: Whole = value()
    is_error: Flag = value()
    named: FromEntry = value(carried=False)
    duration_s: Real = value(carried=False)
    outcome: ToolOutcomeToken = value(carried=False)


@dataclass(frozen=True)
class UnnamedToolCall(Variant):
    """A call this surface may not name at all."""

    CHANNEL: ClassVar[str] = SESSION_CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = "session %s: %s tool%s took %.2f s%s"
    ARGS: ClassVar[tuple[str, ...]] = (
        "session",
        "source",
        "named",
        "duration_s",
        "outcome",
    )
    NOTE: ClassVar[str] = (
        "A device tool's name is the board's vocabulary and an unknown "
        "one is whatever the model invented, so neither is named."
    )

    agent: Identifier = value()
    source: UnnamedToolSource = value()
    duration_ms: Whole = value()
    is_error: Flag = value()
    named: Nothing = value(carried=False)
    duration_s: Real = value(carried=False)
    outcome: ToolOutcomeToken = value(carried=False)


# --- runtime/turntaking.py: who is talking ---------------------------


@dataclass(frozen=True)
class BargeIn(Variant):
    """Speech cuts a reply short."""

    CHANNEL: ClassVar[str] = SESSION_CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = "session %s: barge-in, cancelling the reply in flight"
    ARGS: ClassVar[tuple[str, ...]] = ("session",)

    speech_ms: Whole = value()
    speaking_ms: Whole | Absent = value(
        default=ABSENT,
        note=(
            "Milliseconds from `speaking_started` to the cancel "
            "decision, absent when the reply had not yet spoken."
        ),
    )


@dataclass(frozen=True)
class BargeInUnderFloor(Variant):
    """Too little classified speech to be anything but a noise blip."""

    CHANNEL: ClassVar[str] = SESSION_CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = (
        "session %s: barge-in suppressed, %d ms of speech is under the %.0f ms floor"
    )
    ARGS: ClassVar[tuple[str, ...]] = ("session", "speech_ms", "floor_ms")

    reason: SuppressionToken = value(fixed=SuppressionToken(Suppression.MIN_SPEECH))
    speech_ms: Whole = value()
    floor_ms: Real = value(carried=False)


@dataclass(frozen=True)
class BargeInInRefractory(Variant):
    """The onset transient a device's echo cancellation let through."""

    CHANNEL: ClassVar[str] = SESSION_CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = "session %s: barge-in suppressed inside the refractory window"
    ARGS: ClassVar[tuple[str, ...]] = ("session",)

    reason: SuppressionToken = value(fixed=SuppressionToken(Suppression.REFRACTORY))
    speech_ms: Whole = value()


@dataclass(frozen=True)
class BargeInWithoutTranscript(Variant):
    """A pause that asked ASR and got nothing back."""

    CHANNEL: ClassVar[str] = SESSION_CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = "session %s: barge-in suppressed, nothing transcribed"
    ARGS: ClassVar[tuple[str, ...]] = ("session",)

    reason: SuppressionToken = value(fixed=SuppressionToken(Suppression.NO_TRANSCRIPT))
    speech_ms: Whole = value()


@dataclass(frozen=True)
class BargeInMerged(Variant):
    """An interruption merges with the utterance the reply was
    transcribing."""

    CHANNEL: ClassVar[str] = SESSION_CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = (
        "session %s: barge-in mid-transcription, merging the utterances"
    )
    ARGS: ClassVar[tuple[str, ...]] = ("session",)

    speech_ms: Whole = value()


# --- runtime/filler_runner.py: the latency mask -----------------------


@dataclass(frozen=True)
class FillerSkippedForSpeech(Variant):
    """The timer fired but the user was there first."""

    CHANNEL: ClassVar[str] = SESSION_CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = (
        "session %s: filler skipped, the user is speaking (%d ms heard)"
    )
    ARGS: ClassVar[tuple[str, ...]] = ("session", "speech_ms")

    agent: Identifier = value()
    reason: FillerSkipToken = value(fixed=FillerSkipToken(FillerSkip.USER_SPEAKING))
    speech_ms: Whole = value()


@dataclass(frozen=True)
class FillerSkippedForBargeIn(Variant):
    """The outgoing frames are paused while a barge-in is confirmed, so
    the silence the timer would mask is not silence."""

    CHANNEL: ClassVar[str] = SESSION_CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = "session %s: filler skipped, a barge-in is being confirmed"
    ARGS: ClassVar[tuple[str, ...]] = ("session",)

    agent: Identifier = value()
    reason: FillerSkipToken = value(fixed=FillerSkipToken(FillerSkip.BARGE_IN_PENDING))


@dataclass(frozen=True)
class FillerPlayed(Variant):
    """A pre-synthesized clip masked the wait."""

    CHANNEL: ClassVar[str] = SESSION_CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = "session %s: no reply audio after %d ms, playing filler %d"
    ARGS: ClassVar[tuple[str, ...]] = ("session", "delay_ms", "phrase_index")

    agent: Identifier = value()
    delay_ms: Whole = value(note="Measured, from the transcription to the fire.")
    phrase_index: Count = value()


SESSION_REJECTED = declare(
    "session_rejected",
    note=(
        "A device turned away. Emitted on both scopes: the session "
        "channel for the refusals a session makes after the accept, "
        "and `vinga_server.ws` for the one the endpoint makes before "
        "a session can run at all."
    ),
    variants=(
        RejectedBadDeviceId,
        RejectedAgentNotLoaded,
        RejectedNoAgent,
        RejectedAtCapacity,
    ),
)

SESSION_OPEN = declare("session_open", note="A conversation starts.", variants=(SessionOpen,))

SESSION_LIMIT = declare("session_limit", note="The duration cap fires.", variants=(SessionLimit,))

SESSION_IDLE = declare(
    "session_idle",
    note="The idle timeout hangs up on a realtime session.",
    variants=(SessionIdle,),
)

SESSION_CLOSED = declare("session_closed", note="A conversation ends.", variants=(SessionClosed,))

SPEAKING_STARTED = declare(
    "speaking_started",
    note="The reply's first audio frame goes out.",
    variants=(SpeakingStarted,),
)

HEARD = declare(
    "heard",
    note=(
        "An utterance is transcribed. No transcript: what was said is "
        "the conversation store's, and what an operator measures with "
        "is how long the user spoke."
    ),
    variants=(Heard,),
)

REPLIED = declare("replied", note="A reply finishes.", variants=(Replied,))

AGENT_SAID = declare("agent_said", note="One agent's part of a reply.", variants=(AgentSaid,))

HANDOVER = declare("handover", note="`switch_agent` succeeds.", variants=(Handover,))

PROMPT_ASSEMBLED = declare(
    "prompt_assembled",
    note=(
        "The know-how half of a prompt is assembled and cached. The "
        "per-round memory read is deliberately not part of it, which "
        "is why `memory` is not one of the provenance forms."
    ),
    variants=(PromptAssembled,),
)

LLM_RETRY = declare(
    "llm_retry",
    note="The first-token watchdog cancels a stalled generation and retries the round once.",
    variants=(LlmRetry, LlmRetryOfEntry),
)

LLM_ROUND = declare(
    "llm_round", note="A generation call finishes.", variants=(LlmRound, LlmRoundOfEntry)
)

PROVIDER_FAILED = declare(
    "provider_failed",
    note=(
        "An ASR, LLM or TTS call fails. The class name is reported and "
        "the exception's message is not: a type name says what went "
        "wrong, a message says what a stranger wrote."
    ),
    variants=(ProviderFailed, ProviderOfEntryFailed),
)

TOOL_CALL = declare(
    "tool_call",
    note=(
        "A tool returns. `source` says which namespace the model "
        "reached into; the name itself is only ever this server's own "
        "word for it."
    ),
    variants=(BuiltinToolCall, McpToolCall, UnnamedToolCall),
)

BARGE_IN = declare("barge_in", note="Speech cuts a reply short.", variants=(BargeIn,))

BARGE_IN_SUPPRESSED = declare(
    "barge_in_suppressed",
    note="An interruption is dropped and the reply lives.",
    variants=(BargeInUnderFloor, BargeInInRefractory, BargeInWithoutTranscript),
)

BARGE_IN_MERGED = declare(
    "barge_in_merged",
    note="An interruption merges with the utterance the reply was transcribing.",
    variants=(BargeInMerged,),
)

FILLER_SKIPPED = declare(
    "filler_skipped",
    note="The filler timer fired but the user was there first, so no clip played.",
    variants=(FillerSkippedForSpeech, FillerSkippedForBargeIn),
)

FILLER_PLAYED = declare(
    "filler_played",
    note=(
        "The reply was slow, so a pre-synthesized clip masked the "
        "wait. Its first frame is the turn's `speaking_started`."
    ),
    variants=(FillerPlayed,),
)


__all__ = [
    "AGENT_SAID",
    "AgentSaid",
    "BARGE_IN",
    "BARGE_IN_MERGED",
    "BARGE_IN_SUPPRESSED",
    "BargeIn",
    "BargeInInRefractory",
    "BargeInMerged",
    "BargeInUnderFloor",
    "BargeInWithoutTranscript",
    "BuiltinToolCall",
    "FILLER_PLAYED",
    "FILLER_SKIPPED",
    "FillerPlayed",
    "FillerSkippedForBargeIn",
    "FillerSkippedForSpeech",
    "HANDOVER",
    "HEARD",
    "Handover",
    "Heard",
    "LLM_RETRY",
    "LLM_ROUND",
    "LlmRetry",
    "LlmRetryOfEntry",
    "LlmRound",
    "LlmRoundOfEntry",
    "McpToolCall",
    "PROMPT_ASSEMBLED",
    "PROVIDER_FAILED",
    "PromptAssembled",
    "ProviderFailed",
    "ProviderOfEntryFailed",
    "REPLIED",
    "RejectedAgentNotLoaded",
    "RejectedAtCapacity",
    "RejectedBadDeviceId",
    "RejectedNoAgent",
    "Replied",
    "SESSION_CLOSED",
    "SESSION_IDLE",
    "SESSION_LIMIT",
    "SESSION_OPEN",
    "SESSION_REJECTED",
    "SPEAKING_STARTED",
    "SessionClosed",
    "SessionIdle",
    "SessionLimit",
    "SessionOpen",
    "SpeakingStarted",
    "TOOL_CALL",
    "UnnamedToolCall",
    "WS_CHANNEL",
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
