"""What a read shows of a URL credential written before the rule (#381).

Since #279 a URL carrying a credential is refused at every write, an MCP
server's `url` and a provider's `base_url` alike, and no record made
from an entry carries one. Write time was all of it: a row stored before
that rule, or written straight into the database by something that never
passed through a write, still boots and still reads, and every display
built on the view walk read it back verbatim.

The rows here are planted rather than written, because the write path
refuses them. Each is the model's own dump put in place by
`stores.planted`, which is what a value that never met a write looks
like, and it is the least machinery that is still honest: the shape
under test is a lawful row holding an unlawful value, not a body no
model can parse.

Every display is asserted as a pair, because either half alone is
passable: the sentinel is nowhere in the answer, and the address without
it is in it. A view that dropped the field, or the whole entry, would
satisfy the absence half and fail here.

The surfaces are the ones the walk sits under: a single read, a listing,
the whole-configuration document, the API routes over them and the CLI
renderings over those. The export is the one where stripping changes an
outcome rather than a rendering, so it has cases of its own: a document
exported from a store holding such a row used to be one its own import
path refused whole.
"""

import json
import logging
from collections.abc import Iterator
from typing import NamedTuple

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import insert

from tests.support.config_cli import chain, document, runner
from tests.support.problems import refused as refusal_body
from tests.support.stores import body, planted
from vinga_server import logs, serving
from vinga_server.config import entities, views
from vinga_server.config.api import build_api
from vinga_server.config.boot import load_boot_config
from vinga_server.config.loader import ConfigError, StorageError, compose_config
from vinga_server.config.models import (
    AgentConfig,
    DatabaseConfig,
    FileConfig,
    McpServerConfig,
    ProviderConfig,
    url_credential,
    without_url_credential,
)
from vinga_server.config.secrets import MASTER_KEY_ENV, generate_key, load_keys
from vinga_server.config.store import ConfigStore
from vinga_server.db import open_database, schema

TOKEN = "test-api-token-" + "0123456789abcdef" * 2

# Not a real credential, and shaped so a substring check for it cannot
# match by accident.
SENTINEL = "sk-test-1c9f24ab-never-a-real-credential"

# The one a KEY carries, which cannot be the one above.
#
# A key is read by the inline-secret rule as well, and the value
# sentinel ends in the fragment `credential`, so a key built from it
# would be refused for looking like a secret before the URL rule was
# ever asked, and a case about the URL rule would be passing on the
# other one. This holds no fragment of either tuple: not `secret`,
# `token`, `password`, `api_key`, `apikey` or `credential`, and not the
# `auth` the wider set adds.
KEY_SENTINEL = "sk-test-6b0e73da-not-a-real-one"

# Both, since every surface case looks for the absence of each: the
# rows are planted into one store, so a rendering of the whole of it
# carries every shape at once.
SENTINELS = (SENTINEL, KEY_SENTINEL)

# One host for every planted address, so what tells the cases apart is
# the shape the credential is hidden in rather than where it points.
HOST = "legacy.invalid"


class Legacy(NamedTuple):
    """One planted row: where it goes, what it holds, and the addresses
    a read of it has to answer with instead of what it holds."""

    kind: str
    identity: tuple[str, ...]
    entry: BaseModel
    shown: tuple[str, ...]


# The shapes, across both kinds, both halves of a pair and two depths.
#
# `userinfo` and the credential-shaped query parameter are the two
# answers `url_credential` gives, and `auth` and `authorization` are the
# two spellings #279 added to it, so each of them is planted once. The
# nested ones are a provider option holding a structure, which is a
# shape a provider entry may hold because its options are passed
# through to the implementation, and they are the depth a per-field rule
# would have missed.
#
# The last three carry the credential in a KEY rather than in a value
# (#408). A mapping keyed by whatever the caller wrote is the one place
# a rule about values never looks, and there are three such groups: a
# provider's options at the top, the structures they pass through, and
# an MCP server's `env` and `headers`. All three were stored and shown
# verbatim, so all three are planted.
LEGACY = (
    Legacy(
        kind="provider",
        identity=("llm", "userinfo"),
        entry=ProviderConfig(
            type="openai_compatible",
            base_url=f"https://user:{SENTINEL}@{HOST}/v1",
            model="qwen3:8b",
            egress=False,
            connection={"endpoint": f"https://{HOST}/hook?authorization={SENTINEL}&model=small"},
        ),
        shown=(f"https://{HOST}/v1", f"https://{HOST}/hook?model=small"),
    ),
    Legacy(
        kind="provider",
        identity=("llm", "query"),
        entry=ProviderConfig(
            type="openai_compatible",
            base_url=f"https://{HOST}/v1?auth={SENTINEL}",
            model="qwen3:8b",
            egress=False,
        ),
        shown=(f"https://{HOST}/v1",),
    ),
    Legacy(
        kind="mcp-server",
        identity=("userinfo",),
        entry=McpServerConfig(
            transport="streamable_http", url=f"https://user:{SENTINEL}@{HOST}/mcp"
        ),
        shown=(f"https://{HOST}/mcp",),
    ),
    Legacy(
        kind="mcp-server",
        identity=("query",),
        entry=McpServerConfig(
            transport="streamable_http", url=f"https://{HOST}/mcp?authorization={SENTINEL}"
        ),
        shown=(f"https://{HOST}/mcp",),
    ),
    Legacy(
        kind="provider",
        identity=("llm", "top-key"),
        entry=ProviderConfig.model_validate(
            {
                "type": "openai_compatible",
                "base_url": f"https://{HOST}/v1",
                "model": "qwen3:8b",
                "egress": False,
                f"https://{HOST}/top?auth={KEY_SENTINEL}": "ordinary",
            }
        ),
        shown=(f"https://{HOST}/top",),
    ),
    Legacy(
        kind="provider",
        identity=("llm", "nested-key"),
        entry=ProviderConfig.model_validate(
            {
                "type": "openai_compatible",
                "base_url": f"https://{HOST}/v1",
                "model": "qwen3:8b",
                "egress": False,
                "connection": {f"https://user:{KEY_SENTINEL}@{HOST}/option": "ordinary"},
            }
        ),
        shown=(f"https://{HOST}/option",),
    ),
    Legacy(
        kind="mcp-server",
        identity=("env-key",),
        entry=McpServerConfig.model_validate(
            {
                "transport": "stdio",
                "command": "uvx",
                "env": {f"https://user:{KEY_SENTINEL}@{HOST}/spawn": "ordinary"},
            }
        ),
        shown=(f"https://{HOST}/spawn",),
    ),
)

# Every address a display of the whole store has to hold, which is what
# the document-wide and export cases assert against.
EVERY_ADDRESS = tuple(sorted({address for row in LEGACY for address in row.shown}))

IDS = [f"{row.kind}/{'.'.join(row.identity)}" for row in LEGACY]


@pytest.fixture
def keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MASTER_KEY_ENV, generate_key())


@pytest.fixture
def store(keys: None) -> Iterator[ConfigStore]:
    engine = open_database(DatabaseConfig())
    try:
        yield ConfigStore(engine, load_keys())
    finally:
        engine.dispose()


@pytest.fixture
def legacy(store: ConfigStore) -> ConfigStore:
    """The store with every planted row in it."""
    for row in LEGACY:
        _plant(store, row.kind, row.identity, row.entry)
    return store


@pytest.fixture
def api(keys: None) -> FastAPI:
    return build_api(TOKEN, DatabaseConfig())


@pytest.fixture
def client(api: FastAPI) -> Iterator[TestClient]:
    with TestClient(api, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        yield client


@pytest.fixture
def run(monkeypatch: pytest.MonkeyPatch):
    return runner(monkeypatch)


def _plant(
    store: ConfigStore, kind: str, identity: tuple[str, ...], entry: BaseModel
) -> None:
    """One entry written as a row rather than through a write path,
    which is the only way most of these get in at all.

    The body is the repository's own dump of the model, so what today's
    write path would object to is the value and nothing else: a plant
    that hand-wrote the JSON would be exercising the parser as well.
    """
    descriptor = entities.descriptor(kind)
    table = getattr(schema, descriptor.table)
    columns = dict(zip(descriptor.addressing, identity, strict=True))
    where = [table.c[column] == value for column, value in columns.items()]
    planted(
        store,
        table.delete().where(*where),
        insert(table).values(**columns, body=body(entry)),
    )


def _rendered(value: object) -> str:
    """One view as a caller receives it, serialized, which is the form a
    substring assertion is honest about: a credential nested three
    mappings down is in the answer exactly as much as one at the top."""
    return json.dumps(value, sort_keys=True, default=str)


def _shows_the_address_without_the_credential(rendered: str, addresses: tuple[str, ...]) -> None:
    for address in addresses:
        assert address in rendered
    _carries_no_sentinel(rendered)


def _carries_no_sentinel(*renderings: str) -> None:
    """No sentinel of the set, in any rendering a caller can reach.

    The tuple is looped over rather than named one line at a time, which
    is what keeps the every-surface claim honest as rows are added: a
    case that named one sentinel went on passing when the table grew a
    row carrying the other, which is exactly how the key rows arrived
    with the stderr and header assertions still covering only the value
    sentinel.
    """
    for rendering in renderings:
        for sentinel in SENTINELS:
            assert sentinel not in rendering


def _logged(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Every record written while a command ran, in both formats this
    server writes one in, which is the whole of what a no-leak claim
    about a log can mean: a value kept out of stdout and written to a
    log line is not kept."""
    text = logging.Formatter(logs.TEXT_FORMAT)
    return [
        rendering
        for record in caplog.records
        for rendering in (logs.JsonFormatter().format(record), text.format(record))
    ]


# The planted rows themselves, before any display


@pytest.mark.parametrize("row", LEGACY, ids=IDS)
def test_the_planted_row_really_holds_a_credential_a_write_would_refuse(
    row: Legacy,
) -> None:
    """The guard on every case below. These rows are constructed rather
    than written, so nothing but this says they hold what the display is
    being asked to strip, and a typo in a planted address would leave the
    whole file green over a store holding nothing interesting.

    Keys as well as values, which is what this walk missed when the
    suite was written: three of the rows carry their credential in the
    name a value was written under, and a guard that walked
    `dict.values()` would have vouched for a row holding nothing at all
    (#408).
    """
    held = [
        value
        for value in _strings(row.entry.model_dump())
        if url_credential(value) is not None
    ]

    assert held, row.identity
    for value in held:
        assert any(sentinel in value for sentinel in SENTINELS)
        assert without_url_credential(value) in row.shown


def _strings(value: object) -> Iterator[str]:
    """Every string an entry holds, on both halves of every pair."""
    if isinstance(value, dict):
        for key, nested in value.items():
            yield from _strings(key)
            yield from _strings(nested)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, str):
        yield value


# The views, one case per surface the walk sits under


@pytest.mark.parametrize("row", LEGACY, ids=IDS)
def test_a_single_read_shows_the_address_without_the_credential(
    legacy: ConfigStore, row: Legacy
) -> None:
    read = (
        views.provider(legacy.read_provider(*row.identity))
        if row.kind == "provider"
        else views.mcp_server(legacy.read_mcp_server(*row.identity))
    )

    _shows_the_address_without_the_credential(_rendered(read), row.shown)


@pytest.mark.parametrize("row", LEGACY, ids=IDS)
def test_a_listing_shows_the_address_without_the_credential(
    legacy: ConfigStore, row: Legacy
) -> None:
    snapshot = legacy.load()
    listed = (
        views.providers(snapshot) if row.kind == "provider" else views.mcp_servers(snapshot)
    )

    _shows_the_address_without_the_credential(_rendered(listed), row.shown)


def test_the_whole_configuration_document_shows_none_of_the_credentials(
    legacy: ConfigStore,
) -> None:
    """The document every export and every `show` of the deployment is
    built from, with all four rows in it at once."""
    _shows_the_address_without_the_credential(
        _rendered(views.config(legacy.load())), EVERY_ADDRESS
    )


# The API, which is the same views over HTTP


@pytest.mark.parametrize("row", LEGACY, ids=IDS)
def test_the_api_read_of_one_entity_carries_no_credential(
    legacy: ConfigStore, client: TestClient, row: Legacy
) -> None:
    path = (
        f"/providers/{row.identity[0]}/{row.identity[1]}"
        if row.kind == "provider"
        else f"/mcp-servers/{row.identity[0]}"
    )

    response = client.get(path)

    assert response.status_code == 200
    _shows_the_address_without_the_credential(response.text, row.shown)
    _carries_no_sentinel(str(dict(response.headers)))


def test_the_api_document_read_carries_no_credential(
    legacy: ConfigStore, client: TestClient
) -> None:
    response = client.get("/config")

    assert response.status_code == 200
    _shows_the_address_without_the_credential(response.text, EVERY_ADDRESS)


# The CLI, which renders those answers as YAML


def test_the_cli_shows_the_deployment_without_any_credential(
    legacy: ConfigStore,
    run,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`show` is the display projection of the whole document, so it is
    where an operator would have met the credential first.

    Both process streams and both log formats, which is the sentinel
    shape this repository holds a credential to everywhere else: a value
    kept out of stdout and written to a log line is not kept.
    """
    with caplog.at_level(logging.DEBUG):
        assert run("show") == 0

    printed = capsys.readouterr()
    _shows_the_address_without_the_credential(printed.out, EVERY_ADDRESS)
    _carries_no_sentinel(printed.err, *_logged(caplog))


@pytest.mark.parametrize("row", LEGACY, ids=IDS)
def test_the_cli_shows_one_entity_without_its_credential(
    legacy: ConfigStore, run, capsys: pytest.CaptureFixture[str], row: Legacy
) -> None:
    words = (
        ("provider", "show", *row.identity)
        if row.kind == "provider"
        else ("mcp-server", "show", *row.identity)
    )

    assert run(*words) == 0

    printed = capsys.readouterr()
    _shows_the_address_without_the_credential(printed.out, row.shown)
    _carries_no_sentinel(printed.err)
    # And what was printed is a document rather than a line that
    # happened to hold the address, which is what a `show` is for.
    assert document(printed.out)


# The export, where the strip changes an outcome rather than a rendering


def test_an_export_of_a_legacy_store_imports_onto_a_store_of_its_own(
    legacy: ConfigStore,
    spare_database: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The edge this fix removes.

    An export is the whole-configuration document in the shape `import`
    takes, and `import` runs the write path, which refuses a URL carrying
    a credential. So a store holding one used to export a document that
    nothing could take, its own store included: the one document an
    operator would reach for to move a deployment was the one the
    deployment could not produce.

    Onto a database of its own, because that is the claim in its
    strongest form: the document reproduces the configuration somewhere
    that has never seen the row it came from.
    """
    first = runner(monkeypatch)
    capsys.readouterr()

    assert first("export") == 0
    exported = capsys.readouterr().out

    _carries_no_sentinel(exported)
    for address in EVERY_ADDRESS:
        assert address in exported

    second = runner(monkeypatch, database=spare_database)
    assert second("import", "-f", "-", stdin=exported) == 0
    capsys.readouterr()

    assert second("show") == 0
    _shows_the_address_without_the_credential(capsys.readouterr().out, EVERY_ADDRESS)


def test_the_document_the_export_used_to_produce_is_still_refused(
    spare_database: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The control on the case above, and the reason the strip is what
    makes the round trip work rather than a rendering nicety: nothing
    about the import path moved. A document carrying the credential is
    refused exactly as it was, so what changed is that no export
    produces one.
    """
    run = runner(monkeypatch, database=spare_database)
    carrying = (
        "mcp_servers:\n"
        "  userinfo:\n"
        "    transport: streamable_http\n"
        f"    url: https://user:{SENTINEL}@{HOST}/mcp\n"
    )

    assert run("import", "-f", "-", stdin=carrying) == 1

    printed = capsys.readouterr()
    assert "user and password" in printed.err
    _carries_no_sentinel(printed.out, printed.err)


# The control: display fidelity, for everything that is not this


def test_a_string_that_is_not_a_credential_bearing_url_is_shown_as_written(
    store: ConfigStore, client: TestClient
) -> None:
    """The other half of fail-open display. `url_credential` answers None
    to anything that is not a URL carrying a credential, so a URL without
    one, a URL whose parameters are ordinary, and prose that merely holds
    an address are all shown byte for byte. Without this the four cases
    above would pass on a walk that mangled every string it met.
    """
    untouched = {
        "base_url": f"https://{HOST}/v1",
        "model": "qwen3:8b",
        "note": f"see https://{HOST}/docs?model=small&page=2 for the options",
        "connection": {"endpoint": f"https://{HOST}/hook?model=small", "retries": 2},
        # A key that is a URL and carries nothing, which is the control
        # the key rule needs of its own: what is stripped is a
        # credential, never a key that merely looks like an address.
        f"https://{HOST}/plain?model=small": "ordinary",
    }
    _plant(
        store,
        "provider",
        ("llm", "plain"),
        ProviderConfig.model_validate({"type": "openai_compatible", "egress": False, **untouched}),
    )

    entity = client.get("/providers/llm/plain").json()["entity"]

    assert {key: entity[key] for key in untouched} == untouched


# The keys two of them sanitize alike
#
# Nothing about a stored row stops two keys from reaching one spelling
# once the credential is out of them, and a mapping comprehension would
# have answered with the last of them. A read is a fragment a write of
# it accepts back, so a pair silently missing from one is a pair an
# operator deletes by re-importing what they were shown.


def test_two_keys_that_sanitize_alike_are_both_kept_and_told_apart(
    store: ConfigStore, client: TestClient
) -> None:
    """The rule `views._shown_mapping` documents, at both of its call
    sites: the first claimant keeps the spelling and the next takes
    `#2`, in the order the row holds its keys, and no pair is dropped.

    Both sites, because they are two different builders over one helper:
    a provider's top-level options are merged into a body that already
    holds the declared fields, and a nested structure is a mapping built
    from nothing.
    """
    _plant(
        store,
        "provider",
        ("llm", "collide"),
        ProviderConfig.model_validate(
            {
                "type": "openai_compatible",
                "base_url": f"https://{HOST}/v1",
                "model": "qwen3:8b",
                "egress": False,
                f"https://user:{KEY_SENTINEL}@{HOST}/same": "first",
                f"https://other:{KEY_SENTINEL}@{HOST}/same": "second",
                "connection": {
                    f"https://user:{KEY_SENTINEL}@{HOST}/deep": "one",
                    f"https://other:{KEY_SENTINEL}@{HOST}/deep": "two",
                },
            }
        ),
    )

    response = client.get("/providers/llm/collide")
    entity = response.json()["entity"]

    assert entity[f"https://{HOST}/same"] == "first"
    assert entity[f"https://{HOST}/same#2"] == "second"
    assert entity["connection"] == {
        f"https://{HOST}/deep": "one",
        f"https://{HOST}/deep#2": "two",
    }
    # Nothing dropped, which is the half a deterministic rule exists for.
    assert len(entity["connection"]) == 2
    _carries_no_sentinel(response.text, str(dict(response.headers)))


# The identity itself, which is the third thing a view hands back
#
# A name is held to one URL path segment at WRITE time only, which
# `store._check_addressable` records in as many words: a row written
# before that rule still boots and still appears in a
# whole-configuration read. It appeared with the credential in it, as a
# map key in the document and in every listing, in the secret locations
# beside them, and in the two projections that are a name rather than an
# entity. The identifier below is planted into all of them at once.

HISTORIC = f"https://user:{KEY_SENTINEL}@{HOST}/named"
HISTORIC_SHOWN = f"https://{HOST}/named"


@pytest.fixture
def historic(store: ConfigStore) -> ConfigStore:
    """A deployment whose provider, agent, device binding, default agent
    and stored secret slot are all named the way no write would allow."""
    _plant(store, "provider", ("llm", HISTORIC), ProviderConfig(type="mock"))
    _plant(store, "agent", (HISTORIC,), AgentConfig(prompt="hi", llm=HISTORIC))
    planted(
        store,
        insert(schema.devices).values(mac="aa:bb:cc:dd:ee:ff", agents=[HISTORIC]),
        insert(schema.domain_settings).values(key=schema.DEFAULT_AGENT_KEY, value=HISTORIC),
        # A slot is addressed by the same rule a name is, so it has the
        # same history. The envelope is never opened by a read: what a
        # view shows is the slot and what it shadows.
        schema.providers.update()
        .where(schema.providers.c.name == HISTORIC)
        .values(secrets={HISTORIC: {"v": 1, "ct": "x", "key": "k"}}),
    )
    return store


def test_the_historic_identifier_really_is_one_no_write_would_accept() -> None:
    """The guard, and the measured half of the trade-off in one.

    Such a name carries a credential, which is why it may not be shown.
    It also holds a slash, because `://` does, and a name holding a
    slash is what the addressability rule refuses: a row named this way
    cannot be fetched or deleted over the API, so what a sanitized
    display costs is a spelling that was never a working handle.
    """
    assert url_credential(HISTORIC) is not None
    assert without_url_credential(HISTORIC) == HISTORIC_SHOWN
    assert "/" in HISTORIC


def test_the_whole_configuration_document_names_nothing_verbatim(
    historic: ConfigStore,
) -> None:
    """Every identity-keyed map and both name-shaped projections, in one
    answer: the providers by stage, the agents, the device's bindings,
    the default agent and the secret locations."""
    document = views.config(historic.load())
    rendered = _rendered(document)

    config = document["config"]
    assert list(config["providers"]["llm"]) == [HISTORIC_SHOWN]
    assert list(config["agents"]) == [HISTORIC_SHOWN]
    assert config["devices"] == {"aa:bb:cc:dd:ee:ff": [HISTORIC_SHOWN]}
    assert config["default_agent"] == HISTORIC_SHOWN
    assert [stored["slot"] for stored in document["secrets"]] == [HISTORIC_SHOWN]
    assert [stored["identity"] for stored in document["secrets"]] == [f"llm.{HISTORIC_SHOWN}"]
    _carries_no_sentinel(rendered)


def test_the_listings_and_the_name_projections_name_nothing_verbatim(
    historic: ConfigStore,
) -> None:
    """The same identities through the reads that answer one kind at a
    time, which is where a listing's key and an envelope's secret slot
    are built."""
    snapshot = historic.load()

    assert list(views.providers(snapshot)["llm"]) == [HISTORIC_SHOWN]
    assert list(views.agents(snapshot)) == [HISTORIC_SHOWN]
    assert views.devices(snapshot)["aa:bb:cc:dd:ee:ff"]["entity"] == {
        "agents": [HISTORIC_SHOWN]
    }
    assert views.default_agent(snapshot.domain.default_agent) == {"name": HISTORIC_SHOWN}
    assert list(views.providers(snapshot)["llm"][HISTORIC_SHOWN]["secrets"]) == [
        HISTORIC_SHOWN
    ]
    for view in (views.providers, views.agents, views.devices, views.listing):
        rendered = _rendered(
            view("agent", snapshot) if view is views.listing else view(snapshot)
        )
        _carries_no_sentinel(rendered)


def test_the_api_and_the_cli_name_nothing_verbatim(
    historic: ConfigStore,
    client: TestClient,
    run,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The two renderings an operator actually meets, on both process
    streams and in both log formats."""
    for path in ("/config", "/providers", "/agents", "/devices", "/default-agent"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert HISTORIC_SHOWN in response.text, path
        _carries_no_sentinel(response.text, str(dict(response.headers)))

    with caplog.at_level(logging.DEBUG):
        assert run("show") == 0

    printed = capsys.readouterr()
    assert HISTORIC_SHOWN in printed.out
    _carries_no_sentinel(printed.out, printed.err, *_logged(caplog))


def test_two_historic_names_that_sanitize_alike_are_both_kept(
    store: ConfigStore,
) -> None:
    """The collision rule reaches the identity maps too, which is the
    other half of routing them through the same builder. Two rows are
    two rows in the answer, whatever their names shorten to."""
    for user in ("one", "two"):
        _plant(
            store,
            "provider",
            ("llm", f"https://{user}:{KEY_SENTINEL}@{HOST}/named"),
            ProviderConfig(type="mock"),
        )

    listed = views.providers(store.load())["llm"]

    assert list(listed) == [HISTORIC_SHOWN, f"{HISTORIC_SHOWN}#2"]
    _carries_no_sentinel(_rendered(listed))


def test_a_device_mac_cannot_carry_one_because_the_load_path_refuses_it(
    store: ConfigStore,
) -> None:
    """Why the devices map's KEY is the one identity with no strip on
    it, asserted rather than assumed.

    Every other identity here is checked at write time only, so a
    planted one reaches a view. A MAC is checked on the way OUT as well:
    the row is refused by the load, so it never reaches a view at all
    and a strip on that key would be code nothing can run. The refusal
    names the rule and not the value.
    """
    planted(store, insert(schema.devices).values(mac=HISTORIC, agents=["sam"]))

    with pytest.raises(StorageError) as caught:
        store.load()

    assert "a MAC address is six colon-separated hex pairs" in str(caught.value)
    _carries_no_sentinel(chain(caught.value))


# The refusals, which SAY an identity rather than show one
#
# #382 settled that a boot refusal about the stored half names the entry
# it refused on, in full, because that is the vocabulary the write, the
# API and this deployment's own documents already speak: a refusal
# saying less about a stored world than the write that stored it is
# worth nothing to the operator holding it. That makes a refusal a place
# an identity leaves this package by, after a field, a mapping key and
# the name projections above, and its sentence goes somewhere none of
# those go: a server's stderr as it fails to start, which is read by an
# operator, by a container log and by whatever collects one.
#
# So the same strip is on it, at the same one door. The cases below are
# the four sentences a stored identity can reach: the reference check
# and the completeness check, which are the composition's own; the
# location a per-row read refusal is built from; and the walk over a
# validation error's locations, which is the half this issue converged.

# A provider name nothing defines, so that the reference sentence is
# about the entry rather than about the deployment being empty. Not
# quoted back by that refusal, which is the rule it has always kept.
GONE = "no-such-provider"


@pytest.fixture
def unbootable(store: ConfigStore) -> ConfigStore:
    """A deployment named the way no write would allow, holding the one
    mistake that refuses a boot: an agent whose stage names a provider
    that is not there.

    The provider planted beside it is what the refusal's `defined:` half
    lists, so one sentence carries the identity twice, once as the
    location and once in the list of what could have been meant.
    """
    _plant(store, "provider", ("llm", HISTORIC), ProviderConfig(type="mock"))
    _plant(store, "agent", (HISTORIC,), AgentConfig(prompt="hi", llm=GONE))
    planted(
        store,
        insert(schema.domain_settings).values(
            key=schema.DEFAULT_AGENT_KEY, value=HISTORIC
        ),
    )
    return store


def test_a_boot_refusal_names_the_stored_entry_without_its_credential(
    unbootable: ConfigStore,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The whole boot, from the file half to the composition, which is
    what a server runs and what a reload runs again."""
    monkeypatch.delenv("VINGA_CONFIG", raising=False)

    with caplog.at_level(logging.DEBUG), pytest.raises(ConfigError) as caught:
        load_boot_config()

    message = str(caught.value)
    assert f"agents.{HISTORIC_SHOWN}.llm: names no llm provider that exists" in message
    assert f"(defined: {HISTORIC_SHOWN})" in message
    assert GONE not in message
    _carries_no_sentinel(chain(caught.value), *_logged(caplog))


def test_the_boot_refusal_reaches_stderr_carrying_no_credential(
    unbootable: ConfigStore,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Where an operator actually meets it: the entry point prints the
    sentence on stderr and leaves with 1, before logging is configured
    at all, so this is the one surface the boot refusal has and both
    streams are held to it."""
    monkeypatch.delenv("VINGA_CONFIG", raising=False)

    with caplog.at_level(logging.DEBUG):
        assert serving.run(None) == 1

    printed = capsys.readouterr()
    assert f"agents.{HISTORIC_SHOWN}.llm" in printed.err
    _carries_no_sentinel(printed.out, printed.err, *_logged(caplog))


def test_the_completeness_refusal_lists_the_names_without_their_credential(
    store: ConfigStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The composition's other sentence, which lists the agents a
    default could be set to. A list of stored names is the same
    publication as one of them."""
    monkeypatch.delenv("VINGA_CONFIG", raising=False)
    _plant(store, "agent", (HISTORIC,), AgentConfig(prompt="hi"))

    with pytest.raises(ConfigError) as caught:
        load_boot_config()

    assert f"set it to one of: {HISTORIC_SHOWN}" in str(caught.value)
    _carries_no_sentinel(chain(caught.value))


def test_an_unreadable_row_names_its_entry_without_its_credential(
    store: ConfigStore,
) -> None:
    """The location every per-row refusal is composed from, which is
    built by the store rather than walked out of a validation error. A
    row that will not read is the case that only a stored name can be
    in: the write path refuses this name outright."""
    planted(store, insert(schema.agents).values(name=HISTORIC, body='{"llm": ""}'))

    with pytest.raises(StorageError) as caught:
        store.load()

    assert f"agents.{HISTORIC_SHOWN}: " in str(caught.value)
    _carries_no_sentinel(chain(caught.value))


def test_a_composed_locations_identity_is_named_without_its_credential() -> None:
    """The walk over a validation error's own locations, which is what
    #382 moved onto the shared policy.

    Composed from a mapping rather than from a store, which is the shape
    a composition with no database behind it takes, because that is the
    route that reaches the field validators with a stored identity in
    the location rather than with an entry already validated row by row.
    """
    with pytest.raises(ConfigError) as caught:
        compose_config(
            FileConfig(),
            {"agents": {HISTORIC: {"prompt": "hi", "llm": ""}}},
            "the test's database",
        )

    assert f"agents.{HISTORIC_SHOWN}.llm: " in str(caught.value)
    _carries_no_sentinel(chain(caught.value))


# The record path, which asks the same question and keeps its answer


def test_a_record_of_such_a_provider_carries_no_credential_in_a_key() -> None:
    """A manifest is written beside a capture and into a conversation's
    session row and outlives the conversation, so it is held to the
    rule the display is held to and by the same helper. The value half
    has been stripped since #279; the key half was not, and a record
    keyed by what the caller wrote carried the credential the value no
    longer had (#408).
    """
    entry = ProviderConfig.model_validate(
        {
            "type": "openai_compatible",
            "base_url": f"https://{HOST}/v1",
            "model": "qwen3:8b",
            f"https://user:{KEY_SENTINEL}@{HOST}/top": "ordinary",
            "connection": {f"https://{HOST}/deep?auth={KEY_SENTINEL}": "ordinary"},
        }
    )

    record = views.provider_record(entry)

    assert record[f"https://{HOST}/top"] == "ordinary"
    assert record["connection"] == {f"https://{HOST}/deep": "ordinary"}
    _carries_no_sentinel(_rendered(record))


# The write, which no longer lets one in


# One case per door a caller can write a mapping key through: a
# provider's options at the top, a structure passed through below them,
# and an MCP server's two keyed groups. The refusal names the entry, or
# the declared group inside it, and never the key: the key IS the
# credential here, so quoting it back would be the leak the check exists
# to prevent.
REFUSED_KEYS = (
    (
        "/providers/llm/fresh",
        {
            "type": "openai_compatible",
            "base_url": f"https://{HOST}/v1",
            "model": "m",
            f"https://user:{KEY_SENTINEL}@{HOST}/top": "ordinary",
        },
        'an option key of "providers.llm.fresh"',
    ),
    (
        "/providers/llm/fresh",
        {
            "type": "openai_compatible",
            "base_url": f"https://{HOST}/v1",
            "model": "m",
            "connection": {f"https://{HOST}/deep?auth={KEY_SENTINEL}": "ordinary"},
        },
        'an option key of "providers.llm.fresh"',
    ),
    (
        "/mcp-servers/fresh",
        {
            "transport": "stdio",
            "command": "uvx",
            "env": {f"https://user:{KEY_SENTINEL}@{HOST}/spawn": "ordinary"},
        },
        'a key in "mcp_servers.fresh.env"',
    ),
    (
        "/mcp-servers/fresh",
        {
            "transport": "streamable_http",
            "url": f"https://{HOST}/mcp",
            # Userinfo rather than a parameter, because a header key
            # spelled `?auth=` is secret-shaped by the wider fragment
            # set and the inline-secret rule would answer first.
            "headers": {f"https://user:{KEY_SENTINEL}@{HOST}/h": "ordinary"},
        },
        'a key in "mcp_servers.fresh.headers"',
    ),
)

REFUSED_IDS = ["provider-top", "provider-nested", "mcp-env", "mcp-headers"]


@pytest.mark.parametrize(("path", "written", "where"), REFUSED_KEYS, ids=REFUSED_IDS)
def test_a_url_credential_in_a_key_is_refused_and_never_quoted_back(
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
    path: str,
    written: dict[str, object],
    where: str,
) -> None:
    """What used to be accepted at every one of these doors.

    The sentinel is the key's, not the value's, and it holds no fragment
    of the inline-secret tuples on purpose: with the other sentinel the
    key would be refused for looking like a secret and this rule would
    never be reached, so the case would be green over an unguarded door.
    """
    with caplog.at_level(logging.DEBUG):
        response = client.put(path, json=written)

    assert response.status_code == 422
    detail = refusal_body(response.json(), 422)
    assert detail.startswith(where)
    assert "a key is a name and not an address" in detail
    _carries_no_sentinel(response.text, str(dict(response.headers)), *_logged(caplog))
    # And nothing of the refused write landed.
    assert client.get(path).status_code == 404


def test_the_refusal_carries_the_key_on_nothing_it_raises(store: ConfigStore) -> None:
    """The exception the response is one rendering of, walked the way
    this repository walks one: the message, the arguments, what the
    attributes hold and the same again behind every cause."""
    with pytest.raises(ConfigError) as caught:
        store.set_provider(
            "llm",
            "fresh",
            {
                "type": "openai_compatible",
                "base_url": f"https://{HOST}/v1",
                "model": "m",
                f"https://user:{KEY_SENTINEL}@{HOST}/top": "ordinary",
            },
        )

    _carries_no_sentinel(chain(caught.value), str(caught.value.problems))


def test_the_cli_refuses_such_a_key_on_both_streams(
    run, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    """The same refusal where an operator meets it, held to the two
    streams the process writes and the two formats the log has."""
    carrying = (
        "mcp_servers:\n"
        "  fresh:\n"
        "    transport: stdio\n"
        "    command: uvx\n"
        "    env:\n"
        f"      https://user:{KEY_SENTINEL}@{HOST}/spawn: ordinary\n"
    )

    with caplog.at_level(logging.DEBUG):
        assert run("import", "-f", "-", stdin=carrying) == 1

    printed = capsys.readouterr()
    assert 'a key in "mcp_servers.fresh.env"' in printed.err
    _carries_no_sentinel(printed.out, printed.err, *_logged(caplog))
