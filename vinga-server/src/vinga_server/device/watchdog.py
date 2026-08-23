"""The two deadlines a device connection is held to.

Both are timers with nothing to say about what they are timing. The
first-contact one bounds the handshake: a socket that connects and then
says nothing holds a slot for as long as it likes otherwise. The idle
one bounds the conversation: nothing on the device side ends a realtime
session, so a user who walks away leaves the mic streaming until the
session cap comes due an hour later.

What is deliberately not here is what either deadline means. The
session parses the hello and decides that a silent one is a protocol
error; the session decides what counts as conversing and what to do
when it stops. This module counts, and says when the count is up.
"""

import asyncio
import contextlib
from collections.abc import Awaitable, Callable

# How long to wait for the device hello; the firmware gives the server
# hello the same ten seconds.
HELLO_TIMEOUT_S = 10.0


def first_contact() -> asyncio.Timeout:
    """The window a connected device has to say hello in.

    A helper rather than the constant alone, so the timeout is read at
    call time: the value is one line above and this is the only reader
    of it, which is what lets a test shorten the wait by replacing the
    module's own constant.
    """
    return asyncio.timeout(HELLO_TIMEOUT_S)


class IdleWatchdog:
    """Call `on_idle` once the conversation has been quiet for
    `timeout_s`.

    Its three dependencies are narrow on purpose, and the session is not
    one of them. `timeout_s` is how long silence is allowed to run;
    `defer` is asked every time round the loop whether the countdown
    applies at all right now, so a session that changes mode or starts
    replying extends its own deadline without this object learning what
    either of those is; `on_idle` is what to do about it, which is the
    session's policy and stays there.

    `mark` is the other half of `defer`: the deadline counts from the
    last mark, and the caller marks at both ends the timeout counts
    from, so "the last utterance or the last reply, whichever is later"
    falls out of writing the current time at each rather than having to
    compare them.
    """

    def __init__(
        self,
        timeout_s: float,
        defer: Callable[[], bool],
        on_idle: Callable[[], Awaitable[None]],
    ) -> None:
        self._timeout_s = timeout_s
        self._defer = defer
        self._on_idle = on_idle
        self._task: asyncio.Task[None] | None = None
        # When the conversation was last alive, which is what the
        # timeout counts from. None until the first mark, which `start`
        # makes.
        self._marked_at: float | None = None

    @property
    def marked_at(self) -> float | None:
        """When the conversation was last marked alive, or None before
        the first mark."""
        return self._marked_at

    def mark(self) -> None:
        """Record that the conversation is alive right now."""
        self._marked_at = asyncio.get_running_loop().time()

    def start(self) -> None:
        """Start the countdown, from now."""
        self.mark()
        self._task = asyncio.create_task(self._watch())

    async def stop(self) -> None:
        """Stop it, and wait for the task to be gone before returning."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _watch(self) -> None:
        """Sleep to the deadline, and again if the deadline moved.

        `defer` is asked each time round rather than once, because both
        halves of what it stands for can change mid-session: the
        listening mode is not known until the device asks, and whether a
        reply is in flight changes several times a minute. A deferred
        pass marks the conversation alive and sleeps the full timeout
        again, which is what makes deferral a postponement rather than a
        suspension.
        """
        loop = asyncio.get_running_loop()
        while True:
            now = loop.time()
            if self._defer():
                self.mark()
            assert self._marked_at is not None
            remaining = self._marked_at + self._timeout_s - now
            if remaining > 0:
                await asyncio.sleep(remaining)
                continue
            await self._on_idle()
            return
