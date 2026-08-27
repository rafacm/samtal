"""What the server says about itself, in one place, to whoever is listening.

The structured JSON records are the observability surface
([ADR](../../../../docs/adr/2026-08-04-json-logs-are-the-observability-surface.md)),
which makes them output rather than a debugging aid: their channel, their
sentences, their levels and their field names are a compatibility surface.
They are metadata and nothing else: the record of what was said is the
conversation store (#120, and the [content and telemetry
ADR](../../../../docs/adr/2026-08-15-content-and-telemetry-are-separate-surfaces.md)),
and these events are the operator's live view of the same conversation,
correlated with it by session id.
Yet the machinery serving them used to belong to one subsystem, sitting in
`device/events.py` and used by the device edge and the pipeline while every
other module hand-built an `extra={...}` dict of its own (#138).

So the emitter moved here, to an altitude neither the device edge nor a
runtime owns: `device/`, `runtime/` and every server subsystem import
downward into this module, and it imports none of them. It also stopped
being a payload factory that call sites logged around and became the thing
that emits: a site says
`events.emit(lambda: Heard(agent=..., duration_s=Real(seconds)))`
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

And a site cannot say a thing `catalog.py` does not declare. It hands
`emit()` a thunk that builds a typed variant instead of restating a
template, an argument order, an event name and a field set: the
declaration owns all four, and every value is a type that refuses at
construction what it does not admit, so there is nothing left for a call
to get wrong (#155, #210). What there used to be was a validator that
read the finished payload back and judged it against a registry, and it
went with the duplication it existed to reconcile.

What survives at runtime is the guard, because construction can still
fail on a value. A refusal costs one plain sentence on the emitter's own
channel and the emission is dropped, so a telemetry bug can never cost a
reply. The thunk is what makes that possible: building, validating,
rendering and serializing all happen inside the guard, where
`emit(SomeVariant(...))` would have evaluated the constructor on
whatever path was emitting.
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any, Protocol

from vinga_server.events.catalog import Variant, declaration_of
from vinga_server.events.values import DeviceId, EventValue, SessionId

# The session log channel, by name rather than by `__name__`.
#
# `logs.py` emits `record.name` as the `logger` field of every JSON
# record, and the retained records are a compatibility surface, so that
# field is output: a collector filters on it. Every conversation record
# has carried `vinga_server.session` since the whole session was one
# module, and splitting the code across `device/` and `runtime/` must
# not silently rename it. Naming the channel here says what it is
# rather than which file it happens to live in, and both packages log
# conversation lines through it instead of through a module logger.
SESSION_LOGGER = "vinga_server.session"

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


def _report(
    channel: logging.Logger, level: int, message: str, *args: Any
) -> None:
    """Say one plain sentence about something that went wrong, and never
    fail while saying it.

    Every report this module makes is made from inside a guard: a tap
    raised, or an emission was refused. A logging call is not the inert
    operation it looks like, though. A
    filter or a handler is code somebody else installed, `handle` and
    `filter` are called unwrapped, and a formatter meets whatever the
    record carries, so the report can raise exactly where the guard has
    nothing left to catch it with. The tap report is the sharpest case:
    the failing tap is often the log tap itself, and reporting its
    failure back onto the same broken channel is the recursion the
    guard was built to stop.

    So the report is the last thing that may throw, and it does not.
    Suppressing blind is the right trade here and only here: what is
    being protected is a reply, and what is being lost is one diagnostic
    line about a diagnostic line."""
    try:
        channel.log(level, message, *args)
    except Exception:  # noqa: BLE001 - a report never costs a reply
        pass


def _offer(tap: EventTap, emission: Emission, channel: logging.Logger) -> None:
    """Hand one emission to one tap, under that tap's own guard.

    A tap that raises is reported once on `channel` as a plain sentence,
    and the taps after it still run: a consumer nobody has met yet must
    not be able to cost the operator a log line."""
    try:
        tap.emit(emission)
    except Exception:  # noqa: BLE001 - a consumer never breaks the surface
        # The tap's own class name and nothing else, and no `event`
        # field: a report that went back through the taps would let a
        # broken tap recurse into itself.
        #
        # Nothing about the exception, which is the correction PR #217's
        # review parked here. This used to name its class, and a class
        # name looks like the safest string in Python: `type(name,
        # (Exception,), {})` accepts any string as one, name validation
        # included, so a tap that is an exporter holding whatever a far
        # side answered it with can raise an exception whose NAME is
        # those bytes. The handler does not bind it either: what is
        # never looked at cannot leak later.
        #
        # The tap's own class name stays, and the asymmetry is the
        # point. A tap is an object this server's composition attached,
        # so its class is a program object rather than anything a caller
        # or a far side supplied, and which consumer is broken is the
        # whole of what makes this line actionable.
        _report(
            channel,
            logging.WARNING,
            "an event tap (%s) failed and was skipped",
            type(tap).__name__,
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


# --- what a refusal is allowed to say ---------------------------------
#
# Fixed codes and registry-owned names. Nothing else: a complaint that
# quoted what it rejected would put exactly the bytes this machinery
# exists to keep out of the retained log into the retained log.

# The one code a refusal carries beside the channel mismatch: a
# construction thunk that raised, so no variant exists at all.
#
# It carries NO detail, and that is a decision rather than an omission.
# The obvious detail would be the exception's class name, the way a
# failed tap is reported, and a class name looks like the safest string
# in Python. It is not: `type(name, (Exception,), {})` takes any string
# at all, name validation included, so a thunk that builds its exception
# out of far-side bytes can put those bytes in a class name and the
# refusal would print them. What a caller supplies is a caller's, and
# the surface says nothing about it.
CONSTRUCTION_FAILED = "construction_failed"

# The other, and the only thing a variant cannot check for itself:
# handed to an emitter on a channel it does not declare. Both halves of
# that comparison are the catalog's own.
WRONG_CHANNEL = "wrong_channel"

# And what an event whose construction never finished is called, since
# the thunk is opaque until it returns and a failed one names nothing.
UNBUILT_LABEL = "an event that could not be built"

# The one sentence a refusal renders: the fixed label above and the code
# that says which of the two things went wrong.
REFUSAL_MESSAGE = "the event schema refused an emission of %s: %s"


# --- the typed path ---------------------------------------------------
#
# Nothing in the guard below is an `assert`. `python -O` strips
# assertions, and an optimized production process silently losing its
# guard is exactly the quiet failure #155 exists to end.
#
# What a converted site does instead. It hands the emitter a THUNK
# rather than a constructed variant, and the difference is the whole
# point: building the variant, validating its values, rendering its
# sentence and serializing its payload all happen inside the guard
# below, so a construction failure on a reply path is telemetry's
# problem and never the reply's. `emit(SomeVariant(...))` would have
# evaluated the constructor at the call site, outside anything.
#
# There is no second validation step here. The declaration IS the
# check: a variant that exists has already proved its channel, its
# level, its template, its argument order and every one of its values,
# because none of them could have been constructed otherwise. What is
# left to check at emit time is the one thing a variant cannot know,
# which is whether it was handed to an emitter on the channel it
# declares.


@dataclass(frozen=True)
class Checked:
    """What the emitters dispatch: the payload, level, sentence and
    arguments a variant produced."""

    payload: dict[str, Any]
    level: int
    message: str
    args: tuple[Any, ...]


@dataclass(frozen=True)
class _Refusal:
    """What the construction guard made of an emission it could not
    build: the name a diagnostic may call it, and the code for what was
    wrong."""

    label: str
    code: str


def _identities(
    declared: Mapping[str, Callable[[], EventValue | None]],
) -> tuple[dict[str, EventValue | None], bool]:
    """Build the emitter's own values, under one guard.

    Answers what was built and whether all of it was. One guard around
    the loop rather than one per identity, because any identity failure
    refuses the emission whole: a conversation record missing the
    session it belongs to is a shape the declaration denies exists, and
    nothing is dispatched in its place for a half-built identity to
    decorate.

    Nothing raises. What a failed build was holding is never looked at,
    so there is nothing of it to leak by accident later.
    """
    held: dict[str, EventValue | None] = {}
    try:
        for name, build in declared.items():
            held[name] = build()
    except Exception:  # noqa: BLE001 - telemetry never costs a reply
        return held, False
    return held, True


def _construct(
    channel: str,
    held: dict[str, EventValue | None],
    build: Callable[[], Variant],
) -> Checked | _Refusal:
    """Build one variant and turn it into an emission, or answer what
    was wrong with it.

    Nothing here raises. Nothing about the exception leaves this
    function at all, its class name included: a class name is an
    ordinary string that `type()` accepts without validating, so an
    exception built from far-side bytes carries them in its name.
    """
    try:
        variant = build()
        # Before anything is rendered or serialized: a value in the
        # wrong field is a value on the surface under a name that
        # promises something else, and nothing outside this package
        # typechecks a construction.
        variant.verify()
        declaration = declaration_of(type(variant))
        if variant.CHANNEL != channel:
            # The one thing a variant cannot check for itself, and the
            # only check left at emit time: both halves are
            # registry-owned, the declaration's channel and the
            # emitter's own.
            return _Refusal(declaration.name, WRONG_CHANNEL)
        logged = variant.logged(held)
        return Checked(
            payload={
                "event": declaration.name,
                **{
                    name: None if value is None else value.carried()
                    for name, value in held.items()
                },
                **variant.payload(),
            },
            level=variant.LEVEL,
            message=logged.template,
            args=logged.args,
        )
    except Exception:  # noqa: BLE001 - telemetry never costs a reply
        # Deliberately unbound: the exception is not looked at, so there
        # is nothing of it to leak by accident later.
        pass
    return _Refusal(UNBUILT_LABEL, CONSTRUCTION_FAILED)


def _built(
    log: logging.Logger,
    channel: str,
    identities: Mapping[str, Callable[[], EventValue | None]],
    build: Callable[[], Variant],
) -> Checked | None:
    """One typed emission, its identity and its variant both constructed
    under the guard, or nothing at all where either refused.

    A refusal costs one sentence on the emitter's own channel and the
    emission is dropped: there is nothing to substitute for it, because
    what a site meant to say is exactly what could not be built. The
    sentence is built from registry-owned identifiers only: no
    caller-supplied name, no field value, no exception message, no
    exception CLASS name, and no partially rendered text, because a
    thunk that raised may have raised holding exactly the bytes this
    surface exists to keep out. The record it makes therefore names no
    session and no device either: an identity may itself be what
    refused, and only a validated one could have been echoed.

    A refusal is a schema bug rather than a runtime condition, and it is
    deterministic, so where it is found is development: the lanes fail
    any test whose run produced one of these reports.
    """
    held, whole = _identities(identities)
    outcome = (
        _construct(channel, held, build)
        if whole
        else _Refusal(UNBUILT_LABEL, CONSTRUCTION_FAILED)
    )
    if isinstance(outcome, Checked):
        return outcome
    _report(log, logging.ERROR, REFUSAL_MESSAGE, outcome.label, outcome.code)
    return None


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
        # The thread that agent is talking on, written by the same
        # activation and for the same reason: an event that names the
        # agent names the conversation it was speaking in, and both
        # sides of the boundary emit such events. A server-minted id and
        # therefore metadata, never content.
        self.conversation: str | None = None
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
        """This session's clock, read.

        For measuring an interval on the same clock the events are
        stamped with, which is what keeps a duration on the turn record
        comparable with the offsets around it. Deliberately not how a
        record lands on an event's instant: a second reading is a second
        instant, and the emit below answers with the one it stamped for
        exactly that reason."""
        return self._clock()

    # --- what a call site says ----------------------------------------
    #
    # Each answers the reading its event was stamped with, so a record
    # that has to sit at the same instant takes it from the emission
    # rather than sampling the clock again beside it. Almost every call
    # site ignores the answer, which costs nothing; the one that does
    # not is #120's turn record, whose offset has to equal its `heard`
    # event's exactly rather than to within however long the emit took.

    def emit(self, build: Callable[[], Variant]) -> float:
        """Say one typed conversation event, and answer the reading it
        was stamped with.

        A thunk, for the reason `ServerEvents.emit` gives: building,
        validating, rendering and serializing all happen inside the
        guard, so a construction failure is telemetry's problem rather
        than the reply's. The identity is a thunk of its own and built
        in the same place, because a session id and a device MAC are
        value types like any other and a malformed one must refuse where
        everything else refuses.

        The caller names the variant and its values. The session, the
        device, the event's name, its channel, its level, its sentence
        and the order of that sentence's arguments all come off the
        emitter and the declaration.

        The clock is read before the guard runs, so a refused emission
        still answers the instant it was made at: the one caller that
        reads the answer has a record to place whether or not the event
        it was placed beside survived.
        """
        at = self._clock()
        checked = _built(logger, SESSION_LOGGER, self._identities(), build)
        if checked is None:
            # A refusal was reported, and there is nothing to dispatch
            # in the emission's place: what the site meant to say is
            # exactly what could not be built.
            return at
        emission = Emission(
            payload=checked.payload,
            at=at,
            level=checked.level,
            message=checked.message,
            args=checked.args,
        )
        _dispatch(tuple(self._taps), self._log, emission, logger)
        return emission.at

    def _identities(self) -> dict[str, Callable[[], EventValue | None]]:
        """Whose conversation this is, as values.

        Thunks rather than values, because building one is what has to
        happen inside the guard; read at emit time rather than kept,
        because both halves move during a session: the device id is
        written by the edge as soon as the MAC is normalized, and every
        event before that names none.
        """
        return {
            "session": lambda: SessionId(self.session_id),
            "device": lambda: None if self.device is None else DeviceId(self.device),
        }

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

    def emit(self, build: Callable[[], Variant]) -> None:
        """Say one typed event.

        A thunk rather than a variant, because construction is part of
        what the guard has to cover: `emit(SomeVariant(...))` would
        evaluate the constructor at the call site, where a value that
        did not pass its own type would raise on whatever path was
        emitting. Here building, validating, rendering and serializing
        all happen inside `_built`.

        The caller names the variant and its values. Everything else,
        the event's name, its channel, its level, its sentence and the
        order of that sentence's arguments, comes off the declaration
        the variant belongs to, which is the whole of what a typed site
        stops having to restate correctly.
        """
        checked = _built(self._logger, self.channel, {}, build)
        if checked is None:
            # A refusal was reported, and there is nothing to dispatch
            # in the emission's place.
            return
        emission = Emission(
            payload=checked.payload,
            at=self._clock(),
            level=checked.level,
            message=checked.message,
            args=checked.args,
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
