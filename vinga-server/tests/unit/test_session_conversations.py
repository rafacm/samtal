"""Finding a past conversation and moving onto it, from inside a reply.

The three things this file is about are the three the flow can get
wrong. Which thread the session ends up on, which is state the runtime
owns and the store is only asked about. What the model is allowed to
pick, which is what this agent was offered and nothing else, an
utterance later included. And what a failure says, which is a fixed
sentence with nothing in it that a room or a driver wrote.

Everything is driven through the session drivers every other suite uses
and read where a suite can read it: the thread the next turn will be
recorded on, the turns the next round was handed, and the events the
session emitted. The store behind the seam is written down rather than
migrated, because none of what is under test here is SQL: the reads
themselves have their suite next door in `test_conversations_threads.py`,
and the sanitizing has its own case at the end of this one, against a
store that raises.
"""

import logging
import threading
from typing import Any, cast

import pytest

import vinga_server.conversations.threads as threads_module
from tests.support.configs import BOTH_MAC, POET_MAC, base_config
from tests.support.providers import ScriptedLlm
from tests.support.sessions import (
    call,
    drive_reply,
    events_of,
    run_reply,
    session_for,
    talking,
    talking_thread,
    with_device,
)
from tests.support.stores import StoredThreads, a_backlog, a_candidate
from vinga_server.config import Config
from vinga_server.config.models import DatabaseConfig
from vinga_server.conversations import threads
from vinga_server.conversations.records import Acknowledgement, TurnRecord
from vinga_server.device.session import DeviceSession
from vinga_server.runtime import pipeline as pipeline_module
from vinga_server.tools import builtin

# Two thread ids in the shape the runtime mints, written down so a test
# can say which conversation it means.
GALAXY = "1f0c1d2e3a4b5c6d7e8f90a1b2c3d4e5"
RECIPE = "2f0c1d2e3a4b5c6d7e8f90a1b2c3d4e5"

# One frame of silence, which the mock ASR answers with its configured
# transcript.
UTTERANCE = b"\x00\x00" * 320


def resuming(**overrides: object) -> Config:
    """The suite's configuration: recording on, resumption on. What the
    store actually holds is the seam's business, not this section's."""
    return base_config(
        server={"conversations": {"enabled": True, "resumption": True}}, **overrides
    )


def a_session(
    script: Any,
    threads: Any = None,
    config: Config | None = None,
    mac: str = POET_MAC,
    scripts: dict[str, Any] | None = None,
) -> DeviceSession:
    return session_for(
        config if config is not None else resuming(),
        mac,
        scripts if scripts is not None else {"poet": script},
        threads=threads,
    )


def found(*conversations: str, matched: bool = True, agent: str = "poet") -> StoredThreads:
    """A store that answers one agent's search with these threads, and
    holds each of them with one turn."""
    return StoredThreads(
        found={
            agent: threads.Candidates(
                matched=matched,
                found=tuple(a_candidate(one) for one in conversations),
            )
        },
        held={
            one: a_backlog(one, agent=agent, said=[("what is out there", "Galaxies.")])
            for one in conversations
        },
    )


def results_of(script: ScriptedLlm) -> list[str]:
    """Every tool result this model was handed, in order."""
    return [
        result.content
        for turns, _, _ in script.seen
        for turn in turns
        for result in turn.tool_results
    ]


def errors_of(script: ScriptedLlm) -> list[bool]:
    return [
        result.is_error
        for turns, _, _ in script.seen
        for turn in turns
        for result in turn.tool_results
    ]


# What a server that cannot resume anything answers


async def test_a_search_is_refused_where_the_deployment_did_not_ask_for_it() -> None:
    """Decision 11: off, the tools stay offered and answer a sentence
    the agent reads out. An error result would make the model apologize
    for a fault; this is not a fault."""
    poet = ScriptedLlm(
        [[call("resume_conversation", description="the galaxy")], "I cannot do that."]
    )
    session = a_session(poet, threads=StoredThreads(), config=base_config())
    thread = talking_thread(session)

    assert await run_reply(session, "what were we saying about galaxies") == [
        "I cannot do that."
    ]

    assert results_of(poet) == [builtin.RESUMPTION_UNAVAILABLE]
    assert errors_of(poet) == [False]
    assert talking_thread(session) == thread


async def test_a_fresh_conversation_is_refused_where_resumption_is_off() -> None:
    """The same prerequisite for both tools, which is what keeps the
    off state one behaviour: nothing moves, and the session is exactly
    the session-scoped conversation it was."""
    poet = ScriptedLlm([[call("new_conversation")], "I cannot do that."])
    session = a_session(poet, threads=StoredThreads(), config=base_config())
    thread = talking_thread(session)
    await run_reply(session, "let us start again")

    assert await run_reply(session, "and again") == ["I cannot do that."]

    assert results_of(poet) == [builtin.RESUMPTION_UNAVAILABLE]
    assert errors_of(poet) == [False]
    assert talking_thread(session) == thread
    # And the history is the one it was: the refusal moved nothing.
    assert [turn.content for turn in poet.seen[-1][0]][0] == "let us start again"


async def test_the_tools_are_offered_whether_or_not_anything_can_be_resumed() -> None:
    off = ScriptedLlm(["Nothing."])
    on = ScriptedLlm(["Nothing."])
    await run_reply(a_session(off, config=base_config()), "hello")
    await run_reply(a_session(on, threads=StoredThreads()), "hello")

    for script in (off, on):
        offered = [tool.name for tool in script.seen[0][1]]
        assert "new_conversation" in offered and "resume_conversation" in offered


# Discovery


async def test_a_description_answers_the_threads_to_read_out() -> None:
    poet = ScriptedLlm(
        [[call("resume_conversation", description="the galaxy")], "Two of them, then."]
    )
    store = found(GALAXY, RECIPE)
    session = a_session(poet, threads=store)

    await run_reply(session, "what were we saying about the galaxy")

    (answer,) = results_of(poet)
    assert answer.startswith(builtin.CANDIDATES_FOUND)
    assert GALAXY in answer and RECIPE in answer
    # Numbered, because the number is what a user answers with.
    assert "1. conversation" in answer and "2. conversation" in answer
    assert store.asked == [("poet", "the galaxy")]


async def test_nothing_matching_says_so_and_still_offers_something() -> None:
    poet = ScriptedLlm([[call("resume_conversation", description="the moon")], "Not that."])
    session = a_session(poet, threads=found(GALAXY, matched=False))

    await run_reply(session, "the moon thing")

    (answer,) = results_of(poet)
    assert answer.startswith(builtin.CANDIDATES_UNMATCHED)
    assert GALAXY in answer


async def test_an_agent_with_no_stored_threads_is_told_so() -> None:
    poet = ScriptedLlm([[call("resume_conversation", description="anything")], "Nothing yet."])
    session = a_session(poet, threads=StoredThreads())

    await run_reply(session, "anything from before")

    assert results_of(poet) == [builtin.NOTHING_TO_RESUME]


async def test_the_tool_says_what_it_needs_when_it_was_given_neither() -> None:
    poet = ScriptedLlm([[call("resume_conversation")], "Let me ask."])
    session = a_session(poet, threads=found(GALAXY))

    await run_reply(session, "go back")

    assert results_of(poet) == [builtin.RESUME_NEEDS_AN_ARGUMENT]


# Selection


async def test_picking_a_thread_installs_its_history_and_rebinds() -> None:
    poet = ScriptedLlm(
        [
            [call("resume_conversation", description="the galaxy")],
            [call("resume_conversation", conversation=GALAXY)],
            "Right, we were talking about galaxies.",
        ]
    )
    store = StoredThreads(
        found={"poet": threads.Candidates(matched=True, found=(a_candidate(GALAXY),))},
        held={
            GALAXY: a_backlog(
                GALAXY, said=[("what is out there", "Galaxies, mostly.")]
            )
        },
    )
    session = a_session(poet, threads=store)

    spoken = await run_reply(session, "the galaxy conversation")

    assert spoken == ["Right, we were talking about galaxies."]
    assert talking_thread(session) == GALAXY
    # The round after the move was written against the resumed thread:
    # the stored dialogue, then the seed that opened the new round.
    (turns, _, _) = poet.seen[-1]
    assert [turn.content for turn in turns[:2]] == [
        "what is out there",
        "Galaxies, mostly.",
    ]
    assert turns[-1].content == pipeline_module.RESUMED_GREETING
    assert store.read == [GALAXY]


async def test_the_resumed_thread_is_the_one_the_next_turn_is_recorded_on() -> None:
    poet = ScriptedLlm(
        [[call("resume_conversation", conversation=GALAXY)], "Carrying on."]
    )
    session = a_session(poet, threads=found(GALAXY))
    _offer(session, "poet", GALAXY)

    await run_reply(session, "that one")

    assert talking_thread(session) == GALAXY


async def test_resuming_emits_what_it_rebuilt(caplog: pytest.LogCaptureFixture) -> None:
    poet = ScriptedLlm(
        [[call("resume_conversation", conversation=GALAXY)], "Carrying on."]
    )
    store = StoredThreads(
        held={
            GALAXY: a_backlog(
                GALAXY,
                said=[("what is out there", "Galaxies."), ("and", "More galaxies.")],
            )
        }
    )
    session = a_session(poet, threads=store)
    _offer(session, "poet", GALAXY)

    with caplog.at_level("INFO"):
        await run_reply(session, "that one")

    (resumed,) = [
        record
        for record in caplog.records
        if getattr(record, "event", "") == "conversation_resumed"
    ]
    assert resumed.conversation == GALAXY
    assert (resumed.turns, resumed.skipped, resumed.over_budget) == (2, 0, False)
    # Counts and a flag: what was said on the thread is the store's.
    assert "what is out there" not in resumed.getMessage()


async def test_the_user_picks_one_an_utterance_later() -> None:
    """The flow the state exists for. A tool result lives inside one
    reply, so "the second one" arriving on the next utterance can only
    be resolved against something the runtime kept."""
    poet = ScriptedLlm(
        [
            [call("resume_conversation", description="the galaxy")],
            "The galaxy one or the recipe one?",
            [call("resume_conversation", conversation=RECIPE)],
            "The recipe it is.",
        ]
    )
    session = a_session(poet, threads=found(GALAXY, RECIPE))

    await run_reply(session, "something we talked about before")
    assert talking_thread(session) != RECIPE

    assert await run_reply(session, "the second one") == ["The recipe it is."]
    assert talking_thread(session) == RECIPE


async def test_an_id_nobody_offered_is_refused_without_asking_the_store() -> None:
    poet = ScriptedLlm(
        [[call("resume_conversation", conversation=GALAXY)], "I cannot find that."]
    )
    store = found(GALAXY)
    session = a_session(poet, threads=store)
    thread = talking_thread(session)

    await run_reply(session, "resume the galaxy one")

    assert results_of(poet) == [builtin.NO_SUCH_CANDIDATE]
    assert errors_of(poet) == [False]
    assert talking_thread(session) == thread
    # An id a model invented is not even a query.
    assert store.read == []


async def test_an_id_offered_to_another_agent_is_refused() -> None:
    """Threads are agent-scoped, and the offer is too: the tutor may not
    pick up what the poet was shown, in this session or any other."""
    poet = ScriptedLlm([[call("resume_conversation", description="the galaxy")], "Two."])
    tutor = ScriptedLlm(
        [[call("resume_conversation", conversation=GALAXY)], "Not mine to open."]
    )
    session = a_session(
        None,
        threads=found(GALAXY),
        config=resuming(),
        mac=BOTH_MAC,
        scripts={"poet": poet, "tutor": tutor},
    )
    await run_reply(session, "the galaxy one")
    session.runtime._activate_agent("tutor")

    await run_reply(session, "and you, open it")

    assert results_of(tutor) == [builtin.NO_SUCH_CANDIDATE]


async def test_an_id_from_before_a_newer_search_is_refused() -> None:
    poet = ScriptedLlm(
        [
            [call("resume_conversation", description="the galaxy")],
            "Which one?",
            [call("resume_conversation", description="the recipe")],
            "Which one?",
            [call("resume_conversation", conversation=GALAXY)],
            "I cannot find that.",
        ]
    )
    store = StoredThreads(
        found={"poet": threads.Candidates(matched=True, found=(a_candidate(GALAXY),))},
        held={GALAXY: a_backlog(GALAXY)},
    )
    session = a_session(poet, threads=store)
    await run_reply(session, "the galaxy one")
    # The second search answers with the recipe alone, which is what
    # makes the galaxy stale rather than merely older.
    store.found["poet"] = threads.Candidates(
        matched=True, found=(a_candidate(RECIPE),)
    )
    await run_reply(session, "no, the recipe one")

    await run_reply(session, "the galaxy after all")

    assert results_of(poet)[-1] == builtin.NO_SUCH_CANDIDATE
    assert talking_thread(session) != GALAXY


async def test_a_thread_that_is_gone_is_refused_in_its_own_words() -> None:
    poet = ScriptedLlm(
        [[call("resume_conversation", conversation=GALAXY)], "It is not there."]
    )
    session = a_session(poet, threads=StoredThreads())
    _offer(session, "poet", GALAXY)

    await run_reply(session, "that one")

    assert results_of(poet) == [builtin.CONVERSATION_GONE]
    assert errors_of(poet) == [False]


# What a resumed round is told about the record it got


async def test_a_thread_longer_than_the_budget_resumes_from_its_tail() -> None:
    poet = ScriptedLlm(
        [[call("resume_conversation", conversation=GALAXY)], "Carrying on."]
    )
    store = StoredThreads(
        held={
            GALAXY: a_backlog(
                GALAXY,
                said=[(f"utterance {index}" * 40, "Yes." * 40) for index in range(6)],
            )
        }
    )
    session = a_session(poet, threads=store, config=resuming_with(budget=512))
    _offer(session, "poet", GALAXY)

    await run_reply(session, "that one")

    (turns, _, _) = poet.seen[-1]
    assert turns[-1].content.endswith(pipeline_module.RESUMED_FROM_RECENT)
    # The tail, not the whole of it.
    assert len([turn for turn in turns if turn.role == "user"]) < 6


async def test_a_record_with_holes_in_it_is_a_caveat_and_not_a_refusal() -> None:
    poet = ScriptedLlm(
        [[call("resume_conversation", conversation=GALAXY)], "Carrying on."]
    )
    store = StoredThreads(
        held={
            GALAXY: a_backlog(
                GALAXY, said=[("what is out there", "Galaxies.")], incomplete=True
            )
        }
    )
    session = a_session(poet, threads=store)
    _offer(session, "poet", GALAXY)

    await run_reply(session, "that one")

    assert talking_thread(session) == GALAXY
    (turns, _, _) = poet.seen[-1]
    assert turns[-1].content.endswith(pipeline_module.RESUMED_WITH_GAPS)


# Starting fresh, and the latch both moves share


async def test_a_new_conversation_leaves_the_thread_and_its_words_behind() -> None:
    poet = ScriptedLlm(["Noted.", [call("new_conversation")], "New topic, then."])
    session = a_session(poet, threads=StoredThreads())
    await run_reply(session, "we were talking about the galaxy")
    before = talking_thread(session)

    assert await run_reply(session, "let us talk about something else") == [
        "New topic, then."
    ]

    assert talking_thread(session) not in (None, before)
    (turns, _, _) = poet.seen[-1]
    assert [turn.content for turn in turns] == [pipeline_module.FRESH_GREETING]


async def test_two_moves_in_one_round_honour_the_first() -> None:
    """The precedence is the order the model issued them in, whichever
    kinds they were: the first is made and the rest are not."""
    poet = ScriptedLlm(
        [
            [
                call("new_conversation"),
                call("resume_conversation", conversation=GALAXY),
            ],
            "One thing at a time.",
        ]
    )
    session = a_session(poet, threads=found(GALAXY))
    _offer(session, "poet", GALAXY)
    before = talking_thread(session)

    await run_reply(session, "start again and go back")

    assert talking_thread(session) not in (None, before, GALAXY)


async def test_a_second_move_later_in_the_same_reply_is_refused() -> None:
    """The latch a handover already had, now shared: one move per reply,
    and the round on the other side of the first is told so in words it
    can say out loud."""
    poet = ScriptedLlm(
        [
            [call("new_conversation")],
            [call("resume_conversation", conversation=GALAXY)],
            "One thing at a time.",
        ]
    )
    session = a_session(poet, threads=found(GALAXY))
    _offer(session, "poet", GALAXY)

    assert await run_reply(session, "start again and go back") == [
        "One thing at a time."
    ]

    assert talking_thread(session) != GALAXY
    assert results_of(poet) == [builtin.ALREADY_MOVED]
    assert errors_of(poet) == [False]


async def test_a_handover_takes_the_offer_with_it() -> None:
    """An offer belongs to the conversation it was made in, and a
    handover ends that conversation."""
    poet = ScriptedLlm(
        [
            [call("resume_conversation", description="the galaxy")],
            [call("switch_agent", agent="tutor")],
        ]
    )
    tutor = ScriptedLlm(
        [
            "Tutor here.",
            [call("resume_conversation", conversation=GALAXY)],
            "I cannot find that.",
        ]
    )
    session = a_session(
        None,
        threads=found(GALAXY),
        mac=BOTH_MAC,
        scripts={"poet": poet, "tutor": tutor},
    )
    await run_reply(session, "the galaxy one, and get me the tutor")

    await run_reply(session, "open it")

    assert results_of(tutor)[-1] == builtin.NO_SUCH_CANDIDATE


# The clean switch, which is the same per-thread history seen from the
# handover side


async def test_the_incoming_agent_never_receives_the_outgoing_ones_words() -> None:
    """The behaviour #190 changed. What was said to the poet is on the
    poet's thread, and the tutor's provider is never sent it, which is
    also what keeps a local agent's words off another agent's endpoint.
    """
    sentinel = "sk-live-51characters-of-nobodys-business"
    poet = ScriptedLlm([[call("switch_agent", agent="tutor")]])
    tutor = ScriptedLlm(["Tutor here."])
    session = a_session(
        None, mac=BOTH_MAC, scripts={"poet": poet, "tutor": tutor}
    )
    await run_reply(session, f"my key is {sentinel}")

    await run_reply(session, "get me the tutor")

    assert talking(session) == "tutor"
    assert not any(
        sentinel in turn.content for turns, _, _ in tutor.seen for turn in turns
    )


async def test_switching_back_returns_to_the_agents_own_thread() -> None:
    poet = ScriptedLlm(
        [[call("switch_agent", agent="tutor")], "Back with you.", "Back with you."]
    )
    tutor = ScriptedLlm(["Tutor here.", [call("switch_agent", agent="poet")]])
    session = a_session(
        None, mac=BOTH_MAC, scripts={"poet": poet, "tutor": tutor}
    )
    await run_reply(session, "the tutor please")
    first = talking_thread(session)

    await run_reply(session, "back to the poet")

    assert talking(session) == "poet"
    assert talking_thread(session) != first
    # The poet's own thread, with what it said on it before the switch.
    (turns, _, _) = poet.seen[-1]
    assert turns[0].content == "the tutor please"


# What a store that will not answer may say


async def test_a_store_that_cannot_be_read_says_so_and_moves_nothing() -> None:
    poet = ScriptedLlm(
        [[call("resume_conversation", description="the galaxy")], "Not right now."]
    )
    session = a_session(
        poet, threads=StoredThreads(failure=threads.Unreadable(busy=False))
    )
    thread = talking_thread(session)

    await run_reply(session, "the galaxy one")

    assert results_of(poet) == [builtin.STORE_UNREADABLE]
    assert talking_thread(session) == thread


async def test_a_busy_store_says_to_ask_again() -> None:
    poet = ScriptedLlm(
        [[call("resume_conversation", description="the galaxy")], "In a moment."]
    )
    session = a_session(poet, threads=StoredThreads(failure=threads.Unreadable(busy=True)))

    await run_reply(session, "the galaxy one")

    assert results_of(poet) == [builtin.STORE_BUSY]


async def test_a_poisoned_driver_message_reaches_nothing(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The seam is a boundary, not a pass-through. A driver quotes the
    DSN it could not connect with, and a tool result is both
    model-visible and stored, so a read that raised through the loop
    would put a credential in front of a model, into the record and onto
    every surface the record touches.

    Driven through the real `threads.Reads`, whose engine is made to
    raise: the double next door cannot prove this, because what is under
    test is the catching. Driven as a whole reply, because the stored
    invocation row is one of the surfaces the message must not reach and
    a reply is what produces one.
    """
    sentinel = "postgresql://vinga:hunter2@db.internal:5432/vinga"

    class Raising:
        def connect(self) -> Any:
            raise RuntimeError(f"connection to {sentinel} failed")

        def dispose(self) -> None:
            return None

    monkeypatch.setattr(threads_module, "read_engine", lambda _settings: Raising())
    poet = ScriptedLlm(
        [[call("resume_conversation", description="the galaxy")], "Not right now."]
    )
    kept = Recorder()
    session = session_for(
        resuming(),
        POET_MAC,
        {"poet": poet},
        conversations=kept,
        threads=threads.Reads(DatabaseConfig()),
    )
    with_device(session, POET_MAC)
    session.websocket = cast(Any, _Quiet())
    session.send_audio = _nothing  # type: ignore[method-assign]
    session.runtime._speak = _spoken  # type: ignore[method-assign]
    seen: list[Any] = []
    events_of(session).attach(seen.append)

    with caplog.at_level(logging.DEBUG):
        await drive_reply(session, UTTERANCE)

    # What the model was told, which is the fixed sentence and nothing
    # of the driver's.
    assert results_of(poet) == [builtin.STORE_UNREADABLE]
    assert not any(
        sentinel in turn.content for turns, _, _ in poet.seen for turn in turns
    )
    # What the store was handed, which is the same sentence in the row.
    (record,) = kept.records
    assert record.reply == "Not right now."
    assert [invocation.result for invocation in record.tools] == [
        builtin.STORE_UNREADABLE
    ]
    assert sentinel not in repr(record)
    # And every telemetry surface: the tap, the log records in both
    # their renderings, and the streams the process writes.
    assert not any(sentinel in repr(vars(emission)) for emission in seen)
    for line in caplog.records:
        assert sentinel not in line.getMessage() + repr(line.args) + repr(vars(line))
    assert sentinel not in caplog.text
    printed = capsys.readouterr()
    assert sentinel not in printed.out + printed.err
    # The class name is what an operator gets, which is the rule the
    # reply path already applies to a provider that fails.
    assert any("RuntimeError" in line.getMessage() for line in caplog.records)


class Recorder:
    """Where the store would stand, keeping the records it is handed."""

    def __init__(self) -> None:
        self.records: list[TurnRecord] = []

    def record_turn(self, session_id: str, record: TurnRecord) -> None:
        self.records.append(record)


# Reading a thread the same session is still writing to


class LateStore:
    """A store whose acknowledgement settles a moment after the turn is
    handed over, which is what a queued write looks like from the reply
    that made it."""

    def __init__(self, delay_s: float = 0.05) -> None:
        self.delay_s = delay_s
        self.handles: list[Acknowledgement] = []

    def record_turn(self, session_id: str, record: TurnRecord) -> Acknowledgement:
        landed = Acknowledgement()
        self.handles.append(landed)
        threading.Timer(self.delay_s, lambda: landed.settle(True)).start()
        return landed


class WatchingThreads(StoredThreads):
    """The seam, plus what was true of the writer when it was asked."""

    def __init__(self, store: LateStore, **kept: Any) -> None:
        super().__init__(**kept)
        self._store = store
        self.settled: list[bool] = []

    def backlog(self, conversation: str) -> Any:
        self.settled.append(all(one.wait(0) for one in self._store.handles))
        return super().backlog(conversation)


async def test_a_resume_waits_for_the_turn_this_session_just_recorded() -> None:
    """Read-your-writes on a switch back inside one session. The turn
    that ended the first leg is still on the writer's queue while the
    resume reads the thread, and hydrating without it would rebuild the
    conversation one turn short of what the user just said.
    """
    poet = ScriptedLlm(
        [
            "Noted.",
            [call("resume_conversation", conversation=GALAXY)],
            "Carrying on.",
        ]
    )
    store = LateStore()
    seam = WatchingThreads(store, held={GALAXY: a_backlog(GALAXY)})
    session = session_for(
        resuming(), POET_MAC, {"poet": poet}, conversations=store, threads=seam
    )
    with_device(session, POET_MAC)
    session.websocket = cast(Any, _Quiet())
    session.send_audio = _nothing  # type: ignore[method-assign]
    session.runtime._speak = _spoken  # type: ignore[method-assign]
    # One recorded turn on the thread the session opened on, then the
    # resume, in the same session and with the writer still behind.
    await drive_reply(session, UTTERANCE)
    _offer(session, "poet", GALAXY)
    session.runtime._acknowledged[GALAXY] = store.handles[0]

    await drive_reply(session, UTTERANCE)

    assert seam.settled == [True]
    assert talking_thread(session) == GALAXY


class _Quiet:
    async def send_text(self, text: str) -> None:
        return None


async def _nothing(*args: object, **kwargs: object) -> None:
    return None


async def _spoken(synthesis: Any, resampler: Any, into: list[str]) -> None:
    synthesis.cancel()
    into.append(synthesis.sentence)


def resuming_with(budget: int) -> Config:
    return base_config(
        server={
            "conversations": {
                "enabled": True,
                "resumption": True,
                "resumption_budget_tokens": budget,
            }
        }
    )


def _offer(session: DeviceSession, agent: str, *conversations: str) -> None:
    """Say that this agent was offered these threads, without driving a
    search for it.

    White-box, deliberately, and once for the whole file. What the state
    is FOR has its own tests above, driven end to end through a search
    and a selection; every other case here is about what happens after
    an offer, and driving a search first would put the flow that is not
    under test in front of the one that is. There is no production
    surface for it either: an offer is made by answering a model, which
    is the search these tests are deliberately not repeating.
    """
    resumption = session.runtime._resumption
    assert resumption is not None
    resumption._offered[agent] = conversations

