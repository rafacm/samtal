"""The vocabulary a typed event is written in.

An event's payload is metadata and nothing else ([the content and
telemetry ADR](../../../../docs/adr/2026-08-15-content-and-telemetry-are-separate-surfaces.md)),
and until now that was a claim the registry made ABOUT a value: a field
said "this position holds a session id" and a validator read the value
back at emit time to see whether it did. This module makes it a claim
the value itself carries. A `SessionId` is a session id because it
could not have been constructed otherwise, and a site that has one has
already proved it.

Three properties are load-bearing, and none of them is incidental:

- **Construction is the check.** Every type here refuses at
  construction what the registry used to refuse at emit. The annotation
  alone proves nothing at runtime, so the check is explicit and runs
  whatever a type checker did or did not see.
- **A refusal never repeats what it refused.** The value handed to a
  value type is exactly the thing that may not reach a log, a lane's
  stderr or an exception chain, so `EventValueError` names the type and
  the constraint and stops there. This is the same rule the enforcement
  diagnostics keep, applied one layer earlier.
- **What rides the record is a plain builtin.** `carried()` and
  `rendered()` answer `str`, `int` or the configured path object, never
  the wrapper, so a tap, a JSON formatter and a `%` rendering meet
  exactly what they met before the types existed.

The two halves are separate on purpose. `carried()` is what the payload
field holds and `rendered()` is what the sentence's `%` position
receives, and they differ where a path is concerned: the field carries
the path as text, the sentence renders the object, and that is the
shape the surface has today.

Transitional, and stated so it is not mistaken for the end state: the
kinds, the syntaxes, the descriptor bounds and the composed grammars are
imported from `events_schema.py` rather than restated here, because that
module is still the one home of those facts while the untyped registry
survives.
When the last emit site converts and the registry goes (M3), they move
into this module and the import disappears; a copy made now would be
the second structure the plan exists to remove.
"""

import os
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, ClassVar, Final

from vinga_server.events_schema import (
    AGENT_LIST,
    ALSO_BOUND_TO,
    CLIENT_BOUNDS,
    DEVICE_OR_UNIDENTIFIED,
    EMPTY_FRAGMENT,
    EVENT_NAME,
    FROM_ENTRY,
    LANGUAGE,
    MAC,
    QUOTED_PROVIDER,
    QUOTED_TOOL_NAME,
    REACHING_HOST,
    SESSION_ID,
    SOURCE_KEY_PATTERN,
    ArgKind,
    Bounds,
    Grammar,
    Kind,
    Syntax,
    matcher,
)

# A type name, which is what a `CLASS_NAME` admits. Here rather than
# beside the emitter because it is the `ClassName` value type's own
# constraint; the emitter imports it back for the untyped path it still
# serves.
CLASS_NAME_PATTERN: Final = r"[A-Za-z_][A-Za-z0-9_]*"

_CLASS_NAME = re.compile(rf"\A(?:{CLASS_NAME_PATTERN})\Z")


class EventValueError(ValueError):
    """What a value type raises when it is handed something it does not
    admit.

    Its text names the type and the constraint it failed, and never the
    value: a construction refusal reaches a lane's stderr in strict mode
    and the emitter's guard in forgiving mode, and the value is exactly
    what neither may carry. The same reason `EventSchemaError` reports a
    fixed code and a count instead of the bytes it rejected.
    """


class Absent:
    """The value of a field this variant does not carry at all.

    Distinct from `None`, and the distinction is the whole point: a
    field that is present and null is a fact the record states, and a
    field that is absent is a key the JSON object does not have. A bare
    `| None` cannot say which of the two a site meant, so a variant that
    may omit a field annotates it with this type and the payload builder
    drops it.
    """

    def __repr__(self) -> str:
        return "ABSENT"


ABSENT: Final = Absent()


@dataclass(frozen=True)
class EventValue:
    """What every declared value answers.

    `KIND` and `ARG_KIND` are the documentation facts: what this value
    is called in the generated reference as a payload field and as a
    `%` position. `SYNTAX`, `BOUNDS` and `TOKENS` are the constraint a
    reference prints beside the kind, and they are `None` where the kind
    carries no further claim.
    """

    KIND: ClassVar[Kind]
    ARG_KIND: ClassVar[ArgKind]
    SYNTAX: ClassVar[Syntax | None] = None
    BOUNDS: ClassVar[Bounds | None] = None
    TOKENS: ClassVar[frozenset[str] | None] = None
    # `COMPOSED` arguments only: the shape a formatted fragment is held
    # to. A fragment is never a payload field, so this has no field-side
    # twin.
    GRAMMAR: ClassVar[Grammar | None] = None

    def carried(self) -> object:
        """The plain builtin this value rides the payload as."""
        raise NotImplementedError

    def rendered(self) -> object:
        """What the sentence's `%` position receives. The carried value
        wherever the two are the same thing, which is everywhere except
        a configured path."""
        return self.carried()


@dataclass(frozen=True)
class TextValue(EventValue):
    """A value that is a string on both surfaces."""

    value: str

    def carried(self) -> str:
        return self.value


@dataclass(frozen=True)
class Identifier(TextValue):
    """A trusted name the operator or this server chose: an agent, a
    configured entry, a pipeline stage, a path, an origin.

    Trusted is about provenance rather than shape, so the domain is the
    configuration's own (`IDENTIFIER_DOMAIN`) and no tighter: a name
    carrying a quote or a control character is lawful configuration
    today, and a value type claiming more would refuse a deployment the
    configuration accepted.
    """

    KIND: ClassVar[Kind] = Kind.IDENTIFIER
    ARG_KIND: ClassVar[ArgKind] = ArgKind.IDENTIFIER

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise EventValueError("an Identifier is a string")
        if not self.value.strip():
            raise EventValueError("an Identifier is non-empty once stripped")


@dataclass(frozen=True)
class MachineId(TextValue):
    """A bounded machine form this server minted or normalized, held to
    the named syntax its subclass declares."""

    KIND: ClassVar[Kind] = Kind.ID
    ARG_KIND: ClassVar[ArgKind] = ArgKind.ID

    def __post_init__(self) -> None:
        syntax = self.SYNTAX
        if syntax is None:  # pragma: no cover - a subclass without one
            raise EventValueError("a MachineId subclass declares its syntax")
        if not isinstance(self.value, str):
            raise EventValueError(f"a {syntax.name} is a string")
        if len(self.value) > syntax.max_length or not matcher(syntax.pattern).match(
            self.value
        ):
            raise EventValueError(f"a {syntax.name} matches the {syntax.name} syntax")


@dataclass(frozen=True)
class SessionId(MachineId):
    """The id this server minted for one conversation."""

    SYNTAX: ClassVar[Syntax | None] = SESSION_ID


@dataclass(frozen=True)
class DeviceId(MachineId):
    """One board's MAC, in the canonical form `normalize_mac` answers
    with.

    The session channel's second identity, and the reason the session
    base could not be described before this type existed: what a device
    calls itself arrives in a header an unauthenticated caller wrote, and
    the value that rides the record is the normalized form this server
    made of it or nothing at all. A site that holds one of these has
    already been through `normalize_mac`.
    """

    SYNTAX: ClassVar[Syntax | None] = MAC


@dataclass(frozen=True)
class LanguageTag(MachineId):
    """A language code as an ASR engine reports it.

    Far-side in provenance and bounded in shape, which is why it is an
    `ID` with a syntax rather than a descriptor: what an engine may
    answer is a code, and a code that is not one is a value this surface
    declines rather than truncates.
    """

    SYNTAX: ClassVar[Syntax | None] = LANGUAGE


@dataclass(frozen=True)
class EventName(MachineId):
    """The catalog's own key, carried in every payload as `event`.

    Never constructed by a site: the emitter derives it from the
    declaration the variant belongs to, which is what stops a caller
    naming an event at all. It is a value type so that the base field
    has the same declared shape as every other field.
    """

    SYNTAX: ClassVar[Syntax | None] = EVENT_NAME


@dataclass(frozen=True)
class ClassName(TextValue):
    """An exception or type name, which is the whole of what an event
    may say about a failure: a type name says what went wrong, a message
    says what a stranger wrote."""

    KIND: ClassVar[Kind] = Kind.CLASS_NAME
    ARG_KIND: ClassVar[ArgKind] = ArgKind.CLASS_NAME

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise EventValueError("a ClassName is a string")
        if not _CLASS_NAME.match(self.value):
            raise EventValueError("a ClassName is a Python identifier")

    @classmethod
    def of(cls, failure: BaseException) -> "ClassName":
        """The class of a failure, named.

        The constructor a failing site should reach for, because it
        takes the exception rather than a string: a site that has to
        spell `type(exc).__name__` is a site one edit away from spelling
        `str(exc)`, and that edit is the leak.
        """
        return cls(type(failure).__name__)


@dataclass(frozen=True)
class Count(EventValue):
    """A whole number of zero or more, for the values whose meaning is
    how many."""

    KIND: ClassVar[Kind] = Kind.COUNT
    ARG_KIND: ClassVar[ArgKind] = ArgKind.COUNT

    value: int

    def __post_init__(self) -> None:
        # `bool` first and refused: `True` is an `int` to `isinstance`,
        # and a boolean in a count is a bug rather than a quantity.
        if isinstance(self.value, bool) or not isinstance(self.value, int):
            raise EventValueError("a Count is a whole number")
        if self.value < 0:
            raise EventValueError("a Count is zero or more")

    def carried(self) -> int:
        return self.value


@dataclass(frozen=True)
class ConfiguredPath(EventValue):
    """A directory or file an operator configured.

    The one value whose two surfaces differ: the payload field carries
    the path as text and the sentence renders the object itself, which
    is what every path-bearing event does today and what keeps a record
    identical through the conversion.

    No character class and no length, for the reason `Identifier` gives:
    what an operator may call a directory is the filesystem's business
    and the configuration's, not this module's.
    """

    KIND: ClassVar[Kind] = Kind.IDENTIFIER
    ARG_KIND: ClassVar[ArgKind] = ArgKind.PATHLIKE

    value: str | os.PathLike[str]

    def __post_init__(self) -> None:
        if not isinstance(self.value, (str, os.PathLike)):
            raise EventValueError("a ConfiguredPath is a str or an os.PathLike")
        if not os.fspath(self.value).strip():
            raise EventValueError("a ConfiguredPath is non-empty once stripped")

    def carried(self) -> str:
        return os.fspath(self.value)

    def rendered(self) -> object:
        return self.value


@dataclass(frozen=True)
class Whole(EventValue):
    """A whole number that is a measurement rather than a quantity: a
    protocol version, a round, a duration in milliseconds.

    Beside `Count` rather than instead of it, because the two answer
    different questions and the surface has always told them apart: a
    count is how many, and this is how much or which."""

    KIND: ClassVar[Kind] = Kind.INT
    ARG_KIND: ClassVar[ArgKind] = ArgKind.INT

    value: int

    def __post_init__(self) -> None:
        if isinstance(self.value, bool) or not isinstance(self.value, int):
            raise EventValueError("a Whole is a whole number")

    def carried(self) -> int:
        return self.value


@dataclass(frozen=True)
class Real(EventValue):
    """A measurement in seconds, or any other real quantity a sentence
    renders with `%.2f`.

    An `int` is admitted, because a site whose measure happens to be
    integral passes one and the surface has always taken it. NaN and the
    infinities are not: they are not measurements, and JSON cannot carry
    them.
    """

    KIND: ClassVar[Kind] = Kind.FLOAT
    ARG_KIND: ClassVar[ArgKind] = ArgKind.FLOAT

    value: float

    def __post_init__(self) -> None:
        if isinstance(self.value, bool):
            raise EventValueError("a Real is a number")
        if isinstance(self.value, int):
            return
        if not isinstance(self.value, float) or self.value != self.value:
            raise EventValueError("a Real is a number")
        if self.value in (float("inf"), float("-inf")):
            raise EventValueError("a Real is finite")

    def carried(self) -> float:
        return self.value


@dataclass(frozen=True)
class Flag(EventValue):
    """A boolean, and only a boolean: the one kind for which `1` is a
    different fact rather than the same one written shorter."""

    KIND: ClassVar[Kind] = Kind.BOOL
    ARG_KIND: ClassVar[ArgKind] = ArgKind.BOOL

    value: bool

    def __post_init__(self) -> None:
        if not isinstance(self.value, bool):
            raise EventValueError("a Flag is a boolean")

    def carried(self) -> bool:
        return self.value


@dataclass(frozen=True)
class AgentNames(EventValue):
    """The configured agent names a device is bound to.

    A list on the record, which is what the payload has always carried,
    and a tuple in here, so a value nothing can append to is what a
    variant holds.
    """

    KIND: ClassVar[Kind] = Kind.IDENTIFIER_LIST

    value: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.value, tuple):
            raise EventValueError("AgentNames is a tuple of names")
        for one in self.value:
            # Through `Identifier` rather than beside it: the element
            # rule is the same rule, and a copy of it here would be the
            # second structure.
            Identifier(one)

    def carried(self) -> list[str]:
        return list(self.value)


@dataclass(frozen=True)
class Descriptor(TextValue):
    """A far-side string retained deliberately, bounded and sanitized at
    its decision site and bounded again here.

    The ADR's 2026-08-17 amendment is what admits these at all: what a
    device says ABOUT ITSELF at check-in is metadata once bounded, while
    what a person said through it never is. The bound is the subclass's,
    because the decision site's is, and it is applied here a second time
    for the reason the registry applied it a second time: the site that
    bounds it and the surface that carries it are different pieces of
    code, and only one of them is this one.
    """

    KIND: ClassVar[Kind] = Kind.DESCRIPTOR
    ARG_KIND: ClassVar[ArgKind] = ArgKind.DESCRIPTOR

    def __post_init__(self) -> None:
        bounds = self.BOUNDS
        if bounds is None:  # pragma: no cover - a subclass without one
            raise EventValueError("a Descriptor subclass declares its bounds")
        if not isinstance(self.value, str):
            raise EventValueError("a Descriptor is a string")
        if not self.value or len(self.value) > bounds.max_length:
            raise EventValueError(
                f"a Descriptor is between 1 and {bounds.max_length} characters"
            )
        if bounds.charset == "printable" and not self.value.isprintable():
            raise EventValueError("a Descriptor is printable throughout")


@dataclass(frozen=True)
class ClientId(Descriptor):
    """The device UUID as the firmware sent it, bounded for the event.

    The capture manifest and the conversation store keep the header as
    it arrived; this is the copy the retained telemetry may carry.
    """

    BOUNDS: ClassVar[Bounds | None] = CLIENT_BOUNDS


@dataclass(frozen=True)
class PromptSources(EventValue):
    """How much of a prompt came from where, by provenance.

    The one structured kind on the surface, and the reason it is lawful
    at all is that it carries sizes rather than text: a block's
    provenance is this server's own word for a configuration key, and
    its value is a character count. The grammar is the know-how half
    only, so `memory` is refused here like any unknown prefix, which is
    the `prompt_assembled` event's own decision made unrepresentable.
    """

    KIND: ClassVar[Kind] = Kind.SOURCES

    value: dict[str, int]

    def __post_init__(self) -> None:
        if not isinstance(self.value, dict):
            raise EventValueError("PromptSources is a mapping")
        for key, held in self.value.items():
            if not isinstance(key, str) or not matcher(SOURCE_KEY_PATTERN).match(key):
                raise EventValueError("a PromptSources key is a declared provenance form")
            if isinstance(held, bool) or not isinstance(held, int) or held < 0:
                raise EventValueError("a PromptSources value is a character count")

    def carried(self) -> dict[str, int]:
        return dict(self.value)


# --- the closed sets, as types ----------------------------------------
#
# A token used to be a string a site wrote and a set the registry
# restated; here it is a member of an enumeration, so a site that names
# one has named a value that exists. The enumerations are restated from
# their decision sites rather than imported from them, for the reason
# `events_schema.py` restates the descriptor bounds: this package
# imports the standard library and the schema and nothing else, and the
# decision sites live in `device/`, `runtime/` and `conversations/`,
# which import THIS. The unit tests hold the restatements equal to their
# sites, which is where the conformance walk's token check goes.


class CloseReason(StrEnum):
    """What ended a session, first cause winning."""

    LIMIT = "limit"
    IDLE = "idle"
    DRAIN = "drain"
    CLIENT = "client"
    ERROR = "error"


class Rejection(StrEnum):
    """Why a device was turned away, on either scope."""

    BAD_DEVICE_ID = "bad_device_id"
    AGENT_NOT_LOADED = "agent_not_loaded"
    NO_AGENT = "no_agent"
    CAPACITY = "capacity"


class Suppression(StrEnum):
    """Which barge-in gate dropped an interruption."""

    MIN_SPEECH = "min_speech"
    REFRACTORY = "refractory"
    NO_TRANSCRIPT = "no_transcript"


class FillerSkip(StrEnum):
    """Why the latency mask did not play."""

    USER_SPEAKING = "user_speaking"
    BARGE_IN_PENDING = "barge_in_pending"


class ToolSource(StrEnum):
    """Which namespace a tool call reached into."""

    BUILTIN = "builtin"
    DEVICE = "device"
    MCP = "mcp"
    UNKNOWN = "unknown"


class ProviderOutcome(StrEnum):
    """How a provider call ended, in the words its sentence uses. A
    timeout is worded as one, because where traffic is dropped rather
    than refused the whole symptom is a wait."""

    TIMED_OUT = "timed out"
    FAILED = "failed"


class ToolOutcome(StrEnum):
    """The tail a `tool_call` sentence ends with. Two values, one of
    them empty, which is a closed set like any other: what makes a token
    a token is that the set is closed, not that its members are long."""

    ANSWERED = ""
    FAILED = " and failed"


@dataclass(frozen=True)
class TokenValue(TextValue):
    """One member of a closed set, held to it at construction.

    `ENUM` is the set and `MEMBERS` narrows it where one variant admits
    fewer than the enumeration does (a `tool_call` that names nothing is
    a device call or an invented one, never a builtin). The declared set
    a reference prints is derived from whichever applies, so the
    narrowing is stated once and read everywhere.
    """

    KIND: ClassVar[Kind] = Kind.TOKEN
    ARG_KIND: ClassVar[ArgKind] = ArgKind.TOKEN
    ENUM: ClassVar[type[StrEnum]]
    MEMBERS: ClassVar[frozenset[str] | None] = None

    def __init_subclass__(cls, **kw: Any) -> None:
        super().__init_subclass__(**kw)
        enum = cls.__dict__.get("ENUM") or getattr(cls, "ENUM", None)
        if enum is None:  # pragma: no cover - a subclass declared later
            return
        narrowed = cls.__dict__.get("MEMBERS")
        cls.TOKENS = (
            frozenset(narrowed)
            if narrowed is not None
            else frozenset(str(one) for one in enum)
        )

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise EventValueError(f"a {type(self).__name__} is one of its declared tokens")
        if str(self.value) not in (self.TOKENS or frozenset()):
            raise EventValueError(f"a {type(self).__name__} is one of its declared tokens")

    def carried(self) -> str:
        # `str()` rather than the member itself: an enumeration member is
        # a `str` subclass, and a record carrying one would put the
        # subclass's name into a baseline's argument types and its
        # `repr` into anything that renders it.
        return str(self.value)


@dataclass(frozen=True)
class CloseReasonToken(TokenValue):
    ENUM: ClassVar[type[StrEnum]] = CloseReason


@dataclass(frozen=True)
class RejectionToken(TokenValue):
    ENUM: ClassVar[type[StrEnum]] = Rejection


@dataclass(frozen=True)
class SuppressionToken(TokenValue):
    ENUM: ClassVar[type[StrEnum]] = Suppression


@dataclass(frozen=True)
class FillerSkipToken(TokenValue):
    ENUM: ClassVar[type[StrEnum]] = FillerSkip


@dataclass(frozen=True)
class ToolSourceToken(TokenValue):
    ENUM: ClassVar[type[StrEnum]] = ToolSource


@dataclass(frozen=True)
class UnnamedToolSource(ToolSourceToken):
    """The two sources a `tool_call` may not name: a board's own
    vocabulary and whatever a model invented. Narrowed at construction,
    so the variant that names nothing cannot be built for a builtin."""

    MEMBERS: ClassVar[frozenset[str] | None] = frozenset(
        {ToolSource.DEVICE, ToolSource.UNKNOWN}
    )


@dataclass(frozen=True)
class ProviderOutcomeToken(TokenValue):
    ENUM: ClassVar[type[StrEnum]] = ProviderOutcome


@dataclass(frozen=True)
class ToolOutcomeToken(TokenValue):
    ENUM: ClassVar[type[StrEnum]] = ToolOutcome


# --- the formatted fragments ------------------------------------------
#
# A sentence sometimes renders a shape rather than a value: a
# parenthesized tail, a quoted name, the nothing a site says where it
# has nothing to add. Those positions are bounded by STRUCTURE, never by
# a character class or a length, because what they hold is configured
# names and what an operator may call something is not this module's
# business. Each is a value type whose grammar is the registry's own, so
# a fragment that does not fit the shape its declaration prints cannot
# be constructed.


@dataclass(frozen=True)
class Fragment(TextValue):
    """A formatted fragment, held to the named grammar its subclass
    declares. Never a payload field: `carried()` exists only because
    `rendered()` is defined in terms of it."""

    ARG_KIND: ClassVar[ArgKind] = ArgKind.COMPOSED

    def __post_init__(self) -> None:
        grammar = self.GRAMMAR
        if grammar is None:  # pragma: no cover - a subclass without one
            raise EventValueError("a Fragment subclass declares its grammar")
        if not isinstance(self.value, str):
            raise EventValueError(f"a {grammar.name} is a string")
        if not matcher(grammar.pattern).match(self.value):
            raise EventValueError(f"a {grammar.name} matches the {grammar.name} grammar")


@dataclass(frozen=True)
class Nothing(Fragment):
    """The nothing a site renders where it has nothing to add."""

    GRAMMAR: ClassVar[Grammar | None] = EMPTY_FRAGMENT


@dataclass(frozen=True)
class AlsoBoundTo(Fragment):
    """The tail naming the other agents a device is bound to."""

    GRAMMAR: ClassVar[Grammar | None] = ALSO_BOUND_TO

    @classmethod
    def of(cls, agents: tuple[str, ...]) -> "AlsoBoundTo":
        """The tail for a device bound to these, empty for one bound to
        exactly one. Built here rather than at the site, so the tail and
        the grammar that describes it stay one statement."""
        return cls(f" (also bound to {', '.join(agents)})" if agents else "")


@dataclass(frozen=True)
class AgentList(Fragment):
    """Configured agent names, comma joined, for a sentence that names
    them."""

    GRAMMAR: ClassVar[Grammar | None] = AGENT_LIST

    @classmethod
    def of(cls, agents: tuple[str, ...]) -> "AgentList":
        return cls(", ".join(agents))


@dataclass(frozen=True)
class QuotedToolName(Fragment):
    """A builtin's name, quoted. This server's own word, and the only
    tool name that ever reaches a sentence."""

    GRAMMAR: ClassVar[Grammar | None] = QUOTED_TOOL_NAME

    @classmethod
    def of(cls, name: str) -> "QuotedToolName":
        return cls(f' "{name}"')


@dataclass(frozen=True)
class FromEntry(Fragment):
    """The configured MCP entry a call reached, never the far side's own
    tool name."""

    GRAMMAR: ClassVar[Grammar | None] = FROM_ENTRY

    @classmethod
    def of(cls, entry: str) -> "FromEntry":
        return cls(f' from entry "{entry}"')


@dataclass(frozen=True)
class QuotedProvider(Fragment):
    """The configuration entry a failing provider is, quoted."""

    GRAMMAR: ClassVar[Grammar | None] = QUOTED_PROVIDER

    @classmethod
    def of(cls, entry: str) -> "QuotedProvider":
        return cls(f' "{entry}"')


@dataclass(frozen=True)
class ReachingHost(Fragment):
    """Where a failing call was going, empty for an engine that runs in
    this process."""

    GRAMMAR: ClassVar[Grammar | None] = REACHING_HOST

    @classmethod
    def of(cls, host: str | None) -> "ReachingHost":
        return cls(f" reaching {host}" if host is not None else "")


@dataclass(frozen=True)
class DeviceOrUnidentified(Fragment):
    """The MAC behind a Device-Id header this server recognizes, or the
    fixed phrase. Nothing else: with device auth off nothing has
    verified that header, so an unrecognized one names no device."""

    GRAMMAR: ClassVar[Grammar | None] = DEVICE_OR_UNIDENTIFIED

    @classmethod
    def of(cls, mac: str | None) -> "DeviceOrUnidentified":
        return cls(mac if mac is not None else "an unidentified device")


__all__ = [
    "ABSENT",
    "CLASS_NAME_PATTERN",
    "Absent",
    "AgentList",
    "AgentNames",
    "AlsoBoundTo",
    "ClassName",
    "ClientId",
    "CloseReason",
    "CloseReasonToken",
    "ConfiguredPath",
    "Count",
    "Descriptor",
    "DeviceId",
    "DeviceOrUnidentified",
    "EventName",
    "EventValue",
    "EventValueError",
    "FillerSkip",
    "FillerSkipToken",
    "Flag",
    "Fragment",
    "FromEntry",
    "Identifier",
    "LanguageTag",
    "MachineId",
    "Nothing",
    "PromptSources",
    "ProviderOutcome",
    "ProviderOutcomeToken",
    "QuotedProvider",
    "QuotedToolName",
    "ReachingHost",
    "Real",
    "Rejection",
    "RejectionToken",
    "SessionId",
    "Suppression",
    "SuppressionToken",
    "TextValue",
    "TokenValue",
    "ToolOutcome",
    "ToolOutcomeToken",
    "ToolSource",
    "ToolSourceToken",
    "UnnamedToolSource",
    "Whole",
]
