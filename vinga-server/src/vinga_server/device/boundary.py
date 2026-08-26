"""The device-facing boundary: what the edge and a conversation runtime
may ask of each other, and nothing else.

Two protocols, in opposite directions. `SessionInput` is one
conversation runtime as the device edge sees it: audio in, the
protocol's turn-taking acts, and a lifecycle. `DeviceOutput` is the
device as a runtime sees it: show text, speak, pace, pause, and call the
device's own tools. Both are described in device terms, deliberately.
The test for whether something belongs here is the one the
device-facing-interface subrule states in
[the guidelines](../../../docs/architecture/guidelines.md#the-internal-interface-is-device-facing):
would it still exist if the backend were a telephone call to a human?

What that rules out is the trap this boundary exists to avoid. There is
no universal conversation interface: nothing here says `commit_audio`,
`set_turn_detection` or `truncate_response`, because a boundary that
grows those is a home-grown, slightly wrong copy of every runtime's own
session protocol
([ADR](../../../docs/adr/2026-08-10-normalize-the-hardware-edge.md)).

The boundary is inline awaited calls, not a frame queue. Every method is
called from where that code runs today, so ordering and backpressure are
what they were: audio is awaited from the serve loop, and incoming
frames buffer in the socket meanwhile. This is a seam, not a pipeline.

All PCM crossing it is s16le mono.
"""

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from vinga_server.events import SessionEvents

if TYPE_CHECKING:
    # The name only, for the one annotation below. `providers/__init__`
    # re-exports the provider registry, which builds every configured
    # engine's client, so reaching `providers.base` (which imports
    # nothing but the standard library) executes the whole provider
    # layer on the way. This module is a vocabulary of terms, and the
    # two readers that want the terms without the layer are the ones
    # this deferral exists for: `onboarding.origin` takes
    # `WEBSOCKET_PATH` from here and is imported by `vinga-server
    # config`, which renders its reference and its OpenAPI document
    # with no provider loaded (issue #143).
    #
    # The generation is deferred for exactly the same reason and one
    # more: `generation.py` holds the engines a world was built with, so
    # importing it here would put the provider layer back in the path
    # this deferral exists to keep clear.
    from vinga_server.generation import Generation
    from vinga_server.providers.base import ToolDef

# The rate the input side of the pipeline runs at: what devices send,
# and what a runtime is fed. Here rather than with the codecs because it
# is a term of the boundary: `SessionInput.audio` promises PCM at this
# rate, and both sides read the promise from one place.
PIPELINE_SAMPLE_RATE = 16000

# Where devices are served their conversation, which is the path the OTA
# reply sends them to and the path the websocket edge mounts on. A term
# of this boundary rather than of either side of it: the module that
# assembles the URL a device is handed and the module that answers on it
# have to agree, and neither owns the other.
WEBSOCKET_PATH = "/xiaozhi/v1/"


class DeviceGone(RuntimeError):
    """The device disconnected while the runtime was speaking to it.

    Raised by the edge in place of the transport's own disconnect, so a
    runtime never imports starlette to catch one. The edge translates
    both of the shapes a starlette socket produces, the disconnect it
    saw and the bare `RuntimeError` it raises for a send that came after
    the close, so a runtime catching this type alone catches every
    vanished device (#137). The reply body does exactly that.

    It still subclasses `RuntimeError`, which is what the one site that
    stayed broad relies on: the reply's closing `tts stop` pair
    suppresses `RuntimeError` around a device send, and this falls
    inside what it already swallows. The consequence is accepted and
    stated in the ADR: a broad `RuntimeError` catch also swallows a
    vanished device, which at that site is what is wanted. Filler
    playback used to be the second such site and no longer is: it
    catches this type alone, so a bare `RuntimeError` there is the
    local bug it always was and is reported as one (#182).
    """


class PlayableAudio:
    """An opaque batch of encoded, ready-to-send device audio.

    Produced and consumed only by the device edge. A runtime may test a
    batch for emptiness and concatenate batches, and never reads the
    contents: what a packet is, and whether it is Opus at all, is the
    edge's business.

    It exists because the output side cannot be a single
    `send_audio(pcm)`. The encoder buffers partial frames, so a chunk of
    reply PCM may produce no packet at all, and the filler arbitration
    is a runtime decision that turns on whether there was anything to
    play. This is the local packet list today's code already passes
    around, given a name. It is not a queue, and it never grows
    runtime-shaped methods.
    """

    __slots__ = ("_packets",)

    def __init__(self, packets: Sequence[bytes] = ()) -> None:
        self._packets = tuple(packets)

    @property
    def packets(self) -> tuple[bytes, ...]:
        """The packets, for the edge that made them. Not for a runtime:
        reading these is what "opaque" rules out."""
        return self._packets

    def __bool__(self) -> bool:
        return bool(self._packets)

    def __len__(self) -> int:
        return len(self._packets)

    def __add__(self, other: "PlayableAudio") -> "PlayableAudio":
        if not isinstance(other, PlayableAudio):
            return NotImplemented
        return PlayableAudio(self._packets + other._packets)

    def __repr__(self) -> str:
        return f"PlayableAudio({len(self._packets)} packets)"


@runtime_checkable
class SessionInput(Protocol):
    """One conversation runtime behind the device edge. PCM is s16le
    mono at `PIPELINE_SAMPLE_RATE`."""

    async def audio(self, pcm: bytes) -> None:
        """One decoded mic frame. Only called while the device is
        listening and the edge's guards passed."""

    async def listen_started(self) -> None:
        """The device asked to listen: reset utterance state. The
        listening mode is edge policy and does not cross."""

    async def listen_stopped(self) -> None:
        """Manual end of utterance. Mid-reply this is a deliberate act
        and cancels unconditionally."""

    async def device_aborted(self, reason: str | None) -> None:
        """Device abort: cancel the reply, reset the utterance."""

    def replying(self) -> bool:
        """A reply is in flight, generating or speaking.

        A query the edge's own jobs need (the barge-in-off frame guard,
        the idle watchdog), and one that passes the litmus: whether the
        far end is mid-answer is knowable on a phone call too."""

    async def drain(self, grace_s: float) -> bool:
        """Wait for a reply in flight to finish, whether it is already
        speaking or still generating; never cancels it, and swallows its
        failure (a reply that failed is a reply that finished). True
        when it finished within `grace_s`, or when none was in
        flight."""

    async def close(self) -> None:
        """The session is over: cancel the reply, release state."""


@runtime_checkable
class DeviceOutput(Protocol):
    """What the device can do for a runtime. Implemented by
    `DeviceSession`. Async methods that reach the socket raise
    `DeviceGone` when the device has disconnected.

    Device tool results deliberately do not arrive as events on the
    input side: the MCP envelope transport stays on the edge, and the
    runtime sees only a `ToolDef` and a `(content, is_error)` answer.
    """

    @property
    def output_sample_rate(self) -> int:
        """The rate `encode_audio` expects (24 kHz today)."""

    async def show_transcript(self, text: str) -> None:
        """The `stt` message: what the user was heard to say."""

    async def begin_speaking(self) -> None:
        """The `tts start`, once per reply, idempotent."""

    async def sentence_started(self, text: str) -> None:
        """The `tts sentence_start`: display what is about to be
        heard."""

    def encode_audio(self, pcm: bytes) -> PlayableAudio:
        """Feed reply PCM at `output_sample_rate` into the edge's
        encoder; the batch holds every packet that filled, possibly
        none. Synchronous, and sends nothing."""

    def flush_encoder(self) -> PlayableAudio:
        """Pad the encoder's pending partial frame with silence and
        encode it. Called at the end of every agent leg and after a
        filler clip; never on cancellation, and the encoder object is
        never reset between replies, since its few milliseconds of
        lookahead staying inside is what keeps it reusable."""

    async def send_audio(self, batch: PlayableAudio) -> None:
        """Pace the batch out at frame cadence, recording each packet on
        the capture's reply channel as it goes.

        On the reply's first non-empty batch this stamps and emits
        `speaking_started`, attributed to the agent the runtime has
        active, before the first pacing sleep, the pause-gate wait and
        the socket send. A cancel mid-batch abandons the unsent
        remainder."""

    async def finish_speaking(self) -> None:
        """End of reply: `tts start` if none was sent, then `tts stop`;
        marks conversational activity for the idle watchdog. A reply
        that never spoke still sends the pair, because the device leaves
        its speaking state on the stop."""

    def reply_started(self) -> None:
        """A new reply: reset per-reply speaking state (the started
        flag, the stamp, the tts-start latch). Does not touch the
        encoder."""

    def restart_pacing(self) -> None:
        """A new agent leg: restart the pacing clock."""

    def pause_output(self) -> None:
        """Hold the paced stream before its next frame."""

    def resume_output(self) -> None:
        """Resume, shifting the pacing clock by the pause so the stream
        picks up where it stopped rather than bursting to catch up."""

    def speaking_started_at(self) -> float | None:
        """The `speaking_started` stamp for this reply; None before it.
        Read by the refractory gate and by the filler."""

    def user_turn_ended(self) -> None:
        """The runtime decided the utterance ended. The edge applies its
        listen-mode policy: auto stops listening until the device
        re-arms, realtime keeps listening. The runtime never learns the
        mode names."""

    def device_tools(self) -> "Sequence[ToolDef]":
        """The device's discovered MCP tools, possibly empty."""

    async def call_device_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> tuple[str, bool]:
        """Invoke one device tool; `(content, is_error)`."""


# How one conversation runtime is built for one connection: the device
# to speak through, the session's observability, the agent names the
# device is bound to, and the world to build it from.
#
# The world is the fourth argument rather than something the factory
# looks up, and that is the whole of the generational binding (#191): a
# conversation is built from one generation, speaks through that
# generation's engines for the rest of its life, and is reported to the
# registry as holding it. A factory that read the current world for
# itself would leave the edge unable to say which one it got, and "which
# world is this conversation holding" is exactly what decides when the
# engines of a replaced one may be released.
#
# Deliberately not a config-selectable registry: one runtime exists, and
# a selection mechanism with one option is surface without a reader.
# This is the seam a second runtime plugs into, and what selection needs
# to express is decided when there is a second one.
RuntimeFactory = Callable[
    [DeviceOutput, SessionEvents, Sequence[str], "Generation"], SessionInput
]
