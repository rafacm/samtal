"""What the server knows about the conversations it is holding.

One registry per app, held by its composition. It exists for what a
per-session object cannot do: refuse the next connection when the
server is already at capacity, reach every live session at once when the
process is asked to stop, and say which worlds the conversations in
flight are still holding.

Capacity is a count, not a queue. A device that is refused reconnects on
its next wake word, where a conversation waiting in line for a slot
would be worse than one that never started: the user is standing in
front of the device, talking.

The worlds are the newer half (#191). A conversation is built from the
generation that was current when it was built, and goes on speaking
through that generation's engines however many applies land while it
runs, so a world may not let go of what it holds until the last
conversation holding it has ended. Two states, not one: a session is
admitted before it has proved anything, and many admitted sessions are
turned away before a conversation is ever built for them, so being here
is not being a holder. `bound` is the second state, reported by the
session in the same breath as the construction it describes, and
`remove` gives back a slot and whatever world went with it.
"""

import asyncio
from typing import TYPE_CHECKING, Literal

from vinga_server.events import ServerEvents
from vinga_server.events.catalog import DrainFinished, DrainIncomplete, DrainStarted
from vinga_server.events.values import Count, Real
from vinga_server.generation import Generation, Generations

if TYPE_CHECKING:  # the session imports nothing from here
    from vinga_server.device.session import DeviceSession

events = ServerEvents(__name__)

# Held back from the drain budget for the closes themselves, so the
# overall bound is a backstop for a session stuck somewhere other than
# its reply rather than something that races the per-reply wait. Capped
# at a tenth of the budget as well, so that a deliberately short drain
# period still spends most of itself on the replies.
CLOSE_MARGIN_S = 1.0
CLOSE_MARGIN_FRACTION = 0.1

# What the door answers, and the whole of what it can answer: the one
# state that takes the next conversation, and the two reasons the next
# conversation is turned away. A closed set because it is reported out
# loud, over HTTP, to whoever is deciding where to send traffic.
Admission = Literal["admitting", "draining", "full"]


class SessionRegistry:
    """The live sessions, whether there is room for another, and which
    worlds they are holding."""

    def __init__(self, max_sessions: int, generations: Generations | None = None) -> None:
        self._max_sessions = max_sessions
        self._sessions: set[DeviceSession] = set()
        self._draining = False
        # The world each session is talking through, for the sessions
        # that got as far as having one. Not every admitted session
        # does: a bad Device-Id, a device bound to nothing and a client
        # that vanishes during the bindings lookup are all admitted and
        # removed without a conversation ever being built.
        self._bound: dict[DeviceSession, Generation] = {}
        # Where a retired world goes to be let go of, and None for an
        # application with no holder around it, which is a test with a
        # session and no server. The disposal rule is that module's; the
        # count it runs on is this one's.
        self._generations = generations
        # The disposals in flight. A session ends in a synchronous
        # `finally` and letting go of a provider is a coroutine, so the
        # work is a task; held rather than launched, because a task
        # nobody keeps is a task the loop may collect mid-close, and
        # because the shutdown below has to be able to wait for it.
        self._disposals: set[asyncio.Task[None]] = set()

    def __len__(self) -> int:
        return len(self._sessions)

    @property
    def draining(self) -> bool:
        return self._draining

    @property
    def admission(self) -> Admission:
        """Whether this server may be handed another conversation, and
        which of the two reasons it may not.

        One classifier over the two facts this object owns, so that the
        door and anything reporting on the door cannot come to disagree:
        `admit` decides through this and answers in these words, and the
        readiness probe says the same word out loud.

        Draining wins over full, because it is the terminal one. A full
        server has a slot again when a conversation ends; a draining one
        never admits another, and that is the fact worth reporting to
        whoever is deciding whether to keep sending it work.
        """
        if self._draining:
            return "draining"
        if len(self._sessions) >= self._max_sessions:
            return "full"
        return "admitting"

    def stop_admitting(self) -> None:
        """Turn the next conversation away from here on, without waiting
        for the ones in flight.

        The door half of `drain` below, split out because shutdown begins
        before the drain does: the signal handler schedules the drain
        onto the loop rather than running it, and a `drain_s` of zero
        never runs one at all, so a registry that only latched inside
        `drain` would keep admitting after the process had begun to go.

        Synchronous and idempotent, which is what makes calling it from a
        signal handler and on every path out safe and free: setting one
        bool is atomic under the GIL, and a flag that latches costs
        nothing to set twice. It never clears; a server that has started
        refusing conversations is not going to want them again.
        """
        self._draining = True

    def admit(self, session: "DeviceSession") -> Admission:
        """Take a slot for this session, and say what was decided:
        `admitting` when it got one, and which of the two refusals it met
        when it did not.

        The answer is the classifier's own word rather than a yes or a
        no, because the caller has something to say about the difference:
        a device turned away from a full server and one turned away from
        a server that is shutting down are different diagnoses, and
        collapsing them made a redeploy look like a capacity problem.

        Deliberately not a coroutine: an admission decision that can
        await is one that can race another admission. What it cannot
        refuse to race is the latch, which `stop_admitting` sets from a
        signal handler, so it can land between any two lines here. That
        is why the flag is read again after the insertion: a session
        admitted by a classification the signal then invalidated would be
        a conversation started on a process already shutting down.
        Capacity needs no second look, since this is the only thing that
        adds to the set and no signal handler calls it.
        """
        decision = self.admission
        if decision != "admitting":
            return decision
        held = session in self._sessions
        self._sessions.add(session)
        if self._draining:
            # The signal won the race. The slot goes back, unless this
            # session was already holding one: a conversation admitted
            # before the shutdown began keeps the slot it is speaking
            # through, and only this second attempt is refused.
            if not held:
                self._sessions.discard(session)
            return "draining"
        return "admitting"

    def bound(self, session: "DeviceSession", generation: Generation) -> None:
        """This session is talking through this world, from now until it
        ends.

        Reported by the session in the same synchronous step as the
        construction it describes, so that a world can never be retired
        and disposed of between the moment a conversation was built from
        it and the moment anybody knew (#191).
        """
        self._bound[session] = generation

    def held(self) -> list[Generation]:
        """The worlds live conversations are still speaking through.

        What the disposal rule needs and cannot work out for itself:
        this is the only object that knows the whole session set.
        """
        return list(self._bound.values())

    def remove(self, session: "DeviceSession") -> None:
        """Give the slot back, and the world with it.

        Idempotent, because this runs in a session's `finally` and
        nothing guarantees it ran only once, and a no-op on the world
        for a session that never bound one.

        A world nothing holds any more may be disposed of, which is what
        makes the end of the last conversation on a retired generation
        the moment its engines are released.
        """
        self._sessions.discard(session)
        if self._bound.pop(session, None) is None:
            return
        self._dispose()

    def _dispose(self) -> None:
        """Ask the holder to let go of whatever nobody holds, as a task
        this object owns.

        Tracked rather than fired and forgotten: a close that nothing
        keeps a reference to can be collected part way through, and the
        drain below is entitled to know that the closing has finished.
        """
        if self._generations is None:
            return
        disposal = asyncio.create_task(
            self._generations.dispose(self.held()), name="generation-dispose"
        )
        self._disposals.add(disposal)
        disposal.add_done_callback(self._disposals.discard)

    async def drain(self, timeout_s: float) -> None:
        """Stop admitting sessions, and let the ones in flight finish
        speaking before they are closed, bounded by `timeout_s`.

        The door is shut through `stop_admitting` above, as the first
        statement here, so that this and the shutdown that may have
        already shut it are latching one flag rather than two. Whatever
        has not finished when the bound expires is left to uvicorn's own
        shutdown, which fail-closes every remaining websocket with 1012.
        """
        self.stop_admitting()
        sessions = list(self._sessions)
        if not sessions:
            await self._settled()
            return

        events.emit(
            lambda: DrainStarted(
                sessions=Count(len(sessions)), timeout_s=Real(timeout_s)
            )
        )
        # The drain's budget is what a reply is given, rather than some
        # constant inside the session: an operator who raises drain_s to
        # cover long replies has to actually get longer replies out of it.
        # A slice is held back for the close itself, so the outer bound
        # below stays a backstop instead of racing the inner one.
        reply_grace_s = timeout_s - min(CLOSE_MARGIN_S, timeout_s * CLOSE_MARGIN_FRACTION)
        done, pending = await asyncio.wait(
            [
                asyncio.create_task(
                    # The token goes with the request, so the record says
                    # a drain ended these conversations even where an
                    # idle timer or a disconnect arrives behind it.
                    session.request_shutdown(
                        grace_s=reply_grace_s, close_reason="drain"
                    )
                )
                for session in sessions
            ],
            timeout=timeout_s,
        )
        for task in pending:
            task.cancel()

        # A session whose reply outlasted the grace was closed mid-sentence.
        # It has to be reported: it is the signal that drain_s is too short,
        # and reporting it as a clean drain would hide exactly that.
        cut = sum(1 for task in done if task.exception() is None and not task.result())
        if pending or cut:
            events.emit(
                lambda: DrainIncomplete(
                    sessions=Count(len(sessions)),
                    cut_mid_reply=Count(cut),
                    unfinished=Count(len(pending)),
                    timeout_s=Real(timeout_s),
                )
            )
        else:
            events.emit(lambda: DrainFinished(sessions=Count(len(sessions))))
        await self._settled()

    async def _settled(self) -> None:
        """Wait for the letting-go the sessions that just ended started.

        A session's removal is synchronous and the disposal it triggers
        is not, so at the moment the drain's last conversation returns
        there can be a world part way through closing. The process is
        about to close everything anyway, and waiting here is what makes
        that the same closing rather than a second one racing the first.
        """
        if self._disposals:
            await asyncio.wait(set(self._disposals))
