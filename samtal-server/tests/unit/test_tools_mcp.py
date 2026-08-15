"""The MCP server manager, against a real stdio server.

`tests/support/mcp_stdio_server.py` is spawned as a subprocess, so the
transport under test is the one that ships. No network, no keys, and
deterministic.
"""

import asyncio
import logging
import re
import socket
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from mcp import ClientSession

from samtal_server import logs
from samtal_server.config import Config, McpServerConfig
from samtal_server.logs import _STANDARD_ATTRIBUTES
from samtal_server.runtime.prompt import Guidance
from samtal_server.tools import names
from samtal_server.tools.mcp import (
    CALL_FAILED,
    CONNECTED,
    DISCOVERY_FAILED,
    DOWN,
    DROPPED_AFTER_FAILED_CALL,
    STOPPED,
    TRANSPORT_FAILED,
    UNUSED,
    McpConfigError,
    McpServerDown,
    McpServerManager,
    McpServers,
    McpToolNotGranted,
)
from tests.support.mcp_stdio_server import SHADOWED_TOOL_ENV

STDIO_SERVER = Path(__file__).parents[1] / "support" / "mcp_stdio_server.py"

# A secret shaped like something an LLM API would accept as a tool name,
# for the sentinel below: nothing but letters and digits, so the
# publishing rule's sanitizing leaves it exactly as it is.
CREDENTIAL = "AKIAIOSFODNN7EXAMPLE"

# Where the test server lists `inside__secret_word`, which is the tool
# an entry called `home` publishes into `home__inside`'s namespace. The
# seventh it registers, and the sixth never publishes (too long once a
# prefix is on it), so the two ways of counting disagree here, which is
# the whole reason this number is spelled out rather than assumed.
SHADOWED_POSITION = 7

# What this module logs under, which is what an operator reads.
MANAGER_LOGGER = "samtal_server.tools.mcp"

# What a reason may look like: type names, and a group's several joined
# with commas. Anything a far side wrote has spaces, punctuation or
# quotes in it and does not match.
REASON_TOKEN = re.compile(r"^[A-Za-z][A-Za-z0-9]*(, [A-Za-z][A-Za-z0-9]*)*$")


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
        assert await manager.call("tools__secret_word", {}) == ("rhubarb", False)
        assert await manager.call("tools__add", {"first": 2, "second": 3}) == ("5", False)
    finally:
        await manager.stop()


async def test_a_failing_tool_answers_with_its_error_flag() -> None:
    manager = await running(stdio_entry())
    try:
        text, is_error = await manager.call("tools__always_fails", {})
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
            await manager.call("tools__secret_word", {})
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
        assert await manager.call("tools__secret_word", {}) == ("rhubarb", False)
    finally:
        await manager.stop()


async def test_a_stopped_server_leaves_no_child_behind() -> None:
    manager = await running(stdio_entry())
    await manager.stop()
    assert not manager.up
    with pytest.raises(McpServerDown):
        await manager.call("tools__secret_word", {})


# What a manager knows about itself
#
# The three fields the status surface reports, at each of the four
# moments that decide them: before the first attempt, after a
# connection, after a failure, and after a call failed on a connection
# that was working.


async def test_a_manager_that_has_not_connected_yet_is_down_with_no_reason() -> None:
    before = time.time()
    manager = McpServerManager("tools", stdio_entry())

    assert manager.state == DOWN
    assert manager.reason is None
    assert manager.since >= before


async def test_a_connected_server_records_when_it_connected() -> None:
    before = time.time()
    manager = await running(stdio_entry())
    try:
        assert manager.state == CONNECTED
        assert manager.reason is None
        assert before <= manager.since <= time.time()
    finally:
        await manager.stop()


async def test_a_dead_server_records_a_reason_token_and_never_a_message() -> None:
    # The token is the whole diagnosis the surface carries, and it is
    # this application's word rather than the far side's: an exception's
    # message quotes whatever the other end wrote.
    manager = await running(stdio_entry(command="/nonexistent/mcp-server", args=[]))
    try:
        assert manager.state == DOWN
        assert manager.reason is not None
        assert REASON_TOKEN.match(manager.reason), manager.reason
        assert "nonexistent" not in manager.reason
    finally:
        await manager.stop()


async def test_a_connection_dropped_after_a_failed_call_carries_a_fixed_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one way down that has no exception left to name by the time
    the state is recorded: the call raised, the manager unwound the
    connection so the next session revives it, and nothing about that is
    the far side's to describe."""
    manager = await running(stdio_entry())
    try:

        async def refuse(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("a message from nowhere near this token")

        monkeypatch.setattr(manager._session, "call_tool", refuse)
        with pytest.raises(RuntimeError):
            await manager.call("tools__secret_word", {})

        assert manager.state == DOWN
        assert manager.reason == DROPPED_AFTER_FAILED_CALL
    finally:
        await manager.stop()


async def test_a_server_stopped_on_purpose_is_down_with_nothing_wrong() -> None:
    # Shutting a server down is not a failure, so there is no reason to
    # report for it.
    manager = await running(stdio_entry())
    await manager.stop()

    assert manager.state == DOWN
    assert manager.reason is None


async def test_a_new_reason_for_staying_down_is_a_new_instant() -> None:
    """The state alone would not have moved it, and it has to move: a
    server that goes on being down for a different reason has failed
    again, and an instant that stayed put would date the new reason to
    the old failure."""
    manager = McpServerManager("tools", stdio_entry(command="/nonexistent/one", args=[]))
    await manager.start()
    first_reason, first_since = manager.reason, manager.since
    assert manager.state == DOWN

    # A second failure of another kind, still without ever connecting:
    # a URL on a port that was free a moment ago and nothing of ours
    # took.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    manager._config = McpServerConfig.model_validate(
        {"transport": "streamable_http", "url": f"http://127.0.0.1:{port}/mcp"}
    )
    await manager.start()
    try:
        assert manager.state == DOWN
        assert manager.reason != first_reason
        assert manager.since > first_since
    finally:
        await manager.stop()


async def test_the_instant_moves_when_the_state_does() -> None:
    manager = McpServerManager("tools", stdio_entry())
    manager._config = stdio_entry(command="/nonexistent/mcp-server", args=[])
    await manager.start()
    went_down = manager.since
    assert manager.state == DOWN

    manager._config = stdio_entry()
    manager.ensure_reconnecting()
    try:
        async with asyncio.timeout(20):
            while not manager.up:
                await asyncio.sleep(0.05)
        assert manager.state == CONNECTED
        assert manager.since > went_down
    finally:
        await manager.stop()


def config_with(
    servers: dict[str, object],
    agent_mcp: list[str] | None,
    local_only: bool = False,
) -> Config:
    agent: dict[str, object] = {"prompt": "A"}
    if agent_mcp is not None:
        agent["mcp"] = agent_mcp
    return Config(
        server={"local_only": local_only},
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


async def test_local_only_refuses_a_referenced_server_without_a_declaration() -> None:
    config = config_with({"tools": entry_data()}, ["tools"], local_only=True)
    with pytest.raises(McpConfigError) as excinfo:
        McpServers.build(config)
    message = str(excinfo.value)
    assert "mcp_servers.tools" in message
    assert '"egress: false"' in message


async def test_local_only_builds_a_server_the_operator_declared_local() -> None:
    config = config_with({"tools": entry_data(egress=False)}, ["tools"], local_only=True)
    servers = McpServers.build(config)
    assert "tools" in servers


async def test_local_only_refuses_a_server_declared_egress() -> None:
    config = config_with({"tools": entry_data(egress=True)}, ["tools"], local_only=True)
    with pytest.raises(McpConfigError, match="off this network"):
        McpServers.build(config)


async def test_local_only_leaves_unreferenced_entries_alone() -> None:
    config = config_with(
        {"tools": entry_data(egress=False), "unused": entry_data()},
        ["tools"],
        local_only=True,
    )
    servers = McpServers.build(config)
    assert len(servers) == 1


async def test_the_registry_starts_lists_and_stops() -> None:
    config = config_with({"tools": entry_data()}, ["tools"])
    servers = McpServers.build(config)
    await servers.start_all()
    try:
        offered = {tool.name for tool in servers.tools_for(["tools"])}
        assert "tools__secret_word" in offered
        # An entry nobody manages contributes nothing rather than raising.
        assert servers.tools_for(["ghost"]) == []
        assert await servers.call("tools__secret_word", {}, "assistant") == (
            "rhubarb",
            False,
        )
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
    # Resolved per connection rather than kept on the manager, so this
    # asks the resolver the connection asks. What it answers is
    # unchanged: the reference became the value.
    assert manager._resolve("env") == {"API_TOKEN": "sk-test"}
    # And the configuration itself never held the secret.
    assert config.mcp_servers["tools"].env == {"API_TOKEN": "$SAMTAL_TEST_MCP_TOKEN"}


async def test_a_server_name_the_apis_refuse_is_sanitized_and_still_callable() -> None:
    # An MCP server may publish anything its author liked. Both LLM APIs
    # restrict tool names to [A-Za-z0-9_-], and a name that slips through
    # does not fail politely: it fails the whole next request, so the
    # assistant loses its voice over a tool nobody asked it to use.
    manager = await running(stdio_entry())
    try:
        offered = {tool.name for tool in manager.tools()}
        assert "tools__weather_today_v2" in offered
        assert all(names.TOOL_NAME_PATTERN.match(tool.name) for tool in manager.tools())
        # And the call goes back out under the name the server listed.
        assert await manager.call("tools__weather_today_v2", {}) == ("dotted answer", False)
    finally:
        await manager.stop()


async def test_a_name_too_long_once_prefixed_is_dropped() -> None:
    # 60 characters is legal on its own and too long under "tools__",
    # which is the case an entry-name-only guard misses.
    manager = await running(stdio_entry())
    try:
        assert all(
            len(tool.name) <= names.MAX_TOOL_NAME_LENGTH for tool in manager.tools()
        )
        assert not [tool for tool in manager.tools() if "bbbb" in tool.name]
    finally:
        await manager.stop()


async def test_a_tool_the_server_never_published_is_refused() -> None:
    manager = await running(stdio_entry())
    try:
        with pytest.raises(KeyError):
            await manager.call("tools__nonexistent", {})
    finally:
        await manager.stop()


# The status view
#
# What a gated read of the running server answers with. Built from the
# slice the registry was constructed with and its managers, and from
# nothing else, so it cannot disagree with what is running.


def config_granting(servers: dict[str, object], grants: dict[str, list[str]]) -> Config:
    """A configuration whose agents reach the entries named, which is
    what the grants half of the status view is read from."""
    return Config(
        server={},
        providers={
            "llm": {"mock": {"type": "mock"}},
            "asr": {"mock": {"type": "mock"}},
            "tts": {"mock": {"type": "mock"}},
            "vad": {"mock": {"type": "mock"}},
        },
        mcp_servers=servers,
        agent_defaults=dict.fromkeys(("llm", "asr", "tts", "vad"), "mock"),
        agents={name: {"prompt": "A", "mcp": entries} for name, entries in grants.items()},
        default_agent=next(iter(grants)),
    )


async def test_a_connected_server_reports_its_published_tool_names() -> None:
    config = config_with({"tools": entry_data()}, ["tools"])
    servers = McpServers.build(config)
    await servers.start_all()
    try:
        entry = servers.status()["tools"]

        assert entry["state"] == CONNECTED
        assert entry["reason"] is None
        assert "tools__secret_word" in entry["tools"]
        # Names the model was given, and nothing else a server chose:
        # what it called the tool before the publishing rule and what it
        # said about it are both bytes it wrote.
        assert "weather.today/v2" not in entry["tools"]
        assert all(names.TOOL_NAME_PATTERN.match(tool) for tool in entry["tools"])
    finally:
        await servers.stop_all()


async def test_a_dead_server_is_down_with_its_reason_and_no_tools() -> None:
    dead = entry_data(command="/nonexistent/mcp-server", args=[])
    config = config_with({"tools": dead}, ["tools"])
    servers = McpServers.build(config)
    await servers.start_all()
    try:
        entry = servers.status()["tools"]

        assert entry["state"] == DOWN
        assert entry["reason"] is not None
        assert REASON_TOKEN.match(entry["reason"]), entry["reason"]
        assert entry["tools"] == []
    finally:
        await servers.stop_all()


async def test_an_entry_no_agent_references_is_unused() -> None:
    # No manager exists for it, so it has neither state nor tools of its
    # own; what it has is a name in the configuration and nobody using
    # it, which is a likely answer to "why does the agent not have that
    # tool" and is invisible everywhere else.
    config = config_with({"tools": entry_data(), "shelved": entry_data()}, ["tools"])
    servers = McpServers.build(config)

    entry = servers.status()["shelved"]

    assert entry["state"] == UNUSED
    assert entry["reason"] is None
    assert entry["tools"] == []
    assert entry["grants"] == {}


async def test_every_configured_entry_is_reported_once_by_name() -> None:
    config = config_with({"tools": entry_data(), "shelved": entry_data()}, ["tools"])
    servers = McpServers.build(config)

    assert set(servers.status()) == {"tools", "shelved"}


async def test_the_grants_name_every_agent_that_may_reach_the_server() -> None:
    config = config_granting(
        {"tools": entry_data(), "other": entry_data()},
        {"kids": ["tools"], "house": ["tools", "other"]},
    )
    servers = McpServers.build(config)

    status = servers.status()

    # A mapping rather than a list, and the value says how much of the
    # server the agent gets: None is all of it.
    assert status["tools"]["grants"] == {"house": None, "kids": None}
    assert status["other"]["grants"] == {"house": None}


async def test_the_instants_are_iso_8601_in_utc() -> None:
    config = config_with({"tools": entry_data(), "shelved": entry_data()}, ["tools"])
    servers = McpServers.build(config)

    for entry in servers.status().values():
        when = datetime.fromisoformat(entry["since"])
        assert when.tzinfo is not None
        assert when.utcoffset() == timedelta(0)


async def test_the_status_view_reads_the_slice_it_was_built_with() -> None:
    """Not the database and not the live configuration: an entry written
    since this object was built is not part of the world it manages, and
    a view that went and looked would say it was."""
    config = config_with({"tools": entry_data()}, ["tools"])
    servers = McpServers.build(config)

    config.mcp_servers["written-since"] = McpServerConfig.model_validate(entry_data())

    assert set(servers.status()) == {"tools"}


async def test_the_registry_routes_by_the_qualified_name() -> None:
    config = config_with({"tools": entry_data()}, ["tools"])
    servers = McpServers.build(config)
    await servers.start_all()
    try:
        assert await servers.call("tools__secret_word", {}, "assistant") == (
            "rhubarb",
            False,
        )
        with pytest.raises(McpServerDown):
            await servers.call("ghost__secret_word", {}, "assistant")
        with pytest.raises(McpServerDown):
            await servers.call("unqualified", {}, "assistant")
    finally:
        await servers.stop_all()


# Per-tool grants: what an agent is offered, and what it may call


async def test_a_whole_server_grant_offers_every_published_tool() -> None:
    config = config_with({"tools": entry_data()}, ["tools"])
    servers = McpServers.build(config)
    await servers.start_all()
    try:
        assert [tool.name for tool in servers.tools_for_agent("assistant")] == [
            tool.name for tool in servers.tools_for(["tools"])
        ]
    finally:
        await servers.stop_all()


async def test_an_allow_list_offers_only_the_tools_it_names() -> None:
    config = config_with(
        {"tools": entry_data()}, [{"server": "tools", "tools": ["secret_word"]}]
    )
    servers = McpServers.build(config)
    await servers.start_all()
    try:
        offered = [tool.name for tool in servers.tools_for_agent("assistant")]

        assert offered == ["tools__secret_word"]
        # The server published more than that, so the list is narrowed
        # rather than merely short.
        assert len(servers.tools_for(["tools"])) > 1
    finally:
        await servers.stop_all()


async def test_a_grant_names_the_published_name_after_sanitizing() -> None:
    """The stdio server lists `weather.today/v2`, which publishes as
    `tools__weather_today_v2`. The grant is written the way the operator
    reads it off `config status`, and the raw listed name grants
    nothing: it is not a name anything on this side ever answers to."""
    config = config_with(
        {"tools": entry_data()},
        [{"server": "tools", "tools": ["weather_today_v2"]}],
    )
    servers = McpServers.build(config)
    await servers.start_all()
    try:
        assert [tool.name for tool in servers.tools_for_agent("assistant")] == [
            "tools__weather_today_v2"
        ]
    finally:
        await servers.stop_all()

    config = config_with(
        {"tools": entry_data()},
        [{"server": "tools", "tools": ["weather.today/v2"]}],
    )
    servers = McpServers.build(config)
    await servers.start_all()
    try:
        assert servers.tools_for_agent("assistant") == []
    finally:
        await servers.stop_all()


async def test_two_agents_get_the_subsets_their_own_grants_name() -> None:
    config = config_granting(
        {"tools": entry_data()},
        {
            "kids": [{"server": "tools", "tools": ["secret_word"]}],
            "house": [{"server": "tools", "tools": ["add", "secret_word"]}],
        },
    )
    servers = McpServers.build(config)
    await servers.start_all()
    try:
        assert [tool.name for tool in servers.tools_for_agent("kids")] == [
            "tools__secret_word"
        ]
        assert {tool.name for tool in servers.tools_for_agent("house")} == {
            "tools__add",
            "tools__secret_word",
        }
    finally:
        await servers.stop_all()


async def test_a_call_to_a_granted_away_tool_is_refused() -> None:
    """The snapshot already left it out, so this is the case where a
    model asked for a name it was never offered. The property that the
    agent cannot reach the tool does not rest on the model."""
    config = config_granting(
        {"tools": entry_data()},
        {
            "kids": [{"server": "tools", "tools": ["secret_word"]}],
            "house": ["tools"],
        },
    )
    servers = McpServers.build(config)
    await servers.start_all()
    try:
        assert await servers.call("tools__secret_word", {}, "kids") == ("rhubarb", False)

        with pytest.raises(McpToolNotGranted, match="tools__add"):
            await servers.call("tools__add", {"first": 2, "second": 3}, "kids")
        # The same call from an agent granted the whole server runs.
        assert await servers.call("tools__add", {"first": 2, "second": 3}, "house") == (
            "5",
            False,
        )
    finally:
        await servers.stop_all()


async def test_a_call_from_an_agent_with_no_grant_at_all_is_refused() -> None:
    # Including an agent this world does not know, which is what a
    # session holding a deleted agent is after a reload.
    config = config_granting({"tools": entry_data()}, {"house": ["tools"]})
    servers = McpServers.build(config)
    await servers.start_all()
    try:
        with pytest.raises(McpToolNotGranted):
            await servers.call("tools__secret_word", {}, "stranger")
    finally:
        await servers.stop_all()


# Allowed names that did not publish


def unpublished_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == MANAGER_LOGGER and "not published" in record.getMessage()
    ]


async def test_a_grant_naming_a_tool_the_server_never_listed_is_warned_about(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An allow list cannot be checked when it is written, since only a
    live connection knows what a server offers, so the mistake is said
    out loud at the moment there is something to compare it against."""
    config = config_with(
        {"tools": entry_data()},
        [{"server": "tools", "tools": ["secret_word", "no_such_tool"]}],
    )
    with caplog.at_level(logging.WARNING, logger=MANAGER_LOGGER):
        servers = McpServers.build(config)
        await servers.start_all()
    try:
        (warned,) = unpublished_warnings(caplog)

        assert "no_such_tool" in warned
        assert "tools" in warned
        # The name that did publish is not in a warning about the one
        # that did not.
        assert "secret_word" not in warned
    finally:
        await servers.stop_all()


async def test_a_grant_naming_a_tool_publication_dropped_is_warned_about(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The comparison is against what published, never against the raw
    listing. This server lists a tool whose name is legal until the
    entry prefix is added, so publication drops it: it is exactly as
    unreachable as one the server never listed, and a check against the
    listing would have stayed quiet."""
    dropped = "b" * 60
    config = config_with({"tools": entry_data()}, [{"server": "tools", "tools": [dropped]}])
    with caplog.at_level(logging.WARNING, logger=MANAGER_LOGGER):
        servers = McpServers.build(config)
        await servers.start_all()
    try:
        # The server did list it, so this is the dropped case rather
        # than the never-listed one.
        assert names.qualified("tools", dropped) not in [
            tool.name for tool in servers.tools_for(["tools"])
        ]
        (warned,) = unpublished_warnings(caplog)
        assert dropped in warned
        assert servers.tools_for_agent("assistant") == []
    finally:
        await servers.stop_all()


async def test_a_whole_server_grant_is_warned_about_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # It names no tool, so it can name none that failed to arrive.
    config = config_with({"tools": entry_data()}, ["tools"])
    with caplog.at_level(logging.WARNING, logger=MANAGER_LOGGER):
        servers = McpServers.build(config)
        await servers.start_all()
    try:
        assert unpublished_warnings(caplog) == []
    finally:
        await servers.stop_all()


async def test_the_grants_carry_the_allow_list_beside_the_published_tools() -> None:
    """Where milestone 1 put a null: the value is how much of the server
    that agent gets, so the mismatch between what a grant allows and
    what the server published is one read rather than two."""
    config = config_granting(
        {"tools": entry_data()},
        {
            "house": ["tools"],
            "kids": [{"server": "tools", "tools": ["secret_word", "no_such_tool"]}],
        },
    )
    servers = McpServers.build(config)
    await servers.start_all()
    try:
        entry = servers.status()["tools"]

        assert entry["grants"] == {
            "house": None,
            "kids": ["secret_word", "no_such_tool"],
        }
        # And the published list beside it, which is what the allow list
        # is read against.
        assert "tools__secret_word" in entry["tools"]
        assert "tools__no_such_tool" not in entry["tools"]
    finally:
        await servers.stop_all()


# Entry names that hold the separator, and the namespace between two


async def test_an_entry_name_holding_the_separator_is_reachable_end_to_end() -> None:
    """`home__inside` is a legal entry name, and its tools publish as
    `home__inside__<tool>`. Reading that name by splitting at the first
    separator would look for a server called `home`, so the tool was
    offered and then unreachable."""
    config = config_granting(
        {"home__inside": entry_data()},
        {"assistant": [{"server": "home__inside", "tools": ["secret_word"]}]},
    )
    servers = McpServers.build(config)
    await servers.start_all()
    try:
        offered = [tool.name for tool in servers.tools_for_agent("assistant")]
        assert offered == ["home__inside__secret_word"]

        # The one resolution, which every other question asks.
        entry = servers.owner_of("home__inside__secret_word")
        assert entry == "home__inside"
        assert servers.timeout_for(entry) == 15.0
        assert await servers.call("home__inside__secret_word", {}, "assistant") == (
            "rhubarb",
            False,
        )
        # And the gate is the one the grant names, not a server called
        # `home` that does not exist.
        with pytest.raises(McpToolNotGranted):
            await servers.call("home__inside__add", {"first": 1, "second": 2}, "assistant")
    finally:
        await servers.stop_all()


async def test_the_more_specific_entry_owns_a_name_both_servers_publish(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Two entries can publish one name: this server lists a tool called
    `inside__secret_word`, so under the entry `home` it publishes as
    `home__inside__secret_word`, which is what the entry `home__inside`
    publishes its own `secret_word` as. The name is the more specific
    entry's, and the other one's tool is dropped rather than offered
    under a name that would run somebody else's."""
    config = config_granting(
        {"home": entry_data(), "home__inside": entry_data()},
        {"assistant": ["home", "home__inside"]},
    )
    servers = McpServers.build(config)
    with caplog.at_level(logging.WARNING, logger=MANAGER_LOGGER):
        await servers.start_all()
        offered = [tool.name for tool in servers.tools_for_agent("assistant")]
    try:
        assert offered.count("home__inside__secret_word") == 1
        assert servers.owner_of("home__inside__secret_word") == "home__inside"
        # The outer entry keeps everything else it published.
        assert "home__secret_word" in offered
        assert "home__inside__secret_word" not in [
            tool.name for tool in servers.tools_for(["home"])
        ]
        # The call reaches the owner's tool, and this server answers
        # differently through each of the two, so the answer says which.
        assert await servers.call("home__inside__secret_word", {}, "assistant") == (
            "rhubarb",
            False,
        )
        # What the surface shows is what the model was offered.
        assert servers.status()["home"]["tools"] == [
            tool.name for tool in servers.tools_for(["home"])
        ]

        (warned,) = [
            record.getMessage()
            for record in caplog.records
            if record.name == MANAGER_LOGGER and "namespace" in record.getMessage()
        ]
        # The entry that owns the name and the position of the tool
        # that lost it, never the name itself: the model will not be
        # given it, and half of it is what the far side called its tool.
        assert "mcp server home:" in warned
        assert "home__inside" in warned
        assert "secret_word" not in warned
    finally:
        await servers.stop_all()


async def test_a_shadowed_name_is_reported_once_per_manager_set(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The drop is decided per read, since a reload can change it without
    # anything reconnecting, but the line about it is not a line per
    # reply.
    config = config_granting(
        {"home": entry_data(), "home__inside": entry_data()},
        {"assistant": ["home", "home__inside"]},
    )
    servers = McpServers.build(config)
    await servers.start_all()
    try:
        with caplog.at_level(logging.WARNING, logger=MANAGER_LOGGER):
            for _ in range(3):
                servers.tools_for_agent("assistant")

        assert len([r for r in caplog.records if "namespace" in r.getMessage()]) == 1
    finally:
        await servers.stop_all()


# The operator's guidance, answered by the effective grant
#
# The injection condition is the grant and nothing else, which is the
# deliverable read literally: a granted agent is told about the entry
# whether or not it is connected and whatever its allow list narrows its
# tools to. So these tests never start a server: liveness is not part of
# the question.

GUIDANCE = "Ask before unlocking the door."


async def test_every_granted_agent_gets_the_entrys_guidance() -> None:
    config = config_granting(
        {"home": entry_data(instructions=GUIDANCE)},
        {"house": ["home"], "kids": ["home"]},
    )
    servers = McpServers.build(config)

    for agent in ("house", "kids"):
        assert servers.guidance_for_agent(agent) == (Guidance("home", GUIDANCE),)


async def test_guidance_is_there_while_the_server_is_down() -> None:
    """A server that is unreachable still has an operator's guidance
    about it, and the agent was still granted it. The mismatch is the
    accepted noise the issue names, and the status surface is where it
    is answered."""
    dead = entry_data(command="/nonexistent/mcp-server", args=[], instructions=GUIDANCE)
    config = config_granting({"home": dead}, {"house": ["home"]})
    servers = McpServers.build(config)
    await servers.start_all()
    try:
        assert servers.status()["home"]["state"] == DOWN
        assert servers.guidance_for_agent("house") == (Guidance("home", GUIDANCE),)
    finally:
        await servers.stop_all()


async def test_guidance_survives_an_allow_list_that_offers_nothing() -> None:
    """The grant edge rather than the filtered tool list: an allow list
    naming nothing the server publishes leaves the agent with no tools
    of that entry and with the guidance, because it is still granted."""
    config = config_granting(
        {"home": entry_data(instructions=GUIDANCE)},
        {"house": [{"server": "home", "tools": ["no_such_tool"]}]},
    )
    servers = McpServers.build(config)
    await servers.start_all()
    try:
        assert servers.tools_for_agent("house") == []
        assert servers.guidance_for_agent("house") == (Guidance("home", GUIDANCE),)
    finally:
        await servers.stop_all()


async def test_an_agent_granted_nothing_gets_no_guidance() -> None:
    """`mcp: []` opts an agent out of the tools its siblings have, and
    out of what is said about them."""
    config = config_granting(
        {"home": entry_data(instructions=GUIDANCE)}, {"house": ["home"], "quiet": []}
    )
    servers = McpServers.build(config)

    assert servers.guidance_for_agent("quiet") == ()
    assert servers.guidance_for_agent("stranger") == ()


async def test_an_entry_with_no_guidance_contributes_no_block() -> None:
    config = config_granting(
        {"home": entry_data(instructions=GUIDANCE), "weather": entry_data()},
        {"house": ["weather", "home"]},
    )
    servers = McpServers.build(config)

    # And in grant order, which is what the operator wrote.
    assert servers.guidance_for_agent("house") == (Guidance("home", GUIDANCE),)


async def test_guidance_is_carried_verbatim_through_the_slice() -> None:
    written = "  Ask before unlocking the door.\n\n    The lights are safe.\n"
    config = config_granting({"home": entry_data(instructions=written)}, {"house": ["home"]})

    servers = McpServers.build(config)

    assert servers.guidance_for_agent("house")[0].text == written


# The lifecycle, as events
#
# The five structured events this subsystem emits (#138), driven through
# a real manager against the server this file already spawns. They are a
# compatibility surface from here on: the names, the fields and the
# closed token sets are in the README's event table, and what these
# assert is that the table is true.
#
# The three helpers are shared with the HTTP and reload suites, which
# import them, so "what one of these events carries" is read one way in
# all three.


def emitted(caplog: pytest.LogCaptureFixture, name: str) -> list[logging.LogRecord]:
    """Every record of one event, in the order it was emitted."""
    return [record for record in caplog.records if getattr(record, "event", None) == name]


def one_event(caplog: pytest.LogCaptureFixture, name: str) -> logging.LogRecord:
    matching = emitted(caplog, name)
    assert len(matching) == 1, f"expected one {name} record, got {len(matching)}"
    return matching[0]


def fields_of(record: logging.LogRecord) -> dict[str, object]:
    """The structured half of a record: exactly the attributes the JSON
    formatter writes as top-level keys, read through `logs.py`'s own
    standard-attribute set rather than through a list written here, so a
    field these tests do not know about is a failure rather than a
    silence."""
    return {
        key: value for key, value in vars(record).items() if key not in _STANDARD_ATTRIBUTES
    }


async def test_a_connected_server_says_so_with_a_count_of_its_tools(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger=MANAGER_LOGGER):
        manager = await running(stdio_entry())
        published = len(manager.tools())
        await manager.stop()

    connected = one_event(caplog, "mcp_connected")
    assert connected.name == MANAGER_LOGGER
    assert connected.levelno == logging.INFO
    # A count, never a list: the names are in the sentence, where an
    # operator reads them, and a field a collector groups by has to be a
    # number.
    fields = fields_of(connected)
    assert isinstance(fields.pop("duration_ms"), int)
    assert fields == {
        "event": "mcp_connected",
        "entry": "tools",
        "transport": "stdio",
        "tools": published,
    }
    assert published > 0


async def test_a_server_that_will_not_spawn_is_down_for_the_transport(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger=MANAGER_LOGGER):
        manager = await running(stdio_entry(command="/nonexistent/mcp-server", args=[]))
        await manager.stop()

    down = one_event(caplog, "mcp_down")
    assert down.levelno == logging.WARNING
    fields = fields_of(down)
    assert isinstance(fields.pop("duration_ms"), int)
    assert fields == {"event": "mcp_down", "entry": "tools", "reason": TRANSPORT_FAILED}
    # And a connection that never happened is not reported as one.
    assert emitted(caplog, "mcp_connected") == []


async def test_a_listing_that_will_not_arrive_is_down_for_the_discovery(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The third phase of the connect envelope, and the reason the phase
    is tracked at all: the transport came up and the handshake was
    answered, so calling this a transport failure would send an operator
    to look at a box that is running."""

    async def refuse(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("a message from nowhere near this token")

    monkeypatch.setattr(ClientSession, "list_tools", refuse)

    with caplog.at_level(logging.INFO, logger=MANAGER_LOGGER):
        manager = await running(stdio_entry())
        await manager.stop()

    assert not manager.up
    assert fields_of(one_event(caplog, "mcp_down"))["reason"] == DISCOVERY_FAILED


async def test_a_server_stopped_on_purpose_is_down_at_info_with_no_duration(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A shutdown and a reload both come through here, and an operator
    who asked for one is not being told about a problem."""
    with caplog.at_level(logging.INFO, logger=MANAGER_LOGGER):
        manager = await running(stdio_entry())
        assert manager.up
        await manager.stop()

    down = one_event(caplog, "mcp_down")
    assert down.levelno == logging.INFO
    # No duration: what the field means on every other `mcp_down` is how
    # long the connect ran before it failed, and how long a working
    # connection lasted is a different number under the same name.
    assert fields_of(down) == {"event": "mcp_down", "entry": "tools", "reason": STOPPED}


async def test_a_failed_call_drops_the_call_and_then_the_connection(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pairing is contract rather than accident. One failed call is
    two stories: the tool's, which a conversation's reader wants, and
    the connection's, which belongs in the same bucket as a connect
    failure."""
    manager = await running(stdio_entry())
    try:

        async def refuse(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("a message from nowhere near this token")

        monkeypatch.setattr(manager._session, "call_tool", refuse)
        with caplog.at_level(logging.INFO, logger=MANAGER_LOGGER):
            with pytest.raises(RuntimeError):
                await manager.call("tools__secret_word", {})

        dropped = one_event(caplog, "mcp_call_dropped")
        down = one_event(caplog, "mcp_down")
        # The call's story first and the connection's second, which is
        # the order they happened in.
        assert caplog.records.index(dropped) < caplog.records.index(down)
        assert dropped.levelno == logging.WARNING
        assert down.levelno == logging.WARNING
        # The published name, which is the one the model was given and
        # the one this server's own publishing rule made.
        assert fields_of(dropped) == {
            "event": "mcp_call_dropped",
            "entry": "tools",
            "tool": "tools__secret_word",
        }
        assert fields_of(down) == {
            "event": "mcp_down",
            "entry": "tools",
            "reason": CALL_FAILED,
        }
    finally:
        await manager.stop()


async def test_a_shadowed_tool_is_reported_by_position_and_owner(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No tool name, in the sentence or in the fields: a shadowed tool
    never reached the model-facing list, and half of its name is
    whatever the far side called it.

    The position is the far side's own, which is the only thing that
    makes it worth carrying. This server lists `inside__secret_word`
    seventh, and the sixth is dropped by the publishing rule for being
    too long, so a position counted off the published list would say
    six and send an operator to a tool that published fine."""
    config = config_granting(
        {"home": entry_data(), "home__inside": entry_data()},
        {"assistant": ["home", "home__inside"]},
    )
    servers = McpServers.build(config)
    try:
        with caplog.at_level(logging.INFO, logger=MANAGER_LOGGER):
            await servers.start_all()
            servers.tools_for_agent("assistant")

        shadowed = one_event(caplog, "mcp_tool_shadowed")
        assert shadowed.levelno == logging.WARNING
        assert fields_of(shadowed) == {
            "event": "mcp_tool_shadowed",
            "entry": "home",
            "position": SHADOWED_POSITION,
            "owner": "home__inside",
        }
        # And it really is the listing's, not the published list's.
        assert [tool.name for tool in servers._managers["home"].tools()].index(
            "home__inside__secret_word"
        ) + 1 < SHADOWED_POSITION
    finally:
        await servers.stop_all()


async def test_a_credential_shaped_shadowed_name_reaches_nothing_at_all(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The reason the field is a position. Sanitizing a published name
    only replaces the characters an LLM API refuses, so an alphanumeric
    secret pasted into a tool name survives it whole, and this line is
    the one that would otherwise carry a name nothing published."""
    servers = McpServers.build(
        config_granting(
            {
                "home": entry_data(env={SHADOWED_TOOL_ENV: f"inside__{CREDENTIAL}"}),
                "home__inside": entry_data(),
            },
            {"assistant": ["home", "home__inside"]},
        )
    )
    try:
        with caplog.at_level(logging.DEBUG):
            await servers.start_all()
            offered = [tool.name for tool in servers.tools_for_agent("assistant")]

        # The planted name really was published and really was shadowed,
        # or this test would be passing by testing nothing.
        assert f"home__inside__{CREDENTIAL}" not in offered
        assert emitted(caplog, "mcp_tool_shadowed")

        # Every record rendered the way the container renders it, so a
        # field is searched as well as a sentence, and then every record
        # holding the planted name asked what it was.
        carrying = [
            getattr(record, "event", None)
            for record in caplog.records
            if CREDENTIAL in logs.JsonFormatter().format(record)
        ]
        # One line, and it is the connect listing, which prints the
        # names an entry published because they are what the model was
        # given and what the status surface answers with. That is the
        # rule this module's docstring states, and it is exactly the
        # rule the shadow drop is an exception to: this name was taken
        # away from the model, so the line saying so may not carry it.
        assert carrying == ["mcp_connected"]
    finally:
        await servers.stop_all()
