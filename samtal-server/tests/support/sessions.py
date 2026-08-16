"""Building a `DeviceSession` in process, and driving a reply through it.

The other way to drive a session is over the wire (`wire.py`); this is
the way a test takes when it is about what the reply decides rather
than what the device hears. Everything here builds a real session with
a real runtime behind it, assembled the way `run` assembles one, so the
composition root has one shape in the tests as well as in the server.
That is the rule for this module: a builder here calls the same factory
the server calls, and substitutes collaborators through the arguments
that factory already takes, rather than reaching past it.

The drivers are the second half. A reply has three useful shapes to a
test: run it and answer what was spoken, run it whole with the audio,
or start it and leave it in flight the way an utterance leaves one, so
that everything asking whether this session is replying sees it.

The last section is the waiting a conversation suite does on the writer
running behind the session. Its bound is `WRITER_TIMEOUT_S` rather than
`TIMEOUT_S`, which `configs.py` already uses for a test-scale watchdog
of 50 ms: the two mean opposite things, so they do not share a name.
"""

import asyncio
import threading
import time
from dataclasses import replace
from typing import Any, cast

import pytest

import samtal_server.device.session as session_module
from samtal_server.config import Config
from samtal_server.device.session import DeviceSession
from samtal_server.filler import build_agent_fillers
from samtal_server.providers import ToolCall, Turn, build_agent_providers
from samtal_server.runtime.pipeline import bespoke_runtime_factory
from samtal_server.tools.mcp import McpServers
from samtal_server.tools.memory import MemoryStore
from tests.support.configs import DEVICE_MAC, POET_MAC, base_config
from tests.support.events import only
from tests.support.providers import ScriptedLlm, Unreachable
from tests.support.sockets import LoopingSocket, RecordingSocket

# --- building one -----------------------------------------------------


def device_session(
    config: Config,
    mac: str,
    providers: dict[str, Any] | None = None,
    memory: Any = None,
    fillers: dict[str, Any] | None = None,
    websocket: Any = None,
    mcp_servers: McpServers | None = None,
    conversations: Any = None,
) -> session_module.DeviceSession:
    """A device session with a real bespoke runtime behind it, built the
    way `run` builds one: the agents resolved from the binding, then the
    factory called with them. Every test that drives a session below the
    websocket goes through here, so the composition root has one shape
    in the tests as well as in the server.

    `mcp_servers` is the running registry, which a test that is about
    tools supplies; an empty one is what every other test here needs,
    and is what a deployment with no MCP entries has. `conversations` is
    the store a turn's record is handed to, None everywhere but in the
    suite that is about the record, which is what a deployment that has
    not asked for one has."""
    factory = bespoke_runtime_factory(
        config,
        providers if providers is not None else build_agent_providers(config),
        mcp_servers if mcp_servers is not None else McpServers({}),
        memory,
        fillers if fillers is not None else {},
        conversations,
    )
    session = session_module.DeviceSession(cast(Any, websocket), config, factory)
    session._agents = config.agents_for_device(mac)
    session.runtime = factory(session, session._events, session._agents)
    return session


def session_for(
    config: Config,
    mac: str,
    scripts: dict[str, ScriptedLlm] | None = None,
    memory: MemoryStore | None = None,
    fillers: dict[str, Any] | None = None,
    websocket: Any = None,
    mcp_servers: McpServers | None = None,
    conversations: Any = None,
) -> DeviceSession:
    """A device session with a real bespoke runtime behind it, built the
    way `run` builds one, with the named agents' LLMs replaced by
    scripts. No websocket by default: these tests drive the loop
    directly and never speak."""
    providers = build_agent_providers(config)
    for agent, script in (scripts or {}).items():
        # The entry the script stands in for, so the events a session
        # emits about its LLM carry what a real one's would.
        script.identity = providers[agent].llm.identity
        providers[agent] = type(providers[agent])(
            llm=script,
            asr=providers[agent].asr,
            tts=providers[agent].tts,
            vad=providers[agent].vad,
        )
    return device_session(
        config, mac, providers, memory, fillers, websocket, mcp_servers, conversations
    )


def session_with(
    servers: McpServers | None = None,
    scripts: dict[str, Any] | None = None,
    memory: MemoryStore | None = None,
    mac: str = POET_MAC,
    config: Config | None = None,
):
    return session_for(
        config if config is not None else base_config(),
        mac,
        scripts,
        memory=memory,
        mcp_servers=servers,
    )


def served(
    config: Config, websocket: LoopingSocket, conversations: Any = None
) -> DeviceSession:
    """A session built the way `ws.py` builds one, so `run` is what the
    test drives rather than a hand-assembled close path. `conversations`
    is the store, which reaches a session twice over: through the factory
    that binds its turn recorder, and as the collaborator the session
    opens and closes."""
    factory = bespoke_runtime_factory(
        config, build_agent_providers(config), McpServers({}), None, {}, conversations
    )
    return DeviceSession(
        cast(Any, websocket), config, factory, conversations=conversations
    )


async def open_session(
    config: Config, conversations: Any = None
) -> tuple[DeviceSession, LoopingSocket, Any]:
    """A live session with its hello exchanged, its `run` in flight."""
    websocket = LoopingSocket()
    session = served(config, websocket, conversations)
    task = asyncio.create_task(session.run())
    for _ in range(200):
        await asyncio.sleep(0.01)
        if session.runtime is not None and session._opened_at is not None:
            if websocket.inbox.empty():
                return session, websocket, task
    raise AssertionError("the session never opened")


async def masked_session(config: Config, mac: str, scripts: dict[str, Any] | None = None):
    """A session with its filler cache built the way boot builds it, on
    a recording socket, listening in realtime so the after-reply state
    is assertable."""
    fillers = await build_agent_fillers(config, build_agent_providers(config))
    session = session_for(config, mac, scripts, fillers=fillers)
    session.websocket = cast(Any, RecordingSocket())
    session._listen_mode = "realtime"
    session.listening = True
    return session


def realtime_session(config, asr) -> tuple[session_module.DeviceSession, RecordingSocket]:
    """A session mid-conversation on a realtime device, its ASR swapped
    for the test's."""
    socket = RecordingSocket()
    session = device_session(config, DEVICE_MAC, websocket=socket)
    session._listen_mode = "realtime"
    session.listening = True
    assert session.runtime._providers is not None
    session.runtime._providers = replace(session.runtime._providers, asr=asr)
    return session, socket


# --- driving a reply through it ---------------------------------------


def call(name: str, **arguments: Any) -> ToolCall:
    return ToolCall(id=f"c-{name}", name=name, arguments=arguments)


async def run_reply(session: DeviceSession, said: str) -> list[str]:
    """One reply, with speaking stubbed out: what the loop decides is
    what these tests are about, not the audio."""
    spoken: list[str] = []

    async def speak(synthesis: Any, resampler: Any, into: list[str]) -> None:
        # Sentences reach _speak as a synthesis in flight now (#37), so
        # the stub takes the text off it and skips the audio entirely.
        synthesis.cancel()
        into.append(synthesis.sentence)

    session.runtime._speak = speak  # type: ignore[method-assign]
    session.send_audio = _nothing  # type: ignore[method-assign]
    session.runtime._turns.append(Turn("user", said))
    await session.runtime._speak_reply(said, spoken)
    if spoken:
        session.runtime._turns.append(Turn("assistant", " ".join(spoken)))
    return spoken


async def drive_reply(session: DeviceSession, pcm: bytes) -> None:
    """One whole reply, audio and all, run to completion.

    The two helpers below exist so that the characterization suite,
    which pins today's behavior from outside, names the reply entry
    point in one place instead of thirty. When the reply moves behind
    the device-facing boundary, these lines move with it and the tests
    that use them do not change."""
    await session.runtime._reply(pcm)


def start_reply(session: DeviceSession, pcm: bytes) -> asyncio.Task[None]:
    """A reply in flight, registered the way an utterance registers one,
    so that everything asking whether this session is replying (the idle
    watchdog, the shutdown, the barge-in gates) sees it."""
    session.runtime._reply_task = asyncio.create_task(session.runtime._reply(pcm))
    return session.runtime._reply_task


async def _nothing(*args: object, **kwargs: object) -> None:
    return None


async def reply_with(
    provider_stage: str, exc: BaseException, caplog: pytest.LogCaptureFixture
) -> Any:
    """One reply against a provider that fails, answering the event it
    produced. The reply ends where it always did; what is new is that
    the failure is on the record as more than a traceback."""

    class TextSink:
        async def send_text(self, text: str) -> None:
            return None

    session = session_for(base_config(), POET_MAC, {"poet": ScriptedLlm(["One sentence."])})
    assert session.runtime._providers is not None
    session.runtime._providers = replace(
        session.runtime._providers, **{provider_stage: cast(Any, Unreachable(provider_stage, exc))}
    )
    session.websocket = cast(Any, TextSink())
    session._mac = POET_MAC
    session.send_audio = _nothing  # type: ignore[method-assign]
    with caplog.at_level("INFO"):
        await session.runtime._reply(b"\x00\x00" * 320)
    return only(caplog, "provider_failed")


# --- waiting on the writer behind it ----------------------------------


# Long enough that a wedged writer fails the assertion rather than the
# suite's own scheduling, and never reached when the code is correct.
WRITER_TIMEOUT_S = 30.0


class Gate:
    """The writer's parking seam, driven from the test's thread.

    Called once before each marker transaction. `wait()` returns when
    the writer has arrived and is stopped; `let_through()` releases it
    for exactly one more transaction; `open_forever()` stops gating.
    """

    def __init__(self) -> None:
        self._arrived = threading.Semaphore(0)
        self._release = threading.Semaphore(0)
        self._passthrough = False

    def __call__(self) -> None:
        if self._passthrough:
            return
        self._arrived.release()
        assert self._release.acquire(timeout=WRITER_TIMEOUT_S), "the writer was never released"

    def wait(self) -> None:
        assert self._arrived.acquire(timeout=WRITER_TIMEOUT_S), (
            "the writer never reached the gate"
        )

    def let_through(self, count: int = 1) -> None:
        self._release.release(count)

    def open_forever(self) -> None:
        self._release.release(1024)
        self._passthrough = True


def until(ready: Any, complaint: str) -> Any:
    """Wait for what a test is about, on a running writer. A wait that
    never ends is the test failing with its own sentence."""
    deadline = time.monotonic() + WRITER_TIMEOUT_S
    while time.monotonic() < deadline:
        answer = ready()
        if answer:
            return answer
        time.sleep(0.005)
    raise AssertionError(complaint)
