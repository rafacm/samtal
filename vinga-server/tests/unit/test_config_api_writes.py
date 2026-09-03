"""The write routes, over real HTTP.

Every write the CLI can make is reachable here, answering with what it
did and when it applies, and refusing with the repository's own
sentences under the status codes the plan fixes. The repository decides
everything; what is checked here is that the transport carries the
decision faithfully and adds nothing.

The other property is that nothing leaks, and a write is where that is
hardest: the body is the one place a credential legitimately travels,
and a rejected body is exactly the one a mistake put a credential into.
So every malformed-body path is driven with a sentinel value, and the
sentinel is looked for in the response, in its headers and in every
captured log record.
"""

import logging
from collections.abc import Iterator
from urllib.parse import quote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.support.notices import CHECK_IN, RELOAD, boundaries
from tests.support.problems import PROBLEM_KEYS, refused
from tests.support.stores import holding_the_write_lock, the_lock_held
from vinga_server.config.api import APPLY_BODY_LIMIT, build_api
from vinga_server.config.entities import (
    APPLY_NOTICE,
    BINDING_UNSERVED_NOTICE,
    PROGRAM,
)
from vinga_server.config.models import NOT_A_MAC, PROVIDER_STAGES, DatabaseConfig
from vinga_server.config.secrets import (
    MASTER_KEY_ENV,
    SecretLocation,
    generate_key,
    load_keys,
)
from vinga_server.config.store import ConfigStore
from vinga_server.db import open_database

TOKEN = "test-api-token-" + "0123456789abcdef" * 2

# Not real credentials, and shaped so a substring check for one cannot
# match by accident.
SECRET = "sk-test-4f8b2c9e-never-a-real-credential"
OTHER_SECRET = "tok-test-7a1d3f60-never-a-real-credential"

# A pasted credential holding none of the fragments that make an option
# name secret-shaped, so it is refused as a slot rather than accepted as
# one. The two above are not usable for that: both carry the word
# "credential", which is one of those fragments.
PASTED = "sk-live-3f9a1c7e-never-a-real-value"

# Names that only survive a URL path percent-encoded. Legal repository
# identities, all three.
AWKWARD = ["a name with spaces", "100%-sure", "agente-café"]


@pytest.fixture
def database() -> DatabaseConfig:
    """The database this lane provisioned, which is where the store
    below writes and the application below reads."""
    return DatabaseConfig()


@pytest.fixture
def keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """The master key, in the environment before anything opens the
    database.

    Requested by the store and by the application alike, because since
    #142 the API derives its keys once, when its lifespan opens the
    engine, rather than on every request: a key exported after that has
    happened is a key the API does not have.
    """
    monkeypatch.setenv(MASTER_KEY_ENV, generate_key())


@pytest.fixture
def store(database: DatabaseConfig, keys: None) -> Iterator[ConfigStore]:
    """A second view of the same database, for reading back what a
    request wrote. A second engine on the same file, so what a request
    committed is what this finds."""
    engine = open_database(database)
    try:
        yield ConfigStore(engine, load_keys())
    finally:
        engine.dispose()


@pytest.fixture
def api(database: DatabaseConfig, keys: None) -> FastAPI:
    return build_api(TOKEN, database)


@pytest.fixture
def client(api: FastAPI) -> Iterator[TestClient]:
    """Entered, and not merely constructed: the API's engine is opened by
    the lifespan a `TestClient` runs only as a context manager (#142)."""
    with TestClient(api, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        yield client


def _pipeline(client: TestClient) -> None:
    """A working configuration, written the way a first deployment
    writes one: providers, then the agent defaults, then the agent, then
    what names it."""
    client.put("/providers/llm/claude", json={"type": "anthropic", "model": "m"})
    client.put("/providers/asr/whisper", json={"type": "mock"})
    client.put(
        "/mcp-servers/weather",
        json={"transport": "streamable_http", "url": "https://example.invalid/mcp"},
    )
    client.put("/agent-defaults", json={"llm": "claude", "asr": "whisper"})
    client.put("/agents/sam", json={"prompt": "You are Sam."})


# What a write answers


WRITES = [
    ("put", "/providers/llm/claude", {"type": "anthropic", "model": "m"}),
    ("put", "/mcp-servers/home", {"transport": "stdio", "command": "uvx"}),
    ("put", "/prompt-fragments/household", {"text": "The bins go out on Tuesday."}),
    ("put", "/agents/sam", {"prompt": "You are Sam."}),
    ("put", "/agent-defaults", {}),
    ("put", "/devices/aa:bb:cc:dd:ee:ff", {"agents": ["sam"]}),
    ("put", "/default-agent", {"name": "sam"}),
    ("delete", "/default-agent", None),
]


@pytest.mark.parametrize(("method", "path", "body"), WRITES)
def test_every_write_is_gated(
    client: TestClient, method: str, path: str, body: object
) -> None:
    """The gate is in front of routing, so a write is no more reachable
    without the token than a read is."""
    response = client.request(
        method.upper(), path, json=body, headers={"Authorization": "Bearer wrong"}
    )

    assert response.status_code == 401


@pytest.mark.parametrize(("method", "path", "body"), WRITES)
def test_a_write_says_what_it_did_and_when_it_applies(
    client: TestClient, method: str, path: str, body: object
) -> None:
    """A write is stored where the server reads it later, so one that
    answered only "ok" would leave the one operational trap of that
    design open.

    This application can serve no agent at all, which is what an API
    with no server around it honestly has, so every binding here names
    an agent this server is not serving and carries the sentence that
    says which reload would install it. What the notices depend on is
    asserted below, against an application told what its server
    serves."""
    _pipeline(client)

    response = client.request(method.upper(), path, json=body)

    assert response.status_code == 200
    answer = response.json()
    assert set(answer) == {"wrote", "notice"}
    assert isinstance(answer["wrote"], str) and answer["wrote"]
    assert boundaries(answer["notice"]) == _expected_boundaries(method, path)


def _expected_boundaries(method: str, path: str) -> frozenset[str]:
    """Which boundaries a write's notice names, by what it wrote.

    Two for the kinds this API writes: the device bindings and the
    default agent are what the running server re-reads as a device asks,
    and every other kind is one apply's business. A binding to an agent
    this application cannot serve names both, since the row is live and
    the agent it names is not. A start is on no kind at all, which is
    the whole of what an earlier milestone did.
    """
    if method == "delete" and path.startswith(("/devices/", "/default-agent")):
        return frozenset({CHECK_IN})
    if path.startswith(("/devices/", "/default-agent")):
        return frozenset({CHECK_IN, RELOAD})
    return frozenset({RELOAD})


# Which of the two notices a write carries
#
# The split is the whole operational difference this makes: a binding is
# read by the running server, everything else waits for a restart, and
# the acknowledgement is where an operator finds out which they just
# did.


@pytest.fixture
def serving_client(database: DatabaseConfig) -> Iterator[TestClient]:
    """A client of an API told which agents its server loaded, which is
    what the server passes at build."""
    api = build_api(TOKEN, database, lambda: frozenset({"sam"}))
    with TestClient(api, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        yield client


def test_a_binding_to_a_loaded_agent_needs_no_restart(serving_client: TestClient) -> None:
    _pipeline(serving_client)

    answer = serving_client.put("/devices/aa:bb:cc:dd:ee:ff", json={"agents": ["sam"]})

    assert boundaries(answer.json()["notice"]) == {CHECK_IN}


def test_a_binding_to_an_agent_the_server_has_not_loaded_names_the_restart(
    serving_client: TestClient,
) -> None:
    """The write lands, and the device cannot reach it until this server
    installs that agent, which is what the reload does."""
    _pipeline(serving_client)
    serving_client.put("/agents/poet", json={"prompt": "You are a poet."})

    answer = serving_client.put("/devices/aa:bb:cc:dd:ee:ff", json={"agents": ["poet"]})

    assert answer.status_code == 200
    # Both boundaries at once, which is the whole of why this is not the
    # sentence a binding to a served agent carries: the row is live at
    # the next check-in, and the agent it names arrives with the reload.
    assert boundaries(answer.json()["notice"]) == {CHECK_IN, RELOAD}


def test_the_default_agent_follows_the_same_rule(serving_client: TestClient) -> None:
    _pipeline(serving_client)
    serving_client.put("/agents/poet", json={"prompt": "You are a poet."})

    served = serving_client.put("/default-agent", json={"name": "sam"})
    unserved = serving_client.put("/default-agent", json={"name": "poet"})

    assert boundaries(served.json()["notice"]) == {CHECK_IN}
    assert boundaries(unserved.json()["notice"]) == {CHECK_IN, RELOAD}


def test_removing_a_binding_is_always_live(serving_client: TestClient) -> None:
    """Nothing has to be loaded for a device to stop being served, so
    neither delete has a case where it waits for a restart."""
    _pipeline(serving_client)
    serving_client.put("/devices/aa:bb:cc:dd:ee:ff", json={"agents": ["sam"]})

    unbound = serving_client.delete("/devices/aa:bb:cc:dd:ee:ff")
    cleared = serving_client.delete("/default-agent")

    assert boundaries(unbound.json()["notice"]) == {CHECK_IN}
    assert boundaries(cleared.json()["notice"]) == {CHECK_IN}


def test_the_notice_is_about_the_row_and_not_about_the_request(
    serving_client: TestClient, store: ConfigStore
) -> None:
    """A write normalizes the MAC and strips the names, so the request's
    spelling and the row's are different strings. Both halves of the
    acknowledgement are about the row: a name sent with spaces around it
    binds the loaded agent, and saying "restart" there would send an
    operator to restart a server that is already serving it."""
    _pipeline(serving_client)

    answer = serving_client.put("/devices/AA-BB-CC-DD-EE-FF", json={"agents": ["  sam  "]})

    assert answer.status_code == 200
    answer_body = answer.json()
    assert answer_body["wrote"] == "device aa:bb:cc:dd:ee:ff bound to sam"
    assert boundaries(answer_body["notice"]) == {CHECK_IN}
    # And the row really does hold the stripped name, which is what
    # makes the notice the true one.
    assert store.read_device("aa:bb:cc:dd:ee:ff").entry == ["sam"]


def test_the_default_agent_notice_is_about_the_row_too(
    serving_client: TestClient, store: ConfigStore
) -> None:
    _pipeline(serving_client)

    answer = serving_client.put("/default-agent", json={"name": " sam "})

    assert answer.status_code == 200
    assert answer.json()["wrote"] == "default agent sam"
    assert boundaries(answer.json()["notice"]) == {CHECK_IN}
    assert store.read_default_agent() == "sam"


def test_an_agent_write_carries_the_one_apply_sentence(
    serving_client: TestClient,
) -> None:
    """An agent entry used to fall on both sides of the line and carried
    a sentence of its own that said which fields were which. An apply
    installs the whole entry now, so what it says is the one thing every
    kind of this half says.
    """
    _pipeline(serving_client)

    answer = serving_client.put("/agents/sam", json={"prompt": "You are Sam still."})

    body = answer.json()
    assert body["wrote"] == "agent sam"
    assert boundaries(body["notice"]) == {RELOAD}
    assert body["notice"] == APPLY_NOTICE


# The shape of the sentence, and not only the boundary in it
#
# `boundaries()` answers in tokens, which is what keeps a suite from
# going red over an edit that changed no boundary. The other side of
# that is that it passes ANY sentence containing the announcing phrase,
# so the two decisions #371 made about this sentence need assertions of
# their own: it is one line, because it is printed once per entry of
# every domain-half write, and it names the read that says what is
# waiting as well as the command that installs it.


def test_the_per_write_notice_is_one_line_naming_both_commands() -> None:
    """The shortening, pinned as behavior rather than as prose."""
    assert "\n" not in APPLY_NOTICE
    assert f"{PROGRAM} apply" in APPLY_NOTICE
    assert f"{PROGRAM} diff" in APPLY_NOTICE


def test_the_unserved_binding_notice_names_the_command_that_installs() -> None:
    """The one sentence that names two boundaries at once, held to
    naming the command that crosses the second of them."""
    assert f"{PROGRAM} apply" in BINDING_UNSERVED_NOTICE


def test_the_three_clocks_are_not_in_a_per_write_sentence() -> None:
    """Where they went is `vinga apply --help` and the domain-config
    reference, which is where somebody asking when a change lands is
    already looking. Per write they were noise: the Quick Start run that
    prompted #371 printed them six times.
    """
    for clock in ("next activation", "next utterance", "next conversation"):
        assert clock not in APPLY_NOTICE, clock


# The third sentence: what a reload applies
#
# Every mutation of an MCP entry and of a provider entry, their stored
# secrets included, since a credential is read as the thing that uses it
# is made and a reload makes both again; and every mutation of a prompt
# fragment, whose text a reload puts in front of the next activation of
# every agent that includes it. What is left is the two kinds a reload
# deliberately does not move, below, and the agent, whose fields fall on
# both sides of the line.


MCP_MUTATIONS = [
    ("put", "/mcp-servers/home", {"transport": "stdio", "command": "uvx"}),
    ("put", "/mcp-servers/weather/secrets/headers.Authorization", {"secret": "a-token"}),
    ("delete", "/mcp-servers/weather/secrets/headers.Authorization", None),
    ("delete", "/mcp-servers/weather", None),
    ("put", "/prompt-fragments/house", {"text": "The house is quiet."}),
    ("delete", "/prompt-fragments/house", None),
    ("put", "/providers/llm/claude", {"type": "anthropic", "model": "m"}),
    ("put", "/providers/llm/claude/secrets/api_key", {"secret": "a-key"}),
]


@pytest.mark.usefixtures("store")
@pytest.mark.parametrize(("method", "path", "body"), MCP_MUTATIONS)
def test_every_mutation_a_reload_applies_names_the_reload(
    serving_client: TestClient, method: str, path: str, body: object
) -> None:
    _pipeline(serving_client)
    # In order, so the clear and the delete have something to address.
    if method == "delete":
        serving_client.put(
            "/mcp-servers/weather/secrets/headers.Authorization", json={"secret": "a-token"}
        )
        serving_client.put("/prompt-fragments/house", json={"text": "The house is quiet."})

    answer = serving_client.request(method.upper(), path, json=body)

    assert answer.status_code == 200, answer.text
    assert boundaries(answer.json()["notice"]) == {RELOAD}


LAST_MUTATIONS = [
    ("put", "/agent-defaults", {"llm": "claude", "asr": "whisper"}),
    ("put", "/agents/sam", {"prompt": "You are Sam still."}),
]


@pytest.mark.usefixtures("store")
@pytest.mark.parametrize(("method", "path", "body"), LAST_MUTATIONS)
def test_the_last_two_kinds_name_the_reload_as_well(
    serving_client: TestClient, method: str, path: str, body: object
) -> None:
    """`agent_defaults` is what the effective values every agent
    inherits are read through, and the agent set is what a server can be
    asked for. Both were the reason a candidate generation used to keep
    the previous world's copy, and both are one apply's business now, so
    the boundary they name is the one every other kind names and no kind
    names a start."""
    _pipeline(serving_client)

    answer = serving_client.request(method.upper(), path, json=body)

    assert answer.status_code == 200, answer.text
    assert boundaries(answer.json()["notice"]) == {RELOAD}


def test_an_empty_database_becomes_a_working_configuration(
    client: TestClient, store: ConfigStore
) -> None:
    """The API-era first start, executed: nothing configured is a valid
    state to write into, and every intermediate state here would fail the
    boot-only completeness rule without any of the writes being refused."""
    _pipeline(client)
    assert client.put("/devices/AA-BB-CC-DD-EE-FF", json={"agents": ["sam"]}).json()[
        "wrote"
    ] == "device aa:bb:cc:dd:ee:ff bound to sam"
    assert client.put("/default-agent", json={"name": "sam"}).status_code == 200

    domain = store.load().domain
    assert domain.providers.llm["claude"].type == "anthropic"
    assert domain.mcp_servers["weather"].transport == "streamable_http"
    assert domain.agent_defaults.llm == "claude"
    assert domain.agents["sam"].prompt == "You are Sam."
    assert domain.devices == {"aa:bb:cc:dd:ee:ff": ["sam"]}
    assert domain.default_agent == "sam"


@pytest.mark.parametrize("path", ["/agents/sam", "/agent-defaults"])
def test_an_mcp_list_reads_back_in_the_form_it_was_written(
    client: TestClient, path: str
) -> None:
    """The envelope contract, on the list the grant model widened: a
    read is the fragment a write of it takes. A plain name stays a plain
    name and an object stays `{server, tools}`, so an operator can PUT
    back what a GET answered without the shape drifting under them."""
    _pipeline(client)
    client.put("/mcp-servers/home", json={"transport": "stdio", "command": "uvx"})
    written = ["weather", {"server": "home", "tools": ["turn_on_light"]}]
    body = {"mcp": written} if path == "/agent-defaults" else {"prompt": "S", "mcp": written}

    assert client.put(path, json=body).status_code == 200

    assert client.get(path).json()["entity"]["mcp"] == written


# Every way an object-form grant can be malformed, each carrying the
# sentinel where the mistake is. A grant is a fragment like any other,
# so the body that was refused is a body a pasted credential can be in.
MALFORMED_GRANTS = [
    {"server": "weather", "tools": []},
    # The server itself, on a grant that is refused before any reference
    # check reads it: the name of the entry a fragment asks for is still
    # the caller's bytes until the repository has resolved it.
    {"server": SECRET, "tools": []},
    {"server": "weather", "tools": [SECRET, SECRET]},
    {"server": "weather", "tools": ["  "], "note": SECRET},
    {"server": "weather", SECRET: "yes"},
    {"server": "weather", "tools": [{"pasted": SECRET}]},
    {"tools": [SECRET]},
]


@pytest.mark.parametrize("grant", MALFORMED_GRANTS)
def test_a_malformed_grant_is_refused_without_quoting_it(
    client: TestClient, caplog: pytest.LogCaptureFixture, grant: dict
) -> None:
    """The grant refusals are location-and-rule only. They travel out of
    the repository as this 422 body and as a printed CLI line, so a
    credential pasted into a grant must reach neither, and neither must
    a key the caller invented."""
    _pipeline(client)

    with caplog.at_level(logging.DEBUG):
        response = client.put("/agents/sam", json={"prompt": "S", "mcp": [grant]})

    assert response.status_code == 422
    detail = response.json()["detail"]
    # Still actionable: which entry of the list, and which rule.
    assert "entry 1" in detail
    assert SECRET not in response.text
    assert SECRET not in str(response.headers)
    assert SECRET not in caplog.text


def test_a_server_repeated_in_a_grant_list_is_refused_without_quoting_it(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    # The other list-level refusal, whose subject is a name rather than
    # a shape, so what it must not say is that name.
    _pipeline(client)

    with caplog.at_level(logging.DEBUG):
        response = client.put(
            "/agents/sam",
            json={"prompt": "S", "mcp": [SECRET, {"server": SECRET, "tools": ["a"]}]},
        )

    assert response.status_code == 422
    assert "more than one position (1, 2)" in response.json()["detail"]
    assert SECRET not in response.text
    assert SECRET not in caplog.text


def test_a_grant_naming_an_unknown_server_is_refused_over_http(client: TestClient) -> None:
    _pipeline(client)

    response = client.put(
        "/agents/sam", json={"prompt": "S", "mcp": [{"server": "ghost", "tools": ["a"]}]}
    )

    assert response.status_code == 422
    assert "names no MCP server that exists" in response.json()["detail"]


def test_a_put_replaces_an_entity_and_keeps_its_stored_secret(
    client: TestClient, store: ConfigStore
) -> None:
    """The repository's rule, reachable over HTTP: a fragment cannot
    carry ciphertext, so a whole-row replacement would erase a stored
    secret on an ordinary edit."""
    client.put("/providers/llm/claude", json={"type": "anthropic", "model": "m"})
    client.put("/providers/llm/claude/secrets/api_key", json={"secret": SECRET})

    client.put("/providers/llm/claude", json={"type": "anthropic", "model": "n"})

    assert client.get("/providers/llm/claude").json()["entity"]["model"] == "n"
    assert store.load().secrets.secret(
        SecretLocation.provider("llm", "claude", "api_key")
    ) == SECRET


def test_a_delete_takes_the_stored_secrets_with_it(
    client: TestClient, store: ConfigStore
) -> None:
    client.put("/providers/llm/claude", json={"type": "anthropic", "model": "m"})
    client.put("/providers/llm/claude/secrets/api_key", json={"secret": SECRET})

    response = client.delete("/providers/llm/claude")

    assert response.json()["wrote"] == "provider llm.claude deleted, with its stored secrets"
    assert store.load().secrets.locations() == []
    assert client.get("/providers/llm/claude").status_code == 404


def test_clearing_the_default_agent_is_idempotent(client: TestClient) -> None:
    """Unset is a configuration rather than a missing entity, so clearing
    one that is already clear is not a 404, exactly as in the CLI."""
    assert client.delete("/default-agent").status_code == 200
    assert client.delete("/default-agent").status_code == 200
    assert client.get("/default-agent").json() == {"name": None}


def test_a_device_is_written_under_either_spelling_of_its_mac(
    client: TestClient, store: ConfigStore
) -> None:
    _pipeline(client)

    client.put("/devices/AA-BB-CC-DD-EE-FF", json={"agents": ["sam"]})

    assert set(store.load().domain.devices) == {"aa:bb:cc:dd:ee:ff"}
    assert client.delete("/devices/aa:bb:cc:dd:ee:ff").json()["wrote"] == (
        "device aa:bb:cc:dd:ee:ff deleted"
    )


@pytest.mark.parametrize("name", AWKWARD)
def test_an_identity_that_needs_encoding_round_trips_through_a_write(
    client: TestClient, name: str
) -> None:
    """A name is one decoded path segment, so a space, a percent sign or
    a character outside ASCII is written and read back by
    percent-encoding it and nothing else."""
    encoded = quote(name, safe="")

    assert client.put(f"/agents/{encoded}", json={"prompt": "You are it."}).json()[
        "wrote"
    ] == f"agent {name}"
    assert client.get(f"/agents/{encoded}").json()["entity"]["prompt"] == "You are it."
    assert client.delete(f"/agents/{encoded}").status_code == 200


def test_a_dotted_mcp_slot_rides_in_one_path_segment(
    client: TestClient, store: ConfigStore
) -> None:
    """`env.<KEY>` and `headers.<Key>` carry a dot, which needs no
    encoding and no second addressing scheme."""
    client.put(
        "/mcp-servers/weather",
        json={"transport": "streamable_http", "url": "https://example.invalid/mcp"},
    )

    assert client.put(
        "/mcp-servers/weather/secrets/headers.Authorization", json={"secret": SECRET}
    ).json()["wrote"] == "secret for mcp_server weather headers.Authorization"
    assert client.put(
        "/mcp-servers/weather/secrets/env.API_TOKEN", json={"secret": OTHER_SECRET}
    ).status_code == 200

    assert store.load().secrets.slots_for("mcp_server", "weather") == [
        "env.API_TOKEN",
        "headers.Authorization",
    ]
    assert client.delete("/mcp-servers/weather/secrets/env.API_TOKEN").status_code == 200


def test_a_name_no_path_can_carry_is_unroutable_rather_than_written(
    client: TestClient
) -> None:
    """The reason the addressability rule exists, seen from the
    transport: a slash in a name is a slash in the path, so such a write
    never reaches a handler at all. The rule is what stops one being
    created by a caller that could reach the repository directly."""
    response = client.put("/agents/a%2Fb", json={"prompt": "You are it."})

    assert response.status_code == 404
    assert client.get("/agents").json() == {}


# What a write refuses


def test_a_refused_reference_is_422_in_the_repository_s_own_words(
    client: TestClient
) -> None:
    response = client.put("/agents/sam", json={"llm": "ghost"})

    assert response.status_code == 422
    assert "names no llm provider that exists" in response.json()["detail"]


def test_deleting_something_that_is_not_there_is_404(client: TestClient) -> None:
    for path, section in (
        ("/providers/llm/ghost", "providers"),
        ("/mcp-servers/ghost", "mcp_servers"),
        ("/prompt-fragments/ghost", "prompt_fragments"),
        ("/agents/ghost", "agents"),
        ("/devices/aa:bb:cc:dd:ee:ff", "devices"),
    ):
        response = client.delete(path)
        assert response.status_code == 404, path
        assert refused(response.json(), 404).startswith(f"{section}:"), path


# Every addressed section, as the path a read and a delete of something
# that addresses nothing go to, the status and the sentence both answer
# with, and the value that must not come back. The names are
# credential-shaped because a URL path is where a paste lands; a device
# is addressed by a MAC, which cannot be shaped like a credential, so
# its own identity is the sentinel.
#
# The last row is a segment that addresses nothing in a different way: a
# provider is addressed by a stage and a name together, and a stage that
# is not one of the four is the caller's mistake rather than something
# missing, so it is the same paste meeting a different refusal and a
# different status.
UNWRITTEN = [
    (f"/providers/llm/{quote(SECRET, safe='')}", 404, "providers", SECRET),
    (f"/providers/llm/{quote(f'{SECRET}.pasted', safe='')}", 404, "providers", SECRET),
    (f"/mcp-servers/{quote(SECRET, safe='')}", 404, "mcp_servers", SECRET),
    (f"/prompt-fragments/{quote(SECRET, safe='')}", 404, "prompt_fragments", SECRET),
    (
        f"/prompt-fragments/{quote(f'{SECRET}.pasted', safe='')}",
        404,
        "prompt_fragments",
        SECRET,
    ),
    (f"/agents/{quote(SECRET, safe='')}", 404, "agents", SECRET),
    ("/devices/aa:bb:cc:dd:ee:ff", 404, "devices", "aa:bb:cc:dd:ee:ff"),
    (f"/providers/{quote(SECRET, safe='')}/claude", 422, "providers", SECRET),
]


@pytest.mark.parametrize(("path", "status", "section", "sentinel"), UNWRITTEN)
def test_an_identity_that_addresses_nothing_is_refused_without_it(
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
    path: str,
    status: int,
    section: str,
    sentinel: str,
) -> None:
    """The read and the delete of an identity nothing wrote, for every
    section that has one, and of a stage that is not a stage (#132).

    It arrived in the path and was never validated by anything here, so
    the refusal names the section and the fact and not what was typed.
    The three places it could still come out are all looked at: the
    body, the headers, and every record this server retained while
    answering.
    """
    with caplog.at_level(logging.DEBUG):
        read = client.get(path)
        removed = client.delete(path)

    for response in (read, removed):
        assert response.status_code == status
        # The leak first and the shape after it, so a failure here says
        # which of the two moved.
        assert sentinel not in response.text
        assert sentinel not in str(response.headers)
        assert refused(response.json(), status).startswith(f"{section}:")
    served = [
        record for record in caplog.records if record.name.startswith("vinga_server")
    ]
    assert all(sentinel not in str(record.__dict__) for record in served)


def test_a_secret_for_a_slot_holding_none_is_404(client: TestClient) -> None:
    client.put("/providers/llm/claude", json={"type": "anthropic", "model": "m"})

    response = client.delete("/providers/llm/claude/secrets/api_key")

    assert response.status_code == 404
    assert refused(response.json(), 404).startswith("providers:")


@pytest.mark.parametrize("slot", ["api_key", SECRET])
def test_a_secret_on_an_entity_that_is_not_there_is_404_without_either_name(
    client: TestClient, caplog: pytest.LogCaptureFixture, slot: str
) -> None:
    """The same rule one level in. Both halves of a secret's address are
    typed rather than stored: the entity that would hold the credential
    and the slot it would fill, so the refusal names neither."""
    path = f"/providers/llm/{quote(SECRET, safe='')}/secrets/{quote(slot, safe='')}"

    with caplog.at_level(logging.DEBUG):
        written = client.put(path, json={"secret": OTHER_SECRET})
        removed = client.delete(path)

    for response in (written, removed):
        assert response.status_code == 404
        assert SECRET not in response.text
        assert OTHER_SECRET not in response.text
    served = [
        record for record in caplog.records if record.name.startswith("vinga_server")
    ]
    assert all(SECRET not in str(record.__dict__) for record in served)
    assert all(OTHER_SECRET not in str(record.__dict__) for record in served)


# The second half of a secret's address, driven against entities that
# exist so that the check under test is the slot's own rather than the
# entity miss that would otherwise answer first. One entry per kind: the
# entity to create, the body that creates it, and the slot path with the
# sentence it refuses with.
SLOTS = [
    (
        "/providers/llm/claude",
        {"type": "anthropic", "model": "m"},
        f"/providers/llm/claude/secrets/{quote(PASTED, safe='')}",
        "providers",
    ),
    (
        "/mcp-servers/home",
        {"transport": "stdio", "command": "uvx"},
        f"/mcp-servers/home/secrets/{quote(PASTED, safe='')}",
        "mcp_servers",
    ),
]


@pytest.mark.parametrize(("entity", "body", "path", "section"), SLOTS)
def test_a_slot_that_is_not_a_credential_slot_is_refused_without_it(
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
    entity: str,
    body: dict[str, object],
    path: str,
    section: str,
) -> None:
    """A slot arrives the way an entity name does, in a URL path, and the
    request it arrives on is the one a credential is pasted into: a slot
    that is not a slot may be the credential itself, sent one segment
    early (#132). So the refusal states the rule and never the value.

    Both verbs, and they answer differently on purpose. A write checks
    the slot, which is the 422 above; a delete never validates one, it
    looks for a stored secret and does not find it, which is the 404 its
    section answers with. Neither repeats what was addressed.
    """
    assert client.put(entity, json=body).status_code == 200

    with caplog.at_level(logging.DEBUG):
        written = client.put(path, json={"secret": SECRET})
        removed = client.delete(path)

    for response in (written, removed):
        assert PASTED not in response.text
        assert SECRET not in response.text
    assert written.status_code == 422
    assert refused(written.json(), 422).startswith(f"{section}:")
    assert removed.status_code == 404
    assert refused(removed.json(), 404).startswith(f"{section}:")
    served = [
        record for record in caplog.records if record.name.startswith("vinga_server")
    ]
    assert all(PASTED not in str(record.__dict__) for record in served)
    assert all(SECRET not in str(record.__dict__) for record in served)


def test_an_identity_that_cannot_be_addressed_at_all_is_422(client: TestClient) -> None:
    stage = client.put("/providers/nonsense/x", json={"type": "mock"})
    mac = client.put("/devices/not-a-mac", json={"agents": ["sam"]})

    assert stage.status_code == 422
    stage_detail = refused(stage.json(), 422)
    assert all(known in stage_detail for known in PROVIDER_STAGES), stage_detail
    assert mac.status_code == 422
    assert "MAC" in refused(mac.json(), 422)


def test_a_mac_that_is_not_a_mac_is_refused_without_it(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """The shape refusal, one step before any lookup (#205).

    A device is addressed by a MAC, and the segment carrying it is a
    place a paste lands like every other: what fails this check is a
    value nothing here has validated, and it may be the credential
    itself. So the refusal states what a MAC is and never what arrived.

    All three verbs, because the check runs on the identity rather than
    on the request: a write, a read and a delete each reach it before
    the row is looked for, which is why every one of them is 422 rather
    than the 404 a device that is merely absent gets. The three places
    the value could still come out are all looked at: the body, the
    headers, and every record this server retained while answering.
    """
    path = f"/devices/{quote(PASTED, safe='')}"

    with caplog.at_level(logging.DEBUG):
        written = client.put(path, json={"agents": ["sam"]})
        read = client.get(path)
        removed = client.delete(path)

    for response in (written, read, removed):
        # The leak first and the shape after it, so a failure here says
        # which of the two moved.
        assert PASTED not in response.text
        assert PASTED not in str(response.headers)
        assert response.status_code == 422
        assert NOT_A_MAC in refused(response.json(), 422)
    served = [
        record for record in caplog.records if record.name.startswith("vinga_server")
    ]
    assert all(PASTED not in str(record.__dict__) for record in served)


def test_a_write_that_cannot_take_the_lock_is_409(
    api: FastAPI, store: ConfigStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The retryable refusal, forced by holding a real lock rather than
    hoped for, and asserted by status code so that no wording becomes
    load-bearing.

    The client is built here rather than taken from the fixture because
    the short lock timeout has to be in place before the engine opens:
    the API's engine is its lifespan's since #142, its connections are
    pooled, the timeout rides on a connection's startup options, and a
    connection made under the packaged ten seconds keeps them. So the
    order is the scenario: shorten the timeout, open the application,
    then hold the lock.
    """
    with holding_the_write_lock(monkeypatch), TestClient(
        api, headers={"Authorization": f"Bearer {TOKEN}"}
    ) as client:
        with the_lock_held():
            response = client.put("/agents/sam", json={"prompt": "You are Sam."})

        assert response.status_code == 409
        assert set(response.json()) == PROBLEM_KEYS
        # And with the lock let go, the same request is answered.
        assert client.put("/agents/sam", json={"prompt": "You are Sam."}).status_code == 200


# The argument-shaped bodies


MALFORMED_DEVICE = [
    {},
    {"agent": ["sam"]},
    {"agents": ["sam"], "extra": 1},
    {"agents": None},
    {"agents": "sam"},
    {"agents": [1]},
    ["sam"],
    "sam",
]

MALFORMED_DEFAULT_AGENT = [
    {},
    {"agent": "sam"},
    {"name": "sam", "extra": 1},
    {"name": None},
    {"name": ["sam"]},
    ["sam"],
]

MALFORMED_SECRET = [
    {},
    {"value": SECRET},
    {"secret": SECRET, "extra": 1},
    {"secret": None},
    {"secret": ""},
    {"secret": [SECRET]},
    {"secret": {"secret": SECRET}},
    [SECRET],
    SECRET,
]


@pytest.mark.parametrize("body", MALFORMED_DEVICE)
def test_a_device_body_of_the_wrong_shape_is_refused(client: TestClient, body: object) -> None:
    response = client.put("/devices/aa:bb:cc:dd:ee:ff", json=body)

    assert response.status_code == 422
    assert '"agents"' in response.json()["detail"]


@pytest.mark.parametrize("body", MALFORMED_DEFAULT_AGENT)
def test_a_default_agent_body_of_the_wrong_shape_is_refused(
    client: TestClient, body: object
) -> None:
    response = client.put("/default-agent", json=body)

    assert response.status_code == 422
    assert '"name"' in response.json()["detail"]


@pytest.mark.parametrize("body", MALFORMED_SECRET)
def test_a_secret_body_of_the_wrong_shape_is_refused_without_echoing_it(
    client: TestClient, body: object, caplog: pytest.LogCaptureFixture
) -> None:
    """Every one of these carries the sentinel where a credential would
    be, which is what makes them the case worth checking: the body a
    mistake malforms is the body a mistake put a credential into."""
    client.put("/providers/llm/claude", json={"type": "anthropic", "model": "m"})

    with caplog.at_level(logging.DEBUG):
        response = client.put("/providers/llm/claude/secrets/api_key", json=body)

    assert response.status_code == 422
    assert '"secret"' in response.json()["detail"]
    assert SECRET not in response.text
    assert SECRET not in str(response.headers)
    assert SECRET not in caplog.text


def test_a_body_that_is_not_json_at_all_is_refused_without_echoing_it(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """FastAPI's own validation fires here, and its default response
    quotes the input it rejected."""
    with caplog.at_level(logging.DEBUG):
        broken = client.put(
            "/agents/sam",
            content=f"not json {SECRET}".encode(),
            headers={"content-type": "application/json"},
        )
        missing = client.put("/agents/sam", json=None)

    for response in (broken, missing):
        assert response.status_code == 422
        assert SECRET not in response.text
        assert "Traceback" not in response.text
    assert SECRET not in caplog.text


# Shared prompt fragments
#
# The write body and the read back are pinned exactly here, because a
# fragment is the one entity whose whole content is text promised
# verbatim: anything the transport tidied up would be tidied up in the
# model's prompt too.


FRAGMENT = "  The bins go out on Tuesday.\n\n    The radio is called Bosse.\n"


def test_a_fragment_is_written_and_read_back_byte_for_byte(
    client: TestClient, store: ConfigStore
) -> None:
    written = client.put("/prompt-fragments/household", json={"text": FRAGMENT})

    assert written.status_code == 200, written.text
    assert written.json()["wrote"] == "prompt-fragment household"
    assert boundaries(written.json()["notice"]) == {RELOAD}
    assert client.get("/prompt-fragments/household").json() == {
        "entity": {"text": FRAGMENT},
        "secrets": {},
    }
    assert store.read_prompt_fragment("household").entry.text == FRAGMENT


def test_a_fragment_is_deleted_unless_something_includes_it(
    client: TestClient, store: ConfigStore
) -> None:
    _pipeline(client)
    client.put("/prompt-fragments/household", json={"text": "The bins go out on Tuesday."})
    client.put(
        "/agents/sam", json={"prompt": "You are Sam.", "prompt_includes": ["household"]}
    )

    held = client.delete("/prompt-fragments/household")
    assert held.status_code == 422
    assert "prompt_includes" in held.json()["detail"]

    client.put("/agents/sam", json={"prompt": "You are Sam."})
    gone = client.delete("/prompt-fragments/household")

    assert gone.json()["wrote"] == "prompt-fragment household deleted"
    assert boundaries(gone.json()["notice"]) == {RELOAD}
    assert client.get("/prompt-fragments/household").status_code == 404


@pytest.mark.parametrize("path", ["/agents/sam", "/agent-defaults"])
def test_an_unknown_include_is_refused_without_quoting_it(
    client: TestClient, caplog: pytest.LogCaptureFixture, path: str
) -> None:
    """The refusal names the layer, the position and the rule. It never
    names the include, because a name written beside prompt text is a
    place a credential gets pasted, and this sentence travels out as an
    HTTP body and into whatever collects the log."""
    _pipeline(client)
    body = (
        {"prompt": "You are Sam.", "prompt_includes": [SECRET]}
        if path == "/agents/sam"
        else {"llm": "claude", "prompt_includes": [SECRET]}
    )

    with caplog.at_level(logging.DEBUG):
        response = client.put(path, json=body)

    assert response.status_code == 422
    assert "prompt_includes: entry 1" in response.json()["detail"]
    assert SECRET not in response.text
    assert SECRET not in str(response.headers)
    assert SECRET not in caplog.text


# The bodies an unusable name can arrive with. The invalid ones are the
# point: a refusal about a body says where the body was written, and for
# a fragment that location is the name.
UNUSABLE_BODIES: list[object] = [
    {"text": "a"},
    {},
    None,
    {"text": ""},
    {"text": 4},
    {"text": "a", "extra": "b"},
    [SECRET],
    SECRET,
]


@pytest.mark.parametrize("body", UNUSABLE_BODIES)
def test_an_unusable_fragment_name_is_refused_without_quoting_it(
    client: TestClient, caplog: pytest.LogCaptureFixture, body: object
) -> None:
    """The refusal names the section and the rule, and never the name it
    rejected, whatever else the request got wrong.

    The bodies are the assertion. The name is checked before any of them
    is parsed, so a request that pastes a credential into the path and
    sends a body that will not parse still meets the sentence about the
    name rather than one about the body, which would have named the
    location the body was written at.

    What is asserted about the log is what this server writes. A name
    travels in the path, so the client that sent the request has it by
    construction and records it in its own request line; that is the
    operator's own transport, and what this pins is that nothing on this
    side repeats it.
    """
    with caplog.at_level(logging.DEBUG):
        response = client.put(
            f"/prompt-fragments/{quote(SECRET + '.pasted', safe='')}", json=body
        )

    assert response.status_code == 422
    detail = refused(response.json(), 422)
    # Either the name's own rule, whose character class is the semantic
    # half of it, or the framework's sanitized refusal for a body it
    # could not read at all, which never reaches the repository and
    # names nothing either.
    assert "[A-Za-z0-9_-]+" in detail or "JSON object body" in detail
    assert SECRET not in response.text
    assert SECRET not in str(response.headers)
    served = [
        record for record in caplog.records if record.name.startswith("vinga_server")
    ]
    assert all(SECRET not in record.getMessage() for record in served)
    assert all(SECRET not in str(record.__dict__) for record in served)


def test_a_fragment_the_models_refuse_is_not_quoted_back(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """An inline secret in a fragment is the case where the rejected
    input is itself the thing that must not travel back."""
    with caplog.at_level(logging.DEBUG):
        response = client.put(
            "/providers/llm/claude", json={"type": "anthropic", "api_key": SECRET}
        )

    assert response.status_code == 422
    assert "looks like an inline secret" in response.json()["detail"]
    assert SECRET not in response.text
    assert SECRET not in str(response.headers)
    assert SECRET not in caplog.text


# What a stored secret does, and never does


def test_a_secret_set_over_http_is_stored_encrypted_and_never_read_back(
    client: TestClient, store: ConfigStore, caplog: pytest.LogCaptureFixture
) -> None:
    """The whole contract of a secret write in one place: it is stored
    encrypted, it shadows the reference written for the same slot, it
    decrypts where it is used, and it appears in no response, header or
    log."""
    client.put(
        "/providers/llm/claude",
        json={"type": "anthropic", "model": "m", "api_key_env": "ANTHROPIC_API_KEY"},
    )

    with caplog.at_level(logging.DEBUG):
        wrote = client.put("/providers/llm/claude/secrets/api_key", json={"secret": SECRET})
        read = client.get("/providers/llm/claude")
        whole = client.get("/config")

    assert wrote.json()["wrote"] == "secret for provider llm.claude api_key"
    # Stored as ciphertext, and openable where it is used.
    location = SecretLocation.provider("llm", "claude", "api_key")
    snapshot = store.load()
    assert snapshot.secrets.secret(location) == SECRET
    # Named in the envelope, marked as displacing the reference beside
    # it, and never valued.
    assert read.json()["secrets"] == {"api_key": {"shadows": "api_key_env"}}
    assert read.json()["entity"]["api_key_env"] == "ANTHROPIC_API_KEY"
    for response in (wrote, read, whole):
        assert SECRET not in response.text
        assert SECRET not in str(response.headers)
    assert SECRET not in caplog.text
    assert TOKEN not in caplog.text


def test_a_cleared_secret_stops_shadowing_its_reference(
    client: TestClient, store: ConfigStore
) -> None:
    client.put(
        "/providers/llm/claude",
        json={"type": "anthropic", "model": "m", "api_key_env": "ANTHROPIC_API_KEY"},
    )
    client.put("/providers/llm/claude/secrets/api_key", json={"secret": SECRET})

    response = client.delete("/providers/llm/claude/secrets/api_key")

    assert response.json()["wrote"] == "secret for provider llm.claude api_key cleared"
    assert client.get("/providers/llm/claude").json()["secrets"] == {}
    assert store.load().secrets.locations() == []


# The whole document in one request
#
# `POST /apply` is the one write whose answer is a list, and the split
# between the repository and this route is the one every write has: the
# repository answers with the canonical outcome, and the route computes
# what the repository cannot know, which is when each entry takes effect
# and which activation codes a successful commit retired.

DOCUMENT: dict[str, object] = {
    "providers": {"llm": {"claude": {"type": "anthropic", "model": "m"}}},
    "agents": {"sam": {"prompt": "You are Sam."}},
    "devices": {"AA-BB-CC-DD-EE-FF": ["sam"]},
    "default_agent": "sam",
}


def test_a_document_is_applied_whole_and_listed_in_order(
    client: TestClient, store: ConfigStore
) -> None:
    response = client.post("/apply", json=DOCUMENT)

    assert response.status_code == 200
    entries = response.json()["entries"]
    assert [(one["section"], one["identity"], one["outcome"]) for one in entries] == [
        ("providers", "llm.claude", "wrote"),
        ("agents", "sam", "wrote"),
        ("devices", "aa:bb:cc:dd:ee:ff", "wrote"),
        ("default_agent", "", "wrote"),
    ]
    assert store.load().domain.default_agent == "sam"


def test_every_applied_entry_says_when_it_takes_effect(client: TestClient) -> None:
    """The same four boundaries a single write announces, chosen the
    same way: the entity kinds name the reload, and the two settings are
    read as a device asks for them."""
    entries = client.post("/apply", json=DOCUMENT).json()["entries"]

    named = {one["section"]: boundaries(one["notice"]) for one in entries}
    assert named["providers"] == {RELOAD}
    assert named["agents"] == {RELOAD}
    # This application serves no agent at all, which is the honest answer
    # for one built without a server, so both settings name the reload
    # that would install the agent beside the check-in the row is live at.
    assert named["devices"] == {CHECK_IN, RELOAD}
    assert named["default_agent"] == {CHECK_IN, RELOAD}


def test_an_applied_binding_to_a_loaded_agent_needs_no_reload(
    serving_client: TestClient,
) -> None:
    """The notice depends on the running server, which is why the route
    computes it and the repository does not: the same document answers
    differently on a server that is serving the agent it names."""
    entries = serving_client.post("/apply", json=DOCUMENT).json()["entries"]

    named = {one["section"]: boundaries(one["notice"]) for one in entries}
    assert named["devices"] == {CHECK_IN}
    assert named["default_agent"] == {CHECK_IN}


def test_an_unchanged_entry_has_nothing_to_apply(client: TestClient) -> None:
    """Applied twice: the second says every row was already what the
    document says, and an entry with no write has no boundary to
    announce."""
    client.post("/apply", json=DOCUMENT)

    entries = client.post("/apply", json=DOCUMENT).json()["entries"]

    assert {one["outcome"] for one in entries} == {"unchanged"}
    assert [one["notice"] for one in entries] == [None] * len(entries)


def test_a_refused_document_writes_nothing_and_quotes_nothing(
    client: TestClient, store: ConfigStore, caplog: pytest.LogCaptureFixture
) -> None:
    """Refused whole, with the repository's own sentences, and the
    fragment a mistake put a credential into is not quoted back."""
    with caplog.at_level(logging.DEBUG):
        response = client.post(
            "/apply",
            json={
                "providers": {"llm": {"claude": {"type": "anthropic", "api_key": SECRET}}},
                "agents": {"sam": {"prompt": "You are Sam."}},
            },
        )

    assert response.status_code == 422
    assert set(response.json()) == PROBLEM_KEYS
    assert "looks like an inline secret" in refused(response.json(), 422)
    assert SECRET not in response.text
    assert SECRET not in str(response.headers)
    assert SECRET not in caplog.text
    snapshot = store.load()
    assert snapshot.domain.providers.llm == {}
    assert snapshot.domain.agents == {}


def test_an_empty_document_applies_nothing(client: TestClient) -> None:
    response = client.post("/apply", json={})

    assert response.status_code == 200
    assert response.json() == {"entries": []}


def test_a_document_larger_than_the_endpoint_reads_is_refused(
    client: TestClient, store: ConfigStore
) -> None:
    """Request hygiene, and the sentence names the size rather than
    quoting anything: what is in an over-large body is as unvalidated as
    anything else a fragment carries."""
    padding = "x" * (APPLY_BODY_LIMIT + 1)

    response = client.post(
        "/apply", json={"prompt_fragments": {"household": {"text": padding}}}
    )

    assert response.status_code == 422
    assert str(APPLY_BODY_LIMIT) in refused(response.json(), 422)
    assert padding not in response.text
    assert store.load().domain.prompt_fragments == {}


def test_applying_is_gated(client: TestClient) -> None:
    response = client.post(
        "/apply", json=DOCUMENT, headers={"Authorization": "Bearer wrong"}
    )

    assert response.status_code == 401


# The same reference edges over HTTP, and over the CLI beside it
#
# The sentence is the repository's and travels unchanged, so what these
# add is the surfaces around it: a 422 body, its headers, and every log
# record either side kept. Each document below is valid apart from the
# one reference that will not resolve, so the reference pass is what
# refuses it rather than the model in front of that.

REFERENCE_LEAKS = [
    ("an agent's provider", {"agents": {"a": {"prompt": "p", "llm": SECRET}}}),
    ("an agent's MCP server", {"agents": {"a": {"prompt": "p", "mcp": [SECRET]}}}),
    (
        "an agent's prompt fragment",
        {"agents": {"a": {"prompt": "p", "prompt_includes": [SECRET]}}},
    ),
    ("a device's agent", {"devices": {"aa:bb:cc:dd:ee:ff": [SECRET]}}),
    ("the default agent", {"default_agent": SECRET}),
]


@pytest.mark.parametrize(
    "document",
    [document for _, document in REFERENCE_LEAKS],
    ids=[what for what, _ in REFERENCE_LEAKS],
)
def test_an_applied_reference_that_will_not_resolve_quotes_no_name(
    client: TestClient, caplog: pytest.LogCaptureFixture, document: object
) -> None:
    _pipeline(client)

    with caplog.at_level(logging.DEBUG):
        response = client.post("/apply", json=document)

    assert response.status_code == 422
    detail = refused(response.json(), 422)
    assert detail.startswith("the change was refused")
    assert "not quoted back" in detail
    assert SECRET not in response.text
    assert SECRET not in str(response.headers)
    assert SECRET not in caplog.text


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/agents/sam", {"prompt": "p", "llm": SECRET}),
        ("/agent-defaults", {"tts": SECRET}),
        ("/devices/aa:bb:cc:dd:ee:ff", {"agents": [SECRET]}),
        ("/default-agent", {"name": SECRET}),
    ],
    ids=["an agent's provider", "the defaults' provider", "a binding", "the default"],
)
def test_a_single_write_quotes_no_name_it_could_not_resolve_either(
    client: TestClient, caplog: pytest.LogCaptureFixture, path: str, body: object
) -> None:
    """The wording is the shared semantics', so a single write says what
    an applied document says: one kind of mistake, one vocabulary,
    whichever verb reached it."""
    _pipeline(client)

    with caplog.at_level(logging.DEBUG):
        response = client.request("PUT", path, json=body)

    assert response.status_code == 422
    assert SECRET not in response.text
    assert SECRET not in str(response.headers)
    assert SECRET not in caplog.text


VALIDATOR_LEAKS = [
    (
        "a binding naming one agent twice",
        {"devices": {"aa:bb:cc:dd:ee:ff": [SECRET, SECRET]}},
        "more than one position",
    ),
    (
        "an entry name that is not a tool prefix",
        {"mcp_servers": {f"{SECRET}.pasted": {"transport": "stdio", "command": "uvx"}}},
        "must match [A-Za-z0-9_-]+",
    ),
]


@pytest.mark.parametrize(
    ("document", "rule"),
    [(document, rule) for _, document, rule in VALIDATOR_LEAKS],
    ids=[what for what, _, _ in VALIDATOR_LEAKS],
)
def test_an_applied_validator_names_the_rule_and_not_the_value(
    client: TestClient, caplog: pytest.LogCaptureFixture, document: object, rule: str
) -> None:
    """The two validators an applied document reaches that are not the
    reference pass, over the wire: the sentence names the rule and the
    position, and the value it rejected is nowhere on the response, its
    headers or the log."""
    with caplog.at_level(logging.DEBUG):
        response = client.post("/apply", json=document)

    assert response.status_code == 422
    assert rule in refused(response.json(), 422)
    assert SECRET not in response.text
    assert SECRET not in str(response.headers)
    assert SECRET not in caplog.text


def test_a_single_binding_naming_one_agent_twice_says_the_same(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """And the same sentence from the single write, which is where the
    wording lived before an applied document could reach it."""
    with caplog.at_level(logging.DEBUG):
        response = client.put(
            "/devices/aa:bb:cc:dd:ee:ff", json={"agents": [SECRET, SECRET]}
        )

    assert response.status_code == 422
    assert "more than one position (1, 2)" in refused(response.json(), 422)
    assert SECRET not in response.text
    assert SECRET not in caplog.text
