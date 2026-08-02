# samtal-server v1 implementation notes

**Date:** started 2026-08-02

Companion to [`2026-08-02-samtal-server-v1.md`](2026-08-02-samtal-server-v1.md).
One section per milestone, appended in the same change that ticks the plan's
milestone checklist. Records deviations from the plan, resolutions of the
plan's open questions, and discoveries worth keeping. A milestone with no
deviations says so explicitly.

## M0 Skeleton (PR #1, merged 2026-08-02)

Deviations and additions relative to the plan:

- **Dev dependency `httpx` replaced by `httpx2`.** Starlette's test client
  (which FastAPI's `TestClient` re-exports) deprecated `httpx`; the suite now
  passes with deprecation warnings escalated to errors.
- **CI actions pinned newer than assumed.** GitHub deprecated Node 20
  actions, so the workflow uses `actions/checkout@v7` and
  `astral-sh/setup-uv@v9.0.0`. Note: setup-uv publishes no floating `v9`
  major tag; the exact tag is required.
- **Small unplanned additions**: a `/healthz` endpoint (gives the skeleton a
  testable contract) and a `samtal-server` console entry point reading
  `SAMTAL_HOST`/`SAMTAL_PORT`.
- **Process work rode along** (not part of the milestone as planned):
  AGENTS.md gained the small-commits rule, the PR verification task-list
  rule, and the plan milestone checklist; the repo logo was consolidated to
  a single transparent PNG.

Verified beyond the plan's acceptance criteria: a doc-only push to main
triggers no workflow run (path scoping observed working post-merge).

## M1 Config (PR #2, merged 2026-08-02)

Deviations and additions relative to the plan:

- **Reworked mid-PR to be library-based.** The first implementation
  hand-rolled env overrides and YAML loading; after researching best
  practices (summary in a PR #2 comment), the config became a
  pydantic-settings `BaseSettings`. Source priority follows the library's
  documented chain: init kwargs, then `SAMTAL_`-prefixed environment
  variables with `__` as the nesting delimiter, then the YAML file, then
  the secrets-directory source (inert until configured).
- **Env vars renamed.** `SAMTAL_HOST`/`SAMTAL_PORT` from M0 became the
  library-standard `SAMTAL_SERVER__HOST`/`SAMTAL_SERVER__PORT`, and every
  config key is now env-overridable, not just those two. Renamed while
  nothing was deployed.
- **`.env` support arrived early** (planned around M4/M7): read at startup
  via python-dotenv, with real environment variables taking priority.
  Gotcha: bare `load_dotenv()` searches from the installed package's
  directory, so `find_dotenv(usecwd=True)` is required; caught when the
  first CLI verification started the server instead of failing.
- **Runtime YAML path workaround.** pydantic-settings has no init kwarg
  for a runtime-chosen config file (pydantic-settings#259); the path from
  `--config`/`SAMTAL_CONFIG` reaches `YamlConfigSettingsSource` through a
  `ContextVar`.
- **Custom code kept deliberately**: cross-reference validation, MAC
  normalization, `ConfigError` formatting, and a pre-flight file check,
  because the library source silently skips a missing file and its parse
  errors do not reliably name line and column.
- **One review round, three findings, all fixed**: the inline-secret guard
  was broadened from an exact key list to fragment matching (`secret`,
  `token`, `password`, `api_key`, `apikey`, `credential`) with a `_env`
  suffix carve-out; blank identifiers (empty provider/agent names, empty
  provider `type`, `default_agent: ""`) are rejected via a shared
  `NonBlankStr` type; a README claim about mounted secret files was
  removed because provider secret resolution cannot read files until M4.
- **Example provider types are placeholders.** `config.example.yaml` names
  `sensevoice` and `piper` before the plan's open questions on ASR/TTS
  defaults are decided; M4 settles them.

Resolution of plan open questions: none (all four remain open for M4).
