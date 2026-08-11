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

### PR #95 review round

One external review of the pull request's diff (main...c34331e):
codex CLI 0.147.0, model gpt-5.6-sol, read-only, 2026-08-11, posted
verbatim on the PR. Verdict: mergeable after fixing the provider
reference representation and the masking and concurrency-test gaps.
Three findings, each fixed with its own commit:

1. **P1: provider environment-key references had no defined
   storage.** `api_key_env` is a declared model field, excluded from
   `ProviderConfig.options`, and the providers table had no column
   for it, so a repository written naturally against the schema
   would have silently dropped every cloud provider's credential
   reference. Fixed in 4edaaa8: a nullable `api_key_env` column in
   the table and the baseline migration, the plan's schema section
   updated to name it, and a test pinning both that every declared
   model field has a column and that a row carrying `api_key_env`
   round-trips intact.
2. **P2: masking exposed malformed stored values.** `mask()` passed
   through everything that was not a perfectly formed envelope,
   including near-envelopes with extra keys and stray plaintext in a
   secret slot. Fixed in 6691e99: only the two syntactic
   environment-reference forms pass through ($NAME and the bare
   uppercase name an `*_env` field holds); everything else fails
   closed to the mask, with sentinel-credential tests over each
   malformed case.
3. **P2: the migration-race test did not force the race.** The
   barrier released before either thread entered `open_database`, so
   scheduling could hand one opener the whole migration before the
   other started, and the test could pass with serialization
   removed. Fixed in c37d055: the first opener is held inside the
   migration while it owns the write lock, the second is asserted to
   neither finish nor enter the migration until the first commits,
   and swapping BEGIN IMMEDIATE for a deferred BEGIN now fails the
   test in a single run.

Full lanes after the fixes: ruff clean, unit and integration suites
green (counts in the PR's verification section).

## Milestone 2: repository and write path

`config/store.py`, the write-time validation set, `config/cli.py` with
the command group, the point-of-use secret resolvers, and
`verify_secrets` implemented and tested but called by nothing. The
server's boot path is untouched: it still loads the YAML file, builds
providers with no secret store, and knows nothing about the database.

### What landed

**The validation split.** `Config._check_references` became two module
functions over a domain snapshot, `check_references` (agents and
agent_defaults to providers and MCP servers, device bindings to agents,
default_agent to an agent) and `check_completeness` (default_agent is
required when agents are defined and no device is bound). The model
validator now runs completeness then references and joins the problems,
which is the order and the wording it produced before, so no boot
message moved. The `mcp_servers` and `devices` field validators moved
out the same way, into `check_mcp_entry_names` and
`normalize_device_bindings`, so a snapshot that is not a `Config` can
apply them.

**`server.database.dir`.** A `DatabaseConfig` on `ServerConfig`,
defaulting to `/var/lib/samtal`, with a commented block in
`config.example.yaml`. Nothing reads it except the CLI.

**`config/store.py`.** `DomainConfig` holds the six domain sections in
the existing entity models; `Snapshot` pairs it with the `SecretStore`.
`ConfigStore` owns every write, each one a single transaction that
reads the snapshot, applies the change in the model layer, runs
`check_references`, and only then persists. Rows carry the
model-shaped half only: `set` updates those columns and never the
`secrets` column, deleting an entity deletes its secrets with it, and
`set_secret` and `clear_secret` are the only writes that touch them.
Database failures are normalized to `ConfigError`, with a lock that
could not be taken inside the busy timeout reported as a retryable
"nothing was changed, run it again".

**`verify_secrets`.** Enumerates the stored envelopes and decrypts each
one, so ciphertext with no key, a wrong key, or a corrupt token is a
`ConfigError` naming the entity and the slot. Startup-only by design,
and startup does not call it yet.

**The resolver seam.** `resolve_api_key` answers from the stored
credential for the entry's `api_key` slot before it looks at
`api_key_env`, and `resolve_mcp_values` resolves an MCP server's env
and headers with a stored slot shadowing the `$VAR` written for the
same key. Ciphertext wins in both, and the reference it shadows is not
read at all, so an unset variable behind it cannot fail the boot the
stored secret was set to fix. `build_provider`, `build_agent_providers`
and `McpServers.build` take the store as an optional argument that
defaults to None, which is what every caller passes today.

**`config/cli.py`** and the dispatch in `main.py`. The grammar from the
plan minus `schema` and `reference`, which are milestone 3. Secrets are
read from stdin (`getpass` at a terminal) or `--from-env`, never from
an argument. Every failure is a sanitized `ConfigError` on stderr with
exit code 1, and every mutating command prints the staging notice.

**Tests.** `test_config_checks.py` (the two phases in isolation),
`test_config_store.py` (round trips, the whole refusal matrix, the
natural-order buildup, `verify_secrets`, two concurrent writers),
`test_secret_resolution.py` (a real Anthropic client, a real
openai_compatible client and a real spawned MCP server built from
encrypted credentials), `test_config_cli.py` (the acceptance case
end to end, masking, the staging notice, and the leak surfaces), plus
`SecretStore` cases added to `test_config_secrets.py`.

### Reconciled with milestone 1's review round

This milestone was written against the pre-review schema and rebased
onto the three fixes PR #95's review drove. Two of them reached into
this code.

**The credential reference moved to its own column.** The repository
had folded `api_key_env` into the options JSON, which was the only
place it could go before the column existed. Both directions of the row
mapping now use the column, and a test reads the raw row to pin that
options stays free of it. Nothing else changed: a replacement still
writes the column unconditionally, so clearing a reference by omitting
it from a fragment works.

**Masking now fails closed, including in `show`.** `mask()` passes only
a syntactic environment reference through. The display path applies it
to the value of every secret-shaped key, not just to stored envelopes:
nothing validates the shape of an `api_key_env` value, so an operator
can paste the credential where its variable name belongs, and the
command they would run to find that mistake must not read it back out.
An MCP server's env and headers get the same treatment, which changes
nothing for a valid entry (the model already requires a `$VAR` there)
and covers a value that arrived another way. The rule itself is
single-sourced in `models.is_secret_option` and `models.is_mcp_secret_key`,
which the entity validators use too.

### Deviations from the plan

**`check_completeness` holds one rule, not two.** The plan's strictness
section lists "every agent stage resolving to a provider through the
defaults" alongside the default_agent rule, but that check does not
exist in `_check_references`: it is enforced by `build_agent_providers`,
which the same section says nothing here moves, and moving it would
have changed a boot message. So completeness is today's default_agent
rule, and the stage rule stays in provider construction. Milestone 4
composes `Config` from the snapshot and runs both, exactly as boot does
now.

**The provider credential seam is ambient, not an argument.** Every
provider factory has the signature `(label, config)`, and the
credential is needed inside five of the twelve. Threading an argument
through all of them to be ignored by most would have made the seam
wider than it is, so `build_provider` puts a `ProviderSecrets` value
(stage, name, store) in force for the duration of the one construction
call, in a context variable, the same shape `models.py` already uses
for the YAML path. `resolve_api_key` reads it. The MCP side did not
need this: `McpServerManager` is constructed directly and takes the
store as an argument.

**`ConfigError` joins `ProviderError` as a pass-through in
`build_provider`.** A stored credential that will not decrypt already
raises a message naming the entity and the slot to set again, and the
generic wrapper would have re-labelled it "could not build anthropic
provider".

**`server.database.dir` had to be a real model field.** The plan does
not say whether the CLI could read the key without the server model
knowing it. It could not: `ServerConfig` forbids extra keys, so a
database directory written into the config file would make the server
refuse to boot on the very file that configures its CLI.
`config.example.yaml` gained the commented block in the same commit.

**`show` renders stored secrets as comment lines**, not as a `secrets:`
mapping. A mapping would look like something that could be written
back, and a fragment carrying `secrets:` is refused by the model
anyway (the key is secret-shaped).

**A stored MCP slot with no key in the entity is injected.** The plan
defines slots as `env.<KEY>` and `headers.<KEY>` without saying whether
the key must also exist in the entity. Requiring it would mean writing
a placeholder `$VAR` nobody sets, so a stored slot is added to the
resolved mapping whether or not the entity names it.

**`--config` is accepted before and after the command.** The server
takes it before (`samtal-server --config path`), and every subcommand
convention puts options after. The per-command copy suppresses its
default so the earlier one is not overwritten.

### Resolutions of the plan's open questions

**`config show --json`: no.** The plan asks for a decision in this
milestone if a concrete consumer appears. None did. `show` emits YAML
in the shape the configuration file already has, which is what a person
reads and what a fragment is written in, and the REST API is the
machine interface.

**Read-only loads keep the write lock.** Every transaction on this
engine is `BEGIN IMMEDIATE`, milestone 1's discovery, and the load path
was left on it rather than given a deferred connection of its own. A
load is a handful of small selects; the only readers are a booting
server and a CLI invocation; and a second connection configuration
would be a second thing to keep true (and to reason about at the next
concurrency question) in exchange for contention these never produce.
If a future reader is long-running (a REST API serving reads under
load), that is the moment to revisit it, and the change is local to
`ConfigStore._transaction`.

### Discoveries

**Under WAL, a deferred read-modify-write does not silently lose.**
Swapping the immediate begin for a deferred one and running the
concurrency test, the loser fails immediately with SQLite's
`database is locked` rather than committing a stale-validated write:
in WAL a read transaction that tries to upgrade after another
connection committed is refused outright, and the busy handler does not
run for it. So `BEGIN IMMEDIATE` buys the right behavior rather than
the only safe one: the loser waits, re-reads, and is refused for the
reference it would have left unresolved instead of for a lock. The test
asserts exactly that distinction, and fails on most runs without it.

**Two libraries quote the rejected input back.** `str(ValidationError)`
renders `input_value=...` for every error, and PyYAML's
`MarkedYAMLError.__str__` includes a snippet of the offending source
line. Both are exactly the fragment content that must not be echoed
when the fragment carried an inline secret. Everything here renders
from the parts (`error["loc"]`, `error["msg"]`, `exc.problem` and the
mark) and raises with `from None`. Milestone 3 and 4 surface both
libraries again and have to keep doing the same.

**`McpServerConfig` reads `model_fields_set`**, to tell "my headers are
ignored" from "my headers are wrong". A row therefore has to be loaded
with the other transport's fields omitted rather than passed as None or
empty, or every stdio row fails its own validator.

**SQLAlchemy's error strings carry the statement and its parameters.**
`DBAPIError.__str__` appends `[SQL: ...] [parameters: ...]`, so the
repository never quotes it: the message comes from `exc.orig` (the
sqlite3 text, which is short and parameter-free) or the exception class
name. No plaintext can reach a parameter today (only envelopes are
stored), which is why `hide_parameters` was not turned on as well;
that stays available if a future column ever holds something raw.

**`samtal_server.config` still does not import the database.**
`config/__init__.py` exports neither `store` nor `cli`, and `main.py`
imports the command group inside the branch that dispatches to it, so
serving pulls in neither SQLAlchemy nor Alembic. Worth keeping when
milestone 4 wires the boot path: import the store where boot needs it,
not at package import.

### For the milestones that follow

- The switchover's boot order is already implementable: load the file
  half, `open_database(config.server.database.dir)`, `store.load()`,
  `verify_secrets(snapshot.secrets)`, compose `Config` from the file
  half plus `snapshot.domain`, validate, then pass `snapshot.secrets`
  into `build_agent_providers` and `McpServers.build`. Those two
  arguments are the whole wiring; both already exist and default to
  None.
- The staging notice is `cli.STAGING_NOTICE`, printed by `cli._wrote`,
  and `test_config_cli.py` pins it on writes and its absence on reads.
  Milestone 4 removes the constant, the call and that test together,
  and replaces them with the "applies at the next server start" line.
- The CLI resolves its database directory with `load_config`, so today
  a YAML file whose domain half is invalid also blocks the CLI. That
  converges at the switchover, when the domain half leaves the file;
  until then it is a wrinkle worth knowing if a checkpoint script fails
  in a surprising place.
- `DomainConfig` deliberately duplicates `Config`'s domain fields
  rather than being mixed into it. Milestone 4 decides whether `Config`
  composes from a `DomainConfig` instance or copies its fields across;
  the shared field validators (`check_mcp_entry_names`,
  `normalize_device_bindings`) already make either safe.
- Milestone 3's `--help` text is meant to come from the model field
  descriptions. The CLI's argument help strings are hand-written
  placeholders today (`"the option it fills, such as api_key"`), and
  the fragment-shaped commands (`set ...`) are the ones the plan says
  must derive theirs.

### PR #97 review round

One external review of the pull request's diff: codex CLI 0.147.0,
model gpt-5.6-sol, read-only, 2026-08-11, posted on the PR. Verdict:
mergeable after closing the plaintext paths and the sanitized
boundary's two gaps. Five findings, each fixed with its own commit:

1. **P1: a fragment could persist a plaintext credential.**
   `api_key_env` and the secret-shaped `*_env` extras accepted any
   string, so a credential pasted where its variable name belongs was
   written into the row unencrypted and reported as a successful write,
   and would have been quoted back later by the build-time error saying
   the variable is not set. Fixed in 36241a5: every key ending in
   `_env` must hold an environment variable name (letters, digits and
   underscores, not starting with a digit), refused with a message that
   names the key and shows an example rather than quoting what was
   written. The CLI test that pinned the insecure write as successful
   now pins the refusal, with the credential absent from stdout,
   stderr, the log records and the exception chain.
2. **P2: malformed persisted JSON escaped the sanitized boundary.**
   SQLite enforces no shape on a JSON column, so a row whose `options`
   held a string raised TypeError and one whose `secrets` held a string
   raised AttributeError, neither of which `_transaction` catches, and
   `config list` and `config show` answered with a traceback. Fixed in
   a7064df: every JSON column's container type is checked on load and a
   mismatch is a ConfigError naming the row and the column and never
   the value. Two cases were worse than a traceback and are now
   refused: a `devices` row holding a string bound the device to one
   agent per character, and a `default_agent` value holding an object
   became the `str()` of that object.
3. **P2: MCP credentials were retained as plaintext, and the delivery
   test proved nothing.** The manager decrypted into `_env` and
   `_headers` at construction and held them for the life of the
   process, and the test inspected those attributes, so it would have
   passed with the forwarding removed. Fixed in 4c80892: resolution
   happens inside `_connect`, the constructor still resolves once and
   discards so a missing reference or an undecryptable token fails the
   boot, and both transports are now tested by what arrived: the
   spawned server answers whether its own environment holds the
   expected value, and a local stub records the header the request
   carried.
4. **P2: syntax errors exited 2.** argparse writes to stderr and exits
   from inside `parse_args`, bypassing the ConfigError boundary and the
   documented exit codes. Fixed in 5e9e399: parsing runs inside the
   boundary through a parser subclass whose `error()` raises
   ConfigError, subparsers inherit it, and `--help` keeps exit 0. The
   unrecognized-arguments message is rewritten rather than passed
   through, because argparse names the arguments and the mistake that
   lands there is typing the secret after the slot.
5. **P3: the CHANGELOG advertised a command that does not exist.** The
   entry paired `set` and `delete` over one list of entity kinds, which
   reads as a grammar with `delete agent-defaults` in it. Fixed in
   e82cd48: the two verbs list their own kinds.

Two notes the fixes leave behind.

**Finding 1 changes YAML boot behavior, deliberately.** A pasted value
in an `*_env` key is now refused at parse time rather than at provider
construction. Nothing that worked stops working: the name is looked up
in the environment, and no lookup of a pasted key succeeds, so the
configurations this refuses were already failing at boot. What changes
is where they fail and what the failure says, which is the point, since
the parse-time refusal is the one that can keep the value out of the
message.

**Finding 3 changed one existing test assertion.** Removing the
retained plaintext removes the attributes
`test_a_resolved_secret_reaches_the_spawned_server` named, so it now
asks the resolver the connection asks. The value it asserts and the
property it pins are unchanged.

## Milestone 3: generated documentation

The whole editing pass in one change: `Field(description=...)` on every
domain field, the commented per-entity fragments under
`samtal-server/examples/`, `config/docgen.py` with the JSON Schema and the
markdown reference, `config schema` and `config reference` in front of it,
the `--help` text derived from the same descriptions, the committed
`docs/reference/domain-config.md`, and the CI drift check that keeps it
from rotting. `config.example.yaml` is deliberately untouched, so the
narrative exists in both places during this milestone and the audit below
could be judged with every destination in hand. Nothing about the server's
behavior, the loader, the boot path or the database schema moves.

### What landed

**The descriptions.** Every field of `ProviderConfig`, `ProvidersConfig`,
`McpServerConfig`, `FillerConfig`, `AgentDefaults`, `AgentConfig` and the
six domain sections carries one. The section descriptions live in a single
`DOMAIN_DESCRIPTIONS` mapping in `models.py`, because two models carry
those fields: the composed `Config` the server boots from, and the
`DomainConfig` the repository loads a database into. Server-half fields
are out of scope and keep their comments in `config.example.yaml`.

**The fragments.** Thirteen files under `samtal-server/examples/`: one per
provider type (`llm-anthropic`, `llm-openai-compatible`,
`asr-faster-whisper`, `asr-openai`, `tts-piper`, `tts-elevenlabs`,
`tts-openai`, `vad-silero`), one per MCP transport, `agent`,
`agent-defaults`, and a README explaining that these are fragments to feed
`config set` rather than a configuration the server reads. Each file's
header names the command that installs it, and
`tests/unit/test_config_examples.py` runs every committed fragment through
exactly that command against a scratch database, in the creation order the
reference checks require. The header comment is the test's input, so the
command a reader copies is the command that is known to work.

**`config/docgen.py`.** One source, three renderings: the JSON Schema of
an entity kind or of the whole domain configuration, the markdown
reference, and the field list an argparse epilog carries. The prose no
model can hold (what an entity kind is for, which command writes one, the
singleton semantics, where the examples are) sits beside each entity in
the module. Output is deterministic by construction: no timestamps, no set
iteration, field order is the models' declaration order.

**The commands.** `config schema [entity]` and `config reference`, both
read-only: no `--config`, no database, no encryption key, no staging
notice. The four `set` subcommands gained a generated epilog listing the
fields a fragment may carry with the first sentence of each description,
which is what the issue asks for instead of a second hand-written copy.

**The committed reference and the drift check.**
`docs/reference/domain-config.md` is committed, and the `test` job
regenerates it and diffs it, printing the diff on failure. The workflow's
path filters gained `docs/reference/**` on both push and pull_request, so
hand-editing the committed copy alone also triggers the check rather than
touching nothing under `samtal-server/`. The same comparison is a unit
test as well, so a stale file fails in the suite instead of after a push.
Both directions were replicated locally before the step was committed: the
clean tree passes, and a one-line hand edit to the committed copy fails it.

### The comment audit

Every comment block in `config.example.yaml`'s domain half (providers,
mcp_servers, agent_defaults, agents, devices, default_agent; `memory` is
server-half and excluded), and where it went. Twenty-five contiguous
blocks, holding fifty-one distinct comments once the blocks that document
several commented-out keys in a row are counted apart. "Verbatim" below
means the prose moved with its numbers and findings intact, not that a
sentence was never re-wrapped or re-pointed at its new neighbours.

Destinations in total, over those 51: 15 became or shaped a
`Field(description=...)`, 50 are carried by an example fragment, 9 shaped
reference prose in `docgen.py`, 1 (the devices binding rule, #25) has no
fragment because a device is written with arguments rather than a
document, and none was dropped. The numbers overlap because the split is
the point: a comment documenting both what a field is and why its default
is what it is went to a description and to a fragment.

| # | Lines | Comment | Went to |
| --- | --- | --- | --- |
| 1 | 195-196 | providers section header: named entries, `type` plus options | `ProviderConfig.type` description, `DOMAIN_DESCRIPTIONS["providers"]`, reference "Provider" purpose |
| 2 | 207-212 | openai_compatible egress, and which types reject the key | `ProviderConfig.egress` description; verbatim in `llm-openai-compatible.yaml` |
| 3 | 217-218 | Whisper model sizes, weights download at startup | `asr-faster-whisper.yaml`, verbatim |
| 4 | 220-264 | ten comments: language hint and detection cost, `device`/`compute_type`, beam size, `cpu_threads` and the container quota, `vad_filter` and `vad_parameters`, `condition_on_previous_text`, the temperature ladder, `language_detect`, the confidence floor and fallback; then the cloud-ASR intro | nine verbatim in `asr-faster-whisper.yaml`; the cloud-ASR intro verbatim as the header of `asr-openai.yaml` |
| 5 | 268-317 | four comments: model choice, `base_url` and the 0.1 s floor, the `prompt` vocabulary rule with the 45-of-45 room-tone finding and the field handover incident, the `language` pin with the Swedish misdetection transcript | `asr-openai.yaml`, verbatim |
| 6 | 319-330 | three comments: `temperature`, `timeout_s` and why retries are off, and why there is no `language_detect` here | `asr-openai.yaml`, verbatim |
| 7 | 333-334 | piper-tts is GPL-3.0 and an optional extra | `tts-piper.yaml`, verbatim, with the image variants named |
| 8 | 336-338 | Piper voice names and where they come from | `tts-piper.yaml`, verbatim |
| 9 | 340-344 | two comments: `download_dir`; and "a voice is a provider entry", no per-agent voice option | `download_dir` verbatim in `tts-piper.yaml`; the voice-is-an-entry rule in `ProvidersConfig.tts`'s description, the reference "Provider" purpose, and the fragment's header |
| 10 | 348-354 | ElevenLabs intro: cloud cost, and every option going to the streaming endpoint | `tts-elevenlabs.yaml`, verbatim |
| 11 | 357-380 | `voice_id`, the listing recipe, and the two field findings (stock voices are English-speaker recordings; a professional clone fails at synthesis, not at boot) | `tts-elevenlabs.yaml`, verbatim |
| 12 | 383-413 | six comments: model and the ~75 ms figure, `output_format`, `language_code`, `voice_settings`, `timeout_s`; then the OpenAI-TTS intro | five verbatim in `tts-elevenlabs.yaml`; the intro verbatim as the header of `tts-openai.yaml` |
| 13 | 416-418 | the stock OpenAI voices | `tts-openai.yaml`, verbatim |
| 14 | 421-467 | six comments: `base_url` and egress, the model comparison (~820/1400/1900 ms, ElevenLabs ~190 ms, Piper under 80 ms, 520 to 617 ms per sentence boundary against 111 to 131 ms), `instructions`, `speed`, `timeout_s`, and the absent format option | `tts-openai.yaml`, verbatim |
| 15 | 471-476 | Silero end-of-utterance tuning | `vad-silero.yaml`, verbatim |
| 16 | 478-489 | mcp_servers header: the name becomes a tool prefix and the reserved names; `transport` decides the fields; secrets are `$NAME` only; a server down at startup only warns | `DOMAIN_DESCRIPTIONS["mcp_servers"]`, `McpServerConfig.transport`, `.env` and `.headers` descriptions, reference "MCP server" purpose; verbatim across both MCP fragments |
| 17 | 498-508 | two comments: MCP egress under local_only; `tool_timeout_s` | `McpServerConfig.egress` and `.tool_timeout_s` descriptions; verbatim in both MCP fragments |
| 18 | 536-538 | agent_defaults header, and why there is deliberately no prompt | `DOMAIN_DESCRIPTIONS["agent_defaults"]`, the reference's singleton notes, `agent-defaults.yaml` header |
| 19 | 545-565 | the filler section: what it does, the boot-time synthesis and cache, per-agent replacement, the failure mode, and the 1800 ms default against the ~1.2 s healthy reply and the 2 to 3 s of dead air | `FillerConfig.enabled`, `.delay_ms`, `.phrases` and `AgentDefaults.filler` descriptions; verbatim in `agent-defaults.yaml` |
| 20 | 567-569 | agents header: prompt plus one provider per stage, every stage must resolve | `DOMAIN_DESCRIPTIONS["agents"]`, reference "Agent" purpose, `agent.yaml` header |
| 21 | 572-573 | state the reply language explicitly, or a model picks by training bias | `AgentConfig.prompt` description; verbatim in `agent.yaml` |
| 22 | 579-580 | naming a list replaces the inherited one | `AgentDefaults.mcp` description, the reference's agent-defaults notes; verbatim in `agent.yaml` |
| 23 | 589 | "overriding a default: this one runs on the local model" | `agent.yaml`, as a commented `llm: local` override with its explanation (restored by the PR #99 review round; the first pass had covered only the tts override and the audit overclaimed) |
| 24 | 592 | an empty list opts an agent out of its siblings' tools | `AgentDefaults.mcp` description; verbatim in `agent.yaml` and `agent-defaults.yaml` |
| 25 | 595-598 | devices are bound by MAC, first entry starts the conversation, unknown devices get default_agent | `DOMAIN_DESCRIPTIONS["devices"]` and `["default_agent"]`, reference "Devices" and "Default agent" sections |

Nothing was dropped. Three questions came out of the pass, none of which
this milestone decided on its own:

1. **The file header's own example goes stale at the switchover.**
   `config.example.yaml`'s header (lines 9 to 14, server-half and so
   outside this audit) offers `SAMTAL_DEFAULT_AGENT=assistant` as its
   illustration of the `SAMTAL_` override convention. Milestone 4 turns
   exactly that variable into a boot error naming the move, so the
   header's example has to change with it. Flagged here rather than
   edited, because `config.example.yaml` is untouched in this milestone
   by design.
2. **Should `examples/` carry a second agent?** The example file's
   `storyteller` entry demonstrates a whole-stage override (its own LLM
   and its own voice, and `mcp: []`) next to `assistant`. Its comments
   are preserved in `agent.yaml`, but the two-agent shape it showed is
   not. One `agent.yaml` installed twice under two names says the same
   thing, which is why nothing was added; if a reviewer wants the
   contrast on the page, a second fragment is the answer.
3. **The memory-is-keyed-by-agent-name warning has no home in the
   domain reference.** "Renaming an agent orphans its memory" (lines 521
   to 525) is a `memory:` comment, server-half, and stays in the file.
   It is nevertheless a consequence of an agent's name, which is now
   documented somewhere else entirely, so a reader of the agent section
   will not meet it. Repeating it in the reference means writing prose
   about a server-half section in the domain document; leaving it means
   the warning is one hop further from where the rename happens. Not
   decided here.

### Deviations from the plan

**The fragment inventory is thirteen files, not the plan's twelve.** The
plan's expected list (which it explicitly left to this milestone) named
twelve; `examples/README.md` is the thirteenth committed file, and the
plan asked for it separately. No provider type in the example file went
without a fragment, and none was invented for a type the server does not
implement.

**`docgen` imports `store`.** The whole-domain schema is `DomainConfig`'s,
and that model lives in `store.py`, so `docgen` imports the repository
module and with it SQLAlchemy. Nothing is opened: the import is for the
model class. `samtal_server.config` still does not import either, and
`main.py` still imports the command group lazily, so serving pulls in
neither.

**Two entity kinds in the reference have no `config set` command.** The
plan's entity list is the fragment-shaped ones. `FillerConfig` is
documented as an entity anyway, because it is a nested shape a fragment
has to get right and its fields would otherwise appear nowhere, and
`devices` and `default_agent` get sections of their own built from their
field descriptions plus the commands that write them.

**Provider stage groups are documented through the domain table rather
than as an entity.** `ProvidersConfig` has no section of its own: its four
fields are one row each in the whole-configuration table, and the Provider
entity section carries the stage explanation. A fifth entity kind whose
only content is "these are the four stages" would have been a section to
scroll past.

### Discoveries

**A pipe ends a markdown table cell even inside a code span.** Every
optional field renders as `str | null`, so the generated tables were
silently splitting into extra columns until the type cell was escaped the
same way the description cell already was. Worth knowing for any later
generated table: backticks do not protect a pipe in GFM.

**`AgentConfig`'s own field renders last.** Pydantic orders a subclass's
fields base-first, so `prompt` appears after the six inherited stage
fields in every rendering. Left alone: reordering means moving `prompt`
into a base or restating the fields, and both cost more than the reading
order is worth.

**The example fragments are a test fixture as well as documentation.**
Running each one through the command in its own header found nothing
broken, but it pins something the reference cannot: that the creation
order the fragments imply (providers, MCP servers, agent defaults,
agents) really does pass the write-time reference checks, which is the
first thing a new deployment does.

**The two libraries that quote input back are still quiet.** Milestone
2's discovery holds here: `config schema` and `config reference` parse
nothing, so neither `ValidationError` nor PyYAML is on their path at all,
and the new `set` epilogs are built from descriptions rather than from
anything a user typed.

### For the milestones that follow

- The reference carries a staging paragraph of its own, saying the server
  does not read the database yet, and naming itself as the paragraph the
  switchover removes. It goes with `cli.STAGING_NOTICE`, in `docgen`'s
  `reference()`, and the committed copy is regenerated in the same
  change.
- The switchover's removal pass in `config.example.yaml` is now a pure
  deletion of lines 195 to 513 and 536 to 605, plus the header rewrite.
  Every comment in those ranges has a live destination, listed above, and
  `memory:` (515 to 534) stays where it is, between them.
- The header question above (`SAMTAL_DEFAULT_AGENT` as the example of the
  override convention) is a change milestone 4 has to make anyway, since
  that variable becomes a boot error. Picking a server-half example
  (`SAMTAL_SERVER__PORT`, already in the same sentence) is the whole fix.
- `README.md` and the deployment notes are not yet pointed at
  `docs/reference/domain-config.md` or at `samtal-server/examples/`.
  Milestone 4 owns the documentation sweep, and those two links are part
  of it.
- The drift check runs `uv run samtal-server config reference` from
  `samtal-server/`, so the command has to keep working from a plain
  `uv sync` with no database and no key. Anything milestone 4 adds to the
  CLI's startup path (a database open at import, say) would break the
  documentation lane rather than a config test, which is a confusing
  place to see it fail.
- `DOMAIN_DESCRIPTIONS` is what keeps `Config` and `DomainConfig`
  describing the same six sections. If milestone 4 composes `Config` from
  a `DomainConfig` instance rather than copying its fields across, the
  mapping stays useful; if it deletes those fields from `Config`, the
  mapping has exactly one consumer left and could move into `store.py`.

### PR #99 review round

One external review of the pull request's stacked diff (b9704e0):
codex CLI 0.147.0, model gpt-5.6-sol, read-only, 2026-08-11, posted
verbatim on the PR by the review run itself. Verdict: mergeable after
fixing the premature switchover wording and restoring the dropped
local-model override. Four findings, each fixed with its own commit
after the branch was rebased onto the merged milestones:

1. **P2: restart behavior documented as live before the
   switchover.** The reference's staging paragraph was contradicted
   further down by "a change takes effect at the next server start",
   and the examples README had no staging qualification at all.
   Fixed: both statements are qualified as post-switchover behavior,
   the README carries the staging paragraph with the same removal
   marker, the reference is regenerated.
2. **P2: the local-model override was dropped despite the audit.**
   Audit item 23 claimed the "runs on the local model" comment moved
   to `agent.yaml`, but the fragment only showed the tts override,
   so the zero-dropped claim was wrong by one. Fixed: `agent.yaml`
   gains a commented `llm: local` override with its explanation, and
   the audit row records both the destination and the overclaim.
3. **P3: the MCP audit overstated verbatim coverage.** The
   streamable_http fragment carried a shortened egress comment while
   the audit said both fragments held the rationale verbatim. Fixed:
   the HTTP fragment carries the conversation-derived-data rationale
   and the explicit meaning of `egress: false`.
4. **P3: a generated 96-character prose line.** The fragment-examples
   sentence broke the reference's wrapping convention. Fixed: the
   link sits on its own line, reference regenerated.

## Milestone 4: switchover and docs

The database becomes the source the domain half boots from. `Config` is
composed rather than loaded, a domain key left in the file or in the
environment refuses the boot naming where it moved, the example files
and the smoke lane shrink to their server halves, the staging notices
go, and the documentation says how a deployment configures and backs up
the thing it now depends on.

### What landed

**The split.** `FileConfig` is the settings model, `server` and `memory`
only, with today's `SAMTAL_` environment behavior intact. `Config` stops
being a `BaseSettings` and becomes a plain model composed from the file
half plus a domain snapshot, keeping its name, its attribute paths, its
helper methods and its boot-time validator, so `app.py` and every call
site and test that builds a `Config(...)` directly is unchanged.
`compose_config` is the one place the two meet, and it renders a failure
through the loader's existing formatter with the database named as the
source.

**`config/boot.py`.** The boot order milestone 2 spelled out, in one
function: load the file half, `open_database(server.database.dir)`,
`store.load()`, `verify_secrets`, compose, validate. It returns the
composed `Config` and the `SecretStore` beside it, and disposes the
engine: the configuration is a boot-time snapshot, so nothing after this
reads the database. `create_app` takes the store as a second argument
and hands it to `build_agent_providers` and `McpServers.build`, which is
the whole wiring, both arguments having existed since milestone 2.

**The moved-key refusals.** The file half is checked in the loader's
existing pre-flight, where the parsed top level is already in hand; the
environment is scanned explicitly for the six names, bare and
`__`-nested. Both messages name the key, the command that writes it now,
and the reference. One test per key for each route, plus one pinning
that the value-carrying variables (`SAMTAL_CONFIG`, `SAMTAL_MASTER_KEY`,
`SAMTAL_AUTH_SECRET`) are outside the scan.

**The example files.** `config.example.yaml` is the pure deletion
milestone 3 measured (lines 195 to 513 and 536 to 605), with `memory:`
between them untouched, plus the header rewrite and two comment fixes
the deletion forced: the `database:` block no longer says the server
does not read it, and the `local_only` comment points at the reference
rather than at sections that are no longer in the file.

**The integration lane.** Its conftest gained `booted(config)`: the
domain half of the `Config` a test wrote is dumped as fragments,
written through the repository into a scratch database in the order the
reference checks require, read back, and composed onto the file half
again. Every scenario in the lane therefore covers the round trip, and
no assertion moved. `test_app_boot.py` now boots through the
module-level `app`, which reads both halves itself, against an empty
database directory.

**The smoke lane.** The three `tests/smoke/config*.yaml` files shrink to
their server halves, and each check gained a seeding script
(`seed.sh`, `seed-slim.sh`, `seed-local-engines.sh`) that writes its
domain half through the CLI. CI runs each one from the image being
tested, onto the named volume the container then reads, so the seeding
exercises the shipped artifact; the image sets
`SAMTAL_SERVER__DATABASE__DIR=/data/db`.

**The documentation.** The server README's configuration section
describes both halves, walks a deployment from an empty database, and
points at the generated reference and the fragments; its domain YAML
blocks became the `config set` commands that write them. A new
deployment subsection covers the master key, rotation, WAL-safe backups,
restores and the restart semantics, in the plan's own wording. The root
README gains the step between running the server and flashing a board.

### Deviations from the plan

**`load_config` is gone rather than renamed in place.** The plan says
the file half becomes a settings model without saying what the existing
entry point returns. Keeping the name for a function that returns only
half of a configuration would have been the kind of thing a call site
gets wrong once: `load_file_config` returns the file half,
`load_boot_config` returns both, and the compiler-shaped failure of a
stale call is an import error rather than a missing attribute at
runtime.

**Boot lives in `config/boot.py`, not in `loader.py`.** Milestone 2
noted that `samtal_server.config` imports neither the store nor the
database, and that serving should keep pulling in neither at package
import. Serving now needs both, but `config schema` and `config
reference` still do not, and the documentation lane runs the latter from
a plain sync. A separate module keeps the package import free of
SQLAlchemy for everything that is not a boot.

**`config.deploy.example.yaml` was not in milestone 3's audit, and its
domain half is not deleted.** The audit covered `config.example.yaml`.
The deployment profile carries its own field-measured values
(`cpu_threads: 3` against the container quota, the `language_detect`
ladder, the Piper voice, the VAD tuning), none of which has a
destination in `examples/`, because the fragments document the options
generically and this file documents one deployment's choices. Its
domain half therefore became the `config set` commands that write it,
comments and values intact, at the bottom of the same file. Nothing was
dropped, and the file is still valid YAML for the server half.

**Two smoke-lane tests moved rather than being edited.**
`test_the_slim_boot_config_names_no_local_engine` and
`test_the_local_engine_config_really_names_one` read the smoke YAML
files, which no longer hold providers. They live in
`tests/unit/test_smoke_seeds.py` now, running each seeding script
against a scratch database and reading the result back through the
repository, with their assertions unchanged. That also pins something
the old tests could not: that the scripts work at all, in an order the
write-time reference checks accept.

**Three test bodies lost assertions whose subject was deleted.**
`test_example_config_parses` asserted the example file's providers,
agents and device bindings, `test_deploy_example_config_parses` its
whisper options and its allowlist, and `test_any_top_level_key_is_env_overridable`
used `SAMTAL_DEFAULT_AGENT`, which is now a boot error. The first two
keep their server-half assertions; the third is replaced by the
moved-environment tests, which pin the same variable with the opposite
expectation. No assertion about behavior changed value.

**The checkpoint workflow needed one sentence, not a document.** The
plan expects the `*.local.yaml` checkpoint workflow to become a script
of `config set` calls and the device checkpoint docs to follow.
`docs/xiaozhi-notes.md` turns out to document the upstream server's
configuration, not samtal-server's, and the only place the workflow is
described is one sentence in the server README, which now says the
domain half of a local experiment is a short script of `config set`
calls against a database directory of its own.

### Resolutions of milestone 3's open questions

**1. The file header's `SAMTAL_DEFAULT_AGENT` example: replaced with a
server-half key.** The header now illustrates the override convention
with `SAMTAL_SERVER__PORT=9000` and `SAMTAL_SERVER__LOG_LEVEL=DEBUG`.
The variable it used to name is one this milestone turns into a boot
error, and the header is the last place a reader should meet a variable
that refuses the boot.

**2. A second agent fragment: no.** The `llm: local` commented override
the PR #99 review round restored to `agent.yaml` carries the contrast
the example file's two agents showed (an agent overriding a stage its
siblings inherit). A second file saying the same thing under another
name would be one more document to keep true.

**3. The memory-orphaning warning: kept in the file, pointed at from the
reference.** "Renaming an agent orphans its memory" is a `memory:`
comment about a server-half section, so it stays in
`config.example.yaml` with the section it belongs to. It is also a
consequence of an agent's name, which is now documented in a different
place, so the reference's agent section gained one sentence saying so
and linking to it. A pointer costs a sentence; meeting the warning by
accident costs a persona's accumulated facts.

### Discoveries

**Composition must pass models, never a dump.** `McpServerConfig` reads
`model_fields_set` to tell "my headers are ignored" from "my headers are
wrong", so a snapshot round-tripped through `model_dump()` and
re-validated fails its own validator for every stdio entry. `Config`
takes the loaded model instances themselves, which pydantic accepts
without revalidating, and the integration lane's fragments are dumped
with `exclude_unset=True` for the same reason.

**A test helper that seeds through the repository would have changed
assertions; composing does not.** `load_config_from_data` in
`tests/unit/test_config.py` was rewritten to split the mapping and
compose the halves, rather than to write the domain half through the
CLI. Through the store, the per-entity validation errors arrive in the
repository's message shape (`invalid agent_defaults:\n  - prompt: ...`)
rather than the loader's (`  - agent_defaults.prompt: ...`), which would
have meant editing assertions to match a message that had moved. Boot
validates the composed model, so composing is also the honest route.

**`create_app()` with no config now opens a database**, which is what
turned four unit tests red the first time the lanes ran: two health
tests and one app test called it because they did not care about the
configuration, and they now pass a `Config()` or point
`SAMTAL_SERVER__DATABASE__DIR` at a tmp path. Worth knowing before
adding a test that builds the default app: the default database
directory is `/var/lib/samtal`, and a laptop refuses it.

**The refusal has to be readable from inside the trap it describes.** A
deployment upgrading with a domain-bearing file gets the boot error, and
the CLI it names reads the same file for `server.database.dir`, so it is
refused too. The messages therefore say to remove the sections from the
file, which is what the migration requires anyway; the alternative, a
lenient read for the CLI only, would be a second place that decides what
the file may contain.

### Verification

Lint, unit and integration lanes green locally, and the drift check
(`uv run samtal-server config reference | diff - ../docs/reference/domain-config.md`)
clean. The smoke lane and the image cannot run locally: they need the
built image, and the seeding steps are written against what the image
sets rather than tried. CI is the first run of both.
