"""What owns a provider, and what lets go of one.

A provider used to be built once and held for the life of the process,
so there was nothing here to test: the boot either produced four engines
per agent or refused, and nothing was ever released. Applying stored
configuration without a restart makes both halves real, and this file is
about them (#191).

Two properties run through everything below. Ownership begins the
instant an allocation succeeds, so no exit that is not an install leaves
an object behind; and letting go waits for the work rather than for the
caller, because a worker thread does not stop when the coroutine
awaiting it is cancelled.

The fake provider is what makes the first assertable: a real one holds a
client or a model, and "was it closed" is not a question either of those
answers. The last section is the other direction and uses the real
classes, because a suite that only ever closed a fake would pass with
every concrete provider inheriting the no-op.
"""

import asyncio
import threading
from typing import Any, cast

import httpx
import pytest

from tests.support.configs import config_with
from vinga_server.config import Config
from vinga_server.config.models import ProviderConfig
from vinga_server.providers import (
    Provider,
    ProviderError,
    build_entry,
    build_world,
    dispose,
)
from vinga_server.providers import world as provider_world
from vinga_server.providers.base import Operations
from vinga_server.providers.mock import MockTts

# --- what a build owns -------------------------------------------------


class Recording(MockTts):
    """A voice that remembers being closed.

    A `MockTts` rather than a class of its own, so that everything a
    world does with a TTS entry (an agent talking through it, a filled
    pause being spoken by it) works exactly as it does for the real
    thing, and the only addition is the count this file asserts on.

    The marking is declared here rather than inherited, which every
    provider class has to do (#136): a subclass that said nothing would
    be refused by the egress rule before this file could assert
    anything.
    """

    egress = False

    def __init__(self, **options: Any) -> None:
        super().__init__(**{"sample_rate": 24000, "ms_per_char": 1.0, "min_ms": 20.0} | options)
        self.closes = 0

    async def close(self) -> None:
        self.closes += 1


class Refusing(MockTts):
    """A voice whose close raises, which a teardown may never turn into
    a refusal.

    Its sentence is planted: what a third-party client says while
    failing to shut is exactly the shape of thing that quotes an
    endpoint or a credential, and no answer and no log line may carry
    it.
    """

    egress = False

    def __init__(self, **options: Any) -> None:
        super().__init__(**{"sample_rate": 24000, "ms_per_char": 1.0, "min_ms": 20.0} | options)
        self.closes = 0

    async def close(self) -> None:
        self.closes += 1
        raise RuntimeError(PLANTED)


PLANTED = "sk-teardown-6c1f9d2e-never-a-real-credential"


def recording_types(monkeypatch: pytest.MonkeyPatch) -> list[Recording]:
    """Every TTS entry a build constructs from here on, recorded and
    closeable.

    Patched at the mock module's own factory rather than at the world
    builder, so what these tests drive is the real build: the same
    options reader, the same off-loop construction, the same transfer
    into the owner.
    """
    made: list[Recording] = []

    def build_tts(label: str, config: ProviderConfig) -> Recording:
        voice = Recording()
        made.append(voice)
        return voice

    monkeypatch.setattr("vinga_server.providers.mock.build_tts", build_tts)
    return made


def pipeline(**overrides: object) -> Config:
    """One agent with the four mock stages under it."""
    return config_with(**overrides)


async def test_a_later_entrys_failure_closes_the_earlier_constructions() -> None:
    """The mid-build failure. Everything constructed before the refusal
    is closed, exactly once, and the refusal is still the one the
    provider layer composed."""
    made: list[Recording] = []

    def voice(label: str, config: ProviderConfig) -> Recording:
        built = Recording()
        made.append(built)
        return built

    with pytest.MonkeyPatch.context() as patched:
        patched.setattr("vinga_server.providers.mock.build_tts", voice)
        with pytest.raises(ProviderError, match="unknown option"):
            await build_world(
                pipeline(
                    providers={
                        "llm": {"mock": {"type": "mock"}},
                        "asr": {"mock": {"type": "mock"}},
                        "tts": {"mock": {"type": "mock"}},
                        # Built after the three above, and refused.
                        "vad": {"mock": {"type": "mock", "typo": 1}},
                    }
                )
            )

    assert [one.closes for one in made] == [1]


async def test_an_egress_refusal_closes_the_object_it_just_refused() -> None:
    """The same-entry case, and the reason the check moved out of the
    construction: the egress rule can only be applied to a built
    provider, so refusing one means letting go of one."""
    made: list[Recording] = []

    def voice(label: str, config: ProviderConfig) -> Recording:
        built = Recording()
        made.append(built)
        return built

    with pytest.MonkeyPatch.context() as patched:
        patched.setattr("vinga_server.providers.mock.build_tts", voice)
        with pytest.raises(ProviderError, match="cannot be declared"):
            await build_entry(
                "tts",
                "voice",
                ProviderConfig.model_validate({"type": "mock", "egress": False}),
            )

    assert [one.closes for one in made] == [1]


async def test_a_trailing_unknown_option_refuses_before_anything_is_built(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other same-entry case, and the mirror of it: an option the
    provider never asked about is refused with nothing constructed at
    all.

    Which is the whole point of the ordering. This refusal used to
    happen after the class had been called, so refusing a misspelled
    option on a local engine meant loading a model to find out and then
    having one to let go of.
    """
    made: list[object] = []
    constructed = MockTts.__init__

    def spy(self: MockTts, *args: Any, **kwargs: Any) -> None:
        made.append(self)
        constructed(self, *args, **kwargs)

    # The real factory, watched where the object would come into
    # existence: the class itself. Patching the factory would replace
    # the very reader this case is about.
    monkeypatch.setattr(MockTts, "__init__", spy)
    with pytest.raises(ProviderError, match="unknown option"):
        await build_entry(
            "tts", "voice", ProviderConfig.model_validate({"type": "mock", "typo": 1})
        )

    assert made == []
    # And the control, so the assertion above is about the option rather
    # than about a spy that never fires.
    await build_entry("tts", "voice", ProviderConfig.model_validate({"type": "mock"}))
    assert len(made) == 1


async def test_a_cancelled_preparation_closes_what_it_had_built(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A build whose caller goes away.

    The cancellation lands while a later entry is being constructed,
    which is where a real one lands: construction runs in a worker
    thread, so the caller gives up and the thread finishes anyway. What
    the build had already taken ownership of is closed rather than
    dropped, which is the whole of the ownership rule on the one exit
    nobody chooses.
    """
    made = recording_types(monkeypatch)
    reached = threading.Event()
    holding = threading.Event()
    real = provider_world.construct_provider

    def slow(stage: str, name: str, *args: Any, **kwargs: Any) -> object:
        if stage == "vad":
            reached.set()
            holding.wait(timeout=5)
        return real(stage, name, *args, **kwargs)

    monkeypatch.setattr(provider_world, "construct_provider", slow)
    building = asyncio.create_task(build_world(pipeline()))
    await asyncio.to_thread(reached.wait, 5)
    building.cancel()
    holding.set()
    with pytest.raises(asyncio.CancelledError):
        await building

    assert [one.closes for one in made] == [1]


# --- what a world lets go of ------------------------------------------


async def test_disposal_never_refuses_and_never_repeats_the_prose(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A close that raises is not something a caller can be told about:
    it runs after the world has already moved. So it is classified by
    its exception class, the class alone reaches the log, and the next
    provider is closed anyway."""
    refusing, after = Refusing(), Recording()

    with caplog.at_level("WARNING"):
        await dispose([refusing, after])

    assert (refusing.closes, after.closes) == (1, 1)
    assert "RuntimeError" in caplog.text
    assert PLANTED not in caplog.text


async def test_a_close_waits_for_the_worker_that_is_still_running() -> None:
    """The lease. A provider call that runs off the loop holds one until
    the thread has really finished, so a close asked for while a
    transcription is in flight waits for it even though the caller that
    started it is long gone."""
    operations = Operations()
    inside = threading.Event()
    let_go = threading.Event()

    def blocking() -> str:
        inside.set()
        let_go.wait(timeout=5)
        return "done"

    running = asyncio.create_task(operations.run(blocking))
    await asyncio.to_thread(inside.wait, 5)
    # The caller gives up; the thread does not.
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running

    settling = asyncio.create_task(operations.settled())
    await asyncio.sleep(0.05)
    assert not settling.done(), "a teardown let go while a worker was still inside"

    let_go.set()
    await asyncio.wait_for(settling, timeout=5)


# --- the concrete teardowns, on the real classes ----------------------


class ClosingHttpx:
    """An injected HTTP client that records the close its provider owes
    it."""

    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True

    def build_request(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
        raise AssertionError("this suite never sends a request")


class ClosingSdk:
    """The same for an injected SDK client, whose close is spelled
    differently."""

    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


async def test_the_elevenlabs_client_is_closed_by_its_provider() -> None:
    from vinga_server.providers.elevenlabs_tts import ElevenLabsTts

    client = ClosingHttpx()
    provider = ElevenLabsTts(
        voice_id="v",
        model="m",
        output_format="pcm_24000",
        sample_rate=24000,
        api_key="k",
        client=cast(httpx.AsyncClient, client),
    )

    await provider.close()

    assert client.closed


@pytest.mark.parametrize(
    "provider",
    [
        pytest.param("openai_llm", id="openai-compatible-llm"),
        pytest.param("openai_asr", id="openai-asr"),
        pytest.param("openai_tts", id="openai-tts"),
        pytest.param("anthropic_llm", id="anthropic-llm"),
    ],
)
async def test_every_sdk_client_is_closed_by_its_provider(provider: str) -> None:
    """One case per type that holds an SDK client, because each one
    holds its own and a close written on three of the four would leak on
    the fourth."""
    client = ClosingSdk()
    built = {
        "openai_llm": lambda: _openai_llm(client),
        "openai_asr": lambda: _openai_asr(client),
        "openai_tts": lambda: _openai_tts(client),
        "anthropic_llm": lambda: _anthropic(client),
    }[provider]()

    await built.close()

    assert client.closed


def _openai_llm(client: object) -> Provider:
    from vinga_server.providers.openai_llm import OpenAiCompatibleLlm

    return OpenAiCompatibleLlm(
        base_url="http://localhost:1234/v1",
        model="m",
        max_tokens=64,
        api_key=None,
        client=cast(Any, client),
    )


def _openai_asr(client: object) -> Provider:
    from vinga_server.providers.openai_asr import OpenAiAsr

    return OpenAiAsr(model="m", api_key="k", client=cast(Any, client))


def _openai_tts(client: object) -> Provider:
    from vinga_server.providers.openai_tts import OpenAiTts

    return OpenAiTts(voice="v", model="m", api_key="k", client=cast(Any, client))


def _anthropic(client: object) -> Provider:
    from vinga_server.providers.anthropic_llm import AnthropicLlm

    return AnthropicLlm(model="m", max_tokens=64, api_key="k", client=cast(Any, client))


def test_the_bundled_vad_holds_nothing_to_close() -> None:
    """Silero's no-op, asserted rather than assumed: the detector
    belongs to each session's endpointer, so the provider is three
    numbers and a factory, and a close that dropped something would be
    dropping it under a live session."""
    from vinga_server.providers.silero import SileroVad

    assert SileroVad.close is Provider.close


HAS_FASTER_WHISPER = (
    __import__("importlib.util", fromlist=["util"]).find_spec("faster_whisper") is not None
)

HAS_PIPER = __import__("importlib.util", fromlist=["util"]).find_spec("piper") is not None


@pytest.mark.skipif(not HAS_FASTER_WHISPER, reason="faster-whisper extra not installed")
async def test_faster_whisper_lets_go_of_its_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The local engine's release, on the real class and against a fake
    model: what is asserted is that this process stops holding the
    engine, which is all a close can promise about a library that frees
    on its own schedule."""
    from vinga_server.providers import faster_whisper

    class FakeModel:
        def __init__(self, model: str, **kwargs: object) -> None:
            self.model = model

    monkeypatch.setattr(faster_whisper, "WhisperModel", FakeModel)
    provider = faster_whisper.build(
        "providers.asr.ears", ProviderConfig.model_validate({"type": "faster_whisper"})
    )
    assert provider._engine is not None

    await provider.close()

    assert provider._engine is None


@pytest.mark.skipif(not HAS_PIPER, reason="piper extra not installed")
async def test_piper_lets_go_of_its_voice(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """The same for the local voice, whose reference is the only thing
    this process holds of an onnx session."""
    from vinga_server.providers import piper_tts

    class FakeVoice:
        config = type("Config", (), {"sample_rate": 22050})()

        @staticmethod
        def load(path: object) -> "FakeVoice":
            return FakeVoice()

    monkeypatch.setattr(piper_tts, "PiperVoice", FakeVoice)
    monkeypatch.setattr(piper_tts, "ensure_voice", lambda voice, directory: tmp_path)
    provider = piper_tts.PiperTts(voice="sv_SE-nst-medium", download_dir=tmp_path)
    assert provider._voice is not None

    await provider.close()

    assert provider._voice is None
