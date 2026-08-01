# Agent guidance for samtal

samtal is a self-hostable voice assistant: ESP32-S3 devices (mic, speaker,
display) talk to a Python conversation server over WebSocket. It builds on
78/xiaozhi-esp32 (device firmware) and xinnan-tech/xiaozhi-esp32-server
(server), both MIT.

## Repository layout

- `samtal-server/`: the conversation server (Python). OTA/config HTTP endpoint,
  WebSocket audio channel, VAD → ASR → LLM → TTS pipeline with pluggable
  providers (LLM, voice, MCP tools).
- `samtal-esp32/`: thin firmware customization on top of upstream
  xiaozhi-esp32 (ESP-IDF v6.0.x, target `esp32s3`).
- `docs/xiaozhi-notes.md`: research notes on the upstream architecture, the
  device↔server protocol, ports, configuration keys, and the validated
  end-to-end demo procedure. Read this first for anything protocol-related.
- `vendor/`: reference clones of the upstream repos. Not committed; recreate
  with the clone commands at the top of `docs/xiaozhi-notes.md`.

## Writing conventions

- Never use em-dashes anywhere: docs, commit messages, code comments.
  Rephrase with commas, colons, semicolons, parentheses, or sentence breaks.
- `CHANGELOG.md` follows Keep a Changelog 1.1.0, but with dates
  (`## YYYY-MM-DD`) as section headers instead of version numbers. Group
  entries under `### Added`, `### Changed`, `### Deprecated`, `### Removed`,
  `### Fixed`, `### Security`. Update it with every notable change.
- Describe deployment generically (a container image, your own
  infrastructure). Do not name specific hosting providers or platforms in
  documentation.
- README style follows clew.nvim conventions: centered header with logo and
  etymology, early-development warning, 🚧 marks for unimplemented features,
  honest status reporting.

## Licensing rules

- The project is MIT. When copying or deriving from the upstream repos, keep
  their license notices intact (see `THIRD_PARTY_LICENSES.md`).
- Keep TTS engines as optional pluggable providers; the `edge-tts` Python
  package is GPL-3.0 and must not become a hard dependency of the core
  server.
- Model weights (SenseVoice, Silero, ESP-SR wake words) are downloaded at
  deploy time, never committed or redistributed.

## Hardware context

Primary test device: Waveshare ESP32-S3-Touch-LCD-1.54 (ESP32-S3, 16 MB
flash, 8 MB PSRAM). Flashing uses esptool with merged binaries at offset
`0x0`; the device's backend URL lives in NVS (namespace `wifi`, key
`ota_url`, partition at `0x9000`). Details and gotchas (including the
reply-language configuration trap) are in `docs/xiaozhi-notes.md`.
