# Providers are startup-built singletons behind payload-only protocols

**Status:** Accepted (recorded 2026-08-04, backfilling a decision made
in the v1 design). The payload-only clause is amended by
[ASR results carry language metadata](2026-08-04-asr-results-carry-language-metadata.md);
the singleton and statelessness decisions stand.

## Context

The v1 server builds every provider once, at startup, from the config.
This is deliberate: the expensive work happens before the first
conversation. faster-whisper downloads and loads its model when the
provider is constructed, and the module docstring says so; Piper voices
behave the same way. Sessions receive the shared providers through
`AgentProviders` and never construct their own.

The provider protocols are equally deliberate in their minimalism. They
carry the payload and nothing else:

- `AsrProvider.transcribe(pcm, sample_rate) -> str`
- `Endpointer.feed(pcm) -> bool` plus `reset()`

No session identity crosses the protocol, and no metadata the engine
produces comes back out. This kept v1 small, made the mock and real
providers trivially interchangeable, and made the interface the test
surface.

## Decision

Providers are stateless singletons built at startup and shared by every
session. Provider protocols carry the payload only: audio in, text or a
boolean out.

## Consequences

What it bought: model weights load once and the first conversation does
not pay for them; every provider behind a protocol is swappable in
tests; a session cannot corrupt provider state because there is none.

What field use is pressing on, with evidence:

- Metadata the engine already produces dies inside the provider. The
  detected language and its confidence never reach the session
  ([#22](https://github.com/rafacm/samtal/issues/22)), and where speech
  started never leaves the endpointer
  ([#14](https://github.com/rafacm/samtal/issues/14)).
- Session-scoped provider state has no home. Detect-once-per-session
  language caching (#22, option A) needs the session to learn what the
  provider detected and hand it back as a hint, which the `-> str`
  protocol cannot express.

The decision stands: singletons and narrow protocols are still right
for this server's size. The consequence to respect is that every
protocol widening touches `providers/base.py`, `session.py`, every
implementation of that protocol, and their tests at once, so each
widening is a deliberate, milestone-sized change rather than a rider on
a fix. #14 and #22 are each such a widening.
