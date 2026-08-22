# Type event fields with their StrEnums and delete the TokenValue wrappers

## Goal

Implement issue #238, the opener of the events phase of #246. Every
closed-set event value currently exists twice in
`vinga-server/src/vinga_server/events/values.py`: a `StrEnum`
declaring the set and a frozen `*Token` dataclass holding a plain
`str` checked for membership at construction. This plan types variant
fields directly with the StrEnums, keeps the narrowing capability the
three narrowed wrappers provide, teaches the docgen to introspect an
enum field, and deletes `TokenValue` and its subclasses, roughly 25
names. The reader-facing reference (`docs/reference/events.md`) and
the committed event baseline do not change by a byte; the golden
inventory changes only in its recorded type names and a new
`tokens` key that keeps the narrowed sets structurally pinned.

The companion implementation doc,
[`2026-08-22-typed-event-enums-implementation.md`](2026-08-22-typed-event-enums-implementation.md),
records what each milestone actually did, deviations from this plan,
and discoveries; a milestone with no deviations says so explicitly.

## The issue's decisions, restated

Settled by #238 and not re-litigated here:

1. **Event variant fields are typed directly with the StrEnums**, and
   the `TokenValue` subclasses are deleted. The wrapper turned a
   static guarantee into a runtime check and added about 25 names to
   the vocabulary a reader must hold.
2. **`events_docgen.py` introspects a StrEnum field** (kind, token
   set) instead of reading wrapper metadata.
3. **The `MEMBERS` narrowing capability stays**, for the rare variant
   that admits fewer tokens than its enumeration, as a smaller enum
   or a per-variant annotation. Which of the two is this plan's to
   decide (resolved below).
4. **The generated reference document is unchanged for readers.**
5. **An invalid token becomes a mypy error at the emit site** instead
   of a runtime refusal, where mypy looks (see the honest scope note
   under design decisions), with the emitter's `verify()` remaining
   the runtime backstop everywhere.

## The issue's open questions, resolved

**How does the narrowing survive the wrapper's deletion: a smaller
enum or a per-variant annotation?** As `Literal` annotations over the
parent enum's members, exported from `values.py` as named type
aliases keeping the wrappers' names:

```python
UnnamedToolSource = Literal[ToolSource.DEVICE, ToolSource.UNKNOWN]
PendingRefusal = Literal[NotOffered.PENDING_FULL, NotOffered.MINT_SPENT]
McpConnectFailure = Literal[
    McpDown.TRANSPORT_FAILED,
    McpDown.INITIALIZE_FAILED,
    McpDown.DISCOVERY_FAILED,
    McpDown.CONNECT_TIMEOUT,
]
```

Spelled as plain assignments rather than PEP 695 `type` statements,
because the catalog reads annotations with `get_type_hints`, which
resolves a plain alias to its `Literal` and would hand a `type`
statement's alias back wrapped in `TypeAliasType`, forcing an unwrap
that buys nothing here.

Reasons, against the smaller-enum alternative:

- A smaller enum restates its members' values beside the parent's,
  and two structures that must agree are one structure with a bug
  pending. A `Literal` names the parent's members, so there is
  nothing to drift.
- mypy checks a `Literal` of enum members statically wherever mypy
  looks (which today is the events package alone; the honest scope
  is stated under design decision 5), and the alias documents the
  narrowing in one greppable name either way.
- The members remain members of the parent enum, so nothing about
  the enumerations themselves moves.

The three classifiers feeding the narrowed fields hold plain
strings, deliberately: `runtime/turns.py` spells its source
constants locally so classifying a call does not import the event
vocabulary (and holds them equal to the `tool_invocations.source`
column), `onboarding/pending.py` builds its two refusal sentences
as f-strings over configured bounds, and
`tools/mcp/transport.py`'s `_down_reason(...)` returns its own six
constants. Those modules are NOT migrated by this plan: retyping
them would drag the store column, the bound interpolation, and the
manager's phase plumbing into an issue about deleting wrapper
classes. Instead the emit site converts explicitly, as the adapter
where the classifier's local vocabulary crosses into the event
vocabulary: `ToolSource(classified.source)` in
`runtime/pipeline.py`, `NotOffered(unbound.refusal)` in
`ota/reply.py`, `McpDown(_down_reason(...))` at the construction
in `tools/mcp/manager.py`. The narrowing is therefore declared in
the catalog annotation and held by `verify()` at emit time, not
statically at these sites; a lookup of a string outside the
enumeration raises `ValueError` inside the emit guard's builder,
which `_construct`'s blanket catch turns into the same
`construction_failed` refusal today's `EventValueError` becomes.
The cross-module agreement between each classifier's constants and
its enum is pinned by the drift-guard tests kept under the Tests
section.

## Design decisions this plan makes

1. **`Declared` carries the token facts; the docgen stops reaching
   into the type.** `_read` in `events/catalog.py` learns to accept a
   field whose carried type is a `StrEnum` subclass or a `Literal`
   over one enum's members. For those it stores the enum class as
   `Declared.type` and computes a `tokens: frozenset[str] | None`
   field on `Declared` (all members, or the `Literal`'s subset).
   `tokens_of()` keeps its signature and reads `Declared` (with the
   fixed-member narrowing it already applies). The docgen's kind and
   constraint cells for token fields read `Declared` through small
   accessors instead of `type.KIND`/`type.ARG_KIND`/`type.TOKENS`;
   non-token value types keep their attribute-based path unchanged.
   This deepens the catalog: a variant author's interface shrinks
   from "find or create the wrapper for this enum" to "annotate the
   field with the enum". Two concrete edits this implies, named so
   they are not discovered mid-milestone: `Declared.type` widens
   from `type[EventValue]` to `type[EventValue] | type[StrEnum]`
   (and mypy strict ripples that union through `verify()`,
   `_check`, and everything else reading `Declared.type`), and the
   docgen's `_variant_section` and `_arg_constraint` read
   `declared.type.ARG_KIND`, `.KIND`, and `.GRAMMAR` eagerly
   before any branch, so an enum-typed field would raise
   `AttributeError` regardless of a token branch added later; both
   routes go through the `Declared` accessors first.
2. **Rendering converts the member to a builtin `str` in exactly one
   place each.** `Variant.payload()` and `Variant.logged()` branch on
   `isinstance(held, StrEnum)` and carry `str(held)`. The reason is
   the one `TokenValue.carried()` documented: a record carrying the
   member itself would put the enum subclass into a baseline's
   argument types and its `repr` into anything that renders it. A pin
   asserts the carried and rendered values are `type(...) is str`.
3. **`verify()` holds the narrowing at emit time.** For an enum
   field: `isinstance(held, declared.type)`, plus membership in
   `declared.tokens` where the annotation narrowed it. The refusal
   text keeps naming only the variant, the field, and the declared
   type, never what was held.
4. **`value(fixed=...)` and `Declared.fixed` widen** to admit a
   StrEnum member, so `value(fixed=Rejection.BAD_DEVICE_ID)` replaces
   `value(fixed=RejectionToken(Rejection.BAD_DEVICE_ID))`.
5. **The mypy scope does not change, and the static gain is stated
   plainly.** Strict mypy runs over `src/vinga_server/events` only
   (pyproject `[tool.mypy]`), so the static gain of this change is
   the declarations' internal consistency inside that package and
   nothing more. Every emit site sits outside the scope, so no site
   gets a mypy error from a bad token; and `value()` is annotated
   `-> Any`, so a `fixed=` member is invisible to mypy even inside
   the scope (it is checked at import instead, per design decision
   8). `verify()` inside the emit guard remains the enforcement
   everywhere, exactly as it is today. The `Literal` aliases are
   chosen for the no-drift reason alone, not for site-level
   checking. Widening mypy to the whole tree is its own decision
   with its own cost and is not smuggled into this issue.
6. **`Kind.TOKEN` and `ArgKind.TOKEN` stay.** The reference's kind
   vocabulary is reader-facing and unchanged.
7. **The pins move deliberately or not at all.**
   `docs/reference/events.md` and the committed event baseline
   (`tests/unit/test_event_baseline.py`'s harness output) must be
   byte-identical before and after; the golden inventory
   (`tests/unit/data/event-catalog-golden.json`) is regenerated with
   its own script and changes in two deliberate ways: the `"type"`
   strings (wrapper name to enum name; a narrowed field records the
   parent enum) and a new `"tokens"` key on token fields holding
   the declared set as a sorted list. The second exists because the
   wrapper names carried the narrowing into the golden and the
   parent enum names do not; without it the reference's token
   column would become the sole committed pin on the three
   narrowed sets, and the golden was built to complement exactly
   that kind of reader-facing pin with a structural one.
8. **`_check` refuses a bad `fixed=` member at import.** Today the
   wrapper's constructor runs while the catalog module imports, so
   a fixed value outside its set raises before any lane starts, the
   property the catalog advertises ("a lane, a REPL and a server
   all refuse the same catalog"). A bare member is inert data, so
   `_check` takes over that duty for enum-typed fields: `fixed` is
   an instance of `Declared.type` and, where the annotation
   narrows, a member of `Declared.tokens`, refused with
   `CatalogError` at declaration exactly like the other by-eye
   checks. Without this, the first evidence of a mismatch would be
   a detail-free `construction_failed` at emit, in a running
   deployment on forgiving mode, naming nothing.

## The standing review lenses, pre-answered

- **No-leak.** No new surface. The one refusal path that disappears
  is `TokenValue.__post_init__`, whose message already named only the
  class; `verify()`'s refusals, which replace it at emit time, name
  the variant, field, and declared type, all this module's own words.
  The existing enforcement sentinels keep proving no held value
  reaches a refusal.
- **Pin before reshaping.** The surface is already pinned three ways
  and this plan keeps two of them byte-still: the CI diff of
  `events.md` and the baseline test's byte comparison of committed
  records (`record.msg` and typed args). Both are asserted locally
  before the PR and by CI. The golden inventory is the one pin that
  moves, regenerated by `uv run python -m tests.unit.test_event_golden`
  and reviewed as a loud diff.
- **Closed sets mapped to decision sites.** The three narrowed
  aliases move to their decision sites' own signatures, so the
  narrowing is stated where the code classifies, and the catalog
  reads the same alias. No token is ever chosen from message text;
  nothing in that discipline changes.
- **Honest seams.** No injectable or default-construction policy
  changes.
- **Inventories by tooling.** The migration inventory is the union
  `grep -rnE "Token\(|UnnamedToolSource\(|PendingRefusal\(|McpConnectFailure\(" src tests`
  from `vinga-server/` (65 `Token(` hits plus the narrowed-class
  constructions at `runtime/pipeline.py:270`, `ota/reply.py:337`,
  `tools/mcp/manager.py:622` and their tests, at main `9366dbd4`),
  refreshed after any rebase. The wrapper deletion is verified by
  the same grep over `src` returning nothing (the narrowed names
  survive only as `Literal` aliases, which are never called), plus
  `grep -rn "TokenValue" src tests` returning nothing.

## Module layout

- `events/values.py`: the StrEnums stay where they are; `TokenValue`
  and its 22 subclasses (including the three narrowed ones) are
  deleted; the three `Literal` aliases are declared beside their
  enums; `__all__` drops the wrapper names and gains the aliases.
- `events/catalog.py`: `_read`, `Declared`, `verify`, `payload`,
  `logged`, `tokens_of`, and `_check` learn enum-typed fields as
  described; declarations retype their token fields.
- `events_docgen.py`: token kind and constraint cells read `Declared`
  accessors; everything else untouched.
- Emit sites, in two distinct kinds. Sites that already hold an
  enum member and only unwrap the wrapper (one-line changes):
  `capture.py` (`_disable(self, doing: CaptureWrite, ...)`),
  `ws.py` (`refusal_reason(...) -> AuthRejection | None`),
  `auth.py`, `config/api.py`, `ota/reply.py:362`
  (`_bad_request(message: OtaRefusal)`), `onboarding/origin.py`
  (`origin.source: OriginSource`), and `runtime/pipeline.py:246,388`.
  Sites that hold a plain string and gain an explicit enum lookup
  at the conversion seam: `device/session.py`
  (`_closed_reason() -> str`, latched from several sites, becomes
  `CloseReason(...)` at the emit construction),
  `tools/mcp/reload.py` (`_refusal(exc) -> str` over the five
  `REFUSED_*` constants, which stay exactly as they are: they are
  package surface, re-exported through `tools/mcp/__init__.py` and
  documented by `config/responses.py`, and the lookup happens at
  the emit construction, not in their definitions),
  `tools/mcp/manager.py:622` (`McpDown(_down_reason(...))`, the
  function itself defined in `tools/mcp/transport.py` and not
  touched), `runtime/pipeline.py:270`
  (`ToolSource(classified.source)`), and `ota/reply.py:337`
  (`NotOffered(unbound.refusal)`).

## Tests

- New catalog-level tests on a scratch catalog (the `install()` seam
  the suite already uses): a variant with a plain enum field, one
  with a `Literal`-narrowed field, one with a fixed enum member;
  asserting payload and logged args are builtin `str`, `tokens_of`
  answers the full and narrowed sets, the docgen renders the same
  constraint cell as an equivalent wrapper field did, and `verify()`
  refuses a wrong type, an out-of-narrowing member, and a bare
  string, each with a refusal naming no held value.
- `tests/unit/test_event_values.py`: the `TokenValue` construction
  and refusal tests are deleted with the classes they test; the
  narrowing behavior moves to the catalog-level tests above. Two of
  the tests being touched are not narrowing tests but live
  cross-module drift guards, and both are kept, rewritten against
  the enums and aliases: the pending-refusal guard (the values of
  `get_args(PendingRefusal)` are exactly
  `onboarding/pending.py`'s `CAPACITY_REACHED` and `BUDGET_SPENT`,
  so a reworded bound fails rather than degrades) and the MCP-down
  guard (`McpDown`'s six values are exactly the transport module's
  six constants, and the `McpConnectFailure` subset is exactly the
  four connect-phase ones).
  The only other suite that constructs wrappers is
  `test_event_catalog.py` (three sites); it migrates mechanically
  to members. Every other suite is expected untouched: the matches
  a bare `Token` grep finds elsewhere are `FirstTokenTimeout`, OTA
  token issuance, and an `Authorization: Token` header, and that
  the rest of the test tree does not move is itself part of the
  claim that the surface did not.
- Byte-identity: `uv run vinga-server events reference` diffed
  against the committed `docs/reference/events.md` (locally and by
  CI), and the baseline suite proving the committed records are what
  the harness writes.

## Risks and mitigations

- **An enum member leaks into a record as its subclass.** The
  committed baseline records argument types for the rendered
  positions but only sorted key names for the payload, and
  `json.dumps` serializes a `str` subclass transparently, so a
  member left unconverted in a carried, never-rendered field (most
  of what M1 migrates) would diff nothing in any committed pin.
  The cover is therefore a real-catalog assertion, not a scratch
  one: a test drives the baseline harness's capture of every
  declared emit path and asserts every payload value in every
  produced record is a builtin type (`str`, `int`, `float`,
  `bool`, `None`, or a list of builtin `str`), alongside the
  conversion-point test asserting `type(...) is str`.
- **`get_type_hints` reading of `Literal` and unions.** `_read` must
  handle `Literal[...]` alone and inside unions with `None` and
  `Absent`; the scratch-catalog tests declare exactly those shapes.
  A `Literal` mixing members of two enums is refused at declaration.
- **A missed emit site.** The grep inventory bounds the search;
  `verify()` refuses a stale wrapper instance at emit time (the
  class no longer exists, so it fails at import first); the baseline
  harness drives every declared emit path, so the unit suite reaches
  every migrated site.
- **Docgen ordering drift.** `_tokens` keeps sorting; the events.md
  byte diff is the proof either way.

## Milestones

- [x] **[M1: the catalog admits enum-typed fields, proven on the
  fixed fields](2026-08-22-typed-event-enums-implementation.md#m1-the-catalog-admits-enum-typed-fields-proven-on-the-fixed-fields).**
  (PR #252) Machinery in `catalog.py` and `events_docgen.py`
  as designed, scratch-catalog tests, and the pilot migration:
  every `value(fixed=...)` token field in the catalog moves to its
  enum member. Fixed fields take no caller argument (`init=False`),
  so no emit site changes by construction; the golden inventory is
  regenerated (type names only) and `events.md` is proven
  byte-still. Deepens `events/catalog.py`: variant authors stop
  having to know the wrapper vocabulary; both field styles are
  lawful while the stack is open, and `main` stays releasable.
- [ ] **M2: migrate the caller-passed fields, retype the decision
  sites, delete the wrappers.** Every remaining token field and its
  emit sites move to members; the three narrowed aliases replace
  their classes and the decision sites adopt them; `TokenValue` and
  all subclasses are deleted with their tests; `__all__`, the golden
  inventory, and the test suites land in the same change;
  `events.md` and the committed baseline are proven byte-still.
  Deletes about 25 names from the events vocabulary; no new module.

## Plan review round

External review of commit `470f2e78`, 2026-08-22. Backend: claude
CLI 2.1.239, model `claude-opus-5`, read-only tool set (the interim
fallback tier; the codex quota is exhausted, so independence is
weaker than the sol default: fresh eyes and no session context, but
shared training with the model that wrote the plan). Verdict as
received: not ready; findings 1 and 2 rest on what the three
decision sites actually hold, finding 3 depends on that answer,
findings 4 to 8 amendable in place. Findings condensed but faithful:

1. **P1: the three decision sites hold plain `str`, not enum
   members, so "hand the member straight through, no conversion"
   cannot be implemented as written.** `runtime/turns.py:37-46`
   classifies tool sources into local string constants on purpose
   (held equal to the `tool_invocations.source` column);
   `onboarding/pending.py:104-110` builds `CAPACITY_REACHED` and
   `BUDGET_SPENT` as f-strings over configured bounds, with
   `unbound.py:75` typing `refusal: str | None`;
   `tools/mcp/transport.py:251-273` defines `_down_reason(...) ->
   str` over six local constants. Retyping those to the aliases
   drags four unlisted modules (plus the store column and the
   bound interpolation) into scope; either scope them in or convert
   at the emit site and withdraw the narrowing-at-the-decision-site
   claim.
   *Resolution* (amendment `F1`): resolved by conversion at the
   emit site, not by migrating the classifiers. The open-questions
   section now states which modules stay untouched and why, spells
   the three conversions, withdraws the narrowing-at-the-site
   claim (the narrowing is declared in the catalog and held by
   `verify()`), and notes that an out-of-set lookup becomes the
   same `construction_failed` refusal inside `_construct`'s
   blanket catch that the wrapper's `EventValueError` is today.

2. **P1: the emit-site list is wrong in one entry and short by
   two.** `_down_reason` lives in `tools/mcp/transport.py`, not
   `manager.py`; `device/session.py:585` (`_closed_reason() ->
   str`) and `tools/mcp/reload.py:81-107` (`_refusal(exc) -> str`
   over five `REFUSED_*` constants re-exported through the package
   `__all__` and documented by `config/responses.py`) hold plain
   strings. The plan should distinguish the sites already holding a
   member (`capture.py`, `ws.py`, `ota/reply.py:362`,
   `onboarding/origin.py`, `pipeline.py:246,388`) from the five
   that hold a string.
   *Resolution* (amendment `F2`): the Module layout section now
   lists the sites in two kinds, the seven that already hold a
   member and the five that hold a plain string and gain the enum
   lookup at the emit construction; `_down_reason` is credited to
   `tools/mcp/transport.py` and neither it nor the `REFUSED_*`
   package surface moves.

3. **P1: the test paragraph would delete two live cross-module
   drift guards.** `test_event_values.py:511-517` is the only check
   that `onboarding/pending.py`'s wording and `NotOffered` agree;
   `test_event_values.py:534-548` the only check that the
   transport's six constants and `McpDown` agree. Scratch-catalog
   tests cannot carry either claim; say they are rewritten against
   the enums and the decision sites' constants.
   *Resolution* (amendment `F3`): both guards are kept and
   rewritten against the enums and aliases; the Tests section now
   names them and what each holds equal to.

4. **P2: the mypy scope contradicts the plan's own reason for
   choosing `Literal`.** All three decision sites are outside
   `files = ["src/vinga_server/events"]`, so "a mypy error at the
   site" is false at every site named; and `value()` returns `Any`,
   so a `fixed=` member is invisible to mypy. Decision 5 should
   claim only the declarations' internal consistency and name
   `verify()` as the backstop everywhere else.
   *Resolution* (amendment `F4`): design decision 5 now claims only
   the declarations' internal consistency inside the mypy scope,
   names `value()`'s `Any` return as hiding `fixed=` from mypy, and
   states that the `Literal` aliases are chosen for the no-drift
   reason alone; the open-questions bullet making the site-level
   claim is corrected likewise.

5. **P2: migrating the fixed fields removes an import-time refusal
   and puts nothing in its place.** Today the wrapper constructor
   raises at import for a member outside the set; after the change
   `_check` never looks at `Declared.fixed` and the first evidence
   is a detail-free `construction_failed` at emit. Add a
   declaration-time check in `_check`.
   *Resolution* (amendment `F5`): added as design decision 8:
   `_check` refuses at declaration a `fixed=` member that is not an
   instance of the field's enum or not within its narrowed tokens,
   preserving the refuse-at-import property the wrapper's
   constructor carried.

6. **P2: the migration inventory grep does not cover the three
   hardest sites.** `UnnamedToolSource(`, `PendingRefusal(`,
   `McpConnectFailure(` contain no `Token(`; and the verification
   grep's `reach_ins.py` note is wrong twice over (not under `src`,
   and `tokenize.TokenInfo` does not match `Token(`).
7. **P2: five of the six suites named as constructing wrappers do
   not.** Only `test_event_values.py` (6) and
   `test_event_catalog.py` (3) contain `Token(`; the others match
   `FirstTokenTimeout`, OTA token issuance, or an `Authorization:
   Token` header. Name the two that migrate and claim the rest
   untouched.
   *Resolution* (amendment `F7`): the Tests section now names
   `test_event_values.py` and `test_event_catalog.py` as the only
   suites constructing wrappers and claims the rest of the tree
   untouched, as part of the surface-did-not-move claim.

8. **P2: the baseline pin does not cover payload-only token
   fields.** `event_baseline.py` records argument types and sorted
   payload key names only; a member left unconverted in a carried,
   never-rendered field (most of what M1 migrates) would diff
   nothing, and `json.dumps` serializes the `str` subclass
   transparently. Add a real-catalog assertion that every driven
   record's payload values are builtin types.
   *Resolution* (amendment `F8`): the risk entry now states why no
   committed pin would catch a payload-only member and adds a
   real-catalog test driving the baseline harness's capture and
   asserting every payload value of every produced record is a
   builtin type.

9. **P3: two spots refuse an enum-typed field and are unnamed.**
   `Declared.type: type[EventValue]` and `_read`'s subclass refusal
   must widen (mypy-strict ripples into `verify`, `_check`,
   `_base`); `_arg_constraint` reads `declared.type.ARG_KIND` and
   `.GRAMMAR` eagerly before branching, so an enum-typed argument
   raises `AttributeError` regardless of the token branch.
   *Resolution* (amendment `F9`): design decision 1 now names the
   `Declared.type` widening (with its mypy-strict ripple through
   `verify()` and `_check`) and the eager `ARG_KIND`/`KIND`/
   `GRAMMAR` reads in `_variant_section` and `_arg_constraint`
   that must route through the `Declared` accessors before any
   branch.

10. **P3: after the change no committed structural pin records the
    narrowing.** The golden writes `one.type.__name__`, so the
    three narrowed fields record their parent enums; the
    reference's token column becomes the sole pin on the narrowed
    sets. Record `tokens` in the golden or state the single-pin
    situation explicitly.
    *Resolution* (amendment `F10`): design decision 7 now adds a
    `"tokens"` key to the golden for token fields, holding the
    declared set as a sorted list, so the narrowed sets keep a
    committed structural pin after their type names collapse to the
    parent enums.
