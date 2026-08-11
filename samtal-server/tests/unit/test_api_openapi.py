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
