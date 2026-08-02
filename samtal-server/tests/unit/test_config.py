import tempfile
from pathlib import Path

import pytest
import yaml

from samtal_server.config import Config, ConfigError, load_config
from samtal_server.config.models import normalize_mac

EXAMPLE_CONFIG = Path(__file__).parents[2] / "config.example.yaml"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "SAMTAL_CONFIG",
        "SAMTAL_SERVER__HOST",
        "SAMTAL_SERVER__PORT",
        "SAMTAL_DEFAULT_AGENT",
    ):
        monkeypatch.delenv(var, raising=False)


def write_config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_example_config_parses() -> None:
    config = load_config(EXAMPLE_CONFIG)
    assert config.default_agent == "assistant"
    assert config.providers.llm["claude"].type == "anthropic"
    assert config.providers.llm["claude"].api_key_env == "ANTHROPIC_API_KEY"
    # The assistant inherits its LLM and overrides its voice; the
    # storyteller overrides both.
    assert config.provider_for_agent("assistant", "llm") == ("claude", "agent_defaults.llm")
    assert config.provider_for_agent("assistant", "tts") == ("piper", "agents.assistant.tts")
    assert config.provider_for_agent("storyteller", "llm") == ("local", "agents.storyteller.llm")
    assert config.devices["aa:bb:cc:dd:ee:ff"] == ["assistant"]
    assert config.agents_for_device("11:22:33:44:55:66") == ["storyteller", "assistant"]


def test_no_config_gives_defaults() -> None:
    config = load_config()
    assert config.server.host == "0.0.0.0"
    assert config.server.port == 8003
    assert config.agents == {}
    assert config.default_agent is None


def test_ota_server_settings_have_defaults() -> None:
    config = load_config()
    assert config.server.websocket_url is None
    assert config.server.protocol_version == 1
    assert config.server.timezone_offset_minutes is None


def test_websocket_url_is_accepted_and_stripped() -> None:
    config = load_config_from_data(
        {"server": {"websocket_url": "  ws://192.168.1.10:8003/xiaozhi/v1/  "}}
    )
    assert config.server.websocket_url == "ws://192.168.1.10:8003/xiaozhi/v1/"


@pytest.mark.parametrize("url", ["http://host/xiaozhi/v1/", "192.168.1.10:8003", ""])
def test_non_websocket_url_is_rejected(url: str) -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_config_from_data({"server": {"websocket_url": url}})
    assert "must start with ws:// or wss://" in str(excinfo.value)


def test_protocol_version_outside_the_known_range_is_rejected() -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_config_from_data({"server": {"protocol_version": 4}})
    assert "server.protocol_version" in str(excinfo.value)


def test_config_path_from_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_config(tmp_path, "server:\n  port: 9000\n")
    monkeypatch.setenv("SAMTAL_CONFIG", str(path))
    assert load_config().server.port == 9000


def test_env_overrides_beat_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_config(tmp_path, "server:\n  host: 10.0.0.1\n  port: 9000\n")
    monkeypatch.setenv("SAMTAL_SERVER__HOST", "127.0.0.1")
    monkeypatch.setenv("SAMTAL_SERVER__PORT", "9100")
    config = load_config(path)
    assert config.server.host == "127.0.0.1"
    assert config.server.port == 9100


def test_partial_env_override_keeps_file_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_config(tmp_path, "server:\n  host: 10.0.0.1\n  port: 9000\n")
    monkeypatch.setenv("SAMTAL_SERVER__PORT", "9100")
    config = load_config(path)
    assert config.server.host == "10.0.0.1"
    assert config.server.port == 9100


def test_any_top_level_key_is_env_overridable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_config(
        tmp_path,
        "agents:\n  assistant: {}\n  other: {}\ndefault_agent: assistant\n",
    )
    monkeypatch.setenv("SAMTAL_DEFAULT_AGENT", "other")
    assert load_config(path).default_agent == "other"


def test_non_numeric_port_override_reports_location(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SAMTAL_SERVER__PORT", "not-a-port")
    with pytest.raises(ConfigError, match=r"server\.port"):
        load_config()


def test_missing_file_names_the_path(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="config file not found"):
        load_config(tmp_path / "absent.yaml")


def test_yaml_syntax_error_reports_line(tmp_path: Path) -> None:
    path = write_config(tmp_path, "server:\n  port: 9000\n bad-indent: 1\n")
    with pytest.raises(ConfigError, match=r"invalid YAML .* line 3"):
        load_config(path)


def test_non_mapping_top_level_is_rejected(tmp_path: Path) -> None:
    path = write_config(tmp_path, "- just\n- a\n- list\n")
    with pytest.raises(ConfigError, match="top level must be a mapping"):
        load_config(path)


def test_unknown_top_level_key_is_rejected(tmp_path: Path) -> None:
    path = write_config(tmp_path, "serverr:\n  port: 9000\n")
    with pytest.raises(ConfigError, match="serverr"):
        load_config(path)


def test_default_agent_must_be_defined() -> None:
    with pytest.raises(ConfigError, match='default_agent "ghost" is not a defined agent'):
        load_config_from_data({"default_agent": "ghost"})


def test_agents_require_a_default_agent() -> None:
    with pytest.raises(ConfigError, match="default_agent is required"):
        load_config_from_data({"agents": {"assistant": {}}})


def test_device_bound_to_unknown_agent() -> None:
    data = {
        "agents": {"assistant": {}},
        "default_agent": "assistant",
        "devices": {"aa:bb:cc:dd:ee:ff": "ghost"},
    }
    with pytest.raises(ConfigError, match=r'devices\.aa:bb:cc:dd:ee:ff: agent "ghost"'):
        load_config_from_data(data)


def test_agent_referencing_unknown_provider_lists_defined_ones() -> None:
    data = {
        "providers": {"llm": {"claude": {"type": "anthropic"}}},
        "agents": {"assistant": {"llm": "claud"}},
        "default_agent": "assistant",
    }
    with pytest.raises(
        ConfigError, match=r'assistant\.llm: unknown llm provider "claud" \(defined: claude\)'
    ):
        load_config_from_data(data)


def test_agent_defaults_fill_in_the_stages_an_agent_leaves_out() -> None:
    data = {
        "providers": {
            "llm": {"claude": {"type": "anthropic"}},
            "tts": {"alto": {"type": "mock"}, "tenor": {"type": "mock"}},
        },
        "agent_defaults": {"llm": "claude", "tts": "alto"},
        "agents": {"poet": {"tts": "tenor"}, "tutor": {}},
        "default_agent": "tutor",
    }
    config = load_config_from_data(data)
    assert config.provider_for_agent("poet", "llm") == ("claude", "agent_defaults.llm")
    assert config.provider_for_agent("poet", "tts") == ("tenor", "agents.poet.tts")
    assert config.provider_for_agent("tutor", "tts") == ("alto", "agent_defaults.tts")
    # A stage neither layer names resolves to nothing, which is the boot
    # completeness check's problem, not the schema's.
    assert config.provider_for_agent("tutor", "vad") == (None, "agent_defaults.vad")


def test_a_wrong_agent_default_is_reported_once_against_its_own_layer() -> None:
    data = {
        "providers": {"llm": {"claude": {"type": "anthropic"}}},
        "agent_defaults": {"llm": "claud"},
        "agents": {"poet": {}, "tutor": {}},
        "default_agent": "poet",
    }
    with pytest.raises(ConfigError) as excinfo:
        load_config_from_data(data)
    message = str(excinfo.value)
    assert 'agent_defaults.llm: unknown llm provider "claud" (defined: claude)' in message
    assert "agents.poet" not in message


def test_agent_defaults_reject_a_prompt() -> None:
    # A prompt is a persona's identity; inheriting one silently would make
    # two agents the same agent.
    with pytest.raises(ConfigError, match="agent_defaults.prompt"):
        load_config_from_data({"agent_defaults": {"prompt": "You are helpful."}})


def test_inline_secret_is_rejected_with_env_hint() -> None:
    data = {"providers": {"llm": {"claude": {"type": "anthropic", "api_key": "sk-123"}}}}
    with pytest.raises(ConfigError, match="api_key_env"):
        load_config_from_data(data)


@pytest.mark.parametrize("key", ["client_secret", "secret_key", "auth_token", "password"])
def test_secret_like_option_names_are_rejected(key: str) -> None:
    data = {"providers": {"llm": {"x": {"type": "openai_compatible", key: "shh"}}}}
    with pytest.raises(ConfigError, match=f"{key}_env"):
        load_config_from_data(data)


def test_env_reference_options_are_allowed() -> None:
    data = {
        "providers": {
            "llm": {"x": {"type": "openai_compatible", "client_secret_env": "MY_SECRET"}}
        }
    }
    config = load_config_from_data(data)
    assert config.providers.llm["x"].options == {"client_secret_env": "MY_SECRET"}


def test_blank_identifiers_are_rejected() -> None:
    data = {
        "providers": {"llm": {"": {"type": ""}}},
        "agents": {"": {}},
        "default_agent": "",
    }
    with pytest.raises(ConfigError) as excinfo:
        load_config_from_data(data)
    message = str(excinfo.value)
    assert "providers.llm..[key]" in message
    assert "providers.llm..type" in message
    assert "agents..[key]" in message
    assert "default_agent" in message


def test_whitespace_provider_reference_is_rejected() -> None:
    data = {
        "providers": {"llm": {"claude": {"type": "anthropic"}}},
        "agents": {"assistant": {"llm": "   "}},
        "default_agent": "assistant",
    }
    with pytest.raises(ConfigError, match=r"agents\.assistant\.llm"):
        load_config_from_data(data)


def test_provider_options_pass_through() -> None:
    config = load_config_from_data(
        {"providers": {"llm": {"local": {"type": "openai_compatible", "model": "qwen3:8b"}}}}
    )
    assert config.providers.llm["local"].options == {"model": "qwen3:8b"}


def test_device_macs_are_normalized() -> None:
    config = load_config_from_data(
        {
            "agents": {"assistant": {}},
            "default_agent": "assistant",
            "devices": {"AA-BB-CC-DD-EE-FF": "assistant"},
        }
    )
    assert config.devices == {"aa:bb:cc:dd:ee:ff": ["assistant"]}
    assert config.agents_for_device("AA:BB:CC:DD:EE:FF") == ["assistant"]
    assert config.agents_for_device("11:22:33:44:55:66") == ["assistant"]


def test_a_device_can_be_bound_to_several_agents() -> None:
    config = load_config_from_data(
        {
            "agents": {"poet": {}, "tutor": {}, "kitchen": {}},
            "default_agent": "kitchen",
            "devices": {"aa:bb:cc:dd:ee:ff": ["poet", "tutor"]},
        }
    )
    # The first entry is the agent a conversation starts on; the rest are
    # what M6's switch_agent will be allowed to reach.
    assert config.agents_for_device("aa:bb:cc:dd:ee:ff") == ["poet", "tutor"]
    assert config.agents_for_device("11:22:33:44:55:66") == ["kitchen"]


def test_a_device_resolves_to_nothing_when_no_agent_is_configured() -> None:
    # Defining agents forces a default_agent, so this is the empty
    # configuration: the case the websocket session turns away with 1008.
    assert load_config_from_data({}).agents_for_device("11:22:33:44:55:66") == []


def test_a_device_bound_to_no_agent_is_rejected() -> None:
    data = {
        "agents": {"poet": {}},
        "default_agent": "poet",
        "devices": {"aa:bb:cc:dd:ee:ff": []},
    }
    with pytest.raises(ConfigError, match="at least one agent"):
        load_config_from_data(data)


def test_an_agent_listed_twice_for_one_device_is_rejected() -> None:
    data = {
        "agents": {"poet": {}},
        "default_agent": "poet",
        "devices": {"aa:bb:cc:dd:ee:ff": ["poet", "poet"]},
    }
    with pytest.raises(ConfigError, match='agent "poet" is listed more than once'):
        load_config_from_data(data)


def test_an_unknown_agent_in_a_device_list_is_rejected() -> None:
    data = {
        "agents": {"poet": {}},
        "default_agent": "poet",
        "devices": {"aa:bb:cc:dd:ee:ff": ["poet", "ghost"]},
    }
    with pytest.raises(ConfigError, match=r'devices\.aa:bb:cc:dd:ee:ff: agent "ghost"'):
        load_config_from_data(data)


def test_invalid_mac_is_rejected() -> None:
    with pytest.raises(ConfigError, match="not a MAC address"):
        load_config_from_data({"devices": {"not-a-mac": "assistant"}})


def test_colliding_macs_are_rejected() -> None:
    data = {
        "agents": {"assistant": {}},
        "default_agent": "assistant",
        "devices": {"AA:BB:CC:DD:EE:FF": "assistant", "aa-bb-cc-dd-ee-ff": "assistant"},
    }
    with pytest.raises(ConfigError, match="more than once"):
        load_config_from_data(data)


def test_normalize_mac_rejects_garbage() -> None:
    with pytest.raises(ValueError, match="not a MAC address"):
        normalize_mac("aa:bb:cc:dd:ee")


def test_multiple_problems_reported_together() -> None:
    data = {
        "agents": {"assistant": {"llm": "ghost"}},
        "devices": {"aa:bb:cc:dd:ee:ff": "nobody"},
    }
    with pytest.raises(ConfigError) as excinfo:
        load_config_from_data(data)
    message = str(excinfo.value)
    assert "default_agent is required" in message
    assert 'agent "nobody"' in message
    assert 'unknown llm provider "ghost"' in message


def load_config_from_data(data: dict) -> Config:
    """Run a raw mapping through the real loader via a temporary YAML file."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "config.yaml"
        path.write_text(yaml.safe_dump(data), encoding="utf-8")
        return load_config(path)
