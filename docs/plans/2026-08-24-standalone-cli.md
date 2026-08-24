# Ship the vinga CLI standalone: a thin default install

Issue #223, fourth issue of the #265 CLI chain. Deviations,
resolutions and discoveries land in the companion
`2026-08-24-standalone-cli-implementation.md`, one section per
milestone, appended in the change that ticks the milestone.

## Goal

An operator on a laptop types `uv tool install vinga-server` (or
`uvx`, or eventually a published name) and gets a `vinga` command
that drives a remote vinga over HTTP, installing pydantic, httpx,
typer and YAML and NOT FastAPI, SQLAlchemy, Alembic, cryptography
or the audio stack. The server half moves behind a `[serve]`
extra; the CLI is the package's default face. The #265 wheel-grade
lane lands with it: the built wheel installed into a clean venv
and the actual `vinga` binary driven as a subprocess against a
live server, inventory-complete.

## The issue's decisions, restated, and the re-scope this plan argues

The issue (written 2026-08-20, before #194 landed the Typer
rebuild in-server per #265's resequencing) asks for a standalone
distribution, working name `vinga-cli`, console script `vinga`, a
pure API client of the order of httpx plus the argument layer; the
`--local` break-glass stays in the server package; and it names
the open decisions: shapes source, what "binary" means, repository
layout, version skew.

The census at `8aad881b` forces a re-scope of the letter while
keeping every outcome, and this plan asks the review round and
Rafael to weigh it:

- **The remote verbs and the break-glass verbs are the same
  commands.** `delete`, `set-secret`, `clear-secret` and `show`
  are simultaneously the issue's "remote verbs to move" and its
  "break-glass commands that stay": `--local` is a flag on one
  grammar, not a separate command set. A second distribution
  carrying the remote grammar means those four families exist in
  two grammars that must agree, the design guide's pending bug at
  the scale of a whole CLI.
- **The remote half's dependency closure is already light but for
  six names.** `responses.py` imports nothing from the server;
  `printing.py`, the descriptors, docgen's help renderers and
  `provider_options` are pydantic-only. The heavy edges on the
  REMOTE path are exactly: `store.check_transportable`,
  `store.APPLY_LOCATION`, `store.addressed` (pure functions and a
  constant over documents, living in a 2,694-line SQLAlchemy
  module), `views.reference_value` (one pure function in a
  store-importing module), and `secrets.MASK` plus
  `secrets.provider_identity` (a string constant and a pure
  function in a cryptography-importing module). Six moves to
  light homes and the remote path is clean.
- **A shared-contracts package or a generated client would replace
  working, battle-tested code.** The response models exist, are
  strictly read, and are held to the committed OpenAPI document by
  the round-trip and acceptance suites; `openapi-python-client`
  appears nowhere in the tree, and the M5 spike's method would
  cost a spike to conclude what the working code already proves.
  The document remains the contract; the shapes stay hand-written
  where they are, drift-checked as they are.

So: ONE package, whose default install is the thin client, with
the server behind an extra; the `vinga` console script lives on
it; no second distribution and no grammar split. The issue's
`vinga-cli` working name resolves to "the package's default
install IS the CLI". If a separately NAMED distribution is ever
wanted for discoverability, it is a twenty-line metadata wrapper
depending on this core, and nothing in this plan forecloses it;
building that wrapper today would add a name with no body.

## Decisions

### 1. The dependency tiers, stated in pyproject

Core dependencies become exactly what the client needs: pydantic,
httpx, typer, PyYAML, python-dotenv. A new `serve` extra carries
FastAPI, uvicorn, SQLAlchemy, Alembic, cryptography, av, and
whatever else the census of `pyproject.toml`'s current runtime
list assigns to serving (the implementer inventories every current
dependency into a tier with a reason each; the existing model
extras like faster-whisper stay their own extras). The image
installs `[serve]` plus its existing extras; CI's serve-side jobs
sync with the extra; the docs say `uv tool install vinga-server`
for the CLI and name the extra for serving.

### 2. Six names move to light homes; the heavy imports go lazy

- `check_transportable` and `APPLY_LOCATION` move from `store.py`
  to a light home beside the transport vocabulary they serve (the
  implementer proposes; `config/printing.py` or a sibling), with
  `store.py` importing them back so the repository keeps one
  definition.
- `addressed` moves beside the descriptors it reads
  (`entities.py`).
- `views.reference_value` moves to the light module that renders
  it (its only CLI caller is export's shadow note).
- `MASK` moves beside `is_secret_option` (`models.py`);
  `provider_identity` beside the descriptors. `secrets.py` keeps
  re-exports so the server-side callers do not churn.
- `config/cli.py`'s remaining heavy imports become lazy at their
  arms: `store`/`db`/`load_keys` inside the `--local` handlers
  (already the shape for some), `views`' five kind-renderers
  inside the local show path, `onboarding.origin` inside
  `ota-url`, `docgen.openapi`'s FastAPI reach is already
  function-local. A `--local` or serve invocation without the
  extra refuses with one fixed sentence naming the extra
  (`install vinga-server[serve] on the server host`), tested.
- `main.py`'s eager `app`/`composition` imports move inside the
  serve branch, mirroring the lazy dispatch it already does for
  the command groups; `vinga-server` without the extra serves the
  same fixed sentence, and `vinga-server config ...` works from
  the thin install.

### 3. The `vinga` console script

`[project.scripts] vinga = "vinga_server.config.cli:main"` on this
package. `vinga config list` would stutter, so `vinga` IS the
config grammar directly: `vinga list`, `vinga set provider ...`,
`vinga apply -f -`. The `vinga-server config` spelling stays
untouched (the artifact-pinned spellings and every doc keep
working); the new script is an additional door onto the same
`cli.main`, whose prog name renders as `vinga` when invoked that
way so help and usage read honestly (Click takes the prog from the
invocation; the boundary's fixed sentences carry no prog and need
no change; the one place the grammar prints its own name,
`PROGRAM`, becomes invocation-aware). The docker shim note: the
README's `vinga()` shell function would shadow an installed
binary; the docs section that defines the shim gains the one
sentence saying so and when to drop it.

### 4. Version skew, pre-1.0

No negotiation machinery. The committed OpenAPI document is the
contract and its `API_VERSION = "1"` is the handle; the CLI gains
`vinga --version` printing its own package version, and the skew
policy is documented in `cli.md`: pre-1.0, run the CLI from the
same release line as the server; a mismatched pair may refuse or
misrender and the fix is upgrading the older half. The server's
unauthenticated `/healthz` (outside the API mount) already serves
`version` and `revision` for a human check, and `cli.md` shows the
curl. Machinery beyond that waits for a real skew incident or 1.0,
recorded as the deliberate floor.

### 5. The wheel-grade lane, with the leak surface kept

A new integration module upgrades the live lane the way #265
assigns: build the wheel, `uv venv` a clean environment, install
the BARE wheel (no extras), and drive the actual `vinga` binary as
a SUBPROCESS against the live server. The server stays IN-PROCESS
(the existing `serving()` thread), which keeps `Watched`'s
log-record leak assertions alive; only the CLI crosses a process
boundary, which is exactly the half the wheel grade is about. The
test process still imports `cli.COMMANDS` for the
inventory-derived completeness map while executing through the
subprocess; coverage is the registration table both ways, the
pattern `test_cli_live.py` set. The lane also proves the thin
install negatively: in the clean venv, importing
`vinga_server.config.cli` succeeds while `import fastapi`,
`import sqlalchemy` and `import cryptography` fail, and a
`--local` invocation answers the fixed install-the-extra sentence.
Runtime budget: one wheel build and one venv per module, subprocess
per command; if the full inventory pushes the lane past a couple
of minutes, a representative-per-family subset runs per-PR with
the full sweep kept cheap enough to stay (measured and recorded).

### 6. Docs

`cli.md`'s installation head rewrites around the real story:
`uv tool install vinga-server` / `uvx --from` for a checkout,
the `[serve]` extra for the server host, the `vinga` spelling
beside `vinga-server config` with the stutter note, the skew
policy, the shim-shadowing sentence. READMEs' install lines
update; `CHANGELOG.md` records the tiering (Changed: the default
install is the CLI; serving needs `[serve]`) and the new script
(Added). The `#223` disclaimer in `cli.md`'s "no published
package yet" paragraph updates to the honest present tense.

## Module layout after the change

- `vinga-server/pyproject.toml`: the tiers, the `vinga` script,
  `uv.lock` regenerated.
- `config/cli.py`: lazy arms, invocation-aware `PROGRAM`; the six
  imports repointed.
- `store.py`, `views.py`, `secrets.py`: lose the moved names,
  keep re-exports where server callers are many.
- `main.py`: serve imports inside the serve branch, the extra
  refusal.
- `Dockerfile` and `.github/workflows/vinga-server.yml`: install
  `[serve]`; the new wheel-grade lane step; the existing wheel
  steps keep passing (they exercise the serve wheel and must
  install the extra now).
- `tests/integration/test_cli_wheel.py` (or similar): decision 5.
- `docs/reference/cli.md`, READMEs, `CHANGELOG.md`: decision 6.

## Tests

The existing suites are the pin: nothing about the grammar, the
transport, the refusals or the renderings changes, so every CLI
suite runs unmodified except where an import path moved (the six
names' importers, one rule). New: the tier proof (clean-venv
negative imports plus the CLI working), the extra-refusal
sentences (serve and `--local` without `[serve]`), the
invocation-aware prog rendering, `vinga --version`, and the
wheel-grade lane with its two-way completeness. The image lane
must run pre-merge via workflow_dispatch (the Dockerfile and the
extras change), per the standing rule.

## Risks

- **A missed heavy import at module scope** turns the thin install
  into an ImportError at first use. The clean-venv negative-import
  test is the guard, and it imports the CLI module itself, not
  just the package.
- **The lock and CI churn.** Re-tiering rewrites `uv.lock`; every
  CI job that syncs must name the extra or deliberately not; the
  implementer lists each sync site with its choice.
- **The wheel steps' semantics shift**: the existing
  migrate-from-wheel and render-from-wheel steps assume the wheel
  serves; they now install `[serve]` explicitly, and the NEW lane
  is the one that installs bare. Both are asserted so neither
  quietly tests the other's configuration.
- **Prog-name rendering** touches help output that `cli.md`'s
  generated region pins; the regeneration must show only the
  intended spelling changes, read in the diff.

## Milestones

- [ ] **M1: the tiers and the thin client.**
  Decisions 1, 2, 3: the pyproject split, the six moves, the lazy
  arms, the `vinga` script with invocation-aware prog, the extra
  refusals, the clean-venv tier proof, the image/CI install
  updates, the regenerated artifacts. Design footprint: deepens
  the package boundary itself (what an installer gets by default
  becomes the client's contract); no new modules beyond the light
  homes the moves need.
- [ ] **M2: the wheel-grade lane and the docs.**
  Decisions 4, 5, 6: the subprocess lane with the in-process
  server and both-ways completeness, the version story, the
  install-story rewrite. Design footprint: test assets and
  documentation; the lane becomes the standing proof the thin
  install stays thin.
