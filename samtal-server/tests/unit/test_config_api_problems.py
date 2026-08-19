"""What a refusal body says, byte for byte.

The sentence a refusal carries is the repository's own, and #192 keeps
it that way while wrapping it in a problem document: `detail` is the
same string before and after, so an operator meets one vocabulary
whichever way they reached the API. Substring assertions cannot hold
that claim, because indentation, ordering and prefixes all survive
them, so the sentences below are goldens: the exact bodies of real
repository-backed PUTs, written out in full.

A golden that moves is either a bug or a decision. The one decision
this milestone makes is recorded on `MCP_TRANSPORT_REFUSAL` below.

Beside the goldens, the pydantic mechanism the structured half rests
on: a `ValueError` raised inside a model validator is reachable from
`ValidationError.errors()` as the error's context, which is what lets a
validator that knows its semantic field hand its problems up. A
pydantic release that stopped carrying it would silently flatten every
model-level refusal to one location, so it is pinned rather than
assumed.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError, model_validator

from samtal_server.config.api import build_api
from samtal_server.config.secrets import MASTER_KEY_ENV, generate_key

TOKEN = "test-api-token-" + "0123456789abcdef" * 2


@pytest.fixture
def keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MASTER_KEY_ENV, generate_key())


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

NESTED_SECRET_REFUSAL = (
    "invalid providers.llm.claude:\n"
    '  - "connection.api_key" looks like an inline secret, which is not allowed; '
    "reference an environment variable instead, for example "
    "connection.api_key_env: MY_PROVIDER_API_KEY"
)

FILLER_REFUSAL = (
    "invalid agents.sam:\n"
    "  - filler: filler.enabled is on with no phrases; add at least one, "
    'for example "Hmm, let me see..."'
)

# The one sentence this milestone changes, and the reason it changes:
# the transport validator finds several problems and joined them into
# one line with `; `, which is a line a form cannot decompose. Its
# problems become one entry each, so the prose becomes one line each,
# with the same words per problem and in the same order. Recorded in
# the implementation doc as a deliberate prose change.
MCP_TRANSPORT_REFUSAL = (
    "invalid mcp_servers.home:\n"
    '  - transport "stdio" needs "command"; '
    'transport "stdio" has no url; that belongs to the other transport; '
    "env.API_KEY looks like an inline secret, which is not allowed; reference an "
    "environment variable instead, for example API_KEY: $MY_SERVER_SECRET"
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
