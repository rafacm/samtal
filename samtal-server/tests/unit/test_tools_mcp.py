"""The MCP server manager, against a real stdio server.

`tests/support/mcp_stdio_server.py` is spawned as a subprocess, so the
transport under test is the one that ships. No network, no keys, and
deterministic.
"""

import asyncio
import sys
from pathlib import Path

import pytest

from samtal_server.config import Config, McpServerConfig
from samtal_server.tools.mcp import (
    McpConfigError,
    McpServerDown,
    McpServerManager,
    McpServers,
)

STDIO_SERVER = Path(__file__).parents[1] / "support" / "mcp_stdio_server.py"


def stdio_entry(**overrides: object) -> McpServerConfig:
    return McpServerConfig.model_validate(
        {"transport": "stdio", "command": sys.executable, "args": [str(STDIO_SERVER)]}
        | overrides
    )


async def running(config: McpServerConfig, name: str = "tools") -> McpServerManager:
    manager = McpServerManager(name, config)
    await manager.start()
    return manager


async def test_a_started_server_offers_its_tools_under_its_entry_name() -> None:
    manager = await running(stdio_entry())
    try:
        assert manager.up
        offered = {tool.name for tool in manager.tools()}
        assert "tools__secret_word" in offered
        assert "tools__add" in offered
        (add,) = [tool for tool in manager.tools() if tool.name == "tools__add"]
        assert "Add two whole numbers" in add.description
        # The schema is JSON Schema on both sides; nothing is translated.
        assert add.input_schema["properties"].keys() == {"first", "second"}
    finally:
        await manager.stop()


async def test_a_tool_call_answers_with_its_text() -> None:
    manager = await running(stdio_entry())
    try:
        assert await manager.call("secret_word", {}) == ("rhubarb", False)
        assert await manager.call("add", {"first": 2, "second": 3}) == ("5", False)
    finally:
        await manager.stop()


async def test_a_failing_tool_answers_with_its_error_flag() -> None:
    manager = await running(stdio_entry())
    try:
        text, is_error = await manager.call("always_fails", {})
        assert is_error
        assert "broken on purpose" in text
    finally:
        await manager.stop()


async def test_a_dead_server_does_not_fail_the_start() -> None:
    # Configuration errors fail the boot; liveness is forgiven, because
    # a home automation box rebooting should not need this server to.
    manager = await running(stdio_entry(command="/nonexistent/mcp-server", args=[]))
    try:
        assert not manager.up
        assert manager.tools() == []
        with pytest.raises(McpServerDown):
            await manager.call("secret_word", {})
    finally:
        await manager.stop()


async def test_a_server_that_came_back_is_reconnected_in_the_background() -> None:
    manager = McpServerManager("tools", stdio_entry())
    manager._config = stdio_entry(command="/nonexistent/mcp-server", args=[])
    await manager.start()
    assert not manager.up

    manager._config = stdio_entry()
    manager.ensure_reconnecting()
    try:
        async with asyncio.timeout(20):
            while not manager.up:
                await asyncio.sleep(0.05)
        assert await manager.call("secret_word", {}) == ("rhubarb", False)
    finally:
        await manager.stop()


async def test_a_stopped_server_leaves_no_child_behind() -> None:
    manager = await running(stdio_entry())
    await manager.stop()
    assert not manager.up
    with pytest.raises(McpServerDown):
        await manager.call("secret_word", {})


def config_with(servers: dict[str, object], agent_mcp: list[str] | None) -> Config:
    agent: dict[str, object] = {"prompt": "A"}
    if agent_mcp is not None:
        agent["mcp"] = agent_mcp
    return Config(
        providers={
            "llm": {"mock": {"type": "mock"}},
            "asr": {"mock": {"type": "mock"}},
            "tts": {"mock": {"type": "mock"}},
            "vad": {"mock": {"type": "mock"}},
        },
        mcp_servers=servers,
        agent_defaults=dict.fromkeys(("llm", "asr", "tts", "vad"), "mock"),
        agents={"assistant": agent},
        default_agent="assistant",
    )


def entry_data(**overrides: object) -> dict[str, object]:
    return {
        "transport": "stdio",
        "command": sys.executable,
        "args": [str(STDIO_SERVER)],
    } | overrides


async def test_only_referenced_entries_are_managed() -> None:
    config = config_with({"tools": entry_data(), "unused": entry_data()}, ["tools"])
    servers = McpServers.build(config)
    assert len(servers) == 1
    assert "tools" in servers
    assert "unused" not in servers


async def test_the_registry_starts_lists_and_stops() -> None:
    config = config_with({"tools": entry_data()}, ["tools"])
    servers = McpServers.build(config)
    await servers.start_all()
    try:
        offered = {tool.name for tool in servers.tools_for(["tools"])}
        assert "tools__secret_word" in offered
        # An entry nobody manages contributes nothing rather than raising.
        assert servers.tools_for(["ghost"]) == []
        assert await servers.call("tools", "secret_word", {}) == ("rhubarb", False)
        assert servers.timeout_for("tools") == 15.0
    finally:
        await servers.stop_all()


async def test_a_per_entry_timeout_is_read_from_the_configuration() -> None:
    config = config_with({"tools": entry_data(tool_timeout_s=0.5)}, ["tools"])
    servers = McpServers.build(config)
    assert servers.timeout_for("tools") == 0.5
    assert servers.timeout_for("ghost") is None


async def test_an_unset_secret_reference_fails_the_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SAMTAL_TEST_MCP_TOKEN", raising=False)
    config = config_with(
        {"tools": entry_data(env={"API_TOKEN": "$SAMTAL_TEST_MCP_TOKEN"})}, ["tools"]
    )
    with pytest.raises(McpConfigError, match="SAMTAL_TEST_MCP_TOKEN"):
        McpServers.build(config)


async def test_a_resolved_secret_reaches_the_spawned_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SAMTAL_TEST_MCP_TOKEN", "sk-test")
    config = config_with(
        {"tools": entry_data(env={"API_TOKEN": "$SAMTAL_TEST_MCP_TOKEN"})}, ["tools"]
    )
    servers = McpServers.build(config)
    manager = servers._managers["tools"]
    assert manager._env == {"API_TOKEN": "sk-test"}
    # And the configuration itself never held the secret.
    assert config.mcp_servers["tools"].env == {"API_TOKEN": "$SAMTAL_TEST_MCP_TOKEN"}
