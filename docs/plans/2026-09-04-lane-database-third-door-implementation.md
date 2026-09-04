# Close the third door to the lane database: implementation

Companion to
[`2026-09-04-lane-database-third-door.md`](2026-09-04-lane-database-third-door.md).
One section per milestone, recording what was actually written, the
deviations from the plan, what building it turned up, and the
verification results as they came out. A milestone with no deviations
says so explicitly.

## M1: the cascade, the mirror, and the three-door pin

### What was done

**The helper and its manifest** (`vinga-server/tests/conftest.py`).
`_rebuild_order()` imports the four models and answers
`(DatabaseConfig, ServerConfig, FileConfig, Config)`; its docstring
carries the ordering reason, which is the fact a future editor must not
lose: pydantic inlines a sub-model's schema, defaults included, into
every embedding model's compiled validator at class creation, and a
child's rebuild does not propagate upward, so innermost first is what
makes the outer rebuilds inline the fresh schema. The call result is
the module-level `DATABASE_REBUILD_ORDER`, which is the one manifest
the helper iterates and the one the pin compares its derived set
against. `PACKAGED_CONNECTION` beside it holds the four shipped values.

`_database_condition(*, host, port, name, user, environment_name)` is
the private helper that performs the whole move: the environment name,
the four model-field defaults, then `model_rebuild(force=True)` over
the manifest in order. `environment_name` is presence rather than a
value, `None` meaning the variable is removed, because absent is a
condition in its own right: the packaged span takes the variable away
so `load_file_config()` answers out of the package's own default, and a
span that set it to `vinga` instead would let that test pass through an
override while the model default was still broken. `_database_default`
is now three lines calling it with the lane's values.

**The mirror** (same file). `packaged_database` yields a
`_PackagedDatabase` span object with an `override(**facts)` method that
sets any of the four `VINGA_DB_*` variables for the span, reading the
field-to-variable mapping from the loader's own `DATABASE_ENV_NAMES`
rather than spelling it a second time. One finalizer undoes the span's
own overrides first and then restores the lane condition, so the last
writer of every one of those variables is that finalizer, and it ends
with two same-process assertions: `VINGA_DB_NAME` names the lane
database, and `ServerConfig(**{"database": {}})` resolves to it. The
consumer, `test_the_database_connection_defaults_and_is_overridable`
in `tests/unit/test_config.py`, now calls
`packaged_database.override(...)` and no longer takes `monkeypatch` at
all.

**The pin** (`vinga-server/tests/unit/test_lane_database.py`), five new
tests beside the five that were there:

- `test_the_payload_door_answers_this_lane_through_every_embedder`:
  `ServerConfig`, `FileConfig`, `Config` and `config_with_agent`, each
  on an empty `database` mapping.
- `test_a_partial_database_payload_inherits_this_lane_s_instance`: the
  shape `_restricted_app` builds in the integration lane, asserting the
  omitted `host` and `port` are the lane instance's.
- `test_all_four_connection_facts_travel_the_cascade_and_come_back`:
  the helper driven once with four sentinel values, the complete
  `(host, port, name, user)` tuple asserted through all four models,
  then complete restoration asserted the same way.
- `test_every_declared_embedder_of_the_database_is_in_the_rebuild_order`:
  every model class declared in `config.models`, field annotations
  traversed recursively through containers and unions, the derived set
  asserted non-empty and equal to the manifest, plus an ordering
  assertion that no model is rebuilt before something it embeds.
- `test_the_packaged_span_mirrors_the_cascade_through_the_payload_door`:
  inside the span every door answers the shipped connection and
  `VINGA_DB_NAME` is absent.

The file's module docstring says why it reaches for the conftest's
private `_database_condition` and its manifest: the conftest's own
mechanism is the subject here, and a pin written only against what a
caller sees is exactly the pin that passed through both failures.

**The vacuous test**
(`tests/unit/test_conversations_boot.py::test_recording_off_starts_no_writer_and_writes_no_rows`).
The configuration is built once, its resolved database asserted to be
`LANE_DATABASE`, that same object handed to `create_app`, the engine
opened on `config.server.database`, and `current_database()` asserted on
the connection immediately before the count.

### Deviations from the plan

Three, all small.

1. **The payload-door assertions compare against `LANE_DATABASE`, not
   `DATABASE_PREFIX`.** The plan says each case asserts "the resolved
   name carries the lane prefix". Equality with this worker's own
   database is strictly stronger and is what the isolation contract
   actually promises: another worker's database also carries the run's
   prefix, and writing into it is exactly as wrong as writing into
   `vinga`. The prefix is still what the destructive statements guard
   on, which is where a prefix is the right granularity.

2. **The completeness test also pins the manifest's ORDER.** The plan
   asks for set equality against the ordered tuple. Set equality cannot
   catch a reordering, and the order is the whole mechanism, so the test
   additionally asserts that no entry embeds an entry before it.

3. **The consuming test's other three overrides moved to the fixture
   too.** The plan's resolution names `VINGA_DB_NAME` as the one that
   must not be monkeypatched beside the span. `VINGA_DB_HOST`,
   `VINGA_DB_PORT` and `VINGA_DB_USER` were set in the same four lines,
   and leaving three of four on `monkeypatch` would have left the next
   reader to work out which of the four was special and why. All four
   go through `override`.

### The three `DatabaseConfig().name` call sites

`tests/unit/test_app_lifespan.py` passes `DatabaseConfig().name`
explicitly at three `recording_config(...)` call sites (lines 509, 549
and 690). That was the workaround for exactly this bug: naming the
database is what kept those three off the payload door. **They are no
longer load-bearing.** They are left as they are, deliberately: they
state which database the test runs against, and stating a fact is not a
bug. What has changed is that removing them would now be a no-op rather
than a silent relocation onto `vinga`.

### The relocated boot tests

Fifteen test functions across the two boot suites composed
`server={"database": {}}` and therefore travelled the third door;
`test_recording_off_starts_no_writer_and_writes_no_rows` is
parametrized three ways, so seventeen test items now truly boot against
the lane database rather than against the compose instance's `vinga`.
Four are in `test_conversations_boot.py`
(`test_an_enabled_boot_says_it_is_recording`,
`test_recording_off_starts_no_writer_and_writes_no_rows`,
`test_a_start_failure_in_the_lifespan_still_stops_the_store`,
`test_a_startup_failure_after_the_writer_started_still_stops_it`) and
eleven in `test_app_lifespan.py`. The plan estimated "around 23"; the
measured figure is 15 functions and 17 items.

**All seventeen passed unmodified**, on the first run after the
cascade landed, serial and under `-n auto --dist loadfile` alike. The
risk the plan recorded (that they could surface real differences now
that they exercise the lane database) did not materialize, which the
plan predicted for the right reason: three sibling call sites in
`test_app_lifespan.py` were already booting against the lane database,
so the target was proven before anything moved onto it.

The integration lane's `_restricted_app` partial payload (two call
sites in `test_provisioning.py`) was the other case in the census. It
names its own database and role, so what moved is its omitted `host`
and `port`, from the shipped values onto the lane instance's; on this
tree and on CI those coincide, so nothing there changed behavior
either. The lane is green.

### Verification

Everything from `vinga-server/`, against the development Postgres
started by `docker compose up -d --wait`.

- `uv run ruff check .`: `All checks passed!`
- `uv run pytest tests/unit -q -n auto --dist loadfile` (the lane this
  change is about, run the way CI runs it): `5381 passed, 19 skipped in
  86.25s (0:01:26)`
- `uv run pytest tests/unit -q` (serial): `5381 passed, 19 skipped in
  627.05s (0:10:27)`
- `uv run pytest tests/integration -q`: `239 passed in 387.79s
  (0:06:27)`
- `python3 scripts/check_doc_links.py .` from the repository root:
  `checked 187 files, 0 failures`

The CHANGELOG entry staled the command-spellings census, which the
first serial run caught (`test_the_manifest_is_the_census`, the only
failure in an otherwise green lane). Regenerated with
`uv run python -m tests.unit.test_command_spellings`; the whole of the
diff is line numbers in `CHANGELOG.md` rows shifted by the new entry,
no row's path, class or spelling changed. The new implementation doc
was staged before regenerating, because the census enumerates tracked
files and would otherwise have swept a file git could not see.

The pin was verified red before it was verified green: with the
conftest's rebuild loop cut back to `DATABASE_REBUILD_ORDER[:1]`, which
is exactly the pre-change behavior, the new file reports
`2 failed, 8 passed, 1 error` (the payload-door test, the four-sentinel
test, and the packaged span's finalizer refusing to leave the worker
pointed at `vinga`). The bytecode trap in `AGENTS.md` does not apply to
that cycle: the lane writes no bytecode and clears the caches it finds.
