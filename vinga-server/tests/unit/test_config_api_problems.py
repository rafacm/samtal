"""What a refusal is about, and which field it says it about.

Two claims meet here, which is why they share a file. The first is that
the sentence a refusal carries is the repository's own, unchanged by the
transport: an operator meets one vocabulary whichever way they reached
the API. That was held by goldens until #242, which is to say by copies
of the sentences, so an edit to prose that changed nothing an operator
does turned this file red. It is held differentially now: the same act
is driven through the repository directly and over HTTP, and the two
answers are compared with neither of them written down here. What is
compared per refusal beside that is structure and semantics: the status,
the one body shape, the entity the refusal is about, the number of
problems it decomposes into, and the field each one addresses.

The second claim is that the structured half says the same thing as the
sentence and adds a place to put it: every emitter answers one shape,
the `errors` entries and the `detail` lines are one computation seen
twice, and a pointer addresses the field a form would mark, escaped
where a key holds a dot or a slash. And under both, the standing one:
nothing of what was sent comes back, in the sentence, in a pointer, in
a message, or in the log. The no-leak assertions are not wording pins
and did not retreat.

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

from tests.support.config_cli import runner
from tests.support.problems import paths, refused
from vinga_server import logs
from vinga_server.config.api import (
    PROBLEM_DESCRIPTIONS,
    PROBLEM_MEDIA_TYPE,
    PROBLEM_TITLES,
    build_api,
)
from vinga_server.config.loader import ConfigError
from vinga_server.config.models import DatabaseConfig, json_pointer
from vinga_server.config.secrets import MASTER_KEY_ENV, generate_key, load_keys
from vinga_server.config.store import ConfigStore
from vinga_server.db import open_database

TOKEN = "test-api-token-" + "0123456789abcdef" * 2

# Not real credentials, and shaped so a substring check for one cannot
# match by accident. The second is planted as a key rather than as a
# value, and is spelled without a dot or a slash so that the one case
# that adds them adds them itself.
SENTINEL = "sk-test-6c3e9b12-never-a-real-credential"
KEY_SENTINEL = "sk-test-9d41ac60-never-a-real-credential"

# The third sentinel, and the reason it is spelled without the word
# "credential" in it: the two above match a closed secret-shaped
# fragment, so a key holding either is refused by `ProviderConfig`
# before any later rule reads it. A key that gets PAST that rule is the
# one that reaches the rules underneath, which is where a leak would
# have to be looked for, and until the escape hatch (#88) an option key
# nothing declared was a mistake rather than a shape this repository
# supports. It is planted as the key and inside the URL that key holds,
# so one assertion covers both halves of the same paste.
URL_KEY_SENTINEL = "sk-live-4b7d2e10-never-a-real-one"


@pytest.fixture
def keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MASTER_KEY_ENV, generate_key())


@pytest.fixture
def store(keys: None) -> Iterator[ConfigStore]:
    """The repository on its own, for the assertions that are about the
    exception rather than about the response built from it."""
    engine = open_database(DatabaseConfig())
    try:
        yield ConfigStore(engine, load_keys())
    finally:
        engine.dispose()


@pytest.fixture
def api(keys: None) -> FastAPI:
    return build_api(TOKEN, DatabaseConfig())


@pytest.fixture
def client(api: FastAPI) -> Iterator[TestClient]:
    """Entered, so a request reaches a real repository: a golden taken
    from anything else would be a golden of a fake."""
    with TestClient(api, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        yield client


# The refusals. Each is one real PUT, the repository call the same
# fragment reaches the same refusal by, the entity the refusal is about,
# and the field each of its problems addresses in the order they are
# reported. What the sentence says is nowhere here, deliberately: it is
# the repository's, and the test below holds the two paths to it equal
# without either of them being copied.


class Refusal(NamedTuple):
    what: str
    path: str
    fragment: dict[str, object]
    write: Callable[[ConfigStore, dict[str, object]], None]
    entity: str
    pointers: list[str]


REFUSALS = [
    Refusal(
        "one rejected field",
        "/providers/llm/claude",
        {"model": "m"},
        lambda store, fragment: store.set_provider("llm", "claude", fragment),
        "providers.llm.claude",
        ["/type"],
    ),
    Refusal(
        "two rejected fields",
        "/providers/llm/claude",
        {"type": 5, "api_key_env": 7},
        lambda store, fragment: store.set_provider("llm", "claude", fragment),
        "providers.llm.claude",
        ["/type", "/api_key_env"],
    ),
    Refusal(
        "a nested inline secret",
        "/providers/llm/claude",
        {"type": "anthropic", "connection": {"api_key": SENTINEL}},
        lambda store, fragment: store.set_provider("llm", "claude", fragment),
        "providers.llm.claude",
        # A provider's options are pass-through, so every key under them
        # is the caller's and none may be printed. What the refusal
        # addresses instead is the nearest place this repository can
        # name, which for an option is the fragment itself.
        [""],
    ),
    Refusal(
        "a declared reference field holding something else",
        "/providers/llm/claude",
        {"type": "anthropic", "api_key_env": SENTINEL},
        lambda store, fragment: store.set_provider("llm", "claude", fragment),
        "providers.llm.claude",
        # The other side of that rule, and why it is not "print
        # nothing": this is a field the repository declared, so the
        # pointer addresses it by name.
        ["/api_key_env"],
    ),
    Refusal(
        "a declared option of a typed provider type",
        "/providers/asr/ears",
        {"type": "faster_whisper", "beam_size": "5"},
        lambda store, fragment: store.set_provider("asr", "ears", fragment),
        "providers.asr.ears",
        # The inversion #88 bought. This key used to be an option of a
        # pass-through model and so unprintable, exactly like the nested
        # case above; the type declares it now, so it is a name this
        # repository chose and the pointer addresses it. Options are flat
        # siblings of `type` in the submitted fragment, so the pointer is
        # the field itself rather than anything under an `options` key.
        ["/beam_size"],
    ),
    Refusal(
        "an undeclared option of a typed provider type",
        "/providers/asr/ears",
        {"type": "faster_whisper", "beem_size": 5},
        lambda store, fragment: store.set_provider("asr", "ears", fragment),
        "providers.asr.ears",
        # And the other side of the same rule, which did not move: a key
        # the caller invented is still not printed, whatever the type
        # declares, so the refusal addresses the fragment.
        [""],
    ),
    Refusal(
        "a credential pasted into a declared option of a typed type",
        "/providers/asr/ears",
        {"type": "faster_whisper", "beam_size": SENTINEL},
        lambda store, fragment: store.set_provider("asr", "ears", fragment),
        "providers.asr.ears",
        # The field is named because the type declared it; the value is
        # not, because a value refused for being the wrong shape is
        # exactly the shape of thing a credential is pasted as.
        ["/beam_size"],
    ),
    Refusal(
        "a declared nested option of a typed provider type",
        "/providers/tts/voice",
        {
            "type": "elevenlabs",
            "voice_id": "voice-1",
            "voice_settings": {"stability": SENTINEL},
        },
        lambda store, fragment: store.set_provider("tts", "voice", fragment),
        "providers.tts.voice",
        # Two declared names deep, which is the shape the first typed
        # type could not show: its nested section forwards what it does
        # not declare, so nothing under it is ever refused. This one's
        # is closed, so a field inside it is a name the repository chose
        # and the pointer walks to it. The value is a credential and is
        # nowhere.
        ["/voice_settings/stability"],
    ),
    Refusal(
        "an undeclared key inside a typed type's nested options",
        "/providers/tts/voice",
        {
            "type": "elevenlabs",
            "voice_id": "voice-1",
            "voice_settings": {"stabilty": 0.5},
        },
        lambda store, fragment: store.set_provider("tts", "voice", fragment),
        "providers.tts.voice",
        # And the fallback one level down: a key the caller invented
        # inside a section the repository declared addresses the deepest
        # declared parent, which is the section rather than the fragment.
        ["/voice_settings"],
    ),
    Refusal(
        "a passthrough key naming a field of the request",
        "/providers/llm/local",
        {
            "type": "openai_compatible",
            "base_url": "http://localhost:11434/v1",
            "model": "qwen3:8b",
            "messages": [{"role": "user", "content": SENTINEL}],
        },
        lambda store, fragment: store.set_provider("llm", "local", fragment),
        "providers.llm.local",
        # The escape-hatch type, whose model keeps what it does not
        # declare. `messages` is the one thing it may not keep: the
        # conversation is composed per request, and a key by that name
        # would rewrite it rather than configure a server. The pointer
        # names it because the reserved set is this repository's own
        # seven words, published in the schema; what was written under
        # it is the caller's and is nowhere.
        ["/messages"],
    ),
    Refusal(
        "a filler switched on with no phrases",
        "/agents/sam",
        {"prompt": "You are Sam.", "filler": {"enabled": True}},
        lambda store, fragment: store.set_agent("sam", fragment),
        "agents.sam",
        # The validator is on the filler block and the block hangs off
        # the agent, so the pointer is the two of them: what pydantic
        # located plus what the validator knew.
        ["/filler/phrases"],
    ),
    Refusal(
        "an MCP fragment breaking three rules at once",
        "/mcp-servers/home",
        {
            "transport": "stdio",
            "url": "https://example.invalid/mcp",
            "env": {"API_KEY": SENTINEL},
        },
        lambda store, fragment: store.set_mcp_server("home", fragment),
        "mcp_servers.home",
        # One entry per problem, out of a validator that used to join
        # them into a single sentence at a single location: the missing
        # field, the combination that has no single field to blame, and
        # the declared group whose keys are the caller's.
        ["/command", "", "/env"],
    ),
]

REFUSAL_IDS = [case.what for case in REFUSALS]


@pytest.mark.parametrize("case", REFUSALS, ids=REFUSAL_IDS)
def test_a_refusal_names_its_entity_and_addresses_each_problem(
    client: TestClient, case: Refusal
) -> None:
    """422, the one body shape, the entity the fragment was written for,
    and one problem per rule broken, each addressing the field a form
    would mark.

    The headline names the entity and every problem gets a line under
    it, which is what a terminal prints and what makes a multi-problem
    refusal readable; the words on each line are the repository's."""
    response = client.put(case.path, json=case.fragment)

    assert response.status_code == 422
    body = response.json()
    detail = refused(body, 422)
    assert detail.startswith(f"invalid {case.entity}:")
    assert paths(body) == case.pointers
    assert len(detail.splitlines()) == 1 + len(case.pointers)
    assert SENTINEL not in response.text


@pytest.mark.parametrize("case", REFUSALS, ids=REFUSAL_IDS)
def test_the_api_answers_the_repository_s_own_words(
    client: TestClient, store: ConfigStore, case: Refusal
) -> None:
    """The compatibility claim of #192, held differentially.

    The same fragment is written twice: once through the repository
    directly and once over HTTP, which is the route the repository is
    mounted behind. The sentence and the per-field messages are compared against
    each other rather than against a copy, so an operator meets one
    vocabulary whichever way they reached the API and nothing here has
    to be updated when the repository rewords a rule.
    """
    with pytest.raises(ConfigError) as caught:
        case.write(store, case.fragment)

    body = client.put(case.path, json=case.fragment).json()

    assert body["detail"] == str(caught.value)
    assert body["errors"] == [
        {"path": problem.path, "message": problem.message}
        for problem in caught.value.problems
    ]


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
    body = response.json()
    refused(body, 422)
    assert paths(body) == ["/type"]


def test_the_gate_answers_the_one_shape(api: FastAPI) -> None:
    """The gate runs in front of routing and used to build its own body,
    which is exactly how a shape acquires a second spelling."""
    with TestClient(api) as client:
        response = client.get(f"/agents/{SENTINEL}")

    assert response.status_code == 401
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert response.headers["WWW-Authenticate"] == "Bearer"
    # How to authenticate, and no field of the request to blame.
    body = response.json()
    assert "Authorization: Bearer" in refused(body, 401)
    assert paths(body) == []
    assert SENTINEL not in response.text


def test_a_body_that_cannot_be_read_answers_the_one_shape(client: TestClient) -> None:
    response = client.put(
        "/agents/sam", content=SENTINEL, headers={"Content-Type": "application/json"}
    )

    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    # What was expected, and never what arrived.
    assert "JSON object body" in refused(response.json(), 422)
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
    assert "log" in refused(response.json(), 500)
    assert SENTINEL not in response.text


def test_an_authenticated_unmatched_path_answers_the_one_shape(client: TestClient) -> None:
    """The fifth emitter is the framework. Nothing in this application
    writes this refusal, which is why it was the one leaving in a body of
    Starlette's own."""
    response = client.get(f"/no-such-route/{SENTINEL}")

    assert response.status_code == 404
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    # The framework's own refusal, rendered into this shape: the title
    # is the status's reason phrase and no field is blamed. That it
    # quotes no path is the assertion below.
    body = response.json()
    refused(body, 404)
    assert paths(body) == []
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
    refused(response.json(), 405)
    assert SENTINEL not in response.text


def test_a_trailing_slash_path_answers_the_one_shape(client: TestClient) -> None:
    """This namespace redirects nothing, so a stray slash is an unmatched
    path like any other, and the name in it is not quoted back in a body
    or in a Location header."""
    response = client.get(f"/agents/{SENTINEL}/", follow_redirects=False)

    assert response.status_code == 404
    assert "location" not in response.headers
    refused(response.json(), 404)
    assert SENTINEL not in response.text


def test_a_conversations_refusal_answers_the_same_shape(client: TestClient) -> None:
    """The conversation store's reads live in another module, raise the
    shared refusal types and build no body of their own, so they inherit
    this shape rather than restating it. That is the claim.

    `errors` is empty, which is the honest answer: what this refusal
    names is a deployment setting, not a field of the request.
    """
    response = client.get(f"/sessions/{SENTINEL}")

    assert response.status_code == 404
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    body = response.json()
    # The conversation store's own subject, which is a session id that is
    # not there. The 404 for a deployment with no store at all retired
    # with the file it was about (#283).
    assert "no session of that id" in refused(body, 404).lower()
    assert paths(body) == []
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
    assert paths(body) == ["/type", "/api_key_env"]
    for line, error in zip(body["detail"].splitlines()[1:], body["errors"], strict=True):
        assert line == f"  - {error['path'].removeprefix('/')}: {error['message']}"


# Where a model-level validator says its problem is
#
# The pointer per case is in the table above. What is left here is the
# one rule the table cannot express, because it is about two cases at
# once: an invented key is addressed by the object it was written in and
# never by itself, whichever depth that object is at.


def test_an_unrecognized_key_answers_the_parent_it_was_written_under(
    client: TestClient,
) -> None:
    """A key the model does not declare is a key the caller invented, so
    the refusal points at the object it was written in, never at the
    key. At the top of a fragment that object is the fragment, which is
    the empty pointer."""
    top = client.put("/agents/sam", json={"prompt": "You are Sam.", "surprise": 1})
    nested = client.put(
        "/agents/sam", json={"prompt": "You are Sam.", "filler": {"surprise": 1}}
    )

    for response in (top, nested):
        assert refused(response.json(), 422).startswith("invalid agents.sam:")
        assert "surprise" not in response.text
    assert paths(top.json()) == [""]
    assert paths(nested.json()) == ["/filler"]
    # And the two say the same thing about the key, since it is one
    # rule: what differs is where it was written.
    assert top.json()["errors"][0]["message"] == nested.json()["errors"][0]["message"]


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
    the route a request reaches it by, and the repository call under it
    that composes the same refusal.

    `sentinel` is what to look for, and it is a field rather than the
    module's constant because a key that matches a secret-shaped
    fragment and a key that does not are refused by different rules and
    only the second one reaches the rules underneath.
    """

    what: str
    path: str
    fragment: dict[str, object]
    write: Callable[[ConfigStore, dict[str, object]], None]
    sentinel: str = KEY_SENTINEL


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
        "an invented option key under a type that declares its own",
        "/providers/asr/ears",
        {"type": "faster_whisper", KEY_SENTINEL: 1},
        lambda store, fragment: store.set_provider("asr", "ears", fragment),
    ),
    PlantedKey(
        "an invented key inside a typed type's nested options",
        "/providers/tts/voice",
        {
            "type": "elevenlabs",
            "voice_id": "voice-1",
            "voice_settings": {KEY_SENTINEL: 1},
        },
        lambda store, fragment: store.set_provider("tts", "voice", fragment),
    ),
    PlantedKey(
        "a secret-shaped key a type's own model would have kept",
        "/providers/llm/local",
        {
            "type": "openai_compatible",
            "base_url": "http://localhost:11434/v1",
            "model": "qwen3:8b",
            KEY_SENTINEL: 1,
        },
        lambda store, fragment: store.set_provider("llm", "local", fragment),
    ),
    PlantedKey(
        "a credential-shaped key holding a credential-bearing URL",
        "/providers/llm/local",
        {
            "type": "openai_compatible",
            "base_url": "http://localhost:11434/v1",
            "model": "qwen3:8b",
            URL_KEY_SENTINEL: f"https://user:{URL_KEY_SENTINEL}@host/v1",
        },
        lambda store, fragment: store.set_provider("llm", "local", fragment),
        URL_KEY_SENTINEL,
    ),
    PlantedKey(
        "a secret-shaped key in an MCP server's env",
        "/mcp-servers/home",
        {"transport": "stdio", "command": "uvx", "env": {f"{KEY_SENTINEL}_TOKEN": "v"}},
        lambda store, fragment: store.set_mcp_server("home", fragment),
    ),
    # The two below are the same rule with the key half removed. An MCP
    # server's `url` is a field this repository declared, so the refusal
    # names it and the only place the sentinel can hide is the value,
    # which is the half that matters: a URL is where a credential travels
    # without a key that admits to holding one (#279).
    PlantedKey(
        "a credential written before the host of an MCP server's url",
        "/mcp-servers/home",
        {
            "transport": "streamable_http",
            "url": f"https://user:{URL_KEY_SENTINEL}@host/mcp",
        },
        lambda store, fragment: store.set_mcp_server("home", fragment),
        URL_KEY_SENTINEL,
    ),
    PlantedKey(
        "a credential in a query parameter of an MCP server's url",
        "/mcp-servers/home",
        {
            "transport": "streamable_http",
            "url": f"https://host/mcp?token={URL_KEY_SENTINEL}",
        },
        lambda store, fragment: store.set_mcp_server("home", fragment),
        URL_KEY_SENTINEL,
    ),
    # The two query spellings a provider option's narrower rule never
    # matched. A query parameter is named by the vendor whose endpoint
    # it addresses, so the rule reads the wider set of names (#279), and
    # the same widening reaches a provider's address, which is the third
    # row below.
    PlantedKey(
        "an auth query parameter on an MCP server's url",
        "/mcp-servers/home",
        {
            "transport": "streamable_http",
            "url": f"https://host/mcp?auth={URL_KEY_SENTINEL}",
        },
        lambda store, fragment: store.set_mcp_server("home", fragment),
        URL_KEY_SENTINEL,
    ),
    PlantedKey(
        "an authorization query parameter on an MCP server's url",
        "/mcp-servers/home",
        {
            "transport": "streamable_http",
            "url": f"https://host/mcp?authorization={URL_KEY_SENTINEL}",
        },
        lambda store, fragment: store.set_mcp_server("home", fragment),
        URL_KEY_SENTINEL,
    ),
    PlantedKey(
        "an auth query parameter on a provider's base_url",
        "/providers/llm/local",
        {
            "type": "openai_compatible",
            "model": "qwen3:8b",
            "base_url": f"https://host/v1?auth={URL_KEY_SENTINEL}",
        },
        lambda store, fragment: store.set_provider("llm", "local", fragment),
        URL_KEY_SENTINEL,
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
    assert case.sentinel not in body["detail"]
    for error in body["errors"]:
        assert case.sentinel not in error["path"]
        assert case.sentinel not in error["message"]
    assert case.sentinel not in response.text
    assert case.sentinel not in str(response.headers)

    text = logging.Formatter(logs.TEXT_FORMAT)
    for record in caplog.records:
        assert case.sentinel not in logs.JsonFormatter().format(record)
        assert case.sentinel not in text.format(record)


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
    assert case.sentinel not in str(refusal)
    assert case.sentinel not in repr(refusal)
    assert refusal.__cause__ is None
    assert refusal.__context__ is None
    for carried in refusal.problems:
        assert case.sentinel not in carried.path
        assert case.sentinel not in carried.message


# What the CLI prints for one


def test_the_cli_prints_the_same_sentence_for_a_problem_document(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    store: ConfigStore,
) -> None:
    """The compatibility claim, taken off two real refusals rather than
    a hand-built one: the command runs against a repository-backed API,
    the API answers `application/problem+json`, and what reaches the
    terminal is what the repository said, unchanged.

    Held against the repository's own refusal for the same act rather
    than against a copy of the sentence, so that the two are equal by
    assertion and neither is written down here.

    `_payload` accepts any content type holding `json`, and `_answer`
    reads `detail` and ignores the members it does not know, which is why
    no CLI change was needed. This is what holds that.
    """
    fragment = {"model": "m"}
    with pytest.raises(ConfigError) as caught:
        store.set_provider("llm", "claude", fragment)

    run = runner(monkeypatch)

    assert run("provider", "set", "llm", "claude", "-f", "-", stdin="model: m\n") == 1

    assert capsys.readouterr().err.rstrip("\n") == str(caught.value)


def test_the_cli_prints_a_typed_options_refusal_without_the_value(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    store: ConfigStore,
) -> None:
    """The third surface for #88's own refusal, after the exception and
    the response: what a terminal shows.

    A typed option refused for its shape is the case where the field is
    printed and the value must not be, and the two halves are one
    string: the CLI prints what the repository said.
    """
    fragment = {"type": "faster_whisper", "beam_size": SENTINEL}
    with pytest.raises(ConfigError) as caught:
        store.set_provider("asr", "ears", fragment)

    run = runner(monkeypatch)

    assert (
        run(
            "provider", "set", "asr", "ears",
            "-f",
            "-",
            stdin=f"type: faster_whisper\nbeam_size: {SENTINEL}\n",
        )
        == 1
    )
    printed = capsys.readouterr().err.rstrip("\n")

    assert printed == str(caught.value)
    assert "beam_size" in printed
    assert SENTINEL not in printed


def test_the_cli_prints_a_url_credential_refusal_without_the_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    store: ConfigStore,
) -> None:
    """The fourth surface for the same discipline, and the one the escape
    hatch made reachable.

    A provider entry has always accepted keys nobody declared, but until
    #88 one was a mistake on its way to a refusal. Now an undeclared key
    is a supported shape, and this rule is the one that reads what such
    a key HOLDS: a URL with a credential in it. The refusal has to say
    that a rule was broken and where to look without printing either
    half of what was written, since the key is as good a place to have
    pasted the credential as the URL is.
    """
    fragment = {
        "type": "openai_compatible",
        "base_url": "http://localhost:11434/v1",
        "model": "qwen3:8b",
        URL_KEY_SENTINEL: f"https://user:{URL_KEY_SENTINEL}@host/v1",
    }
    with pytest.raises(ConfigError) as caught:
        store.set_provider("llm", "local", fragment)

    run = runner(monkeypatch)

    assert (
        run(
            "provider", "set", "llm", "local",
            "-f",
            "-",
            stdin=(
                "type: openai_compatible\n"
                "base_url: http://localhost:11434/v1\n"
                "model: qwen3:8b\n"
                f"{URL_KEY_SENTINEL}: https://user:{URL_KEY_SENTINEL}@host/v1\n"
            ),
        )
        == 1
    )
    printed = capsys.readouterr().err.rstrip("\n")

    assert printed == str(caught.value)
    assert URL_KEY_SENTINEL not in printed
    # What is left is what an operator needs: the entry, the rule, and
    # what to do instead.
    assert "providers.llm.local" in printed
    assert "user and password before its host" in printed
    assert "api_key_env" in printed


@pytest.mark.parametrize(
    ("url", "rule"),
    [
        (
            f"https://user:{URL_KEY_SENTINEL}@host/mcp",
            "user and password before its host",
        ),
        (f"https://host/mcp?token={URL_KEY_SENTINEL}", "credential as a query parameter"),
        (f"https://host/mcp?auth={URL_KEY_SENTINEL}", "credential as a query parameter"),
        (
            f"https://host/mcp?authorization={URL_KEY_SENTINEL}",
            "credential as a query parameter",
        ),
    ],
    ids=["userinfo", "a token parameter", "an auth parameter", "an authorization parameter"],
)
def test_the_cli_prints_an_mcp_url_credential_refusal_without_the_value(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    store: ConfigStore,
    url: str,
    rule: str,
) -> None:
    """The same discipline for the rule one section over (#279).

    A provider's address is an option, so the refusal about it can only
    name the option when the type declared it. An MCP server's is a
    declared field of a closed model, so the refusal always names it,
    and what may not be printed is only the value. Both halves are one
    string again: the CLI prints what the repository said.

    Every shape the predicate answers, including the two query spellings
    a provider option's narrower rule never matched, because a terminal
    is where an operator meets the rule and a shape that is refused
    silently somewhere else is not refused here.
    """
    fragment = {"transport": "streamable_http", "url": url}
    with pytest.raises(ConfigError) as caught:
        store.set_mcp_server("weather", fragment)

    run = runner(monkeypatch)

    assert (
        run(
            "mcp-server", "set", "weather",
            "-f",
            "-",
            stdin=f"transport: streamable_http\nurl: {url}\n",
        )
        == 1
    )
    printed = capsys.readouterr().err.rstrip("\n")

    assert printed == str(caught.value)
    assert URL_KEY_SENTINEL not in printed
    # What is left is what an operator needs: the field, the rule, and
    # what to do instead.
    assert "mcp_servers.weather.url" in printed
    assert rule in printed
    assert "headers.Authorization" in printed


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
