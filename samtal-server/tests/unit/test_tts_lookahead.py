"""Synthesizing the next sentence while the current one is still playing.

A reply is spoken sentence by sentence and frames are paced to realtime,
so sending a sentence takes about as long as hearing it. Synthesizing
only once the previous sentence had finished playing therefore put the
next sentence's whole time to first byte on the speaker as silence, once
per sentence, for the whole reply: 617 ms and 520 ms between the
sentences of a three-sentence reply through `gpt-4o-mini-tts`, heard
from a board as "hiccups in the assistant's voice" (#37).

The provider here is slow on purpose, because that is the condition the
defect needs. The central assertion is an ordering rather than a
duration: a sentence's synthesis must begin before that sentence starts
being spoken, which is precisely the inversion the fix makes. Under the
old code `_speak` sent `sentence_start` and only then called the
provider, so it could not have been true of any sentence.
"""

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import replace
from typing import Any, cast

import pytest

import samtal_server.session as session_module
from samtal_server.config import Config
from samtal_server.providers import TtsProvider, build_agent_providers
from tests.unit.test_session_tools import ScriptedLlm, base_config, session_for

# One sentence's audio plays for longer than the next takes to start,
# which is what makes a single sentence of lookahead enough. Both are
# far bigger than any real jitter in the lane.
SYNTHESIS_LATENCY_S = 0.30
SENTENCE_AUDIO_S = 0.60

SENTENCES = ["One here.", "Two here.", "Three here."]


class SlowTts(TtsProvider):
    """A provider with a real time to first byte, and a record of when
    each sentence's synthesis actually began."""

    egress = False

    def __init__(self, latency_s: float = SYNTHESIS_LATENCY_S) -> None:
        self.sample_rate = session_module.OUTPUT_AUDIO.sample_rate
        self._latency_s = latency_s
        self.started: dict[str, float] = {}
        self.finished: dict[str, float] = {}
        self.in_flight = 0
        self.most_in_flight = 0
        self.cancelled: list[str] = []

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        loop = asyncio.get_running_loop()
        self.started[text] = loop.time()
        self.in_flight += 1
        self.most_in_flight = max(self.most_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self._latency_s)
            samples = int(self.sample_rate * SENTENCE_AUDIO_S)
            yield b"\x00\x00" * samples
            self.finished[text] = loop.time()
        except asyncio.CancelledError:
            self.cancelled.append(text)
            raise
        finally:
            self.in_flight -= 1


class TimedSocket:
    """A websocket that remembers when each thing went out."""

    def __init__(self) -> None:
        self.events: list[tuple[float, str, str]] = []

    def _now(self) -> float:
        return asyncio.get_running_loop().time()

    async def send_text(self, text: str) -> None:
        message = json.loads(text)
        self.events.append((self._now(), message["type"], message.get("text", "")))

    async def send_bytes(self, data: bytes) -> None:
        self.events.append((self._now(), "audio", ""))

    def said_at(self, sentence: str) -> float:
        """When the device was told this sentence was starting."""
        for when, kind, text in self.events:
            if kind == "tts" and text == sentence:
                return when
        raise AssertionError(f"{sentence!r} was never announced")

    def sentences(self) -> list[str]:
        return [text for _, kind, text in self.events if kind == "tts" and text]

    def audio_times(self, sentence: str) -> list[float]:
        """When each of this sentence's frames went out. The frames
        between its announcement and the next one are its own."""
        times: list[float] = []
        collecting = False
        for when, kind, text in self.events:
            if kind == "tts" and text:
                if collecting:
                    break
                collecting = text == sentence
            elif collecting and kind == "audio":
                times.append(when)
        return times

    def dead_air_before(self, sentence: str, previous: str) -> float:
        """The silence on the speaker between two sentences: the wall
        clock between the last frame of one and the first frame of the
        next. This is what the device experiences, which is the whole
        point, and it is measured off frames rather than off the
        provider because a sentence run ahead finishes synthesizing long
        before it finishes playing."""
        return min(self.audio_times(sentence)) - max(self.audio_times(previous))


def slow_session(
    rounds: Sequence[Any], tts: SlowTts, config: Config | None = None
) -> tuple[session_module.Session, TimedSocket]:
    script = ScriptedLlm(rounds)
    session = session_for(config or base_config(), "aa:bb:cc:dd:ee:01", {"poet": script})
    assert session._providers is not None
    session._providers = replace(session._providers, tts=tts)
    socket = TimedSocket()
    session.websocket = cast(Any, socket)
    return session, socket


async def speak_a_reply(session: session_module.Session) -> list[str]:
    spoken: list[str] = []
    await session._speak_reply("anything", spoken)
    return spoken


async def test_a_sentence_starts_synthesizing_before_it_starts_being_spoken() -> None:
    # The inversion, and the whole fix. Sentence two and three must be
    # under way while their predecessor is still on the speaker.
    tts = SlowTts()
    session, socket = slow_session([" ".join(SENTENCES)], tts)
    spoken = await speak_a_reply(session)

    assert spoken == SENTENCES
    assert socket.sentences() == SENTENCES
    for sentence in SENTENCES[1:]:
        assert tts.started[sentence] < socket.said_at(sentence), (
            f"{sentence!r} was still being synthesized when it should already "
            "have been waiting"
        )


async def test_the_gap_between_sentences_closes() -> None:
    # The same thing measured the way it was reported: as dead air on
    # the speaker. Each sentence's audio should follow the previous
    # sentence's at the frame cadence, with none of the provider's
    # latency in between.
    tts = SlowTts()
    session, socket = slow_session([" ".join(SENTENCES)], tts)
    await speak_a_reply(session)

    frame_s = session_module.OUTPUT_AUDIO.frame_duration / 1000
    for earlier, later in zip(SENTENCES, SENTENCES[1:], strict=False):
        gap = socket.dead_air_before(later, earlier)
        assert gap < frame_s * 2, (
            f"{gap * 1000:.0f} ms of dead air before {later!r}, which is the "
            "provider's latency landing on the speaker"
        )


async def test_the_frame_cadence_stays_smooth() -> None:
    # The pacer's schedule is absolute from the reply's first frame, so
    # a stall does not merely delay the frames after it: their target
    # times are already in the past, the sleep goes negative, and they
    # burst out to catch up. The device got a dropout followed by a
    # flood rather than a cleanly stretched reply, which is why total
    # playing time hides this defect and has to be measured per frame.
    tts = SlowTts()
    session, socket = slow_session([" ".join(SENTENCES)], tts)
    await speak_a_reply(session)

    frames = [when for when, kind, _ in socket.events if kind == "audio"]
    assert len(frames) > 3 * len(SENTENCES)
    frame_s = session_module.OUTPUT_AUDIO.frame_duration / 1000
    intervals = [later - earlier for earlier, later in zip(frames, frames[1:], strict=False)]
    flood = [gap for gap in intervals if gap < frame_s / 2]
    assert not flood, (
        f"{len(flood)} of {len(intervals)} frames went out faster than the "
        "cadence, which is the pacer catching up after a stall"
    )


async def test_only_one_sentence_is_ever_run_ahead() -> None:
    # Lookahead of one, not of everything: more would mean more
    # concurrent requests to the provider and more audio held for a
    # reply a barge-in may throw away.
    tts = SlowTts()
    session, _ = slow_session([" ".join(SENTENCES)], tts)
    await speak_a_reply(session)
    assert tts.most_in_flight == 2


async def test_a_sentence_run_ahead_and_never_spoken_is_not_recorded() -> None:
    # Barge-in cancels a reply mid-sentence. The sentence already being
    # synthesized behind it was never heard, so it must not reach the
    # history: whoever answers the interruption is written against what
    # the user actually heard.
    tts = SlowTts()
    session, socket = slow_session([" ".join(SENTENCES)], tts)
    spoken: list[str] = []
    reply = asyncio.create_task(session._speak_reply("anything", spoken))
    # Long enough for the first sentence to be playing and the second to
    # be in flight behind it, short enough that neither has finished.
    await asyncio.sleep(SYNTHESIS_LATENCY_S + SENTENCE_AUDIO_S / 2)
    reply.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await reply

    assert spoken == [], "nothing finished playing, so nothing was heard"
    assert SENTENCES[1] in tts.started, "the second sentence was indeed run ahead"
    assert SENTENCES[1] not in socket.sentences(), "and was never announced"
    # And it is not left running behind the reply that no longer exists.
    await asyncio.sleep(0)
    assert tts.in_flight == 0


async def test_lookahead_stops_at_the_end_of_a_round() -> None:
    # The loop breaks out between rounds to run tools, and the text of
    # the next round does not exist yet. Nothing may be run ahead across
    # that boundary, and the tools must not run over the top of speech.
    tts = SlowTts()
    session, socket = slow_session(
        [[SENTENCES[0], session_module.ToolCall("1", "remember", {"text": "x"})], SENTENCES[1]],
        tts,
        config=base_config(),
    )

    ran_at: list[float] = []

    async def run_tools(calls: Any, switches_left: int) -> tuple[list[Any], None]:
        ran_at.append(asyncio.get_running_loop().time())
        return [], None

    session._run_tools = run_tools  # type: ignore[method-assign]
    await speak_a_reply(session)

    # The first round's only sentence had finished being spoken before
    # the tools ran, and the second round's sentence was not touched
    # until after them.
    assert ran_at, "the tool round did not run"
    assert tts.finished[SENTENCES[0]] < ran_at[0]
    assert tts.started[SENTENCES[1]] > ran_at[0]


async def test_a_failing_sentence_still_lets_the_earlier_ones_be_heard() -> None:
    # A failure run ahead is held and raised where the sentence would
    # have been spoken, so the order of what a listener got stays
    # truthful: everything before it was heard, and the reply ends there.
    class FailsOnSecond(SlowTts):
        async def synthesize(self, text: str) -> AsyncIterator[bytes]:
            if text == SENTENCES[1]:
                self.started[text] = asyncio.get_running_loop().time()
                raise RuntimeError("the voice went away")
            async for chunk in super().synthesize(text):
                yield chunk

    tts = FailsOnSecond()
    session, socket = slow_session([" ".join(SENTENCES)], tts)
    spoken: list[str] = []
    with pytest.raises(RuntimeError, match="the voice went away"):
        await session._speak_reply("anything", spoken)

    assert spoken == [SENTENCES[0]]
    # The failing sentence produced no audio, and is recorded nowhere.
    assert socket.audio_times(SENTENCES[0])
    assert socket.audio_times(SENTENCES[1]) == []
    assert SENTENCES[2] not in tts.started
    # It was still announced first, which is what `sentence_start` has
    # always done and is not this change's to alter: the announcement
    # goes out when a sentence is about to be spoken, and whether the
    # audio behind it will arrive is not known at that moment for a
    # sentence that is still streaming.
    assert socket.sentences() == SENTENCES[:2]


async def test_a_handover_speaks_the_new_agents_voice() -> None:
    # The resampler and the provider are per agent leg, and a sentence
    # run ahead belongs to the leg that started it. Two providers here,
    # so a sentence spoken by the wrong one would be visible.
    config = base_config()
    first = SlowTts()
    session, socket = slow_session(
        [[SENTENCES[0], session_module.ToolCall("1", "switch_agent", {"agent": "tutor"})]],
        first,
        config=config,
    )
    fresh = build_agent_providers(config)
    second = SlowTts()
    session._agent_providers = {
        name: replace(
            providers,
            tts=second if name == "tutor" else first,
            llm=ScriptedLlm([SENTENCES[2]]),
        )
        for name, providers in fresh.items()
    }
    session._agents = ["poet", "tutor"]

    await speak_a_reply(session)

    assert SENTENCES[0] in first.started
    assert SENTENCES[2] in second.started
    assert SENTENCES[2] not in first.started
