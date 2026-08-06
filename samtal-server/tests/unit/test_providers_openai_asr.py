"""The OpenAI ASR provider against a mock transport.

No extra to skip on and no network: the openai client is a core
dependency, and it accepts an `http_client`, so the whole provider
(options, the WAV upload, the request shape, failures) runs here
through the real SDK serialization. What a unit test cannot judge is
how well it hears and what the round trip costs, which is the PR's
real-API verification step.
"""

import io
import wave

import httpx
import pytest
from openai import APIStatusError, AsyncOpenAI

from samtal_server.config.models import ProviderConfig
from samtal_server.providers import ProviderError, build_provider
from samtal_server.providers.openai_asr import OpenAiAsr

# One 16 kHz second of s16le silence, comfortably over the API minimum.
ONE_SECOND = b"\x00\x00" * 16000


def provider(handler: object, **overrides: object) -> OpenAiAsr:
    """A provider wired to a mock transport, so nothing leaves the test."""
    client = AsyncOpenAI(
        api_key="test-key",
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
        ),
    )
    options: dict[str, object] = {
        "model": "gpt-4o-mini-transcribe",
        "api_key": "test-key",
        "client": client,
    }
    options.update(overrides)
    return OpenAiAsr(**options)  # type: ignore[arg-type]


def transcript_handler(text: str = "Hej hej") -> object:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"text": text})

    return handler


def build_asr(**options: object) -> object:
    return build_provider("asr", "ears", ProviderConfig.model_validate(options))


def uploaded_audio(request: httpx.Request) -> bytes:
    """The file part of a multipart body, as the far end receives it."""
    body = request.content
    start = body.index(b"RIFF")
    boundary = body[: body.index(b"\r\n")]
    return body[start : body.index(b"\r\n" + boundary, start)]


def form_field(request: httpx.Request, name: str) -> str | None:
    """One text field of a multipart body, None when it was not sent."""
    marker = f'name="{name}"\r\n\r\n'.encode()
    if marker not in request.content:
        return None
    start = request.content.index(marker) + len(marker)
    return request.content[start : request.content.index(b"\r\n", start)].decode()


# --- options ---------------------------------------------------------


def test_a_missing_api_key_env_fails_the_build() -> None:
    with pytest.raises(ProviderError, match="needs an API key"):
        build_asr(type="openai")


def test_an_unset_api_key_variable_fails_the_build(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_KEY", raising=False)
    with pytest.raises(ProviderError, match="not set in the environment"):
        build_asr(type="openai", api_key_env="OPENAI_KEY")


def test_an_unknown_option_fails_the_build(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_KEY", "secret")
    with pytest.raises(ProviderError, match="unknown option"):
        build_asr(type="openai", api_key_env="OPENAI_KEY", beam_size=1)


def test_the_defaults_build(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_KEY", "secret")
    built = build_asr(type="openai", api_key_env="OPENAI_KEY")
    assert isinstance(built, OpenAiAsr)


def test_a_temperature_outside_the_api_range_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_KEY", "secret")
    with pytest.raises(ProviderError, match='"temperature" must be between'):
        build_asr(type="openai", api_key_env="OPENAI_KEY", temperature=2.0)


@pytest.mark.parametrize(
    "base_url",
    [
        "https://api.openai.com/v1",
        "https://api.openai.com/v1/",
        "HTTPS://API.OPENAI.COM/v1",
        "https://api.openai.com:443/v1",
    ],
)
def test_every_spelling_of_openai_keeps_the_startup_guarantees(
    base_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The host decides, not the spelling. A raw string comparison would
    let a trailing slash boot keyless and fail on the first utterance."""
    monkeypatch.delenv("OPENAI_KEY", raising=False)
    with pytest.raises(ProviderError, match="needs an API key"):
        build_asr(type="openai", base_url=base_url)

    monkeypatch.setenv("OPENAI_KEY", "secret")
    with pytest.raises(ProviderError, match='"temperature" must be between'):
        build_asr(type="openai", api_key_env="OPENAI_KEY", base_url=base_url, temperature=2.0)


@pytest.mark.parametrize("base_url", ["not-a-url", "api.openai.com/v1", "https://"])
def test_a_base_url_that_is_not_a_url_fails_the_build(
    base_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Including the one that looks like OpenAI but has no scheme, which
    would otherwise be treated as a compatible endpoint and boot
    keyless."""
    monkeypatch.setenv("OPENAI_KEY", "secret")
    with pytest.raises(ProviderError, match='"base_url" must be a URL'):
        build_asr(type="openai", api_key_env="OPENAI_KEY", base_url=base_url)


def test_a_compatible_endpoint_keeps_its_own_temperature_range() -> None:
    """The range is a fact about OpenAI's models, not about the dialect,
    so guessing on a self-hosted server's behalf would reject a working
    configuration before the request is sent."""
    built = build_asr(
        type="openai", base_url="http://localhost:8000/v1", temperature=2.0, egress=False
    )
    assert isinstance(built, OpenAiAsr)


def test_the_base_url_decides_egress_rather_than_the_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A self-hosted transcription server keeps the audio on the host and
    api.openai.com does not, so the type cannot know its own egress."""
    monkeypatch.setenv("OPENAI_KEY", "secret")
    assert OpenAiAsr.egress is None
    built = build_asr(
        type="openai",
        api_key_env="OPENAI_KEY",
        base_url="http://localhost:8000/v1",
        egress=False,
    )
    assert isinstance(built, OpenAiAsr)


def test_local_only_refuses_the_default_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_KEY", "secret")
    with pytest.raises(ProviderError, match="sends session data off this host"):
        build_provider(
            "asr",
            "ears",
            ProviderConfig.model_validate(
                {"type": "openai", "api_key_env": "OPENAI_KEY", "egress": True}
            ),
            local_only=True,
        )


def test_local_only_admits_a_local_endpoint_that_declares_itself() -> None:
    built = build_provider(
        "asr",
        "ears",
        ProviderConfig.model_validate(
            {"type": "openai", "base_url": "http://localhost:8000/v1", "egress": False}
        ),
        local_only=True,
    )
    assert isinstance(built, OpenAiAsr)


def test_a_local_endpoint_needs_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The SDK insists on a key; a self-hosted server usually does not."""
    monkeypatch.delenv("OPENAI_KEY", raising=False)
    built = build_asr(type="openai", base_url="http://localhost:8000/v1")
    assert isinstance(built, OpenAiAsr)


# --- the request -----------------------------------------------------


async def test_the_request_carries_the_model_and_key() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"text": "Hej hej"})

    result = await provider(handler).transcribe(ONE_SECOND, 16000)

    (request,) = seen
    assert request.method == "POST"
    assert request.url.path == "/v1/audio/transcriptions"
    assert request.headers["authorization"] == "Bearer test-key"
    assert form_field(request, "model") == "gpt-4o-mini-transcribe"
    assert form_field(request, "response_format") == "json"
    assert result.text == "Hej hej"


async def test_the_audio_is_uploaded_as_wav_at_the_rate_it_was_given() -> None:
    """The header carries the rate from the call, so the provider follows
    whatever the pipeline runs at rather than pinning one."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"text": ""})

    await provider(handler).transcribe(b"\x01\x02" * 4800, 24000)

    with wave.open(io.BytesIO(uploaded_audio(seen[0])), "rb") as container:
        assert container.getframerate() == 24000
        assert container.getnchannels() == 1
        assert container.getsampwidth() == 2
        assert container.readframes(4800) == b"\x01\x02" * 4800


async def test_the_upload_is_named_so_the_api_can_read_the_format() -> None:
    """There is no file, but the endpoint decides the format from the
    extension, so the name it is given still has to be right."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"text": ""})

    await provider(handler).transcribe(ONE_SECOND, 16000)
    assert b'filename="utterance.wav"' in seen[0].content


async def test_optional_fields_are_sent_only_when_configured() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"text": ""})

    await provider(handler).transcribe(ONE_SECOND, 16000)
    assert form_field(seen[0], "language") is None
    assert form_field(seen[0], "prompt") is None
    assert form_field(seen[0], "temperature") is None

    configured = provider(handler, language="sv", prompt="samtal", temperature=0.2)
    await configured.transcribe(ONE_SECOND, 16000)
    assert form_field(seen[1], "language") == "sv"
    assert form_field(seen[1], "prompt") == "samtal"
    assert form_field(seen[1], "temperature") == "0.2"


async def test_a_configured_language_beats_the_session_hint() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"text": ""})

    await provider(handler, language="sv").transcribe(ONE_SECOND, 16000, language_hint="en")
    assert form_field(seen[0], "language") == "sv"

    await provider(handler).transcribe(ONE_SECOND, 16000, language_hint="en")
    assert form_field(seen[1], "language") == "en"


# --- what it reports -------------------------------------------------


async def test_the_transcript_is_stripped() -> None:
    result = await provider(transcript_handler("  Hej hej  \n")).transcribe(ONE_SECOND, 16000)
    assert result.text == "Hej hej"


async def test_no_language_is_reported_and_no_session_lock_is_asked_for() -> None:
    """The response carries no usable language and no confidence at all,
    so the fields stay empty rather than echoing the configuration back
    at the session as if it had been detected."""
    asr = provider(transcript_handler(), language="sv")
    result = await asr.transcribe(ONE_SECOND, 16000)
    assert result.language is None
    assert result.language_confidence is None
    assert result.lock_language is None


# --- audio too short to send -----------------------------------------


async def test_audio_under_the_api_minimum_is_answered_without_a_request() -> None:
    """The barge-in path transcribes snippets of tens of milliseconds to
    decide whether an interruption was real. The API refuses those, and
    the refusal would be logged as a failure rather than the non-answer
    it is."""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"text": "should not be reached"})

    asr = provider(handler)
    # 50 ms at 16 kHz, under the API's 0.1 s minimum.
    result = await asr.transcribe(b"\x00\x00" * 800, 16000)
    assert result.text == ""
    assert calls == 0

    # 100 ms exactly is sent.
    await asr.transcribe(b"\x00\x00" * 1600, 16000)
    assert calls == 1


async def test_the_minimum_follows_the_sample_rate() -> None:
    """Bytes are not milliseconds: the same buffer is long enough at
    16 kHz and too short at 48 kHz."""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"text": ""})

    asr = provider(handler)
    await asr.transcribe(b"\x00\x00" * 1600, 16000)
    assert calls == 1
    await asr.transcribe(b"\x00\x00" * 1600, 48000)
    assert calls == 1


# --- failures --------------------------------------------------------


async def test_an_api_error_raises_with_the_reason() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "invalid api key"}})

    with pytest.raises(APIStatusError, match="invalid api key"):
        await provider(handler).transcribe(ONE_SECOND, 16000)


async def test_a_failing_utterance_is_attempted_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """The SDK retries twice by default, which would make timeout_s a
    third of the truth: three attempts plus backoff with the user
    waiting on an answer that is already late."""
    monkeypatch.setenv("OPENAI_KEY", "secret")
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(500, json={"error": {"message": "upstream boom"}})

    built = build_asr(type="openai", api_key_env="OPENAI_KEY")
    assert isinstance(built, OpenAiAsr)
    # The built client is the one under test: retries are configured
    # where it is constructed, so a hand-made client would not prove it.
    built._client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[attr-defined]

    with pytest.raises(APIStatusError):
        await built.transcribe(ONE_SECOND, 16000)
    assert attempts == 1


async def test_the_timeout_is_the_one_the_entry_asked_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_KEY", "secret")
    built = build_asr(type="openai", api_key_env="OPENAI_KEY", timeout_s=7)
    assert isinstance(built, OpenAiAsr)
    assert built._client.timeout == 7  # type: ignore[attr-defined]
    assert built._client.max_retries == 0  # type: ignore[attr-defined]
