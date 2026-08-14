"""The session's tool loop, against scripted fake providers.

The loop is what makes tools a conversation rather than a request: it
speaks, executes, feeds results back, and speaks again, with a cap that
guarantees the reply ends in speech. switch_agent is the case only the
session can serve, because the round after it goes to a different
provider entirely.
"""

import asyncio
import json
import sys
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import samtal_server.runtime.pipeline as pipeline_module
from samtal_server.app import create_app
from samtal_server.config import Config
from samtal_server.device.session import DeviceSession
from samtal_server.providers import (
    LlmEvent,
    LlmProvider,
    TextDelta,
    ToolCall,
    ToolChoice,
    ToolDef,
    Turn,
    Usage,
    build_agent_providers,
)
from samtal_server.tools.builtin import switch_agent_tool
from samtal_server.tools.mcp import McpServers
from samtal_server.tools.memory import MemoryStore
from tests.unit.test_session import (
    DEVICE_HELLO,
    POET_TONE,
    TUTOR_TONE,
    connect,
    device_session,
    say_something,
    sentences,
    shake_hands,
    tone_strength,
)

POET_MAC = "aa:bb:cc:dd:ee:01"
BOTH_MAC = "aa:bb:cc:dd:ee:03"

STDIO_SERVER = Path(__file__).parents[1] / "support" / "mcp_stdio_server.py"


Step = str | list[str | ToolCall | Usage]


class ScriptedLlm(LlmProvider):
    """A model whose every round is written down in advance. A round is
    a sentence to speak, or a list mixing sentences, the tool calls to
    ask for, and the usage a provider that reports one would end with;
    the last round repeats if the loop asks for more."""

    def __init__(self, rounds: Sequence[Step]) -> None:
        self._rounds = list(rounds)
        self.seen: list[tuple[Sequence[Turn], Sequence[ToolDef], ToolChoice]] = []

    async def stream(
        self,
        system: str,
        turns: Sequence[Turn],
        tools: Sequence[ToolDef] = (),
        tool_choice: ToolChoice = "auto",
    ) -> AsyncIterator[LlmEvent]:
        self.seen.append((list(turns), list(tools), tool_choice))
        step = self._rounds[min(len(self.seen) - 1, len(self._rounds) - 1)]
        for item in [step] if isinstance(step, str) else step:
            yield TextDelta(item) if isinstance(item, str) else item


def call(name: str, **arguments: Any) -> ToolCall:
    return ToolCall(id=f"c-{name}", name=name, arguments=arguments)


def base_config(**overrides: object) -> Config:
    """Two agents that differ in prompt and voice, on mock providers."""
    return Config(
        **(
            {
                "providers": {
                    "llm": {"mock": {"type": "mock", "reply": "{system} heard {text}."}},
                    "asr": {"mock": {"type": "mock", "text": "hello"}},
                    "tts": {
                        "tenor": {"type": "mock", "tone_hz": POET_TONE},
                        "alto": {"type": "mock", "tone_hz": TUTOR_TONE},
                    },
                    "vad": {"mock": {"type": "mock"}},
                },
                "agent_defaults": {"llm": "mock", "asr": "mock", "vad": "mock"},
                "agents": {
                    "poet": {"prompt": "POET", "tts": "tenor"},
                    "tutor": {"prompt": "TUTOR", "tts": "alto"},
                },
                "devices": {POET_MAC: ["poet"], BOTH_MAC: ["poet", "tutor"]},
                "default_agent": "poet",
            }
            | overrides
        )
    )


def session_for(
    config: Config,
    mac: str,
    scripts: dict[str, ScriptedLlm] | None = None,
    memory: MemoryStore | None = None,
    fillers: dict[str, Any] | None = None,
    websocket: Any = None,
    mcp_servers: McpServers | None = None,
) -> DeviceSession:
    """A device session with a real bespoke runtime behind it, built the
    way `run` builds one, with the named agents' LLMs replaced by
    scripts. No websocket by default: these tests drive the loop
    directly and never speak."""
    providers = build_agent_providers(config)
    for agent, script in (scripts or {}).items():
        # The entry the script stands in for, so the events a session
        # emits about its LLM carry what a real one's would.
        script.identity = providers[agent].llm.identity
        providers[agent] = type(providers[agent])(
            prompt=providers[agent].prompt,
            llm=script,
            asr=providers[agent].asr,
            tts=providers[agent].tts,
            vad=providers[agent].vad,
        )
    return device_session(config, mac, providers, memory, fillers, websocket, mcp_servers)


async def run_reply(session: DeviceSession, said: str) -> list[str]:
    """One reply, with speaking stubbed out: what the loop decides is
    what these tests are about, not the audio."""
    spoken: list[str] = []

    async def speak(synthesis: Any, resampler: Any, into: list[str]) -> None:
        # Sentences reach _speak as a synthesis in flight now (#37), so
        # the stub takes the text off it and skips the audio entirely.
        synthesis.cancel()
        into.append(synthesis.sentence)

    session.runtime._speak = speak  # type: ignore[method-assign]
    session.send_audio = _nothing  # type: ignore[method-assign]
    session.runtime._turns.append(Turn("user", said))
    await session.runtime._speak_reply(said, spoken)
    if spoken:
        session.runtime._turns.append(Turn("assistant", " ".join(spoken)))
    return spoken


async def drive_reply(session: DeviceSession, pcm: bytes) -> None:
    """One whole reply, audio and all, run to completion.

    The two helpers below exist so that the characterization suite,
    which pins today's behavior from outside, names the reply entry
    point in one place instead of thirty. When the reply moves behind
    the device-facing boundary, these lines move with it and the tests
    that use them do not change."""
    await session.runtime._reply(pcm)


def start_reply(session: DeviceSession, pcm: bytes) -> asyncio.Task[None]:
    """A reply in flight, registered the way an utterance registers one,
    so that everything asking whether this session is replying (the idle
    watchdog, the shutdown, the barge-in gates) sees it."""
    session.runtime._reply_task = asyncio.create_task(session.runtime._reply(pcm))
    return session.runtime._reply_task


async def _nothing(*args: object, **kwargs: object) -> None:
    return None


async def test_a_reply_with_no_tool_calls_is_one_round() -> None:
    script = ScriptedLlm(["Nothing to look up."])
    session = session_for(base_config(), POET_MAC, {"poet": script})
    assert await run_reply(session, "hello") == ["Nothing to look up."]
    assert len(script.seen) == 1


async def test_an_unknown_tool_comes_back_as_an_error_result() -> None:
    script = ScriptedLlm([[call("ghost_tool")], "I could not do that."])
    session = session_for(base_config(), POET_MAC, {"poet": script})
    assert await run_reply(session, "do it") == ["I could not do that."]

    (result,) = [
        result for turns, _, _ in script.seen for turn in turns for result in turn.tool_results
    ]
    assert result.is_error
    assert "no tool called" in result.content


async def test_a_tool_that_never_answers_becomes_a_timeout_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pipeline_module, "DEFAULT_TOOL_TIMEOUT_S", 0.05)
    script = ScriptedLlm([[call("remember", text="a fact")], "Sorry, that took too long."])
    session = session_for(base_config(), POET_MAC, {"poet": script}, memory=_HangingStore())
    assert await run_reply(session, "remember this") == ["Sorry, that took too long."]

    (result,) = [
        result for turns, _, _ in script.seen for turn in turns for result in turn.tool_results
    ]
    assert result.is_error
    assert "did not answer in time" in result.content


class _HangingStore(MemoryStore):
    """A store whose writes never finish, so the loop's per-call timeout
    is the only thing that can end the reply."""

    def __init__(self) -> None:
        super().__init__(Path("/nonexistent"))

    async def remember(self, agent: str, fact: str) -> None:
        await asyncio.sleep(30)


async def test_the_round_cap_ends_the_reply_in_speech() -> None:
    # A model that keeps asking for tools must still stop talking: the
    # last permitted round forbids calling.
    script = ScriptedLlm([[call("ghost_tool")]] * 3 + ["All right, enough."])
    session = session_for(base_config(), POET_MAC, {"poet": script})
    assert await run_reply(session, "loop forever") == ["All right, enough."]

    choices = [choice for _, _, choice in script.seen]
    assert choices == ["auto", "auto", "auto", "none"]
    assert len(script.seen) == pipeline_module.MAX_TOOL_ROUNDS


async def test_history_keeps_the_speech_and_not_the_tool_exchange() -> None:
    script = ScriptedLlm([[call("ghost_tool")], "It did not work."])
    session = session_for(base_config(), POET_MAC, {"poet": script})
    await run_reply(session, "do it")

    assert session.runtime._turns == [Turn("user", "do it"), Turn("assistant", "It did not work.")]
    # The structured turns existed, but only inside the reply.
    assert any(turn.tool_calls for turns, _, _ in script.seen for turn in turns)


async def test_switch_agent_is_offered_only_where_there_is_somewhere_to_go() -> None:
    one = session_for(base_config(), POET_MAC)
    both = session_for(base_config(), BOTH_MAC)
    assert [tool.name for tool in one.runtime._tool_snapshot()] == []
    assert [tool.name for tool in both.runtime._tool_snapshot()] == ["switch_agent"]
    # The enum carries the device's full bound list, which is what lets
    # the agent answer "who can I talk to?".
    (tool,) = both.runtime._tool_snapshot()
    assert tool.input_schema["properties"]["agent"]["enum"] == ["poet", "tutor"]
    assert switch_agent_tool(["poet", "tutor"]).description.count("poet") == 1


async def test_a_successful_switch_hands_over_to_the_other_agent() -> None:
    poet = ScriptedLlm([[call("switch_agent", agent="tutor")]])
    tutor = ScriptedLlm(["Tutor here, hello."])
    session = session_for(base_config(), BOTH_MAC, {"poet": poet, "tutor": tutor})

    assert await run_reply(session, "get me the tutor") == ["Tutor here, hello."]
    assert session._agent == "tutor"
    assert session.runtime._providers is not None
    assert session.runtime._providers.prompt == "TUTOR"

    # The new agent saw the conversation so far plus an ephemeral turn
    # telling it to greet, and that turn is not in the history.
    (turns, _, _) = tutor.seen[0]
    assert turns[0] == Turn("user", "get me the tutor")
    assert turns[-1].content == pipeline_module.SWITCH_GREETING
    assert all(turn.content != pipeline_module.SWITCH_GREETING for turn in session.runtime._turns)


async def test_the_old_agents_words_stay_its_own_turn() -> None:
    # A preamble the poet spoke before handing over is its own assistant
    # turn: the switch happens between two turns, not inside one.
    poet = ScriptedLlm([["One moment.", call("switch_agent", agent="tutor")]])
    tutor = ScriptedLlm(["Tutor here."])
    session = session_for(base_config(), BOTH_MAC, {"poet": poet, "tutor": tutor})
    assert await run_reply(session, "the tutor please") == ["Tutor here."]
    assert [turn.content for turn in session.runtime._turns] == [
        "the tutor please",
        "One moment.",
        "Tutor here.",
    ]


async def test_a_switch_to_an_unbound_agent_is_refused_by_the_agent_talking() -> None:
    poet = ScriptedLlm(
        [[call("switch_agent", agent="stranger")], "I cannot reach that one."]
    )
    session = session_for(base_config(), BOTH_MAC, {"poet": poet})
    assert await run_reply(session, "get me the stranger") == ["I cannot reach that one."]
    assert session._agent == "poet"

    (result,) = [
        result for turns, _, _ in poet.seen for turn in turns for result in turn.tool_results
    ]
    assert result.is_error
    assert "not bound to" in result.content
    # The refusal names what the device can reach, so the model can say.
    assert "poet" in result.content and "tutor" in result.content


async def test_a_switch_to_the_agent_already_speaking_is_refused(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Naming the current persona mid-conversation used to hand over to
    # it: a second LLM round that only greeted a user already talking.
    poet = ScriptedLlm([[call("switch_agent", agent="poet")], "I am the poet already."])
    session = session_for(base_config(), BOTH_MAC, {"poet": poet})

    with caplog.at_level("INFO"):
        assert await run_reply(session, "let me talk to the poet") == ["I am the poet already."]
    assert session._agent == "poet"
    # No handover was announced, and the second round continued the
    # reply rather than greeting a user who is already mid-conversation.
    assert not [record for record in caplog.records if getattr(record, "event", "") == "handover"]
    assert all(
        turn.content != pipeline_module.SWITCH_GREETING
        for turns, _, _ in poet.seen
        for turn in turns
    )

    (result,) = [
        result for turns, _, _ in poet.seen for turn in turns for result in turn.tool_results
    ]
    assert result.is_error
    assert "already speaking as this assistant" in result.content


async def test_only_one_handover_happens_per_reply() -> None:
    poet = ScriptedLlm([[call("switch_agent", agent="tutor")]])
    tutor = ScriptedLlm([[call("switch_agent", agent="poet")], "Staying put, then."])
    session = session_for(base_config(), BOTH_MAC, {"poet": poet, "tutor": tutor})

    assert await run_reply(session, "keep switching") == ["Staying put, then."]
    assert session._agent == "tutor"
    (result,) = [
        result for turns, _, _ in tutor.seen for turn in turns for result in turn.tool_results
    ]
    assert result.is_error
    assert "already been handed over" in result.content


async def test_two_switches_in_one_round_honour_the_first_and_refuse_the_rest() -> None:
    poet = ScriptedLlm(
        [[call("switch_agent", agent="tutor"), call("switch_agent", agent="poet")]]
    )
    tutor = ScriptedLlm(["Tutor here."])
    session = session_for(base_config(), BOTH_MAC, {"poet": poet, "tutor": tutor})
    await run_reply(session, "both please")
    assert session._agent == "tutor"


async def test_remembering_is_offered_and_executed_when_memory_is_configured(
    tmp_path: Path,
) -> None:
    store = MemoryStore(tmp_path)
    script = ScriptedLlm(
        [[call("remember", text="the user is vegetarian")], "I will keep that in mind."]
    )
    session = session_for(base_config(), POET_MAC, {"poet": script}, memory=store)

    assert [tool.name for tool in session.runtime._tool_snapshot()] == ["remember"]
    assert await run_reply(session, "remember I am vegetarian") == ["I will keep that in mind."]
    assert "the user is vegetarian" in store.read("poet")

    (result,) = [
        result for turns, _, _ in script.seen for turn in turns for result in turn.tool_results
    ]
    assert not result.is_error


async def test_a_remembered_fact_is_in_the_next_replys_prompt(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    await store.remember("poet", "the user is vegetarian")
    session = session_for(base_config(), POET_MAC, memory=store)
    assert "the user is vegetarian" in session.runtime._system_prompt()
    assert session.runtime._system_prompt().startswith("POET")


async def test_malformed_arguments_come_back_as_an_error_result() -> None:
    broken = ToolCall(id="c1", name="remember", malformed_arguments="{text: oops")
    script = ScriptedLlm([[broken], "Let me try that again."])
    session = session_for(base_config(), POET_MAC, {"poet": script}, memory=None)
    assert await run_reply(session, "remember this") == ["Let me try that again."]

    (result,) = [
        result for turns, _, _ in script.seen for turn in turns for result in turn.tool_results
    ]
    assert result.is_error
    assert "not a JSON object" in result.content


def test_the_switch_agent_schema_is_json_serializable() -> None:
    # It goes over the wire to two different APIs; anything exotic in it
    # would fail there rather than here.
    json.dumps(switch_agent_tool(["poet", "tutor"]).input_schema)


async def test_a_device_bound_to_two_agents_is_answered_in_the_new_voice() -> None:
    # The M5 assertions, reused across a handover: the reply text comes
    # from the second agent's own prompt and the audio from its voice.
    config = base_config(
        providers={
            "llm": {
                "poetic": {"type": "mock", "reply": "{system} says hello."},
                "handover": {
                    "type": "mock",
                    "reply": "unused",
                    "tool_when": "tutor",
                    "tool_name": "switch_agent",
                    "tool_arguments": {"agent": "tutor"},
                },
            },
            "asr": {"mock": {"type": "mock", "text": "the tutor please"}},
            "tts": {
                "tenor": {"type": "mock", "tone_hz": POET_TONE},
                "alto": {"type": "mock", "tone_hz": TUTOR_TONE},
            },
            "vad": {"mock": {"type": "mock"}},
        },
        agent_defaults={"asr": "mock", "vad": "mock"},
        agents={
            "poet": {"prompt": "POET", "llm": "handover", "tts": "tenor"},
            "tutor": {"prompt": "TUTOR", "llm": "poetic", "tts": "alto"},
        },
    )
    with TestClient(create_app(config)) as client:
        with connect(client, device_id=BOTH_MAC) as websocket:
            shake_hands(websocket)
            texts, audio = say_something(websocket)

    assert sentences(texts) == ["TUTOR says hello."]
    assert tone_strength(audio, TUTOR_TONE) > 10 * tone_strength(audio, POET_TONE)


# What a reload changes for a conversation that is already running


def registry_config(granted: bool) -> Config:
    """The configuration the MCP registry is built from, which is not
    the one the session was built from: the session's carries no MCP
    entries at all, so a tool that turns up in its snapshot can only
    have come from the registry."""
    return base_config(
        mcp_servers={
            "tools": {
                "transport": "stdio",
                "command": sys.executable,
                "args": [str(STDIO_SERVER)],
            }
        },
        agents={
            "poet": {"prompt": "POET", "tts": "tenor", "mcp": ["tools"] if granted else []},
            "tutor": {"prompt": "TUTOR", "tts": "alto"},
        },
    )


async def test_a_reload_between_replies_changes_what_the_next_one_may_reach() -> None:
    """The promise the whole reload path exists for, at the seam where a
    session meets it: the snapshot is taken per reply and asks the
    registry by agent, so a grant written and reloaded mid-conversation
    is on offer in the next reply and no session was dropped."""
    servers = McpServers.build(registry_config(granted=False))
    await servers.start_all()
    script = ScriptedLlm(["First.", "Second."])
    session = session_for(base_config(), POET_MAC, {"poet": script}, mcp_servers=servers)
    try:
        await run_reply(session, "hello")
        offered_before = {tool.name for tool in script.seen[0][1]}

        await servers.reload(lambda: (registry_config(granted=True), None))
        await run_reply(session, "hello again")
    finally:
        await servers.stop_all()

    assert not any(name.startswith("tools__") for name in offered_before)
    assert "tools__secret_word" in {tool.name for tool in script.seen[-1][1]}


async def test_a_hello_without_mcp_gets_no_device_tool_client() -> None:
    with TestClient(create_app(base_config())) as client:
        with connect(client, device_id=POET_MAC) as websocket:
            websocket.send_text(json.dumps(dict(DEVICE_HELLO, features={})))
            websocket.receive_text()
            # A device that never advertised MCP sending one anyway is
            # logged and ignored rather than ending the session.
            websocket.send_text(json.dumps({"type": "mcp", "payload": {"jsonrpc": "2.0"}}))
            texts, _ = say_something(websocket)
    assert sentences(texts) == ["POET heard hello."]


async def test_a_tool_of_an_entry_whose_name_holds_the_separator_is_dispatched() -> None:
    """The session's own routing, on the name shape that used to break
    it: `home__inside` is a legal entry name, and reading the name by
    splitting at the first separator looked for a server called `home`,
    so the tool was offered in the snapshot and answered "there is no
    tool called" when the model asked for it. Both the dispatch and the
    per-entry timeout ask the registry which entry owns the name."""
    config = base_config(
        mcp_servers={
            "home__inside": {
                "transport": "stdio",
                "command": sys.executable,
                "args": [str(STDIO_SERVER)],
                "tool_timeout_s": 7.5,
            }
        },
        agents={
            "poet": {"prompt": "POET", "tts": "tenor", "mcp": ["home__inside"]},
            "tutor": {"prompt": "TUTOR", "tts": "alto"},
        },
    )
    servers = McpServers.build(config)
    await servers.start_all()
    script = ScriptedLlm([[call("home__inside__secret_word")], "The word is rhubarb."])
    session = session_for(base_config(), POET_MAC, {"poet": script}, mcp_servers=servers)
    try:
        assert await run_reply(session, "tell me") == ["The word is rhubarb."]
    finally:
        await servers.stop_all()

    offered = {tool.name for tool in script.seen[0][1]}
    assert "home__inside__secret_word" in offered
    (result,) = [
        result for turns, _, _ in script.seen for turn in turns for result in turn.tool_results
    ]
    assert not result.is_error
    assert result.content == "rhubarb"
    # The entry's own timeout, found through the same resolution the
    # dispatch used rather than by reading the name here.
    assert session.runtime._timeout_for("home__inside__secret_word") == 7.5
