"""The OpenAI TTS provider against a mock transport.

No extra to skip on and no network: the openai client is a core
dependency, and it accepts an `http_client`, so the whole provider
(options, request shape, streaming, failures) runs here through the
real SDK serialization. What a unit test cannot judge is how the voice
sounds and what the round trip costs, which is the PR's real-API
verification step.
"""

import asyncio
import json
from collections.abc import AsyncIterator

import httpx
import pytest
from openai import AsyncOpenAI

from samtal_server.config.models import ProviderConfig
from samtal_server.providers import ProviderCallError, ProviderCallTimeout, build_provider
from samtal_server.providers.base import ProviderError
from samtal_server.providers.openai_tts import OpenAiTts
from tests.support.llm_sdk import Falsey

# Not a real credential, and shaped so a substring check for it cannot
# match by accident. It stands in for what an endpoint can echo back
# into an error body.
SENTINEL = "sk-test-4f8b2c9e-never-a-real-credential"


def provider(handler: object, **overrides: object) -> OpenAiTts:
    """A provider wired to a mock transport, so nothing leaves the test."""
    client = AsyncOpenAI(
        api_key="test-key",
        # As the provider constructs its own: without this the SDK's
        # default of two retries would triple a deliberately failing
        # request, with its backoff, inside a unit test.
        max_retries=0,
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


def test_a_missing_voice_fails_the_build() -> None:
    with pytest.raises(ProviderError, match='"voice" is required'):
        build_tts(type="openai", api_key_env="OPENAI_KEY")


def test_a_missing_api_key_env_fails_the_build() -> None:
    with pytest.raises(ProviderError, match="needs an API key"):
        build_tts(type="openai", voice="alloy")


def test_an_unset_api_key_variable_fails_the_build(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_KEY", raising=False)
    with pytest.raises(ProviderError, match="references an unset environment variable"):
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
    let a trailing slash boot keyless and fail on the first synthesis,
    and would skip the model rules with it."""
    monkeypatch.delenv("OPENAI_KEY", raising=False)
    with pytest.raises(ProviderError, match="needs an API key"):
        build_tts(type="openai", voice="alloy", base_url=base_url)

    monkeypatch.setenv("OPENAI_KEY", "secret")
    with pytest.raises(ProviderError, match='ignores option "speed"'):
        build_tts(
            type="openai",
            voice="alloy",
            api_key_env="OPENAI_KEY",
            base_url=base_url,
            speed=1.2,
        )


@pytest.mark.parametrize("base_url", ["not-a-url", "api.openai.com/v1", "https://"])
def test_a_base_url_that_is_not_a_url_fails_the_build(
    base_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Including the one that looks like OpenAI but has no scheme, which
    would otherwise be treated as a compatible endpoint and boot
    keyless."""
    monkeypatch.setenv("OPENAI_KEY", "secret")
    with pytest.raises(ProviderError, match='"base_url" must be a URL'):
        build_tts(type="openai", voice="alloy", api_key_env="OPENAI_KEY", base_url=base_url)


def test_a_compatible_endpoint_keeps_its_own_model_rules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Which knob a model reads is a fact about OpenAI's models, not
    about the dialect. A compatible server may name a model anything and
    read either knob, so guessing on its behalf would reject working
    configurations before the request is sent."""
    monkeypatch.setenv("OPENAI_KEY", "secret")
    local = {"base_url": "http://localhost:8080/v1", "egress": False}
    # Each of these is refused against OpenAI itself, three tests above.
    for extra in (
        {"model": "gpt-4o-mini-tts", "speed": 1.2},
        {"model": "kokoro", "instructions": "Speak cheerfully"},
        {"model": "kokoro", "speed": 9.0},
    ):
        built = build_tts(type="openai", voice="alloy", **local, **extra)
        assert isinstance(built, OpenAiTts)


def test_the_base_url_decides_egress_rather_than_the_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A self-hosted speech endpoint keeps the reply text on the host and
    api.openai.com does not, so the type cannot know its own egress and
    the entry declares it, exactly as openai_compatible does."""
    monkeypatch.setenv("OPENAI_KEY", "secret")
    assert OpenAiTts.egress is None
    built = build_tts(
        type="openai",
        voice="alloy",
        api_key_env="OPENAI_KEY",
        base_url="http://localhost:8080/v1",
        egress=False,
    )
    assert isinstance(built, OpenAiTts)


def test_local_only_refuses_the_default_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_KEY", "secret")
    with pytest.raises(ProviderError, match="sends session data off this host"):
        build_provider(
            "tts",
            "voice",
            ProviderConfig.model_validate(
                {"type": "openai", "voice": "alloy", "api_key_env": "OPENAI_KEY", "egress": True}
            ),
            local_only=True,
        )


def test_local_only_admits_a_local_endpoint_that_declares_itself() -> None:
    built = build_provider(
        "tts",
        "voice",
        ProviderConfig.model_validate(
            {
                "type": "openai",
                "voice": "alloy",
                "base_url": "http://localhost:8080/v1",
                "egress": False,
            }
        ),
        local_only=True,
    )
    assert isinstance(built, OpenAiTts)


def test_a_local_endpoint_needs_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The SDK insists on a key; a self-hosted server usually does not."""
    monkeypatch.delenv("OPENAI_KEY", raising=False)
    built = build_tts(type="openai", voice="alloy", base_url="http://localhost:8080/v1")
    assert isinstance(built, OpenAiTts)


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


async def test_an_api_error_raises_the_taxonomy_with_the_status_and_no_body() -> None:
    """The status and the SDK class are trusted metadata; the vendor's
    own sentence is not, because the SDK embeds the response body in it
    and a compatible endpoint decides what that body says (#137)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": f"invalid api key {SENTINEL}"}})

    with pytest.raises(ProviderCallError) as failure:
        await collect(provider(handler))

    assert not isinstance(failure.value, ProviderCallTimeout)
    assert "HTTP 401" in str(failure.value)
    # The SDK's own class for a 401, which is the half of the message
    # that says what kind of failure it was.
    assert "AuthenticationError" in str(failure.value)
    assert SENTINEL not in chain(failure.value)


async def test_a_request_that_timed_out_raises_the_timeout_half() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("the endpoint never answered", request=request)

    with pytest.raises(ProviderCallTimeout, match="APITimeoutError"):
        await collect(provider(handler))


async def test_a_failure_after_the_first_chunk_is_wrapped_too() -> None:
    """A response that opened is not a response that arrived: the SDK
    rides httpx, and a transport error escapes the byte iterator wearing
    no SDK class at all."""

    async def stream() -> AsyncIterator[bytes]:
        yield b"\x01\x02"
        raise httpx.ReadError("the connection dropped")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=stream())

    tts = provider(handler)
    chunks: list[bytes] = []
    with pytest.raises(ProviderCallError) as failure:
        async for chunk in tts.synthesize("Hej"):
            chunks.append(chunk)

    assert chunks == [b"\x01\x02"]
    assert not isinstance(failure.value, ProviderCallTimeout)
    assert "ReadError" in str(failure.value)


async def test_a_timeout_after_the_first_chunk_is_still_a_timeout() -> None:
    async def stream() -> AsyncIterator[bytes]:
        yield b"\x01\x02"
        raise httpx.ReadTimeout("the rest never came")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=stream())

    with pytest.raises(ProviderCallTimeout):
        await collect(provider(handler))


async def test_a_non_sdk_failure_passes_through_unwrapped() -> None:
    """The taxonomy claims request failures, not all failures: a bug in
    this process must reach logger.exception as itself.

    Raised from the open stream rather than from the request, because
    the SDK converts everything the transport raises into an
    APIConnectionError of its own before this provider sees it; past
    that point an exception arrives as itself."""

    async def stream() -> AsyncIterator[bytes]:
        yield b"\x01\x02"
        raise ValueError("a local bug")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=stream())

    with pytest.raises(ValueError, match="a local bug"):
        await collect(provider(handler))


async def test_a_cancelled_synthesis_is_not_a_provider_failure() -> None:
    """Barge-in cancels a sentence mid-send, and a cancellation dressed
    as a provider failure would be reported as one."""

    async def stream() -> AsyncIterator[bytes]:
        yield b"\x01\x02"
        raise asyncio.CancelledError()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=stream())

    with pytest.raises(asyncio.CancelledError):
        await collect(provider(handler))


async def test_a_failed_request_leaks_nothing_into_the_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": f"upstream said {SENTINEL}"}})

    with caplog.at_level("DEBUG"), pytest.raises(ProviderCallError) as failure:
        await collect(provider(handler))

    assert SENTINEL not in chain(failure.value)
    assert SENTINEL not in caplog.text
    assert all(SENTINEL not in str(record.__dict__) for record in caplog.records)


async def test_a_failing_sentence_is_attempted_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """The SDK retries twice by default, which would make timeout_s a
    third of the truth: three attempts plus backoff inside one sentence
    of the serial TTS loop, with the device silent throughout."""
    monkeypatch.setenv("OPENAI_KEY", "secret")
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(500, json={"error": {"message": "upstream boom"}})

    built = build_tts(type="openai", voice="alloy", api_key_env="OPENAI_KEY")
    assert isinstance(built, OpenAiTts)
    # The built client is the one under test: retries are configured
    # where it is constructed, so a hand-made client would not prove it.
    built._client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[attr-defined]

    with pytest.raises(ProviderCallError):
        await collect(built)
    assert attempts == 1


async def test_the_timeout_is_the_one_the_entry_asked_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_KEY", "secret")
    built = build_tts(type="openai", voice="alloy", api_key_env="OPENAI_KEY", timeout_s=7)
    assert isinstance(built, OpenAiTts)
    assert built._client.timeout == 7  # type: ignore[attr-defined]
    assert built._client.max_retries == 0  # type: ignore[attr-defined]


async def test_a_falsey_injected_client_is_still_the_one_used() -> None:
    """`client or ...` drops a double that answers False to a truth test,
    which any object defining __bool__ or __len__ does, and builds a real
    client in its place."""
    given = Falsey()
    tts = OpenAiTts(voice="alloy", model="gpt-4o-mini-tts", api_key="test-key", client=given)  # type: ignore[arg-type]
    assert tts._client is given
