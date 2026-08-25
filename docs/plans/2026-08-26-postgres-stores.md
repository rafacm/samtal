# Move both stores from SQLite to Postgres

## Goal

Implement issue #283: the hard cutover of the domain configuration
store and the conversations store from SQLite files under
`server.database.dir` to one Postgres database, with SQLite support
removed rather than kept as a fallback. The developer loop becomes
`docker compose up -d --wait`, then the server migrates on boot as
today; the deployment story becomes an external database named by
environment variables; the analyst story becomes live read-only SQL
as `vinga_ro` instead of copying a file off a pod around its WAL
sidecars. The retryable-409 contract survives on Postgres's own
vocabulary, and the CLI is untouched beyond documentation, which is
what #281 and #282 were for.

The companion implementation doc,
[`2026-08-26-postgres-stores-implementation.md`](2026-08-26-postgres-stores-implementation.md),
records what each milestone actually did, deviations from this
plan, and discoveries; a milestone with no deviations says so
explicitly.

## The issue's decisions, restated

1. **Hard cutover.** No SQLite fallback, no dual-backend
   abstraction, no data migration tooling. Recovery is
   export-and-reapply; stored secrets re-enter through
   `set-secret`. Revision zero against Postgres is available under
   the floor ADR's priced exit, with the reasoning recorded.
2. **One database** (maintainer, 2026-08-24, recorded on the
   issue): the two-store split existed for SQLite's sake, and under
   Postgres those reasons dissolve into row-level locking. Each
   store gets its **own schema** inside it, which is also the seat
   of the two Alembic chains and the boundary the read-only role is
   scoped to (maintainer, 2026-08-25).
3. **A `vinga_ro` read-only role, provisioned in one home**: an SQL
   file the compose service mounts into
   `/docker-entrypoint-initdb.d`, executed as-is by the infra
   repository against its instance. Alembic is deliberately not the
   home (roles are instance-level, and a password cannot live in a
   versioned migration). `vinga_ro` gets `USAGE` plus
   default-privilege `SELECT` on the conversations schema only, so
   secret ciphertexts in the domain schema stay unreadable.
4. **The driver is `psycopg` v3** (`psycopg[binary]` in dev, the
   source wheel documented for deployments that prefer it), the
   engine stays SQLAlchemy with the URL swapped, and the
   one-engine-per-process ownership decisions are unchanged.
5. **Connection configuration is the discrete-variables pattern**:
   `VINGA_DB_HOST`/`PORT`/`NAME`/`USER`/`PASSWORD`, or a single
   `VINGA_DB_URL` that wins when set, replacing
   `server.database.dir`; the compose file feeds
   `POSTGRES_DB`/`POSTGRES_USER`/`POSTGRES_PASSWORD` from the same
   names with the same defaults, so one `.env` flows into both.
6. **The compose shape**: one `postgres:17-alpine` service at the
   repository root, port bound to loopback with an env override, a
   named volume, a `pg_isready` healthcheck.
7. **Boot against an unreachable database is a refusal with a
   fixed sentence**; the fileless create-on-boot behavior dies with
   the file.
8. **Bounds**: the encrypted-secrets layer (application-level
   Fernet) and the JSON body columns do not change shape; `jsonb`
   is a possible follow-up, not this issue. The deployment
   documentation stays generic; the maintainer's own instance grows
   in the infra repository, out of scope beyond the env contract.
   The unit lane's runtime may not double.

## Design decisions this plan makes

1. **Schema names are `domain` and `conversations`, and the seam
   is a declared type.** Each store package declares a `StoreChain`
   (name of its schema, its migrations directory, its advisory-lock
   key) and hands it to `db.open_at`; the two Alembic `env.py`
   files set `version_table_schema` to their own schema, so the two
   chains keep separate version tables inside one database (today
   they are separate only by being separate files,
   `db/__init__.py:158-161`). The schema itself is created (or
   verified) by `upgrade_to_head`, with
   `CREATE SCHEMA IF NOT EXISTS` executed under the chain's
   advisory lock before Alembic runs: Alembic creates the
   schema-qualified version table before any baseline's
   `upgrade()` executes, so a baseline cannot be the schema's
   creator. The server role therefore needs `CREATE` on the
   database, which the compose superuser and any
   database-owner-role deployment have by construction, and the
   deployment docs state it. Both `env.py` files configure
   `version_table_schema` and `include_schemas=True`, and so do
   the autogen entry point and the metadata-drift tests, which
   schema-qualified comparison requires. A database provisioned
   without the initdb file still migrates from truly blank, and a
   test proves exactly that (no init script, empty database, both
   chains to head through the product opener). Table metadata
   carries the schema explicitly (`MetaData(schema=...)`), never a
   `search_path` trick: the schema a table lives in is a fact with
   one home.

2. **Connection settings live in the server section; the env
   spellings are the `VINGA_DB_*` names; the password is
   env-only.** `DatabaseConfig` becomes `host` (default
   `127.0.0.1`), `port` (`5432`), `name` (`vinga`), `user`
   (`vinga`), replacing `dir` (`config/models.py:352-367`). The
   dedicated env variables `VINGA_DB_HOST`/`PORT`/`NAME`/`USER`
   override the YAML the way `VINGA_MASTER_KEY` sits outside the
   section scheme today (`config/loader.py:87-88`), and they are
   the documented spellings, because compose parity is the point:
   the generic `VINGA_SERVER__DATABASE__*` spelling is refused for
   this section with a sentence naming the `VINGA_DB_*` name, using
   the loader's existing moved-key machinery, so one fact does not
   grow two env names. `VINGA_DB_PASSWORD` has no YAML key at all:
   a password in a config file is what the no-secrets-in-YAML
   stance exists to prevent, and the example file documents the
   variable beside the other env-only credentials. Its dev default
   is `vinga`, matching the compose default, so the
   zero-configuration loop works; the deployment docs say plainly
   that the default is a development convenience on a
   loopback-bound instance. `VINGA_DB_URL`, when set, wins whole
   over all five, and is rendered anywhere only through
   `URL.render_as_string(hide_password=True)`.

3. **The single-writer discipline translates to a
   transaction-scoped advisory lock, and the 409 contract
   survives.** Today every write transaction opens with
   `BEGIN IMMEDIATE` so validation reads and the persist happen
   under one lock (`db/__init__.py:424-431`,
   `config/store.py:11-16`). The Postgres translation: the write
   engine's `begin` listener executes
   `SELECT pg_advisory_xact_lock(<key>)` with the store's own key,
   so writers to one store still serialize whole, validation still
   runs against a state no concurrent writer is mutating, and the
   apply verb's refused-whole one-transaction semantics hold under
   the default `READ COMMITTED` isolation with no serialization
   failures to retry: this is the re-proof decision 2 of the issue
   asks for, and it is by construction rather than by isolation
   level. Every connection sets `lock_timeout` to
   `LOCK_TIMEOUT_MS` (10 000, the renamed `BUSY_TIMEOUT_MS`), so a
   writer that cannot take the advisory lock inside ten seconds
   fails with `LockNotAvailable`, and the contract row keeps its
   shape: same bound, same retryable refusal, same sentences.
   `upgrade_to_head` takes the same advisory lock before Alembic
   reads the version table, which is today's migration race
   answered the same way (`db/__init__.py:437-449`). The
   conversations writer thread keeps its queue, markers, and
   per-batch transactions unchanged; only its engine changes.
   `STOP_TIMEOUT_S` keeps its arithmetic against the renamed
   constant (`conversations/store.py:119`).

3a. **Busy is classified by exception type from a closed set, in
   one home.** The two independent string sniffs
   (`"locked" in detail or "busy" in detail`, `db/__init__.py:334`
   and `config/store.py:818`) collapse into one classifier in
   `db/`: `psycopg.errors.LockNotAvailable`, `DeadlockDetected`
   and `SerializationFailure` (walked via `exc.orig`) map to
   `DatabaseBusyError`; every other database failure stays
   `StorageError`. The set is closed and each member has a
   reachable decision site: `LockNotAvailable` is the
   `lock_timeout` expiring on the advisory lock or any DDL lock,
   and the other two cannot occur under the advisory-lock
   discipline but are retryable by Postgres's own definition, so
   classifying them as retryable is the honest arm rather than
   dead code, and the classifier's docstring says exactly that.
   Message text is never consulted. The refusal sentences keep
   their fixed wording; the migration-failure sentence names the
   `VINGA_DB_*` contract instead of `server.database.dir`.

4. **The read path drops its SQLite machinery and keeps its
   promises on MVCC.** `read_engine` loses the `mode=rw` URI, the
   `isolation_level = None` dance and the explicit deferred
   `BEGIN` (`db/__init__.py:233-284`); it becomes an engine with
   `isolation_level="REPEATABLE READ"` and
   `default_transaction_read_only=on`. Repeatable read is
   load-bearing, not decoration: `read_live_binding` reads two rows
   "in one transaction, so a write landing between them cannot
   produce a state that never existed" (`config/store.py:699`),
   and under `READ COMMITTED` each statement takes its own
   snapshot, which is exactly the torn read that sentence
   promises away. Read-only-by-default turns "creates nothing"
   from a URI trick into a server-enforced property. Readers never
   block writers and never wait on the advisory lock, which is the
   WAL snapshot-read property the engine existed for, now held by
   the backend instead of by ceremony. `device/bindings.py` keeps
   its per-lookup transactions, its loud-not-fatal fallback and
   its generation pinning; what it loses is the file-existence
   probe (decision 6).

5. **Both chains re-baseline against Postgres, and the
   stranded-file machinery retires.** New baselines with never-used
   revision ids in fresh numbering blocks (`3001_postgres_domain`
   for the domain chain, `1001_postgres_conversations` for the
   conversations chain), autogenerated from the two `schema.py`
   metadata and reviewed as code; the old revision files delete.
   No Postgres database can be stamped at a SQLite-era revision
   (the only databases carrying those stamps are files this build
   can no longer open), so `Superseded`, `DOMAIN_SUPERSEDED` and
   `_stranded` (`db/__init__.py:55-115,339-358`) are deleted
   rather than ported: the refusal they carried was advice about a
   file, and there is no file. The floor ADR gains its next
   addendum recording this second use of the priced exit: the
   compatibility floor now starts at the Postgres baselines, the
   pre-release license is the same one #243 spent, and the tested
   reset path is unchanged in kind (re-seed, re-secret).
   `sqlite_autoincrement` is replaced by `BigInteger` identity
   columns on the three cursor tables: Postgres sequences never
   reuse a value, which is the monotonic-cursor property the
   AUTOINCREMENT flag bought
   (`conversations/migrations/versions/0001_...:7-10`), and
   `BigInteger` makes `MAX_ROW_ID = 2**63 - 1`
   (`conversations/api.py:81-84`) true by declaration instead of
   by rowid folklore. Timestamps stay ISO-8601 text columns:
   lexicographic-is-chronological carries over unchanged, and
   retyping them is the same class of follow-up as `jsonb`,
   excluded by the issue's bounds. `db/migrations/autogen.py`
   opens a scratch database (`CREATE DATABASE` against the
   configured instance, dropped after) instead of a scratch file,
   and gains a flag for which chain it targets, closing the gap
   that the conversations chain never had an autogen entry.

6. **Store existence stops being file existence, and the
   conversations no-store 404 retires.** Boot always opens (and
   therefore migrates) both chains against the one database:
   migrating the conversations schema when recording is off
   creates empty tables, not a recording, and the privacy promise
   restates as "recording off starts no writer and writes no
   rows", which is what the adapted pins assert.
   `migrate_existing` and its existence gate
   (`conversations/store.py:195-208`) collapse into the boot open;
   `conversations/api.py:601`'s `Path.exists()` 404 arm and its
   `_NO_STORE` sentence retire, and reads against a store that
   never recorded answer their ordinary empty shapes, which is an
   API contract change this plan makes deliberately (the 404
   distinguished "no file" from "no rows", a distinction that no
   longer exists); the OpenAPI document moves with it.
   `DeviceBindings.open` loses its missing-file snapshot-only arm
   (`device/bindings.py:190-197`): by the time bindings open, boot
   has already refused or migrated. The `BindingsSnapshotOnly`
   event retires from the catalog if nothing else can reach it;
   the per-lookup failure fallback and its events stay exactly as
   they are.

7. **The physical-erasure story is re-derived honestly.**
   `secure_delete`, `wal_checkpoint(TRUNCATE)`, `_checkpoint`,
   `CHECKPOINT_WAIT_MS` and `_truncation_due`
   (`conversations/store.py:158-166,347,797,876-914`) have no
   Postgres analogue and are deleted. The deletion contract
   restates: a deleted row leaves every query surface at commit,
   including `vinga_ro`'s; physical reclamation of the freed pages
   is the database server's own storage maintenance (autovacuum),
   not a per-delete overwrite. The generated schema reference and
   the README say this in those words, because the current
   generated page promises zero-overwrite and checkpoint-truncate
   semantics (`conversations/docgen.py:163-174`) that would be
   false the day the backend moves. Retention (`_prune`) keeps its
   writer-thread seat, its cutoff comparison and its events; only
   the checkpoint step leaves.

8. **The unit lane keeps its speed with one worker database and
   truncation between tests; fresh databases are cloned from a
   template only where migration behavior is the subject.** The
   autouse `writable_database_dir` fixture
   (`tests/conftest.py:158-171`) becomes an autouse fixture that
   points `DatabaseConfig`'s defaults at a per-worker database
   whose name carries a unique per-run prefix
   (`vinga_test_{run}_{worker}`, with `{run}` generated once by
   the controller and shared with workers), created and migrated
   once per worker from a same-prefixed template database; between
   tests it issues one `TRUNCATE ... RESTART IDENTITY CASCADE`
   across both schemas' tables, which is milliseconds, preserves
   the fresh-database-per-test property the throwaway directories
   gave, and keeps re-opens cheap (an already-migrated database is
   a version-table check, exactly as today). The suite's
   connection settings are its own: the session conftest clears
   `VINGA_DB_URL` and overrides the `VINGA_DB_*` family for the
   test process, so an exported production URL in a developer's
   environment cannot redirect the fixtures, and every destructive
   statement (`TRUNCATE`, `DROP DATABASE`) runs only after
   verifying `current_database()` equals the exact generated test
   name, refusing otherwise. The unique prefix is also what lets
   two pytest runs share one instance without colliding; run
   teardown drops the run's databases. Tests whose subject is
   migration or fresh-boot behavior (`test_db_open`,
   `test_conversations_boot`, the integration recovery case) take
   an opt-in fixture that provisions a throwaway same-prefixed
   database: cloned from the template when the test wants a
   current store, created empty (`TEMPLATE template0`) when the
   test's subject is the fresh migration itself, which the
   template cannot exercise, and always dropped (with `FORCE`) at
   teardown. Template creation and the clone/create statements run
   on an autocommit maintenance connection under a session-level
   advisory lock (`pg_advisory_lock` on the maintenance
   database), because `CREATE DATABASE` cannot run inside a
   transaction, so the product's transaction-scoped lock cannot
   coordinate it. The lane refuses at session start with one sentence
   naming `docker compose up -d --wait` when the instance is
   unreachable: a skip would shrink the suite silently and read
   green while proving nothing. The runtime budget is acceptance
   criteria: the unit lane's before/after times are recorded in
   the implementation doc, and the after may not double the
   before.

9. **CI provides the instance as a service container, and the
   wheel step keeps its assertions in Postgres vocabulary.** Both
   jobs gain a `postgres:17-alpine` service with a `pg_isready`
   health check and `VINGA_DB_*` env wiring. The wheel-migration
   step creates a fresh database in the service, migrates both
   chains from the built wheel, and keeps the same assertions
   translated: heads read from the two schema-qualified version
   tables, table inventories and body columns read from
   `information_schema` (replacing `sqlite_master`,
   workflow `:404-409`), and the cursor property asserted as
   identity/default-sequence columns in `information_schema`
   (replacing the `AUTOINCREMENT` DDL grep, workflow `:422-429`).

10. **The image gains the driver and loses the directory; the
    entrypoint stays dumb.** `psycopg[binary]` joins the `serve`
    extra (`pyproject.toml:43-75`); the Dockerfile drops
    `VINGA_SERVER__DATABASE__DIR` and its SQLite comment
    (`Dockerfile:109-123`) while `/data` stays for model caches;
    `docker-entrypoint.sh` gains no wait loop, because a boot
    refusal with a fixed sentence is the contract and restart
    policy belongs to the orchestrator; the dev race is covered by
    `docker compose up -d --wait` in the documented loop, per the
    issue. The provisioning SQL lives at `deploy/postgres-init.sql`
    next to the compose file that mounts it, reads
    `VINGA_DB_RO_PASSWORD` via `psql`'s `\getenv` (available since
    psql 15; the compose pin is 17) so the same file runs
    unmodified under compose and in the infra repository, creates
    both schemas owned by the server role, creates `vinga_ro`, and
    scopes `USAGE` plus
    `ALTER DEFAULT PRIVILEGES FOR ROLE vinga IN SCHEMA
    conversations GRANT SELECT ON TABLES TO vinga_ro` to the
    conversations schema alone. A database migrated without the
    file simply has no analyst role, and the deployment docs say
    so.

11. **Engine ownership does not move.** Boot's store open, the
    lifespan-owned API engine, the bindings view's engine and the
    conversations API's per-request reader keep their owners and
    lifetimes; the per-request reader keeps its
    open-and-dispose-per-request shape (its file-moves
    justification dies, but pooling it is an optimization with its
    own risks, left as a recorded follow-up rather than smuggled
    into a cutover). The CLI read timeout keeps its 30 s value
    with its rationale re-derived: the server still holds a write
    for up to the 10 s lock timeout before answering the
    retryable 409 (`config/cli.py:187-195`), and the onboarding
    release grace keeps its reasoning against the same bound
    (`onboarding/pending.py:246-258`).

## The standing review lenses, pre-answered

- **No-leak.** The new secret surface is the database password
  (and a `VINGA_DB_URL` that may embed one). It reaches exactly
  one consumer, the URL the engine is built from; every rendering
  of that URL goes through `render_as_string(hide_password=True)`;
  the boot-refusal and migration-failure sentences are built from
  the discrete host/port/name fields, never from driver text
  (psycopg connection errors quote the DSN), and raise with the
  cause chain severed per house rule. The sentinel: plant a
  credential-shaped password in settings, drive an unreachable
  boot and a migration failure, assert absence from the sentence,
  `args`, `__cause__`/`__context__`, and both log formats.
  Statement echo stays off with its comment.
- **Pin before reshaping.** The pins that must hold through the
  move, run green before and after: the retryable-409 family
  (`test_config_refusals`, the API 409 mappings, the reload/diff
  sentences), the refused-whole apply suite, the store suites, the
  cursor-pagination suite, and the drift lanes. Pins whose subject
  is the SQLite mechanism itself are retired or translated under
  one stated rule: a pin on a *promise* (busy is retryable,
  deletion leaves the query surface, ids are never reused) is
  translated to the Postgres mechanism; a pin on the *mechanism*
  (journal_mode, `-wal` bytes on disk, `sqlite_master` DDL text,
  `mode=rw`) retires with the mechanism, named one by one in the
  implementation doc. The lock-holding tests translate directly:
  a second connection takes `pg_advisory_xact_lock` and the
  constant is monkeypatched down, same shape as
  `_hold_the_write_lock` today. The text-off proof
  (`test_conversations_session:522-531`, reading file bytes)
  translates to asserting the planted text absent from every
  column of every row, the honest surface a server-side store
  offers, with the weakening stated in the test's docstring.
  `test_logs`'s in-memory `sqlite://` engines stay: they test log
  redaction, not the stores, and the neutral in-memory engine is
  the right tool there; the comment says so.
- **Closed sets mapped to decision sites.** Decision 3a's
  exception set, with its docstring naming each member's decision
  site. Event-catalog changes are subtractive only
  (`BindingsSnapshotOnly` if unreachable; the checkpoint-blocked
  path inside `PruneFailed`'s neighborhood if any event named it),
  and the events reference regenerates in the same change.
- **Honest seams.** `StoreChain` is a frozen dataclass compared
  nowhere by truthiness; the classifier takes the exception, not a
  message; injected settings compare `is not None`. The default
  lock-timeout policy gets its own pin (a fresh connection
  answers `SHOW lock_timeout` with the constant), since
  advisory-lock tests with a shortened constant cannot prove the
  default.
- **Inventories by tooling.** Before/after greps recorded in the
  implementation doc: `grep -rn "sqlite\|SQLite" src tests`
  (survivors: `test_logs`'s in-memory engines and
  history-describing prose only); a second grep over the SQLite
  mechanism vocabulary, expected zero outside recorded survivors
  (`BUSY_TIMEOUT`, `busy_timeout`, `PRAGMA`, `journal_mode`,
  `-wal`, `-shm`, `mode=rw`, `BEGIN IMMEDIATE`, `sqlite_master`,
  `sqlite_autoincrement`, `database.dir`, `DATABASE__DIR`,
  `DATABASE_FILENAME`, `database_path`, `conversations_path`,
  `secure_delete`, over `src`, `tests` and `.github`); and
  `grep -rn "VINGA_DB_"` over the same trees plus the compose
  file, `deploy/` and the example configs as the census of the new
  contract. The 46-file test-import census from the pre-plan
  inventory is re-run after each rebase.

## Module layout

- `docker-compose.yml` (repository root): the postgres service.
- `deploy/postgres-init.sql`: schemas, `vinga_ro`, grants.
- `vinga-server/src/vinga_server/db/__init__.py`: rewritten around
  `open_database(settings)`, `open_at(settings, chain)`,
  `read_engine(settings)`, `write_engine`, `upgrade_to_head`, the
  advisory-lock listeners, `LOCK_TIMEOUT_MS`, and the one busy
  classifier; `database_path`, `existing_engine`, `Superseded` and
  the filename constants delete.
- `vinga-server/src/vinga_server/db/chain.py` (or beside the
  opener if it stays under 40 lines): `StoreChain`, the two
  advisory keys.
- `vinga-server/src/vinga_server/db/schema.py`,
  `conversations/schema.py`: schema-qualified metadata; identity
  cursor columns; SQLite comments rewritten.
- The two `migrations/` trees: new baselines, `env.py` gains
  `version_table_schema`; `autogen.py` opens a scratch database
  and takes the chain.
- `vinga-server/src/vinga_server/config/models.py`: the new
  `DatabaseConfig`; `conversations` section prose.
- `vinga-server/src/vinga_server/config/loader.py`: the
  `VINGA_DB_*` names beside the existing env-only credentials; the
  refusal for `VINGA_SERVER__DATABASE__*`.
- `vinga-server/src/vinga_server/config/store.py`, `boot.py`,
  `api.py`, `app.py`, `reload.py`, `device/bindings.py`,
  `conversations/store.py`, `conversations/api.py`,
  `conversations/docgen.py`, `tools/mcp/reload.py`: call-site and
  sentence updates per decisions 3 to 7.
- `vinga-server/tests/conftest.py`,
  `tests/integration/conftest.py`, `tests/support/stores.py`: the
  fixture reshape of decision 8.
- `.github/workflows/vinga-server.yml`: service containers; the
  wheel step.
- `vinga-server/Dockerfile`, `pyproject.toml`, `uv.lock`,
  `config.example.yaml`, `config.deploy.example.yaml`.
- `docs/reference/`: every drift-checked artifact regenerated.
- `docs/adr/2026-08-20-database-upgrades-have-a-compatibility-floor.md`:
  the addendum.
- `vinga-server/README.md`, root `README.md`, `docs/README.md`,
  deployment docs: milestone 2.
- `CHANGELOG.md` under `## 2026-08-26`.

## Tests

Named so the proof is checkable. The busy family: a second
connection holding the advisory lock yields the exact retryable
sentences at the store, the open, the reload and the diff; each
psycopg member of the closed set classifies retryable and a
foreign `OperationalError` does not; `SHOW lock_timeout` pins the
default policy. The refused-whole family: the existing apply suite
green unchanged, plus the two-writer serialization case adapted
from `test_db_open:102`. The no-leak sentinel of the lenses
section. The cursor family: identity columns proven from
`information_schema`; the pagination suite green; a
delete-then-insert case asserting no id reuse. The drift family:
`compare_metadata` against a migrated database for both chains
(the conversations chain gains the metadata-diff check it never
had, replacing the `sqlite_master` DDL assertion). The lane
itself: unit and integration green against the compose instance
locally and the service container in CI, times recorded, budget
held. The recovery case: the integration export, drop-recreate,
boot, apply, re-secret, byte-identical-export path using the
template-clone fixture. The wheel step green with its translated
assertions. Mutation, per house practice: stopping the compose
service must turn boot into the fixed refusal sentence, not a
traceback; killing a second writer's lock wait early must leave
nothing half-applied.

## Risks and mitigations

- **`lock_timeout` must abort an advisory-lock wait.** It does
  (advisory locks are ordinary heavyweight locks), but the claim
  is load-bearing for the whole 409 story, so milestone 1 proves
  it in its first commit with the two-connection test rather than
  discovering it at review; the fallback, if some environment
  disproves it, is `pg_try_advisory_xact_lock` in a bounded wait
  loop, same contract.
- **The unit lane's runtime.** Truncation-per-test and one
  migration per worker are the cheap shape; the budget check is
  acceptance, and the graded response if it fails is fewer
  per-test truncations (truncate only when the test opened an
  engine) before any redesign.
- **`\getenv` and `TEMPLATE` semantics.** Both verified against
  the pinned major (17) inside the milestone, not assumed: the
  initdb file is exercised by compose in CI-adjacent dev, and the
  template clone runs under xdist locally before the workflow
  changes land.
- **A missed SQLite assumption surfacing late.** The inventory
  greps of the lenses section run as part of the milestone's
  verification, not only at plan time.
- **The deployed instance.** Out of scope per the issue; the
  maintainer chose merging when ready, with the infra repository
  growing its instance separately. The changelog and M1 PR body
  say plainly that the image refuses to boot without a database
  from this merge on.

## Milestones

- [ ] **M1: cut both stores over to Postgres.** One atomic
  milestone for the same reason #243 was one: two storage backends
  cannot coexist releasably, and a half-cut state (one store
  moved, or code moved with the lane still SQLite) is exactly what
  the workflow must never publish. The diff is dominated by the
  db/ rewrite, the fixture reshape, and deletions (the WAL and
  checkpoint machinery, the stranded-file arms, the old
  revisions); compose, the initdb file, the config schema, both
  baselines, CI, the generated references, the example files, the
  ADR addendum and the changelog land here because the drift lanes
  and the releasability rule leave them nowhere else. Design
  footprint: deepens `db/` (callers stop knowing which engine,
  driver, or failure vocabulary exists; classification gets one
  home instead of two copies) and `tests/conftest.py` (a test
  stops knowing databases are provisioned at all); adds one seam,
  `StoreChain`, stated as a type, so the two stores stop reaching
  into the opener's parameter list with loose filenames and
  directories.
- [ ] **M2: rewrite the operator story.** The hand-written docs:
  both READMEs' storage, backup (`pg_dump` replacing
  `VACUUM INTO`/`.backup`), reset and recovery sections; the
  deployment docs' external-database contract and the `vinga_ro`
  access recipe (loopback `psql` in dev; port-forward or one-shot
  exec against a deployment, always as `vinga_ro`); the
  `docs/README.md` index line. Documentation-only, trivially
  releasable, cut from M1 so the cutover diff stays reviewable;
  M1's PR body names the one-PR lag. Design footprint: none, by
  design.

## Plan review round

External review of commit `91e9bd2d`, 2026-08-26. Backend: codex
CLI 0.149.1, model `gpt-5.6-sol`, read-only sandbox, runtime
~10m (reconstructed from file times). Verdict as received: ready
after the P1/P2 amendments. Findings condensed but faithful:

1. **P1: Alembic cannot create the schemas from inside the
   baselines.** With `version_table_schema` configured, Alembic
   creates the schema-qualified version table before the
   baseline's `upgrade()` runs, so `CREATE SCHEMA` as the
   baseline's first operation is too late; and schema-qualified
   autogeneration and drift comparison need
   `include_schemas=True`. Create or verify each schema in
   `upgrade_to_head` under the chain's advisory lock before
   Alembic runs; state the required privileges; prove migration
   from a truly blank database without the init script.
   *Resolution*: decision 1 amended exactly so: `upgrade_to_head`
   owns schema creation under the advisory lock,
   `version_table_schema` plus `include_schemas=True` configured
   in both environments, the autogen entry point and the drift
   tests, the `CREATE` privilege stated for the deployment docs,
   and the blank-database proof named in Tests.

2. **P1: the shared test fixture can truncate a developer's real
   database.** Fixed database names plus defaults-only redirection
   mean an exported `VINGA_DB_URL` or `VINGA_DB_*` in the
   developer's environment can point the fixture's `TRUNCATE` or
   `DROP DATABASE ... FORCE` at a non-test database, and fixed
   names collide across concurrent pytest runs. Generate a unique
   per-run prefix shared with workers, explicitly clear or
   override the production variables, and verify
   `current_database()` equals the generated test target before
   every destructive statement.
   *Resolution*: decision 8 amended exactly so: per-run unique
   prefix generated by the controller and shared with workers,
   `VINGA_DB_URL` cleared and the family overridden for the test
   process, the `current_database()` guard before every
   destructive statement, and run-teardown dropping the run's
   databases.

3. **P1: a migrated template cannot test fresh migration, and a
   transaction-scoped lock cannot wrap `CREATE DATABASE`.**
   Cloning the migrated template cannot exercise the fresh
   migrations `test_db_open` pins, and Postgres forbids
   `CREATE DATABASE` inside a transaction, so the product's
   transaction-scoped advisory lock cannot coordinate template
   creation. Use a session-level advisory lock on an autocommit
   maintenance connection; clone the template only for ordinary
   tests; create migration subjects empty (from `template0`) and
   run both chains through the product opener.

4. **P1: the image smoke lane is omitted and will stop working.**
   The image job seeds and smokes real containers that currently
   share SQLite through `/data`; after the cutover every one of
   them needs a reachable Postgres, and a runner-localhost service
   is not reachable as localhost from nested `docker run`
   containers. Include the image job and `tests/smoke` in M1,
   provide a Docker-network-reachable instance, pass per-scenario
   `VINGA_DB_*`, and require a pre-merge manual-dispatch image run
   because the image job does not run on pull requests.

5. **P1: the provisioning script breaks for a configured server
   user and after database reset.** `ALTER DEFAULT PRIVILEGES FOR
   ROLE vinga` hardcodes the role decision 2 makes configurable; a
   `dropdb`/`createdb` reset removes schemas and database-local
   default privileges while `vinga_ro` (instance-level) survives,
   so an unconditional `CREATE ROLE` fails on rerun; and default
   privileges alone do not cover tables that already exist when
   provisioning runs after migration. Parameterize the server
   role, make the script repeatable (role create-or-rotate, grants
   on current plus future tables), document rerun-after-reset, add
   `docker-compose.yml` and `deploy/**` to the CI path filters,
   and assert in the integration lane that `vinga_ro` reads every
   conversations table, inherits a later-created one, and cannot
   touch the domain schema.

6. **P1: `VINGA_DB_URL` is neither constrained to the chosen
   driver nor safely redacted.** An unrestricted SQLAlchemy URL
   admits `postgresql://` (psycopg2 dialect), `sqlite://`, or any
   other backend, against the issue's decisions; and
   `render_as_string(hide_password=True)` leaves sensitive query
   values (`sslpassword`) in place. Accept only `postgresql` and
   `postgresql+psycopg`, normalize the former to psycopg 3, refuse
   every other scheme with a fixed value-free sentence, never
   render the URL or discrete values on any error surface, and
   extend the sentinel to URL authority and query credentials,
   invalid-URL parsing, discrete fields, connection and migration
   failures, chains, and both log formats.

7. **P1: M1 publishes a breaking image while the operator
   instructions remain SQLite-only.** The workflow publishes every
   push to `main`; a one-PR documentation lag means the published
   image's own README still promises an empty local database
   suffices. And the planned recovery test starts from a Postgres
   template, proving Postgres reset rather than the cutover.
   Land quickstart, deployment prerequisites, export-before-
   upgrade ordering, reset, backup, read-only access and old-file
   disposition in the same releasable milestone as the image; test
   replaying a committed pre-cutover export into a truly empty
   Postgres database; state plainly that conversation history is
   not migrated and what happens to the SQLite file and sidecars.

8. **P2: `psycopg[binary]` in `serve` defeats the promised
   source-install option.** Any `serve` install would pull
   `psycopg-binary` even where the deployment docs recommend the
   source-backed implementation. Put base `psycopg` in `serve`,
   the binary implementation behind a development or image door,
   and document the source install's system prerequisites.

9. **P2: the MVCC deletion and nonblocking-reader claims are
   false as written.** A repeatable-read transaction opened before
   a deletion commits keeps seeing the row; and readers take locks
   that block DDL, so a long analyst transaction can make a
   migration hit `lock_timeout`. Promise disappearance to
   transactions started after the deletion commits, test a held
   snapshot across retention, narrow "readers never block writers"
   to ordinary DML, and consider role-level timeouts for
   `vinga_ro`.

10. **P2: `lock_timeout` is not a ten-second bound on a
    transaction or response.** It applies separately to each lock
    acquisition; a transaction can wait ten seconds on the
    advisory lock and again on later locks, plus unbounded
    execution. State only that one acquisition is bounded and
    yields the retryable refusal; treat the CLI and shutdown
    numbers as independent policy margins, not derived maxima.

11. **P3: `db/chain.py` fails the deletion test.** A file holding
    one small dataclass and two constants, placed by line count,
    is a pass-through; and keeping both advisory keys there
    contradicts decision 1's claim that each store owns its chain
    declaration. Keep the type beside the opener and each concrete
    chain beside its store.
