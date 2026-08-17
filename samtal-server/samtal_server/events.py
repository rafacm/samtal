"""What the server says about itself, in one place, to whoever is listening.

The structured JSON records are the observability surface
([ADR](../../docs/adr/2026-08-04-json-logs-are-the-observability-surface.md)),
which makes them output rather than a debugging aid: their channel, their
sentences, their levels and their field names are a compatibility surface.
They are metadata and nothing else: the record of what was said is the
conversation store (#120, and the [content and telemetry
ADR](../../docs/adr/2026-08-15-content-and-telemetry-are-separate-surfaces.md)),
and these events are the operator's live view of the same conversation,
correlated with it by session id.
Yet the machinery serving them used to belong to one subsystem, sitting in
`device/events.py` and used by the device edge and the pipeline while every
other module hand-built an `extra={...}` dict of its own (#138).

So the emitter moved here, to an altitude neither the device edge nor a
runtime owns: `device/`, `runtime/` and every server subsystem import
downward into this module, and it imports none of them. It also stopped
being a payload factory that call sites logged around and became the thing
that emits: a site says
`events.info("heard %.2f s", seconds, event="heard", duration_s=seconds)`
and the emitter builds the payload, wraps it, and hands it to every
attached consumer.

A consumer is a **tap**. The JSON log is the first one and always attached;
the capture's decision track is the second; #120's conversation store and
the #66/#67 exporters attach as more, without touching a single emit site.
That is what the interface exists for.

Three invariants shape the dispatch, and none of them is incidental:

- **An event that is logged is an event that is recorded.** The capture
  used to be written inside the payload builder, before the logging call
  returned, so the record was offered to it first by construction. The
  taps therefore dispatch non-log taps in attachment order FIRST and the
  log LAST, which preserves that ordering exactly.
- **A broken consumer breaks nothing else.** Each tap runs under its own
  guard, so a tap that raises does not starve the taps after it, the log
  above all. The failure is reported once as a plain sentence on the
  emitter's own channel: not an event, because an event would go back
  through the taps and a broken tap would recurse into itself.
- **No consumer can rewrite what is kept.** Every non-log tap is handed
  its own deep copy of the payload, so a tap that edits a nested value,
  or adds a key `logging` reserves, changes only its own copy. The
  retained log is not reachable from code this module does not own.

The tap contract is events only. `SessionEvents.vad()` and `.dropped()`
stay capture-specific side channels: they feed the capture's VAD and drop
tracks, which are sampled per frame and which no other consumer has a
meaning for.

Two scopes, and the difference between them is a clock. A session event
carries the session's identity and is stamped with the session loop's
clock, because the capture's audio tracks are aligned by it. A server
event names what it is about explicitly (`device=`, `entry=`, `host=`) and
is stamped with `time.monotonic`, because server events fire where no loop
is running: `create_app` reports its capture directory before the server
serves anything.

And nothing leaves either emitter unjudged. `events_schema.py` declares
every event this server may emit, and `_emit` holds each emission to
that declaration before a single consumer sees it (#155): what the
caller passed is checked BEFORE the base fields are merged, so a spread
carrying `session=` cannot replace the emitter's own identity, and the
finished payload is then matched whole against the event's declared
variants, sentence and arguments included. Which of the two things that
buys happens depends on `SAMTAL_EVENTS_ENFORCEMENT`: `strict` raises, so
a lane, an import or a REPL refuses a violation outright, and
`forgiving` recovers, so a telemetry bug can never cost a reply.
"""

import asyncio
import contextlib
import logging
import math
import os
import time
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any, Protocol

from samtal_server.events_schema import (
    REGISTRY as DECLARED_EVENTS,
)
from samtal_server.events_schema import (
    SCHEMA_VIOLATION,
    SCHEMA_VIOLATION_MESSAGE,
    SOURCE_KEY_PATTERN,
    ArgKind,
    ArgSpec,
    Bounds,
    EventField,
    EventSpec,
    EventVariant,
    Kind,
    Syntax,
    matcher,
)

# The session log channel, by name rather than by `__name__`.
#
# `logs.py` emits `record.name` as the `logger` field of every JSON
# record, and the retained records are a compatibility surface, so that
# field is output: a collector filters on it. Every conversation record
# has carried `samtal_server.session` since the whole session was one
# module, and splitting the code across `device/` and `runtime/` must
# not silently rename it. Naming the channel here says what it is
# rather than which file it happens to live in, and both packages log
# conversation lines through it instead of through a module logger.
SESSION_LOGGER = "samtal_server.session"

logger = logging.getLogger(SESSION_LOGGER)


@dataclass(frozen=True)
class Emission:
    """One event, complete: everything any consumer could need.

    `payload` is the finished structured dict, the JSON object's own
    keys. `at` is a monotonic reading from the emitter's clock, which is
    what places an event on the capture's timeline. `level`, `message`
    and `args` are the numeric level and the human sentence exactly as an
    ordinary logging call would have received them, unrendered, so the
    log tap can reproduce today's record byte for byte and a consumer
    that only wants the structure reads one field and ignores the rest.
    """

    payload: dict[str, Any]
    at: float
    level: int
    message: str
    args: tuple[Any, ...]


class EventTap(Protocol):
    """One consumer of the structured events."""

    def emit(self, emission: Emission) -> None: ...


class SessionRecording(Protocol):
    """The three methods a session capture answers, as this module sees
    them.

    Described here rather than imported from `capture.py`, and the
    reason is the direction of the arrows. This module is the one every
    subsystem imports downward into, `capture.py` among them once it
    emits its own events, and an import back the other way is a cycle
    that shows up at boot as a partially initialized module rather than
    as anything a reader would recognize. A capture reaches this module
    as an object anyway, which is the whole point of the tap, so what
    was ever needed here was the shape, and a structural type is
    exactly the shape.
    """

    def event(self, payload: dict[str, Any], now: float) -> None: ...

    def vad(
        self, speech_ms: float, listening: bool, replying: bool, now: float
    ) -> None: ...

    def dropped(self, reason: str, now: float) -> None: ...


class LogTap:
    """The tap the surface is named after: one logging call per event,
    on the channel the emitter was built for, with the payload riding
    `extra=` the way every call site used to attach it by hand.

    Always attached, and always last, so nothing is written to the log
    that the consumers before it were not offered first."""

    def __init__(self, channel: logging.Logger) -> None:
        self._channel = channel

    def emit(self, emission: Emission) -> None:
        self._channel.log(
            emission.level, emission.message, *emission.args, extra=emission.payload
        )


class CaptureTap:
    """The capture's decision track, as a tap.

    A thin wrapper rather than the capture itself, because a capture is
    a recording of one session and a tap is a consumer of events: the
    capture keeps the surface it has (`event(payload, at)`), and this is
    the adapter that makes it one of many."""

    def __init__(self, capture: SessionRecording) -> None:
        self.capture = capture

    def emit(self, emission: Emission) -> None:
        self.capture.event(emission.payload, emission.at)


def session_clock() -> float:
    """The session loop's own clock, which is the one the capture's
    tracks are aligned by: an event's offset into the recorded audio is
    only meaningful against the clock the audio was stamped with.

    `time.monotonic` where no loop is running. A session only exists
    inside the loop, so that case is a session built outside one, which
    the conformance tests do to check the boundary's shape; the
    activation such a construction runs emits an event, no capture is
    attached for its reading to be compared against, and the number is
    read by nobody. Raising there instead would make building a session
    an act that needs a loop, which it never has been."""
    try:
        return asyncio.get_running_loop().time()
    except RuntimeError:
        return time.monotonic()


def _report(
    channel: logging.Logger, level: int, message: str, *args: Any
) -> None:
    """Say one plain sentence about something that went wrong, and never
    fail while saying it.

    Every report this module makes is made from inside a guard: a tap
    raised, or an emission was refused, or the enforcement itself broke.
    A logging call is not the inert operation it looks like, though. A
    filter or a handler is code somebody else installed, `handle` and
    `filter` are called unwrapped, and a formatter meets whatever the
    record carries, so the report can raise exactly where the guard has
    nothing left to catch it with. The tap report is the sharpest case:
    the failing tap is often the log tap itself, and reporting its
    failure back onto the same broken channel is the recursion the
    guard was built to stop.

    So the report is the last thing that may throw, and it does not.
    Suppressing blind is the right trade here and only here: what is
    being protected is a reply, and what is being lost is one diagnostic
    line about a diagnostic line."""
    try:
        channel.log(level, message, *args)
    except Exception:  # noqa: BLE001 - a report never costs a reply
        pass


def _offer(tap: EventTap, emission: Emission, channel: logging.Logger) -> None:
    """Hand one emission to one tap, under that tap's own guard.

    A tap that raises is reported once on `channel` as a plain sentence,
    and the taps after it still run: a consumer nobody has met yet must
    not be able to cost the operator a log line."""
    try:
        tap.emit(emission)
    except Exception as exc:  # noqa: BLE001 - a consumer never breaks the surface
        # The class names and nothing else, and no `event` field: a
        # report that went back through the taps would let a broken
        # tap recurse into itself, and a tap may be an exporter
        # holding whatever a far side answered it with.
        _report(
            channel,
            logging.WARNING,
            "an event tap (%s) failed and was skipped: %s",
            type(tap).__name__,
            type(exc).__name__,
        )


def _dispatch(
    taps: tuple[EventTap, ...], log: LogTap, emission: Emission, channel: logging.Logger
) -> None:
    """Offer one emission to every consumer, the log last.

    Each non-log tap is handed its own deep copy of the payload, and the
    log is handed the payload the emitter built. The frozen dataclass
    only stops a tap rebinding a field; the dict behind `payload` is
    ordinary and shared, so without this a tap could rewrite a nested
    value, or add a key `logging` reserves, and the line the operator
    keeps would be the one that tap chose. A consumer is by definition
    code this module does not own, and the retained log must not be
    reachable from it.

    Deep rather than shallow: the top level is where a reserved key
    would land, but `prompt_assembled` already carries a nested dict and
    a shallow copy would share it. The cost is one copy of a small dict
    per non-log tap, and none at all in the common case of no tap
    attached. `args` are deliberately not copied: they are rendered by
    `%` into a string and never written back, and copying an arbitrary
    argument is a copy that can fail.
    """
    for tap in taps:
        _offer(tap, replace(emission, payload=deepcopy(emission.payload)), channel)
    _offer(log, emission, channel)


# --- the schema, enforced ---------------------------------------------
#
# The registry declares what every event is allowed to be; this is where
# an emission meets its declaration. Two ordered steps, and the order is
# load-bearing:
#
# 1. What the caller passed is judged BEFORE the base fields are merged.
#    A session emitter owns `event`, `session` and `device`, and a
#    `**fields` spread built out of far-side data could otherwise carry
#    a `session` key that replaced the emitter's own identity and still
#    typechecked. On a server channel there is no identity to protect,
#    so `session` and `device` are ordinary declarable fields there.
# 2. The finished payload is matched WHOLE against the event's declared
#    variants: the emitting channel, the level the method chose, the
#    sentence compared byte for byte against the variant's own template,
#    the argument tuple against its per-position kinds, and the fields
#    against the variant's table. `Emission.args` reaches every tap and
#    the formatter renders them, so a rule that read the payload alone
#    would leave half the record unguarded.
#
# Nothing here is an `assert`. `python -O` strips assertions, and an
# optimized production process silently losing its enforcement is
# exactly the quiet failure #155 exists to end.

ENFORCEMENT_ENV = "SAMTAL_EVENTS_ENFORCEMENT"

STRICT = "strict"
FORGIVING = "forgiving"
ENFORCEMENT_MODES = (STRICT, FORGIVING)

# Strict by default, because the default is what every context that
# never runs the entrypoint gets: the pytest lanes, CI, an import, a
# REPL. A server process resolves the variable at construction instead,
# where unset means forgiving, since a running server is a deployment
# whatever artifact it runs from.
_enforcement = STRICT

# The registry, read through module state rather than through the import
# directly. That is the seam the emitter-mechanics tests need: they emit
# synthetic names on a synthetic channel on purpose, because what they
# prove is dispatch, taps, copy semantics and ordering rather than the
# production surface, and strict enforcement would otherwise refuse them
# by design.
_registry: dict[str, EventSpec] = DECLARED_EVENTS


class EventSchemaError(Exception):
    """What strict mode raises when an emission is not what the registry
    says that event is.

    Its text names registry-owned identifiers only. A declared event or
    field name may be said, because the registry owns it; an undeclared
    event name, an undeclared field name and every field value are
    caller-supplied bytes under this module's own model, so they are
    reported as a fixed code and a count instead."""


class EventEnforcementError(Exception):
    """An unusable `SAMTAL_EVENTS_ENFORCEMENT` value, refused at
    construction. A misspelled relaxation has to fail at boot rather
    than at the first live violation."""


def enforcement() -> str:
    """Which mode the emitters are in."""
    return _enforcement


def set_enforcement(mode: str) -> None:
    """Choose the mode explicitly. The entrypoints go through
    `resolve_enforcement`; this is what a test and that resolver both
    call underneath."""
    global _enforcement
    if mode not in ENFORCEMENT_MODES:
        raise EventEnforcementError(
            f"{ENFORCEMENT_ENV} has to be '{STRICT}' or '{FORGIVING}'"
        )
    _enforcement = mode


def resolve_enforcement(environ: Mapping[str, str] | None = None) -> str:
    """Read the mode out of the environment and apply it.

    Called by `create_app` before anything that emits is built, and by
    `main()` after it has loaded `.env`, because an import-time read
    could honor neither: `main.py` imports the app, and therefore this
    module, before `main()` runs.

    Unset means forgiving. A running server is a deployment however it
    was launched, and a wheel, source or ASGI-runner deployment must not
    be one telemetry bug away from losing a reply just because it is not
    the container. Anything else refuses, naming the variable and the
    two values it takes; the rejected spelling is deliberately not
    echoed."""
    chosen = (os.environ if environ is None else environ).get(ENFORCEMENT_ENV)
    if chosen is None:
        set_enforcement(FORGIVING)
        return FORGIVING
    set_enforcement(chosen)
    return chosen


def declared_events() -> dict[str, EventSpec]:
    """The registry the emitters are validating against."""
    return _registry


def set_declared_events(registry: dict[str, EventSpec]) -> None:
    """Install a registry. Production installs one, at import; the
    mechanics suite installs a scratch one around itself."""
    global _registry
    _registry = registry


# --- what a violation is allowed to say -------------------------------
#
# Fixed codes, registry-owned names, and counts. Nothing else: a
# complaint that quoted the value it rejected would put exactly the
# bytes this machinery exists to keep out of the retained log into the
# retained log, and a complaint that named an undeclared key would do it
# through the key, since a dict built from far-side data carries far-side
# bytes in its keys.

UNDECLARED_EVENT = "undeclared_event"
UNDECLARED_FIELDS = "undeclared_fields"
BASE_KEY_COLLISION = "base_key_collision"
WRONG_CHANNEL = "wrong_channel"
WRONG_TEMPLATE = "wrong_template"
WRONG_LEVEL = "wrong_level"
WRONG_ARITY = "wrong_arity"
MISSING_FIELD = "missing_field"
WRONG_KIND = "wrong_kind"
NOT_NULLABLE = "not_nullable"
UNLISTED_TOKEN = "unlisted_token"
BAD_SYNTAX = "bad_syntax"
BAD_BOUNDS = "bad_bounds"
BAD_ELEMENT = "bad_element"
BAD_SOURCE_KEY = "bad_source_key"
BAD_SOURCE_VALUE = "bad_source_value"

# What an event that is not in the registry is called in a diagnostic,
# since it cannot be called by the name the caller gave it.
UNDECLARED_LABEL = "an undeclared event"

# The one sentence a refusal renders, strict or forgiving: the event's
# declared name (or the fixed label above) and the violation summary.
REFUSAL_MESSAGE = "the event schema refused an emission of %s: %s"

# What the last-resort guard says. The whole enforcement path runs under
# one `try` in forgiving mode, so a bug in this module degrades an
# emission instead of raising on a reply path; the class name and
# nothing else, the way a failed tap is reported.
GUARD_MESSAGE = (
    "the event schema failed while judging an emission (%s); "
    "a schema_violation was emitted instead"
)

# What a recovered emission says instead of the sentence it was given.
#
# EVERY invalid emission loses its message AND its arguments, whatever
# was wrong with it. Dropping a payload field cannot un-render the same
# value from the arguments, and the two are not independent: one
# credential can be an undeclared field key or value and a lawful
# `IDENTIFIER` argument of the same call at once, so a recovery that
# kept the sentence whenever the arguments happened to validate would
# drop the value from the payload and print it anyway.
#
# Beside `SCHEMA_VIOLATION_MESSAGE` rather than reusing it: that one is
# the registry's own declared template for the recovery EVENT, and this
# is what a surviving event says when only its sentence had to go. Two
# outcomes, two sentences, so a reader of the retained log can tell
# which happened.
SAFE_MESSAGE = (
    "an event was refused by the event schema and its sentence replaced; "
    "reproduce it under SAMTAL_EVENTS_ENFORCEMENT=strict to see which"
)

# A type name, which is what `CLASS_NAME` admits.
CLASS_NAME_PATTERN = r"[A-Za-z_][A-Za-z0-9_]*"

# How a group of them renders when a site reports several at once.
CLASS_NAME_SEPARATOR = ", "


@dataclass(frozen=True)
class Fault:
    """One thing wrong with an emission: a fixed code, and a detail that
    is a registry-owned name, a count or an argument position."""

    code: str
    detail: str = ""

    def rendered(self) -> str:
        return f"{self.code} ({self.detail})" if self.detail else self.code


def refusal_text(label: str, faults: tuple[Fault, ...]) -> str:
    """The sentence a refusal renders, whole. The strict exception
    carries exactly this string and the forgiving complaint logs its two
    halves unrendered, so both surfaces say the same thing."""
    return REFUSAL_MESSAGE % (label, "; ".join(fault.rendered() for fault in faults))


# --- holding a value to its kind --------------------------------------


def _whole_number(value: Any) -> bool:
    """`bool` is checked first and rejected, because `True` is an `int`
    to `isinstance` and a boolean in a duration field is a bug."""
    return not isinstance(value, bool) and isinstance(value, int)


def _identifier_fault(value: Any) -> str | None:
    """A trusted configured name, held to the domain the configuration
    actually promises and to nothing tighter (`IDENTIFIER_DOMAIN`).

    `NonBlankStr` is `StringConstraints(strip_whitespace=True,
    min_length=1)`, so an agent called `secondary"agent`, one carrying a
    control character and one four thousand characters long are all
    lawful configuration today. A length or a character class here would
    turn such a deployment's every `session_open` into a violation, and
    forgiving mode would then drop the field and replace the sentence:
    lawful traffic mangled on account of a claim nobody made. Trusted is
    about provenance, not about shape. Narrowing belongs at
    configuration semantics (#168), where a refusal reaches the operator
    who can fix it."""
    if not isinstance(value, str):
        return WRONG_KIND
    if not value.strip():
        return BAD_BOUNDS
    return None


def _class_name_fault(value: Any, joined: bool) -> str | None:
    if not isinstance(value, str):
        return WRONG_KIND
    parts = value.split(CLASS_NAME_SEPARATOR) if joined else [value]
    if all(matcher(CLASS_NAME_PATTERN).match(part) for part in parts):
        return None
    return BAD_SYNTAX


def _syntax_fault(value: Any, syntax: Syntax | None) -> str | None:
    if not isinstance(value, str):
        return WRONG_KIND
    if syntax is None:
        return WRONG_KIND
    if len(value) > syntax.max_length or not matcher(syntax.pattern).match(value):
        return BAD_SYNTAX
    return None


def _bounds_fault(value: Any, bounds: Bounds | None) -> str | None:
    if not isinstance(value, str):
        return WRONG_KIND
    if bounds is None:
        return WRONG_KIND
    if not value or len(value) > bounds.max_length:
        return BAD_BOUNDS
    if bounds.charset == "printable" and not value.isprintable():
        return BAD_BOUNDS
    return None


def _sources_fault(value: Any) -> str | None:
    """The one structured kind: prompt provenance to character counts.

    The grammar is the know-how half only, so `memory` fails here like
    any unknown prefix; `prompt_assembled` reports the cached half of
    the prompt and excludes the per-round memory read deliberately."""
    if not isinstance(value, dict):
        return WRONG_KIND
    for key, held in value.items():
        if not isinstance(key, str) or not matcher(SOURCE_KEY_PATTERN).match(key):
            return BAD_SOURCE_KEY
        if not _whole_number(held) or held < 0:
            return BAD_SOURCE_VALUE
    return None


def _shape_fault(
    kind: Kind,
    value: Any,
    tokens: frozenset[str] | None,
    syntax: Syntax | None,
    bounds: Bounds | None,
    joined: bool,
) -> str | None:
    """One value against one kind. Answers the violation code, or None
    where the value is what the kind says it is."""
    if kind is Kind.IDENTIFIER:
        return _identifier_fault(value)
    if kind is Kind.TOKEN:
        if not isinstance(value, str):
            return WRONG_KIND
        return None if tokens is not None and value in tokens else UNLISTED_TOKEN
    if kind is Kind.CLASS_NAME:
        return _class_name_fault(value, joined)
    if kind is Kind.ID:
        return _syntax_fault(value, syntax)
    if kind is Kind.DESCRIPTOR:
        return _bounds_fault(value, bounds)
    if kind is Kind.INT:
        return None if _whole_number(value) else WRONG_KIND
    if kind is Kind.FLOAT:
        # An `int` satisfies `FLOAT`, since sites pass round numbers
        # where a measure is integral. NaN and the infinities do not:
        # they are not measurements, and JSON cannot carry them.
        if _whole_number(value):
            return None
        if isinstance(value, float) and math.isfinite(value):
            return None
        return WRONG_KIND
    if kind is Kind.BOOL:
        return None if isinstance(value, bool) else WRONG_KIND
    if kind is Kind.COUNT:
        return None if _whole_number(value) and value >= 0 else WRONG_KIND
    if kind is Kind.IDENTIFIER_LIST:
        if not isinstance(value, (list, tuple)):
            return WRONG_KIND
        return BAD_ELEMENT if any(_identifier_fault(one) for one in value) else None
    if kind is Kind.ID_LIST:
        if not isinstance(value, (list, tuple)):
            return WRONG_KIND
        return BAD_ELEMENT if any(_syntax_fault(one, syntax) for one in value) else None
    if kind is Kind.SOURCES:
        return _sources_fault(value)
    return WRONG_KIND


def _field_fault(field: EventField, value: Any) -> str | None:
    if value is None:
        return None if field.nullable else NOT_NULLABLE
    return _shape_fault(
        field.kind, value, field.tokens, field.syntax, field.bounds, field.joined
    )


# The argument kinds a field kind already describes. `PATHLIKE` and
# `COMPOSED` are the two the payload has no equivalent of, and they are
# handled beside this rather than by widening a field kind: a configured
# path is an object, and a formatted fragment is a grammar.
_ARGUMENT_KINDS: dict[ArgKind, Kind] = {
    ArgKind.IDENTIFIER: Kind.IDENTIFIER,
    ArgKind.TOKEN: Kind.TOKEN,
    ArgKind.CLASS_NAME: Kind.CLASS_NAME,
    ArgKind.ID: Kind.ID,
    ArgKind.DESCRIPTOR: Kind.DESCRIPTOR,
    ArgKind.INT: Kind.INT,
    ArgKind.FLOAT: Kind.FLOAT,
    ArgKind.BOOL: Kind.BOOL,
    ArgKind.COUNT: Kind.COUNT,
}


def _argument_fault(spec: ArgSpec, value: Any) -> str | None:
    if value is None:
        return None if spec.nullable else NOT_NULLABLE
    if spec.kind is ArgKind.PATHLIKE:
        # A configured directory, held to its object type and to
        # non-emptiness. No character class, for the reason
        # `_identifier_fault` gives: what an operator may call a
        # directory is the filesystem's business and the configuration's,
        # not this module's.
        if not isinstance(value, (str, os.PathLike)):
            return WRONG_KIND
        rendered = os.fspath(value)
        if not isinstance(rendered, str) or not rendered.strip():
            return BAD_BOUNDS
        return None
    if spec.kind is ArgKind.COMPOSED:
        if not isinstance(value, str):
            return WRONG_KIND
        if spec.grammar is None or not matcher(spec.grammar.pattern).match(value):
            return BAD_SYNTAX
        return None
    return _shape_fault(
        _ARGUMENT_KINDS[spec.kind],
        value,
        spec.tokens,
        spec.syntax,
        spec.bounds,
        spec.joined,
    )


# --- holding an emission to a variant ---------------------------------


def _argument_faults(
    variant: EventVariant, args: tuple[Any, ...]
) -> tuple[Fault, ...]:
    """Everything wrong with one argument tuple against one variant, by
    position."""
    if len(args) != len(variant.args):
        return (Fault(WRONG_ARITY, f"{len(variant.args)} declared"),)
    faults: list[Fault] = []
    for position, (spec, value) in enumerate(zip(variant.args, args, strict=True)):
        code = _argument_fault(spec, value)
        if code is not None:
            faults.append(Fault(code, f"argument {position}"))
    return tuple(faults)


def _field_faults(
    variant: EventVariant, payload: dict[str, Any]
) -> tuple[Fault, ...]:
    """Everything wrong with one payload against one variant: the
    declared fields in declaration order, then the count of what was not
    declared at all.

    Separate from the arguments because the recovery's final gate is
    about this half alone: a recovered emission has lost the caller's
    sentence and arguments by then, so what is left to ask is whether
    its FIELD shape is one the registry declares."""
    faults: list[Fault] = []
    for name, field in variant.fields.items():
        if name not in payload:
            if field.required:
                faults.append(Fault(MISSING_FIELD, name))
            continue
        code = _field_fault(field, payload[name])
        if code is not None:
            faults.append(Fault(code, name))
    undeclared = sum(1 for key in payload if key not in variant.fields)
    if undeclared:
        faults.append(Fault(UNDECLARED_FIELDS, str(undeclared)))
    return tuple(faults)


def _variant_faults(
    variant: EventVariant,
    args: tuple[Any, ...],
    payload: dict[str, Any],
) -> tuple[Fault, ...]:
    """Everything wrong with one emission against one variant, in a
    deterministic order: the arguments by position, then the fields.

    Channel, template and level are not checked here: they are what
    selected the variant in the first place."""
    return _argument_faults(variant, args) + _field_faults(variant, payload)


def _candidates(
    spec: EventSpec, channel: str, level: int, message: str
) -> tuple[tuple[EventVariant, ...], Fault | None]:
    """The variants this emission could still be, selected by
    registry-owned dimensions only and in one fixed order: the emitter's
    channel, then the declared templates, then the level. Answers the
    dimension that failed where nothing survives, which is what makes a
    refusal name where it went wrong without naming anything the caller
    supplied."""
    on_channel = tuple(one for one in spec.variants if one.channel == channel)
    if not on_channel:
        return (), Fault(WRONG_CHANNEL)
    said = tuple(one for one in on_channel if one.message == message)
    if not said:
        return (), Fault(WRONG_TEMPLATE)
    at_level = tuple(one for one in said if one.level == level)
    if not at_level:
        return (), Fault(WRONG_LEVEL)
    return at_level, None


@dataclass(frozen=True)
class Judgement:
    """What the validator made of one emission: the faults, and the
    variant a recovery would rebuild against.

    `variant` is set only where the selection was unambiguous, since a
    rebuild has to know exactly which shape it is rebuilding towards."""

    faults: tuple[Fault, ...]
    variant: EventVariant | None
    declared: bool
    event: str

    @property
    def label(self) -> str:
        """What a diagnostic may call this event: its declared name, or
        the fixed label, because an undeclared name is caller-supplied
        bytes like any field value."""
        return self.event if self.declared else UNDECLARED_LABEL


def _judge(
    registry: dict[str, EventSpec],
    channel: str,
    level: int,
    message: str,
    args: tuple[Any, ...],
    event: str,
    payload: dict[str, Any],
    collisions: tuple[str, ...],
) -> Judgement:
    """The whole of the second step, and the pre-merge step's result
    carried into it."""
    faults: list[Fault] = []
    if collisions:
        faults.append(Fault(BASE_KEY_COLLISION, ", ".join(collisions)))
    spec = registry.get(event)
    if spec is None:
        faults.append(Fault(UNDECLARED_EVENT))
        return Judgement(tuple(faults), None, False, "")
    candidates, dimension = _candidates(spec, channel, level, message)
    if dimension is not None:
        faults.append(dimension)
        return Judgement(tuple(faults), None, True, event)
    scored = [(_variant_faults(one, args, payload), one) for one in candidates]
    best, variant = min(scored, key=lambda pair: len(pair[0]))
    faults.extend(best)
    return Judgement(
        tuple(faults), variant if len(candidates) == 1 else None, True, event
    )


@dataclass(frozen=True)
class Checked:
    """What the emitters dispatch: the payload, level, sentence and
    arguments that survived enforcement. Identical to what the caller
    passed wherever the emission was what the registry says it is."""

    payload: dict[str, Any]
    level: int
    message: str
    args: tuple[Any, ...]


def _merged(base: dict[str, Any], fields: dict[str, Any]) -> dict[str, Any]:
    """The finished payload. The emitter's base fields win, always: a
    caller key that collides with one is a violation reported above, and
    letting it through here would be the spoofing the pre-merge step
    exists to refuse."""
    return {**base, **{key: value for key, value in fields.items() if key not in base}}


def _replacement(base: dict[str, Any]) -> Checked:
    """The declared recovery event, built from whole cloth: the fixed
    token, the emitter's own trusted identity, the fixed sentence and no
    arguments, so a hostile event name, key, value, message or argument
    in the original call reaches nothing."""
    return Checked(
        payload={**base, "event": SCHEMA_VIOLATION},
        level=logging.ERROR,
        message=SCHEMA_VIOLATION_MESSAGE,
        args=(),
    )


def _recover(
    log: logging.Logger,
    channel: str,
    base: dict[str, Any],
    event: str,
    level: int,
    message: str,
    args: tuple[Any, ...],
    fields: dict[str, Any],
) -> Checked:
    """Forgiving mode, which is one algorithm rather than a list of
    special cases, so simultaneous violations have one defined outcome
    reached the same way every time.

    Select the variant by registry-owned dimensions; drop the caller's
    sentence and arguments, ALWAYS, because an invalid emission is one
    this module has decided it cannot read and half of it is rendered
    text; then rebuild the payload field by field against the variant,
    keeping only what validates and dropping every offender rather than
    failing at the first; then hold the rebuilt payload to that
    variant's field table again. That last check is what stops a
    recovery dispatching a shape the generated reference denies exists:
    a rebuild that leaves a required field missing becomes the declared
    `schema_violation` event outright instead, and so does an undeclared
    event, an unknown template and an ambiguous selection.

    Dropping the arguments unconditionally is the correction PR #169's
    review forced, and the case that forced it is worth stating: one
    credential can be BOTH an undeclared field key or value AND a
    perfectly lawful `IDENTIFIER` or `PATHLIKE` argument of the same
    call. Keeping the sentence because the arguments independently
    validated would then drop the value from the payload and render the
    same value into the log and every tap, which is the leak the whole
    machinery exists to refuse. So there is no emission that is
    partly recovered: either it was valid as given, or its sentence is
    recovery's own.
    """
    collisions = tuple(sorted(key for key in fields if key in base))
    payload = _merged(base, fields)
    judged = _judge(_registry, channel, level, message, args, event, payload, collisions)
    if not judged.faults:
        return Checked(payload, level, message, args)
    _report(
        log,
        logging.ERROR,
        REFUSAL_MESSAGE,
        judged.label,
        "; ".join(fault.rendered() for fault in judged.faults),
    )
    if judged.variant is None:
        return _replacement(base)
    rebuilt = {
        key: value
        for key, value in payload.items()
        if key in judged.variant.fields
        and _field_fault(judged.variant.fields[key], value) is None
    }
    if _field_faults(judged.variant, rebuilt):
        return _replacement(base)
    return Checked(rebuilt, level, SAFE_MESSAGE, ())


def _enforce(
    log: logging.Logger,
    channel: str,
    base: dict[str, Any],
    event: str,
    level: int,
    message: str,
    args: tuple[Any, ...],
    fields: dict[str, Any],
) -> Checked:
    """Every emission passes through here on its way to the taps.

    Strict raises and is done. Forgiving runs the whole
    enforcement-and-recovery path under one guard, candidate selection,
    validation and rebuild alike, because a bug anywhere inside it must
    degrade an emission rather than raise on a reply path. The guard
    does not degrade the caller's payload, since the caller's payload is
    precisely what could not be judged: it builds the replacement
    fresh."""
    if _enforcement == STRICT:
        collisions = tuple(sorted(key for key in fields if key in base))
        payload = _merged(base, fields)
        judged = _judge(
            _registry, channel, level, message, args, event, payload, collisions
        )
        if judged.faults:
            raise EventSchemaError(refusal_text(judged.label, judged.faults))
        return Checked(payload, level, message, args)
    try:
        return _recover(log, channel, base, event, level, message, args, fields)
    except Exception as exc:  # noqa: BLE001 - telemetry never costs a reply
        # Through `_report`, because this handler is the last one there
        # is: a logging call that raised here would leave the guard with
        # nothing behind it and cost the reply the guard exists for.
        _report(log, logging.ERROR, GUARD_MESSAGE, type(exc).__name__)
        return _replacement(base)


class SessionEvents:
    """One conversation's observability: its identity, its log channel,
    and the consumers of what it emits.

    Its device identity and its capture attach in stages, because the
    events emitted before each stage have to carry what they carry
    today: the bad-Device-Id rejection names no device because none was
    understood, the no-agent rejection names one because by then the MAC
    is known, and `session_open` is the first line of the decision track
    because the capture opens just before it.

    Observability is orthogonal to the device-facing boundary: both
    sides emit events, and both sides' events have to look the same and
    reach the same places. So this is not a method on `DeviceOutput`; it
    is handed to a runtime at construction, alongside it.
    """

    def __init__(
        self, session_id: str, clock: Callable[[], float] = session_clock
    ) -> None:
        self.session_id = session_id
        # The device's MAC, written by the edge as soon as it is
        # normalized, so a rejection that follows names the device it
        # turned away.
        self.device: str | None = None
        # The agent currently talking. Written by the runtime when it
        # activates one, read by events either side emits: the frame
        # pacer stamps `speaking_started` on the edge but has to name
        # the agent active at fire time, which a tool-only handover
        # before the first audio makes a different one.
        self.agent: str | None = None
        # An explicit dependency rather than an assumption, so what
        # stamps an event is visible at construction and swappable in a
        # test.
        self._clock = clock
        self._taps: list[EventTap] = []
        self._log = LogTap(logger)
        self._capture: SessionRecording | None = None
        self._capture_tap: CaptureTap | None = None

    # --- the consumers ------------------------------------------------

    def attach(self, tap: EventTap) -> None:
        """Add a consumer. Attached rather than passed at construction
        because consumers arrive partway through a session (the capture
        opens during the handshake) and the events before that point are
        still events."""
        self._taps.append(tap)

    def detach(self, tap: EventTap) -> None:
        """Remove a consumer, leaving the events flowing. Detaching one
        that is not attached is not an error: a caller unwinding does
        not have to remember whether it got that far."""
        with contextlib.suppress(ValueError):
            self._taps.remove(tap)

    def attach_capture(self, capture: SessionRecording) -> None:
        """Begin recording the decision track. The capture keeps its own
        pair of methods rather than being attached as a bare tap,
        because `vad` and `dropped` below need the capture itself.

        A second capture replaces the first rather than joining it. One
        session records once, so two attached captures can only mean a
        caller that attached twice; leaving the first adapter in the tap
        list would keep writing to a recording nobody is going to close,
        while `vad` and `dropped` went to the second one, which is a
        recording split down the middle. Replacing rather than refusing,
        because there is a legitimate second attach in reach (a capture
        that rolls over at its size limit) and refusing would make that
        a caller's problem to sequence.
        """
        self.detach_capture()
        self._capture = capture
        self._capture_tap = CaptureTap(capture)
        self.attach(self._capture_tap)

    def detach_capture(self) -> None:
        """Stop recording. Called before the capture is closed, so the
        last line of the track is whatever the session emitted last."""
        if self._capture_tap is not None:
            self.detach(self._capture_tap)
        self._capture_tap = None
        self._capture = None

    def now(self) -> float:
        """This session's clock, read.

        For measuring an interval on the same clock the events are
        stamped with, which is what keeps a duration on the turn record
        comparable with the offsets around it. Deliberately not how a
        record lands on an event's instant: a second reading is a second
        instant, and the emit below answers with the one it stamped for
        exactly that reason."""
        return self._clock()

    # --- what a call site says ----------------------------------------
    #
    # Each answers the reading its event was stamped with, so a record
    # that has to sit at the same instant takes it from the emission
    # rather than sampling the clock again beside it. Almost every call
    # site ignores the answer, which costs nothing; the one that does
    # not is #120's turn record, whose offset has to equal its `heard`
    # event's exactly rather than to within however long the emit took.

    def debug(self, message: str, *args: Any, event: str, **fields: Any) -> float:
        return self._emit(logging.DEBUG, message, args, event, fields)

    def info(self, message: str, *args: Any, event: str, **fields: Any) -> float:
        return self._emit(logging.INFO, message, args, event, fields)

    def warning(self, message: str, *args: Any, event: str, **fields: Any) -> float:
        return self._emit(logging.WARNING, message, args, event, fields)

    def error(self, message: str, *args: Any, event: str, **fields: Any) -> float:
        return self._emit(logging.ERROR, message, args, event, fields)

    def _emit(
        self,
        level: int,
        message: str,
        args: tuple[Any, ...],
        event: str,
        fields: dict[str, Any],
    ) -> float:
        """The one path every conversation event takes: what every one
        of them carries, then this event's own fields, then every
        consumer in turn with the log last. Answers the reading the
        emission was stamped with.

        The base fields are built here and merged here, never taken from
        the caller: `_enforce` is handed them separately, so a spread
        carrying `session=` is judged as the identity spoofing it is
        rather than merged over the emitter's own."""
        checked = _enforce(
            logger,
            SESSION_LOGGER,
            {"event": event, "session": self.session_id, "device": self.device},
            event,
            level,
            message,
            args,
            fields,
        )
        emission = Emission(
            payload=checked.payload,
            at=self._clock(),
            level=checked.level,
            message=checked.message,
            args=checked.args,
        )
        _dispatch(tuple(self._taps), self._log, emission, logger)
        return emission.at

    # --- the capture's own tracks, which are not events ---------------

    def vad(self, speech_ms: float, listening: bool, replying: bool) -> None:
        """One sample of what the endpointer currently believes, for the
        capture's VAD track. Fed by the runtime, which is the side that
        owns the endpointer.

        Outside the tap contract deliberately: this is sampled once per
        mic frame, it is a track of the recording rather than a decision
        the server made, and a consumer of events has no meaning for
        it."""
        if self._capture is None:
            return
        self._capture.vad(speech_ms, listening, replying, self._clock())

    def dropped(self, reason: str) -> None:
        """One mic frame the session did not use, and why. Part of the
        evidence the capture exists for: the frames dropped before the
        decode are precisely the ones that explain a misfire. Outside
        the tap contract for the reason `vad` gives."""
        if self._capture is None:
            return
        self._capture.dropped(reason, self._clock())


class ServerEvents:
    """The events a subsystem emits about itself, outside any
    conversation: OTA check-ins, onboarding, the MCP lifecycle, the
    provider registry, the API.

    No session and no device defaults: a server-scoped event names what
    it is about explicitly (`device=`, `entry=`, `host=`, `path=`),
    which is what every hand-built site already did. One emitter per
    subsystem, built on that subsystem's existing module logger name, so
    the `logger` field of every record it emits is the one it always
    was.
    """

    def __init__(
        self, channel: str, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.channel = channel
        self._logger = logging.getLogger(channel)
        self._log = LogTap(self._logger)
        # `time.monotonic` rather than a loop clock: server events fire
        # before any loop runs, and `asyncio.get_running_loop()` would
        # raise there.
        self._clock = clock
        _hub.register(self)

    def debug(self, message: str, *args: Any, event: str, **fields: Any) -> None:
        self._emit(logging.DEBUG, message, args, event, fields)

    def info(self, message: str, *args: Any, event: str, **fields: Any) -> None:
        self._emit(logging.INFO, message, args, event, fields)

    def warning(self, message: str, *args: Any, event: str, **fields: Any) -> None:
        self._emit(logging.WARNING, message, args, event, fields)

    def error(self, message: str, *args: Any, event: str, **fields: Any) -> None:
        self._emit(logging.ERROR, message, args, event, fields)

    def _emit(
        self,
        level: int,
        message: str,
        args: tuple[Any, ...],
        event: str,
        fields: dict[str, Any],
    ) -> None:
        # A server emitter has no identity to protect, so `session` and
        # `device` are ordinary declarable fields here; `event` is the
        # whole of its base.
        checked = _enforce(
            self._logger,
            self.channel,
            {"event": event},
            event,
            level,
            message,
            args,
            fields,
        )
        emission = Emission(
            payload=checked.payload,
            at=self._clock(),
            level=checked.level,
            message=checked.message,
            args=checked.args,
        )
        _dispatch(tuple(_hub.taps), self._log, emission, self._logger)


class _ServerHub:
    """Where a consumer of server-scoped events attaches, once, for all
    of them.

    Every subsystem has an emitter of its own, on its own channel, so a
    consumer would otherwise have to discover and mutate each module's
    private one. The hub holds the tap set and the emitters read it at
    emit time, which is what makes attachment order irrelevant: an
    emitter built after a consumer attached is served by the same set.
    """

    def __init__(self) -> None:
        self.taps: list[EventTap] = []
        # What exists to emit, for a consumer that wants to know. Kept
        # deliberately, even though dispatch does not need it: "which
        # channels does this server speak on" is otherwise a question
        # only the import graph can answer.
        self.emitters: list[ServerEvents] = []

    def register(self, emitter: ServerEvents) -> None:
        self.emitters.append(emitter)


_hub = _ServerHub()


def attach_server_tap(tap: EventTap) -> None:
    """Consume every server-scoped event, from every subsystem, whether
    its emitter already exists or is built later."""
    _hub.taps.append(tap)


def detach_server_tap(tap: EventTap) -> None:
    """Stop consuming them. Detaching one that is not attached is not an
    error, for the reason `SessionEvents.detach` gives."""
    with contextlib.suppress(ValueError):
        _hub.taps.remove(tap)


def server_emitters() -> tuple[ServerEvents, ...]:
    """Every server emitter built so far, in construction order."""
    return tuple(_hub.emitters)
