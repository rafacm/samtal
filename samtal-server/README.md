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

## Status

Stack decided, implementation starting; the v1 plan lives at
[`docs/plans/2026-08-02-samtal-server-v1.md`](../docs/plans/2026-08-02-samtal-server-v1.md).
The upstream server currently runs as our reference implementation. Setup notes for the working local demo are in
[`../docs/xiaozhi-notes.md`](../docs/xiaozhi-notes.md).
