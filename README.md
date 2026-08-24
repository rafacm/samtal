<div align="center">

<img src="assets/vinga-logo.png" alt="vinga logo" width="40%">

# vinga 💬

**vinga** *(interj.)* Catalan for "*come on, let's go*".<br>
Come on, speak, on your own terms.

Conversational AI. [Sweded](https://youtu.be/i5Rd8x4OJoY).

[![Server CI](https://github.com/rafacm/vinga/actions/workflows/vinga-server.yml/badge.svg)](https://github.com/rafacm/vinga/actions/workflows/vinga-server.yml)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue)](https://www.python.org/)
[![FastAPI >=0.115](https://img.shields.io/badge/fastapi-%3E%3D0.115-009688)](https://fastapi.tiangolo.com/)
[![ESP-IDF v6.0.x](https://img.shields.io/badge/ESP--IDF-v6.0.x-E7352C)](https://docs.espressif.com/projects/esp-idf/en/latest/esp32s3/)
[![License: MIT](https://img.shields.io/badge/license-MIT-brightgreen)](LICENSE)

[What is it?](#what-is-vinga) • [Features](#features) • [Hardware](#hardware) • [Getting Started](#getting-started) • [Project Layout](#project-layout) • [Credits](#credits) • [Changelog](#changelog)

</div>

> [!WARNING]
> **Early development.** This README describes the intended v1. Sections marked 🚧 are not implemented yet. The loop works end to end today: a Waveshare ESP32-S3 running stock upstream firmware, talking to vinga's own server, with a fully local pipeline (wake word, speech recognition, LLM, speech synthesis) or the cloud providers you configure.

## What is vinga?

> You take what you like and mix it with some other things you like and make a new thing. *Your* thing!<br>
> -- from the movie [Be Kind Rewind (2008)](https://youtu.be/i5Rd8x4OJoY)

We took two projects we liked, the [78/xiaozhi-esp32](https://github.com/78/xiaozhi-esp32) firmware and the [xinnan-tech/xiaozhi-esp32-server](https://github.com/xinnan-tech/xiaozhi-esp32-server) conversation server, and some devices we also liked, the small [Waveshare ESP32-S3 boards](#hardware) that put a microphone, a speaker, and a display on your desk. We reimplemented what we wanted to make our own (the **conversation server**, rebuilt from the [protocol docs](https://github.com/78/xiaozhi-esp32/blob/main/docs/websocket.md) up, in Python you run yourself), kept what was already excellent (the firmware's board support, audio pipeline, and device protocol), and mixed in the things *you* like.

The new thing is a self-hostable voice agent where the device talks to **your server** and nothing else. Every stage is a **pluggable provider**: bring your own LLM (a local [Ollama](https://ollama.com), Anthropic, or any OpenAI-compatible endpoint), your own voices, and your own tools via [MCP](https://modelcontextprotocol.io). The whole loop (wake word → speech recognition → language model → speech synthesis) can run entirely on your own hardware, and wherever you choose a cloud provider instead, it is exactly that, your choice: your keys, through your server, swappable at will. No vendor cloud, no account, no activation. Your thing.

![vinga architecture overview](docs/architecture/excalidraw/vinga-architecture-overview.png)

That is the whole picture at a glance; one conversation turn in full detail, from wake word to spoken reply, is diagrammed and explained step by step in [docs/architecture](docs/architecture/README.md).

## Features

The design premise is a **thin device and a smart server**: the firmware's only tie to a backend is a single config URL, and everything else (endpoints, credentials, even firmware updates) is delivered by *your* server at runtime. Customization lives server-side, in Python, not in C++ you have to reflash.

- **Self-hosted end to end.** The device speaks Opus over a WebSocket to your server. Run it on a laptop or ship the published multi-arch container image to your own infrastructure. WebSocket is the only transport for v1; upstream's MQTT+UDP alternative may follow.
- **No account, no activation, no phone app.** Point the device at your server once; it connects and talks.
- **Configurable agents.** Define several, each with its own prompt, providers, and tools; bind devices to them, and switch mid-conversation by asking. One device can be a whole cast.
- **Pluggable LLM.** Fully local via [Ollama](https://ollama.com), or Anthropic and any OpenAI-compatible endpoint.
- **Pluggable voice.** Speech recognition and synthesis are swappable providers; a zero-API-key local pipeline (Silero VAD + faster-whisper + Piper) works today. Swap in a cloud engine for either half when a better voice, or an ear that copes with a noisy room, is worth more to you than a fully private one.
- **Tools via MCP, on both sides.** Attach any MCP server as assistant tools; the device itself exposes its controls (volume, brightness, screen) as MCP tools over the same channel.
- **Compiler-grade upstream, thin fork.** Device support, audio pipeline, and echo cancellation come from the actively maintained [xiaozhi-esp32](https://github.com/78/xiaozhi-esp32) project; vinga changes as little as possible on the device. 🚧
- **Speech in, speech out, everything visible.** Recognized text and responses render on the device display as the conversation happens.

## Hardware

Any board supported by xiaozhi-esp32 can work; these are the ones vinga targets and tests:

| Board | Display | Audio | Links | Status |
| --- | --- | --- | --- | --- |
| [Waveshare ESP32-S3-ePaper-1.54](https://www.waveshare.com/esp32-s3-epaper-1.54.htm) | 200×200 e-paper | single mic | [guide](docs/devices/waveshare-esp32-s3-epaper-1.54.md) · [wiki](https://docs.waveshare.com/ESP32-S3-ePaper-1.54) | planned 🚧 |
| [Waveshare ESP32-S3-Touch-LCD-1.54](https://www.waveshare.com/esp32-s3-lcd-1.54.htm) | 240×240 LCD, touch | dual-mic with hardware echo cancellation | [guide](docs/devices/waveshare-esp32-s3-touch-lcd-1.54.md) · [wiki](https://docs.waveshare.com/ESP32-S3-Touch-LCD-1.54) | [**working** (upstream firmware)](docs/devices/waveshare-esp32-s3-touch-lcd-1.54.md) |
| [Waveshare ESP32-S3-Touch-AMOLED-2.16](https://www.waveshare.com/esp32-s3-touch-amoled-2.16.htm) | 480×480 AMOLED, touch | dual-mic with hardware echo cancellation | [guide](docs/devices/waveshare-esp32-s3-touch-amoled-2.16.md) · [wiki](https://docs.waveshare.com/ESP32-S3-Touch-AMOLED-2.16) | planned 🚧 |

## Getting Started

The device runs upstream's prebuilt xiaozhi firmware; the server is vinga's, and ships as a container image. This is the short path; [`vinga-server/README.md`](vinga-server/README.md) has every option, the security defaults, and the container details.

**1. Write the server half.** One YAML file says how the process runs. A trial needs almost nothing in it:

```yaml
server:
  # A trial on a network you trust. Leave it out for anything that
  # outlives an afternoon: authentication is on by default.
  auth:
    enabled: false
```

Start from [`vinga-server/config.example.yaml`](vinga-server/config.example.yaml), which documents every key of it, or from [`vinga-server/config.deploy.example.yaml`](vinga-server/config.deploy.example.yaml), a ready-to-adapt deployment profile.

**2. Start the server.** An empty database is a valid state to start on: the server comes up serving no agents, and the next step configures it over the API it serves. Generate the secrets **once** and keep them somewhere you can read them back:

```bash
openssl rand -hex 32 > ~/.vinga-api-secret       # once, ever
openssl rand -hex 32 > ~/.vinga-auth-secret      # once, ever

export VINGA_API_SECRET=$(cat ~/.vinga-api-secret)
export VINGA_AUTH_SECRET=$(cat ~/.vinga-auth-secret)
docker run -d --name vinga -p 8003:8003 \
  -e VINGA_API_SECRET \
  -e VINGA_AUTH_SECRET \
  -v /path/to/config.yaml:/config/config.yaml:ro \
  -v vinga-data:/data \
  ghcr.io/rafacm/vinga-server:latest
```

`VINGA_API_SECRET` is the bearer token for the configuration API; `VINGA_AUTH_SECRET` signs the device tokens. Never mint either inline in the `docker run`: a regenerated device secret invalidates the token every device has stored ([Security](vinga-server/README.md#security)).

**3. Say what the agent is.** The other half of the configuration (which engines, which agents, which devices) lives in a database on the data volume, written with `vinga-server config`, the CLI the image ships, run inside the container where the token is already in its environment. One document says the whole of it, and one command writes it. This agent is fully local and needs no account anywhere: Silero, faster-whisper, [Ollama](https://ollama.com) and Piper.

```bash
# The CLI, inside the container started above.
vinga() { docker exec -i vinga vinga-server "$@"; }

# One document, one transaction. local-stack.yaml is the preset at
# vinga-server/examples/presets/local-stack.yaml in this repository;
# point its llm base_url at the host before applying it, which from
# inside the container is host.docker.internal rather than localhost
# (on Linux, add --add-host=host.docker.internal:host-gateway to the
# docker run in step 2).
vinga config apply -f - < local-stack.yaml

# Which agent an unknown device reaches. Bind specific devices instead
# with: vinga config bind-device aa:bb:cc:dd:ee:ff assistant
vinga config set-default-agent assistant

vinga config list
```

Applying orders the writes for you: the whole document goes in as one transaction, refused whole if anything in it will not resolve, so there is no creation order to get right and nothing is ever half applied. [`vinga-server/examples/presets/`](vinga-server/examples/presets/) holds the same deployment on vendor APIs, [`vinga-server/examples/`](vinga-server/examples/) a commented fragment per entity to copy from, and every field of them is documented in [`docs/reference/domain-config.md`](docs/reference/domain-config.md). The CLI itself, including running it against a deployment it does not host, is [`docs/reference/cli.md`](docs/reference/cli.md); the server README's [Configuration](vinga-server/README.md#configuration) section covers the API surface and how credentials are stored.

**4. Apply it.** A write is stored, not yet in effect; this builds the engines and serves them, without a restart and without dropping a conversation ([how](vinga-server/README.md#applying-a-change-without-a-restart)). The first apply downloads the speech models into `/data`, so it takes a few minutes; later ones take seconds.

```bash
vinga config reload
```

**5. Flash** the prebuilt xiaozhi merged binary for your board at offset `0x0`.

**6. Ask for the URL to type**, and check that something sensible answers on it. No cable is involved in either; the whole cable-free story is [Onboarding a device](vinga-server/README.md#onboarding-a-device).

```bash
vinga config ota-url     # http://192.168.1.10:8003/x/AB2C4D5E/
vinga doctor             # says what a device would be told, or what is wrong
```

**7. Provision WiFi** from the device's captive portal, putting that URL in the portal's advanced section as the server address. Which button brings the portal up depends on the board (PWR on the Touch-LCD-1.54, BOOT on the others), so start from your board's guide in [`docs/devices/`](docs/devices/README.md), which also covers its wake word, its display, and the rest of its controls.

**8. Bind the board.** Step 3 set a `default_agent`, so any board that reaches the server is covered: press the button and talk. Leave it unset instead and an unbound board shows and speaks a six-digit code, and one command binds it; the device polls while it waits, so it connects seconds later.

```bash
vinga config pending                       # which board is showing what
vinga config add-device 418293 assistant   # bind the one showing 418293
```

Every serial gotcha is in [`docs/xiaozhi-notes.md`](docs/xiaozhi-notes.md). vinga has no versioned releases yet: images are tagged `latest`, the build time (`2026-08-03-1200`, UTC), and the commit SHA (`sha-3f9362a`). Only `latest` moves; deploy from one of the other two.

## Project Layout

| Directory | What it is |
| --- | --- |
| [`vinga-server/`](vinga-server/) | The conversation server (Python): OTA/config endpoint, WebSocket audio channel, VAD → ASR → LLM → TTS pipeline with pluggable providers, MCP tools, device authentication. Published as a multi-arch container image. |
| [`vinga-esp32/`](vinga-esp32/) | Thin firmware customization: vinga server as default endpoint, English wake word, minimal UI changes. 🚧 |
| [`docs/`](docs/README.md) | Research notes on the upstream architecture and the device↔server protocol, plus the plans and implementation notes behind each milestone. |
| `vendor/` | Reference clones of the upstream projects (not committed). |

## Credits

vinga is assembled on top of two MIT-licensed projects, and would be nothing without them:

- [**78/xiaozhi-esp32**](https://github.com/78/xiaozhi-esp32) provides the ESP32 firmware: board support, audio pipeline, wake word, display UI, and the device↔server protocol.
- [**xinnan-tech/xiaozhi-esp32-server**](https://github.com/xinnan-tech/xiaozhi-esp32-server) proved the self-hosted backend was possible; vinga-server is a new implementation written against the firmware's [protocol docs](https://github.com/78/xiaozhi-esp32/blob/main/docs/websocket.md), keeping upstream's device-token scheme so stock firmware connects unchanged.

License notices are preserved in [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).

The word *sweded*, and the whole idea of remaking something you love with your own hands, comes from the film *Be Kind Rewind* (2008); its creators explain [How To Swede](https://youtu.be/i5Rd8x4OJoY) on YouTube.

## Changelog

Notable changes are recorded in [CHANGELOG.md](CHANGELOG.md), following the [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format with dated sections.

## License

Released under the [MIT License](LICENSE). Copyright (c) 2026 Rafael Cordones.
