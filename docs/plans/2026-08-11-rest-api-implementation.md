# REST API implementation

Companion to [`2026-08-11-rest-api.md`](2026-08-11-rest-api.md).
One section per milestone, recording what was actually built, the
deviations from the plan, the resolutions of its open questions, and
the discoveries a later milestone would otherwise have to make again.

## Milestone 1: plan and API skeleton

The gated namespace, with nothing behind it yet: `server.api` and the
fail-at-boot token, the sub-application mounted at `/api` with its auth
middleware and sanitized exception handlers, the typed refusals, the
committed OpenAPI document and its CI check. No route exists, so `/api`
answers 401 without the token and 404 with it, and no device-facing
behavior changes.

### What landed

**Typed refusals (`config/loader.py`, `config/store.py`, `db/`).**
`UnknownEntityError`, `DatabaseBusyError` and `StorageError`, all
subclassing `ConfigError`, live beside it in `loader.py`, which imports
no database machinery so `db/__init__.py` can raise them too. The
repository raises the unknown-entity type for every "no such ..." and
for a slot holding no stored secret, the busy type for the lock that
did not arrive inside the busy timeout, and the storage type for the
JSON-column shape refusals and the residual database failures.
`open_database` maps the same two, so a lock timeout during the
open-and-migrate step (which the API is on the path of for every
request) is the retryable refusal rather than the generic one. No
message text moved, and every existing `pytest.raises(ConfigError)`
keeps passing unmodified.

**`EnvName` (`config/models.py`).** An annotated `str` enforcing the
`_ENV_NAME_RE` rule the provider `*_env` validator already applies,
adopted by `api.secret_env` and by `auth.secret_env` in the same
change. The refusal says what the key must hold and shows an example,
never quoting the value.

**`ApiConfig` (`config/models.py`, `config.example.yaml`).**
`server.api` with one key, `secret_env`, defaulting to
`SAMTAL_API_SECRET`, and no `enabled` flag: the API is always mounted,
so the token is always required. The example file gains the block with
its comments above the keys.

**The sub-application (`config/api.py`).** `build_api(token,
database_dir)` returns a FastAPI instance with no docs, no redoc and no
served schema; a pure-ASGI `_BearerGate` comparing bearer tokens with
`hmac.compare_digest` before routing, so an unmatched path answers 401
too and a wrong token is answered exactly as no token at all; exception
handlers mapping the refusal types to 404, 409, 500 and 422, each
rendering `{"detail": "<the repository's own sentence>"}`; a sanitized
`RequestValidationError` handler, because FastAPI's default echoes the
rejected input back and a fragment can carry a pasted credential; and a
`_SanitizedErrors` middleware turning anything unhandled into a generic
500 with the traceback in the log. `store_dependency(directory)` is the
CLI's `_store` in dependency form, attached to `api.state.store` and
consumed by nothing yet.

**Mounting and fail-at-boot (`app.py`).** `create_app` resolves the
token through `api_token(config)` beside where device auth resolves its
secret, refusing the boot with the same shape of message (what is
missing, `openssl rand -hex 32`, where it goes), and mounts the
sub-application so that both `/api` and `/api/` resolve without a
redirect. The token is passed into the gate and kept nowhere else.

**The OpenAPI document (`config/docgen.py`, `config/cli.py`,
`docs/reference/api-openapi.json`).** `samtal-server config openapi`
renders the document from the sub-application's routes and prints it;
it takes no `--config`, opens no database, and needs no key and no
token. The rendering states the four things FastAPI cannot know: the
fixed contract version `"1"`, `servers: [{"url": "/api"}]` for the
mount prefix, the bearer scheme, and the document-level security
requirement. CI regenerates and diffs the committed copy in the same
step pattern as the markdown reference.

**Workflow and fixtures.** `tests/conftest.py` sets a throwaway
`SAMTAL_API_SECRET`, and the workflow's image job carries its own,
passed into every container that starts a server, including the slim
refusal check whose expected message comes from a boot that has to get
past the token first.

**Tests.** `tests/unit/test_config_api.py` (the 401 matrix including an
unmatched path and a wrong scheme, the token in no log record, the
refusal mapping through throwaway routes, the sanitized validation
handler and generic 500 with sentinel values, the store dependency, the
fail-at-boot refusal, and the mounted `/api` and `/api/` answering 401
without the token and 404 with it),
`tests/unit/test_config_refusals.py` (the subtype of every refusal,
with the busy case forced twice by holding a real write lock under a
short busy timeout, once inside `open_database` and once inside a
repository write), `tests/unit/test_config_env_names.py` (the sentinel
in no message, no stderr, no log record and no exception chain, for
both keys), and `tests/unit/test_api_openapi.py` (byte-identical
regeneration, double-render determinism, every `$ref` resolving, and
validation with openapi-spec-validator).

### Deviations from the plan

Four, all small, none changing what the milestone delivers.

**The test lanes get the token at import time, not from an autouse
fixture.** The plan says autouse conftest fixtures. `tests/conftest.py`
already solves this problem once, for the device auth secret, with an
`os.environ.setdefault` at import time, and says why: a module that
builds an app while it is being imported needs the value before
collection rather than before the first test runs. A fixture beside it
would be a second mechanism for the same job, so the API token is set
the same way, in the same file, which covers the unit, integration,
smoke and local lanes together. An already exported value still wins,
which is what lets the smoke lane point at a container started with
another token.

**The loader raises its validation refusal outside the handler.** The
plan asks for sentinel tests over the exception chain. `raise ... from
None` clears `__cause__` but leaves `__context__` pointing at the
pydantic `ValidationError`, whose `str()` quotes the rejected input
back as `input_value=...`, so the chain still carried the paste. The
message is now recorded inside the `except` and raised after it, where
there is no exception being handled and therefore no context at all.
`compose_config` got the same treatment.

**A stored row naming no provider stage is a `StorageError`.** The plan
scopes the storage type to "the JSON-column shape refusals and the
residual database failures". This refusal is neither, but it is a
stored row that cannot be read as configuration and nothing the caller
can do anything about, so it maps to 500 like its neighbours; the
identically worded refusal for a caller's bad stage argument stays a
plain `ConfigError`, which is 422. Mapping by type is what makes two
identical sentences able to answer differently.

**The document's provider-options note is a second rendering of the
reference's.** The plan says the description states the contract "in
the same words the markdown reference uses", and the reference's
sentence ends in "the example fragments below", which points at nothing
in an OpenAPI document. Both are now built from one string in
`docgen.py`, differing only in how they point at the fragments, so the
two cannot come to say different things.

### Discoveries

**A Starlette `Mount` never matches its own bare prefix.** `Mount` at
`/api` compiles the pattern `/api/{path:path}`, so a request to `/api`
misses every route and falls through to the router's trailing-slash
redirect. A redirect is not good enough for a gated namespace: it
answers before the gate does, and a client that does not resend its
`Authorization` header on one meets a 401 it cannot explain. The mount
is therefore paired with a `Route` on the bare prefix whose endpoint is
an object rather than a function, which Starlette calls as ASGI, and
which hands the sub-application exactly the scope `/api/` would have
produced (`root_path` extended by the prefix, `path` with the slash).
`mount_api` in `config/api.py` does both, and the tests assert 401 and
404 on `/api` and `/api/` alike with redirects disabled.

**A generic error handler on a mounted application is not enough.**
Starlette's `ServerErrorMiddleware` answers with the handler's response
and then re-raises, which inside a mount means the exception carries on
into the device-facing app's own error handling. The sanitized 500 is
therefore ASGI middleware that ends the exception where the response
was decided, tracking whether the response had already started before
it tries to send one.

**Forcing the busy refusal needs the timeout monkeypatched, not the
wait.** `BUSY_TIMEOUT_MS` is read from the module global by the
connection listener at connect time, so setting it to 200 ms before the
engine is opened makes both busy cases deterministic and fast: a raw
`sqlite3` connection holds `BEGIN IMMEDIATE` while the engine under
test tries to write, and while a second `open_database` tries to
migrate.

### Notes for the milestones that follow

- The store dependency exists and is attached to `api.state.store`; the
  read and write routes are what will resolve it.
- `_application()` in `config/api.py` is what both `build_api` and
  `document()` build, so a route added for the server is in the
  document by construction. The `openapi()` override is where milestone
  2's injected entity schemas and hoisted `$defs` belong; the `$ref`
  resolution test already walks whatever ends up there.
- The document is regenerated with
  `uv run samtal-server config openapi > ../docs/reference/api-openapi.json`
  in every milestone that touches routes, and both the unit suite and
  CI fail on a stale copy.
- `MALFORMED_REQUEST` is the sanitized body for FastAPI's own
  validation. The argument-shaped writes (devices, default-agent,
  secrets) need their own exact-shape parser in milestone 3, with the
  same rule: describe the expectation, never echo the body.

### PR #104 review round

One external review of the pull request's diff: codex CLI 0.147.0,
model gpt-5.6-sol, read-only, 2026-08-11, posted on the PR by the
review run itself. Verdict: mergeable after the listed fixes. Four
findings, all P1, each fixed with its own commit:

1. **The API's namespace was reachable through the OTA route.** The
   OTA router is registered before the API is mounted, and Starlette
   matches routes in order, so a configured `server.ota_path` of
   `/api/` or anything under it would have been found first and would
   have answered, unauthenticated, a request the token gate never saw.
   The OTA endpoint is deliberately open, because it is what issues the
   device tokens, so this is a hole rather than a curiosity. Fixed in
   833921c: `ota_path`'s validator refuses the reserved prefix, the
   mount path is single-sourced on the models so that what is reserved
   and what is mounted cannot drift apart, and the refusal names the
   rule without quoting the value, since a public deployment hides the
   OTA endpoint behind a long random segment.
2. **The sanitized 500 was not sanitized in the log.** It wrote
   `logger.exception`, which puts the exception's own text, its
   traceback and the request path into the log, and then re-raised once
   a response had started, so an outer logger wrote a second traceback.
   By that point the exception text is whatever a request put in front
   of the code that raised it, and a log line leaks as surely as a
   response body once the log is shipped somewhere. Fixed in 66118c3:
   one fixed line naming the exception's class and nothing else, no
   `exc_info`, no request-controlled path, and no re-raise after the
   response started; the tests assert the sentinel is absent from every
   captured record and both streams, that no record carries `exc_info`,
   and that a mid-stream failure produces exactly one line and no
   exception at the client.
3. **The repository's fragment refusal still chained the pydantic
   error.** `_load` raised inside its handler with `from None`, which
   clears `__cause__` but leaves `__context__` holding the
   `ValidationError`, whose `errors()` carry the complete rejected
   fragment, inline secret included. The existing leak test passed only
   because pydantic's `str()` truncates the middle of a long value,
   which is luck and not a property. Fixed in 941bf7f, with the pattern
   the loader had already been given: format in the handler, raise
   after leaving it, and assert both links are empty.
4. **Two parser failures in the loader chained their parsers.** The
   `SettingsError` path raised `from exc`, and a malformed `SAMTAL_`
   override of a structured key leaves a `JSONDecodeError` whose `.doc`
   is the whole rejected environment value; the YAML pre-flight check
   raised `from exc` too, and a PyYAML mark keeps the entire buffer it
   was parsing, so a complaint about one line carried the whole
   configuration file behind it. Fixed in 1977ebc, the same way, with
   sentinel cases writing the sentinel into the input each parser
   chokes on. Both messages are unchanged: neither was rendered from
   the parser's own text in the first place.

Findings 3 and 4 are the same defect as milestone 1's second deviation,
in the two places that pass was not asked to look at. The rule they
leave behind is worth stating once: in this codebase a sanitized
refusal is built inside the handler and raised outside it, because
`from None` is not enough. `__suppress_context__` only stops the
default traceback printer; the object is still attached, and anything
that walks the chain (a structured log handler, an error reporter, a
debugger) reads what it holds.

Full lanes after the fixes: ruff clean, both suites green and both
drift checks clean (counts in the PR's verification section).
