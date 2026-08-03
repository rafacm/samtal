# M7 Hardening and release: implementation plan

This file details milestone M7 of the
[samtal-server v1 plan](2026-08-02-samtal-server-v1.md). It was agreed
in conversation on 2026-08-03 and is written so a fresh session can
implement it from the repository alone. Implementation notes do not go
in a companion to this file: they go in the v1 plan's own
[implementation doc](2026-08-02-samtal-server-v1-implementation.md), as
a new M7 section appended in the same change that ticks the milestone
in the v1 plan's checklist.

Scope from the v1 plan: device token auth on by default, connection
and session limits, structured logging (emit the heard/replied
conversation events as structured records, so log retention yields
transcripts until v3 brings a real conversation store), multi-arch
Docker image built in CI, README quick start. Accept: `docker run`
with one mounted YAML serves a conversation; image published. M7 is a
device checkpoint milestone: the acceptance is also verified against
the Waveshare board on the desk. This milestone is what makes the root
README's "Self-hosted end to end" container feature line true, and it
must also settle the single-port consequences the v1 plan parks here:
per-path idle timeouts, forwarded-header trust, and a drain period on
shutdown.

## State at the start of M7

M0 to M6 are merged (PRs #1 to #4 and #6 to #8), `main` is clean, no
open PRs. What M7 builds on and has to change:

- `ota.py` answers the version check and returns the websocket URL,
  but sends `"token": ""` with a comment saying M7 turns real tokens
  on (sent rather than omitted so a token left in NVS by another
  server is cleared; keep that behaviour). `Authorization` is read
  nowhere in the package.
- `session.py` calls `websocket.accept()` before validating anything;
  rejections (malformed MAC, no bound agent) are accept-then-close
  1008. `HELLO_TIMEOUT_S = 10` guards the hello. The conversation
  already logs session open/close, `heard "%s"`, `replied "%s"`, the
  per-agent `said "%s"` after a handover, and each tool call with
  duration and error state. The device MAC is a local variable in
  `run()`, not an attribute.
- `main.py` configures logging with `logging.basicConfig` (a comment
  says M7 replaces it) and calls `uvicorn.run(app, host, port)` with
  no graceful-shutdown, ping, or proxy settings.
- `ServerConfig` has only `host`, `port`, `websocket_url`,
  `protocol_version`, and `timezone_offset_minutes`. The OTA and
  websocket paths are module constants.
- There is no Dockerfile anywhere in the repository, and CI is one
  path-scoped `test` job (ruff, unit, integration).
- The token round trip already works on the client side everywhere:
  the stock firmware persists the OTA reply's token in NVS and sends
  `Authorization: Bearer <token>` plus `Protocol-Version`,
  `Device-Id`, and `Client-Id` on the websocket handshake (an empty
  token means no header at all), and xiaozhi-sdk, used by the
  integration fixtures, does the same with the same client UUID for
  OTA and websocket. Upstream's server rejects bad tokens before the
  connection is established, at HTTP level.
- Uvicorn facts verified in its source during planning: the websockets
  implementation pings every 20 s by default (`ws_ping_interval`);
  when `forwarded_allow_ips` is not passed it reads the
  `FORWARDED_ALLOW_IPS` environment variable (default `127.0.0.1`);
  and on shutdown it fail-closes every open websocket with 1012
  immediately, so `timeout_graceful_shutdown` alone does not drain
  conversations.

## Decisions already made (do not reopen)

Agreed with Rafael before this plan was written:

- **Auth is on by default, and a missing secret fails the boot.**
  `server.auth.enabled` defaults to true in the code, so the image
  inherits the default with no image-specific mechanics. Enabled auth
  with the secret env var unset refuses to boot, and the error names
  the variable, shows `openssl rand -hex 32`, and names the opt-out.
  Opting out is deliberate and visible: `enabled: false` in the YAML
  or `SAMTAL_SERVER__AUTH__ENABLED=false` through the existing env
  override path, one flag for a LAN trial. A forgotten secret never
  silently runs open (rejected: warn-and-boot-open, which turns a
  production misconfiguration into a silent hole).
- **The token is upstream's HMAC scheme, not JWT, not per-boot
  random.** `sig.ts` where sig is urlsafe base64 (no padding) of
  HMAC-SHA256 over `client_id|device_id|ts`. Stateless, no new
  dependency, proven against stock firmware, and it survives restarts
  and future multi-replica deployments (shared secret). Rejected: JWT
  (a dependency for no benefit, the token is opaque to the firmware)
  and random in-memory tokens (a restart would lock out every device
  holding an NVS-persisted token until its next OTA check).
- **No separate device allowlist.** The `devices` map plus
  `default_agent` already is one: omit `default_agent` and unknown
  MACs resolve to no agent, get no token, and are turned away.
  Rejected: an upstream-style `allowed_devices` key, redundant state
  to keep in sync.
- **Auth and capacity are checked before `websocket.accept()`.** A
  pre-accept close is an HTTP 403 on the upgrade, which is what
  upstream does and what the firmware handles by retrying and
  refreshing its token at the next OTA check. The existing MAC and
  no-agent rejections stay accept-then-close 1008 exactly as M5 built
  them, to avoid churning their tests.
- **The OTA endpoint stays unauthenticated; its protections are the
  configurable path and stingy issuance.** It is the token issuer, so
  a token check there would be circular. `server.ota_path` lets an
  operator hide it behind a long random segment (bearer-token-in-URL,
  per the v1 plan's security defaults), and a token is issued only
  when the MAC resolves to at least one agent. The websocket path
  stays fixed: the token protects it.
- **Limits are two keys, not a framework.** `max_sessions` (default
  8) rejects pre-accept; `max_session_s` (default 3600) bounds a
  session's total life and thereby also idles it out, so there is no
  separate idle key. The hello timeout stays a firmware-matched
  constant.
- **Explicit 20 s websocket pings settle the per-path idle timeout
  question.** Uvicorn already pings by default; M7 pins the values in
  code so they are load-bearing, documented, and immune to a default
  change. A proxy needs only a read timeout above 20 s, and the OTA
  path needs nothing special. No per-path timeout configuration
  anywhere.
- **Structured logging is stdlib-only.** A JSON formatter of roughly
  sixty lines is not worth a structlog dependency. Events ride
  `extra=` fields on the existing log calls, message text unchanged,
  so every caplog assertion in the test suite keeps passing and text
  mode stays exactly as readable as today. Retained JSON logs are the
  transcript store until v3.
- **The drain runs before uvicorn's shutdown, from a signal handler
  override.** Verified: uvicorn fail-closes websockets with 1012 the
  moment shutdown starts, so waiting for it to drain is not possible.
  First signal: stop admitting sessions, let in-flight replies finish
  speaking, close 1001, bounded by `server.drain_s` (default 20,
  inside the common 30 s orchestrator grace). Second signal: force.
  Uvicorn's own 1012 remains the backstop.
- **No `forwarded_allow_ips` config key.** Uvicorn already honours
  the `FORWARDED_ALLOW_IPS` environment variable when the setting is
  not passed, so the container supports proxy trust with zero code
  and `server.websocket_url` remains the explicit answer. Documented,
  not duplicated into the schema.
- **The image bakes both extras in.** Keyless `docker run` must serve
  a conversation, and piper is the only real TTS today. Measured
  cost: roughly 100 to 150 MB, because onnxruntime (the big wheel) is
  already a core dependency through pysilero-vad. Piper is GPL-3.0:
  shipping it in the image is aggregation, the project stays MIT, and
  the image's GPL contents are noted in `THIRD_PARTY_LICENSES.md` and
  the README. Model weights are still never baked in: `HOME=/data`
  sends every engine's cache to the mounted volume, downloaded at
  first start. Rejected: a lean image plus compose file with a
  separate speech container (needs the OpenAI-compatible ASR/TTS
  providers M4 deferred; still worth doing later, see deferrals) and
  engine installation at container start (slow boots, needs network
  and a writable filesystem, breaks the read-only-rootfs story).
- **One workflow, image publishing gated on the tests.** The existing
  `samtal-server.yml` gains an `image` job with `needs: test`, so a
  separate workflow coupled by `workflow_run` is not needed. PRs
  build and smoke-test the image but never push; pushes to `main`
  publish `ghcr.io/rafacm/samtal-server` tagged `latest`, the date,
  and the short SHA (date-based like the changelog; no semver yet).
  The arm64 half builds under QEMU first; native arm runners are the
  follow-up if the job proves slow.
- **The CI smoke test is a full conversation.** A new opt-in
  `tests/smoke` lane drives healthz, an OTA check that must return a
  verifiable token, and one complete xiaozhi-sdk conversation against
  the running container, which is the milestone acceptance encoded in
  CI. Rejected: curl-only checks, which would leave the acceptance
  manual.
- **Deferred out of M7**: OpenAI-compatible cloud ASR/TTS providers
  and the compose-file pairing they enable, image variants (core-only
  without the GPL extra), semver releases, OTA rate limiting beyond
  the secret path, and native arm runners.

## Design

### 1. Structured logging

New `samtal_server/logs.py`:

```python
class JsonFormatter(logging.Formatter):
    """One JSON object per line: ts (ISO 8601 UTC), level, logger,
    message, exc_info when present, plus every extra= attribute."""

def configure(server: ServerConfig) -> None:
    """Install the root handler: text (today's format) or json."""
```

Extras are found by comparing a record's `__dict__` against the
standard `logging.LogRecord` attribute set, the stdlib-blessed trick,
so call sites need no wrapper. `ServerConfig` gains
`log_format: Literal["text", "json"] = "text"` and
`log_level: str = "INFO"` (validated against the logging level names).
`main.py` drops `basicConfig`, calls `logs.configure(config.server)`
after the config loads (config errors before that still print to
stderr as today), and passes `log_config=None` to uvicorn so its
loggers propagate into the same handler and format.

Conversation events become `extra=` fields on the existing calls,
message text untouched. Every event carries `event`, `session`, and
`device`; the table lists the rest:

| event             | call site                  | extra fields                       |
| ----------------- | -------------------------- | ---------------------------------- |
| `ota_check`       | ota.py resolution log      | client, board, firmware, agents    |
| `session_open`    | session accepted log       | client, agent, agents, protocol    |
| `heard`           | `heard "%s"`               | agent, text, duration_s            |
| `replied`         | `replied "%s"`             | agent, text                        |
| `agent_said`      | per-agent `said "%s"`      | agent, text                        |
| `handover`        | agent switch log           | from_agent, to_agent               |
| `tool_call`       | tool duration log          | agent, tool, duration_ms, is_error |
| `session_closed`  | session closed log         | duration_s                         |
| `session_rejected`| MAC/agent/limit rejections | reason                             |
| `auth_rejected`   | new, ws.py                 | reason                             |

Filtering retained JSON logs on `event in (heard, replied,
agent_said)` grouped by `session` yields the transcript. `Session`
stores the MAC as `self._mac` (set before the rejection paths so
rejects carry it); tokens are never logged, at any level.

### 2. Device token auth

New `samtal_server/auth.py`, crediting upstream's scheme in a comment:

```python
class DeviceAuth:
    def __init__(self, secret: str, expire_s: int) -> None: ...
    def issue(self, client_id: str, device_id: str) -> str:
        """b64url(HMAC-SHA256(secret, f"{client_id}|{device_id}|{ts}"))
        without padding, then f"{sig}.{ts}"."""
    def verify(self, token: str, client_id: str, device_id: str) -> bool:
        """Re-derive and hmac.compare_digest; expired, malformed, or
        mismatched anything is False, never an exception."""

def build_device_auth(config: Config) -> DeviceAuth | None:
    """None when disabled; ConfigError when enabled and the secret
    env var is unset (message names the var, shows openssl rand -hex
    32, and names both opt-outs)."""
```

Config, nested under `server`:

```yaml
server:
  auth:
    enabled: true                     # the default
    secret_env: SAMTAL_AUTH_SECRET    # env var holding the secret
    token_expire_s: 2592000           # 30 days, upstream's default
```

`AuthConfig` follows the `_env` secrets convention (the value is the
variable's name, never the secret). `create_app` builds the instance
once and hangs it on `app.state.device_auth`, so a bad secret setup
fails the boot the way bad provider config does.

Issuing: `ota.py` calls `issue(client_id, device_id)` only when the
MAC resolves to at least one agent and auth is enabled; every other
case keeps sending `""` (which also clears a foreign token from NVS).
The expiry is refreshed naturally because the firmware re-checks OTA
on every boot.

Checking: `ws.py` gains the pre-accept gate. Read `authorization`,
`device-id`, and `client-id` from the handshake headers; require the
`Bearer ` prefix and strip it (both firmware and sdk send it); verify
against the same id pair the OTA reply was issued for. On failure log
`auth_rejected` and close without accepting, which uvicorn turns into
an HTTP 403 on the upgrade. When auth is disabled the gate is a
no-op. `session.py` is untouched by auth.

### 3. Configurable OTA path

`ServerConfig` gains `ota_path: str = "/xiaozhi/ota/"`, validated to
start and end with `/`. `ota.py`'s router is built by
`build_router(path: str)` and registered from `create_app` instead of
at import time. Operators exposing the server publicly append a long
random segment (`/xiaozhi/ota/8f3a…/`) and write that URL into the
device's NVS; the README documents generating one. The websocket path
stays the fixed module constant.

### 4. Connection and session limits

New `samtal_server/registry.py`:

```python
class SessionRegistry:
    def __init__(self, max_sessions: int) -> None: ...
    def try_add(self, session: Session) -> bool   # False when full/draining
    def remove(self, session: Session) -> None    # idempotent
    async def drain(self, timeout_s: float) -> None
    @property
    def draining(self) -> bool: ...
```

Config:

```yaml
server:
  limits:
    max_sessions: 8         # concurrent conversations
    max_session_s: 3600     # one session's maximum life
```

`create_app` owns one registry on `app.state`. `ws.py` calls
`try_add` in the pre-accept gate (after auth, so a full server still
answers a bad token with 403) and logs `session_rejected` with reason
`capacity` when full; `remove` runs in the session's `finally`.
`Session.run` wraps `_serve` in `asyncio.timeout(max_session_s)`; on
expiry it lets an in-flight reply finish briefly, then closes 1000
with reason "session time limit reached". The stock firmware treats
the close as the end of a conversation and reconnects on the next
wake word, so the cap is invisible in normal use.

### 5. Graceful shutdown and drain

`ServerConfig` gains `drain_s: float = 20.0`. `main.py` stops using
`uvicorn.run` and builds `uvicorn.Config`/`uvicorn.Server` directly,
with `ws_ping_interval=20.0`, `ws_ping_timeout=20.0`, and
`timeout_graceful_shutdown=5`, and subclasses the server:

```python
class DrainingServer(uvicorn.Server):
    def handle_exit(self, sig, frame):
        # First signal: schedule the drain, then let uvicorn exit
        # when it completes. Second signal: defer to uvicorn (force).
```

The drain: the registry stops admitting sessions, every live session
gets `await session.request_shutdown()` concurrently (each waits for
its in-flight reply to finish speaking, then closes 1001 "server
shutting down"), all bounded by `drain_s`; then `should_exit` is set
and uvicorn's ordinary shutdown handles the remains, its immediate
1012 fail-close acting as the backstop for anything stuck.
`Session.request_shutdown()` is also what the duration cap and the
drain share, so close behaviour lives once. The Dockerfile's exec-form
entrypoint is what lets SIGTERM reach this handler; the README tells
operators to give `docker stop` a timeout above `drain_s`.

### 6. Container image

`samtal-server/Dockerfile`, two stages, plus `.dockerignore` (`.venv`,
`tests`, caches, `*.local.yaml`, `memory.local`):

```dockerfile
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev --no-editable \
    --extra faster-whisper --extra piper
COPY samtal_server ./samtal_server
COPY README.md ./
RUN uv sync --frozen --no-dev --no-editable \
    --extra faster-whisper --extra piper

FROM python:3.12-slim-bookworm
RUN useradd --uid 1000 --home-dir /data --no-create-home samtal \
    && mkdir /data && chown samtal /data
COPY --from=builder /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH \
    HOME=/data \
    SAMTAL_CONFIG=/config/config.yaml \
    SAMTAL_SERVER__LOG_FORMAT=json
USER samtal
VOLUME /data
EXPOSE 8003
HEALTHCHECK CMD ["python", "-c", \
    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8003/healthz')"]
ENTRYPOINT ["samtal-server"]
```

The dependency layer builds from `pyproject.toml` and `uv.lock` alone
so code changes do not re-download wheels. `HOME=/data` sends the
Hugging Face cache (whisper models) and piper's voice cache to the
volume: models download at first start and are never baked in, per
the repository rule. JSON logs are the container default, overridable
like any config key. The healthcheck assumes the default port; an
operator changing `server.port` overrides the healthcheck too,
documented rather than templated. A read-only rootfs works
(`--read-only --tmpfs /tmp` plus the two mounts) and is documented,
not enforced. Expect an image in the several-hundred-MB range;
"small" means slim base and no models, not small absolute size.

### 7. CI: build, smoke test, publish

The `image` job in `.github/workflows/samtal-server.yml`, with
`needs: test` and `permissions: {contents: read, packages: write}`:

1. Set up buildx and QEMU.
2. Build the amd64 image with `load: true` (GHA layer cache).
3. Sanity-import the extras in the built image:
   `docker run --rm --entrypoint python <image> -c "import
   faster_whisper, piper"`. Repeat under QEMU for the arm64 build, so
   a wheel that silently has no aarch64 variant fails CI, not a user.
4. Run the container with the smoke config mounted at
   `/config/config.yaml`, a throwaway `SAMTAL_AUTH_SECRET`, and a
   published port; wait for the healthcheck.
5. `uv run pytest tests/smoke -v` with `SAMTAL_SMOKE_OTA_URL`
   pointing at the container.
6. On `push` to `main` only: log in to GHCR with `GITHUB_TOKEN` and
   push `linux/amd64,linux/arm64` via `docker/build-push-action`,
   tags from `docker/metadata-action`: `latest`, `YYYY-MM-DD`,
   `sha-<short>`.

`tests/smoke/` is a new lane, skipped entirely unless
`SAMTAL_SMOKE_OTA_URL` is set (same pattern as the local lane), so
plain `pytest` runs are unaffected. It ships its own
`tests/smoke/config.yaml` (mock providers, so the smoke conversation
needs no models or keys) and asserts: healthz answers, the OTA reply
carries a websocket URL and a token that `DeviceAuth` verifies, and
one full sdk conversation completes with the expected mock reply. The
lane also runs locally against any reachable server, which is how the
device-checkpoint image is pre-flighted.

### 8. Tests

Unit (no network, as today):

- `test_auth.py`: issue/verify round trip, expiry boundary, tampered
  signature, wrong device or client id, malformed tokens (`""`,
  `"a"`, `"a.b.c"`, non-integer ts), never-raises guarantee;
- config: auth defaults (enabled, env var name), limits bounds,
  `ota_path` validation, `log_format`/`log_level` validation, every
  new key exercised through `config.example.yaml` parsing as today;
- `test_logs.py`: JSON records carry ts/level/logger/message, extras
  surface, exc_info formats, text mode is byte-identical to today's
  format; event fields asserted via caplog `record` attributes on a
  real `heard`/`replied` flow;
- boot: enabled auth without the secret fails with the helpful
  message; disabled auth boots without it;
- OTA: token issued and verifiable for a bound device, `""` when
  disabled and when the MAC resolves to no agent, custom `ota_path`
  serves and the default path 404s when changed;
- websocket gate: valid token handshakes (the existing `connect()`
  helper in `test_session.py` gains a real Authorization header from
  an issued token), missing/malformed/expired token never reaches
  accept (TestClient raises on the refused upgrade), capacity
  rejection at `max_sessions`, auth-before-capacity ordering;
- session: duration cap with a tiny `max_session_s` closes 1000
  mid-idle and post-reply; `request_shutdown` during a streaming
  reply lets the reply finish and closes 1001;
- registry: add/remove/idempotence, draining refuses admission,
  drain timeout.

Integration (xiaozhi-sdk against a running server, mock providers):

- an autouse fixture in `tests/integration/conftest.py` sets a test
  `SAMTAL_AUTH_SECRET`, so the whole existing lane now runs with auth
  on by default and the sdk forwarding real tokens; the OTA test's
  token assertion flips from `== ""` to verifiable;
- a raw `websockets.connect` with a doctored token and with no token
  is refused at the handshake;
- `max_sessions: 1`: one simulator converses while a second
  connection is refused, then admitted after the first closes;
- drain: trigger the registry drain mid-reply, assert the reply
  finishes, the socket closes 1001, and a new connection is refused
  while draining.

Smoke: as designed in section 7, the acceptance made repeatable.

Local lane: unchanged; real engines are the device checkpoint's job.

Device checkpoint (recorded in the implementation doc): run the
published image on the LAN (real YAML with faster-whisper and piper,
`SAMTAL_AUTH_SECRET` set, JSON logs), point the board's NVS `ota_url`
at it (give the dev machine a DHCP reservation first, per the M6
checkpoint's note), and hold a conversation on stock firmware.
Verify: the token round trip appears in the logs, the heard/replied
JSON records read back as a transcript, a wrong secret locks the
device out and an OTA re-check with the right secret recovers it, and
`docker stop` mid-reply lets the sentence finish before the container
exits.

### 9. Documentation

- `config.example.yaml`: `server.auth`, `server.limits`,
  `server.ota_path`, `server.log_format`, `server.log_level`, and
  `server.drain_s`, each in the same commit as its schema change
  (repository rule).
- `samtal-server/README.md`: new Security section (the token flow,
  the secret, the OTA path segment, only two public endpoints, the
  LAN opt-out), Limits, Logging (the event table, transcripts from
  retained logs), and Running in a container (volumes, env overrides,
  read-only rootfs, healthcheck, stop timeout); the reverse-proxy
  section gains the 20 s pings and `FORWARDED_ALLOW_IPS`; the stale
  "MCP tools land in a later milestone" line and the Status section
  are corrected.
- Root `README.md`: Getting Started becomes the quick start with both
  invocations (LAN trial with `-e SAMTAL_SERVER__AUTH__ENABLED=false`,
  real deployment with `SAMTAL_AUTH_SECRET` and a config mount), the
  container feature line loses its 🚧, and the project layout table's
  samtal-server row is updated.
- `THIRD_PARTY_LICENSES.md`: note the published image's contents
  (piper-tts GPL-3.0 as aggregation, PyAV's bundled FFmpeg LGPL).
- `CHANGELOG.md`: the M7 bullet under `### Added`, and the first use
  of `### Security` for auth-on-by-default.

## Commit breakdown

Small commits, one logical change each, imperative titles of roughly
50 characters with bodies explaining what and why:

1. JSON log formatter, `log_format`/`log_level` keys, `main.py`
   wiring, `config.example.yaml`, unit tests
2. Structured event fields on the conversation logs, `self._mac`
   lift, unit tests
3. HMAC device token scheme in `auth.py`, unit tests
4. `server.auth` config and boot-time secret check,
   `config.example.yaml`, unit tests
5. OTA endpoint issues device tokens, unit and integration tests
6. Websocket handshake checks device tokens pre-accept, unit and
   integration tests
7. Configurable OTA path, `config.example.yaml`, unit tests
8. Session registry and concurrent-session cap, `server.limits`,
   `config.example.yaml`, unit and integration tests
9. Session duration cap, unit tests
10. Drain on shutdown, `server.drain_s`, explicit ping and
    graceful-shutdown settings, `config.example.yaml`, unit and
    integration tests
11. Dockerfile and `.dockerignore`
12. Smoke lane (`tests/smoke`, its mock config)
13. CI image job: build, sanity imports, smoke, publish to GHCR
14. README quick start, server README sections, third-party notes,
    `CHANGELOG.md`
15. Plan checklist tick and implementation doc M7 section (rides in
    the PR that completes the milestone)

## Process constraints

- Branch `feature/hardening-and-release` off `main`; verify `main` is
  current first (`git pull --rebase`). Never commit code to `main`.
- Run from `samtal-server/`: `uv run pytest tests/unit -q`,
  `uv run pytest tests/integration -q`, `uv run ruff check .` before
  every push. CI runs the same, plus the image job.
- The workflow file change rides the same PR and, being in the path
  filter, exercises the image job on the PR itself (build and smoke
  only; publishing runs on `main`).
- Open the PR when done (title per the repository convention:
  imperative verb plus deliverables, never a bare "M7:" prefix; body
  with a Verification task list, boxes checked only for steps
  actually carried out, and no hard-wrapped lines in the PR body).
  **Do not merge it.** The device checkpoint needs the desk, so its
  boxes stay unchecked with a note until it is run.
- The implementation doc section records deviations from this plan,
  or states explicitly that there were none.
- No em-dashes anywhere: docs, commit messages, code comments.
