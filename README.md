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

[What is it?](#what-is-vinga) • [Features](#features) • [Hardware](#hardware) • [Getting Started](#getting-started) • [Documentation](#documentation) • [Credits](#credits) • [Changelog](#changelog)

</div>

> [!WARNING]
> **Early development.** This README describes the intended v1. Sections marked 🚧 are not implemented yet. The loop works end to end today: a Waveshare ESP32-S3 running stock upstream firmware, talking to vinga's own server, with a fully local pipeline (wake word, speech recognition, LLM, speech synthesis) or the cloud providers you configure.

## What is vinga?

![A Waveshare ESP32-S3-Touch-LCD-1.54 in a white case, standing on a weathered wooden rail with a blurred green garden behind it. Its square screen shows the stock xiaozhi firmware's provisioning mode: a WiFi icon, the name of the access point the board is broadcasting, and a line telling you to open it in a browser.](assets/vinga-touch-lcd-1.54.jpg)

*A Touch-LCD-1.54 on stock upstream firmware, broadcasting its own access point and waiting to be told which network to join and which server to call.*

> You take what you like and mix it with some other things you like and make a new thing. *Your* thing!<br>
> -- from the movie [Be Kind Rewind (2008)](https://youtu.be/i5Rd8x4OJoY)

We took two projects we liked, the [78/xiaozhi-esp32](https://github.com/78/xiaozhi-esp32) firmware and the [xinnan-tech/xiaozhi-esp32-server](https://github.com/xinnan-tech/xiaozhi-esp32-server) conversation server, and some devices we also liked, the small [Waveshare ESP32-S3 boards](#hardware) that put a microphone, a speaker, and a display on your desk. We reimplemented what we wanted to make our own (the **conversation server**, rebuilt from the [protocol docs](https://github.com/78/xiaozhi-esp32/blob/main/docs/websocket.md) up, in Python you run yourself), kept what was already excellent (the firmware's board support, audio pipeline, and device protocol), and mixed in the things *you* like.

The new thing is a self-hostable voice agent where the device talks to **your server** and nothing else. Every stage is a **pluggable provider**: bring your own LLM (a local [Ollama](https://ollama.com), Anthropic, or any OpenAI-compatible endpoint), your own voices, and your own tools via [MCP](https://modelcontextprotocol.io). The whole loop (wake word → speech recognition → language model → speech synthesis) [can run entirely on your own hardware](docs/architecture/product-promises.md#a-fully-local-deployment-is-first-class), and wherever you choose a cloud provider instead, it is exactly that, your choice: your keys, through your server, swappable at will. No vendor cloud, no account, nobody else's activation. Your thing.

## Features

The design premise is a [**thin device and a smart server**](docs/architecture/guidelines.md#thin-device-smart-server): the firmware's only tie to a backend is a single config URL, and everything else (endpoints, credentials, even firmware updates) is delivered by *your* server at runtime. Customization lives server-side, in Python, not in C++ you have to reflash.

- **Your server is the only one it talks to.** The board speaks Opus over a WebSocket, the one transport v1 carries, to an address you chose. No account to create, no vendor cloud between the two, no phone app in the way: point the board at your server once and it connects. Run that server on a laptop while you are trying it, or ship the published multi-arch container image to your own infrastructure.
- **A conversation that needs no API key at all.** Silero, faster-whisper, [Ollama](https://ollama.com) and Piper make a loop that [runs entirely on your own hardware](docs/architecture/product-promises.md#a-fully-local-deployment-is-first-class), works today, bills nothing per word, and is heard nowhere else.
- **Every stage is swappable, the language model included.** Point the LLM at that local Ollama, at Anthropic, or at any OpenAI-compatible endpoint. Swap either half of the voice for a cloud engine when a better voice, or an ear that copes with a noisy room, is worth more to you than a fully private one.
- **Configurable agents.** Define several, each with its own prompt, providers, and tools; bind devices to them, and switch mid-conversation by asking. One device can be a whole cast.
- **Tools via MCP, on both sides.** Attach any MCP server as assistant tools; the device itself exposes its controls (volume, brightness, screen) as MCP tools over the same channel.
- **Speech in, speech out, everything visible.** Recognized text and responses render on the device display as the conversation happens.
- **Try it without hardware.** `vinga simulator` is a simulated board in the CLI: it checks in, claims itself, and holds a conversation, with the terminal standing in for the display.

## Hardware

Any board xiaozhi-esp32 supports can work, since the device runs upstream's firmware. These three are the ones vinga cares about, and they are not in the same state. The Touch-LCD-1.54 at the top is the board vinga is developed and tested on; the two under it are targets, with a guide each and no hands-on run behind them yet.

| Board | Display | Audio | Links | Status |
| --- | --- | --- | --- | --- |
| [Waveshare ESP32-S3-Touch-LCD-1.54](https://www.waveshare.com/esp32-s3-lcd-1.54.htm) | 240×240 LCD, touch | dual-mic with hardware echo cancellation | [guide](docs/devices/waveshare-esp32-s3-touch-lcd-1.54.md) · [wiki](https://docs.waveshare.com/ESP32-S3-Touch-LCD-1.54) | [**working** (upstream firmware)](docs/devices/waveshare-esp32-s3-touch-lcd-1.54.md) |
| [Waveshare ESP32-S3-Touch-AMOLED-2.16](https://www.waveshare.com/esp32-s3-touch-amoled-2.16.htm) | 480×480 AMOLED, touch | dual-mic with hardware echo cancellation | [guide](docs/devices/waveshare-esp32-s3-touch-amoled-2.16.md) · [wiki](https://docs.waveshare.com/ESP32-S3-Touch-AMOLED-2.16) | planned 🚧 |
| [Waveshare ESP32-S3-ePaper-1.54](https://www.waveshare.com/esp32-s3-epaper-1.54.htm) | 200×200 e-paper | single mic | [guide](docs/devices/waveshare-esp32-s3-epaper-1.54.md) · [wiki](https://docs.waveshare.com/ESP32-S3-ePaper-1.54) | planned 🚧 |

## Getting Started

At the end of this you press a button on a board across the room, say something, and a model running on your own machine answers out loud. Once it is set up, nothing you say leaves the house: the transcription, the model and the voice all run on your hardware, and no part of a conversation is sent anywhere.

Eight steps, numbered from zero. The device runs upstream's prebuilt xiaozhi firmware; the server is vinga's and ships as a container image, configured over the API it serves with the `vinga` command line. [`vinga-server/README.md`](vinga-server/README.md) has every option, the security defaults, and the container details.

### Prerequisites

- **[Ollama](https://ollama.com)**, which serves the model in step 0.
- **Docker** with Compose v2, for the two containers of step 1.
- **[uv](https://docs.astral.sh/uv/)**, for the CLI of step 2.
- **A board** from the Hardware table, and a USB cable for the ones whose image has no Custom OTA URL field.
- **`curl`, `openssl` and `git`**, which steps 1 and 2 invoke directly. macOS ships all three, which is why they went unnoticed while this was being walked rather than read; a stripped-down Linux may not have them.

**0. Pull the model.** The stack in step 3 is fully local, and the one piece of it that does not ship in the server image is the language model. Ollama serves it on this machine, and the server dials it from inside its container.

```bash
ollama pull qwen3:8b
```

Any model Ollama serves works, and any endpoint that speaks the OpenAI chat completions API does too, llama.cpp and vLLM among them; `qwen3:8b` is the one this walkthrough was run with. What each field of a provider entry means is in [`docs/reference/domain-config.md`](docs/reference/domain-config.md), and [`vinga-server/examples/`](vinga-server/examples/) holds a commented fragment per provider type.

**1. Start the server.** It is two containers, the server and the one Postgres database it keeps everything in, and they come up together from the compose file this repository publishes. No checkout is needed: fetch that file and the provisioning script beside it into a directory of its own, mint the two secrets, and start it.

```bash
mkdir vinga && cd vinga
curl -O https://raw.githubusercontent.com/rafacm/vinga/main/docker-compose.yml
curl --create-dirs -o deploy/postgres-init.sql \
  https://raw.githubusercontent.com/rafacm/vinga/main/deploy/postgres-init.sql

# The two secrets, minted once and kept: this file is what the server
# is given, and it is where you read the API token back from later.
umask 077
{ echo "VINGA_API_SECRET=$(openssl rand -hex 32)"
  echo "VINGA_AUTH_SECRET=$(openssl rand -hex 32)"
  echo "VINGA_SERVER__AUTH__ENABLED=false"
} > .env

docker compose --profile server up -d --wait
```

That is the whole of it: the database, then the server once the database answers, and `--wait` returns when both are healthy. An empty database is a valid state to start on, so no configuration file is needed either; the server comes up serving no agents, and the next two steps configure it over the API. Stop it with `docker compose --profile server down`, and add `-v` to throw the database and the downloaded models away too.

`VINGA_API_SECRET` is the bearer token for the configuration API and `VINGA_AUTH_SECRET` signs the device tokens. **Neither has a default anywhere**, and with no `.env` at all compose refuses before a container starts. After that the two differ, because what needs them differs. The API is always mounted and always behind its token, so a missing `VINGA_API_SECRET` is always a boot that refuses, naming the variable. Device authentication is what `VINGA_AUTH_SECRET` is for, so it is required exactly when that is on, which is the default and is not what the third line above selects: **this trial boots without it**. Mint it now anyway and keep both, because the secret signs every device token and the key in the onboarding URL of step 5 is derived from it, so regenerating it later invalidates the token every device has stored and moves that URL ([Security](vinga-server/README.md#security)).

The same file is where every other variable the server should find in its environment goes, a provider credential your agent names among them. `VINGA_SERVER__AUTH__ENABLED=false` is for a trial on a network you trust; drop that line for anything that outlives an afternoon.

The compose file is the one place the topology is written down, so nothing here restates it: the database is published on loopback while the server's port 8003 is published on every interface, because boards on your LAN have to reach it. **The database password it defaults to is a trial convenience and nothing more**: a deployment sets a real one, and [The configuration database in a deployment](vinga-server/README.md#the-configuration-database-in-a-deployment) covers that along with the privileges the server needs, the provisioning file at [`deploy/postgres-init.sql`](deploy/postgres-init.sql) and backups. The server migrates its schemas at every boot, so there is no init step to run. A checkout already has both files and runs the same command from its root; running the server itself some other way, on one container against a Postgres you provide, is [Running in a container](vinga-server/README.md#running-in-a-container), which also has every key of the server half and the YAML file that is the alternative to a handful of variables.

**2. Install the CLI.** `vinga` is a client of the configuration API rather than a second way into the database, so this is how a deployment is administered from here on, whether it runs on this machine or across the network.

```bash
uv tool install "git+https://github.com/rafacm/vinga#subdirectory=vinga-server"

vinga list
```

There is nothing to export. Run it from the directory step 1 made and it reads that same `.env` itself, searching upwards from wherever it is invoked, and with anything already in your environment winning over the file; a server it is not told about is assumed to be this machine on the port the server half names, which is where step 1 put one. Name a different one with `VINGA_API_URL` or `--api-url`, remembering that plain http is allowed to a loopback address and to nothing else, since the bearer token crosses every request: a deployment anywhere else is https.

**Or install nothing.** The image from step 1 already carries the same CLI, built together with the server it talks to, so the pair cannot disagree about the grammar. From the same directory, a shell function makes it the same word:

```bash
vinga() { docker compose exec -T vinga vinga "$@"; }

vinga list
```

The function shadows an installed `vinga` for as long as the shell defining it lives, and a file on this machine is piped in rather than named, because a path is resolved inside the container; the image door in the CLI's own page has both details and how to put the binary back.

[`docs/reference/cli.md`](docs/reference/cli.md) is the CLI's own page: the one-off `uvx` spelling, the image door above, reaching a deployment you do not host, and every command's help.

**3. Say what the agent is.** Which engines, which agents, which devices: this is the other half of the configuration, and it is written a line at a time. The stack these lines build is fully local and needs no account anywhere: Silero listens for the end of a phrase, faster-whisper transcribes, [Ollama](https://ollama.com) answers and Piper speaks.

```bash
vinga provider set llm local type=openai_compatible base_url=http://host.docker.internal:11434/v1 model=qwen3:8b egress=false
vinga provider set asr whisper type=faster_whisper model=small vad_filter=true
vinga provider set tts voice type=piper voice=en_US-lessac-medium
vinga provider set vad ears type=silero
vinga agent-defaults set llm=local asr=whisper tts=voice vad=ears
vinga agent set assistant "prompt=You are a helpful voice assistant. Keep replies short, plain, and speakable. One or two sentences, no lists, no markdown. Always reply in the language the user spoke."
vinga default-agent set assistant
vinga apply
```

Read top to bottom that is the whole deployment: four engines, the defaults that say which one each job uses, the one agent that inherits them, and which agent a board reaches when nothing has bound it. Bind a specific board instead, by the MAC on its sticker, with `vinga device bind aa:bb:cc:dd:ee:ff assistant`.

`host.docker.internal` rather than `localhost`, because the server dials the model from inside its container and localhost there is the container. The compose file resolves that name to this machine. This was walked on macOS, where it reaches Ollama even though Ollama listens only on loopback. **On Linux, resolving that name is not enough**: Ollama binds loopback there too, and a container cannot reach a loopback-only service, so it has to be told to listen more widely with `OLLAMA_HOST` ([Ollama's FAQ](https://github.com/ollama/ollama/blob/main/docs/faq.md) has the variable and where to set it for your init system). That path is untested here.

Type them in the order shown. A write whose references do not resolve is refused, so the defaults cannot name providers that do not exist yet and the default agent cannot name an agent that does not: the order is the shape of the deployment, not a convention. What is decoupled is installation, not order. Writing an entry stores it and does not touch the running server, so all seven land before anything is built, and there is no window where the server is half configured. `vinga apply` then builds the engines and serves the agents without a restart and without dropping a conversation ([how](vinga-server/README.md#applying-a-change-without-a-restart)). The first apply is the slow one, because it downloads the speech models onto the data volume. `vinga default-agent set` needs no apply either way: it is read as a device asks for it.

The same deployment in document form is [`vinga-server/examples/presets/local-stack.yaml`](vinga-server/examples/presets/local-stack.yaml), imported whole in one transaction with `vinga import -f` and then installed with `vinga apply`, which is the better shape once there is more here than fits on a screen. It is not a drop-in replacement for the lines above: it says `localhost` where they say `host.docker.internal`, and it deliberately sets no `default_agent`, because which board reaches which agent is the one thing a preset cannot know. The presets beside it are the same deployment on vendor APIs. Every field of either spelling is documented in [`docs/reference/domain-config.md`](docs/reference/domain-config.md).

**4. Flash** the prebuilt xiaozhi merged binary for your board at offset `0x0`. Every serial gotcha is in [`docs/devices/README.md`](docs/devices/README.md#driving-a-board-from-a-terminal-session).

**5. Get the URL to type.** The server derives it from the file half and the device-auth secret, and answers it over the API along with which build is running, so this runs from the directory step 1 made like everything else.

```bash
vinga info
# ...
# the URL to type into a device's captive portal, from server.public_url:
# http://192.168.1.10:8003/x/AB2C4D5E/
```

The key at the end is derived from the device-auth secret, so a trial that turned device authentication off in step 1 has none and the URL ends at `/x/`; it serves the same activation flow either way. That key is why the read is behind the same token every other one is: it stands in front of the endpoint that issues device tokens, which is also why the server's own startup line names the origin without it. `docker compose exec vinga vinga-server doctor` says what a device would be told on that URL, or what is wrong, and runs where the server is because that is what it checks; no `--profile` is needed once the stack is up, since the profile decides what starts and this reaches a service that is already running. No cable is involved in any of it; the whole cable-free story is [Onboarding a device](vinga-server/README.md#onboarding-a-device).

**6. Provision WiFi and give the board that URL.** How the URL gets there depends on the image your board runs, so start from your board's guide in [`docs/devices/`](docs/devices/README.md), which says which button brings its portal up and also covers its wake word, its display, and the rest of its controls. Where the image's captive portal carries a Custom OTA URL field in its advanced section, that is the whole step and no cable is needed: join the board's access point and enter your WiFi and the URL together. Where it does not, and the Touch-LCD-1.54 image tested here is one that does not, write the URL into the board's NVS over USB first, by [the procedure on the common page](docs/devices/README.md#writing-the-servers-address-into-nvs), then provision WiFi from the portal.

**7. Talk.** Step 3 set a `default_agent`, so any board that reaches the server is covered: press the button and speak. Leave it unset instead and an unbound board shows and speaks a six-digit code, and one command binds it; the device polls while it waits, so it connects seconds later.

```bash
vinga device pending list                     # which board is showing what
vinga device pending claim 418293 assistant   # bind the one showing 418293
```

Which image tag to deploy from, and the slim variant that carries neither local engine, are in [Choosing an image](vinga-server/README.md#choosing-an-image). Everything else this project knows is indexed in [`docs/`](docs/README.md).

## Documentation

[`docs/`](docs/README.md) is the index, and it says which class each page belongs to and therefore what that page may claim. Four doors into it:

- [**system-overview.md**](docs/system-overview.md): one conversation turn end to end, from the wake word to the spoken reply, diagrammed and explained a step at a time.
- [**devices/**](docs/devices/README.md): a guide per board. Which button starts a conversation, which wake word is enabled, what the display shows, and the serial procedures that get a server's address into a board.
- [**reference/**](docs/reference/): generated from the code and diffed by CI, so it cannot come to describe a server this repository does not build. Every CLI command, every configuration field, the API contract, and the structured events.
- [**architecture/**](docs/architecture/README.md): the promises vinga makes to whoever runs it, the guidelines that keep them, and the design and CLI standards every change is held to.

The other two READMEs are pages in their own right: [`vinga-server/`](vinga-server/README.md) for every provider option, the security defaults, the container and onboarding a device, and [`vinga-esp32/`](vinga-esp32/README.md) for the firmware side.

## Credits

vinga is assembled on top of two MIT-licensed projects, and would be nothing without them:

- [**78/xiaozhi-esp32**](https://github.com/78/xiaozhi-esp32) provides the ESP32 firmware: board support, audio pipeline, wake word, display UI, and the device↔server protocol.
- [**xinnan-tech/xiaozhi-esp32-server**](https://github.com/xinnan-tech/xiaozhi-esp32-server) proved the self-hosted backend was possible; vinga-server is a new implementation written against the firmware's [protocol docs](https://github.com/78/xiaozhi-esp32/blob/main/docs/websocket.md), keeping upstream's device-token scheme so stock firmware connects unchanged.

License notices are preserved in [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).

Those two are the foundation rather than the neighbourhood. [`docs/related-projects.md`](docs/related-projects.md) is the survey of the rest: what each nearby voice assistant is, where it overlaps, where vinga is deliberately different, and what vinga borrows. Several of them are older than vinga and better at what they set out to do, and the page says so.

The word *sweded*, and the whole idea of remaking something you love with your own hands, comes from the film *Be Kind Rewind* (2008); its creators explain [How To Swede](https://youtu.be/i5Rd8x4OJoY) on YouTube.

## Changelog

Notable changes are recorded in [CHANGELOG.md](CHANGELOG.md), following the [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format with dated sections.

## License

Released under the [MIT License](LICENSE). Copyright (c) 2026 Rafael Cordones.
