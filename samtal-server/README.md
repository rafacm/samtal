# samtal-server

The Samtal conversation server (Python), based on
[xinnan-tech/xiaozhi-esp32-server](https://github.com/xinnan-tech/xiaozhi-esp32-server).

It implements the two endpoints a device needs:

- **HTTP OTA/config endpoint**: the device POSTs its identity and receives
  the WebSocket URL (and optionally firmware updates).
- **WebSocket endpoint**: the conversation channel, carrying Opus audio frames
  up, JSON control messages both ways, and Opus audio back.

Behind the WebSocket sits the pipeline: VAD → ASR → LLM (with MCP tools) →
TTS. Every stage is a pluggable provider.

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

## Status

Implementation in progress; the v1 plan lives at
[`docs/plans/2026-08-02-samtal-server-v1.md`](../docs/plans/2026-08-02-samtal-server-v1.md).
The upstream server currently runs as our reference implementation. Setup notes for the working local demo are in
[`../docs/xiaozhi-notes.md`](../docs/xiaozhi-notes.md).
