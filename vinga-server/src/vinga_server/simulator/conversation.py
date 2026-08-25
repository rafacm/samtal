"""One turn of a conversation, held over a real websocket, as a board
holds it.

The device's leg of the xiaozhi protocol: the handshake and its four
headers, the hello exchange, one utterance of the packaged Opus paced the
way a microphone would deliver it, and reading until the reply ends or a
bound expires. Half duplex and no barge-in, which is what a simulator
with no playback can honestly be.

**This module holds no copy of the protocol.** The messages are
`protocol/messages.py`'s models, built and parsed by that module's own
functions; the framing is `protocol/framing.py`; the audio is
`simulator/utterance.py`. What is here is the ORDERING, which is the one
thing neither of those knows.

**The ordering is a machine, not a sequence of reads.** A simulator that
took messages in whatever order they arrived could not say what went
wrong, so the eight states below are declared with their transitions, and
two rules make it a machine rather than a list. A message that arrives in
a state that does not expect it is reported by this side's own name for
it and advances nothing, which is what the firmware does with JSON it
does not understand. And every transition that waits has a bound, so no
state can be waited in forever.

**Nothing the far side wrote reaches a sentence**, and this is the module
where that is hardest, because everything here came from the far side.
The exceptions that prove the rule are the transcript and the reply's
sentences, which are the artifact this command exists to print. Beside
them: the close code is a number compared against a closed set and
reported by this side's word for it, the close reason is read and
discarded, an unmodelled message type is named as unmodelled rather than
quoted, and every websockets exception is reported by its class alone,
recorded inside its handler and raised outside it.

**The only `websockets` import in `src/` is here**, which is what makes
the `[sim]` extra a real gate: `config/cli.py` reaches this module inside
`run`'s own arm, and nothing else in the package imports it at all. An
`__init__` that re-exported this would drag the dependency into every
import of the simulator package, which is the gate defeating itself.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic, sleep
from typing import Literal

from websockets.exceptions import WebSocketException
from websockets.sync.client import connect

from vinga_server.config.loader import ConfigError
from vinga_server.device.watchdog import HELLO_TIMEOUT_S
from vinga_server.device_endpoint import REPORTED_WEBSOCKET
from vinga_server.logs import quieted
from vinga_server.protocol import framing
from vinga_server.protocol.messages import (
    AudioParams,
    DeviceHello,
    ListenMessage,
    ProtocolError,
    ServerHello,
    SttMessage,
    TtsMessage,
    UnknownMessage,
    built,
    parse_server_message,
)
from vinga_server.simulator.board import Identity
from vinga_server.simulator.utterance import Utterance

# The eight states this conversation passes through, in order.
#
# Named as a reader meets them, because these names are what a surprise
# is reported against: "an stt arrived while the reply was complete" is a
# sentence somebody can act on, and a number is not.
OPENED = "opened"

HELLO_SENT = "hello sent"

HELLO_RECEIVED = "hello received"

LISTENING = "listening"

AWAITING_REPLY = "awaiting reply"

SPEAKING = "speaking"

REPLY_COMPLETE = "reply complete"

CLOSED = "closed"

STATES = (
    OPENED,
    HELLO_SENT,
    HELLO_RECEIVED,
    LISTENING,
    AWAITING_REPLY,
    SPEAKING,
    REPLY_COMPLETE,
    CLOSED,
)

# What a binary frame is called where a message type would be. Audio has
# no `type` field because it is not a control message at all, and the
# machine has to be able to say a frame arrived somewhere it should not
# have.
AUDIO = "audio"

# What may arrive in each state, and where it leaves the conversation.
#
# A pair not in this table is a surprise: reported, and advancing
# nothing. Reading it is the whole of the ordering rule, which is why it
# is a table rather than a chain of conditions spread through the loop
# below.
#
# `stt` is expected on both sides of a `tts start` because the server
# does not promise an order between them, and `sentence_start` any number
# of times because a reply is as many sentences as the model wrote.
TRANSITIONS: dict[tuple[str, str], str] = {
    (HELLO_SENT, "hello"): HELLO_RECEIVED,
    (AWAITING_REPLY, "stt"): AWAITING_REPLY,
    (AWAITING_REPLY, "tts start"): SPEAKING,
    (AWAITING_REPLY, AUDIO): AWAITING_REPLY,
    (SPEAKING, "stt"): SPEAKING,
    (SPEAKING, "tts sentence_start"): SPEAKING,
    (SPEAKING, "tts stop"): REPLY_COMPLETE,
    (SPEAKING, AUDIO): SPEAKING,
}

# The bounds, each with the reason that picked it.
#
# The open and the hello share the server's own hello bound, read from
# the module that declares it rather than restated: the far side gives up
# there, so waiting longer learns nothing. `device/watchdog.py` is stdlib
# only, so importing it costs a client install nothing.
OPEN_TIMEOUT_S = HELLO_TIMEOUT_S

HELLO_TIMEOUT = HELLO_TIMEOUT_S

# And the reply, which is the one wait with nothing on either side to
# derive it from: a model and a text-to-speech engine have no bound this
# client can compute, and an unbounded wait is exactly what
# `server.limits.idle_timeout_s` exists on the server to end. Half of
# that default, because this is one turn rather than a session, and a
# person sitting at a terminal has stopped believing in the reply long
# before a minute is up.
REPLY_CEILING_S = 60.0

# How long a close is given to complete before the socket is dropped. The
# same reason a connect has a bound: a peer that will not finish a
# closing handshake must not hold a command open.
CLOSE_TIMEOUT_S = 5.0

# The libraries that would narrate the socket, held quiet around it for
# the reason `device_endpoint.REQUEST_LOGGERS` gives: what they have to
# say is the address, and a log record is a retained surface in a way a
# terminal is not.
SOCKET_LOGGERS = ("websockets", "websockets.client")

# What each close code this side knows means, in this side's own words.
#
# A closed set, and the reason it is closed rather than formatted: a code
# is a number the peer chose and a reason is a string the peer wrote, so
# the number is looked up and the string is dropped. A code outside this
# set is reported as outside it, without being printed, because a number
# from the far side is still the far side's choice.
CLOSE_NAMES: dict[int, str] = {
    1000: "the session ended normally",
    1001: "the deployment said it was going away",
    1002: "the peer refused this side's framing",
    1003: "the peer refused the kind of data this side sent",
    1006: "the connection ended without a closing handshake",
    1011: "the deployment reported an error of its own",
}

UNKNOWN_CLOSE = "the connection closed with a code this client does not know"

NEVER_CLOSED = "the connection was still open when this command finished with it"

# What a mode this simulator listens in is called. Manual, and only
# manual: `auto` re-arms itself after each reply and `realtime` is the
# mode barge-in lives in, and neither is a turn this side can take.
LISTENING_MODE = "manual"


def cannot_open(kind: str) -> str:
    """What a handshake that did not complete says.

    The class name and nothing else, which is the same rule
    `device_endpoint.requested` applies to an HTTP failure and for the
    same reasons: the library puts the URI into its exceptions, a refused
    upgrade carries the peer's own status line, and the class is the part
    that says what happened.
    """
    return (
        f"cannot open a conversation with {REPORTED_WEBSOCKET}: the websocket handshake did "
        f"not complete ({kind}). A board is admitted by the check-in and refused at the "
        f"socket when the token it was handed is not the one this deployment expects, so "
        f"check that this MAC is still bound and check in again. The address is not repeated "
        f"here: it is what a device token would be sent to."
    )


def cannot_speak(kind: str) -> str:
    return (
        f"the conversation with {REPORTED_WEBSOCKET} ended before the reply did "
        f"({kind}). Nothing of what the peer said about it is repeated here."
    )


NO_HELLO = (
    f"{REPORTED_WEBSOCKET} accepted the socket and did not answer the hello inside the "
    f"window a board waits. Nothing further was sent."
)

BAD_HELLO = (
    f"{REPORTED_WEBSOCKET} answered the hello with a message this client cannot read as "
    f"one, so there is no session to speak in. What arrived is not quoted back: it is "
    f"whatever that address returned."
)

NO_REPLY = (
    "the reply did not finish inside the bound this command waits. What was heard before "
    "the bound is above; what the deployment was doing is its own logs' to say."
)


@dataclass(frozen=True)
class Reply:
    """What one turn produced, and what a verdict is written from.

    `transcript` and `sentences` are far-side text, and they are the one
    thing here that may be printed: they are the artifact this command
    exists to show. Everything else is a count, a duration or a name this
    side chose.
    """

    # What the deployment heard, as it announced it.
    transcript: str

    # What it said, sentence by sentence, in the order they arrived.
    sentences: tuple[str, ...]

    # The reply's audio, counted rather than decoded: no codec ships in
    # any tier this command runs from.
    packets: int

    audio_bytes: int

    audio_ms: int

    # How it ended, by this side's name for the code.
    closed: str

    # Every message that arrived in a state that did not expect it, named
    # by this side's words. Empty is the ordinary case, and a non-empty
    # one is a fact worth printing rather than a failure.
    surprises: tuple[str, ...]

    # Where the machine stopped, which is one of `STATES`.
    state: str


def converse(
    *,
    target: str,
    token: str,
    identity: Identity,
    version: int,
    said: Utterance,
    say: Callable[[str], None],
) -> Reply:
    """One turn, from the handshake to the close.

    `say` is how the transcript and each sentence reach a terminal as
    they arrive rather than at the end, which is the whole difference
    between watching a conversation and reading a report of one. Nothing
    else goes through it.

    `target` has already been through `device_endpoint.websocket_target`,
    which is what says a device token may be sent there at all. This
    function does not re-derive that judgement and does not print the
    address whatever happens.
    """
    with quieted(SOCKET_LOGGERS, QUIET_LEVEL):
        socket = _opened(target, token, identity)
        heard = _Heard()
        try:
            _turn(socket, heard, version=version, said=said, say=say)
        finally:
            # Closed before the verdict rather than after it, because the
            # close is where the code this side reports comes from, and
            # because a refusal in flight must not leave a socket open
            # behind it.
            _close(socket)
    return heard.reply(_close_name(socket.close_code))


def _opened(target: str, token: str, identity: Identity):
    """The handshake, with the four headers the firmware sets.

    `Protocol-Version` is sent because the firmware sends it. This server
    reads nothing from it, which is why no real-server case can prove it
    was sent and why the controlled peer in `tests/support/` exists.
    """
    problem: str | None = None
    opened = None
    try:
        opened = connect(
            target,
            additional_headers={
                "Authorization": f"Bearer {token}",
                "Device-Id": identity.mac,
                "Client-Id": identity.client_id,
                "Protocol-Version": "1",
            },
            open_timeout=OPEN_TIMEOUT_S,
            close_timeout=CLOSE_TIMEOUT_S,
        )
    except (WebSocketException, OSError, TimeoutError, ValueError) as exc:
        # Recorded here and raised below, so nothing walking a chain
        # finds the library's exception, its URI or a refused status line
        # behind this sentence.
        problem = cannot_open(type(exc).__name__)
    if opened is None:
        raise ConfigError(problem or cannot_open("no connection"))
    return opened


def _turn(
    socket, heard: "_Heard", *, version: int, said: Utterance, say: Callable[[str], None]
) -> None:
    """The machine, from `hello sent` to `reply complete`.

    Written as one function on purpose: the states are a sequence and
    splitting them across helpers would hide the one thing this module
    exists to make visible, which is the order. What it produces it
    writes into `heard`, so a turn that ends in a refusal still leaves
    the caller everything that had arrived by then.
    """
    _send(socket, built(_hello(version, said)))
    heard.state = HELLO_SENT

    hello = _server_hello(socket, heard)
    session = hello.session_id
    # The reply's own packet duration, announced by the far side. Read
    # where it is plausible and this side's own otherwise, which is
    # decision 3a's rule about remote numbers applied to one that is
    # printed rather than waited on: a server announcing a packet of nine
    # hundred million milliseconds would otherwise choose what this
    # command reports.
    heard.reply_frame_ms = _packet_duration(hello.audio_params, said.frame_duration_ms)

    heard.state = LISTENING
    _send(socket, built(_listen(session, "start")))
    for packet in said.packets:
        socket.send(framing.wrap(version, packet))
        # Paced the way a microphone delivers them, because that is what
        # the endpointer on the other side is measuring. A burst would be
        # the same bytes and not the same utterance.
        sleep(said.frame_duration_ms / 1000)
    _send(socket, built(_listen(session, "stop")))

    heard.state = AWAITING_REPLY
    _read_until_reply_ends(socket, heard, version=version, say=say)


def _close_name(code: int | None) -> str:
    """How the connection ended, in this side's own words.

    The code is looked up rather than printed and the peer's close reason
    is never read at all: a number is the peer's choice and a reason is
    the peer's prose, and a client that relayed either would be
    publishing far-side bytes on the one surface this command has.
    """
    if code is None:
        return NEVER_CLOSED
    return CLOSE_NAMES.get(code, UNKNOWN_CLOSE)


def _hello(version: int, said: Utterance) -> DeviceHello:
    """What this board announces.

    The framing version is the one the check-in reply named, and the
    audio parameters are what the packaged utterance was actually encoded
    at, read off the asset rather than written here. `features` is left
    empty, which is what says this board publishes no MCP tools of its
    own: it has no volume, no screen and no battery to act on.
    """
    return DeviceHello(
        type="hello",
        version=version,
        audio_params=AudioParams(
            sample_rate=said.sample_rate, frame_duration=said.frame_duration_ms
        ),
    )


def _listen(session: str, state: Literal["start", "stop"]) -> ListenMessage:
    """A listen, always naming the mode it listens in."""
    return ListenMessage(type="listen", session_id=session, state=state, mode=LISTENING_MODE)


def _server_hello(socket, heard: "_Heard") -> ServerHello:
    """The one message this conversation cannot go on without.

    Bounded by the server's own hello window: the far side gives up
    waiting for a device hello there, so a client waiting longer for the
    answer learns nothing.
    """
    while True:
        received = _received(socket, HELLO_TIMEOUT, NO_HELLO)
        if isinstance(received, bytes):
            heard.surprise(AUDIO)
            continue
        message = _readable(received)
        if message is None:
            # A hello this client cannot read is not a surprise to note
            # and go on from: there is no session id to speak in, so the
            # conversation is over. The refusal names no field and quotes
            # nothing, because what arrived is whatever that address
            # returned.
            raise ConfigError(BAD_HELLO)
        if isinstance(message, ServerHello):
            heard.state = HELLO_RECEIVED
            return message
        heard.surprise(_named(message))


def _read_until_reply_ends(
    socket, heard: "_Heard", *, version: int, say: Callable[[str], None]
) -> None:
    """Everything from the utterance's end to `tts stop`, or the bound.

    One deadline for the whole reply rather than one per message, because
    what a person is waiting for is the reply and a server that sent a
    sentence a second forever would satisfy any per-message bound.
    """
    deadline = monotonic() + REPLY_CEILING_S
    while heard.state is not REPLY_COMPLETE:
        left = deadline - monotonic()
        if left <= 0:
            raise ConfigError(NO_REPLY)
        received = _received(socket, left, NO_REPLY)
        if isinstance(received, bytes):
            heard.audio(received, version)
            continue
        message = _readable(received)
        if message is None:
            # Named as unreadable rather than quoted: a parse refusal is
            # exactly where far-side bytes would otherwise be repeated,
            # and past the hello a message this client cannot read is a
            # fact to report rather than a reason to stop.
            heard.surprise("a message this client cannot read as one")
            continue
        heard.record(message, say)


def _readable(received: str):
    """One text frame as a message, or None when it is not one.

    The parse refusal is dropped rather than relayed. It names a field
    and a rule and no value, so relaying it would be safe; what a reader
    of this command needs is this side's own word for what happened, and
    two vocabularies for one event is one more than a verdict can use.
    """
    try:
        return parse_server_message(received)
    except ProtocolError:
        return None


def _named(message) -> str:
    """One message as this side names it, and never as the far side did.

    A type this client models is named by the spelling this repository
    already uses for it; a type it does not model is named as unmodelled,
    because `UnknownMessage.type` is a string the peer wrote.
    """
    if isinstance(message, TtsMessage):
        return f"tts {message.state}"
    if isinstance(message, SttMessage):
        return "stt"
    if isinstance(message, ServerHello):
        return "hello"
    if isinstance(message, UnknownMessage):
        return "a message of a type this client does not model"
    return "mcp"


def _packet_duration(announced: AudioParams, ours: int) -> int:
    """How long one reply packet is, for the arithmetic that reports the
    reply's length.

    The far side's number where it is a plausible packet duration and
    this side's otherwise. Nothing here decodes anything, so this is the
    only thing the reported duration can be computed from, which is
    exactly why a remote value gets a rule rather than a cast.
    """
    if isinstance(announced.frame_duration, bool):  # pragma: no cover - pydantic types it
        return ours
    if 1 <= announced.frame_duration <= _LONGEST_PACKET_MS:
        return announced.frame_duration
    return ours


# The longest a single Opus packet can be, which is the codec's own
# maximum and therefore the honest ceiling on what an announcement can
# mean.
_LONGEST_PACKET_MS = 120

QUIET_LEVEL = logging.WARNING


def _send(socket, text: str) -> None:
    """One control message, as a websocket TEXT frame.

    Every JSON control message is text and `framing.wrap` reaches audio
    and nothing else. The two are different calls here so that a reader
    cannot mistake one for the other, and a case asserts it.
    """
    _guarded(lambda: socket.send(text))


def _received(socket, timeout: float, expired: str) -> str | bytes:
    """One frame, or this command's own sentence for a bound that came
    due or a socket that ended."""
    got: list[str | bytes] = []
    problem: str | None = None
    try:
        got.append(socket.recv(timeout=timeout))
    except TimeoutError:
        problem = expired
    except (WebSocketException, OSError, ValueError) as exc:
        problem = cannot_speak(type(exc).__name__)
    if problem is not None:
        raise ConfigError(problem)
    return got[0]


def _guarded(act: Callable[[], None]) -> None:
    """One send, with the library's exceptions contained the way every
    other boundary here contains them."""
    problem: str | None = None
    try:
        act()
    except (WebSocketException, OSError, ValueError) as exc:
        problem = cannot_speak(type(exc).__name__)
    if problem is not None:
        raise ConfigError(problem)


def _close(socket) -> None:
    """Give the socket back, and never raise doing it.

    A close that fails after a reply has been read is not a reason to
    lose the reply, and an exception out of a `finally` would replace
    whatever refusal was already in flight, taking the library's own
    message with it. The peer's close reason is not read at all.
    """
    try:
        socket.close()
    except Exception:
        pass


class _Heard:
    """What has arrived so far, and where the machine is.

    A class rather than a bag of locals because the reading loop, the
    hello wait and the verdict all read it, and threading six values
    through three functions would be the same state with more places to
    forget one.
    """

    def __init__(self) -> None:
        self.state: str = OPENED
        self.transcript: str = ""
        self.sentences: list[str] = []
        self.packets: int = 0
        self.audio_bytes: int = 0
        self.reply_frame_ms: int = 0
        self.surprises: list[str] = []

    def surprise(self, what: str) -> None:
        """A message that arrived where nothing expected it. Reported and
        advancing nothing, which is what the firmware does with JSON it
        does not understand."""
        self.surprises.append(f"{what} arrived while {self.state}")

    def record(self, message, say: Callable[[str], None]) -> None:
        """One message, against the table.

        The transition decides everything: a pair the table does not hold
        is a surprise whatever the message says, so a `tts stop` with no
        `start` before it neither ends the reply nor is silently dropped.
        """
        event = _named(message)
        moved = TRANSITIONS.get((self.state, event))
        if moved is None:
            self.surprise(event)
            return
        self.state = moved
        if isinstance(message, SttMessage):
            self.transcript = message.text
            say(f"heard: {message.text}")
        if isinstance(message, TtsMessage) and message.state == "sentence_start":
            said = message.text or ""
            self.sentences.append(said)
            say(f"said: {said}")

    def audio(self, frame: bytes, version: int) -> None:
        """One binary frame of the reply, counted and size-checked.

        Unwrapped through the server's own framing, so a frame that does
        not match the negotiated version is a surprise rather than a
        number added to a total. Nothing decodes it: no codec ships in
        any tier this command runs from, so what is reported about reply
        audio is arithmetic over frames.
        """
        moved = TRANSITIONS.get((self.state, AUDIO))
        if moved is None:
            self.surprise(AUDIO)
            return
        unwrapped: list[bytes] = []
        try:
            unwrapped.append(framing.unwrap(version, frame).payload)
        except framing.FramingError:
            pass
        if not unwrapped:
            self.surprise("a binary frame that does not match the negotiated framing")
            return
        self.state = moved
        self.packets += 1
        self.audio_bytes += len(unwrapped[0])

    def reply(self, closed: str) -> Reply:
        return Reply(
            transcript=self.transcript,
            sentences=tuple(self.sentences),
            packets=self.packets,
            audio_bytes=self.audio_bytes,
            audio_ms=self.packets * self.reply_frame_ms,
            closed=closed,
            surprises=tuple(self.surprises),
            state=self.state,
        )


__all__ = [
    "AUDIO",
    "AWAITING_REPLY",
    "BAD_HELLO",
    "CLOSED",
    "CLOSE_NAMES",
    "CLOSE_TIMEOUT_S",
    "HELLO_RECEIVED",
    "HELLO_SENT",
    "LISTENING",
    "LISTENING_MODE",
    "NEVER_CLOSED",
    "NO_HELLO",
    "NO_REPLY",
    "OPENED",
    "OPEN_TIMEOUT_S",
    "REPLY_CEILING_S",
    "REPLY_COMPLETE",
    "SOCKET_LOGGERS",
    "SPEAKING",
    "STATES",
    "TRANSITIONS",
    "UNKNOWN_CLOSE",
    "Reply",
    "cannot_open",
    "cannot_speak",
    "converse",
]
