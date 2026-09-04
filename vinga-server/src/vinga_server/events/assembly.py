"""How an event about a provider or a tool call is put together.

The reply path used to hold this. A third of `runtime/pipeline.py`'s
executable code chose between event shapes, ordered field lists, and
knew which value type wrapped which argument, which is telemetry shape
logic living in the middle of the code that answers a person. What a
caller passes now is the thing that happened, in plain values, and what
it gets back is the variant that describes it: it names no variant
class, orders no field list, and learns nothing about which value type
wraps which argument.

The depth is not a vocabulary crossing, because there is none: the
signatures below take builtins and answer a `Variant`. It is one fact
that would otherwise be the caller's: the entry quartet is one type
whose `provider` and `type` are required, so the four values a
configured provider contributes are built in exactly one place and are
answered whole or not at all.

One thing this module deliberately does NOT own, so that no reader
takes its presence for a guarantee: which of a call's names may be
printed at all. `tool_fragment` renders whichever name it is handed and
refuses nothing, because deciding is reading the classifier's own
source constants, which stay beside the classifier (#240, the plan's
decision 4). The three `tool_call` variants make the decision
structural for themselves by declaring the fragment type each carries,
and the three `sentence_withheld` variants beside them do it the same
way, since a withheld sentence is about a tool and is named by the same
policy; the one sentence that is not an event has `pipeline.py`'s
`_tool_fragment` for its single named home. What lives here is the
rendering, and the grammar bounding each shape of it.

`tool_arguments_coerced` is the one event whose sentence may render any
of the three shapes, because it is one variant for all four namespaces:
what makes its answer the same answer is that it takes the two names
from the same decision `tool_call` takes them from, and carries them in
its payload as well as in its sentence.

Plain values in, one variant out, and nothing injectable. The module
imports the catalog and the value vocabulary and no subsystem, which is
what lets it be called from anywhere an event is emitted.
"""

from dataclasses import dataclass
from typing import cast

from vinga_server.events.catalog import (
    BuiltinSentenceWithheld,
    BuiltinToolCall,
    LlmRetry,
    LlmRound,
    McpSentenceWithheld,
    McpToolCall,
    ProviderFailed,
    ToolArgumentsCoerced,
    UnnamedSentenceWithheld,
    UnnamedToolCall,
    Variant,
)
from vinga_server.events.values import (
    ABSENT,
    Absent,
    ClassName,
    ConversationId,
    Count,
    Flag,
    Fragment,
    FromEntry,
    Identifier,
    Nothing,
    ProviderOutcome,
    QuotedProvider,
    QuotedToolName,
    ReachingHost,
    Real,
    ToolOutcome,
    ToolSource,
    UnnamedToolSource,
    Whole,
)

__all__ = [
    "builtin_sentence_withheld",
    "builtin_tool_called",
    "llm_retried",
    "llm_rounded",
    "mcp_sentence_withheld",
    "mcp_tool_called",
    "provider_failure",
    "tool_arguments_coerced",
    "tool_fragment",
    "unnamed_sentence_withheld",
    "unnamed_tool_called",
]


@dataclass(frozen=True)
class _Entry:
    """Which configuration entry a provider is, as the events that name
    one carry it: the entry an operator wrote in the YAML, its type, the
    host it reaches and the model it runs.

    Answered whole or not at all, which is what makes `provider` and
    `type` atomic: a provider the registry did not build (a test's, a
    fixture's) has no identity, and an event that cannot name the entry
    says less rather than guessing. Required here rather than checked
    anywhere, and not exported, so this is the only place the four
    values are built and a half quartet has nowhere to come from.

    `host` is absent for an engine that runs in this process and `model`
    for a type that has none to name. `model` is the GenAI conventions'
    `gen_ai.request.model` (#120), which is what makes a round
    attributable to the model that ran it rather than only to the entry
    that pointed at one: two entries of a type can name different
    models, and a turn's token totals blend the rounds of everything
    that answered it.
    """

    provider: Identifier
    type: Identifier
    host: Identifier | Absent
    model: Identifier | Absent


def _entry_of(provider: object) -> _Entry | None:
    """The configured entry behind one provider, or None where the
    registry never built it.

    Called inside a construction thunk, never beside one: every value it
    builds is validated at construction, and a value that refuses has to
    refuse where the emitter's guard is holding it.
    """
    identity = getattr(provider, "identity", None)
    if identity is None:
        return None
    return _Entry(
        provider=Identifier(identity.name),
        type=Identifier(identity.type),
        host=Identifier(identity.host) if identity.host is not None else ABSENT,
        model=Identifier(identity.model) if identity.model is not None else ABSENT,
    )


# The quartet as the fields carry it: one value per field, and four
# absences where the registry never built the provider.
_Quartet = tuple[
    Identifier | Absent, Identifier | Absent, Identifier | Absent, Identifier | Absent
]


def _entry_fields(provider: object) -> _Quartet:
    """One provider's entry, as the four values the events declare.

    The only crossing from the type above to the fields, which is what
    lets the type keep `provider` and `type` required now that the
    variants cannot: four values or four absences leave here, so a
    record naming an entry but no type has nowhere to come from.
    """
    entry = _entry_of(provider)
    if entry is None:
        return ABSENT, ABSENT, ABSENT, ABSENT
    return entry.provider, entry.type, entry.host, entry.model


def _spoken(held: Identifier | Absent) -> str | None:
    """What a fragment renders from a field that may be absent, in the
    vocabulary the fragment builders take."""
    return None if isinstance(held, Absent) else held.carried()


def tool_fragment(tool: str | None, entry: str | None) -> Fragment:
    """One of the three shapes a sentence renders where a call's name
    would go, chosen by which name the caller passes: a builtin's own
    name quoted, the configured MCP entry a call reached, or nothing.

    Rendering only. WHICH of a call's names may be printed is the
    caller's decision and is not checked here: hand this a device
    tool's name as `tool` and it quotes it. The three `tool_call`
    variants settle that question by declaring the fragment type each
    one carries; `pipeline.py`'s `_tool_fragment` settles it for the
    one sentence no variant declares, beside the classifier whose
    source constants the answer is read from.

    The one construction in this module built beside a log call rather
    than inside an emit thunk. A refusal here lands in the tool-error
    path instead of in the emitter's guard, and what bounds it is the
    fragment's own grammar, which admits any configured name.
    """
    if tool is not None:
        return QuotedToolName.of(tool)
    if entry is not None:
        return FromEntry.of(entry)
    return Nothing("")


# The builders below are thin, and each is one constructor call with its
# arguments wrapped. That is what they should be, and it is not what
# earns the module its name: the quartet type is, since it is the only
# place the four entry values are built, and the field lists and the
# value vocabulary are, since a caller no longer holds either.


def builtin_tool_called(
    agent: str, conversation: str, tool: str, duration_s: float, is_error: bool
) -> Variant:
    """The `tool_call` shape for a builtin: the one branch that names
    its tool, because a builtin's name is this server's own word."""
    return BuiltinToolCall(
        agent=Identifier(agent),
        conversation=ConversationId(conversation),
        tool=Identifier(tool),
        duration_ms=Whole(round(duration_s * 1000)),
        is_error=Flag(is_error),
        named=QuotedToolName.of(tool),
        duration_s=Real(duration_s),
        outcome=_tool_outcome(is_error),
    )


def mcp_tool_called(
    agent: str, conversation: str, entry: str, duration_s: float, is_error: bool
) -> Variant:
    """The `tool_call` shape for a server tool, which names the entry an
    operator wrote in their YAML and never the far side's own name."""
    return McpToolCall(
        agent=Identifier(agent),
        conversation=ConversationId(conversation),
        entry=Identifier(entry),
        duration_ms=Whole(round(duration_s * 1000)),
        is_error=Flag(is_error),
        named=FromEntry.of(entry),
        duration_s=Real(duration_s),
        outcome=_tool_outcome(is_error),
    )


def unnamed_tool_called(
    agent: str, conversation: str, source: str, duration_s: float, is_error: bool
) -> Variant:
    """The `tool_call` shape that names nothing.

    A device tool's name is the board's vocabulary and an unknown name
    is whatever the model invented, and the retained surface admits no
    far-side bytes whichever peer sent them (#154, the
    content-and-telemetry ADR). So `source` says which namespace was
    reached into, and the full name is on the store's `tool_invocations`
    row, where the text switch decides whether it is kept.
    """
    return UnnamedToolCall(
        agent=Identifier(agent),
        conversation=ConversationId(conversation),
        # The classifier answers `runtime/turns.py`'s own constants,
        # which are held equal to the store's column rather than to this
        # vocabulary, so the crossing is spelled here.
        source=_namespace(source),
        duration_ms=Whole(round(duration_s * 1000)),
        is_error=Flag(is_error),
        named=Nothing(""),
        duration_s=Real(duration_s),
        outcome=_tool_outcome(is_error),
    )


def builtin_sentence_withheld(
    agent: str, conversation: str, tool: str, characters: int
) -> Variant:
    """The `sentence_withheld` shape for a builtin: the one branch that
    names its tool, because a builtin's name is this server's own
    word."""
    return BuiltinSentenceWithheld(
        agent=Identifier(agent),
        conversation=ConversationId(conversation),
        tool=Identifier(tool),
        named=QuotedToolName.of(tool),
        characters=Count(characters),
    )


def mcp_sentence_withheld(
    agent: str, conversation: str, entry: str, characters: int
) -> Variant:
    """The `sentence_withheld` shape for a server tool, which names the
    entry an operator wrote in their YAML and never the far side's own
    tool name."""
    return McpSentenceWithheld(
        agent=Identifier(agent),
        conversation=ConversationId(conversation),
        entry=Identifier(entry),
        named=FromEntry.of(entry),
        characters=Count(characters),
    )


def unnamed_sentence_withheld(
    agent: str, conversation: str, source: str, characters: int
) -> Variant:
    """The `sentence_withheld` shape that names nothing: a board's own
    vocabulary, and the sentence whose arguments fit more than one
    offered tool, which is `unknown` because which tool it was is what
    could not be decided."""
    return UnnamedSentenceWithheld(
        agent=Identifier(agent),
        conversation=ConversationId(conversation),
        source=_namespace(source),
        named=Nothing(""),
        characters=Count(characters),
    )


def tool_arguments_coerced(
    agent: str,
    conversation: str,
    source: str,
    tool: str | None,
    entry: str | None,
    coerced: int,
) -> Variant:
    """The `tool_arguments_coerced` event for one corrected call.

    Takes the two names as the caller's decision already resolved them,
    exactly as `tool_fragment` does and for the same reason: which of a
    call's names may be printed is read from the classifier's own source
    constants, which live beside the classifier. What this owns is that
    the fragment and the fields agree, since one variant carries all
    four namespaces and a builtin named in the sentence but not in the
    payload would be two answers to one question.
    """
    return ToolArgumentsCoerced(
        agent=Identifier(agent),
        conversation=ConversationId(conversation),
        source=ToolSource(source),
        coerced=Count(coerced),
        named=tool_fragment(tool, entry),
        tool=Identifier(tool) if tool is not None else ABSENT,
        entry=Identifier(entry) if entry is not None else ABSENT,
    )


def _tool_outcome(is_error: bool) -> ToolOutcome:
    """How a call ended, in the one word the sentence renders."""
    return ToolOutcome.FAILED if is_error else ToolOutcome.ANSWERED


def _namespace(source: str) -> UnnamedToolSource:
    """The namespace word a call that names nothing carries.

    Two refusals stand behind this and neither is here, which is what
    the cast says. `ToolSource` refuses a word that is not one of the
    four, and the variant declares the two of them this shape may say,
    which `verify()` checks inside the emitter's guard before anything
    is rendered. Checking it a third time here would move a refusal out
    of the guard that holds it, for a narrowing the annotation cannot
    carry across a string.
    """
    return cast(UnnamedToolSource, ToolSource(source))


def llm_retried(
    agent: str,
    conversation: str,
    stage: str,
    provider: object,
    round_: int,
    elapsed: float,
) -> Variant:
    """The `llm_retry` event for this stall."""
    entry, type_, host, model = _entry_fields(provider)
    return LlmRetry(
        agent=Identifier(agent),
        conversation=ConversationId(conversation),
        round=Whole(round_),
        duration_ms=Whole(round(elapsed * 1000)),
        stage=Identifier(stage),
        duration_s=Real(elapsed),
        provider=entry,
        type=type_,
        host=host,
        model=model,
    )


def llm_rounded(
    agent: str,
    conversation: str,
    stage: str,
    provider: object,
    round_: int,
    turns: int,
    elapsed: float,
    input_tokens: int | None,
    output_tokens: int | None,
    first_token_ms: int | None,
) -> Variant:
    """The `llm_round` event for this generation.

    The token counts and the time to first token arrive as the plain
    numbers a provider reported, or as None where it reported none:
    their absence is a fact about the endpoint rather than a zero, and
    a round that only asked for a tool timed no spoken token.
    """
    entry, type_, host, model = _entry_fields(provider)
    return LlmRound(
        agent=Identifier(agent),
        conversation=ConversationId(conversation),
        round=Whole(round_),
        turns=Count(turns),
        duration_ms=Whole(round(elapsed * 1000)),
        stage=Identifier(stage),
        duration_s=Real(elapsed),
        provider=entry,
        type=type_,
        host=host,
        model=model,
        input_tokens=Count(input_tokens) if input_tokens is not None else ABSENT,
        output_tokens=Count(output_tokens) if output_tokens is not None else ABSENT,
        first_token_ms=Whole(first_token_ms) if first_token_ms is not None else ABSENT,
    )


def provider_failure(
    agent: str,
    conversation: str,
    stage: str,
    provider: object,
    failure: BaseException,
    elapsed: float,
) -> Variant:
    """The `provider_failed` event for this failure.

    The class name is reported and the exception's message is not, which
    the value types make structural rather than careful: `ClassName` is
    built from the exception itself, and there is no value in this
    vocabulary a message could be constructed as. The exception crosses
    as itself because it is a builtin, so nothing about a provider's own
    types reaches this module.

    Which failure is a wait is a question of type. Every provider raises
    `ProviderCallTimeout` for its SDK's timeouts and that is a
    `TimeoutError`, as are `asyncio.TimeoutError` and the watchdog's own
    `FirstTokenTimeout`, so one `isinstance` covers the lot (#137). It
    used to be decided by looking for "Timeout" in the class name,
    because the SDKs' own classes agreed on nothing:
    `openai.APITimeoutError` is an `APIConnectionError` and
    `httpx.TimeoutException` inherits from neither.
    """
    outcome = (
        ProviderOutcome.TIMED_OUT
        if isinstance(failure, TimeoutError)
        else ProviderOutcome.FAILED
    )
    entry, type_, host, model = _entry_fields(provider)
    return ProviderFailed(
        agent=Identifier(agent),
        conversation=ConversationId(conversation),
        error=ClassName.of(failure),
        duration_ms=Whole(round(elapsed * 1000)),
        stage=Identifier(stage),
        named=QuotedProvider.of(_spoken(entry)),
        outcome=outcome,
        duration_s=Real(elapsed),
        where=ReachingHost.of(_spoken(host)),
        provider=entry,
        type=type_,
        host=host,
        model=model,
    )
