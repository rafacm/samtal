"""The mock LLM's scripted tool calling, which is what makes the tool
loop deterministic in CI."""

from collections.abc import Sequence

from vinga_server.config.models import ProviderConfig
from vinga_server.providers import (
    ProviderError,
    StreamStarted,
    TextDelta,
    ToolCall,
    ToolDef,
    ToolResult,
    Turn,
    build_provider,
)
from vinga_server.providers.mock import MockLlm


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
        # The mock announces its first chunk the way the real adapters
        # do, so the tool loop in CI sees the same event flow.
        StreamStarted(),
        ToolCall(id="call_1", name="tools__secret_word", arguments={"loudly": True}),
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


async def test_the_reply_can_say_which_tools_it_was_offered() -> None:
    """What a session hands the model is otherwise invisible from
    outside it: `MockLlm.stream` ignored its `tools` argument, so a
    scripted conversation about which tools an agent may reach could
    only watch which calls happened, and a forbidden tool nobody called
    would have passed. The placeholder puts the offer in the reply, the
    trick `{system}` already plays for the prompt."""
    llm = scripted(reply="I have {tools}.")

    said = spoken(
        await events(
            llm,
            [Turn("user", "hello")],
            tools=[
                ToolDef(name="home__turn_on_light", description="", input_schema={}),
                ToolDef(name="home__turn_off_light", description="", input_schema={}),
            ],
        )
    )

    assert said == "I have home__turn_on_light, home__turn_off_light."


async def test_a_reply_that_names_no_tools_is_unaffected() -> None:
    # Backward compatible: every existing template is one of these.
    llm = scripted(reply="You said {text}.")
    assert spoken(await events(llm, [Turn("user", "hello")])) == "You said hello."
