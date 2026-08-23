"""The MCP server manager, against a real streamable_http server.

The stdio suite spawns a subprocess because that transport is a child
process by nature. This one's nature is a TCP socket, so the server it
talks to is a FastMCP app hosted in-process by uvicorn on a port the OS
picks. Everything from the manager's `_connect` down to the wire is the
code that ships.

What is covered here is what the transport owns. Since `_connect` passes
its own `httpx.AsyncClient`, the transport does not manage it, so the
redirect policy, the timeouts and the client's closure are this module's
subjects too. Logic that does not depend on the transport (name
sanitization, call timeouts, registry routing, mark-down on a failed
call) stays covered once, over stdio, where it already lives.
"""

import json
import logging
import socket
import threading
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from mcp.server.fastmcp import FastMCP

from tests.support.events import fields_of
from tests.support.events import only as one_event
from tests.support.tools_mcp import LIFECYCLE_TIMEOUT_S, serving
from vinga_server import logs
from vinga_server.config import McpServerConfig
from vinga_server.tools.mcp import (
    CONNECT_TIMEOUT,
    INITIALIZE_FAILED,
    TRANSPORT_FAILED,
    McpServerDown,
    McpServerManager,
    _reason,
)
from vinga_server.tools.mcp import (
    manager as manager_module,
)

# The logger an operator watches for these servers, and the SDK one that
# talks to them, named rather than spelled out at each assertion.
MANAGER_LOGGER = "vinga_server.tools.mcp"
SDK_CLIENT_LOGGER = "mcp.client.streamable_http"

# The SDK's *server* transport hands the reading end of a per-request SSE
# memory stream to its response and never closes it
# (`_handle_post_request` and `_handle_get_request` in
# `mcp/server/streamable_http.py`), so anyio's finalizer warns about an
# unclosed stream and this lane turns warnings into errors. It is the
# scaffolding warning, not the subject: the client under test closes what
# it opens, which the closure tests below assert directly. Scoped to this
# module and to that one message, and the fixture collects the garbage
# before a finalizer can surface it inside somebody else's test.
pytestmark = pytest.mark.filterwarnings("ignore:Unclosed <MemoryObject:ResourceWarning")


def secret_word() -> str:
    """The secret word, which only this tool knows."""
    return "rhubarb"


def add(first: int, second: int) -> int:
    """Add two whole numbers."""
    return first + second


@pytest.fixture
async def server_url() -> AsyncIterator[str]:
    """A running streamable_http MCP server, as its URL.

    Fresh `FastMCP`, app and uvicorn server per test, which is forced
    rather than tidy: `streamable_http_app()` memoizes one session
    manager on the instance, and that manager may be entered exactly
    once, so a shared instance would break the second test to start it.
    """
    server = FastMCP("vinga-test-http-tools")
    server.add_tool(secret_word)
    server.add_tool(add)
    async with serving(server) as url:
        yield url


def http_entry(url: str, **overrides: object) -> McpServerConfig:
    return McpServerConfig.model_validate(
        {"transport": "streamable_http", "url": url} | overrides
    )


async def running(config: McpServerConfig, name: str = "tools") -> McpServerManager:
    manager = McpServerManager(name, config)
    await manager.start()
    return manager


@contextmanager
def unused_url() -> Iterator[str]:
    """A URL on a port nothing is listening on, for the length of a
    block.

    The port is HELD rather than probed and released. A bound socket
    that never listens refuses every connection exactly as an unbound
    port does, which is the property the tests here want, and holding it
    is what makes the property true for longer than an instant: with the
    suite distributed across worker processes, a released number is one
    another worker's `port=0` server can be handed, and a test asserting
    that nothing answers would then be talking to it.
    """
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        yield f"http://127.0.0.1:{held.getsockname()[1]}/mcp"


async def test_a_started_server_offers_its_tools_under_its_entry_name(
    server_url: str,
) -> None:
    manager = await running(http_entry(server_url))
    try:
        assert manager.up
        offered = {tool.name for tool in manager.tools()}
        assert offered == {"tools__secret_word", "tools__add"}
        (listed,) = [tool for tool in manager.tools() if tool.name == "tools__add"]
        assert "Add two whole numbers" in listed.description
        assert listed.input_schema["properties"].keys() == {"first", "second"}
    finally:
        await manager.stop()


async def test_a_tool_call_answers_with_its_text(server_url: str) -> None:
    manager = await running(http_entry(server_url))
    try:
        assert await manager.call("tools__secret_word", {}) == ("rhubarb", False)
        assert await manager.call("tools__add", {"first": 2, "second": 3}) == ("5", False)
    finally:
        await manager.stop()


async def test_a_url_nobody_answers_does_not_fail_the_start(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The stdio suite's dead-command case, over HTTP: liveness is
    # forgiven, so the manager logs, publishes nothing, and stays down.
    with unused_url() as url:
        with caplog.at_level(logging.WARNING, logger=MANAGER_LOGGER):
            manager = await running(http_entry(url))
        try:
            assert not manager.up
            assert manager.tools() == []
            with pytest.raises(McpServerDown):
                await manager.call("tools__secret_word", {})
        finally:
            await manager.stop()

    # The logging half of "logs and stays down", which the state
    # assertions above cannot see. Pinned to this server's own logger and
    # to the level an operator watches, and to the entry name being in
    # the line; the wording around it stays free to change.
    (announced,) = [
        record
        for record in caplog.records
        if record.name == MANAGER_LOGGER and record.levelno == logging.WARNING
    ]
    assert "tools" in announced.getMessage()
    assert announced.exc_info is None


async def test_a_redirecting_server_is_followed_to_its_tools(server_url: str) -> None:
    """`follow_redirects=True` is `_connect`'s to set now that it builds
    the client, and every existing deployment had it from the SDK's
    factory. A proxy in front of the server answers each request with a
    307 to the real one, and the manager still lists tools."""

    class Handler(BaseHTTPRequestHandler):
        def _redirect(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                # Read what was sent, so the connection is not torn down
                # under the client mid-body.
                self.rfile.read(length)
            self.send_response(307)
            self.send_header("Location", server_url)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802 - the stdlib's spelling
            self._redirect()

        def do_POST(self) -> None:  # noqa: N802
            self._redirect()

        def do_DELETE(self) -> None:  # noqa: N802
            self._redirect()

        def log_message(self, *_args: object) -> None:
            """Silence: the proxy is not the subject of the test."""

    proxy = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    thread.start()
    try:
        manager = await running(http_entry(f"http://127.0.0.1:{proxy.server_port}/mcp"))
        try:
            assert manager.up
            assert {tool.name for tool in manager.tools()} == {
                "tools__secret_word",
                "tools__add",
            }
        finally:
            await manager.stop()
    finally:
        proxy.shutdown()
        thread.join(timeout=LIFECYCLE_TIMEOUT_S)
        proxy.server_close()


class CapturedClients:
    """Every `httpx.AsyncClient` the manager built, and how."""

    def __init__(self) -> None:
        self.kwargs: list[dict[str, object]] = []
        self.clients: list[httpx.AsyncClient] = []


def capture_clients(monkeypatch: pytest.MonkeyPatch) -> CapturedClients:
    """Watch client construction without replacing it: the subclass
    records its arguments and itself, then builds the real thing, so the
    connection under test is a real connection."""
    captured = CapturedClients()
    real = httpx.AsyncClient

    class Capturing(real):  # type: ignore[valid-type, misc]
        def __init__(self, **kwargs: object) -> None:
            captured.kwargs.append(kwargs)
            captured.clients.append(self)
            super().__init__(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", Capturing)
    return captured


async def test_the_client_carries_the_wrappers_redirect_and_timeout_policy(
    server_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The deprecated wrapper built the client from the SDK's factory
    with these exact values. Passing our own client means httpx's
    defaults would apply instead if `_connect` forgot them, silently and
    only for deployments with a slow stream or a redirect."""
    captured = capture_clients(monkeypatch)
    manager = await running(http_entry(server_url))
    try:
        assert manager.up
    finally:
        await manager.stop()

    assert len(captured.kwargs) == 1
    (kwargs,) = captured.kwargs
    assert kwargs["follow_redirects"] is True
    assert kwargs["timeout"] == httpx.Timeout(30.0, read=300.0)


async def test_the_client_is_closed_when_the_connection_ends(
    server_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A caller-managed client is not the transport's to close, so a
    manager that leaked one would grow a connection pool per reconnect
    cycle."""
    captured = capture_clients(monkeypatch)
    manager = await running(http_entry(server_url))
    assert manager.up
    await manager.stop()

    (client,) = captured.clients
    assert client.is_closed


async def test_a_refused_handshake_still_closes_the_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other half of the lifecycle: the connect fails past client
    construction, which is the path a flapping server takes on every
    reconnect attempt."""

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - the stdlib's spelling
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_args: object) -> None:
            """Silence: the stub is not the subject of the test."""

    stub = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=stub.serve_forever, daemon=True)
    thread.start()
    captured = capture_clients(monkeypatch)
    try:
        manager = await running(http_entry(f"http://127.0.0.1:{stub.server_port}/mcp"))
        await manager.stop()
    finally:
        stub.shutdown()
        thread.join(timeout=LIFECYCLE_TIMEOUT_S)
        stub.server_close()

    assert not manager.up
    # Construction happened, so this asserts closure rather than absence.
    (client,) = captured.clients
    assert client.is_closed


async def test_a_malformed_handshake_keeps_the_servers_bytes_out_of_the_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An MCP server is a third party, and a hostile or broken one writes
    the handshake. The SDK's client logs the session id it is handed, the
    raw result it could not parse, and a traceback whose validation
    message quotes the bytes that failed, all of which would land in the
    JSON log the operator collects. None of it is this server's to
    publish, so none of it may arrive there."""
    sentinel = "not-a-real-value-8c1d4f7b-poison"

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - the stdlib's spelling
            length = int(self.headers.get("Content-Length") or 0)
            asked = json.loads(self.rfile.read(length)) if length else {}
            # A well-formed JSON-RPC envelope, answering the id that was
            # asked so the client accepts it as its reply, around a
            # result that is not an InitializeResult. That is what makes
            # the SDK log the parse failure and the raw result beside it,
            # and what puts this server's bytes in the validation error
            # the manager then catches.
            body = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": asked.get("id", 0),
                    "result": {"protocolVersion": sentinel, "capabilities": sentinel},
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Mcp-Session-Id", f"{sentinel}-session")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            """Silence: the stub is not the subject of the test."""

    stub = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=stub.serve_forever, daemon=True)
    thread.start()
    try:
        # Everything the SDK's client has to say, down to the session id
        # it announces at info. The lane's own loggers keep their levels:
        # at DEBUG, httpcore prints the headers of every response any
        # httpx client in the process receives, which is a property of
        # turning debug logging on rather than anything this transport
        # decides.
        with caplog.at_level(logging.DEBUG, logger=SDK_CLIENT_LOGGER):
            manager = await running(
                http_entry(f"http://127.0.0.1:{stub.server_port}/mcp"), name="weather"
            )
            await manager.stop()
    finally:
        stub.shutdown()
        thread.join(timeout=LIFECYCLE_TIMEOUT_S)
        stub.server_close()

    assert not manager.up
    # Rendered as the container renders it, since the JSON formatter is
    # what would serialize a traceback into a field of its own.
    rendered = caplog.text + "".join(
        logs.JsonFormatter().format(record) for record in caplog.records
    )
    assert sentinel not in rendered
    assert not [record for record in caplog.records if record.name == SDK_CLIENT_LOGGER]
    assert all(record.exc_info is None for record in caplog.records)
    # The operator still learns that this server is down, and what kind
    # of failure it was, from the line that is ours to write.
    (announced,) = [
        record
        for record in caplog.records
        if record.name == MANAGER_LOGGER and record.levelno == logging.WARNING
    ]
    assert "weather" in announced.getMessage()


def test_a_failure_reason_names_types_and_not_messages() -> None:
    """The reason token is the whole diagnosis in that line, so it has to
    survive a group: the transport raises its failures inside one, and
    "ExceptionGroup" on its own tells an operator nothing."""
    quoted = "not-a-real-value-in-a-message"
    assert _reason(RuntimeError(quoted)) == "RuntimeError"
    grouped = ExceptionGroup(quoted, [ValueError(quoted), TimeoutError()])
    assert _reason(grouped) == "TimeoutError, ValueError"
    assert quoted not in _reason(grouped)


# The lifecycle events this transport decides
#
# The five are covered over stdio, where the manager's logic lives. What
# is here is what only a socket can say: which transport the connect
# event names, the two down reasons a network produces that a child
# process does not, and the sanitization sentinel, which belongs beside
# the malformed-handshake test above because that is where a far side's
# bytes actually arrive.


async def test_the_connect_event_names_the_transport_it_came_up_on(
    server_url: str, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger=MANAGER_LOGGER):
        manager = await running(http_entry(server_url))
        await manager.stop()

    assert fields_of(one_event(caplog, "mcp_connected"))["transport"] == "streamable_http"


async def test_a_url_nobody_answers_is_down_for_the_transport(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """And it is the reason `_down_reason` overrides the phase marker
    for a transport error. This client is entered before it has spoken
    to anything, so a refused connection raises on the first request of
    the handshake rather than on the way in; the marker says
    initialization and the truth is that nothing answered."""
    with unused_url() as url, caplog.at_level(logging.INFO, logger=MANAGER_LOGGER):
        manager = await running(http_entry(url))
        await manager.stop()

    assert fields_of(one_event(caplog, "mcp_down"))["reason"] == TRANSPORT_FAILED


async def test_a_server_that_never_answers_is_down_for_the_bound(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A socket the kernel accepts into its backlog and nothing behind
    it ever reads: the handshake is sent and no answer comes, which is
    the shape of a box that is powered on and wedged. The envelope's own
    bound is what ends it, and that is a reason of its own rather than
    whichever call happened to be outstanding.

    The bound is shortened to keep the test short. What is under test is
    the token, not the production constant.
    """
    monkeypatch.setattr(manager_module, "CONNECT_TIMEOUT_S", 0.3)
    with socket.socket() as listening:
        listening.bind(("127.0.0.1", 0))
        listening.listen(1)
        url = f"http://127.0.0.1:{listening.getsockname()[1]}/mcp"

        with caplog.at_level(logging.INFO, logger=MANAGER_LOGGER):
            manager = await running(http_entry(url))
            await manager.stop()

    assert not manager.up
    assert fields_of(one_event(caplog, "mcp_down"))["reason"] == CONNECT_TIMEOUT


async def test_a_handshake_this_server_cannot_read_keeps_its_bytes_out_of_the_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The sentinel case for the event surface. The suite above proves
    the SDK's own records do not reach a handler; this proves the record
    that replaces them carries the phase and nothing else, in its fields
    as well as in its sentence, with the far side's bytes in the one
    place they are guaranteed to be read: the result the handshake could
    not be parsed out of.
    """
    sentinel = "not-a-real-value-3f9a2b6c-poison"

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - the stdlib's spelling
            length = int(self.headers.get("Content-Length") or 0)
            asked = json.loads(self.rfile.read(length)) if length else {}
            body = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": asked.get("id", 0),
                    "result": {"protocolVersion": sentinel, "capabilities": sentinel},
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Mcp-Session-Id", f"{sentinel}-session")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            """Silence: the stub is not the subject of the test."""

    stub = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=stub.serve_forever, daemon=True)
    thread.start()
    try:
        # Every logger in the process, at the level a deployment runs.
        # Not at DEBUG, which is what the test above uses for the SDK's
        # own channel: at DEBUG httpcore prints the headers of every
        # response any httpx client receives, this stub answers with the
        # sentinel in one of them, and what that would measure is
        # somebody else's debug logging rather than this surface.
        with caplog.at_level(logging.INFO):
            manager = await running(
                http_entry(f"http://127.0.0.1:{stub.server_port}/mcp"), name="weather"
            )
            await manager.stop()
    finally:
        stub.shutdown()
        thread.join(timeout=LIFECYCLE_TIMEOUT_S)
        stub.server_close()

    assert not manager.up
    # The transport carried the bytes and the handshake would not parse,
    # which is the phase this is, and it is neither of the two the type
    # rules override.
    down = one_event(caplog, "mcp_down")
    fields = fields_of(down)
    assert isinstance(fields.pop("duration_ms"), int)
    assert fields == {"event": "mcp_down", "entry": "weather", "reason": INITIALIZE_FAILED}
    # Rendered as the container renders it, so the fields are searched
    # and not only the sentences, and across every record the drive
    # produced rather than only the one under test.
    rendered = caplog.text + "".join(
        logs.JsonFormatter().format(record) for record in caplog.records
    )
    assert sentinel not in rendered
