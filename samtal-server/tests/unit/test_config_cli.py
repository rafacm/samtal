"""The config command group, driven through its entry function.

This is the acceptance suite for the whole write path. The commands are
the ones an operator types, parsed by the real grammar, sent as real
HTTP requests to the real sub-application, handled by the real
repository against a scratch database. What replaces the socket is the
client factory: Starlette's TestClient is itself a synchronous
`httpx.Client` subclass driving an ASGI application through its own
portal, so `cli.main()` stays the unchanged synchronous entry point and
nothing bridges an event loop.

The first test is the acceptance case: an empty database becomes a
working configuration through CLI calls alone, in the natural order,
with nothing wedging on the way. The rest is what has to hold around
it: the exact sentences, which is the regression net for "the API
carries the repository's message and the CLI prints it"; secrets masked
wherever they are read back; the restart notice on every write; the
transport policy that keeps the token off a clear connection; and no
failure path that lets a plaintext, a rejected fragment or a traceback
out.
"""

import logging
from pathlib import Path

import pytest
from sqlalchemy import update

from samtal_server.config import cli
from samtal_server.config.secrets import MASK, MASTER_KEY_ENV
from samtal_server.config.writes import BINDING_NOTICE
from samtal_server.db import open_database, schema
from tests.support.config_cli import (
    FRAGMENT_INPUT,
    FRAGMENT_TEXT,
    OTHER_SECRET,
    SECRET,
    runner,
)
from tests.support.config_cli import document as _document
from tests.support.config_cli import showing as _showing


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
    """The configuration is a boot-time snapshot by design, which makes
    a write that quietly waits for a restart the one thing about that
    design an operator can be caught by."""
    run("set", "provider", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")
    assert cli.RESTART_NOTICE in capsys.readouterr().err

    run("set", "agent", "sam", "-f", "-", stdin="llm: claude\n")
    assert cli.RESTART_NOTICE in capsys.readouterr().err

    run("set-default-agent", "sam")
    assert cli.RESTART_NOTICE in capsys.readouterr().err

    # A read is not a write, and says nothing.
    run("list")
    assert cli.RESTART_NOTICE not in capsys.readouterr().err


def test_a_device_write_says_the_device_meets_it_itself(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The exception to the boot-time snapshot, printed verbatim from
    what the API answered: a running server reads the devices table, so
    a delete reaches the device at its next check-in rather than at the
    next start."""
    run("set", "provider", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")
    run("set", "agent", "sam", "-f", "-", stdin="llm: claude\n")
    run("bind-device", "aa:bb:cc:dd:ee:ff", "sam")
    capsys.readouterr()

    assert run("delete", "device", "aa:bb:cc:dd:ee:ff") == 0

    assert BINDING_NOTICE in capsys.readouterr().err


def test_a_local_device_delete_says_the_same_thing(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The break-glass path writes the same row, so it says the same
    sentence: the two paths must not describe one act differently."""
    run("set", "provider", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")
    run("set", "agent", "sam", "-f", "-", stdin="llm: claude\n")
    run("bind-device", "aa:bb:cc:dd:ee:ff", "sam")
    capsys.readouterr()

    assert run("--local", "delete", "device", "aa:bb:cc:dd:ee:ff") == 0

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
    assert cli.RESTART_NOTICE in written.err

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


def test_a_prompt_fragment_reads_and_deletes_through_the_recovery_path(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--local` is the way in when the server will not start, and it
    covers this section the way it covers the others."""
    run("set", "prompt-fragment", "household", "-f", "-", stdin=FRAGMENT_INPUT)
    capsys.readouterr()

    assert run("--local", "show", "prompt-fragment", "household") == 0
    assert _document(capsys.readouterr().out) == {"text": FRAGMENT_TEXT}

    assert run("--local", "delete", "prompt-fragment", "household") == 0
    assert capsys.readouterr().out == "wrote prompt-fragment household deleted\n"

    assert run("show", "prompt-fragment", "household") == 1
    assert "no prompt fragment of that name exists" in capsys.readouterr().err


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


@pytest.mark.parametrize("name", [SECRET, f"{SECRET}.pasted"])
@pytest.mark.parametrize("local", [False, True])
def test_a_fragment_that_is_not_there_is_refused_without_printing_it(
    run, capsys: pytest.CaptureFixture[str], name: str, local: bool
) -> None:
    """Both ways in, since the recovery path opens the database itself
    and has to say the same sentence: a name that addresses no fragment
    was typed rather than stored, so neither the read nor the delete
    repeats it."""
    flags = ("--local",) if local else ()
    for argv in (
        (*flags, "show", "prompt-fragment", name),
        (*flags, "delete", "prompt-fragment", name),
    ):
        assert run(*argv) == 1

        captured = capsys.readouterr()
        assert "no prompt fragment of that name exists" in captured.err
        assert SECRET not in captured.err
        assert SECRET not in captured.out
        assert "Traceback" not in captured.err


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
        record for record in caplog.records if record.name.startswith("samtal_server")
    ]
    assert all(SECRET not in record.getMessage() for record in served)
    assert all(SECRET not in str(record.__dict__) for record in served)


def test_the_status_help_names_every_state_it_can_print() -> None:
    """`unused` is a state of its own and the one an operator has never
    met before, so leaving it out of the help would leave it out of the
    place they look first."""
    # Whitespace collapsed, because argparse wraps the line it is
    # printed on and where it wraps is not the contract.
    help_text = " ".join(cli._parser().format_help().split())

    assert "connected, down, or unused because no agent references it" in help_text


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
    # The restart sentence rather than the no-restart one, because the
    # application this fixture builds is told of no loaded agents, which
    # is the honest answer for one built without a server around it. The
    # two notices are told apart in test_config_api_pending.py.
    assert cli.RESTART_NOTICE in captured.err


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


def test_the_two_ways_to_bind_a_board_say_which_is_which() -> None:
    """A pair a person picks wrongly once and then remembers wrongly,
    so each names what the other takes."""
    help_text = cli._parser().format_help()  # noqa: SLF001

    assert "by the MAC you already know" in help_text
    assert "showing this activation code" in help_text


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
    assert "no such provider" in capsys.readouterr().err


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


def test_showing_something_that_is_not_there_names_it(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    for argv in (
        ("show", "provider", "llm", "ghost"),
        ("show", "mcp-server", "ghost"),
        ("show", "agent", "ghost"),
        ("show", "device", "aa:bb:cc:dd:ee:ff"),
    ):
        assert run(*argv) == 1
        captured = capsys.readouterr()
        assert "no such" in captured.err
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
            connection.execute(update(schema.providers).values(options="not an object"))
    finally:
        engine.dispose()

    for argv in (("list",), ("show",), ("show", "provider", "llm", "claude")):
        assert run(*argv) == 1
        captured = capsys.readouterr()
        assert "options" in captured.err
        assert "Traceback" not in captured.err


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


def test_a_database_that_cannot_be_opened_names_the_key(
    run, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("", encoding="utf-8")
    monkeypatch.setenv("SAMTAL_SERVER__DATABASE__DIR", str(blocker / "db"))

    assert run("list") == 1

    captured = capsys.readouterr()
    assert "server.database.dir" in captured.err
    assert "Traceback" not in captured.err


# The recovery subset


def test_every_local_invocation_says_what_it_is(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """There is no reliable way to tell whether a server is running
    against the same file, so the honest substitute for a refusal is
    saying what this path is, every time, reads included."""
    run("set", "provider", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")
    capsys.readouterr()

    assert run("--local", "show") == 0
    assert "bypassing the configuration API" in capsys.readouterr().err

    assert run("--local", "delete", "provider", "llm", "claude") == 0
    assert "bypassing the configuration API" in capsys.readouterr().err


# What a local write says it did, against what the API says for the same
# act
#
# The two paths write the same rows, so they may not describe one act
# differently, and a sentence copied into the break-glass branch by hand
# is a sentence that drifts. The expected value here is therefore not a
# constant but the ordinary path's own answer for the same act, captured
# a moment earlier against state put back to what the local run then
# meets, so a change to either side that the other does not follow fails
# rather than passing quietly.
#
# The nine cases are the whole `--local` mutating subset as the grammar
# stands: five deletes, and set-secret and clear-secret on each kind of
# entity a secret lives on. That completeness is kept by review, not by
# machinery: the grammar is imperative parser construction, so a new
# `local_ok=True` command that skips this list fails nothing.


# The preamble, spelled out rather than read from the module under test.
# Comparing production against itself would let the retired sentence
# back in: restoring "a running server will not observe a change made
# this way until its next start" would move both sides of that
# comparison together and pass, while printing a timing claim the write
# under it contradicts. The neutral sentence is therefore a literal
# here, and the one place the two are tied together is the assertion
# just below.
LOCAL_PREAMBLE = (
    "--local is the break-glass path: it reads and writes the database directly, "
    "bypassing the configuration API. Each write says separately when it takes "
    "effect, the same answer the API gives for the same act."
)

# How restart timing has been written on this path, in the words it has
# been written in: the two halves of RESTART_NOTICE, and the clause the
# retired preamble made the claim with. An act a reload applies may
# carry none of them, whichever line they turn up on. Kept as phrases
# rather than as the word "restart", which MCP_RELOAD_NOTICE uses
# legitimately to say that none is needed.
RESTART_TIMING = (
    "until its next start",
    "at the next server start",
    "read once at boot",
)


def test_the_local_preamble_makes_no_timing_claim_of_its_own() -> None:
    """Every --local invocation prints this before the command runs, so
    a timing claim in it is a timing claim about every act, including
    the ones a running server applies without a restart. It says what
    the path is and leaves when to the write."""
    assert cli.LOCAL_NOTICE == LOCAL_PREAMBLE
    for phrasing in RESTART_TIMING:
        assert phrasing not in cli.LOCAL_NOTICE, phrasing


def _a_provider(run) -> None:
    run("set", "provider", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")


def _an_mcp_server(run) -> None:
    run("set", "mcp-server", "home", "-f", "-", stdin="transport: stdio\ncommand: uvx\n")


def _a_prompt_fragment(run) -> None:
    run("set", "prompt-fragment", "household", "-f", "-", stdin=FRAGMENT_INPUT)


def _an_unreferenced_agent(run) -> None:
    """Nothing names it, so the delete is not refused for a reason that
    has nothing to do with what it would then say."""
    run("set", "agent", "sam", "-f", "-", stdin="prompt: You are Sam.\n")


def _a_bound_device(run) -> None:
    _an_unreferenced_agent(run)
    run("bind-device", "aa:bb:cc:dd:ee:ff", "sam")


def _a_provider_secret(run) -> None:
    _a_provider(run)
    run("set-secret", "provider", "llm", "claude", "api_key", stdin=SECRET)


def _an_mcp_secret(run) -> None:
    _an_mcp_server(run)
    run("set-secret", "mcp-server", "home", "env.API_TOKEN", stdin=OTHER_SECRET)


# Each case: what the act needs in the database, the act itself, and
# whether it is one a running server applies by reloading.
LOCAL_MUTATIONS = [
    (_a_provider, ("delete", "provider", "llm", "claude"), False),
    (_an_mcp_server, ("delete", "mcp-server", "home"), True),
    (_a_prompt_fragment, ("delete", "prompt-fragment", "household"), False),
    (_an_unreferenced_agent, ("delete", "agent", "sam"), False),
    (_a_bound_device, ("delete", "device", "aa:bb:cc:dd:ee:ff"), False),
    (_a_provider, ("set-secret", "provider", "llm", "claude", "api_key"), False),
    (_an_mcp_server, ("set-secret", "mcp-server", "home", "env.API_TOKEN"), True),
    (_a_provider_secret, ("clear-secret", "provider", "llm", "claude", "api_key"), False),
    (_an_mcp_secret, ("clear-secret", "mcp-server", "home", "env.API_TOKEN"), True),
]


@pytest.mark.parametrize(
    ("seed", "argv", "reloadable"),
    LOCAL_MUTATIONS,
    ids=[" ".join(argv) for _, argv, _ in LOCAL_MUTATIONS],
)
def test_a_local_write_says_what_the_api_says_for_the_same_act(
    run, capsys: pytest.CaptureFixture[str], seed, argv: tuple[str, ...], reloadable: bool
) -> None:
    """Run one act both ways against equivalent state, and pin the whole
    shape of what the break-glass path printed: what it is, then exactly
    the sentence the ordinary path answered, and nothing else.

    Equivalent is established between the runs rather than assumed, by
    taking the entity out and seeding it again; the comment below says
    what re-seeding alone would have left behind.

    Not the last line alone. The contradiction this exists to catch is a
    preamble that reasserts restart timing ahead of a reload notice,
    which a last-line comparison would step straight over."""
    typed = SECRET if argv[0] == "set-secret" else None

    seed(run)
    capsys.readouterr()
    assert run(*argv, stdin=typed) == 0
    answered = capsys.readouterr().err.rstrip("\n")

    # Back to nothing before the second run, because re-seeding is not
    # by itself a reset: a write that names only an entity's
    # model-shaped columns leaves the rest as it was, the secrets column
    # above all, so seeding a provider again after a set-secret leaves
    # the credential the act just stored. The second run would then be
    # rotating a secret where the first created one, which is a
    # different act from the one being compared. A delete takes the row
    # and its stored secrets together; the acts that are deletes have
    # already left nothing behind, and there is nothing to address.
    if argv[0] != "delete":
        assert run("delete", *argv[1:-1]) == 0

    seed(run)
    capsys.readouterr()
    assert run("--local", *argv, stdin=typed) == 0

    said = capsys.readouterr().err
    assert said.splitlines() == [LOCAL_PREAMBLE, answered]
    if reloadable:
        # Said out loud for the acts the reload applies: the restart
        # sentence must not appear anywhere in this invocation, preamble
        # included, and neither must the phrasings a differently worded
        # restart claim would be made in.
        assert cli.RESTART_NOTICE not in said
        for phrasing in RESTART_TIMING:
            assert phrasing not in said, phrasing


def test_the_recovery_subset_needs_no_server(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The situation --local exists for: the four commands run against
    the database with nothing to ask, which is what `reached` staying
    empty says."""
    run("set", "provider", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")
    run.reached.clear()
    capsys.readouterr()

    assert run("--local", "set-secret", "provider", "llm", "claude", "api_key", stdin=SECRET) == 0
    assert run("--local", "show", "provider", "llm", "claude") == 0
    shown = capsys.readouterr().out
    assert f"api_key: {MASK}" in shown
    assert SECRET not in shown

    assert run("--local", "clear-secret", "provider", "llm", "claude", "api_key") == 0
    assert run("--local", "delete", "provider", "llm", "claude") == 0
    assert run.reached == []


def test_the_recovery_subset_works_with_a_key_that_will_not_load(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A `SAMTAL_MASTER_KEY` that is not a Fernet key is one of the exact
    conditions --local exists to repair: it refuses the boot, so there is
    no server to ask, and reading the keys eagerly would refuse the
    recovery tool for the same reason.

    Reading, deleting and clearing all treat ciphertext as opaque, so
    none of them needs a key at all."""
    run("set", "provider", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")
    run("set-secret", "provider", "llm", "claude", "api_key", stdin=SECRET)
    run.reached.clear()
    capsys.readouterr()
    monkeypatch.setenv(MASTER_KEY_ENV, "not-a-fernet-key")

    assert run("--local", "show") == 0
    whole = capsys.readouterr().out
    assert MASK in whole
    assert SECRET not in whole

    assert run("--local", "show", "provider", "llm", "claude") == 0
    assert f"api_key: {MASK}" in capsys.readouterr().out

    assert run("--local", "clear-secret", "provider", "llm", "claude", "api_key") == 0
    capsys.readouterr()
    assert run("--local", "delete", "provider", "llm", "claude") == 0
    capsys.readouterr()
    assert run.reached == []


def test_storing_a_secret_locally_still_needs_a_usable_key(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The one recovery command that cannot work without one, because it
    encrypts. It names the variable and never the material."""
    run("set", "provider", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")
    capsys.readouterr()
    monkeypatch.setenv(MASTER_KEY_ENV, "not-a-fernet-key")

    assert run("--local", "set-secret", "provider", "llm", "claude", "api_key", stdin=SECRET) == 1

    captured = capsys.readouterr()
    assert MASTER_KEY_ENV in captured.err
    assert "not-a-fernet-key" not in captured.err
    assert SECRET not in captured.err
    assert "Traceback" not in captured.err


def test_local_delete_removes_the_row_that_is_keeping_the_server_down(
    run, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The whole point of the break-glass path, end to end: a row the
    loader refuses is the row stopping the boot, so it is the one that
    has to come out, and every reading command refuses it on the way."""
    run("set", "provider", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")
    run("set", "provider", "asr", "whisper", "-f", "-", stdin="type: mock\n")
    capsys.readouterr()
    engine = open_database(tmp_path / "db")
    try:
        with engine.begin() as connection:
            connection.execute(
                update(schema.providers)
                .where(schema.providers.c.name == "claude")
                .values(options="not an object")
            )
    finally:
        engine.dispose()
    # Nothing can read it, which is the state a server meets at boot.
    assert run("--local", "show") == 1
    assert "options" in capsys.readouterr().err

    assert run("--local", "delete", "provider", "llm", "claude") == 0
    capsys.readouterr()

    # And with it gone the configuration reads again.
    assert run("--local", "show") == 0
    assert "whisper" in capsys.readouterr().out


def test_a_command_outside_the_subset_is_refused_naming_it(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    for argv in (
        ("--local", "list"),
        ("--local", "set", "agent", "sam", "-f", "-"),
        ("--local", "bind-device", "aa:bb:cc:dd:ee:ff", "sam"),
        ("--local", "set-default-agent", "sam"),
        ("--local", "clear-default-agent"),
    ):
        assert run(*argv, stdin="prompt: x\n") == 1, argv
        captured = capsys.readouterr()
        assert "show, delete, clear-secret and set-secret" in captured.err, argv
        assert captured.out == ""
    assert run.reached == []


def test_the_flag_is_accepted_after_its_command_too(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    run("set", "provider", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")
    run.reached.clear()
    capsys.readouterr()

    assert run("show", "provider", "llm", "claude", "--local") == 0
    assert run.reached == []


def test_local_show_reaches_a_name_no_new_write_could_create(
    run, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The reason the recovery subset goes by membership rather than by
    the write-time addressability rule: a row written before that rule
    existed has to stay readable and removable, and it cannot be reached
    over a URL path at all."""
    run("list")
    capsys.readouterr()
    engine = open_database(tmp_path / "db")
    try:
        with engine.begin() as connection:
            connection.execute(
                schema.providers.insert().values(
                    stage="llm", name="a/b", type="mock", egress=None, options={}, secrets={}
                )
            )
    finally:
        engine.dispose()

    assert run("--local", "show", "provider", "llm", "a/b") == 0
    assert "type: mock" in capsys.readouterr().out

    assert run("--local", "delete", "provider", "llm", "a/b") == 0
    capsys.readouterr()
    assert run("--local", "show", "provider", "llm", "a/b") == 1
    assert "no such provider" in capsys.readouterr().err


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
    assert "no such agent" in capsys.readouterr().err


def test_a_name_a_url_path_cannot_carry_is_refused(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """Refused at the repository when it can be reached, and unroutable
    when it cannot: either way no such entity is created."""
    assert run("set", "agent", "a/b", "-f", "-", stdin="prompt: You are it.\n") == 1
    capsys.readouterr()

    assert run("list") == 0
    assert "(none)" in capsys.readouterr().out
