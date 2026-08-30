"""The `memory` noun: three verbs over three scopes, and what they
print.

The operator's door onto what an agent, a board and a conversation
remember, driven the way `conversation` is next door: requests through
the client seam, against a server built per command, with memory seeded
through the store an agent writes through.

What this file exists for beyond the round trips is the pair of
properties the memory noun has and the record's nouns do not:

- **Content never rides argv.** A remembered fact is what somebody said
  in a room and a ledger key is a word a model chose, so either can be
  credential-shaped, and the grammar has no positional for either: the
  correction is read from a file or from standard input, the key is read
  from standard input, and a caller who types one meets a usage refusal
  that quotes nothing back. Asserted on the grammar rather than on the
  invocations this file happens to write, because what is claimed is
  that there is no spelling that would carry it.
- **Neither reaches a URL either.** The requests the commands build are
  recorded, and the target a proxy would log carries neither the text
  nor the key.

Beside those, the two the record's verbs already have: content printed
whole and unable to steer a terminal, and a destructive verb that asks
at a terminal, takes `--force` and is refused by `--no-input`.
"""

import asyncio
import contextlib
import io
import logging
import uuid
from pathlib import Path

import httpx
import pytest

from tests.support.config_cli import answering, runner
from tests.support.stores import memory, memory_rows
from vinga_server import logs
from vinga_server.config import cli
from vinga_server.memory.scopes import MemoryScope

AGENT = "poet"

BOARD = "aa:bb:cc:dd:ee:ff"

# Shaped so a substring check for it cannot match by accident, and
# planted where content and a caller's own words travel.
SENTINEL = "sk-test-6d20f4ae-never-a-real-credential"

# Everything a terminal reads as an instruction rather than as text.
# Planted in a remembered fact, which is where text this server never
# wrote comes from.
STEERING = "\x1b[31mred\x1b[0m\x07\tone\rtwo\nthree"


@pytest.fixture
def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    return runner(monkeypatch)


@pytest.fixture
def thread() -> str:
    """A thread minted per test, the way a session mints one."""
    return uuid.uuid4().hex


class Terminal(io.StringIO):
    """A stdin that says it is a terminal, which is what the
    confirmation and the content reads both branch on."""

    def isatty(self) -> bool:
        return True


def at_a_terminal(monkeypatch: pytest.MonkeyPatch, typed: str = "") -> None:
    monkeypatch.setattr("sys.stdin", Terminal(typed))


def through_a_pipe(monkeypatch: pytest.MonkeyPatch, piped: str = "") -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(piped))


def told(scope: MemoryScope, owner: str, *facts: str) -> list[int]:
    store = memory()
    return [asyncio.run(store.add(scope, owner, fact, agent=AGENT)) for fact in facts]


def kept(conversation: str, **entries: str) -> None:
    store = memory()
    for key, value in entries.items():
        asyncio.run(store.set_state(conversation, key, value, agent=AGENT))


def out(run, capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    capsys.readouterr()
    code = run(*argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _leaked(caplog: pytest.LogCaptureFixture) -> str:
    return "".join(
        record.getMessage() + str(record.__dict__) + logs.JsonFormatter().format(record)
        for record in caplog.records
        if record.name.startswith("vinga_server")
    )


# The listings


def test_the_owner_listing_is_a_borderless_table_with_its_headings(run, capsys) -> None:
    """Columns, because both fields are short and the question is which
    of several owners is the one wanted."""
    told(MemoryScope.AGENT, AGENT, "one", "two")

    code, printed, err = out(run, capsys, "memory", "list", "agent")

    assert (code, err) == (0, "")
    lines = printed.splitlines()
    assert lines[0].split() == ["OWNER", "FACTS"]
    assert lines[1].split() == [AGENT, "2"]


def test_naming_an_owner_lists_that_memory_instead(run, capsys) -> None:
    """The same words one level up, which is what makes the pair one
    verb: with no owner it is who is remembering anything, and with one
    it is what that one remembers."""
    numbers = told(MemoryScope.AGENT, AGENT, "the user is vegetarian")

    code, printed, err = out(run, capsys, "memory", "list", "agent", AGENT)

    assert (code, err) == (0, "")
    assert printed.splitlines()[0] == f"{numbers[0]}: the user is vegetarian"
    assert printed.splitlines()[1].startswith("  written: ")


def test_the_device_scope_is_addressed_by_the_board(run, capsys) -> None:
    told(MemoryScope.DEVICE, BOARD, "the kitchen is small")

    owners = out(run, capsys, "memory", "list", "device")
    notes = out(run, capsys, "memory", "list", "device", BOARD.upper())

    assert owners[1].splitlines()[1].split() == [BOARD, "1"]
    assert notes[1].splitlines()[0].endswith(": the kitchen is small")


def test_the_conversation_scope_lists_the_ledger(run, capsys, thread: str) -> None:
    kept(thread, scene="a forest", turn="4")

    holders = out(run, capsys, "memory", "list", "conversation")
    ledger = out(run, capsys, "memory", "list", "conversation", thread)

    assert holders[1].splitlines()[0].split() == ["CONVERSATION", "STATE", "HELD"]
    assert holders[1].splitlines()[1].split() == [thread, "2", "0"]
    assert ledger[1].splitlines()[0] == "scene: a forest"
    assert ledger[1].splitlines()[2] == "turn: 4"


def test_a_held_fact_says_which_conversation_forgot_it(run, capsys, thread: str) -> None:
    """The rare state, and the one an operator needs the conversation
    for: what can bring it back is the thread that let it go."""
    (number,) = told(MemoryScope.AGENT, AGENT, "the user is vegetarian")
    asyncio.run(
        memory().forget(MemoryScope.AGENT, AGENT, number, thread, agent=AGENT)
    )

    code, printed, err = out(run, capsys, "memory", "list", "agent", AGENT)

    assert (code, err) == (0, "")
    assert f"forgotten in {thread}" in printed


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (("memory", "list", "agent"), "nothing is remembered under that scope"),
        (("memory", "list", "conversation"), "no conversation is keeping anything"),
        (("memory", "list", "agent", AGENT), "this memory holds nothing"),
    ],
)
def test_an_empty_listing_says_so_rather_than_printing_a_heading(
    run, capsys, argv: tuple[str, ...], expected: str
) -> None:
    code, printed, err = out(run, capsys, *argv)

    assert (code, err) == (0, "")
    assert printed.startswith(expected)


def test_an_empty_ledger_says_so(run, capsys, thread: str) -> None:
    code, printed, err = out(run, capsys, "memory", "list", "conversation", thread)

    assert (code, err) == (0, "")
    assert printed.startswith("this conversation is keeping nothing")


def test_the_limit_is_the_apis_rule_and_the_apis_sentence(run, capsys) -> None:
    told(MemoryScope.AGENT, AGENT, "one", "two")

    code, printed, err = out(run, capsys, "memory", "list", "agent", AGENT, "--limit", "1")
    refused = out(run, capsys, "memory", "list", "agent", "--limit", "0")

    assert (code, err) == (0, "")
    assert len(printed.splitlines()) == 2
    assert refused[0] == 1
    assert "limit has to be a whole number" in refused[2]


def test_a_stored_fact_is_printed_whole(run, capsys) -> None:
    """This command exists to show what an agent will be sent, so a
    concealed tail is exactly what the operator came to see: the rule
    `agent preview` draws, applied to the same content one layer down.
    """
    long_fact = "the user said " + "x" * (cli.CELL_LENGTH * 2)
    told(MemoryScope.AGENT, AGENT, long_fact)

    code, printed, err = out(run, capsys, "memory", "list", "agent", AGENT)

    assert (code, err) == (0, "")
    assert long_fact in printed


def test_planted_control_content_cannot_steer_a_terminal(run, capsys) -> None:
    """A fact is what a room said through a transcriber, so what a
    renderer must keep is the line structure: every unprintable becomes
    a question mark, a newline included."""
    told(MemoryScope.AGENT, AGENT, STEERING)

    code, printed, err = out(run, capsys, "memory", "list", "agent", AGENT)

    assert (code, err) == (0, "")
    assert "\x1b" not in printed
    assert "\x07" not in printed
    # Two lines for one fact, whatever the fact tried to add. The store
    # normalizes to one line, and the renderer replaces anything left.
    assert len(printed.splitlines()) == 2


def test_the_output_is_the_same_bytes_at_a_terminal_and_through_a_pipe(
    run, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No colour and no terminal-dependent rendering, so redirecting the
    output changes nothing about it."""
    told(MemoryScope.AGENT, AGENT, STEERING)
    through_a_pipe(monkeypatch)

    piped = _captured(run, ("memory", "list", "agent", AGENT), terminal=False)
    terminal = _captured(run, ("memory", "list", "agent", AGENT), terminal=True)

    assert terminal == piped
    assert piped[1] == ""


class _Stream(io.StringIO):
    """An output stream whose `isatty` this test decides."""

    def __init__(self, terminal: bool) -> None:
        super().__init__()
        self.terminal = terminal

    def isatty(self) -> bool:
        return self.terminal


def _captured(run, argv: tuple[str, ...], terminal: bool) -> tuple[str, str]:
    printed, errors = _Stream(terminal), _Stream(terminal)
    with contextlib.redirect_stdout(printed), contextlib.redirect_stderr(errors):
        assert run(*argv) == 0
    return printed.getvalue(), errors.getvalue()


# The correction


def test_a_correction_reads_the_text_from_standard_input(run, capsys) -> None:
    (number,) = told(MemoryScope.AGENT, AGENT, "the user likes rain")

    capsys.readouterr()
    code = run("memory", "set", "agent", AGENT, str(number), stdin="the user loves rain\n")
    printed = capsys.readouterr().out

    assert code == 0
    assert printed.splitlines()[0] == f"{number}: the user loves rain"
    assert [row["fact"] for row in memory_rows("facts", owner=AGENT)] == [
        "the user loves rain"
    ]


def test_a_correction_reads_the_text_from_a_named_file(
    run, capsys, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (number,) = told(MemoryScope.DEVICE, BOARD, "the kitchen is small")
    written = tmp_path / "note.txt"
    written.write_text("the kitchen is the small one\n", encoding="utf-8")
    at_a_terminal(monkeypatch)

    code, printed, err = out(
        run, capsys, "memory", "set", "device", BOARD, str(number), "-f", str(written)
    )

    assert (code, err) == (0, "")
    assert printed.splitlines()[0] == f"{number}: the kitchen is the small one"


def test_a_correction_at_a_terminal_with_nothing_piped_refuses(
    run, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rule the whole grammar keeps: never block a pipe, and never
    leave somebody at a cursor with no explanation."""
    (number,) = told(MemoryScope.AGENT, AGENT, "the user likes rain")
    at_a_terminal(monkeypatch)

    code, printed, err = out(run, capsys, "memory", "set", "agent", AGENT, str(number))

    assert (code, printed) == (1, "")
    assert "never from an argument" in err
    assert [row["fact"] for row in memory_rows("facts", owner=AGENT)] == [
        "the user likes rain"
    ]


def test_an_empty_read_corrects_nothing(run, capsys) -> None:
    (number,) = told(MemoryScope.AGENT, AGENT, "the user likes rain")

    capsys.readouterr()
    code = run("memory", "set", "agent", AGENT, str(number), stdin="  \n")
    err = capsys.readouterr().err

    assert code == 1
    assert "nothing was read to correct the fact with" in err


def test_the_ledger_is_not_corrected_from_here(run, capsys, thread: str) -> None:
    """State holds what is currently true in one conversation, written
    by the agent as it goes; an operator's correction of it would be a
    move nobody made."""
    kept(thread, scene="a forest")

    capsys.readouterr()
    code = run("memory", "set", "conversation", thread, "1", stdin="a swamp\n")
    err = capsys.readouterr().err

    assert code == 1
    assert "not corrected from here" in err
    assert [row["value"] for row in memory_rows("state", conversation=thread)] == [
        "a forest"
    ]


def test_the_grammar_has_no_positional_for_a_fact(run, capsys, caplog) -> None:
    """The property finding 8 is about, held against the grammar rather
    than against the invocations this file writes: there is no spelling
    that puts the text in argv, so a caller who tries meets a usage
    refusal that quotes nothing back.

    Driven with valid text piped in as well, which is what makes this
    about the grammar: a command that grew a positional for the text
    would take the argument and succeed, and one that has none refuses
    the whole invocation whatever is on standard input.
    """
    (number,) = told(MemoryScope.AGENT, AGENT, "the user likes rain")

    with caplog.at_level(logging.DEBUG):
        capsys.readouterr()
        code = run(
            "memory", "set", "agent", AGENT, str(number), SENTINEL,
            stdin="the user loves rain\n",
        )
        captured = capsys.readouterr()

    assert (code, captured.out) == (1, "")
    assert SENTINEL not in captured.err
    assert SENTINEL not in _leaked(caplog)
    assert [row["fact"] for row in memory_rows("facts", owner=AGENT)] == [
        "the user likes rain"
    ]


# The deletions


def test_delete_confirms_at_a_terminal_and_answers_the_count(
    run, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = told(MemoryScope.AGENT, AGENT, "one", "two")
    at_a_terminal(monkeypatch, "y\n")

    code, printed, err = out(
        run, capsys, "memory", "delete", "agent", AGENT, str(first)
    )

    assert code == 0
    assert "Type y to go ahead" in err
    assert printed == "facts: 1\n"
    assert [row["id"] for row in memory_rows("facts", owner=AGENT)] == [second]


def test_a_declined_confirmation_deletes_nothing(
    run, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    (number,) = told(MemoryScope.AGENT, AGENT, "one")
    at_a_terminal(monkeypatch, "n\n")

    code, printed, err = out(run, capsys, "memory", "delete", "agent", AGENT, str(number))

    assert (code, printed) == (1, "")
    assert "nothing was deleted" in err
    assert len(memory_rows("facts", owner=AGENT)) == 1


def test_force_answers_the_question_without_asking_it(
    run, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    (number,) = told(MemoryScope.AGENT, AGENT, "one")
    at_a_terminal(monkeypatch)

    code, printed, err = out(
        run, capsys, "memory", "delete", "agent", AGENT, str(number), "--force"
    )

    assert code == 0
    assert "Type y" not in err
    assert printed == "facts: 1\n"


def test_no_input_refuses_rather_than_asking(
    run, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The asymmetry the guide states: a confirmation has no other way
    to be answered, and `--force` is that other way."""
    (number,) = told(MemoryScope.AGENT, AGENT, "one")
    at_a_terminal(monkeypatch, "y\n")

    code, printed, err = out(
        run, capsys, "memory", "delete", "agent", AGENT, str(number), "--no-input"
    )

    assert (code, printed) == (1, "")
    assert "--force" in err
    assert len(memory_rows("facts", owner=AGENT)) == 1


def test_the_whole_scope_needs_its_flag(run, capsys, monkeypatch: pytest.MonkeyPatch) -> None:
    """A flag rather than an absent number, so a mistyped number can
    never mean everything: neither of them and both of them are refused
    from the same rule."""
    told(MemoryScope.AGENT, AGENT, "one", "two")
    through_a_pipe(monkeypatch)

    neither = out(run, capsys, "memory", "delete", "agent", AGENT)
    both = out(run, capsys, "memory", "delete", "agent", AGENT, "1", "--all")

    for code, printed, err in (neither, both):
        assert (code, printed) == (1, "")
        assert "exactly one of them is given" in err
    assert len(memory_rows("facts", owner=AGENT)) == 2


def test_the_flag_erases_the_whole_memory(run, capsys, monkeypatch: pytest.MonkeyPatch) -> None:
    told(MemoryScope.AGENT, AGENT, "one", "two")
    told(MemoryScope.DEVICE, BOARD, "the kitchen is small")
    through_a_pipe(monkeypatch)

    code, printed, err = out(run, capsys, "memory", "delete", "agent", AGENT, "--all")

    assert (code, err) == (0, "")
    assert printed == "facts: 2\n"
    assert memory_rows("facts", owner=AGENT) == []
    assert len(memory_rows("facts", owner=BOARD)) == 1


def test_clearing_one_entry_reads_its_name_from_standard_input(
    run, capsys, thread: str
) -> None:
    kept(thread, scene="a forest", turn="4")

    capsys.readouterr()
    code = run("memory", "delete", "conversation", thread, stdin="scene\n")
    printed = capsys.readouterr().out

    assert code == 0
    assert printed == "state: 1\n"
    assert [row["key"] for row in memory_rows("state", conversation=thread)] == ["turn"]


def test_clearing_the_whole_ledger_is_the_flag(
    run, capsys, thread: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    kept(thread, scene="a forest", turn="4")
    through_a_pipe(monkeypatch)

    code, printed, err = out(run, capsys, "memory", "delete", "conversation", thread, "--all")

    assert (code, err) == (0, "")
    assert printed == "state: 2\n"
    assert memory_rows("state", conversation=thread) == []


def test_a_ledger_entry_is_not_addressed_by_a_number(run, capsys, thread: str) -> None:
    """A conversation's entries are named rather than numbered, and the
    name never rides argv, so a number typed where one would go is
    refused rather than guessed at."""
    kept(thread, scene="a forest")

    capsys.readouterr()
    code = run("memory", "delete", "conversation", thread, "7", stdin="scene\n")
    err = capsys.readouterr().err

    assert code == 1
    assert "not by numbers" in err
    assert len(memory_rows("state", conversation=thread)) == 1


def test_a_key_typed_as_an_argument_reaches_nothing(run, capsys, caplog, thread: str) -> None:
    """The other half of finding 8's property: the grammar has no
    positional for a key either, so a caller who types one is refused
    and nothing of it is echoed."""
    kept(thread, scene="a forest")

    with caplog.at_level(logging.DEBUG):
        code, printed, err = out(
            run, capsys, "memory", "delete", "conversation", thread, SENTINEL
        )

    assert (code, printed) == (1, "")
    assert SENTINEL not in err
    assert SENTINEL not in _leaked(caplog)
    assert len(memory_rows("state", conversation=thread)) == 1


# The scope, and what a refusal says


def test_a_scope_this_grammar_does_not_have_is_refused_without_being_quoted(
    run, capsys, caplog
) -> None:
    with caplog.at_level(logging.DEBUG):
        code, printed, err = out(run, capsys, "memory", "list", SENTINEL)

    assert (code, printed) == (1, "")
    assert "agent, device or conversation" in err
    assert SENTINEL not in err
    assert SENTINEL not in _leaked(caplog)


def test_an_unknown_number_is_the_apis_own_sentence(
    run, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    through_a_pipe(monkeypatch)

    code, printed, err = out(
        run, capsys, "memory", "delete", "agent", AGENT, "999999999", "--force"
    )

    assert (code, printed) == (1, "")
    assert "no fact of that number" in err
    assert "999999999" not in err


# What leaves this machine


def test_no_request_carries_a_fact_or_a_key_in_its_target(
    run, capsys, thread: str
) -> None:
    """The transport half of finding 8, recorded rather than reasoned
    about: what a proxy and an access log keep is the method and the
    target, so both values travel in a body and neither reaches a path
    or a query string.
    """
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "PUT":
            return httpx.Response(
                200,
                json={
                    "id": 1,
                    "fact": SENTINEL,
                    "at": "2026-08-30T10:00:00+00:00",
                    "forgotten_at": None,
                    "forgotten_in": None,
                },
            )
        return httpx.Response(200, json={"state": 1})

    answering(run, handler)

    capsys.readouterr()
    assert run("memory", "set", "agent", AGENT, "1", stdin=f"{SENTINEL}\n") == 0
    assert run("memory", "delete", "conversation", thread, stdin=f"{SENTINEL}\n") == 0

    assert len(seen) == 2
    for request in seen:
        assert SENTINEL not in str(request.url)
        assert SENTINEL in request.content.decode("utf-8")
