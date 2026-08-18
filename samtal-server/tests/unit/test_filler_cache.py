"""The clip cache the server shares, before and after the boot fills it.

`AgentFillers` exists so that "this agent configured no fillers" and "no
agent has been synthesized yet" stop being the same answer (#142). What
must not change is what a session sees: a lookup before the fill answers
exactly as the empty dictionary the composition root used to hand out,
which is what makes the mask stand down rather than wait.
"""

import pytest

from samtal_server.filler import AgentFillers, FillerClips

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
