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

from tests.integration.conftest import FRAME_BYTES, SAMPLE_RATE, speech_pcm
from vinga_server.config import Config
from vinga_server.config.models import DatabaseConfig, ProviderConfig
from vinga_server.db import read_engine
from vinga_server.runtime.prompt import STATE_HEADING

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

    (session,) = read("select * from record.sessions")
    turns = read("select * from record.turns order by id")
    calls = read("select * from record.tool_invocations order by id")
    events = read("select * from record.events order by id")

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
    assert read("select * from record.sessions") == []


# Moving a session between conversations, over a real socket
#
# What only this lane can say about resumption: the switch reaches a
# booted server, the tools it enables are offered to a model that is
# really there, the search runs against Postgres rather than a double,
# the id the search answered is what the next call names, and the
# thread a move lands on is the thread the store then records against,
# in a second session, against rows a first one left behind.
#
# The second beat of that flow is a call whose argument is an id the
# model can only have read out of the previous tool result, which is
# more than a template can express. The scripted provider is given the
# rule instead: match the results, lift the first group out, and ask
# for the same tool again with it. That is a test double reading a
# script, not a test double reading.


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


def resuming_config() -> Config:
    """An agent that searches for the earlier conversation, picks what
    the search answered, and then talks on it.

    The script is two beats and a brake. The utterance asks for a
    search; the results are answered with the same tool naming the id
    they carried; and once the resumed dialogue is in front of the model
    it asks for nothing more, which is what makes the utterance after
    the move an ordinary turn in a lane where every utterance
    transcribes the same.
    """
    config = recording_config()
    assert config.server.conversations is not None
    config.server.conversations.resumption = True
    config.providers.llm["mock"] = ProviderConfig(
        type="mock",
        reply="Right, where were we.",
        tool_when="battery",
        tool_name="resume_conversation",
        tool_arguments={"description": "battery"},
        then_pattern='conversation "([0-9a-f]+)"',
        then_arguments={"conversation": "{found}"},
        # The earlier session's reply, which is in front of the model
        # only once the older thread has been rebuilt.
        tool_unless=BATTERY,
    )
    return config


async def test_a_later_session_resumes_the_thread_an_earlier_one_left(serve) -> None:
    """The whole flow, over a socket and against Postgres: search, pick
    what the search found, move, and keep recording on the thread that
    was picked up.

    What the assertions are about is attribution across two sessions.
    The turn that asked stays on the conversation the second session
    opened on; the reply the move was greeted with is the first turn the
    second session recorded on the older thread, and it has nothing
    heard on it, because what the user said was said before the move;
    and the utterance after it lands on that same older thread.
    """
    async with serve(recording_config()) as port:
        await two_turns(port, DEVICE_MAC)
    (earlier,) = read("select * from record.conversations")
    assert earlier["title"] == "how is the battery"
    before = {turn["id"] for turn in read("select id from record.turns")}

    async with serve(resuming_config()) as port:
        await two_turns(port, DEVICE_MAC)

    sessions = read("select * from record.sessions order by id")
    threads = read("select * from record.conversations order by id")
    turns = [
        turn
        for turn in read("select * from record.turns order by id")
        if turn["id"] not in before
    ]
    assert len(sessions) == 2
    # The second session opened on a thread of its own and moved onto
    # the first session's, so there are two and only two.
    assert [thread["conversation"] for thread in threads][0] == earlier["conversation"]
    assert len(threads) == 2
    opened = next(
        thread["conversation"]
        for thread in threads
        if thread["conversation"] != earlier["conversation"]
    )

    asked, seeded, carried = turns
    assert {turn["session"] for turn in turns} == {sessions[1]["session"]}
    # The turn that asked, on the conversation it was asked on, with
    # both halves of the flow recorded under it: the search, then the
    # call naming what the search answered.
    assert asked["conversation"] == opened
    assert asked["heard"] == "how is the battery"
    assert [
        call["name"]
        for call in read("select * from record.tool_invocations order by id")
        if call["turn"] == asked["id"]
    ] == ["resume_conversation", "resume_conversation"]
    # The other side of the move: the greeting, on the older thread,
    # heard from nobody.
    assert (seeded["conversation"], seeded["heard"]) == (
        earlier["conversation"],
        None,
    )
    assert seeded["reply"] == "Right, where were we."
    # And the conversation carries on there.
    assert carried["conversation"] == earlier["conversation"]
    assert carried["heard"] == "how is the battery"
    # Which is a thread two sessions have now spoken on.
    assert {
        turn["session"]
        for turn in read("select * from record.turns")
        if turn["conversation"] == earlier["conversation"]
    } == {sessions[0]["session"], sessions[1]["session"]}


# What a conversation keeps, across a disconnect and back
#
# The case the grace period exists for, and the one nothing else can
# drive: a note written before the thread's first turn has landed, a
# device that then hangs up, and a second session that picks the thread
# up again by name. What only this lane can say is that the ledger
# really re-attaches, because the id it is keyed by is minted by a
# session and the resume is a tool call a model makes over a socket.
#
# The prompt is read back out of the reply, which is what the scripted
# provider's `{system}` is for: what the model was sent is otherwise
# invisible from outside a served process, and it is exactly what is
# under test.


NOTE = "the tavern"

STATE_NOTED = f"Noted scene: {NOTE}"


def stateful_config() -> Config:
    """An agent that writes one note about the conversation it is in,
    on the first round of every turn."""
    config = recording_config()
    config.providers.llm["mock"] = ProviderConfig(
        type="mock",
        reply="It says {tool_result}.",
        tool_when="battery",
        tool_name="set_state",
        tool_arguments={"key": "scene", "value": NOTE},
    )
    return config


def resuming_onto_state_config() -> Config:
    """The second session: search, pick what the search answered, and
    then say what it was sent.

    The brake is the note the first session wrote, which is in front of
    this model only once the older thread has been rebuilt, so the
    utterance after the move is an ordinary turn.
    """
    config = recording_config()
    assert config.server.conversations is not None
    config.server.conversations.resumption = True
    config.providers.llm["mock"] = ProviderConfig(
        type="mock",
        reply="Where were we. {system}",
        tool_when="battery",
        tool_name="resume_conversation",
        tool_arguments={"description": "battery"},
        then_pattern='conversation "([0-9a-f]+)"',
        then_arguments={"conversation": "{found}"},
        tool_unless=STATE_NOTED,
    )
    return config


def state_of(conversation: str) -> list[tuple[str, str]]:
    return [
        (row["key"], row["value"])
        for row in read(
            "select key, value from memory.state "
            f"where conversation = '{conversation}' order by key"
        )
    ]


async def test_a_conversations_notes_come_back_when_the_thread_does(serve) -> None:
    """The whole of the lifecycle promise, end to end.

    The note is written during the first turn's reply, which is before
    that turn's row lands, so the thread it is keyed by does not exist
    yet: it is exactly the state the sweep's grace period is for. The
    device then hangs up, and a second session picks the thread up by
    name; from that round on, what the model is sent carries the ledger
    again.
    """
    async with serve(stateful_config()) as port:
        await two_turns(port, DEVICE_MAC)

    (earlier,) = read("select * from record.conversations")
    assert state_of(earlier["conversation"]) == [("scene", NOTE)]
    before = {turn["id"] for turn in read("select id from record.turns")}

    async with serve(resuming_onto_state_config()) as port:
        await two_turns(port, DEVICE_MAC)

    resumed = [
        turn
        for turn in read("select * from record.turns order by id")
        if turn["id"] not in before
        and turn["conversation"] == earlier["conversation"]
    ]
    assert resumed, "the second session never moved onto the earlier thread"
    # Every round on the thread it came back to was sent the ledger, and
    # the block says which of the three scopes it is.
    for turn in resumed:
        assert STATE_HEADING in turn["reply"]
        assert f"- scene: {NOTE}" in turn["reply"]
    # And the note is still exactly what was written down, once: an
    # upsert by key rather than a second entry.
    assert state_of(earlier["conversation"]) == [("scene", NOTE)]


async def test_a_new_thread_starts_with_nothing_written_down(serve) -> None:
    """The other side of it, on the deployment that cannot resume.

    Text storage off is the configuration in which a thread can never be
    picked up again (`conversations.resumption` is refused with it), so
    the second session opens a thread of its own and starts clean. What
    an agent wants to keep across that has to be remembered rather than
    written down, which is what the tool descriptions say.
    """
    config = stateful_config()
    assert config.server.conversations is not None
    config.server.conversations.text = False

    async with serve(config) as port:
        await two_turns(port, DEVICE_MAC)
    (first,) = read("select * from record.conversations")
    assert state_of(first["conversation"]) == [("scene", NOTE)]

    async with serve(config) as port:
        await two_turns(port, DEVICE_MAC)

    threads = read("select * from record.conversations order by id")
    assert len(threads) == 2, "the second session did not open a thread of its own"
    second = threads[1]["conversation"]
    assert second != first["conversation"]
    # The second conversation kept its own note and inherited nothing:
    # the ledger is the thread's, and this is a new thread.
    assert state_of(second) == [("scene", NOTE)]
    assert len(read("select * from memory.state")) == 2


async def test_a_fresh_conversation_moves_the_session_onto_it(serve) -> None:
    """The interception, end to end: the turn that asked is recorded on
    the thread it was asked on, and the greeting that answered the move
    opens the thread it landed on.

    Both utterances ask, so one session walks three threads, and the
    middle one holds a turn of each kind.
    """
    async with serve(moving_config("new_conversation")) as port:
        await two_turns(port, DEVICE_MAC)

    turns = read("select * from record.turns order by id")
    threads = read("select * from record.conversations order by id")
    sessions = read("select * from record.sessions")

    assert len(sessions) == 1
    first, second, third = (thread["conversation"] for thread in threads)
    assert [turn["conversation"] for turn in turns] == [first, second, second, third]
    # The turns that asked carry the utterance; the greetings that
    # answered the moves were heard from nobody.
    assert [turn["heard"] for turn in turns] == [
        "how is the battery",
        None,
        "how is the battery",
        None,
    ]
    # A thread is named by its earliest utterance, so the one that only
    # ever held a greeting has no name to take.
    assert [thread["title"] for thread in threads] == [
        "how is the battery",
        "how is the battery",
        None,
    ]
