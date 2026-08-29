"""The live event stream: `GET /runtime/events`.

Two mechanisms, and the split is not a preference. The refusals (no
token, no server, a filter that cannot be read) are ordinary buffered
answers and go through the ordinary test client. Everything about a
successful stream does not: a live 200 never completes, so a sync
client that reads a whole body would wait for a server that is
deliberately never going to stop talking, and its transport cannot show
a header before the body it is still waiting for. Those tests drive the
application as ASGI instead, message by message, which is where the
first `http.response.start` is readable and where a chunk can be
asserted while the stream is still open.

The keepalive interval is injected for the same reason: a test that
waited out the default would take fifteen seconds to learn one thing.
"""

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.types import Message

from vinga_server.config.api import build_api
from vinga_server.config.models import DatabaseConfig
from vinga_server.events import Emission
from vinga_server.events.live import LiveEvents

TOKEN = "test-api-token-" + "0123456789abcdef" * 2

EVENTS_PATH = "/runtime/events"

BEARER = {"Authorization": f"Bearer {TOKEN}"}

MAC = "aa:bb:cc:dd:ee:ff"

SESSION = "0123456789abcdef0123456789abcdef"

# What an idle stream writes, which is a comment and carries no event.
KEEPALIVE = b": keepalive\n\n"

# Long enough that no keepalive is ever due while a test is looking at a
# stream. It is not a wait: no test reaches it, because every assertion
# below is about something the test itself put on the stream.
#
# It was 0.05 s, shared by every test here, and that was a race rather
# than a setting: a reader with nothing to deliver writes its keepalive
# the moment the deadline passes, so any test slow enough to cross it
# read a comment where it expected an event, and parsed it as no events
# at all. Fifty milliseconds is nothing on a contended runner, and CI
# duly failed on the one test that opens a second application between
# subscribing and reading (#349).
QUIET_S = 3600.0

# And the short one, injected by the single test whose subject is the
# keepalive itself, where the interval is what is being asserted rather
# than something to stay clear of.
KEEPALIVE_S = 0.05


def emission(event: str = "something", level: int = logging.INFO, **payload: Any) -> Emission:
    """One emission in the shape the tap contract hands the hub."""
    return Emission(
        payload={"event": event, **payload},
        at=0.0,
        level=level,
        message=event,
        args=(),
    )


def api_with(
    live: LiveEvents | None = None, keepalive_s: float = QUIET_S
) -> FastAPI:
    """The configuration API with the running server's hub, or without
    one, which is an application built with no server around it."""
    return build_api(TOKEN, DatabaseConfig(), live=live, keepalive_s=keepalive_s)


class Stream:
    """One request to the stream route, held open, read message by
    message."""

    def __init__(
        self,
        incoming: asyncio.Queue[Message],
        outgoing: asyncio.Queue[Message],
        request: asyncio.Task[None],
    ) -> None:
        self._incoming = incoming
        self._outgoing = outgoing
        self._request = request

    async def start(self) -> Message:
        """The response's first message, which is where a streamed
        answer states its status and its headers."""
        message = await asyncio.wait_for(self._outgoing.get(), timeout=5)
        assert message["type"] == "http.response.start", message
        return message

    async def chunk(self) -> bytes:
        """The next piece of body written, whatever it is, and nothing
        about what follows it."""
        message = await asyncio.wait_for(self._outgoing.get(), timeout=5)
        assert message["type"] == "http.response.body", message
        return bytes(message["body"])

    async def written(self) -> bytes:
        """The next piece of body that carries frames, skipping any
        keepalive.

        A keepalive is a comment and says nothing happened, so a test
        that asserts what a stream carried is never about one: reading
        it as if it were an event is how a slow run turns into a failure
        about the wrong thing. The interval is a long one here, so this
        skips nothing in practice; it is what keeps that true whatever a
        runner is doing.
        """
        while True:
            chunk = await self.chunk()
            if chunk != KEEPALIVE:
                return chunk

    async def until_ended(self) -> list[bytes]:
        """Everything written from here to the end of the body, which is
        how a stream that stopped is told from one that is quiet."""
        chunks: list[bytes] = []
        while True:
            message = await asyncio.wait_for(self._outgoing.get(), timeout=5)
            assert message["type"] == "http.response.body", message
            chunks.append(bytes(message["body"]))
            if not message.get("more_body", False):
                return chunks

    async def disconnect(self) -> None:
        """The client going away, which is the ordinary end of a tail."""
        await self._incoming.put({"type": "http.disconnect"})
        await asyncio.wait_for(self._request, timeout=5)


@contextlib.asynccontextmanager
async def streaming(
    app: FastAPI, query: str = "", token: str = TOKEN
) -> AsyncIterator[Stream]:
    """One GET to the stream route, driven as ASGI."""
    incoming: asyncio.Queue[Message] = asyncio.Queue()
    outgoing: asyncio.Queue[Message] = asyncio.Queue()
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": EVENTS_PATH,
        "raw_path": EVENTS_PATH.encode(),
        "query_string": query.encode(),
        "root_path": "",
        "headers": [
            (b"host", b"testserver"),
            (b"authorization", f"Bearer {token}".encode()),
        ],
        "client": ("127.0.0.1", 4242),
        "server": ("testserver", 80),
    }

    async def receive() -> Message:
        return await incoming.get()

    async def send(message: Message) -> None:
        await outgoing.put(message)

    request = asyncio.create_task(app(scope, receive, send))
    try:
        yield Stream(incoming, outgoing, request)
    finally:
        request.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await request


def events_in(chunk: bytes) -> list[dict[str, Any]]:
    """The objects one written chunk carried, as a reader would parse
    them: the `data:` line of every SSE frame in it."""
    return [
        json.loads(line[len("data: ") :])
        for line in chunk.decode().splitlines()
        if line.startswith("data: ")
    ]


# --- the refusals, which are ordinary buffered answers ----------------


def test_the_stream_needs_the_bearer_token() -> None:
    """The gate runs in front of routing, so this route inherits it and
    declares nothing of its own."""
    with TestClient(api_with(LiveEvents())) as anonymous:
        assert anonymous.get(EVENTS_PATH).status_code == 401
        wrong = anonymous.get(EVENTS_PATH, headers={"Authorization": "Bearer wrong"})
        assert wrong.status_code == 401


def test_an_application_without_a_server_refuses_rather_than_saying_nothing() -> None:
    """The prompt read's case rather than the status read's: a stream
    that opened and stayed quiet is exactly what a working server with
    nothing to say looks like, so there is no honest empty here."""
    with TestClient(api_with(None), headers=BEARER) as client:
        response = client.get(EVENTS_PATH)

    assert response.status_code == 503
    assert "no running server" in response.json()["detail"]


@pytest.mark.parametrize(
    ("query", "rule"),
    [
        ({"device": "not-a-mac"}, "MAC address"),
        ({"device": "11:22:33:44:55"}, "MAC address"),
        ({"session": "not-a-session"}, "uuid hex"),
        ({"session": "0" * 31}, "uuid hex"),
        ({"level": "LOUD"}, "DEBUG"),
        ({"level": "12"}, "DEBUG"),
    ],
)
def test_a_filter_that_cannot_be_read_is_refused_without_being_quoted(
    query: dict[str, str], rule: str
) -> None:
    """A filter arrives in a query string, which reaches a response
    body, a proxy's log and a browser's history, so the refusal carries
    the rule and never the value."""
    with TestClient(api_with(LiveEvents()), headers=BEARER) as client:
        response = client.get(EVENTS_PATH, params=query)

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert rule in detail
    for sent in query.values():
        assert sent not in detail
        assert sent not in response.text


# --- and the stream itself, which never completes ---------------------


async def test_the_first_message_says_it_is_an_event_stream() -> None:
    """Asserted from `http.response.start` rather than from a finished
    response: this answer has no end for a buffered client to reach."""
    async with streaming(api_with(LiveEvents())) as stream:
        start = await stream.start()

    headers = dict(start["headers"])
    assert start["status"] == 200
    assert headers[b"content-type"].startswith(b"text/event-stream")
    assert headers[b"cache-control"] == b"no-store"


async def test_an_event_reaches_the_reader_while_the_stream_is_open() -> None:
    hub = LiveEvents()
    async with streaming(api_with(hub)) as stream:
        await stream.start()

        hub.emit(emission("heard", session=SESSION, device=MAC))

        (streamed,) = events_in(await stream.written())

    assert streamed["event"] == "heard"
    assert streamed["session"] == SESSION
    # The stream's own two fields, which the payload does not carry.
    assert streamed["level"] == "INFO"
    assert streamed["ts"].endswith("+00:00")


async def test_a_device_filter_is_canonicalized_before_it_is_applied() -> None:
    """A MAC written the way a board's label prints it tails the same
    device as one written the way the events carry it."""
    hub = LiveEvents()
    async with streaming(api_with(hub), query="device=AA-BB-CC-DD-EE-FF") as stream:
        await stream.start()

        hub.emit(emission("theirs", device="11:22:33:44:55:66"))
        hub.emit(emission("mine", device=MAC))

        (streamed,) = events_in(await stream.written())

    assert streamed["event"] == "mine"


async def test_the_level_defaults_to_info_and_reads_in_any_case() -> None:
    hub = LiveEvents()
    async with streaming(api_with(hub)) as quiet:
        await quiet.start()
        async with streaming(api_with(hub), query="level=debug") as everything:
            await everything.start()

            hub.emit(emission("quiet-one", level=logging.DEBUG))
            hub.emit(emission("loud-one", level=logging.WARNING))

            assert [
                event["event"] for event in events_in(await everything.written())
            ] == ["quiet-one"]
        assert [event["event"] for event in events_in(await quiet.written())] == [
            "loud-one"
        ]


async def test_a_reader_that_fell_behind_is_told_how_many_it_lost() -> None:
    """The count is its own named SSE event, so a loss renders as a loss
    rather than as a field a reader has to look for."""
    hub = LiveEvents(capacity=1)
    async with streaming(api_with(hub)) as stream:
        await stream.start()

        for number in range(4):
            hub.emit(emission(f"event-{number}"))

        chunk = await stream.written()

    assert chunk.startswith(b"event: dropped\ndata: ")
    assert json.loads(chunk.decode().splitlines()[1][len("data: ") :]) == {"dropped": 3}


async def test_an_idle_stream_says_it_is_still_there() -> None:
    """A comment line, which every SSE reader ignores and every proxy
    counts as traffic. The interval is injected, so this takes a
    fiftieth of a second rather than fifteen."""
    async with streaming(api_with(LiveEvents(), keepalive_s=KEEPALIVE_S)) as stream:
        await stream.start()

        assert await stream.chunk() == KEEPALIVE


async def test_the_stream_ends_when_the_server_closes_the_hub() -> None:
    """What a shutdown does to an open tail: it ends, rather than
    holding the process up."""
    hub = LiveEvents()
    async with streaming(api_with(hub)) as stream:
        await stream.start()

        hub.close()

        await stream.until_ended()


async def test_a_reader_that_goes_away_gives_its_subscription_back() -> None:
    """The generator's `finally` is the whole of the cleanup contract,
    and a client disconnect is the path it is hardest to see: nothing in
    the handler runs again."""
    hub = LiveEvents()
    async with streaming(api_with(hub)) as stream:
        await stream.start()
        assert hub.subscribers == 1

        await stream.disconnect()

    assert hub.subscribers == 0
