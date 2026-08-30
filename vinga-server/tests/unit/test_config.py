from pathlib import Path

import pytest

from tests.support.configs import load_config_from_data
from vinga_server.config import Config, ConfigError, load_file_config
from vinga_server.config.entities import PROGRAM, SERVER_PROGRAM
from vinga_server.config.models import DOMAIN_KEYS, NOT_A_MAC, normalize_mac
from vinga_server.conversations.store import RETENTION_DAYS_DEFAULT

# Not a real credential, and shaped so a substring check for it cannot
# match by accident. Written into the input a parser chokes on, since a
# parser exception keeps what it was reading, and into the places a
# configuration file holds a value this loader validates for its shape.
PARSER_SENTINEL = "sk-test-6e0d4a11-never-a-real-credential"

EXAMPLE_CONFIG = Path(__file__).parents[2] / "config.example.yaml"
DEPLOY_EXAMPLE_CONFIG = Path(__file__).parents[2] / "config.deploy.example.yaml"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "VINGA_CONFIG",
        "VINGA_SERVER__HOST",
        "VINGA_SERVER__PORT",
        "VINGA_DB_HOST",
        "VINGA_DB_PORT",
        "VINGA_DB_NAME",
        "VINGA_DB_USER",
        "VINGA_DEFAULT_AGENT",
    ):
        monkeypatch.delenv(var, raising=False)


def write_config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_example_config_parses() -> None:
    """The example file is the server half now: what it documents is how
    the process runs and where it keeps things. The domain half it used
    to carry lives in the database, and the example fragments under
    examples/ are what tests/unit/test_config_examples.py runs."""
    config = load_file_config(EXAMPLE_CONFIG)
    assert config.server.host == "0.0.0.0"
    assert config.server.port == 8003
    # The short onboarding path ships on, with nothing pinned: its key
    # is derived from the device-auth secret.
    assert config.server.onboarding.enabled is True
    assert config.server.onboarding.key is None


def test_deploy_example_config_parses() -> None:
    config = load_file_config(DEPLOY_EXAMPLE_CONFIG)
    # The deployment profile sets what a plain LAN run leaves defaulted.
    assert config.server.websocket_url == "wss://voice.example.com/xiaozhi/v1/"
    # Behind a TLS proxy nothing about a request says what a person
    # should type, so the profile names the origin explicitly.
    assert config.server.public_url == "https://voice.example.com"
    assert config.server.auth.enabled is True


def test_no_config_gives_defaults() -> None:
    config = load_file_config()
    assert config.server.host == "0.0.0.0"
    assert config.server.port == 8003
    # And the other half of the same default: an empty database.
    empty = load_config_from_data({})
    assert empty.agents == {}
    assert empty.default_agent is None


def test_ota_server_settings_have_defaults() -> None:
    config = load_file_config()
    assert config.server.websocket_url is None
    assert config.server.protocol_version == 1
    assert config.server.timezone_offset_minutes is None


def test_limits_have_defaults() -> None:
    limits = load_file_config().server.limits
    assert limits.max_sessions == 8
    assert limits.max_session_s == 3600
    assert limits.idle_timeout_s == 120


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("max_sessions", 0),
        ("max_sessions", -1),
        ("max_session_s", 0),
        ("max_session_s", -5),
        ("idle_timeout_s", 0),
        ("idle_timeout_s", -5),
    ],
)
def test_limits_below_one_session_or_second_are_rejected(key: str, value: int) -> None:
    with pytest.raises(ConfigError):
        load_config_from_data({"server": {"limits": {key: value}}})


def test_capture_is_off_until_it_is_enabled() -> None:
    # The example config ships the section so the field workflow is one
    # word, which only works if the section on its own records nothing.
    capture = load_config_from_data(
        {"server": {"capture": {"dir": "/tmp/captures"}}}
    ).server.capture
    assert capture is not None
    assert capture.enabled is False


def test_the_example_config_ships_capture_switched_off() -> None:
    capture = load_file_config(EXAMPLE_CONFIG).server.capture
    assert capture is not None, "the example lost its capture section"
    assert capture.enabled is False, "the example config would record room audio"


def test_capture_needs_somewhere_to_write_even_when_disabled() -> None:
    # Turning it on should be one word, not one word and remembering
    # where it writes.
    with pytest.raises(ConfigError):
        load_config_from_data({"server": {"capture": {"enabled": False}}})


def test_the_conversation_store_is_absent_by_default() -> None:
    # Absent, not present-and-off: an absent section is what makes a
    # server that was never asked for a store behave exactly as it did.
    assert Config().server.conversations is None


def test_the_conversation_store_is_off_until_it_is_enabled() -> None:
    # A section on its own records nothing, the same shape capture has,
    # so the switches and the window survive turning recording off.
    conversations = load_config_from_data(
        {"server": {"conversations": {}}}
    ).server.conversations
    assert conversations is not None
    assert conversations.enabled is False


def test_the_conversation_store_defaults_are_the_stated_ones() -> None:
    # Enabling the store alone gives the documented defaults: both
    # storage switches on, and the retention window the store itself
    # defaults to, which is where the number is documented.
    conversations = load_config_from_data(
        {"server": {"conversations": {"enabled": True}}}
    ).server.conversations
    assert conversations is not None
    assert (conversations.metrics, conversations.text) == (True, True)
    assert conversations.retention_days == RETENTION_DAYS_DEFAULT == 90


def test_keeping_conversations_forever_is_expressible() -> None:
    # 0 is the documented opt-out from retention, not a rejected value.
    conversations = load_config_from_data(
        {"server": {"conversations": {"enabled": True, "retention_days": 0}}}
    ).server.conversations
    assert conversations is not None
    assert conversations.retention_days == 0


def test_a_negative_retention_window_is_refused() -> None:
    with pytest.raises(ConfigError):
        load_config_from_data({"server": {"conversations": {"retention_days": -1}}})


def test_an_unknown_conversations_key_is_refused() -> None:
    # extra="forbid", like every server section: a misspelled switch
    # that silently defaulted on would be a privacy setting nobody set.
    with pytest.raises(ConfigError):
        load_config_from_data({"server": {"conversations": {"txt": False}}})


def test_the_example_config_leaves_the_conversation_store_off() -> None:
    # Commented out in the example, so a copied file records nothing
    # until an operator uncomments the block and says enabled.
    assert load_file_config(EXAMPLE_CONFIG).server.conversations is None
    assert load_file_config(DEPLOY_EXAMPLE_CONFIG).server.conversations is None


def test_ota_path_defaults_to_the_documented_one() -> None:
    assert load_file_config().server.ota_path == "/xiaozhi/ota/"


def test_a_custom_ota_path_is_accepted_and_stripped() -> None:
    config = load_config_from_data({"server": {"ota_path": "  /xiaozhi/ota/8f3a9c2b/  "}})
    assert config.server.ota_path == "/xiaozhi/ota/8f3a9c2b/"


@pytest.mark.parametrize(
    "path", ["xiaozhi/ota/", "/xiaozhi/ota", "", "https://host/xiaozhi/ota/"]
)
def test_an_ota_path_without_both_slashes_is_rejected(path: str) -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_config_from_data({"server": {"ota_path": path}})
    assert "is not a usable OTA path" in str(excinfo.value)


def test_logging_settings_have_defaults() -> None:
    config = load_file_config()
    assert config.server.log_format == "text"
    assert config.server.log_level == "INFO"


def test_log_level_is_normalized_to_upper_case() -> None:
    config = load_config_from_data({"server": {"log_level": "debug"}})
    assert config.server.log_level == "DEBUG"


@pytest.mark.parametrize("level", ["verbose", "NOTSET", ""])
def test_unknown_log_level_is_rejected(level: str) -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_config_from_data({"server": {"log_level": level}})
    assert "is not a logging level" in str(excinfo.value)


def test_unknown_log_format_is_rejected() -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_config_from_data({"server": {"log_format": "logfmt"}})
    assert "server.log_format" in str(excinfo.value)


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
    monkeypatch.setenv("VINGA_CONFIG", str(path))
    assert load_file_config().server.port == 9000


def test_the_database_connection_defaults_and_is_overridable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, packaged_database
) -> None:
    """The CLI reads these keys through the same settings machinery the
    server does, so a deployment names its database once.

    `packaged_database` is what the rest of the lane runs without: every
    other test is moved onto the database this run provisioned, and this
    is the one that asks what a deployment is shipped pointing at, which
    is the compose service's own defaults.
    """
    shipped = load_file_config().server.database
    assert (shipped.host, shipped.port, shipped.name, shipped.user) == (
        "127.0.0.1",
        5432,
        "vinga",
        "vinga",
    )

    path = write_config(
        tmp_path,
        "server:\n  database:\n    host: db.internal\n    port: 6543\n"
        "    name: vinga_prod\n    user: vinga_app\n",
    )
    written = load_file_config(path).server.database
    assert (written.host, written.port, written.name, written.user) == (
        "db.internal",
        6543,
        "vinga_prod",
        "vinga_app",
    )

    # The short names beat the file, which is what a deployment sets
    # beside its password.
    monkeypatch.setenv("VINGA_DB_HOST", "127.0.0.2")
    monkeypatch.setenv("VINGA_DB_PORT", "5433")
    monkeypatch.setenv("VINGA_DB_NAME", "vinga_other")
    monkeypatch.setenv("VINGA_DB_USER", "vinga_other_user")
    overridden = load_file_config(path).server.database
    assert (overridden.host, overridden.port, overridden.name, overridden.user) == (
        "127.0.0.2",
        5433,
        "vinga_other",
        "vinga_other_user",
    )


def test_the_generic_spelling_of_a_database_key_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`VINGA_SERVER__DATABASE__HOST` would work by accident of the
    nesting scheme, and letting it would give every connection fact two
    names. It is refused naming the short one instead, because the short
    names are the ones the compose file feeds the database image from,
    so they are the ones a `.env` holds."""
    monkeypatch.setenv("VINGA_SERVER__DATABASE__HOST", "db.internal")

    with pytest.raises(ConfigError) as caught:
        load_file_config()

    problem = str(caught.value)
    assert "VINGA_SERVER__DATABASE__HOST" in problem
    assert "VINGA_DB_HOST" in problem


def test_a_database_port_that_is_not_a_port_is_refused_without_the_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """These variables are set beside a password, so a refusal that
    echoed its input is one typo away from echoing the wrong one."""
    monkeypatch.setenv("VINGA_DB_PORT", "sk-test-9182aa-never-a-real-credential")

    with pytest.raises(ConfigError) as caught:
        load_file_config()

    assert "sk-test-9182aa" not in str(caught.value)
    assert "VINGA_DB_PORT" in str(caught.value)


def test_env_overrides_beat_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_config(tmp_path, "server:\n  host: 10.0.0.1\n  port: 9000\n")
    monkeypatch.setenv("VINGA_SERVER__HOST", "127.0.0.1")
    monkeypatch.setenv("VINGA_SERVER__PORT", "9100")
    config = load_file_config(path)
    assert config.server.host == "127.0.0.1"
    assert config.server.port == 9100


def test_partial_env_override_keeps_file_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_config(tmp_path, "server:\n  host: 10.0.0.1\n  port: 9000\n")
    monkeypatch.setenv("VINGA_SERVER__PORT", "9100")
    config = load_file_config(path)
    assert config.server.host == "10.0.0.1"
    assert config.server.port == 9100


def test_non_numeric_port_override_reports_location(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VINGA_SERVER__PORT", "not-a-port")
    with pytest.raises(ConfigError, match=r"server\.port"):
        load_file_config()


def test_missing_file_names_the_path(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="config file not found"):
        load_file_config(tmp_path / "absent.yaml")


def test_yaml_syntax_error_reports_line(tmp_path: Path) -> None:
    path = write_config(tmp_path, "server:\n  port: 9000\n bad-indent: 1\n")
    with pytest.raises(ConfigError, match=r"invalid YAML .* line 3"):
        load_file_config(path)


def test_a_yaml_parse_failure_carries_no_parser_exception(tmp_path: Path) -> None:
    """A parser exception retains the buffer it was parsing, so leaving
    it as the refusal's cause or context would attach the whole file to
    a complaint about one line of it, credentials included."""
    path = write_config(
        tmp_path, f'server:\n  log_level: "INFO\n  note: {PARSER_SENTINEL}\n'
    )

    with pytest.raises(ConfigError) as caught:
        load_file_config(path)

    assert "invalid YAML" in str(caught.value)
    assert PARSER_SENTINEL not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_a_malformed_structured_override_carries_no_parser_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A VINGA_ override of a structured key is read as JSON, and the
    decoder's exception keeps the whole rejected value in `.doc`. The
    refusal names the field and the source, and nothing behind it holds
    what was written."""
    monkeypatch.setenv("VINGA_SERVER__LIMITS", f'{{"max_sessions": 1, "n": "{PARSER_SENTINEL}"')

    with pytest.raises(ConfigError) as caught:
        load_file_config()

    assert "invalid config in" in str(caught.value)
    assert PARSER_SENTINEL not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_non_mapping_top_level_is_rejected(tmp_path: Path) -> None:
    path = write_config(tmp_path, "- just\n- a\n- list\n")
    with pytest.raises(ConfigError, match="top level must be a mapping"):
        load_file_config(path)


def test_unknown_top_level_key_is_rejected(tmp_path: Path) -> None:
    path = write_config(tmp_path, "serverr:\n  port: 9000\n")
    with pytest.raises(ConfigError, match="serverr"):
        load_file_config(path)


# What a leftover section of each moved key looks like in a file, and
# the command the refusal has to name for it.
MOVED_SECTIONS: list[tuple[str, str, str]] = [
    ("providers", "providers:\n  llm:\n    claude:\n      type: anthropic\n", "provider set"),
    ("mcp_servers", "mcp_servers:\n  home:\n    transport: stdio\n", "mcp-server set"),
    (
        "prompt_fragments",
        "prompt_fragments:\n  household:\n    text: The bins go out on Tuesday.\n",
        "prompt-fragment set",
    ),
    ("agent_defaults", "agent_defaults:\n  llm: claude\n", "agent-defaults set"),
    ("agents", "agents:\n  assistant:\n    prompt: hi\n", "agent set"),
    ("devices", 'devices:\n  "aa:bb:cc:dd:ee:ff":\n    - assistant\n', "device bind"),
    ("default_agent", "default_agent: assistant\n", "default-agent set"),
]


def test_every_domain_section_names_the_command_that_writes_it() -> None:
    """The refusals index this table by domain key, so a section added
    without one would answer with a KeyError out of the boot path rather
    than with the sentence that sends an operator to the command."""
    from vinga_server.config.loader import MOVED_KEY_COMMANDS

    assert set(MOVED_KEY_COMMANDS) == set(DOMAIN_KEYS)
    assert {key for key, _, _ in MOVED_SECTIONS} == set(DOMAIN_KEYS)


@pytest.mark.parametrize(("key", "section", "command"), MOVED_SECTIONS)
def test_a_domain_section_left_in_the_file_names_where_it_moved(
    tmp_path: Path, key: str, section: str, command: str
) -> None:
    """A section the server no longer reads must not be ignored: a
    deployment editing it would be editing nothing, and the failure it
    would eventually meet has nothing to do with the edit."""
    path = write_config(tmp_path, f"server:\n  port: 9000\n{section}")
    with pytest.raises(ConfigError) as excinfo:
        load_file_config(path)
    message = str(excinfo.value)
    assert f"{key}: moved to the database" in message
    # The long spelling, and pinned rather than derived from whatever
    # the loader happens to render: this refuses a BOOT, so its reader
    # is an operator watching a container fail to start and the
    # invocation they have is the image's. A generated document goes the
    # other way, and the rule inverting silently is exactly what a pin
    # of one literal sentence stops.
    assert f"{SERVER_PROGRAM} {command}" in message
    assert f"{PROGRAM} {command}" not in message
    assert "docs/reference/domain-config.md" in message


@pytest.mark.parametrize("key", [key for key, _, _ in MOVED_SECTIONS])
@pytest.mark.parametrize("suffix", ["", "__ASSISTANT", "__ASSISTANT__LLM"])
def test_a_moved_environment_override_names_where_it_moved(
    monkeypatch: pytest.MonkeyPatch, key: str, suffix: str
) -> None:
    """The environment source looks up known fields and ignores every
    other prefixed variable, so without this scan a stale
    VINGA_DEFAULT_AGENT would simply stop applying, silently."""
    variable = f"VINGA_{key.upper()}{suffix}"
    monkeypatch.setenv(variable, "whatever")
    with pytest.raises(ConfigError) as excinfo:
        load_file_config()
    message = str(excinfo.value)
    assert f"{variable}: {key} moved to the database" in message
    assert SERVER_PROGRAM in message


@pytest.mark.parametrize(
    "variable",
    [
        "VINGA_default_agent",
        "ViNgA_DeFaUlT_aGeNt",
        "VINGA_agents__assistant__llm",
        "vinga_AGENTS__assistant",
        "VINGA_AgEnT_dEfAuLtS__llm",
        "vinga_providers__llm__claude__type",
    ],
)
def test_a_moved_override_is_refused_whatever_its_case(
    monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    """pydantic-settings matches environment names without regard to
    case, over the whole name and not only the part after the prefix, so
    every spelling here set what it names before the switchover. A
    case-sensitive scan would leave them applying to nothing, silently,
    which is the one thing this refusal exists to prevent."""
    monkeypatch.setenv(variable, "whatever")
    with pytest.raises(ConfigError) as excinfo:
        load_file_config()
    message = str(excinfo.value)
    # Reported in the spelling it was written in: that is what has to be
    # found and unset.
    assert f"{variable}: " in message
    assert "moved to the database" in message


# A retired section, which is not a moved one
#
# `memory:` did not move to the database's domain half; it stopped
# existing, because remembered facts are kept whether anybody configures
# them or not (#314). Both doors onto it refuse, and neither repeats
# what the operator wrote under it: a directory is not a credential, but
# a value pasted into the wrong key is exactly what a rule with an
# exception in it fails to cover.

RETIRED_DIRECTORY = "/var/lib/vinga/memory"


def test_a_retired_section_left_in_the_file_says_it_retired(tmp_path: Path) -> None:
    path = write_config(
        tmp_path, f"server:\n  port: 9000\nmemory:\n  dir: {RETIRED_DIRECTORY}\n"
    )
    with pytest.raises(ConfigError) as excinfo:
        load_file_config(path)
    message = str(excinfo.value)
    assert "memory: retired" in message
    assert "live in the database" in message
    # Not the moved-section sentence: nothing writes this anywhere, so
    # naming a command would send a reader looking for one.
    assert "moved to the database" not in message
    assert RETIRED_DIRECTORY not in message


def test_a_retired_section_says_the_old_files_are_the_operators(tmp_path: Path) -> None:
    """The hard cutover, said where a deployment meets it: this release
    reads nothing that is on disk and removes nothing either."""
    path = write_config(tmp_path, "server:\n  port: 9000\nmemory:\n  dir: /tmp/x\n")
    with pytest.raises(ConfigError) as excinfo:
        load_file_config(path)
    message = str(excinfo.value)
    assert "not read, not imported and not deleted" in message


@pytest.mark.parametrize(
    "variable",
    [
        "VINGA_MEMORY",
        "VINGA_MEMORY__DIR",
        "VINGA_memory__dir",
        "ViNgA_MeMoRy__DiR",
    ],
)
def test_a_retired_environment_override_is_refused_whatever_its_case(
    monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    """Once the field is deleted pydantic-settings ignores the variable
    under any spelling, `extra="forbid"` notwithstanding, which is the
    hole this scan exists to close."""
    monkeypatch.setenv(variable, RETIRED_DIRECTORY)
    with pytest.raises(ConfigError) as excinfo:
        load_file_config()
    message = str(excinfo.value)
    # Reported in the spelling it was written in, which is what has to
    # be found and unset, and never with what it was set to.
    assert f"{variable}: memory is retired" in message
    assert RETIRED_DIRECTORY not in message


def test_nothing_of_a_retired_variable_reaches_any_surface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole refusal, chain included: the value is not on the
    message, and no exception behind it carries one either."""
    monkeypatch.setenv("VINGA_MEMORY__DIR", RETIRED_DIRECTORY)
    with pytest.raises(ConfigError) as excinfo:
        load_file_config()

    walked: list[BaseException] = []
    cause: BaseException | None = excinfo.value
    while cause is not None:
        walked.append(cause)
        cause = cause.__cause__ or cause.__context__
    assert all(RETIRED_DIRECTORY not in str(one) for one in walked)


@pytest.mark.parametrize("variable", ["VINGA_server__port", "ViNgA_SeRvEr__PoRt"])
def test_a_server_half_override_still_applies_in_any_case(
    monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    """The other side of the same fact: the file half keeps every
    spelling pydantic-settings accepts, so the scan above must not be a
    match on the prefix alone."""
    monkeypatch.setenv(variable, "9111")
    assert load_file_config().server.port == 9111


def test_the_variables_that_carry_a_value_are_left_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The scan matches the six moved section names and nothing else, so
    the reserved variables are outside it by construction. Pinned
    because a scan written as "every VINGA_ variable we do not know"
    would take the auth secret and the master key with it."""
    for variable in ("VINGA_AUTH_SECRET", "VINGA_MASTER_KEY", "VINGA_REVISION"):
        monkeypatch.setenv(variable, "value")
    assert load_file_config().server.port == 8003


def test_default_agent_must_be_defined() -> None:
    with pytest.raises(ConfigError, match="default_agent: names no agent that exists"):
        load_config_from_data({"default_agent": "ghost"})


def test_agents_no_device_can_reach_are_rejected() -> None:
    with pytest.raises(ConfigError, match="default_agent is required"):
        load_config_from_data({"agents": {"assistant": {}}})


def test_bound_devices_make_default_agent_optional() -> None:
    """Omitting default_agent is how a deployment says "only these
    devices": the devices map becomes the allowlist, and every unknown
    MAC resolves to nothing."""
    config = load_config_from_data(
        {
            "agents": {"assistant": {}},
            "devices": {"aa:bb:cc:dd:ee:ff": ["assistant"]},
        }
    )
    assert config.default_agent is None
    assert config.agents_for_device("aa:bb:cc:dd:ee:ff") == ["assistant"]
    assert config.agents_for_device("11:22:33:44:55:66") == []


def test_device_bound_to_unknown_agent() -> None:
    data = {
        "agents": {"assistant": {}},
        "default_agent": "assistant",
        "devices": {"aa:bb:cc:dd:ee:ff": ["ghost"]},
    }
    with pytest.raises(
        ConfigError, match=r"devices\.aa:bb:cc:dd:ee:ff: entry \d+ names no agent that exists"
    ):
        load_config_from_data(data)


def test_agent_referencing_unknown_provider_lists_defined_ones() -> None:
    data = {
        "providers": {"llm": {"claude": {"type": "anthropic"}}},
        "agents": {"assistant": {"llm": "claud"}},
        "default_agent": "assistant",
    }
    with pytest.raises(
        ConfigError,
        match=r"assistant\.llm: names no llm provider that exists, and the name is not "
        r"quoted back \(defined: claude\)",
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
    assert (
        "agent_defaults.llm: names no llm provider that exists, and the name is not "
        "quoted back (defined: claude)"
    ) in message
    assert "agents.poet" not in message


def test_agent_defaults_reject_a_prompt() -> None:
    # A prompt is a persona's identity; inheriting one silently would make
    # two agents the same agent.
    with pytest.raises(ConfigError, match="agent_defaults.prompt"):
        load_config_from_data({"agent_defaults": {"prompt": "You are helpful."}})


def test_inline_secret_is_rejected_with_env_hint() -> None:
    data = {"providers": {"llm": {"claude": {"type": "anthropic", "api_key": "sk-123"}}}}
    with pytest.raises(ConfigError, match="ending in _env"):
        load_config_from_data(data)


# The option name, and the fragment of it that makes it secret-shaped.
# The refusal names the second rather than the first: an option is a key
# the caller wrote, and a key is as good a place to paste a credential
# as a value is, so what a refusal may say about one is which of this
# repository's own words it matched. That the invented part of a key
# reaches nothing is pinned with a planted one, in the API's own suite.
SECRET_LIKE_OPTIONS = [
    ("client_secret", "secret"),
    ("secret_key", "secret"),
    ("auth_token", "token"),
    ("password", "password"),
]


@pytest.mark.parametrize(("key", "matched"), SECRET_LIKE_OPTIONS)
def test_secret_like_option_names_are_rejected(key: str, matched: str) -> None:
    data = {"providers": {"llm": {"x": {"type": "openai_compatible", key: "shh"}}}}
    with pytest.raises(ConfigError) as caught:
        load_config_from_data(data)

    message = str(caught.value)
    assert f'a key containing "{matched}"' in message
    assert "providers.llm.x" in message
    assert "shh" not in message


def test_a_pasted_value_in_an_env_key_is_rejected_without_quoting_it() -> None:
    """Nothing else stops a credential from being pasted where its
    variable name belongs, and the value would then sit unencrypted in
    the file. It never worked either: the name is looked up in the
    environment, and no lookup of a pasted key succeeds."""
    pasted = "sk-live-4f8b2c9e-never-a-real-credential"

    for entry in (
        {"type": "anthropic", "api_key_env": pasted},
        {"type": "openai_compatible", "client_secret_env": pasted},
        # Padding is refused for the same reason: os.environ is not
        # asked about a name with spaces around it.
        {"type": "anthropic", "api_key_env": " ANTHROPIC_API_KEY "},
    ):
        with pytest.raises(ConfigError) as excinfo:
            load_config_from_data({"providers": {"llm": {"claude": entry}}})
        message = str(excinfo.value)
        assert "name of an environment variable" in message
        assert pasted not in message

    # A value that is not a string at all is refused the same way.
    with pytest.raises(ConfigError):
        load_config_from_data(
            {"providers": {"llm": {"claude": {"type": "anthropic", "token_env": 7}}}}
        )


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
            "devices": {"AA-BB-CC-DD-EE-FF": ["assistant"]},
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


def test_a_binding_written_as_one_name_is_refused() -> None:
    """A binding is a list of agent names, and a bare string is not one
    of them any more. Composing a configuration from a raw mapping is
    the one route that reaches the field with something a write path
    has not already shaped, so it is where the refusal is asked for,
    and the refusal is the field's own type reported against the device
    it was written under."""
    data = {
        "agents": {"assistant": {}},
        "devices": {"aa:bb:cc:dd:ee:ff": "assistant"},
    }
    with pytest.raises(ConfigError) as excinfo:
        load_config_from_data(data)

    message = str(excinfo.value)
    assert "devices.aa:bb:cc:dd:ee:ff" in message
    assert "valid list" in message


def test_a_binding_that_is_a_pasted_credential_is_not_repeated() -> None:
    """The same refusal over the value an operator can most expensively
    write there. What is rendered is the location and pydantic's own
    sentence about the type, and the input is not part of either.

    The chain as well as the message: pydantic's ValidationError holds
    every rejected input in its `errors()`, so leaving it as the
    refusal's cause or context would hand the value to anything walking
    behind the sentence."""
    data = {
        "agents": {"assistant": {}},
        "devices": {"aa:bb:cc:dd:ee:ff": PARSER_SENTINEL},
    }
    with pytest.raises(ConfigError) as excinfo:
        load_config_from_data(data)

    message = str(excinfo.value)
    assert "devices.aa:bb:cc:dd:ee:ff" in message
    assert PARSER_SENTINEL not in message
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__context__ is None


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
    with pytest.raises(
        ConfigError, match=r"one agent is named at more than one position \(1, 2\)"
    ):
        load_config_from_data(data)


def test_an_unknown_agent_in_a_device_list_is_rejected() -> None:
    data = {
        "agents": {"poet": {}},
        "default_agent": "poet",
        "devices": {"aa:bb:cc:dd:ee:ff": ["poet", "ghost"]},
    }
    with pytest.raises(
        ConfigError, match=r"devices\.aa:bb:cc:dd:ee:ff: entry \d+ names no agent that exists"
    ):
        load_config_from_data(data)


def test_invalid_mac_is_rejected() -> None:
    """The field it was written under, the rule, and never the key that
    failed it (#205). A configuration file is a place a paste lands like
    any other, and this refusal is printed and logged at boot."""
    with pytest.raises(ConfigError) as caught:
        load_config_from_data({"devices": {PARSER_SENTINEL: ["assistant"]}})

    refusal = str(caught.value)
    assert PARSER_SENTINEL not in refusal, refusal
    assert f"devices: {NOT_A_MAC}" in refusal, refusal


def test_colliding_macs_are_rejected() -> None:
    data = {
        "agents": {"assistant": {}},
        "default_agent": "assistant",
        "devices": {
            "AA:BB:CC:DD:EE:FF": ["assistant"],
            "aa-bb-cc-dd-ee-ff": ["assistant"],
        },
    }
    with pytest.raises(ConfigError, match="more than once"):
        load_config_from_data(data)


def test_normalize_mac_rejects_garbage() -> None:
    """One sentence for every caller, holding the rule and nothing of
    what was handed in: this validator serves the configuration store,
    the OTA check-in, the websocket handshake and the conversations
    query, so its message reaches surfaces it cannot see (#205)."""
    for handed_in in ("aa:bb:cc:dd:ee", PARSER_SENTINEL):
        with pytest.raises(ValueError) as caught:
            normalize_mac(handed_in)
        assert str(caught.value) == NOT_A_MAC
    # And the rule is still stated, which is the whole of what the
    # sentence is for.
    assert "six colon-separated hex pairs" in NOT_A_MAC
    assert "aa:bb:cc:dd:ee:ff" in NOT_A_MAC


def test_multiple_problems_reported_together() -> None:
    data = {
        "agents": {"assistant": {"llm": "ghost"}},
        "devices": {"aa:bb:cc:dd:ee:ff": ["nobody"]},
        "default_agent": "also-nobody",
    }
    with pytest.raises(ConfigError) as excinfo:
        load_config_from_data(data)
    message = str(excinfo.value)
    assert "default_agent: names no agent that exists" in message
    assert "devices.aa:bb:cc:dd:ee:ff: entry 1 names no agent that exists" in message
    assert "names no llm provider that exists" in message

