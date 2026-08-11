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

The default matches the deployed container. On a development
machine `/var/lib/samtal` is usually not writable; opening the
database creates the directory when it can and otherwise fails with
an error naming `server.database.dir`, so the failure tells you
which key to point somewhere writable
(`SAMTAL_SERVER__DATABASE__DIR=./var` or the config file). What a
backup must include is the database file and the master key, which
lives only in the deployment environment; the deployment notes
spell out that a copy of the file alone leaks nothing and restores
nothing encrypted.

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

The mechanism: every write loads the current snapshot, applies the
change in the model layer, and validates the resulting snapshot
minus the completeness checks; only then does the row persist. One
validation code path serves the CLI today and the REST API later.

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
  `egress` (nullable boolean), `options` (JSON: the non-secret
  pass-through keys, exactly today's extras), `secrets` (JSON:
  option name to encrypted envelope, empty by default).
- `mcp_servers`: `name` (primary key), `transport`, `command`,
  `args` (JSON), `env` (JSON), `url`, `headers` (JSON), `egress`,
  `tool_timeout_s`. Values inside `env` and `headers` are either
  literal strings, today's `$VAR` reference strings, or encrypted
  envelopes.
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
  the REST API), stored in the provider row's `secrets` column or
  in place of an MCP env/header value.

`SAMTAL_MASTER_KEY` holds one or more Fernet keys, comma-separated,
newest first, wrapped in MultiFernet: encryption always uses the
newest key, decryption tries them in order, so rotation is add the
new key, restart, re-encrypt at leisure (the re-encrypt command
stays deferred until rotation is actually needed, per the issue).

Boot-time key check, same fail-at-boot pattern as `auth.enabled`:
opening the database enumerates the stored envelopes; if any
ciphertext exists and the key is missing, or any stored token fails
to decrypt under every configured key, the server refuses to start
with an error naming the entity and key location. The verification
pass discards plaintext immediately; materialized secrets exist
only at the point of use (building a provider, connecting an MCP
server), are never stored on a model, and are never logged.
`config show` and `config list` render an encrypted value as a
fixed mask and an environment reference as the reference itself,
which is not a secret.

## Repository and Config composition

`store.py` owns both directions:

- **Load**: read all rows, build the domain half of the
  configuration with the existing pydantic models (`ProviderConfig`
  and friends, unchanged in shape). The result is a
  `DomainConfig` model holding providers, mcp_servers,
  agent_defaults, agents, devices, default_agent.
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
Boot order: load the file half, open the database (which runs the
key check), load the snapshot, compose, validate, build the app.

A domain key left in the YAML file is a boot error naming where it
moved (`providers: moved to the database; write it with
samtal-server config set provider ...`), raised from the loader's
existing pre-flight check where the parsed top level is already in
hand. A `SAMTAL_`-prefixed environment override for a domain key
gets the same treatment: the file-half settings model rejects
unknown keys, and the loader maps the rejection for known domain
names onto the same moved-to-the-database wording, so a stale
deployment variable fails loudly instead of silently not applying.

The `local_only` egress check, provider building, MCP connection,
and everything downstream read the composed `Config` exactly as
today; the only difference visible to them is where the domain
half came from.

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
config set-secret provider <stage> <name> <key>       # value from
config set-secret mcp-server <name> env.<KEY>         # stdin, or
config set-secret mcp-server <name> headers.<KEY>     # --from-env VAR
config list                                           # summary tree
config show [<kind> [<name>]]                         # masked YAML
config schema [<entity>]                              # JSON Schema
config reference                                      # the markdown
                                                      # reference, to
                                                      # stdout
```

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
   `list`, `show` with masking, and the boot key verification in
   the open path. The server still reads YAML; the database is
   fully writable and readable, in parallel, which is what makes
   this PR testable end to end without touching the boot path.
3. **Generated documentation.** The Field-description editing
   pass, `docgen.py`, `config schema` and `config reference`,
   `docs/reference/domain-config.md` committed, the CI drift
   check and workflow path filter update. YAML still works; the
   descriptions document the same models the file loader uses, so
   nothing here waits on the switchover.
4. **Switchover and docs.** `Config` composition boots the domain
   half from the database; domain keys in YAML and domain
   `SAMTAL_` env vars refuse boot naming the move;
   `config.example.yaml` shrinks; the example fragments land with
   the comment audit completed; the integration lane seeds its
   domain config through the repository; README, deployment notes
   (master key generation, database dir, what a backup must
   include, edits apply at restart), checkpoint workflow docs.

Sequenced so the risky editing work (3) and the behavior change
(4) each sit alone in review, and so that a pause after any PR
leaves `main` consistent: after 1 or 2 the database is dormant
machinery, after 3 the docs pipeline runs against the still
YAML-backed models, after 4 the issue is done.

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
  from an installed package, not a source tree. Mitigation: the
  migrations directory lives inside `samtal_server/`, hatchling
  packages it, and the open-and-migrate unit test runs against
  the packaged layout in CI.
- **Secrets leaking through logs or output.** Mitigation: the
  verification pass discards plaintext, materialization happens
  at the point of use only, tests assert masked output and
  capture log records on the boot path.
- **A CLI edit that silently waits for a restart.** By design
  (boot-time snapshot, decision 5), but a known operational trap.
  Mitigation: the deployment notes state it explicitly, and
  `config set` prints a reminder when it can see the change is
  against the deployed database.
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
  is decided during the milestone 4 editing pass, driven by the
  audit rather than fixed here.

## Milestones

One PR per milestone, ticked with its PR number, each linking to
its section of the implementation doc when written.

- [ ] **Storage foundation** (PR TBD): SQLAlchemy, Alembic and
  cryptography as dependencies; `samtal_server/db/` opening,
  configuring and migrating the database; the baseline migration
  holding the full domain schema including the devices entity
  table; `config/secrets.py` with the envelope forms, MultiFernet
  key parsing, and masking. Accept: a fresh directory gains a
  migrated `samtal.db` on open and an already-migrated file
  reopens cleanly; envelope round trips, rotation order, and
  wrong-key refusal are unit-tested; both test lanes and lint
  green; no server behavior change.
- [ ] **Repository and write path** (PR TBD): `config/store.py`
  loading rows into the existing pydantic models and writing
  fragments through them with the write-time validation set;
  `config/cli.py` with set, delete, bind-device,
  set-default-agent, set-secret, list, and show (masked);
  `main.py` subcommand dispatch; boot key verification wired into
  the open path. Accept: a scratch database is populated from
  empty to a full working configuration through CLI calls alone in
  the natural order, every refusal case is tested, secrets are
  masked in all output, the server still boots from YAML
  unchanged, lanes green.
- [ ] **Generated documentation** (PR TBD): the Field-description
  editing pass over the domain models with the comment audit
  started in the implementation doc; `config/docgen.py`; `config
  schema` and `config reference`; `docs/reference/domain-config.md`
  committed; the CI drift check and the workflow path filter
  gaining `docs/reference/**`. Accept: every domain model field
  carries a description, the committed reference regenerates
  byte-identical in CI, lanes green.
- [ ] **Switchover and docs** (PR TBD): `Config` composed from the
  file half plus the database snapshot; domain keys in YAML and
  domain `SAMTAL_` env vars refusing boot with the
  moved-to-the-database error; `config.example.yaml` shrunk to
  server and memory; the commented per-entity fragments under
  `samtal-server/examples/` with the comment audit completed
  (nothing dropped silently); the integration lane seeding through
  the repository; README, deployment notes, checkpoint workflow
  docs, CHANGELOG. Accept: the server boots its domain half from
  the database, a leftover domain key or env var produces the
  naming error, the audit in the implementation doc accounts for
  every migrated comment, both lanes green with no behavioral
  assertion changed.
