# Provider observability: failures, and what a generation cost

## Problem

Two field reports, one gap.

A deployment that restricts outbound network access described what
happens when a provider's host is not permitted: the pod boots healthy,
the LLM answers, and every reply is silent until the synthesis
`timeout_s` expires, with nothing in the logs indicating a network
policy. A failing ASR, LLM or TTS call surfaced only as
`logger.exception("session %s: reply failed")` with no `extra=`, so in
JSON mode it carried no `event` to filter on, no `session` or `device`
to group by, and no provider, stage or host at all. Every other
significant thing in a session has a structured event; a provider that
cannot be reached had a traceback ([#53](https://github.com/rafacm/samtal/issues/53)).

A second session lost 43 s to a handover that produced no speech, in
which the post-handover generation took 19.04 s against a session
median of 1.18 s. The report asked the right question, and the logs
could not answer it: if the slow calls are also the large ones it is
the payload, and if the payload is constant it is the vendor. Stage
latency was inferred from the gaps between events rather than
measured, and the gap between `heard` and `speaking_started` holds both
the LLM and the TTS time to first byte with nothing separating them
([#55](https://github.com/rafacm/samtal/issues/55)).

They are the same problem: a provider's behaviour is invisible. Done
separately they would have invented the vocabulary for naming a
provider twice.

## Changes

**A provider knows which entry it is.** `ProviderIdentity` (stage,
name, type, host) is stamped by `build_provider`, the one place that
knows all four at once. `host` is the provider's own contribution,
since only it knows whether its `base_url` names a vendor or
localhost, and it is the actionable field for anyone with an egress
allowlist. A provider built outside the registry carries no identity,
and the events about it carry fewer fields rather than invented ones.

**`provider_failed`**, emitted where a stage's call fails, carrying
`stage`, `provider`, `type`, `host`, `error`, `duration_ms` and
`agent`, plus the `session` and `device` every conversation event has.
The human sentence and the traceback are unchanged; this is the
structured half. A timeout is worded as one, because where traffic is
dropped rather than refused the symptom is a wait, and the wait lands
at the provider's `timeout_s`, which is itself the diagnosis. It is
detected by class name as well as by type, since every SDK has its own
(`openai.APITimeoutError` is an `APIConnectionError`, and
`httpx.TimeoutException` inherits from neither); the exact class is in
`error` either way.

The LLM stream is watched around its own iteration rather than around
the loop that consumes it. Wrapping the loop would blame the LLM for a
TTS failure raised while speaking what the model had already said, and
report one failure twice. A TTS failure is reported from the synthesis
task where it happens, not where it is re-raised, because a sentence
run ahead can fail long before the moment it would have been spoken.

**`llm_round`**, emitted per generation call, carrying `duration_ms`,
`first_token_ms`, `turns`, `round`, the provider fields, and
`prompt_tokens`/`completion_tokens` where reported. `round` counts the
whole reply rather than one agent's leg, so the generation after a
handover, which was the slow one in the report, is a round of its own
rather than another first round. `turns` is the cheap proxy for a
payload growing turn by turn.

**Token counts.** A new `Usage` variant on the LLM event stream, which
providers yield last. The Anthropic API reports usage on every
streamed message unasked. The OpenAI dialect reports it only for a
request carrying `stream_options: {"include_usage": true}`, which is an
OpenAI field a compatible server is free not to know, so it is sent
only where the endpoint is OpenAI itself: failing a conversation to
enrich a log line is the wrong trade. A usage chunk from any endpoint
that volunteers one is read regardless.

**`tts start` moved.** It went out as soon as transcription finished,
so a device entered its speaking state before the model had answered.
See the ADR: [`tts start` marks speech, not acceptance of the
turn](../adr/2026-08-06-tts-start-marks-speech-not-acceptance.md).

## Key parameters

No new configuration. Nothing is sampled or rate limited: a reply
emits one `llm_round` per generation call, which is one or two for a
typical turn.

## Verification

Unit tests cover both events, the round numbering across a handover,
the absence of token counts as a non-error, the single report of a TTS
failure, and the message ordering.

Against real providers (OpenAI for all three stages, hosts made
unreachable by pointing `base_url` at a refusing port and at a
non-routable address):

| Case | What the event said |
| --- | --- |
| Everything reachable | `llm_round` with `duration_ms` 1544, `first_token_ms` 1388, `turns` 1, `prompt_tokens` 28, `completion_tokens` 7 |
| TTS host refuses | `provider_failed` `stage=tts`, `error=APIConnectionError`, `duration_ms` 4, host named |
| TTS host drops | `provider_failed` `stage=tts`, `error=APITimeoutError`, `duration_ms` 4005, worded "timed out" |
| LLM host refuses | `provider_failed` `stage=llm`, `error=APIConnectionError`, `duration_ms` 1349 |
| ASR host refuses | `provider_failed` `stage=asr`, `error=APIConnectionError`, `duration_ms` 3 |

On the board (Waveshare ESP32-S3-Touch-LCD-1.54, firmware 2.4.0),
against a generation stalled 20 s, reading the firmware's own state
machine over serial. Before the ordering change:

```
listening -> speaking          at the transcript, nothing playing
Application: Abort speaking    7.1 s later, the conversation button
speaking  -> listening
```

with `device aborted (no reason)` on the server at that instant. After:

```
connecting -> listening
listening  -> speaking         20.1 s later, at the first sentence
speaking   -> listening        on tts stop, re-armed by itself
```

and the `llm_round` for that turn read `duration_ms` 20138,
`first_token_ms` 20022, `turns` 1: the wait is entirely before the
first token on a one-turn payload, which is the attribution the field
report could not make. A second utterance was heard immediately
afterwards with no intervention, and its `turns` had grown to 3.

## Files modified

- `samtal_server/providers/base.py`: `ProviderIdentity`, `Usage`,
  `Provider.host` and `Provider.identity`
- `samtal_server/providers/registry.py`: stamps the identity
- `samtal_server/providers/openai_endpoint.py`: `endpoint_host`
- `samtal_server/providers/openai_asr.py`, `openai_tts.py`,
  `elevenlabs_tts.py`, `anthropic_llm.py`, `openai_llm.py`: hosts, and
  usage where the API reports it
- `samtal_server/session.py`: both events, and the `tts start` move
- `docs/adr/2026-08-06-tts-start-marks-speech-not-acceptance.md`
- `README.md` (server): the events table
