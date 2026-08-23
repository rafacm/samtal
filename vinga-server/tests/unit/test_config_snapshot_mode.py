"""A server serving a configuration it was handed rather than read.

The shape a test lane has and an embedded caller can have: nothing
migrated a database before the app was built, so the snapshot the server
was given is the whole truth there is, and it is authoritative for
exactly that reason (`device/bindings.py`). What follows is what this
module is about. The mounted API opens a database beside that snapshot
and writes land in it, but nothing this process serves reads it, so the
two surfaces that span both sides have nothing to span: a comparison
would put the running world against a description of some other server,
and an apply would install that description as this server's whole
domain half.

All three answers are taken through a real server with its lifespan
entered, because the mode is decided by how the server was composed and
a stub would be asserting on the stub.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.support.apps import entered_client
from tests.support.configs import config_with
from tests.support.notices import STORE_BOOT, boundaries
from tests.support.problems import refused as refusal_body
from vinga_server.config import Config
from vinga_server.config.api import MOUNT_PATH

TOKEN = "test-api-token-" + "0123456789abcdef" * 2

API_SECRET_ENV = "VINGA_API_SECRET"

DEVICE = "aa:bb:cc:dd:ee:ff"

DIFF = f"{MOUNT_PATH}/runtime/config/diff"

RELOAD = f"{MOUNT_PATH}/runtime/config/reload"


def handed(directory: Path) -> Config:
    """A configuration composed in memory, naming a directory nothing
    has put a database in."""
    return config_with(
        server={"database": {"dir": str(directory / "db")}},
        devices={DEVICE: ["assistant"]},
    )


def seed(served: TestClient) -> None:
    """A world in the store beside the handed one, written through the
    API of the server that will not read it.

    Written rather than left empty because an empty store makes the two
    refusals below look easy: what they refuse is a store that describes
    a perfectly good server, just not this one.
    """
    for stage in ("llm", "asr", "tts", "vad"):
        served.put(f"{MOUNT_PATH}/providers/{stage}/mock", json={"type": "mock"})
    served.put(
        f"{MOUNT_PATH}/agent-defaults",
        json=dict.fromkeys(("llm", "asr", "tts", "vad"), "mock"),
    )
    served.put(f"{MOUNT_PATH}/agents/assistant", json={"prompt": "A"})


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.delenv("VINGA_CONFIG", raising=False)
    monkeypatch.setenv("VINGA_SERVER__DATABASE__DIR", str(tmp_path / "db"))
    monkeypatch.setenv(API_SECRET_ENV, TOKEN)
    with entered_client(
        handed(tmp_path), headers={"Authorization": f"Bearer {TOKEN}"}
    ) as served:
        seed(served)
        yield served


def test_the_comparison_refuses_rather_than_claim_the_world_is_pending(
    client: TestClient,
) -> None:
    """The latent edge this closes. The database beside a handed
    configuration holds whatever has been written to it since, which is
    not this server's world however complete it looks, so a comparison
    would answer about two servers at once."""
    answer = client.get(DIFF)

    assert answer.status_code == 409
    # The fact this mode turns on, in the refusal: this server's world
    # came from somewhere other than the store beside it.
    assert "store" in refusal_body(answer.json(), 409)


def test_the_apply_refuses_rather_than_install_another_servers_world(
    client: TestClient,
) -> None:
    answer = client.post(RELOAD)

    assert answer.status_code == 409
    detail = refusal_body(answer.json(), 409)
    assert "store" in detail
    # The one 409 in this API that making the request again will not
    # clear, and it says so rather than inviting a retry loop.
    assert "will not help" in detail


def test_a_device_write_says_it_is_stored_and_waits_for_a_start(
    client: TestClient,
) -> None:
    """The honest acknowledgement, and the whole of what is true here.
    A binding is live because a running server re-reads the rows; this
    one re-reads nothing, so what the write can promise is that it was
    stored."""
    bound = client.put(f"{MOUNT_PATH}/devices/{DEVICE}", json={"agents": ["assistant"]})

    assert bound.status_code == 200, bound.text
    assert bound.json()["wrote"] == f"device {DEVICE} bound to assistant"
    assert boundaries(bound.json()["notice"]) == {STORE_BOOT}
    # Stored, which is the half the acknowledgement promises: a server
    # started from this directory would read it.
    assert client.get(f"{MOUNT_PATH}/devices/{DEVICE}").json() == {
        "entity": {"agents": ["assistant"]},
        "secrets": {},
    }


def test_every_live_write_names_the_same_boundary(client: TestClient) -> None:
    """All four of the writes that are otherwise answered as live, since
    an answer right about one of them and wrong about the rest would be
    the trap this mode exists to avoid."""
    client.put(f"{MOUNT_PATH}/devices/{DEVICE}", json={"agents": ["assistant"]})

    answers = [
        client.put(f"{MOUNT_PATH}/default-agent", json={"name": "assistant"}),
        client.delete(f"{MOUNT_PATH}/default-agent"),
        client.delete(f"{MOUNT_PATH}/devices/{DEVICE}"),
    ]

    for answer in answers:
        assert answer.status_code == 200, answer.text
        assert boundaries(answer.json()["notice"]) == {STORE_BOOT}


def test_a_server_reading_a_store_answers_all_three_as_usual(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other side of the fact, so that the mode is what is being
    pinned rather than the routes. A second server started over the
    directory the first one's API created reads its configuration from
    it, and both surfaces answer."""
    monkeypatch.delenv("VINGA_CONFIG", raising=False)
    monkeypatch.setenv("VINGA_SERVER__DATABASE__DIR", str(tmp_path / "db"))
    monkeypatch.setenv(API_SECRET_ENV, TOKEN)
    with entered_client(
        handed(tmp_path), headers={"Authorization": f"Bearer {TOKEN}"}
    ) as first:
        seed(first)
        first.put(f"{MOUNT_PATH}/devices/{DEVICE}", json={"agents": ["assistant"]})

    with entered_client(headers={"Authorization": f"Bearer {TOKEN}"}) as second:
        assert second.get(DIFF).status_code == 200
        assert second.post(RELOAD).status_code == 200
        bound = second.put(
            f"{MOUNT_PATH}/devices/{DEVICE}", json={"agents": ["assistant"]}
        )

    assert STORE_BOOT not in boundaries(bound.json()["notice"])
