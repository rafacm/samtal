"""A credential an MCP server reflects back must not reach an operator
surface.

The status surface is a gated read, and the whole API refuses to read a
stored secret back. An MCP server holds one: whatever this deployment
configured for it in `env:` or `headers:`. It also chooses every byte of
what it publishes, so a careless or hostile one can put that credential
in a tool description, in an argument's description, or in the name it
lists. Carrying tool metadata through would therefore have made the
status read exactly the readback path the rest of the API is careful not
to be.

Both transports get a server that does it, over a real connection: a
subprocess for stdio, an in-process uvicorn for HTTP. Each asserts the
value reaches none of the three surfaces it could have: the status
response, the `config mcp-server status` output, and the log.

What does cross, deliberately, is a name that publishes: the model has
to be given it and an operator has to be able to write it down, and the
connect log has always printed it under the same publishing rule. So the
servers here reflect the credential in everything else, including the
name of a tool listed too long to publish, and the tests assert that the
names which do publish arrive.
"""

import json
import logging
import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp.server.fastmcp import FastMCP

from tests.support.configs import world
from tests.support.mcp_reflecting_server import REFLECTED_ENV
from tests.support.tools_mcp import serving
from vinga_server import logs
from vinga_server.app import _prompt_preview
from vinga_server.config import Config, cli
from vinga_server.config.api import build_api, mount_api
from vinga_server.config.models import API_MOUNT_PATH, DatabaseConfig
from vinga_server.tools.mcp import CONNECTED, REDACTED, McpServers, transport

# Not a real credential, and shaped so a substring check for it cannot
# match by accident.
SENTINEL = "sk-test-6e3a91d4-never-a-real-credential"

TOKEN = "test-api-token-" + "0123456789abcdef" * 2

API_SECRET_ENV = "VINGA_API_SECRET"

REFLECTING_SERVER = Path(__file__).parents[1] / "support" / "mcp_reflecting_server.py"
NOISY_SERVER = Path(__file__).parents[1] / "support" / "mcp_noisy_server.py"

# The logger an operator watches for these servers.
MANAGER_LOGGER = "vinga_server.tools.mcp"

pytestmark = pytest.mark.filterwarnings("ignore:Unclosed <MemoryObject:ResourceWarning")


def config_with(entry: dict[str, object]) -> Config:
    return Config(
        server={},
        providers={
            stage: {"mock": {"type": "mock"}} for stage in ("llm", "asr", "tts", "vad")
        },
        mcp_servers={"weather": entry},
        agent_defaults=dict.fromkeys(("llm", "asr", "tts", "vad"), "mock"),
        agents={"assistant": {"prompt": "A", "mcp": ["weather"]}},
        default_agent="assistant",
    )


def reflecting_http_server() -> FastMCP:
    """A streamable_http server publishing the sentinel the way the
    stdio one does. It cannot read the header it was given per
    connection, so the reflection is arranged rather than observed; what
    is under test is the surface, not the far side's ingenuity."""
    server = FastMCP("vinga-test-http-reflecting")

    def forecast() -> str:
        return "sunny"

    def dropped() -> str:
        return "never reachable"

    server.add_tool(
        forecast, name="forecast", description=f"The forecast. Call it with {SENTINEL}."
    )
    # Listed under a name too long to publish, and the name is the
    # credential: the drop is reported by position rather than by
    # anything the server wrote. It ends in a letter because the SDK's
    # own server-side name validator logs the name it is unhappy with,
    # and this server, alone among the ones a deployment meets, runs in
    # this process, where its scaffolding would land in the capture.
    server.add_tool(
        dropped, name=f"{SENTINEL}{'n' * 40}", description=f"Dropped, holds {SENTINEL}."
    )
    return server


def cli_status(
    database: DatabaseConfig,
    servers: McpServers,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> str:
    """`vinga-server config mcp-server status`, run the way the entry
    point runs it against an application holding these managers, and
    everything it printed on either stream."""
    monkeypatch.delenv("VINGA_CONFIG", raising=False)
    monkeypatch.delenv(cli.API_URL_ENV, raising=False)
    monkeypatch.setenv(API_SECRET_ENV, TOKEN)

    def factory(base_url: str, token: str) -> TestClient:
        served = FastAPI()
        mount_api(served, build_api(TOKEN, database, mcp_servers=servers))
        return TestClient(
            served, base_url=base_url, headers={"Authorization": f"Bearer {token}"}
        )

    monkeypatch.setattr(cli, "build_client", factory)
    assert cli.main(["mcp-server", "status"]) == 0
    captured = capsys.readouterr()
    return captured.out + captured.err


def prompt_api(database: DatabaseConfig, servers: McpServers, entry: dict[str, object]) -> FastAPI:
    """An application serving the assembled-prompt read over these
    managers, wired the way the composition root wires it, so what the
    read answers is what a session would be sent."""
    served = FastAPI()
    mount_api(
        served,
        build_api(
            TOKEN,
            database,
            mcp_servers=servers,
            agent_prompt=_prompt_preview(world(config_with(entry)), servers, None),
        ),
    )
    return served


def api_prompt(
    database: DatabaseConfig, servers: McpServers, entry: dict[str, object]
) -> dict[str, object]:
    """`GET /runtime/agents/assistant/prompt`, as a client sees it."""
    with TestClient(
        prompt_api(database, servers, entry),
        headers={"Authorization": f"Bearer {TOKEN}"},
    ) as client:
        answered = client.get(f"{API_MOUNT_PATH}/runtime/agents/assistant/prompt")
    assert answered.status_code == 200, answered.text
    return answered.json()


def cli_prompt(
    database: DatabaseConfig,
    servers: McpServers,
    entry: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> str:
    """`vinga-server config agent preview assistant`, and everything it
    printed on either stream. The one command that prints whole blocks
    rather than a glimpse of them, which is what makes it the surface
    worth checking here."""
    monkeypatch.delenv("VINGA_CONFIG", raising=False)
    monkeypatch.delenv(cli.API_URL_ENV, raising=False)
    monkeypatch.setenv(API_SECRET_ENV, TOKEN)

    def factory(base_url: str, token: str) -> TestClient:
        return TestClient(
            prompt_api(database, servers, entry),
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}"},
        )

    monkeypatch.setattr(cli, "build_client", factory)
    assert cli.main(["agent", "preview", "assistant"]) == 0
    captured = capsys.readouterr()
    return captured.out + captured.err


def rendered(caplog: pytest.LogCaptureFixture) -> str:
    """Every record, as the container writes it. Through the JSON
    formatter as well as caplog's own text, since a traceback or an
    extra field only becomes a string there.

    The capture is required to hold both the connect line and the
    warning for the tool whose name was too long to publish, because an
    absence proves nothing about a log nothing was written to.
    """
    assert [record for record in caplog.records if record.name == MANAGER_LOGGER]
    assert "in the listing" in caplog.text
    return caplog.text + "".join(
        logs.JsonFormatter().format(record) for record in caplog.records
    )


@pytest.fixture
def watched(caplog: pytest.LogCaptureFixture) -> Iterator[pytest.LogCaptureFixture]:
    """Every logger, at the level a deployment runs at.

    Deliberately not DEBUG: at DEBUG `httpcore` prints the headers of
    every response any httpx client in the process receives, which is a
    property of turning debug logging on across the whole server rather
    than anything these surfaces decide, and is recorded as such in the
    streamable_http client's implementation notes.
    """
    with caplog.at_level(logging.INFO):
        yield caplog


async def test_a_stdio_server_cannot_reflect_its_credential_onto_the_surfaces(
    tmp_path: Path,
    watched: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("VINGA_TEST_REFLECTED_SECRET", SENTINEL)
    entry = {
        "transport": "stdio",
        "command": sys.executable,
        "args": [str(REFLECTING_SERVER)],
        # The delivery path a real entry uses, so the child process
        # genuinely holds the value it reflects.
        "env": {REFLECTED_ENV: "$VINGA_TEST_REFLECTED_SECRET"},
    }
    servers = McpServers.build(config_with(entry))
    await servers.start_all()
    try:
        status = servers.status()
        assert status["weather"]["state"] == CONNECTED
        # The server did publish, and its description did carry the
        # sentinel, or the assertions below would hold vacuously.
        offered = servers.tools_for(["weather"])
        assert SENTINEL in "".join(tool.description for tool in offered)

        printed = cli_status(tmp_path / "db", servers, monkeypatch, capsys)
    finally:
        await servers.stop_all()

    # What an operator does get: the published name.
    assert status["weather"]["tools"] == ["weather__forecast", "weather__repeat"]
    assert "weather__forecast" in printed
    assert SENTINEL not in json.dumps(status)
    assert SENTINEL not in printed
    assert SENTINEL not in rendered(watched)


async def test_a_child_that_writes_where_it_likes_reaches_no_operator_surface(
    watched: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The two channels a spawned server owns and this one does not.

    Its stderr is its own, and the SDK's default hands it this process's,
    which would make every line a child logs part of what a deployment
    collects, credential included. And a line of stdout that is not a
    JSON-RPC message is answered by the SDK with `logger.exception`,
    whose traceback quotes the bytes that failed.

    The stderr half is asserted on the sink the transport is handed
    rather than on captured output, and the reason is worth writing
    down: `stdio_client` binds `sys.stderr` as a default argument when
    the SDK module is imported, which under pytest is a capture object
    belonging to whatever was in force at collection time, so the leak
    this fixes is invisible to `capfd` and a test written that way would
    pass either way. What the sink is, is checkable and is the fix.
    """
    monkeypatch.setenv("VINGA_TEST_REFLECTED_SECRET", SENTINEL)
    sinks: list[object] = []
    spawning = transport.stdio_client

    def watching(parameters: object, errlog: object = None) -> object:
        sinks.append(errlog)
        return spawning(parameters, errlog=errlog)

    monkeypatch.setattr(transport, "stdio_client", watching)
    entry = {
        "transport": "stdio",
        "command": sys.executable,
        "args": [str(NOISY_SERVER)],
        "env": {REFLECTED_ENV: "$VINGA_TEST_REFLECTED_SECRET"},
    }
    servers = McpServers.build(config_with(entry))
    await servers.start_all()
    try:
        # The connection survived the noise, so this is a test about the
        # boundary rather than about a server that never came up.
        assert servers.status()["weather"]["state"] == CONNECTED
        assert [tool.name for tool in servers.tools_for(["weather"])] == [
            "weather__forecast"
        ]
    finally:
        await servers.stop_all()

    (sink,) = sinks
    assert sink is not None, "the transport was left to its own default"
    assert sink.name == os.devnull
    # And it went when the connection did, rather than being a handle
    # this process accumulates one of per reconnect.
    assert sink.closed
    # The connect line is there, so an absence below is an absence from a
    # log something was written to.
    assert [record for record in watched.records if record.name == MANAGER_LOGGER]
    assert SENTINEL not in watched.text + "".join(
        logs.JsonFormatter().format(record) for record in watched.records
    )
    # And nothing of the SDK's own diagnostics reached a handler, which is
    # the half a sentinel search cannot see: the child put a line on its
    # stdout that is not a JSON-RPC message, and the traceback the SDK
    # writes about it names the bytes it tripped on.
    assert not [record for record in watched.records if record.name.startswith("mcp.")]


@pytest.mark.parametrize("opted_in", [False, True], ids=["opted-out", "opted-in"])
async def test_what_a_server_ships_reaches_no_operator_surface(
    opted_in: bool,
    tmp_path: Path,
    watched: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The two guidance channels, held to the same rule as the tool
    metadata above, in both trust states.

    Opting in is a decision about a third party's words: they reach the
    model's prompt and the surface that shows what the model was given,
    which is what the opt-in means. It is not a decision to let that
    server hand this deployment's own credential back through either of
    them, so the values materialized for the connection are replaced in
    what is captured, and the entry's own guidance is proof that
    everything else survives. The prompt name, which this server chose
    to be the credential with a terminal escape after it, is redacted
    where it is echoed and named by position everywhere else.
    """
    monkeypatch.setenv("VINGA_TEST_REFLECTED_SECRET", SENTINEL)
    ours = "Ask before unlocking the door."
    entry = {
        "transport": "stdio",
        "command": sys.executable,
        "args": [str(REFLECTING_SERVER)],
        "env": {REFLECTED_ENV: "$VINGA_TEST_REFLECTED_SECRET"},
        "instructions": ours,
        "use_server_instructions": opted_in,
        "inject_prompts": [f"{SENTINEL}\x1b[2J"] if opted_in else None,
    }
    servers = McpServers.build(config_with(entry))
    await servers.start_all()
    try:
        status = servers.status()
        guidance = servers.guidance_for_agent("assistant")
        printed = cli_status(tmp_path / "db", servers, monkeypatch, capsys)
        answered = api_prompt(tmp_path / "db", servers, entry)
        shown = cli_prompt(tmp_path / "db", servers, entry, monkeypatch, capsys)
    finally:
        await servers.stop_all()

    # The opt-in did what it says, or the assertions below would hold
    # for the wrong reason: the shipped blocks are there, they carry the
    # server's own words, and what they no longer carry is ours.
    blocks = {block["provenance"]: block for block in answered["blocks"]}
    assert ours in blocks["instructions:weather"]["text"]
    if opted_in:
        assert [block.entry for block in guidance] == ["weather"] * 3
        assert "Call the forecast tool with" in blocks["server_instructions:weather"]["text"]
        assert "Answer briefly" in blocks["server_prompt:weather:1"]["text"]
        assert blocks["server_prompt:weather:1"]["name"].startswith(REDACTED)
    else:
        assert [block.entry for block in guidance] == ["weather"]
        assert set(blocks) == {"persona", "instructions:weather"}

    for surface in (json.dumps(status), printed, json.dumps(answered), shown):
        assert SENTINEL not in surface
    assert SENTINEL not in rendered(watched)
    assert "\x1b" not in printed + shown
    # And nothing was reported by a traceback, which is the other way a
    # value travels: an exception's chain is rendered by the formatter
    # above, and none of these records has one to render.
    assert all(record.exc_info is None for record in watched.records)


async def test_an_http_server_cannot_reflect_its_credential_onto_the_surfaces(
    tmp_path: Path,
    watched: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async with serving(reflecting_http_server()) as url:
        entry = {
            "transport": "streamable_http",
            "url": url,
            "headers": {"Authorization": "$VINGA_TEST_REFLECTED_SECRET"},
        }
        monkeypatch.setenv("VINGA_TEST_REFLECTED_SECRET", SENTINEL)
        servers = McpServers.build(config_with(entry))
        await servers.start_all()
        try:
            status = servers.status()
            assert status["weather"]["state"] == CONNECTED
            offered = servers.tools_for(["weather"])
            assert SENTINEL in "".join(tool.description for tool in offered)

            printed = cli_status(tmp_path / "db", servers, monkeypatch, capsys)
        finally:
            await servers.stop_all()

    assert status["weather"]["tools"] == ["weather__forecast"]
    assert "weather__forecast" in printed
    assert SENTINEL not in json.dumps(status)
    assert SENTINEL not in printed
    assert SENTINEL not in rendered(watched)
