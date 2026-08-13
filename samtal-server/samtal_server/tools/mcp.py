"""MCP servers configured per agent, on the official Python SDK.

One manager per referenced `mcp_servers` entry, connected once at
startup and shared by every session whose agent names it. Configuration
mistakes fail the boot the way a bad provider does; liveness does not.
A server that is unreachable at startup logs a warning, contributes no
tools, and is reconnected in the background when a session that would
use it opens, so a home automation box rebooting does not require the
conversation server to reboot too.

Each manager's whole lifecycle lives in one task: the SDK's clients are
async context managers over anyio task groups, and entering them in one
task while exiting in another is what breaks their cancel scopes. The
task connects, publishes its tools, and then waits until asked to stop.
"""

import asyncio
import contextlib
import logging
from collections.abc import Iterable
from contextlib import AsyncExitStack
from typing import Any

import httpx
import mcp.types
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import get_default_environment, stdio_client
from mcp.client.streamable_http import streamable_http_client

from samtal_server.config import Config, McpServerConfig
from samtal_server.config.secrets import SecretStore, resolve_mcp_values
from samtal_server.providers import ToolDef
from samtal_server.tools import names
from samtal_server.tools.publish import PublishedTools, publish

logger = logging.getLogger(__name__)

# How long connecting and listing a server's tools may take. The boot
# waits for this once per server, concurrently.
CONNECT_TIMEOUT_S = 10.0


def _emit_nothing(_record: logging.LogRecord) -> bool:
    """A filter that passes no record. See where it is installed."""
    return False


# The SDK's HTTP client logs what the far end chose: the session id a
# server picked, the raw body of an initialization result that would not
# parse, and, through logger.exception, a traceback whose validation
# message quotes the bytes that failed. JSON log events are this
# server's observability surface and its transcript store
# (docs/adr/2026-08-04-json-logs-are-the-observability-surface.md), and
# nothing a third-party server writes was ever part of it, which is the
# reason uvicorn's access log stays off in main.py as well. So those
# records stop at their own logger, before any handler of ours is
# reached. What an operator reads about an MCP server is what this
# module writes: the entry name, the outcome, and tool names that have
# been through the publishing rule.
logging.getLogger("mcp.client.streamable_http").addFilter(_emit_nothing)


class McpConfigError(ValueError):
    """An MCP server that cannot be built as configured. Raised at boot,
    like a bad provider, because at call time it would fail every
    conversation that reaches it."""


class McpServerDown(RuntimeError):
    """A call to a server that is not currently connected."""


class McpServerManager:
    """One configured MCP server: its connection, its tools, and its
    reconnection."""

    def __init__(
        self, name: str, config: McpServerConfig, secrets: SecretStore | None = None
    ) -> None:
        self._name = name
        self._config = config
        self._secrets = secrets
        # Resolved once here and thrown away. Resolving at construction
        # is what makes an unset $VAR or a token that will not decrypt
        # fail the boot rather than the first conversation that needs
        # the server. Keeping the result is what must not happen: a
        # manager lives as long as the process, so a decrypted
        # credential kept on it is a plaintext secret held for the
        # lifetime of the server, in a long-lived object, for the sake
        # of a value that is needed for the length of one connection.
        # _connect resolves again, where the value goes straight into
        # the child process or the request headers.
        self._resolve("env")
        self._resolve("headers")
        self._session: ClientSession | None = None
        self._published = PublishedTools(tools=[], originals={})
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._settled = asyncio.Event()

    @property
    def name(self) -> str:
        return self._name

    @property
    def up(self) -> bool:
        return self._session is not None

    @property
    def tool_timeout_s(self) -> float:
        return self._config.tool_timeout_s

    def tools(self) -> list[ToolDef]:
        """This server's tools, under the names the model sees. Empty
        while the server is down, which is how an agent configured with
        an unreachable server still holds a conversation."""
        return list(self._published.tools)

    async def start(self) -> None:
        """Connect and list the tools, or log why not. Never raises: a
        dead server is not a boot failure."""
        self._begin()
        await self._settled.wait()

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
            self._task = None

    def ensure_reconnecting(self) -> None:
        """Start a background reconnect if this server is down and no
        attempt is already running. Called when a session opens, so a
        server that came back is picked up without a restart."""
        if self.up or (self._task is not None and not self._task.done()):
            return
        logger.info("mcp server %s: reconnecting in the background", self._name)
        self._begin()

    def _begin(self) -> None:
        self._settled = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name=f"mcp-{self._name}")

    async def _run(self) -> None:
        self._stop.clear()
        try:
            async with AsyncExitStack() as stack:
                async with asyncio.timeout(CONNECT_TIMEOUT_S):
                    session = await self._connect(stack)
                    listed = await session.list_tools()
                # A third-party server's names are no more trustworthy
                # than a device's: they publish through the same rule,
                # or one badly named tool fails every later request.
                self._published = publish(
                    (
                        (tool.name, tool.description or "", tool.inputSchema)
                        for tool in listed.tools
                    ),
                    prefix=self._name,
                    label=f"mcp server {self._name}",
                )
                self._session = session
                logger.info(
                    "mcp server %s connected with %d tool(s): %s",
                    self._name,
                    len(self._published.tools),
                    ", ".join(tool.name for tool in self._published.tools) or "none",
                )
                self._settled.set()
                await self._stop.wait()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "mcp server %s is unavailable, its tools are absent: %s",
                self._name,
                _reason(exc),
            )
        finally:
            self._session = None
            self._published = PublishedTools(tools=[], originals={})
            self._settled.set()

    def _resolve(self, group: str) -> dict[str, str]:
        """This server's env or headers, as the process or the request
        should see them. Called per connection, and the result is never
        stored: the values it holds are the only plaintext secrets the
        server ever materializes."""
        values = self._config.env if group == "env" else self._config.headers
        return resolve_mcp_values(self._name, group, values, self._secrets)

    async def _connect(self, stack: AsyncExitStack) -> ClientSession:
        if self._config.transport == "stdio":
            assert self._config.command is not None
            parameters = StdioServerParameters(
                command=self._config.command,
                args=list(self._config.args),
                # Merged rather than replaced: a spawned server still
                # needs a PATH and a HOME to find its own tools.
                env={**get_default_environment(), **self._resolve("env")},
            )
            read, write = await stack.enter_async_context(stdio_client(parameters))
        else:
            assert self._config.url is not None
            # The transport takes a caller-managed httpx client rather
            # than headers, a timeout and a redirect policy of its own,
            # so the HTTP policy is stated here. The values come from
            # the SDK's own create_mcp_http_client, which is what the
            # deprecated wrapper this replaced built its client with, so
            # this stays a client swap rather than a behavior change for
            # deployments already running them: redirects followed for a
            # proxy in front of the server, 30 s for everything but the
            # read, and 300 s for the read, which was the wrapper's
            # sse_read_timeout and is deliberately longer than
            # CONNECT_TIMEOUT_S because a streamable_http server may
            # hold a GET stream open with nothing to say on it. Entered
            # on the stack before the transport, so unwinding closes the
            # transport first and the client after it, in this one task.
            client = await stack.enter_async_context(
                httpx.AsyncClient(
                    headers=self._resolve("headers") or None,
                    follow_redirects=True,
                    timeout=httpx.Timeout(30.0, read=300.0),
                )
            )
            read, write, _ = await stack.enter_async_context(
                streamable_http_client(self._config.url, http_client=client)
            )
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        return session

    async def call(self, published: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        """Run one of this server's tools, named as the model was given
        it. The name goes back out as the server listed it, since what
        the model saw may have been sanitized. A transport failure marks
        the server down (so the next session revives it) and is raised
        for the session to turn into an error result."""
        session = self._session
        if session is None:
            raise McpServerDown(f'MCP server "{self._name}" is not connected')
        original = self._published.original_for(published)
        if original is None:
            raise KeyError(f'MCP server "{self._name}" has no tool called "{published}"')
        try:
            result = await session.call_tool(original, arguments)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._mark_down()
            raise
        return _result_text(result), bool(result.isError)

    def _mark_down(self) -> None:
        """Unwind the connection so the next session reconnects it."""
        logger.warning("mcp server %s: dropping the connection after a failed call", self._name)
        self._session = None
        self._published = PublishedTools(tools=[], originals={})
        self._stop.set()


def _reason(exc: BaseException) -> str:
    """Why a connection did not happen, in words this server owns.

    An exception's message is not one of them: a server that answers the
    handshake with nonsense puts its own bytes in the validation error
    that follows, and the log line an operator reads is no place for
    them. The type names say what kind of failure it was, which is what
    the line was ever used for, and a group is unwrapped because
    "ExceptionGroup" says nothing at all."""
    if isinstance(exc, BaseExceptionGroup):
        return ", ".join(sorted({_reason(sub) for sub in exc.exceptions})) or "ExceptionGroup"
    return type(exc).__name__


def _result_text(result: mcp.types.CallToolResult) -> str:
    """A tool result as speakable text. Content a voice assistant cannot
    use is named rather than dropped, so the model can say what it got
    instead of appearing to ignore it."""
    parts: list[str] = []
    for item in result.content:
        if isinstance(item, mcp.types.TextContent):
            parts.append(item.text)
        else:
            parts.append(f"[unsupported {item.type} content]")
    return "\n".join(parts)


def _check_egress(name: str, entry: McpServerConfig) -> None:
    """Enforce server.local_only for one referenced MCP server (#30).
    Tool arguments carry conversation-derived data, and no transport
    knows its own egress (a stdio command may proxy anywhere, a url may
    name localhost), so unlike providers there is nothing class-level to
    consult: every referenced entry needs the operator's declaration."""
    if entry.egress is False:
        return
    if entry.egress is None:
        raise McpConfigError(
            f"mcp_servers.{name}: server.local_only is on, and whether an MCP "
            f"server sends session data off this network cannot be known from "
            f'its transport; declare "egress: false" on this entry to assert '
            f"that whatever its command or URL reaches stays local"
        )
    raise McpConfigError(
        f"mcp_servers.{name}: server.local_only is on, but this entry declares "
        f"that it sends session data off this network"
    )


class McpServers:
    """Every MCP server some agent references, built at startup."""

    def __init__(self, managers: dict[str, McpServerManager]) -> None:
        self._managers = managers

    @classmethod
    def build(cls, config: Config, secrets: SecretStore | None = None) -> "McpServers":
        """Managers for the entries agents actually use, the way only
        referenced providers are built. Raises McpConfigError for an
        entry that cannot be built, or one that server.local_only
        forbids, which fails the boot.

        `secrets` is the store a snapshot was loaded with, or None for a
        deployment whose credentials are all environment references."""
        managers: dict[str, McpServerManager] = {}
        for name in sorted(config.referenced_mcp_servers()):
            entry = config.mcp_servers[name]
            if config.server.local_only:
                _check_egress(name, entry)
            try:
                managers[name] = McpServerManager(name, entry, secrets)
            except ValueError as exc:
                raise McpConfigError(f"mcp_servers.{name}: {exc}") from exc
        return cls(managers)

    def __len__(self) -> int:
        return len(self._managers)

    def __contains__(self, entry: object) -> bool:
        return entry in self._managers

    async def start_all(self) -> None:
        """Connect every server concurrently, so one slow box does not
        add its timeout to the boot of the next."""
        if self._managers:
            await asyncio.gather(*(manager.start() for manager in self._managers.values()))

    async def stop_all(self) -> None:
        """Close every connection, so stdio child processes do not
        outlive the server."""
        for manager in self._managers.values():
            with contextlib.suppress(Exception):
                await manager.stop()

    def tools_for(self, entries: Iterable[str]) -> list[ToolDef]:
        """The tools of these entries, skipping servers that are down."""
        return [
            tool
            for entry in entries
            if entry in self._managers
            for tool in self._managers[entry].tools()
        ]

    def revive(self, entries: Iterable[str]) -> None:
        """Kick off a background reconnect for any of these that is
        down. Called when a session opens."""
        for entry in entries:
            manager = self._managers.get(entry)
            if manager is not None:
                manager.ensure_reconnecting()

    def timeout_for(self, entry: str) -> float | None:
        manager = self._managers.get(entry)
        return None if manager is None else manager.tool_timeout_s

    async def call(self, published: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        """Run a tool under the qualified name the model was given. The
        entry prefix says which server owns it, and the server maps the
        rest back to whatever it actually listed."""
        split = names.split_qualified(published)
        manager = self._managers.get(split[0]) if split is not None else None
        if manager is None:
            raise McpServerDown(f'no MCP server owns a tool called "{published}"')
        return await manager.call(published, arguments)
