"""The MCP server manager, against a real stdio server.

`tests/support/mcp_stdio_server.py` is spawned as a subprocess, so the
transport under test is the one that ships. No network, no keys, and
deterministic.
"""

import asyncio
import logging
import re
import time
import traceback
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from mcp import ClientSession

from tests.support.events import events as emitted
from tests.support.events import fields_of
from tests.support.events import only as one_event
from tests.support.mcp_stdio_server import SHADOWED_TOOL_ENV
from tests.support.tools_mcp import (
    MANAGER_LOGGER,
    SHADOWED_POSITION,
    command_arrives,
    config_granting,
    entry_data,
    running,
    stdio_entry,
)
from vinga_server import logs
from vinga_server.config import Config, McpServerConfig
from vinga_server.events import Emission, attach_server_tap, detach_server_tap
from vinga_server.runtime.prompt import Guidance
from vinga_server.tools import names
from vinga_server.tools.mcp import (
    CALL_FAILED,
    CONNECTED,
    DISCOVERY_FAILED,
    DOWN,
    DROPPED_AFTER_FAILED_CALL,
    SDK_LOGGERS,
    STOPPED,
    TRANSPORT_FAILED,
    UNUSED,
    McpCallFailed,
    McpConfigError,
    McpServerDown,
    McpServerManager,
    McpServers,
    McpToolNotGranted,
    transport,
)

# A secret shaped like something an LLM API would accept as a tool name,
# for the sentinel below: nothing but letters and digits, so the
# publishing rule's sanitizing leaves it exactly as it is.
CREDENTIAL = "AKIAIOSFODNN7EXAMPLE"

# What a reason may look like: type names, and a group's several joined
# with commas. Anything a far side wrote has spaces, punctuation or
# quotes in it and does not match.
REASON_TOKEN = re.compile(r"^[A-Za-z][A-Za-z0-9]*(, [A-Za-z][A-Za-z0-9]*)*$")


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


async def test_a_server_that_came_back_is_reconnected_in_the_background(
    tmp_path: Path,
) -> None:
    """What moves between the failed start and the reconnect is the
    world rather than the entry: nothing is at the path the
    configuration names, and then the box that was rebooting is back.
    Which is the whole of what a background reconnect is for, and the
    only thing that ever changes a running manager's configuration is a
    reload, which does it by replacing the manager."""
    command = tmp_path / "mcp-server"
    manager = McpServerManager("tools", stdio_entry(command=str(command)))
    await manager.start()
    assert not manager.up

    command_arrives(command)
    manager.ensure_reconnecting()
    try:
        async with asyncio.timeout(20):
            while not manager.up:
                await asyncio.sleep(0.05)
        assert await manager.call("tools__secret_word", {}) == ("rhubarb", False)
    finally:
        await manager.stop()


@pytest.fixture
def unquieted() -> Iterator[None]:
    """The SDK's loggers as a process that has quieted nothing holds
    them.

    The quieting is process-wide and every suite that starts a manager
    installs it, so a test about when it is installed has to put the
    state back first or it reads whatever ran before it rather than
    what the code does.
    """
    root = logging.getLogger("mcp")
    held = [(logging.getLogger(name), list(logging.getLogger(name).filters))
            for name in SDK_LOGGERS]
    propagating = root.propagate
    for child, _ in held:
        child.filters = []
    root.propagate = True
    try:
        yield
    finally:
        for child, filters in held:
            child.filters = list(filters)
        root.propagate = propagating


async def test_a_background_reconnect_quiets_the_sdk_before_it_connects(
    caplog: pytest.LogCaptureFixture, unquieted: None
) -> None:
    """A session opening revives a down server, and that path begins a
    connection without going through `start`.

    So the quieting belongs where the task is created rather than at
    either caller: a process whose first connect is a background
    reconnect would otherwise talk to a server with the SDK's own
    loggers still reaching every handler of ours, which is where a
    session id a server picked, and a traceback quoting the bytes it
    tripped on, would land. Asserted before the task has run as well as
    after the connect, because the ordering is the whole of the
    property.
    """
    manager = McpServerManager("tools", stdio_entry())

    with caplog.at_level(logging.DEBUG):
        manager.ensure_reconnecting()
        # Nothing has been awaited since, so the task that line created
        # has not run: this is the rule in force BEFORE the connect
        # rather than somewhere during it.
        assert not logging.getLogger("mcp").propagate
        try:
            async with asyncio.timeout(20):
                while not manager.up:
                    await asyncio.sleep(0.05)
            for name in SDK_LOGGERS:
                logging.getLogger(name).warning("the session id a server picked")
        finally:
            await manager.stop()

    assert [record for record in caplog.records if record.name.startswith("mcp.")] == []
    # And the reconnect said so on this server's own logger, so the
    # absence above is an absence from a log something was written to.
    assert [record for record in caplog.records if record.name == MANAGER_LOGGER]


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

        monkeypatch.setattr(manager.session, "call_tool", refuse)
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


async def test_a_new_reason_for_staying_down_is_a_new_instant(tmp_path: Path) -> None:
    """The state alone would not have moved it, and it has to move: a
    server that goes on being down for a different reason has failed
    again, and an instant that stayed put would date the new reason to
    the old failure."""
    command = tmp_path / "mcp-server"
    manager = McpServerManager("tools", stdio_entry(command=str(command)))
    await manager.start()
    first_reason, first_since = manager.reason, manager.since
    assert manager.state == DOWN

    # A second failure of another kind, still without ever connecting
    # and still under the entry it was built with: something is at the
    # path now, and it is not something this process may execute.
    command.write_text("#!/bin/sh\nexec true\n")
    command.chmod(0o644)
    await manager.start()
    try:
        assert manager.state == DOWN
        assert manager.reason != first_reason
        assert manager.since > first_since
    finally:
        await manager.stop()


async def test_the_instant_moves_when_the_state_does(tmp_path: Path) -> None:
    command = tmp_path / "mcp-server"
    manager = McpServerManager("tools", stdio_entry(command=str(command)))
    await manager.start()
    went_down = manager.since
    assert manager.state == DOWN

    command_arrives(command)
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
        assert await servers.call("tools__secret_word", {}, "assistant", "tools") == (
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
    monkeypatch.delenv("VINGA_TEST_MCP_TOKEN", raising=False)
    config = config_with(
        {"tools": entry_data(env={"API_TOKEN": "$VINGA_TEST_MCP_TOKEN"})}, ["tools"]
    )
    with pytest.raises(McpConfigError, match="VINGA_TEST_MCP_TOKEN"):
        McpServers.build(config)


async def test_a_resolved_secret_reaches_the_spawned_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VINGA_TEST_MCP_TOKEN", "sk-test")
    config = config_with(
        {"tools": entry_data(env={"API_TOKEN": "$VINGA_TEST_MCP_TOKEN"})}, ["tools"]
    )
    # Built rather than kept, because building is what resolves at
    # construction and so what a reference nothing satisfies fails.
    McpServers.build(config)
    # Resolved per connection rather than kept on the manager, so this
    # asks the resolver the connection asks, with the entry the manager
    # was built from. What it answers is unchanged: the reference became
    # the value.
    # White-box: resolution happens per connection, inside the
    # transport, and the resolved value is deliberately kept on no
    # object a reader can reach, which is the property being asserted
    # around it. Its observable form is a child process's environment,
    # and `test_secret_resolution.py` spawns one to read it; here the
    # question is the reference rather than the store, and the resolver
    # is asked with the entry the manager was built from.
    assert transport._resolve("tools", config.mcp_servers["tools"], None, "env") == {
        "API_TOKEN": "sk-test"
    }
    # And the configuration itself never held the secret.
    assert config.mcp_servers["tools"].env == {"API_TOKEN": "$VINGA_TEST_MCP_TOKEN"}


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
        assert await servers.call("tools__secret_word", {}, "assistant", "tools") == (
            "rhubarb",
            False,
        )
        with pytest.raises(McpServerDown):
            await servers.call("ghost__secret_word", {}, "assistant", "ghost")
        with pytest.raises(McpServerDown):
            await servers.call("unqualified", {}, "assistant", "tools")
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
    reads it off `config mcp-server status`, and the raw listed name grants
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
        assert await servers.call("tools__secret_word", {}, "kids", "tools") == ("rhubarb", False)

        with pytest.raises(McpToolNotGranted, match="tools__add"):
            await servers.call("tools__add", {"first": 2, "second": 3}, "kids", "tools")
        # The same call from an agent granted the whole server runs.
        assert await servers.call("tools__add", {"first": 2, "second": 3}, "house", "tools") == (
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
            await servers.call("tools__secret_word", {}, "stranger", "tools")
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
        assert await servers.call("home__inside__secret_word", {}, "assistant", "home__inside") == (
            "rhubarb",
            False,
        )
        # And the gate is the one the grant names, not a server called
        # `home` that does not exist.
        with pytest.raises(McpToolNotGranted):
            await servers.call(
                "home__inside__add", {"first": 1, "second": 2}, "assistant", "home__inside"
            )
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
        assert await servers.call("home__inside__secret_word", {}, "assistant", "home__inside") == (
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
# closed token sets are declared in `events_schema.py` and printed in
# the generated event reference, and what these assert is that the
# declaration is true of what the subsystem actually emits.
#
# The three helpers are shared with the HTTP and reload suites, so they
# live in `tests/support/events.py` and "what one of these events
# carries" is read one way in all three.


class Consumer:
    """A server-scope tap that keeps what it was handed.

    The same shape the server pin suite uses, and here for the same
    reason: a record is not the only thing an event reaches. A tap is
    handed the payload as a copy but the `%` arguments as the objects
    themselves, so a claim that some value reaches nobody has to be
    asserted where a consumer stands as well as at the log."""

    def __init__(self) -> None:
        self.seen: list[Emission] = []

    def emit(self, emission: Emission) -> None:
        self.seen.append(emission)

    def rendered(self) -> str:
        """Everything a consumer could read off what it was handed."""
        parts: list[str] = []
        for emission in self.seen:
            parts.append(str(emission.payload))
            for argument in emission.args:
                parts += [str(argument), repr(argument)]
        return "\n".join(parts)


@pytest.fixture
def tap() -> Iterator[Consumer]:
    """A consumer attached to the server hub for one test, which is what
    a #66/#67 exporter will be."""
    consumer = Consumer()
    attach_server_tap(consumer)
    try:
        yield consumer
    finally:
        detach_server_tap(consumer)


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
    # A count, and no names in the line at all: half of a published name
    # is what the far side called its tool, and which names an entry
    # published is answered by `vinga-server config mcp-server status`
    # rather than by the retained logs.
    fields = fields_of(connected)
    assert isinstance(fields.pop("duration_ms"), int)
    assert fields == {
        "event": "mcp_connected",
        "entry": "tools",
        "transport": "stdio",
        "tools": published,
    }
    assert published > 0
    assert "secret_word" not in connected.getMessage()


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

        monkeypatch.setattr(manager.session, "call_tool", refuse)
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
        # The position in this server's listing, never the name: half
        # of a published name is what the far side called its tool, and
        # `secret_word` is its first. The class name beside it is the
        # only record of what actually failed, since the exception
        # raised to the session carries nothing.
        assert fields_of(dropped) == {
            "event": "mcp_call_dropped",
            "entry": "tools",
            "position": 1,
            "error": "RuntimeError",
        }
        assert "secret_word" not in dropped.getMessage()
        assert fields_of(down) == {
            "event": "mcp_down",
            "entry": "tools",
            "reason": CALL_FAILED,
        }
    finally:
        await manager.stop()


async def test_a_failed_call_raises_this_servers_own_words_and_nothing_else(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What this raise carries goes further than a log line.

    The pipeline renders the exception into the tool result the model is
    given, so every character of it lands in the conversation and in the
    record the conversation store keeps of it. An SDK exception raised near a
    response body quotes that body, and a server holding a credential of
    this deployment's can put it in the error it answers with, so the
    call path answers with a fixed sentence of its own and the chain
    behind it is cut: no cause, no context, and nothing of the failure
    in the traceback a handler above might render.
    """
    manager = await running(stdio_entry())
    try:

        async def refuse(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError(f"the far side said {CREDENTIAL} while answering")

        monkeypatch.setattr(manager.session, "call_tool", refuse)
        with caplog.at_level(logging.INFO, logger=MANAGER_LOGGER):
            with pytest.raises(McpCallFailed) as caught:
                await manager.call("tools__secret_word", {})

        # A `RuntimeError` still, because the one production caller
        # catches broadly and both this and `McpServerDown` mean the
        # same thing to it: the tool did not run.
        assert isinstance(caught.value, RuntimeError)
        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None
        # The whole of what an unlucky handler above could render.
        rendered = "".join(
            traceback.format_exception(
                type(caught.value), caught.value, caught.value.__traceback__
            )
        )
        assert CREDENTIAL not in rendered
        assert CREDENTIAL not in str(caught.value)
        # And the sentence the model is actually handed, which is the
        # pipeline's wording around this message.
        assert CREDENTIAL not in f'the tool "tools__secret_word" failed: {caught.value}'
        # What failed is still recorded, by class, where a diagnosis
        # belongs.
        assert fields_of(one_event(caplog, "mcp_call_dropped"))["error"] == "RuntimeError"
        assert CREDENTIAL not in caplog.text
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
        assert [tool.name for tool in servers.manager_of("home").tools()].index(
            "home__inside__secret_word"
        ) + 1 < SHADOWED_POSITION
    finally:
        await servers.stop_all()


async def test_a_credential_shaped_tool_name_reaches_nothing_at_all(
    caplog: pytest.LogCaptureFixture, tap: Consumer
) -> None:
    """The reason every line about a tool is written in positions.

    Sanitizing a published name only replaces the characters an LLM API
    refuses, so an alphanumeric secret pasted into a tool name goes
    through it untouched, and a server that was handed one of this
    deployment's own credentials can hand it back by listing a tool
    under it. It is planted where it publishes *and* is then shadowed,
    so the connect line, the shadow drop and the publication's own
    warnings are all driven at once, and hunted in every place a value
    can reach: a record's arguments, its fields, both shipped formats,
    and a consumer attached to the hub, which is handed the arguments
    themselves rather than a copy.
    """
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
        assert emitted(caplog, "mcp_connected")

        # Every record in both shipped formats, plus the arguments
        # behind them, which is where a value that is rendered into a
        # sentence still sits as an object.
        formatter = logs.JsonFormatter()
        written = "\n".join(
            f"{record.getMessage()}\n{record.args!r}\n{formatter.format(record)}"
            for record in caplog.records
        )
        assert CREDENTIAL not in written
        assert CREDENTIAL not in tap.rendered()
    finally:
        await servers.stop_all()
