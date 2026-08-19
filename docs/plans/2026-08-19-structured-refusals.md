# Structured refusals and the stated round trip

Issue: [#192](https://github.com/rafacm/samtal/issues/192). Companion
implementation doc:
`2026-08-19-structured-refusals-implementation.md`, one section per
milestone, appended in the change that ticks the milestone.

## Goal

Two API ergonomics gaps closed before the Angular admin UI (#129) is
written against the current shapes. Refusals become RFC 9457 problem
details (`application/problem+json`) carrying a machine-readable list
of field-level errors where validation produces them, so a form can
highlight the offending field instead of quoting a paragraph. And the
read-modify-write round trip becomes stated contract: which part of a
read is safe to resubmit, what a masked value means, and what an
unchanged secret needs on resubmit, said in the document and proven by
tests.

## The issue's decisions, restated

- RFC 9457 is the error body shape. The existing prose survives as
  `detail`, unchanged byte for byte, and an extension member carries
  `{path, message}` entries derived where validation already happens,
  the repository. Validation stays single-sourced; only the rendering
  of a refusal gains structure.
- The round-trip rule is stated in the API description and the
  committed OpenAPI document: which read shape is writable as-is,
  which fields are display-only, and what an unchanged secret means on
  resubmit.
- The CLI keeps printing the sentence it prints today; `detail`
  preserves it.
- The committed OpenAPI document and the drift check move in the same
  change.

The issue leaves one question open: whether the masked-values case can
be made safe by contract alone, or needs a writable projection
distinct from the display envelope. Resolved below, corrected by the
review round's first finding: contract alone is disproved by writes
the API accepts today, so the display envelope is made writable by an
unchanged-value marker with defined PUT semantics, rather than by a
second projection shape.

## Evidence, verified at plan time

All at main `68d00ce`.

- The structured source already exists. `store._validation_problems`
  (`store.py:1781`) renders each pydantic error's `loc` as a dotted
  path and its `msg` (stripped of the `Value error, ` prefix) as a
  line of the refusal sentence, and deliberately never touches
  `error["input"]`. The extension member is those same pairs before
  they are joined into prose, so the two cannot disagree.
- The CLI is already compatible. `cli._payload` (`cli.py:537`) accepts
  any content type containing `json`, which `application/problem+json`
  does, and `cli._answer` (`cli.py:528`) reads only `detail` and
  ignores members it does not know. No CLI change is needed, and the
  existing transport suite pins that behavior.
- Four places build a refusal body, all in `api.py`: the `_refusal`
  exception handler (every `REFUSAL_STATUS` type), the `_BearerGate`
  401, the `_SanitizedErrors` last-resort 500, and
  `_malformed_request` 422. The conversations routes raise the shared
  refusal types and add no body-building of their own
  (`grep -n JSONResponse samtal_server/conversations/api.py` finds
  none), so they inherit whatever the handlers answer.
- 71 assertions across the unit and integration suites read or pin a
  refusal's `"detail"`
  (`grep -rn '"detail"' tests/unit tests/support tests/integration`),
  of which the exact-body `== {"detail": ...}` pins are the ones this
  change reshapes; the substring assertions on `detail`'s sentence
  survive unchanged.
- The masked-resubmit facts, corrected by the review round's first
  finding:
  - A validated write can read back masked. `_ENV_NAME_RE`
    (`models.py:69`) accepts lowercase names, the display rule
    (`secrets.py:157-173`) passes only `$NAME` or an uppercase bare
    reference, and `tests/unit/test_config_reads.py:545` plus
    `test_config_cli_secrets.py:83` already pin a validated
    `connection.api_key_env: sk_test_...` reading back as the mask.
    Deliberately so on the display side: a lowercase name in a secret
    slot is credential-shaped often enough that passing it through
    would make the display the leak. MCP references differ the same
    way: `_env_reference` (`models.py:905`) strips whitespace where
    `mask` does not, so an accepted padded reference also reads back
    masked. So "masked values appear only for rows stored outside
    validation" is false, and the round trip needs write-side
    semantics, not only prose.
  - What stays true, and keeps the fix small: resubmitting `********`
    today is refused, never silently stored.
    `models.check_no_inline_secrets` (`models.py:766`) refuses a
    secret-shaped key at any depth of a provider's options naming the
    path and never the value, and `McpServerConfig._secret_problems`
    (`models.py:1150`) requires a `$VAR` reference for a
    secret-bearing `env` or `headers` key. The nested `env`/`headers`
    case is unreachable through the store (both maps are typed
    `dict[str, str]`), recorded in the #207 feature doc as the
    write-time depth question, and is not this plan's to move.
  - `secrets.mask` passes a syntactically valid environment reference
    through, so the common case resubmits as itself; PR #207's
    absence rule keeps every read write-shaped in structure. The gap
    is exactly the masked values, which the marker below closes.

## Design

### The problem body

Every refusal this API answers becomes one shape, served as
`application/problem+json`:

```json
{
  "title": "refused",
  "status": 422,
  "detail": "invalid provider fragment llm.local:\n  - type: ...",
  "errors": [{"path": "type", "message": "..."}]
}
```

- `title` is a short fixed string per status, one per entry of a new
  `PROBLEM_TITLES` mapping beside `PROBLEM_DESCRIPTIONS`, so the two
  status vocabularies live in one place. Titles are boring on purpose
  (`unauthorized`, `not found`, `conflict`, `refused`,
  `server failure`, `no runtime`): RFC 9457 wants a summary of the
  problem type, not a second sentence.
- `status` repeats the HTTP status, as the RFC describes, so a body
  separated from its response (a log, a bug report) still says what it
  was.
- `detail` is exactly the sentence the API answers today, unchanged.
- `errors` is the extension member: a list of `{path, message}`
  objects, `path` an RFC 6901 JSON Pointer into the submitted
  fragment (`/connection/api_key_env`; the empty string is the whole
  fragment, and `~`/`/` in a key are escaped as the RFC says, which
  is why a dotted spelling was rejected: a dot in a key would be
  indistinguishable from nesting). `message` is the same text the
  corresponding `detail` line carries. The member is always present
  and `[]` when the refusal has no field decomposition, so every
  refusal has one shape, the rule the response models already follow.
- `type` and `instance` are omitted. An absent `type` means
  `about:blank` per the RFC, which is the truth: these problems are
  described by their status and their prose, and a URI registry
  nobody serves would be surface with no reader.

### One seam: `problem_response`

The four body-building sites in `api.py` become calls to one function,
`problem_response(status, detail, errors=())`, which builds the body
through the `Problem` model and sets the media type. It is the one
place a refusal becomes bytes, so a handler stops knowing what a
refusal body looks like, and the model and the wire cannot disagree.
The gate and the last-resort middleware construct their responses
through it too.

`responses.Problem` grows the three fields, and a `FieldError` model
(`path`, `message`) is declared beside it, both `extra="forbid"` like
every shape in that module. The document's refusal declarations
(`_problems`) change their content type to `application/problem+json`,
with `Problem` and `FieldError` injected into `components` beside the
entity models, so the committed document says what the wire does.

### The carrier: `ConfigError.problems`

`loader.ConfigError` gains an optional structured payload:
`ConfigError(message, problems=(...))` with a `problems` attribute
defaulting to `()`, each entry a `FieldProblem` named tuple (`path`,
`message`) declared in `models.py` beside the validators that produce
them (loader already imports models, so no new import direction).
Subclasses inherit it untouched, and every existing raise site
compiles unchanged.

Exactly one site fills it: `store._load`, where
`_validation_problems` already walks `ValidationError.errors()`. The
walk is refactored to produce the pairs once and render both the
sentence and the payload from them, so the prose and the structure are
one computation.

The walk alone is not enough, the review round's second finding:
the validators that know their semantic field are model-level
(`ProviderConfig._reject_inline_secrets`,
`McpServerConfig._check_transport_fields`,
`FillerConfig._check_phrases`), so pydantic locates their errors at
the model, not the field, and the transport validator joins several
problems into one message. So `models.py` declares the structured
form beside the validators that produce it: `FieldProblem(path,
message)` and `FieldProblemsError(ValueError)` carrying a tuple of
them, and those validators collect their problems as pairs and raise
the one exception (`check_no_inline_secrets` keeps raising per path
it finds; the transport validator's problems become one pair each).
`_load`'s walk reads each pydantic error's original exception from
`errors()`'s `ctx` and, where it is a `FieldProblemsError`, takes its
pairs with the error's own `loc` as the pointer prefix; every other
error keeps the `(loc, msg)` derivation. The pairs are still the one
computation both prose and payload render from, which means the
transport validator's sentence becomes one line per problem instead
of one `; `-joined line: a deliberate, recorded prose change, same
words per problem, pinned by the new goldens. M1's first commit
verifies the `ctx` mechanism (pydantic carries the raised exception
in the error's context for wrap/value errors) with a pin that fails
loudly on a pydantic upgrade that drops it. `loader.ConfigError`
carries the resulting pairs unchanged; loader already imports
`models`, so the types have one home and no cycle.

Deliberately not filled anywhere else:

- `store._stored` (unreadable rows, `StorageError`, 500): a 500 is not
  the caller's form to highlight, and the detail sentence already
  names the row and fields.
- `store._refuse_unresolved` (reference problems): the entries name
  other entities, not fields of the submitted body, so a form has no
  field to attach them to. `check_references` returns sentences, and
  restructuring it is not worth buying for an attachment point that
  does not exist. If the admin UI later wants structured reference
  errors, that is its own small issue.
- The argument-shaped bodies (`_agents`, `_name`, `_secret`) and the
  claim route's replacement sentences: each is a single fixed
  expectation about a one-key body, which `detail` already states
  whole.

### The round-trip contract, and the unchanged-value marker

The behavior half first, because the review round's first finding
disproved prose alone: a write the API accepts today (a lowercase
bare reference, a whitespace-padded `$VAR`) reads back as `********`,
so a read-modify-resubmit of such an entity would be refused with no
way to say "keep what is there".

**The marker.** On an entity PUT, before validation, the repository
walks the incoming fragment with the kind's `secret_key` predicate,
the same descriptor fact the display masks by (#207), at every depth
the display walks. Where a value under a secret-shaped key equals the
mask literal exactly, the value currently stored at the same path in
that entity's row is substituted; the fragment then validates whole,
exactly as if the operator had retyped the stored value. A mask with
nothing stored at that path, or on a PUT that creates the entity, is
refused with a sentence naming the path: the mask is not a value.
One helper in `store.py` on the entity write path, so the CLI's
`--local` writes inherit it, and the predicate being the descriptor's
means what a read masks and what a write restores cannot come to
disagree. A mask literal under a key the predicate does not match is
not touched and meets validation as itself.

Stated in `API_DESCRIPTION` (one new paragraph) and sharpened in
`Envelope.entity`'s and `Envelope.secrets`' descriptions, all of which
are committed bytes in the OpenAPI document:

- A read's `entity` is writable as-is. PUT replaces the model-shaped
  half and never touches stored secrets, so an edit is read, modify,
  resubmit whole; fields the read omitted (the absence rule) stay
  omitted and mean the same absence on the way back.
- An environment reference reads back as itself and resubmits
  harmlessly. A value shown as `********` resubmits as "keep the
  stored value", by the marker rule above; writing the mask where
  nothing is stored is refused naming the field.
- An unchanged stored secret needs no action on resubmit: the slots in
  `secrets` are informational, and rotating a credential is the secret
  PUT, the only door plaintext enters by.

No writable projection distinct from the display envelope: the marker
makes the display envelope the writable projection, which is the
resolution #194's per-entity export inherits.

`API_VERSION` stays `"1"`: the constant is deliberately not a release
version, the project is pre-release, and the committed document is
the contract and moves with this change.

### Considered and declined

- Echoing the rejected input per error, as FastAPI's own 422 does:
  never; the input is the leak, which is why the sanitized handler
  exists.
- RFC 9457 `type` URIs: surface with no reader, see above.
- Structured reference refusals: declined with reasons above.
- Serving the OpenAPI document live: out of scope here as it was in
  the REST API plan; the committed document is the contract.

## Tests

The suites to touch are the ones already pinning refusal bodies:
`test_config_api.py`, `test_config_api_reads.py`,
`test_config_api_writes.py`, `test_config_api_pending.py`,
`test_config_api_runtime.py`, `test_conversations_api.py`, and the
CLI transport suite for the pass-through pins.

- A `problem(status, detail, errors=())` expected-body builder joins
  the API tests' existing support so the exact-body pins reshape once;
  substring assertions on `detail` stay as they are, which is the
  claim that the sentences did not move.
- New pins, each named to what it holds:
  - One refusal from each emitter (repository refusal, gate 401,
    malformed request 422, last-resort 500) answers the one shape,
    with `application/problem+json` as its content type.
  - A provider fragment failing validation on two fields answers
    `errors` entries whose paths and messages match the `detail`
    lines pairwise, which is the one-computation claim asserted from
    the outside.
  - The model-level cases, one each: a provider inline secret nested
    in an option answers the pointer to the nested key; an MCP
    fragment breaking the transport rule and a secret rule at once
    answers one entry per problem, each with its field's pointer; a
    filler problem answers a pointer under the layer that holds it;
    and a key containing a dot or a slash answers the escaped pointer
    that distinguishes it from nesting.
  - The sentinel: a fragment planted with a credential-shaped value in
    a wrong-typed field is refused with the value absent from
    `detail`, from every `errors` message and path, and from the log,
    in both log formats.
  - A conversations refusal answers the same shape, which is the
    inheritance claim.
  - The CLI prints the same sentence for a problem+json refusal it
    printed before, pinned through the transport suite's fake
    transport.
- The round trip, one test per commanded kind: write the kind's
  example fragment, GET the envelope, PUT `entity` back unchanged,
  expect the acknowledgement, GET again and expect the same envelope.
  This is the contract's executable form.
- The masked resubmit, starting from writes the API accepts, which is
  what the engine-planted technique cannot cover: a provider whose
  nested `*_env` option holds a lowercase reference, and an MCP server
  whose `env` holds a whitespace-padded `$VAR`, each written over
  HTTP, read back masked, resubmitted unchanged, accepted, and read
  back identical, with the stored value proven untouched. Beside
  them: the mask written where nothing is stored (a fresh entity, and
  a fresh path on an existing one) refused naming the path; and a row
  planted with an inline secret through the engine, read back masked,
  resubmitted, round-tripping the planted row without the mask
  becoming the stored value and without the planted value appearing
  in any response.

## The standing review lenses, answered

- **No-leak.** `errors` is built from `loc` and `msg` only, the same
  two keys the prose already uses, and never from `error["input"]`;
  `problem_response` is the single place a refusal becomes bytes; the
  sentinel test plants a credential and asserts absence from
  sentence, paths, messages, and both log formats.
- **Pin before reshaping.** The reshape is deliberate (pre-release,
  #192's point), but the sentences inside it are not: `detail` strings
  are pinned byte-identical through the change by the surviving
  substring assertions and the CLI pass-through pins.
- **Closed sets.** `PROBLEM_TITLES` is a literal mapping beside
  `PROBLEM_DESCRIPTIONS`, keyed by the same statuses; a coherence
  test holds the two key sets equal.
- **Honest seams.** `problem_response` takes `errors=()` as its
  default and compares nothing by truthiness; `ConfigError.problems`
  defaults to `()` so no raise site changes.
- **Inventories by tooling.** The four emitter sites and the 71
  `"detail"` assertions are grep-verified above and re-run per
  milestone; the OpenAPI document is regenerated and diffed by the
  existing drift step, which is the inventory of what the contract
  change touched.

## Risks and mitigations

- **Broad mechanical test churn.** The exact-body pins across six
  suites reshape in one direction. Mitigation: the shared `problem`
  builder, and the reshape in its own commit before any behavior
  change, so the diff that matters stays readable.
- **FastAPI's `responses` merge for a custom media type.** Declaring
  content per status while keeping the schema reference is
  merge-sensitive, as `_resolve_body_schemas` already documents for
  request bodies. Mitigation: the regenerated committed document is
  the oracle; the drift step fails on any leftover
  `application/json` refusal declaration.
- **A masked read that does not round-trip somewhere unforeseen.** The
  per-kind round-trip tests are the net; a kind that fails one is a
  finding about the display path, filed rather than patched here.

## Milestones

- [ ] **M1: refusals as problem details** (PR TBD). The body shape,
  the `problem_response` seam, `ConfigError.problems` filled in
  `store._load`, `Problem`/`FieldError` models, `PROBLEM_TITLES`, the
  media type in `_problems`, the regenerated OpenAPI document, the
  reshaped pins and the new emitter/sentinel/errors tests, CHANGELOG.
  Design footprint: deepens `loader.ConfigError` (carries structure no
  caller has to build), `store._load` (one computation for prose and
  payload), and `api.py`'s refusal rendering, where one seam replaces
  four body-building sites; no new module.
- [ ] **M2: the writable round trip** (PR TBD). The unchanged-value
  marker in the repository's entity write path, the
  `API_DESCRIPTION` paragraph and `Envelope` description sharpening,
  the regenerated document, the per-kind round-trip tests, the
  masked-resubmit tests from API-accepted writes, CHANGELOG. Design
  footprint: deepens the repository's write path (a caller stops
  having to know what the display masked) with the descriptor's
  `secret_key` as the one predicate both directions share, and
  records in the implementation doc that the issue's open question
  closed as marker-plus-contract rather than contract-alone.

Both milestones leave `main` releasable: M1 changes the refusal body
shape in one merge with its document, and M2 lands the marker with
the contract that names it.

## Plan review round

External review: codex-cli 0.147.0, model gpt-5.6-sol, 2026-08-19,
reviewing commit `dddd49c`. Verdict: ready after the P1/P2
amendments. Findings as received, condensed but faithful; each
carries its resolution below it.

1. **P1: the contract-only round trip is disproved by an existing
   validated write.** `_ENV_NAME_RE` (`models.py:69`) accepts
   lowercase names while the display rule
   (`secrets.py:157-173`) passes only `$NAME` or uppercase bare
   references, and `tests/unit/test_config_reads.py:545` plus
   `test_config_cli_secrets.py:83` prove a validated write of
   `connection.api_key_env: sk_test_...` reads back as the mask. MCP
   references differ too: `_env_reference` strips whitespace
   (`models.py:905`), `mask` does not. So M2 needs a real writable
   projection or an explicit unchanged-value marker, with defined PUT
   semantics and coverage starting from API-accepted writes, not only
   engine-planted rows.

   *Resolution.* Adopted whole. The evidence section now records the
   two validated-write counterexamples in place of the disproved
   claim, the round-trip section defines the unchanged-value marker
   (mask literal under a secret-shaped key on PUT means "keep the
   stored value", substituted before validation by one `store.py`
   helper walking the descriptor's `secret_key` predicate at display
   depth; a mask with nothing stored refuses naming the path), M2 is
   retitled "the writable round trip" and carries the marker, and the
   masked-resubmit tests start from the lowercase and
   whitespace-padded API-accepted writes the finding names. No
   upgrade path is needed for already-stored values: they are real
   values that read masked, and the marker keeps them.

2. **P1: pydantic `loc` does not provide the field paths the feature
   promises.** `ProviderConfig._reject_inline_secrets`
   (`models.py:852`), `McpServerConfig._check_transport_fields`
   (`models.py:1123-1148`) and `FillerConfig._check_phrases`
   (`models.py:1258`) are model-level validators: their errors carry
   an empty or collapsed location even though the messages name the
   semantic field, and the transport validator joins several problems
   into one error. The plan needs a mechanism by which validators emit
   safe structured problems where they know the field, one source for
   prose and structure, an unambiguous path encoding (JSON Pointer,
   since dotted strings cannot distinguish a dot in a key from
   nesting), and tests for the provider inline-secret, MCP multi-rule,
   nested filler and arbitrary-key cases.

   *Resolution.* Adopted whole. `path` is now an RFC 6901 JSON
   Pointer. `models.py` declares `FieldProblem` and
   `FieldProblemsError`, the three named validators raise their
   problems as pairs, and `_load` reads the original exception from
   the pydantic error's `ctx` (prefixing with the error's `loc`),
   falling back to `(loc, msg)` everywhere else, so prose and
   structure stay one computation. The transport sentence becoming
   one line per problem is recorded as a deliberate prose change with
   new goldens, and M1's first commit pins the `ctx` mechanism so a
   pydantic upgrade that drops it fails loudly. The four test cases
   the finding names are in the test plan verbatim.

3. **P2: framework-generated 404 and 405 responses bypass the claimed
   single problem shape.** `_application` registers handlers only for
   `ConfigError` subclasses and `RequestValidationError`
   (`api.py:1780-1784`); authenticated unmatched paths and unsupported
   methods are answered by Starlette directly, and
   `test_config_api.py:120-153` exercises routing 404s. Add a
   sanitized `StarletteHTTPException` handler rendering through
   `problem_response`, preserving safe protocol headers such as
   `Allow`, with tests for unmatched paths, wrong methods and
   trailing-slash paths.

4. **P2: the proposed tests do not prove `detail` remains
   byte-identical.** Substring assertions cannot detect changed
   indentation, ordering or prefixes, and a fake-transport CLI test
   only proves relay of a supplied detail. Require exact golden
   strings from real repository-backed PUTs, including a multi-error
   result, and point the CLI compatibility test at a real response or
   the same exact sentence.

5. **P2: M1 omits existing OpenAPI tests that will fail and does not
   pin the absence of `application/json`.**
   `tests/unit/test_api_openapi.py:245-253` and
   `test_config_api_runtime.py:595-599` hard-code `application/json`
   for every refusal. Name both files, require exactly one content
   key (`application/problem+json`) with a resolving `Problem`
   schema, and pin `Problem`/`FieldError` required fields and
   `additionalProperties: false`.

6. **P2: the documentation work does not classify all read shapes as
   writable or display-only.** The issue requires both categories;
   the plan touches only `API_DESCRIPTION` and `Envelope`.
   `ConfigDocument.config`/`.secrets` have no whole-document PUT,
   listings carry identity-keyed wrappers, and pending, runtime and
   conversation reads are not writable configuration. State that only
   the per-entity envelope's `entity` is resubmittable, mark the rest
   display-only, and explain that listing keys select the target URL.

7. **P2: custom titles conflict with omitting `type` under RFC
   9457.** With `type` absent (`about:blank`), the RFC says the title
   should be the recommended HTTP status phrase; custom semantic
   titles imply problem types the body does not identify. Use the
   standard reason phrases, or define type URIs; given the plan's
   aversion to a type registry, the standard phrases are the
   consistent choice.
