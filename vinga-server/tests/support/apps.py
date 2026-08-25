"""Whole applications, with their lifespan entered.

`create_app` describes an application and builds none of its resources
(#142): the bindings engine, the providers, the conversation store, the
MCP managers and the configuration API's live pieces are all the
lifespan's, so a test that touches any of them, or that makes a request
to any route that reads them, has to enter it. `TestClient` enters the
lifespan when it is used as a context manager and not otherwise, which
is what these two wrap.

Two functions rather than one because two things are wanted: the client,
which is what a request goes through, and the app, which is what a test
reaches server-side state on (`app.state.composition`). `entered_client`
is the common case.

What does NOT belong here is a test that inspects a refusal `create_app`
itself makes (a missing auth secret, a missing API token). Those stay in
the describe phase and are still `pytest.raises(...)` around a bare
`create_app`.
"""

import contextlib
from collections.abc import Iterator
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from vinga_server.app import create_app
from vinga_server.config import Config
from vinga_server.config.api import mount_api
from vinga_server.config.secrets import SecretStore


@contextlib.contextmanager
def entered_app(
    config: Config | None = None,
    secrets: SecretStore | None = None,
    from_store: bool = False,
    **client_options: Any,
) -> Iterator[tuple[FastAPI, TestClient]]:
    """`with entered_app(config) as (app, client): ...`

    The app is built, its lifespan entered, and everything it built
    released on the way out. `client_options` are `TestClient`'s own
    (headers, follow_redirects, base_url).

    `from_store` says the configuration handed in stands for one read
    from the database, which a test that seeded the same database first
    is doing. It decides whether device bindings resolve live and
    whether the two surfaces spanning a store and a running world have
    anything to span. False is the honest default here: most of this
    lane composes a `Config` in Python and no store describes it.
    """
    app = create_app(config, secrets, from_store=from_store)
    with TestClient(app, **client_options) as client:
        yield app, client


@contextlib.contextmanager
def entered_client(
    config: Config | None = None,
    secrets: SecretStore | None = None,
    from_store: bool = False,
    **client_options: Any,
) -> Iterator[TestClient]:
    """`with entered_client(config) as client: ...`, for a test that only
    makes requests."""
    with entered_app(config, secrets, from_store, **client_options) as (_, client):
        yield client


def mounted(api: FastAPI) -> FastAPI:
    """A host for the configuration API, mounted where a server mounts
    it, and lent the API's own lifespan.

    A test that wants the mount prefix without a whole server builds a
    bare `FastAPI` and mounts the API on it. Starlette runs no lifespan
    for a mounted application, so that host leaves the API with the one
    thing it cannot serve requests without: the database engine its
    lifespan opens (#142). A real server has a lifespan of its own and
    installs it from there; a host that exists only to provide the prefix
    has nothing else to do, so the API's lifespan becomes its whole
    lifespan and the engine is opened and disposed with the client.
    """
    host = FastAPI(lifespan=api.router.lifespan_context)
    mount_api(host, api)
    return host
