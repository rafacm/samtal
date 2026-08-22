"""Declaring into a catalog of a suite's own, for the length of a test.

The catalog is a global by design: a variant type names its event by
belonging to a declaration, and that belonging has to be readable from
anywhere the type is. The cost is that a suite about the declaration
machinery itself cannot declare anything without adding it to the
production surface, where the generated reference would print it and
the driver suite would demand a driver for it.

So the catalog reads its state through one installed object, and this
is what swaps in a copy and puts the original back. A copy rather than
an empty one, because a test that declares a scratch event still needs
`declaration_of` to answer for the production variants beside it.

The same seam, and the same reason, as `tests/support/schema.py`'s
`scratch_registry` for the untyped registry; that one retires with the
registry.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from vinga_server.events import catalog


@contextmanager
def scratch_catalog() -> Iterator[catalog.CatalogState]:
    """Declare into this catalog, and only inside the block."""
    restored = catalog.installed()
    scratch = restored.copy()
    catalog.install(scratch)
    try:
        yield scratch
    finally:
        catalog.install(restored)
