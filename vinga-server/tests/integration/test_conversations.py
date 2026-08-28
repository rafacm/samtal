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
from typing import Any

from sqlalchemy import text
from xiaozhi_sdk import XiaoZhiWebsocket

from tests.integration.conftest import (
    FRAME_BYTES,
    SAMPLE_RATE,
    converse,
    speech_pcm,
)
from vinga_server.config import Config
from vinga_server.config.models import DatabaseConfig, ProviderConfig
from vinga_server.db import read_engine

DEVICE_MAC = "aa:bb:cc:dd:ee:31"

BATTERY = "72 percent"


def recording_config() -> Config:
    """One agent on the mock pipeline, recording into `directory`.

    The mock LLM asks for the device's own tool on the first round of
    each turn and speaks its answer on the second, which is what makes a
    conversation with a tool call in it deterministic.
    """
    return Config(
        server={"conversations": {"enabled": True}},
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


def read(statement: str) -> list[dict[str, Any]]:
    engine = read_engine(DatabaseConfig())
    try:
        with engine.connect() as connection:
            return [dict(row) for row in connection.execute(text(statement)).mappings()]
    finally:
        engine.dispose()


async def test_a_deployment_records_what_was_said(serve) -> None:
    config = recording_config()
    async with serve(config) as port:
        await two_turns(port, DEVICE_MAC)
    # Read after the server has gone: its lifespan drains the writer on
    # the way out, so what is on disk then is the whole record.

    (session,) = read("select * from conversations.sessions")
    turns = read("select * from conversations.turns order by id")
    calls = read("select * from conversations.tool_invocations order by id")
    events = read("select * from conversations.events order by id")

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


async def test_a_deployment_that_was_not_asked_records_nothing(serve) -> None:
    # Criterion 1 against a real boot: no section, no writer, no row,
    # and a conversation that behaves exactly as the rest of this
    # lane's do. The tables are there either way, because boot migrates
    # the schema whether or not recording is on; empty tables are not a
    # recording, and this is what says so end to end (#283).
    config = recording_config()
    config.server.conversations = None
    async with serve(config) as port:
        events = await two_turns(port, DEVICE_MAC)

    assert events, "the conversation did not run"
    assert read("select * from conversations.sessions") == []


# Moving a session between conversations, over a real socket
#
# What only this lane can say about resumption: the switch reaches a
# booted server, the tools it enables are offered to a model that is
# really there, the search runs against Postgres rather than a double,
# and the thread a move lands on is the thread the store then records
# the next turn against.
#
# What it deliberately does not drive is the second beat of the search
# flow, the call naming the conversation the user picked. That argument
# is an id the model can only have read out of the previous tool result,
# and the mock LLM asks for a tool it was configured with rather than
# one it composed; teaching a test double to lift a value out of a
# result would be more machinery than the claim is worth. The
# interception that beat goes through is driven end to end below by
# `new_conversation`, and by the whole of
# `tests/unit/test_session_conversations.py` against a store double.


def moving_config(tool_name: str, **arguments: object) -> Config:
    """One agent that reaches for one conversation tool per turn, on a
    server with resumption switched on."""
    config = recording_config()
    assert config.server.conversations is not None
    config.server.conversations.resumption = True
    config.providers.llm["mock"] = ProviderConfig(
        type="mock",
        reply="It says {tool_result}.",
        tool_when="battery",
        tool_name=tool_name,
        tool_arguments=dict(arguments),
    )
    return config


async def test_a_later_session_finds_the_thread_an_earlier_one_left(serve) -> None:
    async with serve(recording_config()) as port:
        await two_turns(port, DEVICE_MAC)
    (thread,) = read("select * from conversations.conversations")

    async with serve(moving_config("resume_conversation", description="battery")) as port:
        await converse(port, DEVICE_MAC)

    # The search answered out of the database the first server wrote,
    # and what the agent said carries what it found. The reply is
    # content and is read where content lives.
    said = read("select reply from conversations.turns order by id")[-1]["reply"]
    assert thread["conversation"] in said
    assert thread["title"] == "how is the battery"
    assert thread["title"] in said


async def test_a_fresh_conversation_moves_the_session_onto_it(serve) -> None:
    """The interception, end to end: the turn that asked is recorded on
    the thread it started on, and the turn after it on the one the move
    landed on."""
    async with serve(moving_config("new_conversation")) as port:
        await two_turns(port, DEVICE_MAC)

    turns = read("select * from conversations.turns order by id")
    threads = read("select * from conversations.conversations order by id")
    sessions = read("select * from conversations.sessions")

    assert len(sessions) == 1
    assert [turn["conversation"] for turn in turns] == [
        thread["conversation"] for thread in threads
    ]
    assert len({turn["conversation"] for turn in turns}) == 2
