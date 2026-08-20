"""The catalog's declarations against the registry's, while both hold
the server channels.

Transcribing thirty-three events into typed variants is the kind of work
a reviewer cannot check by eye: fifty-odd templates, their argument
kinds, their field tables, their requiredness, their nullability and
their notes. So it is proved rather than reviewed. `described()` answers
the same `EventSpec` shape the untyped registry answers, and every event
declared in both sources has to be equal in it, byte for byte.

Temporary by construction, and stated so it is not mistaken for a
lasting claim: it retires in the commit that deletes the registry
entries it compares against, which is the same commit that converts the
sites. What outlives it is the golden inventory and the record baseline,
which is where a shape that moved shows up afterwards.

The same test the store's four declarations and the session channel's
twenty passed on their way in (M1 and M2).
"""

import pytest

from vinga_server.events.catalog import described
from vinga_server.events_schema import REGISTRY

DERIVED = {spec.name: spec for spec in described()}

SHARED = sorted(set(DERIVED) & set(REGISTRY))


def test_the_two_sources_still_overlap() -> None:
    """A comparison over an empty intersection is a comparison of
    nothing, which is exactly how a check like this dies quietly."""
    assert len(SHARED) == 33


@pytest.mark.parametrize("name", SHARED)
def test_the_derived_declaration_is_the_registrys(name: str) -> None:
    """Channel, level, template, argument kinds and their constraints,
    field names in order with their kinds, requiredness, nullability,
    token sets, syntaxes, bounds and notes. All of it, by equality."""
    assert DERIVED[name] == REGISTRY[name]
