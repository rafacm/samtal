"""What the shared fakes promise, pinned where the fakes live.

A fake that quietly stops being what its users assume leaves their
assertions passing while they test nothing, and a green suite is no
warning at all. The suites that inject `Falsey` drive a call through it
and look at what the wrapped client was asked for, so what they prove is
that the injection survived; the thing that makes it worth proving is
that a `client or ...` fallback would have dropped this object, and that
holds only as long as the probe answers False to a truth test. Four
suites used to spell that premise out themselves. Now one definition
carries it for all of them, and this is where it is checked, both
halves: it is false, and it is still the client it wraps.
"""

from tests.support.llm_sdk import Falsey


def test_the_probe_answers_false_to_a_truth_test() -> None:
    assert bool(Falsey(object())) is False


def test_the_probe_forwards_to_the_client_it_wraps() -> None:
    wrapped = type("Client", (), {"messages": "the vendor surface"})()

    assert Falsey(wrapped).messages == "the vendor surface"
