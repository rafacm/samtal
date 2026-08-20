"""The diff read as the composition root wires it: one world, or a
refusal.

The transport around the route is `test_config_api_runtime.py`'s and
what the comparison decides is `test_config_diff.py`'s and
`test_mcp_pending.py`'s. What is left here is what only the composition
root does, and all three parts of it are things a stub cannot show.

The stored half is the reload's own re-read, so a stored world the
reload would refuse is refused here in the same words and under the same
status, which takes a real database and a real key to demonstrate. The
running half is read either side of that database read, so an answer is
one world or it is no answer at all, which takes a reload landing in the
middle of one. And nothing of a credential travels, which takes a
credential: the plaintext, the ciphertext the database holds, the mark
taken over it and the name of an environment variable are four distinct
sentinels here, each asserted absent from the answer, from a refusal,
and from what the server wrote about either.
"""

import asyncio
import json
import threading
from collections.abc import Callable
from pathlib import Path

import pytest
from sqlalchemy import text

from tests.support.apps import entered_client
from tests.support.configs import config_with
from tests.support.tools_mcp import entry_data
from vinga_server.app import DIFF_LOADS, config_diff_reader
from vinga_server.config import Config
from vinga_server.config.api import MOUNT_PATH
from vinga_server.config.boot import BootConfig, load_boot_config
from vinga_server.config.loader import RunningConfigMovedError
from vinga_server.config.secrets import (
    MASTER_KEY_ENV,
    SecretLocation,
    SecretStore,
    generate_key,
    load_keys,
)
from vinga_server.config.store import ConfigStore
from vinga_server.db import open_database
from vinga_server.logs import JsonFormatter
from vinga_server.tools.mcp import McpServers

TOKEN = "test-api-token-" + "0123456789abcdef" * 2

API_SECRET_ENV = "VINGA_API_SECRET"

DIFF_PATH = f"{MOUNT_PATH}/runtime/config/diff"

STAGES = ("llm", "asr", "tts", "vad")

# The four forms a stored credential takes, each planted where an answer
# that carried it would have to put it, and each shaped so that a
# substring check for it cannot match by accident.
#
# The plaintext is what an operator typed. The envelope is what the
# database holds, which a read that serialized a row rather than a name
# would carry. The mark is what the comparison itself asks about, which
# is the one an implementation could plausibly put in an answer by
# accident, since it is opaque and looks harmless. And the environment
# variable's name is the fourth: it is not a credential, but it says
# where one is kept, and it is written in the entity body this read must
# never echo.
PLAINTEXT = "sk-diff-1f2e3d4c-never-a-real-credential"

ENV_NAME = "VINGA_DIFF_SENTINEL_ENV_5b7c9d"


def headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The database directory a deployment names through its
    environment, which is what makes `load_boot_config` read this test's
    database rather than a real one's."""
    monkeypatch.delenv("VINGA_CONFIG", raising=False)
    monkeypatch.setenv("VINGA_SERVER__DATABASE__DIR", str(tmp_path / "db"))
    monkeypatch.setenv(API_SECRET_ENV, TOKEN)
    return tmp_path / "db"


@pytest.fixture
def keys(monkeypatch: pytest.MonkeyPatch) -> str:
    key = generate_key()
    monkeypatch.setenv(MASTER_KEY_ENV, key)
    return key


def stored(directory: Path, secret: str | None = None, **entries: object) -> None:
    """A deployment's stored domain half, written the way the API writes
    it: four mock providers, the defaults that name them, one agent and
    the default agent, plus whatever a case adds."""
    engine = open_database(directory)
    try:
        store = ConfigStore(engine, load_keys())
        for stage in STAGES:
            fragment = entries.get(stage, {"type": "mock"})
            store.set_provider(stage, "mock", fragment)
        store.set_agent_defaults(dict.fromkeys(STAGES, "mock"))
        store.set_agent("assistant", {"prompt": "A"})
        store.set_default_agent("assistant")
        for name, entry in entries.get("mcp_servers", {}).items():  # type: ignore[union-attr]
            store.set_mcp_server(name, entry)
        if secret is not None:
            store.set_secret(SecretLocation.provider("llm", "mock", "api_key"), secret)
    finally:
        engine.dispose()


def envelope_of(directory: Path) -> str:
    """The ciphertext the database holds for the planted slot, read as a
    row rather than through the store: what must not travel is the bytes
    on disk, so the sentinel has to be taken from the disk."""
    engine = open_database(directory)
    try:
        with engine.connect() as connection:
            secrets = connection.execute(
                text("select secrets from providers where stage = 'llm' and name = 'mock'")
            ).scalar_one()
    finally:
        engine.dispose()
    # Read as a column rather than through the store, so what comes
    # back is the text the file holds rather than a value some accessor
    # decoded.
    return str(json.loads(secrets)["api_key"]["enc"])


def written(caplog: pytest.LogCaptureFixture) -> str:
    """What the server kept about a request, in both shipped formats."""
    return caplog.text + "".join(
        JsonFormatter().format(record) for record in caplog.records
    )


# The stored half, which is the reload's own re-read


@pytest.mark.usefixtures("keys")
def test_a_stored_secret_that_will_not_open_refuses_as_the_reload_does(
    directory: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stored side runs `reload_domain_config`, which verifies that
    every stored credential opens before it composes anything, so a
    deployment whose key has been rotated away from its secrets meets
    the same refusal here as it would at a reload: the repository's own
    sentence, naming the slot and never the value."""
    stored(directory, secret=PLAINTEXT)
    booted = load_boot_config()

    with entered_client(booted.config, booted.secrets) as served:
        # The key the secret was written under is gone from the
        # environment, which is what a mistaken rotation looks like from
        # the inside of a running server.
        monkeypatch.setenv(MASTER_KEY_ENV, generate_key())
        refused = served.get(DIFF_PATH, headers=headers())

    assert refused.status_code == 422
    detail = refused.json()["detail"]
    assert "cannot be decrypted" in detail
    assert "provider llm.mock api_key" in detail


@pytest.mark.usefixtures("keys")
def test_a_stored_domain_that_will_not_compose_refuses_the_same_way(
    directory: Path,
) -> None:
    """Model-valid rows that are not a valid deployment: an agent naming
    a provider nothing declares. No write this server offers can produce
    it, which is why it is planted as a row, and it is exactly what an
    interrupted migration or another build's write can leave behind. The
    whole-snapshot validation is part of the re-read, so this is a
    refusal rather than an answer computed over half a world."""
    stored(directory)
    booted = load_boot_config()
    engine = open_database(directory)
    try:
        with engine.begin() as connection:
            connection.execute(text("update agents set llm = 'ghost'"))
    finally:
        engine.dispose()

    with entered_client(booted.config, booted.secrets) as served:
        refused = served.get(DIFF_PATH, headers=headers())

    assert refused.status_code == 422
    assert 'unknown llm provider "ghost"' in refused.json()["detail"]


# What must not travel


@pytest.mark.usefixtures("keys")
def test_neither_an_answer_nor_a_refusal_carries_a_credential(
    directory: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """All four forms, over both paths, in the body and in the log.

    The successful answer is the path that reads the mark, decides an
    entity is changed by it, and has to say so with a name. The refusal
    is the path where the stored half could not be read at all, which is
    where an implementation reaching for detail would reach for the
    thing it failed on.
    """
    stored(directory, secret=PLAINTEXT, llm={"type": "mock", "api_key_env": ENV_NAME})
    booted = load_boot_config()
    ciphertext = envelope_of(directory)
    mark = booted.secrets.fingerprint("provider", "llm.mock")
    sentinels = (PLAINTEXT, ciphertext, mark, ENV_NAME)
    # Four distinct strings that are actually there, so an absence
    # asserted below is an absence rather than an empty needle.
    assert len(set(sentinels)) == 4
    assert all(sentinels)

    with caplog.at_level("INFO"):
        with entered_client(booted.config, booted.secrets) as served:
            # A rotation of the same slot, so the entity really is
            # reported as changed: this is the answer that has the most
            # to leak.
            rotated = served.put(
                f"{MOUNT_PATH}/providers/llm/mock/secrets/api_key",
                json={"secret": "another-value-entirely"},
                headers=headers(),
            )
            assert rotated.status_code == 200, rotated.text
            answered = served.get(DIFF_PATH, headers=headers())

            assert answered.status_code == 200, answered.text
            assert answered.json()["providers"]["changed"] == ["llm.mock"]

            # And the refusal, forced by taking the key away.
            monkeypatch.setenv(MASTER_KEY_ENV, generate_key())
            refused = served.get(DIFF_PATH, headers=headers())
            assert refused.status_code == 422

    for sentinel in sentinels:
        assert sentinel not in answered.text
        assert sentinel not in refused.text
        assert sentinel not in written(caplog)


# One world, or none


def moving(config: Config) -> Callable[[], tuple[Config, SecretStore | None]]:
    """A reload's re-read of a configuration a test already has, which
    is what makes a reload land where the test wants it."""

    def read() -> tuple[Config, SecretStore | None]:
        return config, None

    return read


class _Gated:
    """A stored read a test lets through one call at a time.

    The read is where the diff spends its await, so it is where a reload
    has to land to be the race this is about. Semaphores rather than
    events because the calls are counted: the refusal case releases the
    read as many times as the bound allows.
    """

    def __init__(self, answer: Config) -> None:
        self._answer = answer
        self._started = threading.Semaphore(0)
        self._release = threading.Semaphore(0)
        self.reads = 0

    def __call__(self) -> BootConfig:
        self.reads += 1
        self._started.release()
        assert self._release.acquire(timeout=30)
        return BootConfig(self._answer, SecretStore())

    async def in_flight(self) -> None:
        """Wait, off the loop, for a read to be inside the worker
        thread."""
        assert await asyncio.to_thread(self._started.acquire, True, 30)

    def let_through(self) -> None:
        self._release.release()


BEFORE = config_with(mcp_servers={"tools": entry_data()})

AFTER = config_with(mcp_servers={"tools": entry_data(instructions="Ask first.")})


async def test_a_world_that_moves_under_a_read_is_read_again() -> None:
    """A reload lands between the stored read and the composition, and
    the answer describes the world that reload installed.

    The entry the reload applies is the entry the database holds, so a
    diff composed across the change would report `tools` as changed
    against the world it was serving a moment ago, which is a difference
    that no longer exists anywhere. The mark says the world moved, the
    stored half is read again, and what comes out is the empty answer
    that is true of the world running now.
    """
    servers, read = McpServers.build(BEFORE), _Gated(AFTER)
    diff = config_diff_reader(BootConfig(BEFORE, SecretStore()), servers, read)

    answering = asyncio.create_task(diff())
    await read.in_flight()
    installed = servers.generation
    await servers.reload_result(moving(AFTER))
    assert servers.generation > installed
    read.let_through()
    # The second read runs in a world that is holding still.
    await read.in_flight()
    read.let_through()

    answer = await answering
    assert answer.mcp_servers.changed == ()
    assert answer.agents.grants.changed == ()
    assert read.reads == 2


async def test_a_world_that_keeps_moving_refuses_rather_than_mix() -> None:
    """The bound, and what happens at the end of it. Every attempt is
    overtaken by a reload, so no attempt ever holds one world, and the
    answer is the retryable refusal rather than a comparison across two
    of them."""
    servers, read = McpServers.build(BEFORE), _Gated(AFTER)
    diff = config_diff_reader(BootConfig(BEFORE, SecretStore()), servers, read)

    answering = asyncio.create_task(diff())
    for _ in range(DIFF_LOADS):
        await read.in_flight()
        await servers.reload_result(moving(AFTER))
        read.let_through()

    with pytest.raises(RunningConfigMovedError) as caught:
        await answering
    assert "make it again" in str(caught.value)
    assert read.reads == DIFF_LOADS


# The wiring, through the mount a deployment gets


@pytest.mark.usefixtures("keys")
def test_a_running_server_hands_its_own_comparison_to_the_api(
    directory: Path,
) -> None:
    """What the API answers is this server's own two sides: the
    configuration it booted, and the database as it is now. Nothing of
    either is knowledge the API application has."""
    stored(directory)
    booted = load_boot_config()

    with entered_client(booted.config, booted.secrets) as served:
        # Booted from exactly what is stored, so there is nothing
        # pending and every kind still says where it converges.
        settled = served.get(DIFF_PATH, headers=headers()).json()
        assert settled["providers"] == {
            "applies": "restart",
            "added": [],
            "removed": [],
            "changed": [],
        }
        assert settled["devices"] == {"applies": "check-in"}

        written = served.put(
            f"{MOUNT_PATH}/providers/tts/spare",
            json={"type": "mock"},
            headers=headers(),
        )
        assert written.status_code == 200, written.text

        pending = served.get(DIFF_PATH, headers=headers()).json()

    assert pending["providers"]["added"] == ["tts.spare"]
    assert pending["providers"]["applies"] == "restart"
    # And the write's own acknowledgement said the same thing in
    # sentence form, which is the pair an operator sees.
    assert "next server start" in written.json()["notice"]
