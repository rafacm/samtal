"""The live view of the same events the log retains: a subscriber hub.

The structured events are already dispatched to every attached tap
([the package](__init__.py)), and the taps that exist keep what they
are given: the JSON log, the capture's decision track, the conversation
store. This one keeps nothing at all. It holds whoever is watching
right now, hands each of them the events their filters admit, and
forgets everything the moment they stop reading, which is what the
content ADR means by "live views are fed from the event tap, not from a
store" (#342).

Three properties are the whole of the design, and each of them is a
constraint the tap contract imposes rather than a preference.

**It never blocks and it never raises.** `emit` runs synchronously on
whoever emitted, which includes the conversation store's writer thread
and every reply path in the process, so a subscriber that has stopped
reading must cost the emitter nothing. What `emit` does is bounded: it
takes a lock, walks the subscriptions, appends to a bounded deque, and
schedules a wakeup. There is no await, no I/O and no call into
subscriber code, so there is nothing here that can wait on a reader and
nothing that needs a guard of its own. Bounded work rather than
allocation-free work: the streamed object is built once per admitted
emission and the wakeup is scheduled per waiting reader.

**A slow reader loses events, never the server.** Each subscription
holds `CAPACITY` events and no more; past that the oldest is
overwritten and counted, and the count rides the reader's own stream as
its own item, so silence never means loss. Two hundred and fifty six is
minutes of buffer at the log's own volume for a terminal that stopped
scrolling, and past it an honest number is better than back-pressure
into a conversation.

**It is a leaf.** It imports the tap vocabulary and nothing of FastAPI,
of sessions, or of the API: the route, the composition root and the
device edge all reach down into this module, and none of them is named
here. What crosses out of it is `Streamed` and `Dropped`, which are the
two things a reader can be handed.

The lock is reentrant, and that is load bearing rather than defensive.
`close()` is called from a signal handler on the shutdown path
(`serving.py`), and a signal handler runs on the main thread between
bytecodes, which may be a thread already inside `emit` holding this
lock. A plain lock would deadlock the shutdown there. `emit` therefore
walks a list it took under the lock, so a reentrant close that empties
the subscription set mid-walk leaves the walk offering to
subscriptions that have already ended, which is a no-op no reader
observes.
"""

import asyncio
import contextlib
import logging
import threading
from collections import deque
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any

from vinga_server.events import Emission
from vinga_server.events.catalog import LEVELS as DECLARED_LEVELS

# How many events one reader may fall behind by before the oldest of
# them is overwritten.
#
# Documented rather than configurable, because the number a deployment
# would tune is the one thing this buffer must not become: a queue an
# operator can grow is a retention decision, and the surface has none.
# At the log's own volume this is minutes of buffer for a terminal that
# stopped scrolling, and past it the dropped count is the honest answer.
CAPACITY = 256

# The levels a filter may name, derived from the catalog's own set
# rather than restated: what an event may be emitted at and what a
# reader may ask for are one fact, and a second spelling of it is a
# disagreement waiting to happen.
LEVELS: Mapping[str, int] = MappingProxyType(
    {logging.getLevelName(level): level for level in sorted(DECLARED_LEVELS)}
)

# What a reader who names no level is given.
#
# INFO rather than everything. The tap hears an emission before any
# logger threshold does, so a DEBUG default would stream events the
# retained log itself would not carry, and a live view that shows more
# than the record is a live view that cannot be checked against it.
DEFAULT_LEVEL = logging.INFO

# The two fields the stream owns, written after the payload so they can
# never be shadowed by it.
#
# `ts` is here because `Emission.at` is a monotonic reading that means
# nothing off this host, and `level` because the payload carries the
# event's name and not how loud it is. The JSON log's own `ts` is the
# formatter's for the same reason, so a streamed object is the retained
# record plus exactly these two, which is what the no-leak pin asserts.
STREAM_FIELDS = ("level", "ts")


@dataclass(frozen=True)
class Streamed:
    """One event as a reader receives it: the emission's payload with
    the stream's two fields on it."""

    fields: dict[str, Any]


@dataclass(frozen=True)
class Dropped:
    """How many events one reader lost since the last thing it was
    handed. Delivered in the stream rather than counted somewhere a
    reader would have to go and look."""

    count: int


@dataclass(frozen=True)
class Filters:
    """What one reader asked to see.

    Applied at enqueue rather than at delivery, so a filtered reader
    never spends its buffer on events it did not ask for. `device` and
    `session` are the canonical forms the payload carries, so an event
    that names neither passes such a filter only when none is set:
    tailing one device shows that device's traffic and not the server's
    whole life.
    """

    device: str | None = None
    session: str | None = None
    level: int = DEFAULT_LEVEL

    def admits(self, emission: Emission) -> bool:
        if emission.level < self.level:
            return False
        if self.device is not None and emission.payload.get("device") != self.device:
            return False
        return self.session is None or emission.payload.get("session") == self.session


class Subscription:
    """One reader's own queue, and the two ends of it.

    The emit end (`offer`, `end`) runs on whatever thread emitted, under
    the hub's lock. The reading end (`next`, and the async iteration
    over it) runs on the loop this was subscribed from. What connects
    them is an `asyncio.Event` set through `call_soon_threadsafe`, which
    is the one asyncio primitive a foreign thread may touch.
    """

    def __init__(
        self,
        filters: Filters,
        lock: threading.RLock,
        loop: asyncio.AbstractEventLoop,
        capacity: int = CAPACITY,
    ) -> None:
        self.filters = filters
        self._lock = lock
        self._loop = loop
        self._capacity = capacity
        self._queue: deque[Streamed] = deque(maxlen=capacity)
        self._dropped = 0
        self._ready = asyncio.Event()
        self._finished = False

    # --- the emit end, on whatever thread emitted ---------------------

    def offer(self, streamed: Streamed) -> None:
        """Enqueue one event, overwriting the oldest when the queue is
        full and counting what was overwritten. Called with the hub's
        lock held."""
        if len(self._queue) == self._capacity:
            self._dropped += 1
        self._queue.append(streamed)
        self._wake()

    def end(self) -> None:
        """Stop this subscription: what is already queued is still
        delivered, and the reading end sees the stream end after it."""
        self._finished = True
        self._wake()

    def _wake(self) -> None:
        """Nudge the reader, from wherever this is.

        `call_soon_threadsafe` refuses on a loop that has been closed,
        which is a process on its way out and a reader that is already
        gone. Nothing is owed to it, and the tap contract is what
        forbids raising here."""
        with contextlib.suppress(RuntimeError):
            self._loop.call_soon_threadsafe(self._ready.set)

    # --- the reading end, on the loop that subscribed -----------------

    @property
    def ended(self) -> bool:
        """Whether this subscription has stopped and nothing of it is
        left to deliver."""
        with self._lock:
            return self._finished and not self._queue and self._dropped == 0

    async def next(self, timeout: float | None = None) -> Streamed | Dropped | None:
        """The next thing to deliver, or None.

        None means one of two things and the caller distinguishes them
        with `ended`: `timeout` elapsed with nothing to deliver (which
        is what a keepalive is written on), or the subscription is over.
        A dropped count is delivered before the events after it, because
        that is where in the stream the loss happened.
        """
        while True:
            item = self._take()
            if item is not None:
                return item
            if self.ended:
                return None
            try:
                await asyncio.wait_for(self._ready.wait(), timeout)
            except TimeoutError:
                return None

    def __aiter__(self) -> AsyncIterator["Streamed | Dropped"]:
        return self

    async def __anext__(self) -> "Streamed | Dropped":
        item = await self.next()
        if item is None:
            raise StopAsyncIteration
        return item

    def _take(self) -> Streamed | Dropped | None:
        """Whatever is due, without waiting.

        The clear happens here, under the lock and only when there is
        nothing left, which is what makes the wakeup lossless: an
        `offer` either lands before this and leaves an item this call
        returns, or lands after it and schedules a set the waiter below
        meets.
        """
        with self._lock:
            if self._dropped:
                count, self._dropped = self._dropped, 0
                return Dropped(count)
            if self._queue:
                return self._queue.popleft()
            self._ready.clear()
            return None


class LiveEvents:
    """Everyone watching this server right now.

    One object, attached in two places: to the process-wide server tap
    (`attach_server_tap`, at composition time) and to every session's
    own `SessionEvents` as it is constructed. The emission's payload
    says which channel it rode, so a reader does not have to.

    `emit` is the tap contract, and the rest is the reader's side:
    `subscribe` for a queue of one's own, `unsubscribe` to give it back,
    `subscribers` as the count a diagnostic may want, and `close` for
    the end of the process, which wakes and terminates every
    subscription so a shutdown never waits on an open tail.
    """

    def __init__(self, capacity: int = CAPACITY) -> None:
        self.capacity = capacity
        self._lock = threading.RLock()
        self._subscriptions: list[Subscription] = []
        self._closed = False

    # --- the tap contract ---------------------------------------------

    def emit(self, emission: Emission) -> None:
        """Offer one emission to everyone whose filters admit it.

        Never blocks and never raises, by construction rather than by
        guard: a lock, a walk, a bounded append and a scheduled wakeup.
        The streamed object is built once and shared by every reader
        that gets it, which is safe because nothing downstream mutates
        it; the dispatcher already handed this tap a copy of its own.
        """
        with self._lock:
            interested = [
                subscription
                for subscription in self._subscriptions
                if subscription.filters.admits(emission)
            ]
            if not interested:
                return
            streamed = _streamed(emission)
            for subscription in interested:
                subscription.offer(streamed)

    # --- the readers ---------------------------------------------------

    def subscribe(self, filters: Filters | None = None) -> Subscription:
        """A queue of one's own, filled from now on.

        Called on the loop that will read it, which is where the wakeup
        has to land. Subscribing to a hub that has closed answers a
        subscription that has already ended, rather than one that would
        wait forever for a server that is stopping.
        """
        subscription = Subscription(
            filters if filters is not None else Filters(),
            self._lock,
            asyncio.get_running_loop(),
            self.capacity,
        )
        with self._lock:
            if self._closed:
                subscription.end()
            else:
                self._subscriptions.append(subscription)
        return subscription

    def unsubscribe(self, subscription: Subscription) -> None:
        """Give a queue back. Ending one that was never attached, or was
        detached by a close, is not an error: a reader unwinding does
        not have to know which."""
        with self._lock, contextlib.suppress(ValueError):
            self._subscriptions.remove(subscription)
        subscription.end()

    @property
    def subscribers(self) -> int:
        """How many readers this hub is feeding."""
        with self._lock:
            return len(self._subscriptions)

    def close(self) -> None:
        """End every subscription, and refuse to begin another.

        Called on the way out, before uvicorn's own shutdown, so an open
        tail ends with the server rather than holding it up. Safe from a
        signal handler: see the module docstring on the reentrant lock.
        """
        with self._lock:
            self._closed = True
            ending = tuple(self._subscriptions)
            self._subscriptions.clear()
        for subscription in ending:
            subscription.end()


def _streamed(emission: Emission) -> Streamed:
    """One emission as a reader receives it.

    The payload first and the stream's own two fields after it, so a
    payload can never shadow them. The stamp is taken here, at enqueue,
    because that is the moment the event was real to this server; a
    reader that receives it a second later is reading history however
    short.
    """
    return Streamed(
        {
            **emission.payload,
            "level": logging.getLevelName(emission.level),
            "ts": datetime.now(UTC).isoformat(),
        }
    )


__all__ = [
    "CAPACITY",
    "DEFAULT_LEVEL",
    "LEVELS",
    "STREAM_FIELDS",
    "Dropped",
    "Filters",
    "LiveEvents",
    "Streamed",
    "Subscription",
]
