# Pipecat alignment spike plan

## Goal

Answer issue #89 with measured evidence: build the minimal pipecat
pipeline behind a xiaozhi frame serializer, drive it with the
xiaozhi-sdk device simulator, and run the two gates from the issue,
capture alignment and adapter size. Either outcome is a decision for
#31 (both gates pass: pipecat adoption becomes a genuine tradeoff
argued with numbers; either gate fails: #31 is built bespoke behind
the #85 boundary). Findings go back on #84 as the evidence record,
whatever the outcome.

The spike is server-side only: no board, no API keys, no cloud
calls. Order of two to three days. Spike code is throwaway by
default; nothing lands in `samtal_server/`.

The companion implementation doc,
[`2026-08-11-pipecat-alignment-spike-implementation.md`](2026-08-11-pipecat-alignment-spike-implementation.md),
records what each milestone actually did, with deviations and
discoveries.

## Where the spike lives

`spikes/pipecat-alignment/` at the repository root, as its own uv
project with its own `pyproject.toml`. Reasons:

- Nothing lands in `samtal_server/`, per the issue; keeping the
  spike out of `samtal-server/` makes that true by construction and
  keeps CI silent (the workflow triggers only on `samtal-server/`
  paths). Spike code carries no CI obligation.
- Its dependencies (`pipecat-ai` with its silero extra,
  `xiaozhi-sdk`, numpy, scipy, an Opus binding) must not touch the
  server's dependency tree, and a separate project is the cheapest
  way to guarantee that.
- The pipecat version is pinned exactly, because the measured
  numbers are only meaningful against a named version; the findings
  record it.

Layout, subject to implementation reality:

```
spikes/pipecat-alignment/
    pyproject.toml      pinned deps, own uv project
    README.md           how to run the exchange and the analysis
    serializer.py       the xiaozhi frame serializer (gate 2's
                        measured artifact)
    pipeline.py         transport, VAD, canned reply service,
                        wiring; the runnable server
    tap.py              the wire tap: outgoing Opus packets with
                        send timestamps
    compose.py          builds the samtal-format capture pair from
                        the buffer recording and the tap
    inject.py           the tap-injection alignment run (gate 1)
    drive.py            drives the exchange with xiaozhi-sdk
```

The analysis itself is not rewritten: `scripts/echo_leakage.py` and
`scripts/echo_leakage_control.py` run unmodified over the composed
capture pair. If either script needs a change to accept the spike's
data, that is a finding to record, not a patch to slip in quietly.

## The pipeline under test

Minimal by design, per the issue and the #84 decision record:

- **Transport**: pipecat's FastAPI websocket transport, with the
  xiaozhi serializer below. If the simulator's `init_connection`
  requires the OTA discovery step, a minimal OTA endpoint on the
  same app returns the websocket URL; that endpoint is harness, not
  adapter (samtal-server already owns a real one, so adoption would
  keep it, not rewrite it).
- **VAD**: Silero through pipecat's bundled analyzer, local.
- **Reply**: a canned-audio service that streams a fixed local WAV
  as TTS output in small chunks, no LLM and no cloud. On end of
  user turn, the bot speaks the canned clip. The clip is
  speech-like audio (not a tone), long enough that the measurement
  has windows to work with (below).
- **Rates mirror samtal's real shape**: device sends 16 kHz mono
  Opus in 60 ms frames; the server hello announces 24 kHz output,
  as samtal-server does, and the canned audio is 24 kHz, so the
  output path exercises the same encode-and-resample stages whose
  drift is a named failure mode. Hiding that by running everything
  at 16 kHz would weaken exactly the measurement the spike exists
  for.

## The serializer, gate 2's artifact

The load-bearing deliverable: a minimal xiaozhi frame serializer
for the transport, just enough for one full exchange. Hello
handshake, `listen` and `abort` inbound, `tts`
start/sentence_start/stop and `stt` outbound, binary Opus frames
both ways.

It deliberately implements the same semantics as the
`SessionInput`/`DeviceOutput` seam from #85
(`samtal_server/device/boundary.py`), so its size and shape compare
like for like against the bespoke device edge
(`samtal_server/device/session.py`, 883 lines). Concretely, while
building it, keep a running map from each seam obligation to where
it lands in the pipecat stack: absorbed by the framework (pacing?
interruption? the tts-start latch?), implemented in the serializer,
implemented in glue around it, or inexpressible. Anything the seam
could not express is a finding against the comparison's premise and
gets recorded either way.

What counts against the 883 lines is what an adoption would have to
keep and maintain: the serializer plus the transport glue. The
canned reply service, the OTA stub, the tap, and the composer are
measurement harness and are reported separately, outside the
comparison. An adapter that stays small is evidence for
normalize-the-hardware-edge with pipecat behind it; an adapter that
grows into an impedance-matching layer is a finding against
adoption regardless of what the alignment says.

## The measurement, gate 1

### What samtal's capture gets for free, and pipecat must prove

samtal records the reply channel by decoding the very Opus packets
paced out, at the moment they are sent, on one clock; that is why
the #48 echo measurement is trustworthy and why its control passes.
Pipecat's `AudioBufferProcessor` records the bot track upstream of
the transport's pacing, on its own timeline. The question is
whether a shared timeline between that recording and the wire can
be constructed, from what pipecat exposes, to cross-correlation
grade. That construction is not incidental harness: any future echo
measurement or AEC reference on a pipecat runtime would rely on
exactly it, so its feasibility is the thing under test.

### Instrumentation

Two independent recordings of the same exchange:

- **The tap**: every Opus packet leaving the serializer, with a
  monotonic send timestamp, decoded to PCM and laid out on the send
  timeline (gaps as silence). This matches samtal's capture
  semantics for channel 1 and is the ground truth for what the
  device actually heard, and when.
- **The buffer recording**: `AudioBufferProcessor`'s bot track (and
  user track, for the mask), as pipecat records it.

Both are anchored to one epoch at recording start, using the best
mapping pipecat affords (recording start time plus sample
counting). The composer then writes a samtal-format capture pair:

- `<session>.wav`, stereo 16 kHz s16le: channel 0 (mic) is the
  simulator's utterance audio as received, on the shared epoch,
  with low-level noise added so the channel is not digitally
  silent; channel 1 (ref) is the buffer recording's bot track,
  resampled to 16 kHz as samtal's capture does.
- `<session>.jsonl`: the minimal event track the analysis reads,
  on the same epoch: `session_open`, one `heard` per utterance
  (with `duration_s`, so the user-speech mask excludes it), and
  one `speaking_started` per reply (with `agent`), stamped from
  the tap's first packet.

### The runs

1. **Stock control, the sanity baseline.**
   `scripts/echo_leakage_control.py` on the composed pair: injects
   the ref channel into the mic channel and must come back exact.
   This proves the measurement works on this data at all; it does
   not test alignment (both channels share whatever error the
   composition has). A failure here means the pair is malformed,
   and nothing downstream means anything.
2. **Tap injection, the alignment measurement.** `inject.py` adds
   the tap-decoded track, delayed and attenuated (-30 dB, matching
   the round 1 reference), into the mic channel, and
   `scripts/echo_leakage.py` runs over the result with ref still
   the buffer track. This simulates the production echo path (wire
   to air to mic) against the reference an adopted pipecat would
   offer (the buffer recording). If the buffer track is aligned
   with the wire, the measurement recovers the injected delay and
   gain; a constant lag bias names a fixed offset (buffering
   between TTS output and transport send, epoch error), lag
   scatter or detection loss names drift or jitter.
3. **Both delays, and enough audio.** Run 2 executes at 250 ms and
   1500 ms injected delay, the two points where samtal's own
   capture passes, over an exchange totalling at least two minutes
   of reply audio (several turns, or a long canned clip; the round
   1 reference had 116 windows). Two minutes also gives resampling
   drift room to accumulate to where the lag bar would see it.

### The bar

Same as the existing control, applied to the tap-injection runs at
both delays: detected in at least 90% of candidate windows, median
lag within 20 ms of the injected delay, median gain within 3 dB of
the injected gain, and the stable-path criterion (lag IQR under
50 ms) holding. Passing both delays passes gate 1. Any constant
bias, drift rate, or jitter figure measured on the way is recorded
in the findings even on a pass, because it prices the engineering
an adoption would need.

## What this spike does not challenge

Per the issue's constraints, the product promises are fixed
(`docs/architecture/principles.md`): the serializer speaks stock
xiaozhi (compatibility floor; the simulator, unmodified, is the
proof), and the spike pipeline runs fully local (local-first). All
four architecture principles are on the table; the promises are
not. The observability constraint from #84 stands whatever the
gates say: the reasoned decision events survive only in self-owned
processors.

## Deliverables and paper trail

- The spike code under `spikes/pipecat-alignment/`, committed on
  the branch in small units, throwaway by default: nothing lands in
  `samtal_server/`, and merging the PR does not make the spike a
  supported surface.
- The implementation doc, one section per milestone, recording
  deviations, resolutions of this plan's open questions, and the
  measured numbers: both gates' verdicts, the line counts, the
  seam-obligation map, offsets and drift figures, and the pinned
  pipecat version.
- A closing comment on #84 with the evidence record, and a
  `CHANGELOG.md` entry. The PR closes #89.

## Risks and mitigations

- **Pipecat API drift against what this plan assumes.** The plan
  names components (FastAPI websocket transport, Silero analyzer,
  `AudioBufferProcessor`) from documentation, not from running
  code. The implementer verifies against the pinned version first
  and records renames or reshapes as deviations; if a named
  component does not exist in usable form, that is itself gate 2
  evidence.
- **The simulator's handshake requirements.** xiaozhi-sdk may
  require the OTA discovery step and specific hello fields; the
  integration lane's `conftest.py` is the working reference for
  driving it.
- **A failed stock control on the composed pair.** That means the
  composition is wrong, not that pipecat failed; fix the composer
  and rerun. Gate 1 is only ever judged on runs whose stock
  control passes.
- **Epoch construction error read as pipecat misalignment.** The
  constant-bias component of run 2 is reported as a range with the
  epoch method named, and the verdict leans on the stability
  criteria (detection rate, lag IQR, drift) that no epoch error
  can fake. If a constant bias alone breaches the 20 ms bar, the
  finding says exactly that, because an adoption would face the
  same construction.
- **The stale bytecode trap** (AGENTS.md). The spike runs scripts
  outside pytest repeatedly while editing them; export
  `PYTHONDONTWRITEBYTECODE=1` in the spike shell, and if a result
  contradicts the source, suspect the cache first.

## Open questions

- Whether xiaozhi-sdk accepts a direct websocket URL or the OTA
  stub is required (implementation discovers; either is fine).
- Whether pipecat's websocket transport paces audio out at frame
  cadence or writes as fast as encoding allows. Not a blocker
  either way: the simulator buffers. But it changes what the tap
  timeline means, and the answer is itself gate 1 evidence
  (pacing jitter absent from the recorded track is a named
  failure mode).
- Whether one long canned reply or many shorter turns is the
  better window source. Implementation picks whichever the
  simulator sustains; the two-minute reply-audio floor stands
  either way.

## Milestones

Ticked with the PR number when done, each linking to its section
of the implementation doc.

- [ ] **One full exchange**: the spike project scaffolding, the
  serializer, the minimal pipeline; xiaozhi-sdk completes hello,
  an utterance, and hears the canned reply back. Accept: the
  exchange runs locally end to end, driven by `drive.py`, no
  cloud, no keys.
- [ ] **Instrumentation and a well-formed pair**: the tap, the
  buffer recording, the composer; the stock control passes on a
  composed capture. Accept:
  `echo_leakage_control.py` reports PASSED on the pair,
  unmodified scripts.
- [ ] **The alignment verdict**: tap injection at 250 and
  1500 ms over at least two minutes of reply audio, numbers
  against the bar. Accept: both runs executed and recorded,
  verdict stated with the measured lag bias, IQR, and detection
  rate, whichever way it goes.
- [ ] **The size verdict and the paper trail**: serializer and
  glue line counts against 883, the seam-obligation map, the
  implementation doc complete, changelog entry, the #84 evidence
  comment drafted in the PR (posted on merge). Accept: both gate
  verdicts stated in the implementation doc with numbers, PR
  closes #89.
