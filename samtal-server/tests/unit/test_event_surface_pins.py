"""Every structured session event, pinned exactly as it is emitted.

The retained JSON logs are the observability surface (ADR 2026-08-04)
and the transcript store until v3, so the records a session emits are
output rather than an implementation detail. The suites next door assert
what an event is about: this one asserts what it *is*. Per emit path it
pins five things, which together are the whole of what a consumer sees:

- `record.name`, the channel, which is the `logger` field of the JSON
  line;
- `record.levelno`, because a level is part of the surface (a filter set
  to INFO decides what a collector keeps);
- `record.msg`, the unrendered template, which is what catches a
  reworded sentence, a lost `%` argument, and a `%d` quietly becoming a
  `%s`;
- `record.args`, the substituted values themselves, by value and by
  type, which is what catches two arguments swapping places even where
  the rendering happens to read the same;
- the exact set of nonstandard record attributes and their values, which
  is the JSON object's own keys: `logs.py` emits precisely the
  attributes `logging` did not put there, so this suite reads them the
  same way rather than listing them by hand.

The template and the arguments are the pin. `sentence` is carried
alongside as the rendering a person reads in a review diff, with the
session id and every numeric run replaced, and it is deliberately the
weaker of the two: numeric literals inside it are not pinned at all,
which is precisely why the two fields above it exist.

Values that move between runs are named rather than guessed. `dynamic=`
names the payload fields whose value is not pinned (the key still is),
and `dynamic_args=` the argument positions, which keep their type as
`<float>` or `<ConnectionRefusedError>` so a duration that turned into
a string is still a failure. Argument 0 is the session id in every one
of these sentences, since each opens with "session %s", so it is
normalized without being declared.

Written before the emitter moved out of `device/events.py` (#138,
milestone 1) and left untouched through the move, which is what makes it
evidence rather than a description: the surface it pins is the one that
existed before the reshape. Strengthened afterwards by the PR #152
review round, which found the sentence normalization too generous to
catch what this docstring claimed of it, and which deliberately blunted
one pinned sentence: the bad-Device-Id rejection no longer renders the
header a caller submitted. A pin says a sentence is what it is, not
that it is safe, so that one is guarded by a sentinel case beside it.
"""

import asyncio
import logging
import re
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from samtal_server.app import create_app
from samtal_server.config import Config
from samtal_server.device.bindings import DeviceAgents
from samtal_server.device.session import DeviceSession
from samtal_server.logs import _STANDARD_ATTRIBUTES
from samtal_server.providers import AsrResult, build_agent_providers
from samtal_server.runtime.pipeline import bespoke_runtime_factory
from samtal_server.tools.mcp import McpServers
from tests.unit.test_session import (
    DEVICE_MAC,
    DEVICE_UUID,
    RecordingSocket,
    config_with_agent,
    connect,
    say_something,
    shake_hands,
    speech_pcm,
)
from tests.unit.test_session_barge_in import (
    ConfirmingAsr,
    GatedAsr,
    ScriptedEndpointer,
    realtime_session,
)
from tests.unit.test_session_events import only, reply_with
from tests.unit.test_session_filler import (
    DELAY_MS,
    SPEECH,
    masked_config,
    masked_session,
)
from tests.unit.test_session_limits import (
    capped_config,
    idle_config,
    listen_realtime,
    wait_for_close,
)
from tests.unit.test_session_tools import (
    BOTH_MAC,
    POET_MAC,
    ScriptedLlm,
    base_config,
    call,
    run_reply,
    session_for,
)
from tests.unit.test_session_watchdog import STALL_S, StallingLlm, watchdog_config

# The utterance the direct drivers hand a reply: 20 ms of silence, which
# the mock ASR answers whatever it holds.
UTTERANCE = b"\x00\x00" * 320

# Not a real credential, and shaped so a substring check for it cannot
# match by accident. It stands for whatever a caller puts in a header,
# or a far side in an error, that nobody wrote for a log line.
SENTINEL = "sk-test-4f8b2c9e-never-a-real-credential"

# What a value that moves between runs is replaced by, so that the key
# is pinned and the value deliberately is not.
DYNAMIC = "<dynamic>"

# And what the session id is replaced by, which is worth telling apart
# from the rest: it is the one dynamic value every sentence carries.
SESSION = "<session>"

# A session id is a uuid4 hex, and it appears in the sentence as well as
# in the fields.
_SESSION_ID = re.compile(r"\b[0-9a-f]{32}\b")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")

# The client id as it survives the numeric normalization above. Spelled
# out here rather than inline, because the digits of a uuid go the way
# the digits of a duration go and the result is unreadable in a literal.
CLIENT = _NUMBER.sub("<n>", DEVICE_UUID)


def payload_of(record: logging.LogRecord) -> dict[str, Any]:
    """The structured half of a record: exactly the attributes the JSON
    formatter emits as top-level keys.

    Read through `logs.py`'s own standard-attribute set rather than
    through a list written here, so this suite and the formatter cannot
    come to disagree about what an event field is."""
    return {key: value for key, value in vars(record).items() if key not in _STANDARD_ATTRIBUTES}


def sentence_of(record: logging.LogRecord) -> str:
    """The rendered human sentence, with the values that move between
    runs standing in for themselves. The readable half of the pin, and
    the weaker one: see the module docstring."""
    return _NUMBER.sub("<n>", _SESSION_ID.sub(SESSION, record.getMessage()))


def args_of(record: logging.LogRecord, dynamic_args: tuple[int, ...]) -> tuple[Any, ...]:
    """The values substituted into the template, in order.

    A declared-dynamic position keeps its type rather than its value,
    so a duration that stopped being a float is still a failure. The
    first position is the session id in every sentence here."""
    return tuple(
        SESSION
        if index == 0
        else (f"<{type(value).__name__}>" if index in dynamic_args else value)
        for index, value in enumerate(record.args or ())
    )


def pinned(
    record: logging.LogRecord,
    dynamic: tuple[str, ...] = (),
    dynamic_args: tuple[int, ...] = (),
) -> dict[str, Any]:
    """What one emit path produces, in the dimensions a consumer sees."""
    fields = {
        key: DYNAMIC if key == "session" or key in dynamic else value
        for key, value in payload_of(record).items()
    }
    return {
        "logger": record.name,
        "level": record.levelno,
        "template": record.msg,
        "args": args_of(record, dynamic_args),
        "sentence": sentence_of(record),
        "fields": fields,
    }


# --- the three rejections, which happen before a runtime exists --------


class TurnedAwaySocket:
    """Just enough websocket for a connection that is refused: the
    handshake headers, the accept, and the close."""

    def __init__(self, device_id: str) -> None:
        self.headers = {"device-id": device_id, "client-id": DEVICE_UUID}

    async def accept(self) -> None:
        return None

    async def close(self, code: int, reason: str) -> None:
        return None


class ScriptedBindings:
    """A bindings view whose answer is written down, so the two
    no-agent rejections are driven without a database behind them. The
    resolution is the only thing these paths ask of it."""

    def __init__(self, resolution: DeviceAgents) -> None:
        self._resolution = resolution

    async def resolve(self, mac: str) -> DeviceAgents:
        return self._resolution


async def turned_away(
    config: Config, device_id: str, resolution: DeviceAgents | None = None
) -> None:
    """One connection that never becomes a session."""
    factory = bespoke_runtime_factory(
        config, build_agent_providers(config), McpServers({}), None, {}
    )
    session = DeviceSession(
        cast(Any, TurnedAwaySocket(device_id)),
        config,
        factory,
        bindings=None if resolution is None else cast(Any, ScriptedBindings(resolution)),
    )
    await session.run()


async def test_session_rejected_bad_device_id(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING"):
        await turned_away(config_with_agent(), "not-a-mac")

    assert pinned(only(caplog, "session_rejected")) == {
        "logger": "samtal_server.session",
        "level": logging.WARNING,
        "template": (
            "session %s rejected: the Device-Id header is not a device MAC "
            "(six colon-separated hex pairs)"
        ),
        "args": (SESSION,),
        "sentence": (
            "session <session> rejected: the Device-Id header is not a device MAC "
            "(six colon-separated hex pairs)"
        ),
        "fields": {
            "event": "session_rejected",
            "session": DYNAMIC,
            "device": None,
            "reason": "bad_device_id",
        },
    }


async def test_a_rejected_device_id_reaches_no_record_at_all(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A pin says the sentence is what it is; it does not say the
    sentence is safe. The header is bytes an unauthenticated caller
    chose, and these logs are the retained surface, so the value that
    was turned away must appear in no sentence, no argument, no field,
    and no record of any level."""
    with caplog.at_level("DEBUG"):
        await turned_away(config_with_agent(), SENTINEL)

    rejected = only(caplog, "session_rejected")
    assert SENTINEL not in rejected.getMessage()
    assert SENTINEL not in str(rejected.args)
    assert SENTINEL not in str(payload_of(rejected))
    assert not any(SENTINEL in record.getMessage() for record in caplog.records)


async def test_session_rejected_no_agent(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING"):
        await turned_away(config_with_agent(), DEVICE_MAC, DeviceAgents(agents=()))

    assert pinned(only(caplog, "session_rejected")) == {
        "logger": "samtal_server.session",
        "level": logging.WARNING,
        "template": (
            "session %s rejected: device %s has no agent: bind it under devices "
            "or set default_agent"
        ),
        "args": (SESSION, DEVICE_MAC.lower()),
        "sentence": (
            "session <session> rejected: device aa:bb:cc:dd:ee:ff has no agent: "
            "bind it under devices or set default_agent"
        ),
        "fields": {
            "event": "session_rejected",
            "session": DYNAMIC,
            "device": DEVICE_MAC.lower(),
            "reason": "no_agent",
        },
    }


async def test_session_rejected_agent_not_loaded(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING"):
        await turned_away(
            config_with_agent(), DEVICE_MAC, DeviceAgents(agents=(), unloaded=("poet",))
        )

    assert pinned(only(caplog, "session_rejected")) == {
        "logger": "samtal_server.session",
        "level": logging.WARNING,
        "template": (
            "session %s rejected: device %s is bound to agent %s, which this "
            "server has not loaded; restart to load it"
        ),
        "args": (SESSION, DEVICE_MAC.lower(), "poet"),
        "sentence": (
            "session <session> rejected: device aa:bb:cc:dd:ee:ff is bound to agent poet, "
            "which this server has not loaded; restart to load it"
        ),
        "fields": {
            "event": "session_rejected",
            "session": DYNAMIC,
            "device": DEVICE_MAC.lower(),
            "reason": "agent_not_loaded",
        },
    }


# --- the session's own brackets, driven over a real socket ------------


def hold_a_conversation(config: Config) -> None:
    with TestClient(create_app(config)) as client:
        with connect(client) as websocket:
            shake_hands(websocket)
            say_something(websocket)


def test_session_open(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("INFO"):
        hold_a_conversation(config_with_agent())

    assert pinned(only(caplog, "session_open"), dynamic=("revision",)) == {
        "logger": "samtal_server.session",
        "level": logging.INFO,
        "template": (
            "session %s open: device %s (client %s) agent %s%s, protocol v%d, "
            "%d Hz %d ms frames in"
        ),
        "args": (SESSION, DEVICE_MAC.lower(), DEVICE_UUID, "assistant", "", 1, 16000, 60),
        "sentence": (
            f"session <session> open: device aa:bb:cc:dd:ee:ff (client {CLIENT}) "
            "agent assistant, protocol v<n>, <n> Hz <n> ms frames in"
        ),
        "fields": {
            "event": "session_open",
            "session": DYNAMIC,
            "device": DEVICE_MAC.lower(),
            "client": DEVICE_UUID,
            "agent": "assistant",
            "agents": ["assistant"],
            "protocol": 1,
            "revision": DYNAMIC,
        },
    }


def test_session_closed(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("INFO"):
        hold_a_conversation(config_with_agent())

    assert pinned(only(caplog, "session_closed"), dynamic=("duration_s",)) == {
        "logger": "samtal_server.session",
        "level": logging.INFO,
        "template": "session %s closed (device %s)",
        "args": (SESSION, DEVICE_MAC.lower()),
        "sentence": "session <session> closed (device aa:bb:cc:dd:ee:ff)",
        "fields": {
            "event": "session_closed",
            "session": DYNAMIC,
            "device": DEVICE_MAC.lower(),
            "duration_s": DYNAMIC,
        },
    }


def test_session_limit(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("INFO"):
        with TestClient(create_app(capped_config(0.3))) as client:
            with connect(client) as websocket:
                shake_hands(websocket)
                wait_for_close(websocket)

    assert pinned(only(caplog, "session_limit"), dynamic=("duration_s",)) == {
        "logger": "samtal_server.session",
        "level": logging.INFO,
        "template": "session %s reached the %.0f s time limit",
        "args": (SESSION, 0.3),
        "sentence": "session <session> reached the <n> s time limit",
        "fields": {
            "event": "session_limit",
            "session": DYNAMIC,
            "device": DEVICE_MAC.lower(),
            "duration_s": DYNAMIC,
        },
    }


def test_session_idle(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("INFO"):
        with TestClient(create_app(idle_config(0.3))) as client:
            with connect(client) as websocket:
                shake_hands(websocket)
                listen_realtime(websocket)
                wait_for_close(websocket)

    assert pinned(only(caplog, "session_idle"), dynamic=("duration_s",)) == {
        "logger": "samtal_server.session",
        "level": logging.INFO,
        "template": "session %s idle for %.0f s, hanging up",
        "args": (SESSION, 0.3),
        "sentence": "session <session> idle for <n> s, hanging up",
        "fields": {
            "event": "session_idle",
            "session": DYNAMIC,
            "device": DEVICE_MAC.lower(),
            "idle_s": 0.3,
            "duration_s": DYNAMIC,
        },
    }


def test_speaking_started(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("INFO"):
        hold_a_conversation(config_with_agent())

    assert pinned(only(caplog, "speaking_started")) == {
        "logger": "samtal_server.session",
        "level": logging.INFO,
        "template": "session %s: speaking started",
        "args": (SESSION,),
        "sentence": "session <session>: speaking started",
        "fields": {
            "event": "speaking_started",
            "session": DYNAMIC,
            "device": DEVICE_MAC.lower(),
            "agent": "assistant",
        },
    }


# --- the runtime's events, driven below the socket --------------------


def speaking_session(scripts: dict[str, Any] | None = None, mac: str = POET_MAC):
    """A session on a recording socket, which is what makes a reply run
    all the way through speaking."""
    session = session_for(base_config(), mac, cast(Any, scripts))
    session.websocket = cast(Any, RecordingSocket())
    return session


async def test_prompt_assembled(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("INFO"):
        session_for(base_config(), POET_MAC)

    assert pinned(only(caplog, "prompt_assembled")) == {
        "logger": "samtal_server.session",
        "level": logging.INFO,
        "template": "session %s: assembled %d characters of prompt for %s",
        "args": (SESSION, 4, "poet"),
        "sentence": "session <session>: assembled <n> characters of prompt for poet",
        "fields": {
            "event": "prompt_assembled",
            "session": DYNAMIC,
            "device": None,
            "agent": "poet",
            "characters": 4,
            "sources": {"persona": 4},
        },
    }


async def test_heard(caplog: pytest.LogCaptureFixture) -> None:
    session = speaking_session({"poet": ScriptedLlm(["Two words."])})
    with caplog.at_level("INFO"):
        await session.runtime._reply(UTTERANCE)

    assert pinned(only(caplog, "heard")) == {
        "logger": "samtal_server.session",
        "level": logging.INFO,
        "template": 'session %s: heard "%s"',
        "args": (SESSION, "hello"),
        "sentence": 'session <session>: heard "hello"',
        "fields": {
            "event": "heard",
            "session": DYNAMIC,
            "device": None,
            "agent": "poet",
            "text": "hello",
            "duration_s": 0.02,
        },
    }


async def test_replied(caplog: pytest.LogCaptureFixture) -> None:
    session = speaking_session({"poet": ScriptedLlm(["Two words."])})
    with caplog.at_level("INFO"):
        await session.runtime._reply(UTTERANCE)

    assert pinned(only(caplog, "replied")) == {
        "logger": "samtal_server.session",
        "level": logging.INFO,
        "template": 'session %s: replied "%s"',
        "args": (SESSION, "Two words."),
        "sentence": 'session <session>: replied "Two words."',
        "fields": {
            "event": "replied",
            "session": DYNAMIC,
            "device": None,
            "agent": "poet",
            "text": "Two words.",
        },
    }


async def test_llm_round(caplog: pytest.LogCaptureFixture) -> None:
    session = speaking_session({"poet": ScriptedLlm(["Two words."])})
    with caplog.at_level("INFO"):
        await session.runtime._reply(UTTERANCE)

    assert pinned(
        only(caplog, "llm_round"),
        dynamic=("duration_ms", "first_token_ms"),
        dynamic_args=(3,),
    ) == {
        "logger": "samtal_server.session",
        "level": logging.INFO,
        "template": "session %s: %s round %d took %.2f s over %d turns",
        "args": (SESSION, "poet", 1, "<float>", 1),
        "sentence": "session <session>: poet round <n> took <n> s over <n> turns",
        "fields": {
            "event": "llm_round",
            "session": DYNAMIC,
            "device": None,
            "agent": "poet",
            "round": 1,
            "turns": 1,
            "duration_ms": DYNAMIC,
            "stage": "llm",
            "provider": "mock",
            "type": "mock",
            "first_token_ms": DYNAMIC,
        },
    }


async def test_agent_said_and_handover(caplog: pytest.LogCaptureFixture) -> None:
    scripts = {
        "poet": ScriptedLlm([["Handing you over.", call("switch_agent", agent="tutor")]]),
        "tutor": ScriptedLlm(["Hello, I am the tutor."]),
    }
    session = session_for(base_config(), BOTH_MAC, scripts)
    with caplog.at_level("INFO"):
        await run_reply(session, "get me the tutor")

    assert pinned(only(caplog, "agent_said")) == {
        "logger": "samtal_server.session",
        "level": logging.INFO,
        "template": 'session %s: %s said "%s"',
        "args": (SESSION, "poet", "Handing you over."),
        "sentence": 'session <session>: poet said "Handing you over."',
        "fields": {
            "event": "agent_said",
            "session": DYNAMIC,
            "device": None,
            "agent": "poet",
            "text": "Handing you over.",
        },
    }
    assert pinned(only(caplog, "handover")) == {
        "logger": "samtal_server.session",
        "level": logging.INFO,
        "template": "session %s: handed over from agent %s to %s",
        "args": (SESSION, "poet", "tutor"),
        "sentence": "session <session>: handed over from agent poet to tutor",
        "fields": {
            "event": "handover",
            "session": DYNAMIC,
            "device": None,
            "from_agent": "poet",
            "to_agent": "tutor",
        },
    }


async def test_tool_call(caplog: pytest.LogCaptureFixture) -> None:
    script = ScriptedLlm([[call("ghost_tool")], "I could not do that."])
    session = session_for(base_config(), POET_MAC, {"poet": script})
    with caplog.at_level("INFO"):
        await run_reply(session, "do it")

    assert pinned(only(caplog, "tool_call"), dynamic=("duration_ms",), dynamic_args=(2,)) == {
        "logger": "samtal_server.session",
        "level": logging.INFO,
        "template": "session %s: tool %s took %.2f s%s",
        "args": (SESSION, "ghost_tool", "<float>", " and failed"),
        "sentence": "session <session>: tool ghost_tool took <n> s and failed",
        "fields": {
            "event": "tool_call",
            "session": DYNAMIC,
            "device": None,
            "agent": "poet",
            "tool": "ghost_tool",
            "duration_ms": DYNAMIC,
            "is_error": True,
        },
    }


async def test_llm_retry(caplog: pytest.LogCaptureFixture) -> None:
    llm = StallingLlm(delays=[STALL_S, 0.0])
    session = session_for(watchdog_config(), POET_MAC, {"poet": cast(Any, llm)})
    with caplog.at_level("INFO"):
        await run_reply(session, "are you there")

    assert pinned(only(caplog, "llm_retry"), dynamic=("duration_ms",), dynamic_args=(1,)) == {
        "logger": "samtal_server.session",
        "level": logging.WARNING,
        "template": "session %s: no first token after %.1f s, retrying round %d",
        "args": (SESSION, "<float>", 1),
        "sentence": "session <session>: no first token after <n> s, retrying round <n>",
        "fields": {
            "event": "llm_retry",
            "session": DYNAMIC,
            "device": None,
            "agent": "poet",
            "round": 1,
            "duration_ms": DYNAMIC,
            "stage": "llm",
            "provider": "mock",
            "type": "mock",
        },
    }


async def test_provider_failed(caplog: pytest.LogCaptureFixture) -> None:
    failed = await reply_with("asr", ConnectionRefusedError("no route"), caplog)

    assert pinned(failed, dynamic=("duration_ms",), dynamic_args=(4, 7)) == {
        "logger": "samtal_server.session",
        "level": logging.WARNING,
        "template": "session %s: %s provider%s %s after %.2f s%s: %s: %s",
        "args": (
            SESSION,
            "asr",
            ' "cloud"',
            "failed",
            "<float>",
            " reaching api.example.com",
            "ConnectionRefusedError",
            "<ConnectionRefusedError>",
        ),
        "sentence": (
            'session <session>: asr provider "cloud" failed after <n> s '
            "reaching api.example.com: ConnectionRefusedError: no route"
        ),
        "fields": {
            "event": "provider_failed",
            "session": DYNAMIC,
            "device": POET_MAC,
            "agent": "poet",
            "error": "ConnectionRefusedError",
            "duration_ms": DYNAMIC,
            "stage": "asr",
            "provider": "cloud",
            "type": "openai",
            "host": "api.example.com",
        },
    }


# --- the barge-in gates, each driven onto its own decision ------------


async def test_barge_in_on_a_manual_stop(caplog: pytest.LogCaptureFixture) -> None:
    """The unconditional cancel in `_finish_utterance`: no gate ran, and
    the reply had not spoken, so no speaking_ms is carried."""
    asr = GatedAsr()
    session, _ = realtime_session(config_with_agent(), asr)
    session.runtime._endpointer = ScriptedEndpointer(speech_ms=600)

    with caplog.at_level("INFO"):
        session.runtime._utterance = bytearray(speech_pcm(320))
        await session.runtime._finish_utterance(endpointed=True)
        await asyncio.sleep(0.05)
        session.runtime._utterance = bytearray(speech_pcm(320))
        await session.runtime._finish_utterance()
        asr.release.set()
        assert session.runtime._reply_task is not None
        await session.runtime._reply_task

    assert pinned(only(caplog, "barge_in")) == {
        "logger": "samtal_server.session",
        "level": logging.INFO,
        "template": "session %s: barge-in, cancelling the reply in flight",
        "args": (SESSION,),
        "sentence": "session <session>: barge-in, cancelling the reply in flight",
        "fields": {
            "event": "barge_in",
            "session": DYNAMIC,
            "device": None,
            "speech_ms": 600,
        },
    }


async def test_barge_in_suppressed_under_the_speech_floor(
    caplog: pytest.LogCaptureFixture,
) -> None:
    asr = GatedAsr()
    session, _ = realtime_session(config_with_agent(), asr)
    session.runtime._endpointer = ScriptedEndpointer(speech_ms=600)

    with caplog.at_level("INFO"):
        session.runtime._utterance = bytearray(speech_pcm(320))
        await session.runtime._finish_utterance(endpointed=True)
        await asyncio.sleep(0.05)
        session.runtime._endpointer = ScriptedEndpointer(speech_ms=100)
        session.runtime._utterance = bytearray(speech_pcm(320))
        await session.runtime._finish_utterance(endpointed=True)
        asr.release.set()
        assert session.runtime._reply_task is not None
        await session.runtime._reply_task

    assert pinned(only(caplog, "barge_in_suppressed")) == {
        "logger": "samtal_server.session",
        "level": logging.INFO,
        "template": (
            "session %s: barge-in suppressed, %d ms of speech is under the "
            "%.0f ms floor"
        ),
        "args": (SESSION, 100, 500.0),
        "sentence": (
            "session <session>: barge-in suppressed, <n> ms of speech is under "
            "the <n> ms floor"
        ),
        "fields": {
            "event": "barge_in_suppressed",
            "session": DYNAMIC,
            "device": None,
            "reason": "min_speech",
            "speech_ms": 100,
        },
    }


async def test_barge_in_merged_mid_transcription(caplog: pytest.LogCaptureFixture) -> None:
    asr = GatedAsr()
    session, _ = realtime_session(config_with_agent(), asr)
    session.runtime._endpointer = ScriptedEndpointer(speech_ms=600)

    with caplog.at_level("INFO"):
        session.runtime._utterance = bytearray(speech_pcm(320))
        await session.runtime._finish_utterance(endpointed=True)
        await asyncio.sleep(0.05)
        session.runtime._utterance = bytearray(speech_pcm(480))
        await session.runtime._finish_utterance(endpointed=True)
        asr.release.set()
        assert session.runtime._reply_task is not None
        await session.runtime._reply_task

    assert pinned(only(caplog, "barge_in_merged")) == {
        "logger": "samtal_server.session",
        "level": logging.INFO,
        "template": "session %s: barge-in mid-transcription, merging the utterances",
        "args": (SESSION,),
        "sentence": "session <session>: barge-in mid-transcription, merging the utterances",
        "fields": {
            "event": "barge_in_merged",
            "session": DYNAMIC,
            "device": None,
            "speech_ms": 600,
        },
    }


async def speaking_reply(config: Config, asr: Any):
    """A session whose reply is past its own ASR and already speaking,
    which is where the last two gates are reached from."""
    session, socket = realtime_session(config, asr)
    session.runtime._endpointer = ScriptedEndpointer(speech_ms=600)
    session.runtime._reply_task = asyncio.create_task(
        session.runtime._reply(speech_pcm(600))
    )
    while socket.frames < 3:
        await asyncio.sleep(0.02)
    return session


async def test_barge_in_suppressed_inside_the_refractory_window(
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = config_with_agent(
        llm_reply="Hold the thought while this sentence finishes playing out loud.",
        server={"barge_in_refractory_ms": 100_000},
    )
    asr = ConfirmingAsr(AsrResult(text="stop"))
    asr.release.set()
    session = await speaking_reply(config, asr)

    with caplog.at_level("INFO"):
        session.runtime._utterance = bytearray(speech_pcm(600))
        await session.runtime._finish_utterance(endpointed=True)
        assert session.runtime._reply_task is not None
        await session.runtime._reply_task

    assert pinned(only(caplog, "barge_in_suppressed")) == {
        "logger": "samtal_server.session",
        "level": logging.INFO,
        "template": "session %s: barge-in suppressed inside the refractory window",
        "args": (SESSION,),
        "sentence": "session <session>: barge-in suppressed inside the refractory window",
        "fields": {
            "event": "barge_in_suppressed",
            "session": DYNAMIC,
            "device": None,
            "reason": "refractory",
            "speech_ms": 600,
        },
    }


async def test_barge_in_suppressed_with_nothing_transcribed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = config_with_agent(
        llm_reply="Hold the thought while this sentence finishes playing out loud.",
        server={"barge_in_refractory_ms": 0},
    )
    asr = ConfirmingAsr(AsrResult(text=""))
    asr.release.set()
    session = await speaking_reply(config, asr)

    with caplog.at_level("INFO"):
        session.runtime._utterance = bytearray(speech_pcm(600))
        await session.runtime._finish_utterance(endpointed=True)
        assert session.runtime._reply_task is not None
        await session.runtime._reply_task

    assert pinned(only(caplog, "barge_in_suppressed")) == {
        "logger": "samtal_server.session",
        "level": logging.INFO,
        "template": "session %s: barge-in suppressed, nothing transcribed",
        "args": (SESSION,),
        "sentence": "session <session>: barge-in suppressed, nothing transcribed",
        "fields": {
            "event": "barge_in_suppressed",
            "session": DYNAMIC,
            "device": None,
            "reason": "no_transcript",
            "speech_ms": 600,
        },
    }


async def test_barge_in_confirmed_by_a_transcript(caplog: pytest.LogCaptureFixture) -> None:
    """The gate's own cancel, which unlike the manual one fires while
    the reply is speaking and therefore carries speaking_ms."""
    config = config_with_agent(
        llm_reply="Answering {text}.", server={"barge_in_refractory_ms": 0}
    )
    asr = ConfirmingAsr(AsrResult(text="stop and listen"))
    asr.release.set()
    session = await speaking_reply(config, asr)

    with caplog.at_level("INFO"):
        session.runtime._utterance = bytearray(speech_pcm(600))
        await session.runtime._finish_utterance(endpointed=True)
        assert session.runtime._reply_task is not None
        await session.runtime._reply_task

    assert pinned(only(caplog, "barge_in"), dynamic=("speaking_ms",)) == {
        "logger": "samtal_server.session",
        "level": logging.INFO,
        "template": "session %s: barge-in, cancelling the reply in flight",
        "args": (SESSION,),
        "sentence": "session <session>: barge-in, cancelling the reply in flight",
        "fields": {
            "event": "barge_in",
            "session": DYNAMIC,
            "device": None,
            "speech_ms": 600,
            "speaking_ms": DYNAMIC,
        },
    }


# --- the latency mask -------------------------------------------------


async def test_filler_played(caplog: pytest.LogCaptureFixture) -> None:
    session = await masked_session(masked_config(), POET_MAC, {"poet": StallingLlm([STALL_S])})
    with caplog.at_level("INFO"):
        await session.runtime._reply(UTTERANCE)

    assert pinned(only(caplog, "filler_played"), dynamic=("delay_ms",), dynamic_args=(1,)) == {
        "logger": "samtal_server.session",
        "level": logging.INFO,
        "template": "session %s: no reply audio after %d ms, playing filler %d",
        "args": (SESSION, "<int>", 0),
        "sentence": "session <session>: no reply audio after <n> ms, playing filler <n>",
        "fields": {
            "event": "filler_played",
            "session": DYNAMIC,
            "device": None,
            "agent": "poet",
            "delay_ms": DYNAMIC,
            "phrase_index": 0,
        },
    }


async def test_filler_skipped_for_a_user_still_speaking(
    caplog: pytest.LogCaptureFixture,
) -> None:
    session = await masked_session(masked_config(), POET_MAC, {"poet": StallingLlm([STALL_S])})
    with caplog.at_level("INFO"):
        session.runtime._reply_task = asyncio.create_task(session.runtime._reply(UTTERANCE))
        await asyncio.sleep(DELAY_MS / 1000 / 3)
        assert session.runtime._endpointer is not None
        session.runtime._endpointer.feed(SPEECH)
        await session.runtime._reply_task

    assert pinned(
        only(caplog, "filler_skipped"), dynamic=("speech_ms",), dynamic_args=(1,)
    ) == {
        "logger": "samtal_server.session",
        "level": logging.INFO,
        "template": "session %s: filler skipped, the user is speaking (%d ms heard)",
        "args": (SESSION, "<int>"),
        "sentence": "session <session>: filler skipped, the user is speaking (<n> ms heard)",
        "fields": {
            "event": "filler_skipped",
            "session": DYNAMIC,
            "device": None,
            "agent": "poet",
            "reason": "user_speaking",
            "speech_ms": DYNAMIC,
        },
    }


async def test_filler_skipped_while_a_barge_in_is_confirmed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    session = await masked_session(masked_config(), POET_MAC, {"poet": StallingLlm([STALL_S])})
    with caplog.at_level("INFO"):
        session.runtime._reply_task = asyncio.create_task(session.runtime._reply(UTTERANCE))
        await asyncio.sleep(DELAY_MS / 1000 / 3)
        session.runtime._pause_output()
        await asyncio.sleep(DELAY_MS / 1000)
        session.runtime._resume_output()
        await session.runtime._reply_task

    assert pinned(only(caplog, "filler_skipped")) == {
        "logger": "samtal_server.session",
        "level": logging.INFO,
        "template": "session %s: filler skipped, a barge-in is being confirmed",
        "args": (SESSION,),
        "sentence": "session <session>: filler skipped, a barge-in is being confirmed",
        "fields": {
            "event": "filler_skipped",
            "session": DYNAMIC,
            "device": None,
            "agent": "poet",
            "reason": "barge_in_pending",
        },
    }
