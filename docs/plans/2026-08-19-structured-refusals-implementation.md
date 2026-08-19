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
