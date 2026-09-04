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
existing never-fails-the-boot arm and `FillerDisabled`-style
reporting for a fallback phrase that will not synthesize. The
cache result carries the fallback clip beside the filler clips per
agent; the exact shape (a field on `FillerClips` versus a sibling
mapping in `Fillers`) is the implementer's, with the constraint
that one build pass produces both and the reload diff re-runs it
exactly when either section or the voice changed. The deletion
test rejects a new `fallback.py` build module: it would restate
`build_agent_fillers` line for line against a second phrase list.

**Playing lives in the runner; the arm just asks.** `FillerRunner`
already holds the output handle, the clip view, and the paced
recipe; it gains `speak_fallback()`, which sends
`sentence_started(phrase)`, then the clip through the existing
recipe, swallowing `DeviceGone` and reporting any other failure by
class name outside the arm, filler-style, and never raising to its
caller. The failure arm calls it after the reply-failed log and
after `await self._filler.settle()` (so a sounding filler clip is
not talked over and the shared encoder is not interleaved); the
`finally`'s own `settle()` stays and is idempotent. The arm's call
cannot prevent `finish_speaking`, because `speak_fallback` does
not raise. `CancelledError` and `DeviceGone` arms are untouched.

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

**The guard is a pure predicate at the two sentence sites.** A
sentence is withheld when, stripped, it parses as a JSON object
that names an offered tool: the object's own `"name"`, or the
`"name"` inside an object under its `"function"` key (the OpenAI
wire shape models parrot), equals a published name from the
snapshot already in scope. Nothing else is withheld: JSON that
names no offered tool, JSON with no name, and prose all speak.
The predicate lives beside the speaking machinery in
`runtime/speech.py`, taking `(sentence, offered_names)`; inlining
it at the two call sites would duplicate the rule, and `text.py`
stays ignorant of tools. A stated bound, recorded in the module
docstring: the guard catches the shape actually observed (a
compact call in one sentence); a pretty-printed call is chopped by
the splitter's newline rule into fragments that parse as nothing,
and closing that fully would mean buffering heuristics with their
own failure modes. The event is what keeps the residue visible.

**Nothing replaces a withheld sentence unless nothing else spoke.**
Mid-reply, a withheld sentence is dropped and its event emitted;
the surrounding answer speaks, which is the issue's own
constraint. When the reply ends with `spoken` empty and at least
one sentence withheld, the user is about to get the exact silence
#384 exists to end, so the same fallback plays (its event carrying
the other reason). That happens at the end of `_speak_reply`, not
by raising, so the reply-failed log stays what it means.

**Two event declarations, payloads under the content rule.**

- `sentence_withheld`, session channel: the sentence's length in
  characters (never its bytes), and the tool named the way
  `tool_call` already names one (the `_tool_fragment` policy:
  builtin names are this server's vocabulary; a far side's name is
  identified without repeating it). One variant.
- `reply_fallback`, session channel: reason from a closed set with
  one variant per reason, filler-style: `reply_failed` (the
  failure arm) and `nothing_sayable` (the empty-reply case). A
  fallback skipped because the section is off or the clip is
  absent emits nothing; absence of the feature is not a decision
  taken.

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
  (`test_session_characterization.py` 477, 507) are re-cut for the
  new default and their disabled-config variants keep the old
  claim.
- **The clip is cached, not synthesized at failure time**: the TTS
  provider fake counts synthesis calls; a failure turn adds none.
  A fallback phrase that fails boot synthesis disables with the
  filler's reporting and the boot survives
  (`tests/unit/test_filler_cache.py`, `test_session_filler.py`
  shapes). Reload staleness: editing the phrase resynthesizes,
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
  session-level cases in `test_session_tools.py`): the predicate
  matrix (compact call naming an offered tool withheld, the
  `function`-wrapped shape withheld, JSON naming no offered tool
  spoken, JSON with no name spoken, prose about JSON spoken); a
  scripted reply mixing a leaked call with a real sentence speaks
  only the real one, emits `sentence_withheld`, and the record
  keeps the model's text per the content rule already governing
  turns; a reply that is only a leaked call speaks the fallback
  with `nothing_sayable`.
- **Events**: drivers and `CARRIED` rows for both declarations;
  `events.md` and the README index regenerate through their
  generators; the UNTYPED set is not grown (no new bare log lines
  on scoped channels; the runner's existing failure line pattern
  is reused only if its message is identical, otherwise the new
  line registers).
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
  never raises by contract and the arm calls nothing else; the
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
