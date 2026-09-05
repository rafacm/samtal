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

from tests.support.config_cli import document, runner
from tests.support.stores import body, planted
from vinga_server import logs
from vinga_server.config import entities, views
from vinga_server.config.api import build_api
from vinga_server.config.models import (
    DatabaseConfig,
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


# The four shapes, across both kinds and two depths.
#
# `userinfo` and the credential-shaped query parameter are the two
# answers `url_credential` gives, and `auth` and `authorization` are the
# two spellings #279 added to it, so each of them is planted once. The
# nested one is a provider option holding a structure, which is a shape
# a provider entry may hold because its options are passed through to
# the implementation, and it is the depth a per-field rule would have
# missed.
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
        _plant(store, row)
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


def _plant(store: ConfigStore, row: Legacy) -> None:
    """One row holding a value today's write path refuses, written as a
    row.

    The body is the repository's own dump of the model, so what is
    unlawful about the row is the value and nothing else: a plant that
    hand-wrote the JSON would be exercising the parser as well.
    """
    descriptor = entities.descriptor(row.kind)
    table = getattr(schema, descriptor.table)
    columns = dict(zip(descriptor.addressing, row.identity, strict=True))
    where = [table.c[column] == value for column, value in columns.items()]
    planted(
        store,
        table.delete().where(*where),
        insert(table).values(**columns, body=body(row.entry)),
    )


def _rendered(value: object) -> str:
    """One view as a caller receives it, serialized, which is the form a
    substring assertion is honest about: a credential nested three
    mappings down is in the answer exactly as much as one at the top."""
    return json.dumps(value, sort_keys=True, default=str)


def _shows_the_address_without_the_credential(rendered: str, addresses: tuple[str, ...]) -> None:
    for address in addresses:
        assert address in rendered
    assert SENTINEL not in rendered


# The planted rows themselves, before any display


@pytest.mark.parametrize("row", LEGACY, ids=IDS)
def test_the_planted_row_really_holds_a_credential_a_write_would_refuse(
    row: Legacy,
) -> None:
    """The guard on every case below. These rows are constructed rather
    than written, so nothing but this says they hold what the display is
    being asked to strip, and a typo in a planted address would leave the
    whole file green over a store holding nothing interesting."""
    held = [
        value
        for value in _strings(row.entry.model_dump())
        if url_credential(value) is not None
    ]

    assert held, row.identity
    for value in held:
        assert SENTINEL in value
        assert without_url_credential(value) in row.shown


def _strings(value: object) -> Iterator[str]:
    if isinstance(value, dict):
        for nested in value.values():
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
    assert SENTINEL not in str(dict(response.headers))


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
    assert SENTINEL not in printed.err
    text = logging.Formatter(logs.TEXT_FORMAT)
    for record in caplog.records:
        assert SENTINEL not in logs.JsonFormatter().format(record)
        assert SENTINEL not in text.format(record)


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
    assert SENTINEL not in printed.err
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

    assert SENTINEL not in exported
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
    assert SENTINEL not in printed.out
    assert SENTINEL not in printed.err


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
    }
    _plant(
        store,
        Legacy(
            kind="provider",
            identity=("llm", "plain"),
            entry=ProviderConfig(type="openai_compatible", egress=False, **untouched),
            shown=(),
        ),
    )

    entity = client.get("/providers/llm/plain").json()["entity"]

    assert {key: entity[key] for key in untouched} == untouched
