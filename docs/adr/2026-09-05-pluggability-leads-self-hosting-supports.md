# Pluggability leads, self-hosting supports

**Status:** Accepted (recorded 2026-09-05).

## Context

vinga's public story led with self-hosting: the tagline, the README
opener, and the feature order all said "yours to run" first and
"yours to compose" second. Both claims are true, but they are not
equally load-bearing. Self-hosting is exercised once, when the
operator picks where the server runs. The mix is exercised for as
long as the deployment lives: which model answers, which voice
speaks, which ear listens, per agent and per stage, revised whenever
a better option appears. The identity section already frames vinga as
the appliance side of a composition ("vinga owns the appliance,
conversation runtimes own the conversation"), and the project's own
origin story is an act of mixing.

## Decision

When vinga explains itself, mix-and-match is the leading claim: every
stage the server runs is a slot, and which service fills each slot is
the user's choice. Self-hosting is stated as the ground that makes
the choice real, and a fully local deployment as the limiting case of
the blend, kept first-class by the product promises.

This is an emphasis about the promise, not a reversal of the identity
paragraph that rejects "another pluggable VAD/ASR/LLM/TTS server".
The two sit at different altitudes: the user is promised choice among
engines; vinga delivers that promise by hosting engines behind stable
slots, never by making the pipeline machinery itself the product.

## Consequences

- The README leads with agents and the mix (opener, feature order),
  with self-hosting closing rather than opening.
- New user-facing surfaces (docs intros, feature lists, release
  notes) follow the same order.
- Provider breadth is identity work, not convenience work: a stage
  with a single filler is a gap in the leading claim. Today that
  stage is VAD, where the gap is being closed sideways, by better
  endpointing (#31, #81), rather than by a second silence detector.
- The guidelines' identity section cites this record.
