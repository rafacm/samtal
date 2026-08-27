"""The seam-testing template: drive one side from a scripted far side.

The device edge and a conversation runtime meet at one boundary, and
the point of that boundary (#85) is that either side can be reasoned
about, and replaced, without the other. A test proves that by supplying
the far side itself, so what its assertions see is one side alone.

There are two directions, and this module holds one stand-in for each.
`StubRuntime` is a conversation runtime that is not a pipeline: no VAD,
no ASR, no model and no voice, answering whatever it is handed through
`DeviceOutput` alone, so a suite driving it over a real websocket is
watching the edge and nothing else. `FakeDevice` is a device that is
not a socket: no protocol, no codec, no clock, so a suite driving the
real runtime against it is watching the runtime and nothing else.

This is the pattern to copy when a new seam wants covering: name the
far side after the side it replaces, give it only the calls the near
side makes, and keep the one fact the seam turns on (here, that the
device's encoder emits nothing until a whole frame is there).

The frame size here is the output rate's, which is not the wire's:
`tests.support.configs.FRAME_BYTES` is a frame of 16 kHz microphone
audio, and `OUTPUT_FRAME_BYTES` below is a frame of 24 kHz reply audio.
"""

import asyncio
import uuid
from collections.abc import Sequence
from typing import Any

from tests.support.configs import OUTPUT_RATE
from vinga_server.device.boundary import DeviceOutput, PlayableAudio
from vinga_server.events import SessionEvents
from vinga_server.providers import ToolDef

OUTPUT_FRAME_BYTES = OUTPUT_RATE * 60 // 1000 * 2


# A quarter second of reply audio at the output rate, which is four
# whole Opus frames and a bit.
REPLY_PCM = b"\x11\x22" * (OUTPUT_RATE // 4)


class StubRuntime:
    """A conversation runtime that is not a pipeline at all.

    It has no VAD, no ASR, no model and no voice: it answers whatever it
    is handed with one fixed sentence and a burst of tone. Everything it
    needs to do that, it does through `DeviceOutput`, which is the
    claim under test."""

    def __init__(
        self, output: DeviceOutput, events: SessionEvents, agents: Sequence[str]
    ) -> None:
        self.output = output
        self.events = events
        self.agents = list(agents)
        # What the real runtime's constructor does, and what makes this
        # a stand-in rather than a different contract: the session emits
        # `session_open` right after the factory answers, and that event
        # names the agent talking and the thread it is talking on. A
        # runtime that activated neither would have the edge announce a
        # conversation with nobody in it, which the event schema refuses
        # (#155). The thread is minted here for the same reason the real
        # activation mints one (#190): a stub that reused the session id
        # would make two different entities one value.
        self.events.agent = self.agents[0]
        self.events.conversation = uuid.uuid4().hex
        self.heard = bytearray()
        self.closed = False
        self.aborts: list[str | None] = []
        self._replying = False

    async def audio(self, pcm: bytes) -> None:
        self.heard.extend(pcm)

    async def listen_started(self) -> None:
        self.heard.clear()

    async def listen_stopped(self) -> None:
        await self._answer()

    async def device_aborted(self, reason: str | None) -> None:
        self.aborts.append(reason)

    def replying(self) -> bool:
        return self._replying

    async def drain(self, grace_s: float) -> bool:
        return True

    async def close(self) -> None:
        self.closed = True

    async def _answer(self) -> None:
        self._replying = True
        self.output.reply_started()
        self.output.restart_pacing()
        try:
            await self.output.show_transcript("the stub heard something")
            await self.output.begin_speaking()
            await self.output.sentence_started("This is not a pipeline.")
            batch = self.output.encode_audio(REPLY_PCM) + self.output.flush_encoder()
            await self.output.send_audio(batch)
        finally:
            self._replying = False
            await self.output.finish_speaking()


class FakeDevice:
    """A device that is not a socket: no protocol, no codec, no clock.

    Its encoder is the one fact the boundary insists on, because the
    filler arbitration turns on it: PCM accumulates until a whole frame
    is there, so a chunk shorter than a frame produces nothing to play.
    """

    output_sample_rate = OUTPUT_RATE

    def __init__(self) -> None:
        self.calls: list[Any] = []
        self.sent: list[bytes] = []
        self.turn_ends = 0
        self.paused = False
        self._pending = b""
        self._frames = 0
        self._speaking_at: float | None = None

    async def show_transcript(self, text: str) -> None:
        self.calls.append(("transcript", text))

    async def begin_speaking(self) -> None:
        self.calls.append(("begin",))

    async def sentence_started(self, text: str) -> None:
        self.calls.append(("sentence", text))

    def encode_audio(self, pcm: bytes) -> PlayableAudio:
        self._pending += pcm
        packets = []
        while len(self._pending) >= OUTPUT_FRAME_BYTES:
            self._pending = self._pending[OUTPUT_FRAME_BYTES:]
            packets.append(f"frame-{self._frames}".encode())
            self._frames += 1
        return PlayableAudio(packets)

    def flush_encoder(self) -> PlayableAudio:
        if not self._pending:
            return PlayableAudio()
        self._pending = b""
        packets = [f"frame-{self._frames}".encode()]
        self._frames += 1
        return PlayableAudio(packets)

    async def send_audio(self, batch: PlayableAudio) -> None:
        if not batch:
            return
        if self._speaking_at is None:
            self._speaking_at = asyncio.get_running_loop().time()
        self.sent.extend(batch.packets)

    async def finish_speaking(self) -> None:
        self.calls.append(("finish",))

    def reply_started(self) -> None:
        self.calls.append(("reply_started",))
        self._speaking_at = None

    def restart_pacing(self) -> None:
        pass

    def pause_output(self) -> None:
        self.paused = True

    def resume_output(self) -> None:
        self.paused = False

    def speaking_started_at(self) -> float | None:
        return self._speaking_at

    def user_turn_ended(self) -> None:
        self.turn_ends += 1

    def device_tools(self) -> Sequence[ToolDef]:
        return ()

    async def call_device_tool(self, name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        raise AssertionError("this device has no tools")
