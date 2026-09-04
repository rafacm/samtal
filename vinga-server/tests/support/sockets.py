"""Websockets that are not websockets, for driving a session in process.

A session speaks to its device through a small handful of coroutines,
so a test that wants to watch what a reply sent, or to hold a session
open while something else ends it, hands the session one of these
instead of a real socket. Each implements only the calls the session
actually makes on it, which is why there are several rather than one
configurable stand-in: what a test needs is a socket that records, a
socket that stays open, a socket that swallows everything, or one that
keeps the order a turn went out in.

These know nothing of the pipeline, so this module sits on
`tests.support.configs` and the standard library alone. A socket that
also has to be a device (answering tool calls, holding an encoder)
belongs in `boundary.py`, which is about the seam rather than the wire.
"""

import asyncio
import json
from typing import Any

from tests.support.configs import DEVICE_HELLO, DEVICE_MAC, DEVICE_UUID


class RecordingSocket:
    """Just enough websocket for `_speak`: it counts what went out."""

    def __init__(self) -> None:
        self.texts: list[str] = []
        self.frames = 0

    async def send_text(self, text: str) -> None:
        self.texts.append(text)

    async def send_bytes(self, data: bytes) -> None:
        self.frames += 1


def spoken(socket: RecordingSocket) -> list[str]:
    """What this device was told it is about to hear, sentence by
    sentence.

    A reply announces each sentence with a `tts sentence_start` before
    its audio, so this is the device-facing view of what was said, which
    is what makes it a test's view of it too. Announced rather than
    heard: a sentence cut off by a barge-in was announced and only
    partly played, so a suite about what the user actually heard asks a
    different question.
    """
    messages = [json.loads(text) for text in socket.texts]
    return [
        message["text"]
        for message in messages
        if message.get("type") == "tts" and message.get("state") == "sentence_start"
    ]


class LoopingSocket:
    """Enough websocket for `run`: the hello, then a receive that waits
    until something closes the session.

    The tests below that drive `run` directly are the ones about what
    ends a session from the server side, where a test client's own close
    would be a sixth cause racing the one under test.
    """

    def __init__(self) -> None:
        self.headers = {"device-id": DEVICE_MAC, "client-id": DEVICE_UUID}
        self.closed: tuple[int, str] | None = None
        self.inbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.inbox.put_nowait(
            {"type": "websocket.receive", "text": json.dumps(DEVICE_HELLO)}
        )

    async def accept(self) -> None:
        return None

    async def receive(self) -> dict[str, Any]:
        return await self.inbox.get()

    async def send_text(self, text: str) -> None:
        return None

    async def send_bytes(self, data: bytes) -> None:
        return None

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = (code, reason)
        self.inbox.put_nowait({"type": "websocket.disconnect"})


class QuietSocket:
    """Enough websocket for a whole reply to run against. Everything sent
    goes nowhere and nothing fails, so the only failure in the run is the
    one the test raises."""

    async def send_text(self, text: str) -> None:
        return None

    async def send_bytes(self, data: bytes) -> None:
        return None

    async def close(self, code: int, reason: str) -> None:
        return None


# What a frame is written down as, so a text message and a frame can
# share one list and still be told apart: no JSON message is this
# string, which is what makes the marker unambiguous.
FRAME = "\x00frame"


class OrderedSocket:
    """A websocket that keeps what went out AND the order it went out
    in: every text message as it was sent, every frame as `FRAME`.

    The third reading this module offers, and it exists because the
    other two answer questions this one cannot. `RecordingSocket` counts
    frames rather than placing them, and `spoken` reads the text
    messages with the frames already dropped, so neither can say that a
    sentence was announced before or after a clip, or that a turn's
    control messages arrived in the order the firmware waits on. A suite
    about what a device HEARD asks those two; a suite about the shape of
    a turn asks this one.
    """

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.closed: tuple[int, str] | None = None

    async def send_text(self, text: str) -> None:
        self.sent.append(text)

    async def send_bytes(self, data: bytes) -> None:
        self.sent.append(FRAME)

    async def close(self, code: int, reason: str) -> None:
        self.closed = (code, reason)

    @property
    def frames(self) -> int:
        """How many audio frames went out."""
        return self.sent.count(FRAME)

    def messages(self) -> list[dict[str, Any]]:
        """The text messages, parsed, with the frames dropped."""
        return [json.loads(one) for one in self.sent if one != FRAME]

    def announced(self) -> list[str]:
        """The sentences this device was told it is about to hear."""
        return [
            message["text"]
            for message in self.messages()
            if message.get("type") == "tts" and message.get("state") == "sentence_start"
        ]

    def control(self) -> list[tuple[str, str]]:
        """Every text message as its type and its state, which is the
        pair the firmware's own state machine reads."""
        return [
            (message.get("type", ""), message.get("state", ""))
            for message in self.messages()
        ]

    def shape(self) -> list[str]:
        """The whole turn in order, a control message as `type state`
        and a frame as `frame`, which is what an ordering claim is made
        of."""
        return [
            FRAME.lstrip("\x00")
            if one == FRAME
            else " ".join(
                part
                for part in (
                    json.loads(one).get("type", ""),
                    json.loads(one).get("state", ""),
                )
                if part
            )
            for one in self.sent
        ]

    def closing_stop(self) -> bool:
        """Whether the turn ended with the `tts stop` a device in auto
        mode waits on before it listens again."""
        return bool(self.control()) and self.control()[-1] == ("tts", "stop")


class CancellingSocket(OrderedSocket):
    """A device whose first frame is where a barge-in lands.

    Standing in for the cancellation arriving at exactly that await,
    which three real clocks would otherwise have to agree on. What it
    drives is the contract that a cancellation is not swallowed: raised
    here it must leave the send rather than be sanitized into a
    class-name report, and the reply's own `finally` must still get its
    closing message out.
    """

    async def send_bytes(self, data: bytes) -> None:
        raise asyncio.CancelledError

    @property
    def stops(self) -> int:
        """How many closing `tts stop` messages this device was sent,
        which is the number that must be exactly one."""
        return self.control().count(("tts", "stop"))
