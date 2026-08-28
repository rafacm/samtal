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

What is deliberately NOT here: minting an id, rebinding an agent to a
thread, and installing a history. Those are the runtime's, because they
are the transition, and a transition happens at a turn boundary that
only the reply path can see.

Milestone 5's recap offer joins the state below: an over-budget resume
will leave one conversation awaiting a consent decision, kept per agent
beside the offered ids and cleared by the same rules.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from vinga_server.conversations import hydration, threads
from vinga_server.providers import Turn
from vinga_server.tools import builtin


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

    async def described(self, agent: str, description: str) -> str:
        """Search, and answer what the model reads out.

        The answer is a sentence either way. Nothing scoring is not a
        dead end: the newest threads come back with a line saying so, so
        a user who described it badly can still recognize it, and a
        thread that scored nothing is still one they can pick.
        """
        answer = await self._ask(lambda: self._reads.candidates(agent, description))
        if isinstance(answer, str):
            return answer
        if not answer.found:
            self._offered.pop(agent, None)
            return builtin.NOTHING_TO_RESUME
        self._offered[agent] = tuple(one.conversation for one in answer.found)
        return builtin.candidate_list(
            builtin.CANDIDATES_FOUND if answer.matched else builtin.CANDIDATES_UNMATCHED,
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
        context = hydration.hydrated(found.turns, self._budget_tokens)
        return Resumed(
            conversation=conversation,
            turns=context.turns,
            rendered=context.rendered,
            skipped=context.skipped,
            over_budget=context.over_budget,
            incomplete=found.incomplete,
        )

    def forget(self) -> None:
        """Drop every offer this session is holding.

        Called at each transition, whichever kind: a handover, a fresh
        conversation and a resume all end the conversation an offer was
        made in, and an id that outlived it is exactly the stale
        selection the enforcement exists to refuse.
        """
        self._offered.clear()

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


__all__ = ["Resumed", "Resumption", "ThreadReads"]
