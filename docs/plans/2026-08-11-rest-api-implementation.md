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
## Milestone 2: read routes

The gated namespace gets its reads. `ConfigStore` answers for one
entity at a time, `views.py` turns that into the masked envelope both
callers show, and eleven GET routes serve it. The CLI renders the same
envelopes it always rendered, byte for byte, and is still the only
write path.

### What landed

**Repository reads (`config/store.py`).** One method per addressable
entity kind (`read_provider`, `read_mcp_server`, `read_agent`,
`read_agent_defaults`, `read_device`, `read_default_agent`) beside the
whole-snapshot `load()`. Each raises `UnknownEntityError` with the
sentence the CLI has always printed, so "no such provider" is decided
once whatever the transport; a stage that is not a stage and a MAC that
is not a MAC stay plain `ConfigError`, which is the difference between
the 404 and the 422. The agent defaults and the default agent are never
missing: an unwritten singleton is the empty entry and an unset default
agent is null, both configurations rather than absences.

A read returns an `Entity`: the model-shaped half, plus a
`StoredSecret` per slot holding a stored secret, each carrying the
entity key its value displaces (`shadows`). Precedence is the
repository's decision, applied by one rule (`_shadowed`) that the
whole-snapshot helper `stored_secrets()` uses too, so a slot cannot be
said to shadow one key in a listing and another in a single read.

**`config/views.py`.** The CLI's display helpers, moved: the masked
provider and MCP bodies, the agent and agent-defaults layer, the device
binding, and the mask rule applied to an entity in exactly one place.
On top of them it builds the envelope
(`{"entity": ..., "secrets": {slot: {"shadows": ...}}}`), the four
identity-keyed listings, and the whole-configuration document
(`{"config": ..., "secrets": [locations]}`). The API returns those
dictionaries as JSON; `cli.py` renders the same ones as YAML and builds
its comment lines from the envelope rather than from a second walk over
the entity. No CLI test changed, and the output was also compared
directly: every `show` and `list` variant, including the refusals and
their exit codes, run against one seeded database by this branch and by
the milestone 1 commit, is identical byte for byte.

**Read routes (`config/api.py`).** The plan's surface exactly: `GET
/config`, the four listings, one route per entity kind, and
`/default-agent`. Handlers are plain `def` on the threadpool, take the
per-request store dependency and call one repository method and one
view each. The dependency is resolved from `request.app.state.store`
rather than closed over, so `document()` still builds an application
that never opens a database. Identities ride as decoded single path
segments: spaces, percent signs and non-ASCII names round-trip
percent-encoded, and a MAC is normalized before it is looked up.

**Transport models and the document.** `Envelope`, `SecretSlot`,
`ConfigDocument`, `StoredSecretLocation`, `DefaultAgent` and `Problem`,
declared as `response_model` and in each route's `responses`, so the
document carries real schemas and every refusal a route can answer
with. `_entity_schemas()` injects the four entity models into
`components.schemas` with their nested `$defs` hoisted, which is the
seam milestone 3's `openapi_extra` request bodies reference.
`docs/reference/api-openapi.json` is regenerated; the drift,
determinism, `$ref`-resolution and validator tests all still pass.

**Tests.** `tests/unit/test_config_reads.py` (the repository's reads and
the view over them: the refusal sentences, the shadow marks, the empty
secrets, fail-closed masking, and a listing agreeing with a single read)
and `tests/unit/test_config_api_reads.py` (the same over HTTP: envelope
shapes per route family, the listings, 404 with the repository's exact
sentence, 422 for an unaddressable identity, 500 for an unreadable row,
409 with a real lock held, the encoded-identity round trips, and the
no-leak assertions over bodies, headers and logs).
`tests/unit/test_api_openapi.py` gains the route inventory, the refusal
inventory and the registered entity schemas.

### Deviations from the plan

Two, neither changing what the milestone delivers.

**Three milestone 1 tests moved off `/config`.** They asserted the
skeleton's acceptance ("with the token, 404, because there are no
routes") against `/api/config`, which is now a route. They assert the
same property against a path that is not a route, and one new test
takes the other half: `/api/config` answers 200 through the mount, which
is what shows the routes are served where the document says they are.
No CLI test changed, which is the check that mattered.

**The 409 is forced through the open-and-migrate step, not the read.**
The plan asks for the busy refusal exercised through a real GET. Because
the API opens the database per request, a held lock is met by
`open_database`'s migration check before any handler runs, so that is
the phase an HTTP test can reach; the repository-write phase stays
covered by `tests/unit/test_config_refusals.py`, which forces both. The
route test asserts the status code and the error shape only, never the
wording.

### Discoveries

**A pasted credential cannot reach a read as itself, but a
name-shaped one can.** The models refuse an inline secret and refuse an
`*_env` value that does not look like a variable name, so a row holding
an obvious paste cannot be loaded at all. What does load is a
credential shaped like a name (`sk_test_...`), because the model's
check only asks that a reference look like a name, and that is exactly
what `secrets.mask` catches by passing only a canonical reference
through. So the fail-closed masking tests use a name-shaped sentinel:
the earlier draft's plainly-pasted one made the row unloadable and
tested nothing.

**A stored row that fails model validation answered 422.**
`_read_domain` loaded each row through its entity model, and `_load`
raises plain `ConfigError`, which the API maps to 422: the caller's
mistake, which it is not. Only the JSON-column shape refusals were typed
`StorageError` and answered 500. This was first recorded here as a
deferred question; the PR review below insisted on it, correctly, and it
is fixed in 28d1585.

**A response model with `extra="forbid"` is the check that views and
the transport agree.** FastAPI validates what a handler returns through
`response_model`; with the default `extra="ignore"` a key the view
started producing would be dropped from the response silently. Forbidding
extras makes that a failure in the suite instead.

### Notes for milestone 3

- `_reads(api)` in `config/api.py` registers the GETs; the writes want
  the same shape (`_writes(api)`), registered from `_application()` so
  they are in the document by construction.
- `_entity_schemas()` is where the request schemas already live. A PUT's
  `openapi_extra` can `$ref` `#/components/schemas/ProviderConfig` and
  the resolution test will keep it honest.
- `views.reference_value(body, key)` is what turns a `shadows` mark into
  the value it displaced, dotted MCP slots included; the CLI's comment
  lines are built from it.
- The repository's reads are what `--local show` consumes: it needs no
  new existence logic, only the same methods against a locally opened
  store.

### PR #105 review round

One external review of the pull request's diff: codex CLI 0.147.0,
model gpt-5.6-sol, read-only, 2026-08-11, posted on the PR by the
review run itself. Verdict: mergeable after the listed fixes. Seven
findings, five P1 and two P2; six accepted in full, one accepted in
part with the reasoning recorded below.

1. **P1: a secret nested inside a provider option was accepted and
   published.** The inline-secret rule looked only at the top level of
   an entry, and options are passed through to the implementation, so
   `connection: {api_key: ...}` was a fragment the models accepted, the
   repository stored, and every read form returned verbatim: the entity
   GET, the listing, `/config` and `config show` alike. Fixed in
   007796e: the rule is recursive over mappings and lists, naming the
   dotted path and never the value, and the display path masks a
   secret-shaped key at any depth as well. The mask does not lean on
   the validator having run, and it earns its place after the fix too:
   a nested reference key still accepts anything shaped like a variable
   name, which a credential can be. Being honest about the behavior
   change: a fragment that nested such a key was never usable, nothing
   resolved it, and a stored row holding one now reports that it cannot
   be read rather than showing what it holds.
2. **P1: sanitization gaps around the identity refusals**, in three
   parts, two fixed and one declined. Fixed in 143355f: `_mac` (and the
   binding and entry-name refusals beside it, and the CLI's YAML and
   file refusals) raised inside their handlers, so the exception they
   were built from stayed attached as `__context__` and travelled out
   with them, a PyYAML mark holding the whole fragment among them; and
   the error shape's schema description promised that a refusal never
   quotes what was sent, which overstates what holds. It now says what
   does: a refusal names the entity the request addressed and the rule
   it broke, and never quotes a secret or a rejected configuration
   value.
   *Declined, deliberately*: the demand to strip the identity from the
   "no such X" sentences and the offending value from the pre-existing
   stage and MAC refusals. Those exact sentences are the CLI contract
   reviewed under #86, and issue #101's decision 6 freezes them so that
   an operator meets one vocabulary whichever way they reached a
   refusal; they predate this PR and nothing here changed them.
   Echoing a requester's own addressing back to the requester is also
   not the leak class the no-leak contract targets: its targets are
   secrets and rejected configuration values, which findings 1 and 3
   cover. Changing them is a contract change, not a fix, and it belongs
   in a change that says so.
3. **P1: the per-request read path retained library exceptions.**
   Opening the database is on every API request and chained
   SQLAlchemy's exception into its refusal, message included, and a
   SQLAlchemy error holds the statement it failed on with the
   parameters bound to it; the key loader, the repository's
   transaction, and the encrypt and decrypt paths raised inside their
   handlers too, where the library had been handed the key or the
   plaintext; and the storage 500 logged the exception object as a
   record argument, which hands its message and its whole chain to
   anything that walks the record. Fixed in 4d504ea: every one of them
   builds its sentence in the handler and raises outside it, the
   migration failure embeds the driver's own line rather than
   SQLAlchemy's wrapper, and the log line names the exception's class
   and nothing else.
4. **P1: the plan's addressability rule belonged in this milestone.**
   The reads address an entity by putting its identity in a path, so a
   name or a slot holding a slash could not be fetched at all, and
   deferring the rule to the write milestone would have shipped a read
   surface that cannot reach part of what the repository accepts. Fixed
   in 497d5f8, at write time only: names and provider slots must be one
   URL path segment (no slash, no control characters; spaces, percent
   signs and non-ASCII stay legal), and an MCP slot's key half must
   name what the value would otherwise have referenced, a variable
   after `env.` or a header after `headers.`. The load path is
   untouched, so a row written before the rule still boots, still reads
   back in the whole configuration, and is still deletable.
5. **P1: a stored row failing model validation answered 422.** This
   milestone had recorded the mapping as a known wrong answer and
   deferred it; the review insisted, correctly, since 422 tells a
   caller it sent something wrong about a row it cannot influence.
   Fixed in 28d1585: the row loaders and the assembly that turns the
   rows into one configuration raise the storage refusal, naming the
   row and the fields that failed and never their values, built inside
   the handler and raised outside it because a ValidationError holds
   the whole row. Fragments keep the 422 they had. Boot is unchanged by
   construction, the storage type being a `ConfigError`, and a test
   pins it.
6. **P2: nullable response fields were also optional.** `shadows` and
   the default agent's `name` carried a `None` default, which marks
   them optional in the schema, so a generated client had three states
   to handle where the server produces two. Fixed in 3fb1436: required
   and nullable, which is what every response already was, with the
   document regenerated and the required entries asserted.
7. **P2: a stored non-finite float changed the configuration
   silently.** YAML spells `.nan` and `.inf`, JSON has no spelling for
   either, and the serializer answers null, so the option disappears
   and the provider falls back to its own default. Fixed in 550dde9 in
   the semantics layer: every fragment is walked when it is written, at
   any depth and for every entity kind, and a stored value that is not
   finite makes the row report that it cannot be read.

Findings 1, 3 and 5 share a shape worth naming: each is a place where
the sanitized boundary was drawn around the case that was thought of
(a top-level key, a repository write, a fragment) while the same data
reached the same reader by another route. The rule the milestone 1
review left behind, build the refusal inside the handler and raise it
outside, now holds across the config package rather than in the two
places that pass looked at.

Full lanes after the fixes: ruff clean, both suites green and both
drift checks clean (counts in the PR's verification section).

## Milestone 3: write path and CLI switchover

The behavior change, in one push: the API gains every write, the CLI
becomes its client with its grammar untouched, and the recovery subset
becomes the only thing that still opens the database from a command
line. No published state holds two co-equal write paths, which is the
whole reason these land together.

### What landed

**The write routes (`config/api.py`).** The plan's surface exactly: PUT
and DELETE per entity kind, the two write-only secret endpoints, PUT for
the device binding and the default agent, DELETE for the default agent's
idempotent clear, and PUT for the agent defaults, which are one entry
for the deployment and so have nothing to delete. Every success answers
200 with `{"wrote": ..., "notice": RESTART_NOTICE}`: configuration is a
boot-time snapshot, and a write that answered only "ok" would leave the
one operational trap of that design open.

Fragment bodies are received as `Annotated[Any, Body()]` and handed to
the repository unread. Declaring the entity models as body types would
put FastAPI's own validation in front of them, and its 422 echoes the
input it rejected, which for a fragment carrying a pasted credential is
exactly the leak the repository's `_load` exists to avoid. The three
argument-shaped bodies (devices, default agent, secret) get exact-shape
parsers: one key, one shape, and a refusal describing the expectation
rather than quoting what arrived. They are written with plain checks and
no `try`/`except`, because a `KeyError` or a `TypeError` raised on a body
holds the body, and the PR #104 rule is that a sanitized refusal is
raised outside the handler that formed it.

**The write vocabulary (`config/writes.py`).** What a write says it did,
and `RESTART_NOTICE`, in a leaf module both write paths import. The API
answers in these words and `--local` prints the same ones, so the
break-glass path and the ordinary one cannot come to describe the same
act differently.

**The CLI as a client (`config/cli.py`).** The grammar is unchanged and
so is every message: the API carries the repository's sentence in
`detail` and the CLI prints `detail`. Where the server is: `--api-url`,
then `SAMTAL_API_URL`, then `http://127.0.0.1:<server.port>/api`. The
token is the value of the variable `server.api.secret_env` names, read
from the same file configuration, resolved before any request is sent.
The transport policy refuses a plain `http://` connection to a host that
is not this machine (no override flag), refuses a URL carrying userinfo,
strips userinfo from any URL it prints, and reports a non-2xx body that
is not this API's own as a status code rather than relaying it. Timeouts
are explicit: connect 5 s, read 30 s, which is margin above the
database's 10 second busy timeout so that the settled retryable 409
arrives as itself rather than as a client-side timeout.

`--local` covers `show`, `delete`, `clear-secret` and `set-secret`,
carried on the commands themselves (`local_ok`) rather than in a list
kept somewhere else. Every `--local` invocation prints the notice on
stderr, reads included; every other command refuses the flag by naming
the four.

**The acceptance suite (`tests/unit/test_config_cli.py`).** The scenario
tests now drive `cli.main()` through the injected client factory against
the real sub-application over a scratch database, with the exact stderr
assertions kept: that is the regression net for "the API carries the
repository's message and the CLI prints it". Added: the URL and token
resolution, the transport refusals with a sentinel in the userinfo, the
unrecognized-body case, the lock-held 409, the `--local` notice, subset
and boundary, and the awkward-name round trips through the whole client.
`tests/unit/test_config_api_writes.py` is the same surface from the HTTP
side, with every malformed-body path driven by a sentinel and checked
against the response, its headers and the captured log.

**Scripts.** The three seeding scripts start a server of their own, poll
`/healthz`, write over loopback with the image's own CLI and stop it
again, with the lifecycle in one sourced `tests/smoke/serve.sh` and the
server's log printed on any failure. `config.deploy.example.sh` is
rewritten to run against a running server, its measured values
untouched. Both sets of script tests move to the integration lane, the
seeds run unmodified with no fixture, and the deployment profile runs
verbatim against one new conftest fixture serving a built app on an
ephemeral loopback port.

**Documentation.** Getting Started reorders to start, configure,
restart, with the restart as its own step; the server README moves with
it where the switchover forces it. The full sweep is milestone 4.

### Two commits that arrive twice

The addressability rule (`071cd57`) and `set_secret`'s non-empty-string
refusal (`8c7c11b`) were written here because milestone 3 is where the
plan puts them, and milestone 2's own review round landed versions of
both while this branch was in flight. They are the first two commits on
this branch and touch nothing else, so at rebase they are expected to be
dropped in favour of milestone 2's, or merged with them where the
wording differs. What depends on them here does not depend on which
version wins: the write routes and the CLI meet the rule through the
repository, and the tests that would notice a wording change are the
ones in `tests/unit/test_config_store.py` that those same two commits
added.

### Deviations from the plan

**`RESTART_NOTICE` moved rather than being imported from the CLI.** The
plan says the API reuses `cli.RESTART_NOTICE`. That direction cannot be:
the CLI imports the API's mount path and its write vocabulary, so the
API importing the CLI would be a cycle, and even without one it would be
backwards for the server's admin surface to import the command-line
tool. The plan's actual requirement is that decision 5's contract is one
string in one place, and it is: `config/writes.py`, a leaf module with
no heavy imports, which is also what keeps `config schema` and `config
reference` from importing FastAPI on their way to printing a document
that has nothing to do with it.

**The CLI's default address carries the mount prefix.** The plan writes
the default as `http://127.0.0.1:<server.port>`, which cannot reach
anything: the sub-application is mounted at `/api` on the server's port,
which is what the plan's own surface table and the document's `servers`
entry say. The default is the port plus `API_MOUNT_PATH`, from the same
constant the server mounts on. Caught by running a seeding script
against a real server, which is the case an injected test client would
never have shown.

**`config list` is not in the `--local` subset.** The issue's four are
`show`, `delete`, `clear-secret` and `set-secret`, and `list` is not one
of them, so `--local list` is refused by naming the subset. `--local
show` is the reading command that needs no server, and the local
development example in the server README uses it.

**`_summary` is rendered from the masked document.** `config list` used
to walk the loaded models and the secret store; over HTTP what comes
back is the same masked document `show` prints, so the summary is built
from that. The output is unchanged, which the unchanged assertions pin.

### Discoveries

**A name a URL path cannot carry is unroutable, not refused.** The
addressability rule refuses such a name at write time in the repository,
which is where a caller that can reach the repository meets it. A caller
that goes through HTTP never gets that far: `quote("a/b")` is `a%2Fb`,
and both uvicorn and Starlette's TestClient decode the path before
routing, so the request reaches no route and answers 404. The entity is
not created either way, and the CLI test asserts that rather than a
sentence. It is also the concrete reason the rule exists: without it the
repository would happily create rows the API could never address.

**FastAPI deep-merges `openapi_extra` into what it generated.** A route
with a raw-object body gets an auto-generated request schema
(`{"title": "Body"}`), and `openapi_extra` merges key by key rather than
replacing, so the title survived beside the `$ref`. A `$ref` with a
sibling is ignored by some readers and confusing to the rest, so
`_resolve_body_schemas` reduces each write's body schema to the
reference it declared, and the drift test pins it.

**A body with no JSON content type arrives as `bytes`.** With
`Annotated[Any, Body()]`, FastAPI parses JSON only when the content type
says so and otherwise passes the raw bytes through. Nothing leaks: the
repository refuses a `bytes` fragment by naming its type, and the
exact-shape parsers refuse anything that is not a mapping. Worth knowing
before somebody assumes a handler can only ever see a decoded object.

**The port has to be exported, not only read.** A seeding script reads
`SAMTAL_SERVER__PORT` to know where to poll, but the CLI inside it
resolves the port through the settings machinery, which would otherwise
take the mounted file's value. Exporting the one value makes the server,
the CLI and the poll agree by construction.

**Uvicorn in a thread is what a subprocess test needs.** The
deployment-profile script is a subprocess, so the server it talks to
cannot live on the test's own event loop. `uvicorn.Server.run` in a
daemon thread works because uvicorn skips its signal handlers off the
main thread; the fixture waits on `server.started` with a deadline and a
liveness check rather than a bare loop.

### Notes for milestone 4

- The server README's API section, the deployment notes (token
  generation, ingress guidance for `/api/`, loopback-or-TLS, the upgrade
  note) and the markdown reference's API pointers are still to write.
  What milestone 3 touched is only what the switchover forced: the
  container walkthrough, the smoke-lane example, the domain-half
  section's "you need a server up" note, the local-development example,
  and the deployment profile's own header.
- The `--local` subset is documented in one paragraph of the server
  README and in the CLI's own help. The deployment notes are where the
  break-glass procedure belongs in full.
- `config.example.yaml` needs no change: the schema did not move in this
  milestone.
