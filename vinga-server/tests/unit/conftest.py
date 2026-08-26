"""The unit lane stores things, so it asks for somewhere to store them.

Half of this lane opens the domain store or the conversation store, and
the fixtures that hand one over are in `tests/conftest.py`. What is here
is the declaration that makes them work: the databases are provisioned
when a lane says it needs them rather than whenever this repository's
root conftest happens to be imported, so that `tests/smoke`, which
drives a container over HTTP and stores nothing, does not die at
collection for want of an instance it never uses.

At import, which pytest runs before it imports any test module in this
directory: a module composing its `Config` while being imported, and a
module-scoped fixture opening a store, both come after this line.
"""

from tests.conftest import provision_stores

provision_stores()
