"""The JSON control messages, parsed the way the firmware sends them.

The message shapes here come from `docs/websocket.md` in 78/xiaozhi-esp32
and from what xiaozhi-sdk's client actually emits.
"""

import json

import pytest
from pydantic import ValidationError

from vinga_server.protocol.messages import (
    SERVER_MESSAGE_TYPES,
    AbortMessage,
    AudioParams,
    DeviceHello,
    ListenMessage,
    McpMessage,
    ProtocolError,
    ServerHello,
    SttMessage,
    TtsMessage,
    UnknownMessage,
    declared_values,
    parse_message,
    parse_server_message,
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


# The other direction, read rather than written
#
# The half a client parses. Everything here is the mirror of the cases
# above it: a well-formed message reaches its model, a type nothing
# models is carried rather than refused, and a malformed one of a
# modelled type is refused by a sentence that names no value.


def test_every_message_the_server_sends_parses_into_its_own_model() -> None:
    """Built by the builders and read by the parser, which is the round
    trip that says the two halves describe one wire."""
    hello = parse_server_message(server_hello("s1", AudioParams()))
    assert isinstance(hello, ServerHello)
    assert hello.session_id == "s1"
    assert hello.audio_params.sample_rate == 16000

    heard = parse_server_message(stt_message("s1", "hello there"))
    assert isinstance(heard, SttMessage)
    assert heard.text == "hello there"

    for state in ("start", "stop"):
        spoken = parse_server_message(tts_message("s1", state))
        assert isinstance(spoken, TtsMessage)
        assert spoken.state == state
        assert spoken.text is None

    sentence = parse_server_message(tts_message("s1", "sentence_start", text="Hej"))
    assert isinstance(sentence, TtsMessage) and sentence.text == "Hej"


def test_a_parsed_server_message_cannot_be_edited() -> None:
    """Frozen, because a parsed message is a record of what arrived and
    nothing downstream has any business rewriting it."""
    hello = parse_server_message(server_hello("s1", AudioParams()))

    with pytest.raises(ValidationError):
        hello.session_id = "somebody else's session"


def test_a_newer_server_stays_readable() -> None:
    """`extra="ignore"`, the same tolerance the device-side models have:
    a field this client does not know is not a reason to refuse a
    message whose every known field is right."""
    parsed = parse_server_message(
        json.dumps({"type": "stt", "session_id": "s", "text": "hi", "confidence": 0.9})
    )

    assert isinstance(parsed, SttMessage) and parsed.text == "hi"


def test_a_type_this_client_does_not_model_is_carried_rather_than_refused() -> None:
    """The firmware logs JSON it does not understand and moves on, and
    so does this: an `llm` message from a server that grew one is a fact
    to report, not a session to end."""
    parsed = parse_server_message(json.dumps({"type": "llm", "emotion": "happy"}))

    assert isinstance(parsed, UnknownMessage) and parsed.type == "llm"


@pytest.mark.parametrize(
    "payload",
    [
        "not json at all",
        "[]",
        '"a string"',
        "{}",
        '{"type": ""}',
        '{"type": 7}',
    ],
)
def test_a_payload_that_is_not_a_typed_object_is_refused(payload: str) -> None:
    with pytest.raises(ProtocolError):
        parse_server_message(payload)


# The sentinel is shaped so that finding it anywhere can only mean the
# far side's own bytes were quoted back.
SERVER_PLANTED = "sk-server-4f19c2-never-a-real-credential"


@pytest.mark.parametrize(
    "payload",
    [
        # A session id that is not a string, carrying the planted value
        # inside the structure pydantic would render as `input_value=`.
        json.dumps({"type": "hello", "session_id": [SERVER_PLANTED]}),
        # A tts state the protocol does not declare.
        json.dumps({"type": "tts", "state": SERVER_PLANTED}),
        # A transcript that is not text.
        json.dumps({"type": "stt", "text": {"was": SERVER_PLANTED}}),
        # And a hello with no session id at all, which is the one field a
        # client cannot go on without.
        json.dumps({"type": "hello", "transport": SERVER_PLANTED}),
    ],
)
def test_a_malformed_server_message_names_no_value_it_was_given(payload: str) -> None:
    """The reason this parser exists rather than a `data.get(...)` chain.

    Pydantic renders a `ValidationError` with `input_value=` in it, so a
    server that put a credential where a `session_id` belongs would put
    it into the refusal. What comes out names the type, the field and
    pydantic's own fixed rule slug, and nothing else: not on the
    sentence, not in the exception's `args`, and not on a chain behind
    it.
    """
    with pytest.raises(ProtocolError) as raised:
        parse_server_message(payload)

    assert SERVER_PLANTED not in str(raised.value)
    assert SERVER_PLANTED not in repr(raised.value.args)
    assert raised.value.__cause__ is None and raised.value.__context__ is None


def test_json_that_will_not_parse_is_named_by_its_class() -> None:
    """The one refusal `parse_message` still writes differently, and
    deliberately so. A device's edge is inside this deployment and its
    decoder's message is this side's own; what a SERVER wrote is far-side
    bytes, and `json`'s message quotes the document around the character
    it stopped at.
    """
    with pytest.raises(ProtocolError) as raised:
        parse_server_message(f'{{"type": "stt", "text": "{SERVER_PLANTED}"')

    assert SERVER_PLANTED not in str(raised.value)
    assert SERVER_PLANTED not in repr(raised.value.args)
    assert raised.value.__cause__ is None and raised.value.__context__ is None


def test_the_two_inventories_name_the_models_that_parse_them() -> None:
    """The inventories are the models, not a list beside them, which is
    what the capability table reads in both directions."""
    assert set(SERVER_MESSAGE_TYPES) == {"hello", "stt", "tts", "mcp"}
    for message_type, model in SERVER_MESSAGE_TYPES.items():
        parsed = parse_server_message(json.dumps(_least(message_type)))
        assert isinstance(parsed, model), message_type


def _least(message_type: str) -> dict[str, object]:
    """The smallest well-formed message of one server-sent type."""
    return {
        "hello": {"type": "hello", "session_id": "s"},
        "stt": {"type": "stt", "text": ""},
        "tts": {"type": "tts", "state": "stop"},
        "mcp": {"type": "mcp"},
    }[message_type]


def test_the_declared_values_are_read_off_the_models() -> None:
    """The one home of what a facet can be. A second list of TTS states
    written beside the models is two structures that must agree, which
    is a bug pending; this is the function that keeps there being one.
    """
    assert declared_values(TtsMessage, "state") == ("start", "stop", "sentence_start")
    assert declared_values(ListenMessage, "state") == ("start", "stop", "detect")
    # Flattened out of the union an optional field is, so the `None`
    # beside the members is not one of them.
    assert declared_values(ListenMessage, "mode") == ("auto", "manual", "realtime")
    # A field that is not a closed set, and a field that is not there.
    assert declared_values(SttMessage, "text") == ()
    assert declared_values(SttMessage, "state") == ()
