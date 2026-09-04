"""What a session's records must NOT contain, hunted for.

This file used to pin every session event exactly as it was emitted:
channel, level, unrendered template, arguments and field set, restated
per path. That duplication is gone (#210). The declaration is the
template and the argument order now, `docs/reference/events.md` is the
committed contract for names, channels, levels, argument order, types,
requiredness and nullability, and the driver suite
(`test_event_baseline.py`) is the proof that every declared shape is
really emitted. Names and structure are pinned in one artifact, once.

What is left here is the half none of those can carry, and the half the
plan says survives whole: a pin says a sentence is what it is, never
that it is safe. The retained JSON logs are the observability surface
(ADR 2026-08-04) and they carry metadata only: what was said is the
conversation store's (#120, the content-and-telemetry ADR). So a
credential-shaped sentinel is planted where a far side, a device or a
person's own words enter the session, and it is hunted through:

- the record's rendered sentence and its unrendered arguments;
- every field of its payload;
- both formats a deployment can be shipping, JSON and text;
- every other record of the run, at every level;
- and an attached tap, because a consumer is handed the same arguments
  the record carries and reading only the log would miss it.

Absence is asserted beside the thing the event exists for, so a test
that passed by emitting nothing at all fails instead.

The sentinels for what the emitter itself may say when a construction
is refused live in `test_event_enforcement_sentinels.py`; these are the
sentinels for what a lawful record may say.

The file keeps its name. What it pins is still the event surface, in
the one dimension no declaration can carry: the reference says which
fields exist and the driver suite says a record really carries them, and
neither can say that what a stranger sent is not in one of them.

The driving is the driver harness's, imported rather than repeated:
those drivers are where every session path is reached from now, and a
second copy of them here would be a second thing to keep working.
"""

import logging
import sys
from typing import Any, cast

import pytest

from tests.support.configs import (
    BOTH_MAC,
    DEVICE_MAC,
    POET_MAC,
    STDIO_SERVER,
    base_config,
    config_with_agent,
)
from tests.support.device_tools import FakeDevice
from tests.support.events import events, only
from tests.support.mcp_stdio_server import SHADOWED_TOOL_ENV
from tests.support.providers import ScriptedLlm, Unreachable
from tests.support.sessions import (
    call,
    device_session,
    drive_reply,
    events_of,
    run_reply,
    session_for,
)
from tests.support.sockets import RecordingSocket, spoken
from tests.tools.event_baseline import Failing, failing_reply, turned_away
from vinga_server.config import Config
from vinga_server.logs import _STANDARD_ATTRIBUTES, TEXT_FORMAT, JsonFormatter
from vinga_server.tools.mcp import McpServers

# The utterance the direct drivers hand a reply: 20 ms of silence, which
# the mock ASR answers whatever it holds.
UTTERANCE = b"\x00\x00" * 320

# Not a real credential, and shaped so a substring check for it cannot
# match by accident. It stands for whatever a caller puts in a header,
# or a far side in an error, or a person into a microphone, that nobody
# wrote for a log line.
SENTINEL = "sk-test-4f8b2c9e-never-a-real-credential"

# The same thing in the shape a tool name can hold: letters, digits and
# underscores only, so both LLM APIs accept it and the publishing rule's
# sanitizing leaves it exactly as it is, which is precisely how a
# credential can arrive as a tool name a peer chose (#154).
TOOL_SENTINEL = "sk_test_4f8b2c9e_never_a_real_credential"


def payload_of(record: logging.LogRecord) -> dict[str, Any]:
    """The structured half of a record: exactly the attributes the JSON
    formatter emits as top-level keys.

    Read through `logs.py`'s own standard-attribute set rather than
    through a list written here, so this suite and the formatter cannot
    come to disagree about what an event field is."""
    return {key: value for key, value in vars(record).items() if key not in _STANDARD_ATTRIBUTES}


def shipped(record: logging.LogRecord) -> tuple[str, str]:
    """One record as each shipped format writes it: the JSON object a
    collector keeps, and the line a terminal prints.

    What a sentinel case hunts through, beside the payload and the
    arguments. Reading only the payload would miss a value that reached
    the rendering, and reading only the sentence would miss one that
    reached a field, so both formats are asked."""
    return (
        JsonFormatter().format(record),
        logging.Formatter(TEXT_FORMAT).format(record),
    )


class Consumer:
    """A tap that keeps what it was handed, so a claim about what
    reaches a consumer is asserted at the consumer rather than inferred
    from the log."""

    def __init__(self) -> None:
        self.seen: list[Any] = []

    def emit(self, emission: Any) -> None:
        self.seen.append(emission)


def assert_unnamed_in(record: logging.LogRecord, planted: str) -> None:
    """One record says nothing of the planted value, on any surface a
    consumer of that record can reach: the rendered sentence, the
    unrendered arguments, every field, and both shipped formats."""
    assert planted not in record.getMessage()
    assert planted not in str(record.args)
    assert planted not in str(payload_of(record))
    assert not any(planted in rendered for rendered in shipped(record))


def assert_unnamed_anywhere(
    caplog: pytest.LogCaptureFixture, consumer: Consumer, planted: str
) -> None:
    """The planted value reaches no record of the run at any level, and
    no tap.

    Both halves are load-bearing. A run's other records are where a
    value that was kept off its own event turns up on a neighbouring
    one, and a tap is handed the same arguments the record carries, so
    reading only the log would miss a consumer seeing it first."""
    assert caplog.records, "nothing was logged at all, so this proves nothing"
    for record in caplog.records:
        assert_unnamed_in(record, planted)
    assert consumer.seen, "it reached no tap at all, so this proves nothing"
    assert not any(
        planted in str(emission.payload) or planted in str(emission.args)
        for emission in consumer.seen
    )


def assert_unnamed(
    record: logging.LogRecord, consumer: Consumer, caplog: pytest.LogCaptureFixture
) -> None:
    """A tool name a peer chose reaches no part of the record that
    describes the call, no other record of the run, and no tap.

    The check every `tool_call` branch but the builtin one runs, because
    a device's tool name is the board's vocabulary and an MCP tool's is
    the far side's, and the retained surface admits neither."""
    assert_unnamed_in(record, TOOL_SENTINEL)
    assert_unnamed_anywhere(caplog, consumer, TOOL_SENTINEL)


def watched(session: Any) -> Consumer:
    """A consumer attached to one session's events."""
    consumer = Consumer()
    events_of(session).attach(consumer)
    return consumer


def uttering(text: str) -> Any:
    """A session whose ASR answers with `text`, driven below the socket.

    What the sentinel cases below plant an utterance with: the mock LLM
    quotes what it was given, so one string reaches the transcription,
    the reply and every field either of them ever carried."""
    session = device_session(config_with_agent(asr_text=text), DEVICE_MAC)
    session.websocket = cast(Any, RecordingSocket())
    return session


def sentinel_tool_config() -> Config:
    """One MCP entry, `tools`, publishing a tool under a name the test
    chooses through the entry's environment.

    The registry is built from this and the session from `base_config()`,
    which carries no MCP entries at all, so a name that turns up in a
    reply can only have come from the server."""
    return base_config(
        mcp_servers={
            "tools": {
                "transport": "stdio",
                "command": sys.executable,
                "args": [str(STDIO_SERVER)],
                "env": {SHADOWED_TOOL_ENV: TOOL_SENTINEL},
            }
        },
        agents={
            "poet": {"prompt": "POET", "tts": "tenor", "mcp": ["tools"]},
            "tutor": {"prompt": "TUTOR", "tts": "alto"},
        },
    )


# --- what a device sent, which nobody authenticated -------------------


async def test_a_rejected_device_id_reaches_no_record_at_all(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The header is bytes an unauthenticated caller chose, and these
    logs are the retained surface, so the value that was turned away
    must appear in no sentence, no argument, no field, and no record of
    any level.

    Structural now as well as asserted: the variant this path
    constructs declares one fixed reason and nothing a header could be
    carried as. The assertion stays because a variant is one edit away
    from declaring a field, and because that edit should fail here."""
    with caplog.at_level("DEBUG"):
        await turned_away(config_with_agent(), SENTINEL)

    rejected = only(caplog, "session_rejected")
    assert_unnamed_in(rejected, SENTINEL)
    assert not any(SENTINEL in record.getMessage() for record in caplog.records)
    # And the rejection still says which refusal this is.
    assert rejected.reason == "bad_device_id"  # type: ignore[attr-defined]
    assert rejected.device is None  # type: ignore[attr-defined]


# --- what was said in the room ----------------------------------------


async def test_an_utterance_reaches_no_part_of_the_heard_record(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The event says how long somebody spoke and in what language, and
    nothing about what they said (#120). A transcript is whatever was
    spoken in the room, which is the one thing on this surface nobody
    chose to publish."""
    session = uttering(SENTINEL)
    consumer = watched(session)
    with caplog.at_level("DEBUG"):
        await drive_reply(session, UTTERANCE)

    heard = only(caplog, "heard")
    assert_unnamed_in(heard, SENTINEL)
    emissions = [one for one in consumer.seen if one.payload["event"] == "heard"]
    assert emissions, "it reached no tap at all, so this proves nothing"
    assert not any(
        SENTINEL in str(emission.payload) or SENTINEL in str(emission.args)
        for emission in emissions
    )
    # And what the event exists for survives it.
    assert heard.duration_s == 0.02  # type: ignore[attr-defined]


async def test_a_whole_exchange_reaches_no_record_of_itself(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The utterance and the reply that answers it, hunted across every
    record a conversation produces rather than across one.

    The mock LLM quotes what it was given, so one planted string is the
    transcription, the history and every spoken sentence at once. With
    the text off `heard` and off `replied`, nothing a session says about
    itself carries a word of what was said in the room, at any level and
    in either format an operator can be running."""
    session = uttering(SENTINEL)
    consumer = watched(session)
    with caplog.at_level("DEBUG"):
        await drive_reply(session, UTTERANCE)

    assert only(caplog, "replied").sentences == 1  # type: ignore[attr-defined]
    assert_unnamed_anywhere(caplog, consumer, SENTINEL)
    # And the reply really was spoken, which is what makes the hunt
    # above mean something: the device was told the sentence.
    assert any(SENTINEL in sentence for sentence in spoken(session.websocket))


async def test_what_one_agent_said_before_a_handover_reaches_no_record(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The same hunt on the leg-shaped half of a reply. A handover is
    where `agent_said` fires, and it is the one event whose only reason
    to exist was to say what an agent said, so it is worth planting
    through both agents."""
    scripts = {
        "poet": ScriptedLlm([[SENTINEL, call("switch_agent", agent="tutor")]]),
        "tutor": ScriptedLlm([SENTINEL]),
    }
    session = session_for(base_config(), BOTH_MAC, scripts)
    consumer = watched(session)
    with caplog.at_level("DEBUG"):
        spoken = await run_reply(session, "get me the tutor")

    assert only(caplog, "agent_said").sentences == 1  # type: ignore[attr-defined]
    assert spoken == [SENTINEL], "the agents never spoke it, so this proves nothing"
    assert_unnamed_anywhere(caplog, consumer, SENTINEL)


# --- what a peer called a tool ----------------------------------------


async def test_tool_call_for_a_name_nobody_publishes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A name no namespace claims is the model's own invention, so the
    event names nothing at all and `source` carries the whole answer."""
    script = ScriptedLlm([[call(TOOL_SENTINEL)], "I could not do that."])
    session = session_for(base_config(), POET_MAC, {"poet": script})
    consumer = watched(session)
    with caplog.at_level("DEBUG"):
        await run_reply(session, "do it")

    called = only(caplog, "tool_call")
    assert called.source == "unknown"  # type: ignore[attr-defined]
    assert_unnamed(called, consumer, caplog)


async def test_tool_call_for_a_device_tool(caplog: pytest.LogCaptureFixture) -> None:
    """A board publishes its own tool names, so this branch names
    nothing either. The name is planted as a credential shaped to
    survive the publishing rule untouched."""
    device = FakeDevice([{"tools": [{"name": f"self.{TOOL_SENTINEL}", "description": "x"}]}])
    await device.client.discover()
    published = f"self_{TOOL_SENTINEL}"
    script = ScriptedLlm([[call(published)], "Done."])
    session = session_for(base_config(), POET_MAC, {"poet": script})
    # White-box: a board's tools arrive from a discovery run the edge
    # starts over the wire, and this session has no socket. What the
    # case needs is a device-published name on the surface at all.
    session._device_tools = device.client
    consumer = watched(session)
    with caplog.at_level("DEBUG"):
        await run_reply(session, "check the board")

    called = only(caplog, "tool_call")
    assert called.source == "device"  # type: ignore[attr-defined]
    assert_unnamed(called, consumer, caplog)


async def test_tool_call_for_an_mcp_tool(caplog: pytest.LogCaptureFixture) -> None:
    """An MCP call names the entry an operator configured and not the
    tool, which is half the far side's own bytes. The planted name is
    published by the test server through the entry's environment, so it
    is a name this project never typed into a fixture."""
    servers = McpServers.build(sentinel_tool_config())
    await servers.start_all()
    published = f"tools__{TOOL_SENTINEL}"
    script = ScriptedLlm([[call(published)], "Done."])
    session = session_for(base_config(), POET_MAC, {"poet": script}, mcp_servers=servers)
    consumer = watched(session)
    try:
        assert published in {
            tool.name for tool in servers.tools_for_agent("poet")
        }, "the planted name was never published, so this proves nothing"
        with caplog.at_level("DEBUG"):
            await run_reply(session, "ask the server")
    finally:
        await servers.stop_all()

    called = only(caplog, "tool_call")
    assert (called.source, called.entry) == ("mcp", "tools")  # type: ignore[attr-defined]
    assert_unnamed(called, consumer, caplog)


# The board tool whose argument is declared a number, which is what puts
# a model-authored string in front of a parser: `float()`'s own message
# repeats what it rejected, and `_run_one` interpolates an exception's
# message into the result the model reads.
GAIN = {
    "name": "self.audio_speaker.set_gain",
    "description": "Set the speaker gain",
    "inputSchema": {"type": "object", "properties": {"gain": {"type": "number"}}},
}


async def test_a_value_no_conversion_could_make_reaches_no_record(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The coercion at the dispatch is total, and this is what totality
    is worth (#383).

    A conversion that raised would have its message interpolated into a
    stored tool result, and the string a numeric parser rejects is the
    string it repeats. So the planted value is sent as a number the
    schema declares, refused by the grammar, and hunted through the
    result the model reads, every record of the run in both formats, and
    the tap. What the far side received is asserted too, because a
    sentinel that never left is a test proving nothing.
    """
    device = FakeDevice([{"tools": [GAIN]}])
    await device.client.discover()
    script = ScriptedLlm([[call("self_audio_speaker_set_gain", gain=SENTINEL)], "Done."])
    session = session_for(base_config(), POET_MAC, {"poet": script})
    # White-box: a board's tools arrive from a discovery run the edge
    # starts over the wire, and this session has no socket.
    session._device_tools = device.client
    consumer = watched(session)
    with caplog.at_level("DEBUG"):
        spoken = await run_reply(session, "set the gain")

    assert spoken == ["Done."], "the reply never ran, so this proves nothing"
    # The value left unchanged, which is the whole of what the refusal
    # means: the far side decides, on what the model actually sent.
    (sent,) = [one for one in device.sent if one.get("method") == "tools/call"]
    assert sent["params"]["arguments"] == {"gain": SENTINEL}
    # Nothing converted, so nothing is said about a conversion either.
    assert not events(caplog, "tool_arguments_coerced")
    (result,) = [
        result for turns, _, _ in script.seen for turn in turns for result in turn.tool_results
    ]
    assert SENTINEL not in result.content
    assert_unnamed_anywhere(caplog, consumer, SENTINEL)


# --- what a provider answered with ------------------------------------


async def test_a_failing_providers_own_words_reach_no_record(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`provider_failed` takes a `BaseException` from four call sites,
    one of them the LLM stream, so an SDK's or a transport's own
    exception can arrive unwrapped, and an exception raised near a
    response body can carry one in its message. Only the class name is
    reported, and the check runs at the consumer as well as at the log:
    a tap is handed the same arguments the record carries."""
    session, consumer = await a_failing_reply(
        "llm", ConnectionRefusedError(SENTINEL), caplog
    )

    failed = only(caplog, "provider_failed")
    assert_unnamed_anywhere(caplog, consumer, SENTINEL)
    # And the diagnosis survives it: what failed, where, and how long.
    assert failed.error == "ConnectionRefusedError"  # type: ignore[attr-defined]
    assert (
        failed.stage,  # type: ignore[attr-defined]
        failed.provider,  # type: ignore[attr-defined]
        failed.host,  # type: ignore[attr-defined]
        failed.model,  # type: ignore[attr-defined]
    ) == ("llm", "cloud", "api.example.com", "gpt-4o-mini")


async def test_a_failing_provider_the_registry_never_built_says_even_less(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The same variant saying less: one event shape since #240, and
    this is the record it makes for a provider the registry never built.
    The four entry fields are absent rather than empty, and the two
    positions the sentence renders them at are the empty forms of the
    quoted entry and the host tail. It names no configured entry at all,
    so there is even less for a message to ride out on, and the class
    name is still what the record keeps."""
    _session, consumer = await a_failing_reply(
        "asr", Failing(ConnectionRefusedError(SENTINEL)), caplog, wrapped=False
    )

    failed = only(caplog, "provider_failed")
    assert_unnamed_anywhere(caplog, consumer, SENTINEL)
    assert failed.error == "ConnectionRefusedError"  # type: ignore[attr-defined]
    assert not hasattr(failed, "provider")


async def a_failing_reply(
    stage: str, failure: Any, caplog: pytest.LogCaptureFixture, wrapped: bool = True
) -> tuple[Any, Consumer]:
    """One reply against a provider that fails, with a consumer
    attached.

    The driving is the driver harness's, which is where every session
    path is reached from now; what this adds is the sentinel in the
    exception and the tap that watches for it."""
    provider = Unreachable(stage, failure) if wrapped else failure
    consumer = Consumer()
    with caplog.at_level("DEBUG"):
        session = await failing_reply(stage, provider, watch=consumer)
    return session, consumer

