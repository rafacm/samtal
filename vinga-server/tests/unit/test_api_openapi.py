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
import re
from pathlib import Path
from typing import Any

import pytest
from openapi_spec_validator import validate
from openapi_spec_validator.readers import read_from_filename

from vinga_server.config import api, cli, docgen
from vinga_server.config.api import (
    API_VERSION,
    BEARER_SCHEME,
    MOUNT_PATH,
    PROBLEM_MEDIA_TYPE,
    MissingDescriptionError,
)
from vinga_server.config.secrets import MASK, MASTER_KEY_ENV

COMMITTED = Path(__file__).resolve().parents[3] / "docs" / "reference" / "api-openapi.json"

REGENERATE = (
    "docs/reference/api-openapi.json is stale; regenerate it with "
    "`uv run vinga-server config openapi > ../docs/reference/api-openapi.json`"
)


@pytest.fixture
def run(monkeypatch: pytest.MonkeyPatch):
    """The command renders routes and nothing else, so the fixture takes
    away everything else: no config file, no reachable database, no
    encryption key, and no API token either."""
    monkeypatch.delenv("VINGA_CONFIG", raising=False)
    monkeypatch.delenv(MASTER_KEY_ENV, raising=False)
    monkeypatch.delenv("VINGA_API_SECRET", raising=False)
    # A port nothing listens on, so a command that opened the database
    # would refuse here rather than print.
    monkeypatch.setenv("VINGA_DB_PORT", "1")

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
    from vinga_server import __version__

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
        "/prompt-fragments": ["get"],
        "/prompt-fragments/{name}": ["delete", "get", "put"],
        "/agents": ["get"],
        "/agents/{name}": ["delete", "get", "put"],
        "/agent-defaults": ["get", "put"],
        "/devices": ["get"],
        "/devices/pending": ["get"],
        "/devices/pending/{code}": ["post"],
        "/devices/{mac}": ["delete", "get", "put"],
        "/default-agent": ["delete", "get", "put"],
        # The whole domain half in one request, which is a write of the
        # configuration rather than a runtime action: it lands in the
        # store and a running server meets it at the reload that follows.
        "/apply": ["post"],
        # The runtime namespace, which is deliberately not a route
        # inside an entity namespace: an entry may be named `status`,
        # and an agent may be named `prompt`.
        "/runtime/agents/{name}/prompt": ["get"],
        "/runtime/mcp-servers": ["get"],
        # And the two that span both sides, under `/runtime/config/`
        # rather than beside the entity reads: one answers what is
        # stored and not yet served, the other puts it in front of the
        # server, and the two-segment path is what lets an operator's
        # read and its apply sit beside each other.
        "/runtime/config/diff": ["get"],
        "/runtime/config/reload": ["post"],
        # The conversation store's reads and its two erasures. Their
        # route functions live in vinga_server/conversations/api.py and
        # are registered on the same application, which is what puts
        # them here: a route registered by `build_api` instead would be
        # served without ever reaching this document.
        #
        # The two DELETEs overlap deliberately: the addressed form is
        # what the noun grammar wants and the selector form is the purge
        # #282 settled, and both go through one helper.
        "/sessions": ["delete", "get"],
        "/sessions/{session}": ["delete", "get"],
        "/sessions/{session}/turns": ["get"],
    }


def test_the_description_names_only_namespaces_the_routes_serve() -> None:
    """The document's prose is contract too, and it is the half nothing
    generates: the reads moved to `/sessions` while the description went
    on calling their namespace `/conversations`, and the committed
    document shipped that sentence to every reader of the contract.

    So every path the prose spells in backticks has to be one this
    document serves. Derived from the paths rather than listed here, so
    a namespace that arrives later is described or caught rather than
    pinned twice.
    """
    document = json.loads(docgen.openapi())
    served = {path.split("/")[1] for path in document["paths"]}
    # The first segment is the namespace; a method may share the
    # backticks with the path it names, as `GET /runtime/config/diff`.
    described = {
        token.split("/")[1]
        for token in re.findall(
            r"`[A-Z ]*(/[A-Za-z0-9/{}_-]*)`", document["info"]["description"]
        )
    }

    assert described, "the description names no namespace at all"
    assert described <= served, sorted(described - served)
    # And the store's reads are described, so the paragraph cannot go
    # missing and leave this green.
    assert "sessions" in described


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
        # The one body that is the whole configuration rather than one
        # entry of it: a partial `DomainConfig`, whose every field has a
        # default, so the schema of the document is the schema of a
        # partial one and no second model states the same shape.
        ("/apply", "post", "DomainConfig"),
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

    # A grant's allow list is the other case: the model refuses an empty
    # one and one that repeats a name, so the array says both. The
    # refusals themselves are exercised over HTTP in
    # tests/unit/test_config_api_writes.py.
    grant = schemas["McpGrant"]
    array = next(
        branch for branch in grant["properties"]["tools"]["anyOf"] if "items" in branch
    )
    assert array["minItems"] == 1
    assert array["uniqueItems"] is True
    assert array["items"]["minLength"] == 1
    assert grant["required"] == ["server"]
    assert grant["additionalProperties"] is False


def test_a_write_answers_with_what_it_did_and_when_it_applies() -> None:
    """Decision 5's contract, in the document: a write is acknowledged
    rather than silent, and the acknowledgement says when it lands."""
    paths = json.loads(docgen.openapi())["paths"]

    for path, method in (("/agents/{name}", "put"), ("/agents/{name}", "delete")):
        ok = paths[path][method]["responses"]["200"]
        assert ok["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/Acknowledgement"
        }, (path, method)


# What the committed contract says a change is waiting for
#
# The drift check above holds the document to the routes and the models,
# and it is exactly as right as they are: a description that says the
# wrong boundary passes it byte for byte. These two say what the
# document has to MEAN, and they are here because a generated client's
# reader acts on it: a contract that declares the agent set start-bound
# tells a UI to put a restart in front of an operator for a change one
# request applies. The whole domain half is one apply's business (#191),
# and this is the surface that has to say so.


def test_the_reload_description_names_the_whole_domain_half() -> None:
    """The route's own description, which is what a client generator
    puts in front of whoever calls it."""
    reload = json.loads(docgen.openapi())["paths"]["/runtime/config/reload"]["post"]
    described = reload["description"]

    assert "the whole\ndomain half" in described
    # Every kind of the half, named rather than summarized: the two an
    # earlier release could not apply are the two a stale sentence would
    # still be excluding.
    for kind in ("provider entries", "MCP entries", "prompt\nfragments",
                 "the agents themselves", "`agent_defaults`"):
        assert kind in described, kind
    # And the one part that is genuinely start-bound, named as the only
    # one: a description that still listed the agent set beside it is
    # what this exists to fail.
    assert "the server section, which is this process's\nown file" in described
    assert "waits for the start" not in described


def test_the_acknowledgement_notice_names_two_boundaries_and_no_start() -> None:
    """And the schema a client reads a write's answer through. Two
    boundaries, because there are two: a device asking, and a reload."""
    schemas = json.loads(docgen.openapi())["components"]["schemas"]
    notice = schemas["Acknowledgement"]["properties"]["notice"]["description"]

    assert "/runtime/config/reload" in notice
    assert "next OTA check or connection" in notice
    assert "Nothing this API writes waits for a server start." in notice


def test_every_refusal_a_read_can_answer_with_is_described() -> None:
    """A status code is part of this contract, so a client generator has
    to find all of them, each carrying the one error shape."""
    read = json.loads(docgen.openapi())["paths"]["/providers/{stage}/{name}"]["get"]

    assert set(read["responses"]) == {"200", "401", "404", "409", "422", "500"}
    for status in ("401", "404", "409", "422", "500"):
        schema = read["responses"][status]["content"][PROBLEM_MEDIA_TYPE]["schema"]
        assert schema == {"$ref": "#/components/schemas/Problem"}


def test_every_refusal_in_the_document_offers_exactly_one_media_type() -> None:
    """Mechanically, over every operation, because the way this goes
    wrong is invisible in a review of the diff.

    FastAPI generates a response's content from the model a route
    declares and deep-merges anything declared beside it, so a refusal
    that named both would advertise an `application/json` body this API
    never sends, in a document a client generator would build against.
    The refusals name a schema reference and no model for that reason,
    and this is what holds it: one content key per refusal, the problem
    media type, resolving to `Problem`.
    """
    document = json.loads(docgen.openapi())

    refusals = [
        (path, method, status, response)
        for path, operations in document["paths"].items()
        for method, operation in operations.items()
        for status, response in operation["responses"].items()
        if not status.startswith("2")
    ]
    assert refusals

    for path, method, status, response in refusals:
        where = (path, method, status)
        assert set(response["content"]) == {PROBLEM_MEDIA_TYPE}, where
        assert response["content"][PROBLEM_MEDIA_TYPE]["schema"] == {
            "$ref": "#/components/schemas/Problem"
        }, where


def test_the_refusal_shape_is_required_whole_and_closed() -> None:
    """Every member on every refusal, and nothing else on any of them.

    The reason is the one the read shapes follow: a member that is
    sometimes absent leaves a client telling "the server said none" from
    "the server did not say", and `errors` is the member that would be
    tempting to omit when a refusal names no field.
    """
    schemas = json.loads(docgen.openapi())["components"]["schemas"]

    assert set(schemas["Problem"]["required"]) == {"title", "status", "detail", "errors"}
    assert schemas["Problem"]["additionalProperties"] is False
    assert set(schemas["FieldError"]["required"]) == {"path", "message"}
    assert schemas["FieldError"]["additionalProperties"] is False


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


def test_every_field_the_conversation_reads_answer_with_is_required() -> None:
    """The same rule, over the store's shapes, where nearly every column
    is nullable: a null is what the storage switches leave behind, and a
    client that cannot tell it from a field the server omitted cannot
    read the switches off the answer either."""
    schemas = json.loads(docgen.openapi())["components"]["schemas"]

    for name in (
        "SessionList",
        "SessionSummary",
        "SessionDetail",
        "SessionTurns",
        "SessionTurn",
        "ToolInvocation",
        "TurnLeg",
    ):
        schema = schemas[name]
        assert set(schema["required"]) == set(schema["properties"]), name


def test_the_conversation_reads_type_what_the_store_types() -> None:
    """A structure the store knows the shape of is that shape in the
    document, not a bare string or an open object: the two closed sets
    come from the tuples `conversations/schema.py` declares, so a token
    added there reaches the contract by being added once, and a handover
    leg is a schema of its own rather than four keys named in prose."""
    from vinga_server.conversations.schema import CLOSE_REASONS, TOOL_SOURCES

    schemas = json.loads(docgen.openapi())["components"]["schemas"]

    assert schemas["ToolInvocation"]["properties"]["source"]["enum"] == list(TOOL_SOURCES)
    for model in ("SessionSummary", "SessionDetail"):
        branches = schemas[model]["properties"]["close_reason"]["anyOf"]
        tokens = [branch for branch in branches if "enum" in branch]
        assert [branch["enum"] for branch in tokens] == [list(CLOSE_REASONS)], model
        # And a token a later release latches is still served: the column
        # is deliberately unconstrained, and a read that refused one
        # would drop a whole page over one row.
        assert {"type": "string"} in branches, model
        assert {"type": "null"} in branches, model

    legs = schemas["SessionTurn"]["properties"]["legs"]["anyOf"]
    array = next(branch for branch in legs if "items" in branch)
    assert array["items"] == {"$ref": "#/components/schemas/TurnLeg"}
    assert set(schemas["TurnLeg"]["required"]) == {
        "agent",
        "text",
        "input_tokens",
        "output_tokens",
    }
    assert schemas["TurnLeg"]["additionalProperties"] is False

    agents = schemas["SessionDetail"]["properties"]["agents"]["anyOf"]
    names = next(branch for branch in agents if "items" in branch)
    assert names["items"] == {"type": "string"}


def test_the_leg_schema_is_the_leg_the_pipeline_records() -> None:
    """The transport shape and the record it is serialized from, held
    equal: the writer copies a leg key for key, so a field added to one
    and not the other would be a leg the document does not describe."""
    from dataclasses import fields

    from vinga_server.conversations.api import TurnLeg
    from vinga_server.conversations.records import TurnLeg as RecordedLeg

    assert set(TurnLeg.model_fields) == {field.name for field in fields(RecordedLeg)}


def test_the_conversation_reads_describe_their_pagination() -> None:
    """The three query arguments are parsed by the routes rather than by
    FastAPI, so what a client is told about them is what these
    descriptions say and nothing is derived from a type."""
    listing = json.loads(docgen.openapi())["paths"]["/sessions"]["get"]
    described = {
        parameter["name"]: parameter["description"] for parameter in listing["parameters"]
    }

    assert set(described) == {"device", "limit", "cursor"}
    assert "1 to 200" in described["limit"]
    assert "50" in described["limit"]
    assert "row id" in described["cursor"]


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


def test_a_typed_types_options_are_reachable_from_the_provider_write() -> None:
    """The structural half of #88's documentation claim, walked the way
    a client would walk it.

    The provider PUT takes its body unread, so it cannot carry a
    discriminated request schema keyed on `type`; what it carries
    instead is a mapping in words. That is only worth anything if the
    components it names are there, so this starts at the route, reads
    the names out of its description, and finds each one in
    `components.schemas` with its leaf fields and their descriptions,
    nested models included.
    """
    from vinga_server.config.provider_options import component_name, declared_options

    document = json.loads(docgen.openapi())
    described = document["paths"]["/providers/{stage}/{name}"]["put"]["description"]
    schemas = document["components"]["schemas"]

    declared = declared_options()
    assert declared, "no type declares options, so this walk asserts nothing"

    for stage, type_name, model in declared:
        component = component_name(stage, type_name)
        # The route says which component to read, naming the stage and
        # the type that select it.
        assert component in described
        assert f"`type: {type_name}`" in described
        assert f"`{stage}` stage" in described

        assert component in schemas
        # And the component is the whole contract: every declared field,
        # each with the description the model carries.
        properties = schemas[component]["properties"]
        assert set(properties) == set(model.model_fields)
        assert all(body.get("description") for body in properties.values())

    # The nested leaf, which is the half a one-level injection would
    # lose: `vad_parameters` is a reference, and what it refers to is a
    # component of its own carrying the leaf a fragment writes.
    reference = schemas["AsrFasterWhisperOptions"]["properties"]["vad_parameters"]["$ref"]
    nested = schemas[reference.rsplit("/", 1)[-1]]
    assert nested["properties"]["min_silence_duration_ms"]["description"]


def test_the_description_admits_the_provider_options_contract() -> None:
    """The one part no schema can describe, in the words the markdown
    reference uses for it."""
    description = json.loads(docgen.openapi())["info"]["description"]

    assert "#88" in description
    assert "passed through rather than declared" in description


def test_the_document_states_the_unchanged_value_marker() -> None:
    """What a client sends back to keep a value it was not shown is a
    literal, so the contract has to carry that literal.

    Both places a reader could look: the namespace description, and the
    description of the envelope field the mask appears in. Compared
    against the constant the mask is rendered from rather than against a
    copy of it, since the whole point of stating it is that the document
    and the display cannot come to mean different strings.

    This is also where the two are held equal. `api.py` derives its
    sentence from the constant; `responses.py` cannot, because the
    import that would do it closes a cycle (`responses` is under
    `entities`, which is under `loader`, which `secrets` imports), and
    its docstring says so. So the equality is asserted here, on the
    rendered document, which is the byte a client reads.
    """
    document = json.loads(docgen.openapi())
    entity = document["components"]["schemas"]["Envelope"]["properties"]["entity"]

    for description in (document["info"]["description"], entity["description"]):
        assert f"`{MASK}`" in description
        assert "keep the stored value" in description


# The prose the document is built from
#
# Since #242 the document-level and refusal descriptions are package
# data under `config/api_descriptions/` rather than literals in
# `api.py`, which puts a new way to break the contract on the table: an
# installation that did not carry a file. The proof that a wheel carries
# them is in CI, which renders this document from the installed artifact
# with the source tree off sys.path. What is left to hold here is the
# other half of that promise, the one a wheel check cannot show because
# a passing wheel never takes the path: a description that cannot be
# assembled refuses by name, with a sentence, rather than leaving the
# contract a paragraph short.
#
# Both reach the loader and its directory through their underscored
# names, which is deliberate and is the shape `test_build_info.py`
# already has for `_CHECKOUT`: what is being exercised is a refusal
# raised at import, and the only other way to reach it would be to move
# the real directory aside under a suite that runs four workers over one
# filesystem.


def test_a_missing_description_refuses_by_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A packaging fault, said as one."""
    monkeypatch.setattr(api, "_DESCRIPTIONS", tmp_path)

    with pytest.raises(MissingDescriptionError) as refusal:
        api._description("api")

    assert "packaging" in str(refusal.value)


def test_a_description_naming_an_unfillable_sigil_refuses_by_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """And the other way a description can fail to be assembled: a
    substitution nothing provides. Silently leaving the sigil in place
    would put `$NOTHING$` in a published contract."""
    monkeypatch.setattr(api, "_DESCRIPTIONS", tmp_path)
    (tmp_path / "api.md").write_text("a paragraph naming $NOTHING$\n", encoding="utf-8")

    with pytest.raises(MissingDescriptionError) as refusal:
        api._description("api")

    assert "NOTHING" in str(refusal.value)


def test_a_description_carries_the_file_through_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The loader transforms two things and nothing else: it fills the
    sigils, and it drops the single trailing newline a text file ends
    with. Blank lines are paragraph breaks the document carries, and the
    wrapping inside a paragraph is the wrapping the contract has, which
    is what let the move leave the committed document byte-identical."""
    monkeypatch.setattr(api, "_DESCRIPTIONS", tmp_path)
    (tmp_path / "api.md").write_text(
        "first, masked as $MASK$\n\nsecond,\nwrapped\n", encoding="utf-8"
    )

    assert api._description("api") == f"first, masked as {MASK}\n\nsecond,\nwrapped"


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
