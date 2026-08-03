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

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # the session imports nothing from here
    from samtal_server.session import Session


class SessionRegistry:
    """The live sessions, and whether there is room for another."""

    def __init__(self, max_sessions: int) -> None:
        self._max_sessions = max_sessions
        self._sessions: set[Session] = set()

    def __len__(self) -> int:
        return len(self._sessions)

    def try_add(self, session: "Session") -> bool:
        """Take a slot for this session, or answer False when the server
        is full. Deliberately not a coroutine: an admission decision that
        can await is one that can race another admission."""
        if len(self._sessions) >= self._max_sessions:
            return False
        self._sessions.add(session)
        return True

    def remove(self, session: "Session") -> None:
        """Give the slot back. Idempotent, because this runs in a
        session's `finally` and nothing guarantees it ran only once."""
        self._sessions.discard(session)
