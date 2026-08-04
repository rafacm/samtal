# ASR results carry language metadata, sessions carry the cache

**Status:** Accepted. Amends the payload-only clause of
[providers are startup-built singletons](2026-08-04-providers-are-stateless-singletons.md);
the singleton and statelessness decisions there stand.

## Context

The live-deployment measurements in
[#22](https://github.com/rafacm/samtal/issues/22) put per-utterance
language detection at 3.4 s of a 6.7 s median ASR stage, a constant
cost that no decode option removes, and showed misdetections (which
cluster at low confidence) driving the worst turns. The remedies all
need what the payload-only protocol destroyed: detect once per session
needs the detected language to outlive one call, and a confidence
floor needs the confidence to leave the provider at all.

The constraint from the standing decision: providers are shared
singletons with no session identity, so "per session" state cannot
live in them.

## Decision

`AsrProvider.transcribe` returns an `AsrResult` (text, detected
language, its confidence) and accepts an optional `language_hint`. A
provider whose policy wants per-session reuse sets
`AsrResult.lock_language`; the session stores it and passes it back as
the hint on later utterances. Policy lives in the provider and its
configuration; the cache, the only per-session state, lives in the
session.

## Consequences

- Providers stay stateless singletons; the lock/hint round-trip is the
  mechanism that lets a per-session policy live in a shared provider.
- The `heard` event carries `language` and `language_confidence` when
  an engine detected, so operators can watch the policy work from the
  logs (per the
  [observability record](2026-08-04-json-logs-are-the-observability-surface.md)).
- Any ASR provider added later, including the cloud providers of
  [#11](https://github.com/rafacm/samtal/issues/11), implements the
  same contract; one that has no notion of language returns bare text
  and the session behaves as before.
- As the amended record predicted, widening the protocol touched
  `base.py`, both ASR providers, the session, and their tests in one
  change; this is the worked example of why such widenings are their
  own milestone-sized changes.
