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
