# ASR language surface: detect once, trust by confidence

## Problem

Language detection cost the live deployment in issue #22 a constant
3.4 s of a 6.7 s median ASR stage, on every utterance, because the
only lever was `language`: a binary between fast-and-monolingual and
multilingual-and-slower. Detection also failed expensively: the four
misdetections measured all sat at low confidence (0.39 to 0.89, with
the one genuine Spanish utterance at 0.97), and a wrong language sent
the decoder into repetition loops, making non-English-detected turns
half again slower than English ones. samtal trusted a 0.39 guess
exactly as much as a 0.97 one, and faster-whisper's own
`language_detection_threshold` is not a fallback (below it the engine
just scans more segments), so the remedy had to be built here.

The protocol was the obstacle: `AsrProvider.transcribe` returned a
bare string, so the detected language and its confidence died inside
the provider, and the shared singleton provider had nowhere to keep
per-session state (see the amended ADR).

## Changes

- `AsrProvider.transcribe` returns `AsrResult` (text, `language`,
  `language_confidence`, `lock_language`) and accepts a
  `language_hint`. The session stores a returned `lock_language` and
  hands it back as the hint on later utterances; that round-trip is
  the whole cross-call mechanism, recorded in
  `docs/adr/2026-08-04-asr-results-carry-language-metadata.md`.
- The `faster_whisper` provider gained three options:
  `language_detect: once` locks the first confident detection for the
  session; `language_confidence_floor` (default 0.6) is what confident
  means; `language_fallback` is used instead of any detection below
  the floor. The fallback re-invocation happens before any decoding:
  faster-whisper detects before it decodes and its segments are lazy,
  so the distrusted decode never runs.
- A configured `language` still pins everything and beats the hint.
- The `heard` event carries `language` and `language_confidence` when
  an engine detected, so the policy is observable from retained logs.

## Key parameters

| Option | Default | Notes |
|---|---|---|
| `language_detect` | `every_utterance` | `once` locks per session |
| `language_confidence_floor` | 0.6 | measured failures sat at 0.39 to 0.89; successes at 0.64 to 0.97 |
| `language_fallback` | unset | used below the floor; never locked |

The floor rides on `vad_filter: true` (see the 2026-08-04 decode
options feature doc): the 0.89-confidence misdetection in the field
data was on silence-padded audio that in-call VAD now strips before
detection sees it.

## Verification

- Unit tests script a fake engine's detections and assert: metadata
  reaches the result, a hint pins the language and skips detection, a
  configured language beats the hint, a low-confidence detection
  re-invokes with the fallback before decoding, a confident one is not
  second-guessed, and `once` locks only confident fresh detections.
- A session-level test proves the round-trip: `lock_language` from
  utterance one arrives as `language_hint` on utterance two, and both
  heard events carry the language fields.
- Full unit suite green locally (481 passed); lint clean; CI runs the
  same plus integration and the image smoke lane.
- Not verified here: the 3.4 s per-turn saving on real hardware. The
  operator behind #22 offered before/after numbers from the live
  deployment within a day of a build shipping this.

## Files modified

- `samtal-server/samtal_server/providers/base.py`
- `samtal-server/samtal_server/providers/mock.py`
- `samtal-server/samtal_server/providers/__init__.py`
- `samtal-server/samtal_server/providers/faster_whisper.py`
- `samtal-server/samtal_server/session.py`
- `samtal-server/tests/unit/test_providers.py`
- `samtal-server/tests/unit/test_providers_faster_whisper.py`
- `samtal-server/tests/unit/test_session_events.py`
- `samtal-server/config.example.yaml`
- `samtal-server/README.md`
- `docs/adr/` (new record, one status line amended)
- `CHANGELOG.md`
