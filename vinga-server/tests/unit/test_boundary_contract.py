"""The boundary as a contract, exercised from both sides of the seam.

The point of #85 is that the device edge and a conversation runtime can
be reasoned about, and replaced, one at a time. Two suites prove it,
and they are deliberately small: what the pipeline decides is covered
by the pipeline's own tests, and re-testing it through the boundary
would be scope growth.

Downwards: a stub runtime, injected through the factory the composition
root normally fills, holds a whole turn over a real websocket. Nothing
of the pipeline is involved, so what the assertions see is the edge
alone, and the fact that the turn happens at all is the demonstration
that a second runtime can exist.

Upwards: a fake device, which is not a socket and knows no Opus, drives
the real bespoke runtime through a turn and an interruption. Nothing of
the wire is involved, so what the assertions see is the runtime alone.
"""

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncIterator, Iterator, Sequence
from dataclasses import replace
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from tests.support.boundary import FakeDevice, StubRuntime
from tests.support.configs import DEVICE_MAC, config_with_agent, world
from tests.support.providers import built_world
from tests.support.sessions import device_session, listening_in
from tests.support.stores import memory as lane_memory
from tests.support.wire import connect, send_pcm, shake_hands, speech_pcm
from vinga_server import __version__
from vinga_server.app import create_app
from vinga_server.audio.opus import OpusEncoder
from vinga_server.config import Config
from vinga_server.device.boundary import DeviceGone, DeviceOutput, PlayableAudio, SessionInput
from vinga_server.events import SessionEvents
from vinga_server.generation import Generation
from vinga_server.providers import (
    LlmEvent,
    LlmProvider,
    TextDelta,
    ToolChoice,
    ToolDef,
    Turn,
)
from vinga_server.runtime.pipeline import bespoke_runtime_factory
from vinga_server.tools.device import DeviceToolClient
from vinga_server.tools.mcp import McpServers


@contextlib.contextmanager
def client_with_a_stub(
    built: list[StubRuntime], config: Config | None = None
) -> Iterator[TestClient]:
    """A served app, with the composition root's factory swapped for one
    that builds stubs. This is the whole of what plugging in a second
    runtime takes.

    The swap happens inside the entered lifespan, because that is where
    the composition now exists: construction is the lifespan's (#142),
    and this is the one write to a built composition the codebase
    sanctions. It lands before any connection, and the endpoint reads the
    factory per connection, so what every socket below gets is the
    stub."""
    app = create_app(config if config is not None else config_with_agent())
    with TestClient(app) as client:

        def factory(
            output: DeviceOutput,
            events: SessionEvents,
            agents: Sequence[str],
            generation: Generation,
        ) -> SessionInput:
            runtime = StubRuntime(output, events, agents)
            built.append(runtime)
            return cast(SessionInput, runtime)

        app.state.composition.runtime_factory = factory
        yield client


def test_a_stub_runtime_holds_a_turn_over_the_real_wire() -> None:
    """Handshake, transcript, speaking state, sentence, paced frames and
    the closing stop, with no pipeline anywhere: every message here was
    produced by the edge, on the runtime's instruction."""
    built: list[StubRuntime] = []
    received: list[Any] = []
    with client_with_a_stub(built) as client:
        with connect(client) as websocket:
            shake_hands(websocket)
            websocket.send_text(json.dumps({"type": "listen", "state": "start", "mode": "manual"}))
            send_pcm(websocket, speech_pcm(200), OpusEncoder())
            websocket.send_text(json.dumps({"type": "listen", "state": "stop"}))
            while True:
                message = websocket.receive()
                if message.get("text") is None:
                    received.append("frame")
                    continue
                parsed = json.loads(message["text"])
                if parsed["type"] == "mcp":
                    continue
                received.append(f"{parsed['type']} {parsed.get('state', '')}".strip())
                if received[-1] == "tts stop":
                    break

    assert received[:3] == ["stt", "tts start", "tts sentence_start"]
    assert received[-1] == "tts stop"
    assert set(received[3:-1]) == {"frame"}
    # The mic audio arrived decoded, at the pipeline rate, and only
    # while the device was listening.
    (runtime,) = built
    assert len(runtime.heard) > 0
    assert runtime.closed


def test_the_factory_is_handed_the_device_it_speaks_for() -> None:
    """What crosses at construction: the device to speak through, the
    session's observability with its identity already on it, and the
    agents this device is bound to. Nothing else."""
    built: list[StubRuntime] = []
    with client_with_a_stub(built) as client:
        with connect(client) as websocket:
            hello = shake_hands(websocket)

    (runtime,) = built
    assert isinstance(runtime.output, DeviceOutput)
    assert runtime.agents == ["assistant"]
    assert runtime.events.session_id == hello["session_id"]
    assert runtime.events.device == DEVICE_MAC.lower()
    # Activation is the runtime's, and it is part of what the factory
    # answers rather than something the edge does afterwards: the edge
    # emits `session_open` the moment the factory returns, and that
    # record names the agent talking. So the value here is the stub's
    # own choice of first agent, made in its constructor the way the
    # bespoke runtime makes it, and never one the edge wrote.
    assert runtime.events.agent == runtime.agents[0]


def test_frames_that_arrive_before_a_listen_never_reach_the_runtime() -> None:
    """The mic guards stay on the edge, before the decode, because the
    frames they drop are the evidence a capture exists for."""
    built: list[StubRuntime] = []
    with client_with_a_stub(built) as client:
        with connect(client) as websocket:
            shake_hands(websocket)
            send_pcm(websocket, speech_pcm(200), OpusEncoder())
            websocket.send_text(json.dumps({"type": "listen", "state": "start", "mode": "manual"}))
            # A listen is answered by nothing, so an abort follows it as
            # a marker: once the runtime has seen that, it has seen
            # everything sent before it.
            websocket.send_text(json.dumps({"type": "abort", "reason": "sync"}))
            deadline = time.monotonic() + 5
            while not built[0].aborts and time.monotonic() < deadline:
                time.sleep(0.01)
            assert built[0].aborts == ["sync"]

    (runtime,) = built
    assert runtime.heard == b""


# Planted in the close reason, which the far end writes and this end
# has no reason to trust.
SENTINEL = "sk-test-9b3e5c02-never-a-real-credential"


def chain(exc: BaseException) -> str:
    """Everything a renderer of this exception could reach: the error
    itself and every cause and context behind it."""
    parts: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        parts += [repr(current), str(current)]
        current = current.__cause__ or current.__context__
    return "\n".join(parts)


class VanishedSocket:
    """A device that has already gone away."""

    def __init__(self, error: BaseException | None = None) -> None:
        self._error = error or WebSocketDisconnect(1006)

    async def send_text(self, text: str) -> None:
        raise self._error

    async def send_bytes(self, data: bytes) -> None:
        raise self._error


def outgoing(session: Any) -> list[Any]:
    """Every way the boundary speaks to a device, which is every way one
    can be found to have vanished."""
    return [
        session.show_transcript("anything"),
        session.begin_speaking(),
        session.sentence_started("anything"),
        session.send_audio(PlayableAudio([b"packet"])),
        session.finish_speaking(),
    ]


async def test_a_vanished_device_reaches_the_runtime_as_device_gone() -> None:
    """The transport's disconnect is translated at the boundary, so a
    runtime never imports starlette to catch one. It subclasses
    RuntimeError on purpose, which is what lets the two sites that still
    swallow a vanished device broadly keep their catch.

    Translated, not wrapped: neither the cause nor the context leads
    back to the transport's own exception. A runtime cannot use it (that
    is the point of the boundary), and it carries a close reason written
    by the far end, which is not a thing to hand to whatever logs this.
    """
    session = device_session(config_with_agent(), DEVICE_MAC, websocket=VanishedSocket())
    for call in outgoing(session):
        try:
            await call
        except DeviceGone as exc:
            assert exc.__cause__ is None
            assert exc.__context__ is None
        else:  # pragma: no cover - the assertion below reports it
            raise AssertionError("a vanished device went unreported")


async def test_a_disconnect_carries_nothing_of_the_transports_own(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Both shapes a starlette socket produces, each carrying a secret
    where a close reason or a message goes. What the runtime is handed
    says "the device disconnected" and nothing else, and nothing on the
    way there writes the rest down."""
    for error in (
        WebSocketDisconnect(1006, f"closing: {SENTINEL}"),
        RuntimeError(f'Cannot call "send" once a close message has been sent: {SENTINEL}'),
    ):
        session = device_session(
            config_with_agent(), DEVICE_MAC, websocket=VanishedSocket(error)
        )
        with caplog.at_level("DEBUG"):
            for call in outgoing(session):
                try:
                    await call
                except DeviceGone as exc:
                    assert SENTINEL not in chain(exc)
                else:  # pragma: no cover - the assertion below reports it
                    raise AssertionError("a vanished device went unreported")

        # Nothing at the edge logs one today, which is itself the
        # assertion: a line added later must not reach for the
        # transport's exception to say what happened.
        assert SENTINEL not in caplog.text
        assert all(SENTINEL not in str(record.__dict__) for record in caplog.records)
        caplog.clear()


class SlowLlm(LlmProvider):
    """A model that takes long enough to answer for an interruption to
    land while it is still generating."""

    def __init__(self, delay_s: float, reply: str) -> None:
        self._delay_s = delay_s
        self._reply = reply

    async def stream(
        self,
        system: str,
        turns: Sequence[Turn],
        tools: Sequence[ToolDef] = (),
        tool_choice: ToolChoice = "auto",
    ) -> AsyncIterator[LlmEvent]:
        await asyncio.sleep(self._delay_s)
        yield TextDelta(self._reply)


def runtime_for(config: Config, device: FakeDevice, llm: Any = None) -> Any:
    built = built_world(config)
    if llm is not None:
        agent = built.agents["assistant"]
        built = replace(
            built,
            agents=dict(built.agents)
            | {
                "assistant": type(agent)(
                    llm=llm, asr=agent.asr, tts=agent.tts, vad=agent.vad
                )
            },
        )
    generations = world(config, providers=built)
    factory = bespoke_runtime_factory(generations, McpServers({}), lane_memory())
    return factory(
        cast(DeviceOutput, device),
        SessionEvents("contract"),
        ["assistant"],
        generations.current(),
    )


async def test_the_bespoke_runtime_holds_a_turn_against_a_fake_device() -> None:
    """One utterance in, one spoken reply out, with nothing on the other
    side that knows what a websocket is."""
    device = FakeDevice()
    assert isinstance(device, DeviceOutput)
    runtime = runtime_for(config_with_agent(asr_text="what time is it"), device)

    await runtime.listen_started()
    await runtime.audio(speech_pcm(300))
    await runtime.listen_stopped()
    assert await runtime.drain(5.0) is True

    assert device.calls[:4] == [
        ("reply_started",),
        ("transcript", "what time is it"),
        ("begin",),
        ("sentence", "You said what time is it."),
    ]
    assert device.calls[-1] == ("finish",)
    assert device.sent, "the reply was never spoken"
    # The runtime reported the end of the user's turn; what to do about
    # the microphone was left to the device.
    assert device.turn_ends == 1


async def test_an_interruption_cancels_the_reply_and_answers_the_new_one() -> None:
    """A manual stop mid-reply is a deliberate act, so it cancels
    unconditionally. The runtime cancels its own reply, which is the
    conversational consequence; the device is told a new reply started
    and is never asked to flush a queue it does not have."""
    device = FakeDevice()
    runtime = runtime_for(
        config_with_agent(asr_text="hello"),
        device,
        llm=SlowLlm(0.3, "A slow answer nobody will hear the end of."),
    )

    await runtime.listen_started()
    await runtime.audio(speech_pcm(300))
    await runtime.listen_stopped()
    await asyncio.sleep(0.05)
    assert runtime.replying()

    await runtime.audio(speech_pcm(300))
    await runtime.listen_stopped()
    assert await runtime.drain(5.0) is True

    # Two replies were started and two turns ended; the interrupted one
    # never reached the history, because it never spoke.
    assert [call for call in device.calls if call == ("reply_started",)] == [
        ("reply_started",),
        ("reply_started",),
    ]
    assert device.turn_ends == 2
    # White-box: the history is what the next round is written against,
    # and the only thing that reads it is a model. Driving a third round
    # to see it would add a third reply to a scenario whose whole claim
    # is what two of them did, so the shape is asserted where it is.
    assert [turn.role for turn in runtime._turns] == ["user", "user", "assistant"]
    assert not device.paused, "a manual stop needs no confirmation, so nothing was held"


async def test_the_end_of_a_user_turn_counts_as_activity() -> None:
    """The idle timeout counts from both ends of a turn, and the end of
    the user's is one of them.

    A runtime that reports a turn and then answers nothing (an empty
    transcript, an utterance its own gates dropped) still had somebody
    talking into the microphone. The mark belongs to the edge, at the
    boundary method, so every runtime inherits it by reporting the turn
    rather than by remembering to ask; a runtime that forgot would
    otherwise leave the watchdog counting from before the user last
    spoke and hang up on a live conversation."""
    for mode in ("realtime", "auto"):
        session = device_session(config_with_agent(), DEVICE_MAC)
        listening_in(session, mode)
        # White-box for this line and the three reads below it: the idle
        # watchdog's clock is a timestamp the edge keeps, and what it
        # decides is whether a live conversation is hung up on after a
        # configured silence. The public observation is waiting that
        # silence out, so a test of "the mark moved" would be a test
        # that sits through a timeout it did not move.
        session._watchdog.mark()
        before = session._watchdog.marked_at
        assert before is not None
        await asyncio.sleep(0.01)

        session.user_turn_ended()

        assert session._watchdog.marked_at is not None
        assert session._watchdog.marked_at > before, mode


class ScriptedMcpSocket:
    """A device that answers the MCP handshake over the websocket, and
    can be told to disappear afterwards."""

    def __init__(self) -> None:
        self.gone = False
        self.client: Any = None

    async def send_text(self, text: str) -> None:
        if self.gone:
            raise WebSocketDisconnect(1006)
        payload = json.loads(text)["payload"]
        method = payload.get("method")
        if method is None or method.startswith("notifications/"):
            return
        asyncio.get_running_loop().call_soon(self._answer, payload)

    def _answer(self, payload: dict[str, Any]) -> None:
        if payload["method"] == "initialize":
            result: dict[str, Any] = {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "board", "version": "2.4.0"},
            }
        else:
            result = {
                "tools": [
                    {
                        "name": "self.audio_speaker.set_volume",
                        "description": "Set the speaker volume",
                        "inputSchema": {"type": "object", "properties": {}},
                    }
                ]
            }
        self.client.handle({"jsonrpc": "2.0", "id": payload["id"], "result": result})


async def test_a_device_that_vanishes_mid_tool_call_reports_device_gone() -> None:
    """The device tool transport is the edge's, and it writes to the
    same socket as everything else, so it owes the same promise: a
    runtime calling a device tool must not have to catch the
    transport's own exception to notice the device left."""
    socket = ScriptedMcpSocket()
    session = device_session(config_with_agent(), DEVICE_MAC, websocket=socket)
    # White-box: the edge starts device discovery from inside `run`,
    # after a hello this session never received, and what is under test
    # is the transport underneath a discovered tool. Building the client
    # over the session's own MCP send is what `_start_device_discovery`
    # does, minus the handshake that is not the subject.
    session._device_tools = DeviceToolClient(
        session._send_mcp, "contract", "vinga-server", __version__
    )
    socket.client = session._device_tools
    await session._device_tools.discover()
    (tool,) = session.device_tools()

    socket.gone = True
    try:
        await session.call_device_tool(tool.name, {"volume": 50})
    except DeviceGone as exc:
        assert exc.__cause__ is None
        assert exc.__context__ is None
    else:  # pragma: no cover - the assertion below reports it
        raise AssertionError("a vanished device went unreported")
