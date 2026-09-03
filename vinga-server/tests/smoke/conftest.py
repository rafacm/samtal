"""The opt-in smoke lane: the milestone acceptance, against a container.

The unit and integration lanes run the server in-process. This one runs
nothing itself: it points at a server that is already up, which in CI is
the freshly built image and locally is whatever you started, and holds a
real conversation with it. That makes "`docker run` with one mounted
YAML serves a conversation" something CI checks rather than something
somebody remembers to try.

Opt in with VINGA_SMOKE_OTA_URL, the same pattern as the local lane, so
a bare `pytest` never picks this up. VINGA_AUTH_SECRET must be the
secret the server under test was started with: the lane verifies the
token it is issued, which is only possible with the signing key.

This lane deliberately does NOT call `provision_stores`, which the other
three lanes call from their own conftest. It stores nothing: the server
under test owns its database and this side of the wire only speaks HTTP
and a websocket. In CI that is not a preference but a requirement, since
the pytest runs on the runner while the database sits on a Docker
network only the containers can resolve, and reaching for one here is
what turned a green image into a failed job.
"""

import os
import urllib.error
import urllib.parse
import urllib.request

import pytest

OTA_URL_ENV = "VINGA_SMOKE_OTA_URL"
AUTH_SECRET_ENV = "VINGA_AUTH_SECRET"

# What the lane speaks to the server it points at.
DEVICE_MAC = "aa:bb:cc:dd:ee:ff"


@pytest.fixture(scope="session")
def ota_url() -> str:
    url = os.environ.get(OTA_URL_ENV)
    if not url:
        pytest.skip(f"the smoke lane is opt-in: set {OTA_URL_ENV} to a running server")
    return url


@pytest.fixture(scope="session")
def base_url(ota_url: str) -> str:
    """The server's origin, for anything that is not the OTA path."""
    parts = urllib.parse.urlsplit(ota_url)
    return f"{parts.scheme}://{parts.netloc}"


@pytest.fixture(scope="session")
def device_auth(ota_url: str):
    """The issuer the server under test is using, so the lane can verify
    a token rather than merely observe that one was sent."""
    from vinga_server.auth import DeviceAuth
    from vinga_server.config.models import AuthConfig

    secret = os.environ.get(AUTH_SECRET_ENV, "")
    if not secret:
        pytest.fail(
            f"{AUTH_SECRET_ENV} must hold the secret the server under test was "
            f"started with, so the lane can verify the token it is issued",
            pytrace=False,
        )
    return DeviceAuth(secret, AuthConfig().token_expire_s)


@pytest.fixture(scope="session")
def wait_for_server(base_url: str) -> None:
    """A container that has just started may not be listening yet.

    Readiness is what is waited for, because every case behind this
    fixture goes on to talk to the server as a device would, and
    readiness is the answer to whether it may.
    """
    import time

    deadline = time.monotonic() + 60
    while True:
        try:
            with urllib.request.urlopen(f"{base_url}/readyz", timeout=5):
                return
        except (urllib.error.URLError, OSError):
            if time.monotonic() > deadline:
                pytest.fail(
                    f"no server answered at {base_url}/readyz within 60 s", pytrace=False
                )
            time.sleep(1)
