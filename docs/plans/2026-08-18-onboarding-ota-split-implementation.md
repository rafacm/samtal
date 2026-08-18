# Untangle onboarding.py and ota.py: implementation

Companion to
[`2026-08-18-onboarding-ota-split.md`](2026-08-18-onboarding-ota-split.md).
One section per milestone, recording what was actually written, the
deviations from the plan, what building it turned up, and the
verification results as they came out. A milestone with no deviations
says so explicitly.

## The inventory, retaken at main@8c20596

Issue #143's anchors are pinned to main@8dd1a5f and have moved. The
figures below were retaken at 8c20596, the commit this plan's branch is
based on, and they are what the plan's evidence section cites. Recorded
here because both milestones' designs rest on them, and because a
number that moves under a later milestone is a finding rather than a
typo.

**767 lines in `onboarding.py`, 608 in `ota.py`.** Both figures are the
issue's, unchanged.

**The cycle's edges.** `onboarding.py:72` imports `ota` at module
scope; `build_router` (678-706) registers `ota.check_version`,
`ota.describe` and `ota.activate` by reference and uses `ota.spellings`
and `ota.ACTIVATE_SEGMENT`. `ota.py`'s three function-body imports sit
at 427 (`activation_object`), 514 (`ACTIVATION_ALGORITHMS`) and 564
(`portal_url_line`), each with its apology comment. `ota.py:57` takes
`WEBSOCKET_PATH` from `ws`, and `ws.py:29-34` defers `Composition`
under `TYPE_CHECKING` naming the chain. `config/api.py` defers the
pending table twice (the `TYPE_CHECKING` block at 120-129, and
`_empty_pending` at 681-691) and `config/cli.py` defers `Origin` (103)
and the module itself inside `_onboarding_url` (801).

**The two halves are marked in the file itself**: the docstring
declares both at 40-46, and a section comment at 362-379 marks the
pending-table seam. `activation_object` (656-675) is the one
cross-half function.

**The unbound decision's three sites**: `DeviceAgents.authoritative`
(`device/bindings.py:58-88`), `ota._activation` (376-444) and
`PendingDevices.observe` (468-505).

**Origin assembly**: `ota.websocket_url_for` (108-115) against
`onboarding.public_origin` (230-267).

**Four emit sites on `samtal_server.onboarding` and twelve on
`samtal_server.ota`**, counted as `event=` keywords in each file, which
is what the sixteen sidecar identities in the conformance suite are.

**The re-export surface, by tooling.** An AST walk over
`samtal_server/` and `tests/` for `from samtal_server.onboarding import
...` and `from samtal_server import onboarding` answers with twenty
names plus the module itself. The test suites alone import fifteen of
them (`ACTIVATION_TIMEOUT_MS`, `BUDGET_SPENT`, `CAPACITY_REACHED`,
`CODE_DIGITS`, `CODE_TTL_S`, `FACT_LENGTH`, `KEY_LENGTH`,
`MINT_BUDGET`, `MINT_WINDOW_S`, `PENDING_CAPACITY`, `PendingDevices`,
`RELEASED_GRACE_S`, `derive_key`, `onboarding_key`, `onboarding_path`)
and bind the module itself in three more; the plan's "at least
fourteen" is the floor it said it was.

## M1: the onboarding package

`samtal_server/onboarding.py` became
`samtal_server/onboarding/`: `__init__.py` (223 lines), `keys.py`
(183), `origin.py` (177), `pending.py` (320) and `unbound.py` (149).
Everything the plan lists for each file went to that file, and the code
and its comments moved verbatim except where this section says
otherwise.

The facade builds the emitter under the package's own name and the four
submodules that emit take it with `from . import events`, which is the
#155 package-owned-channel rule and what keeps the channel, and the
`logger` field of every record, exactly what it was. The tunable bounds
(`CODE_DIGITS`, `_CODE_CEILING`, `CODE_TTL_S`, `PENDING_CAPACITY`,
`MINT_BUDGET`, `MINT_WINDOW_S`, `RELEASED_GRACE_S`) are defined in the
facade above the submodule imports; `pending.py` binds the package once
(`import samtal_server.onboarding as onboarding`) and reads every one
of them through it at the moment it decides by one. `build_router`
stays in the facade, with a docstring paragraph naming it as the one
surviving edge to `ota` and M2 as its retirement.

`unbound.py` is new work rather than moved code: `activation_for` with
the typed `Unbound` result (`activation`, a four-outcome `outcome`
literal, `refusal`), holding today's `_activation` sequence with each
branch returning its tagged outcome instead of warning in place.
Nothing calls it this milestone; `ota` still calls `activation_object`
and reads `ACTIVATION_ALGORITHMS` through the facade.

### Deviations from the plan

Three, all small, none of them behavioral.

1. **`events_schema.py` was touched, and the events reference gained
   one line.** The plan foresaw this conditionally ("checked and
   updated the same way if the walk resolves them by location") and the
   walk does resolve it by location:
   `test_every_composed_argument_names_a_grammar_and_its_builder` runs
   `scope_of` over every composed grammar's builders, and
   `ORIGIN_PROVENANCE`'s `samtal_server.onboarding:Origin.provenance`
   fails the moment `Origin` is defined in a submodule. The citation is
   now `samtal_server.onboarding.origin:Origin.provenance`. That string
   is rendered into the generated reference, so
   `docs/reference/events.md` changed by exactly that one table cell.
   No registry entry, channel, template, field or level changed, which
   is what the rest of the empty diff shows. The other citation the
   plan named (`ALSO_BOUND_TO`/`AGENT_LIST`, at
   `samtal_server.ota:check_version`) is M2's, and untouched here.

2. **`activation_for`'s `client_id`, `board` and `firmware` are `str`,
   not `str | None`.** The plan's signature sketch marks the three
   optional. `PendingDevices.observe` takes them as `str` and hands
   each to `_fact`, which calls `.strip()`, so a None would be a
   crash rather than a case; today's `_activation` passes a required
   header and two reporters that fall back to fixed strings, so no
   caller has one to pass. Declaring an optional the body cannot
   survive would be a promise the seam does not keep.

3. **`activation_for` is `async` with nothing awaited in it.** The
   plan declares it `async def` and M2's caller is an async handler, so
   the shape is settled rather than discovered; it is recorded here
   because a reader meets the `async` before they meet the caller.

### Discoveries

- **`pending.py`'s early binding of the package is safe, and tested by
  the module's own text.** `CAPACITY_REACHED` and `BUDGET_SPENT` are
  f-strings evaluated at import, and they read
  `onboarding.PENDING_CAPACITY`, `onboarding.MINT_BUDGET` and
  `onboarding.MINT_WINDOW_S` while the package is still initializing.
  That is exactly the ordering the plan's write-through design asks
  for, and it exercises the partial-import path at every start rather
  than only under a test.
- **The write-through is a live property, not an inherited one.** Both
  sentences those constants render are also the words two warnings
  print, and they are snapshots of the bounds as configured, as they
  were before: a monkeypatched `MINT_BUDGET` changes what is refused
  and not what the refusal says. Unchanged behavior, worth saying,
  because the two live one line apart now.
- **`unbound.py` imports `device/bindings.py` for `DeviceAgents`**, so
  the onboarding package now reaches SQLAlchemy and the configuration
  store through that one module. It reaches neither `ota`, `ws` nor the
  providers package by that route, so M2's import-weight check is not
  compromised; but the plan's "imports nothing device-facing" holds of
  `keys`, `origin` and `pending` only, which is what the CLI and the
  configuration API actually read.
- **`CALL_ALTERNATIVES` needed no change.** The plan expected entries
  there to move; all of its keys name `samtal_server.ota`, so they are
  M2's.

### Verification

- `uv run ruff check .`: clean.
- `uv run pytest tests/unit -q`: 2988 passed, 16 skipped, which is what
  the same command answered on the branch base before any of this.
- `uv run pytest tests/integration -q`: 58 passed.
- The four generated references, regenerated and diffed against the
  committed copies: all four clean. The committed
  `docs/reference/events.md` carries the one grammar-citation cell of
  deviation 1; `domain-config.md`, `conversations-schema.md` and
  `api-openapi.json` are byte-identical to what main holds.
- **No test file changed except the conformance suite.** `git diff
  --stat` against the branch base lists
  `tests/unit/test_event_schema_conformance.py` and nothing else under
  `tests/`. What changed in it is what its own exhaustive failures
  named: four sidecar identities (`log_banner` 1 and 2 to
  `samtal_server.onboarding.origin`, `_log_mismatch` 1 and 2 to
  `samtal_server.onboarding.keys`) and three token decision sites (the
  `Offer:refused` half of `activation_not_offered.reason`, all of
  `activation_not_offered.arg:1`, and
  `onboarding_banner.origin_source`), plus the one comment that named
  the old file.

The three greps the plan asks M1 to record:

**(a) The re-export surface, shown importable.** Every name any module
or suite imports from `samtal_server.onboarding`, in one statement:

```console
$ python -c "
from samtal_server import onboarding
from samtal_server.onboarding import (
    ACTIVATION_ALGORITHMS, ACTIVATION_TIMEOUT_MS, BUDGET_SPENT, CAPACITY_REACHED,
    CODE_DIGITS, CODE_TTL_S, FACT_LENGTH, KEY_LENGTH, MINT_BUDGET, MINT_WINDOW_S,
    PENDING_CAPACITY, RELEASED_GRACE_S, Origin, PendingDevice, PendingDevices,
    activation_object, derive_key, onboarding_key, onboarding_path, portal_url_line,
)
for name in ('log_banner', 'build_router', 'events', 'MINT_BUDGET'):
    assert hasattr(onboarding, name), name
print('20 imported names plus the module itself: OK')
"
20 imported names plus the module itself: OK
```

**(b) The one facade edge.**

```console
$ grep -rn "import ota" samtal_server/onboarding/
samtal_server/onboarding/__init__.py:47:from samtal_server import ota
```

**(c) No test imports `build_router`.**

```console
$ grep -rn "build_router" tests/
$
```

Nothing under `tests/` names it at all, in either module's spelling,
which is the proof the plan's fourth review finding asked M1 for: M2
may retire the symbol when `app.py` switches to the alias router
without touching a suite.

### Left for M2

The `config/api.py` and `config/cli.py` deferrals and their apology
comments stay, as review finding 3 settled: importing any onboarding
submodule executes the facade, and the facade still imports `ota` for
`build_router`. `ota.py` is still one module, `websocket_url_for` is
still in it, `WEBSOCKET_PATH` is still in `ws.py`, and the unified
`assemble` helper is not built.
