# DB-backed domain configuration plan

## Goal

Implement issue #86: move the domain half of the configuration
(providers, mcp_servers, agent_defaults, agents, devices,
default_agent) out of the YAML file into SQLite, encrypt stored
secrets at rest under a master key from the environment, put a
`samtal-server config` CRUD command group in front of it as the
write path, and replace the example file's domain comments with
documentation generated from the models.

The issue's six decisions are settled and this plan does not
re-litigate them; it makes them concrete. The issue's open
questions are resolved below, each with its reason. Configuration
remains a boot-time snapshot: the server reads the database once at
startup into the same validated pydantic shape it uses today, and a
database edit takes effect at the next restart. The CLI is how a
deployment is configured while it runs; the restart is when the
edit is picked up. Hot apply arrives with the REST API (decision
5), not here, and the deployment notes say so out loud, because a
config edit that silently does nothing until a restart is the same
trap as an orchestrator config change without a rollout.

The companion implementation doc,
[`2026-08-11-db-domain-config-implementation.md`](2026-08-11-db-domain-config-implementation.md),
records what each milestone actually did, with deviations from this
plan, resolutions of its open questions, and discoveries; a
milestone with no deviations says so explicitly.

## The six decisions, restated for reference

From issue #86, fixed, one line each:

1. The split is server vs domain: `server:` and `memory:` stay
   file/env backed through pydantic-settings; providers,
   mcp_servers, agent_defaults, agents, devices and default_agent
   move to the database.
2. SQLite, through SQLAlchemy with Alembic migrations from day one.
3. Secrets at rest are either environment references (today's
   `api_key_env` and `$VAR` forms) or Fernet ciphertext under
   `SAMTAL_MASTER_KEY`; missing or wrong key with encrypted secrets
   present refuses boot; MultiFernet keeps rotation designed in.
4. The YAML domain sections are removed, no importer; the write
   path is a `samtal-server config` command group taking YAML
   fragments for nested entities and direct commands for flat
   operations, a deliberate rehearsal of the REST API.
5. Validation stays in the pydantic layer; writes are validated
   strictly, including cross-references; boot still validates the
   whole snapshot as the last line of defence.
6. Documentation is generated outward from `Field(description=...)`
   on the models; long-form narrative lives in richly commented
   per-entity example fragments; migrating the existing comments is
   a deliberate editing pass where nothing is dropped silently.

## Resolved open questions

### The database path: `server.database.dir`

The database lives on the data volume alongside the memory
directory: a `database` section in the server half with a `dir`
key, default `/var/lib/samtal`, holding one SQLite file named
`samtal.db` inside it. The directory rather than the file is the
key, mirroring `memory.dir` and `capture.dir`, and it leaves room
for database-adjacent artifacts later without a second path key.

`/var/lib/samtal` is the generic model default, not what the
container runs with: the image runs as an unprivileged user whose
writable volume is `/data` (`Dockerfile`), so the image sets
`SAMTAL_SERVER__DATABASE__DIR=/data/db` in its environment, the
same way its other paths already live on the volume. On a
development machine `/var/lib/samtal` is usually not writable
either; opening the database creates the directory when it can and
otherwise fails with an error naming `server.database.dir`, so the
failure tells you which key to point somewhere writable
(`SAMTAL_SERVER__DATABASE__DIR=./var` or the config file).

Backups are taken with SQLite's own mechanisms, never by copying
the live file: under WAL a plain copy of `samtal.db` can miss
committed data still sitting in the `-wal` file. The deployment
notes prescribe `sqlite3 samtal.db "VACUUM INTO 'backup.db'"` (or
the `.backup` command) against the live database, or a plain copy
only of a stopped, checkpointed one. A restore needs the backup
file and every MultiFernet key still required to decrypt what it
holds, so the keys are escrowed wherever the deployment keeps its
environment secrets, separate from the backup. The claim is scoped
honestly: a copy of the database alone exposes no stored plaintext
secret, and it does expose the rest of the domain configuration
(prompts, endpoints, environment variable names), which is why the
file still belongs on the data volume and in access-controlled
backups, not in a repository.

### agent_defaults stays a singleton

One row, fixed key, exactly the semantics of today's
`agent_defaults` section. Per-family modelling is not built and not
half-built: when the family stage arrives it re-keys the table with
a migration, which Alembic exists for. The singleton behavior is
documented in the generated reference (an agent that names no
provider for a stage inherits the singleton's entry; a list field
such as `mcp` replaces rather than extends, as today).

### Write-time strictness, without an ordering deadlock

Writes are validated strictly, and the check set is chosen so that
a server started with almost nothing can be built up through the
CLI without ever being wedged:

- **Write-time checks**: entity shape (the existing pydantic
  models, verbatim) and reference resolution. An agent naming an
  unknown provider is refused; deleting a provider or MCP server an
  agent still references is refused; binding a device to an unknown
  agent is refused; setting default_agent to an unknown agent is
  refused. This forces the natural creation order (providers, MCP
  servers, agents, devices) and is the semantics the REST API
  inherits.
- **Boot-only checks**: deployment completeness. "default_agent is
  required when agents are defined and no device is bound" is a
  statement about a runnable server, not about a valid entity, and
  enforcing it at write time would deadlock the natural order (the
  first agent cannot exist before default_agent names it, and
  default_agent cannot name it before it exists). Completeness
  stays where it is today, in the boot validator, with the same
  error format.

The mechanism is not "the current validator minus something",
because the current `Config._check_references` mixes both kinds in
one model validator and provider construction enforces more
completeness afterwards. The checks become named, independently
tested functions over the domain snapshot, and each caller states
which it runs:

- `check_shape`: the per-entity pydantic models, exactly as today
  (this is parsing, not a separate pass).
- `check_references`: every reference resolves; agents to
  providers and MCP servers, device bindings to agents,
  default_agent to an agent. Run at write and at boot.
- `check_completeness`: the runnable-server rules; today's
  "default_agent is required when agents are defined and no device
  is bound", every agent stage resolving to a provider through the
  defaults. Run at boot only.
- Deployment checks (`local_only` egress, provider construction,
  auth secret presence) stay where they live today, downstream of
  the composed config; nothing here moves them.

The refactor splits the existing `_check_references` into the
reference and completeness halves without changing a message; the
boot path runs both halves in sequence, so the composed `Config`
validates exactly what it validates today. Every write loads the
current snapshot, applies the change in the model layer, runs
`check_references`, and only then persists. Deleting an agent
still referenced by a device binding or by default_agent is
refused by the same pass, and the write-time refusal test matrix
includes both cases. One validation code path serves the CLI today
and the REST API later.

## Planned for, not built: the REST API refactor

When the REST API exists the CLI becomes a thin client of it, so
this step keeps the seam clean now:

- The repository is the semantics layer: validation, reference
  checks, masking rules, secret handling all live there, never in
  CLI argument handling. The API will mount the same repository
  behind HTTP; the CLI then swaps its backend from "open the
  database" to "call the API" without its command grammar changing.
- The command surface is designed as the API rehearsal (decision
  4): one entity kind per noun, YAML fragments as the write
  payload in the same shape the API will accept as JSON, secrets
  write-only with masked reads.
- Nothing in the CLI holds state beyond one invocation, and no
  command's meaning depends on being co-located with the database
  file, except the explicitly local plumbing (`schema`,
  `reference`) which reads models, not data.

## Room for the per-device runtime field (#92 stage 1)

Devices are stored as an entity table with one row per device, not
as bare binding association rows. The row today carries the MAC and
the ordered agent list; a per-device `runtime` column (#92 stage 1,
sibling conversation runtimes) is then an additive migration plus a
model field, with no reshaping of existing rows. Nothing
runtime-shaped is built now: no column, no model field, no CLI
flag. The room is the entity-row shape itself.

## Module layout

```
samtal_server/db/
    __init__.py     open_database(dir): engine, pragmas, migrate
    schema.py       SQLAlchemy metadata: the domain tables
    migrations/     Alembic environment and versions, shipped
                    inside the wheel
samtal_server/config/
    models.py       existing models; domain models gain
                    Field(description=...) in milestone 3
    loader.py       the file half (server, memory) only, plus the
                    moved-key boot errors
    secrets.py      the envelope: env and encrypted forms,
                    MultiFernet key handling, masking
    store.py        the repository: rows to pydantic models and
                    back, write validation, snapshot load, boot
                    key verification
    docgen.py       JSON Schema and the markdown reference
    cli.py          the samtal-server config command group
```

`main.py` gains subcommand dispatch: bare `samtal-server` serves as
today, `samtal-server config ...` runs the command group and exits.

## The schema

Baseline Alembic migration, one revision, these tables:

- `providers`: `stage` and `name` (composite primary key), `type`,
  `api_key_env` (nullable text: the declared environment-reference
  credential field, which is not a model extra and therefore needs
  its own column), `egress` (nullable boolean), `options` (JSON:
  the non-secret pass-through keys, exactly today's extras),
  `secrets` (JSON: option name to encrypted envelope, empty by
  default).
- `mcp_servers`: `name` (primary key), `transport`, `command`,
  `args` (JSON), `env` (JSON), `url`, `headers` (JSON), `egress`,
  `tool_timeout_s`, `secrets` (JSON, empty by default). Values
  inside `env` and `headers` are literal strings or today's `$VAR`
  reference strings, never envelopes; encrypted values live in the
  `secrets` column keyed by dotted path (`env.API_ACCESS_TOKEN`,
  `headers.Authorization`), so the row's model-shaped half loads
  into `McpServerConfig` unchanged.
- `agent_defaults`: fixed single row; `llm`, `asr`, `tts`, `vad`
  (nullable text), `mcp` (nullable JSON list), `filler` (nullable
  JSON).
- `agents`: `name` (primary key), `prompt`, and the same override
  columns as agent_defaults.
- `devices`: `mac` (primary key, normalized form), `agents` (JSON,
  the ordered binding list). See the runtime-room section above.
- `domain_settings`: `key` (primary key), `value` (JSON). Holds
  `default_agent`; room for future domain-level scalars without a
  migration per scalar.

Typed columns carry identity and references; JSON carries the
nested structures the pydantic models already own (provider
options, filler sections, args, env, headers, binding lists).
Referential integrity is enforced in the repository, not with
SQLite foreign keys: validation is single-sourced in the
model/repository layer (decision 5), the same layer the REST API
will use, and SQLite foreign-key enforcement is a per-connection
pragma that would duplicate half of those checks in a second,
weaker place. The database opens in WAL mode with a busy timeout,
so a CLI write while the server holds the file open does not fail;
the server does not observe it until restart, by design.

WAL and a busy timeout are liveness, not atomicity: the
read-modify-validate-write sequence every CLI write performs must
be one serialized transaction, or two concurrent invocations can
each validate against the pre-change snapshot and persist writes
that are individually valid and jointly not (both deleting what
the other's write references, or one losing the other's update).
Every repository write therefore runs inside a single transaction
opened with `BEGIN IMMEDIATE`, taking the write lock before the
snapshot is read, so the read, the reference check, and the
persist see and produce one consistent state; a lock that cannot
be taken within the busy timeout fails the command with a
retryable error rather than half-applying. Migration is
serialized the same way: the upgrade-on-open runs under an
immediate transaction too, so two processes opening a fresh
database do not race the baseline migration; whichever loses the
lock finds the schema already current. Both properties are
tested with two concurrent writers.

Migrations run programmatically (Alembic's command API against the
scripts packaged in `samtal_server/db/migrations/`) whenever the
database is opened, by the server at boot and by the CLI. A fresh
file migrates from empty to current in one step; there is no
separate init command to forget.

## The secret envelope

A stored secret value has exactly the two forms decision 3 names:

- **Environment reference**, carried over verbatim from today:
  `api_key_env: ANTHROPIC_API_KEY` in provider options, `$VAR`
  values in MCP `env` and `headers`. Fragments accept only this
  form for secret-shaped keys, enforced by the same validators
  that guard the YAML file today, so plaintext never passes
  through a file.
- **Ciphertext**: a JSON object envelope (`{"enc": "..."}`) holding
  a Fernet token, written only by `config set-secret` (and later
  the REST API), stored in the entity row's `secrets` column, keyed
  by the credential slot it fills (a provider's `api_key`, an MCP
  server's `env.API_ACCESS_TOKEN` or `headers.Authorization`).
  Fernet authenticates the token but not where it belongs, so the
  encrypted payload is not the bare secret: it is a small JSON
  document carrying the secret and its canonical location (entity
  kind, identity, slot). Decryption verifies the location matches
  the slot being resolved and refuses a mismatch, so a valid
  token copied to a different row (say, into an
  attacker-controlled MCP server's headers) does not decrypt into
  a credential it was never set for.

The pydantic domain models never carry an envelope or a decrypted
value, deliberately: `ProviderConfig` rejects secret-shaped extras
and `McpServerConfig` requires `$VAR` for secret-shaped env and
header keys, and those validators keep guarding fragments exactly
as they guard the YAML file today. The persistence representation
for ciphertext is therefore separate from the models: the loaded
snapshot pairs the model-shaped domain configuration with a
`SecretStore`, a mapping from (entity kind, entity identity,
slot) to envelope, carried alongside the models and never inside
them, so "writes validate through the existing models" stays true
without weakening a validator.

Point of use has an explicit resolver seam, and it is the seam the
code already has: providers resolve credentials through
`resolve_api_key` (`providers/openai_endpoint.py` and friends) and
MCP servers resolve `$VAR` values through `resolve_env_references`
(`tools/mcp.py`). Both gain the secret store as a second source,
injected at boot where `build_agent_providers` and
`McpServers.build` run: a slot with a stored envelope decrypts
there, at construction, and the transient plaintext goes straight
into the provider client or the spawned process environment
without ever landing on a pydantic model. The unit tests for this
build a real provider and a real MCP manager from encrypted
credentials, not just round-trip the envelope.

`SAMTAL_MASTER_KEY` holds one or more Fernet keys, comma-separated,
newest first, wrapped in MultiFernet: encryption always uses the
newest key, decryption tries them in order. What this release
supports is adding a new key; it does not support retiring an old
one. Until the deferred re-encrypt command exists (deferred until
rotation is actually needed, per the issue), only newly written
secrets use the new key, so every old key must stay in
`SAMTAL_MASTER_KEY` for as long as any token written under it
remains in the database, and the deployment notes say exactly
that. The interim workaround is re-running `set-secret` for each
stored secret, which rewrites its token under the newest key.

Boot-time key check, same fail-at-boot pattern as `auth.enabled`,
and deliberately not part of opening the database: `open_database`
only opens and migrates, so the CLI keeps working when the key is
missing, wrong, or a token is corrupt, which is exactly when the
CLI is the recovery tool (`show` stays masked and needs no
decryption, `delete` and `clear-secret` remove what cannot be
read, a fresh `set-secret` replaces it). The exhaustive check is
`verify_secrets`, run by server startup: it enumerates the stored
envelopes, and if any ciphertext exists with no key configured, or
any stored token fails to decrypt under every configured key, the
server refuses to start with an error naming the entity and slot.
`set-secret` is the one CLI command that requires a valid
encryption key (it cannot write without one); everything else
touches ciphertext only as opaque data. The verification pass
discards plaintext immediately; materialized secrets exist only at
the point of use (building a provider, connecting an MCP server),
are never stored on a model, and are never logged.
`config show` and `config list` render an encrypted value as a
fixed mask and an environment reference as the reference itself,
which is not a secret.

The error paths hold the same line as the happy path:

- `set-secret` reading from an interactive terminal uses no-echo
  input (`getpass`); a pipe or redirect reads plainly, which is
  what scripts use. The value never appears in an argument.
- Every database, migration, malformed-envelope, cryptography, and
  fragment-validation failure surfaces through `ConfigError` with
  a message that names the location and the kind of failure and
  never embeds the rejected value; raw tracebacks from the
  underlying libraries do not reach the CLI user or the boot log.
- The SQLAlchemy engine runs with statement echo off and parameter
  logging never enabled, so a secret bound into an INSERT cannot
  ride a debug log line.
- Tests cover the leak surfaces directly: for invalid fragments,
  malformed envelopes, database failures, and wrong keys, the
  assertions read stdout, stderr, captured log records, and the
  exception chain, and require the plaintext (and for fragments,
  the rejected input) absent from all of them.

## Repository and Config composition

`store.py` owns both directions:

- **Load**: read all rows, build the domain half of the
  configuration with the existing pydantic models (`ProviderConfig`
  and friends, unchanged in shape). The result is a
  `DomainConfig` model holding providers, mcp_servers,
  agent_defaults, agents, devices, default_agent, paired with the
  `SecretStore` described in the envelope section, which rides
  beside the models and never inside them.
- **Write**: parse the fragment or arguments through the same
  models, apply to the current snapshot, run the write-time
  validation set (above), persist.

`Config` stops being one big `BaseSettings`. The file half becomes
a settings model owning `server` and `memory` with today's
`SAMTAL_` env behavior intact (`SAMTAL_SERVER__PORT` keeps
working); `Config` becomes the composition of the file half plus
the injected domain snapshot, keeping its name, its attribute
paths (`config.providers`, `config.agents`, ...), its helper
methods (`provider_for_agent`, `mcp_for_agent`,
`agents_for_device`, ...), and its boot-time cross-reference
validator, so `app.py` and the call sites do not change shape.
Boot order: load the file half, open the database, verify the
stored secrets against the configured keys, load the snapshot,
compose, validate, build the app.

A domain key left in the YAML file is a boot error naming where it
moved (`providers: moved to the database; write it with
samtal-server config set provider ...`), raised from the loader's
existing pre-flight check where the parsed top level is already in
hand. A `SAMTAL_`-prefixed environment override for a domain key
gets the same treatment, and not through pydantic:
pydantic-settings' environment source looks up known fields and
ignores unmatched prefixed variables even under `extra="forbid"`,
so a stale `SAMTAL_DEFAULT_AGENT` would silently stop applying.
The loader therefore scans `os.environ` itself for the removed
domain names under the `SAMTAL_` prefix, bare and in their
`__`-nested forms (`SAMTAL_PROVIDERS__...`, `SAMTAL_AGENTS__...`,
`SAMTAL_MCP_SERVERS__...`, `SAMTAL_AGENT_DEFAULTS__...`,
`SAMTAL_DEVICES__...`, `SAMTAL_DEFAULT_AGENT`), and raises the
same moved-to-the-database error naming the variable. Reserved
non-config variables (`SAMTAL_CONFIG`, `SAMTAL_MASTER_KEY`, and
value-carrying variables a config key references by name, such as
`SAMTAL_AUTH_SECRET`) are outside the scan by construction, since
the scan matches domain key names only. One test per removed
domain prefix pins the scan.

The `local_only` egress check, provider building, MCP connection,
and everything downstream read the composed `Config` exactly as
today; the only difference visible to them is where the domain
half came from. Boot order places `verify_secrets` between opening
the database and building providers, so a bad key fails with the
naming error rather than a decryption traceback mid-build.

## The CLI

`samtal-server config <command>`, argparse subparsers, exit code 0
or 1 with errors on stderr in the loader's existing error format.
The grammar, one noun per entity kind:

```
config set provider <stage> <name> -f fragment.yaml   # or - for stdin
config set mcp-server <name> -f fragment.yaml
config set agent <name> -f fragment.yaml
config set agent-defaults -f fragment.yaml
config delete provider <stage> <name>
config delete mcp-server <name>
config delete agent <name>
config delete device <mac>
config bind-device <mac> <agent> [<agent> ...]
config set-default-agent <name>
config clear-default-agent                # back to devices-as-allowlist
config set-secret provider <stage> <name> <slot>      # value from
config set-secret mcp-server <name> env.<KEY>         # stdin, or
config set-secret mcp-server <name> headers.<KEY>     # --from-env VAR
config clear-secret provider <stage> <name> <slot>
config clear-secret mcp-server <name> <dotted-key>
config list                                           # summary tree
config show                                           # everything
config show provider <stage> <name>                   # one entity,
config show mcp-server <name>                         # masked YAML
config show agent <name>
config show agent-defaults
config show device <mac>
config schema [<entity>]                              # JSON Schema
config reference                                      # the markdown
                                                      # reference, to
                                                      # stdout
```

Every entity is addressed the way its identity is keyed: a
provider is `(stage, name)` everywhere it is named (`set`,
`delete`, `show`, `set-secret`, `clear-secret`), never by name
alone. `clear-default-agent` exists because omitting default_agent
is a meaningful configuration (the devices map as the allowlist,
per today's validator), and without it that state would be
unreachable once set; clearing it is refused by the boot-only
completeness check no more than setting it late is, and the
write-time reference pass simply no longer has a reference to
check. `clear-secret` is the inverse write `set-secret` needs for
recovery and rotation hygiene.

Fragments are the same YAML shape as today's sections, one entity
per invocation, validated by the same models: `config set provider
llm claude -f claude.yaml` takes exactly what today's
`providers.llm.claude` mapping holds. `set` is create-or-replace
(the row is the whole entity, as the fragment is), which is also
the REST API's PUT semantics. Secret-bearing keys in a fragment
accept environment references only, exactly like today's
validators; plaintext enters through `set-secret` reading stdin or
a named environment variable, never through a file or an argument
(arguments land in shell history).

`set` replaces the entity, not its stored secrets. A fragment
cannot contain ciphertext by design, so whole-row replacement that
covered the `secrets` column would silently erase every stored
secret on any ordinary edit; instead the model-shaped half of the
row is replaced and the `secrets` column is untouched, modified
only by `set-secret` and `clear-secret`. The supported slots are
defined, not arbitrary: for a provider, a slot is a secret-shaped
option name (the `_SECRET_KEY_FRAGMENTS` rule the models already
use, `api_key` being the common case), and the resolver hands it
to the factory through the same `<slot>_env`-shaped seam
(`resolve_api_key`); for an MCP server, a slot is a dotted
`env.<KEY>` or `headers.<KEY>` path. When both forms exist for
the same slot (an `api_key_env` reference in the entity and a
stored ciphertext), the ciphertext wins, because `set-secret` is
the later and more deliberate act; `show` marks the shadowed
environment reference so the precedence is visible rather than
silent. Deleting the entity deletes its stored secrets with it.

The `*.local.yaml` checkpoint workflow becomes a short script of
`config set` calls, and the device checkpoint docs update to match
in the switchover milestone.

## Generated documentation and the editing pass

The source of truth becomes `Field(description=...)` on the domain
models (decision 6). Concretely:

- Every field of every domain model gains a description, migrated
  from the `config.example.yaml` comment that documents it today.
  This is the deliberate editing pass the issue describes: each
  comment splits into a short factual description and the
  narrative that belongs in a fragment or the reference, and a
  comment with no obvious new home is raised in the PR as a
  question, never dropped silently. The implementation doc records
  the audit: every domain comment in the example file today, and
  where it went.
- `config schema [entity]` emits the JSON Schema pydantic already
  produces, descriptions included: the machine- and LLM-readable
  contract, and what an agent reads before writing a fragment.
- `config reference` renders the markdown reference from the same
  models; the committed copy lives at
  `docs/reference/domain-config.md`. CI regenerates it and diffs,
  so it cannot rot; the workflow's path filter gains
  `docs/reference/**` so an edit to the committed copy alone also
  triggers the check.
- The long-form narrative (field measurements, tuning advice,
  incident findings) survives in richly commented per-entity
  example fragments under `samtal-server/examples/`, one file per
  entity or provider type (`llm-anthropic.yaml`,
  `asr-faster-whisper.yaml`, `tts-elevenlabs.yaml`, ...), linked
  from the reference. These are examples to feed `config set`,
  not live config: the commented-YAML tradition continues there.
  The fragments land in the same PR as the descriptions and the
  reference, so the editing pass is one reviewable whole: every
  comment's destination (description, fragment, or reference
  prose) exists when the audit is judged, and the reference never
  links to files that do not exist yet. The old
  `config.example.yaml` deliberately still duplicates the
  narrative during that PR; the switchover PR then only removes,
  with no editorial judgment mixed into the behavior change.
- CLI `--help` text for fragment-shaped input is derived from the
  same `Field(description=...)` values rather than hand-written,
  which the issue asks for explicitly; the short description is
  written once, on the model.
- Provider-type options remain the one part schema generation
  cannot document (`extra="allow"` pass-through, typed option
  models are #88); until #88 they stay documented in the example
  fragments, and the reference says so.

`config.example.yaml` shrinks to the server and memory sections in
the switchover milestone, its header rewritten to point at the CLI
and the reference for the domain half.

## PR structure

Four PRs, one per milestone, each independently green (the
repository merges rebase-only and CI runs lint, unit, and
integration on every `samtal-server/**` change), each with its
CHANGELOG entry, each landing its implementation-doc section in the
change that ticks its milestone:

1. **Storage foundation.** Dependencies (SQLAlchemy, Alembic,
   cryptography: all non-GPL, fine as core dependencies under the
   licensing rules), `db/` with open-and-migrate, the baseline
   migration, `secrets.py` with the envelope and key handling. No
   server behavior changes; nothing imports the new modules yet
   outside tests.
2. **Repository and write path.** `store.py`, the write-time
   validation set, the CLI command group including `set-secret`,
   `list`, `show` with masking, and `verify_secrets` as the
   startup-only check kept out of `open_database`, tested but not
   yet called by the still YAML-driven boot. The server still
   reads YAML; the database is
   fully writable and readable, in parallel, which is what makes
   this PR testable end to end without touching the boot path.
3. **Generated documentation.** The whole editing pass in one
   reviewable PR: the Field descriptions, the commented per-entity
   fragments under `samtal-server/examples/`, and the completed
   comment audit in the implementation doc; `docgen.py`, `config
   schema` and `config reference` with `--help` text derived from
   the descriptions; `docs/reference/domain-config.md` committed;
   the CI drift check and workflow path filter update. YAML still
   works and `config.example.yaml` still carries its comments, so
   during this PR the narrative exists in both places and the
   audit is judged with every destination in hand.
4. **Switchover and docs.** `Config` composition boots the domain
   half from the database; domain keys in YAML and domain
   `SAMTAL_` env vars refuse boot naming the move;
   `config.example.yaml` shrinks (pure removal, the audit was
   completed in PR 3); the integration lane seeds its
   domain config through the repository; the container image and
   the smoke lane move with the switchover in the same PR: the
   `Dockerfile` points `server.database.dir` at the data volume,
   and every smoke-image check in the workflow (the full
   conversation, the external-provider slim boot, and the
   missing-local-extra refusal) is redesigned to seed its domain
   configuration into a database on the mounted volume through the
   CLI before booting the container, replacing the domain half of
   the `tests/smoke/config*.yaml` files; README, deployment notes
   (master key generation, database dir, what a backup must
   include, edits apply at restart), checkpoint workflow docs.

Sequenced so the risky editing work (3) and the behavior change
(4) each sit alone in review, and so that a pause after any PR
leaves `main` consistent: after 1 or 2 the database is dormant
machinery, after 3 the docs pipeline runs against the still
YAML-backed models, after 4 the issue is done.

Between PR 2 and the switchover the CLI can write a database the
server does not read, and the image publishes from `main`, so
that window is a real deployment state, not a hypothetical. It is
handled out loud rather than hidden: until the switchover lands,
every mutating `config` command prints a prominent notice that
the server does not yet read the database and this write is
staging for the switchover; the switchover PR removes the notice
in the same change that makes the database live. The CHANGELOG
entry for PR 2 says the same. This keeps the CLI usable for
exactly what the window is for (staging a deployment's domain
configuration ahead of the flip) while making it impossible to
mistake a staged write for an applied one.

## Tests

- **Unit** (`tests/unit`): open-and-migrate on a tmp path
  (including reopening an already-migrated file); envelope round
  trips, MultiFernet rotation order, wrong-key and missing-key
  refusal with the entity named; repository load and write paths,
  every write-time refusal case (unknown provider reference,
  delete-while-referenced, unknown agent in a binding, unknown
  default_agent), the completeness checks staying boot-only
  (building up from empty in natural order never wedges); CLI
  commands driven through their entry function with captured
  output, including masking in `show` and `list` and plaintext
  never appearing in any output or log record; schema and
  reference generation producing parseable output with every
  domain field described.
- **Integration** (`tests/integration`): the lane's conftest gains
  a helper that seeds the domain snapshot through the repository
  into a tmp database and boots the app against it; existing
  scenarios keep their assertions. The switchover PR carries this
  churn; behavior assertions do not change, the same bar as the
  boundary refactor.
- **Smoke** (`tests/smoke` and the image jobs in the workflow):
  the three image checks boot from domain-bearing YAML files
  today, which the switchover turns into boot errors. Each check
  gains a seeding step that writes its domain configuration into
  a database on the mounted data volume (running the CLI from the
  image itself, so the seeding also exercises the shipped
  artifact) before the container starts; the
  `tests/smoke/config*.yaml` files shrink to their server halves.
- The CI drift check is itself the test for the committed
  reference.

## Risks and mitigations

- **The comment editing pass drops field knowledge.** The
  config.example.yaml domain comments encode measured latencies
  and incident findings. Mitigation: the audit is a deliverable
  (implementation doc lists every comment and its destination),
  and PR 3 and PR 4 are reviewed against it; a comment with no
  home is a review question, per the issue.
- **Integration-lane churn at switchover.** The lane configures
  agents through YAML today. Mitigation: one seeding helper in
  conftest, mechanical edits only, assertion values change
  nowhere; a needed assertion change means the switchover changed
  behavior and is wrong.
- **Alembic packaged in the wheel.** Migration discovery must work
  from an installed package, not a source tree, and a unit test
  run from the editable checkout proves nothing about that: the
  source tree makes every file discoverable. Mitigation: the
  migrations directory lives inside `samtal_server/` and
  hatchling packages it, and PR 1 adds a CI step that builds the
  wheel (`uv build`), installs it into a scratch environment, and
  migrates a fresh database using only the installed artifact,
  with the source tree off `sys.path`.
- **Secrets leaking through logs or output.** Mitigation: the
  verification pass discards plaintext, materialization happens
  at the point of use only, tests assert masked output and
  capture log records on the boot path.
- **A CLI edit that silently waits for a restart.** By design
  (boot-time snapshot, decision 5), but a known operational trap.
  Mitigation: the deployment notes state it explicitly, and every
  mutating command prints "applies at the next server start" once
  the switchover has landed (before it, the staging notice from
  the PR-structure section prints instead).
- **The dev-machine default path.** `/var/lib/samtal` is not
  writable on a laptop. Mitigation: the open error names
  `server.database.dir` and the env override for it; the README
  dev section shows the one-line override.
- **The stale bytecode trap** (AGENTS.md). The CLI runs outside
  pytest constantly during this work. `PYTHONDONTWRITEBYTECODE=1`
  for every manual run; if a result contradicts the source,
  suspect the cache first.

## Open questions

- Bulk export/import stays deferred (issue's out-of-scope list);
  the checkpoint workflow's `config set` scripts are the interim.
  Revisit if backup or migration tooling needs it.
- The re-encrypt command for key rotation stays deferred until
  rotation is actually needed; MultiFernet keeps it possible.
- Whether `config show` needs a `--json` flag before the REST API
  exists. Decided in milestone 2 if a concrete consumer appears;
  the default answer is no, the API is the machine interface.
- The exact fragment file inventory under `samtal-server/examples/`
  is decided during the milestone 3 editing pass, driven by the
  audit rather than fixed here.

## Plan review round

One external review of the plan as first committed (f87b4e8): codex
CLI 0.147.0, model gpt-5.6-sol, read-only against this repository
with the issue #86 body supplied, 2026-08-11. Verdict: ready after
the P1 and P2 amendments, not ready to implement as is. Findings as
received, condensed; each carries its resolution once the amendment
addressing it lands.

1. **P1: encrypted secrets cannot flow through the proposed
   models.** `ProviderConfig` only represents `api_key_env` and its
   validator rejects secret-shaped extras; `McpServerConfig.env`
   and `.headers` are `dict[str, str]`, so an `{"enc": ...}` value
   cannot pass model validation, and the secret validator would
   also reject decrypted plaintext. This contradicts both "existing
   models unchanged in shape" and "every write validates the
   resulting snapshot through those models". The plan must define a
   separate persistence representation for stored secret values and
   an explicit point-of-use resolver, state how provider secret
   keys map to factory credentials and how MCP encrypted values
   stay outside `McpServerConfig`, and test building a real
   provider and MCP manager from encrypted credentials.
   *Resolution*: the envelope section now states the models never
   carry envelopes or plaintext and their validators stay intact;
   ciphertext lives in a per-entity `secrets` column keyed by
   credential slot (dotted paths for MCP env and headers), loaded
   into a `SecretStore` carried beside the models, never inside;
   the point-of-use resolver is the seam the code already has
   (`resolve_api_key`, `resolve_env_references`), which gains the
   store as a second source where `build_agent_providers` and
   `McpServers.build` run; and the unit tests build a real
   provider and a real MCP manager from encrypted credentials.
2. **P1: stale domain environment variables will be silently
   ignored.** pydantic-settings' environment source only looks for
   known fields and ignores unmatched OS variables even with
   `extra="forbid"`, so a stale `SAMTAL_DEFAULT_AGENT` or
   `SAMTAL_AGENTS__...` would silently stop applying instead of
   producing the moved-key boot error. The loader must explicitly
   scan `os.environ` for the removed domain prefixes, including
   nested `__` forms, while allowing reserved variables, with one
   test per removed prefix.
   *Resolution*: the composition section no longer relies on
   `extra="forbid"` for environment variables; the loader scans
   `os.environ` for the six removed domain names, bare and
   `__`-nested, and raises the moved-to-the-database error naming
   the variable, with reserved variables outside the scan by
   construction and one test per removed prefix.
3. **P1: the switchover cannot keep the current container and
   smoke CI green.** The image runs as an unprivileged user with
   `/data` as its writable volume; `/var/lib/samtal` is neither
   created nor writable, so "the default matches the deployed
   container" is false. All three image checks in CI boot from
   domain-bearing files under `tests/smoke/`, which after the
   switchover become boot errors or an empty database; the plan
   only reseeds the integration lane. PR 4 must point the image's
   database dir at the data volume and redesign every smoke-image
   setup to seed a database before boot.
   *Resolution*: the database-path section now names
   `/var/lib/samtal` as the generic model default and has the
   image set `SAMTAL_SERVER__DATABASE__DIR=/data/db`; PR 4's scope
   and a new smoke bullet in the tests section cover the
   `Dockerfile` change and the redesign of all three image checks
   to seed their domain configuration through the CLI, from the
   image itself, onto the mounted volume before boot, with the
   smoke config files shrunk to their server halves.
4. **P1: putting boot verification in generic database opening
   wedges the recovery CLI.** If `open_database()` always verifies
   every ciphertext, a missing key, wrong key, or corrupt token
   prevents `show`, `delete`, or replacement commands from running,
   so the only supported write path cannot repair the condition
   that prevents boot. Opening and migrating must be separate from
   server boot verification; masked reads and deletion must not
   require decryption; `set-secret` requires a valid encryption
   key; the exhaustive decryptability check runs at server startup.
   *Resolution*: `open_database` now only opens and migrates; the
   exhaustive check is `verify_secrets`, run by server startup
   between opening and provider building; `show`, `delete` and
   `clear-secret` treat ciphertext as opaque data so the CLI
   remains the recovery tool under a missing or wrong key; only
   `set-secret` requires a valid key. PR 2 and the milestone
   wording updated to match.
5. **P2: the validation modes are not concretely separable.** The
   current `Config._check_references` combines the default-agent
   completeness rule with all cross-reference checks in one model
   validator, and full pipeline completeness is enforced later by
   provider construction, so "validate the snapshot minus
   completeness checks" is not an available operation on the models
   as written. The plan should name independently tested validation
   phases and how write and boot select them, and the write-time
   refusal matrix must include deleting an agent still referenced
   by devices or default_agent.
   *Resolution*: the strictness section now names the phases
   (`check_shape` as the models themselves, `check_references`
   run at write and boot, `check_completeness` boot-only, with
   deployment checks unmoved downstream), derives them by
   splitting the existing `_check_references` without changing a
   message, and adds agent deletion under a device binding or
   default_agent reference to the write-time refusal matrix.
6. **P2: CRUD cannot represent several necessary state
   transitions.** No command clears `default_agent` (so the valid
   device-allowlist configuration is unreachable once it is set),
   there is no `clear-secret`, and provider `show` addressing is
   ambiguous because provider identity is `(stage, name)`. Add
   explicit unset operations and unambiguous addressing.
   *Resolution*: the grammar gains `clear-default-agent` and
   `clear-secret` for both entity kinds, `show` is spelled per
   entity kind with providers addressed as `(stage, name)`
   everywhere they are named, and the section states why clearing
   default_agent is a meaningful configuration rather than a
   degenerate one.
7. **P2: create-or-replace semantics do not define what happens to
   stored secrets.** A fragment cannot include ciphertext, so
   whole-row replacement either silently erases secrets or forces
   round-tripping a masked value; arbitrary `set-secret` keys are
   also not mapped to known credential slots. Define
   omitted-secret semantics: preserve stored secrets on entity
   replacement, modify them only through `set-secret` and
   `clear-secret`, and define the supported slots and the
   precedence between encrypted values and environment references.
   *Resolution*: the CLI section now states that `set` replaces
   the model-shaped half and never touches the `secrets` column;
   slots are defined by the existing secret-shaped-key rule for
   providers and dotted env/headers paths for MCP servers;
   ciphertext wins over an environment reference for the same
   slot, with `show` marking the shadowed reference; entity
   deletion removes its stored secrets.
8. **P2: snapshot validation and persistence need one serialized
   transaction.** WAL and a busy timeout do not make
   read-modify-validate-write atomic: concurrent CLI processes can
   validate against stale snapshots, lose updates, or race
   migrations. Require one transaction (`BEGIN IMMEDIATE` with
   bounded retries) around read, validation, and persistence;
   specify migration serialization; test two concurrent writers.
   *Resolution*: the schema section now requires every repository
   write to run read, reference check, and persist inside one
   `BEGIN IMMEDIATE` transaction, failing with a retryable error
   when the lock is not acquired within the busy timeout, and
   serializes the upgrade-on-open under the same locking so
   concurrent openers cannot race the baseline migration; both
   are tested with two concurrent writers.
9. **P2: the documented backup procedure is unsafe under WAL.**
   Copying only `samtal.db` while the server or CLI is active can
   omit committed data still in the WAL, and "a copy of the file
   leaks nothing" is too broad since prompts, endpoints, and
   variable names remain readable. Prescribe the SQLite backup API
   or a stopped, checkpointed database; escrow every MultiFernet
   key still required; scope the claim to stored plaintext secrets.
   *Resolution*: the database-path section now prescribes `VACUUM
   INTO` or `.backup` for live backups and plain copies only of a
   stopped, checkpointed database, requires key escrow separate
   from the backup, and scopes the claim to "exposes no stored
   plaintext secret" while naming what the file does expose.
10. **P2: the wheel packaging test does not test a wheel.** Tests
    run from the source checkout through the editable environment,
    which proves nothing about Alembic scripts being included and
    discoverable in a built wheel. PR 1 must build the wheel,
    install it into an isolated environment, and migrate a fresh
    database using only the installed artifact.
    *Resolution*: PR 1 and milestone 1 now include a CI step that
    builds the wheel with `uv build`, installs it into a scratch
    environment, and migrates a fresh database from the installed
    artifact alone with the source tree off `sys.path`; the risk
    entry states why the editable-checkout test proves nothing.
11. **P2: PR 2 exposes a CLI whose writes the server never
    reads.** During PRs 2 and 3, `config set` succeeds (and may
    print a restart reminder) but restarting still boots from
    YAML: two writable configurations and a misleading operational
    command. Keep the CLI explicitly unavailable until the
    switchover, print a staging-only warning, or treat PRs 2 to 4
    as a dependent stack.
    *Resolution*: the PR-structure section now treats the window
    as a real deployment state: until the switchover, every
    mutating command prints a prominent staging notice that the
    server does not yet read the database, removed by the
    switchover PR in the same change that makes the database
    live, with the PR 2 CHANGELOG entry saying the same.
12. **P2: the documentation editing pass is split across PRs
    inconsistently.** PR 3 performs the editing pass and generates
    a reference linking to fragments, but the fragments, completed
    audit, and fragment inventory are deferred to PR 4, which can
    leave PR 3 with incomplete narrative documentation or broken
    links. Move the fragments and the completed audit into PR 3
    while the old YAML still duplicates them; PR 4 removes the
    domain YAML without documentation judgment mixed in. Also
    require argparse help text derived from the same
    `Field.description` values, which the issue explicitly asks.
    *Resolution*: PR 3 now carries the whole editing pass
    (descriptions, fragments, completed audit) as one reviewable
    unit while `config.example.yaml` still duplicates the
    narrative; PR 4 and milestone 4 become pure removal on the
    documentation side; `--help` text derives from the Field
    descriptions; the fragment-inventory open question moves to
    milestone 3.
13. **P2: secret error-path protections and tests are
    incomplete.** Interactive stdin can echo a secret; YAML and
    pydantic failures can expose rejected input in tracebacks;
    database, migration, malformed-envelope, and cryptography
    errors are not normalized through `ConfigError`. Require
    no-echo terminal input, centralized sanitized exception
    handling, SQLAlchemy parameter logging disabled, and tests
    over stdout, stderr, logs, and traceback behavior.
    *Resolution*: the envelope section gains an error-path
    contract: `getpass` for interactive `set-secret`, every
    failure normalized through `ConfigError` naming location and
    kind without embedding the rejected value, statement echo and
    parameter logging off, and leak-surface tests asserting
    plaintext absent from stdout, stderr, log records, and the
    exception chain for all four failure families.
14. **P3: Fernet tokens are not bound to their entity and key
    location.** Fernet authenticates the token but not where it
    belongs: a valid ciphertext moved to an attacker-controlled
    MCP header would decrypt and transmit another stored
    credential. Consider encrypting a payload containing the
    plaintext and its canonical location, rejecting a mismatch.
    *Resolution*: adopted; the envelope's encrypted payload is a
    JSON document carrying the secret and its canonical (entity
    kind, identity, slot) location, and decryption refuses a
    location mismatch. A consequence is stated by design: moving
    an entity's secret means re-running `set-secret`, there is no
    copy path for ciphertext.
15. **P3: rotation wording implies completion is possible in this
    release.** Without the deferred re-encrypt command, adding a
    new first key only affects newly written secrets; old keys
    cannot be retired. State that every old key must remain in
    `SAMTAL_MASTER_KEY` until all tokens have been rewritten by
    future tooling.
    *Resolution*: the envelope section now states that this
    release supports adding a key and not retiring one, that
    every old key stays in `SAMTAL_MASTER_KEY` while any token
    written under it remains, and that re-running `set-secret`
    per stored secret is the interim rewrite path; the deployment
    notes carry the same statement.

## Milestones

One PR per milestone, ticked with its PR number, each linking to
its section of the implementation doc when written.

- [x] **[Storage foundation](2026-08-11-db-domain-config-implementation.md#milestone-1-storage-foundation)**
  (PR #95): SQLAlchemy, Alembic and
  cryptography as dependencies; `samtal_server/db/` opening,
  configuring and migrating the database; the baseline migration
  holding the full domain schema including the devices entity
  table; `config/secrets.py` with the envelope forms, MultiFernet
  key parsing, and masking. Accept: a fresh directory gains a
  migrated `samtal.db` on open and an already-migrated file
  reopens cleanly; envelope round trips, rotation order, and
  wrong-key refusal are unit-tested; a CI step builds the wheel,
  installs it into a scratch environment, and migrates a fresh
  database from the installed artifact alone; both test lanes and
  lint green; no server behavior change.
- [x] **[Repository and write path](2026-08-11-db-domain-config-implementation.md#milestone-2-repository-and-write-path)**
  (PR #97, merged as PR #100): `config/store.py`
  loading rows into the existing pydantic models and writing
  fragments through them with the write-time validation set;
  `config/cli.py` with set, delete, bind-device,
  set-default-agent, set-secret, list, and show (masked);
  `main.py` subcommand dispatch; `verify_secrets` implemented and
  tested, kept out of `open_database` so the CLI works without a
  key. Accept: a scratch database is populated from
  empty to a full working configuration through CLI calls alone in
  the natural order, every refusal case is tested, secrets are
  masked in all output, the server still boots from YAML
  unchanged, lanes green.
- [ ] **Generated documentation** (PR TBD): the Field-description
  editing pass over the domain models; the commented per-entity
  fragments under `samtal-server/examples/`; the comment audit
  completed in the implementation doc; `config/docgen.py`; `config
  schema` and `config reference` with `--help` derived from the
  descriptions; `docs/reference/domain-config.md` committed; the
  CI drift check and the workflow path filter gaining
  `docs/reference/**`. Accept: every domain model field carries a
  description, every domain comment in `config.example.yaml` is
  accounted for in the audit, the committed reference regenerates
  byte-identical in CI, lanes green.
- [ ] **Switchover and docs** (PR TBD): `Config` composed from the
  file half plus the database snapshot; domain keys in YAML and
  domain `SAMTAL_` env vars refusing boot with the
  moved-to-the-database error; `config.example.yaml` shrunk to
  server and memory as pure removal (the audit was completed in
  milestone 3); the integration lane seeding through the
  repository; the `Dockerfile` database dir on the data volume
  and the smoke checks seeding through the CLI; README,
  deployment notes, checkpoint workflow docs, CHANGELOG. Accept:
  the server boots its domain half from the database, a leftover
  domain key or env var produces the naming error, the image and
  all three smoke checks pass in CI, both lanes green with no
  behavioral assertion changed.
