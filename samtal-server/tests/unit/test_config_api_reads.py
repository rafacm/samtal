"""The read routes, over real HTTP.

Every read the CLI can do is reachable here, answering with the same
sentences and the status codes the plan fixes: 404 for an identity that
addresses nothing, 422 for one that cannot be addressed at all, 409 for
the lock that did not arrive, 500 for a stored row that cannot be read.
The messages are asserted equal to the repository's, never parsed.

The other property is that nothing leaks. A read is masked, so a stored
secret appears in no response body, no header and no log record, and a
plaintext that got into a row another way comes back masked rather than
read out by the request an operator would make to find it.
"""

import logging
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import update

from samtal_server import db as db_module
from samtal_server.config.api import build_api
from samtal_server.config.secrets import (
    MASK,
    MASTER_KEY_ENV,
    SecretLocation,
    generate_key,
    load_keys,
)
from samtal_server.config.store import ConfigStore
from samtal_server.db import DATABASE_FILENAME, open_database, schema

TOKEN = "test-api-token-" + "0123456789abcdef" * 2

# Not real credentials, and shaped so a substring check for one cannot
# match by accident.
SECRET = "sk-test-4f8b2c9e-never-a-real-credential"
OTHER_SECRET = "tok-test-7a1d3f60-never-a-real-credential"

# A credential shaped like a variable name: it gets past the models'
# paste check and is what the display path's own rule has to catch.
PASTED = "sk_test_4f8b2c9e_never_a_real_credential"

# Short enough that a blocked reader gives up inside a test run, and
# long enough that an unblocked one never sees it.
SHORT_BUSY_MS = 200

# Names that only survive a URL path percent-encoded. Legal repository
# identities, all three.
AWKWARD = ["a name with spaces", "100%-sure", "agente-café"]


@pytest.fixture
def directory(tmp_path: Path) -> Path:
    return tmp_path / "db"


@pytest.fixture
def store(directory: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ConfigStore]:
    """The repository the test writes through, on the database the API
    reads. The API opens its own connection per request, so what is
    written here is what a request finds."""
    monkeypatch.setenv(MASTER_KEY_ENV, generate_key())
    engine = open_database(directory)
    try:
        yield ConfigStore(engine, load_keys())
    finally:
        engine.dispose()


@pytest.fixture
def api(directory: Path) -> FastAPI:
    return build_api(TOKEN, directory)


@pytest.fixture
def client(api: FastAPI) -> TestClient:
    return TestClient(api, headers={"Authorization": f"Bearer {TOKEN}"})


def _populate(store: ConfigStore) -> None:
    store.set_provider(
        "llm",
        "claude",
        {"type": "anthropic", "model": "m", "api_key_env": "ANTHROPIC_API_KEY"},
    )
    store.set_provider("tts", "voice", {"type": "piper", "model": "es"})
    store.set_mcp_server(
        "weather",
        {
            "transport": "streamable_http",
            "url": "https://example.invalid/mcp",
            "headers": {"Authorization": "$WEATHER_TOKEN", "X-Region": "eu"},
        },
    )
    store.set_agent_defaults({"llm": "claude"})
    store.set_agent("sam", {"prompt": "You are Sam.", "tts": "voice"})
    store.bind_device("AA-BB-CC-DD-EE-FF", ["sam"])
    store.set_default_agent("sam")


def _with_secrets(store: ConfigStore) -> None:
    store.set_secret(SecretLocation.provider("llm", "claude", "api_key"), SECRET)
    store.set_secret(SecretLocation.mcp_server("weather", "headers.Authorization"), OTHER_SECRET)


ENTITY_PATHS = [
    "/providers/llm/claude",
    "/mcp-servers/weather",
    "/agents/sam",
    "/agent-defaults",
    "/devices/aa:bb:cc:dd:ee:ff",
]

LIST_PATHS = ["/config", "/providers", "/mcp-servers", "/agents", "/devices", "/default-agent"]


# What a read answers


def test_every_route_is_gated(client: TestClient, store: ConfigStore) -> None:
    """The gate is in front of routing, so a real route is no more
    reachable without the token than an imaginary one."""
    _populate(store)

    for path in ENTITY_PATHS + LIST_PATHS:
        response = client.get(path, headers={"Authorization": "Bearer wrong"})
        assert response.status_code == 401, path


@pytest.mark.parametrize("path", ENTITY_PATHS)
def test_an_entity_reads_as_an_envelope(
    client: TestClient, store: ConfigStore, path: str
) -> None:
    """One shape for every kind: the entity, and the slots holding a
    stored secret beside it."""
    _populate(store)

    body = client.get(path).json()

    assert set(body) == {"entity", "secrets"}
    assert isinstance(body["entity"], dict)


def test_a_provider_names_its_stored_slot_and_what_it_shadows(
    client: TestClient, store: ConfigStore
) -> None:
    """The fact a masked read exists to convey, and the one the entity
    can never carry: this slot is filled from the database, and the
    reference written beside it is not what the server uses."""
    _populate(store)
    _with_secrets(store)

    body = client.get("/providers/llm/claude").json()

    assert body == {
        "entity": {"type": "anthropic", "api_key_env": "ANTHROPIC_API_KEY", "model": "m"},
        "secrets": {"api_key": {"shadows": "api_key_env"}},
    }


def test_an_mcp_server_marks_the_header_its_secret_displaces(
    client: TestClient, store: ConfigStore
) -> None:
    _populate(store)
    _with_secrets(store)

    body = client.get("/mcp-servers/weather").json()

    assert body["secrets"] == {"headers.Authorization": {"shadows": "headers.Authorization"}}
    assert body["entity"]["headers"] == {"Authorization": "$WEATHER_TOKEN", "X-Region": "eu"}


def test_a_stored_secret_with_no_reference_beside_it_shadows_nothing(
    client: TestClient, store: ConfigStore
) -> None:
    store.set_provider("llm", "plain", {"type": "anthropic", "model": "m"})
    store.set_secret(SecretLocation.provider("llm", "plain", "api_key"), SECRET)

    assert client.get("/providers/llm/plain").json()["secrets"] == {
        "api_key": {"shadows": None}
    }


@pytest.mark.parametrize("path", ["/agents/sam", "/agent-defaults", "/devices/aa:bb:cc:dd:ee:ff"])
def test_a_kind_that_holds_no_secret_answers_with_an_empty_mapping(
    client: TestClient, store: ConfigStore, path: str
) -> None:
    _populate(store)

    assert client.get(path).json()["secrets"] == {}


def test_the_listings_are_keyed_by_identity(client: TestClient, store: ConfigStore) -> None:
    """The names a client needs come with the entities, which is what a
    mapping gives and an array would not."""
    _populate(store)

    providers = client.get("/providers").json()
    assert set(providers) == {"llm", "asr", "tts", "vad"}
    assert providers["llm"]["claude"]["entity"]["type"] == "anthropic"
    assert providers["asr"] == {}
    assert set(client.get("/mcp-servers").json()) == {"weather"}
    assert client.get("/agents").json()["sam"]["entity"]["prompt"] == "You are Sam."
    assert client.get("/devices").json() == {
        "aa:bb:cc:dd:ee:ff": {"entity": {"agents": ["sam"]}, "secrets": {}}
    }


def test_the_default_agent_is_a_name_and_may_be_null(
    client: TestClient, store: ConfigStore
) -> None:
    """Unset is a configuration rather than a missing entity, so it is
    never a 404."""
    _populate(store)
    assert client.get("/default-agent").json() == {"name": "sam"}

    store.clear_default_agent()

    response = client.get("/default-agent")
    assert response.status_code == 200
    assert response.json() == {"name": None}


def test_the_whole_configuration_reads_in_one_request(
    client: TestClient, store: ConfigStore
) -> None:
    _populate(store)
    _with_secrets(store)

    body = client.get("/config").json()

    assert body["config"]["agents"]["sam"]["prompt"] == "You are Sam."
    assert body["config"]["agent_defaults"] == {"llm": "claude"}
    assert body["config"]["devices"] == {"aa:bb:cc:dd:ee:ff": ["sam"]}
    assert body["config"]["default_agent"] == "sam"
    assert body["secrets"] == [
        {
            "kind": "mcp_server",
            "identity": "weather",
            "slot": "headers.Authorization",
            "shadows": "headers.Authorization",
        },
        {
            "kind": "provider",
            "identity": "llm.claude",
            "slot": "api_key",
            "shadows": "api_key_env",
        },
    ]


def test_an_empty_database_reads_as_an_empty_configuration(client: TestClient) -> None:
    """The first start of a fresh deployment: nothing configured is a
    valid state to read, not an error."""
    body = client.get("/config").json()

    assert body["config"]["agents"] == {}
    assert body["secrets"] == []
    assert client.get("/agents").json() == {}
    assert client.get("/agent-defaults").json() == {"entity": {}, "secrets": {}}


def test_a_device_is_addressed_by_either_spelling_of_its_mac(
    client: TestClient, store: ConfigStore
) -> None:
    _populate(store)

    assert client.get("/devices/AA-BB-CC-DD-EE-FF").status_code == 200
    assert client.get("/devices/aa:bb:cc:dd:ee:ff").status_code == 200


@pytest.mark.parametrize("name", AWKWARD)
def test_an_identity_that_needs_encoding_round_trips(
    client: TestClient, store: ConfigStore, name: str
) -> None:
    """A name is one decoded path segment, so spaces, percent signs and
    characters outside ASCII are reached by percent-encoding and nothing
    else: no second addressing scheme, and none needed."""
    store.set_provider("llm", name, {"type": "anthropic", "model": "m"})
    store.set_agent(name, {"prompt": "You are it."})
    encoded = quote(name, safe="")

    assert client.get(f"/providers/llm/{encoded}").json()["entity"]["type"] == "anthropic"
    assert client.get(f"/agents/{encoded}").json()["entity"]["prompt"] == "You are it."
    # And the listing carries the name back undecorated, which is what
    # the client would encode again to address it.
    assert name in client.get("/agents").json()


def test_a_dotted_slot_reads_back_under_its_own_name(
    client: TestClient, store: ConfigStore
) -> None:
    """Both halves of an MCP slot survive a read: the group and the key
    the value would otherwise have referenced."""
    store.set_mcp_server("home", {"transport": "stdio", "command": "uvx"})
    store.set_secret(SecretLocation.mcp_server("home", "env.API_ACCESS_TOKEN"), SECRET)
    store.set_secret(SecretLocation.mcp_server("home", "headers.X-Api-Key"), OTHER_SECRET)

    body = client.get("/mcp-servers/home").json()

    assert set(body["secrets"]) == {"env.API_ACCESS_TOKEN", "headers.X-Api-Key"}
    assert SECRET not in body and OTHER_SECRET not in str(body)


# What a read refuses


MISSING = [
    ("/providers/llm/ghost", "providers.llm.ghost: no such provider"),
    ("/mcp-servers/ghost", "mcp_servers.ghost: no such MCP server"),
    ("/agents/ghost", "agents.ghost: no such agent"),
    ("/devices/aa:bb:cc:dd:ee:ff", "devices.aa:bb:cc:dd:ee:ff: no such device"),
]


@pytest.mark.parametrize(("path", "detail"), MISSING)
def test_a_missing_entity_is_404_in_the_repository_s_own_words(
    client: TestClient, store: ConfigStore, path: str, detail: str
) -> None:
    """The same sentence the CLI prints, so an operator meets one
    vocabulary whichever way they reached the read."""
    store.set_provider("llm", "claude", {"type": "anthropic", "model": "m"})

    response = client.get(path)

    assert response.status_code == 404
    assert response.json() == {"detail": detail}


def test_an_identity_that_cannot_be_addressed_at_all_is_422(
    client: TestClient, store: ConfigStore
) -> None:
    """A stage that is not a stage and a MAC that is not a MAC are the
    caller's mistake rather than something missing, and the codes have
    to tell those apart."""
    _populate(store)

    stage = client.get("/providers/nonsense/claude")
    mac = client.get("/devices/not-a-mac")

    assert stage.status_code == 422
    assert "is not a provider stage" in stage.json()["detail"]
    assert mac.status_code == 422
    assert "is not a MAC address" in mac.json()["detail"]


def test_a_row_that_cannot_be_read_is_500(
    client: TestClient, store: ConfigStore, caplog: pytest.LogCaptureFixture
) -> None:
    """The request was fine and the stored state is not, which is the
    server's problem and not the caller's."""
    _populate(store)
    with store._engine.begin() as connection:
        connection.execute(update(schema.providers).values(options="not an object"))

    with caplog.at_level(logging.ERROR):
        response = client.get("/config")

    assert response.status_code == 500
    assert "the options column does not hold an object" in response.json()["detail"]
    assert "Traceback" not in response.text

    # One fixed line, whose only variable part is the class name. Every
    # field is inspected, args included: a record holding the exception
    # itself would carry its message and its chain to anything that
    # walks the record, which is what a structured log handler does.
    logged = [record for record in caplog.records if record.name.startswith("samtal_server")]
    assert [record.args for record in logged] == [("StorageError",)]
    for record in logged:
        assert record.exc_info is None
        assert "unreadable stored state" in record.getMessage()
        for value in record.__dict__.values():
            assert not isinstance(value, BaseException)
        assert "options column" not in str(record.__dict__)


@pytest.mark.parametrize(
    ("table", "values", "path"),
    [
        (schema.providers, {"type": ""}, "/providers/llm/claude"),
        (schema.mcp_servers, {"transport": "nonsense"}, "/mcp-servers/weather"),
        (schema.agents, {"llm": ""}, "/agents/sam"),
        (schema.agent_defaults, {"tts": ""}, "/agent-defaults"),
        (schema.devices, {"mac": "not-a-mac"}, "/devices/aa:bb:cc:dd:ee:ff"),
    ],
)
def test_a_stored_row_that_will_not_validate_is_500(
    client: TestClient, store: ConfigStore, table: object, values: dict[str, object], path: str
) -> None:
    """A row that cannot be read as configuration is the server's
    problem, not the request's, whichever entity kind it belongs to and
    whether it is asked for by name or met on the way through the whole
    document."""
    _populate(store)
    with store._engine.begin() as connection:
        connection.execute(update(table).values(**values))

    for target in (path, "/config"):
        response = client.get(target)
        assert response.status_code == 500, target
        assert "cannot be read" in response.json()["detail"]
        assert "Traceback" not in response.text


def test_a_stored_number_that_is_not_finite_is_500(
    client: TestClient, store: ConfigStore
) -> None:
    """Serializing a stored NaN would answer null, which is a value
    nobody wrote and a different configuration from the stored one. The
    read says the row cannot be read instead."""
    _populate(store)
    with store._engine.begin() as connection:
        connection.execute(
            update(schema.providers)
            .where(schema.providers.c.name == "claude")
            .values(options={"temperature": float("nan")})
        )

    response = client.get("/providers/llm/claude")

    assert response.status_code == 500
    assert "not a finite number" in response.json()["detail"]
    assert client.get("/config").status_code == 500


def test_a_read_that_cannot_take_the_lock_is_409(
    client: TestClient, store: ConfigStore, directory: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The API opens the database per request, so a held lock is met by
    the open-and-migrate step before a handler runs; a read takes the
    same lock after that. Both are the retryable refusal, forced here by
    holding a real lock rather than hoped for, and asserted by status
    code so that no wording becomes load-bearing."""
    _populate(store)
    monkeypatch.setattr(db_module, "BUSY_TIMEOUT_MS", SHORT_BUSY_MS)
    holder = sqlite3.connect(directory / DATABASE_FILENAME, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        response = client.get("/config")
    finally:
        holder.close()

    assert response.status_code == 409
    assert set(response.json()) == {"detail"}
    # And the lock let go, the same request answers.
    assert client.get("/config").status_code == 200


# What a read never shows


def test_no_stored_secret_reaches_a_body_a_header_or_a_log(
    client: TestClient, store: ConfigStore, caplog: pytest.LogCaptureFixture
) -> None:
    _populate(store)
    _with_secrets(store)

    with caplog.at_level(logging.DEBUG):
        responses = [client.get(path) for path in ENTITY_PATHS + LIST_PATHS]

    for response in responses:
        assert SECRET not in response.text
        assert OTHER_SECRET not in response.text
        assert SECRET not in str(response.headers)
        assert OTHER_SECRET not in str(response.headers)
    assert SECRET not in caplog.text
    assert OTHER_SECRET not in caplog.text
    # The token that reached every one of them is not logged either.
    assert TOKEN not in caplog.text


def test_a_secret_shaped_key_nested_in_an_option_is_masked_everywhere(
    client: TestClient, store: ConfigStore, caplog: pytest.LogCaptureFixture
) -> None:
    """An option can be a structure, so a secret-shaped key can be
    nested inside one. The models refuse to accept a value under such a
    key now, but a reference key one level down accepts anything that
    looks like a variable name, which a credential can. Every read form
    masks it: the entity, the listing, the whole document, and no body,
    header or log record carries it."""
    store.set_provider(
        "llm",
        "claude",
        {"type": "anthropic", "connection": {"api_key_env": PASTED, "host": "example"}},
    )

    with caplog.at_level(logging.DEBUG):
        entity = client.get("/providers/llm/claude")
        listed = client.get("/providers")
        whole = client.get("/config")

    assert entity.json()["entity"]["connection"] == {"api_key_env": MASK, "host": "example"}
    assert listed.json()["llm"]["claude"]["entity"]["connection"]["api_key_env"] == MASK
    assert whole.json()["config"]["providers"]["llm"]["claude"]["connection"] == {
        "api_key_env": MASK,
        "host": "example",
    }
    for response in (entity, listed, whole):
        assert PASTED not in response.text
        assert PASTED not in str(response.headers)
    assert PASTED not in caplog.text


def test_a_plaintext_that_got_into_a_row_comes_back_masked(
    client: TestClient, store: ConfigStore
) -> None:
    """Fail-closed masking over the transport: the models keep an
    obvious paste out, but a credential shaped like a variable name gets
    past that check, and the request an operator would make to find the
    mistake must not be the one that publishes it."""
    _populate(store)
    with store._engine.begin() as connection:
        connection.execute(
            update(schema.providers)
            .where(schema.providers.c.name == "claude")
            .values(api_key_env=PASTED)
        )

    entity = client.get("/providers/llm/claude")
    whole = client.get("/config")

    assert entity.json()["entity"]["api_key_env"] == MASK
    assert PASTED not in entity.text
    assert whole.json()["config"]["providers"]["llm"]["claude"]["api_key_env"] == MASK
    assert PASTED not in whole.text
