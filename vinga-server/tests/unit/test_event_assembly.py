"""What `events/assembly.py` builds, asked of its whole interface.

The module is what the reply path stopped knowing: a caller hands it
the thing that happened in plain values and is handed the variant that
describes it. So every claim here is made the way a caller makes it, by
calling an exported name, and every builder is checked against the
variant written out by hand, which is the comparison that would catch a
field landing in the wrong one.

The one structural claim is the entry quartet's, and it is made by two
tests that answer different halves of it. Since the collapse each of
the three events declares `provider`, `type`, `host` and `model` as
absent-able, so the variant itself would accept an entry name with no
type beside it; what refuses that is the frozen type inside this
module, whose `provider` and `type` are required, and the single
crossing from it to the fields.

That the crossing keeps its promise is what the builder test says: it
asks every builder that carries the quartet, over every shape an
identity has, and a crossing that answered a half quartet fails it.
That nothing ELSE can make the four is what the namespace test says: it
reads the module's public names rather than its `__all__`, since
`__all__` governs `import *` alone and a public helper added beside it
would pass a check on the list while being importable by anybody.
Together they are what makes "whole or not at all" a fact about the
module rather than a habit at its call sites. The emission-level half
of the same claim is in `test_event_surface_pins.py`, where a record
from a provider the registry never built is checked for the absence of
the key.

A declaration-time entanglement check, which would let the catalog say
this itself, is deliberately out of scope (#240); this file plus that
one is the pin standing in for it.
"""

from dataclasses import dataclass
from types import ModuleType

import pytest

from vinga_server.events import assembly
from vinga_server.events.catalog import (
    BuiltinToolCall,
    LlmRetry,
    LlmRound,
    McpToolCall,
    ProviderFailed,
    UnnamedToolCall,
    Variant,
)
from vinga_server.events.values import (
    ClassName,
    ConversationId,
    Count,
    Flag,
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
    Whole,
)
from vinga_server.providers.base import ProviderIdentity


@dataclass(frozen=True)
class Stamped:
    """A provider as the assembly sees one: an object with an identity,
    or without.

    The registry stamps a `ProviderIdentity` onto every provider it
    builds, and the assembly reads it off whatever it is handed, which
    is what lets it take a plain `object` and import nothing from
    `providers/`. The real type is used here rather than a stand-in, so
    the four attribute names are pinned where they are actually read.
    """

    identity: ProviderIdentity | None = None


CLOUD = Stamped(
    ProviderIdentity(
        stage="llm",
        name="cloud",
        type="openai",
        host="api.example.com",
        model="gpt-4o-mini",
    )
)

# An engine that runs in this process reaches no host, and a type with
# no model to name carries none: the two halves of the quartet that are
# absent-able on their own.
IN_PROCESS = Stamped(
    ProviderIdentity(stage="asr", name="local", type="sensevoice", host=None, model=None)
)

# A provider the registry never built, which in practice is a fixture's.
UNREGISTERED = Stamped()


INTERFACE = [
    "builtin_tool_called",
    "llm_retried",
    "llm_rounded",
    "mcp_tool_called",
    "provider_failure",
    "tool_arguments_coerced",
    "tool_fragment",
    "unnamed_tool_called",
]


def defined_here(module: ModuleType) -> list[str]:
    """Every public name a module DEFINES, its imports excluded.

    `vars()` holds what a module imported as well as what it wrote, and
    what `assembly.py` imports is the catalog and the value vocabulary,
    which say nothing about its own interface. What is left is what a
    caller can reach and this module is answerable for.
    """
    return sorted(
        name
        for name, held in vars(module).items()
        if not name.startswith("_")
        and getattr(held, "__module__", None) == module.__name__
    )


def test_the_module_defines_its_builders_and_nothing_that_makes_an_entry() -> None:
    """The interface is the seven builders and the fragment. The quartet
    type and the crossing that fills it are private, which is what makes
    the builders the only producers of the four entry values.

    Asked of the module's namespace rather than of `__all__`, because
    `__all__` governs `import *` and nothing else: a public
    `entry_fields` added beside it would be importable by name while a
    check on the list stayed green. The list is then held to the
    namespace, so the two cannot drift.
    """
    assert defined_here(assembly) == INTERFACE
    assert sorted(assembly.__all__) == INTERFACE


# --- the entry quartet, whole or not at all ---------------------------


def carried(built: Variant) -> dict[str, object]:
    """The payload a variant would ride, which is where an absent field
    is a key that is not there."""
    return built.payload()


QUARTET = ("provider", "type", "host", "model")

# The thread every builder here is told about. A uuid hex, the shape the
# runtime mints, so a builder is exercised with what a session hands it.
THREAD = "9f0c1d2e3a4b5c6d7e8f90a1b2c3d4e5"


@pytest.mark.parametrize("provider", [CLOUD, IN_PROCESS, UNREGISTERED])
def test_no_builder_names_an_entry_without_naming_its_type(provider: Stamped) -> None:
    """The entanglement, asked of every builder that carries the
    quartet and every shape a provider's identity has.

    `host` and `model` are absent-able on their own and are not part of
    this claim; `provider` and `type` are one answer, and a record
    carrying either alone would name a configuration entry this server
    cannot say the type of.
    """
    built = [
        assembly.llm_retried("poet", THREAD, "llm", provider, 2, 0.5),
        assembly.llm_rounded("poet", THREAD, "llm", provider, 2, 3, 0.5, 140, 12, 220),
        assembly.provider_failure("poet", THREAD, "llm", provider, ConnectionRefusedError(), 0.5),
    ]

    for one in built:
        named = {key for key in QUARTET if key in carried(one)}
        assert named in ({"provider", "type", "host", "model"}, {"provider", "type"}, set())


def test_an_engine_in_this_process_names_its_entry_and_no_host() -> None:
    """The two halves that are absent on their own: `host` for an engine
    that reaches nothing, `model` for a type with none to name."""
    payload = carried(assembly.llm_retried("poet", THREAD, "asr", IN_PROCESS, 1, 0.5))

    assert payload["provider"] == "local"
    assert payload["type"] == "sensevoice"
    assert "host" not in payload
    assert "model" not in payload


# --- one shape per site, compared against the variant itself ----------


def test_a_retry_on_a_configured_provider_carries_its_entry() -> None:
    assert assembly.llm_retried("poet", THREAD, "llm", CLOUD, 2, 0.5) == LlmRetry(
        agent=Identifier("poet"),
        conversation=ConversationId(THREAD),
        round=Whole(2),
        duration_ms=Whole(500),
        stage=Identifier("llm"),
        duration_s=Real(0.5),
        provider=Identifier("cloud"),
        type=Identifier("openai"),
        host=Identifier("api.example.com"),
        model=Identifier("gpt-4o-mini"),
    )


def test_a_retry_on_a_provider_with_no_identity_says_less() -> None:
    """The same variant, and the fields it cannot fill left absent:
    since the collapse this is one shape saying less rather than a
    second shape."""
    assert assembly.llm_retried("poet", THREAD, "llm", UNREGISTERED, 2, 0.5) == LlmRetry(
        agent=Identifier("poet"),
        conversation=ConversationId(THREAD),
        round=Whole(2),
        duration_ms=Whole(500),
        stage=Identifier("llm"),
        duration_s=Real(0.5),
    )


def test_a_round_carries_the_numbers_the_provider_reported() -> None:
    assert assembly.llm_rounded("poet", THREAD, "llm", CLOUD, 2, 3, 0.5, 140, 12, 220
    ) == LlmRound(
        agent=Identifier("poet"),
        conversation=ConversationId(THREAD),
        round=Whole(2),
        turns=Count(3),
        duration_ms=Whole(500),
        stage=Identifier("llm"),
        duration_s=Real(0.5),
        provider=Identifier("cloud"),
        type=Identifier("openai"),
        host=Identifier("api.example.com"),
        model=Identifier("gpt-4o-mini"),
        input_tokens=Count(140),
        output_tokens=Count(12),
        first_token_ms=Whole(220),
    )


def test_a_round_that_reported_nothing_carries_no_zeroes() -> None:
    """An endpoint that reported no usage and a round that spoke no
    token are absences rather than zeroes, which is a different fact and
    has to stay a different record."""
    payload = carried(
        assembly.llm_rounded("poet", THREAD, "llm", UNREGISTERED, 1, 1, 0.5, None, None, None)
    )

    assert "input_tokens" not in payload
    assert "output_tokens" not in payload
    assert "first_token_ms" not in payload


def test_a_failure_names_the_entry_and_the_host_it_reached() -> None:
    assert assembly.provider_failure(
        "poet", THREAD, "llm", CLOUD, ConnectionRefusedError("no route"), 0.5
    ) == ProviderFailed(
        agent=Identifier("poet"),
        conversation=ConversationId(THREAD),
        error=ClassName("ConnectionRefusedError"),
        duration_ms=Whole(500),
        stage=Identifier("llm"),
        named=QuotedProvider(' "cloud"'),
        outcome=ProviderOutcome.FAILED,
        duration_s=Real(0.5),
        where=ReachingHost(" reaching api.example.com"),
        provider=Identifier("cloud"),
        type=Identifier("openai"),
        host=Identifier("api.example.com"),
        model=Identifier("gpt-4o-mini"),
    )


def test_a_failure_with_no_entry_renders_nothing_in_both_positions() -> None:
    """The collapse's one loosening, at the site that needed it: the two
    fragments are the optional forms now, and this is the record that
    made them so."""
    assert assembly.provider_failure(
        "poet", THREAD, "asr", UNREGISTERED, ConnectionRefusedError("no route"), 0.5
    ) == ProviderFailed(
        agent=Identifier("poet"),
        conversation=ConversationId(THREAD),
        error=ClassName("ConnectionRefusedError"),
        duration_ms=Whole(500),
        stage=Identifier("asr"),
        named=QuotedProvider(""),
        outcome=ProviderOutcome.FAILED,
        duration_s=Real(0.5),
        where=ReachingHost(""),
    )


def test_a_wait_is_told_from_a_refusal_by_type() -> None:
    """One `isinstance` covers every timeout the five providers raise,
    the watchdog's own included (#137). The outcome is rendered and not
    carried, so the variant is what says it."""
    timed_out = assembly.provider_failure("poet", THREAD, "llm", UNREGISTERED, TimeoutError(), 0.5)

    assert timed_out.outcome is ProviderOutcome.TIMED_OUT  # type: ignore[attr-defined]


# --- the three tool-call shapes ---------------------------------------


def test_a_builtin_call_names_the_tool_this_server_authored() -> None:
    assert assembly.builtin_tool_called("poet", THREAD, "remember", 0.25, False) == BuiltinToolCall(
        agent=Identifier("poet"),
        conversation=ConversationId(THREAD),
        tool=Identifier("remember"),
        duration_ms=Whole(250),
        is_error=Flag(False),
        named=QuotedToolName(' "remember"'),
        duration_s=Real(0.25),
        outcome=ToolOutcome.ANSWERED,
    )


def test_a_server_call_names_the_entry_an_operator_wrote() -> None:
    assert assembly.mcp_tool_called("poet", THREAD, "tools", 0.25, True) == McpToolCall(
        agent=Identifier("poet"),
        conversation=ConversationId(THREAD),
        entry=Identifier("tools"),
        duration_ms=Whole(250),
        is_error=Flag(True),
        named=FromEntry(' from entry "tools"'),
        duration_s=Real(0.25),
        outcome=ToolOutcome.FAILED,
    )


@pytest.mark.parametrize("source", [ToolSource.DEVICE, ToolSource.UNKNOWN])
def test_a_call_this_surface_may_not_name_names_only_its_namespace(
    source: ToolSource,
) -> None:
    """A device tool's name is the board's vocabulary and an unknown one
    is whatever the model invented, so the shape carries neither."""
    assert assembly.unnamed_tool_called(
        "poet", THREAD, str(source), 0.25, False
    ) == UnnamedToolCall(
        agent=Identifier("poet"),
        conversation=ConversationId(THREAD),
        source=source,
        duration_ms=Whole(250),
        is_error=Flag(False),
        named=Nothing(""),
        duration_s=Real(0.25),
        outcome=ToolOutcome.ANSWERED,
    )


# --- the fragment the sentence that is not an event renders -----------


def test_the_fragment_renders_whichever_name_it_is_handed() -> None:
    """Rendering, which is all this function does.

    WHICH of a call's names may be printed is not pinned here and is not
    decided here: hand this a device tool's name as the first argument
    and it quotes it, because the decision reads the classifier's source
    constants and lives beside them, in `pipeline.py`'s `_tool_fragment`.
    What pins the decision is `test_session_tools.py`, at the sentence
    itself: a builtin's name reaches the warning line and a name no
    namespace publishes does not.

    Built beside a log call rather than inside an emit thunk, which is
    the one exception in this module to the rule that a value refuses
    where the emitter's guard is holding it.
    """
    assert assembly.tool_fragment("remember", None).carried() == ' "remember"'
    assert assembly.tool_fragment(None, "tools").carried() == ' from entry "tools"'
    assert assembly.tool_fragment(None, None).carried() == ""
