"""The vocabulary a typed event is written in.

An event's payload is metadata and nothing else ([the content and
telemetry ADR](../../../docs/adr/2026-08-15-content-and-telemetry-are-separate-surfaces.md)),
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
kinds, the syntaxes and the descriptor bounds are imported from
`events_schema.py` rather than restated here, because that module is
still the one home of those facts while the untyped registry survives.
When the last emit site converts and the registry goes (M3), they move
into this module and the import disappears; a copy made now would be
the second structure the plan exists to remove.
"""

import os
import re
from dataclasses import dataclass
from typing import ClassVar, Final

from vinga_server.events_schema import (
    EVENT_NAME,
    SESSION_ID,
    ArgKind,
    Bounds,
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


__all__ = [
    "ABSENT",
    "CLASS_NAME_PATTERN",
    "Absent",
    "ClassName",
    "ConfiguredPath",
    "Count",
    "EventName",
    "EventValue",
    "EventValueError",
    "Identifier",
    "MachineId",
    "SessionId",
    "TextValue",
]
