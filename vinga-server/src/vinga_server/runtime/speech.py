"""Turning a reply's sentences into audio, one ahead of the one being
spoken.

The lookahead is a runtime concern by the litmus test: deciding when
speech synthesis may begin is not something that would exist if the
backend were a telephone call to a human. What it produces (PCM at the
voice's own rate) crosses the device-facing boundary; how far ahead it
runs, and what happens to a sentence nobody will hear, stays here.
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable

from vinga_server.providers import TtsProvider


class _Synthesis:
    """One sentence being turned into audio, started before the moment it
    is needed.

    A reply is spoken sentence by sentence, and frames are paced to
    realtime, so sending a sentence takes about as long as hearing it.
    Synthesizing only when the previous sentence has finished playing
    therefore puts the next sentence's whole time to first byte on the
    speaker as silence, once per sentence, for the whole reply. Measured
    on a three-sentence reply: 617 ms and 520 ms between sentences
    through `gpt-4o-mini-tts`, reported from a board session as "hiccups
    in the assistant's voice". Starting the work early spends that time
    against playback that is already happening (#37).

    A task pulls from the provider into a one-chunk buffer. `chunks()`
    yields what has arrived and waits for the rest, so a sentence run
    ahead has already paid its time to first byte by the time anyone
    asks for it, while the first sentence of a reply still streams:
    nothing is held back waiting for a sentence to finish.

    The buffer holds one chunk, not the sentence. Playback consumes at
    realtime and a provider can produce much faster than that, so an
    unbounded buffer would hold a whole sentence of PCM per sentence run
    ahead and remove the backpressure the paced consumer used to apply
    to the provider. One chunk is all the lookahead needs, because what
    it exists to absorb is the wait for the *first* chunk.

    A failure is held rather than raised where it happened, and re-raised
    from `chunks()` at the point the sentence would have been spoken.
    That keeps the order of what a caller sees: the sentences before a
    failing one are spoken, and the reply fails where it would have.

    The first chunk is reported the same way a failure is, and for the
    same reason: this is where the request is made and where the answer
    arrives, so it is the only place the wait between them exists. What
    the caller does with it is the caller's; nothing here is timed for
    the device, which has its own `speaking_started` for that.
    """

    def __init__(
        self,
        sentence: str,
        tts: TtsProvider,
        report_failure: Callable[[BaseException, float], None],
        report_first_audio: Callable[[int], None],
    ) -> None:
        self.sentence = sentence
        self._buffer: asyncio.Queue[bytes | None] = asyncio.Queue()
        # The backpressure. Held per chunk waiting to be spoken and
        # released as each is taken, so the provider is asked for the
        # next chunk only once the previous has been picked up. The
        # bound is on data, not on the queue, so that the end-of-audio
        # sentinel below can always be delivered: a bounded queue that
        # is full when the consumer goes away leaves the drain task
        # blocked forever on a sentinel nobody is waiting for.
        self._room = asyncio.Semaphore(1)
        self._failure: BaseException | None = None
        self._report_failure = report_failure
        self._report_first_audio = report_first_audio
        self._task = asyncio.create_task(self._drain(tts))

    async def _drain(self, tts: TtsProvider) -> None:
        started = asyncio.get_running_loop().time()
        first = True
        try:
            async for chunk in tts.synthesize(self.sentence):
                if first:
                    first = False
                    self._report_first_audio(
                        round((asyncio.get_running_loop().time() - started) * 1000)
                    )
                await self._room.acquire()
                self._buffer.put_nowait(chunk)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - re-raised in chunks()
            self._failure = exc
            # Reported here rather than where it is re-raised: a
            # sentence run ahead can fail long before the moment it
            # would have been spoken, and the event an operator
            # correlates with a network policy should carry the time
            # the call actually failed at.
            self._report_failure(exc, asyncio.get_running_loop().time() - started)
        finally:
            self._buffer.put_nowait(None)

    async def chunks(self) -> AsyncIterator[bytes]:
        """The audio, in order, waiting only for what has not arrived."""
        while True:
            chunk = await self._buffer.get()
            if chunk is None:
                break
            self._room.release()
            yield chunk
        if self._failure is not None:
            raise self._failure

    def cancel(self) -> None:
        """Abandon a sentence that will never be spoken. Nothing to
        record: `_speak` counts a sentence only after its audio has gone
        out, so one dropped here was never counted anywhere."""
        self._task.cancel()

    async def wait_cancelled(self) -> None:
        with contextlib.suppress(asyncio.CancelledError):
            await self._task


async def speak_after(
    speaking: asyncio.Task[None] | None,
    sentence: str,
    tts: TtsProvider,
    report_failure: Callable[[BaseException, float], None],
    report_first_audio: Callable[[int], None],
    speak: Callable[[_Synthesis], Awaitable[None]],
) -> asyncio.Task[None]:
    """Start `sentence` synthesizing, wait for the sentence already being
    spoken to finish, then start speaking this one. Answers the task now
    speaking, for the next call to wait on.

    The first statement before the first await is the entire fix: the
    new sentence's time to first byte is spent against the previous
    sentence's playback, which is already happening, rather than against
    silence.

    Speaking is a task rather than an await so that it overlaps the model
    still streaming. Awaiting it here instead would mean a sentence is
    not spoken until the *next* one has been written, which would put the
    model's thinking time in front of the first word of every reply and
    make a one-sentence reply wait for the stream to end.
    """
    started = _Synthesis(sentence, tts, report_failure, report_first_audio)
    try:
        if speaking is not None:
            await speaking
    except BaseException:
        # The sentence just started will never be spoken now, whether
        # this was a provider failure or a barge-in cancelling the reply.
        started.cancel()
        await started.wait_cancelled()
        raise
    return asyncio.create_task(speak(started))
