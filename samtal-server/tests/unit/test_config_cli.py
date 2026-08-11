"""The config command group, driven through its entry function.

This is the acceptance suite for the whole write path. The commands are
the ones an operator types, parsed by the real grammar, sent as real
HTTP requests to the real sub-application, handled by the real
repository against a scratch database. What replaces the socket is the
client factory: Starlette's TestClient is itself a synchronous
`httpx.Client` subclass driving an ASGI application through its own
portal, so `cli.main()` stays the unchanged synchronous entry point and
nothing bridges an event loop.

The first test is the acceptance case: an empty database becomes a
working configuration through CLI calls alone, in the natural order,
with nothing wedging on the way. The rest is what has to hold around
it: the exact sentences, which is the regression net for "the API
carries the repository's message and the CLI prints it"; secrets masked
wherever they are read back; the restart notice on every write; the
transport policy that keeps the token off a clear connection; and no
failure path that lets a plaintext, a rejected fragment or a traceback
out.
"""

import io
import logging
import sqlite3
import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import update

from samtal_server import db as db_module
from samtal_server.config import cli
from samtal_server.config.api import MOUNT_PATH, build_api, mount_api
from samtal_server.config.loader import ConfigError, load_file_config
from samtal_server.config.secrets import MASK, MASTER_KEY_ENV, generate_key
from samtal_server.db import DATABASE_FILENAME, open_database, schema

# Not real credentials, and shaped so a substring check for one cannot
# match by accident.
SECRET = "sk-test-4f8b2c9e-never-a-real-credential"
OTHER_SECRET = "tok-test-7a1d3f60-never-a-real-credential"

API_SECRET_ENV = "SAMTAL_API_SECRET"

TOKEN = "test-api-token-" + "0123456789abcdef" * 2

# Short enough that a blocked writer gives up inside a test run, and
# long enough that an unblocked one never sees it.
SHORT_BUSY_MS = 200


@pytest.fixture
def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Run one command the way the entry point runs it, against a server
    of this test's own.

    The application is built per request rather than once, from the
    database directory the CLI itself would have resolved, because that
    is what a deployment's server does too: the CLI and the server read
    `server.database.dir` through the same machinery and cannot disagree
    about it.
    """
    monkeypatch.delenv("SAMTAL_CONFIG", raising=False)
    monkeypatch.delenv(cli.API_URL_ENV, raising=False)
    monkeypatch.setenv("SAMTAL_SERVER__DATABASE__DIR", str(tmp_path / "db"))
    monkeypatch.setenv(MASTER_KEY_ENV, generate_key())
    monkeypatch.setenv(API_SECRET_ENV, TOKEN)

    reached: list[str] = []

    def factory(base_url: str, token: str) -> TestClient:
        reached.append(base_url)
        directory = load_file_config(None).server.database.dir
        api = build_api(token, directory)
        # A base URL with a path prefix is the deployed shape, where the
        # sub-application is mounted on the server's own port, so the
        # fixture mounts it exactly where the server does rather than
        # serving it at the root and letting the prefix go nowhere.
        served: object = api
        if urlsplit(base_url).path.rstrip("/"):
            assert urlsplit(base_url).path.rstrip("/") == MOUNT_PATH
            served = FastAPI()
            mount_api(served, api)
        return TestClient(
            served,
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}"},
        )

    monkeypatch.setattr(cli, "build_client", factory)

    def _run(*argv: str, stdin: str | None = None) -> int:
        if stdin is not None:
            monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
        return cli.main(list(argv))

    _run.reached = reached
    return _run


def _chain(exc: BaseException) -> str:
    """Everything an exception carries, including what a chain walker
    would find behind it."""
    parts: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        parts += [repr(current), str(current)]
        current = current.__cause__ or current.__context__
    return "\n".join(parts)


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


def test_every_mutating_command_says_when_the_write_applies(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The configuration is a boot-time snapshot by design, which makes
    a write that quietly waits for a restart the one thing about that
    design an operator can be caught by."""
    run("set", "provider", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")
    assert cli.RESTART_NOTICE in capsys.readouterr().err

    run("set", "agent", "sam", "-f", "-", stdin="llm: claude\n")
    assert cli.RESTART_NOTICE in capsys.readouterr().err

    run("set-default-agent", "sam")
    assert cli.RESTART_NOTICE in capsys.readouterr().err

    # A read is not a write, and says nothing.
    run("list")
    assert cli.RESTART_NOTICE not in capsys.readouterr().err


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


def test_a_secret_nested_in_an_option_is_refused_and_never_read_back(
    run, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    """A provider option can be a structure, so both halves are checked
    here: a secret-shaped key nested inside one is refused without
    quoting the value, and a nested reference key holding something
    that is not a reference is masked by `show` rather than printed."""
    nested = f"type: anthropic\nconnection:\n  api_key: {SECRET}\n"

    with caplog.at_level(logging.DEBUG):
        assert run("set", "provider", "llm", "claude", "-f", "-", stdin=nested) == 1

    captured = capsys.readouterr()
    assert "connection.api_key" in captured.err
    assert "looks like an inline secret" in captured.err
    assert SECRET not in captured.err
    assert SECRET not in captured.out
    assert SECRET not in caplog.text

    pasted = "sk_test_4f8b2c9e_never_a_real_credential"
    accepted = f"type: anthropic\nconnection:\n  api_key_env: {pasted}\n"
    assert run("set", "provider", "llm", "claude", "-f", "-", stdin=accepted) == 0
    capsys.readouterr()

    assert run("show", "provider", "llm", "claude") == 0
    shown = capsys.readouterr().out
    # Quoted by the YAML dumper, since the mask begins with an alias
    # indicator; what matters is that the value shown is the mask.
    assert yaml.safe_load(shown)["connection"]["api_key_env"] == MASK
    assert pasted not in shown


def test_malformed_yaml_is_refused_without_echoing_the_line(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("set", "agent", "sam", "-f", "-", stdin=f"prompt: '{SECRET}\n") == 1

    captured = capsys.readouterr()
    assert "invalid YAML" in captured.err
    assert SECRET not in captured.err
    assert "Traceback" not in captured.err


def test_a_number_that_is_not_finite_is_refused(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """YAML spells NaN and infinity, JSON does not. A stored one would
    be read back as null, which silently turns the configuration into a
    different one, so the write is refused where every other fragment
    rule is applied."""
    assert run(
        "set", "provider", "llm", "claude", "-f", "-", stdin="type: anthropic\ntemperature: .nan\n"
    ) == 1

    captured = capsys.readouterr()
    assert "not a finite number" in captured.err
    assert "Traceback" not in captured.err

    # And a finite value goes through and shows as itself.
    assert run(
        "set", "provider", "llm", "claude", "-f", "-", stdin="type: anthropic\ntemperature: 0.7\n"
    ) == 0
    capsys.readouterr()
    run("show", "provider", "llm", "claude")
    assert _document(capsys.readouterr().out)["temperature"] == 0.7


TRANSPORT_REFUSALS = [
    ("a timestamp", "type: anthropic\nreleased: 2026-01-01\n", "JSON has no way to write"),
    (
        "a timestamp with a time",
        "type: anthropic\nwhen: 2026-01-01 12:00:00\n",
        "JSON has no way to write",
    ),
    ("binary", "type: anthropic\nblob: !!binary |\n  AAEC\n", "JSON has no way to write"),
    ("a set", "type: anthropic\ntags: !!set\n  ? a\n  ? b\n", "JSON has no way to write"),
    ("a recursive alias", "&loop\ntype: anthropic\nself: *loop\n", "contains itself"),
    ("an integer key", "type: anthropic\noptions:\n  1: x\n", "rather than a string"),
    ("a null key", "type: anthropic\noptions:\n  ~: x\n", "rather than a string"),
]


@pytest.mark.parametrize(("what", "fragment", "expected"), TRANSPORT_REFUSALS)
def test_a_fragment_json_cannot_carry_is_refused_before_it_travels(
    run, capsys: pytest.CaptureFixture[str], what: str, fragment: str, expected: str
) -> None:
    """YAML is the wider language, so a fragment can hold things the
    request body has no way to say. Every one of them meets the
    repository's sentence rather than the JSON encoder's TypeError,
    ValueError or RecursionError, and none of them writes anything."""
    assert run("set", "provider", "llm", "claude", "-f", "-", stdin=fragment) == 1, what

    captured = capsys.readouterr()
    assert expected in captured.err, what
    assert "Traceback" not in captured.err, what
    assert captured.out == "", what

    # And nothing was written: the entity does not exist.
    assert run("show", "provider", "llm", "claude") == 1
    assert "no such provider" in capsys.readouterr().err


def test_a_fragment_sharing_one_anchor_twice_still_travels(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The check refuses a structure that contains itself, not one that
    mentions the same anchor twice, which is an ordinary YAML file and
    is written out twice in JSON."""
    fragment = "type: anthropic\none: &shared\n  a: 1\ntwo: *shared\n"

    assert run("set", "provider", "llm", "claude", "-f", "-", stdin=fragment) == 0
    capsys.readouterr()

    run("show", "provider", "llm", "claude")
    shown = _document(capsys.readouterr().out)
    assert shown["one"] == {"a": 1}
    assert shown["two"] == {"a": 1}


def test_a_parser_failure_carries_no_parser_exception(tmp_path: Path) -> None:
    """A PyYAML mark holds the whole buffer it was parsing, which here
    is the fragment, so the refusal is built inside the handler and
    raised outside it: `from None` would leave the parser's exception
    reachable as __context__."""
    fragment = tmp_path / "broken.yaml"
    fragment.write_text(f"prompt: '{SECRET}\n", encoding="utf-8")

    with pytest.raises(cli.ConfigError) as caught:
        cli._fragment(str(fragment))

    assert "invalid YAML" in str(caught.value)
    assert SECRET not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None

    with pytest.raises(cli.ConfigError) as missing:
        cli._fragment(str(tmp_path / "nowhere.yaml"))

    assert missing.value.__cause__ is None
    assert missing.value.__context__ is None


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


def test_a_config_file_that_is_not_there_is_an_error_not_a_default(
    run, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("--config", str(tmp_path / "nowhere.yaml"), "list") == 1
    assert "config file not found" in capsys.readouterr().err


# Where the CLI sends a command, and what it will not send it over


def test_the_default_target_is_this_machine_on_the_configured_port(
    run, tmp_path: Path
) -> None:
    """The same file the server reads, through the same machinery, so a
    deployment names its port once and the CLI cannot disagree with the
    server about where the server is."""
    config = tmp_path / "config.yaml"
    config.write_text("server:\n  port: 9123\n", encoding="utf-8")

    assert run("--config", str(config), "list") == 0

    assert run.reached == [f"http://127.0.0.1:9123{MOUNT_PATH}"]


def test_the_environment_names_the_target_and_the_flag_beats_it(
    run, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(cli.API_URL_ENV, "http://127.0.0.1:9001/api")
    assert run("list") == 0

    assert run("--api-url", "http://localhost:9002/api", "list") == 0

    assert run.reached == ["http://127.0.0.1:9001/api", "http://localhost:9002/api"]


def test_a_plain_connection_to_another_host_is_refused(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The bearer token rides on every request and grants everything the
    API can do, so loopback-or-TLS is the rule for the whole client
    rather than a set-secret footnote, and there is deliberately no flag
    to override it."""
    assert run("--api-url", "http://config.example.invalid/api", "list") == 1

    captured = capsys.readouterr()
    assert "no flag to override" in captured.err
    assert "https://" in captured.err
    assert run.reached == []


def test_tls_to_another_host_is_permitted(run) -> None:
    assert run("--api-url", "https://config.example.invalid/api", "list") == 0

    assert run.reached == ["https://config.example.invalid/api"]


def test_the_mount_prefix_in_the_url_reaches_the_mounted_namespace(run) -> None:
    """The deployed shape: the API is mounted on the server's own port,
    so a base URL naming that prefix is what a request has to be joined
    onto."""
    assert run("--api-url", f"http://127.0.0.1:8003{MOUNT_PATH}", "list") == 0


# The URLs the parser itself refuses, each carrying the sentinel where
# the parser would have put it into its own ValueError.
UNREADABLE_URLS = [
    ("a port that is not a number", f"http://localhost:{SECRET}/api"),
    ("a port that is empty of digits", "http://localhost:notaport/api"),
    ("an unclosed IPv6 literal", f"http://[::1{SECRET}/api"),
    ("a malformed IPv6 literal", f"http://[bad::{SECRET}::x]:8003/api"),
]

# The URLs that parse and are then refused on their merits. These do
# name the address, minus any userinfo, because an operator who typed
# the wrong scheme needs to see which address was read that way.
UNUSABLE_URLS = [
    ("a scheme that is not http", "ftp://host/api"),
    ("no host at all", "http:///api"),
]


@pytest.mark.parametrize(("what", "url"), UNREADABLE_URLS)
def test_a_url_that_cannot_be_read_is_refused_inside_the_boundary(
    run, capsys: pytest.CaptureFixture[str], what: str, url: str
) -> None:
    """`urlsplit` raises on a malformed IPv6 literal and `.port` raises
    on a port that is not a number, and both carry the text they refused.
    Outside a handler that is a traceback out of main() with the address
    in it, which is the address somebody was typing a token near."""
    assert run("--api-url", url, "list") == 1, what

    captured = capsys.readouterr()
    assert "Traceback" not in captured.err, what
    assert SECRET not in captured.err, what
    assert captured.out == "", what
    assert run.reached == []


@pytest.mark.parametrize(("what", "url"), UNUSABLE_URLS)
def test_a_url_that_is_read_and_refused_names_the_address(
    run, capsys: pytest.CaptureFixture[str], what: str, url: str
) -> None:
    assert run("--api-url", url, "list") == 1, what

    captured = capsys.readouterr()
    assert "http://" in captured.err or "ftp://" in captured.err, what
    assert "Traceback" not in captured.err, what
    assert run.reached == []


def test_a_url_refusal_carries_no_parser_exception(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The chain, not just the message: a ValueError from the URL parser
    holds the text it refused, and anything that walks a chain reads
    what it holds."""
    with pytest.raises(ConfigError) as caught:
        cli._permitted(f"http://localhost:{SECRET}/api", "--api-url")

    assert SECRET not in _chain(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_a_url_carrying_a_credential_is_refused_without_repeating_it(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """A token does not belong in a URL, and the refusal for putting one
    there must not be what publishes it."""
    assert run("--api-url", f"https://user:{SECRET}@config.example.invalid/api", "list") == 1

    captured = capsys.readouterr()
    assert "username or a password" in captured.err
    assert SECRET not in captured.err
    assert "user" not in captured.err.split("https://")[-1].split("/")[0]
    assert run.reached == []


def test_a_missing_token_is_named_before_any_request_is_sent(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(API_SECRET_ENV)

    assert run("list") == 1

    captured = capsys.readouterr()
    assert API_SECRET_ENV in captured.err
    assert "--local" in captured.err
    assert run.reached == []


def test_the_token_comes_from_the_variable_the_config_file_names(
    run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Which on a deployment is the variable the server itself was
    started with, so exec into the running container and the CLI has the
    token for free."""
    config = tmp_path / "config.yaml"
    config.write_text("server:\n  api:\n    secret_env: SAMTAL_OTHER_TOKEN\n", encoding="utf-8")
    monkeypatch.delenv(API_SECRET_ENV)
    monkeypatch.setenv("SAMTAL_OTHER_TOKEN", TOKEN)

    assert run("--config", str(config), "list") == 0


def test_a_server_that_cannot_be_reached_says_so_and_names_the_recovery_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The real client, against a port nothing is listening on: the one
    case the injected test client cannot show."""
    monkeypatch.delenv("SAMTAL_CONFIG", raising=False)
    monkeypatch.setenv("SAMTAL_SERVER__DATABASE__DIR", str(tmp_path / "db"))
    monkeypatch.setenv(API_SECRET_ENV, TOKEN)

    assert cli.main(["--api-url", "http://127.0.0.1:1", "list"]) == 1

    captured = capsys.readouterr()
    assert "cannot reach the configuration API at http://127.0.0.1:1" in captured.err
    assert "--local" in captured.err
    assert "Traceback" not in captured.err


def test_a_body_that_is_not_this_api_s_own_is_not_relayed(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """What a proxy or a gateway returns is not this API's sanitized
    output, so it is reported as a status code and never printed."""
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse

    gateway = FastAPI()

    @gateway.get("/{path:path}")
    def refuse(path: str) -> HTMLResponse:
        return HTMLResponse(f"<html>502 {SECRET}</html>", status_code=502)

    monkeypatch.setattr(
        cli, "build_client", lambda base_url, token: TestClient(gateway, base_url=base_url)
    )

    assert run("list") == 1

    captured = capsys.readouterr()
    assert "answered 502" in captured.err
    assert SECRET not in captured.err
    assert "<html>" not in captured.err


def test_a_write_that_cannot_take_the_lock_prints_the_retryable_refusal(
    run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The reason the client's read timeout has margin above the
    database's busy timeout: the settled answer to contention is a
    sentence the operator can act on, and a client-side timeout at five
    seconds would replace it with one that says nothing.

    Both sides are taken under the same held lock, so what is asserted is
    that the CLI printed what the API answered, whatever that turns out
    to be: the API opens the database per request, so a held lock is met
    by the open-and-migrate step and the sentence is that one rather than
    the repository's own."""
    run("set", "agent", "sam", "-f", "-", stdin="prompt: You are Sam.\n")
    capsys.readouterr()
    monkeypatch.setattr(db_module, "BUSY_TIMEOUT_MS", SHORT_BUSY_MS)
    directory = tmp_path / "db"
    holder = sqlite3.connect(directory / DATABASE_FILENAME, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        over_http = TestClient(
            build_api(TOKEN, directory), headers={"Authorization": f"Bearer {TOKEN}"}
        ).put("/agents/sam", json={"prompt": "Still Sam."})
        assert run("set", "agent", "sam", "-f", "-", stdin="prompt: Still Sam.\n") == 1
    finally:
        holder.close()

    assert over_http.status_code == 409
    captured = capsys.readouterr()
    assert captured.err.rstrip("\n") == over_http.json()["detail"]
    assert captured.out == ""
    # And with the lock let go, the same command is answered.
    assert run("set", "agent", "sam", "-f", "-", stdin="prompt: Still Sam.\n") == 0


# The recovery subset


def test_every_local_invocation_says_what_it_is(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """There is no reliable way to tell whether a server is running
    against the same file, so the honest substitute for a refusal is
    saying what this path is, every time, reads included."""
    run("set", "provider", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")
    capsys.readouterr()

    assert run("--local", "show") == 0
    assert "bypassing the configuration API" in capsys.readouterr().err

    assert run("--local", "delete", "provider", "llm", "claude") == 0
    assert "bypassing the configuration API" in capsys.readouterr().err


def test_the_recovery_subset_needs_no_server(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The situation --local exists for: the four commands run against
    the database with nothing to ask, which is what `reached` staying
    empty says."""
    run("set", "provider", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")
    run.reached.clear()
    capsys.readouterr()

    assert run("--local", "set-secret", "provider", "llm", "claude", "api_key", stdin=SECRET) == 0
    assert run("--local", "show", "provider", "llm", "claude") == 0
    shown = capsys.readouterr().out
    assert f"api_key: {MASK}" in shown
    assert SECRET not in shown

    assert run("--local", "clear-secret", "provider", "llm", "claude", "api_key") == 0
    assert run("--local", "delete", "provider", "llm", "claude") == 0
    assert run.reached == []


def test_the_recovery_subset_works_with_a_key_that_will_not_load(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A `SAMTAL_MASTER_KEY` that is not a Fernet key is one of the exact
    conditions --local exists to repair: it refuses the boot, so there is
    no server to ask, and reading the keys eagerly would refuse the
    recovery tool for the same reason.

    Reading, deleting and clearing all treat ciphertext as opaque, so
    none of them needs a key at all."""
    run("set", "provider", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")
    run("set-secret", "provider", "llm", "claude", "api_key", stdin=SECRET)
    run.reached.clear()
    capsys.readouterr()
    monkeypatch.setenv(MASTER_KEY_ENV, "not-a-fernet-key")

    assert run("--local", "show") == 0
    whole = capsys.readouterr().out
    assert MASK in whole
    assert SECRET not in whole

    assert run("--local", "show", "provider", "llm", "claude") == 0
    assert f"api_key: {MASK}" in capsys.readouterr().out

    assert run("--local", "clear-secret", "provider", "llm", "claude", "api_key") == 0
    capsys.readouterr()
    assert run("--local", "delete", "provider", "llm", "claude") == 0
    capsys.readouterr()
    assert run.reached == []


def test_storing_a_secret_locally_still_needs_a_usable_key(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The one recovery command that cannot work without one, because it
    encrypts. It names the variable and never the material."""
    run("set", "provider", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")
    capsys.readouterr()
    monkeypatch.setenv(MASTER_KEY_ENV, "not-a-fernet-key")

    assert run("--local", "set-secret", "provider", "llm", "claude", "api_key", stdin=SECRET) == 1

    captured = capsys.readouterr()
    assert MASTER_KEY_ENV in captured.err
    assert "not-a-fernet-key" not in captured.err
    assert SECRET not in captured.err
    assert "Traceback" not in captured.err


def test_local_delete_removes_the_row_that_is_keeping_the_server_down(
    run, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The whole point of the break-glass path, end to end: a row the
    loader refuses is the row stopping the boot, so it is the one that
    has to come out, and every reading command refuses it on the way."""
    run("set", "provider", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")
    run("set", "provider", "asr", "whisper", "-f", "-", stdin="type: mock\n")
    capsys.readouterr()
    engine = open_database(tmp_path / "db")
    try:
        with engine.begin() as connection:
            connection.execute(
                update(schema.providers)
                .where(schema.providers.c.name == "claude")
                .values(options="not an object")
            )
    finally:
        engine.dispose()
    # Nothing can read it, which is the state a server meets at boot.
    assert run("--local", "show") == 1
    assert "options" in capsys.readouterr().err

    assert run("--local", "delete", "provider", "llm", "claude") == 0
    capsys.readouterr()

    # And with it gone the configuration reads again.
    assert run("--local", "show") == 0
    assert "whisper" in capsys.readouterr().out


def test_a_command_outside_the_subset_is_refused_naming_it(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    for argv in (
        ("--local", "list"),
        ("--local", "set", "agent", "sam", "-f", "-"),
        ("--local", "bind-device", "aa:bb:cc:dd:ee:ff", "sam"),
        ("--local", "set-default-agent", "sam"),
        ("--local", "clear-default-agent"),
    ):
        assert run(*argv, stdin="prompt: x\n") == 1, argv
        captured = capsys.readouterr()
        assert "show, delete, clear-secret and set-secret" in captured.err, argv
        assert captured.out == ""
    assert run.reached == []


def test_the_flag_is_accepted_after_its_command_too(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    run("set", "provider", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")
    run.reached.clear()
    capsys.readouterr()

    assert run("show", "provider", "llm", "claude", "--local") == 0
    assert run.reached == []


def test_local_show_reaches_a_name_no_new_write_could_create(
    run, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The reason the recovery subset goes by membership rather than by
    the write-time addressability rule: a row written before that rule
    existed has to stay readable and removable, and it cannot be reached
    over a URL path at all."""
    run("list")
    capsys.readouterr()
    engine = open_database(tmp_path / "db")
    try:
        with engine.begin() as connection:
            connection.execute(
                schema.providers.insert().values(
                    stage="llm", name="a/b", type="mock", egress=None, options={}, secrets={}
                )
            )
    finally:
        engine.dispose()

    assert run("--local", "show", "provider", "llm", "a/b") == 0
    assert "type: mock" in capsys.readouterr().out

    assert run("--local", "delete", "provider", "llm", "a/b") == 0
    capsys.readouterr()
    assert run("--local", "show", "provider", "llm", "a/b") == 1
    assert "no such provider" in capsys.readouterr().err


# Identities that only survive a URL path encoded


@pytest.mark.parametrize("name", ["a name with spaces", "100%-sure", "agente-café"])
def test_an_awkward_name_round_trips_through_the_whole_client(
    run, name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("set", "agent", name, "-f", "-", stdin="prompt: You are it.\n") == 0
    assert f"wrote agent {name}" in capsys.readouterr().out

    assert run("show", "agent", name) == 0
    assert _document(capsys.readouterr().out)["prompt"] == "You are it."

    assert run("delete", "agent", name) == 0
    capsys.readouterr()
    assert run("show", "agent", name) == 1
    assert "no such agent" in capsys.readouterr().err


def test_a_name_a_url_path_cannot_carry_is_refused(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """Refused at the repository when it can be reached, and unroutable
    when it cannot: either way no such entity is created."""
    assert run("set", "agent", "a/b", "-f", "-", stdin="prompt: You are it.\n") == 1
    capsys.readouterr()

    assert run("list") == 0
    assert "(none)" in capsys.readouterr().out
