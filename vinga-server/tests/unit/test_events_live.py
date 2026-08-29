"""The subscriber hub: what a reader is handed, and what it costs to
hand it.

The suite next door (`test_events.py`) proves the dispatch machinery
this attaches to. What is proved here is the hub's own four promises:

- it hears both channels, the session's and the server's, and hands one
  reader what its filters admit and nothing else;
- `emit` never waits on a reader, proved from a foreign thread with the
  loop parked, which is the shape the conversation store's writer
  thread really emits in;
- a reader that falls behind loses the oldest events and is told how
  many, in its own stream, so silence never means loss;
- everything ends: unsubscribing detaches, and a close wakes every
  reader and terminates it, which is what keeps a shutdown from waiting
  on an open tail.

And the no-leak pin, which is the reason this surface can exist at all:
what a reader receives is the record the log retains plus the stream's
own two fields, asserted as an equality rather than as a scan for
sentinels.

Emissions are constructed directly where the property under test is the
hub's own, since `Emission` is the tap contract's vocabulary; the
channel and no-leak tests go through the real emitters and the real
catalog, because what they claim is about production events.
"""

import asyncio
import logging
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.support.events import fields_of, only
from vinga_server.events import (
    Emission,
    ServerEvents,
    SessionEvents,
    attach_server_tap,
    detach_server_tap,
)
from vinga_server.events.catalog import (
    APP_CHANNEL,
    CaptureEnabled,
    RejectedNoAgent,
    declaration_of,
    declared_values,
)
from vinga_server.events.catalog import catalog as declarations
from vinga_server.events.live import (
    CAPACITY,
    DEFAULT_LEVEL,
    LEVELS,
    STREAM_FIELDS,
    Dropped,
    Filters,
    LiveEvents,
    Streamed,
    Subscription,
)
from vinga_server.events.values import ConfiguredPath, DeviceId

MAC = "aa:bb:cc:dd:ee:ff"

SESSION = "0123456789abcdef0123456789abcdef"


def emission(
    event: str = "something",
    level: int = logging.INFO,
    **payload: object,
) -> Emission:
    """One emission in the shape a tap receives it."""
    return Emission(
        payload={"event": event, **payload},
        at=0.0,
        level=level,
        message=event,
        args=(),
    )


async def delivered(subscription: Subscription) -> list[Streamed | Dropped]:
    """Everything waiting for one reader right now, and nothing that is
    not: a zero timeout is what says "whatever is due" without waiting
    for whatever is next."""
    items: list[Streamed | Dropped] = []
    while True:
        item = await subscription.next(timeout=0)
        if item is None:
            return items
        items.append(item)


def events_of(items: list[Streamed | Dropped]) -> list[str]:
    """The event names delivered, which is what most of these assert."""
    return [item.fields["event"] for item in items if isinstance(item, Streamed)]


@pytest.fixture
def hub() -> Iterator[LiveEvents]:
    """A hub that is closed however the test leaves it, so no reader
    outlives the test that made it."""
    live = LiveEvents()
    try:
        yield live
    finally:
        live.close()


async def test_a_reader_is_handed_the_events_in_the_order_they_were_emitted(
    hub: LiveEvents,
) -> None:
    subscription = hub.subscribe()

    hub.emit(emission("first"))
    hub.emit(emission("second"))

    assert events_of(await delivered(subscription)) == ["first", "second"]


async def test_a_reader_hears_both_the_session_and_the_server_channels(
    hub: LiveEvents, tmp_path: Path
) -> None:
    """One hub, two attach points, and the payload is what says which
    channel an event rode."""
    subscription = hub.subscribe()
    session = SessionEvents(SESSION)
    session.device = MAC
    session.attach(hub)
    attach_server_tap(hub)
    try:
        session.emit(lambda: RejectedNoAgent(mac=DeviceId(MAC)))
        ServerEvents(APP_CHANNEL).emit(
            lambda: CaptureEnabled(path=ConfiguredPath(str(tmp_path)))
        )
    finally:
        detach_server_tap(hub)
        session.detach(hub)

    seen = await delivered(subscription)
    assert events_of(seen) == [
        declaration_of(RejectedNoAgent).name,
        declaration_of(CaptureEnabled).name,
    ]
    # The session event carries the session's identity; the server one
    # names what it is about and no session at all.
    assert seen[0].fields["session"] == SESSION
    assert seen[0].fields["device"] == MAC
    assert "session" not in seen[1].fields


async def test_a_streamed_event_is_the_log_record_plus_the_two_stream_fields(
    hub: LiveEvents, caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    """The no-leak contract, as an equality rather than a hunt.

    The stream carries the payload the log retains and nothing else, so
    a field that appeared on one and not the other is a failure here. It
    cannot be a whole-object equality: `ts` and `level` do not exist in
    the payload, which is exactly why they are the stream's own.
    """
    subscription = hub.subscribe()
    attach_server_tap(hub)
    try:
        with caplog.at_level(logging.DEBUG):
            ServerEvents(APP_CHANNEL).emit(
                lambda: CaptureEnabled(path=ConfiguredPath(str(tmp_path)))
            )
    finally:
        detach_server_tap(hub)

    (streamed,) = await delivered(subscription)
    assert isinstance(streamed, Streamed)
    record = only(caplog, declaration_of(CaptureEnabled).name)

    carried = {
        name: value
        for name, value in streamed.fields.items()
        if name not in STREAM_FIELDS
    }
    assert carried == fields_of(record)
    assert streamed.fields["level"] == "WARNING"
    # An instant off this host rather than the monotonic reading the
    # emission carries, and readable as one.
    assert streamed.fields["ts"].endswith("+00:00")


def test_no_declared_field_is_named_after_one_the_stream_owns() -> None:
    """The two fields the stream adds are written after the payload, so
    a declaration that used either name would be silently overwritten on
    the way to a reader while the retained log kept it. There is no such
    declaration, and this is what keeps it that way."""
    named = {
        (declaration.name, declared.name)
        for declaration in declarations().values()
        for variant in declaration.variants
        for declared in declared_values(variant)
        if declared.name in STREAM_FIELDS
    }

    assert named == set()


# --- the filters ------------------------------------------------------


async def test_a_device_filter_admits_that_device_alone(hub: LiveEvents) -> None:
    subscription = hub.subscribe(Filters(device=MAC))

    hub.emit(emission("mine", device=MAC))
    hub.emit(emission("theirs", device="11:22:33:44:55:66"))
    # And an event that names no device at all, which is the server's
    # own life rather than this board's.
    hub.emit(emission("nobody's"))

    assert events_of(await delivered(subscription)) == ["mine"]


async def test_a_session_filter_admits_that_session_alone(hub: LiveEvents) -> None:
    subscription = hub.subscribe(Filters(session=SESSION))

    hub.emit(emission("mine", session=SESSION))
    hub.emit(emission("theirs", session="f" * 32))
    hub.emit(emission("nobody's"))

    assert events_of(await delivered(subscription)) == ["mine"]


async def test_the_level_is_a_threshold_and_info_is_the_default(hub: LiveEvents) -> None:
    quiet = hub.subscribe()
    loud = hub.subscribe(Filters(level=logging.WARNING))
    everything = hub.subscribe(Filters(level=logging.DEBUG))

    hub.emit(emission("debug", level=logging.DEBUG))
    hub.emit(emission("info", level=logging.INFO))
    hub.emit(emission("warning", level=logging.WARNING))

    assert Filters().level == DEFAULT_LEVEL
    assert events_of(await delivered(quiet)) == ["info", "warning"]
    assert events_of(await delivered(loud)) == ["warning"]
    assert events_of(await delivered(everything)) == ["debug", "info", "warning"]


def test_the_level_names_are_the_catalog_s_own() -> None:
    """A reader may ask for exactly the levels an event may be emitted
    at, because the mapping is derived from the catalog's set rather
    than written out beside it."""
    assert set(LEVELS) == {"DEBUG", "INFO", "WARNING", "ERROR"}
    assert LEVELS["WARNING"] == logging.WARNING


# --- what a reader that falls behind is told --------------------------


async def test_a_full_queue_overwrites_the_oldest_and_says_how_many() -> None:
    small = LiveEvents(capacity=2)
    subscription = small.subscribe()

    for number in range(5):
        small.emit(emission(f"event-{number}"))

    seen = await delivered(subscription)
    # The count arrives first, because that is where in the stream the
    # loss happened, and the two survivors are the newest.
    assert seen[0] == Dropped(3)
    assert events_of(seen) == ["event-3", "event-4"]

    # And the counter is reset by the delivery, so the next report is
    # about what has been lost since.
    small.emit(emission("later"))
    later = await delivered(subscription)
    assert events_of(later) == ["later"]
    assert not [item for item in later if isinstance(item, Dropped)]
    small.close()


def test_the_capacity_is_documented_and_the_same_for_every_reader() -> None:
    assert LiveEvents().capacity == CAPACITY == 256


async def test_emitting_from_a_foreign_thread_never_waits_on_the_loop(
    hub: LiveEvents,
) -> None:
    """The tap contract's whole point, in the shape it really happens
    in: the conversation store's writer thread emits while this loop is
    busy, and a subscriber that is not reading must cost that thread
    nothing.

    The loop is parked on a blocking wait for the duration, so nothing
    the hub scheduled on it can run. The emitter finishing anyway is the
    assertion.
    """
    subscription = hub.subscribe()
    finished = threading.Event()

    def emitting() -> None:
        for number in range(CAPACITY * 2):
            hub.emit(emission(f"event-{number}"))
        finished.set()

    thread = threading.Thread(target=emitting)
    thread.start()
    try:
        assert finished.wait(timeout=5), "emit blocked on a reader that was not reading"
    finally:
        thread.join(timeout=5)

    # And nothing was lost silently: the reader is told what it missed
    # while the loop was parked.
    seen = await delivered(subscription)
    assert seen[0] == Dropped(CAPACITY)
    assert len(seen) == CAPACITY + 1


# --- ending, both ways -------------------------------------------------


async def test_unsubscribing_detaches_the_reader_and_ends_its_stream(
    hub: LiveEvents,
) -> None:
    subscription = hub.subscribe()
    assert hub.subscribers == 1

    hub.unsubscribe(subscription)

    assert hub.subscribers == 0
    hub.emit(emission("after"))
    # The stream ends rather than hanging on a hub that is no longer
    # feeding it.
    assert [item async for item in subscription] == []


async def test_a_reader_is_woken_and_ended_by_a_close(hub: LiveEvents) -> None:
    """What keeps a shutdown from waiting on an open tail: the reader is
    parked on the next event, and the close is what returns it."""
    subscription = hub.subscribe()
    reading = asyncio.create_task(anext(aiter(subscription), None))
    await asyncio.sleep(0)
    assert not reading.done()

    hub.close()

    assert await asyncio.wait_for(reading, timeout=5) is None
    assert hub.subscribers == 0


async def test_a_close_delivers_what_was_already_queued(hub: LiveEvents) -> None:
    """Ending is not discarding: an event the hub took before the close
    still reaches the reader that was already holding it."""
    subscription = hub.subscribe()
    hub.emit(emission("before"))

    hub.close()

    assert [item.fields["event"] async for item in subscription] == ["before"]


async def test_subscribing_to_a_closed_hub_answers_a_stream_that_is_over(
    hub: LiveEvents,
) -> None:
    hub.close()

    subscription = hub.subscribe()

    assert subscription.ended
    assert [item async for item in subscription] == []
    assert hub.subscribers == 0


async def test_the_subscriber_count_is_the_readers_it_is_feeding(
    hub: LiveEvents,
) -> None:
    assert hub.subscribers == 0
    first = hub.subscribe()
    second = hub.subscribe()
    assert hub.subscribers == 2

    hub.unsubscribe(first)
    assert hub.subscribers == 1
    hub.unsubscribe(second)
    assert hub.subscribers == 0


async def test_emitting_with_nobody_watching_is_a_no_op(hub: LiveEvents) -> None:
    """No reader, no queue and no wakeup. What the dispatcher pays is
    its own copy and this hub's lock, which is the cost the plan takes
    knowingly; what it does not pay is a stamp and an append per event
    nobody asked for."""
    hub.emit(emission("unheard"))

    subscription = hub.subscribe()
    assert await delivered(subscription) == []


async def test_a_streamed_object_is_shared_and_never_the_emitter_s_payload(
    hub: LiveEvents,
) -> None:
    """Two readers of one event get one object, and it is not the dict
    the emission carried: the dispatcher hands this tap its own copy,
    and the stream's two fields are added to a dict of its own."""
    first = hub.subscribe()
    second = hub.subscribe()
    one = emission("shared")

    hub.emit(one)

    (to_first,) = await delivered(first)
    (to_second,) = await delivered(second)
    assert isinstance(to_first, Streamed)
    assert to_first is to_second
    assert to_first.fields is not one.payload
