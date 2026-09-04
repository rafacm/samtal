"""A reply that leaked a tool call into its own speech.

`test_speech_guard.py` is the rule; this is the reply path around it.
What the rule decides is read here through the things that receive a
reply: what the device was told it is about to hear, what the model is
handed on the next round, what the store kept, and what the logs say
happened.

Four questions, in the order they matter. Which sentences survive, and
that a leak beside a real answer costs only the leak. That both of the
tool loop's sentence sites go through the same door, one sentence
leaving through `push` and one through `flush`. That a reply left with
nothing to say falls back on the phrase #384 cached, and that a reply
which said something first does not. And that a withheld sentence
reaches no surface anybody keeps, which is asserted with a planted
secret rather than by reading the code that drops it.
"""

import json
from typing import Any, cast

import pytest

from tests.support.configs import BOTH_MAC, POET_MAC, base_config
from tests.support.device_tools import FakeDevice
from tests.support.events import both_formats, events, fields_of, only
from tests.support.providers import ScriptedLlm, built_world
from tests.support.records import SpyStore
from tests.support.sessions import call, drive_reply, run_reply, session_for
from tests.support.sockets import OrderedSocket
from vinga_server.config import Config
from vinga_server.device.session import DeviceSession
from vinga_server.filler import build_agent_fillers
from vinga_server.providers.base import TtsProvider
from vinga_server.providers.mock import MockTts

# One frame of silence, which the mock ASR answers with "hello" whatever
# it holds: these tests are about what the reply speaks, not what was
# said to it.
UTTERANCE = b"\x00\x00" * 320

# A leaked call to a builtin every agent here is offered. No sentence
# ending anywhere in it, which matters for the flush case below: the
# splitter can only cut it at a newline, so a copy without one cannot
# leave through `push`.
LEAK = '{"name": "remember", "arguments": {"text": "I like tea"}}'

# Planted where a real secret plausibly ends up: inside the arguments of
# a call a model wrote into its own speech, which is the text this whole
# feature exists to keep off the speaker and out of the record.
SENTINEL = "sk-test-1d0c7a6b-never-a-real-credential"
LEAKED_SECRET = json.dumps({"name": "remember", "arguments": {"text": SENTINEL}})


async def phrases(config: Config) -> dict[str, Any]:
    """The fallback phrases a boot would have cached for this world.

    A session is handed them the way `app.py` hands them over, because
    what the empty-reply check does is ask the runner for one: a session
    built without them has nothing to say and would pass a test about
    saying nothing for the wrong reason.
    """
    return (await build_agent_fillers(config, built_world(config).agents)).fallbacks


async def speaking_session(
    config: Config | None = None,
    mac: str = POET_MAC,
    scripts: dict[str, Any] | None = None,
    conversations: Any = None,
) -> DeviceSession:
    """A session that really speaks, onto a socket that keeps the order,
    with this world's fallback phrases cached."""
    settled = config if config is not None else base_config()
    session = session_for(
        settled,
        mac,
        scripts,
        conversations=conversations,
        fallbacks=await phrases(settled),
    )
    session.websocket = cast(Any, OrderedSocket())
    return session


def wire(session: DeviceSession) -> OrderedSocket:
    return cast(OrderedSocket, session.websocket)


# --- which sentences survive ------------------------------------------


async def test_a_leak_beside_a_real_sentence_costs_only_the_leak(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The issue's own constraint: the test is on the sentence about to
    be spoken, so one bad sentence does not discard a good answer."""
    script = ScriptedLlm([f"Noted.\n{LEAK}\n"])
    session = session_for(base_config(), POET_MAC, {"poet": script})

    with caplog.at_level("INFO"):
        assert await run_reply(session, "remember that I like tea") == ["Noted."]

    withheld = only(caplog, "sentence_withheld")
    assert fields_of(withheld)["tool"] == "remember"
    assert fields_of(withheld)["characters"] == len(LEAK)


async def test_a_leak_that_ends_the_reply_leaves_through_the_flush_tail(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The other of the two sentence sites. `LEAK` holds no
    sentence-ending punctuation, so with no newline behind it the
    splitter cannot cut it at all and it reaches the guard as the
    unterminated tail `flush` answers with."""
    script = ScriptedLlm([f"Noted.\n{LEAK}"])
    session = session_for(base_config(), POET_MAC, {"poet": script})

    with caplog.at_level("INFO"):
        assert await run_reply(session, "remember that I like tea") == ["Noted."]

    assert fields_of(only(caplog, "sentence_withheld"))["tool"] == "remember"


async def test_a_sentence_about_json_is_still_spoken() -> None:
    """The narrowness, through the reply path rather than the rule: an
    agent explaining JSON is an ordinary conversation."""
    answer = "JSON writes objects inside braces, like this one does."
    script = ScriptedLlm([answer])
    session = session_for(base_config(), POET_MAC, {"poet": script})

    assert await run_reply(session, "what is json") == [answer]


# --- what replaces a reply that said nothing --------------------------


async def test_a_reply_that_is_only_a_leaked_call_says_the_fallback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The handover between the two halves of this plan. Nothing failed
    and nothing was spoken, which is exactly the silence the cached
    phrase exists to end, so it goes out under its own reason."""
    session = await speaking_session(scripts={"poet": ScriptedLlm([LEAK])})

    with caplog.at_level("INFO"):
        await drive_reply(session, UTTERANCE)

    assert fields_of(only(caplog, "reply_fallback"))["reason"] == "nothing_sayable"
    assert len(wire(session).announced()) == 1
    assert wire(session).frames > 0
    assert wire(session).closing_stop()
    # And nothing says the reply failed, because it did not.
    assert ": reply failed: " not in caplog.text


async def test_the_mask_is_waited_out_before_the_phrase() -> None:
    """The notice waits for a clip still sounding, exactly as the
    failure arm's does.

    A reply that spoke nothing sent no batch, so the tail wait inside
    `_send_reply_audio` was never reached and this is the first thing in
    the turn that waits for the mask at all. Without the wait the notice
    would talk over a filled pause and interleave the shared encoder,
    and the settle is also what lets a barge-in confirmed during it take
    the turn rather than be swallowed.

    Read as the order the two verbs were called in. White-box in the
    reach and exact in what it claims: what the arm promises is that it
    settles before it speaks, and no surface outside the runner can say
    which of two sends happened first once the pacing has had its way
    with them.
    """
    session = await speaking_session(scripts={"poet": ScriptedLlm([LEAK])})
    runner = session.runtime._filler
    order: list[str] = []
    settle, speak = runner.settle, runner.speak_fallback

    async def watched_settle() -> None:
        order.append("settle")
        await settle()

    async def watched_speak(reason: Any) -> None:
        order.append("speak")
        await speak(reason)

    runner.settle = watched_settle
    runner.speak_fallback = watched_speak

    await drive_reply(session, UTTERANCE)

    assert order[:2] == ["settle", "speak"]


async def test_a_reply_that_spoke_and_withheld_says_no_phrase(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Mid-reply the withheld sentence is dropped and the answer around
    it speaks, which is the whole point of testing per sentence."""
    session = await speaking_session(
        scripts={"poet": ScriptedLlm([f"Noted.\n{LEAK}"])}
    )

    with caplog.at_level("INFO"):
        await drive_reply(session, UTTERANCE)

    assert events(caplog, "reply_fallback") == []
    assert wire(session).announced() == ["Noted."]


async def test_speech_before_a_wholly_withheld_leg_plays_no_fallback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The first of the two handover orders, and the one `spoken` alone
    cannot answer: it is cleared at the leg boundary, so a reply read
    from it here would look empty and tell a user who had just heard a
    sentence that there was nothing to say."""
    scripts = {
        "poet": ScriptedLlm([["Handing you over.", call("switch_agent", agent="tutor")]]),
        "tutor": ScriptedLlm([LEAK]),
    }
    session = await speaking_session(mac=BOTH_MAC, scripts=scripts)

    with caplog.at_level("INFO"):
        await drive_reply(session, UTTERANCE)

    assert events(caplog, "reply_fallback") == []
    assert wire(session).announced() == ["Handing you over."]
    assert len(events(caplog, "sentence_withheld")) == 1


async def test_a_wholly_unsayable_multi_leg_reply_plays_the_fallback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The other order. Both legs leaked and neither spoke, so the reply
    ends where the one-leg case ends, and the phrase that goes out is
    the agent's who was talking when it ended."""
    scripts = {
        "poet": ScriptedLlm([[LEAK, call("switch_agent", agent="tutor")]]),
        "tutor": ScriptedLlm([LEAK]),
    }
    session = await speaking_session(mac=BOTH_MAC, scripts=scripts)

    with caplog.at_level("INFO"):
        await drive_reply(session, UTTERANCE)

    said = only(caplog, "reply_fallback")
    assert fields_of(said)["reason"] == "nothing_sayable"
    assert fields_of(said)["agent"] == "tutor"
    assert len(events(caplog, "sentence_withheld")) == 2


# --- what a withheld sentence may not reach ---------------------------


class SayingTts(TtsProvider):
    """A voice that writes down every sentence it was asked to speak.

    Audio is what a byte search cannot answer: what goes out is Opus,
    and a sentinel would not survive the encoder whether it was spoken
    or not. What CAN be answered is whether the voice was ever asked for
    it, and a sentence nobody synthesized is a sentence no frame can
    carry.
    """

    def __init__(self) -> None:
        self._inner = MockTts(sample_rate=24000, ms_per_char=1.0, min_ms=60.0)
        self.sample_rate = self._inner.sample_rate
        self.asked: list[str] = []

    def synthesize(self, text: str) -> Any:
        self.asked.append(text)
        return self._inner.synthesize(text)


async def test_a_withheld_sentence_reaches_no_retained_surface(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The no-leak claim, over every surface that keeps anything.

    The record's `reply` is what the user heard and is assembled solely
    from what was spoken, and there is no raw generated-text channel
    anywhere, so a sentence the guard dropped has nowhere to be. That is
    a claim about six surfaces at once, which is why it is one test with
    a planted secret rather than six readings of the code.
    """
    spy = SpyStore()
    script = ScriptedLlm([f"{LEAKED_SECRET}\nAll set.\n", "And then this."])
    session = await speaking_session(scripts={"poet": script}, conversations=spy)

    with caplog.at_level("INFO"):
        await drive_reply(session, UTTERANCE)
        # The following round, which is what makes the history the model
        # is handed observable at all.
        await drive_reply(session, UTTERANCE)

    # The device: what it was told it is about to hear.
    assert wire(session).announced() == ["All set.", "And then this."]
    # Both log formats, and the structured half of every record.
    assert SENTINEL not in both_formats(caplog)
    assert all(
        SENTINEL not in json.dumps(fields_of(record), default=str)
        for record in caplog.records
        if getattr(record, "event", None) is not None
    )
    # The store, whole: the reply, the legs, and every tool row on it.
    assert len(spy.records) == 2
    assert all(SENTINEL not in repr(record) for _, record in spy.records)
    assert [record.reply for _, record in spy.records] == ["All set.", "And then this."]
    # And the history the second round was written against.
    assert all(
        SENTINEL not in str(turn.content) for turns, _, _ in script.seen for turn in turns
    )


async def test_no_audio_is_ever_made_of_a_withheld_sentence() -> None:
    """The other half of the device claim. A frame carries Opus, so the
    honest question is whether the voice was asked for the sentence at
    all: it was not, so nothing that went out can be it."""
    voice = SayingTts()
    script = ScriptedLlm([f"{LEAKED_SECRET}\nAll set.\n"])
    config = base_config()
    session = session_for(
        config,
        POET_MAC,
        {"poet": script},
        stages={"tts": cast(Any, voice)},
        fallbacks=await phrases(config),
    )
    session.websocket = cast(Any, OrderedSocket())

    await drive_reply(session, UTTERANCE)

    assert voice.asked == ["All set."]


async def test_a_far_side_tool_name_reaches_no_payload_or_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A board names its own tools, and this surface may not repeat one.

    The leaked call names a device tool whose name carries the sentinel,
    which is the same rule `tool_call` keeps: the record says which
    namespace was reached into and nothing else, and the withholding
    happens all the same.
    """
    board = FakeDevice(
        [
            {
                "tools": [
                    {
                        "name": f"self.{SENTINEL}",
                        "description": "A board control",
                        "inputSchema": {"type": "object", "properties": {}},
                    }
                ]
            }
        ]
    )
    await board.client.discover()
    published = board.client.tools()[0].name
    leaked = json.dumps({"name": published, "arguments": {}})
    session = session_for(base_config(), POET_MAC, {"poet": ScriptedLlm([leaked])})
    # White-box, the way every device-tool case in these suites reaches
    # it: a board's tools arrive from a discovery run the edge starts
    # over the wire, and this session has no socket to run one on.
    session._device_tools = board.client

    with caplog.at_level("INFO"):
        assert await run_reply(session, "turn it up") == []

    withheld = only(caplog, "sentence_withheld")
    assert fields_of(withheld)["source"] == "device"
    assert "tool" not in fields_of(withheld)
    assert SENTINEL not in both_formats(caplog)
