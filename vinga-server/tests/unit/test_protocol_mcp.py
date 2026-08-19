"""The device MCP channel's wire layer, against the shapes in upstream's
`docs/mcp-protocol.md`."""

import json

import pytest

from vinga_server.protocol import mcp


def test_the_envelope_is_an_mcp_protocol_message() -> None:
    text = mcp.envelope("s-1", {"jsonrpc": "2.0", "method": "ping"})
    assert json.loads(text) == {
        "session_id": "s-1",
        "type": "mcp",
        "payload": {"jsonrpc": "2.0", "method": "ping"},
    }


def test_initialize_carries_an_empty_vision_stanza() -> None:
    # xiaozhi-sdk reads params.capabilities.vision.url and .token without
    # checking they exist; omitting them raises a KeyError on the device
    # side and the handshake never completes.
    payload = mcp.initialize_request(1, "vinga-server", "0.1.0")
    assert payload["method"] == "initialize"
    assert payload["id"] == 1
    assert payload["params"]["capabilities"]["vision"] == {"url": "", "token": ""}
    assert payload["params"]["protocolVersion"] == mcp.PROTOCOL_VERSION
    assert payload["params"]["clientInfo"] == {"name": "vinga-server", "version": "0.1.0"}


def test_the_initialized_notification_has_no_id() -> None:
    payload = mcp.initialized_notification()
    assert payload == {"jsonrpc": "2.0", "method": "notifications/initialized"}
    assert "id" not in payload


def test_tools_list_asks_for_a_page_without_user_tools() -> None:
    first = mcp.tools_list_request(2)
    assert first["params"] == {"cursor": "", "withUserTools": False}
    assert mcp.tools_list_request(3, "page-2")["params"]["cursor"] == "page-2"


def test_tools_call_names_the_tool_and_its_arguments() -> None:
    payload = mcp.tools_call_request(4, "self.audio_speaker.set_volume", {"volume": 50})
    assert payload["method"] == "tools/call"
    assert payload["params"] == {
        "name": "self.audio_speaker.set_volume",
        "arguments": {"volume": 50},
    }
    assert payload["id"] == 4


def test_a_successful_response_carries_its_result() -> None:
    response = mcp.parse_response({"jsonrpc": "2.0", "id": 3, "result": {"isError": False}})
    assert response is not None
    assert response.id == 3
    assert not response.failed
    assert response.result == {"isError": False}


def test_an_error_response_reads_as_a_message() -> None:
    response = mcp.parse_response(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "error": {"code": -32601, "message": "Unknown tool: self.nope"},
        }
    )
    assert response is not None
    assert response.failed
    assert response.error is not None
    assert "Unknown tool" in response.error
    assert "-32601" in response.error


def test_a_device_notification_is_not_a_response() -> None:
    # Device-initiated messages carry a method and no id; they are logged
    # and never replied to, so the parser tells them apart rather than
    # failing on them.
    assert (
        mcp.parse_response(
            {"jsonrpc": "2.0", "method": "notifications/state_changed", "params": {}}
        )
        is None
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"jsonrpc": "2.0", "result": {}},
        {"jsonrpc": "2.0", "id": "3", "result": {}},
        {"jsonrpc": "2.0", "id": 3},
    ],
)
def test_an_unmatched_response_is_an_error(payload: dict) -> None:
    with pytest.raises(mcp.McpProtocolError):
        mcp.parse_response(payload)


def test_a_tools_page_reads_its_tools_and_its_cursor() -> None:
    tools, cursor = mcp.parse_tools_page(
        {
            "tools": [
                {
                    "name": "self.get_device_status",
                    "description": "device state",
                    "inputSchema": {"type": "object", "properties": {}},
                },
                {"name": "self.audio_speaker.set_volume"},
            ],
            "nextCursor": "page-2",
        }
    )
    assert [tool.name for tool in tools] == [
        "self.get_device_status",
        "self.audio_speaker.set_volume",
    ]
    assert tools[0].description == "device state"
    # A tool listed without a schema still gets a usable one.
    assert tools[1].input_schema == {"type": "object"}
    assert cursor == "page-2"


def test_the_last_tools_page_has_no_cursor() -> None:
    tools, cursor = mcp.parse_tools_page({"tools": []})
    assert tools == []
    assert cursor == ""


def test_an_unusable_tool_entry_does_not_cost_the_page() -> None:
    tools, _ = mcp.parse_tools_page(
        {"tools": ["not an object", {"description": "nameless"}, {"name": "self.ok"}]}
    )
    assert [tool.name for tool in tools] == ["self.ok"]


def test_a_tool_result_joins_its_text_content() -> None:
    text, is_error = mcp.parse_tool_result(
        {"content": [{"type": "text", "text": "true"}], "isError": False}
    )
    assert (text, is_error) == ("true", False)


def test_a_failing_tool_result_keeps_its_error_flag() -> None:
    text, is_error = mcp.parse_tool_result(
        {"content": [{"type": "text", "text": "no such volume"}], "isError": True}
    )
    assert (text, is_error) == ("no such volume", True)


def test_content_a_voice_assistant_cannot_speak_is_named() -> None:
    text, _ = mcp.parse_tool_result(
        {"content": [{"type": "image", "data": "..."}, {"type": "text", "text": "and this"}]}
    )
    assert text == "[unsupported image content]\nand this"
