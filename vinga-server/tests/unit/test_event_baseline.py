"""The record baseline, and the obligation that makes it a proof.

The harness in `tests/tools/event_baseline.py` drives every emit path
and captures what it produced. On its own that would prove only what it
happened to execute, which is exactly the hole a runtime harness falls
into. So the completeness claim comes from outside it: from the catalog.

Every variant the catalog declares has to be produced by some driver's
run. Every legal variant is constructible, and therefore directly
drivable, so a declaration nothing can produce is a permanent
enlargement of what this server may say. That is what the plan means by
claiming exhaustiveness over variants rather than over call sites, and
it is what the static walk this file used to carry was standing in for
while an untyped emit site was invisible to anything but a reading of
the source. The walk retired with the last conversion.

A variant is identified by its event, channel, level and template AND by
its payload's keys, because several events say one sentence about two
shapes: `llm_round` reports a provider the registry built out of a
configured entry and one it never built with the same words, and the
four dimensions alone would let either stand in for both.

Beside it, the smaller claim the drivers can give themselves: each one
produces the event it says it does, so a driver whose path stopped
firing fails rather than quietly recording a neighbour's records.

All of it holds before a conversion and after it, which is the point:
the committed capture is a file that does not move when the sites do.
"""

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from tests.tools.event_baseline import (
    COMMITTED,
    DRIVERS,
    NOW,
    SCOPE,
    a_manifest,
    a_turn,
    captured,
    committed,
    driven,
    payload,
    rendered,
)
from vinga_server.conversations.store import ConversationStore
from vinga_server.events.catalog import Variant, catalog, payload_shape

REGENERATE = (
    "the captured records are no longer the committed baseline. If a "
    "conversion was supposed to preserve them, this is the failure it was "
    "written to catch; if the surface changed on purpose, regenerate with: "
    "uv run python -m tests.tools.event_baseline"
)


@pytest.fixture(scope="module")
def produced() -> dict[str, list[logging.LogRecord]]:
    """Driven once for the whole file: every driver opens a database and
    some park a writer thread, so running them per test would pay for
    the same evidence several times over."""
    return driven()


@pytest.fixture(scope="module")
def capture(
    produced: dict[str, list[logging.LogRecord]],
) -> dict[str, list[dict[str, Any]]]:
    """The same run, in the dimensions the committed baseline holds."""
    return captured(produced)


def test_every_driver_names_a_path_of_its_own() -> None:
    """One driver per emit path, so a capture keyed by identity is a
    capture of eighty-one paths rather than of however many survived a
    collision."""
    claimed = [driver.identity for driver in DRIVERS]

    assert len(set(claimed)) == len(claimed) == 81


def test_every_driven_path_produces_the_event_it_emits(
    capture: dict[str, list[dict[str, Any]]],
) -> None:
    """Not merely that a driver produced something: every record kept
    has to be the event that driver names, so a path that stopped firing
    fails rather than quietly recording a neighbour's."""
    for driver in DRIVERS:
        produced = {one["event"] for one in capture[driver.key]}
        assert produced == {driver.event}, driver.key


def matches(variant: type[Variant], record: dict[str, Any]) -> bool:
    """Whether one captured record is an emission of one variant.

    The four dimensions, and then the payload's keys: everything the
    variant always carries is there, and nothing it never declares is.
    That second half is what tells apart the pairs of variants that say
    one sentence about two shapes, since a record naming a configured
    provider entry cannot be the variant that declares no such field.
    """
    shape = payload_shape(variant)
    required = {one.name for one in shape if one.carried and one.required}
    declared = {one.name for one in shape if one.carried}
    keys = set(record["fields"])
    return (
        record["channel"] == variant.CHANNEL
        and record["level"] == variant.LEVEL
        and record["template"] == variant.TEMPLATE
        and required <= keys <= declared
    )


def test_every_catalog_variant_on_a_scoped_channel_is_produced(
    capture: dict[str, list[dict[str, Any]]],
) -> None:
    """The obligation that outlives the walk: every legal variant is
    constructible, and therefore drivable, so a declaration nothing can
    produce is a permanent enlargement of what this server may say.

    Nothing is exempt any more. The one event that was, the emitter's
    own recovery, is undeclared since #239: every declaration left is
    one an ordinary emit site produces."""
    driven: dict[str, list[dict[str, Any]]] = {}
    for records in capture.values():
        for record in records:
            driven.setdefault(record["event"], []).append(record)

    unproduced = [
        f"{name}: {variant.__name__}"
        for name, declaration in catalog().items()
        for variant in declaration.variants
        if variant.CHANNEL in SCOPE
        and not any(matches(variant, one) for one in driven.get(name, []))
    ]

    assert unproduced == []


def test_the_capture_is_the_committed_baseline(
    capture: dict[str, list[dict[str, Any]]],
) -> None:
    """Channel, level, unrendered template, argument types and payload
    keys, per path. What a conversion must not move."""
    assert capture == committed(), REGENERATE


def test_the_committed_file_is_what_the_harness_writes(
    capture: dict[str, list[dict[str, Any]]],
) -> None:
    """So that regenerating is a no-op diff rather than a reformat."""
    assert COMMITTED.read_text(encoding="utf-8") == rendered(capture)


def test_the_baseline_records_shapes_rather_than_values() -> None:
    """A baseline that recorded a temporary directory or a wall clock
    would change every run, and a file that changes every run is a file
    nobody reads. Argument types, not arguments; payload keys, not
    payload values."""
    recorded = json.loads(COMMITTED.read_text(encoding="utf-8"))

    for records in recorded.values():
        for one in records:
            assert set(one) == {
                "channel",
                "level",
                "template",
                "argument_types",
                "fields",
                "event",
            }


# The types a JSON record is made of. Matched exactly rather than by
# subclass, which is the whole point: a `StrEnum` member IS a `str`, so
# `isinstance` would call an unconverted one lawful and `json.dumps`
# would serialize it without a word.
BUILTINS = (str, int, float, bool, type(None))


def plain(held: Any) -> bool:
    """Whether one payload value is a builtin, containers included."""
    if type(held) in BUILTINS:
        return True
    if type(held) is list:
        return all(plain(one) for one in held)
    if type(held) is dict:
        return all(plain(key) and plain(one) for key, one in held.items())
    return False


def test_every_driven_record_carries_builtins(
    produced: dict[str, list[logging.LogRecord]],
) -> None:
    """No wrapper and no enumeration member reaches a record.

    The committed baseline cannot say this: it records the argument
    TYPES and the payload's KEYS, so a member left unconverted in a
    carried, never-rendered field moves nothing in it, and a tap reading
    the payload would get the subclass. Asserted over the real catalog
    rather than a scratch one, since what is being claimed is that every
    declared path converts.
    """
    unconverted = [
        f"{key}: {name} is a {type(held).__name__}"
        for key, records in produced.items()
        for record in records
        for name, held in payload(record).items()
        if not plain(held)
    ]

    assert unconverted == []


def test_the_store_says_nothing_else(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The count the drivers above are complete against, and the one
    claim the retired pin suite made that neither the golden inventory
    nor the baseline carries.

    An ordinary session, start to close, emits no store event at all
    beyond the opening line, which is what makes the four failure and
    retention paths the whole of the rest. Kept here because it is
    behavior rather than shape: a store that started saying something on
    every turn would pass both files above and change what a deployment
    keeps.
    """
    store = ConversationStore(tmp_path, retention_days=0)

    with caplog.at_level(logging.DEBUG):
        store.start()
        store.open_session("alpha", 100.0, a_manifest(NOW))
        store.record_turn("alpha", a_turn())
        store.close_session("alpha", duration_s=5.0, reason="client")
        store.stop()

    said = [one for one in caplog.records if one.name in SCOPE]
    assert [getattr(one, "event", None) for one in said] == ["conversations_enabled"]
