"""JSON control messages of the xiaozhi websocket protocol.

Only the message types the server acts on are modelled. Anything else
parses to `UnknownMessage`, so the session can log and move on, mirroring
the firmware, which logs JSON it does not understand and drops it.

Upstream reference: `docs/websocket.md` in 78/xiaozhi-esp32.
"""

import json
from typing import Literal

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

_MESSAGE_TYPES: dict[str, type[BaseModel]] = {
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

    model = _MESSAGE_TYPES.get(message_type)
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


def server_hello(session_id: str, audio_params: AudioParams) -> str:
    """The handshake acknowledgement. xiaozhi-sdk indexes `session_id` and
    every `audio_params` field without defaults, so none are optional."""
    return json.dumps(
        {
            "type": "hello",
            "transport": "websocket",
            "session_id": session_id,
            "audio_params": audio_params.model_dump(),
        }
    )


def stt_message(session_id: str, text: str) -> str:
    """The transcription of what the user said, which the device shows
    on its display while the reply is being prepared."""
    return json.dumps({"session_id": session_id, "type": "stt", "text": text})


def tts_message(
    session_id: str,
    state: Literal["start", "stop", "sentence_start"],
    text: str | None = None,
) -> str:
    """A TTS state change; `sentence_start` carries the text the device
    shows on its display."""
    message: dict[str, object] = {"session_id": session_id, "type": "tts", "state": state}
    if text is not None:
        message["text"] = text
    return json.dumps(message)
