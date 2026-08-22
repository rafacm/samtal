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
