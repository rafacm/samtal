"""What the CLI prints from an answer, and what it refuses to print.

Five commands ask the running server rather than the database: `device
pending list` lists the boards waiting to be claimed, `status` says what
each MCP server is doing, `apply` installs a changed registry and says
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
from typing import get_args

import pytest

from tests.support.config_cli import SECRET, runner
from tests.support.config_cli import chain as _chain
from tests.support.config_cli import showing as _showing
from vinga_server.config import Config, cli, entities, printing
from vinga_server.config.cli import (
    APPLY_SECTIONS,
    DIFF_SECTIONS,
    flags,
    named_lists,
    nested,
    outcomes,
)
from vinga_server.config.entities import APPLY_NOTICE
from vinga_server.config.loader import ConfigError, ReloadInProgressError
from vinga_server.config.responses import (
    AgentsReload,
    Applies,
    ConfigReloadResult,
    DiffApplies,
    FillersReload,
    LiveKind,
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
    operator to the apply that installs an agent."""
    run.runtime["agent_prompt"] = _previewing(_assembled())

    assert run("agent", "preview", "stranger") == 1

    assert "config apply" in capsys.readouterr().err


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
            else FillersReload(
                resynthesized=[],
                reused=[],
                disabled=[],
                fallback_resynthesized=[],
                fallback_reused=[],
                fallback_degraded=[],
            ),
            providers=providers
            if providers is not None
            else ProvidersReload(built=[], reused=[], retired=[]),
            agents=agents
            if agents is not None
            else AgentsReload(added=[], removed=[], defaults_changed=False),
        )

    return reload


def test_apply_prints_what_it_did_and_what_is_running(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    entry = {"transport": "streamable_http", "url": "http://127.0.0.1:9/mcp"}
    servers = _configured({"weather": entry}, {"sam": ["weather"]})
    run.runtime["mcp_servers"] = servers
    run.runtime["reload"] = _applied(
        servers,
        prompts=("sam",),
        fillers=FillersReload(
            resynthesized=["sam"],
            reused=["kid"],
            disabled=["mute"],
            fallback_resynthesized=["kid"],
            fallback_reused=["sam"],
            fallback_degraded=["mute"],
        ),
        providers=ProvidersReload(
            built=["tts.voice"], reused=["llm.mock"], retired=["asr.old"]
        ),
        agents=AgentsReload(added=["kid"], removed=["mute"], defaults_changed=True),
        started=("weather",),
        stopped=("gone",),
    )

    assert run("apply") == 0

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
    # And the failure phrases beside them, crossed the other way round
    # on purpose: the two kinds are staled apart, so one agent can be
    # reused under one and re-synthesized under the other.
    assert "  fallback_resynthesized: kid" in printed
    assert "  fallback_reused: sam" in printed
    assert "  fallback_degraded: mute" in printed
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

    assert f"agents: {cli.NOT_APPLIED}" in cli._apply_listing(body)


def test_the_apply_listing_renders_every_field_of_every_section() -> None:
    """The named failure to test for: a section's field that is neither
    a list of names nor a flag would drop silently out of the rendering
    above, and an operator would be reading an answer with a hole in it.

    Read off the models rather than listed here, so a field added to a
    section is either rendered or fails this."""
    for section, shape in APPLY_SECTIONS.items():
        rendered = set(outcomes(shape)) | set(flags(shape))
        # The MCP status document is the one field rendered by a
        # listing of its own rather than by the rules above, which is
        # what makes it the one exception this pin states.
        unrendered = set(shape.model_fields) - rendered
        assert unrendered == ({"servers"} if section == "mcp" else set())


def test_apply_prints_the_refusal_the_api_answered(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """No server to reload is a 503 with a sentence, and the sentence is
    what an operator reads: this client adds nothing to it."""
    assert run("apply") == 1

    assert "no running server" in capsys.readouterr().err


def test_apply_refuses_an_answer_it_cannot_read(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "_call", lambda *_args, **_kwargs: {"started": "weather"})

    assert run("apply") == 1

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
def test_apply_prints_nothing_from_an_answer_of_the_wrong_shape(
    body: object, run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The status rules apply to the reload's answer too: `_names`
    prints every element of the outcome lists, and the status half is
    the same document the status command refuses when it cannot read
    it, so a stray shape anywhere must end in the fixed sentence rather
    than in output or a traceback."""
    monkeypatch.setattr(cli, "_call", lambda *_args, **_kwargs: body)

    assert run("apply") == 1

    captured = capsys.readouterr()
    assert cli.UNRECOGNIZED_ANSWER in captured.err
    assert ANSWERED not in captured.err + captured.out
    assert "Traceback" not in captured.err


# The comparison
#
# Names and closed tokens by construction: no bodies, no values, no
# masks and no secret marks cross this surface, which is what makes its
# no-leak claim structural. What is left to check is that every field of
# every kind can be said, because a field the rendering has no rule for
# would drop out of an answer nobody could tell was short, and that what
# is printed is what has something to say: since #425 a kind with an
# empty list and a false flag is absent rather than enumerated, so the
# pins are about which lines appear rather than about all of them
# appearing.

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
        "fallback": {"applies": "reload", "changed": []},
    },
    "devices": {"applies": "check-in"},
    "default_agent": {"applies": "check-in"},
}


# The state Getting Started's step 2 leaves behind: four providers, an
# agent and the shared defaults written to the store and none of them
# installed. It is the answer #425 counted twenty-four lines of, and the
# case below is what those lines became.
DIFF_PENDING: dict[str, object] = {
    **DIFF_EMPTY,
    "providers": {
        "applies": "reload",
        "added": ["asr.whisper", "llm.local", "tts.voice", "vad.ears"],
        "removed": [],
        "changed": [],
    },
    "agent_defaults": {"applies": "reload", "changed": True},
    "agents": {**DIFF_EMPTY["agents"], "added": ["assistant"]},  # type: ignore[dict-item]
}

PENDING = """\
pending, at the next `vinga apply`:
  providers       added: asr.whisper, llm.local, tts.voice, vad.ears
  agent_defaults  changed
  agents          added: assistant

devices and default_agent are read as a device asks for them, so nothing about \
them waits for an apply.
"""


def test_the_comparison_groups_what_is_pending_under_one_head() -> None:
    """The whole answer, byte for byte.

    Three kinds waiting at one boundary, said once over the group rather
    than once per kind (#425): the head names the command an operator
    runs, each line under it says what moved about one kind, and a kind
    with nothing to say is not a line at all. The two columns are what
    makes it readable down the left, so the padding is part of the pin.
    """
    assert cli._diff_listing(DIFF_PENDING) == PENDING


def test_the_comparison_heads_a_group_once_however_many_kinds_wait() -> None:
    """The head is over the group, which is a claim about a diff with
    one change in it as much as about one with three: the boundary is
    stated once per run either way, and a second head would be the
    per-kind label under another name."""
    one = {**DIFF_EMPTY, "agent_defaults": {"applies": "reload", "changed": True}}

    rendered = cli._diff_listing(one)

    assert rendered.count(cli.HEADS[Applies.RELOAD]) == 1
    assert cli._diff_listing(DIFF_PENDING).count(cli.HEADS[Applies.RELOAD]) == 1
    assert "  agent_defaults  changed\n" in rendered


def test_the_comparison_says_so_when_nothing_is_pending() -> None:
    """A sentence rather than no output at all: a command that printed
    nothing would read as one that failed to answer, and what this
    answers is that the two worlds agree."""
    rendered = cli._diff_listing(DIFF_EMPTY)

    assert rendered.startswith(cli.SERVING_THE_STORE + "\n")
    assert cli.HEADS[Applies.RELOAD] not in rendered


def test_the_comparison_says_why_two_kinds_are_never_pending() -> None:
    """Printed on every comparison, because "why is my device not in
    this list" is a question about every comparison rather than about
    this one's state. And it names the kinds it is true of: the sentence
    is fixed text, so the pin is what holds it to the shapes."""
    live = [kind for kind, shape in DIFF_SECTIONS.items() if shape is LiveKind]

    assert live
    for kind in live:
        assert kind in cli.READ_AS_ASKED
    assert cli._diff_listing(DIFF_EMPTY).endswith(cli.READ_AS_ASKED + "\n")
    assert cli._diff_listing(DIFF_PENDING).endswith(cli.READ_AS_ASKED + "\n")


def test_every_boundary_a_comparison_can_name_has_a_head() -> None:
    """The named failure to test for: a boundary added to the alias the
    diff's fields are declared with, and not to the table, would head
    its group with a `KeyError` or with nothing.

    Both directions, keyed off the alias rather than listed here: a
    member without a head is the hole, and a head for a token no field
    can carry is a line nobody will ever read.
    """
    assert set(cli.HEADS) == set(get_args(DiffApplies))


def test_the_comparison_prints_only_what_has_something_to_say() -> None:
    """An empty list and a false flag are absent rather than enumerated
    (#425): absence is absence, and what an operator is reading for is
    what moved. What is filtered is a function of the two worlds, so
    two reads of one pair of worlds are still the same bytes, which the
    case further down pins."""
    rendered = cli._diff_listing(DIFF_PENDING)

    assert "(none)" not in rendered
    assert "removed" not in rendered
    # The kinds with nothing to say are not lines, and the ones with
    # something to say name only the facts they have.
    assert "mcp_servers" not in rendered
    assert "prompt_fragments" not in rendered
    assert "  agents          added: assistant\n" in rendered


def test_the_comparison_names_what_moved_and_where_it_reaches() -> None:
    """The four clocks an agent's entry has, flattened into labelled
    facts of the agent line that holds them rather than into four
    indented blocks of their own, which is what most of the answer #425
    counted was.

    Two facts about one kind join on one line, in the order the model
    declares them: what an operator asks is what moved about the agents,
    and the answer is one line about the agents.
    """
    body = {**DIFF_EMPTY}
    body["agents"] = {
        **DIFF_EMPTY["agents"],  # type: ignore[dict-item]
        "changed": ["sam"],
        "prompt": {"applies": "reload", "changed": ["sam"]},
        "filler": {"applies": "reload", "changed": ["kids"]},
    }

    rendered = cli._diff_listing(body)

    assert "  agents  changed: sam; prompt changed: sam; filler changed: kids\n" in rendered


@pytest.mark.parametrize(
    "named", [pytest.param("", id="empty"), pytest.param("  \t ", id="whitespace")]
)
def test_a_change_named_with_nothing_is_still_a_change(named: str) -> None:
    """The one answer this command must never give falsely: nothing is
    pending, which says an operator's writes are installed.

    `EntityDiff` declares its names as strings and says nothing about
    their length, so a list holding one that is empty or is only
    whitespace is an answer `Act.read()` accepts. `printable` strips
    before it bounds, so such a name renders to nothing, and a rendering
    that read presence off the rendered string would lose the change and
    then say none was pending.

    Read through the act, so what is exercised is the shape this client
    insists on and then the renderer it feeds.
    """
    body = {
        **DIFF_EMPTY,
        "providers": {"applies": "reload", "added": [named], "removed": [], "changed": []},
    }

    rendered = cli._diff_listing(cli.DIFF.read(body))

    assert cli.SERVING_THE_STORE not in rendered
    assert f"  providers  added: {cli.UNNAMEABLE}\n" in rendered
    # And the count survives with it: two such names are two things.
    both = {**body, "providers": {**body["providers"], "added": [named, named]}}
    assert f"added: {cli.UNNAMEABLE}, {cli.UNNAMEABLE}\n" in cli._diff_listing(
        cli.DIFF.read(both)
    )


def test_the_comparison_puts_each_kind_under_its_own_boundary() -> None:
    """Two boundaries in one answer, which no other case here has: every
    populated kind in them says `reload`, so a rendering that grouped
    only `reload`, or that ordered its groups by something of its own,
    would pass all of them.

    Read through the act, because what makes this answer legal is the
    alias the fields are declared with: `restart` is a `DiffApplies`
    member, so a kind carrying it validates and has to be rendered
    somewhere.
    """
    body = {
        **DIFF_EMPTY,
        "providers": {
            "applies": "restart",
            "added": ["llm.local"],
            "removed": [],
            "changed": [],
        },
        "agents": {**DIFF_EMPTY["agents"], "added": ["assistant"]},  # type: ignore[dict-item]
    }

    rendered = cli._diff_listing(cli.DIFF.read(body))

    restart, reload = cli.HEADS[Applies.RESTART], cli.HEADS[Applies.RELOAD]
    assert rendered.count(restart) == 1
    assert rendered.count(reload) == 1
    # Each fact under the head of the boundary its own kind named, and
    # nothing else under either.
    blocks = {
        lines[0]: lines[1:] for lines in (block.splitlines() for block in rendered.split("\n\n"))
    }
    assert blocks[restart] == ["  providers  added: llm.local"]
    assert blocks[reload] == ["  agents     added: assistant"]
    # And the groups in the order `Applies` declares its members, which
    # is where `restart` above `reload` comes from: read off the
    # declaration rather than written out here.
    planted = (Applies.RESTART, Applies.RELOAD)
    assert [line for line in rendered.splitlines() if line in {restart, reload}] == [
        cli.HEADS[boundary] for boundary in Applies if boundary in planted
    ]


def test_the_comparison_renders_the_same_answer_as_the_same_bytes() -> None:
    """Determinism, said as bytes rather than as a style. What the
    rendering leaves out is a function of the answer alone, so two
    renders of one answer are one string; a set iterated somewhere in
    the grouping is where that would stop being true."""
    assert cli._diff_listing(DIFF_PENDING) == cli._diff_listing(DIFF_PENDING)


def test_the_comparison_does_not_let_a_name_steer_a_terminal(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The one thing a comparison carries that a far side wrote: the
    names, which are the rows as the store holds them.

    The grouping moved every line of this rendering, so the door the
    names go through is worth pinning where it now stands rather than
    where it used to. Neutralized rather than dropped, for the reason
    the import cases give: a name that vanished would be a pending
    change an operator was never told about.
    """
    body = {**DIFF_EMPTY}
    body["providers"] = {
        "applies": "reload",
        "added": [f"llm.local{STEERING}"],
        "removed": [],
        "changed": [],
    }
    monkeypatch.setattr(cli, "_call", lambda *_args, **_kwargs: body)

    assert run("diff") == 0

    printed = capsys.readouterr().out
    assert STEERING not in printed
    assert "llm.local" in printed


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
        # `applies` is which group a kind's facts land in rather than a
        # fact of its own, which is the one field the three rules do not
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


# Importing, and the apply that follows it
#
# `import` writes the document to the store and stops there (#371), and
# `apply` is the separate command that installs what is stored. The
# cases belong in this file rather than beside the acceptance spine's
# other write cases because what decides the behavior is a running
# server, and this is the file that injects one.
#
# What each surface is: one request per command, the notices the import
# prints because nothing else will say what the write is waiting on, and
# the order the two streams are read in.

# The MCP entry every case here configures, which is referenced by one
# agent and connected by nobody: what these are about is the apply's
# answer rather than a live connection.
ENTRY = {"transport": "streamable_http", "url": "http://127.0.0.1:9/mcp"}

WRITTEN = """\
providers:
  llm:
    brain:
      type: mock
      reply: Hello.
"""

# What the entry above is called in an imported document's answer, which
# is the section and the identity under it.
WROTE = "providers.llm.brain: wrote"

HELD = "a reload of this server's configuration is already running."


def _held():
    """A running server whose apply is refused because another one has
    it, which is the 409 an operator most plausibly meets: two people
    administering one deployment."""

    async def reload() -> ConfigReloadResult:
        raise ReloadInProgressError(HELD)

    return reload


def test_an_import_says_what_the_write_is_waiting_on(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The whole of what the verb promises: the document is written and
    nothing is installed, so the boundary it is waiting at is the
    operator's to know.

    One line for the whole document since #426, and it is this client's
    own: the act that was typed, what it wrote, and the state the write
    is in with the command that ends it. Pinned as bytes rather than
    through `boundaries`, because the line's whole point is that it
    replaces the sentence that reading looks for.

    One request, which is the other half of the claim: there is no
    second act behind this one to quieten or to turn off.
    """
    assert run("import", "-f", "-", stdin=WRITTEN) == 0

    printed = capsys.readouterr()
    assert printed.out.splitlines() == [WROTE]
    assert printed.err.splitlines() == [f"imported 1 entry, {cli.NOT_SERVING_YET}"]
    assert len(run.clients) == 1


IMPORTED_NOTHING = [
    ("a document that names nothing", "{}\n", []),
    ("a document the store already says", WRITTEN, [WROTE.replace("wrote", "unchanged")]),
]


@pytest.mark.parametrize(
    ("document", "printed_lines"),
    [(document, lines) for _, document, lines in IMPORTED_NOTHING],
    ids=[what for what, _, _ in IMPORTED_NOTHING],
)
def test_an_import_that_wrote_nothing_says_no_boundary_either(
    run,
    capsys: pytest.CaptureFixture[str],
    document: str,
    printed_lines: list[str],
) -> None:
    """Two of the three imports that succeed write nothing at all.

    A document every entry of which the store already holds writes no
    row, and a document naming no section has nothing to write in the
    first place. Neither is waiting on anything, so neither prints a
    boundary sentence: the notice is a fact of a write rather than of a
    command that ran.
    """
    # The all-unchanged case needs the row there first, so that what the
    # second run meets is a store that already says it.
    if printed_lines:
        assert run("import", "-f", "-", stdin=document) == 0
        capsys.readouterr()

    assert run("import", "-f", "-", stdin=document) == 0

    printed = capsys.readouterr()
    if printed_lines:
        assert printed.out.splitlines() == printed_lines
    else:
        assert printed.out.startswith(cli.NOTHING_IMPORTED)
    assert printed.err == ""


def test_what_an_import_wrote_is_read_before_the_boundary_under_it(run) -> None:
    """The ordering rule the rendering exists to keep.

    Two streams reach one terminal and only one of them is buffered, so
    what an operator reads in is flush order rather than write order.
    The lines saying what was written go to stdout and the line saying
    what they are waiting on goes to stderr, and a line that arrived
    above the entries it counts would be a boundary attached to
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

    with contextlib.redirect_stdout(buffered), contextlib.redirect_stderr(unbuffered):
        assert run("import", "-f", "-", stdin=WRITTEN) == 0
        buffered.flush()
        written = shared.getvalue().decode("utf-8")

    assert WROTE in written
    assert written.index(WROTE) < written.index(cli.NOT_SERVING_YET)


def test_a_refused_document_is_the_whole_answer(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """A document that will not resolve changed nothing, so there is
    nothing on either stream but the refusal, and one request was
    made."""
    assert run("import", "-f", "-", stdin="agents:\n  sam: {prompt: p, llm: ghost}\n") == 1

    printed = capsys.readouterr()
    assert printed.out == ""
    assert len(run.clients) == 1


def test_a_refused_act_is_raised_with_nothing_behind_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The half no assertion about a stream can make.

    A sequence stops at the first act that is refused, and the refusal
    is re-raised outside the handler that caught it. An exception raised
    INSIDE a handler carries the one it was handling on `__context__`,
    where a chain walker finds whatever THAT one was holding, which is
    why the refusal below carries a cause of its own: a transport
    failure behind a request is the shape that does it, and an httpx
    exception holds the URL it was given.
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
        cli._performed(cli.Invocation(), (first, replace(first)), reached)

    assert str(caught.value) == "the request did not complete"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert SECRET not in _chain(caught.value)


# What an imported answer may put where text belongs
#
# The three strings an imported entry carries reach stdout and stderr as
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
    """One imported entry as the API answers one, with whatever a case
    wants to see refused or neutralized in it."""
    return {
        "section": "agents",
        "identity": "sam",
        "outcome": "wrote",
        "notice": APPLY_NOTICE.sentence,
    } | overrides


IMPORTED_HOSTILE = [
    ("an escape sequence in an identity", {"identity": f"sam{STEERING}"}),
    ("an escape sequence in a notice", {"notice": f"wait{STEERING}"}),
    ("a lone surrogate in an identity", {"identity": f"sam{SURROGATE}"}),
    ("a lone surrogate in a notice", {"notice": f"wait{SURROGATE}"}),
]


@pytest.mark.parametrize(
    "overrides",
    [overrides for _, overrides in IMPORTED_HOSTILE],
    ids=[what for what, _ in IMPORTED_HOSTILE],
)
def test_the_import_rendering_does_not_let_an_answer_steer_a_terminal(
    overrides: dict[str, object], capsys: pytest.CaptureFixture[str]
) -> None:
    """Both strings the rendering prints, the identity on stdout and the
    notice on stderr.

    Read through the act rather than handed a dictionary, so what is
    exercised is the shape this client insists on and then the renderer
    it feeds, which is the path an answer really takes.
    """
    cli._imported(cli.IMPORT.read({"entries": [_entry(**overrides)]}))

    printed = capsys.readouterr()
    written = printed.out + printed.err
    assert STEERING not in written
    assert SURROGATE not in written
    # And the line is still the answer to what was asked, so what
    # happened to the character is neutralizing rather than dropping the
    # output it was in. Which of the two streams carried it is the
    # difference between the renderings and is pinned elsewhere.
    assert "agents.sam" in written


def test_an_imported_notice_arrives_neutralized_rather_than_dropped(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The other half of the rule above, on the one stream this
    rendering writes a far side's sentence to: what a notice loses is
    the characters that steer a terminal, and it keeps the words. A
    sentence dropped whole would be a boundary an operator was never
    told about.

    Quoted at all because the entry names no boundary set this client
    knows, which is the arm a server's sentence still reaches: a set it
    does know is answered in this client's own words, and a hostile
    sentence behind one is never printed at all.
    """
    cli._imported(cli.IMPORT.read({"entries": [_entry(notice=f"wait{STEERING}")]}))

    written = capsys.readouterr().err
    assert written.splitlines()[-1].startswith("wait?")
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
        cli._imported(cli.IMPORT.read({"entries": [_entry(identity=SURROGATE)]}))

    stream.flush()
    assert "agents" in stream.buffer.getvalue().decode("utf-8")


@pytest.mark.parametrize(
    ("outcome", "notice"),
    [
        pytest.param("unchanged", APPLY_NOTICE.sentence, id="unchanged-with-a-boundary"),
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
        cli.IMPORT.read(body)

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
        cli.IMPORT.read({"entries": [_entry(section=section)]})

    assert str(caught.value) == cli.UNREADABLE_WRITE
    assert SECRET not in _chain(caught.value)


def test_no_imported_refusal_is_retained_on_its_chain() -> None:
    """The half no assertion about a stream can make: a validation error
    retains the input it rejected, and the input here is a refused entry
    carrying a pasted credential in every field a body chooses."""
    body = {"entries": [_entry(outcome="unchanged", identity=SECRET, notice=SECRET)]}

    with pytest.raises(ConfigError) as caught:
        cli.IMPORT.read(body)

    assert SECRET not in _chain(caught.value)


# A boundary this client does not recognize
#
# An acknowledgement carries `applies` beside the sentence, and this
# client is one half of a pair that is not deployed together: the
# walkthrough runs a new client against whatever image is already there,
# which is how #386 was found. So the field has to be readable in three
# states, and each of them has to go through the act that reads the
# body rather than through a renderer handed a dictionary: strict
# validation refuses a body before any renderer sees it, which is
# exactly the failure a renderer-only case would not have seen.
#
# The middle state is #369's `sometime-in-the-future` bite in this
# surface's terms: a token from a server newer than this client is read
# as an older server's silence, never as a boundary to guess at.

# A boundary no version of this server has ever emitted, spelled the way
# a later one plausibly would.
UNKNOWN_BOUNDARY = "sometime-in-the-future"

ACKNOWLEDGED = {"wrote": "agent sam", "notice": APPLY_NOTICE.sentence}

# Spelled as the wire spells them, which is what a body carries.
TOLERATED = [
    ("no applies at all, from a server older than the field", None, ()),
    ("a token this client does not know", [Applies.RELOAD.value, UNKNOWN_BOUNDARY], ()),
    ("an empty set", [], ()),
    ("a set this client knows whole", [Applies.RELOAD.value], (Applies.RELOAD,)),
]


@pytest.mark.parametrize(
    ("applies", "expected"),
    [(applies, expected) for _, applies, expected in TOLERATED],
    ids=[what for what, _, _ in TOLERATED],
)
def test_a_write_is_read_whatever_boundaries_it_announces(
    applies: list[str] | None, expected: tuple[object, ...]
) -> None:
    """The single write's answer, through the act that reads it.

    A body this client cannot read whole reads as the field's default,
    which is what a server older than the vocabulary sends by saying
    nothing: the write landed, and turning the sentence beside it into a
    refusal would punish the client for the server's age after the fact.
    """
    body = ACKNOWLEDGED if applies is None else ACKNOWLEDGED | {"applies": applies}

    answer = cli.BIND_DEVICE.read(body)

    assert answer["notice"] == APPLY_NOTICE.sentence
    assert answer["applies"] == expected
    assert UNKNOWN_BOUNDARY not in repr(answer)


@pytest.mark.parametrize(
    ("applies", "expected"),
    [(applies, expected) for _, applies, expected in TOLERATED],
    ids=[what for what, _, _ in TOLERATED],
)
def test_an_imported_entry_is_read_whatever_boundaries_it_announces(
    applies: list[str] | None, expected: tuple[object, ...]
) -> None:
    """And the same three states one level down, because the tolerance
    lives in a walk over the shape and a nested entry is where a walk
    goes wrong."""
    entry = _entry() if applies is None else _entry(applies=applies)

    answer = cli.IMPORT.read({"entries": [entry]})

    read = answer["entries"][0]
    assert read["notice"] == APPLY_NOTICE.sentence
    assert read["applies"] == expected
    assert UNKNOWN_BOUNDARY not in repr(answer)


def test_an_unknown_boundary_is_never_printed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """What a body put in a closed field is not this client's to echo.

    The rendering half of the rule above, on the one stream a far side's
    words reach: an operator is told the sentence the server composed
    and nothing this client could not read.
    """
    cli._imported(cli.IMPORT.read({"entries": [_entry(applies=[UNKNOWN_BOUNDARY])]}))

    printed = capsys.readouterr()
    assert APPLY_NOTICE.sentence in printed.err
    assert UNKNOWN_BOUNDARY not in printed.out + printed.err


# And what this client says about a boundary it does know
#
# The other half of #386, completed by #426. The server states what is
# true of the write and this client names the command that crosses the
# boundary it states, and since the token is what travels, whichever
# side can say the whole of it says it once: where this client knows the
# set, its own line REPLACES the sentence rather than following it.
#
# The replacement happens for a set this client knows and has something
# to run about, and for nothing else. A set with no command to cross it,
# an absent set and a set carrying a token this client cannot name all
# print the server's sentence alone, which is the same rule the reading
# keeps one level down: an unknown state is quoted, never guessed at.

# What a write waiting at a reload is answered with, read from the table
# rather than written out again: what this asserts is which voice is
# printed, and what it says is the table's business.
RELOAD_LINE = cli.SPOKEN[frozenset({Applies.RELOAD})]

SPOKEN_FOR = [
    ("a set this client knows", [Applies.RELOAD.value], True),
    ("a set with no command that crosses it", [Applies.CHECK_IN.value], False),
    ("no applies at all, from a server older than the field", None, False),
    ("an empty set", [], False),
    ("a token this client does not know", [UNKNOWN_BOUNDARY], False),
]


@pytest.mark.parametrize(
    ("applies", "spoken"),
    [(applies, spoken) for _, applies, spoken in SPOKEN_FOR],
    ids=[what for what, _, _ in SPOKEN_FOR],
)
def test_a_write_is_answered_in_this_clients_words_where_it_knows_the_boundary(
    applies: list[str] | None, spoken: bool, capsys: pytest.CaptureFixture[str]
) -> None:
    """One write acknowledged, through the act that reads the body.

    One line either way, and which side wrote it is the whole of what
    changes: this client's where it can name the state and the command
    that ends it, and the server's verbatim where it cannot, because an
    old client printing a state sentence unchanged is what makes a state
    sentence safe.
    """
    body = ACKNOWLEDGED if applies is None else ACKNOWLEDGED | {"applies": applies}

    cli._acknowledged(cli.BIND_DEVICE.read(body))

    printed = capsys.readouterr()
    assert printed.err.splitlines() == [RELOAD_LINE if spoken else APPLY_NOTICE.sentence]
    assert (cli.INSTALLS in printed.err) is spoken


@pytest.mark.parametrize(
    ("applies", "spoken"),
    [(applies, spoken) for _, applies, spoken in SPOKEN_FOR],
    ids=[what for what, _, _ in SPOKEN_FOR],
)
def test_an_imported_entry_is_answered_by_the_side_that_knows_the_boundary(
    applies: list[str] | None, spoken: bool, capsys: pytest.CaptureFixture[str]
) -> None:
    """And the same five states one level down, where the boundaries
    arrive inside a list of entries rather than beside one write.

    A document's answer is the count line and what it could not say
    itself: the set this client knows rides the count line, and each set
    it does not contributes the server's sentence under it.
    """
    entry = _entry() if applies is None else _entry(applies=applies)

    cli._imported(cli.IMPORT.read({"entries": [entry]}))

    printed = capsys.readouterr()
    counted = f"imported 1 entry, {cli.NOT_SERVING_YET}" if spoken else "imported 1 entry"
    assert printed.err.splitlines() == [counted] + (
        [] if spoken else [APPLY_NOTICE.sentence]
    )
    assert (cli.INSTALLS in printed.err) is spoken


def test_a_document_waiting_on_one_install_says_so_once(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The collapse, over both sets there is something to run about.

    A document that wrote nine entities is waiting on one apply, not on
    nine and not on two: `{reload}` and `{reload, check-in}` are waiting
    on the same install, which is the fact the one command above
    `INSTALLS` states. So the answer is the act, the count, and the one
    clause, whatever mixture of the two known sets the entries carried.
    """
    entries = [
        _entry(identity="sam", applies=[Applies.RELOAD.value]),
        _entry(identity="alex", applies=[Applies.RELOAD.value]),
        _entry(
            section="devices",
            identity="aa:bb:cc:dd:ee:ff",
            notice=entities.BINDING_UNSERVED_NOTICE.sentence,
            applies=[Applies.RELOAD.value, Applies.CHECK_IN.value],
        ),
    ]

    cli._imported(cli.IMPORT.read({"entries": entries}))

    printed = capsys.readouterr()
    assert printed.out.splitlines() == [
        "agents.sam: wrote",
        "agents.alex: wrote",
        "devices.aa:bb:cc:dd:ee:ff: wrote",
    ]
    assert printed.err.splitlines() == [f"imported 3 entries, {cli.NOT_SERVING_YET}"]


def test_an_unchanged_entry_is_counted_by_neither_half(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """What the count counts is what this command wrote.

    An entry the store already said is not something the import did, so
    it is on stdout as its own outcome and nowhere in the line about
    what is waiting. A document of nothing but those has no line at all,
    which is what an import that changed nothing has to say about a
    boundary: there is nothing waiting.
    """
    unchanged = _entry(identity="alex", outcome="unchanged", notice=None)
    entries = [_entry(identity="sam", applies=[Applies.RELOAD.value]), unchanged]

    cli._imported(cli.IMPORT.read({"entries": entries}))

    printed = capsys.readouterr()
    assert printed.out.splitlines() == ["agents.sam: wrote", "agents.alex: unchanged"]
    assert printed.err.splitlines() == [f"imported 1 entry, {cli.NOT_SERVING_YET}"]

    cli._imported(cli.IMPORT.read({"entries": [unchanged]}))

    all_unchanged = capsys.readouterr()
    assert all_unchanged.out.splitlines() == ["agents.alex: unchanged"]
    assert all_unchanged.err == ""


def test_the_boundaries_are_read_as_a_set_and_not_as_a_sequence(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The same two boundaries, however a body serialized them.

    JSON has no set, so `applies` arrives as a list and the order is
    whatever the server that built it happened to produce; a token
    listed twice is the same answer as a token listed once. Neither is
    a fact about the write, so neither may reach the table lookup: three
    entries waiting at a reload and a check-in are answered by the one
    clause whichever way round each of them arrived, rather than one of
    them falling back to the server's sentence beneath it.
    """
    unserved = entities.BINDING_UNSERVED_NOTICE
    both = [Applies.RELOAD.value, Applies.CHECK_IN.value]
    entries = [
        _entry(identity="sam", notice=unserved.sentence, applies=both),
        _entry(identity="alex", notice=unserved.sentence, applies=list(reversed(both))),
        _entry(identity="kim", notice=unserved.sentence, applies=[*both, Applies.RELOAD.value]),
    ]

    cli._imported(cli.IMPORT.read({"entries": entries}))

    printed = capsys.readouterr()
    assert printed.err.splitlines() == [f"imported 3 entries, {cli.NOT_SERVING_YET}"]
    assert unserved.sentence not in printed.err


def test_two_entries_from_an_older_server_keep_both_sentences(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The mixed-version arm, and why the dedupe is by sentence.

    Every entry from a server older than the vocabulary carries the same
    empty set, so a boundary-only key would collapse an ordinary stored
    entry and a device binding, which are two different sentences, into
    one and tell an operator half of what they are waiting on. With no
    set to key on, the sentence is what is left, and both are printed
    under a count line carrying no clause: this client cannot say what
    to run about a boundary that was never stated.
    """
    entries = [
        _entry(identity="sam"),
        _entry(
            section="devices",
            identity="aa:bb:cc:dd:ee:ff",
            notice=entities.BINDING_NOTICE.sentence,
        ),
    ]

    cli._imported(cli.IMPORT.read({"entries": entries}))

    printed = capsys.readouterr()
    assert printed.err.splitlines() == [
        "imported 2 entries",
        APPLY_NOTICE.sentence,
        entities.BINDING_NOTICE.sentence,
    ]
    assert cli.INSTALLS not in printed.err


def test_a_mixed_document_keeps_the_remedy_and_the_quoted_sentence(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The answer both halves have to survive.

    One entry this client can speak for and one from a server whose
    boundary it cannot name: the collapse may not swallow the sentence
    it did not compose, and the quoting may not cost the operator the
    one command there is to run. So the clause rides the count line and
    the unreadable entry's sentence goes under it, and the token this
    client could not read reaches neither stream.
    """
    entries = [
        _entry(identity="sam", applies=[Applies.RELOAD.value]),
        _entry(
            section="devices",
            identity="aa:bb:cc:dd:ee:ff",
            notice=entities.BINDING_NOTICE.sentence,
            applies=[UNKNOWN_BOUNDARY],
        ),
    ]

    cli._imported(cli.IMPORT.read({"entries": entries}))

    printed = capsys.readouterr()
    assert printed.err.splitlines() == [
        f"imported 2 entries, {cli.NOT_SERVING_YET}",
        entities.BINDING_NOTICE.sentence,
    ]
    assert UNKNOWN_BOUNDARY not in printed.out + printed.err


def test_one_answer_renders_the_same_bytes_twice(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Determinism, on the surface this milestone re-cut, and on each
    stream separately: what is printed is a function of the answer, so
    two renderings of one answer are the same bytes on stdout and the
    same bytes on stderr.

    The answer carries both arms, because the one thing here that is not
    a fixed string is the dedupe, and a dedupe over an unordered
    container is where a rendering comes to differ between two runs of
    one program.
    """
    answer = {
        "entries": [
            _entry(identity="sam", applies=[Applies.RELOAD.value]),
            _entry(identity="alex", notice=entities.BINDING_NOTICE.sentence),
            _entry(identity="kim", applies=[UNKNOWN_BOUNDARY]),
        ]
    }

    cli._imported(cli.IMPORT.read(answer))
    first = capsys.readouterr()
    cli._imported(cli.IMPORT.read(answer))
    second = capsys.readouterr()

    assert first.out == second.out
    assert first.err == second.err
    # And it really is the answer this case is about rather than an
    # empty one compared with itself twice.
    assert first.err.splitlines()[0] == f"imported 3 entries, {cli.NOT_SERVING_YET}"


# What a single write's own answer may put where text belongs
#
# The import surface's cases above, on the surface that gained the same
# door in #426. Both strings an acknowledgement carries are far-side
# text: `wrote` is a line composed around a kind and an identity an
# operator chose, and `notice` is a sentence a server of any age
# composed. Neither is a closed token, so what a hostile or broken far
# side can put in either is whatever it likes.
#
# The two go through different bounds, which is the line `printable`
# draws and not a difference between the surfaces: what `wrote` says is
# a location, which a bound protects, and what `notice` says ends in the
# state it exists to state, which a cut would lose. The notice cases
# plant the boundary states the quoting arm is taken on, since a set
# this client knows is answered in its own words and a hostile sentence
# behind one never reaches a stream at all.


@pytest.mark.parametrize(
    "planted",
    [
        pytest.param(STEERING, id="an escape sequence"),
        pytest.param(SURROGATE, id="a lone surrogate"),
    ],
)
def test_a_write_rendering_does_not_let_what_it_wrote_steer_a_terminal(
    planted: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """The line saying what was written, on the stream it goes to.

    Read through the act rather than handed a dictionary, so what is
    exercised is the shape this client insists on and then the renderer
    it feeds, which is the path an answer really takes.
    """
    body = ACKNOWLEDGED | {"wrote": f"agent sam{planted}"}

    cli._acknowledged(cli.BIND_DEVICE.read(body))

    printed = capsys.readouterr()
    written = printed.out + printed.err
    assert planted not in written
    # And the line is still the answer to what was asked, so what
    # happened to the character is neutralizing rather than dropping the
    # output it was in.
    assert printed.out.startswith("wrote agent sam?")


def test_a_write_rendering_does_not_let_a_notice_steer_a_terminal(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The same rule on the other string, and the other stream."""
    body = ACKNOWLEDGED | {"notice": f"wait{STEERING}"}

    cli._acknowledged(cli.BIND_DEVICE.read(body))

    printed = capsys.readouterr()
    written = printed.out + printed.err
    assert STEERING not in written
    # Neutralized rather than dropped: a sentence dropped whole would be
    # a boundary an operator was never told about.
    assert printed.err.startswith("wait?")


def test_an_unprintable_line_never_leaves_as_an_exception() -> None:
    """`print` encodes, and a lone surrogate raises `UnicodeEncodeError`
    from inside it, which leaves this boundary as a traceback carrying
    the value: the failure a stream assertion cannot see, on the field
    the door was missing from.

    The planted value is a pasted credential with the surrogate behind
    it, which is the pair that makes the claim worth making. The
    credential reaches stdout, deliberately and for the reason the
    import cases record: what `wrote` names is a row as the store holds
    it, and a rendering that hid one would hide what the store holds.
    What may never happen is the raise, because an exception from the
    encoder carries the whole line out past the boundary that turns a
    failure into a sentence.
    """
    stream = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", errors="strict")
    body = ACKNOWLEDGED | {"wrote": f"agent {SECRET}{SURROGATE}"}
    leaked = ""

    with contextlib.redirect_stdout(stream):
        try:
            cli._acknowledged(cli.BIND_DEVICE.read(body))
        except UnicodeEncodeError as raised:  # pragma: no cover - the door keeps this empty
            leaked = _chain(raised)

    assert leaked == ""
    assert SECRET not in leaked
    stream.flush()
    written = stream.buffer.getvalue().decode("utf-8")
    assert written.startswith(f"wrote agent {SECRET}?")
    assert SURROGATE not in written


def test_an_unprintable_notice_never_leaves_as_an_exception() -> None:
    """The failure a stream assertion cannot see: `print` encodes, and a
    lone surrogate raises `UnicodeEncodeError` from inside it, which
    leaves this boundary as a traceback carrying the value.

    Written to a real encoding rather than to pytest's capture, because
    what raises is the encoder and a buffer that never encodes cannot
    raise.
    """
    stream = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", errors="strict")
    body = ACKNOWLEDGED | {"notice": f"wait{SURROGATE}"}

    with contextlib.redirect_stderr(stream):
        cli._acknowledged(cli.BIND_DEVICE.read(body))

    stream.flush()
    written = stream.buffer.getvalue().decode("utf-8")
    assert "wait" in written
    assert SURROGATE not in written


def test_no_refused_acknowledgement_is_retained_on_its_chain() -> None:
    """The half no assertion about a stream can make: a validation error
    retains the input it rejected, and the input here is an
    acknowledgement carrying a pasted credential in both the fields a
    body composes."""
    body = {"wrote": {"agent": SECRET}, "notice": SECRET, "applies": []}

    with pytest.raises(ConfigError) as caught:
        cli.BIND_DEVICE.read(body)

    assert str(caught.value) == cli.UNREADABLE_WRITE
    assert SECRET not in _chain(caught.value)
