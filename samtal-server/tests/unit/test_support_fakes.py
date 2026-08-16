"""What the shared fakes promise, pinned where the fakes live.

A fake that quietly stops being what its users assume leaves their
assertions passing while they test nothing, and a green suite is no
warning at all. The suites that inject `Falsey` assert only that the
provider kept the object they handed it; that assertion holds for any
object whatsoever, so the seam it probes, a client that a `client or
...` fallback would drop on the floor, exists only as long as the probe
answers False to a truth test. Four suites used to spell that premise
out themselves. Now one definition carries it for all of them, and this
is where it is checked.
"""

from tests.support.llm_sdk import Falsey


def test_the_probe_answers_false_to_a_truth_test() -> None:
    assert bool(Falsey()) is False
