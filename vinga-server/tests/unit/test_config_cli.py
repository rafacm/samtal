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

Five neighbours hold the rest of the surface, split off by #139 along
the boundaries the production split produced, each named for the concern
it keeps: `test_config_cli_transport.py` (where a command is sent and
what it will not be sent over), `test_config_cli_rendering.py` (the four
answers the running server is asked for), `test_config_cli_secrets.py`
(a credential's whole life), `test_config_cli_grammar.py` (the parse and
its exit codes) and `test_config_cli_local.py` (the break-glass path).
"""

import logging
from pathlib import Path

import pytest
from sqlalchemy import update

from tests.support.config_cli import (
    FRAGMENT_INPUT,
    FRAGMENT_TEXT,
    SECRET,
    runner,
)
from tests.support.config_cli import document as _document
from tests.support.config_cli import showing as _showing
from vinga_server.config import cli
from vinga_server.config.entities import (
    NO_SUCH_AGENT,
    NO_SUCH_DEVICE,
    NO_SUCH_FRAGMENT,
    NO_SUCH_MCP_SERVER,
    NO_SUCH_PROVIDER,
)
from vinga_server.config.store import NOT_A_STAGE
from vinga_server.config.writes import (
    BINDING_NOTICE,
    BINDING_UNSERVED_NOTICE,
    RELOAD_NOTICE,
)
from vinga_server.db import open_database, schema


@pytest.fixture
def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """One command run the way the entry point runs it, against a server
    of this test's own."""
    return runner(tmp_path, monkeypatch)


def test_an_empty_database_becomes_a_working_configuration(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The natural order, end to end: providers, MCP servers, agents,
    devices, default agent. Every intermediate state here would fail the
    boot-only completeness rule, and none of the writes may be refused."""
    claude = "type: anthropic\nmodel: m\n"
    assert run("set", "provider", "llm", "claude", "-f", "-", stdin=claude) == 0
    assert run("set", "provider", "asr", "whisper", "-f", "-", stdin="type: mock\n") == 0
    assert run("set", "provider", "tts", "voice", "-f", "-", stdin="type: mock\n") == 0
    assert run("set", "provider", "vad", "ears", "-f", "-", stdin="type: mock\n") == 0
    assert run(
        "set",
        "mcp-server",
        "home",
        "-f",
        "-",
        stdin="transport: stdio\ncommand: uvx\negress: false\n",
    ) == 0
    assert run(
        "set",
        "agent-defaults",
        "-f",
        "-",
        stdin="llm: claude\nasr: whisper\ntts: voice\nvad: ears\nmcp: [home]\n",
    ) == 0
    # The agent no default_agent names yet: the write that would deadlock
    # if completeness were enforced here.
    assert run("set", "agent", "sam", "-f", "-", stdin="prompt: You are Sam.\n") == 0
    assert run("bind-device", "AA-BB-CC-DD-EE-FF", "sam") == 0
    assert run("set-default-agent", "sam") == 0
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

    assert run("set", "provider", "llm", "claude", "-f", str(fragment)) == 0
    capsys.readouterr()

    assert run("show", "provider", "llm", "claude") == 0
    assert _document(capsys.readouterr().out) == {"type": "anthropic", "model": "m"}


def test_a_missing_fragment_file_is_named(run, capsys: pytest.CaptureFixture[str]) -> None:
    assert run("set", "agent", "sam", "-f", "/nowhere/at/all.yaml") == 1

    captured = capsys.readouterr()
    assert "fragment file not found" in captured.err
    assert "Traceback" not in captured.err


def test_every_mutating_command_says_when_the_write_applies(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """A write is stored where the server reads it later, which makes a
    write that quietly waits the one thing about that design an operator
    can be caught by.

    The agent's answer is asserted whole rather than by substring: it is
    the sentence an operator acts on, and what it has to carry is the
    three moments a live conversation meets an applied change at."""
    run("set", "provider", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")
    assert RELOAD_NOTICE in capsys.readouterr().err

    run("set", "agent", "sam", "-f", "-", stdin="llm: claude\n")
    assert capsys.readouterr().err == f"{RELOAD_NOTICE}\n"

    run("set-default-agent", "sam")
    # The application this fixture builds is told of no servable agents,
    # so the default agent it just named is one this server is not
    # serving, and the sentence says which reload would install it.
    assert BINDING_UNSERVED_NOTICE in capsys.readouterr().err

    # A read is not a write, and says nothing.
    run("list")
    assert BINDING_UNSERVED_NOTICE not in capsys.readouterr().err


def test_a_device_write_says_the_device_meets_it_itself(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The one kind nothing has to be asked to apply, printed verbatim
    from what the API answered: a running server reads the devices table
    as a device asks, so a delete reaches the device at its next
    check-in and needs neither a reload nor a start."""
    run("set", "provider", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")
    run("set", "agent", "sam", "-f", "-", stdin="llm: claude\n")
    run("bind-device", "aa:bb:cc:dd:ee:ff", "sam")
    capsys.readouterr()

    assert run("delete", "device", "aa:bb:cc:dd:ee:ff") == 0

    assert BINDING_NOTICE in capsys.readouterr().err


def _an_agent(run) -> None:
    run("set", "provider", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")
    run("set", "agent", "sam", "-f", "-", stdin="llm: claude\n")


# Shared prompt fragments
#
# The exact CLI input and the exact rendering, pinned: a fragment is
# written as `text: ...` like every other entity's body, and what comes
# back out is the same bytes, because those bytes are what the model is
# given.


def test_a_prompt_fragment_is_written_shown_and_listed(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("set", "prompt-fragment", "household", "-f", "-", stdin=FRAGMENT_INPUT) == 0
    written = capsys.readouterr()
    assert written.out == "wrote prompt-fragment household\n"
    # A fragment is prompt text, which a reload puts in front of the
    # next activation of every agent that includes it.
    assert RELOAD_NOTICE in written.err

    assert run("show", "prompt-fragment", "household") == 0
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
    run("set", "prompt-fragment", "household", "-f", "-", stdin=FRAGMENT_INPUT)
    assert run(
        "set", "agent", "sam", "-f", "-", stdin="llm: claude\nprompt_includes: [household]\n"
    ) == 0
    capsys.readouterr()

    assert run("show", "agent", "sam") == 0

    assert _document(capsys.readouterr().out)["prompt_includes"] == ["household"]


def test_a_fragment_an_agent_includes_is_not_deleted_from_under_it(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    _an_agent(run)
    run("set", "prompt-fragment", "household", "-f", "-", stdin=FRAGMENT_INPUT)
    run("set", "agent", "sam", "-f", "-", stdin="llm: claude\nprompt_includes: [household]\n")
    capsys.readouterr()

    assert run("delete", "prompt-fragment", "household") == 1

    assert "prompt_includes" in capsys.readouterr().err


# Every section a command addresses one entry of, as the arguments that
# address something nothing wrote, the sentence both verbs answer with,
# and the value that must not be printed. The names are credential
# shaped, because a command line is where a paste lands; a device is
# addressed by a MAC, which cannot be, so its own identity is the
# sentinel.
UNWRITTEN = [
    (("provider", "llm", SECRET), NO_SUCH_PROVIDER, SECRET),
    (("provider", "llm", f"{SECRET}.pasted"), NO_SUCH_PROVIDER, SECRET),
    (("mcp-server", SECRET), NO_SUCH_MCP_SERVER, SECRET),
    (("prompt-fragment", SECRET), NO_SUCH_FRAGMENT, SECRET),
    (("prompt-fragment", f"{SECRET}.pasted"), NO_SUCH_FRAGMENT, SECRET),
    (("agent", SECRET), NO_SUCH_AGENT, SECRET),
    (("device", "aa:bb:cc:dd:ee:ff"), NO_SUCH_DEVICE, "aa:bb:cc:dd:ee:ff"),
    # The same paste one argument to the left. A provider is addressed
    # by a stage and a name together, and a stage that is not one of the
    # four is refused before anything is looked up, on both paths.
    (("provider", SECRET, "claude"), NOT_A_STAGE, SECRET),
]


@pytest.mark.parametrize(("addressed", "sentence", "sentinel"), UNWRITTEN)
@pytest.mark.parametrize("local", [False, True])
def test_an_identity_that_addresses_nothing_is_refused_without_printing_it(
    run,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    addressed: tuple[str, ...],
    sentence: str,
    sentinel: str,
    local: bool,
) -> None:
    """Every section, both verbs, and both ways in (#132).

    The recovery path opens the database itself and has to say the same
    sentence as the client, so `--local` is driven beside the ordinary
    path. An identity that addresses nothing was typed rather than
    stored, so neither the read nor the delete repeats it: not on
    stderr, not on stdout, and not in a record either of them retained.
    That covers a name nothing wrote and a stage that is not a stage,
    which are the two ways a segment can address nothing.
    """
    flags = ("--local",) if local else ()
    for verb in ("show", "delete"):
        with caplog.at_level(logging.DEBUG):
            assert run(*flags, verb, *addressed) == 1

        captured = capsys.readouterr()
        # The leak first and the wording after it, so a failure here
        # says which of the two moved. The refusal is asserted whole, so
        # a sentence that grew the identity back on the end would fail;
        # `--local` prints its break-glass banner before it, which is
        # why this is the tail of the stream rather than all of it.
        assert sentinel not in captured.err
        assert sentinel not in captured.out
        assert captured.err.endswith(f"{sentence}\n")
        assert "Traceback" not in captured.err
        # This project's own records. The client's HTTP library writes
        # the URL it requested, which is the identity the operator just
        # typed going out over the loopback socket the CLI is talking to
        # itself on, not something a server retained.
        written = [r for r in caplog.records if r.name.startswith("vinga_server")]
        assert all(sentinel not in str(record.__dict__) for record in written)


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
    argv = ("set", layer) if layer == "agent-defaults" else ("set", "agent", "sam")
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
        argv = ("set", "prompt-fragment", f"{SECRET}.pasted", "-f", "-")
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

    assert run("add-device", code, "sam") == 0

    captured = capsys.readouterr()
    # The operator never had to find the MAC; the acknowledgement names
    # the row that was written.
    assert captured.out == "wrote device aa:bb:cc:dd:ee:ff bound to sam\n"
    # The sentence for a binding whose agent is not being served rather
    # than the plain one, because the application this fixture builds is
    # told of no servable agents, which is the honest answer for one
    # built without a server around it. The two notices are told apart
    # in test_config_api_pending.py.
    assert BINDING_UNSERVED_NOTICE in captured.err


def test_add_device_retires_the_code(run, capsys: pytest.CaptureFixture[str]) -> None:
    _an_agent(run)
    code = _showing(run)

    run("add-device", code, "sam")
    capsys.readouterr()

    assert run("pending") == 0
    assert capsys.readouterr().out.startswith("no device is waiting to be claimed")


def test_add_device_with_a_stale_code_says_to_read_the_screen(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The API's sentence, printed verbatim, as every refusal is."""
    _an_agent(run)
    capsys.readouterr()

    assert run("add-device", "000000", "sam") == 1

    captured = capsys.readouterr()
    assert captured.err.startswith("no device is waiting with that activation code")
    assert "on the device's screen" in captured.err
    assert captured.out == ""
    assert "Traceback" not in captured.err


def test_add_device_inherits_the_reference_check(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The repository decides what an agent is, here as everywhere: the
    claim route calls the same method bind-device calls. What differs is
    the sentence, which on this route does not repeat the names it
    refused: a code is typed by hand, and so is whatever is typed beside
    it."""
    code = _showing(run)

    assert run("add-device", code, "ghost") == 1

    captured = capsys.readouterr()
    assert "at least one agent this deployment does not have" in captured.err
    assert "ghost" not in captured.err
    assert "Traceback" not in captured.err
    # And the board is still showing the number, so the number still
    # works.
    _an_agent(run)
    assert run("add-device", code, "sam") == 0


def test_a_refused_write_exits_one_with_the_reason(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("set", "agent", "sam", "-f", "-", stdin="llm: ghost\n") == 1

    captured = capsys.readouterr()
    assert 'unknown llm provider "ghost"' in captured.err
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
        assert run("set", "agent", "sam", "-f", "-", stdin=fragment) == 1

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
    assert run("set", "agent", "sam", "-f", "-", stdin=f"prompt: '{SECRET}\n") == 1

    captured = capsys.readouterr()
    assert "invalid YAML" in captured.err
    assert SECRET not in captured.err
    assert "Traceback" not in captured.err


def test_a_number_that_is_not_finite_is_refused(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """YAML spells NaN and infinity, JSON does not. A stored one would
    be read back as null, which silently turns the configuration into a
    different one, so the write is refused where every other fragment
    rule is applied."""
    assert run(
        "set", "provider", "llm", "claude", "-f", "-", stdin="type: anthropic\ntemperature: .nan\n"
    ) == 1

    captured = capsys.readouterr()
    assert "not a finite number" in captured.err
    assert "Traceback" not in captured.err

    # And a finite value goes through and shows as itself.
    assert run(
        "set", "provider", "llm", "claude", "-f", "-", stdin="type: anthropic\ntemperature: 0.7\n"
    ) == 0
    capsys.readouterr()
    run("show", "provider", "llm", "claude")
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
    assert run("set", "provider", "llm", "claude", "-f", "-", stdin=fragment) == 1, what

    captured = capsys.readouterr()
    assert expected in captured.err, what
    assert "Traceback" not in captured.err, what
    assert captured.out == "", what

    # And nothing was written: the entity does not exist.
    assert run("show", "provider", "llm", "claude") == 1
    assert NO_SUCH_PROVIDER in capsys.readouterr().err


def test_a_fragment_sharing_one_anchor_twice_still_travels(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The check refuses a structure that contains itself, not one that
    mentions the same anchor twice, which is an ordinary YAML file and
    is written out twice in JSON."""
    fragment = "type: anthropic\none: &shared\n  a: 1\ntwo: *shared\n"

    assert run("set", "provider", "llm", "claude", "-f", "-", stdin=fragment) == 0
    capsys.readouterr()

    run("show", "provider", "llm", "claude")
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
    run("set", "provider", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")
    run(
        "set",
        "mcp-server",
        "home",
        "-f",
        "-",
        stdin="transport: stdio\ncommand: uvx\n",
    )
    run("set", "agent-defaults", "-f", "-", stdin="llm: claude\n")
    run("set", "agent", "sam", "-f", "-", stdin="prompt: You are Sam.\n")
    run("bind-device", "aa:bb:cc:dd:ee:ff", "sam")
    capsys.readouterr()

    run("show", "mcp-server", "home")
    assert _document(capsys.readouterr().out)["command"] == "uvx"
    run("show", "agent", "sam")
    assert _document(capsys.readouterr().out)["prompt"] == "You are Sam."
    run("show", "agent-defaults")
    assert _document(capsys.readouterr().out) == {"llm": "claude"}
    run("show", "device", "AA-BB-CC-DD-EE-FF")
    assert _document(capsys.readouterr().out) == {"agents": ["sam"]}


def test_showing_something_that_is_not_there_names_the_section_only(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """One fixed sentence per section, and the identity that was asked
    for in none of them (#132)."""
    for argv, sentence in (
        (("show", "provider", "llm", "ghost"), NO_SUCH_PROVIDER),
        (("show", "mcp-server", "ghost"), NO_SUCH_MCP_SERVER),
        (("show", "prompt-fragment", "ghost"), NO_SUCH_FRAGMENT),
        (("show", "agent", "ghost"), NO_SUCH_AGENT),
        (("show", "device", "aa:bb:cc:dd:ee:ff"), NO_SUCH_DEVICE),
    ):
        assert run(*argv) == 1
        captured = capsys.readouterr()
        assert captured.err == f"{sentence}\n"
        assert "Traceback" not in captured.err


def test_the_default_agent_can_be_cleared(run, capsys: pytest.CaptureFixture[str]) -> None:
    run("set", "agent", "sam", "-f", "-", stdin="prompt: You are Sam.\n")
    run("set-default-agent", "sam")
    capsys.readouterr()

    assert run("clear-default-agent") == 0
    run("list")
    assert "default_agent: (none)" in capsys.readouterr().out


def test_a_row_of_the_wrong_shape_is_reported_rather_than_raised(
    run, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The reading commands are what an operator reaches for when
    something is wrong with the database, so a row that cannot be read
    has to come back as a sentence rather than as a traceback."""
    run("set", "provider", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")
    capsys.readouterr()

    engine = open_database(tmp_path / "db")
    try:
        with engine.begin() as connection:
            connection.execute(update(schema.providers).values(body="not json at all"))
    finally:
        engine.dispose()

    for argv in (("list",), ("show",), ("show", "provider", "llm", "claude")):
        assert run(*argv) == 1
        captured = capsys.readouterr()
        assert "providers.llm.claude" in captured.err
        assert "cannot be read as configuration" in captured.err
        assert "Traceback" not in captured.err


def test_a_database_that_cannot_be_opened_names_the_key(
    run, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("", encoding="utf-8")
    monkeypatch.setenv("VINGA_SERVER__DATABASE__DIR", str(blocker / "db"))

    assert run("list") == 1

    captured = capsys.readouterr()
    assert "server.database.dir" in captured.err
    assert "Traceback" not in captured.err


# Identities that only survive a URL path encoded


@pytest.mark.parametrize("name", ["a name with spaces", "100%-sure", "agente-café"])
def test_an_awkward_name_round_trips_through_the_whole_client(
    run, name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("set", "agent", name, "-f", "-", stdin="prompt: You are it.\n") == 0
    assert f"wrote agent {name}" in capsys.readouterr().out

    assert run("show", "agent", name) == 0
    assert _document(capsys.readouterr().out)["prompt"] == "You are it."

    assert run("delete", "agent", name) == 0
    capsys.readouterr()
    assert run("show", "agent", name) == 1
    assert NO_SUCH_AGENT in capsys.readouterr().err


def test_a_name_a_url_path_cannot_carry_is_refused(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """Refused at the repository when it can be reached, and unroutable
    when it cannot: either way no such entity is created."""
    assert run("set", "agent", "a/b", "-f", "-", stdin="prompt: You are it.\n") == 1
    capsys.readouterr()

    assert run("list") == 0
    assert "(none)" in capsys.readouterr().out
