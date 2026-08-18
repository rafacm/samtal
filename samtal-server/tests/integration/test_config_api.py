"""The configuration API over a real socket, and across a restart.

The unit suite drives the same routes through an injected test client,
which is the right seam for the acceptance suite and cannot show two
things. One is that an answer arrives at all rather than being cut short
by the client's own timeouts, which only a real connection and a real
lock can demonstrate. The other is the API-era first start, which is not
one request but a sequence of them across two server lifetimes: start on
an empty database, configure over HTTP, restart, and serve what was
configured.
"""

import os
import sqlite3
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from samtal_server import db as db_module
from samtal_server.app import create_app
from samtal_server.config import cli
from samtal_server.config.boot import load_boot_config
from samtal_server.db import DATABASE_FILENAME

# The pipeline a first deployment writes, in the order the write-time
# reference checks require: providers, then what names them.
PIPELINE = [
    ("PUT", "/providers/llm/mock", {"type": "mock", "reply": "You said {text}."}),
    ("PUT", "/providers/asr/ears", {"type": "mock", "text": "hello"}),
    ("PUT", "/providers/tts/voice", {"type": "mock"}),
    ("PUT", "/providers/vad/gate", {"type": "mock"}),
    (
        "PUT",
        "/agent-defaults",
        {"llm": "mock", "asr": "ears", "tts": "voice", "vad": "gate"},
    ),
    ("PUT", "/agents/assistant", {"prompt": "You are an assistant."}),
    ("PUT", "/devices/aa:bb:cc:dd:ee:ff", {"agents": ["assistant"]}),
]


def _token() -> str:
    return os.environ["SAMTAL_API_SECRET"]


@pytest.fixture
def boot_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The database directory both application lifetimes read, named the
    way a deployment names it: through the environment the settings
    machinery reads."""
    monkeypatch.delenv("SAMTAL_CONFIG", raising=False)
    monkeypatch.setenv("SAMTAL_SERVER__DATABASE__DIR", str(tmp_path / "db"))
    return tmp_path / "db"


def test_an_empty_start_is_configured_over_http_and_serves_after_a_restart(
    served_api, boot_directory: Path
) -> None:
    """Getting Started, executed. An empty domain half is a valid boot,
    which is what makes the whole procedure work: the server comes up
    serving no agents, is configured over the API it serves, and picks
    the configuration up at the next start.

    Two application lifetimes, deliberately. The first one answers the
    writes and never sees their effect, because configuration is a
    boot-time snapshot; the second is the restart, and it is the one that
    has to serve the conversation."""
    with served_api(boot_directory) as api_url:
        client = httpx.Client(
            base_url=api_url, headers={"Authorization": f"Bearer {_token()}"}, timeout=30
        )
        try:
            # Empty is a state, not a failure: this is what the first
            # start of a fresh deployment reads.
            assert client.get("/config").json()["config"]["agents"] == {}

            for method, path, body in PIPELINE:
                answer = client.request(method, path, json=body)
                assert answer.status_code == 200, (path, answer.text)
                assert answer.json()["notice"]

            # The writes are all in, and readable through the same API.
            assert client.get("/agents").json()["assistant"]["entity"]["prompt"]
        finally:
            client.close()

    # The restart. A second application, built the way a deployment
    # builds one, from the database the writes above went into.
    booted = load_boot_config()
    restarted = create_app(booted.config, booted.secrets)

    # Read through its own lifespan, which is what builds them (#142):
    # the pipeline resolving for that agent is what "serves the
    # conversation configuration" means, every stage built at boot, and
    # it is also the check the first application could not have passed.
    with TestClient(restarted) as serving:
        composition = restarted.state.composition
        assert composition.config.agents_for_device("aa:bb:cc:dd:ee:ff") == ["assistant"]
        providers = composition.agent_providers["assistant"]
        assert composition.config.prompt_for_agent("assistant") == "You are an assistant."
        assert providers.llm is not None
        assert providers.asr is not None
        assert providers.tts is not None
        assert providers.vad is not None

        # It serves, too.
        assert serving.get("/healthz").status_code == 200
        agents = serving.get(
            "/api/agents", headers={"Authorization": f"Bearer {_token()}"}
        ).json()
        assert agents["assistant"]["entity"]["prompt"] == "You are an assistant."


def test_a_contended_write_answers_over_a_real_socket(
    served_api, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The retryable refusal through the client the CLI actually builds,
    over a real connection, with a real lock held.

    The unit suite forces the same 409 through an injected test client,
    which cannot show what this one does: that the answer arrives rather
    than being cut short by the client's own read timeout. The thresholds
    here are deliberately short so the test finishes; that the production
    read timeout outlasts the production busy timeout is asserted
    directly in the unit suite, where nothing is shortened."""
    directory = tmp_path / "db"
    monkeypatch.setattr(db_module, "BUSY_TIMEOUT_MS", 500)

    with served_api(directory) as api_url:
        # The API opens the database per request, so one read is what
        # creates the file this then takes the lock on.
        opener = cli.build_client(api_url, _token())
        try:
            assert opener.get("/config").status_code == 200
        finally:
            opener.close()

        holder = sqlite3.connect(directory / DATABASE_FILENAME, isolation_level=None)
        holder.execute("BEGIN IMMEDIATE")
        client = cli.build_client(api_url, _token())
        try:
            response = client.put("/agents/sam", json={"prompt": "You are Sam."})
        finally:
            client.close()
            holder.close()

        assert response.status_code == 409
        assert set(response.json()) == {"detail"}

        # And with the lock let go the same request is answered, which is
        # what makes the refusal above the retryable one it says it is.
        client = cli.build_client(api_url, _token())
        try:
            answered = client.put("/agents/sam", json={"prompt": "You are Sam."})
        finally:
            client.close()
        assert answered.status_code == 200


def test_the_reload_answers_over_a_real_socket(served_api, tmp_path: Path) -> None:
    """The reload's answer on a real connection, through the client the
    CLI actually builds.

    A server with nothing configured is what this fixture serves, so
    what it shows is the shape of the answer and that it arrives at all.
    The refusal is the half that needs something running to be worth
    asserting, and it is proven in `test_mcp_reload.py` against a server
    holding a connected MCP server and a live grant.
    """
    with served_api(tmp_path / "db") as api_url:
        client = cli.build_client(api_url, _token())
        try:
            applied = client.post("/runtime/mcp-servers/reload")
        finally:
            client.close()

    assert applied.status_code == 200, applied.text
    assert applied.json() == {
        "started": [],
        "restarted": [],
        "stopped": [],
        "unchanged": [],
        "servers": {},
    }
