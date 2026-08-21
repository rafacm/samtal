# Pipeline ownership

Which parts of the conversation pipeline are shared shape, the thing
any streaming voice framework provides, and which parts are vinga's
own semantics, which none of them has. The standing decision this
inventory serves is recorded on issue #84 (evaluate adopting pipecat
as the pipeline framework): not now; the fork in the road is #31, the
event-driven streaming ASR interface; consult the evidence when that
issue goes live. This page is the repository-resident form of that
inventory, and it changes when the pipeline does.

## The shared bucket

What any framework provides: frame flow and orchestration, VAD and
endpointing wiring,
[output pacing](../glossary.md#output-pacing),
[capture](../glossary.md#capture), and latency metrics. This is the
part an adoption would replace, and it is stable, small, and
measured: the pacing clock is edge-owned and reset per reply, and the
capture passes its own synthetic-echo controls
(`scripts/echo_leakage_control.py`).

## The owned bucket

What no framework has: the
[gate ladder](../glossary.md#gate-ladder) semantics (transcript
confirmation, merge-mid-ASR, the
[refractory period](../glossary.md#refractory-period)), the
[filler](../glossary.md#conversational-filler) with its
yield-to-live-speech rule, reason-annotated decision events
(`barge_in_suppressed` with a reason, `filler_skipped` with a
reason), and the
[wire-true capture](../glossary.md#wire-true-capture) property.
Under any adoption these port as custom processors, so they are a
relocation cost, never a saving, and the decision-sites principle
([principles.md](principles.md)) requires them to stay self-owned
wherever they run.

## The evidence

The pipecat alignment spike (issue #89, PR #90, pinned pipecat-ai
1.7.0) measured both of its gates; the full record is
[the spike implementation doc](../plans/2026-08-11-pipecat-alignment-spike-implementation.md)
and the evidence comment on #84.

- **Gate 1, capture alignment: passed.** A wire-true capture is
  constructible on pipecat to +1.2 ms of lag and 0.2 dB of gain, but
  only through the turn track recorded at the native output rate; the
  obvious recording API silently corrupts the reference whenever the
  device streams during a reply, which barge-in makes the normal
  condition.
- **Gate 2, adapter size: neutral.** The adoption-required adapter
  came out the same size as the bespoke code doing the same work (154
  against 155 code-only lines), covering 8 of the 23
  `SessionInput`/`DeviceOutput` seam obligations and speaking xiaozhi
  protocol v1 only. Adoption relocates the device edge; it does not
  shrink it.

## Where the growth lands

The overlap surface was roughly 2,000 lines when #84 was written
(2026-08-09) and roughly 5,100 at `9e8b2743` (2026-08-21):
`runtime/pipeline.py` 1774, `device/session.py` 1226, `capture.py`
647, `runtime/turntaking.py` 354, `runtime/filler_runner.py` 312,
`runtime/turns.py` 272, `runtime/speech.py` 159, plus the audio
helpers. Nearly all of the growth is in the owned bucket: turn-taking
semantics, the filler, the turn record, and the reasoned events they
emit. The shared bucket has barely moved. That is the pattern to
watch: bespoke growth that duplicates a framework argues for
adoption, bespoke growth in semantics no framework has argues
against, and so far it is the second.

## When to reopen the question

- **#31 goes active.** A bespoke event-driven streaming ASR pipeline
  is re-implementing a framework's core rather than its periphery,
  which is the line #84 draws.
- **A second runtime is actually wanted** (#92, stage 2). pipecat
  would arrive as a sibling runtime behind
  `SessionInput`/`DeviceOutput`, per-device selectable, never as a
  backend swap: the runtimes-are-siblings principle.
- **#81 reaches its v2 stage** (continuous end-of-turn prediction
  over streaming input), which is itself sequenced behind #31. Its
  earlier stages deliberately need no framework: smart-turn v3 is
  consumable as a standalone ONNX model.

Whatever reopens it, the spike's figures are pinned to pipecat-ai
1.7.0 and transfer to no other release without re-measuring; the
transport and the audio buffer processor are areas that project
changes often.
