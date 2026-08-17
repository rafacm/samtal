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

import io
import logging
import sqlite3
import sys
from pathlib import Path

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import update

import samtal_server.tools.mcp as mcp_module
from samtal_server import db as db_module
from samtal_server.config import Config, cli
from samtal_server.config.api import MOUNT_PATH, build_api
from samtal_server.config.loader import ConfigError
from samtal_server.config.secrets import MASK, MASTER_KEY_ENV
from samtal_server.config.writes import BINDING_NOTICE
from samtal_server.db import DATABASE_FILENAME, open_database, schema
from samtal_server.tools.mcp import McpReload, McpServers
from tests.support.config_cli import (
    API_SECRET_ENV,
    FRAGMENT_INPUT,
    FRAGMENT_TEXT,
    OTHER_SECRET,
    SECRET,
    TOKEN,
    runner,
)
from tests.support.config_cli import chain as _chain
from tests.support.config_cli import document as _document
from tests.support.config_cli import showing as _showing

# Short enough that a blocked writer gives up inside a test run, and
# long enough that an unblocked one never sees it.
SHORT_BUSY_MS = 200


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


def test_pending_lists_nothing_when_nothing_is_waiting(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("pending") == 0

    assert capsys.readouterr().out.startswith("no device is waiting to be claimed")


def test_pending_lists_the_code_each_device_is_showing(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """What an operator reads while holding a board: the digits to type,
    the MAC they will bind, and what tells two boards apart."""
    first = _showing(run)
    second = _showing(run, "11:22:33:44:55:66")

    assert run("pending") == 0

    printed = capsys.readouterr().out
    assert printed.splitlines()[0].split() == ["code", "device", "board", "firmware", "expires"]
    assert first in printed
    assert second in printed
    assert "aa:bb:cc:dd:ee:ff" in printed
    assert "waveshare-esp32-s3-touch-lcd-1.54" in printed
    assert "2.4.0" in printed


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


# What the MCP servers are doing
#
# The other read of the running server rather than of the database.
# Nothing is connected here: a live connection is a subprocess or a
# socket, and what these are about is the rendering of the three states
# and the shape the client insists on.


def _configured(servers: dict[str, object], grants: dict[str, list]) -> McpServers:
    """A registry built from a configuration, the way a server builds
    one, and never started, so everything referenced is down."""
    config = Config(
        server={},
        providers={
            stage: {"mock": {"type": "mock"}} for stage in ("llm", "asr", "tts", "vad")
        },
        mcp_servers=servers,
        agent_defaults=dict.fromkeys(("llm", "asr", "tts", "vad"), "mock"),
        agents={
            name: {"prompt": "A", "mcp": entries} for name, entries in grants.items()
        },
        default_agent=next(iter(grants)),
    )
    return McpServers.build(config)


def test_status_says_so_when_nothing_is_configured(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("status") == 0

    assert capsys.readouterr().out.startswith("this server has no MCP servers configured")


def test_status_shows_each_entry_its_state_and_who_may_reach_it(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    entry = {"transport": "streamable_http", "url": "http://127.0.0.1:9/mcp"}
    run.runtime["mcp_servers"] = _configured(
        {"weather": entry, "shelved": entry}, {"sam": ["weather"]}
    )

    assert run("status") == 0

    printed = capsys.readouterr().out
    assert "weather: down since " in printed
    assert "  agents: sam" in printed
    # The state that exists only on this surface: configured, referenced
    # by nobody, so no connection was ever built for it.
    assert "shelved: unused since " in printed
    # A server with nothing published and nobody granted says so rather
    # than printing an empty line.
    assert "  tools: (none)" in printed


def test_status_lists_the_agents_of_an_entry_in_name_order(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The order the README's sample output is written in, pinned here
    so the two cannot drift."""
    entry = {"transport": "streamable_http", "url": "http://127.0.0.1:9/mcp"}
    run.runtime["mcp_servers"] = _configured(
        {"home": entry}, {"kids": ["home"], "house": ["home"]}
    )

    assert run("status") == 0

    assert "  agents: house, kids" in capsys.readouterr().out


def test_status_shows_how_much_of_a_server_each_agent_gets(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The allow list beside the published list, which is what makes a
    grant naming a tool the server does not offer answerable without a
    second read."""
    entry = {"transport": "streamable_http", "url": "http://127.0.0.1:9/mcp"}
    run.runtime["mcp_servers"] = _configured(
        {"weather": entry}, {"sam": [{"server": "weather", "tools": ["forecast", "wind"]}]}
    )

    assert run("status") == 0

    assert "  agents: sam (forecast, wind)" in capsys.readouterr().out


def test_the_status_help_names_every_state_it_can_print() -> None:
    """`unused` is a state of its own and the one an operator has never
    met before, so leaving it out of the help would leave it out of the
    place they look first."""
    # Whitespace collapsed, because argparse wraps the line it is
    # printed on and where it wraps is not the contract.
    help_text = " ".join(cli._parser().format_help().split())

    assert "connected, down, or unused because no agent references it" in help_text


def test_status_refuses_an_answer_it_cannot_read(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A body without the fields a status entry carries did not come
    from this API, and a proxy's page is not rendered as though it
    had."""
    monkeypatch.setattr(cli, "_call", lambda *_args, **_kwargs: {"weather": {"up": True}})

    assert run("status") == 1

    assert cli.UNRECOGNIZED_ANSWER in capsys.readouterr().err


def _status_entry(**overrides: object) -> dict[str, object]:
    """One entry as the API answers it, with one field replaced by
    whatever a test wants to see refused."""
    return {
        "state": "connected",
        "reason": None,
        "since": "2026-08-13T09:12:03.104213+00:00",
        "tools": ["weather__forecast"],
        "grants": {"sam": None},
    } | overrides


# Not a real credential, and shaped so a substring check for it cannot
# match by accident. Placed where a body's own values would be printed.
ANSWERED = "sk-test-0c9b41ae-never-a-real-credential"


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"weather": _status_entry(state={"leak": ANSWERED})}, id="state-object"),
        pytest.param({"weather": _status_entry(state=ANSWERED)}, id="state-not-a-state"),
        pytest.param({"weather": _status_entry(since={"leak": ANSWERED})}, id="since-object"),
        pytest.param({"weather": _status_entry(reason={"leak": ANSWERED})}, id="reason-object"),
        pytest.param({"weather": _status_entry(tools=[{"leak": ANSWERED}])}, id="tool-object"),
        pytest.param({"weather": _status_entry(tools={"leak": ANSWERED})}, id="tools-object"),
        pytest.param({"weather": _status_entry(grants=[ANSWERED])}, id="grants-list"),
        pytest.param(
            {"weather": _status_entry(grants={"sam": {"leak": ANSWERED}})}, id="grant-object"
        ),
        pytest.param({"weather": ANSWERED}, id="entry-not-an-object"),
        pytest.param([{"leak": ANSWERED}], id="document-not-an-object"),
    ],
)
def test_status_prints_nothing_from_an_answer_of_the_wrong_shape(
    body: object, run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The renderer prints what it is given, so what it is given is
    checked all the way down first. Every one of these carries a value
    where a printed one belongs, and none of them may reach the
    terminal: a body this client cannot recognize did not come from this
    API's sanitized output, and what a proxy or a captive portal returns
    is text nobody vouched for."""
    monkeypatch.setattr(cli, "_call", lambda *_args, **_kwargs: body)

    assert run("status") == 1

    captured = capsys.readouterr()
    assert cli.UNRECOGNIZED_ANSWER in captured.err
    assert ANSWERED not in captured.err + captured.out
    assert "Traceback" not in captured.err


def test_the_valid_shape_those_refusals_were_built_from_is_accepted(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The control for the parametrization above: each of those bodies
    is this one with a single field replaced, so this is what makes the
    refusals about the replacement."""
    monkeypatch.setattr(cli, "_call", lambda *_args, **_kwargs: {"weather": _status_entry()})

    assert run("status") == 0

    assert "weather: connected since 2026-08-13T09:12" in capsys.readouterr().out


def test_a_status_refusal_carries_nothing_of_the_body() -> None:
    """The sentence and nothing behind it. A refusal raised while an
    exception was being handled would keep that one as its context, and
    anything walking the chain would find the body on it."""
    body = {"weather": _status_entry(since={"leak": ANSWERED})}

    with pytest.raises(ConfigError) as caught:
        cli._status_listing(body)

    assert ANSWERED not in _chain(caught.value)


# The assembled prompt
#
# The renderer is the point of these: `config prompt` is an inspection
# command, so it prints whole blocks, and everything else in this module
# prints through a renderer that strips and truncates.


def _prompt_block(**overrides: object) -> dict[str, object]:
    return {"provenance": "persona", "characters": 4, "text": "POET"} | overrides


def _assembled(*blocks: dict[str, object], characters: int = 4) -> dict[str, object]:
    return {"blocks": list(blocks) or [_prompt_block()], "characters": characters}


def _previewing(assembled: object):
    async def assemble(agent: str) -> object:
        return assembled if agent == "poet" else None

    return assemble


def test_prompt_prints_each_block_its_size_and_the_total(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    body = _assembled(
        _prompt_block(),
        _prompt_block(
            provenance="instructions:home", characters=20, text="Heading:\nAsk first."
        ),
        characters=26,
    )
    monkeypatch.setattr(cli, "_call", lambda *_args, **_kwargs: body)

    assert run("prompt", "poet") == 0

    printed = capsys.readouterr().out
    assert "persona (4 characters)" in printed
    assert "POET" in printed
    assert "instructions:home (20 characters)" in printed
    assert "Ask first." in printed
    assert printed.endswith("total: 26 characters\n")


def test_prompt_never_truncates_a_block(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The whole reason this command does not render through
    `_printable`: a realistic prompt is far longer than GLIMPSE_LENGTH,
    and a concealed tail is exactly what an operator came to see."""
    tail = "END-OF-THE-PROMPT"
    long_block = "x" * (cli.GLIMPSE_LENGTH * 3) + tail
    body = _assembled(
        _prompt_block(text=long_block, characters=len(long_block)),
        characters=len(long_block),
    )
    monkeypatch.setattr(cli, "_call", lambda *_args, **_kwargs: body)

    assert run("prompt", "poet") == 0

    printed = capsys.readouterr().out
    assert tail in printed
    assert long_block in printed
    assert "..." not in printed


def test_prompt_keeps_the_newlines_and_replaces_the_control_characters(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A prompt is written in newlines and tabs, so they pass; anything
    else unprintable is replaced rather than dropped, so a block that
    arrived mangled reads as mangled and an escape sequence cannot drive
    the terminal."""
    text = "first line\n\tindented\x1b[31mred\x07"
    body = _assembled(_prompt_block(text=text, characters=len(text)))
    monkeypatch.setattr(cli, "_call", lambda *_args, **_kwargs: body)

    assert run("prompt", "poet") == 0

    printed = capsys.readouterr().out
    assert "first line\n\tindented?[31mred?" in printed
    assert "\x1b" not in printed
    assert "\x07" not in printed
    # The count is the server's, counting what is stored, so a
    # replacement never falsifies it.
    assert f"({len(text)} characters)" in printed


def test_prompt_sanitizes_a_published_prompts_name(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A published prompt's name is a string the server chose and the
    operator copied, so it is the one value on this surface that may
    hold an escape sequence. It goes through the block sanitizer with
    the provenance and the text."""
    body = _assembled(
        _prompt_block(
            provenance="server_prompt:home:1",
            name="house\x1b[31m_style",
            characters=8,
            text="Be brief.",
        ),
        characters=8,
    )
    monkeypatch.setattr(cli, "_call", lambda *_args, **_kwargs: body)

    assert run("prompt", "poet") == 0

    printed = capsys.readouterr().out
    assert "server_prompt:home:1 (8 characters), the server prompt named house?[31m_style" in (
        printed
    )
    assert "\x1b" not in printed


def test_prompt_names_nothing_beside_a_block_that_has_no_name(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "_call", lambda *_args, **_kwargs: _assembled())

    assert run("prompt", "poet") == 0

    assert "persona (4 characters)\n" in capsys.readouterr().out


def test_prompt_says_what_the_server_answered_for_an_unloaded_agent(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    run.runtime["agent_prompt"] = _previewing(_assembled())

    assert run("prompt", "stranger") == 1

    assert "restart" in capsys.readouterr().err


def test_prompt_without_a_server_says_so(run, capsys: pytest.CaptureFixture[str]) -> None:
    assert run("prompt", "poet") == 1

    assert "no running server" in capsys.readouterr().err


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(
            {"blocks": [_prompt_block(provenance=None)], "characters": 4}, id="provenance"
        ),
        pytest.param({"blocks": [_prompt_block(text=None)], "characters": 4}, id="text"),
        pytest.param({"blocks": [_prompt_block(characters=ANSWERED)], "characters": 4}, id="size"),
        pytest.param({"blocks": [{"leak": ANSWERED}], "characters": 4}, id="block-fields"),
        pytest.param({"blocks": [_prompt_block(name=4)], "characters": 4}, id="name"),
        pytest.param({"blocks": ANSWERED, "characters": 4}, id="blocks-not-a-list"),
        pytest.param({"blocks": [], "characters": ANSWERED}, id="total"),
        pytest.param([_prompt_block()], id="document-not-an-object"),
    ],
)
def test_prompt_prints_nothing_from_an_answer_of_the_wrong_shape(
    body: object, run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "_call", lambda *_args, **_kwargs: body)

    assert run("prompt", "poet") == 1

    captured = capsys.readouterr()
    assert cli.UNRECOGNIZED_ANSWER in captured.err
    assert ANSWERED not in captured.err + captured.out
    assert "Traceback" not in captured.err


def test_a_prompt_refusal_carries_nothing_of_the_body() -> None:
    with pytest.raises(ConfigError) as caught:
        cli._prompt_listing({"blocks": [{"leak": ANSWERED}], "characters": 4})

    assert ANSWERED not in _chain(caught.value)


# Applying them, which is the other half of the same surface
#
# The registry's own diff is exercised against real servers elsewhere;
# what these are about is the command: that it reaches the right route,
# renders both halves of the answer, waits long enough for one, and
# refuses a body that is not one.


def _applied(**outcome: object):
    """A server that answers a reload with what it did, standing in for
    a registry these tests deliberately do not start."""

    async def reload() -> McpReload:
        return McpReload(**outcome)

    return reload


def test_reload_prints_what_it_did_and_what_is_running(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    entry = {"transport": "streamable_http", "url": "http://127.0.0.1:9/mcp"}
    run.runtime["mcp_servers"] = _configured({"weather": entry}, {"sam": ["weather"]})
    run.runtime["mcp_reload"] = _applied(started=("weather",), stopped=("gone",))

    assert run("reload") == 0

    printed = capsys.readouterr().out
    assert "started: weather" in printed
    assert "restarted: (none)" in printed
    assert "stopped: gone" in printed
    assert "unchanged: (none)" in printed
    # And the status underneath, which is what says whether an entry
    # that started actually connected.
    assert "weather: down since " in printed


def test_reload_prints_the_refusal_the_api_answered(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """No server to reload is a 503 with a sentence, and the sentence is
    what an operator reads: this client adds nothing to it."""
    assert run("reload") == 1

    assert "no running server" in capsys.readouterr().err


def test_reload_refuses_an_answer_it_cannot_read(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "_call", lambda *_args, **_kwargs: {"started": "weather"})

    assert run("reload") == 1

    assert cli.UNRECOGNIZED_ANSWER in capsys.readouterr().err


def _reload_answer(**overrides: object) -> dict[str, object]:
    """One reload answer as the API returns it, with one field replaced
    by whatever a test wants to see refused."""
    return (
        dict.fromkeys(cli.RELOAD_OUTCOMES, [])
        | {"servers": {"weather": _status_entry()}}
        | overrides
    )


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(_reload_answer(started=[{"leak": ANSWERED}]), id="outcome-object"),
        pytest.param(_reload_answer(unchanged=ANSWERED), id="outcome-not-a-list"),
        pytest.param(
            {outcome: [] for outcome in cli.RELOAD_OUTCOMES}, id="servers-missing"
        ),
        pytest.param(
            _reload_answer(servers={"weather": _status_entry(state=ANSWERED)}),
            id="servers-invalid",
        ),
    ],
)
def test_reload_prints_nothing_from_an_answer_of_the_wrong_shape(
    body: object, run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The status rules apply to the reload's answer too: `_names`
    prints every element of the outcome lists, and the status half is
    the same document the status command refuses when it cannot read
    it, so a stray shape anywhere must end in the fixed sentence rather
    than in output or a traceback."""
    monkeypatch.setattr(cli, "_call", lambda *_args, **_kwargs: body)

    assert run("reload") == 1

    captured = capsys.readouterr()
    assert cli.UNRECOGNIZED_ANSWER in captured.err
    assert ANSWERED not in captured.err + captured.out
    assert "Traceback" not in captured.err


def test_reload_gives_the_server_longer_to_answer_than_a_write(
    run, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The client must not give up on a reload the server then applies:
    that would leave nobody knowing what is running, which is the exact
    ambiguity this whole feature exists to remove. So the bound is the
    server's own envelope with room to spare, and the command really
    does use it.

    Driven through a real `httpx.Client` over a mock transport rather
    than through the fixture's TestClient, which carries a timeout from
    another copy of httpx entirely and would report whatever that made
    of one.
    """
    envelope = (
        mcp_module.CONNECT_TIMEOUT_S
        + mcp_module.STOP_TIMEOUT_S
        + mcp_module.CANCEL_TIMEOUT_S
    )
    assert cli.RELOAD_READ_TIMEOUT_S > cli.READ_TIMEOUT_S
    assert cli.RELOAD_READ_TIMEOUT_S >= 2 * envelope

    made: list[httpx.Client] = []
    empty = dict.fromkeys(cli.RELOAD_OUTCOMES, []) | {"servers": {}}

    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=empty if "reload" in request.url.path else {})

    def factory(base_url: str, token: str | None = None) -> httpx.Client:
        client = httpx.Client(
            base_url=base_url,
            transport=httpx.MockTransport(answer),
            timeout=httpx.Timeout(cli.READ_TIMEOUT_S, connect=cli.CONNECT_TIMEOUT_S),
        )
        made.append(client)
        return client

    monkeypatch.setattr(cli, "build_client", factory)

    assert run("status") == 0
    assert run("reload") == 0

    status_client, reload_client = made
    assert status_client.timeout.read == cli.READ_TIMEOUT_S
    assert reload_client.timeout.read == cli.RELOAD_READ_TIMEOUT_S
    # And the connect bound is untouched: a server that is not there
    # must not take a minute to say so.
    assert reload_client.timeout.connect == cli.CONNECT_TIMEOUT_S


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


def test_an_invalid_fragment_is_refused_without_echoing_it(
    run, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    """An inline secret in a fragment is the case where the rejected
    input is itself the thing that must not be printed back."""
    with caplog.at_level(logging.DEBUG):
        fragment = f"type: anthropic\napi_key: {SECRET}\n"
        assert run("set", "provider", "llm", "claude", "-f", "-", stdin=fragment) == 1

    captured = capsys.readouterr()
    assert "looks like an inline secret" in captured.err
    assert SECRET not in captured.err
    assert SECRET not in captured.out
    assert SECRET not in caplog.text


def test_a_secret_nested_in_an_option_is_refused_and_never_read_back(
    run, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    """A provider option can be a structure, so both halves are checked
    here: a secret-shaped key nested inside one is refused without
    quoting the value, and a nested reference key holding something
    that is not a reference is masked by `show` rather than printed."""
    nested = f"type: anthropic\nconnection:\n  api_key: {SECRET}\n"

    with caplog.at_level(logging.DEBUG):
        assert run("set", "provider", "llm", "claude", "-f", "-", stdin=nested) == 1

    captured = capsys.readouterr()
    assert "connection.api_key" in captured.err
    assert "looks like an inline secret" in captured.err
    assert SECRET not in captured.err
    assert SECRET not in captured.out
    assert SECRET not in caplog.text

    pasted = "sk_test_4f8b2c9e_never_a_real_credential"
    accepted = f"type: anthropic\nconnection:\n  api_key_env: {pasted}\n"
    assert run("set", "provider", "llm", "claude", "-f", "-", stdin=accepted) == 0
    capsys.readouterr()

    assert run("show", "provider", "llm", "claude") == 0
    shown = capsys.readouterr().out
    # Quoted by the YAML dumper, since the mask begins with an alias
    # indicator; what matters is that the value shown is the mask.
    assert yaml.safe_load(shown)["connection"]["api_key_env"] == MASK
    assert pasted not in shown


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


def test_a_secret_is_read_from_stdin_and_never_shown(
    run, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    run("set", "provider", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")
    capsys.readouterr()

    with caplog.at_level(logging.DEBUG):
        assert run("set-secret", "provider", "llm", "claude", "api_key", stdin=f"{SECRET}\n") == 0

    captured = capsys.readouterr()
    assert "wrote secret for provider llm.claude api_key" in captured.out
    assert SECRET not in captured.out
    assert SECRET not in captured.err
    assert SECRET not in caplog.text


def test_a_secret_can_come_from_a_named_variable(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("set", "provider", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")
    monkeypatch.setenv("SAMTAL_TEST_KEY", SECRET)

    assert run(
        "set-secret", "provider", "llm", "claude", "api_key", "--from-env", "SAMTAL_TEST_KEY"
    ) == 0

    monkeypatch.delenv("SAMTAL_TEST_KEY")
    assert run(
        "set-secret", "provider", "llm", "claude", "api_key", "--from-env", "SAMTAL_TEST_KEY"
    ) == 1
    captured = capsys.readouterr()
    assert "SAMTAL_TEST_KEY" in captured.err
    assert SECRET not in captured.err


def test_an_interactive_terminal_is_read_without_echo(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A typed secret must not land in the scrollback, so a terminal is
    read through getpass rather than by reading stdin."""
    run("set", "provider", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")

    class Terminal(io.StringIO):
        def isatty(self) -> bool:
            return True

    asked: list[str] = []
    monkeypatch.setattr(sys, "stdin", Terminal("this is never read\n"))
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: asked.append(prompt) or SECRET)

    assert run("set-secret", "provider", "llm", "claude", "api_key") == 0

    assert asked, "the terminal was read without getpass"
    assert SECRET not in capsys.readouterr().out


def test_an_empty_secret_is_refused(run, capsys: pytest.CaptureFixture[str]) -> None:
    run("set", "provider", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")

    assert run("set-secret", "provider", "llm", "claude", "api_key", stdin="\n") == 1
    assert "empty" in capsys.readouterr().err


def test_show_and_list_mask_stored_secrets_and_mark_what_they_shadow(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    run(
        "set",
        "provider",
        "llm",
        "claude",
        "-f",
        "-",
        stdin="type: anthropic\nmodel: m\napi_key_env: ANTHROPIC_API_KEY\n",
    )
    run(
        "set",
        "mcp-server",
        "weather",
        "-f",
        "-",
        stdin="transport: streamable_http\nurl: https://example.invalid/mcp\n",
    )
    run("set-secret", "provider", "llm", "claude", "api_key", stdin=SECRET)
    run("set-secret", "mcp-server", "weather", "headers.Authorization", stdin=OTHER_SECRET)
    capsys.readouterr()

    run("show")
    shown = capsys.readouterr().out
    assert MASK in shown
    assert SECRET not in shown
    assert OTHER_SECRET not in shown
    # The environment reference is not a secret, and the stored value
    # that displaces it is marked rather than left silent.
    assert "api_key_env: ANTHROPIC_API_KEY" in shown
    assert "used instead of api_key_env: ANTHROPIC_API_KEY" in shown

    run("show", "provider", "llm", "claude")
    assert f"api_key: {MASK}" in capsys.readouterr().out

    run("list")
    listed = capsys.readouterr().out
    assert "[secrets: api_key]" in listed
    assert "[secrets: headers.Authorization]" in listed
    assert SECRET not in listed and OTHER_SECRET not in listed


def test_a_pasted_credential_in_a_reference_field_is_refused(
    run, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    """An api_key_env holding the credential instead of its variable
    name would be written to the row unencrypted, and it never worked
    either. The write is refused, and the value the fragment carried
    goes nowhere: not to stdout, stderr or a log record."""
    fragment = f"type: anthropic\nmodel: m\napi_key_env: {SECRET}\n"

    with caplog.at_level(logging.DEBUG):
        assert run("set", "provider", "llm", "claude", "-f", "-", stdin=fragment) == 1

    captured = capsys.readouterr()
    assert "name of an environment variable" in captured.err
    assert SECRET not in captured.err
    assert SECRET not in captured.out
    assert SECRET not in caplog.text
    assert "Traceback" not in captured.err

    # And nothing was written: the entity does not exist.
    assert run("show", "provider", "llm", "claude") == 1
    assert "no such provider" in capsys.readouterr().err


def test_an_mcp_reference_shows_and_anything_else_in_its_place_does_not(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The model already requires a $VAR for a secret-bearing key, so a
    valid entry displays exactly as it was written; the mask is what
    covers a value that got in another way."""
    run(
        "set",
        "mcp-server",
        "weather",
        "-f",
        "-",
        stdin=(
            "transport: streamable_http\n"
            "url: https://example.invalid/mcp\n"
            "headers:\n"
            "  Authorization: $WEATHER_TOKEN\n"
            "  X-Region: eu\n"
        ),
    )
    capsys.readouterr()

    run("show", "mcp-server", "weather")
    shown = capsys.readouterr().out
    assert "$WEATHER_TOKEN" in shown
    # A key that carries no secret keeps its literal value: masking it
    # would hide configuration for nothing.
    assert "eu" in shown


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


def test_deleting_an_entity_takes_its_secrets_with_it(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    run("set", "provider", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")
    run("set-secret", "provider", "llm", "claude", "api_key", stdin=SECRET)
    capsys.readouterr()

    assert run("delete", "provider", "llm", "claude") == 0
    capsys.readouterr()

    run("list")
    listed = capsys.readouterr().out
    assert "claude" not in listed
    assert "[secrets:" not in listed


def test_a_secret_can_be_cleared(run, capsys: pytest.CaptureFixture[str]) -> None:
    run("set", "provider", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")
    run("set-secret", "provider", "llm", "claude", "api_key", stdin=SECRET)
    capsys.readouterr()

    assert run("clear-secret", "provider", "llm", "claude", "api_key") == 0
    run("list")
    assert "[secrets:" not in capsys.readouterr().out


def test_the_cli_still_works_when_the_key_is_missing_or_wrong(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The recovery case the boot check is deliberately kept out of
    opening the database for: reading, deleting and replacing all treat
    ciphertext as opaque, and only storing a new secret needs a key."""
    run("set", "provider", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")
    run("set-secret", "provider", "llm", "claude", "api_key", stdin=SECRET)
    capsys.readouterr()

    monkeypatch.delenv(MASTER_KEY_ENV)
    assert run("list") == 0
    assert "[secrets: api_key]" in capsys.readouterr().out
    assert run("show", "provider", "llm", "claude") == 0
    shown = capsys.readouterr().out
    assert MASK in shown
    assert SECRET not in shown

    assert run("set-secret", "provider", "llm", "claude", "api_key", stdin=SECRET) == 1
    assert MASTER_KEY_ENV in capsys.readouterr().err

    # And the unreadable secret can be removed and the entity replaced.
    assert run("clear-secret", "provider", "llm", "claude", "api_key") == 0
    replacement = "type: anthropic\nmodel: n\n"
    assert run("set", "provider", "llm", "claude", "-f", "-", stdin=replacement) == 0


def test_an_unusable_key_names_its_position_and_not_its_material(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("set", "provider", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")
    capsys.readouterr()
    monkeypatch.setenv(MASTER_KEY_ENV, "not-a-fernet-key")

    assert run("set-secret", "provider", "llm", "claude", "api_key", stdin=SECRET) == 1

    captured = capsys.readouterr()
    assert MASTER_KEY_ENV in captured.err
    assert "not-a-fernet-key" not in captured.err
    assert SECRET not in captured.err
    assert "Traceback" not in captured.err


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


def test_a_config_file_that_is_not_there_is_an_error_not_a_default(
    run, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("--config", str(tmp_path / "nowhere.yaml"), "list") == 1
    assert "config file not found" in capsys.readouterr().err


# Where the CLI sends a command, and what it will not send it over


def test_the_default_target_is_this_machine_on_the_configured_port(
    run, tmp_path: Path
) -> None:
    """The same file the server reads, through the same machinery, so a
    deployment names its port once and the CLI cannot disagree with the
    server about where the server is."""
    config = tmp_path / "config.yaml"
    config.write_text("server:\n  port: 9123\n", encoding="utf-8")

    assert run("--config", str(config), "list") == 0

    assert run.reached == [f"http://127.0.0.1:9123{MOUNT_PATH}"]


def test_the_environment_names_the_target_and_the_flag_beats_it(
    run, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(cli.API_URL_ENV, "http://127.0.0.1:9001/api")
    assert run("list") == 0

    assert run("--api-url", "http://localhost:9002/api", "list") == 0

    assert run.reached == ["http://127.0.0.1:9001/api", "http://localhost:9002/api"]


def test_a_plain_connection_to_another_host_is_refused(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The bearer token rides on every request and grants everything the
    API can do, so loopback-or-TLS is the rule for the whole client
    rather than a set-secret footnote, and there is deliberately no flag
    to override it.

    The refusal says "loopback address", which is what the check
    actually tests: the documentation says the same words, and the two
    describing the same rule differently is how a reader concludes that
    the machine's own network address would have been allowed."""
    assert run("--api-url", "http://config.example.invalid/api", "list") == 1

    captured = capsys.readouterr()
    assert "no flag to override" in captured.err
    assert "loopback" in captured.err
    assert "https://" in captured.err
    assert run.reached == []


def test_tls_to_another_host_is_permitted(run) -> None:
    assert run("--api-url", "https://config.example.invalid/api", "list") == 0

    assert run.reached == ["https://config.example.invalid/api"]


def test_the_mount_prefix_in_the_url_reaches_the_mounted_namespace(run) -> None:
    """The deployed shape: the API is mounted on the server's own port,
    so a base URL naming that prefix is what a request has to be joined
    onto."""
    assert run("--api-url", f"http://127.0.0.1:8003{MOUNT_PATH}", "list") == 0


# The URLs the parser itself refuses, each carrying the sentinel where
# the parser would have put it into its own ValueError.
UNREADABLE_URLS = [
    ("a port that is not a number", f"http://localhost:{SECRET}/api"),
    ("a port that is empty of digits", "http://localhost:notaport/api"),
    ("an unclosed IPv6 literal", f"http://[::1{SECRET}/api"),
    ("a malformed IPv6 literal", f"http://[bad::{SECRET}::x]:8003/api"),
]

# The URLs that parse and are then refused on their merits. These do
# name the address, minus any userinfo, because an operator who typed
# the wrong scheme needs to see which address was read that way.
UNUSABLE_URLS = [
    ("a scheme that is not http", "ftp://host/api"),
    ("no host at all", "http:///api"),
]


@pytest.mark.parametrize(("what", "url"), UNREADABLE_URLS)
def test_a_url_that_cannot_be_read_is_refused_inside_the_boundary(
    run, capsys: pytest.CaptureFixture[str], what: str, url: str
) -> None:
    """`urlsplit` raises on a malformed IPv6 literal and `.port` raises
    on a port that is not a number, and both carry the text they refused.
    Outside a handler that is a traceback out of main() with the address
    in it, which is the address somebody was typing a token near."""
    assert run("--api-url", url, "list") == 1, what

    captured = capsys.readouterr()
    assert "Traceback" not in captured.err, what
    assert SECRET not in captured.err, what
    assert captured.out == "", what
    assert run.reached == []


@pytest.mark.parametrize(("what", "url"), UNUSABLE_URLS)
def test_a_url_that_is_read_and_refused_names_the_address(
    run, capsys: pytest.CaptureFixture[str], what: str, url: str
) -> None:
    assert run("--api-url", url, "list") == 1, what

    captured = capsys.readouterr()
    assert "http://" in captured.err or "ftp://" in captured.err, what
    assert "Traceback" not in captured.err, what
    assert run.reached == []


def test_a_url_refusal_carries_no_parser_exception(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The chain, not just the message: a ValueError from the URL parser
    holds the text it refused, and anything that walks a chain reads
    what it holds."""
    with pytest.raises(ConfigError) as caught:
        cli._permitted(f"http://localhost:{SECRET}/api", "--api-url")

    assert SECRET not in _chain(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_a_url_carrying_a_credential_is_refused_without_repeating_it(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """A token does not belong in a URL, and the refusal for putting one
    there must not be what publishes it."""
    assert run("--api-url", f"https://user:{SECRET}@config.example.invalid/api", "list") == 1

    captured = capsys.readouterr()
    assert "username or a password" in captured.err
    assert SECRET not in captured.err
    assert "user" not in captured.err.split("https://")[-1].split("/")[0]
    assert run.reached == []


def test_a_missing_token_is_named_before_any_request_is_sent(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(API_SECRET_ENV)

    assert run("list") == 1

    captured = capsys.readouterr()
    assert API_SECRET_ENV in captured.err
    assert "--local" in captured.err
    assert run.reached == []


def test_the_token_comes_from_the_variable_the_config_file_names(
    run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Which on a deployment is the variable the server itself was
    started with, so exec into the running container and the CLI has the
    token for free."""
    config = tmp_path / "config.yaml"
    config.write_text("server:\n  api:\n    secret_env: SAMTAL_OTHER_TOKEN\n", encoding="utf-8")
    monkeypatch.delenv(API_SECRET_ENV)
    monkeypatch.setenv("SAMTAL_OTHER_TOKEN", TOKEN)

    assert run("--config", str(config), "list") == 0


def test_the_wrong_token_is_refused_by_the_server_the_way_any_failure_is(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The other half of resolving a token: sending one the server does
    not hold. It is a real 401 from the real gate, and it reaches the
    operator through the same contract every other refusal does, the
    detail on stderr and exit 1."""
    monkeypatch.setenv(API_SECRET_ENV, "not-the-token-this-server-was-given")

    assert run("list") == 1

    captured = capsys.readouterr()
    assert "Authorization" in captured.err
    assert "bearer token" in captured.err
    assert captured.out == ""
    # It was sent, which is what distinguishes this from a token that
    # could not be resolved at all.
    assert run.reached


def test_a_server_that_cannot_be_reached_says_so_and_names_the_recovery_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The real client, against a port nothing is listening on: the one
    case the injected test client cannot show."""
    monkeypatch.delenv("SAMTAL_CONFIG", raising=False)
    monkeypatch.setenv("SAMTAL_SERVER__DATABASE__DIR", str(tmp_path / "db"))
    monkeypatch.setenv(API_SECRET_ENV, TOKEN)

    assert cli.main(["--api-url", "http://127.0.0.1:1", "list"]) == 1

    captured = capsys.readouterr()
    assert "cannot reach the configuration API at http://127.0.0.1:1" in captured.err
    assert "--local" in captured.err
    assert "Traceback" not in captured.err


def test_a_body_that_is_not_this_api_s_own_is_not_relayed(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """What a proxy or a gateway returns is not this API's sanitized
    output, so it is reported as a status code and never printed."""
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse

    gateway = FastAPI()

    @gateway.get("/{path:path}")
    def refuse(path: str) -> HTMLResponse:
        return HTMLResponse(f"<html>502 {SECRET}</html>", status_code=502)

    monkeypatch.setattr(
        cli, "build_client", lambda base_url, token: TestClient(gateway, base_url=base_url)
    )

    assert run("list") == 1

    captured = capsys.readouterr()
    assert "answered 502" in captured.err
    assert SECRET not in captured.err
    assert "<html>" not in captured.err


def test_the_read_timeout_outlasts_the_database_s_busy_timeout() -> None:
    """The constant this depends on, asserted against the constant it has
    to outlast.

    The contention tests below shorten the busy timeout so they finish
    inside a test run, which means they would keep passing if the read
    timeout were put back to httpx's five second default: the very
    regression the explicit timeout exists to prevent. So the relationship
    is checked directly, at the production values, where nothing has been
    shortened."""
    busy_timeout_s = db_module.BUSY_TIMEOUT_MS / 1000

    assert cli.READ_TIMEOUT_S > busy_timeout_s
    # Margin, not just order: a read timeout a hair above the busy
    # timeout would still turn a slow answer into a transport error.
    assert cli.READ_TIMEOUT_S >= busy_timeout_s * 2
    # And the connect timeout is bounded, which is the other half: a
    # server that is not there must not take the read timeout to say so.
    assert cli.CONNECT_TIMEOUT_S < busy_timeout_s


def test_the_client_is_built_with_those_timeouts() -> None:
    """The constants are only worth asserting if the client is built
    from them."""
    client = cli.build_client("http://127.0.0.1:8003/api", TOKEN)
    try:
        assert client.timeout.read == cli.READ_TIMEOUT_S
        assert client.timeout.connect == cli.CONNECT_TIMEOUT_S
    finally:
        client.close()


def test_a_write_that_cannot_take_the_lock_prints_the_retryable_refusal(
    run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The reason the client's read timeout has margin above the
    database's busy timeout: the settled answer to contention is a
    sentence the operator can act on, and a client-side timeout at five
    seconds would replace it with one that says nothing.

    Both sides are taken under the same held lock, so what is asserted is
    that the CLI printed what the API answered, whatever that turns out
    to be: the API opens the database per request, so a held lock is met
    by the open-and-migrate step and the sentence is that one rather than
    the repository's own."""
    run("set", "agent", "sam", "-f", "-", stdin="prompt: You are Sam.\n")
    capsys.readouterr()
    monkeypatch.setattr(db_module, "BUSY_TIMEOUT_MS", SHORT_BUSY_MS)
    directory = tmp_path / "db"
    holder = sqlite3.connect(directory / DATABASE_FILENAME, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        over_http = TestClient(
            build_api(TOKEN, directory), headers={"Authorization": f"Bearer {TOKEN}"}
        ).put("/agents/sam", json={"prompt": "Still Sam."})
        assert run("set", "agent", "sam", "-f", "-", stdin="prompt: Still Sam.\n") == 1
    finally:
        holder.close()

    assert over_http.status_code == 409
    captured = capsys.readouterr()
    assert captured.err.rstrip("\n") == over_http.json()["detail"]
    assert captured.out == ""
    # And with the lock let go, the same command is answered.
    assert run("set", "agent", "sam", "-f", "-", stdin="prompt: Still Sam.\n") == 0


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
