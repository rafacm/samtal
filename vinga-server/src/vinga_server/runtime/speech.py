"""Turning a reply's sentences into audio, one ahead of the one being
spoken, and deciding which of them may be spoken at all.

The lookahead is a runtime concern by the litmus test: deciding when
speech synthesis may begin is not something that would exist if the
backend were a telephone call to a human. What it produces (PCM at the
voice's own rate) crosses the device-facing boundary; how far ahead it
runs, and what happens to a sentence nobody will hear, stays here.

The guard at the bottom is the other half of that last clause, and it
is here for the same reason: what may be said is a fact about the
sentence about to be spoken, and this is the module that owns sentences
on their way to a voice. `text.py` cuts them and stays ignorant of
tools; the tool loop asks this once per sentence and speaks whatever
comes back.

**The bound the guard states rather than closes.** A sentence is tested
whole, as it arrives, because a sentence has already been promised to
TTS by the time it exists: the splitter cuts at a newline, so a
pretty-printed call arrives as a handful of fragments no decoder can
read, and each of them speaks. Closing that would mean holding
sentences across newlines, which is where the model's own formatting
says one thing ended, so the residue is left visible through the event
instead.

**What the splitter does hold, and what bounds it.** A compact call is
one line and reaches here whole, which it did not always: the
punctuation rule cut `{"a":"Milk. And eggs"}` at the `. ` inside the
argument into two fragments that were each ordinary text, and both
were spoken (#391). `text.py` now stands the punctuation rule down
while a brace is open, counting braces outside JSON strings, which is
the smallest rule that keeps a compact object in one piece for this to
read. It is bounded three ways, because a sentence held there is a
sentence not yet being synthesized: a newline cuts whatever is open,
`flush` releases everything at the end of the stream, and
`MAX_HELD_FOR_A_BRACE` caps the span, so an unmatched `{` in prose or
a quotation mark that opens a string nothing closes costs a bounded
delay rather than the rest of the reply.

**And the cost of the argument-only prong.** Matching an object's key
set against a tool's declared properties withholds a JSON example whose
keys happen to mirror an offered tool, which an agent asked to explain
one would say out loud. That is the trade the narrower rule cannot
make: the shape actually observed in the field carries no name at all
(`{"volume":"100"}`), so a guard that insisted on one would not catch
the thing it exists for. The event makes each withholding visible, and
a deployment for which the trade is wrong changes the model rather than
the guard.
"""

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Sequence
from typing import Any

from vinga_server.providers import ToolDef, TtsProvider


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


# One decoder for every sentence of every reply. It holds no state
# between calls: `raw_decode` takes the string and the index to start
# at and answers the value and where it ended, so a shared instance is
# a saved construction rather than anything two replies could see each
# other through.
_DECODER = json.JSONDecoder()


def _objects(sentence: str) -> Iterator[dict[str, Any]]:
    """Every complete JSON object inside this sentence, in the order
    they open.

    Complete is the whole point. A model that leaks a call rarely leaks
    it alone: what arrives is `Sure: {"volume":"100"}`, or a call and an
    `Okay.` inside one cut, so "does the sentence parse" answers no for
    exactly the sentences this exists to catch. Walking the `{`
    positions and asking for a balanced object at each answers the
    question that matters instead, and it is bounded by the sentence's
    own length twice over: one attempt per `{` in it, and each attempt
    reading no further than its end.

    An object nested inside another is offered as well, because the
    outer one is asked for first and a failure at either position costs
    only the attempt. Anything that is not an object at a `{` (a stray
    brace in prose, a truncated call) is skipped and the walk goes on.
    """
    start = 0
    while (index := sentence.find("{", start)) != -1:
        try:
            decoded, _ = _DECODER.raw_decode(sentence, index)
        except ValueError:
            decoded = None
        if isinstance(decoded, dict):
            yield decoded
        start = index + 1


def _properties(tool: ToolDef) -> frozenset[str]:
    """The top-level property names one tool declares, and nothing
    deeper: what a match is made of is the names an argument object
    would carry, so a nested schema's own properties are a different
    tool's vocabulary as far as this is concerned."""
    schema = tool.input_schema
    declared = schema.get("properties") if isinstance(schema, dict) else None
    return frozenset(declared) if isinstance(declared, dict) else frozenset()


def _named_call(decoded: dict[str, Any], published: frozenset[str]) -> str | None:
    """The offered tool this object calls by name, in either of the two
    shapes a model writes one in: its own `name`, and the `name` inside
    the object under `function`, which is the OpenAI wire shape models
    parrot back as prose."""
    name = decoded.get("name")
    if isinstance(name, str) and name in published:
        return name
    function = decoded.get("function")
    if isinstance(function, dict):
        inner = function.get("name")
        if isinstance(inner, str) and inner in published:
            return inner
    return None


def _by_arguments(decoded: dict[str, Any], tools: Sequence[ToolDef]) -> list[str]:
    """The offered tools whose declared properties this object's keys
    all fall inside, which is the shape observed in the field: a leaked
    call whose name never made it out of the model, only its arguments.

    Key names and never values. The observed payload sent `"100"` where
    the schema declares an integer, so a rule that validated would have
    passed exactly the call it exists to catch. An empty object matches
    nothing: every schema trivially contains no keys, and `{}` in a
    sentence is punctuation rather than a call.
    """
    keys = frozenset(decoded)
    if not keys:
        return []
    return [tool.name for tool in tools if keys <= _properties(tool)]


def withhold_tool_shaped(
    sentence: str,
    tools: Sequence[ToolDef],
    report: Callable[[str | None, int], None],
) -> bool:
    """Whether this sentence is shaped like a call to a tool this reply
    actually offered, and so is dropped instead of spoken.

    True means withheld and already reported, through `report`, which is
    handed the published name of the tool identified and how many
    characters were not spoken. The sentence itself goes no further than
    this call: `report` is given a count rather than the text, so
    nothing downstream is holding the bytes it would have to remember
    not to print.

    Both prongs are anchored to the tools of this reply and to nothing
    else. Someone asking an agent to explain a JSON snippet is an
    ordinary conversation, and "looks like JSON" would eat it; what is
    withheld is an object naming a tool that was on the table, or one
    whose keys all fall inside the properties one of them declared.

    A named match identifies its tool. An argument-only match identifies
    one only where exactly one tool's schema fits: a key set that fits
    several is withheld the same, because every reading of it is
    tool-shaped, and it names none of them, because which one it was is
    exactly what could not be decided.

    The whole sentence goes, never a part of it. A call embedded beside
    prose is one cut of a reply and there is no honest way to speak the
    half of it that was an answer: the leak is evidence the model is
    writing calls into its speech, and half a sentence read aloud is a
    worse turn than one sentence missing from it.
    """
    published = frozenset(tool.name for tool in tools)
    for decoded in _objects(sentence):
        named = _named_call(decoded, published)
        if named is not None:
            report(named, len(sentence))
            return True
        matched = _by_arguments(decoded, tools)
        if matched:
            report(matched[0] if len(matched) == 1 else None, len(sentence))
            return True
    return False
