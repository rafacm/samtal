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
- **VAD**: Silero through pipecat's bundled analyzer, local. The
  simulator's utterance is a real 16 kHz speech clip followed by
  paced silence, not the integration lane's 300 Hz sine: that
  lane's mock providers never cared, but Silero is a speech
  detector and a tone that fails to trip it would stall the
  exchange for a reason that looks like a spike bug.
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
(`samtal_server/device/session.py`).

The comparison is defined here, before any code exists, so the
numbers cannot be shaped to flatter the outcome:

- **Denominator.** Two baselines, both reported: the 883 lines
  issue #89 pins (the implementation doc's snapshot at the
  boundary work), and the file's physical line count at this
  branch's base commit, named by commit id. Counting method for
  every figure is physical lines (`wc -l`), stated once and used
  everywhere.
- **Numerator.** Everything an adoption would have to keep and
  maintain, regardless of filename: the serializer, any transport
  subclass or override it forces, any pacing layer added (per the
  tap section), any state machine or helper the exchange needs.
  The canned reply service, the OTA stub, the tap, and the
  composer are measurement harness, reported separately, outside
  the comparison.
- **The obligation map.** For every `SessionInput` and
  `DeviceOutput` obligation, one row: where it lands (framework,
  serializer, glue), the evidence when the claim is "the
  framework absorbs it" (the component and the behavior
  observed, not a documentation citation), whether the spike
  actually exercises it, and what production code would remain.
  "Required but not implemented by the spike" is an explicit
  category, distinct from absorbed: the spike serializer speaks
  one exchange, while the bespoke edge also carries rejection
  paths, close codes, listening policy, pause and resume, idle
  limits, capture, and device tools, and rows the spike does not
  cover must say so rather than vanish from the denominator's
  side of the ledger.
- **The comparable slice.** Alongside full-file counts, the
  findings name which bespoke-edge responsibilities the spike's
  exchange actually exercises and give the bespoke line count of
  that slice, so there is one honest small-vs-small number next
  to the honest small-vs-whole one.
- **The qualitative bar, fixed now.** The adapter has become an
  impedance-matching layer when it re-implements scheduling or
  pacing the framework claims to own, duplicates framework state
  to correct its timing, or grows a per-reply state machine
  beyond message translation. Any of those is a finding against
  adoption regardless of what the alignment says; a small
  adapter that stays translation is evidence for
  normalize-the-hardware-edge with pipecat behind it.

Anything the seam could not express is a finding against the
comparison's premise and gets recorded either way.

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

- **The tap**: every outgoing Opus packet, timestamped at the
  latest observable boundary, immediately after the awaited
  websocket send for that packet returns, not at serialization.
  Serialization can precede framework queuing, pacing, and the
  actual write, and a tap upstream of those would call audio
  "sent" that had not left yet, which is the exact mistake the
  spike exists to catch. The tapped packets are decoded to PCM
  and laid out on the send timeline by the epoch rules below.
  This matches samtal's capture semantics for channel 1 and is
  the ground truth for what the device actually heard, and when.
  The tap also reports the inter-send interval distribution
  against the 60 ms frame cadence: paced output shows a tight
  mode at 60 ms, bursting shows up directly, and either way the
  distribution is gate 1 evidence. If the transport turns out
  not to pace, the spike either adds production-representative
  pacing and counts it as adapter glue in gate 2, or records the
  absence as gate evidence; what it must not do is let the
  simulator's receive buffering quietly absorb the question.
- **The buffer recording**: `AudioBufferProcessor`'s bot track (and
  user track, for the mask), as pipecat records it.

Both recordings are placed on one shared timeline by a mapping
that is predefined here, before any correlation result is seen,
and never adjusted afterwards:

- One monotonic clock, read once before recording starts, is the
  epoch for everything.
- Every wire send is timestamped on that clock at the moment the
  awaited websocket write returns; every buffer-track delivery
  from `AudioBufferProcessor` is timestamped on the same clock at
  the moment the spike's handler observes it, together with its
  cumulative sample count.
- Sample placement is explicit: a buffer delivery of N samples
  observed at time t occupies (t - N/rate, t]; tap packets are
  placed starting at their send timestamp and never overwrite an
  earlier packet, so packet k occupies
  `start = max(previous_end, round(send_t * rate))` onwards and a
  gap appears only when a send arrives later than the previous
  packet's playout would have ended; gaps in either track are
  silence. Leading silence is kept, never trimmed.

  **This rule was corrected by the PR review round, after the first
  runs and with the correction recorded rather than folded in.** The
  mapping as first pinned had tap packets *end* at their send
  timestamp. That is wrong in two independent ways. pipecat's output
  transport sends a chunk and only then sleeps, so the timestamp
  taken when the awaited send returns opens that packet's 60 ms
  playout slot rather than closing it, and placing the packet before
  its own timestamp shifts the whole tap track one frame early.
  samtal's own capture, the semantics this spike exists to mirror,
  places decoded audio starting at the send time and keeps packets
  contiguous when sends arrive early (`capture.py`: `at =
  max(channel.next_frame, self._frame_of(now), self._start_frame)`).
  Ending-at placement also silently overwrote earlier audio whenever
  two placements collided, which is data loss dressed as a mapping.
  Amending a pinned method after seeing results is exactly what the
  plan set out to avoid, so the amendment is stated here, its reason
  is a property of the transport and of samtal's capture rather than
  of any measured lag, and every downstream figure was rerun under
  it.
- No onset-based or correlation-based shifting of either track,
  ever: aligning by first audible sample or by best correlation
  would erase exactly the fixed latency under test.
- The raw timestamp logs (per-send, per-delivery) are written to
  disk beside the WAV and kept, so the mapping is auditable after
  the fact.

If `AudioBufferProcessor` turns out to expose only an aggregate
buffer with no per-delivery observation point that can be
timestamped independently, that is itself a gate 1 finding to
record, not a gap to paper over with inferred alignment.

The composer then writes a samtal-format capture pair:

- `<session>.wav`, stereo 16 kHz s16le: channel 0 (mic) is the
  simulator's utterance audio as received, on the shared epoch,
  otherwise untouched (no added noise: the injection is what
  makes the channel non-silent, and synthetic noise would only
  obscure what the normalization guards already handle); channel
  1 (ref) is the buffer recording's bot track, resampled to
  16 kHz as samtal's capture does.
- `<session>.jsonl`: the minimal event track the analysis reads,
  on the same epoch: `session_open`, one `heard` per utterance
  (with `duration_s`, so the user-speech mask excludes it), and
  one `speaking_started` per reply (with `agent`), stamped from
  the tap's first packet.

### Resampling and drift, the method

Rates and resampling are stated per observation point, because a
resampler of the spike's own can manufacture the offset or drift
it would then attribute to pipecat:

- The native rate at every point is named in the findings: the
  simulator's utterance (16 kHz), the tap's decoded PCM (24 kHz,
  the announced output rate), and the buffer track at whatever
  rate `AudioBufferProcessor` actually delivers, which the spike
  verifies rather than assumes.
- Both tracks that need 16 kHz for the pair are converted by the
  same stateful resampler, run once over each full continuous
  track, never per packet or per delivery (stateless per-chunk
  resampling resets filter state at every boundary and injects
  exactly the artifacts under test). Input and output sample
  counts and the resampler's filter delay are recorded, and the
  same figures go in the findings.
- Where resampling happens inside the live pipeline is observed
  and recorded, not inferred from the canned clip's rate: a
  24 kHz clip proves nothing about which stage resampled if a
  service upstream already converted it.
- The drift statistic is fixed now: the robust slope
  (Theil-Sen) of per-window measured lag against window time, in
  ms per minute, plus the difference between the median lags of
  the first and last quartile of windows. Drift that moves lag
  by more than 20 ms across the measured span fails gate 1, the
  same spirit as the lag bar; anything smaller is reported with
  the run's detectable floor stated (a two-minute span cannot
  see slopes that move lag less than the window lag resolution),
  and the run is lengthened if a smaller drift matters to the
  verdict.

### The runs

1. **Stock control, the sanity baseline, at both delays.**
   `scripts/echo_leakage_control.py` on the composed pair, run
   twice: `--delay-ms 250` and `--delay-ms 1500`, matching the
   short and long controls #89 names (the control widens its own
   lag search past the injected delay). Both must come back exact.
   This proves the measurement works on this data at all; it does
   not test alignment (both channels share whatever error the
   composition has). A failure here means the pair is malformed,
   and nothing downstream means anything.
2. **Tap injection, the alignment measurement.** `inject.py` adds
   the tap-decoded track, delayed and attenuated (-30 dB, matching
   the round 1 reference), into the mic channel, and
   `scripts/echo_leakage.py` runs over the result with ref still
   the buffer track. The script's default lag search stops at
   1.2 s, which would put the 1500 ms echo outside the search
   space entirely; both tap runs therefore pass an explicit
   `--max-lag-s` of at least 2.0, wide enough past the longer
   injection that a substantial fixed bias is diagnosed rather
   than truncated, and the findings record whether any measured
   lag lands at the search boundary, because a boundary value
   means the search was still too narrow, not that the lag was
   measured. This simulates the production echo path (wire
   to air to mic) against the reference an adopted pipecat would
   offer (the buffer recording). If the buffer track is aligned
   with the wire, the measurement recovers the injected delay and
   gain; a constant lag bias names a fixed offset (buffering
   between TTS output and transport send, epoch error), lag
   scatter or detection loss names drift or jitter.
3. **Both delays, over the same pair, with enough windows.**
   Run 2 executes at 250 ms and 1500 ms injected delay, the two
   points where samtal's own capture passes, and both injections
   run offline over the same captured pair, so the two verdicts
   differ only in the injection. The floor is stated in windows,
   not minutes, because windowing, the discarded lag-search
   prefix, the reply-level threshold, and the user mask all eat
   candidates: at least 100 candidate windows per delay, the
   round 1 scale. That implies on the order of two minutes of
   reply audio, preferably one long non-repeating speech-like
   clip rather than many short turns (repetition creates
   correlation ambiguity, and turn boundaries multiply masked
   stretches), and long audio also gives resampling drift room
   to accumulate to where the drift statistic can see it. The
   findings report candidate windows, hits, and how much audio
   the mask and thresholds discarded, so a thin result cannot
   pass silently.

### The bar

Same as the existing control, applied to the tap-injection runs at
both delays: detected in at least 90% of candidate windows, median
lag within 20 ms of the injected delay, median gain within 3 dB of
the injected gain, and the stable-path criterion (lag IQR under
50 ms) holding, plus the drift bar above (lag moved by more than
20 ms across the measured span fails). Passing both delays passes
gate 1. Any constant
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
  code. The first hour of implementation is a feasibility
  checkpoint on the load-bearing specifics, before any serializer
  code is written: whether the transport's serializer contract
  carries mixed JSON text and binary audio in both directions,
  where the hello handshake can run relative to pipeline start,
  whether the output path exposes a per-packet awaited send to
  tap, what `AudioBufferProcessor`'s delivery and timing
  semantics actually are, and the serializer's lifecycle hooks.
  Each answer lands in the implementation doc. If the answer
  forces a custom transport rather than a serializer, the spike
  proceeds and counts that transport as adapter code in gate 2,
  which is exactly the kind of evidence the gate exists to
  surface; if a named component does not exist in usable form,
  that too is gate 2 evidence, not a dead end.
- **The simulator's handshake requirements.** xiaozhi-sdk may
  require the OTA discovery step and specific hello fields; the
  integration lane's `conftest.py` is the working reference for
  driving it.
- **A failed stock control on the composed pair.** That means the
  composition is wrong, not that pipecat failed; fix the composer
  and rerun. Gate 1 is only ever judged on runs whose stock
  control passes.
- **Epoch construction error read as pipecat misalignment.** The
  mapping is predefined above and frozen before any correlation
  result is seen, and its raw timestamp logs are kept, so a
  disputed lag can be re-derived from the logs rather than argued
  about. A constant epoch error is stable, so stability criteria
  cannot catch it; what bounds it is the mapping's own audit trail
  (scheduling delay between a send returning and its timestamp is
  the residual, and that is microseconds to low milliseconds, not
  tens). If a constant bias breaches the 20 ms bar anyway, the
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
  cadence or writes as fast as encoding allows. The tap's
  inter-send distribution answers this empirically, and the
  instrumentation section says what each answer costs: no pacing
  means either production-representative pacing added and
  counted as adapter glue, or the absence recorded as gate
  evidence. The simulator buffering the result proves receipt,
  not a playback clock, so it settles nothing.
- How many turns the simulator sustains in one connection, and
  whether the 100-window floor is met by one long reply or a few
  long ones. The floor is in candidate windows and the source is
  preferably one long non-repeating clip; within that, the
  implementation picks what the simulator sustains.

## Plan review round

One external review of the plan as first committed (898a905): codex
CLI 0.147.0, model gpt-5.6-sol, read-only against this repository
with both issue bodies supplied, 2026-08-11. Verdict: well scoped
and faithful to the issue; the tap-injection concept sound; not
ready to implement, because four findings could each independently
produce an invalid gate verdict. Findings as received, condensed;
each carries its resolution once the amendment addressing it lands.

1. **P1: the 1500 ms run cannot pass as specified.**
   `echo_leakage.py` searches lags up to 1.2 s by default, so a
   1500 ms echo is outside the search space; the control widens its
   own range but the plan's tap runs did not. The plan also ran the
   stock control once where #89 asks for short and long.
   *Resolution*: the stock control now runs at both 250 and
   1500 ms; the tap runs pass an explicit `--max-lag-s` of at
   least 2.0, and any lag landing at the search boundary is
   recorded as a too-narrow search, not a measurement.
2. **P1: the epoch construction is too vague to support the
   absolute-lag verdict.** "Recording start plus sample counting"
   does not define whether the buffer retains leading silence, when
   sample zero becomes valid, or how scheduling delay is
   represented; the stock control cannot detect an epoch error, and
   stability cannot either, since a constant epoch error is
   perfectly stable and wrong. Onset- or correlation-based
   alignment would erase exactly the latency under test.
   *Resolution*: the mapping is now predefined in the
   instrumentation section and frozen before any result is seen:
   one monotonic epoch read before recording, timestamps at the
   awaited send return and at each buffer delivery with its
   cumulative sample count, explicit sample placement rules,
   leading silence kept, no onset or correlation shifting, and
   the raw timestamp logs preserved for audit. A buffer with no
   independently timestampable delivery point is itself a gate 1
   finding.
3. **P1: the tap is not yet established as wire ground truth.**
   A packet "leaving the serializer" may precede framework
   queuing, pacing, and the awaited websocket write; and if the
   transport bursts, the simulator buffering the result hides a
   production obligation named in #89 rather than settling it.
   *Resolution*: the tap now sits immediately after the awaited
   websocket send returns, reports the inter-send interval
   distribution against the 60 ms cadence as gate 1 evidence,
   and a non-pacing transport costs either
   production-representative pacing counted as adapter glue or
   an explicit gate finding; the "not a blocker" open question
   is rewritten accordingly.
4. **P1: gate 2 is not yet a like-for-like size comparison.** The
   spike serializer supports one exchange while the bespoke edge
   covers far more; the obligation map lacked a "required but not
   implemented" category; the 883 figure is a historical snapshot
   (the file is 899 physical lines at current HEAD); the counting
   method and the qualitative "impedance-matching layer" bar were
   undefined.
   *Resolution*: the gate 2 section now fixes the comparison
   before implementation: both baselines reported with commit ids
   and `wc -l` as the stated method, the numerator counts
   everything adoption-required regardless of filename, the
   obligation map gains a "required but not implemented" category
   with evidence required for "absorbed" claims, a comparable
   bespoke slice is counted alongside the full file, and the
   impedance-matching bar is defined (re-implemented scheduling,
   duplicated framework state, a per-reply state machine beyond
   translation).
5. **P2: two minutes of reply audio does not guarantee enough
   candidate windows**, after windowing, the discarded lag-search
   prefix, the level threshold, and the user mask; and the added
   mic noise was unspecified where it should be either dropped or
   pinned.
   *Resolution*: the floor is now at least 100 candidate windows
   per delay (the round 1 scale) rather than a clock figure, both
   injections run offline over the same captured pair, one long
   non-repeating clip is preferred, the findings report windows,
   hits, and discarded audio, and the added noise is dropped
   (the injection already makes the channel non-silent).
6. **P2: resampling and drift need an explicit method.** The tap
   must also be resampled to 16 kHz, through the same stateful
   resampler as the buffer track; a drift statistic was promised
   but never defined, and the 50 ms IQR bar only catches large
   drift over two minutes.
   *Resolution*: a resampling-and-drift method section now names
   the native rate at every observation point, requires one
   stateful resampler run over each full track with sample counts
   and filter delay recorded, observes rather than infers where
   the live pipeline resamples, and fixes the drift statistic
   (Theil-Sen slope of lag over time plus first-vs-last-quartile
   lag difference, with more than 20 ms of movement across the
   span failing gate 1 and the detectable floor stated).
7. **P2: two mechanics should be front-loaded as feasibility
   checks**: whether the transport's serializer contract supports
   mixed JSON and binary plus hello handling and exposes the real
   send boundary; and that Silero must be driven by real speech
   audio, not the integration lane's 300 Hz sine.
   *Resolution*: milestone 1 now opens with a first-hour
   feasibility checkpoint (mixed frame types, hello routing, the
   send hook, buffer delivery semantics, serializer lifecycle),
   whose answers land in the implementation doc, with a forced
   custom transport counted as gate 2 adapter code; and the
   utterance is a real 16 kHz speech clip followed by paced
   silence.

## Milestones

All milestones land in one pull request. They are ticked with the
issue number rather than the PR's, because the spike's branch carries
no PR at the time they were ticked; substitute the PR number when it
is opened. Each links to its section of the implementation doc.

- [x] [**One full exchange**](2026-08-11-pipecat-alignment-spike-implementation.md#milestone-1-one-full-exchange)
  (PR #90): the feasibility checkpoint from the
  risk section first (mixed frame types, hello routing, the send
  hook, buffer delivery semantics, serializer lifecycle, answers
  recorded), then the spike project scaffolding, the serializer,
  the minimal pipeline; xiaozhi-sdk completes hello, an
  utterance of real speech, and hears the canned reply back.
  Accept: the exchange runs locally end to end, driven by
  `drive.py`, no cloud, no keys, and the checkpoint's answers
  are in the implementation doc.
- [x] [**Instrumentation and a well-formed pair**](2026-08-11-pipecat-alignment-spike-implementation.md#milestone-2-instrumentation-and-a-well-formed-pair)
  (PR #90): the tap, the
  buffer recording, the composer; the stock control passes on a
  composed capture. Accept:
  `echo_leakage_control.py` reports PASSED on the pair,
  unmodified scripts.
- [x] [**The alignment verdict**](2026-08-11-pipecat-alignment-spike-implementation.md#milestone-3-the-alignment-verdict)
  (PR #90): tap injection at 250 and
  1500 ms over the same captured pair, at least 100 candidate
  windows per delay, numbers against the bar. Accept: both runs
  executed and recorded,
  verdict stated with the measured lag bias, IQR, and detection
  rate, whichever way it goes.
- [x] [**The size verdict and the paper trail**](2026-08-11-pipecat-alignment-spike-implementation.md#milestone-4-the-size-verdict-and-the-paper-trail)
  (PR #90): serializer and
  glue line counts against both baselines with the comparable
  slice, the seam-obligation map, the
  implementation doc complete, changelog entry, the #84 evidence
  comment drafted in the PR (posted on merge). Accept: both gate
  verdicts stated in the implementation doc with numbers, PR
  closes #89.
