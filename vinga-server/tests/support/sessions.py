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

import vinga_server.device.session as session_module
from tests.support.configs import DEVICE_MAC, POET_MAC, base_config
from tests.support.events import only
from tests.support.providers import ScriptedLlm, Unreachable
from tests.support.sockets import LoopingSocket, RecordingSocket
from vinga_server.config import Config
from vinga_server.device.session import DeviceSession
from vinga_server.filler import build_agent_fillers
from vinga_server.providers import ToolCall, Turn, build_agent_providers
from vinga_server.runtime.pipeline import bespoke_runtime_factory
from vinga_server.tools.mcp import McpServers
from vinga_server.tools.memory import MemoryStore

# --- building one -----------------------------------------------------


def agent_providers(
    config: Config,
    scripts: dict[str, Any] | None = None,
    stages: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Every agent's engines, with substitutions applied before a
    session is built rather than after.

    `scripts` replaces the named agents' models; `stages` replaces one
    named stage (`asr`, `tts`, `vad`) for every agent. Both are what the
    composition root's own parameter takes, so a test that wants a
    scripted far side hands one in the way `create_app` hands the real
    ones in, and no test has to reach into a live runtime to swap an
    engine it could have built with.
    """
    providers = build_agent_providers(config)
    for agent, script in (scripts or {}).items():
        # The entry the script stands in for, so the events a session
        # emits about its LLM carry what a real one's would.
        script.identity = providers[agent].llm.identity
        providers[agent] = replace(providers[agent], llm=script)
    if stages:
        providers = {
            agent: replace(entry, **stages) for agent, entry in providers.items()
        }
    return providers


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
        providers if providers is not None else agent_providers(config),
        mcp_servers if mcp_servers is not None else McpServers({}),
        memory,
        fillers if fillers is not None else {},
        conversations,
    )
    session = session_module.DeviceSession(cast(Any, websocket), config, factory)
    # White-box, deliberately, and the only three sites in this file that
    # are. These two lines are `run`'s own, transcribed: it resolves the
    # binding, keeps the agents, and calls the factory with the session,
    # the session's events object and those agents. Nothing public does
    # that half, because the only caller that ever needs to is the edge
    # itself, and reaching it through `run` means a socket, a hello and a
    # live task, which is `open_session` below and a different test. What
    # cannot be established any other way is that a session built here is
    # wired exactly as a served one: the runtime holds the session as its
    # device, and both sides attribute their events to the same object,
    # which is what makes `_mac` and `_agent` one fact rather than two.
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
    stages: dict[str, Any] | None = None,
) -> DeviceSession:
    """A device session with a real bespoke runtime behind it, built the
    way `run` builds one, with the named agents' LLMs replaced by
    scripts. No websocket by default: these tests drive the loop
    directly and never speak."""
    return device_session(
        config,
        mac,
        agent_providers(config, cast(Any, scripts), stages),
        memory,
        fillers,
        websocket,
        mcp_servers,
        conversations,
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
        # White-box, deliberately: this is the handshake's completion,
        # and the only thing that records it. `run` stamps `_opened_at`
        # on the accept and builds the runtime after the binding
        # resolves, so the pair is what says the session is past every
        # rejection and inside the guard. Nothing public reports it: the
        # server hello goes out through a socket the test supplied, so
        # waiting on the wire would prove the send and not the state
        # behind it, and a caller that polled `runtime` alone would
        # return a session mid-accept. A test that raced this would fail
        # somewhere else entirely.
        if session.runtime is not None and session._opened_at is not None:
            if websocket.inbox.empty():
                return session, websocket, task
    raise AssertionError("the session never opened")


def _listening_in_realtime(session: DeviceSession) -> None:
    """A device that streams its microphone continuously.

    White-box, deliberately, and the last of this file's three. The mode
    a device asked to listen in reaches a session in one way, as the
    `mode` field of a `listen start` message on the wire, and these
    sessions have no serve loop to receive one: they are built below the
    websocket precisely so that what a reply decides can be asserted
    without a device. What the mode decides is who re-arms the listening
    after an utterance, which is the property the suites that ask for one
    are about, so a session left in the default mode would answer their
    question with the wrong policy rather than fail.
    """
    session._listen_mode = "realtime"
    session.listening = True


async def masked_session(config: Config, mac: str, scripts: dict[str, Any] | None = None):
    """A session with its filler cache built the way boot builds it, on
    a recording socket, listening in realtime so the after-reply state
    is assertable."""
    fillers = await build_agent_fillers(config, build_agent_providers(config))
    session = session_for(config, mac, scripts, fillers=fillers)
    session.websocket = cast(Any, RecordingSocket())
    _listening_in_realtime(session)
    return session


def realtime_session(
    config,
    asr,
    vad: Any = None,
    scripts: dict[str, Any] | None = None,
) -> tuple[session_module.DeviceSession, RecordingSocket]:
    """A session mid-conversation on a realtime device, its ASR swapped
    for the test's, and its endpointing and its model too where the test
    says so. All three are stages of the agent's providers, so all three
    are handed in where a deployment's own are."""
    socket = RecordingSocket()
    stages: dict[str, Any] = {"asr": asr}
    if vad is not None:
        stages["vad"] = vad
    session = device_session(
        config,
        DEVICE_MAC,
        agent_providers(config, scripts, stages),
        websocket=socket,
    )
    _listening_in_realtime(session)
    return session, socket


def call(name: str, **arguments: Any) -> ToolCall:
    return ToolCall(id=f"c-{name}", name=name, arguments=arguments)


# --- driving a reply through it ---------------------------------------
#
# One of these three is public and two are not, which is worth stating
# once rather than three times.
#
# The public way into a reply is `start_reply(pcm, result)` plus
# `drain(grace_s)`, both on the runtime's own interface: the first
# creates the reply task the edge's jobs ask about, the second waits for
# it. `start_reply` below is exactly that and nothing else.
#
# What that pair cannot establish is what the other two are for. It
# swallows a failure inside the reply, deliberately and by contract, so
# a reply that raised is indistinguishable through it from one that
# finished; the suites that drive a whole reply are the ones asking
# whether it survived, so `drive_reply` awaits the reply body and lets
# what happened inside reach the test. And it takes an utterance rather
# than a transcript, so what a reply was answering is whatever the
# configured ear made of the PCM: `run_reply` is the sixty-odd tests
# that are about the decision the loop makes for an exactly known
# sentence, before any of it becomes audio, and neither the transcript
# going in nor the sentences coming out has a public form here.
#
# Neither is fixed by inventing a production interface. A reply that
# reports its own failure, or one that takes a transcript from outside,
# would be surface with no caller but this file, which is what the
# design guide's test-surface rule forbids.


async def run_reply(session: DeviceSession, said: str) -> list[str]:
    """One reply for a known sentence, with speaking stubbed out: what
    the loop decides is what these tests are about, not the audio.

    White-box, per the note above. The history is written the way
    `_reply` writes it around the same call, because the loop reads the
    user's turn out of it and the next reply reads the assistant's.
    """
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
    """One whole reply, audio and all, run to completion, with whatever
    happened inside it reaching the caller.

    White-box, per the note above: `drain` answers that the reply
    finished and never how, which is the one thing a suite about a
    failing reply needs.
    """
    await session.runtime._reply(pcm)


# Long enough that a wedged reply fails the assertion rather than the
# suite's own scheduling, and never reached when the code is correct.
REPLY_TIMEOUT_S = 30.0


async def wait_for_reply(session: DeviceSession) -> None:
    """Wait out the reply in flight, the way the shutdown waits one out.
    Public throughout: `drain` is what the edge itself asks."""
    assert await session.runtime.drain(REPLY_TIMEOUT_S), "the reply never finished"


def start_reply(session: DeviceSession, pcm: bytes) -> None:
    """A reply in flight, registered the way an utterance registers one,
    so that everything asking whether this session is replying (the idle
    watchdog, the shutdown, the barge-in gates) sees it.

    The runtime's own public entry point, named here so that the suites
    driving a reply name it in one place. Whether it has finished is
    `replying()`; waiting for it is `drain()`."""
    session.runtime.start_reply(pcm, None)


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

    session = session_for(
        base_config(),
        POET_MAC,
        {"poet": ScriptedLlm(["One sentence."])},
        stages={provider_stage: cast(Any, Unreachable(provider_stage, exc))},
    )
    session.websocket = cast(Any, TextSink())
    # White-box, and the same construction `device_session` explains: a
    # session built below the websocket never ran the handshake that
    # reads the Device-Id header, and the event under test names the
    # device it failed for.
    session._mac = POET_MAC
    session.send_audio = _nothing  # type: ignore[method-assign]
    with caplog.at_level("INFO"):
        await drive_reply(session, b"\x00\x00" * 320)
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
