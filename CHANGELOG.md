# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
using dates (`## YYYY-MM-DD`) as section headers instead of version numbers.

## 2026-08-03

### Added

- samtal-server hardening and release (M7): the server is now something
  you can deploy. It ships as a multi-arch container image
  (`ghcr.io/rafacm/samtal-server`, amd64 and arm64, tagged `latest`, the
  build time, and the commit SHA), built and published by CI only after
  the tests pass, with both local engines baked in so one `docker run`
  with one mounted YAML serves a conversation. Model weights are still
  never baked in: `HOME` points at the mounted volume, where whisper
  models and Piper voices download at first start. A fourth test lane,
  `tests/smoke`, holds a whole conversation with the freshly built
  container in CI, which turns the milestone acceptance into something
  checked rather than remembered.
- Structured logging: `server.log_format` (`text` or `json`, and `json`
  is the image's default) and `server.log_level`. Every conversation
  event now carries structured fields alongside its human sentence
  (`event`, `session`, `device`, plus what the event holds), so retained
  JSON logs filtered on `heard`/`replied`/`agent_said` and grouped by
  session read back as transcripts. That stands in for a conversation
  store until v3 brings a real one.
- Limits and a graceful shutdown: `server.limits.max_sessions` (eight
  concurrent conversations) and `server.limits.max_session_s` (an hour,
  which bounds an idle session too, so there is no separate idle key).
  On SIGTERM the server stops admitting sessions and lets every reply in
  flight finish speaking before closing those sockets, inside
  `server.drain_s`; a second signal forces the exit. Uvicorn cannot do
  this part, since it fail-closes every websocket with 1012 the moment
  its own shutdown begins.

### Changed

- `docs/xiaozhi-notes.md` records three findings from provisioning a
  board against an HTTPS backend: that a device missing from the
  `devices:` allowlist still gets `200 OK` from the OTA check with an
  empty token and is refused only at the WebSocket handshake, that the
  firmware needs no certificate work because the ESP-IDF bundle plus
  cross-signed verification covers the current Let's Encrypt chain, and
  that probing a WebSocket route with `curl` requires `--http1.1` or the
  route answers a misleading `404`. The NVS note now also lists which
  namespaces to carry across a regeneration and which regenerate
  themselves.
- Published images carry the build time (`2026-08-03-1200`, UTC) where
  they carried the build date. A date-only tag was claimed by every
  build that day, so it moved like a second `latest` while reading like
  a release marker: two merges on 2026-08-03 both took `2026-08-03`,
  and the second changed what that tag meant four hours after the
  first. `latest` is now the only tag that moves.
- `default_agent` is now required only when agents are defined and no
  device is bound to one. Omitting it is how a deployment says "only
  these devices": every unknown MAC then resolves to no agent, is issued
  no token, and is turned away, which makes the `devices` map the
  allowlist without a second list to keep in sync.
- WebSocket pings are explicit at 20 seconds, which settles the per-path
  idle timeout question the v1 plan parked: a proxy in front needs only
  a read timeout above that interval, and the two paths need no
  different treatment.

### Fixed

- A realtime-mode session no longer goes deaf after its first utterance.
  It served exactly one exchange: a realtime device sends `listen start`
  once and then streams continuously, and the server stopped listening
  after every utterance waiting for a re-arm that was never coming, so a
  board answered one question per button press. The firmware asks for
  realtime exactly when its echo cancellation is on, which makes this
  the normal case for the hardware this project targets rather than an
  edge case. A realtime session now keeps listening, including while it
  speaks, so an utterance that ends mid-reply cancels that reply and is
  answered instead: talking over the assistant stops it. The new
  `server.barge_in` (default true) turns the interrupting off for a
  board whose echo cancellation leaks the speaker back into the
  microphone, where a reply would otherwise interrupt itself;
  conversations stay multi-turn either way. The listening mode a device
  asks for is now logged at info, and an interruption logs a `barge_in`
  event.
- An interrupted reply now leaves the conversation history holding
  exactly the sentences the user heard. Sentences were counted per
  round, and a reply cut off mid-round lost all of them, so a device
  that spoke for thirteen seconds before being interrupted left no
  trace: the reply answering the interruption was written as though
  none of it had been said. They are counted one at a time now, as
  each sentence's audio goes out, which also keeps the sentence that
  was cut off partway out of the history and out of the retained
  logs.

### Security

- Device authentication is on by default, and a server started with it
  enabled and no secret in the environment refuses to boot rather than
  quietly serving every device that connects. The OTA endpoint issues
  each bound device an HMAC token (upstream's scheme, so stock firmware
  needs no change), and the websocket handshake verifies it before
  accepting the upgrade: a missing, forged, expired, or foreign token is
  refused with HTTP 403 and never reaches a socket. Opting out for a
  trial on a trusted network is one deliberate flag,
  `server.auth.enabled: false`.
- `server.ota_path` makes the endpoint's path configurable, so a public
  deployment can hide the one endpoint that cannot require a token
  behind a long random segment.
- FastAPI's `/docs`, `/redoc`, and `/openapi.json` are no longer served.
  A device needs two paths and a healthcheck a third.

## 2026-08-02

### Added

- samtal-server tools and MCP (M6): the assistant can now do things, not
  only say them. Three sources of tools merge into one list the model
  sees, kept apart by the shape of their names rather than by collision
  handling: MCP servers configured per agent under a new top-level
  `mcp_servers` section (stdio and streamable-http, referenced through
  an `mcp` list that `agent_defaults` can supply, secrets written as
  `$VAR` and resolved at boot), the device's own tools discovered over
  the conversation socket, and two builtins. `switch_agent` moves a
  conversation between the agents its device is bound to, and the new
  agent greets in its own prompt and its own voice with the history
  carried over; `remember` keeps a fact in a per-agent file that is
  injected into that agent's prompt on every reply, configured by an
  optional `memory` section. The session owns the tool loop, so
  providers stay translators and the round after a handover can go to a
  different one; a tool that fails, times out, or does not exist becomes
  an error result the model explains in its own voice rather than a
  broken reply. A server that is unreachable at startup logs a warning
  and reconnects in the background, while configuration mistakes still
  fail the boot. The official `mcp` SDK is now a core dependency.
- samtal-server agents and bindings (M5): distinct personas, enforced. A
  new top-level `agent_defaults` section holds what every agent uses
  unless it names something else, so a typical agent shrinks to a prompt
  and a voice; it deliberately takes no prompt, since a prompt is what
  makes an agent that agent. A device is bound to one agent or to a list
  of them, the first being the agent a conversation starts on and the
  rest the ones M6's spoken switching will reach, and the session now
  holds an explicit active agent whose prompt, providers, and endpointer
  swap together. Two simulated devices in one server run get two
  personas: the reply text comes from each agent's own prompt and the
  audio in each agent's own voice. The opt-in local lane runs the same
  thing on real engines, identifying the voice each device was answered
  in by re-speaking the reply in both configured voices.
- samtal-server conversation pipeline (M4), replacing the M3 echo: while
  the device listens, decoded audio feeds a Silero VAD endpointer; the
  finished utterance is transcribed (announced to the device in an `stt`
  message), the LLM streams a reply that a sentence splitter cuts into
  speakable pieces, and TTS speaks each sentence back as paced Opus frames
  at 24 kHz, the rate the server hello now announces. Conversation history
  accumulates per connection, `abort` still cancels a reply mid-stream,
  and provider failures end the reply but never the session. Every stage
  is a pluggable provider chosen per agent and built at server startup, so
  configuration mistakes fail the boot: `silero` VAD (pysilero-vad, core),
  `faster_whisper` ASR (extra), `anthropic` and `openai_compatible` LLM
  (core), `piper` TTS (extra, GPL-3.0), and deterministic keyless `mock`
  providers that let CI run the whole pipeline. Model weights and voices
  download at startup, never ship in the package.
- samtal-server opt-in local test lane (`SAMTAL_LOCAL_LANE=1 uv run
  pytest tests/local`): one real conversation through the fully local
  pipeline against a local Ollama, with a pre-flight check that fails
  naming whatever is missing. Never runs in CI; skips without the opt-in.

- samtal-server device websocket endpoint (M3) at `/xiaozhi/v1/`: accepted
  upgrade, hello exchange with a 10 second timeout, and an audio loop that
  echoes each utterance back re-encoded (a full Opus decode/encode round
  trip on PyAV), framed by `tts` messages and paced at the frame cadence.
  Utterances end on `listen stop` or through an energy endpointer standing
  in for M4's VAD; `abort` interrupts a reply in flight; binary framing
  covers protocol versions 1 to 3; devices that resolve to no agent are
  closed with policy code 1008. The integration lane now runs the
  xiaozhi-sdk simulator end to end against a live server. Verified on the
  desk: the board that got 403 since M2 now holds the hello exchange and
  echoes speech.
- samtal-server device OTA/config endpoint (M2) at `/xiaozhi/ota/`: a device
  POSTs its system info and receives the WebSocket URL, an (as yet empty)
  token, the binary protocol version to speak, and the wall clock. The
  firmware section always answers "up to date" because samtal-server serves
  no images, and no activation section is ever sent. The `Device-Id` MAC
  resolves to an agent through the config, falling back to `default_agent`.
  A `GET` on the same path reports where devices are being sent. New
  `server` keys: `websocket_url` (defaults to the address the device reached
  the OTA endpoint on), `protocol_version`, and `timezone_offset_minutes`.
- samtal-server configuration layer (M1), built on pydantic-settings:
  models for `server`, `providers`, `agents`, `devices`, and
  `default_agent`, loaded from one YAML file (`--config` or
  `SAMTAL_CONFIG`). Any key is overridable via `SAMTAL_`-prefixed
  environment variables (nested keys joined with `__`, e.g.
  `SAMTAL_SERVER__PORT`), and a `.env` file is read at startup with
  environment variables taking priority. Secrets are referenced by
  environment variable name only, and validation reports every problem
  with its location. A documented `config.example.yaml` ships with the
  server.
- `docs/README.md` as an index of the research notes, plans, and feature
  docs, linked from the root README's project layout.
- samtal-server README sections on transports (WebSocket only for v1, with
  upstream's MQTT+UDP as the additive alternative; WebRTC is not an upstream
  transport) and on ports and topology, covering the single-port choice, its
  tradeoffs, and what a reverse proxy in front of it has to get right.
- Waveshare ESP32-S3-Touch-AMOLED-2.16 (480×480 AMOLED, dual-mic AEC) listed
  as a planned target board.
- samtal-server stack decision: Python 3.12 + FastAPI (uv-managed), with the
  xiaozhi-sdk device simulator for hardware-free integration tests.
- samtal-server v1 plan (`docs/plans/2026-08-02-samtal-server-v1.md`):
  architecture, milestones M0 to M7 with device checkpoints, folder-scoped
  GitHub Actions CI, and instance-config separation.
- Workflow and documentation conventions in `AGENTS.md`: feature branches
  with rebase-only PRs for code work, dated plan files in `docs/plans/`,
  feature docs in `docs/features/`, and `gh` API tips.
- M0 skeleton for samtal-server: uv-managed Python 3.12 package with FastAPI
  app and `/healthz`, unit and integration test lanes, ruff, and the
  folder-scoped GitHub Actions workflow.

### Changed

- samtal-server `devices` values are now one agent name or a list of
  them, always stored as a list, and `Config.agent_for_device` became
  `agents_for_device`, returning the whole list. Existing single-name
  bindings keep working unchanged. `config.example.yaml` gained
  `agent_defaults`, a second voice, a second persona, and a list-valued
  binding.
- samtal-server agents must now name a provider for all four pipeline
  stages (`llm`, `asr`, `tts`, `vad`); the server refuses to start
  otherwise. `config.example.yaml`'s placeholder `sensevoice` entry became
  the real `faster_whisper` type, and its agent prompt now states the
  reply language explicitly.
- README header now shows project status badges for server CI, Python,
  FastAPI, ESP-IDF, and the MIT license.
- Hardware tables (root and samtal-esp32 READMEs) now list the e-paper
  board first, link each board name to its product page, and keep a single
  "wiki" link in the Links column.
- samtal-server now logs its own work: the CLI gives the root logger a
  handler, which uvicorn does not do, so messages from samtal-server no
  longer vanish while uvicorn's request lines appear.
- Updated logo artwork (`assets/samtal-logo.png`), same concept: the person
  and the device sharing one waveform.
- Hardware tables now link each board's product page and technical
  documentation ("doc").
- The logo is a single transparent PNG of the original artwork
  (`assets/samtal-logo.png`); the traced SVG variant is removed.

## 2026-08-01

### Changed

- New logo: a person and the device sharing one waveform, echoing the
  etymology of samtal (together + speech).
- Logo rebuilt as vector art: `assets/samtal-logo.svg` is now the source of
  truth, and `assets/samtal-logo.png` is rendered from it with a transparent
  background (fixes white edge pixels on dark pages).
- README header now shows the project logo (`assets/samtal-logo.png`).
- README rewritten in the style of clew.nvim: etymology header, early-development
  warning with 🚧 markers, feature bullets, hardware table.

### Fixed

- The vector trace of the logo had flattened the original color gradations;
  the SVG now uses real linear gradients on the orange and blue regions so it
  matches the raster original on both light and dark backgrounds.

### Added

- `AGENTS.md` with project conventions for coding agents, and `CLAUDE.md`
  referencing it.

- Project scaffold: `samtal-esp32/` (device firmware) and `samtal-server/`
  (conversation server) subprojects.
- `docs/xiaozhi-notes.md`: research notes on the upstream xiaozhi firmware and
  server, covering architecture, device↔server protocol, configuration, and the
  procedure used for the first working end-to-end demo on a Waveshare
  ESP32-S3-Touch-LCD-1.54.
- MIT license and third-party license notices for the upstream projects
  ([78/xiaozhi-esp32](https://github.com/78/xiaozhi-esp32),
  [xinnan-tech/xiaozhi-esp32-server](https://github.com/xinnan-tech/xiaozhi-esp32-server)).
