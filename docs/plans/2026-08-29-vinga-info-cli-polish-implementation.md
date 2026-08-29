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

PR #352.

### What landed

Four commits, each a change and the pages it falsified: the bare
invocation, the description, the relocation, the changelog.

- **A bare invocation is answered with its own help page**, off the
  context of the group that was left without a verb, so a bare sub-noun
  (`vinga device pending`) gets its own page rather than the root's. It
  leaves through `ConfigError` like every other answer to an invocation
  that was not a completed command: stderr, exit 1, chain empty.

  *Superseded in part by the review round below, findings 1 and 2.* The
  first cut recognized the invocation by the wording of Click's message
  and read the `.env` in front of the parse; the mechanism is now a
  group class raising a typed exception, and the read has moved to the
  command it is for.
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
  tree.** The exception a group raises carries that group's own
  context, and for `vinga device pending` that is the innermost group's,
  still holding its parent chain. So one arm at the boundary answers
  every depth, and there is nothing to walk: no lookup of "which group
  did they stop at", which would have been a second place the tree is
  traversed. True of the wording-based first cut and of the class-based
  one the review round replaced it with, which is why the fix was a
  change of discriminator and not of shape.
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

Re-run after the review round below; the counts are that run's.

- `uv run ruff check .`: clean.
- `uv run pytest tests/unit -q -n auto --dist loadfile`: 4423 passed,
  19 skipped.
- `uv run pytest tests/integration -q`: 212 passed, against the Postgres
  this machine was already running with the compose defaults.
- `python3 scripts/check_doc_links.py .`: 164 files, 0 failures.
- Not verified here: nothing on hardware, and nothing in the image lane,
  which is CI's. M2 adds no board or device procedure.

### PR review round

External review of PR #352, verdict mergeable after fixes. Four
findings, condensed but faithful, each with the resolution and the
commit that carries it.

1. **P2: a command line could type its way to the help page.** The
   boundary told a bare invocation apart by looking for Click's words
   "Missing command" in the message it composed, and that message is
   composed around what was typed: `vinga "Missing command"` is an
   unknown command whose name is the marker, and `vinga list "Missing
   command"` is an argument too many carrying it. Both got a help page
   instead of the fixed refusal for the mistake they really were.

   *Resolution*: adopted, `759f06d9`. Every group is a `_Grouped`,
   which decides the question itself from the context's own record of
   what is left to resolve and raises `NoArgsIsHelpError`, Click's own
   class for that meaning and raised nowhere else here. The boundary
   answers a class rather than a wording. Deciding it in the group
   rather than leaving it to the library also keeps the invocation with
   options and no command (`vinga --api-url URL`), which Click's own
   no-args flag does not see, and the two private attributes the
   decision reads are named rather than felt for, so a Typer rename
   fails on the first invocation instead of answering that every
   command line is bare. The cli-guide gains the general rule: a
   boundary that matches words in a message has made the message an
   input.

2. **P2: a broken `.env` pre-empted the page it was supposed to
   print.** The read sat at the mouth of the boundary, in front of the
   parse, so a bare `vinga` in a directory holding an unreadable `.env`
   answered `DOTENV_UNREADABLE` rather than its help, contradicting the
   changelog entry shipped beside it.

   *Resolution*: adopted, `9e6325da`. The read moves one frame in, to
   `_Verbatim.invoke`, which is the last moment before a command runs
   and the first at which it is known that one will. Still inside the
   boundary, still before anything looks at the environment, so every
   command behaves exactly as before and no invocation that runs no
   command opens the file at all. That makes three answers that need no
   environment (`--version`, `--help`, a bare invocation) where the
   dotenv suite's section had recorded one, and the suite now drives
   the root, a noun and a sub-noun with the planted `.env` in place,
   over both streams, the log and the chain.

3. **P2: the CLI reference described only explicit `--help`.** The
   behavior this milestone changed was written down in the guide that
   explains why the grammar has its shape, and not on the page a reader
   opens to find out what it does.

   *Resolution*: adopted, `26ce722b`. A section of its own in the
   hand-written half, where the plan's documentation footprint put it:
   the two ways to ask, what differs (stderr and exit 1, so a redirect
   gets an empty file and a script still reads it as a failure), which
   page a bare noun prints, that neither way needs a `.env` or a
   server, and that a word the grammar does not have gets the refusal
   rather than a page.

4. **P2: the milestone's PR number was still a placeholder.** Both the
   plan's checklist tick and this section said "PR TBD".

   *Resolution*: adopted, in this commit, with the census manifest
   regenerated beside it because these are the milestone's last
   documentation edits.

## M3: `apply` reloads by default

PR #353.

### What landed

Five commits: the mechanism, the migration, the new pins, the lanes
that drive a real server, and the pages this falsified.

- **`apply` writes the document and installs it.** Two acts on one row,
  and which of them an invocation runs is a hook on the row
  (`Command.selects`, a callable from the invocation to the acts) rather
  than a tuple cut down after the fact. `Command.acts()` still answers
  the full static set, so the API contract check enumerates coverage
  from what the row can reach rather than from what one invocation ran.
- **Two renderings, and they are two rows.** `APPLY_QUIETLY` and
  `APPLY_RELOAD` are `dataclasses.replace` of `APPLY` and `RELOAD`, so
  the request half (method, path, body, shapes, both timeouts) cannot
  drift from the rows the contract check holds. What differs is the
  rendering: `_applied` keeps the per-entity boundary notices, which is
  `--no-reload`'s answer, and `_applied_quietly` drops them, because the
  reload's listing on the next line is the boundary being crossed.
- **`_performed`**, which runs one invocation's acts and stops at the
  first refusal, so a refused document never reaches the reload. An act
  that failed behind an act that already changed something adds what its
  row says is now unknown (`Act.unanswered`), and the composed sentence
  is built inside the handler and raised outside it, so nothing walks
  from it to what the failure was carrying.
- **`COMMITTED_UNANSWERED`**, the sentence itself: the write committed,
  no completed reload answer arrived, run `vinga diff` and then
  `vinga reload`. It claims nothing about the running server, for the
  two reasons the plan gives, and both are pinned (a held reload's 409
  in the rendering suite, an ambiguous transport failure in the
  transport suite).
- **The export header is three steps now**: stage, enter the
  credentials, reload. A reload builds the engines a document names and
  a document never carries their credentials, so a rebuild that
  installed on its way past would refuse in the middle of a recovery.

### Deviations from the plan

Four, three of them the same shape: the plan named the behavior and the
milestone had to decide where the seam goes.

1. **`Act` gained a field, but not the one the plan implied.** The plan
   says the change is a small `Command`/`Act` interface change, and M1's
   note anticipated M3 giving an act the invocation. It did not, and did
   not need to: two rows differing only in `render` say "which rendering
   this invocation gets" without handing a renderer anything but the
   answer, which is the property that keeps a rendering a function of
   what came back. What `Act` did gain is `unanswered`, which is a fact
   about what an act follows rather than about its answer, and which the
   plan's committed-but-unanswered sentence needs a home for.

2. **The export header, and two committed artifacts with it.** The plan
   names the READMEs and the cli-guide; the header `export` prints is
   the same promise in code, and it told an operator to apply a document
   whose credentials arrive afterwards. Changing it moved
   `tests/integration/data/pre-cutover-export.yaml`, which is compared
   byte for byte against a fresh export and therefore carries whatever
   header this build prints, and it needed a new entry in the
   respelling differential's substitution table. That table is the
   rename's, so the entry is labelled as not the rename's and the
   docstring says an entry with no reason named is what the table must
   never grow.

3. **Three lanes depart from verbatim, and each says so where it does.**
   The plan asks for the preset test to be renamed; the published-recipe
   runner needed the same treatment for the same reason (installing a
   preset builds what it names, which here is a model download and an
   Ollama nobody is running), and `config.deploy.example.sh` is run
   verbatim by the integration lane and could not be staged from
   outside, so it stages in its own text. The last one is not only a
   test convenience: a seeding script should not choose the minutes a
   deployment spends downloading ASR weights.

4. **The row's help sentence is one sentence.** The first draft ended
   with a second (`. --no-reload stages the write instead`), which
   `test_every_description_is_one_lowercase_sentence` refuses. It reads
   `; or write it without applying, with --no-reload`, and the flag at
   the end rather than mid-clause is also what keeps Click from wrapping
   it as `--no-` / `reload` in the rendered listing.

### The apply-test migration

Inventoried by grep (`grep -rn '"apply"' tests/`): 36 matches across
eight files, of which 32 are command lines and four are not (the
retired-word list in the spelling census, the never-destroys row in the
confirmation suite, a temporary directory named `apply`, and a refusal
family key).

Twenty-three command lines took `--no-reload`, by what they are about:
storage semantics and idempotence (the acceptance spine's apply
section), the round trip in both directions, the transportability guard,
the transport suite's request-shape and no-narration cases, the apply
bound, both recovery rebuilds and the cutover, the export round trip
over the wire, and both lanes' bootstraps. Eight kept the default
spelling deliberately: every one of them is a refusal, and a refused
document never reaches the second act, which is worth having driven.

Two lanes stage programmatically rather than by editing what they run:
the published-recipe runner inserts the flag (`_staged`), and the
preset test is renamed `test_a_preset_stages_onto_an_empty_store` with
the departure in its docstring.

The default is driven where a reload can answer: four cases in the
rendering suite against an injected runtime, two in the transport suite
against mock transports, and one over the wire in the live lane, on a
document of its own after the reload test. What that one asserts is the
write's lines, the reload's listing under them, an empty stderr, and
then a read of the running process (`agent preview`) carrying text that
exists only in the document just applied, which is what makes it a
claim about the world that was installed rather than about the headings
a reload prints either way.

### Discoveries

- **The refusal a default apply meets in a lane is the real one.** The
  published-recipe case and the deployment-profile script both failed
  with `the reload was refused ... the engines the stored configuration
  names could not all be built`, which is exactly the sequence an
  operator would meet if a rebuild installed before its credentials were
  entered. The export header change is that failure written down.
- **A committed artifact can carry a generated header.** The
  pre-cutover export is documented as a record nothing in this
  repository can produce again, and its header is nevertheless whatever
  the current build prints, because the test compares the two byte for
  byte with only the secret-set annotations taken off. Worth knowing
  before changing anything an export opens with.
- **`Reached` made the second act free.** M1's review round moved
  address and token resolution to once per invocation, so adding a
  second request to `apply` needed no work at all: both acts go where
  the command said it was going.

### Verification

- `uv run ruff check .`: clean.
- `uv run pytest tests/unit -q -n auto --dist loadfile`: 4421 passed,
  19 skipped.
- `uv run pytest tests/integration -q`: 213 passed, against the Postgres
  on 127.0.0.1:5432.
- `python3 scripts/check_doc_links.py .`: 164 files, 0 failures.
- The spellings census runs inside the unit lane and is green with the
  manifest regenerated in the documentation commit.
- `docs/reference/api-openapi.json` is unchanged, which is the honest
  answer rather than a step skipped: nothing about the API moved, and
  the generator was run to check.
- Not verified here: nothing on hardware, and nothing in the image lane,
  which is CI's. M3 adds no board or device procedure.
