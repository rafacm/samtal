# Declare every event's schema and enforce it at emit time

## Goal

Implement issue #155: the no-leak contract on the event surface is a
convention held by review vigilance (nineteen of the 2026-08-14
batch's roughly thirty review findings were leak-shaped content on
the retained log), and the content-and-telemetry ADR decided the
events carry metadata only. This issue turns that decision into
machinery: one registry declares every event's name, level, scope,
and exact field set with per-field kinds and closed token sets, the
emitters enforce the declaration at emit time (strict in tests and
development, forgiving in production), and the README event table is
provably in agreement with the registry. After this issue, a new
field carrying far-side bytes is a schema violation a test lane
refuses, not a review finding.

The companion implementation doc,
[`2026-08-16-schema-declared-events-implementation.md`](2026-08-16-schema-declared-events-implementation.md),
records what each milestone actually did, deviations from this plan,
and discoveries; a milestone with no deviations says so explicitly.

## The issue's decisions, restated

Settled by issue #155 and not re-litigated here:

1. **One registry declares every event**: name, level, channel, and
   the exact field set with per-field types; reason-token fields
   declare their closed token sets. The README event table is
   generated from or checked against the registry, so the two cannot
   drift.
2. **Enforcement is at emit time in the emitter**, strict in tests
   and development (an undeclared event, an undeclared field, a
   wrong type, or an unlisted token raises) and forgiving in
   production (the emit is logged with a one-line non-event
   complaint and the offending fields dropped, because a telemetry
   bug must never take down a reply).
3. **Free-text fields do not exist.** Every string field is one of:
   a trusted configured identifier (entry, agent, stage), a token
   from a declared closed set, a class name, or a bounded numeric or
   id form. A field that needs prose is a design error the registry
   refuses to encode.
4. **Usage and model fields adopt the OTel GenAI vocabulary** per
   the ADR, in the same change that declares them.
5. **The existing pin suites are the no-behavior-change contract**:
   `tests/unit/test_event_surface_pins.py` and
   `tests/unit/test_server_event_pins.py` pass unmodified, proving
   the registry describes the surface that exists rather than a new
   one.

## Evidence, re-verified at plan time

The issue's evidence is pinned to main@749bd23 (68 paths); at
main@af9e4d4, after #120's M5 narrowing, #144, the 2026-08-17 ADR
amendment, and #165's sentence narrowing (this plan's prerequisite,
merged as PR #166), the surface is:

- **81 emit sites, 57 distinct event names**, inventoried by an AST
  scan of every `events.<level>(...)` call carrying `event=` in
  `samtal_server/` (the scan is this plan's tooling-backed
  inventory; it becomes a committed conformance test in M1 rather
  than a one-off). The count moved from the issue's 68 because #120
  added the `conversations_*` events and M5 reshaped several fields.
  The two contract pin files pin the surface as it stood at their
  #138 baseline; the `conversations_*` events postdate it, appear
  in NEITHER pin file, and their existing tests assert selected
  attributes rather than the exact channel, sentence, arguments and
  payload. M1 therefore adds a pre-enforcement characterization pin
  file (`tests/unit/test_conversations_event_pins.py`, in the exact
  style of the two contract files) covering the five store paths,
  with the coverage comparison PATH-BASED rather than name-based:
  the two contract files hold 76 literal per-path expectations,
  the five store paths complete the 81, and the conformance test
  asserts that correspondence so an unpinned new path for an
  existing event name is caught; committed green BEFORE any
  enforcement exists, so M2's strict lanes stand on pins for the
  whole surface. The two existing files stay byte-unchanged.
- **One session channel and 13 server channels**: `SessionEvents`
  emits on `samtal_server.session`; `ServerEvents` is constructed
  with `__name__` in `registry`, `onboarding`, `ws`, `capture`,
  `filler`, `app`, `ota`, `tools/memory`, `tools/mcp`,
  `config/api`, `providers/openai_asr`, `conversations/store`, and
  `device/bindings`.
- **Nine events take part of their payload from a `**spread`** whose
  keys are not visible at the call site: `asr_prompt_echo`
  (`_echo_fields`), `ota_check` (a local `fields` dict),
  `heard` (`language_fields`), `llm_retry` and `llm_round`
  (`provider_fields` and `tokens`), `provider_failed` and
  `tool_call` (local `fields`), `activation_refused` (`refusal`),
  `barge_in` (`_speaking_ms_field`). Each spread's key set is closed
  and readable in its builder, and the existing pin suites already
  pin the results; declaring them is reading, not guessing.
- **One event name lives on both scopes**: `session_rejected` is
  emitted by the session emitter (handshake refusals) and by
  `ws.py`'s server emitter (auth-stage refusals naming `device` and
  `session` explicitly).
- **The GenAI adoption already happened**: #120's M5 (PR #160)
  renamed the usage fields to `input_tokens`/`output_tokens` and put
  `model` on `provider_fields`. This issue declares those fields;
  decision 4 is satisfied by declaration, with no rename left to do,
  which is verified in M1 by reading `provider_fields` and the
  `tokens` builder rather than assumed.
- **The README event table** sits in `samtal-server/README.md`'s
  Logging section (36 rows at f35001a), with prose `when` cells and
  `fields` cells that name fields and tokens in backticks; nothing
  checks it against anything today.
- **The AST guard precedent** (`test_event_surface_guard.py`) bans
  hand-built `extra=` outside `events.py`; the registry's
  conformance test extends the same technique from "everything goes
  through the emitter" to "everything the emitter is given is
  declared".

## Decisions this plan makes

### The registry is data in its own module

`samtal_server/events_schema.py`, importing nothing but the standard
library, imported by `events.py` (the arrows keep pointing downward:
every subsystem imports `events`, `events` imports `events_schema`,
and neither imports a subsystem). The module holds frozen
dataclasses and the declarations:

- `EventField(kind, required=True, tokens=None, nullable=False)`.
  `kind` is a closed enum of the shapes decision 3 allows:
  - `IDENTIFIER`: a trusted configured name (agent, entry, stage,
    path, origin), a non-empty bounded `str` the operator or the
    server chose;
  - `TOKEN`: a `str` from the field's declared `tokens` frozenset;
  - `CLASS_NAME`: an exception or type name, `str`, no spaces;
  - `ID`: a bounded machine form the server minted or normalized
    (session id, MAC, revision string, language code), each with a
    per-field syntax constraint (a MAC matches the canonical MAC
    form, a language code the code form), not a generic
    "bounded string";
  - `DESCRIPTOR`: a far-side-supplied string retained
    deliberately, lawful only where its decision site already
    sanitizes it (bounded in length, stripped of unprintables) and
    declared with an explicit per-field maximum length and
    character constraint that validation enforces again at emit.
    The kind stands on the content-and-telemetry ADR's 2026-08-17
    amendment (Rafael's decision, main@744acef): bounded
    device-descriptor metadata, what a device says about itself at
    check-in, is metadata the events may carry once bounded, while
    conversation-derived text stays banned without exception. The
    kind is narrow by construction: M1's provenance inventory
    assigns it ONLY where the site's normalization cannot be
    tightened to an `ID` syntax without changing what the surface
    deliberately retains (`board` is the known case); a field
    whose lawful values already fit
    a syntactic form (`client` when it is a UUID, `firmware` when
    it is a version) is declared `ID` with that form IF its
    decision site normalizes to it, and otherwise stays
    `DESCRIPTOR` with the site's real bounds, because a registry
    that claims a tighter form than the site guarantees would turn
    lawful production traffic into violations. Retaining these
    fields at all is a recorded product decision this issue cannot
    re-decide; what the registry adds is honesty about which
    fields carry far-side bytes rather than laundering them as
    identifiers, plus a sentinel proof of the boundary: M2 plants
    a credential-shaped but syntactically admissible value in each
    `DESCRIPTOR` field and asserts it appears in exactly its own
    declared field of its own event's record and NOWHERE else (no
    other field, no complaint, no exception text, no other tap
    surface).
    M1 inventories every string field by provenance (operator
    configuration, server-minted, far-side sanitized) and assigns
    the kind from the inventory; a far-side string whose decision
    site does NOT sanitize it is a real finding, fixed at that
    decision site in M1, and two such sites are already proven
    rather than plausible: `ota.py`'s `reported_board` and
    `reported_version` only strip whitespace before `ota_check`
    renders them in fields AND message arguments, and
    `session_open` renders an unbounded raw Client-Id in its
    sentence and `client` field. The fix is an event-only
    normalized copy covering BOTH the payload field and the
    message argument (what the site answers elsewhere, the OTA
    response above all, is untouched); a bounds-and-strip on
    adversarial input changes nothing for the lawful values the
    pin suites plant, so the pins stay untouched, and each fix is
    its own commit named in the implementation doc. The
    adversarial tests assert the sentinel's placement across
    `record.args`, the rendered sentence, both formats, the
    complaint, the exception, and an attached tap, not the payload
    field alone. This is the one deliberate
    narrowing of the "no emit site changes" rule, and it is
    confined to sanitization of values the taxonomy cannot
    otherwise admit;
  - `INT`, `FLOAT` (an `INT` value satisfies `FLOAT`, since sites
    pass round numbers where a measure is integral; `bool` is
    rejected for both, checked first, because `True` is an `int`
    to `isinstance` and a boolean in a duration field is a bug;
    floats must be finite, since NaN and infinities are not
    measurements and JSON cannot carry them faithfully), `BOOL`;
  - `COUNT`: an `int >= 0`, for the fields whose meaning is "how
    many" (`sentences`, `tools`, and kin; `agents` is NOT one of
    these, see the list kinds);
  - `IDENTIFIER_LIST` and `ID_LIST`: a list whose every element
    satisfies the element kind, with the same per-field bounds.
    The surface carries them today: `ota_check` and
    `activation_complete` carry `agents` (configured agent names)
    and `ota_check` carries `unloaded` (the same), `session_open`
    carries `agents`, and `capture_pruned` and
    `conversations_pruned` carry `sessions`. Which `sessions` and
    `agents` fields are lists and which are counts is read from
    each site in M1 rather than assumed; the declaration follows
    the site;
  - `SOURCES`: the one structured kind, for `prompt_assembled`'s
    `sources`: a mapping whose keys follow the closed provenance
    grammar of the KNOW-HOW half only (`persona`,
    `fragment:<name>`, `instructions:<entry>`,
    `server_instructions:<entry>`,
    `server_prompt:<entry>:<position>`, with `<name>` and
    `<entry>` configured identifiers and `<position>` a positive
    integer) and whose values are counts. `memory` is NOT in the
    grammar: `prompt_assembled` deliberately reports the cached
    know-how half and excludes the per-round memory read, so a
    `memory` key is a violation like any unknown prefix. M1
    derives the allowed forms from the know-how builders rather
    than from the provenance vocabulary at large, and M2's tests
    cover an empty mapping, a populated one, every allowed form,
    and the negative cases `memory` and an unknown prefix.
  `nullable` exists because the session scope's base `device` is
  `None` until the MAC is normalized, and only for fields like it.
- `EventSpec(name, variants)`, where each `EventVariant` declares
  one legal emission shape completely: `channel`, `level`,
  `message` (the exact registry-owned template string), `args`
  (the argument tuple as per-position kinds), and `fields` (this
  variant's payload shape). Variants are the unit because one spec
  with one flat field table cannot describe the surface that
  exists: `session_rejected` is emitted with three different
  arities across four templates on two channels, `ota_check` with
  three arities, `mcp_reload`'s applied and refused answers carry
  mutually exclusive field sets, and several events change fields
  with level. A variant is exactly what one emit site (or one
  branch of one) produces, so the pin suites, which already pin
  per-site, are the variant inventory's cross-check. Conditional
  field presence (`duration_ms` on the four failing `mcp_down`
  reasons, `speech_ms` when the endpointer held) is expressed by
  variants rather than by per-field optionality wherever the
  condition follows the site; `required=False` remains only for
  genuinely value-dependent presence inside one site
  (`language` on `heard` when the engine detected).
  The channel encodes scope: `samtal_server.session` is the
  session scope, a module `__name__` a server scope, and
  `session_rejected` declares variants on both
  `samtal_server.session` and `samtal_server.ws`. Validation
  matches the emission against the event's variants (the emitting
  object's actual channel included; `SessionEvents` emits on the
  session channel, a `ServerEvents` carries its channel by
  construction), and an emission matching no variant is a
  violation naming the failing dimension. The conformance test
  ties each module's `ServerEvents(__name__)` to the variants
  declared on that channel, so an event emitted from the wrong
  module fails even with lawful fields. Levels are per-variant
  because they are part of the compatibility surface. A variant's
  `fields` covers the full payload the log tap receives, base
  fields included (`event`, and on the session channel `session`
  and `device`), so one validation reads the finished payload and
  channel differences fall out of the declarations rather than
  special cases. M1's inventory therefore reads every emit site's
  template and every argument position, not only its field
  keywords. Arguments have their own `ArgSpec` taxonomy beside the
  field kinds, because the pinned surface carries shapes no field
  does: `PATHLIKE` (a trusted configured path, `Path` or `str`,
  which the capture events render), `COMPOSED` (a formatted
  fragment of identifiers whose grammar and producing builder the
  declaration names, validated against that grammar: the quoted
  tool name, the from-entry fragment, the provider-location
  fragment, the comma-joined agent list), and the shared numeric,
  identifier, token, and class-name kinds; `IDENTIFIER` is not
  widened to punctuated strings. M2's tests cover the pinned
  `Path` and composed-fragment examples and adversarial failures
  of each grammar.
- `REGISTRY: dict[str, EventSpec]` with all 57 events, grouped and
  commented by subsystem in the order of the README table, each
  declaration citing nothing: the fields and tokens are the
  declaration, and the conformance test is what ties them to the
  sites.

The registry declares the tap-contract surface only. `vad` and
`dropped` are capture side channels outside the tap contract
(`events.py` says why), and they stay outside the registry.

### Enforcement lives in `_emit`, and the mode is an env switch

Both emitters validate in `_emit` before dispatch, in two steps
whose order is load-bearing. First the caller-supplied fields are
checked BEFORE the base-field merge, and a session caller
supplying any base key (`event`, `session`, `device`) is itself a
violation: today `**fields` merges after the base fields, so a
spread carrying `session=` would silently replace the emitter's
own identity and still typecheck, which is spoofing the machinery
must refuse rather than validate. Only then is the payload built
(base fields from the emitter, never from the caller) and
validated whole against the event's variants: channel, level, the
`message` string compared byte-for-byte against the variant's
registry-owned template (the template is the retained sentence's
skeleton, so an emit whose message is not a declared template
fails even when every field is lawful: without this,
`events.info(secret, event="heard", ...)` would pass), the
argument tuple against the variant's per-position kinds, no
declared-required field missing, no undeclared field present,
every field matching its kind, every `TOKEN` value in its set. On server channels
`session` and `device` remain ordinary declarable event fields,
since a server emitter has no base identity to protect. M2's
tests include collision cases planting sentinel-shaped replacement
values for each base key. The message arguments are inside the machinery, not
beside it: `Emission.args` reaches every tap and the formatter
renders them, so each spec also declares its argument tuple (arity
and per-position kind, drawn from the same taxonomy), transcribed
in M1 while reading each site, and validation covers the tuple the
way it covers fields. No argument on the surface falls outside the taxonomy: the one
that did, the recovered transcript `asr_prompt_echo` rendered into
its sentence, is removed by issue #165, implemented as a
prerequisite to this issue, so this plan's baseline is post-#165
main and the registry declares the narrowed sentence like any
other. No grandfather machinery exists; decision 5's
pins-unmodified contract holds without exception against that
baseline.

Violations become one `EventSchemaViolation` value, and its
diagnostics render REGISTRY-OWNED identifiers only. A declared
event or field name may be named, because the registry owns it; an
undeclared event name or an undeclared spread key is itself a
caller-supplied string (a dict built from far-side data puts
far-side bytes in its keys), so unknowns are reported as fixed
violation codes plus counts (an undeclared-event code, an
undeclared-field count), never by the rejected name. Neither a
field's value nor an unknown name reaches the strict exception's
`str`, `repr`, or `args`, the forgiving complaint, either log
format, or any attached tap. In forgiving mode an undeclared event
does not retain its raw name either: the payload's `event` becomes
the fixed token `schema_violation` beside the base fields, so the
line survives without laundering the rejected name into the
retained log. M2's sentinel proofs cover five shapes, a
credential-shaped value in a wrong-kind field, a credential-shaped
undeclared event name, a credential-shaped undeclared spread key,
a credential-shaped MESSAGE (an undeclared template carrying the
sentinel as the sentence itself), and a credential-shaped argument
in a declared template's slot of the wrong kind, each asserted
absent from all the surfaces above, in both modes.

- **Strict** (the module default): `_emit` raises
  `EventSchemaError`. This is what every context that never runs
  the server entrypoint gets: the pytest lanes, CI, an import, a
  REPL. The lanes additionally pin it explicitly in
  `tests/conftest.py` beside the existing import-time settings, so
  an ambient variable on a CI runner cannot quietly relax them.
- **Forgiving**: the offending fields are dropped from the payload
  (an undeclared event keeps only its base fields), and an invalid
  emission's human sentence is replaced wholesale by a fixed safe
  message with no caller-supplied arguments, because dropping a
  payload field cannot un-render the same value from `args`; a
  valid emission keeps its own sentence untouched. The emit
  proceeds so the operator still gets a line and the taps still
  get the event, and one plain ERROR sentence on the emitter's
  own channel reports the violation (ERROR rather than WARNING
  because `log_level` admits roots above WARNING and a complaint
  that vanishes under an ERROR root is no complaint; M2 tests the
  complaint's survival under a root of ERROR; a root of CRITICAL
  suppresses it along with `provider_failed` and every other
  ERROR-class diagnostic, that operator's explicit choice,
  recorded here rather than engineered around with a side
  channel). Not an event itself, for the
  same reason a tap failure's report is not one: a complaint that
  went back through validation could recurse. The recovery is a
  deterministic ALGORITHM, not a per-class list: variant selection
  uses registry-owned dimensions only (the emitter's channel
  first, then the declared templates, then level); a unique match
  REBUILDS the payload field by field against that variant,
  keeping only fields that validate and dropping every offender
  (never a fail-fast that drops one and retains the next), and
  ANY invalid emission has its message and args replaced by the
  fixed safe sentence; no unique match, including a template the
  registry does not know, degrades to the fresh base-only
  `schema_violation` emission. Multiple simultaneous violations
  therefore have one defined outcome, reached the same way every
  time. Behind the matrix sits a LAST-RESORT GUARD:
  in forgiving mode the WHOLE enforcement-and-recovery path
  (candidate selection, validation, rebuild, and matrix
  application alike) runs under one `try/except`, so a bug
  anywhere inside it cannot raise on a reply path. The guard does not degrade the caller's
  payload, because the caller's payload is exactly what could not
  be judged: it constructs a FRESH emission from whole cloth, the
  fixed event token `schema_violation`, the emitter's own trusted
  base identity (`session` and `device` on the session channel,
  nothing else), the fixed safe sentence, and an empty argument
  tuple, so a hostile event name, key, value, message or argument
  in the original call reaches nothing. `schema_violation` is
  itself a declared registry event, the one INTERNAL event beside
  the 57 production-source events (the registry's own counts and
  every document say 57 plus one): fixed at ERROR, one variant per
  channel across `samtal_server.session` and all 13 server
  channels, a fixed template, no arguments, and the channel's base
  fields, nothing else. It has no ordinary emit site for the
  conformance walk to find, so the walk exempts it by name the way
  the extra= guard exempts `events.py`, and its own test asserts
  the last-resort guard is its only producer; the generated
  reference and the README index carry it like any other event,
  marked internal. M2 tests the guard with a
  matrix, an injected validator raising a sentinel-bearing
  exception combined with a hostile event name, a hostile key, a
  hostile value, a hostile message, and hostile args, asserting
  the reply survives and no sentinel appears on any surface; and
  one non-injected combined-violation sentinel emits hostile key,
  value, message and args together through the ordinary forgiving
  path, proving the algorithm's one defined outcome without any
  fault injection.
- The switch is `SAMTAL_EVENTS_ENFORCEMENT` (`strict` or
  `forgiving`), held in a module flag with a setter, and it is
  APPLICATION CONSTRUCTION that resolves it: `create_app` invokes
  the resolver before building anything that emits, because a
  production process may import `create_app` and serve it under an
  ASGI runner without ever running `main()`, and the plan's own
  rule is that any running server is a deployment. `main()` ALSO
  resolves, after it has loaded `.env` and after the `config`,
  `conversations`, and (from M3) `events` subcommand early exits
  (so an invalid value of a server-only variable cannot block a
  recovery command or schema generation; a subprocess test proves
  `events reference` succeeds under an invalid ambient value),
  which is
  what lets the documented dotenv layer set the variable; an
  import-time read could honor neither (`main.py` imports `app`
  and therefore `events` before `main()` runs). The lanes set
  strict in the environment before any app construction, so a
  test-built app stays strict. Resolution, in both resolvers:
  - `strict` or `forgiving`: as written;
  - unset: `forgiving`, because a running server is a deployment
    whatever artifact it runs from and however it was launched,
    and a wheel, source, or ASGI-runner deployment must not be one
    telemetry bug away from losing a reply just because it is not
    the container;
  - anything else: construction refuses (a raise from `create_app`
    or a startup refusal from `main()`), naming the variable and
    the two values. A misspelled relaxation must fail at boot, not
    at the first live violation.
  The container image still sets
  `SAMTAL_EVENTS_ENFORCEMENT=forgiving` in its `ENV` block beside
  `SAMTAL_SERVER__LOG_FORMAT=json`: redundant with the entrypoint
  default on purpose, so the production posture is visible in the
  artifact rather than implied. This is deliberately not a
  `ServerConfig` field: `events.py` sits below `config/` in the
  import graph by design, the switch governs telemetry machinery
  rather than server behavior, and #139 is about to migrate the
  operator schema this would otherwise join. The README Logging
  section documents the variable in one paragraph.
- The issue's "strict in development" is read as the development
  FEEDBACK LOOP: the lanes, imports, and tooling, which the module
  default keeps strict. A locally launched server process goes
  through `main()` and is treated as a deployment; a developer who
  wants a loud local server sets the variable to `strict`, and the
  README paragraph says so. This interpretation is recorded here
  because the alternative (strict for every non-container server)
  is exactly the reply-loss the issue's own decision 2 forbids.
- M2 exercises the real entrypoint in subprocesses: unset,
  `forgiving`, `strict`, an unknown value refusing startup, and a
  `.env` file carrying the variable, so import order and ambient
  environment are covered rather than assumed.

The emitter-mechanics tests need a seam, because strict enforcement
would otherwise reject them by design: `test_events.py` emits
synthetic names (`one` through `four`), undeclared fields, and a
synthetic channel on purpose, since it tests dispatch, taps, copy
semantics, and ordering rather than the production surface. The
validator therefore reads the registry through injectable module
state, and a test-scoped context manager installs a scratch
registry (declaring the synthetic events) for exactly these tests.
M2 updates `test_events.py` to use the seam; it is a mechanics
suite, not one of the two characterization pin files, and those two
stay byte-unchanged. The full lanes still run every PRODUCTION
emission under strict enforcement, which is the point.

Validation is written in explicit conditions that raise; it
contains no `assert` statement, because `python -O` strips
assertions and an optimized production process silently losing its
enforcement is exactly the quiet failure this issue exists to end.
M2 proves it: a subprocess running under `-O` emits an invalid
event in strict mode and the test asserts it still raises.

Validation runs per emit on the event path only (never per frame:
the per-frame `vad`/`dropped` samples are outside the tap contract
and untouched). The cost is a dict walk over a handful of keys per
event, and the strict lane pays it in every existing test, which is
itself the conformance proof running continuously.

### The conformance test makes the inventory permanent

`tests/unit/test_event_schema_conformance.py`, in the style of
`test_event_surface_guard.py`:

- An AST walk over `samtal_server/` collects every emitter call
  with an `event=` literal and keys conformance BY SOURCE CALL:
  each of the 81 sites (branch by branch where one site emits
  more than one shape) must map to exactly one declared variant,
  matching the module's channel, the method-derived level, the
  byte-exact template, the positional arity and argument kinds,
  the statically-named fields, and the named spread inventory; a
  site matching no variant, or two, fails naming the site.
- Coverage is TWO-WAY, because containment alone would let a
  surplus declared field sit unused as a permanent enlargement of
  the allowlist: every declared non-base field must be evidenced,
  either by a static keyword at some emit site or by an explicit
  spread-inventory entry naming the builder (`_echo_fields`,
  `language_fields`, `provider_fields`, and the rest of the nine)
  whose AST the test parses to extract the keys it can produce and
  asserts they match the entry; and every declared TOKEN SET must
  map to its actual decision site: the conformance inventory
  names, per event and field, the producing function or constant,
  module-qualified and crossing modules where production does
  (`activation_not_offered.reason` is produced by `onboarding.py`'s
  refusal constructors, not by `ota.py` where the emit sits), and
  the test resolves the named object and compares the values it
  can produce with the declared set, so an unrelated literal or a
  docstring cannot satisfy the check. A declared field or token
  nothing evidences fails the test.
- The registry's own coherence is asserted: every `TOKEN` field
  carries a non-empty token set, no other kind carries one, every
  event has at least one level and one channel, base fields are
  declared exactly where the channel requires them, every
  `DESCRIPTOR` carries explicit bounds, every spec declares its
  argument tuple.
- A planted-source test proves the walk sees the shapes it claims
  to see, as the guard's planted tests do.

M1's claim is calibrated to what this proves: the registry is
DECLARED AND STATICALLY CONFORMANT after M1 (every site maps into
it, every declaration is evidenced); "provably complete" belongs
to M2, when strict enforcement runs every production emission the
lanes exercise through the validator.

### The schema reference is generated; the README table is a
### name-checked overview

Round 2 settled the mechanism: parsing prose cells for field-level
exactness is both impossible for `session_rejected` (one row cannot
inhabit two channel tables, two rows fail a duplicate rule) and
incomplete (levels, kinds, requiredness, nullability and tokens
could drift while the parse stayed green). The repository already
owns the right machinery, so M3 uses it:

- **A generated reference document**, the issue's own first
  option: `samtal-server events reference` prints the complete
  schema from the registry, one section per event, every variant
  with its channel, level, template, argument kinds, and field
  table (kind, required, nullable, tokens), exactly as
  `config reference` and `conversations schema` already do for
  their domains. It is committed as `docs/reference/events.md`,
  and the CI workflow gains a drift step diffing the committed
  file against a fresh generation, byte-identical or the build
  fails, alongside the three existing drift checks. Every registry
  property the documentation claims is therefore checked by
  construction: the document IS the registry, rendered.
- **The README prose table becomes a two-column name-and-when
  index**: the `fields` column is REMOVED, because prose field and
  token claims the checker does not parse can go stale while a
  name-level check stays green, and half-checked documentation
  reads as checked. Per-field explanatory prose worth keeping (a
  count never a list, which reasons are intentional) moves into
  registry-owned note strings the generated reference renders, so
  nothing is lost, only relocated to the surface that is checked.
  The index gains the missing events (the 57 production events
  plus the internal recovery event) and a pointer to the generated
  reference for every field and token fact.
  `tests/unit/test_event_docs.py` checks the index at name level:
  every registry event in exactly one row, every row a declared
  event, no duplicates; `session_rejected` is one row whose prose
  names both channels; the section's lead sentence scopes its
  base-field claim to the session channel.

The mutation matrix follows the mechanism: a registry mutation
(field added, token dropped, level changed) must make the CI drift
step fail against the committed reference; a dropped, bogus, or
duplicate README row must fail the name-level test; each observed
and reverted.

### What is deliberately out of scope

- No emit site changes its fields, levels, sentences, or names,
  with the one narrow exception finding 3's resolution defines
  (sanitizing a far-side string at its decision site where the
  taxonomy cannot otherwise admit it, invisible to the pins'
  lawful planted values): the pin suites pass unmodified, and any
  real mismatch the registry work uncovers between the README and
  reality is fixed in the README or recorded as a follow-up
  issue, never by reshaping the surface under this issue.
- The conversation store, capture, and audit surfaces are other
  surfaces; the registry covers the structured events only.
- Exporters (#66/#67) consume the registry later; nothing here
  builds them.
- #141 moves pipeline emit sites and must not run concurrently;
  the batch's one-at-a-time rule already guarantees that.

### Three milestones, three PRs, stacked

1. **M1, the registry exists and is statically conformant**:
   `events_schema.py` with all 57 declarations (the nine spread
   builders read, their key sets and token sets transcribed), the
   conformance test, the registry-coherence tests. No enforcement
   yet: the emitters do not read the registry in M1. The release
   from this merge is behavior-identical EXCEPT where the
   provenance inventory forces a decision-site sanitization fix
   (adversarial-input-only, each its own commit, named in the
   implementation doc), and those fixes normalize EVENT-ONLY
   copies: the value a site emits is bounded without changing what
   the same site answers elsewhere, proven for OTA by an
   adversarial endpoint test asserting the OTA response bytes and
   stored state are unchanged while the emitted field carries the
   sanitized form. The declarations are pinned to reality by the
   conformance test and by review. Like every milestone, M1 lands
   its implementation-doc section (deviations or an explicit
   none), its changelog entry, and its tick-and-link in the same
   change.
2. **M2, the emitters enforce**: two-step validation in both
   `_emit`s (pre-merge caller check, then the variant match
   including template and argument tuple), `EventSchemaError`, the
   forgiving recovery matrix behind the last-resort guard with the
   declared `schema_violation` recovery event, mode resolution in
   `create_app` and `main()`, the Dockerfile `ENV` line, the
   schema seam and the updated mechanics tests, the five-shape
   sentinel matrix and the subprocess proofs, the README Logging
   paragraph on the switch. Both pin suites and the full lanes
   green under strict enforcement is the milestone's core proof.
   M2 lands its implementation-doc section, changelog entry, and
   tick-and-link in the same change.
3. **M3, the reference cannot drift**: the
   `samtal-server events reference` command, the committed
   `docs/reference/events.md`, the CI drift step, the README
   table's missing rows and name-level `test_event_docs.py`,
   mutation proofs per the mechanism, and M3's own
   implementation-doc section, changelog entry, and tick-and-link,
   the same per-milestone duty as M1 and M2 rather than a final
   wrap.

## Files touched

New: `samtal_server/events_schema.py`,
`tests/unit/test_event_schema_conformance.py`,
`tests/unit/test_conversations_event_pins.py` (M1, the
post-baseline characterization pins),
`tests/unit/test_event_docs.py` (M3, name-level),
`docs/reference/events.md` (M3, generated), this plan's
implementation doc.

Modified: `samtal_server/events.py` (M2, validation in the two
`_emit`s and the mode flag), `samtal_server/app.py` (M2,
`create_app` invokes the mode resolver), `samtal_server/main.py`
(M2, resolution after dotenv and the subcommand exits; M3, the
`events reference` subcommand's wiring if it lives there),
`tests/conftest.py` (M2, pinning the lanes strict),
`tests/unit/test_events.py` (M2, the mechanics tests adopt the
schema seam), `Dockerfile` (M2, one `ENV` line),
`.github/workflows/samtal-server.yml` (M3, the drift step),
`samtal-server/README.md` (M2 one paragraph; M3 the missing rows
and the reference pointer), `CHANGELOG.md` (per milestone).

Modified in M1 for decision-site sanitization: `ota.py`
(`reported_board`/`reported_version` bounding) and
`device/session.py` (the `session_open` client rendering), the two
proven sites, plus whatever else the provenance inventory turns
up, each fix its own adversarial-input-only commit under the
event-only-copy rule in the milestone section.

Untouched on purpose: every emit site's fields, levels, sentences
and names (up to the conditional sanitization above), both pin
suites, `test_event_surface_guard.py`, `logs.py`, the capture side
channels, `config/` (no new `ServerConfig` field, so the #144
example-config pin is unaffected).

## Tests

- M1: the conformance test (two-way AST coverage, planted-source
  proof, registry coherence including argument tuples and
  descriptor bounds) and the post-baseline characterization pins.
  The strict runtime lane does not exist yet.
- M2: strict mode raises on each violation class (undeclared
  event, undeclared field, wrong kind, unlisted token, missing
  required field, wrong level, wrong channel, base-key collision,
  bad argument tuple); forgiving mode follows the recovery matrix,
  still emits, and complains once, covered per class, plus the
  injected-raising-validator guard proof; the sentinel matrix
  plants credential-shaped values as a wrong-kind field value, an
  undeclared event name, and an undeclared spread key, asserted
  absent from exception str, repr and args, the complaint, both
  log formats, `Emission.args`, and an attached tap; the
  entrypoint subprocess tests cover unset, `forgiving`, `strict`,
  an unknown value refusing startup, a `.env`-carried value, and
  the `-O` strictness proof; both pin suites unmodified and green
  under strict, which is the standing conformance proof.
- M3: the generated reference byte-checked by the CI drift step,
  the README name-level checks, and their mutation proofs per the
  mechanism.

## Verification

Per milestone, from `samtal-server/`: `uv run ruff check .`,
`uv run pytest tests/unit -q`, `uv run pytest tests/integration -q`,
collected-count recorded before and after (never lower; rises only
by the new tests), and the pin-suite files byte-unchanged
(`git diff --stat` on the two files is empty). For M2, the sentinel
and both-modes tests named above; for M3, the mutation matrix
recorded in the PR body with observed failure output per branch.
`PYTHONDONTWRITEBYTECODE=1` outside pytest.

## Risks and mitigations

- **The registry mis-describes a spread site and strict mode breaks
  a lane.** The spread builders are read in M1 and the declarations
  reviewed against the pin suites; M2's full-lane run under strict
  enforcement is the backstop, and a failure there is a wrong
  declaration to fix in M2, never a pin to weaken.
- **A dynamic or conditional field the inventory missed.** The
  conformance test only sees static keywords; the strict lanes see
  everything the tests exercise. A field only production reaches
  (an error path no test drives) would surface in forgiving mode as
  a complaint line, not an outage; the complaint names the declared
  event and reports the unknown field only as a fixed violation
  code and count (an undeclared field name is caller-supplied
  bytes under this plan's own model), and the key's identity is
  recovered by reproducing under strict mode, where the
  declaration is one fix away. This failure mode is
  documented in the implementation doc rather than hidden.
- **Import-order emissions before the entrypoint resolves the
  mode.** The module default is strict, so an emission between
  import and `main()`'s resolution would be validated strictly in
  a server process. Those emissions are module-level constructions
  only (no event fires at import today, which the conformance walk
  confirms); the resolution happens in `main()` before `create_app`
  emits anything, and the subprocess tests cover the real order.
- **Validation cost on the reply path.** A per-event dict walk over
  fewer than a dozen keys; the events fire per decision, not per
  frame. No caching machinery unless a lane shows a need, which
  none is expected to.
- **The README check is brittle against prose edits.** The index
  carries names and prose only; a wording edit passes, a
  dropped, added, or duplicated row fails, and every schema fact
  lives in the generated reference the CI drift step holds
  byte-identical, which is the drift the checks exist to catch.
- **`session_rejected` on two channels.** Declared once with both
  channels and the union payload validated per emit; the channel
  gates which emitter may say it, and the base-field difference is
  encoded per channel. If encoding one spec for two channels turns
  out muddier than two, the milestone may split the declaration
  and record the deviation.

## Milestones

- [ ] M1: the registry exists and is statically conformant:
      `samtal_server/events_schema.py` declares all 57 events with
      fields, kinds, levels, scopes, and token sets;
      `test_event_schema_conformance.py` ties every static emit
      site to it two ways and proves its own walk on planted
      source; `test_conversations_event_pins.py` pins the
      post-baseline paths exactly; the string-field provenance
      inventory is recorded; the milestone's implementation-doc
      section, changelog entry, and tick land in the same change;
      no enforcement wired; both lanes green, pin suites
      untouched.
- [ ] M2: the emitters enforce at emit time: caller fields checked
      before the base-field merge, strict raises
      `EventSchemaError`, forgiving follows the recovery matrix
      behind the last-resort guard with the fixed safe sentence,
      diagnostics render registry-owned names only,
      `SAMTAL_EVENTS_ENFORCEMENT` is resolved by the entrypoint
      after dotenv (unset forgiving there, unknown refuses
      startup) with the module default strict and conftest pinning
      the lanes, the image sets forgiving, the mechanics tests
      adopt the schema seam, the sentinel matrix and subprocess
      proofs pass, both pin suites pass unmodified under strict
      enforcement; implementation-doc section, changelog entry,
      and tick land in the same change.
- [ ] M3: the schema reference cannot drift:
      `samtal-server events reference` generates
      `docs/reference/events.md`, the CI drift step holds it
      byte-identical, the README table gains its missing rows and
      the name-level `test_event_docs.py`, the mutation matrix is
      recorded; implementation-doc section, changelog entry
      closing the issue, and tick land in the same change.

## Plan review round

External review of commit b436c55 by codex 0.147.0 (model
gpt-5.6-sol), 2026-08-16, prompted with this plan, the issue body,
the emitter, the emit sites and spread builders, both pin suites,
the guard, the README table, the Dockerfile, and the prior plans.
Findings as received, condensed but faithful:

1. **P1: the registry does not declare or enforce channels.** The
   issue requires name, level, channel, fields; `EventSpec` had
   scope but no channel, and nothing tied a module's
   `ServerEvents(__name__)` to its events. `session_rejected`
   needs `samtal_server.session` and `samtal_server.ws` on their
   respective scopes.

   *Resolution*: accepted. `EventSpec` now declares `channels`, the
   frozenset of logger channels the event may ride, which is both
   the issue's channel requirement and the scope encoding;
   validation compares the emitter's actual channel, the
   conformance test ties each `ServerEvents(__name__)` module to
   its declared events, and `session_rejected` declares both its
   channels. Amended in the registry section.

2. **P1: the field-kind taxonomy cannot encode the current
   payloads.** No list kind, and `agents` is not a count:
   `ota_check` carries `agents` and `unloaded` lists,
   `session_open` and `activation_complete` carry agent lists,
   `capture_pruned` a session-id list; `prompt_assembled.sources`
   is a mapping keyed by dynamic provenance strings
   (`fragment:<name>` and kin), not a fixed sub-shape.

   *Resolution*: accepted. The taxonomy gains `IDENTIFIER_LIST`
   and `ID_LIST` kinds with per-element validation, the wrongly
   cited count examples are corrected with the list-or-count
   choice read from each site in M1, and `sources` is declared as
   a mapping validated against the closed provenance grammar with
   count values, tested empty, populated, and per form. Amended in
   the registry section.

3. **P1: the proposed string kinds bless unbounded far-side values
   as trusted metadata.** `ota_check.client`, `board`, `firmware`
   come from request headers and JSON, bounded only by strip and
   nonemptiness at their sites; a generic IDENTIFIER/ID kind would
   accept a credential-shaped string. Inventory string fields by
   provenance, give field-specific length and syntax constraints,
   and normalize untrusted values at their decision sites, which
   requires relaxing the plan's blanket no-emit-site-change rule
   where the surface cannot satisfy the settled taxonomy.

   *Resolution*: accepted. The taxonomy gains `DESCRIPTOR` for
   far-side strings retained deliberately, lawful only where the
   decision site sanitizes them and enforced again at emit with
   per-field length and character constraints; `ID` gains
   per-field syntax constraints; M1 inventories every string field
   by provenance; and the no-emit-site-change rule is narrowed
   exactly once: a far-side string whose site does not sanitize it
   is fixed at that site, which leaves the pin suites' lawful
   planted values untouched. Amended in the registry section and
   the out-of-scope section.

4. **P1: payload-only validation leaves message and args outside
   the no-leak machinery.** `Emission.args` reaches taps and the
   formatter renders them; `asr_prompt_echo`'s recovered branch
   renders the recovered transcript into the message, and its pin
   preserves it. Dropping a payload field does not remove the same
   value from args; forgiving mode must not re-render the original
   message for an invalid emission; sentinels must inspect args
   and attached taps.

   *Resolution*: accepted, with the narrowing routed through the
   contract rather than around it. Each spec now declares its
   argument tuple (arity and per-position kind) and validation
   covers it; an invalid emission's sentence is replaced wholesale
   by a fixed safe message in forgiving mode; the sentinel tests
   inspect exception text, complaint, both formats,
   `Emission.args` and an attached tap. The one argument the
   taxonomy cannot admit, the recovered transcript in
   `asr_prompt_echo`, is a visible single-entry grandfather citing
   a follow-up narrowing issue M1 files, because deleting it
   inside #155 would break the pin suites the issue declares
   unmodifiable. Amended in the enforcement section.

5. **P1: validating after base-field merging permits session
   identity spoofing.** `**fields` can overwrite `session` and
   `device` and still typecheck. Validate caller fields before
   merging and forbid session callers supplying base keys.

   *Resolution*: accepted. Validation is now two ordered steps:
   caller fields checked pre-merge with base keys forbidden on the
   session channel, then the emitter-built payload validated
   whole; server channels keep `session` and `device` as ordinary
   declarable fields; collision tests plant sentinel-shaped
   replacements per base key. Amended in the enforcement section.

6. **P1: the violation diagnostics can leak rejected names.**
   Undeclared event names and spread keys are caller-supplied
   strings; complaints and exceptions must render registry-owned
   identifiers, fixed codes and counts only, and an undeclared
   event must not retain its raw name. Add sentinels for an
   undeclared event name, an undeclared spread key, and a
   wrong-kind value, asserted absent from exception str, repr and
   args, complaint records, both formats, and attached taps.

   *Resolution*: accepted. Diagnostics render registry-owned
   identifiers only; unknown names become fixed codes and counts;
   a forgiving undeclared event is re-labeled with the fixed
   `schema_violation` token instead of keeping its raw name; and
   the sentinel matrix covers the three shapes across all six
   surfaces the finding lists. Amended in the enforcement section.

7. **P1: the enforcement-mode seam initializes too early and does
   not classify production safely.** `main()` loads `.env` after
   `events.py` is imported, so an import-time read cannot honor
   the documented dotenv layer; a strict default makes a source or
   wheel production deployment able to lose a reply; an unknown
   value silently meaning strict defers a typo's failure to a live
   emit. Initialize after dotenv, default unclassified server runs
   to forgiving, force strict in pytest and CI, keep the image
   explicit, refuse unknown values at startup, and test the real
   entrypoint in subprocesses.

   *Resolution*: accepted in full. The mode is resolved by
   `main()` after dotenv loading (unset means forgiving there,
   unknown refuses startup naming the variable), the module
   default outside the entrypoint stays strict and the lanes pin
   it in conftest, the image keeps its explicit ENV line as
   visible posture, and M2 tests the real entrypoint in
   subprocesses across all five states. The plan records the
   development-loop interpretation of the issue's strictness
   wording, with the reply-loss argument as the reason.

8. **P1: M2 cannot keep the unit lane green without modifying
   tests the plan leaves untouched.** `test_events.py` emits
   undeclared names, undeclared fields, incomplete payloads, and a
   synthetic channel by design; strict enforcement rejects them,
   and the file is absent from the touched list. Update it through
   an explicit schema seam; only the two pin files are the
   byte-unchanged contract.

   *Resolution*: accepted. The validator reads the registry through
   injectable module state with a test-scoped context manager
   installing a scratch registry; M2 updates `test_events.py` to
   declare its synthetic events through the seam, the file joins
   the touched list, and the two pin files remain the only
   byte-unchanged contract. Amended in the enforcement section and
   the files list.

9. **P1: the README check proves containment, not agreement.** The
   check rejected nothing extra, the table holds 34 event rows so
   23 events are missing, the lead sentence's base-field claim is
   false for server events, and free backticked prose is not a
   machine representation of field and token sets. Give rows a
   mechanically delimited field list with per-field tokens (or
   generate an appendix), check equality both ways plus duplicate
   rows and scope-aware base fields, add bogus-field and
   bogus-token mutations, and budget the missing rows.

   *Resolution*: accepted. M3 now splits the table by channel with
   truthful per-section base-field sentences, adds the 23 missing
   rows as its budgeted writing task, gives the fields cell a
   mechanically delimited grammar (comma-separated fields, one
   parenthesized annotation each, tokens enumerated inside), and
   the check asserts exact equality both ways at every layer, with
   duplicate-row, bogus-field and bogus-token mutations joining
   the matrix. Amended in the README-check section.

10. **P2: M1's conformance test cannot prove exactness.** Call-site
    containment does not reject a surplus declared field, and
    spreads are invisible; either require two-way coverage
    (declared fields evidenced statically or by inventoried
    spread-builder branches, tokens mapped to decision-site
    literals) or stop calling M1 provably complete.

    *Resolution*: accepted, both halves. The conformance test is
    now two-way (declared fields evidenced by a static keyword or
    a parsed spread-builder inventory entry; declared tokens found
    as decision-site literals; anything unevidenced fails), and
    M1's milestone line is recalibrated to "declared and
    statically conformant", with "provably complete" reserved for
    M2's strict lanes. Amended in the conformance section and the
    milestone list.

11. **P2: the stated pin evidence is stale.** The `conversations_*`
    events appear in neither pin file; their five paths are only
    loosely asserted. Add exact pre-enforcement characterization
    coverage for paths added since the pin files' baseline,
    without modifying those two files.

    *Resolution*: accepted. The evidence section now states the
    pin files' real baseline instead of claiming currency, and M1
    adds `test_conversations_event_pins.py` in the contract files'
    exact style, covering the five store paths plus whatever a
    diff of pinned names against the inventory turns up, committed
    green before enforcement. Amended in the evidence section, the
    files list, and the M1 milestone.

12. **P2: forgiving recovery is undefined where no field can be
    dropped.** Missing required field, wrong level, wrong scope
    have no defined result, and a validator bug can still escape
    through a reply path. Provide a recovery matrix per violation
    class and a final forgiving-mode guard around validation,
    tested with an injected validator raising a sentinel.

    *Resolution*: accepted. The forgiving path is now a recovery
    matrix defined per violation class (drop the field, re-label
    the event, emit-what-was-given for a missing required field,
    keep the caller's level and the emitter's channel), and a
    last-resort guard wraps the whole validation in forgiving mode
    so a validator bug degrades the emission instead of raising on
    a reply path, tested with an injected raising validator
    carrying a sentinel. Amended in the enforcement section.

13. **P2: type enforcement lacks optimized-mode and edge
    semantics.** Booleans are `int` subclasses, nonfinite floats
    are unaddressed, and `assert`-based validation dies under
    `python -O`. Use explicit conditions and raises, reject bools
    for numeric kinds, require finiteness, and prove strictness
    under `-O` in a subprocess.

    *Resolution*: accepted. Booleans are rejected for numeric
    kinds with the isinstance-order stated, floats must be finite,
    validation uses explicit raising conditions with no assert
    statement, and M2 carries the `-O` subprocess proof. Amended
    in the registry and enforcement sections.

Verdict: not ready.

## Plan review round 2

Second external review, of the branch at 0e64ce8 (the thirteen
round-1 amendments included), codex 0.147.0 (model gpt-5.6-sol),
2026-08-16. The round confirmed round-1 resolutions 1, 5, 8, 11 and
13, confirmed the 81/57/13 inventory independently, and returned
eleven findings. As received, condensed but faithful:

1. **P1: `DESCRIPTOR` defeats the no-leak guarantee.** Length and
   printability still admit a credential-shaped value unchanged,
   contradicting the ADR's no-far-side-bytes rule, and the
   sentinel matrix never plants a valid descriptor. Remove the
   kind; make each value a normalized `ID` or closed token, or
   remove it in a separately accepted narrowing.

   *Resolution*: partially adopted. Adopted: `ID`-with-syntax
   wherever the decision site normalizes to a form (decided
   per field by M1's provenance inventory), and the strengthened
   sentinel planting a syntactically admissible credential shape
   in every remaining `DESCRIPTOR` field, asserting containment to
   exactly its own declared field. Rejected with reasons: removing
   the kind or the fields. Retaining bounded device-reported
   descriptors (`board` above all) is a recorded, reviewed
   decision of the onboarding work, documented in the README, and
   re-deciding it is a product narrowing outside a refactoring
   issue whose contract pins the surface; a registry that cannot
   say "this field deliberately carries sanitized far-side bytes"
   would be less honest than one that can. The ADR's rule is what
   the follow-up narrowing issues enforce surface change by
   surface change; the registry's job here is to describe and
   bound the surface that exists.

2. **P1: the `asr_prompt_echo` grandfather preserves an
   acknowledged far-side leak.** The recovered transcript is user
   speech reaching the rendered log and taps; a grandfather is a
   permanent bypass. Require the narrowing before enforcement and
   resolve the pin-contract conflict explicitly.

   *Resolution*: rejected with reasons, and the conflict resolved
   explicitly as demanded. Narrowing the sentence inside #155
   would edit `test_server_event_pins.py`, one of the two files
   the issue's decision 5 declares unmodified and the batch's
   standing rule says a refactoring PR may not touch by
   definition; between an external recommendation and the
   repository's settled contract, the contract wins. The leak is
   real and is treated as such: M1 files the narrowing issue
   (remove the recovered text from the sentence, a breaking
   surface change with its own changelog entry and pin update,
   under the ADR's authority), and until it lands the registry
   carries the one-entry grandfather the enforcement section now
   bounds by conformance (exactly one site and position, a second
   entry fails, deletion asserted when the issue closes). The
   bypass is neither permanent nor arbitrary; it is the honest
   encoding of a surface fact this issue is contractually barred
   from changing.

3. **P1: message templates remain an unchecked leak path.** The
   registry declares fields and argument tuples but not the
   `message` string; `events.info(secret, event="heard", ...)`
   passes validation. Declare each legal emission's exact
   registry-owned template and compare before dispatch, with
   direct-message sentinels in both modes.

   *Resolution*: accepted. Each variant declares its exact
   template, validation compares the message byte-for-byte before
   dispatch, and the sentinel matrix grows to five shapes
   including a sentinel-as-message and a sentinel argument, both
   modes. Amended in the enforcement section.

4. **P1: one spec and one argument tuple cannot describe the
   surface.** `session_rejected` has arities 1 to 3 across four
   templates, `ota_check` 3 to 5, `mcp_reload` mutually exclusive
   applied and refused shapes; onboarding provenance and
   comma-joined class-name arguments also fall outside the
   taxonomy. The registry needs per-variant declarations keyed by
   channel, level, template, argument tuple, and field shape, and
   M1 must inventory every argument position.

   *Resolution*: accepted. The registry is now variant-keyed:
   `EventSpec` holds `EventVariant`s each declaring channel,
   level, exact message template, per-position argument kinds, and
   this variant's field shape, with conditional presence expressed
   by variants where it follows the site and by optionality only
   where it is value-dependent within one site. M1's inventory
   reads templates and argument positions site by site, with
   derived-argument builders named. Amended in the registry
   section; the grandfather claim about "the one argument outside
   the taxonomy" is superseded by that inventory, whose result is
   recorded in the implementation doc.

5. **P1: last-resort recovery can retain a hostile event name, and
   `schema_violation` is outside the declared registry.** Recovery
   must build a fresh payload with the fixed event, trusted bases,
   fixed message, empty args; the recovery event must itself be
   declared and covered, or demoted to a non-event complaint; test
   a throwing validator combined with hostile name, key, value,
   message and args.

   *Resolution*: accepted. The guard now constructs a fresh
   emission (fixed `schema_violation` event, trusted bases only,
   fixed sentence, empty args) instead of degrading the caller's
   payload; `schema_violation` is declared in the registry with
   that fixed shape and covered by conformance and the generated
   reference; and the guard test is the full hostile matrix.
   Amended in the enforcement section.

6. **P1: entrypoint resolution leaves programmatic servers
   strict.** A production process importing `create_app` under an
   ASGI runner never runs `main()`, so a mismatch can still kill a
   reply. Resolve at application construction (unset forgiving),
   keep `main()` resolving after dotenv, and sequence resolution
   after the `config`/`conversations` early exits.

   *Resolution*: accepted. `create_app` invokes the resolver
   before building anything that emits, unset means forgiving
   there too, the lanes set strict in the environment before app
   construction, `main()` still resolves after dotenv and after
   the `config`/`conversations` early exits, and an unknown value
   refuses at whichever resolver meets it first. Amended in the
   enforcement section.

7. **P1: the README conformance design is impossible for
   `session_rejected` and incomplete.** One-row-per-event cannot
   inhabit two channel tables; level, kind, requiredness and
   nullability can drift while CI stays green. Key rows by event
   and channel or add a parsed channel column; compare every
   property the table claims, or generate a complete schema
   appendix.

   *Resolution*: accepted, via the issue's first option. M3 now
   generates `docs/reference/events.md` from the registry through
   a `samtal-server events reference` command with a CI drift
   step, exactly the machinery `config reference` and
   `conversations schema` already use, so every declared property
   is covered byte-for-byte; the README prose table remains the
   human overview, gains the missing rows, and is checked at name
   level only, which handles `session_rejected` as one row naming
   both channels. The round-1 delimited-grammar design is
   superseded. Amended in the M3 section.

8. **P2: the `SOURCES` grammar admits `memory`, which
   `prompt_assembled` explicitly excludes.** Derive the forms from
   the know-how builders; add negative tests for `memory` and
   unknown prefixes.

   *Resolution*: accepted. `memory` is out of the grammar with the
   exclusion stated, the forms derive from the know-how builders,
   and the negative tests cover `memory` and an unknown prefix.
   Amended in the registry section.

9. **P2: token conformance by module-wide literal occurrence is
   too weak.** A docstring satisfies it, and
   `activation_not_offered`'s reasons are produced in
   `onboarding.py`, a different module. Require per-event,
   per-field decision-site inventories naming the producing
   function or constant, following cross-module producers.

   *Resolution*: accepted. The conformance inventory names the
   producing function or constant per event and field,
   module-qualified and cross-module where production crosses
   modules, and the test resolves the named object and compares
   its producible values with the declaration. Amended in the
   conformance section.

10. **P2: a WARNING complaint disappears at supported log
    levels.** `log_level` admits ERROR and CRITICAL, and such
    deployments retain no complaint. Emit at a level that
    survives, or a dedicated path, and test under ERROR and
    CRITICAL roots.

    *Resolution*: partially adopted. The complaint moves to ERROR
    with a survival test under an ERROR root; a CRITICAL root is
    accepted as suppressing it together with every other
    ERROR-class diagnostic, that operator's explicit choice, in
    preference to a dedicated unfiltered side channel that would
    bypass the logging configuration the deployment chose. Amended
    in the enforcement section.

11. **P2: the sanitization allowance contradicts the
    identical-behavior and untouched-files claims, and sanitizing
    `reported_version` would change the OTA response.** List the
    conditionally modified decision-site files, drop the
    identical-by-construction claim, normalize event-only copies,
    and prove the OTA response unchanged under adversarial input.

    *Resolution*: accepted. The files section now lists the
    conditionally modified decision-site modules, the
    identical-by-construction claim is scoped to what is actually
    identical, sanitization produces event-only normalized copies,
    and an adversarial endpoint test proves the OTA response and
    stored state unchanged. Amended in the milestone and files
    sections.

Verdict: not ready.

## Plan review round 3

Third external review, of the branch at b9fbc28, codex 0.147.0
(model gpt-5.6-sol), 2026-08-17. Seven P1 and four P2, verdict not
ready. Findings 1 and 2 escalated past the plan's authority: the
reviewer refuted the round-2 rejection grounds (the README's
documented retention is older than the accepted ADR and does not
override it), so the conflict went to Rafael, who decided the
hybrid on 2026-08-17: the ADR is amended (bounded device-descriptor
metadata is metadata; conversation-derived text banned without
exception), and the `asr_prompt_echo` narrowing is issue #165,
implemented as a prerequisite to this issue's enforcement. The
amendment is on main (744acef) and #165 is in flight. Findings as
received, condensed but faithful:

1. **P1: `DESCRIPTOR` still violates the settled contract.** The
   README records behavior, not an ADR override; sanitized
   far-side strings remain far-side bytes; remove the kind or
   change the ADR first.

   *Resolution*: resolved by Rafael's ADR amendment (2026-08-17,
   main@744acef): bounded device-descriptor metadata is metadata
   the events may carry, declared and re-enforced by this
   registry. `DESCRIPTOR` now stands on the amended ADR, cited in
   the registry section, with the containment sentinel unchanged.

2. **P1: the grandfather leaves the exact leak #155 must
   refuse.** The pin-file argument holds only as sequencing;
   make the narrowing a prerequisite.

   *Resolution*: accepted via the same decision. Issue #165 (the
   sentence narrowing, its pin, its changelog breaking entry) is
   filed and being implemented ahead of this issue; this plan's
   baseline is post-#165 main, the `GRANDFATHERED_ARGS` machinery
   is deleted from the design, and decision 5's pins-unmodified
   contract holds without exception. Amended throughout the
   enforcement section.

3. **P1: the argument taxonomy cannot encode the pinned
   surface.** `Path` arguments and formatted identifier
   compositions are neither identifiers nor tokens; define an
   argument-only taxonomy.

   *Resolution*: accepted. Arguments get their own `ArgSpec` kinds
   beside the field kinds: `PATHLIKE` (a trusted configured path,
   `Path` or `str`) and `COMPOSED` (a formatted fragment whose
   grammar and producing builder the declaration names, validated
   against that grammar), alongside the shared numeric and
   identifier kinds; `IDENTIFIER` is not widened. Tests cover the
   pinned `Path` and composed-fragment examples and adversarial
   failures. Amended in the registry section.

4. **P1: forgiving recovery is neither total nor deterministic
   for variants.** Wrong templates, bad tuples, multi-violation
   emissions and ambiguous variant selection are undefined; the
   guard covers only the validator.

   *Resolution*: accepted. The recovery is now an algorithm:
   variant selection uses registry-owned dimensions only (channel,
   then declared templates, then level); a unique match rebuilds
   the payload field by field, validating every retained field and
   dropping every offender, and replaces message and args on ANY
   invalid emission; no unique match degrades to the fresh
   base-only `schema_violation` emission; the last-resort guard
   wraps the WHOLE enforcement-and-recovery path, not the
   validator alone. A non-injected combined-violation sentinel
   (hostile key, value, message, and args together) joins the
   matrix. Amended in the enforcement section.

5. **P1: the conformance test is event-wide, not
   variant-exact.** Containment by event name cannot prove each
   of the 81 sites maps to one variant, and pinned-name
   comparison misses an unpinned new path.

   *Resolution*: accepted. Conformance is keyed by source call:
   every one of the 81 sites (branch by branch where one site
   emits variants) must map to exactly one variant, matching
   channel, method-derived level, byte-exact template, arity and
   argument kinds, static fields, and named spread inventory; and
   the pin-coverage comparison is path-based (76 pinned literal
   expectations plus the five conversation paths cover the 81).
   Amended in the conformance section.

6. **P1: the README table is still not provably matched.** Field
   and token claims in prose rows can go stale while the
   name-level check stays green.

   *Resolution*: accepted, second option. The README table drops
   its schema-bearing fields column and becomes a two-column
   name-and-when index pointing at the generated reference for
   every field and token fact; the name-level check then covers
   everything the table still claims. Per-field explanatory prose
   worth keeping moves into registry-owned note strings rendered
   by the generated reference. The stale delimited-grammar
   sentence in the risks section is corrected. Amended in the M3
   section.

7. **P1: `schema_violation` has no coherent count, level,
   channel, or milestone placement.** 57 versus 58, unspecified
   level, 14 possible channels, no emit site for the walk.

   *Resolution*: accepted. The registry is "57 production-source
   events plus one internal recovery event"; `schema_violation`
   is fixed at ERROR, declared with one variant per channel (all
   14), fixed template, no arguments, base fields per channel;
   the conformance walk exempts it from the emit-site rule the
   way the extra= guard exempts `events.py`, with its own test
   asserting the guard is its only producer. Counts, the
   generated reference, the README index, and the milestone
   text now say 57 plus one. Amended in the registry, M2, and M3
   sections.

8. **P2: `events reference` is missing from mode-resolution
   sequencing.** An invalid server-only variable must not block a
   docs command.

   *Resolution*: accepted. The subcommand dispatches before
   enforcement resolution and server argument parsing, beside the
   `config` and `conversations` exits, with a subprocess test
   proving an invalid ambient value does not block it. Amended in
   the enforcement and M3 sections.

9. **P2: the risk section reintroduces leaking unknown field
   names.** A production-only missed field's complaint must not
   name the field.

   *Resolution*: accepted. The risk bullet now says the complaint
   names the declared event and reports the unknown field only as
   a fixed violation code and count; the key's identity belongs to
   strict-mode reproduction. Amended in the risks section.

10. **P2: the sanitization resolution understates known mandatory
    sites and tests only the field.** `reported_board` and
    `reported_version` only strip whitespace; `session_open`
    renders a raw Client-Id; args leak even when fields are
    clean.

    *Resolution*: accepted. The provenance inventory's two known
    mandatory sites are named up front (`ota.py`'s
    `reported_board`/`reported_version` bounding, and
    `session_open`'s client rendering in `device/session.py`),
    event-only copies cover payload fields AND message arguments,
    and the adversarial tests assert the sentinel's placement
    across `record.args`, the rendered sentence, both formats,
    complaint, exception, and taps, while the OTA response bytes
    and stored state stay unchanged. Amended in the registry and
    milestone sections.

11. **P2: milestone documentation and changelog obligations
    disagree.** Every milestone owes its changelog entry,
    implementation section, and tick.

    *Resolution*: accepted. Every numbered milestone and checklist
    entry now carries the per-milestone duties: append the
    implementation section (deviations or an explicit none),
    update the changelog, tick and link in the same change.
    Amended in the milestones sections.

Verdict as posted: not ready; the blocking conflict was resolved by
Rafael's decision and #165, and the remaining findings are amended
above.
