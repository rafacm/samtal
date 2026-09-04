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

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.support.problems import PROBLEM_KEYS
from tests.support.stores import holding_the_write_lock, the_lock_held
from vinga_server.app import create_app
from vinga_server.config import cli
from vinga_server.config.boot import load_boot_config

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
    return os.environ["VINGA_API_SECRET"]


@pytest.fixture
def no_config_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """A boot with nothing but the environment, which is how a
    deployment names its database."""
    monkeypatch.delenv("VINGA_CONFIG", raising=False)


def test_an_empty_start_is_configured_over_http_and_serves_after_a_restart(
    served_api, no_config_file: None
) -> None:
    """Getting Started, executed. An empty domain half is a valid boot,
    which is what makes the whole procedure work: the server comes up
    serving no agents, is configured over the API it serves, and picks
    the configuration up at the next start.

    Two application lifetimes, deliberately. The first one answers the
    writes and never sees their effect, because configuration is a
    boot-time snapshot; the second is the restart, and it is the one that
    has to serve the conversation."""
    with served_api() as api_url:
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
    restarted = create_app(booted.config, booted.secrets, from_store=True)

    # Read through its own lifespan, which is what builds them (#142):
    # the pipeline resolving for that agent is what "serves the
    # conversation configuration" means, every stage built at boot, and
    # it is also the check the first application could not have passed.
    with TestClient(restarted) as serving:
        composition = restarted.state.composition
        generation = composition.generations.current()
        served = generation.config
        assert served.agents_for_device("aa:bb:cc:dd:ee:ff") == ["assistant"]
        providers = generation.providers.agents["assistant"]
        assert served.prompt_for_agent("assistant") == "You are an assistant."
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
    served_api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The retryable refusal through the client the CLI actually builds,
    over a real connection, with a real lock held.

    The unit suite forces the same 409 through an injected test client,
    which cannot show what this one does: that the answer arrives rather
    than being cut short by the client's own read timeout. The threshold
    here is deliberately short so the test finishes; that the production
    read timeout outlasts the production lock timeout is asserted
    directly in the unit suite, where nothing is shortened."""
    with holding_the_write_lock(monkeypatch), served_api() as api_url:
        opener = cli.build_client(api_url, _token())
        try:
            assert opener.get("/config").status_code == 200
        finally:
            opener.close()

        with the_lock_held():
            client = cli.build_client(api_url, _token())
            try:
                response = client.put("/agents/sam", json={"prompt": "You are Sam."})
            finally:
                client.close()

        assert response.status_code == 409
        assert set(response.json()) == PROBLEM_KEYS

        # And with the lock let go the same request is answered, which is
        # what makes the refusal above the retryable one it says it is.
        client = cli.build_client(api_url, _token())
        try:
            answered = client.put("/agents/sam", json={"prompt": "You are Sam."})
        finally:
            client.close()
        assert answered.status_code == 200


def test_the_reload_answers_over_a_real_socket(served_api) -> None:
    """The reload's answer on a real connection, through the client the
    CLI actually builds.

    A server with nothing configured is what this fixture serves, so
    what it shows is the shape of the answer and that it arrives at all.
    The refusal is the half that needs something running to be worth
    asserting, and it is proven in `test_mcp_reload.py` against a server
    holding a connected MCP server and a live grant.
    """
    with served_api() as api_url:
        client = cli.build_client(api_url, _token())
        try:
            applied = client.post(RELOAD)
        finally:
            client.close()

    assert applied.status_code == 200, applied.text
    assert applied.json() == {
        "mcp": {
            "started": [],
            "restarted": [],
            "stopped": [],
            "unchanged": [],
            "servers": {},
        },
        "prompts": {"changed": []},
        # Present and empty rather than null: this server applies both
        # kinds of cached speech, the filled pauses and the phrase a
        # failed reply says, and it considered every agent it has, which
        # is none.
        "fillers": {
            "resynthesized": [],
            "reused": [],
            "disabled": [],
            "fallback_resynthesized": [],
            "fallback_reused": [],
            "fallback_degraded": [],
        },
        # Present and empty for the same reason: this server builds the
        # engines its agents reference, and it has no agents.
        "providers": {"built": [], "reused": [], "retired": []},
        # And the last section, present and empty for the same reason
        # again: the agent set is what this apply installs, and the
        # store and the world being served agree that there is none.
        "agents": {"added": [], "removed": [], "defaults_changed": False},
    }


# The stored-versus-running diff
#
# The care point of the whole read, over a real socket: what is pending
# is what has not been applied, and what has been applied stops being
# pending the moment it is. Only a running server can show the second
# half, because the thing that applies an MCP change is a request made
# to the server that is serving the diff.

DIFF = "/runtime/config/diff"

RELOAD = "/runtime/config/reload"

# Entries no agent grants, so nothing is ever connected for them and the
# reload starts and stops nothing. They are the case the comparison
# turns on: an entry with no manager still has to be compared, and a
# prompt-only edit still has to be reported.
TOOLS = {"transport": "stdio", "command": "/bin/echo", "args": ["tools"]}

WEATHER = {"transport": "stdio", "command": "/bin/echo", "args": ["weather"]}


def test_the_diff_reports_what_this_server_has_not_picked_up(
    served_api, no_config_file: None
) -> None:
    """One server lifetime, from an empty domain to a configured one.

    Everything written here is written after the server booted, so the
    diff is the only surface that says so, and the care point is what
    the case is about: everything stored is pending until a request
    applies it, and nothing is pending afterwards. The one kind that is
    never pending at all is the device binding, which the running server
    reads as a device asks for it.
    """
    with served_api() as api_url:
        client = httpx.Client(
            base_url=api_url, headers={"Authorization": f"Bearer {_token()}"}, timeout=30
        )
        try:
            # A fresh start serving an empty domain: nothing is pending,
            # and every kind still says where its changes converge.
            settled = client.get(DIFF).json()
            assert settled["providers"] == {
                "applies": "reload",
                "added": [],
                "removed": [],
                "changed": [],
            }
            assert settled["devices"] == {"applies": "check-in"}
            assert settled["default_agent"] == {"applies": "check-in"}

            for method, path, body in PIPELINE:
                assert client.request(method, path, json=body).status_code == 200, path

            configured = client.get(DIFF).json()
            assert configured["providers"]["added"] == [
                "asr.ears",
                "llm.mock",
                "tts.voice",
                "vad.gate",
            ]
            assert configured["agents"]["added"] == ["assistant"]
            assert configured["agent_defaults"]["changed"] is True
            # The device the pipeline bound is the claim this read must
            # not make: a binding is read as the device asks for it, so
            # it was in effect within seconds of the write and there is
            # nothing here to report.
            assert configured["devices"] == {"applies": "check-in"}
            assert configured["default_agent"] == {"applies": "check-in"}

            # The MCP half, which is the half a running server can apply.
            assert client.put("/mcp-servers/tools", json=TOOLS).status_code == 200
            assert client.put("/mcp-servers/weather", json=WEATHER).status_code == 200
            assert client.get(DIFF).json()["mcp_servers"] == {
                "applies": "reload",
                "added": ["tools", "weather"],
                "removed": [],
                "changed": [],
            }

            assert client.post(RELOAD).status_code == 200
            applied = client.get(DIFF).json()
            assert applied["mcp_servers"]["added"] == []
            # The provider entries went with them: their definitions are
            # what a reload rebuilds, so what was pending is served.
            assert applied["providers"]["added"] == []
            # And so did the last two kinds, which is what the final
            # milestone did: the agent this run wrote is one this server
            # can be asked for now, and the layer it inherits through is
            # the layer being served.
            assert applied["agents"]["added"] == []
            assert applied["agents"]["applies"] == "reload"
            assert applied["agent_defaults"] == {"applies": "reload", "changed": False}

            # One edit to what a connection is made of, and one to text
            # that no connection ever sees. Both are stored changes the
            # running server has not applied, and an answer that reported
            # only the first would hide a rewrite an operator is waiting
            # for.
            assert (
                client.put(
                    "/mcp-servers/tools", json=TOOLS | {"args": ["tools", "again"]}
                ).status_code
                == 200
            )
            assert (
                client.put(
                    "/mcp-servers/weather", json=WEATHER | {"instructions": "Ask first."}
                ).status_code
                == 200
            )
            assert client.get(DIFF).json()["mcp_servers"]["changed"] == [
                "tools",
                "weather",
            ]

            # Applied, and therefore no longer pending.
            assert client.post(RELOAD).status_code == 200
            assert client.get(DIFF).json()["mcp_servers"]["changed"] == []
        finally:
            client.close()
