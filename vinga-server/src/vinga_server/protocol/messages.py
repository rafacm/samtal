"""JSON control messages of the xiaozhi websocket protocol, both
directions.

Only the message types either side acts on are modelled. Anything else
parses to `UnknownMessage`, so a reader can log and move on, mirroring
the firmware, which logs JSON it does not understand and drops it.

The two halves are symmetric since #248, and the asymmetry that came
before is worth recording because it explains the shape. The
device-to-server half was modelled from the start; the server-to-device
half was three `json.dumps` calls, which is fine while the only reader is
the server, because a server never parses what it just wrote. `vinga
simulator run` is the first thing in this repository that READS the
server's half, and a client hand-rolling `data.get("type") == "tts"` would
be a second encoding of the wire in the one module whose whole
justification is that it holds none.

So both halves have models, both have a parser, and the three builders
are derived from the models rather than written beside them, which is
what stops the models and the wire from disagreeing.

Upstream reference: `docs/websocket.md` in 78/xiaozhi-esp32.
"""

import json
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class ProtocolError(ValueError):
    """A payload that cannot be understood as a protocol message."""


class AudioParams(BaseModel):
    """The audio stream parameters each side announces in its hello."""

    model_config = ConfigDict(extra="ignore")

    format: str = "opus"
    sample_rate: int = 16000
    channels: int = 1
    frame_duration: int = 60  # milliseconds per Opus packet


class DeviceHello(BaseModel):
    """The first message a device sends after the upgrade."""

    model_config = ConfigDict(extra="ignore")

    type: Literal["hello"]
    version: int = 1
    transport: str = "websocket"
    features: dict[str, object] = Field(default_factory=dict)
    audio_params: AudioParams = Field(default_factory=AudioParams)


class ListenMessage(BaseModel):
    """The device starting or stopping microphone capture, or reporting a
    wake word (`state: detect`, with the word in `text`)."""

    model_config = ConfigDict(extra="ignore")

    type: Literal["listen"]
    session_id: str = ""
    state: Literal["start", "stop", "detect"]
    mode: Literal["auto", "manual", "realtime"] | None = None
    text: str | None = None


class AbortMessage(BaseModel):
    """The device interrupting whatever the server is saying."""

    model_config = ConfigDict(extra="ignore")

    type: Literal["abort"]
    session_id: str = ""
    reason: str | None = None


class McpMessage(BaseModel):
    """A JSON-RPC 2.0 envelope for the device's own MCP tools. Carried
    here so it parses cleanly; the server acts on it from M6."""

    model_config = ConfigDict(extra="ignore")

    type: Literal["mcp"]
    session_id: str = ""
    payload: dict[str, object] = Field(default_factory=dict)


class UnknownMessage(BaseModel):
    """A well-formed message of a type the server does not model."""

    type: str
    data: dict[str, object]


DeviceMessage = DeviceHello | ListenMessage | AbortMessage | McpMessage | UnknownMessage

# Which model each device-to-server type parses as, and the public
# inventory of that half of the wire.
#
# Public since #248, because a second reader arrived. `vinga simulator`
# states what it supports and what it does not, and it states it at
# (type, state, mode) granularity, off these models' own `Literal`
# members: a type-level claim would have called `listen` supported and
# published a claim two thirds false, since `start` and `stop` in
# `manual` sit beside `detect`, `auto` and `realtime`, which a simulator
# cannot do at all. A private name reached from another module is a fact
# with no home.
MESSAGE_TYPES: dict[str, type[BaseModel]] = {
    "hello": DeviceHello,
    "listen": ListenMessage,
    "abort": AbortMessage,
    "mcp": McpMessage,
}


def _refusal(message_type: str, model: type[BaseModel], exc: ValidationError) -> str:
    """What a malformed message of a modelled type may be called.

    Pydantic renders a `ValidationError` with `input_value=` in it, so a
    device that aborts with `{"reason": ["sk-live-..."]}` puts its own
    bytes into this sentence, and the session's edge logs the sentence
    verbatim on a surface the no-leak contract governs (#185, the
    content-and-telemetry ADR). The sentence therefore names the message
    type, where the message broke, and which rule it broke, and nothing
    that arrived.

    Where it broke is read from the model's own field names rather than
    from the error's `loc`, because a `loc` inside a nested value can
    hold a key the far side wrote; a location that is not one of this
    model's fields is called `shape`. Which rule is pydantic's error
    `type`, a fixed slug (`string_type`, `literal_error`, `missing`)
    with no value in it. Both halves are this side's vocabulary, which
    is what makes the sentence safe to keep.
    """
    fields = set(model.model_fields)
    faults = sorted(
        {
            (
                str(error["loc"][0])
                if error["loc"] and str(error["loc"][0]) in fields
                else "shape",
                error["type"],
            )
            for error in exc.errors()
        }
    )
    named = ", ".join(f"{where} ({rule})" for where, rule in faults)
    return f'malformed "{message_type}" message: {named or "shape"}'


def parse_message(text: str | bytes) -> DeviceMessage:
    """Parse one text frame into a message, raising ProtocolError when it
    is not JSON, not an object, untyped, or malformed for a known type."""
    try:
        data = json.loads(text)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ProtocolError(f"not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ProtocolError("a protocol message must be a JSON object")

    message_type = data.get("type")
    if not isinstance(message_type, str) or not message_type:
        raise ProtocolError('a protocol message must carry a string "type"')

    model = MESSAGE_TYPES.get(message_type)
    if model is None:
        return UnknownMessage(type=message_type, data=data)
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        refusal = _refusal(message_type, model, exc)
    # Raised out here rather than in the arm, the way the device edge
    # raises `DeviceGone` (`device/session.py`): inside it, the
    # `ValidationError` becomes this one's `__context__` even under
    # `from None`, and anything that walks the chain of what it catches
    # has the rejected value back. Nothing about which field validator
    # objected is diagnosable from the exception anyway, since the
    # sentence above is what this boundary exists to produce.
    raise ProtocolError(refusal)


# What the server sends
#
# Frozen, because a parsed message is a record of what arrived and
# nothing downstream has any business editing it; `extra="ignore"` like
# their device-side siblings, so a newer server stays readable rather
# than becoming a refusal.
#
# The field ORDER is part of each model. A builder below dumps the model
# and the dump follows the declaration, so these four lines are the wire
# layout every device in the field already reads.


class ServerHello(BaseModel):
    """The handshake acknowledgement. xiaozhi-sdk indexes `session_id`
    and every `audio_params` field without defaults, so none are
    optional, and a client that did not get a session id has nothing to
    put in the messages it sends next."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    type: Literal["hello"] = "hello"
    transport: str = "websocket"
    session_id: str
    audio_params: AudioParams = Field(default_factory=AudioParams)


class SttMessage(BaseModel):
    """The transcription of what the user said, which the device shows on
    its display while the reply is being prepared."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    session_id: str = ""
    type: Literal["stt"] = "stt"
    text: str


class TtsMessage(BaseModel):
    """A TTS state change; `sentence_start` carries the text the device
    shows on its display.

    `text` is `None` for a state that has none, and the builder drops it
    rather than sending a null: the firmware reads the field where it
    expects a string, and a null is not one.
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    session_id: str = ""
    type: Literal["tts"] = "tts"
    state: Literal["start", "stop", "sentence_start"]
    text: str | None = None


# Which model each server-to-device type parses as, and the public
# inventory of that half of the wire.
#
# The read side is closed exactly as the send side is, and both are read
# the same way: `vinga simulator` states what it supports at (type,
# state, mode) granularity off these models' own `Literal` members, so a
# fourth TTS state added here appears in that help as an unclassified row
# rather than as a silently supported one.
#
# The `mcp` envelope is `McpMessage` in both directions because it IS one
# structure: a JSON-RPC envelope with a session id, carried unchanged
# whichever way it travels. A second model of it would be the pending bug
# the design guide names.
SERVER_MESSAGE_TYPES: dict[str, type[BaseModel]] = {
    "hello": ServerHello,
    "stt": SttMessage,
    "tts": TtsMessage,
    "mcp": McpMessage,
}

ServerMessage = ServerHello | SttMessage | TtsMessage | McpMessage | UnknownMessage


def parse_server_message(text: str | bytes) -> ServerMessage:
    """Parse one text frame the server sent, with the boundary discipline
    `parse_message` has and for the same reason.

    Pydantic renders a `ValidationError` with `input_value=` in it, so a
    server that put a credential where a `session_id` belongs would
    otherwise put it into this sentence, and a client's sentences reach a
    terminal and a log record alike. What comes out names the message
    type, where it broke and which rule it broke, and nothing that
    arrived.
    """
    read: list[object] = []
    unreadable = ""
    try:
        read.append(json.loads(text))
    except (ValueError, UnicodeDecodeError) as exc:
        # By its class, not by its text: `json`'s own message quotes the
        # document around the character it stopped at, and what a SERVER
        # wrote is far-side bytes. Recorded here and raised below, so
        # nothing walking a chain finds the decoder's message behind it.
        unreadable = f"not valid JSON ({type(exc).__name__})"
    if unreadable:
        raise ProtocolError(unreadable)
    data = read[0]
    if not isinstance(data, dict):
        raise ProtocolError("a protocol message must be a JSON object")

    message_type = data.get("type")
    if not isinstance(message_type, str) or not message_type:
        raise ProtocolError('a protocol message must carry a string "type"')

    model = SERVER_MESSAGE_TYPES.get(message_type)
    if model is None:
        return UnknownMessage(type=message_type, data=data)
    try:
        parsed = model.model_validate(data)
    except ValidationError as exc:
        refusal = _refusal(message_type, model, exc)
    else:
        # Narrowed here rather than at the return, because
        # `model_validate` on a variable of type `type[BaseModel]` says
        # only that a model came back.
        assert isinstance(parsed, ServerHello | SttMessage | TtsMessage | McpMessage)
        return parsed
    # Raised outside the handler, for the reason `parse_message` gives:
    # inside it the `ValidationError` becomes this one's `__context__`
    # even under `from None`, and anything walking that chain has the
    # rejected value back.
    raise ProtocolError(refusal)


# The three builders, derived from the models above
#
# Derived rather than written beside them, which is what stops a field
# added to a model from being a field the wire never carries. What may
# not change is the bytes: `tests/unit/test_protocol_messages.py`
# transcribes all three and compares whole strings, because every device
# in the field reads them.
#
# `json.dumps` over the dump rather than `model_dump_json`, and the
# difference is exactly those bytes. Pydantic's own serializer writes
# compact separators and emits non-ASCII raw, so it would rewrite every
# one of these messages while changing nothing about what they mean. The
# models are still the single home of the shape, which is the property
# the derivation was for; the encoder is the stdlib's, which is the one
# these messages have always been written by.


def built(message: BaseModel) -> str:
    """One control message as its bytes.

    Public because both directions build one. The three builders below
    are the server's, and `vinga simulator` builds a device hello and a
    `listen` off the device-side models through this same function, so
    there is one place that decides what a control message looks like on
    the wire.

    `exclude_none` is what drops a `text` nobody gave rather than sending
    a null in its place: the firmware reads the field where it expects a
    string, and a null is not one.
    """
    return json.dumps(message.model_dump(exclude_none=True))


def server_hello(session_id: str, audio_params: AudioParams) -> str:
    return built(ServerHello(session_id=session_id, audio_params=audio_params))


def stt_message(session_id: str, text: str) -> str:
    return built(SttMessage(session_id=session_id, text=text))


def tts_message(
    session_id: str,
    state: Literal["start", "stop", "sentence_start"],
    text: str | None = None,
) -> str:
    return built(TtsMessage(session_id=session_id, state=state, text=text))


# The state and mode inventory, off the models themselves
#
# One home for "what values does this facet of this message type
# declare", read by both directions of the capability table. The
# alternative is a list of states written beside the models, which is two
# structures that must agree.


def declared_values(model: type[BaseModel], field: str) -> tuple[str, ...]:
    """The `Literal` members one field of one message model declares, or
    `()` where the model has no such field or the field is not a closed
    set.

    Flattened out of the union an optional field is, so
    `Literal[...] | None` answers the members and not the `None` beside
    them; whether an absent value is itself a case is the caller's
    question, and `ListenMessage.mode` is the field that makes it one.
    """
    if field not in model.model_fields:
        return ()
    return _members(model.model_fields[field].annotation)


def _members(annotation: object) -> tuple[str, ...]:
    found: list[str] = []
    for member in get_args(annotation) or ():
        if isinstance(member, str):
            found.append(member)
            continue
        found.extend(_members(member))
    return tuple(found)
