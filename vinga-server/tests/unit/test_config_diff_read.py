"""The diff read as the composition root wires it: one world, or a
refusal.

The transport around the route is `test_config_api_runtime.py`'s and
what the comparison decides is `test_config_diff.py`'s and
`test_mcp_pending.py`'s. What is left here is what only the composition
root does, and all three parts of it are things a stub cannot show.

The stored half is the re-read the reload begins with, so a stored world
that fails it is refused here under the status it would be refused under
there, in a sentence of this route's own rather than the store's, which
takes a real database and a real key to demonstrate. The
running half is read either side of that database read, so an answer is
one world or it is no answer at all, which takes a reload landing in the
middle of one. And nothing of a credential travels, which takes a
credential: a plaintext, the ciphertext the database holds and the mark
taken over it, for the value this server booted with and again for the
value stored while it runs, plus the name of an environment variable,
are seven distinct sentinels here, each asserted absent from the answer,
from a refusal, and from what the server wrote about either.
"""

import asyncio
import json
import threading
from collections.abc import Callable
from pathlib import Path

import pytest
from sqlalchemy import select, update

from tests.support.apps import entered_client
from tests.support.configs import config_with, world
from tests.support.problems import refused as refusal_body
from tests.support.tools_mcp import Applying, entry_data, reading
from vinga_server.app import DIFF_LOADS, config_diff_reader
from vinga_server.config import Config
from vinga_server.config.api import MOUNT_PATH
from vinga_server.config.boot import BootConfig, load_boot_config
from vinga_server.config.loader import (
    ConfigError,
    DatabaseBusyError,
    RunningConfigMovedError,
    StorageError,
)
from vinga_server.config.models import PROGRAM, DatabaseConfig
from vinga_server.config.secrets import (
    MASTER_KEY_ENV,
    SecretLocation,
    SecretStore,
    generate_key,
    load_keys,
)
from vinga_server.config.store import ConfigStore
from vinga_server.db import open_database, schema
from vinga_server.logs import JsonFormatter
from vinga_server.tools.mcp import McpServers

TOKEN = "test-api-token-" + "0123456789abcdef" * 2

API_SECRET_ENV = "VINGA_API_SECRET"

DIFF_PATH = f"{MOUNT_PATH}/runtime/config/diff"

STAGES = ("llm", "asr", "tts", "vad")

# The forms a stored credential takes, each planted where an answer that
# carried it would have to put it, and each shaped so that a substring
# check for it cannot match by accident.
#
# The plaintext is what an operator typed. The envelope is what the
# database holds, which a read that serialized a row rather than a name
# would carry. The mark is what the comparison itself asks about, which
# is the one an implementation could plausibly put in an answer by
# accident, since it is opaque and looks harmless. Each of those three
# exists twice over, once per side of the comparison, and the case below
# takes both. And the environment variable's name is the last: it is not
# a credential, but it says where one is kept, and it is written in the
# entity body this read must never echo.
PLAINTEXT = "sk-diff-1f2e3d4c-never-a-real-credential"

# What the same slot holds after a rotation, which is what the stored
# side of the comparison is reading while the running side still holds
# the one above.
ROTATED = "sk-diff-8a6b5c4d-also-never-a-real-credential"

ENV_NAME = "VINGA_DIFF_SENTINEL_ENV_5b7c9d"


def headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The database directory a deployment names through its
    environment, which is what makes `load_boot_config` read this test's
    database rather than a real one's."""
    monkeypatch.delenv("VINGA_CONFIG", raising=False)
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
    engine = open_database(DatabaseConfig())
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
    engine = open_database(DatabaseConfig())
    try:
        with engine.connect() as connection:
            secrets = connection.execute(
                select(schema.providers.c.secrets).where(
                    schema.providers.c.stage == "llm",
                    schema.providers.c.name == "mock",
                )
            ).scalar_one()
    finally:
        engine.dispose()
    # Read as a column rather than through the store, so what comes
    # back is the text the file holds rather than a value some accessor
    # decoded.
    # A value rather than the text it was dumped to: psycopg reads a
    # `json` column into Python objects, where the SQLite driver handed
    # back the string.
    return str(secrets["api_key"]["enc"])


def mark_of(directory: Path) -> str:
    """The mark the comparison takes over what the database holds for
    the planted slot right now, which is the stored side's half of the
    question `same_provider` asks."""
    engine = open_database(DatabaseConfig())
    try:
        snapshot = ConfigStore(engine, load_keys()).load()
    finally:
        engine.dispose()
    return snapshot.secrets.fingerprint("provider", "llm.mock")


def written(caplog: pytest.LogCaptureFixture) -> str:
    """What the server kept about a request, in both shipped formats."""
    return caplog.text + "".join(
        JsonFormatter().format(record) for record in caplog.records
    )


# The stored half, which is the reload's own re-read


@pytest.mark.usefixtures("keys")
def test_a_stored_secret_that_will_not_open_refuses_under_the_reload_s_status(
    directory: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stored side runs `reload_domain_config`, which verifies that
    every stored credential opens before it composes anything, so a
    deployment whose key has been rotated away from its secrets is
    refused here under the status a reload would refuse under.

    The sentence is this route's own and not the store's. What the store
    would have said names the slot, which would be within this API's
    ordinary contract and outside this read's, whose whole answer is
    names and labels; the fixed sentence says where the location can be
    had instead.
    """
    stored(directory, secret=PLAINTEXT)
    booted = load_boot_config()

    with entered_client(booted.config, booted.secrets, from_store=True) as served:
        # The key the secret was written under is gone from the
        # environment, which is what a mistaken rotation looks like from
        # the inside of a running server.
        monkeypatch.setenv(MASTER_KEY_ENV, generate_key())
        refused = served.get(DIFF_PATH, headers=headers())

    assert refused.status_code == 422
    # The sentence is fixed and says so: where exactly the stored half
    # was refused is the one thing this read never carries, because a
    # sentence composed over stored state can quote what was written
    # into the wrong column.
    assert "deliberately not said here" in refusal_body(refused.json(), 422)
    assert "api_key" not in refused.text


@pytest.mark.usefixtures("keys")
def test_a_stored_domain_that_will_not_compose_refuses_the_same_way(
    directory: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Model-valid rows that are not a valid deployment: an agent naming
    a provider nothing declares. No write this server offers can produce
    it, which is why it is planted as a row, and it is exactly what an
    interrupted migration or another build's write can leave behind. The
    whole-snapshot validation is part of the re-read, so this is a
    refusal rather than an answer computed over half a world.

    And the value the stored row held is the point of the fixed
    sentence: what a refused row holds is whatever was written into it,
    which is as likely to be a credential pasted into the wrong column
    as a name somebody mistyped. The store names it, this read does
    not.

    "The same way" is asserted rather than asserted about: both causes
    are driven here, one after the other over one store, and the two
    answers are held equal. A sentence copied into this file could not
    make that claim, since a copy agrees with itself whatever the two
    routes do."""
    stored(directory, secret=PLAINTEXT)
    booted = load_boot_config()
    _plant_unknown_provider()

    with entered_client(booted.config, booted.secrets, from_store=True) as served:
        uncomposable = served.get(DIFF_PATH, headers=headers())

    # The row put back, so the only thing wrong with the store is the
    # credential that will no longer open.
    _drop_planted_provider()

    with entered_client(booted.config, booted.secrets, from_store=True) as served:
        monkeypatch.setenv(MASTER_KEY_ENV, generate_key())
        unopenable = served.get(DIFF_PATH, headers=headers())

    assert uncomposable.status_code == 422
    assert unopenable.status_code == 422
    assert refusal_body(uncomposable.json(), 422) == refusal_body(unopenable.json(), 422)
    assert "ghost" not in uncomposable.text
    assert "api_key" not in unopenable.text


def _plant_unknown_provider() -> None:
    """An agent naming a provider nothing declares, written as a row
    because no write this server offers can produce one. Into the body
    rather than into a column of its own, which is where every non-key
    field lives since #243.

    Read, edited in Python, written back, which is what replaced the
    SQLite `json_set` this used to call (#283): the body is a text
    column holding a dumped model whichever backend it sits in, and
    editing it here leaves the rest of the entry exactly as it was
    written without either backend's JSON functions in the way.
    """
    _rewrite_agent_body(lambda body: {**body, "llm": "ghost"})


def _drop_planted_provider() -> None:
    """The same row without the planted key, which is the entry
    `stored` wrote: the agent names no llm of its own and inherits the
    one `agent_defaults` names."""
    _rewrite_agent_body(lambda body: {k: v for k, v in body.items() if k != "llm"})


def _rewrite_agent_body(edit) -> None:
    engine = open_database(DatabaseConfig())
    try:
        with engine.begin() as connection:
            for name, body in connection.execute(
                select(schema.agents.c.name, schema.agents.c.body)
            ).all():
                connection.execute(
                    update(schema.agents)
                    .where(schema.agents.c.name == name)
                    .values(body=json.dumps(edit(json.loads(body))))
                )
    finally:
        engine.dispose()


# What a refused stored half says, and what it stops carrying


REJECTED = "sk-stored-in-the-wrong-column-3a7f"

# A world with no MCP entries at all: what these cases are about is the
# refusal, and the registry is here only because the closure holds one.
NO_ENTRIES = config_with()


def failing(exc: Exception) -> Callable[[], BootConfig]:
    def read() -> BootConfig:
        raise exc

    return read


@pytest.mark.parametrize(
    ("raised", "answered"),
    [
        (ConfigError, ConfigError),
        (StorageError, StorageError),
    ],
)
async def test_a_refused_stored_half_keeps_its_type_and_loses_its_words(
    raised: type[Exception], answered: type[Exception]
) -> None:
    """The type is what the API turns into a status, so it survives; the
    sentence is composed over stored state, so it does not.

    The chain is the other half of the same rule. A replacement raised
    inside the handler would carry the original as its `__context__`,
    and anything walking an exception (a logger, a traceback renderer, a
    debugger attached to a running deployment) would find the words
    again with the sanitizing bypassed.
    """
    diff = config_diff_reader(
        world(NO_ENTRIES),
        McpServers.build(NO_ENTRIES),
        failing(raised("agents.assistant.llm: names no llm provider that exists")),
    )

    with pytest.raises(answered) as caught:
        await diff()

    assert type(caught.value) is answered
    # The sentence is this read's own rather than the one that was
    # raised, which is what "loses its words" means here.
    assert REJECTED not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


async def test_a_busy_database_stays_the_retryable_refusal() -> None:
    """The one stored-side refusal whose status is neither 422 nor 500:
    a sentence of its own so that "make the request again" is still said
    to whoever meets it, and its own type so that it still answers
    409."""
    diff = config_diff_reader(
        world(NO_ENTRIES),
        McpServers.build(NO_ENTRIES),
        failing(DatabaseBusyError(f"cannot migrate the database at /srv/{REJECTED}")),
    )

    with pytest.raises(DatabaseBusyError) as caught:
        await diff()

    # Retryable, and still said so after the replacement.
    assert "again" in str(caught.value)
    assert REJECTED not in str(caught.value)
    assert caught.value.__context__ is None


# What must not travel


@pytest.mark.usefixtures("keys")
def test_neither_an_answer_nor_a_refusal_carries_a_credential(
    directory: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Every form of both sides, over both paths, in the body and in the
    log.

    The successful answer is the path that reads the marks, decides an
    entity is changed by them, and has to say so with a name. The
    refusal is the path where the stored half could not be read at all,
    which is where an implementation reaching for detail would reach for
    the thing it failed on.

    Both sides, because a comparison reads two. The credential is
    rotated so that the entity really is reported as changed, which
    means the running side holds one value and the stored side holds
    another, and each has a plaintext, a ciphertext and a mark of its
    own. Sentinels taken only before the rotation would leave the values
    the route actually consulted on the stored side unchecked, which is
    the whole half that arrives from the database at request time.
    """
    stored(directory, secret=PLAINTEXT, llm={"type": "mock", "api_key_env": ENV_NAME})
    booted = load_boot_config()
    # What the running side is holding: the value this server booted
    # with, the ciphertext it was loaded from, and the mark taken over
    # it, which is one of the two things the comparison reads.
    booted_side = (
        PLAINTEXT,
        envelope_of(directory),
        booted.secrets.fingerprint("provider", "llm.mock"),
    )

    with caplog.at_level("INFO"):
        with entered_client(booted.config, booted.secrets, from_store=True) as served:
            rotated = served.put(
                f"{MOUNT_PATH}/providers/llm/mock/secrets/api_key",
                json={"secret": ROTATED},
                headers=headers(),
            )
            assert rotated.status_code == 200, rotated.text
            # And what the stored side is holding from here on, which is
            # the other thing the comparison reads.
            stored_side = (ROTATED, envelope_of(directory), mark_of(directory))
            sentinels = (*booted_side, *stored_side, ENV_NAME)
            # Seven distinct strings that are all really there, so an
            # absence asserted below is an absence rather than an empty
            # needle or a value that was never read.
            assert len(set(sentinels)) == 7
            assert all(sentinels)

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
    reloads = Applying(servers, BEFORE)
    diff = config_diff_reader(reloads.generations, servers, read)

    answering = asyncio.create_task(diff())
    await read.in_flight()
    installed = reloads.generations.mark
    await reloads.apply(reading(AFTER))
    assert reloads.generations.mark > installed
    read.let_through()
    # The second read runs in a world that is holding still.
    await read.in_flight()
    read.let_through()

    answer = await answering
    assert answer.mcp_servers.changed == ()
    assert answer.agents.grants.changed == ()
    assert read.reads == 2


async def test_a_read_that_lands_inside_an_apply_reads_again() -> None:
    """The barrier between an apply's two swaps, which is the position a
    counter cannot cover.

    An apply changes serving state twice: the generation first, the MCP
    world after it. Between them the world is neither the one before nor
    the one after, and a mark that only counted finished applies would
    read as steady over exactly that window. The holder reads as nothing
    at all instead, and the guard treats no answer the way it treats a
    different one.

    The window is entered through the holder the apply enters it
    through, rather than by holding a manager's stop open: what a reader
    meets is the holder's state, and driving it any other way would be
    driving something else to reach the same state.
    """
    servers, read = McpServers.build(BEFORE), _Gated(BEFORE)
    reloads = Applying(servers, BEFORE)
    diff = config_diff_reader(reloads.generations, servers, read)

    answering = asyncio.create_task(diff())
    await read.in_flight()
    with reloads.generations.applying() as install:
        install(reloads.generations.current())
        # The first attempt's second sample lands inside the window, so
        # its read is thrown away; the second attempt then starts inside
        # the window, so its first sample is unstable too and its read
        # is thrown away as well.
        read.let_through()
        await read.in_flight()
    read.let_through()
    # And the third runs in a world that is holding still.
    await read.in_flight()
    read.let_through()

    answer = await answering
    assert answer.mcp_servers.changed == ()
    assert read.reads == DIFF_LOADS


async def test_a_world_that_keeps_moving_refuses_rather_than_mix() -> None:
    """The bound, and what happens at the end of it. Every attempt is
    overtaken by a reload, so no attempt ever holds one world, and the
    answer is the retryable refusal rather than a comparison across two
    of them."""
    servers, read = McpServers.build(BEFORE), _Gated(AFTER)
    reloads = Applying(servers, BEFORE)
    diff = config_diff_reader(reloads.generations, servers, read)

    answering = asyncio.create_task(diff())
    for _ in range(DIFF_LOADS):
        await read.in_flight()
        await reloads.apply(reading(AFTER))
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

    with entered_client(booted.config, booted.secrets, from_store=True) as served:
        # Booted from exactly what is stored, so there is nothing
        # pending and every kind still says where it converges.
        settled = served.get(DIFF_PATH, headers=headers()).json()
        assert settled["providers"] == {
            "applies": "reload",
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
    assert pending["providers"]["applies"] == "reload"
    # And the write's own acknowledgement said the same thing in
    # sentence form, which is the pair an operator sees.
    assert f"{PROGRAM} apply" in written.json()["notice"]
