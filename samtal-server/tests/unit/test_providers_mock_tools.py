"""The mock LLM's scripted tool calling, which is what makes the tool
loop deterministic in CI."""

from collections.abc import Sequence

from samtal_server.config.models import ProviderConfig
from samtal_server.providers import (
    ProviderError,
    TextDelta,
    ToolCall,
    ToolResult,
    Turn,
    build_provider,
)
from samtal_server.providers.mock import MockLlm


def scripted(**options: object) -> MockLlm:
    config = ProviderConfig.model_validate({"type": "mock", **options})
    provider = build_provider("llm", "mock", config)
    assert isinstance(provider, MockLlm)
    return provider


async def events(llm: MockLlm, turns: Sequence[Turn], **kwargs: object) -> list[object]:
    return [event async for event in llm.stream("SYSTEM", turns, **kwargs)]  # type: ignore[arg-type]


def spoken(collected: Sequence[object]) -> str:
    return "".join(event.text for event in collected if isinstance(event, TextDelta))


async def test_without_tool_options_the_mock_only_speaks() -> None:
    llm = scripted(reply="You said {text}.")
    assert spoken(await events(llm, [Turn("user", "hello")])) == "You said hello."


async def test_the_trigger_phrase_asks_for_the_scripted_tool() -> None:
    llm = scripted(
        reply="The tool said {tool_result}.",
        tool_when="secret",
        tool_name="tools__secret_word",
        tool_arguments={"loudly": True},
    )
    collected = await events(llm, [Turn("user", "tell me the secret")])
    assert collected == [
        ToolCall(id="call_1", name="tools__secret_word", arguments={"loudly": True})
    ]


async def test_the_round_after_the_results_speaks_them() -> None:
    llm = scripted(reply="The tool said {tool_result}.", tool_when="secret", tool_name="t")
    turns = [
        Turn("user", "tell me the secret"),
        Turn("assistant", "", tool_calls=(ToolCall(id="call_1", name="t"),)),
        Turn("tool", "", tool_results=(ToolResult(tool_call_id="call_1", content="rhubarb"),)),
    ]
    collected = await events(llm, turns)
    assert not [event for event in collected if isinstance(event, ToolCall)]
    assert spoken(collected) == "The tool said rhubarb."


async def test_an_untriggered_utterance_never_calls_a_tool() -> None:
    llm = scripted(reply="You said {text}.", tool_when="secret", tool_name="t")
    collected = await events(llm, [Turn("user", "just talk to me")])
    assert spoken(collected) == "You said just talk to me."


async def test_a_forbidden_call_is_not_made() -> None:
    # The session's last permitted round passes tool_choice="none" so a
    # reply always ends in speech; the mock has to honour that or the
    # loop would never terminate.
    llm = scripted(reply="Fine, {tool_result}then.", tool_when="secret", tool_name="t")
    collected = await events(llm, [Turn("user", "the secret")], tool_choice="none")
    assert spoken(collected) == "Fine, then."


def test_a_tool_arguments_option_that_is_not_a_mapping_is_refused() -> None:
    try:
        scripted(tool_when="x", tool_name="t", tool_arguments="volume=5")
    except ProviderError as exc:
        assert "must be a mapping" in str(exc)
    else:
        raise AssertionError("a scalar tool_arguments should not build")
