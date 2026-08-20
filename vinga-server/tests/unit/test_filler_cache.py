"""Where the clips a server plays live, and who binds them.

The cache used to be one mutable object the boot filled once and
everything held a reference to. It is a value the generation carries
now (#191), which is what lets a reload put different clips in front of
the next session without a conversation's masking changing under it.

What must not change is what a session sees: an agent nothing
synthesized for is absent, which is what makes the mask stand down
rather than wait.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.support.configs import config_with_agent
from vinga_server import app as app_module
from vinga_server.app import create_app
from vinga_server.config import Config
from vinga_server.filler import FillerClips, Fillers
from vinga_server.providers import AgentProviders

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
