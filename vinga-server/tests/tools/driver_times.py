"""How long each baseline driver takes, which no durations report says.

The event baseline's eighty-one drivers all run inside one module-scoped
`capture()` fixture, so `pytest --durations` reports a single setup line
for the whole harness and nothing per driver. A driver that waits out a
real provider timeout is therefore invisible: whatever the harness
costs, the lane cannot say where it went, which is how four drivers
came to be seventy of its eighty-seven seconds unnoticed. This runs the
same loop `driven()` runs, one tempdir and one `listening()` per
driver, and prints what each one cost:

    uv run python -m tests.tools.driver_times

It measures and prints; it asserts nothing and holds nothing to a
number. What a driver may take is a judgement about this machine and
this runner, and a threshold here would either be so loose it never
fires or so tight it fails on a loaded CI box. The suite next door
(`tests/unit/test_event_baseline.py`) is what holds the drivers to
anything.
"""

import asyncio
import inspect
import tempfile
import time
from pathlib import Path

from tests.tools.event_baseline import DRIVERS, listening


def timed() -> list[tuple[str, float]]:
    """Every driver run once, in declaration order, with its wall time.

    The same shape as `driven()`: a temporary directory per driver, the
    scoped channels listened to while it runs, and a coroutine driver
    run to completion on a loop of its own. Timing anything less would
    time a different thing than the fixture spends.
    """
    times: list[tuple[str, float]] = []
    for driver in DRIVERS:
        started = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="vinga-drivers-") as directory:
            with listening():
                answer = driver.drive(Path(directory))
                if inspect.isawaitable(answer):
                    asyncio.run(answer)
        times.append((driver.key, time.perf_counter() - started))
    return times


def report(times: list[tuple[str, float]]) -> str:
    """The slowest first, because the reason to run this is to find the
    tail."""
    width = max(len(key) for key, _ in times)
    lines = [
        f"{key.ljust(width)}  {seconds:7.2f}s"
        for key, seconds in sorted(times, key=lambda one: -one[1])
    ]
    lines.append(f"{'TOTAL'.ljust(width)}  {sum(one for _, one in times):7.2f}s")
    lines.append(f"{'DRIVERS'.ljust(width)}  {len(times):7d}")
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover - the measurement path
    # The run's environment, set the way a lane sets it, for the reason
    # the regeneration path gives: an app refuses to boot without its
    # secrets and a database needs somewhere writable, and `conftest.py`
    # is where all of that is decided.
    import tests.conftest  # noqa: F401

    print(report(timed()))
