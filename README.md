<div align="center">

<img src="assets/samtal-logo.png" alt="samtal logo" width="40%">

# samtal 💬

**samtal** *(n.)* Swedish for *conversation*;<br>
from *sam-* (together) + *tal* (speech). Speech, together, with your own hardware.

[![Server CI](https://github.com/rafacm/samtal/actions/workflows/samtal-server.yml/badge.svg)](https://github.com/rafacm/samtal/actions/workflows/samtal-server.yml)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue)](https://www.python.org/)
[![FastAPI >=0.115](https://img.shields.io/badge/fastapi-%3E%3D0.115-009688)](https://fastapi.tiangolo.com/)
[![ESP-IDF v6.0.x](https://img.shields.io/badge/ESP--IDF-v6.0.x-E7352C)](https://docs.espressif.com/projects/esp-idf/en/latest/esp32s3/)
[![License: MIT](https://img.shields.io/badge/license-MIT-brightgreen)](LICENSE)

[What is it?](#what-is-samtal) • [Features](#features) • [Hardware](#hardware) • [Getting Started](#getting-started) • [Project Layout](#project-layout) • [Credits](#credits) • [Changelog](#changelog)

</div>

> [!WARNING]
> **Early development.** This README describes the intended v1. Sections marked 🚧 are not implemented yet. The foundation has been validated end-to-end: a Waveshare ESP32-S3 device talking to a self-hosted Python server with a fully local pipeline (wake word, speech recognition, LLM, speech synthesis), currently using the upstream xiaozhi firmware and server it builds on; the samtal code around it is new.

## What is samtal?

A self-hostable voice assistant that pairs **small ESP32-S3 devices** (a microphone, speaker, and display on your desk) with a **Python conversation server you run yourself**. The entire loop (wake word → speech recognition → language model → speech synthesis) happens on infrastructure you control: no vendor cloud, no account, no activation. Every stage is a **pluggable provider**: bring your own LLM (Anthropic, any OpenAI-compatible endpoint, or a local Ollama), your own voices, and your own tools via [MCP](https://modelcontextprotocol.io). Built on the excellent [xiaozhi](https://github.com/78/xiaozhi-esp32) firmware and protocol.

## Features

The design premise is a **thin device and a smart server**: the firmware's only tie to a backend is a single config URL, and everything else (endpoints, credentials, even firmware updates) is delivered by *your* server at runtime. Customization lives server-side, in Python, not in C++ you have to reflash.

- **Self-hosted end to end.** The device speaks Opus over a WebSocket to your server and nothing else. Run it on a laptop or ship it as a container image to your own infrastructure. WebSocket is the only transport for v1; upstream's MQTT+UDP alternative may follow. 🚧
- **No account, no activation, no phone app.** Point the device at your server once; it connects and talks.
- **Pluggable LLM.** Anthropic, any OpenAI-compatible endpoint, or fully local via [Ollama](https://ollama.com). 🚧
- **Pluggable voice.** Speech recognition and synthesis are swappable providers; a zero-API-key local pipeline (SileroVAD + SenseVoice + EdgeTTS) works today.
- **Tools via MCP, on both sides.** Attach any MCP server as assistant tools; the device itself exposes its controls (volume, brightness, screen) as MCP tools over the same channel.
- **Compiler-grade upstream, thin fork.** Device support, audio pipeline, and echo cancellation come from the actively maintained [xiaozhi-esp32](https://github.com/78/xiaozhi-esp32) project; samtal changes as little as possible on the device. 🚧
- **Speech in, speech out, everything visible.** Recognized text and responses render on the device display as the conversation happens.

## Hardware

Any board supported by xiaozhi-esp32 can work; these are the ones samtal targets and tests:

| Board | Display | Audio | Links | Status |
| --- | --- | --- | --- | --- |
| [Waveshare ESP32-S3-ePaper-1.54](https://www.waveshare.com/esp32-s3-epaper-1.54.htm) | 200×200 e-paper | single mic | [wiki](https://docs.waveshare.com/ESP32-S3-ePaper-1.54) | planned 🚧 |
| [Waveshare ESP32-S3-Touch-LCD-1.54](https://www.waveshare.com/esp32-s3-lcd-1.54.htm) | 240×240 LCD, touch | dual-mic with hardware echo cancellation | [wiki](https://docs.waveshare.com/ESP32-S3-Touch-LCD-1.54) | **working** (upstream firmware) |
| [Waveshare ESP32-S3-Touch-AMOLED-2.16](https://www.waveshare.com/esp32-s3-touch-amoled-2.16.htm) | 480×480 AMOLED, touch | dual-mic with hardware echo cancellation | [wiki](https://docs.waveshare.com/ESP32-S3-Touch-AMOLED-2.16) | planned 🚧 |

## Getting Started

samtal does not have its own releases yet. What works today is the validated reference setup: upstream prebuilt firmware on the device, the upstream Python server locally, and one NVS write to point the device at your server. The complete procedure, including configuration for a fully local zero-API-key pipeline, is documented in [`docs/xiaozhi-notes.md`](docs/xiaozhi-notes.md).

The short version:

1. **Flash** the prebuilt xiaozhi merged binary for your board at offset `0x0`.
2. **Point** the device at your server by writing one NVS key (`wifi/ota_url`) over USB.
3. **Run** the Python server with your chosen providers in one YAML file.
4. **Provision WiFi** from the device's captive portal, press the button, and talk.

## Project Layout

| Directory | What it is |
| --- | --- |
| [`samtal-server/`](samtal-server/) | The conversation server (Python): OTA/config endpoint, WebSocket audio channel, VAD → ASR → LLM → TTS pipeline with pluggable providers. 🚧 |
| [`samtal-esp32/`](samtal-esp32/) | Thin firmware customization: samtal server as default endpoint, English wake word, minimal UI changes. 🚧 |
| [`docs/`](docs/README.md) | Research notes on the upstream architecture and the device↔server protocol, plus the plans and implementation notes behind each milestone. |
| `vendor/` | Reference clones of the upstream projects (not committed). |

## Credits

samtal is assembled on top of two MIT-licensed projects, and would be nothing without them:

- [**78/xiaozhi-esp32**](https://github.com/78/xiaozhi-esp32) provides the ESP32 firmware: board support, audio pipeline, wake word, display UI, and the device↔server protocol.
- [**xinnan-tech/xiaozhi-esp32-server**](https://github.com/xinnan-tech/xiaozhi-esp32-server) is the Python conversation server samtal starts from.

License notices are preserved in [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).

## Changelog

Notable changes are recorded in [CHANGELOG.md](CHANGELOG.md), following the [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format with dated sections.

## License

Released under the [MIT License](LICENSE). Copyright (c) 2026 Rafael Cordones.
