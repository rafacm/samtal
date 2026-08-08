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
from samtal_server.providers import AgentProviders

logger = logging.getLogger(__name__)


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
            logger.warning(
                "agent %s: filler synthesis failed, latency masking is off "
                "for this agent: %s: %s",
                name,
                type(exc).__name__,
                exc,
                extra={
                    "event": "filler_disabled",
                    "agent": name,
                    "error": type(exc).__name__,
                },
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
