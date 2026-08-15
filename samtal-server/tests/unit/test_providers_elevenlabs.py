"""The ElevenLabs TTS provider against a mock transport.

No extra to skip on and no network: httpx is a core dependency, so the
whole provider (options, request shape, streaming, failures) runs here.
What a unit test cannot judge is how the real voice sounds and what the
round trip costs, which is the PR's real-API verification step.
"""

import asyncio

import httpx
import pytest

from samtal_server.config.models import ProviderConfig
from samtal_server.providers import ProviderCallError, ProviderCallTimeout, build_provider
from samtal_server.providers.base import ProviderError
from samtal_server.providers.elevenlabs_tts import ElevenLabsTts

# Not a real credential, and shaped so a substring check for it cannot
# match by accident. It stands in for what the API can echo back into
# an error body.
SENTINEL = "sk-test-4f8b2c9e-never-a-real-credential"


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


# --- options ---------------------------------------------------------


def test_a_missing_voice_id_fails_the_build() -> None:
    with pytest.raises(ProviderError, match='"voice_id" is required'):
        build_tts(type="elevenlabs", api_key_env="ELEVEN_KEY")


def test_a_missing_api_key_env_fails_the_build() -> None:
    with pytest.raises(ProviderError, match="needs an API key"):
        build_tts(type="elevenlabs", voice_id="voice-1")


def test_an_unset_api_key_variable_fails_the_build(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ELEVEN_KEY", raising=False)
    with pytest.raises(ProviderError, match="references an unset environment variable"):
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


async def test_an_api_error_raises_the_taxonomy_with_the_status_and_no_body() -> None:
    """The status is trusted metadata; the body is not, and it used to
    be quoted into the message. The API is free to echo whatever was
    sent to produce the failure, and the session renders this message
    into the log line that is kept (#137)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": {"message": f"invalid api key {SENTINEL}"}})

    with pytest.raises(ProviderCallError) as failure:
        await collect(provider(handler))

    assert not isinstance(failure.value, ProviderCallTimeout)
    assert "HTTP 401" in str(failure.value)
    assert SENTINEL not in chain(failure.value)


async def test_an_error_body_reaches_neither_the_message_nor_the_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A body no longer has to be truncated to keep a stray HTML error
    page out of the log, because none of it is quoted at all."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=f"<html>{SENTINEL}</html>".encode() + b"x" * 5000)

    with caplog.at_level("DEBUG"), pytest.raises(ProviderCallError) as failure:
        await collect(provider(handler))

    assert len(str(failure.value)) < 100
    assert SENTINEL not in chain(failure.value)
    assert SENTINEL not in caplog.text
    assert all(SENTINEL not in str(record.__dict__) for record in caplog.records)


async def test_a_request_that_timed_out_raises_the_timeout_half() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("the API never answered", request=request)

    with pytest.raises(ProviderCallTimeout, match="ConnectTimeout"):
        await collect(provider(handler))


async def test_a_transport_failure_raises_the_error_half() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to the API", request=request)

    with pytest.raises(ProviderCallError, match="ConnectError") as failure:
        await collect(provider(handler))
    assert not isinstance(failure.value, ProviderCallTimeout)


async def test_a_failure_after_the_first_chunk_is_wrapped_too() -> None:
    """A response that opened is not a response that arrived: this
    provider streams the audio out of the body, and the connection can
    drop halfway through a sentence."""

    async def stream() -> object:
        yield b"\x01\x02"
        raise httpx.ReadError("the connection dropped")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=stream())  # type: ignore[arg-type]

    chunks: list[bytes] = []
    with pytest.raises(ProviderCallError) as failure:
        async for chunk in provider(handler).synthesize("Hej"):
            chunks.append(chunk)

    assert chunks == [b"\x01\x02"]
    assert "ReadError" in str(failure.value)


async def test_a_timeout_after_the_first_chunk_is_still_a_timeout() -> None:
    async def stream() -> object:
        yield b"\x01\x02"
        raise httpx.ReadTimeout("the rest never came")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=stream())  # type: ignore[arg-type]

    with pytest.raises(ProviderCallTimeout):
        await collect(provider(handler))


async def test_a_non_sdk_failure_passes_through_unwrapped() -> None:
    """The taxonomy claims request failures, not all failures: a bug in
    this process must reach logger.exception as itself."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise ValueError("a local bug")

    with pytest.raises(ValueError, match="a local bug"):
        await collect(provider(handler))


async def test_a_cancelled_synthesis_is_not_a_provider_failure() -> None:
    """Barge-in cancels a sentence mid-send, and a cancellation dressed
    as a provider failure would be reported as one."""

    async def stream() -> object:
        yield b"\x01\x02"
        raise asyncio.CancelledError()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=stream())  # type: ignore[arg-type]

    with pytest.raises(asyncio.CancelledError):
        await collect(provider(handler))


# --- failures while releasing the connection -------------------------


class ClosingStream(httpx.AsyncByteStream):
    """A response body that fails when it is released, which is what a
    connection reset at the end of a sentence looks like. `chunks` is
    what it delivers first, and a stream that raises mid-body never
    reaches completion, so the release is still ours to make."""

    def __init__(self, chunks: list[bytes], mid_body: BaseException | None = None) -> None:
        self._chunks = chunks
        self._mid_body = mid_body

    async def __aiter__(self):
        for chunk in self._chunks:
            yield chunk
        if self._mid_body is not None:
            raise self._mid_body

    async def aclose(self) -> None:
        raise httpx.ReadError(f"the connection reset while closing, carrying {SENTINEL}")


async def test_a_failure_while_releasing_a_refused_request_keeps_the_status() -> None:
    """The status is what explains the sentence; a release that failed
    afterwards is a consequence of the same broken connection, and it
    used to replace the sanitized error on its way out of the finally."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, stream=ClosingStream([]))

    with pytest.raises(ProviderCallError) as failure:
        await collect(provider(handler))

    assert "HTTP 401" in str(failure.value)
    assert SENTINEL not in chain(failure.value)


async def test_a_failure_while_releasing_after_a_mid_stream_failure_keeps_the_first() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, stream=ClosingStream([b"\x01\x02"], httpx.ReadTimeout("the rest never came"))
        )

    with pytest.raises(ProviderCallTimeout) as failure:
        await collect(provider(handler))

    assert "ReadTimeout" in str(failure.value)
    assert SENTINEL not in chain(failure.value)


async def test_a_failure_while_releasing_a_good_response_is_still_the_taxonomy() -> None:
    """With nothing else wrong it is the whole story, so it is reported,
    but as the taxonomy rather than as a raw httpx error the pipeline
    would have to guess at."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=ClosingStream([b"\x01\x02"]))

    with pytest.raises(ProviderCallError) as failure:
        await collect(provider(handler))

    assert "ReadError" in str(failure.value)
    assert SENTINEL not in chain(failure.value)


async def test_a_failure_while_releasing_a_cancelled_sentence_keeps_the_cancellation() -> None:
    """Barge-in is the whole reason cancellation passes through
    untouched, and a connection that also broke while closing must not
    turn it into a provider failure the reply path would report."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=ClosingStream([b"\x01\x02"], asyncio.CancelledError()))

    with pytest.raises(asyncio.CancelledError) as failure:
        await collect(provider(handler))

    assert SENTINEL not in chain(failure.value)


async def test_a_falsey_injected_client_is_still_the_one_used() -> None:
    """`client or ...` drops a double that answers False to a truth test,
    which any object defining __bool__ or __len__ does, and builds a real
    client in its place."""

    class Falsey:
        def __bool__(self) -> bool:
            return False

    given = Falsey()
    tts = ElevenLabsTts(
        voice_id="voice-1",
        model="eleven_flash_v2_5",
        output_format="pcm_24000",
        sample_rate=24000,
        api_key="test-key",
        client=given,  # type: ignore[arg-type]
    )
    assert tts._client is given
