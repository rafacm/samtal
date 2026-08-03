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
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # the session imports nothing from here
    from samtal_server.session import Session

logger = logging.getLogger(__name__)


class SessionRegistry:
    """The live sessions, and whether there is room for another."""

    def __init__(self, max_sessions: int) -> None:
        self._max_sessions = max_sessions
        self._sessions: set[Session] = set()
        self._draining = False

    def __len__(self) -> int:
        return len(self._sessions)

    @property
    def draining(self) -> bool:
        return self._draining

    def try_add(self, session: "Session") -> bool:
        """Take a slot for this session, or answer False when the server
        is full or on its way out. Deliberately not a coroutine: an
        admission decision that can await is one that can race another
        admission."""
        if self._draining or len(self._sessions) >= self._max_sessions:
            return False
        self._sessions.add(session)
        return True

    def remove(self, session: "Session") -> None:
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

        logger.info(
            "draining %d session(s), up to %.0f s",
            len(sessions),
            timeout_s,
            extra={
                "event": "drain_started",
                "sessions": len(sessions),
                "timeout_s": timeout_s,
            },
        )
        _, pending = await asyncio.wait(
            [asyncio.create_task(session.request_shutdown()) for session in sessions],
            timeout=timeout_s,
        )
        for task in pending:
            task.cancel()
        if pending:
            logger.warning(
                "%d session(s) did not finish inside the drain period",
                len(pending),
                extra={"event": "drain_timeout", "sessions": len(pending)},
            )
        else:
            logger.info(
                "every session drained",
                extra={"event": "drain_finished", "sessions": len(sessions)},
            )
