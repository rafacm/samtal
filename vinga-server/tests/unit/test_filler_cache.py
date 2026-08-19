"""The clip cache the server shares, before and after the boot fills it.

`AgentFillers` exists so that "this agent configured no fillers" and "no
agent has been synthesized yet" stop being the same answer (#142). What
must not change is what a session sees: a lookup before the fill answers
exactly as the empty dictionary the composition root used to hand out,
which is what makes the mask stand down rather than wait.
"""

import pytest
from fastapi.testclient import TestClient

from tests.support.configs import config_with_agent
from vinga_server import app as app_module
from vinga_server.app import create_app
from vinga_server.config import Config
from vinga_server.filler import AgentFillers, FillerClips
from vinga_server.providers import AgentProviders

CLIP = FillerClips(delay_ms=800.0, phrases=("hmm",), clips=(b"\x00\x00",), sample_rate=16000)


def test_before_the_fill_it_answers_as_an_empty_cache() -> None:
    fillers = AgentFillers()

    assert "assistant" not in fillers
    assert fillers.get("assistant") is None
    with pytest.raises(KeyError):
        fillers["assistant"]


def test_after_the_fill_it_answers_as_the_synthesized_clips() -> None:
    fillers = AgentFillers()

    fillers.fill({"assistant": CLIP})

    assert "assistant" in fillers
    assert fillers["assistant"] is CLIP
    assert fillers.get("assistant") is CLIP
    # An agent nothing synthesized for is still absent, which is the
    # answer that stands the mask down.
    assert "poet" not in fillers
    assert fillers.get("poet") is None


def test_ready_is_what_tells_pending_from_absent() -> None:
    fillers = AgentFillers()
    assert not fillers.ready

    # Even a synthesis that produced nothing has run, and a caller that
    # asks is told so.
    fillers.fill({})

    assert fillers.ready


def test_the_fill_happens_once() -> None:
    fillers = AgentFillers()
    fillers.fill({"assistant": CLIP})

    with pytest.raises(AssertionError):
        fillers.fill({"assistant": CLIP})


def test_a_reference_taken_before_the_fill_sees_the_clips() -> None:
    """What the whole shape is for: everything that will read the cache
    is assembled before the synthesis has run."""
    fillers = AgentFillers()
    held = fillers

    fillers.fill({"assistant": CLIP})

    assert held.get("assistant") is CLIP


def test_startup_fills_the_cache_the_composition_holds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The wiring, end to end: the cache `create_app` hands out is the
    one the lifespan fills, and what it fills it with is what the boot
    synthesized. Without this, a startup that dropped the synthesis
    result on the floor would pass every other test here, because a cache
    that was never filled answers exactly as one with nothing for this
    agent."""

    async def synthesized(
        config: Config, agent_providers: dict[str, AgentProviders]
    ) -> dict[str, FillerClips]:
        return {"assistant": CLIP}

    monkeypatch.setattr(app_module, "build_agent_fillers", synthesized)

    app = create_app(config_with_agent())
    # Before the lifespan there is no composition at all: the build owns
    # the cache along with everything else, so the cold state a reader
    # could ever see is the one inside an entered lifespan.
    assert getattr(app.state, "composition", None) is None

    with TestClient(app):
        fillers = app.state.composition.agent_fillers
        assert fillers.ready
        assert fillers.get("assistant") is CLIP
