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

The new thing is a self-hostable voice agent where the device talks to **your server** and nothing else. Every stage is a **pluggable provider**: bring your own LLM (a local [Ollama](https://ollama.com), Anthropic, or any OpenAI-compatible endpoint), your own voices, and your own tools via [MCP](https://modelcontextprotocol.io). The whole loop (wake word → speech recognition → language model → speech synthesis) can run entirely on your own hardware, and wherever you choose a cloud provider instead, it is exactly that, your choice: your keys, through your server, swappable at will. No vendor cloud, no account, nobody else's activation. Your thing.

![vinga architecture overview](docs/architecture/diagrams/excalidraw/vinga-architecture-overview.png)

That is the whole picture at a glance; one conversation turn in full detail, from wake word to spoken reply, is diagrammed and explained step by step in [docs/system-overview.md](docs/system-overview.md).

## Features

The design premise is a **thin device and a smart server**: the firmware's only tie to a backend is a single config URL, and everything else (endpoints, credentials, even firmware updates) is delivered by *your* server at runtime. Customization lives server-side, in Python, not in C++ you have to reflash.

- **Self-hosted end to end.** The device speaks Opus over a WebSocket to your server. Run it on a laptop or ship the published multi-arch container image to your own infrastructure. WebSocket is the only transport for v1; upstream's MQTT+UDP alternative may follow.
- **No account, no vendor cloud, no phone app.** Point the device at your server once; it connects and talks.
- **Configurable agents.** Define several, each with its own prompt, providers, and tools; bind devices to them, and switch mid-conversation by asking. One device can be a whole cast.
- **Pluggable LLM.** Fully local via [Ollama](https://ollama.com), or Anthropic and any OpenAI-compatible endpoint.
- **Pluggable voice.** Speech recognition and synthesis are swappable providers; a zero-API-key local pipeline (Silero VAD + faster-whisper + Piper) works today. Swap in a cloud engine for either half when a better voice, or an ear that copes with a noisy room, is worth more to you than a fully private one.
- **Tools via MCP, on both sides.** Attach any MCP server as assistant tools; the device itself exposes its controls (volume, brightness, screen) as MCP tools over the same channel.
- **Compiler-grade upstream, thin fork.** Device support, audio pipeline, and echo cancellation come from the actively maintained [xiaozhi-esp32](https://github.com/78/xiaozhi-esp32) project; vinga changes as little as possible on the device. 🚧
- **Speech in, speech out, everything visible.** Recognized text and responses render on the device display as the conversation happens.
- **Try it without hardware.** `vinga simulator` is a simulated board in the CLI: it checks in, claims itself, and holds a conversation, with the terminal standing in for the display.

## Hardware

Any board supported by xiaozhi-esp32 can work; these are the ones vinga targets and tests:

| Board | Display | Audio | Links | Status |
| --- | --- | --- | --- | --- |
| [Waveshare ESP32-S3-ePaper-1.54](https://www.waveshare.com/esp32-s3-epaper-1.54.htm) | 200×200 e-paper | single mic | [guide](docs/devices/waveshare-esp32-s3-epaper-1.54.md) · [wiki](https://docs.waveshare.com/ESP32-S3-ePaper-1.54) | planned 🚧 |
| [Waveshare ESP32-S3-Touch-LCD-1.54](https://www.waveshare.com/esp32-s3-lcd-1.54.htm) | 240×240 LCD, touch | dual-mic with hardware echo cancellation | [guide](docs/devices/waveshare-esp32-s3-touch-lcd-1.54.md) · [wiki](https://docs.waveshare.com/ESP32-S3-Touch-LCD-1.54) | [**working** (upstream firmware)](docs/devices/waveshare-esp32-s3-touch-lcd-1.54.md) |
| [Waveshare ESP32-S3-Touch-AMOLED-2.16](https://www.waveshare.com/esp32-s3-touch-amoled-2.16.htm) | 480×480 AMOLED, touch | dual-mic with hardware echo cancellation | [guide](docs/devices/waveshare-esp32-s3-touch-amoled-2.16.md) · [wiki](https://docs.waveshare.com/ESP32-S3-Touch-AMOLED-2.16) | planned 🚧 |

## Getting Started

Seven steps. The device runs upstream's prebuilt xiaozhi firmware; the server is vinga's and ships as a container image, configured over the API it serves with the `vinga` command line. [`vinga-server/README.md`](vinga-server/README.md) has every option, the security defaults, and the container details.

**1. Start the server.** It keeps everything it stores in one Postgres database, which is the one thing it needs from you before it will boot; an empty database is a valid state to start on, so no configuration file is needed either. The server comes up serving no agents, and the next two steps configure it over the API. Generate the secrets **once** and keep them somewhere you can read them back:

```bash
openssl rand -hex 32 > ~/.vinga-api-secret       # once, ever
openssl rand -hex 32 > ~/.vinga-auth-secret      # once, ever

# The database. A network of its own, because the server reaches it by
# name: inside a container, 127.0.0.1 is the container itself.
docker network create vinga-net
docker run -d --name vinga-db --network vinga-net \
  -e POSTGRES_DB=vinga -e POSTGRES_USER=vinga -e POSTGRES_PASSWORD=vinga \
  postgres:17-alpine

# Wait for it, because the server refuses to boot on a database it
# cannot reach yet rather than retrying: a restart policy is where that
# decision belongs, and here it is one line.
until docker exec vinga-db pg_isready -U vinga -d vinga; do sleep 1; done

export VINGA_API_SECRET=$(cat ~/.vinga-api-secret)
export VINGA_AUTH_SECRET=$(cat ~/.vinga-auth-secret)
docker run -d --name vinga -p 8003:8003 --network vinga-net \
  -e VINGA_API_SECRET \
  -e VINGA_AUTH_SECRET \
  -e VINGA_DB_HOST=vinga-db \
  -e VINGA_SERVER__AUTH__ENABLED=false \
  -v vinga-data:/data \
  ghcr.io/rafacm/vinga-server:latest
```

`VINGA_API_SECRET` is the bearer token for the configuration API and `VINGA_AUTH_SECRET` signs the device tokens, so neither is minted inline in the `docker run`: a regenerated device secret invalidates the token every device has stored ([Security](vinga-server/README.md#security)). `VINGA_SERVER__AUTH__ENABLED=false` is for a trial on a network you trust; drop that line for anything that outlives an afternoon, since device authentication is on by default. Every other key of the server half, and the YAML file that is the alternative to a handful of variables, are in [Running in a container](vinga-server/README.md#running-in-a-container).

The `VINGA_DB_*` family is how the server is told where that database is (`VINGA_DB_HOST`, `VINGA_DB_PORT`, `VINGA_DB_NAME`, `VINGA_DB_USER`, `VINGA_DB_PASSWORD`, or a single `VINGA_DB_URL` that replaces all five), and everything unset above is a default that matches the values in the command. **The password `vinga` is a trial convenience and nothing more**: a deployment sets a real one, and [The configuration database in a deployment](vinga-server/README.md#the-configuration-database-in-a-deployment) covers that along with the privileges the server needs, the provisioning file at [`deploy/postgres-init.sql`](deploy/postgres-init.sql) and backups. A checkout skips all of this with `docker compose up -d --wait`, which starts the same database on loopback with the same defaults. The server migrates its schemas at every boot, so there is no init step to run, and it refuses to start rather than waiting when the database is not there.

**2. Install the CLI**, and point it at the server you just started. `vinga` is a client of the configuration API rather than a second way into the database, so this is how a deployment is administered from here on, whether it runs on this machine or across the network.

```bash
uv tool install "git+https://github.com/rafacm/vinga#subdirectory=vinga-server"

# Where the API is, and the token from step 1. Plain http is allowed to a
# loopback address and to nothing else; a deployment anywhere else is https.
export VINGA_API_URL=http://127.0.0.1:8003/api
export VINGA_API_SECRET=$(cat ~/.vinga-api-secret)

vinga list
```

[`docs/reference/cli.md`](docs/reference/cli.md) is the CLI's own page: the one-off `uvx` spelling, reaching a deployment you do not host, and every command's help.

**3. Say what the agent is.** The other half of the configuration (which engines, which agents, which devices) is one document, written in one transaction and refused whole rather than half applied, so there is no creation order to get right ([Configuration](vinga-server/README.md#configuration)). This preset is fully local and needs no account anywhere: Silero, faster-whisper, [Ollama](https://ollama.com) and Piper.

```bash
curl -O https://raw.githubusercontent.com/rafacm/vinga/main/vinga-server/examples/presets/local-stack.yaml

# The server dials the model from inside the container, so change the llm
# base_url in that file from localhost to host.docker.internal. On Linux,
# add --add-host=host.docker.internal:host-gateway to the docker run above.
vinga apply -f local-stack.yaml

# Which agent an unknown device reaches. Bind specific devices instead
# with: vinga device bind aa:bb:cc:dd:ee:ff assistant
vinga default-agent set assistant

vinga reload
```

A write is stored, not yet in effect; `reload` builds the engines and serves them, without a restart and without dropping a conversation ([how](vinga-server/README.md#applying-a-change-without-a-restart)). The first one downloads the speech models onto the data volume, so it takes a few minutes; later ones take seconds. [`vinga-server/examples/presets/`](vinga-server/examples/presets/) holds the same deployment on vendor APIs, [`vinga-server/examples/`](vinga-server/examples/) a commented fragment per entity to copy from, and every field of them is documented in [`docs/reference/domain-config.md`](docs/reference/domain-config.md).

**4. Flash** the prebuilt xiaozhi merged binary for your board at offset `0x0`. Every serial gotcha is in [`docs/devices/README.md`](docs/devices/README.md#driving-a-board-from-a-terminal-session).

**5. Get the URL to type.** This step runs where the server is, because the URL is derived from the file half and the device-auth secret, which live with the server rather than with the client.

```bash
docker exec -i vinga vinga-server config ota-url
# http://192.168.1.10:8003/x/AB2C4D5E/
```

`docker exec -i vinga vinga-server doctor` says what a device would be told on that URL, or what is wrong. No cable is involved in either; the whole cable-free story is [Onboarding a device](vinga-server/README.md#onboarding-a-device).

**6. Provision WiFi and give the board that URL.** How the URL gets there depends on the image your board runs, so start from your board's guide in [`docs/devices/`](docs/devices/README.md), which says which button brings its portal up and also covers its wake word, its display, and the rest of its controls. Where the image's captive portal carries a Custom OTA URL field in its advanced section, that is the whole step and no cable is needed: join the board's access point and enter your WiFi and the URL together. Where it does not, and the Touch-LCD-1.54 image tested here is one that does not, write the URL into the board's NVS over USB first, by [the procedure on the common page](docs/devices/README.md#writing-the-servers-address-into-nvs), then provision WiFi from the portal.

**7. Talk.** Step 3 set a `default_agent`, so any board that reaches the server is covered: press the button and speak. Leave it unset instead and an unbound board shows and speaks a six-digit code, and one command binds it; the device polls while it waits, so it connects seconds later.

```bash
vinga device pending list                     # which board is showing what
vinga device pending claim 418293 assistant   # bind the one showing 418293
```

**No board yet?** A simulated one ships with the CLI. It makes the real check-in, shows the activation code a screen would show, and then holds one conversation over the websocket: it says a packaged sentence and prints the transcript and the reply as they arrive. It is a board's protocol, not a board: no microphone, no speaker, no wake word and one fixed sentence, and its help page lists exactly what it does and does not do.

```bash
uvx --from "vinga-server[sim] @ git+https://github.com/rafacm/vinga#subdirectory=vinga-server" \
  vinga simulator run https://voice.example/xiaozhi/ota/ --claim assistant
```

Which image tag to deploy from, and the slim variant that carries neither local engine, are in [Choosing an image](vinga-server/README.md#choosing-an-image). Everything else this project knows is indexed in [`docs/`](docs/README.md).

## Project Layout

| Directory | What it is |
| --- | --- |
| [`vinga-server/`](vinga-server/) | The conversation server (Python): OTA/config endpoint, WebSocket audio channel, VAD → ASR → LLM → TTS pipeline with pluggable providers, MCP tools, device authentication. Published as a multi-arch container image. |
| [`vinga-esp32/`](vinga-esp32/) | Thin firmware customization: vinga server as default endpoint, English wake word, minimal UI changes. 🚧 |
| [`deploy/`](deploy/) | What a deployment runs against its own Postgres before the server does: [`postgres-init.sql`](deploy/postgres-init.sql) creates the two schemas the server owns and the read-only role the conversation record is read through. The `docker-compose.yml` at the root runs the same file against the development database. |
| [`docs/`](docs/README.md) | The reference pages (the CLI, every configuration field, the API contract, the events), the per-board device guides, the architecture and its principles, and the record: research notes, plans, features and decisions. |
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
