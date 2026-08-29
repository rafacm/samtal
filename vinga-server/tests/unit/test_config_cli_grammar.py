"""The grammar itself: what it accepts, what it says, and how it exits.

Every other config suite drives a command that parses. This one is about
the parse: a subcommand nobody has, a missing positional, an unknown
flag, an argument too many, a bare invocation that named no command at
all, a word the grammar used to have, and `--help`, which is the one
invocation that is not a failure.

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

import io
import logging
from importlib import metadata
from pathlib import Path

import pytest
from typer._click.core import Context

from tests.support.config_cli import SECRET, chain, logged, runner
from tests.support.events import both_formats
from vinga_server.config import cli, docgen, entities


@pytest.fixture
def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """One command run the way the entry point runs it, against a server
    of this test's own."""
    return runner(monkeypatch)


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
    place they look first.

    Read off the MCP servers' own page since #341, which is where the
    listing that carries this line now is.
    """
    help_text = printed_help(run, capsys, "mcp-server")

    assert "connected, down, or unused because no agent references it" in help_text


def test_the_two_ways_to_bind_a_board_say_which_is_which(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """A pair a person picks wrongly once and then remembers wrongly,
    so each names what the other takes.

    They sit on two pages now rather than one, which is half the fix:
    binding by a MAC is a verb of the board and claiming a code is a
    verb of the boards that are waiting. The help text is the other
    half, and it still points at the sibling from each side.
    """
    board = printed_help(run, capsys, "device")
    waiting = printed_help(run, capsys, "device", "pending")

    assert "by the MAC you already know" in board
    assert "showing this activation code" in waiting
    assert "use device bind when you know the MAC instead" in waiting


def test_a_mistake_in_the_grammar_exits_one_like_every_other_failure(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """Click's standalone mode exits 2 from inside the parse, which
    would make an unknown command the one failure that bypasses the
    documented exit codes and the sanitized boundary."""
    for argv in (
        ("nonsense",),
        (),
        ("provider", "set", "llm"),
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
    assert run("provider", "secret", "set", "llm", "claude", "api_key", SECRET) == 1

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
HELPED = [("the group",), ("provider",), ("provider", "secret"), ("schema",)]


@pytest.mark.parametrize(
    "words", HELPED, ids=[" ".join(words) for words in HELPED]
)
def test_asking_for_help_carries_no_library_exception_with_it(
    run, capsys: pytest.CaptureFixture[str], words: tuple[str, ...]
) -> None:
    """At the root, at a noun, at a sub-noun and at a leaf: help leaves
    through exit 0 with both chain slots empty, so nothing walking the
    chain finds a Typer exception behind it."""
    asked = () if words == ("the group",) else words

    with pytest.raises(SystemExit) as caught:
        run(*asked, "--help")

    assert caught.value.code == 0
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert capsys.readouterr().out.startswith("Usage: vinga-server config")


# A bare invocation, at the root and at every noun
#
# Arriving with no command is not a completed command, so it exits 1
# like every other one of those; what it gets instead of a sentence is
# the page it was one word short of, because the reader is not making a
# mistake about the grammar, they are asking to see it. The page goes to
# stderr, since stdout is data and this invocation produced none, and
# `--help` above keeps stdout and 0.
#
# Every group of the tree, nested ones included, because the page has to
# be the one the reader stopped at rather than the root's, and what
# makes it so is that each group raises from its own context. A group
# whose parent's page were printed instead would list commands the words
# already typed cannot reach.
BARE: list[tuple[str, ...]] = [(), *sorted(cli.GROUPS)]


def _bare_id(path: tuple[str, ...]) -> str:
    return " ".join(path) if path else "the group"


@pytest.mark.parametrize("path", BARE, ids=[_bare_id(path) for path in BARE])
def test_a_bare_invocation_prints_its_own_help_and_exits_one(
    run, capsys: pytest.CaptureFixture[str], path: tuple[str, ...]
) -> None:
    """Exit 1, the page on stderr, and nothing at all on stdout."""
    assert run(*path) == 1, path

    captured = capsys.readouterr()
    assert captured.out == "", path
    assert captured.err.startswith(
        " ".join(["Usage:", cli.DISPATCHED, *path]) + " [OPTIONS] COMMAND [ARGS]..."
    ), path
    assert "Commands:" in captured.err, path
    assert "Traceback" not in captured.err, path


@pytest.mark.parametrize("path", BARE, ids=[_bare_id(path) for path in BARE])
def test_a_bare_invocation_carries_nothing_on_its_chain(path: tuple[str, ...]) -> None:
    """The half no assertion about a stream can make, and the reason
    this answer is raised after the handler rather than inside it: a
    Click exception holds the context it was raised from and that
    context holds the argument list."""
    refusal = _refusal(path)

    assert refusal.__cause__ is None, path
    assert refusal.__context__ is None, path


# And one of them with a credential in the place a credential really can
# be typed. `--api-url` is a URL and a URL can carry a query, so an
# operator who has been handed an addressed endpoint pastes the whole
# thing; the option is accepted before the command word, so it is
# accepted in front of a group with no verb after it, which is exactly
# the invocation this page is printed for.
ADDRESSED = f"https://vinga.example/api?token={SECRET}"


@pytest.mark.parametrize("path", [("provider",), ("device", "pending")], ids=[
    "a noun", "a sub-noun"
])
def test_a_bare_invocation_says_nothing_of_the_address_it_was_given(
    run,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    path: tuple[str, ...],
) -> None:
    """The page is generated from the tree and the invocation reaches
    none of it: not the page, not stdout, and not the log in either of
    the two renderings a deployment keeps it in."""
    with caplog.at_level(logging.DEBUG):
        assert run("--api-url", ADDRESSED, *path) == 1

    captured = capsys.readouterr()
    assert "Commands:" in captured.err
    assert SECRET not in captured.err
    assert SECRET not in captured.out
    assert SECRET not in caplog.text
    assert SECRET not in both_formats(caplog)
    assert SECRET not in chain(_refusal(("--api-url", ADDRESSED, *path)))


# And the same invocation, typed by somebody trying to be it
#
# The page is chosen by class: the group raises `NoArgsIsHelpError` and
# nothing else in this grammar does. The reading it replaced was
# Click's wording, "Missing command", looked for in the message, and the
# trouble with reading a wording is that a wording is something a caller
# can type. Both of these put that exact phrase where Click composes it
# into a sentence of its own, and each is owed the fixed refusal for the
# mistake it really is rather than a page.
#
# Not a leak of a credential, so the sentinel here is the marker itself:
# what would go wrong is a refusal turning into a help page, which tells
# a reader their typo was a command they merely under-specified.
MARKER = "Missing command"

COLLIDING = [
    ("a command named like the marker", (MARKER,), "that is not a command"),
    (
        "an argument that says it",
        ("list", MARKER),
        "unrecognized extra arguments",
    ),
]


@pytest.mark.parametrize(
    ("argv", "sentence"),
    [(argv, sentence) for _, argv, sentence in COLLIDING],
    ids=[shape for shape, _, _ in COLLIDING],
)
def test_a_command_line_cannot_type_its_way_to_the_help_page(
    run, capsys: pytest.CaptureFixture[str], argv: tuple[str, ...], sentence: str
) -> None:
    """One sentence, and no page anywhere in it."""
    assert run(*argv) == 1, argv

    captured = capsys.readouterr()
    assert sentence in captured.err, argv
    assert "run with --help for the grammar" in captured.err, argv
    assert "Commands:" not in captured.err, argv
    assert "Usage:" not in captured.err, argv
    assert captured.out == "", argv


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
        ("provider", "secret", "set", "llm", "claude", "api_key", SECRET),
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
        ("provider", "secret", "set", "llm", SECRET, "api_key", "--from-env"),
        "an option was given without its value",
    ),
    (
        "an argument that is missing",
        ("provider", "secret", "set", "llm", SECRET),
        "a required argument is missing",
    ),
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
        cli._parsed(list(argv), cli.DISPATCHED)
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
# One of them used to be reachable and is not any more. A group left
# without a verb is answered with its own page above, off the context
# the mistake carries; the sentence stays for the same mistake with no
# context on it, which is a shape no command line produces and which
# would otherwise fall to the vague fallback.
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
        "a group with no command and no context",
        USAGE_ERROR(f"Missing command. {SECRET}"),
        "a command is missing",
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


def _page(words: tuple[str, ...]) -> str:
    """One command's help page, rendered the way the committed
    reference renders it: a stated width and no color, so what is read
    here does not depend on the terminal."""
    shape = cli.command()
    context = Context(
        shape,
        info_name=cli.PROGRAM,
        terminal_width=80,
        max_content_width=80,
        color=False,
        help_option_names=cli.HELP_OPTION_NAMES,
    )
    # Down the tree with the contexts chained, which is what the
    # committed reference does and what carries the help spellings from
    # the root's settings to every page below it.
    for word in words:
        shape = shape.commands[word]
        context = Context(
            shape,
            info_name=word,
            parent=context,
            terminal_width=80,
            max_content_width=80,
            color=False,
        )
    return shape.get_help(context)


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


# The rows that write an entity from a fragment
#
# Selected by the verb being the LAST word and the kind naming a
# descriptor, which is what the noun-first turn made of them. Selecting
# on the FIRST word was what these two parametrizations did before, and
# after the turn no row has `set` first: pytest built zero cases, and
# both tests would have passed with every field and the credential
# warning gone from every page. A parametrization that can empty itself
# is a test that can stop testing without failing, so the count is
# asserted below rather than assumed.
WRITING_KINDS = [
    row.kind
    for row in cli.COMMANDS
    if row.words == (row.kind, "set") and row.kind in set(docgen.entity_names())
]


def test_every_kind_that_takes_a_fragment_has_a_case() -> None:
    """The guard on the two parametrizations under this: five kinds
    write from a fragment, and a selection that found none of them would
    make both of them vacuous rather than red."""
    assert WRITING_KINDS
    assert sorted(WRITING_KINDS) == sorted(kind.name for kind in entities.ENTITIES)


@pytest.mark.parametrize("kind", WRITING_KINDS)
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
    helped = printed_help(run, capsys, kind, "set")
    model = docgen.entity(kind).model

    for name, info in model.model_fields.items():
        assert name in helped, name
        assert _said(docgen.type_name(info.annotation)) in _said(helped), name
        given = docgen.default(info)
        held = "(required)" if given == "required" else f"(default: {given})"
        assert _said(held) in _said(helped), name
        assert _said(_first_sentence(info.description)) in _said(helped), name


@pytest.mark.parametrize("kind", WRITING_KINDS)
def test_a_set_help_says_a_credential_is_never_one_of_its_arguments(
    run, capsys: pytest.CaptureFixture[str], kind: str
) -> None:
    """The store already refuses a plaintext credential by the shape of
    the key it was written under, whichever way the entity was written.
    What the help adds is the reason an inline value is the wrong place
    for one even where the key would have been accepted, which is that
    an argument lands in shell history and in the process list, and it
    is on every `set` page because every one of them takes pairs."""
    helped = printed_help(run, capsys, kind, "set")

    assert _said(cli.SECRET_NOT_A_PAIR) in _said(helped)
    assert _said(f"{cli.PROGRAM} <kind> secret set") in _said(helped)


# The two positions a global option is given in
#
# `--config` and `--api-url` are accepted before the command word and
# after it, because both readings are natural: `vinga-server --config
# path` is how the server takes it, and options after their subcommand
# is how everything else does. What makes that subtle is the merge: a
# value given before the command must survive a command that was not
# given one, which argparse spelled as `default=SUPPRESS` and the Typer
# layer spells as a per-position fold.
#
# Stated separately for the two positions the grammar really has. At the
# root all of them are accepted before every command. At the leaf the
# exclusions bind, and they are exclusions with reasons: the four
# documentation commands render the models, the routes and the command
# tree, so they open no database, reach no server and take none of the
# three, and `ota-url`
# derives a string from the file half and contacts nothing, so it takes
# `--config` and nothing that addresses an API.
#
# Parameterized over the options the root declares rather than over a
# list of them, so a third global option inherits the matrix by being
# declared.

# The two the root declares that no command may declare, each with its
# reason: help is Click's own and is on every page of the tree by
# construction, and `--version` is about the installed artifact rather
# than about this invocation, so a command has nothing to do with it.
ROOT_ONLY = frozenset({"-h", "--help", "--version"})

ROOT_OPTIONS = frozenset(
    spelling
    for parameter in cli.command().params
    for spelling in parameter.opts
    if spelling not in ROOT_ONLY
)

# What each command takes of them where it is not all of them.
LEAF_EXCLUSIONS: dict[tuple[str, ...], frozenset[str]] = {
    ("ota-url",): frozenset({"--config"}),
    ("schema",): frozenset(),
    ("reference",): frozenset(),
    ("openapi",): frozenset(),
    ("cli-reference",): frozenset(),
}


def test_the_root_position_takes_every_global_option() -> None:
    """Read off the root rather than listed, so a fifth global option
    joins the matrix below by being declared. The four are named here
    because they are what exists, and a fifth is a deliberate edit to
    this line rather than a silent widening."""
    assert ROOT_OPTIONS == {"--config", "--api-url", "--force", "--no-input"}


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
    with. A third global option would have no answer here, and says so
    by failing rather than by quietly taking another option's.
    """
    if option == "--config":
        return (
            _configured(tmp_path, 9101),
            _configured(tmp_path, 9102),
            "http://127.0.0.1:9101/api",
            "http://127.0.0.1:9102/api",
        )
    assert option == "--api-url", option
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


# The globals split two ways, because a value and a flag can be given
# wrongly in different ways. An option carrying a value can conflict
# with a copy of itself in the other position; a flag cannot, since its
# two positions can only agree, and the conflict worth checking for the
# two flags is with each other, which
# `test_config_cli_confirmation.py` holds.
#
# Both halves are derived from what the root declares rather than listed
# a second time, so a fifth global option inherits whichever matrix its
# shape puts it in.
FLAG_OPTIONS = tuple(
    sorted(
        spelling
        for parameter in cli.command().params
        for spelling in parameter.opts
        if spelling not in ROOT_ONLY and getattr(parameter, "is_flag", False)
    )
)

VALUE_OPTIONS = tuple(sorted(ROOT_OPTIONS - set(FLAG_OPTIONS)))


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


# The same three positions for the two flags
#
# What a flag says is read where it is acted on rather than off the
# client it built, because a flag builds no client: at a terminal, a
# destructive verb either asks, proceeds without asking, or refuses, and
# which of those it does is the whole of what these two options decide.


class _Terminal(io.StringIO):
    """A stdin that says it is a terminal, which is what the
    confirmation branches on."""

    def isatty(self) -> bool:
        return True


def _flag_answer(option: str) -> int:
    """What one flag makes a destructive verb answer at a terminal.

    A third flag would have no answer here, and says so by failing
    rather than by quietly taking another flag's.
    """
    if option == "--force":
        return 0
    assert option == "--no-input", option
    return 1


def _a_deletable_agent(run) -> None:
    assert run("agent", "set", "kids", "-f", "-", stdin="prompt: You are kids.\n") == 0


@pytest.mark.parametrize("option", FLAG_OPTIONS)
def test_a_flag_before_the_command_survives_a_command_that_names_none(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], option: str
) -> None:
    """The merge, for the half that is a boolean: an absent copy at the
    command position must not overwrite what the root position said,
    which an ordinary boolean default would do by arriving as False."""
    _a_deletable_agent(run)
    capsys.readouterr()
    monkeypatch.setattr("sys.stdin", _Terminal("y\n"))

    assert run(option, "agent", "delete", "kids") == _flag_answer(option)


@pytest.mark.parametrize("option", FLAG_OPTIONS)
def test_a_flag_after_the_command_is_taken_on_its_own(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], option: str
) -> None:
    _a_deletable_agent(run)
    capsys.readouterr()
    monkeypatch.setattr("sys.stdin", _Terminal("y\n"))

    assert run("agent", "delete", "kids", option) == _flag_answer(option)


# What every description in the tree is
#
# One lowercase sentence with no full stop in it, which is the rule
# three of the four audited guides state and the one the committed help
# pages are diffed byte for byte against. Held over `GROUPS` and
# `COMMANDS` together, because a group's description sits in the same
# listing a row's does and a reader cannot tell which is which.
#
# The dotted paths are not full stops and are not treated as any: what
# the rule is about is a second sentence, so what is looked for is a
# stop that ends the string or is followed by a space.


def _described() -> list[tuple[str, str]]:
    return [
        *((" ".join(path), described) for path, described in cli.GROUPS.items()),
        *((" ".join(row.words), row.help) for row in cli.COMMANDS),
    ]


@pytest.mark.parametrize(
    ("named", "described"), _described(), ids=[named for named, _ in _described()]
)
def test_every_description_is_one_lowercase_sentence(named: str, described: str) -> None:
    assert described[:1].islower(), named
    assert not described.endswith("."), named
    assert ". " not in described, named
    assert "\n" not in described, named


def test_the_version_is_asked_of_the_root_alone() -> None:
    """A version is a fact about the installed artifact, so it is asked
    once and not once per command: a `--version` on every page would be
    forty-eight ways to ask the same question."""
    assert "--version" in {
        spelling for parameter in cli.command().params for spelling in parameter.opts
    }
    for row in cli.COMMANDS:
        declared = {
            spelling for parameter in _leaf(row.words).params for spelling in parameter.opts
        }
        assert "--version" not in declared, " ".join(row.words)


@pytest.mark.parametrize(
    "row", cli.COMMANDS, ids=[" ".join(row.words) for row in cli.COMMANDS]
)
def test_every_page_answers_the_short_spelling_of_help(row) -> None:
    """clig 7: `-h` and `--help` both, on every page, because `-h` is
    the one half the world types first.

    Read off the rendered page rather than off the parameters, because
    Click's help option is not one of them: it is built from the
    context, which is where this is set and where every page below
    inherits it from.
    """
    assert "-h, --help" in _page(row.words)


# The version, and the one thing it must not become
#
# 12 Factor 3: the version has to be reachable from the thing an
# operator is holding. The running server already answers `version` and
# `revision` on `/healthz` and stamps both on every session record,
# while the CLI could not be asked at all, which is the wrong way round.


def test_the_version_names_the_distribution_and_leaves_through_zero() -> None:
    """Asking is not failing, so it leaves the way `--help` does."""
    with pytest.raises(SystemExit) as caught:
        cli.main(["--version"])

    assert caught.value.code == 0


def test_the_version_is_the_same_bytes_through_either_spelling(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A version is a fact about the installed artifact, so it may no
    more vary with the invocation than a generated document may. The
    console script is the call with no argument list, which is what
    tells the two entry points apart."""
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    dispatched = capsys.readouterr().out

    monkeypatch.setattr("sys.argv", ["vinga", "--version"])
    with pytest.raises(SystemExit):
        cli.main()
    scripted = capsys.readouterr().out

    assert dispatched == scripted
    assert dispatched == f"{cli.DISTRIBUTION} {cli.installed_version()}\n"


def test_a_tree_with_nothing_installed_says_so_rather_than_guessing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A version this code invented would be worse than none, since what
    the read is for is comparing two halves of a deployment."""
    def missing(_name: str) -> str:
        raise metadata.PackageNotFoundError(cli.DISTRIBUTION)

    monkeypatch.setattr(metadata, "version", missing)

    assert cli.installed_version() == cli.VERSION_UNKNOWN


# The tree, held to the table
#
# The grammar was two words deep until #223 and the assumption was
# written into more places than the registration loop: the loop itself,
# the kind resolution, the two lanes' inventory helpers, the refusal
# families and the two document renderers. Each of those has its own
# case; this is the one that makes a SEVENTH site fail loudly, because
# what it asserts is the property all six were about.


@pytest.mark.parametrize(
    "row", cli.COMMANDS, ids=[" ".join(row.words) for row in cli.COMMANDS]
)
def test_every_row_is_reachable_by_its_own_words(row) -> None:
    """Walked down the built tree word by word, so a row registered
    under the wrong parent, or with a word in the middle discarded, is a
    row this cannot reach.

    The failure it exists for is silent: a completeness test that cannot
    see a command reports full coverage of a tree with a hole in it.
    """
    found = cli.command()
    for word in row.words[:-1]:
        assert word in getattr(found, "commands", {}), " ".join(row.words)
        found = found.commands[word]
    assert row.words[-1] in getattr(found, "commands", {}), " ".join(row.words)


# A word the grammar used to have, and does not
#
# Every completeness test on this page is positive: each says something
# the tree HAS is reachable, described and driven. Not one of them can
# see a word the tree should no longer have, and an alias kept for
# kindness would pass all of them as one more row. So the removal is
# pinned the way the arrival is, from both ends in one test: the word is
# gone from the table and from the page a reader lists commands off, the
# invocation answers the refusal any other invented word gets, and the
# spelling that replaced it works.
#
# No alias, deliberately, and the stance rather than an oversight:
# nothing third-party is installed against this grammar, and a
# deployment that types the old word is told it is not a command in the
# same sentence every other typo gets.


def test_the_flat_status_word_is_gone_and_the_noun_spelling_answers(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """`status` moved under `mcp-server` in #341, with no alias left
    behind."""
    assert ("status",) not in {row.words for row in cli.COMMANDS}
    assert "status" not in cli.command().commands

    assert run("status") == 1
    refused = capsys.readouterr()
    assert refused.err.strip() == cli.usage_line("that is not a command")
    assert refused.out == ""

    assert run("mcp-server", "status") == 0
    assert capsys.readouterr().out.startswith("this server has no MCP servers configured")


def test_every_group_of_the_tree_carries_a_command() -> None:
    """A noun path with no command under it is a heading nothing
    answers to, which is what a discarded intermediate word leaves
    behind."""
    for path in cli.GROUPS:
        assert any(row.words[: len(path)] == path for row in cli.COMMANDS), " ".join(path)
        assert getattr(_leaf(path), "commands", {}), " ".join(path)


@pytest.mark.parametrize(
    "row", cli.COMMANDS, ids=[" ".join(row.words) for row in cli.COMMANDS]
)
def test_a_rows_kind_is_a_kind_the_registry_has(row) -> None:
    """`kind` stopped being the last word when the tree grew a third
    level, so what is left to check is that the explicit fact names
    something: a kind the registry has, the device, or nothing at all
    for a row that addresses no kind."""
    known = {kind.name for kind in cli.entities.ENTITIES} | {"device", ""}

    assert row.kind in known, " ".join(row.words)


def test_the_deep_rows_resolve_to_their_own_kind() -> None:
    """The three-word cases decision 5a names, each of which a
    positional rule reads wrongly: a provider secret's kind is the noun
    it sits under and not `set`, and a pending claim's kind is the
    device its two-word noun path opens with."""
    by_words = {row.words: row for row in cli.COMMANDS}

    assert by_words[("provider", "secret", "set")].kind == "provider"
    assert by_words[("provider", "secret", "clear")].kind == "provider"
    assert by_words[("mcp-server", "secret", "set")].kind == "mcp-server"
    assert by_words[("device", "pending", "claim")].kind == "device"
    assert by_words[("device", "pending", "list")].kind == "device"


def test_the_reference_carries_a_heading_for_every_level() -> None:
    """The renderer's own case: a discarded intermediate group would be
    a missing heading in the committed reference, which CI would catch
    as drift without saying why."""
    rendered = cli.cli_reference()

    assert f"### `{cli.PROGRAM} provider secret`" in rendered
    assert f"### `{cli.PROGRAM} provider secret set`" in rendered
    assert f"### `{cli.PROGRAM} device pending`" in rendered
    assert f"### `{cli.PROGRAM} device pending claim`" in rendered


# What the program was invoked as
#
# The name a live help page prints is a closed map from a known entry
# point to a written-down string, and `argv[0]` is read by nothing at
# all. That is the strongest form of the rule and also the easiest to
# lose: one `sys.argv[0]` interpolated into a usage line would be a
# value nobody validated printed into help, into the committed
# reference, into an operator's exported file and into whatever collects
# stderr.
#
# `argv[0]` is what a shell puts there, and a shell puts whatever it was
# given: a symlink's name, an `exec -a`, a path under a directory named
# after whatever the person was pasting. So it is treated as a value,
# and this drives all six surfaces the plan names with a
# credential-shaped one in place.

HOSTILE = "sk-argv-3f9a1c7e-never-a-real-credential"


def test_no_surface_interpolates_what_the_program_was_invoked_as(
    run,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Six surfaces, one sentinel, and it reaches none of them.

    Help and a leaf's help, because that is the only surface the
    invocation is allowed to vary; the recipes and the reference,
    because those are generated documents and a document may no more
    vary with the invocation than with the terminal; an export, because
    it is the one output an operator keeps in a file; and the log
    records and the exception chain a refusal is carried by.
    """
    monkeypatch.setattr("sys.argv", [f"/opt/{HOSTILE}/bin/vinga", "list"])
    assert run("agent", "set", "kids", "-f", "-", stdin="prompt: You are kids.\n") == 0
    capsys.readouterr()

    with pytest.raises(SystemExit):
        cli.main(["--help"])
    root_help = capsys.readouterr().out
    with pytest.raises(SystemExit):
        cli.main(["agent", "set", "--help"])
    leaf_help = capsys.readouterr().out

    with caplog.at_level(logging.DEBUG):
        assert run("export") == 0
    exported = capsys.readouterr().out

    with pytest.raises(cli.ConfigError) as refused:
        cli._parsed(["agent", "show", "no-such-agent"], cli.DISPATCHED)

    surfaces = {
        "help": root_help,
        "leaf help": leaf_help,
        "recipes": cli.cli_recipes(),
        "reference": cli.cli_reference(),
        "export": exported,
        "logs": logged(caplog),
        "chain": chain(refused.value),
    }

    # The surfaces are about something: an empty one would pass this
    # while proving nothing.
    assert all(text for text in surfaces.values()), surfaces
    assert [where for where, text in surfaces.items() if HOSTILE in text] == []


def test_a_help_page_prints_the_entry_point_it_was_reached_by(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one licensed variation, and it is a fixed string per entry
    point rather than anything the invocation carried: the console
    script is the call with no argument list, and the dispatch is the
    call with one."""
    monkeypatch.setattr("sys.argv", [f"/opt/{HOSTILE}/bin/vinga", "--help"])
    with pytest.raises(SystemExit):
        cli.main()
    scripted = capsys.readouterr().out

    with pytest.raises(SystemExit):
        cli.main(["--help"])
    dispatched = capsys.readouterr().out

    assert scripted.startswith(f"Usage: {cli.CONSOLE_SCRIPT} ")
    assert dispatched.startswith(f"Usage: {cli.DISPATCHED} ")
    assert HOSTILE not in scripted + dispatched
