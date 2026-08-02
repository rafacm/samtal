"""The tool half of the configuration: MCP servers, the agent lists that
reference them, and the memory section."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from samtal_server.config import Config, McpServerConfig, resolve_env_references


def config_with(**overrides: object) -> Config:
    """A minimal valid configuration, plus whatever the test is about."""
    base: dict[str, object] = {
        "providers": {
            "llm": {"mock": {"type": "mock"}},
            "asr": {"mock": {"type": "mock"}},
            "tts": {"mock": {"type": "mock"}},
            "vad": {"mock": {"type": "mock"}},
        },
        "agent_defaults": dict.fromkeys(("llm", "asr", "tts", "vad"), "mock"),
        "agents": {"assistant": {"prompt": "A"}},
        "default_agent": "assistant",
    }
    return Config(**(base | overrides))


STDIO = {"transport": "stdio", "command": "mcp-proxy", "args": ["http://ha/sse"]}
HTTP = {"transport": "streamable_http", "url": "http://localhost:8000/mcp"}


def test_both_transports_parse() -> None:
    config = config_with(mcp_servers={"ha": STDIO, "weather": HTTP})
    assert config.mcp_servers["ha"].command == "mcp-proxy"
    assert config.mcp_servers["ha"].args == ["http://ha/sse"]
    assert config.mcp_servers["weather"].url == "http://localhost:8000/mcp"
    # The default timeout is what keeps spoken silence bounded.
    assert config.mcp_servers["weather"].tool_timeout_s == 15.0


@pytest.mark.parametrize(
    ("entry", "fragment"),
    [
        ({"transport": "stdio"}, '"command"'),
        ({"transport": "streamable_http"}, '"url"'),
        (STDIO | {"url": "http://x/mcp"}, "url"),
        (STDIO | {"headers": {"X-Trace": "1"}}, "headers"),
        (HTTP | {"command": "mcp-proxy"}, "command"),
        (HTTP | {"env": {"HOME": "/root"}}, "env"),
    ],
)
def test_a_field_belonging_to_the_other_transport_is_an_error(
    entry: dict, fragment: str
) -> None:
    # A silently ignored header is the difference between "my headers are
    # ignored" and "my headers are wrong", which is a debugging afternoon.
    with pytest.raises(ValidationError, match=fragment):
        config_with(mcp_servers={"x": entry})


@pytest.mark.parametrize("name", ["self", "switch_agent", "remember", "home.assistant"])
def test_a_reserved_or_unusable_entry_name_fails_the_boot(name: str) -> None:
    with pytest.raises(ValidationError, match="not a usable entry name"):
        config_with(mcp_servers={name: STDIO})


@pytest.mark.parametrize(
    "entry",
    [
        STDIO | {"env": {"API_ACCESS_TOKEN": "sk-literal"}},
        HTTP | {"headers": {"Authorization": "Bearer sk-literal"}},
    ],
)
def test_an_inline_secret_is_refused(entry: dict) -> None:
    with pytest.raises(ValidationError, match="inline secret"):
        config_with(mcp_servers={"x": entry})


def test_a_non_secret_key_may_hold_a_literal() -> None:
    entry = McpServerConfig.model_validate(STDIO | {"env": {"TZ": "Europe/Stockholm"}})
    assert entry.env == {"TZ": "Europe/Stockholm"}


def test_env_references_resolve_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SAMTAL_TEST_HA_TOKEN", "secret-value")
    resolved = resolve_env_references(
        "mcp_servers.ha.env",
        {"API_ACCESS_TOKEN": "$SAMTAL_TEST_HA_TOKEN", "TZ": "Europe/Stockholm"},
    )
    assert resolved == {"API_ACCESS_TOKEN": "secret-value", "TZ": "Europe/Stockholm"}


def test_an_unset_env_reference_names_where_it_was_written(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SAMTAL_TEST_MISSING", raising=False)
    with pytest.raises(ValueError, match=r"mcp_servers\.ha\.env\.TOKEN.*SAMTAL_TEST_MISSING"):
        resolve_env_references("mcp_servers.ha.env", {"TOKEN": "$SAMTAL_TEST_MISSING"})


def test_agents_inherit_the_default_mcp_list() -> None:
    config = config_with(
        mcp_servers={"ha": STDIO, "weather": HTTP},
        agent_defaults=dict.fromkeys(("llm", "asr", "tts", "vad"), "mock")
        | {"mcp": ["weather"]},
        agents={"assistant": {"prompt": "A"}, "home": {"prompt": "H", "mcp": ["ha", "weather"]}},
    )
    assert config.mcp_for_agent("assistant") == ["weather"]
    # A list replaces rather than extends, like the stage fields.
    assert config.mcp_for_agent("home") == ["ha", "weather"]
    assert config.referenced_mcp_servers() == {"ha", "weather"}


def test_an_empty_list_opts_an_agent_out() -> None:
    config = config_with(
        mcp_servers={"ha": STDIO},
        agent_defaults=dict.fromkeys(("llm", "asr", "tts", "vad"), "mock") | {"mcp": ["ha"]},
        agents={"assistant": {"prompt": "A"}, "quiet": {"prompt": "Q", "mcp": []}},
    )
    assert config.mcp_for_agent("assistant") == ["ha"]
    assert config.mcp_for_agent("quiet") == []


def test_an_unreferenced_server_is_not_connected() -> None:
    config = config_with(mcp_servers={"ha": STDIO})
    assert config.referenced_mcp_servers() == set()


@pytest.mark.parametrize(
    ("layer", "location"),
    [
        ("agent_defaults", "agent_defaults.mcp"),
        ("agents", "agents.assistant.mcp"),
    ],
)
def test_an_unknown_server_reference_names_the_layer_that_holds_it(
    layer: str, location: str
) -> None:
    stages = dict.fromkeys(("llm", "asr", "tts", "vad"), "mock")
    overrides: dict[str, object] = (
        {"agent_defaults": stages | {"mcp": ["nope"]}}
        if layer == "agent_defaults"
        else {"agents": {"assistant": {"prompt": "A", "mcp": ["nope"]}}}
    )
    with pytest.raises(ValidationError, match=location):
        config_with(**overrides)


def test_memory_is_optional_and_takes_a_directory(tmp_path: Path) -> None:
    assert config_with().memory is None
    config = config_with(memory={"dir": str(tmp_path / "memory")})
    assert config.memory is not None
    assert config.memory.dir == tmp_path / "memory"


def test_a_memory_section_without_a_directory_is_an_error() -> None:
    with pytest.raises(ValidationError, match="dir"):
        config_with(memory={})
