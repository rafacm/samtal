"""Applying the stored configuration to a running server.

The MCP half is exercised against real servers in
`test_tools_mcp_reload.py` and the transport around the route is
`test_config_api_runtime.py`'s. What is left here is what the
generalized apply itself decides: which slices of the stored world it
installs, what it refuses when they do not add up with the ones it is
keeping, what it reports having done, and what a live session reads once
it has done it.

The overlay is most of the file, and its cases are chosen for the shape
they share: a stored edit reaches a running session or it does not, and
which one it is has to follow the field rather than the entity. An
agent's own prompt reaches it; the fragments every agent inherits
through `agent_defaults` do not, because the effective-value helpers
read that layer and installing it would apply a start-bound change
through the back door.

The filled pauses are the second half of the file and are the same shape
one step on: what an apply decides about a clip follows the two things a
clip is made of, each read from the world it belongs to. The engines are
the third and the same shape again, one entry at a time. So the
assertions in both are about object identity as much as about names,
because "nothing was sent to a voice" and "no model was loaded" are not
things a name list can say.
"""

import asyncio
import json
import logging
import threading
import time
from collections.abc import Callable, Collection, Mapping
from pathlib import Path
from typing import Any, cast

import pytest
from cryptography.fernet import Fernet, MultiFernet
from sqlalchemy import select, update

from tests.support.apps import entered_client
from tests.support.configs import config_with, world
from tests.support.problems import refused as refused_body
from tests.support.providers import BrokenTts, RecordingLlm, ScriptedLlm, built_world
from tests.support.sessions import agent_providers, call, run_reply, session_for
from tests.support.stores import memory as lane_memory
from tests.support.tools_mcp import reading
from vinga_server.app import _prompt_preview, config_diff_reader, config_reloader
from vinga_server.config import Config, cli
from vinga_server.config.api import MOUNT_PATH
from vinga_server.config.boot import BootConfig, load_boot_config
from vinga_server.config.loader import (
    ConfigError,
    DatabaseBusyError,
    ProviderRefusedError,
    ReloadInProgressError,
    StorageError,
)
from vinga_server.config.models import DatabaseConfig
from vinga_server.config.reload import ConfigReload
from vinga_server.config.responses import ConfigReloadResult
from vinga_server.config.secrets import (
    MASTER_KEY_ENV,
    SecretLocation,
    SecretStore,
    encrypt,
    generate_key,
    load_keys,
)
from vinga_server.config.store import ConfigStore
from vinga_server.db import open_database, schema
from vinga_server.filler import FillerClips, build_agent_fillers
from vinga_server.generation import Generation, Generations
from vinga_server.logs import JsonFormatter
from vinga_server.providers import ProviderWorld
from vinga_server.providers import world as provider_world
from vinga_server.providers.mock import MockTts
from vinga_server.tools.mcp import McpServers

DEVICE = "aa:bb:cc:dd:ee:ff"


def served(**overrides: object) -> Config:
    """A configuration one device reaches one agent through, so a
    session can be built on it."""
    return config_with(**({"devices": {DEVICE: ["assistant"]}} | overrides))


def applying(
    running: Config,
    stored: Config,
    fillers: Mapping[str, FillerClips] | None = None,
    providers: ProviderWorld | None = None,
    running_secrets: SecretStore | None = None,
    stored_secrets: SecretStore | None = None,
    held: Callable[[], Collection[Generation]] = tuple,
) -> tuple[Generations, ConfigReload]:
    """A running server and the apply that would put `stored` in front
    of it, with the holder the apply installs into handed back so a test
    can read what is being served.

    The world this server is serving is the running configuration with
    its own engines built from it, which is what a boot leaves behind:
    a case that wants a voice that will not speak, or an engine it can
    watch being closed, hands its own world in. `fillers` are the clips
    that server already has, which is what a reuse assertion needs a
    world to start from.

    `held` is who is still holding a world when the apply retires one,
    and nobody is the default: a suite with no conversations open is a
    server whose replaced engines may go at once.
    """
    generations = world(
        running,
        running_secrets,
        fillers,
        built_world(running) if providers is None else providers,
    )
    return generations, ConfigReload(
        generations,
        McpServers.build(running),
        reading(stored, stored_secrets),
        held,
    )


async def applied(
    running: Config, stored: Config, **over: object
) -> tuple[Generations, ConfigReloadResult]:
    generations, reload = applying(running, stored, **cast(Any, over))
    return generations, await reload.apply()


# What an apply installs, field by field


async def test_an_agents_own_prompt_is_applied() -> None:
    generations, result = await applied(
        served(agents={"assistant": {"prompt": "A"}}),
        served(agents={"assistant": {"prompt": "B"}}),
    )

    assert generations.current().config.prompt_for_agent("assistant") == "B"
    assert result.prompts.changed == ["assistant"]


async def test_the_shared_fragments_are_applied_whole() -> None:
    """The fragment kind is replaced from the store rather than merged,
    so an edit to the text every including agent carries reaches all of
    them at once."""
    running = served(
        prompt_fragments={"house": {"text": "Quiet."}},
        agents={"assistant": {"prompt": "A", "prompt_includes": ["house"]}},
    )
    stored = served(
        prompt_fragments={"house": {"text": "Loud."}},
        agents={"assistant": {"prompt": "A", "prompt_includes": ["house"]}},
    )

    generations, result = await applied(running, stored)

    fragments = generations.current().config.fragments_for_agent("assistant")
    assert [fragment.text for fragment in fragments] == ["Loud."]
    # The agent's assembled inputs moved, which is what the section
    # reports: the fragment's own name is the diff's answer, not this
    # one's.
    assert result.prompts.changed == ["assistant"]


# Two fragments that both exist in both worlds and say different
# things, so that moving an agent's own list between them is the only
# thing an apply can be reading. The fragment kind is replaced wholesale
# either way, and here it is replaced with an identical copy of itself.
QUIET = "The house is quiet."

LOUD = "The house is loud."

BOTH_FRAGMENTS = {"quiet": {"text": QUIET}, "loud": {"text": LOUD}}


def including(*fragments: str) -> Config:
    """One agent whose own `prompt_includes` names these, over a
    fragment kind that does not move."""
    return served(
        prompt_fragments=BOTH_FRAGMENTS,
        agents={
            "assistant": {"prompt": "A", "prompt_includes": list(fragments)}
        },
    )


async def test_an_agents_own_include_list_is_applied() -> None:
    """The other half of the agent's prompt slice, on its own.

    The case is built so that nothing else could produce the answer:
    both fragments exist in both worlds with the same text, so replacing
    the fragment kind changes nothing, and the agent's own list is the
    only thing that moved. An apply that dropped `prompt_includes` from
    the overlay would leave the agent including `quiet` and every
    assertion below would fail.
    """
    running, stored = including("quiet"), including("loud")
    llm = RecordingLlm()
    generations, reload = applying(running, stored, providers=scripted(running, llm))
    diff = config_diff_reader(
        generations, McpServers.build(running), reading(stored)
    )

    pending = await diff()
    assert pending.agents.prompt.changed == ("assistant",)

    result = await reload.apply()

    # The world being served names the new fragment and resolves it to
    # the new text, which is the pair an activation reads.
    served_now = generations.current().config
    assert served_now.agents["assistant"].prompt_includes == ["loud"]
    assert [fragment.text for fragment in served_now.fragments_for_agent("assistant")] == [
        LOUD
    ]
    assert result.prompts.changed == ["assistant"]

    # And the next activation is what actually reaches the model.
    await run_reply(talking_to(running, generations), "hello")
    assert LOUD in llm.systems[-1]
    assert QUIET not in llm.systems[-1]

    # The comparison clears, which is the care point: what an apply has
    # already applied is not reported as pending.
    assert (await diff()).agents.prompt.changed == ()


async def test_an_agent_defaults_include_reaches_an_inheriting_agent() -> None:
    """The inheritance path, now that the layer under every agent is a
    reload's. `agent_defaults` is what every effective-value helper falls
    back through, so an edit to it reaches every agent that names nothing
    of its own, and the apply reports which agents those are."""
    running = served(
        prompt_fragments={"house": {"text": "Quiet."}},
        agent_defaults=dict.fromkeys(("llm", "asr", "tts", "vad"), "mock"),
        agents={"assistant": {"prompt": "A"}},
    )
    stored = served(
        prompt_fragments={"house": {"text": "Quiet."}},
        agent_defaults=dict.fromkeys(("llm", "asr", "tts", "vad"), "mock")
        | {"prompt_includes": ["house"]},
        agents={"assistant": {"prompt": "A"}},
    )

    generations, result = await applied(running, stored)

    inherited = generations.current().config.fragments_for_agent("assistant")
    assert [fragment.name for fragment in inherited] == ["house"]
    assert result.prompts.changed == ["assistant"]
    assert result.agents is not None
    assert result.agents.defaults_changed is True


async def test_an_agent_the_store_added_is_served_from_the_swap() -> None:
    """The last kind to move. An apply builds the added agent's pipeline
    with everything else it builds, so the agent is servable the instant
    the request answers rather than at the next start."""
    generations, result = await applied(
        served(agents={"assistant": {"prompt": "A"}}),
        served(agents={"assistant": {"prompt": "A"}, "helper": {"prompt": "H"}}),
    )

    assert set(generations.current().config.agents) == {"assistant", "helper"}
    assert generations.current().providers.agents["helper"].llm is not None
    assert result.agents is not None
    assert (result.agents.added, result.agents.removed) == (["helper"], [])
    # Reported once, and under the section whose vocabulary fits: an
    # agent that has just arrived has no previous text to differ from.
    assert result.prompts.changed == []


async def test_an_agent_the_store_deleted_leaves_the_new_world() -> None:
    """The other direction. The world after the apply cannot be asked
    for the deleted agent at all; what keeps a live session survivable is
    that the session holds the world it was built from, which the
    generation the apply retired still is."""
    running = served(
        agents={"assistant": {"prompt": "A"}, "helper": {"prompt": "H"}},
        devices={DEVICE: ["assistant", "helper"]},
    )
    generations, result = await applied(running, served(agents={"assistant": {"prompt": "A"}}))

    assert set(generations.current().config.agents) == {"assistant"}
    assert result.agents is not None
    assert (result.agents.added, result.agents.removed) == ([], ["helper"])


async def test_the_whole_agent_layer_applies_and_the_comparison_clears() -> None:
    """`agent_defaults` whole, which is the last thing an apply was
    keeping back, and the care point over it.

    Every field of the layer is exercised at once because they were held
    back as one and are applied as one: the stage every agent inherits,
    the grant list every agent inherits, the fragments and the filled
    pauses. What proves it is the effective values an agent that
    configures none of its own reads, since those are what an activation
    and a session are actually built from, and the comparison going
    quiet afterwards.
    """
    entries = {
        "llm": {"mock": {"type": "mock"}},
        "asr": {"mock": {"type": "mock"}},
        "tts": {"mock": {"type": "mock"}, "other": {"type": "mock", "tone_hz": 300}},
        "vad": {"mock": {"type": "mock"}},
    }
    stages = dict.fromkeys(("llm", "asr", "tts", "vad"), "mock")
    running = served(
        providers=entries,
        prompt_fragments={"house": {"text": "Quiet."}},
        agent_defaults=stages,
        agents={"assistant": {"prompt": "A"}},
    )
    stored = served(
        providers=entries,
        prompt_fragments={"house": {"text": "Quiet."}},
        agent_defaults=stages
        | {
            "tts": "other",
            "prompt_includes": ["house"],
            "filler": {"enabled": True, "phrases": ["Hmm..."]},
        },
        agents={"assistant": {"prompt": "A"}},
    )
    generations, reload = applying(running, stored)
    diff = config_diff_reader(generations, McpServers.build(running), reading(stored))

    assert (await diff()).agent_defaults.changed is True

    result = await reload.apply()

    served_now = generations.current().config
    assert served_now.provider_for_agent("assistant", "tts")[0] == "other"
    assert [fragment.name for fragment in served_now.fragments_for_agent("assistant")] == [
        "house"
    ]
    section = served_now.filler_for_agent("assistant")
    assert section is not None and section.phrases == ["Hmm..."]
    assert result.agents is not None
    assert result.agents.defaults_changed is True
    assert (await diff()).agent_defaults.changed is False


async def test_an_added_agent_stops_being_pending_once_it_is_applied() -> None:
    """The care point for the last kind to move. An agent the store
    holds and this server does not is pending; the apply installs it,
    and an answer that went on reporting it would be telling an operator
    to apply a change that is already being served."""
    running = served(agents={"assistant": {"prompt": "A"}})
    stored = served(
        agents={"assistant": {"prompt": "A"}, "helper": {"prompt": "H"}},
        devices={DEVICE: ["assistant", "helper"]},
    )
    generations, reload = applying(running, stored)
    diff = config_diff_reader(generations, McpServers.build(running), reading(stored))

    pending = await diff()
    assert pending.agents.added == ("helper",)
    assert pending.agents.applies.value == "reload"

    await reload.apply()

    assert (await diff()).agents.added == ()


async def test_an_agent_repointed_at_another_entry_is_applied() -> None:
    """The last half of the provider slice to move. Which entry serves
    which of an agent's stages used to be composed at a start; the whole
    entry is a reload's now, so an agent pointed at another model speaks
    through it from the next conversation."""
    running = served(agents={"assistant": {"prompt": "A"}})
    stored = served(
        providers={
            "llm": {"mock": {"type": "mock"}, "other": {"type": "mock"}},
            "asr": {"mock": {"type": "mock"}},
            "tts": {"mock": {"type": "mock"}},
            "vad": {"mock": {"type": "mock"}},
        },
        agents={"assistant": {"prompt": "A", "llm": "other"}},
    )

    generations, result = await applied(running, stored)

    assert generations.current().config.provider_for_agent("assistant", "llm")[0] == "other"
    assert result.providers is not None
    assert "llm.other" in result.providers.built


# The filled pauses, and what a reload does to a clip
#
# A clip is a configured phrase spoken by a configured voice, and the two
# together are what an apply decides by. Each half is read from the world
# it belongs to, so a candidate that rebuilt the voice is a candidate
# whose clips are made again in it, and one that carried the voice over
# keeps the very objects it had.


def masking(agent: str = "assistant", *phrases: str, **overrides: object) -> Config:
    """A configuration whose one agent masks its latency, with whichever
    phrases the case is about."""
    return served(
        agents={
            agent: {
                "prompt": "A",
                "filler": {
                    "enabled": True,
                    "phrases": list(phrases) or ["Hmm, let me see..."],
                },
            }
        },
        **overrides,
    )


async def synthesized(
    running: Config,
) -> tuple[ProviderWorld, dict[str, FillerClips]]:
    """The world a server is running: the engines it built and the clips
    it synthesized with them.

    Both, and the same objects, because the identity of each is what the
    assertions below are: a clip carried over is the object it was, and
    it was carried over because the voice it was spoken by is the object
    it was.
    """
    providers = built_world(running)
    return providers, (await build_agent_fillers(running, providers.agents)).clips


async def test_a_prompt_edit_carries_every_clip_over_untouched() -> None:
    """The point of the comparison, pinned where it cannot be faked: the
    clip in the new world is the very object the old one held, so nothing
    was sent to a voice."""
    running = masking()
    stored = served(
        agents={
            "assistant": {
                "prompt": "B",
                "filler": {"enabled": True, "phrases": ["Hmm, let me see..."]},
            }
        }
    )
    providers, clips = await synthesized(running)

    generations, result = await applied(
        running, stored, fillers=clips, providers=providers
    )

    assert generations.current().fillers["assistant"] is clips["assistant"]
    assert result.fillers is not None
    assert result.fillers.reused == ["assistant"]
    assert result.fillers.resynthesized == []
    assert result.fillers.disabled == []
    # And the prompt half really did move, so this is reuse across an
    # apply that changed something rather than across one that did not.
    assert result.prompts.changed == ["assistant"]


async def test_a_phrase_edit_resynthesizes_only_that_agent() -> None:
    """Two masked agents, one edited. The other keeps its object, which
    is what says the decision is per agent rather than per apply."""
    running = served(
        devices={DEVICE: ["assistant", "helper"]},
        agents={
            "assistant": {
                "prompt": "A",
                "filler": {"enabled": True, "phrases": ["Hmm..."]},
            },
            "helper": {
                "prompt": "H",
                "filler": {"enabled": True, "phrases": ["One moment..."]},
            },
        },
    )
    stored = served(
        devices={DEVICE: ["assistant", "helper"]},
        agents={
            "assistant": {
                "prompt": "A",
                "filler": {"enabled": True, "phrases": ["Let me think..."]},
            },
            "helper": {
                "prompt": "H",
                "filler": {"enabled": True, "phrases": ["One moment..."]},
            },
        },
    )
    providers, clips = await synthesized(running)

    generations, result = await applied(
        running, stored, fillers=clips, providers=providers
    )

    assert result.fillers is not None
    assert result.fillers.resynthesized == ["assistant"]
    assert result.fillers.reused == ["helper"]
    applied_clips = generations.current().fillers
    assert applied_clips["assistant"] is not clips["assistant"]
    assert applied_clips["assistant"].phrases == ("Let me think...",)
    assert applied_clips["helper"] is clips["helper"]


async def test_an_agent_repointed_at_another_voice_is_spoken_again() -> None:
    """The same half, one level on. The store points the agent at a
    different voice entry, which a reload applies now, so the clip is a
    different voice's and is made again; nothing is left pending in the
    comparison."""
    voices = {
        "llm": {"mock": {"type": "mock"}},
        "asr": {"mock": {"type": "mock"}},
        "tts": {"mock": {"type": "mock"}, "other": {"type": "mock", "tone_hz": 300}},
        "vad": {"mock": {"type": "mock"}},
    }
    running = masking(providers=voices)
    stored = served(
        providers=voices,
        agents={
            "assistant": {
                "prompt": "A",
                "tts": "other",
                "filler": {"enabled": True, "phrases": ["Hmm, let me see..."]},
            }
        },
    )
    providers, clips = await synthesized(running)
    generations, reload = applying(
        running, stored, fillers=clips, providers=providers
    )
    diff = config_diff_reader(generations, McpServers.build(running), reading(stored))

    result = await reload.apply()

    assert result.fillers is not None
    assert result.fillers.resynthesized == ["assistant"]
    assert result.fillers.reused == []
    assert generations.current().fillers["assistant"] is not clips["assistant"]
    # And nothing is left pending: the entry the apply installed is the
    # entry the store holds.
    assert (await diff()).agents.changed == ()


async def test_a_provider_secret_rotation_is_applied_and_re_voices_the_clips() -> None:
    """The other half of the same pair, and the half that moved with the
    providers. A rotated credential is part of what a voice was built
    with, so the entry is built again with the new one, the clips that
    voice spoke are made again in the new object, and what was pending
    in the comparison is not pending any more."""
    keys = MultiFernet([Fernet(generate_key())])
    location = SecretLocation.provider("tts", "mock", "api_key")
    rotated = SecretStore({location: encrypt(location, ROTATED, keys)}, keys)
    running = masking()
    providers, clips = await synthesized(running)
    generations, reload = applying(
        running,
        running,
        fillers=clips,
        providers=providers,
        running_secrets=SecretStore({location: encrypt(location, PLAINTEXT, keys)}, keys),
        stored_secrets=rotated,
    )
    diff = config_diff_reader(
        generations, McpServers.build(running), reading(running, rotated)
    )

    assert (await diff()).providers.changed == ("tts.mock",)

    result = await reload.apply()

    assert result.providers is not None
    assert result.providers.built == ["tts.mock"]
    assert result.fillers is not None
    assert result.fillers.resynthesized == ["assistant"]
    installed = generations.current()
    assert installed.fillers["assistant"] is not clips["assistant"]
    # Spoken by the object the rotation built, which is what makes this
    # a re-voicing rather than a re-run of the same synthesis.
    assert installed.providers.instances["tts.mock"] is not providers.instances["tts.mock"]
    # And nothing is pending any more, which is the care point: what an
    # apply has applied is not reported as waiting for a restart.
    assert (await diff()).providers.changed == ()


async def test_a_synthesis_failure_applies_the_world_with_no_clip() -> None:
    """A filled pause is a mask, so a voice that will not speak is a
    degraded agent and never a refused reload. The generation lands, that
    agent has no clip, and the answer names it under the one outcome that
    means exactly that."""
    running = masking()
    stored = masking("assistant", "Let me think...")

    generations, result = await applied(
        running, stored, providers=voiced_by(running, BrokenTts())
    )

    assert result.fillers is not None
    assert result.fillers.disabled == ["assistant"]
    assert result.fillers.resynthesized == []
    assert result.fillers.reused == []
    # The world applied: the phrases moved and the agent has no clip.
    assert generations.mark == 1
    assert generations.current().config.filler_for_agent("assistant").phrases == [
        "Let me think..."
    ]
    assert "assistant" not in generations.current().fillers


async def test_a_synthesis_failure_reaches_the_response_body_and_the_rendering() -> None:
    """The whole path, since the outcome is what an operator reads: the
    section is on the wire under the closed token, and the CLI prints it
    beside its siblings rather than dropping a shape it has no rule for.
    """
    running = masking()
    generations, reload = applying(
        running, masking(), providers=voiced_by(running, BrokenTts())
    )

    body = (await reload.apply()).model_dump(mode="json")

    assert body["fillers"] == {
        "resynthesized": [],
        "reused": [],
        "disabled": ["assistant"],
    }
    assert "  disabled: assistant" in cli._reload_listing(body)


async def test_an_agent_defaults_filler_edit_reaches_an_inheriting_agent() -> None:
    """The inheritance path for the clips. The layer under every agent is
    a reload's now, so an agent that configures no filled pause of its
    own is synthesized from what it inherits."""
    stages = dict.fromkeys(("llm", "asr", "tts", "vad"), "mock")
    running = served(agent_defaults=stages, agents={"assistant": {"prompt": "A"}})
    stored = served(
        agent_defaults=stages | {"filler": {"enabled": True, "phrases": ["Hmm..."]}},
        agents={"assistant": {"prompt": "A"}},
    )

    generations, result = await applied(running, stored)

    section = generations.current().config.filler_for_agent("assistant")
    assert section is not None and section.phrases == ["Hmm..."]
    assert result.fillers is not None
    assert (result.fillers.resynthesized, result.fillers.reused) == (["assistant"], [])
    assert generations.current().fillers["assistant"].phrases == ("Hmm...",)


async def test_a_session_opened_before_an_apply_keeps_the_clips_it_bound() -> None:
    """The convergence point, from the side that must not move. A
    conversation binds its clips when it opens, so a re-synthesized one
    reaches the next conversation and never changes what this one is
    masking with; the one opened after the apply has the new clips."""
    running = masking()
    stored = masking("assistant", "Let me think...")
    providers, clips = await synthesized(running)
    generations, reload = applying(
        running, stored, fillers=clips, providers=providers
    )
    before = talking_to(running, generations)

    await reload.apply()
    after = talking_to(running, generations)

    # White-box, and the only way to say it: which clips a conversation
    # is masking with is not on the device boundary, deliberately, since
    # the edge has no business knowing a conversation masks at all.
    assert before.runtime._filler._fillers["assistant"] is clips["assistant"]
    assert after.runtime._filler._fillers["assistant"] is not clips["assistant"]
    assert after.runtime._filler._fillers["assistant"].phrases == ("Let me think...",)


# The engines, and what an apply does to one
#
# The half a prompt edit must never pay for. An entry whose definition
# and stored credential are what they were is carried into the new world
# as the object it already was, and only what really moved is built
# again; what nothing holds any more is let go of, and what a
# conversation is still speaking through is not.


class Closing(MockTts):
    """A voice that remembers being closed, so a test can see a world
    let go of one."""

    egress = False

    def __init__(self, **options: object) -> None:
        super().__init__(sample_rate=24000, ms_per_char=1.0, min_ms=20.0)
        self.closes = 0

    async def close(self) -> None:
        self.closes += 1


def voices(**options: object) -> Config:
    """One agent whose voice is an entry a case can rewrite."""
    return served(
        providers={
            "llm": {"mock": {"type": "mock"}},
            "asr": {"mock": {"type": "mock"}},
            "tts": {"voice": {"type": "mock", **options}},
            "vad": {"mock": {"type": "mock"}},
        },
        agent_defaults={"llm": "mock", "asr": "mock", "vad": "mock"},
        agents={"assistant": {"prompt": "A", "tts": "voice"}},
    )


async def test_a_prompt_only_apply_reuses_every_engine() -> None:
    """The point of the comparison, pinned by object identity: an edit
    to a prompt sends nothing to a model loader, so every engine in the
    new world is the very object the old one held."""
    running = served(agents={"assistant": {"prompt": "A"}})
    stored = served(agents={"assistant": {"prompt": "B"}})
    generations, reload = applying(running, stored)
    booted = generations.current().providers

    result = await reload.apply()

    assert result.providers is not None
    assert result.providers.built == []
    assert result.providers.reused == ["asr.mock", "llm.mock", "tts.mock", "vad.mock"]
    assert result.providers.retired == []
    # Identity, not equality, and every entry of it: two objects built
    # from one entry are exactly what this milestone tells apart, so an
    # apply that rebuilt them all would satisfy equality and fail here.
    installed = generations.current().providers
    assert [installed.instances[name] is booted.instances[name] for name in booted.instances] == [
        True,
        True,
        True,
        True,
    ]
    assert installed.agents["assistant"].tts is booted.agents["assistant"].tts
    # And the prompt half really did move, so this is reuse across an
    # apply that changed something.
    assert result.prompts.changed == ["assistant"]


async def test_a_rewritten_entry_is_built_again_and_the_old_one_is_let_go() -> None:
    """The other side of the same decision. The entry moved, so the new
    world has a new object, and the old one is closed once the world
    holding it is nobody's."""
    running, stored = voices(tone_hz=440.0), voices(tone_hz=880.0)
    old = voiced_by(running, Closing())
    generations, result = await applied(running, stored, providers=old)

    assert result.providers is not None
    assert result.providers.built == ["tts.voice"]
    assert result.providers.reused == ["asr.mock", "llm.mock", "vad.mock"]
    installed = generations.current().providers.instances
    assert installed["tts.voice"] is not old.instances["tts.voice"]
    assert cast(Closing, old.instances["tts.voice"]).closes == 1


async def test_an_engine_a_live_conversation_holds_is_not_closed_under_it() -> None:
    """The wait. A conversation speaks through the world it was built
    from for the rest of its life, so an apply that replaced its voice
    leaves that voice alone until the conversation ends."""
    running, stored = voices(tone_hz=440.0), voices(tone_hz=880.0)
    old = voiced_by(running, Closing())
    holding: list[Generation] = []
    generations, reload = applying(
        running, stored, providers=old, held=lambda: list(holding)
    )
    holding.append(generations.current())
    voice = cast(Closing, old.instances["tts.voice"])

    await reload.apply()
    assert voice.closes == 0

    # The last conversation on it ends, and the world it was holding
    # lets go of what nothing else is speaking through.
    holding.clear()
    await generations.dispose(holding)
    assert voice.closes == 1


async def test_an_entry_the_last_agent_stopped_naming_retires() -> None:
    """What `retired` names, now that there is a way for an entry to
    leave a world at all.

    A world builds the entries its agents reference and no others, so an
    entry can only leave one when the last agent that named it does. The
    store deletes that agent, the apply installs a world with no reason
    to build its voice, and the section says so. Retired is not closed:
    when the engine is actually released depends on the conversations
    still holding it, and the world here is holding none.
    """
    stages = {"llm": "mock", "asr": "mock", "vad": "mock"}
    both = served(
        providers={
            "llm": {"mock": {"type": "mock"}},
            "asr": {"mock": {"type": "mock"}},
            "tts": {"voice": {"type": "mock"}, "spare": {"type": "mock"}},
            "vad": {"mock": {"type": "mock"}},
        },
        agent_defaults=stages,
        agents={
            "assistant": {"prompt": "A", "tts": "voice"},
            "helper": {"prompt": "H", "tts": "spare"},
        },
        devices={DEVICE: ["assistant", "helper"]},
    )
    without_helper = served(
        providers={
            "llm": {"mock": {"type": "mock"}},
            "asr": {"mock": {"type": "mock"}},
            "tts": {"voice": {"type": "mock"}},
            "vad": {"mock": {"type": "mock"}},
        },
        agent_defaults=stages,
        agents={"assistant": {"prompt": "A", "tts": "voice"}},
        devices={DEVICE: ["assistant"]},
    )

    generations, result = await applied(both, without_helper)

    assert result.providers is not None
    assert result.providers.retired == ["tts.spare"]
    assert result.providers.reused == ["asr.mock", "llm.mock", "tts.voice", "vad.mock"]
    assert "tts.spare" not in generations.current().providers.instances
    assert result.agents is not None
    assert result.agents.removed == ["helper"]


async def test_an_entry_a_rewritten_world_still_names_is_not_retired() -> None:
    """The other side of the same word. An entry whose definition moved
    is built again and is under `built`; `retired` is for a name no
    world after this apply serves at all."""
    _, result = await applied(voices(tone_hz=440.0), voices(tone_hz=880.0))

    assert result.providers is not None
    assert (result.providers.built, result.providers.retired) == (["tts.voice"], [])


async def test_an_egress_refusal_leaves_the_running_engines_exactly_as_they_were() -> None:
    """The promise the double residency buys. The candidate's voice is
    built, refused by the egress rule, and closed; what this server is
    serving is the same object it was serving before the request."""
    running = voices()
    stored = served(
        providers={
            "llm": {"mock": {"type": "mock"}},
            "asr": {"mock": {"type": "mock"}},
            # A marking the type decides for itself, which is refused in
            # any mode and only once the object exists to be asked.
            "tts": {"voice": {"type": "mock", "egress": False}},
            "vad": {"mock": {"type": "mock"}},
        },
        agent_defaults={"llm": "mock", "asr": "mock", "vad": "mock"},
        agents={"assistant": {"prompt": "A", "tts": "voice"}},
    )
    generations, reload = applying(running, stored)
    serving_now = generations.current()

    with pytest.raises(ProviderRefusedError) as caught:
        await reload.apply()

    assert generations.current() is serving_now
    assert generations.current().providers.instances == serving_now.providers.instances
    # And the mark says so too, which is what a reader composing an
    # answer across an await is holding: a refusal that built a model
    # and threw it away is still a refusal that moved nothing.
    assert generations.mark == 0
    # And the sentence says nothing about the entry, the type or the
    # option it refused on, all of which are stored values.
    assert "voice" not in str(caught.value)
    # Nor does anything behind it: the refusal is composed after the
    # handler has closed, so neither what the provider layer raised nor
    # anything it was holding travels with it, and a traceback rendered
    # from this carries the fixed sentence and nothing else.
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


async def test_a_typed_options_refusal_reaches_the_reload_as_the_fixed_sentence(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """#88's refusal, met where detail is deliberately withheld.

    A write names the field, because a caller wrote it and can correct
    it. A reload is about stored values nobody sent, so the answer is
    the fixed sentence and the log gets the class name, and this is the
    case that proves the new refusal is inside that rule rather than
    beside it: the option name and the value are both stored, and
    neither may travel.
    """
    planted = "sk-live-71b0c4e3-never-a-real-credential"
    running = voices()
    stored = served(
        providers={
            "llm": {"mock": {"type": "mock"}},
            "asr": {"ears": {"type": "faster_whisper", "beam_size": planted}},
            "tts": {"voice": {"type": "mock"}},
            "vad": {"mock": {"type": "mock"}},
        },
        agent_defaults={"llm": "mock", "asr": "ears", "vad": "mock"},
        agents={"assistant": {"prompt": "A", "tts": "voice"}},
    )
    generations, reload = applying(running, stored)
    serving_now = generations.current()

    with caplog.at_level(logging.DEBUG), pytest.raises(ProviderRefusedError) as caught:
        await reload.apply()

    assert generations.current() is serving_now
    assert planted not in str(caught.value)
    assert "beam_size" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert planted not in caplog.text
    assert "beam_size" not in caplog.text


async def test_a_voice_that_will_not_close_still_leaves_an_applied_world(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Teardown never refuses. A close that raises runs after the world
    has already moved, so the apply answers, the mark settles, and
    neither the answer nor the log carries what the failure said."""

    class Refusing(Closing):
        async def close(self) -> None:
            await super().close()
            raise RuntimeError(TEARDOWN_PLANTED)

    running, stored = voices(tone_hz=440.0), voices(tone_hz=880.0)
    generations, reload = applying(
        running, stored, providers=voiced_by(running, Refusing())
    )

    with caplog.at_level("WARNING"):
        result = await reload.apply()

    assert result.providers is not None
    assert result.providers.built == ["tts.voice"]
    assert generations.mark == 1
    assert TEARDOWN_PLANTED not in caplog.text
    assert TEARDOWN_PLANTED not in json.dumps(result.model_dump(mode="json"))
    assert "RuntimeError" in caplog.text


async def test_a_voice_that_will_not_finish_closing_still_settles_the_apply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other half of the same posture, and the one an operator
    waits through. A close that never finishes is bounded once for the
    whole teardown, and the apply it runs behind answers, settles its
    mark and gives the exclusion back whatever the bound found."""

    class Hanging(Closing):
        async def close(self) -> None:
            await super().close()
            await asyncio.Event().wait()

    monkeypatch.setattr(provider_world, "DISPOSAL_TIMEOUT_S", 0.1)
    running, stored = voices(tone_hz=440.0), voices(tone_hz=880.0)
    generations, reload = applying(
        running, stored, providers=voiced_by(running, Hanging())
    )

    started = time.monotonic()
    result = await reload.apply()
    elapsed = time.monotonic() - started

    assert result.providers is not None
    assert result.providers.built == ["tts.voice"]
    assert elapsed < 1.0, elapsed
    assert generations.mark == 1
    assert reload.running is False


# Planted, and shaped so that a substring check for it cannot match by
# accident: what a client says while failing to shut is exactly the
# shape of thing that quotes an endpoint or a credential.
TEARDOWN_PLANTED = "sk-apply-teardown-91f3c7-never-a-real-credential"


# What an apply refuses


async def test_a_fragment_and_the_layer_naming_it_go_together() -> None:
    """What used to be the overlay's one refusal, and is now an ordinary
    apply.

    A fragment deleted in the store while a layer this server was keeping
    still named it described a world nothing could serve, so the apply
    refused and said to restart. Nothing is kept back any more, so the
    store's own world is what gets installed: the deletion and the layer
    that named it arrive together, exactly as they were written.
    """
    defaults = dict.fromkeys(("llm", "asr", "tts", "vad"), "mock") | {
        "prompt_includes": ["house"]
    }
    running = served(
        prompt_fragments={"house": {"text": "Quiet."}},
        agent_defaults=defaults,
        agents={"assistant": {"prompt": "A"}},
    )
    # The store deleted the fragment and the layer that names it in the
    # same breath, which was a perfectly valid stored world all along.
    stored = served(agents={"assistant": {"prompt": "A"}})
    generations, result = await applied(running, stored)

    applied_config = generations.current().config
    assert applied_config.prompt_fragments == {}
    assert applied_config.fragments_for_agent("assistant") == []
    assert result.prompts.changed == ["assistant"]
    assert generations.mark == 1


class _Held:
    """A stored read a test releases when it likes.

    The read is where the first half of an apply spends its await, so it
    is where a second one has to arrive to meet the exclusion at all.
    Semaphores rather than events because the wait for the read to have
    started happens off the loop, which is the only way to observe a
    worker thread from a coroutine without racing it.
    """

    def __init__(self, answer: Config) -> None:
        self._answer = reading(answer)
        self._entered = threading.Semaphore(0)
        self._release = threading.Semaphore(0)

    def __call__(self) -> BootConfig:
        self._entered.release()
        assert self._release.acquire(timeout=30)
        return self._answer()

    async def in_flight(self) -> None:
        assert await asyncio.to_thread(self._entered.acquire, True, 30)

    def let_through(self) -> None:
        self._release.release()


async def test_a_second_apply_while_one_is_running_is_refused() -> None:
    """One at a time, refused rather than queued: a second would carry a
    configuration read later than the first one's into a world the first
    is in the middle of replacing."""
    running = served(agents={"assistant": {"prompt": "A"}})
    held = _Held(running)
    reload = ConfigReload(
        world(running, providers=built_world(running)),
        McpServers.build(running),
        held,
    )

    first = asyncio.create_task(reload.apply())
    await held.in_flight()
    try:
        with pytest.raises(ReloadInProgressError) as caught:
            await reload.apply()
    finally:
        held.let_through()
        await first

    assert "already running" in str(caught.value)
    # And the exclusion is released once the first has finished, so the
    # next one is answered.
    held.let_through()
    assert (await reload.apply()).prompts.changed == []


# What a live session reads across one


def voiced_by(config: Config, tts: object) -> ProviderWorld:
    """The world a server would be running with this voice in it, in
    both halves: what the agent speaks through and what the entry
    resolves to."""
    return agent_providers(config, stages={"tts": cast(Any, tts)})


def scripted(config: Config, llm: RecordingLlm) -> ProviderWorld:
    """The world a server would be running with a model that records
    what it was sent.

    The substitution goes into the world rather than into the session,
    because a session speaks through its generation's engines now: a
    script handed to a session directly would be replaced by whatever
    the world holds the moment an apply installed one.
    """
    return agent_providers(config, {"assistant": llm})


def talking_to(config: Config, generations: Generations):
    """One session built the way the server builds one, against the
    holder an apply installs into and speaking through its engines."""
    return session_for(config, DEVICE, generations=generations)


async def test_a_session_activated_before_an_apply_keeps_its_know_how() -> None:
    """The convergence point, from the side that must not move. Prompt
    text is assembled once per activation and cached for it, so a
    conversation already in progress goes on speaking the world it was
    activated in."""
    running = served(agents={"assistant": {"prompt": "BEFORE"}})
    stored = served(agents={"assistant": {"prompt": "AFTER"}})
    llm = RecordingLlm(["one", "two"])
    generations, reload = applying(running, stored, providers=scripted(running, llm))
    session = talking_to(running, generations)

    await run_reply(session, "hello")
    await reload.apply()
    await run_reply(session, "hello again")

    assert [system.startswith("BEFORE") for system in llm.systems] == [True, True]


async def test_a_session_opened_after_an_apply_assembles_the_new_text() -> None:
    """And the side that must: a session opening now reads the holder,
    so it is activated in the world the apply installed."""
    running = served(agents={"assistant": {"prompt": "BEFORE"}})
    stored = served(agents={"assistant": {"prompt": "AFTER"}})
    llm = RecordingLlm()
    generations, reload = applying(running, stored, providers=scripted(running, llm))

    await reload.apply()
    await run_reply(talking_to(running, generations), "hello")

    assert llm.systems[-1].startswith("AFTER")


async def test_an_applied_fragment_reaches_the_next_activation() -> None:
    running = served(
        prompt_fragments={"house": {"text": "The house is quiet."}},
        agents={"assistant": {"prompt": "A", "prompt_includes": ["house"]}},
    )
    stored = served(
        prompt_fragments={"house": {"text": "The house is loud."}},
        agents={"assistant": {"prompt": "A", "prompt_includes": ["house"]}},
    )
    llm = RecordingLlm()
    generations, reload = applying(running, stored, providers=scripted(running, llm))

    await reload.apply()
    await run_reply(talking_to(running, generations), "hello")

    assert "The house is loud." in llm.systems[-1]
    assert "The house is quiet." not in llm.systems[-1]


# The agent set, from a conversation's side
#
# An apply moves which agents this server can be asked for, and a
# conversation is the one thing that cannot be asked to move with it: it
# was built from a world, it is speaking through that world's engines,
# and its device is bound to whatever the binding said when it opened.
# So a deleted agent has two answers rather than one, and both are here.


def two_agents(**agents: object) -> Config:
    """One device bound to two agents, either of which the store may
    then delete."""
    return served(
        agents=dict(agents), devices={DEVICE: sorted(agents)}
    )


async def test_a_deleted_agent_still_answers_the_session_that_was_serving_it() -> None:
    """The rule that makes a deletion survivable, at the moment it
    becomes reachable.

    A conversation is bound to the world it opened in, and its device's
    bound list is that world's. An apply removes the agent it is talking
    as; the next activation reads the current world, does not find it
    there, and falls back to the session's own rather than indexing an
    entry that is gone. What the model is sent is the prompt this
    conversation has been served all along.
    """
    running = two_agents(
        assistant={"prompt": "ASSISTANT"}, helper={"prompt": "HELPER"}
    )
    stored = served(agents={"assistant": {"prompt": "ASSISTANT"}})
    handover = ScriptedLlm([[call("switch_agent", agent="helper")]])
    helper = RecordingLlm(["Helper here."])
    generations, reload = applying(
        running,
        stored,
        providers=agent_providers(running, {"assistant": handover, "helper": helper}),
    )
    session = talking_to(running, generations)

    await reload.apply()
    await run_reply(session, "hand me over")

    assert "helper" not in generations.current().config.agents
    assert helper.systems[-1].startswith("HELPER")


async def test_a_retained_agent_reads_the_world_the_apply_installed() -> None:
    """The other half of the same rule, which is what keeps the fallback
    a fallback: an agent the current world does hold is read out of it,
    so a handover mid-conversation meets the prompt the apply installed
    rather than the one this session opened on."""
    running = two_agents(
        assistant={"prompt": "ASSISTANT"}, helper={"prompt": "BEFORE"}
    )
    stored = two_agents(
        assistant={"prompt": "ASSISTANT"}, helper={"prompt": "AFTER"}
    )
    handover = ScriptedLlm([[call("switch_agent", agent="helper")]])
    helper = RecordingLlm(["Helper here."])
    generations, reload = applying(
        running,
        stored,
        providers=agent_providers(running, {"assistant": handover, "helper": helper}),
    )
    session = talking_to(running, generations)

    await reload.apply()
    await run_reply(session, "hand me over")

    assert helper.systems[-1].startswith("AFTER")


async def test_the_preview_and_the_comparison_agree_with_an_activation() -> None:
    """The three surfaces that answer about one agent's prompt, taken
    against one apply.

    They are three different questions and they read one world: the
    comparison says what is stored and not yet served, the preview says
    what a session opening now would be sent, and the activation is what
    a session is actually sent. Before the apply the comparison names
    the agent and the other two answer the old text; after it the
    comparison is empty and the other two answer the new one, character
    for character.
    """
    running = served(agents={"assistant": {"prompt": "BEFORE"}})
    stored = served(agents={"assistant": {"prompt": "AFTER"}})
    llm = RecordingLlm()
    generations = world(running, providers=scripted(running, llm))
    servers = McpServers.build(running)
    reload = ConfigReload(generations, servers, reading(stored))
    preview = _prompt_preview(generations, servers, lane_memory())
    diff = config_diff_reader(generations, servers, reading(stored))

    pending = await diff()
    assert pending.agents.prompt.changed == ("assistant",)
    assert (await preview("assistant")).text == "BEFORE"

    await reload.apply()

    settled = await diff()
    assert settled.agents.prompt.changed == ()
    assembled = await preview("assistant")
    assert assembled.text == "AFTER"
    await run_reply(talking_to(running, generations), "hello")
    assert llm.systems[-1] == assembled.text


# What a refusal says, and what it does not carry
#
# The composition root's half: the closure the API is handed, which runs
# the re-read where a reload runs it and replaces a refused stored half's
# sentence with a fixed one. What is refused there is arbitrary stored
# state, and a sentence composed over it can quote a value written into
# the wrong column, which a credential pasted into one is exactly the
# shape of. So these cases take a real database and a real key: a stub
# would be asserting on the stub.

STAGES = ("llm", "asr", "tts", "vad")

# The forms a stored credential takes, each planted where an answer that
# carried it would have to put it, and each shaped so that a substring
# check for it cannot match by accident.
PLAINTEXT = "sk-reload-2b4d6f80-never-a-real-credential"

ROTATED = "sk-reload-7e1c3a95-also-never-a-real-credential"

ENV_NAME = "VINGA_RELOAD_SENTINEL_ENV_4d8e2a"

# What a refused row holds, which is what the fixed sentence exists to
# keep out of an answer.
REJECTED = "sk-stored-in-the-wrong-column-9b2e"

RELOAD_PATH = f"{MOUNT_PATH}/runtime/config/reload"

TOKEN = "test-api-token-" + "0123456789abcdef" * 2

API_SECRET_ENV = "VINGA_API_SECRET"


def bearer() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The database directory a deployment names through its
    environment, which is what makes `load_boot_config` read this test's
    database rather than a real one's."""
    monkeypatch.delenv("VINGA_CONFIG", raising=False)
    monkeypatch.setenv(API_SECRET_ENV, TOKEN)
    return tmp_path / "db"


@pytest.fixture
def keys(monkeypatch: pytest.MonkeyPatch) -> str:
    key = generate_key()
    monkeypatch.setenv(MASTER_KEY_ENV, key)
    return key


def seeded(directory: Path, secret: str | None = None, **entries: object) -> None:
    """A deployment's stored domain half, written the way the API writes
    it, plus whatever a case adds."""
    engine = open_database(DatabaseConfig())
    try:
        store = ConfigStore(engine, load_keys())
        for stage in STAGES:
            store.set_provider(stage, "mock", entries.get(stage, {"type": "mock"}))
        store.set_agent_defaults(dict.fromkeys(STAGES, "mock"))
        store.set_agent("assistant", {"prompt": "A"})
        store.set_default_agent("assistant")
        if secret is not None:
            store.set_secret(SecretLocation.provider("llm", "mock", "api_key"), secret)
    finally:
        engine.dispose()


def envelope_of(directory: Path) -> str:
    """The ciphertext the database holds for the planted slot, read as a
    row rather than through the store: what must not travel is the bytes
    on disk, so the sentinel has to be taken from the disk."""
    engine = open_database(DatabaseConfig())
    try:
        with engine.connect() as connection:
            secrets = connection.execute(
                select(schema.providers.c.secrets).where(
                    schema.providers.c.stage == "llm",
                    schema.providers.c.name == "mock",
                )
            ).scalar_one()
    finally:
        engine.dispose()
    # A value rather than the text it was dumped to: psycopg reads a
    # `json` column into Python objects, where the SQLite driver handed
    # back the string.
    return str(secrets["api_key"]["enc"])


def mark_of(directory: Path) -> str:
    """The mark taken over what the database holds for the planted slot
    right now, which is the stored side of the comparison a later
    milestone will rebuild providers by."""
    engine = open_database(DatabaseConfig())
    try:
        snapshot = ConfigStore(engine, load_keys()).load()
    finally:
        engine.dispose()
    return snapshot.secrets.fingerprint("provider", "llm.mock")


def logged(caplog: pytest.LogCaptureFixture) -> str:
    """What the server kept about a request, in both shipped formats."""
    return caplog.text + "".join(
        JsonFormatter().format(record) for record in caplog.records
    )


def failing(exc: Exception):
    def read() -> BootConfig:
        raise exc

    return read


def reloader(exc: Exception):
    """The composition root's closure over a read that refuses."""
    running = served(agents={"assistant": {"prompt": "A"}})
    return config_reloader(
        world(running, providers=built_world(running)),
        McpServers.build(running),
        failing(exc),
    )


@pytest.mark.usefixtures("keys")
def test_a_stored_secret_that_will_not_open_refuses_under_a_fixed_sentence(
    directory: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The re-read verifies that every stored credential opens before it
    composes anything, so a deployment whose key has been rotated away
    from its secrets is refused with nothing swapped.

    The sentence is the route's own and not the store's: what the store
    would have said names the slot, and a reload's stored half is
    arbitrary bytes."""
    seeded(directory, secret=PLAINTEXT)
    booted = load_boot_config()

    with entered_client(booted.config, booted.secrets, from_store=True) as serving:
        monkeypatch.setenv(MASTER_KEY_ENV, generate_key())
        refused = serving.post(RELOAD_PATH, headers=bearer())

    assert refused.status_code == 422
    # Fixed, and saying so: where exactly the stored half was refused is
    # the one thing a reload's answer never carries, because a sentence
    # composed over stored state can quote what was written into the
    # wrong column.
    assert "deliberately not said here" in refused_body(refused.json(), 422)
    assert "api_key" not in refused.text


@pytest.mark.usefixtures("keys")
def test_a_stored_domain_that_will_not_compose_refuses_the_same_way(
    directory: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Model-valid rows that are not a valid deployment, planted as a
    row because no write this server offers can produce one. What the
    row holds is as likely to be a credential pasted into the wrong
    column as a name somebody mistyped, which is the whole reason the
    sentence is fixed.

    This is also where the stored half being validated in one place
    shows: the apply used to compose a candidate out of the store and
    the world it was keeping, and could refuse on the combination. It
    installs what the store describes now, so a refusal here is the
    store's own validation, and what it must still be is a refusal that
    changed nothing.

    "The same way" is asserted rather than asserted about: both causes
    are driven here, one after the other over one store, and the two
    answers are held equal. A sentence copied into this file could not
    make that claim, since a copy agrees with itself whatever the two
    refusals do.
    """
    seeded(directory, secret=PLAINTEXT)
    booted = load_boot_config()
    _plant_unknown_provider()

    with entered_client(booted.config, booted.secrets, from_store=True) as serving:
        generations = serving.app.state.composition.generations
        before = generations.current()

        uncomposable = serving.post(RELOAD_PATH, headers=bearer())

        # Nothing moved, which is what "and nothing was changed" in the
        # sentence promises: the same world, and a mark that says so to
        # anything composing an answer across an await.
        assert generations.current() is before
        assert generations.mark == 0

    # The row put back, so the only thing wrong with the store is the
    # credential that will no longer open.
    _drop_planted_provider()

    with entered_client(booted.config, booted.secrets, from_store=True) as serving:
        monkeypatch.setenv(MASTER_KEY_ENV, generate_key())
        unopenable = serving.post(RELOAD_PATH, headers=bearer())

    assert uncomposable.status_code == 422
    assert unopenable.status_code == 422
    assert refused_body(uncomposable.json(), 422) == refused_body(unopenable.json(), 422)
    assert REJECTED not in uncomposable.text
    assert "api_key" not in unopenable.text


def _plant_unknown_provider(name: str = REJECTED) -> None:
    """An agent naming a provider nothing declares, written as a row
    because no write this server offers can produce one. Into the body
    rather than into a column of its own, which is where every non-key
    field lives since #243.

    Read, edited in Python, written back, which is what replaced the
    SQLite `json_set` this used to call (#283): the body is a text
    column holding a dumped model whichever backend it sits in, and
    editing it here leaves the rest of the entry exactly as it was
    written without either backend's JSON functions in the way.
    """
    _rewrite_agent_body(lambda body: {**body, "llm": name})


def _drop_planted_provider() -> None:
    """The same row without the planted key, which is the entry the
    seeding wrote: the agent names no llm of its own and inherits the
    one `agent_defaults` names."""
    _rewrite_agent_body(lambda body: {k: v for k, v in body.items() if k != "llm"})


def _rewrite_agent_body(edit) -> None:
    engine = open_database(DatabaseConfig())
    try:
        with engine.begin() as connection:
            for name_, body in connection.execute(
                select(schema.agents.c.name, schema.agents.c.body)
            ).all():
                connection.execute(
                    update(schema.agents)
                    .where(schema.agents.c.name == name_)
                    .values(body=json.dumps(edit(json.loads(body))))
                )
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "raised", [ConfigError, StorageError, DatabaseBusyError]
)
async def test_a_refused_stored_half_keeps_its_type_and_loses_its_words(
    raised: type[ConfigError],
) -> None:
    """The type is what the API turns into a status, so it survives; the
    words are composed over stored state, so they do not.

    Built in the handler and raised after it, which is load bearing:
    raised inside one, the replacement would carry the original as its
    context, and anything walking an exception chain would find the
    words again with the sanitizing bypassed."""
    apply = reloader(raised(f'agents.assistant.llm: unknown provider "{REJECTED}"'))

    with pytest.raises(raised) as caught:
        await apply()

    assert type(caught.value) is raised
    # The words are this server's own rather than the ones that were
    # raised, which is what "loses its words" means here.
    assert REJECTED not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


async def test_the_exclusion_refusal_keeps_its_own_words() -> None:
    """The one refusal the closure passes through as itself: a second
    apply while one is running is about this server's own exclusion and
    was composed over nothing stored, so replacing its sentence would
    lose the only advice it carries."""
    running = served(agents={"assistant": {"prompt": "A"}})
    held = _Held(running)
    apply = config_reloader(
        world(running, providers=built_world(running)),
        McpServers.build(running),
        held,
    )

    first = asyncio.create_task(apply())
    await held.in_flight()
    try:
        with pytest.raises(ReloadInProgressError) as caught:
            await apply()
    finally:
        held.let_through()
        await first

    assert "already running" in str(caught.value)


@pytest.mark.usefixtures("keys")
def test_neither_an_answer_nor_a_refusal_carries_a_credential(
    directory: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Every form of both sides, over both paths, in the body and in the
    log.

    Both sides, because an apply reads two: the world this server is
    serving and the one the database holds. The credential is rotated so
    that the two really differ, which means the running side holds one
    value and the stored side another, and each has a plaintext, a
    ciphertext and a mark of its own. Sentinels taken only before the
    rotation would leave the values the apply actually read on the
    stored side unchecked.
    """
    seeded(directory, secret=PLAINTEXT, llm={"type": "mock", "api_key_env": ENV_NAME})
    booted = load_boot_config()
    booted_side = (
        PLAINTEXT,
        envelope_of(directory),
        booted.secrets.fingerprint("provider", "llm.mock"),
    )

    with caplog.at_level("INFO"), entered_client(
        booted.config, booted.secrets, from_store=True
    ) as serving:
        rotated = serving.put(
            f"{MOUNT_PATH}/providers/llm/mock/secrets/api_key",
            json={"secret": ROTATED},
            headers=bearer(),
        )
        assert rotated.status_code == 200, rotated.text
        stored_side = (ROTATED, envelope_of(directory), mark_of(directory))
        sentinels = (*booted_side, *stored_side, ENV_NAME)
        # Seven distinct strings that are all really there, so an
        # absence asserted below is an absence rather than an empty
        # needle or a value that was never read.
        assert len(set(sentinels)) == 7
        assert all(sentinels)

        answered = serving.post(RELOAD_PATH, headers=bearer())
        assert answered.status_code == 200, answered.text

        monkeypatch.setenv(MASTER_KEY_ENV, generate_key())
        refused = serving.post(RELOAD_PATH, headers=bearer())
        assert refused.status_code == 422

    for sentinel in sentinels:
        assert sentinel not in answered.text
        assert sentinel not in refused.text
        assert sentinel not in logged(caplog)


# What an entry that will not build says on the wire, and what it does
# not
#
# The refusal this milestone added, driven the whole way: a real store, a
# real re-read, a real build that refuses, and the mounted route in
# front of it. Everything a provider refusal has to say about is stored
# state (the entry, the option, the type, the credential), which is why
# the sentence it answers with is fixed and interpolates nothing.

# An entry name and an option name, both of them stored keys an operator
# chose, and both shaped so a substring check for them cannot match by
# accident.
PLANTED_ENTRY = "voice-9c4a1f-never-a-real-entry"

PLANTED_OPTION = "planted_option_3f9c_never_a_real_option"


def served_by(caplog: pytest.LogCaptureFixture) -> str:
    """What this server wrote about a request, in both shipped formats.

    This server's own records and not every record the run produced: a
    client library logs the URL it called, and an entry's name is how
    that entry is addressed, so the request line an operator's proxy
    keeps is the API's addressing rather than anything a refusal said.
    What this asserts about is what the refusal wrote.
    """
    ours = [record for record in caplog.records if record.name.startswith("vinga_server")]
    return "".join(record.getMessage() for record in ours) + "".join(
        JsonFormatter().format(record) for record in ours
    )


def voiced(directory: Path, entry: dict[str, object]) -> None:
    """The voice every agent inherits, under a name of this case's
    choosing, so that the entry an apply refuses on is one this server
    is really speaking through: a world builds the entries its agents
    reference and no others."""
    engine = open_database(DatabaseConfig())
    try:
        store = ConfigStore(engine, load_keys())
        store.set_provider("tts", PLANTED_ENTRY, entry)
        store.set_agent_defaults(dict.fromkeys(STAGES, "mock") | {"tts": PLANTED_ENTRY})
    finally:
        engine.dispose()


@pytest.mark.usefixtures("keys")
def test_an_engine_that_will_not_build_refuses_the_route_and_names_none_of_it(
    directory: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The whole path for a provider refusal: a stored entry this server
    cannot build, applied through the route an operator calls.

    What it must answer is the fixed sentence under 422, with the world
    it is serving untouched, and none of what it refused on anywhere: not
    the entry, not the option, not in the body and not in either shipped
    log format.
    """
    seeded(directory)
    voiced(directory, {"type": "mock"})
    booted = load_boot_config()

    with caplog.at_level("INFO"), entered_client(
        booted.config, booted.secrets, from_store=True
    ) as serving:
        composition = serving.app.state.composition
        before = composition.generations.current()
        # The voice this server is really speaking through, rewritten
        # with an option the provider never asked about. The write is
        # stored, as every write is, and the apply is where it refuses.
        wrote = serving.put(
            f"{MOUNT_PATH}/providers/tts/{PLANTED_ENTRY}",
            json={"type": "mock", PLANTED_OPTION: 1},
            headers=bearer(),
        )
        assert wrote.status_code == 200, wrote.text

        refused = serving.post(RELOAD_PATH, headers=bearer())

        # Nothing of the world it is serving moved: the same generation,
        # holding the same engines, object for object.
        after = composition.generations.current()
        assert after is before
        assert after.providers.instances == before.providers.instances

    assert refused.status_code == 422
    refused_body(refused.json(), 422)
    written = served_by(caplog)
    for sentinel in (PLANTED_ENTRY, PLANTED_OPTION):
        assert sentinel not in refused.text
        assert sentinel not in written
    # The class of it is what this server keeps, which is what an
    # operator has instead of the sentence.
    assert "ProviderError" in written
