"""Every event this server may emit, declared: name, channel, level,
sentence, arguments and fields.

The retained JSON records are the observability surface
([ADR](../../docs/adr/2026-08-04-json-logs-are-the-observability-surface.md)),
and they carry metadata and nothing else ([the content and telemetry
ADR](../../docs/adr/2026-08-15-content-and-telemetry-are-separate-surfaces.md)).
Until #155 that second rule was a convention held by review vigilance:
nineteen of the roughly thirty findings across the 2026-08-14 refactoring
batch's review rounds were leak-shaped content on the retained log, each
found and fixed by hand. This module is what turns the convention into
data. One declaration per event says exactly what it is allowed to be,
so a field carrying far-side bytes becomes a schema violation a test
lane refuses rather than a finding somebody has to notice.

Three properties of the declarations are load-bearing:

- **There is no free-text kind.** Every string field is a trusted
  configured identifier (trusted by provenance: an operator wrote it,
  and its domain here is the configuration's own rather than a tighter
  one this module invented), a token from a closed set, a class name, a
  bounded machine id with a syntax, or a `DESCRIPTOR`: a far-side
  string retained deliberately, lawful only where its decision site
  bounds and sanitizes it, and declared with the maximum length and
  character constraint that site guarantees. `DESCRIPTOR` exists
  because the content-and-telemetry ADR's 2026-08-17 amendment says
  bounded device-descriptor metadata is metadata: what a device says
  ABOUT ITSELF at check-in may ride the events once bounded, while what
  a person said through it may not. A registry that could not say
  "this field deliberately carries sanitized far-side bytes" would
  launder those fields as identifiers instead of naming them.
- **The sentence is part of the declaration.** `Emission.args` reaches
  every tap and the formatter renders the template, so a payload rule
  that ignored the message would leave the other half of the record
  unguarded. Each variant therefore declares the exact template string
  and the kind of every argument position.
- **A variant is one whole emission shape.** One flat field table
  cannot describe this surface: `session_rejected` is emitted with
  three arities across four templates on two channels, `ota_check` with
  three, `mcp_reload`'s applied and refused answers carry mutually
  exclusive fields, and several events change level with shape. A
  variant is exactly what one emit site, or one branch of one,
  produces. Conditional presence is expressed by variants wherever the
  condition follows the site, and by `required=False` only where it is
  value-dependent inside a single site (`language` on `heard` when the
  engine detected, the token counts on `llm_round` when the provider
  reported them).

The module imports the standard library and nothing else, which is what
keeps the arrows pointing downward: every subsystem imports `events`,
`events` imports this, and neither imports a subsystem.

What lives here is the declaration only. Enforcement at emit time is
M2's, and the generated `docs/reference/events.md` is M3's; both read
this module rather than restating it. The tap contract is events only,
so `SessionEvents.vad()` and `.dropped()`, which are capture side
channels sampled per frame, are outside the registry deliberately, the
way they are outside the tap contract.
"""

import logging
import re
from dataclasses import dataclass
from enum import Enum
from functools import cache

# --- the channels -----------------------------------------------------
#
# The channel is the scope: one session channel, named rather than
# derived from a file so that splitting the code across packages cannot
# rename it, and one channel per server subsystem, each built on that
# subsystem's own module name.

SESSION_CHANNEL = "vinga_server.session"

SERVER_CHANNELS = (
    "vinga_server.app",
    "vinga_server.capture",
    "vinga_server.config.api",
    "vinga_server.conversations.store",
    "vinga_server.device.bindings",
    "vinga_server.filler",
    "vinga_server.onboarding",
    "vinga_server.ota",
    "vinga_server.providers.openai_asr",
    "vinga_server.registry",
    "vinga_server.tools.mcp",
    "vinga_server.tools.memory",
    "vinga_server.ws",
)

CHANNELS = (SESSION_CHANNEL, *SERVER_CHANNELS)


# --- what a value may be ----------------------------------------------


class Kind(Enum):
    """The shapes a payload field may take. A field that wants prose is
    a design error this enum refuses to encode."""

    # A trusted name the operator or this server chose: an agent, a
    # configuration entry, a pipeline stage, a path, an origin. Its
    # domain is the configuration's own (`IDENTIFIER_DOMAIN`): non-empty
    # once stripped, and nothing further, because nothing further is
    # what the operator was promised. Trusted is about provenance, not
    # about shape.
    IDENTIFIER = "identifier"
    # One value out of the field's declared closed set.
    TOKEN = "token"
    # An exception or type name. `joined` admits the ", "-separated form
    # a group of them renders as.
    CLASS_NAME = "class_name"
    # A bounded machine form this server minted or normalized, with a
    # per-field syntax rather than a generic "bounded string".
    ID = "id"
    # A far-side string retained deliberately, bounded and sanitized at
    # its decision site and bounded again here.
    DESCRIPTOR = "descriptor"
    INT = "int"
    FLOAT = "float"
    BOOL = "bool"
    # An `int >= 0` whose meaning is "how many".
    COUNT = "count"
    IDENTIFIER_LIST = "identifier_list"
    ID_LIST = "id_list"
    # The one structured kind: a mapping from prompt provenance to
    # character counts.
    SOURCES = "sources"


class ArgKind(Enum):
    """The shapes a `%` argument may take.

    Beside the field kinds rather than instead of them, because the
    rendered sentence carries shapes no field does: a configured path
    object, and formatted fragments of identifiers whose grammar the
    declaration names. Widening `IDENTIFIER` to cover a punctuated
    fragment would have made the tightest kind the loosest one.
    """

    IDENTIFIER = "identifier"
    TOKEN = "token"
    CLASS_NAME = "class_name"
    ID = "id"
    # Reuses the corresponding field's bounds and character constraint:
    # a lawful descriptor necessarily reaches the argument positions
    # that render it.
    DESCRIPTOR = "descriptor"
    INT = "int"
    FLOAT = "float"
    BOOL = "bool"
    COUNT = "count"
    # A trusted configured path, `Path` or `str`.
    PATHLIKE = "pathlike"
    # A formatted fragment of identifiers, validated against the named
    # grammar rather than against a string type.
    COMPOSED = "composed"


@dataclass(frozen=True)
class Syntax:
    """The form one `ID` field's values take, named so a generated
    reference can print it and a validator can hold values to it."""

    name: str
    pattern: str
    max_length: int
    note: str = ""


@dataclass(frozen=True)
class Bounds:
    """What a `DESCRIPTOR` field's decision site guarantees, restated
    here so the emitter enforces it a second time.

    `charset` is a rule rather than a set: `printable` means every
    character satisfies `str.isprintable()`, which is false for every
    control character, for the separators, and for the non-ASCII spaces.
    That is exactly the set that has to go: a newline would split one
    retained record into two, and a terminal escape would let whoever
    sent it paint an operator's screen.
    """

    max_length: int
    charset: str = "printable"


@dataclass(frozen=True)
class Grammar:
    """One `COMPOSED` argument's shape, with the code that builds it.

    Naming the builder is what keeps the grammar honest: a fragment
    nobody assembles is a pattern somebody guessed.
    """

    name: str
    pattern: str
    builders: tuple[str, ...]
    note: str = ""


# --- the syntaxes ------------------------------------------------------

MAC = Syntax(
    "mac",
    r"[0-9a-f]{2}(?::[0-9a-f]{2}){5}",
    17,
    "The canonical form `normalize_mac` answers with.",
)

REPORTED_MAC = Syntax(
    "reported_mac",
    r"[0-9A-Fa-f]{2}(?:[:-][0-9A-Fa-f]{2}){5}",
    17,
    "The Device-Id header as the firmware sent it, which the OTA "
    "sentence renders beside the normalized form the field carries. "
    "Only a header `normalize_mac` accepted ever reaches that sentence, "
    "so the looser separator and case are the whole of the difference.",
)

SESSION_ID = Syntax(
    "session_id",
    r"[0-9A-Za-z_-]{1,64}",
    64,
    "A token this server minted. Production ids are `uuid4().hex`; the "
    "syntax is the bounded machine form rather than that one spelling, "
    "because the capture and store suites drive sessions of their own "
    "naming and a session id is never far-side bytes whoever chose it.",
)

ACTIVATION_CODE = Syntax(
    "activation_code",
    r"[0-9]{6}",
    6,
    "A claim ticket read off a screen, not a credential.",
)

EVENT_NAME = Syntax(
    "event_name",
    r"[a-z][a-z0-9_]{0,63}",
    64,
    "The registry's own key, carried in the payload as `event`.",
)

LANGUAGE = Syntax(
    "language",
    r"[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{1,8})*",
    16,
    "A language code as an ASR engine reports it: the bare ISO 639 code "
    "or a tagged form such as `en-US`.",
)

SYNTAXES: dict[str, Syntax] = {
    one.name: one for one in (MAC, REPORTED_MAC, SESSION_ID, ACTIVATION_CODE, EVENT_NAME, LANGUAGE)
}


# --- the descriptor bounds --------------------------------------------
#
# Restated from the decision sites (`config/models.py`'s `BOARD_LIMIT`,
# `FIRMWARE_LIMIT` and `CLIENT_ID_LIMIT`) rather than imported from
# them, because this module imports the standard library and nothing
# else. The conformance test holds the two statements equal, so the
# restatement cannot drift.

BOARD_BOUNDS = Bounds(64)
FIRMWARE_BOUNDS = Bounds(32)
CLIENT_BOUNDS = Bounds(64)

# What a trusted configured name may be, which is what the
# configuration says and no more.
#
# `NonBlankStr`, the type behind an agent name, a provider entry name
# and a provider type, is `StringConstraints(strip_whitespace=True,
# min_length=1)`: any non-empty string once stripped. It admits quotes,
# control characters and any length at all. An agent called
# `secondary"agent` is lawful configuration today, so a registry
# claiming a tighter domain would turn that deployment's every
# `session_open` into a schema violation the moment M2 enforces, and
# forgiving mode would then drop the field and replace the sentence:
# lawful traffic mangled by a claim the configuration never made.
#
# So the identifier kinds and the grammars below describe what
# configuration guarantees. Narrowing belongs at configuration
# semantics, where a refusal reaches the operator who can fix it, not
# here, where it reaches a log line nobody asked for. The follow-up
# issue this milestone files proposes exactly that, and these patterns
# tighten from that side when it lands.
IDENTIFIER_DOMAIN = "a non-empty string once stripped, as NonBlankStr defines it"


# --- the composed grammars --------------------------------------------
#
# Bounded by STRUCTURE rather than by character class or length: what a
# fragment promises is its shape (a parenthesized tail, a quoted name, a
# comma-joined list), never what an operator may have called something.

# One configured name inside a fragment. Any non-empty run of
# characters, newlines included, because that is the domain above.
_NAME = r"[\s\S]+"

EMPTY_FRAGMENT = Grammar(
    "empty_fragment",
    r"",
    ("vinga_server.events.values:Nothing",),
    "The nothing a site renders where it has nothing to add. Declared "
    "rather than left untyped, so a variant that may only say nothing "
    "says exactly that.",
)

ALSO_BOUND_TO = Grammar(
    "also_bound_to",
    rf"(?: \(also bound to {_NAME}\))?",
    (
        "vinga_server.ota.reply:check_version",
        "vinga_server.events.values:AlsoBoundTo.of",
    ),
    "The tail naming the agents a device is bound to beside the one "
    "that answered, empty for a device bound to exactly one. The names "
    "inside it are comma joined, and the grammar does not say so: a "
    "configured name may itself hold a comma, so the joined fragment "
    "cannot be parsed back into the names that made it, and a pattern "
    "claiming otherwise would refuse a lawful deployment.",
)

AGENT_LIST = Grammar(
    "agent_list",
    _NAME,
    (
        "vinga_server.ota.reply:check_version",
        "vinga_server.events.values:AgentList.of",
    ),
    "The configured agent names a device is bound to, comma-joined. "
    "Non-empty, and nothing further: see the tail grammar above for why "
    "the joining is not part of the claim.",
)

SESSION_LIST = Grammar(
    "session_list",
    r"[0-9A-Za-z_-]{1,64}(?:, [0-9A-Za-z_-]{1,64})*",
    ("vinga_server.capture:CaptureStore.prune",),
    "The session ids a prune removed, comma-joined.",
)

QUOTED_TOOL_NAME = Grammar(
    "quoted_tool_name",
    r' "[\s\S]+"',
    ("vinga_server.events.values:QuotedToolName.of",),
    "A builtin's name, which is this server's own word, bounded here by "
    "the quoting alone. A device tool's "
    "name is the board's vocabulary and an unknown one is whatever the "
    "model invented, so neither is ever rendered here.",
)

FROM_ENTRY = Grammar(
    "from_entry",
    r' from entry "[\s\S]+"',
    ("vinga_server.events.values:FromEntry.of",),
    "The configured MCP entry a call reached, never the far side's own "
    "tool name. Entry names are separately held to `[A-Za-z0-9_-]+` by "
    "the configuration, which makes this grammar a floor rather than "
    "the whole truth; the floor is what the registry may claim, since "
    "the tighter rule is configuration's to keep and to change.",
)

QUOTED_PROVIDER = Grammar(
    "quoted_provider",
    r' "[\s\S]+"',
    ("vinga_server.events.values:QuotedProvider.of",),
    "The configuration entry the failing provider is, bounded by the "
    "quoting alone.",
)

REACHING_HOST = Grammar(
    "reaching_host",
    r"(?: reaching [\s\S]+)?",
    ("vinga_server.events.values:ReachingHost.of",),
    "Where the call was going, empty for an engine that runs in this "
    "process.",
)

ORIGIN_PROVENANCE = Grammar(
    "origin_provenance",
    r"(?:from|guessed from) [\s\S]+",
    ("vinga_server.onboarding.origin:Origin.provenance",),
    "Which configuration key the banner's origin came out of, and "
    "whether it was read or inferred.",
)

DEVICE_OR_UNIDENTIFIED = Grammar(
    "device_or_unidentified",
    r"[0-9a-f]{2}(?::[0-9a-f]{2}){5}|an unidentified device",
    ("vinga_server.events.values:DeviceOrUnidentified.of",),
    "The MAC behind a Device-Id header this server recognizes, or the "
    "fixed phrase. Nothing else: with device auth off nothing has "
    "verified that header, so an unrecognized one names no device at "
    "all.",
)

GRAMMARS: dict[str, Grammar] = {
    one.name: one
    for one in (
        EMPTY_FRAGMENT,
        ALSO_BOUND_TO,
        AGENT_LIST,
        SESSION_LIST,
        QUOTED_TOOL_NAME,
        FROM_ENTRY,
        QUOTED_PROVIDER,
        REACHING_HOST,
        ORIGIN_PROVENANCE,
        DEVICE_OR_UNIDENTIFIED,
    )
}


@cache
def matcher(pattern: str) -> re.Pattern[str]:
    """One anchored matcher per pattern, compiled once.

    Anchored here rather than in every declaration, so a pattern cannot
    be written unanchored by accident and admit a prefix.
    """
    return re.compile(rf"\A(?:{pattern})\Z")


# --- the declarations' building blocks --------------------------------


@dataclass(frozen=True)
class EventField:
    """One payload field's declared shape."""

    kind: Kind
    required: bool = True
    nullable: bool = False
    tokens: frozenset[str] | None = None
    syntax: Syntax | None = None
    bounds: Bounds | None = None
    # CLASS_NAME only: whether the field may carry the ", "-joined form
    # a group of exceptions renders as.
    joined: bool = False
    # Prose the generated reference renders beside the field. The
    # registry owns it, so it is checked by the drift step the way every
    # other declared property is, unlike the README's prose was.
    note: str = ""


@dataclass(frozen=True)
class ArgSpec:
    """One `%` argument position's declared shape."""

    kind: ArgKind
    nullable: bool = False
    tokens: frozenset[str] | None = None
    syntax: Syntax | None = None
    bounds: Bounds | None = None
    grammar: Grammar | None = None
    joined: bool = False
    note: str = ""


@dataclass(frozen=True)
class EventVariant:
    """One legal emission, whole: where it rides, how loud it is, the
    sentence it renders, the arguments that sentence takes, and the
    payload it carries."""

    channel: str
    level: int
    message: str
    # Required, and before `args` so that it is: a variant with no field
    # table is not a shape, and a default of None would trade a
    # construction error for an attribute error somewhere later.
    fields: dict[str, EventField]
    args: tuple[ArgSpec, ...] = ()
    note: str = ""


@dataclass(frozen=True)
class EventSpec:
    """One event name and every shape it may be emitted in."""

    name: str
    variants: tuple[EventVariant, ...]
    note: str = ""
    # True for the one event no ordinary emit site produces: the
    # forgiving recovery's own. The conformance walk exempts it by name
    # the way the `extra=` guard exempts `events.py`.
    internal: bool = False

    @property
    def channels(self) -> frozenset[str]:
        return frozenset(variant.channel for variant in self.variants)

    @property
    def levels(self) -> frozenset[int]:
        return frozenset(variant.level for variant in self.variants)


# --- shorthand, so a declaration reads as one line --------------------


def identifier(**kw: object) -> EventField:
    return EventField(Kind.IDENTIFIER, **kw)  # type: ignore[arg-type]


def token(values: object, **kw: object) -> EventField:
    return EventField(Kind.TOKEN, tokens=frozenset(values), **kw)  # type: ignore[arg-type,call-overload]


def class_name(**kw: object) -> EventField:
    return EventField(Kind.CLASS_NAME, **kw)  # type: ignore[arg-type]


def machine_id(syntax: Syntax, **kw: object) -> EventField:
    return EventField(Kind.ID, syntax=syntax, **kw)  # type: ignore[arg-type]


def descriptor(bounds: Bounds, **kw: object) -> EventField:
    return EventField(Kind.DESCRIPTOR, bounds=bounds, **kw)  # type: ignore[arg-type]


def whole(**kw: object) -> EventField:
    return EventField(Kind.INT, **kw)  # type: ignore[arg-type]


def real(**kw: object) -> EventField:
    return EventField(Kind.FLOAT, **kw)  # type: ignore[arg-type]


def flag(**kw: object) -> EventField:
    return EventField(Kind.BOOL, **kw)  # type: ignore[arg-type]


def count(**kw: object) -> EventField:
    return EventField(Kind.COUNT, **kw)  # type: ignore[arg-type]


def identifier_list(**kw: object) -> EventField:
    return EventField(Kind.IDENTIFIER_LIST, **kw)  # type: ignore[arg-type]


def id_list(syntax: Syntax, **kw: object) -> EventField:
    return EventField(Kind.ID_LIST, syntax=syntax, **kw)  # type: ignore[arg-type]


def sources(**kw: object) -> EventField:
    return EventField(Kind.SOURCES, **kw)  # type: ignore[arg-type]


def arg_identifier(**kw: object) -> ArgSpec:
    return ArgSpec(ArgKind.IDENTIFIER, **kw)  # type: ignore[arg-type]


def arg_token(values: object, **kw: object) -> ArgSpec:
    return ArgSpec(ArgKind.TOKEN, tokens=frozenset(values), **kw)  # type: ignore[arg-type,call-overload]


def arg_class_name(**kw: object) -> ArgSpec:
    return ArgSpec(ArgKind.CLASS_NAME, **kw)  # type: ignore[arg-type]


def arg_id(syntax: Syntax, **kw: object) -> ArgSpec:
    return ArgSpec(ArgKind.ID, syntax=syntax, **kw)  # type: ignore[arg-type]


def arg_descriptor(bounds: Bounds, **kw: object) -> ArgSpec:
    return ArgSpec(ArgKind.DESCRIPTOR, bounds=bounds, **kw)  # type: ignore[arg-type]


def arg_whole(**kw: object) -> ArgSpec:
    return ArgSpec(ArgKind.INT, **kw)  # type: ignore[arg-type]


def arg_real(**kw: object) -> ArgSpec:
    return ArgSpec(ArgKind.FLOAT, **kw)  # type: ignore[arg-type]


def arg_count(**kw: object) -> ArgSpec:
    return ArgSpec(ArgKind.COUNT, **kw)  # type: ignore[arg-type]


def arg_path(**kw: object) -> ArgSpec:
    return ArgSpec(ArgKind.PATHLIKE, **kw)  # type: ignore[arg-type]


def arg_composed(grammar: Grammar, **kw: object) -> ArgSpec:
    return ArgSpec(ArgKind.COMPOSED, grammar=grammar, **kw)  # type: ignore[arg-type]


# --- the base fields --------------------------------------------------
#
# Every variant's `fields` covers the whole payload a tap receives, base
# fields included, so one validation reads the finished payload and the
# difference between the scopes falls out of the declarations rather
# than out of a special case.

SESSION_BASE: dict[str, EventField] = {
    "event": machine_id(EVENT_NAME),
    "session": machine_id(SESSION_ID),
    # Null until the edge has normalized the MAC, which is why the
    # bad-Device-Id rejection names no device.
    "device": machine_id(MAC, nullable=True),
}

SERVER_BASE: dict[str, EventField] = {"event": machine_id(EVENT_NAME)}


def session_payload(**own: EventField) -> dict[str, EventField]:
    return {**SESSION_BASE, **own}


def server_payload(**own: EventField) -> dict[str, EventField]:
    return {**SERVER_BASE, **own}


# --- the token sets, spelled where they are read ----------------------

MCP_CONNECT_REASONS = (
    "transport_failed",
    "initialize_failed",
    "discovery_failed",
    "connect_timeout",
)
MCP_REFUSAL_REASONS = ("in_progress", "database_busy", "unreadable", "invalid", "unexpected")
ORIGIN_SOURCES = (
    "server.public_url",
    "server.websocket_url",
    "the listen address (server.host and server.port)",
)

# The two bounds the pending table refuses a code at, in the words their
# warnings use. Sentences rather than short tokens, and still a closed
# set of exactly two values this server minted: what makes a `TOKEN` a
# token is that the set is closed, not that its members are short.
PENDING_REFUSALS = (
    "128 devices are already waiting to be claimed, which is the cap",
    "30 activation codes have been issued in the last 10 minutes, which is the limit",
)

# What `_bad_request` says, and the whole of what it may say: every
# caller passes a fixed sentence, which is what keeps a header this
# endpoint could not read out of the log a deployment ships.
OTA_REFUSALS = (
    "the Device-Id header is required and holds the device MAC",
    "the Client-Id header is required and holds the device UUID",
    "the Device-Id header does not hold a MAC address; it has to be six "
    "colon-separated hex pairs, for example aa:bb:cc:dd:ee:ff. What was sent is not "
    "quoted back, since a header that missed the MAC may hold anything at all",
)


# --- the provenance grammar of `prompt_assembled.sources` -------------
#
# The know-how half only. `prompt_assembled` deliberately reports the
# cached half of the prompt and excludes the per-round memory read, so
# `memory` is a violation here like any unknown prefix, even though it is
# a provenance token elsewhere in the prompt assembly.

SOURCE_FORMS = (
    "persona",
    "fragment:<name>",
    "instructions:<entry>",
    "server_instructions:<entry>",
    "server_prompt:<entry>:<position>",
)

_CONFIGURED_NAME = r"[A-Za-z0-9_-]+"

SOURCE_KEY_PATTERN = (
    rf"persona"
    rf"|fragment:{_CONFIGURED_NAME}"
    rf"|instructions:{_CONFIGURED_NAME}"
    rf"|server_instructions:{_CONFIGURED_NAME}"
    rf"|server_prompt:{_CONFIGURED_NAME}:[1-9][0-9]*"
)


# --- the declarations -------------------------------------------------
#
# Grouped by the subsystem that emits them, in the order a request meets
# them: the device's check-in, its session, the pipeline inside it, the
# providers behind that, then the server's own lifecycle surfaces.


_SPECS: list[EventSpec] = [
]


# --- the one event no emit site produces ------------------------------
#
# The forgiving mode's recovery event (M2). An emission that matches no
# declared variant becomes this one: the fixed token, the emitter's own
# trusted identity, a fixed sentence and no arguments, so a hostile
# name, key, value, message or argument in the original call reaches
# nothing. It is declared here like any other event, because a tap fed a
# shape the generated reference denies exists would make the reference a
# liar; it has no ordinary emit site, so the conformance walk exempts it
# by name the way the `extra=` guard exempts `events.py`.

SCHEMA_VIOLATION = "schema_violation"

SCHEMA_VIOLATION_MESSAGE = (
    "an event was refused by the event schema and replaced by this one; "
    "reproduce it under VINGA_EVENTS_ENFORCEMENT=strict to see which"
)

_SPECS.append(
    EventSpec(
        SCHEMA_VIOLATION,
        internal=True,
        note=(
            "What the emitter emits in forgiving mode when an "
            "emission cannot be recovered into a declared shape. Fixed at "
            "ERROR, because `log_level` admits roots above WARNING and a "
            "complaint that vanishes under one is no complaint."
        ),
        variants=tuple(
            EventVariant(
                channel=channel,
                level=logging.ERROR,
                message=SCHEMA_VIOLATION_MESSAGE,
                args=(),
                fields=(
                    session_payload() if channel == SESSION_CHANNEL else server_payload()
                ),
            )
            for channel in CHANNELS
        ),
    )
)


REGISTRY: dict[str, EventSpec] = {spec.name: spec for spec in _SPECS}

# The production surface: everything an ordinary emit site may produce.
PRODUCTION_EVENTS: frozenset[str] = frozenset(
    name for name, spec in REGISTRY.items() if not spec.internal
)

INTERNAL_EVENTS: frozenset[str] = frozenset(
    name for name, spec in REGISTRY.items() if spec.internal
)
