"""Websockets that are not websockets, for driving a session in process.

A session speaks to its device through a small handful of coroutines,
so a test that wants to watch what a reply sent, or to hold a session
open while something else ends it, hands the session one of these
instead of a real socket. Each implements only the calls the session
actually makes on it, which is why there are three rather than one
configurable stand-in: what a test needs is a socket that records, a
socket that stays open, or a socket that swallows everything.

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
