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
from tests.support.config_cli import chain as _chain
from tests.support.config_cli import logged as _logged
from vinga_server.config import cli
from vinga_server.config.loader import ConfigError
from vinga_server.config.secrets import MASK, MASTER_KEY_ENV


@pytest.fixture
def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """One command run the way the entry point runs it, against a server
    of this test's own."""
    return runner(monkeypatch)


def test_an_invalid_fragment_is_refused_without_echoing_it(
    run, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    """An inline secret in a fragment is the case where the rejected
    input is itself the thing that must not be printed back."""
    with caplog.at_level(logging.DEBUG):
        fragment = f"type: anthropic\napi_key: {SECRET}\n"
        assert run("provider", "set", "llm", "claude", "-f", "-", stdin=fragment) == 1

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
        assert run("provider", "set", "llm", "claude", "-f", "-", stdin=nested) == 1

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
    assert run("provider", "set", "llm", "claude", "-f", "-", stdin=accepted) == 0
    capsys.readouterr()

    assert run("provider", "show", "llm", "claude") == 0
    shown = capsys.readouterr().out
    # Quoted by the YAML dumper, since the mask begins with an alias
    # indicator; what matters is that the value shown is the mask.
    assert yaml.safe_load(shown)["connection"]["api_key_env"] == MASK
    assert pasted not in shown


def test_a_secret_is_read_from_stdin_and_never_shown(
    run, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    run("provider", "set", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")
    capsys.readouterr()

    with caplog.at_level(logging.DEBUG):
        assert run(
            "provider", "secret", "set", "llm", "claude", "api_key", stdin=f"{SECRET}\n"
        ) == 0

    captured = capsys.readouterr()
    assert "wrote secret for provider llm.claude api_key" in captured.out
    assert SECRET not in captured.out
    assert SECRET not in captured.err
    assert SECRET not in caplog.text


def test_a_secret_can_come_from_a_named_variable(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("provider", "set", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")
    monkeypatch.setenv("VINGA_TEST_KEY", SECRET)

    assert run(
        "provider", "secret", "set", "llm", "claude", "api_key", "--from-env", "VINGA_TEST_KEY"
    ) == 0

    monkeypatch.delenv("VINGA_TEST_KEY")
    assert run(
        "provider", "secret", "set", "llm", "claude", "api_key", "--from-env", "VINGA_TEST_KEY"
    ) == 1
    captured = capsys.readouterr()
    assert "--from-env names a variable that is not set" in captured.err
    assert SECRET not in captured.err


def test_a_variable_that_is_not_set_names_the_rule_and_never_the_name(
    run, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    """The name after `--from-env` is typed, and the mistake that
    produces this refusal most often is typing the secret itself there:
    one word early, and the value lands where the variable's name
    belongs. So the refusal names the rule, and the sentinel here is
    planted as the name (#289).

    The variable is not set under any spelling, which is what makes the
    refusal fire; that it is set nowhere is the point, since the secret
    typed by mistake is a value no environment holds.
    """
    run("provider", "set", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")
    capsys.readouterr()

    with caplog.at_level(logging.DEBUG):
        assert run(
            "provider", "secret", "set", "llm", "claude", "api_key", "--from-env", SECRET
        ) == 1

    captured = capsys.readouterr()
    assert "--from-env names a variable that is not set" in captured.err
    assert SECRET not in captured.err
    assert SECRET not in captured.out
    assert SECRET not in _logged(caplog)


def test_that_refusal_carries_the_name_in_no_chain_either(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """White-box for the chain, the way the URL refusals are checked: a
    chain is not printed, and is reachable only from where the refusal
    is raised."""
    with pytest.raises(ConfigError) as caught:
        cli._read_secret(cli.Invocation(from_env=SECRET))

    assert SECRET not in _chain(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_an_interactive_terminal_is_read_without_echo(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A typed secret must not land in the scrollback, so a terminal is
    read through getpass rather than by reading stdin."""
    run("provider", "set", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")

    class Terminal(io.StringIO):
        def isatty(self) -> bool:
            return True

    asked: list[str] = []
    monkeypatch.setattr(sys, "stdin", Terminal("this is never read\n"))
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: asked.append(prompt) or SECRET)

    assert run("provider", "secret", "set", "llm", "claude", "api_key") == 0

    assert asked, "the terminal was read without getpass"
    assert SECRET not in capsys.readouterr().out


def test_an_empty_secret_is_refused(run, capsys: pytest.CaptureFixture[str]) -> None:
    run("provider", "set", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")

    assert run("provider", "secret", "set", "llm", "claude", "api_key", stdin="\n") == 1
    assert "empty" in capsys.readouterr().err


def test_show_and_list_mask_stored_secrets_and_mark_what_they_shadow(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    run(
        "provider", "set", "llm", "claude",
        "-f",
        "-",
        stdin="type: anthropic\nmodel: m\napi_key_env: ANTHROPIC_API_KEY\n",
    )
    run(
        "mcp-server", "set", "weather",
        "-f",
        "-",
        stdin="transport: streamable_http\nurl: https://example.invalid/mcp\n",
    )
    run("provider", "secret", "set", "llm", "claude", "api_key", stdin=SECRET)
    run("mcp-server", "secret", "set", "weather", "headers.Authorization", stdin=OTHER_SECRET)
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

    run("provider", "show", "llm", "claude")
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
        assert run("provider", "set", "llm", "claude", "-f", "-", stdin=fragment) == 1

    captured = capsys.readouterr()
    assert "name of an environment variable" in captured.err
    assert SECRET not in captured.err
    assert SECRET not in captured.out
    assert SECRET not in caplog.text
    assert "Traceback" not in captured.err

    # And nothing was written: the entity does not exist.
    assert run("provider", "show", "llm", "claude") == 1
    assert capsys.readouterr().err.startswith("providers:")


def test_an_mcp_reference_shows_and_anything_else_in_its_place_does_not(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The model already requires a $VAR for a secret-bearing key, so a
    valid entry displays exactly as it was written; the mask is what
    covers a value that got in another way."""
    run(
        "mcp-server", "set", "weather",
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

    run("mcp-server", "show", "weather")
    shown = capsys.readouterr().out
    assert "$WEATHER_TOKEN" in shown
    # A key that carries no secret keeps its literal value: masking it
    # would hide configuration for nothing.
    assert "eu" in shown


def test_deleting_an_entity_takes_its_secrets_with_it(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    run("provider", "set", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")
    run("provider", "secret", "set", "llm", "claude", "api_key", stdin=SECRET)
    capsys.readouterr()

    assert run("provider", "delete", "llm", "claude") == 0
    capsys.readouterr()

    run("list")
    listed = capsys.readouterr().out
    assert "claude" not in listed
    assert "[secrets:" not in listed


def test_a_secret_can_be_cleared(run, capsys: pytest.CaptureFixture[str]) -> None:
    run("provider", "set", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")
    run("provider", "secret", "set", "llm", "claude", "api_key", stdin=SECRET)
    capsys.readouterr()

    assert run("provider", "secret", "clear", "llm", "claude", "api_key") == 0
    run("list")
    assert "[secrets:" not in capsys.readouterr().out


def test_the_cli_still_works_when_the_key_is_missing_or_wrong(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The recovery case the boot check is deliberately kept out of
    opening the database for: reading, deleting and replacing all treat
    ciphertext as opaque, and only storing a new secret needs a key."""
    run("provider", "set", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")
    run("provider", "secret", "set", "llm", "claude", "api_key", stdin=SECRET)
    capsys.readouterr()

    monkeypatch.delenv(MASTER_KEY_ENV)
    assert run("list") == 0
    assert "[secrets: api_key]" in capsys.readouterr().out
    assert run("provider", "show", "llm", "claude") == 0
    shown = capsys.readouterr().out
    assert MASK in shown
    assert SECRET not in shown

    assert run("provider", "secret", "set", "llm", "claude", "api_key", stdin=SECRET) == 1
    assert MASTER_KEY_ENV in capsys.readouterr().err

    # And the unreadable secret can be removed and the entity replaced.
    assert run("provider", "secret", "clear", "llm", "claude", "api_key") == 0
    replacement = "type: anthropic\nmodel: n\n"
    assert run("provider", "set", "llm", "claude", "-f", "-", stdin=replacement) == 0


def test_an_unusable_key_names_its_position_and_not_its_material(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("provider", "set", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")
    capsys.readouterr()
    monkeypatch.setenv(MASTER_KEY_ENV, "not-a-fernet-key")

    assert run("provider", "secret", "set", "llm", "claude", "api_key", stdin=SECRET) == 1

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
        ("provider", "set", "llm", "claude", "-f", "-"),
        "type: anthropic\nmodel: m\n",
        ("provider", "secret", "set", "llm", "claude", PASTED),
        "providers",
    ),
    (
        ("mcp-server", "set", "home", "-f", "-"),
        "transport: stdio\ncommand: uvx\n",
        ("mcp-server", "secret", "set", "home", PASTED),
        "mcp_servers",
    ),
]


@pytest.mark.parametrize(("write", "fragment", "addressed", "section"), SLOTS)
def test_a_slot_that_is_not_a_credential_slot_is_refused_without_printing_it(
    run,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    write: tuple[str, ...],
    fragment: str,
    addressed: tuple[str, ...],
    section: str,
) -> None:
    """The slot half of a secret's address (#132).

    A secret set is the command a credential is pasted into, so a
    credential typed one argument early lands in the slot. The entity it
    is offered to exists here on purpose: the entity-miss refusal used
    to answer first, which meant this check was never the one under
    test. Neither the slot nor the secret behind it may be printed.
    """
    assert run(*write, stdin=fragment) == 0
    capsys.readouterr()

    with caplog.at_level(logging.DEBUG):
        assert run(*addressed, stdin=SECRET) == 1

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


# A stream that will not give the secret
#
# The three interactive reads in this grammar can each fail in ways no
# argument of theirs decides, and the secret's two are the ones a
# credential is being typed into. `EOFError` is what the no-echo prompt
# raises when the stream ends under it and is not an `OSError`; a stream
# that has gone is an `OSError`; bytes the terminal's encoding will not
# decode leave as a `UnicodeError`, which is a `ValueError` and not an
# `OSError` either. Each of them carries something, and a decoding
# failure carries the worst of it: the bytes somebody just typed.

FAILING_READS = [
    ("the prompt met the end of the stream", EOFError(f"reading {PASTED}")),
    ("the stream has gone", OSError(5, "Input/output error", PASTED)),
    (
        "bytes the terminal will not decode",
        UnicodeDecodeError("utf-8", SECRET.encode() + b"\xff", 41, 42, "invalid"),
    ),
    ("a value the reader refuses", ValueError(f"cannot read {SECRET}")),
]


class _FailingTerminal(io.IOBase):
    """A terminal whose read raises whatever it was built with."""

    def __init__(self, raised: BaseException) -> None:
        super().__init__()
        self._raised = raised

    def isatty(self) -> bool:
        return True

    def read(self, *args: object, **kwargs: object) -> str:
        raise self._raised

    def readline(self, *args: object, **kwargs: object) -> str:
        raise self._raised


class _FailingPipe(_FailingTerminal):
    """The same, through a pipe, which takes the other of the two
    reads."""

    def isatty(self) -> bool:
        return False


@pytest.mark.parametrize(
    ("through", "raised"),
    [
        (shape, raised)
        for shape in (_FailingTerminal, _FailingPipe)
        for _, raised in FAILING_READS
    ],
    ids=[
        f"{name} {what}"
        for name in ("at a terminal", "through a pipe")
        for what, _ in FAILING_READS
    ],
)
def test_a_secret_that_cannot_be_read_is_a_sentence_rather_than_a_traceback(
    run,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    through: type,
    raised: BaseException,
) -> None:
    """Both mechanisms, both failing, and one fixed sentence out of
    each. Nothing is stored, nothing is quoted, and neither the value
    the failure held nor a traceback reaches any of the four surfaces.
    """
    assert run(
        "provider", "set", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n"
    ) == 0
    capsys.readouterr()
    monkeypatch.setattr(sys, "stdin", through(raised))
    monkeypatch.setattr("getpass.getpass", lambda *_a, **_k: raised_by(raised))

    with caplog.at_level(logging.DEBUG):
        assert run("provider", "secret", "set", "llm", "claude", "api_key") == 1

    captured = capsys.readouterr()
    assert captured.err == cli.SECRET_UNREADABLE + "\n"
    assert captured.out == ""
    assert "Traceback" not in captured.err
    for sentinel in (SECRET, PASTED):
        assert sentinel not in captured.err + captured.out + _logged(caplog)


def raised_by(raised: BaseException) -> str:
    raise raised


@pytest.mark.parametrize(
    "raised", [raised for _, raised in FAILING_READS], ids=[what for what, _ in FAILING_READS]
)
def test_the_unreadable_secret_refusal_carries_nothing_on_its_chain(
    monkeypatch: pytest.MonkeyPatch, raised: BaseException
) -> None:
    """The half no assertion about a stream can make. A decoding failure
    retains the bytes it was given, and a prompt's end-of-file retains
    what it was reading from; neither is behind the sentence."""
    monkeypatch.setattr(sys, "stdin", _FailingPipe(raised))

    with pytest.raises(ConfigError) as caught:
        cli._read_secret(cli.Invocation())

    assert str(caught.value) == cli.SECRET_UNREADABLE
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    for sentinel in (SECRET, PASTED):
        assert sentinel not in _chain(caught.value)


def test_no_input_at_a_failing_terminal_never_reaches_the_read(
    run, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The path the flag opened, held to not reaching the boundary at
    all: what a terminal with prompting disabled answers is the empty
    secret, and a read that never happens cannot fail."""
    assert run(
        "provider", "set", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n"
    ) == 0
    capsys.readouterr()
    monkeypatch.setattr(sys, "stdin", _FailingTerminal(OSError(5, "gone", PASTED)))

    assert run("provider", "secret", "set", "llm", "claude", "api_key", "--no-input") == 1

    captured = capsys.readouterr()
    assert captured.err == cli.SECRET_EMPTY + "\n"
    assert PASTED not in captured.err
