"""Pre-synthesized conversational fillers, one set per agent voice.

The silence between the end of an utterance and the first audio of a
reply is where a voice assistant feels dead: field round 1 measured
1.5 to 3 s of dead air on healthy turns, a 5.1 s median on a slow
morning, and the user's on-record reaction was "Are you there?" (#48).
Humans hold exactly this gap with a filled pause ("Hmm, let me
see..."), and playing one when the reply is late is latency masking;
the session plays it, this module only prepares the clips.

Synthesized once, at boot, and cached as PCM, never at fire time:
synthesizing at the moment of masking would add TTS latency to the
exact gap being masked, and a cached clip keeps working when the TTS
provider is the thing being slow. A synthesis failure logs a warning
and leaves the feature off for that agent; it never fails the boot,
because masking is an enhancement, and a server that answers plainly
beats one that does not start.
"""

import logging
from dataclasses import dataclass

from samtal_server.config import Config
from samtal_server.events import ServerEvents
from samtal_server.providers import AgentProviders

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


class AgentFillers:
    """The clip cache the whole server shares, keyed by agent.

    Built empty and handed to everything that will read it, because the
    synthesis it holds is async and the readers are assembled before the
    startup that runs it. Filled once, when it has run.

    A lookup before the fill answers exactly as an empty cache does, and
    after it exactly as the filled one: a clip nobody has synthesized yet
    is not reachable, and the session's mask stands down for it the way
    it stands down for an agent that configured none. That is the
    fire-time behavior, and it is deliberately unchanged.

    What is new is that the two cases are no longer the same answer to a
    caller that asks: `ready` says whether the synthesis has run at all,
    so "this agent has no fillers" and "no agent has any yet" can be
    told apart by anything that ever needs to. Nothing does yet.
    """

    def __init__(self) -> None:
        self._clips: dict[str, FillerClips] = {}
        self._ready = False

    def __contains__(self, name: object) -> bool:
        return name in self._clips

    def __getitem__(self, name: str) -> FillerClips:
        return self._clips[name]

    def get(self, name: str, default: FillerClips | None = None) -> FillerClips | None:
        return self._clips.get(name, default)

    @property
    def ready(self) -> bool:
        """Whether the boot-time synthesis has run. False means every
        lookup is answering "not yet" rather than "never"."""
        return self._ready

    def fill(self, clips: dict[str, FillerClips]) -> None:
        """Take the synthesized clips, once, at startup.

        Asserted rather than tolerated: this cache is filled by the one
        lifespan that owns it, and a second fill would mean two boots
        share one server's clips or that a caller is using this as a
        mutable dictionary. Neither is a thing to absorb quietly.
        """
        assert not self._ready, "the filler cache is filled once, at startup"
        self._clips.update(clips)
        self._ready = True


async def build_agent_fillers(
    config: Config, agent_providers: dict[str, AgentProviders]
) -> dict[str, FillerClips]:
    """The filler cache for every agent whose configuration enables one.
    An agent whose synthesis fails, or whose voice answers a phrase with
    no audio, is skipped with a warning: the feature is off for it, and
    the boot carries on."""
    fillers: dict[str, FillerClips] = {}
    for name, providers in agent_providers.items():
        section = config.filler_for_agent(name)
        if section is None or not section.enabled:
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
            events.warning(
                "agent %s: filler synthesis failed, latency masking is off "
                "for this agent (%s)",
                name,
                type(exc).__name__,
                event="filler_disabled",
                agent=name,
                error=type(exc).__name__,
            )
            continue
        fillers[name] = FillerClips(
            delay_ms=section.delay_ms,
            phrases=tuple(section.phrases),
            clips=tuple(clips),
            sample_rate=providers.tts.sample_rate,
        )
        logger.info(
            "agent %s: cached %d filler clip(s) in its own voice",
            name,
            len(clips),
        )
    return fillers
