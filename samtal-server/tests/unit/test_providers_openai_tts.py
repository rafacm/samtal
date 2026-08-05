"""The OpenAI TTS provider against a mock transport.

No extra to skip on and no network: the openai client is a core
dependency, and it accepts an `http_client`, so the whole provider
(options, request shape, streaming, failures) runs here through the
real SDK serialization. What a unit test cannot judge is how the voice
sounds and what the round trip costs, which is the PR's real-API
verification step.
"""

import json
from collections.abc import AsyncIterator

import httpx
import pytest
from openai import APIStatusError, AsyncOpenAI

from samtal_server.config.models import ProviderConfig
from samtal_server.providers import ProviderError, build_provider
from samtal_server.providers.openai_tts import OpenAiTts


def provider(handler: object, **overrides: object) -> OpenAiTts:
    """A provider wired to a mock transport, so nothing leaves the test."""
    client = AsyncOpenAI(
        api_key="test-key",
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
        ),
    )
    options: dict[str, object] = {
        "voice": "alloy",
        "model": "gpt-4o-mini-tts",
        "api_key": "test-key",
        "client": client,
    }
    options.update(overrides)
    return OpenAiTts(**options)  # type: ignore[arg-type]


async def collect(tts: OpenAiTts, text: str = "Hej") -> bytes:
    return b"".join([chunk async for chunk in tts.synthesize(text)])


def build_tts(**options: object) -> object:
    return build_provider("tts", "voice", ProviderConfig.model_validate(options))


# --- options ---------------------------------------------------------


def test_a_missing_voice_fails_the_build() -> None:
    with pytest.raises(ProviderError, match='"voice" is required'):
        build_tts(type="openai", api_key_env="OPENAI_KEY")


def test_a_missing_api_key_env_fails_the_build() -> None:
    with pytest.raises(ProviderError, match="needs an API key"):
        build_tts(type="openai", voice="alloy")


def test_an_unset_api_key_variable_fails_the_build(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_KEY", raising=False)
    with pytest.raises(ProviderError, match="not set in the environment"):
        build_tts(type="openai", voice="alloy", api_key_env="OPENAI_KEY")


def test_an_unknown_option_fails_the_build(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_KEY", "secret")
    with pytest.raises(ProviderError, match="unknown option"):
        build_tts(type="openai", voice="alloy", api_key_env="OPENAI_KEY", voice_id="alloy")


def test_the_sample_rate_is_what_the_pcm_format_produces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_KEY", "secret")
    built = build_tts(type="openai", voice="alloy", api_key_env="OPENAI_KEY")
    assert isinstance(built, OpenAiTts)
    assert built.sample_rate == 24000


def test_speed_is_refused_on_a_model_that_ignores_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_KEY", "secret")
    with pytest.raises(ProviderError, match='ignores option "speed"'):
        build_tts(type="openai", voice="alloy", api_key_env="OPENAI_KEY", speed=1.2)


def test_instructions_are_refused_on_a_model_that_ignores_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_KEY", "secret")
    with pytest.raises(ProviderError, match='ignores option "instructions"'):
        build_tts(
            type="openai",
            voice="alloy",
            api_key_env="OPENAI_KEY",
            model="tts-1",
            instructions="Speak cheerfully",
        )


def test_speed_is_accepted_on_the_model_that_takes_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_KEY", "secret")
    built = build_tts(
        type="openai", voice="alloy", api_key_env="OPENAI_KEY", model="tts-1", speed=1.2
    )
    assert isinstance(built, OpenAiTts)


def test_a_speed_outside_the_api_range_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_KEY", "secret")
    with pytest.raises(ProviderError, match='"speed" must be between'):
        build_tts(
            type="openai", voice="alloy", api_key_env="OPENAI_KEY", model="tts-1", speed=9.0
        )


def test_the_type_marks_egress_and_rejects_a_declaration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_KEY", "secret")
    assert OpenAiTts.egress is True
    with pytest.raises(ProviderError, match="decided by type"):
        build_tts(type="openai", voice="alloy", api_key_env="OPENAI_KEY", egress=False)


# --- the request -----------------------------------------------------


async def test_the_request_carries_the_voice_model_and_key() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"\x00\x01")

    await collect(provider(handler), "Hej hej")

    (request,) = seen
    assert request.method == "POST"
    assert request.url.path == "/v1/audio/speech"
    assert request.headers["authorization"] == "Bearer test-key"
    assert json.loads(request.content) == {
        "model": "gpt-4o-mini-tts",
        "voice": "alloy",
        "input": "Hej hej",
        "response_format": "pcm",
    }


async def test_optional_body_fields_are_sent_only_when_configured() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"")

    await collect(provider(handler, instructions="Speak slowly and warmly"))
    assert json.loads(seen[0].content)["instructions"] == "Speak slowly and warmly"

    await collect(provider(handler, model="tts-1", speed=1.25))
    body = json.loads(seen[1].content)
    assert body["speed"] == 1.25
    assert "instructions" not in body


# --- streaming -------------------------------------------------------


async def test_the_audio_streams_through_unchanged() -> None:
    audio = bytes(range(0, 64))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=audio)

    assert await collect(provider(handler)) == audio


async def test_chunks_are_yielded_sample_aligned() -> None:
    """A response chunk can end mid-sample; downstream counts samples in
    pairs and would drop the odd byte, shifting the rest of the reply."""

    async def stream() -> AsyncIterator[bytes]:
        yield b"\x01\x02\x03"
        yield b"\x04\x05"
        yield b"\x06\x07\x08"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=stream())

    chunks = [chunk async for chunk in provider(handler).synthesize("Hej")]
    assert all(len(chunk) % 2 == 0 for chunk in chunks)
    assert b"".join(chunks) == b"\x01\x02\x03\x04\x05\x06\x07\x08"


async def test_a_trailing_odd_byte_is_dropped_rather_than_shifting_the_stream(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"\x01\x02\x03")

    chunks = [chunk async for chunk in provider(handler).synthesize("Hej")]
    assert b"".join(chunks) == b"\x01\x02"
    assert "incomplete sample" in caplog.text


# --- failures --------------------------------------------------------


async def test_an_api_error_raises_with_the_reason() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "invalid api key"}})

    with pytest.raises(APIStatusError, match="invalid api key"):
        await collect(provider(handler))
