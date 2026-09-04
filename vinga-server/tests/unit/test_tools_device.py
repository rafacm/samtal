"""The device tool client, driven the way a device drives it.

The far side here is a small scripted stand-in for the firmware: it
receives the payloads the client sends and answers them, so the
handshake, the pagination, the sanitized names, and the calls are all
exercised without a board or a socket.
"""

import asyncio

import pytest

from tests.support.device_tools import STATUS, VOLUME, FakeDevice


async def test_the_handshake_lists_the_devices_tools_under_safe_names() -> None:
    device = FakeDevice([{"tools": [VOLUME, STATUS]}])
    await device.client.discover()

    assert device.answered == ["initialize", "tools/list"]
    # The initialized notification goes out between the two, and is not
    # answered because notifications carry no id.
    assert [payload.get("method") for payload in device.sent] == [
        "initialize",
        "notifications/initialized",
        "tools/list",
    ]
    assert [tool.name for tool in device.client.tools()] == [
        "self_audio_speaker_set_volume",
        "self_get_device_status",
    ]
    assert device.client.tools()[0].input_schema == VOLUME["inputSchema"]
    assert device.client.discovered


async def test_pagination_follows_the_cursor_to_the_end() -> None:
    device = FakeDevice(
        [
            {"tools": [VOLUME], "nextCursor": "page-2"},
            {"tools": [STATUS]},
        ]
    )
    await device.client.discover()
    assert [tool.name for tool in device.client.tools()] == [
        "self_audio_speaker_set_volume",
        "self_get_device_status",
    ]
    cursors = [
        payload["params"]["cursor"]
        for payload in device.sent
        if payload.get("method") == "tools/list"
    ]
    assert cursors == ["", "page-2"]


async def test_a_name_collision_after_sanitizing_drops_the_later_tool() -> None:
    # Two dotted names can sanitize to the same thing; first listed wins,
    # so the outcome is the same on every run.
    device = FakeDevice([{"tools": [{"name": "self.a.b"}, {"name": "self.a b"}]}])
    await device.client.discover()
    assert [tool.name for tool in device.client.tools()] == ["self_a_b"]
    assert device.client.knows("self_a_b")


async def test_a_name_too_long_for_the_apis_is_dropped() -> None:
    device = FakeDevice([{"tools": [{"name": "self." + "x" * 70}, VOLUME]}])
    await device.client.discover()
    assert [tool.name for tool in device.client.tools()] == ["self_audio_speaker_set_volume"]


async def test_a_call_goes_out_under_the_devices_own_name() -> None:
    device = FakeDevice([{"tools": [VOLUME]}])
    await device.client.discover()

    assert await device.client.call("self_audio_speaker_set_volume", {"volume": 40}) == (
        "true",
        False,
    )
    (call,) = [payload for payload in device.sent if payload.get("method") == "tools/call"]
    assert call["params"] == {
        "name": "self.audio_speaker.set_volume",
        "arguments": {"volume": 40},
    }


async def test_a_failing_call_keeps_its_error_flag() -> None:
    device = FakeDevice([{"tools": [VOLUME]}])
    device.call_results["self.audio_speaker.set_volume"] = {
        "content": [{"type": "text", "text": "out of range"}],
        "isError": True,
    }
    await device.client.discover()
    assert await device.client.call("self_audio_speaker_set_volume", {"volume": 400}) == (
        "out of range",
        True,
    )


async def test_a_call_to_a_tool_the_device_never_listed_is_refused() -> None:
    device = FakeDevice([{"tools": [VOLUME]}])
    await device.client.discover()
    with pytest.raises(KeyError):
        await device.client.call("self_reboot", {})


async def test_a_device_that_never_answers_costs_no_tools_and_no_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import vinga_server.tools.device as device_module

    monkeypatch.setattr(device_module, "REQUEST_TIMEOUT_S", 0.05)
    device = FakeDevice([])
    device.silent_methods = {"initialize"}
    await device.client.discover()
    assert device.client.tools() == []
    assert device.client.discovered


async def test_an_unanswered_call_can_be_abandoned_from_outside() -> None:
    # xiaozhi-sdk ignores tools/call for a name it does not know without
    # answering, so the session bounds every call with a timeout; the
    # client must not leak the pending request when that fires.
    device = FakeDevice([{"tools": [VOLUME]}])
    device.silent_methods = {"tools/call"}
    await device.client.discover()

    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.05):
            await device.client.call("self_audio_speaker_set_volume", {"volume": 40})
    # White-box: an abandoned call leaves an entry in the client's
    # pending map, and a map that grows per timed-out call is memory
    # nothing reports. What it costs is a session that has been talking
    # to a slow board for an hour, which no assertion can wait for.
    assert device.client._pending == {}


async def test_a_device_notification_is_logged_and_not_answered() -> None:
    device = FakeDevice([{"tools": []}])
    device.client.handle(
        {"jsonrpc": "2.0", "method": "notifications/state_changed", "params": {"state": "idle"}}
    )
    device.client.handle({"jsonrpc": "2.0", "id": 99, "result": {}})
    assert device.sent == []


async def test_closing_gives_up_on_anything_in_flight() -> None:
    device = FakeDevice([{"tools": [VOLUME]}])
    device.silent_methods = {"tools/call"}
    await device.client.discover()

    pending = asyncio.create_task(device.client.call("self_audio_speaker_set_volume", {}))
    await asyncio.sleep(0)
    device.client.close()
    with pytest.raises(asyncio.CancelledError):
        await pending
