"""Every emit path, held to what the catalog declares about it.

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
its payload's keys, because an event can say one sentence about
several shapes: `tool_call` reports a builtin, a server tool and a call
it may not name at all with the same words, and the four dimensions
alone would let any of the three stand in for the others.

Beside it, the smaller claim the drivers can give themselves: each one
produces the event it says it does, so a driver whose path stopped
firing fails rather than quietly recording a neighbour's records.

There is no committed capture any more (#241). A file of eighty-six
recorded shapes was a third pin on a catalog `docs/reference/events.md`
already pins, and every event change cost a regeneration of it. What it
uniquely held is here instead, live and needing nothing on disk: every
produced record conforms to a variant its event declares, and `CARRIED`
below is the one thing a declaration cannot say, which is which of a
variant's optional fields each PATH actually fills.

Nothing here reports a payload value. The drivers work from real
material, a planted API token among it, so a red lane says channel,
level, template, field names and type names and stops there; the
builtins check below is the model, and it names the offending TYPE.
"""

import logging
from pathlib import Path
from typing import Any

import pytest

from tests.tools.event_baseline import (
    DRIVERS,
    NOW,
    SCOPE,
    a_manifest,
    a_turn,
    captured,
    driven,
    payload,
)
from vinga_server.conversations.store import ConversationStore
from vinga_server.events.catalog import Variant, catalog, payload_shape


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
    """The same run, in the dimensions a consumer sees."""
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


def variants_of(event: str | None) -> tuple[type[Variant], ...]:
    """Every variant declared for one record's event, and none for a
    record naming an event no declaration owns: that is a record outside
    the surface rather than a lookup to raise on."""
    declaration = catalog().get(event or "")
    return () if declaration is None else declaration.variants


def matches(variant: type[Variant], record: dict[str, Any]) -> bool:
    """Whether one captured record is an emission of one variant.

    The four dimensions, and then the payload's keys: everything the
    variant always carries is there, and nothing it never declares is.
    That second half is what tells apart the variants of an event that
    say one sentence about several shapes, since a record naming the
    MCP entry a call reached cannot be the variant that declares no
    such field.
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


def described(key: str, record: dict[str, Any]) -> str:
    """One record as a failure may name it: where it came from and the
    dimensions it was matched on. Never a value."""
    return (
        f"{key}: {record['event']} on {record['channel']} at "
        f"{logging.getLevelName(record['level'])} carrying "
        f"({', '.join(record['fields'])}) for {record['template']!r}"
    )


def test_every_driven_record_conforms_to_a_declared_variant(
    capture: dict[str, list[dict[str, Any]]],
) -> None:
    """The direction the every-variant check below does not cover: it
    asks whether each DECLARATION was produced, and this asks whether
    each RECORD was declared.

    Channel, level and template are derived from the variant at emit, so
    a single declaration edited on its own moves the record with it and
    passes here. What does not pass is a record whose shape belongs to
    no declaration at all: a template or a level moved between two
    variants of one event, a payload that gained a key nothing declares
    or lost one every variant requires, or an untyped emit site put back
    on a scoped channel. Template equality is also what subsumes arity,
    which is why the retired capture's argument types are not missed.
    """
    unmatched = [
        described(key, one)
        for key, records in capture.items()
        for one in records
        if not any(matches(variant, one) for variant in variants_of(one["event"]))
    ]

    assert unmatched == []


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


# What each driver's path actually carries: one tuple of field names per
# record it keeps, in the order it produced them.
#
# This is the one thing the declarations cannot say and the one thing
# the committed capture uniquely held. `matches()` above asserts a
# RANGE, `required <= keys <= declared`, because an event's optional
# fields are optional per emission: `llm_round` names the configured
# entry behind a provider the registry built and says nothing about one
# it never built, from the same variant. So a path that quietly stopped
# filling its optional fields, a regressed entry quartet or a piece of
# dead usage plumbing, passes every range check while `events.md` does
# not move.
#
# It is a declaration rather than a recording: updating it is part of
# changing what a path carries, the same way the reference is
# regenerated when a variant's fields move. A driver added without a row
# here fails the check below rather than being silently uncovered.
CARRIED: dict[str, tuple[tuple[str, ...], ...]] = {
    "vinga_server.conversations.store:ConversationStore.start #1": (
        ("event", "path"),
    ),
    "vinga_server.conversations.store:ConversationStore.record_event #1": (
        ("event", "session"),
    ),
    "vinga_server.conversations.store:ConversationStore._failed #1": (
        ("event", "failure"),
    ),
    "vinga_server.conversations.store:ConversationStore._prune #1": (
        ("event", "failure"),
    ),
    "vinga_server.conversations.store:ConversationStore._prune #2": (
        ("event", "sessions"),
    ),
    "vinga_server.device.session:DeviceSession._watch_for_idle #1": (
        ("device", "duration_s", "event", "idle_s", "session"),
    ),
    "vinga_server.device.session:DeviceSession.run #1": (
        ("device", "event", "reason", "session"),
    ),
    "vinga_server.device.session:DeviceSession.run #2": (
        ("device", "event", "reason", "session"),
    ),
    "vinga_server.device.session:DeviceSession.run #3": (
        ("device", "event", "reason", "session"),
    ),
    "vinga_server.device.session:DeviceSession.run #4": (
        ("agent", "agents", "client", "device", "event", "protocol", "revision", "session"),
    ),
    "vinga_server.device.session:DeviceSession.run #5": (
        ("device", "duration_s", "event", "session"),
    ),
    "vinga_server.device.session:DeviceSession.run #6": (
        ("device", "duration_s", "event", "reason", "session"),
    ),
    "vinga_server.device.session:DeviceSession.send_audio #1": (
        ("agent", "device", "event", "session"),
    ),
    "vinga_server.runtime.pipeline:PipelineRuntime._watchdog_stream #1": (
        ("agent", "device", "duration_ms", "event", "model", "provider", "round", "session",
         "stage", "type"),
        ("agent", "device", "duration_ms", "event", "round", "session", "stage"),
    ),
    "vinga_server.runtime.pipeline:PipelineRuntime._llm_round_done #1": (
        ("agent", "device", "duration_ms", "event", "first_token_ms", "input_tokens", "model",
         "output_tokens", "provider", "round", "session", "stage", "turns", "type"),
        ("agent", "device", "duration_ms", "event", "first_token_ms", "round", "session", "stage",
         "turns"),
    ),
    "vinga_server.runtime.pipeline:PipelineRuntime._provider_failed #1": (
        ("agent", "device", "duration_ms", "error", "event", "host", "model", "provider", "session",
         "stage", "type"),
        ("agent", "device", "duration_ms", "error", "event", "session", "stage"),
    ),
    "vinga_server.runtime.pipeline:PipelineRuntime._prompt_assembled #1": (
        ("agent", "characters", "device", "event", "session", "sources"),
    ),
    "vinga_server.runtime.pipeline:PipelineRuntime._reply #1": (
        ("agent", "device", "duration_s", "event", "session"),
    ),
    "vinga_server.runtime.pipeline:PipelineRuntime._reply #2": (
        ("agent", "device", "event", "sentences", "session"),
    ),
    "vinga_server.runtime.pipeline:PipelineRuntime._speak_reply #1": (
        ("agent", "device", "event", "sentences", "session"),
    ),
    "vinga_server.runtime.pipeline:PipelineRuntime._speak_reply #2": (
        ("device", "event", "from_agent", "session", "to_agent"),
    ),
    "vinga_server.runtime.pipeline:PipelineRuntime._run_one #1": (
        ("agent", "device", "duration_ms", "event", "is_error", "session", "source", "tool"),
        ("agent", "device", "duration_ms", "event", "is_error", "session", "source"),
        ("agent", "device", "duration_ms", "entry", "event", "is_error", "session", "source"),
    ),
    "vinga_server.runtime.turntaking:TurnTaking.finish_utterance #1": (
        ("device", "event", "session", "speech_ms"),
    ),
    "vinga_server.runtime.turntaking:TurnTaking._gate_barge_in #1": (
        ("device", "event", "reason", "session", "speech_ms"),
    ),
    "vinga_server.runtime.turntaking:TurnTaking._gate_barge_in #2": (
        ("device", "event", "session", "speech_ms"),
    ),
    "vinga_server.runtime.turntaking:TurnTaking._gate_barge_in #3": (
        ("device", "event", "reason", "session", "speech_ms"),
    ),
    "vinga_server.runtime.turntaking:TurnTaking._gate_barge_in #4": (
        ("device", "event", "reason", "session", "speech_ms"),
    ),
    "vinga_server.runtime.turntaking:TurnTaking._gate_barge_in #5": (
        ("device", "event", "session", "speaking_ms", "speech_ms"),
    ),
    "vinga_server.runtime.filler_runner:FillerRunner._fire #1": (
        ("agent", "device", "event", "reason", "session", "speech_ms"),
    ),
    "vinga_server.runtime.filler_runner:FillerRunner._fire #2": (
        ("agent", "device", "event", "reason", "session"),
    ),
    "vinga_server.runtime.filler_runner:FillerRunner._fire #3": (
        ("agent", "delay_ms", "device", "event", "phrase_index", "session"),
    ),
    "vinga_server.app:_build_composition #1": (
        ("event", "path"),
    ),
    "vinga_server.app:_build_composition #2": (
        ("event", "path"),
    ),
    "vinga_server.capture:SessionCapture._disable #1": (
        ("event", "failure", "reason", "session"),
    ),
    "vinga_server.capture:SessionCapture._finish_at_limit #1": (
        ("event", "session"),
    ),
    "vinga_server.capture:CaptureStore.prune #1": (
        ("event", "sessions"),
    ),
    "vinga_server.capture:CaptureStore.prune #2": (
        ("event", "total_mb"),
    ),
    "vinga_server.capture:CaptureStore.open #1": (
        ("event", "failure", "reason", "session"),
    ),
    "vinga_server.capture:CaptureStore.open #2": (
        ("event", "free_mb", "reason", "session"),
    ),
    "vinga_server.capture:CaptureStore.open #3": (
        ("event", "failure", "reason", "session"),
    ),
    "vinga_server.capture:CaptureStore.open #4": (
        ("event", "path", "session"),
    ),
    "vinga_server.config.api:_SanitizedErrors.__call__ #1": (
        ("event",),
    ),
    "vinga_server.config.api:_refusal.handler #1": (
        ("event",),
    ),
    "vinga_server.device.bindings:DeviceBindings.open #1": (
        ("event", "path"),
    ),
    "vinga_server.device.bindings:DeviceBindings._warn #1": (
        ("device", "event", "failure"),
    ),
    "vinga_server.filler:build_agent_fillers #1": (
        ("agent", "error", "event"),
    ),
    "vinga_server.onboarding.keys:_log_mismatch #1": (
        ("attempted_length", "event"),
    ),
    "vinga_server.onboarding.keys:_log_mismatch #2": (
        ("attempted_length", "event"),
    ),
    "vinga_server.onboarding.origin:log_banner #1": (
        ("event", "onboarding", "origin", "origin_source"),
    ),
    "vinga_server.onboarding.origin:log_banner #2": (
        ("event", "keyed", "onboarding", "origin", "origin_source"),
    ),
    "vinga_server.ota.poll:activate #1": (
        ("agents", "device", "event"),
    ),
    "vinga_server.ota.poll:activate #2": (
        ("code", "device", "event", "unloaded"),
    ),
    "vinga_server.ota.poll:_version_two #1": (
        ("code", "device", "event", "reason"),
    ),
    "vinga_server.ota.poll:_version_two #2": (
        ("code", "device", "event", "reason"),
    ),
    "vinga_server.ota.poll:_version_two #3": (
        ("code", "device", "event", "reason"),
    ),
    "vinga_server.ota.reply:check_version #1": (
        ("agents", "board", "client", "code", "device", "event", "firmware", "unloaded"),
    ),
    "vinga_server.ota.reply:check_version #2": (
        ("agents", "board", "client", "device", "event", "firmware", "unloaded"),
    ),
    "vinga_server.ota.reply:check_version #3": (
        ("agents", "board", "client", "device", "event", "firmware", "unloaded"),
    ),
    "vinga_server.ota.reply:check_version #4": (
        ("agents", "board", "client", "device", "event", "firmware", "unloaded"),
    ),
    "vinga_server.ota.reply:_activation #1": (
        ("device", "event", "reason"),
    ),
    "vinga_server.ota.reply:_activation #2": (
        ("device", "event", "reason"),
    ),
    "vinga_server.ota.reply:_bad_request #1": (
        ("event",),
    ),
    "vinga_server.providers.openai_asr:OpenAiAsr._retry_without_prompt #1": (
        ("duration_s", "event", "host", "outcome"),
    ),
    "vinga_server.providers.openai_asr:OpenAiAsr._retry_without_prompt #2": (
        ("duration_s", "event", "host", "outcome", "retry_ms"),
    ),
    "vinga_server.providers.openai_asr:OpenAiAsr._retry_without_prompt #3": (
        ("duration_s", "event", "host", "outcome", "retry_ms"),
    ),
    "vinga_server.providers.openai_asr:OpenAiAsr._retry_without_prompt #4": (
        ("duration_s", "event", "host", "outcome", "retry_ms"),
    ),
    "vinga_server.providers.openai_asr:OpenAiAsr._retry_without_prompt #5": (
        ("duration_s", "event", "host", "outcome", "retry_ms"),
    ),
    "vinga_server.registry:SessionRegistry.drain #1": (
        ("event", "sessions", "timeout_s"),
    ),
    "vinga_server.registry:SessionRegistry.drain #2": (
        ("cut_mid_reply", "event", "sessions", "timeout_s", "unfinished"),
    ),
    "vinga_server.registry:SessionRegistry.drain #3": (
        ("event", "sessions"),
    ),
    "vinga_server.tools.mcp.manager:McpServerManager._run #1": (
        ("duration_ms", "entry", "event", "tools", "transport"),
    ),
    "vinga_server.tools.mcp.manager:McpServerManager._run #2": (
        ("duration_ms", "entry", "event", "reason"),
    ),
    "vinga_server.tools.mcp.manager:McpServerManager._run #3": (
        ("entry", "event", "reason"),
    ),
    "vinga_server.tools.mcp.manager:McpServerManager._mark_down #1": (
        ("entry", "error", "event", "position"),
    ),
    "vinga_server.tools.mcp.manager:McpServerManager._mark_down #2": (
        ("entry", "event", "reason"),
    ),
    "vinga_server.tools.mcp.registry:McpServers._reachable #1": (
        ("entry", "event", "owner", "position"),
    ),
    "vinga_server.tools.mcp.reload:_refused #1": (
        ("event", "outcome", "reason"),
    ),
    "vinga_server.tools.mcp.reload:_apply #1": (
        ("duration_ms", "event", "outcome", "restarted", "started", "stopped", "unchanged"),
    ),
    "vinga_server.tools.memory:MemoryStore.read #1": (
        ("agent", "error", "event"),
    ),
    "vinga_server.ws:conversation #1": (
        ("device", "event", "reason"),
    ),
    "vinga_server.ws:conversation #2": (
        ("device", "event", "reason", "session"),
    ),
}


def test_every_driver_carries_the_fields_its_path_declares(
    capture: dict[str, list[dict[str, Any]]],
) -> None:
    """The per-path exactness `matches()` gives up, asserted here.

    Field names only, which is all a payload key set is, so a red lane
    is as values-free as the rest of the file.
    """
    assert sorted(CARRIED) == sorted(driver.key for driver in DRIVERS)

    drifted = [
        f"{key}: carries {carried}, the table says {CARRIED[key]}"
        for key, records in capture.items()
        for carried in [tuple(tuple(one["fields"]) for one in records)]
        if carried != CARRIED[key]
    ]

    assert drifted == []


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

    Nothing else here can say this: the checks above read the payload's
    KEYS, so a member left unconverted in a carried, never-rendered
    field moves none of them, and a tap reading the payload would get
    the subclass. Asserted over the real catalog rather than a scratch
    one, since what is being claimed is that every declared path
    converts. The report names the field and the TYPE it holds, never
    the value.
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
    claim the retired pin suite made that no shape check carries.

    An ordinary session, start to close, emits no store event at all
    beyond the opening line, which is what makes the four failure and
    retention paths the whole of the rest. Kept here because it is
    behavior rather than shape: a store that started saying something on
    every turn would conform to its declaration, carry the fields the
    table above expects, and change what a deployment keeps.
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
