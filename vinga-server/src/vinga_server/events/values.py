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
    ACTIVATION_CODE,
    AGENT_LIST,
    ALSO_BOUND_TO,
    BOARD_BOUNDS,
    CLIENT_BOUNDS,
    DEVICE_OR_UNIDENTIFIED,
    EMPTY_FRAGMENT,
    EVENT_NAME,
    FIRMWARE_BOUNDS,
    FROM_ENTRY,
    LANGUAGE,
    MAC,
    ORIGIN_PROVENANCE,
    QUOTED_PROVIDER,
    QUOTED_TOOL_NAME,
    REACHING_HOST,
    REPORTED_MAC,
    SESSION_ID,
    SESSION_LIST,
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

# How a group of class names renders when a site reports several at
# once. Beside the pattern above because the joining is part of what a
# `ClassNames` is; the emitter imports it back for the untyped path it
# still serves.
CLASS_NAME_SEPARATOR: Final = ", "


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
    # `CLASS_NAME` only: whether this value may carry the ", "-joined
    # form a group of exceptions renders as. A documentation fact like
    # the three above, and a constraint the type enforces.
    JOINED: ClassVar[bool] = False
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
class ReportedMac(MachineId):
    """The Device-Id header as the firmware sent it.

    Rendered and never carried: the field beside it holds the canonical
    form `normalize_mac` answered with, and this is the spelling the OTA
    sentence shows so an operator can grep for what the board printed.
    Only a header `normalize_mac` accepted ever reaches a sentence, so
    the looser separator and case are the whole of the difference.
    """

    SYNTAX: ClassVar[Syntax | None] = REPORTED_MAC


@dataclass(frozen=True)
class ActivationCode(MachineId):
    """A claim ticket read off a device's screen.

    Not a credential, which is why it may be said at all: it names a
    board an operator is holding, and the token the reply issues never
    reaches an event.
    """

    SYNTAX: ClassVar[Syntax | None] = ACTIVATION_CODE


@dataclass(frozen=True)
class SessionIds(EventValue):
    """The session ids a prune removed, as a list on the record.

    A tuple in here so a value nothing can append to is what a variant
    holds, and each element is a `SessionId`: the element rule is that
    type's, and a copy of it here would be the second structure.
    """

    KIND: ClassVar[Kind] = Kind.ID_LIST
    ARG_KIND: ClassVar[ArgKind] = ArgKind.ID
    SYNTAX: ClassVar[Syntax | None] = SESSION_ID

    value: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.value, tuple):
            raise EventValueError("SessionIds is a tuple of session ids")
        for one in self.value:
            SessionId(one)

    def carried(self) -> list[str]:
        return list(self.value)


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
        parts = self.value.split(CLASS_NAME_SEPARATOR) if self.JOINED else [self.value]
        if not all(_CLASS_NAME.match(part) for part in parts):
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
class ClassNames(ClassName):
    """One failure's class name, or a group's names joined with `, `.

    The MCP lifecycle is where a group happens: a transport raises
    inside anyio task groups, sometimes inside a group holding a group,
    so what a handler catches is an `ExceptionGroup` whose own name says
    nothing at all. The site unwraps it to the sorted set of the names
    inside; this is the type that says the joined form is lawful, and
    the separator lives here because the joining does.
    """

    JOINED: ClassVar[bool] = True


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
class BoardName(Descriptor):
    """What a device calls itself at its configuration check.

    The OTA endpoint is unauthenticated and the board name arrives in a
    JSON body, so this is a stranger's string of a stranger's length
    with any character in it. `bounded_descriptor` truncates and strips
    the unprintables at the decision site; this is the bound applied
    again where the value reaches the surface.
    """

    BOUNDS: ClassVar[Bounds | None] = BOARD_BOUNDS


@dataclass(frozen=True)
class FirmwareVersion(Descriptor):
    """The firmware version a device states at its configuration check,
    which is the only moment it ever states one: the websocket handshake
    does not carry it."""

    BOUNDS: ClassVar[Bounds | None] = FIRMWARE_BOUNDS


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


class AuthRejection(StrEnum):
    """Why the handshake gate refused before the accept."""

    NO_TOKEN = "no_token"
    BAD_TOKEN = "bad_token"


class OriginSource(StrEnum):
    """Which configuration key the startup banner's origin came out of.

    The last of the three is a guess and the provenance fragment beside
    it says so; the token is which key was read, not how sure it is.
    """

    PUBLIC_URL = "server.public_url"
    WEBSOCKET_URL = "server.websocket_url"
    LISTEN_ADDRESS = "the listen address (server.host and server.port)"


class ActivationRefusal(StrEnum):
    """Which check a version-2 activation poll failed.

    Nothing of the body is ever quoted, on any of the three: the token
    names which check, and the sentence says the value is not repeated.
    """

    UNREADABLE_BODY = "unreadable_body"
    UNKNOWN_ALGORITHM = "unknown_algorithm"
    CHALLENGE_MISMATCH = "challenge_mismatch"


class NotOffered(StrEnum):
    """Why an unbound device was answered with no activation code.

    Three members and two shapes. `UNREADABLE` is the view's failure and
    says so in a sentence of its own; the other two are the pending
    table's bounds, worded as the sentences their warning renders,
    because that is what the surface has always carried. Long members
    and a closed set are not in tension: what makes a token a token is
    that the set is closed.
    """

    UNREADABLE = "unreadable"
    PENDING_FULL = "128 devices are already waiting to be claimed, which is the cap"
    MINT_SPENT = (
        "30 activation codes have been issued in the last 10 minutes, "
        "which is the limit"
    )


class OtaRefusal(StrEnum):
    """The whole of what a rejected OTA request may say.

    Every refusal this endpoint makes is one of these three fixed
    sentences, said once to the caller and once to the log, which is
    what keeps a header it could not read out of both. The endpoint
    reaches for these members rather than restating them, so the closed
    set and the wording have one home.
    """

    DEVICE_ID_REQUIRED = "the Device-Id header is required and holds the device MAC"
    CLIENT_ID_REQUIRED = "the Client-Id header is required and holds the device UUID"
    DEVICE_ID_UNREADABLE = (
        "the Device-Id header does not hold a MAC address; it has to be six "
        "colon-separated hex pairs, for example aa:bb:cc:dd:ee:ff. What was sent is "
        "not quoted back, since a header that missed the MAC may hold anything at all"
    )


class CaptureDeclined(StrEnum):
    """Why a session is not being recorded."""

    UNUSABLE = "unusable"
    MIN_FREE_MB = "min_free_mb"
    OPEN = "open"


class CaptureWrite(StrEnum):
    """Which of a recording's two tracks a failed write was for. Worded
    as the sentence renders it, since the sentence names the doing."""

    AUDIO = "write audio"
    EVENT = "write an event"


class EchoOutcome(StrEnum):
    """How the ASR prompt-echo guard's retry ended."""

    SKIPPED = "skipped"
    TIMED_OUT = "timed_out"
    CONFIRMED_ECHO = "confirmed_echo"
    CONFIRMED_EMPTY = "confirmed_empty"
    RECOVERED = "recovered"


class McpTransport(StrEnum):
    """How a configured MCP entry is reached."""

    STDIO = "stdio"
    STREAMABLE_HTTP = "streamable_http"


class McpDown(StrEnum):
    """Why an entry's tools are gone.

    Six members and two shapes: the four a failed connect is classified
    into, the intentional stop, and the drop that follows a failed call.
    Every one is chosen where the exception is classified and never
    built out of its message.
    """

    TRANSPORT_FAILED = "transport_failed"
    INITIALIZE_FAILED = "initialize_failed"
    DISCOVERY_FAILED = "discovery_failed"
    CONNECT_TIMEOUT = "connect_timeout"
    STOPPED = "stopped"
    CALL_FAILED = "call_failed"


class McpReloadOutcome(StrEnum):
    """Whether a reload changed anything."""

    APPLIED = "applied"
    REFUSED = "refused"


class McpRefusal(StrEnum):
    """Why a reload was refused and nothing was changed. Chosen where
    the exception is classified and never built out of its message."""

    IN_PROGRESS = "in_progress"
    DATABASE_BUSY = "database_busy"
    UNREADABLE = "unreadable"
    INVALID = "invalid"
    UNEXPECTED = "unexpected"


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


@dataclass(frozen=True)
class AuthRejectionToken(TokenValue):
    ENUM: ClassVar[type[StrEnum]] = AuthRejection


@dataclass(frozen=True)
class OriginSourceToken(TokenValue):
    ENUM: ClassVar[type[StrEnum]] = OriginSource


@dataclass(frozen=True)
class ActivationRefusalToken(TokenValue):
    ENUM: ClassVar[type[StrEnum]] = ActivationRefusal


@dataclass(frozen=True)
class NotOfferedToken(TokenValue):
    ENUM: ClassVar[type[StrEnum]] = NotOffered


@dataclass(frozen=True)
class PendingRefusal(NotOfferedToken):
    """The two bounds the pending table refuses a code at, and only
    those. The view's own failure says so in a sentence of its own and
    cannot be built here, which is the narrowing the untyped registry
    spelled out variant by variant."""

    MEMBERS: ClassVar[frozenset[str] | None] = frozenset(
        {NotOffered.PENDING_FULL, NotOffered.MINT_SPENT}
    )


@dataclass(frozen=True)
class OtaRefusalToken(TokenValue):
    ENUM: ClassVar[type[StrEnum]] = OtaRefusal


@dataclass(frozen=True)
class CaptureDeclinedToken(TokenValue):
    ENUM: ClassVar[type[StrEnum]] = CaptureDeclined


@dataclass(frozen=True)
class CaptureWriteToken(TokenValue):
    ENUM: ClassVar[type[StrEnum]] = CaptureWrite


@dataclass(frozen=True)
class EchoOutcomeToken(TokenValue):
    ENUM: ClassVar[type[StrEnum]] = EchoOutcome


@dataclass(frozen=True)
class McpTransportToken(TokenValue):
    ENUM: ClassVar[type[StrEnum]] = McpTransport


@dataclass(frozen=True)
class McpDownToken(TokenValue):
    ENUM: ClassVar[type[StrEnum]] = McpDown


@dataclass(frozen=True)
class McpConnectFailure(McpDownToken):
    """The four a failed connect is classified into. A stop and a drop
    after a failed call are the other two ways down and each says so in
    a sentence of its own, so neither can be built for the sentence that
    reports an entry that never came up."""

    MEMBERS: ClassVar[frozenset[str] | None] = frozenset(
        {
            McpDown.TRANSPORT_FAILED,
            McpDown.INITIALIZE_FAILED,
            McpDown.DISCOVERY_FAILED,
            McpDown.CONNECT_TIMEOUT,
        }
    )


@dataclass(frozen=True)
class McpReloadOutcomeToken(TokenValue):
    ENUM: ClassVar[type[StrEnum]] = McpReloadOutcome


@dataclass(frozen=True)
class McpRefusalToken(TokenValue):
    ENUM: ClassVar[type[StrEnum]] = McpRefusal


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
class SessionList(Fragment):
    """The session ids a prune removed, comma-joined, for the sentence
    that names them. The field beside it carries the ids themselves."""

    GRAMMAR: ClassVar[Grammar | None] = SESSION_LIST

    @classmethod
    def of(cls, sessions: tuple[str, ...]) -> "SessionList":
        return cls(", ".join(sessions))


@dataclass(frozen=True)
class OriginProvenance(Fragment):
    """Which configuration key the startup banner's origin came out of,
    and whether it was read or inferred.

    Built by `Origin.provenance`, which is where the guess and its
    reasons are decided; this is the type that says the assembled shape
    is one the sentence may render.
    """

    GRAMMAR: ClassVar[Grammar | None] = ORIGIN_PROVENANCE


# What a refusal says instead of naming a device, wherever there is no
# device this server recognizes to name. Here rather than at the site,
# because the grammar below is what admits it and the two would
# otherwise be a pair that has to agree.
UNIDENTIFIED_DEVICE: Final = "an unidentified device"


@dataclass(frozen=True)
class DeviceOrUnidentified(Fragment):
    """The MAC behind a Device-Id header this server recognizes, or the
    fixed phrase. Nothing else: with device auth off nothing has
    verified that header, so an unrecognized one names no device."""

    GRAMMAR: ClassVar[Grammar | None] = DEVICE_OR_UNIDENTIFIED

    @classmethod
    def of(cls, mac: str | None) -> "DeviceOrUnidentified":
        return cls(mac if mac is not None else UNIDENTIFIED_DEVICE)


__all__ = [
    "ABSENT",
    "Absent",
    "ActivationCode",
    "ActivationRefusal",
    "ActivationRefusalToken",
    "AgentList",
    "AgentNames",
    "AlsoBoundTo",
    "AuthRejection",
    "AuthRejectionToken",
    "BoardName",
    "CLASS_NAME_PATTERN",
    "CLASS_NAME_SEPARATOR",
    "CaptureDeclined",
    "CaptureDeclinedToken",
    "CaptureWrite",
    "CaptureWriteToken",
    "ClassName",
    "ClassNames",
    "ClientId",
    "CloseReason",
    "CloseReasonToken",
    "ConfiguredPath",
    "Count",
    "Descriptor",
    "DeviceId",
    "DeviceOrUnidentified",
    "EchoOutcome",
    "EchoOutcomeToken",
    "EventName",
    "EventValue",
    "EventValueError",
    "FillerSkip",
    "FillerSkipToken",
    "FirmwareVersion",
    "Flag",
    "Fragment",
    "FromEntry",
    "Identifier",
    "LanguageTag",
    "MachineId",
    "McpConnectFailure",
    "McpDown",
    "McpDownToken",
    "McpRefusal",
    "McpRefusalToken",
    "McpReloadOutcome",
    "McpReloadOutcomeToken",
    "McpTransport",
    "McpTransportToken",
    "NotOffered",
    "NotOfferedToken",
    "Nothing",
    "OriginProvenance",
    "OriginSource",
    "OriginSourceToken",
    "OtaRefusal",
    "OtaRefusalToken",
    "PendingRefusal",
    "PromptSources",
    "ProviderOutcome",
    "ProviderOutcomeToken",
    "QuotedProvider",
    "QuotedToolName",
    "ReachingHost",
    "Real",
    "Rejection",
    "RejectionToken",
    "ReportedMac",
    "SessionId",
    "SessionIds",
    "SessionList",
    "Suppression",
    "SuppressionToken",
    "TextValue",
    "TokenValue",
    "ToolOutcome",
    "ToolOutcomeToken",
    "ToolSource",
    "ToolSourceToken",
    "UNIDENTIFIED_DEVICE",
    "UnnamedToolSource",
    "Whole",
]
