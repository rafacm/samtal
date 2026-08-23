# vinga-server

The Vinga conversation server (Python): a wire-compatible backend for
[78/xiaozhi-esp32](https://github.com/78/xiaozhi-esp32) devices, written
against the firmware's
[protocol docs](https://github.com/78/xiaozhi-esp32/blob/main/docs/websocket.md);
the device-token scheme follows
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

Those four acronyms are the vocabulary of the whole configuration, so in
full:

| Stage | | What it does |
| ----- | - | ------------ |
| `vad` | voice activity detection | Decides where speech starts and when the user has stopped, so the rest of the pipeline runs on an utterance rather than on a stream |
| `asr` | automatic speech recognition | Turns that utterance into text. Also called speech to text |
| `llm` | large language model | Writes the reply, and asks for tools |
| `tts` | text to speech | Turns each sentence of the reply back into audio |

They run strictly in that order, each waiting on the one before it, which
is why the latency of any one of them is latency the user hears.

Those two paths, `/healthz`, and the configuration API under `/api` are
everything the server exposes: the interactive API docs are turned off,
the WebSocket requires a device token the OTA endpoint issued, and every
request to `/api` carries a bearer token.

## Goals

- Python-only, and no database server to run: the domain half of the
  configuration lives in an embedded SQLite file on the data volume,
  and a conversation needs no store at all
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
| "Hello, I am your vinga assistant..." (3.6 s) | 1781 ms | 658 ms | 887 ms | 1177 ms |
| "Hej, jag är din vingasassistent." (2.1 s) | 1743 ms | 545 ms | 607 ms | 1101 ms |

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
gives up, turning "vingasassistent" into "samhållssystem" at 10 dB
and the whole sentence into unrelated English at 0 dB. A larger local
model closes some of that, at a latency cost the table above already
shows there is no room for.

That table is synthesized speech, which is cleaner than a room. On the
device, with both engines pinned to Swedish, the cloud engine was still
the better of the two but neither was perfect:

| Said to the board | `gpt-4o-mini-transcribe` | `faster_whisper` small |
| --- | --- | --- |
| "Vad heter Sveriges huvudstad?" | exact | "Vad heter Sveriges Hubbetsstad?" |
| "Vad heter din vingasassistent?" | "Vad hette ditt vingasassistent?" | "Men hejter den vingaassistenten." |

What the local engine still wins: it is the only one that keeps the
audio on your host, and it is the only one that reports which language
it heard. It is also free per utterance, which a busy household
notices.

### OpenAI transcription

```bash
vinga-server config set provider asr ears -f - <<'YAML'
type: openai
api_key_env: OPENAI_API_KEY
prompt: vinga
YAML
```

Keys are named, never written, exactly as for the TTS types above.

| Option | Default | What it does |
| ------ | ------- | ------------ |
| `api_key_env` | required for OpenAI itself | Name of the variable holding the key |
| `model` | `gpt-4o-mini-transcribe` | `gpt-4o-transcribe` is the larger sibling; `whisper-1` is the same Whisper V2 you could run locally |
| `base_url` | `https://api.openai.com/v1` | Point it at any server implementing `/v1/audio/transcriptions` |
| `prompt` | unset | Vocabulary the engine would not otherwise guess: names, places, the assistant's own. Never agent names or anything imperative: see below |
| `language` | unset | Spoken language (ISO 639-1). Set it for any non-English deployment: see below |
| `temperature` | unset | 0.0 to 1.0, the API's own default when unset |
| `timeout_s` | `30` | Seconds before a transcription is abandoned, and a real bound: retries are off |

**This `prompt` is not the agent's prompt.** Two unrelated options share
the name: the one here is a list of words the transcriber should expect,
and the one under `agents:` is the agent's instruction sent to the LLM.
This one is a hint about vocabulary, not a request for behaviour, which
is exactly what makes the failure below surprising.

**Set `prompt`, and keep it to vocabulary.** An unfamiliar proper noun
is the one thing this type reliably gets wrong, and the prompt is what
fixes it: under noise, "vinga" came back as "sample" without it and as
"Vinga" with it. It fixes vocabulary, not language, so it cannot
compensate for the setting below: on the board, `prompt: vinga` still
produced "samstal" until `language` was pinned, and produced
"vingasassistent" exactly once it was.

**Never put anything the assistant could act on in it.** On short or
low-content audio the model hands the prompt back as the transcript
instead of hearing anything, reliably: 45 out of 45 clips of room tone
under a second came back as the prompt word for word. A prompt of plain
vocabulary makes that harmless noise. A prompt naming your agents makes
it an instruction, and in a field session it was one: a 0.9 s utterance
transcribed as `vinga, Oliver, Greta, Mateo`, and the model read the
agent names as a request and handed over to an agent nobody had
asked for. The server never hands a transcript that is the prompt and
nothing else (trimmed, case-insensitive, and ignoring a full stop the
model added) to the LLM as if spoken. Nor does it treat the echo as
proof of silence, because a field test caught that reading swallowing
real speech: nine echoes in two days of testing, every one on a clip
under two seconds, two of them a user saying "yes, please" and being
ignored.
An echoed clip is transcribed once more with the prompt withheld; a
real short answer survives that retry and is heard normally, genuine
silence comes back empty and is discarded, and each trip logs one
`asr_prompt_echo` event saying which it was. The rule stands anyway:
wake words, agent names and anything imperative do not belong here.
Recognising an agent's name when it is genuinely spoken is worth less
than never acting on one that was not.

**Set `language` unless the household speaks English.** This is the
one setting a device checkpoint changed our mind about. On clean audio,
leaving it unset costs nothing: recognition is multilingual either way,
and detection happens inside the model rather than as a separate pass
you pay for. On a real board in a real room it is a different story.
Unpinned, Swedish came back as English-shaped nonsense:

| Said to the board | Unpinned | `language: sv` |
| --- | --- | --- |
| "Vad heter Sveriges huvudstad?" | "Hat hetas verigezogistad." | exact |
| "Vad heter din vingasassistent?" | "Wat hat er dien samstal asynstind?" | "Vad hette ditt vingasassistent?" |

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
| "Hello, I am your vinga assistant..." | 79 ms | 188 ms | 764 ms |
| "Hej, jag är din vingasassistent." | 54 ms | 194 ms | 818 ms |
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

```bash
vinga-server config set provider tts eleven -f - <<'YAML'
type: elevenlabs
voice_id: PUT_YOUR_VOICE_ID_HERE
api_key_env: ELEVENLABS_API_KEY
YAML
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

```bash
vinga-server config set provider tts openai_voice -f - <<'YAML'
type: openai
voice: alloy
api_key_env: OPENAI_API_KEY
YAML
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
reply. See Choosing a voice above for the comparison in full.

That cost is now paid once per reply rather than once per sentence.
The server synthesizes the next sentence while the current one is
still playing, so the boundaries inside a reply cost a single frame,
and the stutter that used to make this type unsuitable for an agent
that tells stories is gone. Reply length no longer picks between the
types; the wait before the first word is what does.

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
`[A-Za-z0-9_-]+` name and cannot be `self` or a builtin's name
(`switch_agent`, `remember`). Both transports the specification
defines are supported:

```bash
vinga-server config set mcp-server home -f - <<'YAML'
transport: stdio
command: mcp-proxy
args: ["http://homeassistant.local:8123/mcp_server/sse"]
env:
  API_ACCESS_TOKEN: $HOME_ASSISTANT_TOKEN
YAML

vinga-server config set mcp-server weather -f - <<'YAML'
transport: streamable_http
url: http://localhost:8000/mcp
headers:
  Authorization: $WEATHER_TOKEN
tool_timeout_s: 15
YAML
```

**SSE-only servers** are reached through a bridge rather than a
transport of their own. Point `mcp-proxy` at the endpoint and configure
the result as the stdio server it now is, which is what the `home` entry
above does:

```yaml
transport: stdio
command: mcp-proxy
args: ["https://example.invalid/mcp_server/sse"]
```

There is no native SSE arm and there will not be one. The specification
moved its HTTP story to streamable HTTP and left SSE deprecated, so a
third transport here would be permanent maintenance for a shrinking
population, bought straight after this server paid to leave one
deprecated client behind. The bridge is one line of configuration and
everything else about the entry, secrets, egress, grants, the timeout,
is the same as any other stdio server's.

**Per-tool grants.** An `mcp` entry is either the entry name on its own,
which is the whole server, or an object naming the server and the tools
of it that layer may reach:

```bash
vinga-server config set agent kids -f - <<'YAML'
prompt: You are the assistant in the kids' room.
mcp:
  - weather
  - server: home
    tools: [turn_on_light, turn_off_light]
YAML
```

The tools are named the way the model is given them minus the entry
prefix (`turn_on_light` for `home__turn_on_light`), which is the name
`vinga-server config status` prints, so an operator writes down the
name they read; what the server called the tool before the publishing
rule got to it is never a name this side answers to. Leaving `tools` out
of the object means the whole server, the same as the string form, and
an empty list is refused: "granted, nothing allowed" is a confusing
spelling of `mcp: []`. It is an allow list and there is no deny list,
because a denied set fails open: a kitchen-sink server that adds a tool
would silently grant it to every agent that denied the old ones, which
is exactly wrong on the shared family device the feature exists for. The
same grant is checked again when a call arrives, so a tool an agent was
not offered is refused rather than run if the model asks for it anyway.

An allow list cannot be checked when it is written, since only a live
connection knows what a server publishes. A name that matches nothing is
logged when the server's tools come out of the publishing rule, and
`config status` shows each agent's allowed tools beside the published
ones, so the mismatch is answerable in one read. The comparison is
against what published rather than against what the server listed: a
tool dropped for a name collision or for being too long once prefixed is
exactly as unreachable as one that was never offered.

Secrets follow the same rule as everywhere else: a value of `$NAME` is
read from that environment variable at startup, and any secret-looking
key (`token`, `api_key`, `authorization`, ...) must use that form. An
unset variable fails the boot, as does an unknown reference or a
reserved entry name. A server that is merely unreachable does not: it
logs a warning, contributes no tools, and reconnects in the background
when a session that would use it opens.

**Guidance for a server's tools.** A tool's own description says what
it does; how this deployment wants it used is the operator's, and it
belongs beside the server rather than copied into every persona that
was granted it. An entry's `instructions` is that text, injected into
the system prompt of every agent the entry is granted to:

```bash
vinga-server config set mcp-server home -f - <<'YAML'
transport: stdio
command: mcp-proxy
args: ["http://homeassistant.local:8123/mcp_server/sse"]
instructions: |
  The lights, the blinds and the front door are on this server. Turn
  lights on and off freely. Always ask the user to confirm before
  unlocking the door.
YAML
```

It is stored and injected as written, its indentation and its own blank
lines included, and it goes into the prompt under a heading naming the
prefix its tools carry (`home__`), so the model can tie the paragraph to
the names it can call. The only bytes trimmed are whitespace at the two
ends of the whole assembled prompt, and what the surface below reports
is trimmed with them: what it counts is what the model receives.

The grant is the whole condition. Every agent granted the entry is told
about it, whether or not the server is connected and whatever an allow
list narrows its tools to; an agent with `mcp: []` is told none of it.
That means guidance about a tool a particular agent cannot reach is
noise in that agent's prompt, and the answer is to write about the
surface the agents are actually granted, not to expect the server to
work it out. Guidance is whole-entry: an entry whose tools want two
different paragraphs is two entries.

Editing it does not restart the connection, since it is prompt text the
connection never sees, so a reload reports the entry as `unchanged` and
the tools do not blink. What that costs is stated rather than hidden:
the new text reaches a conversation at its **next activation**, a new
session or an agent switch, and a conversation already running keeps
the text it was activated with until it ends. Sessions are minutes
long, so what an edit buys is the next one.

**Guidance the server ships about itself** is a different thing, and it
is off. A server has two channels of its own: the `instructions` of its
handshake, which the specification describes as how to use the server
and its features, and the prompts it publishes. Both are a third party
writing part of your agent's system prompt, so each is an explicit
opt-in on the entry, and there is no way to turn them on for a whole
deployment at once:

```yaml
use_server_instructions: true
inject_prompts: [forecast_style]
```

`use_server_instructions` injects the handshake's text. What a server
ships is captured on every connect whether or not the entry opted in,
so turning it on applies at the next reload with no reconnection, and
turning it off stops the injection while the connection stands.

`inject_prompts` names published prompts, one at a time, by the name
the server lists them under. Wholesale is not offered: the
specification defines prompts as user-controlled templates and a server
may publish dozens, so the operator who read its documentation names
the ones that are standing guidance. The names are checked against the
server's own listing before anything is fetched, and a name it does not
publish, a prompt that declares required arguments, and one that
renders anything but text are each skipped with a warning naming the
entry and the **position in the list**, never the name: a prompt name
is a string the server chose and you copied, so it may hold anything,
and the same is true of every byte of what it publishes. None of it is
ever written to a log. Editing the list changes what a connect fetches,
so applying it does restart the connection.

**One thing is taken back out.** Whatever this deployment gave the
entry in its `env` or `headers` is replaced with `[redacted]` in what
the server ships back, before any of it is stored. Opting in is a
decision about a third party's words, not a decision to let that server
hand your own credential back through a prompt or a gated read, and a
careless server that echoes what it was configured with is the ordinary
case rather than the hostile one. Values shorter than eight characters
are left alone, since an `env` holds ports and locales too.

Both channels are capped at 4000 characters per block, and a longer one
is skipped whole rather than truncated: half an instruction is an
instruction nobody reviewed. What is injected appears under
`server_instructions:<entry>` and `server_prompt:<entry>:<position>` in
the surface below, with a heading in the prompt itself saying the
server is the one talking, so neither you nor the model has to guess
whose words they are. The prompts are re-fetched on every reconnect,
which reaches new sessions and switched-in agents the way a reload's
guidance does.

**The device's own tools** need no configuration. A board whose hello
advertises `features.mcp` is asked for its tools over the same socket
the audio runs on, and they arrive under their firmware names with the
dots replaced (`self_audio_speaker_set_volume`), because both LLM APIs
restrict tool names to `[A-Za-z0-9_-]`.

**Builtins** are `switch_agent`, offered when the device is bound to
more than one agent, and `remember`, offered when memory is configured.
A successful `switch_agent` ends the current agent's reply: the new
agent greets the user in its own prompt and its own voice, with the
conversation so far carried over.
`remember` appends one fact to the agent's memory file:

```yaml
memory:
  dir: /var/lib/vinga/memory
```

One file per agent, created on first write and injected into that
agent's system prompt on every reply, capped at 8 KiB or 200 lines with
the oldest dropped first. Memory is keyed by agent and not by device: a
agent is one entity across rooms. Leave the section out and there is
no `remember` tool and no injection.

No builtin is granted the way an MCP server is. Each appears under a
structural condition instead, and the conditions are not agent-shaped.
`switch_agent`'s is the device's: it exists exactly when the board is
bound to more than one agent, and withholding it from one of them would
strand a conversation on whichever agent has no way back, which is the
receptionist handoff the tool was written for. `remember`'s is the
deployment's: memory is configured or it is not, and where it is, the
injection into the system prompt is unconditional, so an agent with the
tool withheld would recall for ever and never learn. Tools with sound
structural rules do not need a grant model on top of them; the day a
builtin arrives whose availability is genuinely per-agent policy, the
grant edge the `mcp` list already carries is where it lands.

A tool that fails, times out, or does not exist comes back to the model
as an error result rather than ending the reply, so the assistant says
what went wrong in its own voice and the user's language. The device
hears silence while a tool runs, bounded by `tool_timeout_s` (15 seconds
by default).

**What a tool answers with is speakable text.** The output of this
pipeline is a voice and its history is text throughout, so text is what
a result contributes. A server that answers with an image, or with
anything else the specification allows, has that part rendered as a
named placeholder (`[unsupported image content]`) rather than dropped:
the model can then say what it was given and that it cannot use it,
which is better than a reply that reads as though the tool was ignored.
The condition for revisiting this is the display: when the device path
can render more than speech, a result can start carrying structured
content to the board, and that work belongs beside the display protocol
rather than inside the tool loop.

### What the model is actually sent

An agent's system prompt is assembled from more than one place now, so
there is a command that says what it adds up to:

```console
$ vinga-server config prompt house
persona (133 characters)
You are the assistant in the living room. Answer in the language you
were spoken to, and keep answers short: this is spoken out loud.

fragment:household (100 characters)
The bins go out on Tuesday evening, the kitchen speaker is called
Bosse, and the cat is called Ines.

instructions:home (210 characters)
Guidance for using the tools whose names begin with home__:
The lights, the blinds and the front door are on this server. Turn
lights on and off freely. Always ask the user to confirm before
unlocking the door.

server_instructions:home (172 characters)
What the server behind the home__ tools says about using them:
Device names are the ones set in Home Assistant. A scene is turned on
like a light, not called like a script.

memory (108 characters)
You remember these facts about past conversations:
- the user is vegetarian
- the user's dog is called Bosse

total: 731 characters
```

**The order is fixed and documented**, and deliberately not
configurable: the agent's own prompt first, because it says who is
speaking and everything after it is read in that voice; then the shared
fragments it includes, in the order its layer lists them, because they
are standing context the persona speaks within; then the guidance of
each MCP entry the agent is granted, in the order the grants name them,
each under its heading, and within one entry what the operator wrote
first and what the server itself ships after it; then the remembered
facts last, under the heading they have always had. Blocks are
separated by blank lines. One
documented order beats a per-deployment permutation, and it is what lets
a later feature compose against a known base.

**A shared fragment is written once and included by name.** Household
facts, a house style, anything every agent in the deployment should
know: it goes into `prompt_fragments` under a name, and each agent that
should carry it names that fragment in its `prompt_includes`.

```bash
vinga-server config set prompt-fragment household -f examples/prompt-fragment.yaml
vinga-server config set agent house -f agent.yaml   # prompt_includes: [household]
```

The alternative is copying the same paragraph into every persona prompt
and watching the copies drift, which is what this exists to stop.
`prompt_includes` follows the `mcp` list's rules exactly: leaving it out
inherits the `agent_defaults` list, naming a list replaces the inherited
one rather than extending it, and `prompt_includes: []` opts one agent
out of what its siblings share. A name that matches no fragment is
refused when it is written, since the fragment is a row in the same
database. The text is injected as written, with no heading over it: it
is prompt text the operator composed, and a heading would editorialize.
Its indentation and its own blank lines are part of it, and the only
bytes trimmed are whitespace at the two ends of the whole prompt, which
the surface above reports trimmed with them. A fragment is one of the
kinds `vinga-server config reload` applies, so writing or editing one
reaches every agent that includes it at that agent's next activation,
and the write says so.

**Each block is counted** because every one of them competes with the
others for the context budget of a small local model, and there is no
automatic trimming: a server that silently dropped an instruction block
would be worse than one that says what it injected. The number to tune
against is the total, which is the sum of the blocks plus the blank line
between each pair of them: the prompt is the blocks joined and nothing
else, so a character counted here is a character the model receives. Agent activation also logs a `prompt_assembled`
event with the same per-source counts, so a model that degrades in the
field can be diagnosed from the retained logs without reproducing the
session.

**It is a preview of a new session**, not a readback of a running one.
The persona, the fragments and the guidance are assembled when a
conversation starts
and again when it switches agents, and held for the life of that
activation; the remembered facts are read on every reply, so a fact
stored by one conversation is known to a concurrent one on its next
reply. So this command answers what a session opening now would be
given, which is what an operator auditing a configuration wants, and a
conversation that started before the last reload is holding the older
text until it ends.

Over the API it is `GET /api/runtime/agents/{name}/prompt`, and it is a
read of the running server rather than of the database: the agents this
server is serving, the MCP slice it is running, and the memory it
writes. An agent it is not serving answers 404 naming the reload, since
that is what installs one.

### What the MCP servers are doing

The configuration says what should be running; `vinga-server config
status` says what is:

```console
$ vinga-server config status
home: connected since 2026-08-13T09:12:03.104213+00:00
  tools: home__turn_on_light, home__turn_off_light, home__unlock_door
  agents: house, kids (turn_on_light, turn_off_light)
weather: down since 2026-08-13T10:41:57.882014+00:00 (ConnectionRefusedError)
  tools: (none)
  agents: house
archive: unused since 2026-08-13T09:12:02.991044+00:00
  tools: (none)
  agents: (none)
```

Three states. **connected** is offering the tools listed under it.
**down** is not, with the reason beside it: the class of the failure, or
`DroppedAfterFailedCall` for a connection dropped after a tool call
failed on it, which is what a server restarting looks like from here. A
down server contributes no tools and is reconnected in the background
when a session that would use it opens, so this is a diagnosis rather
than a chore. **unused** is configured and referenced by no agent, so no
connection was ever built for it: the answer to "why does the agent not
have that tool" when the entry looks right, and invisible everywhere
else.

Each agent under `agents:` is named on its own when it may reach the
whole server, and with its allowed tools in parentheses when it was
granted only some of them. That list sits beside the published one, so
an allowed name the server does not actually offer is answerable in one
read.

It is a read of the running server rather than of the database, which is
why there is no `--local` for it and why what it says cannot disagree
with what is actually connected. Over the API it is
`GET /api/runtime/mcp-servers`, keyed by entry name. The `/runtime`
namespace is separate from the entity namespaces on purpose: an
`mcp_servers` entry may legally be named `status`, and a runtime route
under `/mcp-servers/` would have shadowed it.

The tool lists are published names and nothing else, deliberately: a
description, or the name a server listed before the publishing rule got
to it, is bytes that server chose, and a server holding one of this
deployment's credentials could reflect it in either. Published names
are the exception because the model has to be given them and an
operator has to be able to write one down.

### Applying a change without a restart

A running server serves one immutable snapshot of the domain half at a
time, so writing an entry and granting it to an agent used to cost a
restart and every conversation on the server. `vinga-server config
reload` builds the next snapshot and swaps to it instead:

```console
$ vinga-server config set mcp-server weather -f weather.yaml
wrote mcp-server weather
This applies when the running server is asked to reload: run
`vinga-server config reload`, which ...
$ vinga-server config set agent house -f house.yaml
$ vinga-server config reload
mcp:
  started: weather
  restarted: (none)
  stopped: (none)
  unchanged: home
prompts:
  changed: house
fillers:
  resynthesized: (none)
  reused: house, kids
  disabled: (none)
providers:
  built: (none)
  reused: asr.ears, llm.local, tts.voice, vad.gate
  retired: (none)
agents:
  added: house
  removed: (none)
  defaults_changed: no

home: connected since 2026-08-13T09:12:03.104213+00:00
  tools: home__turn_on_light, home__turn_off_light
  agents: kids, house
weather: connected since 2026-08-13T11:02:44.118902+00:00
  tools: weather__forecast
  agents: house
```

**What it applies** is the whole domain half, re-read from the
configuration database: the `providers` entries and the `mcp_servers`
entries with the secrets stored on them, the agents' effective `mcp`
grant lists (`agents.<name>.mcp` and `agent_defaults.mcp`), the shared
`prompt_fragments`, the agents themselves and the `agent_defaults`
layer under them. An MCP entry that is new or newly referenced is
started, one whose fragment or whose stored secrets changed is stopped
and rebuilt (so rotating a credential applies here too), one that is
gone or no longer referenced is stopped, and an unchanged one keeps the
connection it had, untouched. The MCP outcomes come back with the status
document, so one command both applies and verifies, and the sections for
the kinds a later release will apply are named rather than missing.

An entry whose `instructions` is all that changed is `unchanged`, and
that word is about the connection: nothing was reconnected, and the new
guidance is what the next activation reads. That is deliberate. The
text configures a prompt and not a connection, so dropping a live one
to apply it (mid-call tools, a respawned stdio child) would be churn
without a cause.

**Filled pauses are re-synthesized, and only where they had to be.** A
clip is a configured phrase spoken by a configured voice, and the unit
of comparison is the whole effective `filler` section: an apply keeps
every clip whose section and whose voice are what they were, and
synthesizes the rest. So an edit to a prompt sends nothing to a
text-to-speech engine, and neither does an edit to a provider entry no
masked agent speaks through; but an edit to any field of the section
does, `delay_ms` included, even though the
audio that comes back is identical to what it replaced, and so does
rewriting the entry an agent's voice comes from, whose clips are spoken
again in the engine the reload built. That is
deliberate rather than an oversight: the section is one value, and a
comparison that covered part of it would be a second rule about what a
clip depends on. Synthesis is real work at the configured provider, and
may be billed there, so a reload of a deployment with many masked agents
costs what this says it does. The
`fillers` section names each agent under one of three outcomes, and an
agent whose synthesis failed is `disabled`: the reload applied, that
agent runs with the mask off, and the next reload tries again. A
text-to-speech hiccup never holds back a prompt fix.

**The engines are rebuilt, and only the ones that moved.** An entry
whose definition and stored credential are what they were is carried
into the new world as the object it already was, so an edit to a prompt
reloads no local model and rotating one provider's key does not touch
another's. A rewritten entry is built while the old one is still
serving, and the conversations that open after the apply speak through
the new one; one that a conversation is still speaking through is
released when that conversation ends, so applying a change to a local
model briefly holds two of it. The `providers` section names the entries
built, reused and retired. An entry that will not build, or that
`server.local_only` forbids, refuses the reload with nothing changed.

**The agent set moves with the rest.** An agent the store has added is
built with everything else the apply builds and is servable the instant
the request answers, so a device bound to it reaches it at its next
check-in with no restart between the write and the board; an agent it
has deleted is one no session can be opened as from that same instant,
while a conversation already talking as it finishes on the world it was
built from and is served that world's prompt to the end. The `agents`
section names both, and says whether `agent_defaults` moved. The one
thing an agent carries that a reload does not move is its memory, which
is keyed by its name. The whole `server`
section (including the configuration file itself) is start-time as it
always was.

**No session is dropped**, and when one meets the change depends on
which half moved. The tools an agent may reach are snapshotted per
reply, so a conversation in progress meets the new tool world on its
next utterance; a tool call in flight on a server the reload stopped
fails into the same error result a server dropping mid-call produces,
which the assistant explains in its own words. Prompt text is assembled
once per activation and cached for it, so a rewritten prompt, fragment
or `instructions` reaches a conversation at its next activation, which
is a new session or an agent switch, and never mid-reply. Filler clips
are bound by a conversation when it opens, so a re-synthesized one
reaches the next conversation rather than changing what an open one is
masking with.

**Nothing is half applied.** The whole new world is composed, validated
and built before anything running is touched, so an unset `$VAR`, a
credential that will not decrypt, an entry `server.local_only` forbids,
or a stored configuration that will not compose into something this
server can serve refuses the reload and leaves it exactly as it was. A
server that merely will not connect is not that: it applies, shows
`down` with its reason, and is reconnected in the background like any
other. One apply runs at a time; a second is refused with the retryable
409 a contended write answers with, having changed nothing.

Over the API it is `POST /api/runtime/config/reload`.

## Stack

Python 3.12 with [FastAPI](https://fastapi.tiangolo.com), managed with
[uv](https://docs.astral.sh/uv/). Pydantic models validate both halves
of the configuration, the YAML file and the rows of the SQLite database
behind `vinga-server config` (SQLAlchemy Core, Alembic migrations run
on open); the same types and the same repository back the configuration
API, of which the command grammar is a client. Integration tests
drive the server with the [xiaozhi-sdk](https://pypi.org/project/xiaozhi-sdk/)
device simulator, so CI holds real conversations without hardware. The wire
protocol is kept isolated behind a small interface, separate from the
conversation pipeline.

## Development

```bash
uv sync                             # install dependencies
uv sync --extra faster-whisper --extra piper  # add the local ASR/TTS engines
uv run vinga-server                # run the server
uv run pytest tests/unit -q         # unit tests
uv run pytest tests/integration -q  # integration tests
uv run ruff check .                 # lint

# What CI runs the unit lane as: distributed over worker processes,
# a file at a time. Reach for it to reproduce a failure that only
# shows up in CI. Local runs are serial by default.
uv run pytest tests/unit -q -n auto --dist loadfile
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
VINGA_LOCAL_LANE=1 uv run pytest tests/local -q
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
`VINGA_LOCAL_OLLAMA` and `VINGA_LOCAL_LLM_MODEL` override both. The
first run downloads the whisper model and Piper voice at server startup
and can take a few minutes; later runs finish in seconds. Without
`VINGA_LOCAL_LANE=1` the lane skips, so a bare `pytest` stays safe.

### The smoke lane: a conversation with a container

A fourth lane runs nothing itself. It points at a server that is already
up and holds one whole conversation with it: healthz, an OTA check whose
token it verifies, and a full utterance-to-audio exchange through the
device simulator. CI runs it against the image it just built, seeding
that image's own CLI into the volume it then reads, which is what turns
"a seeded volume and one `docker run` serve a conversation" into
something checked rather than remembered.

```bash
docker build -t vinga-server:local .

# The domain half first, written by the CLI from the image itself into
# the volume the server then reads. tests/smoke/seed.sh is what CI runs:
# it starts a server of its own inside this container, configures it over
# loopback, and stops it again, which is why the container gets what a
# server needs.
docker run --rm \
  -e VINGA_AUTH_SECRET=smoke-secret \
  -e VINGA_API_SECRET=smoke-api-token \
  -v smoke-data:/data \
  -v "$PWD/tests/smoke:/smoke:ro" \
  -v "$PWD/tests/smoke/config.yaml:/config/config.yaml:ro" \
  --entrypoint sh vinga-server:local /smoke/seed.sh

docker run -d --name vinga-smoke -p 8003:8003 \
  -e VINGA_AUTH_SECRET=smoke-secret \
  -e VINGA_API_SECRET=smoke-api-token \
  -v smoke-data:/data \
  -v "$PWD/tests/smoke/config.yaml:/config/config.yaml:ro" \
  vinga-server:local

VINGA_SMOKE_OTA_URL=http://127.0.0.1:8003/xiaozhi/ota/ \
VINGA_AUTH_SECRET=smoke-secret \
  uv run pytest tests/smoke -v
```

The secret has to match the one the server under test was started with:
the lane verifies the token it is issued, and that needs the signing key.
It skips without `VINGA_SMOKE_OTA_URL`, so a bare `pytest` stays safe,
and it works against any reachable server, not only a container.

## Configuration

Configuration comes in two halves, kept in two places for one reason:
how the process runs is decided when it is deployed, and what it says
and to whom is decided while it runs.

**The server half is one YAML file.** `server:` (host, port, auth,
onboarding, limits, logging, capture, where the database lives) and an
optional `memory:`. It is passed as `--config /path/to/config.yaml` or through
the `VINGA_CONFIG` environment variable; with neither set, defaults
apply, and it is handled by
[pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/).
[`config.example.yaml`](config.example.yaml) documents every key of it,
and [`config.deploy.example.yaml`](config.deploy.example.yaml) is a
ready-to-adapt profile for the container image behind a proxy on a small
CPU quota, holding values validated by latency measurements from a live
deployment. That profile's domain half is the runnable script beside it,
[`config.deploy.example.sh`](config.deploy.example.sh), which the test
suite runs against a real server, so its measured values are checked
rather than merely written down.

**The domain half lives in a database**, one SQLite file under
`server.database.dir`, written with `vinga-server config`: named
`providers` per stage (`llm`, `asr`, `tts`, `vad`), named `mcp_servers`,
named `prompt_fragments` holding the blocks of prompt text agents share,
`agent_defaults` holding what every agent uses unless it says otherwise,
`agents` combining a prompt with provider, fragment and MCP references,
`devices` binding MAC addresses to agents, and `default_agent` for
unknown devices.

The CLI writes it through the configuration API on the running server,
so these commands need one to be up, and an empty database is a valid
state for it to be up on. From inside the container the token and the
loopback address are already in the environment; from outside, name the
API with `--api-url` (or `VINGA_API_URL`) and carry the token yourself.
A whole deployment, from an empty database:

```bash
vinga-server config set provider llm claude -f examples/llm-anthropic.yaml
vinga-server config set provider asr ears -f examples/asr-openai.yaml
vinga-server config set provider tts voice -f examples/tts-piper.yaml
vinga-server config set provider vad silero -f examples/vad-silero.yaml
vinga-server config set prompt-fragment household -f examples/prompt-fragment.yaml
vinga-server config set agent-defaults -f examples/agent-defaults.yaml
vinga-server config set agent assistant -f examples/agent.yaml
vinga-server config bind-device aa:bb:cc:dd:ee:ff assistant
vinga-server config set-default-agent assistant
vinga-server config list
```

A board in front of you needs neither its MAC nor that `bind-device`
line: `config pending` lists what is waiting and `config add-device`
binds one by the code on its screen. That is
[Onboarding a device](#onboarding-a-device).

That order is not a style: a write whose references do not resolve is
refused, so providers and MCP servers come first, then the agents, then
the bindings. The rules about a runnable server (every stage of every
agent resolving, a default agent when nothing is bound) are checked at
boot instead, so a half-built database is a legitimate state to be in
and an illegitimate one to serve from.

**When the server will not start**, there is nothing to write through,
which is what `--local` is for: `show`, `delete`, `clear-secret` and
`set-secret` against the database directly, the four commands that get a
deployment out of a state its own server refuses. Every `--local`
invocation says on stderr that it bypasses the API, and each write then
says when it takes effect, the same answer the API gives for that act;
every other command refuses `--local` by naming the four.

Every field of the domain half is documented in
[`../docs/reference/domain-config.md`](../docs/reference/domain-config.md),
generated from the models: `vinga-server config reference` prints that
same document, and `vinga-server config schema [entity]` prints the JSON
Schema behind it. [`examples/`](examples/) holds a commented fragment per
entity and provider type, each naming the command that installs it, and
that is where the measured numbers and the field findings behind each
provider option are kept. `config list` and `config show` read back what
is stored, with every secret masked.

**The `server` section is what a start reads.** The port, the
directories, the limits and the barge-in tuning come out of the file
this process was launched with, and a change to any of them is picked up
when it is restarted. Nothing in the database is in that half.

**What a running server serves of the domain half is a generation:** an
immutable snapshot, validated whole, built entirely before anything
binds it. There is more than one of them over the life of a process, and
applying a change installs the next one rather than editing the one in
place, so a conversation goes on speaking the world it opened in while
new work binds the world that is current. A change reaches it in one of
two ways, and every mutating command says which.

**The reload applies the domain half, on request.** Writing a provider
entry or an MCP entry, rotating a secret on either, changing which
agents may reach an MCP server, editing a prompt fragment, writing an
agent, deleting one, or rewriting the `agent_defaults` layer under them
all takes effect when a running server is asked to reload, with no
restart and no session dropped: that is [Applying a change without a
restart](#applying-a-change-without-a-restart). Those writes name
`vinga-server config reload`, and say the three moments a conversation
already in progress meets an applied change at: the tools an agent may
reach at its next utterance, its prompt text at its next activation, and
the voice it speaks in and the clips it masks with at the next
conversation. An agent the reload added is one a device can be bound to
and reach at its next check-in; one it deleted is one no session can be
opened as from the moment the request answers, while a conversation
already talking as it finishes on the world it was built from. The one
thing an agent carries that a reload does not move is its memory, which
is keyed by its name.

**Device bindings are the other way, applied by being noticed.** A
running server reads the devices table and the default agent as a device
asks for them, so
binding a board, unbinding it, or changing the default agent applies
at that device's next OTA check or connection, with nothing asked of
the server at all. Those
writes say so instead. That ends where the agent does: a
binding naming an agent this server is not serving resolves to
nothing until the reload that installs it, and the
acknowledgement says that rather than promising otherwise. A
conversation already running is never touched by either.

Since a voice is a `tts` provider entry, two agents that should sound
different reference two entries, and a typical agent is a prompt plus a
voice. `agent_defaults` takes no prompt: a prompt is what makes an agent
that agent. A device is bound to one agent or to a list of them; with a
list, the first entry is the agent a conversation starts on, and the
rest are the ones `switch_agent` can reach.

Every key of the file half can be overridden with a `VINGA_`-prefixed
environment variable, nested keys joined with `__`:
`VINGA_SERVER__PORT=9000`, `VINGA_SERVER__DATABASE__DIR=./var`.
Environment variables beat the YAML file, and a `.env` file in the
directory the server is started from is read at startup (real
environment variables beat `.env` too). This layering matches container
deployments: the YAML arrives as a mounted file, overrides and secrets
as environment variables. The domain half has no environment layer: a
`VINGA_` variable naming one of its sections, like a section left in
the file, refuses the boot and names the command that writes it now,
because a configuration that quietly stopped applying is worse than one
that will not start.

`server.database.dir` defaults to `/var/lib/vinga`, which is the
generic answer rather than any deployment's: the container image points
it at its volume, and a development machine that cannot write there gets
an error naming the key. Point it somewhere writable for local work:

```bash
VINGA_SERVER__DATABASE__DIR=./var uv run vinga-server
```

The reading commands take the same key, through `--local`, which is what
lets one be run without a server on the other side:

```bash
VINGA_SERVER__DATABASE__DIR=./var uv run vinga-server config --local show
```

### The configuration API

The domain half is read and written over a REST API the server mounts at
`/api` on its own port. It is what `vinga-server config` talks to, and
it is the machine-readable way in for anything else. The contract is the
committed OpenAPI document,
[`../docs/reference/api-openapi.json`](../docs/reference/api-openapi.json):
every route, the schema of every body, and the refusals each route can
answer with. It is generated from the routes themselves by
`vinga-server config openapi` and regenerated by CI, so it cannot drift
from what is served. The API itself serves no interactive docs and no
live schema endpoint; the committed file is the contract.

**Every request carries a bearer token.** The API is always mounted and
there is no flag that turns it off, because an admin surface that can be
switched off by forgetting a key is a surface that ships unprotected.
The token is the value of the environment variable
`server.api.secret_env` names, `VINGA_API_SECRET` by default, and a
server started without it refuses to boot, naming the variable and the
fix:

```bash
VINGA_API_SECRET=$(openssl rand -hex 32)
```

Generate it once and keep it where the deployment keeps its other
environment secrets. A request with no token, or with the wrong one, is
answered 401 whichever path it asked for, whether or not that path is a
route: only an authenticated caller gets to learn which routes exist.
One request, for the shape of them:

```bash
curl -sS -H "Authorization: Bearer $VINGA_API_SECRET" \
  http://127.0.0.1:8003/api/config
```

**One noun per entity kind**, addressed the way the entity is keyed (a
provider by its stage and its name, a device by its MAC):

```
GET                 /api/config
GET                 /api/providers
GET PUT DELETE      /api/providers/{stage}/{name}
    PUT DELETE      /api/providers/{stage}/{name}/secrets/{slot}
GET                 /api/mcp-servers
GET PUT DELETE      /api/mcp-servers/{name}
    PUT DELETE      /api/mcp-servers/{name}/secrets/{slot}
GET                 /api/agents
GET PUT DELETE      /api/agents/{name}
GET PUT             /api/agent-defaults
GET                 /api/devices
GET PUT DELETE      /api/devices/{mac}
GET PUT DELETE      /api/default-agent
```

**And one namespace that is not about stored configuration at all**,
kept apart from the entity namespaces because an entity may legally be
named after any word a route might want:

```
GET                 /api/runtime/agents/{name}/prompt
GET                 /api/runtime/config/diff
GET                 /api/runtime/mcp-servers
POST                /api/runtime/config/reload
```

The first answers the system prompt a session opening now as that agent
would be sent, block by block with the size of each, which [What the
model is actually sent](#what-the-model-is-actually-sent) describes and
`vinga-server config prompt` prints. The second answers what the
database holds that this server is not serving, kind by kind: the names
added, removed and changed, and for each kind whether its changes reach
a conversation at the next reload or at a device's
next check-in, which is what makes it possible to say whether a write is
still waiting. It carries entity names and those labels and nothing
else, so a rotated credential shows up as the provider that holds it
being listed as changed. The third answers what each configured MCP
server is doing right now, which [What the MCP servers are
doing](#what-the-mcp-servers-are-doing) describes and `vinga-server
config status` prints. The reload applies what the stored configuration
holds to the running server, which [Applying a change without a
restart](#applying-a-change-without-a-restart) describes and
`vinga-server config reload` prints; it is the only route here that
changes what the server is doing rather than what is stored.

**And one namespace that reads the conversation record**, the store
[The conversation store](#the-conversation-store) describes:

```
GET                 /api/conversations
GET                 /api/conversations/{session}
GET                 /api/conversations/{session}/turns
```

The first lists the sessions, newest first, filtered by `?device=` when
given. The second is one session whole: its row, with how many turns and
events hang off it. The third is one session's turns, oldest first, each
carrying its numbers and the tool calls it made nested in the order the
model issued them. The list and the timeline page on the monotonic row
ids the store was built with; the detail read is one session and takes
neither argument. `?limit=` holds 50 rows by default and 200 at most,
and `?cursor=` is a row id this API answered with, meaning the sessions
before it in the listing and the turns after it in a timeline, which is
the direction a client that has read up to a turn asks in. A page
answers `{"items": [...], "next_cursor": <id or null>}`, and the cursor
is null when there was nothing beyond that page. A deployment that never
recorded answers 404 naming `server.conversations.enabled`; one that
recorded and has since switched recording off still serves what it
recorded. The events themselves are deliberately not served here: the
database is that surface, and there is no analysis endpoint for the same
reason there is no analysis command.

`GET /api/config` is the whole domain configuration, masked, with the
location of every stored secret beside it, which is the JSON of what
`config show` prints. Every other read answers with the entity's masked
body and the slots holding a secret stored in the database, each marked
with the entity key its value displaces, which is the one thing a masked
entity cannot carry itself. A read is masked always, and a stored secret
is never read back by any route.

A PUT is create-or-replace, the CLI's `set`: the body is the same
fragment, as JSON rather than YAML, every reference it names has to
exist already, and the entity's stored secrets are left alone. A secret
is written by PUTting `{"secret": "..."}` to its slot, which is the only
plaintext this API accepts and the reason the whole of it belongs on a
loopback connection or behind TLS. Three writes take an argument rather
than a fragment: that one, a device binding (`{"agents": [...]}`), and
the default agent (`{"name": "..."}`), whose DELETE clears it.

**A successful write says when it takes effect.** It answers
`{"wrote": "...", "notice": "..."}`, and the notice is one of a handful
of sentences. A device binding and the default agent carry the one about
a device asking, because a running server reads them as it asks: they
apply at that device's next OTA check or connection. Every other kind
this API writes, which is the whole of the rest of the domain half,
carries the one that names the reload above, and that sentence also
names the three moments a conversation already in progress meets an
applied change at: the tools an agent may reach at its next utterance,
its prompt text at its next activation, and the voice it speaks in and
the clips it masks with at the next conversation. A binding naming an
agent this server is not serving yet carries a sentence of its own, and
it is the one an operator is most likely to need, because both halves of
it are true at once: the row is live, and the agent arrives at the
reload that installs it. Nothing about a running conversation changes
when a write lands, in any of these cases.

**A refusal is an RFC 9457 problem document**, served as
`application/problem+json`, and carries the sentence the CLI prints in
`detail`, with a status code: 404 for an entity that does not exist,
409 for the retryable busy database lock or a reload already running
(nothing was changed, so retry), 422 for a fragment or an address the
caller got wrong, 500 for stored state that cannot be read, and 503 for
a runtime action asked of an application with no server around it.
Beside `detail` are the status's standard reason phrase as `title`, the
status repeated, and `errors`: one `{path, message}` entry per field of
the submitted fragment the refusal names, `path` an RFC 6901 JSON
Pointer into it, so a form can mark the offending field rather than
quote the paragraph. `errors` is always present and empty where the
refusal names no field. A request body is never quoted back, on any
path, and neither is a traceback: a fragment can carry a credential
pasted where a variable name belongs, and a refusal that echoed it
would be the leak. **Neither are its keys.** A refusal names only fields
this server declares and positions in a list; a key the request invented
(an unrecognized one, an option a provider passes through, an entry of
an `env` or `headers` map) is as good a place to paste a credential as a
value is, so a refusal about one says which rule it broke and points at
the nearest enclosing place this server can name.

**`vinga-server config` is the ergonomic client**, and the shape of a
deployment's own use of the API. It finds the server in this order:

1. `--api-url`
2. `VINGA_API_URL`
3. `http://127.0.0.1:<server.port>/api`, the port read from the same
   YAML file the server was started with (`--config`, or
   `VINGA_CONFIG`), so the two cannot disagree about it

The token is the value of the variable `server.api.secret_env` names,
read from that same file, and a missing one is a sentence naming the
variable before any request is sent. On a deployment both fall out for
free: exec into the running container, and the token variable and the
loopback address are already in the environment. That is the intended
way to run these commands.

The token grants everything the API can do, so the client refuses a
plain `http://` connection to a host that is not a loopback address
(`127.0.0.1`, `::1` or `localhost`), and there is deliberately no flag
to override it: such a flag's only purpose would be
sending the token in clear. A URL carrying a username or a password is
refused outright, and any URL the client prints has that stripped. Its
timeouts are explicit (5 s to connect, 30 s to read) so that the
server's own retryable answer, which can take up to the database's 10
second busy timeout to arrive, reaches you as itself rather than as a
transport error.

**`--local` is the break-glass path**, for when there is no server to
write through. It covers four commands, `show`, `delete`, `clear-secret`
and `set-secret`, opens the database directly, and prints on stderr
every time that it bypasses the API. When a change made this way is
observed is then the write's own answer, in the same two cases as
over the API: the next `config
reload` for a provider entry, an MCP entry, the secrets stored on
either, a prompt fragment, an agent and the defaults under them, and the
device's next check-in for a
binding. Every other command refuses the flag by
naming the four. It does not check whether a server is running:
there is no reliable way to, and a wrong refusal would wedge the
recovery path in exactly the situation it exists for. What makes that
safe is that a write is one transaction either way, so two writers
serialize: the second one waits for the lock, and on any ordinary write
it gets it and commits well inside the database's 10 second busy
timeout. Only a writer still waiting when that timeout runs out is
refused, and it is told nothing was changed and to run the command
again.

Its `show` and `delete` go by what is stored rather than by what a new
write would be allowed to create, which is what lets it reach a row
written before a rule that a later release added. The full procedure is
in the deployment notes, under
[The configuration API in a deployment](#the-configuration-api-in-a-deployment).

### Secrets

A credential is never written in the configuration, in either half. Two
forms are supported:

- **An environment reference**, which is the only form a fragment may
  carry: a provider names the variable holding its key (`api_key_env:
  ANTHROPIC_API_KEY`), an MCP server writes `$NAME` where the secret
  goes. The server reads the variable at startup and fails the boot when
  it is unset, rather than failing every conversation later.
- **A value encrypted in the database**, written with `config
  set-secret`, which reads it from stdin (not echoed at a terminal) or
  from a named variable with `--from-env`, and never from an argument:

  ```bash
  vinga-server config set-secret provider llm claude api_key
  ```

  Encryption uses `VINGA_MASTER_KEY`, one or more Fernet keys, newest
  first, comma separated. Generate one with:

  ```bash
  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  ```

  A stored secret takes precedence over an environment reference for the
  same slot, and `config show` marks the reference it displaces. With
  ciphertext stored and no key configured, or a key that does not open
  it, the server refuses to start naming the entity and the slot, and
  the API goes down with it, so the repair runs through `--local`. Two
  ways out, differing in what they need: `config --local clear-secret`
  removes the envelope and needs no key at all, leaving the slot to its
  environment reference if it has one, and `config --local set-secret`
  writes the credential again and needs a usable key in
  `VINGA_MASTER_KEY` to encrypt under, since storing a secret is the
  one write that encrypts anything.

Instance configs stay out of the repository; `*.local.yaml` and `.env`
are gitignored for local experiments, and the domain half of a local
experiment is a short script of `config set` calls against a database
directory of its own.

## Security

**Which hosts a configuration reaches.** Worth reading before deploying
anywhere with an egress allowlist, because a blocked host does not
announce itself: the server boots healthy, other stages keep working,
and the blocked stage waits out its `timeout_s` while the device plays
silence.

| Configured as | Reaches | Notes |
| --- | --- | --- |
| `llm: anthropic` | `api.anthropic.com` | |
| `llm: openai_compatible` | whatever `base_url` names | `api.openai.com` when pointed at OpenAI, a host on your own network when pointed at Ollama or vLLM |
| `asr: openai` | `api.openai.com` by default, else `base_url` | **Shares its host with an OpenAI LLM.** Adding cloud ASR to a deployment already using OpenAI for the LLM needs no new host |
| `tts: openai` | `api.openai.com` by default, else `base_url` | Same |
| `tts: elevenlabs` | `api.elevenlabs.io` | A separate host, and the one most likely to be missed |
| `asr: faster_whisper` | `huggingface.co` at first start only | Model weights, downloaded once into `/data` |
| `tts: piper` | the voice collection at first start only | Same |
| `vad: silero` | nothing | Weights ship with the package |
| `mcp_servers` (`streamable_http`) | whatever the entry's `url` names | Each entry is its own host |
| `mcp_servers` (`stdio`) | wherever the command it runs goes | Not knowable from the configuration |

The two local engines are the only entries that reach anything at
startup and then stop, which is why a deployment that has been running
for months can still be broken by an egress rule: nothing re-reaches
those hosts until the volume is cleared.

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
VINGA_AUTH_SECRET=$(openssl rand -hex 32)
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

or `VINGA_SERVER__AUTH__ENABLED=false` in the environment.

**Who gets a token is the allowlist.** A token is only issued to a device
the configuration resolves to at least one agent. Omit `default_agent`
and the `devices` map becomes an allowlist: an unknown MAC is issued
nothing and turned away. There is no second list to keep in sync.

**The OTA endpoint is the token issuer, so it cannot require a token.**
What protects it instead is stingy issuance and a path you choose. It is
served at two of them, and both are the same handler:

- `/x/<key>/`, the short path an operator types into a board's captive
  portal, where the key is eight base32 characters derived from the
  device-auth secret. Derived, so nothing configures or stores it, it
  survives restarts, and it changes only when that secret does. With
  device authentication off there is no secret to derive from and the
  route is served keyless at `/x/`.
- `server.ota_path`, the legacy full path, for boards already carrying
  one in NVS. Exposed publicly it should be a long random segment
  (`openssl rand -hex 8`), and it is nullable, so a deployment whose
  boards have all been onboarded through the short path can unmount it:

  ```yaml
  server:
    ota_path: /xiaozhi/ota/8f3a9c2b1d4e5f60/   # or null to unmount
  ```

The two segments are treated differently on purpose. The derived key is
printed at startup and repeated in the log line a wrong key produces,
which is what makes a typo and a rotated secret diagnose themselves; it
is a deployment-scoped path segment rather than a per-device
credential, and that trade is deliberate and recorded. The `ota_path`
segment is never printed anywhere, and neither is any device token.

The WebSocket path never moves: the token is what protects it.

**Nothing else is exposed.** `/x/<key>/`, `/xiaozhi/ota/` (or wherever
you put it), each with an `activate` beneath it that a waiting board
polls, `/xiaozhi/v1/`, `/healthz`, and the configuration API under
`/api/`, which answers 401 to anything not carrying its bearer token. FastAPI's
`/docs`, `/redoc`, and `/openapi.json` are turned off on both
applications, and `server.ota_path` refuses a path under `/api/`: the
OTA route is registered before the API is mounted, so it would be found
first and would answer a request the token gate never saw.

**Fully local is checked, not hoped for.** Every provider type declares
whether it sends session data (audio, transcripts, replies) off the
host, and with `server.local_only: true` the server refuses to boot any
provider that does, naming the stage and provider. The local engines
(Silero, faster-whisper, Piper) pass; an `anthropic` or `elevenlabs`
entry fails. The three `base_url` types, `openai_compatible` for the
LLM stage and `openai` for both ASR and TTS, can each point at
localhost or at a cloud vendor, so under `local_only` they must carry
your own declaration:

```bash
vinga-server config set provider llm local -f - <<'YAML'
type: openai_compatible
base_url: http://localhost:11434/v1
model: qwen3:8b
# Your assertion that this endpoint stays on this host.
egress: false
YAML
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

## Masking reply latency

The silence between the end of an utterance and the first audio of the
reply is where the assistant feels dead: healthy field turns run 1.5
to 3 s of it, and a slow provider stretches it well past the point
where users ask "are you there?". Humans hold exactly this gap with a
filled pause, and an agent can too:

```bash
vinga-server config set agent-defaults -f - <<'YAML'
filler:
  # off by default
  enabled: true
  delay_ms: 1800
  phrases:
    - "Hmm, let me see..."
    - "Good question..."
YAML
```

When a reply's first audio has not started within `delay_ms` of the
utterance being transcribed, the session plays one of the phrases,
rotating through them, and the real reply queues behind the clip's
tail. The clips are synthesized ahead of time in each agent's own voice
and cached as PCM, never at fire time: synthesis at the moment of
masking would add TTS latency to the exact gap being masked, and a
cached clip keeps working when the TTS provider is the thing being
slow. Ahead of time is the server start and every `vinga-server config
reload` after it, which re-synthesizes the agents whose effective
`filler` section or whose voice moved, whichever field of the section it
was, and hands the result to the next conversation; one already open
keeps the clips it opened with. A synthesis failure logs a
warning and leaves the feature off for that agent rather than failing
the boot or refusing the reload.

The filler is honest assistant speech: it moves the device into its
speaking state, counts as the turn's `speaking_started`, lands on
capture channel 1, and enters the barge-in gates like any reply audio,
so talking over it interrupts the reply, which is the correct reading.
One filler per turn, logged as a `filler_played` event; a turn that
outlives both the filler and the first-token watchdog resolves through
the watchdog's give-up path (see below), the filler being the soft
early threshold and the watchdog the hard late one. Write the phrases
in each agent's own language; an agent's own `filler` section replaces
the inherited one wholly, like the stage fields, and the reasoning
behind the default delay is in
[`examples/agent-defaults.yaml`](examples/agent-defaults.yaml).

The mask yields to the user. At fire time the timer stands down, with
a `filler_skipped` event, when the endpointer holds unresolved speech
or a barge-in confirmation has the outgoing frames paused. Both mean
the turn ended at a premature endpoint and the user is already mid
continuation: the reply in flight is about to be cancelled, and a
clip played into that would talk over them (field round 2 measured
exactly this on dictation-style turns). The skip consumes no phrase,
and the reply that answers the completed sentence arms its own timer.

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

**A stalled generation is retried, then dropped.** An LLM whose stream
shows no sign of life within `llm_first_token_timeout_s` (ten seconds
by default) has its request cancelled and the round retried once,
logged as `llm_retry`; a second stall gives the round up as a
`provider_failed` with `error: FirstTokenTimeout` and the session goes
back to listening, so the worst a stalled provider can cost is one
silent turn. Only the wait for the stream to begin is bounded: a long
reply that is already streaming runs to the end, a round that streams
nothing but a tool call counts as delivering too, and barging in still
cancels a stalled round the way it cancels anything else. The
reasoning behind the default is in `config.example.yaml`.

## Logging

Two formats, one handler:

```yaml
server:
  log_format: text   # or json, which is the container image's default
  log_level: INFO
```

Every event is logged as a human sentence and, in `json` mode, as a line
of structured fields. Every record carries `event`; the ones a
conversation emits, on the `vinga_server.session` channel, carry
`session` and `device` beside it, and the server's own channels carry
either only where the record is about one.

Which fields an event carries, which tokens a reason field admits, what
each of them is held to and which sentence it renders are the generated
[event schema reference](../docs/reference/events.md), one section per
event and one subsection per shape it may be emitted in. It is generated
from the declarations themselves, so it cannot say anything they do not.
This index is the other half: what exists, and when it fires.

| `event` | when |
| --- | --- |
| `ota_check` | a device checks in (no session yet, so the record names the device) |
| `activation_not_offered` | an unbound device is answered with no activation code, and why |
| `activation_complete` | a waiting device has been claimed; its next check hands it a token |
| `activation_pending` | a waiting device polls and is still waiting |
| `activation_refused` | a version-2 activation poll fails one of the checks this server can hold it to; nothing of the body is ever quoted |
| `ota_request_rejected` | a request this endpoint could not read, refused with one of three fixed sentences |
| `onboarding_banner` | where devices are configured, said once at startup |
| `onboarding_key_mismatch` | a request reaches the onboarding path carrying a key-shaped segment that is not this server's; neither is repeated |
| `onboarding_key_unshaped` | the same path, carrying something that is not key-shaped at all |
| `auth_rejected` | a handshake is refused before the accept; no device, since nothing is authenticated yet and the Device-Id header is whatever the caller sent |
| `session_rejected` | a device is turned away, either by the endpoint before a session can run (`vinga_server.ws`) or by the session after the accept (`vinga_server.session`) |
| `session_open` | a conversation starts |
| `session_limit` | the duration cap fires |
| `session_idle` | the idle timeout hangs up on a realtime session |
| `session_closed` | a conversation ends |
| `speaking_started` | the reply's first audio frame goes out |
| `heard` | an utterance is transcribed. No transcript: what was said is the conversation store's |
| `replied` | a reply finishes |
| `agent_said` | one agent's part of a reply |
| `handover` | `switch_agent` succeeds |
| `prompt_assembled` | the know-how half of a prompt is assembled and cached, with each block's size by provenance |
| `llm_retry` | the first-token watchdog cancels a stalled generation and retries the round once |
| `llm_round` | a generation call finishes |
| `provider_failed` | an ASR, LLM or TTS call fails; a round whose retry also stalled carries `FirstTokenTimeout` |
| `tool_call` | a tool returns |
| `barge_in` | speech cuts a reply short |
| `barge_in_suppressed` | an interruption is dropped and the reply lives |
| `barge_in_merged` | an interruption merges with the utterance the reply was transcribing |
| `filler_skipped` | the filler timer fired but the user was there first, so no clip played |
| `filler_played` | the reply was slow, so a pre-synthesized filler clip masked the wait (its first frame is the turn's `speaking_started`) |
| `asr_prompt_echo` | a transcript came back as the ASR prompt and the clip was retried once without it, on what the first request left of `timeout_s` (no session or device: providers serve them all) |
| `mcp_connected` | an MCP entry's connect finishes and its tools are published (no session or device: one entry serves every conversation, and the rest of this block is the same) |
| `mcp_down` | an MCP entry fails to come up, or its connection is given up. `stopped` is the intentional one (a shutdown or a reload) and the only one at INFO |
| `mcp_call_dropped` | a tool call failed and the connection was dropped because of it, always beside an `mcp_down` with `call_failed` |
| `mcp_tool_shadowed` | a published tool is dropped because a more specific entry owns its name |
| `mcp_reload` | a reload of the MCP servers finishes, whether or not the caller is still connected |
| `memory_unreadable` | an agent's memory could not be read, so it remembers nothing this round |
| `filler_disabled` | filler synthesis failed for one agent, so latency masking is off for it |
| `capture_started` | a session is being recorded |
| `capture_declined` | a session is not being recorded, and why |
| `capture_limit` | a recording reaches its per-session ceiling |
| `capture_failed` | a recording stops after a write failed |
| `capture_pruned` | old recordings are removed to stay inside the disk budget |
| `capture_over_budget` | the disk budget is exceeded and nothing more can be pruned |
| `capture_enabled` | capture is on, said once at startup and at WARNING: recording room audio is not something to discover by accident |
| `capture_disabled` | capture is configured but off |
| `conversations_enabled` | the conversation store opens at startup, which means this server is recording what is said to it (no session or device: it is said once, before anything connects) |
| `conversations_dropped` | the store is behind and events for one session are being dropped, said once per session at its first drop; the total lands on that session's row |
| `conversations_failed` | a write to the store failed and its batch was dropped, or a prune could not run |
| `conversations_pruned` | retention deleted sessions older than the window (at INFO: a policy doing its job) |
| `drain_started` | a shutdown begins draining |
| `drain_finished` | every reply finished speaking |
| `drain_incomplete` | a reply was cut, or a session hung |
| `device_bindings_snapshot_only` | there is no configuration database, so device bindings resolve from the world this server is serving |
| `device_bindings_unreadable` | the configuration database could not be read, so the answer is the served world's and may be older |
| `api_error` | the configuration API failed to handle a request; the class name and nothing else |
| `api_storage_error` | the configuration API met unreadable stored state |

No MCP event names a tool, and none of them can. Half of a published
tool name is whatever the far side called it, sanitizing replaces only
the characters both LLM APIs refuse, and an alphanumeric credential goes
through that untouched, so a server handed one of your own could put it
in the logs you keep by listing a tool under it. Every line about a
single tool therefore says which one by its position in that server's
listing. `vinga-server config status` prints the names themselves, to a
terminal, when you ask it.

Every event above is declared: its channel, its level, the sentence it
renders, the arguments that sentence takes and every field it may carry,
with closed sets for the fields that hold a reason token. The reference
this index points at is those declarations rendered, and CI regenerates
it and refuses any difference. The emitters build each emission inside a
guard, so an emission that could not be built costs one line on the
emitter's own channel, naming a fixed label and a fixed code and nothing
about the emission itself; it is dropped rather than written in some
other shape, because a telemetry bug must never cost a reply.

These events are metadata, and metadata only. What was said is in the
conversation store, keyed by the same `session`: query `turns` there for
the transcript and the reply, and `tool_invocations` for what a tool was
asked and what it answered. Filtering the logs for it no longer works,
and that is the point (see the [content and telemetry
ADR](../docs/adr/2026-08-15-content-and-telemetry-are-separate-surfaces.md)):
a surface with no free-text field cannot leak one. What the events keep
is what a latency brief reads, which is every duration, every count and
every identifier they ever carried. Tokens are never logged, at any
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
`VINGA_SERVER__CAPTURE__ENABLED=true` turns it on for one run without
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
It is on the WAV, and the `heard` event beside it in the decision track
points at the interesting twenty seconds instead of ten minutes of
scrubbing; with the conversation store on and `text: true`, the phrase
itself is one query away, since both records carry the same session id. Copy the three files off
after each session; a field recording is not repeatable.

## The conversation store

**This keeps what was said in a database.** It is off by default and off
until `enabled` says otherwise, and a warning at startup says when it is
on and names the file.

```yaml
server:
  conversations:
    enabled: false
    # store the structured events and every measured number
    metrics: true
    # store conversation text, and tool names, arguments and results
    text: true
    # prune whole sessions older than this; 0 keeps everything
    retention_days: 90
```

What lands is `conversations.db`, beside `vinga.db` in
`server.database.dir`: one row per session (device, agents, protocol,
the resolved providers, when it opened, when and why it closed), one row
per turn (what was heard and what was replied, the ASR, LLM and TTS
timings, the rounds and the token counts), one row per tool call a turn
made (its source, its arguments and its result), and one row per
structured event, which is the same decision track the capture writes
beside its audio. Audio never enters it: the capture is the recording,
this is the queryable record. The columns are documented in
[`../docs/reference/conversations-schema.md`](../docs/reference/conversations-schema.md),
generated from the schema itself, and `vinga-server conversations
schema` prints the same document.

The section's absence, and `enabled: false`, both mean the same thing:
nothing is recorded and no file is created. An existing file is still
brought up to the current schema at every start, because switching
recording off does not make what was already recorded unreadable.

The two switches under the flag are independent, and all four
combinations are supported configurations:

| `metrics` | `text` | What a session keeps |
| --- | --- | --- |
| on | on | everything: the events, every measured number, and what was said |
| on | off | the events and the numbers; the text columns, tool names, arguments and results are null |
| off | on | what was said, with no events rows and the numbers null: the transparency-first setting |
| off | off | the session spine and the shape of each turn, and nothing else |

Session rows land in every enabled configuration, because retention,
purging and every read key on them, and their timestamps survive both
switches for the same reason. Each session row also records which way
the switches were set for it, so a null column is distinguishable from
a column that was never stored.

**The switches are deployment-wide, and they are the only privacy
control this release has.** Until per-user controls exist, enabling text
storage on a device a household shares stores what guests say to it,
which is the same statement the capture section makes about audio.
Attributing a session on a shared device to one member needs voiceprint
identification, which does not exist here yet, so the deletion unit that
is enforceable today is the session, and the session id is surfaced
everywhere: on the events, on the capture triplet's filenames, and as
the purge command's selector.

Retention is 90 days by default: whole sessions older than the window
are deleted, row and children together, at startup and at each session
close, and a line says how many went. `retention_days: 0` keeps
everything, which is a deliberate choice rather than a default, because
a store with no policy retains forever.

Deletion on demand is one command, and it works with no server running,
because deletion has to work exactly when the server is broken or gone:

```console
$ vinga-server conversations purge --session 3ab9e1a12f584dd8a6cae5c1f8e618b2
$ vinga-server conversations purge --device aa:bb:cc:dd:ee:ff --before 2026-08-01
```

Selectors combine with AND, and at least one is required. Purging a
session that is still running ends its recording: the writer finds the
row gone and stops writing for that session, so what is said afterwards
is not recorded. Capture files are a separate instrument and are never
touched; the session id is the correlation key for whoever needs to
remove the matching triplet.

A purge deletes what is recorded, and a session that has only just
started may not be in the file yet: the writer commits the session row
moments after the conversation opens, so a purge arriving inside that
window reports deleting nothing and the row lands behind it. Run it
again if the counts came back zero for a session you know exists. This
is a queue-latency window rather than a durable state: everything the
session then records is ordinary rows, deletable by the same command.

Deletion is physical rather than query-level. The database runs with
`PRAGMA secure_delete=ON`, so a freed page is overwritten with zeros
instead of lingering in the freelist, and both retention and purge
finish with `PRAGMA wal_checkpoint(TRUNCATE)` so the deleted frames do
not survive in the write-ahead log. Two limits, stated rather than
implied: a checkpoint a reader is blocking does not fail the deletion,
which is committed either way, and the truncation is retried at the next
quiet moment (the purge command says so when it leaves one owed); and
copies that have already left the file are yours to manage.

**Read it with SQL over a WAL-safe copy, never a plain `cp` of a live
file.** The database runs in WAL mode, where a copy on its own can miss
committed data still sitting in the `-wal` file, exactly as for
`vinga.db`:

```bash
sqlite3 /var/lib/vinga/conversations.db ".backup '/tmp/conversations.db'"
sqlite3 /tmp/conversations.db 'select * from turns order by id desc limit 20'
```

There is deliberately no analysis command: the store is SQL, and the ids
on `sessions`, `turns` and `events` are monotonic and never reused, so a
client that has read up to one can ask for what came after it.

Writing never happens on the conversation's path. One background thread
does every database call behind a queue nothing on the session loop ever
waits on, and it commits at turn boundaries and at session close, so a
page opened mid conversation reads everything up to the last completed
turn. A database that is wedged or locked drops events, says so once per
session, and records the count on the session row, and it never delays a
reply.

Turns and closes are never refused at the queue, whatever the backlog:
they are the record's structural truth and they arrive at conversational
pace. That is not a promise that a close always lands. A close whose own
transaction fails leaves the session row open-shaped, with a null
`closed_at` and no close reason, which is the same incomplete state a
process killed mid-session leaves behind: it is readable, it is listed,
and retention prunes it on `started_at` like any other. A line at
warning level says so when it happens.

## Which build is running

`version` is the package version and has read `0.1.0` since the package
skeleton. `revision` is which build of it, and it is the field that
distinguishes one deploy from another.

```console
$ curl -s localhost:8003/healthz
{"status":"ok","version":"0.1.0","revision":"a1b2c3d"}
```

**A running pod's revision equals its image tag's suffix**: a container
from the image tagged `sha-9fd3de5` reports `9fd3de5`, so a post-deploy
check is an equality check. CI passes the same seven characters
`docker/metadata-action` puts in the tag, computed from one expression,
so the two cannot drift. It used to pass the full 40-character SHA,
which made the match a prefix check; a deployment scripted it as
equality, which is the natural reading, and got a false failure.

It also rides every `session_open` event, which is the widest payoff for
one field: the JSON logs already ship to a collector, so every session is
attributable to a build rather than only the ones somebody thought to
investigate. Two field recordings that behaved differently are otherwise
indistinguishable from one code change and two different rooms. The OTA
reply carries it too, under `server`, which is the one place a device is
told what it is about to talk to.

The value is resolved once at startup, in this order:

1. `VINGA_REVISION`. The published image bakes in the commit its tags
   are computed from, so `/healthz` and the image's `sha-` tag agree.
2. `git describe --always --dirty`, which covers running from a working
   tree. A tree with uncommitted changes reports `-dirty`, because a
   build running code that is not any commit is exactly when knowing
   matters.
3. `unknown`. An image built with no build argument runs and says it does
   not know; it never fails to start over it.

Building an image yourself:

```console
docker build --build-arg VINGA_REVISION=$(git rev-parse --short HEAD) -t vinga-server .
```

## Running in a container

The default image carries both local engines, so one mounted YAML and
one seeded database serve a conversation. The server starts first, on
whatever the database holds (nothing, the first time, which is a valid
state to serve), and the domain half is written into it with the CLI
inside the running container, where the API token and the loopback
address are already in the environment:

```bash
docker run -d --name vinga \
  -p 8003:8003 \
  -e VINGA_API_SECRET \
  -e VINGA_AUTH_SECRET \
  -v /path/to/config.yaml:/config/config.yaml:ro \
  -v vinga-data:/data \
  ghcr.io/rafacm/vinga-server:latest

docker exec -i vinga vinga-server \
  config set provider llm claude -f - < examples/llm-anthropic.yaml

docker restart vinga
```

- `/config/config.yaml` is where `VINGA_CONFIG` points, and it is the
  server half. Mount it read-only; override any key of it with a
  `VINGA_`-prefixed environment variable.
- `/data` is the volume every engine caches into (`HOME` points there):
  whisper models and Piper voices download at first start and survive a
  new image. Model weights are never baked in.
- The configuration database lives on that volume too: the image sets
  `VINGA_SERVER__DATABASE__DIR=/data/db`, since the volume is the only
  place an unprivileged user with a read-only root filesystem can
  write. It is created and migrated on first open, so there is no init
  command to forget.
- Logs default to `json` in the image, which is the only default that
  differs from running it directly. Override with
  `VINGA_SERVER__LOG_FORMAT=text`.
- The healthcheck assumes the default port; change `server.port` and
  override `--health-cmd` too.
- A read-only root filesystem works: add `--read-only --tmpfs /tmp` and
  keep the two mounts.
- Stop it with `docker stop -t 30 vinga`, above `drain_s`, so
  conversations in flight finish their sentence.

Behind a TLS-terminating proxy, either set `server.websocket_url`
explicitly or pass the proxy's address in `FORWARDED_ALLOW_IPS`, which
uvicorn honours from the environment.

### The configuration database in a deployment

Five things a deployment has to get right about it, none of which the
server can decide for you.

**The master key is generated once and escrowed.** Set
`VINGA_MASTER_KEY` wherever the deployment keeps its environment
secrets, alongside `VINGA_AUTH_SECRET`:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

It is only needed once a credential is stored encrypted; a deployment
whose keys are all environment references never needs one. Once
ciphertext exists, losing the key means losing those credentials: the
server refuses to start with a stored secret it cannot open, naming the
entity and the slot. That refusal takes the API with it, so the way back
in is `--local`, with two choices per slot.
`config --local clear-secret` drops the envelope and needs no key, which
is enough when the entity carries an environment reference for the same
slot or can go without.
`config --local set-secret` writes the value again and needs a usable
key in `VINGA_MASTER_KEY` to encrypt under; it need not be the
lost one, because what the next boot needs is a key list that opens
every envelope still stored, which means every secret written under the
lost key has to be set again or cleared.

**Rotation adds a key, and this release cannot retire one.**
`VINGA_MASTER_KEY` holds a comma-separated list, newest first;
encryption always uses the newest and decryption tries them in order. A
new key therefore only affects secrets written after it, so every old
key must stay in the list for as long as any token written under it
remains in the database. Re-running `config set-secret` for each stored
secret rewrites it under the newest key, which is the interim path
until a re-encrypt command exists.

**Backups use SQLite's own mechanisms, never a plain copy of a live
file.** The database runs in WAL mode, where a copy of `vinga.db` on
its own can miss committed data still sitting in the `-wal` file:

```bash
sqlite3 /data/db/vinga.db "VACUUM INTO '/backup/vinga-$(date +%F).db'"
```

`.backup` does the same job through the backup API. A plain `cp` is
only safe against a stopped, checkpointed database. Back it up with the
memory directory, which is ordinary files on the same volume.

**A restore needs both halves of the secret.** The backup file, and
every master key still required to decrypt what it holds, which is why
the keys are escrowed with the deployment's other environment secrets
and separately from the backup itself. A restored database with no key
is a configuration whose credentials will not open.

**What a copy of the file exposes.** No stored plaintext secret: the
encrypted values are ciphertext and the environment references are
variable names rather than values. It does expose the rest of the
domain configuration, which is to say the prompts, the endpoints and
the variable names, so the file belongs on the data volume and in
access-controlled backups, not in a repository.

And the operational one, said again because it is the trap of a
configuration a running server is not re-reading on its own: **an edit
is stored and changes nothing until something applies it.** A `config
set` against a running deployment is accepted by that server and is not
in effect when the command returns, which both the command and the API's
answer say every time they write. There are two ways it becomes
effective, and each write says which case it is in: everything in the
domain half, from a provider entry to an agent to the defaults under
them, reaches a running server when it is asked to reload; a device
binding and the default agent reach it at that device's next check-in,
with nothing asked of the server. The one thing an agent carries that a
reload does not move is its memory, which is keyed by its name, so
renaming an agent still orphans what it remembered.

### The configuration API in a deployment

Four more things, about the surface that writes it. What the API is and
what it serves is under [The configuration API](#the-configuration-api);
this is what a deployment has to decide about it.

**Set `VINGA_API_SECRET` before rolling the image, not after.** The API
is always mounted and always gated, so an image from this release
started without that variable does not come up. It is the one upgrade
step this change forces, and the boot error is the safety net rather
than the plan: it names the variable, prints
`VINGA_API_SECRET=$(openssl rand -hex 32)`, and says where the value
goes. Generate one, put it wherever the deployment keeps
`VINGA_AUTH_SECRET` and `VINGA_MASTER_KEY`, and then roll the image.
`server.api.secret_env` renames the variable for a deployment whose
convention is another one.

**Decide what happens to `/api/` at the edge.** It is on the same port
as the device endpoints, because the server is one process, so anything
routing that port outward routes the admin surface with it unless it is
told otherwise. Three answers, in the order they are worth reaching for:

- **Do not route it externally at all.** The device endpoints are the
  only two that need to be reachable from outside, so route
  `/xiaozhi/ota/` and `/xiaozhi/v1/` and let `/api/` be reachable only
  from inside. Configure it by exec into the running container, or by
  forwarding the port to your own machine for the length of a session.
  This is the default worth defending: the surface with the most
  authority is the one nothing outside can address.
- **Route it separately and restrict it**, when a front end or an
  operator genuinely needs it from outside: a route of its own for the
  `/api/` prefix, TLS on it, and whatever source restriction the edge
  can express, so that the bearer token is not the only thing between
  the internet and the configuration.
- Route the port as one thing and rely on the token alone. That is what
  happens by accident, and it is worth choosing deliberately if it is
  what you want, because a token in a client's environment is a token
  in more places than a private address is.

**Loopback or TLS, for the whole API and not only for secret writes.**
The bearer token rides on every request and grants everything the API
can do, `set-secret` included, so a plain `http://` request to it from
another machine puts the token on the wire in clear. This is a rule the
client enforces rather than recommends: `vinga-server config` refuses a
plain `http://` URL whose host is not a loopback address, with no flag
to override it. The machine's own address on the network is not one of
those, and neither is a name that resolves to loopback: the check reads
the host as written. Reach the API over `https://`, through a tunnel
that terminates TLS, or on loopback from inside the container, which is
the case the default address is built for.

**When the server will not start, `--local` is the way back in.** A
configuration the server refuses to boot on (a stored secret no
configured key opens, an entity that cannot be loaded, a reference that
no longer resolves) leaves nothing to write through, which is the one
situation the API cannot answer for. The recovery path opens the
database directly and covers four commands:

```bash
# What is stored.
vinga-server config --local show
# Take out what will not load.
vinga-server config --local delete agent broken
# Repair a credential.
vinga-server config --local clear-secret provider llm claude api_key
vinga-server config --local set-secret provider llm claude api_key
```

`set-secret` reads the value from stdin, or from a named variable with
`--from-env`, and never from an argument. `--local` needs the master key
only for `set-secret`, and needs no API token and no running server at
all. Every other command refuses the flag by naming these four.

**Run them in a container of their own**, because the container that
serves is the one that will not start, so there is nothing to exec into.
Same image, same mounted YAML and same data volume, with the command
replaced: the image's entrypoint is `vinga-server`, so what follows it
is the command line above.

```bash
docker run --rm -i \
  -v /path/to/config.yaml:/config/config.yaml:ro \
  -v vinga-data:/data \
  ghcr.io/rafacm/vinga-server:latest \
  config --local show

# set-secret is the one that needs the key, and reads the value on stdin
# (-i is what gives it one).
docker run --rm -i \
  -e VINGA_MASTER_KEY \
  -v /path/to/config.yaml:/config/config.yaml:ro \
  -v vinga-data:/data \
  ghcr.io/rafacm/vinga-server:latest \
  config --local set-secret provider llm claude api_key
```

The YAML is mounted because the image points `VINGA_CONFIG` at
`/config/config.yaml`, and a command that finds nothing there refuses
rather than guessing. Which directory it then opens comes from the
image's own `VINGA_SERVER__DATABASE__DIR`, `/data/db`, which is why the
volume is the mount that matters. No port is published and no secret
beyond the master key is passed, because nothing here serves anything or
reaches the API.

Every `--local` invocation prints one line on stderr saying that it
bypasses the API, and every write under it then says when it takes
effect, the same sentence the API answers that act with: the next server
start, the next `config reload` for an MCP entry, a secret on one, a
prompt fragment or an agent's prompt, or the device's next check-in for
a binding. That is not a warning about a hazard: it is the start-time
contract and its two exceptions, said out loud at the one moment an
operator is most likely to expect otherwise. The line itself is printed rather than enforced
because there is no reliable way to tell whether a server is running
against the same file, and a wrong refusal would wedge the recovery path
in exactly the situation it exists for. Concurrency is safe regardless:
each write is one transaction, so a `--local` write racing a server's
own serializes with it, and the one that arrives second waits for the
lock and then commits. The retryable refusal is what a writer meets only
if the lock has not come free inside the 10 second busy timeout, and it
says nothing was changed and to run the command again.

Restart the server when the repair is done. That is the point of the
path: it exists for a server that will not start, and starting it is
the goal. It is not that a running server could observe none of this,
which the write itself says: an MCP repair made this way is applied by
`vinga-server config reload` like any other, and a binding is read at
the device's next check-in.

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

## Onboarding a device

A device running stock xiaozhi firmware knows one thing about its
backend: the OTA URL, held in NVS (namespace `wifi`, key `ota_url`).
Everything else reaches it from the reply to that URL: the WebSocket
URL, its token and protocol version, and the wall clock. So onboarding
a board is two questions: what URL does it get, and which agent does it
talk to.

Both are answered without a cable and without looking up a MAC address.

**1. Ask for the URL to type.**

```bash
vinga-server config ota-url
# http://192.168.1.10:8003/x/AB2C4D5E/
```

The command contacts nothing: it reads the same config file the server
reads and derives the same key from the same device-auth secret, so it
answers before the first start and while a board waits on a bench. On
stderr it says where the origin came from, and an origin nobody
configured reads as the guess it is: set `server.public_url` to name the
deployment exactly.

This is the one place the URL is printed. The running server's startup
line names the origin and points here rather than repeating the URL, and
a GET of the endpoint repeats it only to whoever already reached it: the
key stands in front of the endpoint that issues device tokens, and a log
line is kept, shipped and read by everyone who can read logs.

The two inputs are the file and the secret, so it runs wherever both
are. Inside the container they already are:

```bash
docker exec -i vinga vinga-server config ota-url
```

On a machine with neither a checkout nor an installed server, `uvx`
fetches the CLI, and the file and the secret are yours to hand it:

```bash
VINGA_AUTH_SECRET=$(cat ~/.vinga-auth-secret) \
  uvx --from "git+https://github.com/rafacm/vinga#subdirectory=vinga-server" \
  vinga-server config --config ./config.yaml ota-url
```

That resolves the whole server, dependencies and all, to run a command
that opens no socket; a slim redistribution of the CLI is a follow-up
rather than a thing that exists. It also resolves without this
repository's lockfile, so it takes the newest dependencies its
constraints allow rather than the tested ones. In a checkout,
`uv run vinga-server config ota-url` from `vinga-server/` is the
lockfile's own environment and the one this repository tests.

Eight characters, in an alphabet with no `0`/`O` and no `1`/`I`/`l`,
because this string gets typed on a phone keyboard off a small display.
It is derived rather than stored, so it survives restarts and changes
only when the device-auth secret does.

**2. Check what answers there**, before typing it into anything:

```bash
vinga-server doctor
# http://192.168.1.10:8003/x/AB2C4D5E/ is vinga-server 0.1.0, and sends
# devices to ws://192.168.1.10:8003/xiaozhi/v1/ (protocol version 1).
```

With no argument it checks the URL above; give it one to check any
other. It is a GET and never a POST, so it mints nothing, and it
reports what a device would be told: nothing answers there, something
other than vinga-server answers, vinga-server answers but sends
devices to a plain `ws://` URL from behind TLS (see
[Behind a reverse proxy](#behind-a-reverse-proxy)), or it is healthy.
Healthy exits 0 and the rest exit 1.

**3. Type it into the board's captive portal.** A board with no Wi-Fi
provisioning brings up its own access point; join it, and the portal
offers the network form plus an advanced section holding the server
address. Put the URL there. Which button starts that portal and what
the board shows while it waits are per-board facts, and they are in
[`../docs/devices/`](../docs/devices/README.md).

A portal that saves the address without its trailing slash is fine:
every device-facing route answers both spellings itself, and none of
them redirects, because the firmware does not follow a redirect on
these requests.

**4. Read the six digits off the board, if it shows any.** A device the
configuration resolves to no agent is answered with an activation code
instead of a token: the firmware shows it and speaks it, and re-checks
every half minute to two minutes, so the number on the screen is always
the current one. A board a `default_agent` already covers shows no code
and connects straight away, which is the case just below.
`vinga-server config pending` lists every board waiting, with the board
type and firmware version each one reported, which is how two boards on
one desk are told apart.

**5. Bind it, by the code rather than by the MAC:**

```bash
vinga-server config add-device 418293 assistant
# wrote device aa:bb:cc:dd:ee:ff bound to assistant
```

The device polls every three seconds while it waits, so it connects
seconds later with no restart and no power cycle. `bind-device` is the
same write for a MAC you already know; `add-device` is for the board in
front of you.

**Which devices are offered a code.** Exactly those the database
resolves to nothing: no binding row of their own, and no
`default_agent` set. A deployment with a default agent covers every
unknown board by design, so its devices are handed a token straight
away and never see a code, which is also why nothing changes for a
deployment that upgraded into this. Turning `server.onboarding.enabled`
off removes both the short URL and the code ceremony.

**On a fresh deployment, write the agent and apply it.** A server
serves the world it last installed, so an agent written into an empty
database is not being served yet: bind a board to it and the
acknowledgement says which reload installs it rather than promising
otherwise. `vinga-server config reload` is that reload, and from there
the board connects at its next check-in, seconds later; no step of a
deployment's life needs a restart once the process is up.

**Already-provisioned boards keep working.** A board carrying a full
`ota_url` in NVS reaches `server.ota_path` exactly as before, and both
routes serve the same endpoint. Rewriting NVS wipes a board's Wi-Fi
provisioning, so there is no need to move a fleet at all; a board that
is being reprovisioned anyway can be given the short URL instead. Once
no board needs it, `ota_path: null` unmounts the legacy route and
leaves one way in.

**A rotated device-auth secret changes the key**, because the key is
derived from it. Boards already connected do not care: they hold their
own full URL. What needs the old key is a board being onboarded through
the URL somebody wrote down before the rotation, and
`server.onboarding.key` pins it for exactly that. A wrong key answers
404, byte for byte what a path that was never served answers, while
logging the attempted key beside the correct one, so a typo and a
rotation both diagnose themselves in the server's own log.

**The WebSocket URL** is derived from the address the device reached
the OTA endpoint on, so a LAN deployment needs no extra configuration.
Set `server.websocket_url` when the server sits behind a proxy or a
name the request headers do not carry.

vinga-server serves no firmware images: the reply always tells the
device it is up to date.

The ceremony above has been driven end to end against a simulated
device and a served server. The checkpoint on a factory-firmware board,
which is what turns "the firmware shows the code" from a reading of
upstream's sources into an observation, is still open; the procedure
and every serial gotcha are in
[`../docs/xiaozhi-notes.md`](../docs/xiaozhi-notes.md).

## Transports

**WebSocket only.** The device speaks Opus over one WebSocket, and that is
the only transport vinga-server implements or plans for v1.

Upstream supports a second one, **MQTT plus UDP**: the OTA reply carries an
`mqtt` section instead of a `websocket` one, control messages go over MQTT
and audio over a separate UDP stream. vinga-server never sends an `mqtt`
section, so devices always take the WebSocket path. Supporting it later is
additive and needs no change to what exists: the OTA endpoint would choose
which section to send per device.

**WebRTC is not an upstream transport.** The only WebRTC reference upstream
is the WebRTC/NSNet noise-suppression algorithm in the device's audio front
end (and it ships disabled). A WebRTC transport would be new work on both
sides, not adoption of something the firmware already speaks.

## Ports and topology

Both endpoints share one port (`server.port`, default 8003), because
vinga-server is a single ASGI app. Upstream splits them across two (HTTP
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
  same thing. `vinga-server doctor` is what says this has
  happened: it names a `ws://` websocket URL behind an `https://` OTA
  URL as the fault it is, rather than leaving a board failing at the
  handshake with every other line looking right.
- **Set `server.public_url` too.** TLS ends at the proxy, so nothing a
  request carries says what a person should type; without it the
  onboarding URL is derived from `server.websocket_url`, and failing
  that guessed from the listen address, which is a guess that says so.
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

vinga-server serves conversations end to end: OTA and WebSocket
endpoints, the VAD/ASR/LLM/TTS pipeline on pluggable providers, agents
bound to devices, MCP tools on both sides, device authentication,
onboarding by a short URL and an activation code, limits,
structured logging, and a published multi-arch container image. The v1
plan and its per-milestone implementation notes live in
[`docs/plans/`](../docs/plans/); setup notes for a device on your desk are
in [`../docs/xiaozhi-notes.md`](../docs/xiaozhi-notes.md).
