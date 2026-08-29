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

from collections.abc import Sequence
from pathlib import Path

import pytest

from tests.support.config_cli import SECRET, runner
from tests.support.config_cli import chain as _chain
from tests.support.config_cli import showing as _showing
from vinga_server.config import Config, cli, printing
from vinga_server.config.cli import (
    DIFF_SECTIONS,
    RELOAD_SECTIONS,
    flags,
    named_lists,
    nested,
    outcomes,
)
from vinga_server.config.loader import ConfigError
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
