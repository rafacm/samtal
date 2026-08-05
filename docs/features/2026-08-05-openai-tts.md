# OpenAI TTS provider

## Problem

The second of the three cloud providers in issue #11. The `elevenlabs`
type had just given the server a cloud voice, but it also gave the
operator a second vendor account, a second key, and a second bill. A
deployment whose LLM stage is already on OpenAI has a key in hand that
could serve the voice too, and no configuration that let it.

The packaging questions were already answered on #11 and did not have
to be reopened: cloud providers ship in the existing image, with no
extras group and no image variant.

## Changes

A new `tts` provider type, `openai`, streaming from the speech
endpoint (`POST /v1/audio/speech`).

**No new dependency at all**, which is the difference from the
ElevenLabs provider. That one needed `httpx` promoted to a direct
dependency; this one needs nothing, because the `openai` client is
already a core dependency carried for the `openai_compatible` LLM type
and speech is a method on the client that already ships.

**No audio format option**, which is the other difference from
ElevenLabs. The API's `pcm` format is signed 16-bit little-endian mono
at a fixed 24 kHz with no header, which is both this stage's interface
and the rate devices are spoken at, so nothing is resampled and there
is nothing to choose. ElevenLabs needed `output_format` because it
offers several rates. The remaining OpenAI formats are containers that
would have to be decoded just to be re-encoded as Opus, costing a
dependency and latency.

**Chunks are yielded sample-aligned**, for the reason the ElevenLabs
provider documents: an HTTP chunk can end on the first byte of a
sample, and passing the odd byte on would leave the next chunk
starting mid-sample and shift every following sample by one byte,
turning the rest of the reply into noise. Worth confirming rather than
assuming here, since the audio arrives through the SDK rather than
from `httpx` directly: `iter_bytes()` preserves transport chunk
boundaries, so a 3/2/3-byte response really does reach the alignment
logic as three ragged chunks and leaves as `\x01\x02`, `\x03\x04`,
`\x05\x06\x07\x08`.

**The steering knobs are validated against the model.** This is the
one place the type refuses a configuration the API would accept. Each
speech model reads one of `instructions` and `speed` and silently
ignores the other: the `gpt-4o` models take prose, `tts-1` and
`tts-1-hd` take the multiplier. The API does not complain about the
one it does not read, so naming the wrong one for the model would be a
knob that never takes effect, which is exactly what this module's
option checking exists to prevent. It costs a hardcoded `gpt-4o`
prefix check, which is the deliberate trade.

`OptionsReader` grows `optional_number`, a number with no default, for
`speed`: there is no value worth guessing at when the operator has not
named one, and the API's own default should apply.

**Egress marking.** The type carries `egress = True` (#30), so a
`server.local_only` boot refuses it by name.

**Key handling** follows the existing rule: `api_key_env` names an
environment variable, resolved at startup through the same
`resolve_api_key` the `anthropic` provider uses. As with ElevenLabs
and unlike the LLM providers, there is no SDK-side fallback
resolution, so an entry without `api_key_env` is refused outright.

## Key parameters

| Option | Default | Notes |
| ------ | ------- | ----- |
| `voice` | required | One of the stock voices, the same on every account |
| `api_key_env` | required | Environment variable holding the key |
| `model` | `gpt-4o-mini-tts` | The current speech model, and the fastest of the three to start speaking |
| `instructions` | unset | How to speak, in prose. Read by the `gpt-4o` models only |
| `speed` | unset | 0.25 to 4.0. Read by `tts-1` and `tts-1-hd` only |
| `timeout_s` | `30` | Bounds a hung request |

Sample rate is not an option: it is 24 kHz, fixed by the `pcm` format.

## Verification

**Round trip through the ASR stage**, which is the alignment check
against a real stream rather than a mock, since a one-byte shift is
white noise and transcribes to nothing. Three sentences synthesized
and fed back through faster-whisper:

| In | Out |
| -- | --- |
| "The kitchen light is now off." | "The kitchen light is now off." (en) |
| "Hello, I am your samtal assistant. How can I help you today?" | "Hello, I am your SOMtol Assistant. How can I help you today?" (en) |
| "Hej, jag är din samtalsassistent." | "Hej, jag är din samtalsassistent." (sv) |

The second line is the ASR guessing at an unfamiliar proper noun, not
an audio defect; the Swedish sentence carries the same word and comes
back exactly, which is the stronger evidence of the two.

**Added latency, and the finding this change turns up.** Median of
five rounds per sentence, warm, same machine:

| Sentence | OpenAI | ElevenLabs | Piper |
| -------- | ------ | ---------- | ----- |
| "The kitchen light is now off." | 888 ms | 194 ms | 40 ms |
| "Hello, I am your samtal assistant..." | 764 ms | 188 ms | 79 ms |
| "Hej, jag är din samtalsassistent." | 818 ms | 194 ms | 54 ms |

Roughly +700 ms on ElevenLabs and +800 ms on Piper: a pause a person
notices at the start of every reply, and the honest reason to pick
this type is the shared key rather than the experience.

The provider does stream. The figure is flat whatever the sentence
length, and the longest sentence starts soonest, so the wait is
OpenAI's time to first byte and not a whole sentence being synthesized
before anything is sent. Piper is the contrast: its figure grows with
sentence length because it does exactly that.

Comparing the three speech models was worth doing, because the result
contradicts how the older pair is usually described:

| Model | Short sentence | Longer sentence |
| ----- | -------------- | --------------- |
| `gpt-4o-mini-tts` | 908 ms | 820 ms |
| `tts-1` | 1549 ms | 1413 ms |
| `tts-1-hd` | 1974 ms | 1861 ms |

`tts-1` is documented as the low-latency model of the two older ones,
and it is, but both are slower than the current model by a wide
margin. The default is therefore also the fastest, and the
documentation says so rather than repeating the vendor's framing.

An idle gap costs nothing: after 12 seconds of silence, past httpx's
5 second default `keepalive_expiry`, the next request returned first
audio in 552 ms, faster than the warm median rather than slower. No
connection warming needed.

**Test suite.** 16 unit tests against an `httpx.MockTransport`, driven
through a real `AsyncOpenAI` client rather than a stub, so the
assertions are against what the SDK actually puts on the wire.
Nothing is skipped, unlike the Piper and faster-whisper suites, since
there is no extra that might be absent. Full unit suite: 543 passed,
2 skipped (both pre-existing, the piper and faster-whisper extras).
Integration suite: 27 passed. `uv run ruff check .` clean.

**Not verified here:** the end-to-end leg on the test board. The
ElevenLabs change has it and this one does not.

## Files modified

- `samtal-server/samtal_server/providers/openai_tts.py` (new)
- `samtal-server/samtal_server/providers/registry.py`
- `samtal-server/tests/unit/test_providers_openai_tts.py` (new)
- `samtal-server/config.example.yaml`
- `samtal-server/README.md`
- `CHANGELOG.md`
