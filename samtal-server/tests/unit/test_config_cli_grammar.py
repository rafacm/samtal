"""The grammar itself: what it accepts, what it says, and how it exits.

Every other config suite drives a command that parses. This one is about
the parse: a subcommand nobody has, a missing positional, an unknown
flag, an argument too many, and `--help`, which is the one invocation
that is not a failure.

The exit code is the contract under all of it. argparse exits 2 from
inside `parse_args`, which would make a typed command the single failure
that bypasses both the documented codes and the sanitized boundary, so
the group catches it and exits 1 like everything else. And a refusal for
an extra argument must not echo the argument: the mistake it covers is
typing the secret after the slot.

The two help-text assertions are here because the help is part of the
grammar rather than a rendering of an answer. Each pins a phrase an
operator looks for in the place they look first: the state a `status`
can report that they have never met before, and which of the two ways to
bind a board takes a MAC and which takes an activation code.
"""

from pathlib import Path

import pytest

from samtal_server.config import cli
from tests.support.config_cli import SECRET, runner


@pytest.fixture
def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """One command run the way the entry point runs it, against a server
    of this test's own."""
    return runner(tmp_path, monkeypatch)


def test_the_status_help_names_every_state_it_can_print() -> None:
    """`unused` is a state of its own and the one an operator has never
    met before, so leaving it out of the help would leave it out of the
    place they look first."""
    # Whitespace collapsed, because argparse wraps the line it is
    # printed on and where it wraps is not the contract.
    help_text = " ".join(cli._parser().format_help().split())

    assert "connected, down, or unused because no agent references it" in help_text


def test_the_two_ways_to_bind_a_board_say_which_is_which() -> None:
    """A pair a person picks wrongly once and then remembers wrongly,
    so each names what the other takes."""
    help_text = cli._parser().format_help()  # noqa: SLF001

    assert "by the MAC you already know" in help_text
    assert "showing this activation code" in help_text


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
