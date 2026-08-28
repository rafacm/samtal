"""The `conversation` noun: three verbs over the API, and what they
print.

The store's other projection in front of an operator, driven the way
`session` is next door: requests through the client seam, against a
server built per command, with the record seeded by the real writer.
There is no local-database path here and there is not going to be one
(#281, #282).

What this file exists for beyond the round trips is the part the session
verbs did not have to answer. A session row is short values a server
minted; a thread's title and its dialogue are what a room said into a
microphone, so this is the surface where printed content really is
content:

- **Content cannot steer a terminal.** Titles and dialogue go through
  the merged `printable` bounding, embedded newlines included, so the
  line structure of a listing and of a dialogue block is the renderer's
  alone. The tests plant escapes, control characters, tabs and newlines
  and compare both streams byte for byte.
- **A null title is the fixed placeholder.** A thread recorded under
  text-off has no name, which is an ordinary state rather than a
  rendering that failed.
- **`show` is one command and two reads.** The header comes from the
  detail and the dialogue from the timeline beside it, because a column
  holding an utterance is a column that wraps.
- **Only this API's own refusal is relayed.** These verbs print what a
  refusal said, so what answers has to be shown to be this API before
  its words reach a terminal: the media type, the shape, the status and
  the title all have to agree, and a body that fails any of them is the
  fixed sentence with nothing of itself in it.
"""

import contextlib
import datetime as dt
import io
import logging
from pathlib import Path
from typing import Any

import httpx
import pytest

from tests.support.config_cli import runner
from tests.support.configs import DEVICE_MAC
from vinga_server import logs
from vinga_server.config import cli
from vinga_server.config.models import DatabaseConfig
from vinga_server.config.responses import PROBLEM_MEDIA_TYPE
from vinga_server.conversations.records import TurnRecord
from vinga_server.conversations.store import ConversationStore

NOW = dt.datetime(2026, 8, 15, 12, 0, tzinfo=dt.UTC)

FIRST = "9f0c1d2e3a4b5c6d7e8f90a1b2c3d4e5"

SECOND = "1a2b3c4d5e6f708192a3b4c5d6e7f809"

# Shaped so a substring check for it cannot match by accident, and
# planted where a refusal's own input can carry it out.
SENTINEL = "sk-test-8c4a17be-never-a-real-credential"

# Everything a terminal reads as an instruction rather than as text: a
# colour change, a cursor move, a bell, a tab, a carriage return and a
# newline. Planted in an utterance, which is where text this server
# never wrote comes from, and which is what a title is made of.
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


def manifest(agent: str) -> dict[str, Any]:
    return {
        "started_at": "2026-08-15T10:00:00+00:00",
        "server": {"version": "0.1.0", "revision": "abc1234"},
        "device": {"mac": DEVICE_MAC.lower(), "client": "test"},
        "protocol": "1",
        "agent": agent,
        "agents": [agent],
        "providers": {"llm": {"name": "claude", "type": "anthropic"}},
    }


def recorded(
    session: str,
    conversation: str = FIRST,
    said: tuple[tuple[str, str], ...] = (("turn the light on", "Done."),),
    agent: str = "sam",
    at: dt.datetime = NOW,
    text: bool = True,
) -> None:
    """One thread, written the way the server writes one."""
    store = ConversationStore(
        DatabaseConfig(), now=lambda: at, retention_days=0, text=text
    )
    store.start()
    try:
        store.open_session(session, 100.0, manifest(agent))
        for index, (heard, reply) in enumerate(said):
            store.record_turn(
                session,
                TurnRecord(
                    at=101.0 + index,
                    conversation=conversation,
                    agent=agent,
                    heard=heard,
                    reply=reply,
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
    of the caller's own terminal.
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


# The listing


def test_the_listing_is_a_borderless_table_with_its_headings(run, capsys) -> None:
    """Columns, because the question a listing answers is which of
    several threads is the one wanted, and that is read across a line."""
    recorded("alpha")

    code, printed, err = out(run, capsys, "conversation", "list")

    assert (code, err) == (0, "")
    lines = printed.splitlines()
    assert lines[0].split() == [
        "CONVERSATION",
        "AGENT",
        "TITLE",
        "LAST-ACTIVE",
        "TURNS",
    ]
    cells = lines[1].split()
    assert cells[0] == FIRST
    assert cells[1] == "sam"
    assert cells[-1] == "1"
    # No borders and no trailing whitespace: a line ends where its last
    # cell does.
    assert all(line == line.rstrip() for line in lines)


def test_an_empty_listing_says_so_rather_than_printing_a_heading(run, capsys) -> None:
    """A table with a heading and no rows reads as a rendering that
    failed. The sentence says what an empty answer means here."""
    code, printed, err = out(run, capsys, "conversation", "list")

    assert (code, err) == (0, "")
    assert "recorded no conversations" in printed
    assert "CONVERSATION" not in printed


def test_the_agent_filter_narrows_the_listing(run, capsys) -> None:
    """A flag rather than an address, because what it names is whose
    threads to show rather than one thread."""
    recorded("alpha", FIRST, agent="sam")
    recorded("beta", SECOND, agent="nadia")

    code, printed, _ = out(run, capsys, "conversation", "list", "--agent", "nadia")

    assert code == 0
    assert SECOND in printed
    assert FIRST not in printed


def test_the_limit_is_the_apis_rule_and_the_apis_sentence(run, capsys) -> None:
    """One vocabulary whichever way an operator reached the command: the
    CLI holds no second parser in front of the API's, and the refusal
    names the rule rather than what was typed."""
    recorded("alpha")

    code, _, err = out(run, capsys, "conversation", "list", "--limit", SENTINEL)

    assert code == 1
    assert "limit has to be a whole number" in err
    assert SENTINEL not in err


def test_a_null_title_is_the_fixed_placeholder(run, capsys) -> None:
    """A thread recorded under text-off has no name, because a title IS
    the first utterance and none was stored. An ordinary state, and an
    empty cell would read as a column that failed to render."""
    recorded("alpha", text=False)

    code, printed, _ = out(run, capsys, "conversation", "list")

    assert code == 0
    cells = printed.splitlines()[1].split()
    assert cells[2] == "-"


# The detail, which is a header and a dialogue


def test_show_prints_the_header_and_then_the_dialogue(run, capsys) -> None:
    """One command and two reads: the thread's own fields, and then what
    was said in it, oldest first as speaker-labelled blocks."""
    recorded(
        "alpha",
        said=(("what is the weather like", "Sunny."), ("and tomorrow", "Rain.")),
    )

    code, printed, err = out(run, capsys, "conversation", "show", FIRST)

    assert (code, err) == (0, "")
    assert printed.startswith(f"conversation: {FIRST}\n")
    assert "  agent: sam\n" in printed
    assert "  title: what is the weather like\n" in printed
    assert "  created: 2026-08-15T12:00:00+00:00\n" in printed
    assert "  last active: 2026-08-15T12:00:00+00:00\n" in printed
    # And the dialogue under it, in the order it was said.
    assert "you: what is the weather like\nsam: Sunny.\n" in printed
    assert "you: and tomorrow\nsam: Rain.\n" in printed
    assert printed.index("what is the weather like\nsam:") < printed.index("and tomorrow")


def test_a_whole_thread_prints_no_incomplete_line(run, capsys) -> None:
    """The ordinary case, and the reason the line is conditional: a
    thread with nothing lost is every thread, and a line saying so on
    each of them would make the one that matters harder to see."""
    recorded("alpha")

    printed = out(run, capsys, "conversation", "show", FIRST)[1]

    assert "incomplete" not in printed


def test_show_of_an_unknown_thread_is_the_apis_own_sentence(run, capsys) -> None:
    """The refusal is the server's, passed through, and it names no id."""
    code, printed, err = out(run, capsys, "conversation", "show", SENTINEL)

    assert (code, printed) == (1, "")
    assert "no conversation of that id" in err
    assert SENTINEL not in err


def test_a_thread_with_no_stored_text_prints_the_placeholders(run, capsys) -> None:
    """Text-off leaves the turns and none of the words in them, which is
    the setting doing what it says. The dialogue is still printed, with
    the fixed placeholder on both speakers: the turn happened, and
    nothing of it was stored."""
    recorded("alpha", text=False)

    printed = out(run, capsys, "conversation", "show", FIRST)[1]

    assert "  title: -\n" in printed
    assert "you: -\nsam: -\n" in printed


# What content cannot do


@pytest.mark.parametrize("verb", ["list", "show"])
def test_planted_control_content_cannot_steer_a_terminal(run, capsys, verb: str) -> None:
    """The determinism rule with no content exemption, on the surface
    that exists to print content. An utterance becomes a title and a
    dialogue line, so one carrying escapes, control characters, a tab
    and a newline is planted and the output is held to carrying none of
    them.

    Bounded rather than stripped: an unprintable becomes a question
    mark, because something that arrived mangled should read as mangled
    rather than silently disappear.
    """
    recorded("alpha", said=((STEERING, STEERING),))

    argv = ("conversation", verb, *(() if verb == "list" else (FIRST,)))
    code, printed, err = out(run, capsys, *argv)

    assert (code, err) == (0, "")
    assert "\x1b" not in printed
    assert "\x07" not in printed
    assert "\t" not in printed
    assert "\r" not in printed
    # One line per row, or per header field and per speaker, and the
    # count is the renderer's alone: nothing an utterance carries adds a
    # line, however many newlines it holds.
    assert len(printed.splitlines()) == (2 if verb == "list" else 7)
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
    recorded("alpha", said=((STEERING, STEERING),))
    argv = ("conversation", verb, *(() if verb == "list" else (FIRST,)))
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


# The erasure


def test_delete_confirms_at_a_terminal_and_answers_the_counts(
    run, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Registered destructive, so it asks; and the counts it prints are
    the four a thread erasure takes, with nothing said about the
    sessions and the events it deliberately leaves standing."""
    recorded("alpha")
    at_a_terminal(monkeypatch, "y\n")

    code, printed, err = out(run, capsys, "conversation", "delete", FIRST)

    assert code == 0
    assert "Type y to go ahead" in err
    assert printed.splitlines() == [
        "turns: 1",
        "tool_invocations: 0",
        "conversations: 1",
        "milestones: 0",
    ]
    assert out(run, capsys, "conversation", "list")[1].startswith(
        "this server has recorded no"
    )
    # The session it was spoken in is untouched, which is the asymmetry
    # between the two erasures.
    assert "alpha" in out(run, capsys, "session", "list")[1]


def test_a_declined_confirmation_deletes_nothing(
    run, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded("alpha")
    at_a_terminal(monkeypatch, "n\n")

    code, printed, err = out(run, capsys, "conversation", "delete", FIRST)

    assert (code, printed) == (1, "")
    assert "nothing was deleted" in err
    assert FIRST in out(run, capsys, "conversation", "list")[1]


def test_force_answers_the_question_without_asking_it(
    run, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded("alpha")
    at_a_terminal(monkeypatch, "")

    code, printed, err = out(run, capsys, "conversation", "delete", FIRST, "--force")

    assert code == 0
    assert "Type y" not in err
    assert printed.startswith("turns: 1\n")


def test_a_refused_erasure_says_nothing_it_was_handed(
    run, capsys, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The id this verb takes is typed by an operator, which is exactly
    where a credential lands when a command is mistyped. The sentinel is
    planted in it and hunted through both streams and the log."""
    through_a_pipe(monkeypatch)

    with caplog.at_level(logging.DEBUG):
        code, printed, err = out(run, capsys, "conversation", "delete", SENTINEL)

    assert (code, printed) == (1, "")
    assert "no conversation of that id" in err
    assert SENTINEL not in err
    assert SENTINEL not in _leaked(caplog)


def test_a_planted_title_never_reaches_a_refusal_or_the_log(
    run, capsys, caplog: pytest.LogCaptureFixture
) -> None:
    """The other direction: an utterance that became a title is content,
    so it belongs on the read surfaces that exist to show it and nowhere
    else. It is printed by `list` and by `show`, and it is absent from
    stderr and from every log record either command wrote."""
    recorded("alpha", said=((SENTINEL, "Done."),))

    with caplog.at_level(logging.DEBUG):
        listed = out(run, capsys, "conversation", "list")
        shown = out(run, capsys, "conversation", "show", FIRST)

    assert SENTINEL in listed[1]
    assert SENTINEL in shown[1]
    assert (listed[2], shown[2]) == ("", "")
    assert SENTINEL not in _leaked(caplog)


# What answered, and whether its words may be printed


def answering(
    monkeypatch: pytest.MonkeyPatch, status: int, body: dict[str, Any], media_type: str
) -> None:
    """Something in front of this API, answering every request with a
    body of its own.

    A mock transport rather than a route, because the point of these two
    tests is a body no application in this repository would ever write:
    a proxy, a gateway or a captive portal is what puts one on the wire,
    and it can spell it however it likes.
    """

    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body, headers={"content-type": media_type})

    def factory(base_url: str, token: str) -> httpx.Client:
        return httpx.Client(base_url=base_url, transport=httpx.MockTransport(answer))

    monkeypatch.setattr(cli, "build_client", factory)


def test_a_json_body_with_a_detail_is_not_this_apis_refusal(
    run, capsys, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A JSON object with a string `detail` in it is a shape anything
    can write, and this command prints what it is given.

    So the media type is what decides: this API answers a refusal as
    `application/problem+json` and a middlebox answering
    `application/json` is not it, however the body is shaped. The
    planted value is a credential and terminal-steering bytes, because
    what is being kept off the terminal is both.
    """
    answering(
        monkeypatch,
        404,
        {"detail": f"{SENTINEL}{STEERING}"},
        "application/json",
    )

    with caplog.at_level(logging.DEBUG):
        code, printed, err = out(run, capsys, "conversation", "list")

    assert (code, printed) == (1, "")
    assert "answered 404 with a body this client does not recognize" in err
    both = printed + err
    assert SENTINEL not in both
    assert "\x1b[" not in both
    assert "\x07" not in both
    assert SENTINEL not in _leaked(caplog)


def test_a_problem_body_that_disagrees_with_its_response_is_not_relayed(
    run, capsys, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The media type alone is not enough: it is a header, and whatever
    writes the body writes that too.

    So the body has to be the `Problem` shape, and its `status` and
    `title` have to be the response's own. This one is spelled exactly
    right and answered under the wrong status, which is what a refusal
    lifted from one response and replayed under another looks like.
    """
    answering(
        monkeypatch,
        404,
        {
            "title": "Internal Server Error",
            "status": 500,
            "detail": f"{SENTINEL}{STEERING}",
            "errors": [],
        },
        PROBLEM_MEDIA_TYPE,
    )

    with caplog.at_level(logging.DEBUG):
        code, printed, err = out(run, capsys, "conversation", "show", FIRST)

    assert (code, printed) == (1, "")
    assert "answered 404 with a body this client does not recognize" in err
    both = printed + err
    assert SENTINEL not in both
    assert "\x1b[" not in both
    assert "\x07" not in both
    assert SENTINEL not in _leaked(caplog)
