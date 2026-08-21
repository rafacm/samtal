"""Building MCP entries, and standing a real server up behind them.

The MCP suites never fake the protocol: the stdio ones spawn
`mcp_stdio_server.py` as a subprocess and the HTTP one hosts a `FastMCP`
app on a port the OS picks, so what is under test is the transport that
ships. What belongs here is everything around that: the entry a
configuration names a server with, the configurations whose agents are
granted it, the manager and registry builders that start one, the
re-read a reload is handed, and the context manager that serves an app
for the length of a block.

Nothing here asserts. A helper here hands back a started thing or a
`Config`, and the suite says what it expects of it.

`config_granting` and `reload_config` are both "a configuration whose
agents reach these entries", and they are kept apart because they are
not the same configuration: the second also carries `local_only`, which
is the switch its own suite is about.

The file is called `tools_mcp.py` rather than `mcp.py` after the
production module it serves, because `mcp_stdio_server.py` beside it is
run as a script: that puts this directory first on the subprocess's
path, and a module named `mcp.py` here would be what its
`from mcp.server.fastmcp import FastMCP` finds.
"""

import asyncio
import gc
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from mcp.server.fastmcp import FastMCP
from sse_starlette.sse import AppStatus

from tests.support.configs import STDIO_SERVER, world
from tests.support.providers import built_world
from vinga_server.config import Config, McpServerConfig
from vinga_server.config.boot import BootConfig
from vinga_server.config.reload import ConfigReload
from vinga_server.config.responses import ConfigReloadResult
from vinga_server.config.secrets import SecretStore
from vinga_server.tools.mcp import McpServerManager, McpServers

# --- what a suite reads its own subject by ----------------------------


# Where the test server lists `inside__secret_word`, which is the tool
# an entry called `home` publishes into `home__inside`'s namespace. The
# seventh it registers, and the sixth never publishes (too long once a
# prefix is on it), so the two ways of counting disagree here, which is
# the whole reason this number is spelled out rather than assumed.
SHADOWED_POSITION = 7

# What this module logs under, which is what an operator reads.
MANAGER_LOGGER = "vinga_server.tools.mcp"


# --- the entry a configuration names a server with --------------------


def stdio_entry(**overrides: object) -> McpServerConfig:
    return McpServerConfig.model_validate(
        {"transport": "stdio", "command": sys.executable, "args": [str(STDIO_SERVER)]}
        | overrides
    )


def entry_data(**overrides: object) -> dict[str, object]:
    return {
        "transport": "stdio",
        "command": sys.executable,
        "args": [str(STDIO_SERVER)],
    } | overrides


def command_arrives(command: Path) -> None:
    """Put this suite's stdio server behind a path that had nothing at
    it, which is what a box that was rebooting coming back looks like
    from here.

    For the tests about a server that was down and is not any more. The
    entry is written once and never touched again: what moves is the
    world it names, which is the difference the manager is actually
    about, since a configuration that changed under a running manager is
    a reload and reaches it another way entirely.

    A shell script rather than a symlink to this interpreter. A
    symlinked interpreter resolves its prefix to the base installation
    rather than to the environment the tests run in, and would find none
    of the packages the server imports.
    """
    command.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    command.chmod(0o755)


# --- the configurations that grant it ---------------------------------


def config_granting(servers: dict[str, object], grants: dict[str, list[str]]) -> Config:
    """A configuration whose agents reach the entries named, which is
    what the grants half of the status view is read from."""
    return Config(
        server={},
        providers={
            "llm": {"mock": {"type": "mock"}},
            "asr": {"mock": {"type": "mock"}},
            "tts": {"mock": {"type": "mock"}},
            "vad": {"mock": {"type": "mock"}},
        },
        mcp_servers=servers,
        agent_defaults=dict.fromkeys(("llm", "asr", "tts", "vad"), "mock"),
        agents={name: {"prompt": "A", "mcp": entries} for name, entries in grants.items()},
        default_agent=next(iter(grants)),
    )


def reload_config(
    servers: dict[str, object],
    grants: dict[str, list],
    local_only: bool = False,
) -> Config:
    """One agent per grant list, so a test can move an entry between
    agents as well as in and out of the configuration."""
    return Config(
        server={"local_only": local_only},
        providers={
            stage: {"mock": {"type": "mock"}} for stage in ("llm", "asr", "tts", "vad")
        },
        mcp_servers=servers,
        agent_defaults=dict.fromkeys(("llm", "asr", "tts", "vad"), "mock"),
        agents={name: {"prompt": "A", "mcp": mcp} for name, mcp in grants.items()},
        default_agent=next(iter(grants)),
    )


# --- starting the real thing ------------------------------------------


async def running(config: McpServerConfig, name: str = "tools") -> McpServerManager:
    manager = McpServerManager(name, config)
    await manager.start()
    return manager


async def started(config: Config, secrets: SecretStore | None = None) -> McpServers:
    servers = McpServers.build(config, secrets)
    await servers.start_all()
    return servers


def reading(config: Config, secrets: SecretStore | None = None):
    """The re-read a reload is handed, standing in for the database.

    A `BootConfig` because that is what `reload_domain_config` answers
    with and what the apply is typed against: the composed models and
    the stored secrets beside them, which is one value rather than two
    that could be passed in the wrong order.
    """
    loaded = BootConfig(config, secrets if secrets is not None else SecretStore())
    return lambda: loaded


class Applying:
    """The generalized apply of one running registry, with the stored
    half a test moves between calls.

    The production shape holds its re-read for the life of the server,
    since where a server's database is does not change per request. A
    test's does: what these suites vary is exactly what the store would
    say next. So the read is a field here and `apply` takes the next one,
    while the object underneath is the same one across calls, which is
    what the exclusion tests need in order to have an exclusion to test.

    The running world is the configuration the registry was built from,
    which is what makes the overlay the identity function for these
    suites: what they are about is the MCP half, and an apply that
    changed a prompt as well would be two subjects in one assertion.

    The engines are the ones that configuration names, built here for
    the same reason: an apply synthesizes the filled pauses of the world
    it installs, and one handed no engines would be exercising a server
    that cannot speak.
    """

    def __init__(
        self, servers: McpServers, running: Config, secrets: SecretStore | None = None
    ) -> None:
        self.stored = reading(running, secrets)
        self.generations = world(running, secrets, providers=built_world(running))
        self._applying = ConfigReload(
            self.generations,
            servers,
            lambda: self.stored(),
        )

    @property
    def running(self) -> bool:
        """Whether an apply is between its two phases right now."""
        return self._applying.running

    async def apply(self, read=None) -> ConfigReloadResult:
        """One apply, against `read` as the stored half from now on."""
        if read is not None:
            self.stored = read
        return await self._applying.apply()


# --- one HTTP server, for the length of a block -----------------------


# How long a server may take to come up or go down before the test gives
# up on it. Generous: it is a deadlock guard, not a measurement.
LIFECYCLE_TIMEOUT_S = 20.0


@asynccontextmanager
async def serving(server: FastMCP) -> AsyncIterator[str]:
    """One `FastMCP` instance served over HTTP for the length of a
    block, as its URL.

    Separate from the fixture above so a test that needs a server
    publishing something else of its own gets the awkward parts of this
    (the startup race, the port, and the process-wide SSE flag below)
    rather than a second copy of them.
    """
    config = uvicorn.Config(
        server.streamable_http_app(),
        host="127.0.0.1",
        # The OS picks a free port, so there is no probe-then-bind race
        # with anything else on the machine.
        port=0,
        log_level="warning",
    )
    uvicorn_server = uvicorn.Server(config)
    serving = asyncio.create_task(uvicorn_server.serve())
    async with asyncio.timeout(LIFECYCLE_TIMEOUT_S):
        while not uvicorn_server.started:
            if serving.done():
                # Surfaces a startup failure as itself rather than as a
                # timeout twenty seconds later.
                serving.result()
                raise RuntimeError("the test server stopped before it started")
            await asyncio.sleep(0.01)
    port = uvicorn_server.servers[0].sockets[0].getsockname()[1]

    # sse_starlette keeps one process-wide "the server is shutting down"
    # flag. It monkeypatches `uvicorn.Server.handle_exit` to set it, and
    # nothing ever clears it, so `tests/unit/test_drain.py` calling
    # `handle_exit` on its own server (which chains to the patched base)
    # leaves it set for the rest of the session. While it is set, every
    # SSE response in the process returns without sending anything, and
    # an SSE response is how this server answers a POST, so it would
    # accept connections and complete no request. Cleared for the length
    # of the fixture and put back, so this module neither depends on the
    # order the suite runs in nor changes it for anybody else.
    shutting_down = AppStatus.should_exit
    AppStatus.should_exit = False
    try:
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        AppStatus.should_exit = shutting_down
        uvicorn_server.should_exit = True
        async with asyncio.timeout(LIFECYCLE_TIMEOUT_S):
            await serving
        # Still inside this test's warning filters, which is the point:
        # what the server left behind is finalized here rather than
        # halfway through whatever runs next.
        gc.collect()
