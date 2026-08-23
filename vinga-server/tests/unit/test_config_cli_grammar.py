"""The grammar itself: what it accepts, what it says, and how it exits.

Every other config suite drives a command that parses. This one is about
the parse: a subcommand nobody has, a missing positional, an unknown
flag, an argument too many, and `--help`, which is the one invocation
that is not a failure.

The exit code is the contract under all of it. Click's own error path
prints the mistake and exits 2, which would make a typed command the
single failure that bypasses both the documented codes and the sanitized
boundary, so the group runs Click with that path off and answers every
usage error itself, exiting 1 like everything else. And a refusal must
never echo what was typed: Click quotes an unknown command, repeats a
bad value and offers a did-you-mean built from an unknown option, and
the mistake all of that covers is typing the secret after the slot.

The two help-text assertions are here because the help is part of the
grammar rather than a rendering of an answer. Each pins a phrase an
operator looks for in the place they look first: the state a `status`
can report that they have never met before, and which of the two ways to
bind a board takes a MAC and which takes an activation code.
"""

from pathlib import Path

import pytest

from tests.support.config_cli import SECRET, runner
from vinga_server.config import cli, docgen


@pytest.fixture
def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """One command run the way the entry point runs it, against a server
    of this test's own."""
    return runner(tmp_path, monkeypatch)


def printed_help(run, capsys: pytest.CaptureFixture[str], *words: str) -> str:
    """The help an operator gets, read where they read it: by asking one
    command of the grammar for it, which is the only way they ever will.
    Whitespace collapsed, because the formatter wraps the line it is
    printed on and where it wraps is not the contract."""
    with pytest.raises(SystemExit) as caught:
        run(*words, "--help")
    assert caught.value.code == 0
    return " ".join(capsys.readouterr().out.split())


def test_the_status_help_names_every_state_it_can_print(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """`unused` is a state of its own and the one an operator has never
    met before, so leaving it out of the help would leave it out of the
    place they look first."""
    help_text = printed_help(run, capsys)

    assert "connected, down, or unused because no agent references it" in help_text


def test_the_two_ways_to_bind_a_board_say_which_is_which(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """A pair a person picks wrongly once and then remembers wrongly,
    so each names what the other takes."""
    help_text = printed_help(run, capsys)

    assert "by the MAC you already know" in help_text
    assert "showing this activation code" in help_text


def test_a_mistake_in_the_grammar_exits_one_like_every_other_failure(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """Click's standalone mode exits 2 from inside the parse, which
    would make an unknown command the one failure that bypasses the
    documented exit codes and the sanitized boundary."""
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
    which is where Click would otherwise echo it back."""
    assert run("set-secret", "provider", "llm", "claude", "api_key", SECRET) == 1

    captured = capsys.readouterr()
    assert "unrecognized extra arguments" in captured.err
    assert SECRET not in captured.err
    assert SECRET not in captured.out


def test_asking_for_help_is_not_a_failure(run, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        run("--help")

    assert caught.value.code == 0
    assert "Usage: vinga-server config" in capsys.readouterr().out


# One case per shape the usage boundary names
#
# Click states a usage mistake two ways: as a subclass of `UsageError`,
# which the boundary translates by class, and as the base class with a
# sentence of its own, which it tells apart by Click's fixed words. Both
# readings exist so that none of Click's own text reaches a stream, and
# what makes that worth machinery is that three of these shapes quote
# what was typed: the extra argument, the unknown command, and the
# unknown option with a did-you-mean built from it.
#
# Each case therefore plants a credential where the mistake would put
# one. Two of the shapes carry no value of their own (an option missing
# its value names the option, a missing argument names the metavar), and
# they still get a credential-shaped word on the line, because what is
# being asserted is that nothing typed reaches a stream at all.
#
# `BadParameter` is named by the boundary and has no case here: no
# argument of this grammar is a typed choice, so Click has nothing to
# refuse a value for. It is translated anyway, because the shape being
# unreachable today is not a reason for it to leak the day it is not.
PLANTED: list[tuple[str, tuple[str, ...], str]] = [
    (
        "an argument too many",
        ("set-secret", "provider", "llm", "claude", "api_key", SECRET),
        "unrecognized extra arguments",
    ),
    ("a command that is not one", (SECRET,), "that is not a command"),
    (
        "an option that is not one",
        ("list", f"--{SECRET}"),
        "that is not an option of this command",
    ),
    (
        "an option with no value",
        ("set-secret", "provider", "llm", SECRET, "api_key", "--from-env"),
        "an option was given without its value",
    ),
    (
        "an argument that is missing",
        ("set-secret", "provider", "llm", SECRET),
        "a required argument is missing",
    ),
    ("a command that is missing", ("set",), "a command is missing"),
]


@pytest.mark.parametrize(
    ("argv", "sentence"),
    [(argv, sentence) for _, argv, sentence in PLANTED],
    ids=[shape for shape, _, _ in PLANTED],
)
def test_a_usage_mistake_says_nothing_of_what_was_typed(
    run, capsys: pytest.CaptureFixture[str], argv: tuple[str, ...], sentence: str
) -> None:
    """Every shape the boundary names, refused in this grammar's words.

    The sentence is this module's and the value is nowhere: not on
    stderr, where the refusal is printed, and not on stdout, which a
    command that got this far has written nothing to.
    """
    assert run(*argv) == 1, argv

    captured = capsys.readouterr()
    assert sentence in captured.err
    assert "run with --help for the grammar" in captured.err
    assert SECRET not in captured.err
    assert SECRET not in captured.out
    assert "Traceback" not in captured.err


# The help, held to what generates it
#
# The claim the rebuild makes about help is that it is generated rather
# than written: an option's description is the declaration's, and a
# fragment field's line is the model's. Both are claims a reader cannot
# check by reading, because what makes them true is that nobody typed
# the text twice, so they are enumerated over the whole command tree
# instead.


def _leaf(words: tuple[str, ...]):
    """One command of the grammar, by the words that name it. Walked
    down the tree the entry point runs, so what is inspected is what an
    operator reaches rather than a second construction of it."""
    found = cli.command()
    for word in words:
        found = found.commands[word]
    return found


def _said(text: str) -> str:
    """One string with every space taken out, which is how a sentence in
    the help is compared with the sentence it was declared as.

    The formatter wraps, and where it wraps depends on the width of the
    terminal it is printed to, so no run of spaces in the output is the
    contract. It also breaks a word at a hyphen, which collapsing
    whitespace would leave as `set- secret`, and that is the shape this
    exists for rather than the ordinary wrap.
    """
    return "".join(text.split())


def _typed(parameter) -> str:
    """The string an operator sees a parameter as: its longest spelling
    for an option, the metavar it is written under for an argument."""
    if parameter.param_type_name == "option":
        return max(parameter.opts, key=len)
    return str(parameter.metavar)


@pytest.mark.parametrize(
    "row", cli.COMMANDS, ids=[" ".join(row.words) for row in cli.COMMANDS]
)
def test_every_command_describes_every_parameter_it_declares(
    run, capsys: pytest.CaptureFixture[str], row
) -> None:
    """Nothing a command declares is missing from the page an operator
    reads: every parameter by the name it is typed under, every
    description it was given, and one `[required]` for each parameter
    that has to be there.

    The two options whose default is a resolution order rather than a
    value carry it in their description, which is where a default that
    is not a literal has to live; `--local` has no default worth
    printing, since a flag that is not given is a flag that is not
    given.
    """
    helped = printed_help(run, capsys, *row.words)
    declared = _leaf(row.words).params

    for parameter in declared:
        assert _typed(parameter) in helped, parameter.name
        if parameter.help:
            assert _said(parameter.help) in _said(helped), parameter.name

    assert helped.count("[required]") == sum(1 for one in declared if one.required)


@pytest.mark.parametrize(
    "kind", [row.words[1] for row in cli.COMMANDS if row.words[0] == "set"]
)
def test_a_set_help_lists_the_model_it_writes(
    run, capsys: pytest.CaptureFixture[str], kind: str
) -> None:
    """Every field of the model a fragment is validated against, with
    the type it holds and the value it has when the fragment leaves it
    out. Read off the model here and rendered from the model there, so a
    field added to a model appears in the help of the command that
    writes it without anyone remembering to put it there.
    """
    helped = printed_help(run, capsys, "set", kind)
    model = docgen.entity(kind).model

    for name, info in model.model_fields.items():
        assert name in helped, name
        assert _said(docgen.type_name(info.annotation)) in _said(helped), name
        given = docgen.default(info)
        held = "(required)" if given == "required" else f"(default: {given})"
        assert _said(held) in _said(helped), name
