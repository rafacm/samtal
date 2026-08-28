# Related projects

The projects vinga is measured against, and the ones it is made of. This
is a survey of the neighbourhood, not a scoreboard: most of these predate
vinga, several are better at what they set out to do, and two of them
supply components vinga ships.

Two kinds of entry, kept apart because they answer different questions.
[Alternatives](#alternatives-and-neighbours) are projects a person could
choose *instead of* vinga, and each one answers four questions in the
same order: what it is, where it overlaps, where vinga is deliberately
different, and what vinga borrows. [What vinga is built
from](#what-vinga-is-built-from) is the shorter register: what the thing
is and why vinga touches it. License terms for anything shipped or
bundled live in [`THIRD_PARTY_LICENSES.md`](../THIRD_PARTY_LICENSES.md)
and are not repeated here.

Claims are dated at the paragraph that makes them and go stale. Check
before quoting one.

## Alternatives and neighbours

### Rhasspy

[rhasspy/rhasspy](https://github.com/rhasspy/rhasspy) ·
[docs](https://rhasspy.readthedocs.io/en/latest/) · MIT ·
**archived 6 October 2025**

A fully offline voice assistant for home automation, built as independent
services that coordinate over MQTT using a superset of the Snips *Hermes*
protocol, in a base station plus satellites topology, with first-class
Home Assistant integration. It covers wake word, speech recognition,
intent recognition, and speech synthesis.

**Overlap.** The premise is the same one vinga starts from: thin
listening endpoints, a server the user runs, nothing leaving the house.
vinga's ESP32-S3 board is functionally a satellite and the vinga server
is functionally the base station, with the same four pipeline stages
wired as swappable services rather than a monolith.

**Difference.** Rhasspy recognises *intents*, vinga holds a
*conversation*. Rhasspy matches speech against a template grammar and
emits a JSON intent for an automation to act on; there is no language
model in the loop and no dialogue state. vinga's middle stage is an LLM
with MCP tools, and the grammar of what can be said is whatever the model
understands. The transports differ to match: MQTT and Hermes messages
against Opus frames over a WebSocket carrying the xiaozhi device
protocol.

**What vinga takes.** Two of vinga's pipeline components come out of
this project. Piper, the local speech synthesis provider, is a Rhasspy
project, and vinga still downloads its voices from the
`rhasspy/piper-voices` model repository; `rhasspy/piper` was archived in
the same month as Rhasspy itself, so vinga uses the maintained
`piper1-gpl` successor. `pysilero-vad`, the voice activity detection in
every conversation vinga holds and a core dependency rather than an
optional one, is also published from the `rhasspy` organisation. vinga runs none
of Rhasspy and depends on two pieces of it.

#### The successor line: Wyoming and Home Assistant Assist

Rhasspy 2.5, which the readthedocs site documents, is not where this
lineage ended. Rhasspy 3 replaced the Hermes and MQTT design with the
[Wyoming protocol](https://www.home-assistant.io/integrations/wyoming/),
and Wyoming is what Home Assistant's Assist pipeline speaks today: a wake
word, speech-to-text, a conversation agent, and speech synthesis services
(openWakeWord, `wyoming-faster-whisper`, Piper) wired together, with
[`wyoming-satellite`](https://github.com/rhasspy/wyoming-satellite) and
Home Assistant's own ESP32 voice hardware as the endpoints.

That stack, rather than Rhasspy 2.5, is vinga's nearest living
neighbour, and it arrives at nearly the same picture from the other
direction: wake word to speech recognition to a language model to speech
synthesis, on small always-on hardware, self-hosted. Two differences are
structural rather than cosmetic.

- **The hub.** Assist assumes Home Assistant as the conversation agent
  and the reason the device exists. vinga assumes no hub: the agent is
  configured in vinga's own config, and home automation is one MCP tool
  server among any others, or absent.
- **The wire.** Wyoming is a protocol its own ecosystem defines and can
  change. vinga speaks the xiaozhi device protocol, which buys the
  firmware, board support, audio pipeline and echo cancellation of an
  actively maintained device project without writing any of it, at the
  cost of implementing a protocol vinga does not control. That trade,
  and what stock firmware costs the server as a result, is written up in
  [`xiaozhi-notes.md`](xiaozhi-notes.md).

### ElatoAI

[akdeb/ElatoAI](https://github.com/akdeb/ElatoAI) · MIT · commercial
hardware and a Kickstarter alongside the repository

Realtime voice AI on an ESP32-S3, PSRAM not required, built on the
Arduino framework. The device holds a secure WebSocket to an edge server
and streams Opus at 12 kbps and 24 kHz in both directions; the backend
runs as Deno edge functions or Cloudflare Workers with Supabase for the
database and authentication, and the conversation itself is handed to a
vendor's realtime speech-to-speech API (OpenAI Realtime, Gemini Live,
Grok, ElevenLabs Conversational AI, Hume EVI). A FastAPI self-hosted path
exists.

**Overlap.** The closest match to vinga's physical shape of anything in
this document, and independent evidence that the shape works: same chip
family, same codec, same transport, same division of labour between a
thin device and a server that thinks. Both aim at conversation that
survives being talked over and interrupted, not at one-shot commands.

**Difference.** Where the intelligence lives, and who owns the account.
ElatoAI's default path puts the whole conversation inside one vendor's
realtime API and the device's identity inside a hosted Supabase project,
which is what buys its latency and its short setup. vinga splits the
pipeline into stages it owns (VAD, speech recognition, language model,
speech synthesis) so that each one is swappable and the whole loop can
run with no account anywhere, accepting that a staged pipeline has more
moving parts than a single realtime socket. The firmware differs in the
same direction: Arduino and a bespoke sketch against ESP-IDF and an
upstream project vinga tries not to fork.

**What vinga takes.** Nothing in code. It is worth reading as the
strongest argument for the road vinga did not take, single-vendor
realtime speech-to-speech, which is a plausible future vinga provider
rather than a rival architecture.

### Hermes Agent

[NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent)
· MIT · actively developed as of August 2026

A self-hosted LLM agent from Nous Research: persistent memory across
sessions, skills it writes for itself, cron-scheduled jobs, and delivery
into a couple of dozen chat platforms (Telegram, Discord, Slack,
WhatsApp, Signal, email) plus a native CLI. It both consumes and exposes
MCP. The site
[hermes-agent.ai](https://hermes-agent.ai/features/voice-mode) documents
it but states it is a fan site, unaffiliated with Nous Research.

**Overlap.** Almost none at the layer vinga occupies, despite the shared
"voice" vocabulary. Hermes Agent's voice mode is voice *messaging*:
transcribing a Telegram voice note with Whisper, replying through
ElevenLabs or a local engine, push-to-talk in the terminal. There is no
wake word, no always-on microphone, no audio streaming protocol, and no
hardware. Everything vinga spends its effort on lives below where Hermes
Agent starts.

Worth flagging, because the names collide and the search results do not
separate them: Hermes Agent has nothing to do with the *Hermes protocol*
that Rhasspy speaks, which came from Snips.

**Difference, and where it is really a stack.** Hermes Agent is a
candidate to sit behind vinga rather than beside it. vinga is the ears,
the mouth, and the presence in the room; an agent like this one is a
candidate brain, reachable two ways that already exist: as an MCP tool
server vinga attaches, or as the endpoint vinga's LLM provider points
at.

**The tension to watch.** vinga's own direction (per-device agents,
household users, budgets) overlaps with what Hermes Agent calls memory
and user modelling. That is an argument for keeping vinga's agent layer
thin and delegating upward, not for rebuilding it.

## What vinga is built from

Not neighbours: the projects vinga actually runs, links, or drives. The
preference throughout is to use a published artifact rather than fork
one, so most of these are ordinary dependencies with no vinga patches.

### The upstream pair

[78/xiaozhi-esp32](https://github.com/78/xiaozhi-esp32) supplies the
firmware and therefore the device half of everything: board support,
audio pipeline, wake word, display UI, and the protocol vinga
implements.
[xinnan-tech/xiaozhi-esp32-server](https://github.com/xinnan-tech/xiaozhi-esp32-server)
is the Python conversation server vinga starts from and reimplements
rather than extends. Both are MIT, both are credited in [the project
README](../README.md#credits), and the protocol they define is documented
in [`xiaozhi-notes.md`](xiaozhi-notes.md). They are the foundation, not
alternatives.

`esp_xiaozhi`, the ESP-IDF component packaging of that firmware, is the
route to a customised device without a fork, and is a spike scheduled for
a later version in [the v1
plan](plans/2026-08-02-samtal-server-v1.md#later-versions-not-planned-here).

### In the server pipeline

- [`pysilero-vad`](https://github.com/rhasspy/pysilero-vad) (MIT), voice
  activity detection, a core dependency. From the Rhasspy organisation.
- [`faster-whisper`](https://github.com/SYSTRAN/faster-whisper) (MIT),
  local speech recognition, an optional extra.
- [`piper1-gpl`](https://github.com/OHF-voice/piper1-gpl) (GPL-3.0),
  local speech synthesis, an optional extra and never a core dependency.
  Its license is why the container image has a `slim` variant.
- [PyAV](https://github.com/PyAV-Org/PyAV), Opus encode and decode in the
  server, a core dependency, and the other copyleft component in the
  image through its bundled FFmpeg.
- The [MCP](https://modelcontextprotocol.io) Python SDK, which carries
  tools in both directions: MCP servers the assistant may call, and the
  device's own controls exposed as tools.
- Cloud providers reached over their APIs rather than bundled:
  [Anthropic](https://www.anthropic.com/api) and
  [OpenAI](https://platform.openai.com) SDKs, any OpenAI-compatible
  endpoint including a local [Ollama](https://ollama.com), and ElevenLabs
  over plain HTTP.
- `edge-tts`, listed here for the opposite reason: it is GPL-3.0, vinga
  does not depend on it, and keeping speech synthesis engines pluggable
  is what makes that possible.

Model weights (Whisper models, Piper voices, Silero, and the ESP-SR wake
words on the device) download at deploy time under their own licenses and
are never committed or redistributed.

### In the tests

[`xiaozhi-sdk`](https://pypi.org/project/xiaozhi-sdk/) is a Python
implementation of the device side of the protocol, published on PyPI, and
vinga uses it as a device simulator to drive the server end to end
without a board on the desk (see the testing section of
[`vinga-server/README.md`](../vinga-server/README.md)). It is a
development dependency only. It brings `opuslib`, which is unmaintained
and compiles with an `is not 0` identity comparison, which is why
`pyproject.toml` filters that one `SyntaxWarning` in a test suite that
otherwise turns warnings into errors.

### On the device

ESP-SR supplies the wake word models, licensed by Espressif for use on
Espressif chips.

## Not yet surveyed

Named here so a later session does not have to rediscover them. No claims
are made about any of these until someone reads them properly.

- Willow, an ESP32-S3 voice assistant with its own inference server.
- ESPHome's voice assistant component, the other way to get audio off an
  ESP32 and into Home Assistant.
- LiveKit Agents and Pipecat, as realtime conversation frameworks rather
  than assistants. Pipecat's barge-in gating is already cited as prior
  art in
  [an ADR](adr/2026-08-05-replies-cancel-only-on-evidence-of-speech.md).
- The vendor realtime speech-to-speech APIs themselves (OpenAI Realtime,
  Gemini Live, ElevenLabs Conversational AI), which the ElatoAI entry
  above treats as one architecture and vinga would treat as one
  provider.
