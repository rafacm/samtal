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
Agents reach tools over MCP, both their own servers and the device's own
controls.

Those two paths, plus `/healthz`, are everything the server exposes: the
interactive API docs are turned off, and the WebSocket requires a device
token the OTA endpoint issued.

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

### The smoke lane: a conversation with a container

A fourth lane runs nothing itself. It points at a server that is already
up and holds one whole conversation with it: healthz, an OTA check whose
token it verifies, and a full utterance-to-audio exchange through the
device simulator. CI runs it against the image it just built, which is
what turns "`docker run` with one mounted YAML serves a conversation"
into something checked rather than remembered.

```bash
docker build -t samtal-server:local .
docker run -d --name samtal-smoke -p 8003:8003 \
  -e SAMTAL_AUTH_SECRET=smoke-secret \
  -v "$PWD/tests/smoke/config.yaml:/config/config.yaml:ro" \
  samtal-server:local

SAMTAL_SMOKE_OTA_URL=http://127.0.0.1:8003/xiaozhi/ota/ \
SAMTAL_AUTH_SECRET=smoke-secret \
  uv run pytest tests/smoke -v
```

The secret has to match the one the server under test was started with:
the lane verifies the token it is issued, and that needs the signing key.
It skips without `SAMTAL_SMOKE_OTA_URL`, so a bare `pytest` stays safe,
and it works against any reachable server, not only a container.

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

## Security

**Devices authenticate, by default.** The OTA endpoint issues each device
a token, the firmware persists it to NVS, and the WebSocket handshake
checks it before accepting the upgrade. A connection with no token, a
forged one, an expired one, or one issued for a different device is
refused with HTTP 403 on the upgrade, which stock firmware handles by
retrying and picking up a fresh token at its next OTA check.

The token is upstream's scheme, `sig.ts`, where `sig` is HMAC-SHA256 over
`client_id|device_id|ts`. It is stateless: a restart does not lock out
every device holding a persisted token, and two replicas sharing a secret
accept each other's tokens.

The secret comes from the environment, never from the config file:

```bash
SAMTAL_AUTH_SECRET=$(openssl rand -hex 32)
```

**A missing secret fails the boot.** Authentication is enabled by default,
and starting with it enabled and no secret refuses to come up, naming the
variable and the fix. A deployment that forgot its secret must not look
exactly like a working one. For a trial on a network you trust, opting
out is one deliberate flag:

```yaml
server:
  auth:
    enabled: false
```

or `SAMTAL_SERVER__AUTH__ENABLED=false` in the environment.

**Who gets a token is the allowlist.** A token is only issued to a device
the configuration resolves to at least one agent. Omit `default_agent`
and the `devices` map becomes an allowlist: an unknown MAC is issued
nothing and turned away. There is no second list to keep in sync.

**The OTA endpoint is the token issuer, so it cannot require a token.**
What protects it instead is stingy issuance and a path you choose. When
the server is reachable from outside your network, hide it behind a long
random segment and write that whole URL into the device's NVS:

```yaml
server:
  ota_path: /xiaozhi/ota/8f3a9c2b1d4e5f60/   # openssl rand -hex 8
```

The WebSocket path never moves: the token is what protects it.

**Nothing else is exposed.** `/xiaozhi/ota/` (or wherever you put it),
`/xiaozhi/v1/`, and `/healthz`. FastAPI's `/docs`, `/redoc`, and
`/openapi.json` are turned off.

## Limits

Two numbers bound what one server holds, and neither is visible in normal
use: a device refused a slot or closed by the duration cap reconnects on
its next wake word.

```yaml
server:
  limits:
    max_sessions: 8       # concurrent conversations
    max_session_s: 3600   # one session's maximum life
  drain_s: 20             # how long a shutdown waits for replies to finish
```

There is deliberately no idle timeout: `max_session_s` bounds an idle
session too, and a device that stopped talking hours ago is exactly what
it is for. `max_sessions` is a count with no queue behind it, because a
conversation waiting in line is worse than one that never started.

**Shutting down drains.** On SIGTERM the server stops admitting sessions,
lets every reply in flight finish speaking, and closes those sockets with
1001, all inside `drain_s`. A second signal forces the exit. Give
`docker stop` a `-t` above `drain_s`; its default is ten seconds.

`drain_s` is the whole budget a reply gets, so raise it if your replies
are long: a spoken answer is paced at the frame rate, so thirty seconds
of speech takes thirty seconds to deliver. When a reply outlasts the
budget its socket is still closed politely, but the drain logs
`drain_incomplete` with `cut_mid_reply`, which is the signal that
`drain_s` is too short for the replies this server gives.

## Logging

Two formats, one handler:

```yaml
server:
  log_format: text   # or json, which is the container image's default
  log_level: INFO
```

Every conversation event is logged as a human sentence and, in `json`
mode, as a line of structured fields. Each carries `event`, `session`,
and `device`, plus its own:

| `event`            | when                            | fields                             |
| ------------------ | ------------------------------- | ---------------------------------- |
| `ota_check`        | a device checks in (no session) | `client`, `board`, `firmware`, `agents` |
| `session_open`     | a conversation starts           | `client`, `agent`, `agents`, `protocol` |
| `heard`            | an utterance is transcribed     | `agent`, `text`, `duration_s`      |
| `replied`          | a reply finishes                | `agent`, `text`                    |
| `agent_said`       | one agent's part of a reply     | `agent`, `text`                    |
| `handover`         | `switch_agent` succeeds         | `from_agent`, `to_agent`           |
| `tool_call`        | a tool returns                  | `agent`, `tool`, `duration_ms`, `is_error` |
| `session_limit`    | the duration cap fires          | `duration_s`                       |
| `session_closed`   | a conversation ends             | `duration_s`                       |
| `session_rejected` | a device is turned away         | `reason`                           |
| `auth_rejected`    | a handshake is refused          | `reason`                           |
| `drain_started`    | a shutdown begins draining      | `sessions`, `timeout_s`            |
| `drain_finished`   | every reply finished speaking   | `sessions`                         |
| `drain_incomplete` | a reply was cut, or a session hung | `cut_mid_reply`, `unfinished`   |

Retained JSON logs are the conversation store until v3 brings a real one:
filter on `event` in `heard`, `replied`, `agent_said` and group by
`session`, and you have the transcript. Tokens are never logged, at any
level.

## Running in a container

The image carries both local engines, so one `docker run` with one
mounted YAML serves a conversation:

```bash
docker run -d --name samtal \
  -p 8003:8003 \
  -e SAMTAL_AUTH_SECRET \
  -v /path/to/config.yaml:/config/config.yaml:ro \
  -v samtal-data:/data \
  ghcr.io/rafacm/samtal-server:latest
```

- `/config/config.yaml` is where `SAMTAL_CONFIG` points. Mount it
  read-only; override any key with a `SAMTAL_`-prefixed environment
  variable.
- `/data` is the volume every engine caches into (`HOME` points there):
  whisper models and Piper voices download at first start and survive a
  new image. Model weights are never baked in.
- Logs default to `json` in the image, which is the only default that
  differs from running it directly. Override with
  `SAMTAL_SERVER__LOG_FORMAT=text`.
- The healthcheck assumes the default port; change `server.port` and
  override `--health-cmd` too.
- A read-only root filesystem works: add `--read-only --tmpfs /tmp` and
  keep the two mounts.
- Stop it with `docker stop -t 30 samtal`, above `drain_s`, so
  conversations in flight finish their sentence.

Behind a TLS-terminating proxy, either set `server.websocket_url`
explicitly or pass the proxy's address in `FORWARDED_ALLOW_IPS`, which
uvicorn honours from the environment.

Images are published to `ghcr.io/rafacm/samtal-server` for amd64 and
arm64, tagged `latest`, the build date, and the short commit SHA. Each
one has passed the unit, integration, and smoke lanes.

The image contains `piper-tts` (GPL-3.0) alongside the MIT server. That
is aggregation, not a derived work; see
[`../THIRD_PARTY_LICENSES.md`](../THIRD_PARTY_LICENSES.md).

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

- **Set `server.websocket_url` explicitly, or trust the proxy.** The
  derived URL is wrong behind a proxy that terminates TLS: uvicorn only
  trusts `X-Forwarded-Proto` from `forwarded_allow_ips`, which defaults to
  `127.0.0.1` and so will not match the proxy's address. The reply then
  says `ws://` where it should say `wss://`, and devices fail to connect
  with nothing obviously misconfigured. Either name the URL yourself, or
  put the proxy's address in the `FORWARDED_ALLOW_IPS` environment
  variable, which uvicorn reads when the setting is not passed. There is
  no config key for it: `server.websocket_url` is the explicit answer, and
  the environment variable covers the rest without a second way to say the
  same thing.
- **One idle timeout is enough, above 20 seconds.** The server pings every
  connected device every 20 seconds, so a conversation WebSocket is never
  actually idle even when nobody is speaking. A proxy therefore needs only
  a read timeout above that interval, and the two paths need no different
  treatment.
- **Allow the upgrade and turn off response buffering** on the WebSocket
  path. A proxy that buffers, or that does not pass `Upgrade` and
  `Connection` through, either breaks the handshake or adds latency to every
  spoken reply.
- **Restarts end conversations.** Every open WebSocket dies with the
  process, and the OTA endpoint shares that process, so neither can be
  restarted without the other. The server drains on SIGTERM (see
  [Limits](#limits)); give whatever stops it a grace period above
  `drain_s`.

Separating the two later needs no separate ports and no code change: run the
same image twice, route `/xiaozhi/ota/` to one group and `/xiaozhi/v1/` to
the other, and point `server.websocket_url` at the second. Devices follow,
because they are told where to go.

## Status

samtal-server serves conversations end to end: OTA and WebSocket
endpoints, the VAD/ASR/LLM/TTS pipeline on pluggable providers, agents
bound to devices, MCP tools on both sides, device authentication, limits,
structured logging, and a published multi-arch container image. The v1
plan and its per-milestone implementation notes live in
[`docs/plans/`](../docs/plans/); setup notes for a device on your desk are
in [`../docs/xiaozhi-notes.md`](../docs/xiaozhi-notes.md).
