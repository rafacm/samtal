"""The `/runtime` namespace: what the server is doing, not what is
stored.

The status read answers from the MCP registry the serving application
was handed, so what is checked here is the transport around it: the
gate, the shape, the honest empty answer when there is no server, and
the one structural reason this namespace exists at all, that an entity
may legally be named after a word a route wants.
"""

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from samtal_server.config import Config
from samtal_server.config.api import build_api
from samtal_server.config.secrets import MASTER_KEY_ENV, generate_key
from samtal_server.tools.mcp import CONNECTED, DOWN, UNUSED, McpServers

TOKEN = "test-api-token-" + "0123456789abcdef" * 2

STATUS_PATH = "/runtime/mcp-servers"

STDIO_SERVER = Path(__file__).parents[1] / "support" / "mcp_stdio_server.py"


def entry_data(**overrides: object) -> dict[str, object]:
    return {
        "transport": "stdio",
        "command": sys.executable,
        "args": [str(STDIO_SERVER)],
    } | overrides


def config_with(servers: dict[str, object], granted: list[str]) -> Config:
    return Config(
        server={},
        providers={
            stage: {"mock": {"type": "mock"}} for stage in ("llm", "asr", "tts", "vad")
        },
        mcp_servers=servers,
        agent_defaults=dict.fromkeys(("llm", "asr", "tts", "vad"), "mock"),
        agents={"assistant": {"prompt": "A", "mcp": granted}},
        default_agent="assistant",
    )


@pytest.fixture
def directory(tmp_path: Path) -> Path:
    return tmp_path / "db"


@pytest.fixture
def keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MASTER_KEY_ENV, generate_key())


@contextmanager
def serving(directory: Path, servers: McpServers | None) -> Iterator[TestClient]:
    api = build_api(TOKEN, directory, mcp_servers=servers)
    with TestClient(api, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        yield client


@pytest.fixture
def client(directory: Path) -> Iterator[TestClient]:
    """An application built without a server around it, which is what
    the document is rendered from and what every test that does not care
    about the runtime gets."""
    with serving(directory, None) as client:
        yield client


def test_the_status_read_needs_the_bearer_token(directory: Path) -> None:
    # The gate runs in front of routing, so this route inherits it
    # rather than declaring anything of its own. A client of its own,
    # since the shared one carries the header.
    with TestClient(build_api(TOKEN, directory)) as anonymous:
        assert anonymous.get(STATUS_PATH).status_code == 401
        wrong = anonymous.get(STATUS_PATH, headers={"Authorization": "Bearer wrong"})
        assert wrong.status_code == 401


def test_an_application_without_a_server_reports_no_runtime(client: TestClient) -> None:
    """The same honesty `loaded_agents = ()` has: there is nothing
    running to describe, so the answer is empty rather than invented."""
    response = client.get(STATUS_PATH)

    assert response.status_code == 200
    assert response.json() == {}


async def test_the_read_answers_with_what_the_registry_holds(directory: Path) -> None:
    config = config_with({"tools": entry_data(), "shelved": entry_data()}, ["tools"])
    servers = McpServers.build(config)
    await servers.start_all()
    try:
        with serving(directory, servers) as client:
            answered = client.get(STATUS_PATH).json()
    finally:
        await servers.stop_all()

    assert set(answered) == {"shelved", "tools"}
    connected = answered["tools"]
    assert connected["state"] == CONNECTED
    assert connected["reason"] is None
    assert "tools__secret_word" in connected["tools"]
    assert connected["grants"] == {"assistant": None}
    # An entry no agent references is the question this surface answers
    # that nothing else does.
    assert answered["shelved"]["state"] == UNUSED
    assert answered["shelved"]["grants"] == {}


async def test_a_dead_server_is_reported_down_with_its_reason(directory: Path) -> None:
    dead = entry_data(command="/nonexistent/mcp-server", args=[])
    servers = McpServers.build(config_with({"tools": dead}, ["tools"]))
    await servers.start_all()
    try:
        with serving(directory, servers) as client:
            answered = client.get(STATUS_PATH).json()
    finally:
        await servers.stop_all()

    assert answered["tools"]["state"] == DOWN
    assert answered["tools"]["reason"]
    assert answered["tools"]["tools"] == []


@pytest.mark.usefixtures("keys")
def test_an_entry_named_status_is_still_an_entity(client: TestClient) -> None:
    """The reason the runtime namespace is not `/mcp-servers/status`.

    `status` passes the entry-name rule, so a database written before
    this route existed may already hold one, and a runtime route inside
    the entity namespace would have shadowed it. It is read, written and
    deleted as the entity it is, while the runtime route answers beside
    it.
    """
    entry = {"transport": "streamable_http", "url": "http://127.0.0.1:9/mcp"}

    assert client.put("/mcp-servers/status", json=entry).status_code == 200
    read = client.get("/mcp-servers/status")
    assert read.status_code == 200
    assert read.json()["entity"]["url"] == entry["url"]
    assert "status" in client.get("/mcp-servers").json()
    # And the runtime read is a different resource, not that entry.
    assert client.get(STATUS_PATH).json() == {}
    assert client.delete("/mcp-servers/status").status_code == 200
    assert client.get("/mcp-servers/status").status_code == 404
