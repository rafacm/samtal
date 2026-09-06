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

### Verification

Run from `vinga-server/` against a development Postgres.

- `uv run ruff check .`: passed.
- `uv run pytest tests/unit -q`: 5930 passed, 19 skipped.
- `uv run pytest tests/integration -q`: 245 passed.
- `uv run python -m tests.unit.test_command_spellings`: regenerated
  after the last documentation edit; the manifest's diff is line
  numbers only, no spelling gained or lost.
- `python scripts/check_doc_links.py .`: 211 files, 0 failures.
- `docs/reference/cli.md` and `docs/reference/api-openapi.json`:
  unmodified, as the plan expects.
