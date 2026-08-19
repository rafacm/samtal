"""What a refusal says, byte for byte, and which field it says it about.

Two claims meet here, which is why they share a file. The sentence a
refusal carries is the repository's own, and #192 keeps it that way
while wrapping it in an RFC 9457 problem document: `detail` is the same
string before and after, so an operator meets one vocabulary whichever
way they reached the API. Substring assertions cannot hold that claim,
because indentation, ordering and prefixes all survive them, so the
sentences below are goldens: the exact bodies of real repository-backed
PUTs, written out in full.

A golden that moves is either a bug or a decision. The one decision
this milestone makes is recorded on `MCP_TRANSPORT_REFUSAL` below.

The other claim is that the structured half says the same thing as the
sentence and adds a place to put it: every emitter answers one shape,
the `errors` entries and the `detail` lines are one computation seen
twice, and a pointer addresses the field a form would mark, escaped
where a key holds a dot or a slash. And under both, the standing one:
nothing of what was sent comes back, in the sentence, in a pointer, in
a message, or in the log.

Beside them, the pydantic mechanism the structured half rests on: a
`ValueError` raised inside a model validator is reachable from
`ValidationError.errors()` as the error's context, which is what lets a
validator that knows its semantic field hand its problems up. A
pydantic release that stopped carrying it would silently flatten every
model-level refusal to one location, so it is pinned rather than
assumed.
"""

import logging
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import NamedTuple

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError, model_validator

from samtal_server import logs
from samtal_server.config.api import (
    MALFORMED_REQUEST,
    PROBLEM_DESCRIPTIONS,
    PROBLEM_MEDIA_TYPE,
    PROBLEM_TITLES,
    UNAUTHORIZED,
    UNEXPECTED,
    build_api,
)
from samtal_server.config.loader import ConfigError
from samtal_server.config.models import UNRECOGNIZED_KEY_REFUSED, json_pointer
from samtal_server.config.secrets import MASTER_KEY_ENV, generate_key, load_keys
from samtal_server.config.store import ConfigStore
from samtal_server.conversations.api import NO_STORE
from samtal_server.db import open_database
from tests.support.config_cli import runner
from tests.support.problems import problem

TOKEN = "test-api-token-" + "0123456789abcdef" * 2

# Not real credentials, and shaped so a substring check for one cannot
# match by accident. The second is planted as a key rather than as a
# value, and is spelled without a dot or a slash so that the one case
# that adds them adds them itself.
SENTINEL = "sk-test-6c3e9b12-never-a-real-credential"
KEY_SENTINEL = "sk-test-9d41ac60-never-a-real-credential"


@pytest.fixture
def keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MASTER_KEY_ENV, generate_key())


@pytest.fixture
def store(tmp_path: Path, keys: None) -> Iterator[ConfigStore]:
    """The repository on its own, for the assertions that are about the
    exception rather than about the response built from it."""
    engine = open_database(tmp_path / "db")
    try:
        yield ConfigStore(engine, load_keys())
    finally:
        engine.dispose()


@pytest.fixture
def api(tmp_path: Path, keys: None) -> FastAPI:
    return build_api(TOKEN, tmp_path / "db")


@pytest.fixture
def client(api: FastAPI) -> Iterator[TestClient]:
    """Entered, so a request reaches a real repository: a golden taken
    from anything else would be a golden of a fake."""
    with TestClient(api, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        yield client


# The goldens. Each is the whole `detail` of one real PUT, quoted here
# in the shape a terminal prints it.

SINGLE_ERROR_REFUSAL = "invalid providers.llm.claude:\n  - type: Field required"

MULTI_ERROR_REFUSAL = (
    "invalid providers.llm.claude:\n"
    "  - type: Input should be a valid string\n"
    "  - api_key_env: Input should be a valid string"
)

# The three that a model-level validator writes are spelled out as the
# message and assembled into the sentence, because the same string is
# also the `errors` entry beside it, and a golden written twice is a
# golden that can be updated once.

NESTED_SECRET_MESSAGE = (
    'a key containing "api_key" looks like an inline secret, which is not allowed; '
    "reference an environment variable instead, in a key of the same name ending in "
    "_env. The key is not quoted back"
)

NESTED_SECRET_REFUSAL = f"invalid providers.llm.claude:\n  - {NESTED_SECRET_MESSAGE}"

DECLARED_ENV_MESSAGE = (
    '"api_key_env" must hold the name of an environment variable, and what it holds '
    "does not look like one; a pasted value belongs nowhere in this file, so name the "
    "variable holding it, for example api_key_env: MY_PROVIDER_KEY"
)

DECLARED_ENV_REFUSAL = f"invalid providers.llm.claude:\n  - {DECLARED_ENV_MESSAGE}"

FILLER_MESSAGE = (
    "filler.enabled is on with no phrases; add at least one, "
    'for example "Hmm, let me see..."'
)

FILLER_REFUSAL = f"invalid agents.sam:\n  - filler: {FILLER_MESSAGE}"

MCP_SECRET_MESSAGE = (
    'a key in env containing "api_key" looks like an inline secret, which is not '
    "allowed; reference an environment variable instead, for example "
    "$MY_SERVER_SECRET. The key is not quoted back"
)

# The one sentence this milestone changes, and the reason it changes:
# the transport validator finds several problems and joined them into
# one line with `; `, which is a line a form cannot decompose. Its
# problems become one entry each, so the prose becomes one line each,
# with the same words per problem and in the same order. Recorded in
# the implementation doc as a deliberate prose change.
MCP_TRANSPORT_REFUSAL = (
    "invalid mcp_servers.home:\n"
    '  - transport "stdio" needs "command"\n'
    '  - transport "stdio" has no url; that belongs to the other transport\n'
    f"  - {MCP_SECRET_MESSAGE}"
)


def test_one_rejected_field_answers_its_golden(client: TestClient) -> None:
    response = client.put("/providers/llm/claude", json={"model": "m"})

    assert response.status_code == 422
    assert response.json()["detail"] == SINGLE_ERROR_REFUSAL


def test_two_rejected_fields_answer_their_golden(client: TestClient) -> None:
    """One line per problem, in the order pydantic reports them, under
    one headline naming the entity."""
    response = client.put("/providers/llm/claude", json={"type": 5, "api_key_env": 7})

    assert response.status_code == 422
    assert response.json()["detail"] == MULTI_ERROR_REFUSAL


def test_a_nested_inline_secret_answers_its_golden(client: TestClient) -> None:
    """A model-level validator's sentence, which names the path it found
    and never the value."""
    response = client.put(
        "/providers/llm/claude",
        json={"type": "anthropic", "connection": {"api_key": "sk-live-not-a-real-value"}},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == NESTED_SECRET_REFUSAL


def test_a_filler_without_phrases_answers_its_golden(client: TestClient) -> None:
    response = client.put(
        "/agents/sam", json={"prompt": "You are Sam.", "filler": {"enabled": True}}
    )

    assert response.status_code == 422
    assert response.json()["detail"] == FILLER_REFUSAL


def test_an_mcp_fragment_breaking_three_rules_answers_its_golden(client: TestClient) -> None:
    response = client.put(
        "/mcp-servers/home",
        json={
            "transport": "stdio",
            "url": "https://example.invalid/mcp",
            "env": {"API_KEY": "not-a-reference"},
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == MCP_TRANSPORT_REFUSAL


# The mechanism


def test_a_validator_error_is_reachable_from_the_pydantic_error_context() -> None:
    """`errors()` carries the exception a validator raised, as the
    object, under the error's `ctx`.

    This is the whole seam by which a validator that knows its semantic
    field says so: pydantic locates a model-level error at the model,
    so the field is in the raised exception or nowhere. The pin is
    about the mechanism and not about this project's types, so it uses
    a throwaway model and a throwaway exception class: a pydantic
    release that stopped carrying the object, or that carried a copy
    rather than the instance, fails here loudly rather than quietly
    flattening every model-level refusal.
    """

    class Planted(ValueError):
        pass

    raised = Planted("the validator's own words")

    class Fragment(BaseModel):
        value: int = 1

        @model_validator(mode="after")
        def _refuse(self) -> "Fragment":
            raise raised

    with pytest.raises(ValidationError) as caught:
        Fragment()

    (error,) = caught.value.errors()
    assert error["loc"] == ()
    assert error["ctx"]["error"] is raised
    assert error["msg"] == "Value error, the validator's own words"


# The emitters


def test_a_repository_refusal_answers_the_one_shape(client: TestClient) -> None:
    response = client.put("/providers/llm/claude", json={"model": "m"})

    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert response.json() == problem(
        422, SINGLE_ERROR_REFUSAL, [("/type", "Field required")]
    )


def test_the_gate_answers_the_one_shape(api: FastAPI) -> None:
    """The gate runs in front of routing and used to build its own body,
    which is exactly how a shape acquires a second spelling."""
    with TestClient(api) as client:
        response = client.get(f"/agents/{SENTINEL}")

    assert response.status_code == 401
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json() == problem(401, UNAUTHORIZED)
    assert SENTINEL not in response.text


def test_a_body_that_cannot_be_read_answers_the_one_shape(client: TestClient) -> None:
    response = client.put(
        "/agents/sam", content=SENTINEL, headers={"Content-Type": "application/json"}
    )

    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert response.json() == problem(422, MALFORMED_REQUEST)
    assert SENTINEL not in response.text


def test_the_last_resort_answers_the_one_shape(api: FastAPI) -> None:
    """The middleware that ends an exception nothing else handled. Its
    body says nothing about the failure, and now it says nothing in the
    same shape as everything else."""

    @api.get("/boom")
    def endpoint() -> dict[str, str]:
        raise RuntimeError(f"a connection string with {SENTINEL} in it")

    response = TestClient(api).get("/boom", headers={"Authorization": f"Bearer {TOKEN}"})

    assert response.status_code == 500
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert response.json() == problem(500, UNEXPECTED)
    assert SENTINEL not in response.text


def test_an_authenticated_unmatched_path_answers_the_one_shape(client: TestClient) -> None:
    """The fifth emitter is the framework. Nothing in this application
    writes this refusal, which is why it was the one leaving in a body of
    Starlette's own."""
    response = client.get(f"/no-such-route/{SENTINEL}")

    assert response.status_code == 404
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    # Asserted rather than assumed: the detail is Starlette's fixed
    # phrase, and a routing refusal that quoted the path would be
    # publishing the request.
    assert response.json() == problem(404, "Not Found")
    assert SENTINEL not in response.text


def test_a_wrong_method_answers_the_one_shape_and_keeps_its_allow(
    client: TestClient,
) -> None:
    """A 405 without its `Allow` is not a 405, so the exception's own
    protocol headers survive the rendering."""
    response = client.post("/config", json={"anything": SENTINEL})

    assert response.status_code == 405
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert response.headers["allow"] == "GET"
    assert response.json() == problem(405, "Method Not Allowed")
    assert SENTINEL not in response.text


def test_a_trailing_slash_path_answers_the_one_shape(client: TestClient) -> None:
    """This namespace redirects nothing, so a stray slash is an unmatched
    path like any other, and the name in it is not quoted back in a body
    or in a Location header."""
    response = client.get(f"/agents/{SENTINEL}/", follow_redirects=False)

    assert response.status_code == 404
    assert "location" not in response.headers
    assert response.json() == problem(404, "Not Found")
    assert SENTINEL not in response.text


def test_a_conversations_refusal_answers_the_same_shape(client: TestClient) -> None:
    """The conversation reads live in another module, raise the shared
    refusal types and build no body of their own, so they inherit this
    shape rather than restating it. That is the claim.

    `errors` is empty, which is the honest answer: what this refusal
    names is a deployment setting, not a field of the request.
    """
    response = client.get(f"/conversations/{SENTINEL}")

    assert response.status_code == 404
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert response.json() == problem(404, NO_STORE)
    assert SENTINEL not in response.text


# The two renderings say the same thing


def test_the_errors_and_the_detail_lines_are_the_same_problems(
    client: TestClient,
) -> None:
    """One computation seen twice. Asserted from the outside, pairwise,
    so a second walk producing a different decomposition would show up
    as a mismatch rather than as two plausible bodies."""
    response = client.put("/providers/llm/claude", json={"type": 5, "api_key_env": 7})

    body = response.json()
    assert body["detail"] == MULTI_ERROR_REFUSAL
    assert body["errors"] == [
        {"path": "/type", "message": "Input should be a valid string"},
        {"path": "/api_key_env", "message": "Input should be a valid string"},
    ]
    for line, error in zip(body["detail"].splitlines()[1:], body["errors"], strict=True):
        assert line == f"  - {error['path'].removeprefix('/')}: {error['message']}"


# Where a model-level validator says its problem is


def test_a_nested_inline_secret_names_the_fragment_and_not_the_key(
    client: TestClient,
) -> None:
    """A provider's options are pass-through, so every key under them is
    the caller's and none of them may be printed. What the refusal names
    instead is the closed fragment the key matched, which is one of this
    repository's own six words, and a pointer to the nearest place it can
    name, which for an option is the fragment itself."""
    response = client.put(
        "/providers/llm/claude",
        json={"type": "anthropic", "connection": {"api_key": SENTINEL}},
    )

    assert response.status_code == 422
    assert response.json() == problem(
        422, NESTED_SECRET_REFUSAL, [("", NESTED_SECRET_MESSAGE)]
    )
    assert SENTINEL not in response.text


def test_a_declared_field_is_named_in_its_own_refusal(client: TestClient) -> None:
    """The other side of the same rule, and why it is not "print
    nothing": `api_key_env` is a field this repository declared, so the
    refusal names it, and the pointer addresses it."""
    response = client.put(
        "/providers/llm/claude", json={"type": "anthropic", "api_key_env": SENTINEL}
    )

    assert response.status_code == 422
    assert response.json() == problem(
        422, DECLARED_ENV_REFUSAL, [("/api_key_env", DECLARED_ENV_MESSAGE)]
    )
    assert SENTINEL not in response.text


def test_an_mcp_fragment_breaking_two_rules_answers_one_entry_each(
    client: TestClient,
) -> None:
    """The transport rule and the secret rule at once, each with its own
    field's pointer, out of one validator that used to join them into a
    single sentence at a single location."""
    response = client.put(
        "/mcp-servers/home",
        json={
            "transport": "stdio",
            "url": "https://example.invalid/mcp",
            "env": {"API_KEY": SENTINEL},
        },
    )

    assert response.status_code == 422
    assert response.json() == problem(
        422,
        MCP_TRANSPORT_REFUSAL,
        [
            ("/command", 'transport "stdio" needs "command"'),
            # The one problem about a combination rather than a key: the
            # empty pointer is the fragment itself, which is what RFC
            # 6901 gives a problem with no single field to blame.
            ("", 'transport "stdio" has no url; that belongs to the other transport'),
            # The group and not the key: `env` is a declared field, and
            # everything keyed under it is whatever the caller wrote.
            ("/env", MCP_SECRET_MESSAGE),
        ],
    )
    assert SENTINEL not in response.text


def test_a_filler_problem_points_under_the_layer_that_holds_it(
    client: TestClient,
) -> None:
    """The validator is on the filler block, and the block hangs off the
    agent, so the pointer is the two of them: what pydantic located plus
    what the validator knew."""
    response = client.put(
        "/agents/sam", json={"prompt": "You are Sam.", "filler": {"enabled": True}}
    )

    assert response.status_code == 422
    assert response.json() == problem(
        422, FILLER_REFUSAL, [("/filler/phrases", FILLER_MESSAGE)]
    )


def test_an_unrecognized_key_answers_the_parent_it_was_written_under(
    client: TestClient,
) -> None:
    """A key the model does not declare is a key the caller invented, so
    the refusal says that a key was not recognized and points at the
    object it was written in, never at the key. At the top of a fragment
    that object is the fragment, which is the empty pointer."""
    top = client.put("/agents/sam", json={"prompt": "You are Sam.", "surprise": 1})
    nested = client.put(
        "/agents/sam", json={"prompt": "You are Sam.", "filler": {"surprise": 1}}
    )

    assert top.json() == problem(
        422,
        f"invalid agents.sam:\n  - {UNRECOGNIZED_KEY_REFUSED}",
        [("", UNRECOGNIZED_KEY_REFUSED)],
    )
    assert nested.json() == problem(
        422,
        f"invalid agents.sam:\n  - filler: {UNRECOGNIZED_KEY_REFUSED}",
        [("/filler", UNRECOGNIZED_KEY_REFUSED)],
    )


def test_the_pointer_escapes_what_the_rfc_says_to_escape() -> None:
    """The construction itself, pinned where it is built rather than
    through a refusal, because no refusal can reach it any more: every
    segment a pointer may carry is a name this repository declared, and
    none of those holds a `~` or a `/`. The escaping stays because the
    contract says RFC 6901 and a name that acquired one would otherwise
    silently address something else."""
    assert json_pointer(()) == ""
    assert json_pointer(("filler", "phrases")) == "/filler/phrases"
    assert json_pointer(("mcp", 0)) == "/mcp/0"
    assert json_pointer(("a~b", "c/d")) == "/a~0b/c~1d"


# Nothing of what was sent comes back


def test_a_planted_credential_is_absent_from_every_surface(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """A credential-shaped value in a field whose type is wrong, which is
    the shape of the mistake: a key pasted where pydantic will reject it
    on grounds that have nothing to do with what it holds. It must
    survive in none of the four places a refusal can carry something:
    the sentence, a pointer, a message, and the log, in either format
    this server writes.
    """
    with caplog.at_level(logging.DEBUG):
        response = client.put(
            "/providers/llm/claude", json={"type": [SENTINEL], "api_key_env": SENTINEL}
        )

    assert response.status_code == 422
    body = response.json()
    assert SENTINEL not in body["detail"]
    for error in body["errors"]:
        assert SENTINEL not in error["path"]
        assert SENTINEL not in error["message"]
    assert SENTINEL not in response.text
    assert SENTINEL not in str(response.headers)

    text = logging.Formatter(logs.TEXT_FORMAT)
    for record in caplog.records:
        assert SENTINEL not in logs.JsonFormatter().format(record)
        assert SENTINEL not in text.format(record)


# A credential planted as a key, which is the other half of the same
# rule: a key is as good a place to paste one as a value, and better at
# hiding there, because a key looks like a name.

class PlantedKey(NamedTuple):
    """One fragment carrying the sentinel as a key, and the two ways in:
    the route a request reaches it by, and the repository call the CLI's
    break-glass path reaches the same refusal by."""

    what: str
    path: str
    fragment: dict[str, object]
    write: Callable[[ConfigStore, dict[str, object]], None]


PLANTED_KEYS = [
    PlantedKey(
        "an unrecognized key at the top of a fragment",
        "/agents/sam",
        {"prompt": "You are Sam.", KEY_SENTINEL: 1},
        lambda store, fragment: store.set_agent("sam", fragment),
    ),
    PlantedKey(
        "an unrecognized key nested inside a declared block",
        "/agents/sam",
        {"prompt": "You are Sam.", "filler": {KEY_SENTINEL: 1}},
        lambda store, fragment: store.set_agent("sam", fragment),
    ),
    PlantedKey(
        "a secret-shaped option key nested under another invented one",
        "/providers/llm/claude",
        {"type": "anthropic", KEY_SENTINEL: {f"{KEY_SENTINEL}_token": "v"}},
        lambda store, fragment: store.set_provider("llm", "claude", fragment),
    ),
    PlantedKey(
        "an option key holding a dot and a slash",
        "/providers/llm/claude",
        {"type": "anthropic", f"{KEY_SENTINEL}.a/b": {"api_key": "v"}},
        lambda store, fragment: store.set_provider("llm", "claude", fragment),
    ),
    PlantedKey(
        "a secret-shaped key in an MCP server's env",
        "/mcp-servers/home",
        {"transport": "stdio", "command": "uvx", "env": {f"{KEY_SENTINEL}_TOKEN": "v"}},
        lambda store, fragment: store.set_mcp_server("home", fragment),
    ),
]

PLANTED_IDS = [case.what for case in PLANTED_KEYS]


@pytest.mark.parametrize("case", PLANTED_KEYS, ids=PLANTED_IDS)
def test_a_credential_planted_as_a_key_is_absent_from_every_surface(
    client: TestClient, caplog: pytest.LogCaptureFixture, case: PlantedKey
) -> None:
    """Over HTTP: the sentence, every pointer, every message, the whole
    body, the headers, and the log in both formats this server writes."""
    with caplog.at_level(logging.DEBUG):
        response = client.put(case.path, json=case.fragment)

    assert response.status_code == 422
    body = response.json()
    assert KEY_SENTINEL not in body["detail"]
    for error in body["errors"]:
        assert KEY_SENTINEL not in error["path"]
        assert KEY_SENTINEL not in error["message"]
    assert KEY_SENTINEL not in response.text
    assert KEY_SENTINEL not in str(response.headers)

    text = logging.Formatter(logs.TEXT_FORMAT)
    for record in caplog.records:
        assert KEY_SENTINEL not in logs.JsonFormatter().format(record)
        assert KEY_SENTINEL not in text.format(record)


@pytest.mark.parametrize("case", PLANTED_KEYS, ids=PLANTED_IDS)
def test_a_credential_planted_as_a_key_is_absent_from_the_exception(
    store: ConfigStore, case: PlantedKey
) -> None:
    """And underneath the transport, on the exception itself.

    An exception is a surface of its own: anything that walks one
    (a logger asked for a traceback, a debugger, a report) reads its
    message, its repr, its cause and its context, and the API's own
    `problems` ride on it. The repository builds the sentence inside the
    handler and raises outside it for exactly this reason, which is what
    keeps the rejected fragment off the chain.
    """
    with pytest.raises(ConfigError) as caught:
        case.write(store, case.fragment)

    refusal = caught.value
    assert KEY_SENTINEL not in str(refusal)
    assert KEY_SENTINEL not in repr(refusal)
    assert refusal.__cause__ is None
    assert refusal.__context__ is None
    for carried in refusal.problems:
        assert KEY_SENTINEL not in carried.path
        assert KEY_SENTINEL not in carried.message


# What the CLI prints for one


def test_the_cli_prints_the_same_sentence_for_a_problem_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The compatibility claim, taken off a real refusal rather than a
    hand-built one: the command runs against a repository-backed API, the
    API answers `application/problem+json`, and what reaches the terminal
    is the golden above, unchanged.

    `_payload` accepts any content type holding `json`, and `_answer`
    reads `detail` and ignores the members it does not know, which is why
    no CLI change was needed. This is what holds that.
    """
    run = runner(tmp_path, monkeypatch)

    assert run("set", "provider", "llm", "claude", "-f", "-", stdin="model: m\n") == 1

    assert capsys.readouterr().err.rstrip("\n") == SINGLE_ERROR_REFUSAL


# The status vocabulary


def test_the_two_things_said_about_a_status_are_said_about_the_same_statuses() -> None:
    """A title with no description, or a description with no title, is a
    status one of the two mappings has not heard of, which is how a
    closed set stops being one."""
    assert set(PROBLEM_TITLES) == set(PROBLEM_DESCRIPTIONS)


def test_each_title_is_the_status_s_standard_reason_phrase() -> None:
    """Standard phrases and not this API's own words: with `type` absent
    the problem type is `about:blank`, whose title RFC 9457 says should
    be the status's recommended phrase, and a title of this server's own
    would name a problem type the body does not identify."""
    assert PROBLEM_TITLES == {
        401: "Unauthorized",
        404: "Not Found",
        405: "Method Not Allowed",
        409: "Conflict",
        422: "Unprocessable Content",
        500: "Internal Server Error",
        503: "Service Unavailable",
    }
