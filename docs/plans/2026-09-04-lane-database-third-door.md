# Close the third door to the lane database

Plan for [#333](https://github.com/rafacm/vinga/issues/333).
Implementation notes land in the companion
`2026-09-04-lane-database-third-door-implementation.md`, one
section per milestone, appended in the change that ticks the
milestone here.

## Goal

The unit lane's isolation contract promises that every test runs
against its per-worker lane database, and `_database_default` sets
the fact at what the #283 round called both doors: the environment
variable and `DatabaseConfig`'s model default. There is a third
door. Pydantic inlines a sub-model's schema, defaults included,
into every embedding model's compiled validator at class creation,
so a composition whose payload carries a `database` mapping that
omits fields (`Config(server={"database": {}})`) fills them from
`ServerConfig`'s stale inlined copy and boots against the compose
instance's real `vinga` database. Around 23 unit-lane boot tests
travel that door today, one of them asserting row counts against a
database it never booted into. This plan closes the door where the
first two were closed, and extends the agreement pin so a fourth
door cannot open silently.

## The issue's decisions, restated

- `_database_default` sets the fact where `ServerConfig` actually
  reads it (or `ServerConfig` stops baking the default at import
  time).
- A pin in the shape of `tests/unit/test_lane_database.py` fails
  whenever a boot-suite composition resolves to a database outside
  the lane prefix.

## Where the facts already live, and the mechanism verified

`DatabaseConfig` (`config/models.py:367-400`) declares four plain
class-level defaults and reads no environment; exactly three
models transitively embed it, `ServerConfig`, `FileConfig` and
`Config` (enumerated by walking `BaseModel.__subclasses__()` over
the whole package). `_database_default` (`tests/conftest.py:482`)
mutates `DatabaseConfig.model_fields[...].default` and rebuilds
`DatabaseConfig` alone; its own docstring already says pydantic
bakes defaults into validators at class creation, it just does not
follow that through to the embedding models. Verified empirically
on this tree (pydantic 2.13.4): after the conftest's exact
mutation, `DatabaseConfig()` and `ServerConfig()` answer the lane
name (the `default_factory` door is a call-time callable), while
`ServerConfig(**{"database": {}})`,
`Config(server={"database": {}})` and
`FileConfig(**{"server": {"database": {}}})` all answer `vinga`;
and rebuilding the chain in the order `DatabaseConfig`,
`ServerConfig`, `FileConfig`, `Config` (a child's rebuild does not
propagate upward) makes every case answer the lane name with
`load_file_config(None)` still resolving correctly. The leaking
callers are the `recording_config`/`quiet_config` helpers in
`test_conversations_boot.py` and `test_app_lifespan.py` that build
`server={"database": {}}`; three call sites in the latter already
route around the bug by passing `DatabaseConfig().name`
explicitly, which is the workaround this plan retires the need
for.

## Open questions, resolved

**Fix the conftest's door-setting, not the models.** The two
model-side shapes were considered and rejected with the recon's
evidence. Environment-only cannot work: `Config(server=...)` never
passes through `load_file_config`, so `_with_database_environment`
never runs and no variable can reach it; the env half is already
set and already correct. Env-reading `default_factory` fields on
`DatabaseConfig` would close the baking hole but invert the
documented precedence (env beats file, pinned in
`test_config.py:305-318`; a factory only runs when the key is
absent, so the file would beat env) and give the four facts a
second reading site against the one-home stance recorded in
`loader.py:156-168`. Production is untouched either way: the only
mutators of `model_fields`/`model_rebuild` in the entire tree are
the two conftest sites, and the server's own boot resolves through
`load_file_config`.

**One helper owns the four values and the cascade, and both
callers use it.** A private conftest helper takes the four
connection facts and performs the whole move: set the environment
name, set the four model-field defaults, rebuild the cascade in
the required order. `_database_default` becomes a call to it with
the lane values; `packaged_database` (the mirror fixture that
temporarily restores the shipped defaults and today rebuilds only
`DatabaseConfig`, which would re-create the asymmetry in the
opposite direction) becomes two calls to it, shipped values in,
lane values back. The order constraint and the reason (a child's
rebuild does not propagate) live as a comment on the helper, since
that is the fact a future editor must not lose.

**The pin grows a third door and a completeness half.**
`test_lane_database.py`'s agreement test was framed as "the
agreement rather than either half" and enumerated two doors when
there are three. It gains: the payload-composition door,
`ServerConfig(**{"database": {}})`,
`FileConfig(**{"server": {"database": {}}})` and
`config_with_agent(server={"database": {}})`, each asserting the
resolved name carries the lane prefix (`DATABASE_PREFIX`, already
importable beside the names the pin imports today); and a
completeness assertion that derives, by walking model fields, the
set of models transitively embedding `DatabaseConfig` and asserts
it equals the set the helper rebuilds, so a fourth embedder added
later fails the pin instead of reopening the hole silently.

**The vacuous test becomes real, and is reread rather than
assumed.**
`test_recording_off_starts_no_writer_and_writes_no_rows` today
boots into `vinga` and counts rows in the lane database, asserting
zero about a database it never touched. After the fix both halves
are the lane database and the autouse truncate keeps the count
honest; the implementation rereads the test and records in the
implementation doc whether its assertions needed strengthening
rather than assuming green means proven. The three explicit
`DatabaseConfig().name` workaround call sites in
`test_app_lifespan.py` are left as they are: they state their
database and stating is not a bug, but the implementation doc
notes they are no longer load-bearing.

## Module layout

No production module changes at all. The conftest deepens (one
helper owns the four facts and the cascade), and the pin file
deepens (three doors and a completeness rule instead of two
doors).

## Tests

- The extended `test_lane_database.py` above, with the
  payload-composition door asserted through all three embedders
  and the support helper, and the completeness assertion derived
  from the models rather than written as a list.
- The existing two-door assertions, the URL pin, and the refusal
  sentences stay untouched.
- `test_the_database_connection_defaults_and_is_overridable`
  (through `packaged_database`) keeps passing, now with the
  cascade mirrored, and a case drives the packaged span itself
  through the payload door: inside the fixture's span,
  `ServerConfig(**{"database": {}})` answers the shipped name;
  after it, the lane name, pinning the mirror symmetry that the
  old single-model rebuild would have broken.
- The whole unit lane run the CI way is itself the migration
  proof: the ~23 relocated boot tests run against the lane
  template, which already migrates all three schema chains.

## Risks

- **The relocated boot tests could surface real differences.**
  They now truly exercise the lane database; three sibling call
  sites already boot against it, so the target is proven, and any
  newly red case is a finding about the test, recorded rather
  than papered over.
- **Rebuild cost and ordering.** Three extra
  `model_rebuild(force=True)` calls per process at conftest
  import, order-constrained; the order lives in one place with
  its reason, and the completeness pin catches a future embedder
  that would silently need a fourth.
- **A developer's real `vinga` database stops being touched by the
  unit lane**, which is the point; the #283 and #190 records in
  the ADR and implementation docs stay accurate as history and
  need no amendment, and the plan adds a dated pointer beside the
  #283 round's two-doors paragraph naming this plan as the third
  door's closure.

## Milestones

- [ ] **M1: the cascade, the mirror, and the three-door pin.** The
  conftest helper with the ordered cascade; `_database_default`
  and `packaged_database` rebuilt on it; the extended pin with the
  payload door and the derived completeness assertion; the
  packaged-span symmetry case; the reread of the vacuous test
  recorded; the dated pointer in the #283 implementation doc; a
  CHANGELOG entry; the implementation-doc section. Design
  footprint: test infrastructure only, no production change; the
  four connection facts get one home in the conftest the way the
  store's secrets rule has one home in `models.py`.
  Documentation footprint: `CHANGELOG.md` and the dated pointer;
  nothing generated is touched.
