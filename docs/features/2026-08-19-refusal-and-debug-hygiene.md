# Refusal and debug hygiene: the two surfaces a level and a name reopen

**Date:** 2026-08-19

## Problem

Two ways for something unsanitized to reach the surfaces the
[content-and-telemetry ADR](../adr/2026-08-15-content-and-telemetry-are-separate-surfaces.md)
governs, neither of them a line this repository writes on purpose.
They are a pair because both are the same mistake in different clothes:
a decision taken carefully at one site is undone somewhere else by a
default.

**#124, the level.** `logs.configure` already held four libraries below
the server's own level, because the openai and anthropic clients log
response headers verbatim, with httpx and httpcore tracing underneath.
Two more carry this deployment's own bytes and were not on the list.

- `uvicorn.error` is the HTTP server's trace. At debug it prints the
  request line and every request header, which is exactly what
  `main.uvicorn_config` turns the access log off to keep off the log:
  the OTA path holds the deployment's secret segment, and a device's
  handshake carries its bearer token. uvicorn hands that same logger to
  the websockets protocol it serves with, so the same records render
  every device frame's payload, text decoded, which is the room.
- `sqlalchemy` is the same shape one level lower and one level louder.
  Its payload is not behind DEBUG: an engine whose logger is enabled
  for **INFO** echoes every statement with the parameters bound to it,
  and those parameters are the stored configuration.

`server.log_level: DEBUG` is the ordinary thing to turn on while
diagnosing something, and that was all it took.

**#132, the name.** A read or a delete of something that is not there
answered with the identity it had been asked for:
`providers.llm.<name>: no such provider`, and the same shape for MCP
servers, agents and devices. That identity is a value nothing in this
deployment has validated. It arrived in a URL path or on a command
line, which is where a paste lands, and the sentence built from it
travels out as a 404 body, as a printed line, and into whatever the
caller keeps. The prompt-fragments section already answered one fixed
sentence for exactly this reason; four of its neighbours did not.

## Changes

### #124: two more libraries under the floor

`VENDOR_LOGGERS` and its single `VENDOR_LOG_FLOOR` become
`VENDOR_LOG_FLOORS`, a name-to-floor mapping, because the two levels
are now different: INFO for the libraries whose payload is at debug
(anthropic, httpcore, httpx, openai, `uvicorn.error`), WARNING for
sqlalchemy, whose payload is at info. The comment names each exposure
where the floor is set.

INFO keeps what is worth keeping and carries none of it: httpx's one
line per request (method, URL, status, no headers and no body), and
uvicorn's startup and per-connection lines.

Two decisions recorded in the code rather than left to be rediscovered:

- **The MCP SDK is deliberately not on the list.**
  `tools/mcp/transport.py` already takes that whole namespace off this
  server's handlers (`quiet_sdk_loggers`, #140): a filter on the four
  client loggers and `propagate = False` on `mcp` itself. That is
  stronger than a floor, and it is owned by the module that connects
  with it. A floor here as well would be a second rule to keep in
  agreement with the first.
- **The sqlalchemy floor is not a reliance on the library's own.**
  SQLAlchemy pins `logging.getLogger("sqlalchemy")` at WARNING when it
  is imported, which is why nothing was leaking through it today. The
  floor makes the guarantee this deployment's, and its test clears the
  library's pin first so that the floor is what is under test.

There is no configuration key to lift any of them, and deliberately so:
a diagnosis that genuinely needs a wire trace raises the logger by name
in the process that needs it, which is a deliberate act rather than a
side effect of the server's own level.

### #132: one fixed sentence per section

The refusals, old to new:

| Old | New |
| --- | --- |
| `providers.<stage>.<name>: no such provider` | `providers: no provider of that name exists for that stage` |
| `mcp_servers.<name>: no such MCP server` | `mcp_servers: no MCP server of that name exists` |
| `agents.<name>: no such agent` | `agents: no agent of that name exists` |
| `devices.<mac>: no such device` | `devices: no device with that MAC is bound` |
| `prompt_fragments: no prompt fragment of that name exists` | unchanged |
| `<kind> <identity> <slot>: no secret is stored for this slot` | `<section>: no secret is stored for that slot` |
| `<kind> <identity> <slot>: no such entity; create it first with samtal-server config set` | `<the section's own sentence>; create it first with samtal-server config set` |

Fixed for every kind, including the ones whose identity has a rigid
shape (a MAC, a stage word). A MAC that reached a refusal is a MAC only
in the sense that something parsed it that way, and a rule with an
exception in it is a rule that gets the exception wrong later.

The shape follows from that. `EntityDescriptor.missing` stops being a
hook and becomes the string it always wanted to be, since nothing about
the answer depends on the request any more. The devices map is a
`Setting` rather than an entity, so that tier grows the same fact and a
`setting()` accessor to match `descriptor()`, rather than one tier's
sentence being a loose constant and the rest being descriptor facts.
The two entity-miss refusals inside `_check_slot` raise through the
descriptor already, so they move with it. `store.py` stops re-exporting
`NO_SUCH_FRAGMENT`: the five sentences live together in `entities.py`,
and everything reads them there.

## Key parameters

- `VENDOR_LOG_FLOORS` (`samtal_server/logs.py`): `anthropic`,
  `httpcore`, `httpx`, `openai` and `uvicorn.error` at INFO,
  `sqlalchemy` at WARNING. Applied as `max(server level, floor)`, so it
  only ever quietens.
- `NO_SUCH_PROVIDER`, `NO_SUCH_MCP_SERVER`, `NO_SUCH_FRAGMENT`,
  `NO_SUCH_AGENT`, `NO_SUCH_DEVICE`
  (`samtal_server/config/entities.py`): the five refusal sentences,
  each carried by its kind's descriptor.

No configuration keys, no event fields and no event sentences changed,
and all four committed references regenerate byte-identical: no refusal
sentence is rendered into any of them.

## What was checked and deliberately left

- **The MCP SDK's two remaining renderings.** `quiet_sdk_loggers` takes
  the namespace off this server's handlers, so nothing reaches the
  retained log. Worth knowing for anyone who attaches a handler to
  `mcp` on purpose: `mcp.client.streamable_http` logs a parse failure
  with `logger.exception`, and `Raw result: ...` at warning, both of
  which render what the far side sent. A level floor could not have
  closed either.
- **The refusals that describe a shape rather than an absence.** `"..."
  is not a MAC address`, `"..." is not a credential slot on a provider`
  and the two dotted-slot rules quote what they refused, and the last
  two are reached only after the entity was confirmed to exist, so what
  they name is stored configuration rather than a caller's paste. The
  MAC rule is not: it refuses the value it prints. That is a different
  family from an entity miss, and the issue's boundary (the hint lists
  in reference-check refusals are a separate surface) puts it outside
  this change; it is worth its own look.
- **The CLI's own HTTP client.** `samtal-server config` talks to the
  API over a socket, and httpx narrates the URL it requested, identity
  included, at INFO. That is the client saying what the operator just
  typed, on a process that installs no handler, rather than anything a
  server retained; the sentinel tests filter to this project's own
  records for that reason, as the earlier rounds do.

## Verification

- Lint: `uv run ruff check .` clean.
- Unit: `uv run pytest tests/unit -q`, 3028 passed and 16 skipped
  (3008 before).
- Integration: `uv run pytest tests/integration -q`, 59 passed (58
  before).
- All four committed-reference drift checks (domain config,
  conversations schema, events, OpenAPI) diff empty.
- Every sentinel was proven to bite, by reverting the fix in place,
  watching the test fail for the right reason, and restoring the file
  from a copy and touching it (never `git checkout`, per `AGENTS.md`).
  - #124, the served run: with `uvicorn.error` off the floor, the
    integration test failed with the OTA secret segment, the device's
    bearer token and the hello frame's payload (`< TEXT '{"type":
    "hello"...`, and the server's own reply frame beside it) in the
    log.
  - #124, the engine: with `sqlalchemy` off the floor, the unit test
    failed on the bound parameter appearing in `caplog.text`. Its
    companion, which leaves the engine's logger alone, is what says the
    property is reachable at all.
  - #132: with the identity interpolated back into the read and the
    delete, the API test failed on the credential in the 404 body and
    the CLI test on the same value on stderr, on every section. The
    leak is asserted before the wording in both, so the failure is the
    leak rather than a changed sentence.

## Files modified

- `samtal-server/samtal_server/logs.py`
- `samtal-server/samtal_server/config/entities.py`
- `samtal-server/samtal_server/config/store.py`
- `samtal-server/tests/unit/test_logs.py`
- `samtal-server/tests/unit/test_config_api.py`
- `samtal-server/tests/unit/test_config_api_reads.py`
- `samtal-server/tests/unit/test_config_api_writes.py`
- `samtal-server/tests/unit/test_config_cli.py`
- `samtal-server/tests/unit/test_config_cli_local.py`
- `samtal-server/tests/unit/test_config_cli_secrets.py`
- `samtal-server/tests/unit/test_config_reads.py`
- `samtal-server/tests/unit/test_config_refusals.py`
- `samtal-server/tests/unit/test_config_store.py`
- `samtal-server/tests/integration/test_access_logs.py`
- `CHANGELOG.md`
