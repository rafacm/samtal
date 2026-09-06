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

### Verification

Run from `vinga-server/` against a development Postgres.

- `uv run ruff check .`: passed.
- `uv run pytest tests/unit -q`: 5945 passed, 19 skipped (10m27s), with
  the census manifest failing on the run before it was regenerated and
  its own suite passing after (48 passed).
- `uv run pytest tests/integration -q`: 245 passed (5m56s).
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
