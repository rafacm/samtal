"""The timing tool still imports, and still leads with the slowest.

`tests/tools/driver_times.py` is reached by hand, months apart, by
whoever is next chasing the harness's wall time; #254 is the next such
occasion. Nothing else imports it, so a rename inside
`tests/tools/event_baseline.py` would leave it broken with a green
lane, and the person who reached for it would find that out instead of
finding the slow driver.

So this pins the two things that can rot: that the module imports, and
that its report is ordered the way the tool exists to be read. Nothing
here asserts how long a driver takes, deliberately, for the reason the
tool's own docstring gives: that is a judgement about the machine, and
a threshold would either never fire or fail on a loaded runner.
"""

from tests.tools.driver_times import report, timed


def test_the_report_leads_with_the_slowest_and_ends_with_its_totals() -> None:
    # Imported and not run: running it is the eighteen seconds the
    # harness costs, and what this file is about is that the name is
    # still there to run.
    assert callable(timed)

    lines = report([("a", 1.0), ("b", 2.0)]).splitlines()

    assert lines[0].startswith("b")
    assert lines[1].startswith("a")
    assert lines[-2].startswith("TOTAL")
    assert "3.00s" in lines[-2]
    assert lines[-1].startswith("DRIVERS")
    assert lines[-1].split()[-1] == "2"
