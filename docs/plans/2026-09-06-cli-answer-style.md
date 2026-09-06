# A CLI answer says what happened, in the operator's words

Plan for [#426](https://github.com/rafacm/vinga/issues/426), closing
[#424](https://github.com/rafacm/vinga/issues/424) and
[#425](https://github.com/rafacm/vinga/issues/425) on the way: each is
one specimen of the pattern this plan settles once. Implementation
notes land in the companion
`2026-09-06-cli-answer-style-implementation.md`, one section per
milestone, appended in the change that ticks the milestone here.

## Goal

Walking Getting Started end to end on 2026-09-06, every command in
steps 2 and 3 answered the same way: every field of the response,
empty or not, in the server's field names, at a length that buries
what changed. Four specimens: `import` (four lines of prose to say
"run `vinga apply`", and `The binding` said about a document that
binds nothing, #424), `diff` (twenty-four lines to say three things,
#425), `apply` (twenty lines, no statement of success, and an
advertisement for MCP), and `info` (a six-line tally of mostly
zeroes under a three-line banner).

The pattern behind all four: a command renders every field of the
answer it was handed, in the field's own name, whether or not that
field has anything to say. It is consistent, which is what makes it a
house style rather than four bugs, and this plan replaces that house
style with five practices, applies them to the four commands, and
writes them into `docs/architecture/cli-guide.md` with merged
examples so a reviewer can hold the next command to them.

The API keeps its shapes, its field names and its prose. Nothing here
changes a response model; every one of these answers is a rendering
of a documented response consumed by other clients, and what changes
is what the CLI does with what it is handed. The one server-side
change is a new notice sentence for a default-agent write (#424's
separable finding), which is a new value in an existing field, not a
shape.

## The issues' decisions, restated

- **The practice is decided once, in `cli-guide.md`, not patched four
  times** (#426). The four commands are brought to it in this one
  plan, each in its own milestone so behavior changes sit alone in
  review.
- **The API is out of scope** (#424, #425, #426, all three
  explicitly). The CLI is the consumer that renders; the `notice`
  field keeps its prose for callers with no UI of their own.
- **The boundary survives** (#424). The guide's counterexample is a
  bare "written" line; `not serving yet` is the half that cannot be
  cut from any compaction.
- **The streams stay split** (#424). What was written is data on
  stdout; what it is waiting on is about this invocation and stays on
  stderr, flushed in the order `_imported_entries` documents.
- **Filtering is not a determinism violation** (#425). What is
  printed is a function of the stored state, so two runs against one
  state are still the same bytes.
- **`Onboarding URL` is the label, because it is the codebase's own
  name** (#426), with the device's word (`OTA`) as a parenthetical
  for whoever is typing it into a field labelled that.
- **The label goes above the URL, and the wrap protection is kept**
  (decided by the maintainer on 2026-09-06, choosing the first of the
  two doors #426 offers). The URL stays on a line with nothing in
  front of it, for the reason `_identity_block` records; what is
  compacted is the provenance sentence, not the protection.
- **An unknown boundary set is quoted, never guessed at** (#386,
  unchanged). Everything this plan does to a notice applies only
  where the client knows the set; the fallback arms of
  `_announced` and the import dedupe do not move.

## The five practices, decided

Each becomes a section of `cli-guide.md`'s practices in M5, with the
merged example the earlier milestones produce and a labelled
counterexample (all four are **historical** by then: this
repository's own pre-#426 renderings, quoted from the issues).

1. **An answer prints what has something to say.** An empty list, a
   false flag and a section of nothing are absent, not enumerated as
   `(none)`. Absence is absence; what is filtered is a function of
   the state, so determinism holds. A run with nothing at all to say
   says that in one fixed sentence, because empty output would read
   as a command that failed to answer.
2. **An answer speaks the verb that was typed.** The operator ran
   `import`; the answer says `imported`, not `wrote`, `stored` and
   `resynthesized`. Where the server's sentence states a boundary the
   client knows, the client's line **replaces** it rather than
   following it (the #386 mechanism completed: the token is what
   travels, and whichever side speaks, speaks once). Where the client
   does not know the set, the server's sentence is quoted alone,
   exactly as today.
3. **An action that succeeds says so**, in one line of its own, on
   stderr, because "it worked" is a fact about this invocation. A
   slow action's elapsed time is the progress line's job
   (`narrated`), deliberately: a wall-clock number in non-terminal
   output would make two runs against one state different bytes, and
   time is not state.
4. **A command volunteers no advice about features not in use.** The
   same instinct put a paragraph advertising the CLI reference at the
   end of Getting Started's step 2, and it was deleted for the same
   reason. A feature's own noun answers questions about it.
5. **A boundary is stated once per run, over the group**, not once
   per kind and not once per entry. Every pending kind in a diff is
   waiting on the same apply, and saying so ten times says less than
   saying it once.

## Open questions, resolved

### Where the success line and the count go: stderr, with the notices

`apply`'s new success sentence and `import`'s count-and-boundary line
are about this invocation, so they are stderr, under the same flush
discipline `_acknowledged` and `_imported_entries` already document.
stdout keeps exactly what it has: the apply's outcome listing and the
import's per-entry lines are the artifact. The alternative, success
on stdout, would put an invocation fact into a pipe that scripts read
for data, which is the line the stream-split practice draws.

### How `import` gets to one line: the known sets share one remedy

Today's stderr under an import is the server's sentence plus the
client's advice, per distinct boundary set. The target is
`imported 7 entries, not serving yet: run `vinga apply``.

The design: when **every** notice-carrying entry's boundary set is
one the client knows, the import prints one line: the act, the count
of entries written, and the client's own boundary-and-remedy clause.
This collapse is honest because of a fact the code already states
above `INSTALLS`: one command of this grammar crosses one of the four
boundaries, and it is `vinga apply`. Both known sets ({reload} and
{reload, check-in}) are waiting on that same install; the check-in
half of the second is not actionable by any command, and for a whole
document it is detail the single write's own answer carries better.
The mixed answer is specified rather than implied, because both
halves must survive it. The count line always prints. The
actionable-set clause rides it whenever any entry carries a set the
client has a remedy for; both actionable sets share the one clause,
per the collapse above, so the clause appears once however many
entries carry either. After it, every entry whose set is
non-actionable, absent or unknown contributes its server sentence,
deduplicated by sentence exactly as today: the mixed-version arm of
#386's plan does not move. So a document with one recognized entry
and one from an older server prints the count line with the remedy
clause and that older entry's sentence under it, and neither fact is
lost.

A document whose entries were all unchanged has no notices and gets
no boundary line, which is today's behavior; its stdout lines say
`unchanged` per entry. A document that named nothing keeps
`NOTHING_IMPORTED` unchanged.

### What a single write says: the client's line replaces, per set

`_acknowledged` keeps `wrote <thing>` on stdout. On stderr,
`_announced` changes from "sentence, remedy under it" to "the
client's own line where it knows the set, the server's sentence where
it does not". The client's lines live in the table that is today
`REMEDIES`, reworded to stand alone rather than to follow a sentence:

- {reload}: stored and not serving yet, run `vinga apply`;
  `vinga diff` lists everything pending.
- {reload, check-in}: the agent named is not serving yet; run
  `vinga apply`, and the device reaches it at its check-in after
  that.

Exact sentences are the implementer's within these constraints: each
names the state (the boundary that survives), then the remedy; each
is fixed text with no interpolation beyond `PROGRAM`; the table's
comment keeps its statement of what a key may be. Sets with nothing
to run about ({check-in}, {restart}, {store-boot}) keep the server's
sentence alone, unchanged: the server's words for those are pure
state, already right, and a client line would restate them for no
gain.

And the fallback arm gets the door it is missing today:
`_acknowledged` passes `str(acknowledgement["notice"])` straight to
`_announced` (`cli.py:4585`), while `_imported_entries` wraps its
sentence in `printable(..., UNBOUNDED)`. Every path that can print a
server sentence prints it through `printable` with the unbounded
length, for the reason the import path records: a boundary sentence
cut at a bound loses the state it ends with, and nothing an answer
carries steers a terminal. The single-write surface gains the
hostile-notice cases the import surface already has, driven through
`Act.read()`: a notice carrying an escape sequence and one carrying
a lone surrogate each arrive neutralized on stderr, with nothing of
either retained on an exception chain.

### `The binding` said about a document that binds nothing: a new notice

`write_default_agent` reuses `BINDING_UNSERVED_NOTICE`, whose "The
binding" names the row a `write_device` just wrote; for a
default-agent write it names a concept the operator's document never
contained. Per the comment in `entities.py` ("each sentence exists
because neither of the two above will do"), the fix is a new
`Notice`, not a reworded shared one: a default agent covers every
device nothing binds, and that is what its sentence says. Same
`applies` set ({reload, check-in}) when the named agent is unloaded,
so every client-side mechanism above treats it identically; the
sentence only ever reaches an operator through an old client, a bare
API caller, or the unknown-set arm.

The sentence has two producers, and both change, because the path
that produced #424's specimen is the second one: an imported
`default_agent` entry is answered by `_applied_notice`, whose
fallback sends both `devices` and `default_agent` through
`_binding_notice` (`api.py:2776-2807`). `_applied_notice` therefore
gains a default-agent branch keyed on `entry.section`, choosing the
new notice exactly where `write_default_agent` does, with the
unloaded-agent and snapshot questions asked the same way. Both
paths are pinned through the real API: the single write's
acknowledgement and an imported document's `default_agent` entry
each assert the new sentence and the unchanged `applies` set.
`tests/support/notices.py` gains the instance as the seventh member
of `_COMPOSED`, which holds six today.

### What the diff prints: groups by boundary, sections by content

The rendering becomes:

```
pending, at the next `vinga apply`:
  providers       added: asr.whisper, llm.local, tts.voice, vad.ears
  agent_defaults  changed
  agents          added: assistant

devices and default_agent are read as a device asks for them, so
nothing about them waits for an apply.
```

- **Grouped by `applies` token**, one head per group present, in the
  fixed order of the `Applies` declaration. The heads are this
  client's words for each token (`pending, at the next
  `vinga apply`:` for reload, `pending, at the next server start:`
  for restart), from a table beside `INSTALLS` so the one command
  keeps its one spelling. The table is total over `DiffApplies` and
  a pin says so, the way the #386 plan pins its alias: a member
  added to the alias without a head fails a test rather than
  rendering a hole. There is no unknown-token arm here, because
  there cannot be one: a diff's `applies` fields are scalar
  `DiffApplies` literals, and `_declared`'s tolerance is for
  tuple-valued token sets only, so an unknown scalar refuses the
  whole answer with the fixed unreadable-answer sentence. That is
  #386's settled behavior for this surface, and it does not move.
- **A section appears only when it has content**: a non-empty name
  list or a true flag. Within a section, only the non-empty facts,
  joined on one line (`added: a, b; changed: c`). The agents'
  sub-sections flatten to labelled facts of the agents line
  (`agents  prompt changed: kids`), because four indented blocks of
  `(none)` was most of what #425 counted.
- **The `LiveKind` sentence is fixed text**, printed after the
  groups, saying what the model's docstring means: these are read as
  a device asks and are in effect at its next check-in, which is why
  nothing about them can be pending. It prints always, because it is
  the answer to "why are devices never in this list", which is a
  question about every diff, not about this one's state.
- **A diff with nothing pending** prints one fixed sentence saying
  the running server is serving what is stored, then the `LiveKind`
  sentence. Empty output would read as a failed command.
- **The preamble goes.** `DIFF_INTRO`'s definitions were
  documentation printed on every run; with the group heads speaking
  plain words, the tokens no longer appear in the output at all, and
  the vocabulary's published homes (the OpenAPI document and
  `vinga diff --help`'s row) already exist. No help row changes:
  `docs/reference/cli.md` stays byte-identical.

Everything stays on stdout: the whole of what `diff` answers is the
artifact, including the sentence that explains its own scope, the
same reading `_identity_block` records for `info`.

### What the apply prints: outcomes that happened, then a full stop

- **Same content rule as the diff**: a section with nothing to say is
  absent; within a section, only non-empty lists and true flags. A
  `None` section keeps its `NOT_APPLIED` line: "this build does not
  touch this kind" is content, not emptiness.
- **The field names get operator labels.** `fallback_resynthesized:
  assistant` is a field name from the layer that did the work,
  unreadable to the operator whose document contains no filler. One
  table in `cli.py` beside `APPLY_SECTIONS` maps (section, field) to
  a lowercase operator phrase (for that one: the failure phrase each
  agent speaks when a reply fails, synthesized in its voice), and a
  completeness pin asserts every list field and every flag of every
  section model has a label, so a field added to the contract is a
  failing test rather than a line that quietly goes missing. That is
  the property the derived rendering exists for, kept, with the
  vocabulary moved to the side that talks to people.
- **The success line**: one fixed sentence on stderr after the
  listing, stating the stored configuration is installed and serving.
  No duration, per practice 3. It cannot live in `_apply_listing`,
  which returns one stdout string that `_printed` prints; `APPLY`
  gets its own render callable in `cli.py`, shaped like `_imported`:
  print the listing to stdout, flush, then the sentence on stderr,
  so the success lands under the listing it is about on a merged
  terminal. The ordering is pinned the way the import's is, with a
  recording stream proving listing-before-success, beside the
  presence-on-success and absence-on-refusal cases.
- **The MCP status block prints only when MCP servers exist.**
  `NOTHING_CONFIGURED` stops appearing in an apply's answer and
  remains exactly what `mcp-server status` answers for an empty
  deployment, where it is the whole answer to a question the operator
  asked. `_status_block` keeps one entry point; the apply's caller
  asks it only when there are entries.

### What `info` prints: the same facts, at a glance

```
vinga - Conversational AI. Sweded.
configuration API: http://127.0.0.1:8003/api
server: 0.1.0 (f3b361c)

onboarding URL (the address a device's captive portal asks for), from server.public_url:
http://192.168.1.117:8003/x/6IFH6IQ5/

configured: 4 providers, 1 agent, agent_defaults set, no devices, default agent assistant
```

- **The banner and the contacted line do not move.** `_contacted`
  prints them before the first act on purpose: they are the half of
  the answer no server can supply, and a refusal has to land under
  them. This is why the issue's sketch of a version-carrying banner
  line is not taken: the version arrives in the first act's answer,
  and buying one line would cost the property that an unreachable
  server's refusal appears under the address that refused. Recorded
  here so the compaction is of the server's half only.
- **Version and revision become one line**, `server: <version>
  (<revision>)`, both through `printable` as today.
- **The label above the URL** leads with `onboarding URL`, carries
  the captive-portal/OTA parenthetical, and keeps the provenance on
  the label line; the URL stays bare on its own line, `printable`
  with `UNBOUNDED`, exactly as now. The onboarding-off arm
  (`ONBOARDING_OFF_HERE`) is untouched.
- **The tally becomes one line.** Kinds with a zero count are absent;
  a count of one uses the descriptor's own singular command noun
  (`kind.name`) and any other count uses `kind.name + "s"`, which
  reproduces every merged kind's plural and is derived rather than
  listed. `agent_defaults set` appears when the singleton section is
  non-empty. Devices: `no devices` or `N devices bound`. The default
  agent: `default agent <name>` (through `printable`) or `no default
  agent`, because an unbound board reaching nothing is a fact the
  operator needs, not an empty field to hide. A deployment with
  nothing at all configured prints `configured: nothing yet`, which
  is the line Getting Started's step 2 points at.

### Where the elapsed-time question lands: answered by practice 3

`import` and `apply` keep `narrated`, which already draws elapsed
whole seconds on a terminal and writes no byte anywhere else. The
success line is the full stop; the clock stays the affordance. If a
deployment ever wants durations in retained output, that is an events
question, not a rendering one.

## The standing review lenses

- **No-leak.** One door is added, and no value loses one: the plan
  closes the pre-existing gap where `_acknowledged` printed a
  server's notice without `printable`, so after M1 every server
  sentence any rendering quotes goes through
  `printable(..., UNBOUNDED)`. Every name printed by the new
  renderings goes through the same `printable`/`_names` calls the
  old ones used; the new sentences (success, group heads, the
  LiveKind line, `nothing yet`) are fixed text; the import's count
  is arithmetic over the answer; labels are this module's own
  strings. The unknown-token arm still never echoes the token
  (pinned already by the #386 cases, which stay).
- **Pin before reshaping.** This plan changes rendered bytes
  deliberately, so the pins move with the behavior in the same
  commits rather than being preserved: `test_config_cli_rendering.py`,
  `test_config_apply.py`, `test_config_cli_info.py`,
  `test_config_cli_summary.py` and `tests/support/notices.py` are the
  homes, and each milestone rewrites only the assertions its surface
  owns. The frozen transcript in `test_config_cli_respelling.py` is
  not recaptured; M1's changed stderr advice arrives as a licensed
  substitution, the way #386's did.
- **Closed sets mapped to decision sites.** The diff group heads and
  the client boundary lines are keyed by `Applies` members; the
  decision site is the answer's own `applies` field. On the
  acknowledgement surfaces the set is read through `_boundaries` and
  an absent, empty or unknown set takes the quoting arm; on the diff
  the field is a scalar `DiffApplies` and an unknown value refuses
  the whole answer, which is the settled #386 split between the two
  shapes. No new token, no new field.
- **Honest seams.** No injectable dependency is added. The one
  server-side change is a new `Notice` instance; the producer-side
  pin (every `Notice.applies` member is an `Applies` member) covers
  it by existing.
- **Inventories by tooling.** The `(none)` sites:
  `grep -n '(none)' src/vinga_server/config/cli.py` (the memory
  listings and `_status_block`'s own lines are out of scope and stay;
  the diff, apply and info sites go). The committed transcripts that
  change: `grep -rn "applies at reload\|configured:\|not yet
  serving\|(none)\|defaults_changed" README.md vinga-server/README.md
  docs/` re-run per milestone, because a rebase can add one; the
  `(none)` and `defaults_changed` terms are what find an apply
  transcript, which the first three patterns miss, and the
  `docs/plans/` hits it returns are historical records that never
  move.

## Module layout

No new module; four renderers deepen in place, and one notice is
added where the other five live.

- **`config/cli.py`**: `_announced` and the table now named `REMEDIES`
  change meaning (replace, not follow) and the table is renamed to
  say so; `_imported_entries` gains the count-and-collapse rule;
  `_diff_listing`/`_diff_block` re-cut around boundary groups;
  `_apply_listing` gains the content rule, the label table and the
  success line; `_identity_block` and `_configured_counts` compact.
  Every one of these is presentation of a shape the module already
  reads, which is the file's stated job; nothing new is exported.
- **`config/entities.py`** deepens by one sentence: the default-agent
  write gets the notice that says what it is.
- **`config/api.py`**: `write_default_agent` picks the new notice,
  and `_applied_notice` gains the default-agent branch, so the single
  write and the imported entry answer the same sentence.
- **`tests/support/notices.py`** gains the seventh instance, in
  `_COMPOSED`.

## Tests

- Per surface, the existing suites re-pin the new bytes:
  `test_config_cli_rendering.py` (import, diff, apply renderings, the
  steer-a-terminal and neutralization cases keep their planted
  values), `test_config_apply.py` (the every-field-renders test
  becomes every-field-has-a-label plus only-content-prints),
  `test_config_cli_info.py` (identity block, URL still whole and
  bare, credential still stdout-only), `test_config_cli_summary.py`
  (the tally line).
- **New pins.** The label completeness pin over `APPLY_SECTIONS`.
  The import collapse: a document whose entries carry {reload} and
  {reload, check-in} prints one stderr line with the count and the
  remedy clause; a mixed document with at least one recognized entry
  and one unknown-set entry prints the count line still carrying the
  remedy clause plus that entry's server sentence, asserting both
  facts survive and the unknown token itself is never printed;
  all-unchanged prints none. The diff:
  a one-change diff renders the group head once; an all-empty diff
  prints the nothing-pending sentence; the LiveKind sentence is
  present in both; the head table is total over `DiffApplies`; a
  diff carrying an unknown `applies` value still refuses whole
  through `Act.read()`, which is an existing assertion kept, not a
  new arm. The apply: success
  line present on the happy path, absent on a refusal; no
  `NOTHING_CONFIGURED` in an apply with no MCP entries, still
  answered by `mcp-server status`. Info: zero kinds absent,
  singular/plural derived, `nothing yet` for the empty store.
- **Unchanged on purpose**: the #386 fallback cases through
  `Act.read()`, the producer-side closed-set pin, the flush-order
  cases, `test_command_spellings.py`'s guard (the new sentences that
  name commands are `respell`-class spellings in `cli.py`, already
  inside its reach).
- Determinism: no existing case renders these answers twice, so each
  milestone adds one rather than leaning on a style: two renders of
  one answer compared as bytes, per surface (import, diff, apply,
  info), with stdout and stderr captured separately where a surface
  writes both. The two commands with new fixed stderr sentences
  (import's count line, apply's success line) also run once at a
  pseudo-terminal and once redirected and compare the redirected
  bytes, which is #297's both-ways shape holding the new sentences
  to the affordance licence.

## Risks

- **The collapse rule could hide a boundary.** Bounded by its
  premise, which is pinned: it fires only when every set is known,
  and the known sets are exactly the ones whose remedy is the same
  one command. If a third known set ever gains a different remedy,
  the table gains a row and the collapse premise breaks loudly in the
  import pins, which assert the one-line form against a two-set
  document.
- **Labels are a second structure beside the models.** Held by the
  completeness pin in the direction that matters (a field without a
  label fails); the other direction (a label without a field) is dead
  code the same test reports by keying the table off the models.
- **The README transcripts and the walkthrough drift.** Each
  milestone updates the transcripts its surface owns in the same PR,
  and the grep in the inventories section is re-run after every
  rebase; the census manifest regenerates per milestone since this
  plan and the CHANGELOG stale it regardless.
- **`test_config_cli_respelling.py`'s licensed table grows again.**
  Expected and small; the docstring amendment #386 made already
  describes the regime the new lines live under.
- **Cutting `(none)` where it is load-bearing.** The memory, status
  and session renderings keep theirs: `NOTHING_THERE` and the status
  block's `(none)` are answers to per-row questions, not enumerations
  of empty change sets. The grep inventory is what holds the line.

## Milestones

- [x] **[M1: an import answers in one line, and a default agent stops
  being a binding](2026-09-06-cli-answer-style-implementation.md#m1-an-import-answers-in-one-line-and-a-default-agent-stops-being-a-binding)**
  (#424, PR #428). `_announced` replaces where the set is
  known; the client table reworded to stand alone; the import count
  line with the collapse rule and its pins; the new default-agent
  notice in `entities.py`, both `api.py` producers picking it
  (`write_default_agent` and `_applied_notice`), both pinned through
  the real API paths, `tests/support/notices.py` carrying it as the
  seventh `_COMPOSED` member; the `printable(..., UNBOUNDED)` door
  in `_acknowledged` with the single-write hostile-notice cases
  through `Act.read()`; the licensed substitution;
  `vinga-server/README.md`'s write transcript re-captured (the
  1116-area notice block and the mcp-server transcript);
  `cli-guide.md`'s "The sentence states and the client advises"
  passage amended to "the client speaks" in the same change;
  CHANGELOG `Changed`; the implementation-doc section. Closes #424.
  Design footprint: deepens `cli.py`'s acknowledgement renderers and
  `entities.py` (one more sentence that exists because no other will
  do); no new module. Documentation footprint: `vinga-server/README.md`
  (maintained map, carries the old two-voice transcript),
  `docs/architecture/cli-guide.md` (the practice its example
  falsifies), `CHANGELOG.md`, the census manifest.
- [x] **[M2: a diff prints its changes, grouped by the boundary they
  wait at](2026-09-06-cli-answer-style-implementation.md#m2-a-diff-prints-its-changes-grouped-by-the-boundary-they-wait-at)**
  (#425, PR #429). The grouped rendering, the content rule, the
  LiveKind sentence, the nothing-pending sentence, `DIFF_INTRO`
  deleted, the diff pins re-cut, the head table pinned total over
  `DiffApplies` with the existing refuses-whole case kept (review
  finding 2 withdrew the unknown-token head this bullet used to name);
  CHANGELOG; implementation doc. Closes #425. Design footprint:
  `_diff_listing` re-cut around the boundary axis the answer already
  carries; the shape-reading helpers (`named_lists`, `flags`,
  `nested`) are unchanged, so the contract-driven property stands.
  Documentation footprint: none beyond CHANGELOG and the census
  manifest; `docs/reference/cli.md` asserted byte-identical, and the
  cli-guide's diff example arrives with M5's practices since the
  guide has no diff worked example today.
- [x] **[M3: an apply says what happened and that it
  worked](2026-09-06-cli-answer-style-implementation.md#m3-an-apply-says-what-happened-and-that-it-worked)**
  (PR TBD). The content rule, the label table with its completeness
  pin, the apply-specific render callable (listing, flush, success
  sentence on stderr) wired as `APPLY.render` with the ordering pin, the MCP
  block only when entries exist; the apply pins re-cut; README
  step-3 prose checked against the new output; CHANGELOG;
  implementation doc. Design footprint: `_apply_listing`
  keeps its derived skeleton and gains the one table whose absence
  was the operator reading build-internals; deletion test says the
  table and the renderer stay in `cli.py` beside their only readers.
  Documentation footprint: `vinga-server/README.md`'s apply
  transcript (~1122-1148) re-captured with its surrounding prose
  reconciled; root `README.md` if step 3's surrounding prose
  describes the old shape; CHANGELOG; census manifest.
- [ ] **M4: `info` answers at a glance, with the URL protection
  kept.** The one-line server fact, the relabelled URL line above the
  bare URL, the one-line tally with derived plurals and the
  `nothing yet` arm; `test_config_cli_info.py` and
  `test_config_cli_summary.py` re-pinned; both root-README `info`
  transcripts re-captured; CHANGELOG; implementation doc. Design
  footprint: `_identity_block` and `_configured_counts` deepen; the
  wrap-protection reasoning stays in the docstring it lives in now.
  Documentation footprint: root `README.md` (two transcripts),
  CHANGELOG, census manifest.
- [ ] **M5: the practices, written where reviewers look** (#426). The
  five practices join `cli-guide.md` in its house shape (each with a
  merged example from M1-M4 and a historical counterexample quoted
  from the issues), the reviewer checklist at the top gains the
  questions they add, and the Getting Started walkthrough is re-read
  end to end against a live compose deployment if one is available,
  otherwise against the suite's rendered output, with the result
  recorded honestly in the PR. Closes #426. Design footprint: none
  (documentation). Documentation footprint: `docs/architecture/
  cli-guide.md`, CHANGELOG, census manifest; the docs workflow is
  this PR's CI shape.

## Plan review round

Backend codex (codex-cli 0.153.0), model `gpt-5.6-sol`, 2026-09-06,
against commit `52551544`; the reviewer ran 4m52s. Verdict: not
ready, pending the P1 amendments.

1. **P1: the new default-agent notice does not reach document
   imports.** The plan changes only `write_default_agent`; an import
   routes both `devices` and `default_agent` entries through
   `api._applied_notice`, whose fallback is `_binding_notice`
   (`api.py:2776-2807`), so the Getting Started import would still
   say "The binding". Also `tests/support/notices.py` already holds
   six notices, so the new one is the seventh, and it must join
   `_COMPOSED`.

   *Resolution*: accepted in full, and verified against `api.py`
   before amending: `default_agent` is not in `_SECTION_NOTICE`, so
   an imported entry falls through to `_binding_notice` at line 2807,
   which is exactly the path the walkthrough's specimen took. The
   plan now names both producers, gives `_applied_notice` its
   default-agent branch keyed on the entry's section, pins both the
   single write and the imported entry through the real API paths,
   and corrects the notice count to seven with `_COMPOSED` named as
   where the instance lands.

2. **P1: the unknown-token diff group head cannot pass `Act.read()`
   and contradicts the no-leak rule.** Diff `applies` fields are
   scalar `DiffApplies` literals, and `_declared`'s tolerance is for
   tuple-valued token sets only; an unknown scalar refuses the whole
   answer, which is #386's settled behavior. The quoted-token head is
   unreachable, and quoting a token also contradicts the plan's own
   statement that an unknown token is never echoed.

   *Resolution*: accepted in full; the quoted-head arm is withdrawn.
   It was written against a reading of the diff the shapes refute:
   `_declared`'s tolerance was deliberately scoped to sequences of
   closed tokens by #386's own review round, and the scalar branch
   refusing whole is recorded there as behavior that plan was not
   allowed to change. The group-head table is instead pinned total
   over `DiffApplies`, so the failure mode a widened alias would
   create is a failing test, and the existing refuses-whole
   assertion is named as kept rather than replaced.

3. **P1: the single-write fallback prints an untrusted notice without
   a display door.** `_acknowledged` passes
   `str(acknowledgement["notice"])` straight to `_announced`
   (`cli.py:4585`), unlike `_imported_entries`, which wraps its
   sentence in `printable(..., UNBOUNDED)`. The hostile-notice tests
   cover imports only, so an old or newer server can put an escape
   sequence or a lone surrogate on stderr through a single write.

   *Resolution*: accepted in full. The gap predates this plan, and
   the plan's no-leak paragraph overclaimed by not seeing it; both
   are corrected. `_acknowledged` gains the
   `printable(..., UNBOUNDED)` door in M1, the no-leak lens
   paragraph now states the door as added rather than inherited, and
   the single-write hostile cases (escape sequence, lone surrogate,
   driven through `Act.read()`, nothing retained on a chain) join
   M1's deliverables beside the import cases they mirror.

4. **P2: the apply success line needs a stream-aware renderer and the
   flush discipline.** `_apply_listing` returns one stdout string and
   `APPLY` wraps it in `_printed`; the success sentence cannot live
   there, and printing stderr afterwards without flushing stdout can
   land the success above the listing it is about.

   *Resolution*: accepted in full. The apply gains its own render
   callable in `cli.py`, shaped like `_imported`: listing to stdout,
   flush, sentence to stderr, wired as `APPLY.render`. The flush is
   the same discipline `_acknowledged` and `_imported_entries`
   document, and the ordering pin with a recording stream joins the
   M3 tests beside presence-on-success and absence-on-refusal.

5. **P2: M3 omits the maintained apply transcript it invalidates.**
   `vinga-server/README.md:1122-1148` carries the full current apply
   field dump and MCP status block, and none of the plan's inventory
   greps (`applies at reload`, `configured:`, `not yet serving`)
   finds it.

   *Resolution*: accepted in full. M3's documentation footprint now
   names the transcript with its line range and the reconciliation
   of the prose around it, and the inventory grep gains `(none)` and
   `defaults_changed`, the two terms that actually match an apply
   transcript, with the note that its `docs/plans/` hits are
   historical records that never move.

6. **P2: mixed known and unknown import boundaries are
   underspecified, and the named test can permit lost advice.** As
   written, a mixed answer prints the count and the unknown entries'
   sentences; nothing says the known entries' remedy survives, which
   contradicts the plan's own boundary-survives decision.

   *Resolution*: accepted in full. The mixed answer is now specified:
   the count line always prints, the remedy clause rides it whenever
   any entry carries an actionable set (once, since both actionable
   sets share the one remedy), and every non-actionable, absent or
   unknown set contributes its server sentence deduplicated by
   sentence. The named test now requires at least one recognized and
   one unknown entry and asserts both facts survive while the
   unknown token is never printed.

7. **P2: the determinism test the plan leans on does not exist for
   these renderers.** The named suites render each answer once and
   compare fields or substrings; no case renders twice and compares
   bytes, and no case compares the terminal and redirected paths of
   the new fixed sentences.

   *Resolution*: accepted in full; the plan no longer claims the
   style exists. Each milestone adds an explicit byte-equality case
   for its surface, streams captured separately where both are
   written, and the two new fixed stderr sentences get the
   terminal-versus-redirected comparison in #297's both-ways shape.
