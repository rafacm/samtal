# Structured refusals and the stated round trip: implementation

Companion to
[`2026-08-19-structured-refusals.md`](2026-08-19-structured-refusals.md).
One section per milestone, recording what was actually written, the
deviations from the plan, what building it turned up, and the
verification results as they came out. A milestone with no deviations
says so explicitly.

## M1: refusals as problem details

Every refusal the `/api` namespace answers is now an RFC 9457 problem
document served as `application/problem+json`, carrying the
repository's own sentence as `detail` and, where the refusal names
fields of the submitted fragment, one `errors` entry per field
addressed by JSON Pointer. The plan's design landed whole. Six
deviations and clarifications are recorded below; the fifth and sixth
are the only ones that changed a decision rather than a spelling.

`detail` is byte-identical to the sentence the API answered before,
with two exceptions, both deliberate and both recorded: the MCP
transport rule's line, which is the prose change the plan sanctioned
(deviation 2 below), and every refusal that used to name a key the
caller invented, which the review round's first finding narrowed. That
round is recorded at the end of this file, and it is why a pointer no
longer reaches an option key or an `env` entry.

### What was written

`models.py` declares `FieldProblem` (a `NamedTuple` of `path` and
`message`), `FieldProblemsError` (a `ValueError` carrying a tuple of
them) and `json_pointer`, beside the validators that produce them. The
three model-level validators raise the new exception:
`check_no_inline_secrets` (through a segment-carrying inner walk),
`McpServerConfig._check_transport_fields` with `_secret_problems`, and
`FillerConfig._check_phrases`.

`loader.ConfigError` gained a constructor taking an optional
`problems` sequence, defaulting to `()`. Every existing raise site and
all seven subclasses are untouched.

`store._validation_problems` walks a `ValidationError` once and returns
both renderings: the sentence, and the tuple of `FieldProblem` the
sentence was rendered from. `_load` is the one site that fills
`ConfigError.problems`; `_stored` and the domain-load walk take the
sentence alone, and say why in a comment. `_error_problems` is the
decomposition: a `FieldProblemsError` in the pydantic error's `ctx`
becomes its own pairs, and everything else becomes one pair at its own
location.

`api.py` gained `PROBLEM_TITLES` beside `PROBLEM_DESCRIPTIONS`,
`PROBLEM_MEDIA_TYPE`, `PROBLEM_SCHEMA` and `problem_response`, which is
the one place a refusal becomes bytes. Five emitters call it: the
`_refusal` handler (reading `exc.problems`), the `_BearerGate` 401, the
`_SanitizedErrors` 500, `_malformed_request`, and the new
`_routing_refusal` registered for `StarletteHTTPException`.
`responses.Problem` grew `title`, `status` and `errors`, and
`FieldError` was declared beside it, both required whole and
`extra="forbid"`.

### Deviations and clarifications

1. **The prose renders from the location, the payload from the
   pointer.** The plan says the walk produces `(pointer, message)`
   pairs once and renders both the sentence and the payload from them.
   Rendering the sentence from the pointer would have changed it: the
   provider inline-secret refusal has no location prefix today and
   would have acquired one, and the filler refusal's `filler:` prefix
   would have become `filler.phrases:`. Both are pinned as goldens, and
   the plan permits exactly one prose change. So the single walk
   produces, per problem, the location pydantic reported (for the
   sentence, unchanged) and the pointer (for the payload), from one
   decomposition of one error. "One computation, two renderings" is
   preserved; what is not shared is the spelling of the place, which is
   deliberate: the sentence uses the dotted spelling an operator's own
   file uses, and the payload uses the pointer a reader can act on.

2. **`str(FieldProblemsError)` is the messages newline-joined.** The
   store renders from the pairs, but `loader._format_validation_error`,
   which is the boot path, has only the sentence pydantic composed. A
   `; `-joined `str` would have left the boot rendering one line where
   the API's is three. Joining on newlines makes both render one line
   per problem, so the transport rule's recorded prose change applies
   at boot too, not only over HTTP.

3. **The transport rule's foreign-field problem carries the empty
   pointer.** Its sentence names several fields at once
   (`transport "stdio" has no url, headers; that belongs to the other
   transport`). One entry per named field would have repeated the line
   in the sentence; rewording per field was ruled out by the plan's
   "same words per problem". The empty pointer is what RFC 6901 gives a
   problem about the fragment as a whole, which is what a combination
   of keys is.

4. **`check_no_inline_secrets` walks segments, not a joined path.** The
   dotted path in the sentence and the pointer in the problem are now
   derived from the same tuple of segments, which is what keeps them
   from naming different keys. One side effect: the leaf that
   `is_secret_option` is asked about is the last segment rather than
   the text after the last dot, which differs only for a top-level
   option key that itself holds a dot. Both spellings refuse the same
   keys, because the rule is a substring test.

5. **405 joined both status mappings.** The plan lists six statuses for
   `PROBLEM_TITLES`. The framework emitter can answer 405, and
   `problem_response` looks its title up by status, so a seventh entry
   was needed; the coherence test holds the two key sets equal, so it
   joined `PROBLEM_DESCRIPTIONS` as well. No route declares 405, so the
   description reaches no operation in the document today; it is there
   because a status this API can answer belongs in the vocabulary that
   describes them.

6. **The refusal declarations name a schema, not a response model.**
   The plan flagged FastAPI's merge behavior as a risk. Declaring
   `{"model": Problem}` alongside a custom content key leaves the
   generated `application/json` in place, which is the trap. Dropping
   the model and declaring the content explicitly avoids it entirely:
   without a model FastAPI builds no response field and injects no
   content, and it also skips its own `HTTPValidationError` 422 for a
   route that already declares 422. `Problem` and `FieldError` are
   therefore injected into `components` beside the entity models. The
   whole-document pin asserts the outcome mechanically rather than by
   reading the diff.

### What building it turned up

- **pydantic carries the exception object itself.** On 2.13.4,
  `ValidationError.errors()` puts the raised `ValueError` in
  `ctx["error"]` as the instance, not a copy, so `isinstance` and the
  attributes on it both survive. This is the whole seam, and the first
  commit pins it against a throwaway model and a throwaway exception
  class so a release that dropped it fails loudly.
- **The conversation routes needed no change.** They raise the shared
  refusal types and build no body, so the new shape reached them by
  construction; the pin that says so is the only thing added for them.
- **The CLI needed no change either**, as the plan's evidence said:
  `_payload` accepts any content type holding `json` and `_answer`
  reads `detail` and ignores what it does not know. The compatibility
  test now runs a real command against a repository-backed API and
  compares the printed sentence against the same golden the HTTP test
  uses, rather than relaying a hand-built body.
- **`Problem` moved position in `components/schemas`.** It used to be
  collected by FastAPI from the response models and is now injected, so
  it sits with the injected schemas at the end. That is part of the
  regenerated document's diff and is not a contract change.
- **The document's remaining `application/json` occurrences are 48**,
  all of them success bodies and request bodies. No refusal declares
  it.

### The plan's open question

Untouched here: it is M2's. Nothing in M1 depends on how the
masked-resubmit case resolves.

### Verification

All from `samtal-server/`, at the last commit of the milestone.

- `uv run ruff check .`: `All checks passed!`
- `uv run pytest tests/unit -q`: 3098 passed, 16 skipped.
- `uv run pytest tests/integration -q`: 60 passed.
- The four generated references regenerated and byte-compared the way
  the workflow's drift steps do (`config reference`,
  `conversations schema`, `events reference`, `config openapi`): only
  `docs/reference/api-openapi.json` moved, and it moved in the commit
  that changed the wire. The other three are byte-identical to their
  committed copies.

No hardware was involved in this milestone, so nothing about a device
is claimed.

## PR review round

External review of the PR diff (`main...8f6bfde`): codex CLI 0.147.0,
model gpt-5.6-sol, read-only, 2026-08-19. Verdict: mergeable after the
listed fixes. Findings as received, condensed but faithful; each
carries its resolution and the commit that made it.

1. **P1: untrusted JSON keys leak verbatim through `detail` and
   `errors.path`.** `_validation_problems` serializes every pydantic
   `loc` segment without establishing that it is a schema-owned name,
   so a `PUT /agents/sam` body holding an unknown key returns that key
   in the sentence and as a pointer segment. `McpServerConfig`'s secret
   rule interpolates arbitrary `env` and `headers` keys into both
   surfaces independently. This contradicts the rule this repository
   already wrote down for a malformed `mcp` grant, that a key the model
   does not declare came out of the request and may be anything at all.
   The sentinel test planted credentials only in values under known
   fields, so it missed the path. Render only schema-owned names and
   positions, point at the nearest safe parent, make the arbitrary-key
   validator messages generic, and add API, CLI, exception-chain,
   header and log assertions with the sentinel placed in a key.

   *Resolution.* Adopted whole, in `f703715`. `models.safe_location` is
   the rule with one home: it walks a location against the model, a
   declared field descending into its own annotation and a position
   into a list's item type, and truncates at the first segment that is
   neither. `_validation_problems` puts every location through it and
   both renderings truncate together, so a pointer now stops at
   `/env`, at `/filler`, or at the empty pointer. `_grant_location`
   reads the same rule rather than restating it, and `_GRANT_FIELDS`
   is gone. An `extra_forbidden` error is rendered as
   `an unrecognized key is not permitted` at the object the key was
   written in, the error type being the decision site because it is a
   closed token where the message is not. `check_no_inline_secrets`
   takes a `declared` flag and keeps naming `api_key_env`, which is a
   field this repository declares, while an option and everything under
   it is refused by naming the closed fragment its name matched;
   `_secret_problems` does the same for `env` and `headers`, pointing
   at the group. Five planted-key cases assert absence from the
   sentence, every pointer, every message, the whole body, the headers,
   the log in both formats, and the exception's own `str`, `repr`,
   chain and `problems`. The CLI leg is the secrets suite's
   nested-option refusal, which now asserts that the key the operator
   wrote is absent from what the command printed.

   Three judgment calls inside those bounds, recorded rather than
   buried. The narrowing is applied at the repository boundary
   (`store._validation_problems`) and not to `loader`'s rendering of
   the YAML file half: a config file is a local artifact its operator
   wrote, the same trust level the moved-key and moved-variable
   refusals already print names from, and truncating there would cost
   an operator the diagnostic without a matching threat. The
   validators' messages did narrow everywhere, because a validator
   cannot know which boundary is rendering it. And two keys in one MCP
   group that matched the same fragment are one problem, since the two
   entries would be indistinguishable and a refusal that said the same
   thing twice would only suggest the second was about something else.

   One consequence worth naming: no pointer can now carry a `~` or a
   `/`, since every segment in one is a name this repository declared.
   The escaping stays, because the contract says RFC 6901 and a name
   that acquired one would otherwise silently address something else,
   and the test that used to reach it through a refusal is now a direct
   pin on `json_pointer`.

2. **P2: the plan's problem example contradicts the reviewed wire
   contract.** The example shows the custom title `"refused"` and the
   path `"type"`, while the settled design immediately below it
   requires the standard 422 reason phrase and RFC 6901 pointer syntax.

   *Resolution.* Adopted, in `684168e`: the example now reads
   `"title": "Unprocessable Content"` and `"path": "/type"`. It is the
   only edit this milestone made to the plan's body.

3. **P3: the release documentation claims every refusal sentence is
   byte-identical.** The changelog says the sentence and the CLI output
   are unchanged byte for byte, and eleven lines later correctly
   records that MCP transport refusals went from one semicolon-joined
   line to several. The implementation doc's overview repeats the
   unqualified claim.

   *Resolution.* Adopted, in `7041816`. Both claims now name both
   exceptions: the transport rule's prose change, which the plan
   sanctioned, and finding 1's narrowing, which landed in this round.

### Verification after the round

All from `samtal-server/`, at the last commit of the round.

- `uv run ruff check .`: `All checks passed!`
- `uv run pytest tests/unit -q`: 3110 passed, 16 skipped.
- `uv run pytest tests/integration -q`: 60 passed.
- The four generated references regenerated and byte-compared as the
  workflow's drift steps do: only `docs/reference/api-openapi.json`
  moved, carrying the sharpened `Problem` and `FieldError`
  descriptions, and it moved in `f703715` with the change that
  narrowed them. The other three are byte-identical to their committed
  copies.

## M2: the writable round trip

A read of one entity is now a write of it, and the API says so. The
behaviour half is the unchanged-value marker: on an entity PUT, before
validation, the repository walks the incoming fragment with the kind's
own `secret_key` predicate and replaces the mask with what the entity
already holds at that path, so a read resubmitted whole validates as if
the operator had retyped the value the display would not show them. The
contract half is one new `API_DESCRIPTION` paragraph and the two
sharpened `Envelope` field descriptions, which are committed bytes of
the OpenAPI document.

The plan's design landed whole. Six deviations and clarifications are
recorded below; the first and the third are the only ones that changed a
shape rather than a spelling.

### What was written

`store._write` gained one step. `_parse` split into `_readable`, which
is everything before validation that is about the fragment being a
fragment at all (an omitted body is the empty one, a body that is not a
mapping is refused, what JSON cannot carry is refused), and `_load`,
which is unchanged. Between them sits `ConfigStore._kept`, the marker.

`_kept` collects the paths where a secret-shaped key holds the mask
exactly (`_masked_paths`), and returns the fragment untouched when there
are none, which is every ordinary edit. Otherwise it reads the entity's
current entry, looks each path up on the stored side (`_held`), and
rebuilds the fragment with the stored value in place of the mask
(`_substituted`/`_inside`, copying only the containers along the path).
Paths with nothing stored behind them become one refusal
(`_mask_refusal`), a `ConfigError` carrying its sentence and its
`FieldProblem` entries, exactly as a validation refusal does.

`_refusal_line` is the one home for the shape of a problem line, read by
`_validation_problems` and by `_mask_refusal`, which has no pydantic
error behind it.

`API_DESCRIPTION` carries the round-trip paragraph, `Envelope.entity`
says the mask resubmits as keep-the-stored-value and no longer says the
opposite in passing, and `Envelope.secrets` says it is display-only and
that rotation is the secret PUT.

### Deviations and clarifications

1. **One seam, six functions.** The plan says "one helper in
   `store.py`". The write path gained exactly one call, `self._kept`,
   which is the seam a caller and every other part of `_write` sees;
   behind it are five module functions, each a separate question with a
   separate answer: which paths carry a mark, what the stored side holds
   at a path, how a value is put back without copying the whole
   fragment, what the refusal says, and how it is worded under the
   naming rule. Folding them into one function would have made one
   thirty-line body out of five things that are individually short and
   testable through the seam.

2. **The marker's read is its own transaction, before the write's.**
   Substituting inside the write transaction would have moved parsing
   and validation under the write lock, which `_write` deliberately
   keeps them out of. The read is therefore a second transaction, and
   what it can be stale by is the lost update a read-modify-write over
   HTTP already has: two operators editing one entity. What it
   substitutes is the model-shaped half an earlier read showed, never
   ciphertext, which no fragment can carry and no entity write replaces.

   **Wrong, and reversed by the review round's first finding below.** A
   marker-bearing write now takes the write lock first and resolves
   under it. This note is left as it was written because it is why the
   code shipped that way; what it got wrong is recorded with the
   finding.

3. **A stored null is nothing to keep.** `_held` answers "nothing" for
   an absent key and for a null alike, so a mask over a field the entity
   holds as null is refused rather than resolved to null. That agrees
   with the display's absence rule rather than departing from it: a
   field holding a default that means absence is left out of the read,
   so a mask can only have arrived there by hand.

4. **The walk depends on `_readable` having run.** The marker walks a
   fragment recursively, and a YAML anchor can make a structure that
   contains itself. `check_transportable` refuses exactly that, and it
   runs inside `_readable`, one line above the marker, which is what
   makes the walk terminate. The ordering is stated in both docstrings
   because it is not visible from either one alone.

5. **A secret-shaped key is not walked into.** The display displaces
   whatever such a key holds, structures included, so nothing under one
   was ever shown to resubmit, and the marker stops where the display
   stops. A mask under a key the predicate does not match is not a
   marker at all and meets validation as the string it is, which is
   pinned.

6. **Two refusal wordings, and identical problems are one problem.**
   The naming rule is `safe_location`'s, the same one every refusal
   built from a validation error goes through, so a declared field is
   named (`"api_key_env" holds the mask ...`) and a key the caller wrote
   is not (`a key in env holds the mask ...`, or `a key holds the mask
   ...` where there is no declared parent to name). Two marks that
   truncate to one place are one entry, for the reason
   `McpServerConfig._secret_problems` already gives: the entries would
   be indistinguishable, and saying the same thing twice suggests the
   second was about something else.

### What building it turned up

- **The per-kind round trip passed the moment it was written.** All
  five kinds round-tripped their own example fragment before the marker
  existed, which is #207's absence rule and `leads_with` doing what they
  were for: a read is already write-shaped in structure and in order.
  The masked values were the whole of the gap, which is what the review
  round's first finding said and what the eight tests that fail without
  the marker measure.
- **Only the provider declares a secret-shaped field name.** Of the five
  kinds' models, `api_key_env` is the only declared field name its
  kind's predicate matches, so the "name the field" wording is reachable
  through it and the "name no key" wording through everything else: a
  provider's options at any depth, and an MCP server's `env` and
  `headers`, which are keyed by whatever was written.
- **A planted row round-trips only if what was planted is writable.**
  The marker substitutes the stored value and validates the result, so a
  row carrying a value the models would refuse is refused on resubmit.
  That is the promise rather than a hole in it, "validates exactly as if
  the operator had retyped it", and it is why the planted-row test
  plants a credential shaped like an environment name: the case the
  display exists for, a value that got past the models and must not be
  shown, and the case an operator meets.
- **The CLI needed nothing**, as the plan said: the marker is the
  repository's, so `--local` writes inherit it, and a `set` over the API
  inherits it from the server. The pin is a document `show` printed,
  sent back through `set`, and shown again identical.

### The plan's open question

Closed, and closed the way the review round's first finding forced:
**marker plus contract**, not contract alone. Contract alone was
disproved by writes the API accepts today, and the alternative the issue
named, a writable projection distinct from the display envelope, was not
taken: the marker makes the display envelope the writable projection, so
there is one shape for a reader and a writer rather than two that can
come to disagree. That is also the resolution #194's per-entity export
inherits.

### Verification

All from `samtal-server/`, at the last commit of the milestone.

- `uv run ruff check .`: `All checks passed!`
- `uv run pytest tests/unit -q`: 3124 passed, 16 skipped.
- `uv run pytest tests/integration -q`: 60 passed.
- The four generated references regenerated and byte-compared the way
  the workflow's drift steps do (`config reference`,
  `conversations schema`, `events reference`, `config openapi`): only
  `docs/reference/api-openapi.json` moved, carrying the round-trip
  paragraph and the two sharpened `Envelope` descriptions, and it moved
  in the commit that changed the prose. The other three are
  byte-identical to their committed copies.
- The marker was bitten: with the walk that collects the marks disabled,
  eight of the fourteen new tests fail (both masked resubmits, all three
  nothing-stored refusals, the planted row, the sentinel and the CLI
  leg) and the five per-kind round trips and the not-secret-shaped case
  pass, which is the measure of what the marker is for. Restored by
  copy-aside and `touch`.

No hardware was involved in this milestone, so nothing about a device is
claimed.

### PR review round

External review of the PR diff (`main...86b3dd8`): codex CLI 0.147.0,
model gpt-5.6-sol, read-only, 2026-08-19. Verdict: mergeable after the
listed fixes. Findings as received, condensed but faithful; each carries
its resolution and the commit that made it.

1. **P1: masked PUTs break write serialization and can resurrect a stale
   hidden value.** The marker read the stored value in one
   `BEGIN IMMEDIATE` transaction, released it, and then opened another to
   persist, which contradicts the one-transaction guarantee this module's
   own docstring states. If A resolves a mask to X, B changes or removes
   that path, and A then writes, A restores X: no serial ordering
   produces that, and the marker did not keep the value stored when A
   wrote. Acquire the write transaction before reading the current
   entity, resolve and validate the marker while holding it, and run
   reference checking and persistence without releasing it. Add a
   deterministic concurrent-writer test modeled on
   `test_two_concurrent_writers_serialize`.

   *Resolution.* Adopted whole, in `050d11b`. A fragment carrying a mark
   now takes the write lock first: the domain is read once inside that
   transaction, the marks are resolved against the entry that read holds,
   the fragment is validated, and the reference pass and the row write
   follow without the lock being released. The one read serves both the
   resolution and the reference pass, so the marked path costs no more
   reads than it did. A fragment with no marks depends on nothing stored
   and keeps the shape it had, parsed and checked before the lock is
   asked for, which is the property `_write` had before this milestone.
   `_parsed` and `_persist` are the two halves both paths now share,
   extracted rather than repeated, and `_keep` takes the stored entry
   instead of fetching one, which is what makes the caller's transaction
   the whole of its correctness.

   The correction, because deviation 2 above argued the other way. It
   called the gap the lost update a read-modify-write already has, and
   that was wrong: the operator's read-modify-write window is the
   operator's, spanning two requests they made themselves and can be
   told about, while this window was inside one server-side write call,
   which is the unit this repository promises is atomic. The two are not
   the same kind of thing, and the second is not excused by the first.
   The deviation note is left standing with the correction on it rather
   than rewritten, since what it claimed is why the code shipped that
   way.

   The new test is the sibling of the one the finding names, paced the
   same deterministic way: the resubmitting writer announces that it has
   looked the stored value up and waits for the other to finish. Under
   one lock that wait always times out, because the other writer cannot
   begin while this one holds it and the busy timeout is twenty times the
   wait; under two it always resurrects. Reverting `_write` to the two
   transactions makes it fail with the planted credential back in the
   row, which is how the pin was checked.

2. **P2: the OpenAPI contract never identifies the unchanged-value
   marker.** `API_DESCRIPTION` and `Envelope.entity` said only "the
   mask", and the generated document held no `********` anywhere, while
   the plan requires stating that a value shown as `********` means keep
   the stored value. Derive both descriptions from the shared `MASK`
   constant, regenerate the document, and pin the exact marker in an
   OpenAPI test.

   *Resolution.* Adopted, in `2223ccb`, with one judgment call recorded
   rather than buried. `api.py` derives its sentence from `secrets.MASK`,
   which it already imports. `responses.py` cannot: the import that would
   derive it closes a cycle, since `responses` sits under `entities`,
   which sits under `loader`, which is what `secrets` imports, and it was
   tried and raised exactly that. So that one description writes the
   literal out, its module docstring says why and where the two are held
   equal, and the pin asserts the constant appears in both descriptions
   of the rendered document, which is the byte a client reads.

   Two alternatives were weighed and declined. Moving `MASK` down to
   `models.py`, the only shared ancestor, would derive by construction,
   but it puts a display constant in the models module and hands
   `responses.py` the heaviest import in the package, against the rule
   its docstring exists to state. Defining it in `responses.py` for
   `secrets` to read is acyclic and worse: a security module would read
   its central display constant out of the module of HTTP shapes. A
   duplicated eight-character literal held equal by a pin that fails
   loudly is the smaller cost.

### Verification after the round

All from `samtal-server/`, at the last commit of the round.

- `uv run ruff check .`: `All checks passed!`
- `uv run pytest tests/unit -q`: 3126 passed, 16 skipped.
- `uv run pytest tests/integration -q`: 60 passed.
- The four generated references regenerated and byte-compared as the
  workflow's drift steps do: only `docs/reference/api-openapi.json`
  moved, carrying the mask literal in the two descriptions, and it moved
  in `2223ccb` with the change that stated it. The other three are
  byte-identical to their committed copies.
