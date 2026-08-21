"""The `/runtime` namespace: what the server is doing, not what is
stored.

The status read answers from the MCP registry the serving application
was handed, the prompt read from the assembly the composition root
closed over, and the diff read from the comparison it closed over, so
what is checked here is the transport around them: the gate, the shape,
the honest answer when there is no server, and the one structural reason
this namespace exists at all, that an entity may legally be named after
a word a route wants.
"""

import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.support.apps import entered_client
from tests.support.configs import world
from tests.support.problems import PROBLEM_KEYS, problem
from tests.support.providers import built_world
from vinga_server import app as app_module
from vinga_server.app import config_reloader
from vinga_server.config import Config
from vinga_server.config.api import (
    DIFF_MOVED_DESCRIPTION,
    DIFF_REFUSED_DESCRIPTION,
    MALFORMED_REQUEST_DESCRIPTION,
    MOUNT_PATH,
    NO_RUNTIME_DIFF,
    NO_RUNTIME_DIFF_DESCRIPTION,
    NO_RUNTIME_PROMPT_DESCRIPTION,
    NO_STORED_WORLD,
    PROBLEM_DESCRIPTIONS,
    PROBLEM_MEDIA_TYPE,
    RELOAD_HELD_DESCRIPTION,
    RELOAD_REFUSED_DESCRIPTION,
    build_api,
    document,
)
from vinga_server.config.boot import BootConfig
from vinga_server.config.loader import (
    ConfigError,
    DatabaseBusyError,
    ProviderRefusedError,
    ReloadInProgressError,
    RunningConfigMovedError,
    StorageError,
)
from vinga_server.config.models import MemoryConfig
from vinga_server.config.reload import PROVIDERS_REFUSED
from vinga_server.config.responses import (
    AgentsDiff,
    Applies,
    ConfigDiff,
    ConfigReloadResult,
    EntityDiff,
    FillerDiff,
    GrantsDiff,
    LiveKind,
    McpReloadResult,
    PromptDiff,
    PromptsReload,
    SingletonDiff,
)
from vinga_server.config.secrets import MASTER_KEY_ENV, generate_key
from vinga_server.config.store import ConfigStore
from vinga_server.db import open_database
from vinga_server.logs import JsonFormatter
from vinga_server.runtime import prompt
from vinga_server.runtime.prompt import Guidance
from vinga_server.tools.mcp import (
    CONNECTED,
    DOWN,
    UNUSED,
    McpServers,
)

TOKEN = "test-api-token-" + "0123456789abcdef" * 2

API_SECRET_ENV = "VINGA_API_SECRET"

STATUS_PATH = "/runtime/mcp-servers"

RELOAD_PATH = "/runtime/config/reload"

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
    config_diff: object = None,
    snapshot_only: bool = False,
) -> Iterator[TestClient]:
    api = build_api(
        TOKEN,
        directory,
        mcp_servers=servers,
        reload=reload,
        agent_prompt=agent_prompt,
        config_diff=config_diff,
        snapshot_only=snapshot_only,
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

    The entries name a command that does not exist, so everything
    referenced is down: what this shows is that the mounted API reports
    this server's own entries rather than the empty answer an application
    built without one gives. The lifespan is entered, because the
    managers the API reports are what it builds and connects (#142).
    """
    monkeypatch.setenv(API_SECRET_ENV, TOKEN)
    unreachable = entry_data(command="/nonexistent/mcp-server", args=[])
    config = config_with({"tools": unreachable, "shelved": unreachable}, ["tools"], directory)

    with entered_client(config) as served:
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
#
# What the stub answers is the whole reply, status included, because
# that is what the callable this application is handed answers: the
# outcomes and the status are taken together where the two phases live,
# so that no await can land between them, and this route adds nothing
# to what came back.


def outcome(
    servers: McpServers, prompts: Sequence[str] = (), **fields: object
) -> ConfigReloadResult:
    """One apply's whole answer, the way the composition root composes
    one: the MCP section whole, the prompts section beside it, and the
    sections no milestone fills yet answering null."""
    lists = ("started", "restarted", "stopped", "unchanged")
    return ConfigReloadResult(
        mcp=McpReloadResult(
            **{name: list(fields.get(name, ())) for name in lists},
            servers=servers.typed_status(),
        ),
        prompts=PromptsReload(changed=list(prompts)),
    )


def answering(applied: ConfigReloadResult):
    async def reload() -> ConfigReloadResult:
        return applied

    return reload


def refusing(exc: Exception):
    async def reload() -> ConfigReloadResult:
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
    assert set(response.json()) == PROBLEM_KEYS
    assert "no running server" in response.json()["detail"]


def test_half_a_runtime_refuses_to_reload_from_either_side(directory: Path) -> None:
    """The route refuses on "this application has a running server
    around it", and half a runtime is not one.

    Neither half alone is a composition this server builds today, which
    is exactly why the refusal has to be the endpoint's own behavior:
    what an application was handed is decided somewhere else and can
    change, and a state-changing route that applied a configuration to
    a registry nothing gave it would be answering 200 for a reload
    whose other half nobody can report on.

    The result is built here rather than through `outcome` because
    there is no registry to take a status from, which is the whole
    point of the composition.
    """
    orphan = ConfigReloadResult(
        mcp=McpReloadResult(
            started=[], restarted=[], stopped=[], unchanged=[], servers={}
        ),
        prompts=PromptsReload(changed=[]),
    )

    with serving(directory, None, answering(orphan)) as client:
        refused = client.post(RELOAD_PATH)
    assert refused.status_code == 503
    assert "no running server" in refused.json()["detail"]

    # And the other way round, which is what the guard said before the
    # composing moved out of this handler.
    servers = McpServers.build(config_with({"tools": entry_data()}, ["tools"]))
    with serving(directory, servers) as client:
        refused = client.post(RELOAD_PATH)
    assert refused.status_code == 503
    assert "no running server" in refused.json()["detail"]


def test_a_reload_answers_with_what_it_did_and_what_is_running(directory: Path) -> None:
    servers = McpServers.build(
        config_with({"tools": entry_data(), "shelved": entry_data()}, ["tools"])
    )
    applied = outcome(
        servers,
        prompts=("assistant",),
        started=("tools",),
        stopped=("gone",),
        unchanged=("shelved",),
    )

    with serving(directory, servers, answering(applied)) as client:
        response = client.post(RELOAD_PATH)

    assert response.status_code == 200
    answer = response.json()
    # One section per kind, and every one of them present: the schema is
    # published complete, so a kind this server cannot apply yet answers
    # null rather than arriving later and breaking a generated client.
    assert set(answer) == {"mcp", "prompts", "fillers", "providers", "agents"}
    assert answer["prompts"] == {"changed": ["assistant"]}
    assert (answer["fillers"], answer["providers"], answer["agents"]) == (None, None, None)
    assert set(answer["mcp"]) == {"started", "restarted", "stopped", "unchanged", "servers"}
    assert answer["mcp"]["started"] == ["tools"]
    assert answer["mcp"]["restarted"] == []
    assert answer["mcp"]["stopped"] == ["gone"]
    assert answer["mcp"]["unchanged"] == ["shelved"]
    # And the whole status document, exactly as the read beside it
    # answers: one round trip applies and verifies.
    with serving(directory, servers) as client:
        assert answer["mcp"]["servers"] == client.get(STATUS_PATH).json()


def test_the_mcp_section_is_what_the_retired_route_answered(directory: Path) -> None:
    """The one pin that says the MCP move was a move.

    The generalized reload has exactly three intentional transport
    deltas: the path, this nesting, and the fixed sentence a refused
    stored half now carries. Everything else is invariant, and the
    invariant worth writing down is this one: the serialized `mcp`
    section of a successful apply is byte for byte the body
    `POST /runtime/mcp-servers/reload` used to answer with, which is the
    four outcome lists and the status document beside them.
    """
    servers = McpServers.build(
        config_with({"tools": entry_data(), "shelved": entry_data()}, ["tools"])
    )
    former = McpReloadResult(
        started=["tools"],
        restarted=[],
        stopped=["gone"],
        unchanged=["shelved"],
        servers=servers.typed_status(),
    )
    applied = outcome(
        servers, started=("tools",), stopped=("gone",), unchanged=("shelved",)
    )

    with serving(directory, servers, answering(applied)) as client:
        response = client.post(RELOAD_PATH)

    assert response.json()["mcp"] == former.model_dump()


@pytest.mark.parametrize(
    ("refusal", "status"),
    [
        (ReloadInProgressError("already running"), 409),
        (DatabaseBusyError("the configuration database is busy"), 409),
        (ConfigError("the reload was refused and nothing was changed: mcp_servers.x"), 422),
        # The provider layer's refusal in the configuration's own
        # vocabulary: an ordinary 422 with a type of its own, which is
        # what lets its fixed sentence through the composition root's
        # rewrite rather than being replaced by the general one.
        (ProviderRefusedError(PROVIDERS_REFUSED), 422),
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
    assert response.json() == problem(status, str(refusal))


def test_a_read_that_fails_unexpectedly_answers_without_quoting_it(
    directory: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The refusals above are this application's own sentences, composed
    to be shown. The one below is not: the `read` callable opens a
    database, and what a driver raises when that goes wrong is nobody's
    to publish, a connection string being one of the things it
    plausibly holds. The real `reload` is driven rather than a stub,
    because the sanitizing is its and this asserts the whole path to the
    body a client reads."""
    sentinel = "postgres://user:hunter2@db.internal/vinga"
    servers = McpServers.build(config_with({"tools": entry_data()}, ["tools"]))

    def read() -> BootConfig:
        raise RuntimeError(f"could not connect using {sentinel}")

    running = config_with({}, [])
    reload = config_reloader(world(running, providers=built_world(running)), servers, read)

    with caplog.at_level("INFO"):
        with serving(directory, servers, reload) as client:
            response = client.post(RELOAD_PATH)

    assert response.status_code == 500
    assert response.json() == problem(500, app_module.RELOAD_UNREADABLE)
    assert sentinel not in response.text
    # And nothing of it in what the server kept about the refusal
    # either, in either shipped format.
    written = caplog.text + "".join(
        JsonFormatter().format(record) for record in caplog.records
    )
    assert sentinel not in written
    # The refusal was still recorded as one.
    assert [
        record.reason for record in caplog.records if getattr(record, "event", "") == "mcp_reload"
    ] == ["unexpected"]


def test_a_running_server_hands_its_own_reload_to_the_api(
    monkeypatch: pytest.MonkeyPatch, directory: Path
) -> None:
    """The wiring, through the mount a deployment gets: the route is
    served, and it is not the 503 an application without a server
    answers. The entry names a command that does not exist, so the reload
    applies an unchanged configuration to a registry of down
    managers."""
    monkeypatch.setenv(API_SECRET_ENV, TOKEN)
    unreachable = entry_data(command="/nonexistent/mcp-server", args=[])
    config = config_with({"tools": unreachable}, ["tools"], directory)
    seed(directory, config)

    with entered_client(config) as served:
        answered = served.post(
            f"{MOUNT_PATH}{RELOAD_PATH}", headers={"Authorization": f"Bearer {TOKEN}"}
        )

    assert answered.status_code == 200, answered.text
    assert answered.json()["mcp"]["unchanged"] == ["tools"]
    assert answered.json()["mcp"]["servers"]["tools"]["state"] == DOWN


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
    assert "exactly as it was" in described
    # And its 409, which the shared sentence is also not true of: one
    # apply at a time is this endpoint's own exclusion, and the
    # snapshot-mode refusal is the one 409 in this API that making the
    # request again will not clear.
    held = rendered[RELOAD_PATH]["post"]["responses"]["409"]["description"]
    assert held == RELOAD_HELD_DESCRIPTION
    assert held != PROBLEM_DESCRIPTIONS[409]
    # Per route rather than a global edit: the writes still describe
    # what a 422 means to them.
    assert rendered["/mcp-servers/{name}"]["put"]["responses"]["422"]["description"] == (
        PROBLEM_DESCRIPTIONS[422]
    )


def test_a_server_with_no_store_behind_it_refuses_to_reload(directory: Path) -> None:
    """The mode a test lane and an embedded caller run in: the
    configuration was handed to this server rather than read from a
    store, so the database beside it describes some other server. An
    apply would install that description as this server's world, which
    is why it refuses rather than doing it."""
    servers = McpServers.build(config_with({"tools": entry_data()}, ["tools"]))
    applied = outcome(servers)

    with serving(
        directory, servers, answering(applied), snapshot_only=True
    ) as client:
        response = client.post(RELOAD_PATH)

    assert response.status_code == 409
    assert response.json() == problem(409, NO_STORED_WORLD)
    # And nothing of the apply ran: the refusal is the route's, in front
    # of the callable it would otherwise have awaited.
    assert "will not help" in response.json()["detail"]


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
    assert set(response.json()) == PROBLEM_KEYS
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
        schema = responses[status]["content"][PROBLEM_MEDIA_TYPE]["schema"]
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


def test_the_server_shipped_blocks_are_counted_and_named_on_the_surface(
    directory: Path,
) -> None:
    """The bytes an entry opted into reach exactly two places, and this
    is the second: the surface exists to say what the model was given,
    and the provenance is what tells an operator whose words they are
    reading. The published prompt's configured name comes with it, since
    a read body is where operator-written configuration is echoed back."""
    shipped = "Call list_devices before anything else."
    published = "Answer in short sentences."
    assembled = prompt.know_how(
        "POET",
        guidance=[
            Guidance("home", "Ask first."),
            prompt.ServerInstructions("home", shipped),
            prompt.ServerPrompt("home", 1, "house_style", published),
        ],
    )

    with serving(directory, None, agent_prompt=previewing(assembled)) as client:
        body = client.get(PROMPT_PATH).json()

    assert [block["provenance"] for block in body["blocks"]] == [
        "persona",
        "instructions:home",
        "server_instructions:home",
        "server_prompt:home:1",
    ]
    assert [block["name"] for block in body["blocks"]] == [
        None,
        None,
        None,
        "house_style",
    ]
    assert shipped in body["blocks"][2]["text"]
    assert published in body["blocks"][3]["text"]
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

    with entered_client(config) as served:
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


# The stored-versus-running diff
#
# What this module owns, a third time: the gate, the shape of the answer,
# the status each refusal maps to, and the honest refusal when there is
# no server. What the comparison decides is `test_config_diff.py`'s and
# `test_mcp_pending.py`'s; what the composition root does with a database
# and a moving world is `test_config_diff_read.py`'s.

DIFF_PATH = "/runtime/config/diff"

NOTHING = {"added": (), "removed": (), "changed": ()}


def answer(
    providers: tuple[str, ...] = (),
    mcp_servers: tuple[str, ...] = (),
    grants: tuple[str, ...] = (),
    prompts: tuple[str, ...] = (),
    fillers: tuple[str, ...] = (),
) -> ConfigDiff:
    """One whole diff, the way the composition root composes one: every
    kind present with its own regime, and whatever this case is about
    filled in."""
    return ConfigDiff(
        providers=EntityDiff(
            applies=Applies.RESTART, added=providers, removed=(), changed=()
        ),
        mcp_servers=EntityDiff(
            applies=Applies.RELOAD, added=(), removed=(), changed=mcp_servers
        ),
        prompt_fragments=EntityDiff(applies=Applies.RELOAD, **NOTHING),
        agent_defaults=SingletonDiff(applies=Applies.RESTART, changed=False),
        agents=AgentsDiff(
            applies=Applies.RESTART,
            **NOTHING,
            grants=GrantsDiff(applies=Applies.RELOAD, changed=grants),
            prompt=PromptDiff(applies=Applies.RELOAD, changed=prompts),
            filler=FillerDiff(applies=Applies.RELOAD, changed=fillers),
        ),
        devices=LiveKind(applies=Applies.CHECK_IN),
        default_agent=LiveKind(applies=Applies.CHECK_IN),
    )


def comparing(composed: ConfigDiff):
    async def diff() -> ConfigDiff:
        return composed

    return diff


def refusing_diff(exc: Exception):
    async def diff() -> ConfigDiff:
        raise exc

    return diff


def test_the_diff_read_needs_the_bearer_token(directory: Path) -> None:
    with TestClient(build_api(TOKEN, directory)) as anonymous:
        assert anonymous.get(DIFF_PATH).status_code == 401
        wrong = anonymous.get(DIFF_PATH, headers={"Authorization": "Bearer wrong"})
        assert wrong.status_code == 401


def test_an_application_without_a_server_has_nothing_to_compare(
    client: TestClient,
) -> None:
    """Unlike the status read, there is no honest empty answer: an empty
    diff would say that everything stored is already in effect, which is
    a claim about a running server there is none of.

    The whole body, because the sentence is the point. The shared 503
    says the reads in this namespace answer emptily, which is what this
    route refuses to do, so it carries one of its own and the document's
    description of the same status says the same thing.
    """
    response = client.get(DIFF_PATH)

    assert response.status_code == 503
    assert response.json() == problem(503, NO_RUNTIME_DIFF)
    assert response.json()["detail"] != PROBLEM_DESCRIPTIONS[503]


def test_the_diff_answers_every_kind_with_its_own_regime(directory: Path) -> None:
    """The whole shape on the wire, since this is the contract a client
    generates against: seven kinds, each labelled, the two live ones
    carrying their label and nothing else, and the three halves an agent
    entry converges by beside the agents rather than inside their
    lists."""
    composed = answer(
        providers=("llm.local",),
        mcp_servers=("tools",),
        grants=("assistant",),
        prompts=("assistant",),
        fillers=("assistant",),
    )

    with serving(directory, None, config_diff=comparing(composed)) as client:
        response = client.get(DIFF_PATH)

    assert response.status_code == 200
    assert response.json() == {
        "providers": {
            "applies": "restart",
            "added": ["llm.local"],
            "removed": [],
            "changed": [],
        },
        "mcp_servers": {
            "applies": "reload",
            "added": [],
            "removed": [],
            "changed": ["tools"],
        },
        "prompt_fragments": {
            "applies": "reload",
            "added": [],
            "removed": [],
            "changed": [],
        },
        "agent_defaults": {"applies": "restart", "changed": False},
        "agents": {
            "applies": "restart",
            "added": [],
            "removed": [],
            "changed": [],
            "grants": {"applies": "reload", "changed": ["assistant"]},
            "prompt": {"applies": "reload", "changed": ["assistant"]},
            "filler": {"applies": "reload", "changed": ["assistant"]},
        },
        "devices": {"applies": "check-in"},
        "default_agent": {"applies": "check-in"},
    }


@pytest.mark.parametrize(
    ("refusal", "status"),
    [
        (RunningConfigMovedError("the running configuration changed"), 409),
        (DatabaseBusyError("the configuration database is busy"), 409),
        (ConfigError('agents.assistant.llm: unknown llm provider "ghost"'), 422),
        (StorageError("the stored configuration cannot be read"), 500),
    ],
)
def test_a_diff_refusal_maps_to_its_status_and_carries_its_own_sentence(
    directory: Path, refusal: Exception, status: int
) -> None:
    """The same typed refusals the reload answers, plus the one this read
    has of its own: a world that moved while it was being compared is
    retryable exactly as a contended write is."""
    with serving(directory, None, config_diff=refusing_diff(refusal)) as client:
        response = client.get(DIFF_PATH)

    assert response.status_code == status
    assert response.json() == problem(status, str(refusal))


def test_the_diff_read_describes_the_refusals_it_can_actually_answer() -> None:
    """Three of its refusals cannot inherit a shared sentence.

    Its 422 is not about addressing, since it addresses nothing and
    carries no body: it is the stored half being refused. Its 409 is not
    one of the three things the shared sentence lists, because a world
    that moved under a read is neither a held lock nor a reload asked
    for twice. And the shared 503 says the reads in this namespace
    answer emptily, which is the one thing this read must not do.
    """
    responses = document()["paths"][DIFF_PATH]["get"]["responses"]

    for status in ("401", "409", "422", "500", "503"):
        schema = responses[status]["content"][PROBLEM_MEDIA_TYPE]["schema"]
        assert schema == {"$ref": "#/components/schemas/Problem"}, status
    assert responses["409"]["description"] == DIFF_MOVED_DESCRIPTION
    assert responses["409"]["description"] != PROBLEM_DESCRIPTIONS[409]
    assert responses["422"]["description"] == DIFF_REFUSED_DESCRIPTION
    assert responses["422"]["description"] != PROBLEM_DESCRIPTIONS[422]
    assert responses["503"]["description"] == NO_RUNTIME_DIFF_DESCRIPTION
    assert responses["503"]["description"] != PROBLEM_DESCRIPTIONS[503]
    # The 500 is the shared one, and stays it: a failure that is not the
    # caller's means here what it means everywhere.
    assert responses["500"]["description"] == PROBLEM_DESCRIPTIONS[500]


def test_the_document_says_what_changed_means() -> None:
    """The one semantic an operator can be surprised by, in the contract
    rather than in a commit message: a credential set again to the same
    value reports its entity as changed, because what is compared is a
    mark over the ciphertext."""
    rendered = document()
    described = rendered["components"]["schemas"]["EntityDiff"]["properties"]
    changed = described["changed"]["description"]

    assert "differs from what this server is serving" in changed
    assert "never that something was written" in changed
    for prose in (rendered["info"]["description"], changed):
        assert "plaintext may not have" in prose
