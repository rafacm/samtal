"""What this server may say, declared once per event as typed variants.

The registry this replaced described an emission; a catalog entry IS
one. That is the whole difference, and everything else follows from it.
A site used to restate the template, the argument order, the event name
and the field set at every call, and five structures had to agree for
one record to be lawful: the declaration, the call, the pin, the sidecar
entry and the conformance walk's reading of the source. Here the call
constructs the declaration, so there is nothing left for it to disagree
with.

**One declaration per event code, holding a discriminated set of typed
variants.** An event is not one shape: `conversations_failed` says two
different sentences about two different failures under one name, and
the surface has events with variants across channels and levels. So a
declaration names the event and owns its variants, and a caller
constructs the specific variant named after the thing that happened.
The generated reference derives from the enclosing declaration.

**A variant owns its whole emission.** Its channel, its level, its
exact payload shape (names, types, requiredness, nullability), and its
rendering. `Emission` and `LogTap` are untouched: every variant derives
its logging specification, the unrendered template and the ordered
argument tuple those two already carry, from its own fields. The record
a tap or a log reader sees is therefore the record it saw before, which
the driver suite proves by driving every path rather than claims.

**Absence and null are different answers.** A field that is present and
null is a fact the record states; a field that is absent is a key the
JSON object does not have. A bare `| None` cannot say which a site
meant, so a variant annotates an omittable field with `Absent` and the
payload builder drops it, while a nullable one keeps its key.

**Documentation facts are declaration metadata.** Notes, the constraint
a value type carries, the syntaxes and the bounds are declared here and
on the value types, never introspected from prose, and there is no
second description of a declaration for a generator to read: the
reference reads the declarations themselves. A closed set is the one
constraint the annotation itself may state: a field annotated with its
enumeration admits every member, one annotated with a `Literal` over
some of them admits those, and `Declared` carries the answer either
way, so nothing downstream reads it off a type.

The module imports the value vocabulary and the standard library, and
imports no subsystem: the arrows keep pointing downward.
"""

import logging
import re
from collections.abc import Mapping
from dataclasses import MISSING, dataclass, field, fields, is_dataclass
from enum import StrEnum
from types import MappingProxyType, UnionType
from typing import Any, ClassVar, Literal, Union, get_args, get_origin, get_type_hints

from vinga_server.events.values import (
    ABSENT,
    Absent,
    ActivationCode,
    ActivationRefusal,
    AgentList,
    AgentNames,
    AlsoBoundTo,
    ArgKind,
    AuthRejection,
    BoardName,
    CaptureDeclined,
    CaptureWrite,
    ClassName,
    ClassNames,
    ClientId,
    CloseReason,
    ConfiguredPath,
    ConversationId,
    Count,
    DeviceId,
    DeviceOrUnidentified,
    EchoOutcome,
    EventName,
    EventValue,
    EventValueError,
    FillerSkip,
    FirmwareVersion,
    Flag,
    Fragment,
    FromEntry,
    Grammar,
    Identifier,
    Kind,
    LanguageTag,
    LoopbackHost,
    McpConnectFailure,
    McpDown,
    McpRefusal,
    McpReloadOutcome,
    McpTransport,
    MemoryScope,
    Nothing,
    NotOffered,
    OriginProvenance,
    OriginSource,
    OtaRefusal,
    PendingRefusal,
    PromptSources,
    ProviderOutcome,
    QuotedProvider,
    QuotedToolName,
    ReachingHost,
    Real,
    Rejection,
    ReportedMac,
    SessionId,
    SessionIds,
    SessionList,
    Suppression,
    ToolOutcome,
    ToolSource,
    UnnamedToolSource,
    Whole,
)

# --- the channels -----------------------------------------------------
#
# The channel is the scope, and it is a compatibility fact: `logs.py`
# writes `record.name` as the `logger` field of every JSON record, and a
# collector filters on it. One session channel, named rather than
# derived from a file so that splitting the code across packages cannot
# rename it, and one channel per server subsystem, each built on that
# subsystem's own module name.
#
# Named here rather than beside each area, because the tuple below has to
# be one reading of them rather than a second list: `CHANNELS` is what a
# variant's channel is checked against and what the recovery event is
# declared on, and both are needed before the first declaration runs.

SESSION_CHANNEL = "vinga_server.session"

APP_CHANNEL = "vinga_server.app"
CAPTURE_CHANNEL = "vinga_server.capture"
CONFIG_API_CHANNEL = "vinga_server.config.api"
CONVERSATIONS_CHANNEL = "vinga_server.conversations.store"
BINDINGS_CHANNEL = "vinga_server.device.bindings"
FILLER_CHANNEL = "vinga_server.filler"
MEMORY_CHANNEL = "vinga_server.memory.store"
ONBOARDING_CHANNEL = "vinga_server.onboarding"
OTA_CHANNEL = "vinga_server.ota"
ASR_CHANNEL = "vinga_server.providers.openai_asr"
PROVIDERS_CHANNEL = "vinga_server.providers.world"
REGISTRY_CHANNEL = "vinga_server.registry"
MCP_CHANNEL = "vinga_server.tools.mcp"
WS_CHANNEL = "vinga_server.ws"

SERVER_CHANNELS: tuple[str, ...] = (
    APP_CHANNEL,
    CAPTURE_CHANNEL,
    CONFIG_API_CHANNEL,
    CONVERSATIONS_CHANNEL,
    BINDINGS_CHANNEL,
    FILLER_CHANNEL,
    MEMORY_CHANNEL,
    ONBOARDING_CHANNEL,
    OTA_CHANNEL,
    ASR_CHANNEL,
    PROVIDERS_CHANNEL,
    REGISTRY_CHANNEL,
    MCP_CHANNEL,
    WS_CHANNEL,
)

CHANNELS: tuple[str, ...] = (SESSION_CHANNEL, *SERVER_CHANNELS)


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

    `type` is a value type or a `StrEnum`: a closed-set field is
    annotated with its enumeration, so declaring one is annotating the
    field with the set rather than finding the wrapper that held it.
    `tokens` is what such a field admits, the whole enumeration or the
    subset a `Literal` annotation narrows it to, and it is `None` for a
    field a value type carries, since a value type declares no set.
    """

    name: str
    type: type[EventValue] | type[StrEnum]
    required: bool
    nullable: bool
    carried: bool
    note: str
    rendered_note: str
    fixed: EventValue | StrEnum | None = None
    tokens: frozenset[str] | None = None


def value(
    *,
    carried: bool = True,
    note: str = "",
    rendered_note: str = "",
    fixed: EventValue | StrEnum | None = None,
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


def _carried(held: EventValue | StrEnum) -> object:
    """The plain builtin one held value rides the payload as.

    `str()` for an enumeration member rather than the member itself: a
    member is a `str` subclass, and a record carrying one would hand the
    subclass to a tap and its `repr` to anything that renders it.
    """
    return str(held) if isinstance(held, StrEnum) else held.carried()


def _rendered(held: EventValue | StrEnum) -> object:
    """What one held value's `%` position receives, converted for the
    reason above."""
    return str(held) if isinstance(held, StrEnum) else held.rendered()


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
            # And within the narrowing where the annotation states one: a
            # member of the enumeration is not necessarily a member of
            # the `Literal` the field declared. Asked of every enum-typed
            # field, since the check is the same one for a field whose
            # set is the whole enumeration and passes there by
            # construction.
            if declared.tokens is not None and str(held) not in declared.tokens:
                raise CatalogError(f"{where} is a narrowed {declared.type.__name__}")

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
            built[declared.name] = None if held is None else _carried(held)
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
                None if held is None else _rendered(held)
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
        if len(carried_types) != 1:
            raise CatalogError(
                f"{variant.__name__}.{one.name} declares one value type, "
                f"optionally with None or Absent"
            )
        held, tokens = _declared_type(variant, one.name, carried_types[0])
        read.append(
            Declared(
                name=one.name,
                type=held,
                required=required,
                nullable=nullable,
                carried=bool(one.metadata.get("carried", True)),
                note=str(one.metadata.get("note", "")),
                rendered_note=str(one.metadata.get("rendered_note", "")),
                fixed=one.metadata.get("fixed"),
                tokens=tokens,
            )
        )
    return tuple(read)


def _declared_type(
    variant: type[Variant], name: str, annotation: Any
) -> tuple[type[EventValue] | type[StrEnum], frozenset[str] | None]:
    """What one field carries, and the closed set it admits.

    Two annotations state a set themselves: the enumeration, which
    admits every member, and a `Literal` over some of one enumeration's
    members, which admits those. A `Literal` mixing two enumerations
    names no set at all, so it is refused rather than answered with an
    arbitrary one of them. Everything else is a value type, which states
    no set: an enumeration is the only way to declare one.
    """
    if isinstance(annotation, type) and issubclass(annotation, StrEnum):
        return annotation, frozenset(str(one) for one in annotation)
    if get_origin(annotation) is Literal:
        members = get_args(annotation)
        enums = {type(one) for one in members}
        held = enums.pop() if len(enums) == 1 else None
        if held is None or not issubclass(held, StrEnum):
            raise CatalogError(
                f"{variant.__name__}.{name} declares members of one StrEnum"
            )
        return held, frozenset(str(one) for one in members)
    if isinstance(annotation, type) and issubclass(annotation, EventValue):
        return annotation, None
    raise CatalogError(
        f"{variant.__name__}.{name} declares one value type, "
        f"optionally with None or Absent"
    )


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
        if one.carried and kind_of(one) is None:
            raise CatalogError(f"{where}.{one.name} carries a value with no field kind")
        # A wrapper's constructor used to hold a fixed token to its set
        # while this module imported, and a bare member is inert data, so
        # the duty is this check's now: without it the first evidence of
        # a mismatch would be a `construction_failed` at emit, in a
        # running deployment, naming nothing.
        if one.tokens is not None and one.fixed is not None:
            if not isinstance(one.fixed, one.type) or str(one.fixed) not in one.tokens:
                raise CatalogError(
                    f"{where}.{one.name} fixes a value outside its declared tokens"
                )
    # A sentence may render one of the emitter's own values as well as
    # one of the variant's: every session sentence opens with the
    # session id, and a base name cannot collide with a declared one
    # because the check above already refused that.
    names = {one.name: one for one in (*base, *declared)}
    # A value may be rendered in two positions, and two sentences on
    # this surface do it: an activation code is shown and then repeated
    # inside the command an operator is told to type, and so is the MAC
    # beside it. Refusing that would make a site pass the same value
    # under two names to say one thing twice.
    for name in variant.ARGS:
        if name not in names:
            raise CatalogError(f"{where} renders {name}, which it does not declare")
        if not names[name].required:
            raise CatalogError(f"{where} renders {name}, which it may not carry at all")
        # And a rendered value has to have an argument kind, which is
        # what a reference prints for the position: two payload kinds
        # have no argument twin, and a sentence rendering one of those
        # would print a cell with nothing in it.
        if arg_kind_of(names[name]) is None:
            raise CatalogError(f"{where} renders {name}, which has no argument kind")
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
    name: str,
    *,
    variants: tuple[type[Variant], ...],
    note: str = "",
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
# in front of them. The generated reference and the suite that holds
# every record to its declaration both need the whole payload, so the
# base is described here, once, rather than by each of them.

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
# Nothing beyond the declarations themselves. A variant's class-level
# facts, its `Declared` values and the value types those name carry
# every property the reference prints, so the generator reads them
# rather than a second description built beside them.


def kind_of(declared: Declared) -> Kind | None:
    """What one declared value is called as a payload field.

    Asked of the declaration rather than of the type it names, because
    a closed-set field is annotated with its enumeration and an
    enumeration carries none of these documentation facts. `None` where
    the value has no field kind at all: a formatted fragment is a shape
    a sentence renders and never a key a record carries, which is what
    `_check` refuses for a carried value.
    """
    held = declared.type
    if issubclass(held, StrEnum):
        return Kind.TOKEN
    return getattr(held, "KIND", None)


def arg_kind_of(declared: Declared) -> ArgKind | None:
    """And what it is called as a `%` position."""
    held = declared.type
    if issubclass(held, StrEnum):
        return ArgKind.TOKEN
    return getattr(held, "ARG_KIND", None)


def grammar_of(declared: Declared) -> Grammar | None:
    """The shape a `COMPOSED` argument is held to, where it is one."""
    held = declared.type
    if issubclass(held, StrEnum):
        return None
    return held.GRAMMAR


def tokens_of(declared: Declared) -> frozenset[str] | None:
    """The closed set one declared value admits.

    The annotation's own, or the single member where the variant fixes
    it: a variant that always says `no_agent` declares that one reason
    and not the four its enumeration holds. Public because it is what a
    reference prints and what a caller reading the catalog would
    otherwise re-derive from `fixed`.
    """
    tokens = declared.tokens
    if declared.fixed is not None and tokens is not None:
        return frozenset({str(_carried(declared.fixed))})
    return tokens


def rendered_values(variant: type[Variant]) -> tuple[Declared, ...]:
    """The values one variant's sentence renders, in `%` order.

    The base as well as the variant's own, because `ARGS` may name a
    base value and a reference has to describe the position it lands in.
    """
    by_name = {one.name: one for one in payload_shape(variant)}
    return tuple(by_name[name] for name in variant.ARGS)


def carried_values(variant: type[Variant]) -> tuple[Declared, ...]:
    """The whole payload one variant produces, in the order a record
    carries it."""
    return tuple(one for one in payload_shape(variant) if one.carried)



# --- conversations/store.py: the system of record for content ---------
#
# The store's own five lines, and the first area to convert: the
# smallest channel on the surface, which is what makes it the one that
# proves the machinery rather than exercises it.



@dataclass(frozen=True)
class ConversationsEnabled(Variant):
    """The store opened, which means this server is recording.

    No value on it since the record moved into the database beside the
    configuration (#283): what it used to carry was the file's path, and
    the connection that replaced the file is not something an event may
    carry. Which database a server is recording into is answered by the
    server's own configuration, once, rather than by a line per start.
    """

    CHANNEL: ClassVar[str] = CONVERSATIONS_CHANNEL
    LEVEL: ClassVar[int] = logging.WARNING
    TEMPLATE: ClassVar[str] = "recording conversations"
    ARGS: ClassVar[tuple[str, ...]] = ()


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
    """Retention deleted what had aged out of the window."""

    CHANNEL: ClassVar[str] = CONVERSATIONS_CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = (
        "conversations: pruned %d conversation(s) and %d session record(s) older "
        "than %d days"
    )
    ARGS: ClassVar[tuple[str, ...]] = ("conversations", "sessions", "days")

    conversations: Count = value(
        note=(
            "Threads taken whole, with their turns. The unit retention "
            "prunes by, so this is the count that says how much dialogue "
            "left."
        )
    )
    sessions: Count = value(
        note=(
            "Session records taken, which is only the aged ones no "
            "surviving turn still names. A count, not a list."
        )
    )
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
    note=(
        "Retention deleted the conversations that had aged out of the "
        "window, and the session records nothing points at any more. At "
        "INFO: a policy doing its job."
    ),
    variants=(ConversationsPruned,),
)


# --- device/session.py: the conversation's own edge --------------------
#
# The session channel, which is the one every conversation record rides.
# Its base is three values rather than one (`values.py`'s `SessionId`
# and `DeviceId` beside the event's name), and every sentence here opens
# by rendering the first of them, which is why `ARGS` may name a base
# value at all.



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

    reason: Rejection = value(fixed=Rejection.BAD_DEVICE_ID)


@dataclass(frozen=True)
class RejectedAgentNotLoaded(Variant):
    """A device bound to an agent the world this server is serving does
    not hold.

    The binding is live and the agent is one apply away, which is what
    the sentence has to say: a server serves the domain half it last
    installed, so an agent written since then is reached by asking for
    the stored configuration to be applied rather than by a restart
    (#191).
    """

    CHANNEL: ClassVar[str] = SESSION_CHANNEL
    LEVEL: ClassVar[int] = logging.WARNING
    TEMPLATE: ClassVar[str] = (
        "session %s rejected: device %s is bound to agent %s, which this "
        "server is not serving; install it with: vinga-server config apply"
    )
    ARGS: ClassVar[tuple[str, ...]] = ("session", "mac", "unloaded")

    reason: Rejection = value(fixed=Rejection.AGENT_NOT_LOADED)
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

    reason: Rejection = value(fixed=Rejection.NO_AGENT)
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
    reason: Rejection = value(fixed=Rejection.CAPACITY)
    shown: DeviceOrUnidentified = value(carried=False)


@dataclass(frozen=True)
class RejectedWhileDraining(Variant):
    """The same refusal as the one above, from the other cause.

    Told apart because the two send whoever reads them to different
    places: a server at capacity is a sizing question, and a server that
    is shutting down is a redeploy that will be over in a moment. Said as
    one word, every rolling restart read as a load problem, at exactly
    the moment `/readyz` was answering `draining`.
    """

    CHANNEL: ClassVar[str] = WS_CHANNEL
    LEVEL: ClassVar[int] = logging.WARNING
    TEMPLATE: ClassVar[str] = (
        "refused a websocket handshake from %s: the server is shutting down"
    )
    ARGS: ClassVar[tuple[str, ...]] = ("shown",)

    device: DeviceId | None
    session: SessionId
    reason: Rejection = value(fixed=Rejection.DRAINING)
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
    conversation: ConversationId = value(
        note=(
            "The thread the agent was talking on, stamped by the same "
            "activation that stamped the agent. A server-minted id and "
            "therefore metadata; what was said on the thread is the "
            "store's."
        )
    )
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
    reason: CloseReason = value(
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
    conversation: ConversationId = value(
        note=(
            "The thread the agent was talking on, stamped by the same "
            "activation that stamped the agent. A server-minted id and "
            "therefore metadata; what was said on the thread is the "
            "store's."
        )
    )


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
    conversation: ConversationId = value(
        note=(
            "The thread the agent was talking on, stamped by the same "
            "activation that stamped the agent. A server-minted id and "
            "therefore metadata; what was said on the thread is the "
            "store's."
        )
    )
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
    conversation: ConversationId = value(
        note=(
            "The thread the agent was talking on, stamped by the same "
            "activation that stamped the agent. A server-minted id and "
            "therefore metadata; what was said on the thread is the "
            "store's."
        )
    )
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
    conversation: ConversationId = value(
        note=(
            "The thread the agent was talking on, stamped by the same "
            "activation that stamped the agent. A server-minted id and "
            "therefore metadata; what was said on the thread is the "
            "store's."
        )
    )
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
    from_conversation: ConversationId = value(
        note="The thread the outgoing agent was on, which the handover turn belongs to."
    )
    to_conversation: ConversationId = value(
        note=(
            "The thread the incoming agent is on: its own, minted at its first "
            "activation in this session and continued at every later one."
        )
    )


@dataclass(frozen=True)
class ConversationResumed(Variant):
    """A stored thread is picked up again."""

    CHANNEL: ClassVar[str] = SESSION_CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = "session %s: resumed conversation %s from %d stored turns"
    ARGS: ClassVar[tuple[str, ...]] = ("session", "conversation", "turns")

    conversation: ConversationId = value(
        note=(
            "The thread the session moved onto, which the agent that "
            "asked for it is bound to from here."
        )
    )
    turns: Count = value(
        note=(
            "How many stored turns were rebuilt into the model's "
            "context. A count and never the dialogue: what was said is "
            "the store's."
        )
    )
    skipped: Count = value(
        note=(
            "How many stored turns could not be rebuilt because no text "
            "was kept for them, which is what a thread recorded under "
            "the stricter storage setting leaves behind."
        )
    )
    over_budget: Flag = value(
        note=(
            "Whether the thread held more than the hydration budget had "
            "room for, so what was rebuilt is its recent tail."
        )
    )


@dataclass(frozen=True)
class MilestoneRecorded(Variant):
    """A consented recap is stored as a checkpoint on its thread."""

    CHANNEL: ClassVar[str] = SESSION_CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = "session %s: recorded a recap milestone on conversation %s"
    ARGS: ClassVar[tuple[str, ...]] = ("session", "conversation")

    conversation: ConversationId = value(
        note=(
            "The thread the checkpoint was recorded on. The identity and "
            "nothing else: a recap is a summary of what a room said, so "
            "it is conversation content and lives in the store under the "
            "text switch, never on a decision track."
        )
    )


@dataclass(frozen=True)
class PromptAssembled(Variant):
    """The know-how half of a prompt is assembled and cached."""

    CHANNEL: ClassVar[str] = SESSION_CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = "session %s: assembled %d characters of prompt for %s"
    ARGS: ClassVar[tuple[str, ...]] = ("session", "characters", "agent")

    agent: Identifier = value()
    conversation: ConversationId = value(
        note=(
            "The thread the agent was talking on, stamped by the same "
            "activation that stamped the agent. A server-minted id and "
            "therefore metadata; what was said on the thread is the "
            "store's."
        )
    )
    characters: Count = value()
    sources: PromptSources = value(
        note=(
            "Each block's size by provenance: how much of the prompt "
            "came from where, never any of the prompt itself."
        )
    )


@dataclass(frozen=True)
class LlmRetry(Variant):
    """The first-token watchdog retries a round."""

    CHANNEL: ClassVar[str] = SESSION_CHANNEL
    LEVEL: ClassVar[int] = logging.WARNING
    TEMPLATE: ClassVar[str] = "session %s: no first token after %.1f s, retrying round %d"
    ARGS: ClassVar[tuple[str, ...]] = ("session", "duration_s", "round")
    NOTE: ClassVar[str] = (
        "`provider` and `type` are atomic: a provider with an identity "
        "carries both, and one the registry never built carries neither. "
        "`host` is absent for an engine that runs in this process and "
        "`model` for a type that has none to name."
    )

    agent: Identifier = value()
    conversation: ConversationId = value(
        note=(
            "The thread the agent was talking on, stamped by the same "
            "activation that stamped the agent. A server-minted id and "
            "therefore metadata; what was said on the thread is the "
            "store's."
        )
    )
    round: Whole = value()
    duration_ms: Whole = value()
    stage: Identifier = value()
    duration_s: Real = value(carried=False)
    provider: Identifier | Absent = value(default=ABSENT)
    type: Identifier | Absent = value(default=ABSENT)
    host: Identifier | Absent = value(default=ABSENT)
    model: Identifier | Absent = value(
        default=ABSENT, note="The GenAI conventions' `gen_ai.request.model`."
    )


@dataclass(frozen=True)
class LlmRound(Variant):
    """A generation call finishes."""

    CHANNEL: ClassVar[str] = SESSION_CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = "session %s: %s round %d took %.2f s over %d turns"
    ARGS: ClassVar[tuple[str, ...]] = ("session", "agent", "round", "duration_s", "turns")
    NOTE: ClassVar[str] = (
        "`provider` and `type` are atomic: a provider with an identity "
        "carries both, and one the registry never built carries neither. "
        "`host` is absent for an engine that runs in this process and "
        "`model` for a type that has none to name."
    )

    agent: Identifier = value()
    conversation: ConversationId = value(
        note=(
            "The thread the agent was talking on, stamped by the same "
            "activation that stamped the agent. A server-minted id and "
            "therefore metadata; what was said on the thread is the "
            "store's."
        )
    )
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
    provider: Identifier | Absent = value(default=ABSENT)
    type: Identifier | Absent = value(default=ABSENT)
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
    first_token_ms: Whole | Absent = value(
        default=ABSENT,
        note=(
            "Times the first spoken token, so a round that only asked "
            "for a tool carries none."
        ),
    )


@dataclass(frozen=True)
class ProviderFailed(Variant):
    """An ASR, LLM or TTS call fails."""

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
        "`provider` and `type` are atomic: a provider with an identity "
        "carries both, and one the registry never built carries neither "
        "and names no entry and no host in the sentence either. `host` "
        "is absent for an engine that runs in this process and `model` "
        "for a type that has none to name."
    )

    agent: Identifier = value()
    conversation: ConversationId = value(
        note=(
            "The thread the agent was talking on, stamped by the same "
            "activation that stamped the agent. A server-minted id and "
            "therefore metadata; what was said on the thread is the "
            "store's."
        )
    )
    error: ClassName = value(
        note="A round whose retry also stalled carries `FirstTokenTimeout`."
    )
    duration_ms: Whole = value()
    stage: Identifier = value()
    named: QuotedProvider = value(carried=False)
    outcome: ProviderOutcome = value(carried=False)
    duration_s: Real = value(carried=False)
    where: ReachingHost = value(carried=False)
    provider: Identifier | Absent = value(default=ABSENT)
    type: Identifier | Absent = value(default=ABSENT)
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
    conversation: ConversationId = value(
        note=(
            "The thread the agent was talking on, stamped by the same "
            "activation that stamped the agent. A server-minted id and "
            "therefore metadata; what was said on the thread is the "
            "store's."
        )
    )
    source: ToolSource = value(fixed=ToolSource.BUILTIN)
    tool: Identifier = value(note="The only tool names this server authors.")
    duration_ms: Whole = value()
    is_error: Flag = value()
    named: QuotedToolName = value(carried=False)
    duration_s: Real = value(carried=False)
    outcome: ToolOutcome = value(carried=False)


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
    conversation: ConversationId = value(
        note=(
            "The thread the agent was talking on, stamped by the same "
            "activation that stamped the agent. A server-minted id and "
            "therefore metadata; what was said on the thread is the "
            "store's."
        )
    )
    source: ToolSource = value(fixed=ToolSource.MCP)
    entry: Identifier = value(
        note="The configured entry, never the far side's tool name."
    )
    duration_ms: Whole = value()
    is_error: Flag = value()
    named: FromEntry = value(carried=False)
    duration_s: Real = value(carried=False)
    outcome: ToolOutcome = value(carried=False)


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
    conversation: ConversationId = value(
        note=(
            "The thread the agent was talking on, stamped by the same "
            "activation that stamped the agent. A server-minted id and "
            "therefore metadata; what was said on the thread is the "
            "store's."
        )
    )
    source: UnnamedToolSource = value()
    duration_ms: Whole = value()
    is_error: Flag = value()
    named: Nothing = value(carried=False)
    duration_s: Real = value(carried=False)
    outcome: ToolOutcome = value(carried=False)


@dataclass(frozen=True)
class ToolArgumentsCoerced(Variant):
    """A call went out with argument types this server corrected.

    One variant for all four namespaces, because the decision is one
    decision whoever runs the call, and the naming policy the three
    `tool_call` shapes make structural is carried here by `source`
    together with the two optional names: a builtin names itself, an
    MCP call names the entry an operator configured, and a device tool
    or an invented name names nothing at all.
    """

    CHANNEL: ClassVar[str] = SESSION_CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = (
        "session %s: %s tool%s was called with %d argument(s) whose types the "
        "schema declares otherwise, converted for the call"
    )
    ARGS: ClassVar[tuple[str, ...]] = ("session", "source", "named", "coerced")
    NOTE: ClassVar[str] = (
        "How many arguments were converted and never which or to what: "
        "a property name is the far side's own word and a value is what "
        "the model wrote, and neither belongs on a retained surface. "
        "What the record keeps is the arguments as the model sent them, "
        "which is where the two can be compared."
    )

    agent: Identifier = value()
    conversation: ConversationId = value(
        note=(
            "The thread the agent was talking on, stamped by the same "
            "activation that stamped the agent. A server-minted id and "
            "therefore metadata; what was said on the thread is the "
            "store's."
        )
    )
    source: ToolSource = value(note="Which namespace the corrected call reached into.")
    coerced: Count = value(
        note=(
            "How many of the call's arguments were converted, which is "
            "one or more: the event is emitted only where the copy "
            "differs from what the model sent."
        )
    )
    named: Fragment = value(
        carried=False,
        rendered_note=(
            "One of the three shapes of the `tool_call` naming policy, "
            "and any of them, because one variant carries all four "
            "sources: a builtin's own name quoted, the configured MCP "
            "entry a call reached, or nothing at all."
        ),
    )
    tool: Identifier | Absent = value(
        default=ABSENT, note="Present for a builtin, the only tool names this server authors."
    )
    entry: Identifier | Absent = value(
        default=ABSENT,
        note="Present for an MCP call: the configured entry, never the far side's tool name.",
    )


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

    reason: Suppression = value(fixed=Suppression.MIN_SPEECH)
    speech_ms: Whole = value()
    floor_ms: Real = value(carried=False)


@dataclass(frozen=True)
class BargeInInRefractory(Variant):
    """The onset transient a device's echo cancellation let through."""

    CHANNEL: ClassVar[str] = SESSION_CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = "session %s: barge-in suppressed inside the refractory window"
    ARGS: ClassVar[tuple[str, ...]] = ("session",)

    reason: Suppression = value(fixed=Suppression.REFRACTORY)
    speech_ms: Whole = value()


@dataclass(frozen=True)
class BargeInWithoutTranscript(Variant):
    """A pause that asked ASR and got nothing back."""

    CHANNEL: ClassVar[str] = SESSION_CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = "session %s: barge-in suppressed, nothing transcribed"
    ARGS: ClassVar[tuple[str, ...]] = ("session",)

    reason: Suppression = value(fixed=Suppression.NO_TRANSCRIPT)
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
    conversation: ConversationId = value(
        note=(
            "The thread the agent was talking on, stamped by the same "
            "activation that stamped the agent. A server-minted id and "
            "therefore metadata; what was said on the thread is the "
            "store's."
        )
    )
    reason: FillerSkip = value(fixed=FillerSkip.USER_SPEAKING)
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
    conversation: ConversationId = value(
        note=(
            "The thread the agent was talking on, stamped by the same "
            "activation that stamped the agent. A server-minted id and "
            "therefore metadata; what was said on the thread is the "
            "store's."
        )
    )
    reason: FillerSkip = value(fixed=FillerSkip.BARGE_IN_PENDING)


@dataclass(frozen=True)
class FillerPlayed(Variant):
    """A pre-synthesized clip masked the wait."""

    CHANNEL: ClassVar[str] = SESSION_CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = "session %s: no reply audio after %d ms, playing filler %d"
    ARGS: ClassVar[tuple[str, ...]] = ("session", "delay_ms", "phrase_index")

    agent: Identifier = value()
    conversation: ConversationId = value(
        note=(
            "The thread the agent was talking on, stamped by the same "
            "activation that stamped the agent. A server-minted id and "
            "therefore metadata; what was said on the thread is the "
            "store's."
        )
    )
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
        RejectedWhileDraining,
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

CONVERSATION_RESUMED = declare(
    "conversation_resumed",
    note=(
        "A stored thread is picked up again, at the boundary the "
        "selection ended a reply on. Counts and a flag: which thread, "
        "how much of it was rebuilt, how much of it could not be, and "
        "whether there was more of it than the budget had room for."
    ),
    variants=(ConversationResumed,),
)

MILESTONE_RECORDED = declare(
    "milestone_recorded",
    note=(
        "A consented recap is stored as a checkpoint on its thread, "
        "after the user has heard it. The thread's identity and nothing "
        "else: what the recap says is what a room said, so it is "
        "conversation content and belongs to the store."
    ),
    variants=(MilestoneRecorded,),
)

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
    variants=(LlmRetry,),
)

LLM_ROUND = declare("llm_round", note="A generation call finishes.", variants=(LlmRound,))

PROVIDER_FAILED = declare(
    "provider_failed",
    note=(
        "An ASR, LLM or TTS call fails. The class name is reported and "
        "the exception's message is not: a type name says what went "
        "wrong, a message says what a stranger wrote."
    ),
    variants=(ProviderFailed,),
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

TOOL_ARGUMENTS_COERCED = declare(
    "tool_arguments_coerced",
    note=(
        "A call went out with argument types this server corrected: a "
        "value whose string form converts to the declared type exactly "
        "is converted once, at the dispatch (#383). How many, and never "
        "which or to what."
    ),
    variants=(ToolArgumentsCoerced,),
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


# --- ota/: the configuration check and the activation ceremony --------
#
# No session exists yet at a check-in, so these records name the device
# instead. Every one of them says what the board called itself and what
# firmware it says it runs, which are far-side strings the endpoint
# bounds at its decision site and the value types bound again here.



@dataclass(frozen=True)
class OtaCheckActivating(Variant):
    """An unbound device is answered with a code to show."""

    CHANNEL: ClassVar[str] = OTA_CHANNEL
    LEVEL: ClassVar[int] = logging.WARNING
    TEMPLATE: ClassVar[str] = (
        "device %s (%s, firmware %s) has no agent and is showing activation "
        "code %s; bind it with: vinga-server config device pending claim %s <agent>"
    )
    ARGS: ClassVar[tuple[str, ...]] = ("said_device", "board", "firmware", "code", "code")

    device: DeviceId = value()
    client: ClientId | None = value(
        note=(
            "The device UUID, bounded for the event only: the token the "
            "reply issues is still signed for the header exactly as it "
            "arrived."
        )
    )
    board: BoardName = value(
        note="What the device calls itself. `unknown` when it said nothing usable."
    )
    firmware: FirmwareVersion = value(
        note=(
            "The only moment a device ever states its firmware version: "
            "the websocket handshake does not carry it."
        )
    )
    agents: AgentNames = value()
    unloaded: AgentNames = value(
        note=(
            "Agents this device is bound to that the world this server is "
            "serving does not hold. Named on every record rather than only "
            "on the one that complains, so a query for devices waiting on a "
            "reload is one field."
        )
    )
    code: ActivationCode = value()
    # The header as the firmware spelled it, rendered beside the
    # canonical form the field carries.
    said_device: ReportedMac = value(carried=False)


@dataclass(frozen=True)
class OtaCheckAgentNotLoaded(Variant):
    """The binding is there; the world this server is serving is what is
    behind. Its sentence names the same one action the session-side
    rejection does, for the same reason."""

    CHANNEL: ClassVar[str] = OTA_CHANNEL
    LEVEL: ClassVar[int] = logging.WARNING
    TEMPLATE: ClassVar[str] = (
        "device %s (%s, firmware %s) is bound to agent %s, which this server "
        "is not serving; install it with: vinga-server config apply"
    )
    ARGS: ClassVar[tuple[str, ...]] = ("said_device", "board", "firmware", "named")

    device: DeviceId = value()
    client: ClientId | None = value()
    board: BoardName = value()
    firmware: FirmwareVersion = value()
    agents: AgentNames = value()
    unloaded: AgentNames = value()
    said_device: ReportedMac = value(carried=False)
    named: AgentList = value(carried=False)


@dataclass(frozen=True)
class OtaCheckNoAgent(Variant):
    """A device bound to nothing, with no default to fall back on."""

    CHANNEL: ClassVar[str] = OTA_CHANNEL
    LEVEL: ClassVar[int] = logging.WARNING
    TEMPLATE: ClassVar[str] = (
        "device %s (%s, firmware %s) has no agent: bind it under devices "
        "or set default_agent"
    )
    ARGS: ClassVar[tuple[str, ...]] = ("said_device", "board", "firmware")

    device: DeviceId = value()
    client: ClientId | None = value()
    board: BoardName = value()
    firmware: FirmwareVersion = value()
    agents: AgentNames = value()
    unloaded: AgentNames = value()
    said_device: ReportedMac = value(carried=False)


@dataclass(frozen=True)
class OtaCheckResolved(Variant):
    """The ordinary answer: this device has an agent to talk to."""

    CHANNEL: ClassVar[str] = OTA_CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = "device %s (%s, firmware %s) resolved to agent %s%s"
    ARGS: ClassVar[tuple[str, ...]] = (
        "said_device",
        "board",
        "firmware",
        "agent",
        "bound_tail",
    )

    device: DeviceId = value()
    client: ClientId | None = value()
    board: BoardName = value()
    firmware: FirmwareVersion = value()
    agents: AgentNames = value()
    unloaded: AgentNames = value()
    said_device: ReportedMac = value(carried=False)
    agent: Identifier = value(carried=False)
    bound_tail: AlsoBoundTo = value(carried=False)


@dataclass(frozen=True)
class ActivationNotOfferedUnreadable(Variant):
    """The database could not be read, so no code was minted: this
    device may already be bound."""

    CHANNEL: ClassVar[str] = OTA_CHANNEL
    LEVEL: ClassVar[int] = logging.WARNING
    TEMPLATE: ClassVar[str] = (
        "device %s is unbound in the configuration this server started with, "
        "but the database could not be read, so no activation code was "
        "issued: this device may already be bound. Fix the database and it "
        "is offered one at its next check"
    )
    ARGS: ClassVar[tuple[str, ...]] = ("device",)

    device: DeviceId = value()
    reason: NotOffered = value(fixed=NotOffered.UNREADABLE)


@dataclass(frozen=True)
class ActivationNotOfferedRefused(Variant):
    """The pending table refused to mint, and says at which bound."""

    CHANNEL: ClassVar[str] = OTA_CHANNEL
    LEVEL: ClassVar[int] = logging.WARNING
    TEMPLATE: ClassVar[str] = (
        "device %s is unbound but was offered no activation code: %s. It is "
        "answered exactly as it was before onboarding existed, with no "
        "token; bind it by its MAC with: vinga-server config device bind "
        "%s <agent>"
    )
    ARGS: ClassVar[tuple[str, ...]] = ("device", "reason", "device")

    device: DeviceId = value()
    reason: PendingRefusal = value()


@dataclass(frozen=True)
class ActivationComplete(Variant):
    """A waiting device has been claimed."""

    CHANNEL: ClassVar[str] = OTA_CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = (
        "device %s is activated: its next configuration check hands it a token"
    )
    ARGS: ClassVar[tuple[str, ...]] = ("device",)

    device: DeviceId = value()
    agents: AgentNames = value()


@dataclass(frozen=True)
class ActivationPending(Variant):
    """A waiting device polled and is still waiting."""

    CHANNEL: ClassVar[str] = OTA_CHANNEL
    LEVEL: ClassVar[int] = logging.DEBUG
    TEMPLATE: ClassVar[str] = "device %s is still waiting to be claimed"
    ARGS: ClassVar[tuple[str, ...]] = ("device",)

    device: DeviceId = value()
    code: ActivationCode | None = value(
        note="Null for a MAC this server holds no pending entry for."
    )
    unloaded: AgentNames = value()


@dataclass(frozen=True)
class ActivationRefusedUnreadableBody(Variant):
    """A version-2 body that is not a JSON object."""

    CHANNEL: ClassVar[str] = OTA_CHANNEL
    LEVEL: ClassVar[int] = logging.WARNING
    TEMPLATE: ClassVar[str] = (
        "device %s sent a version-2 activation body that is not a JSON "
        "object; it is answered as still waiting. Nothing of the body is "
        "quoted here"
    )
    ARGS: ClassVar[tuple[str, ...]] = ("device",)

    device: DeviceId = value()
    code: ActivationCode = value()
    reason: ActivationRefusal = value(fixed=ActivationRefusal.UNREADABLE_BODY)


@dataclass(frozen=True)
class ActivationRefusedUnknownAlgorithm(Variant):
    """A version-2 body naming an algorithm this server does not know."""

    CHANNEL: ClassVar[str] = OTA_CHANNEL
    LEVEL: ClassVar[int] = logging.WARNING
    TEMPLATE: ClassVar[str] = (
        "device %s sent a version-2 activation body naming an algorithm this "
        "server does not know; it is answered as still waiting. The value is "
        "not quoted here, since it is whatever the request carried"
    )
    ARGS: ClassVar[tuple[str, ...]] = ("device",)

    device: DeviceId = value()
    code: ActivationCode = value()
    reason: ActivationRefusal = value(fixed=ActivationRefusal.UNKNOWN_ALGORITHM)


@dataclass(frozen=True)
class ActivationRefusedChallengeMismatch(Variant):
    """A version-2 body answering a challenge this server did not issue."""

    CHANNEL: ClassVar[str] = OTA_CHANNEL
    LEVEL: ClassVar[int] = logging.WARNING
    TEMPLATE: ClassVar[str] = (
        "device %s sent a version-2 activation body answering a challenge "
        "this server did not issue for it; it is answered as still waiting"
    )
    ARGS: ClassVar[tuple[str, ...]] = ("device",)

    device: DeviceId = value()
    code: ActivationCode = value()
    reason: ActivationRefusal = value(fixed=ActivationRefusal.CHALLENGE_MISMATCH)


@dataclass(frozen=True)
class OtaRequestRejected(Variant):
    """A request this endpoint could not read."""

    CHANNEL: ClassVar[str] = OTA_CHANNEL
    LEVEL: ClassVar[int] = logging.WARNING
    TEMPLATE: ClassVar[str] = "rejected OTA request: %s"
    ARGS: ClassVar[tuple[str, ...]] = ("refusal",)

    refusal: OtaRefusal = value(carried=False)


# --- onboarding/: the banner and the short path -----------------------


# The origins a line printed with no request in hand can have, which is
# the whole of what the banner below may say. `OriginSource` gained a
# fourth member for the portal line on the OTA GET (#340), which
# resolves the address a request arrived on and is not an event: the
# narrowing is what keeps the banner's declared token set the three it
# has always been, rather than widening a surface because a sentence
# elsewhere gained an answer.
BannerOrigin = Literal[
    OriginSource.PUBLIC_URL,
    OriginSource.WEBSOCKET_URL,
    OriginSource.LISTEN_ADDRESS,
]


@dataclass(frozen=True)
class OnboardingOff(Variant):
    """Devices are configured at the `server.ota_path` path."""

    CHANNEL: ClassVar[str] = ONBOARDING_CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = (
        "device onboarding is off: devices are configured at the "
        "server.ota_path path on %s (%s), which is not printed here, since "
        "that segment is this deployment's secret"
    )
    ARGS: ClassVar[tuple[str, ...]] = ("origin", "provenance")

    origin: Identifier = value()
    origin_source: BannerOrigin = value()
    onboarding: Flag = value(fixed=Flag(False))
    provenance: OriginProvenance = value(carried=False)


@dataclass(frozen=True)
class OnboardingOn(Variant):
    """Devices are configured at the short path, behind its key."""

    CHANNEL: ClassVar[str] = ONBOARDING_CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = (
        "device onboarding is on: devices are configured on %s (%s), at the "
        "short path vinga-server config ota-url prints. The path is not "
        "repeated here, since its key stands in front of the endpoint that "
        "issues device tokens"
    )
    ARGS: ClassVar[tuple[str, ...]] = ("origin", "provenance")

    origin: Identifier = value()
    origin_source: BannerOrigin = value()
    onboarding: Flag = value(fixed=Flag(True))
    keyed: Flag = value(
        note=(
            "Whether anything stands in front of the short route at all. A "
            "fact about the deployment rather than about the key, which is "
            "what makes it safe to say."
        )
    )
    provenance: OriginProvenance = value(carried=False)


@dataclass(frozen=True)
class OnboardingKeyMismatch(Variant):
    """A request carried a key-shaped segment, and not this server's."""

    CHANNEL: ClassVar[str] = ONBOARDING_CHANNEL
    LEVEL: ClassVar[int] = logging.WARNING
    TEMPLATE: ClassVar[str] = (
        "a request reached the onboarding path carrying %d characters shaped "
        "like a key, and not this server's; neither is repeated here. Check "
        "the URL typed into the device's captive portal against the one "
        "printed by vinga-server config ota-url"
    )
    ARGS: ClassVar[tuple[str, ...]] = ("attempted_length",)

    attempted_length: Count = value()


@dataclass(frozen=True)
class OnboardingKeyUnshaped(Variant):
    """A request carried something that is not key-shaped at all."""

    CHANNEL: ClassVar[str] = ONBOARDING_CHANNEL
    LEVEL: ClassVar[int] = logging.WARNING
    TEMPLATE: ClassVar[str] = (
        "a request reached the onboarding path carrying %d characters that "
        "are not shaped like a key at all, so they are not repeated here; "
        "the URL to type comes from vinga-server config ota-url"
    )
    ARGS: ClassVar[tuple[str, ...]] = ("attempted_length",)

    attempted_length: Count = value()


# --- ws.py: the handshake gate ----------------------------------------


@dataclass(frozen=True)
class AuthRejected(Variant):
    """A handshake refused before the accept."""

    CHANNEL: ClassVar[str] = WS_CHANNEL
    LEVEL: ClassVar[int] = logging.WARNING
    TEMPLATE: ClassVar[str] = (
        "refused a websocket handshake from an unidentified client: %s"
    )
    ARGS: ClassVar[tuple[str, ...]] = ("reason",)

    device: DeviceId | None = value()
    reason: AuthRejection = value()


# --- providers/openai_asr.py: the prompt-echo guard -------------------
#
# No session or device: providers are shared singletons that serve every
# conversation, so these name the host instead.



@dataclass(frozen=True)
class EchoSkipped(Variant):
    """Under a second of budget remained, so no retry was sent."""

    CHANNEL: ClassVar[str] = ASR_CHANNEL
    LEVEL: ClassVar[int] = logging.WARNING
    TEMPLATE: ClassVar[str] = (
        "openai asr: the transcript came back as the configured prompt with "
        "%.1f s of the timeout left, too little to retry, treating %.2f s of "
        "audio as nothing said"
    )
    ARGS: ClassVar[tuple[str, ...]] = ("remaining_s", "duration_s")

    outcome: EchoOutcome = value(
        fixed=EchoOutcome.SKIPPED,
        note="Under a second of budget remained, so no retry was sent.",
    )
    duration_s: Real = value()
    host: Identifier = value()
    remaining_s: Real = value(carried=False)


@dataclass(frozen=True)
class EchoRetryTimedOut(Variant):
    """The retry outran what the first request left of the budget."""

    CHANNEL: ClassVar[str] = ASR_CHANNEL
    LEVEL: ClassVar[int] = logging.WARNING
    TEMPLATE: ClassVar[str] = (
        "openai asr: the retry outran the timeout's remaining %.1f s, "
        "treating %.2f s of audio as nothing said"
    )
    ARGS: ClassVar[tuple[str, ...]] = ("remaining_s", "duration_s")

    outcome: EchoOutcome = value(
        fixed=EchoOutcome.TIMED_OUT,
        note="The retry outran what the first request left of the budget.",
    )
    duration_s: Real = value()
    host: Identifier = value()
    retry_ms: Whole = value()
    remaining_s: Real = value(carried=False)


@dataclass(frozen=True)
class EchoConfirmed(Variant):
    """The retry came back as the configured prompt again."""

    CHANNEL: ClassVar[str] = ASR_CHANNEL
    LEVEL: ClassVar[int] = logging.WARNING
    TEMPLATE: ClassVar[str] = (
        "openai asr: the retry came back as the prompt again, treating "
        "%.2f s of audio as nothing said"
    )
    ARGS: ClassVar[tuple[str, ...]] = ("duration_s",)

    outcome: EchoOutcome = value(
        fixed=EchoOutcome.CONFIRMED_ECHO,
        note="The retry came back as the configured prompt again.",
    )
    duration_s: Real = value()
    host: Identifier = value()
    retry_ms: Whole = value()


@dataclass(frozen=True)
class EchoConfirmedEmpty(Variant):
    """The retry heard nothing."""

    CHANNEL: ClassVar[str] = ASR_CHANNEL
    LEVEL: ClassVar[int] = logging.WARNING
    TEMPLATE: ClassVar[str] = (
        "openai asr: the retry came back empty, treating %.2f s of audio as "
        "nothing said"
    )
    ARGS: ClassVar[tuple[str, ...]] = ("duration_s",)

    outcome: EchoOutcome = value(
        fixed=EchoOutcome.CONFIRMED_EMPTY,
        note="The retry heard nothing.",
    )
    duration_s: Real = value()
    host: Identifier = value()
    retry_ms: Whole = value()


@dataclass(frozen=True)
class EchoRecovered(Variant):
    """The retry's transcript is heard."""

    CHANNEL: ClassVar[str] = ASR_CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = (
        "openai asr: the retry recovered %.2f s of audio the echo guard "
        "would have discarded"
    )
    ARGS: ClassVar[tuple[str, ...]] = ("duration_s",)

    outcome: EchoOutcome = value(
        fixed=EchoOutcome.RECOVERED,
        note=(
            "The retry's transcript is heard. What was recovered is not in "
            "the sentence: conversation-derived text is banned on the events "
            "however it was recovered (#165)."
        ),
    )
    duration_s: Real = value()
    host: Identifier = value()
    retry_ms: Whole = value()


# --- providers/world.py: what building an entry noticed ---------------
#
# No session and no device: this is said while a world is being built,
# which is a boot or an apply and never a conversation.


@dataclass(frozen=True)
class ProviderReachesLoopback(Variant):
    """A built entry points at this machine, from inside a container."""

    CHANNEL: ClassVar[str] = PROVIDERS_CHANNEL
    LEVEL: ClassVar[int] = logging.WARNING
    TEMPLATE: ClassVar[str] = (
        "providers.%s.%s reaches %s, which inside a container is the "
        "container itself rather than the machine running it; if that "
        "endpoint is on the machine, name it instead "
        "(host.docker.internal, which the compose file resolves on every "
        "platform) and apply"
    )
    ARGS: ClassVar[tuple[str, ...]] = ("stage", "provider", "host")
    NOTE: ClassVar[str] = (
        "A warning and never a refusal: localhost is right for an "
        "endpoint sharing this container or its network namespace, and "
        "only the deployment knows which it is."
    )

    stage: Identifier = value()
    provider: Identifier = value(note="The configuration entry, by name.")
    type: Identifier = value(note="The entry's provider type.")
    host: LoopbackHost = value(
        note=(
            "Which spelling of this machine the entry's endpoint named. "
            "The whole of what this event says about a base_url, and a "
            "closed set of three, so nothing an operator wrote can ride "
            "it."
        )
    )


# --- tools/mcp/: the MCP lifecycle ------------------------------------
#
# No session or device: one entry serves every conversation.



@dataclass(frozen=True)
class McpConnected(Variant):
    """An entry's connect finishes and its tools are published."""

    CHANNEL: ClassVar[str] = MCP_CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = "mcp server %s connected with %d tool(s)"
    ARGS: ClassVar[tuple[str, ...]] = ("entry", "tools")

    entry: Identifier = value()
    transport: McpTransport = value()
    tools: Count = value(note="A count, never a list.")
    duration_ms: Whole = value()


@dataclass(frozen=True)
class McpConnectFailed(Variant):
    """An entry did not come up, and its tools are absent."""

    CHANNEL: ClassVar[str] = MCP_CHANNEL
    LEVEL: ClassVar[int] = logging.WARNING
    TEMPLATE: ClassVar[str] = "mcp server %s is unavailable, its tools are absent: %s"
    ARGS: ClassVar[tuple[str, ...]] = ("entry", "failure")

    entry: Identifier = value()
    reason: McpConnectFailure = value()
    duration_ms: Whole = value(note="How long the connect ran before it failed.")
    failure: ClassNames = value(carried=False)


@dataclass(frozen=True)
class McpStopped(Variant):
    """The intentional one, a shutdown or a reload."""

    CHANNEL: ClassVar[str] = MCP_CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = "mcp server %s is stopped and its tools are gone"
    ARGS: ClassVar[tuple[str, ...]] = ("entry",)
    NOTE: ClassVar[str] = (
        "The intentional one, a shutdown or a reload, and the only "
        "`mcp_down` at INFO. No duration: how long a working connection "
        "lasted is a different number under the same name."
    )

    entry: Identifier = value()
    reason: McpDown = value(fixed=McpDown.STOPPED)


@dataclass(frozen=True)
class McpDropped(Variant):
    """The connection is dropped so the next session reconnects it."""

    CHANNEL: ClassVar[str] = MCP_CHANNEL
    LEVEL: ClassVar[int] = logging.WARNING
    TEMPLATE: ClassVar[str] = (
        "mcp server %s: dropping the connection after a failed call"
    )
    ARGS: ClassVar[tuple[str, ...]] = ("entry",)
    NOTE: ClassVar[str] = "Always beside an `mcp_call_dropped`, in that order."

    entry: Identifier = value()
    reason: McpDown = value(fixed=McpDown.CALL_FAILED)


@dataclass(frozen=True)
class McpCallDropped(Variant):
    """A tool call failed and took the connection with it."""

    CHANNEL: ClassVar[str] = MCP_CHANNEL
    LEVEL: ClassVar[int] = logging.WARNING
    TEMPLATE: ClassVar[str] = (
        "mcp server %s: the call to published tool %s failed (%s), so its "
        "answer is lost"
    )
    ARGS: ClassVar[tuple[str, ...]] = ("entry", "position", "error")

    entry: Identifier = value()
    position: Count | None = value(
        note=(
            "The tool's place in the far side's listing, counted from one. "
            "Null for a name this connection no longer knows."
        )
    )
    error: ClassNames = value(
        note=(
            "The failure's class name, and for a group of them the sorted "
            "names joined with a comma. Never a message."
        )
    )


@dataclass(frozen=True)
class McpToolShadowed(Variant):
    """A published tool is dropped because a more specific entry owns its
    name."""

    CHANNEL: ClassVar[str] = MCP_CHANNEL
    LEVEL: ClassVar[int] = logging.WARNING
    TEMPLATE: ClassVar[str] = (
        "mcp server %s: dropping published tool %d, its name is inside the "
        "namespace of the entry %s, which owns it"
    )
    ARGS: ClassVar[tuple[str, ...]] = ("entry", "position", "owner")

    entry: Identifier = value()
    position: Count = value(note="The tool's place in the far side's listing.")
    owner: Identifier = value()


@dataclass(frozen=True)
class McpReloadRefused(Variant):
    """A reload that changed nothing."""

    CHANNEL: ClassVar[str] = MCP_CHANNEL
    LEVEL: ClassVar[int] = logging.WARNING
    TEMPLATE: ClassVar[str] = (
        "mcp servers were not reloaded and nothing was changed (%s)"
    )
    ARGS: ClassVar[tuple[str, ...]] = ("reason",)

    outcome: McpReloadOutcome = value(fixed=McpReloadOutcome.REFUSED)
    reason: McpRefusal = value(
        note=(
            "Chosen where the exception is classified and never built out "
            "of its message."
        )
    )


@dataclass(frozen=True)
class McpReloadApplied(Variant):
    """A reload that was applied."""

    CHANNEL: ClassVar[str] = MCP_CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = (
        "mcp servers reloaded: %d started, %d restarted, %d stopped, %d unchanged"
    )
    ARGS: ClassVar[tuple[str, ...]] = ("started", "restarted", "stopped", "unchanged")

    outcome: McpReloadOutcome = value(fixed=McpReloadOutcome.APPLIED)
    started: Count = value()
    restarted: Count = value()
    stopped: Count = value()
    unchanged: Count = value()
    duration_ms: Whole = value(
        note=(
            "Measured from when the request was accepted, so it covers the "
            "re-read as well as the apply."
        )
    )


# --- memory/store.py --------------------------------------------------



@dataclass(frozen=True)
class MemoryUnreadable(Variant):
    """One scope of an agent's memory could not be read."""

    CHANNEL: ClassVar[str] = MEMORY_CHANNEL
    LEVEL: ClassVar[int] = logging.WARNING
    TEMPLATE: ClassVar[str] = (
        "could not read %s memory for agent %s (%s); it remembers nothing of it "
        "this round"
    )
    ARGS: ClassVar[tuple[str, ...]] = ("scope", "agent", "error")

    agent: Identifier = value(
        note=(
            "The agent whose reply the read was for, which is the acting "
            "agent rather than the owner of the rows: a device scope is read "
            "on behalf of whichever agent is speaking on that device."
        )
    )
    scope: MemoryScope = value(
        note=(
            "Which memory could not be read. A read that answers several "
            "scopes at once says this once per scope it could not answer, so "
            "nothing renders empty unreported."
        )
    )
    error: ClassName = value()


@dataclass(frozen=True)
class MemoryUnwritable(Variant):
    """A change to one scope of an agent's memory could not be stored."""

    CHANNEL: ClassVar[str] = MEMORY_CHANNEL
    LEVEL: ClassVar[int] = logging.WARNING
    TEMPLATE: ClassVar[str] = (
        "could not write %s memory for agent %s (%s); nothing was changed"
    )
    ARGS: ClassVar[tuple[str, ...]] = ("scope", "agent", "error")

    agent: Identifier = value(
        note="The agent that was acting, by its configured name."
    )
    scope: MemoryScope = value(note="Which memory the refused write was for.")
    error: ClassName = value()


@dataclass(frozen=True)
class MemoryCleanupFailed(Variant):
    """The memory of conversations that are gone could not be removed."""

    CHANNEL: ClassVar[str] = MEMORY_CHANNEL
    LEVEL: ClassVar[int] = logging.WARNING
    TEMPLATE: ClassVar[str] = (
        "could not remove the memory of conversations that are gone (%s); the next "
        "sweep takes what this one did not"
    )
    ARGS: ClassVar[tuple[str, ...]] = ("error",)

    error: ClassName = value(
        note=(
            "The class of the failure, and the whole of what this event "
            "carries. There is no agent and no scope, because what this "
            "answers for is the memory of threads nobody owns any more."
        )
    )


# --- filler.py --------------------------------------------------------



@dataclass(frozen=True)
class FillerDisabled(Variant):
    """Filler synthesis failed for one agent."""

    CHANNEL: ClassVar[str] = FILLER_CHANNEL
    LEVEL: ClassVar[int] = logging.WARNING
    TEMPLATE: ClassVar[str] = (
        "agent %s: filler synthesis failed, latency masking is off for this "
        "agent (%s)"
    )
    ARGS: ClassVar[tuple[str, ...]] = ("agent", "error")

    agent: Identifier = value()
    error: ClassName = value()


# --- capture.py: the recording surface --------------------------------



@dataclass(frozen=True)
class CaptureStarted(Variant):
    """A session is being recorded."""

    CHANNEL: ClassVar[str] = CAPTURE_CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = "session %s: capturing to %s"
    ARGS: ClassVar[tuple[str, ...]] = ("session", "path")

    session: SessionId = value()
    path: ConfiguredPath = value()


@dataclass(frozen=True)
class CaptureDirectoryUnusable(Variant):
    """The configured directory could not be prepared."""

    CHANNEL: ClassVar[str] = CAPTURE_CHANNEL
    LEVEL: ClassVar[int] = logging.WARNING
    TEMPLATE: ClassVar[str] = "session %s: not capturing, %s is unusable (%s)"
    ARGS: ClassVar[tuple[str, ...]] = ("session", "directory", "failure")

    session: SessionId = value()
    reason: CaptureDeclined = value(fixed=CaptureDeclined.UNUSABLE)
    failure: ClassName = value()
    directory: ConfiguredPath = value(carried=False)


@dataclass(frozen=True)
class CaptureBelowFloor(Variant):
    """The volume is nearly full."""

    CHANNEL: ClassVar[str] = CAPTURE_CHANNEL
    LEVEL: ClassVar[int] = logging.WARNING
    TEMPLATE: ClassVar[str] = (
        "session %s: not capturing, %.0f MB free is below the %.0f MB floor"
    )
    ARGS: ClassVar[tuple[str, ...]] = ("session", "free", "floor_mb")

    session: SessionId = value()
    reason: CaptureDeclined = value(fixed=CaptureDeclined.MIN_FREE_MB)
    free_mb: Count = value()
    # The measure the sentence renders, beside the rounded count the
    # field keeps, and the floor it was compared against.
    free: Real = value(carried=False)
    floor_mb: Real = value(carried=False)


@dataclass(frozen=True)
class CaptureFilesUnopenable(Variant):
    """The recording's own files would not open."""

    CHANNEL: ClassVar[str] = CAPTURE_CHANNEL
    LEVEL: ClassVar[int] = logging.WARNING
    TEMPLATE: ClassVar[str] = (
        "session %s: not capturing, could not open the files (%s)"
    )
    ARGS: ClassVar[tuple[str, ...]] = ("session", "failure")

    session: SessionId = value()
    reason: CaptureDeclined = value(fixed=CaptureDeclined.OPEN)
    failure: ClassName = value()


@dataclass(frozen=True)
class CaptureLimit(Variant):
    """A recording reached its per-session ceiling."""

    CHANNEL: ClassVar[str] = CAPTURE_CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = "session %s: capture reached its %.0f s limit"
    ARGS: ClassVar[tuple[str, ...]] = ("session", "limit_s")

    session: SessionId = value()
    limit_s: Real = value(carried=False)


@dataclass(frozen=True)
class CaptureFailed(Variant):
    """A recording stopped after a write failed."""

    CHANNEL: ClassVar[str] = CAPTURE_CHANNEL
    LEVEL: ClassVar[int] = logging.WARNING
    TEMPLATE: ClassVar[str] = "session %s: capture stopped after failing to %s (%s)"
    ARGS: ClassVar[tuple[str, ...]] = ("session", "reason", "failure")

    session: SessionId = value()
    reason: CaptureWrite = value(
        note="Which of the recording's two tracks the write was for."
    )
    failure: ClassName = value()


@dataclass(frozen=True)
class CapturePruned(Variant):
    """Old recordings were removed to stay inside the disk budget."""

    CHANNEL: ClassVar[str] = CAPTURE_CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = "capture: pruned %d session(s) to stay under %.0f MB: %s"
    ARGS: ClassVar[tuple[str, ...]] = ("removed", "budget_mb", "listed")

    sessions: SessionIds = value(note="The ids themselves, not a count.")
    removed: Count = value(carried=False)
    budget_mb: Real = value(carried=False)
    listed: SessionList = value(carried=False)


@dataclass(frozen=True)
class CaptureOverBudget(Variant):
    """The disk budget is exceeded and nothing more can be pruned."""

    CHANNEL: ClassVar[str] = CAPTURE_CHANNEL
    LEVEL: ClassVar[int] = logging.WARNING
    TEMPLATE: ClassVar[str] = (
        "capture: %.0f MB on disk is over the %.0f MB budget and nothing "
        "more can be pruned; raise max_total_mb or lower max_session_s"
    )
    ARGS: ClassVar[tuple[str, ...]] = ("used", "budget_mb")

    total_mb: Count = value()
    used: Real = value(carried=False)
    budget_mb: Real = value(carried=False)


# --- app.py: what the composition root says about capture -------------



@dataclass(frozen=True)
class CaptureEnabled(Variant):
    """Recording is on, said once at startup."""

    CHANNEL: ClassVar[str] = APP_CHANNEL
    LEVEL: ClassVar[int] = logging.WARNING
    TEMPLATE: ClassVar[str] = (
        "session capture is on: room audio and a track of the session's "
        "events are being written to %s"
    )
    ARGS: ClassVar[tuple[str, ...]] = ("path",)

    path: ConfiguredPath = value()


@dataclass(frozen=True)
class CaptureDisabled(Variant):
    """Capture is configured but off."""

    CHANNEL: ClassVar[str] = APP_CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = (
        "session capture is configured but off; set server.capture.enabled "
        "to record to %s"
    )
    ARGS: ClassVar[tuple[str, ...]] = ("path",)

    path: ConfiguredPath = value()


# --- registry.py: the shutdown drain ----------------------------------



@dataclass(frozen=True)
class DrainStarted(Variant):
    """A shutdown begins draining."""

    CHANNEL: ClassVar[str] = REGISTRY_CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = "draining %d session(s), up to %.0f s"
    ARGS: ClassVar[tuple[str, ...]] = ("sessions", "timeout_s")

    sessions: Count = value()
    timeout_s: Real = value()


@dataclass(frozen=True)
class DrainFinished(Variant):
    """Every reply finished speaking."""

    CHANNEL: ClassVar[str] = REGISTRY_CHANNEL
    LEVEL: ClassVar[int] = logging.INFO
    TEMPLATE: ClassVar[str] = "every session drained"

    sessions: Count = value()


@dataclass(frozen=True)
class DrainIncomplete(Variant):
    """A reply was cut, or a session hung."""

    CHANNEL: ClassVar[str] = REGISTRY_CHANNEL
    LEVEL: ClassVar[int] = logging.WARNING
    TEMPLATE: ClassVar[str] = (
        "drained with %d session(s) cut mid-reply and %d that did not finish"
    )
    ARGS: ClassVar[tuple[str, ...]] = ("cut_mid_reply", "unfinished")

    sessions: Count = value()
    cut_mid_reply: Count = value()
    unfinished: Count = value()
    timeout_s: Real = value()


# --- device/bindings.py: the live view of who is bound ----------------



# `BindingsSnapshotOnly` was declared here and retired with the file it
# was about (#283). It said "there is no configuration database at this
# path", which was a thing that could be true of a file a lookup did not
# find; a database the server has already migrated at boot is not
# missing by the time bindings open, so nothing can reach the event and
# a name in this vocabulary that nothing emits is worse than no name.


@dataclass(frozen=True)
class BindingsUnreadable(Variant):
    """The database could not be read, so the answer is the snapshot's."""

    CHANNEL: ClassVar[str] = BINDINGS_CHANNEL
    LEVEL: ClassVar[int] = logging.WARNING
    TEMPLATE: ClassVar[str] = (
        "cannot read the device bindings for %s; answering from the "
        "configuration this server started with, which may be older than "
        "the database. The failure's kind is recorded beside this line"
    )
    ARGS: ClassVar[tuple[str, ...]] = ("device",)

    device: DeviceId = value()
    failure: ClassName = value()


# --- config/api.py: the administration surface ------------------------



@dataclass(frozen=True)
class ApiError(Variant):
    """The configuration API failed to handle a request."""

    CHANNEL: ClassVar[str] = CONFIG_API_CHANNEL
    LEVEL: ClassVar[int] = logging.ERROR
    TEMPLATE: ClassVar[str] = (
        "the configuration API failed to handle a request (%s)"
    )
    ARGS: ClassVar[tuple[str, ...]] = ("failure",)

    failure: ClassName = value(carried=False)


@dataclass(frozen=True)
class ApiStorageError(Variant):
    """The configuration API met unreadable stored state."""

    CHANNEL: ClassVar[str] = CONFIG_API_CHANNEL
    LEVEL: ClassVar[int] = logging.ERROR
    TEMPLATE: ClassVar[str] = (
        "the configuration API met unreadable stored state (%s)"
    )
    ARGS: ClassVar[tuple[str, ...]] = ("failure",)

    failure: ClassName = value(carried=False)


OTA_CHECK = declare(
    "ota_check",
    note=(
        "What a device said about itself at its configuration check, and "
        "what this server resolved it to. No session exists yet, so the "
        "record names the device instead."
    ),
    variants=(
        OtaCheckActivating,
        OtaCheckAgentNotLoaded,
        OtaCheckNoAgent,
        OtaCheckResolved,
    ),
)

ACTIVATION_NOT_OFFERED = declare(
    "activation_not_offered",
    note="An unbound device that was answered with no activation code, and why.",
    variants=(ActivationNotOfferedUnreadable, ActivationNotOfferedRefused),
)

ACTIVATION_COMPLETE = declare(
    "activation_complete",
    note="A waiting device has been claimed; its next check hands it a token.",
    variants=(ActivationComplete,),
)

ACTIVATION_PENDING = declare(
    "activation_pending",
    note="A waiting device polled and is still waiting.",
    variants=(ActivationPending,),
)

ACTIVATION_REFUSED = declare(
    "activation_refused",
    note=(
        "A version-2 activation poll failed one of the checks this server "
        "can hold it to. Nothing of the body is ever quoted: the checks "
        "name which one failed and stop there."
    ),
    variants=(
        ActivationRefusedUnreadableBody,
        ActivationRefusedUnknownAlgorithm,
        ActivationRefusedChallengeMismatch,
    ),
)

OTA_REQUEST_REJECTED = declare(
    "ota_request_rejected",
    note=(
        "A request this endpoint could not read. The sentence is one of "
        "three fixed refusals, so nothing a request carried is "
        "interpolated into the retained log."
    ),
    variants=(OtaRequestRejected,),
)

ONBOARDING_BANNER = declare(
    "onboarding_banner",
    note="Where devices are configured, said once at startup.",
    variants=(OnboardingOff, OnboardingOn),
)

ONBOARDING_KEY_MISMATCH = declare(
    "onboarding_key_mismatch",
    note=(
        "A request carried a key-shaped segment, and not this server's. "
        "Neither is repeated."
    ),
    variants=(OnboardingKeyMismatch,),
)

ONBOARDING_KEY_UNSHAPED = declare(
    "onboarding_key_unshaped",
    note="A request carried something that is not key-shaped at all.",
    variants=(OnboardingKeyUnshaped,),
)

AUTH_REJECTED = declare(
    "auth_rejected",
    note=(
        "A handshake refused before the accept. No device: nothing is "
        "authenticated at this point, so the Device-Id header is a string "
        "whoever opened the socket chose."
    ),
    variants=(AuthRejected,),
)

ASR_PROMPT_ECHO = declare(
    "asr_prompt_echo",
    note=(
        "A transcript came back as the ASR prompt and the clip was retried "
        "once without it, on what the first request left of `timeout_s`. No "
        "session or device: providers are shared singletons that serve "
        "every conversation, so the event names the host instead."
    ),
    variants=(
        EchoSkipped,
        EchoRetryTimedOut,
        EchoConfirmed,
        EchoConfirmedEmpty,
        EchoRecovered,
    ),
)

MCP_CONNECTED = declare(
    "mcp_connected",
    note=(
        "An entry's connect finishes and its tools are published. No "
        "session or device: one entry serves every conversation, and the "
        "rest of this block is the same."
    ),
    variants=(McpConnected,),
)

MCP_DOWN = declare(
    "mcp_down",
    note="An entry fails to come up, or its connection is given up.",
    variants=(McpConnectFailed, McpStopped, McpDropped),
)

MCP_CALL_DROPPED = declare(
    "mcp_call_dropped",
    note=(
        "A tool call failed and the connection was dropped because of it. "
        "The tool is said by its position in the far side's listing and "
        "never by its name: half a published name is what the far side "
        "called its tool."
    ),
    variants=(McpCallDropped,),
)

MCP_TOOL_SHADOWED = declare(
    "mcp_tool_shadowed",
    note="A published tool is dropped because a more specific entry owns its name.",
    variants=(McpToolShadowed,),
)

MCP_RELOAD = declare(
    "mcp_reload",
    note=(
        "A reload of the MCP servers finishes, whether or not the caller is "
        "still connected. Exactly one per reload, at whichever of the two "
        "phases ended it."
    ),
    variants=(McpReloadRefused, McpReloadApplied),
)

PROVIDER_REACHES_LOOPBACK = declare(
    "provider_reaches_loopback",
    note=(
        "A provider entry built inside a container names this machine "
        "in its endpoint, which inside a container is the container. At "
        "WARNING and once per build of the entry, on the boot and on "
        "every apply that rebuilds it. Never a refusal: the same "
        "configuration is correct where the endpoint shares the "
        "container or its network namespace."
    ),
    variants=(ProviderReachesLoopback,),
)

MEMORY_UNREADABLE = declare(
    "memory_unreadable",
    note=(
        "One scope of an agent's memory could not be read, so the agent "
        "remembers nothing of it this round. Said once per scope the read "
        "could not answer, since a read that serves a whole prompt answers "
        "three of them."
    ),
    variants=(MemoryUnreadable,),
)

MEMORY_UNWRITABLE = declare(
    "memory_unwritable",
    note=(
        "A change an agent asked for could not be stored, so nothing was "
        "changed. The write path's own event, beside the read path's "
        "above: the two fail differently and answer "
        "differently. A read that fails is contained (the agent "
        "remembers nothing of that scope this round and the reply "
        "happens), while a "
        "write that fails is a sanitized refusal the model reads out, "
        "so this is where an operator learns what the database actually "
        "said, by class name and never by message."
    ),
    variants=(MemoryUnwritable,),
)

MEMORY_CLEANUP_FAILED = declare(
    "memory_cleanup_failed",
    note=(
        "State and held facts belonging to conversations that are gone "
        "could not be removed. Agent-free on purpose, beside the two "
        "events above: the boot sweep and a session's own teardown act "
        "for no agent and on no scope, so neither of the other two could "
        "report this honestly. Nothing is lost by a failure here except "
        "the space, and the next sweep takes what this one did not."
    ),
    variants=(MemoryCleanupFailed,),
)

FILLER_DISABLED = declare(
    "filler_disabled",
    note="Filler synthesis failed for one agent, so latency masking is off for it.",
    variants=(FillerDisabled,),
)

CAPTURE_STARTED = declare(
    "capture_started", note="A session is being recorded.", variants=(CaptureStarted,)
)

CAPTURE_DECLINED = declare(
    "capture_declined",
    note="A session is not being recorded, and why.",
    variants=(CaptureDirectoryUnusable, CaptureBelowFloor, CaptureFilesUnopenable),
)

CAPTURE_LIMIT = declare(
    "capture_limit",
    note="A recording reached its per-session ceiling.",
    variants=(CaptureLimit,),
)

CAPTURE_FAILED = declare(
    "capture_failed",
    note="A recording stopped after a write failed.",
    variants=(CaptureFailed,),
)

CAPTURE_PRUNED = declare(
    "capture_pruned",
    note="Old recordings were removed to stay inside the disk budget.",
    variants=(CapturePruned,),
)

CAPTURE_OVER_BUDGET = declare(
    "capture_over_budget",
    note="The disk budget is exceeded and nothing more can be pruned.",
    variants=(CaptureOverBudget,),
)

CAPTURE_ENABLED = declare(
    "capture_enabled",
    note=(
        "Said once at startup, at WARNING: recording room audio is a thing "
        "an operator should not discover by accident."
    ),
    variants=(CaptureEnabled,),
)

CAPTURE_DISABLED = declare(
    "capture_disabled",
    note="Capture is configured but off.",
    variants=(CaptureDisabled,),
)

DRAIN_STARTED = declare(
    "drain_started", note="A shutdown begins draining.", variants=(DrainStarted,)
)

DRAIN_FINISHED = declare(
    "drain_finished", note="Every reply finished speaking.", variants=(DrainFinished,)
)

DRAIN_INCOMPLETE = declare(
    "drain_incomplete",
    note="A reply was cut, or a session hung.",
    variants=(DrainIncomplete,),
)

DEVICE_BINDINGS_UNREADABLE = declare(
    "device_bindings_unreadable",
    note="The database could not be read, so the answer is the served world's.",
    variants=(BindingsUnreadable,),
)

API_ERROR = declare(
    "api_error",
    note=(
        "The configuration API failed to handle a request. The class name "
        "and nothing else."
    ),
    variants=(ApiError,),
)

API_STORAGE_ERROR = declare(
    "api_storage_error",
    note="The configuration API met unreadable stored state.",
    variants=(ApiStorageError,),
)


__all__ = [
    "ACTIVATION_COMPLETE",
    "ACTIVATION_NOT_OFFERED",
    "ACTIVATION_PENDING",
    "ACTIVATION_REFUSED",
    "AGENT_SAID",
    "API_ERROR",
    "API_STORAGE_ERROR",
    "APP_CHANNEL",
    "ASR_CHANNEL",
    "ASR_PROMPT_ECHO",
    "AUTH_REJECTED",
    "ActivationComplete",
    "ActivationNotOfferedRefused",
    "ActivationNotOfferedUnreadable",
    "ActivationPending",
    "ActivationRefusedChallengeMismatch",
    "ActivationRefusedUnknownAlgorithm",
    "ActivationRefusedUnreadableBody",
    "AgentSaid",
    "ApiError",
    "ApiStorageError",
    "AuthRejected",
    "BARGE_IN",
    "BARGE_IN_MERGED",
    "BARGE_IN_SUPPRESSED",
    "BINDINGS_CHANNEL",
    "BannerOrigin",
    "BargeIn",
    "BargeInInRefractory",
    "BargeInMerged",
    "BargeInUnderFloor",
    "BargeInWithoutTranscript",
    "BindingsUnreadable",
    "BuiltinToolCall",
    "CAPTURE_CHANNEL",
    "CAPTURE_DECLINED",
    "CAPTURE_DISABLED",
    "CAPTURE_ENABLED",
    "CAPTURE_FAILED",
    "CAPTURE_LIMIT",
    "CAPTURE_OVER_BUDGET",
    "CAPTURE_PRUNED",
    "CAPTURE_STARTED",
    "CHANNELS",
    "CONFIG_API_CHANNEL",
    "CONVERSATIONS_CHANNEL",
    "CONVERSATIONS_DROPPED",
    "CONVERSATIONS_ENABLED",
    "CONVERSATIONS_FAILED",
    "CONVERSATIONS_PRUNED",
    "CONVERSATION_RESUMED",
    "CaptureBelowFloor",
    "CaptureDirectoryUnusable",
    "CaptureDisabled",
    "CaptureEnabled",
    "CaptureFailed",
    "CaptureFilesUnopenable",
    "CaptureLimit",
    "CaptureOverBudget",
    "CapturePruned",
    "CaptureStarted",
    "CatalogError",
    "CatalogState",
    "ConversationResumed",
    "ConversationsDropped",
    "ConversationsEnabled",
    "ConversationsPruned",
    "DEVICE_BINDINGS_UNREADABLE",
    "DRAIN_FINISHED",
    "DRAIN_INCOMPLETE",
    "DRAIN_STARTED",
    "Declaration",
    "Declared",
    "DrainFinished",
    "DrainIncomplete",
    "DrainStarted",
    "EchoConfirmed",
    "EchoConfirmedEmpty",
    "EchoRecovered",
    "EchoRetryTimedOut",
    "EchoSkipped",
    "FILLER_CHANNEL",
    "FILLER_DISABLED",
    "FILLER_PLAYED",
    "FILLER_SKIPPED",
    "FillerDisabled",
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
    "LlmRound",
    "Logged",
    "MCP_CALL_DROPPED",
    "MCP_CHANNEL",
    "MCP_CONNECTED",
    "MCP_DOWN",
    "MCP_RELOAD",
    "MCP_TOOL_SHADOWED",
    "MEMORY_CHANNEL",
    "MEMORY_UNREADABLE",
    "MEMORY_UNWRITABLE",
    "MILESTONE_RECORDED",
    "McpCallDropped",
    "McpConnectFailed",
    "McpConnected",
    "McpDropped",
    "McpReloadApplied",
    "McpReloadRefused",
    "McpStopped",
    "McpToolCall",
    "McpToolShadowed",
    "MemoryCleanupFailed",
    "MemoryUnreadable",
    "MemoryUnwritable",
    "MilestoneRecorded",
    "ONBOARDING_BANNER",
    "ONBOARDING_CHANNEL",
    "ONBOARDING_KEY_MISMATCH",
    "ONBOARDING_KEY_UNSHAPED",
    "OTA_CHANNEL",
    "OTA_CHECK",
    "OTA_REQUEST_REJECTED",
    "OnboardingKeyMismatch",
    "OnboardingKeyUnshaped",
    "OnboardingOff",
    "OnboardingOn",
    "OtaCheckActivating",
    "OtaCheckAgentNotLoaded",
    "OtaCheckNoAgent",
    "OtaCheckResolved",
    "OtaRequestRejected",
    "PROMPT_ASSEMBLED",
    "PROVIDER_FAILED",
    "PROVIDER_REACHES_LOOPBACK",
    "PROVIDERS_CHANNEL",
    "PromptAssembled",
    "ProviderFailed",
    "ProviderReachesLoopback",
    "PruneFailed",
    "REGISTRY_CHANNEL",
    "REPLIED",
    "RejectedAgentNotLoaded",
    "RejectedAtCapacity",
    "RejectedBadDeviceId",
    "RejectedNoAgent",
    "RejectedWhileDraining",
    "Replied",
    "SERVER_CHANNELS",
    "SESSION_CHANNEL",
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
    "TOOL_ARGUMENTS_COERCED",
    "TOOL_CALL",
    "ToolArgumentsCoerced",
    "UnnamedToolCall",
    "Variant",
    "WS_CHANNEL",
    "WriteFailed",
    "arg_kind_of",
    "base_of",
    "carried_values",
    "catalog",
    "declaration_of",
    "declare",
    "declared_values",
    "grammar_of",
    "install",
    "installed",
    "kind_of",
    "payload_shape",
    "rendered_values",
    "tokens_of",
    "value",
]
