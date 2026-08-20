"""The record baseline, and the two obligations that make it a proof.

The harness in `tests/tools/event_baseline.py` drives every emit path in
scope and captures what it produced. On its own that would prove only
what it happened to execute, which is exactly the hole a runtime harness
falls into. So the path list comes from outside it, twice over:

- **From a static reading of the source.** The harness's own walk reads
  the scoped modules and answers every emit path in them, in both the
  untyped and the typed shape, and the drivers' identities must EQUAL
  that inventory in both directions. A sixth path with no driver and a
  driver naming no path fail the same way. Each inventoried path must
  also produce a record of the event it emits, so a driver that runs and
  emits something else is a failure rather than a pass.
- **From the catalog.** Every variant declared on a scoped channel must
  be produced by some driver's run: every legal variant is
  constructible, and therefore directly drivable, which is what the plan
  means by claiming exhaustiveness over variants rather than over call
  sites. A variant is identified by its event, channel, level and
  template AND by its payload's keys, because several events say one
  sentence about two shapes: `llm_round` reports a provider the registry
  built out of a configured entry and one it never built with the same
  words, and the four dimensions alone would let either stand in for
  both.

A walk is only worth what it finds, so it is proved here on planted
sources rather than trusted: both shapes, both numbered in one sequence
within their enclosing scope, and a tap's own `emit` left alone.

All of it holds before a conversion and after it, which is the point:
the committed capture is a file that does not move when the sites do.

The first version of this borrowed the conformance suite's walk, which
reads only the untyped shape. After the store converted, that walk found
nothing in scope while the harness claimed five paths, and PR #217's
review named the obligation for what it had become: vacuous.
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
    sites,
    sites_in,
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
def capture() -> dict[str, list[dict[str, Any]]]:
    """Driven once for the whole file: every driver opens a database and
    some park a writer thread, so running them per test would pay for
    the same evidence four times."""
    return captured()


def test_every_emit_path_in_scope_is_driven_and_only_those() -> None:
    """The obligation the harness cannot give itself, in both
    directions: a driver claiming a path that does not exist is as wrong
    as a path with no driver, and containment either way would let one
    of the two through."""
    walked = {site.identity for site in sites()}
    claimed = {driver.identity for driver in DRIVERS}

    assert sorted(walked - claimed) == [], "emit paths with no driver"
    assert sorted(claimed - walked) == [], "drivers naming no emit path"
    assert len(walked) == len(sites()) == len(DRIVERS)


def test_every_driven_path_produces_the_event_it_emits(
    capture: dict[str, list[dict[str, Any]]],
) -> None:
    """Not merely that a driver produced something. The walk reads which
    event each path emits, from the `event=` keyword or from the variant
    the thunk constructs, and every record kept has to be that one."""
    expected = {site.identity: site.event for site in sites()}

    for driver in DRIVERS:
        produced = {one["event"] for one in capture[driver.key]}
        assert produced == {expected[driver.identity]}, driver.key


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

    The recovery event is exempt, and by its declaration's own
    `internal` flag rather than by its name: no ordinary emit site
    produces it, which is what the flag says, and the emitter's two
    refusal branches are driven by the guard's own suites instead."""
    driven: dict[str, list[dict[str, Any]]] = {}
    for records in capture.values():
        for record in records:
            driven.setdefault(record["event"], []).append(record)

    unproduced = [
        f"{name}: {variant.__name__}"
        for name, declaration in catalog().items()
        if not declaration.internal
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


# --- the walk, proved on planted sources ------------------------------
#
# Written rather than trusted, for the reason the conformance suite
# gives about its own: an inventory that stopped finding things would
# turn every obligation above into a pass over an empty set.

PLANTED = "vinga_server.planted"

BOTH_SHAPES = """
from vinga_server.events import ServerEvents
from vinga_server.events.catalog import ConversationsPruned

events = ServerEvents(__name__)


class Store:
    def run(self):
        events.warning("said %s", one, event="conversations_enabled", path=one)
        events.emit(lambda: ConversationsPruned(sessions=one, days=two))
"""

A_TAPS_OWN_EMIT = """
from vinga_server.events import ServerEvents
from vinga_server.events.catalog import ConversationsPruned

events = ServerEvents(__name__)


class Sink:
    def emit(self, emission):
        self.kept.append(emission)


class Store:
    def run(self, tap):
        tap.emit(emission)
        events.emit(lambda: ConversationsPruned(sessions=one, days=two))
"""


def test_the_walk_reads_both_shapes_and_numbers_them_in_one_sequence() -> None:
    """The moment the identity has to stay stable is the one where a
    module is half converted, so the two shapes share one ordinal
    counter rather than each starting at 1."""
    found = sites_in(PLANTED, BOTH_SHAPES)

    assert [(one.function, one.ordinal, one.event) for one in found] == [
        ("Store.run", 1, "conversations_enabled"),
        ("Store.run", 2, "conversations_pruned"),
    ]


def test_the_walk_leaves_a_taps_own_emit_alone() -> None:
    """`emit` is a tap's method as well as an emitter's, and a scoped
    module may hold both. The receiver is what tells them apart: a tap
    is not the module's emitter, whatever it is called."""
    found = sites_in(PLANTED, A_TAPS_OWN_EMIT)

    assert [(one.function, one.ordinal) for one in found] == [("Store.run", 1)]


def test_the_walk_refuses_a_thunk_it_cannot_read() -> None:
    """A path the walk cannot read is a path the inventory would
    silently lose, so it is an error rather than a skip."""
    with pytest.raises(AssertionError, match="construct one variant"):
        sites_in(PLANTED, "events = ServerEvents(__name__)\nevents.emit(lambda: 1)\n")
