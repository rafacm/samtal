# samtal-server

The Samtal conversation server (Python), based on
[xinnan-tech/xiaozhi-esp32-server](https://github.com/xinnan-tech/xiaozhi-esp32-server).

It implements the two endpoints a device needs:

- **HTTP OTA/config endpoint** (`/xiaozhi/ota/`): the device POSTs its
  identity and receives the WebSocket URL (and optionally firmware updates).
- **WebSocket endpoint** (`/xiaozhi/v1/`): the conversation channel,
  carrying Opus audio frames up, JSON control messages both ways, and Opus
  audio back.

Behind the WebSocket sits the conversation pipeline: VAD segments speech,
ASR transcribes it, the LLM streams a reply, and TTS speaks it back
sentence by sentence, every stage a pluggable provider chosen per agent.
Agents reach tools over MCP, both their own servers and the device's own
controls.

Those two paths, plus `/healthz`, are everything the server exposes: the
interactive API docs are turned off, and the WebSocket requires a device
token the OTA endpoint issued.

## Goals

- Python-only, no database required for the core loop
- Configurable providers:
  - **LLM**: Anthropic, any OpenAI-compatible endpoint (Ollama, LM Studio,
    gateways)
  - **ASR**: local (faster-whisper) or cloud (OpenAI, and anything
    speaking the same dialect)
  - **TTS**: pluggable engines as optional extras (Piper)
  - **MCP**: attach any MCP servers as tools for the assistant, alongside
    the device's own
- Distributed as a multi-arch container image, deployable on your own
  infrastructure

## Providers

Each pipeline stage is a named provider entry in the configuration, and
each agent picks one provider per stage. The v1 set:

| Stage | Type                | Runs               | Install                          |
| ----- | ------------------- | ------------------ | -------------------------------- |
| vad   | `silero`            | locally            | core (pysilero-vad)              |
| asr   | `faster_whisper`    | locally            | `uv sync --extra faster-whisper` |
| asr   | `openai`            | OpenAI or anywhere | core                             |
| llm   | `anthropic`         | Anthropic          | core                             |
| llm   | `openai_compatible` | anywhere           | core                             |
| tts   | `piper`             | locally            | `uv sync --extra piper`          |
| tts   | `elevenlabs`        | ElevenLabs         | core                             |
| tts   | `openai`            | OpenAI or anywhere | core                             |
| any   | `mock`              | in tests           | core (deterministic, keyless)    |

"Anywhere" is a `base_url`: those three types speak a dialect rather
than name a vendor, so each reaches a self-hosted server implementing
the same endpoint. That is what keeps a fully local pipeline available
through them, and it is why they cannot declare their own egress.

Model weights are never shipped: faster-whisper models and Piper voices
download at server startup into a local cache (`download_dir` on the
provider entry). A fully local, keyless pipeline is Silero +
faster-whisper + Ollama (through `openai_compatible`) + Piper, and
`server.local_only: true` makes the server refuse to boot anything
else (see Security below).

Cloud providers need no extra. They speak their APIs over HTTP, or
through an SDK the core install already carries for another stage, so
they are in every install and cost nothing to carry; what makes a
provider optional is weight or licensing, and a network client has
neither.

Licensing note: `piper-tts` (piper1-gpl) is GPL-3.0, which is why it is an
optional extra and never a core dependency of the MIT server. The same
applies to any future `edge-tts` provider.

### Choosing how it hears

The ASR stage has two types, and the trade between them is not the one
the voices have. Going to the cloud for a voice costs latency; going
to the cloud to listen mostly saves it, because a transcription is one
round trip against someone else's accelerator instead of a CPU decode
on yours. Median of five per utterance, one machine and one network,
so the columns are comparable:

| Utterance | `faster_whisper` small | `gpt-4o-mini-transcribe` | `gpt-4o-transcribe` | `whisper-1` |
| --- | --- | --- | --- | --- |
| "The kitchen light is now off." (1.8 s) | 1688 ms | 536 ms | 627 ms | 1076 ms |
| "Hello, I am your samtal assistant..." (3.6 s) | 1781 ms | 658 ms | 887 ms | 1177 ms |
| "Hej, jag är din samtalsassistent." (2.1 s) | 1743 ms | 545 ms | 607 ms | 1101 ms |

Read that against your own hardware before believing it: the local
column is an int8 CPU decode on a laptop, and a machine with a GPU
would change the answer. The cloud column would not move much, since
almost all of it is the round trip.

**The same measurement on a real device narrows the gap a long way.**
Taken from the server's own log on a Waveshare ESP32-S3, `heard` minus
the end of the utterance, so it is the whole stage and not just the
call:

| | on the board | on the desk |
| --- | --- | --- |
| `gpt-4o-mini-transcribe` | 642, 647, 825 ms | 536 to 658 ms |
| `faster_whisper` small | 964 ms (one sample) | 1688 to 1781 ms |

The cloud figures held; the local one did not, which says the desk
number for `faster_whisper` was measured under contention and flatters
the cloud. Treat the first table's local column as a worst case and
this one as the honest comparison, and measure your own either way.

Accuracy is the other half, and it separates them further, *provided
the language is pinned*. The caveat is not a footnote: see "Set
`language`" below, because unpinned on a real device the cloud engine
was the worse of the two. Synthesized speech under white noise,
standing in for a far-field microphone:

| Signal to noise | `faster_whisper` small | `gpt-4o-mini-transcribe` | `gpt-4o-transcribe` |
| --- | --- | --- | --- |
| clean | exact | exact | exact |
| 10 dB | Swedish already wrong | exact | exact |
| 5 dB | Swedish wrong | exact | exact |
| 0 dB | Swedish is a different sentence | exact | exact |

English survived everywhere; Swedish is where the local `small` model
gives up, turning "samtalsassistent" into "samhållssystem" at 10 dB
and the whole sentence into unrelated English at 0 dB. A larger local
model closes some of that, at a latency cost the table above already
shows there is no room for.

That table is synthesized speech, which is cleaner than a room. On the
device, with both engines pinned to Swedish, the cloud engine was still
the better of the two but neither was perfect:

| Said to the board | `gpt-4o-mini-transcribe` | `faster_whisper` small |
| --- | --- | --- |
| "Vad heter Sveriges huvudstad?" | exact | "Vad heter Sveriges Hubbetsstad?" |
| "Vad heter din samtalsassistent?" | "Vad hette ditt samtalsassistent?" | "Men hejter den samtalassistenten." |

What the local engine still wins: it is the only one that keeps the
audio on your host, and it is the only one that reports which language
it heard. It is also free per utterance, which a busy household
notices.

### OpenAI transcription

```yaml
providers:
  asr:
    ears:
      type: openai
      api_key_env: OPENAI_API_KEY
      prompt: samtal
```

Keys are named, never written, exactly as for the TTS types above.

| Option | Default | What it does |
| ------ | ------- | ------------ |
| `api_key_env` | required for OpenAI itself | Name of the variable holding the key |
| `model` | `gpt-4o-mini-transcribe` | `gpt-4o-transcribe` is the larger sibling; `whisper-1` is the same Whisper V2 you could run locally |
| `base_url` | `https://api.openai.com/v1` | Point it at any server implementing `/v1/audio/transcriptions` |
| `prompt` | unset | Words the engine would not otherwise guess: names, places, the assistant's own |
| `language` | unset | Spoken language (ISO 639-1). Set it for any non-English deployment: see below |
| `temperature` | unset | 0.0 to 1.0, the API's own default when unset |
| `timeout_s` | `30` | Seconds before a transcription is abandoned, and a real bound: retries are off |

**Set `prompt`.** An unfamiliar proper noun is the one thing this type
reliably gets wrong, and the prompt is what fixes it: under noise,
"samtal" came back as "sample" without it and as "Samtal" with it. It
fixes vocabulary, not language, so it cannot compensate for the setting
below: on the board, `prompt: samtal` still produced "samstal" until
`language` was pinned, and produced "samtalsassistent" exactly once it
was.

**Set `language` unless the household speaks English.** This is the
one setting a device checkpoint changed our mind about. On clean audio,
leaving it unset costs nothing: recognition is multilingual either way,
and detection happens inside the model rather than as a separate pass
you pay for. On a real board in a real room it is a different story.
Unpinned, Swedish came back as English-shaped nonsense:

| Said to the board | Unpinned | `language: sv` |
| --- | --- | --- |
| "Vad heter Sveriges huvudstad?" | "Hat hetas verigezogistad." | exact |
| "Vad heter din samtalsassistent?" | "Wat hat er dien samstal asynstind?" | "Vad hette ditt samtalsassistent?" |

The audio was not the problem; the language choice was. Far-field mic
audio through Opus gives detection much less to go on than a clean
file, and the model appears to fall back on English phonetics. Pinning
fixed it outright, and no `prompt` rescued it while unpinned.

Setting it is still a hint rather than a hard pin: a `gpt-4o` model
given Swedish audio and `language: en` answers in Swedish anyway, where
the local engine would have forced the wrong language and produced
nonsense. So a wrong value is fairly harmless, and it is leaving it
*empty* that costs you.

**No language is reported back.** `AsrResult`'s language fields stay
empty, so the `heard` log line carries no `language`, and there is no
`language_detect` option. The API returns a language only for
`whisper-1` asked for a format the other models do not support, as an
English name rather than an ISO code, and never a confidence. An empty
field beats a guess, and nothing is lost: the local engine's
`language_detect: once` exists to skip a detection pass that costs
seconds of CPU, and here detection is free.

**It does not stream, deliberately.** The stage hands over one whole
utterance and the LLM cannot start on half a sentence, so response
deltas would arrive before anything could use them. The TTS stage is
the opposite case and does stream.

**Very short audio is answered empty without a request.** OpenAI
refuses anything under 0.1 s, and the barge-in path is what would send
it: a snippet classified as speech mid-reply gets transcribed to
decide whether the interruption was real. That refusal would be logged
as a failure rather than the non-answer it is. The floor was measured
against OpenAI and applies only there. A compatible endpoint that
accepts shorter clips receives them, because suppressing one it would
have answered would drop a barge-in it could have confirmed.

`base_url` is the same door the `openai_compatible` LLM type and the
`openai` TTS type open, with the same consequences: the host rather
than the spelling decides whether an entry counts as OpenAI, a
`base_url` that is not a URL fails the boot, and the endpoint rather
than the type decides egress, so an entry under `server.local_only`
carries its own `egress: false`.

Only OpenAI's own host *requires* a key. A keyless self-hosted server
can leave `api_key_env` out, but a gateway or hosted endpoint that
authenticates still names its variable there and the key is sent, so
"compatible" does not mean "keyless".

**It sends the microphone audio wherever `base_url` points**, which by
default is OpenAI, and that is a stronger claim than the TTS types
make: what leaves is what was said in the room, not what the assistant
answered. See Security below for how `server.local_only` treats it.

### Choosing a voice

The three TTS types differ in the one thing a conversation actually
feels: how long the device stays silent before it starts speaking.
Measured on one machine in a single run, median of five rounds per
sentence, so the columns are comparable with each other:

| | Piper | ElevenLabs | OpenAI |
| --- | ----- | ---------- | ------ |
| "The kitchen light is now off." | 40 ms | 194 ms | 888 ms |
| "Hello, I am your samtal assistant..." | 79 ms | 188 ms | 764 ms |
| "Hej, jag är din samtalsassistent." | 54 ms | 194 ms | 818 ms |
| Runs | on your host | ElevenLabs | OpenAI |
| Needs | `--extra piper` | a key | a key |

Read down the columns, not across the rows. Piper is the only one
whose figure grows with sentence length, because it synthesizes a
whole sentence before yielding anything; both cloud types stream, so
their figure is flat and a longer sentence starts no later than a
short one. Past a long enough sentence Piper is the slower of the two
to start speaking, even though it is local.

**The number above is only the start of the reply, and it used to not
be the whole cost.** A reply is spoken sentence by sentence, and each
sentence used to be synthesized only once the previous one had
finished playing, so the same wait fell at every sentence boundary
too. On a three-sentence reply:

| | Gap at each sentence boundary | Total dead air mid-reply |
| --- | --- | --- |
| Piper | 40 to 80 ms | negligible |
| ElevenLabs | 129 to 139 ms | 268 ms |
| OpenAI | 478 to 884 ms | 1362 ms |

Around 130 ms passed unnoticed. Around 600 ms did not: it was audible
as the voice stuttering every few seconds through a long reply, and
worse than a plain pause, because the frame pacer's schedule is
absolute from the reply's first frame, so the frames after a stall
burst out to catch up. The device got a dropout followed by a flood.

The server now synthesizes the next sentence while the current one is
still playing, so that latency is spent against playback that is
already happening. The same replies, measured again: every
boundary is one frame, 60 ms, which is the cadence rather than a gap.
The table above is what it cost before, kept because it is what the
start-of-reply figure has to be weighed against if a provider ever
becomes slower than a sentence is long.

This leaves the start of the reply as the only latency a listener
meets, which is a one-time delay a person tolerates rather than a
defect they notice every few seconds. It is why the recommendation
below no longer bounds a cloud provider on latency alone.

So: **Piper** if it must stay on your host or cost nothing per
character, **ElevenLabs** for the best voice per millisecond, and
**OpenAI** when the deployment is already on OpenAI and one key is
worth more to you than 700 ms at the start of a reply. Reply length no
longer picks between them, which it did while every sentence boundary
cost what the first one did. Each type's own section below has its
options and the details behind its number.

These are one machine on one day from one network, not a benchmark.
Your ratios should hold; your absolute numbers will not. (The
ElevenLabs section quotes ~130 ms from its own earlier measurement,
with a different voice on a different day, which is the size of the
run-to-run variation to expect.)

### ElevenLabs

The reason to reach for the `elevenlabs` TTS type is that it sounds
markedly better than Piper. It needs two things: a key, and a voice
id.

```yaml
providers:
  tts:
    eleven:
      type: elevenlabs
      voice_id: PUT_YOUR_VOICE_ID_HERE
      api_key_env: ELEVENLABS_API_KEY
```

The key is named, never written. `api_key_env` gives the name of an
environment variable and the server reads it at startup, failing the
boot if it is unset rather than failing every conversation later. A
`.env` file next to the config works, since the server loads one.

The voice id is the id, not the display name, and it is
account-specific even for the stock voices, so an id copied from
someone else's configuration will usually 404. Pick one in the
ElevenLabs app, or list your own:

```bash
curl -s -H "xi-api-key: $ELEVENLABS_API_KEY" \
  https://api.elevenlabs.io/v1/voices \
  | jq -r '.voices[] | "\(.voice_id)  \(.name)"'
```

| Option | Default | What it does |
| ------ | ------- | ------------ |
| `voice_id` | required | Which voice speaks |
| `api_key_env` | required | Name of the variable holding the key |
| `model` | `eleven_flash_v2_5` | Flash is the low-latency model (~75 ms to first byte, 32 languages including Swedish). `eleven_multilingual_v2` sounds better and answers slower |
| `output_format` | `pcm_24000` | Must be one of the `pcm_<rate>` formats. The default matches the rate devices are spoken at, so nothing is resampled. `pcm_44100` and up need a paid tier |
| `language_code` | unset | ISO 639-1, pins the spoken language instead of letting the model infer it from the text |
| `voice_settings` | unset | `stability`, `similarity_boost`, `style`, `speed`, `use_speaker_boost`, passed to the API as given |
| `timeout_s` | `30` | Seconds before a synthesis request is abandoned |

Reference for all of it: the [streaming
endpoint](https://elevenlabs.io/docs/api-reference/text-to-speech/stream),
the [model list](https://elevenlabs.io/docs/overview/models), the
[voice listing
endpoint](https://elevenlabs.io/docs/api-reference/voices/search), and
what the [voice
settings](https://elevenlabs.io/docs/overview/capabilities/text-to-speech/best-practices)
do to a voice.

**What it costs in latency.** First audio at about 130 to 190 ms
whatever the sentence, which is the fastest of the two cloud types by
a wide margin; see Choosing a voice above for the comparison and what
the numbers mean. An idle conversation pays nothing extra to resume.

**It sends your replies to ElevenLabs**, which is what the reply text
is: the API is billed by character. The type is marked as egress
accordingly, so `server.local_only: true` refuses to boot it (see
Security below). Nothing else in the pipeline moves: VAD, ASR and the
LLM stay wherever you configured them.

### OpenAI

The `openai` TTS type is the one to reach for if the deployment is
already on OpenAI: the same key serves the LLM stage, and the voices
are the stock ones, so there is nothing to pick out of a library.

```yaml
providers:
  tts:
    openai_voice:
      type: openai
      voice: alloy
      api_key_env: OPENAI_API_KEY
```

Keys are named, never written, exactly as for ElevenLabs above.

Voices are shared across every account (`alloy`, `ash`, `ballad`,
`coral`, `echo`, `sage`, `shimmer`, `verse`, `marin`, `cedar`), so a
voice copied from someone else's configuration works. Hear them in the
[voice gallery](https://www.openai.fm/).

| Option | Default | What it does |
| ------ | ------- | ------------ |
| `voice` | required | Which voice speaks |
| `api_key_env` | required for OpenAI itself | Name of the variable holding the key |
| `model` | `gpt-4o-mini-tts` | The current speech model, steered in prose, and the fastest of the three to start speaking. `tts-1` and `tts-1-hd` are the older pair |
| `base_url` | `https://api.openai.com/v1` | Point it at any server implementing `/v1/audio/speech` |
| `instructions` | unset | How to speak, in prose ("Speak slowly and warmly"). Read by the `gpt-4o` models only |
| `speed` | unset | A multiplier from 0.25 to 4.0. Read by `tts-1` and `tts-1-hd` only |
| `timeout_s` | `30` | Seconds before a synthesis request is abandoned, and a real bound: retries are off |

`base_url` is the same door the `openai_compatible` LLM type opens.
Several self-hosted speech servers implement this endpoint, so a fully
local pipeline stays available through the same dialect, and a keyless
one of those can leave `api_key_env` out; an endpoint that
authenticates still names its variable there, since only OpenAI's own
host makes a key mandatory. It is also what decides whether this type sends
anything off your host, which is why it cannot declare its own egress:
under `server.local_only` the entry carries its own `egress: false` to
assert the endpoint is local, exactly as `openai_compatible` does.

Whether an entry counts as OpenAI is decided by the host, so every
spelling of it (a trailing slash, an explicit port, a different case)
keeps the same startup checks. A `base_url` that is not a URL at all
fails the boot rather than the first synthesis.

Retries are off. The SDK would otherwise attempt a failed request
three times, which inside the serial sentence loop means the device
sits silent for three timeouts plus backoff. A sentence that fails
should fail now and let the conversation move on.

The last two are the one place this type refuses a configuration the
API would accept. Each OpenAI model reads one of them and silently
ignores the other, so naming the wrong one for the model fails the
boot rather than becoming a knob that never takes effect.

That check applies only when `base_url` names OpenAI's host, because
it is a fact about OpenAI's models rather than about the dialect. A compatible
server may name a model `gpt-4o-anything` and read `speed`, or read
`instructions` on a model named nothing like OpenAI's, and its `speed`
need not stop at 4.0. Both knobs are passed through to such an
endpoint unexamined, so it answers for itself.

There is no audio format option, unlike ElevenLabs: the API's `pcm`
format is fixed at 24 kHz, which is the rate devices are spoken at, so
nothing is resampled and there is nothing to choose. Reference for the
rest: the [speech
endpoint](https://platform.openai.com/docs/api-reference/audio/createSpeech)
and the [text-to-speech
guide](https://platform.openai.com/docs/guides/text-to-speech).

**What it costs in latency, and it is the one real drawback.** First
audio arrives at about 820 to 900 ms, roughly +700 ms on ElevenLabs
and +800 ms on Piper: a pause a person notices at the start of every
reply, and again at every sentence boundary within it, which is the
part that makes long replies stutter. See Choosing a voice above for
the comparison in full.

Until that lands, this type suits agents that answer in a sentence or
two. On an agent that tells stories, the gaps are the thing you will
hear.

Which model you pick matters more here than the option table suggests,
and the default is the fastest of the three by some margin. Worth
stating plainly, because it contradicts how the older pair is usually
described:

| Model | Short sentence | Longer sentence |
| ----- | -------------- | --------------- |
| `gpt-4o-mini-tts` | 908 ms | 820 ms |
| `tts-1` | 1549 ms | 1413 ms |
| `tts-1-hd` | 1974 ms | 1861 ms |

Reach for this type because the deployment is already on OpenAI and
one key is worth something. If what you want is the best voice per
millisecond, ElevenLabs is the better buy.

**It sends your replies wherever `base_url` points**, which by default
is OpenAI. See Security below for how `server.local_only` treats it.

## Tools

Beyond speaking, an agent reaches three kinds of tool, merged into one
list the model sees and told apart by the shape of their names.

**MCP servers** are named entries under `mcp_servers`, the way providers
are, and an agent references them through an `mcp` list that
`agent_defaults` can supply. Naming a list replaces the inherited one
rather than extending it, so `mcp: []` is how an agent opts out of tools
its siblings have. Each server's tools are offered under its entry name
(`home__turn_on_light`), which is why an entry name has to be a plain
`[A-Za-z0-9_-]+` name and cannot be `self`, `switch_agent`, or
`remember`. Both transports the specification defines are supported:

```yaml
mcp_servers:
  home:
    transport: stdio
    command: mcp-proxy
    args: ["http://homeassistant.local:8123/mcp_server/sse"]
    env:
      API_ACCESS_TOKEN: $HOME_ASSISTANT_TOKEN
  weather:
    transport: streamable_http
    url: http://localhost:8000/mcp
    headers:
      Authorization: $WEATHER_TOKEN
    tool_timeout_s: 15
```

Secrets follow the same rule as everywhere else: a value of `$NAME` is
read from that environment variable at startup, and any secret-looking
key (`token`, `api_key`, `authorization`, ...) must use that form. An
unset variable fails the boot, as does an unknown reference or a
reserved entry name. A server that is merely unreachable does not: it
logs a warning, contributes no tools, and reconnects in the background
when a session that would use it opens.

**The device's own tools** need no configuration. A board whose hello
advertises `features.mcp` is asked for its tools over the same socket
the audio runs on, and they arrive under their firmware names with the
dots replaced (`self_audio_speaker_set_volume`), because both LLM APIs
restrict tool names to `[A-Za-z0-9_-]`.

**Builtins** are `switch_agent`, offered when the device is bound to
more than one agent, and `remember`, offered when memory is configured.
A successful `switch_agent` ends the current agent's reply: the new
agent greets the user in its own prompt and its own voice, with the
conversation so far carried over. `remember` appends one fact to the
agent's memory file:

```yaml
memory:
  dir: /var/lib/samtal/memory
```

One file per agent, created on first write and injected into that
agent's system prompt on every reply, capped at 8 KiB or 200 lines with
the oldest dropped first. Memory is keyed by agent and not by device: a
persona is one entity across rooms. Leave the section out and there is
no `remember` tool and no injection.

A tool that fails, times out, or does not exist comes back to the model
as an error result rather than ending the reply, so the assistant says
what went wrong in its own voice and the user's language. The device
hears silence while a tool runs, bounded by `tool_timeout_s` (15 seconds
by default).

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
uv sync --extra faster-whisper --extra piper  # add the local ASR/TTS engines
uv run samtal-server                # run the server
uv run pytest tests/unit -q         # unit tests
uv run pytest tests/integration -q  # integration tests
uv run ruff check .                 # lint
```

The test lanes run the whole pipeline on the built-in mock providers, so
they need no keys, no model downloads, and no network.

### The local lane: a real conversation

CI never touches real engines. To check the overall work with them, an
opt-in third lane holds one real conversation end to end: it starts a
real server on the fully local pipeline (Silero, faster-whisper, Ollama,
Piper), speaks a Piper-synthesized question through the device simulator,
and asserts the transcript and a coherent spoken reply.

```bash
uv sync --extra faster-whisper --extra piper
SAMTAL_LOCAL_LANE=1 uv run pytest tests/local -q
```

The run ends with a summary of the conversation it held, so a pass shows
its work rather than a green dot:

```
=========================== local lane conversation ============================
pipeline: silero + faster-whisper small + qwen3:8b + en_US-lessac-medium
question: "What is the capital of Sweden?" (1.6 s of audio)
heard   : "What is the capital of Sweden?" (+1.2 s)
reply   : "The capital of Sweden is Stockholm." (first sentence +3.8 s, 2.0 s of audio)
```

A pre-flight check runs first and fails with the command that fixes
whatever is missing (extras not installed, no Ollama answering, no usable
model). By default it talks to Ollama at `localhost:11434` and prefers
`qwen3:8b`, falling back to the first installed model;
`SAMTAL_LOCAL_OLLAMA` and `SAMTAL_LOCAL_LLM_MODEL` override both. The
first run downloads the whisper model and Piper voice at server startup
and can take a few minutes; later runs finish in seconds. Without
`SAMTAL_LOCAL_LANE=1` the lane skips, so a bare `pytest` stays safe.

### The smoke lane: a conversation with a container

A fourth lane runs nothing itself. It points at a server that is already
up and holds one whole conversation with it: healthz, an OTA check whose
token it verifies, and a full utterance-to-audio exchange through the
device simulator. CI runs it against the image it just built, which is
what turns "`docker run` with one mounted YAML serves a conversation"
into something checked rather than remembered.

```bash
docker build -t samtal-server:local .
docker run -d --name samtal-smoke -p 8003:8003 \
  -e SAMTAL_AUTH_SECRET=smoke-secret \
  -v "$PWD/tests/smoke/config.yaml:/config/config.yaml:ro" \
  samtal-server:local

SAMTAL_SMOKE_OTA_URL=http://127.0.0.1:8003/xiaozhi/ota/ \
SAMTAL_AUTH_SECRET=smoke-secret \
  uv run pytest tests/smoke -v
```

The secret has to match the one the server under test was started with:
the lane verifies the token it is issued, and that needs the signing key.
It skips without `SAMTAL_SMOKE_OTA_URL`, so a bare `pytest` stays safe,
and it works against any reachable server, not only a container.

## Configuration

Configuration is handled by
[pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/).
The server reads one YAML file, passed as `--config /path/to/config.yaml` or
via the `SAMTAL_CONFIG` environment variable; with neither set, defaults
apply. [`config.example.yaml`](config.example.yaml) documents every key,
and [`config.deploy.example.yaml`](config.deploy.example.yaml) is a
ready-to-adapt profile for the container image behind a proxy on a small
CPU quota, holding values validated by latency measurements from a live
deployment. The reference file covers:
`server` (host/port), named `providers` per stage (`llm`, `asr`, `tts`,
`vad`), named `mcp_servers`, `agent_defaults` holding what every agent
uses unless it says otherwise, `agents` combining a prompt with provider
and MCP references, `devices` binding MAC addresses to agents,
`default_agent` for unknown devices, and an optional `memory` section.

Since a voice is a `tts` provider entry, two agents that should sound
different reference two entries, and a typical agent is a prompt plus a
voice. `agent_defaults` takes no prompt: a prompt is what makes an agent
that agent. A device is bound to one agent or to a list of them; with a
list, the first entry is the agent a conversation starts on, and the
rest are the ones `switch_agent` can reach.

Every key can be overridden with a `SAMTAL_`-prefixed environment variable,
nested keys joined with `__`: `SAMTAL_SERVER__PORT=9000`,
`SAMTAL_DEFAULT_AGENT=assistant`. Environment variables beat the YAML file,
and a `.env` file in the directory the server is started from is read at
startup (real environment variables beat `.env` too). This layering matches
container deployments: the YAML arrives as a mounted file, overrides and
secrets as environment variables.

Secrets never live in the file: a provider names the environment variable
that holds its key (for example `api_key_env: ANTHROPIC_API_KEY`), and an
MCP server writes `$NAME` where the secret goes. Instance
configs stay out of the repository; `*.local.yaml` and `.env` are gitignored
for local experiments.

## Security

**Devices authenticate, by default.** The OTA endpoint issues each device
a token, the firmware persists it to NVS, and the WebSocket handshake
checks it before accepting the upgrade. A connection with no token, a
forged one, an expired one, or one issued for a different device is
refused with HTTP 403 on the upgrade, which stock firmware handles by
retrying and picking up a fresh token at its next OTA check.

The token is upstream's scheme, `sig.ts`, where `sig` is HMAC-SHA256 over
`client_id|device_id|ts`. It is stateless: a restart does not lock out
every device holding a persisted token, and two replicas sharing a secret
accept each other's tokens.

The secret comes from the environment, never from the config file:

```bash
SAMTAL_AUTH_SECRET=$(openssl rand -hex 32)
```

Generate it once and keep it. Changing the secret invalidates the token
every device has stored, and a device only refreshes at its next OTA
check, which it makes on boot: until then it is refused at the handshake
and plays an error tone with nothing on its screen. That is the intended
behaviour of a rotated secret, but it is worth doing deliberately rather
than by regenerating one inside a `docker run` you repeat.

**A missing secret fails the boot.** Authentication is enabled by default,
and starting with it enabled and no secret refuses to come up, naming the
variable and the fix. A deployment that forgot its secret must not look
exactly like a working one. For a trial on a network you trust, opting
out is one deliberate flag:

```yaml
server:
  auth:
    enabled: false
```

or `SAMTAL_SERVER__AUTH__ENABLED=false` in the environment.

**Who gets a token is the allowlist.** A token is only issued to a device
the configuration resolves to at least one agent. Omit `default_agent`
and the `devices` map becomes an allowlist: an unknown MAC is issued
nothing and turned away. There is no second list to keep in sync.

**The OTA endpoint is the token issuer, so it cannot require a token.**
What protects it instead is stingy issuance and a path you choose. When
the server is reachable from outside your network, hide it behind a long
random segment and write that whole URL into the device's NVS:

```yaml
server:
  ota_path: /xiaozhi/ota/8f3a9c2b1d4e5f60/   # openssl rand -hex 8
```

The WebSocket path never moves: the token is what protects it.

**Nothing else is exposed.** `/xiaozhi/ota/` (or wherever you put it),
`/xiaozhi/v1/`, and `/healthz`. FastAPI's `/docs`, `/redoc`, and
`/openapi.json` are turned off.

**Fully local is checked, not hoped for.** Every provider type declares
whether it sends session data (audio, transcripts, replies) off the
host, and with `server.local_only: true` the server refuses to boot any
provider that does, naming the stage and provider. The local engines
(Silero, faster-whisper, Piper) pass; an `anthropic` or `elevenlabs`
entry fails. The three `base_url` types, `openai_compatible` for the
LLM stage and `openai` for both ASR and TTS, can each point at
localhost or at a cloud vendor, so under `local_only` they must carry
your own declaration:

```yaml
providers:
  llm:
    local:
      type: openai_compatible
      base_url: http://localhost:11434/v1
      model: qwen3:8b
      # Your assertion that this endpoint stays on this host.
      egress: false
```

MCP servers sit inside the same boundary, because tool arguments carry
conversation-derived data. No transport can know where they end up (a
stdio command may proxy anywhere, a URL may name localhost), so under
`local_only` every MCP server an agent references must carry the same
`egress: false` declaration, asserting that whatever its command or URL
reaches stays on your own network.

The checks run at boot, never at request time: a local_only server that
starts is a local_only server, and a config edit that would break the
promise stops the server from coming up instead of quietly shipping
audio to a vendor.

## Listening and barge-in

The firmware decides how it listens and the server follows. In `auto`
mode the device shuts its microphone off while a reply plays and sends a
fresh `listen start` afterwards. In `realtime` mode, which it picks when
its echo cancellation is on, it streams continuously and asks only once,
so the session here never stops listening: an utterance that ends while
a reply is playing cancels that reply and is answered instead. Talking
over the assistant stops it, which is what barge-in means.

```yaml
server:
  barge_in: true               # speech during a reply interrupts it
  barge_in_min_speech_ms: 500  # least classified speech that may interrupt
  barge_in_refractory_ms: 1000 # interruptions ignored after playback starts
```

Turn it off for a board whose echo cancellation leaks the speaker back
into the microphone, typically a single-mic board, where a reply would
otherwise interrupt itself. Conversations stay multi-turn with it off;
only the interrupting goes. What says a board wants it: replies that
answer nothing at all, arriving just after the previous reply finished
speaking.

An interruption the endpointer hears is gated before it may cancel: a
reply is only cancelled on evidence of user speech. Speech shorter than
`barge_in_min_speech_ms` is a noise blip and never interrupts; inside
`barge_in_refractory_ms` of the reply's first audio frame, nothing
does, since what the microphone hears then is as likely the playback
onset as the user. Past both, the reply pauses while ASR transcribes
the interruption, and only a non-empty transcript cancels; an empty
one resumes the reply where it stopped, about one ASR pass later. An
interruption landing while the reply is still transcribing merges with
what it interrupted instead, so one reply answers the whole sentence.
Every one of these decisions is a structured log event, which is what
the thresholds are tuned from. A manual `listen stop` mid-reply is the
user holding the button and speaking, so it cancels unconditionally.

## Limits

Three numbers bound what one server holds, and none is visible in normal
use: a device refused a slot, or closed by either time bound, reconnects
on its next wake word.

```yaml
server:
  limits:
    # concurrent conversations
    max_sessions: 8
    # one session's maximum life
    max_session_s: 3600
    # how long a realtime session may go without conversing
    idle_timeout_s: 120
  drain_s: 20             # how long a shutdown waits for replies to finish
```

`idle_timeout_s` is the one users actually meet. A realtime device asks
to listen once and then streams its mic for the rest of the connection,
and nothing in the firmware ever closes that channel, so walking away
mid-conversation used to leave a mic running until the hour was up. The
clock counts from the end of the last utterance or the end of the last
reply, whichever is later; arriving audio does not reset it, because a
realtime session streams silence too. Two minutes by default: long
enough to think, read something out, or answer the door.

It applies to realtime sessions only. An auto-mode device stops
listening after each reply and re-arms per turn, so it is not streaming
a room to anybody, and `max_session_s` is its bound. There is no off
switch; a deployment that wants none sets `idle_timeout_s` near
`max_session_s`.

`max_sessions` is a count with no queue behind it, because a
conversation waiting in line is worse than one that never started.

**Shutting down drains.** On SIGTERM the server stops admitting sessions,
lets every reply in flight finish speaking, and closes those sockets with
1001, all inside `drain_s`. A second signal forces the exit. Give
`docker stop` a `-t` above `drain_s`; its default is ten seconds.

`drain_s` is the whole budget a reply gets, so raise it if your replies
are long: a spoken answer is paced at the frame rate, so thirty seconds
of speech takes thirty seconds to deliver. When a reply outlasts the
budget its socket is still closed politely, but the drain logs
`drain_incomplete` with `cut_mid_reply`, which is the signal that
`drain_s` is too short for the replies this server gives.

## Logging

Two formats, one handler:

```yaml
server:
  log_format: text   # or json, which is the container image's default
  log_level: INFO
```

Every conversation event is logged as a human sentence and, in `json`
mode, as a line of structured fields. Each carries `event`, `session`,
and `device`, plus its own:

| `event`            | when                            | fields                             |
| ------------------ | ------------------------------- | ---------------------------------- |
| `ota_check`        | a device checks in (no session) | `client`, `board`, `firmware`, `agents` |
| `session_open`     | a conversation starts           | `client`, `agent`, `agents`, `protocol`, `revision` |
| `heard`            | an utterance is transcribed     | `agent`, `text`, `duration_s`, plus `language` and `language_confidence` when the engine detected |
| `speaking_started` | the reply's first audio frame goes out | `agent`                     |
| `replied`          | a reply finishes                | `agent`, `text`                    |
| `agent_said`       | one agent's part of a reply     | `agent`, `text`                    |
| `handover`         | `switch_agent` succeeds         | `from_agent`, `to_agent`           |
| `barge_in`         | speech cuts a reply short       | `speech_ms`, plus `speaking_ms` when the reply had started speaking |
| `barge_in_suppressed` | an interruption is dropped and the reply lives | `reason` (`min_speech`, `refractory`, `no_transcript`), `speech_ms` |
| `barge_in_merged`  | an interruption merges with the utterance the reply was transcribing | `speech_ms` |
| `tool_call`        | a tool returns                  | `agent`, `tool`, `duration_ms`, `is_error` |
| `session_limit`    | the duration cap fires          | `duration_s`                       |
| `session_idle`     | the idle timeout hangs up on a realtime session | `idle_s`, `duration_s`     |
| `session_closed`   | a conversation ends             | `duration_s`                       |
| `session_rejected` | a device is turned away         | `reason`                           |
| `auth_rejected`    | a handshake is refused          | `reason`                           |
| `drain_started`    | a shutdown begins draining      | `sessions`, `timeout_s`            |
| `drain_finished`   | every reply finished speaking   | `sessions`                         |
| `drain_incomplete` | a reply was cut, or a session hung | `cut_mid_reply`, `unfinished`   |

Retained JSON logs are the conversation store until v3 brings a real one:
filter on `event` in `heard`, `replied`, `agent_said` and group by
`session`, and you have the transcript. Tokens are never logged, at any
level.

## Capturing a session

**This records room audio to disk.** It is off by default and off until
`enabled` says otherwise, and a warning at startup plus one line per
recorded session say when it is on. Turn it off again once the
recording has been taken.

```yaml
server:
  capture:
    enabled: false
    dir: /data/captures
    # stop capturing a session after this long
    max_session_s: 900
    # budget for the directory, oldest captures pruned first
    max_total_mb: 2000
    # refuse to start a capture below this much free space
    min_free_mb: 1000
```

The flag is the switch, rather than the presence of the section, so
turning capture off again does not mean deleting the directory and the
budgets along with it: the field workflow is to record, then stop, and
the tuning is worth keeping across that. `dir` is required even while
disabled, so switching on is one word rather than one word and
remembering where it writes. A section that is present but off says so
once at startup, because a configured capture that records nothing is
otherwise a silence to debug.

Because it is one flag, the env layer can do the flip on its own:
`SAMTAL_SERVER__CAPTURE__ENABLED=true` turns it on for one run without
editing the config the deployment mounts, and dropping the variable
turns it off again. That is usually the least disruptive way to take a
field recording.

It exists because acoustic problems cannot be reproduced in any test
lane. The unit lane feeds synthetic frames and the integration lane
drives a simulator, and both bypass the microphone, the board's echo
cancellation, and the room. Whether a reply interrupts itself turns on
how much of the assistant's own voice survives the board's cancellation
and reaches the endpointer, and no test can tell you that number.

Three files per session, sharing one timeline:

| File | What it holds |
| --- | --- |
| `<session>.wav` | Stereo 16 kHz s16le. Channel 0 is the microphone as decoded, channel 1 is what was paced out to the speaker. |
| `<session>.jsonl` | Every structured event, plus a `t_ms` offset into the audio, plus dropped frames per second and the endpointer's opinion per frame. |
| `<session>.json` | What the capture was made against: server revision, the firmware the device reported, the resolved providers verbatim, and the barge-in thresholds. |

Stereo rather than two files is the whole point: sample N in both
channels is the same instant, so echo leakage is a measurement (cross
correlate the channels and read off gain and delay) rather than a
guess, and the overlap is directly audible in any audio editor. A
channel that goes quiet is filled with silence rather than compressed,
so nothing slides against the events.

The microphone is captured before the session's own guards, so the
frames a configuration discards (not listening, or `barge_in: false`
during a reply) are in the file anyway. Those are the frames that
explain a misfire.

Storage is 64 kB/s, so a fifteen minute session is about 58 MB and the
2000 MB budget is around nine hours. Both bounds matter: agent memory
and the model caches share the volume and grow underneath the budget,
so capture declines to start and says why rather than being the thing
that fills the disk.

A capture cut off by a restart stays readable. The WAV header carries
byte counts that are only patched on a clean close, so a truncated file
claims zero length, but everything after the 44 byte header is raw
interleaved PCM and the manifest's `complete: false` says the length has
to come from the file size. Both files are flushed as they are written,
so what is lost is at most the last fraction of a second.

In the field: turn it on, hold sessions in the conditions that actually
break things, and say a marker phrase aloud when something goes wrong.
It lands in a `heard` transcript and points at the interesting twenty
seconds instead of ten minutes of scrubbing. Copy the three files off
after each session; a field recording is not repeatable.

## Which build is running

`version` is the package version and has read `0.1.0` since the package
skeleton. `revision` is which build of it, and it is the field that
distinguishes one deploy from another.

```console
$ curl -s localhost:8003/healthz
{"status":"ok","version":"0.1.0","revision":"a1b2c3d"}
```

It also rides every `session_open` event, which is the widest payoff for
one field: the JSON logs already ship to a collector, so every session is
attributable to a build rather than only the ones somebody thought to
investigate. Two field recordings that behaved differently are otherwise
indistinguishable from one code change and two different rooms. The OTA
reply carries it too, under `server`, which is the one place a device is
told what it is about to talk to.

The value is resolved once at startup, in this order:

1. `SAMTAL_REVISION`. The published image bakes in the commit its tags
   are computed from, so `/healthz` and the image's `sha-` tag agree.
2. `git describe --always --dirty`, which covers running from a working
   tree. A tree with uncommitted changes reports `-dirty`, because a
   build running code that is not any commit is exactly when knowing
   matters.
3. `unknown`. An image built with no build argument runs and says it does
   not know; it never fails to start over it.

Building an image yourself:

```console
docker build --build-arg SAMTAL_REVISION=$(git rev-parse HEAD) -t samtal-server .
```

## Running in a container

The default image carries both local engines, so one `docker run` with
one mounted YAML serves a conversation:

```bash
docker run -d --name samtal \
  -p 8003:8003 \
  -e SAMTAL_AUTH_SECRET \
  -v /path/to/config.yaml:/config/config.yaml:ro \
  -v samtal-data:/data \
  ghcr.io/rafacm/samtal-server:latest
```

- `/config/config.yaml` is where `SAMTAL_CONFIG` points. Mount it
  read-only; override any key with a `SAMTAL_`-prefixed environment
  variable.
- `/data` is the volume every engine caches into (`HOME` points there):
  whisper models and Piper voices download at first start and survive a
  new image. Model weights are never baked in.
- Logs default to `json` in the image, which is the only default that
  differs from running it directly. Override with
  `SAMTAL_SERVER__LOG_FORMAT=text`.
- The healthcheck assumes the default port; change `server.port` and
  override `--health-cmd` too.
- A read-only root filesystem works: add `--read-only --tmpfs /tmp` and
  keep the two mounts.
- Stop it with `docker stop -t 30 samtal`, above `drain_s`, so
  conversations in flight finish their sentence.

Behind a TLS-terminating proxy, either set `server.websocket_url`
explicitly or pass the proxy's address in `FORWARDED_ALLOW_IPS`, which
uvicorn honours from the environment.

### Choosing an image

Two variants are published, built from one Dockerfile so they cannot
drift. They are the same server; the only difference is which optional
extras are installed.

| Variant | Tags | Carries | Use it when |
| --- | --- | --- | --- |
| default | `latest`, `2026-08-03-1200`, `sha-3f9362a` | both local engines | any config naming `faster_whisper` or `piper`, and anything fully local |
| slim | `slim`, `2026-08-03-1200-slim`, `sha-3f9362a-slim` | neither | ASR and TTS both name external providers |

The default variant is the unsuffixed one, following the convention
that an unqualified tag is the batteries-included image (as in
`python:3.12` against `python:3.12-slim`). Nothing about `latest`
changed when slim arrived.

Slim is 494 MB against the default's 883 MB, a saving of 389 MB, and it
contains no GPL component. The saving is mostly not piper: `faster-whisper`
brings its own inference stack, which is why the reduction is much larger
than the size of the engines themselves.

`silero` VAD is in both. It is a core dependency rather than an optional
extra, it is light, and it runs on every audio frame whichever ASR
provider is configured, so a slim deployment still segments speech
locally.

A slim image given a config that names a local engine refuses to start,
naming the extra it lacks:

```
providers.asr.whisper: type "faster_whisper" needs the faster-whisper
extra; install it with: uv sync --extra faster-whisper
```

That message is written for a source checkout. In a container the answer
is not to install anything but to pull the default variant instead.

Both variants are published for amd64 and arm64, and each has passed the
unit, integration, and smoke lanes: the same whole-conversation smoke
test runs against both.

The moving tag is the only one that moves, `latest` for the default
variant and `slim` for slim, so it is the tag to pull when trying the
server and the wrong one to deploy from. The dated and SHA tags are
never reused: several merges can land on one day, and each gets its own
timestamp, so a rollback names the build it wants.

**Pair the two variants by their SHA tag, not their dated one.** They
are built by separate jobs that finish minutes apart, so one commit can
produce `2026-08-06-1048` and `2026-08-06-1047-slim`. The dated tag is
honest about when each image was built; `sha-<short>` is the one that
says which commit, and it matches across both.

The default image contains `piper-tts` (GPL-3.0) alongside the MIT
server. That is aggregation, not a derived work; the slim variant
contains no GPL component at all. See
[`../THIRD_PARTY_LICENSES.md`](../THIRD_PARTY_LICENSES.md).

## Pointing a device at the server

A device running stock xiaozhi firmware knows only its OTA URL, held in NVS
(namespace `wifi`, key `ota_url`); see
[`../docs/xiaozhi-notes.md`](../docs/xiaozhi-notes.md) for how to write it.
Point it at `http://<server-host>:8003/xiaozhi/ota/` and everything else
reaches the device from the reply: the WebSocket URL, its token and protocol
version, and the wall clock.

By default the WebSocket URL is derived from the address the device reached
the OTA endpoint on, so a LAN deployment needs no extra configuration. Set
`server.websocket_url` when the server sits behind a proxy or a name the
request headers do not carry.

Opening `http://<server-host>:8003/xiaozhi/ota/` in a browser reports where
devices are being sent, which is the quickest way to check a deployment.

samtal-server serves no firmware images: the reply always tells the device it
is up to date, and never asks it to activate.

## Transports

**WebSocket only.** The device speaks Opus over one WebSocket, and that is
the only transport samtal-server implements or plans for v1.

Upstream supports a second one, **MQTT plus UDP**: the OTA reply carries an
`mqtt` section instead of a `websocket` one, control messages go over MQTT
and audio over a separate UDP stream. samtal-server never sends an `mqtt`
section, so devices always take the WebSocket path. Supporting it later is
additive and needs no change to what exists: the OTA endpoint would choose
which section to send per device.

**WebRTC is not an upstream transport.** The only WebRTC reference upstream
is the WebRTC/NSNet noise-suppression algorithm in the device's audio front
end (and it ships disabled). A WebRTC transport would be new work on both
sides, not adoption of something the firmware already speaks.

## Ports and topology

Both endpoints share one port (`server.port`, default 8003), because
samtal-server is a single ASGI app. Upstream splits them across two (HTTP
8003, WebSocket 8000).

The advertised WebSocket URL is deliberately independent of the listening
topology, which is what keeps this from being a one-way door: whatever the
process listens on, `server.websocket_url` decides what devices are told to
connect to.

What one port buys:

- A LAN deployment needs no configuration at all. The WebSocket URL is
  derived from the address the device reached the OTA endpoint on. With two
  ports that is impossible, since the request tells you the HTTP port and not
  the other one.
- One firewall rule, one container port, one certificate, one route.

What it costs:

- No separation at the network layer. Exposing one endpoint and not the
  other, or giving them different idle timeouts, has to be done by path
  rather than by port. This matters in practice: a WebSocket carrying a
  conversation needs a long idle timeout, an OTA check wants a short one.
- No independent scaling or lifecycle. WebSocket connections are long-lived
  and stateful while OTA requests are short and stateless, but they share a
  process, so they share a worker pool, a restart, and a crash.

### Behind a reverse proxy

One port and two paths means a proxy in front has to treat those paths
differently. Four things to get right:

- **Set `server.websocket_url` explicitly, or trust the proxy.** The
  derived URL is wrong behind a proxy that terminates TLS: uvicorn only
  trusts `X-Forwarded-Proto` from `forwarded_allow_ips`, which defaults to
  `127.0.0.1` and so will not match the proxy's address. The reply then
  says `ws://` where it should say `wss://`, and devices fail to connect
  with nothing obviously misconfigured. Either name the URL yourself, or
  put the proxy's address in the `FORWARDED_ALLOW_IPS` environment
  variable, which uvicorn reads when the setting is not passed. There is
  no config key for it: `server.websocket_url` is the explicit answer, and
  the environment variable covers the rest without a second way to say the
  same thing.
- **One idle timeout is enough, above 20 seconds.** The server pings every
  connected device every 20 seconds, so a conversation WebSocket is never
  actually idle even when nobody is speaking. A proxy therefore needs only
  a read timeout above that interval, and the two paths need no different
  treatment.
- **Allow the upgrade and turn off response buffering** on the WebSocket
  path. A proxy that buffers, or that does not pass `Upgrade` and
  `Connection` through, either breaks the handshake or adds latency to every
  spoken reply.
- **Restarts end conversations.** Every open WebSocket dies with the
  process, and the OTA endpoint shares that process, so neither can be
  restarted without the other. The server drains on SIGTERM (see
  [Limits](#limits)); give whatever stops it a grace period above
  `drain_s`.

Separating the two later needs no separate ports and no code change: run the
same image twice, route `/xiaozhi/ota/` to one group and `/xiaozhi/v1/` to
the other, and point `server.websocket_url` at the second. Devices follow,
because they are told where to go.

## Status

samtal-server serves conversations end to end: OTA and WebSocket
endpoints, the VAD/ASR/LLM/TTS pipeline on pluggable providers, agents
bound to devices, MCP tools on both sides, device authentication, limits,
structured logging, and a published multi-arch container image. The v1
plan and its per-milestone implementation notes live in
[`docs/plans/`](../docs/plans/); setup notes for a device on your desk are
in [`../docs/xiaozhi-notes.md`](../docs/xiaozhi-notes.md).
