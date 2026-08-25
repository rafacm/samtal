"""The JSON control messages, parsed the way the firmware sends them.

The message shapes here come from `docs/websocket.md` in 78/xiaozhi-esp32
and from what xiaozhi-sdk's client actually emits.
"""

import json

import pytest

from vinga_server.protocol.messages import (
    AbortMessage,
    AudioParams,
    DeviceHello,
    ListenMessage,
    McpMessage,
    ProtocolError,
    UnknownMessage,
    parse_message,
    server_hello,
    stt_message,
    tts_message,
)

FIRMWARE_HELLO = {
    "type": "hello",
    "version": 1,
    "features": {"mcp": True, "aec": True},
    "transport": "websocket",
    "audio_params": {
        "format": "opus",
        "sample_rate": 16000,
        "channels": 1,
        "frame_duration": 60,
    },
}


def test_the_firmware_hello_parses() -> None:
    message = parse_message(json.dumps(FIRMWARE_HELLO))
    assert isinstance(message, DeviceHello)
    assert message.version == 1
    assert message.transport == "websocket"
    assert message.features["mcp"] is True
    assert message.audio_params.sample_rate == 16000
    assert message.audio_params.frame_duration == 60


def test_a_minimal_hello_fills_in_the_protocol_defaults() -> None:
    message = parse_message('{"type": "hello"}')
    assert isinstance(message, DeviceHello)
    assert message.version == 1
    assert message.audio_params == AudioParams()


def test_unmodelled_hello_fields_are_ignored() -> None:
    hello = dict(FIRMWARE_HELLO, text_font={"bundle": "noto-v1"})
    assert isinstance(parse_message(json.dumps(hello)), DeviceHello)


@pytest.mark.parametrize("state", ["start", "stop", "detect"])
def test_listen_states_parse(state: str) -> None:
    message = parse_message(json.dumps({"session_id": "s", "type": "listen", "state": state}))
    assert isinstance(message, ListenMessage)
    assert message.state == state


@pytest.mark.parametrize("mode", ["auto", "manual", "realtime"])
def test_a_listen_start_carries_its_mode(mode: str) -> None:
    # The mode is not decoration: realtime is the device saying it will
    # stream continuously, which is what keeps a session listening.
    message = parse_message(
        json.dumps({"session_id": "s", "type": "listen", "state": "start", "mode": mode})
    )
    assert isinstance(message, ListenMessage)
    assert message.mode == mode


def test_a_wake_word_report_carries_its_text() -> None:
    message = parse_message(
        '{"session_id": "s", "type": "listen", "state": "detect", "text": "Hi ESP"}'
    )
    assert isinstance(message, ListenMessage)
    assert message.text == "Hi ESP"


def test_an_unheard_of_listen_state_is_malformed() -> None:
    with pytest.raises(ProtocolError, match="listen"):
        parse_message('{"type": "listen", "state": "pause"}')


def test_abort_parses_with_and_without_a_reason() -> None:
    with_reason = parse_message('{"type": "abort", "reason": "wake_word_detected"}')
    assert isinstance(with_reason, AbortMessage)
    assert with_reason.reason == "wake_word_detected"
    bare = parse_message('{"type": "abort"}')
    assert isinstance(bare, AbortMessage)
    assert bare.reason is None


def test_a_malformed_message_names_the_field_and_the_rule_not_the_value() -> None:
    """The refusal is logged verbatim by the session edge, so what it
    says is a retained surface. A device controls the value it sends and
    nothing else about this sentence: the type is one of the four
    modelled spellings, the field name comes from the model, and the
    rule is pydantic's own fixed slug. The rejected value appears
    nowhere, and neither does the `ValidationError` that carries it in
    its rendering, which is why the chain behind the refusal is empty."""
    planted = "sk-live-4e91c7a2-never-a-real-credential"
    with pytest.raises(ProtocolError) as caught:
        parse_message(json.dumps({"type": "abort", "reason": [planted]}))

    assert str(caught.value) == 'malformed "abort" message: reason (string_type)'
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_a_malformed_nested_value_is_named_by_the_field_that_holds_it() -> None:
    """A `loc` reaches into nested values, and a key inside one can be
    something the far side wrote. Only the outermost name is used, and
    only when the model declares it."""
    with pytest.raises(ProtocolError) as caught:
        parse_message(json.dumps({"type": "hello", "audio_params": {"sample_rate": "fast"}}))

    assert str(caught.value) == 'malformed "hello" message: audio_params (int_parsing)'


def test_mcp_messages_keep_their_jsonrpc_payload() -> None:
    message = parse_message(
        '{"type": "mcp", "payload": {"jsonrpc": "2.0", "id": 1, "result": {}}}'
    )
    assert isinstance(message, McpMessage)
    assert message.payload["jsonrpc"] == "2.0"


def test_an_unmodelled_type_comes_back_whole_for_logging() -> None:
    message = parse_message('{"type": "goodbye", "why": "battery"}')
    assert isinstance(message, UnknownMessage)
    assert message.type == "goodbye"
    assert message.data["why"] == "battery"


@pytest.mark.parametrize(
    "payload",
    ["not json", "[1, 2]", '"hello"', "{}", '{"type": 7}', '{"type": ""}'],
)
def test_unusable_payloads_raise_a_protocol_error(payload: str) -> None:
    with pytest.raises(ProtocolError):
        parse_message(payload)


def test_the_server_hello_carries_everything_the_sdk_indexes() -> None:
    # xiaozhi-sdk reads session_id, sample_rate, channels, and
    # frame_duration without defaults; a missing one crashes the client.
    reply = json.loads(server_hello("abc123", AudioParams()))
    assert reply["type"] == "hello"
    assert reply["transport"] == "websocket"
    assert reply["session_id"] == "abc123"
    assert reply["audio_params"] == {
        "format": "opus",
        "sample_rate": 16000,
        "channels": 1,
        "frame_duration": 60,
    }


def test_tts_messages_only_carry_text_when_there_is_some() -> None:
    start = json.loads(tts_message("s", "start"))
    assert start == {"session_id": "s", "type": "tts", "state": "start"}
    sentence = json.loads(tts_message("s", "sentence_start", text="Hej"))
    assert sentence["text"] == "Hej"


# The three builders, transcribed byte for byte
#
# The pin that comes before the reshape. `protocol/messages.py` models
# the device-to-server half and builds the server-to-device half by hand,
# and #248 derives those builders from models of their own so the models
# and the wire cannot come to disagree. What every device in the field
# reads is the bytes, so what a derivation may not change is the bytes:
# not the key order, not the separators, not the escaping of a character
# outside ASCII.
#
# So this is a transcription rather than a description. Each string below
# was read off the builders as they stood before the models arrived, and
# the case asserts equality with the whole string rather than membership
# of a parsed object, because a parse is exactly the comparison that
# would not have noticed.

HELLO_BYTES = (
    '{"type": "hello", "transport": "websocket", "session_id": "abc123", '
    '"audio_params": {"format": "opus", "sample_rate": 16000, "channels": 1, '
    '"frame_duration": 60}}'
)

# The same builder with nothing left at a default, so the pin is about
# the layout rather than about the defaults happening to line up.
HELLO_BYTES_UNUSUAL = (
    '{"type": "hello", "transport": "websocket", "session_id": "s", '
    '"audio_params": {"format": "pcm", "sample_rate": 24000, "channels": 2, '
    '"frame_duration": 20}}'
)

STT_BYTES = '{"session_id": "s", "type": "stt", "text": "hi"}'

# A transcript is whatever was said, and what was said is not always
# ASCII. `json.dumps` escapes it and a byte-preserving derivation has to
# escape it the same way, which is the half of this pin a parsed
# comparison could not hold.
STT_BYTES_ESCAPED = '{"session_id": "s", "type": "stt", "text": "det \\u00e4r bra"}'

TTS_START_BYTES = '{"session_id": "s", "type": "tts", "state": "start"}'

TTS_STOP_BYTES = '{"session_id": "s", "type": "tts", "state": "stop"}'

TTS_SENTENCE_BYTES = (
    '{"session_id": "s", "type": "tts", "state": "sentence_start", "text": "Hej"}'
)

# The empty string is a sentence's text as much as any other string is,
# and it is the one a builder that dropped falsy values would lose.
TTS_EMPTY_SENTENCE_BYTES = (
    '{"session_id": "s", "type": "tts", "state": "sentence_start", "text": ""}'
)


def test_the_server_hello_is_built_byte_for_byte_as_it_always_was() -> None:
    assert server_hello("abc123", AudioParams()) == HELLO_BYTES

    unusual = AudioParams(format="pcm", sample_rate=24000, channels=2, frame_duration=20)
    assert server_hello("s", unusual) == HELLO_BYTES_UNUSUAL


def test_the_transcript_is_built_byte_for_byte_as_it_always_was() -> None:
    assert stt_message("s", "hi") == STT_BYTES
    assert stt_message("s", "det är bra") == STT_BYTES_ESCAPED


def test_every_tts_state_is_built_byte_for_byte_as_it_always_was() -> None:
    assert tts_message("s", "start") == TTS_START_BYTES
    assert tts_message("s", "stop") == TTS_STOP_BYTES
    assert tts_message("s", "sentence_start", text="Hej") == TTS_SENTENCE_BYTES
    assert tts_message("s", "sentence_start", text="") == TTS_EMPTY_SENTENCE_BYTES


def test_a_text_nobody_gave_is_absent_rather_than_null() -> None:
    """The one shape rule the transcriptions above imply and no single
    string states: a `start` and a `stop` carry no `text` key at all,
    rather than a key whose value is null. The firmware reads the field
    where it expects one, and a null is not a string.
    """
    assert "text" not in json.loads(tts_message("s", "start"))
    assert "text" not in json.loads(tts_message("s", "stop"))
    assert json.loads(tts_message("s", "sentence_start", text=""))["text"] == ""
