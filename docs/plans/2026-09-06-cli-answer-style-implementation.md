# A CLI answer says what happened, in the operator's words: implementation

Companion to
[`2026-09-06-cli-answer-style.md`](2026-09-06-cli-answer-style.md). One
section per milestone, recording what was actually written, the
deviations from the plan, what building it turned up, and the
verification results as they came out. A milestone with no deviations
says so explicitly.

## M1: an import answers in one line, and a default agent stops being a binding

### What was done

`config/entities.py`. `DEFAULT_AGENT_UNSERVED_NOTICE` is the seventh
notice: the same two boundaries as the binding's (`reload`,
`check-in`), and a sentence about the row that was actually written. It
says what a default agent is, which is the fact the binding sentence has
no room for: it covers every device that has no binding of its own, so
the devices it is true of are precisely the ones an operator's document
never named. The comment above the notices says seven rather than six,
and the paragraph on `APPLY_NOTICE` that pointed at `cli.REMEDIES` now
describes the replacement rather than the advice under it.

`config/api.py`. `_binding_notice` gained a third parameter, `unserved`,
defaulted to `BINDING_UNSERVED_NOTICE`: the two questions it asks
(is the named agent loaded, does this server read a store at all) are
the same for both live rows, and the only thing it cannot ask is which
row was written. `write_default_agent` and `_applied_notice`'s
default-agent branch pass the new notice; every other call site is
untouched. The branch in `_applied_notice` is keyed on `entry.section`,
which is the path #424's specimen came down: `default_agent` is not in
`_SECTION_NOTICE`, so an imported entry fell through to the binding
sentence.

`config/cli.py`. `REMEDIES` is `SPOKEN`, reworded so each line stands
alone rather than following a sentence: the state first (`stored, not
serving yet`), the command after it. `_announced` returns
`SPOKEN.get(applies, sentence)`, so this client's line replaces the
server's where it knows the set and quotes it where it does not; the
unknown, absent and nothing-to-run-about arms are unchanged behavior
reached by the same lookup. `NOT_SERVING_YET` is the one clause a whole
document is answered with, beside `INSTALLS` and for the reason stated
there: both known sets are waiting on the one install this grammar has.
`_imported_entries` answers with the count line first (`imported N
entries`, with the clause where any entry carries an actionable set) and
then every non-actionable, absent or unknown entry's server sentence,
deduplicated by the sentence itself. `_acknowledged` passes the server's
sentence through `printable(..., UNBOUNDED)` before `_announced`, which
is the display door the import path already had and the single write did
not.

`tests/support/notices.py`. The new notice is the seventh member of
`_COMPOSED`. The printed-output arm reads `_ANNOUNCED`, which is
`_COMPOSED`'s sentences and `cli.SPOKEN`'s lines with the boundaries
each announces, both derived rather than restated.

The pins. `test_config_api_writes.py`: the unserved default agent
answered by its own sentence through the route, an applied document's
`default_agent` and `devices` entries answered by their own sentences
through `POST /apply`, the sentence-shape pin beside the binding's, and
the composed count at seven. `test_config_cli_rendering.py`: the five
boundary states re-pinned on both surfaces (one line, from whichever
side can say it), the collapse over a document carrying both known sets,
an unchanged entry counted by neither half, the all-unchanged document
printing nothing, the mixed document keeping the clause and the quoted
sentence with the unknown token reaching neither stream, byte equality
across two renders of one answer per stream, and the single write's
hostile-notice cases (escape sequence, lone surrogate, nothing retained
on a refusal's chain). `test_config_cli_progress.py`: the count line is
the same bytes at a terminal and redirected, driven against two stores
because an import that finds its own writes has no count to print.
`test_config_cli.py` and `test_config_cli_rename.py` re-pinned to the
one line each surface now prints.

Documents. `docs/architecture/cli-guide.md`'s "the sentence states and
the client advises" is "the sentence states and the client speaks"
(#386, #426), with the seventh notice in the example beside it.
`vinga-server/README.md`'s write transcript and the two paragraphs
describing the two-voice shape. `CHANGELOG.md` gained two `Changed`
entries. `vinga-server/tests/unit/command-spellings.txt` regenerated
through its own module. `docs/reference/cli.md` and
`docs/reference/api-openapi.json` are byte-identical, as the plan
expects: no help row moved and no response model did.

### Deviations from the plan

Four, none changing what the milestone delivers.

**The new notice is chosen through a parameter rather than a second
decision site.** The plan says `_applied_notice` gains a default-agent
branch choosing the new notice "exactly where `write_default_agent`
does", and the literal reading is two copies of the snapshot-and-unloaded
decision. `_binding_notice` takes the unserved sentence as an argument
instead, so the two live rows cannot come to disagree about when a write
lands while disagreeing about what it wrote, which is the failure that
produced #424 from the other direction.

**`tests/support/notices.py` deepened beyond gaining the instance.** The
plan gives it one line of work. But its printed-output arm reads the
server's sentences, and after this milestone a write whose set the
client knows prints no server sentence at all, so eleven assertions
across `test_config_cli.py` and `test_config_cli_rename.py` would have
had to stop asking which boundary a command announced and start
comparing strings, which is the coupling that module exists to prevent.
It reads `cli.SPOKEN` as well, derived from the table rather than
restated. The import's count line is deliberately not in it: one clause
covers both known sets, so a reading of it could not say which set an
answer carried, and the import suites pin that line as bytes instead.

**Two import assertions moved from `boundaries()` to bytes.** For the
same reason and in the other direction: `test_config_cli.py`'s
whole-document import and `test_config_cli_rendering.py`'s acceptance
case asserted the boundary set of what was printed, and what is printed
now is this client's own count line. They pin the line.

**The both-ways case lives in `test_config_cli_progress.py`.** The plan
places it by shape (#297's) rather than by file. That suite owns the
terminal-versus-redirected machinery and the licence the progress line
is drawn under, which is what the case is about. It needs two stores
rather than two runs against one, because an import against the store
its own first run wrote answers `unchanged` and has no count to print.

### The review round

Backend codex, against PR #428. One P1, accepted: the door this
milestone added covered `notice` and not `wrote`, so an acknowledgement
carrying an escape sequence or a lone surrogate in the line saying what
was written could still steer stdout or raise `UnicodeEncodeError` out
of `print`, which carries the whole line past the boundary that turns a
failure into a sentence. The gap predates the milestone on that field;
what the milestone did was close half of it and say so.

`wrote` now leaves through `printable` at the DEFAULT bound, which is
the deliberate difference from the reviewer's suggestion of the
unbounded rule. `_entry_name` is the same value's door one level up and
uses the same bound: what that line names is a kind and an identity an
operator chose, quoted inside a sentence of this client's own, and a
bound is what protects a value like that. The unbounded rule is for a
boundary sentence, whose tail is the state it exists to state, and its
comment in `printing.py` says as much. No merged acknowledgement is
near the bound: the longest in the committed transcript is 59
characters against 120, and no `wrote` byte moved, which the respelling
differential confirms by passing unchanged.

The cases the finding asks for join the notice-side ones in
`test_config_cli_rendering.py`, driven through `Act.read()`: an escape
sequence and a lone surrogate in `wrote`, each arriving neutralized on
stdout, and the encoder case written to a real encoding with a pasted
credential behind the surrogate, asserting nothing reaches an exception
chain. The credential itself does reach stdout, deliberately and for
the reason the import cases record: what `wrote` names is a row as the
store holds it.

### Verification

Run from `vinga-server/` against a development Postgres.

- `uv run ruff check .`: passed.
- `uv run pytest tests/unit -q`: 5930 passed, 19 skipped, and 5933
  passed with the review round's three cases.
- `uv run pytest tests/integration -q`: 245 passed. Not re-run for the
  review round, which touches one rendering and its own suite and
  nothing that lane covers.
- `uv run python -m tests.unit.test_command_spellings`: regenerated
  after the last documentation edit; the manifest's diff is line
  numbers only, no spelling gained or lost.
- `python scripts/check_doc_links.py .`: 211 files, 0 failures.
- `docs/reference/cli.md` and `docs/reference/api-openapi.json`:
  unmodified, as the plan expects.

## M2: a diff prints its changes, grouped by the boundary they wait at

### What was done

`config/cli.py`. `DIFF_INTRO` is gone and `HEADS` stands in its place,
beside `INSTALLS` and reading it: one head per boundary, in this
client's words, so the API's tokens are no longer printed by this
command at all. `_diff_listing` groups rather than walks. It asks each
kind what it has to say, tags every fact with the boundary that fact is
waiting at, and prints one block per boundary present in the order
`Applies` declares them, with one line per kind under the head and the
kind's facts joined on it. `_diff_block` is `_diff_facts`, which returns
facts rather than lines: a name list with names in it, a flag that is
true, and the facts of the parts under a kind with the part's name in
front of them. The three shape readers (`named_lists`, `flags`,
`nested`) are untouched, so the contract-driven property is the one it
was. Two fixed sentences join them: `SERVING_THE_STORE` for a comparison
that found nothing, and `READ_AS_ASKED` after every comparison, saying
why the two live kinds are never in a group.

The pins. `test_config_cli_rendering.py`: the whole answer for Getting
Started's state pinned byte for byte, the head printed once over a
one-change diff and over a three-kind one, the nothing-pending sentence,
the live-kinds sentence at the end of both and naming every `LiveKind`
section, `HEADS` equal to `DiffApplies`, only-what-has-something-to-say,
the agents' clocks as labelled facts of the agents line, two renders of
one answer as one string, and a name carrying an escape sequence
arriving neutralized through the act. The refuses-whole cases for an
unknown `applies` value are unchanged, which is what the plan's review
round settled: a diff's `applies` is a scalar and `Act.read()` refuses
the whole answer before any rendering runs.

Documents. `CHANGELOG.md` gained one `Changed` entry. The plan's M2
bullet still listed "the unknown-token group-head case", which its own
review round withdrew in finding 2; the tick corrects it to what the
resolution settled, so the checklist and the resolution say one thing.
`vinga-server/tests/unit/command-spellings.txt` regenerated through its
own module. `docs/reference/cli.md` and `docs/reference/api-openapi.json`
are byte-identical: no help row moved and no response model did.

### Deviations from the plan

Five, none changing what the milestone delivers.

**`check-in` has a head, and it heads no group this server can send.**
The plan says the table is total over `DiffApplies` and pinned so, and
`check-in` is a `DiffApplies` member, so it has a line. It is unreachable
today for the reason the plan gives: the two kinds carrying that token
are `LiveKind`s, which name nothing, so they contribute no facts and
therefore no group, and the fixed sentence answers them instead. The
line exists so that a kind that ever carries `check-in` and does name
something is headed rather than met with a `KeyError`.

**The totality pin is an equality.** The plan asks that every
`DiffApplies` member have a head. The pin asserts the keys are exactly
that set, which adds the other direction: a head for a token no field
can carry is a line nobody would ever read, and keying the assertion off
the alias is what reports it.

**A kind's facts join one line, sub-sections included.** The plan's
example is `agents  prompt changed: kids`, which reads either as a line
of its own or as one fact of the agents line. It is the second: an
operator asks what moved about the agents and reads one line about the
agents, so `agents  changed: sam; prompt changed: sam; filler changed:
kids` is one line. Each fact is still tagged with its own part's
boundary rather than with the kind's, so a part that ever waits
somewhere else lands under its own head instead of under a wrong one.

**The two columns are not `_table`.** The plan leaves the choice open.
The left column is padded to the widest kind name printed anywhere in
the answer, with a two-space gutter, so the columns line up across
groups rather than per group, and a row exists only when it has facts,
which is what keeps trailing whitespace impossible.

**Two integration transcripts moved with the unit pins.** The plan's
test section names unit suites only, but `tests/integration/
test_cli_live.py` and `tests/integration/test_cli_wheel.py` each
asserted the old per-kind label. The first now pins the nothing-pending
and live-kinds sentences, which is what an applied deployment answers;
the second pins the live-kinds sentence, which prints whatever the
state, since that lane's claim is about a bare install rendering the
answer at all.

And one addition rather than a deviation: the diff had no
steer-a-terminal case, because its rendering used to be read as names
and closed tokens. The grouping moved every line of it, so the case the
plan says to keep for the other surfaces is added here, driven through
`Act.read()`: a name carrying an escape sequence arrives neutralized and
not dropped.

### What building it turned up

The nothing-pending sentence was first called `NOTHING_PENDING`, which
is the name of the sentence `device pending list` prints when no board
is waiting to be claimed (`cli.py:458`). Python rebinds a module
constant silently and `ruff` does not report it, so the module imported
and linted clean while two commands answered one sentence: the device
listing printed "nothing is pending: this server is serving what the
store holds". Two suites caught it, which is what those pins are for.
The comparison's sentence is `SERVING_THE_STORE` now. Worth recording
because the file has 40-odd fixed sentences and nothing in the toolchain
holds their names apart.

### Verification

Run from `vinga-server/` against a development Postgres.

- `uv run ruff check .`: passed.
- `uv run pytest tests/unit -q`: 5936 passed, 19 skipped (10m24s).
- `uv run pytest tests/integration -q`: 245 passed (5m56s).
- `uv run python -m tests.unit.test_command_spellings`: regenerated
  after the last source edit; the manifest's diff is line numbers only,
  no spelling gained or lost. Regenerated a second time after the
  documentation edits, which changed nothing: this section contributes
  no command spelling the census counts.
- `python scripts/check_doc_links.py .`: 211 files, 0 failures.
- `docs/reference/cli.md` and `docs/reference/api-openapi.json`:
  byte-identical to the branch base, as the plan expects. No help row
  moved and no response model did.

One honesty note about the order. The two lanes above were run against
the tree as it stands except for this section, which was written after
them; the lanes do not read it, and the census was regenerated and its
own suite re-run afterwards.

### The review round

Backend codex, against PR #429. Two P2s, both accepted.

**A non-empty change list could render as "nothing is pending".**
`_diff_facts` decided whether a kind had something to say from the
string `_names` returned. `EntityDiff` declares its names as strings and
says nothing about how long one may be, so `added: [""]` is an answer
`Act.read()` accepts, and `printable` strips before it bounds, so such a
name rendered to nothing. The fact disappeared; a kind whose only
pending change was that name fell out of the answer; and with no group
left the command printed `SERVING_THE_STORE`, which tells an operator
their writes are installed. Of everything this rendering can get wrong
that is the worst, because it is the one answer that is acted on by
doing nothing.

Presence now comes from the validated tuple. The rendering half went
into `_names` rather than into the diff, because it is a door and not a
branch: a name that comes back from `printable` with nothing in it
prints as `UNNAMEABLE`, one question mark, which is what `printable`
already answers for every other character it cannot write. So a list of
N names is N things however they were spelled, on the apply's outcome
lists and the grant and tool listings as well, each of which had the
same hole one row deep. The sub-section facts needed nothing of their
own: they are `_diff_facts`'s answer one level down. Flags were never at
risk, being booleans. The regression is parametrized over an empty name
and a whitespace-only one, driven through `Act.read()`, and asserts both
that the nothing-pending sentence is absent and that the kind is there
with the placeholder, plus that two such names print as two things.

**The multi-boundary grouping had no behavioral pin.** Every populated
fixture said `reload` and the head table's pin compared keys only, so a
rendering that grouped `reload` alone, or that ordered its groups by
something of its own, would have passed the whole milestone. One answer
now carries a kind at `restart` and a kind at `reload`, built through
`Act.read()` because what makes it legal is the alias the fields are
declared with. It asserts each head appears exactly once, that each
kind's line sits under the head of the boundary its own kind named and
nothing else sits under either, and that the groups follow the order
`Applies` declares its members in, read off the declaration rather than
written out.

Verification after the round, from `vinga-server/`: `uv run ruff check
.` passed; `uv run pytest tests/unit -q` gave 5942 passed, 19 skipped
in 628s; the census manifest was regenerated after each commit, and
moved for the first (source line positions) and not the second. The
integration lane was not re-run, and here is the reasoning rather than
the assertion: its two diff assertions read `cli.SERVING_THE_STORE` and
`cli.READ_AS_ASKED` by name against a deployment whose comparison is
empty, and an empty comparison has no list for the presence rule to
read and no group for the ordering pin to order. Nothing on this path
moves either sentence or the answer they are asserted against.
## M3: an apply says what happened and that it worked

### What was done

`config/cli.py`. `_apply_listing` keeps its derived skeleton and gains
the content rule: a section with something to say is a block, a section
answered null keeps its `NOT_APPLIED` line, and within a block a list
with names in it and a flag that is true. A true flag prints as its
label alone, the way the comparison prints one, since `yes` after a
label that already says what happened is a word about nothing.
`APPLY_LABELS` sits beside `APPLY_SECTIONS` and maps (section, field) to
the operator's phrase for that outcome, keyed by the pair because one
field name means two things in two sections: a provider entry that was
`reused` is an engine nothing rebuilt, and a filler that was `reused` is
audio nothing sent to a voice. Each label is written from its own
field's description in `responses.py`. `NOTHING_DIFFERED` is what an
apply that moved nothing says, for the reason the comparison has a
sentence of its own. The status block is asked for only where there are
entries, and the blank line that separates it goes with it, so an answer
without one ends on the line before rather than on whitespace.

`_applied` is the apply's own render callable, wired as `APPLY.render`
in place of `_printed(_apply_listing)`: the listing to stdout, a flush,
then `INSTALLED` on stderr. Shaped like `_imported` and for the reason
that renderer records, which the plan's review round made a finding of:
`_printed` prints one string and knows nothing of a second stream, and
stderr is unbuffered while stdout is not, so without the flush the
success can land above the listing it is about.

The pins. `test_config_cli_rendering.py`: the whole listing pinned byte
for byte over an answer with every list filled and the flag true, which
is where each label is held to what an operator reads; the label table
equal to what the models declare, with the every-field-is-rendered
assertion kept beside it; only-what-has-something-to-say as an equality
over a one-outcome answer, which also pins that no trailing blank line
survives an answer with no MCP entries; the nothing-differed sentence;
no `NOTHING_CONFIGURED` in an apply with no entries while `mcp-server
status` still answers it, asserted in one case so the two halves cannot
drift; two renders as the same bytes per stream; the ordering over one
shared buffer with a buffered stdout and an unbuffered stderr on it; the
success line present on the happy path and absent from a refusal; and a
started entry whose name carries an escape sequence arriving
neutralized. `test_config_cli_progress.py`: the success line is the same
bytes at a terminal and redirected. `test_config_reload.py`: the two
filler outcomes it asserts through the rendering, in their labels.
`tests/integration/test_cli_live.py`: the apply's stderr is the success
sentence rather than empty.

Documents. `vinga-server/README.md`'s apply transcript re-captured, with
the paragraph under it saying what the new shape is and what has not
moved, and the `instructions` paragraph naming both words for the one
outcome it sends a reader to look for. `CHANGELOG.md` gained one
`Changed` entry. `vinga-server/tests/unit/command-spellings.txt`
regenerated through its own module. `docs/reference/cli.md` and
`docs/reference/api-openapi.json` are byte-identical: no help row moved
and no response model did.

### Deviations from the plan

Three, none changing what the milestone delivers.

**Two labels are the field's own word.** The plan says the field names
get operator labels, and `prompts.changed`, `agents.added` and
`agents.removed` are already the operator's words for what happened to
their own document. They are rows of the table all the same, because
what the table buys is totality rather than novelty: the pin is keyed
off the models, so a field added without a label fails whether or not
its label would have been a new word.

**The root README needed no reconciliation.** Step 3 runs `vinga apply`
and says what it is for, what it costs the first time, and that
importing is additive; nothing around it describes what the command
prints. Recorded rather than silently skipped, since the plan's
documentation footprint names it conditionally.

**The status block's grant line moved in the transcript with the rest.**
`_granted` sorts by agent name and the committed transcript listed
`kids, house`, which no run produces. It is not a rendering this
milestone touched; re-capturing the block around it was the moment the
line stopped being wrong, and leaving it would have committed a
transcript known not to be one.

### What building it turned up

Nothing new about the constant names: the M2 trap was checked for
before either sentence was named, and `INSTALLED`, `NOTHING_DIFFERED`
and `_applied` were unused in the module. The near miss was elsewhere:
`tests/integration/test_cli_live.py` already has a module constant named
`INSTALLED`, which is the document that lane imports, so the assertion
there spells `cli.INSTALLED` and the two never meet.

`ruff format` is not a gate in this repository and `cli.py` does not
satisfy it today, at some forty sites that predate this milestone. Worth
knowing before reading a `--check` run as a regression: `uv run ruff
check .` is the lint lane, and it passes.

### The review round

Backend codex, against PR #430. One P2, accepted: the case that pins the
labels says every list of the answer is populated, and two of the MCP
section's four were not. `restarted` and `unchanged` printed nothing, so
`connection remade` and `connection kept` were held by the completeness
pin's table keys alone, which is a claim about the table rather than
about the output: either could have been skipped by the rendering, or
carried any wording at all, with the suite still green.

Both are populated now, with names distinct from the two beside them, so
each line is attributable to the field it came from. The docstring says
what the fixture is for rather than repeating the claim that was untrue,
and it records the one repetition that stays: the two kinds of filler
cross deliberately, since one agent kept under the filled pauses and
spoken again under the failure phrases is the property that says they
are staled apart. That repetition costs the pin nothing, because the
labels are literal text printed in the models' declaration order, so a
swapped pair moves the bytes wherever the names fall. The sweep the
finding asks for found no third case: every other list of every section,
and the one flag, were populated already.

### Verification

Run from `vinga-server/` against a development Postgres.

- `uv run ruff check .`: passed.
- `uv run pytest tests/unit -q`: 5945 passed, 19 skipped (10m27s), with
  the census manifest failing on the run before it was regenerated and
  its own suite passing after (48 passed). For the review round, 5949
  passed, 19 skipped (10m28s), the four added by a rebase onto main.
- `uv run pytest tests/integration -q`: 245 passed (5m56s). Not re-run
  for the review round, which moved one unit fixture and no committed
  transcript: that lane pins the apply's stderr and the prompts line,
  and neither moved.
- `uv run python -m tests.unit.test_command_spellings`: regenerated
  after the last documentation edit. Compared ignoring line numbers, the
  manifest gains exactly two spellings and loses none, both `historical`
  and both this milestone's own documents: the CHANGELOG entry and this
  section each name `vinga apply`.
- `python scripts/check_doc_links.py .`: 211 files, 0 failures.
- `docs/reference/cli.md` and `docs/reference/api-openapi.json`:
  byte-identical to the branch base. No help row moved and no response
  model did.

The lanes above were run against the tree as it stands except for this
section and the manifest, which followed them; the lanes do not read
this section, and the census was regenerated and its own suite re-run
afterwards.

## M4: `info` answers at a glance, with the URL protection kept

### What was done

`config/cli.py`, three renderings and the constants beside them.

`_identity_block` prints the build in one line, `server: <version>
(<revision>)`, both values through `printable` at the bounded length
exactly as the two lines were. `BUILD` is the label, beside `BANNER` and
`CONTACTED` where the other two labels of this answer live. The block's
leading blank line goes with the second line: the build fact belongs
under the address that answered it, and the blank that separated them
was separating one fact from itself.

`ONBOARDING_URL_LABEL` leads with `onboarding URL`, which is this
codebase's own name for the value, and its parenthetical says both what
the value is for and what a board calls the field it goes in. Everything
that made the old line what it was is unchanged: the provenance still
rides the label, the URL still lands on a line with nothing in front of
it, and it still goes through `printable(..., UNBOUNDED)`. The
docstring's wrap-protection paragraph gained the sentence that says
which half was compacted and why the other could not be: a label may
wrap and lose nothing, and the line under it may not.

`_configured_counts` answers in one line. Kinds with a zero count are
absent; a count of one uses `kind.name`, the descriptor's own command
noun, and any other count uses that noun with an `s`, which is derived
from the registry rather than listed beside it. The singleton, which the
old rendering skipped for having no count to give, says `agent_defaults
set` when anything is set in it. The two settings that are not kinds
always say what is true, `no devices` or `N devices bound` and `default
agent <name>` or `no default agent`, because an unbound board reaching
nothing is the fact an operator is looking for rather than an empty
field to hide. A store nothing has been written to says `configured:
nothing yet` (`NOTHING_YET`), since empty output would read as a command
that failed to answer. Order is the registry's over the counted kinds,
then the singletons, then devices, then the default agent: two passes
over `entities.ENTITIES` rather than one, so the order is the one stated
rather than the one a single pass happens to produce.

`_default_agent` is gone. It existed because two renderings said the
same thing, and the tally stopped saying it that way; what is left is
one `printable` call inlined where `_summary` writes the row, with the
comment that says why the tree's word and `info`'s differ.

The pins. `test_config_cli_info.py`: the end-to-end case reads the build
line by position and the whole tally as one string, which makes it a
zero-kinds-absent pin for free, since the deployment it composes writes
no `agent_defaults`; the URL case reads the label through
`cli.ONBOARDING_URL_LABEL` and asserts what it leads with, so the pin is
about the shape rather than a second copy of the string; the
default-agent, refusal and onboarding-off cases follow the new bytes.
Five new cases: a kind nothing was written of is absent, asserted over
the whole line; singular and plural over a one-word kind and a
hyphenated one, in both directions; `nothing yet` for the empty store;
`agent_defaults set` present only when the section holds something; and
two runs against one state compared as bytes on both streams, with
stderr empty, which is the determinism case this surface did not have.
`tests/integration/test_cli_live.py` and `test_cli_wheel.py` re-pinned
to the same lines over the wire and from an installed binary.

Documents. Both root `README.md` transcripts, and the sentence after
step 2's that pointed at "everything at zero". `CHANGELOG.md` gained two
`Changed` entries. `vinga-server/tests/unit/command-spellings.txt`
regenerated.

### Deviations from the plan

Three, none changing what the milestone delivers.

**The parenthetical names OTA, which the plan's target block does not.**
The plan says both things: its rendered target block reads `onboarding
URL (the address a device's captive portal asks for)`, while the issues'
decisions bullet above it says the device's word, `OTA`, rides as a
parenthetical "for whoever is typing it into a field labelled that". The
decision is the half that states a requirement and the block is its
sketch, so the label carries both: `onboarding URL (the address a
device's captive portal asks for, labelled OTA there)`. Recorded because
a reviewer diffing the block against the output will find the extra
clause.

**`_default_agent` was deleted rather than left alone.** The plan's
module layout names `_identity_block` and `_configured_counts` as the
two that deepen. The helper was not a third: it was one line whose whole
justification was that two renderings said the same thing, and this
milestone is the change that stops them from doing so. Inlined into its
one remaining caller, which is what the deletion test asks for.

**Two integration lanes were re-pinned as well.** The plan's test list
names the two unit suites. `tests/integration/test_cli_live.py` asserts
the build lines and the URL label over the wire and
`tests/integration/test_cli_wheel.py` asserts them from an installed
binary, and both were assertions on the exact bytes this milestone
changes.

### What building it turned up

The constant-rebinding trap was checked for before either name was
written: `BUILD` and `NOTHING_YET` were unused in `cli.py`. A first
draft also named `NO_DEVICES`, `DEVICES_BOUND`, `DEFAULT_AGENT` and
`NO_DEFAULT_AGENT`; they were dropped again, because four constants
holding fragments of one line put the line back together in the reader's
head for no gain. `NOTHING_YET` stays, since it is a whole answer.

The tally's own test helper found the zero-kinds pin for free: the
end-to-end case's deployment writes providers, an MCP server, a
fragment, two agents, a binding and a default agent, and no
`agent_defaults`, so asserting the whole line asserts that the singleton
is absent as well.

### Verification

Run from `vinga-server/` against a development Postgres.

- `uv run ruff check .`: passed.
- `uv run pytest tests/unit -q`: 5956 passed, 19 skipped (10m32s), with
  the census manifest failing on that run and its own suite passing
  after it was regenerated.
- `uv run pytest tests/integration -q`: 245 passed (5m57s). The first
  run of it failed two `info` assertions this milestone had rewritten
  against the wrong deployment: the lane in `test_cli_live.py` does have
  MCP servers, and the wheel lane has no default agent, so it prints the
  `no default agent` clause. Both pins now say what those lanes really
  answer, and the whole-line claim they were reaching for is made in the
  unit suite, where the document is the case's own.
- `uv run python -m tests.unit.test_command_spellings`: regenerated
  after the last documentation edit. Compared ignoring line numbers, the
  manifest gains exactly one spelling and loses none: `vinga info`,
  `historical`, in this milestone's own CHANGELOG entry.
- `python scripts/check_doc_links.py .`: 211 files, 0 failures.
- `docs/reference/cli.md` and `docs/reference/api-openapi.json`:
  byte-identical to the branch base. The `info` help row does not
  change, and no response model does.

### The review round

Backend codex, against PR #431. One P2, accepted.

**A whitespace-only default agent rendered as a blank fact.** The tally
asked the raw value whether there was a default agent and then printed
it through `printable`, which strips before it bounds, so a name the
answer carried as `"   "` produced `default agent ` with nothing after
it: neither of the two forms this line promises, and the same hole M2
closed on the diff's name lists. The rendering now falls back to
`UNNAMEABLE` for the reason recorded there. Which of the two forms is
printed is asked of the value rather than of what it prints as, since a
null is a deployment with no default agent and a name that renders to
nothing is a deployment that has one; an empty string is a set name by
that reading too, so `nothing yet` is now decided on the null as well.
Four cases through `Act.read()` pin the distinction, and a fifth pins
that a store whose only setting is an unnameable agent is not an empty
one.

The rest of the milestone's own `printable` calls were swept for the
same shape. The tally's other facts are arithmetic, so there is no name
among them: the kind counts and the device count are integers and the
singleton is a count of keys. Two sites were looked at and left. The
build line's version and revision are not names in a listing, and they
carried the same bare `printable` when they were two lines. The tree's
`default_agent` row keeps `(none)`, which is the answer every other row
of that listing gives to the same question; the tree prints no name
through `UNNAMEABLE` anywhere, and singling out one row would make
`vinga list` disagree with itself.

Verified the same way, from `vinga-server/`: `uv run ruff check .`
passed, `uv run pytest tests/unit -q` gave 5962 passed and 19 skipped
(10m33s), and `python scripts/check_doc_links.py .` gave 211 files and 0
failures. The integration lane was not re-run: neither of the two
transcripts it pins reaches this arm, since both of those deployments
answer a null default agent, and the manifest moved only by the line
numbers this change shifted plus the `vinga list` this paragraph names.
## M5: the practices, written where reviewers look

### What was done

`docs/architecture/cli-guide.md`, in two commits: the practices, then
the questions that reach them.

**The five practices are five new sections**, in the order the checklist
asks them, placed after "A write says what it did and when it takes
effect" because that is where the page stops talking about the grammar
and starts talking about the answer. Each is written in the page's house
shape: the statement, the reasoning, an example naming the merged
constants and renderers M1 to M4 produced, and one counterexample
labelled **historical** quoting this repository's own pre-#426 output
with the issue it came from.

- **An answer prints what has something to say** (#426 for the
  counterexample). Example: `_diff_listing` and `SERVING_THE_STORE`,
  `_apply_listing` with `NOTHING_DIFFERED` and the `NOT_APPLIED` line a
  null section keeps, `_configured_counts` with `NOTHING_YET`. It also
  records where the rule stops, which is the two absences `info` prints
  anyway (`no devices`, `no default agent`) and the `(none)` the memory
  and status listings keep. Counterexample: the six-row `configured:`
  block `info` printed whatever was in it.
- **An answer speaks the verb that was typed** (#424). Example: the
  import's count line and `APPLY_LABELS` with its completeness pin, plus
  the statement that no response model moved. The boundary half is *not*
  restated here: the section points at "the sentence states and the
  client speaks" inside the write practice, which M1 amended, per the
  locality rule. Counterexample: the pre-M1 import stderr, both
  sentences and both remedies, with `The binding` said over a
  `default_agent` write.
- **An action that succeeds says so** (#426). Example: `_applied`, the
  flush, and `INSTALLED` on stderr, with the stream split cross-linked.
  The elapsed-time decision is a paragraph of its own: the duration is
  `narrated`'s and never a byte of retained output, cross-linked to the
  determinism practice whose licence it runs under. Counterexample: the
  twenty-line apply dump, quoted from the transcript M3 replaced, which
  ends without saying anything worked.
- **A boundary is stated once per run, over the group** (#425). Example:
  `HEADS`, `INSTALLS` and the import's one `NOT_SERVING_YET` clause, and
  `READ_AS_ASKED` named as the deliberate exception that is about scope
  rather than about a boundary. Counterexample: the whole pre-M2 diff
  for Getting Started's state.
- **A command volunteers no advice about features not in use** (#426).
  Example: `_status_block` asked only where there are entries, with
  `NOTHING_CONFIGURED` still the whole answer to `mcp-server status`,
  and the deleted step-2 paragraph as the same instinct in a document.
  Counterexample: that MCP paragraph printed under every apply.

**The reviewer's checklist gained five questions**, 6 to 10, between the
question about the streams and the one about refusals, so the answer's
shape is asked about where a reviewer has just finished asking what the
command prints and when its write lands. The questions behind them keep
their wording and their links and move down; the list runs to sixteen,
and both counts in "On this page" say so.

`docs/architecture/README.md` counted the checklist for a reader
deciding whether to open the page ("as eleven questions at the top") and
says sixteen now: the count has one home and a sentence elsewhere that
repeats it is corrected in the change that moves it.

`CHANGELOG.md` gained one `Changed` entry. No file under
`vinga-server/src` changed, which is what a documentation milestone
means here; the census manifest was regenerated, and `docs/reference/
cli.md` and `docs/reference/api-openapi.json` are byte-identical.

### The Getting Started re-read, and how it was done

**Against rendered output, not a live deployment.** The plan offers
either; a compose deployment was deliberately not run in this milestone,
so what the walkthrough's four commands print was reproduced from the
merged renderers and compared against the committed transcripts and the
prose around them. Recorded plainly because it is the weaker of the two
lanes: what it cannot catch is a server that answers something other
than what these fixtures carry, and the integration lane, which does
reach a live server, is what covers that and passed in M1 to M4.

What was compared, and what it found:

- **Step 2, `vinga info` on an empty store.** `_configured_counts`
  renders `configured: nothing yet`, which is `README.md:175` exactly.
  The sentence under it, which says the tally is the whole of what there
  is to say about a deployment nothing has been written to, is true of
  that line. No change needed.
- **Step 4, `vinga info` for the onboarding URL.** `_identity_block`
  with a `public_url` provenance renders `onboarding URL (the address a
  device's captive portal asks for, labelled OTA there), from
  server.public_url:` and the bare URL under it, which is
  `README.md:272` and the line after it. No change needed.
- **Step 3, `vinga import`.** The document names seven entries (four
  providers, the shared defaults, one agent, the default agent);
  `_imported_entries` prints the seven `created` lines on stdout and one
  line on stderr reading: imported 7 entries, not serving yet: run
  `vinga apply`. That is the sentence the plan's target block states. The
  README carries no transcript for this command, and the prose beside it
  ("That saved the document as your configuration and left the running
  server alone") is what the stderr line now says in the operator's
  words rather than something it contradicts. No change needed.
- **Step 3, `vinga apply`.** The README carries no transcript and no
  claim about what it prints, only the warning that the first one is
  slow, which is still true and is what the progress line is for. The
  maintained transcript is `vinga-server/README.md`'s, re-captured in M3
  with its prose.
- **The inventory grep re-run** (`applies at reload`, `configured:`,
  `not yet serving`, `(none)`, `defaults_changed`, over `README.md`,
  `vinga-server/README.md` and `docs/`, discounting `docs/plans/`): the
  only hits left are `README.md:175`'s new line, the three `(none)`s in
  the `mcp-server status` transcript, which the plan keeps deliberately,
  a sentence in `vinga-server/README.md` describing what the *API*
  answers, which is unchanged, and one unrelated line in a 2026-08-19
  feature note. Nothing stale was found, so this milestone changed no
  transcript and no walkthrough prose.

### Where #426's "done when" stands

- **The practices are in `cli-guide.md`, with merged examples and
  labelled counterexamples.** Done in this milestone, and the checklist
  reaches all five.
- **`import`, `diff`, `apply` and `info` conform to them.** Done in M1
  to M4 and pinned there: the import's one line, the diff's groups, the
  apply's labelled outcomes, the tally. Every one of the four also
  passed the content rule, which is what the "prints what has something
  to say" pins assert per surface.
- **An action that succeeds says so.** Done for the apply (`INSTALLED`,
  M3) and for the import, whose count line is the same shape on the same
  stream (M1). The verb that has no such line is a write acknowledgement,
  which says what it wrote on stdout and is not silent about success by
  the same argument.
- **Determinism.** Each of the four surfaces gained an explicit
  byte-equality case, streams captured separately where both are
  written, and the two new fixed stderr sentences gained the
  terminal-versus-redirected comparison in #297's shape. Recorded per
  milestone above.
- **#424 and #425 are closed by PRs #428 and #429**, as the plan's
  checklist records. Whether GitHub shows them closed was not queried
  from this milestone, which runs no GitHub commands.

### Deviations from the plan

Two, neither changing what the milestone delivers.

**The re-read was done against rendered output rather than a live
deployment.** The plan licenses either, "if one is available, otherwise
against the suite's rendered output"; no compose deployment was raised
here, so the second lane is what ran, and the section above says what
that lane can and cannot see.

**The historical diff was reconstructed rather than pasted.** #425's
specimen is quoted in the issue, which this milestone does not read; the
block in the guide is the pre-M2 renderer (`_diff_block` and
`DIFF_INTRO`, at `f7311714~1`) run over `DIFF_PENDING`, the fixture the
merged pin uses for the state Getting Started's step 2 leaves behind. So
it is this repository's own output for that state rather than a
paraphrase, which is what **historical** claims, and the sentence under
it counts what the block itself shows rather than repeating the issue's
count.

### What building it turned up

The census classifies a `vinga <verb>` spelling in this page as
`respell` and holds it to naming a registered command, whatever it is
quoting: the exception list is per-invocation, not per-block. Every
spelling in the new counterexamples happens to be a live command
(`vinga apply`, `vinga diff`, `vinga mcp-server set`), so nothing had to
be excepted, but a historical block quoting a retired spelling would
have failed the guard rather than the review, which is worth knowing
before quoting one.

### Verification

Run from the repository root and from `vinga-server/`.

- `python scripts/check_doc_links.py .`: 211 files, 0 failures, re-run
  after every commit, since the checklist links the anchors the
  practices create.
- `uv run python -m tests.unit.test_command_spellings`: regenerated
  after each documentation edit that moved a line. Compared ignoring
  line numbers, the manifest gains exactly twenty spellings and loses
  none: twelve in the guide, classified `respell` and every one of them
  a command the tree has (`vinga apply` seven times, `vinga diff`
  twice, `vinga info`, `vinga mcp-server set`, `vinga mcp-server
  status`), and eight `historical` ones in this section.
- `uv run pytest tests/unit/test_command_spellings.py -q`: 48 passed.
- `uv run ruff check .`: passed. Nothing under `vinga-server/src` was
  touched at all, and the only file under `tests/` that moved is the
  generated manifest.
- `uv run pytest tests/unit -q`: 5957 passed, 19 skipped (10m37s), run
  at the end to show nothing else moved. An earlier run was killed
  rather than reported: it hit the census drift check while a
  documentation edit was in flight beside it, which is what that check
  is for.
- `docs/reference/cli.md` and `docs/reference/api-openapi.json`:
  byte-identical to the branch base. No help row moved and no response
  model did.

The order, since it decides what the lane saw. The unit lane ran against
the tree as it stands apart from this section and the two documentation
edits recorded above it, all three of which are Markdown; the only unit
test that reads any of them is the census drift check, which was re-run
after the manifest was regenerated and passes.
