# A failed reply says so, and a leaked tool call is never spoken

Plan for [#384](https://github.com/rafacm/vinga/issues/384) and
[#385](https://github.com/rafacm/vinga/issues/385), one plan because
the issues share their answer: what replaces an unsayable sentence
and what replaces a failed reply is the same cached clip. #343 is
the earlier filing of #384's problem and is closed by the same
work. Implementation notes land in the companion
`2026-09-04-reply-failure-fallback-implementation.md`, one section
per milestone, appended in the change that ticks the milestone
here.

## Goal

Today a terminally failed reply is deliberately a silent turn: the
general failure arm logs one class name and nothing reaches the
speaker or the display, so from the couch a broken pipeline is
indistinguishable from a slow one, and diagnosing the walkthrough's
cold Ollama took an hour and a `docker logs`. Separately, a model
that emits a tool call as ordinary prose is read out loud and
rendered verbatim, the one user-facing surface with no filter on
untrusted content. This plan gives the reply path a bounded voice
for both: a short fixed phrase per agent, synthesized at boot in
the agent's own voice and cached as PCM exactly the way the filler
clips are, spoken from the failure arm and shown on the display;
and a narrow per-sentence guard that withholds a sentence shaped
like a call to a tool this session actually offered, emitting an
event, with the same clip covering the reply that ends with
nothing sayable in it.

## The issues' decisions, restated

From #384:

- A short fixed phrase is spoken from the general failure arm, from
  a clip cached at boot per agent; caching is the design, because
  synthesis at failure time adds latency to a failed turn and the
  TTS provider may itself be what failed.
- Only the general failure arm speaks. `DeviceGone` cannot;
  `CancelledError` must not, because a barge-in means the user is
  talking.
- A fixed phrase, never `str(exc)`; provider text is untrusted.
- The display shows it too.
- It can be turned off, the way the filler can, for a deployment
  that would rather have silence.

From #385:

- The test is on the sentence about to be spoken, not the whole
  reply, so one bad sentence does not discard a good answer.
- The check is narrow: the shape of a call to a tool this session
  actually offered, never "looks like JSON"; someone asking the
  agent to explain a JSON snippet is a real conversation.
- An event fires when it does; a model doing this repeatedly is a
  fact about the deployment's model choice the operator should see.
- What replaces the withheld sentence is answered together with
  #384.

## Where the facts already live

The failure arm is `_reply`'s `except Exception` (`pipeline.py`
lines 1282-1297), whose comment already states the no-leak policy
the phrase must keep; the `finally` (1298-1342) settles the filler,
emits `Replied` only when `spoken` is non-empty, records the turn,
and sends the closing `tts stop` through `finish_speaking`, which
is what re-arms the device's listening and must stay reachable
whatever the fallback does. Sentences flow through
`SentenceSplitter` at exactly two sites in `_tool_loop` (the
`push` loop at 1582-1585 and the `flush` tail at 1599-1603), and
the offered tools are the local `tools` in the same scope, so the
guard needs no new state on the runtime. The paced-clip recipe the
fallback copies is `FillerRunner._fire` (`runtime/filler_runner.py`
217-230), including the rule that the three encoder calls take no
await between them because the reply task feeds the same encoder,
and its failure arms (231-262) are the model for swallowing
correctly. Boot synthesis and caching live in `filler.py`
(`FillerClips`, `build_agent_fillers`), invoked from `app.py` at
boot and `config/reload.py` at reload, carried per generation
(`generation.py`), and handed to the runner, never stored on the
runtime. The filler deliberately sends no `sentence_start` and
stays out of the transcript everywhere; the fallback deliberately
differs, and the resolution below says why.

## Open questions, resolved

**The fallback is vinga's own words: displayed, but not the
agent's speech.** The filler's stay-out-of-the-transcript rule
exists because a noise that buys time is not a sentence of the
reply. The fallback is different in one direction only: it carries
information the user needs, so it goes to the display
(`sentence_started`, the only display mechanism the protocol has)
as well as the speaker. It is still not something the model said,
so it never enters `spoken`, the assistant `Turn`, the working
history, or the conversation record, and `Replied`/`AgentSaid`
keep their meaning of model speech that went out. What says it
happened is its own event, below. The turn record already records
the failed reply exactly as today.

**One shelf, two kinds of clip.** `filler.py` deepens into the
module that owns per-agent boot-cached speech: `build_agent_fillers`
grows into building both the filler phrases and the one fallback
phrase per agent, under one staleness rule (each kind keyed by its
own config section and the TTS provider identity, so toggling the
filler cannot stale the fallback or the reverse), reusing its
existing never-fails-the-boot arm. A fallback phrase that will not
synthesize degrades rather than disappears: the display half of
#384 needs no PCM and no working TTS provider, so the cache keeps
the configured phrase with no clip, a terminal failure still sends
the display message and closes with `tts stop`, only the audio is
lost, and the telemetry below distinguishes the two without
carrying the phrase. The
cache shape is settled here rather than left open. `FillerClips`
stays exactly what it is; a frozen `FallbackClip` (phrase, clip
bytes or `None` for display-only, sample rate) joins it, and
`Fillers` grows a sibling per-agent mapping `fallbacks` beside
`clips`, plus its own per-kind outcome names
(`fallback_resynthesized`, `fallback_reused`,
`fallback_degraded`), because the existing
`resynthesized`/`reused`/`disabled` lists describe the filler and
cannot honestly describe an agent whose filler was reused while
its fallback resynthesized or failed. `Generation` carries the new
mapping beside `fillers`; the reload result and its response model
grow the per-kind outcome fields, which regenerates the OpenAPI
document; `config/diff.py` gains `_FALLBACK_FIELDS` and
`same_fallback` beside the filler pair; and `config/entities.py`
gains the `fallback` `NestedShape` beside the filler's, with the
`views.py` treatment for `phrase`. One build pass produces both
kinds, and the reload diff re-runs each exactly when its own
section or the voice changed. The deletion
test rejects a new `fallback.py` build module: it would restate
`build_agent_fillers` line for line against a second phrase list.

**Playing lives in the runner; the arm just asks.** `FillerRunner`
already holds the output handle, the clip view, and the paced
recipe; it gains `speak_fallback()`, which sends
`sentence_started(phrase)` whenever the section is enabled, then
the clip through the existing recipe when one was cached
(display-only degradation otherwise), swallowing `DeviceGone` and
reporting any other failure by class name outside the arm,
filler-style. The failure arm calls it after the reply-failed log and
after `await self._filler.settle()` (so a sounding filler clip is
not talked over and the shared encoder is not interleaved); the
`finally`'s own `settle()` stays and is idempotent. The contract,
exactly the filler runner's own: `CancelledError` propagates,
because swallowing it would consume a barge-in or an abort and the
outer `finally` runs and sends `tts stop` either way;
`DeviceGone` is swallowed; every other failure is sanitized to its
class name and swallowed, reported outside the arm. So the arm's
call cannot prevent `finish_speaking`, and a cancellation arriving
mid-fallback stops the clip promptly with `tts stop` still
attempted exactly once, which the tests drive. The `_reply`
`CancelledError` and `DeviceGone` arms are untouched.

**Config: an agent-layer `fallback` section, on by default.**
`FallbackConfig` beside `FillerConfig`: `enabled: bool = True`,
`phrase: NonBlankStr` defaulting to a fixed English sentence
("I ran into a problem and could not answer. The server log has
the details."), inheritable from `agent_defaults` and replaceable
wholly per agent, `extra="forbid"` like everything else. On by
default because the silent turn is at its worst during onboarding,
where misconfiguration is likeliest and nobody has a log open; the
deployment that prefers silence turns it off, which is the issue's
own asymmetry. The domain-config reference, the OpenAPI document
and the example agent files regenerate or update accordingly.

**The guard: isolate any complete JSON object in the sentence,
then match it against the offered tools two ways.** A sentence may
carry a call beside prose (`Sure: {"volume":"100"}`, or a call
followed by `Okay.` inside one cut), so the test is not "the
sentence parses" but "the sentence contains a complete object that
matches": the guard walks the sentence's `{` positions and asks
`json.JSONDecoder().raw_decode` for a balanced object at each,
bounded by the sentence's own length. A decoded object matches,
and the whole sentence is withheld, in either of two cases, both
anchored to the tools this session actually offered:

- **A named call**: the object's own `"name"`, or the `"name"`
  inside an object under its `"function"` key (the OpenAI wire
  shape models parrot), equals a published name from the snapshot.
- **An argument-only call**, the shape actually observed
  (`{"volume":"100"}` carries no name): the object's key set is
  non-empty and is a subset of the declared top-level `properties`
  of at least one offered tool's `input_schema`. Matching is by
  key names only, never by validating values, since the observed
  value had the wrong type, and a set matching several tools'
  schemas is withheld the same, because every reading of it is
  tool-shaped. The cost is stated: an agent speaking a JSON
  example whose keys mirror an offered tool is withheld too, the
  event makes it visible, and a deployment for which that is wrong
  turns the model, not the guard.

Everything else speaks: JSON naming no offered tool and matching
no offered schema, and prose. The guard and its emission live
behind one helper beside the speaking machinery in
`runtime/speech.py`, taking the sentence and the offered
`ToolDef`s; inlining it at the two call sites would duplicate the
rule, and `text.py` stays ignorant of tools. A stated bound,
recorded in the module docstring: a pretty-printed multiline call
is chopped by the splitter's newline rule into fragments no
decoder can read, and closing that would mean buffering sentences
that have already been promised to TTS, stalling live speech on
every ordinary `{`; the event is what keeps the residue visible.

**A withheld sentence enters nothing.** The record's `reply` is
what the user heard, assembled solely from what was spoken, and no
raw generated-text channel exists; the withheld bytes therefore
enter neither the leg, nor `spoken`, nor the working history and
assistant turns, nor the `TurnRecord`, nor any event payload or
log line, and the sentinel case in the tests proves every one of
those absences.

**Nothing replaces a withheld sentence unless nothing else spoke,
and "nothing else spoke" is a reply-wide fact.** Mid-reply, a
withheld sentence is dropped and its event emitted; the
surrounding answer speaks, which is the issue's own constraint.
`spoken` cannot carry the reply-wide question, because
`_speak_reply` clears it after every completed leg, so a reply
where an earlier agent spoke and the final leg was wholly withheld
would look empty. The check therefore reads two per-reply facts
owned by `_speak_reply` and listed in the runtime docstring's
state inventory: whether any sentence of this reply was spoken
across all legs, and whether any was withheld. When the reply ends
having spoken nothing and withheld something, the user is about to
get the exact silence #384 exists to end, so the same fallback
plays (its event carrying the other reason). That happens at the
end of `_speak_reply`, not by raising, so the reply-failed log
stays what it means. The tests drive both handover orders: speech
before a withheld final leg plays no fallback; a wholly unsayable
multi-leg reply plays it.

**Two event declarations, payloads under the content rule.**

- `sentence_withheld`, session channel: the sentence's length in
  characters (never its bytes), and the tool identified the way
  `tool_call` identifies one. The catalog permits one concrete
  value type per field and `_tool_fragment` returns three, so this
  follows `tool_call`'s own answer to the same problem:
  source-specific variants under one declaration, selected by the
  existing classifier, one per fragment shape (builtin named in
  this server's vocabulary, an entry-owned tool by its entry, a
  far-side or unmatched name identified without repeating it; an
  argument-only match that resolves to no single tool rides the
  unnamed variant). A sentinel-bearing device or MCP tool name in
  the test proves no far-side bytes reach payloads or logs.
- `reply_fallback`, session channel: reason from a closed set with
  one variant per reason, filler-style: `reply_failed` (the
  failure arm) and `nothing_sayable` (the empty-reply case), each
  carrying whether audio played or the turn degraded to
  display-only (finding 4's distinction, a boolean, never the
  phrase). A fallback skipped because the section is off emits
  nothing; absence of the feature is not a decision taken.
- `fallback_degraded`, on the filler build channel: a fallback
  phrase whose synthesis failed at boot or reload, its own
  declaration because `FillerDisabled`'s stated meaning is that
  latency masking is off, which this is not. Class-name-only
  failure vocabulary, like its sibling.
- The runner's playback-failure line for the fallback is a new
  UNTYPED entry with its exact channel and template
  (`"session %s: fallback playback failed: %s"` beside the
  filler's own line), registered in the closed UNTYPED set in
  `test_event_baseline.py`, because the filler's template says
  filler and reusing it would be semantic drift. The
  failure-arm tests plant a sentinel in both the exception message
  and its `__cause__` and inspect record internals and both
  renderings.

Both follow the full catalog discipline: declaration, baseline
driver, `CARRIED` row, regenerated `events.md`, README event index
rows. The fallback phrase itself is configuration, not content,
but it still stays off the events; the event says which reason,
never the words.

**The two smaller things in #384 stay small.** The 10 s
`llm_first_token_timeout_s` default keeps its value: with the
fallback audible, the cost of a conservative timeout drops from an
undiagnosable silence to a spoken notice, which weakens the case
for a global default tuned to cold local models; the README's
existing prose for that key gains the sentence naming the local
cold-load case and the remedy. The root README's Getting Started
gains one line naming `vinga events` as the first diagnostic stop.
Both are documentation footprint, not behavior.

## Module layout

No new module. `filler.py` deepens (one build pass, two clip
kinds), `runtime/filler_runner.py` deepens (`speak_fallback` on
the runner that already owns paced clip playback),
`runtime/speech.py` gains the pure predicate beside the machinery
that speaks sentences, `runtime/pipeline.py` wires the arm, the
two guard sites and the empty-reply check, and `config/models.py`
gains `FallbackConfig` beside `FillerConfig` with the same
resolution rule (`fallback_for_agent`). The seam the runner
already states (`FillerCache`-style read-only views, `TurnView`)
widens only by the fallback read.

## Tests

Existing assets carry the shapes; nothing they pin is restated.

- **The failure arm speaks** (`tests/unit/test_session_reply_failures.py`):
  a provider failure with the fallback enabled reaches the fake
  device as one `sentence_started` carrying the phrase and a
  non-empty audio batch; the secret-planting sentinel case extends
  to assert the sentinel reaches neither the display text nor any
  log in either format; `DeviceGone` and cancellation still
  produce nothing; with `enabled: false` the turn is byte-for-byte
  today's silence. The two "ends quietly" characterization pins
  (`test_session_characterization.py` 477, 507) stay untouched and
  silent under the default-on configuration, because what they
  drive is a send-path failure translated to `DeviceGone`, which
  must stay silent whatever the configuration says; the
  enabled-fallback and disabled-silence behaviors get their own
  new general-exception cases beside them.
- **The clip is cached, not synthesized at failure time**: the TTS
  provider fake counts synthesis calls; a failure turn adds none.
  A fallback phrase that fails boot synthesis degrades to
  display-only and the boot survives: a failure turn on that agent
  still sends `sentence_started(phrase)` and the closing
  `tts stop` with no audio batch, and the degradation is visible
  in the telemetry (`tests/unit/test_filler_cache.py`,
  `test_session_filler.py` shapes). Reload staleness: editing the phrase resynthesizes,
  toggling the filler alone does not touch the fallback clip, and
  the reverse (`config/diff` tests).
- **Coordination with the filler**: a failure while the filler
  clip is sounding settles it first, then speaks the fallback; the
  shared-encoder invariant test shape
  (`test_a_filler_sounding_never_sends_the_replys_packets`) gains
  the fallback variant.
- **The watchdog case end to end**: the issue's own trigger, a
  first-token timeout after retries, heard as the fallback
  (`tests/unit/test_session_watchdog.py`).
- **The guard** (`tests/unit/test_speech_guard.py`, new, plus
  session-level cases in `test_session_tools.py`): the helper
  matrix (compact call naming an offered tool withheld; the
  `function`-wrapped shape withheld; the observed argument-only
  payload `{"volume":"100"}` against the volume tool's schema
  withheld; a call embedded beside prose in one sentence withheld;
  JSON naming no offered tool and matching no offered schema
  spoken; JSON whose keys are not any offered tool's spoken; prose
  about JSON spoken); a scripted reply mixing a leaked call with a
  real sentence speaks only the real one and emits
  `sentence_withheld`; both caller paths of the centralized helper
  are driven, one case whose call is newline-terminated mid-stream
  so the sentence exits through `splitter.push`, and one whose
  call is the reply's unterminated tail so it exits through
  `flush`; a reply that is only a leaked call speaks
  the fallback with `nothing_sayable`. Withheld bytes reach no
  retained surface: a sentinel-bearing withheld sentence, with
  recording enabled and a following scripted round, is asserted
  absent from the device (`sentence_started` and audio), from both
  log formats, from event payloads, from the stored `TurnRecord`
  and its legs, and from the next round's request history.
- **Events**: drivers and `CARRIED` rows for all three
  declarations (`reply_fallback`, `sentence_withheld`,
  `fallback_degraded`); `events.md` and the README index
  regenerate through their generators; the UNTYPED set grows by
  exactly the one named playback-failure template and nothing
  else, asserted by the set's own both-directions test.
- **Message order**: the successful-turn ordering pin
  (`test_one_turn_has_the_control_message_order_the_firmware_expects`)
  stays untouched; a failure-turn ordering case pins
  `stt`, `tts start`, `tts sentence_start`, `tts stop` for the
  fallback turn.
- **Config surface**: the new section through
  `test_config_examples`, docgen, round-trip and the OpenAPI pin,
  regenerated through the generators.

## Risks

- **The shared Opus encoder.** The fallback send copies the
  filler's no-await-between-encoder-calls recipe verbatim; the
  encoder-interleaving test variant is the tripwire.
- **The finally must survive the fallback.** `speak_fallback`
  raises nothing but `CancelledError` by contract, the outer
  `finally` runs under cancellation too, and the arm calls nothing
  else; the
  reply-failures suite drives a fallback whose own send explodes
  and asserts the closing `tts stop` still goes out and exactly
  one reply-failed line is logged.
- **Default-on changes shipped behavior.** Deployments upgrade
  into a speaking failure arm. The CHANGELOG entry says so
  plainly, the README documents the off switch, and the phrase is
  fixed configuration, so nothing untrusted can reach it.
- **Doc and census staleness.** events.md, domain-config.md,
  api-openapi.json and the example files all regenerate through
  generators; the root README edit can stale the command-spellings
  manifest, regenerated through
  `uv run python -m tests.unit.test_command_spellings`.
- **Two issues, one plan.** M1 is releasable alone (#384 and #343
  close on it); M2 stacks for the guard (#385). A reply with a
  leaked call under M1 alone behaves exactly as today, so the
  intermediate `main` breaks nothing it does not already break.

## Milestones

- [ ] **M1: the failure arm gets a voice.** `FallbackConfig` and
  `fallback_for_agent`; the build pass extended to cache the
  fallback clip per agent with its own staleness key and the
  never-fails-the-boot reporting; `FillerRunner.speak_fallback`;
  the failure arm wired after `settle()`; the `reply_fallback`
  event (`reply_failed` variant reachable, `nothing_sayable`
  declared with it only if the catalog forbids a later variant
  addition, otherwise deferred to M2); the tests above minus the
  guard's; the README (filler section sibling prose, the
  first-token-timeout sentence, the silent-turn sentence at
  2221-2232 amended, the event index), `examples/agent-defaults.yaml`
  and `examples/agent.yaml`, `docs/conversational-quality-regression-suite.md`'s
  degraded-to-silence invariant re-worded, the generated
  references through their generators, a CHANGELOG entry; closes
  #384 and #343. Design footprint: deepens `filler.py` (one build
  pass owns per-agent cached speech), `filler_runner.py` (the one
  owner of paced clip playback grows its second caller's verb) and
  the config models; no new module. Documentation footprint as
  listed; the root README `vinga events` line lands here.
- [ ] **M2: the sentence guard.** The predicate in
  `runtime/speech.py`; the two `_tool_loop` sites; the
  empty-reply fallback with `nothing_sayable`; the
  `sentence_withheld` event; the guard and session tests; the
  README's reply-path prose naming the guard and its bound;
  `events.md` and the index rows; a CHANGELOG entry; closes #385.
  Design footprint: deepens `runtime/speech.py` (the sentence
  policy sits beside the sentence machinery) and the tool loop's
  two sites; no new state on the runtime, the offered set stays a
  loop local. Documentation footprint: the README reply-path
  paragraph and the event surfaces; `system-overview.md`'s step
  list gains the guard clause only if its current wording claims
  every sentence is spoken, confirmed during implementation.

## Plan review round

Backend codex (codex-cli 0.153.0), model `gpt-5.6-sol`, sandbox
read-only, 2026-09-04, against commit `d1c05512`; the reviewer ran
about 17 minutes. Verdict: ready after the P1/P2 amendments.

1. **P1: the proposed predicate does not catch the failure class.**
   The observed payload `{"volume":"100"}` carries no `name`, and
   the splitter's cutting rules mean a compact call followed by
   prose reaches the guard as one non-JSON sentence while
   pretty-printed JSON arrives as non-JSON fragments. Define how
   complete JSON values are isolated (mixed prose and multiline
   forms included), how argument-only objects are narrowly matched
   against the offered `input_schema`s (matching cannot require
   successful validation, since the observed value had the wrong
   type), resolve ambiguous schema matches, and test the observed
   payload directly.

   *Resolution*: accepted in full. The guard now isolates any
   complete JSON object inside a sentence with `raw_decode` over
   the sentence's `{` positions, matches it two ways (a named call
   against the published names, and an argument-only object whose
   non-empty key set is a subset of at least one offered tool's
   declared properties, values never validated), withholds on
   ambiguity with the reason stated, tests the observed payload
   directly, and states the multiline bound with the streaming
   argument for it.

2. **P1: retaining the withheld model text contradicts the no-leak
   rule and the record semantics.** The record's `reply` is what
   the user heard, assembled solely from `spoken`; no raw
   generated-text channel exists. Withheld bytes must enter
   neither `leg`, `spoken`, the working history, assistant turns
   nor `TurnRecord`, with a sentinel test across device, both log
   formats, event payloads, stored records and subsequent model
   history.

   *Resolution*: accepted in full. The wrong sentence in the test
   plan is replaced: a withheld sentence enters nothing, the
   design section says so against the record's own semantics, and
   the sentinel case asserts absence from the device, both log
   formats, event payloads, the stored record and its legs, and
   the next round's request history.

3. **P1: the proposed characterization changes would violate the
   DeviceGone constraint.** The two "ends quietly" pins drive
   socket disappearance translated to `DeviceGone`, which #384
   requires to stay silent regardless of configuration. Leave both
   pins silent under the default-on configuration and add separate
   general-exception cases for enabled fallback and disabled
   silence.

   *Resolution*: accepted in full; both pins stay untouched with
   the reason stated in the test plan, and the new behaviors get
   their own general-exception cases.

4. **P1: a failed boot synthesis unnecessarily removes the
   required display fallback.** The display message needs no PCM
   and no working TTS provider. Retain the configured phrase when
   synthesis fails: a terminal failure still sends the display
   message and closes with `tts stop`, audio becomes optional, and
   telemetry distinguishes audio delivery from display-only
   degradation without carrying the phrase.

   *Resolution*: accepted in full. The cache keeps the phrase with
   no clip, `speak_fallback` sends the display message whenever
   the section is enabled and plays audio only when a clip was
   cached, the boot-failure test asserts the display-only turn,
   and the events from finding 9's resolution distinguish the two.

5. **P1: `spoken` cannot determine whether the whole reply said
   nothing.** `_speak_reply` clears `spoken` after every completed
   leg, so an earlier leg's speech followed by a withheld final
   leg reads as empty. Introduce a reply-wide fact that survives
   leg clearing, and test both handover orders.

   *Resolution*: accepted in full; the check reads two reply-wide
   facts owned by `_speak_reply` and listed in the runtime state
   inventory, and both handover orders are in the test plan.

6. **P1: one `sentence_withheld` variant cannot safely express the
   promised tool fragment.** `_tool_fragment` returns three
   concrete types and the catalog permits one concrete value type
   per field; `tool_call` uses separate variants for exactly this
   distinction. Declare source-specific variants selected by the
   existing classifier, or omit the fragment for one metadata-only
   variant, and prove with a sentinel-bearing far-side tool name
   that nothing reaches logs or payloads.

   *Resolution*: accepted in full; the declaration takes
   `tool_call`'s own shape, source-specific variants selected by
   the existing classifier, with the ambiguous argument-only match
   riding the unnamed variant, and the sentinel case is in the
   test plan.

7. **P2: cancellation during fallback playback is contradictory.**
   "Never raises" would swallow a barge-in's `CancelledError`.
   Require `CancelledError` to propagate, `DeviceGone` swallowed,
   ordinary failures sanitized and swallowed, and test
   cancellation after playback has begun with `tts stop` still
   attempted exactly once.

   *Resolution*: accepted in full; the contract is restated as the
   filler runner's own (`CancelledError` propagates, `DeviceGone`
   swallowed, the rest sanitized and swallowed) and the
   mid-playback cancellation case is in the test plan. The
   "explodes mid-fallback" risk case keeps its claim, since only
   non-cancellation failures are swallowed.

8. **P2: independent filler and fallback caching does not fit the
   current generation and reload result.** `Fillers`' single clip
   mapping and its `resynthesized`/`reused`/`disabled` lists
   cannot honestly describe per-kind outcomes. Settle the cache
   structure in the plan: per-kind outcomes, generation wiring,
   reload response and OpenAPI changes, the diff surface, and the
   `config/entities.py` `NestedShape`.

   *Resolution*: accepted in full; the cache shape section now
   names `FallbackClip`, the `fallbacks` mapping, the three
   per-kind outcome names, the generation wiring, the reload
   response and OpenAPI regeneration, the diff pair and the
   entities `NestedShape`, and no longer leaves the shape to the
   implementer.

9. **P2: fallback synthesis and playback failures have no honest
   telemetry surface.** `FillerDisabled`'s meaning is latency
   masking; reusing it would be semantic drift, and a new bare log
   line contradicts "the UNTYPED set is not grown". Declare a
   fallback-specific cache-failure event and either a typed
   playback-failure event or the exact new UNTYPED
   channel/template, with sentinel-planting tests over record
   internals and both renderings.

   *Resolution*: accepted in full. `fallback_degraded` is its own
   declaration for the cache failure, the playback failure is a
   named new UNTYPED template registered in the closed set, the
   `reply_fallback` variants carry the audio-versus-display-only
   boolean, and the sentinel tests plant both the message and the
   `__cause__`.

10. **P2: the two sentence decision sites are not independently
    pinned.** Compact JSON at EOF exercises only `flush`. Require
    a newline-terminated case through `push` and an EOF-tail case
    through `flush`, or centralize guard plus emission behind one
    helper and test both callers.

    *Resolution*: accepted via the centralization arm, which
    finding 1's resolution already made the design (guard plus
    emission behind one helper in `runtime/speech.py`), and the
    test plan now drives both caller paths, a newline-terminated
    case through `push` and an unterminated tail through `flush`.

11. **P2: pin-before-reshaping coverage is incomplete.** No
    existing test asserts the filler sends no `sentence_start`,
    and the proposed failure-order test lists only control
    messages. Add the explicit filler pin first, and assert the
    full failure order: `stt`, `tts start`, the fallback
    `sentence_start`, one or more frames, then `tts stop`.

12. **P2: default-on boot synthesis creates an unbounded
    upgrade-time operation.** Startup awaits the whole cache build
    before serving, synthesis has no deadline, and an upgrading
    deployment cannot pre-stage the new field because old models
    forbid it. Define a bounded startup policy, test a TTS stream
    that never completes, and document the unavoidable
    first-upgrade synthesis with its latency and billing
    consequence.
