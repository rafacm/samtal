# samtal-server

The Samtal conversation server (Python), based on
[xinnan-tech/xiaozhi-esp32-server](https://github.com/xinnan-tech/xiaozhi-esp32-server).

It implements the two endpoints a device needs:

- **HTTP OTA/config endpoint** — the device POSTs its identity and receives
  the WebSocket URL (and optionally firmware updates).
- **WebSocket endpoint** — the conversation channel: Opus audio frames up,
  JSON control messages both ways, Opus audio back.

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

## Status

Not started — the upstream server currently runs as our reference
implementation. Setup notes for the working local demo are in
[`../docs/xiaozhi-notes.md`](../docs/xiaozhi-notes.md).
