"""Tools end to end: the milestone's acceptance and the cases around it.

The device is xiaozhi-sdk, the pipeline is the mock providers, and the
MCP server is a real one spawned over stdio, so the whole path (LLM
asks, server routes, tool answers, LLM speaks the answer) runs without
a key, a model, or a network. The mock LLM's tool calling is scripted,
which is what makes a conversation about tools deterministic.
"""

import sys
from pathlib import Path

import pytest

from tests.integration.conftest import dominant_hz, spoken
from vinga_server.config import Config
from vinga_server.config.models import DatabaseConfig
from vinga_server.memory.store import open_memory

STDIO_SERVER = Path(__file__).parents[1] / "support" / "mcp_stdio_server.py"

DEVICE_MAC = "aa:bb:cc:dd:ee:21"
SWITCHER_MAC = "aa:bb:cc:dd:ee:22"

POET_TONE = 440.0
TUTOR_TONE = 880.0


def stdio_server(**overrides: object) -> dict[str, object]:
    return {
        "transport": "stdio",
        "command": sys.executable,
        "args": [str(STDIO_SERVER)],
    } | overrides


def one_agent(llm: dict[str, object], **extra: object) -> Config:
    """One agent on the mock pipeline, with whatever tools the test
    is about."""
    return Config(
        **(
            {
                "providers": {
                    "llm": {"mock": llm},
                    "asr": {"mock": {"type": "mock", "text": "tell me the secret"}},
                    "tts": {"mock": {"type": "mock"}},
                    "vad": {"mock": {"type": "mock"}},
                },
                "agent_defaults": dict.fromkeys(("llm", "asr", "tts", "vad"), "mock"),
                "agents": {"assistant": {"prompt": "ASSISTANT"}},
                "devices": {DEVICE_MAC: ["assistant"]},
                "default_agent": "assistant",
            }
            | extra
        )
    )


async def test_a_conversation_triggers_an_mcp_tool_and_the_reply_reflects_it(
    serve, simulate
) -> None:
    """The M6 acceptance: a simulator conversation triggers a mock MCP
    tool, and the spoken reply carries the tool's answer."""
    config = one_agent(
        {
            "type": "mock",
            "reply": "The tool says {tool_result}.",
            "tool_when": "secret",
            "tool_name": "tools__secret_word",
        },
        mcp_servers={"tools": stdio_server()},
        agent_defaults=dict.fromkeys(("llm", "asr", "tts", "vad"), "mock")
        | {"mcp": ["tools"]},
    )
    async with serve(config) as port:
        events, audio = await simulate(port, DEVICE_MAC)

    assert spoken(events) == "The tool says rhubarb."
    # And it was actually spoken, not merely announced.
    assert audio.size > 0


async def test_the_model_reaches_a_tool_the_device_itself_provides(serve, simulate) -> None:
    # The device's tools are discovered over the same socket the audio
    # runs on, which also proves the initialize handshake against the
    # sdk's own implementation of it.
    def battery(arguments: dict) -> tuple[str, bool]:
        return "72 percent", False

    device_tool = {
        "name": "self.get_battery_level",
        "description": "How much charge the board has left.",
        "inputSchema": {"type": "object", "properties": {}},
        "tool_func": battery,
        "is_async": False,
    }
    config = one_agent(
        {
            "type": "mock",
            "reply": "The board says {tool_result}.",
            "tool_when": "secret",
            # The dotted device name, sanitized for the LLM APIs.
            "tool_name": "self_get_battery_level",
        }
    )
    async with serve(config) as port:
        events, _ = await simulate(port, DEVICE_MAC, [device_tool])

    # xiaozhi-sdk JSON-encodes whatever a device tool returns, so the
    # quotes are the device's, not this server's.
    assert spoken(events) == 'The board says "72 percent".'


def switching_config(target: str) -> Config:
    """A device bound to two agents, the first of which is scripted to
    hand the conversation to `target`."""
    return Config(
        providers={
            "llm": {
                "handover": {
                    "type": "mock",
                    "reply": "I cannot: {tool_result}",
                    "tool_when": "secret",
                    "tool_name": "switch_agent",
                    "tool_arguments": {"agent": target},
                },
                "plain": {"type": "mock", "reply": "{system} here, hello."},
            },
            "asr": {"mock": {"type": "mock", "text": "tell me the secret"}},
            "tts": {
                "tenor": {"type": "mock", "tone_hz": POET_TONE},
                "alto": {"type": "mock", "tone_hz": TUTOR_TONE},
            },
            "vad": {"mock": {"type": "mock"}},
        },
        agent_defaults={"asr": "mock", "vad": "mock"},
        agents={
            "poet": {"prompt": "POET", "llm": "handover", "tts": "tenor"},
            "tutor": {"prompt": "TUTOR", "llm": "plain", "tts": "alto"},
        },
        devices={SWITCHER_MAC: ["poet", "tutor"]},
        default_agent="poet",
    )


async def test_switching_agents_changes_the_prompt_and_the_voice(serve, simulate) -> None:
    # The M5 assertions, reused across a handover: the reply text comes
    # from the second agent's own prompt, and the audio is its voice.
    async with serve(switching_config("tutor")) as port:
        events, audio = await simulate(port, SWITCHER_MAC)

    assert spoken(events) == "TUTOR here, hello."
    assert abs(dominant_hz(audio) - TUTOR_TONE) < 20


async def test_a_switch_to_an_unbound_agent_is_answered_by_the_first(serve, simulate) -> None:
    async with serve(switching_config("stranger")) as port:
        events, audio = await simulate(port, SWITCHER_MAC)

    said = spoken(events)
    assert said.startswith("I cannot:")
    assert 'not bound to agent "stranger"' in said
    # Refused, so the poet is still the one talking.
    assert abs(dominant_hz(audio) - POET_TONE) < 20


async def test_a_fact_remembered_in_one_conversation_reaches_the_next(
    serve, simulate
) -> None:
    # The reply quotes the system prompt it was handed, so the injected
    # facts are visible in what the device hears. The second
    # conversation remembers the same fact again, which is what makes
    # its two copies proof that the first one survived the disconnect.
    fact = "the user is vegetarian"
    config = one_agent(
        {
            "type": "mock",
            "reply": "Memory: {system}",
            "tool_when": "secret",
            "tool_name": "remember",
            "tool_arguments": {"text": fact},
        },
    )
    async with serve(config) as port:
        first, _ = await simulate(port, DEVICE_MAC)
        # Read back through the store the server writes through, opened
        # here as a second process would open it.
        store = open_memory(DatabaseConfig())
        try:
            stored = store.read("assistant")
        finally:
            store.close()
        second, _ = await simulate(port, DEVICE_MAC)

    assert stored.splitlines() == [f"- {fact}"]
    assert spoken(first).count(fact) == 1
    assert spoken(second).count(fact) == 2


async def test_a_dead_mcp_server_still_boots_and_still_talks(serve, simulate) -> None:
    # Configuration errors fail the boot; being unreachable does not.
    config = one_agent(
        {"type": "mock", "reply": "Talking anyway."},
        mcp_servers={"tools": stdio_server(command="/nonexistent/mcp-server", args=[])},
        agent_defaults=dict.fromkeys(("llm", "asr", "tts", "vad"), "mock")
        | {"mcp": ["tools"]},
    )
    async with serve(config) as port:
        events, _ = await simulate(port, DEVICE_MAC)

    assert spoken(events) == "Talking anyway."
    assert [event["state"] for event in events if event.get("type") == "tts"][-1] == "stop"


async def test_a_tool_that_runs_long_is_cut_off_and_still_answered(serve, simulate) -> None:
    config = one_agent(
        {
            "type": "mock",
            "reply": "Sorry: {tool_result}",
            "tool_when": "secret",
            "tool_name": "tools__slow_answer",
            "tool_arguments": {"seconds": 30},
        },
        mcp_servers={"tools": stdio_server(tool_timeout_s=0.5)},
        agent_defaults=dict.fromkeys(("llm", "asr", "tts", "vad"), "mock")
        | {"mcp": ["tools"]},
    )
    async with serve(config) as port:
        events, _ = await simulate(port, DEVICE_MAC)

    said = spoken(events)
    assert "did not answer in time" in said
    # And the device was released: auto mode waits for this before it
    # listens again.
    assert [event["state"] for event in events if event.get("type") == "tts"][-1] == "stop"


@pytest.mark.parametrize("entry", ["self", "remember"])
async def test_a_reserved_server_name_fails_the_boot(entry: str) -> None:
    with pytest.raises(Exception, match=r"must match \[A-Za-z0-9_-\]\+"):
        one_agent({"type": "mock"}, mcp_servers={entry: stdio_server()})


async def test_a_server_tool_the_apis_would_refuse_is_still_reachable(
    serve, simulate
) -> None:
    # The stdio server publishes "weather.today/v2", which is legal MCP
    # and illegal in both LLM APIs' tool-name rule. It has to arrive
    # sanitized and still route back to the name the server listed,
    # otherwise it either breaks the request or cannot be called.
    config = one_agent(
        {
            "type": "mock",
            "reply": "The tool says {tool_result}.",
            "tool_when": "secret",
            "tool_name": "tools__weather_today_v2",
        },
        mcp_servers={"tools": stdio_server()},
        agent_defaults=dict.fromkeys(("llm", "asr", "tts", "vad"), "mock")
        | {"mcp": ["tools"]},
    )
    async with serve(config) as port:
        events, _ = await simulate(port, DEVICE_MAC)

    assert spoken(events) == "The tool says dotted answer."


# Per-tool grants, proven from what the model was offered


RESTRICTED_MAC = "aa:bb:cc:dd:ee:23"


def granting_config(kids_grant: object) -> Config:
    """Two agents on one MCP server, one of them restricted, each on a
    device of its own so neither is offered `switch_agent`. The reply is
    the list of tools the model was handed, which is the only place the
    offer is visible from outside the session."""
    return Config(
        providers={
            "llm": {"mock": {"type": "mock", "reply": "I have {tools}."}},
            "asr": {"mock": {"type": "mock", "text": "what can you do"}},
            "tts": {"mock": {"type": "mock"}},
            "vad": {"mock": {"type": "mock"}},
        },
        mcp_servers={"tools": stdio_server()},
        agent_defaults=dict.fromkeys(("llm", "asr", "tts", "vad"), "mock"),
        agents={
            "house": {"prompt": "HOUSE", "mcp": ["tools"]},
            "kids": {"prompt": "KIDS", "mcp": [kids_grant]},
        },
        devices={DEVICE_MAC: ["house"], RESTRICTED_MAC: ["kids"]},
        default_agent="house",
    )


def offered(events: list[dict]) -> set[str]:
    """The tool names the reply said it was given."""
    return set(spoken(events).removeprefix("I have ").rstrip(".").split(", "))


# Everything this server publishes under the `tools` entry, which is
# what an ungranted agent reaches. The two names the publishing rule
# drops (a dotted one, one too long once prefixed) are not here because
# they never reach a model at all.
PUBLISHED = {
    "tools__secret_word",
    "tools__add",
    "tools__slow_answer",
    "tools__always_fails",
    "tools__weather_today_v2",
    "tools__inside__secret_word",
}

# The builtins due in this configuration, which is the three that are
# always due. `switch_agent` is not among them: each device here is
# bound to a single agent, so there is nowhere to switch. `remember` is,
# because remembered facts live in a schema every server migrates at
# boot and no deployment is without one (#314); the two conversation
# tools are, because they are offered whether or not a deployment can
# resume anything: what a server that cannot answers with is a sentence
# the agent reads out, and a tool that is simply absent is a tool a
# model invents (#190).
# Spelled as a set the assertions below compare against, so that a
# conditional builtin appearing where its condition does not hold fails
# this test rather than passing under a subtraction.
DUE_BUILTINS: set[str] = {"remember", "new_conversation", "resume_conversation"}


async def test_a_restricted_agent_is_offered_exactly_its_subset(serve, simulate) -> None:
    """The issue's third verification step. Proven from the offer rather
    than from which calls happened: the model is free to call nothing,
    and a conversation that watched calls would pass with a forbidden
    tool sitting on the list.

    Both offers are compared whole. A grant decides the MCP half of the
    list and a structural condition decides the builtin half, and this
    is the one place both halves are visible at once, so an extra name
    from either of them is a failure here."""
    config = granting_config({"server": "tools", "tools": ["secret_word", "add"]})
    async with serve(config) as port:
        restricted, _ = await simulate(port, RESTRICTED_MAC)
        whole, _ = await simulate(port, DEVICE_MAC)

    assert offered(restricted) == DUE_BUILTINS | {"tools__secret_word", "tools__add"}
    # The sibling agent on the same server, so the subset is a
    # restriction rather than everything the server published.
    assert offered(whole) == DUE_BUILTINS | PUBLISHED
    assert offered(whole) > offered(restricted)
