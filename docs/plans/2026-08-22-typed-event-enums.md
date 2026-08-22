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
inventory changes only in its recorded type names.

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
- mypy checks a `Literal` of enum members statically, which is the
  whole point of the issue: passing `ToolSource.BUILTIN` where the
  annotation says `UnnamedToolSource` is an error at the site.
- The members remain members of the parent enum, so the three
  decision sites that classify into the narrowed set
  (`_down_reason` in `tools/mcp/manager.py`, the tool-call
  classifier feeding `runtime/pipeline.py`, the pending-table
  refusal feeding `ota/reply.py`) retype their own returns and
  fields with the same alias and hand the member straight through,
  no conversion and no second construction. That puts the narrowing
  at the decision site, where the closed-set lens wants it.

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
   field with the enum".
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
5. **The mypy scope does not change.** Strict mypy runs over
   `src/vinga_server/events` only (pyproject `[tool.mypy]`), so the
   static guarantee lands fully inside the package (every `fixed=`
   value, the catalog declarations) and at the decision sites that
   adopt the narrowed aliases; emit sites outside the package remain
   runtime-checked by `verify()`, as they are today. Widening mypy to
   the whole tree is its own decision with its own cost and is not
   smuggled into this issue.
6. **`Kind.TOKEN` and `ArgKind.TOKEN` stay.** The reference's kind
   vocabulary is reader-facing and unchanged.
7. **The pins move deliberately or not at all.**
   `docs/reference/events.md` and the committed event baseline
   (`tests/unit/test_event_baseline.py`'s harness output) must be
   byte-identical before and after; the golden inventory
   (`tests/unit/data/event-catalog-golden.json`) is regenerated with
   its own script and changes only in `"type"` strings (wrapper name
   to enum name; a narrowed field records the parent enum, its
   narrowing already printed by the reference's token column).

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
- **Inventories by tooling.** The migration inventory is
  `grep -rn "Token(" src tests` from `vinga-server/` (65 hits at
  main `9366dbd4`), refreshed after any rebase; the wrapper deletion
  is verified by `grep -rn "TokenValue\|Token(" src` returning
  nothing but the tokenize false positive in
  `tests/tools/reach_ins.py`.

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
- Emit sites (`auth.py`, `ws.py`, `capture.py`, `config/api.py`,
  `device/session.py`, `onboarding/origin.py`, `ota/reply.py`,
  `runtime/pipeline.py`, `tools/mcp/manager.py`,
  `tools/mcp/reload.py`): pass the enum member where they passed a
  wrapper; the three decision sites adopt the narrowed aliases in
  their own types.

## Tests

- New catalog-level tests on a scratch catalog (the `install()` seam
  the suite already uses): a variant with a plain enum field, one
  with a `Literal`-narrowed field, one with a fixed enum member;
  asserting payload and logged args are builtin `str`, `tokens_of`
  answers the full and narrowed sets, the docgen renders the same
  constraint cell as an equivalent wrapper field did, and `verify()`
  refuses a wrong type, an out-of-narrowing member, and a bare
  string, each with a refusal naming no held value.
- `tests/unit/test_event_values.py`: the `TokenValue` construction,
  narrowing, and refusal tests are deleted with the classes they
  test; the narrowing behavior now lives at catalog level above.
  Existing suites that construct wrappers
  (`test_event_catalog.py`, `test_ota_tokens.py`,
  `test_session_watchdog.py`, `test_session_filler.py`,
  `test_session_reply_failures.py`, `test_config_api.py`) migrate
  mechanically to members.
- Byte-identity: `uv run vinga-server events reference` diffed
  against the committed `docs/reference/events.md` (locally and by
  CI), and the baseline suite proving the committed records are what
  the harness writes.

## Risks and mitigations

- **An enum member leaks into a record as its subclass.** The single
  conversion point in `payload()`/`logged()` is pinned by a test
  asserting builtin `str`, and the committed baseline's typed args
  would show any slip as a byte diff.
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

- [ ] **M1: the catalog admits enum-typed fields, proven on the
  fixed fields.** Machinery in `catalog.py` and `events_docgen.py`
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
