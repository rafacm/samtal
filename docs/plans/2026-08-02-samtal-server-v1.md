# samtal-server v1 plan

## Goal

A device running stock upstream xiaozhi firmware, with only its NVS `ota_url`
pointed at samtal-server, holds a spoken conversation through providers we
configure. Configuration only: no UI, no database. Different devices can be
bound to different agents through the config file.

## Non-goals for v1

- No web UI, user accounts, families, or budgets (v3 concerns; the config
  model must not block them)
- No firmware work; the upstream prebuilt binaries are the device side
- No external pipeline-orchestration framework; the protocol edge stays
  isolated behind a small interface so one could be adopted later without
  rework
- WebSocket transport only: no MQTT/UDP, no vision endpoint, no firmware OTA
  update serving (the OTA endpoint answers version checks with "up to date")
- No voiceprint recognition

## Architecture

```
                      FastAPI (uvicorn)
  POST /xiaozhi/ota/  ──────────────►  ota: device registry lookup,
                                            returns websocket URL (+ token)
  WS   /xiaozhi/v1/   ──────────────►  protocol edge: xiaozhi JSON messages
                                            + binary Opus frames ◄─► events
                                       session: one per connection
                                            VAD ► ASR ► LLM (+tools) ► TTS
                                       providers: pluggable per agent
```

Modules, roughly one package each:

- **`config`**: pydantic models loaded from one YAML file plus env overrides.
  Top-level keys: `server`, `providers`, `agents`, `devices` (MAC to agent
  binding), `default_agent`. Secrets referenced by env var name, never inline.
  Instance configs never live in this repository: the server reads the config
  path from `SAMTAL_CONFIG` (or `--config`), the repo commits only a
  documented `config.example.yaml`, and `*.local.yaml` is gitignored for
  local experiments. A personal deployment keeps its real config (and
  secrets) wherever it is deployed from.
- **`protocol`**: the xiaozhi wire protocol, isolated from everything else.
  Message types (`hello`, `listen`, `abort`, `tts`, `stt`, `llm`, `mcp`),
  binary Opus framing (protocol versions 1 to 3), handshake headers
  (`Device-Id`, `Client-Id`, `Authorization`). Upstream reference:
  `docs/websocket.md` and `docs/mcp-protocol.md` in the firmware repo.
- **`audio`**: Opus decode/encode and resampling (16 kHz mono 60 ms frames in,
  24 kHz out), buffering between frame cadence and pipeline chunks.
- **`pipeline`**: the per-session conversation loop: VAD segments speech, ASR
  transcribes, LLM streams a reply with tool calling, TTS streams audio back.
  Interruption support (device barge-in aborts TTS) from the start, since the
  protocol carries `abort`.
- **`providers`**: one small interface per stage with swappable
  implementations. v1 set:
  - LLM: Anthropic, OpenAI-compatible (covers Ollama, LM Studio, gateways)
  - ASR: one local engine plus OpenAI-compatible cloud (open question below)
  - TTS: one keyless default plus OpenAI-compatible cloud (open question
    below; `edge-tts` only ever as an optional extra, it is GPL-3.0)
  - VAD: Silero
- **`tools`**: MCP client manager. Per agent: configured MCP servers
  (stdio/streamable-http) plus the device's own MCP tools discovered over the
  websocket, merged into the LLM tool list.

## Milestones

Each milestone is mergeable and ends green in CI. "Device checkpoint" means a
manual test against real hardware on the desk. Tick a milestone (with its PR
number) in the same change that completes it, linking it to its section in
the [implementation notes](2026-08-02-samtal-server-v1-implementation.md).

- [x] **[M0 Skeleton](2026-08-02-samtal-server-v1-implementation.md#m0-skeleton-pr-1-merged-2026-08-02)**
  (PR #1, merged 2026-08-02): uv project, package layout, pytest, ruff, and
  the GitHub Actions workflow described under Testing strategy. Accept:
  `uv run pytest` passes on a trivial test locally and in CI, and the
  workflow only runs when `samtal-server/` changes.
- [x] **[M1 Config](2026-08-02-samtal-server-v1-implementation.md#m1-config-pr-2-merged-2026-08-02)**
  (PR #2, merged 2026-08-02): pydantic config models, YAML loading,
  validation errors that actually help. Accept: example config parses; bad
  configs fail with clear messages.
- [x] **[M2 OTA endpoint](2026-08-02-samtal-server-v1-implementation.md#m2-ota-endpoint-pr-3)**
  (PR #3): device POST answered with websocket URL and firmware "up to
  date"; unknown devices get `default_agent`. Accept: simulator and `curl`
  get correct JSON. Device checkpoint: real board redirected to
  samtal-server reaches the websocket.
- [x] **[M3 Protocol handshake and audio loop](2026-08-02-samtal-server-v1-implementation.md#m3-protocol-handshake-and-audio-loop-pr-4)**
  (PR #4): websocket accept, `hello` exchange, Opus decode/encode round
  trip (echo or canned reply). Accept: xiaozhi-sdk connects, completes
  hello, exchanges audio frames in CI.
- [x] **[M4 Conversation pipeline](2026-08-02-samtal-server-v1-implementation.md#m4-conversation-pipeline-pr-6)**
  (PR #6): VAD, ASR, LLM, TTS wired with mock providers in CI and real
  providers locally. Accept: scripted simulator conversation gets a
  coherent spoken reply. Device checkpoint: first real conversation.
- [ ] **M5 Agents and bindings**: per-device agent resolution, per-agent
  prompt, providers, and voice. Accept: two simulated devices with different
  MACs get different personas in one server run.
- [ ] **M6 Tools/MCP**: server-side MCP servers per agent plus device MCP
  tools, round-tripped through LLM tool calling. Accept: simulator
  conversation triggers a mock MCP tool and the reply reflects its result.
- [ ] **M7 Hardening and release**: device token auth on by default,
  connection and session limits, structured logging (emit the heard/replied
  conversation events as structured records, so log retention yields
  transcripts until v3 brings a real conversation store), multi-arch Docker
  image built in CI, README quick start. Accept: `docker run` with one mounted
  YAML serves a conversation; image published.

## Testing strategy

- **Unit**: protocol codec (message parsing, Opus framing), config
  validation, audio buffering. No network.
- **Integration**: `xiaozhi-sdk` as a virtual device against a running server
  in CI. Mock providers (deterministic ASR/LLM/TTS stubs) keep CI free of
  keys, model downloads, and flakiness. A second lane with Ollama can run
  locally.
- **Hardware checkpoints**: M2, M4, and M7 verified against the
  Touch-LCD-1.54 on the desk.
- **CI on GitHub Actions**, scoped to this folder: a
  `.github/workflows/samtal-server.yml` workflow triggered only on changes
  under `samtal-server/**` (plus the workflow file itself), with
  `samtal-server` as the working directory. Jobs: lint (ruff), unit tests,
  and integration tests (server + xiaozhi-sdk simulator + mock providers) as
  separate steps so failures read clearly. Python 3.12 via uv with dependency
  caching. Unit and integration tests are both required for merge; the
  integration job needs no secrets, no model downloads, and no GPU, so it
  stays fast and reliable. Future subprojects (e.g. samtal-esp32) get their
  own path-scoped workflows following the same pattern.

## Packaging and deployment

- uv-managed project; heavyweight optional deps (local ASR engines, GPL TTS)
  as extras so the core install stays lean.
- Multi-arch (arm64 + amd64) Docker image; models and voices downloaded on
  first start to a mounted volume, never baked into the image.
- Config via one mounted YAML plus env vars for secrets.
- Security defaults per the agreed layers: auth enabled by default,
  configurable OTA path segment, no public endpoints beyond the two the
  device needs.
- Single-port consequences to settle in M7. Both endpoints share
  `server.port` and are told apart by path, which pushes work onto whatever
  routes to them; the README documents the operator-facing version, and M7
  is where the image and its defaults have to answer for it:
  - **Idle timeouts differ per path.** The conversation socket goes quiet
    between utterances, so a timeout tuned for short HTTP requests cuts
    conversations off; the OTA path wants the opposite. Where only one
    value can be set, the long one has to win. Worth deciding whether the
    server sends WebSocket pings to keep the socket alive under proxies
    that cannot be configured per path, since that removes the tradeoff
    entirely.
  - **Forwarded headers are not trusted by default.** Uvicorn's
    `--forwarded-allow-ips` defaults to `127.0.0.1`, so behind a
    TLS-terminating proxy the derived URL says `ws://` instead of `wss://`.
    Today the answer is to set `server.websocket_url` explicitly; M7 should
    decide whether the image exposes that setting so the derivation works
    unattended.
  - **A redeploy ends every conversation in flight**, and OTA cannot be
    restarted independently of it. The image needs a drain period on
    shutdown, which means handling the termination signal rather than dying
    on it.
  - Orchestrator-specific manifests stay with the deployment, not in this
    repository.

## Open questions (decide during M4, not before)

- Local ASR default: faster-whisper (multilingual, covers Swedish, heavier)
  vs FunASR/SenseVoice (what upstream uses, zh/en focused). Both fit the
  provider interface; pick after measuring latency on target hardware.
- TTS keyless default: Piper (local, permissive) vs edge-tts (free cloud but
  GPL-3.0 and unofficial API, extras-only either way).
- Opus bindings: opuslib vs PyAV; decide on wheel quality for arm64.
- Binary protocol version to advertise (1 vs 2/3 with timestamps) after
  testing against firmware v2.4.0.

## Later versions (not planned here)

v2: richer interruption handling, more providers, possibly a pipeline
orchestration framework. v3: SQLite persistence, users/families/budgets, admin API and UI,
voiceprint for shared devices. Device side: `esp_xiaozhi` component spike for
a no-fork custom firmware.
