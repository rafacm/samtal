# Rename the two-clock verbs: implementation

Companion to
[`2026-09-03-two-clock-verb-rename.md`](2026-09-03-two-clock-verb-rename.md).
One section per milestone, recording what was actually written, the
deviations from the plan, what building it turned up, and the
verification results as they came out. A milestone with no deviations
says so explicitly.

## M1: the rename, whole

### What was done

`config/entities.py`. `RELOAD_NOTICE` became `APPLY_NOTICE`, one line
saying the write is stored and not yet serving and naming both
`vinga apply` and `vinga diff`. `BINDING_UNSERVED_NOTICE` kept its two
facts and respelled the command. The other three notices are untouched,
as the plan says, since no clock they name moved.
`tests/support/notices.py` changed one entry of its phrase table
(`RELOAD` is announced by `vinga apply`); the four boundary tokens did
not move, which is why every downstream suite that asserts tokens
stayed green through the shortening.

`config/cli.py`. The write act `APPLY` became `IMPORT`
(`_import_path`, still returning the API's apply route;
`IMPORT_READ_TIMEOUT_S`, still None), and the install act `RELOAD`
became `APPLY` (`_apply_path` on the API's reload route,
`APPLY_READ_TIMEOUT_S` at sixty seconds, `UNREADABLE_APPLY`,
`_apply_listing`, `APPLY_SECTIONS`). Both rows carry the seam comment
the plan asks for: the API names the mechanism, the CLI names the act,
and whether the API's own vocabulary follows is #287's question.
Deleted, each verified by grep first: `_applying`, `APPLY_QUIETLY`,
`APPLY_RELOAD`, `APPLY_UNANSWERED`, `_applied_quietly`,
`Invocation.no_reload`, `NO_RELOAD_HELP`, the `no_reload` parameter of
what is now `_imported_document`, and `Act.unanswered` with the branch
of `_performed` that read it. `Command.selects` stayed (the three memory
commands use it, and its comment now names them), as did tuple `does`,
`acts()` and `_performed`. `_applied` became `_imported` and renders
every import, since nothing installs behind it. `NOTHING_APPLIED` became
`NOTHING_IMPORTED`. `EXPORT_HEADER`'s three steps became
`import -f` / `secret set` / `apply`, and `DIFF_INTRO` keeps the API's
`reload` label while explaining it with `vinga apply`.

Server-side prescriptive strings. The two `events/catalog.py` templates
and `config/api.py`'s `_UNLOADED_AGENT` now say
`vinga-server config apply`. The API's own vocabulary did not move: the
routes, the response models, the `applies` tokens and the
`api_descriptions/` prose that describes the mechanism are all as they
were.

`config/docgen.py`. The domain-config introduction was rewritten for the
new grammar, `_TOPIC_COMMANDS` learned `("import",)` as a preset-topic
command, and the recipe rendering now deduplicates per-file SEQUENCES
rather than individual command lines (`_steps`). That is the mechanism
choice the plan left open; the reasoning is in the docstring and the
behavior is pinned. The devices recipe, where both presets quote the
same two lines, still says them once; each preset's import/apply pair
survives whole.

The suites. Every invocation respelled, every `--no-reload` case
dropped, and the cases that existed only because `apply` was two acts
deleted with the machinery they were about. Six new pins: the four
loud-failure ones, the notice-shape ones, the `DIFF_INTRO` mapping, the
apply-help clocks, the recipe adjacency, and the option-aware census
guard. The pre-cutover fixture moved into `_HISTORICAL_PATHS` and its
test drives `import` and compares the configuration body.
`test_config_cli_respelling.py` kept its transcript and its steps and
gained three labeled #371 substitutions.

Documents. Root README step 3 and the two paragraphs after it;
`vinga-server/README.md` throughout, including the
"Applying a change without a restart" transcript and the two recovery
procedures; `config.example.yaml`; `config.deploy.example.sh` and
`.yaml`; `examples/README.md`; both presets; the two MCP example
fragments; `docs/architecture/cli-guide.md` (flat verbs, the #341
passage rewritten to hold both instances of its rule, the round trip,
the Owed line and the timeout passage); `docs/reference/cli.md` prose
including the new versions-skew paragraph; the CHANGELOG entry; and the
census manifest, regenerated with the last document edit.

### Deviations from the plan

Five, all small.

**The loud-failure pins live in `test_config_cli_grammar.py`, not
`test_config_cli.py`.** That module is the one about the parse, its
docstring already promises "a word the grammar used to have", and
`test_the_flat_status_word_is_gone_and_the_noun_spelling_answers` is the
#341 precedent for exactly this shape. The four pins sit beside it under
a comment naming the difference: `status` was retired and names nothing,
while `apply` still names a row and what moved is what it takes.

**The pins assert exit code 1 and this grammar's own sentence, not
Click's exit 2 naming the word.** The plan describes Click's behavior;
this CLI does not have it. `_usage_problems` translates every usage
error into a fixed sentence of its own and `main` exits 1, deliberately,
so that no refusal echoes what was typed (a secret typed after a slot is
the mistake that motivated it). The pins therefore read
`cli.usage_line("that is not a command")` and
`cli.usage_line("that is not an option of this command")`.

**`docs/reference/api-openapi.json` did not change.** The plan expected
it to, because `config/api.py`'s `_UNLOADED_AGENT` respells. That
constant is the 404 body's `detail`, not a description the OpenAPI
document carries, so three of the four generated references moved and
the fourth is byte-identical. The constant was respelled anyway, and the
census records it.

**`_applied_entries` became `_imported_entries` rather than keeping its
name.** The plan asks for the helper to stay and its pins to run
unchanged, which they do; the rename is for consistency with the
rendering above it, and its docstring no longer claims to be the half
two renderings share.

**Two example fragments were swept that the plan does not name.**
`examples/mcp-server-stdio.yaml` and
`mcp-server-streamable-http.yaml` each say when a change lands in a bare
verb sentence ("at the next reload", "asked to reload") that the census
families cannot match. They are exactly the hidden-spelling risk the
plan's manual grep exists for, so they moved with the rest.

### Discoveries

**The versions paragraph could not quote the retired spelling.** The new
paragraph in `docs/reference/cli.md` is about being told to run
`vinga reload`, and writing that invocation on a live page is what the
census guard exists to fail. It says "the `reload` its own grammar no
longer has" instead: the bare word is not an invocation, the sentence is
unchanged in meaning, and the guard stays honest.

**The census's guard was blind exactly where the plan predicted, and
the new one bites.** Before the documents were swept, the option-aware
guard flagged fifteen live lines pairing an `apply` invocation with `-f`
or `--no-reload`, every one of which the registered-command guard passed
because `apply` names a row. Both are green now.

**The recipe dedup had a second reader.** Both presets quote the same
`device bind` and `default-agent set` lines, so the global dedup was
load-bearing there and could not simply be dropped. Sequence-level
deduplication keeps that collapse and lets the two import/apply pairs
through, which is why the mechanism is per-file rather than adjacency
suppression.

### Verification

Run from `vinga-server/`, against a development Postgres started from
the committed compose file under a project name of this run's own.

- `uv run ruff check .`: `All checks passed!`
- `uv run pytest tests/unit -q`: `4931 passed, 19 skipped in 527.52s`.
- `uv run pytest tests/unit -q -n auto --dist loadfile`, which is how CI
  runs the lane: `4931 passed, 19 skipped in 76.67s`.
- `uv run pytest tests/integration -q`: `233 passed in 345.42s`.
- `python3 scripts/check_doc_links.py .` from the repository root:
  `checked 176 files, 0 failures`.

The first run of each lane was not green, and the four failures it found
are recorded rather than hidden. Three unit cases read a renamed surface
by its old name (`test_config_diff_read.py` asserting the notice says
"asked to reload", `test_config_reload.py` calling `_reload_listing`,
`test_onboarding_activation.py` asserting the OTA warning names
`config reload`), and one integration case did the same
(`test_agent_guidance.py`). All four are the sweep's own edit made in
four places the sweep missed, and each has its own commit.

Not verified here, and stated rather than claimed: the image build and
its smoke lane, which run on CI against an image this session did not
build; and the device checkpoint, which this change does not touch.

### PR review round

External review of the branch as pushed to PR #372, at `b9930e0b`
against `origin/main`: backend codex (codex-cli 0.153.0), model
gpt-5.6-sol, 2026-09-03, runtime 6m15s. Sol rather than the fast tier
because the diff changes what an operator types. Four findings, one P1
and three P2, verdict as received: mergeable after fixes. All four
confirmed against the sources before being fixed; none rejected, and one
sub-instruction inside the P1 was refuted with evidence.

The P1 and the first P2 are the same failure seen twice, and it is worth
naming: **the sweep moved every sentence that names a command and left
the sentences that use the word.** `apply` was two things before this
rename, a verb and an ordinary English word for what a write does to a
store, and the census can only see the first. Every miss the round found
is the second: a footer saying credentials go in "after applying", a
refusal saying a write may have been "applied", a guide saying `apply`
consumes the exported document. Nothing about the code changed under any
of them; what changed is which of two meanings a reader now takes.

1. **P1: the exported recovery instructions restore credentials after
   installation.** `EXPORT_HEADER` orders import, secret entry, apply,
   while `EXPORT_SECRETS_HEADING` says "Enter each of them after
   applying". Following the footer installs a deployment whose engines
   have no credentials, or fails the apply. Fix: say after importing and
   before applying, update the #371 respelling substitution, and pin the
   footer against the header's order.

   *Resolution* (`1ed51636`): confirmed and fixed. The footer now names
   both verbs in the header's order, and a new case in
   `test_config_round_trip.py` reads that order off the header rather
   than restating it, so the two halves of an export cannot come apart
   again. The stale note above the round trip's own fixture, which still
   said "the sequence is apply, then the secret sets", moved with it.

   The substitution half of the fix is refuted rather than adopted. The
   #223 transcript carries no credential footer at all: its final
   `export` runs after `provider-secret-clear` and
   `mcp-server-secret-clear`, so nothing is stored and the heading is
   never printed (`grep -c "Stored credentials"
   tests/unit/data/cli-respelling.txt` is 0). The pre-cutover fixture
   does carry the old line, and its test already excludes the credential
   annotations along with the header, so its bytes stay as printed and
   nothing compares them.

2. **P2: the live-vocabulary sweep missed write-side apply language.**
   `UNREADABLE_WRITE` tells a failed caller to check whether the
   document "was applied"; the CLI guide still says `apply` reads back,
   takes and consumes an exported document; the deploy example still
   gives `apply` the document-write semantics and the additive rule.
   Fix: use written or import in those, and add assertions covering the
   contract strings.

   *Resolution* (`fee630fb`): confirmed and fixed, in seven places. The
   refusal is the one that reaches an operator, and the coordinator's
   caution about it held: twenty acts carry that sentence, from a
   provider `set` to a secret write to `import`, and not one of them
   installs anything, so "applied" meant written for every user and the
   word is now written for every user rather than split. The pin reads
   the carriers off the registration table and holds the install to not
   being among them, which is the assertion the finding asked for: a
   string comparison alone would not have said why the word is wrong.

   The documentation half is left to the manual audit and the census
   rather than given a pin of its own, deliberately. A test that
   forbade "`apply` takes" in live prose would be a blacklist of
   English, unable to enumerate the sentences it is about and certain to
   go stale against the next paraphrase; the guard that can be stated is
   the option-aware one below.

3. **P2: the recipe verification claim is false because published
   applies are skipped.** `docs/reference/cli.md` says the whole recipe
   list is run against a live server on every build, while the live lane
   skips every bare `apply`, and the test's name and docstring claim the
   same coverage. Fix: disclose the exclusion in the published reference
   and describe the verified subset, or make those applies safe to run.

   *Resolution* (`6563445a`): confirmed and fixed by the first option.
   The sentence lives inside the page's generated markers, so it moved
   through `RECIPES_INTRO` in `cli.py` and the region was regenerated;
   editing the page would have been reverted by the next render. The
   test says the exception in its name as well as its docstring, and it
   now asserts that the published intro names the line it skips, so the
   page and the lane are held together rather than merely written to
   agree today.

   The second option was weighed and not taken. What would make a
   preset's apply safe is a deployment whose providers are all mocks,
   which is a different document from the preset being published: the
   lane would install something nobody ships and the published preset
   would still be uninstalled.

4. **P2: the stale-apply census guard is neither option-aware nor
   continuation-aware.** It searched option substrings, so `-f` matches
   the valid `--force`, and it read only the invocation's physical line,
   so a backslash-continued `vinga apply` followed by `-f
   document.yaml` was missed. Fix: tokenize complete command spans
   including continuations, compare exact option tokens, and pin both.

   *Resolution* (`72523e87`): confirmed, both halves reproduced before
   fixing (`vinga apply --force` was flagged; the continued form was
   not). Tokens are compared whole now, with an attached value read as
   the option it names, and the span is joined across trailing
   backslashes the way a shell joins it. Twelve parametrized cases carry
   the rule, seven that must be flagged and five that must not, because
   a guard that cries wolf over a global option is a guard somebody
   turns off. The false positive is the more dangerous of the two: it
   would have been met by whoever next wrote a correct line.

Nothing in this round falsifies a decision recorded in the plan, so the
plan is unchanged apart from the milestone's PR number.

### Verification after the review round

Run from `vinga-server/`, against the Postgres the round provided.

- `uv run ruff check .`: `All checks passed!`
- `uv run pytest tests/unit -q -n auto --dist loadfile`, which is how CI
  runs the lane: `4944 passed, 19 skipped in 85.39s`.
- `uv run pytest tests/integration -q`: `233 passed in 336.55s`.
- `python3 scripts/check_doc_links.py .` from the repository root:
  `checked 176 files, 0 failures`.

One fix broke one lane and the break is recorded rather than hidden.
Giving `EXPORT_SECRETS_HEADING` a second line put a line into the
pre-cutover comparison, which stripped the credential footer by matching
its heading rather than by taking the block: an export is the header,
the configuration and then the annotations, so the annotations are the
tail of the file and are dropped as one now (`4d7adcdc`). The rule is
about the file's shape rather than about the heading's wording, which is
what makes it survive the next line the prose grows.

Still not verified, and stated rather than claimed: the image build and
its smoke lane, and the device checkpoint.
