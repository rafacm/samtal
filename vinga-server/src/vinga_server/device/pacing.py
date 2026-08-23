"""One reply's outgoing audio: the encoder, the cadence, and the
per-reply latches that hang off them.

A reply is not a buffer the device drains at its own speed. The board
plays what arrives as it arrives, so a long answer sent as fast as it
encodes floods its playback queue; what goes out has to leave at the
rate it will be heard at. That clock, the pause a barge-in confirmation
holds it with, and the once-per-reply facts measured against it (when
the first frame went out, whether the device has been told anything is
coming) are one cluster of arithmetic, and this is where it lives.

The session keeps the vocabulary either side of it. PCM comes in and
packets come out as plain bytes: `PlayableAudio` is a term of the
device boundary, and the events a first frame occasions are the
session's to emit, so nothing here has ever heard of an agent or an
event. What the session hands over instead is one closure per packet,
`deliver`, and this module decides when to call it.
"""

import asyncio
from collections.abc import Awaitable, Callable

from vinga_server.audio.opus import OpusEncoder


class ReplyPacer:
    """The reply audio clock: encode, pace, pause, and the latches that
    fire once per reply.

    Three clocks are deliberately distinct here, because they restart on
    different occasions. The cadence clock (`cadence_start`, and the
    frame count measured from it) restarts per agent leg, since a
    handover starts a new stream of frames. The speaking stamp and the
    `tts start` latch restart per reply, because the device is told once
    that an answer is coming however many agents end up producing it.
    And the encoder restarts never: its few milliseconds of lookahead
    staying inside is what keeps it reusable across both.
    """

    def __init__(self, sample_rate: int, frame_duration_ms: int) -> None:
        self._encoder = OpusEncoder(
            sample_rate=sample_rate, frame_duration_ms=frame_duration_ms
        )
        self._frame_s = frame_duration_ms / 1000
        # Outgoing frame pacing, reset per reply on the first frame.
        self._pace_start: float | None = None
        self._pace_count = 0
        # Whether this reply has sent any audio yet. Pacing restarts per
        # agent leg, so it cannot double as this flag: what reads it
        # must fire once per reply, not once per handover.
        self._speaking_started = False
        # When this reply's first frame went out, for the barge-in
        # refractory gate and the barge_in event's speaking_ms.
        self._speaking_started_at: float | None = None
        # Whether this reply has told the device it is speaking. The
        # `tts start` it stands for is sent once per reply, and never
        # before there is something to say.
        self._tts_started = False
        # The frame pacer waits on this before each send. The
        # transcript-confirmation gate clears it to hold playback while
        # ASR decides whether anything was said; resuming shifts the
        # pacing clock by the pause, so the stream picks up where it
        # stopped instead of bursting to catch up.
        self._pace_resume = asyncio.Event()
        self._pace_resume.set()
        self._pace_paused_at: float | None = None

    def encode(self, pcm: bytes) -> list[bytes]:
        """Feed reply PCM at the rate this pacer was built for; the
        answer holds every packet that filled, possibly none.
        Synchronous, and sends nothing."""
        return self._encoder.encode(pcm)

    def flush(self) -> list[bytes]:
        """Pad the encoder's pending partial frame with silence and
        encode it. The codec object itself is never reset between
        replies: its few milliseconds of lookahead staying inside is what
        keeps it reusable."""
        return self._encoder.flush()

    def reply_started(self) -> None:
        """A new reply: nothing has been spoken and the device has not
        been told anything is coming. The encoder is deliberately left
        alone."""
        self._speaking_started = False
        self._speaking_started_at = None
        self._tts_started = False

    def restart(self) -> None:
        """A new agent leg: the pacing clock starts again at its first
        frame."""
        self._pace_start = None
        self._pace_count = 0

    def first_frame(self, now: float) -> bool:
        """Whether this is the first frame of the reply, stamping it if
        it is.

        Answered rather than announced: what a first frame occasions is
        an event attributed to whichever agent is speaking, and neither
        the event nor the agent is a term of this module. The caller
        that gets True is the one that knows both.
        """
        if self._speaking_started:
            return False
        self._speaking_started = True
        self._speaking_started_at = now
        return True

    def tts_start_due(self) -> bool:
        """Whether the device still has to be told this reply is
        starting, latching the answer so only the first caller gets it.

        The latch is here rather than at the caller for the reason the
        stamp above is: it is a fact about one reply's audio, and the
        reply is what this object is a clock for.
        """
        if self._tts_started:
            return False
        self._tts_started = True
        return True

    def speaking_started_at(self) -> float | None:
        """When this reply's first frame was stamped, or None before it.
        Read by the barge-in refractory gate and by the filler."""
        return self._speaking_started_at

    @property
    def cadence_start(self) -> float | None:
        """The instant the current run of frames is paced from, or None
        before its first frame. A pause shifts it forward by the pause's
        length, which is what lets the cadence survive one."""
        return self._pace_start

    @property
    def paused(self) -> bool:
        """Whether the stream is being held before its next frame."""
        return self._pace_paused_at is not None

    def pause(self) -> None:
        """Hold the outgoing frame pacing before the next send. Audio
        stops within a frame either way; what a pause preserves is the
        option of resuming."""
        if self._pace_paused_at is not None:
            return
        self._pace_paused_at = asyncio.get_running_loop().time()
        self._pace_resume.clear()

    def resume(self) -> None:
        """Let the frames flow again, with the pacing clock shifted by
        the pause so the stream picks up where it stopped rather than
        bursting to catch up on the frames the pause displaced."""
        if self._pace_paused_at is None:
            return
        if self._pace_start is not None:
            self._pace_start += asyncio.get_running_loop().time() - self._pace_paused_at
        self._pace_paused_at = None
        self._pace_resume.set()

    async def transmit(
        self, packet: bytes, deliver: Callable[[bytes], Awaitable[None]]
    ) -> None:
        """One packet's whole passage out: wait for its slot, wait out a
        pause, hand it to `deliver`, and count it.

        The count advances only after `deliver` returns, which is the
        one ordering rule a caller has to know and the reason this is a
        transaction rather than a gate answered before the send. A
        device that vanished mid-reply raises out of `deliver`, and the
        frame it never received is not a frame this clock has paced;
        counting it would leave the cadence claiming a frame's worth of
        audio the speaker never played, and every slot after it would be
        that much late.
        """
        loop = asyncio.get_running_loop()
        if self._pace_start is None:
            self._pace_start = loop.time()
        await asyncio.sleep(self._pace_start + self._pace_count * self._frame_s - loop.time())
        # A barge-in being confirmed holds the stream here; resuming
        # shifts `_pace_start`, so the cadence survives the pause.
        await self._pace_resume.wait()
        await deliver(packet)
        self._pace_count += 1
