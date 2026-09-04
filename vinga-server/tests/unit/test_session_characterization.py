"""What a session does today, pinned from outside before it is split.

Issue #85 lifts the device-facing boundary out of `session.py`: the
edge (handshake, codecs, framing, pacing) on one side, the conversation
runtime (endpointing, gates, filler, ASR/LLM/TTS) on the other. The
move is meant to change nothing observable, and "nothing observable"
needs to be checkable rather than asserted in a pull request
description.

So these tests hold the properties the extraction is most able to break
and that no other test holds: the log channel every conversation record
is emitted on, the exact moment `speaking_started` is stamped and which
agent it names, the arbitration between a reply chunk too short to
produce a packet and a filler timer that has not fired, the discipline
that keeps the shared Opus encoder's feed order intact across a filler,
the control message order of one turn on the wire, the shutdown waiting
out a reply that is generating rather than speaking, and both halves of
the pair of exceptions that end a reply quietly.

They deliberately assert wire bytes, structured events, and log records
rather than internals, so that they keep their meaning once the code
they cover lives in two packages. Where they must drive the reply, they
go through `drive_reply` and `start_reply`, which name that entry point
in one place.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from tests.support.configs import BOTH_MAC, POET_MAC, POET_TONE, base_config, config_with_agent
from tests.support.events import events, only
from tests.support.providers import (
    ScriptedLlm,
    StallingLlm,
    Unreachable,
    built_world,
)
from tests.support.sessions import call, drive_reply, session_for, start_reply
from tests.support.sockets import OrderedSocket, spoken
from tests.support.wire import connect, send_pcm, shake_hands, speech_pcm
from vinga_server.app import create_app
from vinga_server.config import Config
from vinga_server.filler import build_agent_fillers
from vinga_server.protocol import framing
from vinga_server.providers import (
    LlmEvent,
    LlmProvider,
    TextDelta,
    ToolChoice,
    ToolDef,
    Turn,
)

# The session log channel, by name. `logs.py` emits `record.name` as the
# `logger` field of every JSON record, and a collector filters on it, so
# this string is part of the observable output and not an implementation
# detail of where the code lives.
SESSION_LOGGER = "vinga_server.session"

# A voice whose every chunk is 20 ms, which at the 24 kHz output rate is
# a third of an Opus frame: one chunk on its own produces no packet at
# all, which is the case the filler arbitration turns on.
SHORT_MS = 20

# Test-scale filler delay: well over the near-instant mock pipeline,
# well under the stalls scripted below.
FILLER_DELAY_MS = 60.0

UTTERANCE = b"\x00\x00" * 320

STALL_S = 0.5

# A whole sentence the splitter emits on its own (over its four-character
# floor, and with the trailing space that confirms the cut) whose spoken
# form is one 20 ms chunk: audible, and too short to fill a frame. The
# trailing space is deliberate and is why the tail below joins with one.
SHORT_SENTENCE = "Right. "
TAIL_SENTENCE = "And here is the rest."

# The filler clip has to pace over several frames rather than one, or
# there is no window between two of its sends for the reply to feed the
# shared encoder in, which is the race the batching rule exists for. At
# a millisecond per character this phrase is about half a second of
# audio, so its clip is eight or nine frames.
FILLER_PHRASE = "Hmm, let me think about that one for a moment. " * 11


def stuttering_config(delay_ms: float = FILLER_DELAY_MS) -> Config:
    """One agent whose voice speaks in chunks too short to fill an Opus
    frame, with a filler to arbitrate against."""
    return base_config(
        providers={
            "llm": {"mock": {"type": "mock"}},
            "asr": {"mock": {"type": "mock", "text": "hello"}},
            "tts": {
                "tenor": {
                    "type": "mock",
                    "tone_hz": POET_TONE,
                    "ms_per_char": 1,
                    "min_ms": SHORT_MS,
                }
            },
            "vad": {"mock": {"type": "mock"}},
        },
        agents={
            "poet": {
                "prompt": "POET",
                "tts": "tenor",
                "filler": {
                    "enabled": True,
                    "delay_ms": delay_ms,
                    "phrases": [FILLER_PHRASE],
                },
            }
        },
        devices={POET_MAC: ["poet"]},
        default_agent="poet",
    )


class FailingAfterAPause(LlmProvider):
    """A model that goes quiet for longer than the filler's delay and
    then fails, which is what puts a failed reply in front of a clip
    that is still sounding."""

    def __init__(self, gap_s: float) -> None:
        self._gap_s = gap_s

    async def stream(
        self,
        system: str,
        turns: Sequence[Turn],
        tools: Sequence[ToolDef] = (),
        tool_choice: ToolChoice = "auto",
    ) -> AsyncIterator[LlmEvent]:
        await asyncio.sleep(self._gap_s)
        raise RuntimeError("the model gave up")
        yield  # pragma: no cover - never reached, makes this a generator


class PausingLlm(LlmProvider):
    """A model that says one short thing, goes quiet for longer than the
    filler's delay, and then finishes. The gap is what puts a filler in
    flight while a reply chunk is already in the encoder."""

    def __init__(self, head: str, gap_s: float, tail: str) -> None:
        self._head = head
        self._gap_s = gap_s
        self._tail = tail

    async def stream(
        self,
        system: str,
        turns: Sequence[Turn],
        tools: Sequence[ToolDef] = (),
        tool_choice: ToolChoice = "auto",
    ) -> AsyncIterator[LlmEvent]:
        yield TextDelta(self._head)
        await asyncio.sleep(self._gap_s)
        yield TextDelta(self._tail)


class ProbingSocket:
    """Enough websocket to watch a reply go out: the text messages in
    order, the Opus payload of every frame, and, at each frame, whatever
    `probe` answers at that instant."""

    def __init__(
        self, probe: Callable[[], Any] | None = None, log: list[Any] | None = None
    ) -> None:
        self.texts: list[str] = []
        self.frames: list[bytes] = []
        self.text_probes: list[Any] = []
        self.frame_probes: list[Any] = []
        self.closed: tuple[int, str] | None = None
        self._probe = probe
        self._log = log

    async def send_text(self, text: str) -> None:
        self.text_probes.append(None if self._probe is None else self._probe())
        self.texts.append(text)

    async def send_bytes(self, data: bytes) -> None:
        self.frame_probes.append(None if self._probe is None else self._probe())
        payload = framing.unwrap(1, data).payload
        if self._log is not None:
            self._log.append(("send", payload))
        self.frames.append(payload)

    async def close(self, code: int, reason: str) -> None:
        self.closed = (code, reason)


class VanishingSocket(ProbingSocket):
    """A device that goes away on the first frame it is sent, raising
    whatever a caller wants to see swallowed."""

    def __init__(self, error: BaseException) -> None:
        super().__init__()
        self._error = error

    async def send_bytes(self, data: bytes) -> None:
        raise self._error


class RecordingEncoder:
    """The session's Opus encoder with every call written down: what was
    fed in, and what came back out. Wrapping the real one rather than
    faking it keeps the packets real, which is what the content
    assertions below compare against."""

    def __init__(self, inner: Any, log: list[Any]) -> None:
        self._inner = inner
        self._log = log
        self.sample_rate = inner.sample_rate
        self.frame_duration_ms = inner.frame_duration_ms

    def encode(self, pcm: bytes) -> list[bytes]:
        packets = self._inner.encode(pcm)
        self._log.append(("encode", packets))
        return packets

    def flush(self) -> list[bytes]:
        packets = self._inner.flush()
        self._log.append(("flush", packets))
        return packets


async def masked_session(
    config: Config,
    mac: str,
    scripts: dict[str, Any] | None = None,
    probe: Any = None,
    log: list[Any] | None = None,
) -> Any:
    """A session with its cached speech built the way boot builds it,
    both kinds, speaking to a socket that records what it hears."""
    built = await build_agent_fillers(config, built_world(config).agents)
    assert built.clips, "the config under test is supposed to have filler clips"
    session = session_for(
        config, mac, scripts, fillers=built.clips, fallbacks=built.fallbacks
    )
    session.websocket = cast(Any, ProbingSocket(probe, log))
    return session


def test_every_conversation_event_is_logged_on_the_session_channel(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`logs.py` emits `record.name` as the `logger` field of every JSON
    record, and every conversation record has carried
    `vinga_server.session` since the whole session was one module. The
    field is queried in retained logs, so it is output, and a module
    that moves must not quietly rename it. No other test covers this:
    `test_logs.py` formats a synthetic record it names itself."""
    with caplog.at_level("INFO"):
        with TestClient(create_app(config_with_agent(asr_text="what time is it"))) as client:
            with connect(client) as websocket:
                shake_hands(websocket)
                say_one_turn(websocket)

    for name in ("session_open", "heard", "llm_round", "speaking_started", "replied"):
        assert only(caplog, name).name == SESSION_LOGGER, name
    # session_closed is emitted on the way out, after the client has let
    # go of the socket, so it is asserted with the rest of the channel
    # rather than by position.
    assert {record.name for record in events(caplog, "session_closed")} <= {SESSION_LOGGER}


def say_one_turn(websocket, duration_ms: int = 300) -> list[str | dict]:
    """One turn, driven the way the firmware drives it, returning the
    outgoing messages and frames interleaved in the order they arrived:
    a JSON message as its dict, a binary frame as the string "frame"."""
    from vinga_server.audio.opus import OpusEncoder

    websocket.send_text(json.dumps({"type": "listen", "state": "start", "mode": "manual"}))
    send_pcm(websocket, speech_pcm(duration_ms), OpusEncoder())
    websocket.send_text(json.dumps({"type": "listen", "state": "stop"}))
    received: list[str | dict] = []
    while True:
        message = websocket.receive()
        if message.get("text") is None:
            received.append("frame")
            continue
        parsed = json.loads(message["text"])
        received.append(parsed)
        if parsed.get("type") == "tts" and parsed.get("state") == "stop":
            return received


def test_one_turn_has_the_control_message_order_the_firmware_expects() -> None:
    """The transcript first, then the speaking state, then what is about
    to be heard, then the audio, then the end. The firmware leaves its
    speaking state on `tts stop` and (in auto mode) re-arms its
    listening there, so this order is the device contract, not a
    stylistic one."""
    with TestClient(create_app(config_with_agent())) as client:
        with connect(client) as websocket:
            shake_hands(websocket)
            received = say_one_turn(websocket)

    # The device advertised MCP, so its tool discovery runs alongside the
    # turn on the same socket. Those envelopes are the device tool
    # transport rather than part of the turn, and they are deliberately
    # not ordered against it.
    shape = [
        item if isinstance(item, str) else f"{item['type']} {item.get('state', '')}".strip()
        for item in received
        if isinstance(item, str) or item["type"] != "mcp"
    ]
    assert shape[0] == "stt"
    assert shape[1] == "tts start"
    assert shape[2] == "tts sentence_start"
    assert shape[-1] == "tts stop"
    assert set(shape[3:-1]) == {"frame"}


async def test_a_chunk_too_short_to_fill_a_frame_leaves_the_filler_armed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A reply chunk that produced no packet must not touch the filler.
    Today the send path returns on an empty packet list before it
    consults the filler at all, so a sentence of one 20 ms chunk leaves
    an unfired timer alone and the mask still plays when the model goes
    quiet. Were the arbitration reached with nothing to play, the timer
    would be cancelled as "the reply's audio is ready" and the silence
    that follows would go unmasked."""
    session = await masked_session(
        stuttering_config(),
        POET_MAC,
        {"poet": PausingLlm(SHORT_SENTENCE, FILLER_DELAY_MS / 1000 * 3, TAIL_SENTENCE)},
        probe=lambda: bool(
            [r for r in caplog.records if getattr(r, "event", None) == "filler_played"]
        ),
    )
    with caplog.at_level("INFO"):
        await drive_reply(session, UTTERANCE)

    socket = cast(ProbingSocket, session.websocket)
    # The short sentence was announced, and announced before the filler
    # fired, so its chunk really did go through the encoder while the
    # timer was still pending.
    announced = [
        probe
        for text, probe in zip(socket.texts, socket.text_probes, strict=True)
        if '"sentence_start"' in text and SHORT_SENTENCE.strip() in text
    ]
    assert announced == [False]
    played = only(caplog, "filler_played")
    assert played.agent == "poet"
    assert events(caplog, "filler_skipped") == []
    assert spoken(socket) == [SHORT_SENTENCE.strip(), TAIL_SENTENCE]


async def test_the_fillers_first_frame_stamps_and_attributes_speaking_started(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When the mask speaks first, its first frame is the turn's
    `speaking_started`: one per reply, named for the agent that is
    talking, and emitted before the frame reaches the socket rather than
    after it. The stamp is what the barge-in refractory window is
    measured from, so where it is taken is behavior."""
    session = await masked_session(
        stuttering_config(),
        POET_MAC,
        {"poet": StallingLlm([STALL_S])},
        probe=lambda: bool(
            [r for r in caplog.records if getattr(r, "event", None) == "speaking_started"]
        ),
    )
    with caplog.at_level("INFO"):
        await drive_reply(session, UTTERANCE)

    played = only(caplog, "filler_played")
    started = only(caplog, "speaking_started")
    assert started.agent == "poet"
    assert caplog.records.index(played) < caplog.records.index(started)
    socket = cast(ProbingSocket, session.websocket)
    assert socket.frame_probes, "the reply sent no frames at all"
    # Every frame, including the filler's first, went out with the event
    # already emitted: the stamp precedes the pacing and the send.
    assert all(socket.frame_probes)


async def test_a_tool_only_handover_attributes_speaking_started_to_the_new_agent(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The first agent asks for the handover and says nothing at all, so
    the reply's first audio belongs to the second. `speaking_started` is
    emitted where the frames are paced, but it names the agent active
    when it fires, and here that agent is the one that took over."""
    scripts = {
        "poet": ScriptedLlm([[call("switch_agent", agent="tutor")]]),
        "tutor": ScriptedLlm(["Tutor here."]),
    }
    session = session_for(base_config(), BOTH_MAC, cast(Any, scripts))
    session.websocket = cast(Any, ProbingSocket())
    with caplog.at_level("INFO"):
        await drive_reply(session, UTTERANCE)

    handover = only(caplog, "handover")
    assert (handover.from_agent, handover.to_agent) == ("poet", "tutor")
    started = only(caplog, "speaking_started")
    assert started.agent == "tutor"
    assert caplog.records.index(handover) < caplog.records.index(started)


async def test_a_filler_sounding_never_sends_the_replys_packets(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The reply task and the filler share one Opus encoder, and the
    reply feeds it between awaits. So the filler encodes its whole batch
    (clip, resampler tail, encoder flush) in one synchronous expression
    and only then sends: split across awaits, its flush could carry out
    audio the reply fed in the meantime. What it sends is exactly what
    those three calls returned, in that order, with nothing sent in
    between."""
    log: list[Any] = []
    session = await masked_session(
        stuttering_config(),
        POET_MAC,
        {"poet": PausingLlm(SHORT_SENTENCE, FILLER_DELAY_MS / 1000 * 3, TAIL_SENTENCE)},
        log=log,
    )
    # White-box: what is pinned is the feed order into the one Opus
    # encoder a session shares between the reply and the mask, and an
    # encoder's feed order leaves no trace in the frames that come out
    # of it. Wrapping the edge's own encoder is what makes the order
    # observable; building a second one would observe a second order.
    # The encoder is the pacer's, which is what owning the reply audio
    # clock means, and it is reached where it lives.
    session._pacer._encoder = cast(Any, RecordingEncoder(session._pacer._encoder, log))
    with caplog.at_level("INFO"):
        await drive_reply(session, UTTERANCE)

    only(caplog, "filler_played")
    kinds = [kind for kind, _ in log]
    first_flush = kinds.index("flush")
    # The filler encodes clip, resampler tail and encoder flush in one
    # synchronous expression, so nothing has gone out when the flush
    # runs: the batch is complete before the first send.
    assert "send" not in kinds[:first_flush]
    batch = log[first_flush - 2 : first_flush + 1]
    assert [kind for kind, _ in batch] == ["encode", "encode", "flush"]
    packets = [packet for _, produced in batch for packet in produced]
    assert len(packets) > 2, "the clip has to pace over several frames for this to mean anything"

    # The reply feeds the shared encoder while the filler is still
    # pacing, which is the interleaving the batching rule exists for.
    paced_before_the_reply_encoded = 0
    for kind, _ in log[first_flush + 1 :]:
        if kind == "encode":
            break
        paced_before_the_reply_encoded += kind == "send"
    else:  # pragma: no cover - the assertion below reports it
        raise AssertionError("the reply never fed the encoder while the filler was sounding")
    assert 0 < paced_before_the_reply_encoded < len(packets)

    # And what went out is the filler's own packets, in order: the
    # reply's audio stayed in the encoder, where the reply's own send
    # will collect it.
    sent = [payload for kind, payload in log[first_flush + 1 :] if kind == "send"]
    assert sent[: len(packets)] == packets
    assert len(sent) > len(packets), "the reply never got to speak"


async def test_the_shutdown_waits_out_a_reply_that_is_still_generating(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A reply in flight is waited for whether it is speaking or merely
    thinking: the shutdown asks whether the task is done, not whether
    audio is going out. Cutting the socket during the generation would
    strand a device that has been told a reply is coming."""
    loop = asyncio.get_running_loop()
    session = session_for(
        base_config(), POET_MAC, {"poet": cast(Any, StallingLlm([0.3], reply="All done."))}
    )
    socket = ProbingSocket()
    session.websocket = cast(Any, socket)
    with caplog.at_level("INFO"):
        start_reply(session, UTTERANCE)
        await asyncio.sleep(0.05)
        # Generating: nothing has been spoken, and nothing is closed.
        assert not socket.frames
        began = loop.time()
        assert await session.request_shutdown(grace_s=5.0) is True

    assert loop.time() - began >= 0.2
    assert not session.runtime.replying()
    assert socket.frames, "the reply was cut off before it spoke"
    assert socket.closed is not None
    assert spoken(socket) == ["All done."]


@pytest.mark.parametrize(
    "error", [WebSocketDisconnect(1006), RuntimeError("the socket is closed")]
)
async def test_a_reply_ends_quietly_when_the_send_path_raises(
    caplog: pytest.LogCaptureFixture, error: BaseException
) -> None:
    """A device that vanishes mid-reply ends the reply, not the session,
    and says nothing about it. Both halves of the pair are the socket
    speaking: starlette raises `WebSocketDisconnect` when it has seen
    the close and a bare `RuntimeError` for a send that came after one.
    Both must stay quiet, because "reply failed" is what an operator
    reads as a bug in the pipeline, and a device switched off is not
    one.

    What carries the second half changed under #137 and what this test
    watches did not: the edge now translates that `RuntimeError` into
    `DeviceGone` instead of letting the reply body catch it broadly. A
    bug in the encoder or the resampler, which used to go quiet through
    the same catch, is reported now; that is the point of the change,
    and it is pinned in `test_session_reply_failures.py`."""
    session = session_for(base_config(), POET_MAC)
    session.websocket = cast(Any, VanishingSocket(error))
    with caplog.at_level("INFO"):
        await drive_reply(session, UTTERANCE)

    assert "reply failed" not in caplog.text
    # The reply got as far as transcribing before the socket vanished.
    assert only(caplog, "heard").duration_s > 0


@pytest.mark.parametrize(
    "error", [WebSocketDisconnect(1006), RuntimeError("the socket is closed")]
)
async def test_a_filler_ends_quietly_when_the_send_path_raises(
    caplog: pytest.LogCaptureFixture, error: BaseException
) -> None:
    """The same for the mask, and for the same reason twice over: a
    broken mask must never break the reply it masks, nor report itself
    as a failure of the pipeline."""
    fillers = (
        await build_agent_fillers(
            stuttering_config(), built_world(stuttering_config()).agents
        )
    ).clips
    session = session_for(
        stuttering_config(),
        POET_MAC,
        {"poet": cast(Any, StallingLlm([STALL_S]))},
        fillers=fillers,
    )
    session.websocket = cast(Any, VanishingSocket(error))
    with caplog.at_level("INFO"):
        await drive_reply(session, UTTERANCE)

    only(caplog, "filler_played")
    assert "filler playback failed" not in caplog.text
    assert "reply failed" not in caplog.text


@pytest.mark.parametrize("speaking", [True, False])
async def test_a_general_failure_speaks_or_stays_silent_as_configured(
    caplog: pytest.LogCaptureFixture, speaking: bool
) -> None:
    """The two pins above are `DeviceGone` and must stay silent whatever
    the configuration says, because a device that went away has nobody
    left to tell. This is the other reading of the same moment: a bug in
    this process, which is what the general arm is for, and which now
    speaks unless the deployment asked it not to (#384).

    Beside them rather than folded into them, so neither claim can be
    read off the other's evidence.
    """
    config = base_config(
        agents={
            "poet": {
                "prompt": "POET",
                "tts": "tenor",
                "fallback": {"enabled": speaking, "phrase": "Something went wrong."},
            }
        },
        devices={POET_MAC: ["poet"]},
        default_agent="poet",
    )
    fallbacks = (await build_agent_fillers(config, built_world(config).agents)).fallbacks
    session = session_for(config, POET_MAC, fallbacks=fallbacks)
    socket = OrderedSocket()
    session.websocket = cast(Any, socket)

    async def speak(synthesis: Any, resampler: Any, into: list[str]) -> None:
        synthesis.cancel()
        await synthesis.wait_cancelled()
        raise RuntimeError("the encoder is wedged")

    session.runtime._speak = speak  # type: ignore[method-assign]
    with caplog.at_level("INFO"):
        await drive_reply(session, UTTERANCE)

    assert socket.announced() == (["Something went wrong."] if speaking else [])
    assert (socket.frames > 0) is speaking
    # Either way the turn ends the way the firmware needs it to.
    assert socket.closing_stop()


async def test_a_failed_turn_has_the_control_message_order_the_firmware_expects(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The sibling of the successful turn's order pin above, for the
    turn that used to send no sentence at all.

    Same contract, one more message in it: the transcript, the speaking
    state, the sentence the device is about to hear, its audio, and the
    end. The frames are asserted in position rather than filtered away,
    because "the phrase was announced and then played" is the whole
    claim and a filtered list cannot tell it from the reverse.
    """
    config = base_config(
        agents={
            "poet": {
                "prompt": "POET",
                "tts": "tenor",
                "fallback": {"enabled": True, "phrase": "Something went wrong."},
            }
        },
        devices={POET_MAC: ["poet"]},
        default_agent="poet",
    )
    fallbacks = (await build_agent_fillers(config, built_world(config).agents)).fallbacks
    session = session_for(
        config,
        POET_MAC,
        stages={"llm": cast(Any, Unreachable("llm", ConnectionRefusedError("no route")))},
        fallbacks=fallbacks,
    )
    socket = OrderedSocket()
    session.websocket = cast(Any, socket)

    with caplog.at_level("INFO"):
        await drive_reply(session, UTTERANCE)

    shape = socket.shape()
    assert shape[0] == "stt"
    assert shape[1] == "tts start"
    assert shape[2] == "tts sentence_start"
    assert shape[-1] == "tts stop"
    assert set(shape[3:-1]) == {"frame"}


async def test_a_filler_sounding_never_sends_the_fallbacks_packets(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The shared-encoder invariant, for the reply's other clip.

    The failure phrase copies the fire path's batching recipe verbatim,
    and the reason is the same one: the reply task feeds the same Opus
    encoder between its own awaits. This drives the harder ordering,
    where a clip is still sounding when the reply fails: the arm settles
    the mask first, so the two clips never interleave in the encoder,
    and each batch is complete before it is sent.
    """
    log: list[Any] = []
    session = await masked_session(
        stuttering_config(),
        POET_MAC,
        {"poet": FailingAfterAPause(FILLER_DELAY_MS / 1000 * 3)},
        log=log,
    )
    # White-box for the reason the sibling above gives: the feed order
    # into the one Opus encoder a session shares leaves no trace in the
    # frames that come out of it.
    session._pacer._encoder = cast(Any, RecordingEncoder(session._pacer._encoder, log))

    with caplog.at_level("INFO"):
        await drive_reply(session, UTTERANCE)

    only(caplog, "filler_played")
    only(caplog, "reply_fallback")
    kinds = [kind for kind, _ in log]
    # Two batches, each of them clip, resampler tail and encoder flush
    # with nothing sent in between, and the mask's is finished before the
    # phrase's begins.
    flushes = [index for index, kind in enumerate(kinds) if kind == "flush"]
    assert len(flushes) == 2
    for flush in flushes:
        assert [kind for kind, _ in log[flush - 2 : flush + 1]] == [
            "encode",
            "encode",
            "flush",
        ]
    assert "send" in kinds[flushes[0] : flushes[1]]
