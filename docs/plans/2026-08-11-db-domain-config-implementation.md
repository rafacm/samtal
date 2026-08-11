# DB-backed domain configuration implementation

Companion to
[`2026-08-11-db-domain-config.md`](2026-08-11-db-domain-config.md).
One section per milestone, recording what was actually built, the
deviations from the plan, the resolutions of its open questions, and
the discoveries a later milestone would otherwise have to make again.

## Milestone 1: storage foundation

Dependencies, `samtal_server/db/` with open-and-migrate, the baseline
migration, and `samtal_server/config/secrets.py` with the envelope and
the key handling. Nothing outside tests imports any of it, so the
server behaves exactly as it did: the YAML file is still the only
configuration it boots from.

### What landed

**Dependencies.** SQLAlchemy, Alembic and cryptography as core
dependencies, all three non-GPL. cryptography already arrived
transitively through the vendor SDKs; declaring it directly is what
makes the server's own use of it visible to a dependency audit.

**`samtal_server/db/schema.py`.** The six tables the plan's schema
section defines, as SQLAlchemy Core metadata: `providers` keyed by
`(stage, name)` with `options` and `secrets` JSON columns,
`mcp_servers` with its model-shaped columns plus a `secrets` column
keyed by dotted path, `agent_defaults` as a single row, `agents`,
`devices` as an entity table keyed by MAC with the ordered binding
list in JSON and no runtime column, and `domain_settings` for the
domain-level scalars. The metadata carries a naming convention, so a
later migration can address a constraint SQLite would otherwise leave
unnamed and unaddressable.

**`samtal_server/db/__init__.py`.** `open_database(dir)` creates the
directory when it can, builds an engine on `<dir>/samtal.db`, sets WAL
and a 10 s busy timeout on every connection, and runs the packaged
Alembic migrations to head. Failures are `ConfigError` naming
`server.database.dir`, including the case of an existing directory that
is not writable, which `mkdir(exist_ok=True)` says nothing about and
which would otherwise surface from somewhere deep inside a migration.
Statement echo is off and parameter logging is never enabled, so a
secret bound into an INSERT cannot ride a debug log line.

Every transaction begins with `BEGIN IMMEDIATE`, taking the write lock
before anything is read. That is what serializes the upgrade-on-open:
the loser of a race waits, then reads the schema the winner committed
and finds it current. It is also, already, the property the
repository's read-modify-validate-write needs in milestone 2.

**`samtal_server/db/migrations/`.** The Alembic environment and one
baseline revision (`0001`) creating the schema, inside the package so a
built wheel carries them. The environment supports only the
programmatic path: a connection arrives on the config's attributes, and
an invocation without one raises rather than inventing a URL, since a
URL of its own would be a second place the database path is decided.

**`samtal_server/config/secrets.py`.** The envelope. A stored secret is
either an environment reference, unchanged from today and left to the
existing model validators, or the JSON object `{"enc": "<token>"}`. The
encrypted payload is a small JSON document holding the secret together
with its canonical location (`SecretLocation`: entity kind, identity,
slot), and `decrypt` refuses a token whose payload names a different
location, so a valid token copied into another row does not open there.
`load_keys` parses `SAMTAL_MASTER_KEY` as comma-separated Fernet keys,
newest first, into a MultiFernet; an absent key returns None rather
than raising, because the CLI has to keep working without one. `mask`
renders ciphertext as a fixed mask and passes an environment reference
through as itself.

**Tests.** `tests/unit/test_db_open.py` covers a fresh directory
gaining a migrated database, the pragmas, an already-migrated file
reopening, two concurrent openers, the two unwritable-directory
messages, and the migration scripts sitting inside the package.
`tests/unit/test_config_secrets.py` covers the round trip, rotation
order, wrong key, missing key, malformed envelope, location mismatch,
key parsing, and masking. Every refusal asserts the location is named
and the plaintext appears nowhere in the message or the exception
chain.

**CI.** A step in the `test` job builds the wheel, installs it alone
into a scratch environment, and migrates a fresh database from it with
the source tree off `sys.path` (`python -P`, run from a directory
outside the checkout). The script asserts it imported from
site-packages rather than trusting it.

### Deviations from the plan

Three, all small.

**`agent_defaults` gets an explicit primary key.** The plan says "fixed
single row" without saying how. The table has an `id` column with a
check constraint pinning it to one value, which makes the singleton a
property of the schema rather than a convention the repository has to
remember. Re-keying it for per-family defaults later is exactly what
the plan says Alembic exists for.

**The baseline migration and `open_database` landed in one commit.**
The suggested split (module, then migration) has no coherent
intermediate: `open_database` without migration scripts cannot open
anything, and the migration environment imports the package the module
defines. Splitting it would have committed a broken state.

**`domain_settings.value` is not nullable.** The plan does not say
either way. Clearing `default_agent` deletes the row rather than
writing a null, so there is no state a null would represent, and
allowing one would create a second way to say the same thing.

### Discoveries

**Alembic hands transaction control back when it is given a
transaction.** `MigrationContext` sets `_in_external_transaction` when
the connection it is configured with is already in a transaction, and
`begin_transaction()` then returns a null context. So the usual
`with context.begin_transaction():` in `env.py` becomes a no-op and the
caller's commit is what ends the migration, which is what lets
`open_database` take `BEGIN IMMEDIATE` before Alembic reads the version
table. Written the usual way in `env.py` regardless, with a comment,
so the file still reads like every other Alembic environment.

**pysqlite's implicit transaction has to be turned off first.** With
the driver's default `isolation_level`, the driver opens its own
transaction before DML, the `PRAGMA journal_mode=WAL` is a silent
no-op, and the `BEGIN IMMEDIATE` never reaches SQLite. Setting
`isolation_level = None` on connect hands transaction control to
SQLAlchemy and to the begin listener.

**The concurrency test is a real test.** With the `BEGIN IMMEDIATE`
swapped for a deferred `BEGIN`, it fails on every run (checked three
times): both openers read the empty version table under a shared lock
and neither can then upgrade to a write lock. The failure is immediate
rather than a busy-timeout wait, because SQLite does not run the busy
handler for that deadlock.

**The wheel check resolves dependencies fresh.** It installs the wheel
from PyPI resolution rather than from `uv.lock`, which is right for
what it tests (the artifact a user would install), and has a
consequence worth knowing before somebody debugs it: an upstream
release can turn that step red on a PR that changed nothing related.
Locally it pulled newer `mcp` and `cryptography` than the lockfile
pins, and the migration ran unaffected.

**Removing the packaged migrations makes the step fail loudly.**
Checked by deleting `db/migrations/` from the installed wheel and
rerunning: Alembic raises `CommandError: Path doesn't exist`, which
`open_database` turns into the `ConfigError` naming
`server.database.dir`. The check tests what it claims to.

### For the milestones that follow

- Every transaction on this engine is `IMMEDIATE`, including a
  read-only one. That is what milestone 2's writes want for free, and
  it means the boot-time snapshot load takes the write lock too.
  Whether the read path deserves a deferred connection of its own is a
  milestone 2 decision; nothing here depends on the answer.
- `verify_secrets` is deliberately absent, per review finding 4. It
  belongs to server startup in milestone 2, not to `open_database`.
- The secret store's key is `SecretLocation(kind, identity, slot)`,
  with a provider's identity spelled `<stage>.<name>` and an MCP
  server's slot spelled `env.<KEY>` or `headers.<KEY>`. Because the
  location travels inside the ciphertext, renaming an entity or moving
  a slot means re-running `set-secret`; the CLI's rename and delete
  paths have to say so rather than copying tokens.
- `schema.DEFAULT_AGENT_KEY` and `schema.AGENT_DEFAULTS_ID` are the two
  fixed keys the repository needs; both live with the tables so they
  cannot drift from the check constraint that enforces one of them.
