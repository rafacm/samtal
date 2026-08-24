# Rebuild the config CLI on Typer: implementation

Companion to [`2026-08-23-typer-cli.md`](2026-08-23-typer-cli.md). One
section per milestone, recording what was actually written, the
deviations from the plan, what building it turned up, and the
verification results as they came out. A milestone with no deviations
says so explicitly.

## M1: the grammar moves to Typer whole

### What was done

Seven commits: the dependency, the rebuild, and five that pin what the
rebuild changed.

**The dependency** (`ecc86d2a`). `typer>=0.27.1` joins the runtime
dependencies, with `uv.lock` in the same commit because CI installs
`--frozen`. Runtime rather than development: the command group ships
with the server.

**The rebuild** (`5e1e9c06`). `config/cli.py`'s argument layer is a
Typer app. What went: `_Parser`, `_usage_problem`'s narrow shape,
`_fragment_parser`, and `_parser()`'s 291 lines of subparser
construction with their `set_defaults(run=..., act=...)` wiring. What
carried over unchanged: the acts table, every renderer, every refusal
sentence, the transport policy (`_call`, `_sent`, `_answer`,
`_permitted`, `_base_url`, `_token`, both timeouts), `_fragment`,
`_read_secret`, `_store`, and the four no-API commands' bodies. The
verb set, the positional shapes and the `-f` writes are what they
were, so all four artifact-pinned spellings (`config reload`,
`config add-device`, `config bind-device`, `config ota-url`) and every
other spelling stand.

Four structures are new, and they are the whole of the argument layer:

- `Invocation`, the seam. A frozen dataclass naming every field the
  grammar can hand an act, replacing `argparse.Namespace` as the type
  every act's `path`, `body` and `local` callable takes. The bodies
  did not change; only the annotation did, so an act reading a field
  nobody sets is now a name that is not there.
- `Globals` and its `merged`, which is the `default=argparse.SUPPRESS`
  dance restated. The root callback resolves the first answer onto the
  Click context, every position under it folds its own copies in, and
  a value wins only where it was given. `--local` accumulates rather
  than overrides, because it is presence-only.
- `Command`, one row per command: its words, what it does, how it
  declares its arguments, its help, its epilog, and `local_ok`.
  `COMMANDS` is thirty-four of them and `GROUPS` is the five group
  words with the help a leaf row cannot carry. The registration loop
  in `command()` is the only reader.
- The usage boundary, below.

`main`, `build_client` and the exit codes are unchanged.
`cli.main(argv)` still returns 0 or 1, still prints a `ConfigError`
sentence to stderr, and still leaves `--help` through `SystemExit(0)`.

**The break-glass gate** moved from `main` into `_invocation`, which
every command's declaration calls before it does anything. It reads
`local_ok` off the row, which is decision 5: membership of the
recovery subset is a fact of the command rather than an imperative
install on a subparser, so `test_the_two_lists_cover_the_whole_break_glass_subset`
(`e5eba024`) can hold the differential suite's two lists to it. Both
differential pins carry over with their meaning intact: the
acknowledgement equality and its wording sibling assert real output
and name no sentence.

**The usage boundary** is decision 2's. The app runs with Click's
standalone mode off, driven through `make_context`/`invoke` directly,
so nothing Click decides reaches a stream. A `ClickException` is
translated two ways and never by passing its text through: the
subclasses by CLASS (`NoSuchOption`, `MissingParameter`,
`BadOptionUsage`, `BadArgumentUsage`, `BadParameter`), and the base
`UsageError`, which is one class for three different mistakes, by
Click's own fixed words (`Got unexpected extra argument`,
`No such command`, `Missing command`), which are the part of those
sentences carrying no value. Anything else gets the deliberately vague
fallback. The secret-never-an-argument sentence stays on the
unrecognized-arguments shape. The refusal is built inside the handler
and raised after it, the way `_fragment` and `_understood` raise, so
no Click exception is left as a `__context__` for a chain walker to
find the argument list behind.

That closes the hole the plan's decision 2 named: `config doctor` used
to answer argparse's `invalid choice: 'doctor'`, echoing a word that a
URL or a credential follows on the command line.

**The planted-secret sweep** (`504430db`) is one case per shape the
boundary names, each with a credential where the mistake would put
one, asserting this grammar's sentence on stderr and the value on
neither stream. `BadParameter` has no case: no argument of this
grammar is a typed choice, so Click has nothing to refuse a value for,
and the boundary translates it anyway.

**`fragment_help` deepened** (`785970c0`) to render each field's type
and default beside its description, through the same `type_name` and
`default` the reference's table cells are computed with. Two tests
enumerate the command tree the entry point runs: every command's help
carries every parameter it declares with the description it was given
and one `[required]` per parameter that has to be there, and every
`set` help carries every field of the model its fragment is validated
against with that field's type and default.

**The position matrix** (`fe921df6`) is decision 2 as amended, stated
separately for the two positions. The root half reads the options off
the root's own declarations, so a fourth inherits the matrix; the leaf
half holds every command of the table to the options it should take,
with the four documented exclusions. The behavior half is
parameterized over the two options that carry a value (before alone,
after alone, and both with the nearer winning, read back through the
address the client was built with) and `--local` gets the two cases
presence-only admits.

**The tightening** (`818b571d`) passes the three globals positionally
into `_invocation`, which is what took a command declaration's body
from eleven lines to one.

### Deviations from the plan

Four, none of them to the grammar.

**1. Typer vendors Click, so the boundary imports from
`typer._click.exceptions`.** Typer 0.27 ships its own copy of Click
(`typer._click`) rather than importing the installed one, and it is
that copy's classes a usage error is raised as. `click.UsageError`
catches none of them, and translating by class is decision 2's
requirement, so the five subclasses plus `ClickException` and `Exit`
are imported from where they are, with a comment saying why and what
a Typer release that moves them would do (fail at import, loudly). The
alternatives were worse: matching on `type(exc).__name__` is a string
where a class belongs, and reaching the base through
`typer.BadParameter.__mro__` is the same private dependency spelled so
that a reader cannot see it.

**2. The UNSET sentinel is `None` and `False`.** The plan asks for
each command's copy of a global option to default to an UNSET
sentinel. It defaults to `None` for the two options that carry a value
and `False` for the flag, which are the not-given values and are
unambiguous: neither option can be typed as `None`, and `--local` has
no negative spelling. A sentinel object of this module's own would be
run through Click's string conversion on the way to `ctx.params` and
would read back as its repr in the help, which is the one place these
defaults are published. The SUPPRESS semantics are reproduced exactly;
`Globals.merged` is where they live, and the matrix proves it (with
the merge broken to always take the leaf value, 33 of the grammar and
local suites' tests fail).

**3. `cli.py` grew rather than shrank.** The plan's goal says the
argument layer replaces "~800 lines of argparse machinery" with an
expected saving of a few hundred lines. Neither half held for M1. The
machinery that went was 333 lines (`_parser()` 291, `_fragment_parser`
16, `_Parser` plus `_usage_problem` 26), and what replaced it is 889:
the file is 2,035 lines before and 2,678 after, `+643`. The reason is
structural rather than incidental: argparse builds a parameter by
calling `add_argument` in a loop, while Typer reads a signature, so
each of the grammar's argument shapes is a function with that shape
written out. There are fourteen of them for thirty-four commands, and
they are what the saving would have had to come out of. The rest of
the growth is the table's own help prose (one row per command, where
argparse had one `add_parser` call), the four new structures above,
and this repository's comment density. What the milestone did buy is
what its own design footprint claims and what M2 to M4 spend: adding a
command is a row, `local_ok` is readable, and the help is generated.
Reported rather than forced: no shape was compressed to hit a number.

**4. Two help-rendering behaviors are Click's, not argparse's.** The
usage line reads `Usage:` rather than `usage:`, and the command
listing shows a command's short help, which Click cuts at the first
sentence or the terminal width unless `short_help` is given. Every row
passes its help as both, so the listing shows the whole sentence, and
the two help-phrase assertions the grammar suite has always made pass
unchanged. Click also rewraps an epilog into paragraphs, which would
reflow the generated field listing into prose, so the commands are
registered with a `TyperCommand` subclass whose `format_epilog` writes
the lines as they were laid out. That is the same reason argparse's
`RawDescriptionHelpFormatter` was asked for before this.

### The changed usage sentences

Every assertion that moved, and all of them under one rule:
**usage-boundary wording only.** Nothing about what a command does,
what it prints on success, or what a refusal from the API or the
repository says changed anywhere.

| File | Assertion | Before | After |
| --- | --- | --- | --- |
| `test_config_cli_grammar.py` | `test_asking_for_help_is_not_a_failure` | `"usage: vinga-server config"` | `"Usage: vinga-server config"` |
| `test_doctor.py` | `test_the_config_group_no_longer_answers_this_command` | `"invalid choice" in err` | `"that is not a command" in err`, and `"doctor" not in err` |

That is the whole list. The other seven config suites and the docgen
suite kept every assertion they had; the two suites above kept theirs
apart from these two lines, and the docstrings that named argparse
were rewritten to name Click, which changed no assertion.

The second row is the strengthening decision 2 records, and it gained
an assertion rather than only changing one: the old refusal quoted the
word that was typed, and the mistyped command an operator makes at
this entry point is followed by whatever they were about to hand a
command that takes secrets.

### The sentences the boundary now says

Each with `; run with --help for the grammar` appended, which is the
sibling grammars' shape.

| Shape | Sentence |
| --- | --- |
| `NoSuchOption` | that is not an option of this command |
| `MissingParameter` | a required argument is missing |
| `BadOptionUsage` | an option was given without its value |
| `BadArgumentUsage` | an argument was given in a shape this command does not take |
| `BadParameter` | an argument was given a value this command does not take |
| `Got unexpected extra argument` | unrecognized extra arguments. A secret is never given as an argument: set-secret reads it from stdin, or from the variable named with --from-env |
| `No such command` | that is not a command |
| `Missing command` | a command is missing |
| anything else | the command line could not be parsed |

### The inventory

`wc -l`, before at `877f391b` and after at `818b571d`:

| File | Before | After |
| --- | --- | --- |
| `src/vinga_server/config/cli.py` | 2035 | 2678 |
| `src/vinga_server/config/docgen.py` | 473 | 484 |
| `tests/unit/test_config_cli_grammar.py` | 105 | 428 |
| `tests/unit/test_config_cli_local.py` | 620 | 663 |

The grammar's own shape, unchanged: 18 top-level commands in the order
they were declared in before, 5 of them groups, 34 invocable commands
in all, 9 of them mutating members of the break-glass subset and 7 of
them reading members.

Exit greps, from `vinga-server/`:

```
$ grep -n "argparse" src/vinga_server/config/cli.py
1907:# reproduces argparse's `default=SUPPRESS` dance exactly. A sentinel
1994:    exactly the reason argparse's raw formatter was asked for before
```

Both are prose naming what the new code replaced; there is no import
and no call. `_Parser` and `_usage_problem`'s old shape: the class is
gone, and `_usage_problem` is the name of the new boundary, which
takes a `ClickException` rather than argparse's message string and
translates by class.

### Verification

All from `vinga-server/`.

- `uv run ruff check .`: `All checks passed!`, at each commit.
- `uv run pytest tests/unit -q -n auto --dist loadfile`:
  `2959 passed, 20 skipped in 42.10s` at the tip (2870 before the
  milestone; the 89 are the new derivation, planted-secret, help and
  matrix cases).
- `uv run pytest tests/integration -q`: `61 passed in 192.03s`.
- `uv run mypy` (the scoped `events` lane):
  `Success: no issues found in 4 source files`.
- The four drift checks, run the way CI runs them
  (`config reference`, `conversations schema`, `events reference`,
  `config openapi` each diffed against its committed copy): all four
  clean. `domain-config.md` did not move, which is what decision 3
  requires of M1: no descriptor's `command` field changed.
- `uv sync --frozen`: `Checked 99 packages`, after the lock change.
- The child-interpreter docgen pin
  (`test_the_reference_and_the_schema_render_from_the_models_alone`)
  green untouched: `ALLOWED_IMPORTS` is the same exact set, because
  the `fragment_help` deepening reads two functions that were already
  in the module.
- The matrix proved to bite: with `Globals.merged` rewritten to take
  the leaf value unconditionally, 33 tests of the grammar and local
  suites fail; restored, 133 pass.
- The help tests proved width-independent: the grammar suite is green
  at `COLUMNS=40`, `80` and `200`.

### Not verified here

Nothing about a real terminal. Every help assertion in this milestone
is made against captured output with the spaces taken out, because the
formatter wraps at the width of whatever it prints to. What a person
sees in an 80-column terminal was read by hand during the rebuild and
is not pinned; decision 8's marker-delimited reference, which renders
at a fixed width with no terminal detection, is M4's and is where that
becomes a check rather than a reading.

## PR review round, M1 (PR #271)

External review of the PR diff, 2026-08-23. Verdict as received:
mergeable after fixes. Three findings, one P1 and two P2, each fixed in
its own commit. All three were about the same thing from three sides:
a claim this milestone makes that its tests could not have caught being
false.

### 1. P1: `--help` carried Typer's exception out on its chain

`_parsed` turned Click's exit request into `SystemExit` from inside the
handler, with `raise ... from None`. That sets `__suppress_context__`,
which stops a traceback being printed and leaves `__context__` exactly
where it was, so the Typer exception rode out behind the exit, holding
the context it was raised from and therefore the argument list. The
module's whole no-leak discipline is about what a chain walker finds
rather than about what is displayed, so the suppression was cosmetic.

Fixed in `363f299e`: the exit code is recorded in the arm and raised
after the block, which is the pattern every refusal here already raises
by. Four cases pin it, at the root, at a leaf, at the one group word
that is also a command, and at a command that reaches nothing: exit 0
with `__cause__` and `__context__` both None. With the raise moved back
inside the arm all four fail.

### 2. P2: the planted-secret suite did not reach three branches

The boundary translates five Click classes and falls back for anything
else. Two of those classes and the fallback have no route through this
grammar, so three branches were unexercised, and every assertion the
suite made was about a stream, which meant the sanitized raise could
move back inside the Click handler with the suite still green.

Fixed in `bc341fc0`, three ways. The six reachable shapes assert the
credential is absent from the log too, in both renderings a deployment
keeps (`tests/support/events.both_formats`). The same six assert the
refusal's own chain: both slots empty, and nothing in the chain says
the credential, read through `tests/support/config_cli.chain`. And the
four shapes the grammar cannot produce (`BadParameter`,
`BadArgumentUsage`, a base `UsageError` whose words are new, and a
`ClickException` that is not a usage error) are driven at
`_usage_problem` itself, each constructed carrying a credential in the
message Click would have written.

Two reach-ins, both decided rather than drifted into. `cli._parsed` is
reached because `main` catches this exception by design and answers
with a sentence and an exit code, so no caller-facing surface ever
holds it and the claim cannot otherwise be stated. `cli._usage_problem`
is reached because a branch with no command line that produces it is
exactly the branch that leaks the day it becomes reachable. The
constructed exceptions are built from the names `cli` itself imports,
so a Typer upgrade breaks one place rather than two.

With the sanitized raise moved back inside the handler, all six chain
cases fail, each naming the Click exception found behind the sentence.

### 3. P2: `--from-env` did not say what it was the alternative to

Its default is behavior: with no `--from-env` the secret is read from
stdin, without echo at a terminal. The help said nothing about it,
Typer prints no default for the `None` that stands for not given, and
the help test checked descriptions and required markers only, so an
option that read as having no alternative passed.

Fixed in `c474c9de`. The help says it in the shape the two sibling
options already use, and the tree-enumerating test holds every option
that takes a value and does not have to be given to stating a default;
flags are excluded, because a flag that is not given is a flag that is
not given. The same commit gives the `set` help test decision 4's third
column: each field's description, not only its type and its default. A
type and a default with no sentence beside them say what a key holds
without saying what it is. With `--from-env`'s default removed and the
descriptions dropped from the generated listing, seven cases fail.

### Verification after the round

All from `vinga-server/`.

- `uv run ruff check .`: `All checks passed!`
- `uv run mypy`: `Success: no issues found in 4 source files`
- `uv run pytest tests/unit -q -n auto --dist loadfile`:
  `2973 passed, 20 skipped in 42.91s` (14 more than before the round:
  4 from finding 1, 10 from finding 2).
- `uv run pytest tests/integration -q`: `61 passed in 198.43s (0:03:18)`
- The four drift checks: all clean, `domain-config.md` still unmoved.
- `uv sync --frozen`: `Checked 99 packages in 1ms`
## M2: inline values, apply, export

### What was done

Ten commits: the write path's reshape, the verb that reshape exists
for, its route, the three surfaces the milestone adds, two the review of
the finished diff produced, and this record with the help pin beside
it.

**The phased write path** (`ab452399`). `store._write` was one function
that opened its own transaction, placed the entry, ran the reference
pass and wrote the row, in two arms telling apart a fragment that
depends on stored state from one that does not. It is four phases now,
with the transaction between them:

- **Preparation** (`_prepare`), outside the lock: the name made usable,
  the kind's own before-parse check, the body read as a fragment, and
  the model where nothing about the fragment depends on the store,
  which is whenever it carries no unchanged-value marker. A fragment a
  caller got wrong still costs no lock.
- **Staging** (`_stage_entity`), inside it: the marker resolved against
  the one snapshot the transaction read, the model run where
  preparation could not, and the entry placed where the configuration
  would hold it.
- **Checking**: `_refuse_unresolved` against that candidate state, once.
- **Persistence** (`_persist`): the rows that moved.

A row became data (`_Row`: a table, the columns that address it, and
the columns to set, with `values=None` the delete). That is what lets
staging decide what a write would do without doing it, and it gives a
device binding and the default-agent setting one row shape each
whichever of the three verbs writes them.

`ConfigStore._write` is now the one-entity case of those phases, which
is decision 6's requirement that there be one write path rather than
two.

**`ConfigStore.apply`** (`a5bfe63b`). A partial DomainConfig-shaped
document, in one transaction: every entry prepared, every entry staged
into one candidate state, `check_references` run once against it, then
the rows written. The settings arrive in their domain shape and are
adapted onto the same normalization and the same rows their own verbs
write. Additive, idempotent by comparison, refused whole and reported
whole. The entry count is bounded (`APPLY_LIMIT = 500`) and refused
before anything is prepared.

**`POST /apply`** (`b009659e`). The store answers canonical outcomes;
the route computes the notices from the loaded-agents and snapshot-only
dependencies the settings routes already use, and does the device
housekeeping the two settings routes do, all of it after the commit.
The body is described as `DomainConfig` in the document, which is one
shape rather than a second schema restating it.

**Inline `key=value`** (`fe774855`). The pairs assemble the mapping the
fragment would have held and enter the exact path a `-f` fragment
enters. `-f` and the pairs are exclusive, and neither given is the
missing-argument sentence, raised by the grammar itself because Click
cannot see it: either of the two satisfies the command. Six malformed
shapes, six fixed value-free sentences, PyYAML's exception caught
without binding it and the refusal raised after the arm.

**`config apply`** (`c0a8d7fe`) and **`config export`** (`eb2d0a37`).
`apply` posts a document with no read timeout at all;
`export` is CLI-side assembly of the whole-document and per-entity
reads, with a reproduction header and the stored credentials rendered
as the commands that enter them.

**A dotted identity, split by its parameters** (`54088887`), and **one
home for that split** (`1b1e0468`). Both are in Discoveries below.

### Deviations from the plan

Three, and the first is the one worth reading.

**1. Idempotence compares the ENTRIES, not the write-shaped
projection.** Decision 6 says an entry is compared "against the stored
one through the same write-shaped projection reads use (masks resolving
to keep-stored per #192's marker)". Both readings of that were tried
and both are wrong, in opposite directions.

Comparing the row a write would produce (`_to_row`, which is
`exclude_unset`) reports an entry that spells a field at its own default
as a change from one that leaves it out. That is not a corner case: a
display shows a default that is a real value, so an EXPORTED body
carries fields the write that created the entry never spelled, and an
export applied back onto its own store reported most of itself as
written. The round-trip test caught it.

Comparing the masked display, which is what "the projection reads use"
literally names, is worse. The marker substitutes stored values into
the incoming fragment where a mask appears, but a fragment carrying a
NEW value under a secret-shaped key is not touched by the marker, and
the display masks both sides to `********`: rotating a lowercase
`api_key_env` from one value to another would be reported unchanged and
silently not written, on exactly the values #192's marker exists for.

So the comparison is `entry != stored`, the two pydantic models. Two
entries holding the same values are the same configuration however
sparsely either was written, and a value that differs is a value that
differs. Both rejected readings are pinned as tests
(`test_a_body_spelling_a_default_it_holds_is_unchanged`,
`test_a_value_the_display_would_mask_is_still_compared`) so the
comparison cannot drift back to either.

**2. The refusals aggregate in two rounds rather than one.** The plan
says refusals "aggregate into one ConfigError". They aggregate per
PHASE: every preparation refusal at once, and, if preparation was
clean, every staging refusal at once. A document whose fragments will
not parse never reaches staging, so its reference refusals are not in
the same message. That falls out of the phase structure rather than
being chosen: staging cannot run against a candidate state that has
entries missing from it, and reporting reference refusals computed
against a half-built state would be reporting refusals that are not
true. The reference pass itself still produces one refusal listing
every unresolved reference, which is the sentence a single write earns,
byte for byte (`test_a_reference_refusal_is_the_sentence_a_single_write_earns`).

**3. The body size bound is weaker than "refused before any
mutation" suggests, and says so.** FastAPI reads and parses a request
body before a handler or any dependency runs, so the check in
`apply_document` is against `Content-Length` after the body is already
in memory: what it bounds is what reaches the repository, not what the
socket accepted, and a request that declares no length is bounded by
the entry count alone. The entry count is the bound that matters, since
it is the one that bounds the transaction, and the comment beside
`APPLY_BODY_LIMIT` states this rather than implying otherwise.

### Discoveries

**A name may hold a dot, and an identity is not split at every one
of them.** An applied document names a provider by the dotted join of
its stage and its name, which is the spelling every other surface uses.
Reading it back by splitting at every dot made `llm.claude.v2` into
three parameters where the kind takes two, and the zip that pairs them
with the columns they select on raised a ValueError rather than a
refusal. Nothing about a name forbids a dot: the write path refuses a
slash and a control character and nothing else. Found by reading the
finished diff rather than by a test, and now guarded by one
(`test_a_name_holding_a_dot_is_still_one_name`).

The fix has one home, `store.addressed(descriptor, identity)`, because
three surfaces ask the same question: an applied document, a stored
secret's location, and the CLI's export, which renders a location back
into the `set-secret` command that fills it. That last one is new, and
it is what made the rule worth publishing rather than spelling a third
time.

**Two name collisions, one of them silent.** `store.py` already had a
`_stage(stage: str) -> str` (the provider-stage check) and `cli.py`
already had a `_stored_slots(...)` for the summary tree. Ruff's F811
does not fire when the first binding is used before the second is
defined, so the store's collision passed lint and simply called the
wrong function. Both were renamed (`_stage_change`, `_stored_slot_note`)
and both files now pass a `grep -oE "^def [a-zA-Z_]+" | sort | uniq -d`
check, which is worth running after any commit that adds several
functions to one of these files.

**Skipping the write for an unchanged entry is observationally
equivalent.** The one case where it changes the TABLE rather than the
row is `set agent-defaults` with an empty body against a store that has
no singleton row: nothing is inserted. Every read answers the same,
because an unwritten singleton is the empty entry by construction
(`store._entry`), and the whole suite is green on it.

**The apply document's schema is `DomainConfig` itself.** Every field
of that model has a default, so the schema of the whole configuration
IS the schema of a partial one, and the request body needed no model of
its own.

### The artifact diff

`docs/reference/api-openapi.json` moved, and only it. Read as a diff
rather than trusted:

| | |
| --- | --- |
| paths added | `/apply` |
| paths changed | none |
| schemas added | `AppliedDocument`, `AppliedEntry`, `DomainConfig`, `ProvidersConfig` |
| schemas changed | none |
| schemas removed | none |
| `info` changed | no |

`+260` lines, all additions. `domain-config.md`, `conversations-schema.md`
and `events.md` did not move, which is what the risks section requires
of every milestone: no descriptor's `command` text changed, and no
spelling did.

### The inventory

`wc -l`, before at `7a81395e` and after at `eb2d0a37`:

| File | Before | After |
| --- | --- | --- |
| `src/vinga_server/config/store.py` | 2043 | 2549 |
| `src/vinga_server/config/cli.py` | 2678 | 3228 |
| `src/vinga_server/config/api.py` | 2487 | 2624 |
| `src/vinga_server/config/responses.py` | 1016 | 1070 |

New suite: `tests/unit/test_config_apply.py`, 525 lines. The grammar
grew two top-level commands (`apply`, `export`) and five leaves under
`export`, taking the invocable count from 34 to 41; the break-glass
subset is unchanged at nine mutating and seven reading members, and
neither new command is in it.

### Verification

All from `vinga-server/`.

- `uv run ruff check .`: `All checks passed!`, at each commit.
- `uv run pytest tests/unit -q -n auto --dist loadfile`:
  `3065 passed, 20 skipped in 42.55s` at the tip (2959 at M1's tip; the
  106 are the apply suite, the inline-value and boundary cases, the
  route's cases, the pending housekeeping, the export round trip and
  the help pin).
- `uv run pytest tests/integration -q`: `61 passed in 193.28s`. The
  first run of it reported a spurious error, and it is worth knowing
  what it was: `tests/integration/conftest.py` asserts that no
  `__pycache__` exists under `src/`, and a `uv run pytest` invoked
  without `PYTHONDONTWRITEBYTECODE=1` leaves some. Delete them and
  export the variable, which is what `AGENTS.md` says about everything
  that is not pytest and, on the strength of this, about pytest run from
  a shell too.
- `uv run mypy` (the scoped `events` lane):
  `Success: no issues found in 4 source files`.
- The four drift checks, run the way CI runs them: all four clean.
  `api-openapi.json` regenerated and committed; the other three did not
  move, asserted as `git diff --stat` over `docs/reference/` showing
  one file.
- `uv sync --frozen`: `Checked 99 packages`. No dependency moved in
  this milestone.
- The store refactor's equivalence: every one of the 2959 tests M1 left
  green is green unchanged after `ab452399`, including the whole
  unchanged-value marker round trip, the break-glass differential
  (`test_a_local_write_acknowledges_what_the_api_acknowledges` and its
  wording sibling), the body round trip in `test_config_bodies.py` and
  every API write case. No assertion was edited to accommodate the
  reshape.

### Not verified here

The live lane, which is M3's: every case in this milestone runs the CLI
through the acceptance seam (`TestClient` in place of a socket) rather
than over a real one, so `apply`'s unbounded read timeout is asserted
against a mock transport rather than against a slow server, and the
over-limit refusal is asserted through the in-process application.
Decision 10 puts both of those on the wire.

## PR review round, M2 (PR #272)

External review of the PR diff, 2026-08-24. Verdict as received:
mergeable after fixes. Six findings, four P1 and two P2, each fixed in
its own commit.

Four of the six are about the same thing, and it is worth naming once
because it is the pattern rather than four accidents. This milestone
put a new surface in front of validators that already existed: a
document is written by hand, holds several entities and reaches every
refusal a single write reaches. Three of those refusals quoted what
they rejected, and one library exception was not being caught at all.
None of that was introduced here, and all of it was reachable here for
the first time from one command an operator pastes a whole
configuration into. Where a wording was pre-existing it is noted below,
and every one of them was changed in the SHARED semantics rather than
in apply's use of them, so a single write and an applied document keep
saying one thing.

### 1. P1: a YAML source could leak through the parser's own words, or not be caught at all

Two failures behind one finding, and they were in the two halves of
what had been two parsers.

The fragment reader built its refusal from `exc.problem`, and PyYAML
answers a tag it has no constructor for by quoting the tag. A tag is a
run of characters an operator typed, so `!<credential> value` is a
document whose refusal printed the credential. That shape was there
from the beginning, for `-f` writes; apply inherited it by calling the
same function. **Pre-existing wording**, hardened at the boundary the
two now share.

The inline reader caught `yaml.YAMLError`, which is what PyYAML
documents rather than what it raises. Its constructors raise the
ordinary exceptions for a scalar out of range: an integer of five
thousand digits leaves as CPython's own `ValueError` about the digit
limit, `2026-99-99` as a `ValueError` from `datetime`, and two thousand
nested lists as a `RecursionError` out of the composer. All three
reached an operator as a traceback with the source in it, and an
integer of five thousand digits fits on a command line as easily as in
a file.

*Fixed* (`741cfe4d`): `_parsed_yaml` is the one boundary. It catches
`(YAMLError, ValueError, ArithmeticError, RecursionError)`, records the
category inside the arm and raises after it, and says a fixed sentence
plus at most the two integers of the mark. Nothing of the exception
survives the arm. Cases: four unreadable sources (a tag, a scalar the
constructor refuses, an unterminated quote, an impossible date) times
the two commands that read one, plus the chain for each, plus an
eighth inline-value case for the non-YAMLError family.

*Load-bearing:* narrowing the caught set back to `YAMLError` fails
eight cases; putting the parser's words back into the sentence fails
three.

### 2. P1: an unresolved reference quoted the name it could not find

Four of the five refusals `check_references` writes quoted it: the
default agent, an agent in a device binding, a provider reference and
an MCP server. The fifth, `prompt_includes`, was deliberately written
the other way when it was added, and its own comment gave the reason:
a reference is written beside prompt text and provider options, so it
is a place a paste lands; the sentence travels out as a CLI line, an
HTTP 422 body and a boot log; and the charset rules do not close it,
since a credential can be written in `[A-Za-z0-9_-]`. Every word of
that is true of the other four. **Pre-existing wording**, and #132's
own rule already covered it: its deliberately-unchanged list is the
refusals that describe STORED configuration, and a submitted reference
is not one.

The finding also caught that the M2 no-leak case for this never reached
the reference pass at all: its fragment was refused a phase earlier, in
preparation, so it was asserting the wrong boundary's promise.

*Fixed* (`0b83302a`): each of the five names the field path, which says
which entry to look at, and the names that DO exist, which say what
could have been meant, both written by this deployment. A list entry is
named by its position, the shape `prompt_includes` already had and
which `mcp` and a device binding have joined. `defined()` is the hint's
one home, since five refusals were spelling it four ways.

Cases: one per reference edge over the store, HTTP and the CLI, each
with a credential where the name goes and each fragment otherwise
VALID, plus `test_the_reference_pass_is_reached_at_all` as the guard
that says the pass was reached, which is the thing the first version
did not check.

*Load-bearing:* putting one quoted name back fails eleven cases across
the three surfaces.

### 3. P1: two more validators quoted their rejected input

A binding naming one agent twice said which name; an MCP entry name
that is not a usable tool prefix was interpolated into the refusal's
own path. **Both pre-existing.** The second is the sharper miss:
`check_prompt_fragment_names` sitting beside it had already been
written the other way and its docstring named this one as the
exception.

*Fixed* (`168f2b46`): the binding names positions, which is what every
other list in `models.py` does, and compares the names as they will be
stored, so the two spellings of one name are the one name they become
rather than a binding that silently holds it once. The entry-name rule
becomes `MCP_ENTRY_NAME_RULE`, the sibling of
`PROMPT_FRAGMENT_NAME_RULE`, and says one sentence however many names
fail it: a section keyed by what the operator wrote has no position to
point at, and two identical lines would only suggest the second was
about something else.

*Load-bearing:* putting either quoted value back fails fourteen cases
across the store, the API, the CLI and the boot path.

### 4. P1: two entries could address one thing, and the last one won

A mapping cannot hold one key twice, which rules out the obvious
duplicate and rules out nothing else: a name is made canonical on the
way in, and two keys that differ before that are one key after it.
`AA-BB-CC-DD-EE-FF` and `aa:bb:cc:dd:ee:ff` are one device; `sam` and
` sam ` are one agent. Both were staged, both were answered with an
outcome, and the row held whichever was written last. New in this
milestone: a single write cannot have this problem, because it writes
one entity.

*Fixed* (`ab1ba5f0`): `_distinct_entries` between the two phases,
because canonical is what preparation makes and because it is a
question about the document rather than about the store. Refused rather
than merged, for the reason a claim by activation code is refused
rather than merged: the two entries say different things about one
thing and only whoever wrote them knows which is meant.

*Load-bearing:* removing the call fails the three cases.

### 5. P2: the structural phase did not aggregate

The two phases after it did, and the comment beside them says a
document is refused whole and therefore reported whole, so a document
with a malformed `agents` section and a malformed `devices` section
contradicted the code's own claim by reporting one of them.

*Fixed* (`082873d7`): the extraction aggregates, and the providers
section aggregates its stage groups inside it. `_Aggregated` is what
makes the nesting fold: a phase that runs another inside it catches the
type and folds `lines` in, where an ordinary `ConfigError` would be
folded in whole and put a second headline under the first. The stage is
now checked before the shape under it, so a word that is not a stage is
refused as one.

*Load-bearing:* an eager loop reports one section; dropping the fold
nests a headline. Either fails the case.

### 6. P2: an exported command broke on a legal leading-dash name

Nothing about a name forbids a leading dash: the write path refuses a
slash and a control character and nothing else, so `--from-env` is a
legal provider name and `--local` a legal slot. The exported
reproduction command wrote it as a bare word, and the grammar read it
as an option, so the one line an export exists to be pasted was the one
line that did not run.

*Fixed* (`242124e2`): Click's `--` after the command's own words.

**What the live paths needed, which the finding asked about: nothing.**
A leading-dash name is written, read, deleted and given a credential
today with the same `--`, which is Click's own mechanism and works
through this grammar unchanged; it is also the only way to write such a
name in the first place, so the exported command is now the command an
operator typed. Without the marker the refusal is an honest one (`that
is not an option of this command` for a `set`, `a required argument is
missing` for a `set-secret`) rather than a silent wrong write, and
`test_the_same_command_without_the_marker_does_not_run` pins that side.
The API is unaffected: an identity travels as a percent-encoded path
segment, where a dash means nothing.

*Load-bearing:* dropping the marker fails the executing case and the
export round trip. The case EXECUTES the exported argv rather than
reading it, because a rendering that merely looked right is what it
replaces.

### Verification after the round

All from `vinga-server/`.

- `uv run ruff check .`: `All checks passed!`, at each commit.
- `uv run mypy`: `Success: no issues found in 4 source files`
- `uv run pytest tests/unit -q -n auto --dist loadfile`:
  `3137 passed, 20 skipped in 43.03s` (72 more than before the round).
- `uv run pytest tests/integration -q`: `61 passed in 193.71s (0:03:13)`
- The four drift checks: all four clean, and none of the four committed
  documents moved in this round. `api-openapi.json` was allowed to and
  did not need to: every refusal reworded here is carried in `detail`
  at runtime and is not part of the contract's bytes, and no response
  model or schema changed.
- `uv sync --frozen`: `Checked 99 packages in 1ms`

## M3: the live acceptance lane

### What was done

Ten commits, all of them test assets. One new module,
`tests/integration/test_cli_live.py`, and nothing else in the repository
moved.

Nine of the ten are the milestone's own, below. The tenth (`077016a9`)
is the rebase over M2's review round: that round's finding 6 put Click's
end-of-options marker into the exported `set-secret` commands, and this
lane's export assertion still expected the bare form, so the assertion
now expects the command an operator types. It postdates the first
writing of this section, which is why it is named here rather than
inferable from the commit list.

The hashes below are the rebased ones. Every number in this section, the
inventory and the verification included, was observed at the branch tip
after the PR review round recorded at the foot of it.

**The harness** (`ff6094e3`). A real uvicorn on an ephemeral loopback
port, in a thread, driven by `cli.main` with `build_client` untouched:
the address resolution, the transport policy, the bearer token and both
timeouts are the deployed ones, and what answers is a server. The
module's server is booted once and shared, because what the cases
describe is one operator's session against one deployment; the two that
assert what a store holds afterwards ask for a second server on a store
nobody else wrote.

The boot is decision 9's fileless one: `load_boot_config()` with no path
and no `VINGA_CONFIG`, so the file half comes from `VINGA_SERVER__*` and
the packaged defaults. That is a deviation from decision 10's
`served_api`, and the reason is below.

`run` is the whole of the harness's own vocabulary: one command line,
run the way the entry point runs it, recorded against its row of
`cli.COMMANDS` when it succeeded.

**The families** (`94301255`, `8af566d1`, `54700aa4`, `fc729d67`,
`0954e739`). One entity's whole life per commanded kind (written from a
fragment, read, exported, written again from the inline pairs, deleted);
both device commands, the second of them through a real OTA check-in;
a credential entered both ways, read back masked, exported as the
commands that refill it, cleared; the reload and the two reads of the
running server; the export round trip against a store the lane wrote
through nine commands. The four commands that reach nothing run in the
same environment, which is the only thing worth asserting about them
here: the environment names a server and a database and they still open
neither.

**The refusals** (`0d45dccd`). One per family, where a family is a
top-level word of the grammar and the set of them is read off the
registration table. Each asserts the sentence as it arrived, exit 1, and
nothing on stdout. The table's `wire` column says where each refusal is
composed, and a second parameterized case makes that load-bearing: every
command line is run again with the root `--api-url` pointing at a port
nothing listens on, where a refusal the server composes cannot happen
and the transport sentence takes its place, and a refusal this side
composes is unchanged.

**The two apply-bound proofs** (`cf0c80b0`), on a store of their own.
`APPLY_LIMIT - 1` entries are applied while an ordinary read of the same
server, through the same client implementation, with its bound cut to
something this server cannot meet, gives up and says so. The over-limit document is
refused with the limit named, nothing of it quoted, and the store read
back empty.

**The closing two** (`e834a3e0`). The fileless boot stated as a claim,
and the completeness test.

### The coverage map

Twenty families, forty-one commands, every one of them run in this lane
and answered successfully. Thirty-seven of them reach the server, and
for those "answered" means over real HTTP: an address resolved, the
transport policy applied, a socket opened, a bearer token checked by a
real ASGI server. The other four (`schema`, `reference`, `openapi`,
`ota-url`) reach nothing by design, and what the lane asserts about them
is that opposite claim, in an environment that names a running server
and a database directory. The `family` column is `row.words[0]` for
`row in cli.COMMANDS`, which is what the refusal table is held to; the
`commands` column is that family's rows.

| Family | Commands | Driven by |
| --- | --- | --- |
| `set` | the 5 kinds | the per-kind cycle, both write forms |
| `delete` | the 4 deletable kinds, `device` | the per-kind cycle; the bind case |
| `show` | itself, the 5 kinds, `device` | the cycle, the bootstrap read, both device cases |
| `export` | itself, the 5 kinds | the cycle; the round trip |
| `apply` | itself | the bootstrap, idempotence, the round trip, both bounds |
| `set-secret` | `provider`, `mcp-server` | the credential case, `--from-env` and stdin |
| `clear-secret` | `provider`, `mcp-server` | the credential case |
| `bind-device` | itself | the known-MAC case |
| `add-device` | itself | the activation-code case, after a real check-in |
| `pending` | itself | the activation-code case, before and after the claim |
| `set-default-agent` | itself | the activation-code case (a check-in mints no code) |
| `clear-default-agent` | itself | the same case (the next check-in does) |
| `reload` | itself | the running-server case |
| `status` | itself | the running-server case, after the reload |
| `prompt` | itself | the running-server case, refused before and answered after |
| `list` | itself | the running-server case |
| `schema` | itself | the offline case, whole and per kind |
| `reference` | itself | the offline case |
| `openapi` | itself | the offline case |
| `ota-url` | itself | the offline case |

The completeness test is derived rather than declared: `run` records
what ran, the test holds that recording to `cli.COMMANDS`, and a command
with no successful run in the lane is named in the failure. It proved to
bite: with the `ota-url` call taken out of the offline case, it fails
with `['ota-url']` and nothing else does.

### Deviations from the plan

Four, none of them to what the lane covers.

**1. The lane boots its own server rather than using `served_api`.**
Decision 10 names the `served_api` fixture and the `test_ota_endpoint.py`
pattern. The pattern is what this uses (a real uvicorn in a thread on
port 0, waited for on `server.started`); the fixture is not, for two
reasons. It composes a `Config(...)` in Python, which is not a fileless
boot and would have left decision 9's case with a second server of its
own to boot and assert against; and it is function-scoped, which would
have been one boot per test where the whole point of the shared store is
that a session has history. The lane's `serving` is the same shape with
`load_boot_config()` in place of the composed `Config`, so the fileless
boot is what every case in the lane runs against and the case that
asserts it is a claim about the server the rest of the lane used rather
than about a server built to be asserted about.

**2. No production hook was needed for the inventory derivation.** The
milestone entry allows one, required to be a read of the registration
table. `cli.COMMANDS` is already in the module's `__all__` and `Command`
already carries `words`, so the derivation is an import and the
production tree is untouched. Recorded because the allowance was
explicit and unused.

**3. `add-device` is driven the whole way, not "as far as a real server
allows without a device".** A board's part in the ceremony is one HTTP
POST to the OTA endpoint, which `test_ota_endpoint.py` already makes
without hardware, and the fixture serves the whole app rather than the
API alone, so the endpoint is on the same port. The lane therefore mints
a real code, lists it with `pending`, and claims it with `add-device`.
That is more than the milestone asked for and is the reason the two
settings commands became assertable too (see the discovery below).

**4. The apply-timeout differential rewrites one row of the
registration table.** Asserting that a large batch "completes with the
client waiting it out" needs a comparison, because a batch this lane can
afford to run finishes well inside the ordinary 30 second bound and a
test with no comparison would pass with any bound at all. The bound
reaches a request only through the act on a command's row (`_call`'s
default and `Act.read_timeout_s` are both bound at import, so patching
`cli.READ_TIMEOUT_S` changes nothing), so the case monkeypatches
`cli.COMMANDS` with `show`'s row rebuilt at a 5 ms bound and compares
the two commands against the same server through the same client
implementation (each command builds a client of its own and closes it,
so what they share is the server and the code that talks to it rather
than one open connection). The
threshold is deliberately short so the test finishes, which is the same
move `test_config_api.py` makes with the database's busy timeout and for
the same reason. Measured: the apply of 499 entries takes ~145 ms and
the read it is compared against ~30 ms, so both sides of the comparison
clear the bound by an order of magnitude.

### Discoveries

**A binding and the default agent are observably live, and it takes two
check-ins to say so.** `set-default-agent` then a check-in mints no code
at all, because the board resolves to the default agent;
`clear-default-agent` then the same check-in mints one. Nothing
in-process can show that: what re-reads the database between the two
requests is the running server, and the pair is now the whole of the
onboarding case's spine rather than a setting written and read back.

**A read of a 499-agent store takes about 30 ms over loopback.** That is
what makes deviation 4's comparison possible at all, and it is worth
recording as the number the 5 ms bound was chosen against.

**`clear-secret` on an entity that does not exist says "no secret is
stored for that slot".** Not "no provider of that name exists", which is
what the sibling `set-secret` says and what the refusal table was first
written expecting. It is the honest answer for a slot read rather than
an entity read, and it is not a bug; recorded because the two commands
sit next to each other and read differently.

**Nothing in M1's or M2's work broke on a real socket.** The lane was
written expecting to find something, since every case in it was
previously asserted through an in-process client. Every command answered
the way the acceptance suites say it does, and no production code was
touched in this milestone.

### The inventory

| File | Lines | Test functions | Cases |
| --- | --- | --- | --- |
| `tests/integration/test_cli_live.py` | 1567 | 18 | 60 |

At the milestone's own tip it was 1,163 lines, 17 functions and 59
cases; the review round's leak work is the rest.

Nothing else changed. `src/` is byte for byte what M2 left, which is
what the milestone's "test assets only" footprint means.

### Verification

All from `vinga-server/`.

- `uv run ruff check .`: `All checks passed!`, at each commit.
- `uv run pytest tests/unit -q -n auto --dist loadfile`:
  `3137 passed, 20 skipped in 42.83s`, which is exactly what M2's own
  round left: this milestone adds no unit test.
- `uv run pytest tests/integration -q`: `121 passed in 199.56s (0:03:19)`,
  where M2's tip was `61 passed in 193.71s (0:03:13)`. **The new lane's
  own runtime is 3.2 to 3.7 s** over three consecutive runs of
  `uv run pytest tests/integration/test_cli_live.py -q`, which is 60
  cases and four server boots. The lane does not double the integration
  lane's time; it adds about six seconds to it, which is what 60 cases
  against a server that boots on an empty database and never speaks to a
  device cost, the review round's log capture included.
- `uv run mypy` (the scoped `events` lane):
  `Success: no issues found in 4 source files`.
- `uv sync --frozen`: `Checked 99 packages in 1ms`. No dependency moved
  in this milestone.
- The four drift checks, run the way CI runs them: all four clean.
  Nothing in M3 touches an artifact source, and `git status` is empty
  after regenerating all four.
- The completeness test proved to bite: with the `ota-url` call removed
  from the offline case, it fails naming `['ota-url']`; restored, it
  passes. Under a partial selection (`-k`) it skips, with the reason
  printed, rather than failing for commands nobody asked it to drive.
- The apply-bound comparison proved to bite: with the shortened read
  bound raised to the production 30 s, the elapsed-time assertion fails
  at `0.14 > 30.0`, which is the case reporting that it has stopped
  comparing anything.

### Not verified here

**The wheel-and-subprocess grade**, which decision 10 records as #223's:
every command here runs in this process, through `cli.main`, rather than
as `vinga-server config ...` from an installed wheel. What that would
add is the entry point's own packaging, which is the standalone CLI
issue's business.

**A recipe run per published recipe**, which decision 9 puts in this
lane and M4 writes: there are no recipes yet, and the lane they will run
in is this file.

**Anything about concurrency.** One client, one command at a time. The
retryable 409 under a held lock is proven over a real socket in
`test_config_api.py`, and nothing here adds to it.

## PR review round, M3 (PR #273)

External review of the PR diff, 2026-08-24. Verdict as received:
mergeable after fixes. Three findings, one P2 pair and one P3, each
fixed in its own commit. The first is the one worth reading: a lane
whose whole subject is what crosses a connection had checks that would
have passed a value crossing it.

### 1. P2: the refusal cases accepted an appended leak

Every refusal asserted a substring of stderr and that no traceback was
in it. A sentence with a credential appended to it contains the
substring and holds no traceback, so the check that exists to catch
exactly that shape could not. Three more gaps of the same kind: the
credential case asserted on `out` and threw `err` away at every step, no
case looked at what the server LOGGED while a command ran, and the
over-limit case checked one entry name out of five hundred and one.

Fixed in `bf87ad30`, four ways.

**The whole of stderr, not a phrase in it.** The refusal table carries
the full text now and each case asserts equality. Where a constant
publishes the sentence it is imported (`cli.usage_line` with
`SECRET_NEVER_AN_ARGUMENT`, `entities.NO_SUCH_AGENT` and
`NO_SUCH_PROVIDER`, `models.NOT_A_MAC`, `models.DOMAIN_KEYS`,
`docgen.entity_names`, `store.TOO_MANY_ENTRIES`); where the text is
assembled at its raise site with no constant to import, it is written
out. The transport sentence, whose middle is the case's own ephemeral
address, is pinned at both ends instead, which bounds it the same way.

**A sentinel in the input.** `PLANTED` goes where a refusal's own input
can hold a credential: the body of the write that will be refused for
its reference, the fragment that will not parse (on the line above the
one it breaks on, where an operator's would be), and every entry of the
over-limit document. Where it deliberately does NOT go is the
unresolvable name itself, because the reference refusal names what it
could not resolve, and planting a credential there would be asserting
that a refusal must not say the thing it is for saying.

**The log records.** A `watched` fixture collects everything logged
while a case runs. `caplog` alone was tried first and is not enough:
pytest's handler sits on the root logger and uvicorn configures its
three loggers not to propagate, so the handler is attached to the root
and to each of those by name, with the root at DEBUG and `httpx` and
`httpcore` lowered from the level this server's logging setup pins them
at. Records are kept and rendered whole rather than formatted, because a
value can ride one interpolated into the message, unformatted in `args`,
on an extra attribute, or inside an exception on `exc_info`.

That the capture reaches the SERVER's thread is asserted rather than
assumed, in the onboarding case: an unbound check-in is the one thing in
this lane that makes the server log, and the case holds the collection
to containing a `vinga_server.*` record made on a thread this one is
not.

**The chain, one attribute deeper.** `carried` walks a refusal's chain
rendering each exception's repr, its str, its own attributes and one
level into what those attributes hold. The unit lane's `chain` helper
renders repr and str, which is not enough here: PyYAML keeps the WHOLE
buffer it was parsing on a mark object hung off the exception, and
neither the repr nor the str of the exception shows it. This was found
by the deliberate-leak run below rather than by reading, and it is the
one place the round changed what a check looks at rather than how much
of it.

### The deliberate-leak run

The finding asked for one case proved load-bearing. Three were, one per
surface, each a scratch edit to `config/cli.py`'s YAML boundary reverted
afterwards (`git status` clean, the file restored and touched per
`AGENTS.md`).

| Leak | Edit | Caught by |
| --- | --- | --- |
| the sentence quotes the source it could not parse | append `[{text}]` to the refusal | stderr equality, naming the appended fragment |
| the source is written to a log record and printed nowhere | a `DEBUG` record before the parse | `logs` |
| the refusal is raised inside the handler | `raise` moved into the `except` arm | `chain`, only after `carried` went a level deeper |

The third is the one that mattered: with the shallow walk it PASSED,
because `str()` of a marked YAML error renders the snippet around the
line it stopped on and not the buffer behind it. The deepened walk fails
it, naming `chain`.

One case in the lane now checks the checks: it plants the sentinel in a
record's arguments, on an extra attribute, inside an attached exception,
on both streams and behind an exception, and asserts every one of those
surfaces is named.

### 2. P2: the record predated the tip it described

The M3 section was written before the branch was rebased over M2's
review round, so its unit count (3,079) was a tree that no longer
existed, its file line count was two short, and its commit hashes were
the pre-rebase ones.

Fixed in this commit: every lane rerun at the current tip and every
number replaced with what came out, the hashes rewritten to the rebased
ones, the inventory given the function and case counts beside the line
count, and `077016a9` named in the section, since it postdates the
section's first writing and cannot be inferred from a commit list that
does not exist any more.

### 3. P3: the record claimed a socket four commands never open

The section and the changelog entry both said all forty-one commands
complete "over the wire". Four of them (`schema`, `reference`,
`openapi`, `ota-url`) deliberately reach nothing, and what the lane
asserts about them is the opposite claim, so counting them into the wire
total both overstated the coverage and lost the reason for running them
here at all.

Fixed in `b1b62d90`: forty-one run in the lane and answer, thirty-seven
of them over real HTTP, and the four are held to opening nothing in an
environment that names a running server and a database directory. "The
same connection" went with it, in the doc and in the two test docstrings
and the comment that said it: each command builds a client of its own
and closes it, so what the apply-bound comparison shares with the read
it is compared against is the server and the code that talks to it.

### Verification after the round

Rerun at the branch tip and recorded in the M3 section's own
verification block above rather than a second time here, which is what
finding 2 is about. In summary: ruff clean, mypy clean,
`3137 passed, 20 skipped` on the unit lane, `121 passed in 199.56s` on
the integration lane with the live lane's own runtime at 3.2 to 3.7 s,
the four drift checks clean with nothing regenerated, and
`uv sync --frozen` unchanged at 99 packages.

## M4: cli.md, recipes, presets, the sweep

### What was done

Six commits: two presets, two renderers behind one new verb, the
committed page and its drift lane, the live runs of both, and the
documentation sweep in two halves.

**The presets** (`f8790996`). `examples/presets/local-stack.yaml` and
`cloud-stack.yaml`, each a complete apply document: providers, the
defaults over them and one agent, with every credential in the cloud one
named as the environment variable that holds it. Neither names a device
or a default agent, because which board reaches which agent is the one
thing a preset cannot know, and both headers say so and quote the two
commands that do it. A second tier under `examples/` rather than more
fragments: a fragment is one entity's body, and what makes a preset
worth having is that it is several kinds at once, which is what stops
the creation order being something an operator has to know.

**The two renderers and the verb** (`96323551`). `config cli-reference`
prints the generated half of the CLI reference. Its command pages are
rendered in `cli.py`, walked off the tree `command()` builds, each
through a `Context` stating its width and refusing color; its recipes
are rendered in `docgen`, read out of the example files. Decision 8 put
the first here for the reason the design guide gives, and the second
follows the same rule from the other side: what a recipe reads is the
registry and the files it claims, which is `docgen`'s subject and not
this module's.

**The committed page and its lane** (`901972eb`). `docs/reference/cli.md`:
a hand-written head (installing it, reaching a server, the break-glass
path, what `apply` and `export` promise) and a generated region between
two marker comments. The drift check rebuilds the page rather than
diffing a fragment of it, which is described under the recipe-form
decision below.

**Both run over the wire** (`2ebdfcae`). Each preset applied twice
against a server booted on nothing, and every published recipe line run
in the published order against another. The lane's earlier commit
already carries `cli-reference` in the offline case and a refusal for
its family, since the registration table's completeness test would
otherwise have failed the commit that added the row.

**The sweep** (`6901007a`, `7374b9eb`). The two quick starts, the
example config's command block, and the deployment profile.

### The recipe form, and why the generated region is one

Decision 9 leaves the recipes' concrete form open and the milestone
brief allows them inside `cli.md`'s one generated region. They are
there, as a `## Recipes` section above `## Every command`, and the
reasons are three.

**One page, because a reader wants one.** The recipes answer "what do I
type" and the command pages answer "what does this take"; a person who
has just read the first has the second under the same scroll.

**One lane, because two would be two of everything.** A second generated
document is a second command, a second CI step, a second drift test and
a second committed file, for text that is regenerated by the same verb
in the same breath. The plan's "its own drift lane like the other
generated documents" is satisfied by the lane existing and biting, not
by the file count.

**One region, because the rebuild is simpler than an extraction.** The
check keeps everything up to and including the opening marker,
regenerates the region, closes it, and diffs the whole file. That is
also exactly how the page was first built, so the recipe a developer
runs to fix a failure is the check itself. The blank line after the
opening marker is part of the contract rather than a taste: a paragraph
pressed against an HTML comment is swallowed into the comment's block by
every markdown renderer there is.

Where the recipes come from is the part worth stating, because it is
what makes them derived rather than written. Every example file already
quotes its own commands as indented comment lines, and that indentation
is the whole of the rule: it is what tells a block meant to be copied
from a sentence that mentions a command in passing. Those quoted lines
are collected (presets first, then each descriptor's example files in
the registry's order), grouped by the words the command itself starts
with, and deduplicated. A `set <kind>` line finds its topic by the kind
it names; `apply`, the four device and default-agent verbs, and
`set-secret` have topics of their own; anything else is a refusal naming
the file, so a command block added to an example cannot be quietly
dropped on the floor.

That gives the whole list a property worth more than any of the above:
it runs top to bottom against an empty database, in the order it is
published, which is what the live lane does with it.

### The deployment script's disposition: migrated

`config.deploy.example.sh` is migrated onto one `config apply`, not
retired. It carries what a preset does not and should not: one
deployment's measured values (the CPU quota the ASR thread pool is
pinned to, the language ladder, the Swedish voice) and the deliberate
absence of a default agent that turns the device bindings into an
allowlist. It is also the companion to the server-half profile beside
it, and `tests/integration/test_config_examples.py` runs it verbatim
against a real server, which is what keeps those measurements from
drifting into prose nobody executes.

What the migration bought is what apply buys everywhere: seven writes
that had to be ordered are one transaction that cannot be half applied,
and its device binding is a `devices` section of the document rather
than a command after it, which exercises apply's settings adapter in the
integration lane as a side effect. Its header lost the sentence telling
an operator to restart the server afterwards, untrue since the reload
landed.

### Deviations from the plan

**The recipes have no drift lane of their own** (decision 9's "its own
drift lane like the other generated documents"). They share `cli.md`'s,
for the three reasons above. The lane bites either way: a changed
example header moves the generated region and fails the check.

**`docgen` reads a directory now**, which nothing above it in that
module does. The module's standing claim is about its import graph (the
models and the registry, no repository, no application) and that is
untouched: `pathlib` and `re` are the standard library, and the
child-interpreter pin is byte for byte what M3 left. What is new is that
one rendering needs the example files to be beside the package, which
they are in a checkout and are not in a built wheel, so
`docgen.MISSING_EXAMPLES` is a fixed sentence rather than a traceback.
Recorded as a cost rather than hidden: the alternative was a registry
that carried a second copy of every example's own command, which is the
drift decision 9 exists to prevent.

**No filler recipe.** Decision 9's topic list names fillers. A filler is
a `NestedShape`: it has no command, no route and no example file of its
own, which `test_a_nested_shape_has_no_fragment_command` already pins,
and it is written as a section of an agent or of the defaults. A recipe
for it would have to be hand-written beside the fragments, which is the
one thing the decision forbids. The agent and agent-defaults recipes
carry it, and `examples/agent-defaults.yaml` documents the section in
full.

**The quick start's preset is piped rather than named.** Decision 9 asks
the repository README's six heredocs to collapse to "one `vinga config
apply` line against a preset". They collapse to one apply line, reading
the preset from stdin: the container image ships the CLI but not
`examples/`, so a `-f examples/presets/local-stack.yaml` inside the
container would name a file that is not there. `-f -` with the preset
redirected in is the same one line and is true.

### Discoveries

**An example file quoted a command twice, and it showed.**
`tts-piper.yaml` quoted a second install (`piper_english`) to make the
point that a voice is an entry and two voices are two entries. Read as a
recipe, stripped of the sentence around it, that line installs the same
Swedish voice under an English name. The point is now made in prose and
the file quotes one command, which is the first thing the recipes
changed about a file rather than about a document.

**The registry's order is not `DOMAIN_KEYS`' order.** `ENTITIES` runs
provider, mcp-server, prompt-fragment, agent, agent-defaults, while
`DOMAIN_KEYS` runs the defaults before the agents. The recipes iterate
the registry, and the whole list still runs green, because an agent's
references are to providers and fragments rather than to the defaults
layer: writing an agent before the defaults is refused by nothing. Worth
recording because the two orders read as if they must agree and do not
have to.

**The four documentation commands became five and the matrix knew.**
Adding a row to `COMMANDS` failed three tests before it passed any: the
leaf-option matrix (a new command with no global options is an explicit
exclusion, not a default), the live lane's family-refusal table, and the
lane's completeness test. That is decision 5's declarative table working
exactly as it was meant to.

### The inventory

| File | Lines | What |
| --- | --- | --- |
| `docs/reference/cli.md` | 1495 | new; 225 hand-written, the rest generated |
| `examples/presets/cloud-stack.yaml` | 96 | new |
| `examples/presets/local-stack.yaml` | 86 | new |
| `src/vinga_server/config/cli.py` | 3431 | +141: the renderer, the verb, its row |
| `src/vinga_server/config/docgen.py` | 701 | +217: the recipes |
| `tests/unit/test_config_docgen.py` | 428 | +135: the presets tier, the recipes, the drift lane |
| `tests/integration/test_cli_live.py` | 1278 | +117: the presets and the recipes over the wire |
| `config.deploy.example.sh` | 143 | seven writes became one applied document |

Plus the sweep: `README.md`, `vinga-server/README.md`,
`config.example.yaml`, `config.deploy.example.yaml`,
`examples/README.md`, `examples/tts-piper.yaml`, and the workflow's
fifth drift step.

### The sweep's checklist

Re-grepped at the end of the milestone. Every `vinga-server config`
spelling outside `src/`, `tests/` and `vendor/`, checked against the
final grammar:

| Site | Verdict |
| --- | --- |
| `README.md` | rewritten: six heredocs to one apply |
| `vinga-server/README.md` | bootstrap rewritten; timeout paragraph now names apply's exception; two links to `cli.md` added; every other spelling unchanged and true |
| `config.example.yaml` | command block rewritten; the restart sentence removed |
| `config.deploy.example.sh` | migrated onto apply |
| `config.deploy.example.yaml` | the sentence describing the script updated |
| `examples/*.yaml` | unchanged except `tts-piper.yaml`'s duplicate quote |
| `examples/README.md` | two tiers described; the "applies at the next server start" sentence removed |
| `docs/reference/domain-config.md`, `events.md`, `api-openapi.json` | generated; none moved |
| `docs/devices/waveshare-esp32-s3-touch-amoled-2.16.md`, `docs/architecture/design-guide.md`, `AGENTS.md` | `add-device`, `prompt`, `schema`: unchanged spellings, still true |
| `spikes/2026-08-20-openapi-ts-client/` | generated client artifacts; `openapi` and `status` unchanged |

The four artifact-pinned spellings (`config reload`, `config
add-device`, `config bind-device`, `config ota-url`) are all still in
the grammar and all appear on the generated page, which is now a fifth
place they are findable.

### Verification

All from `vinga-server/`.

- `uv run ruff check .`: `All checks passed!`, at each commit.
- `uv run mypy` (the scoped `events` lane):
  `Success: no issues found in 4 source files`.
- `uv run pytest tests/unit -q -n auto --dist loadfile`:
  `3146 passed, 20 skipped in 43.37s`, where M3's tip was
  `3079 passed, 20 skipped`.
- `uv run pytest tests/integration -q`:
  `125 passed in 189.69s (0:03:09)`, where M3's tip was `120 passed in
  196.51s`. The CLI lane's own runtime is `64 passed in 3.94s`, against
  M3's 59 cases in 2.7 to 3.6 s: the five new cases add roughly a
  second, most of it the two extra server boots the preset cases need.
- **All five drift checks**, run exactly as the CI steps run them:
  `domain-config.md`, `conversations-schema.md`, `events.md`,
  `api-openapi.json` and `cli.md`, all clean. Only `cli.md` is new;
  none of the other four moved, which is what the risks section asks
  each milestone to assert.
- `uv sync --frozen`: `Checked 99 packages in 1ms`. No dependency
  moved: the renderer uses the Typer already in the tree.
- The child-interpreter import pin is untouched and green:
  `ALLOWED_IMPORTS` is byte for byte what M3 left, and
  `test_the_reference_and_the_schema_render_from_the_models_alone`
  passes, which is the claim that `docgen`'s new directory read did not
  widen its import graph.
- The drift lane proved to bite: an edited word inside the generated
  region fails `test_the_committed_cli_reference_matches_the_grammar`
  and the CI step's `diff`; the same edit in the hand-written head
  passes both, which is the half of the protocol a whole-file diff would
  otherwise have got wrong.
- The recipe extraction proved to bite: a command block added to an
  example with a verb no topic covers raises the fixed sentence naming
  the file, and one added with a covered verb turns the drift check red
  until the page is regenerated.
- `docs/reference/cli.md` read top to bottom as a landing document: no
  issue references anywhere on it, no em-dashes, every internal link
  resolved, and both halves of the installation section run as written.

### Not verified here

**The published-package installation.** `cli.md` says `uvx
vinga-server` and `uv tool install vinga-server` do not resolve a
release, and gives the checkout and wheel forms instead. Both of those
were run by hand; neither is in a lane, and the packaged form belongs
with the standalone CLI issue that will publish one.

**`config cli-reference` from a built wheel.** It reads the example
files, which a wheel does not carry, so it refuses with
`docgen.MISSING_EXAMPLES` there. The other four rendering commands run
from a wheel and one of them is checked that way in CI; this one is
deliberately a repository command, and the CI step that runs it runs it
from the checkout.

**The presets against real engines.** Both apply green and read back,
which is a claim about the configuration rather than about Ollama,
faster-whisper or a vendor key. Whether the deployment they describe
answers is what a device checkpoint is for.
