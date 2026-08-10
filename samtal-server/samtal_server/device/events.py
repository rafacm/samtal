"""What a conversation says about itself, in one object.

Observability is orthogonal to the device-facing boundary: both sides
emit events, and both sides' events have to look the same and reach the
same places. So it is not a method on `DeviceOutput`; it is handed to a
runtime at construction, alongside it.

Routing every event through one object is also what keeps the capture
invariant intact: an event that is logged is an event that is recorded
on the capture's decision track, whichever side emitted it.
"""

import asyncio
import logging
from typing import Any

from samtal_server.capture import SessionCapture

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


class SessionEvents:
    """The session's observability: its identity, its log channel, the
    structured event builder, and the capture's decision track.

    Its device identity and its capture hook attach in stages, because
    the events emitted before each stage have to carry what they carry
    today: the bad-Device-Id rejection names no device because none was
    understood, the no-agent rejection names one because by then the MAC
    is known, and `session_open` is the first line of the decision track
    because the capture opens just before it.
    """

    # The channel every conversation record is emitted on, whichever
    # package the call site lives in.
    log = logger

    def __init__(self, session_id: str) -> None:
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
        self._capture: SessionCapture | None = None

    def attach_capture(self, capture: SessionCapture) -> None:
        """Begin recording the decision track. Attached rather than
        passed at construction because the capture opens partway through
        the handshake, and the events before that point are still
        events."""
        self._capture = capture

    def detach_capture(self) -> None:
        """Stop recording, leaving the events flowing. Called before the
        capture is closed, so the last line of the track is whatever the
        session emitted last."""
        self._capture = None

    def event(self, event: str, **fields: Any) -> dict[str, Any]:
        """The structured half of a log line: what every conversation
        event carries, plus this event's own fields. Passed as `extra=`,
        so it is invisible in the text format and top-level keys in the
        JSON one.

        Every event goes through here, which is why the capture's
        decision track is hooked here too rather than at each call site:
        an event that is logged is an event that is recorded."""
        payload = {
            "event": event,
            "session": self.session_id,
            "device": self.device,
            **fields,
        }
        if self._capture is not None:
            self._capture.event(payload, asyncio.get_running_loop().time())
        return payload

    def vad(self, speech_ms: float, listening: bool, replying: bool) -> None:
        """One sample of what the endpointer currently believes, for the
        capture's VAD track. Fed by the runtime, which is the side that
        owns the endpointer, through the same object every event goes
        through."""
        if self._capture is None:
            return
        self._capture.vad(speech_ms, listening, replying, asyncio.get_running_loop().time())

    def dropped(self, reason: str) -> None:
        """One mic frame the session did not use, and why. Part of the
        evidence the capture exists for: the frames dropped before the
        decode are precisely the ones that explain a misfire."""
        if self._capture is None:
            return
        self._capture.dropped(reason, asyncio.get_running_loop().time())
