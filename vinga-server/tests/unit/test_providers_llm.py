"""Building the LLM providers and shaping their requests. Actual
streaming needs a live endpoint; that is the local lane's job."""

import json
from pathlib import Path

import httpx
import pytest
from openai import AsyncOpenAI

from tests.support.llm_sdk import (
    FakeChoice,
    FakeChunk,
    FakeCompletions,
    FakeDelta,
    FakeMessage,
    FakeMessages,
    FakeStream,
)
from tests.support.llm_sdk import Falsey as FalseyClient
from vinga_server.config.models import ProviderConfig
from vinga_server.providers import (
    LlmProvider,
    ProviderError,
    TextDelta,
    Turn,
    build_entry,
    openai_llm,
)
from vinga_server.providers.anthropic_llm import AnthropicLlm
from vinga_server.providers.kit import DEFAULT_MAX_TOKENS, DEFAULT_TIMEOUT_S, MAX_RETRIES
from vinga_server.providers.openai_endpoint import DEFAULT_BASE_URL, OPENAI_HOST
from vinga_server.providers.openai_llm import OpenAiCompatibleLlm, chat_messages


def provider_config(**data: object) -> ProviderConfig:
    return ProviderConfig.model_validate(data)


async def test_anthropic_requires_a_model() -> None:
    with pytest.raises(ProviderError, match='"model" is required'):
        await build_entry("llm", "claude", provider_config(type="anthropic"))


async def test_a_named_but_unset_api_key_env_fails_the_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VINGA_TEST_KEY", raising=False)
    config = provider_config(
        type="anthropic", model="claude-sonnet-5", api_key_env="VINGA_TEST_KEY"
    )
    with pytest.raises(ProviderError, match="references an unset environment variable") as failure:
        await build_entry("llm", "claude", config)
    # The entry, not the reference: what an operator wrote in that field
    # is not repeated back into a sentence main prints and the logs keep.
    assert "providers.llm.claude" in str(failure.value)
    assert "VINGA_TEST_KEY" not in str(failure.value)


async def test_a_set_api_key_env_builds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VINGA_TEST_KEY", "sk-test")
    config = provider_config(
        type="anthropic", model="claude-sonnet-5", api_key_env="VINGA_TEST_KEY"
    )
    assert isinstance(await build_entry("llm", "claude", config), AnthropicLlm)


async def test_openai_compatible_requires_a_base_url() -> None:
    """The subject `required_string` used to hold, through the model:
    there is no endpoint to speak to without one, and the refusal names
    the field it is about."""
    with pytest.raises(ProviderError, match="base_url") as failure:
        await build_entry("llm", "local", provider_config(type="openai_compatible", model="qwen3"))
    assert "providers.llm.local" in str(failure.value)


async def test_openai_compatible_requires_a_model() -> None:
    """The other required name, at the same gate.

    Only the missing endpoint was pinned here, and the parity table
    cannot cover either: its harness supplies both so that a case about
    one option is not failed by the absence of another. A model is as
    required as an endpoint is, and for the same reason `required_string`
    was: there is nothing to ask the endpoint for without one.
    """
    config = provider_config(type="openai_compatible", base_url="http://localhost:11434/v1")
    with pytest.raises(ProviderError, match="model") as failure:
        await build_entry("llm", "local", config)
    assert "providers.llm.local" in str(failure.value)
    assert failure.value.__cause__ is None


async def test_openai_compatible_builds_keyless_for_local_endpoints() -> None:
    config = provider_config(
        type="openai_compatible", base_url="http://localhost:11434/v1", model="qwen3:8b"
    )
    assert isinstance(await build_entry("llm", "local", config), OpenAiCompatibleLlm)


@pytest.mark.parametrize("base_url", ["not-a-url", "api.openai.com/v1", "https://"])
async def test_an_openai_compatible_base_url_that_is_not_a_url_fails_the_build(
    base_url: str,
) -> None:
    """The rule the `openai` ASR and TTS types already hold, in the
    stage that joined the dialect late. A `base_url` with no host used
    to build a provider that failed every request instead of the boot
    that read it, and one that looks like OpenAI without a scheme is
    the case that costs most: no host to compare, so it would have been
    treated as a compatible endpoint and booted keyless."""
    config = provider_config(type="openai_compatible", base_url=base_url, model="qwen3:8b")
    with pytest.raises(ProviderError, match='"base_url" must be a URL') as failure:
        await build_entry("llm", "local", config)
    assert "providers.llm.local" in str(failure.value)


async def test_a_credential_pasted_where_the_base_url_goes_is_not_echoed() -> None:
    """`base_url` is the credential-bearing option under an innocuous
    name: an operator pasting a key into it writes a value with no host,
    which is this refusal's own case and the one shape `url_credential`
    cannot mask. The sentence would otherwise reach stderr, the API's
    422 body and the boot log with the key in it.

    What is left is asserted beside what is gone, because a refusal that
    said nothing would pass the absence half and leave an operator with
    no way to find the entry: the entry's own name, the rule it broke,
    and an example of a URL that keeps it."""
    # Not a real credential, and shaped so a substring check for it
    # cannot match by accident.
    secret = "sk-proj-9c4e17ab-never-a-real-credential"
    config = provider_config(type="openai_compatible", base_url=secret, model="qwen3:8b")

    with pytest.raises(ProviderError) as failure:
        await build_entry("llm", "local", config)

    refusal = str(failure.value)
    assert secret not in refusal
    assert "9c4e17ab" not in refusal
    assert "providers.llm.local" in refusal
    assert "must be a URL with a scheme and a host" in refusal
    assert DEFAULT_BASE_URL in refusal


# --- the client each entry builds ------------------------------------


async def test_the_anthropic_client_carries_the_timeout_and_sends_one_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Injecting a client proves nothing about the one a deployment gets:
    a constructor that forgot both arguments would pass every test that
    hands its own client in. Until this issue these clients had no
    timeout at all and the SDK's two retries, which would make any
    bound three attempts plus backoff inside one turn."""
    monkeypatch.setenv("VINGA_TEST_KEY", "sk-test")
    built = await build_entry(
        "llm",
        "claude",
        provider_config(type="anthropic", model="claude-sonnet-5", api_key_env="VINGA_TEST_KEY"),
    )
    assert isinstance(built, AnthropicLlm)
    # White-box, deliberately: a deployment's client is built inside the
    # provider and handed to nobody, so its timeout and its retry budget
    # are observable only against the real vendor. What they bound is one
    # turn's worst case, which is why they are asserted at all.
    assert built._client.timeout == DEFAULT_TIMEOUT_S
    assert built._client.max_retries == MAX_RETRIES


async def test_the_openai_compatible_client_carries_the_timeout_and_sends_one_attempt() -> None:
    built = await build_entry(
        "llm",
        "local",
        provider_config(
            type="openai_compatible", base_url="http://localhost:11434/v1", model="qwen3:8b"
        ),
    )
    assert isinstance(built, OpenAiCompatibleLlm)
    # White-box, deliberately: a deployment's client is built inside the
    # provider and handed to nobody, so its timeout and its retry budget
    # are observable only against the real vendor. What they bound is one
    # turn's worst case, which is why they are asserted at all.
    assert built._client.timeout == DEFAULT_TIMEOUT_S
    assert built._client.max_retries == MAX_RETRIES


def anthropic_client(messages: FakeMessages) -> object:
    return type("Client", (), {"messages": messages})()


def openai_client(completions: FakeCompletions) -> object:
    return type("Client", (), {"chat": type("Chat", (), {"completions": completions})()})()


async def spoken(llm: LlmProvider) -> list[str]:
    """One round through the provider, as the pipeline drives it."""
    return [
        event.text async for event in llm.stream("be brief", [Turn("user", "hi")])
        if isinstance(event, TextDelta)
    ]


async def test_an_injected_anthropic_client_is_used_as_given() -> None:
    """The seam the other three cloud providers already had, and what
    the tool-calling tests now arrive through. Proved by driving a round
    through it: a client that was dropped would send this request to
    Anthropic instead of to the double that recorded it."""
    messages = FakeMessages(FakeStream(["Said."], FakeMessage([])))
    llm = AnthropicLlm(
        model="claude-sonnet-5",
        max_tokens=64,
        api_key="sk-test",
        client=anthropic_client(messages),  # type: ignore[arg-type]
    )

    assert await spoken(llm) == ["Said."]
    assert messages.request["model"] == "claude-sonnet-5"


async def test_an_injected_openai_compatible_client_is_used_as_given() -> None:
    completions = FakeCompletions([FakeChunk([FakeChoice(FakeDelta(content="Said."))])])
    llm = OpenAiCompatibleLlm(
        base_url="http://localhost:11434/v1",
        model="qwen3:8b",
        max_tokens=64,
        api_key=None,
        client=openai_client(completions),  # type: ignore[arg-type]
    )

    assert await spoken(llm) == ["Said."]
    assert completions.request["model"] == "qwen3:8b"


async def test_a_compatible_endpoint_is_not_asked_for_token_counts() -> None:
    """`stream_options` is an OpenAI field a compatible server is free
    not to know, so asking a self-hosted endpoint for usage could fail a
    conversation to enrich a log line. The host decides, which is why
    the same round against OpenAI's own asks: a refusal added at the
    factory must not have moved that answer for either of them.

    Two halves, because a deployment's client is built inside the
    provider and handed to nobody, so no request a factory-built entry
    sends can be read without reaching into it. What can be read is the
    host it was built with, which is the whole of what the decision
    below is taken on, so the halves meet there: the factory hands the
    entry's own `base_url` through, and a provider holding that host
    shapes the request this way. A factory that passed the default URL
    instead of what the entry wrote would fail the first half."""
    for name, base_url, host in (
        ("local", "http://localhost:11434/v1", "localhost"),
        ("hosted", "https://api.openai.com/v1", OPENAI_HOST),
    ):
        built = await build_entry(
            "llm",
            name,
            provider_config(type="openai_compatible", base_url=base_url, model="qwen3:8b"),
        )
        assert isinstance(built, OpenAiCompatibleLlm)
        assert built.host == host

    local = FakeCompletions([FakeChunk([FakeChoice(FakeDelta(content="Said."))])])
    openai = FakeCompletions([FakeChunk([FakeChoice(FakeDelta(content="Said."))])])

    def llm(base_url: str, completions: FakeCompletions) -> OpenAiCompatibleLlm:
        return OpenAiCompatibleLlm(
            base_url=base_url,
            model="qwen3:8b",
            max_tokens=64,
            api_key=None,
            client=openai_client(completions),  # type: ignore[arg-type]
        )

    assert await spoken(llm("http://localhost:11434/v1", local)) == ["Said."]
    assert await spoken(llm("https://api.openai.com/v1", openai)) == ["Said."]

    assert "stream_options" not in local.request
    assert openai.request["stream_options"] == {"include_usage": True}


# The escape hatch, and what it may not reach
#
# A transport that answers every request itself, so a real SDK client can
# be driven with no network, no key and no vendor, and what it recorded
# is the JSON that left the process. `data: [DONE]` is an empty but
# well-formed stream, which is all these cases need: what is under test
# is the request, and the reply is the shortest legal one.


def recording(sent: dict[str, object]) -> httpx.AsyncClient:
    """An HTTP client that files each request body under `sent`."""

    def answer(request: httpx.Request) -> httpx.Response:
        sent.update(json.loads(request.content))
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=b"data: [DONE]\n\n",
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(answer))


def recorded(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """The same transport put where a FACTORY-built provider will find
    it, which is the client class the builder reaches for.

    Reaching into the object the factory returned would leave the one
    thing under test untested: a deployment's client is built inside the
    provider from what `build` handed it, so a forwarding dropped
    between the entry and the constructor is invisible to any case that
    supplies its own client. Replacing the class keeps `build` and
    `__init__` real and only changes what carries the bytes.
    """
    sent: dict[str, object] = {}
    real = AsyncOpenAI

    def client(**arguments: object) -> AsyncOpenAI:
        return real(**arguments, http_client=recording(sent))  # type: ignore[arg-type]

    monkeypatch.setattr(openai_llm, "AsyncOpenAI", client)
    return sent
#
# `openai_compatible` is the one type whose options model keeps its door
# open, and an accepted key that went nowhere would be the silently
# ignored configuration the whole issue exists to remove: the reader
# this model replaces refused every leftover, so nothing was ever
# ignored, and the hatch has to be better than that rather than worse.
# So these ask the question at the wire.


async def test_the_cap_on_a_reply_is_the_one_the_kit_names() -> None:
    """The model states the default as a number because it may not
    import the kit (the kit speaks httpx, and the declaration is on
    three paths that load no client library). This is the side that may
    import both, so this is where the two are held together."""
    built = await build_entry(
        "llm",
        "local",
        provider_config(
            type="openai_compatible", base_url="http://localhost:11434/v1", model="qwen3:8b"
        ),
    )
    assert isinstance(built, OpenAiCompatibleLlm)
    assert built._max_tokens == DEFAULT_MAX_TOKENS


# The configured cap, which until #277 could not be configured at all
#
# `max_tokens` contains the fragment `token`, so the shared secret-key
# heuristic refused the option on every write surface: both builders
# read a key no entry could carry, and the default above always won.
# That is a claim about the value reaching the provider, so it is
# asserted where the value arrives rather than at `.options`.

CONFIGURED_MAX_TOKENS = 2048


async def test_a_configured_cap_reaches_the_anthropic_provider() -> None:
    """The untyped builder's half. `anthropic` declares no options
    model, so its reader takes the key off the entry's pass-through
    extras, which is exactly what the heuristic used to empty."""
    built = await build_entry(
        "llm",
        "claude",
        provider_config(
            type="anthropic",
            model="claude-sonnet-5",
            max_tokens=CONFIGURED_MAX_TOKENS,
        ),
    )

    assert isinstance(built, AnthropicLlm)
    assert built._max_tokens == CONFIGURED_MAX_TOKENS
    # Named as not being the default, because the default winning
    # silently is the defect itself: an assertion that could be
    # satisfied by it would be asserting nothing.
    assert CONFIGURED_MAX_TOKENS != DEFAULT_MAX_TOKENS


async def test_a_configured_cap_reaches_the_openai_compatible_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The typed builder's half, and the one case taken to the wire.

    Two halves, for the reason the passthrough cases have two: a
    deployment's client is built inside the provider and handed to
    nobody, so what a factory-built entry sends is read off the
    transport rather than off the object. A factory that dropped the
    option would fail the first; a provider that held it and composed
    the default would fail the second.
    """
    sent: dict[str, object] = recorded(monkeypatch)

    built = await build_entry(
        "llm",
        "local",
        provider_config(
            type="openai_compatible",
            base_url="http://localhost:11434/v1",
            model="qwen3:8b",
            max_tokens=CONFIGURED_MAX_TOKENS,
        ),
    )

    assert isinstance(built, OpenAiCompatibleLlm)
    assert built._max_tokens == CONFIGURED_MAX_TOKENS
    assert await spoken(built) == []
    assert sent["max_tokens"] == CONFIGURED_MAX_TOKENS


async def test_an_option_this_repository_never_heard_of_reaches_the_endpoint() -> None:
    """The hatch taking effect. `top_p` is nobody's declared option here
    and every server speaking this dialect takes one, so it is written
    on the entry, survives validation as an extra, and arrives in the
    request the provider sends.

    Two halves, for the reason the usage case below has two: a
    deployment's client is built inside the provider and handed to
    nobody, so no request a factory-built entry sends can be read
    without reaching into it. What can be read is what the factory
    handed over, and the halves meet there. A factory that dropped the
    extras would fail the first; a provider that held them and sent
    nothing would fail the second.
    """
    built = await build_entry(
        "llm",
        "local",
        provider_config(
            type="openai_compatible",
            base_url="http://localhost:11434/v1",
            model="qwen3:8b",
            top_p=0.9,
            keep_alive="30m",
        ),
    )
    assert isinstance(built, OpenAiCompatibleLlm)
    assert built._passthrough == {"top_p": 0.9, "keep_alive": "30m"}

    completions = FakeCompletions([FakeChunk([FakeChoice(FakeDelta(content="Said."))])])
    llm = OpenAiCompatibleLlm(
        base_url="http://localhost:11434/v1",
        model="qwen3:8b",
        max_tokens=64,
        api_key=None,
        client=openai_client(completions),  # type: ignore[arg-type]
        passthrough=built._passthrough,
    )

    assert await spoken(llm) == ["Said."]
    assert completions.request["extra_body"] == {"top_p": 0.9, "keep_alive": "30m"}


async def test_a_passthrough_option_is_a_top_level_field_of_the_request_body() -> None:
    """And what `extra_body` means, against the SDK rather than against
    a description of it.

    The case above records the argument the provider passes; this one
    records the JSON that leaves the process, because the claim being
    made is about the outgoing request body and nothing short of the
    body can settle it. A real client over a transport that answers
    every request itself: no network, no key, no vendor.

    The reserved half rides along, one level deeper than the write-time
    refusal that normally stops it: a provider constructed directly is a
    seam of its own, and the promise that a passthrough key cannot
    rewrite the request is kept there too. It has to be, since the SDK
    merges `extra_body` OVER the fields the provider sets rather than
    under them.
    """
    sent: dict[str, object] = {}
    client = AsyncOpenAI(
        base_url="http://endpoint.invalid/v1",
        api_key="unused",
        http_client=recording(sent),
    )
    llm = OpenAiCompatibleLlm(
        base_url="http://endpoint.invalid/v1",
        model="qwen3:8b",
        max_tokens=64,
        api_key=None,
        client=client,
        passthrough={"top_p": 0.9, "model": "hijacked", "messages": []},
    )

    assert await spoken(llm) == []

    assert sent["top_p"] == 0.9
    assert sent["model"] == "qwen3:8b"
    assert sent["max_tokens"] == 64
    assert sent["messages"] == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "hi"},
    ]


async def test_the_committed_body_of_this_type_reaches_the_endpoint_whole(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The compatibility fixture, taken all the way to the wire.

    `domain-bodies/provider/every-field.json` was written as an
    `openai_compatible` entry carrying options no builder read, to prove
    a body could hold anything and still parse. Under #88 that type is
    the escape hatch, which is the recorded reason the fixture needed no
    compatibility decision: the same body parses, and its four unread
    keys stop being inert.

    "Stop being inert" is a claim about the request, so it is asserted
    against the request. The bodies suite says those keys survive
    validation as `model_extra`, and a builder that dropped, renamed or
    filtered any of them would pass that and fail this. Keys AND values,
    since forwarding a key with the wrong value under it is the same
    silence in a different shape, and `null` under `connection.timeout_s`
    is the one an `exclude_unset` reflex would eat.
    """
    monkeypatch.setenv("MY_PROVIDER_KEY", "sk-test")
    body = json.loads(
        (
            Path(__file__).parent / "data" / "domain-bodies" / "provider" / "every-field.json"
        ).read_text(encoding="utf-8")
    )
    sent = recorded(monkeypatch)

    built = await build_entry("llm", "local", provider_config(**body))
    assert isinstance(built, OpenAiCompatibleLlm)
    assert await spoken(built) == []

    for key, value in body.items():
        if key in ("type", "api_key_env", "egress", "base_url"):
            continue
        assert sent[key] == value, key
    # Named as well as walked, so that a fixture edited down to nothing
    # would fail here rather than pass vacuously.
    assert sent["temperature"] == 0.7
    assert sent["max_reply_length"] == 512
    assert sent["stop"] == ["\n\n"]
    assert sent["connection"] == {"retries": 2, "timeout_s": None}
    # And the fields the type composes are its own, unmoved.
    assert sent["model"] == "a-model"
    assert sent["stream"] is True


async def test_a_passthrough_key_naming_a_request_field_fails_the_build() -> None:
    """And the ordinary way in, where it is refused before anything is
    built at all: the model says which names the request composes, and
    the refusal says which one was written."""
    config = provider_config(
        type="openai_compatible",
        base_url="http://localhost:11434/v1",
        model="qwen3:8b",
        tool_choice="none",
    )
    with pytest.raises(ProviderError, match="tool_choice") as failure:
        await build_entry("llm", "local", config)

    assert "providers.llm.local" in str(failure.value)
    assert failure.value.__cause__ is None


async def test_a_falsey_anthropic_client_is_still_the_one_used() -> None:
    messages = FakeMessages(FakeStream(["Said."], FakeMessage([])))
    llm = AnthropicLlm(
        model="claude-sonnet-5",
        max_tokens=64,
        api_key="sk-test",
        client=FalseyClient(anthropic_client(messages)),  # type: ignore[arg-type]
    )

    assert await spoken(llm) == ["Said."]
    assert messages.request["model"] == "claude-sonnet-5"


async def test_a_falsey_openai_compatible_client_is_still_the_one_used() -> None:
    completions = FakeCompletions([FakeChunk([FakeChoice(FakeDelta(content="Said."))])])
    llm = OpenAiCompatibleLlm(
        base_url="http://localhost:11434/v1",
        model="qwen3:8b",
        max_tokens=64,
        api_key=None,
        client=FalseyClient(openai_client(completions)),  # type: ignore[arg-type]
    )

    assert await spoken(llm) == ["Said."]
    assert completions.request["model"] == "qwen3:8b"


def test_chat_messages_prepend_the_system_prompt() -> None:
    turns = [Turn("user", "hi"), Turn("assistant", "hello"), Turn("user", "bye")]
    assert chat_messages("be brief", turns) == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "bye"},
    ]
    assert chat_messages("", turns)[0]["role"] == "user"
