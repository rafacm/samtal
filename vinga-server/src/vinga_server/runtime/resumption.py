"""Finding an earlier conversation, and picking it up again.

The flow between a model asking for a past thread and the runtime moving
onto one. Three things live here because they are one decision spread
over two utterances, and splitting them would put half of it in the tool
layer and half in the reply path:

- **What was offered.** A tool result exists only inside one reply's
  working list, so a model asked to "resume the second one" an utterance
  later has nothing to resolve it against but its own recollection. The
  offer is therefore kept here, per agent, and an id is honored only
  when this agent was offered it: an invented id, a stale one from
  before a newer search, and one offered to a different agent in the
  same session are all the same refusal.
- **What a thread becomes.** The store's rows, under the deployment's
  budget, through the hydrator. This module is where the two meet, so
  neither the tool nor the reply path has to know that a budget exists.
- **What a failure says.** Every read goes through the sanitized seam
  and is bounded here, so a slow database is a tool that answered a
  sentence rather than a reply that stalled. The sentences are the
  tools' own closed vocabulary, imported rather than restated.

- **What a long thread was offered.** A backlog wider than the budget
  is not swapped in; the user is asked whether to hear a recap of the
  whole of it or carry on from the recent part, and the one
  conversation awaiting that answer is kept here per agent, beside the
  offered ids and cleared by the same rules. A `start_from` for
  anything else is a recap nobody was asked about.
- **What a recap is made of.** The same rows under a budget of their
  own, plus the range they really covered and the ids inside it, which
  is what a checkpoint is allowed to claim and what the store checks
  that claim against when it finally writes one.

What is deliberately NOT here: minting an id, rebinding an agent to a
thread, installing a history, speaking a recap and storing one. Those
are the runtime's, because they are the transition and the reply, and
both happen at a turn boundary that only the reply path can see.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from vinga_server.conversations import hydration, threads
from vinga_server.conversations.records import StoredTurn
from vinga_server.providers import Turn
from vinga_server.tools import builtin

# How much of a thread a recap is allowed to read before it summarizes.
#
# Its own budget rather than the hydration one, and wider, because the
# two are spending on different things: hydration is buying the context
# a reply is generated against every round from here, and a recap is
# buying one round that happens once and is then stored as a paragraph.
# Bounded all the same, because a thread has no ceiling and a request
# does. Where a backlog is wider than this, the recap reads the newest
# of it and records that it did: the checkpoint's `from_turn` is the
# oldest turn it really saw and is inside its coverage, and everything
# below that is truncated rather than summarized.
RECAP_INPUT_BUDGET_TOKENS = 24000


class ThreadReads(Protocol):
    """The store, as this flow reads it.

    Named here rather than imported because this is the side that says
    what it needs: two questions, neither of which raises. What answers
    them in a server is `conversations.threads.Reads`, which opens a
    connection per call and turns every failure into a value.
    """

    def candidates(
        self, agent: str, description: str
    ) -> "threads.Candidates | threads.Unreadable": ...

    def backlog(
        self, conversation: str
    ) -> "threads.Backlog | None | threads.Unreadable": ...


@dataclass(frozen=True)
class Resumed:
    """A thread that is ready to be moved onto.

    Everything the runtime needs to make the transition and to say what
    it did: the context to install, the two counts the event carries,
    and the two facts the seeded round is told about, which are that the
    thread was longer than the budget and that its record has a hole in
    it.
    """

    conversation: str
    turns: tuple[Turn, ...] = ()
    rendered: int = 0
    skipped: int = 0
    over_budget: bool = False
    incomplete: bool = False


@dataclass(frozen=True)
class Recap:
    """What a consented recap is made from, and what it will be allowed
    to claim.

    The input is already messages, because summarizing a thread is
    reading it the way a model reads it. What sits beside it is the
    honest half: `from_turn` and `after_turn` are the range the
    summarizer really saw under its own budget, `covered` is every turn
    id inside that range as the thread held them, and `parent` is the
    checkpoint whose text was folded into that input. A recap that
    recorded anything wider would be a summary claiming turns it never
    read.

    `covered` is the half that survives the wait. The recap is spoken
    before it is stored, so the ids are what the store checks the thread
    against at the moment it would write the row: an erasure that landed
    in between took its sources, and a checkpoint standing for turns
    that are gone is exactly the erased content coming back.

    `tail` is what the thread holds after the range, which is what the
    installed context is built from once the recap exists. Empty in the
    ordinary case, and not assumed to be: a turn that stored no text is
    read by nobody and is still there.
    """

    conversation: str
    input: tuple[Turn, ...]
    from_turn: int
    after_turn: int
    covered: tuple[int, ...]
    parent: int | None
    incomplete: bool = False
    tail: tuple[StoredTurn, ...] = ()


class Resumption:
    """One session's resumption flow.

    Per session rather than per process, because what it holds is what
    this session's agents were offered. Built only where the deployment
    switched resumption on, so the runtime's `is not None` is the whole
    of "can this server resume anything": a server that cannot has no
    object here, and both tools answer the one sentence that says so.
    """

    def __init__(
        self, reads: ThreadReads, budget_tokens: int, timeout_s: float
    ) -> None:
        self._reads = reads
        self._budget_tokens = budget_tokens
        self._timeout_s = timeout_s
        # What each agent was last offered, in the order it was read
        # out. Replaced by a newer search and dropped whole at every
        # transition, so an id never outlives the conversation it was
        # offered in.
        self._offered: dict[str, tuple[str, ...]] = {}
        # One search at a time for this session, which is what makes
        # "last" mean the last one the model asked for. See `described`.
        self._searching = asyncio.Lock()
        # The one conversation each agent has asked the user a question
        # about: whether to hear a recap of it or carry on from its
        # recent part. One per agent, because the question is asked
        # about one thread and answering it is the next thing that
        # happens; replaced by a newer offer, dropped by a newer search,
        # and dropped whole at every transition, exactly as the ids
        # above are.
        self._awaiting: dict[str, str] = {}

    async def described(self, agent: str, description: str) -> str:
        """Search, and answer what the model reads out.

        The answer is a sentence either way. Nothing scoring is not a
        dead end: the newest threads come back with a line saying so, so
        a user who described it badly can still recognize it, and a
        thread that scored nothing is still one they can pick.

        Searches are serialized, which is what makes the state below say
        what it claims to. A round may issue several of these and the
        tool loop runs them together, so two of them race to replace the
        offer; whichever database read finished first would lose, and
        the ids a selection is then honored against would be decided by
        how quickly two queries came back rather than by which search
        the model asked for last. The lock is the session's rather than
        the agent's because a session has one agent talking at a time,
        so the two are the same lock with one fewer thing in it. What it
        costs is one slow read delaying the next; both are still bounded
        by the tool timeout above them, so a database thinking about it
        is a tool that answered a sentence either way.
        """
        async with self._searching:
            answer = await self._ask(lambda: self._reads.candidates(agent, description))
            if isinstance(answer, str):
                return answer
            # A newer search replaces what this agent may pick, and with
            # it any question it had already asked about one of the old
            # ones: the user has moved on, and an answer to a question
            # about a thread nobody is offering any more is not an
            # answer.
            self._awaiting.pop(agent, None)
            if not answer.found:
                self._offered.pop(agent, None)
                return builtin.NOTHING_TO_RESUME
            self._offered[agent] = tuple(one.conversation for one in answer.found)
            return builtin.candidate_list(
                builtin.CANDIDATES_FOUND
                if answer.matched
                else builtin.CANDIDATES_UNMATCHED,
                answer.found,
            )

    def offers(self, agent: str, conversation: str) -> bool:
        """Whether this agent may pick this conversation, which it may
        only if this agent was offered it and has not searched since."""
        return conversation in self._offered.get(agent, ())

    async def resumed(self, agent: str, conversation: str) -> "Resumed | str":
        """The thread, ready to move onto, or the sentence saying why
        not.

        The offer is checked again here rather than trusted from the
        caller, because this is the module that made it and a second
        opinion about what was offered would be a second answer.

        Milestone-aware by way of the read: where the thread has a recap
        checkpoint, the store hands back that checkpoint and the turns
        after its coverage, and the hydrator pins the one in front of
        the others. Nothing here has to know that happened.
        """
        found = await self._backlog(agent, conversation)
        if isinstance(found, str):
            return found
        context = hydration.hydrated(
            found.turns,
            self._budget_tokens,
            milestone=None if found.milestone is None else found.milestone.text,
        )
        return Resumed(
            conversation=conversation,
            turns=context.turns,
            rendered=context.rendered,
            skipped=context.skipped,
            over_budget=context.over_budget,
            incomplete=found.incomplete,
        )

    def awaits(self, agent: str, conversation: str) -> bool:
        """Whether this agent has asked the user how to pick this thread
        up, which is the only state in which an answer means anything.
        """
        return self._awaiting.get(agent) == conversation

    def offer_choice(self, agent: str, conversation: str) -> None:
        """Remember that this agent has just asked the user which way to
        pick this thread up. One per agent: the question is about one
        thread and answering it is the next thing that happens."""
        self._awaiting[agent] = conversation

    async def recap(self, agent: str, conversation: str) -> "Recap | str":
        """What a consented recap will be made from, or the sentence
        saying why it cannot be.

        Read fresh rather than kept from the offer, and the reason is
        the flow's own shape: the offer was made in one reply and the
        answer arrives in the next, with a turn of the user's in
        between. What is summarized is the thread as it is now.

        The input is bounded by the recap's own budget and truncated
        oldest first, and what comes back records where the reading
        really began. That is the whole of the honesty here: a recap
        that read the newest half of a long thread says so in
        `from_turn`, and hydration afterwards treats everything below it
        as truncated rather than summarized.

        The ids inside that range come back too, because the read and
        the write are separated by a summarization round and a paragraph
        read out loud. What they are for is the store's own check at the
        far end of that interval, and they are read here because here is
        where the thread was really looked at.
        """
        found = await self._backlog(agent, conversation)
        if isinstance(found, str):
            return found
        read = hydration.hydrated(
            found.turns,
            RECAP_INPUT_BUDGET_TOKENS,
            milestone=None if found.milestone is None else found.milestone.text,
        )
        if read.from_turn is None or read.after_turn is None:
            # Nothing text-bearing to summarize. A thread of gaps, or one
            # whose whole content is a checkpoint nothing followed:
            # either way there is no range a new checkpoint could
            # honestly claim, so there is no recap to make.
            return builtin.NOTHING_TO_RESUME
        return Recap(
            conversation=conversation,
            input=read.turns,
            from_turn=read.from_turn,
            after_turn=read.after_turn,
            covered=tuple(
                one.id
                for one in found.turns
                if read.from_turn <= one.id <= read.after_turn
            ),
            parent=None if found.milestone is None else found.milestone.id,
            incomplete=found.incomplete,
            tail=tuple(one for one in found.turns if one.id > read.after_turn),
        )

    def after_recap(self, recap: "Recap", text: str) -> Resumed:
        """The context a thread is picked up on once its recap exists.

        Pure, and deliberately not a second read: what the thread is now
        is the recap that was just spoken plus whatever the summarizer
        did not reach, and both are already in hand. The same hydrator
        renders it as it renders every other resume, so a thread read
        back tomorrow from the stored checkpoint reads exactly like this
        one does today.
        """
        context = hydration.hydrated(recap.tail, self._budget_tokens, milestone=text)
        return Resumed(
            conversation=recap.conversation,
            turns=context.turns,
            rendered=context.rendered,
            skipped=context.skipped,
            over_budget=context.over_budget,
            incomplete=recap.incomplete,
        )

    def forget(self) -> None:
        """Drop every offer this session is holding.

        Called at each transition, whichever kind: a handover, a fresh
        conversation and a resume all end the conversation an offer was
        made in, and an id that outlived it is exactly the stale
        selection the enforcement exists to refuse. The question a long
        thread was asked about goes with them, for the same reason.
        """
        self._offered.clear()
        self._awaiting.clear()

    async def _backlog(
        self, agent: str, conversation: str
    ) -> "threads.Backlog | str":
        """One thread as the store holds it, or the sentence saying why
        this agent may not have it.

        The one door both the resume and the recap read through, so
        "what may this agent pick up" is answered once. The offer is
        checked here rather than trusted from the caller, because this
        is the module that made it.
        """
        if not self.offers(agent, conversation):
            return builtin.NO_SUCH_CANDIDATE
        found = await self._ask(lambda: self._reads.backlog(conversation))
        if isinstance(found, str):
            return found
        if found is None:
            # Deleted since it was offered, or never written. One answer
            # for both, because there is nothing to resume either way.
            return builtin.CONVERSATION_GONE
        if found.agent != agent:
            # Unreachable through `offers` above, which is scoped to the
            # agent the search ran for, and checked anyway: the column
            # is the one the whole entity is keyed on, and an agent
            # reading another agent's thread is the failure this feature
            # must not have.
            return builtin.NO_SUCH_CANDIDATE
        return found

    async def _ask(self, read: Callable[[], Any]) -> Any:
        """One store read, off the event loop and bounded.

        In a worker thread because the driver is synchronous and every
        live conversation shares this loop; bounded because the caller
        is a reply, and a database that is thinking about it is a tool
        that did not answer rather than a session that stopped
        listening. A timeout is reported as busy, which is what it is
        from here: nothing is known to be wrong, and asking again in a
        moment is the thing to do.
        """
        try:
            async with asyncio.timeout(self._timeout_s):
                answer = await asyncio.to_thread(read)
        except TimeoutError:
            return builtin.STORE_BUSY
        if isinstance(answer, threads.Unreadable):
            return builtin.STORE_BUSY if answer.busy else builtin.STORE_UNREADABLE
        return answer


__all__ = [
    "RECAP_INPUT_BUDGET_TOKENS",
    "Recap",
    "Resumed",
    "Resumption",
    "ThreadReads",
]
