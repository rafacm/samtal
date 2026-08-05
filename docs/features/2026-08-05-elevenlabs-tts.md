# ElevenLabs TTS provider

## Problem

Every provider outside the LLM stage was local: `faster_whisper` for
ASR, `piper` for TTS, `silero` for VAD. Piper is the only real TTS the
server has, and it sounds like a local engine from 2023. A self-hoster
who wants a voice worth listening to had no configuration that got
them there, which is the first half of issue #11.

Two questions had to be answered before any code: whether a cloud
provider needs its own extras group, and whether it needs its own
container image. Both were answered no, on issue #11, and this change
is the first one built on that answer.

## Changes

A new `tts` provider type, `elevenlabs`, streaming from the ElevenLabs
streaming endpoint (`POST /v1/text-to-speech/{voice_id}/stream`).

**No SDK, no extra.** The endpoint is one POST whose response body is
the audio, so `httpx` covers it. `httpx` was already installed as a
transitive dependency of both LLM SDKs; this change promotes it to a
declared direct dependency, since this is the first module to import it
directly. A vendor SDK would have added a release cadence to the
lockfile and bought nothing. What makes a provider optional in this
repository is weight (faster-whisper's model runtime) or licensing
(piper-tts is GPL-3.0), and a network client is neither.

**Raw PCM, no transcoding.** `output_format` asks the API for signed
16-bit little-endian mono, which is exactly what `TtsProvider` passes
along, and the default `pcm_24000` matches the rate devices are spoken
at, so the session's resampler is a no-op for this provider. Only the
`pcm_<rate>` formats are accepted: decoding mp3 or opus in order to
re-encode it as Opus would cost both a dependency and latency.

**Chunks are yielded sample-aligned.** This is the one non-obvious
piece. HTTP chunk boundaries fall wherever the network puts them, so a
response chunk can end on the first byte of a sample. `Resampler.process`
truncates an odd trailing byte (`pcm[: samples * 2]`), which would
leave the next chunk starting mid-sample and shift every following
sample by one byte, turning the rest of the reply into noise. The
provider carries the odd byte into the next chunk instead. Two tests
pin the behaviour, because the failure would only ever show up as
noise on the board.

**Egress marking.** The type carries `egress = True` (#30), so a
`server.local_only` boot refuses it by name rather than quietly
sending reply text to a vendor.

**Key handling** follows the existing rule: `api_key_env` names an
environment variable, resolved at startup through the same
`resolve_api_key` the `anthropic` provider uses, so an unset variable
fails the boot rather than every conversation. Unlike the LLM
providers there is no SDK-side fallback resolution, so an entry
without `api_key_env` is refused outright.

## Key parameters

| Option | Default | Notes |
| ------ | ------- | ----- |
| `voice_id` | required | The id from your voice library, not the display name |
| `api_key_env` | required | Environment variable holding the key |
| `model` | `eleven_flash_v2_5` | ~75 ms to first byte, 32 languages including Swedish; `eleven_multilingual_v2` sounds better and answers slower |
| `output_format` | `pcm_24000` | Must be `pcm_<rate>`; matches the device rate, so nothing is resampled. `pcm_44100` and up need a paid tier |
| `language_code` | unset | ISO 639-1, pins the spoken language instead of letting the model infer it |
| `voice_settings` | unset | Passed through as given; keys validated (`stability`, `similarity_boost`, `style`, `speed`, `use_speaker_boost`) |
| `timeout_s` | `30` | Long enough for a slow first byte, short enough that a hung request does not hold a sentence open forever |

The model default is the one judgement call worth restating. Flash
exists for real-time agents, and a voice assistant is that case; an
operator who would rather have Multilingual v2's fidelity than its
latency sets `model` and pays for it in the gap the user hears.

## Verification

- 17 unit tests against an `httpx.MockTransport`, covering options,
  request shape, streaming, sample alignment and API failures. Nothing
  is skipped: unlike the Piper and faster-whisper suites there is no
  extra to be missing.
- Full suite green: 527 unit tests (2 skipped, both pre-existing), 27
  integration tests, `ruff check` clean.

### Against the real API

Three sentences synthesized, two English and one Swedish, then fed
back through the ASR stage (whisper small, resampled to 16 kHz the way
the session does it). All three transcribed to their input text,
including the Swedish "samtalsassistent". That is the alignment check
that matters: a one-byte shift is white noise, and white noise
transcribes to nothing.

### Added latency versus Piper

Median of five rounds per sentence, same machine, warm.

| Sentence | Speech | Piper first audio | ElevenLabs first audio | Added |
| -------- | ------ | ----------------- | ---------------------- | ----- |
| "The kitchen light is now off." | 1.65 s | 43 ms | 132 ms | +89 ms |
| "Hello, I am your samtal assistant. How can I help you today?" | 3.6 s | 92 ms | 131 ms | +39 ms |

The shape is more useful than either number. ElevenLabs is flat at
about 130 ms whatever the sentence, because it streams and the clock
stops at the first chunk. Piper's grows with sentence length, because
it synthesizes the whole sentence in one thread hop before yielding
anything. So the penalty shrinks as sentences get longer, and for a
long enough sentence Piper would be the slower of the two to first
audio. Occasional network variance shows up as an outlier (one round
hit 646 ms); the medians are stable.

An idle gap costs nothing measurable: after 12 seconds of silence,
past httpx's 5 second default `keepalive_expiry`, the next request
still returned first audio in 186 ms. Reconnecting is cheap relative
to the request itself, so no connection warming is needed.

### Device checkpoint

Ran on the Waveshare ESP32-S3-Touch-LCD-1.54, against a config with
two agents bound to the board: `cloud` on ElevenLabs and `local` on
Piper, so one conversation covers the provider and the A/B by ear.

A multi-turn conversation went through: two questions answered on the
cloud agent, then "it's the local" handed over to Piper mid-session and
the voice changed audibly. Four ElevenLabs requests, all 200, no
non-200s, and no `incomplete sample` warnings, which is the log line
that fires if a response ever ends mid-sample.

The number the desk showed that the bench could not: `speaking started`
is logged 4 ms after the streaming POST returns its headers. The first
Opus frame leaves for the device as the audio arrives, rather than
after the sentence is synthesized, which is the whole point of using
the streaming endpoint.

Getting there needed a detour worth recording. The board's NVS
`ota_url` pointed at a deployed server, so pressing PWR held a
conversation against that instead, with its old voice; the server here
logged nothing at all. Rewriting a provisioned board's NVS is
destructive, since ESP-IDF generates whole partitions rather than
editing keys, and this one carries wifi credentials, the device UUID
and RF calibration. Instead the 16 KB partition was dumped, one entry
patched in place, and the original flashed back afterwards, verified
byte-identical by hash. The patch keeps the stored size at 88 bytes and
pads the shorter URL with NULs, so the span and every following entry
stay at their exact offsets; `Settings::GetString` in the firmware
resizes to the stored length and then pops every trailing NUL, so the
padding reads back as the short URL. Both CRCs were recomputed from the
untouched partition first, and the patcher refuses to write if its CRC
convention does not reproduce them.

## Files modified

- `samtal-server/samtal_server/providers/elevenlabs_tts.py` (new)
- `samtal-server/samtal_server/providers/registry.py`: the `elevenlabs`
  factory and its `tts` table entry
- `samtal-server/pyproject.toml`: `httpx` as a direct dependency
- `samtal-server/tests/unit/test_providers_elevenlabs.py` (new)
- `samtal-server/config.example.yaml`: the `eleven` provider entry
- `samtal-server/README.md`: the provider table, a note on why cloud
  providers need no extra, and the `local_only` example
- `CHANGELOG.md`
