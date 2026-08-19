"""What a typed event's vocabulary admits, and what it says when it
refuses.

Two claims, and the second is the one that matters more. The first is
ordinary: each value type accepts what its kind describes and refuses
what it does not, at construction rather than at emit, so a site that
holds one has already proved it. The second is the no-leak claim these
types inherit from the enforcement diagnostics: the value handed to a
refusing constructor is precisely what may not reach a log, a lane's
stderr or an exception chain, so a credential-shaped sentinel goes
through every refusing branch and is hunted in the exception's `str`,
its `repr` and its `args`.

Asserted by absence AND by equality where the shape allows it, for the
reason the sentinel suite gives: a substring hunt proves only that this
spelling did not appear.
"""

import os
from pathlib import Path

import pytest

from vinga_server.conversations.schema import CLOSE_REASONS as STORED_CLOSE_REASONS
from vinga_server.conversations.schema import TOOL_SOURCES as STORED_TOOL_SOURCES
from vinga_server.device.session import CLOSE_REASONS as CLOSED_BY
from vinga_server.events.values import (
    ABSENT,
    AgentList,
    AgentNames,
    AlsoBoundTo,
    ClassName,
    ClientId,
    CloseReason,
    CloseReasonToken,
    ConfiguredPath,
    Count,
    DeviceId,
    DeviceOrUnidentified,
    EventName,
    EventValueError,
    Flag,
    FromEntry,
    Identifier,
    LanguageTag,
    Nothing,
    PromptSources,
    ProviderOutcome,
    ProviderOutcomeToken,
    QuotedProvider,
    QuotedToolName,
    ReachingHost,
    Real,
    RejectionToken,
    SessionId,
    ToolOutcome,
    ToolSource,
    ToolSourceToken,
    UnnamedToolSource,
    Whole,
)
from vinga_server.runtime.turns import TOOL_SOURCES as CLASSIFIED_AS

# The same spelling the enforcement sentinels use: printable, so it is
# an ordinary string rather than something a type check would catch
# anyway, and dotted, so it satisfies no declared `ID` syntax.
SENTINEL = "sk.leak.4a7d2f1e.never-a-real-credential"


# --- what each type admits --------------------------------------------


def test_an_identifier_is_any_configured_name() -> None:
    """The configuration's own domain and no tighter: a quote and a
    control character are lawful configuration today, and a value type
    claiming more would refuse a deployment the configuration took."""
    assert Identifier('secondary"agent').carried() == 'secondary"agent'
    assert Identifier("a\x07b").carried() == "a\x07b"


@pytest.mark.parametrize("refused", ["", "   ", 7, None])
def test_an_identifier_refuses_what_is_not_a_name(refused: object) -> None:
    with pytest.raises(EventValueError):
        Identifier(refused)  # type: ignore[arg-type]


def test_a_session_id_is_the_bounded_machine_form() -> None:
    assert SessionId("alpha").carried() == "alpha"
    assert SessionId("a" * 64).carried() == "a" * 64


@pytest.mark.parametrize("refused", ["", "a" * 65, "has space", "dotted.id", 7])
def test_a_session_id_refuses_anything_outside_its_syntax(refused: object) -> None:
    with pytest.raises(EventValueError):
        SessionId(refused)  # type: ignore[arg-type]


def test_an_event_name_is_the_catalogs_own_key() -> None:
    assert EventName("conversations_enabled").carried() == "conversations_enabled"
    with pytest.raises(EventValueError):
        EventName("Conversations")


def test_a_class_name_is_a_python_identifier() -> None:
    assert ClassName("RuntimeError").carried() == "RuntimeError"


@pytest.mark.parametrize("refused", ["", "not a class", "near a value: syntax error", 7])
def test_a_class_name_refuses_a_message(refused: object) -> None:
    with pytest.raises(EventValueError):
        ClassName(refused)  # type: ignore[arg-type]


def test_a_class_name_is_built_from_the_failure_itself() -> None:
    """`of` takes the exception rather than a string, which is what
    keeps a site from spelling `str(exc)` one edit later."""
    failure = RuntimeError("near a value nothing may repeat: syntax error")

    named = ClassName.of(failure)

    assert named.carried() == "RuntimeError"
    assert str(failure) not in repr(named)


def test_a_count_is_zero_or_more_and_never_a_boolean() -> None:
    assert Count(0).carried() == 0
    assert Count(90).carried() == 90
    for refused in (-1, True, 1.5, "2"):
        with pytest.raises(EventValueError):
            Count(refused)  # type: ignore[arg-type]


def test_a_configured_path_carries_text_and_renders_the_object() -> None:
    """The one value whose two surfaces differ, and the difference is
    the surface's own: the field holds the path as text, the sentence
    renders the object the site passed."""
    directory = Path("/var/lib/vinga")

    value = ConfiguredPath(directory)

    assert value.carried() == os.fspath(directory)
    assert value.rendered() is directory


@pytest.mark.parametrize("refused", ["", "  ", 7, None])
def test_a_configured_path_refuses_what_is_not_a_path(refused: object) -> None:
    with pytest.raises(EventValueError):
        ConfiguredPath(refused)  # type: ignore[arg-type]


def test_absence_is_its_own_value_rather_than_null() -> None:
    """A field that is present and null is a fact the record states; a
    field that is absent is a key the JSON object does not have. The
    two are different answers and this is the second one."""
    assert ABSENT is not None
    assert repr(ABSENT) == "ABSENT"


# --- the session channel's own values ---------------------------------


def test_a_device_id_is_the_canonical_mac() -> None:
    assert DeviceId("aa:bb:cc:dd:ee:ff").carried() == "aa:bb:cc:dd:ee:ff"


@pytest.mark.parametrize(
    "refused", ["AA:BB:CC:DD:EE:FF", "aa-bb-cc-dd-ee-ff", "not-a-mac", "", 7]
)
def test_a_device_id_refuses_anything_normalize_mac_would_not_answer(
    refused: object,
) -> None:
    """The canonical form and nothing else. What arrives in a Device-Id
    header is bytes an unauthenticated caller chose, and the value that
    rides a record is what `normalize_mac` made of it."""
    with pytest.raises(EventValueError):
        DeviceId(refused)  # type: ignore[arg-type]


def test_a_language_tag_is_a_code_rather_than_a_sentence() -> None:
    assert LanguageTag("en").carried() == "en"
    assert LanguageTag("en-US").carried() == "en-US"
    with pytest.raises(EventValueError):
        LanguageTag("the user spoke English")


def test_a_whole_is_a_measurement_and_never_a_boolean() -> None:
    assert Whole(0).carried() == 0
    assert Whole(-3).carried() == -3
    for refused in (True, 1.5, "2"):
        with pytest.raises(EventValueError):
            Whole(refused)  # type: ignore[arg-type]


def test_a_real_admits_an_integral_measure_and_refuses_the_unmeasurable() -> None:
    """An `int` where a measure is integral, which is what the sites
    pass; NaN and the infinities are not measurements and JSON cannot
    carry them."""
    assert Real(2).carried() == 2
    assert Real(0.25).carried() == 0.25
    for refused in (float("nan"), float("inf"), float("-inf"), True, "2"):
        with pytest.raises(EventValueError):
            Real(refused)  # type: ignore[arg-type]


def test_a_flag_is_a_boolean_and_not_a_number() -> None:
    assert Flag(False).carried() is False
    with pytest.raises(EventValueError):
        Flag(1)  # type: ignore[arg-type]


def test_agent_names_carry_a_list_and_hold_each_element_to_a_name() -> None:
    assert AgentNames(("poet", "tutor")).carried() == ["poet", "tutor"]
    with pytest.raises(EventValueError):
        AgentNames(("poet", "  "))


def test_a_client_id_is_bounded_and_printable() -> None:
    """The one descriptor on this channel: what a device says about
    itself, bounded for the event while the manifest keeps the header as
    it arrived."""
    assert ClientId("a" * 64).carried() == "a" * 64
    for refused in ("", "a" * 65, "two\nlines", "\x1b[31m"):
        with pytest.raises(EventValueError):
            ClientId(refused)


def test_prompt_sources_carry_sizes_by_a_declared_provenance() -> None:
    assert PromptSources({"persona": 4, "fragment:tone": 9}).carried() == {
        "persona": 4,
        "fragment:tone": 9,
    }


@pytest.mark.parametrize(
    "refused",
    [
        {"memory": 4},
        {"persona": -1},
        {"persona": True},
        {"whatever the user said": 4},
        {4: 4},
        "persona",
    ],
)
def test_prompt_sources_refuse_anything_that_is_not_a_size_by_provenance(
    refused: object,
) -> None:
    """`memory` fails here like any unknown prefix: `prompt_assembled`
    reports the cached half of the prompt and excludes the per-round
    memory read deliberately."""
    with pytest.raises(EventValueError):
        PromptSources(refused)  # type: ignore[arg-type]


# --- the closed sets are closed, and by their decision sites ----------


def test_the_close_reasons_are_the_ones_the_edge_can_latch() -> None:
    """The enumeration restates what `device/session.py` decides, which
    is the link the conformance walk's token sidecar used to hold. Here
    by equality, so a sixth reason latched there and not declared here
    fails rather than degrades."""
    assert frozenset(CloseReason) == frozenset(CLOSED_BY)
    assert frozenset(CloseReason) == frozenset(STORED_CLOSE_REASONS)


def test_the_tool_sources_are_the_ones_the_classifier_can_answer() -> None:
    assert frozenset(ToolSource) == frozenset(CLASSIFIED_AS)
    assert frozenset(ToolSource) == frozenset(STORED_TOOL_SOURCES)


def test_a_token_admits_its_set_and_refuses_everything_else() -> None:
    assert CloseReasonToken(CloseReason.DRAIN).carried() == "drain"
    assert RejectionToken("no_agent").carried() == "no_agent"
    with pytest.raises(EventValueError):
        CloseReasonToken("hung_up")


def test_a_token_carries_a_plain_string_rather_than_its_member() -> None:
    """An enumeration member is a `str` subclass, so a record carrying
    one would put the subclass's name into a baseline's argument types
    and its `repr` into anything that renders it."""
    carried = CloseReasonToken(CloseReason.CLIENT).carried()

    assert type(carried) is str


def test_a_narrowed_token_refuses_the_members_its_variant_may_not_say() -> None:
    """A `tool_call` that names nothing is a device call or an invented
    one. A builtin is neither, and the type is where that is refused
    rather than at review."""
    assert UnnamedToolSource(ToolSource.DEVICE).carried() == "device"
    assert UnnamedToolSource.TOKENS == frozenset({"device", "unknown"})
    assert ToolSourceToken.TOKENS == frozenset({"builtin", "device", "mcp", "unknown"})
    with pytest.raises(EventValueError):
        UnnamedToolSource(ToolSource.BUILTIN)


def test_the_outcome_tokens_are_the_words_their_sentences_use() -> None:
    """Short or long, a set is closed or it is not."""
    assert ProviderOutcomeToken(ProviderOutcome.TIMED_OUT).carried() == "timed out"
    assert frozenset(ToolOutcome) == frozenset({"", " and failed"})


# --- the formatted fragments ------------------------------------------


def test_a_fragment_is_built_by_the_type_that_declares_its_grammar() -> None:
    """The builder and the grammar are one statement, so a site cannot
    assemble a shape the declaration does not describe."""
    assert AlsoBoundTo.of(("tutor", "poet")).carried() == " (also bound to tutor, poet)"
    assert AlsoBoundTo.of(()).carried() == ""
    assert AgentList.of(("poet",)).carried() == "poet"
    assert QuotedToolName.of("remember").carried() == ' "remember"'
    assert FromEntry.of("tools").carried() == ' from entry "tools"'
    assert QuotedProvider.of("cloud").carried() == ' "cloud"'
    assert ReachingHost.of("api.example.com").carried() == " reaching api.example.com"
    assert ReachingHost.of(None).carried() == ""
    assert Nothing("").carried() == ""
    assert DeviceOrUnidentified.of(None).carried() == "an unidentified device"
    assert DeviceOrUnidentified.of("aa:bb:cc:dd:ee:ff").carried() == "aa:bb:cc:dd:ee:ff"


@pytest.mark.parametrize(
    "build",
    [
        lambda: Nothing("something"),
        lambda: AlsoBoundTo("also bound to tutor"),
        lambda: AgentList(""),
        lambda: QuotedToolName("remember"),
        lambda: FromEntry(' "tools"'),
        lambda: ReachingHost("api.example.com"),
        lambda: DeviceOrUnidentified("a device nobody knows"),
    ],
)
def test_a_fragment_that_is_not_its_shape_is_refused(build: object) -> None:
    """Bounded by structure rather than by a character class: what an
    operator may call something is not this module's business, and the
    shape around it is."""
    with pytest.raises(EventValueError):
        build()  # type: ignore[operator]


# --- and none of them ever repeats what it refused --------------------


# Every type that can refuse the sentinel on its VALUE rather than on
# its Python type. `Identifier` and `ConfiguredPath` are deliberately
# absent: both admit any non-blank string, because that is what the
# configuration guarantees, so neither has a value-shaped refusal to
# drive. Their type-shaped refusals are asserted above.
REFUSING = (
    ("session id", lambda: SessionId(SENTINEL)),
    ("device id", lambda: DeviceId(SENTINEL)),
    ("language tag", lambda: LanguageTag(SENTINEL)),
    ("event name", lambda: EventName(SENTINEL)),
    ("class name", lambda: ClassName(SENTINEL)),
    ("count", lambda: Count(SENTINEL)),
    ("whole", lambda: Whole(SENTINEL)),
    ("real", lambda: Real(SENTINEL)),
    ("flag", lambda: Flag(SENTINEL)),
    ("client id", lambda: ClientId(SENTINEL * 4)),
    ("agent names", lambda: AgentNames((SENTINEL, "  "))),
    ("prompt sources", lambda: PromptSources({SENTINEL: 1})),
    ("close reason", lambda: CloseReasonToken(SENTINEL)),
    ("narrowed tool source", lambda: UnnamedToolSource(SENTINEL)),
    ("fragment", lambda: FromEntry(SENTINEL)),
)


@pytest.mark.parametrize("name, build", REFUSING, ids=[one for one, _ in REFUSING])
def test_a_refusal_never_repeats_the_value_it_refused(
    name: str, build: object
) -> None:
    """The rule the whole surface keeps, applied one layer earlier than
    the enforcement diagnostics keep it. A construction refusal reaches
    a lane's stderr in strict mode and the emitter's guard in forgiving
    mode, and the value is what neither may carry."""
    with pytest.raises(EventValueError) as raised:
        build()  # type: ignore[operator]

    assert SENTINEL not in str(raised.value)
    assert SENTINEL not in repr(raised.value)
    assert SENTINEL not in repr(raised.value.args)


def test_an_identifier_refusal_names_the_type_and_the_constraint() -> None:
    """By equality rather than by absence, because absence alone proves
    only that this spelling did not appear."""
    with pytest.raises(EventValueError) as raised:
        Identifier("   ")

    assert raised.value.args == ("an Identifier is non-empty once stripped",)
