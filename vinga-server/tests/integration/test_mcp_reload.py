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

from tests.integration.conftest import FRAME_BYTES, SAMPLE_RATE, speech_pcm, spoken
from tests.support.notices import RELOAD, boundaries
from tests.support.problems import refused as refusal_body
from vinga_server.config import Config
from vinga_server.config.models import API_MOUNT_PATH

STDIO_SERVER = Path(__file__).parents[1] / "support" / "mcp_stdio_server.py"

DEVICE_MAC = "aa:bb:cc:dd:ee:31"

# The entry the operator writes mid-session, and the tool it publishes.
ENTRY = "weather"

TOOL = f"{ENTRY}__secret_word"


def stdio_entry() -> dict[str, object]:
    return {
        "transport": "stdio",
        "command": sys.executable,
        "args": [str(STDIO_SERVER)],
    }


def one_agent(**extra: object) -> Config:
    """A working deployment, by default with no MCP servers at all: the
    agent is complete, and the tool the model is scripted to call does
    not exist."""
    return Config(
        **(
            {
                "providers": {
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
                "agent_defaults": dict.fromkeys(("llm", "asr", "tts", "vad"), "mock"),
                "agents": {"assistant": {"prompt": "ASSISTANT"}},
                "devices": {DEVICE_MAC: ["assistant"]},
                "default_agent": "assistant",
            }
            | extra
        )
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
        headers={"Authorization": f"Bearer {os.environ['VINGA_API_SECRET']}"},
        timeout=60,
    )


async def test_a_written_and_granted_server_is_usable_without_a_restart(
    serve_app, tmp_path: Path
) -> None:
    async with serve_app(one_agent()) as (port, app), control_client(
        port
    ) as control:
        device = Device(port)
        await device.connect()
        session = device.client.session_id
        assert len(app.state.composition.sessions) == 1
        try:
            # The tool the model is scripted to call does not exist yet,
            # so the reply carries the loop's own refusal. That is what
            # makes the second utterance's answer mean something.
            first = await device.say_something()
            assert f'no tool called "{TOOL}"' in first

            # The operator's two writes and the reload, with the device
            # connected and the session alive throughout.
            written = await control.put(f"/mcp-servers/{ENTRY}", json=stdio_entry())
            assert written.status_code == 200, written.text
            # And the write said how to apply it, which is what this
            # test then does.
            assert boundaries(written.json()) == {RELOAD}

            granted = await control.put(
                "/agents/assistant", json={"prompt": "ASSISTANT", "mcp": [ENTRY]}
            )
            assert granted.status_code == 200, granted.text

            applied = await control.post("/runtime/config/reload")
            assert applied.status_code == 200, applied.text
            assert applied.json()["mcp"]["started"] == [ENTRY]
            assert applied.json()["mcp"]["restarted"] == []
            assert applied.json()["mcp"]["stopped"] == []
            running = applied.json()["mcp"]["servers"][ENTRY]
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
            assert len(app.state.composition.sessions) == 1
            assert not [
                event for event in device.events if event.get("type") == "websocket"
            ]
        finally:
            await device.close()


async def test_a_refused_reload_leaves_the_running_servers_alone(
    serve_app, tmp_path: Path
) -> None:
    """The other half of the promise, on a server with something to
    lose: a reload the stored configuration refuses changes nothing, and
    the conversation that was using an MCP server goes on using it.

    Provoked the way an operator would provoke it by accident, and by
    the one way this API leaves open. Every write route refuses a
    fragment that would leave a reference dangling, and every delete
    refuses while something still names its subject, so what is left is
    the rule that is checked when a configuration is composed and by no
    write: a deployment with agents has to be reachable. Unbinding the
    board and then clearing the default agent is two live, legal writes
    that between them leave a snapshot no boot would accept.
    """
    granted = one_agent(
        mcp_servers={ENTRY: stdio_entry()},
        agents={"assistant": {"prompt": "ASSISTANT", "mcp": [ENTRY]}},
    )
    async with serve_app(granted) as (port, app), control_client(
        port
    ) as control:
        device = Device(port)
        await device.connect()
        try:
            assert await device.say_something() == "The tool says rhubarb."
            before = (await control.get("/runtime/mcp-servers")).json()
            assert before[ENTRY]["state"] == "connected"
            assert TOOL in before[ENTRY]["tools"]

            assert (await control.delete(f"/devices/{DEVICE_MAC}")).status_code == 200
            assert (await control.delete("/default-agent")).status_code == 200

            refused = await control.post("/runtime/config/reload")

            assert refused.status_code == 422
            detail = refusal_body(refused.json(), 422)
            # It names nothing of what it refused on: the stored half is
            # arbitrary bytes, and the location is available from a
            # server started over the same store.
            assert "default_agent" not in detail
            # Nothing was applied, and the instants say so rather than
            # the states: a manager stopped and started again would
            # report `connected` too, and would have moved.
            assert (await control.get("/runtime/mcp-servers")).json() == before
            # And the conversation that was using the server still is.
            assert await device.say_something() == "The tool says rhubarb."

            # Repaired, and the same request applies. The entry comes
            # back `unchanged` rather than `restarted`, which is the
            # other way of saying the refusal never touched it.
            repaired = await control.put("/default-agent", json={"name": "assistant"})
            assert repaired.status_code == 200, repaired.text
            applied = await control.post("/runtime/config/reload")
            assert applied.status_code == 200, applied.text
            assert applied.json()["mcp"]["unchanged"] == [ENTRY]
            assert applied.json()["mcp"]["servers"] == before

            assert await device.say_something() == "The tool says rhubarb."
        finally:
            await device.close()
