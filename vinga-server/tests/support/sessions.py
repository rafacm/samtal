"""Building a `DeviceSession` in process, and driving a reply through it.

The other way to drive a session is over the wire (`wire.py`); this is
the way a test takes when it is about what the reply decides rather
than what the device hears. Everything here builds a real session with
a real runtime behind it, assembled the way `run` assembles one, so the
composition root has one shape in the tests as well as in the server.
That is the rule for this module: a builder here calls the same factory
the server calls, and substitutes collaborators through the arguments
that factory already takes, rather than reaching past it.

The reading is the second part. What a session did is asked of the
things that received it: the agent talking is read off the events
object, where both sides of the boundary read it, and the conversation
it kept is read from the round after, because the history exists so
that the next round is written against what was said.

Who holds the floor is the third, and the only part of this module that
is white-box throughout. Its note says why once, for all four of its
names.

The drivers are the fourth. A reply has three useful shapes to a test:
run it for a sentence the test names and answer what was spoken, run it
whole with the audio, or start it and leave it in flight the way an
utterance leaves one, so that everything asking whether this session is
replying sees it. Their note says which of the three is public.

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
from tests.support.configs import DEVICE_MAC, POET_MAC, base_config, world
from tests.support.events import only
from tests.support.providers import ScriptedLlm, Unreachable, built_world
from tests.support.sockets import LoopingSocket, RecordingSocket
from tests.support.stores import memory as lane_memory
from vinga_server.config import Config
from vinga_server.config.models import normalize_mac
from vinga_server.conversations.store import Half
from vinga_server.device.session import DeviceSession
from vinga_server.events import SessionEvents
from vinga_server.filler import build_agent_fillers
from vinga_server.generation import Generations
from vinga_server.memory.store import MemoryStore
from vinga_server.providers import ProviderWorld, ToolCall, Turn
from vinga_server.runtime.pipeline import bespoke_runtime_factory
from vinga_server.tools.mcp import McpServers

# --- building one -----------------------------------------------------


def agent_providers(
    config: Config,
    scripts: dict[str, Any] | None = None,
    stages: dict[str, Any] | None = None,
) -> ProviderWorld:
    """The world's engines, with substitutions applied before a session
    is built rather than after.

    `scripts` replaces the named agents' models; `stages` replaces one
    named stage (`asr`, `tts`, `vad`) for every agent. Both are what the
    composition root's own build produces, so a test that wants a
    scripted far side hands one in the way a server hands the real ones
    in, and no test has to reach into a live runtime to swap an engine
    it could have built with.

    A world rather than a mapping, because a world is what a generation
    holds and a generation is what a session binds. A substitution
    replaces what the entry resolved to, in both halves of the world:
    what the agents talk through and what the entry itself answers, so
    that an apply which carries an unchanged entry over carries the
    stand-in with it, exactly as it would carry the engine it stands in
    for.
    """
    built = built_world(config)
    providers = dict(built.agents)
    for agent, script in (scripts or {}).items():
        # The entry the script stands in for, so the events a session
        # emits about its LLM carry what a real one's would.
        script.identity = providers[agent].llm.identity
        providers[agent] = replace(providers[agent], llm=script)
    if stages:
        providers = {
            agent: replace(entry, **stages) for agent, entry in providers.items()
        }
    swapped = {
        id(getattr(built.agents[agent], stage)): getattr(entry, stage)
        for agent, entry in providers.items()
        for stage in ("llm", "asr", "tts", "vad")
        if getattr(built.agents[agent], stage) is not getattr(entry, stage)
    }
    return replace(
        built,
        agents=providers,
        instances={
            identity: swapped.get(id(engine), engine)
            for identity, engine in built.instances.items()
        },
    )


def device_session(
    config: Config,
    mac: str,
    providers: ProviderWorld | None = None,
    memory: Any = None,
    fillers: dict[str, Any] | None = None,
    websocket: Any = None,
    mcp_servers: McpServers | None = None,
    conversations: Any = None,
    generations: Generations | None = None,
    threads: Any = None,
    fallbacks: dict[str, Any] | None = None,
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
    not asked for one has. `threads` is the other direction through the
    same store, the read a resume goes through, and None is the same
    kind of absence; a session resumes nothing unless it is handed one
    AND its configuration switched resumption on, which is the runtime's
    own read of the section rather than this argument.

    `generations` is the holder the server would hand the factory, and
    it is a parameter for the one kind of test that needs it: a reload
    replaces what the holder answers, so a suite about what a session
    reads across an apply has to build its session against the holder
    the apply installs into. Everything else gets a holder over the
    configuration it passed, which is the world that server serves and
    the only one it will ever serve.

    `fillers` are the clips that world holds, since a session binds them
    off its generation rather than being handed them: a suite that wants
    a masked session says which clips the world has, and a suite that
    hands its own holder in has already said. `fallbacks` is the other
    cache on the same generation and is passed the same way; absent is a
    world nothing was synthesized for, where a failed reply is the
    silence it was before the phrase existed.

    `memory` is the one collaborator with a default rather than an
    absence, because there is no deployment without a memory store
    (#314). None means the lane's own, which is empty unless the test
    put something in it; a suite that wants a store which cannot reach
    its database hands one in."""
    if generations is None:
        generations = world(
            config,
            fillers=fillers,
            providers=providers if providers is not None else agent_providers(config),
            fallbacks=fallbacks,
        )
    factory = bespoke_runtime_factory(
        generations,
        mcp_servers if mcp_servers is not None else McpServers({}),
        memory if memory is not None else lane_memory(),
        conversations,
        threads,
    )
    session = session_module.DeviceSession(cast(Any, websocket), generations, factory)
    # White-box, deliberately, and the only four sites in this file that
    # are. These lines are `run`'s own, transcribed: it reads the device
    # off the handshake before anything else can happen, resolves the
    # binding, keeps the agents, captures the world it is going to build
    # from, and calls the factory with the session, the session's events
    # object, those agents and that world. The MAC is first there and
    # first here, normalized as the edge normalizes it, which is what
    # makes a session built by this one that knows which device it is on:
    # memory's device scope is addressed by it, every event carries it,
    # and a served session has always had one. Nothing public does that half,
    # because the only caller that ever needs to is the edge itself, and
    # reaching it through `run` means a socket, a hello and a live task,
    # which is `open_session` below and a different test. What cannot be
    # established any other way is that a session built here is wired
    # exactly as a served one: the runtime holds the session as its
    # device, both sides attribute their events to the same object, and
    # the world the runtime speaks through is the one the session says
    # it is holding.
    session._mac = normalize_mac(mac)
    session._agents = config.agents_for_device(mac)
    session._generation = generations.current()
    session.runtime = factory(
        session, session._events, session._agents, session._generation
    )
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
    generations: Generations | None = None,
    threads: Any = None,
    fallbacks: dict[str, Any] | None = None,
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
        generations,
        threads,
        fallbacks,
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


def events_of(session: DeviceSession) -> SessionEvents:
    """A session's observability.

    White-box in the reach and public in everything it is used for. The
    session builds its own events object and publishes no accessor,
    which is right for production: the runtime is handed one at
    construction and nothing else in the server asks a session for it.
    A suite about what a session emitted has no other way to attach a
    tap to that one object, and attaching to a second one would be
    watching something the session does not use. `attach`, `detach` and
    `session_id` on what comes back are the interface a tap's own
    consumers use.
    """
    return session._events


def attached_taps(session: DeviceSession) -> list[Any]:
    """The consumers still attached to this session's events.

    White-box in the read, and the asymmetry is deliberate on the
    production side: `attach` and `detach` are public and a reader is
    not, because nothing in the server asks who is listening. What the
    suites using this claim is that a session which ended detached what
    it attached, and a tap left behind is a consumer still being written
    to after the store it writes into has stopped, which surfaces as a
    failure in whatever runs next rather than in the session that
    leaked it.
    """
    return list(events_of(session)._taps)


def attached_capture(session: DeviceSession) -> Any:
    """The capture this session's events are still writing their
    decision track into, or None.

    The reading half of `attach_capture` and `detach_capture`, white-box
    for the reason `attached_taps` gives about its own: nothing in the
    server asks who is listening. A capture left attached is the same
    leak in its other shape, an open file being written to by a session
    that has stopped.
    """
    return events_of(session)._capture


def stamp_with(session: DeviceSession, clock: Any) -> None:
    """Make this session's events read a clock the test wrote.

    White-box, deliberately. `SessionEvents` takes its clock at
    construction, exactly so that what stamps an event is visible and
    swappable, and the session constructs its own; nothing hands one in,
    and adding a parameter to `DeviceSession` for it would be production
    surface with no production caller. What the reads below are for is
    an interval or an ordering between two stamps, which a real clock
    can put in the same microsecond and which no surface reports
    separately from the values it is comparing.
    """
    events_of(session)._clock = clock


def with_device(session: DeviceSession, mac: str) -> DeviceSession:
    """The MAC the handshake would have read off the Device-Id header.

    White-box, and the same construction `device_session` explains: a
    session built below the websocket never ran `run`, which is where a
    device identity is read and normalized. Every event and every stored
    turn carries the device it is about, so a suite about either has to
    say which device this was, and there is no other way to say it
    without a socket and a handshake.
    """
    session._mac = mac
    return session


def served(
    config: Config, websocket: LoopingSocket, conversations: Any = None
) -> DeviceSession:
    """A session built the way `ws.py` builds one, so `run` is what the
    test drives rather than a hand-assembled close path. `conversations`
    is the store, which reaches a session twice over: through the factory
    that binds its turn recorder, and as the collaborator the session
    opens and closes."""
    generations = world(config, providers=built_world(config))
    factory = bespoke_runtime_factory(
        generations, McpServers({}), lane_memory(), conversations
    )
    return DeviceSession(
        cast(Any, websocket), generations, factory, conversations=conversations
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


def listening_in(session: DeviceSession, mode: str) -> None:
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
    session._listen_mode = mode
    session.listening = True


def listening_in_realtime(session: DeviceSession) -> None:
    """A device that streams its microphone continuously, which is what
    most of these suites want."""
    listening_in(session, "realtime")


async def masked_session(config: Config, mac: str, scripts: dict[str, Any] | None = None):
    """A session with its cached speech built the way boot builds it,
    both kinds, on a recording socket, listening in realtime so the
    after-reply state is assertable.

    Both kinds because the boot builds both: a masked session whose
    world had no failure phrase would be a world this server never
    serves, and the two interact (a reply that fails while a clip is
    sounding settles the clip before it speaks)."""
    fillers = await build_agent_fillers(config, built_world(config).agents)
    session = session_for(
        config, mac, scripts, fillers=fillers.clips, fallbacks=fillers.fallbacks
    )
    session.websocket = cast(Any, RecordingSocket())
    listening_in_realtime(session)
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
    listening_in_realtime(session)
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


# --- what a session says it did ---------------------------------------


def talking(session: DeviceSession) -> str | None:
    """The agent talking right now, read where both sides of the
    boundary read it. The events object is where the active agent
    lives, because every event either side emits is attributed to it,
    and `agent` on it is public."""
    return events_of(session).agent


def talking_thread(session: DeviceSession) -> str | None:
    """The conversation that agent is talking on, read in the same
    place and public for the same reason: an event that names the agent
    names the thread it was speaking in, so both live on the events
    object and both sides of the boundary read them there.

    A session's threads are per agent, so this moves with a handover.
    What it answers is the thread the NEXT turn will be recorded on,
    which is what a suite about attribution compares a finished record
    against."""
    return events_of(session).conversation


# What one more round is driven with when the point of the round is to
# see the history it was handed rather than what it answers.
PROBE = "and then?"


async def history(session: DeviceSession, script: Any) -> list[Any]:
    """The conversation this session kept, as the next round sees it.

    The history is not a surface: it exists so that the next round is
    written against what was said, and the model is the thing that
    receives it. So one more round is what makes it observable, and
    what comes back is the turns that round was handed, minus the
    utterance it is answering.
    """
    await run_reply(session, PROBE)
    return list(script.seen[-1][0])[:-1]


# --- who holds the floor ----------------------------------------------
#
# Four reaches, one reason, stated once. The turn-taking side is reached
# through `SessionInput` in production: the edge feeds it audio and tells
# it a `listen start` or a `listen stop` arrived, and everything else it
# does it decides for itself off the endpointer. That is the whole
# public surface, and it is not enough for a suite about the deciding.
#
# An endpointer-driven end of utterance is a decision the endpointer
# makes while audio is being fed, and the device has no message for it;
# `listen stop` is the manual end, which is a different gate with a
# different rule and the one thing these tests must not use. What the
# buffer holds at that instant is what the gates measure, and putting
# real speech there means a real VAD classifying synthetic tones and
# ending the utterance at a moment nothing chose. And the reply task is
# what `replying()` and `drain()` answer about without handing over, so
# a caller that has to await this one exactly, cancellation included,
# holds it.


def turn_taking(session: DeviceSession) -> Any:
    """The floor behind this session. White-box, per the note above."""
    return session.runtime._turntaking


def plant_utterance(session: DeviceSession, pcm: bytes) -> None:
    """The audio a device would have streamed, put where the floor keeps
    it, so the instant the gates read is the instant the test chose.
    White-box, per the note above."""
    turn_taking(session)._utterance = bytearray(pcm)


async def end_utterance(session: DeviceSession, endpointed: bool = True) -> None:
    """End the utterance the way the endpointer ends one. White-box,
    per the note above."""
    await turn_taking(session).finish_utterance(endpointed=endpointed)


def reply_in_flight(session: DeviceSession) -> Any:
    """The reply task this session has running, for a caller that has to
    await or identify this one and not merely wait for it to end.
    White-box, per the note above."""
    return session.runtime._reply_task


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
    """Wait out the reply in flight, and let whatever happened inside it
    reach the caller.

    `drain` is the bound and nothing else. Its contract is that a reply
    which failed is a reply which finished, deliberately, because that
    is what the edge needs to know; so it answers True for a task
    holding an exception, and a suite that waited through it alone would
    walk past a failure that arrived late. The task is awaited after it,
    which is the line that raises. Held before the wait rather than
    after, because a barge-in landing in between replaces it.
    """
    reply = reply_in_flight(session)
    assert reply is not None, "no reply was in flight to wait for"
    assert await session.runtime.drain(REPLY_TIMEOUT_S), "the reply never finished"
    await reply


def start_reply(session: DeviceSession, pcm: bytes, result: Any = None) -> None:
    """A reply in flight, registered the way an utterance registers one,
    so that everything asking whether this session is replying (the idle
    watchdog, the shutdown, the barge-in gates) sees it.

    The runtime's own public entry point, named here so that the suites
    driving a reply name it in one place. `result` is a transcription
    that already exists, which is what a confirmed barge-in hands it.
    Whether the reply has finished is `replying()`; waiting for it out
    is `wait_for_reply` above, which raises what it was holding."""
    session.runtime.start_reply(pcm, result)


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
    with_device(session, POET_MAC)
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

    Called in front of each of a marker's two transactions, with the
    half it is about to open. `wait()` returns when the writer has
    arrived and is stopped; `let_through()` releases it for exactly one
    more transaction; `open_forever()` stops gating.

    One half at a time, and the durable one by default, which is what
    every marker test means by "the writer is stopped in front of this
    marker": those tests were written when a marker was one transaction,
    and a stop they did not ask for would silently change what their
    releases count. A test about the interval between the two halves
    builds a gate for `Half.EVENTS` instead.
    """

    def __init__(self, half: Half = Half.DURABLE) -> None:
        self._half = half
        self._arrived = threading.Semaphore(0)
        self._release = threading.Semaphore(0)
        self._passthrough = False

    def __call__(self, half: Half = Half.DURABLE) -> None:
        if self._passthrough or half is not self._half:
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
