# Hang up on a realtime session that stopped talking

## Problem

Issue #20: a realtime device opens its audio channel on the wake word
and nothing closes it when the conversation is over.

On the device side the only closers are a short PWR press while
listening, losing the network, and powering off. There is no idle
timeout in the firmware. On the server side the only bound was
`server.limits.max_session_s`, an hour in a typical deployment. So a
user who simply walks away left the mic streaming for the rest of that
hour, which costs four separate things:

- **Privacy.** A realtime session streams continuously, silence
  included, so room audio keeps reaching the server long after the last
  utterance.
- **Capacity.** An abandoned session holds one of `max_sessions` for the
  full cap. At the session counts these deployments run, a couple of
  forgotten conversations is most of the capacity.
- **Battery.** `Application::CanEnterSleepMode()` returns false while the
  audio channel is open, and `PowerSaveTimer::PowerSaveCheck()` resets
  its tick count whenever that is false, so a board configured to shut
  down after five idle minutes never does while a session is open.
- **Wasted work.** Opus decode and VAD run on every frame of the
  silence.

## Changes

- New `server.limits.idle_timeout_s`, default 120 seconds. Validated
  `gt=0`, like `max_session_s`, and with no off switch for the same
  reason: a deployment that wants none sets it near the life cap.
- A per-session watchdog task, started once the handshake is done and
  cancelled in the session's `finally`. It closes with a 1000 normal
  closure and the reason `idle timeout`, through the same
  `request_shutdown` the life cap and the shutdown drain use, so a reply
  already speaking finishes its sentence first.
- The clock is written at `_finish_utterance` and at `_reply`'s
  `finally`, so "the end of the last utterance or the end of the last
  reply, whichever is later" falls out of both writing the current time
  rather than needing a comparison. It is written on an accepted `listen
  start` too, which a review found: while a session is not realtime the
  deadline is pushed forward each round, and one that turns realtime
  part-way through a round would otherwise inherit the remainder and be
  hung up on seconds after the user started talking.
- A distinct `session_idle` log event carrying `idle_s` and
  `duration_s`, so an operator can tell an abandoned conversation from
  one that ran out of its hour.
- `samtal-esp32/README.md` reworked as the issue's comment asked: the
  idle timeout goes to the front of the list of what closes a channel,
  since it is the one users will actually meet, and the sentence
  pointing at this issue is gone. The battery paragraph now says what
  the timeout changes, and the claim that the five-minute shutdown is
  "the main protection against an abandoned open session" is dropped,
  because it no longer is.

Three deliberate decisions, each of which could reasonably have gone the
other way:

**Arriving audio is not activity.** This is the whole trick. A realtime
session streams continuously, so a timer reset by incoming frames would
never fire, which is the bug rather than the fix. What counts is
conversation.

**A reply still streaming counts as activity.** Not because it would
otherwise be cut off; `request_shutdown` waits politely for a reply to
finish speaking, so a single-turn test cannot see the difference. It is
what happens next. A timer that came due during a reply has already
decided to hang up, so without the guard the socket closes the instant
the reply ends and the user gets no window at all to answer what they
just heard.

**Realtime only,** as the issue specifies. An auto-mode device stops
listening after each reply and re-arms per turn, so it is not streaming
a room to anybody, and `max_session_s` remains its bound. Worth noting
that the privacy argument is the only one of the four above that is
specific to realtime: an abandoned auto-mode session still holds a slot
and still keeps the board awake. Whether the timeout should widen to
cover those is a separate call and is left alone here.

## Key parameters

| Key | Default | Meaning |
|---|---|---|
| `server.limits.idle_timeout_s` | `120.0` | Seconds a realtime session may go without conversing before the server closes it. Counted from the end of the last utterance or the end of the last reply, whichever is later. |

Two minutes is a judgement, not a measurement: long enough to think,
read something out, or answer the door, and short enough that walking
away does not leave a mic streaming for the rest of the hour. The
endpointer's own `max_utterance_ms` closes an utterance every few
seconds, so even a monologue cannot starve the timer.

## Verification

`uv run pytest tests/unit -q`: 596 passed, 2 skipped.
`uv run pytest tests/integration -q`: 27 passed. `ruff check` clean.

Five of the new unit tests were each run against a control with the
relevant piece disabled, so none of them passes for the wrong reason:

| Test | Control | Result without the change |
|---|---|---|
| a realtime session that stops talking is hung up on | watchdog not started | fails: closed by the life cap, reason `session time limit reached` |
| the idle close is logged as its own event | watchdog not started | fails: no `session_idle` record |
| the timeout leaves a session that never went realtime alone | realtime scoping removed | fails: socket closed under a manual-mode turn |
| a reply still speaking is not an idle session | `_replying()` guard removed | fails: socket closed the moment the first reply ended, so the second turn never happened |
| going realtime late gets a full window | `listen start` mark removed | fails: socket closed in the gap between asking to listen and speaking |

Two of those first passed against their own controls and had to be
rewritten. The reply one needed a second turn, for the reason in the
second decision above. The late-realtime one needed a pause between
asking to listen and speaking: an utterance ending pushes the deadline
out by itself, so speaking straight away hid the very gap the test
exists for.

One thing no test covers, stated plainly rather than left to be
discovered: the activity mark in `_finish_utterance` is presently
redundant. Every path from there either starts a reply or leaves one
already running, and a reply marks again when it ends, so the utterance
mark is always superseded and removing it keeps the suite green. It
stays because the rule the timeout is specified by names both ends, and
because the day an utterance stops implying a reply is not a day anyone
will remember it. This is noted in the code at the line itself.

The life cap in the new tests is set to ten seconds rather than an hour,
so that a regression fails the lane in seconds instead of hanging it:
`wait_for_close` blocks until something closes the socket, and the close
reason is what tells the two bounds apart.

Not verified here, and left for the board:

- A real device reconnecting on the next wake word after an idle close.
  The firmware path is the same one the life cap already uses, and the
  close code is identical, so this is expected rather than hoped for,
  but expected is not observed.
- Whether 120 s is the right default in use. It is a judgement about how
  long a person pauses mid-conversation, and only sitting with the
  device answers it.

## Files modified

- `samtal-server/samtal_server/config/models.py`
- `samtal-server/samtal_server/session.py`
- `samtal-server/tests/unit/test_session_limits.py`
- `samtal-server/tests/unit/test_config.py`
- `samtal-server/config.example.yaml`
- `samtal-server/README.md`
- `samtal-esp32/README.md`
- `CHANGELOG.md`
