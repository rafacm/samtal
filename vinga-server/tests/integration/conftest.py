"""Shared machinery for the integration lane.

Every test here runs a real server on an ephemeral port and talks to it
with xiaozhi-sdk as the device, on mock providers, so the lane needs no
keys, no models, and no network. The pieces two or more test modules
need live here as fixtures rather than being imported across modules.

A test writes the configuration it is about as a `Config`, and the
server it gets is booted the way a deployment boots: the domain half is
written into a scratch database through the repository, read back, and
composed onto the file half again. So every scenario in this lane also
covers the round trip through the database, and a test says what it is
about rather than how the configuration is stored.
"""

import asyncio
import contextlib
import math
import os
import struct
import threading
import time
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import uvicorn
from xiaozhi_sdk import XiaoZhiWebsocket

from tests.conftest import provision_stores
from vinga_server.app import create_app
from vinga_server.config import Config, FileConfig, compose_config
from vinga_server.config.models import (
    API_MOUNT_PATH,
    PROVIDER_STAGES,
    AgentConfig,
    AgentDefaults,
    DatabaseConfig,
    McpServerConfig,
    PromptFragmentConfig,
    ProviderConfig,
    domain_fields,
)
from vinga_server.config.store import ConfigStore, Snapshot
from vinga_server.db import open_database

# Every server in this lane boots the way a deployment boots, which means
# every one of them opens a store. Said here, at this conftest's import,
# because the root conftest provisions for the lanes that ask and not for
# whoever imports it: `tests/smoke` drives a container over HTTP and
# needs no instance of its own.
provision_stores()

SAMPLE_RATE = 16000
FRAME_MS = 60
FRAME_BYTES = SAMPLE_RATE * FRAME_MS // 1000 * 2


def booted(config: Config):
    """The app the server would serve, from the same configuration after
    a round trip through the database this run provisioned.

    The fragments are the entities the test wrote, dumped as the fields
    it set, which is exactly what a fragment is. The order is the one
    the write-time reference checks require.

    Seeding and serving are the same database: what the app reads
    afterwards is what a write through its own API wrote, and a second
    server started on it reads what the first one left. The lane's
    conftest points `DatabaseConfig`'s defaults at a database of this
    worker's own, cleared between tests, so a test that names nothing
    still gets isolation.

    `from_store=True` because that is what this stands for: the domain
    half really was read out of the database a line above, so the
    server's device bindings resolve live and the surfaces that span a
    store and a running world have something to span.
    """
    database = config.server.database
    engine = open_database(database)
    try:
        snapshot = _seeded(ConfigStore(engine), config)
    finally:
        engine.dispose()
    composed = compose_config(
        FileConfig(server=config.server),
        domain_fields(snapshot.domain),
        "the domain schema of the vinga database",
    )
    return create_app(composed, snapshot.secrets, from_store=True)


def _seeded(store: ConfigStore, config: Config) -> Snapshot:
    for stage in PROVIDER_STAGES:
        for name, entry in getattr(config.providers, stage).items():
            store.set_provider(stage, name, _fragment(entry))
    for name, server in config.mcp_servers.items():
        store.set_mcp_server(name, _fragment(server))
    for name, block in config.prompt_fragments.items():
        store.set_prompt_fragment(name, _fragment(block))
    store.set_agent_defaults(_fragment(config.agent_defaults))
    for name, agent in config.agents.items():
        store.set_agent(name, _fragment(agent))
    for mac, bound in config.devices.items():
        store.bind_device(mac, bound)
    if config.default_agent is not None:
        store.set_default_agent(config.default_agent)
    return store.load()


def _fragment(
    entry: ProviderConfig
    | McpServerConfig
    | PromptFragmentConfig
    | AgentConfig
    | AgentDefaults,
) -> dict[str, Any]:
    """One entity as the document that writes it: the fields it set and
    nothing else. Never a full dump, which would name the fields the
    entity deliberately left unset and fail its own validator (an MCP
    server reads `model_fields_set` to tell "my headers are ignored"
    from "my headers are wrong")."""
    return entry.model_dump(exclude_unset=True)


@contextlib.asynccontextmanager
async def running_app(config: Config):
    """A live server on an ephemeral port, yielding its port and the app
    it serves, torn down on the way out. The app is what a test needs
    when it has to reach server-side state (the session registry) that a
    device could not."""
    async with _serving(booted(config)) as served:
        yield served


@contextlib.asynccontextmanager
async def restarted_app(config: Config):
    """A second server start on a database somebody has already written.

    Nothing is seeded: the domain half is read as it stands, which is
    what a restart is, and the difference from `running_app` is the
    whole point of the tests that use it. `config` is only there for its
    file half, the port this process runs with.

    There is no directory argument any more, and the reason is the whole
    of the reshape (#283): a test's database is the one the lane
    provisioned for its worker, cleared between tests, so "the same
    database the last server used" is what a restart gets by default.
    """
    engine = open_database(config.server.database)
    try:
        snapshot = ConfigStore(engine).load()
    finally:
        engine.dispose()
    composed = compose_config(
        FileConfig(server=config.server, memory=config.memory),
        domain_fields(snapshot.domain),
        "the domain schema of the vinga database",
    )
    async with _serving(create_app(composed, snapshot.secrets, from_store=True)) as served:
        yield served


@contextlib.asynccontextmanager
async def _serving(app):
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    )
    task = asyncio.create_task(server.serve())
    while not server.started:
        if task.done():
            task.result()
        await asyncio.sleep(0.01)
    try:
        yield server.servers[0].sockets[0].getsockname()[1], app
    finally:
        server.should_exit = True
        await task


@contextlib.asynccontextmanager
async def running(config: Config):
    """A live server on an ephemeral port, yielding just the port."""
    async with running_app(config) as (port, _):
        yield port


def speech_pcm(duration_ms: int) -> bytes:
    samples = SAMPLE_RATE * duration_ms // 1000
    return b"".join(
        struct.pack("<h", int(8000 * math.sin(2 * math.pi * 300 * n / SAMPLE_RATE)))
        for n in range(samples)
    )


def dominant_hz(audio: np.ndarray) -> float:
    """The strongest frequency in the received reply. The sdk hands its
    decoded audio back at the rate it was constructed with, whatever the
    server hello announced, so the analysis rate is that one."""
    spectrum = np.abs(np.fft.rfft(audio.astype(np.float64)))
    return float(np.fft.rfftfreq(audio.size, 1 / SAMPLE_RATE)[int(np.argmax(spectrum))])


async def converse(
    port: int,
    mac: str,
    device_tools: Sequence[dict[str, Any]] | None = None,
    ota_path: str = "/xiaozhi/ota/",
) -> tuple[list[dict], np.ndarray]:
    """One device's whole conversation: OTA discovery, hello, an
    utterance, and the spoken reply collected until `tts stop`.

    `device_tools` are registered before connecting, so they are what
    the server's tools/list finds; each entry is an xiaozhi-sdk tool
    (name, description, inputSchema, tool_func, is_async).

    `ota_path` is where the device was told to look, which for a board
    onboarded by its short URL is `/x/<key>/` rather than the legacy
    path: the same endpoint, and the whole conversation has to come out
    of either."""
    events: list[dict] = []
    reply_finished = asyncio.Event()

    async def on_message(data: dict) -> None:
        events.append(data)
        if data.get("type") == "tts" and data.get("state") == "stop":
            reply_finished.set()

    client = XiaoZhiWebsocket(
        on_message,
        ota_url=f"http://127.0.0.1:{port}{ota_path}",
        audio_sample_rate=SAMPLE_RATE,
    )
    try:
        if device_tools:
            await client.set_mcp_tool(list(device_tools))
        assert await client.init_connection(mac)
        pcm = speech_pcm(960)
        for start in range(0, len(pcm), FRAME_BYTES):
            assert await client.send_audio(pcm[start : start + FRAME_BYTES])
        await client.send_silence_audio(1.2)
        await asyncio.wait_for(reply_finished.wait(), timeout=30)
        await asyncio.sleep(0.3)
        chunks = list(client.output_audio_queue)
    finally:
        await client.close()
    return events, np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.int16)


def spoken(events: list[dict]) -> str:
    return " ".join(
        event["text"]
        for event in events
        if event.get("type") == "tts" and event["state"] == "sentence_start"
    )


BYTECODE_OFF = "PYTHONDONTWRITEBYTECODE"

PACKAGE = Path(__file__).resolve().parents[2] / "src" / "vinga_server"


def script_environment(without: Sequence[str] = (), **overrides: str) -> dict[str, str]:
    """The environment a deployment script gets when this lane runs it.

    This process's own, minus the variables the caller names, plus the
    ones it sets, and always with bytecode writing off.

    That last part is the harness's job and not the scripts'. They are
    deployment artifacts, run here verbatim, and each of them starts
    `vinga-server` and its CLI as processes of its own. Those children
    inherit this environment, and without the flag they leave a
    `__pycache__` beside every module they import. `tests/conftest.py`
    stops this process writing bytecode and clears the caches it finds
    once, before the first import, so a cache a child writes during the
    run is one nothing clears: it outlives the run and goes stale on the
    next edit, which is the trap that file exists to close.
    """
    named = set(without)
    environment = {key: value for key, value in os.environ.items() if key not in named}
    environment.update(overrides)
    # Last, so no caller can hand a script an environment that writes
    # bytecode by naming the variable itself.
    environment[BYTECODE_OFF] = "1"
    return environment


@pytest.fixture(scope="session", autouse=True)
def no_bytecode_left_behind() -> Iterator[None]:
    """That the lane wrote no bytecode cache into the package.

    The check the safeguard needs and could not have: a subprocess
    started without `PYTHONDONTWRITEBYTECODE` writes caches that the
    clearing in `tests/conftest.py` has already run past, so the lane
    that defeats the safeguard is also the lane least able to notice.
    Reading the tree once the lane is over is what notices, and it
    notices whichever test spawned the subprocess.

    Session-scoped and autouse, so it is the last thing the lane does,
    and it names the directories rather than merely counting them: the
    path is what says which subprocess wrote it.
    """
    yield
    # A wrong PACKAGE path would make rglob yield nothing and the guard
    # pass vacuously, which is precisely the failure it exists to catch.
    assert PACKAGE.is_dir(), f"the package tree moved out from under this guard: {PACKAGE}"
    left = sorted(str(cache) for cache in PACKAGE.rglob("__pycache__"))
    assert not left, (
        "the lane left bytecode caches under vinga_server, which nothing "
        "clears and which go stale on the next edit:\n"
        + "\n".join(left)
        + "\nEvery subprocess a test starts needs PYTHONDONTWRITEBYTECODE=1 "
        "in its environment; build it with script_environment()."
    )


@contextlib.contextmanager
def _served_api(database: DatabaseConfig | None = None):
    """A real server on an ephemeral loopback port, serving an empty
    domain half, yielding the base URL of its configuration API.

    An empty domain is a valid boot (the completeness check only fires
    when agents exist), which is exactly what makes the API-era first
    start work: start with nothing, configure over the API, restart. The
    scripts this backs are the documented procedure, so what they get is
    the mounted namespace on a real port rather than an application
    object.

    Run in a thread rather than on the test's own loop, because what
    talks to it is a subprocess: uvicorn skips its signal handlers off
    the main thread, which is the one thing that would otherwise need
    care here.
    """
    # A deployment's database is migrated before its app is built:
    # `main()` composes through `load_boot_config` and the ASGI entry
    # point through `create_app` with no configuration, and both open
    # and migrate it on the way. This fixture hands a configuration in,
    # so it migrates the database itself and says `from_store=True`
    # rather than composing the other shape, where the world a server
    # serves is one no store describes and the surfaces that span both
    # sides refuse.
    database = DatabaseConfig() if database is None else database
    open_database(database).dispose()
    # The port lives on the socket rather than in the configuration: the
    # models refuse 0, which is right for a deployment and is not what
    # binding an ephemeral port means.
    config = Config(server={"database": database.model_dump()})
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(config, from_store=True),
            host="127.0.0.1",
            port=0,
            log_level="warning",
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 30
        while not server.started:
            assert thread.is_alive() and time.monotonic() < deadline, "the server never started"
            time.sleep(0.02)
        port = server.servers[0].sockets[0].getsockname()[1]
        yield f"http://127.0.0.1:{port}{API_MOUNT_PATH}"
    finally:
        server.should_exit = True
        thread.join(timeout=30)


@pytest.fixture
def served_api():
    """`with served_api() as url: ...`, for the scripts that document
    themselves as running against a running server. A second, isolated
    one takes a `DatabaseConfig` naming the `spare_database` fixture."""
    return _served_api


@pytest.fixture
def serve():
    """The server runner: `async with serve(config) as port: ...`."""
    return running


@pytest.fixture
def serve_app():
    """The same, plus the app: `async with serve_app(c) as (port, app):`."""
    return running_app


@pytest.fixture
def restart():
    """The same database served again with nothing re-seeded, which is
    what a restart reads: `async with restart(config) as (port, app):`."""
    return restarted_app


@pytest.fixture
def simulate():
    """The device simulator: `await simulate(port, mac)`."""
    return converse
