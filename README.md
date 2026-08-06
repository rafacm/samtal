<div align="center">

<img src="assets/samtal-logo.png" alt="samtal logo" width="40%">

# samtal 💬

**samtal** *(n.)* Swedish for *conversation*;<br>
from *sam-* (together) + *tal* (speech). Speech, together, with your own hardware.

Conversational AI. [Sweded](https://youtu.be/i5Rd8x4OJoY).

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

> You take what you like and mix it with some other things you like and make a new thing. *Your* thing!<br>
> -- from the movie [Be Kind Rewind (2008)](https://youtu.be/i5Rd8x4OJoY)

We took two projects we liked, the [78/xiaozhi-esp32](https://github.com/78/xiaozhi-esp32) firmware and the [xinnan-tech/xiaozhi-esp32-server](https://github.com/xinnan-tech/xiaozhi-esp32-server) conversation server, and some devices we also liked, the small [Waveshare ESP32-S3 boards](#hardware) that put a microphone, a speaker, and a display on your desk. We reimplemented what we wanted to make our own (the **conversation server**, rebuilt in Python you run yourself), kept what was already excellent (the firmware's board support, audio pipeline, and device protocol), and mixed in the things *you* like.

The new thing is a self-hostable voice assistant where the device talks to **your server** and nothing else. Every stage is a **pluggable provider**: bring your own LLM (a local [Ollama](https://ollama.com), Anthropic, or any OpenAI-compatible endpoint), your own voices, and your own tools via [MCP](https://modelcontextprotocol.io). The whole loop (wake word → speech recognition → language model → speech synthesis) can run entirely on your own hardware, and wherever you choose a cloud provider instead, it is exactly that, your choice: your keys, through your server, swappable at will. No vendor cloud, no account, no activation. Your thing.

## Features

The design premise is a **thin device and a smart server**: the firmware's only tie to a backend is a single config URL, and everything else (endpoints, credentials, even firmware updates) is delivered by *your* server at runtime. Customization lives server-side, in Python, not in C++ you have to reflash.

- **Self-hosted end to end.** The device speaks Opus over a WebSocket to your server and nothing else. Run it on a laptop or ship the published multi-arch container image to your own infrastructure. WebSocket is the only transport for v1; upstream's MQTT+UDP alternative may follow.
- **No account, no activation, no phone app.** Point the device at your server once; it connects and talks.
- **Pluggable LLM.** Fully local via [Ollama](https://ollama.com), or Anthropic and any OpenAI-compatible endpoint. 🚧
- **Pluggable voice.** Speech recognition and synthesis are swappable providers; a zero-API-key local pipeline (Silero VAD + faster-whisper + Piper) works today, and the cloud is there for either half when you would rather have the better voice, or the ear that copes with a noisy room in your own language, than the private one.
- **Tools via MCP, on both sides.** Attach any MCP server as assistant tools; the device itself exposes its controls (volume, brightness, screen) as MCP tools over the same channel.
- **Compiler-grade upstream, thin fork.** Device support, audio pipeline, and echo cancellation come from the actively maintained [xiaozhi-esp32](https://github.com/78/xiaozhi-esp32) project; samtal changes as little as possible on the device. 🚧
- **Speech in, speech out, everything visible.** Recognized text and responses render on the device display as the conversation happens.

## Hardware

Any board supported by xiaozhi-esp32 can work; these are the ones samtal targets and tests:

| Board | Display | Audio | Links | Status |
| --- | --- | --- | --- | --- |
| [Waveshare ESP32-S3-ePaper-1.54](https://www.waveshare.com/esp32-s3-epaper-1.54.htm) | 200×200 e-paper | single mic | [wiki](https://docs.waveshare.com/ESP32-S3-ePaper-1.54) | planned 🚧 |
| [Waveshare ESP32-S3-Touch-LCD-1.54](https://www.waveshare.com/esp32-s3-lcd-1.54.htm) | 240×240 LCD, touch | dual-mic with hardware echo cancellation | [wiki](https://docs.waveshare.com/ESP32-S3-Touch-LCD-1.54) | [**working** (upstream firmware)](samtal-esp32/README.md#using-the-device) |
| [Waveshare ESP32-S3-Touch-AMOLED-2.16](https://www.waveshare.com/esp32-s3-touch-amoled-2.16.htm) | 480×480 AMOLED, touch | dual-mic with hardware echo cancellation | [wiki](https://docs.waveshare.com/ESP32-S3-Touch-AMOLED-2.16) | planned 🚧 |

## Getting Started

The device runs upstream's prebuilt xiaozhi firmware; the server is samtal's, and ships as a container image.

**1. Run the server.** For a trial on a network you trust, authentication off and everything in one file:

```bash
docker run -d --name samtal -p 8003:8003 \
  -e SAMTAL_SERVER__AUTH__ENABLED=false \
  -v /path/to/config.yaml:/config/config.yaml:ro \
  -v samtal-data:/data \
  ghcr.io/rafacm/samtal-server:latest
```

For anything that outlives an afternoon, leave authentication on (it is the default) and give it a secret. Generate the secret **once** and keep it somewhere you can read it back:

```bash
openssl rand -hex 32 > ~/.samtal-secret          # once, ever

export SAMTAL_AUTH_SECRET=$(cat ~/.samtal-secret)
docker run -d --name samtal -p 8003:8003 \
  -e SAMTAL_AUTH_SECRET \
  -v /path/to/config.yaml:/config/config.yaml:ro \
  -v samtal-data:/data \
  ghcr.io/rafacm/samtal-server:latest
```

Generating it inline in the `docker run` would mint a new secret on every restart, and each new secret invalidates the token every device has stored. A device that has one then gets refused until its next OTA check, which it only makes on boot, so it sits there playing an error tone at you.

Start from [`samtal-server/config.example.yaml`](samtal-server/config.example.yaml), which documents every key, or from [`samtal-server/config.deploy.example.yaml`](samtal-server/config.deploy.example.yaml), a ready-to-adapt profile for the container image behind a proxy on a small CPU quota. Speech models download into the `/data` volume at first start, so the first run takes a few minutes and later ones take seconds.

**2. Flash** the prebuilt xiaozhi merged binary for your board at offset `0x0`.

**3. Point** the device at your server by writing one NVS key (`wifi/ota_url` = `http://<server-host>:8003/xiaozhi/ota/`) over USB.

**4. Provision WiFi** from the device's captive portal, press the button, and talk.

The complete procedure, including a fully local zero-API-key pipeline and every serial gotcha, is in [`docs/xiaozhi-notes.md`](docs/xiaozhi-notes.md); the server's own options, security defaults, and container details are in [`samtal-server/README.md`](samtal-server/README.md). samtal has no versioned releases yet: images are tagged `latest`, the build time (`2026-08-03-1200`, UTC), and the commit SHA (`sha-3f9362a`). Only `latest` moves; deploy from one of the other two.

## Project Layout

| Directory | What it is |
| --- | --- |
| [`samtal-server/`](samtal-server/) | The conversation server (Python): OTA/config endpoint, WebSocket audio channel, VAD → ASR → LLM → TTS pipeline with pluggable providers, MCP tools, device authentication. Published as a multi-arch container image. |
| [`samtal-esp32/`](samtal-esp32/) | Thin firmware customization: samtal server as default endpoint, English wake word, minimal UI changes. 🚧 |
| [`docs/`](docs/README.md) | Research notes on the upstream architecture and the device↔server protocol, plus the plans and implementation notes behind each milestone. |
| `vendor/` | Reference clones of the upstream projects (not committed). |

## Credits

samtal is assembled on top of two MIT-licensed projects, and would be nothing without them:

- [**78/xiaozhi-esp32**](https://github.com/78/xiaozhi-esp32) provides the ESP32 firmware: board support, audio pipeline, wake word, display UI, and the device↔server protocol.
- [**xinnan-tech/xiaozhi-esp32-server**](https://github.com/xinnan-tech/xiaozhi-esp32-server) is the Python conversation server samtal starts from.

License notices are preserved in [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).

The word *sweded*, and the whole idea of remaking something you love with your own hands, comes from the film *Be Kind Rewind* (2008); its creators explain [How To Swede](https://youtu.be/i5Rd8x4OJoY) on YouTube.

## Changelog

Notable changes are recorded in [CHANGELOG.md](CHANGELOG.md), following the [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format with dated sections.

## License

Released under the [MIT License](LICENSE). Copyright (c) 2026 Rafael Cordones.
