"""Per-agent speech synthesized ahead of time, one set per agent voice.

Two kinds of cached clip live here, and they are one module because they
are one build: a phrase, spoken by an agent's own voice, made once and
held as PCM. The filled pauses came first and name the module; the
failure phrase joined them (#384). Splitting the second into a module of
its own would restate this one line for line against a different phrase
list, which is the deletion test answering before it is asked.

The filled pause is latency masking. The silence between the end of an
utterance and the first audio of a reply is where a voice assistant
feels dead: field round 1 measured 1.5 to 3 s of dead air on healthy
turns, a 5.1 s median on a slow morning, and the user's on-record
reaction was "Are you there?" (#48). Humans hold exactly this gap with a
filled pause ("Hmm, let me see..."), and playing one when the reply is
late is the mask; the session plays it, this module only prepares the
clips.

The failure phrase is the opposite end of the same turn. A reply that
failed terminally used to be silence, so a broken pipeline and a slow
one were the same experience from the couch and diagnosing one took a
log; the phrase is what the failure arm says instead, on the speaker and
on the display.

Synthesized ahead of time and cached as PCM, never at fire time, and for
the same three reasons in both cases: synthesizing at the moment of
masking would add TTS latency to the exact gap being masked,
synthesizing at the moment of failure would add it to a turn that has
already gone wrong, and a cached clip keeps working when the TTS
provider is the thing being slow or the thing that failed. A synthesis
failure logs and degrades rather than refusing: it never fails the boot
and never refuses a reload, because a server that answers plainly beats
one that does not start (#191). The two degrade differently, because
they lose different amounts: a filler with no clip is a mask that is
off, while a failure phrase with no clip still has a display half, so
the phrase is kept without audio and the failed turn still says its
piece in writing.

"Ahead of time" is a world's worth of clips rather than a process's:
the clips a world serves are part of it, so they are carried by the
generation and bound by a session at its construction. What that buys
is the reuse below. Making a clip costs a round trip to a voice, so a
world composed from another one keeps every clip whose reasons to exist
have not moved, and an edit to a prompt never sends a single phrase to
a text-to-speech engine. Each kind is keyed by its own configuration
section, so toggling the mask cannot stale a failure phrase or the
reverse.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from vinga_server.config import Config
from vinga_server.config.models import FallbackConfig, FillerConfig
from vinga_server.events import ServerEvents
from vinga_server.events.catalog import FallbackDegraded, FillerDisabled
from vinga_server.events.values import ClassName, Identifier
from vinga_server.providers import AgentProviders

if TYPE_CHECKING:
    # Named for the annotation alone: `generation` imports `FillerClips`
    # from here, so a runtime import would close the cycle.
    from vinga_server.generation import Generation

logger = logging.getLogger(__name__)

events = ServerEvents(__name__)

# How long one failure phrase may take to reach this server before the
# build gives up on its audio and keeps the words alone.
#
# It exists because this kind is on by default, which the filler is not.
# Startup awaits the whole build before the server begins serving, so
# every upgrading deployment pays one synthesis per agent at its first
# boot after the change, through whatever voice it configured, and a
# configuration written before this section existed could not have
# staged an opt-out (the old models refuse the unknown key). A voice
# that hangs would therefore hold a boot open with no bound at all. Ten
# seconds is generous for one short sentence through a cloud voice on a
# cold connection and bounded enough that a hung provider delays a boot
# by seconds per agent rather than indefinitely; a phrase that exceeds
# it degrades to display-only exactly like one whose synthesis failed.
#
# The filler's own build keeps its current unbounded behavior. It is
# opt-in and pre-existing: a deployment that switched it on chose the
# synthesis, and bounding it here would be a behavior change nobody
# asked for riding along with this one.
FALLBACK_SYNTHESIS_TIMEOUT_S = 10.0


@dataclass(frozen=True)
class FillerClips:
    """One agent's fillers, ready to play: the configured phrases, one
    PCM clip per phrase in the agent's own voice, and the delay the
    timer fires at. `sample_rate` is the TTS provider's; the session
    resamples to its output rate at fire time, the way it does for any
    reply audio."""

    delay_ms: float
    phrases: tuple[str, ...]
    clips: tuple[bytes, ...]
    sample_rate: int


@dataclass(frozen=True)
class FallbackClip:
    """One agent's failure phrase, ready to say: the words, and the PCM
    of them in the agent's own voice.

    `clip` is None where the voice would not speak them, and that is a
    usable state rather than an absence: the display half of a failure
    notice needs no audio and no working TTS provider, so the words go
    out on the display and only the sound is lost. An agent that
    configures no phrase, or has switched one off, is absent from the
    mapping entirely, which is the real absence.

    `sample_rate` is the TTS provider's, resampled at speak time exactly
    as a filler clip is.
    """

    phrase: str
    clip: bytes | None
    sample_rate: int


@dataclass(frozen=True)
class Fillers:
    """One world's clips, and how each agent's came to be there.

    The clips are what the world serves; the name lists are what an
    operator is told a build did, and they are a closed set chosen here,
    where the decision is actually made, rather than reconstructed by a
    caller comparing two mappings. `disabled` names an agent whose
    filler synthesis failed: the world applies with no clip for it, and
    it runs with the mask off until the next apply tries again.

    Six lists rather than three, because there are two kinds and one
    agent can meet them differently: a filler carried over unchanged
    beside a failure phrase just re-synthesized is one honest sentence
    about that agent, and a single set of outcomes could only tell it as
    two contradictory ones. `fallback_degraded` is the failure kind's
    own third outcome, and it does not mean what `disabled` means: the
    agent still says its piece on the display, and only the audio is
    gone.

    An agent that configures neither kind, or has switched one off, is
    in none of that kind's lists: there is nothing about it to report,
    and naming it under an outcome would say a decision was made where
    none was needed.
    """

    clips: dict[str, FillerClips] = field(default_factory=dict)
    resynthesized: tuple[str, ...] = ()
    reused: tuple[str, ...] = ()
    disabled: tuple[str, ...] = ()
    fallbacks: dict[str, FallbackClip] = field(default_factory=dict)
    fallback_resynthesized: tuple[str, ...] = ()
    fallback_reused: tuple[str, ...] = ()
    fallback_degraded: tuple[str, ...] = ()


def _voiced_by(section: object, providers: AgentProviders | None) -> tuple[object, object]:
    """What one agent's clips of one kind depend on, and therefore what
    would make them stale: the section that chose the words, and the
    voice that speaks them.

    Each half is read from the world it belongs to, which is what makes
    a comparison of two of these a comparison of two worlds. The voice
    is the object rather than a description of the entry it was built
    from, and deliberately: an apply carries an unchanged entry's engine
    over as the object it already was and builds a changed one afresh,
    so object identity says exactly "the same voice would speak this",
    which a name or a model string cannot (#191). A candidate whose TTS
    entry was rewritten, or whose stored credential was rotated, is
    therefore a different voice and its clips are made again in it.

    The section is the caller's to read, which is what keeps the two
    kinds independent: a comparison made from the filler section says
    nothing about the failure phrase, and the reverse.

    None is an agent the world has no engines for, which cannot happen
    for an agent a world serves and is not a reason to keep a clip.
    """
    return (section, None if providers is None else providers.tts)


def _kept(
    previous: "Generation | None", config: Config, providers: AgentProviders, agent: str
) -> FillerClips | None:
    """The filler clips a new world may keep for one agent, or None when
    there is a set to make.

    Three reads of the previous world and no more: what it was
    configured with, the engines it was speaking through, and the clips
    it holds. The generation is where those three are one object, which
    is why it is what a world composed from another one is handed.

    None covers three cases that are one decision: there is no previous
    world, the previous world had no clip for this agent (nothing was
    configured then, or its synthesis failed and this apply is the
    retry), or what the clip depends on has moved.
    """
    if previous is None:
        return None
    kept = previous.fillers.get(agent)
    if kept is None:
        return None
    spoken_by = _voiced_by(
        previous.config.filler_for_agent(agent), previous.providers.agents.get(agent)
    )
    if spoken_by != _voiced_by(config.filler_for_agent(agent), providers):
        return None
    return kept


def _kept_fallback(
    previous: "Generation | None", config: Config, providers: AgentProviders, agent: str
) -> FallbackClip | None:
    """The failure phrase a new world may keep for one agent, or None
    when there is one to make. The same three reads and the same three
    cases as `_kept` above, against this kind's own section and this
    kind's own mapping, which is the whole of what keeps the two from
    staling each other.

    A phrase kept with no clip is kept as it is rather than retried: it
    is the same phrase in the same voice, and the voice already answered
    for it. The retry is the next apply, where a rebuilt engine is a
    different object and this comparison says so.
    """
    if previous is None:
        return None
    kept = previous.fallbacks.get(agent)
    if kept is None:
        return None
    spoken_by = _voiced_by(
        previous.config.fallback_for_agent(agent), previous.providers.agents.get(agent)
    )
    if spoken_by != _voiced_by(config.fallback_for_agent(agent), providers):
        return None
    return kept


async def _pcm(stream: AsyncIterator[bytes], phrase: str) -> bytes:
    """One phrase as PCM, drained from the voice that is speaking it.

    A voice that answers with no audio raises rather than returning
    nothing, because to every caller here those are one outcome: there
    is no clip, and the reason belongs in a class name rather than in a
    second return shape.
    """
    pcm = bytearray()
    async for chunk in stream:
        pcm.extend(chunk)
    if not pcm:
        raise ValueError(f'the voice answered "{phrase}" with no audio')
    return bytes(pcm)


async def _filler_clips(
    agent: str, section: FillerConfig, providers: AgentProviders
) -> FillerClips | None:
    """One agent's filled pauses, or None where the voice would not
    speak them, in which case the feature is off for that agent and the
    boot or the apply carries on."""
    try:
        clips = [await _pcm(providers.tts.synthesize(phrase), phrase) for phrase in section.phrases]
    except Exception as exc:  # noqa: BLE001 - degrade, never fail the boot
        # The class name and never the exception (the PR #153 review).
        # This catch is around a whole synthesis, so what arrives is
        # whatever a voice provider or its transport raised, and an
        # exception raised near a response can carry a fragment of one.
        # Handing the object itself as a `%` argument also handed it to
        # every consumer, since `Emission.args` is deliberately not
        # copied for a tap. The failure is bound as a default rather
        # than closed over, because `except` deletes its name on the way
        # out and a thunk read later would find nothing.
        events.emit(
            lambda failure=exc: FillerDisabled(  # type: ignore[misc]
                agent=Identifier(agent), error=ClassName.of(failure)
            )
        )
        return None
    logger.info("agent %s: cached %d filler clip(s) in its own voice", agent, len(clips))
    return FillerClips(
        delay_ms=section.delay_ms,
        phrases=tuple(section.phrases),
        clips=tuple(clips),
        sample_rate=providers.tts.sample_rate,
    )


async def _fallback_clip(
    agent: str, section: FallbackConfig, providers: AgentProviders
) -> FallbackClip:
    """One agent's failure phrase, with its audio where the voice spoke
    it in time and without where it did not.

    Always a clip, never None: the words are configuration and are known
    whatever the voice does, and they are half of what #384 asks for.
    What a failure costs is the audio, which is what the event beside it
    reports.

    Bounded, unlike the filler's own synthesis, for the reason
    `FALLBACK_SYNTHESIS_TIMEOUT_S` gives: a deadline exceeded is the
    same outcome as a refusal, reported by the same event under the
    class name `TimeoutError`.
    """
    clip: bytes | None = None
    try:
        async with asyncio.timeout(FALLBACK_SYNTHESIS_TIMEOUT_S):
            clip = await _pcm(providers.tts.synthesize(section.phrase), section.phrase)
    except Exception as exc:  # noqa: BLE001 - degrade, never fail the boot
        # The class name and never the exception, for the reason the
        # filler half above gives at length, and bound as a default for
        # the same one.
        events.emit(
            lambda failure=exc: FallbackDegraded(  # type: ignore[misc]
                agent=Identifier(agent), error=ClassName.of(failure)
            )
        )
    return FallbackClip(
        phrase=section.phrase, clip=clip, sample_rate=providers.tts.sample_rate
    )


async def build_agent_fillers(
    config: Config,
    agent_providers: Mapping[str, AgentProviders],
    previous: "Generation | None" = None,
) -> Fillers:
    """Every clip of both kinds that this world's agents need, made
    again only where they had to be.

    `agent_providers` is the engines of the world being built, which is
    what a clip is spoken in: an apply that rebuilt a voice synthesizes
    in the new one.

    `previous` is the world these clips are composed from, and None is a
    boot, which has nothing to keep. An agent whose section and whose
    voice are both what they were keeps the very object it had, which is
    what a caller pins by identity; anything else is synthesized here.
    Each kind asks that question of its own section, so an agent whose
    filler was switched off keeps the failure phrase it already had.

    An agent whose filler synthesis fails, or whose voice answers a
    phrase with no audio, is left with no clip and named under
    `disabled`: the mask is off for it, and the boot or the apply
    carries on. An agent whose failure phrase will not synthesize keeps
    the phrase without audio and is named under `fallback_degraded`: its
    failed turns are shown rather than spoken.
    """
    fillers: dict[str, FillerClips] = {}
    resynthesized: list[str] = []
    reused: list[str] = []
    disabled: list[str] = []
    fallbacks: dict[str, FallbackClip] = {}
    fallback_resynthesized: list[str] = []
    fallback_reused: list[str] = []
    fallback_degraded: list[str] = []
    for name, providers in agent_providers.items():
        section = config.filler_for_agent(name)
        if section is not None and section.enabled:
            kept = _kept(previous, config, providers, name)
            if kept is not None:
                # The object itself, not a copy of it: what carries over
                # is the audio, and identity is how a caller proves
                # nothing was sent to a voice.
                fillers[name] = kept
                reused.append(name)
            else:
                clips = await _filler_clips(name, section, providers)
                if clips is None:
                    disabled.append(name)
                else:
                    fillers[name] = clips
                    resynthesized.append(name)
        fallback = config.fallback_for_agent(name)
        if fallback.enabled:
            held = _kept_fallback(previous, config, providers, name)
            if held is not None:
                fallbacks[name] = held
                fallback_reused.append(name)
            else:
                made = await _fallback_clip(name, fallback, providers)
                fallbacks[name] = made
                if made.clip is None:
                    fallback_degraded.append(name)
                else:
                    fallback_resynthesized.append(name)
    return Fillers(
        clips=fillers,
        # Sorted, because these are read by a person and by a client
        # comparing two answers, and neither should see an order that
        # depends on how the agents were built.
        resynthesized=tuple(sorted(resynthesized)),
        reused=tuple(sorted(reused)),
        disabled=tuple(sorted(disabled)),
        fallbacks=fallbacks,
        fallback_resynthesized=tuple(sorted(fallback_resynthesized)),
        fallback_reused=tuple(sorted(fallback_reused)),
        fallback_degraded=tuple(sorted(fallback_degraded)),
    )


__all__ = ["FallbackClip", "FillerClips", "Fillers", "build_agent_fillers"]
