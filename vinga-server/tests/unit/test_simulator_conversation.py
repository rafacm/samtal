"""`vinga simulator run`'s websocket half, against a peer a case
controls.

The integration lane drives this against a real vinga-server, which is
the compatibility claim only a real server can make. This file makes the
claims a real server cannot be asked for, and that is why it exists
rather than overlapping:

- the four handshake headers, read off the peer's own recording.
  `Protocol-Version` is read by NOTHING on the server side, so a
  conversation that succeeded against a real server would say nothing
  about whether it was sent;
- every adversarial answer. A malformed hello, a `tts stop` with no
  start, a truncated binary frame, a close carrying credential-shaped
  bytes: a correct server produces none of them, and each is a shape this
  client has to have a decided answer to;
- the no-leak inventory of the websocket half, five cases, each planting
  its own sentinel on all four surfaces.

The utterance is real and the pacing is not. `conversation.sleep` is
replaced, because the packets are twenty-eight sixty-millisecond frames
and waiting them out per case would be most of a minute across this file
for no assertion at all; the pacing itself is asserted once, against that
same seam, which is the only thing a real sleep would have added.
"""

import json
import logging
import threading
import time
from collections.abc import Iterator

import pytest

from tests.support.config_cli import chain, logged
from tests.support.peer import SESSION, Recorded, conversing, greet, peer, read_until_listen_stop
from vinga_server.config.loader import ConfigError
from vinga_server.logs import quieted
from vinga_server.protocol import framing
from vinga_server.protocol.messages import (
    AudioParams,
    server_hello,
    stt_message,
    tts_message,
)
from vinga_server.simulator import board, conversation, utterance

# Nothing here is a real credential, and each is shaped so a substring
# check for it cannot match by accident. One per field that could carry
# one, so a leak names its own source.
DEVICE_TOKEN = "dev-tok-9e42c1-never-a-real-credential"

CLOSE_REASON = "sk-closereason-6b1f22-never-a-real-credential"

HELLO_PLANTED = "sk-hello-3a7d54-never-a-real-credential"

REPLY = ["Yes, I can hear you.", "What would you like?"]


@pytest.fixture
def unpaced(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[float]]:
    """The pacing seam, held by the case.

    `sleep` is imported into `conversation`, so replacing the module's
    own name replaces the seam and not the standard library. What every
    case here gets is the same order of sends with none of the waiting.
    """
    slept: list[float] = []
    monkeypatch.setattr(conversation, "sleep", slept.append)
    yield slept


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> list:
    """The connection this side opened, recorded at the module's own
    seam.

    `connect` is imported into `conversation`, so replacing the module's
    name replaces the seam and not the library. A case that has to know
    when the far side has finished closing needs the object holding that
    answer, and there is no other way to reach it from outside.
    """
    held: list = []
    real = conversation.connect

    def connecting(*arguments, **named):
        socket = real(*arguments, **named)
        held.append(socket)
        return socket

    monkeypatch.setattr(conversation, "connect", connecting)
    return held


@pytest.fixture
def said() -> utterance.Utterance:
    return utterance.packaged()


@pytest.fixture
def identity() -> board.Identity:
    return board.Identity.of(board.DEFAULT_MAC)


def held(url: str, identity: board.Identity, said: utterance.Utterance, **overrides):
    """One turn, with the arguments every case shares."""
    printed: list[str] = []
    arguments = {
        "target": url,
        "token": DEVICE_TOKEN,
        "identity": identity,
        "version": 1,
        "said": said,
        "say": printed.append,
    }
    arguments.update(overrides)
    reply = conversation.converse(**arguments)
    return reply, printed


# The ordinary turn


def test_one_turn_reaches_the_end_of_the_reply(unpaced, identity, said) -> None:
    """The happy path, and what a verdict is written from: the
    transcript, the sentences, the reply's audio counted rather than
    decoded, and where the machine finished."""
    with peer(conversing(sentences=REPLY, packets=5)) as (url, recorded):
        reply, printed = held(url, identity, said)

    assert reply.transcript == "Hello, can you hear me?"
    assert reply.sentences == tuple(REPLY)
    assert reply.packets == 5
    assert reply.audio_ms == 5 * AudioParams().frame_duration
    assert reply.state == conversation.CLOSED
    assert reply.surprises == ()
    assert reply.closed == conversation.CLOSE_NAMES[1000]
    # As they arrived, which is the difference between watching a
    # conversation and reading a report of one.
    assert printed == [
        "heard: Hello, can you hear me?",
        f"said: {REPLY[0]}",
        f"said: {REPLY[1]}",
    ]
    assert recorded.finished.wait(timeout=10)


def test_the_handshake_carries_the_four_headers_the_firmware_sets(
    unpaced, identity, said
) -> None:
    """The claim only this peer can make.

    `Protocol-Version` is read by nothing on the server side, so no
    real-server case can prove it was sent, and a simulator that stopped
    sending it would be a simulator no longer doing what a board does.
    """
    with peer(conversing(sentences=REPLY)) as (url, recorded):
        held(url, identity, said)

    assert recorded.headers["authorization"] == f"Bearer {DEVICE_TOKEN}"
    assert recorded.headers["device-id"] == identity.mac
    assert recorded.headers["client-id"] == identity.client_id
    assert recorded.headers["protocol-version"] == "1"


def test_the_hello_announces_the_negotiated_version_and_the_packaged_audio(
    unpaced, identity, said
) -> None:
    """What this board says it is: the framing version the check-in reply
    named, and the rate and packet duration the asset was actually
    encoded at, read off the asset rather than written twice.

    `features` is empty, which is what says this board publishes no MCP
    tools of its own, and it is why no `mcp` envelope ever arrives.
    """
    with peer(conversing(sentences=REPLY)) as (url, recorded):
        held(url, identity, said, version=3)

    [hello] = recorded.of_type("hello")
    assert hello["version"] == 3
    assert hello["transport"] == "websocket"
    assert hello["audio_params"]["sample_rate"] == said.sample_rate
    assert hello["audio_params"]["frame_duration"] == said.frame_duration_ms
    assert hello["features"] == {}


def test_the_utterance_is_bracketed_by_a_manual_listen(unpaced, identity, said) -> None:
    """Start, then the packets, then stop, and both listens name the mode
    they listen in: a listen carrying no mode is one the capability table
    says this simulator does not send."""
    with peer(conversing(sentences=REPLY)) as (url, recorded):
        held(url, identity, said)

    start, stop = recorded.of_type("listen")
    assert (start["state"], start["mode"]) == ("start", conversation.LISTENING_MODE)
    assert (stop["state"], stop["mode"]) == ("stop", conversation.LISTENING_MODE)
    assert start["session_id"] == stop["session_id"] == SESSION


@pytest.mark.parametrize("version", framing.SUPPORTED_VERSIONS)
def test_the_packets_go_out_under_the_negotiated_framing(
    version: int, unpaced, identity, said
) -> None:
    """The round trip at all three versions, through the server's own
    `wrap` on this side and `unwrap` on the peer's.

    The asset is stored as version 2 frames and sent under whatever the
    session negotiated, which is the whole reason the two are separate
    facts: at version 1 the packets go out bare.
    """
    with peer(conversing(sentences=REPLY)) as (url, recorded):
        held(url, identity, said, version=version)

    sent = [framing.unwrap(version, frame).payload for frame in recorded.frames]
    assert tuple(sent) == said.packets


def test_the_control_channel_is_text_and_only_audio_is_framed(
    unpaced, identity, said
) -> None:
    """The rule finding 3 of the plan review corrected: every JSON
    control message is a websocket TEXT frame, and `framing.wrap` reaches
    audio and nothing else.

    Asserted from both ends: every text frame this side sent parses as a
    control message, and `wrap` was reached exactly as many times as
    there are packets in the utterance and not once more.

    The peer sends no reply audio, deliberately. `framing` is one module
    object, so a peer that wrapped its own frames would be counted here
    and the assertion would be about the sum of two sides.
    """
    wrapped: list[int] = []
    real = framing.wrap

    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(
            conversation.framing,
            "wrap",
            lambda version, payload, **rest: wrapped.append(version) or real(
                version, payload, **rest
            ),
        )
        with peer(conversing(sentences=REPLY, packets=0)) as (url, recorded):
            held(url, identity, said)

    assert {message["type"] for message in recorded.messages()} == {"hello", "listen"}
    assert len(wrapped) == len(said.packets), "wrap was reached for something that is not audio"
    assert len(recorded.frames) == len(said.packets)


def test_the_packets_are_paced_the_way_a_microphone_delivers_them(
    unpaced, identity, said
) -> None:
    """One packet duration between packets, because that is what the
    endpointer on the other side is measuring. A burst would be the same
    bytes and not the same utterance."""
    with peer(conversing(sentences=REPLY)) as (url, _):
        held(url, identity, said)

    assert unpaced == [said.frame_duration_ms / 1000] * len(said.packets)


# The ordering, driven off its happy path


def test_a_tts_stop_with_no_start_before_it_advances_nothing(
    unpaced, identity, said
) -> None:
    """The machine's first rule. A stop this side never expected does not
    end the reply, and it does not vanish either: it is reported by its
    own name and the state it arrived in."""

    def script(connection, recorded: Recorded) -> None:
        greet(connection, recorded)
        read_until_listen_stop(connection, recorded)
        connection.send(tts_message(SESSION, "stop"))
        connection.send(tts_message(SESSION, "start"))
        connection.send(tts_message(SESSION, "sentence_start", text=REPLY[0]))
        connection.send(tts_message(SESSION, "stop"))

    with peer(script) as (url, _):
        reply, _ = held(url, identity, said)

    assert reply.state == conversation.CLOSED
    assert reply.sentences == (REPLY[0],)
    assert reply.surprises == (f"tts stop arrived while {conversation.AWAITING_REPLY}",)


def test_a_transcript_after_the_reply_completed_advances_nothing(
    unpaced, identity, said
) -> None:
    """The reply is over when `tts stop` says so, and the read loop stops
    there, so an `stt` sent after it never reaches this side at all. What
    this asserts is that the state is what ended the reading, not a
    guess about what the peer stopped sending."""

    def script(connection, recorded: Recorded) -> None:
        greet(connection, recorded)
        read_until_listen_stop(connection, recorded)
        connection.send(tts_message(SESSION, "start"))
        connection.send(tts_message(SESSION, "stop"))
        connection.send(stt_message(SESSION, "said after the reply ended"))

    with peer(script) as (url, _):
        reply, printed = held(url, identity, said)

    assert reply.state == conversation.CLOSED
    assert reply.transcript == ""
    assert printed == []


def test_a_binary_frame_before_the_hello_advances_nothing(unpaced, identity, said) -> None:
    """Audio arriving where a hello is expected. Reported, and the hello
    that follows it still lands, which is what "advances nothing" has to
    mean to be worth saying."""

    def script(connection, recorded: Recorded) -> None:
        received = connection.recv()
        recorded.texts.append(received)
        connection.send(framing.wrap(1, b"audio before anything"))
        connection.send(server_hello(SESSION, AudioParams()))
        read_until_listen_stop(connection, recorded)
        connection.send(tts_message(SESSION, "start"))
        connection.send(tts_message(SESSION, "stop"))

    with peer(script) as (url, _):
        reply, _ = held(url, identity, said)

    assert reply.state == conversation.CLOSED
    assert reply.surprises == (f"audio arrived while {conversation.HELLO_SENT}",)


def test_a_frame_that_does_not_match_the_framing_is_reported_not_counted(
    unpaced, identity, said
) -> None:
    """A truncated frame under a negotiated version 2: it fails the
    server's own `unwrap`, so it is a surprise rather than a number added
    to the reply's size."""

    def script(connection, recorded: Recorded) -> None:
        greet(connection, recorded)
        read_until_listen_stop(connection, recorded)
        connection.send(tts_message(SESSION, "start"))
        connection.send(framing.wrap(2, b"a whole packet"))
        connection.send(b"\x00\x02\x00")
        connection.send(tts_message(SESSION, "stop"))

    with peer(script) as (url, _):
        reply, _ = held(url, identity, said, version=2)

    assert reply.packets == 1
    assert reply.audio_bytes == len(b"a whole packet")
    assert reply.surprises == (
        "a binary frame that does not match the negotiated framing arrived while "
        f"{conversation.SPEAKING}",
    )


def test_audio_before_the_reply_starts_is_a_surprise_and_not_a_count(
    unpaced, identity, said
) -> None:
    """A frame arriving before `tts start` is a frame from outside the
    reply.

    The machine used to expect audio in `awaiting reply` as well as in
    `speaking`, which meant a frame from before the reply began was added
    to the reply's own packets, bytes and duration. What this command
    reports about a reply then depended on what arrived before there was
    one.

    Bite: with `(AWAITING_REPLY, AUDIO)` back in the table, the totals
    below are 2, 20 and two packets' worth of milliseconds instead of 1,
    10 and one, and the surprise is not recorded at all.
    """
    def script(connection, recorded: Recorded) -> None:
        greet(connection, recorded)
        read_until_listen_stop(connection, recorded)
        connection.send(framing.wrap(1, b"0123456789"))
        connection.send(tts_message(SESSION, "start"))
        connection.send(framing.wrap(1, b"9876543210"))
        connection.send(tts_message(SESSION, "stop"))

    with peer(script) as (url, _):
        reply, _ = held(url, identity, said)

    assert reply.packets == 1
    assert reply.audio_bytes == 10
    assert reply.audio_ms == said.frame_duration_ms
    assert reply.surprises == (f"audio arrived while {conversation.AWAITING_REPLY}",)


def test_a_message_of_a_type_this_client_does_not_model_is_named_not_quoted(
    unpaced, identity, said
) -> None:
    """A server that grew an `llm` message. Reported as unmodelled, and
    the type is NOT repeated: `UnknownMessage.type` is a string the peer
    wrote."""

    def script(connection, recorded: Recorded) -> None:
        greet(connection, recorded)
        read_until_listen_stop(connection, recorded)
        connection.send(json.dumps({"type": HELLO_PLANTED, "emotion": "happy"}))
        connection.send(tts_message(SESSION, "start"))
        connection.send(tts_message(SESSION, "stop"))

    with peer(script) as (url, _):
        reply, _ = held(url, identity, said)

    assert reply.state == conversation.CLOSED
    [surprise] = reply.surprises
    assert "does not model" in surprise
    assert HELLO_PLANTED not in surprise


def test_a_close_that_will_not_complete_is_a_safe_outcome_rather_than_a_raise(
    unpaced, identity, said, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half of the eighth state.

    A close that fails after a reply has been read is not a reason to
    lose the reply, and an exception out of that `finally` would replace
    whatever refusal was already in flight and carry the library's own
    message out with it. So the reply survives whole, the machine does
    NOT advance to `closed`, and how the connection ended is reported as
    not this side's to say rather than guessed from a code nobody set.

    Bite: with the close swallowing its failure and advancing anyway,
    this reports `closed` and "the session ended normally" about a
    connection whose close never finished.

    The failure is planted on the CONNECTION rather than on the function
    that closes it, through the same `connect` seam every other case here
    reaches the socket by, so what is under test is the module's own
    close and not a stand-in for it.
    """
    real = conversation.connect

    def refusing(*arguments, **named):
        socket = real(*arguments, **named)
        monkeypatch.setattr(socket, "close", _raising)
        return socket

    monkeypatch.setattr(conversation, "connect", refusing)

    with peer(conversing(sentences=REPLY)) as (url, _):
        reply, _ = held(url, identity, said)

    assert reply.sentences == tuple(REPLY)
    assert reply.state == conversation.REPLY_COMPLETE
    assert reply.closed == conversation.CLOSE_FAILED


def _raising(*arguments: object, **named: object) -> None:
    """A close that will not go, with a message nothing may repeat."""
    raise OSError(CLOSE_REASON)


def test_the_transitions_are_a_table_rather_than_a_chain() -> None:
    """The machine held to being one, off the declaration rather than off
    a run: every state a transition names is one of the eight, and every
    event it names is a message this side classifies, audio, or this
    side's own close."""
    for (state, _), moved in conversation.TRANSITIONS.items():
        assert state in conversation.STATES
        assert moved in conversation.STATES
    assert conversation.TRANSITIONS[(conversation.SPEAKING, "tts stop")] == (
        conversation.REPLY_COMPLETE
    )
    # The eighth state, reached through the table like every other, and
    # from one state only.
    assert conversation.TRANSITIONS[(conversation.REPLY_COMPLETE, conversation.CLOSE)] == (
        conversation.CLOSED
    )
    assert [
        state for state, event in conversation.TRANSITIONS if event == conversation.CLOSE
    ] == [conversation.REPLY_COMPLETE]
    # Nothing may be read in `listening`: this side is sending there, and
    # a machine that accepted a reply mid-utterance would have no order
    # at all.
    assert not any(state == conversation.LISTENING for state, _ in conversation.TRANSITIONS)


# What every wait is bounded by


def test_every_wait_carries_the_bound_the_plan_named() -> None:
    """The bounds table, asserted against the reasons rather than against
    the code that implements them.

    The open and the hello are the SERVER's own hello window, read from
    the module that declares it: the far side gives up there, so waiting
    longer learns nothing. The reply's is local, because a model and a
    text-to-speech engine have no bound this client can compute.
    """
    from vinga_server.device.watchdog import HELLO_TIMEOUT_S

    assert conversation.OPEN_TIMEOUT_S == HELLO_TIMEOUT_S
    assert conversation.HELLO_TIMEOUT == HELLO_TIMEOUT_S
    assert 0 < conversation.REPLY_CEILING_S <= 120.0
    assert 0 < conversation.CLOSE_TIMEOUT_S


def test_a_peer_that_never_answers_the_hello_gives_up_at_the_bound(
    unpaced, identity, said, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A socket that accepts and then says nothing. Bounded, and answered
    by the fixed sentence rather than by a wait a person watches."""
    monkeypatch.setattr(conversation, "HELLO_TIMEOUT", 0.2)

    def script(connection, recorded: Recorded) -> None:
        recorded.finished.wait(timeout=5)

    with peer(script) as (url, _), pytest.raises(ConfigError) as refused:
        held(url, identity, said)

    assert str(refused.value) == conversation.NO_HELLO


def test_a_peer_that_talks_without_saying_hello_still_gives_up(
    unpaced, identity, said, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bound on the TRANSITION rather than on each read.

    A bound per read is not a bound at all: a peer that sends one frame
    of anything just before each window comes due restarts the window
    every time. Here the peer sends a `stt` in a tight loop and never a
    hello, which is a valid message arriving where nothing expects one,
    so every frame is a surprise and none of them advances anything.

    Bite: with the deadline computed inside the loop instead of on entry
    to the state, this case waits out every one of the peer's frames and
    only then times out, so it takes the whole chatter rather than the
    whole bound. The peer chatters for a fixed span far longer than the
    wait, and the assertion sits between the two: a quarter of a second
    when the bound holds, six when it does not. The chatter is bounded
    rather than endless on purpose, because a bug that hangs is a bug a
    runner cannot report.
    """
    monkeypatch.setattr(conversation, "HELLO_TIMEOUT", 0.2)
    chatter_until = time.monotonic() + 6.0

    def script(connection, recorded: Recorded) -> None:
        recorded.texts.append(connection.recv())
        while time.monotonic() < chatter_until:
            try:
                connection.send(stt_message(SESSION, "not a hello"))
            except Exception:
                return
            time.sleep(0.01)

    started = time.monotonic()
    with peer(script) as (url, _), pytest.raises(ConfigError) as refused:
        held(url, identity, said)
    took = time.monotonic() - started

    assert str(refused.value) == conversation.NO_HELLO
    assert took < 3.0, "the hello wait restarted on the traffic that was meant to end it"


def test_a_reply_that_never_ends_gives_up_at_the_ceiling(
    unpaced, identity, said, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `tts start` with no stop after it. The bound is on the whole
    reply rather than per message, because what a person is waiting for
    is the reply and a server sending a sentence a second forever would
    satisfy any per-message bound."""
    monkeypatch.setattr(conversation, "REPLY_CEILING_S", 0.3)

    def script(connection, recorded: Recorded) -> None:
        greet(connection, recorded)
        read_until_listen_stop(connection, recorded)
        connection.send(tts_message(SESSION, "start"))
        recorded.finished.wait(timeout=5)

    with peer(script) as (url, _), pytest.raises(ConfigError) as refused:
        held(url, identity, said)

    assert str(refused.value) == conversation.NO_REPLY


# The websocket half's no-leak inventory
#
# Five cases, one sentinel each, on the four surfaces the rest of this
# suite uses: stdout, stderr, every log record rendered whole, and the
# exception chain a walker would find. Two of the five are about the
# address the reply named and are driven through the command in
# `test_simulator_board.py`, where the check-in's answer is a case's to
# write; the three here are about the socket itself.


def test_the_device_token_reaches_no_surface_at_all(
    unpaced,
    identity,
    said,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The credential nobody typed. It is on the handshake, so the peer
    has it and this side has it, and neither is a reason for it to be
    printed or logged.

    The log surface is the one that matters here: `websockets` narrates
    its connections, and what it has to say includes the request's
    headers.
    """
    with caplog.at_level(0):
        with peer(conversing(sentences=REPLY)) as (url, recorded):
            held(url, identity, said)

    captured = capsys.readouterr()
    assert recorded.headers["authorization"] == f"Bearer {DEVICE_TOKEN}"
    for surface in (captured.out, captured.err, logged(caplog)):
        assert DEVICE_TOKEN not in surface


def test_a_malformed_server_hello_is_refused_without_quoting_a_field(
    unpaced,
    identity,
    said,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A hello with no session id, carrying a credential-shaped value
    where a client would look for one.

    There is no session to speak in, so this is a refusal rather than a
    surprise to note and go on from, and the sentence names no field and
    quotes nothing: what arrived is whatever that address returned.
    """
    def script(connection, recorded: Recorded) -> None:
        recorded.texts.append(connection.recv())
        connection.send(json.dumps({"type": "hello", "session_id": [HELLO_PLANTED]}))
        recorded.finished.wait(timeout=5)

    with caplog.at_level(0):
        with peer(script) as (url, _), pytest.raises(ConfigError) as refused:
            held(url, identity, said)

    assert str(refused.value) == conversation.BAD_HELLO
    captured = capsys.readouterr()
    surfaces = (captured.out, captured.err, logged(caplog), chain(refused.value))
    for surface in surfaces:
        assert HELLO_PLANTED not in surface
    assert refused.value.__cause__ is None and refused.value.__context__ is None


def test_the_connections_own_logger_emits_at_no_level_at_all(
    unpaced,
    identity,
    said,
    opened: list,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The surface a floor could not hold.

    `websockets/sync/connection.py` calls `self.logger.error(...,
    exc_info=True)` from four reachable paths, the keepalive ping among
    them. An ERROR record clears a WARNING floor and `exc_info=True` puts
    a whole traceback on a retained surface, which is the one thing every
    sentence in this module exists to keep off one.

    So the connection is handed a disabled, non-propagating logger of
    this module's own, and what is asserted is the connection's actual
    logger rather than a name: the exact call the library makes, with a
    credential-shaped exception behind it, reaching nothing.

    Bite: the same record emitted on the library's own logger inside the
    floor this module used to rely on DOES land, traceback and all, which
    is what says the floor was never the fix.
    """
    with peer(conversing(sentences=REPLY)) as (url, _):
        held(url, identity, said)

    [socket] = opened
    planted = RuntimeError(HELLO_PLANTED)

    with caplog.at_level(0):
        socket.logger.error("keepalive ping failed", exc_info=planted)

    # The library wraps what it is given in a `LoggerAdapter`, which
    # delegates `isEnabledFor` to the logger underneath it, so that is
    # the object the claim is about.
    underneath = getattr(socket.logger, "logger", socket.logger)
    assert underneath is conversation.socket_logger()
    assert underneath.disabled and not underneath.propagate
    assert HELLO_PLANTED not in logged(caplog)
    assert caplog.records == []

    # And the floor alone, asked the same question, which is the bite.
    with caplog.at_level(0), quieted(conversation.SOCKET_LOGGERS, conversation.QUIET_LEVEL):
        logging.getLogger("websockets.client").error("keepalive ping failed", exc_info=planted)

    assert HELLO_PLANTED in logged(caplog), (
        "the floor stopped admitting the record this fix exists for, so the bite is stale"
    )


def test_a_hello_that_named_no_audio_parameters_ends_the_conversation(
    unpaced, identity, said
) -> None:
    """The refusal the model change makes reachable from here.

    Everything this side does after the hello is paced and announced by
    what the hello named: the reply's own packet duration is read off
    it, and a block manufactured out of nothing would have been this
    client's guesses reported as the far side's answer. So a hello
    without one is not a hello, and there is no session to speak in.

    Bite: with `audio_params` back to a `default_factory`, this peer's
    hello parses, the conversation proceeds, and the case does not raise
    at all.
    """
    def script(connection, recorded: Recorded) -> None:
        recorded.texts.append(connection.recv())
        connection.send(json.dumps({"type": "hello", "session_id": SESSION}))
        recorded.finished.wait(timeout=5)

    with peer(script) as (url, _), pytest.raises(ConfigError) as refused:
        held(url, identity, said)

    assert str(refused.value) == conversation.BAD_HELLO


def test_a_peer_close_reason_is_read_and_never_relayed(
    unpaced,
    identity,
    said,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A close carrying credential-shaped bytes.

    The code is looked up in a closed set and reported in this side's own
    words; the reason is arbitrary far-side prose and is dropped. A close
    code outside the set is reported as outside it, without the number.

    The two closes race, and this case is about one side of that race.
    Once `tts stop` has landed the client leaves the turn and closes
    normally, and whichever close is processed first decides the code:
    this side's own 1000 is an honest verdict too, but it is a different
    case. So the peer holds its 4001 until this side is standing at its
    own close, and this side's close waits for the peer's frame to have
    been read before it goes. Both waits are bounded and both bounds are
    asserted, so a runner slow enough to outlive one is a named
    synchronization failure rather than the race quietly back.
    """
    # One bound for both sides, and the one the mid-utterance case below
    # already polls `close_code` under.
    bound = 10.0
    at_the_close = threading.Event()
    peer_waited: list[bool] = []
    expired: list[str] = []

    def script(connection, recorded: Recorded) -> None:
        greet(connection, recorded)
        read_until_listen_stop(connection, recorded)
        connection.send(tts_message(SESSION, "start"))
        connection.send(tts_message(SESSION, "stop"))
        peer_waited.append(at_the_close.wait(timeout=bound))
        connection.close(code=4001, reason=CLOSE_REASON)

    real = conversation.connect

    def holding(*arguments, **named):
        socket = real(*arguments, **named)
        closing = socket.close

        def close_after_the_peers(*arguments: object, **named: object) -> None:
            # Entering here IS this side reaching its close, which is
            # what the peer is waiting to hear before it sends 4001.
            at_the_close.set()
            deadline = time.monotonic() + bound
            while socket.close_code is None and time.monotonic() < deadline:
                time.sleep(0.01)
            if socket.close_code is None:
                expired.append("the peer's close was never read")
            # Unconditional: the socket is given back whatever the wait
            # decided, so cleanup never rides on an assertion.
            closing(*arguments, **named)

        monkeypatch.setattr(socket, "close", close_after_the_peers)
        return socket

    monkeypatch.setattr(conversation, "connect", holding)

    with caplog.at_level(0):
        with peer(script) as (url, recorded):
            reply, _ = held(url, identity, said)

    # Both bounds, asserted before the verdict they exist to decide: a
    # runner that outlived either one has to say so by name, or the
    # verdict below would be the old race passing or failing quietly.
    # The wait for the script is the longer one on purpose, so what it
    # reports is the peer's outcome rather than a second race with it.
    assert recorded.finished.wait(timeout=bound * 2)
    assert peer_waited == [True], "the peer never saw this side reach its own close"
    assert expired == []
    assert reply.closed == conversation.UNKNOWN_CLOSE
    assert "4001" not in reply.closed
    captured = capsys.readouterr()
    for surface in (captured.out, captured.err, logged(caplog), reply.closed):
        assert CLOSE_REASON not in surface


def test_a_handshake_the_peer_refuses_names_a_class_and_nothing_else(
    unpaced, identity, said, caplog: pytest.LogCaptureFixture
) -> None:
    """The refusal a bad token produces against a real server, driven
    here against a port nothing is listening on.

    What may be said is the exception's class. The library puts the URI
    into its exceptions and a refused upgrade carries the peer's own
    status line, and neither is this side's to repeat: the address is
    what a device token would be sent to.
    """
    with caplog.at_level(0), pytest.raises(ConfigError) as refused:
        held("ws://127.0.0.1:9/xiaozhi/v1/", identity, said)

    assert str(refused.value).startswith("cannot open a conversation with ")
    assert "127.0.0.1:9" not in str(refused.value)
    assert DEVICE_TOKEN not in str(refused.value) + logged(caplog) + chain(refused.value)
    assert refused.value.__cause__ is None and refused.value.__context__ is None


def test_the_refusals_of_this_module_are_built_rather_than_formatted() -> None:
    """The two that take a value take a CLASS NAME and nothing else, and
    the rest are constants. A sentence assembled from a peer's own text
    would pass every case above on the day it was written and leak on the
    first connection that carried something else."""
    for sentence in (conversation.NO_HELLO, conversation.BAD_HELLO, conversation.NO_REPLY):
        assert "{" not in sentence and "%s" not in sentence
    for built in (conversation.cannot_open("SomeError"), conversation.cannot_speak("SomeError")):
        assert "SomeError" in built
    for name in conversation.CLOSE_NAMES.values():
        assert "{" not in name and "%s" not in name


def test_a_peer_that_goes_away_mid_utterance_is_a_sentence_not_a_traceback(
    unpaced,
    identity,
    said,
    opened: list,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The send that is likeliest to fail, and the one that used to fail
    loudest.

    The utterance is the longest thing this command sends, so a
    disconnect during it is the ordinary way a run ends badly. It used to
    go out through a bare `socket.send`, which meant a `ConnectionClosed`
    left this module with its own message, its traceback and its chain
    intact; every other send in the file has always gone through the
    boundary.

    The peer waits for the FIRST audio frame before closing with a
    credential-shaped reason, and the pacing hook waits for that close to
    complete before letting the next packet go. Both halves are load
    bearing: a peer that closed after greeting would fail the `listen
    start` instead, which is a control send and has always been guarded,
    and a hook that did not wait would race the close against the
    remaining twenty-seven packets.

    Bite: with `_send_audio` reverted to a bare `socket.send`, what
    leaves this module is a `ConnectionClosedError` whose own message is
    "received 1011 (internal error) <reason>", so the raise is not a
    `ConfigError` at all and the reason reaches the chain.
    """
    def script(connection, recorded: Recorded) -> None:
        greet(connection, recorded)
        for received in connection:
            if isinstance(received, bytes):
                recorded.frames.append(received)
                break
            recorded.texts.append(received)
        connection.close(code=1011, reason=CLOSE_REASON)

    def wait_for_the_close(_seconds: float) -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if opened and opened[0].close_code is not None:
                return
            time.sleep(0.01)

    monkeypatch.setattr(conversation, "sleep", wait_for_the_close)

    with caplog.at_level(0):
        with peer(script) as (url, _), pytest.raises(ConfigError) as refused:
            held(url, identity, said)

    assert str(refused.value) == conversation.cannot_speak("ConnectionClosedError")
    assert refused.value.__cause__ is None and refused.value.__context__ is None
    captured = capsys.readouterr()
    surfaces = (captured.out, captured.err, logged(caplog), chain(refused.value))
    for surface in surfaces:
        assert CLOSE_REASON not in surface
        assert DEVICE_TOKEN not in surface
