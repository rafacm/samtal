# Rename the two-clock verbs: import writes, apply installs

Implements [#371](https://github.com/rafacm/vinga/issues/371).

Companion implementation doc:
`2026-09-03-two-clock-verb-rename-implementation.md`, one section per
milestone, appended in the same change that ticks the milestone
checklist below.

## Goal

`vinga apply` becomes `vinga import`: write the document to the store
and stop there. `vinga reload` becomes `vinga apply`: install the
stored configuration on the running server. `--no-reload` is deleted,
because a write-only `import` needs no flag to stay write-only. The
per-write boundary notice shrinks to one line that names the boundary
and points at `vinga diff`, and the three-clocks prose moves to the
new `apply`'s help and stays in the reference. Every live document
that quotes the old spellings is swept in the same change, which the
command-spellings census enforces rather than requests.

## The issue's decisions, restated

- `vinga apply` becomes `vinga import`. It writes the document and
  stops; the reload half of today's verb leaves it.
- `vinga reload` becomes `vinga apply`. It installs the stored
  configuration on the running server.
- `--no-reload` is deleted rather than renamed. A write-only `import`
  needs no flag to stay write-only.
- The names pair deliberately: `import`/`export` are the store's
  document I/O, `diff`/`apply` are the store-versus-running-server
  reconciliation. "Reload" named the mechanism; "apply" names the
  outcome.
- Pre-release stance holds: no aliases, and the old spellings fail
  loudly. `vinga reload` becomes an unknown command; `vinga apply -f`
  errors under the new `apply`, which takes no `-f`.
- `RELOAD_NOTICE` and `BINDING_UNSERVED_NOTICE` are rewritten anyway,
  and the shortening is weighed with them: the issue directs the plan
  to weigh cutting the per-write notice to one line naming the
  boundary and pointing at `vinga diff`, with the three clocks moving
  to `vinga apply --help` and the reference. Weighed below, and taken.
- The cli-guide passage that #341 wrote is rewritten to record the new
  instance of its own rule; the README Quick Start, the preset header
  comments, the vinga-server README's
  "applying a change without a restart" material, and the generated
  references move with it; the census sweeps stragglers.

## Open questions, resolved

### Does `import --apply` exist as the one-shot spelling? No.

The old default (`apply` = write, then install) does not survive as a
flag on `import`. Import-diff-apply is the whole rhythm, and a script
that wants the one-shot types two commands.

- The #341 rule, applied in its own shape. The rule says: when a
  verb's plain meaning and its act come apart, move the command, and
  give the **narrower** behavior a flag rather than giving the wider
  one a second verb. `import --apply` is the inverse: the **wider**
  behavior as a flag on the narrow verb. A flag that makes a verb do a
  second act is the overloading this issue exists to remove, one
  spelling later.
- The machinery it would keep alive is the machinery this rename gets
  to delete. The two-act apply is the only user of `Act.unanswered`
  and of `APPLY_UNANSWERED`, the sentence explaining what an answered
  write followed by an unanswered install can honestly claim, and it
  is why the applied document has two renderings. With no two-act
  command those go; with `import --apply` they stay for one flag.
  (`Command.selects`, tuple `does` and `_performed` are shared
  machinery serving other rows and stay regardless; see the review
  round.)
- The footgun answer is the notice, not a flag. An operator who
  imports and stops is told, on the same write, exactly which command
  installs and which command shows what is pending. The verb no
  longer lies; the sentence under it carries the rest.
- `export`'s round trip gets simpler, not longer. The export header's
  three steps become `import`, the `secret set` commands, `apply`,
  with no staging flag to explain: staging is what `import` is.

### The API keeps the mechanism vocabulary; the CLI renames the acts

The rename is the CLI grammar's. The server API is deliberately
untouched:

- `POST /api/runtime/config/reload` and the whole-document write route
  keep their paths.
- The response models keep their names (`ConfigReloadResult`,
  `AppliedDocument`, `ConfigDiff`).
- The diff's `applies` boundary tokens keep their values: `reload`,
  `check-in`, `restart`, `store-boot` are API contract, pinned by
  `tests/support/notices.py` tokens and the OpenAPI document. At the
  API level "reload" names the mechanism truthfully: the server
  re-reads the store and swaps the world. What the token's phrase
  announces in prose changes (below); the token does not.
- `config/reload.py`, the `mcp_reload` event name, and the events
  package's field vocabulary are all server mechanism, unchanged.

The reasons: the issue's blast radius names the CLI, the notices and
the documents, not the server routes; a contract change would re-cut
`docs/reference/api-openapi.json` consumers for no operator-visible
gain; and #287 (a CLI generated from the OpenAPI contract) is the
recorded place where the API's own verb vocabulary gets decided, which
is exactly why this rename lands first. The seam this leaves is one
comment's worth: the CLI's `import` posts to the API's apply route and
the CLI's `apply` posts to the API's reload route, and the act rows
say so where they are declared.

What does change server-side is every sentence that tells an operator
what to type, because the census guard holds those to the registered
tree:

- The two event message templates in `events/catalog.py` that say
  "install it with: vinga-server config reload" become
  "install it with: vinga-server config apply"
  (`docs/reference/events.md` regenerates).
- The one OpenAPI description string in `config/api.py` that names
  `vinga-server config reload` respells the same way
  (`docs/reference/api-openapi.json` regenerates).
- `docgen.py`'s domain-config introduction, which walks the
  boundaries and names `{PROGRAM} reload`, is rewritten for the new
  grammar (`docs/reference/domain-config.md` regenerates).

### The notices, rewritten and shortened

The current `RELOAD_NOTICE` is four lines printed verbatim on every
domain-half write; the 2026-09-03 Quick Start run printed it six times.
The guide's own dedup rule for the old `apply` rejects that shape ("a
document that wrote nine entities is waiting on one reload, not on
nine"), and a per-process write cannot dedup across a script, so the
sentence itself shrinks. The three clocks (tools at the next
utterance, prompt at the next activation, voice at the next
conversation) are true and stay published, but per write they are
noise: they move to `vinga apply --help` and stay in
`docs/reference/domain-config.md`, which is where an operator who
wants the clocks already looks.

The constant is renamed for the boundary it announces, and the new
sentences are:

- `APPLY_NOTICE` (was `RELOAD_NOTICE`), one line:

  > This is stored and not yet serving: `vinga apply` installs the
  > stored configuration on the running server, and `vinga diff` lists
  > everything pending.

- `BINDING_UNSERVED_NOTICE`, still two facts because both halves are
  true at once, respelled and tightened:

  > The binding applies at the device's next OTA check or connection,
  > but this server is not serving the agent it names yet:
  > `vinga apply` installs the stored agents, and the device reaches
  > it at the check-in after that.

- `RESTART_NOTICE`, `BINDING_NOTICE` and `SNAPSHOT_NOTICE` are
  untouched: no clock they name moved.

`tests/support/notices.py` is the one reader of these sentences, and
its `_ANNOUNCED_BY[RELOAD]` phrase becomes `f"{PROGRAM} apply"`. The
boundary tokens the suites assert stay exactly as they are, so an
edit that kept every boundary keeps every downstream test green,
which is the module doing its job.

One check the wording above already passes, recorded so the review
can hold it: the `RELOAD` boundary's announcing phrase
(`vinga apply`) must appear in both sentences, and must not appear in
the three untouched ones. It does and it does not, respectively.

### The smaller decisions the issue leaves open

- **CLI-side names follow the grammar.** In `config/cli.py`: the
  write act `APPLY` becomes `IMPORT`, its `APPLY_READ_TIMEOUT_S`
  (None, unbounded) becomes `IMPORT_READ_TIMEOUT_S`, and its path
  helper becomes `_import_path` (still returning the API's apply
  route, with the seam comment). The install act `RELOAD` becomes
  `APPLY`, `RELOAD_READ_TIMEOUT_S` becomes `APPLY_READ_TIMEOUT_S`,
  `UNREADABLE_RELOAD` becomes `UNREADABLE_APPLY` and its sentence says
  "answered the apply", `_reload_listing`/`RELOAD_SECTIONS` become
  `_apply_listing`/`APPLY_SECTIONS`. `_applied` (the rendering of a
  written document with its boundaries) becomes `_imported`.
  `AppliedDocument` and every other API shape name stays, per the seam
  above.
- **Only the apply-specific machinery is deleted**, held to the
  deletion test and to grep: `_applying`, `APPLY_QUIETLY`,
  `APPLY_RELOAD`, `APPLY_UNANSWERED`, `_applied_quietly`,
  `Invocation.no_reload`, `NO_RELOAD_HELP`, the `no_reload` parameter
  of `_applied_document`, and the `selects=_applying` entry on the
  row. The shared machinery stays: `Command.selects` (the three
  memory commands use it), tuple `does` (`info` and the conversation
  reads use it), `acts()` and `_performed`. `Act.unanswered` goes
  only after grep confirms `APPLY_RELOAD` was its sole user. Every
  deletion is verified by grep, not memory.
- **The help texts.** `import`'s help says what it does and names the
  boundary: write a whole document to the store in one transaction,
  refused whole if anything in it will not resolve, additive, never
  deleting; nothing running changes until `vinga apply`. The new
  `apply`'s help absorbs the three clocks: install the stored
  configuration on the running server, without a restart and without
  dropping a conversation; a conversation in progress meets new tools
  at its next utterance and new prompt text at its next activation,
  while a changed voice reaches the next conversation.
- **`EXPORT_HEADER` respells its three steps**: `import -f`, the
  `secret set` commands, `apply`; and the sentence explaining why the
  first step stages ("a reload builds the engines...") simplifies,
  because staging no longer needs a flag to explain.
- **`APPLY_UNANSWERED`'s job disappears, not relocates.** The new
  `apply` follows nothing, so a failed one has nothing extra to say;
  the new `import` installs nothing, so it cannot leave an install
  unanswered. The sentence's forwarding role (run `diff`, then the
  installer) survives only in the one-line `APPLY_NOTICE`.
- **The census changes one list, not its rules.** `apply` and
  `reload` are both in `_RETIRED_WORDS` from the #223 re-cut, so the
  retired-word families keep matching, and `import` joins the live
  words through the registered tree itself. The one code change:
  `vinga-server/tests/integration/data/pre-cutover-export.yaml` joins
  `_HISTORICAL_PATHS`, with a comment in the census's own style. The
  fixture is the pre-cutover build's output committed as it was
  printed, nothing left in the repository can produce it again, and
  its header quotes `vinga reload`; it is a record exactly the way
  the respelling transcript beside it already is, and today's
  `respell` class is the misclassification this rename exposes.
- **The simulator's two sentences** (`NOT_ADMITTED_YET` and the
  not-admitted listing) respell `{PROGRAM} reload` to
  `{PROGRAM} apply` with no other change.
- **`DIFF_INTRO` explains the kept token with the new verb.** The
  `applies` labels stay the API's (`reload`, `check-in`, `restart`),
  and the intro's explanation of the `reload` label changes from "at
  the next reload" to naming the command that crosses it: applied
  when `vinga apply` next installs the stored configuration. A pin in
  the rendering tests holds the intro to naming `vinga apply` beside
  the `reload` label, since the existing assertions read only the
  per-kind lines.

## Design footprint

No new module and no new seam. The change deepens
`config/cli.py`'s grammar by removing an interface: the `apply` row
loses a flag, a selects hook and a second rendering, and its caller
(an operator) stops having to know that one verb is sometimes two
acts. `config/entities.py`'s notice set keeps the same five-sentence
interface with shorter sentences. The one seam this touches, the
CLI-verb-to-API-route mapping, becomes visible instead of implicit:
two act rows whose comment says the API's vocabulary is the
mechanism's and whose renaming is #287's question.

## Documentation footprint

Hand-maintained pages whose description of current behavior this
change falsifies, found through the authority taxonomy in
`docs/README.md`:

- **Root `README.md`** (Getting Started): the step-3 command sequence
  ends in `vinga apply` instead of `vinga reload`; the paragraph after
  it respells ("`vinga apply` then builds the engines...", "the first
  apply is the slow one", "needs no apply either way"); the preset
  paragraph becomes the import-then-apply story
  ("imported whole in one transaction with `vinga import -f`, then
  installed with `vinga apply`"). Note: the maintainer has an
  uncommitted Quick Start rework of this same region in the main
  checkout (stashed during this run); this plan edits the committed
  text, and the stash's later landing carries a mechanical respelling
  conflict, flagged in the PR.
- **`vinga-server/README.md`**: the
  "Applying a change without a restart" section keeps its title and
  anchor (both already say apply) and respells its commands and its
  example transcript, including the quoted notice line; the `apply`
  paragraph ("An `apply` asks for this itself...") is rewritten for
  the new grammar and `--no-reload` leaves it; the recovery procedure
  under "When the server will not start" and every other prescriptive
  spelling in the page respells (the census enumerates them).
- **`vinga-server/config.example.yaml`**: the three comment lines
  naming `config apply`, `config apply --no-reload` and
  `config reload` respell to the new grammar.
- **`vinga-server/config.deploy.example.sh`,
  `config.deploy.example.yaml` and `examples/README.md`**: the deploy
  seeding script's `apply --no-reload` invocation and its staging
  paragraph, the profile's "one `config apply` against the running
  server" sentence, and the examples README's fragment/preset
  definitions all respell to the import/apply grammar. These are
  exactly the stale spellings the census cannot flag, because a stale
  `vinga apply -f` resolves to the newly valid `apply` row; the
  named-file sweep and the guard below are the mitigation.
- **`vinga-server/examples/presets/local-stack.yaml` and
  `cloud-stack.yaml`**: header comments become
  `vinga import -f <preset>` followed by `vinga apply`. The
  domain-config recipe machinery moves with them:
  `docgen._TOPIC_COMMANDS` learns `("import",)` as a preset-topic
  command (an unknown line is a refusal there, not a drop), and the
  recipe rendering keeps each preset's ordered import-then-apply pair
  intact instead of deduplicating the two identical `vinga apply`
  lines across presets into one (today's global `dict.fromkeys` would
  leave one preset reading as never applied). The exact mechanism is
  the implementer's; the behavior is pinned by a new docgen test that
  every preset `import` line in the rendered recipes is immediately
  followed by its `apply` line. The live lane keeps refusing to build
  either stack: it exercises the store-only half by running `import`,
  which after this rename IS the documented first command, so the
  old divergence comment about `--no-reload` comes out.
- **`docs/architecture/cli-guide.md`**: the flat-verbs census adds
  `import` and respells its example block; the `vinga reload`
  example under "The flat system verbs" becomes the new `apply` with
  the same argument (it does not apply a provider, it applies the
  deployment); the #341 passage under "A write says what it did"
  is rewritten to record this rename as the second instance of its
  own rule (the verb narrowed to its act instead of the act widening
  to the name), keeping the historical record of the #341 move and
  retiring the `--no-reload` consequences it derived; the round-trip
  passage and the "Owed" progress-line and timeout passages respell.
  The guide's `vinga`-spelled quotes are census class `respell`, so
  the sweep is enforced, and `_GUIDE_REJECTED` needs no new entry.
- **`CHANGELOG.md`**: one dated entry, Changed (the two renames, the
  notice shortening) and Removed (`--no-reload`).
- **Generated, through their generators only**: the
  `docs/reference/cli.md` generated region, prose outside the markers
  swept by hand; `docs/reference/domain-config.md`;
  `docs/reference/events.md`; `docs/reference/api-openapi.json`.
- **`docs/reference/cli.md`, "Versions, and the two halves
  disagreeing"**: the section claims a mismatched pair fails legibly
  at the API seam (absent route, unrecognized shape). This rename
  preserves every route and shape while changing server-produced
  command prose, so the skew it creates is textual: a new CLI against
  an old server is told to run `vinga reload`, which it no longer
  has, and an old CLI against a new server is told to run
  `vinga apply`, which in its own grammar is the write. The section
  gains a paragraph naming this skew, keeping the no-alias decision
  and the standing one-sentence policy (run the CLI from the same
  release line as the server) as the answer: command notices from a
  mismatched pair are not to be followed until the halves match.
- **Untouched by design**: `docs/plans/`, `docs/features/`,
  `docs/adr/`, `CHANGELOG.md` history, `cli-guide-audit.md`, the
  respelling differential fixtures. All census class `historical`;
  rewriting one would falsify the record.
- **`docs/xiaozhi-notes.md`, board guides**: no claim about these
  verbs; no change. Open issues the rename touches (#345, #297, #369,
  #129, #316, #343) are edited on GitHub, not in this repository, and
  stay out of this change per the issue ("that decision belongs to
  #346, not here" and its analogues).

## Tests

Reuse the existing assets; the suites named here already exist unless
marked new.

- **The boundary contract holds without edits to its assertions.**
  Every suite that asserts write acknowledgements does it in
  `tests/support/notices.py` tokens; the phrase table there changes
  (`RELOAD` is announced by `{PROGRAM} apply`), the tokens do not,
  and the module's own assert fails loudly on a notice that names no
  boundary. That module is the pin that proves the shortening kept
  the boundary.
- **The grammar sweep.** `tests/unit/test_config_cli.py`,
  `test_config_cli_transport.py`, `test_config_cli_rendering.py`,
  `test_config_round_trip.py`, `test_config_api_writes.py`,
  `test_config_cli_untransportable.py`, and the integration
  `test_cli_live.py` and `test_cli_wheel.py` respell their
  invocations and drop their `--no-reload` cases. The rendering
  tests for `_applied_quietly` are deleted with it; `_imported`'s
  rendering keeps the existing `_applied` pins (entries on stdout,
  distinct notices once each on stderr, the empty-document line, the
  flush-order property).
- **Old spellings fail loudly, pinned (new).** Four pins in
  `test_config_cli.py`: `vinga reload` exits 2 with Click's
  no-such-command error naming `reload`; `vinga apply -f x.yaml`
  exits 2 with no-such-option `-f`; `vinga import --no-reload -f -`
  exits 2 with no-such-option `--no-reload` (the flag was deleted,
  not transferred); `vinga import -f -` is the registered write.
  These are the pre-release stance made falsifiable.
- **The shortened notice is pinned as behavior (new).** Beside the
  boundary-token assertions, two semantic pins on the constants:
  `APPLY_NOTICE` contains no newline and names both `vinga apply`
  and `vinga diff`; `BINDING_UNSERVED_NOTICE` names `vinga apply`.
  `boundaries()` alone passes any sentence containing the announcing
  phrase, so the one-line shape and the `diff` pointer need their own
  assertions.
- **A semantic guard for the swapped verb (new).** The census scanner
  stops at the first option, so a stale live `vinga apply -f ...` or
  `apply --no-reload` passes the registered-command guard by naming
  the new `apply`. A new test beside the census asserts that no live
  (`respell`) match's raw line pairs an `apply` invocation with `-f`,
  `--file` or `--no-reload`; the historical classes stay exempt.
- **`NOTHING_APPLIED` becomes the empty-import line.** Its sentence
  ("nothing was applied. An applied document's top-level keys...")
  is write-sense prose the census cannot see; it is renamed
  `NOTHING_IMPORTED` and reworded for the import grammar, and the
  manual audit covers write-sense "apply/applied" prose as well as
  `reload`.
- **The census is both tool and test.** After the sweep,
  `test_every_live_spelling_names_a_command_the_tree_has` is the
  proof no live document still prescribes `vinga reload`; the
  manifest is regenerated with
  `uv run python -m tests.unit.test_command_spellings` in the same
  commit as the last document edit, never by hand.
- **Generated-document drift checks** (unit and integration lanes)
  hold the four regenerated references to their generators.
- **`test_config_cli_respelling.py`'s transcript and steps stay
  frozen, its substitution table grows.** The differential drives the
  current grammar against the #223 transcript, and its `RESPELLINGS`
  table is the licensed difference, with the #341 export-header entry
  as the precedent. This change adds narrowly labeled #371 entries
  for exactly the deliberate output changes: the old `RELOAD_NOTICE`
  text to the new `APPLY_NOTICE` text, the old
  `BINDING_UNSERVED_NOTICE` text to the new one, and the old export
  header's three steps to the new ones. The transcript file and the
  step list are not recaptured and not respelled; a difference the
  table does not explain stays a failure. (The module is census class
  historical, so its quoted old spellings are untouched by the
  guard.)

## Risks

- **The README overlap.** The maintainer's stashed Quick Start rework
  edits the same region. Mitigation: this plan touches the committed
  text minimally (respellings and the one preset paragraph), and the
  PR names the conflict so the stash's owner expects it.
- **The #341 cli-guide passage is a load-bearing record.** Rewriting
  it wrong would erase why the rule exists. Mitigation: the rewrite
  keeps the #341 story as history (the act was widened to match the
  name), adds #371 as the second instance (the verb was narrowed to
  its act, moving the command rather than the reader), and the review
  round reads it as prose, not as a diff.
- **Hidden spellings.** A prescriptive `reload` in a file the census
  families do not match (prose without backticks, a bare verb
  sentence). Mitigation: a manual `grep -rni 'reload'` over
  non-historical paths at implementation time, reading each hit, on
  top of the census.
- **The pre-cutover test's comparison spans the header.**
  `test_a_pre_cutover_export_applies_into_an_empty_postgres_database`
  compares a fresh export against the fixture stripping only the
  `secret set` lines, and the fixture's header cannot change while
  `EXPORT_HEADER` does. Mitigation, adopted from the round: the test
  consumes the fixture with the current `import` command, and the
  comparison excludes the version-specific reproduction header along
  with the secret footer, comparing the configuration body, and the
  test's docstring and the comparison helper's documentation are
  rewritten to say exactly that: the byte-for-byte claim narrows to
  the YAML configuration body, with the header and the credential
  annotations both named as excluded.
- **The two renderings' deletion could drop a pinned behavior**
  (the stderr flush ordering lived in `_applied_entries`).
  Mitigation: the surviving rendering keeps the shared helper and its
  pins run unchanged.
- **Event message edits ripple into `events.md` and its pins.** The
  two catalog templates are message text, not field vocabulary;
  `events.md` regenerates, and the golden assertions read fields, not
  sentences. Verified by running both lanes, not assumed.

## Milestones

One milestone, and the census is why it cannot be two: renaming the
verbs in `config/cli.py` immediately turns every live `vinga reload`
quote in the tree into a `respell` match naming no registered
command, so the unit lane is red until code, documents and manifest
move together. A docs-first or code-first split would merge a red
`main` either way, and the workflow publishes an image on every push
to `main`.

- [x] **[M1: the rename, whole](2026-09-03-two-clock-verb-rename-implementation.md#m1-the-rename-whole).** `config/cli.py` grammar
  (`import` row, `apply` row, the apply-specific machinery deleted,
  `DIFF_INTRO` reworded), `config/entities.py` notices
  (`APPLY_NOTICE` one-liner, `BINDING_UNSERVED_NOTICE` respelled),
  `events/catalog.py` and `config/api.py` prescriptive strings,
  `docgen.py` introduction and recipe machinery (`("import",)`
  topic, per-preset import/apply pairs kept whole), the
  `tests/support/notices.py` phrase table, the test sweep with the
  four loud-failure pins, the notice and `DIFF_INTRO` semantic pins,
  the recipe adjacency pin, the option-aware stale-`apply` guard,
  the labeled #371 `RESPELLINGS` entries, the pre-cutover fixture
  reclassified historical with its test driving `import` and
  comparing the configuration body, the four regenerated references,
  the hand-maintained pages (root README, vinga-server README,
  config.example.yaml, the deploy example pair, examples README,
  both presets, cli-guide, the cli.md versions paragraph), the
  CHANGELOG entry, and the census manifest regenerated in the same
  commit as the last document edit. PR TBD.

## Plan review round

Backend codex (codex-cli 0.153.0), model `gpt-5.6-sol`, 2026-09-03,
against commit 79ca37d2, runtime ~8 minutes. Verdict as received:
**not ready**. Findings condensed but faithful; each carries a
resolution appended with its amendment commit.

**1. P1: the plan deletes shared command machinery other commands
still require.** `info` and `conversation show` have tuple `does`
values, and all three memory commands use `selects`
(`cli.py:7243/7356/7381`); `_performed` is the shared sequencing for
all of them. The plan should delete only the apply-specific selector
and acts, preserving `Command.selects`, tuple `does`, and
`_performed`; `Act.unanswered` may go only after verifying it has no
other user.

*Resolution*: adopted. The open-question bullet no longer claims
`Command.selects` for the deletion, and the smaller-decisions bullet
now lists exactly the apply-specific pieces, names the shared
machinery that stays with its other users, and conditions
`Act.unanswered`'s deletion on a grep for other users.

**2. P1: the recipe generator cannot represent the planned preset
import/apply pairs.** `docgen._TOPIC_COMMANDS` recognizes only
`apply` as a preset command and refuses unknown lines
(`docgen.py:768/938`), and recipes deduplicate commands across both
presets (`docgen.py:815`), so two identical trailing `vinga apply`
lines collapse and one preset reads as left unapplied. The live lane
also deliberately avoids installing presets (`test_cli_live.py:2399`).
The plan should update the recipe machinery to recognize `import`,
keep each preset's ordered pair intact, keep the live verification
from building either stack, and pin that every preset import is
immediately followed by an apply.

*Resolution*: adopted. The presets bullet in the documentation
footprint now carries the docgen recipe work: `("import",)` joins
`_TOPIC_COMMANDS`, per-preset ordered pairs survive rendering, a new
pin holds import-then-apply adjacency in the rendered recipes, and
the live lane runs `import` (now exactly the documented command) and
still builds neither stack.

**3. P1: the respelling differential fails on the notice and export
changes.** `test_config_cli_respelling.py` drives the current grammar
against a frozen transcript with a licensed substitution table
(`RESPELLINGS`), and the transcript records the old notice and the
old export header; the plan's "untouched" claim cannot hold. The
module's own rule is that later intentional changes get labeled
`RESPELLINGS` entries (its #341 entry is the precedent). The plan
should add narrowly labeled #371 substitutions rather than recapture
or respell the transcript.

*Resolution*: adopted. The Tests section now says the transcript and
steps stay frozen while `RESPELLINGS` gains labeled #371 entries for
the two notice texts and the export header, following the module's
own #341 precedent.

**4. P1: the pre-cutover export fixture conflicts with the census and
with its byte comparison.** `pre-cutover-export.yaml` is committed as
printed by a build that no longer exists, currently census class
`respell` (manifest lines 1174-1177), so removing `reload` fails the
guard; and `test_a_pre_cutover_export_applies_into_an_empty_postgres_database`
compares a fresh export against the fixture stripping only `secret
set` lines, so the planned `EXPORT_HEADER` change breaks the
comparison, and the test itself types `apply --no-reload -f`. The
plan should keep the fixture byte-for-byte, classify that path
historical in the census, drive the current `import` when consuming
it, and compare the configuration body excluding the version-specific
header as well as the secret footer.

*Resolution*: adopted. The census decision now moves the fixture into
`_HISTORICAL_PATHS` (a record misclassified as live, exposed by this
rename), and a new risk entry has the pre-cutover test driving
`import` and comparing the configuration body with both the
reproduction header and the secret footer excluded. The fixture's
bytes do not move.

**5. P2: the census is blind to the stale half of a semantic verb
swap.** The scanner stops at the first option, so a stale
`vinga apply -f ...` resolves to the newly valid `apply` row and
passes. The footprint misses `config.deploy.example.sh:41`,
`config.deploy.example.yaml:165`, `examples/README.md:5`, and the
`NOTHING_APPLIED` sentence ("nothing was applied. An applied
document's...") that becomes the empty-import line. The plan should
add a semantic guard for live `apply` invocations carrying `-f` or
`--no-reload`, audit write-sense "apply/applied" prose as well as
`reload`, and name those files.

*Resolution*: adopted. The documentation footprint now names the
deploy example pair and the examples README, the Tests section gains
the option-aware guard for live `apply` invocations and the
`NOTHING_IMPORTED` rewording, and the manual audit widens to
write-sense apply prose. Internal API shape names keep the decided
vocabulary.

**6. P2: `DIFF_INTRO` keeps explaining the `reload` token with a
command that no longer exists.** The token stays, but its
operator-facing explanation must say the boundary is crossed by
`vinga apply`, and the mapping deserves a pin; neither the footprint
nor M1 names `DIFF_INTRO`.

*Resolution*: adopted. A smaller-decisions bullet now specifies the
`DIFF_INTRO` rewording (token kept, explanation names `vinga apply`)
and the pin that holds the mapping.

**7. P2: the notice tests do not enforce the shortening or the
`vinga diff` pointer.** `boundaries()` passes any sentence containing
`vinga apply`. Add semantic assertions: `APPLY_NOTICE` contains no
newline and names both `vinga apply` and `vinga diff`.

*Resolution*: adopted. The Tests section pins the no-newline shape
and both command names on `APPLY_NOTICE`.

**8. P2: command-bearing notices skew across independently versioned
halves.** `docs/reference/cli.md` (Versions section, ~line 241)
claims a mismatched pair fails at the API seam; this change preserves
every route and shape while changing server-produced command prose,
so a new CLI against an old server is told to run `vinga reload` and
an old CLI against a new server is told to run `vinga apply`, which
in its grammar is the write. Update the versions guidance to describe
this skew and require same-release halves before following returned
command notices.

*Resolution*: adopted. The documentation footprint now carries the
versions-section paragraph: the skew is textual rather than
API-seam-visible, and the same-release policy is the answer for
returned command notices.

**9. P2: no pin proves `--no-reload` was deleted from `import`.**
Add `vinga import --no-reload -f -` as an exit-2 no-such-option pin.

*Resolution*: adopted. The loud-failure pins grow to four, with
`vinga import --no-reload -f -` exiting 2 on no-such-option.

## Plan review delta round

Backend codex (codex-cli 0.153.0), model `gpt-5.6-terra`, 2026-09-03,
against commit ddfb9c7b, runtime ~4 minutes, scope: the nine
resolutions and anything the amendments introduced. Verdict as
received: **ready after amendments**.

**1. P2: M1's completion contract is stale after the amendments.**
It said "three new loud-failure pins" against the Tests section's
four, and omitted the docgen, historical-fixture and semantic-census
work the amendments added.

*Resolution*: adopted. The M1 checklist item now enumerates the
amended work and says four pins.

**2. P2: the pre-cutover test's docstring becomes false once the
header is excluded.** Its byte-for-byte claim must narrow with the
comparison.

*Resolution*: adopted. The risk entry now requires the docstring and
the comparison helper's documentation to state that only the YAML
configuration body is compared, header and credential annotations
excluded.
