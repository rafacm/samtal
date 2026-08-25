"""A websocket endpoint that records what it was handed and answers
whatever a case tells it to.

An instrument, not a second server, and the distinction is the whole
reason it exists. The real-server lane proves COMPATIBILITY, which is the
thing that matters most and the thing only a real vinga-server can say.
It cannot say two others:

- **the handshake headers.** `Protocol-Version` is read by nothing on the
  server side (`ws.py`), so a conversation that succeeds says nothing
  about whether the header was sent. Here the headers are recorded, and
  reading them off that recording is the only way that fact is
  observable at all.
- **every adversarial answer.** A malformed server hello, a `tts stop`
  with no start, a truncated binary frame, a close carrying
  credential-shaped bytes: a correct server produces none of them, so a
  client's behavior when it meets one cannot be driven against one.

What a case gets is a URL and a `Recorded`; what it gives is a script,
which is an ordinary function handed the live connection. Everything the
script does is the peer's whole behavior, so a case that wants a peer
that says nothing writes a script that returns.
"""

import contextlib
import json
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

from websockets.sync.server import ServerConnection, serve

from vinga_server.protocol import framing
from vinga_server.protocol.messages import AudioParams, server_hello, stt_message, tts_message

# What the peer calls the session it hands out. A case that needs to know
# what the client echoed reads it from here rather than inventing one.
SESSION = "a-session-the-peer-named"


@dataclass
class Recorded:
    """Everything one connection was handed, in the order it arrived."""

    # The handshake's own headers, lower-cased the way HTTP means them.
    # Empty until a client connects.
    headers: dict[str, str] = field(default_factory=dict)

    # Every text frame the client sent, as it sent it.
    texts: list[str] = field(default_factory=list)

    # Every binary frame, whole and still framed.
    frames: list[bytes] = field(default_factory=list)

    # Set once the script has finished, so a case can wait for the peer
    # to be done rather than sleeping.
    finished: threading.Event = field(default_factory=threading.Event)

    def messages(self) -> list[dict]:
        """The text frames as the objects they are."""
        return [json.loads(text) for text in self.texts]

    def of_type(self, wanted: str) -> list[dict]:
        return [message for message in self.messages() if message.get("type") == wanted]


@contextlib.contextmanager
def peer(script: Callable[[ServerConnection, Recorded], None]) -> Iterator[tuple[str, Recorded]]:
    """A running peer on an ephemeral loopback port, and what it records.

    The script runs on the serving thread with the connection open. When
    it returns, the connection is closed by the library's own handler
    unless the script closed it itself, which is what lets a case choose
    the close code.
    """
    recorded = Recorded()

    def handle(connection: ServerConnection) -> None:
        recorded.headers = {
            name.lower(): value for name, value in connection.request.headers.raw_items()
        }
        try:
            script(connection, recorded)
        finally:
            recorded.finished.set()

    with serve(handle, "127.0.0.1", 0) as server:
        port = server.socket.getsockname()[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"ws://127.0.0.1:{port}/xiaozhi/v1/", recorded
        finally:
            server.shutdown()
            thread.join(timeout=10)


# The scripts a case reaches for, and the pieces they are built from


def read_until_listen_stop(connection: ServerConnection, recorded: Recorded) -> None:
    """Take everything the client sends through its `listen stop`.

    Every case that answers a reply reads the utterance first, because
    the client sends its packets before it waits for anything and a peer
    that answered early would be testing a different order.
    """
    for received in connection:
        if isinstance(received, bytes):
            recorded.frames.append(received)
            continue
        recorded.texts.append(received)
        message = json.loads(received)
        if message.get("type") == "listen" and message.get("state") == "stop":
            return


def greet(connection: ServerConnection, recorded: Recorded) -> None:
    """Read the device hello and answer with the server's own.

    Built by `protocol/messages.py`, so what the peer sends is what a
    vinga-server sends: an instrument that hand-rolled its hello would be
    testing the client against a second encoding of the wire.
    """
    received = connection.recv()
    assert isinstance(received, str), "a device opens with a text frame"
    recorded.texts.append(received)
    connection.send(server_hello(SESSION, AudioParams()))


def speak(connection: ServerConnection, *, heard: str, sentences: list[str], packets: int) -> None:
    """One reply: what was heard, then the sentences and their audio."""
    connection.send(stt_message(SESSION, heard))
    connection.send(tts_message(SESSION, "start"))
    for sentence in sentences:
        connection.send(tts_message(SESSION, "sentence_start", text=sentence))
    for at in range(packets):
        connection.send(framing.wrap(1, bytes([at % 251]) * 40))
    connection.send(tts_message(SESSION, "stop"))


def conversing(
    *, heard: str = "Hello, can you hear me?", sentences: list[str], packets: int = 3
) -> Callable[[ServerConnection, Recorded], None]:
    """The ordinary peer: greet, listen to the whole utterance, reply.

    This is the shape a correct server has, and it is here so that the
    adversarial scripts can be read as the one thing each of them
    changes.
    """

    def script(connection: ServerConnection, recorded: Recorded) -> None:
        greet(connection, recorded)
        read_until_listen_stop(connection, recorded)
        speak(connection, heard=heard, sentences=sentences, packets=packets)

    return script
