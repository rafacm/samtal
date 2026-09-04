# Implementation notes: a failed reply says so

Companion to
[`2026-09-04-reply-failure-fallback.md`](2026-09-04-reply-failure-fallback.md),
one section per milestone, appended in the change that ticks the
milestone there.

## M1: the failure arm gets a voice

Built as the plan describes, amendments included. The deviations below
are the places where the plan left a choice open or where the code
disagreed with the plan's reading of it; everything not listed here is
the plan's own shape.

### Deviations and decisions

**`nothing_sayable` is deferred to M2, and the catalog permits it.** The
plan's M1 line makes this conditional: declare the second variant now
only if the catalog forbids adding one later. It does not. A
`Declaration` holds a tuple of variant classes and `declare()` is an
ordinary call at import, so M2 adds `NothingSayable` to
`REPLY_FALLBACK`'s tuple and a member to `FallbackReason` with no
migration of any kind. The enumeration is declared with one member and a
note saying the second is coming, which is what keeps the `reason` field
honest: a closed set with one token today reads as "this is the reason",
not as "there is only one".

**No `views.py` change was needed for `phrase`.** The plan asks for the
filler's `views.py` treatment. That treatment exists because
`FillerConfig.phrases` defaults to an empty list, and `_absent` hides a
default that means absence (null, an empty list, an empty mapping).
`FallbackConfig.phrase` defaults to a real sentence, so it is shown at
whatever it holds by the rule that was already there. Adding it to the
`shown` tuple would have been a line with no reader.

**The diff gained a reported half, not just the predicate pair.** The
plan's finding 8 names `_FALLBACK_FIELDS` and `same_fallback`. A
predicate nothing reads is dead code, and the reload-staleness claim the
Tests section asks for (`toggling the filler alone does not touch the
fallback clip, and the reverse`) is only observable to an operator if
the comparison reports it. So `AgentsDiff` grew a fourth sub-section,
`FallbackDiff`, beside `filler`. This cost nothing beyond the model: the
CLI renders diff and apply sections by walking the response models, so
the new section and the three new `FillersReload` lists print without a
line of rendering code.

**`FillersReload` grew three fields rather than gaining a sibling
model.** The plan says "the reload result and its response model grow
the per-kind outcome fields", and `Fillers` names them
`fallback_resynthesized`, `fallback_reused`, `fallback_degraded`. One
model with six lists keeps the two structures that have to agree down to
one; a second model would have been a second shape for a caller to
learn. The three are required, like their siblings, and the two suites
that construct the model by hand were updated.

**`build_agent_fillers` keeps its name.** It builds both kinds now, and
the name says one. Renaming would have moved the baseline driver
identity (`vinga_server.filler:build_agent_fillers #1`), every call site
and every test import, for a word; the module docstring and the
function's own now say what it builds. `Fillers` was already the name
for "one world's clips" rather than "one world's filled pauses", so the
type names did not have to move either.

**No new per-reply state on the runtime.** The plan asks for the state
inventory to be updated "for any new per-reply state". M1 adds none: the
failure arm reads the world's cache through the runner it already holds.
The class docstring says so explicitly, beside the inventory, because
"nothing was added here" is the fact a reader of that list needs. The
reply-wide facts finding 5 asks for belong to M2's empty-reply check.

**The UNTYPED playback line is driven by the `reply_fallback` driver.**
The closed set in `test_event_baseline.py` is asserted in both
directions against what the drivers actually produced, so a row added
without a producer fails the lane. One driver therefore produces both:
its world caches a phrase at a sample rate nothing can resample from, so
the typed record goes out (it is emitted before any audio work, the way
`filler_played` is) and the resample that follows raises into the arm
that writes the untyped line. A `DeviceGone` there would have produced
neither, since that is swallowed by contract. The driver's docstring
says all of this.

**`fallback_degraded` needed a driver of its own, sharing a body.** One
driver names one event, and a voice that refuses degrades both kinds, so
`build_agent_fillers #2` drives the same voiceless world as `#1` and
keeps the other record. The driver count is now 89.

**One existing sentinel test changed shape.** A voice that refuses is
now caught in two arms of one build, so
`test_a_filler_that_will_not_synthesize_refuses_carrying_nothing` sees
two refusals rather than one. Both have to carry nothing, so `reported`
and `carries_nothing` gained a count and the test asserts two. Reading
one of them would have been reading half the evidence.

**The failure-arm log matcher was tightened.** `reply_failure` in
`test_session_reply_failures.py` matched the substring `reply failed`,
which the new event's own sentence ("the reply failed, so this agent's
fallback phrase went out") also contains. It now matches
`": reply failed: "`, the arm's own punctuation.

### The two pins that did not move

Both "ends quietly" characterization cases
(`test_a_reply_ends_quietly_when_the_send_path_raises` and
`test_a_filler_ends_quietly_when_the_send_path_raises`) are untouched,
per finding 3: what they drive is a socket disappearance translated to
`DeviceGone`, which must stay silent whatever the configuration says.
The new behaviors got their own general-exception case beside them,
parameterized over the switch.

`test_one_turn_has_the_control_message_order_the_firmware_expects` is
untouched too, and the failure turn's order is pinned beside it.

Per finding 11, the filler's no-`sentence_start` pin landed green in its
own commit (`f1f47110`) before any of the fallback work, so the
deliberate difference is a diff against a pin.

### Shape notes

`OrderedSocket` moved into `tests/support/sockets.py` rather than being
written twice. It is the module's third reading of a turn and the note
there says why the other two cannot answer these questions:
`RecordingSocket` counts frames rather than placing them, and `spoken`
reads the text messages with the frames already dropped, so neither can
say a sentence was announced before or after a clip.

`config_with_agent` in `tests/support/configs.py` gained an `agent`
argument: the four stages are always named there, so a caller adds the
section it is about rather than restating the world around it.

### Bounds

The per-phrase synthesis deadline is `FALLBACK_SYNTHESIS_TIMEOUT_S` in
`filler.py`, ten seconds, with the reasoning beside it: generous for one
short sentence through a cloud voice on a cold connection, bounded
enough that a hung provider delays a boot by seconds per agent. The
filler's own build stays unbounded, and the constant's note says why
(opt-in and pre-existing; bounding it here would be an unasked-for
behavior change riding along).

### Verification

From `vinga-server/`:

- `uv run ruff check .` clean.
- `uv run pytest tests/unit -q -n auto --dist loadfile`: 5370 passed,
  19 skipped.
- `uv run pytest tests/integration -q`: see the PR's verification list.
- `python3 scripts/check_doc_links.py .` from the repository root: 184
  files, 0 failures.

Generated references were regenerated through their generators:
`config reference`, `config openapi`, `config cli-reference`, `events
reference`, and the command-spellings manifest through
`uv run python -m tests.unit.test_command_spellings`.
