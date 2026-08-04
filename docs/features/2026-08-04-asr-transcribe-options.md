# faster-whisper decode options

## Problem

`WhisperModel.transcribe()` was called with 2 of its 30+ parameters,
and the live-deployment measurements in issue #22 traced the worst
turns directly to the inherited defaults: no in-call VAD (so leading
silence is decoded, the mechanism behind #14's worst symptoms),
`condition_on_previous_text=True` (the documented cause of repetition
loops, seen in the field as one word repeated four times), and the
six-step temperature fallback ladder (one 2.3 s utterance retried into
a 19.8 s decode). Sizing the CPU thread pool needed the
`OMP_NUM_THREADS` environment variable, invisible to anyone reading
the config. Issue #19 additionally showed `beam_size: 5` costing a
multiple of the decode time for no measurable accuracy gain on short
spoken commands.

## Changes

- `OptionsReader` gained `boolean` and `numbers` readers (a strict
  true/false, and a non-empty list of numbers with a single number
  taken as a list of one), rejecting wrong types with errors that name
  the entry.
- The `faster_whisper` provider passes through `vad_filter`,
  `vad_parameters`, `condition_on_previous_text` and `temperature` to
  `transcribe()`, and `cpu_threads` to the `WhisperModel` constructor.
  All keep the engine's defaults when unset.
- `beam_size` now defaults to 1 (greedy decoding), the one deliberate
  default change, as proposed in #19 and validated in production by
  the #22 measurements.
- `config.example.yaml` documents each option with its operational
  rationale.

`hallucination_silence_threshold` was considered and deliberately not
exposed: upstream only honours it when `word_timestamps=True`, whose
per-token cost is the opposite of what this change pursues, and
`vad_filter` addresses the same silence-induced-hallucination failure
mode.

## Key parameters

| Option | Default | Notes |
|---|---|---|
| `beam_size` | 1 (was 5) | greedy decoding |
| `cpu_threads` | 0 (engine default) | set to the container CPU quota |
| `vad_filter` | false (engine default) | recommended true |
| `vad_parameters` | unset | engine VAD tuning mapping |
| `condition_on_previous_text` | true (engine default) | recommended false |
| `temperature` | unset (engine ladder) | recommended `[0.0, 0.2]` |

## Verification

- Unit tests assert the exact kwargs reaching a fake engine, for the
  defaults and for a fully configured entry, plus type-error naming.
- Full unit suite green locally; lint clean; CI runs lint, unit and
  integration lanes on the PR.
- Not verified here: real-conversation latency. The operator behind
  #22 offered before/after numbers from the live deployment within a
  day of a build shipping these options.

## Files modified

- `samtal-server/samtal_server/providers/registry.py`
- `samtal-server/samtal_server/providers/faster_whisper.py`
- `samtal-server/tests/unit/test_providers.py`
- `samtal-server/tests/unit/test_providers_faster_whisper.py`
- `samtal-server/config.example.yaml`
- `CHANGELOG.md`
