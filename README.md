# Samtal

**Samtal** (Swedish for *conversation*) is an open-source, self-hostable voice
assistant. It pairs small ESP32-S3 devices — a microphone, speaker, and display
on your desk — with a Python server you run on your own infrastructure, so the
entire conversation loop (wake word, speech recognition, language model, speech
synthesis) happens under your control, with pluggable LLM, voice, and MCP tool
providers.

## Architecture

```
┌──────────────┐  Opus audio + JSON over WebSocket  ┌───────────────┐
│ ESP32-S3     │ ◄────────────────────────────────► │ samtal-server │
│ device       │                                    │ (Python)      │
│ mic·spk·LCD  │  HTTP OTA/config endpoint          │ VAD·ASR·LLM·TTS│
└──────────────┘                                    └───────────────┘
```

- **[`samtal-esp32/`](samtal-esp32/)** — device firmware (ESP-IDF). Initially a
  thin customization of the upstream xiaozhi firmware: our server as the
  default endpoint, English wake word, minimal UI changes.
- **[`samtal-server/`](samtal-server/)** — the conversation server (Python).
  Implements the device protocol (WebSocket + OTA endpoint) with configurable
  providers: any OpenAI-compatible or Anthropic LLM, local or cloud
  ASR/TTS, and MCP servers for tools. Ships as a container image.
- **`vendor/`** (not committed) — reference clones of the upstream projects we
  build on.
- **[`docs/xiaozhi-notes.md`](docs/xiaozhi-notes.md)** — our research notes on
  the upstream architecture, protocol, and configuration.

## Status

Early days. A full end-to-end demo works (2026-08-01): a Waveshare
ESP32-S3-Touch-LCD-1.54 running upstream firmware, talking to the upstream
Python server on a laptop with an entirely local pipeline (SileroVAD, SenseVoice
ASR, Ollama LLM, EdgeTTS). See the notes in `docs/` for how it was wired up.

Supported hardware to start:

- Waveshare ESP32-S3-Touch-LCD-1.54 (240×240 LCD, touch, dual-mic AEC)
- Waveshare ESP32-S3-ePaper-1.54 (200×200 e-paper) — planned

## Credits

Samtal builds on two excellent MIT-licensed projects:

- [78/xiaozhi-esp32](https://github.com/78/xiaozhi-esp32) — the ESP32 voice
  assistant firmware and device↔server protocol.
- [xinnan-tech/xiaozhi-esp32-server](https://github.com/xinnan-tech/xiaozhi-esp32-server)
  — the Python conversation server we started from.

Thank you to their authors and contributors. See
[THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md) for license notices.

## License

[MIT](LICENSE)
