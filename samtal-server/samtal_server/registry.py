"""What the server knows about the conversations it is holding.

One registry per app, on `app.state`. It exists for what a per-session
object cannot do: refuse the next connection when the server is already
at capacity, and (from the drain onwards) reach every live session at
once when the process is asked to stop.

Capacity is a count, not a queue. A device that is refused reconnects on
its next wake word, where a conversation waiting in line for a slot
would be worse than one that never started: the user is standing in
front of the device, talking.
"""

import asyncio
from typing import TYPE_CHECKING

from samtal_server.events import ServerEvents

if TYPE_CHECKING:  # the session imports nothing from here
    from samtal_server.device.session import DeviceSession

events = ServerEvents(__name__)

# Held back from the drain budget for the closes themselves, so the
# overall bound is a backstop for a session stuck somewhere other than
# its reply rather than something that races the per-reply wait. Capped
# at a tenth of the budget as well, so that a deliberately short drain
# period still spends most of itself on the replies.
CLOSE_MARGIN_S = 1.0
CLOSE_MARGIN_FRACTION = 0.1


class SessionRegistry:
    """The live sessions, and whether there is room for another."""

    def __init__(self, max_sessions: int) -> None:
        self._max_sessions = max_sessions
        self._sessions: set[DeviceSession] = set()
        self._draining = False

    def __len__(self) -> int:
        return len(self._sessions)

    @property
    def draining(self) -> bool:
        return self._draining

    def try_add(self, session: "DeviceSession") -> bool:
        """Take a slot for this session, or answer False when the server
        is full or on its way out. Deliberately not a coroutine: an
        admission decision that can await is one that can race another
        admission."""
        if self._draining or len(self._sessions) >= self._max_sessions:
            return False
        self._sessions.add(session)
        return True

    def remove(self, session: "DeviceSession") -> None:
        """Give the slot back. Idempotent, because this runs in a
        session's `finally` and nothing guarantees it ran only once."""
        self._sessions.discard(session)

    async def drain(self, timeout_s: float) -> None:
        """Stop admitting sessions, and let the ones in flight finish
        speaking before they are closed, bounded by `timeout_s`.

        Draining latches on: this runs on the way out, and a server that
        has started refusing connections is not going to want them
        again. Whatever has not finished when the bound expires is left
        to uvicorn's own shutdown, which fail-closes every remaining
        websocket with 1012.
        """
        self._draining = True
        sessions = list(self._sessions)
        if not sessions:
            return

        events.info(
            "draining %d session(s), up to %.0f s",
            len(sessions),
            timeout_s,
            event="drain_started",
            sessions=len(sessions),
            timeout_s=timeout_s,
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
            events.warning(
                "drained with %d session(s) cut mid-reply and %d that did not finish",
                cut,
                len(pending),
                event="drain_incomplete",
                sessions=len(sessions),
                cut_mid_reply=cut,
                unfinished=len(pending),
                timeout_s=timeout_s,
            )
        else:
            events.info(
                "every session drained", event="drain_finished", sessions=len(sessions)
            )
