"""What the server says about itself, in one place, to whoever is listening.

The structured JSON records are the observability surface
([ADR](../../docs/adr/2026-08-04-json-logs-are-the-observability-surface.md)),
which makes them output rather than a debugging aid: their channel, their
sentences, their levels and their field names are a compatibility surface,
and the retained logs are the transcript store until v3 brings a real one.
Yet the machinery serving them used to belong to one subsystem, sitting in
`device/events.py` and used by the device edge and the pipeline while every
other module hand-built an `extra={...}` dict of its own (#138).

So the emitter moved here, to an altitude neither the device edge nor a
runtime owns: `device/`, `runtime/` and every server subsystem import
downward into this module, and it imports none of them. It also stopped
being a payload factory that call sites logged around and became the thing
that emits: a site says `events.info("heard %r", text, event="heard", ...)`
and the emitter builds the payload, wraps it, and hands it to every
attached consumer.

A consumer is a **tap**. The JSON log is the first one and always attached;
the capture's decision track is the second; #120's conversation store and
the #66/#67 exporters attach as more, without touching a single emit site.
That is what the interface exists for.

Three invariants shape the dispatch, and none of them is incidental:

- **An event that is logged is an event that is recorded.** The capture
  used to be written inside the payload builder, before the logging call
  returned, so the record was offered to it first by construction. The
  taps therefore dispatch non-log taps in attachment order FIRST and the
  log LAST, which preserves that ordering exactly.
- **A broken consumer breaks nothing else.** Each tap runs under its own
  guard, so a tap that raises does not starve the taps after it, the log
  above all. The failure is reported once as a plain sentence on the
  emitter's own channel: not an event, because an event would go back
  through the taps and a broken tap would recurse into itself.
- **No consumer can rewrite what is kept.** Every non-log tap is handed
  its own deep copy of the payload, so a tap that edits a nested value,
  or adds a key `logging` reserves, changes only its own copy. The
  retained log is not reachable from code this module does not own.

The tap contract is events only. `SessionEvents.vad()` and `.dropped()`
stay capture-specific side channels: they feed the capture's VAD and drop
tracks, which are sampled per frame and which no other consumer has a
meaning for.

Two scopes, and the difference between them is a clock. A session event
carries the session's identity and is stamped with the session loop's
clock, because the capture's audio tracks are aligned by it. A server
event names what it is about explicitly (`device=`, `entry=`, `host=`) and
is stamped with `time.monotonic`, because server events fire where no loop
is running: `create_app` reports its capture directory before the server
serves anything.
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any, Protocol

# The session log channel, by name rather than by `__name__`.
#
# `logs.py` emits `record.name` as the `logger` field of every JSON
# record, and retained JSON logs are the transcript store until v3
# brings a real one, so that field is output. Every conversation record
# has carried `samtal_server.session` since the whole session was one
# module, and splitting the code across `device/` and `runtime/` must
# not silently rename it. Naming the channel here says what it is
# rather than which file it happens to live in, and both packages log
# conversation lines through it instead of through a module logger.
SESSION_LOGGER = "samtal_server.session"

logger = logging.getLogger(SESSION_LOGGER)


@dataclass(frozen=True)
class Emission:
    """One event, complete: everything any consumer could need.

    `payload` is the finished structured dict, the JSON object's own
    keys. `at` is a monotonic reading from the emitter's clock, which is
    what places an event on the capture's timeline. `level`, `message`
    and `args` are the numeric level and the human sentence exactly as an
    ordinary logging call would have received them, unrendered, so the
    log tap can reproduce today's record byte for byte and a consumer
    that only wants the structure reads one field and ignores the rest.
    """

    payload: dict[str, Any]
    at: float
    level: int
    message: str
    args: tuple[Any, ...]


class EventTap(Protocol):
    """One consumer of the structured events."""

    def emit(self, emission: Emission) -> None: ...


class SessionRecording(Protocol):
    """The three methods a session capture answers, as this module sees
    them.

    Described here rather than imported from `capture.py`, and the
    reason is the direction of the arrows. This module is the one every
    subsystem imports downward into, `capture.py` among them once it
    emits its own events, and an import back the other way is a cycle
    that shows up at boot as a partially initialized module rather than
    as anything a reader would recognize. A capture reaches this module
    as an object anyway, which is the whole point of the tap, so what
    was ever needed here was the shape, and a structural type is
    exactly the shape.
    """

    def event(self, payload: dict[str, Any], now: float) -> None: ...

    def vad(
        self, speech_ms: float, listening: bool, replying: bool, now: float
    ) -> None: ...

    def dropped(self, reason: str, now: float) -> None: ...


class LogTap:
    """The tap the surface is named after: one logging call per event,
    on the channel the emitter was built for, with the payload riding
    `extra=` the way every call site used to attach it by hand.

    Always attached, and always last, so nothing is written to the log
    that the consumers before it were not offered first."""

    def __init__(self, channel: logging.Logger) -> None:
        self._channel = channel

    def emit(self, emission: Emission) -> None:
        self._channel.log(
            emission.level, emission.message, *emission.args, extra=emission.payload
        )


class CaptureTap:
    """The capture's decision track, as a tap.

    A thin wrapper rather than the capture itself, because a capture is
    a recording of one session and a tap is a consumer of events: the
    capture keeps the surface it has (`event(payload, at)`), and this is
    the adapter that makes it one of many."""

    def __init__(self, capture: SessionRecording) -> None:
        self.capture = capture

    def emit(self, emission: Emission) -> None:
        self.capture.event(emission.payload, emission.at)


def session_clock() -> float:
    """The session loop's own clock, which is the one the capture's
    tracks are aligned by: an event's offset into the recorded audio is
    only meaningful against the clock the audio was stamped with.

    `time.monotonic` where no loop is running. A session only exists
    inside the loop, so that case is a session built outside one, which
    the conformance tests do to check the boundary's shape; the
    activation such a construction runs emits an event, no capture is
    attached for its reading to be compared against, and the number is
    read by nobody. Raising there instead would make building a session
    an act that needs a loop, which it never has been."""
    try:
        return asyncio.get_running_loop().time()
    except RuntimeError:
        return time.monotonic()


def _offer(tap: EventTap, emission: Emission, channel: logging.Logger) -> None:
    """Hand one emission to one tap, under that tap's own guard.

    A tap that raises is reported once on `channel` as a plain sentence,
    and the taps after it still run: a consumer nobody has met yet must
    not be able to cost the operator a log line."""
    try:
        tap.emit(emission)
    except Exception as exc:  # noqa: BLE001 - a consumer never breaks the surface
        # The class names and nothing else, and no `event` field: a
        # report that went back through the taps would let a broken
        # tap recurse into itself, and a tap may be an exporter
        # holding whatever a far side answered it with.
        channel.warning(
            "an event tap (%s) failed and was skipped: %s",
            type(tap).__name__,
            type(exc).__name__,
        )


def _dispatch(
    taps: tuple[EventTap, ...], log: LogTap, emission: Emission, channel: logging.Logger
) -> None:
    """Offer one emission to every consumer, the log last.

    Each non-log tap is handed its own deep copy of the payload, and the
    log is handed the payload the emitter built. The frozen dataclass
    only stops a tap rebinding a field; the dict behind `payload` is
    ordinary and shared, so without this a tap could rewrite a nested
    value, or add a key `logging` reserves, and the line the operator
    keeps would be the one that tap chose. A consumer is by definition
    code this module does not own, and the retained log must not be
    reachable from it.

    Deep rather than shallow: the top level is where a reserved key
    would land, but `prompt_assembled` already carries a nested dict and
    a shallow copy would share it. The cost is one copy of a small dict
    per non-log tap, and none at all in the common case of no tap
    attached. `args` are deliberately not copied: they are rendered by
    `%` into a string and never written back, and copying an arbitrary
    argument is a copy that can fail.
    """
    for tap in taps:
        _offer(tap, replace(emission, payload=deepcopy(emission.payload)), channel)
    _offer(log, emission, channel)


class SessionEvents:
    """One conversation's observability: its identity, its log channel,
    and the consumers of what it emits.

    Its device identity and its capture attach in stages, because the
    events emitted before each stage have to carry what they carry
    today: the bad-Device-Id rejection names no device because none was
    understood, the no-agent rejection names one because by then the MAC
    is known, and `session_open` is the first line of the decision track
    because the capture opens just before it.

    Observability is orthogonal to the device-facing boundary: both
    sides emit events, and both sides' events have to look the same and
    reach the same places. So this is not a method on `DeviceOutput`; it
    is handed to a runtime at construction, alongside it.
    """

    def __init__(
        self, session_id: str, clock: Callable[[], float] = session_clock
    ) -> None:
        self.session_id = session_id
        # The device's MAC, written by the edge as soon as it is
        # normalized, so a rejection that follows names the device it
        # turned away.
        self.device: str | None = None
        # The agent currently talking. Written by the runtime when it
        # activates one, read by events either side emits: the frame
        # pacer stamps `speaking_started` on the edge but has to name
        # the agent active at fire time, which a tool-only handover
        # before the first audio makes a different one.
        self.agent: str | None = None
        # An explicit dependency rather than an assumption, so what
        # stamps an event is visible at construction and swappable in a
        # test.
        self._clock = clock
        self._taps: list[EventTap] = []
        self._log = LogTap(logger)
        self._capture: SessionRecording | None = None
        self._capture_tap: CaptureTap | None = None

    # --- the consumers ------------------------------------------------

    def attach(self, tap: EventTap) -> None:
        """Add a consumer. Attached rather than passed at construction
        because consumers arrive partway through a session (the capture
        opens during the handshake) and the events before that point are
        still events."""
        self._taps.append(tap)

    def detach(self, tap: EventTap) -> None:
        """Remove a consumer, leaving the events flowing. Detaching one
        that is not attached is not an error: a caller unwinding does
        not have to remember whether it got that far."""
        with contextlib.suppress(ValueError):
            self._taps.remove(tap)

    def attach_capture(self, capture: SessionRecording) -> None:
        """Begin recording the decision track. The capture keeps its own
        pair of methods rather than being attached as a bare tap,
        because `vad` and `dropped` below need the capture itself.

        A second capture replaces the first rather than joining it. One
        session records once, so two attached captures can only mean a
        caller that attached twice; leaving the first adapter in the tap
        list would keep writing to a recording nobody is going to close,
        while `vad` and `dropped` went to the second one, which is a
        recording split down the middle. Replacing rather than refusing,
        because there is a legitimate second attach in reach (a capture
        that rolls over at its size limit) and refusing would make that
        a caller's problem to sequence.
        """
        self.detach_capture()
        self._capture = capture
        self._capture_tap = CaptureTap(capture)
        self.attach(self._capture_tap)

    def detach_capture(self) -> None:
        """Stop recording. Called before the capture is closed, so the
        last line of the track is whatever the session emitted last."""
        if self._capture_tap is not None:
            self.detach(self._capture_tap)
        self._capture_tap = None
        self._capture = None

    def now(self) -> float:
        """The reading this session's events are stamped with.

        For a record that has to land on the same timeline as an event
        without being one: #120's turn record is stamped where `heard`
        is emitted, and reading the clock through here is what makes
        them the same clock rather than two that happen to agree."""
        return self._clock()

    # --- what a call site says ----------------------------------------

    def debug(self, message: str, *args: Any, event: str, **fields: Any) -> None:
        self._emit(logging.DEBUG, message, args, event, fields)

    def info(self, message: str, *args: Any, event: str, **fields: Any) -> None:
        self._emit(logging.INFO, message, args, event, fields)

    def warning(self, message: str, *args: Any, event: str, **fields: Any) -> None:
        self._emit(logging.WARNING, message, args, event, fields)

    def error(self, message: str, *args: Any, event: str, **fields: Any) -> None:
        self._emit(logging.ERROR, message, args, event, fields)

    def _emit(
        self,
        level: int,
        message: str,
        args: tuple[Any, ...],
        event: str,
        fields: dict[str, Any],
    ) -> None:
        """The one path every conversation event takes: what every one
        of them carries, then this event's own fields, then every
        consumer in turn with the log last."""
        payload = {
            "event": event,
            "session": self.session_id,
            "device": self.device,
            **fields,
        }
        emission = Emission(
            payload=payload, at=self._clock(), level=level, message=message, args=args
        )
        _dispatch(tuple(self._taps), self._log, emission, logger)

    # --- the capture's own tracks, which are not events ---------------

    def vad(self, speech_ms: float, listening: bool, replying: bool) -> None:
        """One sample of what the endpointer currently believes, for the
        capture's VAD track. Fed by the runtime, which is the side that
        owns the endpointer.

        Outside the tap contract deliberately: this is sampled once per
        mic frame, it is a track of the recording rather than a decision
        the server made, and a consumer of events has no meaning for
        it."""
        if self._capture is None:
            return
        self._capture.vad(speech_ms, listening, replying, self._clock())

    def dropped(self, reason: str) -> None:
        """One mic frame the session did not use, and why. Part of the
        evidence the capture exists for: the frames dropped before the
        decode are precisely the ones that explain a misfire. Outside
        the tap contract for the reason `vad` gives."""
        if self._capture is None:
            return
        self._capture.dropped(reason, self._clock())


class ServerEvents:
    """The events a subsystem emits about itself, outside any
    conversation: OTA check-ins, onboarding, the MCP lifecycle, the
    provider registry, the API.

    No session and no device defaults: a server-scoped event names what
    it is about explicitly (`device=`, `entry=`, `host=`, `path=`),
    which is what every hand-built site already did. One emitter per
    subsystem, built on that subsystem's existing module logger name, so
    the `logger` field of every record it emits is the one it always
    was.
    """

    def __init__(
        self, channel: str, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.channel = channel
        self._logger = logging.getLogger(channel)
        self._log = LogTap(self._logger)
        # `time.monotonic` rather than a loop clock: server events fire
        # before any loop runs, and `asyncio.get_running_loop()` would
        # raise there.
        self._clock = clock
        _hub.register(self)

    def debug(self, message: str, *args: Any, event: str, **fields: Any) -> None:
        self._emit(logging.DEBUG, message, args, event, fields)

    def info(self, message: str, *args: Any, event: str, **fields: Any) -> None:
        self._emit(logging.INFO, message, args, event, fields)

    def warning(self, message: str, *args: Any, event: str, **fields: Any) -> None:
        self._emit(logging.WARNING, message, args, event, fields)

    def error(self, message: str, *args: Any, event: str, **fields: Any) -> None:
        self._emit(logging.ERROR, message, args, event, fields)

    def _emit(
        self,
        level: int,
        message: str,
        args: tuple[Any, ...],
        event: str,
        fields: dict[str, Any],
    ) -> None:
        emission = Emission(
            payload={"event": event, **fields},
            at=self._clock(),
            level=level,
            message=message,
            args=args,
        )
        _dispatch(tuple(_hub.taps), self._log, emission, self._logger)


class _ServerHub:
    """Where a consumer of server-scoped events attaches, once, for all
    of them.

    Every subsystem has an emitter of its own, on its own channel, so a
    consumer would otherwise have to discover and mutate each module's
    private one. The hub holds the tap set and the emitters read it at
    emit time, which is what makes attachment order irrelevant: an
    emitter built after a consumer attached is served by the same set.
    """

    def __init__(self) -> None:
        self.taps: list[EventTap] = []
        # What exists to emit, for a consumer that wants to know. Kept
        # deliberately, even though dispatch does not need it: "which
        # channels does this server speak on" is otherwise a question
        # only the import graph can answer.
        self.emitters: list[ServerEvents] = []

    def register(self, emitter: ServerEvents) -> None:
        self.emitters.append(emitter)


_hub = _ServerHub()


def attach_server_tap(tap: EventTap) -> None:
    """Consume every server-scoped event, from every subsystem, whether
    its emitter already exists or is built later."""
    _hub.taps.append(tap)


def detach_server_tap(tap: EventTap) -> None:
    """Stop consuming them. Detaching one that is not attached is not an
    error, for the reason `SessionEvents.detach` gives."""
    with contextlib.suppress(ValueError):
        _hub.taps.remove(tap)


def server_emitters() -> tuple[ServerEvents, ...]:
    """Every server emitter built so far, in construction order."""
    return tuple(_hub.emitters)
