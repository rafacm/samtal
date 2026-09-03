"""The config command group, driven through its entry function.

This is the acceptance spine: the commands are the ones an operator
types, parsed by the real grammar, sent as real HTTP requests to the
real sub-application, handled by the real repository against a scratch
database. What replaces the socket is the client factory: Starlette's
TestClient is itself a synchronous `httpx.Client` subclass driving an
ASGI application through its own portal, so `cli.main()` stays the
unchanged synchronous entry point and nothing bridges an event loop.

The first test is the acceptance case: an empty database becomes a
working configuration through CLI calls alone, in the natural order,
with nothing wedging on the way. The rest is what has to hold around it,
one entity kind at a time: what a write is answered with, what a read
shows, what a delete refuses when something else names what it would
remove, and no failure path that lets a plaintext, a rejected fragment
or a traceback out. That per-kind behavior is what #139 made
descriptor-driven, and this file is where the claim that all five kinds
behave alike is kept.

Four neighbours hold the rest of the surface, split off by #139 along
the boundaries the production split produced, each named for the concern
it keeps: `test_config_cli_transport.py` (where a command is sent and
what it will not be sent over), `test_config_cli_rendering.py` (the four
answers the running server is asked for), `test_config_cli_secrets.py`
(a credential's whole life) and `test_config_cli_grammar.py` (the parse
and its exit codes).
"""

import io
import logging
import sys
from pathlib import Path

import pytest
from sqlalchemy import update

from tests.support.config_cli import (
    FRAGMENT_INPUT,
    FRAGMENT_TEXT,
    SECRET,
    runner,
)
from tests.support.config_cli import chain as _chain
from tests.support.config_cli import document as _document
from tests.support.config_cli import logged as _logged
from tests.support.config_cli import showing as _showing
from tests.support.notices import CHECK_IN, RELOAD, boundaries
from vinga_server.config import cli
from vinga_server.config.models import NOT_A_MAC, DatabaseConfig
from vinga_server.db import open_database, schema


@pytest.fixture
def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """One command run the way the entry point runs it, against a server
    of this test's own."""
    return runner(monkeypatch)


def test_an_empty_database_becomes_a_working_configuration(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The natural order, end to end: providers, MCP servers, agents,
    devices, default agent. Every intermediate state here would fail the
    boot-only completeness rule, and none of the writes may be refused."""
    claude = "type: anthropic\nmodel: m\n"
    assert run("provider", "set", "llm", "claude", "-f", "-", stdin=claude) == 0
    assert run("provider", "set", "asr", "whisper", "-f", "-", stdin="type: mock\n") == 0
    assert run("provider", "set", "tts", "voice", "-f", "-", stdin="type: mock\n") == 0
    assert run("provider", "set", "vad", "ears", "-f", "-", stdin="type: mock\n") == 0
    assert run(
        "mcp-server", "set", "home",
        "-f",
        "-",
        stdin="transport: stdio\ncommand: uvx\negress: false\n",
    ) == 0
    assert run(
        "agent-defaults", "set",
        "-f",
        "-",
        stdin="llm: claude\nasr: whisper\ntts: voice\nvad: ears\nmcp: [home]\n",
    ) == 0
    # The agent no default_agent names yet: the write that would deadlock
    # if completeness were enforced here.
    assert run("agent", "set", "sam", "-f", "-", stdin="prompt: You are Sam.\n") == 0
    assert run("device", "bind", "AA-BB-CC-DD-EE-FF", "sam") == 0
    assert run("default-agent", "set", "sam") == 0
    capsys.readouterr()

    assert run("show") == 0
    shown = _document(capsys.readouterr().out)

    assert shown["providers"]["llm"]["claude"] == {"type": "anthropic", "model": "m"}
    assert shown["mcp_servers"]["home"]["command"] == "uvx"
    assert shown["agent_defaults"]["mcp"] == ["home"]
    assert shown["agents"]["sam"]["prompt"] == "You are Sam."
    assert shown["devices"] == {"aa:bb:cc:dd:ee:ff": ["sam"]}
    assert shown["default_agent"] == "sam"


def test_a_fragment_can_come_from_a_file(
    run, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fragment = tmp_path / "claude.yaml"
    fragment.write_text("type: anthropic\nmodel: m\n", encoding="utf-8")

    assert run("provider", "set", "llm", "claude", "-f", str(fragment)) == 0
    capsys.readouterr()

    assert run("provider", "show", "llm", "claude") == 0
    assert _document(capsys.readouterr().out) == {"type": "anthropic", "model": "m"}


# A fragment file that gives no fragment
#
# Four ways one can fail and one sentence each, chosen by the class of
# the failure. None of them holds the path, the library's `strerror` or
# a byte of the file (#289): the path is typed, `-f` is one option away
# from the secret commands, and a file that will not decode is as likely
# to be key material as a mistyped document.
#
# The table below is the whole of `_FILE_PROBLEMS`, one row per class it
# answers, each raised from the read itself rather than arranged on a
# filesystem: a row whose failure a filesystem will not reliably produce
# is a row that goes untested, and a test that skips itself on the
# machine that runs it proves nothing on any other. What is asserted is
# the whole of what an operator gets, so a row deleted from the
# production table, or one left to fall through to the general sentence,
# is a failure here rather than a silence.
#
# Every injected exception carries the sentinel where the real one
# carries a value: an `OSError`'s `filename`, a decode error's buffer.
# The two real-filesystem cases below the table are what says the
# classes are the ones a filesystem actually raises.

FRAGMENT_NAME = "fragment.yaml"

FILE_FAILURES = [
    ("nothing at the path", FileNotFoundError(2, "No such file", SECRET), "FILE_NOT_FOUND"),
    (
        "a path component that is not a directory",
        NotADirectoryError(20, "Not a directory", SECRET),
        "FILE_NOT_FOUND",
    ),
    (
        "a directory where a file belongs",
        IsADirectoryError(21, "Is a directory", SECRET),
        "FILE_NOT_READABLE",
    ),
    (
        "a file this user may not read",
        PermissionError(13, "Permission denied", SECRET),
        "FILE_NOT_READABLE",
    ),
    (
        "bytes that are not UTF-8",
        UnicodeDecodeError("utf-8", f"prompt: {SECRET}\n".encode() + b"\xff", 41, 42, "invalid"),
        "FILE_NOT_TEXT",
    ),
    ("anything else the system says", OSError(5, "Input/output error", SECRET), "FILE_UNREADABLE"),
]


@pytest.fixture
def refusing_read(monkeypatch: pytest.MonkeyPatch):
    """Make the fragment file's own read raise, and leave every other
    read in the process alone: the command reads its configuration file
    through the same call."""

    def arrange(raised: BaseException) -> None:
        real = Path.read_text

        def read_text(self: Path, *args: object, **kwargs: object) -> str:
            if self.name == FRAGMENT_NAME:
                raise raised
            return real(self, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(Path, "read_text", read_text)

    return arrange


@pytest.mark.parametrize(
    ("raised", "sentence"),
    [(raised, sentence) for _, raised, sentence in FILE_FAILURES],
    ids=[what for what, _, _ in FILE_FAILURES],
)
def test_every_way_a_fragment_file_fails_has_one_sentence_of_its_own(
    run,
    refusing_read,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    raised: BaseException,
    sentence: str,
) -> None:
    refusing_read(raised)
    fragment = tmp_path / FRAGMENT_NAME

    with caplog.at_level(logging.DEBUG):
        assert run("agent", "set", "sam", "-f", str(fragment)) == 1

    captured = capsys.readouterr()
    # The whole of stderr, not a substring of it: what is under test is
    # that this row has a sentence rather than the next row's.
    assert captured.err == getattr(cli, sentence) + "\n"
    assert captured.out == ""
    assert caplog.records == []
    assert SECRET not in _logged(caplog)


@pytest.mark.parametrize(
    ("raised", "sentence"),
    [(raised, sentence) for _, raised, sentence in FILE_FAILURES],
    ids=[what for what, _, _ in FILE_FAILURES],
)
def test_no_fragment_file_refusal_carries_what_the_failure_held(
    refusing_read, tmp_path: Path, raised: BaseException, sentence: str
) -> None:
    """White-box for the chain, the way the parser's refusals are
    checked below: what an operating-system error holds is the path it
    was given, what a decode error holds is the bytes it was given, and
    neither is printed, so neither can be asserted through the runner.
    """
    refusing_read(raised)

    with pytest.raises(cli.ConfigError) as caught:
        cli._file(str(tmp_path / FRAGMENT_NAME))

    assert str(caught.value) == getattr(cli, sentence)
    assert SECRET not in _chain(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_a_missing_fragment_file_really_does_take_that_row(
    run, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The table above injects the classes; these two say a filesystem
    raises them, so the rows are answering real failures rather than
    exceptions the test made up. A path that is not there and a
    directory where a file belongs are the two every filesystem
    produces the same way."""
    missing = tmp_path / f"{SECRET}.yaml"

    assert run("agent", "set", "sam", "-f", str(missing)) == 1

    captured = capsys.readouterr()
    assert captured.err == cli.FILE_NOT_FOUND + "\n"
    assert str(missing) not in captured.err
    assert SECRET not in captured.err + captured.out


def test_a_directory_where_a_fragment_belongs_really_does_take_its_row(
    run, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = tmp_path / f"{SECRET}.yaml"
    directory.mkdir()

    assert run("agent", "set", "sam", "-f", str(directory)) == 1

    captured = capsys.readouterr()
    assert captured.err == cli.FILE_NOT_READABLE + "\n"
    assert "Is a directory" not in captured.err
    assert SECRET not in captured.err + captured.out


def test_a_fragment_file_that_is_not_text_is_refused_rather_than_thrown(
    run, tmp_path: Path, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    """The half of #289 that was not about echoing, over a real file:
    `UnicodeDecodeError` is a `ValueError` rather than an `OSError`, so a
    file that is not UTF-8 used to leave as a traceback, and the
    exception it left as holds the buffer it could not decode.

    The sentinel is inside the file, on the line above the bytes that
    break the decoding, which is where an operator's would be: a
    secrets file pointed at by mistake holds the credential already.
    """
    binary = tmp_path / "keys.bin"
    binary.write_bytes(f"prompt: {SECRET}\n".encode() + b"\xff\xfe\x80\x00")

    with caplog.at_level(logging.DEBUG):
        assert run("agent", "set", "sam", "-f", str(binary)) == 1

    captured = capsys.readouterr()
    assert captured.err == cli.FILE_NOT_TEXT + "\n"
    assert captured.out == ""
    assert SECRET not in captured.err
    assert "Traceback" not in captured.err
    assert SECRET not in _logged(caplog)


def test_a_fragment_file_that_will_not_parse_is_not_named_either(
    run, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The sibling of the three above, on the path that opens the file
    and reads it: what the parser could not read is located by a line
    and a column, and the file is called what this module calls it
    rather than what it was typed as."""
    broken = tmp_path / f"{SECRET}.yaml"
    broken.write_text("prompt: A\nmcp: [\n", encoding="utf-8")

    assert run("agent", "set", "sam", "-f", str(broken)) == 1

    captured = capsys.readouterr()
    assert f"invalid YAML in {cli.FILE_SOURCE} at line" in captured.err
    assert SECRET not in captured.err
    assert str(broken) not in captured.err


def test_every_mutating_command_says_when_the_write_applies(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """A write is stored where the server reads it later, which makes a
    write that quietly waits the one thing about that design an operator
    can be caught by.

    The boundary each write names is what an operator acts on, so that
    is what is asserted; the agent's notice is also the whole of what
    the command printed, which is what pins that a write says one thing
    and not a paragraph of them."""
    run("provider", "set", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")
    assert boundaries(capsys.readouterr().err) == {RELOAD}

    run("agent", "set", "sam", "-f", "-", stdin="llm: claude\n")
    written = capsys.readouterr().err
    assert boundaries(written) == {RELOAD}
    assert written.count("\n") == 1

    run("default-agent", "set", "sam")
    # The application this fixture builds is told of no servable agents,
    # so the default agent it just named is one this server is not
    # serving, and the acknowledgement names the reload that would
    # install it beside the check-in the row is live at.
    assert boundaries(capsys.readouterr().err) == {CHECK_IN, RELOAD}

    # A read is not a write, and says nothing.
    run("list")
    assert capsys.readouterr().err == ""


def test_a_device_write_says_the_device_meets_it_itself(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The one kind nothing has to be asked to apply, printed verbatim
    from what the API answered: a running server reads the devices table
    as a device asks, so a delete reaches the device at its next
    check-in and needs neither a reload nor a start."""
    run("provider", "set", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")
    run("agent", "set", "sam", "-f", "-", stdin="llm: claude\n")
    run("device", "bind", "aa:bb:cc:dd:ee:ff", "sam")
    capsys.readouterr()

    assert run("device", "delete", "aa:bb:cc:dd:ee:ff") == 0

    assert boundaries(capsys.readouterr().err) == {CHECK_IN}


def _an_agent(run) -> None:
    run("provider", "set", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")
    run("agent", "set", "sam", "-f", "-", stdin="llm: claude\n")


# Shared prompt fragments
#
# The exact CLI input and the exact rendering, pinned: a fragment is
# written as `text: ...` like every other entity's body, and what comes
# back out is the same bytes, because those bytes are what the model is
# given.


def test_a_prompt_fragment_is_written_shown_and_listed(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("prompt-fragment", "set", "household", "-f", "-", stdin=FRAGMENT_INPUT) == 0
    written = capsys.readouterr()
    assert written.out == "wrote prompt-fragment household\n"
    # A fragment is prompt text, which a reload puts in front of the
    # next activation of every agent that includes it.
    assert boundaries(written.err) == {RELOAD}

    assert run("prompt-fragment", "show", "household") == 0
    assert _document(capsys.readouterr().out) == {"text": FRAGMENT_TEXT}

    assert run("list") == 0
    listed = capsys.readouterr().out
    assert "prompt_fragments:" in listed
    assert f"  household ({len(FRAGMENT_TEXT)} characters)" in listed

    assert run("show") == 0
    assert _document(capsys.readouterr().out)["prompt_fragments"] == {
        "household": {"text": FRAGMENT_TEXT}
    }


def test_an_agent_includes_a_fragment_and_reads_it_back(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The write, read-back loop across the two entities: the fragment
    exists first, the agent names it, and the include is echoed in the
    shape it was written in."""
    _an_agent(run)
    run("prompt-fragment", "set", "household", "-f", "-", stdin=FRAGMENT_INPUT)
    assert run(
        "agent", "set", "sam", "-f", "-", stdin="llm: claude\nprompt_includes: [household]\n"
    ) == 0
    capsys.readouterr()

    assert run("agent", "show", "sam") == 0

    assert _document(capsys.readouterr().out)["prompt_includes"] == ["household"]


def test_a_fragment_an_agent_includes_is_not_deleted_from_under_it(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    _an_agent(run)
    run("prompt-fragment", "set", "household", "-f", "-", stdin=FRAGMENT_INPUT)
    run("agent", "set", "sam", "-f", "-", stdin="llm: claude\nprompt_includes: [household]\n")
    capsys.readouterr()

    assert run("prompt-fragment", "delete", "household") == 1

    assert "prompt_includes" in capsys.readouterr().err


# Every section a command addresses one entry of, as the noun and the
# identity that address something nothing wrote, the sentence both verbs
# answer with,
# and the value that must not be printed. The names are credential
# shaped, because a command line is where a paste lands; a device is
# addressed by a MAC, which a credential-shaped argument never reaches
# this refusal as, so its own identity is the sentinel here. The
# argument that is not a MAC at all meets the shape refusal a step
# earlier, and has a test of its own below.
UNWRITTEN = [
    (("provider", "llm", SECRET), "providers", SECRET),
    (("provider", "llm", f"{SECRET}.pasted"), "providers", SECRET),
    (("mcp-server", SECRET), "mcp_servers", SECRET),
    (("prompt-fragment", SECRET), "prompt_fragments", SECRET),
    (("prompt-fragment", f"{SECRET}.pasted"), "prompt_fragments", SECRET),
    (("agent", SECRET), "agents", SECRET),
    (("device", "aa:bb:cc:dd:ee:ff"), "devices", "aa:bb:cc:dd:ee:ff"),
    # The same paste one argument to the left. A provider is addressed
    # by a stage and a name together, and a stage that is not one of the
    # four is refused before anything is looked up, on both paths.
    (("provider", SECRET, "claude"), "providers", SECRET),
]


@pytest.mark.parametrize(("addressed", "section", "sentinel"), UNWRITTEN)
def test_an_identity_that_addresses_nothing_is_refused_without_printing_it(
    run,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    addressed: tuple[str, ...],
    section: str,
    sentinel: str,
) -> None:
    """Every section and both verbs (#132).

    An identity that addresses nothing was typed rather than stored, so
    neither the read nor the delete repeats it: not on stderr, not on
    stdout, and not in a record either of them retained. That covers a
    name nothing wrote and a stage that is not a stage, which are the
    two ways a segment can address nothing.
    """
    noun, identity = addressed[0], addressed[1:]
    for verb in ("show", "delete"):
        with caplog.at_level(logging.DEBUG):
            assert run(noun, verb, *identity) == 1

        captured = capsys.readouterr()
        # The leak first and the section after it, so a failure here
        # says which of the two moved. The last line of the stream is
        # the refusal.
        assert sentinel not in captured.err
        assert sentinel not in captured.out
        assert captured.err.splitlines()[-1].startswith(f"{section}:")
        assert "Traceback" not in captured.err
        # This project's own records. The client's HTTP library writes
        # the URL it requested, which is the identity the operator just
        # typed going out over the loopback socket the CLI is talking to
        # itself on, not something a server retained.
        written = [r for r in caplog.records if r.name.startswith("vinga_server")]
        assert all(sentinel not in str(record.__dict__) for record in written)


# Every command that takes a MAC as an argument.
NOT_MACS = [
    ("device", "show"),
    ("device", "delete"),
    ("device", "bind"),
]


@pytest.mark.parametrize("argv", NOT_MACS, ids=[" ".join(argv) for argv in NOT_MACS])
def test_a_mac_that_is_not_a_mac_is_refused_without_printing_it(
    run,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    argv: tuple[str, ...],
) -> None:
    """The shape refusal, a step before any lookup (#205).

    A MAC is an ordinary command-line argument, so it is a place a paste
    lands like the names above, and `device bind` in particular takes it
    beside other arguments an operator is typing from a note. What fails
    the check is a value nothing here has validated, so the line an
    operator reads states what a MAC has to be and never what they
    typed: not on stderr, not on stdout, and not in a record it
    retained.
    """
    trailing = ("sam",) if argv[-1] == "bind" else ()

    with caplog.at_level(logging.DEBUG):
        assert run(*argv, SECRET, *trailing) == 1

    captured = capsys.readouterr()
    # The leak first and the sentence after it, so a failure here says
    # which of the two moved. The last line of the stream is the
    # refusal.
    assert SECRET not in captured.err
    assert SECRET not in captured.out
    assert captured.err.splitlines()[-1] == NOT_A_MAC
    assert "Traceback" not in captured.err
    written = [r for r in caplog.records if r.name.startswith("vinga_server")]
    assert all(SECRET not in str(record.__dict__) for record in written)


@pytest.mark.parametrize("layer", ["agent", "agent-defaults"])
def test_an_unknown_include_is_refused_without_printing_it(
    run, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture, layer: str
) -> None:
    """The sentinel is looked for on both streams and in every log
    record: a name written beside prompt text is where a paste lands,
    and this refusal is the line an operator reads."""
    _an_agent(run)
    written = (
        f"llm: claude\nprompt_includes: [{SECRET}]\n"
        if layer == "agent-defaults"
        else f"llm: claude\nprompt: hi\nprompt_includes: [{SECRET}]\n"
    )
    argv = ("agent-defaults", "set") if layer == "agent-defaults" else ("agent", "set", "sam")
    capsys.readouterr()

    with caplog.at_level(logging.DEBUG):
        assert run(*argv, "-f", "-", stdin=written) == 1

    captured = capsys.readouterr()
    assert "prompt_includes: entry 1" in captured.err
    assert SECRET not in captured.err
    assert SECRET not in captured.out
    assert all(SECRET not in record.getMessage() for record in caplog.records)
    assert "Traceback" not in captured.err


# The bodies an unusable name can be written with. The invalid ones are
# the point: a refusal about a body names where the body was written,
# and for a fragment that location is the name.
UNUSABLE_FRAGMENTS = [
    "text: a\n",
    "{}\n",
    "text: ''\n",
    "text: 4\n",
    "text: a\nextra: b\n",
    "- a list\n",
    f"text: {SECRET}\n",
]


@pytest.mark.parametrize("written", UNUSABLE_FRAGMENTS)
def test_an_unusable_fragment_name_is_refused_without_printing_it(
    run, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture, written: str
) -> None:
    """Whatever else is wrong with the write, the name is what is
    refused, and the name is not printed: it is checked before the body
    it arrived with is parsed, so no sentence about the body carries the
    location it was written at."""
    with caplog.at_level(logging.DEBUG):
        argv = ("prompt-fragment", "set", f"{SECRET}.pasted", "-f", "-")
        assert run(*argv, stdin=written) == 1

    captured = capsys.readouterr()
    assert "[A-Za-z0-9_-]+" in captured.err
    assert SECRET not in captured.err
    assert SECRET not in captured.out
    assert "Traceback" not in captured.err
    # The server's own records, for the reason the API suite gives: a
    # name travels in the request path, so the HTTP client that sends it
    # holds it by construction and writes it into its own request line.
    served = [
        record for record in caplog.records if record.name.startswith("vinga_server")
    ]
    assert all(SECRET not in record.getMessage() for record in served)
    assert all(SECRET not in str(record.__dict__) for record in served)


def test_add_device_binds_the_board_showing_the_code(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    _an_agent(run)
    code = _showing(run)
    capsys.readouterr()

    assert run("device", "pending", "claim", code, "sam") == 0

    captured = capsys.readouterr()
    # The operator never had to find the MAC; the acknowledgement names
    # the row that was written.
    assert captured.out == "wrote device aa:bb:cc:dd:ee:ff bound to sam\n"
    # Both boundaries rather than the check-in alone, because the
    # application this fixture builds is told of no servable agents,
    # which is the honest answer for one built without a server around
    # it. The two are told apart in test_config_api_pending.py.
    assert boundaries(captured.err) == {CHECK_IN, RELOAD}


def test_add_device_retires_the_code(run, capsys: pytest.CaptureFixture[str]) -> None:
    _an_agent(run)
    code = _showing(run)

    run("device", "pending", "claim", code, "sam")
    capsys.readouterr()

    assert run("device", "pending", "list") == 0
    assert capsys.readouterr().out.startswith("no device is waiting to be claimed")


def test_add_device_with_a_stale_code_says_to_read_the_screen(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The API's sentence, printed verbatim, as every refusal is."""
    _an_agent(run)
    capsys.readouterr()

    assert run("device", "pending", "claim", "000000", "sam") == 1

    captured = capsys.readouterr()
    assert captured.err.startswith("no device is waiting with that activation code")
    assert "on the device's screen" in captured.err
    assert captured.out == ""
    assert "Traceback" not in captured.err


def test_add_device_inherits_the_reference_check(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The repository decides what an agent is, here as everywhere: the
    claim route calls the same method `device bind` calls. What differs is
    the sentence, which on this route does not repeat the names it
    refused: a code is typed by hand, and so is whatever is typed beside
    it."""
    code = _showing(run)

    assert run("device", "pending", "claim", code, "ghost") == 1

    captured = capsys.readouterr()
    assert "at least one agent this deployment does not have" in captured.err
    assert "ghost" not in captured.err
    assert "Traceback" not in captured.err
    # And the board is still showing the number, so the number still
    # works.
    _an_agent(run)
    assert run("device", "pending", "claim", code, "sam") == 0


def test_a_refused_write_exits_one_with_the_reason(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("agent", "set", "sam", "-f", "-", stdin="llm: ghost\n") == 1

    captured = capsys.readouterr()
    assert "agents.sam.llm: names no llm provider that exists" in captured.err
    assert "Traceback" not in captured.err
    assert captured.out == ""


def test_a_malformed_grant_is_refused_without_echoing_it(
    run, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    """The other end of the same rule, on the surface an operator
    actually reads: a grant whose allow list repeats a name is refused
    by position, and the name is the one thing the line does not
    carry."""
    fragment = f"prompt: A\nmcp:\n  - server: weather\n    tools: [{SECRET}, {SECRET}]\n"

    with caplog.at_level(logging.DEBUG):
        assert run("agent", "set", "sam", "-f", "-", stdin=fragment) == 1

    captured = capsys.readouterr()
    assert "entry 1" in captured.err
    assert "more than one position (1, 2)" in captured.err
    assert SECRET not in captured.err
    assert SECRET not in captured.out
    assert SECRET not in caplog.text
    assert "Traceback" not in captured.err


def test_malformed_yaml_is_refused_without_echoing_the_line(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("agent", "set", "sam", "-f", "-", stdin=f"prompt: '{SECRET}\n") == 1

    captured = capsys.readouterr()
    assert "invalid YAML" in captured.err
    assert SECRET not in captured.err
    assert "Traceback" not in captured.err


# Every source this CLI hands a YAML parser, and every way one can fail
#
# Two ways in, a fragment and a whole document, each from a file or from
# stdin, and one boundary under all of them. The cases below are the two
# the boundary exists for.
#
# A TAG is the leak the parser writes for you: PyYAML answers a tag it
# has no constructor for by quoting the tag, and a tag is a run of
# characters an operator typed, so `!<credential> value` is a document
# whose refusal used to carry the credential. The pasted value on the
# line beside it covers the shape that was already safe, so a change
# that fixed one and broke the other fails here.
#
# And a CONSTRUCTION failure is the one that was not a refusal at all.
# `yaml.safe_load` documents `YAMLError`, and its constructors raise the
# ordinary exceptions on a scalar out of range, so an integer of five
# thousand digits left as a ValueError with a traceback carrying the
# whole source.

UNREADABLE = [
    ("a tag naming what nothing constructs", f"!{SECRET} value\n"),
    ("a scalar the constructor refuses", f"model: {'1' * 5000}\nnote: {SECRET}\n"),
    ("an unterminated quote", f"prompt: '{SECRET}\n"),
    ("a date nothing has", f"when: 2026-99-99\nnote: {SECRET}\n"),
]

# The two commands that read a YAML source, and what each calls it.
READERS = [
    ("a fragment", ("agent", "set", "sam", "-f", "-")),
    ("a document", ("import", "-f", "-")),
]


@pytest.mark.parametrize(
    "argv", [argv for _, argv in READERS], ids=[what for what, _ in READERS]
)
@pytest.mark.parametrize(
    "source", [source for _, source in UNREADABLE], ids=[what for what, _ in UNREADABLE]
)
def test_a_source_that_will_not_parse_says_nothing_of_what_it_held(
    run,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    argv: tuple[str, ...],
    source: str,
) -> None:
    with caplog.at_level(logging.DEBUG):
        assert run(*argv, stdin=source) == 1

    captured = capsys.readouterr()
    assert "invalid YAML" in captured.err
    assert SECRET not in captured.err
    assert SECRET not in captured.out
    assert "Traceback" not in captured.err
    assert all(SECRET not in str(record.__dict__) for record in caplog.records)


@pytest.mark.parametrize(
    "source", [source for _, source in UNREADABLE], ids=[what for what, _ in UNREADABLE]
)
def test_a_source_that_will_not_parse_carries_no_parser_exception(
    tmp_path: Path, source: str
) -> None:
    """White-box for the chain, which is not printed and so cannot be
    asserted through the runner: a PyYAML mark holds the whole buffer it
    was parsing, and one of these failures is not a PyYAML exception at
    all, so what has to be true of the refusal is that nothing walking
    it finds either."""
    written = tmp_path / "unreadable.yaml"
    written.write_text(source, encoding="utf-8")

    with pytest.raises(cli.ConfigError) as caught:
        cli._fragment(str(written))

    assert "invalid YAML" in str(caught.value)
    assert SECRET not in _chain(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_a_number_that_is_not_finite_is_refused(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """YAML spells NaN and infinity, JSON does not. A stored one would
    be read back as null, which silently turns the configuration into a
    different one, so the write is refused where every other fragment
    rule is applied."""
    assert run(
        "provider", "set", "llm", "claude", "-f", "-", stdin="type: anthropic\ntemperature: .nan\n"
    ) == 1

    captured = capsys.readouterr()
    assert "not a finite number" in captured.err
    assert "Traceback" not in captured.err

    # And a finite value goes through and shows as itself.
    assert run(
        "provider", "set", "llm", "claude", "-f", "-", stdin="type: anthropic\ntemperature: 0.7\n"
    ) == 0
    capsys.readouterr()
    run("provider", "show", "llm", "claude")
    assert _document(capsys.readouterr().out)["temperature"] == 0.7


TRANSPORT_REFUSALS = [
    ("a timestamp", "type: anthropic\nreleased: 2026-01-01\n", "JSON has no way to write"),
    (
        "a timestamp with a time",
        "type: anthropic\nwhen: 2026-01-01 12:00:00\n",
        "JSON has no way to write",
    ),
    ("binary", "type: anthropic\nblob: !!binary |\n  AAEC\n", "JSON has no way to write"),
    ("a set", "type: anthropic\ntags: !!set\n  ? a\n  ? b\n", "JSON has no way to write"),
    ("a recursive alias", "&loop\ntype: anthropic\nself: *loop\n", "contains itself"),
    ("an integer key", "type: anthropic\noptions:\n  1: x\n", "rather than a string"),
    ("a null key", "type: anthropic\noptions:\n  ~: x\n", "rather than a string"),
]


@pytest.mark.parametrize(("what", "fragment", "expected"), TRANSPORT_REFUSALS)
def test_a_fragment_json_cannot_carry_is_refused_before_it_travels(
    run, capsys: pytest.CaptureFixture[str], what: str, fragment: str, expected: str
) -> None:
    """YAML is the wider language, so a fragment can hold things the
    request body has no way to say. Every one of them meets the
    repository's sentence rather than the JSON encoder's TypeError,
    ValueError or RecursionError, and none of them writes anything."""
    assert run("provider", "set", "llm", "claude", "-f", "-", stdin=fragment) == 1, what

    captured = capsys.readouterr()
    assert expected in captured.err, what
    assert "Traceback" not in captured.err, what
    assert captured.out == "", what

    # And nothing was written: the entity does not exist.
    assert run("provider", "show", "llm", "claude") == 1
    assert capsys.readouterr().err.startswith("providers:")


def test_a_fragment_sharing_one_anchor_twice_still_travels(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The check refuses a structure that contains itself, not one that
    mentions the same anchor twice, which is an ordinary YAML file and
    is written out twice in JSON."""
    fragment = "type: anthropic\none: &shared\n  a: 1\ntwo: *shared\n"

    assert run("provider", "set", "llm", "claude", "-f", "-", stdin=fragment) == 0
    capsys.readouterr()

    run("provider", "show", "llm", "claude")
    shown = _document(capsys.readouterr().out)
    assert shown["one"] == {"a": 1}
    assert shown["two"] == {"a": 1}


def test_a_parser_failure_carries_no_parser_exception(tmp_path: Path) -> None:
    """A PyYAML mark holds the whole buffer it was parsing, which here
    is the fragment, so the refusal is built inside the handler and
    raised outside it: `from None` would leave the parser's exception
    reachable as __context__."""
    fragment = tmp_path / "broken.yaml"
    fragment.write_text(f"prompt: '{SECRET}\n", encoding="utf-8")

    # White-box for this refusal test: what is under test is the
    # exception's CHAIN, and a chain is not printed. The command prints
    # one sanitized line, which the runner-driven tests beside this one
    # assert; a __cause__ or a __context__ still holding the library's
    # own exception is reachable only from where the refusal is raised,
    # and anything that renders a traceback would find it there.
    with pytest.raises(cli.ConfigError) as caught:
        cli._fragment(str(fragment))

    assert "invalid YAML" in str(caught.value)
    assert SECRET not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None

    with pytest.raises(cli.ConfigError) as missing:
        cli._fragment(str(tmp_path / "nowhere.yaml"))

    assert missing.value.__cause__ is None
    assert missing.value.__context__ is None


def test_show_renders_every_entity_kind(run, capsys: pytest.CaptureFixture[str]) -> None:
    run("provider", "set", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")
    run(
        "mcp-server", "set", "home",
        "-f",
        "-",
        stdin="transport: stdio\ncommand: uvx\n",
    )
    run("agent-defaults", "set", "-f", "-", stdin="llm: claude\n")
    run("agent", "set", "sam", "-f", "-", stdin="prompt: You are Sam.\n")
    run("device", "bind", "aa:bb:cc:dd:ee:ff", "sam")
    capsys.readouterr()

    run("mcp-server", "show", "home")
    assert _document(capsys.readouterr().out)["command"] == "uvx"
    run("agent", "show", "sam")
    assert _document(capsys.readouterr().out)["prompt"] == "You are Sam."
    run("agent-defaults", "show")
    assert _document(capsys.readouterr().out) == {"llm": "claude"}
    run("device", "show", "AA-BB-CC-DD-EE-FF")
    assert _document(capsys.readouterr().out) == {"agents": ["sam"]}


def test_an_agent_is_printed_prompt_first(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The printed document, byte for byte, which is the other half of
    the claim the API's exact-bytes pin makes: YAML keeps the order the
    mapping was built in, so an agent read here opens with the prompt
    that makes it that agent and then says what it overrides.

    The API answers the same order in JSON, which
    `test_config_api_reads.py` pins on the response bytes. What this
    adds is that the rendering does not sort it away on the way to
    stdout.
    """
    run("provider", "set", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")
    run(
        "agent", "set", "sam",
        "-f",
        "-",
        stdin="llm: claude\nprompt: You are Sam.\n",
    )
    capsys.readouterr()

    assert run("agent", "show", "sam") == 0

    assert capsys.readouterr().out == "prompt: You are Sam.\nllm: claude\n"


def test_showing_something_that_is_not_there_names_the_section_only(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The section it would have been in, one line, and the identity
    that was asked for in none of them (#132)."""
    for argv, section, identity in (
        (("provider", "show", "llm", "ghost"), "providers", "ghost"),
        (("mcp-server", "show", "ghost"), "mcp_servers", "ghost"),
        (("prompt-fragment", "show", "ghost"), "prompt_fragments", "ghost"),
        (("agent", "show", "ghost"), "agents", "ghost"),
        (("device", "show", "aa:bb:cc:dd:ee:ff"), "devices", "aa:bb:cc:dd:ee:ff"),
    ):
        assert run(*argv) == 1
        captured = capsys.readouterr()
        assert captured.err.startswith(f"{section}:")
        assert captured.err.count("\n") == 1
        assert identity not in captured.err
        assert "Traceback" not in captured.err


def test_the_default_agent_can_be_cleared(run, capsys: pytest.CaptureFixture[str]) -> None:
    run("agent", "set", "sam", "-f", "-", stdin="prompt: You are Sam.\n")
    run("default-agent", "set", "sam")
    capsys.readouterr()

    assert run("default-agent", "clear") == 0
    run("list")
    assert "default_agent: (none)" in capsys.readouterr().out


def test_a_row_of_the_wrong_shape_is_reported_rather_than_raised(
    run, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The reading commands are what an operator reaches for when
    something is wrong with the database, so a row that cannot be read
    has to come back as a sentence rather than as a traceback."""
    run("provider", "set", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")
    capsys.readouterr()

    engine = open_database(DatabaseConfig())
    try:
        with engine.begin() as connection:
            connection.execute(update(schema.providers).values(body="not json at all"))
    finally:
        engine.dispose()

    for argv in (("list",), ("show",), ("provider", "show", "llm", "claude")):
        assert run(*argv) == 1
        captured = capsys.readouterr()
        assert "providers.llm.claude" in captured.err
        assert "cannot be read as configuration" in captured.err
        assert "Traceback" not in captured.err


def test_a_database_that_cannot_be_opened_names_the_variables(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A port nothing listens on, which is the ordinary shape of a
    misconfigured deployment. What comes back is a sentence naming the
    variables to look at, and no part of the connection: the driver's
    own error quotes the DSN it tried."""
    monkeypatch.setenv("VINGA_DB_PORT", "1")

    assert run("list") == 1

    captured = capsys.readouterr()
    assert "VINGA_DB_HOST" in captured.err
    assert "Traceback" not in captured.err


# Identities that only survive a URL path encoded


@pytest.mark.parametrize("name", ["a name with spaces", "100%-sure", "agente-café"])
def test_an_awkward_name_round_trips_through_the_whole_client(
    run, name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("agent", "set", name, "-f", "-", stdin="prompt: You are it.\n") == 0
    assert f"wrote agent {name}" in capsys.readouterr().out

    assert run("agent", "show", name) == 0
    assert _document(capsys.readouterr().out)["prompt"] == "You are it."

    assert run("agent", "delete", name) == 0
    capsys.readouterr()
    assert run("agent", "show", name) == 1
    assert capsys.readouterr().err.startswith("agents:")


def test_a_name_a_url_path_cannot_carry_is_refused(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """Refused at the repository when it can be reached, and unroutable
    when it cannot: either way no such entity is created."""
    assert run("agent", "set", "a/b", "-f", "-", stdin="prompt: You are it.\n") == 1
    capsys.readouterr()

    assert run("list") == 0
    assert "(none)" in capsys.readouterr().out


# The other way to write an entity
#
# `set <kind> [identity] key=value...` assembles the fragment the YAML
# would and enters the same path: the same transportability check, the
# same request, the same acknowledgement. What each case below proves is
# the resulting store state, read back through `show`, which is the only
# thing that can say the two ways of writing one entity meant one thing.

INLINE = [
    (
        "a flat entity",
        ("provider", "set", "llm", "claude"),
        ("type=anthropic", "model=m"),
        "type: anthropic\nmodel: m\n",
        ("provider", "show", "llm", "claude"),
    ),
    (
        "the singleton, addressed by nothing",
        ("agent-defaults", "set"),
        ("llm=claude",),
        "llm: claude\n",
        ("agent-defaults", "show"),
    ),
    (
        "a dotted key, which nests",
        ("agent", "set", "sam"),
        ("prompt=hi", "filler.enabled=false"),
        "prompt: hi\nfiller:\n  enabled: false\n",
        ("agent", "show", "sam"),
    ),
    (
        "values that are not strings",
        ("provider", "set", "llm", "claude"),
        ("type=anthropic", "temperature=0.7", "stream=true", "retries=3", "seed=null"),
        "type: anthropic\ntemperature: 0.7\nstream: true\nretries: 3\nseed: null\n",
        ("provider", "show", "llm", "claude"),
    ),
    (
        "a value holding the separator",
        ("provider", "set", "llm", "claude"),
        ("type=anthropic", "base_url=https://example.invalid/v1?a=b"),
        "type: anthropic\nbase_url: https://example.invalid/v1?a=b\n",
        ("provider", "show", "llm", "claude"),
    ),
]


@pytest.mark.parametrize(
    ("argv", "pairs", "fragment", "read"),
    [(argv, pairs, fragment, read) for _, argv, pairs, fragment, read in INLINE],
    ids=[what for what, _, _, _, _ in INLINE],
)
def test_inline_fields_write_what_the_fragment_writes(
    run,
    capsys: pytest.CaptureFixture[str],
    argv: tuple[str, ...],
    pairs: tuple[str, ...],
    fragment: str,
    read: tuple[str, ...],
) -> None:
    """The claim the inline form makes is that it is the fragment, so
    the case is differential: write it both ways against the same
    starting state and read the same document back."""
    _an_agent(run)
    assert run(*argv, *pairs) == 0
    capsys.readouterr()
    run(*read)
    inline = _document(capsys.readouterr().out)

    assert run(*argv, "-f", "-", stdin=fragment) == 0
    capsys.readouterr()
    run(*read)

    assert inline == _document(capsys.readouterr().out)
    assert inline is not None


def test_an_inline_write_is_acknowledged_the_way_a_fragment_is(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """Same path, same answer: the acknowledgement and the boundary it
    names are the API's, and the inline form does not touch either."""
    assert run("provider", "set", "llm", "claude", "type=anthropic", "model=m") == 0

    written = capsys.readouterr()
    assert written.out == "wrote provider llm.claude\n"
    assert boundaries(written.err) == {RELOAD}


def test_an_inline_value_json_cannot_carry_meets_the_same_sentence(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """A bare date is a YAML scalar, so the pair parser passes it, and
    the rule about what JSON can carry stays where it lives for a
    fragment too."""
    assert run("provider", "set", "llm", "claude", "type=anthropic", "released=2026-01-01") == 1

    captured = capsys.readouterr()
    assert "JSON has no way to write" in captured.err
    assert "Traceback" not in captured.err


def test_a_secret_shaped_inline_key_is_refused_by_the_store(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The rule that a plaintext credential never enters is the
    repository's and is about the key's shape, so it holds whichever way
    the entity was written."""
    assert run("provider", "set", "llm", "claude", "type=anthropic", f"api_key={SECRET}") == 1

    captured = capsys.readouterr()
    assert "looks like an inline secret" in captured.err
    assert SECRET not in captured.err
    assert SECRET not in captured.out


# The two ways are alternatives


def test_neither_way_of_writing_an_entity_is_the_missing_argument(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """Click cannot see this one, because either of the two satisfies
    the command, so the grammar says it itself, in the words the
    boundary says it in."""
    assert run("agent", "set", "sam") == 1

    captured = capsys.readouterr()
    assert cli.MISSING_ARGUMENT in captured.err
    assert "run with --help for the grammar" in captured.err
    assert captured.out == ""


def test_both_ways_at_once_is_refused(
    run, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    fragment = tmp_path / "sam.yaml"
    fragment.write_text("prompt: hi\n", encoding="utf-8")

    assert run("agent", "set", "sam", "prompt=hi", "-f", str(fragment)) == 1

    captured = capsys.readouterr()
    assert cli.BOTH_INPUTS in captured.err
    assert captured.out == ""
    # And nothing was written either way.
    assert run("agent", "show", "sam") == 1


# The pair parser as a boundary
#
# Every malformed shape it names, each with a credential written where a
# mistake would put one, asserted absent from stdout, stderr, every log
# record and the exception's whole chain. The value typed beside a key
# is exactly where a paste lands, which is what makes this the same
# no-leak boundary `_fragment` is.

# The headline a source that will not parse earns, which the boundary
# follows with where the parser stopped and the shared tail. Asserted as
# the headline rather than as the whole sentence because the position
# moves with the input, and the position is the only thing about the
# failure the refusal carries.
_UNREADABLE_VALUE = f"invalid YAML in {cli.PAIR_SOURCE}"

MALFORMED = [
    ("no separator at all", (SECRET,), cli.PAIR_NEEDS_EQUALS),
    ("an empty key", (f"={SECRET}",), cli.PAIR_EMPTY_KEY),
    ("an empty dotted segment", (f"a..b={SECRET}",), cli.PAIR_EMPTY_KEY),
    ("a leading dot", (f".a={SECRET}",), cli.PAIR_EMPTY_KEY),
    ("the same key twice", (f"model={SECRET}", f"model={SECRET}"), cli.PAIR_DUPLICATE_KEY),
    ("a key nested inside another", (f"a.b={SECRET}", f"a={SECRET}"), cli.PAIR_NESTED_KEY),
    ("a value that will not parse", (f"model='{SECRET}",), _UNREADABLE_VALUE),
    # Not a YAMLError at all: CPython refuses to parse an integer of
    # more than 4300 digits, and PyYAML lets that ValueError through.
    ("a value the parser cannot construct", (f"model={'1' * 5000}",), _UNREADABLE_VALUE),
    ("a value that is a list", (f"model=[{SECRET}]",), cli.PAIR_NOT_SCALAR),
    ("a value that is a mapping", (f"model={{a: {SECRET}}}",), cli.PAIR_NOT_SCALAR),
]


@pytest.mark.parametrize(
    ("pairs", "sentence"),
    [(pairs, sentence) for _, pairs, sentence in MALFORMED],
    ids=[what for what, _, _ in MALFORMED],
)
def test_a_malformed_pair_says_nothing_of_what_was_typed(
    run,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    pairs: tuple[str, ...],
    sentence: str,
) -> None:
    with caplog.at_level(logging.DEBUG):
        assert run("provider", "set", "llm", "claude", "type=anthropic", *pairs) == 1

    captured = capsys.readouterr()
    assert sentence in captured.err
    assert SECRET not in captured.err
    assert SECRET not in captured.out
    assert "Traceback" not in captured.err
    assert all(SECRET not in str(record.__dict__) for record in caplog.records)
    # And nothing of the refused write landed.
    assert run("provider", "show", "llm", "claude") == 1


@pytest.mark.parametrize(
    ("pairs", "sentence"),
    [(pairs, sentence) for _, pairs, sentence in MALFORMED],
    ids=[what for what, _, _ in MALFORMED],
)
def test_a_malformed_pair_carries_no_parser_exception(
    pairs: tuple[str, ...], sentence: str
) -> None:
    """White-box for the chain, which is not printed and so cannot be
    asserted through the runner: PyYAML's mark holds the whole buffer it
    was parsing, which here is the value, so the refusal is built inside
    the handler and raised outside it and nothing walking the chain
    finds the value behind it."""
    with pytest.raises(cli.ConfigError) as caught:
        cli._pairs(pairs)

    assert sentence in str(caught.value)
    assert SECRET not in _chain(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


# The whole configuration in one command
#
# `import` is the only write that carries several entities, and the only
# one whose answer is a list. What it prints is one line per entry in
# the configuration's own section order, and then the boundaries the
# entries that were written are waiting on, each distinct one once.
#
# All of these are about the store, which is all `import` touches
# (#371): the transaction, the outcomes and the sentences. Installing
# what was written is `apply`, a command of its own, and it is exercised
# where a running server can answer it:
# `test_config_cli_rendering.py` injects one, and the live lane has a
# real one.

DOCUMENT = """\
providers:
  llm:
    claude: {type: anthropic, model: m}
agents:
  sam: {prompt: You are Sam., llm: claude}
devices:
  AA-BB-CC-DD-EE-FF: [sam]
default_agent: sam
"""


def test_import_writes_a_whole_deployment_from_one_file(
    run, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The acceptance case for the verb: an empty database becomes a
    working configuration in one command, including the two orderings a
    sequence of single writes has to get right by hand."""
    document = tmp_path / "setup.yaml"
    document.write_text(DOCUMENT, encoding="utf-8")

    assert run("import", "-f", str(document)) == 0

    written = capsys.readouterr()
    assert written.out.splitlines() == [
        "providers.llm.claude: wrote",
        "agents.sam: wrote",
        "devices.aa:bb:cc:dd:ee:ff: wrote",
        "default_agent: wrote",
    ]
    assert boundaries(written.err) == {CHECK_IN, RELOAD}
    assert run("show") == 0
    shown = _document(capsys.readouterr().out)
    assert shown["agents"]["sam"]["prompt"] == "You are Sam."
    assert shown["devices"] == {"aa:bb:cc:dd:ee:ff": ["sam"]}
    assert shown["default_agent"] == "sam"


def test_import_reads_a_document_from_stdin(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("import", "-f", "-", stdin=DOCUMENT) == 0

    assert "agents.sam: wrote" in capsys.readouterr().out


def test_the_same_document_twice_changes_nothing_and_says_so(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """Idempotence is what makes an imported document a thing to keep
    in a repository and run, so it is what the second run has to say:
    every row already what the document names, and no boundary to wait
    on."""
    run("import", "-f", "-", stdin=DOCUMENT)
    capsys.readouterr()

    assert run("import", "-f", "-", stdin=DOCUMENT) == 0

    again = capsys.readouterr()
    assert {line.split(": ")[-1] for line in again.out.splitlines()} == {"unchanged"}
    assert again.err == ""


def test_a_refused_document_prints_every_mistake_and_writes_nothing(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """Refused whole, reported whole, and the store afterwards is what
    it was: the provider in this document is perfectly good and does not
    land either."""
    refused = "providers:\n  llm:\n    claude: {type: anthropic}\nagents:\n  sam: {llm: ghost}\n"

    assert run("import", "-f", "-", stdin=refused) == 1

    captured = capsys.readouterr()
    assert "agents.sam.llm: names no llm provider that exists" in captured.err
    assert captured.out == ""
    assert "Traceback" not in captured.err
    assert run("provider", "show", "llm", "claude") == 1
    assert capsys.readouterr().err.startswith("providers:")


def test_an_empty_document_says_it_imported_nothing(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("import", "-f", "-", stdin="{}\n") == 0

    assert capsys.readouterr().out.startswith("the document names no section")


def test_a_document_json_cannot_carry_is_refused_before_it_travels(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The same rule a fragment meets, under the location the repository
    names a refusal about the whole document with."""
    assert run(
        "import", "-f", "-", stdin="providers:\n  llm:\n    claude: {type: a, when: 2026-01-01}\n"
    ) == 1

    captured = capsys.readouterr()
    assert captured.err.startswith("invalid document:")
    assert "JSON has no way to write" in captured.err
    assert "Traceback" not in captured.err


def test_a_refused_document_never_echoes_what_was_written(
    run, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    """A document is a file an operator wrote, so a fragment inside one
    is where a paste lands exactly as a fragment sent on its own is."""
    written = f"providers:\n  llm:\n    claude: {{type: anthropic, api_key: {SECRET}}}\n"

    with caplog.at_level(logging.DEBUG):
        assert run("import", "-f", "-", stdin=written) == 1

    captured = capsys.readouterr()
    assert "looks like an inline secret" in captured.err
    assert SECRET not in captured.err
    assert SECRET not in captured.out
    served = [r for r in caplog.records if r.name.startswith("vinga_server")]
    assert all(SECRET not in str(record.__dict__) for record in served)


# The reference edges as an operator meets them
#
# The third surface: the sentence the repository wrote, printed. The
# documents below are valid apart from the one reference that will not
# resolve, so what refuses them is the reference pass rather than the
# model in front of it, and what an operator reads is the line and not
# the name they pasted into it.

REFERENCE_LEAKS = [
    ("an agent's provider", f"agents:\n  a: {{prompt: p, llm: {SECRET}}}\n"),
    ("an agent's MCP server", f"agents:\n  a: {{prompt: p, mcp: [{SECRET}]}}\n"),
    (
        "an agent's prompt fragment",
        f"agents:\n  a: {{prompt: p, prompt_includes: [{SECRET}]}}\n",
    ),
    ("a device's agent", f"devices:\n  aa:bb:cc:dd:ee:ff: [{SECRET}]\n"),
    ("the default agent", f"default_agent: {SECRET}\n"),
]


@pytest.mark.parametrize(
    "document",
    [document for _, document in REFERENCE_LEAKS],
    ids=[what for what, _ in REFERENCE_LEAKS],
)
def test_an_imported_reference_is_refused_without_printing_the_name(
    run,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    document: str,
) -> None:
    _an_agent(run)
    capsys.readouterr()

    with caplog.at_level(logging.DEBUG):
        assert run("import", "-f", "-", stdin=document) == 1

    captured = capsys.readouterr()
    assert captured.err.startswith("the change was refused")
    assert "not quoted back" in captured.err
    assert SECRET not in captured.err
    assert SECRET not in captured.out
    assert "Traceback" not in captured.err
    served = [r for r in caplog.records if r.name.startswith("vinga_server")]
    assert all(SECRET not in str(record.__dict__) for record in served)


VALIDATOR_LEAKS = [
    (
        "a binding naming one agent twice",
        f"devices:\n  aa:bb:cc:dd:ee:ff: [{SECRET}, {SECRET}]\n",
        "more than one position",
    ),
    (
        "an entry name that is not a tool prefix",
        f"mcp_servers:\n  {SECRET}.pasted: {{transport: stdio, command: uvx}}\n",
        "must match [A-Za-z0-9_-]+",
    ),
]


@pytest.mark.parametrize(
    ("document", "rule"),
    [(document, rule) for _, document, rule in VALIDATOR_LEAKS],
    ids=[what for what, _, _ in VALIDATOR_LEAKS],
)
def test_an_imported_validator_is_refused_without_printing_the_value(
    run,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    document: str,
    rule: str,
) -> None:
    """The two validators a document reaches that are not the reference
    pass, as an operator meets them: the rule and the position, and not
    the word they pasted."""
    with caplog.at_level(logging.DEBUG):
        assert run("import", "-f", "-", stdin=document) == 1

    captured = capsys.readouterr()
    assert rule in captured.err
    assert SECRET not in captured.err
    assert SECRET not in captured.out
    assert "Traceback" not in captured.err
    served = [r for r in caplog.records if r.name.startswith("vinga_server")]
    assert all(SECRET not in str(record.__dict__) for record in served)


# A pipe asked for at a terminal
#
# `-f -` reads standard input, and the read used to be unconditional, so
# a person who typed it at a prompt met a cursor and no explanation.
# That is the prompt rule broken from the other side: never require a
# prompt, and never read one without asking whether there is anybody
# there.


class _Terminal(io.StringIO):
    """A stdin that says it is a terminal, which is what the read
    branches on."""

    def isatty(self) -> bool:
        return True


@pytest.mark.parametrize(
    "argv",
    [
        ("import", "-f", "-"),
        ("provider", "set", "llm", "claude", "-f", "-"),
    ],
    ids=["a document", "a fragment"],
)
def test_reading_a_document_from_a_terminal_says_so_rather_than_hanging(
    run,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    argv: tuple[str, ...],
) -> None:
    """One sentence with the usage tail, and exit 1, which is what every
    other mistake in the grammar answers with. Nothing is read, so the
    case cannot hang: a test that hung would take the lane with it."""
    monkeypatch.setattr(sys, "stdin", _Terminal(""))
    capsys.readouterr()

    assert run(*argv) == 1

    captured = capsys.readouterr()
    assert captured.err == cli.usage_line(cli.STDIN_AT_A_TERMINAL) + "\n"
    assert captured.out == ""


def test_a_pipe_is_read_exactly_as_it_was(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The licensed half: what changed is the terminal case, and a pipe,
    a redirect and a heredoc all read as they always did."""
    assert run(
        "provider", "set", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n"
    ) == 0
    capsys.readouterr()

    assert run("provider", "show", "llm", "claude") == 0
    assert _document(capsys.readouterr().out) == {"type": "anthropic", "model": "m"}
