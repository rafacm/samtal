"""Where the clips a server plays live, and who binds them.

The cache used to be one mutable object the boot filled once and
everything held a reference to. It is a value the generation carries
now (#191), which is what lets a reload put different clips in front of
the next session without a conversation's masking changing under it.

What must not change is what a session sees: an agent nothing
synthesized for is absent, which is what makes the mask stand down
rather than wait.

Two kinds live on that shelf now (#384). The filled pause is above; the
phrase a failed reply says is below, under its own heading, because
what separates it is worth reading in one place: it is on by default, it
keeps its words when its audio is lost, and it is staled by its own
section alone.
"""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from tests.support.configs import config_with_agent
from tests.support.events import only
from tests.support.providers import BrokenTts, built_world
from vinga_server import app as app_module
from vinga_server import filler as filler_module
from vinga_server.app import create_app
from vinga_server.config import Config
from vinga_server.config.models import FallbackConfig
from vinga_server.config.secrets import SecretStore
from vinga_server.filler import FallbackClip, FillerClips, Fillers, build_agent_fillers
from vinga_server.generation import Generation
from vinga_server.providers import AgentProviders, ProviderWorld
from vinga_server.providers.base import TtsProvider

CLIP = FillerClips(delay_ms=800.0, phrases=("hmm",), clips=(b"\x00\x00",), sample_rate=16000)


def test_a_lookup_for_an_agent_with_no_clip_answers_absent() -> None:
    """The fire-time behavior, unchanged and pinned here because the
    runner's stand-down is built on it: a mapping is asked three ways and
    all three say no."""
    fillers: dict[str, FillerClips] = {"assistant": CLIP}

    assert "poet" not in fillers
    assert fillers.get("poet") is None
    with pytest.raises(KeyError):
        fillers["poet"]


def test_startup_puts_the_synthesized_clips_on_the_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The wiring, end to end: what the boot synthesized is what the
    world this server serves holds. Without this, a startup that dropped
    the synthesis result on the floor would pass every other test here,
    because a world with no clips answers exactly as one with nothing for
    this agent."""

    async def synthesized(
        config: Config,
        agent_providers: dict[str, AgentProviders],
        previous: Any = None,
    ) -> Fillers:
        return Fillers(clips={"assistant": CLIP}, resynthesized=("assistant",))

    monkeypatch.setattr(app_module, "build_agent_fillers", synthesized)

    app = create_app(config_with_agent())
    # Before the lifespan there is no composition at all: the build owns
    # the world along with everything else, so the coldest state a reader
    # could ever see is the one inside an entered lifespan.
    assert getattr(app.state, "composition", None) is None

    with TestClient(app):
        generation = app.state.composition.generations.current()
        assert generation.fillers.get("assistant") is CLIP


# --- the other clip on the same shelf ---------------------------------
#
# What a failed reply says is built by the same pass, kept under its own
# section and reported under its own outcomes. The cases below are the
# three things that separates it from the filled pause beside it: it is
# on by default, it keeps its words when its audio is lost, and it is
# staled by its own section alone.

PHRASE = "Something went wrong."

FALLBACK = FallbackClip(phrase=PHRASE, clip=b"\x00\x00", sample_rate=16000)


def voiced(config: Config, tts: Any) -> dict[str, AgentProviders]:
    """This configuration's engines with every agent's voice replaced,
    which is what makes a synthesis outcome the test's to choose."""
    return {
        name: replace(entry, tts=cast(Any, tts))
        for name, entry in built_world(config).agents.items()
    }


class NeverFinishesTts(TtsProvider):
    """A voice whose stream opens and never completes, which is what a
    hung provider looks like from the build's side: not a refusal, not
    an empty answer, just a boot that would never end."""

    sample_rate = 24000

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        yield b"\x00\x00"
        await asyncio.sleep(3600)


async def test_the_failure_phrase_is_cached_for_every_agent_by_default() -> None:
    """On where nothing says otherwise, which is the whole asymmetry
    with the filled pause: a deployment that wrote no section at all
    gets a phrase per agent, because the silent turn is at its worst
    where nobody has thought about any of this yet."""
    config = config_with_agent()

    built = await build_agent_fillers(config, built_world(config).agents)

    cached = built.fallbacks["assistant"]
    assert cached.phrase == FallbackConfig().phrase
    assert cached.clip
    assert built.fallback_resynthesized == ("assistant",)
    assert (built.fallback_reused, built.fallback_degraded) == ((), ())
    # And it is not the filled pause, which stays off where nothing
    # asked for it: the two kinds are read from two sections.
    assert built.clips == {}


async def test_an_agent_that_switched_it_off_has_no_phrase_at_all() -> None:
    """Off is absence rather than a phrase with nothing behind it: the
    runner reads the mapping, and an entry that is present is an entry
    it speaks."""
    config = config_with_agent(agent={"fallback": {"enabled": False}})

    built = await build_agent_fillers(config, built_world(config).agents)

    assert built.fallbacks == {}
    # In none of the three outcomes either: there was no decision to
    # make about this agent, and naming it under one would say there was.
    assert (
        built.fallback_resynthesized,
        built.fallback_reused,
        built.fallback_degraded,
    ) == ((), (), ())


async def test_a_voice_that_refuses_leaves_the_words_and_loses_the_audio(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The degradation the display half needs. A failed synthesis takes
    the audio and nothing else: the phrase is still cached, so a failed
    turn still says its piece in writing, and the outcome says which of
    the two happened without carrying the words."""
    config = config_with_agent()

    with caplog.at_level("WARNING"):
        built = await build_agent_fillers(config, voiced(config, BrokenTts()))

    cached = built.fallbacks["assistant"]
    assert cached.phrase == FallbackConfig().phrase
    assert cached.clip is None
    assert built.fallback_degraded == ("assistant",)
    assert built.fallback_resynthesized == ()
    degraded = only(caplog, "fallback_degraded")
    assert degraded.agent == "assistant"
    assert degraded.error == "RuntimeError"
    # The words are configuration, and the record is telemetry: the one
    # never rides the other.
    assert cached.phrase not in caplog.text


async def test_a_voice_that_never_finishes_degrades_and_the_build_ends(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bound this kind has and the filled pause does not.

    Startup awaits the whole build before the server serves, and this
    section is on by default, so every upgrading deployment synthesizes
    one phrase per agent at its first boot after the change through
    whatever voice it configured. A voice that hangs would hold that
    boot open forever. The deadline is what makes the worst case a
    bounded delay and a degraded agent, and it is the same degradation a
    refusal produces, reported under the class name of the wait.
    """
    monkeypatch.setattr(filler_module, "FALLBACK_SYNTHESIS_TIMEOUT_S", 0.05)
    config = config_with_agent()

    with caplog.at_level("WARNING"):
        built = await build_agent_fillers(config, voiced(config, NeverFinishesTts()))

    assert built.fallbacks["assistant"].clip is None
    assert built.fallback_degraded == ("assistant",)
    assert only(caplog, "fallback_degraded").error == "TimeoutError"


async def test_startup_puts_the_failure_phrases_on_the_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other half of the boot wiring. A startup that dropped this
    mapping would pass every test above, because a world with no phrases
    answers exactly as one with nothing for this agent."""

    async def synthesized(
        config: Config,
        agent_providers: dict[str, AgentProviders],
        previous: Any = None,
    ) -> Fillers:
        return Fillers(
            fallbacks={"assistant": FALLBACK}, fallback_resynthesized=("assistant",)
        )

    monkeypatch.setattr(app_module, "build_agent_fillers", synthesized)

    with TestClient(create_app(config_with_agent())) as client:
        generation = client.app.state.composition.generations.current()
        assert generation.fallbacks.get("assistant") is FALLBACK


# --- what stales which -------------------------------------------------


def previously(config: Config, built: Fillers, providers: dict[str, Any]) -> Generation:
    """The world a rebuild composes from: what it was configured with,
    what it synthesized, and the engines it spoke through. The three are
    one object here for the reason the build reads them as one."""
    return Generation(
        config,
        SecretStore(),
        built.clips,
        ProviderWorld(agents=providers),
        built.fallbacks,
    )


async def rebuilt(before: Config, after: Config) -> tuple[Fillers, Fillers]:
    """One world built, then a second composed from it, through engines
    carried over as the objects they were: an entry a reload did not
    rewrite is the same voice, which is what makes reuse observable."""
    providers = dict(built_world(before).agents)
    first = await build_agent_fillers(before, providers)
    second = await build_agent_fillers(
        after, providers, previously(before, first, providers)
    )
    return first, second


async def test_switching_the_filler_off_leaves_the_failure_phrase_alone() -> None:
    """The two kinds are staled apart, and this is the direction that
    costs the most if it is wrong: an operator turning the mask off has
    not asked for every agent's failure phrase to be spoken again."""
    before = config_with_agent(agent={"filler": {"enabled": True, "phrases": ["Hmm..."]}})
    after = config_with_agent(agent={"filler": {"enabled": False}})

    first, second = await rebuilt(before, after)

    assert second.clips == {}
    assert second.fallback_reused == ("assistant",)
    assert second.fallback_resynthesized == ()
    # The object itself, which is how a caller proves nothing was sent
    # to a voice.
    assert second.fallbacks["assistant"] is first.fallbacks["assistant"]


async def test_rewording_the_failure_phrase_leaves_the_filler_alone() -> None:
    """And the reverse, so neither direction rests on the other's
    evidence."""
    before = config_with_agent(agent={"filler": {"enabled": True, "phrases": ["Hmm..."]}})
    after = config_with_agent(
        agent={
            "filler": {"enabled": True, "phrases": ["Hmm..."]},
            "fallback": {"phrase": "Sorry, I broke."},
        }
    )

    first, second = await rebuilt(before, after)

    assert second.reused == ("assistant",)
    assert second.clips["assistant"] is first.clips["assistant"]
    assert second.fallback_resynthesized == ("assistant",)
    assert second.fallbacks["assistant"].phrase == "Sorry, I broke."


async def test_an_unchanged_degraded_phrase_is_carried_over_rather_than_retried() -> None:
    """What the reload response promises, pinned against what the cache
    actually does.

    A failure phrase that lost its audio is cached without it rather
    than left out, because the words are the half the display needs. So
    an apply that moved neither the section nor the voice finds a clip
    to keep and keeps it: the agent is reused, no phrase is sent to a
    voice, and the degradation survives. That is the opposite of the
    filled pause beside it, whose failed synthesis leaves nothing in the
    mapping and is therefore tried again by the very next build, and the
    two descriptions have to say which of the two they are.
    """
    config = config_with_agent()
    providers = voiced(config, BrokenTts())
    first = await build_agent_fillers(config, providers)
    assert first.fallback_degraded == ("assistant",)

    second = await build_agent_fillers(
        config, providers, previously(config, first, providers)
    )

    assert second.fallback_reused == ("assistant",)
    assert second.fallback_degraded == ()
    # The object itself, which is what says nothing was asked of the
    # voice a second time, and the degradation with it.
    assert second.fallbacks["assistant"] is first.fallbacks["assistant"]
    assert second.fallbacks["assistant"].clip is None


async def test_moving_the_phrase_retries_a_degraded_agent() -> None:
    """And the condition the description names: an edit to the section
    is what asks the voice again, so an operator who wants a retry has
    something to do other than wait for a reload that would not have
    done one."""
    providers = voiced(config_with_agent(), BrokenTts())
    before = config_with_agent()
    first = await build_agent_fillers(before, providers)

    after = config_with_agent(agent={"fallback": {"phrase": "Sorry, I broke."}})
    second = await build_agent_fillers(
        after, providers, previously(before, first, providers)
    )

    assert second.fallback_degraded == ("assistant",)
    assert second.fallback_reused == ()
    assert second.fallbacks["assistant"].phrase == "Sorry, I broke."
