"""The ElevenLabs TTS provider against a mock transport.

No extra to skip on and no network: httpx is a core dependency, so the
whole provider (options, request shape, streaming, failures) runs here.
What a unit test cannot judge is how the real voice sounds and what the
round trip costs, which is the PR's real-API verification step.
"""

import httpx
import pytest

from samtal_server.config.models import ProviderConfig
from samtal_server.providers import ProviderError, build_provider
from samtal_server.providers.elevenlabs_tts import ElevenLabsTts


def provider(
    handler: object, **overrides: object
) -> ElevenLabsTts:
    """A provider wired to a mock transport, so nothing leaves the test."""
    client = httpx.AsyncClient(
        base_url="https://api.elevenlabs.io",
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
        headers={"xi-api-key": "test-key"},
    )
    options: dict[str, object] = {
        "voice_id": "voice-1",
        "model": "eleven_flash_v2_5",
        "output_format": "pcm_24000",
        "sample_rate": 24000,
        "api_key": "test-key",
        "client": client,
    }
    options.update(overrides)
    return ElevenLabsTts(**options)  # type: ignore[arg-type]


async def collect(tts: ElevenLabsTts, text: str = "Hej") -> bytes:
    return b"".join([chunk async for chunk in tts.synthesize(text)])


def build_tts(**options: object) -> object:
    return build_provider("tts", "voice", ProviderConfig.model_validate(options))


# --- options ---------------------------------------------------------


def test_a_missing_voice_id_fails_the_build() -> None:
    with pytest.raises(ProviderError, match='"voice_id" is required'):
        build_tts(type="elevenlabs", api_key_env="ELEVEN_KEY")


def test_a_missing_api_key_env_fails_the_build() -> None:
    with pytest.raises(ProviderError, match="needs an API key"):
        build_tts(type="elevenlabs", voice_id="voice-1")


def test_an_unset_api_key_variable_fails_the_build(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ELEVEN_KEY", raising=False)
    with pytest.raises(ProviderError, match="not set in the environment"):
        build_tts(type="elevenlabs", voice_id="voice-1", api_key_env="ELEVEN_KEY")


def test_an_unknown_option_fails_the_build(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ELEVEN_KEY", "secret")
    with pytest.raises(ProviderError, match="unknown option"):
        build_tts(
            type="elevenlabs", voice_id="voice-1", api_key_env="ELEVEN_KEY", voice="lessac"
        )


def test_the_sample_rate_comes_from_the_output_format(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ELEVEN_KEY", "secret")
    built = build_tts(
        type="elevenlabs",
        voice_id="voice-1",
        api_key_env="ELEVEN_KEY",
        output_format="pcm_16000",
    )
    assert isinstance(built, ElevenLabsTts)
    assert built.sample_rate == 16000


def test_the_default_output_format_matches_the_device_rate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ELEVEN_KEY", "secret")
    built = build_tts(type="elevenlabs", voice_id="voice-1", api_key_env="ELEVEN_KEY")
    assert isinstance(built, ElevenLabsTts)
    assert built.sample_rate == 24000


def test_a_non_pcm_output_format_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ELEVEN_KEY", "secret")
    with pytest.raises(ProviderError, match="pcm_<rate>"):
        build_tts(
            type="elevenlabs",
            voice_id="voice-1",
            api_key_env="ELEVEN_KEY",
            output_format="mp3_44100_128",
        )


def test_an_unknown_voice_setting_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ELEVEN_KEY", "secret")
    with pytest.raises(ProviderError, match="unknown voice_settings key"):
        build_tts(
            type="elevenlabs",
            voice_id="voice-1",
            api_key_env="ELEVEN_KEY",
            voice_settings={"stabilty": 0.5},
        )


def test_a_voice_setting_of_the_wrong_type_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ELEVEN_KEY", "secret")
    with pytest.raises(ProviderError, match='voice_settings "stability" must be a number'):
        build_tts(
            type="elevenlabs",
            voice_id="voice-1",
            api_key_env="ELEVEN_KEY",
            voice_settings={"stability": "high"},
        )


def test_the_type_marks_egress_and_rejects_a_declaration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ELEVEN_KEY", "secret")
    assert ElevenLabsTts.egress is True
    with pytest.raises(ProviderError, match="decided by type"):
        build_tts(
            type="elevenlabs", voice_id="voice-1", api_key_env="ELEVEN_KEY", egress=False
        )


# --- the request -----------------------------------------------------


async def test_the_request_carries_the_voice_model_and_key() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"\x00\x01")

    await collect(provider(handler), "Hej hej")

    (request,) = seen
    assert request.method == "POST"
    assert request.url.path == "/v1/text-to-speech/voice-1/stream"
    assert request.url.params["output_format"] == "pcm_24000"
    assert request.headers["xi-api-key"] == "test-key"
    import json

    assert json.loads(request.content) == {"text": "Hej hej", "model_id": "eleven_flash_v2_5"}


async def test_optional_body_fields_are_sent_only_when_configured() -> None:
    import json

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"")

    await collect(
        provider(handler, language_code="sv", voice_settings={"stability": 0.4, "speed": 1.1})
    )

    body = json.loads(seen[0].content)
    assert body["language_code"] == "sv"
    assert body["voice_settings"] == {"stability": 0.4, "speed": 1.1}


# --- streaming -------------------------------------------------------


async def test_the_audio_streams_through_unchanged() -> None:
    audio = bytes(range(0, 64))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=audio)

    assert await collect(provider(handler)) == audio


async def test_chunks_are_yielded_sample_aligned() -> None:
    """A response chunk can end mid-sample; downstream counts samples in
    pairs and would drop the odd byte, shifting the rest of the reply."""

    async def stream() -> object:
        yield b"\x01\x02\x03"
        yield b"\x04\x05"
        yield b"\x06\x07\x08"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=stream())  # type: ignore[arg-type]

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
        return httpx.Response(401, json={"detail": {"message": "invalid api key"}})

    with pytest.raises(RuntimeError, match="HTTP 401.*invalid api key"):
        await collect(provider(handler))


async def test_a_long_error_body_is_truncated() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b"x" * 5000)

    with pytest.raises(RuntimeError) as failure:
        await collect(provider(handler))
    assert len(str(failure.value)) < 600
