# Enforce the egress guarantee through one rule

## Goal

Implement issue #136: the declared-egress guarantee behind
`server.local_only` (principles: a fully local deployment is
first-class, enforced not documented) has two independent
implementations and a silent default. `providers/registry.py`'s
`_check_egress` reads the class marking via
`getattr(type(provider), "egress", True)` and the `Provider` base
class itself assigns `egress: ClassVar[bool | None] = True`, so a
provider class that never declares silently counts as egress;
`tools/mcp.py` carries a second `_check_egress` with its own
semantics, messages, and exception type. Make it one deep module
with mandatory explicit declarations: a provider class with no
marking of its own fails at build time in any mode, with a message
naming the class.

The companion implementation doc,
[`2026-08-15-one-egress-rule-implementation.md`](2026-08-15-one-egress-rule-implementation.md),
records what the milestone actually did, deviations from this plan,
and discoveries; a milestone with no deviations says so explicitly.

## The issue's decisions, restated

Settled by issue #136 and not re-litigated here:

1. **One module owns the rule; both the provider registry and the
   MCP build path call it.**
2. **Every provider class must carry an explicit `egress` marking**
   (`True`, `False`, or `None` for config-decided types). A class
   with no marking fails at construction with a message naming the
   class, not only under `local_only`.
3. **MCP entries keep their current per-entry declaration
   semantics; only the enforcement code unifies.**
4. **Refusal messages stay value-free** per the existing
   sanitization conventions.
5. **Existing `local_only` refusal behavior is unchanged for
   declared providers and MCP entries: existing tests pass
   unmodified.** The one deliberate exception is stated below.
6. **CHANGELOG entry.**

Evidence in the issue is pinned to main@8dd1a5f; re-verified at
main@bf4b131 for this plan: the provider check is
registry.py:275-303 (unchanged shape), the MCP check moved to
tools/mcp.py:1249-1267 with its call site in `_managers_for`
(tools/mcp.py:1167-1168), and the silent default lives in BOTH the
`getattr(..., True)` fallback and the base-class assignment
`egress: ClassVar[bool | None] = True` (providers/base.py:72),
which the issue's evidence did not name but which is the same hole:
any `Provider` subclass inherits `True` without ever declaring.
Every shipped provider class already declares explicitly
(AnthropicLlm and ElevenLabsTts `True`; FasterWhisperAsr, PiperTts,
SileroVad and the four mocks `False`; OpenAiCompatibleLlm,
OpenAiAsr and OpenAiTts `None`), so the third acceptance criterion
is already satisfied by the tree and this plan's job is to make it
enforced rather than incidental.

## Decisions this plan makes

### The module is `samtal_server/egress.py`, and it owns sentences as well as logic

A new top-level module, sibling to neither package it serves, so
`providers/` and `tools/` both import downward and no cycle forms.
It holds:

- The resolution rule: a type's class marking is authoritative; the
  entry's `egress` key exists only for types marked `None`;
  declaring the key on a type that knows its own is refused.
- The `local_only` refusals for the three provider outcomes
  (marked or declared egress; `None` with no declaration; the
  config-key conflict, which refuses in any mode as today) and the
  two MCP outcomes (declared egress; no declaration).
- The mandatory-marking check: the built provider's concrete class
  must assign `egress` in its own namespace
  (`vars(type(provider)).get("egress", MISSING)`), not merely
  inherit one, so an undeclared subclass of a marked provider is
  refused rather than silently riding its parent's marking. The
  declared value is then validated by identity: exactly `True`,
  `False`, or `None`, and anything else (`0`, `""`, a property, a
  typo) is refused. Both refusals fire at build time, in any mode,
  with a message naming the class (`type(provider).__name__`) and
  the configured type; class names are code identifiers, not
  configuration values, so the messages stay value-free, and the
  invalid-value refusal names the class without echoing the value.
- Every refusal sentence, verbatim as the two implementations word
  them today. The message texts are pinned by existing tests
  (`test_providers_egress.py` asserts fragments like
  `'"egress: false"'`; `test_tools_mcp.py` likewise), and decision
  5 makes those tests the contract: the module reproduces the
  wording exactly, including the provider messages' "off this
  host" and the MCP messages' "off this network", which is a real
  semantic difference (a stdio command reaches the network, not
  just the host) and not drift to flatten.

### Exception types stay at the call sites

`ProviderError` and `McpConfigError` are part of each surface's
contract and their homes do not move. `egress.py` raises its own
`EgressRefusal(Exception)` carrying only the finished sentence;
`build_provider` wraps it as `ProviderError` and `_managers_for`
as `McpConfigError`, both with the message passed through
untouched. Importing either error type into `egress.py` is
rejected: `McpConfigError` lives in `tools/mcp.py`, and the module
that exists to be below both callers cannot import one of them.

### The base class keeps the annotation and loses the value

`providers/base.py` keeps `egress: ClassVar[bool | None]` as an
annotation with no assignment, so the attribute does not exist at
runtime until a subclass assigns it, and rewrites the docstring
paragraph that currently blesses the default ("The default is
True..."): the new paragraph states that every concrete type
declares its own marking and that building an undeclared type is
refused outright. The abstract stage bases (`VadProvider`,
`AsrProvider`, `LlmProvider`, `TtsProvider`) stay undeclared: they
are never built, and requiring markings on abstract classes would
force meaningless declarations.

The check runs at build time against the concrete class's own
namespace (the review round's finding 1: `getattr` traverses the
MRO, so it cannot tell a declaration from an inheritance), not as
a definition-time `__init_subclass__` hook. A definition-time hook
would fire for every hand-built test double that subclasses a
stage base without caring about egress, and the suite is full of
those by design; the guarantee only needs to hold for providers
the registry builds, which is every provider a running server
holds.

### The one test whose pinned behavior changes

`test_providers_egress.py`'s
`test_a_type_that_forgot_to_declare_counts_as_egress` asserts the
silent default this issue exists to remove (`Forgetful.egress is
True`). It is rewritten, not deleted: the new test builds a
`Forgetful` provider through `build_provider` with `local_only`
off and asserts `ProviderError` naming the class, which is the
second acceptance criterion's coverage. Decision 5's
"existing tests pass unmodified" is scoped by the issue to
declared providers and MCP entries; an undeclared class is the
behavior deliberately changed, and this rewrite is called out here
so the PR review can hold the diff to exactly this one test.
Every other test in `test_providers_egress.py`, and every MCP
egress test in `test_tools_mcp.py`, passes unmodified.

Three additions in the same file, all building through
`build_provider` with `local_only` off:

- The no-runtime-default test: the stage bases and `Provider`
  itself expose no `egress` attribute at runtime, so a future
  "helpful" default cannot come back silently.
- The inherited-marking refusal (review finding 1): an unmarked
  class subclassing a marked concrete provider is refused, naming
  the subclass.
- The invalid-value refusal (review finding 2): a class declaring
  `egress = 0` (a falsey non-bool that presence-only checking
  would wave through as local) is refused, naming the class.

### Call sites shrink to translation

`registry.py`'s `_check_egress` body moves into `egress.py`; the
registry keeps a thin call inside `build_provider` that wraps
`EgressRefusal` in `ProviderError`. `tools/mcp.py`'s
`_check_egress` disappears the same way; `_managers_for` calls the
module under its existing `if config.server.local_only:` guard and
wraps in `McpConfigError`. Both `#30` comment references move with
the logic so the trail from the principles page stays intact.

### No configuration, schema, or operator-docs changes

`ProviderConfig.egress` and `McpServerConfig.egress` keep their
fields, validation, and documentation; `config.example.yaml` and
the READMEs describe operator-visible behavior, which is unchanged
for every declared entry. The new refusal (an unmarked class) can
only be reached by code adding a provider type, so its
documentation is the base-class docstring and this plan, not the
README.

### One milestone, one PR

The diff is one new module, two call sites reduced, one docstring
corrected, one test rewritten plus one added, and the CHANGELOG.
Splitting it would leave a merge where the rule exists in three
places. `main` stays releasable at the merge.

## Files touched

```
samtal-server/samtal_server/egress.py            new: the rule, the sentences, EgressRefusal
samtal-server/samtal_server/providers/base.py    annotation keeps, value goes, docstring rewritten
samtal-server/samtal_server/providers/registry.py  _check_egress replaced by the call
samtal-server/samtal_server/tools/mcp.py         _check_egress replaced by the call
samtal-server/tests/unit/test_providers_egress.py  the one rewrite, the one addition
CHANGELOG.md                                     2026-08-15 entry under Changed
docs/plans/2026-08-15-one-egress-rule.md
docs/plans/2026-08-15-one-egress-rule-implementation.md
```

## Verification

- `uv run ruff check .`, `uv run pytest tests/unit -q`,
  `uv run pytest tests/integration -q`, all from `samtal-server/`.
- `git diff --stat` over `tests/` shows exactly one file touched
  (`test_providers_egress.py`), and its diff shows exactly the one
  rewrite and the three named additions; every other egress and
  local_only test passes unmodified, which is the issue's fourth
  acceptance criterion made checkable.
- The rewritten test demonstrably fails against the old code (the
  silent default builds Forgetful without complaint) and passes
  with the module in place.
- `grep -rn "_check_egress" samtal_server` finds only the new
  module's internals, none in `providers/` or `tools/`.

## Risks and mitigations

- **Message drift while moving sentences.** The refusal texts are
  pinned by fragment assertions, not full-string equality, so a
  reworded sentence could slip past the suite while breaking the
  operator-facing wording. Mitigation: the sentences move by copy,
  the implementation doc diffs old against new refusal text
  verbatim, and decision 5 makes any test edit outside the named
  rewrite a red flag in review.
- **A hidden constructor of providers outside `build_provider`.**
  If any code path builds registry types without `build_provider`,
  the mandatory-marking check would not guard it. Mitigation: the
  subagent verifies at implementation time that `build_provider`
  is the only factory call site (the registry's own docstrings
  claim it), and records the answer in the implementation doc.
- **The missing base value surprises `ClassVar` readers.** Code
  reading `provider.egress` on an arbitrary provider now risks
  `AttributeError` where it silently got `True` before.
  Mitigation: the only readers are the enforcement module (which
  checks presence first) and tests; the subagent greps for other
  readers and records the result.

## Plan review round

One external review of the plan as first committed (a8ec8d9): codex
CLI, model gpt-5.6-sol, read-only against this repository with the
issue #136 body supplied, 2026-08-15. Verdict: ready after the
P1/P2 amendments. Findings as received, condensed; each carries its
resolution once the amendment addressing it lands.

1. **P1: inherited markings bypass the mandatory declaration
   rule.** The plan requires a marking "of its own" but then
   defines absence across the whole MRO and proposes
   `getattr(type(provider), "egress", MISSING)`, which traverses
   the MRO, so an undeclared subclass of a marked concrete
   provider silently inherits its marking; the proposed
   `Forgetful(Provider)` test catches only a missing root default.
   Read only the concrete class namespace
   (`vars(type(provider)).get("egress", MISSING)`), add a
   build-path regression test where an unmarked class inherits
   from a marked provider and is refused with `local_only` off,
   and relax the "exactly one rewrite and one addition"
   constraint accordingly.
   *Resolution*: adopted. The mandatory-marking decision now reads
   the concrete class's own namespace, the tests section gains the
   inherited-marking refusal case, and the tests-diff constraint
   is restated as one rewrite plus the three named additions.
2. **P2: present but invalid markings are neither rejected nor
   tested.** The settled rule permits exactly `True`, `False`, or
   `None`, but presence-only checking lets `egress = 0` or
   `egress = ""` pass `local_only` as local with no static type
   lane to catch it. Validate by identity that the declared value
   is `True`, `False`, or `None`; refuse any other value in every
   mode with a value-free message naming the class; add a
   build-path test for an invalid declaration with `local_only`
   off.
   *Resolution*: adopted. The mandatory-marking decision now
   validates the declared value by identity and refuses anything
   else in any mode, and the tests section gains the invalid-value
   refusal case.

## Milestones

- [ ] **Move both egress checks into one module and make the
  marking mandatory** (PR TBD): `samtal_server/egress.py` lands
  with the resolution rule, the verbatim refusal sentences, the
  own-namespace marking check with identity validation of the
  declared value, and `EgressRefusal`;
  `registry.py` and `tools/mcp.py` shrink to wrapping calls;
  `base.py` keeps the annotation, drops the value, rewrites the
  docstring; `test_a_type_that_forgot_to_declare_counts_as_egress`
  becomes the construction-refusal test, joined by the
  no-runtime-default, inherited-marking and invalid-value tests;
  CHANGELOG entry under Changed, 2026-08-15; the
  implementation doc section written in the change that ticks this
  box. Accept: lint and both lanes green; the tests diff touches
  exactly the named file with exactly the named changes; no
  `_check_egress` left outside the module.
