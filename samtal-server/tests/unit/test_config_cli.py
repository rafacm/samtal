"""The config command group, driven through its entry function.

The acceptance case is the first test: an empty directory becomes a
working configuration through CLI calls alone, in the natural order,
with nothing wedging on the way. The rest is what has to hold around
it: secrets masked wherever they are read back, the staging notice on
every write, and no failure path that lets a plaintext, a rejected
fragment or a traceback out.
"""

import io
import logging
import sys
from pathlib import Path

import pytest
import yaml
from sqlalchemy import update

from samtal_server.config import cli
from samtal_server.config.secrets import MASK, MASTER_KEY_ENV, generate_key
from samtal_server.db import open_database, schema

# Not real credentials, and shaped so a substring check for one cannot
# match by accident.
SECRET = "sk-test-4f8b2c9e-never-a-real-credential"
OTHER_SECRET = "tok-test-7a1d3f60-never-a-real-credential"


@pytest.fixture
def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Run one command the way the entry point runs it, against a
    database of this test's own."""
    monkeypatch.delenv("SAMTAL_CONFIG", raising=False)
    monkeypatch.setenv("SAMTAL_SERVER__DATABASE__DIR", str(tmp_path / "db"))
    monkeypatch.setenv(MASTER_KEY_ENV, generate_key())

    def _run(*argv: str, stdin: str | None = None) -> int:
        if stdin is not None:
            monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
        return cli.main(list(argv))

    return _run


def _document(out: str) -> object:
    """A `show` document without the secret notes underneath it."""
    return yaml.safe_load("\n".join(line for line in out.splitlines() if not line.startswith("#")))


def test_an_empty_database_becomes_a_working_configuration(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The natural order, end to end: providers, MCP servers, agents,
    devices, default agent. Every intermediate state here would fail the
    boot-only completeness rule, and none of the writes may be refused."""
    claude = "type: anthropic\nmodel: m\n"
    assert run("set", "provider", "llm", "claude", "-f", "-", stdin=claude) == 0
    assert run("set", "provider", "asr", "whisper", "-f", "-", stdin="type: mock\n") == 0
    assert run("set", "provider", "tts", "voice", "-f", "-", stdin="type: mock\n") == 0
    assert run("set", "provider", "vad", "ears", "-f", "-", stdin="type: mock\n") == 0
    assert run(
        "set",
        "mcp-server",
        "home",
        "-f",
        "-",
        stdin="transport: stdio\ncommand: uvx\negress: false\n",
    ) == 0
    assert run(
        "set",
        "agent-defaults",
        "-f",
        "-",
        stdin="llm: claude\nasr: whisper\ntts: voice\nvad: ears\nmcp: [home]\n",
    ) == 0
    # The agent no default_agent names yet: the write that would deadlock
    # if completeness were enforced here.
    assert run("set", "agent", "sam", "-f", "-", stdin="prompt: You are Sam.\n") == 0
    assert run("bind-device", "AA-BB-CC-DD-EE-FF", "sam") == 0
    assert run("set-default-agent", "sam") == 0
    capsys.readouterr()

    assert run("show") == 0
    shown = _document(capsys.readouterr().out)

    assert shown["providers"]["llm"]["claude"] == {"type": "anthropic", "model": "m"}
    assert shown["mcp_servers"]["home"]["command"] == "uvx"
    assert shown["agent_defaults"]["mcp"] == ["home"]
    assert shown["agents"]["sam"]["prompt"] == "You are Sam."
    assert shown["devices"] == {"aa:bb:cc:dd:ee:ff": ["sam"]}
    assert shown["default_agent"] == "sam"


def test_a_fragment_can_come_from_a_file(
    run, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fragment = tmp_path / "claude.yaml"
    fragment.write_text("type: anthropic\nmodel: m\n", encoding="utf-8")

    assert run("set", "provider", "llm", "claude", "-f", str(fragment)) == 0
    capsys.readouterr()

    assert run("show", "provider", "llm", "claude") == 0
    assert _document(capsys.readouterr().out) == {"type": "anthropic", "model": "m"}


def test_a_missing_fragment_file_is_named(run, capsys: pytest.CaptureFixture[str]) -> None:
    assert run("set", "agent", "sam", "-f", "/nowhere/at/all.yaml") == 1

    captured = capsys.readouterr()
    assert "fragment file not found" in captured.err
    assert "Traceback" not in captured.err


def test_every_mutating_command_says_the_write_is_staging(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    run("set", "provider", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")
    assert "STAGING ONLY" in capsys.readouterr().err

    run("set", "agent", "sam", "-f", "-", stdin="llm: claude\n")
    assert "STAGING ONLY" in capsys.readouterr().err

    run("set-default-agent", "sam")
    assert "STAGING ONLY" in capsys.readouterr().err

    # A read is not a write, and says nothing.
    run("list")
    assert "STAGING ONLY" not in capsys.readouterr().err


def test_a_refused_write_exits_one_with_the_reason(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("set", "agent", "sam", "-f", "-", stdin="llm: ghost\n") == 1

    captured = capsys.readouterr()
    assert 'unknown llm provider "ghost"' in captured.err
    assert "Traceback" not in captured.err
    assert captured.out == ""


def test_an_invalid_fragment_is_refused_without_echoing_it(
    run, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    """An inline secret in a fragment is the case where the rejected
    input is itself the thing that must not be printed back."""
    with caplog.at_level(logging.DEBUG):
        fragment = f"type: anthropic\napi_key: {SECRET}\n"
        assert run("set", "provider", "llm", "claude", "-f", "-", stdin=fragment) == 1

    captured = capsys.readouterr()
    assert "looks like an inline secret" in captured.err
    assert SECRET not in captured.err
    assert SECRET not in captured.out
    assert SECRET not in caplog.text


def test_malformed_yaml_is_refused_without_echoing_the_line(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("set", "agent", "sam", "-f", "-", stdin=f"prompt: '{SECRET}\n") == 1

    captured = capsys.readouterr()
    assert "invalid YAML" in captured.err
    assert SECRET not in captured.err
    assert "Traceback" not in captured.err


def test_a_secret_is_read_from_stdin_and_never_shown(
    run, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    run("set", "provider", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")
    capsys.readouterr()

    with caplog.at_level(logging.DEBUG):
        assert run("set-secret", "provider", "llm", "claude", "api_key", stdin=f"{SECRET}\n") == 0

    captured = capsys.readouterr()
    assert "wrote secret for provider llm.claude api_key" in captured.out
    assert SECRET not in captured.out
    assert SECRET not in captured.err
    assert SECRET not in caplog.text


def test_a_secret_can_come_from_a_named_variable(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("set", "provider", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")
    monkeypatch.setenv("SAMTAL_TEST_KEY", SECRET)

    assert run(
        "set-secret", "provider", "llm", "claude", "api_key", "--from-env", "SAMTAL_TEST_KEY"
    ) == 0

    monkeypatch.delenv("SAMTAL_TEST_KEY")
    assert run(
        "set-secret", "provider", "llm", "claude", "api_key", "--from-env", "SAMTAL_TEST_KEY"
    ) == 1
    captured = capsys.readouterr()
    assert "SAMTAL_TEST_KEY" in captured.err
    assert SECRET not in captured.err


def test_an_interactive_terminal_is_read_without_echo(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A typed secret must not land in the scrollback, so a terminal is
    read through getpass rather than by reading stdin."""
    run("set", "provider", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")

    class Terminal(io.StringIO):
        def isatty(self) -> bool:
            return True

    asked: list[str] = []
    monkeypatch.setattr(sys, "stdin", Terminal("this is never read\n"))
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: asked.append(prompt) or SECRET)

    assert run("set-secret", "provider", "llm", "claude", "api_key") == 0

    assert asked, "the terminal was read without getpass"
    assert SECRET not in capsys.readouterr().out


def test_an_empty_secret_is_refused(run, capsys: pytest.CaptureFixture[str]) -> None:
    run("set", "provider", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")

    assert run("set-secret", "provider", "llm", "claude", "api_key", stdin="\n") == 1
    assert "empty" in capsys.readouterr().err


def test_show_and_list_mask_stored_secrets_and_mark_what_they_shadow(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    run(
        "set",
        "provider",
        "llm",
        "claude",
        "-f",
        "-",
        stdin="type: anthropic\nmodel: m\napi_key_env: ANTHROPIC_API_KEY\n",
    )
    run(
        "set",
        "mcp-server",
        "weather",
        "-f",
        "-",
        stdin="transport: streamable_http\nurl: https://example.invalid/mcp\n",
    )
    run("set-secret", "provider", "llm", "claude", "api_key", stdin=SECRET)
    run("set-secret", "mcp-server", "weather", "headers.Authorization", stdin=OTHER_SECRET)
    capsys.readouterr()

    run("show")
    shown = capsys.readouterr().out
    assert MASK in shown
    assert SECRET not in shown
    assert OTHER_SECRET not in shown
    # The environment reference is not a secret, and the stored value
    # that displaces it is marked rather than left silent.
    assert "api_key_env: ANTHROPIC_API_KEY" in shown
    assert "used instead of api_key_env: ANTHROPIC_API_KEY" in shown

    run("show", "provider", "llm", "claude")
    assert f"api_key: {MASK}" in capsys.readouterr().out

    run("list")
    listed = capsys.readouterr().out
    assert "[secrets: api_key]" in listed
    assert "[secrets: headers.Authorization]" in listed
    assert SECRET not in listed and OTHER_SECRET not in listed


def test_a_pasted_credential_in_a_reference_field_is_refused(
    run, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    """An api_key_env holding the credential instead of its variable
    name would be written to the row unencrypted, and it never worked
    either. The write is refused, and the value the fragment carried
    goes nowhere: not to stdout, stderr or a log record."""
    fragment = f"type: anthropic\nmodel: m\napi_key_env: {SECRET}\n"

    with caplog.at_level(logging.DEBUG):
        assert run("set", "provider", "llm", "claude", "-f", "-", stdin=fragment) == 1

    captured = capsys.readouterr()
    assert "name of an environment variable" in captured.err
    assert SECRET not in captured.err
    assert SECRET not in captured.out
    assert SECRET not in caplog.text
    assert "Traceback" not in captured.err

    # And nothing was written: the entity does not exist.
    assert run("show", "provider", "llm", "claude") == 1
    assert "no such provider" in capsys.readouterr().err


def test_an_mcp_reference_shows_and_anything_else_in_its_place_does_not(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The model already requires a $VAR for a secret-bearing key, so a
    valid entry displays exactly as it was written; the mask is what
    covers a value that got in another way."""
    run(
        "set",
        "mcp-server",
        "weather",
        "-f",
        "-",
        stdin=(
            "transport: streamable_http\n"
            "url: https://example.invalid/mcp\n"
            "headers:\n"
            "  Authorization: $WEATHER_TOKEN\n"
            "  X-Region: eu\n"
        ),
    )
    capsys.readouterr()

    run("show", "mcp-server", "weather")
    shown = capsys.readouterr().out
    assert "$WEATHER_TOKEN" in shown
    # A key that carries no secret keeps its literal value: masking it
    # would hide configuration for nothing.
    assert "eu" in shown


def test_show_renders_every_entity_kind(run, capsys: pytest.CaptureFixture[str]) -> None:
    run("set", "provider", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")
    run(
        "set",
        "mcp-server",
        "home",
        "-f",
        "-",
        stdin="transport: stdio\ncommand: uvx\n",
    )
    run("set", "agent-defaults", "-f", "-", stdin="llm: claude\n")
    run("set", "agent", "sam", "-f", "-", stdin="prompt: You are Sam.\n")
    run("bind-device", "aa:bb:cc:dd:ee:ff", "sam")
    capsys.readouterr()

    run("show", "mcp-server", "home")
    assert _document(capsys.readouterr().out)["command"] == "uvx"
    run("show", "agent", "sam")
    assert _document(capsys.readouterr().out)["prompt"] == "You are Sam."
    run("show", "agent-defaults")
    assert _document(capsys.readouterr().out) == {"llm": "claude"}
    run("show", "device", "AA-BB-CC-DD-EE-FF")
    assert _document(capsys.readouterr().out) == {"agents": ["sam"]}


def test_showing_something_that_is_not_there_names_it(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    for argv in (
        ("show", "provider", "llm", "ghost"),
        ("show", "mcp-server", "ghost"),
        ("show", "agent", "ghost"),
        ("show", "device", "aa:bb:cc:dd:ee:ff"),
    ):
        assert run(*argv) == 1
        captured = capsys.readouterr()
        assert "no such" in captured.err
        assert "Traceback" not in captured.err


def test_the_default_agent_can_be_cleared(run, capsys: pytest.CaptureFixture[str]) -> None:
    run("set", "agent", "sam", "-f", "-", stdin="prompt: You are Sam.\n")
    run("set-default-agent", "sam")
    capsys.readouterr()

    assert run("clear-default-agent") == 0
    run("list")
    assert "default_agent: (none)" in capsys.readouterr().out


def test_deleting_an_entity_takes_its_secrets_with_it(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    run("set", "provider", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")
    run("set-secret", "provider", "llm", "claude", "api_key", stdin=SECRET)
    capsys.readouterr()

    assert run("delete", "provider", "llm", "claude") == 0
    capsys.readouterr()

    run("list")
    listed = capsys.readouterr().out
    assert "claude" not in listed
    assert "[secrets:" not in listed


def test_a_secret_can_be_cleared(run, capsys: pytest.CaptureFixture[str]) -> None:
    run("set", "provider", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")
    run("set-secret", "provider", "llm", "claude", "api_key", stdin=SECRET)
    capsys.readouterr()

    assert run("clear-secret", "provider", "llm", "claude", "api_key") == 0
    run("list")
    assert "[secrets:" not in capsys.readouterr().out


def test_the_cli_still_works_when_the_key_is_missing_or_wrong(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The recovery case the boot check is deliberately kept out of
    opening the database for: reading, deleting and replacing all treat
    ciphertext as opaque, and only storing a new secret needs a key."""
    run("set", "provider", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")
    run("set-secret", "provider", "llm", "claude", "api_key", stdin=SECRET)
    capsys.readouterr()

    monkeypatch.delenv(MASTER_KEY_ENV)
    assert run("list") == 0
    assert "[secrets: api_key]" in capsys.readouterr().out
    assert run("show", "provider", "llm", "claude") == 0
    shown = capsys.readouterr().out
    assert MASK in shown
    assert SECRET not in shown

    assert run("set-secret", "provider", "llm", "claude", "api_key", stdin=SECRET) == 1
    assert MASTER_KEY_ENV in capsys.readouterr().err

    # And the unreadable secret can be removed and the entity replaced.
    assert run("clear-secret", "provider", "llm", "claude", "api_key") == 0
    replacement = "type: anthropic\nmodel: n\n"
    assert run("set", "provider", "llm", "claude", "-f", "-", stdin=replacement) == 0


def test_an_unusable_key_names_its_position_and_not_its_material(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("set", "provider", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")
    capsys.readouterr()
    monkeypatch.setenv(MASTER_KEY_ENV, "not-a-fernet-key")

    assert run("set-secret", "provider", "llm", "claude", "api_key", stdin=SECRET) == 1

    captured = capsys.readouterr()
    assert MASTER_KEY_ENV in captured.err
    assert "not-a-fernet-key" not in captured.err
    assert SECRET not in captured.err
    assert "Traceback" not in captured.err


def test_a_row_of_the_wrong_shape_is_reported_rather_than_raised(
    run, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The reading commands are what an operator reaches for when
    something is wrong with the database, so a row that cannot be read
    has to come back as a sentence rather than as a traceback."""
    run("set", "provider", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")
    capsys.readouterr()

    engine = open_database(tmp_path / "db")
    try:
        with engine.begin() as connection:
            connection.execute(update(schema.providers).values(options="not an object"))
    finally:
        engine.dispose()

    for argv in (("list",), ("show",), ("show", "provider", "llm", "claude")):
        assert run(*argv) == 1
        captured = capsys.readouterr()
        assert "options" in captured.err
        assert "Traceback" not in captured.err


def test_a_mistake_in_the_grammar_exits_one_like_every_other_failure(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """argparse exits 2 from inside parse_args, which would make an
    unknown command the one failure that bypasses the documented exit
    codes and the sanitized boundary."""
    for argv in (
        ("nonsense",),
        (),
        ("set", "provider", "llm"),
        ("show", "provider"),
        ("list", "--nope"),
    ):
        assert run(*argv) == 1, argv
        captured = capsys.readouterr()
        assert captured.err.strip()
        assert "Traceback" not in captured.err


def test_an_extra_argument_is_refused_without_echoing_it(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The mistake this covers is typing the secret after the slot,
    which is where argparse would otherwise echo it back."""
    assert run("set-secret", "provider", "llm", "claude", "api_key", SECRET) == 1

    captured = capsys.readouterr()
    assert "unrecognized extra arguments" in captured.err
    assert SECRET not in captured.err
    assert SECRET not in captured.out


def test_asking_for_help_is_not_a_failure(run, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        run("--help")

    assert caught.value.code == 0
    assert "usage: samtal-server config" in capsys.readouterr().out


def test_a_database_that_cannot_be_opened_names_the_key(
    run, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("", encoding="utf-8")
    monkeypatch.setenv("SAMTAL_SERVER__DATABASE__DIR", str(blocker / "db"))

    assert run("list") == 1

    captured = capsys.readouterr()
    assert "server.database.dir" in captured.err
    assert "Traceback" not in captured.err


def test_the_database_directory_comes_from_the_config_file(
    run, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The same key the server reads, through the same machinery, so a
    deployment names its database directory once."""
    monkeypatch.delenv("SAMTAL_SERVER__DATABASE__DIR")
    named = tmp_path / "named"
    config = tmp_path / "config.yaml"
    config.write_text(f"server:\n  database:\n    dir: {named}\n", encoding="utf-8")

    assert run("--config", str(config), "list") == 0
    assert (named / "samtal.db").is_file()

    # And a config file that is not there is an error, not a default.
    assert run("--config", str(tmp_path / "nowhere.yaml"), "list") == 1
    assert "config file not found" in capsys.readouterr().err
