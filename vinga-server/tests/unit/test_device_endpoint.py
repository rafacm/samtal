"""The address two commands share, and the boundary they make requests
through.

`vinga-server doctor` and `vinga simulator` both stand where a board
stands. What they have in common is here, and `test_doctor.py` is the
other half of this file's coverage: every case it already had runs
unchanged against the moved code, which is the pin the extraction was
made against, so what this file adds is the two things the doctor never
needed. Composing an activation target out of an address that carries a
secret path segment and a query string, and reading a websocket URL as
somewhere a device token may actually be sent.
"""

import logging
import threading
from urllib.parse import urlsplit

import httpx
import pytest

from vinga_server import device_endpoint
from vinga_server.config.loader import ConfigError
from vinga_server.config.printing import GLIMPSE_LENGTH
from vinga_server.ota.router import ACTIVATE_SEGMENT

# Not a real credential, and shaped so a substring check for it cannot
# match by accident. The path segment in front of an OTA endpoint is the
# whole protection a deployment with onboarding turned off has, so it is
# treated here as the secret it is.
SEGMENT = "AB2C4D5E-never-a-real-path-key"

PASTED = "hunter2-never-a-real-password-9c3f"


def test_the_activation_segment_is_the_one_the_server_serves() -> None:
    """Two spellings of one fact would be a bug pending.

    The client half cannot import `ota.router`, which imports FastAPI,
    so the segment is written out in `device_endpoint` with the reason
    beside it. That makes this the assertion that keeps the two equal,
    and this file may make it because a unit test carries the whole
    install.
    """
    assert device_endpoint.ACTIVATION_SEGMENT == ACTIVATE_SEGMENT


# The address policy


@pytest.mark.parametrize(
    "url",
    [
        f"https://voice.example/x/{SEGMENT}/\n",
        f"https://voice.example/x/{SEGMENT} /",
        f"https://voice.example/x/{SEGMENT}/\t",
    ],
)
def test_an_address_carrying_a_character_a_url_cannot_hold_is_refused(url: str) -> None:
    """`urlsplit` deletes tabs, carriage returns and newlines rather than
    refusing them, so a URL carrying one parses cleanly and then reaches
    httpx, which raises naming the character and its position. This is
    where they stop, and the refusal repeats none of it."""
    with pytest.raises(ConfigError) as caught:
        device_endpoint.Endpoint.parsed(url, "the URL", device_endpoint.SUPPLIED_ENDPOINT)

    assert SEGMENT not in str(caught.value)


@pytest.mark.parametrize(
    "url",
    [
        f"ftp://voice.example/x/{SEGMENT}/",
        f"/x/{SEGMENT}/",
        f"https:///x/{SEGMENT}/",
    ],
)
def test_an_address_that_is_not_http_with_a_host_is_refused(url: str) -> None:
    with pytest.raises(ConfigError) as caught:
        device_endpoint.Endpoint.parsed(url, "the URL", device_endpoint.SUPPLIED_ENDPOINT)

    assert SEGMENT not in str(caught.value)


def test_an_address_carrying_a_credential_is_refused_without_repeating_it() -> None:
    """Anything in a URL ends up in shell history, in process lists and
    in access logs, and a refusal that quoted the address back would be
    the thing that published it."""
    with pytest.raises(ConfigError) as caught:
        device_endpoint.Endpoint.parsed(
            f"https://board:{PASTED}@voice.example/x/{SEGMENT}/",
            "the URL",
            device_endpoint.SUPPLIED_ENDPOINT,
        )

    assert PASTED not in str(caught.value)
    assert SEGMENT not in str(caught.value)


def test_a_plain_http_address_is_ordinary_here() -> None:
    """The device-facing policy, and where it differs from the
    configuration client's on purpose: a board on a LAN is pointed at
    exactly this, and the request that goes to it carries no credential
    at all."""
    endpoint = device_endpoint.Endpoint.parsed(
        "http://192.168.1.40:8003/xiaozhi/ota/", "the URL", device_endpoint.SUPPLIED_ENDPOINT
    )

    assert endpoint.given == "http://192.168.1.40:8003/xiaozhi/ota/"


def test_an_accepted_address_is_returned_exactly_as_it_was_typed() -> None:
    """Trailing slash included: the short path and the OTA path both end
    in one, and a device sends what it was handed."""
    for url in (
        f"https://voice.example/x/{SEGMENT}/",
        f"https://voice.example/x/{SEGMENT}",
    ):
        assert (
            device_endpoint.Endpoint.parsed(
                url, "the URL", device_endpoint.SUPPLIED_ENDPOINT
            ).given
            == url
        )


# Composing the activation target
#
# The case a two-string type could not have been given: the segment goes
# on the PATH, and a query string stays behind it.


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        (
            f"https://voice.example/x/{SEGMENT}/",
            f"https://voice.example/x/{SEGMENT}/{ACTIVATE_SEGMENT}",
        ),
        (
            f"https://voice.example/x/{SEGMENT}",
            f"https://voice.example/x/{SEGMENT}/{ACTIVATE_SEGMENT}",
        ),
        (
            f"https://voice.example/x/{SEGMENT}/?token=abc",
            f"https://voice.example/x/{SEGMENT}/{ACTIVATE_SEGMENT}?token=abc",
        ),
        (
            f"https://voice.example/x/{SEGMENT}/?token=abc#frag",
            f"https://voice.example/x/{SEGMENT}/{ACTIVATE_SEGMENT}?token=abc#frag",
        ),
    ],
)
def test_the_activation_target_appends_to_the_path_and_nothing_else(
    given: str, expected: str
) -> None:
    """A supplied URL may carry a query string, and `...?token=x` with
    `/activate` stuck on the end is the segment written inside the
    credential's value. That is the whole reason this is an operation on
    a parsed address."""
    endpoint = device_endpoint.Endpoint.parsed(
        given, "the URL", device_endpoint.SUPPLIED_ENDPOINT
    )

    assert endpoint.activation().given == expected


def test_the_activation_target_keeps_the_stand_in_name() -> None:
    """A composed address is still the address nothing may print."""
    endpoint = device_endpoint.Endpoint.parsed(
        f"https://voice.example/x/{SEGMENT}/", "the URL", device_endpoint.SUPPLIED_ENDPOINT
    )

    assert endpoint.activation().shown == device_endpoint.SUPPLIED_ENDPOINT


# Far-side text that hands the address back
#
# A refusal here is a fixed sentence and quotes nothing, so the few
# fields these commands DO show are the only route a supplied URL has to
# a surface. Reflecting the request target into an answer is what a
# proxy, a captive portal and an error page each do by default, which
# makes this the ordinary case rather than the adversarial one.


def _endpoint(url: str) -> device_endpoint.Endpoint:
    return device_endpoint.Endpoint.parsed(url, "the URL", device_endpoint.SUPPLIED_ENDPOINT)


@pytest.mark.parametrize(
    "reflected",
    [
        SEGMENT,
        f"/x/{SEGMENT}/",
        f"error at /x/{SEGMENT}/?token={PASTED}",
        PASTED,
        f"token={PASTED}",
        SEGMENT.upper(),
    ],
    ids=[
        "the segment alone",
        "the path whole",
        "the request target as a gateway echoes it",
        "one query value",
        "the query whole",
        "the segment lower-cased on its way back",
    ],
)
def test_far_side_text_handing_the_address_back_says_none_of_it(reflected: str) -> None:
    """Every spelling of a reflection, because a gateway echoes the
    target it did not recognize and an application echoes the one
    parameter it read."""
    said = _endpoint(f"https://voice.example/x/{SEGMENT}/?token={PASTED}").repeated(reflected)

    assert SEGMENT.lower() not in said.lower()
    assert PASTED not in said
    assert device_endpoint.WITHHELD in said


def test_the_host_is_not_redacted_out_of_what_a_screen_would_show() -> None:
    """The activation message carries the deployment's origin on
    purpose: it is the line the firmware draws for a person to type into
    a browser. Redacting it would take the answer out of the answer."""
    said = _endpoint(f"https://voice.example/x/{SEGMENT}/").repeated("voice.example\n659505")

    assert "voice.example" in said
    assert "659505" in said


def test_a_part_too_short_to_be_a_secret_is_left_alone() -> None:
    """A one or two character segment is what a path is made of rather
    than what it hides, and matching those as substrings would take a
    letter out of the middle of an ordinary word."""
    said = _endpoint("https://voice.example/x/v1/").repeated("exactly six digits: 659505")

    assert said == "exactly six digits: 659505"


def test_the_bound_and_the_control_characters_still_apply() -> None:
    """One door, so the two rules cannot be applied by halves: what a
    terminal would obey and how much of it there may be are governed
    here as much as the address is."""
    said = _endpoint("https://voice.example/xiaozhi/ota/").repeated("a\nb\x1b[2J" + "c" * 400)

    assert "\n" not in said
    assert "\x1b" not in said
    assert len(said) <= GLIMPSE_LENGTH


def test_the_activation_target_withholds_what_it_was_composed_from() -> None:
    """A composed address is the supplied one with a segment on the end,
    so a poll's answer is read as carefully as a check-in's."""
    polled = _endpoint(f"https://voice.example/x/{SEGMENT}/?token={PASTED}").activation()

    said = polled.repeated(f"/x/{SEGMENT}/{ACTIVATE_SEGMENT}?token={PASTED}")

    assert SEGMENT not in said
    assert PASTED not in said


# Reading a websocket URL a reply named
#
# Two readings of one string, and the difference between them is the
# whole reason there are two: a diagnosis reports where a device would be
# sent and never goes there, and a client is about to send a device token
# to it.


def test_a_diagnosis_reports_a_credentialled_websocket_url_with_it_taken_out() -> None:
    """The doctor's reading, which is unchanged by the move: such a URL
    is a fact worth reporting, and the report is not what publishes the
    credential."""
    read = device_endpoint.reported_websocket(
        f"wss://board:{PASTED}@voice.example/xiaozhi/v1/"
    )

    assert read is not None
    scheme, shown = read
    assert scheme == "wss"
    assert PASTED not in shown


def test_a_client_refuses_a_websocket_url_carrying_a_credential() -> None:
    """The client's reading, stricter by exactly the rule that is about a
    credential: connecting anyway would be the thing that published it."""
    assert (
        device_endpoint.websocket_target(
            f"wss://board:{PASTED}@voice.example/xiaozhi/v1/", "https"
        )
        is None
    )


def test_a_client_refuses_a_plain_websocket_url_from_behind_tls() -> None:
    """The TLS-proxy misconfiguration, read as a rule about a credential:
    a device token crossing a plain socket from behind TLS is the mistake
    the configuration client has no flag to make."""
    assert device_endpoint.websocket_target("ws://voice.example/xiaozhi/v1/", "https") is None


def test_a_plain_websocket_url_from_a_plain_endpoint_is_the_ordinary_lan_case() -> None:
    assert (
        device_endpoint.websocket_target("ws://192.168.1.40:8003/xiaozhi/v1/", "http")
        == "ws://192.168.1.40:8003/xiaozhi/v1/"
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://voice.example/xiaozhi/v1/",
        "wss:///xiaozhi/v1/",
        "wss://voice.example:notaport/xiaozhi/v1/",
        "not a url at all",
        "",
    ],
)
def test_a_websocket_url_this_client_cannot_read_reaches_neither_reader(url: str) -> None:
    """There is deliberately no fallback to the raw string. A URL that
    will not parse is exactly the one whose credential could not be taken
    off."""
    assert device_endpoint.reported_websocket(url) is None
    assert device_endpoint.websocket_target(url, "https") is None


def test_the_scheme_a_reader_answers_with_is_the_parser_s_normalized_one() -> None:
    """A comparison against the literal `ws://` is one a `WS://` walks
    past."""
    read = device_endpoint.reported_websocket("WSS://voice.example/xiaozhi/v1/")

    assert read is not None
    assert read[0] == "wss"
    assert urlsplit("WSS://voice.example/xiaozhi/v1/").scheme == "wss"


def test_the_downgrade_rule_is_about_those_two_schemes_and_no_others() -> None:
    assert device_endpoint.downgraded("https", "ws")
    assert not device_endpoint.downgraded("https", "wss")
    assert not device_endpoint.downgraded("http", "ws")
    assert not device_endpoint.downgraded("http", "wss")


# The request boundary's logging, when two of them overlap
#
# The quieting is process state, and a request is the span it has to
# cover. Two of them at once is not hypothetical here: the live lane
# runs this client in the same process as a uvicorn that makes requests
# of its own, and `simulator run` will hold a socket open while a poll
# goes out beside it.


class _Held:
    """A far side that answers only when a case says so, and records
    which URL it was asked for."""

    def __init__(self, entered: threading.Event, released: threading.Event) -> None:
        self.entered = entered
        self.released = released

    def client(self, url: str) -> httpx.Client:
        return httpx.Client(base_url=url, transport=httpx.MockTransport(self._handle))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.entered.set()
        assert self.released.wait(timeout=10)
        return httpx.Response(200, json={})


def _requesting(url: str, far_side: _Held, failed: list[BaseException]) -> threading.Thread:
    def request() -> None:
        try:
            device_endpoint.requested("GET", _endpoint(url), build=far_side.client)
        except BaseException as exc:  # pragma: no cover - a failure is the assertion
            failed.append(exc)

    return threading.Thread(target=request, daemon=True)


def test_two_overlapping_requests_neither_unquiet_the_other(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The interleaving the levels are process state for.

    Unserialized, the pair undoes itself in the direction that matters:
    the first in saves the loud level, the second saves the quiet one,
    and the first OUT restores the loud level underneath a request the
    second is still making, so the library writes that request's URL
    into a record after all. The pair then finishes on the level the
    second saved, which is the wrong one the other way.

    Both halves are asserted, because either alone would pass with the
    other broken: no record carries either sentinel, and both loggers
    end at exactly the level they started at.
    """
    first = f"https://voice.example/x/{SEGMENT}-one/"
    second = f"https://voice.example/x/{SEGMENT}-two/"
    loggers = [logging.getLogger(name) for name in device_endpoint.REQUEST_LOGGERS]
    was = [logger.level for logger in loggers]
    failed: list[BaseException] = []
    inside, release = threading.Event(), threading.Event()
    waiting, let_go = threading.Event(), threading.Event()
    try:
        for logger in loggers:
            logger.setLevel(logging.INFO)
        with caplog.at_level(logging.DEBUG):
            caplog.clear()
            ahead = _requesting(first, _Held(inside, release), failed)
            behind = _requesting(second, _Held(waiting, let_go), failed)
            ahead.start()
            assert inside.wait(timeout=10)
            behind.start()
            # The second cannot be inside the boundary while the first
            # is: this is the wait that would let it in without the
            # lock, and it is what makes the case an interleaving rather
            # than two requests in a row.
            assert not waiting.wait(timeout=0.25)
            release.set()
            ahead.join(timeout=10)
            let_go.set()
            behind.join(timeout=10)
        written = "\n".join(
            f"{record.getMessage()}\n{record.args!r}" for record in caplog.records
        )

        assert failed == []
        assert f"{SEGMENT}-one" not in written
        assert f"{SEGMENT}-two" not in written
        assert [logger.level for logger in loggers] == [logging.INFO] * len(loggers)
    finally:
        for logger, level in zip(loggers, was, strict=True):
            logger.setLevel(level)
