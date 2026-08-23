# Pin the event catalog once, not three times: implementation

Companion to
[`2026-08-22-pin-catalog-once.md`](2026-08-22-pin-catalog-once.md). One
section per milestone, recording what was actually written, the
deviations from the plan, what building it turned up, and the
verification results as they came out. A milestone with no deviations
says so explicitly.

## M1: retire the two JSON pins

### What was done

Five commits, in the plan's order: the docgen change with the
regenerated reference, the golden deletion, the baseline retirement with
the live checks, the prose sweep, and this section with the changelog.

**The argument's identity, restored inside the kept artifact.**
`events_docgen.py` gains `_argument()`, which renders a `%` position as
its declared field name followed by its kind (`` `session` (`ID`) ``)
instead of the kind alone. The plan's decision 3 is what this closes:
the Argument cell used to be an `ArgKind` name, so reversing two
same-kinded entries of a variant's `ARGS` moved nothing committed, and
the one-shape implementation doc recorded the golden inventory as the
only committed pin on exactly that. The reference's "How to read it"
paragraph gained the clause that says what the column now holds.
`docs/reference/events.md` was regenerated once for it: 181 lines
changed, all of them argument rows apart from that paragraph, which is
the one-time reader-improving diff the plan priced.

`test_event_docs.py`'s argument row check asserts the whole cell,
building the expected string from the declaration's own `name` rather
than through the generator's helper, which is that file's standing
convention: an assertion built on the generator would be the same string
computed twice.

**The golden, deleted whole.** `tests/unit/test_event_golden.py` and
`tests/unit/data/event-catalog-golden.json` are gone, with the
`uv run python -m tests.unit.test_event_golden` regeneration command.

**The record baseline, retired.** `tests/unit/data/event-baseline.json`
is gone. `tests/tools/event_baseline.py` loses the `__main__`
regeneration block, `rendered()`, `committed()`, the `COMMITTED` path
constant, `captured()`'s `produced=None` branch (its only caller was
that block, so the parameter is required now), `shape()`'s
`argument_types`, and the #210 walk relics that nothing read: `MODULES`,
`PACKAGE`, `LEVEL_METHODS`, `TYPED_METHOD` and `SESSION_RECEIVER`, with
the `json` and `vinga_server` imports they were the last users of. Its
module docstring is rewritten to say what "baseline" means now: a driver
inventory, no committed capture, nothing written to disk and no
regeneration command. Three other passages in it that described a
regeneration run (the two ported-driver notes and `patched()`'s reason
for not using `monkeypatch`) were corrected with it, and the drivers'
temporary directory prefix is `vinga-drivers-` rather than
`vinga-baseline-`. `driven()`, the eighty-one drivers themselves and
`tests/tools/driver_times.py`'s imports are untouched.

**The suite, live.** `tests/unit/test_event_baseline.py` keeps its
module-scoped single drive and goes from eight tests to seven, and back
to eight when the review round's finding 2 added the untyped check. Deleted:
`test_the_capture_is_the_committed_baseline`,
`test_the_committed_file_is_what_the_harness_writes` and
`test_the_baseline_records_shapes_rather_than_values`, whose subject is
gone, and the `REGENERATE` message they carried. Kept: driver-path
uniqueness, driven-path-produces-its-event, every-variant-produced, the
builtin payload values (#252's pin), and
`test_the_store_says_nothing_else`, unchanged with its docstring
corrected since it cited both deleted files. Added:

- `test_every_driven_record_conforms_to_a_declared_variant`. The
  direction the every-variant check does not cover: that one asks
  whether each declaration was produced, this asks whether each record
  was declared. It matches every produced record against the variants of
  its event on channel, level, template equality and the payload key
  range, through the same `matches()` the every-variant check uses. The
  review round's finding 3 widened its population from the eighty-six
  records the drivers keep to every typed record their runs produced,
  264 of them: a session driver crosses a dozen neighbouring paths on
  the way to its own decision, and those records are in no table and
  read by nothing else. That is what gives this check a failure of its
  own, which the kept population could not: any disagreement in a KEPT
  record is also a `CARRIED` row change, since `matched()` answers "no
  declared variant" for exactly the records this test lists.
  `variants_of()` answers `()` for a record naming an event no
  declaration owns, so an undeclared event would be a listed failure
  rather than a `KeyError`; the docstring says plainly that this is
  defensive and unreachable while the drivers filter on `event`.
- `test_no_unlisted_record_rides_a_scoped_channel_untyped` (added by
  the review round's finding 2), holding the run's UNFILTERED records to
  a closed set of six channel-and-sentence pairs: the untyped
  diagnostics that survived #210. `driven()` answers a `Run` now, with
  `kept` per driver and `said` unfiltered, because a claim about what is
  not one of the eighty-one typed paths cannot be made from a mapping
  those paths were selected out of.
- `test_every_driver_produces_the_shape_its_path_declares`, holding the
  capture to `CARRIED`: the per-driver table of decision 2, driver key
  to one row per record, each row the variant the record is an emission
  of and the payload keys it carries, asserted exactly, eighty-one
  drivers and eighty-six records. The table's keys are asserted equal to
  the driver keys first, so a driver added without a row fails rather
  than going uncovered. The variant column is the PR review round's P1
  (finding 1 below): `matches()` tries every variant of a record's
  event, so without it a path that started emitting a same-shaped
  sibling of its own variant passed every check in the file.

Both report channel, level, template, field names and type names only,
never a payload value, per the plan's no-leak lens: the drivers work
from real material with a planted API token among it. The suite's module
docstring closes on the live guarantee and says that rule outright.

**How `CARRIED` was built and checked.** Generated once from the live
capture, then verified two ways before the committed file was deleted.
Mechanically: every one of the 86 rows' key tuple equals the `fields`
list the committed `event-baseline.json` recorded for the same driver,
which is the artifact this replaces, and two consecutive generations
produced identical source. The variant column added for finding 1 was
generated the same way, under an assertion that every one of the 86
records matches EXACTLY one declared variant, which is what makes the
column well defined. By reading, against the declarations rather than
against the capture: `DeviceSession.run #4` is exactly `SessionOpen`'s
required set; `_run_one #1`'s three rows are exactly `BuiltinToolCall`,
`UnnamedToolCall` and `McpToolCall`'s; `CaptureStore.open #1` and `#2`
are `CaptureDirectoryUnusable`'s and `CaptureBelowFloor`'s;
`ws:conversation #2` is `RejectedAtCapacity`'s; and
`_llm_round_done #1`'s two rows are `LlmRound`'s required set plus the
optional fields the registered path fills and the unregistered one does
not, which is the case the table exists for.

**The prose sweep.** Every live site naming a deleted artifact as the
pin, corrected. The two coverage-delegation headers now name what
carries the coverage: `test_event_catalog.py`'s says the declarations
are pinned by the generated reference and held to being produced by the
live suite, and the two pin suites' say the same.

### Deviations from the plan

1. **The prose inventory was four sites short.** Finding 3's
   enumeration named about a dozen; the repo-wide grep found four more
   live statements that the milestone makes false, and all four were
   corrected with the listed ones:
   `src/vinga_server/events/catalog.py:286`,
   `tests/unit/test_event_enum_fields.py:14` and `:147` (each saying an
   unconverted enumeration member would reach "a baseline's argument
   types", which `shape()` no longer records), and
   `tests/unit/test_event_typed_emit.py:22` (which named the committed
   baseline as the owner of the claim that a converted path's record did
   not move). That makes three `src` docstrings rather than the two the
   no-leak lens qualified for, still behavior-free.

2. **Two of the four named mutations cannot go red as stated, and were
   planted where the disagreement they describe can exist.** A record's
   channel, level and template are derived from its variant at emit:
   `_construct()` reads `variant.LEVEL` and `logged.template` straight
   off the declaration. So a `TEMPLATE` or a `LEVEL` edited in the
   catalog moves the record and the declaration together and matches
   itself, whatever it is edited to. Verified rather than argued:
   `SessionOpen.LEVEL` changed from `INFO` to `DEBUG` in the catalog
   leaves all seven tests green. The honest form of both mutations is
   therefore a disagreement planted at the derivation itself, in
   `events/__init__.py`, which is where a real one would come from; both
   are recorded in the table below with what was changed. The
   conformance check's template and level comparisons are load-bearing
   for a second reason the mutations do not reach: they are what tells
   apart the variants of one event that carry identical field sets, so
   `matched()` can say WHICH variant a record is and `CARRIED` can hold
   a path to producing that one. The first version of this paragraph
   claimed the every-variant check caught a site that stopped
   constructing one of three otherwise indistinguishable
   `barge_in_suppressed` variants, which the review round's finding 1
   showed to be false in exactly that example: three drivers produce
   three variants one each, so a swap between two of them leaves all
   three produced. The per-path variant column is what actually catches
   it.

3. **`shape()`'s `argument_types` had one more consumer in prose than
   the plan expected**, which is deviation 1's second and third sites.
   Nothing read it in code.

Nothing else deviated. Decisions 1, 2, 4 and 5 landed as written.

### The successor practice

The last three issues used "the baseline SHA is unchanged" as their
behavior-preservation proof, and this milestone deletes that instrument.
What replaces it: the live suite green plus the `events.md` byte diff.
Where a record-level comparison is genuinely needed, the practice is
capture-twice-in-memory. A scratch test drives `captured(driven())`
before and after the change in the same process, or once on each branch,
and compares the two structures directly. No file is written, nothing is
committed, and there is nothing to regenerate afterwards; the comparison
is thrown away with the scratch test that made it.

### Mutation proofs

Each mutation was applied to a copy-backed file, the suite was run
against it, and the file was restored by copy-back plus `touch` per
`AGENTS.md`, with `git status` checked clean afterwards. Runs are
`uv run pytest tests/unit/test_event_baseline.py -q`, seven tests.

| Mutation | Where | Result |
| --- | --- | --- |
| A template moved off its declaration: `SessionOpen`'s emitted message gains a character while the declaration keeps the original | `events/__init__.py`, `_construct`'s `message=` | RED: conformance and every-variant-produced. The conformance failure names the driver, the event, the channel, the level, the field names and the template, and no value. |
| A level changed off its declaration: `SessionOpen` emitted at `DEBUG` while the declaration says `INFO` | `events/__init__.py`, `_construct`'s `level=` | RED: conformance and every-variant-produced. |
| A record matching no declared variant: `SessionOpen`'s payload gains an undeclared key | `events/__init__.py`, `_construct`'s `payload=` | RED: conformance, every-variant-produced and the carried-key table. |
| A half-quartet `llm_round`: the entry fields dropped from the path that fills them | `events/assembly.py`, `llm_rounded`'s `_entry_fields` call | RED: the carried-key table alone. Conformance stays GREEN, which is the point of the table: `required <= keys <= declared` admits the shrunken record. |
| Control: a level changed in the catalog alone (`SessionOpen.LEVEL` to `DEBUG`) | `events/catalog.py` | GREEN, all seven. The record derives from the declaration, so both sides move together. This is why the first two mutations are planted at the derivation. |

Three more from the PR review round, against the eight-test suite:

| Mutation | Where | Result |
| --- | --- | --- |
| A site constructing its own sibling: `BargeInInRefractory` and `BargeInWithoutTranscript` swapped between the two gate branches that build them, so each of two situations is reported under the other's sentence and the other's `reason` | `runtime/turntaking.py:263,301` | RED: the carried-shape table ALONE, naming both drivers and the variant each now produces. The suite as it stood before the round was entirely green under this mutation, verified rather than argued: the key-only table generated under the swap is byte-identical to the one the PR committed. This is finding 1. |
| One of several producers of a variant broken: `PromptAssembled` gains an undeclared key when the agent is the tutor, whose two prompt assemblies are neighbours of the handover drivers and are kept by nobody | `events/__init__.py`, `_construct`'s `payload=` | RED: conformance ALONE. The carried table cannot see it (the record is in no driver's kept set) and every-variant cannot (the poet's assembly still produces the variant). This is the isolating proof finding 3 asked for. |
| The reviewer's own mutation: `RejectedAgentNotLoaded` gains an undeclared key while its two `session_rejected` siblings keep producing | `events/__init__.py`, `_construct`'s `payload=` | RED: conformance, every-variant-produced and the carried table. Not isolating, because that variant has exactly one driver: breaking its only producer leaves the declaration unproduced. Recorded as run. |

### The repo-wide inventory

`grep -rn "golden\|baseline" .` from the repository root, excluding
`.git` and `vendor/`, before and after.

- Before: 450 lines.
- After: 426 lines, of which 33 are this milestone's own new writing
  (this document and the changelog entry), naming the deleted artifacts
  as history rather than as pins.

Survivors, every one in the plan's expected categories:

- **The two module names and the imports of them.**
  `tests/tools/event_baseline.py` and
  `tests/unit/test_event_baseline.py` keep their names, and their
  docstrings now say what those names mean: a driver inventory and the
  live conformance suite. They are named from
  `tests/unit/test_event_surface_pins.py` (which also imports `Failing`,
  `failing_reply` and `turned_away` from the harness),
  `tests/tools/driver_times.py` and `tests/unit/test_driver_times.py`
  (the timing tool and its own pin),
  `tests/unit/test_event_catalog.py` and
  `tests/unit/test_event_typed_emit.py`.
- **Historical records.** `docs/plans/*` and the older `CHANGELOG.md`
  entries, left as the record of what was true when they were written.
- **The CI wheel-migration comment**
  (`.github/workflows/vinga-server.yml:227`), whose "baseline script"
  is Alembic's.
- **Unrelated word uses.** The database's baseline revision
  (`db/__init__.py`, `test_db_open.py`, `test_conversations_boot.py`,
  the compatibility-floor ADR, `principles.md`), the config diff's
  comparison baseline (`config/diff.py`, `config/api.py`, the OpenAPI
  description), the MCP reload's baseline generation
  (`tools/mcp/registry.py`, `test_mcp_pending.py`), the API problem
  bodies quoted as goldens (`test_config_api_problems.py`,
  `config/store.py`) and the quality regression suite's recorded
  baseline (`docs/conversational-quality-regression-suite.md`).

No survivor names a deleted artifact as a live pin.

### Verification

All from `vinga-server/`.

- `uv run ruff check .`: clean.
- `uv run mypy`: clean (strict over the events package, which is what
  the project's mypy configuration covers).
- `uv run pytest tests/unit -q`: 2797 passed, 20 skipped, in 4m24s.
  `tests/unit/test_event_baseline.py` alone is 7 passed in 19.8 s,
  which is the plan's net seven.
- `uv run pytest tests/integration -q`: 61 passed in 2m57s.
- `uv run vinga-server events reference > ../docs/reference/events.md`,
  run twice after the docgen change: the first run's output is what is
  committed and the second left the file byte-identical. Re-run once
  more at the end of the milestone against the committed file: no
  difference.
- The reference diff is 181 lines changed: every variant's argument
  rows, plus the one paragraph that says what the Argument column now
  holds. No template, field, token, syntax, grammar or note moved.
- The four mutation proofs and the control, in the table above.
- The repo-wide grep, before and after, in the section above.
