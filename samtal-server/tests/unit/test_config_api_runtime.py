"""The `/runtime` namespace: what the server is doing, not what is
stored.

The status read answers from the MCP registry the serving application
was handed, and the prompt read from the assembly the composition root
closed over, so what is checked here is the transport around them: the
gate, the shape, the honest answer when there is no server, and the one
structural reason this namespace exists at all, that an entity may
legally be named after a word a route wants.
"""

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from samtal_server.app import create_app
from samtal_server.config import Config
from samtal_server.config.api import (
    MALFORMED_REQUEST_DESCRIPTION,
    MOUNT_PATH,
    NO_RUNTIME_PROMPT_DESCRIPTION,
    PROBLEM_DESCRIPTIONS,
    RELOAD_REFUSED_DESCRIPTION,
    build_api,
    document,
)
from samtal_server.config.loader import (
    ConfigError,
    DatabaseBusyError,
    ReloadInProgressError,
    StorageError,
)
from samtal_server.config.models import MemoryConfig
from samtal_server.config.secrets import MASTER_KEY_ENV, generate_key
from samtal_server.config.store import ConfigStore
from samtal_server.db import open_database
from samtal_server.runtime import prompt
from samtal_server.runtime.prompt import Guidance
from samtal_server.tools.mcp import CONNECTED, DOWN, UNUSED, McpReload, McpServers

TOKEN = "test-api-token-" + "0123456789abcdef" * 2

API_SECRET_ENV = "SAMTAL_API_SECRET"

STATUS_PATH = "/runtime/mcp-servers"

RELOAD_PATH = "/runtime/mcp-servers/reload"

STDIO_SERVER = Path(__file__).parents[1] / "support" / "mcp_stdio_server.py"


def entry_data(**overrides: object) -> dict[str, object]:
    return {
        "transport": "stdio",
        "command": sys.executable,
        "args": [str(STDIO_SERVER)],
    } | overrides


def config_with(
    servers: dict[str, object], granted: list[str], database: Path | None = None
) -> Config:
    return Config(
        server={} if database is None else {"database": {"dir": str(database)}},
        providers={
            stage: {"mock": {"type": "mock"}} for stage in ("llm", "asr", "tts", "vad")
        },
        mcp_servers=servers,
        agent_defaults=dict.fromkeys(("llm", "asr", "tts", "vad"), "mock"),
        agents={"assistant": {"prompt": "A", "mcp": granted}},
        default_agent="assistant",
    )


def seed(directory: Path, config: Config) -> None:
    """The configuration this test's server booted on, written into the
    database it would read again, so a reload that changes nothing has
    nothing to change."""
    engine = open_database(directory)
    try:
        store = ConfigStore(engine)
        for stage in ("llm", "asr", "tts", "vad"):
            for name, entry in getattr(config.providers, stage).items():
                store.set_provider(stage, name, entry.model_dump(exclude_unset=True))
        for name, entry in config.mcp_servers.items():
            store.set_mcp_server(name, entry.model_dump(exclude_unset=True))
        store.set_agent_defaults(config.agent_defaults.model_dump(exclude_unset=True))
        for name, agent in config.agents.items():
            store.set_agent(name, agent.model_dump(exclude_unset=True))
        store.set_default_agent(config.default_agent)
    finally:
        engine.dispose()


@pytest.fixture
def directory(tmp_path: Path) -> Path:
    return tmp_path / "db"


@pytest.fixture
def keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MASTER_KEY_ENV, generate_key())


@contextmanager
def serving(
    directory: Path,
    servers: McpServers | None,
    reload: object = None,
    agent_prompt: object = None,
) -> Iterator[TestClient]:
    api = build_api(
        TOKEN,
        directory,
        mcp_servers=servers,
        mcp_reload=reload,
        agent_prompt=agent_prompt,
    )
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


def test_a_running_server_hands_its_own_managers_to_the_api(
    monkeypatch: pytest.MonkeyPatch, directory: Path
) -> None:
    """The wiring, through the mount a deployment gets.

    No lifespan, so nothing is connected and everything referenced is
    down: what this shows is that the mounted API reports this server's
    own entries rather than the empty answer an application built
    without one gives.
    """
    monkeypatch.setenv(API_SECRET_ENV, TOKEN)
    config = config_with(
        {"tools": entry_data(), "shelved": entry_data()}, ["tools"], directory
    )
    served = TestClient(create_app(config))

    answered = served.get(
        f"{MOUNT_PATH}{STATUS_PATH}", headers={"Authorization": f"Bearer {TOKEN}"}
    )

    assert answered.status_code == 200
    assert answered.json()["tools"]["state"] == DOWN
    assert answered.json()["shelved"]["state"] == UNUSED


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


# The reload action
#
# The registry's own two phases are exercised against real servers in
# `test_tools_mcp_reload.py`; what is left here is what this module owns
# and nothing else: the gate, the shape of the answer, the status code
# each refusal maps to, and the honest refusal when there is no server.
#
# The reload callable is a stub for a reason beyond brevity. A TestClient
# drives the application on a portal loop of its own, so a registry whose
# managers were started on the test's loop would be stopped and started
# from another one, which is exactly what the managers' one-task rule
# forbids. The registry handed in here has never been started, so its
# status is a read of plain attributes.


def outcome(**fields: object) -> McpReload:
    return McpReload(**fields)


def answering(applied: McpReload):
    async def reload() -> McpReload:
        return applied

    return reload


def refusing(exc: Exception):
    async def reload() -> McpReload:
        raise exc

    return reload


def test_the_reload_needs_the_bearer_token(directory: Path) -> None:
    with TestClient(build_api(TOKEN, directory)) as anonymous:
        assert anonymous.post(RELOAD_PATH).status_code == 401
        wrong = anonymous.post(RELOAD_PATH, headers={"Authorization": "Bearer wrong"})
        assert wrong.status_code == 401


def test_an_application_without_a_server_refuses_to_reload(client: TestClient) -> None:
    """Unlike the read beside it, there is no honest empty answer: an
    application with no runtime cannot apply anything, and answering 200
    would say it had."""
    response = client.post(RELOAD_PATH)

    assert response.status_code == 503
    assert set(response.json()) == {"detail"}
    assert "no running server" in response.json()["detail"]


def test_a_reload_answers_with_what_it_did_and_what_is_running(directory: Path) -> None:
    servers = McpServers.build(
        config_with({"tools": entry_data(), "shelved": entry_data()}, ["tools"])
    )
    applied = outcome(started=("tools",), stopped=("gone",), unchanged=("shelved",))

    with serving(directory, servers, answering(applied)) as client:
        response = client.post(RELOAD_PATH)

    assert response.status_code == 200
    answer = response.json()
    assert set(answer) == {"started", "restarted", "stopped", "unchanged", "servers"}
    assert answer["started"] == ["tools"]
    assert answer["restarted"] == []
    assert answer["stopped"] == ["gone"]
    assert answer["unchanged"] == ["shelved"]
    # And the whole status document, exactly as the read beside it
    # answers: one round trip applies and verifies.
    with serving(directory, servers) as client:
        assert answer["servers"] == client.get(STATUS_PATH).json()


@pytest.mark.parametrize(
    ("refusal", "status"),
    [
        (ReloadInProgressError("already running"), 409),
        (DatabaseBusyError("the configuration database is busy"), 409),
        (ConfigError("the reload was refused and nothing was changed: mcp_servers.x"), 422),
        (StorageError("the stored configuration cannot be read"), 500),
    ],
)
def test_a_refusal_maps_to_its_status_and_carries_its_own_sentence(
    directory: Path, refusal: Exception, status: int
) -> None:
    servers = McpServers.build(config_with({"tools": entry_data()}, ["tools"]))

    with serving(directory, servers, refusing(refusal)) as client:
        response = client.post(RELOAD_PATH)

    assert response.status_code == status
    assert response.json() == {"detail": str(refusal)}


def test_a_running_server_hands_its_own_reload_to_the_api(
    monkeypatch: pytest.MonkeyPatch, directory: Path
) -> None:
    """The wiring, through the mount a deployment gets: the route is
    served, and it is not the 503 an application without a server
    answers. No lifespan, so nothing is connected and the reload applies
    an unchanged configuration to a registry of down managers."""
    monkeypatch.setenv(API_SECRET_ENV, TOKEN)
    config = config_with({"tools": entry_data()}, ["tools"], directory)
    seed(directory, config)
    served = TestClient(create_app(config))

    answered = served.post(
        f"{MOUNT_PATH}{RELOAD_PATH}", headers={"Authorization": f"Bearer {TOKEN}"}
    )

    assert answered.status_code == 200, answered.text
    assert answered.json()["unchanged"] == ["tools"]
    assert answered.json()["servers"]["tools"]["state"] == DOWN


def test_the_reload_says_that_unchanged_is_about_the_connection() -> None:
    """An instructions-only edit is reported `unchanged` on purpose, so
    the word has to mean the connection and not the entry. A contract
    that read it as "nothing about this entry moved" would have a
    client, or an operator, believe a rewrite had not been applied.

    And the two halves of an entry reach a conversation at different
    moments, which is the other thing this document has to say: tools
    and grants on the next utterance, because they are snapshotted per
    reply, and guidance at the next activation, because prompt text is
    assembled there and cached.
    """
    rendered = document()
    described = rendered["components"]["schemas"]["McpReloadResult"]["properties"]
    unchanged = described["unchanged"]["description"]

    assert "kept the connection" in unchanged
    assert "instructions" in unchanged
    assert "next activation" in unchanged
    assert "nothing changed about" not in unchanged

    for prose in (
        rendered["info"]["description"],
        rendered["paths"][RELOAD_PATH]["post"]["description"],
    ):
        assert "next utterance" in prose
        assert "next activation" in prose


def test_the_reload_describes_a_422_of_its_own() -> None:
    """The shared sentence for 422 is about addressing: a stage that is
    not a stage, a MAC that is not one. This endpoint addresses nothing
    and carries no body, so the only thing its 422 can mean is that the
    stored configuration was refused, which is also where the endpoint's
    guarantee belongs."""
    rendered = document()["paths"]

    described = rendered[RELOAD_PATH]["post"]["responses"]["422"]["description"]
    assert described == RELOAD_REFUSED_DESCRIPTION
    assert described != PROBLEM_DESCRIPTIONS[422]
    assert "exactly as they were" in described
    # Per route rather than a global edit: the writes still describe
    # what a 422 means to them.
    assert rendered["/mcp-servers/{name}"]["put"]["responses"]["422"]["description"] == (
        PROBLEM_DESCRIPTIONS[422]
    )


# The assembled-prompt read
#
# What this module owns, again: the gate, the shape of the answer, the
# status code each refusal maps to, and the honest one when there is no
# server. What the blocks hold is the assembler's, and the wiring that
# hands a real one to a mounted application is checked at the end.

PROMPT_PATH = "/runtime/agents/assistant/prompt"

# The same route as the document names it, since a path parameter is a
# template there and an agent name here.
PROMPT_TEMPLATE = "/runtime/agents/{name}/prompt"


def previewing(assembled: object):
    async def assemble(agent: str) -> object:
        return assembled if agent == "assistant" else None

    return assemble


def test_the_prompt_read_needs_the_bearer_token(directory: Path) -> None:
    with TestClient(build_api(TOKEN, directory)) as anonymous:
        assert anonymous.get(PROMPT_PATH).status_code == 401
        wrong = anonymous.get(PROMPT_PATH, headers={"Authorization": "Bearer wrong"})
        assert wrong.status_code == 401


def test_an_application_without_a_server_has_no_prompt_to_assemble(
    client: TestClient,
) -> None:
    """Unlike the status read, there is no honest empty answer: an
    application with no runtime cannot say what a session would be
    sent, and an empty block list would say it had."""
    response = client.post(RELOAD_PATH)
    assert response.status_code == 503

    response = client.get(PROMPT_PATH)

    assert response.status_code == 503
    assert set(response.json()) == {"detail"}
    assert "no running server" in response.json()["detail"]


def test_an_agent_this_server_did_not_load_is_a_404_naming_the_restart(
    directory: Path,
) -> None:
    with serving(directory, None, agent_prompt=previewing(prompt.know_how("P"))) as client:
        response = client.get("/runtime/agents/stranger/prompt")

    assert response.status_code == 404
    detail = response.json()["detail"]
    assert "restart" in detail
    # The name arrived in the path and is not quoted back.
    assert "stranger" not in detail


def test_the_blocks_and_the_total_are_the_assemblers_own(directory: Path) -> None:
    assembled = prompt.with_memory(
        prompt.know_how("POET", guidance=[Guidance("home", "Ask first.")]), "- a fact"
    )

    with serving(directory, None, agent_prompt=previewing(assembled)) as client:
        answered = client.get(PROMPT_PATH)

    assert answered.status_code == 200
    body = answered.json()
    assert set(body) == {"blocks", "characters"}
    assert [block["provenance"] for block in body["blocks"]] == [
        "persona",
        "instructions:home",
        "memory",
    ]
    assert [block["characters"] for block in body["blocks"]] == [
        block.characters for block in assembled.blocks
    ]
    assert [block["text"] for block in body["blocks"]] == [
        block.text for block in assembled.blocks
    ]
    # The total is the whole prompt, and the whole prompt is the blocks
    # joined: a byte counted here is a byte the model receives.
    assert body["characters"] == assembled.characters
    assert body["characters"] == len(
        "\n\n".join(block["text"] for block in body["blocks"])
    )
    assert body["characters"] > sum(block["characters"] for block in body["blocks"])


def test_the_prompt_read_describes_the_refusals_it_can_actually_answer() -> None:
    """Two of its refusals cannot inherit a shared sentence.

    The 422 is the framework's own, and FastAPI's default body for it
    lists the input it rejected, which this API replaces globally with
    the sanitized `Problem`. A document that advertised the other shape
    would have a client reading a field this server never sends.

    And the shared 503 says the reads in this namespace answer emptily,
    which is true of the status read next door and false here: an empty
    block list would say a session is sent nothing.
    """
    responses = document()["paths"][PROMPT_TEMPLATE]["get"]["responses"]

    for status in ("401", "404", "422", "503"):
        schema = responses[status]["content"]["application/json"]["schema"]
        assert schema == {"$ref": "#/components/schemas/Problem"}, status
    assert responses["422"]["description"] == MALFORMED_REQUEST_DESCRIPTION
    assert responses["422"]["description"] != PROBLEM_DESCRIPTIONS[422]
    assert responses["503"]["description"] == NO_RUNTIME_PROMPT_DESCRIPTION
    assert responses["503"]["description"] != PROBLEM_DESCRIPTIONS[503]
    assert "no honest empty answer" in responses["503"]["description"]
    # Per route rather than a global edit: the reload, which is an
    # action, keeps the shared sentence about actions.
    reload_503 = document()["paths"][RELOAD_PATH]["post"]["responses"]["503"]
    assert reload_503["description"] == PROBLEM_DESCRIPTIONS[503]


def test_a_fragment_is_counted_on_the_surface_under_its_own_provenance(
    directory: Path,
) -> None:
    """Every injected block is reported by the surface that counts it,
    which is what makes the fragment section's cost visible rather than
    only its existence."""
    written = "The bins go out on Tuesday."
    assembled = prompt.with_memory(
        prompt.know_how(
            "POET",
            [prompt.Fragment("household", written)],
            [Guidance("home", "Ask first.")],
        ),
        "- a fact",
    )

    with serving(directory, None, agent_prompt=previewing(assembled)) as client:
        body = client.get(PROMPT_PATH).json()

    assert [block["provenance"] for block in body["blocks"]] == [
        "persona",
        "fragment:household",
        "instructions:home",
        "memory",
    ]
    fragment = body["blocks"][1]
    assert fragment["text"] == written
    assert fragment["characters"] == len(written)
    # And the fragment is a block like the others: the prompt is what
    # they join to, so its bytes are counted once and reported once.
    assert body["characters"] == len(
        "\n\n".join(block["text"] for block in body["blocks"])
    )


def test_a_running_server_hands_its_own_assembly_to_the_api(
    monkeypatch: pytest.MonkeyPatch, directory: Path, tmp_path: Path
) -> None:
    """The wiring, through the mount a deployment gets: the loaded
    agent, the running slice and the memory store, none of which the API
    application knows anything about."""
    monkeypatch.setenv(API_SECRET_ENV, TOKEN)
    config = config_with({"tools": entry_data(instructions="Ask first.")}, ["tools"], directory)
    config = config.model_copy(update={"memory": MemoryConfig(dir=tmp_path / "memory")})
    served = TestClient(create_app(config))

    answered = served.get(
        f"{MOUNT_PATH}{PROMPT_PATH}", headers={"Authorization": f"Bearer {TOKEN}"}
    )

    assert answered.status_code == 200, answered.text
    body = answered.json()
    assert [block["provenance"] for block in body["blocks"]] == [
        "persona",
        "instructions:tools",
    ]
    assert body["blocks"][0]["text"] == "A"
    assert "Ask first." in body["blocks"][1]["text"]
    assert body["characters"] == len(
        "\n\n".join(block["text"] for block in body["blocks"])
    )
    # And an agent nothing loaded is the 404, through the same mount.
    missing = served.get(
        f"{MOUNT_PATH}/runtime/agents/stranger/prompt",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert missing.status_code == 404
