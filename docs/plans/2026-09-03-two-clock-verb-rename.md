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
  to delete. The two-act command is why `Command.selects` exists, why
  `Act.unanswered` exists, why `APPLY_UNANSWERED` has to explain what
  an answered write followed by an unanswered install can honestly
  claim, and why the applied document has two renderings. With no
  two-act command, all of it goes; with `import --apply`, all of it
  stays for one flag.
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
- **The two-act machinery is deleted whole**, held to the deletion
  test: `_applying`, `APPLY_QUIETLY`, `APPLY_RELOAD`,
  `APPLY_UNANSWERED`, `_applied_quietly`, `Invocation.no_reload`,
  `NO_RELOAD_HELP`, the `no_reload` parameter of `_applied_document`,
  and `Command.selects`. If `Act.unanswered` and the multi-act
  sequencing in `_performed` have no remaining user (grep says the
  apply row was the only tuple-`does` row, verified again at
  implementation time), they go too, and `Command.does` may narrow
  back to one act if nothing else holds the tuple shape. Every
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
- **`_RETIRED_WORDS` in the census is already right.** `apply` and
  `reload` are both in it from the #223 re-cut, so the retired-word
  families keep matching; `import` joins the live words through the
  registered tree itself. No census code changes, only the manifest
  regeneration.
- **The simulator's two sentences** (`NOT_ADMITTED_YET` and the
  not-admitted listing) respell `{PROGRAM} reload` to
  `{PROGRAM} apply` with no other change.

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
- **`vinga-server/examples/presets/local-stack.yaml` and
  `cloud-stack.yaml`**: header comments become
  `vinga import -f <preset>` followed by `vinga apply`.
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
- **Old spellings fail loudly, pinned (new).** Three pins in
  `test_config_cli.py`: `vinga reload` exits 2 with Click's
  no-such-command error naming `reload`; `vinga apply -f x.yaml`
  exits 2 with no-such-option `-f`; `vinga import -f -` is the
  registered write. These are the pre-release stance made falsifiable.
- **The census is both tool and test.** After the sweep,
  `test_every_live_spelling_names_a_command_the_tree_has` is the
  proof no live document still prescribes `vinga reload`; the
  manifest is regenerated with
  `uv run python -m tests.unit.test_command_spellings` in the same
  commit as the last document edit, never by hand.
- **Generated-document drift checks** (unit and integration lanes)
  hold the four regenerated references to their generators.
- **`test_config_cli_respelling.py` and its differential stay
  untouched**: they are the #223 record, census class historical.

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

- [ ] **M1: the rename, whole.** `config/cli.py` grammar
  (`import` row, `apply` row, two-act machinery deleted),
  `config/entities.py` notices (`APPLY_NOTICE` one-liner,
  `BINDING_UNSERVED_NOTICE` respelled), `events/catalog.py` and
  `config/api.py` prescriptive strings, `docgen.py` introduction,
  `tests/support/notices.py` phrase table, the test sweep with the
  three new loud-failure pins, the four regenerated references, the
  hand-maintained pages (root README, vinga-server README,
  config.example.yaml, both presets, cli-guide), the CHANGELOG
  entry, and the census manifest regenerated in the same commit as
  the last document edit. PR: TBD.
