"""The two registers a running server keeps about devices.

One is in memory: which sessions are open, which is what a drain walks
on the way out. The other is on disk: which board is bound to which
agent, which is what an operator writes while the server runs. What
belongs here is the scaffolding for both, since a test about either is a
test about a server that is already up.

The scripted session answers the way a real one does about the only
thing a drain asks it (did your reply finish inside the grace) and
records how it was asked, so a drain test reads the record rather than
the clock. The binding half writes through the repository the CLI and
the API write through, deliberately on a different connection from the
one the running app reads.

`BINDINGS_DEVICE_MAC` is the normalized form the bindings table stores,
which is not the form `configs.DEVICE_MAC` presents at a handshake, so
it is a definition of its own rather than a second spelling of that one.
"""

import asyncio
import contextlib
from collections.abc import Iterator
from typing import Any, cast

from fastapi.testclient import TestClient

from tests.support.configs import BOUND_MAC, DEVICE_UUID
from vinga_server.config import Config, FileConfig, compose_config
from vinga_server.config.models import DatabaseConfig, domain_fields
from vinga_server.config.store import ConfigStore
from vinga_server.db import open_database
from vinga_server.ota import OTA_PATH
from vinga_server.registry import SessionRegistry

# --- the sessions a drain walks ---------------------------------------


class FakeSession:
    """A session that records how it was asked to stop, and answers the
    way a real one does: True when its reply finished inside the grace."""

    def __init__(self, speaking_for: float = 0.0) -> None:
        self.speaking_for = speaking_for
        self.shutdown: tuple[int, str] | None = None
        self.granted_s: float | None = None
        self.close_reason: str | None = None

    async def request_shutdown(
        self,
        code: int = 1001,
        reason: str = "server shutting down",
        grace_s: float = 10.0,
        close_reason: str | None = None,
    ) -> bool:
        self.granted_s = grace_s
        self.close_reason = close_reason
        finished = self.speaking_for <= grace_s
        await asyncio.sleep(min(self.speaking_for, grace_s))
        self.shutdown = (code, reason)
        return finished


def registry_with(*sessions: FakeSession, max_sessions: int = 8) -> SessionRegistry:
    registry = SessionRegistry(max_sessions=max_sessions)
    for session in sessions:
        assert registry.admit(cast(Any, session)) == "admitting"
    return registry


# --- the bindings an operator writes ----------------------------------


BINDINGS_DEVICE_MAC = "aa:bb:cc:dd:ee:ff"

STAGES = ("llm", "asr", "tts", "vad")
AGENT = dict.fromkeys(STAGES, "mock")


@contextlib.contextmanager
def store_at() -> Iterator[ConfigStore]:
    """The repository over this lane's database, the way the CLI and the
    API reach it: opened, used, disposed. This is the write side of
    every test here, and it is deliberately a different connection from
    the one the running app reads through."""
    engine = open_database(DatabaseConfig())
    try:
        yield ConfigStore(engine)
    finally:
        engine.dispose()


def booted(
    *,
    agents: tuple[str, ...] = ("assistant",),
    devices: dict[str, list[str]] | None = None,
    default_agent: str | None = None,
) -> Config:
    """The configuration a server booting on this lane's database would
    hold: the domain half written through the repository, read back, and
    composed onto a file half naming the same database.

    Really writing it is what makes this different from the rest of the
    unit lane, where a `Config` is composed in memory and there is no
    stored half at all.
    """
    if devices is None:
        devices = {BOUND_MAC: [agents[0]]}
    with store_at() as store:
        for stage in STAGES:
            store.set_provider(stage, "mock", {"type": "mock"})
        for name in agents:
            store.set_agent(name, dict(AGENT))
        for mac, bound in devices.items():
            store.bind_device(mac, bound)
        if default_agent is not None:
            store.set_default_agent(default_agent)
        snapshot = store.load()
    return compose_config(
        FileConfig(),
        domain_fields(snapshot.domain),
        "the test's database",
    )


def check_in(client: TestClient, device_id: str = BINDINGS_DEVICE_MAC) -> dict:
    """One OTA check-in, the way the firmware makes it."""
    response = client.post(
        OTA_PATH,
        json={"application": {"version": "2.4.0"}, "board": {"type": "test-board"}},
        headers={"Device-Id": device_id, "Client-Id": DEVICE_UUID},
    )
    assert response.status_code == 200
    return response.json()
