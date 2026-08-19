"""The record baseline, and the two obligations that make it a proof.

The harness in `tests/tools/event_baseline.py` drives every emit path in
scope and captures what it produced. On its own that would prove only
what it happened to execute, which is exactly the hole a runtime harness
falls into. So the path list comes from outside it, twice over:

- **From the static walk, while it exists.** Every emit site the
  conformance suite finds in a scoped module must be claimed by a
  driver. That obligation is what makes the capture complete before a
  conversion, and it retires with the last conversion, since a converted
  site is invisible to a walk that looks for `event=` keywords.
- **From the catalog, which outlives it.** Every variant declared on a
  scoped channel must be produced by some driver's run. That one
  survives the conversion and is what the plan means by claiming
  exhaustiveness over the catalog's legal variants rather than over
  arbitrary call sites: every legal variant is constructible, and
  therefore directly drivable.

Both hold before a conversion and after it, which is the point: the
committed capture is a file that does not move when the sites do.
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
    rendered,
)
from tests.unit.test_event_schema_conformance import emit_sites
from vinga_server.conversations.store import ConversationStore
from vinga_server.events.catalog import catalog

REGENERATE = (
    "the captured records are no longer the committed baseline. If a "
    "conversion was supposed to preserve them, this is the failure it was "
    "written to catch; if the surface changed on purpose, regenerate with: "
    "uv run python -m tests.tools.event_baseline"
)


@pytest.fixture(scope="module")
def capture() -> dict[str, list[dict[str, Any]]]:
    """Driven once for the whole file: every driver opens a database and
    some park a writer thread, so running them per test would pay for
    the same evidence four times."""
    return captured()


def test_every_statically_known_emit_site_in_scope_is_driven() -> None:
    """The obligation the harness cannot give itself. Equality rather
    than containment for as long as the walk still sees these sites: a
    driver claiming a path that does not exist is as wrong as a path
    with no driver."""
    walked = {site.identity for site in emit_sites() if site.module in SCOPE}
    claimed = {driver.identity for driver in DRIVERS}

    assert walked <= claimed, f"emit sites with no driver: {sorted(walked - claimed)}"


def test_every_catalog_variant_on_a_scoped_channel_is_produced(
    capture: dict[str, list[dict[str, Any]]],
) -> None:
    """The obligation that outlives the walk. A variant identifies
    itself by its event, channel, level and template, which is what a
    captured record carries."""
    declared = {
        (name, variant.CHANNEL, variant.LEVEL, variant.TEMPLATE)
        for name, declaration in catalog().items()
        for variant in declaration.variants
        if variant.CHANNEL in SCOPE
    }
    produced = {
        (one["event"], one["channel"], one["level"], one["template"])
        for records in capture.values()
        for one in records
    }

    assert declared == produced


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
