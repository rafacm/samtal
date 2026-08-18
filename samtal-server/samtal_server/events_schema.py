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

SESSION_CHANNEL = "samtal_server.session"

SERVER_CHANNELS = (
    "samtal_server.app",
    "samtal_server.capture",
    "samtal_server.config.api",
    "samtal_server.conversations.store",
    "samtal_server.device.bindings",
    "samtal_server.filler",
    "samtal_server.onboarding",
    "samtal_server.ota",
    "samtal_server.providers.openai_asr",
    "samtal_server.registry",
    "samtal_server.tools.mcp",
    "samtal_server.tools.memory",
    "samtal_server.ws",
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
    (
        "samtal_server.runtime.pipeline:_tool_named",
        "samtal_server.runtime.pipeline:PipelineRuntime._provider_failed",
    ),
    "The nothing a site renders where it has nothing to add. Declared "
    "rather than left untyped, so a variant that may only say nothing "
    "says exactly that.",
)

ALSO_BOUND_TO = Grammar(
    "also_bound_to",
    rf"(?: \(also bound to {_NAME}\))?",
    (
        "samtal_server.ota:check_version",
        "samtal_server.device.session:DeviceSession.run",
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
        "samtal_server.ota:check_version",
        "samtal_server.device.session:DeviceSession.run",
    ),
    "The configured agent names a device is bound to, comma-joined. "
    "Non-empty, and nothing further: see the tail grammar above for why "
    "the joining is not part of the claim.",
)

SESSION_LIST = Grammar(
    "session_list",
    r"[0-9A-Za-z_-]{1,64}(?:, [0-9A-Za-z_-]{1,64})*",
    ("samtal_server.capture:CaptureStore.prune",),
    "The session ids a prune removed, comma-joined.",
)

QUOTED_TOOL_NAME = Grammar(
    "quoted_tool_name",
    r' "[\s\S]+"',
    ("samtal_server.runtime.pipeline:_tool_named",),
    "A builtin's name, which is this server's own word, bounded here by "
    "the quoting alone. A device tool's "
    "name is the board's vocabulary and an unknown one is whatever the "
    "model invented, so neither is ever rendered here.",
)

FROM_ENTRY = Grammar(
    "from_entry",
    r' from entry "[\s\S]+"',
    ("samtal_server.runtime.pipeline:_tool_named",),
    "The configured MCP entry a call reached, never the far side's own "
    "tool name. Entry names are separately held to `[A-Za-z0-9_-]+` by "
    "the configuration, which makes this grammar a floor rather than "
    "the whole truth; the floor is what the registry may claim, since "
    "the tighter rule is configuration's to keep and to change.",
)

QUOTED_PROVIDER = Grammar(
    "quoted_provider",
    r' "[\s\S]+"',
    ("samtal_server.runtime.pipeline:PipelineRuntime._provider_failed",),
    "The configuration entry the failing provider is, bounded by the "
    "quoting alone.",
)

REACHING_HOST = Grammar(
    "reaching_host",
    r"(?: reaching [\s\S]+)?",
    ("samtal_server.runtime.pipeline:PipelineRuntime._provider_failed",),
    "Where the call was going, empty for an engine that runs in this "
    "process.",
)

ORIGIN_PROVENANCE = Grammar(
    "origin_provenance",
    r"(?:from|guessed from) [\s\S]+",
    ("samtal_server.onboarding.origin:Origin.provenance",),
    "Which configuration key the banner's origin came out of, and "
    "whether it was read or inferred.",
)

DEVICE_OR_UNIDENTIFIED = Grammar(
    "device_or_unidentified",
    r"[0-9a-f]{2}(?::[0-9a-f]{2}){5}|an unidentified device",
    ("samtal_server.ws:conversation",),
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

CLOSE_REASONS = ("limit", "idle", "drain", "client", "error")
MCP_CONNECT_REASONS = (
    "transport_failed",
    "initialize_failed",
    "discovery_failed",
    "connect_timeout",
)
MCP_REFUSAL_REASONS = ("in_progress", "database_busy", "unreadable", "invalid", "unexpected")
TOOL_SOURCES = ("builtin", "device", "mcp", "unknown")
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
    # --- ota.py: the configuration check and the activation ceremony --
    EventSpec(
        "ota_check",
        note=(
            "What a device said about itself at its configuration check, "
            "and what this server resolved it to. No session exists yet, "
            "so the record names the device instead."
        ),
        variants=(
            EventVariant(
                channel="samtal_server.ota",
                level=logging.WARNING,
                message=(
                    "device %s (%s, firmware %s) has no agent and is showing activation "
                    "code %s; bind it with: samtal-server config add-device %s <agent>"
                ),
                args=(
                    arg_id(REPORTED_MAC),
                    arg_descriptor(BOARD_BOUNDS),
                    arg_descriptor(FIRMWARE_BOUNDS),
                    arg_id(ACTIVATION_CODE),
                    arg_id(ACTIVATION_CODE),
                ),
                fields=server_payload(
                    device=machine_id(MAC),
                    client=descriptor(
                        CLIENT_BOUNDS,
                        nullable=True,
                        note=(
                            "The device UUID, bounded for the event only: the token "
                            "the reply issues is still signed for the header exactly "
                            "as it arrived."
                        ),
                    ),
                    board=descriptor(
                        BOARD_BOUNDS,
                        note="What the device calls itself. `unknown` when it said nothing usable.",
                    ),
                    firmware=descriptor(
                        FIRMWARE_BOUNDS,
                        note=(
                            "The only moment a device ever states its firmware version: "
                            "the websocket handshake does not carry it."
                        ),
                    ),
                    agents=identifier_list(),
                    unloaded=identifier_list(
                        note=(
                            "Agents this device is bound to that this process did not "
                            "load. Named on every record rather than only on the one "
                            "that complains, so a query for devices waiting on a "
                            "restart is one field."
                        )
                    ),
                    code=machine_id(ACTIVATION_CODE),
                ),
            ),
            EventVariant(
                channel="samtal_server.ota",
                level=logging.WARNING,
                message=(
                    "device %s (%s, firmware %s) is bound to agent %s, which this server "
                    "has not loaded; restart to load it"
                ),
                args=(
                    arg_id(REPORTED_MAC),
                    arg_descriptor(BOARD_BOUNDS),
                    arg_descriptor(FIRMWARE_BOUNDS),
                    arg_composed(AGENT_LIST),
                ),
                fields=server_payload(
                    device=machine_id(MAC),
                    client=descriptor(CLIENT_BOUNDS, nullable=True),
                    board=descriptor(BOARD_BOUNDS),
                    firmware=descriptor(FIRMWARE_BOUNDS),
                    agents=identifier_list(),
                    unloaded=identifier_list(),
                ),
            ),
            EventVariant(
                channel="samtal_server.ota",
                level=logging.WARNING,
                message=(
                    "device %s (%s, firmware %s) has no agent: bind it under devices "
                    "or set default_agent"
                ),
                args=(
                    arg_id(REPORTED_MAC),
                    arg_descriptor(BOARD_BOUNDS),
                    arg_descriptor(FIRMWARE_BOUNDS),
                ),
                fields=server_payload(
                    device=machine_id(MAC),
                    client=descriptor(CLIENT_BOUNDS, nullable=True),
                    board=descriptor(BOARD_BOUNDS),
                    firmware=descriptor(FIRMWARE_BOUNDS),
                    agents=identifier_list(),
                    unloaded=identifier_list(),
                ),
            ),
            EventVariant(
                channel="samtal_server.ota",
                level=logging.INFO,
                message="device %s (%s, firmware %s) resolved to agent %s%s",
                args=(
                    arg_id(REPORTED_MAC),
                    arg_descriptor(BOARD_BOUNDS),
                    arg_descriptor(FIRMWARE_BOUNDS),
                    arg_identifier(),
                    arg_composed(ALSO_BOUND_TO),
                ),
                fields=server_payload(
                    device=machine_id(MAC),
                    client=descriptor(CLIENT_BOUNDS, nullable=True),
                    board=descriptor(BOARD_BOUNDS),
                    firmware=descriptor(FIRMWARE_BOUNDS),
                    agents=identifier_list(),
                    unloaded=identifier_list(),
                ),
            ),
        ),
    ),
    EventSpec(
        "activation_not_offered",
        note="An unbound device that was answered with no activation code, and why.",
        variants=(
            EventVariant(
                channel="samtal_server.ota",
                level=logging.WARNING,
                message=(
                    "device %s is unbound in the configuration this server started with, "
                    "but the database could not be read, so no activation code was "
                    "issued: this device may already be bound. Fix the database and it "
                    "is offered one at its next check"
                ),
                args=(arg_id(MAC),),
                fields=server_payload(
                    device=machine_id(MAC), reason=token({"unreadable"})
                ),
            ),
            EventVariant(
                channel="samtal_server.ota",
                level=logging.WARNING,
                message=(
                    "device %s is unbound but was offered no activation code: %s. It is "
                    "answered exactly as it was before onboarding existed, with no "
                    "token; bind it by its MAC with: samtal-server config bind-device "
                    "%s <agent>"
                ),
                args=(arg_id(MAC), arg_token(PENDING_REFUSALS), arg_id(MAC)),
                fields=server_payload(
                    device=machine_id(MAC), reason=token(PENDING_REFUSALS)
                ),
            ),
        ),
    ),
    EventSpec(
        "activation_complete",
        note="A waiting device has been claimed; its next check hands it a token.",
        variants=(
            EventVariant(
                channel="samtal_server.ota",
                level=logging.INFO,
                message="device %s is activated: its next configuration check hands it a token",
                args=(arg_id(MAC),),
                fields=server_payload(device=machine_id(MAC), agents=identifier_list()),
            ),
        ),
    ),
    EventSpec(
        "activation_pending",
        note="A waiting device polled and is still waiting.",
        variants=(
            EventVariant(
                channel="samtal_server.ota",
                level=logging.DEBUG,
                message="device %s is still waiting to be claimed",
                args=(arg_id(MAC),),
                fields=server_payload(
                    device=machine_id(MAC),
                    code=machine_id(
                        ACTIVATION_CODE,
                        nullable=True,
                        note="Null for a MAC this server holds no pending entry for.",
                    ),
                    unloaded=identifier_list(),
                ),
            ),
        ),
    ),
    EventSpec(
        "activation_refused",
        note=(
            "A version-2 activation poll failed one of the checks this "
            "server can hold it to. Nothing of the body is ever quoted: "
            "the checks name which one failed and stop there."
        ),
        variants=(
            EventVariant(
                channel="samtal_server.ota",
                level=logging.WARNING,
                message=(
                    "device %s sent a version-2 activation body that is not a JSON "
                    "object; it is answered as still waiting. Nothing of the body is "
                    "quoted here"
                ),
                args=(arg_id(MAC),),
                fields=server_payload(
                    device=machine_id(MAC),
                    code=machine_id(ACTIVATION_CODE),
                    reason=token({"unreadable_body"}),
                ),
            ),
            EventVariant(
                channel="samtal_server.ota",
                level=logging.WARNING,
                message=(
                    "device %s sent a version-2 activation body naming an algorithm this "
                    "server does not know; it is answered as still waiting. The value is "
                    "not quoted here, since it is whatever the request carried"
                ),
                args=(arg_id(MAC),),
                fields=server_payload(
                    device=machine_id(MAC),
                    code=machine_id(ACTIVATION_CODE),
                    reason=token({"unknown_algorithm"}),
                ),
            ),
            EventVariant(
                channel="samtal_server.ota",
                level=logging.WARNING,
                message=(
                    "device %s sent a version-2 activation body answering a challenge "
                    "this server did not issue for it; it is answered as still waiting"
                ),
                args=(arg_id(MAC),),
                fields=server_payload(
                    device=machine_id(MAC),
                    code=machine_id(ACTIVATION_CODE),
                    reason=token({"challenge_mismatch"}),
                ),
            ),
        ),
    ),
    EventSpec(
        "ota_request_rejected",
        note=(
            "A request this endpoint could not read. The sentence is one "
            "of three fixed refusals, so nothing a request carried is "
            "interpolated into the retained log."
        ),
        variants=(
            EventVariant(
                channel="samtal_server.ota",
                level=logging.WARNING,
                message="rejected OTA request: %s",
                args=(arg_token(OTA_REFUSALS),),
                fields=server_payload(),
            ),
        ),
    ),
    # --- onboarding.py: the banner and the short path ----------------
    EventSpec(
        "onboarding_banner",
        note="Where devices are configured, said once at startup.",
        variants=(
            EventVariant(
                channel="samtal_server.onboarding",
                level=logging.INFO,
                message=(
                    "device onboarding is off: devices are configured at the "
                    "server.ota_path path on %s (%s), which is not printed here, since "
                    "that segment is this deployment's secret"
                ),
                args=(arg_identifier(), arg_composed(ORIGIN_PROVENANCE)),
                fields=server_payload(
                    origin=identifier(),
                    origin_source=token(ORIGIN_SOURCES),
                    onboarding=flag(),
                ),
            ),
            EventVariant(
                channel="samtal_server.onboarding",
                level=logging.INFO,
                message=(
                    "device onboarding is on: devices are configured on %s (%s), at the "
                    "short path samtal-server config ota-url prints. The path is not "
                    "repeated here, since its key stands in front of the endpoint that "
                    "issues device tokens"
                ),
                args=(arg_identifier(), arg_composed(ORIGIN_PROVENANCE)),
                fields=server_payload(
                    origin=identifier(),
                    origin_source=token(ORIGIN_SOURCES),
                    onboarding=flag(),
                    keyed=flag(
                        note=(
                            "Whether anything stands in front of the short route at "
                            "all. A fact about the deployment rather than about the "
                            "key, which is what makes it safe to say."
                        )
                    ),
                ),
            ),
        ),
    ),
    EventSpec(
        "onboarding_key_mismatch",
        note="A request carried a key-shaped segment, and not this server's. Neither is repeated.",
        variants=(
            EventVariant(
                channel="samtal_server.onboarding",
                level=logging.WARNING,
                message=(
                    "a request reached the onboarding path carrying %d characters shaped "
                    "like a key, and not this server's; neither is repeated here. Check "
                    "the URL typed into the device's captive portal against the one "
                    "samtal-server config ota-url prints"
                ),
                args=(arg_count(),),
                fields=server_payload(attempted_length=count()),
            ),
        ),
    ),
    EventSpec(
        "onboarding_key_unshaped",
        note="A request carried something that is not key-shaped at all.",
        variants=(
            EventVariant(
                channel="samtal_server.onboarding",
                level=logging.WARNING,
                message=(
                    "a request reached the onboarding path carrying %d characters that "
                    "are not shaped like a key at all, so they are not repeated here; "
                    "the URL to type comes from samtal-server config ota-url"
                ),
                args=(arg_count(),),
                fields=server_payload(attempted_length=count()),
            ),
        ),
    ),
    # --- ws.py: the handshake gate -----------------------------------
    EventSpec(
        "auth_rejected",
        note=(
            "A handshake refused before the accept. No device: nothing is "
            "authenticated at this point, so the Device-Id header is a "
            "string whoever opened the socket chose."
        ),
        variants=(
            EventVariant(
                channel="samtal_server.ws",
                level=logging.WARNING,
                message="refused a websocket handshake from an unidentified client: %s",
                args=(arg_token({"no_token", "bad_token"}),),
                fields=server_payload(
                    device=machine_id(MAC, nullable=True),
                    reason=token({"no_token", "bad_token"}),
                ),
            ),
        ),
    ),
    # --- device/session.py: the conversation's own edge --------------
    EventSpec(
        "session_rejected",
        note=(
            "A device turned away. Emitted on both scopes: the session "
            "channel for the refusals a session makes after the accept, "
            "and `samtal_server.ws` for the one the endpoint makes before "
            "a session can run at all."
        ),
        variants=(
            EventVariant(
                channel=SESSION_CHANNEL,
                level=logging.WARNING,
                message=(
                    "session %s rejected: the Device-Id header is not a device MAC "
                    "(six colon-separated hex pairs)"
                ),
                args=(arg_id(SESSION_ID),),
                fields=session_payload(reason=token({"bad_device_id"})),
            ),
            EventVariant(
                channel=SESSION_CHANNEL,
                level=logging.WARNING,
                message=(
                    "session %s rejected: device %s is bound to agent %s, which this "
                    "server has not loaded; restart to load it"
                ),
                args=(arg_id(SESSION_ID), arg_id(MAC), arg_composed(AGENT_LIST)),
                fields=session_payload(reason=token({"agent_not_loaded"})),
            ),
            EventVariant(
                channel=SESSION_CHANNEL,
                level=logging.WARNING,
                message=(
                    "session %s rejected: device %s has no agent: bind it under devices "
                    "or set default_agent"
                ),
                args=(arg_id(SESSION_ID), arg_id(MAC)),
                fields=session_payload(reason=token({"no_agent"})),
            ),
            EventVariant(
                channel="samtal_server.ws",
                level=logging.WARNING,
                message="refused a websocket handshake from %s: the server is at capacity",
                args=(arg_composed(DEVICE_OR_UNIDENTIFIED),),
                fields=server_payload(
                    device=machine_id(MAC, nullable=True),
                    session=machine_id(SESSION_ID),
                    reason=token({"capacity"}),
                ),
            ),
        ),
    ),
    EventSpec(
        "session_open",
        note="A conversation starts.",
        variants=(
            EventVariant(
                channel=SESSION_CHANNEL,
                level=logging.INFO,
                message=(
                    "session %s open: device %s (client %s) agent %s%s, protocol v%d, "
                    "%d Hz %d ms frames in"
                ),
                args=(
                    arg_id(SESSION_ID),
                    arg_id(MAC),
                    arg_descriptor(CLIENT_BOUNDS),
                    arg_identifier(),
                    arg_composed(ALSO_BOUND_TO),
                    arg_whole(),
                    arg_whole(),
                    arg_whole(),
                ),
                fields=session_payload(
                    client=descriptor(
                        CLIENT_BOUNDS,
                        nullable=True,
                        note=(
                            "The device UUID, bounded for the event only: the capture "
                            "manifest and the conversation store keep the header as it "
                            "arrived."
                        ),
                    ),
                    agent=identifier(),
                    agents=identifier_list(),
                    protocol=whole(),
                    revision=identifier(
                        note=(
                            "Which build this server is, so every session from here on "
                            "is attributable to one."
                        )
                    ),
                ),
            ),
        ),
    ),
    EventSpec(
        "session_limit",
        note="The duration cap fires.",
        variants=(
            EventVariant(
                channel=SESSION_CHANNEL,
                level=logging.INFO,
                message="session %s reached the %.0f s time limit",
                args=(arg_id(SESSION_ID), arg_real()),
                fields=session_payload(duration_s=real()),
            ),
        ),
    ),
    EventSpec(
        "session_idle",
        note="The idle timeout hangs up on a realtime session.",
        variants=(
            EventVariant(
                channel=SESSION_CHANNEL,
                level=logging.INFO,
                message="session %s idle for %.0f s, hanging up",
                args=(arg_id(SESSION_ID), arg_real()),
                fields=session_payload(idle_s=real(), duration_s=real()),
            ),
        ),
    ),
    EventSpec(
        "session_closed",
        note="A conversation ends.",
        variants=(
            EventVariant(
                channel=SESSION_CHANNEL,
                level=logging.INFO,
                message="session %s closed (device %s)",
                args=(arg_id(SESSION_ID), arg_id(MAC)),
                fields=session_payload(
                    duration_s=real(),
                    reason=token(
                        CLOSE_REASONS,
                        note=(
                            "The first cause to fire, so a drain closing a session an "
                            "idle timer was about to hang up on reads `drain`."
                        ),
                    ),
                ),
            ),
        ),
    ),
    EventSpec(
        "speaking_started",
        note="The reply's first audio frame goes out.",
        variants=(
            EventVariant(
                channel=SESSION_CHANNEL,
                level=logging.INFO,
                message="session %s: speaking started",
                args=(arg_id(SESSION_ID),),
                fields=session_payload(agent=identifier()),
            ),
        ),
    ),
    # --- runtime/pipeline.py: what happens inside a conversation -----
    EventSpec(
        "heard",
        note=(
            "An utterance is transcribed. No transcript: what was said is "
            "the conversation store's, and what an operator measures with "
            "is how long the user spoke."
        ),
        variants=(
            EventVariant(
                channel=SESSION_CHANNEL,
                level=logging.INFO,
                message="session %s: heard %.2f s of speech",
                args=(arg_id(SESSION_ID), arg_real()),
                fields=session_payload(
                    agent=identifier(),
                    duration_s=real(),
                    language=machine_id(
                        LANGUAGE,
                        required=False,
                        note="Only engines that detected carry this.",
                    ),
                    language_confidence=real(required=False),
                ),
            ),
        ),
    ),
    EventSpec(
        "replied",
        note="A reply finishes.",
        variants=(
            EventVariant(
                channel=SESSION_CHANNEL,
                level=logging.INFO,
                message="session %s: %s replied in %d sentences",
                args=(arg_id(SESSION_ID), arg_identifier(), arg_count()),
                fields=session_payload(
                    agent=identifier(),
                    sentences=count(
                        note=(
                            "How many of them the user heard, so a reply a barge-in cut "
                            "short reports what went out."
                        )
                    ),
                ),
            ),
        ),
    ),
    EventSpec(
        "agent_said",
        note="One agent's part of a reply.",
        variants=(
            EventVariant(
                channel=SESSION_CHANNEL,
                level=logging.INFO,
                message="session %s: %s said %d sentences",
                args=(arg_id(SESSION_ID), arg_identifier(), arg_count()),
                fields=session_payload(agent=identifier(), sentences=count()),
            ),
        ),
    ),
    EventSpec(
        "handover",
        note="`switch_agent` succeeds.",
        variants=(
            EventVariant(
                channel=SESSION_CHANNEL,
                level=logging.INFO,
                message="session %s: handed over from agent %s to %s",
                args=(arg_id(SESSION_ID), arg_identifier(), arg_identifier()),
                fields=session_payload(from_agent=identifier(), to_agent=identifier()),
            ),
        ),
    ),
    EventSpec(
        "prompt_assembled",
        note=(
            "The know-how half of a prompt is assembled and cached. The "
            "per-round memory read is deliberately not part of it, which "
            "is why `memory` is not one of the provenance forms."
        ),
        variants=(
            EventVariant(
                channel=SESSION_CHANNEL,
                level=logging.INFO,
                message="session %s: assembled %d characters of prompt for %s",
                args=(arg_id(SESSION_ID), arg_count(), arg_identifier()),
                fields=session_payload(
                    agent=identifier(),
                    characters=count(),
                    sources=sources(
                        note=(
                            "Each block's size by provenance: how much of the prompt "
                            "came from where, never any of the prompt itself."
                        )
                    ),
                ),
            ),
        ),
    ),
    EventSpec(
        "llm_retry",
        note="The first-token watchdog cancels a stalled generation and retries the round once.",
        variants=(
            EventVariant(
                channel=SESSION_CHANNEL,
                level=logging.WARNING,
                message="session %s: no first token after %.1f s, retrying round %d",
                args=(arg_id(SESSION_ID), arg_real(), arg_whole()),
                fields=session_payload(
                    agent=identifier(),
                    round=whole(),
                    duration_ms=whole(),
                    stage=identifier(),
                ),
                note="A provider the registry did not build names no entry.",
            ),
            EventVariant(
                channel=SESSION_CHANNEL,
                level=logging.WARNING,
                message="session %s: no first token after %.1f s, retrying round %d",
                args=(arg_id(SESSION_ID), arg_real(), arg_whole()),
                fields=session_payload(
                    agent=identifier(),
                    round=whole(),
                    duration_ms=whole(),
                    stage=identifier(),
                    provider=identifier(),
                    type=identifier(),
                    host=identifier(required=False),
                    model=identifier(
                        required=False,
                        note="The GenAI conventions' `gen_ai.request.model`.",
                    ),
                ),
                note=(
                    "`provider` and `type` are atomic: a provider with an identity "
                    "carries both. `host` is absent for an engine that runs in this "
                    "process and `model` for a type that has none to name."
                ),
            ),
        ),
    ),
    EventSpec(
        "llm_round",
        note="A generation call finishes.",
        variants=(
            EventVariant(
                channel=SESSION_CHANNEL,
                level=logging.INFO,
                message="session %s: %s round %d took %.2f s over %d turns",
                args=(
                    arg_id(SESSION_ID),
                    arg_identifier(),
                    arg_whole(),
                    arg_real(),
                    arg_count(),
                ),
                fields=session_payload(
                    agent=identifier(),
                    round=whole(
                        note=(
                            "Counts the whole reply rather than one agent's leg, so the "
                            "generation after a handover is a round of its own."
                        )
                    ),
                    turns=count(note="The cheap proxy for payload size."),
                    duration_ms=whole(),
                    stage=identifier(),
                    input_tokens=count(required=False),
                    output_tokens=count(required=False),
                    first_token_ms=whole(
                        required=False,
                        note=(
                            "Times the first spoken token, so a round that only asked "
                            "for a tool carries none."
                        ),
                    ),
                ),
            ),
            EventVariant(
                channel=SESSION_CHANNEL,
                level=logging.INFO,
                message="session %s: %s round %d took %.2f s over %d turns",
                args=(
                    arg_id(SESSION_ID),
                    arg_identifier(),
                    arg_whole(),
                    arg_real(),
                    arg_count(),
                ),
                fields=session_payload(
                    agent=identifier(),
                    round=whole(),
                    turns=count(),
                    duration_ms=whole(),
                    stage=identifier(),
                    provider=identifier(),
                    type=identifier(),
                    host=identifier(required=False),
                    model=identifier(
                        required=False,
                        note=(
                            "Present where the configured entry names one. The GenAI "
                            "conventions' `gen_ai.request.model`."
                        ),
                    ),
                    input_tokens=count(
                        required=False,
                        note=(
                            "Present where the provider reported usage; their "
                            "absence is a fact about the endpoint."
                        ),
                    ),
                    output_tokens=count(required=False),
                    first_token_ms=whole(required=False),
                ),
            ),
        ),
    ),
    EventSpec(
        "provider_failed",
        note=(
            "An ASR, LLM or TTS call fails. The class name is reported and "
            "the exception's message is not: a type name says what went "
            "wrong, a message says what a stranger wrote."
        ),
        variants=(
            EventVariant(
                channel=SESSION_CHANNEL,
                level=logging.WARNING,
                message="session %s: %s provider%s %s after %.2f s%s: %s",
                args=(
                    arg_id(SESSION_ID),
                    arg_identifier(),
                    arg_composed(EMPTY_FRAGMENT),
                    arg_token({"timed out", "failed"}),
                    arg_real(),
                    arg_composed(EMPTY_FRAGMENT),
                    arg_class_name(),
                ),
                fields=session_payload(
                    agent=identifier(),
                    error=class_name(),
                    duration_ms=whole(),
                    stage=identifier(),
                ),
                note="A provider the registry did not build names no entry and no host.",
            ),
            EventVariant(
                channel=SESSION_CHANNEL,
                level=logging.WARNING,
                message="session %s: %s provider%s %s after %.2f s%s: %s",
                args=(
                    arg_id(SESSION_ID),
                    arg_identifier(),
                    arg_composed(QUOTED_PROVIDER),
                    arg_token({"timed out", "failed"}),
                    arg_real(),
                    arg_composed(REACHING_HOST),
                    arg_class_name(),
                ),
                fields=session_payload(
                    agent=identifier(),
                    error=class_name(
                        note=(
                            "A round whose retry also stalled carries "
                            "`FirstTokenTimeout`."
                        )
                    ),
                    duration_ms=whole(),
                    stage=identifier(),
                    provider=identifier(),
                    type=identifier(),
                    host=identifier(required=False),
                    model=identifier(required=False),
                ),
            ),
        ),
    ),
    EventSpec(
        "tool_call",
        note=(
            "A tool returns. `source` says which namespace the model "
            "reached into; the name itself is only ever this server's own "
            "word for it."
        ),
        variants=(
            EventVariant(
                channel=SESSION_CHANNEL,
                level=logging.INFO,
                message="session %s: %s tool%s took %.2f s%s",
                args=(
                    arg_id(SESSION_ID),
                    arg_token({"builtin"}),
                    arg_composed(QUOTED_TOOL_NAME),
                    arg_real(),
                    arg_token({"", " and failed"}),
                ),
                fields=session_payload(
                    agent=identifier(),
                    source=token({"builtin"}),
                    tool=identifier(note="The only tool names this server authors."),
                    duration_ms=whole(),
                    is_error=flag(),
                ),
            ),
            EventVariant(
                channel=SESSION_CHANNEL,
                level=logging.INFO,
                message="session %s: %s tool%s took %.2f s%s",
                args=(
                    arg_id(SESSION_ID),
                    arg_token({"mcp"}),
                    arg_composed(FROM_ENTRY),
                    arg_real(),
                    arg_token({"", " and failed"}),
                ),
                fields=session_payload(
                    agent=identifier(),
                    source=token({"mcp"}),
                    entry=identifier(
                        note="The configured entry, never the far side's tool name."
                    ),
                    duration_ms=whole(),
                    is_error=flag(),
                ),
            ),
            EventVariant(
                channel=SESSION_CHANNEL,
                level=logging.INFO,
                message="session %s: %s tool%s took %.2f s%s",
                args=(
                    arg_id(SESSION_ID),
                    arg_token({"device", "unknown"}),
                    arg_composed(EMPTY_FRAGMENT),
                    arg_real(),
                    arg_token({"", " and failed"}),
                ),
                fields=session_payload(
                    agent=identifier(),
                    source=token({"device", "unknown"}),
                    duration_ms=whole(),
                    is_error=flag(),
                ),
                note=(
                    "A device tool's name is the board's vocabulary and an unknown "
                    "one is whatever the model invented, so neither is named."
                ),
            ),
        ),
    ),
    EventSpec(
        "barge_in",
        note="Speech cuts a reply short.",
        variants=(
            EventVariant(
                channel=SESSION_CHANNEL,
                level=logging.INFO,
                message="session %s: barge-in, cancelling the reply in flight",
                args=(arg_id(SESSION_ID),),
                fields=session_payload(
                    speech_ms=whole(),
                    speaking_ms=whole(
                        required=False,
                        note=(
                            "Milliseconds from `speaking_started` to the cancel "
                            "decision, absent when the reply had not yet spoken."
                        ),
                    ),
                ),
            ),
        ),
    ),
    EventSpec(
        "barge_in_suppressed",
        note="An interruption is dropped and the reply lives.",
        variants=(
            EventVariant(
                channel=SESSION_CHANNEL,
                level=logging.INFO,
                message=(
                    "session %s: barge-in suppressed, %d ms of speech is under the "
                    "%.0f ms floor"
                ),
                args=(arg_id(SESSION_ID), arg_whole(), arg_real()),
                fields=session_payload(reason=token({"min_speech"}), speech_ms=whole()),
            ),
            EventVariant(
                channel=SESSION_CHANNEL,
                level=logging.INFO,
                message="session %s: barge-in suppressed inside the refractory window",
                args=(arg_id(SESSION_ID),),
                fields=session_payload(reason=token({"refractory"}), speech_ms=whole()),
            ),
            EventVariant(
                channel=SESSION_CHANNEL,
                level=logging.INFO,
                message="session %s: barge-in suppressed, nothing transcribed",
                args=(arg_id(SESSION_ID),),
                fields=session_payload(
                    reason=token({"no_transcript"}), speech_ms=whole()
                ),
            ),
        ),
    ),
    EventSpec(
        "barge_in_merged",
        note="An interruption merges with the utterance the reply was transcribing.",
        variants=(
            EventVariant(
                channel=SESSION_CHANNEL,
                level=logging.INFO,
                message="session %s: barge-in mid-transcription, merging the utterances",
                args=(arg_id(SESSION_ID),),
                fields=session_payload(speech_ms=whole()),
            ),
        ),
    ),
    EventSpec(
        "filler_skipped",
        note="The filler timer fired but the user was there first, so no clip played.",
        variants=(
            EventVariant(
                channel=SESSION_CHANNEL,
                level=logging.INFO,
                message="session %s: filler skipped, the user is speaking (%d ms heard)",
                args=(arg_id(SESSION_ID), arg_whole()),
                fields=session_payload(
                    agent=identifier(),
                    reason=token({"user_speaking"}),
                    speech_ms=whole(),
                ),
            ),
            EventVariant(
                channel=SESSION_CHANNEL,
                level=logging.INFO,
                message="session %s: filler skipped, a barge-in is being confirmed",
                args=(arg_id(SESSION_ID),),
                fields=session_payload(
                    agent=identifier(), reason=token({"barge_in_pending"})
                ),
            ),
        ),
    ),
    EventSpec(
        "filler_played",
        note=(
            "The reply was slow, so a pre-synthesized clip masked the "
            "wait. Its first frame is the turn's `speaking_started`."
        ),
        variants=(
            EventVariant(
                channel=SESSION_CHANNEL,
                level=logging.INFO,
                message="session %s: no reply audio after %d ms, playing filler %d",
                args=(arg_id(SESSION_ID), arg_whole(), arg_count()),
                fields=session_payload(
                    agent=identifier(),
                    delay_ms=whole(
                        note="Measured, from the transcription to the fire."
                    ),
                    phrase_index=count(),
                ),
            ),
        ),
    ),
    # --- providers/openai_asr.py: the prompt-echo guard --------------
    EventSpec(
        "asr_prompt_echo",
        note=(
            "A transcript came back as the ASR prompt and the clip was "
            "retried once without it, on what the first request left of "
            "`timeout_s`. No session or device: providers are shared "
            "singletons that serve every conversation, so the event names "
            "the host instead."
        ),
        variants=(
            EventVariant(
                channel="samtal_server.providers.openai_asr",
                level=logging.WARNING,
                message=(
                    "openai asr: the transcript came back as the configured prompt with "
                    "%.1f s of the timeout left, too little to retry, treating %.2f s of "
                    "audio as nothing said"
                ),
                args=(arg_real(), arg_real()),
                fields=server_payload(
                    outcome=token(
                        {"skipped"},
                        note="Under a second of budget remained, so no retry was sent.",
                    ),
                    duration_s=real(),
                    host=identifier(),
                ),
            ),
            EventVariant(
                channel="samtal_server.providers.openai_asr",
                level=logging.WARNING,
                message=(
                    "openai asr: the retry outran the timeout's remaining %.1f s, "
                    "treating %.2f s of audio as nothing said"
                ),
                args=(arg_real(), arg_real()),
                fields=server_payload(
                    outcome=token(
                        {"timed_out"},
                        note="The retry outran what the first request left of the budget.",
                    ),
                    duration_s=real(),
                    host=identifier(),
                    retry_ms=whole(),
                ),
            ),
            EventVariant(
                channel="samtal_server.providers.openai_asr",
                level=logging.WARNING,
                message=(
                    "openai asr: the retry came back as the prompt again, treating "
                    "%.2f s of audio as nothing said"
                ),
                args=(arg_real(),),
                fields=server_payload(
                    outcome=token(
                        {"confirmed_echo"},
                        note="The retry came back as the configured prompt again.",
                    ),
                    duration_s=real(),
                    host=identifier(),
                    retry_ms=whole(),
                ),
            ),
            EventVariant(
                channel="samtal_server.providers.openai_asr",
                level=logging.WARNING,
                message=(
                    "openai asr: the retry came back empty, treating %.2f s of audio as "
                    "nothing said"
                ),
                args=(arg_real(),),
                fields=server_payload(
                    outcome=token(
                        {"confirmed_empty"},
                        note="The retry heard nothing.",
                    ),
                    duration_s=real(),
                    host=identifier(),
                    retry_ms=whole(),
                ),
            ),
            EventVariant(
                channel="samtal_server.providers.openai_asr",
                level=logging.INFO,
                message=(
                    "openai asr: the retry recovered %.2f s of audio the echo guard "
                    "would have discarded"
                ),
                args=(arg_real(),),
                fields=server_payload(
                    outcome=token(
                        {"recovered"},
                        note=(
                            "The retry's transcript is heard. What was recovered is not "
                            "in the sentence: conversation-derived text is banned on "
                            "the events however it was recovered (#165)."
                        ),
                    ),
                    duration_s=real(),
                    host=identifier(),
                    retry_ms=whole(),
                ),
            ),
        ),
    ),
    # --- tools/mcp.py: the MCP lifecycle -----------------------------
    EventSpec(
        "mcp_connected",
        note=(
            "An entry's connect finishes and its tools are published. No "
            "session or device: one entry serves every conversation, and "
            "the rest of this block is the same."
        ),
        variants=(
            EventVariant(
                channel="samtal_server.tools.mcp",
                level=logging.INFO,
                message="mcp server %s connected with %d tool(s)",
                args=(arg_identifier(), arg_count()),
                fields=server_payload(
                    entry=identifier(),
                    transport=token({"stdio", "streamable_http"}),
                    tools=count(note="A count, never a list."),
                    duration_ms=whole(),
                ),
            ),
        ),
    ),
    EventSpec(
        "mcp_down",
        note="An entry fails to come up, or its connection is given up.",
        variants=(
            EventVariant(
                channel="samtal_server.tools.mcp",
                level=logging.WARNING,
                message="mcp server %s is unavailable, its tools are absent: %s",
                args=(arg_identifier(), arg_class_name(joined=True)),
                fields=server_payload(
                    entry=identifier(),
                    reason=token(MCP_CONNECT_REASONS),
                    duration_ms=whole(
                        note="How long the connect ran before it failed."
                    ),
                ),
            ),
            EventVariant(
                channel="samtal_server.tools.mcp",
                level=logging.INFO,
                message="mcp server %s is stopped and its tools are gone",
                args=(arg_identifier(),),
                fields=server_payload(entry=identifier(), reason=token({"stopped"})),
                note=(
                    "The intentional one, a shutdown or a reload, and the only "
                    "`mcp_down` at INFO. No duration: how long a working connection "
                    "lasted is a different number under the same name."
                ),
            ),
            EventVariant(
                channel="samtal_server.tools.mcp",
                level=logging.WARNING,
                message="mcp server %s: dropping the connection after a failed call",
                args=(arg_identifier(),),
                fields=server_payload(
                    entry=identifier(), reason=token({"call_failed"})
                ),
                note="Always beside an `mcp_call_dropped`, in that order.",
            ),
        ),
    ),
    EventSpec(
        "mcp_call_dropped",
        note=(
            "A tool call failed and the connection was dropped because of "
            "it. The tool is said by its position in the far side's "
            "listing and never by its name: half a published name is what "
            "the far side called its tool."
        ),
        variants=(
            EventVariant(
                channel="samtal_server.tools.mcp",
                level=logging.WARNING,
                message=(
                    "mcp server %s: the call to published tool %s failed (%s), so its "
                    "answer is lost"
                ),
                args=(
                    arg_identifier(),
                    arg_count(nullable=True),
                    arg_class_name(joined=True),
                ),
                fields=server_payload(
                    entry=identifier(),
                    position=count(
                        nullable=True,
                        note=(
                            "The tool's place in the far side's listing, counted from "
                            "one. Null for a name this connection no longer knows."
                        ),
                    ),
                    error=class_name(
                        joined=True,
                        note=(
                            "The failure's class name, and for a group of them the "
                            "sorted names joined with a comma. Never a message."
                        ),
                    ),
                ),
            ),
        ),
    ),
    EventSpec(
        "mcp_tool_shadowed",
        note="A published tool is dropped because a more specific entry owns its name.",
        variants=(
            EventVariant(
                channel="samtal_server.tools.mcp",
                level=logging.WARNING,
                message=(
                    "mcp server %s: dropping published tool %d, its name is inside the "
                    "namespace of the entry %s, which owns it"
                ),
                args=(arg_identifier(), arg_count(), arg_identifier()),
                fields=server_payload(
                    entry=identifier(),
                    position=count(note="The tool's place in the far side's listing."),
                    owner=identifier(),
                ),
            ),
        ),
    ),
    EventSpec(
        "mcp_reload",
        note=(
            "A reload of the MCP servers finishes, whether or not the "
            "caller is still connected. Exactly one per reload, at "
            "whichever of the two phases ended it."
        ),
        variants=(
            EventVariant(
                channel="samtal_server.tools.mcp",
                level=logging.WARNING,
                message="mcp servers were not reloaded and nothing was changed (%s)",
                args=(arg_token(MCP_REFUSAL_REASONS),),
                fields=server_payload(
                    outcome=token({"refused"}),
                    reason=token(
                        MCP_REFUSAL_REASONS,
                        note=(
                            "Chosen where the exception is classified and never built "
                            "out of its message."
                        ),
                    ),
                ),
            ),
            EventVariant(
                channel="samtal_server.tools.mcp",
                level=logging.INFO,
                message="mcp servers reloaded: %d started, %d restarted, %d stopped, %d unchanged",
                args=(arg_count(), arg_count(), arg_count(), arg_count()),
                fields=server_payload(
                    outcome=token({"applied"}),
                    started=count(),
                    restarted=count(),
                    stopped=count(),
                    unchanged=count(),
                    duration_ms=whole(
                        note=(
                            "Measured from when the request was accepted, so it covers "
                            "the re-read as well as the apply."
                        )
                    ),
                ),
            ),
        ),
    ),
    # --- tools/memory.py ---------------------------------------------
    EventSpec(
        "memory_unreadable",
        note="An agent's memory could not be read; it remembers nothing this round.",
        variants=(
            EventVariant(
                channel="samtal_server.tools.memory",
                level=logging.WARNING,
                message="could not read memory for agent %s (%s); it remembers nothing this round",
                args=(arg_identifier(), arg_class_name()),
                fields=server_payload(agent=identifier(), error=class_name()),
            ),
        ),
    ),
    # --- filler.py ---------------------------------------------------
    EventSpec(
        "filler_disabled",
        note="Filler synthesis failed for one agent, so latency masking is off for it.",
        variants=(
            EventVariant(
                channel="samtal_server.filler",
                level=logging.WARNING,
                message=(
                    "agent %s: filler synthesis failed, latency masking is off for this "
                    "agent (%s)"
                ),
                args=(arg_identifier(), arg_class_name()),
                fields=server_payload(agent=identifier(), error=class_name()),
            ),
        ),
    ),
    # --- capture.py: the recording surface ---------------------------
    EventSpec(
        "capture_started",
        note="A session is being recorded.",
        variants=(
            EventVariant(
                channel="samtal_server.capture",
                level=logging.INFO,
                message="session %s: capturing to %s",
                args=(arg_id(SESSION_ID), arg_path()),
                fields=server_payload(
                    session=machine_id(SESSION_ID), path=identifier()
                ),
            ),
        ),
    ),
    EventSpec(
        "capture_declined",
        note="A session is not being recorded, and why.",
        variants=(
            EventVariant(
                channel="samtal_server.capture",
                level=logging.WARNING,
                message="session %s: not capturing, %s is unusable (%s)",
                args=(arg_id(SESSION_ID), arg_path(), arg_class_name()),
                fields=server_payload(
                    session=machine_id(SESSION_ID),
                    reason=token({"unusable"}),
                    failure=class_name(),
                ),
            ),
            EventVariant(
                channel="samtal_server.capture",
                level=logging.WARNING,
                message="session %s: not capturing, %.0f MB free is below the %.0f MB floor",
                args=(arg_id(SESSION_ID), arg_real(), arg_real()),
                fields=server_payload(
                    session=machine_id(SESSION_ID),
                    reason=token({"min_free_mb"}),
                    free_mb=count(),
                ),
            ),
            EventVariant(
                channel="samtal_server.capture",
                level=logging.WARNING,
                message="session %s: not capturing, could not open the files (%s)",
                args=(arg_id(SESSION_ID), arg_class_name()),
                fields=server_payload(
                    session=machine_id(SESSION_ID),
                    reason=token({"open"}),
                    failure=class_name(),
                ),
            ),
        ),
    ),
    EventSpec(
        "capture_limit",
        note="A recording reached its per-session ceiling.",
        variants=(
            EventVariant(
                channel="samtal_server.capture",
                level=logging.INFO,
                message="session %s: capture reached its %.0f s limit",
                args=(arg_id(SESSION_ID), arg_real()),
                fields=server_payload(session=machine_id(SESSION_ID)),
            ),
        ),
    ),
    EventSpec(
        "capture_failed",
        note="A recording stopped after a write failed.",
        variants=(
            EventVariant(
                channel="samtal_server.capture",
                level=logging.WARNING,
                message="session %s: capture stopped after failing to %s (%s)",
                args=(
                    arg_id(SESSION_ID),
                    arg_token({"write audio", "write an event"}),
                    arg_class_name(),
                ),
                fields=server_payload(
                    session=machine_id(SESSION_ID),
                    reason=token(
                        {"write audio", "write an event"},
                        note="Which of the recording's two tracks the write was for.",
                    ),
                    failure=class_name(),
                ),
            ),
        ),
    ),
    EventSpec(
        "capture_pruned",
        note="Old recordings were removed to stay inside the disk budget.",
        variants=(
            EventVariant(
                channel="samtal_server.capture",
                level=logging.INFO,
                message="capture: pruned %d session(s) to stay under %.0f MB: %s",
                args=(arg_count(), arg_real(), arg_composed(SESSION_LIST)),
                fields=server_payload(
                    sessions=id_list(
                        SESSION_ID, note="The ids themselves, not a count."
                    )
                ),
            ),
        ),
    ),
    EventSpec(
        "capture_over_budget",
        note="The disk budget is exceeded and nothing more can be pruned.",
        variants=(
            EventVariant(
                channel="samtal_server.capture",
                level=logging.WARNING,
                message=(
                    "capture: %.0f MB on disk is over the %.0f MB budget and nothing "
                    "more can be pruned; raise max_total_mb or lower max_session_s"
                ),
                args=(arg_real(), arg_real()),
                fields=server_payload(total_mb=count()),
            ),
        ),
    ),
    # --- app.py: what the composition root says about capture --------
    EventSpec(
        "capture_enabled",
        note=(
            "Said once at startup, at WARNING: recording room audio is a "
            "thing an operator should not discover by accident."
        ),
        variants=(
            EventVariant(
                channel="samtal_server.app",
                level=logging.WARNING,
                message=(
                    "session capture is on: room audio and a track of the session's "
                    "events are being written to %s"
                ),
                args=(arg_path(),),
                fields=server_payload(path=identifier()),
            ),
        ),
    ),
    EventSpec(
        "capture_disabled",
        note="Capture is configured but off.",
        variants=(
            EventVariant(
                channel="samtal_server.app",
                level=logging.INFO,
                message=(
                    "session capture is configured but off; set server.capture.enabled "
                    "to record to %s"
                ),
                args=(arg_path(),),
                fields=server_payload(path=identifier()),
            ),
        ),
    ),
    # --- conversations/store.py: the system of record for content ----
    EventSpec(
        "conversations_enabled",
        note=(
            "The store opens at startup, which means this server is "
            "recording what is said to it. Said once, before anything "
            "connects, and at WARNING for the reason `capture_enabled` is."
        ),
        variants=(
            EventVariant(
                channel="samtal_server.conversations.store",
                level=logging.WARNING,
                message="recording conversations to %s",
                args=(arg_path(),),
                fields=server_payload(path=identifier()),
            ),
        ),
    ),
    EventSpec(
        "conversations_dropped",
        note=(
            "The store is behind and events for one session are being "
            "dropped. Said once per session at its first drop; the total "
            "lands on that session's row."
        ),
        variants=(
            EventVariant(
                channel="samtal_server.conversations.store",
                level=logging.WARNING,
                message="session %s: the conversation store is behind, dropping events",
                args=(arg_id(SESSION_ID),),
                fields=server_payload(session=machine_id(SESSION_ID)),
            ),
        ),
    ),
    EventSpec(
        "conversations_failed",
        note="A write to the store failed and its batch was dropped, or a prune could not run.",
        variants=(
            EventVariant(
                channel="samtal_server.conversations.store",
                level=logging.WARNING,
                message="the conversation store dropped a batch after a write failed (%s)",
                args=(arg_class_name(),),
                fields=server_payload(
                    failure=class_name(
                        note="The exception's class name, never its message."
                    )
                ),
            ),
            EventVariant(
                channel="samtal_server.conversations.store",
                level=logging.WARNING,
                message="the conversation store could not prune (%s)",
                args=(arg_class_name(),),
                fields=server_payload(failure=class_name()),
            ),
        ),
    ),
    EventSpec(
        "conversations_pruned",
        note="Retention deleted sessions older than the window. At INFO: a policy doing its job.",
        variants=(
            EventVariant(
                channel="samtal_server.conversations.store",
                level=logging.INFO,
                message="conversations: pruned %d session(s) older than %d days",
                args=(arg_count(), arg_count()),
                fields=server_payload(sessions=count(note="A count, not a list.")),
            ),
        ),
    ),
    # --- registry.py: the shutdown drain -----------------------------
    EventSpec(
        "drain_started",
        note="A shutdown begins draining.",
        variants=(
            EventVariant(
                channel="samtal_server.registry",
                level=logging.INFO,
                message="draining %d session(s), up to %.0f s",
                args=(arg_count(), arg_real()),
                fields=server_payload(sessions=count(), timeout_s=real()),
            ),
        ),
    ),
    EventSpec(
        "drain_finished",
        note="Every reply finished speaking.",
        variants=(
            EventVariant(
                channel="samtal_server.registry",
                level=logging.INFO,
                message="every session drained",
                args=(),
                fields=server_payload(sessions=count()),
            ),
        ),
    ),
    EventSpec(
        "drain_incomplete",
        note="A reply was cut, or a session hung.",
        variants=(
            EventVariant(
                channel="samtal_server.registry",
                level=logging.WARNING,
                message="drained with %d session(s) cut mid-reply and %d that did not finish",
                args=(arg_count(), arg_count()),
                fields=server_payload(
                    sessions=count(),
                    cut_mid_reply=count(),
                    unfinished=count(),
                    timeout_s=real(),
                ),
            ),
        ),
    ),
    # --- device/bindings.py: the live view of who is bound -----------
    EventSpec(
        "device_bindings_snapshot_only",
        note="There is no configuration database, so bindings resolve from the boot snapshot.",
        variants=(
            EventVariant(
                channel="samtal_server.device.bindings",
                level=logging.DEBUG,
                message=(
                    "no configuration database at %s: device bindings resolve from the "
                    "configuration this server was built with"
                ),
                args=(arg_path(),),
                fields=server_payload(path=identifier()),
            ),
        ),
    ),
    EventSpec(
        "device_bindings_unreadable",
        note="The database could not be read, so the answer is the boot snapshot's.",
        variants=(
            EventVariant(
                channel="samtal_server.device.bindings",
                level=logging.WARNING,
                message=(
                    "cannot read the device bindings for %s; answering from the "
                    "configuration this server started with, which may be older than "
                    "the database. The failure's kind is recorded beside this line"
                ),
                args=(arg_id(MAC),),
                fields=server_payload(
                    device=machine_id(MAC), failure=class_name()
                ),
            ),
        ),
    ),
    # --- config/api.py: the administration surface -------------------
    EventSpec(
        "api_error",
        note="The configuration API failed to handle a request. The class name and nothing else.",
        variants=(
            EventVariant(
                channel="samtal_server.config.api",
                level=logging.ERROR,
                message="the configuration API failed to handle a request (%s)",
                args=(arg_class_name(),),
                fields=server_payload(),
            ),
        ),
    ),
    EventSpec(
        "api_storage_error",
        note="The configuration API met unreadable stored state.",
        variants=(
            EventVariant(
                channel="samtal_server.config.api",
                level=logging.ERROR,
                message="the configuration API met unreadable stored state (%s)",
                args=(arg_class_name(),),
                fields=server_payload(),
            ),
        ),
    ),
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
    "reproduce it under SAMTAL_EVENTS_ENFORCEMENT=strict to see which"
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
