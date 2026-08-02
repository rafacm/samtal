# samtal-server

The Samtal conversation server (Python), based on
[xinnan-tech/xiaozhi-esp32-server](https://github.com/xinnan-tech/xiaozhi-esp32-server).

It implements the two endpoints a device needs:

- **HTTP OTA/config endpoint** (`/xiaozhi/ota/`): the device POSTs its
  identity and receives the WebSocket URL (and optionally firmware updates).
- **WebSocket endpoint** (`/xiaozhi/v1/`): the conversation channel,
  carrying Opus audio frames up, JSON control messages both ways, and Opus
  audio back.

🚧 Behind the WebSocket will sit the pipeline: VAD → ASR → LLM (with MCP
tools) → TTS, every stage a pluggable provider. Until it lands, the server
echoes each utterance back re-encoded, which proves the handshake and the
audio loop end to end.

## Goals

- Python-only, no database required for the core loop
- Configurable providers:
  - **LLM**: Anthropic, any OpenAI-compatible endpoint, Ollama
  - **ASR**: local (SenseVoice) or cloud
  - **TTS**: pluggable engines as optional extras
  - **MCP**: attach any MCP servers as tools for the assistant
- Distributed as a multi-arch container image, deployable on your own
  infrastructure

## Stack

Python 3.12 with [FastAPI](https://fastapi.tiangolo.com), managed with
[uv](https://docs.astral.sh/uv/). Pydantic models validate the YAML
configuration; the same types back the future admin API. Integration tests
drive the server with the [xiaozhi-sdk](https://pypi.org/project/xiaozhi-sdk/)
device simulator, so CI holds real conversations without hardware. The wire
protocol is kept isolated behind a small interface, separate from the
conversation pipeline.

## Development

```bash
uv sync                             # install dependencies
uv run samtal-server                # run the server
uv run pytest tests/unit -q         # unit tests
uv run pytest tests/integration -q  # integration tests
uv run ruff check .                 # lint
```

## Configuration

Configuration is handled by
[pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/).
The server reads one YAML file, passed as `--config /path/to/config.yaml` or
via the `SAMTAL_CONFIG` environment variable; with neither set, defaults
apply. [`config.example.yaml`](config.example.yaml) documents every key:
`server` (host/port), named `providers` per stage (`llm`, `asr`, `tts`,
`vad`), `agents` combining a prompt with provider references, `devices`
binding MAC addresses to agents, and `default_agent` for unknown devices.

Every key can be overridden with a `SAMTAL_`-prefixed environment variable,
nested keys joined with `__`: `SAMTAL_SERVER__PORT=9000`,
`SAMTAL_DEFAULT_AGENT=assistant`. Environment variables beat the YAML file,
and a `.env` file in the directory the server is started from is read at
startup (real environment variables beat `.env` too). This layering matches
container deployments: the YAML arrives as a mounted file, overrides and
secrets as environment variables.

Secrets never live in the file: a provider names the environment variable
that holds its key (for example `api_key_env: ANTHROPIC_API_KEY`). Instance
configs stay out of the repository; `*.local.yaml` and `.env` are gitignored
for local experiments.

## Pointing a device at the server

A device running stock xiaozhi firmware knows only its OTA URL, held in NVS
(namespace `wifi`, key `ota_url`); see
[`../docs/xiaozhi-notes.md`](../docs/xiaozhi-notes.md) for how to write it.
Point it at `http://<server-host>:8003/xiaozhi/ota/` and everything else
reaches the device from the reply: the WebSocket URL, its token and protocol
version, and the wall clock.

By default the WebSocket URL is derived from the address the device reached
the OTA endpoint on, so a LAN deployment needs no extra configuration. Set
`server.websocket_url` when the server sits behind a proxy or a name the
request headers do not carry.

Opening `http://<server-host>:8003/xiaozhi/ota/` in a browser reports where
devices are being sent, which is the quickest way to check a deployment.

samtal-server serves no firmware images: the reply always tells the device it
is up to date, and never asks it to activate.

## Transports

**WebSocket only.** The device speaks Opus over one WebSocket, and that is
the only transport samtal-server implements or plans for v1.

Upstream supports a second one, **MQTT plus UDP**: the OTA reply carries an
`mqtt` section instead of a `websocket` one, control messages go over MQTT
and audio over a separate UDP stream. samtal-server never sends an `mqtt`
section, so devices always take the WebSocket path. Supporting it later is
additive and needs no change to what exists: the OTA endpoint would choose
which section to send per device.

**WebRTC is not an upstream transport.** The only WebRTC reference upstream
is the WebRTC/NSNet noise-suppression algorithm in the device's audio front
end (and it ships disabled). A WebRTC transport would be new work on both
sides, not adoption of something the firmware already speaks.

## Ports and topology

Both endpoints share one port (`server.port`, default 8003), because
samtal-server is a single ASGI app. Upstream splits them across two (HTTP
8003, WebSocket 8000).

The advertised WebSocket URL is deliberately independent of the listening
topology, which is what keeps this from being a one-way door: whatever the
process listens on, `server.websocket_url` decides what devices are told to
connect to.

What one port buys:

- A LAN deployment needs no configuration at all. The WebSocket URL is
  derived from the address the device reached the OTA endpoint on. With two
  ports that is impossible, since the request tells you the HTTP port and not
  the other one.
- One firewall rule, one container port, one certificate, one route.

What it costs:

- No separation at the network layer. Exposing one endpoint and not the
  other, or giving them different idle timeouts, has to be done by path
  rather than by port. This matters in practice: a WebSocket carrying a
  conversation needs a long idle timeout, an OTA check wants a short one.
- No independent scaling or lifecycle. WebSocket connections are long-lived
  and stateful while OTA requests are short and stateless, but they share a
  process, so they share a worker pool, a restart, and a crash.

### Behind a reverse proxy

One port and two paths means a proxy in front has to treat those paths
differently. Four things to get right:

- **Set `server.websocket_url` explicitly.** The derived URL is wrong behind
  a proxy that terminates TLS: uvicorn only trusts `X-Forwarded-Proto` from
  `--forwarded-allow-ips`, which defaults to `127.0.0.1` and so will not
  match the proxy's address. The reply then says `ws://` where it should say
  `wss://`, and devices fail to connect with nothing obviously
  misconfigured.
- **Give the two paths different idle timeouts.** An OTA check is a
  sub-second request. A conversation WebSocket goes quiet whenever nobody is
  speaking, so a proxy timeout tuned for short HTTP requests (60 seconds is
  a common default) cuts the conversation off mid-pause. Where the timeout
  can only be set once for the whole service, the long value has to win: a
  generous timeout on the OTA path costs little, a short one on the
  WebSocket path ends conversations.
- **Allow the upgrade and turn off response buffering** on the WebSocket
  path. A proxy that buffers, or that does not pass `Upgrade` and
  `Connection` through, either breaks the handshake or adds latency to every
  spoken reply.
- **Restarts end conversations.** Every open WebSocket dies with the
  process, and the OTA endpoint shares that process, so neither can be
  restarted without the other. Allow a drain period long enough for
  conversations in flight to finish.

Separating the two later needs no separate ports and no code change: run the
same image twice, route `/xiaozhi/ota/` to one group and `/xiaozhi/v1/` to
the other, and point `server.websocket_url` at the second. Devices follow,
because they are told where to go.

## Status

Implementation in progress; the v1 plan lives at
[`docs/plans/2026-08-02-samtal-server-v1.md`](../docs/plans/2026-08-02-samtal-server-v1.md).
The upstream server currently runs as our reference implementation. Setup notes for the working local demo are in
[`../docs/xiaozhi-notes.md`](../docs/xiaozhi-notes.md).
