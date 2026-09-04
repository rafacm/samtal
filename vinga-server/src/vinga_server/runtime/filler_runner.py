"""This session's cached speech: the latency mask it plays when a reply
is late, and the phrase it says when one fails.

The sibling of [`filler.py`](../filler.py), and the two are told apart
by when they run: that module builds both kinds of clip once at boot,
synthesizing each agent's phrases in its own voice and caching them as
PCM, while this one is the per-session runner that decides whether a
cached clip goes out at all. Nothing here synthesizes anything, and
nothing there knows a session exists.

One class for both, because they are one act with two triggers: taking
a clip from this world's cache, resampling it, encoding it in one batch
and sending it down the device's paced path. Everything that act needs
(the output handle, the read-only clip views, the batching rule that
keeps the shared encoder honest) is held here once, and a second owner
of paced clip playback would be a second copy of the rule the risk
section of #74 exists about.

They differ in what triggers them and in what they are. The silence
between the end of an utterance and the first audio of a reply is where
a voice assistant feels dead (#48), and a filled pause is how a human
holds that gap: a timer armed at the transcription, and a clip if the
reply has not started speaking by the time it expires. That is a noise
that buys time, so it announces nothing and stays out of the transcript.
The failure phrase is asked for rather than timed, by the reply body's
own failure arm, and it carries information the user needs, so it goes
to the display as well as the speaker (#384). It is still not something
the model said, and it enters no transcript either.

The mask yields to whoever holds the floor, which is read as two
questions and answered with nothing. A user still speaking, or a
barge-in being confirmed, stands the timer down, because a mask that
talks over the user is worse than no mask at all. The failure phrase
asks no such question: it is said after the reply is over, when nobody
is holding anything.
"""

import asyncio
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import TYPE_CHECKING

from vinga_server.audio.resample import Resampler
from vinga_server.device.boundary import DeviceGone, DeviceOutput
from vinga_server.events import SessionEvents, logger
from vinga_server.events.catalog import (
    FillerPlayed,
    FillerSkippedForBargeIn,
    FillerSkippedForSpeech,
    ReplyFailedFallback,
)
from vinga_server.events.values import (
    ConversationId,
    Count,
    Flag,
    Identifier,
    Whole,
)
from vinga_server.filler import FallbackClip, FillerClips

if TYPE_CHECKING:
    # Named for the annotation alone: the runner never needs the floor
    # module at import time, and keeping it out is what stops the two
    # from growing a runtime edge nothing asked for.
    from vinga_server.runtime.turntaking import TurnTaking


class FillerRunner:
    """One turn's latency mask at a time, and its failure phrase, for
    the life of one connection.

    `fillers` is the clip cache keyed by agent, read off the generation
    this session bound and never asked for again: a reload that
    re-synthesizes a clip reaches the next session rather than this
    conversation, which is what keeps the masking a turn was armed under
    from changing under it. Empty means no agent masks its latency, and
    a plain dictionary is as much a cache as the mapping a generation
    carries, so a test that hands it two entries need not build a world.
    `agents` is what the device is bound to, which is what the arming
    rule asks rather than only the agent talking now.

    `fallbacks` is the other cache off the same generation, read the
    same way and bound at the same instant: what a failed reply says is
    the phrase this conversation opened with, whatever a reload has
    synthesized since. It defaults to empty, which is honest for every
    caller that is not about failure phrases and is what a world built
    before anything was synthesized holds.

    `turn` is whoever is deciding the floor, and the fire-time
    stand-down asks it two things: how much of what was fed the
    endpointer classified as speech (`speech_ms`), and whether the
    outgoing frames are being held for a barge-in confirmation
    (`output_paused`). Two reads and nothing else. The mask yields to
    the user, so nothing about the floor is this runner's to change, and
    reading rather than writing is what keeps the question out of the
    device boundary, where only the filler would ever have asked it."""

    def __init__(
        self,
        events: SessionEvents,
        output: DeviceOutput,
        fillers: Mapping[str, FillerClips],
        agents: Sequence[str],
        turn: "TurnTaking",
        fallbacks: Mapping[str, FallbackClip] = MappingProxyType({}),
    ) -> None:
        self._events = events
        self.session_id = events.session_id
        self._output = output
        self._fillers = fillers
        self._fallbacks = fallbacks
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
            self._events.emit(
                lambda: FillerSkippedForSpeech(
                    agent=Identifier(self._events.agent),
                    conversation=ConversationId(self._events.conversation),
                    speech_ms=Whole(speech_ms),
                )
            )
            return
        if self._turn.output_paused:
            self._events.emit(
                lambda: FillerSkippedForBargeIn(
                    agent=Identifier(self._events.agent),
                    conversation=ConversationId(self._events.conversation),
                )
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
        self._events.emit(
            lambda: FillerPlayed(
                agent=Identifier(self._events.agent),
                conversation=ConversationId(self._events.conversation),
                delay_ms=Whole(elapsed_ms),
                phrase_index=Count(index),
            )
        )
        failed: str | None = None
        try:
            await self._output.begin_speaking()
            resampler = Resampler(clips.sample_rate, self._output.output_sample_rate)
            # Three encoder calls with no await between them, all of
            # them done before the send below is awaited, and sent once.
            # The reply task feeds the same encoder between its own
            # awaits, so a flush split off after an await could carry
            # out audio that belongs to the reply.
            batch = (
                self._output.encode_audio(resampler.process(clips.clips[index]))
                + self._output.encode_audio(resampler.flush())
                + self._output.flush_encoder()
            )
            await self._output.send_audio(batch)
        except DeviceGone:
            # The device left mid-clip, which ends the clip and nothing
            # else. Only this type, the way the reply body catches it
            # (#137): the edge translates both of the transport's
            # disconnect shapes into it, and `DeviceGone` is what the
            # two device-facing calls in the block above raise, so
            # nothing a resample or an encode can go wrong with reaches
            # here. A bare `RuntimeError` used to be caught alongside
            # it and returned on in silence, which made a local bug
            # look like a disconnect; it now falls to the arm below and
            # is logged (#182).
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # A filler that could not be resampled, encoded, flushed or
            # sent is a bug in this process, and the mask stands down
            # exactly as before: swallowed, the reply unharmed. The
            # class name and nothing else, no `exc_info` and no
            # `str(exc)`, for the reason the reply body gives: a
            # traceback rendered onto the retained log prints the whole
            # chain behind it, and a failure anywhere near provider
            # bytes can carry them in its message (#182).
            failed = type(exc).__name__
        # Reported out here rather than in the arm: inside it the
        # swallowed exception is still the active one, so a logging call
        # that itself failed would escape carrying it as `__context__`,
        # which is the message this line took care not to print.
        if failed is not None:
            logger.error(
                "session %s: filler playback failed: %s", self.session_id, failed
            )

    async def _waited_out(self, task: asyncio.Task[None]) -> None:
        """Wait out one clip task, telling its cancellation apart from a
        cancellation of the caller.

        Both arrive at this await as a `CancelledError` and they mean
        opposite things. The clip's own is the thing being waited for
        and is over when it lands: swallowing it is the whole point of
        waiting. The caller's is a barge-in or an abort taking the turn
        away, and swallowing that is how a reply goes on running after
        the user has interrupted it. Worse in the failure arm, which
        waits here before it speaks: the confirmation ladder pauses the
        outgoing frames before it cancels and resumes them only after
        the cancel has been awaited (`runtime/turntaking.py`), so a
        swallowed cancellation walks straight into a paced send that
        cannot complete until the thing waiting on it returns
        (`device/pacing.py`).

        Told apart by counting cancellation requests against this task
        rather than by inspecting the clip's: a cancel delivered to a
        task awaiting another one cancels that other one too, so which
        of the two ended up cancelled says nothing about who asked. The
        count taken before the await is what a new request is measured
        against, which is also what keeps a reply that was ALREADY
        cancelled from re-raising here: its `finally` has a closing
        `tts stop` still to send, and that is not this method's to take
        away.
        """
        current = asyncio.current_task()
        requested = 0 if current is None else current.cancelling()
        try:
            await task
        except asyncio.CancelledError:
            if current is not None and current.cancelling() > requested:
                raise

    async def tail(self) -> None:
        """The reply's own audio is ready: an unfired timer loses (the
        silence it was going to mask is over), and a clip already
        sounding is waited out, so the first real sentence queues
        behind its tail rather than interleaving with it or cutting it
        mid-word.

        A cancellation of the reply arriving during the wait goes on
        rather than being swallowed, for the reason `_waited_out`
        gives."""
        task = self._filler_task
        if task is None or task.done():
            return
        if not self._filler_sounding:
            task.cancel()
        await self._waited_out(task)

    async def settle(self) -> None:
        """End-of-reply cleanup, whatever path ended it: stand down an
        unfired timer, wait out a clip still sounding (a reply that
        failed silently still finishes its "let me see" before the
        closing tts stop), and see a cancellation through so nothing
        of this turn's filler outlives the turn.

        The clip's own cancellation is what is seen through. A
        cancellation of the caller that arrives during the wait is a
        different fact and goes on, for the reason `_waited_out`
        gives."""
        task = self._filler_task
        self._filler_task = None
        if task is None:
            return
        if not self._filler_sounding:
            task.cancel()
        try:
            await self._waited_out(task)
        finally:
            self._filler_sounding = False

    async def speak_fallback(self) -> None:
        """Say the active agent's fixed failure phrase, on the display
        and, where a clip was cached, out loud.

        Here rather than in the reply body because this class already
        holds all three things it takes: the output handle, the
        read-only view of what was synthesized for this world, and the
        paced recipe below that a clip has to go out through. The reply
        body asks; nothing about playing a cached clip moves.

        The phrase goes to the display as well as the speaker, which is
        the one place this differs from the filler above and the
        difference is deliberate. A filled pause is a noise that buys
        time and sends no `sentence_start` anywhere. This carries
        information the user needs, and `sentence_started` is the only
        display the protocol has. It is still not something the model
        said, so nothing here touches the reply's spoken sentences, the
        conversation history or the stored turn: what says it happened
        is the record below.

        Nothing at all where the agent has no phrase, which is an agent
        whose section is off and a world built before anything was
        synthesized alike: an entry that is present is one to say, which
        is the same rule the clip lookup above follows. A phrase whose
        synthesis failed is present without audio, and the turn is shown
        and not heard.

        The contract is the fire path's, exactly: `CancelledError`
        propagates, because swallowing one would consume a barge-in or
        an abort and the reply's own `finally` sends the closing
        `tts stop` either way; a device that went away is swallowed,
        since there is nobody left to tell; anything else is a bug in
        this process, reported by class name and swallowed, because a
        broken notice must not cost the turn the `tts stop` that re-arms
        a device's listening.
        """
        cached = self._fallbacks.get(self._events.agent or "")
        if cached is None:
            return
        self._events.emit(
            lambda: ReplyFailedFallback(
                agent=Identifier(self._events.agent),
                conversation=ConversationId(self._events.conversation),
                audio=Flag(cached.clip is not None),
            )
        )
        failed: str | None = None
        try:
            await self._output.begin_speaking()
            await self._output.sentence_started(cached.phrase)
            if cached.clip is not None:
                resampler = Resampler(cached.sample_rate, self._output.output_sample_rate)
                # Three encoder calls with no await between them, for
                # the reason `_fire` gives at length: the reply task
                # feeds the same encoder, and a flush split off after an
                # await could carry out audio that is not this clip's.
                batch = (
                    self._output.encode_audio(resampler.process(cached.clip))
                    + self._output.encode_audio(resampler.flush())
                    + self._output.flush_encoder()
                )
                await self._output.send_audio(batch)
        except DeviceGone:
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # The class name and nothing else, for the reason the fire
            # path gives: a traceback rendered onto the retained log
            # prints the whole chain behind it, and this runs in the arm
            # that catches whatever a provider raised.
            failed = type(exc).__name__
        # Reported out here rather than in the arm, for the reason the
        # fire path gives: inside it the swallowed exception is still
        # the active one, and a logging call that itself failed would
        # escape carrying it.
        if failed is not None:
            logger.error(
                "session %s: fallback playback failed: %s", self.session_id, failed
            )

    def abandon(self) -> None:
        """The reply this mask belongs to is being cancelled, and the
        filler is reply audio: it dies with the reply rather than being
        waited out. Fire and forget, because the settle that follows is
        what sees the cancellation through."""
        if self._filler_task is not None:
            self._filler_task.cancel()
