"""The compatibility contract the prose pins used to be.

Event identifiers and typed fields are what a consumer reads; the human
sentence is presentation. The consumer survey behind #210 found nobody
parsing a sentence: the three production taps read structured payloads,
and the reference and the README index are rendered from declarations.
So the surface's pins stop restating templates and argument tuples, and
what replaces them is this: one committed file holding, for every
declared event, its name, its variants' channels and levels, and the
names, declared types, requiredness and nullability of everything they
carry, asserted against the catalog in BOTH directions.

Both directions matter separately. Containment one way misses a
declaration nobody wrote down; containment the other misses a line for
an event that no longer exists, which is a contract describing a
surface this server does not have.

There is no wording in it. Not a template, not a note, not a sentence:
those are free to improve, which is the whole point of moving the pins
here. What is pinned is structure, and a rename, a retype, a reordering
or a presence change is a loud diff on a file a reviewer reads.

Regenerate it deliberately, never by hand:

    uv run python -m tests.unit.test_event_golden
"""

import json
from pathlib import Path
from typing import Any

from vinga_server.events.catalog import catalog, payload_shape

COMMITTED = Path(__file__).resolve().parent / "data" / "event-catalog-golden.json"

REGENERATE = (
    f"{COMMITTED.name} no longer describes the catalog; regenerate it with: "
    f"uv run python -m tests.unit.test_event_golden"
)


def inventory() -> dict[str, Any]:
    """The catalog's structure, as the committed file records it.

    Lists rather than objects wherever order is a fact: a variant's
    position identifies it, and a payload's key order is part of the
    record a consumer reads.
    """
    return {
        name: [
            {
                "channel": variant.CHANNEL,
                "level": variant.LEVEL,
                "arguments": [
                    {"name": one, "type": _typed(variant, one)} for one in variant.ARGS
                ],
                "fields": [
                    {
                        "name": one.name,
                        "type": one.type.__name__,
                        "required": one.required,
                        "nullable": one.nullable,
                    }
                    for one in payload_shape(variant)
                    if one.carried
                ],
            }
            for variant in declaration.variants
        ]
        for name, declaration in catalog().items()
    }


def _typed(variant: Any, name: str) -> str:
    return {one.name: one.type.__name__ for one in payload_shape(variant)}[name]


def committed() -> dict[str, Any]:
    return json.loads(COMMITTED.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def rendered(inventory: dict[str, Any]) -> str:
    return json.dumps(inventory, indent=2) + "\n"


def test_every_declared_event_is_in_the_committed_inventory() -> None:
    missing = sorted(set(inventory()) - set(committed()))

    assert missing == [], f"declared but not recorded: {', '.join(missing)}. {REGENERATE}"


def test_every_recorded_event_is_still_declared() -> None:
    """The direction containment the other way would miss: a line for an
    event that no longer exists describes a surface this server does not
    have."""
    invented = sorted(set(committed()) - set(inventory()))

    assert invented == [], f"recorded but not declared: {', '.join(invented)}. {REGENERATE}"


def test_the_committed_inventory_is_the_catalogs_shape() -> None:
    """Names, channels, levels, argument order and types, field order,
    types, requiredness and nullability. Nothing else, and no wording."""
    assert committed() == inventory(), REGENERATE


def test_the_committed_file_is_what_the_generator_writes() -> None:
    """So that regenerating it is a no-op diff rather than a reformat,
    which is what keeps a real change readable."""
    assert COMMITTED.read_text(encoding="utf-8") == rendered(inventory())


def test_the_inventory_carries_no_wording() -> None:
    """The risk this file was designed against: a golden that ossified
    sentences would pin exactly what #210 set out to free."""
    recorded = COMMITTED.read_text(encoding="utf-8")

    assert "%s" not in recorded
    assert "%d" not in recorded
    for word in ("note", "message", "template", "sentence"):
        assert word not in recorded


if __name__ == "__main__":  # pragma: no cover - the regeneration path
    COMMITTED.parent.mkdir(parents=True, exist_ok=True)
    COMMITTED.write_text(rendered(inventory()), encoding="utf-8")
    print(f"wrote {COMMITTED}")
