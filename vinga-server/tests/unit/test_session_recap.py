"""Offering a recap of a long thread, and what happens once one is
consented to.

The flow this file is about has one property everything else follows
from: **what the user hears is what the store keeps, and the store keeps
it only after they have heard it.** So the recap is spoken by the
runtime rather than handed back to a model to rephrase, and the
checkpoint is enqueued after the audio has gone out, which means a
barge-in, a voice that failed, a device that left and a database that
refused all leave the thread exactly as it was.

Driven as whole replies rather than through `run_reply`, and that is the
one thing that makes these assertions possible: the reply's own speaking
path is what carries the recap, so a suite that stubbed it would be
asserting about a promise it had removed. The voice is a double that
records what it was asked to say and says when it finished saying it,
and the store double snapshots that at the moment it is handed a
checkpoint.

The model is one `ScriptedLlm` across three rounds, because that is what
a consent reply really is: the round that calls the tool, the
summarization round the runtime runs against the same provider, and the
round seeded on the other side of the move.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any, cast

import pytest

from tests.support.configs import POET_MAC, base_config
from tests.support.events import both_formats, fields_of, only
from tests.support.providers import ScriptedLlm
from tests.support.sessions import (
    call,
    drive_reply,
    events_of,
    session_for,
    talking_thread,
    with_device,
)
from tests.support.stores import StoredThreads, a_backlog, a_milestone
from vinga_server.config import Config
from vinga_server.conversations.records import Acknowledgement
from vinga_server.device.boundary import DeviceGone
from vinga_server.device.session import DeviceSession
from vinga_server.providers import TtsProvider
from vinga_server.runtime import pipeline as pipeline_module
from vinga_server.runtime import resumption as resumption_module
from vinga_server.tools import builtin

GALAXY = "1f0c1d2e3a4b5c6d7e8f90a1b2c3d4e5"

UTTERANCE = b"\x00\x00" * 320

# What the summarizer answers with, in the shape a recap comes back in:
# one paragraph, said out loud. Distinctive so that a search for it
# through the events and the logs cannot match by accident.
RECAP = "We talked about the andromeda galaxy and where it is going."

# A budget nothing here fits inside, so every thread in this file is
# over budget and every plain resume offers the choice.
TIGHT = 512


def resuming(**overrides: Any) -> Config:
    return base_config(
        server={
            "conversations": {
                "enabled": True,
                "resumption": True,
                "resumption_budget_tokens": TIGHT,
            }
        },
        **overrides,
    )


def a_long_thread(count: int = 8, **kept: Any) -> StoredThreads:
    """A thread of `count` turns, each wide enough that the tight budget
    cannot hold two of them."""
    return StoredThreads(
        held={
            GALAXY: a_backlog(
                GALAXY,
                said=[
                    (f"utterance {index} " * 40, f"reply {index} " * 40)
                    for index in range(count)
                ],
                **kept,
            )
        }
    )


class RecordingTts(TtsProvider):
    """A voice that says what it was asked and reports when it has
    finished saying it.

    `finished` is the whole point: `_speak` appends a sentence to the
    reply only after every chunk has been paced out to the device, so a
    text that has arrived here is a text the user heard. The store
    double snapshots this list, which is how "stored after playback"
    becomes an assertion rather than a hope.
    """

    egress = False

    def __init__(self, chunks: int = 3) -> None:
        self.sample_rate = 24000
        self.asked: list[str] = []
        self.finished: list[str] = []
        self._chunks = chunks

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        self.asked.append(text)
        for _ in range(self._chunks):
            yield b"\x00\x00" * 240
        self.finished.append(text)


class FailingTts(RecordingTts):
    """A voice that breaks partway through a sentence, which is what a
    reply cut off by its own provider looks like from here."""

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        self.asked.append(text)
        yield b"\x00\x00" * 240
        raise RuntimeError("the voice service refused")


class HeldTts(RecordingTts):
    """A voice that stops in the middle of a sentence until it is let
    go, so a test can interrupt a reply while the recap is being
    spoken."""

    def __init__(self) -> None:
        super().__init__()
        self.speaking = asyncio.Event()
        self.release = asyncio.Event()

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        self.asked.append(text)
        yield b"\x00\x00" * 240
        self.speaking.set()
        await self.release.wait()
        self.finished.append(text)


class Kept:
    """Where the store would stand, keeping what it was handed and what
    was true of the voice when it was handed it."""

    def __init__(self, landing: bool = True) -> None:
        self.turns: list[Any] = []
        self.milestones: list[Any] = []
        self.heard_by_then: list[tuple[str, ...]] = []
        self._landing = landing
        self._voice: RecordingTts | None = None

    def watching(self, voice: RecordingTts) -> "Kept":
        self._voice = voice
        return self

    def record_turn(self, session_id: str, record: Any) -> None:
        self.turns.append(record)
        return None

    def record_milestone(self, session_id: str, record: Any) -> Acknowledgement:
        self.milestones.append(record)
        self.heard_by_then.append(
            () if self._voice is None else tuple(self._voice.finished)
        )
        landed = Acknowledgement()
        landed.settle(self._landing)
        return landed


def consenting(
    voice: RecordingTts,
    store: StoredThreads,
    kept: Kept | None = None,
    recap: str = RECAP,
    after: str = "What would you like to pick up?",
) -> tuple[DeviceSession, ScriptedLlm]:
    """A session whose next reply consents to a recap.

    Three rounds, which is what a consent reply is made of: the tool
    call, the summarization the runtime runs against the same provider,
    and the round seeded on the other side of the move.
    """
    poet = ScriptedLlm(
        [
            [call("resume_conversation", conversation=GALAXY, start_from="recap")],
            recap,
            after,
        ]
    )
    session = speaking_session(poet, voice, store, kept)
    _offer(session, "poet", GALAXY)
    _asked(session, "poet", GALAXY)
    return session, poet


def speaking_session(
    poet: ScriptedLlm,
    voice: RecordingTts,
    store: StoredThreads,
    kept: Kept | None = None,
    config: Config | None = None,
) -> DeviceSession:
    """A session that really speaks: the reply's own synthesis path is
    what the recap travels on, so nothing about it is stubbed out except
    the socket at the far end."""
    session = session_for(
        config if config is not None else resuming(),
        POET_MAC,
        {"poet": poet},
        conversations=kept,
        threads=store,
        stages={"tts": cast(Any, voice)},
    )
    with_device(session, POET_MAC)
    session.websocket = cast(Any, _Quiet())
    session.send_audio = _nothing  # type: ignore[method-assign]
    return session


# The offer, and the two answers to it


async def test_a_long_thread_offers_the_choice_and_moves_nothing() -> None:
    poet = ScriptedLlm(
        [[call("resume_conversation", conversation=GALAXY)], "Which would you like?"]
    )
    kept = Kept()
    session = speaking_session(poet, RecordingTts(), a_long_thread(), kept)
    _offer(session, "poet", GALAXY)
    thread = talking_thread(session)

    await drive_reply(session, UTTERANCE)

    assert _results(poet) == [builtin.TOO_LONG_TO_RESUME_WHOLE]
    assert talking_thread(session) == thread
    assert kept.milestones == []


async def test_the_recent_half_of_the_choice_resumes_the_tail_and_stores_nothing() -> None:
    poet = ScriptedLlm(
        [
            [call("resume_conversation", conversation=GALAXY, start_from="recent")],
            "Carrying on.",
        ]
    )
    kept = Kept()
    session = speaking_session(poet, RecordingTts(), a_long_thread(), kept)
    _offer(session, "poet", GALAXY)
    _asked(session, "poet", GALAXY)

    await drive_reply(session, UTTERANCE)

    assert talking_thread(session) == GALAXY
    (turns, _, _) = poet.seen[-1]
    assert turns[-1].content.endswith(pipeline_module.RESUMED_FROM_RECENT)
    assert kept.milestones == []


async def test_a_start_from_nobody_was_offered_is_refused() -> None:
    """The enforcement finding 5 asked for, at its second argument: a
    model cannot consent on the user's behalf to a recap the tool never
    offered."""
    poet = ScriptedLlm(
        [
            [call("resume_conversation", conversation=GALAXY, start_from="recap")],
            "Let me ask first.",
        ]
    )
    kept = Kept()
    session = speaking_session(poet, RecordingTts(), a_long_thread(), kept)
    # Offered the thread, never asked the question about it.
    _offer(session, "poet", GALAXY)
    thread = talking_thread(session)

    await drive_reply(session, UTTERANCE)

    assert _results(poet) == [builtin.NO_CHOICE_OFFERED]
    assert _errors(poet) == [False]
    assert talking_thread(session) == thread
    assert kept.milestones == []


async def test_a_start_from_naming_no_conversation_is_not_a_selection_at_all() -> None:
    """A call that names no thread is not a move, whatever else it
    carries, so it reaches the dispatch like any other read and is
    answered by the sentence that asks for one of the two arguments."""
    poet = ScriptedLlm(
        [
            [call("resume_conversation", start_from="recap")],
            "Which conversation did you mean?",
        ]
    )
    kept = Kept()
    session = speaking_session(poet, RecordingTts(), a_long_thread(), kept)
    thread = talking_thread(session)

    await drive_reply(session, UTTERANCE)

    assert _results(poet) == [builtin.RESUME_NEEDS_AN_ARGUMENT]
    assert talking_thread(session) == thread
    assert kept.milestones == []


async def test_a_start_from_outside_the_two_is_refused() -> None:
    poet = ScriptedLlm(
        [
            [call("resume_conversation", conversation=GALAXY, start_from="everything")],
            "Which did you mean?",
        ]
    )
    session = speaking_session(poet, RecordingTts(), a_long_thread(), Kept())
    _offer(session, "poet", GALAXY)
    _asked(session, "poet", GALAXY)

    await drive_reply(session, UTTERANCE)

    assert _results(poet) == [builtin.UNKNOWN_START]


# The consent, and the ordering it rests on


async def test_the_recap_is_spoken_verbatim_and_stored_after_it_is_heard() -> None:
    """The whole promise in one case. The summarizer's text reaches the
    voice byte for byte, and the checkpoint reaches the store only once
    the voice has finished saying it."""
    voice = RecordingTts()
    kept = Kept().watching(voice)
    session, poet = consenting(voice, a_long_thread(), kept)

    await drive_reply(session, UTTERANCE)

    assert voice.asked[0] == RECAP
    (record,) = kept.milestones
    assert record.text == RECAP
    assert record.text == voice.asked[0]
    # Stored after playback: the voice had already finished the recap by
    # the time the store was handed it.
    assert kept.heard_by_then == [(RECAP,)]
    assert talking_thread(session) == GALAXY


async def test_the_consent_turn_records_the_recap_as_its_reply() -> None:
    """The turn is an ordinary turn on the thread the reply began on,
    and what the user heard is what it says was replied."""
    voice = RecordingTts()
    kept = Kept().watching(voice)
    session, _ = consenting(voice, a_long_thread(), kept)
    origin = talking_thread(session)

    await drive_reply(session, UTTERANCE)

    (record,) = kept.turns
    assert record.conversation == origin
    assert RECAP in (record.reply or "")


async def test_the_round_after_the_recap_is_told_not_to_say_it_again() -> None:
    voice = RecordingTts()
    session, poet = consenting(voice, a_long_thread())

    await drive_reply(session, UTTERANCE)

    (turns, _, _) = poet.seen[-1]
    assert turns[-1].content.startswith(pipeline_module.RECAPPED_GREETING)
    # The context installed is the checkpoint standing for the thread,
    # not the thread.
    assert RECAP in turns[0].content
    assert not any("utterance 0" in turn.content for turn in turns)


async def test_the_checkpoint_landing_is_announced_and_carries_no_words(
    caplog: pytest.LogCaptureFixture,
) -> None:
    voice = RecordingTts()
    kept = Kept().watching(voice)
    session, _ = consenting(voice, a_long_thread(), kept)
    tap = Tap()
    events_of(session).attach(tap)

    with caplog.at_level(logging.INFO):
        await drive_reply(session, UTTERANCE)

    recorded = fields_of(only(caplog, "milestone_recorded"))
    assert recorded["conversation"] == GALAXY
    assert RECAP not in repr(recorded)
    assert [one.payload["conversation"] for one in tap.of("milestone_recorded")] == [
        GALAXY
    ]


async def test_a_store_that_did_not_land_it_says_nothing_and_re_offers() -> None:
    """A checkpoint that was not written is a checkpoint the next resume
    knows nothing about, so the choice is offered again. The session
    keeps the context it installed, because the user did hear the
    recap."""
    voice = RecordingTts()
    kept = Kept(landing=False).watching(voice)
    store = a_long_thread()
    session, _ = consenting(voice, store, kept)
    tap = Tap()
    events_of(session).attach(tap)

    await drive_reply(session, UTTERANCE)

    assert talking_thread(session) == GALAXY
    assert tap.of("milestone_recorded") == []


# Everything that can go wrong before the user has heard it


async def test_a_voice_that_broke_mid_recap_stores_nothing() -> None:
    voice = FailingTts()
    kept = Kept().watching(voice)
    session, _ = consenting(voice, a_long_thread(), kept)
    thread = talking_thread(session)

    await drive_reply(session, UTTERANCE)

    assert voice.asked == [RECAP]
    assert voice.finished == []
    assert kept.milestones == []
    # The move never happened either: the reply ended where the voice
    # did, and the thread is the one it began on.
    assert talking_thread(session) == thread


async def test_a_device_that_left_mid_recap_stores_nothing() -> None:
    voice = RecordingTts()
    kept = Kept().watching(voice)
    session, _ = consenting(voice, a_long_thread(), kept)
    thread = talking_thread(session)
    session.send_audio = _gone  # type: ignore[method-assign]

    await drive_reply(session, UTTERANCE)

    assert kept.milestones == []
    assert talking_thread(session) == thread


async def test_a_barge_in_mid_recap_stores_nothing_and_the_next_resume_re_offers() -> None:
    """The ordering seen from the one direction that matters most: the
    user cut the recap off, so they did not hear it, so it is not
    stored, so the thread is unchanged and asking again asks the same
    question."""
    voice = HeldTts()
    kept = Kept().watching(voice)
    store = a_long_thread()
    session, _ = consenting(voice, store, kept)
    thread = talking_thread(session)

    session.runtime.start_reply(UTTERANCE)
    await asyncio.wait_for(voice.speaking.wait(), timeout=5.0)
    await session.runtime.cancel_reply()
    voice.release.set()

    # The recap was being spoken and was never finished, so it was
    # never stored and nothing moved.
    assert voice.asked == [RECAP]
    assert voice.finished == []
    assert kept.milestones == []
    assert talking_thread(session) == thread

    asking = ScriptedLlm(
        [[call("resume_conversation", conversation=GALAXY)], "Which would you like?"]
    )
    again = speaking_session(asking, RecordingTts(), store, kept)
    _offer(again, "poet", GALAXY)
    await drive_reply(again, UTTERANCE)

    assert _results(asking) == [builtin.TOO_LONG_TO_RESUME_WHOLE]


async def test_a_summarizer_that_failed_falls_back_and_stores_nothing() -> None:
    """Not a failed resume. The thread is picked up from its recent
    tail, the seeded round is told the recap could not be made, and
    nothing is written, so the next resume offers the choice again."""
    voice = RecordingTts()
    kept = Kept().watching(voice)
    poet = ScriptedLlm(
        [
            [call("resume_conversation", conversation=GALAXY, start_from="recap")],
            # The summarization round answers nothing at all, which is
            # the shape a provider that produced no text leaves.
            "",
            "Carrying on.",
        ]
    )
    session = speaking_session(poet, voice, a_long_thread(), kept)
    _offer(session, "poet", GALAXY)
    _asked(session, "poet", GALAXY)

    await drive_reply(session, UTTERANCE)

    assert kept.milestones == []
    assert talking_thread(session) == GALAXY
    (turns, _, _) = poet.seen[-1]
    assert turns[-1].content.endswith(pipeline_module.RECAP_UNAVAILABLE)


async def test_a_summarization_round_that_ran_long_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pipeline_module, "RECAP_ROUND_TIMEOUT_S", 0.01)
    voice = RecordingTts()
    kept = Kept().watching(voice)
    poet = _SlowSummarizer(
        [
            [call("resume_conversation", conversation=GALAXY, start_from="recap")],
            RECAP,
            "Carrying on.",
        ]
    )
    session = speaking_session(cast(Any, poet), voice, a_long_thread(), kept)
    _offer(session, "poet", GALAXY)
    _asked(session, "poet", GALAXY)

    await drive_reply(session, UTTERANCE)

    assert kept.milestones == []
    assert talking_thread(session) == GALAXY


# What a recap is allowed to claim


async def test_a_backlog_wider_than_the_recap_budget_records_its_true_first_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The finding this column exists for. The summarizer read the
    newest part of a long thread, so the checkpoint records where its
    reading really began, and nothing later can read it as covering the
    turns it never saw."""
    monkeypatch.setattr(resumption_module, "RECAP_INPUT_BUDGET_TOKENS", 300)
    voice = RecordingTts()
    kept = Kept().watching(voice)
    session, poet = consenting(voice, a_long_thread(count=8), kept)

    await drive_reply(session, UTTERANCE)

    (record,) = kept.milestones
    # The turns are numbered from one, so a checkpoint that began in the
    # middle is one whose reading was truncated.
    assert record.from_turn > 1
    assert record.after_turn == 8
    # And what it claims is exactly what the summarizer was shown: the
    # oldest turn it read is in that round's context and the one before
    # it is not.
    (summarizing, _, _) = poet.seen[1]
    shown = " ".join(turn.content for turn in summarizing)
    assert f"utterance {record.from_turn - 1} " in shown
    assert f"utterance {record.from_turn - 2} " not in shown


async def test_a_recap_of_a_recap_records_the_one_it_consumed() -> None:
    """`parent` is provenance rather than navigation: content that
    reached this checkpoint only through an earlier one is still this
    checkpoint's content, which is what makes erasing it transitive."""
    voice = RecordingTts()
    kept = Kept().watching(voice)
    earlier = a_milestone(text="we had talked about galaxies", id=41, after_turn=4)
    store = StoredThreads(
        held={
            GALAXY: a_backlog(
                GALAXY,
                said=[
                    (f"utterance {index} " * 40, f"reply {index} " * 40)
                    for index in range(5, 9)
                ],
                milestone=earlier,
                first_id=5,
            )
        }
    )
    session, poet = consenting(voice, store, kept)

    await drive_reply(session, UTTERANCE)

    (record,) = kept.milestones
    assert record.parent == 41
    assert (record.from_turn, record.after_turn) == (5, 8)
    # And the earlier recap was part of what was summarized.
    (summarizing, _, _) = poet.seen[1]
    assert any("we had talked about galaxies" in turn.content for turn in summarizing)


# The no-leak sentinel


async def test_the_recap_reaches_the_store_and_no_telemetry_surface(
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A recap is a summary of what a room said, so it is conversation
    content: it belongs in the column and in the voice, and on no
    decision track, in no log record and on no stream."""
    sentinel = "sk-live-51characters-of-nobodys-business"
    voice = RecordingTts()
    kept = Kept().watching(voice)
    session, _ = consenting(
        voice, a_long_thread(), kept, recap=f"We talked about {sentinel} at length."
    )
    tap = Tap()
    events_of(session).attach(tap)

    with caplog.at_level(logging.DEBUG):
        await drive_reply(session, UTTERANCE)

    (record,) = kept.milestones
    assert sentinel in (record.text or "")
    assert voice.asked[0] == record.text
    assert not any(sentinel in repr(one.payload) for one in tap.seen)
    assert sentinel not in both_formats(caplog)
    printed = capsys.readouterr()
    assert sentinel not in printed.out + printed.err


class _SlowSummarizer(ScriptedLlm):
    """A model whose second round, which is the summarization one, takes
    longer than the bound allows."""

    async def stream(self, system: str, turns: Any, tools: Any = (), tool_choice: Any = "auto"):
        if system == pipeline_module.RECAP_INSTRUCTION:
            await asyncio.sleep(5.0)
        async for event in super().stream(system, turns, tools, tool_choice):
            yield event


class Tap:
    """A consumer of this session's events, kept because what a recap
    must never reach is a decision track."""

    def __init__(self) -> None:
        self.seen: list[Any] = []

    def emit(self, emission: Any) -> None:
        self.seen.append(emission)

    def of(self, name: str) -> list[Any]:
        return [one for one in self.seen if one.payload.get("event") == name]


class _Quiet:
    async def send_text(self, text: str) -> None:
        return None


async def _nothing(*args: object, **kwargs: object) -> None:
    return None


async def _gone(*args: object, **kwargs: object) -> None:
    raise DeviceGone("the device went away")


def _results(script: ScriptedLlm) -> list[str]:
    return [
        result.content
        for turns, _, _ in script.seen
        for turn in turns
        for result in turn.tool_results
    ]


def _errors(script: ScriptedLlm) -> list[bool]:
    return [
        result.is_error
        for turns, _, _ in script.seen
        for turn in turns
        for result in turn.tool_results
    ]


def _offer(session: DeviceSession, agent: str, *conversations: str) -> None:
    """Say that this agent was offered these threads, without driving a
    search for it. White-box for the reason the suite next door states:
    an offer is made by answering a model, and every case here is about
    what happens after one."""
    flow = session.runtime._resumption
    assert flow is not None
    flow._offered[agent] = conversations


def _asked(session: DeviceSession, agent: str, conversation: str) -> None:
    """Say that this agent has asked the user which way to pick this
    thread up, which is what a `start_from` is an answer to. The
    production surface for it is `offer_choice`, driven end to end by
    the first case in this file; the rest are about the answer."""
    flow = session.runtime._resumption
    assert flow is not None
    flow.offer_choice(agent, conversation)
