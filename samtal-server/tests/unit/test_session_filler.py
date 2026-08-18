"""Latency masking with pre-synthesized conversational fillers.

The silence between the end of an utterance and the first audio of the
reply is where the assistant feels dead: healthy field turns ran 1.5 to
3 s of it, a slow morning a 5.1 s median, and the user's reaction on
record was "Are you there?" (#48). When a reply's first audio has not
started within the configured delay, the session plays a clip
synthesized at boot in the active agent's voice, and the real reply
queues behind its tail (#74).

These tests drive full replies against a scripted slow LLM, with the
delay shrunk to test scale and the mock TTS trimmed to one frame per
clip, and cover the composition with the first-token watchdog: the
filler is the soft early threshold, the watchdog the hard late one.
"""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any, cast

import pytest

from samtal_server.config import Config
from samtal_server.filler import build_agent_fillers
from samtal_server.providers import Turn, build_agent_providers
from samtal_server.providers.base import TtsProvider
from tests.support.configs import BOTH_MAC, DELAY_MS, POET_MAC, SPEECH, base_config, masked_config
from tests.support.events import events, only
from tests.support.providers import BrokenTts, ScriptedLlm, StallingLlm
from tests.support.sessions import call, masked_session, session_for
from tests.support.sockets import RecordingSocket

STALL_S = 0.5

UTTERANCE = b"\x00\x00" * 320


async def test_a_slow_reply_is_masked_at_the_threshold(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The LLM stalls past the threshold: the filler fires once, its
    first frame is the turn's speaking_started (the real reply emits no
    second one), and the reply then arrives normally."""
    session = await masked_session(masked_config(), POET_MAC, {"poet": StallingLlm([STALL_S])})
    with caplog.at_level("INFO"):
        await session.runtime._reply(UTTERANCE)

    played = only(caplog, "filler_played")
    assert played.agent == "poet"
    assert played.delay_ms >= DELAY_MS
    assert played.phrase_index == 0
    started = only(caplog, "speaking_started")
    replied = only(caplog, "replied")
    assert session.runtime._turns[-1].content == "Recovered now."
    # The filler is what started the speaking, and the reply followed.
    order = [caplog.records.index(record) for record in (played, started, replied)]
    assert order == sorted(order)
    assert cast(Any, session.websocket).frames > 0


async def test_a_fast_reply_plays_no_filler(caplog: pytest.LogCaptureFixture) -> None:
    session = await masked_session(masked_config(delay_ms=500.0), POET_MAC)
    with caplog.at_level("INFO"):
        await session.runtime._reply(UTTERANCE)

    assert events(caplog, "filler_played") == []
    only(caplog, "speaking_started")
    assert session.runtime._turns[-1].content == "POET heard hello."
    # The timer was stood down with the reply, not left running.
    assert session.runtime._filler_task is None


async def test_one_filler_per_turn_and_the_variants_rotate(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A stall several thresholds long earns one filler, not a repeat;
    the next turn's filler is the next phrase."""
    session = await masked_session(masked_config(), POET_MAC, {"poet": StallingLlm([STALL_S])})
    with caplog.at_level("INFO"):
        await session.runtime._reply(UTTERANCE)
        await session.runtime._reply(UTTERANCE)

    played = events(caplog, "filler_played")
    assert [record.phrase_index for record in played] == [0, 1]
    assert len(events(caplog, "speaking_started")) == 2


async def test_the_filler_speaks_in_the_active_agents_voice_after_a_handover(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The per-agent cache holds a clip per voice, and the clip is
    chosen from the agent active at fire time, so the turn after a
    handover masks in the new agent's voice."""
    config = masked_config()
    fillers = await build_agent_fillers(config, build_agent_providers(config))
    # Two voices, two clips: what "in its own voice" means in PCM.
    assert fillers["poet"].clips != fillers["tutor"].clips
    scripts = {
        "poet": ScriptedLlm([[call("switch_agent", agent="tutor")]]),
        "tutor": StallingLlm([0.0, STALL_S], reply="Tutor here."),
    }
    session = session_for(config, BOTH_MAC, cast(Any, scripts), fillers=fillers)
    session.websocket = cast(Any, RecordingSocket())

    # The handover turn, driven below the reply layer so no timer is
    # armed for it; the tutor's first (instant) call answers it.
    spoken: list[str] = []
    session.runtime._turns.append(Turn("user", "get me the tutor"))
    await session.runtime._speak_reply("get me the tutor", spoken)
    session.runtime._turns.append(Turn("assistant", " ".join(spoken)))
    assert session._agent == "tutor"

    with caplog.at_level("INFO"):
        await session.runtime._reply(UTTERANCE)

    played = only(caplog, "filler_played")
    assert played.agent == "tutor"
    # The tutor has one phrase, and that is the one that played.
    assert played.phrase_index % len(fillers["tutor"].clips) == 0


def half_masked_config(delay_ms: float = DELAY_MS) -> Config:
    """The asymmetric shape the arming rule exists for: the poet has no
    filler, the tutor does."""
    config = masked_config(delay_ms)
    return base_config(
        providers=config.providers.model_dump(exclude_none=True),
        agents={
            "poet": {"prompt": "POET", "tts": "tenor"},
            "tutor": {
                "prompt": "TUTOR",
                "tts": "alto",
                "filler": {
                    "enabled": True,
                    "delay_ms": delay_ms,
                    "phrases": ["Hmm, mal überlegen..."],
                },
            },
        },
    )


async def test_a_handover_from_a_fillerless_agent_still_masks_the_new_voice(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The timer is armed when any bound agent has fillers, not just
    the starting one: a filler-less poet hands over immediately, the
    tutor's greeting stalls, and the fire finds the tutor active and
    plays its clip."""
    config = half_masked_config()
    scripts = {
        "poet": ScriptedLlm([[call("switch_agent", agent="tutor")]]),
        "tutor": StallingLlm([STALL_S]),
    }
    session = await masked_session(config, BOTH_MAC, cast(Any, scripts))
    with caplog.at_level("INFO"):
        await session.runtime._reply(UTTERANCE)

    played = only(caplog, "filler_played")
    assert played.agent == "tutor"
    only(caplog, "speaking_started")
    assert session.runtime._turns[-1].content == "Recovered now."


async def test_a_fire_on_an_agent_without_clips_quietly_plays_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The tutor being bound arms the timer, but the poet stays active
    and has no clip, so the fire does nothing: no audio, no event, no
    state left behind, and the turn proceeds normally."""
    session = await masked_session(
        half_masked_config(), BOTH_MAC, {"poet": StallingLlm([STALL_S])}
    )
    with caplog.at_level("INFO"):
        await session.runtime._reply(UTTERANCE)

    assert events(caplog, "filler_played") == []
    only(caplog, "speaking_started")
    assert session.runtime._turns[-1].content == "Recovered now."
    assert session.runtime._filler_task is None
    assert session.runtime._filler_sounding is False
    assert session.runtime._filler_fires == 0


async def test_a_fire_into_live_user_speech_is_skipped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The endpointer holds unresolved speech at fire time, which is a
    user mid-continuation after a premature endpoint: the timer stands
    down with a filler_skipped event instead of talking over them, and
    the reply proceeds unmasked (field round 2 measured 4 of 20 fires
    landing 1.4 to 1.8 s into speech already underway)."""
    session = await masked_session(masked_config(), POET_MAC, {"poet": StallingLlm([STALL_S])})
    with caplog.at_level("INFO"):
        session.runtime._reply_task = asyncio.create_task(session.runtime._reply(UTTERANCE))
        await asyncio.sleep(DELAY_MS / 1000 / 3)
        assert session.runtime._turntaking.endpointer is not None
        session.runtime._turntaking.endpointer.feed(SPEECH)
        await session.runtime._reply_task

    skipped = only(caplog, "filler_skipped")
    assert skipped.reason == "user_speaking"
    assert skipped.speech_ms > 0
    assert events(caplog, "filler_played") == []
    assert session.runtime._turns[-1].content == "Recovered now."
    # The skip consumed no phrase and left no state behind.
    assert session.runtime._filler_task is None
    assert session.runtime._filler_fires == 0


async def test_a_fire_during_a_barge_in_confirmation_is_skipped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A barge-in confirmation has the outgoing frames paused at fire
    time: the endpointed continuation already emptied the endpointer,
    but the pause means the reply in flight is about to be cancelled,
    so the timer stands down rather than masking a doomed turn."""
    session = await masked_session(masked_config(), POET_MAC, {"poet": StallingLlm([STALL_S])})
    with caplog.at_level("INFO"):
        session.runtime._reply_task = asyncio.create_task(session.runtime._reply(UTTERANCE))
        await asyncio.sleep(DELAY_MS / 1000 / 3)
        session.runtime._turntaking._pause_output()
        await asyncio.sleep(DELAY_MS / 1000)
        session.runtime._turntaking._resume_output()
        await session.runtime._reply_task

    skipped = only(caplog, "filler_skipped")
    assert skipped.reason == "barge_in_pending"
    assert events(caplog, "filler_played") == []
    assert session.runtime._turns[-1].content == "Recovered now."
    assert session.runtime._filler_task is None
    assert session.runtime._filler_fires == 0


async def test_the_feature_is_off_by_default(caplog: pytest.LogCaptureFixture) -> None:
    """A config with no filler section builds no clips, and a session
    without any masks nothing however slow the reply."""
    assert await build_agent_fillers(base_config(), build_agent_providers(base_config())) == {}

    session = session_for(base_config(), POET_MAC, {"poet": cast(Any, StallingLlm([0.3]))})
    session.websocket = cast(Any, RecordingSocket())
    with caplog.at_level("INFO"):
        await session.runtime._reply(UTTERANCE)

    assert events(caplog, "filler_played") == []
    only(caplog, "speaking_started")


class SilentTts(TtsProvider):
    """A voice that answers with no audio at all, which is as useless
    as failing."""

    sample_rate = 24000

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        return
        yield  # pragma: no cover - makes this an async generator


async def test_a_synthesis_failure_disables_the_agent_with_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failing voice and a silent one both degrade to disabled for
    their agent, each with a filler_disabled warning, and the build
    returns rather than raising: the boot never fails over a mask."""
    config = masked_config()
    providers = build_agent_providers(config)
    providers["poet"] = replace(providers["poet"], tts=cast(Any, BrokenTts()))
    providers["tutor"] = replace(providers["tutor"], tts=cast(Any, SilentTts()))
    with caplog.at_level("WARNING"):
        assert await build_agent_fillers(config, providers) == {}

    disabled = {record.agent: record.error for record in events(caplog, "filler_disabled")}
    assert disabled == {"poet": "RuntimeError", "tutor": "ValueError"}


async def test_the_filler_composes_with_the_first_token_watchdog(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A round that stalls past both thresholds: the filler fires at the
    soft one, the watchdog retries and gives up at the hard one, and the
    session closes the turn cleanly, with one speaking_started, its tts
    stop, and a return to listening rather than a stuck state."""
    config = masked_config(delay_ms=50.0, server={"llm_first_token_timeout_s": 0.15})
    session = await masked_session(config, POET_MAC, {"poet": StallingLlm([30.0])})
    with caplog.at_level("INFO"):
        session.runtime._reply_task = asyncio.create_task(session.runtime._reply(UTTERANCE))
        await session.runtime._reply_task

    only(caplog, "filler_played")
    only(caplog, "speaking_started")
    only(caplog, "llm_retry")
    failed = only(caplog, "provider_failed")
    assert failed.error == "FirstTokenTimeout"
    assert events(caplog, "replied") == []
    # The turn is over and the session is healthy: not replying, still
    # listening, the device told speech ended, and no filler left over.
    assert not session.runtime.replying()
    assert session.listening is True
    socket = cast(Any, session.websocket)
    assert '"stop"' in socket.texts[-1]
    assert session.runtime._filler_task is None


def test_an_enabled_filler_with_no_phrases_is_refused() -> None:
    with pytest.raises(ValueError, match="no phrases"):
        Config(
            providers={"tts": {"mock": {"type": "mock"}}},
            agents={"solo": {"filler": {"enabled": True}}},
            default_agent="solo",
        )


def test_an_agents_own_filler_replaces_the_inherited_one() -> None:
    """The same inherit-or-replace rule as the stage fields: the
    default applies where an agent names nothing, and an agent's own
    section replaces it wholly."""
    config = base_config(
        agent_defaults={
            "llm": "mock",
            "asr": "mock",
            "vad": "mock",
            "filler": {"enabled": True, "phrases": ["One moment..."]},
        },
        agents={
            "poet": {"prompt": "POET", "tts": "tenor"},
            "tutor": {"prompt": "TUTOR", "tts": "alto", "filler": {"enabled": False}},
        },
    )
    inherited = config.filler_for_agent("poet")
    assert inherited is not None and inherited.enabled
    assert inherited.delay_ms == 1800.0
    own = config.filler_for_agent("tutor")
    assert own is not None and not own.enabled
