"""The `vinga-server conversations` commands.

Two things are worth pinning. That `schema` really opens nothing: it is
run against a database directory that cannot exist, so a command that
touched the file would fail there rather than print. And that no refusal
repeats what was typed, which is the same rule the config group speaks
and the reason both parsers turn argparse's own usage errors into
ConfigErrors.
"""

import logging

import pytest

from vinga_server import logs
from vinga_server.conversations import cli

# Shaped like something that must not be echoed back, and planted where
# argparse would quote it: as the command word, and as an extra
# argument.
SENTINEL = "hunter2-not-a-real-credential-9f31c7"

# A directory that cannot be created, so a command that opened the
# database would fail here rather than print.
NOWHERE = "/nowhere/at/all"


@pytest.fixture
def run(monkeypatch: pytest.MonkeyPatch):
    """The group composes `server.database.dir` the way the server does,
    and nothing here reads it, so pointing it somewhere unopenable is
    both the whole of the setup and half of what is asserted."""
    monkeypatch.delenv("VINGA_CONFIG", raising=False)
    monkeypatch.setenv("VINGA_SERVER__DATABASE__DIR", NOWHERE)

    def _run(*argv: str) -> int:
        return cli.main(list(argv))

    return _run


def test_the_schema_command_prints_the_reference_and_opens_nothing(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("schema") == 0

    printed = capsys.readouterr().out
    assert printed.startswith("# Conversation store schema reference")
    assert "### `sessions`" in printed


def test_the_purge_command_is_gone(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The deletion asserted as a fact of the grammar rather than as an
    absence in this file.

    Every other case here would pass just as well with the subparser
    restored, because none of them types the word. This one does, with
    the selector it used to take, and requires the answer a word that is
    not a command gets: the group's own fixed sentence, exit 1, and
    nothing of what was typed echoed back into it.
    """
    assert run("purge", "--session", SENTINEL) == 1

    captured = capsys.readouterr()
    assert captured.err == (
        "that is not a command; expected one of: schema; "
        "run with --help for the grammar\n"
    )
    assert captured.out == ""
    assert SENTINEL not in captured.err


def test_the_group_help_offers_schema_and_nothing_else(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The other half of the same fact: what the group tells an operator
    it can do. A subparser restored without its selectors would still
    show up here."""
    with pytest.raises(SystemExit) as left:
        run("--help")

    assert left.value.code == 0
    printed = capsys.readouterr().out
    assert "{schema}" in printed
    assert "purge" not in printed
    assert "--session" not in printed
    assert "--device" not in printed
    assert "--before" not in printed


def test_a_mistake_in_the_grammar_leaves_by_the_same_door(
    run, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str],
) -> None:
    """argparse writes to stderr and exits 2 from inside parse_args,
    which would make an unknown command the one failure that bypasses
    the documented exit code. It also quotes what was typed: an
    unrecognized command comes back as `invalid choice: 'x'` and extra
    arguments come back verbatim, so the sentinel is planted as each in
    turn and hunted everywhere the command writes."""
    with caplog.at_level(logging.DEBUG):
        # As the command word, which argparse answers with invalid
        # choice.
        assert run(SENTINEL) == 1
        # As an extra argument, which it answers with unrecognized
        # arguments.
        assert run("schema", SENTINEL) == 1
        assert run() == 1

    captured = capsys.readouterr()
    rendered = caplog.text + "".join(
        logs.JsonFormatter().format(record) for record in caplog.records
    )
    assert SENTINEL not in captured.err
    assert SENTINEL not in captured.out
    assert SENTINEL not in rendered
    assert "Traceback" not in captured.err
    # And the refusal for a word that is not a command still says which
    # word is.
    assert "schema" in captured.err


def test_the_command_word_dispatches_to_this_group(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A word check in main rather than an argparse subparser, so adding
    the group cannot change how `vinga-server --config path` parses."""
    from vinga_server import main as entrypoint

    monkeypatch.setattr(
        entrypoint.sys, "argv", ["vinga-server", "conversations", "schema"]
    )
    monkeypatch.delenv("VINGA_CONFIG", raising=False)
    monkeypatch.setenv("VINGA_SERVER__DATABASE__DIR", NOWHERE)

    with pytest.raises(SystemExit) as left:
        entrypoint.main()

    assert left.value.code == 0
    assert capsys.readouterr().out.startswith("# Conversation store schema reference")
