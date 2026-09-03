"""What the CLI prints from an answer, and what it refuses to print.

Five commands ask the running server rather than the database: `device
pending list` lists the boards waiting to be claimed, `status` says what
each MCP server is doing, `reload` applies a changed registry and says
what it did, `agent preview` assembles an agent's prompt block by block,
and `diff` says what the store holds that the server is not serving.
None of them writes anything, and all five are rendered rather than
relayed, so this is where the rendering rules live.

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

import contextlib
import io
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import pytest

from tests.support.config_cli import SECRET, runner
from tests.support.config_cli import chain as _chain
from tests.support.config_cli import showing as _showing
from tests.support.notices import RELOAD, boundaries
from vinga_server.config import Config, cli, printing
from vinga_server.config.cli import (
    DIFF_SECTIONS,
    RELOAD_SECTIONS,
    flags,
    named_lists,
    nested,
    outcomes,
)
from vinga_server.config.entities import APPLY_NOTICE as APPLY_NOTICE_TEXT
from vinga_server.config.loader import ConfigError, ReloadInProgressError
from vinga_server.config.responses import (
    AgentsReload,
    ConfigReloadResult,
    FillersReload,
    McpReloadResult,
    PromptsReload,
    ProvidersReload,
)
from vinga_server.tools.mcp import McpServers


@pytest.fixture
def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """One command run the way the entry point runs it, against a server
    of this test's own."""
    return runner(monkeypatch)


def test_pending_lists_nothing_when_nothing_is_waiting(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("device", "pending", "list") == 0

    assert capsys.readouterr().out.startswith("no device is waiting to be claimed")


def test_pending_lists_the_code_each_device_is_showing(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """What an operator reads while holding a board: the digits to type,
    the MAC they will bind, and what tells two boards apart."""
    first = _showing(run)
    second = _showing(run, "11:22:33:44:55:66")

    assert run("device", "pending", "list") == 0

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
    assert run("mcp-server", "status") == 0

    assert capsys.readouterr().out.startswith("this server has no MCP servers configured")


def test_status_shows_each_entry_its_state_and_who_may_reach_it(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    entry = {"transport": "streamable_http", "url": "http://127.0.0.1:9/mcp"}
    run.runtime["mcp_servers"] = _configured(
        {"weather": entry, "shelved": entry}, {"sam": ["weather"]}
    )

    assert run("mcp-server", "status") == 0

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

    assert run("mcp-server", "status") == 0

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

    assert run("mcp-server", "status") == 0

    assert "  agents: sam (forecast, wind)" in capsys.readouterr().out


def test_status_refuses_an_answer_it_cannot_read(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A body without the fields a status entry carries did not come
    from this API, and a proxy's page is not rendered as though it
    had."""
    monkeypatch.setattr(cli, "_call", lambda *_args, **_kwargs: {"weather": {"up": True}})

    assert run("mcp-server", "status") == 1

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

    assert run("mcp-server", "status") == 1

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

    assert run("mcp-server", "status") == 0

    assert "weather: connected since 2026-08-13T09:12" in capsys.readouterr().out


def test_a_status_refusal_carries_nothing_of_the_body() -> None:
    """The sentence and nothing behind it. A refusal raised while an
    exception was being handled would keep that one as its context, and
    anything walking the chain would find the body on it."""
    body = {"weather": _status_entry(since={"leak": ANSWERED})}

    # Read through the act rather than through the command, for this
    # refusal test: what is under test is the exception's CHAIN, and a
    # chain is not printed. The command prints one sanitized line, which
    # the runner-driven tests beside this one assert; a __cause__ or a
    # __context__ still holding the library's own exception is reachable
    # only from where the refusal is raised, and anything that renders a
    # traceback would find it there. The act is where the shape lives,
    # so it is also where the refusal comes from.
    with pytest.raises(ConfigError) as caught:
        cli.STATUS.read(body)

    assert ANSWERED not in _chain(caught.value)


# The assembled prompt
#
# The renderer is the point of these: `agent preview` is an inspection
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

    assert run("agent", "preview", "poet") == 0

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
    `printing.printable`: a realistic prompt is far longer than
    GLIMPSE_LENGTH, and a concealed tail is exactly what an operator
    came to see."""
    tail = "END-OF-THE-PROMPT"
    long_block = "x" * (printing.GLIMPSE_LENGTH * 3) + tail
    body = _assembled(
        _prompt_block(text=long_block, characters=len(long_block)),
        characters=len(long_block),
    )
    monkeypatch.setattr(cli, "_call", lambda *_args, **_kwargs: body)

    assert run("agent", "preview", "poet") == 0

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

    assert run("agent", "preview", "poet") == 0

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

    assert run("agent", "preview", "poet") == 0

    printed = capsys.readouterr().out
    assert "server_prompt:home:1 (8 characters), the server prompt named house?[31m_style" in (
        printed
    )
    assert "\x1b" not in printed


def test_prompt_names_nothing_beside_a_block_that_has_no_name(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "_call", lambda *_args, **_kwargs: _assembled())

    assert run("agent", "preview", "poet") == 0

    assert "persona (4 characters)\n" in capsys.readouterr().out


def test_prompt_says_what_the_server_answered_for_an_unserved_agent(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """Printed verbatim from what the server answered, which sends an
    operator to the reload that installs an agent."""
    run.runtime["agent_prompt"] = _previewing(_assembled())

    assert run("agent", "preview", "stranger") == 1

    assert "config reload" in capsys.readouterr().err


def test_prompt_without_a_server_says_so(run, capsys: pytest.CaptureFixture[str]) -> None:
    assert run("agent", "preview", "poet") == 1

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

    assert run("agent", "preview", "poet") == 1

    captured = capsys.readouterr()
    assert cli.UNRECOGNIZED_ANSWER in captured.err
    assert ANSWERED not in captured.err + captured.out
    assert "Traceback" not in captured.err


def test_a_prompt_refusal_carries_nothing_of_the_body() -> None:
    # Read through the act, for the reason the status refusal above gives.
    with pytest.raises(ConfigError) as caught:
        cli.PROMPT.read({"blocks": [{"leak": ANSWERED}], "characters": 4})

    assert ANSWERED not in _chain(caught.value)


# Applying them, which is the other half of the same surface
#
# The registry's own diff is exercised against real servers elsewhere;
# what these are about is the command: that it reaches the right route,
# renders both halves of the answer, waits long enough for one, and
# refuses a body that is not one.


def _applied(
    servers: McpServers,
    prompts: Sequence[str] = (),
    fillers: FillersReload | None = None,
    providers: ProvidersReload | None = None,
    agents: AgentsReload | None = None,
    **outcome: object,
):
    """A server that answers a reload with what it applied and what is
    running, standing in for a registry these tests deliberately do not
    start. Every half, because that is what the callable a running
    server hands the API answers with: they are composed where the
    phases are, so that nothing can happen between them."""
    lists = ("started", "restarted", "stopped", "unchanged")

    async def reload() -> ConfigReloadResult:
        return ConfigReloadResult(
            mcp=McpReloadResult(
                **{name: list(outcome.get(name, ())) for name in lists},
                servers=servers.typed_status(),
            ),
            prompts=PromptsReload(changed=list(prompts)),
            fillers=fillers
            if fillers is not None
            else FillersReload(resynthesized=[], reused=[], disabled=[]),
            providers=providers
            if providers is not None
            else ProvidersReload(built=[], reused=[], retired=[]),
            agents=agents
            if agents is not None
            else AgentsReload(added=[], removed=[], defaults_changed=False),
        )

    return reload


def test_reload_prints_what_it_did_and_what_is_running(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    entry = {"transport": "streamable_http", "url": "http://127.0.0.1:9/mcp"}
    servers = _configured({"weather": entry}, {"sam": ["weather"]})
    run.runtime["mcp_servers"] = servers
    run.runtime["reload"] = _applied(
        servers,
        prompts=("sam",),
        fillers=FillersReload(resynthesized=["sam"], reused=["kid"], disabled=["mute"]),
        providers=ProvidersReload(
            built=["tts.voice"], reused=["llm.mock"], retired=["asr.old"]
        ),
        agents=AgentsReload(added=["kid"], removed=["mute"], defaults_changed=True),
        started=("weather",),
        stopped=("gone",),
    )

    assert run("reload") == 0

    printed = capsys.readouterr().out
    assert "mcp:" in printed
    assert "  started: weather" in printed
    assert "  restarted: (none)" in printed
    assert "  stopped: gone" in printed
    assert "  unchanged: (none)" in printed
    # The prompt half beside the MCP one, since an apply moves both.
    assert "prompts:" in printed
    assert "  changed: sam" in printed
    # And the filler half, all three outcomes, the degraded one
    # included: an agent whose voice would not speak is what an operator
    # most needs to read off this, since the reload applied anyway.
    assert "fillers:" in printed
    assert "  resynthesized: sam" in printed
    assert "  reused: kid" in printed
    assert "  disabled: mute" in printed
    # And the engines, whose three outcomes an operator reads for the
    # opposite reason: what was built is what a swap of a local model
    # cost, and what was reused is what it did not.
    assert "providers:" in printed
    assert "  built: tts.voice" in printed
    assert "  reused: llm.mock" in printed
    assert "  retired: asr.old" in printed
    # And the agent set, whose two lists say what a device can reach
    # from now on and what it cannot, beside the one field of the whole
    # answer that is a flag rather than a list: there is one
    # `agent_defaults` and nothing to name.
    assert "agents:" in printed
    assert "  added: kid" in printed
    assert "  removed: mute" in printed
    assert "  defaults_changed: yes" in printed
    # And the status underneath, which is what says whether an entry
    # that started actually connected.
    assert "weather: down since " in printed


def test_a_section_answered_null_is_named_rather_than_missing() -> None:
    """The branch the published schema keeps alive. Four sections are
    declared optional, because narrowing a contract a generated client
    already holds buys nothing; this server fills all of them, so what
    could still answer null is an older one. A section silently missing
    from the output would read as a kind with nothing to report, so it
    is named instead.
    """
    body = _reload_answer()
    body["agents"] = None

    assert f"agents: {cli.NOT_APPLIED}" in cli._reload_listing(body)


def test_the_reload_listing_renders_every_field_of_every_section() -> None:
    """The named failure to test for: a section's field that is neither
    a list of names nor a flag would drop silently out of the rendering
    above, and an operator would be reading an answer with a hole in it.

    Read off the models rather than listed here, so a field added to a
    section is either rendered or fails this."""
    for section, shape in RELOAD_SECTIONS.items():
        rendered = set(outcomes(shape)) | set(flags(shape))
        # The MCP status document is the one field rendered by a
        # listing of its own rather than by the rules above, which is
        # what makes it the one exception this pin states.
        unrendered = set(shape.model_fields) - rendered
        assert unrendered == ({"servers"} if section == "mcp" else set())


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
    """One reload answer as the API returns it, with one field of its
    MCP section replaced by whatever a test wants to see refused."""
    mcp = (
        dict.fromkeys(outcomes(McpReloadResult), [])
        | {"servers": {"weather": _status_entry()}}
        | overrides
    )
    return {
        "mcp": mcp,
        "prompts": {"changed": []},
        "fillers": None,
        "providers": None,
        "agents": None,
    }


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(_reload_answer(started=[{"leak": ANSWERED}]), id="outcome-object"),
        pytest.param(_reload_answer(unchanged=ANSWERED), id="outcome-not-a-list"),
        pytest.param(
            {
                "mcp": dict.fromkeys(outcomes(McpReloadResult), []),
                "prompts": {"changed": []},
                "fillers": None,
                "providers": None,
                "agents": None,
            },
            id="servers-missing",
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


# The comparison
#
# Names and closed tokens by construction: no bodies, no values, no
# masks and no secret marks cross this surface, which is what makes its
# no-leak claim structural. What is left to check is that every kind is
# printed and that every field of every kind is printed, because a kind
# or a field that dropped out would read as one with nothing pending.

DIFF_EMPTY: dict[str, object] = {
    "providers": {"applies": "reload", "added": [], "removed": [], "changed": []},
    "mcp_servers": {"applies": "reload", "added": [], "removed": [], "changed": []},
    "prompt_fragments": {"applies": "reload", "added": [], "removed": [], "changed": []},
    "agent_defaults": {"applies": "reload", "changed": False},
    "agents": {
        "applies": "reload",
        "added": [],
        "removed": [],
        "changed": [],
        "grants": {"applies": "reload", "changed": []},
        "prompt": {"applies": "reload", "changed": []},
        "filler": {"applies": "reload", "changed": []},
    },
    "devices": {"applies": "check-in"},
    "default_agent": {"applies": "check-in"},
}


def test_the_comparison_prints_every_kind_and_its_boundary() -> None:
    """Every kind, including the ones with nothing to name.

    A kind silently missing from the output would read as a kind with
    nothing pending rather than as one this read reports without lists,
    and which of those it is is exactly what the label says.
    """
    rendered = cli._diff_listing(DIFF_EMPTY)

    for kind in DIFF_SECTIONS:
        assert f"{kind}: applies at " in rendered
    assert "devices: applies at check-in" in rendered
    assert "providers: applies at reload" in rendered


def test_the_comparison_names_what_moved_and_where_it_reaches() -> None:
    """The three clocks an agent's entry has, each printed under the
    agent kind that holds them."""
    body = {**DIFF_EMPTY}
    body["agents"] = {
        **DIFF_EMPTY["agents"],  # type: ignore[dict-item]
        "changed": ["sam"],
        "prompt": {"applies": "reload", "changed": ["sam"]},
    }

    rendered = cli._diff_listing(body)

    assert "  changed: sam" in rendered
    assert "  prompt: applies at reload" in rendered
    assert "    changed: sam" in rendered


def test_the_comparison_renders_every_field_of_every_kind() -> None:
    """The named failure to test for: a field that is neither a list of
    names, nor a flag, nor a kind's own sub-section would drop silently
    out of the rendering, and an operator would be reading an answer
    with a hole in it.

    Read off the models rather than listed here, so a field added to the
    comparison is either rendered or fails this. The walk follows the
    nested sections too, since that is where three of the fields are.
    """
    unwalked = list(DIFF_SECTIONS.values())
    while unwalked:
        shape = unwalked.pop()
        rendered = set(named_lists(shape)) | set(flags(shape)) | set(nested(shape))
        unwalked += [
            cli._section(shape.model_fields[name].annotation) for name in nested(shape)
        ]
        # `applies` is the label on the block's own heading rather than
        # a line under it, which is the one field the three rules do not
        # claim and this pin states.
        assert set(shape.model_fields) - rendered == {"applies"}


def test_the_comparison_refuses_an_answer_it_cannot_read(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A body missing a kind is a body this client cannot read as a
    comparison, and it meets the fixed sentence rather than rendering
    most of an answer."""
    monkeypatch.setattr(cli, "_call", lambda *_args, **_kwargs: {"providers": {}})

    assert run("diff") == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == cli.UNREADABLE_READ + "\n"


def test_diff_prints_the_refusal_the_api_answered(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """No server to compare against is a refusal with a sentence rather
    than an empty comparison, which would say that everything stored is
    already in effect."""
    assert run("diff") == 1

    assert "no running server" in capsys.readouterr().err


# What a body may put where a closed token belongs
#
# Nothing bounds it. `applies` is declared as one of three words and a
# body is free to answer a list, an object, a number or a word this
# client has never heard of, and each of those used to reach the same
# line: an unhashable value used as a dictionary key raises a
# `TypeError` out of the boundary that catches validation errors, which
# is a traceback holding the value that caused it.
#
# The case that removes a field cannot catch this, which is why these
# are separate: a body missing a kind never reaches the token at all.

DIFF_TOKENS = [
    ("a list", []),
    ("an object", {}),
    ("a number", 4),
    ("nothing at all", None),
    ("a word this client does not know", "whenever"),
    ("a credential pasted where a token belongs", SECRET),
]


@pytest.mark.parametrize(
    ("answered"), [answered for _, answered in DIFF_TOKENS], ids=[what for what, _ in DIFF_TOKENS]
)
def test_a_token_the_comparison_cannot_read_is_a_sentence(
    run,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    answered: object,
) -> None:
    """One fixed sentence and exit 1, whatever the shape, and neither
    the value nor a traceback on either stream."""
    body = {**DIFF_EMPTY}
    body["providers"] = {**DIFF_EMPTY["providers"], "applies": answered}  # type: ignore[dict-item]
    monkeypatch.setattr(cli, "_call", lambda *_args, **_kwargs: body)

    assert run("diff") == 1

    captured = capsys.readouterr()
    assert captured.err == cli.UNREADABLE_READ + "\n"
    assert captured.out == ""
    assert "Traceback" not in captured.err
    assert SECRET not in captured.err


@pytest.mark.parametrize(
    ("answered"), [answered for _, answered in DIFF_TOKENS], ids=[what for what, _ in DIFF_TOKENS]
)
def test_no_token_the_comparison_refuses_is_retained_on_its_chain(answered: object) -> None:
    """The half no assertion about a stream can make: what the refusal
    carries behind it. A validation error retains the input it rejected,
    and a `TypeError` from a lookup retains the key it was given."""
    body = {**DIFF_EMPTY}
    body["providers"] = {**DIFF_EMPTY["providers"], "applies": answered}  # type: ignore[dict-item]

    with pytest.raises(ConfigError) as caught:
        cli.DIFF.read(body)

    assert str(caught.value) == cli.UNREADABLE_READ
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert SECRET not in _chain(caught.value)


# Applying, and the reload behind it
#
# `apply` writes the document and then installs it (#341), which makes
# it the one row whose acts an invocation chooses between: the default
# runs both, `--no-reload` stages the write for a later reload. The
# cases belong in this file rather than beside the acceptance spine's
# other apply cases because what decides the behavior is a running
# server, and this is the file that injects one.
#
# What each of the three surfaces is: two requests in order under the
# default and one under the flag, the notices dropped under the default
# because the reload's own listing follows them and kept under the flag
# because nothing else will say it, and the sentence a committed write
# whose reload did not answer may add.

# The MCP entry every case here configures, which is referenced by one
# agent and connected by nobody: what these are about is the reload's
# answer rather than a live connection.
ENTRY = {"transport": "streamable_http", "url": "http://127.0.0.1:9/mcp"}

WRITTEN = """\
providers:
  llm:
    brain:
      type: mock
      reply: Hello.
"""

# What the entry above is called in an applied document's answer, which
# is the section and the identity under it.
WROTE = "providers.llm.brain: wrote"

HELD = "a reload of this server's configuration is already running."


def _held():
    """A running server whose reload is refused because another one has
    it, which is the 409 an operator most plausibly meets behind a
    committed write: two people administering one deployment."""

    async def reload() -> ConfigReloadResult:
        raise ReloadInProgressError(HELD)

    return reload


def test_apply_installs_what_it_wrote(run, capsys: pytest.CaptureFixture[str]) -> None:
    """The default, which is the verb doing what its name promises.

    Two requests in one command, and the order is readable in the
    output: what was written, and then what the reload made of it.
    """
    servers = _configured({"weather": ENTRY}, {"sam": ["weather"]})
    run.runtime["mcp_servers"] = servers
    run.runtime["reload"] = _applied(
        servers, providers=ProvidersReload(built=["llm.brain"], reused=[], retired=[])
    )

    assert run("apply", "-f", "-", stdin=WRITTEN) == 0

    printed = capsys.readouterr()
    lines = printed.out.splitlines()
    assert lines[0] == WROTE
    assert "  built: llm.brain" in lines
    # Two clients, because each request builds one and closes it: the
    # write and the reload are two requests rather than a flag on one.
    assert len(run.clients) == 2
    # And nothing on stderr. The boundary the notice names is the
    # reload, and the reload is the answer above it: printing the
    # sentence here would tell an operator to run the command whose
    # answer they are reading.
    assert printed.err == ""


def test_no_reload_stages_the_write_and_says_what_it_is_waiting_on(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The staging spelling, which is the rendering that keeps the
    notices: nothing in this command installs the write, so the
    boundary it is waiting at is the operator's to know.

    One request, which is the other half of the claim: the flag turns
    the second act off rather than quietening it.
    """
    assert run("apply", "--no-reload", "-f", "-", stdin=WRITTEN) == 0

    printed = capsys.readouterr()
    assert printed.out.splitlines() == [WROTE]
    assert boundaries(printed.err) == {RELOAD}
    assert len(run.clients) == 1


def test_a_write_whose_reload_is_held_claims_only_what_it_knows(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The first of the two failures behind a committed write.

    A 409 says another reload is already running, and that one re-read
    the store either side of this commit, so whether the document is
    live is not knowable from here. What the command may say is what it
    saw: the write was acknowledged, no reload answered, and here are
    the two commands that settle it.
    """
    run.runtime["mcp_servers"] = _configured({"weather": ENTRY}, {"sam": ["weather"]})
    run.runtime["reload"] = _held()

    assert run("apply", "-f", "-", stdin=WRITTEN) == 1

    printed = capsys.readouterr()
    # The write's own answer is printed first, because it happened.
    assert printed.out.splitlines() == [WROTE]
    # Then the server's refusal, and then what this client knows.
    assert printed.err == f"{HELD}\n{cli.APPLY_UNANSWERED}\n"
    assert "Traceback" not in printed.err
    # And the write really did commit, which is the claim the sentence
    # makes and the one thing here that is checkable.
    capsys.readouterr()
    assert run("provider", "show", "llm", "brain") == 0
    assert "Hello." in capsys.readouterr().out


APPLIED_NOTHING = [
    ("a document that names nothing", "{}\n", []),
    ("a document the store already says", WRITTEN, [WROTE.replace("wrote", "unchanged")]),
]


@pytest.mark.parametrize(
    ("document", "printed_lines"),
    [(document, lines) for _, document, lines in APPLIED_NOTHING],
    ids=[what for what, _, _ in APPLIED_NOTHING],
)
def test_an_apply_that_wrote_nothing_says_nothing_about_a_write(
    run,
    capsys: pytest.CaptureFixture[str],
    document: str,
    printed_lines: list[str],
) -> None:
    """The sentence has to be true of every apply that was answered, and
    two of the three wrote nothing at all.

    A document every entry of which the store already holds writes no
    row, and a document naming no section has nothing to write in the
    first place. A sentence opening "The document was written" would be
    false in both, which is the one thing a sentence added to a refusal
    may never be: it is the half the operator has no other way to check.
    """
    # The all-unchanged case needs the row there first, staged, so that
    # what the second run meets is a store that already says it.
    if printed_lines:
        assert run("apply", "--no-reload", "-f", "-", stdin=document) == 0
        capsys.readouterr()
    run.runtime["mcp_servers"] = _configured({"weather": ENTRY}, {"sam": ["weather"]})
    run.runtime["reload"] = _held()

    assert run("apply", "-f", "-", stdin=document) == 1

    printed = capsys.readouterr()
    if printed_lines:
        assert printed.out.splitlines() == printed_lines
    else:
        assert printed.out.startswith(cli.NOTHING_APPLIED)
    assert printed.err == f"{HELD}\n{cli.APPLY_UNANSWERED}\n"
    # The claim itself, held to what happened: nothing was written, and
    # the sentence says nothing that says otherwise.
    assert "was written" not in printed.err


def test_an_empty_apply_is_read_before_the_failure_that_followed_it(run) -> None:
    """The ordering rule, on the one arm that used to leave without it.

    Two streams reach one terminal, and only one of them is buffered, so
    what an operator reads in is flush order rather than write order. A
    document that named nothing prints one line and used to return
    before the flush, so the refusal from the reload behind it, written
    to an unbuffered stderr, arrived above the output it came after: the
    apply would read as having failed rather than as having applied
    nothing.

    Asserted over one shared buffer with two wrappers on it, a buffered
    one for stdout and an unbuffered one for stderr, because ordering
    between two independently captured streams is not a thing a test can
    see: pytest's capture keeps them apart, which is exactly what hid
    this.
    """
    shared = io.BytesIO()
    buffered = io.TextIOWrapper(shared, encoding="utf-8", write_through=False)
    unbuffered = io.TextIOWrapper(shared, encoding="utf-8", write_through=True)
    run.runtime["mcp_servers"] = _configured({"weather": ENTRY}, {"sam": ["weather"]})
    run.runtime["reload"] = _held()

    with contextlib.redirect_stdout(buffered), contextlib.redirect_stderr(unbuffered):
        assert run("apply", "-f", "-", stdin="{}\n") == 1
        buffered.flush()
        written = shared.getvalue().decode("utf-8")

    assert cli.NOTHING_APPLIED in written
    assert written.index(cli.NOTHING_APPLIED) < written.index(HELD)


def test_a_refused_document_never_reaches_the_reload(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The other order the two acts can end in. A document that will not
    resolve changed nothing, so there is nothing to install and no
    second request to make, and the refusal is the whole answer."""
    run.runtime["mcp_servers"] = _configured({"weather": ENTRY}, {"sam": ["weather"]})
    run.runtime["reload"] = _held()

    assert run("apply", "-f", "-", stdin="agents:\n  sam: {prompt: p, llm: ghost}\n") == 1

    printed = capsys.readouterr()
    assert printed.out == ""
    assert cli.APPLY_UNANSWERED not in printed.err
    assert len(run.clients) == 1


def test_what_a_second_act_adds_is_raised_with_nothing_behind_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The half no assertion about a stream can make.

    The sentence is composed while a refusal is being handled, and an
    exception raised inside a handler carries the one it was handling on
    `__context__`, where a chain walker finds whatever THAT one was
    holding. Which is why the refusal below carries a cause of its own:
    a transport failure behind a request is the shape that does it, and
    an httpx exception holds the URL it was given.
    """
    answered: list[object] = []

    def call(*_args: object, **_kwargs: object) -> object:
        answered.append(_args)
        if len(answered) == 1:
            return {"entries": []}
        refused = ConfigError("the request did not complete")
        refused.__cause__ = RuntimeError(SECRET)
        raise refused

    monkeypatch.setattr(cli, "_call", call)
    first = cli.Act(
        method="POST",
        path=lambda _args: "/apply",
        answers=dict[str, object],
        render=lambda _answer: None,
    )
    reached = cli.Reached(address=cli.Address(base="", query="", shown=""), token="")

    with pytest.raises(ConfigError) as caught:
        cli._performed(
            cli.Invocation(),
            (first, replace(first, unanswered=cli.APPLY_UNANSWERED)),
            reached,
        )

    assert str(caught.value).endswith(cli.APPLY_UNANSWERED)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert SECRET not in _chain(caught.value)


# What an applied answer may put where text belongs
#
# The three strings an applied entry carries reach stdout and stderr as
# themselves: the section and the identity are composed into a line, and
# the notice is the sentence under it. A section is a closed token and
# the outcome is one too, but an identity is a name as the store holds
# it and a notice is a sentence the server composed, so what a hostile
# or broken far side can put in either is whatever it likes.
#
# Two shapes reach a stream and one never does. An escape sequence
# steers a terminal, and a lone surrogate raises out of `print` itself,
# past the boundary that turns a failure into a sentence, which no
# assertion about a stream would have caught. A credential pasted into
# an identity is the third and is deliberately NOT one of them: an
# identity is the name of a row the operator asked about, nothing
# distinguishes a pasted value from a name, and a rendering that hid it
# would hide what the store holds. Where a credential must not reach is
# a refusal and the chain under it, which is what the cases further
# down are about.

# An escape sequence that clears the screen and moves the cursor, which
# is what "an answer cannot steer a terminal" is about.
STEERING = "\x1b[2J\x1b[H"

# The one character a str may hold that stdout cannot encode.
SURROGATE = "\ud800"


def _entry(**overrides: object) -> dict[str, object]:
    """One applied entry as the API answers one, with whatever a case
    wants to see refused or neutralized in it."""
    return {
        "section": "agents",
        "identity": "sam",
        "outcome": "wrote",
        "notice": APPLY_NOTICE_TEXT,
    } | overrides


APPLIED_HOSTILE = [
    ("an escape sequence in an identity", {"identity": f"sam{STEERING}"}),
    ("an escape sequence in a notice", {"notice": f"wait{STEERING}"}),
    ("a lone surrogate in an identity", {"identity": f"sam{SURROGATE}"}),
    ("a lone surrogate in a notice", {"notice": f"wait{SURROGATE}"}),
]


@pytest.mark.parametrize(
    "overrides",
    [overrides for _, overrides in APPLIED_HOSTILE],
    ids=[what for what, _ in APPLIED_HOSTILE],
)
@pytest.mark.parametrize(
    "render",
    [cli._applied, cli._applied_quietly],
    ids=["staging", "quiet"],
)
def test_neither_apply_rendering_lets_an_answer_steer_a_terminal(
    render, overrides: dict[str, object], capsys: pytest.CaptureFixture[str]
) -> None:
    """Both renderings, because they print the same two strings and
    differ only in whether the notice is one of them.

    Read through the act rather than handed a dictionary, so what is
    exercised is the shape this client insists on and then the renderer
    it feeds, which is the path an answer really takes.
    """
    render(cli.APPLY.read({"entries": [_entry(**overrides)]}))

    printed = capsys.readouterr()
    written = printed.out + printed.err
    assert STEERING not in written
    assert SURROGATE not in written
    # And the line is still the answer to what was asked, so what
    # happened to the character is neutralizing rather than dropping the
    # output it was in. Which of the two streams carried it is the
    # difference between the renderings and is pinned elsewhere.
    assert "agents.sam" in written


def test_a_staged_notice_arrives_neutralized_rather_than_dropped(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The other half of the rule above, on the one stream the staging
    rendering writes a far side's sentence to: what a notice loses is
    the characters that steer a terminal, and it keeps the words. A
    sentence dropped whole would be a boundary an operator was never
    told about."""
    cli._applied(cli.APPLY.read({"entries": [_entry(notice=f"wait{STEERING}")]}))

    written = capsys.readouterr().err
    assert written.startswith("wait?")
    assert STEERING not in written


def test_an_unprintable_identity_never_leaves_as_an_exception() -> None:
    """The failure a stream assertion cannot see: `print` encodes, and a
    lone surrogate raises `UnicodeEncodeError` from inside it, which
    leaves this boundary as a traceback carrying the value.

    Written to a real encoding rather than to pytest's capture, because
    what raises is the encoder and a buffer that never encodes cannot
    raise.
    """
    stream = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", errors="strict")

    with contextlib.redirect_stdout(stream):
        cli._applied_quietly(cli.APPLY.read({"entries": [_entry(identity=SURROGATE)]}))

    stream.flush()
    assert "agents" in stream.buffer.getvalue().decode("utf-8")


@pytest.mark.parametrize(
    ("outcome", "notice"),
    [
        pytest.param("unchanged", APPLY_NOTICE_TEXT, id="unchanged-with-a-boundary"),
        pytest.param("wrote", None, id="wrote-with-none"),
    ],
)
def test_an_entry_whose_outcome_and_notice_disagree_is_refused(
    outcome: str, notice: str | None
) -> None:
    """The model's own stated contract, enforced rather than described.

    An entry that changed nothing has nothing waiting to be applied, so
    a boundary sentence on one is a sentence printed by an entry with
    nothing to say; a write with no boundary is the same disagreement
    from the other side. Read through the act, so the refusal is the
    fixed one an operator meets.
    """
    body = {"entries": [_entry(outcome=outcome, notice=notice)]}

    with pytest.raises(ConfigError) as caught:
        cli.APPLY.read(body)

    assert str(caught.value) == cli.UNREADABLE_WRITE
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


@pytest.mark.parametrize(
    "section",
    [
        pytest.param(SECRET, id="a credential pasted where a section belongs"),
        pytest.param("agent", id="a word this API does not emit"),
    ],
)
def test_a_section_this_api_does_not_emit_is_refused(section: str) -> None:
    """A section is printed as itself, so it is a closed token: seven
    words and nothing else, which is what keeps a body's own text out of
    the left-hand side of a line."""
    with pytest.raises(ConfigError) as caught:
        cli.APPLY.read({"entries": [_entry(section=section)]})

    assert str(caught.value) == cli.UNREADABLE_WRITE
    assert SECRET not in _chain(caught.value)


def test_no_applied_refusal_is_retained_on_its_chain() -> None:
    """The half no assertion about a stream can make: a validation error
    retains the input it rejected, and the input here is a refused entry
    carrying a pasted credential in every field a body chooses."""
    body = {"entries": [_entry(outcome="unchanged", identity=SECRET, notice=SECRET)]}

    with pytest.raises(ConfigError) as caught:
        cli.APPLY.read(body)

    assert SECRET not in _chain(caught.value)
