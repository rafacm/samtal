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

import logging
from pathlib import Path

import pytest

from tests.support.config_cli import SECRET, chain, runner
from tests.support.events import both_formats
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


# Where help leaves through, and what it leaves carrying
#
# `--help` is answered by Click, which asks its context to exit, and
# this group turns that request into the SystemExit(0) it has always
# left through. The turn happens after the handler rather than inside
# it, for the reason every refusal in `cli.py` is raised after its
# handler: an exception raised while another is being handled keeps that
# one on `__context__`, and here that one is a library exception holding
# the context it was raised from, which holds the argument list. That is
# where a secret typed as an argument would be.
#
# `raise ... from None` is not the fix and was the bug: it sets
# `__suppress_context__`, which stops a traceback being printed and
# leaves `__context__` exactly where it was, so the leak survives every
# assertion made about a stream.
HELPED = [("the group",), ("show",), ("set", "provider"), ("schema",)]


@pytest.mark.parametrize(
    "words", HELPED, ids=[" ".join(words) for words in HELPED]
)
def test_asking_for_help_carries_no_library_exception_with_it(
    run, capsys: pytest.CaptureFixture[str], words: tuple[str, ...]
) -> None:
    """At the root and at a leaf, and at the one group word that is also
    a command: help leaves through exit 0 with both chain slots empty,
    so nothing walking the chain finds a Typer exception behind it."""
    asked = () if words == ("the group",) else words

    with pytest.raises(SystemExit) as caught:
        run(*asked, "--help")

    assert caught.value.code == 0
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert capsys.readouterr().out.startswith("Usage: vinga-server config")


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
    run,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    argv: tuple[str, ...],
    sentence: str,
) -> None:
    """Every shape the boundary names, refused in this grammar's words.

    The sentence is this module's and the value is nowhere: not on
    stderr, where the refusal is printed, not on stdout, which a command
    that got this far has written nothing to, and not in the log, in
    either of the two renderings a deployment keeps it in. The log is
    asserted because "nowhere" is the claim: nothing here writes a
    record today, and a boundary that started logging what it refused
    would be exactly the change this is addressed to.
    """
    with caplog.at_level(logging.DEBUG):
        assert run(*argv) == 1, argv

    captured = capsys.readouterr()
    assert sentence in captured.err
    assert "run with --help for the grammar" in captured.err
    assert SECRET not in captured.err
    assert SECRET not in captured.out
    assert "Traceback" not in captured.err
    assert SECRET not in caplog.text
    assert SECRET not in both_formats(caplog)


def _refusal(argv: tuple[str, ...]) -> cli.ConfigError:
    """The refusal one command line earns, as the exception rather than
    as the sentence `main` prints.

    A deliberate reach past the entry point, and the only place the
    claim below can be made: `main` catches this exception by design and
    answers with a sentence and an exit code, so no caller-facing
    surface ever holds it, and what is being asserted is what a chain
    walker would find on it.
    """
    with pytest.raises(cli.ConfigError) as caught:
        cli._parsed(list(argv))
    return caught.value


@pytest.mark.parametrize(
    "argv", [argv for _, argv, _ in PLANTED], ids=[shape for shape, _, _ in PLANTED]
)
def test_a_refusal_carries_nothing_of_the_command_line_on_its_chain(
    run, argv: tuple[str, ...]
) -> None:
    """The half no assertion about a stream can make.

    A Click exception holds the context it was raised from and that
    context holds the argument list, so a refusal raised inside the
    handler would carry the whole command line on `__context__` while
    printing this grammar's own sentence. Both slots are empty, and
    nothing in the chain says the credential.
    """
    refusal = _refusal(argv)

    assert refusal.__cause__ is None
    assert refusal.__context__ is None
    assert SECRET not in chain(refusal)


# The shapes the real grammar cannot produce
#
# The boundary translates five Click classes and falls back for
# everything else, and two of those classes plus the fallback have no
# route through this grammar: nothing here is a typed choice, so Click
# never refuses a value, and nothing raises `BadArgumentUsage`. A branch
# that cannot be exercised is exactly the branch that leaks the day it
# becomes reachable, so these are driven at the boundary itself, with a
# credential in the message each of them would have carried.
#
# The base `UsageError` is reached through a subclass `cli` imports
# rather than through a second import of Typer's private copy of Click:
# one place breaking on a Typer upgrade is enough.
USAGE_ERROR = cli.NoSuchOption.__base__

CONSTRUCTED = [
    (
        "a value the command does not take",
        cli.BadParameter(f"Invalid value for 'STAGE': '{SECRET}' is not one of 'llm', 'asr'."),
        "an argument was given a value this command does not take",
    ),
    (
        "an argument in a shape it does not take",
        cli.BadArgumentUsage(f"Got unexpected extra arguments ({SECRET})"),
        "an argument was given in a shape this command does not take",
    ),
    (
        "a usage error whose words are new",
        USAGE_ERROR(f"Some later Click says something else about {SECRET}."),
        "the command line could not be parsed",
    ),
    (
        "a Click failure that is not a usage error",
        cli.ClickException(f"something else went wrong with {SECRET}"),
        "the command line could not be parsed",
    ),
]


@pytest.mark.parametrize(
    ("raised", "sentence"),
    [(raised, sentence) for _, raised, sentence in CONSTRUCTED],
    ids=[shape for shape, _, _ in CONSTRUCTED],
)
def test_a_shape_the_grammar_cannot_produce_is_translated_too(
    raised: Exception, sentence: str
) -> None:
    """The boundary's own answer, asked directly, because these four
    have no command line that produces them."""
    said = cli._usage_problem(raised)

    assert said == f"{sentence}; run with --help for the grammar"
    assert SECRET not in said


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


def _states_a_default(parameter) -> bool:
    """Whether this parameter is one whose default has to be written
    into its own description.

    An option that takes a value and is not required has a default, and
    not one of this grammar's is a value the library could print: two
    are resolution orders and one is a stream, and each is declared as
    the `None` that stands for not given, for which Typer prints
    nothing at all. So the description is the only place the default can
    be said, and saying it is not optional: `--from-env` unstated reads
    as an option with no alternative rather than as the one that
    replaces reading the secret from stdin.

    A flag is excluded, because a flag that is not given is a flag that
    is not given, and printing a default for it would be noise.
    """
    return (
        parameter.param_type_name == "option"
        and not parameter.required
        and not getattr(parameter, "is_flag", False)
    )


def _first_sentence(description: str | None) -> str:
    """The part of a field's description a help line carries.

    The rule is `docgen`'s, restated here the way the docgen suite
    restates it: a change to how much of a description the help carries
    should turn up as a failing test rather than as help that quietly
    says less. The descriptions are written so that the first sentence
    is the one that has to be there.
    """
    assert description, "an undescribed field is invisible in all three renderings at once"
    head, separator, _ = description.partition(". ")
    return head + separator.strip()


@pytest.mark.parametrize(
    "row", cli.COMMANDS, ids=[" ".join(row.words) for row in cli.COMMANDS]
)
def test_every_command_describes_every_parameter_it_declares(
    run, capsys: pytest.CaptureFixture[str], row
) -> None:
    """Nothing a command declares is missing from the page an operator
    reads: every parameter by the name it is typed under, every
    description it was given, one `[required]` for each parameter that
    has to be there, and a stated default for every option that takes a
    value and does not have to be given.

    That last one is the assertion `--from-env` needed and did not have:
    its default is to read the secret from stdin, which is behavior an
    operator has to know and which the library prints nothing about.
    """
    helped = printed_help(run, capsys, *row.words)
    declared = _leaf(row.words).params

    for parameter in declared:
        assert _typed(parameter) in helped, parameter.name
        if parameter.help:
            assert _said(parameter.help) in _said(helped), parameter.name
        if _states_a_default(parameter):
            assert "default:" in _said(parameter.help or ""), parameter.name

    assert helped.count("[required]") == sum(1 for one in declared if one.required)


@pytest.mark.parametrize(
    "kind", [row.words[1] for row in cli.COMMANDS if row.words[0] == "set"]
)
def test_a_set_help_lists_the_model_it_writes(
    run, capsys: pytest.CaptureFixture[str], kind: str
) -> None:
    """Every field of the model a fragment is validated against, with
    the type it holds, the value it has when the fragment leaves it out,
    and what it is for. Read off the model here and rendered from the
    model there, so a field added to a model appears in the help of the
    command that writes it without anyone remembering to put it there.

    All three, because all three are what a person writing a fragment
    has to know, and a type and a default with no sentence beside them
    say what a key holds without saying what it is.
    """
    helped = printed_help(run, capsys, "set", kind)
    model = docgen.entity(kind).model

    for name, info in model.model_fields.items():
        assert name in helped, name
        assert _said(docgen.type_name(info.annotation)) in _said(helped), name
        given = docgen.default(info)
        held = "(required)" if given == "required" else f"(default: {given})"
        assert _said(held) in _said(helped), name
        assert _said(_first_sentence(info.description)) in _said(helped), name


# The two positions a global option is given in
#
# `--config`, `--api-url` and `--local` are accepted before the command
# word and after it, because both readings are natural: `vinga-server
# --config path` is how the server takes it, and options after their
# subcommand is how everything else does. What makes that subtle is the
# merge: a value given before the command must survive a command that
# was not given one, which argparse spelled as `default=SUPPRESS` and
# the Typer layer spells as a per-position fold.
#
# Stated separately for the two positions the grammar really has. At the
# root all of them are accepted before every command. At the leaf the
# exclusions bind, and they are exclusions with reasons: the three
# documentation commands render the models and the routes, so they open
# no database, reach no server and take none of the three, and `ota-url`
# derives a string from the file half and contacts nothing, so it takes
# `--config` and nothing that addresses an API.
#
# Parameterized over the options the root declares rather than over a
# list of them, so a fourth global option inherits the matrix by being
# declared.

ROOT_OPTIONS = frozenset(
    spelling
    for parameter in cli.command().params
    for spelling in parameter.opts
    if spelling != "--help"
)

# What each command takes of them where it is not all of them.
LEAF_EXCLUSIONS: dict[tuple[str, ...], frozenset[str]] = {
    ("ota-url",): frozenset({"--config"}),
    ("schema",): frozenset(),
    ("reference",): frozenset(),
    ("openapi",): frozenset(),
}


def test_the_root_position_takes_every_global_option() -> None:
    """Read off the root rather than listed, so a fourth global option
    joins the matrix below by being declared. The three are named here
    because they are what exists, and a fourth is a deliberate edit to
    this line rather than a silent widening."""
    assert ROOT_OPTIONS == {"--config", "--api-url", "--local"}


@pytest.mark.parametrize(
    "row", cli.COMMANDS, ids=[" ".join(row.words) for row in cli.COMMANDS]
)
def test_every_command_takes_the_global_options_in_its_own_position(row) -> None:
    """The leaf half: a command declares its own copy of each global
    option the exclusions leave it, and of no other."""
    declared = {
        spelling for parameter in _leaf(row.words).params for spelling in parameter.opts
    }

    assert declared & ROOT_OPTIONS == LEAF_EXCLUSIONS.get(row.words, ROOT_OPTIONS)


def _positions(option: str, tmp_path: Path) -> tuple[str, str, str, str]:
    """Two values for one option, and the address a command reaches when
    each of them wins.

    `--config` names a file whose `server.port` is what the default
    address is built from, and `--api-url` is the address, so both are
    read back the same way: through the base URL the client was built
    with.
    """
    if option == "--config":
        return (
            _configured(tmp_path, 9101),
            _configured(tmp_path, 9102),
            "http://127.0.0.1:9101/api",
            "http://127.0.0.1:9102/api",
        )
    return (
        "http://127.0.0.1:9101/api",
        "http://127.0.0.1:9102/api",
        "http://127.0.0.1:9101/api",
        "http://127.0.0.1:9102/api",
    )


def _configured(tmp_path: Path, port: int) -> str:
    path = tmp_path / f"config-{port}.yaml"
    path.write_text(f"server:\n  port: {port}\n", encoding="utf-8")
    return str(path)


# The two that carry a value, which are the two that can conflict.
# `--local` is presence-only and has no value to disagree about, so its
# cases are the two below this pair.
VALUE_OPTIONS = ("--config", "--api-url")


@pytest.mark.parametrize("option", VALUE_OPTIONS)
def test_a_value_before_the_command_survives_a_command_that_names_none(
    run, tmp_path: Path, option: str
) -> None:
    """The one the merge exists for: without it the command's own empty
    copy would overwrite what came before it, and every invocation that
    named a file up front would read the default one."""
    before, _, reached, _ = _positions(option, tmp_path)

    assert run(option, before, "list") == 0

    assert run.reached[-1] == reached


@pytest.mark.parametrize("option", VALUE_OPTIONS)
def test_a_value_after_the_command_is_taken_on_its_own(
    run, tmp_path: Path, option: str
) -> None:
    _, after, _, reached = _positions(option, tmp_path)

    assert run("list", option, after) == 0

    assert run.reached[-1] == reached


@pytest.mark.parametrize("option", VALUE_OPTIONS)
def test_a_value_after_the_command_beats_one_before_it(
    run, tmp_path: Path, option: str
) -> None:
    """The nearer position wins, which is the half nothing used to
    prove: the two coverages that existed were both of a flag."""
    before, after, _, reached = _positions(option, tmp_path)

    assert run(option, before, "list", option, after) == 0

    assert run.reached[-1] == reached


def test_the_flag_before_the_command_survives_a_command_that_does_not_repeat_it(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--local` is presence-only, so what it has instead of a conflict
    is this: a flag that is not there says nothing, and cannot unsay a
    flag that is. Read back through the server not being reached, which
    is what the break-glass path is."""
    assert run("--local", "show") == 0

    assert run.reached == []
    assert "bypassing the configuration API" in capsys.readouterr().err


def test_the_flag_in_both_positions_is_the_same_flag(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """And the other half of presence-only: given twice it is given, and
    the preamble is printed once rather than once per position."""
    assert run("--local", "show", "--local") == 0

    assert run.reached == []
    assert capsys.readouterr().err.count("bypassing the configuration API") == 1
