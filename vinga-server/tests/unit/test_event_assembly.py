"""What `events/assembly.py` builds, asked of its whole interface.

The module is what the reply path stopped knowing: a caller hands it
the thing that happened in plain values and is handed the variant that
describes it. So every claim here is made the way a caller makes it, by
calling an exported name, and every builder is checked against the
variant written out by hand, which is the comparison that would catch a
field landing in the wrong one.

The one structural claim is the entry quartet's. Since the collapse
each of the three events declares `provider`, `type`, `host` and
`model` as absent-able, so the variant itself would accept an entry
name with no type beside it; what refuses that is the frozen type
inside this module, whose `provider` and `type` are required, and the
single crossing from it to the fields. Neither is exported, and the
export list is pinned below for exactly that reason: the builders are
the only way to make the four, so "whole or not at all" is a fact about
the module rather than a habit at its call sites. The emission-level
half of the same claim is in `test_event_surface_pins.py`, where a
record from a provider the registry never built is checked for the
absence of the key.

A declaration-time entanglement check, which would let the catalog say
this itself, is deliberately out of scope (#240); this file plus that
one is the pin standing in for it.
"""

from dataclasses import dataclass

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


def test_the_module_exports_its_builders_and_nothing_that_makes_an_entry() -> None:
    """The interface is the builders and the fragment. The quartet type
    and the reader that fills it are not in it, which is what makes the
    builders the only producers of the four entry values."""
    assert sorted(assembly.__all__) == [
        "builtin_tool_called",
        "llm_retried",
        "llm_rounded",
        "mcp_tool_called",
        "provider_failure",
        "tool_fragment",
        "unnamed_tool_called",
    ]


# --- the entry quartet, whole or not at all ---------------------------


def carried(built: Variant) -> dict[str, object]:
    """The payload a variant would ride, which is where an absent field
    is a key that is not there."""
    return built.payload()


QUARTET = ("provider", "type", "host", "model")


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
        assembly.llm_retried("poet", "llm", provider, 2, 0.5),
        assembly.llm_rounded("poet", "llm", provider, 2, 3, 0.5, 140, 12, 220),
        assembly.provider_failure("poet", "llm", provider, ConnectionRefusedError(), 0.5),
    ]

    for one in built:
        named = {key for key in QUARTET if key in carried(one)}
        assert named in ({"provider", "type", "host", "model"}, {"provider", "type"}, set())


def test_an_engine_in_this_process_names_its_entry_and_no_host() -> None:
    """The two halves that are absent on their own: `host` for an engine
    that reaches nothing, `model` for a type with none to name."""
    payload = carried(assembly.llm_retried("poet", "asr", IN_PROCESS, 1, 0.5))

    assert payload["provider"] == "local"
    assert payload["type"] == "sensevoice"
    assert "host" not in payload
    assert "model" not in payload


# --- one shape per site, compared against the variant itself ----------


def test_a_retry_on_a_configured_provider_carries_its_entry() -> None:
    assert assembly.llm_retried("poet", "llm", CLOUD, 2, 0.5) == LlmRetry(
        agent=Identifier("poet"),
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
    assert assembly.llm_retried("poet", "llm", UNREGISTERED, 2, 0.5) == LlmRetry(
        agent=Identifier("poet"),
        round=Whole(2),
        duration_ms=Whole(500),
        stage=Identifier("llm"),
        duration_s=Real(0.5),
    )


def test_a_round_carries_the_numbers_the_provider_reported() -> None:
    assert assembly.llm_rounded(
        "poet", "llm", CLOUD, 2, 3, 0.5, 140, 12, 220
    ) == LlmRound(
        agent=Identifier("poet"),
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
        assembly.llm_rounded("poet", "llm", UNREGISTERED, 1, 1, 0.5, None, None, None)
    )

    assert "input_tokens" not in payload
    assert "output_tokens" not in payload
    assert "first_token_ms" not in payload


def test_a_failure_names_the_entry_and_the_host_it_reached() -> None:
    assert assembly.provider_failure(
        "poet", "llm", CLOUD, ConnectionRefusedError("no route"), 0.5
    ) == ProviderFailed(
        agent=Identifier("poet"),
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
        "poet", "asr", UNREGISTERED, ConnectionRefusedError("no route"), 0.5
    ) == ProviderFailed(
        agent=Identifier("poet"),
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
    timed_out = assembly.provider_failure("poet", "llm", UNREGISTERED, TimeoutError(), 0.5)

    assert timed_out.outcome is ProviderOutcome.TIMED_OUT  # type: ignore[attr-defined]


# --- the three tool-call shapes ---------------------------------------


def test_a_builtin_call_names_the_tool_this_server_authored() -> None:
    assert assembly.builtin_tool_called("poet", "remember", 0.25, False) == BuiltinToolCall(
        agent=Identifier("poet"),
        tool=Identifier("remember"),
        duration_ms=Whole(250),
        is_error=Flag(False),
        named=QuotedToolName(' "remember"'),
        duration_s=Real(0.25),
        outcome=ToolOutcome.ANSWERED,
    )


def test_a_server_call_names_the_entry_an_operator_wrote() -> None:
    assert assembly.mcp_tool_called("poet", "tools", 0.25, True) == McpToolCall(
        agent=Identifier("poet"),
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
    assert assembly.unnamed_tool_called("poet", str(source), 0.25, False) == UnnamedToolCall(
        agent=Identifier("poet"),
        source=source,
        duration_ms=Whole(250),
        is_error=Flag(False),
        named=Nothing(""),
        duration_s=Real(0.25),
        outcome=ToolOutcome.ANSWERED,
    )


# --- the fragment the sentence that is not an event renders -----------


def test_the_fragment_names_what_this_application_authored() -> None:
    """Built beside a log line rather than inside an emit thunk, and the
    only naming rule in this module no variant declares for itself."""
    assert assembly.tool_fragment("remember", None).carried() == ' "remember"'
    assert assembly.tool_fragment(None, "tools").carried() == ' from entry "tools"'
    assert assembly.tool_fragment(None, None).carried() == ""
