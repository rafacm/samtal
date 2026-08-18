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
itself makes (a missing auth secret, a missing API token, an unusable
enforcement mode). Those stay in the describe phase and are still
`pytest.raises(...)` around a bare `create_app`.
"""

import contextlib
from collections.abc import Iterator
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from samtal_server.app import create_app
from samtal_server.config import Config
from samtal_server.config.secrets import SecretStore


@contextlib.contextmanager
def entered_app(
    config: Config | None = None,
    secrets: SecretStore | None = None,
    **client_options: Any,
) -> Iterator[tuple[FastAPI, TestClient]]:
    """`with entered_app(config) as (app, client): ...`

    The app is built, its lifespan entered, and everything it built
    released on the way out. `client_options` are `TestClient`'s own
    (headers, follow_redirects, base_url).
    """
    app = create_app(config, secrets)
    with TestClient(app, **client_options) as client:
        yield app, client


@contextlib.contextmanager
def entered_client(
    config: Config | None = None,
    secrets: SecretStore | None = None,
    **client_options: Any,
) -> Iterator[TestClient]:
    """`with entered_client(config) as client: ...`, for a test that only
    makes requests."""
    with entered_app(config, secrets, **client_options) as (_, client):
        yield client
