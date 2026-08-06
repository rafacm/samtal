# OpenAI ASR provider

## Problem

The last of the three cloud providers in issue #11, and the first that
is not a voice. Outside the LLM stage the ASR stage had exactly one
implementation, `faster_whisper`, which means either a GPU to provision
or a CPU decode slow enough to be felt between the user finishing a
sentence and the assistant starting one, plus a model download at
startup. A self-hoster who wanted better multilingual accuracy had no
configuration that reached it.

The packaging questions were answered on #11 and did not have to be
reopened: cloud providers ship in the existing image, with no extras
group and no image variant.

## Changes

A new `asr` provider type, `openai`, transcribing one whole utterance
through `POST /v1/audio/transcriptions`.

**No new dependency**, for the reason the `openai` TTS type needed
none: the `openai` client is already a core dependency, carried for the
`openai_compatible` LLM type, and transcription is a method on the
client that already ships. One key now serves all three network stages.

**The endpoint rules moved to a shared module first.** The TTS type
derived three answers from its `base_url` (is a key required, do the
type's model rules apply, does session data leave the host), and this
type derives the same three from the same option. Those belong to the
endpoint rather than to either stage, so `providers/openai_endpoint.py`
now holds the host comparison, the key resolution and the retry policy,
and the TTS provider calls them. Behaviour is unchanged and its suite
passes untouched. `openai_compatible` stays out of it: it requires
`base_url` rather than defaulting to OpenAI and has no rule that turns
on the host, so it has nothing to share but a constant.

**The audio goes up as WAV**, which is a 44 byte header in front of the
samples the stage already holds. The endpoint takes a file rather than
a buffer and a rate, and WAV is the one accepted format that carries
s16le PCM as it is; every other choice would mean encoding audio for
the far end to decode again, at the cost of a dependency and latency.
The header is written from the `sample_rate` argument, so unlike
`faster_whisper` (which refuses anything but 16 kHz) this provider
follows whatever the pipeline is running at. `wave` from the standard
library writes it, so there is nothing to hand-verify about the header.

**It does not stream, and that is a decision.** #11 asks a network
provider to stream or to justify not streaming. The stage's interface
is one utterance in and one transcript out, because the LLM stage
cannot begin on half a sentence; and the utterance is already complete
before `transcribe` is called, since the endpointer is what decides the
call happens at all. Streaming the response would deliver text deltas
nothing downstream can consume. The TTS stage is the opposite case, and
does stream.

**It does not detect language, and that costs nothing it would
otherwise buy.** The model still recognises whatever is spoken with
`language` unset; what is missing is the report. A language comes back
only for `whisper-1` asked for `verbose_json`, which the gpt-4o models
do not support, and it arrives as an English name ("swedish") rather
than the ISO code the rest of the pipeline speaks. No model reports a
confidence at all, and `language_confidence` is what the session's
floor logic is built on. So `AsrResult`'s language fields stay empty
and `lock_language` is never asked for. The lock exists to spare
faster-whisper a constant encoder pass per utterance (#22); here
detection happens inside the model at no measurable cost, so there is
nothing to spare.

**Audio under 0.1 s is answered empty without a round trip.** The
barge-in path is what sends it: a snippet classified as speech during a
reply is transcribed to decide whether the interruption was real (#28),
and the shortest of those are tens of milliseconds. Verified against
the real API rather than assumed, since the documented minimum and the
observed one need not agree.

**Egress marking.** The type carries `egress = None` like the TTS type
and `openai_compatible`, because `base_url` decides: a self-hosted
transcription server keeps the audio on the host, `api.openai.com` does
not. An entry under `server.local_only` declares its own.

## Key parameters

| Option | Default | Notes |
| ------ | ------- | ----- |
| `api_key_env` | required for OpenAI itself | Environment variable holding the key |
| `model` | `gpt-4o-mini-transcribe` | `gpt-4o-transcribe` is the larger sibling; `whisper-1` is the same Whisper V2 as the local engine |
| `base_url` | `https://api.openai.com/v1` | Any server implementing `/v1/audio/transcriptions` |
| `prompt` | unset | Vocabulary the engine would not otherwise guess |
| `language` | unset | ISO 639-1. A hint on the gpt-4o models, not a pin |
| `temperature` | unset | 0.0 to 1.0, range checked only against OpenAI itself |
| `timeout_s` | `30` | Bounds a turn, and a real bound because retries are off |

Sample rate is not an option: the WAV header carries whatever the
pipeline passes to `transcribe`.

## Verification

Reference audio was synthesized with the ElevenLabs provider at
`pcm_16000`, which is the pipeline's own rate and format. Synthesized
speech is cleaner than a room, so it is used for the latency figures
and for the round-trip check, and the accuracy claim is made on the
degraded versions below instead.

**Latency, median of five rounds per utterance, warm, one machine and
one network:**

| Utterance | `faster_whisper` small | `gpt-4o-mini-transcribe` | `gpt-4o-transcribe` | `whisper-1` |
| --- | --- | --- | --- | --- |
| "The kitchen light is now off." (1.8 s) | 1688 ms | 536 ms | 627 ms | 1076 ms |
| "Hello, I am your samtal assistant..." (3.6 s) | 1781 ms | 658 ms | 887 ms | 1177 ms |
| "Hej, jag är din samtalsassistent." (2.1 s) | 1743 ms | 545 ms | 607 ms | 1101 ms |

This is the finding that makes this provider unlike the two voices:
going to the cloud to listen is roughly a second per turn *faster* than
the local engine, not slower, because the work runs on someone else's
accelerator and the round trip is cheaper than an int8 CPU decode. The
local column is a laptop without a GPU, and the documentation says so,
because that is the number a reader has to check against their own
hardware. The cloud column would barely move on better hardware, since
almost all of it is the round trip.

Two model notes worth recording. `whisper-1` through the API is about
twice the latency of the gpt-4o pair, so hosted Whisper V2 is the worst
of both worlds: it is neither local nor fast. And the larger
`gpt-4o-transcribe` costs little over `mini` on short utterances, which
is why the option table recommends it for noisy input without a latency
caveat.

**Accuracy under noise.** The three utterances mixed with white noise
at three signal-to-noise ratios, standing in for a far-field
microphone. Only the Swedish sentence separates the engines:

| Signal to noise | `faster_whisper` small | `gpt-4o-mini-transcribe` | `gpt-4o-transcribe` |
| --- | --- | --- | --- |
| clean | exact | exact | exact |
| 10 dB | "din samhållssystem" | exact | exact |
| 5 dB | "din samhållssystem" | exact | exact |
| 0 dB | "Okay, you are adding some help from the system." | exact | exact |

The local `small` model does not merely mishear at 0 dB, it leaves the
language. Both gpt-4o models returned "Hej, jag är din
samtalsassistent." exactly at every level. English survived on every
engine at every level, which is the honest other half: this buys most
where the local models are weakest, and a monolingual English
deployment has less to gain.

**The one thing this provider gets wrong, and its fix.** An unfamiliar
proper noun. Under noise "samtal" came back as "sample"; with
`prompt: samtal` on the same audio it came back as "Samtal". This is
what the `prompt` option is for and why the documentation tells
operators to set it rather than listing it neutrally.

**`language` is a hint, not a pin.** Swedish audio with `language: en`
still came back in Swedish, where the local engine would have forced
English and produced nonsense. Worth stating because the option means
something different on the two types.

**The API's short-audio minimum, measured rather than assumed.** Sending
progressively shorter clips straight to the endpoint, bypassing the
provider's guard:

| Clip | API |
| ---- | --- |
| 50 ms | HTTP 400, "Audio file might be corrupted or unsupported" |
| 90 ms | HTTP 400, same |
| 100 ms | accepted |

So the documented 0.1 s is the real boundary, and the guard sits
exactly on it. Note the error text names corruption rather than
length, which is another reason not to let it reach the log as a
barge-in failure.

Also observed at the boundary: a 100 to 200 ms clip that *is* accepted
comes back as a hallucinated fragment ("The", "Leg", "Zmra") rather
than an empty string. That is not a defect in this provider and it is
not new (a short clip is a short clip on any engine), but it does mean
the barge-in gates cannot rely on an empty transcript alone. They
already do not: #28 gates on 500 ms of classified speech before ASR is
consulted at all. Left as an observation.

**The compatible-endpoint path, against a real server.** A minimal
`/v1/audio/transcriptions` backed by faster-whisper, with the provider
pointed at it, no `api_key_env`, `egress: false`, and `local_only=True`
on the build. It booted keyless, the third-party server parsed the WAV
at 16 kHz, `model`, `language` and the `utterance.wav` filename all
arrived as sent, and the Swedish sentence came back exactly. This is
the claim that a fully local pipeline stays available through this
type, checked rather than asserted.

**End to end, through the device simulator.** No board was connected
for this change, so the pipeline leg ran through `xiaozhi-sdk` against
a real server: a real Opus channel, Silero endpointing, this provider
transcribing, Ollama (`gemma4:e4b`) replying and Piper speaking.

| Asked | Heard | Replied |
| ----- | ----- | ------- |
| "What is the capital of Sweden?" | "What is the capital of Sweden?" | "The capital of Sweden is Stockholm." |
| "Vad heter Sveriges huvudstad?" | "Vad heter Sveriges huvudstad?" | "Huvudstaden i Sverige är Stockholm." |

Both transcripts exact, in two languages, with no `language` configured,
which is the multilingual case working through the whole stack rather
than in a script.

**Not verified: the test board.** No device was attached to this
session, so the Waveshare checkpoint that the last two provider PRs ran
has not been done, and the PR leaves that box unchecked. It is the leg
that matters most for an ASR change, because a real room and a real
microphone are exactly what the synthesized audio above stands in for,
and it is what found the per-sentence latency defect in #38. Worth
running before this type is recommended to anyone.

**Test suite.** 29 unit tests against an `httpx.MockTransport`, driven
through a real `AsyncOpenAI` client rather than a stub, so the
assertions are against what the SDK puts on the wire. The multipart
body is read back rather than trusted, which is what proves the WAV
header carries the rate from the call and the part is named with the
extension the endpoint reads the format from. Nothing is skipped, since
there is no extra that might be absent. Full run: 612 passed, 2 skipped
(both pre-existing), unit and integration together. `uv run ruff
check .` clean.

## Files modified

- `samtal-server/samtal_server/providers/openai_asr.py` (new)
- `samtal-server/samtal_server/providers/openai_endpoint.py` (new)
- `samtal-server/samtal_server/providers/openai_tts.py`
- `samtal-server/samtal_server/providers/registry.py`
- `samtal-server/tests/unit/test_providers_openai_asr.py` (new)
- `samtal-server/config.example.yaml`
- `samtal-server/README.md`
- `README.md`
- `CHANGELOG.md`
