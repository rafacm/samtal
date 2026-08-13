"""The committed OpenAPI document.

The document is the machine-readable contract, so "it generates" has to
mean more than "it is JSON": it is validated with openapi-spec-validator
here, which is what a client generator would do to it, and every $ref in
it has to resolve. The committed copy is diffed against a fresh
rendering the way the markdown reference already is, once in CI and once
here so a stale copy fails in the suite rather than after a push.

It is also deliberately deterministic. A document that varied between
two runs would turn the drift check red on an unrelated change, so the
fixed contract version and the double-render check are both pinned.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from openapi_spec_validator import validate
from openapi_spec_validator.readers import read_from_filename

from samtal_server.config import cli, docgen
from samtal_server.config.api import API_VERSION, BEARER_SCHEME, MOUNT_PATH
from samtal_server.config.secrets import MASTER_KEY_ENV

COMMITTED = Path(__file__).resolve().parents[3] / "docs" / "reference" / "api-openapi.json"

REGENERATE = (
    "docs/reference/api-openapi.json is stale; regenerate it with "
    "`uv run samtal-server config openapi > ../docs/reference/api-openapi.json`"
)


@pytest.fixture
def run(monkeypatch: pytest.MonkeyPatch):
    """The command renders routes and nothing else, so the fixture takes
    away everything else: no config file, no writable database
    directory, no encryption key, and no API token either."""
    monkeypatch.delenv("SAMTAL_CONFIG", raising=False)
    monkeypatch.delenv(MASTER_KEY_ENV, raising=False)
    monkeypatch.delenv("SAMTAL_API_SECRET", raising=False)
    monkeypatch.setenv("SAMTAL_SERVER__DATABASE__DIR", "/nowhere/at/all")

    def _run(*argv: str) -> int:
        return cli.main(list(argv))

    return _run


def _refs(node: Any) -> list[str]:
    if isinstance(node, dict):
        found = [node["$ref"]] if isinstance(node.get("$ref"), str) else []
        return found + [ref for value in node.values() for ref in _refs(value)]
    if isinstance(node, list):
        return [ref for item in node for ref in _refs(item)]
    return []


def _resolve(document: dict, ref: str) -> object:
    assert ref.startswith("#/"), f"{ref} is not a local reference"
    node: Any = document
    for part in ref.removeprefix("#/").split("/"):
        assert isinstance(node, dict) and part in node, f"{ref} does not resolve"
        node = node[part]
    return node


def test_the_committed_document_matches_the_routes() -> None:
    """The same check CI runs."""
    assert COMMITTED.read_text(encoding="utf-8") == docgen.openapi(), REGENERATE


def test_the_document_is_deterministic() -> None:
    assert docgen.openapi() == docgen.openapi()


def test_the_committed_document_is_a_valid_openapi_document() -> None:
    """What a client generator will accept, rather than what happens to
    parse as JSON."""
    document, _ = read_from_filename(str(COMMITTED))
    validate(document)


def test_every_reference_in_the_document_resolves() -> None:
    """Request schemas are injected rather than collected by FastAPI, so
    a $ref that names a component nobody registered is the failure this
    catches."""
    document = json.loads(docgen.openapi())
    for ref in _refs(document):
        _resolve(document, ref)


def test_the_document_carries_the_mount_prefix() -> None:
    """A mounted application renders its internal paths, so the prefix
    has to be said somewhere, and `servers` is where OpenAPI says it."""
    document = json.loads(docgen.openapi())

    assert document["servers"] == [{"url": MOUNT_PATH}]


def test_the_version_is_the_contract_version_not_the_package_version() -> None:
    from samtal_server import __version__

    document = json.loads(docgen.openapi())

    assert document["info"]["version"] == API_VERSION
    assert document["info"]["version"] != __version__


def test_the_bearer_scheme_is_stated_and_required() -> None:
    """Enforcement is middleware, so nothing derives the scheme from a
    dependency: the document states it and requires it document-wide."""
    document = json.loads(docgen.openapi())
    scheme = document["components"]["securitySchemes"][BEARER_SCHEME]

    assert scheme["type"] == "http"
    assert scheme["scheme"] == "bearer"
    assert document["security"] == [{BEARER_SCHEME: []}]


def test_the_document_describes_every_route_the_api_serves() -> None:
    """The document is generated from the routes, so this is what makes
    a route added without a thought for the contract visible. The methods
    are asserted per path as well: which of them a resource answers to is
    the contract, and a PUT that never reached the document would read
    like a resource nobody may write."""
    paths = json.loads(docgen.openapi())["paths"]

    assert {path: sorted(operations) for path, operations in paths.items()} == {
        "/config": ["get"],
        "/providers": ["get"],
        "/providers/{stage}/{name}": ["delete", "get", "put"],
        "/providers/{stage}/{name}/secrets/{slot}": ["delete", "put"],
        "/mcp-servers": ["get"],
        "/mcp-servers/{name}": ["delete", "get", "put"],
        "/mcp-servers/{name}/secrets/{slot}": ["delete", "put"],
        "/agents": ["get"],
        "/agents/{name}": ["delete", "get", "put"],
        "/agent-defaults": ["get", "put"],
        "/devices": ["get"],
        "/devices/pending": ["get"],
        "/devices/pending/{code}": ["post"],
        "/devices/{mac}": ["delete", "get", "put"],
        "/default-agent": ["delete", "get", "put"],
        # The runtime namespace, which is deliberately not a route
        # inside an entity namespace: an entry may be named `status`.
        "/runtime/mcp-servers": ["get"],
        "/runtime/mcp-servers/reload": ["post"],
    }


def test_the_pending_listing_is_described_before_the_mac_route() -> None:
    """Starlette matches in registration order, and the document is
    generated from that order, so a route registered the wrong way round
    shows up here as a contract change rather than as a puzzling 422 at
    runtime. What a request actually meets is asserted in
    test_config_api_pending.py."""
    paths = list(json.loads(docgen.openapi())["paths"])

    assert paths.index("/devices/pending") < paths.index("/devices/{mac}")


def test_a_write_declares_the_entity_schema_it_takes() -> None:
    """The running code receives a raw object, so nothing about the body
    reaches the document by itself. Each write names the component it
    accepts, and the reference is the whole of it: FastAPI deep-merges
    `openapi_extra` into what it generated, and a `$ref` with siblings
    beside it is at best ignored."""
    paths = json.loads(docgen.openapi())["paths"]

    for path, method, model in (
        ("/providers/{stage}/{name}", "put", "ProviderConfig"),
        ("/mcp-servers/{name}", "put", "McpServerConfig"),
        ("/agents/{name}", "put", "AgentConfig"),
        ("/agent-defaults", "put", "AgentDefaults"),
        ("/devices/{mac}", "put", "DeviceBinding"),
        # Add-by-code takes the same body as bind-by-MAC: the code names
        # the device, and the agents are the same argument.
        ("/devices/pending/{code}", "post", "DeviceBinding"),
        ("/default-agent", "put", "DefaultAgentName"),
        ("/providers/{stage}/{name}/secrets/{slot}", "put", "SecretValue"),
        ("/mcp-servers/{name}/secrets/{slot}", "put", "SecretValue"),
    ):
        body = paths[path][method]["requestBody"]
        assert body["required"] is True, path
        assert body["content"]["application/json"]["schema"] == {
            "$ref": f"#/components/schemas/{model}"
        }, path

    # And a delete takes no body at all.
    assert "requestBody" not in paths["/agents/{name}"]["delete"]


def test_the_document_permits_no_body_the_api_refuses() -> None:
    """A contract looser than the code is one a client generator builds
    the wrong request from. The empty secret is the case: the parser and
    the repository both refuse it, so the schema says so too."""
    schemas = json.loads(docgen.openapi())["components"]["schemas"]

    assert schemas["SecretValue"]["properties"]["secret"]["minLength"] == 1
    assert schemas["SecretValue"]["required"] == ["secret"]
    assert schemas["SecretValue"]["additionalProperties"] is False


def test_a_write_answers_with_what_it_did_and_when_it_applies() -> None:
    """Decision 5's contract, in the document: a write is acknowledged
    rather than silent, and the acknowledgement carries the restart
    sentence."""
    paths = json.loads(docgen.openapi())["paths"]

    for path, method in (("/agents/{name}", "put"), ("/agents/{name}", "delete")):
        ok = paths[path][method]["responses"]["200"]
        assert ok["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/Acknowledgement"
        }, (path, method)


def test_every_refusal_a_read_can_answer_with_is_described() -> None:
    """A status code is part of this contract, so a client generator has
    to find all of them, each carrying the one error shape."""
    read = json.loads(docgen.openapi())["paths"]["/providers/{stage}/{name}"]["get"]

    assert set(read["responses"]) == {"200", "401", "404", "409", "422", "500"}
    for status in ("401", "404", "409", "422", "500"):
        schema = read["responses"][status]["content"]["application/json"]["schema"]
        assert schema == {"$ref": "#/components/schemas/Problem"}


def test_every_field_a_read_always_answers_with_is_required() -> None:
    """Nullable is not optional. Each of these is in every response, so
    a client is never left telling "the server said null" apart from
    "the server did not say", which is a third state nothing can act
    on."""
    schemas = json.loads(docgen.openapi())["components"]["schemas"]

    assert schemas["DefaultAgent"]["required"] == ["name"]
    assert schemas["SecretSlot"]["required"] == ["shadows"]
    assert set(schemas["StoredSecretLocation"]["required"]) == {
        "kind",
        "identity",
        "slot",
        "shadows",
    }
    assert set(schemas["Envelope"]["required"]) == {"entity", "secrets"}
    assert schemas["ConfigDocument"]["required"] == ["config", "secrets"]
    assert set(schemas["PendingDevice"]["required"]) == {
        "mac",
        "client_id",
        "board",
        "firmware",
        "first_seen",
        "last_seen",
        "expires_at",
    }
    assert set(schemas["McpServerStatus"]["required"]) == {
        "state",
        "reason",
        "since",
        "tools",
        "grants",
    }


def test_the_entity_schemas_are_registered_with_their_definitions() -> None:
    """The write routes will receive raw objects, so FastAPI collects
    none of these models on its own: they are injected, with their
    nested definitions hoisted beside them, which is what makes a $ref
    to one of them resolve."""
    schemas = json.loads(docgen.openapi())["components"]["schemas"]

    for name in (
        "ProviderConfig",
        "McpServerConfig",
        "AgentConfig",
        "AgentDefaults",
        # The three argument-shaped bodies, injected the same way and for
        # the same reason: they document a shape the runtime parser
        # enforces, and are deliberately not declared as body types.
        "DeviceBinding",
        "DefaultAgentName",
        "SecretValue",
    ):
        assert name in schemas
    # Nested one level down in pydantic's own output, and a component of
    # its own here.
    assert "FillerConfig" in schemas
    assert "$defs" not in schemas["AgentConfig"]


def test_the_description_admits_the_provider_options_contract() -> None:
    """The one part no schema can describe, in the words the markdown
    reference uses for it."""
    description = json.loads(docgen.openapi())["info"]["description"]

    assert "#88" in description
    assert "passed through rather than declared" in description


def test_the_command_needs_no_database_no_key_and_no_token(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The directory the fixture names cannot be created, no key is set
    and no API token either, so a command that opened the database or
    built the gate would fail here rather than print."""
    assert run("openapi") == 0

    printed = capsys.readouterr().out
    assert json.loads(printed)["openapi"].startswith("3.")
    assert printed == docgen.openapi()
