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
   honoring, our own boards are resettable, and the ADR itself does
   not change (databases stay supported best-effort from revision
   0001, so the migration-era column branches stay).
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
which declares a list. The refusal must name the field and must not
echo arbitrary input beyond what the existing config-write refusal
rendering already permits; the test bite pins the refusal's shape on
the write path (a `set device` body carrying `agents: sam`), not just
direct model construction. `normalize_device_bindings` keeps its MAC
canonicalization and its duplicate-MAC refusal; the empty-list and
duplicate-agent refusals stay, in whatever home the deletion leaves
natural (the function may collapse into its caller if the deletion
test says it no longer earns its name).

**How does the diff stop being form-sensitive without a second
normalization?** By reusing the one that exists: `as_mcp_grant` in
`config/models.py` is already the normalization point every runtime
consumer goes through. `same_agent` (and the `agent_defaults`
comparison beside it, which holds the same field) compares entries
with their `mcp` lists mapped through `as_mcp_grant`, so a form-only
rewrite compares equal everywhere and a real grant change still shows
in both the agents list and the registry-derived grants list. The
normalization is applied inside `config/diff.py` at the comparison,
not by mutating either snapshot: the diff's inputs stay the composed
worlds as loaded, and locality is kept because the spelling rule
still has exactly one home in `models.py`.

**What does `openai_compatible` do with a malformed `base_url`?**
Exactly what its siblings do: `build` in `providers/openai_llm.py`
calls `parse_base_url` before constructing the provider, so a
`base_url` without a scheme or host is refused at build time (boot or
apply) instead of surfacing as a failed first request with token
counts silently gone. `parse_base_url`'s refusal text is reused
unchanged, including its echo of the rejected value; that echo is a
pre-existing surface shared with the `openai` ASR and TTS types, and
whether any of the three should echo is flagged to the external
review rather than redecided here. After the call, `endpoint_host`
can no longer return None on this path, which makes its docstring's
claim true for all its callers. The host equality that drives
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
pin. The early return in `_version_two` (`ota/poll.py`) is what lets
a stock board polling without the version-2 header activate at all,
and stock xiaozhi firmware is the compatibility floor promised in
`docs/architecture/principles.md`. The fix is the test #225 requires
for every kept branch: a version-1 poll (no `Activation-Version`
header) reaches the pending path, emits `ActivationPending` with its
code, and produces none of the version-2 refusal events. No behavior
changes.

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
  answering).
- **Changelog**: the two deletions are `### Removed` entries; the
  diff fix and the `openai_compatible` refusal are `### Fixed`; the
  comment correction and the version-1 pin need no changelog entry
  (no observable behavior moves).
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
  refusal is pydantic's, rendered through the existing config-write
  refusal path; the test bite asserts the refusal names
  `devices`/the MAC's field and does not echo a planted
  credential-shaped binding value beyond that path's existing
  contract. (3) `parse_base_url`'s refusal is reused unchanged; its
  echo is pre-existing, shared with two sibling types, and named to
  the review as an open question rather than silently extended or
  silently fixed.
- **Pin before reshaping.** The diff fix adds the failing-then-green
  characterization first: a form-only rewrite (string to equivalent
  object) currently reports the agent under `agents.changed` with an
  empty grants list; the fix flips that pin to "no diff anywhere",
  and a second pin holds a real grant edit reporting in both lists,
  byte-unchanged before and after.
- **Closed sets.** No reason token, event field, or `Applies` value
  is added or removed anywhere in this plan. The version-1 pin
  asserts existing events only.
- **Honest seams.** No injectable dependency changes. The
  `openai_compatible` change is inside its factory; the injected
  client path (`client is not None`) is untouched.
- **Inventories by tooling.** Before the binding-deletion commit, a
  grep inventory of `_binding_as_list` callers and of the string form
  in tests and docs (`tests/integration/test_two_personas.py:53` is
  known; the sweep is rerun, not trusted) goes in the implementation
  doc. Same for `CANONICAL_REDIRECTS`/`_canonical_slash` references
  before the doctor commit.

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
  shape a pre-2026-08-13 server sent (the existing "older server"
  tests in `tests/unit/test_config_cli_onboarding.py` invert from
  followed to refused rather than being deleted); the no-leak
  sentinel above.
- Binding: a `set device` write carrying `agents: sam` is refused
  naming the field; the two-personas integration test binds with
  lists in both places; MAC canonicalization, duplicate-MAC,
  duplicate-agent, and empty-list refusals keep their existing pins.
- Diff: the two characterization pins above, in
  `tests/unit/test_config_diff.py`.
- Provider: `openai_compatible` refuses a scheme-less `base_url` at
  build with the shared refusal; a well-formed non-OpenAI URL still
  builds keyless with `_ask_for_usage` off, pinned through public
  behavior (the request sent), not attribute reach-in.
- Activation: the version-1 poll pin in
  `tests/unit/test_onboarding_activation.py`.

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
  blast radius.

## Milestones

- [ ] **M1: Delete the obsolete tolerances.** Two commits plus
  changelog: the doctor redirect-follow deletion and the bare-string
  binding deletion, each with its test bite and regenerated
  reference surfaces. Design footprint: deepens `config/cli.py`'s
  probe (its contract shrinks to one GET, no redirects) and
  `config/models.py`'s binding normalization (one accepted shape);
  callers stop having to know that a redirect might be followed or
  that a binding might be a string. No new modules, no new seams.
- [ ] **M2: Fix the audit's four defects.** Four commits, one per
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
