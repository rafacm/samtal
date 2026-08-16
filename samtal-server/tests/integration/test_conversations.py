"""The conversation store against a real server and a real device.

The unit lane drives the same wiring through a test client. What only
this lane can say is that a whole deployment records: a server booted
the way a deployment boots, a device that is xiaozhi-sdk over a real
socket, two utterances on one connection, a tool the device itself
serves, and a file on disk afterwards holding the session, its turns,
the calls under them and the decision track beside them.

The tool matters here rather than in the unit lane: a device tool is the
one source whose name and arguments come off the wire from the peer, and
its invocation row is what the text switch governs.
"""

import asyncio
from pathlib import Path
from typing import Any

from sqlalchemy import text
from xiaozhi_sdk import XiaoZhiWebsocket

from samtal_server.config import Config
from samtal_server.conversations.store import read_conversations
from tests.integration.conftest import FRAME_BYTES, SAMPLE_RATE, speech_pcm

DEVICE_MAC = "aa:bb:cc:dd:ee:31"

BATTERY = "72 percent"


def recording_config(directory: Path) -> Config:
    """One agent on the mock pipeline, recording into `directory`.

    The mock LLM asks for the device's own tool on the first round of
    each turn and speaks its answer on the second, which is what makes a
    conversation with a tool call in it deterministic.
    """
    return Config(
        server={
            "database": {"dir": str(directory)},
            "conversations": {"enabled": True},
        },
        providers={
            "llm": {
                "mock": {
                    "type": "mock",
                    "reply": "The board says {tool_result}.",
                    "tool_when": "battery",
                    "tool_name": "self_get_battery_level",
                }
            },
            "asr": {"mock": {"type": "mock", "text": "how is the battery"}},
            "tts": {"mock": {"type": "mock"}},
            "vad": {"mock": {"type": "mock"}},
        },
        agent_defaults=dict.fromkeys(("llm", "asr", "tts", "vad"), "mock"),
        agents={"assistant": {"prompt": "ASSISTANT"}},
        devices={DEVICE_MAC: ["assistant"]},
        default_agent="assistant",
    )


def battery_tool() -> dict[str, Any]:
    def level(arguments: dict) -> tuple[str, bool]:
        return BATTERY, False

    return {
        "name": "self.get_battery_level",
        "description": "How much charge the board has left.",
        "inputSchema": {"type": "object", "properties": {}},
        "tool_func": level,
        "is_async": False,
    }


async def two_turns(port: int, mac: str) -> list[dict]:
    """One connection, two utterances, both answered.

    `conftest.converse` holds one turn and hangs up, which would be two
    sessions rather than one conversation. The sdk listens in realtime
    mode, so a second utterance on the same socket is what a device
    actually does between replies.
    """
    events: list[dict] = []
    replied = asyncio.Event()

    async def on_message(data: dict) -> None:
        events.append(data)
        if data.get("type") == "tts" and data.get("state") == "stop":
            replied.set()

    client = XiaoZhiWebsocket(
        on_message,
        ota_url=f"http://127.0.0.1:{port}/xiaozhi/ota/",
        audio_sample_rate=SAMPLE_RATE,
    )
    try:
        await client.set_mcp_tool([battery_tool()])
        assert await client.init_connection(mac)
        pcm = speech_pcm(960)
        for _ in range(2):
            replied.clear()
            for start in range(0, len(pcm), FRAME_BYTES):
                assert await client.send_audio(pcm[start : start + FRAME_BYTES])
            await client.send_silence_audio(1.2)
            await asyncio.wait_for(replied.wait(), timeout=30)
        await asyncio.sleep(0.3)
    finally:
        await client.close()
    return events


def read(directory: Path, statement: str) -> list[dict[str, Any]]:
    engine = read_conversations(directory)
    try:
        with engine.connect() as connection:
            return [dict(row) for row in connection.execute(text(statement)).mappings()]
    finally:
        engine.dispose()


async def test_a_deployment_records_what_was_said(serve, tmp_path: Path) -> None:
    config = recording_config(tmp_path)
    async with serve(config) as port:
        await two_turns(port, DEVICE_MAC)
    # Read after the server has gone: its lifespan drains the writer on
    # the way out, so what is on disk then is the whole record.

    (session,) = read(tmp_path, "select * from sessions")
    turns = read(tmp_path, "select * from turns order by id")
    calls = read(tmp_path, "select * from tool_invocations order by id")
    events = read(tmp_path, "select * from events order by id")

    assert session["device"] == DEVICE_MAC
    assert session["agent"] == "assistant"
    assert session["closed_at"] is not None
    assert session["close_reason"] == "client"
    assert session["dropped"] == 0

    assert len(turns) == 2
    for turn in turns:
        assert turn["session"] == session["session"]
        assert turn["heard"] == "how is the battery"
        assert turn["reply"] == f'The board says "{BATTERY}".'
        assert turn["rounds"] == 2
        assert turn["tool_calls"] == 1
        assert turn["asr_ms"] is not None
        assert turn["llm_ms"] is not None
        assert turn["tts_first_audio_ms"] is not None

    assert [call["turn"] for call in calls] == [turn["id"] for turn in turns]
    for call in calls:
        # The one source whose name is the peer's own word, which is why
        # it is stored as content rather than named on the event.
        assert call["source"] == "device"
        assert call["entry"] is None
        assert call["name"] == "self_get_battery_level"
        assert call["is_error"] == 0
        assert call["duration_ms"] is not None

    named = [row["name"] for row in events]
    assert named[0] == "session_open"
    assert named[-1] == "session_closed"
    assert named.count("heard") == len(turns)
    assert named.count("llm_round") == sum(turn["rounds"] for turn in turns)
    assert named.count("tool_call") == sum(turn["tool_calls"] for turn in turns)


async def test_a_deployment_that_was_not_asked_records_nothing(
    serve, tmp_path: Path
) -> None:
    # Criterion 1 against a real boot: no section, no file, and a
    # conversation that behaves exactly as the rest of this lane's do.
    config = recording_config(tmp_path)
    config.server.conversations = None
    async with serve(config) as port:
        events = await two_turns(port, DEVICE_MAC)

    assert events, "the conversation did not run"
    assert not (tmp_path / "conversations.db").exists()
