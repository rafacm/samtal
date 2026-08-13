"""A written and granted MCP server becomes reachable without a restart.

The issue's first verification step, executed end to end: a real server
on a real port, a real device on a real websocket, the writes and the
reload over the real configuration API, and a real MCP server spawned
over stdio in between.

One socket and one session across the whole sequence, which is the whole
point. The promise is per-reply pickup inside a running conversation,
and the lane's `converse` helper opens, speaks once and closes, so a
test built on it would pass by reconnecting: it would prove that a
restart is not needed and say nothing about the session. So this one
holds the connection open across both utterances and asserts the session
never changed.
"""

import asyncio
import os
import sys
from pathlib import Path

import httpx
from xiaozhi_sdk import XiaoZhiWebsocket

from samtal_server.config import Config
from samtal_server.config.models import API_MOUNT_PATH
from samtal_server.config.writes import MCP_RELOAD_NOTICE
from tests.integration.conftest import FRAME_BYTES, SAMPLE_RATE, speech_pcm, spoken

STDIO_SERVER = Path(__file__).parents[1] / "support" / "mcp_stdio_server.py"

DEVICE_MAC = "aa:bb:cc:dd:ee:31"

# The entry the operator writes mid-session, and the tool it publishes.
ENTRY = "weather"

TOOL = f"{ENTRY}__secret_word"


def one_agent() -> Config:
    """A working deployment with no MCP servers at all: the agent is
    complete, and the tool the model is scripted to call does not
    exist."""
    return Config(
        providers={
            "llm": {
                "mock": {
                    "type": "mock",
                    "reply": "The tool says {tool_result}.",
                    "tool_when": "secret",
                    "tool_name": TOOL,
                }
            },
            "asr": {"mock": {"type": "mock", "text": "tell me the secret"}},
            "tts": {"mock": {"type": "mock"}},
            "vad": {"mock": {"type": "mock"}},
        },
        agent_defaults=dict.fromkeys(("llm", "asr", "tts", "vad"), "mock"),
        agents={"assistant": {"prompt": "ASSISTANT"}},
        devices={DEVICE_MAC: ["assistant"]},
        default_agent="assistant",
    )


class Device:
    """One board, connected once and spoken to more than once.

    The lane's helper is a whole conversation from connect to close;
    this is the same conversation held open, so that what changes
    between two utterances is the server rather than the connection.
    """

    def __init__(self, port: int) -> None:
        self._port = port
        self.events: list[dict] = []
        self._finished = asyncio.Event()
        self.client = XiaoZhiWebsocket(
            self._received,
            ota_url=f"http://127.0.0.1:{port}/xiaozhi/ota/",
            audio_sample_rate=SAMPLE_RATE,
        )

    async def _received(self, data: dict) -> None:
        self.events.append(data)
        if data.get("type") == "tts" and data.get("state") == "stop":
            self._finished.set()

    async def connect(self) -> None:
        assert await self.client.init_connection(DEVICE_MAC)

    async def say_something(self) -> str:
        """One utterance, and the reply spoken back to it.

        The device is in auto mode, so the sdk re-arms its own listening
        when the previous reply's `tts stop` arrives, which is what lets
        a second utterance go out on the connection the first one used.
        """
        said = len(self.events)
        self._finished.clear()
        pcm = speech_pcm(960)
        for start in range(0, len(pcm), FRAME_BYTES):
            assert await self.client.send_audio(pcm[start : start + FRAME_BYTES])
        await self.client.send_silence_audio(1.2)
        await asyncio.wait_for(self._finished.wait(), timeout=30)
        return spoken(self.events[said:])

    async def close(self) -> None:
        await self.client.close()


def control_client(port: int) -> httpx.AsyncClient:
    """The operator's side: the configuration API of the very server the
    device is talking to, on the same port, gated by the same token that
    server was started with."""
    return httpx.AsyncClient(
        base_url=f"http://127.0.0.1:{port}{API_MOUNT_PATH}",
        headers={"Authorization": f"Bearer {os.environ['SAMTAL_API_SECRET']}"},
        timeout=60,
    )


async def test_a_written_and_granted_server_is_usable_without_a_restart(
    serve_app_in, tmp_path: Path
) -> None:
    async with serve_app_in(tmp_path / "db", one_agent()) as (port, app), control_client(
        port
    ) as control:
        device = Device(port)
        await device.connect()
        session = device.client.session_id
        assert len(app.state.sessions) == 1
        try:
            # The tool the model is scripted to call does not exist yet,
            # so the reply carries the loop's own refusal. That is what
            # makes the second utterance's answer mean something.
            first = await device.say_something()
            assert f'no tool called "{TOOL}"' in first

            # The operator's two writes and the reload, with the device
            # connected and the session alive throughout.
            written = await control.put(
                f"/mcp-servers/{ENTRY}",
                json={
                    "transport": "stdio",
                    "command": sys.executable,
                    "args": [str(STDIO_SERVER)],
                },
            )
            assert written.status_code == 200, written.text
            # And the write said how to apply it, which is what this
            # test then does.
            assert written.json()["notice"] == MCP_RELOAD_NOTICE

            granted = await control.put(
                "/agents/assistant", json={"prompt": "ASSISTANT", "mcp": [ENTRY]}
            )
            assert granted.status_code == 200, granted.text

            applied = await control.post("/runtime/mcp-servers/reload")
            assert applied.status_code == 200, applied.text
            assert applied.json()["started"] == [ENTRY]
            assert applied.json()["restarted"] == []
            assert applied.json()["stopped"] == []
            running = applied.json()["servers"][ENTRY]
            assert running["state"] == "connected", running
            assert TOOL in running["tools"]
            assert running["grants"] == {"assistant": None}

            # The same socket and the same session, and now the tool is
            # there: the snapshot is taken per reply, so this utterance
            # was offered a world the previous one did not have.
            second = await device.say_something()
            assert second == "The tool says rhubarb."

            # No reconnect anywhere: the session the second reply was
            # spoken in is the one the first was, on the socket that was
            # never closed, and the server saw one session throughout.
            assert device.client.session_id == session
            assert len(app.state.sessions) == 1
            assert not [
                event for event in device.events if event.get("type") == "websocket"
            ]
        finally:
            await device.close()
