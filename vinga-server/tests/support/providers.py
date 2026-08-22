"""The scripted far sides a session's pipeline runs against.

What belongs here is a stand-in for something the runtime talks to and
waits on: a model, an ear, a voice, an endpointer, the tool registry a
prompt is assembled from. Each one is scripted rather than clever, so a
test says in advance what the far side will do and then asserts on what
the session made of it.

Two rules keep these honest. A fake here implements only the calls the
runtime actually makes, so a stage growing a new method is a failure
rather than a silently unused stub; and a fake that fails does so in
the place a real one would, which is why the two broken voices below
are two classes rather than one with a flag.

Nothing here builds a session or touches a socket, so this module sits
directly on `vinga_server` and the standard library.
"""

import asyncio
from collections.abc import AsyncIterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from vinga_server.config import Config
from vinga_server.config.secrets import SecretStore
from vinga_server.providers import (
    AsrResult,
    LlmEvent,
    LlmProvider,
    ProviderIdentity,
    ProviderWorld,
    StreamStarted,
    TextDelta,
    ToolCall,
    ToolChoice,
    ToolDef,
    Turn,
    Usage,
    build_world,
)
from vinga_server.providers.base import TtsProvider, VadProvider
from vinga_server.providers.mock import MockAsr
from vinga_server.runtime.prompt import GuidanceBlock
from vinga_server.tools.mcp import McpServers


def built_world(config: Config, secrets: SecretStore | None = None) -> ProviderWorld:
    """Every engine a configuration's agents reference, built through
    the server's own path, from a caller that is not a coroutine.

    Building is a coroutine because owning is: a provider refused after
    it exists has to be closed, and a close is one. Most of the helpers
    in this lane are ordinary functions whose subject is a conversation
    rather than a lifecycle, and `await`ing the world in front of every
    one of them would put a hundred and sixty-five awaits in front of
    tests that are about something else.

    So the real builder runs to completion on a loop of its own, in a
    thread of its own, which works whether or not the caller is already
    on one. Nothing constructed here is bound to that loop: a provider
    is built from options and holds no waiter until it is used. A suite
    that is about the lifecycle itself calls `build_world` directly, as
    a server does.
    """
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, _built(config, secrets)).result()


async def _built(config: Config, secrets: SecretStore | None) -> ProviderWorld:
    return (await build_world(config, secrets)).world


# --- the models -------------------------------------------------------


Step = str | list[str | StreamStarted | ToolCall | Usage]


class ScriptedLlm(LlmProvider):
    """A model whose every round is written down in advance. A round is
    a sentence to speak, or a list mixing sentences, the liveness a real
    adapter announces its first raw chunk with, the tool calls to ask
    for, and the usage a provider that reports one would end with; the
    last round repeats if the loop asks for more."""

    def __init__(self, rounds: Sequence[Step]) -> None:
        self._rounds = list(rounds)
        self.seen: list[tuple[Sequence[Turn], Sequence[ToolDef], ToolChoice]] = []
        # The system prompt of every round, the way `RecordingLlm` keeps
        # it: what the model was sent is the only place from outside a
        # session that the assembled prompt is visible, and a suite about
        # tools often has to ask both questions of one round.
        self.systems: list[str] = []

    async def stream(
        self,
        system: str,
        turns: Sequence[Turn],
        tools: Sequence[ToolDef] = (),
        tool_choice: ToolChoice = "auto",
    ) -> AsyncIterator[LlmEvent]:
        self.systems.append(system)
        self.seen.append((list(turns), list(tools), tool_choice))
        step = self._rounds[min(len(self.seen) - 1, len(self._rounds) - 1)]
        for item in [step] if isinstance(step, str) else step:
            yield TextDelta(item) if isinstance(item, str) else item


# Well past the test-scale timeout the watchdog suites shrink to, and
# never actually waited out: the watchdog cancels the sleep.
STALL_S = 30.0


class StallingLlm(LlmProvider):
    """A model whose first token takes a scripted time to arrive, per
    call: the delays list is consumed one per stream, the last entry
    repeating. What follows the delay is a healthy one-sentence
    reply."""

    def __init__(self, delays: Sequence[float], reply: str = "Recovered now.") -> None:
        self.delays = list(delays)
        self.calls = 0
        self._reply = reply

    async def stream(
        self,
        system: str,
        turns: Sequence[Turn],
        tools: Sequence[ToolDef] = (),
        tool_choice: ToolChoice = "auto",
    ) -> AsyncIterator[LlmEvent]:
        delay = self.delays[min(self.calls, len(self.delays) - 1)]
        self.calls += 1
        await asyncio.sleep(delay)
        for index, word in enumerate(self._reply.split(" ")):
            yield TextDelta(word if index == 0 else " " + word)


class RecordingLlm(LlmProvider):
    """A model that keeps the system prompt of every round it was asked
    for, which is the only place from outside a session that what the
    model received is visible."""

    def __init__(self, replies: Sequence[str] = ("Said.",)) -> None:
        self._replies = list(replies)
        self.systems: list[str] = []

    async def stream(
        self,
        system: str,
        turns: Sequence[Turn],
        tools: Sequence[ToolDef] = (),
        tool_choice: ToolChoice = "auto",
    ) -> AsyncIterator[LlmEvent]:
        self.systems.append(system)
        yield TextDelta(self._replies[min(len(self.systems) - 1, len(self._replies) - 1)])


# --- the ears ---------------------------------------------------------


class GatedAsr(MockAsr):
    """The mock ASR with a hand-operated gate: every call records the
    PCM it was handed and waits for release, so a test can hold a reply
    inside transcription while a barge-in lands."""

    def __init__(self) -> None:
        super().__init__(text="{ms} ms")
        self.pcms: list[bytes] = []
        self.release = asyncio.Event()

    async def transcribe(
        self, pcm: bytes, sample_rate: int, language_hint: str | None = None
    ) -> AsrResult:
        self.pcms.append(pcm)
        await self.release.wait()
        return await super().transcribe(pcm, sample_rate, language_hint)


class ConfirmingAsr:
    """First call is the reply's own ASR; every later call is a
    barge-in confirmation, gated on release, answering the scripted
    result."""

    def __init__(self, confirmation: AsrResult) -> None:
        self._confirmation = confirmation
        self.calls = 0
        self.release = asyncio.Event()

    async def transcribe(
        self, pcm: bytes, sample_rate: int, language_hint: str | None = None
    ) -> AsrResult:
        self.calls += 1
        if self.calls == 1:
            return AsrResult(text="the question")
        await self.release.wait()
        return self._confirmation


class ScriptedEndpointer:
    """An endpointer whose answers the test writes down. `feed()` is
    exercised, by the suites that seed an utterance the way the device
    does, and answers the scripted non-endpointing result: what a test
    controls is `speech_ms()`, so a scenario says how much speech the
    endpointer holds and then decides for itself when the utterance
    ends."""

    def __init__(self, speech_ms: float) -> None:
        self._speech_ms = speech_ms

    def feed(self, pcm: bytes) -> bool:
        return False

    def reset(self) -> None:
        return None

    def speech_start(self) -> int | None:
        return None

    def speech_ms(self) -> float:
        return self._speech_ms


class ScriptedVad(VadProvider):
    """The VAD an agent is built with, making the endpointer a test
    wrote down.

    An endpointer belongs to the agent talking: the runtime asks its VAD
    for a fresh one at every activation, so this is where a scripted one
    is handed in, rather than written over the one the runtime built.
    """

    egress = False

    def __init__(self, speech_ms: float) -> None:
        self._speech_ms = speech_ms

    def new_endpointer(self) -> ScriptedEndpointer:
        return ScriptedEndpointer(self._speech_ms)


# --- the voices -------------------------------------------------------
#
# Two ways for a voice to be broken, and the difference is the point of
# keeping them apart: one refuses when it is asked, before any audio is
# in flight, and one refuses while it is being iterated, with a reply
# already under way.


class BrokenTts(TtsProvider):
    """A voice that fails outright at synthesis time."""

    sample_rate = 24000

    def synthesize(self, text: str) -> AsyncIterator[bytes]:
        raise RuntimeError("no voice today")


class BrokenStreamingTts(TtsProvider):
    """A voice that refuses, so a reply ends between the round that
    asked for a tool and the dispatch that would have run it."""

    egress = False

    def __init__(self) -> None:
        self.sample_rate = 24000

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        raise RuntimeError("the voice service refused")
        yield b""


# --- a stage no request reaches at all --------------------------------


class Unreachable:
    """A provider entry whose host cannot be reached, for all three
    stages. Stamped with an identity the way the registry stamps a real
    one, since that is what the events are supposed to carry."""

    def __init__(self, stage: str, exc: BaseException) -> None:
        self._exc = exc
        self.identity = ProviderIdentity(
            stage=stage,
            name="cloud",
            type="openai",
            host="api.example.com",
            model="gpt-4o-mini",
        )
        self.sample_rate = 16000

    async def transcribe(self, *args: object, **kwargs: object) -> AsrResult:
        raise self._exc

    async def stream(self, *args: object, **kwargs: object) -> Any:
        raise self._exc
        yield  # pragma: no cover - never reached, makes this a generator

    async def synthesize(self, text: str) -> Any:
        raise self._exc
        yield  # pragma: no cover - never reached, makes this a generator


# --- the registry a prompt's know-how half is assembled from ----------


class CountingServers(McpServers):
    """A registry that answers a fixed set of guidance blocks and counts
    who asked. What it is for is the cache: every visible property of an
    assembled half looks the same whether it was assembled once or ten
    times, so the question has to be asked of the source."""

    def __init__(self, guidance: tuple[GuidanceBlock, ...] = ()) -> None:
        super().__init__({})
        self._guidance = guidance
        self.asked: list[str] = []

    def guidance_for_agent(self, agent: str) -> tuple[GuidanceBlock, ...]:
        self.asked.append(agent)
        return self._guidance
