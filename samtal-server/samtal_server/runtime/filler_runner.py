"""This session's latency mask: one timer per turn, and the clip it
plays when the reply is late.

The sibling of [`filler.py`](../filler.py), and the two are told apart
by when they run: that module builds the clips once at boot,
synthesizing each agent's phrases in its own voice and caching them as
PCM, while this one is the per-session runner that decides whether a
cached clip is played at all. Nothing here synthesizes anything, and
nothing there knows a session exists.

The silence between the end of an utterance and the first audio of a
reply is where a voice assistant feels dead (#48), and a filled pause is
how a human holds that gap. The timer is armed at the transcription; if
the reply has not started speaking by the time it expires, the active
agent's clip goes out through the device's normal paced path and the
reply's first real sentence queues behind its tail.

The mask yields to whoever holds the floor, which is read through
`TurnView` and nothing else: two questions and no answers. A user still
speaking, or a barge-in being confirmed, stands the timer down, because
a mask that talks over the user is worse than no mask at all.
"""

import asyncio
import contextlib
from collections.abc import Sequence
from typing import Protocol

from samtal_server.audio.resample import Resampler
from samtal_server.device.boundary import DeviceGone, DeviceOutput
from samtal_server.events import SessionEvents, logger
from samtal_server.filler import FillerClips


class TurnView(Protocol):
    """What the fire-time stand-down asks of whoever is deciding the
    floor: how much of what was fed the endpointer classified as speech,
    and whether the outgoing frames are being held for a barge-in
    confirmation.

    Two reads and nothing else. The mask yields to the user, so nothing
    about the floor is this runner's to change, and the read-only shape
    is what keeps the question out of the device boundary, where only
    the filler would ever have asked it."""

    def speech_ms(self) -> int: ...

    @property
    def output_paused(self) -> bool: ...


class FillerRunner:
    """One turn's latency mask at a time, for the life of one connection.

    `fillers` is the boot-time clip cache keyed by agent, held by
    reference because the boot fills it once synthesis has run, so a
    runner built before the clips exist still sees them; empty means no
    agent masks its latency. `agents` is what the device is bound to,
    which is what the arming rule asks rather than only the agent
    talking now."""

    def __init__(
        self,
        events: SessionEvents,
        output: DeviceOutput,
        fillers: dict[str, FillerClips],
        agents: Sequence[str],
        turn: TurnView,
    ) -> None:
        self._events = events
        self.session_id = events.session_id
        self._output = output
        self._fillers = fillers
        self._agents = list(agents)
        self._turn = turn
        # One timer per turn, armed at the transcription:
        # `_filler_sounding` flips the moment it fires, which is what
        # lets the real reply's audio queue behind the clip's tail rather
        # than interleave with it, and the fire counter is what rotates
        # the phrase variants.
        self._filler_task: asyncio.Task[None] | None = None
        self._filler_sounding = False
        self._filler_fires = 0

    @property
    def armed(self) -> bool:
        """Whether this turn's timer is still around, unfired or already
        sounding. False once the turn has settled, which is what "no
        filler left over" means."""
        return self._filler_task is not None

    @property
    def sounding(self) -> bool:
        """Whether a clip is playing right now, which is what makes the
        reply's own audio queue behind its tail instead of cancelling
        it."""
        return self._filler_sounding

    @property
    def fires(self) -> int:
        """How many clips this session has played, which is also what
        rotates the phrase variants."""
        return self._filler_fires

    def arm(self) -> None:
        """Start this turn's latency mask, when any agent this session
        could become has one: a timer from the transcription that plays
        a cached filler clip if the reply's first audio has not started
        in time.

        Any bound agent, not just the active one, because a handover
        mid-turn can move the conversation to an agent with fillers
        before the reply first speaks: armed only for the starting
        agent, a filler-less receptionist handing over to a masked
        specialist would leave the specialist's slow greeting unmasked
        even though the fire-time lookup already resolves the active
        agent. The delay is the active agent's own where it has one,
        and the earliest configured among the bound agents otherwise;
        at fire time an active agent with no clip quietly plays
        nothing. A session bound only to filler-less agents still
        skips the timer entirely.

        Armed once per turn and never re-armed, so a first-token
        watchdog retry does not earn a second filler: the filler is the
        soft early threshold, the watchdog the hard late one, and a
        stalled round hears one "let me see" before the watchdog gives
        the round up."""
        reachable = [self._fillers[name] for name in self._agents if name in self._fillers]
        if not reachable:
            return
        own = self._fillers.get(self._events.agent or "")
        delay_ms = own.delay_ms if own is not None else min(c.delay_ms for c in reachable)
        self._filler_sounding = False
        armed_at = asyncio.get_running_loop().time()
        self._filler_task = asyncio.create_task(self._fire(delay_ms / 1000, armed_at))

    async def _fire(self, delay_s: float, armed_at: float) -> None:
        """Wait out the delay, then mask the silence, unless the reply's
        first audio arrived first.

        The clip is chosen from the agent active at fire time, so a
        handover already made is spoken in the voice now talking, and
        an active agent with no clips of its own plays nothing,
        quietly: no event, no state, the turn proceeds unmasked. It
        goes out through the normal paced path: `_begin_speaking` moves
        the device into its speaking state (once per reply, so the real
        sentence that follows sends no second one), the frames land on
        capture channel 1, and `speaking_started` fires on the clip's
        first frame and counts as the turn's. No `sentence_start` is
        sent: the filler is a noise that buys time, not a sentence of
        the reply, and it stays out of the transcript everywhere.

        A device that went away mid-clip ends the clip, not the
        session; anything else unexpected is logged and swallowed,
        because a broken mask must never break the reply it masks.

        The mask yields to the user. A fire-time check skips the clip
        when the endpointer holds unresolved speech (the user is
        talking, or just trailed off into silence the endpointer has
        not yet resolved) and when a barge-in confirmation has the
        outgoing frames paused. Both mean the silence the timer set
        out to mask is not silence: the turn it would mask belongs to
        a premature endpoint, the reply in flight is about to be
        cancelled, and a clip played now talks over the user's own
        continuation. Field round 2 measured exactly this: 4 of 20
        fires landed 1.4 to 1.8 s into speech already underway, all
        in dictation-style turns. Skipped, not deferred: one filler
        per turn stays the rule, and the cancelled reply's successor
        arms its own timer."""
        await asyncio.sleep(delay_s)
        if self._output.speaking_started_at() is not None:
            return
        speech_ms = self._turn.speech_ms()
        if speech_ms > 0:
            self._events.info(
                "session %s: filler skipped, the user is speaking (%d ms heard)",
                self.session_id,
                speech_ms,
                event="filler_skipped",
                agent=self._events.agent,
                reason="user_speaking",
                speech_ms=speech_ms,
            )
            return
        if self._turn.output_paused:
            self._events.info(
                "session %s: filler skipped, a barge-in is being confirmed",
                self.session_id,
                event="filler_skipped",
                agent=self._events.agent,
                reason="barge_in_pending",
            )
            return
        clips = self._fillers.get(self._events.agent or "")
        if clips is None:
            return
        # Claimed synchronously between the checks above and the first
        # await below: from here `tail` waits for the clip's tail
        # instead of cancelling the timer.
        self._filler_sounding = True
        index = self._filler_fires % len(clips.clips)
        self._filler_fires += 1
        elapsed_ms = round((asyncio.get_running_loop().time() - armed_at) * 1000)
        self._events.info(
            "session %s: no reply audio after %d ms, playing filler %d",
            self.session_id,
            elapsed_ms,
            index,
            event="filler_played",
            agent=self._events.agent,
            delay_ms=elapsed_ms,
            phrase_index=index,
        )
        try:
            await self._output.begin_speaking()
            resampler = Resampler(clips.sample_rate, self._output.output_sample_rate)
            # Encoded whole before the first await, and sent once. The
            # reply task feeds the same encoder between its own awaits,
            # so a flush split off after an await could carry out audio
            # that belongs to the reply.
            batch = (
                self._output.encode_audio(resampler.process(clips.clips[index]))
                + self._output.encode_audio(resampler.flush())
                + self._output.flush_encoder()
            )
            await self._output.send_audio(batch)
        except (DeviceGone, RuntimeError):
            # Broader than the reply body's, and knowingly so. The `try`
            # above covers resampling, encoding and the encoder flush as
            # well as the send, so the `RuntimeError` half can still be
            # a local bug swallowed as a disconnect. Narrowing it means
            # deciding what a filler that fails to encode should do,
            # which is the filler path's own question and is tracked as
            # #182.
            return
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("session %s: filler playback failed", self.session_id)

    async def tail(self) -> None:
        """The reply's own audio is ready: an unfired timer loses (the
        silence it was going to mask is over), and a clip already
        sounding is waited out, so the first real sentence queues
        behind its tail rather than interleaving with it or cutting it
        mid-word."""
        task = self._filler_task
        if task is None or task.done():
            return
        if not self._filler_sounding:
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def settle(self) -> None:
        """End-of-reply cleanup, whatever path ended it: stand down an
        unfired timer, wait out a clip still sounding (a reply that
        failed silently still finishes its "let me see" before the
        closing tts stop), and see a cancellation through so nothing
        of this turn's filler outlives the turn."""
        task = self._filler_task
        self._filler_task = None
        if task is None:
            return
        if not self._filler_sounding:
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        self._filler_sounding = False

    def abandon(self) -> None:
        """The reply this mask belongs to is being cancelled, and the
        filler is reply audio: it dies with the reply rather than being
        waited out. Fire and forget, because the settle that follows is
        what sees the cancellation through."""
        if self._filler_task is not None:
            self._filler_task.cancel()
