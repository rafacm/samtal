"""What the CLI prints from an answer, and what it refuses to print.

Four commands ask the running server rather than the database: `pending`
lists the boards waiting to be claimed, `status` says what each MCP
server is doing, `reload` applies a changed registry and says what it
did, and `prompt` assembles an agent's prompt block by block. None of
them writes anything, and all four are rendered rather than relayed, so
this is where the rendering rules live.

Since #139 the shape of an answer is the pydantic model `api.py`
declares it answers with, read in strict mode, unknown fields dropped
and nothing coerced. What that buys is what these tests are mostly
about: a body this client cannot recognize did not come from this API's
sanitized output, and what a proxy or a captive portal returns is text
nobody vouched for, so it meets one fixed sentence and reaches neither
stream nor the exception chain under it. Each parametrized case is the
valid body with a single field replaced by a value shaped like a pasted
credential, and the control beside it is the valid body itself, which is
what makes the refusals about the replacement.

The renderers themselves are the other half: the states an operator has
never met before, the order two names are listed in, the block a
prompt's reader came to see whole, and the escape sequence that must not
reach the terminal on any of them.
"""

from dataclasses import asdict
from pathlib import Path

import pytest

from tests.support.config_cli import chain as _chain
from tests.support.config_cli import runner
from tests.support.config_cli import showing as _showing
from vinga_server.config import Config, cli
from vinga_server.config.loader import ConfigError
from vinga_server.config.responses import McpReloadResult
from vinga_server.tools.mcp import McpReload, McpServers


@pytest.fixture
def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """One command run the way the entry point runs it, against a server
    of this test's own."""
    return runner(tmp_path, monkeypatch)


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


def _applied(servers: McpServers, **outcome: object):
    """A server that answers a reload with what it did and what is
    running, standing in for a registry these tests deliberately do not
    start. Both halves, because both are what the callable a running
    server hands the API answers with: they are composed where the two
    phases are, so that nothing can happen between them."""

    async def reload() -> McpReloadResult:
        return McpReloadResult(
            **{field: list(names) for field, names in asdict(McpReload(**outcome)).items()},
            servers=servers.typed_status(),
        )

    return reload


def test_reload_prints_what_it_did_and_what_is_running(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    entry = {"transport": "streamable_http", "url": "http://127.0.0.1:9/mcp"}
    servers = _configured({"weather": entry}, {"sam": ["weather"]})
    run.runtime["mcp_servers"] = servers
    run.runtime["mcp_reload"] = _applied(servers, started=("weather",), stopped=("gone",))

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
