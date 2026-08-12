"""The two routes that turn a code on a screen into a binding.

The listing answers "which of these is the board on my desk", and the
claim binds it through the same repository call binding by MAC uses, so
reference checking and transactionality are inherited rather than
restated. What is checked here is the transport around that: the path,
the status codes, the sentences, the claim lifecycle under contention,
and the one mechanical constraint the plan names out loud, that the
literal word `pending` never enters MAC normalization.
"""

import threading
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from samtal_server.config.api import (
    CODE_IN_FLIGHT,
    MOUNT_PATH,
    UNKNOWN_CODE,
    build_api,
    mount_api,
)
from samtal_server.config.secrets import MASTER_KEY_ENV, generate_key
from samtal_server.config.store import ConfigStore
from samtal_server.config.writes import BINDING_NOTICE, RESTART_NOTICE
from samtal_server.onboarding import CODE_TTL_S, PendingDevices
from tests.unit.test_onboarding_pending import Clock

TOKEN = "test-api-token-" + "0123456789abcdef" * 2

MAC = "aa:bb:cc:dd:ee:ff"
OTHER_MAC = "11:22:33:44:55:66"
UUID = "6f1a2b3c-4d5e-6f70-8192-a3b4c5d6e7f8"
BOARD = "waveshare-esp32-s3-touch-lcd-1.54"
FIRMWARE = "2.4.0"


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def pending(clock: Clock) -> PendingDevices:
    return PendingDevices(clock)


@pytest.fixture
def client(
    tmp_path: Path, pending: PendingDevices, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setenv(MASTER_KEY_ENV, generate_key())
    api = build_api(TOKEN, tmp_path / "db", ["assistant"], pending)
    with TestClient(api, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        _agents(client)
        yield client


def _agents(client: TestClient) -> None:
    """A configuration a device can be bound to, written the way a first
    deployment writes one."""
    for stage in ("llm", "asr", "tts", "vad"):
        assert client.put(f"/providers/{stage}/mock", json={"type": "mock"}).status_code == 200
    body = dict.fromkeys(("llm", "asr", "tts", "vad"), "mock")
    assert client.put("/agents/assistant", json=body).status_code == 200
    # A second agent, which this application was not told its server
    # loaded: what a binding written after a boot names.
    assert client.put("/agents/written-since-boot", json=body).status_code == 200


def _waiting(pending: PendingDevices, mac: str = MAC) -> str:
    return pending.observe(mac, UUID, BOARD, FIRMWARE).device.code


def _claim(client: TestClient, code: str, *agents: str):
    return client.post(f"/devices/pending/{code}", json={"agents": list(agents) or ["assistant"]})


# The listing


def test_nothing_waiting_is_an_empty_listing(client: TestClient) -> None:
    response = client.get("/devices/pending")

    assert response.status_code == 200
    assert response.json() == {}


def test_the_listing_is_keyed_by_the_code_the_device_is_showing(
    client: TestClient, pending: PendingDevices, clock: Clock
) -> None:
    code = _waiting(pending)

    entries = client.get("/devices/pending").json()

    assert list(entries) == [code]
    entry = entries[code]
    assert entry["mac"] == MAC
    assert entry["client_id"] == UUID
    assert entry["board"] == BOARD
    assert entry["firmware"] == FIRMWARE
    # The code is the key it is filed under, not a field inside it.
    assert "code" not in entry
    assert datetime.fromisoformat(entry["first_seen"]).timestamp() == clock.now
    assert datetime.fromisoformat(entry["last_seen"]).timestamp() == clock.now
    assert datetime.fromisoformat(entry["expires_at"]).timestamp() == clock.now + CODE_TTL_S


def test_an_expired_code_leaves_the_listing(
    client: TestClient, pending: PendingDevices, clock: Clock
) -> None:
    _waiting(pending)
    clock.advance(CODE_TTL_S)

    assert client.get("/devices/pending").json() == {}


def test_the_listing_needs_the_token(client: TestClient) -> None:
    assert client.get("/devices/pending", headers={"Authorization": ""}).status_code == 401


def test_the_literal_word_pending_never_enters_mac_normalization(
    client: TestClient, pending: PendingDevices
) -> None:
    """Starlette matches routes in registration order, so the listing has
    to be registered before /devices/{mac}. Registered the other way
    round, this request would meet the MAC validator and answer 422 with
    a sentence about hex pairs."""
    _waiting(pending)

    response = client.get("/devices/pending")

    assert response.status_code == 200
    assert "MAC" not in response.text
    assert isinstance(response.json(), dict)


def test_the_listing_is_reachable_where_the_server_mounts_it(
    tmp_path: Path, pending: PendingDevices
) -> None:
    """The same route through the mount, since the route order that
    matters is the one inside the sub-application and the prefix is what
    a client actually types."""
    _waiting(pending)
    served = FastAPI()
    mount_api(served, build_api(TOKEN, tmp_path / "db", ["assistant"], pending))
    client = TestClient(served, headers={"Authorization": f"Bearer {TOKEN}"})

    response = client.get(f"{MOUNT_PATH}/devices/pending")

    assert response.status_code == 200
    assert len(response.json()) == 1


def test_a_bound_devices_listing_carries_no_code(
    client: TestClient, pending: PendingDevices
) -> None:
    """Codes belong to the pending listing and nowhere else: the bound
    devices are configuration, and a code is not part of one."""
    code = _claim_and_read(client, pending)

    assert code not in client.get("/devices").text
    assert code not in client.get(f"/devices/{MAC}").text
    assert code not in client.get("/config").text


def _claim_and_read(client: TestClient, pending: PendingDevices) -> str:
    code = _waiting(pending)
    assert _claim(client, code).status_code == 200
    return code


# The claim


def test_claiming_a_code_binds_the_device_it_belongs_to(
    client: TestClient, pending: PendingDevices
) -> None:
    code = _waiting(pending)

    response = _claim(client, code)

    assert response.status_code == 200, response.text
    # The acknowledgement names the MAC that was bound, which is the
    # thing the operator did not have to go and find.
    assert response.json()["wrote"] == f"device {MAC} bound to assistant"
    assert client.get(f"/devices/{MAC}").json()["entity"] == {"agents": ["assistant"]}


def test_a_claim_says_the_device_needs_no_restart(
    client: TestClient, pending: PendingDevices
) -> None:
    response = _claim(client, _waiting(pending))

    assert response.json()["notice"] == BINDING_NOTICE


def test_a_claim_naming_an_agent_this_server_has_not_loaded_says_restart(
    client: TestClient, pending: PendingDevices
) -> None:
    """A fresh deployment's ordinary case: the agent was written after
    this server booted, so the binding is live and the agent is not.
    Saying "no restart is needed" there would be a promise the device
    cannot keep."""
    response = _claim(client, _waiting(pending), "written-since-boot")

    assert response.status_code == 200, response.text
    assert response.json()["notice"] == RESTART_NOTICE


def test_a_claim_retires_the_code(client: TestClient, pending: PendingDevices) -> None:
    code = _waiting(pending)
    _claim(client, code)

    assert client.get("/devices/pending").json() == {}
    second = _claim(client, code)
    assert second.status_code == 404
    assert second.json()["detail"] == UNKNOWN_CODE


def test_an_unknown_code_points_at_the_screen(client: TestClient) -> None:
    response = _claim(client, "000000")

    assert response.status_code == 404
    assert response.json()["detail"] == UNKNOWN_CODE
    assert "on the device's screen" in response.json()["detail"]
    # Never quoted back: what arrived is whatever was typed into the
    # path, and what is worth saying is what to read instead.
    assert "000000" not in response.text


def test_an_expired_code_is_answered_the_same_way(
    client: TestClient, pending: PendingDevices, clock: Clock
) -> None:
    code = _waiting(pending)
    clock.advance(CODE_TTL_S)

    assert _claim(client, code).json()["detail"] == UNKNOWN_CODE


def test_a_code_being_claimed_right_now_is_a_retryable_refusal(
    client: TestClient, pending: PendingDevices
) -> None:
    code = _waiting(pending)
    pending.reserve(code)

    response = _claim(client, code)

    assert response.status_code == 409
    assert response.json()["detail"] == CODE_IN_FLIGHT
    assert "run the command again" in response.json()["detail"]


def test_two_concurrent_claims_of_one_code_bind_it_once(
    client: TestClient, pending: PendingDevices, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The race the claim lifecycle exists for, through the real routes
    and made deterministic: the second claim arrives while the first is
    inside the repository write, which is the window the reservation
    covers and the only one wide enough to be raced by hand.

    One bind and one retryable refusal, never two binds.
    """
    code = _waiting(pending)
    writing, proceed = threading.Event(), threading.Event()
    write = ConfigStore.bind_device

    def held(self: ConfigStore, mac: str, agents: list[str]) -> None:
        writing.set()
        assert proceed.wait(timeout=30)
        write(self, mac, agents)

    monkeypatch.setattr(ConfigStore, "bind_device", held)
    first: list = []
    claim = threading.Thread(target=lambda: first.append(_claim(client, code)))
    claim.start()
    try:
        assert writing.wait(timeout=30)
        second = _claim(client, code)
    finally:
        proceed.set()
        claim.join(timeout=30)

    assert second.status_code == 409
    assert second.json()["detail"] == CODE_IN_FLIGHT
    assert first[0].status_code == 200, first[0].text
    assert client.get(f"/devices/{MAC}").json()["entity"] == {"agents": ["assistant"]}


def test_a_refused_write_leaves_the_code_claimable(
    client: TestClient, pending: PendingDevices
) -> None:
    """Reference checking is the repository's, inherited rather than
    restated, and its refusal is the repository's own sentence. The
    device is still showing the number, so the number has to still
    work."""
    code = _waiting(pending)

    refused = _claim(client, code, "no-such-agent")

    assert refused.status_code == 422
    assert "no-such-agent" in refused.json()["detail"]
    assert client.get("/devices/pending").json()[code]["mac"] == MAC
    assert _claim(client, code).status_code == 200


def test_a_malformed_body_neither_binds_nor_burns_the_code(
    client: TestClient, pending: PendingDevices
) -> None:
    code = _waiting(pending)

    refused = client.post(f"/devices/pending/{code}", json={"agent": "assistant"})

    assert refused.status_code == 422
    assert '"agents"' in refused.json()["detail"]
    assert _claim(client, code).status_code == 200


def test_the_claim_needs_the_token(client: TestClient, pending: PendingDevices) -> None:
    code = _waiting(pending)

    response = client.post(
        f"/devices/pending/{code}", json={"agents": ["assistant"]}, headers={"Authorization": ""}
    )

    assert response.status_code == 401
    # And nothing happened behind the gate.
    assert client.get("/devices/pending").json()[code]["mac"] == MAC


def test_each_waiting_device_is_claimed_by_its_own_code(
    client: TestClient, pending: PendingDevices
) -> None:
    first = _waiting(pending)
    second = _waiting(pending, OTHER_MAC)

    assert _claim(client, second).json()["wrote"] == f"device {OTHER_MAC} bound to assistant"
    assert client.get("/devices/pending").json()[first]["mac"] == MAC
