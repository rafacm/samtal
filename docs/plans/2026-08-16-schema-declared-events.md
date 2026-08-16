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
  added the `conversations_*` events and M5 reshaped several fields;
  the pin suites moved with them, so the contract files are current.
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
  - `ID`: a bounded machine form (session id, MAC, client UUID,
    revision string, language code);
  - `INT`, `FLOAT` (an `INT` value satisfies `FLOAT`, since sites
    pass round numbers where a measure is integral), `BOOL`;
  - `COUNT`: an `int >= 0`, for the fields whose meaning is "how
    many" (`sentences`, `tools`, `agents`, `sessions`);
  - one bespoke structured kind for `prompt_assembled`'s `sources`
    (the only nested field on the surface), declared with its own
    fixed sub-shape read from the emit site, not a generic "dict"
    escape hatch.
  `nullable` exists because the session scope's base `device` is
  `None` until the MAC is normalized, and only for fields like it.
- `EventSpec(name, channels, levels, fields)`. `channels` is the
  frozenset of logger channels the event may be emitted on, which
  is the issue's "channel" made concrete and is also what encodes
  scope: `samtal_server.session` is the session scope, a module
  `__name__` is a server scope, and `session_rejected` declares
  both `samtal_server.session` and `samtal_server.ws`. Validation
  compares the emitting object's actual channel (`SessionEvents`
  emits on the session channel; a `ServerEvents` carries its
  channel by construction) against the declaration, and the
  conformance test ties each module's `ServerEvents(__name__)` to
  the events declared on that channel, so an event emitted from
  the wrong module is a violation even when its fields are right.
  `levels` is the frozenset of logging levels the event is emitted
  at today, because the levels are part of the compatibility
  surface. `fields` covers the full payload the log tap receives,
  base fields included (`event`, and on the session channel
  `session` and `device`), so one validation reads the finished
  payload and channel differences fall out of the specs rather
  than special cases.
- `REGISTRY: dict[str, EventSpec]` with all 57 events, grouped and
  commented by subsystem in the order of the README table, each
  declaration citing nothing: the fields and tokens are the
  declaration, and the conformance test is what ties them to the
  sites.

The registry declares the tap-contract surface only. `vad` and
`dropped` are capture side channels outside the tap contract
(`events.py` says why), and they stay outside the registry.

### Enforcement lives in `_emit`, and the mode is an env switch

Both emitters validate the finished payload in `_emit` before
dispatch: the event is declared for this scope, the level is in the
declared set, no declared-required field is missing, no undeclared
field is present, every field matches its kind, every `TOKEN` value
is in its set. Violations become one `EventSchemaViolation` value
listing the offending field names (names only: a field's value at a
violating site is exactly the bytes the registry exists to keep off
the surface, so neither the strict exception text nor the forgiving
complaint ever renders one; M2 plants a credential-shaped value in
a wrong-kind field and asserts its absence from the exception
message, the complaint line, and both log formats).

- **Strict** (the default): `_emit` raises `EventSchemaError`. This
  is what the test lanes and a source checkout run.
- **Forgiving**: the offending fields are dropped from the payload
  (an undeclared event keeps only its base fields), the emit
  proceeds so the operator still gets the line and the taps still
  get the event, and one plain WARNING sentence on the emitter's
  own channel names the event and the dropped field names. Not an
  event itself, for the same reason a tap failure's report is not
  one: a complaint that went back through validation could recurse.
- The switch is `SAMTAL_EVENTS_ENFORCEMENT` (`strict` or
  `forgiving`), read once at import time into a module flag with a
  setter for tests. The container image sets
  `SAMTAL_EVENTS_ENFORCEMENT=forgiving` in its `ENV` block, beside
  the existing `SAMTAL_SERVER__LOG_FORMAT=json`, because the image
  is the production artifact; a source run is development and stays
  strict. This is deliberately not a `ServerConfig` field:
  `events.py` sits below `config/` in the import graph by design,
  the switch governs telemetry machinery rather than server
  behavior, and #139 is about to migrate the operator schema this
  would otherwise join. The README Logging section documents the
  variable in one paragraph.
- An unknown value of the variable means strict, because the honest
  failure mode of a misspelled relaxation is loudness, not silence.

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
  every statically-named keyword field is declared for it (the
  guard's two stated-limits convention applies: `**spread` keys are
  not statically visible, and the strict lane at runtime is what
  validates them, which the existing pin suites exercise for all
  nine spread events).
- The registry's own coherence is asserted: every `TOKEN` field
  carries a non-empty token set, no other kind carries one, every
  event has at least one level, scope-base fields are declared
  exactly where the scope requires them.
- A planted-source test proves the walk sees the shapes it claims
  to see, as the guard's planted tests do.

### The README table is checked, not generated

The table's `when` cells carry prose worth keeping (they explain,
per event, what moment of the system the event marks), so
generating the table would trade away its best content. Instead
`tests/unit/test_event_docs.py` parses the table and cross-checks:

- event-name equality both ways: every registry event has exactly
  one row, every row names a declared event;
- every declared field name appears in backticks in its row's
  fields cell (base fields excepted: the section's lead sentence
  already says every event carries `event`, `session`, `device`);
- every declared token appears in backticks in its row's cell;
- levels are not in the table today and are not added by this
  check; they are declared in the registry and enforced at emit.

The check reads the table from the README at test time, so a table
edit that breaks agreement fails the unit lane, which is what the
acceptance criterion's "diff-checked in CI" means here. Small README
edits to reach exactness are expected (a field the table forgot, a
token the prose spells differently) and are ordinary doc fixes
recorded in the implementation doc, not behavior changes.

### What is deliberately out of scope

- No emit site changes its fields, levels, sentences, or names:
  the pin suites pass unmodified, and any real mismatch the
  registry work uncovers between the README and reality is fixed
  in the README or recorded as a follow-up issue, never by
  reshaping the surface under this issue.
- The conversation store, capture, and audit surfaces are other
  surfaces; the registry covers the structured events only.
- Exporters (#66/#67) consume the registry later; nothing here
  builds them.
- #141 moves pipeline emit sites and must not run concurrently;
  the batch's one-at-a-time rule already guarantees that.

### Three milestones, three PRs, stacked

1. **M1, the registry exists and is provably complete**:
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
`tests/unit/test_event_docs.py`, this plan's implementation doc.

Modified: `samtal_server/events.py` (M2, validation in the two
`_emit`s and the mode flag), `Dockerfile` (M2, one `ENV` line),
`samtal-server/README.md` (M2 one paragraph; M3 exactness edits),
`CHANGELOG.md` (per milestone).

Untouched on purpose: every emit site in `samtal_server/`, both pin
suites, `test_event_surface_guard.py`, `logs.py`, the capture side
channels, `config/` (no new `ServerConfig` field, so the #144
example-config pin is unaffected).

## Tests

- M1: the conformance test (AST walk, planted-source proof,
  registry coherence). The strict runtime lane does not exist yet.
- M2: strict mode raises on each violation class (undeclared event,
  undeclared field, wrong kind, unlisted token, missing required
  field, wrong level, wrong scope); forgiving mode drops the
  offending fields, still emits, and complains once, covered for
  the same classes; the sentinel test plants
  `sk-test-...never-a-real-credential` in a violating field and
  asserts absence from the exception text, the complaint, and both
  log formats; the env switch's three states (unset = strict,
  `forgiving`, unrecognized = strict); both pin suites unmodified
  and green under strict, which is the standing conformance proof.
- M3: the table checks with their mutation proofs.

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
- **Import-order emissions before the mode flag is read.** The flag
  is read at module import, before any emitter exists, so there is
  no window in which emissions are validated under the wrong mode.
- **Validation cost on the reply path.** A per-event dict walk over
  fewer than a dozen keys; the events fire per decision, not per
  frame. No caching machinery unless a lane shows a need, which
  none is expected to.
- **The README check is brittle against prose edits.** The check
  reads backticked names only, never prose; a wording edit that
  keeps the names passes, and one that drops a name should fail,
  because that is the drift the check exists to catch.
- **`session_rejected` on two channels.** Declared once with both
  channels and the union payload validated per emit; the channel
  gates which emitter may say it, and the base-field difference is
  encoded per channel. If encoding one spec for two channels turns
  out muddier than two, the milestone may split the declaration
  and record the deviation.

## Milestones

- [ ] M1: the registry exists and is provably complete:
      `samtal_server/events_schema.py` declares all 57 events with
      fields, kinds, levels, scopes, and token sets;
      `test_event_schema_conformance.py` ties every static emit
      site to it and proves its own walk on planted source; no
      enforcement wired; both lanes green, pin suites untouched.
- [ ] M2: the emitters enforce at emit time: strict raises
      `EventSchemaError`, forgiving drops offending fields and
      complains in one plain sentence, `SAMTAL_EVENTS_ENFORCEMENT`
      switches with strict as default and unknown values strict,
      the image sets forgiving, the no-leak sentinel proofs pass,
      both pin suites pass unmodified under strict enforcement.
- [ ] M3: the README table cannot drift: `test_event_docs.py`
      cross-checks rows against the registry both ways with field
      and token mentions, the mutation matrix is recorded, the
      README reaches exactness, CHANGELOG closes the issue's entry.

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

3. **P1: the proposed string kinds bless unbounded far-side values
   as trusted metadata.** `ota_check.client`, `board`, `firmware`
   come from request headers and JSON, bounded only by strip and
   nonemptiness at their sites; a generic IDENTIFIER/ID kind would
   accept a credential-shaped string. Inventory string fields by
   provenance, give field-specific length and syntax constraints,
   and normalize untrusted values at their decision sites, which
   requires relaxing the plan's blanket no-emit-site-change rule
   where the surface cannot satisfy the settled taxonomy.

4. **P1: payload-only validation leaves message and args outside
   the no-leak machinery.** `Emission.args` reaches taps and the
   formatter renders them; `asr_prompt_echo`'s recovered branch
   renders the recovered transcript into the message, and its pin
   preserves it. Dropping a payload field does not remove the same
   value from args; forgiving mode must not re-render the original
   message for an invalid emission; sentinels must inspect args
   and attached taps.

5. **P1: validating after base-field merging permits session
   identity spoofing.** `**fields` can overwrite `session` and
   `device` and still typecheck. Validate caller fields before
   merging and forbid session callers supplying base keys.

6. **P1: the violation diagnostics can leak rejected names.**
   Undeclared event names and spread keys are caller-supplied
   strings; complaints and exceptions must render registry-owned
   identifiers, fixed codes and counts only, and an undeclared
   event must not retain its raw name. Add sentinels for an
   undeclared event name, an undeclared spread key, and a
   wrong-kind value, asserted absent from exception str, repr and
   args, complaint records, both formats, and attached taps.

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

8. **P1: M2 cannot keep the unit lane green without modifying
   tests the plan leaves untouched.** `test_events.py` emits
   undeclared names, undeclared fields, incomplete payloads, and a
   synthetic channel by design; strict enforcement rejects them,
   and the file is absent from the touched list. Update it through
   an explicit schema seam; only the two pin files are the
   byte-unchanged contract.

9. **P1: the README check proves containment, not agreement.** The
   check rejected nothing extra, the table holds 34 event rows so
   23 events are missing, the lead sentence's base-field claim is
   false for server events, and free backticked prose is not a
   machine representation of field and token sets. Give rows a
   mechanically delimited field list with per-field tokens (or
   generate an appendix), check equality both ways plus duplicate
   rows and scope-aware base fields, add bogus-field and
   bogus-token mutations, and budget the missing rows.

10. **P2: M1's conformance test cannot prove exactness.** Call-site
    containment does not reject a surplus declared field, and
    spreads are invisible; either require two-way coverage
    (declared fields evidenced statically or by inventoried
    spread-builder branches, tokens mapped to decision-site
    literals) or stop calling M1 provably complete.

11. **P2: the stated pin evidence is stale.** The `conversations_*`
    events appear in neither pin file; their five paths are only
    loosely asserted. Add exact pre-enforcement characterization
    coverage for paths added since the pin files' baseline,
    without modifying those two files.

12. **P2: forgiving recovery is undefined where no field can be
    dropped.** Missing required field, wrong level, wrong scope
    have no defined result, and a validator bug can still escape
    through a reply path. Provide a recovery matrix per violation
    class and a final forgiving-mode guard around validation,
    tested with an injected validator raising a sentinel.

13. **P2: type enforcement lacks optimized-mode and edge
    semantics.** Booleans are `int` subclasses, nonfinite floats
    are unaddressed, and `assert`-based validation dies under
    `python -O`. Use explicit conditions and raises, reject bools
    for numeric kinds, require finiteness, and prove strictness
    under `-O` in a subprocess.

Verdict: not ready.
