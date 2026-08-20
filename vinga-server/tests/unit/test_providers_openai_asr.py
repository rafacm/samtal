"""The OpenAI ASR provider against a mock transport.

No extra to skip on and no network: the openai client is a core
dependency, and it accepts an `http_client`, so the whole provider
(options, the WAV upload, the request shape, failures) runs here
through the real SDK serialization. What a unit test cannot judge is
how well it hears and what the round trip costs, which is the PR's
real-API verification step.
"""

import asyncio
import io
import logging
import wave
from collections.abc import Iterator

import httpx
import pytest
from openai import AsyncOpenAI

from tests.support.events import events as emitted
from tests.support.events import fields_of
from tests.support.llm_sdk import Falsey
from vinga_server.config.models import ProviderConfig
from vinga_server.events import Emission, attach_server_tap, detach_server_tap
from vinga_server.logs import TEXT_FORMAT, JsonFormatter
from vinga_server.providers import (
    ProviderCallError,
    ProviderCallTimeout,
    build_provider,
    openai_asr,
)
from vinga_server.providers.base import ProviderError
from vinga_server.providers.openai_asr import OpenAiAsr

# One 16 kHz second of s16le silence, comfortably over the API minimum.
ONE_SECOND = b"\x00\x00" * 16000

# Not a real credential, and shaped so a substring check for it cannot
# match by accident. It stands in for what an endpoint can echo back
# into an error body.
SENTINEL = "sk-test-4f8b2c9e-never-a-real-credential"

# What the model hands back on the retry in the sentinel test below.
# The same shape, and a different value from the one above, so a hit
# says which path let it through: an error body the far side wrote, or
# a transcript a person spoke. A user reading a key aloud is a turn like
# any other, which is what makes this the honest stand-in for what a
# recovered transcript can be.
RECOVERED = "sk-test-9d3a7b1c-never-a-real-credential"


def mock_client(handler: object) -> AsyncOpenAI:
    """An SDK client that answers from the handler, so nothing leaves the
    test."""
    return AsyncOpenAI(
        api_key="test-key",
        # As the provider constructs its own: without this the SDK's
        # default of two retries would triple a deliberately failing
        # request and hide how many the provider itself sends.
        max_retries=0,
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
        ),
    )


def provider(handler: object, **overrides: object) -> OpenAiAsr:
    """A provider wired to a mock transport, so nothing leaves the test."""
    client = mock_client(handler)
    options: dict[str, object] = {
        "model": "gpt-4o-mini-transcribe",
        "api_key": "test-key",
        "client": client,
    }
    options.update(overrides)
    return OpenAiAsr(**options)  # type: ignore[arg-type]


def transported(built: OpenAiAsr, handler: object) -> OpenAiAsr:
    """The provider the registry built, with its own client answering
    from the handler instead of from OpenAI.

    White-box, deliberately, and this is the only shape of reach-in this
    file keeps. The client a deployment gets is built inside the
    provider and handed to nobody, so how many attempts it makes, how
    long it waits, and what it puts on the wire are observable only
    against the real endpoint. Swapping the transport under that client
    is what puts it under a test at all; a hand-made client would be a
    different object carrying different settings.
    """
    built._client._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(handler)  # type: ignore[arg-type]
    )
    return built


def transcript_handler(text: str = "Hej hej") -> object:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"text": text})

    return handler


def build_asr(**options: object) -> object:
    return build_provider("asr", "ears", ProviderConfig.model_validate(options))


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


class Tap:
    """A server-scope consumer that keeps every emission it was handed.

    A clean `LogRecord` does not prove a clean consumer, which is what
    the server pin suite says about its own sentinels. Non-log taps are
    dispatched first and are handed the emission's own `args` tuple,
    whose members are deliberately not copied, so anything passed as a
    `%` argument reaches every consumer as the object itself. A claim
    that a value reaches no retained surface is therefore asserted here
    as well as at the log."""

    def __init__(self) -> None:
        self.seen: list[Emission] = []

    def emit(self, emission: Emission) -> None:
        self.seen.append(emission)

    def saw(self, event: str) -> list[Emission]:
        return [one for one in self.seen if one.payload.get("event") == event]

    def rendered(self) -> str:
        """Everything a consumer could read off what it was handed: the
        unrendered sentence, the payload, and every argument behind
        it."""
        parts: list[str] = []
        for emission in self.seen:
            parts += [emission.message, str(emission.payload), repr(emission.args)]
            for argument in emission.args:
                parts += [str(argument), repr(argument)]
        return "\n".join(parts)


@pytest.fixture
def tap() -> Iterator[Tap]:
    """A consumer attached to the server hub for one test, which is what
    a #66/#67 exporter will be. Detached however the test ends, since
    the hub outlives it."""
    consumer = Tap()
    attach_server_tap(consumer)
    try:
        yield consumer
    finally:
        detach_server_tap(consumer)


def surfaces(record: logging.LogRecord) -> str:
    """Every retained rendering of one record: the unrendered template,
    the sentence a reader sees, the arguments substituted into it, the
    structured fields, and both shipped log formats."""
    return "\n".join(
        [
            str(record.msg),
            record.getMessage(),
            repr(record.args),
            str(fields_of(record)),
            logging.Formatter(TEXT_FORMAT).format(record),
            JsonFormatter().format(record),
        ]
    )


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
    with pytest.raises(ProviderError, match="references an unset environment variable"):
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

    configured = provider(handler, language="sv", prompt="vinga", temperature=0.2)
    await configured.transcribe(ONE_SECOND, 16000)
    assert form_field(seen[1], "language") == "sv"
    assert form_field(seen[1], "prompt") == "vinga"
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


# --- an echoed prompt ------------------------------------------------


async def test_a_transcript_that_is_the_prompt_is_treated_as_nothing_said() -> None:
    """The model hands the prompt back on short or low-content audio.
    It reached a field session as an utterance the user never said, and
    the prompt named the agents, so the model read it as a request and
    handed over."""
    asr = provider(
        transcript_handler("vinga, Oliver, Greta, Mateo"),
        prompt="vinga, Oliver, Greta, Mateo",
    )
    assert (await asr.transcribe(ONE_SECOND, 16000)).text == ""


async def test_the_echo_is_matched_trimmed_and_case_insensitively() -> None:
    asr = provider(transcript_handler("  VINGA, oliver  \n"), prompt=" vinga, Oliver ")
    assert (await asr.transcribe(ONE_SECOND, 16000)).text == ""


async def test_an_echo_written_as_a_sentence_is_still_an_echo() -> None:
    """The model is transcribing, so it sometimes ends the prompt it
    hands back with a full stop. Seen once in 45 provoked echoes."""
    asr = provider(
        transcript_handler("Vinga, Oliver, Greta, Mateo."),
        prompt="vinga, Oliver, Greta, Mateo",
    )
    assert (await asr.transcribe(ONE_SECOND, 16000)).text == ""


async def test_a_transcript_that_merely_contains_the_prompt_is_kept() -> None:
    """Someone can say the words in the prompt. Only a transcript that
    is the prompt and nothing else is an echo."""
    asr = provider(transcript_handler("vinga, are you there?"), prompt="vinga")
    assert (await asr.transcribe(ONE_SECOND, 16000)).text == "vinga, are you there?"


async def test_an_entry_with_no_prompt_suppresses_nothing() -> None:
    asr = provider(transcript_handler("vinga"))
    assert (await asr.transcribe(ONE_SECOND, 16000)).text == "vinga"


# --- the retry behind the echo guard (#69) ---------------------------


async def test_an_echo_is_retried_without_the_prompt_and_the_retry_is_heard(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The field test behind #69: a user answers "yes, please", the
    model echoes the prompt, and the guard used to treat the echo as
    proof of silence. The same clip transcribes fine without the
    prompt's help, so the retry's transcript is the one the session
    hears."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        text = "vinga, Oliver" if len(seen) == 1 else "Yes, please."
        return httpx.Response(200, json={"text": text})

    asr = provider(handler, prompt="vinga, Oliver", language="sv", temperature=0.2)
    with caplog.at_level("INFO"):
        result = await asr.transcribe(ONE_SECOND, 16000)

    assert result.text == "Yes, please."
    assert len(seen) == 2
    # The retry withholds only the prompt; the pinned language and the
    # temperature still steer the second hearing like the first.
    assert form_field(seen[0], "prompt") == "vinga, Oliver"
    assert form_field(seen[1], "prompt") is None
    assert form_field(seen[1], "language") == "sv"
    assert form_field(seen[1], "temperature") == "0.2"
    (event,) = [r for r in caplog.records if getattr(r, "event", None) == "asr_prompt_echo"]
    assert event.outcome == "recovered"  # type: ignore[attr-defined]
    assert event.duration_s == 1.0  # type: ignore[attr-defined]


async def test_a_recovered_transcript_reaches_no_record_or_consumer(
    caplog: pytest.LogCaptureFixture, tap: Tap
) -> None:
    """The sentinel for the one echo-guard outcome that has a transcript
    in hand. It used to be quoted into the `recovered` sentence, and
    conversation-derived text is banned on the events without exception
    (the content-and-telemetry ADR, as amended 2026-08-17): a transcript
    is content however it was recovered. Planted as a credential,
    because that is what the ban is worth: a user reading a key aloud is
    a turn like any other, and the retained log is not where it belongs.

    Both retained surfaces, because a clean record does not prove a
    clean consumer: the tap sees the emission before the log does.

    And every record rather than the event's, because the retained log
    is the whole log. This path writes plain `logger` calls beside its
    events (the retry announcement is one of them, on the very branch
    that has the transcript in hand), and a sentinel that filtered to
    `asr_prompt_echo` first would watch one line and bless the file."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        text = "vinga, Oliver" if len(seen) == 1 else RECOVERED
        return httpx.Response(200, json={"text": text})

    asr = provider(handler, prompt="vinga, Oliver")
    with caplog.at_level("DEBUG"):
        result = await asr.transcribe(ONE_SECOND, 16000)

    # The session is supposed to hear it. The surfaces are not.
    assert result.text == RECOVERED
    assert len(seen) == 2

    # Every line the run wrote, whatever channel it landed on and
    # whether or not it was an event.
    assert caplog.records, "nothing was logged at all, so this proves nothing"
    for record in caplog.records:
        assert RECOVERED not in surfaces(record), record.name

    records = emitted(caplog, "asr_prompt_echo")
    assert records, "the guard never fired, so this proves nothing"
    for record in records:
        assert record.outcome == "recovered"  # type: ignore[attr-defined]

    consumed = tap.saw("asr_prompt_echo")
    assert consumed, "it reached no tap at all, so this proves nothing"
    for emission in consumed:
        assert emission.payload["outcome"] == "recovered"
        assert RECOVERED not in emission.message
        assert RECOVERED not in repr(emission.args)
        assert RECOVERED not in str(emission.payload)
    assert RECOVERED not in tap.rendered()

    # And the diagnosis survives it: how much audio the guard was about
    # to discard, and what the second hearing cost.
    (event,) = records
    assert event.duration_s == 1.0  # type: ignore[attr-defined]
    assert event.retry_ms >= 0  # type: ignore[attr-defined]


async def test_a_retry_that_echoes_again_confirms_nothing_was_said(
    caplog: pytest.LogCaptureFixture,
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"text": "vinga, Oliver"})

    asr = provider(handler, prompt="vinga, Oliver")
    with caplog.at_level("INFO"):
        result = await asr.transcribe(ONE_SECOND, 16000)

    assert result.text == ""
    assert len(seen) == 2
    (event,) = [r for r in caplog.records if getattr(r, "event", None) == "asr_prompt_echo"]
    assert event.outcome == "confirmed_echo"  # type: ignore[attr-defined]


async def test_a_retry_that_comes_back_empty_confirms_the_silence(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Genuine silence or noise transcribes to nothing once the prompt
    is withheld, which is the guard's original story confirmed."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        text = "vinga, Oliver" if len(seen) == 1 else ""
        return httpx.Response(200, json={"text": text})

    asr = provider(handler, prompt="vinga, Oliver")
    with caplog.at_level("INFO"):
        result = await asr.transcribe(ONE_SECOND, 16000)

    assert result.text == ""
    assert len(seen) == 2
    (event,) = [r for r in caplog.records if getattr(r, "event", None) == "asr_prompt_echo"]
    assert event.outcome == "confirmed_empty"  # type: ignore[attr-defined]


async def test_a_transcript_that_is_not_the_prompt_is_never_retried(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Only the tripped guard pays for a second round trip; the normal
    path stays one request per utterance."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"text": "Hej hej"})

    asr = provider(handler, prompt="vinga, Oliver")
    with caplog.at_level("INFO"):
        result = await asr.transcribe(ONE_SECOND, 16000)

    assert result.text == "Hej hej"
    assert len(seen) == 1
    assert not [r for r in caplog.records if getattr(r, "event", None) == "asr_prompt_echo"]


async def test_every_timeout_reaching_the_transport_is_a_real_number() -> None:
    """The per-request timeout is a client option, not a form field, so
    it may never carry the Omit serialization sentinel the neighbouring
    fields use: Omit is not a NotGiven to the SDK, so it flows through
    to httpx as the literal connect timeout and fails every ordinary
    call at connect time. A mock transport never connects, which is how
    the suite missed it and the deployment did not (#75); what the
    transport CAN see is the request's timeout extension, so this pins
    every phase of it to a real number or None, on the ordinary path
    where no per-request timeout is given at all."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"text": "Hej hej"})

    asr = provider(handler)
    await asr.transcribe(ONE_SECOND, 16000)

    (request,) = seen
    for phase, value in request.extensions["timeout"].items():
        assert value is None or isinstance(value, (int, float)), (phase, value)


async def test_the_retry_lives_on_what_the_first_request_left_of_the_timeout() -> None:
    """Client retries are off so timeout_s bounds the user's wait, and
    a retry with a fresh timeout of its own would quietly double that
    bound. The retry request therefore carries the remaining budget as
    its own deadline."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        text = "vinga, Oliver" if len(seen) == 1 else "Yes, please."
        return httpx.Response(200, json={"text": text})

    asr = provider(handler, prompt="vinga, Oliver", timeout_s=30.0)
    result = await asr.transcribe(ONE_SECOND, 16000)

    assert result.text == "Yes, please."
    # httpx carries the per-request override in the request extensions,
    # which is where the far end of the mock transport can see it.
    assert seen[1].extensions["timeout"]["read"] <= 30.0


async def test_an_echo_with_no_budget_left_is_not_retried(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A first request that ate nearly the whole timeout leaves the
    retry more likely to be cut off than to answer, so it is skipped
    and the clip discarded directly rather than making the user wait
    out a request that was never going to land."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"text": "vinga, Oliver"})

    # The budget is below the one-second retry floor before the first
    # request even starts, which stands in for a first request that
    # consumed almost all of a real timeout without a slow test.
    asr = provider(handler, prompt="vinga, Oliver", timeout_s=0.5)
    with caplog.at_level("INFO"):
        result = await asr.transcribe(ONE_SECOND, 16000)

    assert result.text == ""
    assert len(seen) == 1
    (event,) = [r for r in caplog.records if getattr(r, "event", None) == "asr_prompt_echo"]
    assert event.outcome == "skipped"  # type: ignore[attr-defined]
    assert not hasattr(event, "retry_ms")


async def test_the_deadline_is_absolute_rather_than_per_connection_phase(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The SDK's timeout argument is an httpx timeout, which is per
    phase: passed alone, the retry could spend the remaining budget on
    each of connect, write and read. The asyncio deadline around the
    request is what makes the budget end to end, so a retry that keeps
    one phase alive without answering is still cut off when the shared
    budget runs out. The mock transport enforces no httpx timeout at
    all, so this test hangs for the whole sleep if the absolute
    deadline is ever removed."""
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, json={"text": "vinga, Oliver"})
        # Far longer than the whole budget, inside what would be a
        # single read phase.
        await asyncio.sleep(30)
        return httpx.Response(200, json={"text": "never delivered"})

    # A tiny budget keeps the test fast; the floor comes down with it
    # so the retry is still sent rather than skipped.
    monkeypatch.setattr(openai_asr, "RETRY_FLOOR_S", 0.0)
    asr = provider(handler, prompt="vinga, Oliver", timeout_s=0.05)
    loop = asyncio.get_running_loop()
    with caplog.at_level("INFO"):
        started = loop.time()
        result = await asr.transcribe(ONE_SECOND, 16000)
        elapsed = loop.time() - started

    assert result.text == ""
    assert calls == 2
    assert elapsed < 1.0
    (event,) = [r for r in caplog.records if getattr(r, "event", None) == "asr_prompt_echo"]
    assert event.outcome == "timed_out"  # type: ignore[attr-defined]


async def test_a_retry_cut_off_by_the_deadline_discards_rather_than_fails(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The reply a timed-out retry would end is one the guard was about
    to end anyway, so the timeout is the discarding outcome rather than
    an error surfaced to the session. Raised by the transport here, so
    this pins the SDK's own timeout class beside the asyncio deadline
    the test above pins."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(200, json={"text": "vinga, Oliver"})
        raise httpx.ReadTimeout("the deadline came first", request=request)

    asr = provider(handler, prompt="vinga, Oliver")
    with caplog.at_level("INFO"):
        result = await asr.transcribe(ONE_SECOND, 16000)

    assert result.text == ""
    assert len(seen) == 2
    (event,) = [r for r in caplog.records if getattr(r, "event", None) == "asr_prompt_echo"]
    assert event.outcome == "timed_out"  # type: ignore[attr-defined]
    assert event.retry_ms >= 0  # type: ignore[attr-defined]


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


async def test_the_minimum_belongs_to_the_endpoint_not_the_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 0.1 s floor was measured against OpenAI, so it is applied
    only there, like the model rules and the temperature range."""
    monkeypatch.setenv("OPENAI_KEY", "secret")
    # 50 ms at 16 kHz: under the floor OpenAI was measured against.
    short = b"\x00\x00" * 800
    at_openai: list[bytes] = []
    at_compatible: list[bytes] = []

    def watching(seen: list[bytes]) -> object:
        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.content)
            return httpx.Response(200, json={"text": "ja"})

        return handler

    openai = build_asr(type="openai", api_key_env="OPENAI_KEY")
    compatible = build_asr(type="openai", base_url="http://localhost:8000/v1", egress=False)
    assert isinstance(openai, OpenAiAsr)
    assert isinstance(compatible, OpenAiAsr)
    transported(openai, watching(at_openai))
    transported(compatible, watching(at_compatible))

    assert (await openai.transcribe(short, 16000)).text == ""
    assert at_openai == [], "the clip went to OpenAI, which would refuse it"
    assert (await compatible.transcribe(short, 16000)).text == "ja"
    assert len(at_compatible) == 1


async def test_a_compatible_endpoint_receives_the_short_clip_openai_would_refuse() -> None:
    """A self-hosted server may accept shorter audio, and dropping a clip
    it would have answered would silently suppress a barge-in it could
    have confirmed."""
    seen: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.content)
        return httpx.Response(200, json={"text": "ja"})

    built = build_asr(type="openai", base_url="http://localhost:8000/v1", egress=False)
    assert isinstance(built, OpenAiAsr)
    transported(built, handler)

    # 50 ms: refused by OpenAI, and dropped by the guard against it.
    result = await built.transcribe(b"\x00\x00" * 800, 16000)
    assert len(seen) == 1
    assert result.text == "ja"


async def test_empty_audio_is_never_sent_anywhere() -> None:
    """Not even to an endpoint that declared no minimum: there is
    nothing in the buffer to transcribe."""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"text": "should not be reached"})

    built = build_asr(type="openai", base_url="http://localhost:8000/v1", egress=False)
    assert isinstance(built, OpenAiAsr)
    transported(built, handler)

    assert (await built.transcribe(b"", 16000)).text == ""
    assert calls == 0


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


async def test_an_api_error_raises_the_taxonomy_with_the_status_and_no_body() -> None:
    """The taxonomy applies to the failure of transcribe as a whole. The
    status and the SDK class are trusted metadata; the vendor's own
    sentence is not, because the SDK embeds the response body in it and
    a compatible endpoint decides what that body says (#137)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": f"invalid api key {SENTINEL}"}})

    with pytest.raises(ProviderCallError) as failure:
        await provider(handler).transcribe(ONE_SECOND, 16000)

    assert not isinstance(failure.value, ProviderCallTimeout)
    assert "HTTP 401" in str(failure.value)
    # The SDK's own class for a 401, which is the half of the message
    # that says what kind of failure it was.
    assert "AuthenticationError" in str(failure.value)
    assert SENTINEL not in chain(failure.value)


async def test_a_first_request_that_timed_out_surfaces_as_a_timeout() -> None:
    """The other half of the split the echo retry forces: a timeout on
    the request that opens the call is a failure the session hears
    about, while a timeout inside the echo retry stays the discarding
    outcome the test below pins."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("the endpoint never answered", request=request)

    with pytest.raises(ProviderCallTimeout, match="APITimeoutError"):
        await provider(handler).transcribe(ONE_SECOND, 16000)


async def test_a_non_sdk_failure_passes_through_unwrapped() -> None:
    """The taxonomy claims request failures, not all failures: a bug in
    this process must reach logger.exception as itself.

    Raised by a client double rather than by the mock transport, because
    this provider reads the whole response inside the SDK's request
    path, and the SDK converts everything raised down there into an
    APIConnectionError of its own before this provider sees it. A bug in
    the calling code above it is what remains, and this is it."""

    class Transcriptions:
        async def create(self, **_options: object) -> object:
            raise ValueError("a local bug")

    client = type(
        "Client", (), {"audio": type("Audio", (), {"transcriptions": Transcriptions()})()}
    )()
    asr = OpenAiAsr(model="gpt-4o-mini-transcribe", api_key="test-key", client=client)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="a local bug"):
        await asr.transcribe(ONE_SECOND, 16000)


async def test_a_failed_request_leaks_nothing_into_the_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": f"upstream said {SENTINEL}"}})

    with caplog.at_level("DEBUG"), pytest.raises(ProviderCallError) as failure:
        await provider(handler).transcribe(ONE_SECOND, 16000)

    assert SENTINEL not in chain(failure.value)
    assert SENTINEL not in caplog.text
    assert all(SENTINEL not in str(record.__dict__) for record in caplog.records)


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
    transported(built, handler)

    with pytest.raises(ProviderCallError):
        await built.transcribe(ONE_SECOND, 16000)
    assert attempts == 1


async def test_the_timeout_is_the_one_the_entry_asked_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_KEY", "secret")
    built = build_asr(type="openai", api_key_env="OPENAI_KEY", timeout_s=7)
    assert isinstance(built, OpenAiAsr)
    # White-box, deliberately: a deployment's client is built inside the
    # provider and handed to nobody, so its timeout and its retry budget
    # are observable only against the real vendor. What they bound is one
    # turn's worst case, which is why they are asserted at all.
    assert built._client.timeout == 7  # type: ignore[attr-defined]
    assert built._client.max_retries == 0  # type: ignore[attr-defined]


async def test_a_falsey_injected_client_is_still_the_one_used() -> None:
    """`client or ...` drops a double that answers False to a truth test,
    which any object defining __bool__ or __len__ does, and builds a real
    client in its place. Asked the way a caller would see it: the
    transcript only arrives if the request went to the injected client's
    transport, and a dropped one would have gone to OpenAI."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"text": "Hej hej"})

    asr = provider(handler, client=Falsey(mock_client(handler)))

    assert (await asr.transcribe(ONE_SECOND, 16000)).text == "Hej hej"
    assert len(seen) == 1
