# Take the transcript out of the echo guard's recovered sentence

## Goal

Implement issue #165: the echo-guard retry's success branch renders
what the user said into the retained log
(`samtal_server/providers/openai_asr.py:369`, the
`asr_prompt_echo` `recovered` sentence interpolating the recovered
transcript). The content-and-telemetry ADR, as amended 2026-08-17,
bans conversation-derived text on the events without exception.
Remove the text from the sentence, update its one pin as a
deliberate breaking surface narrowing, and prove the absence with a
sentinel. Prerequisite to #155, whose registry taxonomy cannot
admit a conversation-text argument.

The companion implementation doc,
[`2026-08-17-asr-prompt-echo-transcript-implementation.md`](2026-08-17-asr-prompt-echo-transcript-implementation.md),
records what was actually done and any deviations; none are
expected.

## The issue's decisions, restated

1. The `recovered` sentence stops rendering the transcript; the
   diagnostics stay (`duration_s`, `retry_ms`, the `_echo_fields`
   field set). The transcript still reaches the session and, when
   recording is on, the conversation store, where content belongs.
2. The pin update is confined to the recovered-branch pin in
   `tests/unit/test_server_event_pins.py`; every other pin in both
   suites is byte-unchanged.
3. A sentinel test plants a credential-shaped recovered transcript
   and asserts absence from every retained surface.
4. CHANGELOG marks the breaking surface change.

## Decisions this plan makes

- **The new sentence**: `"openai asr: the retry recovered %.2f s
  of audio the echo guard would have discarded"`, one argument
  (`duration_s`), INFO as today. It says what happened and how
  much audio it concerned, which with `retry_ms` is everything the
  event's own fields already carry; naming that a transcript
  exists without quoting it adds no diagnostic the fields lack.
  The template drops from two arguments to one, which is exactly
  the narrowing.
- **The pin edit is one dictionary**: `template`, `args`, and
  `sentence` of `test_asr_prompt_echo_recovered` change; its
  `fields`, `level`, and `logger` do not. The suite's other pins
  are proven byte-unchanged by `git diff` inspection: the diff to
  the file touches only that test.
- **The sentinel test** lives beside the provider's own tests
  (`tests/unit/test_providers_openai_asr.py`, which already owns
  the echo-guard scenarios): the fake answers the retry with
  `sk-test-...never-a-real-credential`-shaped text, and the test
  covers both retained surfaces the emitter serves, because a
  clean `LogRecord` does not prove a clean consumer (the server
  pin suite says exactly this about its own sentinels): a server
  tap is attached and the sentinel asserted absent from
  `Emission.message`, `Emission.args`, and `Emission.payload`,
  and every log record's raw and rendered message, args, extracted
  fields, and both format renderings are checked the same way,
  with the test also asserting the tap and the recovered event
  were actually observed (a sentinel test that saw no event proves
  nothing). The transcription result still carries the text: the
  session is supposed to hear it; the surfaces are not.
- **One milestone, one PR**, branch `fix/asr-prompt-echo-transcript`.
  This is a surface narrowing, so the changelog gets a
  `**Breaking:**` bullet under 2026-08-17's Changed, modeled on
  the 2026-08-16 narrowing entries, and the SAME change updates
  the existing ADR-amendment bullet that says the violation "is
  scheduled for removal": once this lands that sentence is stale,
  so it is rewritten to say the narrowing landed here.

## Files touched

`samtal_server/providers/openai_asr.py` (the one sentence),
`tests/unit/test_server_event_pins.py` (the one pin),
`tests/unit/test_providers_openai_asr.py` (the sentinel test),
`CHANGELOG.md`, this plan and its implementation doc.

## Verification

From `samtal-server/`: `uv run ruff check .`,
`uv run pytest tests/unit -q`, `uv run pytest tests/integration -q`;
`git diff` on `test_server_event_pins.py` inspected to touch only
the recovered pin; `test_event_surface_pins.py` byte-unchanged;
collected count rises by exactly the sentinel test;
`PYTHONDONTWRITEBYTECODE=1` outside pytest.

## Risks

The sentence is pinned in exactly one place (verified by grepping
both pin suites for `asr_prompt_echo` and for the template text),
so the blast radius is the one test; the integration lane does not
assert this sentence. If a second consumer of the wording turns up,
it is updated in the same commit and recorded in the
implementation doc.

## Milestones

- [x] **[The sentence narrows](2026-08-17-asr-prompt-echo-transcript-implementation.md#milestone-1-the-sentence-narrows)**
      (PR TBD): template down to one argument, pin updated, sentinel
      proof in the provider suite, CHANGELOG entry; both lanes green.

## Plan review round

External review of commit da3a93d by codex 0.147.0 (model
gpt-5.6-sol), 2026-08-17. Two findings, verdict ready after the
amendments:

1. **P2: the sentinel omits the event-consumer surface.** Server
   taps receive a distinct `Emission` before logging, and the pin
   suite itself says a clean `LogRecord` does not prove a clean
   consumer.

   *Resolution*: accepted. The sentinel now attaches a server tap
   and asserts absence from `Emission.message`, `args`, and
   `payload` as well as every log surface, and asserts the tap and
   the recovered event were actually observed. Amended in the
   sentinel decision.

2. **P2: the changelog plan leaves a stale "scheduled for
   removal" claim.** The 2026-08-17 ADR bullet describes this
   narrowing as future work, which this change makes false.

   *Resolution*: accepted. The same change rewrites that bullet to
   say the narrowing landed here, beside the new `**Breaking:**`
   #165 bullet. Amended in the milestone decision.
