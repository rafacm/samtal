"""Pre-synthesized conversational fillers, one set per agent voice.

The silence between the end of an utterance and the first audio of a
reply is where a voice assistant feels dead: field round 1 measured
1.5 to 3 s of dead air on healthy turns, a 5.1 s median on a slow
morning, and the user's on-record reaction was "Are you there?" (#48).
Humans hold exactly this gap with a filled pause ("Hmm, let me
see..."), and playing one when the reply is late is latency masking;
the session plays it, this module only prepares the clips.

Synthesized ahead of time and cached as PCM, never at fire time:
synthesizing at the moment of masking would add TTS latency to the
exact gap being masked, and a cached clip keeps working when the TTS
provider is the thing being slow. A synthesis failure logs a warning
and leaves the feature off for that agent; it never fails the boot and
never refuses a reload, because masking is an enhancement, and a server
that answers plainly beats one that does not start (#191).

"Ahead of time" is a world's worth of clips rather than a process's:
the clips a world serves are part of it, so they are carried by the
generation and bound by a session at its construction. What that buys
is the reuse below. Making a clip costs a round trip to a voice, so a
world composed from another one keeps every clip whose reasons to exist
have not moved, and an edit to a prompt never sends a single phrase to
a text-to-speech engine.
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

from vinga_server.config import Config
from vinga_server.events import ServerEvents
from vinga_server.events.catalog import FillerDisabled
from vinga_server.events.values import ClassName, Identifier
from vinga_server.providers import AgentProviders

logger = logging.getLogger(__name__)

events = ServerEvents(__name__)


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


class Served(Protocol):
    """The world a new one may keep clips from: what it was configured
    with, and the clips it holds.

    A protocol rather than `generation.Generation`, which is the one
    thing that satisfies it: the generation is where a world's
    configuration and its clips are one object, and this module is
    imported by it. Declaring the two reads is also the whole of what
    reuse needs, so a test supplies a configuration and a mapping and
    not a generation.
    """

    @property
    def config(self) -> Config: ...

    @property
    def fillers(self) -> Mapping[str, FillerClips]: ...


@dataclass(frozen=True)
class Fillers:
    """One world's clips, and how each agent's came to be there.

    The clips are what the world serves; the three name lists are what
    an operator is told a reload did, and they are a closed set chosen
    here, where the decision is actually made, rather than reconstructed
    by a caller comparing two mappings. `disabled` names an agent whose
    synthesis failed: the world applies with no clip for it, and it runs
    with the mask off until the next apply tries again.

    An agent that configures no filled pause, or has switched one off,
    is in none of the three: there is nothing about it to report, and
    naming it under an outcome would say a decision was made where none
    was needed.
    """

    clips: dict[str, FillerClips] = field(default_factory=dict)
    resynthesized: tuple[str, ...] = ()
    reused: tuple[str, ...] = ()
    disabled: tuple[str, ...] = ()


def _voiced_by(
    config: Config, providers: AgentProviders, agent: str
) -> tuple[object, object]:
    """What one agent's clips depend on, and therefore what would make
    them stale: the filler section that chose the phrases and the
    timing, and the voice that speaks them.

    Both halves are read from the world that is actually running where
    the voice is concerned, because the voice is what synthesis uses.
    Providers are built at a start, so the previous world's voice and a
    candidate's are one object and a provider edit neither invalidates a
    clip nor replaces it; that edit stays pending in the comparison
    until the milestone that rebuilds providers, which is also where
    this becomes the candidate world's voice (#191).
    """
    return (config.filler_for_agent(agent), providers.tts)


def _kept(
    previous: Served | None, config: Config, providers: AgentProviders, agent: str
) -> FillerClips | None:
    """The clip a new world may keep for one agent, or None when there
    is one to make.

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
    if _voiced_by(previous.config, providers, agent) != _voiced_by(config, providers, agent):
        return None
    return kept


async def build_agent_fillers(
    config: Config,
    agent_providers: Mapping[str, AgentProviders],
    previous: Served | None = None,
) -> Fillers:
    """The clips for every agent whose configuration enables one, made
    again only where they had to be.

    `previous` is the world these clips are composed from, and None is a
    boot, which has nothing to keep. An agent whose section and whose
    voice are both what they were keeps the very object it had, which is
    what a caller pins by identity; anything else is synthesized here.

    An agent whose synthesis fails, or whose voice answers a phrase with
    no audio, is left with no clip and named under `disabled`: the
    feature is off for it, and the boot or the apply carries on.
    """
    fillers: dict[str, FillerClips] = {}
    resynthesized: list[str] = []
    reused: list[str] = []
    disabled: list[str] = []
    for name, providers in agent_providers.items():
        section = config.filler_for_agent(name)
        if section is None or not section.enabled:
            continue
        kept = _kept(previous, config, providers, name)
        if kept is not None:
            # The object itself, not a copy of it: what carries over is
            # the audio, and identity is how a caller proves nothing was
            # sent to a voice.
            fillers[name] = kept
            reused.append(name)
            continue
        clips: list[bytes] = []
        try:
            for phrase in section.phrases:
                pcm = bytearray()
                async for chunk in providers.tts.synthesize(phrase):
                    pcm.extend(chunk)
                if not pcm:
                    raise ValueError(f'the voice answered "{phrase}" with no audio')
                clips.append(bytes(pcm))
        except Exception as exc:  # noqa: BLE001 - degrade, never fail the boot
            # The class name and never the exception (the PR #153
            # review). This catch is around a whole synthesis, so what
            # arrives is whatever a voice provider or its transport
            # raised, and an exception raised near a response can carry
            # a fragment of one. Handing the object itself as a `%`
            # argument also handed it to every consumer, since
            # `Emission.args` is deliberately not copied for a tap.
            # The two values are bound as defaults rather than closed
            # over: this thunk is built inside a loop, and a closure
            # would read whichever agent the loop had reached by the
            # time the guard called it.
            events.emit(
                lambda agent=name, failure=exc: FillerDisabled(  # type: ignore[misc]
                    agent=Identifier(agent), error=ClassName.of(failure)
                )
            )
            disabled.append(name)
            continue
        fillers[name] = FillerClips(
            delay_ms=section.delay_ms,
            phrases=tuple(section.phrases),
            clips=tuple(clips),
            sample_rate=providers.tts.sample_rate,
        )
        resynthesized.append(name)
        logger.info(
            "agent %s: cached %d filler clip(s) in its own voice",
            name,
            len(clips),
        )
    return Fillers(
        clips=fillers,
        # Sorted, because these are read by a person and by a client
        # comparing two answers, and neither should see an order that
        # depends on how the agents were built.
        resynthesized=tuple(sorted(resynthesized)),
        reused=tuple(sorted(reused)),
        disabled=tuple(sorted(disabled)),
    )


__all__ = ["FillerClips", "Fillers", "Served", "build_agent_fillers"]
