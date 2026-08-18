"""Who holds the floor: the mic feed, the utterance buffer, and the
gates in front of a barge-in.

The sibling of [`turns.py`](turns.py), and the two are told apart by
what they are about: `turns.py` records what a turn contained, this
module decides who is speaking. Nothing here reads a transcript and
nothing there watches the microphone.

While the device listens, every decoded frame lands in the utterance
buffer and in the agent's endpointer. When the endpointer says the
utterance ended, or the device says so by hand, the buffer is trimmed
back to the speech and handed to the orchestrator as the thing to
answer.

An utterance that ends while a reply is streaming is the user cutting
in, which is what barge-in is. An endpointer-driven cancel is gated: a
reply is only cancelled on evidence of user speech (enough classified
speech, a transcript when in doubt), because acoustics alone are as
often noise or the reply's own bleed as the user (#28). A manual
`listen stop` mid-reply is a deliberate act and cancels unconditionally.

The orchestrator is reached through `ReplyControl` and nothing else, so
the reply task, the conversation history, and the provider
observability stay where they are: this module decides, and something
else acts on the decision.
"""

import asyncio
from typing import Protocol

from samtal_server.config import ServerConfig
from samtal_server.device.boundary import PIPELINE_SAMPLE_RATE, DeviceOutput
from samtal_server.events import SessionEvents, logger
from samtal_server.providers import AsrResult, Endpointer

# How much recent mic audio the utterance buffer keeps. A realtime
# session listens through the silences too, so without a bound the
# buffer would grow for the whole session (about 115 MB at the one-hour
# cap). Well above the endpointer's 10 s `max_utterance_ms`, so what a
# trim can ever drop is silence nobody is going to transcribe.
UTTERANCE_TAIL_S = 30
UTTERANCE_TAIL_BYTES = UTTERANCE_TAIL_S * PIPELINE_SAMPLE_RATE * 2


class ReplyControl(Protocol):
    """The slice of the orchestrator the turn-taking side may touch:
    whether a reply is in flight, how one is started, how one is
    cancelled, and the confirmation transcription the gate ladder needs.

    Narrow on purpose. The confirmation is injected whole rather than
    assembled here, because what it runs inside (the provider watch, the
    session's language lock) belongs to the orchestrator and the ladder
    needs none of it to decide."""

    def replying(self) -> bool: ...

    def start_reply(self, pcm: bytes, result: AsrResult | None) -> None: ...

    async def cancel_reply(self) -> None: ...

    async def confirm_transcript(self, pcm: bytes) -> AsrResult: ...


class TurnTaking:
    """One conversation's floor, for the life of one connection.

    `endpointer` is public and starts as None: the agent's VAD makes it,
    and an agent handover replaces it, since the previous agent's
    endpointer carries the previous agent's tuning and mid-utterance
    state."""

    def __init__(
        self,
        events: SessionEvents,
        output: DeviceOutput,
        server: ServerConfig,
        reply: ReplyControl,
    ) -> None:
        self._events = events
        self.session_id = events.session_id
        self._output = output
        self._server = server
        self._reply = reply
        self.endpointer: Endpointer | None = None
        self._utterance = bytearray()
        # How much the tail cap has cut from the front of `_utterance`
        # since the last reset, which is what maps the endpointer's
        # speech-start offset (counted over everything fed) onto a
        # position in the buffer that remains.
        self._utterance_dropped = 0
        # The PCM the reply task in flight was handed, held until its
        # ASR call returns. Still being set is the mid-ASR marker: a
        # barge-in landing then killed the head of the user's own
        # sentence, so this is also the merge source that reconstitutes
        # it in front of the continuation.
        self._reply_pcm: bytes | None = None
        # Whether this runtime is holding the device's outgoing frames
        # while a barge-in is confirmed. Tracked here rather than asked
        # of the device: the runtime is the only thing that ever pauses
        # the stream, so its own intent is the honest answer, and the
        # boundary stays free of a query only the filler would read.
        self._output_paused = False

    @property
    def output_paused(self) -> bool:
        """Whether the outgoing frames are held for a confirmation, read
        by the filler's fire-time stand-down and written by nothing
        outside the ladder below."""
        return self._output_paused

    async def feed(self, pcm: bytes) -> None:
        """One decoded mic frame, at `PIPELINE_SAMPLE_RATE`. Called only
        while the device is listening and the edge's guards passed, which
        is why the VAD sample below records the listening as true without
        asking."""
        if self.endpointer is None:
            return
        self._utterance.extend(pcm)
        if len(self._utterance) > UTTERANCE_TAIL_BYTES:
            excess = len(self._utterance) - UTTERANCE_TAIL_BYTES
            del self._utterance[:excess]
            self._utterance_dropped += excess
        endpointed = self.endpointer.feed(pcm)
        # After the feed, so the sample is the endpointer's opinion of
        # the audio just recorded rather than of the frame before it.
        self._events.vad(self.endpointer.speech_ms(), True, self._reply.replying())
        if endpointed:
            await self.finish_utterance(endpointed=True)

    def restart(self) -> None:
        """Start a fresh utterance: the buffer, the drop accounting and
        the endpointer all go back to where they began."""
        self._utterance.clear()
        self._utterance_dropped = 0
        if self.endpointer is not None:
            self.endpointer.reset()

    async def manual_stop(self) -> None:
        """A manual end of utterance. Nothing buffered means nothing was
        said, so there is nothing to answer."""
        if self._utterance:
            await self.finish_utterance()

    def clear_pending(self) -> None:
        """The reply in flight is past its own ASR, or over: nothing of
        the user's sentence is left for a barge-in to destroy, so the
        merge source comes down."""
        self._reply_pcm = None

    def speech_ms(self) -> int:
        """How much of what was fed the endpointer classified as speech,
        rounded to milliseconds, and zero where no endpointer exists
        yet."""
        return round(self.endpointer.speech_ms()) if self.endpointer is not None else 0

    async def finish_utterance(self, endpointed: bool = False) -> None:
        """Hand the buffered utterance to the reply task. Listening then
        stops until the device asks again, which auto mode does by
        sending `listen start` after the reply's `tts stop`. Not in
        realtime mode: that device asked once and is still streaming, so
        stopping here would leave nobody to re-arm it and the session
        would answer one utterance and go deaf.

        An utterance that ends while a reply is still streaming is the
        user cutting in, so the reply in flight is cancelled and this one
        answered instead. Cancelling sends the old reply's `tts stop`
        before the new reply's `tts start`, because `cancel_reply` waits
        for the task it cancelled. When the endpointer decided the end,
        the cancel first has to pass the gates in `_gate_barge_in`,
        because that decision is acoustic and acoustics mid-reply are as
        often noise or playback bleed as the user; a manual `listen
        stop` is the user holding the button and speaking, so it stays
        unconditional. With `server.barge_in` off the utterance is
        dropped instead, which is what a board with leaky echo
        cancellation wants; from the mic that case is already filtered
        in `_handle_audio`, so what reaches here is a manual `listen
        stop` mid-reply."""
        speech_ms = self.speech_ms()
        pcm = self._trimmed_utterance()
        self.restart()
        # Reported before any of the gates below can drop the utterance:
        # somebody talked, whether or not it earns a reply, and the edge
        # counts the idle timeout from both ends of a turn.
        self._output.user_turn_ended()
        result: AsrResult | None = None
        if self._reply.replying():
            if not self._server.barge_in:
                logger.warning(
                    "session %s: dropping an utterance, a reply is already streaming",
                    self.session_id,
                )
                return
            if endpointed:
                gated = await self._gate_barge_in(pcm, speech_ms)
                if gated is None:
                    return
                pcm, result = gated
            else:
                self._events.info(
                    "session %s: barge-in, cancelling the reply in flight",
                    self.session_id,
                    event="barge_in",
                    speech_ms=speech_ms,
                    **self._speaking_ms_field(),
                )
                await self._reply.cancel_reply()
        logger.info(
            "session %s: utterance of %.1f s",
            self.session_id,
            len(pcm) / 2 / PIPELINE_SAMPLE_RATE,
        )
        self._reply_pcm = pcm if result is None else None
        self._reply.start_reply(pcm, result)

    async def _gate_barge_in(
        self, pcm: bytes, speech_ms: int
    ) -> tuple[bytes, AsrResult | None] | None:
        """Decide what an endpointed utterance may do to the reply in
        flight: None to drop it and let the reply live, or the PCM to
        answer (with its transcription, when confirming it already ran
        ASR). The gates exist because a reply is only cancelled on
        evidence of user speech; acoustics alone can at most pause it
        (see the ADR of that name).

        In order: too little classified speech is a noise blip and is
        dropped; a reply still inside ASR was transcribing the head of
        the user's own sentence, so it is cancelled and its audio
        prepended, one reply answering the whole sentence; right after
        playback starts, the onset transient the device's echo
        cancellation lets through is dropped; anything else pauses the
        outgoing frames and asks ASR, and only a non-empty transcript
        cancels. An empty one resumes the paced stream where it
        stopped, so a wrong pause costs one ASR latency, not a reply."""
        server = self._server
        if speech_ms < server.barge_in_min_speech_ms:
            self._events.info(
                "session %s: barge-in suppressed, %d ms of speech is under the "
                "%.0f ms floor",
                self.session_id,
                speech_ms,
                server.barge_in_min_speech_ms,
                event="barge_in_suppressed",
                reason="min_speech",
                speech_ms=speech_ms,
            )
            return None
        if self._reply_pcm is not None:
            head = self._reply_pcm
            self._events.info(
                "session %s: barge-in mid-transcription, merging the utterances",
                self.session_id,
                event="barge_in_merged",
                speech_ms=speech_ms,
            )
            await self._reply.cancel_reply()
            return head + pcm, None
        loop = asyncio.get_running_loop()
        if (
            self._output.speaking_started_at() is not None
            and (loop.time() - self._output.speaking_started_at()) * 1000
            < server.barge_in_refractory_ms
        ):
            self._events.info(
                "session %s: barge-in suppressed inside the refractory window",
                self.session_id,
                event="barge_in_suppressed",
                reason="refractory",
                speech_ms=speech_ms,
            )
            return None
        self._pause_output()
        try:
            # In the receive path on purpose: incoming frames buffer in
            # the socket for the duration, so ordering is unaffected.
            result = await self._reply.confirm_transcript(pcm)
        except Exception:
            logger.exception("session %s: barge-in confirmation failed", self.session_id)
            self._resume_output()
            return None
        if not result.text.strip():
            self._events.info(
                "session %s: barge-in suppressed, nothing transcribed",
                self.session_id,
                event="barge_in_suppressed",
                reason="no_transcript",
                speech_ms=speech_ms,
            )
            self._resume_output()
            return None
        self._events.info(
            "session %s: barge-in, cancelling the reply in flight",
            self.session_id,
            event="barge_in",
            speech_ms=speech_ms,
            **self._speaking_ms_field(),
        )
        await self._reply.cancel_reply()
        # The pause belonged to the cancelled reply; the one about to
        # answer starts with the frames flowing. Resuming rather than
        # clearing by hand shifts a pacing clock the next agent leg
        # restarts from scratch anyway.
        self._resume_output()
        return pcm, result

    def _speaking_ms_field(self) -> dict[str, int]:
        """The barge_in event's speaking_ms: milliseconds from
        speaking_started to the cancel decision, absent when the reply
        had not yet spoken."""
        if self._output.speaking_started_at() is None:
            return {}
        elapsed = asyncio.get_running_loop().time() - self._output.speaking_started_at()
        return {"speaking_ms": round(elapsed * 1000)}

    def _trimmed_utterance(self) -> bytes:
        """The buffered utterance, cut down to the speech plus a short
        pre-roll. A continuously listening session buffers everything
        between utterances (the reply's own playback time, the pause
        while the user thinks), and the endpointer rightly ignores that
        silence, so it would otherwise all ride along to ASR (#14). The
        pre-roll keeps the first phoneme intact; the trailing silence
        the endpointer sat through stays, since it is bounded and ASR
        needs the end of the speech anyway."""
        speech_start = self.endpointer.speech_start() if self.endpointer is not None else None
        if speech_start is None:
            return bytes(self._utterance)
        pre_roll = int(self._server.utterance_pre_roll_ms / 1000 * PIPELINE_SAMPLE_RATE) * 2
        start = speech_start - self._utterance_dropped - pre_roll
        if start <= 0:
            return bytes(self._utterance)
        start -= start % 2  # never split a 16-bit sample
        return bytes(self._utterance[start:])

    def _pause_output(self) -> None:
        self._output_paused = True
        self._output.pause_output()

    def _resume_output(self) -> None:
        self._output_paused = False
        self._output.resume_output()
