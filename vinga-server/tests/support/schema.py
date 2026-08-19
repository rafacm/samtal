"""Installing a registry of a suite's own, for the length of a test.

Strict enforcement is what every lane runs under (#155), and that is
the point of it: every production emission the suites drive is held to
its declaration. A suite that is not about the production surface at
all is the exception the seam exists for. `test_events.py` emits
synthetic names on a synthetic channel because what it proves is
dispatch, taps, copy semantics and ordering; holding those emissions to
the real registry would be holding a mechanics test to a surface it is
deliberately not exercising.

So the validator reads its registry through module state, and this is
what swaps one in and puts the real one back. Scoped rather than set
once, because the real registry is what every other suite in the same
process needs.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from vinga_server import events
from vinga_server.events_schema import EventSpec


@contextmanager
def scratch_registry(specs: tuple[EventSpec, ...]) -> Iterator[dict[str, EventSpec]]:
    """Validate against these declarations, and only inside the block."""
    installed = {spec.name: spec for spec in specs}
    restored = events.declared_events()
    events.set_declared_events(installed)
    try:
        yield installed
    finally:
        events.set_declared_events(restored)
