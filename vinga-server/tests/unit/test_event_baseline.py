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
below holds each PATH to the shape it is supposed to produce, which is
the pair of things no declaration can state: which of an event's
same-shaped variants this path emits, and which of that variant's
optional fields it fills.

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
    Run,
    a_manifest,
    a_turn,
    captured,
    driven,
    payload,
    shape,
)
from vinga_server.config.models import DatabaseConfig
from vinga_server.conversations.store import ConversationStore
from vinga_server.events.catalog import Variant, catalog, payload_shape


@pytest.fixture(scope="module")
def run() -> Run:
    """Driven once for the whole file: every driver opens a database and
    some park a writer thread, so running them per test would pay for
    the same evidence several times over."""
    return driven()


@pytest.fixture(scope="module")
def produced(run: Run) -> dict[str, list[logging.LogRecord]]:
    """The same run, as each driver's own records."""
    return run.kept


@pytest.fixture(scope="module")
def capture(
    produced: dict[str, list[logging.LogRecord]],
) -> dict[str, list[dict[str, Any]]]:
    """The same run, in the dimensions a consumer sees."""
    return captured(produced)


def test_every_driver_names_a_path_of_its_own() -> None:
    """One driver per emit path, so a capture keyed by identity is a
    capture of eighty-four paths rather than of however many survived a
    collision.

    Eighty since #283, when `BindingsSnapshotOnly` retired with the
    file-existence probe that was the only thing able to emit it,
    eighty-one since #190 gave a reply a second kind of boundary to
    move at, eighty-two since a consented recap became a checkpoint
    worth announcing, eighty-three since a provider built inside a
    container says so when its endpoint is this machine (#340), and
    eighty-four since the memory store's write path gained an event of
    its own (#314)."""
    claimed = [driver.identity for driver in DRIVERS]

    assert len(set(claimed)) == len(claimed) == 84


def test_every_driven_path_produces_the_event_it_emits(
    capture: dict[str, list[dict[str, Any]]],
) -> None:
    """Not merely that a driver produced something: every record kept
    has to be the event that driver names, so a path that stopped firing
    fails rather than quietly recording a neighbour's."""
    for driver in DRIVERS:
        produced = {one["event"] for one in capture[driver.key]}
        assert produced == {driver.event}, driver.key


def variants_of(event: str) -> tuple[type[Variant], ...]:
    """Every variant declared for one record's event.

    The empty answer is defensive and, today, unreachable: `driven()`
    keeps only records whose `event` equals a driver's, and every
    driver names a declared one. It is a `get` rather than a subscript
    so that a record outside the surface would be a readable row in the
    failures below rather than a `KeyError` raised inside a
    comprehension.
    """
    declaration = catalog().get(event)
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


def test_every_driven_record_conforms_to_a_declared_variant(run: Run) -> None:
    """The direction the every-variant check below does not cover: it
    asks whether each DECLARATION was produced, and this asks whether
    each RECORD was declared.

    Over every typed record the runs produced, which is three times the
    eighty-six the drivers keep: a session driver crosses a dozen
    neighbouring paths on its way to its own decision, and those records
    are in no table and read by nothing else. That wider population is
    what this holds and `CARRIED` cannot, since a table of per-driver
    rows only ever sees the driver's own event.

    Channel, level and template are derived from the variant at emit, so
    a single declaration edited on its own moves the record with it and
    passes here. What does not pass is a record whose shape belongs to
    no declaration at all: a template or a level moved between two
    variants of one event, or a payload that gained a key nothing
    declares or lost one every variant requires. Template equality is
    also what subsumes arity, which is why the retired capture's
    argument types are not missed.

    An untyped record is not in scope here and cannot be: it carries no
    `event` attribute, so the drivers' filter drops it before this sees
    anything. `UNTYPED` below is where that half lives.
    """
    unmatched = [
        described(key, record)
        for key, records in run.said.items()
        for one in records
        for record in [shape(one)]
        if record["event"] is not None
        and not any(
            matches(variant, record) for variant in variants_of(record["event"])
        )
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


# Every record the drivers put on a scoped channel that is NOT one of
# the eighty-four typed paths, by channel and by sentence.
#
# These are the untyped diagnostics that survived #210: per-utterance
# and per-listen lines a session writes, the filler cache's own line,
# the ASR echo retry, the MCP guidance line, and the reply-failed line
# beside the typed `provider_failed`. They are not events, they carry no
# payload, and no tap is offered them.
#
# The set is closed here because nothing else closes it any more. The
# static walk that read the scoped modules for emit sites retired with
# the last conversion (#210) and this milestone deleted its last
# remains, so an untyped emit site put back on a scoped channel would
# otherwise reach a deployment's logs with nothing red anywhere: the
# drivers' filter drops it, and a record with no `event` is invisible to
# every check that reads the capture. Asserted in both directions, so a
# site converted to a declaration is a row removed here rather than a
# line that outlives what it described.
UNTYPED: frozenset[tuple[str, str]] = frozenset(
    {
        ("vinga_server.session", "session %s: listening (%s mode)"),
        ("vinga_server.session", "session %s: utterance of %.1f s"),
        ("vinga_server.session", "session %s: reply failed: %s"),
        ("vinga_server.filler", "agent %s: cached %d filler clip(s) in its own voice"),
        (
            "vinga_server.providers.openai_asr",
            "openai asr: the transcript came back as the configured prompt, "
            "retrying %.2f s of audio without it",
        ),
        (
            "vinga_server.tools.mcp",
            "mcp server %s shipped guidance: %d characters of instructions, and "
            "prompts at inject_prompts position(s) %s",
        ),
    }
)


def test_no_unlisted_record_rides_a_scoped_channel_untyped(run: Run) -> None:
    """The half the conformance check structurally cannot reach.

    Channels and sentences, which are source text rather than anything a
    run produced, so this is as values-free as the rest of the file.
    """
    found = {
        (one.name, one.msg)
        for records in run.said.values()
        for one in records
        if getattr(one, "event", None) is None
    }

    assert found == UNTYPED


# What each driver's path actually produces: one row per record it
# keeps, in the order it produced them, naming the VARIANT the record is
# an emission of and the payload keys it carries.
#
# Both halves are things the declarations cannot say on their own, and
# both are things the committed capture uniquely held.
#
# The variant name is the per-path pin. `matches()` above tries every
# variant of a record's event, so it says a record is one of the shapes
# that event may take and never which one this path is supposed to
# produce. Four events declare siblings whose payload keys are identical
# and whose sentences are not: `barge_in_suppressed` has three,
# `session_rejected` three, `ota_check` three, `capture_declined` two. A
# gate that inverted two of its branches would emit each of two
# situations under the other's sentence and the other's `reason`, and
# every check that reads the record alone would stay green.
#
# The keys are the per-path exactness `matches()` gives up. It asserts a
# RANGE, `required <= keys <= declared`, because an event's optional
# fields are optional per emission: `llm_round` names the configured
# entry behind a provider the registry built and says nothing about one
# it never built, from the same variant. So a path that quietly stopped
# filling its optional fields, a regressed entry quartet or a piece of
# dead usage plumbing, passes every range check while `events.md` does
# not move.
#
# It is a declaration rather than a recording: updating it is part of
# changing what a path produces, the same way the reference is
# regenerated when a variant's fields move. A driver added without a row
# here fails the check below rather than being silently uncovered.
CARRIED: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "vinga_server.conversations.store:ConversationStore.start #1": (
        ("ConversationsEnabled", ("event",)),
    ),
    "vinga_server.conversations.store:ConversationStore.record_event #1": (
        ("ConversationsDropped", ("event", "session")),
    ),
    "vinga_server.conversations.store:ConversationStore._failed #1": (
        ("WriteFailed", ("event", "failure")),
    ),
    "vinga_server.conversations.store:ConversationStore._prune #1": (
        ("PruneFailed", ("event", "failure")),
    ),
    "vinga_server.conversations.store:ConversationStore._prune #2": (
        ("ConversationsPruned", ("conversations", "event", "sessions")),
    ),
    "vinga_server.device.session:DeviceSession._idle_expired #1": (
        ("SessionIdle", ("device", "duration_s", "event", "idle_s", "session")),
    ),
    "vinga_server.device.session:DeviceSession.run #1": (
        ("RejectedBadDeviceId", ("device", "event", "reason", "session")),
    ),
    "vinga_server.device.session:DeviceSession.run #2": (
        ("RejectedAgentNotLoaded", ("device", "event", "reason", "session")),
    ),
    "vinga_server.device.session:DeviceSession.run #3": (
        ("RejectedNoAgent", ("device", "event", "reason", "session")),
    ),
    "vinga_server.device.session:DeviceSession.run #4": (
        (
            "SessionOpen",
            (
                "agent",
                "agents",
                "client",
                "conversation",
                "device",
                "event",
                "protocol",
                "revision",
                "session",
            ),
        ),
    ),
    "vinga_server.device.session:DeviceSession.run #5": (
        ("SessionLimit", ("device", "duration_s", "event", "session")),
    ),
    "vinga_server.device.session:DeviceSession.run #6": (
        ("SessionClosed", ("device", "duration_s", "event", "reason", "session")),
    ),
    "vinga_server.device.session:DeviceSession.send_audio #1": (
        ("SpeakingStarted", ("agent", "conversation", "device", "event", "session")),
    ),
    "vinga_server.runtime.pipeline:PipelineRuntime._watchdog_stream #1": (
        (
            "LlmRetry",
            (
                "agent",
                "conversation",
                "device",
                "duration_ms",
                "event",
                "model",
                "provider",
                "round",
                "session",
                "stage",
                "type",
            ),
        ),
        (
            "LlmRetry",
            (
                "agent",
                "conversation",
                "device",
                "duration_ms",
                "event",
                "round",
                "session",
                "stage",
            ),
        ),
    ),
    "vinga_server.runtime.pipeline:PipelineRuntime._llm_round_done #1": (
        (
            "LlmRound",
            (
                "agent",
                "conversation",
                "device",
                "duration_ms",
                "event",
                "first_token_ms",
                "input_tokens",
                "model",
                "output_tokens",
                "provider",
                "round",
                "session",
                "stage",
                "turns",
                "type",
            ),
        ),
        (
            "LlmRound",
            (
                "agent",
                "conversation",
                "device",
                "duration_ms",
                "event",
                "first_token_ms",
                "round",
                "session",
                "stage",
                "turns",
            ),
        ),
    ),
    "vinga_server.runtime.pipeline:PipelineRuntime._provider_failed #1": (
        (
            "ProviderFailed",
            (
                "agent",
                "conversation",
                "device",
                "duration_ms",
                "error",
                "event",
                "host",
                "model",
                "provider",
                "session",
                "stage",
                "type",
            ),
        ),
        (
            "ProviderFailed",
            (
                "agent",
                "conversation",
                "device",
                "duration_ms",
                "error",
                "event",
                "session",
                "stage",
            ),
        ),
    ),
    "vinga_server.runtime.pipeline:PipelineRuntime._prompt_assembled #1": (
        (
            "PromptAssembled",
            (
                "agent",
                "characters",
                "conversation",
                "device",
                "event",
                "session",
                "sources",
            ),
        ),
    ),
    "vinga_server.runtime.pipeline:PipelineRuntime._reply #1": (
        ("Heard", ("agent", "conversation", "device", "duration_s", "event", "session")),
    ),
    "vinga_server.runtime.pipeline:PipelineRuntime._reply #2": (
        ("Replied", ("agent", "conversation", "device", "event", "sentences", "session")),
    ),
    "vinga_server.runtime.pipeline:PipelineRuntime._speak_reply #1": (
        ("AgentSaid", ("agent", "conversation", "device", "event", "sentences", "session")),
    ),
    "vinga_server.runtime.pipeline:PipelineRuntime._move_to #1": (
        (
            "Handover",
            (
                "device",
                "event",
                "from_agent",
                "from_conversation",
                "session",
                "to_agent",
                "to_conversation",
            ),
        ),
    ),
    "vinga_server.runtime.pipeline:PipelineRuntime._move_to #2": (
        (
            "ConversationResumed",
            (
                "conversation",
                "device",
                "event",
                "over_budget",
                "session",
                "skipped",
                "turns",
            ),
        ),
    ),
    "vinga_server.runtime.pipeline:PipelineRuntime._store_recap #1": (
        ("MilestoneRecorded", ("conversation", "device", "event", "session")),
    ),
    "vinga_server.runtime.pipeline:PipelineRuntime._run_one #1": (
        (
            "BuiltinToolCall",
            (
                "agent",
                "conversation",
                "device",
                "duration_ms",
                "event",
                "is_error",
                "session",
                "source",
                "tool",
            ),
        ),
        (
            "UnnamedToolCall",
            (
                "agent",
                "conversation",
                "device",
                "duration_ms",
                "event",
                "is_error",
                "session",
                "source",
            ),
        ),
        (
            "McpToolCall",
            (
                "agent",
                "conversation",
                "device",
                "duration_ms",
                "entry",
                "event",
                "is_error",
                "session",
                "source",
            ),
        ),
    ),
    "vinga_server.runtime.turntaking:TurnTaking.finish_utterance #1": (
        ("BargeIn", ("device", "event", "session", "speech_ms")),
    ),
    "vinga_server.runtime.turntaking:TurnTaking._gate_barge_in #1": (
        ("BargeInUnderFloor", ("device", "event", "reason", "session", "speech_ms")),
    ),
    "vinga_server.runtime.turntaking:TurnTaking._gate_barge_in #2": (
        ("BargeInMerged", ("device", "event", "session", "speech_ms")),
    ),
    "vinga_server.runtime.turntaking:TurnTaking._gate_barge_in #3": (
        ("BargeInInRefractory", ("device", "event", "reason", "session", "speech_ms")),
    ),
    "vinga_server.runtime.turntaking:TurnTaking._gate_barge_in #4": (
        ("BargeInWithoutTranscript", ("device", "event", "reason", "session", "speech_ms")),
    ),
    "vinga_server.runtime.turntaking:TurnTaking._gate_barge_in #5": (
        ("BargeIn", ("device", "event", "session", "speaking_ms", "speech_ms")),
    ),
    "vinga_server.runtime.filler_runner:FillerRunner._fire #1": (
        (
            "FillerSkippedForSpeech",
            (
                "agent",
                "conversation",
                "device",
                "event",
                "reason",
                "session",
                "speech_ms",
            ),
        ),
    ),
    "vinga_server.runtime.filler_runner:FillerRunner._fire #2": (
        (
            "FillerSkippedForBargeIn",
            (
                "agent",
                "conversation",
                "device",
                "event",
                "reason",
                "session",
            ),
        ),
    ),
    "vinga_server.runtime.filler_runner:FillerRunner._fire #3": (
        (
            "FillerPlayed",
            (
                "agent",
                "conversation",
                "delay_ms",
                "device",
                "event",
                "phrase_index",
                "session",
            ),
        ),
    ),
    "vinga_server.app:_build_composition #1": (
        ("CaptureEnabled", ("event", "path")),
    ),
    "vinga_server.app:_build_composition #2": (
        ("CaptureDisabled", ("event", "path")),
    ),
    "vinga_server.capture:SessionCapture._disable #1": (
        ("CaptureFailed", ("event", "failure", "reason", "session")),
    ),
    "vinga_server.capture:SessionCapture._finish_at_limit #1": (
        ("CaptureLimit", ("event", "session")),
    ),
    "vinga_server.capture:CaptureStore.prune #1": (
        ("CapturePruned", ("event", "sessions")),
    ),
    "vinga_server.capture:CaptureStore.prune #2": (
        ("CaptureOverBudget", ("event", "total_mb")),
    ),
    "vinga_server.capture:CaptureStore.open #1": (
        ("CaptureDirectoryUnusable", ("event", "failure", "reason", "session")),
    ),
    "vinga_server.capture:CaptureStore.open #2": (
        ("CaptureBelowFloor", ("event", "free_mb", "reason", "session")),
    ),
    "vinga_server.capture:CaptureStore.open #3": (
        ("CaptureFilesUnopenable", ("event", "failure", "reason", "session")),
    ),
    "vinga_server.capture:CaptureStore.open #4": (
        ("CaptureStarted", ("event", "path", "session")),
    ),
    "vinga_server.config.api:_SanitizedErrors.__call__ #1": (
        ("ApiError", ("event",)),
    ),
    "vinga_server.config.api:_refusal.handler #1": (
        ("ApiStorageError", ("event",)),
    ),
    "vinga_server.device.bindings:DeviceBindings._warn #1": (
        ("BindingsUnreadable", ("device", "event", "failure")),
    ),
    "vinga_server.filler:build_agent_fillers #1": (
        ("FillerDisabled", ("agent", "error", "event")),
    ),
    "vinga_server.onboarding.keys:_log_mismatch #1": (
        ("OnboardingKeyMismatch", ("attempted_length", "event")),
    ),
    "vinga_server.onboarding.keys:_log_mismatch #2": (
        ("OnboardingKeyUnshaped", ("attempted_length", "event")),
    ),
    "vinga_server.onboarding.origin:log_banner #1": (
        ("OnboardingOff", ("event", "onboarding", "origin", "origin_source")),
    ),
    "vinga_server.onboarding.origin:log_banner #2": (
        ("OnboardingOn", ("event", "keyed", "onboarding", "origin", "origin_source")),
    ),
    "vinga_server.ota.poll:activate #1": (
        ("ActivationComplete", ("agents", "device", "event")),
    ),
    "vinga_server.ota.poll:activate #2": (
        ("ActivationPending", ("code", "device", "event", "unloaded")),
    ),
    "vinga_server.ota.poll:_version_two #1": (
        ("ActivationRefusedUnreadableBody", ("code", "device", "event", "reason")),
    ),
    "vinga_server.ota.poll:_version_two #2": (
        ("ActivationRefusedUnknownAlgorithm", ("code", "device", "event", "reason")),
    ),
    "vinga_server.ota.poll:_version_two #3": (
        ("ActivationRefusedChallengeMismatch", ("code", "device", "event", "reason")),
    ),
    "vinga_server.ota.reply:check_version #1": (
        ("OtaCheckActivating", ("agents", "board", "client", "code", "device", "event", "firmware",
                                "unloaded")),
    ),
    "vinga_server.ota.reply:check_version #2": (
        ("OtaCheckAgentNotLoaded", ("agents", "board", "client", "device", "event", "firmware",
                                    "unloaded")),
    ),
    "vinga_server.ota.reply:check_version #3": (
        ("OtaCheckNoAgent", ("agents", "board", "client", "device", "event", "firmware",
                             "unloaded")),
    ),
    "vinga_server.ota.reply:check_version #4": (
        ("OtaCheckResolved", ("agents", "board", "client", "device", "event", "firmware",
                              "unloaded")),
    ),
    "vinga_server.ota.reply:_activation #1": (
        ("ActivationNotOfferedUnreadable", ("device", "event", "reason")),
    ),
    "vinga_server.ota.reply:_activation #2": (
        ("ActivationNotOfferedRefused", ("device", "event", "reason")),
    ),
    "vinga_server.ota.reply:_bad_request #1": (
        ("OtaRequestRejected", ("event",)),
    ),
    "vinga_server.providers.openai_asr:OpenAiAsr._retry_without_prompt #1": (
        ("EchoSkipped", ("duration_s", "event", "host", "outcome")),
    ),
    "vinga_server.providers.openai_asr:OpenAiAsr._retry_without_prompt #2": (
        ("EchoRetryTimedOut", ("duration_s", "event", "host", "outcome", "retry_ms")),
    ),
    "vinga_server.providers.openai_asr:OpenAiAsr._retry_without_prompt #3": (
        ("EchoConfirmed", ("duration_s", "event", "host", "outcome", "retry_ms")),
    ),
    "vinga_server.providers.openai_asr:OpenAiAsr._retry_without_prompt #4": (
        ("EchoConfirmedEmpty", ("duration_s", "event", "host", "outcome", "retry_ms")),
    ),
    "vinga_server.providers.openai_asr:OpenAiAsr._retry_without_prompt #5": (
        ("EchoRecovered", ("duration_s", "event", "host", "outcome", "retry_ms")),
    ),
    "vinga_server.providers.world:_loopback_inside_a_container #1": (
        ("ProviderReachesLoopback", ("event", "host", "provider", "stage", "type")),
    ),
    "vinga_server.registry:SessionRegistry.drain #1": (
        ("DrainStarted", ("event", "sessions", "timeout_s")),
    ),
    "vinga_server.registry:SessionRegistry.drain #2": (
        ("DrainIncomplete", ("cut_mid_reply", "event", "sessions", "timeout_s", "unfinished")),
    ),
    "vinga_server.registry:SessionRegistry.drain #3": (
        ("DrainFinished", ("event", "sessions")),
    ),
    "vinga_server.tools.mcp.manager:McpServerManager._run #1": (
        ("McpConnected", ("duration_ms", "entry", "event", "tools", "transport")),
    ),
    "vinga_server.tools.mcp.manager:McpServerManager._run #2": (
        ("McpConnectFailed", ("duration_ms", "entry", "event", "reason")),
    ),
    "vinga_server.tools.mcp.manager:McpServerManager._run #3": (
        ("McpStopped", ("entry", "event", "reason")),
    ),
    "vinga_server.tools.mcp.manager:McpServerManager._mark_down #1": (
        ("McpCallDropped", ("entry", "error", "event", "position")),
    ),
    "vinga_server.tools.mcp.manager:McpServerManager._mark_down #2": (
        ("McpDropped", ("entry", "event", "reason")),
    ),
    "vinga_server.tools.mcp.registry:McpServers._reachable #1": (
        ("McpToolShadowed", ("entry", "event", "owner", "position")),
    ),
    "vinga_server.tools.mcp.reload:_refused #1": (
        ("McpReloadRefused", ("event", "outcome", "reason")),
    ),
    "vinga_server.tools.mcp.reload:_apply #1": (
        ("McpReloadApplied", ("duration_ms", "event", "outcome", "restarted", "started", "stopped",
                              "unchanged")),
    ),
    "vinga_server.memory.store:MemoryStore.read #1": (
        ("MemoryUnreadable", ("agent", "error", "event", "scope")),
    ),
    "vinga_server.memory.store:MemoryStore._store #1": (
        ("MemoryUnwritable", ("agent", "error", "event", "scope")),
    ),
    "vinga_server.ws:conversation #1": (
        ("AuthRejected", ("device", "event", "reason")),
    ),
    "vinga_server.ws:conversation #2": (
        ("RejectedAtCapacity", ("device", "event", "reason", "session")),
    ),
}


def matched(record: dict[str, Any]) -> str:
    """Which variant one record is an emission of.

    Exactly one, for every record the drivers keep: the four dimensions
    and the key range together separate the siblings of every declared
    event. The other answers are named rather than raised on, so a
    record that stopped being identifiable is a readable row in the
    failure above and here.
    """
    found = [
        variant.__name__
        for variant in variants_of(record["event"])
        if matches(variant, record)
    ]
    if len(found) == 1:
        return found[0]
    return " or ".join(found) if found else "no declared variant"


def test_every_driver_produces_the_shape_its_path_declares(
    capture: dict[str, list[dict[str, Any]]],
) -> None:
    """The per-path pin, which is what `matches()` gives up by trying
    every variant of an event.

    Variant names and field names, which is all a declaration and a
    payload key set are, so a red lane is as values-free as the rest of
    the file.
    """
    assert sorted(CARRIED) == sorted(driver.key for driver in DRIVERS)

    drifted = [
        f"{key}: produces {produces}, the table says {CARRIED[key]}"
        for key, records in capture.items()
        for produces in [tuple((matched(one), tuple(one["fields"])) for one in records)]
        if produces != CARRIED[key]
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
    store = ConversationStore(DatabaseConfig(), retention_days=0)

    with caplog.at_level(logging.DEBUG):
        store.start()
        store.open_session("alpha", 100.0, a_manifest(NOW))
        store.record_turn("alpha", a_turn())
        store.close_session("alpha", duration_s=5.0, reason="client")
        store.stop()

    said = [one for one in caplog.records if one.name in SCOPE]
    assert [getattr(one, "event", None) for one in said] == ["conversations_enabled"]
