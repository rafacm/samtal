# vinga info, and a CLI polish round: implementation

The companion to
[`2026-08-29-vinga-info-cli-polish.md`](2026-08-29-vinga-info-cli-polish.md),
one section per milestone, appended in the change that ticks the
milestone. It records deviations from the plan, resolutions of anything
the plan left open, and discoveries; a milestone with no deviations
says so explicitly.

## M1: `vinga info`

PR #347.

### What landed

In the order the commits tell it: the route, then the row, then the
generated documents, then the tests, then the pages the change
falsified.

- **`RuntimeInfo` in `config/responses.py`**, strict like every model
  there, with five fields: `version`, `revision`, `onboarding_enabled`,
  and the nullable `onboarding_url` and `onboarding_provenance`. Its
  docstring carries the security argument rather than leaving it in a
  plan, because the model is what a client generator reads.
- **`GET /runtime/info`**, first route of the `_runtime` namespace,
  answering from `ApiRuntime.identity` with `Cache-Control: no-store`
  on the way back and `_problems(401, 503)` in the document. The 503
  has a description of its own (`api_descriptions/no-runtime-info.md`),
  because the shared sentence says the reads in this namespace answer
  emptily and there is no honest empty identity, which is the prompt
  read's and the diff's argument met a third time.
- **`api.py` imports nothing new from `onboarding`.** The identity
  reaches it as a value on `ApiRuntime`, filled by the composition root
  (`app.py:_runtime_identity`), which already sits downstream of
  `onboarding.origin`. `test_onboarding_import_weight.py` is untouched
  and green.
- **`build_api` and `build_api_runtime` gained `identity` as a
  trailing defaulted parameter**, so every existing positional call
  still means what it meant.
- **The `info` row**, flat, first of the flat verbs, two acts in order
  (`IDENTITY` on the new route, then `COUNTS`, which is `GET /config`
  rendered as a count per kind). The kinds and their order come from
  `entities.ENTITIES`: a kind addressed by no segment is the singleton
  and has no count, one addressed by two is nested a level deeper, and
  the devices and the default agent are written out for the reason
  `_summary` writes them out.
- **The pages this falsified**: the cli-guide's flat-verbs section and
  its credential practice, the root README's step 5, and the paragraph
  in `vinga-server/README.md` that said `ota-url` was the one place the
  URL is printed.

### Deviations from the plan

Two, both small, both in the same direction: the plan specified what to
print and this milestone had to decide how.

1. **`Command` gained one field, `opens`.** The plan says the row runs
   two acts and prints the contacted address before either, and says
   `conversation show` proves no new machinery is needed for the two
   acts. That is true of the acts and not of the line in front of them:
   an act's renderer is handed the answer and nothing else, which is
   what keeps a rendering a function of what came back, and the address
   is a fact of the invocation that no answer carries. Rather than give
   an act the invocation (the `Act` interface change M3 owns) or fold
   the print into a `declare` function (whose job is the argument
   shape, one per shape rather than one per command), `Command` carries
   an optional callable that runs before the first act. One row uses
   it. `Command.acts()` is unchanged, so the API contract test still
   enumerates coverage from the row.

2. **The two long values are bounded at their own length, not at
   `GLIMPSE_LENGTH`.** `printable`'s default bound is 120 characters,
   which is right for a glimpse of far-side text quoted inside a
   sentence and wrong for both values here: the guessed-origin
   provenance `onboarding.origin` composes runs to 191 characters and
   was truncated mid-clause in the live lane, losing the fix it ends
   with, and a long origin would have truncated the URL itself, which
   is the one value in this output an operator retypes by hand. So
   `IDENTITY_LENGTH = 512` is passed for those two. They are still
   bounded, and for the two reasons everything an answer carries is
   bounded: no answer chooses how long a command's output is, and
   nothing an answer carries steers a terminal.

   *Superseded by the review round below, finding 2.* The reasoning
   above is right about why 120 is wrong and wrong about 512 being
   enough: nothing bounds `server.public_url`, so no number is. The two
   values are printed whole.

### Discoveries

- **The census was already stale on the plan branch.** `docs/plans/
  2026-08-29-vinga-info-cli-polish.md` landed without regenerating
  `tests/unit/command-spellings.txt`, so `test_the_manifest_is_the_census`
  was failing before this milestone began. The regeneration in this
  milestone's last documentation commit clears it along with its own
  additions.
- **A generated document carries one spelling, and a response model's
  description is a generated document.** The first draft of
  `RuntimeInfo.onboarding_url` said `vinga-server config ota-url`,
  which is the spelling source files use and the one
  `test_a_generated_document_carries_one_spelling` refuses in
  `api-openapi.json`. The canonical spelling in anything rendered is
  `vinga <verb>`. Worth knowing before writing prose into a model.
- **The lane's own onboarding URL is a guess, and that is the honest
  answer.** `tests/support/deployment.py` boots on an ephemeral port
  while the configuration's `server.port` is the default, so
  `public_origin` guesses from the listen address and says so at
  length. The live-lane case asserts the whole provenance sentence
  including its fix, which is what caught the truncation above.
- **The onboarding key is exactly eight characters**, in an alphabet
  with no `0`/`O` and no `1`/`I`/`l`. A sentinel test that hunts for
  the key segment on its own has to allow for that rather than assume a
  long token.

### Verification

- `uv run ruff check .`: clean.
- `uv run pytest tests/unit -q`: green.
- `uv run pytest tests/integration -q`: green against the Postgres this
  machine was already running with the compose defaults.
- The wheel lane's `info` case is run by the same integration command;
  the image lane is CI's.
- Not verified here: nothing on hardware. M1 adds no board or device
  procedure, and no page under `docs/devices/` speaks about the API.

### PR review round

External review of PR #347, verdict mergeable after fixes. Four
findings, condensed but faithful, each with the resolution and the
commit that carries it. Every one of them was confirmed against the
merged code before it was fixed, by reverting the fix's source half and
running the case it added.

1. **P1: the counts act could print rejected response data or escape
   with a traceback.** `ConfigDocument` declares the masked
   configuration as `dict[str, Any]` and stops there, and
   `_configured_counts` trusted every type under it: a `default_agent`
   that was an object was printed as its repr, a section that was a
   number or a list raised a `TypeError` out of the boundary, and an
   absent section raised a `KeyError`. The malformed-answer cases that
   existed replaced the first act's response, which is refused before
   the second act runs, so the renderer had never met a body it did not
   compose itself.

   *Resolution*: adopted, `9080b17f`. Every section is read as a shape
   through `_understood`, with the nesting taken off the descriptor's
   addressing rather than a second list, so an unreadable one meets the
   fixed chainless sentence. Seven second-act cases cover the wrong
   container, the wrong scalar and the absent section, across both
   streams, both log formats and the exception chain. Confirmed against
   the old code, which printed the planted value on stdout.

   Not fixed here, and recorded rather than left silent: `_summary`
   renders the same document with the same trust, so `vinga list` has
   the shape of this bug for the sections it walks. It is not this
   change's code and deserves its own.

2. **P2: a valid configuration could produce a silently truncated
   URL.** `server.public_url` accepts an origin with a path prefix and
   bounds neither, so a 642-character public URL composes a
   654-character onboarding URL, of which 512 rendered.

   *Resolution*: adopted, `5f87c62f`, taking the review's first option.
   No number fixes it, because no number bounds what a configuration
   can legally hold, so the choice was between refusing a configuration
   nothing else refuses and printing the value whole. Whole, which is
   the call `_block` already makes for a prompt and for the same
   reason: a renderer that quietly cuts the thing it exists to show
   makes it lie, and a URL cut at any length is typed into a captive
   portal and fails there silently. `printable` gains `None` as its
   bound, documented as a different rule rather than a bigger number,
   and the half with no exceptions is unchanged: nothing an answer
   carries steers a terminal.

3. **P2: `RuntimeInfo` accepted contradictory onboarding states.** The
   flag and the two nullable fields were independent, and the renderer
   branched on the URL while the sentence came from the flag.

   *Resolution*: adopted, `2a2c4baa`. A model validator makes the three
   one fact: onboarding on with both answered, or off with both null.
   Refused rather than reconciled, since reconciling is picking a half
   to believe, and nothing is quoted back. The renderer asks the flag
   now. All six inconsistent shapes and both consistent ones are pinned
   through `IDENTITY.read`.

4. **P2: the displayed API address was not necessarily the one either
   act contacted.** `_call` re-read the file half and re-resolved the
   address and the token per request, and the opener resolved a third
   time, so `info` performed three independent resolutions of one
   question and a file changing under a running command could put one
   endpoint on the banner and another behind the answers.

   *Resolution*: adopted, `4276c9a9`. `Reached` is what an invocation
   resolved about where it is talking and with what, built once by
   `Command.perform` in front of the opener and every act. The
   simulator's claim resolves its own inside the `--claim` arm, which
   is what keeps the device side clear of the operator-side credential.
   The test counts the reads of the file half and moves the port under
   the command; the old code read three times.

## M2: the polish

PR TBD.

### What landed

Four commits, each a change and the pages it falsified: the bare
invocation, the description, the relocation, the changelog.

- **A bare invocation is answered with its own help page.** The parse
  is still left to fail the way it always did, and `_page_instead`
  turns that one failure into the page, off the context the mistake
  carries, so a bare sub-noun (`vinga device pending`) gets its own page
  rather than the root's. It leaves through `ConfigError` like every
  other answer to an invocation that was not a completed command:
  stderr, exit 1, chain empty. Not `no_args_is_help=True`, which is
  wrong twice (stdout for something that is not data, and a 0 that says
  a command completed).
- **The root description**, in the vocabulary of the person reading it
  rather than this repository's. The hand-written head of
  `docs/reference/cli.md` is reordered the same way, keeping the
  definition of the two halves for the sections that use the term
  later.
- **`status` under its noun**, row moved whole so `_status_block`
  renders the same bytes, `_ORDER` dropping the word by itself because
  it is derived from the table. No alias.
- **The respell sweep**, in the same commit as the move, so no commit
  ships a document naming a command the tree does not have.

### Deviations from the plan

Three, all small, two of them corrections to the brief rather than to
the plan.

1. **The flat-verbs section had no `status` to lose.** The plan and the
   brief both say that section "gains `info` and loses `status`". It
   gained `info` in M1; it never listed `status` at all, on `main` or
   on this branch. What landed instead is a labelled historical
   counterexample there, because the section's rule is exactly the one
   the relocation is an instance of: a verb whose subject is one noun
   does not belong beside the verbs whose subject is the deployment.
   Written in the long spelling, which is the guide's own rule for a
   quotation of what the grammar used to be (the short spelling states
   the live standard, and the census guard enforces the split).

2. **Two more spellings than the census listed, both templated.**
   `models.py:1758` and `entities.py:534` compose their descriptions
   with `f"{PROGRAM} status"`, and an f-string interpolation is not a
   quoted invocation, so the sweep cannot see either. They are the same
   fact as `models.py:1727` and regenerate into the same two documents.
   Found by regenerating `domain-config.md` and seeing it not change
   when it should have.

3. **The `_runtime` docstring needed no extension.** The plan licenses
   one "if it reads naturally"; M1 had already added the `info`
   sentence to it, and its collision rationale is about an entry legally
   named `status` shadowing a runtime route, which a CLI respell does
   not touch. The README's copy of that rationale did gain a sentence,
   because a reader who has just typed `vinga mcp-server status` will
   otherwise read the next paragraph as a contradiction: there the word
   is in the verb slot, where an entry name never is.

### Discoveries

- **A bare group's help page comes off the exception, not off the
  tree.** Click's `Context.fail` attaches the failing context to the
  `UsageError`, and for `vinga device pending` that is the innermost
  group's context, still carrying its parent chain. So one branch at the
  boundary answers every depth, and there is nothing to walk: no lookup
  of "which group did they stop at", which would have been a second
  place the tree is traversed.
- **The refusal that moved was a deletion, not an edit.** The live
  lane's refusal table is keyed by family, and `family_of` reads the
  noun path off the row, so `("mcp-server", "status")` belongs to a
  family that already had its refusal. The `("status",)` row had to go
  rather than be re-pointed, or the table would have had two entries for
  one family and failed the completeness pin.
- **The census was stale again when this milestone began.** M1's last
  commit substituted its PR number into two plan documents, which moved
  the lines the manifest records. Regenerating it is the last
  documentation act of a milestone for exactly this reason, and it is
  worth expecting rather than debugging.

### Verification

- `uv run ruff check .`: clean.
- `uv run pytest tests/unit -q`: 4396 passed, 19 skipped.
- `uv run pytest tests/integration -q`: 212 passed, against the Postgres
  this machine was already running with the compose defaults.
- `python3 scripts/check_doc_links.py .`: 164 files, 0 failures.
- Not verified here: nothing on hardware, and nothing in the image lane,
  which is CI's. M2 adds no board or device procedure.
