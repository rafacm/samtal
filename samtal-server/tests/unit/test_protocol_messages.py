"""The JSON control messages, parsed the way the firmware sends them.

The message shapes here come from `docs/websocket.md` in 78/xiaozhi-esp32
and from what xiaozhi-sdk's client actually emits.
"""

import json

import pytest

from samtal_server.protocol.messages import (
    AbortMessage,
    AudioParams,
    DeviceHello,
    ListenMessage,
    McpMessage,
    ProtocolError,
    UnknownMessage,
    parse_message,
    server_hello,
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
