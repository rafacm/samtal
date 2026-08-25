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

from urllib.parse import urlsplit

import pytest

from vinga_server import device_endpoint
from vinga_server.config.loader import ConfigError
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
