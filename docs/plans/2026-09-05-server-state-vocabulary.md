# State the boundary, and let the client name the command

Plan for [#386](https://github.com/rafacm/vinga/issues/386).
Implementation notes land in the companion
`2026-09-05-server-state-vocabulary-implementation.md`, one section per
milestone, appended in the change that ticks the milestone here.

## Goal

A write to the configuration API is acknowledged with a sentence the
server composes, and two of the five sentences it can compose name a
command of the CLI's grammar. The CLI prints that sentence verbatim.
So a `vinga import` against an image built before #372 closed with
"run `vinga reload`", which the CLI installed beside it answers with
"that is not a command". Both halves were internally consistent; the
contradiction lived only in the pairing, which is why the
command-spellings census could not see it.

This plan moves the command half to the side that owns the grammar.
The server states which boundary a write is waiting at, as a closed
token it already publishes elsewhere; the client turns that token into
a sentence naming its own commands. After it, no sentence a
configuration write is acknowledged with names a client command, so no
image built from this commit onward can contradict any client, whatever
the grammar does next.

What it cannot do is repair an image that already shipped. The image
that produced #386 carries its bytes, and nothing merged here reaches
them. The guarantee is forward: the class closes for every image built
after this, and for the pairing a walkthrough hands out today the
CLI reference's install-nothing door already answers, since
`docker compose exec -T vinga vinga ...` runs client and server from
one build.

## The issue's decisions, restated

- **Direction one is the root fix.** The server states what state a
  thing is in, in a closed vocabulary; the client phrases the remedy in
  its own grammar. Settled, and not re-argued here.
- **Direction two is already shipped and is not this issue's work.**
  Pinning image and client together in the walkthrough is what the CLI
  reference's `docker compose exec` door does, and a dedicated CLI image
  was considered and argued against. This plan cross-references it once
  and adds nothing to it.
- **#369 is the pattern.** The OTA reply's `access` field has the server
  stating a fact in a closed set (`ota/reply.py:96-110`) while the
  client phrases what to do about it and tolerates a word it does not
  know (`simulator/board.py:357-363, 552`). This plan is the same shape
  applied to the acknowledgement.
- **Skew detection stays open in the issue.** Resolved below.

## Where the facts already live, and the census

Every count here is from a command, and every command is written out so
a reviewer can re-run it. The AST sweep is the one that matters, because
a plain grep cannot tell a sentence a caller receives from a comment
explaining one.

**The sweep.** Run from `vinga-server/`, it walks every module of the
package, parses it, and reports every assignment whose value contains a
backticked command spelling, whether written out or interpolated
through `PROGRAM` / `SERVER_PROGRAM`:

```python
import ast, pathlib, re
root = pathlib.Path("src/vinga_server")
pat = re.compile(r"`(?:\{(?:SERVER_)?PROGRAM\}|vinga(?:-server)?)((?: [a-z][a-z-]*)+)")
for path in sorted(root.rglob("*.py")):
    text = path.read_text()
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            src = ast.get_source_segment(text, node.value) or ""
            for m in pat.finditer(src):
                print(path, node.lineno, m.group(0))
```

It finds **46 spellings**. Two sites it cannot see, because neither is
an assignment and neither uses backticks, were found by
`grep -rn "vinga-server config" src/vinga_server/config/store.py
src/vinga_server/events/` and are counted below:
`store.py:1919` (the secret-holder refusal, which composes
`f"{SERVER_PROGRAM} {holder.name} set"` off the descriptor) and seven
message templates in `events/catalog.py`.

The supporting counts:

- `grep -rn "PROGRAM" src/vinga_server | awk -F: '{print $1}' | sort | uniq -c`
  → 71 references in 7 files: `config/cli.py` 23, `config/entities.py`
  22, `config/docgen.py` 11, `config/models.py` 7, `config/loader.py` 4,
  `config/store.py` 2, `config/server_reference.py` 2.
- `grep -o '`vinga[^`]*`' docs/reference/api-openapi.json | sort | uniq -c`
  → 11 spellings in the served OpenAPI document: 8 name the client
  program `vinga`, 2 name `vinga-server config`, 1 is a directory path.

**Classified by the surface each reaches at runtime.**

**(a) API response bodies that travel to a remote client: 7 constants,
9 spellings.** This is the issue's coupling in its sharpest form.

| constant | spelling | how it travels |
| --- | --- | --- |
| `entities.APPLY_NOTICE` | `vinga apply`, `vinga diff` | `Acknowledgement.notice`, `AppliedEntry.notice` |
| `entities.BINDING_UNSERVED_NOTICE` | `vinga apply` | the same two fields |
| `api._UNKNOWN_CODE` | `vinga-server config device pending list` | 404 `Problem.detail` |
| `api._CLAIM_REFUSED` | `vinga-server config list` | 4xx `Problem.detail` |
| `api._UNLOADED_AGENT` | `vinga-server config apply`, `... list` | 404 `Problem.detail` |
| `store.ALREADY_BOUND` | `vinga-server config device show <mac>` | 409 `Problem.detail` |
| `store.py:1919` | `vinga-server <noun> set` | 404 `Problem.detail` |

Only the first two name the **client** program. The five refusals name
the server's own in-image spelling, which is a deliberate mitigation
recorded at `models.py:115-125` and `loader.py:100-127`: a server
composes them, and a server runs inside the image. That mitigation is
narrower than it reads, and two facts found by this census say so, both
of them worth recording:

- **The premise is stale.** `pyproject.toml:108-115` ships two console
  scripts from one distribution, and the CLI reference's door is
  `docker compose exec -T vinga vinga list`
  (`docs/reference/cli.md:213`). The short script *is* installed in the
  image. The comment at `models.py:119` says it is not.
- **The mitigation does not close the skew class.** An old image
  composing `vinga-server config reload` is as wrong on a new client's
  host as `vinga reload` is: what went stale is the verb, and the
  program word does not hold it still. The mitigation fixes *which host
  the advice is for*, which is a different defect.

Scoped accordingly: this plan fixes the acknowledgement, and the
refusals are resolved below with a reason and a follow-up.

**(b) Server logs, boot output and the event stream.** The seven
`events/catalog.py` templates and the two moved-key refusals in
`loader.py:654,806` rendered through `served()`. Versioned with the
server and read where the server runs. Unchanged, and no line of this
plan touches them.

**(c) Generated documents and OpenAPI descriptions the server serves:
25 spellings.** `entities.py` descriptor prose and `command=` fields
(8), `models.py` field descriptions (4), `responses.py` field
descriptions (3), `docgen.py` (4), `conversations/docgen.py` (4),
`server_reference.py` (1), `events_docgen.py` (1). Self-consistent per
build. Unchanged, with the reason below.

**(d) CLI-side renderings: 13 spellings across 12 constants in
`config/cli.py`.** Correct by construction, and already inside the
census guard's reach: `tests/unit/test_command_spellings.py` holds
every `respell` match to naming a command the registered tree has, and
one checkout can see both halves.

**What is already right, and is the template.** `responses.Applies` is
a `StrEnum` of three boundary tokens (`restart`, `reload`, `check-in`,
`responses.py:647-666`) carried by all seven `applies` fields of the
diff. `config/diff.py:81-103` is its one decision site. `cli.py:3285-3295`
is the client half already merged: `DIFF_INTRO` explains the API's
tokens and says which of *its own* commands crosses each of them, with
a comment recording that the token stays the API's word so a generated
client reads what this one does. The acknowledgement is the one surface
that did not get this treatment.

**And the duplication it left.** `tests/support/notices.py` is a fourth
encoding of the same vocabulary: four token names, and an
`_ANNOUNCED_BY` table that recovers a boundary from an acknowledgement
by looking for a phrase in the prose, one of the phrases being
`vinga apply`. Two structures that must agree, held together by a
substring search.

**Two staleness bugs this census turned up**, both of them the same
class as #386 and both fixed on the way past:

- `Acknowledgement.notice`'s own description (`responses.py:1137-1156`,
  committed in `docs/reference/api-openapi.json`) says the sentence
  "names `POST /runtime/config/reload`" and "names the three moments a
  conversation meets an applied change at". Since #372 it names neither:
  the route is not in it and #371 deliberately removed the three clocks.
  The served contract describes a notice the server stopped sending.
- `vinga-server/README.md:1085-1096` shows a transcript whose prompt is
  `vinga-server config mcp-server set` and whose printed notice says
  `vinga apply`. A reader following that transcript inside the image is
  told to type a command in the other of the two spellings, in the same
  code block. The coupling, visible in a committed document.

**Where the current sentences are pinned**, which is the pin-before-
reshaping answer: `tests/unit/test_config_api_writes.py:322-340` (three
cases, two of which assert that `vinga apply` and `vinga diff` are *in*
the notices), `tests/support/notices.py:47`,
`tests/unit/test_config_cli_rendering.py:1099,1175`, and most rigidly
`tests/unit/test_config_cli_respelling.py:124-140`, a frozen
pre-rename transcript plus a table of licensed substitutions whose
docstring states the assumption this plan retires: "the reload notice
names the command that applies a write. Those move with the grammar,
deliberately."

## Open questions, resolved

### Which strings change: the acknowledgement, and nothing else

**Class (a)'s two notices change.** They are the only server-composed
sentences that name a *client* command, they are the ones #386 hit, and
they have a closed vocabulary already on the wire to state instead.

**Class (a)'s five refusals do not change here, and a follow-up issue
is filed for them.** They are the same coupling, and the reason to
leave them is mechanical rather than principled: an acknowledgement can
drop its command half because `Applies` already exists to carry the
fact, while a refusal has no machine-readable identity at all.
`Problem` deliberately carries no `type` (`responses.py:1077-1084`,
"`type` and `instance` are deliberately absent"), so a client has
nothing to map to a sentence of its own, and de-commanding the prose
without giving the client a token makes the refusal strictly less
useful: "run `vinga-server config list` to see the agents that exist"
would become a paragraph that names no way to look. Naming the API
route instead was considered and rejected: a route in an operator's
terminal is worse than a command, and it is the shape the stale
`Acknowledgement.notice` description already demonstrates aging badly.
The follow-up issue records the mechanism the fix needs (a problem-type
vocabulary), the two facts this census established about the
`SERVER_PROGRAM` mitigation, and the six spellings involved.

**Class (b) does not change.** An event message and a boot refusal are
read where the server runs, are shipped in the image that composed
them, and name that image's own command. Nothing crosses a version
boundary.

**Class (c) does not change.** A field description naming
`vinga agent preview <agent>` is a pointer to where a fact is visible,
not a remedy for a state the reader is in. It is rendered from the
build that serves it, and it stales the way a README stales rather than
the way #386 staled: nobody is told to type it in answer to something
that just happened. Recorded rather than swept, because sweeping 25
sites for a defect nobody has met would churn `domain-config.md`,
`api-openapi.json`, the conversations schema and the census manifest for
no operator-visible gain.

**Class (d) does not change** and is where a spelling belongs.

### The replacement pattern: `Applies` on the acknowledgement

Each acknowledgement carries the boundaries it is waiting at, as a
tuple of the token the diff already publishes, beside the prose.

- **The closed set has one home: `responses.Applies`.** It gains a
  fourth member, `STORE_BOOT = "store-boot"`, because a write to a
  server serving a handed configuration waits at a boundary the diff
  never reports. The enum's docstring, which today says "three
  boundaries and no fourth", is corrected: the server has four, and a
  diff can announce three of them. The seven diff fields narrow to a
  named alias, `DiffApplies = Literal[Applies.RESTART, Applies.RELOAD,
  Applies.CHECK_IN]`, so the contract keeps saying exactly what the
  diff can emit; a test asserts the alias's members plus `STORE_BOOT`
  are the whole enum, so the two cannot drift. The cheaper option,
  widening the enum and leaving the diff fields declaring a value they
  never send, was rejected: an honest seam is the point of the field.
- **The pairing has one home: `entities.py`.** The five notices become
  five instances of a frozen `Notice` dataclass carrying `applies` and
  `sentence`, and a descriptor's `notice` is one of those. The sentence
  and the boundaries it announces are then one structure, which is what
  ends the substring search in `tests/support/notices.py`. `entities`
  imports `Applies` from `responses`, a new edge and a safe one:
  `responses` imports pydantic and nothing of this server, which is the
  property `test_cli_import_weight.py` already holds it to.
- **The field is defaulted, not required, and that is the one
  deliberate exception** to the rule `SecretSlot.shadows` states
  ("nullable and required, not optional"). A server older than the
  vocabulary sends no key; `cli._declared` drops what the shape does
  not carry and hands the rest to strict validation, so a required
  field would turn an old image's *successful write* into a refusal
  the client raises after the write landed. Making #386's confusing
  sentence into a hard failure is a worse answer than #386.
- **No model validator ties `applies` to `notice`.** The invariant (a
  written entry has both, an unchanged one has neither) is enforced
  where the value is produced, by the `Notice` pairing, and pinned by a
  test. A validator on a shared model would fire on the client and
  punish it for the server's age, which is the same defect as the
  required field.
- **The client maps tokens to its own sentence**, from one table in
  `cli.py` beside the three renderings that read it. `DIFF_INTRO`'s
  gloss is rebuilt from that table, so the `{PROGRAM} apply` at
  `cli.py:3290` and the one in the new advice are one string rather
  than two. Because the spelling now lives on the client's side of the
  boundary, the command-spellings census guard covers it: a future
  rename that missed it fails a test in the same checkout, which is
  exactly the reach #386 says the census does not have across the
  boundary.
- **The forward-compatibility rule: an unknown token renders as the
  state's own words, never a guessed command.** The client renders its
  remedy when every token in `applies` is one it knows; when the field
  is absent, empty, or carries a token it does not know, it prints the
  server's sentence alone and adds nothing. This is `board.py:552`'s
  rule (`reply.access if reply.access in KNOWN_ACCESS else None`)
  applied to a set instead of a word.
- **And the rule has to be kept before the renderer, in
  `cli._declared`.** The OTA precedent gets its tolerance from a second
  module: `ota/reply.py` types the producer closed and
  `simulator/board.py:366-385` types the consumer `str | None`, with a
  comment saying a `Literal` there would make an unknown word a
  malformed reply, "the harsher of the two readings and the wrong one
  for a client". This surface has no second module to put that in, and
  is not allowed one: `cli.py:2255-2258` records that a hand-kept
  second encoding of the API's shapes is what the `_understood` helper
  exists to have removed. So the tolerance is a rule about the shape,
  in the one place that already holds shape-guided rules. `_declared`'s
  tuple branch gains it: **a sequence of a closed token is read whole
  or not at all**, so one member this client does not recognize makes
  the sequence a fact it cannot act on, and the honest reading of that
  is the same as an older server's silence, the field's default. Stated
  as a shape rule and not as a field name, which is the discipline that
  branch is written to ("guided by the shape and not by a list of field
  names"). The scalar enum branch is untouched: an unknown `applies` on
  a diff still refuses the whole answer, which is today's behavior and
  is not this plan's to change. Verified against pydantic before
  writing this: a missing key and an explicit `()` both validate to the
  default under `strict=True`, and a tuple carrying one unrecognized
  member raises `ValidationError`, which is exactly the refusal the new
  rule intercepts.
- **The import dedupe keys on whichever half is doing the work.** Today
  it deduplicates the printed sentences (`cli._imported_entries`), so a
  document that wrote nine entities waiting on one apply prints one
  line. Keying on the boundary set alone would be right for a server
  that sends one and wrong for one that does not: every entry from an
  older server carries the same empty set, so an ordinary stored entry
  and a device binding, two different sentences, would collapse into
  one and an operator would be told half of what they are waiting on.
  The key is therefore the boundary set where there is one, and the
  sentence where there is not, which is the same rule the rendering
  keeps one level up: a known set is spoken by the client, and an
  unknown or absent one is quoted from the server.
- **The prose stays, and stops naming commands.** Keeping it is what
  makes an old client safe against a new server: it prints the sentence
  verbatim, and a state-only sentence is never wrong. Removing the
  command from it is therefore the load-bearing half of this plan, and
  the token is what buys back the advice the operator loses.

### Skew detection: no version comparison, and the hook recorded

The client does know the server's revision (`RuntimeInfo.revision`,
printed by `vinga info`), and it should not compare it. `build_info.py`
resolves the revision from a build argument, else `git describe
--always --dirty`, else `unknown`; none of those three is orderable
against a client's own grammar, so a comparison would have to encode a
table of grammar epochs, which is a second structure that must agree
with the first.

What replaces it is better and is free: after this plan, an
acknowledgement carrying no `applies` field *is* a server older than
this vocabulary, detected at the one place the staleness bites, with no
version arithmetic. The client's response to it is to quote the server
rather than guess, which is the honest thing to do about a sentence it
cannot improve.

Nothing further is filed for this, deliberately: once the server stops
naming client commands, the contradiction class #386 hit cannot recur
for any image built from here, so a warning would announce a mismatch
that no longer has a consequence. The place this must be revisited is
[#287](https://github.com/rafacm/vinga/issues/287), where the CLI
becomes a separate distribution with its own release cadence and mixed
versions become ordinary rather than accidental; the absent-field
signal is the hook it would attach to, and the plan says so here so
that it is not rediscovered.

### Compatibility posture: additive only, and three artifacts move

The pre-release stance holds (no third-party installs to support,
boards resettable), so an old image is not a compatibility target and
no negotiation, no version header and no dual-shape rendering is
built. What the stance does not license is *breaking* on an old image,
because the walkthrough pairs an old image with a new client by
construction, which is how #386 was found. So every wire change here is
additive: a new optional field on two response models, a fourth member
on an enum whose existing fields narrow to the three they already emit,
and no field removed, renamed or retyped.

The deliberate artifact moves:

- `docs/reference/api-openapi.json`, twice. M1 adds the two `applies`
  fields, the fourth enum member and the corrected
  `Acknowledgement.notice` description; M2 rewrites
  `AppliedEntry.notice`'s description and `_one_outcome`'s error text,
  so it regenerates too. One consequence of the narrowing is worth
  naming before a reviewer meets it in the diff, because it is larger
  than it sounds: pydantic renders a `Literal` of enum members inline
  rather than as a `$ref`, so the seven diff fields stop pointing at
  the `Applies` component and carry a three-value enum each. The
  component survives, referenced by the two new `applies` fields, which
  is where the whole vocabulary is now published. The trade is
  deliberate: an inline `Literal` in this document is the merged
  precedent already (`AppliedEntry.section`), and a field declaring a
  value it never sends is the thing the honest-seam rule exists to
  refuse. Regenerated with
  `uv run vinga-server config openapi > ../docs/reference/api-openapi.json`,
  which `tests/unit/test_api_openapi.py` names in its own staleness
  message.
- `vinga-server/tests/unit/command-spellings.txt`, in both milestones.
  The manifest records physical line positions across every tracked
  file, so this plan's own document, the CHANGELOG entries and the
  implementation-doc sections stale it whatever the code does.
  Regenerated with
  `uv run python -m tests.unit.test_command_spellings`, before the unit
  lane.
- `docs/reference/cli.md` is expected to stay byte-identical and its
  freshness pin says so; nothing here changes a help epilog. If M2's
  advice sentence is ever surfaced in `vinga apply --help`, that is a
  separate decision and not taken here.

`tests/unit/test_api_contract.py` reads the committed JSON as bytes and
compares each act's declared answer against it, so the regeneration is
what keeps it green; it is not a second thing to edit.
`docs/reference/domain-config.md` and `docs/reference/events.md` are
untouched, both being class (c) and (b) respectively.

## Module layout

No new module. Four deepened, and one client-side table that stays
beside its callers.

- **`config/cli.py` gains the remedy table**, beside `DIFF_INTRO`,
  `_acknowledged` and `_imported_entries`, which are its three
  consumers and all of them are here. It does not become a module of
  its own, and the deletion test is why: inlining it leaves one table
  read from three places in the same file, which is not two structures
  that must agree, and a `config/remedies.py` is then a name that hides
  nothing. The design guide names that shape directly, as a
  `config/cli_render.py` "that exists only because `cli.py` is long",
  and records the merged instance of the positive form: the derived
  `outcomes` fact "lives in `cli.py` now, beside the renderer that is
  its only caller" (`design-guide.md:99-124, 258-266`). What the table
  ends is still real and is what the milestone claims: the
  `{PROGRAM} apply` at `cli.py:3290` and the acknowledgement's advice
  become one string instead of two.
- **`config/entities.py` deepens.** A notice stops being a bare string
  and becomes the pair it always was, so nothing downstream has to
  recover the boundary from the words.
- **`config/responses.py` deepens.** The wire vocabulary it already
  declares grows the fourth member the acknowledgement needs, and the
  diff's use of it narrows to what the diff emits.
- **`tests/support/notices.py` shrinks to an adapter.** It stops
  sniffing prose and reads the field; its four token names become
  imports of `Applies` rather than a fourth encoding.
- **No new seam in `api.py`.** The route already asks the descriptor
  for its notice; it asks for both halves of one object instead of one
  string.

## Tests

Reusing what exists wherever the assertion already has a home.

- `tests/support/notices.py` re-anchored on the field. Every downstream
  suite that asserts in boundary tokens keeps its assertions unchanged,
  which is that module doing its job; what changes is that a prose edit
  can no longer silently move a boundary.
- `tests/unit/test_config_api_writes.py:322-340`: the two cases that
  assert `vinga apply` and `vinga diff` are *in* the notices invert to
  assert they are *not*, and gain the boundary-set assertion in their
  place. The third case (the three clocks are absent) stands unchanged.
- **The closed-set pin.** `set(get_args(DiffApplies)) | {Applies.STORE_BOOT}
  == set(Applies)`, so a fifth boundary cannot be added on one side
  only, and every `Notice.applies` member is an `Applies` member.
- **And the pin that proves the narrowing is enforced**, which the one
  above does not: it is membership bookkeeping, and seven annotations
  could still be typed `Applies` while it passes. So each of the seven
  diff models is constructed with `Applies.STORE_BOOT` and asserted to
  raise, which is what says the narrowing reached the fields rather
  than only the alias. Verified against pydantic while amending: a
  `Literal` of enum members rejects a member outside it and accepts
  every member inside it.
- **The invariant that keeps the class closed.** No `Notice.sentence`
  contains `PROGRAM`. This is the guard #386 asks for, scoped to the
  surface it is true of: it is one line, it fails loudly on a
  reintroduction, and it does not overreach into the refusals, which
  still name commands deliberately. It joins
  `tests/unit/test_config_api_writes.py` rather than the census, whose
  three classes are about file-and-line spellings across the tree and
  cannot express "reaches a response body".
- **The census stays where it is** and covers the remedy table with no
  change: `cli.py` is already swept, and every `respell`-class spelling
  in it is held to the registered tree by the guard in
  `tests/unit/test_command_spellings.py`. That the spelling is now
  inside the guard's reach is the plan's central claim, and the guard
  proving it needs no new code is the evidence.
- **The fallback cases, driven through `Act.read()` and not through the
  renderer.** A renderer-only case would pass while the real path
  refuses the body before rendering, which is the failure mode the
  review round caught, so each case starts from a body and goes through
  the act that reads it: an acknowledgement with no `applies` key
  prints the server's sentence alone; one carrying a token this client
  does not know does the same, rather than raising; one carrying a
  known set prints the server's sentence and the client's remedy under
  it. All three run twice, once through the single-write act
  (`Acknowledgement`) and once through the import act
  (`AppliedDocument`), because the tolerance lives in a shape walk and
  a nested entry is where a walk goes wrong. The unknown-token case
  also asserts the token itself is never printed: what a body put in a
  closed field is not this client's to echo. The middle case is #369's
  `sometime-in-the-future` bite (`test_simulator_board.py:471`) in this
  surface's terms.
- **The producer side stays closed**, asserted from the other end: every
  `Notice.applies` member is an `Applies` member, so no route can emit
  the token the client is being taught to tolerate. Tolerance is a
  reading rule, never a licence to send.
- `tests/unit/test_config_cli_respelling.py` gains one licensed
  substitution for the new stderr text, and its docstring's claim that
  a notice names the command that applies a write is amended to say
  where that half went. The frozen transcript is not recaptured, which
  is the whole point of it.
- `tests/unit/test_api_openapi.py` and `test_api_contract.py` are the
  proof the committed document moved deliberately and in one piece;
  neither needs editing.
- The import dedupe keeps its existing case and gains two: two entries
  waiting at the same boundary print one remedy, so a future prose edit
  cannot split a dedupe; and a mixed-version case where two entries
  carry different notices and no `applies` at all prints both
  sentences, which is what the old-server arm has to do and what a
  boundary-only key would have collapsed.

## Risks

- **The `Applies` widening reaches the diff's contract.** Mitigated by
  the narrowed alias, which keeps the diff declaring three, by the
  equality pin, which fails if either side gains a member alone, and by
  the rejection pin, which fails if a field kept the wide annotation.
  The regenerated OpenAPI document is where a reviewer sees the whole
  effect in one diff, the inlining included.
- **The shape rule in `_declared` reaches further than one field.** It
  is written for a sequence of closed tokens, and today there is
  exactly one such shape in `responses.py`, the field this plan adds.
  That is the bound: the scalar enum branch keeps refusing, so no
  existing field's reading changes, and the two `Act.read()` cases are
  what hold the new branch to the shape it claims.
- **A defaulted field is a hole a server could fall through.** The
  server always sets it; nothing but an older server omits it. Held by
  the pin that every `Notice` carries a non-empty `applies`, and by the
  `_one_outcome` validator already refusing an entry whose outcome and
  notice disagree.
- **An operator loses actionable advice if M2's client half is wrong.**
  This is why the milestones are cut where they are: M1 changes no
  printed byte, and M2 changes the sentence and adds the remedy in one
  commit, so review sees the before and after of the same terminal
  output rather than a window where the advice is gone.
- **The census manifest stales on this plan's own files.** It does,
  every time; regenerate through its module before the unit lane, as
  the two most recent plans record.
- **The stale `Acknowledgement.notice` description is load-bearing
  prose in a committed contract.** Rewriting it in M1, where the field
  it describes is being changed anyway, is what keeps it from being
  rewritten twice.

## Milestones

- [ ] **M1: the boundary an acknowledgement announces, on the wire.**
  `Applies` gains `STORE_BOOT` with its docstring corrected, and the
  seven diff fields narrow to `DiffApplies`; `Acknowledgement` and
  `AppliedEntry` gain `applies`, defaulted so an older server's body
  still validates; the five notice constants become `Notice` pairs in
  `entities.py` and `api.py` reads both halves; `cli._declared` gains
  the read-whole-or-not-at-all rule for a sequence of closed tokens,
  with the two `Act.read()` cases proving an unknown member reads as
  the default rather than raising; `tests/support/notices.py`
  reads the field instead of the prose; the stale
  `Acknowledgement.notice` description is rewritten to describe what
  the server sends today; the closed-set pin and the
  `STORE_BOOT`-rejection pin; `docs/reference/api-openapi.json`
  and the census manifest regenerate; a CHANGELOG `Added` entry; the
  implementation-doc section. The field and the rule for reading it
  land together, because a field whose tolerance arrives a milestone
  later is a field that refuses in between. No printed byte changes, so
  this merges releasable on its own. Design footprint: deepens
  `responses.py` (the
  vocabulary it publishes now covers every boundary a write can wait
  at) and `entities.py` (a notice is the pair it always was); removes
  the fourth encoding in the test support; no new module, and the only
  interface widening is one field on two models. Documentation
  footprint: `docs/reference/api-openapi.json`, a generated reference,
  regenerates through `vinga-server config openapi`;
  `vinga-server/tests/unit/command-spellings.txt` regenerates through
  its own module; `CHANGELOG.md`, a dated execution record, gains the
  entry.
- [ ] **M2: the sentence states, the client advises.** `APPLY_NOTICE`
  and `BINDING_UNSERVED_NOTICE` lose their command halves and state
  only what is true of the write; `cli.py` gains the table holding this
  client's sentence per boundary set, and `DIFF_INTRO` is rebuilt from
  it; `_acknowledged` and `_imported_entries` print the server's
  sentence with the client's remedy under it, falling back to the
  server's sentence alone for an absent, empty or unknown set, and the
  import dedupe keys on the boundary set where there is one and on the
  sentence where there is not; the two inverted pins, the
  no-`PROGRAM`-in-a-sentence invariant, the fallback cases through
  `Act.read()` and the mixed-version import case;
  the licensed substitution and the amended docstring in
  `test_config_cli_respelling.py`; the follow-up issue for class (a)'s
  five refusals is filed; a CHANGELOG `Changed` entry; the
  implementation-doc section. Behavior changes sit alone in this
  review. Design footprint: no new module; the seam that changes is the
  acknowledgement's, which now crosses as a token rather than as a
  sentence with a command in it, and the client's answer to it is one
  table in `cli.py` beside its three consumers, so `cli.py` loses a
  duplicated spelling rather than gaining a layer.
  Documentation footprint:
  `docs/architecture/cli-guide.md` is the guideline that must move, its
  practice "A write says what it did and when it takes effect"
  (line 607) currently reading "`APPLY_NOTICE` names the boundary and
  the two commands either side of it"; `vinga-server/README.md`, a
  maintained map, carries the notice verbatim in a transcript
  (line 1096) and describes it in prose (line 1760), and the transcript
  is where the coupling is visible in a committed document today;
  `docs/reference/api-openapi.json` regenerates if a description moves
  and `docs/reference/cli.md` is asserted byte-identical;
  `vinga-server/tests/unit/command-spellings.txt` regenerates;
  `CHANGELOG.md` gains the entry.

## Plan review round

Backend codex, model `gpt-5.6-sol`, 2026-09-05, against commit
`44dcd490`; the reviewer ran 3m23s. Verdict: ready after the P1/P2
amendments.

1. **P1: unknown boundary tokens cannot reach the promised fallback.**
   The plan requires `applies` to use the closed `responses.Applies`
   vocabulary and also requires an unknown token to render the server
   sentence alone. The CLI validates shared response models strictly:
   `_declared()` leaves an unknown enum value as a string specifically
   so validation refuses it, and `_understood()` then raises
   `ConfigError` (`cli.py:2261-2292, 2307-2325`). Unlike the OTA
   precedent, whose client field is deliberately `str | None`
   (`simulator/board.py:366-385`), the proposed acknowledgement field
   has no tolerant client-side representation, so a renderer-only
   unknown-token test would pass while the real `Act.read()` path fails
   before rendering. Define how producer-side closed validation and
   consumer-side forward compatibility coexist, and exercise both
   `Acknowledgement` and `AppliedDocument` through `Act.read()` with an
   unknown token, asserting the token is never emitted.

   *Resolution*: accepted in full, and verified against pydantic before
   amending (a missing key and an explicit `()` both validate to the
   default under `strict=True`; a tuple carrying one unrecognized
   member raises `ValidationError`). The tolerance cannot be a second
   client-side shape here, the way `simulator/board.py` gets one,
   because there is one shared module and `cli.py:2255-2258` records
   that a hand-kept second encoding is exactly what `_understood` was
   introduced to remove. So it becomes a shape rule in the one place
   that already holds shape-guided rules: `_declared`'s tuple branch
   reads a sequence of closed tokens whole or not at all, and one
   member this client does not recognize reads as the field's default,
   which is an older server's silence. The scalar enum branch is
   untouched, so no existing field's reading moves. The plan now states
   the rule and its reasoning beside the forward-compatibility bullet,
   moves the field and the rule into the same milestone (a field whose
   tolerance arrives later is a field that refuses in between), drives
   every fallback case through `Act.read()` rather than the renderer
   and runs each twice, once through `Acknowledgement` and once through
   `AppliedDocument`, asserts the unknown token is never printed, adds
   the producer-side pin that no `Notice` can emit a token outside the
   enum, and records the new branch's blast radius as a risk.

2. **P2: boundary-only import deduplication loses old-server notices.**
   Every entry from an older server defaults to the same empty set, so
   deduplicating solely by boundary set collapses two distinct legacy
   notices (an ordinary stored entry and a device binding) into one.
   Deduplicate known client remedies by boundary set but preserve
   distinct fallback server sentences when `applies` is absent, empty or
   unknown, and add a mixed-version import case with two different
   notices and no `applies`.

   *Resolution*: accepted in full. The plan now states the dedupe key
   as the boundary set where there is one and the sentence where there
   is not, which is the same rule the rendering keeps one level up (a
   known set is spoken by the client, an unknown or absent one is
   quoted from the server), with the collapse it avoids written out:
   an ordinary stored entry and a device binding both arriving with an
   empty set are two different sentences and one of them would have
   been dropped. The mixed-version import case joins the tests and the
   M2 deliverables.

3. **P2: `config/remedies.py` fails the deletion test.** Its only caller
   is `cli.py`, and inlining would still leave one table shared by
   `DIFF_INTRO`, `_acknowledged` and `_imported_entries`, not two tables
   that must agree. The design guide keeps a derived fact beside its
   only caller and names a `config/cli_render.py` extraction as the
   counterexample (`design-guide.md:99-124, 258-266`). Keep the remedy
   table and its rendering helper in `cli.py` beside their consumers.

   *Resolution*: accepted in full, and the citation checked before
   accepting: the guide's counterexample is a `config/cli_render.py`
   "that exists only because `cli.py` is long", and its worked example
   records the positive form as merged, the derived `outcomes` fact
   living "in `cli.py` now, beside the renderer that is its only
   caller". The plan's justification was simply wrong: inlining leaves
   one table read from three places in one file, not two structures
   that must agree. The module is gone from the plan; the module-layout
   section now opens "No new module" and says where the table lives and
   why, the replacement-pattern bullet and the M2 design footprint
   follow, and the census bullet no longer claims a new file is what
   brings the spelling into the guard's reach, since `cli.py` is
   already swept. What the table ends is unchanged and is still
   claimed: `DIFF_INTRO`'s spelling and the acknowledgement's advice
   become one string.

4. **P2: M1's narrowed diff types leave an existing CI fixture
   invalid.** `test_config_api_runtime.answer()` constructs the
   provider, agent-defaults and agent sections with `Applies.RESTART`
   though the real decision table emits `RELOAD`
   (`test_config_api_runtime.py:877-907`, `diff.py:81-103`); those
   constructions become invalid under the proposed `Literal`, so M1 as
   named is not releasable. Amend the fixture to the actual boundaries,
   and add a contract test proving each diff model rejects
   `STORE_BOOT`: the enum-set equality pin proves membership
   bookkeeping, not that the seven annotations enforce the narrowing.

   *Resolution*: the second half accepted, the first refuted with
   evidence. The narrowing is one alias with three members,
   `Literal[Applies.RESTART, Applies.RELOAD, Applies.CHECK_IN]`, and
   `Applies.RESTART` is in it, so the fixture's constructions stay
   valid and M1 stays releasable; this was checked by constructing the
   models rather than reasoned about. The fixture's boundaries are not
   a claim about `diff.py`'s table either: its docstring says it
   composes "every kind present with its own regime", which is variety
   for the cases that read it, and the real table has its own pin. So
   the fixture is left alone, and touching it would be churn in the
   milestone that must move no printed byte. The pin the finding asks
   for is right and is added: each of the seven diff models is
   constructed with `STORE_BOOT` and asserted to raise, since
   membership bookkeeping would pass while seven fields kept the wide
   annotation. Amending also turned up a consequence the plan had not
   named: a `Literal` of enum members renders inline rather than as a
   `$ref`, so the seven diff fields stop pointing at the `Applies`
   component, which now survives through the two new fields. That is
   recorded with the artifact moves, with the reason the trade is taken.

5. **P2: `AppliedEntry.notice` remains a stale served contract.** The
   plan rewrites only `Acknowledgement.notice`'s description;
   `AppliedEntry.notice`'s (`responses.py:1206-1211`,
   `api-openapi.json:4919-4929`) still says the notice itself tells when
   the change takes effect, and `_one_outcome`'s documentation and error
   text call the notice "the boundary" (`responses.py:1214-1244`).
   Rewrite `AppliedEntry.notice` in M2 to describe the state sentence
   and direct boundary semantics to `applies`, update `_one_outcome`'s
   prose and error text, then regenerate the OpenAPI document.
