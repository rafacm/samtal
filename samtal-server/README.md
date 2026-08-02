# samtal-server

The Samtal conversation server (Python), based on
[xinnan-tech/xiaozhi-esp32-server](https://github.com/xinnan-tech/xiaozhi-esp32-server).

It implements the two endpoints a device needs:

- **HTTP OTA/config endpoint** (`/xiaozhi/ota/`): the device POSTs its
  identity and receives the WebSocket URL (and optionally firmware updates).
- **WebSocket endpoint** (`/xiaozhi/v1/`): the conversation channel,
  carrying Opus audio frames up, JSON control messages both ways, and Opus
  audio back.

Behind the WebSocket sits the conversation pipeline: VAD segments speech,
ASR transcribes it, the LLM streams a reply, and TTS speaks it back
sentence by sentence, every stage a pluggable provider chosen per agent.
🚧 MCP tools land in a later milestone.

## Goals

- Python-only, no database required for the core loop
- Configurable providers:
  - **LLM**: Anthropic, any OpenAI-compatible endpoint (Ollama, LM Studio,
    gateways)
  - **ASR**: local (faster-whisper) or cloud
  - **TTS**: pluggable engines as optional extras (Piper)
  - **MCP**: attach any MCP servers as tools for the assistant, alongside
    the device's own
- Distributed as a multi-arch container image, deployable on your own
  infrastructure

## Providers

Each pipeline stage is a named provider entry in the configuration, and
each agent picks one provider per stage. The v1 set:

| Stage | Type                | Runs      | Install                          |
| ----- | ------------------- | --------- | -------------------------------- |
| vad   | `silero`            | locally   | core (pysilero-vad)              |
| asr   | `faster_whisper`    | locally   | `uv sync --extra faster-whisper` |
| llm   | `anthropic`         | Anthropic | core                             |
| llm   | `openai_compatible` | anywhere  | core                             |
| tts   | `piper`             | locally   | `uv sync --extra piper`          |
| any   | `mock`              | in tests  | core (deterministic, keyless)    |

Model weights are never shipped: faster-whisper models and Piper voices
download at server startup into a local cache (`download_dir` on the
provider entry). A fully local, keyless pipeline is Silero +
faster-whisper + Ollama (through `openai_compatible`) + Piper.

Licensing note: `piper-tts` (piper1-gpl) is GPL-3.0, which is why it is an
optional extra and never a core dependency of the MIT server. The same
applies to any future `edge-tts` provider.

## Tools

Beyond speaking, an agent reaches three kinds of tool, merged into one
list the model sees and told apart by the shape of their names.

**MCP servers** are named entries under `mcp_servers`, the way providers
are, and an agent references them through an `mcp` list that
`agent_defaults` can supply. Naming a list replaces the inherited one
rather than extending it, so `mcp: []` is how an agent opts out of tools
its siblings have. Each server's tools are offered under its entry name
(`home__turn_on_light`), which is why an entry name has to be a plain
`[A-Za-z0-9_-]+` name and cannot be `self`, `switch_agent`, or
`remember`. Both transports the specification defines are supported:

```yaml
mcp_servers:
  home:
    transport: stdio
    command: mcp-proxy
    args: ["http://homeassistant.local:8123/mcp_server/sse"]
    env:
      API_ACCESS_TOKEN: $HOME_ASSISTANT_TOKEN
  weather:
    transport: streamable_http
    url: http://localhost:8000/mcp
    headers:
      Authorization: $WEATHER_TOKEN
    tool_timeout_s: 15
```

Secrets follow the same rule as everywhere else: a value of `$NAME` is
read from that environment variable at startup, and any secret-looking
key (`token`, `api_key`, `authorization`, ...) must use that form. An
unset variable fails the boot, as does an unknown reference or a
reserved entry name. A server that is merely unreachable does not: it
logs a warning, contributes no tools, and reconnects in the background
when a session that would use it opens.

**The device's own tools** need no configuration. A board whose hello
advertises `features.mcp` is asked for its tools over the same socket
the audio runs on, and they arrive under their firmware names with the
dots replaced (`self_audio_speaker_set_volume`), because both LLM APIs
restrict tool names to `[A-Za-z0-9_-]`.

**Builtins** are `switch_agent`, offered when the device is bound to
more than one agent, and `remember`, offered when memory is configured.
A successful `switch_agent` ends the current agent's reply: the new
agent greets the user in its own prompt and its own voice, with the
conversation so far carried over. `remember` appends one fact to the
agent's memory file:

```yaml
memory:
  dir: /var/lib/samtal/memory
```

One file per agent, created on first write and injected into that
agent's system prompt on every reply, capped at 8 KiB or 200 lines with
the oldest dropped first. Memory is keyed by agent and not by device: a
persona is one entity across rooms. Leave the section out and there is
no `remember` tool and no injection.

A tool that fails, times out, or does not exist comes back to the model
as an error result rather than ending the reply, so the assistant says
what went wrong in its own voice and the user's language. The device
hears silence while a tool runs, bounded by `tool_timeout_s` (15 seconds
by default).

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
uv sync --extra faster-whisper --extra piper  # add the local ASR/TTS engines
uv run samtal-server                # run the server
uv run pytest tests/unit -q         # unit tests
uv run pytest tests/integration -q  # integration tests
uv run ruff check .                 # lint
```

The test lanes run the whole pipeline on the built-in mock providers, so
they need no keys, no model downloads, and no network.

### The local lane: a real conversation

CI never touches real engines. To check the overall work with them, an
opt-in third lane holds one real conversation end to end: it starts a
real server on the fully local pipeline (Silero, faster-whisper, Ollama,
Piper), speaks a Piper-synthesized question through the device simulator,
and asserts the transcript and a coherent spoken reply.

```bash
uv sync --extra faster-whisper --extra piper
SAMTAL_LOCAL_LANE=1 uv run pytest tests/local -q
```

The run ends with a summary of the conversation it held, so a pass shows
its work rather than a green dot:

```
=========================== local lane conversation ============================
pipeline: silero + faster-whisper small + qwen3:8b + en_US-lessac-medium
question: "What is the capital of Sweden?" (1.6 s of audio)
heard   : "What is the capital of Sweden?" (+1.2 s)
reply   : "The capital of Sweden is Stockholm." (first sentence +3.8 s, 2.0 s of audio)
```

A pre-flight check runs first and fails with the command that fixes
whatever is missing (extras not installed, no Ollama answering, no usable
model). By default it talks to Ollama at `localhost:11434` and prefers
`qwen3:8b`, falling back to the first installed model;
`SAMTAL_LOCAL_OLLAMA` and `SAMTAL_LOCAL_LLM_MODEL` override both. The
first run downloads the whisper model and Piper voice at server startup
and can take a few minutes; later runs finish in seconds. Without
`SAMTAL_LOCAL_LANE=1` the lane skips, so a bare `pytest` stays safe.

## Configuration

Configuration is handled by
[pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/).
The server reads one YAML file, passed as `--config /path/to/config.yaml` or
via the `SAMTAL_CONFIG` environment variable; with neither set, defaults
apply. [`config.example.yaml`](config.example.yaml) documents every key:
`server` (host/port), named `providers` per stage (`llm`, `asr`, `tts`,
`vad`), named `mcp_servers`, `agent_defaults` holding what every agent
uses unless it says otherwise, `agents` combining a prompt with provider
and MCP references, `devices` binding MAC addresses to agents,
`default_agent` for unknown devices, and an optional `memory` section.

Since a voice is a `tts` provider entry, two agents that should sound
different reference two entries, and a typical agent is a prompt plus a
voice. `agent_defaults` takes no prompt: a prompt is what makes an agent
that agent. A device is bound to one agent or to a list of them; with a
list, the first entry is the agent a conversation starts on, and the
rest are the ones `switch_agent` can reach.

Every key can be overridden with a `SAMTAL_`-prefixed environment variable,
nested keys joined with `__`: `SAMTAL_SERVER__PORT=9000`,
`SAMTAL_DEFAULT_AGENT=assistant`. Environment variables beat the YAML file,
and a `.env` file in the directory the server is started from is read at
startup (real environment variables beat `.env` too). This layering matches
container deployments: the YAML arrives as a mounted file, overrides and
secrets as environment variables.

Secrets never live in the file: a provider names the environment variable
that holds its key (for example `api_key_env: ANTHROPIC_API_KEY`), and an
MCP server writes `$NAME` where the secret goes. Instance
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
