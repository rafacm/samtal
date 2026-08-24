"""A credential's whole life on this surface, and where it may appear.

A secret reaches the configuration by one of three doors, all of them
here: typed at a terminal, which is read without echo so it does not
land in the scrollback; piped on stdin; or named as an environment
variable the CLI reads for itself. It is never an argument, because an
argument is what a shell history keeps.

After that the rule is one rule: it goes in and it does not come back
out. `show` and `list` mask what is stored and mark the environment
reference the stored value displaces, rather than leaving the shadowing
silent. A fragment carrying an inline credential where a `$VAR` belongs
is refused without the refusal quoting it, at every depth an option can
nest to. Deleting the entity takes its secrets with it.

The key is the last quarter of it. `VINGA_MASTER_KEY` encrypts what is
stored, and the recovery case is deliberate: reading, deleting and
replacing all treat ciphertext as opaque, so only storing a new secret
needs a key at all, and the refusal when there is none names the
variable and never the material.

Every assertion looks for a sentinel shaped so a substring check for it
cannot match by accident, on both streams and in every log record.
"""

import io
import logging
import sys
from pathlib import Path

import pytest
import yaml

from tests.support.config_cli import OTHER_SECRET, SECRET, runner
from vinga_server.config import cli
from vinga_server.config.secrets import MASK, MASTER_KEY_ENV


@pytest.fixture
def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """One command run the way the entry point runs it, against a server
    of this test's own."""
    return runner(tmp_path, monkeypatch)


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
    assert 'a key containing "api_key"' in captured.err
    assert "looks like an inline secret" in captured.err
    # The key the operator wrote is not printed back at them, because
    # this one could have been the credential.
    assert "connection" not in captured.err
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
    monkeypatch.setenv("VINGA_TEST_KEY", SECRET)

    assert run(
        "set-secret", "provider", "llm", "claude", "api_key", "--from-env", "VINGA_TEST_KEY"
    ) == 0

    monkeypatch.delenv("VINGA_TEST_KEY")
    assert run(
        "set-secret", "provider", "llm", "claude", "api_key", "--from-env", "VINGA_TEST_KEY"
    ) == 1
    captured = capsys.readouterr()
    assert "VINGA_TEST_KEY" in captured.err
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


# A credential shaped like a variable name: it gets past the models'
# paste check, which only asks that a reference look like a name, and is
# what the display path's own rule has to catch.
PASTED_REFERENCE = "sk_test_4f8b2c9e_never_a_real_credential"


def test_a_credential_nested_in_an_option_is_masked_in_the_rendered_document(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The masking is the view's and the rendering is the CLI's, so the
    depth the view masks at is the depth the printed YAML masks at. An
    option can be a structure, a reference key one level down accepts
    anything shaped like a variable name, and this is the command an
    operator runs when they suspect they pasted one."""
    run(
        "set",
        "provider",
        "llm",
        "claude",
        "-f",
        "-",
        stdin=(
            f"type: anthropic\nconnection:\n  api_key_env: {PASTED_REFERENCE}\n"
            "  host: example\n"
        ),
    )
    capsys.readouterr()

    assert run("show", "provider", "llm", "claude") == 0

    shown = capsys.readouterr().out
    assert MASK in shown
    assert "host: example" in shown
    assert PASTED_REFERENCE not in shown


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
    assert capsys.readouterr().err.startswith("providers:")


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


# A pasted credential holding none of the fragments that make an option
# name secret-shaped, so it is refused as a slot rather than accepted as
# one. The suite's own sentinels are not usable for that: they carry the
# word "credential", which is one of those fragments.
PASTED = "sk-live-3f9a1c7e-never-a-real-value"

# One entry per kind, against an entity that exists, so the check under
# test is the slot's own rather than an entity miss answering first: how
# the entity is written, how a secret on it is addressed, and the
# section the refusal names.
SLOTS = [
    (
        ("set", "provider", "llm", "claude", "-f", "-"),
        "type: anthropic\nmodel: m\n",
        ("provider", "llm", "claude", PASTED),
        "providers",
    ),
    (
        ("set", "mcp-server", "home", "-f", "-"),
        "transport: stdio\ncommand: uvx\n",
        ("mcp-server", "home", PASTED),
        "mcp_servers",
    ),
]


@pytest.mark.parametrize(("write", "fragment", "addressed", "section"), SLOTS)
@pytest.mark.parametrize("local", [False, True])
def test_a_slot_that_is_not_a_credential_slot_is_refused_without_printing_it(
    run,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    write: tuple[str, ...],
    fragment: str,
    addressed: tuple[str, ...],
    section: str,
    local: bool,
) -> None:
    """The slot half of a secret's address, on both paths (#132).

    `set-secret` is the command a credential is pasted into, so a
    credential typed one argument early lands in the slot. The entity it
    is offered to exists here on purpose: the entity-miss refusal used
    to answer first, which meant this check was never the one under
    test. Neither the slot nor the secret behind it may be printed.
    """
    assert run(*write, stdin=fragment) == 0
    capsys.readouterr()
    flags = ("--local",) if local else ()

    with caplog.at_level(logging.DEBUG):
        assert run(*flags, "set-secret", *addressed, stdin=SECRET) == 1

    captured = capsys.readouterr()
    assert PASTED not in captured.err
    assert PASTED not in captured.out
    assert SECRET not in captured.err
    assert SECRET not in captured.out
    assert captured.err.splitlines()[-1].startswith(f"{section}:")
    assert "Traceback" not in captured.err
    written = [record for record in caplog.records if record.name.startswith("vinga_server")]
    assert all(PASTED not in str(record.__dict__) for record in written)
    assert all(SECRET not in str(record.__dict__) for record in written)
