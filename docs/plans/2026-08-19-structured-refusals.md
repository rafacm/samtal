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
distinct from the display envelope. Resolved below: contract alone,
with the proof as tests.

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
- The masked-resubmit facts, which are what make the round trip
  statable without a second projection:
  - `models.check_no_inline_secrets` (`models.py:766`) refuses a
    secret-shaped key at any depth of a provider's options, whatever
    the value, naming the dotted path and never the value. A
    resubmitted `********` is refused loudly, not stored.
  - `McpServerConfig._secret_problems` (`models.py:1150`) requires a
    `$VAR` reference for a secret-bearing `env` or `headers` key, so a
    resubmitted mask is refused there too. The nested case is
    unreachable through the store (both maps are typed
    `dict[str, str]`), recorded in the #207 feature doc as the
    write-time depth question, and is not this plan's to move.
  - `secrets.mask` (`secrets.py:161`) passes only syntactically valid
    environment references through, and a reference resubmits as
    itself. So for any row written through validation, the read body
    is byte-for-byte the fragment a write accepts; `********` appears
    only for rows stored outside validation, and PR #207's absence
    rule already keeps every read write-shaped (its feature doc's own
    sentence).

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
  objects, `path` the dotted location inside the submitted fragment
  (empty string for a fragment-level problem), `message` the
  same text the corresponding `detail` line carries. It is always
  present and `[]` when the refusal has no field decomposition, so
  every refusal has one shape, the rule the response models already
  follow.
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
`message`) declared beside it in `loader.py`, which imports nothing
new. Subclasses inherit it untouched, and every existing raise site
compiles unchanged.

Exactly one site fills it: `store._load`, where
`_validation_problems` already walks `ValidationError.errors()`. The
walk is refactored to produce the pairs once and render both the
sentence and the payload from them, so the prose and the structure are
one computation.

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

### The round-trip contract

Stated in `API_DESCRIPTION` (one new paragraph) and sharpened in
`Envelope.entity`'s and `Envelope.secrets`' descriptions, all of which
are committed bytes in the OpenAPI document:

- A read's `entity` is writable as-is. PUT replaces the model-shaped
  half and never touches stored secrets, so an edit is read, modify,
  resubmit whole; fields the read omitted (the absence rule) stay
  omitted and mean the same absence on the way back.
- An environment reference reads back as itself and resubmits
  harmlessly. `********` appears only for a value that entered storage
  outside this API's validation, and resubmitting it is refused with a
  sentence naming the field, never stored.
- An unchanged stored secret needs no action on resubmit: the slots in
  `secrets` are informational, and rotating a credential is the secret
  PUT, the only door plaintext enters by.

No writable projection distinct from the display envelope. The three
facts above make the display envelope the writable projection, which
is the resolution #194's per-entity export inherits.

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
- The masked resubmit: a row planted with an inline secret through the
  engine (the existing unreadable-row tests' technique), read back
  masked, resubmitted, refused with the field named and the planted
  value absent from the whole response.

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
- [ ] **M2: the round-trip contract** (PR TBD). The
  `API_DESCRIPTION` paragraph and `Envelope` description sharpening,
  the regenerated document, the per-kind round-trip tests, the
  masked-resubmit test, CHANGELOG. Design footprint: deepens the
  document's prose surface and the API test suite; it adds no code
  path, which is the point, and records in the implementation doc that
  the issue's open question closed as contract-alone.

Both milestones leave `main` releasable: M1 changes the refusal body
shape in one merge with its document, and M2 is prose and tests.
