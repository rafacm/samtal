"""What a typed event's vocabulary admits, and what it says when it
refuses.

Two claims, and the second is the one that matters more. The first is
ordinary: each value type accepts what its kind describes and refuses
what it does not, at construction rather than at emit, so a site that
holds one has already proved it. The second is the no-leak claim these
types inherit from the emitter's refusal report: the value handed to a
refusing constructor is precisely what may not reach a log, a lane's
stderr or an exception chain, so a credential-shaped sentinel goes
through every refusing branch and is hunted in the exception's `str`,
its `repr` and its `args`.

Asserted by absence AND by equality where the shape allows it, for the
reason the sentinel suite gives: a substring hunt proves only that this
spelling did not appear.
"""

import inspect
import os
from pathlib import Path
from typing import get_args

import pytest

from vinga_server.conversations.schema import CLOSE_REASONS as STORED_CLOSE_REASONS
from vinga_server.conversations.schema import TOOL_SOURCES as STORED_TOOL_SOURCES
from vinga_server.device.session import CLOSE_REASONS as CLOSED_BY
from vinga_server.events.values import (
    ABSENT,
    ActivationCode,
    ActivationRefusal,
    AgentList,
    AgentNames,
    AlsoBoundTo,
    AuthRejection,
    BoardName,
    Bounds,
    CaptureDeclined,
    CaptureWrite,
    ClassName,
    ClassNames,
    ClientId,
    CloseReason,
    ConfiguredPath,
    Count,
    DeviceId,
    DeviceOrUnidentified,
    EchoOutcome,
    EventName,
    EventValueError,
    FirmwareVersion,
    Flag,
    FromEntry,
    Identifier,
    LanguageTag,
    McpConnectFailure,
    McpDown,
    McpRefusal,
    McpReloadOutcome,
    McpTransport,
    Nothing,
    NotOffered,
    OriginProvenance,
    OriginSource,
    OtaRefusal,
    PendingRefusal,
    PromptSources,
    ProviderOutcome,
    QuotedProvider,
    QuotedToolName,
    ReachingHost,
    Real,
    ReportedMac,
    SessionId,
    SessionIds,
    SessionList,
    ToolOutcome,
    ToolSource,
    Whole,
)
from vinga_server.runtime.turns import TOOL_SOURCES as CLASSIFIED_AS

# The same spelling the refusal sentinels use: printable, so it is
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


def test_the_outcome_tokens_are_the_words_their_sentences_use() -> None:
    """Short or long, a set is closed or it is not."""
    assert ProviderOutcome.TIMED_OUT == "timed out"
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
    assert QuotedProvider.of(None).carried() == ""
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
        lambda: QuotedProvider("cloud"),
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
    ("fragment", lambda: FromEntry(SENTINEL)),
)


@pytest.mark.parametrize("name, build", REFUSING, ids=[one for one, _ in REFUSING])
def test_a_refusal_never_repeats_the_value_it_refused(
    name: str, build: object
) -> None:
    """The rule the whole surface keeps, applied one layer earlier than
    the emitter's own refusal report keeps it. A construction refusal is
    caught by the emitter's guard and reported on the emitter's own
    channel, and the value is what that report may not carry."""
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


# --- the server channels' own values ----------------------------------


def test_a_reported_mac_is_the_header_as_the_firmware_spelled_it() -> None:
    """Looser than the canonical form in separator and case, and only
    because a header `normalize_mac` accepted is the only one that ever
    reaches the sentence that renders it."""
    assert ReportedMac("AA-BB-CC-DD-EE-FF").carried() == "AA-BB-CC-DD-EE-FF"
    assert ReportedMac("aa:bb:cc:dd:ee:ff").carried() == "aa:bb:cc:dd:ee:ff"
    with pytest.raises(EventValueError):
        ReportedMac("aabbccddeeff")


def test_an_activation_code_is_six_digits_read_off_a_screen() -> None:
    assert ActivationCode("123456").carried() == "123456"
    for refused in ("12345", "1234567", "12345a"):
        with pytest.raises(EventValueError):
            ActivationCode(refused)


def test_a_board_name_and_a_firmware_version_carry_their_own_bounds() -> None:
    """Two descriptors rather than one, because the decision sites
    truncate to two different lengths and the bound is the site's."""
    assert BoardName("waveshare-s3").carried() == "waveshare-s3"
    assert FirmwareVersion("1.8.2").carried() == "1.8.2"
    assert BoardName.BOUNDS is not None
    assert FirmwareVersion.BOUNDS is not None
    assert BoardName.BOUNDS.max_length > FirmwareVersion.BOUNDS.max_length
    with pytest.raises(EventValueError):
        FirmwareVersion("v" * (FirmwareVersion.BOUNDS.max_length + 1))
    with pytest.raises(EventValueError):
        BoardName("waveshare\nsecond line")


def test_the_descriptor_bounds_are_the_ones_the_check_in_truncates_to() -> None:
    """The event's bound and the decision site's are one number. A
    descriptor bounded here more loosely than the site truncates would
    be a claim the site does not keep."""
    from vinga_server.config.models import (
        BOARD_LIMIT,
        CLIENT_ID_LIMIT,
        FIRMWARE_LIMIT,
    )

    assert BoardName.BOUNDS == Bounds(BOARD_LIMIT)
    assert FirmwareVersion.BOUNDS == Bounds(FIRMWARE_LIMIT)
    assert ClientId.BOUNDS == Bounds(CLIENT_ID_LIMIT)


def test_session_ids_carry_a_list_and_hold_each_element_to_the_syntax() -> None:
    assert SessionIds(("alpha", "beta")).carried() == ["alpha", "beta"]
    with pytest.raises(EventValueError):
        SessionIds(("alpha", SENTINEL))
    with pytest.raises(EventValueError):
        SessionIds(["alpha"])  # type: ignore[arg-type]


def test_joined_class_names_admit_a_group_and_a_plain_one_alike() -> None:
    """A transport raises inside anyio task groups, so what a handler
    catches is a group whose own name says nothing; the site unwraps it
    to the names inside and this is the type that admits the joining."""
    assert ClassNames("TimeoutError").carried() == "TimeoutError"
    assert ClassNames("ConnectError, TimeoutError").carried() == (
        "ConnectError, TimeoutError"
    )
    assert ClassNames.JOINED is True
    assert ClassName.JOINED is False
    with pytest.raises(EventValueError):
        ClassNames("ConnectError; TimeoutError")
    with pytest.raises(EventValueError):
        ClassName("ConnectError, TimeoutError")


# --- and their closed sets, by their decision sites -------------------


def test_the_ota_refusals_are_the_whole_of_what_that_endpoint_says() -> None:
    """By identity rather than by equality: the endpoint reaches for
    these members rather than restating the sentences, so the closed set
    and the wording have one home."""
    from vinga_server.ota import reply

    assert reply.DEVICE_ID_PROBLEM is OtaRefusal.DEVICE_ID_UNREADABLE
    assert len(frozenset(OtaRefusal)) == 3


def test_the_pending_refusals_are_the_two_bounds_the_table_refuses_at() -> None:
    """Restated rather than imported, because the pending table imports
    the emitter and an import back would be a cycle. Held equal here, so
    a reworded bound fails rather than degrades."""
    from vinga_server.onboarding.pending import BUDGET_SPENT, CAPACITY_REACHED

    assert frozenset(get_args(PendingRefusal)) == frozenset(
        {CAPACITY_REACHED, BUDGET_SPENT}
    )
    assert NotOffered.PENDING_FULL == CAPACITY_REACHED
    assert NotOffered.MINT_SPENT == BUDGET_SPENT


def test_the_origin_sources_are_the_three_the_banner_can_name() -> None:
    from vinga_server.config import ServerConfig
    from vinga_server.onboarding.origin import public_origin

    assert public_origin(ServerConfig(public_url="https://vinga.example")).source == (
        OriginSource.PUBLIC_URL
    )
    assert public_origin(
        ServerConfig(websocket_url="wss://vinga.example/ws")
    ).source == OriginSource.WEBSOCKET_URL
    assert public_origin(ServerConfig()).source == OriginSource.LISTEN_ADDRESS


def test_the_mcp_down_reasons_are_the_ones_the_transport_classifies_into() -> None:
    """The enumeration and the narrowing its connect sentence declares,
    both held equal to the transport's own constants, so a seventh way
    down classified there and not declared here fails rather than
    degrades."""
    from vinga_server.tools.mcp import transport

    connect = {
        transport.TRANSPORT_FAILED,
        transport.INITIALIZE_FAILED,
        transport.DISCOVERY_FAILED,
        transport.CONNECT_TIMEOUT,
    }

    assert frozenset(McpDown) == frozenset(
        connect | {transport.STOPPED, transport.CALL_FAILED}
    )
    assert frozenset(get_args(McpConnectFailure)) == frozenset(connect)


def test_the_mcp_reload_words_are_the_ones_the_reload_answers_with() -> None:
    """Both sets held against the reload's own constants rather than
    restated beside them. The refusals are what the emit site looks up,
    so a reworded constant that only this file agreed with would leave a
    refused reload saying nothing at all."""
    from vinga_server.tools.mcp import reload

    assert frozenset(McpReloadOutcome) == frozenset({reload.APPLIED, reload.REFUSED})
    assert frozenset(McpRefusal) == frozenset(
        {
            reload.REFUSED_IN_PROGRESS,
            reload.REFUSED_BUSY,
            reload.REFUSED_UNREADABLE,
            reload.REFUSED_INVALID,
            reload.REFUSED_UNEXPECTED,
        }
    )


def test_the_remaining_server_sets_are_the_words_their_sites_write() -> None:
    """The small ones whose decision site fixes them per variant or
    reads them off configuration: a declined recording, the echo guard's
    outcome, a version-2 poll's refusal, and a configured transport.

    Restated rather than typed at the site, and for two different
    reasons. The first three are `fixed=` on their variants, so no site
    passes them at all and there is nothing to type. The fourth is the
    configuration's own `Literal`, which is its schema and its generated
    reference, not this module's to name.
    """
    assert frozenset(CaptureDeclined) == frozenset({"unusable", "min_free_mb", "open"})
    assert frozenset(EchoOutcome) == frozenset(
        {"skipped", "timed_out", "confirmed_echo", "confirmed_empty", "recovered"}
    )
    assert frozenset(ActivationRefusal) == frozenset(
        {"unreadable_body", "unknown_algorithm", "challenge_mismatch"}
    )

    from vinga_server.config.models import McpServerConfig

    assert frozenset(McpTransport) == frozenset(
        get_args(McpServerConfig.model_fields["transport"].annotation)
    )


# --- and the three sets whose decision site is typed by them ----------
#
# The stronger arrangement, where the site does not write a word at all:
# its own signature is the enumeration, so a spelling the set does not
# hold is unwritable rather than caught at emit. Asserted by identity
# against what the site really answers, which is what tells this apart
# from a test that restates the members beside them.


def test_a_refused_handshake_answers_a_member_rather_than_a_word() -> None:
    from vinga_server.ws import refusal_reason

    signature = inspect.signature(refusal_reason)

    assert signature.return_annotation == (AuthRejection | None)
    assert frozenset(AuthRejection) == frozenset({"no_token", "bad_token"})


def test_a_failed_capture_write_names_its_track_by_member() -> None:
    from vinga_server.capture import SessionCapture

    # White-box, and it is a source reading rather than a call: the
    # claim is that a closed set is named by its members at the one site
    # that passes them, and a signature's annotation is where that is
    # written. Nothing about a running capture reports which type its
    # own parameter is declared as.
    doing = inspect.signature(SessionCapture._disable).parameters["doing"]

    assert doing.annotation is CaptureWrite
    assert frozenset(CaptureWrite) == frozenset({"write audio", "write an event"})


def test_the_banners_origin_carries_a_member_rather_than_a_key_name() -> None:
    """By identity on what `public_origin` really answers, which is the
    half a signature cannot give: a dataclass annotated with the type
    still takes whatever it is handed.

    Held against the narrowing the banner's field declares rather than
    against the whole enumeration (#340). The fourth member is a line
    answering a request, which the banner is not: what it can say and
    what its field admits are one set, checked both ways here.
    """
    from vinga_server.config import ServerConfig
    from vinga_server.events.catalog import BannerOrigin
    from vinga_server.onboarding.origin import public_origin

    answered = [
        public_origin(ServerConfig(public_url="https://vinga.example")).source,
        public_origin(ServerConfig(websocket_url="wss://vinga.example/ws")).source,
        public_origin(ServerConfig()).source,
    ]

    assert [type(one) for one in answered] == [OriginSource] * 3
    assert frozenset(answered) == frozenset(get_args(BannerOrigin))
    assert OriginSource.REQUEST_HOST not in answered


def test_the_two_new_fragments_are_built_by_the_types_that_declare_them() -> None:
    assert SessionList.of(("alpha", "beta")).carried() == "alpha, beta"
    assert OriginProvenance("from server.public_url").carried() == (
        "from server.public_url"
    )
    assert OriginProvenance("guessed from the listen address").carried() == (
        "guessed from the listen address"
    )
    with pytest.raises(EventValueError):
        SessionList.of(())
    with pytest.raises(EventValueError):
        OriginProvenance("server.public_url")
