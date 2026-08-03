# Realtime multi-turn and barge-in: implementation plan

This file details the fix for issue
[#10](https://github.com/rafacm/samtal/issues/10), a realtime-mode
session going deaf after its first utterance. It was agreed in
conversation on 2026-08-03 and is written so a fresh session can
implement it from the repository alone. This work sits outside the v1
plan, so implementation notes go to this plan's own companion doc,
[2026-08-03-realtime-multi-turn-and-barge-in-implementation.md](2026-08-03-realtime-multi-turn-and-barge-in-implementation.md),
appended in the same change that ticks the checklist below.

## Checklist

- [ ] Realtime multi-turn and barge-in: one PR delivering the fix, its
  tests, and the documentation updates

## Context

The firmware sends `listen start` once (mode `realtime`) and streams
mic audio continuously, including during reply playback; its hardware
echo cancellation keeps the mic stream clean. But `self.listening` is
only set by `listen start`, `_finish_utterance()` clears it, and
`_handle_audio` drops all frames while it is False, so the session
serves exactly one exchange. Auto mode works only because the firmware
re-sends `listen start` after each `tts stop`. The firmware picks
realtime exactly when echo cancellation is on, so any AEC-capable
board (the project's primary hardware) hits this.

Agreed scope: the full fix, barge-in. In realtime mode the session
keeps listening during playback; an utterance that endpoints mid-reply
cancels the reply in flight and is answered. A config fallback covers
boards whose AEC leaks echo (for example a single-mic board such as
the Waveshare ESP32-S3-ePaper-1.54), where the endpointer could
self-trigger on the assistant's own voice: with barge-in off, frames
arriving during playback are dropped but multi-turn still works.

All paths below are under `samtal-server/` unless noted. Line numbers
refer to `main` at ba9008e.

## Design

### State: realtime sessions never stop listening

- `samtal_server/session.py` `__init__` (near line 156): add
  `self._listen_mode: str | None = None` and a property `_realtime`
  returning `self._listen_mode == "realtime"`.
- `_handle_text`, `ListenMessage(state="start", mode=mode)` branch
  (line 451): store `self._listen_mode = mode` before
  `self.listening = True`. Upgrade the "listening (%s mode)" log from
  debug to info so the mode is visible at the default log level (the
  issue's diagnosis needed DEBUG to see it).
- `_finish_utterance` (line 486): change the unconditional
  `self.listening = False` to `if not self._realtime:
  self.listening = False`. In realtime there is nothing to re-arm,
  ever. This also fixes abort-in-realtime for free: the
  `AbortMessage` branch (461-466) never touched `listening` and now
  does not need to.
- `listen stop` (455-458) keeps clearing `listening` regardless of
  mode (an explicit stop is explicit). Do not clear `_listen_mode`;
  the next `listen start` overwrites it.

### Barge-in: make the audio path async, cancel via existing machinery

- `_handle_audio` (424) becomes `async def`; its tail becomes
  `if self._endpointer.feed(pcm): await self._finish_utterance()`.
  The call site in `_serve` (420) gains `await`.
- `_finish_utterance` (480) becomes `async def`; the other caller
  (the `listen stop` branch, 458) gains `await`. New body: the
  reply-in-flight branch checks `self.config.server.barge_in`:
  - flag off: keep the current warning ("dropping an utterance, a
    reply is already streaming") and return;
  - flag on: log an info line with `extra=self._event("barge_in")`,
    then `await self._cancel_reply()` and fall through to
    `create_task(self._reply(pcm))`.
- Gate on the flag, not the mode: in auto mode the branch is
  unreachable (`listening` is False during replies); in manual mode
  reaching it takes a deliberate `listen start` plus `stop` mid-reply,
  which deserves the same interruption.
- Ordering is already guaranteed: `_cancel_reply` (507) cancels and
  awaits the old task, whose `finally` sends the old reply's
  `tts stop` (559-560), so the new reply's `tts start` always follows
  it. The same path is proven by
  `test_abort_during_a_streaming_reply_does_not_eat_the_next_utterance`
  (tests/unit/test_session.py:310).
- Half-spoken turns: `_reply`'s `finally` appends only sentences
  actually spoken; history stays truthful under barge-in. No change.
- Update `_finish_utterance`'s docstring and the module docstring
  paragraph (lines 24-29) that currently documents the deferral:
  realtime keeps listening through playback, and an utterance that
  endpoints mid-reply cancels the reply (barge-in) unless
  `server.barge_in` is off.

### Fallback flag: `server.barge_in`, global, default true

- `samtal_server/config/models.py`, `ServerConfig` (102): add a flat
  field `barge_in: bool = True` after `limits` (136), commented like
  `drain_s`: default on because the firmware only presents realtime
  when its echo cancellation is on; turn off for a board whose AEC
  leaks the speaker into the mic (a single-mic board), where the
  device still holds multi-turn conversations but frames arriving
  while a reply plays are dropped instead of interrupting it.
- Not per-device: `devices` is `dict[str, list[str]]` (MAC to agent
  names); adding per-device properties is a schema migration, out of
  scope for a bug fix. The global flag can later become the fallback
  for a per-device override.
- Drop point when off: in `_handle_audio`, right after the
  `not self.listening or self._endpointer is None` check (425),
  return early when `self._reply_task` is alive and
  `not self.config.server.barge_in`; before `framing.unwrap`, so
  dropped frames also skip the Opus decode. No re-arm code is needed
  anywhere: `listening` stays True in realtime and the guard opens
  when the reply task finishes. Do not add an endpointer reset at the
  end of `_reply` (it would clobber an utterance already in flight
  near the reply's end).
- `config.example.yaml`: commented `# barge_in: true` block under
  `server:` between `limits:` and `# drain_s:`, with the leaky-AEC
  explanation (the config schema change updates the example in the
  same change).

### Bounded utterance buffer (a consequence of always-listening)

With realtime always-listening, `self._utterance.extend(pcm)` (439)
grows without bound during silence (about 115 MB per session at the
3600 s cap). Add near `PIPELINE_SAMPLE_RATE` (79):

```python
UTTERANCE_TAIL_S = 30
UTTERANCE_TAIL_BYTES = UTTERANCE_TAIL_S * PIPELINE_SAMPLE_RATE * 2
```

with a comment noting this sits comfortably above the endpointer's
`max_utterance_ms` cap of 10 s, so only pre-speech silence is ever
trimmed. In `_handle_audio` after the extend:
`del self._utterance[: len(self._utterance) - UTTERANCE_TAIL_BYTES]`
when over.

### Non-changes, verified during planning

- `tts stop` on cancellation: already sent by `_reply`'s `finally`;
  the abort path relies on it today.
- `max_utterance_ms` (10 s) bounds over-talk; an over-long barge
  simply endpoints and interrupts.
- `request_shutdown` (294): a barge-in during a drain cancels the
  snapshot task early and the new reply dies on the closed socket via
  already-suppressed exceptions. Harmless; note it in the
  implementation doc.

## Tests

In `tests/unit/test_session.py` (TestClient plus mock providers;
reuse `connect`, `shake_hands`, `speech_pcm`, `send_pcm`,
`collect_reply`, `heard_ms`; one `OpusEncoder` per session as in
`test_trailing_silence_ends_the_utterance_without_listen_stop` at
:250). Add an `endpoint_silence(websocket, encoder)` helper sending
roughly 700 ms or more of silence.

1. `test_realtime_mode_serves_a_second_utterance_without_listen_start`:
   the #10 regression. One `listen start` mode realtime; speech
   600 ms plus silence; collect reply; then speech 240 ms plus
   silence with no further `listen start`; collect a second reply.
   Assert heard_ms ranges [540, 660] then [180, 300]. Hangs on
   current `main`.
2. `test_realtime_barge_in_cancels_the_reply_in_flight`: a long mock
   reply (about 8 s: long `llm_reply`, mock TTS speaks 40 ms per
   char); barge with speech 240 ms plus silence while it streams, no
   abort or listen messages. Assert the first reply's audio is cut
   well short (under half the full tone) and the second reply hears
   [180, 300].
3. `test_realtime_without_barge_in_drops_frames_during_playback_but_hears_after`:
   the same long reply, config with `server.barge_in = False`
   (extend `config_with_agent` with an optional server kwarg or
   build the Config inline). The barge attempt is ignored: the first
   reply completes in full (audio_ms within one frame of expected);
   a post-reply utterance is answered with no re-sent `listen start`.
4. `test_abort_in_realtime_mode_keeps_the_session_listening`:
   realtime; abort mid-reply; then speech with no `listen start`;
   the second reply is heard. Deadlocks on current `main`.
5. `test_auto_mode_still_requires_a_new_listen_start_after_the_reply`:
   guards that auto and manual semantics are unchanged. After a full
   auto-mode turn, frames without a new `listen start` are dropped;
   after a fresh `listen start` the next utterance is heard.
6. `test_the_utterance_buffer_keeps_only_a_bounded_tail`: a direct
   async test (pytest asyncio_mode is auto), building a Session as
   `test_a_session_refuses_an_agent_its_device_is_not_bound_to`
   (:425) does; monkeypatch `UTTERANCE_TAIL_BYTES` down to about 2 s,
   feed about 4 s of wrapped Opus silence through `_handle_audio`,
   assert the buffer stays within the cap (plus one frame of slack).
7. `tests/unit/test_protocol_messages.py`: add a `mode == "realtime"`
   round-trip assertion only if not already covered.
8. Integration, `tests/integration/test_device_simulator.py`: new
   `test_a_second_utterance_is_answered_without_reconnecting`. The
   xiaozhi-sdk's default mode is `realtime` (core.py:47) and it never
   re-sends `listen start` outside auto mode, so after the first
   reply, clear the event, send the tone plus
   `send_silence_audio(1.2)` again, and `asyncio.wait_for` the second
   `tts stop`; assert two `stt` events. Keep the existing M4 test
   untouched.
9. The whole existing suite must pass, especially
   `test_abort_during_a_streaming_reply_does_not_eat_the_next_utterance`
   and both `test_drain.py` files.

## Documentation

- The companion implementation doc (linked above), appended in the
  same change that ticks the checklist: deviations from this plan,
  discoveries, and the shutdown-drain note from the non-changes
  section. A milestone with no deviations says so explicitly.
- `CHANGELOG.md`: a `### Fixed` entry under the `## 2026-08-03`
  header (one entry covering the fix and the flag it adds).
- `session.py` module docstring rewrite and `config.example.yaml`
  update, both described above.

## Process

- Branch `fix/realtime-deafness` off up-to-date `main`; rebase-merge
  repo. Small commits, imperative titles of roughly 50 characters
  with why-bodies:
  1. `Make the session audio path async` (mechanical, no behavior
     change)
  2. `Keep realtime sessions listening across turns` (mode field,
     conditional clear, buffer cap, docstrings; tests 1, 4, 5, 6)
  3. `Cancel the reply in flight when the user barges in` (barge-in
     branch; test 2)
  4. `Add server.barge_in flag for boards with leaky AEC` (config
     field, drop guard, config.example.yaml; test 3)
  5. `Prove a second turn end to end with the simulator` (test 8,
     plus test 7 if needed)
  6. `Document realtime multi-turn and barge-in` (implementation
     doc, CHANGELOG, tick the checklist)
- PR titled along the lines of `Honour realtime listening:
  multi-turn and barge-in`, body closing with `Fixes #10`, with a
  Verification section as a task list:
  - [ ] `uv run ruff check .`
  - [ ] `uv run pytest tests/unit -q`
  - [ ] `uv run pytest tests/integration -q`
  - [ ] Hardware checkpoint: Waveshare ESP32-S3-Touch-LCD-1.54
    (firmware 2.4.0, presents realtime) holds a multi-turn
    conversation; barge-in interrupts a reply. Left unchecked with a
    note: needs the board, not verifiable from CI.
  - [ ] Hardware checkpoint: a single-mic board with
    `barge_in: false` holds multi-turn without self-interruption.
    Left unchecked, same note.

## Verification

From `samtal-server/`: `uv run ruff check .`,
`uv run pytest tests/unit -q`, `uv run pytest tests/integration -q`.
The two hardware checkpoints run later at the desk (serial procedure
in `docs/xiaozhi-notes.md`); the PR's unchecked boxes carry them.

## Open risks

- Echo tail at reply boundaries: with barge-in on, a leaky AEC may
  endpoint a ghost utterance right after `tts stop`; ASR transcribes
  nothing and the session sends an empty `tts start`/`stop` pair
  (already the graceful path at session.py:539-541). Ghost
  heard-less replies at the checkpoint are the signal that a board
  wants `barge_in: false`.
- Timing-sensitive tests 2 and 3 rely on the roughly 8 s mock reply
  outlasting sub-second barge sends; bounds are loose (half the
  tone). Lengthen the reply text if flaky.
- Barge-in mid-tool-round: cancellation propagates through
  `_run_one`'s CancelledError re-raise (733-734); no code change
  expected, not separately tested. Acceptable for a bug fix.
