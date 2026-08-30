"""The `session` noun: four verbs over the API, and what they print.

The one part of this grammar that reads a schema the domain
configuration knows nothing about, and it reads it the same way as
everything else here: as requests through the client seam, against a
server built per command. There is no local-database path and there is
not going to be one (#281, #282), so a store is seeded with the real
writer and the commands are held to what the API answers about it.

Two properties this file exists for, beyond the round trips:

- **Printed content cannot steer a terminal.** A title, an agent name
  and a board's self-description all originate in what a room said to a
  device or what an operator typed, so every cell and every block line
  goes through the merged `printable` bounding. The tests plant ANSI
  escapes, control characters, tabs and newlines and compare the two
  streams byte for byte at a terminal and through a pipe.
- **A destructive verb asks, and its refusals name no value.** The two
  erasures are registered destructive, so they confirm at a terminal and
  take `--force`; what they say when they refuse is a fixed sentence,
  and the sentinel is hunted through stderr and the log.
"""

import contextlib
import datetime as dt
import io
import logging
from pathlib import Path
from typing import Any

import pytest

from tests.support.config_cli import runner
from tests.support.configs import DEVICE_MAC
from vinga_server import logs
from vinga_server.config.models import DatabaseConfig
from vinga_server.conversations.records import TurnRecord
from vinga_server.conversations.store import ConversationStore

NOW = dt.datetime(2026, 8, 15, 12, 0, tzinfo=dt.UTC)

FIRST = "9f0c1d2e3a4b5c6d7e8f90a1b2c3d4e5"

SECOND = "1a2b3c4d5e6f708192a3b4c5d6e7f809"

OTHER_DEVICE = "11:22:33:44:55:66"

# Shaped so a substring check for it cannot match by accident, and
# planted where a deletion's refusal would carry it out.
SENTINEL = "sk-test-6b2e01cf-never-a-real-credential"

# Everything a terminal reads as an instruction rather than as text: a
# colour change, a cursor move, a bell, a tab, a carriage return and a
# newline. Planted in an utterance, because that is where text this
# server never wrote comes from.
STEERING = "\x1b[31mred\x1b[0m\x07\tone\rtwo\nthree"


@pytest.fixture
def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    return runner(monkeypatch)


class Terminal(io.StringIO):
    """A stdin that says it is a terminal, which is the one thing the
    confirmation branches on."""

    def isatty(self) -> bool:
        return True


def at_a_terminal(monkeypatch: pytest.MonkeyPatch, typed: str) -> None:
    monkeypatch.setattr("sys.stdin", Terminal(typed))


def through_a_pipe(monkeypatch: pytest.MonkeyPatch, piped: str = "") -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(piped))


def manifest(device: str, started_at: str, agent: str = "sam") -> dict[str, Any]:
    return {
        "started_at": started_at,
        "server": {"version": "0.1.0", "revision": "abc1234"},
        "device": {"mac": device, "client": agent},
        "protocol": "1",
        "agent": agent,
        "agents": [agent],
        "providers": {"llm": {"name": "claude", "type": "anthropic"}},
    }


def recorded(
    session: str,
    conversation: str = FIRST,
    heard: str = "turn the light on",
    device: str = DEVICE_MAC.lower(),
    started_at: str = "2026-08-15T10:00:00+00:00",
    agent: str = "sam",
) -> None:
    """One recorded session, written the way the server writes one."""
    store = ConversationStore(DatabaseConfig(), now=lambda: NOW, retention_days=0)
    store.start()
    try:
        store.open_session(session, 100.0, manifest(device, started_at, agent))
        store.record_event(session, "heard", logging.INFO, {"duration_s": 1.0}, 101.0)
        store.record_turn(
            session,
            TurnRecord(
                at=101.2,
                conversation=conversation,
                agent=agent,
                heard=heard,
                reply="Done.",
            ),
        )
        store.close_session(session, duration_s=2.0, reason="client")
    finally:
        store.stop()


def _leaked(caplog: pytest.LogCaptureFixture) -> str:
    """Everything this server and this command logged, in both shipped
    formats.

    Filtered to this project's own channels, and the reason is the test
    environment rather than a weakening of the claim: Starlette's
    TestClient is built on a vendored `httpx2` whose logger the CLI's
    own quieting does not name, and what it writes is the request line
    of the caller's own terminal rather than anything this code emitted.
    Against a real server the CLI quiets `httpx` and `httpcore` around
    every request, which is the same property on the shipped path.
    """
    return "".join(
        record.getMessage() + str(record.__dict__) + logs.JsonFormatter().format(record)
        for record in caplog.records
        if record.name.startswith("vinga_server")
    )


def out(run, capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    capsys.readouterr()
    code = run(*argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# The reads


def test_the_listing_is_a_borderless_table_with_its_headings(run, capsys) -> None:
    """Columns rather than a document, because the question a listing
    answers is which of several sessions is the one wanted, and that is
    read across a line."""
    recorded("alpha")

    code, printed, err = out(run, capsys, "session", "list")

    assert (code, err) == (0, "")
    lines = printed.splitlines()
    assert lines[0].split() == [
        "SESSION",
        "DEVICE",
        "AGENT",
        "STARTED",
        "CLOSED",
        "REASON",
        "TURNS",
    ]
    assert lines[1].split()[0] == "alpha"
    assert lines[1].split()[1] == DEVICE_MAC.lower()
    assert lines[1].split()[-1] == "1"
    # No borders and no trailing whitespace: a line ends where its last
    # cell does.
    assert all(line == line.rstrip() for line in lines)


def test_an_empty_listing_says_so_rather_than_printing_a_heading(run, capsys) -> None:
    """A table with a heading and no rows reads as a rendering that
    failed. The sentence says what an empty answer means here."""
    code, printed, err = out(run, capsys, "session", "list")

    assert (code, err) == (0, "")
    assert "recorded no sessions" in printed
    assert "SESSION" not in printed


def test_the_device_filter_narrows_the_listing(run, capsys) -> None:
    """A flag rather than an address, because what it names is which
    board's sessions to show rather than one session."""
    recorded("alpha")
    recorded("beta", SECOND, device=OTHER_DEVICE)

    code, printed, _ = out(run, capsys, "session", "list", "--device", OTHER_DEVICE)

    assert code == 0
    assert "beta" in printed
    assert "alpha" not in printed


def test_the_limit_is_the_apis_rule_and_the_apis_sentence(run, capsys) -> None:
    """The CLI holds no second parser in front of the API's: what a
    limit has to be is one rule with one refusal, and the refusal names
    the rule rather than what was typed."""
    recorded("alpha")

    code, _, err = out(run, capsys, "session", "list", "--limit", SENTINEL)

    assert code == 1
    assert "limit has to be a whole number" in err
    assert SENTINEL not in err


def test_show_prints_the_detail_block(run, capsys) -> None:
    """A block because half of what a session row carries is a list or
    a nested object, and a column holding one is a column that wraps."""
    recorded("alpha")

    code, printed, err = out(run, capsys, "session", "show", "alpha")

    assert (code, err) == (0, "")
    assert printed.startswith("session: alpha\n")
    assert "  agent: sam\n" in printed
    assert "  turns: 1\n" in printed
    assert "  metrics: yes\n" in printed
    assert "  close_reason: client\n" in printed


def test_show_of_an_unknown_session_is_the_apis_own_sentence(run, capsys) -> None:
    """One vocabulary whichever way an operator reached the command: the
    refusal is the server's, passed through, and it names no id."""
    code, printed, err = out(run, capsys, "session", "show", SENTINEL)

    assert (code, printed) == (1, "")
    assert "no session of that id" in err
    assert SENTINEL not in err


# What a null is, and what content cannot do


def test_a_null_cell_is_the_fixed_placeholder(run, capsys) -> None:
    """A session that never closed has no reason and no closing stamp,
    which is an ordinary state rather than a rendering that failed."""
    store = ConversationStore(DatabaseConfig(), now=lambda: NOW, retention_days=0)
    store.start()
    store.open_session("alpha", 100.0, manifest(DEVICE_MAC.lower(), NOW.isoformat()))
    store.stop()

    code, printed, _ = out(run, capsys, "session", "list")

    assert code == 0
    cells = printed.splitlines()[1].split()
    assert cells[-2:] == ["-", "0"]


@pytest.mark.parametrize("verb", ["list", "show"])
def test_planted_control_content_cannot_steer_a_terminal(run, capsys, verb: str) -> None:
    """The determinism rule with no content exemption. An agent name is
    an operator's word and reaches both surfaces, so a name carrying
    escapes, control characters, a tab and a newline is planted and the
    output is held to carrying none of them.

    Bounded rather than stripped: an unprintable becomes a question
    mark, because something that arrived mangled should read as mangled
    rather than silently disappear.
    """
    recorded("alpha", agent=STEERING)

    argv = ("session", verb, *(() if verb == "list" else ("alpha",)))
    code, printed, err = out(run, capsys, *argv)

    assert (code, err) == (0, "")
    assert "\x1b" not in printed
    assert "\x07" not in printed
    assert "\t" not in printed
    assert "\r" not in printed
    # One line per row or per field, and the count is the renderer's
    # alone: nothing an utterance carries adds a line.
    assert len(printed.splitlines()) == (2 if verb == "list" else 17)
    assert "?" in printed


@pytest.mark.parametrize("verb", ["list", "show"])
def test_the_output_is_the_same_bytes_at_a_terminal_and_through_a_pipe(
    run, monkeypatch: pytest.MonkeyPatch, verb: str
) -> None:
    """No colour and no terminal-dependent rendering anywhere in these
    commands, so redirecting the output changes nothing about it.

    Run twice against streams that answer `isatty` differently and
    compared byte for byte, rather than asserted in prose: the claim is
    that nothing here asks, and the only way to hold it is to give two
    different answers and get one output.
    """
    recorded("alpha", agent=STEERING)
    argv = ("session", verb, *(() if verb == "list" else ("alpha",)))
    through_a_pipe(monkeypatch)

    piped = _captured(run, argv, terminal=False)
    terminal = _captured(run, argv, terminal=True)

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
    """What one command wrote to each stream, with both of them saying
    whether they are a terminal."""
    printed, errors = _Stream(terminal), _Stream(terminal)
    with contextlib.redirect_stdout(printed), contextlib.redirect_stderr(errors):
        assert run(*argv) == 0
    return printed.getvalue(), errors.getvalue()


# The two erasures


def test_delete_confirms_at_a_terminal_and_answers_the_counts(
    run, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Registered destructive, so it asks; and what it prints afterwards
    is what it took, per table, because a caller cannot know what a
    session was holding."""
    recorded("alpha")
    at_a_terminal(monkeypatch, "y\n")

    code, printed, err = out(run, capsys, "session", "delete", "alpha")

    assert code == 0
    assert "Type y to go ahead" in err
    assert printed.splitlines() == [
        "sessions: 1",
        "turns: 1",
        "tool_invocations: 0",
        "events: 1",
        "conversations: 1",
        "milestones: 0",
        "state: 0",
        "held_facts: 0",
    ]
    assert out(run, capsys, "session", "list")[1].startswith("this server has recorded no")


def test_a_declined_confirmation_deletes_nothing(
    run, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded("alpha")
    at_a_terminal(monkeypatch, "n\n")

    code, printed, err = out(run, capsys, "session", "delete", "alpha")

    assert (code, printed) == (1, "")
    assert "nothing was deleted" in err
    assert "alpha" in out(run, capsys, "session", "list")[1]


def test_force_answers_the_question_without_asking_it(
    run, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded("alpha")
    at_a_terminal(monkeypatch, "")

    code, printed, err = out(run, capsys, "session", "delete", "alpha", "--force")

    assert code == 0
    assert "Type y" not in err
    assert printed.startswith("sessions: 1\n")


def test_a_purge_by_device_and_day_takes_only_what_both_name(
    run, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The selectors combine with AND, which is the safe direction for
    something with no undo: a purge always names less than each selector
    alone would."""
    recorded("old-here", started_at="2026-08-01T10:00:00+00:00")
    recorded(
        "old-there",
        SECOND,
        device=OTHER_DEVICE,
        started_at="2026-08-01T10:00:00+00:00",
    )
    recorded(
        "new-here",
        "3c4d5e6f708192a3b4c5d6e7f8091a2b",
        started_at="2026-08-20T10:00:00+00:00",
    )
    through_a_pipe(monkeypatch)

    code, printed, err = out(
        run,
        capsys,
        "session",
        "purge",
        "--device",
        DEVICE_MAC.lower(),
        "--before",
        "2026-08-10",
    )

    assert (code, err) == (0, "")
    assert printed.startswith("sessions: 1\n")
    left = out(run, capsys, "session", "list")[1]
    assert "old-there" in left and "new-here" in left and "old-here" not in left


def test_a_purge_with_no_selector_is_refused_and_takes_nothing(
    run, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole store is not something this command can ask for. One
    home for the rule, which is the endpoint, and one sentence, which is
    the one it answers with."""
    recorded("alpha")
    at_a_terminal(monkeypatch, "y\n")

    code, printed, err = out(run, capsys, "session", "purge")

    assert (code, printed) == (1, "")
    assert "at least one of session, device or before" in err
    assert "alpha" in out(run, capsys, "session", "list")[1]


def test_a_refused_erasure_says_nothing_it_was_handed(
    run, capsys, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Every value these two verbs take is typed by an operator, which
    is exactly where a credential lands when a command is mistyped. The
    sentinel is planted in each and hunted through both streams and the
    log."""
    through_a_pipe(monkeypatch)

    with caplog.at_level(logging.DEBUG):
        by_id = out(run, capsys, "session", "delete", SENTINEL)
        by_day = out(run, capsys, "session", "purge", "--before", SENTINEL)

    assert (by_id[0], by_id[1]) == (1, "")
    assert (by_day[0], by_day[1]) == (1, "")
    assert SENTINEL not in by_id[2]
    assert SENTINEL not in by_day[2]
    assert "before has to be a calendar day" in by_day[2]
    assert SENTINEL not in _leaked(caplog)
