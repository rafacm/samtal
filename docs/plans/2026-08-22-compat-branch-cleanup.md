# Retire the obsolete compatibility branches and fix what the audit found

## Goal

Implement issue #225's outcome as recorded in its audit comment of
2026-08-22: delete the two tolerance branches that exist only for
pre-release history no supported deployment can produce, and fix the
four defects the audit surfaced, each as its own commit. The keep
column of the audit (corruption recovery, re-creatable input states,
migration-era column handling) is untouched by design; this plan
changes refusal and recovery paths only where the audit's delete
column and findings list point.

The companion implementation doc,
[`2026-08-22-compat-branch-cleanup-implementation.md`](2026-08-22-compat-branch-cleanup-implementation.md),
records what each milestone actually did, deviations from this plan,
and discoveries; a milestone with no deviations says so explicitly.

## The issue's decisions, restated

Settled by #225, its audit comment, and the session that accepted it;
not re-litigated here:

1. **The yardstick is the 2026-08-20 compatibility-floor ADR read
   under the pre-release stance**: no third-party installations need
   honoring, and our own boards are resettable. The ADR's decisions
   do not change (databases stay supported best-effort from revision
   0001, so the migration-era column branches stay), but the stance
   itself lands in the repository: M1 adds a dated addendum to the
   ADR's Context recording the 2026-08-22 decision and that it is
   what licenses removing pre-release tolerance from refusal paths,
   so the checkout carries the deletions' justification instead of a
   Context that argues the other way while the record lives only in
   an issue comment.
2. **Two branches are deleted**: the single-redirect follow in
   `config doctor` (`config/cli.py`) and the bare-string device
   binding (`_binding_as_list` in `config/models.py`). Each deletion
   is a behavior change to a refusal path and gets its own test bite
   and changelog entry, and the no-leak lens applies to every piece
   of error text touched.
3. **The string-form MCP grant is supported syntax**, not legacy
   debt. No code change; the classification lives in the audit
   comment and needs no restating in code.
4. **The four defect findings are fixed under #225 as four separate
   commits**, not split into their own issues: the diff
   form-sensitivity leak, the `openai_compatible` URL validation gap,
   the conversation store's read-side comment overclaim, and the
   untested version-1 activation poll.
5. **Everything else in the audit's keep column stays byte-identical.**
   A diff in this plan's PRs that touches a keep-column branch is a
   review finding by definition.

## The issue's open questions, resolved

**Does deleting the doctor redirect-follow change what an operator
meets?** Only behind a redirect. After the deletion `_probed` makes
one GET and refuses any redirect, which is what it already did for
every redirect except Starlette's own trailing-slash canonicalization
from a server older than the 2026-08-13 spellings fix. No such server
exists, and a proxy that canonicalizes a missing slash now produces
the standing refusal instead of a silent extra hop, which is the
stricter and simpler contract: every hop past the first was already
"a hop somebody else chose", and now the first is too.

**What refuses a bare-string binding once `_binding_as_list` stops
converting it?** Pydantic, through the `devices` field's own type,
which declares a list, and only on the one route that can still
carry the string form: composing a `Config`/`DomainConfig` from a
raw domain mapping (`loader._composed`; in the suite,
`load_config_from_data`). Every write path is already closed
upstream of the validator and does not change: the API's `_agents`
(`config/api.py:2207`) refuses a non-list body before `bind_device`
is called, the CLI builds a list from argparse, `store._binding`
wraps its arguments in a list, YAML `devices:` is refused whole by
`_check_moved_keys`, and the DB read path refuses a string in
`_list`. The test bite is therefore `load_config_from_data` with
`devices: {mac: "assistant"}` refusing, rendered by
`_format_validation_error` (`loader.py:355`), which reads pydantic's
`msg` only (`Input should be a valid list` against the `devices.
<mac>` location); no write-path bite is available or needed, and the
plan says so rather than promising one. `normalize_device_bindings`
keeps its MAC canonicalization and its duplicate-MAC refusal; the
empty-list and duplicate-agent refusals stay, in whatever home the
deletion leaves natural (the function may collapse into its caller
if the deletion test says it no longer earns its name).

**How does the diff stop being form-sensitive without a second
normalization?** By reusing the one that exists: `as_mcp_grant` in
`config/models.py` is already the normalization point every runtime
consumer goes through. `same_agent` (and the `agent_defaults`
comparison beside it, which holds the same field) compares entries
with their `mcp` lists mapped through `as_mcp_grant`, so a form-only
rewrite compares equal everywhere and a real grant change still shows
in both the agents list and the registry-derived grants list. One
stale record to correct alongside:
`docs/plans/2026-08-20-config-diff-read-implementation.md` still
states the superseded comparison rule ("with the `mcp` field
excluded"), which could steer an implementer into exclusion, and
exclusion would remove real grant edits from `agents.changed`
entirely; the diff commit appends a dated correction note to that
doc pointing at `config/diff.py`'s own comments as current. The
mapping preserves the `None`-vs-`[]` distinction the field's own
semantics carry (`None` inherits `agent_defaults`, `[]` opts out,
and `mcp_for_agent` turns on exactly that): it is `None if
entry.mcp is None else [as_mcp_grant(item) for item in entry.mcp]`,
never `entry.mcp or []`, which would report nothing pending for an
edit from `null` to `[]` while a reload revokes every inherited
tool. The normalization is applied inside `config/diff.py` at the
comparison, not by mutating either snapshot: the diff's inputs stay
the composed worlds as loaded, and locality is kept because the
spelling rule still has exactly one home in `models.py`.

**What does `openai_compatible` do with a malformed `base_url`?**
Exactly what its siblings do: `build` in `providers/openai_llm.py`
calls `parse_base_url` before constructing the provider, so a
`base_url` without a scheme or host is refused at build time (boot or
apply) instead of surfacing as a failed first request with token
counts silently gone. The same commit drops the `got "{base_url}"`
clause from `parse_base_url`'s refusal for all three types that
share it: `base_url` is this repository's known credential-bearing
option under an innocuous name, an operator pasting a key where the
URL goes produces a value with no netloc, which is exactly the case
`url_credential` cannot mask, and echoing it would put the key on
stderr, in the API's 422 body, and in the boot log. The sibling
pins (`test_providers_openai_tts.py:190` and the ASR mirror) match
on the static half of the sentence only, so nothing else moves. A
sentinel bite plants a credential-shaped `base_url`
(`sk-`-prefixed, no scheme) and asserts the refusal renders the
label, the rule, and the example, never the value. `build` discards
`parse_base_url`'s boolean deliberately, unlike the sibling
factories that bind it: the constructor needs `self.host` anyway for
the failure event, `_ask_for_usage` derives from that same host, and
threading the boolean in would be a second encoding of the one
predicate; a one-line comment at the call site says so. After the
`parse_base_url` call, `endpoint_host` cannot return None on the
factory path, though a directly constructed provider with an
injected client still can hold `host = None`, which is that seam's
existing contract and stays. The host equality that drives
`_ask_for_usage` is unchanged.

**Is the conversation store's pre-narrowing claim a comment fix or a
missing read-side strip?** A comment fix. The `EVENT_CONTENT` strip
runs in `_event_row` at write time only; no read path applies it, and
the comments in `conversations/store.py` and `conversations/docgen.py`
claiming "the same rule reads a database written by a server from
before the narrowing" assert a branch that does not exist. The
honest claim is write-time-only defense in depth. A read-side strip
is not added: the events table is counted, never served, by the API,
the narrowing predates every database the pre-release stance honors,
and adding an unreachable branch to make an overclaim true would be
backwards. The docgen change regenerates
`docs/reference/conversations-schema.md` in the same commit, keeping
the drift check green.

**Is the version-1 activation poll a branch to delete or to pin?** To
pin, and the reach half of the pin already exists: the audit's "no
test naming the header" was wrong.
`test_a_version_one_body_is_accepted_as_it_is`
(`test_onboarding_activation.py:360`) already drives the branch with
`Activation-Version: 1`, which per `docs/xiaozhi-notes.md` is what a
board without a burned serial actually sends (the header present
with value 1, not absent; the absent case is separately exercised at
`:342-347`), and the integration onboarding test polls the same way.
What no test asserts is the branch's consequence, and that is the
bite: extend the existing test to assert `ActivationPending` carries
the waiting code and that none of the version-2 refusal events
(`ActivationRefusedUnreadableBody`,
`ActivationRefusedUnknownAlgorithm`,
`ActivationRefusedChallengeMismatch`) fires. The early return in
`_version_two` (`ota/poll.py`) is what lets a stock board activate,
and stock xiaozhi firmware is the compatibility floor promised in
`docs/architecture/principles.md`. No behavior changes.

## Design decisions this plan makes

- **No new modules anywhere.** Every change deepens an existing
  module or deletes from one; the deletion test is applied to
  `_binding_as_list` after its string arm goes, and to
  `_canonical_slash` and `_redirect_refused` after the loop goes
  (`_redirect_refused` survives as the refusal every redirect now
  meets; its text drops the sentence describing the one it used to
  follow).
- **`CANONICAL_REDIRECTS`, `_canonical_slash`, and the follow loop go
  together** in one commit, with the doctor's docstring rewritten to
  state the new contract (one GET, no redirects, ever) and why
  (every device-facing route answers both spellings directly; a
  redirect from that endpoint is by definition somebody else
  answering). The stale prose the same commit rewrites includes
  `_doctor`'s own comment at `cli.py:409-412` ("answered by wherever
  it ended up"), whose `response.url` scheme read simplifies with
  the follow gone.
- **Changelog**: the two deletions are `### Removed` entries; the
  diff fix and the `openai_compatible` refusal are `### Fixed`; the
  conversation-store comment correction gets a one-line `### Fixed`
  too, because the regenerated `conversations-schema.md` is a
  published reference making a different claim and the changelog's
  own practice records documentation corrections. Only the version-1
  pin needs no entry (a test addition, nothing published moves).
- **Generated surfaces move with their source**: if the `devices`
  write schema published in `docs/reference/api-openapi.json` or the
  domain reference carries the string binding form, it is regenerated
  in the binding-deletion commit; the conversations schema reference
  is regenerated with the docgen comment fix.

## The standing review lenses, pre-answered

- **No-leak.** Three pieces of error text move. (1) The doctor's
  redirect refusal keeps its structure: static text plus `shown`,
  which is already either `SUPPLIED_ENDPOINT` or the derived short
  URL, never the supplied address or the `Location` target; the
  sentinel test plants a redirect whose target carries a
  credential-shaped string and asserts the refusal renders neither
  the target nor the supplied URL. (2) The bare-string binding
  refusal is pydantic's, rendered by `_format_validation_error`
  (`loader.py:355`), which reads pydantic's `msg` and the field
  location only, never the input; the test bite asserts the refusal
  names `devices.<mac>` and that a planted credential-shaped binding
  value does not appear in the rendered message. (3)
  `parse_base_url`'s refusal loses its echo of the rejected value in
  the same commit that adds the third caller; the sentinel bite in
  the open-question answer covers the credential-shaped case. Its
  removal is the review's finding 3, decided: the plan is what makes
  the surface newly reachable, so the echo goes rather than gets a
  third caller.
- **Pin before reshaping.** The diff fix adds the failing-then-green
  characterization first: a form-only rewrite (string to equivalent
  object) currently reports the agent under `agents.changed` with an
  empty grants list; the fix flips that pin to "no diff anywhere".
  The pin holding a real grant edit in `agents.changed` is the
  existing `test_a_grants_only_edit_is_the_registry_s_to_report`
  (`test_config_diff.py:251`), byte-unchanged before and after (the
  grants-list half of any new pin proves nothing, since `McpPending`
  is carried through verbatim from the test's own input). A third
  pin holds the `None`-vs-`[]` case: an agent edited from `mcp:
  null` to `mcp: []` still reports under `agents.changed`.
- **Closed sets.** No reason token, event field, or `Applies` value
  is added or removed anywhere in this plan. The version-1 pin
  asserts existing events only.
- **Honest seams.** No injectable dependency changes. The
  `openai_compatible` change is inside its factory; the injected
  client path (`client is not None`) is untouched.
- **Inventories by tooling.** Before the binding-deletion commit, a
  grep inventory of `_binding_as_list` callers and of the string
  binding form across `tests/` and docs goes in the implementation
  doc. The review's own sweep sizes it at roughly fifteen files:
  `tests/support/checkin.py:74` (`unbound_config`, shared with the
  activation suite M2's version-1 commit builds on), seven sites in
  `tests/unit/test_config.py` (`:372`, `:489`, `:501`, `:665`,
  `:725`, `:732`, `:746`, four of which are pins whose test text
  carries the string and rewrites to the list form while their
  behavior claims stay), seven integration tests
  (`test_activation.py:51`, `test_ota_endpoint.py:59`,
  `test_drain.py:51`, `test_access_logs.py:66`,
  `test_device_bindings.py:47`, `test_ws_auth.py:35`,
  `test_two_personas.py:54`), and
  `test_event_descriptor_sanitization.py:355`. That list is the
  starting point and the grep is rerun at commit time, not trusted.
  For the doctor commit, the sweep is the `redirecting` fixture's
  users in `tests/unit/test_config_cli_onboarding.py`, not just
  `CANONICAL_REDIRECTS`/`_canonical_slash` references (finding 7
  names the two tests the symbol sweep misses).

## Module layout

Unchanged. Files touched: `config/cli.py`, `config/models.py`,
`config/store.py` (only if `_binding_as_list`'s collapse reaches the
`DomainConfig` validator wiring), `config/diff.py`,
`providers/openai_llm.py`, `providers/openai_endpoint.py` (docstring
truth only, if at all), `conversations/store.py`,
`conversations/docgen.py`, plus tests, `CHANGELOG.md`, and generated
reference docs.

## Tests

Reuse the existing assets; the new bites are:

- Doctor: every redirect is refused, including the trailing-slash
  shape a pre-2026-08-13 server sent. In
  `tests/unit/test_config_cli_onboarding.py`, swept via the
  `redirecting` fixture's users:
  `test_the_canonical_trailing_slash_redirect_is_followed` inverts
  to refusal; `test_a_second_redirect_is_one_too_many` is deleted
  (with no follow there is no second hop, and its two-request
  assertion has no inverted form);
  `test_a_location_that_cannot_be_read_is_not_a_canonical_slash` is
  deleted with `_canonical_slash` itself (it reaches the function
  directly because the public route cannot). The no-leak sentinel
  above stays.
- Binding: `load_config_from_data` with a string binding is refused
  naming `devices.<mac>` (the deletion's pin, the one route that
  reaches the arm); the API and CLI write paths are asserted
  unchanged by their existing refusal pins; the string-form test
  sites across the suite (the finding-2 inventory) rewrite to the
  list form; MAC canonicalization, duplicate-MAC, duplicate-agent,
  and empty-list refusals keep their existing behavior, with their
  test text updated to the list spelling where it carried a string.
- Diff: the two characterization pins above, in
  `tests/unit/test_config_diff.py`.
- Provider: `openai_compatible` refuses a scheme-less `base_url` at
  build with the shared refusal; a well-formed non-OpenAI URL still
  builds keyless with `_ask_for_usage` off, pinned through public
  behavior (the request sent), not attribute reach-in.
- Activation: the event assertions added to
  `test_a_version_one_body_is_accepted_as_it_is` in
  `tests/unit/test_onboarding_activation.py`, with the header sent
  as `Activation-Version: 1`.

## Risks and mitigations

- **A hidden consumer of the doctor's redirect-follow** (a proxy in
  the field). Mitigated by the pre-release stance being explicit in
  the audit comment, and by the refusal text telling the operator
  exactly what to do (ask the address you meant directly).
- **The string binding form documented somewhere the grep misses.**
  Mitigated by the tooling inventory before the commit and the doc
  drift checks in CI.
- **The diff normalization accidentally widening to
  `agent_defaults`' other fields or masking a real change.**
  Mitigated by the paired pins: form-only rewrite reports nothing,
  real edit reports everywhere it used to.
- **`parse_base_url` in the LLM factory changing boot behavior for a
  deployed config with a malformed URL.** That is the point; the
  changelog entry says so, and the pre-release stance covers the
  blast radius. The write path knowingly stays as it is: an operator
  can still `config set` a scheme-less `base_url`, be acknowledged,
  and meet the refusal at the next boot or apply, an asymmetry
  shared with the `openai` ASR and TTS types and not this plan's to
  fix.

## Milestones

- [x] **[M1: Delete the obsolete tolerances](2026-08-22-compat-branch-cleanup-implementation.md#m1-delete-the-obsolete-tolerances).**
  (PR [#237](https://github.com/rafacm/vinga/pull/237)) Three commits plus changelog: the ADR Context addendum
  recording the pre-release stance, then the doctor redirect-follow
  deletion and the
  bare-string binding deletion, each deletion with its test bite and
  regenerated reference surfaces. Design footprint: deepens
  `config/cli.py`'s probe (its contract shrinks to one GET, no
  redirects) and `config/models.py`'s binding normalization (one
  accepted shape); callers stop having to know that a redirect might
  be followed or that a binding might be a string. No new modules,
  no new seams.
- [x] **[M2: Fix the audit's four defects](2026-08-22-compat-branch-cleanup-implementation.md#m2-fix-the-audits-four-defects).**
  (PR TBD) Four commits, one per
  finding, in the order: diff normalization, `openai_compatible`
  build validation, conversation-store comment correction with
  regenerated schema reference, version-1 activation pin. Plus the
  changelog entries named above. Design footprint: deepens
  `config/diff.py` (spelling-insensitivity becomes the comparison's
  own guarantee) and `providers/openai_llm.py` (its factory takes on
  the URL rule its siblings already hold); no new modules, no new
  seams.

M2 stacks on M1's branch; each milestone is a PR, and every merge
leaves `main` releasable.

## Plan review round

External review: claude backend (codex quota exhausted), claude CLI,
model claude-opus-5, read-only tool set, 2026-08-22, of commit
91f580b. Findings condensed but faithful; resolutions follow each.

**1 (P1). The binding deletion's test bite names a write path that
does not exist and would not exercise the deletion.** There is no
`set device` verb; every write path is closed upstream of the
validator (`config/api.py:2207` `_agents` refuses a non-list before
`bind_device`; the CLI builds a list from argparse; `store._binding`
wraps in a list; YAML `devices:` is refused by `_check_moved_keys`;
the DB read path refuses a string in `_list`). The only route to the
string arm is composing a config from a raw mapping
(`loader._composed`, in tests `load_config_from_data`). The pin is
`load_config_from_data` with `devices: {mac: "assistant"}` refusing,
rendered by `_format_validation_error` (`loader.py:355`), and the
plan must state the API and CLI paths do not change; the no-leak
pre-answer named the wrong rendering path.

*Resolution*: accepted in full. The open-question answer now names
the one reachable route and every closed write path, the test bite
is the `load_config_from_data` refusal rendered by
`_format_validation_error`, and the no-leak pre-answer names that
path and what it reads.

**2 (P2). The string-binding inventory is off by roughly twenty
sites, including a fixture M2's own commit depends on.** The form is
in `tests/support/checkin.py:74` (which the activation suite M2
builds on drives), seven sites in `tests/unit/test_config.py`, seven
integration tests, and `test_event_descriptor_sanitization.py:355`.
Four are pins the plan promised untouched and will start failing
with a `list_type` error instead of the refusal they are about. M1's
binding commit rewrites them to the list form; the commit is roughly
fifteen files, not one.

*Resolution*: accepted. The inventory lens now carries the review's
full site list as the starting point (rerun at commit time), names
the shared `checkin.py` fixture and the four pins whose text
rewrites, and the tests section stops claiming those pins stay
untouched.

**3 (P2). Reusing `parse_base_url`'s refusal extends a credential
echo to a third provider type, and the plan defers rather than
decides.** The refusal ends `got "{base_url}"`. `base_url` is this
repository's known credential-bearing option; an operator pasting a
key where the URL goes (`base_url: sk-proj-...`) has no hostname, so
the new refusal would print the key to stderr, the API's 422 body,
and the boot log, and `url_credential` cannot mask a value with no
netloc. Drop the echo clause from `parse_base_url` in the same
commit (the sibling pins match on the static half only) and add a
sentinel bite planting a credential-shaped `base_url`. Deferring to
review is not available: the plan is what makes the surface newly
reachable.

*Resolution*: accepted; decided as the finding says. The echo clause
goes from `parse_base_url` in the commit that adds the third caller,
the sentinel bite is in the plan, and the no-leak pre-answer states
the decision instead of deferring it.

**4 (P2). The version-1 activation pin already exists, and the shape
the plan proposes pinning is not one a board sends.**
`test_a_version_one_body_is_accepted_as_it_is`
(`test_onboarding_activation.py:360`) already sends `version="1"`
and asserts 202; the integration onboarding test polls with
`Activation-Version: 1`; the absent-header case is exercised at
`:342-347`; and per `docs/xiaozhi-notes.md:214-215` a board sends
the header with value 1, so present-and-1 is the floor, not absent.
What is missing is the event assertion: extend the existing test to
assert `ActivationPending` carries the code and no version-2 refusal
event fires. Drop "no test naming the header".

*Resolution*: accepted; the audit's claim was wrong and the plan now
says so. The bite becomes event assertions on the existing test,
with the header present and equal to 1, which is the shape a board
sends.

**5 (P2). The diff normalization must preserve `None` against `[]`,
or it masks a real change.** `mcp: None` inherits defaults and
`mcp: []` opts out (`mcp_for_agent`); the naive
`entry.mcp or []` spelling collapses them, so an agent edited from
`null` to `[]` would vanish from `agents.changed` while a reload
revokes every tool. The mapping is `None if entry.mcp is None else
[as_mcp_grant(item) for item in entry.mcp]`, identically in
`same_agent` and the `agent_defaults` comparison, and the pin set
gains the `None`-vs-`[]` case.

*Resolution*: accepted; the open-question answer now spells the
mapping with the `None` preservation and the reason, and the pin
lens gains the `None`-vs-`[]` case, noting on the review's own
observation that the real grants-list pin is the existing
grants-only-edit test.

**6 (P2). The pre-release stance is load-bearing for both deletions
but recorded only in an issue comment, and the ADR's Context argues
the other way.** After the deletions land, the checkout contains no
record of their justification and a future session reading the ADR
finds the opposite reasoning. A short dated amendment to the ADR's
Context (migration decisions unchanged) lands in M1 beside the
deletions.

*Resolution*: accepted; M1 gains the ADR addendum as its first
commit, and decision 1 now states what changes in the ADR and what
does not.

**7 (P2). Two doctor tests break or die and the plan's grep finds
only one; a second stale comment is unnamed.**
`test_a_second_redirect_is_one_too_many` (`:847`) asserts two
requests and has no inverted form; `test_a_location_that_cannot_be_
read_is_not_a_canonical_slash` (`:828`) reaches `_canonical_slash`
directly and dies with it;
`test_the_canonical_trailing_slash_redirect_is_followed` is the
inversion. `_doctor`'s own comment (`cli.py:409-412`, "answered by
wherever it ended up") is also stale. Sweep the `redirecting`
fixture's users, not the two symbol names.

*Resolution*: accepted; the tests section names the two deletions
and the one inversion, the doctor commit's rewrite list includes
`_doctor`'s comment, and the inventory lens (finding 2's amendment)
already swapped the symbol sweep for the fixture sweep.

**8 (P3). `build` would call `parse_base_url` and throw away the
answer `__init__` recomputes.** Two structures that must agree in
the module being deepened. Say the answer is deliberately discarded
because `self.host` is needed anyway for the failure event and the
two derivations are the same expression on purpose, or thread it.

*Resolution*: accepted with the deliberate-discard arm; the
open-question answer states it and puts a comment at the call site
so the next reader does not re-ask.

**9 (P3). "Makes its docstring's claim true for all its callers"
overclaims.** A directly constructed provider with an injected
client still gets `self.host = None` for a hostless URL; scope the
sentence to the factory path.

*Resolution*: accepted; the sentence is scoped to the factory path
(rewritten in the finding-8 amendment) and no docstring changes.

**10 (P3). The diff module's governing implementation doc still
states the opposite comparison rule** ("with the `mcp` field
excluded"), which could steer the implementer into exclusion,
removing real grant edits from `agents.changed`. Note the staleness
and whether the diff commit corrects it.

*Resolution*: accepted; the open-question answer notes the stale
line and the diff commit appends a dated correction note to that
implementation doc pointing at `config/diff.py`'s comments as
current, rather than silently rewriting the doc's history.

**11 (P3). The comment correction changes a published reference but
the plan waives a changelog entry.** The regenerated
`conversations-schema.md` makes a different claim; the changelog's
own practice records documentation corrections. Add a one-line
`### Fixed`.

*Resolution*: accepted; the changelog decision now carries the entry
and scopes the waiver to the version-1 pin alone.

**12 (P3). The `openai_compatible` refusal moves to boot but the
write path still accepts the value.** The asymmetry is shared with
the sibling types and not this plan's to fix; say in the risks
section that the write path is knowingly left as it is.

*Resolution*: accepted; the risks section carries the sentence.

On the pre-answered lenses the review confirms: no-leak (1) holds,
(2) held in conclusion but named the wrong path, (3) did not hold;
pin-before-reshape holds except the `None`/`[]` case, noting the
real grants-list pin is `test_a_grants_only_edit_is_the_registry_s_
to_report`; closed sets, honest seams, and the deletion test hold;
the inventory intent holds and its scope did not.

Verdict: ready after the P1 and P2 amendments.
