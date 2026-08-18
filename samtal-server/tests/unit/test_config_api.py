"""The configuration API's sub-application: the gate and the answers.

Two properties carry this file. Nothing inside /api answers anything
but 401 without the right token, matched route or not, because the gate
runs before routing. And every refusal leaves as `{"detail": ...}` with
the repository's own sentence and the status code the plan fixes, with
no traceback and no echo of what was sent.

Milestone 1 has no routes, so the mapping is exercised through
throwaway routes registered on a test-built application: what is under
test is the handler wiring, and a route that raises on demand is the
smallest thing that reaches it.
"""

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from pydantic import BaseModel

from samtal_server.app import create_app
from samtal_server.config import Config, ConfigError, load_file_config
from samtal_server.config.api import (
    MALFORMED_REQUEST,
    MOUNT_PATH,
    UNAUTHORIZED,
    UNEXPECTED,
    ApiRuntime,
    StoreHandle,
    api_token,
    build_api,
    build_api_runtime,
    open_store,
    store_dependency,
)
from samtal_server.config.loader import DatabaseBusyError, StorageError, UnknownEntityError
from samtal_server.config.store import ConfigStore
from tests.support.apps import entered_client

TOKEN = "test-api-token-" + "0123456789abcdef" * 2

API_SECRET_ENV = "SAMTAL_API_SECRET"

# Not a real credential, and shaped so a substring check for it cannot
# match by accident.
SENTINEL = "sk-test-2b7d1f0a-never-a-real-credential"


@pytest.fixture
def api(tmp_path: Path) -> FastAPI:
    return build_api(TOKEN, tmp_path / "db")


@pytest.fixture
def client(api: FastAPI) -> TestClient:
    return TestClient(api)


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _route(api: FastAPI, path: str, raises: Exception):
    @api.get(path)
    def endpoint() -> dict[str, str]:
        raise raises

    return endpoint


# The gate


def test_a_request_without_a_token_is_refused(client: TestClient) -> None:
    response = client.get("/config")

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json() == {"detail": UNAUTHORIZED}
    # The body says how to authenticate and nothing else.
    assert "Authorization: Bearer" in UNAUTHORIZED


def test_an_unmatched_path_is_refused_before_it_is_routed(client: TestClient) -> None:
    """The reason the gate is middleware: a dependency runs only for a
    matched route, so an unauthenticated caller would learn which paths
    exist from the 404s."""
    for method, path in (
        ("get", "/no-such-route"),
        ("post", "/config"),
        ("delete", "/agents/sam"),
    ):
        response = getattr(client, method)(path)
        assert response.status_code == 401, path


def test_a_wrong_token_and_a_wrong_scheme_answer_alike(client: TestClient) -> None:
    """Never whether the token was close: the answer to a wrong one is
    the answer to no one at all."""
    missing = client.get("/config")
    wrong_scheme = client.get("/config", headers={"Authorization": f"Token {TOKEN}"})
    wrong_token = client.get("/config", headers=_bearer(TOKEN[:-1] + "0"))
    empty = client.get("/config", headers=_bearer(""))

    for response in (wrong_scheme, wrong_token, empty):
        assert response.status_code == missing.status_code
        assert response.json() == missing.json()


def test_the_scheme_is_matched_without_regard_to_case(client: TestClient) -> None:
    response = client.get("/no-such-route", headers={"Authorization": f"bearer {TOKEN}"})

    assert response.status_code == 404


def test_the_right_token_reaches_routing(client: TestClient) -> None:
    """Past the gate, routing decides: a path that is not a route is a
    404, and only an authenticated caller ever finds that out."""
    response = client.get("/no-such-route", headers=_bearer(TOKEN))

    assert response.status_code == 404


@pytest.mark.parametrize(
    "path", ["/config/", f"/agents/{SENTINEL}/", f"/providers/llm/{SENTINEL}/"]
)
def test_no_route_redirects_a_stray_trailing_slash(api: FastAPI, path: str) -> None:
    """The router's default answers `/config/` with a 307 whose Location
    is the request's own path, which for an entity route is the name the
    caller sent: a value quoted back in a response header, where a proxy
    and a browser both keep it. This namespace redirects nothing, so a
    stray slash is an unmatched path like any other.

    Nothing relied on the redirect. `/api` and `/api/` resolve through
    the mount, which is a different mechanism and is asserted below.
    """
    client = TestClient(api, follow_redirects=False)

    response = client.get(path, headers=_bearer(TOKEN))

    assert response.status_code == 404
    assert "location" not in response.headers
    assert SENTINEL not in response.text


def test_the_token_reaches_no_log_record(
    api: FastAPI, caplog: pytest.LogCaptureFixture
) -> None:
    """Whether the request carried it, got it wrong, or failed after it:
    the token is never logged, at any level."""
    _route(api, "/boom", RuntimeError("nothing to do with the token"))
    client = TestClient(api)

    with caplog.at_level(logging.DEBUG):
        client.get("/config")
        client.get("/config", headers=_bearer(TOKEN))
        client.get("/boom", headers=_bearer(TOKEN))

    for record in caplog.records:
        assert TOKEN not in record.getMessage()
        assert TOKEN not in str(record.__dict__)


# The refusals


REFUSALS = [
    (UnknownEntityError("agents.sam: no such agent"), 404),
    (DatabaseBusyError("the configuration database is busy"), 409),
    (StorageError("the options column does not hold an object with string keys"), 500),
    (ConfigError("invalid agents.sam: the fragment is wrong"), 422),
]


@pytest.mark.parametrize(("refusal", "status"), REFUSALS)
def test_each_refusal_maps_to_its_status(
    api: FastAPI, refusal: ConfigError, status: int
) -> None:
    _route(api, "/boom", refusal)

    response = TestClient(api).get("/boom", headers=_bearer(TOKEN))

    assert response.status_code == status
    # The repository's own sentence, unchanged: one vocabulary whether
    # an operator met it through the CLI or over HTTP.
    assert response.json() == {"detail": str(refusal)}


def test_an_unhandled_failure_is_a_generic_500(
    api: FastAPI,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The body says nothing, and neither does the log beyond the fact
    of the failure and its kind. An exception's own message is whatever
    a request put in front of the code that raised it, and a traceback
    carries the values that produced it, so neither is written: a log
    line is as much of a leak as a response body once the log is
    shipped somewhere."""
    _route(api, "/boom", RuntimeError(f"connection string with {SENTINEL} in it"))

    with caplog.at_level(logging.DEBUG):
        response = TestClient(api).get("/boom", headers=_bearer(TOKEN))

    assert response.status_code == 500
    assert response.json() == {"detail": UNEXPECTED}
    assert SENTINEL not in response.text

    # It happened, and what kind of failure it was.
    assert any(
        "failed to handle a request (RuntimeError)" in record.getMessage()
        for record in caplog.records
    )
    for record in caplog.records:
        assert SENTINEL not in record.getMessage()
        assert SENTINEL not in str(record.__dict__)
        # No traceback, here or anywhere an outer logger could pick the
        # exception up after this one answered.
        assert record.exc_info is None
        assert record.exc_text is None

    captured = capsys.readouterr()
    assert SENTINEL not in captured.err
    assert SENTINEL not in captured.out
    assert "Traceback" not in captured.err


def test_a_failure_after_the_response_started_is_not_re_raised(
    api: FastAPI, caplog: pytest.LogCaptureFixture
) -> None:
    """Once a response has started there is nothing left to say that
    would not corrupt it, and re-raising would only reach an outer
    logger, which would write the traceback this took care not to."""

    @api.get("/half")
    def endpoint() -> StreamingResponse:
        def chunks():
            yield b"half a response"
            raise RuntimeError(f"then {SENTINEL}")

        return StreamingResponse(chunks())

    with caplog.at_level(logging.DEBUG):
        # No pytest.raises: the exception ends in the middleware, so the
        # client sees a truncated response rather than a traceback.
        TestClient(api).get("/half", headers=_bearer(TOKEN))

    assert [record.getMessage() for record in caplog.records].count(
        "the configuration API failed to handle a request (RuntimeError)"
    ) == 1
    for record in caplog.records:
        assert SENTINEL not in str(record.__dict__)
        assert record.exc_info is None


def test_a_body_that_is_not_the_expected_shape_is_not_quoted_back(api: FastAPI) -> None:
    """FastAPI's own 422 echoes the rejected input back per error, and a
    fragment can carry a pasted credential."""

    class Body(BaseModel):
        name: str

    @api.post("/needs-a-body")
    def endpoint(body: Body) -> dict[str, str]:
        return {"name": body.name}

    client = TestClient(api)
    responses = [
        client.post("/needs-a-body", json={"api_key": SENTINEL}, headers=_bearer(TOKEN)),
        client.post("/needs-a-body", content=SENTINEL, headers=_bearer(TOKEN)),
        client.post("/needs-a-body", json=[SENTINEL], headers=_bearer(TOKEN)),
    ]

    for response in responses:
        assert response.status_code == 422
        assert response.json() == {"detail": MALFORMED_REQUEST}
        assert SENTINEL not in response.text


# The store dependency


def test_the_store_dependency_serves_the_engine_it_was_given(tmp_path: Path) -> None:
    """One engine per process, opened by a lifespan and handed to every
    request (#142). What a request gets is a view over it, so there is
    nothing to dispose when the request ends, and the next request reads
    what the last one wrote through the engine that is still open."""
    directory = tmp_path / "db"
    runtime = build_api_runtime(directory)
    with open_store(directory) as handle:
        runtime.store = handle

        generator = store_dependency(runtime)
        store = next(generator)
        assert isinstance(store, ConfigStore)
        store.set_agent("sam", {"prompt": "hello"})
        with pytest.raises(StopIteration):
            next(generator)

        assert store._engine is handle.engine
        assert "sam" in next(store_dependency(runtime)).load().domain.agents

    # And the engine goes when the lifespan that opened it goes.
    assert handle.engine.pool.checkedin() == 0


def test_a_request_with_no_engine_is_a_programming_error(tmp_path: Path) -> None:
    """An application whose lifespan never ran has no engine, and saying
    so is the whole answer: opening one here would be an engine nothing
    disposes, on an application nobody may serve requests from."""
    runtime = build_api_runtime(tmp_path / "db")

    with pytest.raises(RuntimeError, match="no database engine"):
        next(store_dependency(runtime))


def test_the_application_has_no_engine_until_its_lifespan_runs(api: FastAPI) -> None:
    """The standalone owner: `build_api` gives the application a lifespan
    of its own, which opens the engine on the way in and lets it go on
    the way out."""
    runtime: ApiRuntime = api.state.api_runtime
    assert runtime.store is None

    with TestClient(api):
        held: ApiRuntime = api.state.api_runtime
        assert isinstance(held.store, StoreHandle)

    assert runtime.store is None


# The mount


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    """The whole server, with the API mounted on it the way a
    deployment gets it.

    The lifespan is entered, because that is where the mounted API's
    database engine comes from (#142): Starlette runs no lifespan for a
    mounted application, so the server's own is what installs it, and a
    read through the mount is the thing that proves it.
    """
    monkeypatch.setenv(API_SECRET_ENV, TOKEN)
    config = Config(server={"database": {"dir": str(tmp_path / "db")}})
    with entered_client(config, follow_redirects=False) as client:
        yield client


@pytest.mark.parametrize("path", [MOUNT_PATH, f"{MOUNT_PATH}/", f"{MOUNT_PATH}/config"])
def test_the_whole_namespace_is_gated_without_a_redirect(
    served: TestClient, path: str
) -> None:
    """Both /api and /api/ resolve. A trailing-slash redirect would
    answer before the gate does, and a client that does not resend its
    Authorization header on one would meet a 401 it cannot explain."""
    response = served.get(path)

    assert response.status_code == 401, path
    assert response.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize("path", [MOUNT_PATH, f"{MOUNT_PATH}/", f"{MOUNT_PATH}/nothing"])
def test_the_namespace_answers_the_token_holder(served: TestClient, path: str) -> None:
    """With the token, routing answers: none of these is a route, and
    only an authenticated caller gets to find that out."""
    response = served.get(path, headers=_bearer(TOKEN))

    assert response.status_code == 404, path


def test_a_read_route_is_reachable_through_the_mount(served: TestClient) -> None:
    """The routes are served where the document says they are, which a
    test against the sub-application on its own cannot show."""
    response = served.get(f"{MOUNT_PATH}/config", headers=_bearer(TOKEN))

    assert response.status_code == 200
    assert response.json()["config"]["agents"] == {}


def test_the_device_facing_app_is_unchanged(served: TestClient) -> None:
    """No device path acquired a token requirement."""
    response = served.get("/healthz")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.parametrize("path", [f"{MOUNT_PATH}/", f"{MOUNT_PATH}/x/", f"{MOUNT_PATH}/ota/x/"])
def test_an_ota_path_inside_the_api_namespace_is_refused(tmp_path: Path, path: str) -> None:
    """The OTA endpoint is the one route that is deliberately
    unauthenticated (it issues the device tokens), and it is registered
    before the API is mounted, so a configured path under /api/ would
    be found first and would answer a request the gate never saw. The
    configuration is refused rather than the ordering quietly relied
    on.

    Read through the loader, which is where an operator meets it and
    where the message is rendered from the error rather than from
    pydantic's own str()."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(f"server:\n  ota_path: {path}\n", encoding="utf-8")

    with pytest.raises(ConfigError) as caught:
        load_file_config(config_file)

    message = str(caught.value)
    assert "server.ota_path" in message
    assert f"{MOUNT_PATH}/ is reserved" in message


def test_the_refused_ota_path_is_not_quoted_back(tmp_path: Path) -> None:
    """A public deployment hides the OTA endpoint behind a long random
    segment, which is the closest this key comes to a secret, so the
    refusal names the rule and not the value."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(f"server:\n  ota_path: {MOUNT_PATH}/8f3a9c2b/\n", encoding="utf-8")

    with pytest.raises(ConfigError) as caught:
        load_file_config(config_file)

    assert "8f3a9c2b" not in str(caught.value)


@pytest.mark.parametrize("path", ["/xiaozhi/ota/", "/apix/", "/xiaozhi/ota/8f3a9c2b/"])
def test_an_ota_path_outside_the_namespace_still_passes(path: str) -> None:
    assert Config(server={"ota_path": path}).server.ota_path == path


def test_the_reserved_path_is_the_one_the_api_is_mounted_at() -> None:
    """One string: a namespace reserved somewhere else than the mount
    would reserve nothing."""
    from samtal_server.config.models import API_MOUNT_PATH

    assert MOUNT_PATH == API_MOUNT_PATH


def test_a_server_without_a_token_refuses_to_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(API_SECRET_ENV, raising=False)

    with pytest.raises(ConfigError) as caught:
        create_app(Config())

    assert API_SECRET_ENV in str(caught.value)


def test_the_missing_token_is_what_a_boot_refuses_over_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deployment can arrive with more than one thing wrong, and what
    it is told about first is what its operator goes and fixes. The
    admin surface's own credential comes before anything the
    configuration references, so a forgotten variable is not reported as
    somebody else's problem."""
    monkeypatch.delenv(API_SECRET_ENV, raising=False)
    config = Config(
        providers={
            stage: {"mock": {"type": "mock"}} for stage in ("llm", "asr", "tts", "vad")
        },
        mcp_servers={
            "tools": {
                "transport": "stdio",
                "command": "/bin/true",
                # A reference nothing set, which is the other refusal
                # this boot has waiting for it.
                "env": {"API_TOKEN": "$SAMTAL_TEST_UNSET_MCP_TOKEN"},
            }
        },
        agent_defaults=dict.fromkeys(("llm", "asr", "tts", "vad"), "mock"),
        agents={"assistant": {"prompt": "A", "mcp": ["tools"]}},
        default_agent="assistant",
    )
    monkeypatch.delenv("SAMTAL_TEST_UNSET_MCP_TOKEN", raising=False)

    with pytest.raises(ConfigError) as caught:
        create_app(config)

    assert API_SECRET_ENV in str(caught.value)


# The token


def test_a_missing_token_refuses_the_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(API_SECRET_ENV, raising=False)

    with pytest.raises(ConfigError) as caught:
        api_token(Config())

    message = str(caught.value)
    assert API_SECRET_ENV in message
    assert "openssl rand -hex 32" in message
    assert MOUNT_PATH in message


def test_a_blank_token_counts_as_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(API_SECRET_ENV, "   ")

    with pytest.raises(ConfigError):
        api_token(Config())


def test_a_custom_variable_is_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(API_SECRET_ENV, raising=False)
    monkeypatch.setenv("MY_OWN_API_TOKEN", TOKEN)

    config = Config(server={"api": {"secret_env": "MY_OWN_API_TOKEN"}})

    assert api_token(config) == TOKEN
