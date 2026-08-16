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
main@f35001a, after #120's M5 narrowing and #144, the surface is:

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
  style of the two contract files) covering the five store paths
  and any other post-baseline path a diff of pinned event names
  against the inventory turns up, committed green BEFORE any
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
    The fields in this class today are `ota_check`'s `client`,
    `board` and `firmware` and their kin on the activation events,
    which the onboarding work bounded at their sites on purpose;
    the kind exists so the registry says which fields carry
    far-side bytes rather than laundering them as identifiers.
    M1 inventories every string field by provenance (operator
    configuration, server-minted, far-side sanitized) and assigns
    the kind from the inventory; a far-side string whose decision
    site does NOT sanitize it is a real finding, fixed at that
    decision site in M1 (a bounds-and-strip on adversarial input
    changes nothing for the lawful values the pin suites plant, so
    the pins stay untouched), and each such fix is its own commit
    named in the implementation doc. This is the one deliberate
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
    grammar (`persona`, `memory`, `fragment:<name>`,
    `instructions:<entry>`, `server_instructions:<entry>`,
    `server_prompt:<entry>:<position>`, with `<name>` and
    `<entry>` configured identifiers and `<position>` a positive
    integer) and whose values are counts. A key matching no
    provenance form is a violation; M2's tests cover an empty
    mapping, a populated one, and every provenance form.
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
  keywords; arguments that render joined or derived values (the
  onboarding provenance, a comma-joined class-name list) are
  declared as what their builder produces, with the builder named
  in the conformance inventory.
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
validated whole: the event is declared for the emitting channel,
the level is in the declared set, no declared-required field is
missing, no undeclared field is present, every field matches its
kind, every `TOKEN` value is in its set. On server channels
`session` and `device` remain ordinary declarable event fields,
since a server emitter has no base identity to protect. M2's
tests include collision cases planting sentinel-shaped replacement
values for each base key. The message arguments are inside the machinery, not
beside it: `Emission.args` reaches every tap and the formatter
renders them, so each spec also declares its argument tuple (arity
and per-position kind, drawn from the same taxonomy), transcribed
in M1 while reading each site, and validation covers the tuple the
way it covers fields. One argument on the surface today cannot
satisfy the taxonomy: `asr_prompt_echo`'s recovered branch renders
the recovered transcript into its sentence, and its pin preserves
the text. That argument is declared as an explicit, single-entry
grandfather (`GRANDFATHERED_ARGS`, carrying the site and the
tracking-issue number), because removing it is a surface narrowing
this issue's no-behavior-change contract forbids; M1 files the
follow-up issue for the narrowing and the registry entry cites it,
so the exception is visible machinery rather than a quiet hole.

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
retained log. M2's sentinel proofs cover three shapes, a
credential-shaped value in a wrong-kind field, a credential-shaped
undeclared event name, and a credential-shaped undeclared spread
key, each asserted absent from all the surfaces above.

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
  get the event, and one plain WARNING sentence on the emitter's
  own channel reports the violation. Not an event itself, for the
  same reason a tap failure's report is not one: a complaint that
  went back through validation could recurse. The recovery is a
  MATRIX, defined per violation class rather than implied by
  "drop": an undeclared field, wrong kind, or unlisted token drops
  that field; an undeclared event re-labels to `schema_violation`
  with base fields only; a missing required field emits what was
  given (nothing to drop, the complaint says which class fired); a
  wrong level emits at the level the caller chose (a level is not
  droppable and rewriting it would falsify the record); a wrong
  channel emits on the channel the emitter owns (it has no other);
  and every one of these carries the fixed safe sentence and the
  one-line complaint. Behind the matrix sits a LAST-RESORT GUARD:
  in forgiving mode the whole validation runs under its own
  `try/except`, so a bug inside the validator itself produces the
  fixed safe complaint and a degraded base-fields emission instead
  of an exception on a reply path. M2 tests the guard by injecting
  a validator that raises an exception carrying a sentinel and
  asserting the reply survives and the sentinel appears nowhere.
- The switch is `SAMTAL_EVENTS_ENFORCEMENT` (`strict` or
  `forgiving`), held in a module flag with a setter, and it is the
  SERVER ENTRYPOINT that resolves it: `main()` reads the variable
  after it has loaded `.env`, so the documented dotenv layer works
  for this variable like any other, which an import-time read
  could never honor (`main.py` imports `app` and therefore
  `events` before `main()` runs). Resolution in `main()`:
  - `strict` or `forgiving`: as written;
  - unset: `forgiving`, because a running server is a deployment
    whatever artifact it runs from, and a wheel or source
    deployment must not be one telemetry bug away from losing a
    reply just because it is not the container;
  - anything else: the server refuses to start, naming the
    variable and the two values. A misspelled relaxation must fail
    at boot, not at the first live violation.
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
  with an `event=` literal and asserts the name is declared and
  every statically-named keyword field is declared for it.
- Coverage is TWO-WAY, because containment alone would let a
  surplus declared field sit unused as a permanent enlargement of
  the allowlist: every declared non-base field must be evidenced,
  either by a static keyword at some emit site or by an explicit
  spread-inventory entry naming the builder (`_echo_fields`,
  `language_fields`, `provider_fields`, and the rest of the nine)
  whose AST the test parses to extract the keys it can produce and
  asserts they match the entry; and every declared token must
  appear as a literal in the emitting module (the decision site),
  found by the same walk. A declared field or token nothing
  evidences fails the test.
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

### The README table is checked, not generated

The table's `when` cells carry prose worth keeping (they explain,
per event, what moment of the system the event marks), so
generating the whole table would trade away its best content. But
free backticked prose is not a machine representation of a field
set, the table holds 34 event rows against 57 declared events, and
its lead sentence's base-field claim is only true for the session
channel. M3 therefore restructures the table without discarding the
prose:

- The table splits into a session-channel table and a
  server-channel table (matching how the payloads actually differ),
  each section's lead sentence stating its own base fields
  truthfully; the 23 missing events get rows, budgeted as M3's
  main writing task.
- The fields cell adopts a mechanically delimited grammar the
  checker parses exactly: the cell is a comma-separated list of
  `field` names, each optionally followed by one parenthesized
  annotation; a token-set field's annotation lists every token in
  backticks; prose lives inside the parentheses. The `when` cell
  stays free prose.
- `tests/unit/test_event_docs.py` then checks real agreement, both
  directions at every layer: every registry event has exactly one
  row and every row names a declared event (duplicates fail); each
  row's parsed field list equals the declared non-base field set
  exactly, so an extra documented field fails like a missing one;
  each token-set field's parenthesized tokens equal the declared
  set exactly, bogus tokens included; the two sections' event sets
  match the channel split.

The check reads the table from the README at test time, so a table
edit that breaks agreement fails the unit lane, which is what the
acceptance criterion's "diff-checked in CI" means here. The
mutation matrix covers every branch: a dropped row, a bogus row, a
duplicate row, a dropped field mention, an EXTRA bogus field
mention, a dropped token, and an extra bogus token, each observed
failing and reverted.

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
   yet: the emitters do not read the registry in M1, so the release
   from this merge behaves identically by construction, and the
   declarations are already pinned to reality by the conformance
   test and by review.
2. **M2, the emitters enforce**: validation in both `_emit`s,
   `EventSchemaError`, the forgiving path with its complaint line,
   the env switch with strict default, the Dockerfile `ENV` line,
   the no-leak sentinel tests for both modes, the README Logging
   paragraph on the switch. Both pin suites and the full lanes
   green under strict enforcement is the milestone's core proof.
3. **M3, the table cannot drift**: `test_event_docs.py`, whatever
   small README edits exactness demands, mutation proofs (drop a
   row, drop a field mention, drop a token mention, add a bogus
   row, each observed failing and reverted), CHANGELOG, and the
   implementation-doc wrap.

## Files touched

New: `samtal_server/events_schema.py`,
`tests/unit/test_event_schema_conformance.py`,
`tests/unit/test_conversations_event_pins.py` (M1, the
post-baseline characterization pins),
`tests/unit/test_event_docs.py`, this plan's implementation doc.

Modified: `samtal_server/events.py` (M2, validation in the two
`_emit`s and the mode flag), `samtal_server/main.py` (M2, the
entrypoint's mode resolution), `tests/conftest.py` (M2, pinning the
lanes strict), `tests/unit/test_events.py` (M2, the mechanics tests
adopt the schema seam), `Dockerfile` (M2, one `ENV` line),
`samtal-server/README.md` (M2 one paragraph; M3 exactness edits),
`CHANGELOG.md` (per milestone).

Untouched on purpose: every emit site in `samtal_server/`, both pin
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
- M3: the table checks (exact equality both ways at row, field,
  and token level, per the delimited grammar) with their mutation
  proofs.

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
  a complaint line, not an outage; the complaint names the event
  and field so the fix is one declaration. This failure mode is
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
- **The README check is brittle against prose edits.** The fields
  cell's grammar confines prose to parentheses and the `when`
  cell, which the check never reads for names; a wording edit
  there passes, and an edit to the delimited field list should
  fail, because that is the drift the check exists to catch.
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
      inventory is recorded and the narrowing follow-up issue
      filed; no enforcement wired; both lanes green, pin suites
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
      enforcement.
- [ ] M3: the README table cannot drift: the table splits by
      channel with the delimited fields-cell grammar and gains the
      missing rows, `test_event_docs.py` proves exact agreement
      both ways at row, field, and token level, the mutation
      matrix is recorded, CHANGELOG closes the issue's entry.

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

2. **P1: the `asr_prompt_echo` grandfather preserves an
   acknowledged far-side leak.** The recovered transcript is user
   speech reaching the rendered log and taps; a grandfather is a
   permanent bypass. Require the narrowing before enforcement and
   resolve the pin-contract conflict explicitly.

3. **P1: message templates remain an unchecked leak path.** The
   registry declares fields and argument tuples but not the
   `message` string; `events.info(secret, event="heard", ...)`
   passes validation. Declare each legal emission's exact
   registry-owned template and compare before dispatch, with
   direct-message sentinels in both modes.

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

6. **P1: entrypoint resolution leaves programmatic servers
   strict.** A production process importing `create_app` under an
   ASGI runner never runs `main()`, so a mismatch can still kill a
   reply. Resolve at application construction (unset forgiving),
   keep `main()` resolving after dotenv, and sequence resolution
   after the `config`/`conversations` early exits.

7. **P1: the README conformance design is impossible for
   `session_rejected` and incomplete.** One-row-per-event cannot
   inhabit two channel tables; level, kind, requiredness and
   nullability can drift while CI stays green. Key rows by event
   and channel or add a parsed channel column; compare every
   property the table claims, or generate a complete schema
   appendix.

8. **P2: the `SOURCES` grammar admits `memory`, which
   `prompt_assembled` explicitly excludes.** Derive the forms from
   the know-how builders; add negative tests for `memory` and
   unknown prefixes.

9. **P2: token conformance by module-wide literal occurrence is
   too weak.** A docstring satisfies it, and
   `activation_not_offered`'s reasons are produced in
   `onboarding.py`, a different module. Require per-event,
   per-field decision-site inventories naming the producing
   function or constant, following cross-module producers.

10. **P2: a WARNING complaint disappears at supported log
    levels.** `log_level` admits ERROR and CRITICAL, and such
    deployments retain no complaint. Emit at a level that
    survives, or a dedicated path, and test under ERROR and
    CRITICAL roots.

11. **P2: the sanitization allowance contradicts the
    identical-behavior and untouched-files claims, and sanitizing
    `reported_version` would change the OTA response.** List the
    conditionally modified decision-site files, drop the
    identical-by-construction claim, normalize event-only copies,
    and prove the OTA response unchanged under adversarial input.

Verdict: not ready.
