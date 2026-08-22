# Retire the obsolete compatibility branches: implementation

Companion to
[`2026-08-22-compat-branch-cleanup.md`](2026-08-22-compat-branch-cleanup.md).
One section per milestone, recording what was actually written, the
deviations from the plan, what building it turned up, and the
verification results as they came out. A milestone with no deviations
says so explicitly.

## M1: delete the obsolete tolerances

### What was done

Four commits: the ADR addendum, the two deletions with their test
bites and changelog entries, and this document.

**The stance, recorded.** A dated addendum to the Context of
[`docs/adr/2026-08-20-database-upgrades-have-a-compatibility-floor.md`](../adr/2026-08-20-database-upgrades-have-a-compatibility-floor.md):
the project is pre-release behind a notice nobody reading it can miss,
no beta is declared, no third-party installation is known, and the
maintainer's own boards are resettable, so tolerance for a state only
a pre-release build could produce has no beneficiary and a refusal
path may stop accepting a shape no supported deployment produces. The
Decision is untouched and the addendum says so twice: it does not
reach the databases, whose best-effort floor from revision `0001`
stands, and the licence it grants ends the day a beta is declared.
That is what the two deletions below rest on, and it now rests in the
checkout rather than in an issue comment while the ADR's Context
argued the other way.

**The doctor's redirect-follow.** `CANONICAL_REDIRECTS`,
`_canonical_slash` and the follow loop go together from
`vinga-server/src/vinga_server/config/cli.py`. `_probed` makes one GET
and refuses any redirect through the `_redirect_refused` that already
covered every other shape; its docstring states the new contract (one
request, no redirects) and why (every device-facing route answers both
spellings of its path directly since the 2026-08-13 checkpoint, so a
redirect from that address is something else answering). The refusal's
text drops the sentence describing the one redirect it used to follow
and interpolates `shown` alone, never the supplied URL and never the
`Location`. `_doctor`'s own comment about a URL being "answered by
wherever it ended up" is rewritten: with no hop, the response comes
from the address that was asked, which is what the `response.url`
scheme read now means.

`_redirect_refused` survives the deletion test as the refusal every
redirect now meets; `_canonical_slash` does not survive it at all,
having no callers left.

**The bare-string device binding.** The string arm goes from
`vinga-server/src/vinga_server/config/models.py`:
`normalize_device_bindings` keeps its MAC canonicalization and its
duplicate-MAC refusal, and the per-device helper keeps the empty-list
and duplicate-agent refusals. A binding written as anything but a list
is now left for pydantic to report against the field's own declared
type. The one route that still reaches the field with a raw value is
composing a `Config` or `DomainConfig` from a domain mapping
(`loader._composed`, in the suite `load_config_from_data`), and that
is where the deletion's pin sits: `devices: {mac: "assistant"}` is
refused naming `devices.aa:bb:cc:dd:ee:ff` and pydantic's own
`Input should be a valid list`, rendered by `_format_validation_error`
(`config/loader.py:355`), which reads the location and the message and
never the input. A second bite plants the file's credential-shaped
sentinel as the binding value and asserts the rendered refusal does
not contain it.

The write paths are unchanged and no test was added to them, because
each is closed upstream of the validator and already pinned where it
is closed: the API's `_agents` refuses a non-list body with
`DEVICE_BODY` (`config/api.py:2207`), the CLI builds a list from
argparse, `store._binding` wraps its arguments in a list, a leftover
YAML `devices:` section is refused whole by `_check_moved_keys`, and
the database read refuses a stored string in `_list`.

### The inventories, by tooling

Run from `vinga-server/` before each deletion, and rerun at commit
time rather than trusted from the plan.

**`_binding_as_list` callers.** `grep -rn "_binding_as_list"` over
`src` and `tests`: one call site, `normalize_device_bindings`
(`models.py:1998`), and no test reaching it by name. After the
deletion the same grep over the whole repository outside `vendor/`
finds only this plan's own prose.

**The string binding form.** Two passes, and the second is the one
that matters.

- `grep -rEn '"devices"[[:space:]]*:[[:space:]]*\{...' src tests` plus
  `grep -rn 'devices=' src tests` found 22 bare-string bindings on 21
  lines in 13 files:
  `tests/support/checkin.py:74`; seven in `tests/unit/test_config.py`
  (`:372` the YAML sample of a moved section, `:489`, `:501`, `:665`,
  `:725`, `:732`, `:746`); `tests/unit/test_event_descriptor_
  sanitization.py:355`; `tests/unit/test_session.py:109`;
  `tests/unit/test_ota_tokens.py:78`; three in `tests/unit/test_ota.py`
  (`:132`, `:148`, `:198`); and seven integration tests
  (`test_two_personas.py:54`, `test_ota_endpoint.py:59`,
  `test_ws_auth.py:35`, `test_device_bindings.py:47`,
  `test_activation.py:51`, `test_drain.py:51`,
  `test_access_logs.py:66`; `:732` carries two bindings on one line).
  The plan's starting list held 16 of these: the four in
  `test_ota.py` and `test_ota_tokens.py`, the one in
  `test_session.py`, and the second binding on `:732` were not on it.
- The grep still missed one, and the full unit suite found it:
  `tests/unit/test_ota_tokens.py:26`, a binding whose MAC key is a
  call (`DEVICE_MAC.lower()`) rather than a string literal, which no
  pattern anchored on a quoted key can match. The honest inventory is
  therefore a parse, not a grep: a short `ast` walk over every
  `.py` file outside `.venv`, reporting any dict under a `devices`
  key or a `devices=` keyword argument whose values are string
  literals. Against the finished tree it reports exactly two hits and
  both are intended: the deletion's own refusal pin
  (`test_config.py:696`) and a `"devices": {"applies": "check-in"}`
  response-body assertion in `test_config_api_runtime.py:976`, which
  is a diff label rather than a binding.

Twenty-three bindings across thirteen files in the end, all rewritten
to the list form with their behavior claims unchanged. The four refusal pins
the plan flagged (`:725` invalid MAC, `:732` colliding MACs, `:746`
multiple problems, `:501` unknown agent) keep the refusals they are
about; only their binding spelling moved.

**Generated surfaces.** All four regenerated and diffed against
`../docs/reference/`: `config reference`, `conversations schema`,
`events reference`, `config openapi`. All four identical, so nothing
was regenerated in the commit. The string form was never published:
`docs/reference/domain-config.md` documents `devices` as
`dict[str, list[str]]`, and the OpenAPI document's only `devices`
schema is the diff's `LiveKind`, the device write body being an
`agents` array.

**The doctor's test sweep.** The plan's sweep is the `redirecting`
fixture's users rather than the two symbol names, and the fixture is
not the whole of it either: two tests build their own redirecting app
out of a `FastAPI()` with only the slashed route registered, which is
Starlette issuing the canonical 307 itself. The sweep that finds
everything is running the file, and it is what the deviations below
are drawn from.

### Deviations from the plan

Three, all inside the plan's own delegations rather than against it.

1. **The plan's doctor test list is short by two, and one of them is a
   deletion it does not name.**
   `test_a_slash_an_older_server_would_redirect_still_reaches_it`
   (`:391`) is a second inversion: it asserts a healthy verdict behind
   the framework's own 307, so it inverts to the refusal and is
   renamed `..._is_refused_too`. Its fidelity is worth keeping beside
   the fixture-built one, since the redirect under test is Starlette's
   rather than a hand-written header.
   `test_the_verdict_reads_the_url_the_response_came_from` (`:567`) is
   deleted: its whole premise is that the verdict is decided behind a
   redirect, and with no hop the response comes from the address that
   was asked. What else it covered, that a scheme is compared
   case-insensitively rather than by prefix, is pinned four ways over
   by `test_the_tls_verdict_compares_schemes_rather_than_prefixes`
   and `test_an_upper_case_secure_websocket_url_is_still_healthy`,
   both byte-unchanged.
2. **`_binding_as_list` is renamed rather than collapsed.** The plan
   left its home to the deletion test. Inlined into
   `normalize_device_bindings` it would put a nested loop and two
   raises inside a loop that currently reads as three lines of
   intent, so it stays; but with the conversion gone the name
   describes something it no longer does. It is `_check_binding` now,
   returns nothing, and the caller assigns the value it was handed:
   the rules one binding has to satisfy that its type cannot state.
3. **Nothing was regenerated.** The plan provided for regenerating a
   reference surface publishing the string form in the binding
   commit. None does, which the four drift checks prove, so the commit
   touches no file under `docs/reference/`.

### Discoveries

- **A grep over test sources cannot see a binding whose key is
  computed.** `test_ota_tokens.py:26` cost a full unit run to find
  (six failures, all one helper), and the plan, the review's own
  sweep, and this session's first grep all missed it for the same
  reason. Where a form has to disappear from a suite, the inventory
  is a parse; the `ast` walk above took less time to write than the
  run that found the miss.
- **The `redirecting` fixture is not the whole redirect surface.** Two
  of the four tests that had to move build a canned app whose 307
  comes from the framework, and neither names a redirect symbol nor
  uses the fixture. The general form: a sweep by fixture is a sweep by
  one of the ways a behavior gets exercised.
- **The doctor deletion got no repo-wide symbol sweep, and the binding
  deletion did.** The plan swapped the doctor's symbol sweep for the
  fixture sweep (finding 7), which found the tests but not the prose,
  and the asymmetry cost a review finding. Run afterwards,
  `grep -rn 'CANONICAL_REDIRECTS\|_canonical_slash'` over everything
  outside `vendor/` returns eleven lines, counted before the
  correction note described next was appended: nine in this plan and
  this document, one in
  `docs/plans/2026-08-12-device-onboarding-implementation.md:393`, and
  one in
  `docs/plans/2026-08-19-governance-simplification-implementation.md:2136`.
  No source, no test, no reference document. The lesson is that the
  fixture sweep and the symbol sweep answer different questions: one
  finds what exercises the behavior, the other finds what claims it.
- **The binding's two rules have always had an in-process-only hole.**
  `_check_binding` looks at a `list` and returns for anything else, so
  a sequence pydantic's lax mode coerces to one, a tuple or a set,
  reaches the field having satisfied neither rule. Measured rather
  than reasoned: `devices={mac: ("a", "a")}` composes to
  `["a", "a"]` and `devices={mac: ()}` to `[]`, both of which the
  rules exist to refuse. It predates this milestone and no transport
  can deliver either shape (JSON has no tuple, YAML no set, the
  database stores a JSON array), so it is reachable only by
  constructing the model in-process. Left as it was: widening the
  guard is a new refusal path, which is a decision of its own and not
  one a deletion milestone should slip in. The docstring is narrowed
  to what the code does and names the gap, so the next reader meets it
  as a known one.
- **A standing claim gets a correction note; a dated measurement does
  not.** The two prose hits above are different kinds of record, and
  the criterion that separates them is tense, not topic. The
  onboarding milestone's paragraph says `config doctor` "still follows
  one redirect" in the present, which a reader would take as current,
  so it gets a dated correction note appended pointing at `_probed`'s
  docstring, the way the diff plan's own stale-rule note is handled.
  The governance milestone's line is a row in a before-and-after
  audit table of underscore reach-ins in one suite, a count taken on a
  dated day and true of that day; the test it counts is deleted, and
  amending the table would rewrite a measurement rather than correct a
  claim. It is left as it is.
- **No documentation outside the plans claimed the string form.** The
  `devices` prose in `docs/reference/domain-config.md` gives the type
  as `dict[str, list[str]]`, the OpenAPI description speaks of a
  device's `agents` array, and the example configuration files carry
  no `devices` section at all, the domain half having moved to the
  database. The two mentions of
  `normalize_device_bindings` in
  `docs/plans/2026-08-11-db-domain-config-implementation.md` are about
  where the validator lives, not about what it accepts, so no
  correction note is owed there.

### Verification

Run from `vinga-server/`, at the last code commit of the milestone.

- `uv run ruff check .`: all checks passed.
- `uv run mypy`: success, no issues found in 3 source files. Its scope
  is the events package, which this milestone does not touch.
- `uv run pytest tests/unit -q`: 2,797 passed, 20 skipped, in 5:41.
  (At the doctor commit, before the two new binding bites: 2,795
  passed, 20 skipped. That commit is two fewer than the milestone
  started with: three tests deleted and one added.)
- `uv run pytest tests/integration -q`: 61 passed, in 3:10.
- The four documentation drift checks, regenerated and diffed against
  `../docs/reference/`: `config reference`, `conversations schema`,
  `events reference` and `config openapi` are all identical, and no
  file under `docs/reference/` appears in this milestone's commits.
- The binding inventory's `ast` walk, rerun against the finished
  tree: two hits, both intended (the refusal pin's own input, and a
  diff-label assertion that is not a binding).

One intermediate red is worth recording rather than smoothing over:
the first full unit run after the binding deletion was 6 failed, 2,791
passed, all six in `tests/unit/test_ota_tokens.py` and all from the
one helper the greps had missed. The green figures above are from the
run after that fix.

Not verified here, and not claimed: the container image and the smoke
lane, which no part of this milestone touches; and the doctor's
behavior against a real proxy that canonicalizes a missing trailing
slash, which no test in this repository can stand up. What the change
does there is refuse rather than hop, which is the plan's stated
intent and the changelog entry's stated consequence.

## PR review round, M1 (PR #237)

External review of the PR diff: claude backend (codex quota
exhausted), claude CLI, model claude-opus-5, read-only tool set,
2026-08-22, posted on the PR by the self-posting script. Verdict:
mergeable after the listed fixes; three P2s and four P3s, each fixed
with its own commit before merge.

1. **The `Config.devices` field comment still advertised the deleted
   tolerance** (models.py:2278). Fixed in `a002c2e1`: the comment
   states the list-only contract.
2. **The 2026-08-12 onboarding implementation doc claimed in the
   present tense that doctor follows a redirect**, and the milestone
   never ran the doctor's repo-wide symbol sweep. Fixed in
   `bb63e807`: a dated correction note appended, pointing at
   `_probed`'s docstring and this PR; the 2026-08-19 audit table was
   judged a dated measurement, not a standing claim, and left as
   received, with the tense-not-topic criterion recorded under
   discoveries alongside the sweep result.
3. **The credential sentinel pinned the message but not the
   exception chain.** Fixed in `825e32af`: `__cause__` and
   `__context__` are asserted `None`, matching the suite's chain
   pins.
4. **A two-personas comment described the deleted form.** Fixed in
   `4f04562d`.
5. **The doctor commit's test arithmetic said net zero for a net
   minus two.** Fixed in `7f438546`.
6. **The changelog overclaimed the refusal's silence** (the derived
   short URL is printed deliberately in the no-argument form). Fixed
   in `d5dea79d`: the clause scoped to a URL an operator supplied.
7. **`_check_binding`'s docstring promised rules its list guard does
   not enforce for a tuple or set** (pre-existing, in-process only).
   Fixed in `6577162b`: the docstring narrowed; the hole measured
   and recorded under discoveries rather than widened away.

## M2: fix the audit's four defects

### What was done

Five commits: the four defects in the plan's order, each with its
changelog entry where one is owed, and this document.

**The diff reads a grant, not its spelling.**
`vinga-server/src/vinga_server/config/diff.py` gains `_same_layer`, the
comparison both `same_agent` and the `agent_defaults` singleton now go
through, and `_grants`, which is the mapping the plan spells:
`None if layer.mcp is None else [as_mcp_grant(item) for item in
layer.mcp]`. `AgentConfig` subclasses `AgentDefaults`, so one function
covers both layers and the two comparisons cannot come apart. Nothing
is normalized into either snapshot: the diff's inputs stay the composed
worlds as they were loaded, and the spelling rule keeps its one home in
`models.py`, which this reads. The module docstring gains the paragraph
saying that an agent layer is where model equality read literally is
not the question, since the file's opening claim was that equality is
model inequality plus a fingerprint.

Three pins in `tests/unit/test_config_diff.py`: a form-only rewrite of
an agent's own grant reports nothing the diff computes, the same
rewrite in `agent_defaults` reports nothing, and an agent edited from
no `mcp` list to an empty one is still reported under `agents.changed`.
The grants half of the first pin is worth nothing on its own and the
plan says so: the grants list is `McpPending`'s, handed in by the case
and carried through verbatim, so a pin on it holds the carrying and not
the comparison. What the registry answers is `test_mcp_pending.py`, and
widening it is not this milestone's.
`test_a_grants_only_edit_is_the_registry_s_to_report` is byte-unchanged,
which is what holds the other direction.

`docs/plans/2026-08-20-config-diff-read-implementation.md` gains a
`## Corrections` section dated 2026-08-22 saying its "with the `mcp`
field excluded" line describes a superseded rule, why reading it as a
specification would be worse than reading nothing, and that
`config/diff.py`'s own docstring and comments are current. The history
above it is left as written.

**The `openai_compatible` factory refuses a malformed `base_url`.**
`build` in `providers/openai_llm.py` calls `parse_base_url(label,
base_url)` before constructing, with the seven-line comment saying the
boolean is discarded deliberately: the constructor derives the host it
needs for the failure event from the same URL, `_ask_for_usage` derives
from that host, and threading the answer through would encode one
predicate twice. The refusal in `providers/openai_endpoint.py` loses
its `; got "{base_url}"` clause in the same commit, and its docstring
gains the reason (a key pasted where the URL goes has no netloc, which
is this refusal's own case and the one shape `url_credential` cannot
mask).

Three bites in `tests/unit/test_providers_llm.py`: the scheme-less,
hostless and host-without-scheme URLs refused at build with the shared
sentence and the entry's own name; a credential-shaped `base_url`
whose value is asserted absent from the refusal; and the usage bite,
which is in two halves because a deployment's client is built inside
the provider and handed to nobody. A factory-built entry is asserted to
hold the host its own `base_url` names, for a local endpoint and for
OpenAI's, and a round driven through an injected client for the same
two hosts asserts `stream_options` is on one request and not the other.
The halves meet at the host, which is the whole of what the decision is
taken on, so a factory that passed the default URL rather than the
entry's fails the first half.

**The events strip is described as what it is.** Three sentences
corrected, in `conversations/store.py` (the module docstring and the
comment over `EVENT_CONTENT`) and `conversations/docgen.py`. Each
claimed the strip is "the rule that reads a database written before the
narrowing"; it runs in `_event_row` at write time and no read applies
it. The corrected claim is write-time-only defense in depth, with the
two supporting facts stated where they are load-bearing: the strip is
applied where a row is built, and the API counts a session's events and
never returns their fields. `docs/reference/conversations-schema.md` is
regenerated in the same commit, which is the only generated surface
this milestone moves.

**The version-1 activation poll's consequence is asserted.**
`test_a_version_one_body_is_accepted_as_it_is` keeps its request and
gains the two assertions: `activation_pending` carries the code the
board is showing its owner, and no `activation_refused` record was
written. Read through `tests/support/events.py`, the suite's shared
capture idiom, imported with the `emitted` alias the modules that
called it that already use. No behavior change and no changelog entry.

### The inventories, by tooling

Run from `vinga-server/`, at commit time.

- **The refusal's other callers.** `grep -rn "parse_base_url" src
  tests`: three factories call it (`openai_tts.py:194`,
  `openai_asr.py:425`, and now `openai_llm.py:276`) and no test names
  it. `grep -rn 'must be a URL' src tests ../docs`: the one source
  sentence and the two sibling pins
  (`test_providers_openai_tts.py:190`, `test_providers_openai_asr.py:265`),
  both matching `'"base_url" must be a URL'` alone, so dropping the
  echo moved neither. No committed document quotes the sentence.
- **The strip's claim.** `grep -rn "narrowing" src`: six hits, three of
  them the claim (store's docstring, store's `EVENT_CONTENT` comment,
  docgen) and three ordinary uses of the word. `grep -rn
  "EVENT_CONTENT" src tests`: the definition, one use in `_event_row`,
  the two prose sites, and one test reference, which is the whole of
  the surface and confirms there is no read-side arm to correct.
- **Generated surfaces.** All four regenerated and diffed:
  `conversations schema` moved with the docgen fix and is committed
  with it; `config reference`, `events reference` and `config openapi`
  are identical.

### Deviations from the plan

Five. The first four are additions inside what the plan delegates
rather than departures from it; the fifth was a departure and is
corrected.

1. **A fourth diff pin.** The plan names three; the second comparison
   the fix changes is `agent_defaults`, and a change to a comparison
   with no case of its own is a change nothing holds. The added pin is
   the same form-only rewrite one layer down.
2. **A `stream_options` pin that did not exist.** The plan asks for the
   keyless build to be pinned through public behavior rather than
   attribute reach-in. Nothing in the suite asserted the request shape
   at all (`grep -rn "stream_options\|_ask_for_usage" tests` found
   nothing before this commit), so the bite drives a round against both
   a local endpoint and OpenAI's own: absence alone would pass for a
   provider that never asks anybody. The factory half was added in the
   review round, since a round through an injected client says nothing
   about which URL the factory handed the provider.
3. **Two docstrings in `openai_endpoint.py`.** The plan allows
   "docstring truth only, if at all". The module docstring said the LLM
   stage takes only the host from the shared module, which stopped
   being true, and `endpoint_host`'s said a built provider never has a
   hostless URL, which review finding 9 scoped to the factory path. Both
   now say what holds.
4. **A third site for the strip's claim.** The plan names
   `conversations/store.py` around lines 120-140 and
   `conversations/docgen.py`. The store's module docstring carries the
   same claim at line 55 ("what keeps it correct for a database written
   by an older server") and is corrected with the other two.
5. **Two changelog entries landed under the wrong heading, and were
   moved.** The plan settles `### Fixed` for all three of this
   milestone's entries (its changelog decision, and the review's
   finding 11 for the documentation correction). The
   `openai_compatible` refusal and the conversation-store correction
   were written under `### Changed` instead, and the store entry was
   written long where the plan decided one line. Both are under
   `### Fixed` now and the store entry is the one-liner, which is what
   the rest of this section describes.

### Discoveries

- **The characterization test earns its name.** Against the unfixed
  comparison, both form-only pins fail (the agent one reporting the
  agent under `agents.changed`, the layer one reporting
  `agent_defaults.changed` true) and the `None`-against-`[]` pin passes,
  which is exactly what it is for: it holds the wrong fix rather than
  the current behavior. The check was run by copying the file aside,
  writing `HEAD`'s version over it, running, and copying back, which is
  the restore procedure AGENTS.md gives, `touch` included.
- **The provider bites fail the same way.** Against the unfixed factory
  all four items fail, the credential one because nothing is raised at
  all: a hostless `base_url` built a provider, and only its first
  request would have found out.
- **Three refusal variants, one event name.** The version-2 refusals
  are one declared event (`activation_refused`) with a reason token
  distinguishing them, so "none of the three fired" is a single
  assertion on an empty record list rather than three. `ActivationPending`
  is a DEBUG event on the `vinga_server.ota` channel, so the capture
  names both the level and the logger.
- **`model_copy(update=...)` is the cheap way to compare two models
  under one substituted field.** It keeps the class, so pydantic's own
  equality does the rest, and no field of the entry has to be
  enumerated here: a field added to an agent layer tomorrow is compared
  without this module hearing about it.

### Verification

Run from `vinga-server/`, at the last code commit of the milestone.

- `uv run ruff check .`: all checks passed.
- `uv run mypy`: success, no issues found in 3 source files. Its scope
  is the events package, which this milestone does not touch.
- `uv run pytest tests/unit -q`: 2,805 passed, 20 skipped, in 5:38.
  Eight more items than M1's 2,797: three diff pins, three parametrized
  URL refusals, the credential sentinel and the request-shape bite. The
  activation and conversation commits add no items.
- `uv run pytest tests/integration -q`: 61 passed, in 3:11.
- The four documentation drift checks, regenerated and diffed against
  `../docs/reference/`: `config reference`, `events reference` and
  `config openapi` identical; `conversations schema` identical after
  the regeneration committed with the docgen fix.

Not verified here, and not claimed: the container image and the smoke
lane, which no part of this milestone touches; and the
`openai_compatible` refusal against a real deployment carrying a
malformed `base_url`, which is a boot this repository cannot stand up.
What it does there is fail the boot instead of every conversation,
which is the plan's stated intent and the changelog entry's stated
consequence.

## PR review round, M2 (PR #250)

External review of the PR diff: claude backend (codex quota
exhausted), claude CLI, model claude-opus-5, read-only tool set,
2026-08-22, posted on the PR by the self-posting script. Verdict:
mergeable after fix 1; findings 2 through 5 taken in the same round.
Each fixed with its own commit.

1. **Two changelog entries under `### Changed` against the plan's
   settled `### Fixed`, unrecorded.** Fixed in `0141d2ce`: both
   moved, the store entry cut to the plan's one-liner, and the
   deviations section now records the miss.
2. **The request-shape pin never exercised the factory path.** Fixed
   in `d186e47f` by assertion rather than client swap: a
   factory-built entry's public `host` is pinned to the host its own
   `base_url` names, which is the whole input `_ask_for_usage`
   derives from; the reviewer's hypothetical (`build` passing
   `DEFAULT_BASE_URL`) was run as a mutation and fails the pin. A
   client swap was rejected because reading a factory-built
   provider's requests needs an underscore reach-in.
3. **The credential sentinel asserted only absence.** Fixed in
   `6d360165`: the label, the rule, and the example are asserted
   present, so the whole planned bite is pinned.
4. **"Reports nothing anywhere" overclaimed a vacuous grants
   assertion.** Fixed in `bc9ab9b4`: the doc claims what the pin
   shows (nothing the diff computes), with the registry's own suite
   named for the other half.
5. **A ragged mid-paragraph line from the docstring edit.** Fixed in
   `ad421abb`, comment-only, drift check re-verified.

A figure to correct in passing: the M2 verification block above says
2,805 unit items, which was true at the pre-rebase HEAD; after the
rebase onto merged main (which brought #236's pin into history) the
tree gives 2,806, and the five fix commits add no test items.
