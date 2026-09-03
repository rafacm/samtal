"""The confirmation a destructive verb asks, and the flags around it.

Three things are held here, and they are three because each of them
fails a different way.

**Which verbs are destructive** is a fact on the registration table, and
a test that derived the expected set from that same fact would pass on a
`delete` row accidentally marked false: the row would leave the
implementation and the expectation together. So the intended set is
written out below as a closed semantic list and compared against the
table in BOTH directions. The one derivation kept is from the descriptor
registry rather than from the flag, so a new deletable kind joins both
sides at once.

**When it asks** is the whole precedence matrix, every row a case: both
flags, both TTY states, and the three positions a flag can be given in.
The asymmetry worth naming rather than smoothing is that `--no-input`
refuses a destructive verb and does not refuse a secret write: a
confirmation has no other way to be answered, while a secret has three
doors and disabling one leaves two.

**What it says** is three fixed constants carrying no address and no
other value from the command line, and the sentinels for that are
exhaustive rather than representative: every identity segment a
destructive command takes carries a DISTINCT credential-shaped value,
one per field, so a leak names its own source, and absence is asserted
on all four surfaces a value can come out on.
"""

import io
import logging
from pathlib import Path

import pytest

from tests.support.config_cli import chain as _chain
from tests.support.config_cli import document as _document
from tests.support.config_cli import logged as _logged
from tests.support.config_cli import runner
from vinga_server.config import cli, entities


@pytest.fixture
def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    return runner(monkeypatch)


class Terminal(io.StringIO):
    """A stdin that says it is a terminal, which is the one thing the
    confirmation branches on. The scripted text is what the person at
    the keyboard types."""

    def isatty(self) -> bool:
        return True


def at_a_terminal(monkeypatch: pytest.MonkeyPatch, typed: str) -> None:
    monkeypatch.setattr("sys.stdin", Terminal(typed))


def through_a_pipe(monkeypatch: pytest.MonkeyPatch, piped: str = "") -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(piped))


# The destructive set, as a closed semantic list
#
# Stated as a rule plus six names rather than as ten literals, so a
# fifth deletable kind does not silently shrink the expectation. The
# count is part of the assertion because the list is the oracle: a set
# that grew without this file noticing is exactly what the comparison
# below exists to catch.


def intended() -> set[tuple[str, ...]]:
    """Every command whose effect cannot be undone by running another
    command with information the operator still has."""
    return {
        *((kind.name, "delete") for kind in entities.ENTITIES if kind.has_delete),
        ("device", "delete"),
        ("provider", "secret", "clear"),
        ("mcp-server", "secret", "clear"),
        ("default-agent", "clear"),
        # The conversation store's three erasures, one per projection
        # plus the purge. None is undoable by any command in this
        # grammar and none is undoable at all: what they take is
        # dialogue, which nothing here can write back.
        ("session", "delete"),
        ("session", "purge"),
        ("conversation", "delete"),
        # And memory's own, which is one verb over three scopes. Every
        # deletion it makes is a hard delete: the soft forgetting an
        # agent does belongs to the conversation that spoke it, and this
        # door is correction and audit rather than that flow, so nothing
        # it takes is held for an undo.
        ("memory", "delete"),
    }


def test_the_table_marks_exactly_the_destructive_commands() -> None:
    """Both directions, because each catches a different mistake: a row
    in the list without the flag is a delete that stopped asking, and a
    row with the flag that is not in the list is a command that started
    asking for no stated reason."""
    marked = {row.words for row in cli.COMMANDS if row.destroys}

    assert marked == intended()
    assert len(marked) == 12


def test_a_replacement_write_is_not_destructive() -> None:
    """The half decided rather than left implicit.

    A `set` overwrites an entity whole and `import` overwrites many,
    and neither is confirmed: the API acknowledges exactly what was
    written, the previous value is recoverable by writing it back and
    `export` is the documented way to have it, and a prompt in the
    middle of the most-scripted command in the grammar is what the
    automation rule forbids. A rebinding is the same shape, and `apply`
    changes what a server is doing rather than what is stored.
    """
    never = {
        ("provider", "set"),
        ("agent-defaults", "set"),
        ("import",),
        ("device", "bind"),
        ("device", "pending", "claim"),
        ("apply",),
        ("default-agent", "set"),
    }

    assert never & {row.words for row in cli.COMMANDS if row.destroys} == set()


# The precedence matrix
#
# One case per row of it. The store is seeded with an agent, and what
# each case asserts is the exit code, which stream the sentence went to,
# and whether the agent is still there afterwards, because "it did not
# prompt" and "it did not delete" are two different claims.


def an_agent(run) -> None:
    assert run("agent", "set", "kids", "-f", "-", stdin="prompt: You are kids.\n") == 0


def still_stored(run, capsys: pytest.CaptureFixture[str]) -> bool:
    capsys.readouterr()
    code = run("agent", "show", "kids")
    capsys.readouterr()
    return code == 0


def test_a_terminal_is_asked_and_a_yes_goes_ahead(
    run, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    an_agent(run)
    capsys.readouterr()
    at_a_terminal(monkeypatch, "y\n")

    assert run("agent", "delete", "kids") == 0

    captured = capsys.readouterr()
    # The question is about this invocation, so it is on stderr with the
    # notices rather than in the data a caller came for.
    assert cli.CONFIRMATION in captured.err
    assert cli.CONFIRMATION not in captured.out
    assert not still_stored(run, capsys)


def test_a_terminal_is_asked_and_anything_else_stops(
    run, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    an_agent(run)
    capsys.readouterr()
    at_a_terminal(monkeypatch, "n\n")

    assert run("agent", "delete", "kids") == 1

    captured = capsys.readouterr()
    assert captured.err.endswith(cli.DECLINED + "\n")
    assert captured.out == ""
    assert still_stored(run, capsys)


def test_force_at_a_terminal_does_not_ask(
    run, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    an_agent(run)
    capsys.readouterr()
    at_a_terminal(monkeypatch, "")

    assert run("agent", "delete", "kids", "--force") == 0

    assert cli.CONFIRMATION not in capsys.readouterr().err
    assert not still_stored(run, capsys)


def test_no_input_at_a_terminal_refuses(
    run, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The asymmetry, stated: this refuses because a confirmation has no
    second door, and `--force` is that door."""
    an_agent(run)
    capsys.readouterr()
    at_a_terminal(monkeypatch, "y\n")

    assert run("agent", "delete", "kids", "--no-input") == 1

    captured = capsys.readouterr()
    assert captured.err == cli.NO_INPUT_REFUSED + "\n"
    assert cli.CONFIRMATION not in captured.err
    assert still_stored(run, capsys)


def test_force_and_no_input_together_go_ahead(
    run, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not a conflict: `--force` answers the question `--no-input` would
    have refused for, and a script that passes both is asking for the
    same thing twice."""
    an_agent(run)
    capsys.readouterr()
    at_a_terminal(monkeypatch, "")

    assert run("agent", "delete", "kids", "--force", "--no-input") == 0

    assert cli.CONFIRMATION not in capsys.readouterr().err
    assert not still_stored(run, capsys)


@pytest.mark.parametrize(
    "flags",
    [(), ("--force",), ("--no-input",), ("--force", "--no-input")],
    ids=["neither", "force", "no-input", "both"],
)
def test_a_pipe_is_never_blocked_whatever_the_flags(
    run,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    flags: tuple[str, ...],
) -> None:
    """Never block a pipe, which is the automation rule. There is nobody
    to ask, so there is nothing to disable and nothing to force."""
    an_agent(run)
    capsys.readouterr()
    through_a_pipe(monkeypatch)

    assert run("agent", "delete", "kids", *flags) == 0

    assert cli.CONFIRMATION not in capsys.readouterr().err
    assert not still_stored(run, capsys)


# Where a flag was given
#
# The three positions, which is what `bool | None` buys: an absent copy
# at the command position must not overwrite what the root position
# said, and an ordinary boolean default would arrive as False and do
# exactly that.


def test_a_flag_given_before_the_command_survives_a_command_without_one(
    run, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    an_agent(run)
    capsys.readouterr()
    at_a_terminal(monkeypatch, "y\n")

    assert run("--no-input", "agent", "delete", "kids") == 1

    assert capsys.readouterr().err == cli.NO_INPUT_REFUSED + "\n"
    assert still_stored(run, capsys)


def test_a_flag_given_after_the_command_applies(
    run, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    an_agent(run)
    capsys.readouterr()
    at_a_terminal(monkeypatch, "y\n")

    assert run("agent", "delete", "kids", "--no-input") == 1

    assert capsys.readouterr().err == cli.NO_INPUT_REFUSED + "\n"


def test_the_command_position_wins_where_both_said_something(
    run, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Like `--config` and `--api-url`: the nearer position wins, and
    the further one survives only where the nearer said nothing."""
    an_agent(run)
    capsys.readouterr()
    at_a_terminal(monkeypatch, "")

    assert run("--no-input", "agent", "delete", "kids", "--force") == 0

    assert not still_stored(run, capsys)


# The other prompt in the grammar
#
# `--no-input` disables every prompt, which is the rule, and not only the
# confirmation's.


class _NeverRead(io.IOBase):
    """A terminal that fails the case if anything reads it.

    A preloaded `StringIO` cannot tell "read it and got a value" from
    "did not read it", and the difference is the whole of what is being
    checked: a real terminal has nothing in it until a person types, so
    a read of one waits rather than answering. This one has no value to
    hand back and says so by failing loudly, which is what a real
    terminal does slowly.
    """

    def isatty(self) -> bool:
        return True

    def read(self, *args: object, **kwargs: object) -> str:
        raise AssertionError("a terminal was read while prompting was disabled")

    def readline(self, *args: object, **kwargs: object) -> str:
        raise AssertionError("a terminal was read while prompting was disabled")


def a_provider(run) -> None:
    assert run(
        "provider", "set", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n"
    ) == 0


def test_no_input_at_a_terminal_answers_without_reading_it(
    run, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """It does not refuse with a flag's own sentence, and it does not
    read: a terminal nobody may type at holds nothing, so the answer is
    the empty secret it would have yielded, arrived at without the wait.

    The stdin double fails the case if it is read at all, and the
    prompt does the same, so "it did not block" is asserted rather than
    inferred from a value a preloaded buffer happened to hold.
    """
    a_provider(run)
    capsys.readouterr()
    monkeypatch.setattr("sys.stdin", _NeverRead())
    monkeypatch.setattr(
        "getpass.getpass",
        lambda *_a, **_k: pytest.fail("a prompt was printed while prompting was disabled"),
    )

    assert run("provider", "secret", "set", "llm", "claude", "api_key", "--no-input") == 1

    captured = capsys.readouterr()
    assert captured.err == cli.SECRET_EMPTY + "\n"
    assert captured.out == ""


def test_no_input_through_a_pipe_reads_it_plainly(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The half the flag leaves alone. A pipe has the value in it
    already, so nothing waits and nothing prompts, and disabling
    prompting changes nothing about the read."""
    a_provider(run)
    capsys.readouterr()

    assert run(
        "provider", "secret", "set", "llm", "claude", "api_key",
        "--no-input",
        stdin="sk-not-a-real-credential\n",
    ) == 0

    assert "wrote " in capsys.readouterr().out


def test_from_env_is_read_whatever_the_flags_say(
    run, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """No prompt was ever going to happen, so neither flag has anything
    to do."""
    assert run(
        "provider", "set", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n"
    ) == 0
    capsys.readouterr()
    monkeypatch.setenv("A_VARIABLE", "sk-not-a-real-credential")
    at_a_terminal(monkeypatch, "")

    assert run(
        "provider", "secret", "set", "llm", "claude", "api_key",
        "--from-env", "A_VARIABLE", "--no-input",
    ) == 0

    assert "wrote " in capsys.readouterr().out


# What the three sentences may not carry
#
# One distinct credential-shaped value per field, so a leak names its
# own source, and every identity segment a destructive command takes has
# one. Every segment: a provider is addressed by a stage AND a name, and
# a case that planted a real stage beside a planted name would have been
# checking one of the two while calling itself exhaustive. `code` is the
# one segment no destructive command carries, since claiming a board is
# a rebinding rather than a destruction, and it is named here rather
# than left out silently.

PLANTED_NAME = "sk-name-4f8b2c9e-never-a-real-credential"

PLANTED_SLOT = "sk-slot-7a1d3f60-never-a-real-credential"

PLANTED_MAC = "sk-mac-2b6e5c41-never-a-real-credential"

PLANTED_STAGE = "sk-stage-9c3d7e28-never-a-real-credential"

# Every destructive shape, with a distinct sentinel in every field it
# addresses. The provider rows carry two and three, which is what makes
# a leak from the middle segment visible as itself.
ADDRESSED: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = [
    (
        "provider delete",
        ("provider", "delete", PLANTED_STAGE, PLANTED_NAME),
        (PLANTED_STAGE, PLANTED_NAME),
    ),
    ("agent delete", ("agent", "delete", PLANTED_NAME), (PLANTED_NAME,)),
    ("mcp-server delete", ("mcp-server", "delete", PLANTED_NAME), (PLANTED_NAME,)),
    (
        "prompt-fragment delete",
        ("prompt-fragment", "delete", PLANTED_NAME),
        (PLANTED_NAME,),
    ),
    ("device delete", ("device", "delete", PLANTED_MAC), (PLANTED_MAC,)),
    (
        "provider secret clear",
        ("provider", "secret", "clear", PLANTED_STAGE, PLANTED_NAME, PLANTED_SLOT),
        (PLANTED_STAGE, PLANTED_NAME, PLANTED_SLOT),
    ),
    (
        "mcp-server secret clear",
        ("mcp-server", "secret", "clear", PLANTED_NAME, PLANTED_SLOT),
        (PLANTED_NAME, PLANTED_SLOT),
    ),
    ("default-agent clear", ("default-agent", "clear"), ()),
]


def surfaces(
    capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture, argv: tuple[str, ...]
) -> dict[str, str]:
    """The four places a value can come out on: the two streams, the
    records this process wrote while the case ran, and the exception the
    refusal is carried by, chain included."""
    captured = capsys.readouterr()
    refused = ""
    try:
        cli._parsed(list(argv), cli.DISPATCHED)
    except BaseException as exc:  # noqa: BLE001 - the chain is what is being read
        refused = _chain(exc)
    return {
        "stdout": captured.out,
        "stderr": captured.err,
        "logs": _logged(caplog),
        "chain": refused,
    }


@pytest.mark.parametrize(
    ("argv", "planted"),
    [(argv, planted) for _, argv, planted in ADDRESSED],
    ids=[name for name, _, _ in ADDRESSED],
)
def test_the_declined_sentence_carries_no_field_of_the_command(
    run,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    argv: tuple[str, ...],
    planted: tuple[str, ...],
) -> None:
    """The confirmation question and the sentence a decline prints, held
    to being the constants they are declared as."""
    at_a_terminal(monkeypatch, "n\n" * 4)
    capsys.readouterr()

    with caplog.at_level(logging.DEBUG):
        assert run(*argv) == 1

    found = surfaces(capsys, caplog, argv)
    assert found["stderr"] == cli.CONFIRMATION + cli.DECLINED + "\n"
    assert found["stdout"] == ""
    for sentinel in planted:
        assert [where for where, text in found.items() if sentinel in text] == []


@pytest.mark.parametrize(
    ("argv", "planted"),
    [(argv, planted) for _, argv, planted in ADDRESSED],
    ids=[name for name, _, _ in ADDRESSED],
)
def test_the_no_input_refusal_carries_no_field_of_the_command(
    run,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    argv: tuple[str, ...],
    planted: tuple[str, ...],
) -> None:
    at_a_terminal(monkeypatch, "")
    capsys.readouterr()

    with caplog.at_level(logging.DEBUG):
        assert run(*argv, "--no-input") == 1

    found = surfaces(capsys, caplog, (*argv, "--no-input"))
    assert found["stderr"] == cli.NO_INPUT_REFUSED + "\n"
    assert found["stdout"] == ""
    for sentinel in planted:
        assert [where for where, text in found.items() if sentinel in text] == []


def test_the_three_sentences_are_constants_with_nothing_to_fill() -> None:
    """The structural half of the claim above: a sentence with a
    placeholder in it is a sentence something could be interpolated
    into later, which is how this rule is broken by accident."""
    for sentence in (cli.CONFIRMATION, cli.DECLINED, cli.NO_INPUT_REFUSED):
        assert "{" not in sentence
        assert "%" not in sentence


def test_a_declined_delete_leaves_the_entry_where_it_was(
    run, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The store effect, which is the claim a stream assertion cannot
    make: a decline is not a delete that printed differently."""
    an_agent(run)
    capsys.readouterr()
    at_a_terminal(monkeypatch, "no thanks\n")

    assert run("agent", "delete", "kids") == 1
    capsys.readouterr()

    assert run("agent", "show", "kids") == 0
    assert _document(capsys.readouterr().out)["prompt"] == "You are kids."


# A terminal that will not answer
#
# The question is printed and the answer never arrives. Two ways: the
# stream has gone, which is an `OSError`, and the bytes will not decode
# under the terminal's encoding, which is a `UnicodeError` and so not an
# `OSError` at all. The second is the one that matters most, because
# what it retains is the bytes it could not read, off a terminal
# somebody is typing a delete into.


class _Failing(io.StringIO):
    """A terminal whose read raises whatever it was built with."""

    def __init__(self, raised: BaseException) -> None:
        super().__init__("")
        self._raised = raised

    def isatty(self) -> bool:
        return True

    def readline(self, *args: object, **kwargs: object) -> str:
        raise self._raised


UNREADABLE_TERMINAL = [
    ("the stream has gone", OSError(5, "Input/output error", PLANTED_NAME)),
    (
        "bytes the terminal will not decode",
        UnicodeDecodeError("utf-8", PLANTED_SLOT.encode() + b"\xff", 41, 42, "invalid"),
    ),
    ("a value the reader refuses", ValueError(f"cannot read {PLANTED_MAC}")),
]


@pytest.mark.parametrize(
    "raised",
    [raised for _, raised in UNREADABLE_TERMINAL],
    ids=[what for what, _ in UNREADABLE_TERMINAL],
)
def test_a_terminal_that_will_not_answer_deletes_nothing_and_says_so(
    run,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    raised: BaseException,
) -> None:
    """One fixed sentence, exit 1, and the entry still there: a read
    that failed is not an answer, and the safe reading of no answer is
    the one that changes nothing.

    Each failure carries a credential-shaped value of its own, and none
    of them reaches any of the four surfaces.
    """
    an_agent(run)
    capsys.readouterr()
    monkeypatch.setattr("sys.stdin", _Failing(raised))

    with caplog.at_level(logging.DEBUG):
        assert run("agent", "delete", "kids") == 1

    captured = capsys.readouterr()
    assert captured.err == cli.CONFIRMATION + cli.CONFIRMATION_UNREADABLE + "\n"
    assert captured.out == ""
    assert "Traceback" not in captured.err
    for sentinel in (PLANTED_NAME, PLANTED_SLOT, PLANTED_MAC):
        assert sentinel not in captured.err + captured.out + _logged(caplog)

    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert still_stored(run, capsys)


@pytest.mark.parametrize(
    "raised",
    [raised for _, raised in UNREADABLE_TERMINAL],
    ids=[what for what, _ in UNREADABLE_TERMINAL],
)
def test_the_unreadable_terminal_refusal_carries_nothing_on_its_chain(
    monkeypatch: pytest.MonkeyPatch, raised: BaseException
) -> None:
    """The half no assertion about a stream can make. A decoding failure
    holds the bytes it was given, and an operating-system error holds
    what it was reading from; neither is behind the sentence."""
    monkeypatch.setattr("sys.stdin", _Failing(raised))

    with pytest.raises(cli.ConfigError) as caught:
        cli._permitted_to_destroy(cli.Invocation())

    assert str(caught.value) == cli.CONFIRMATION_UNREADABLE
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    for sentinel in (PLANTED_NAME, PLANTED_SLOT, PLANTED_MAC):
        assert sentinel not in _chain(caught.value)
