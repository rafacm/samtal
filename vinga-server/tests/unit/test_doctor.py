"""The `vinga-server doctor` command: what answers on an OTA URL.

Driven through `doctor.main()`, which since #244 is the whole entry
point of a command of its own; the tests at the foot of this file are
what pin the word that reaches it. On the other side it meets a canned
endpoint behind this module's client seam, plus one case against the
real describe handler so that a change to what that handler prints
cannot pass unnoticed.

The hostile cases are the other half. What this command reaches may be a
proxy, a captive portal or anything else that answers, so its body, the
URL it names and the version it claims are text nobody vouched for, and
the sentinel assertions here are the M1 round's no-leak discipline
applied to a reader that did not exist then: a terminal. A URL an
operator typed is the same kind of text from the other direction, since
it may be the deployment's own secret `ota_path`, and that is why the
usage errors and the entry point's own refusals are asserted to repeat
nothing of what was typed.

The environment fixture and the vectors below are this file's own copy
of the `ota-url` suite's, duplicated rather than shared when the two
commands came apart: they are a handful of lines, and a support module
holding them would be a name nobody could place for two suites that no
longer share a command.
"""

import contextlib
import http.server
import json
import logging
import subprocess
import sys
import threading
from collections.abc import Mapping
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient

from vinga_server import __version__, doctor
from vinga_server.app import create_app
from vinga_server.config import Config
from vinga_server.ota import OTA_PATH

# Not a real secret: fixed, so the key below is a vector rather than
# something these tests recompute with the code under test. The same
# pair `test_onboarding.py` uses.
SECRET = "a-fixed-secret-for-the-vector"

KEY = "NUGFZQ2Y"

AUTH_SECRET_ENV = "VINGA_AUTH_SECRET"

API_SECRET_ENV = "VINGA_API_SECRET"

# Not a real credential, and shaped so a substring check for it cannot
# match by accident. It arrives from the far end, which is what makes it
# different from every other sentinel in this suite.
PASTED = "hunter2-never-a-real-password-9c3f"

DESCRIBE = (
    "vinga-server 9.9.9 (revision sha-3f9362a) OTA endpoint.\n"
    "Devices are sent to {websocket} (protocol version 1).\n"
    "Type this into the device's captive portal: {url} (from server.public_url)\n"
)


@pytest.fixture(autouse=True)
def _environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A machine with the device-auth secret and nothing else: no
    config file, no API token, and a database directory that does not
    exist. This command may need none of them."""
    monkeypatch.delenv("VINGA_CONFIG", raising=False)
    monkeypatch.delenv(API_SECRET_ENV, raising=False)
    monkeypatch.setenv(AUTH_SECRET_ENV, SECRET)


def _config_file(tmp_path: Path, body: str) -> str:
    path = tmp_path / "config.yaml"
    path.write_text(body, encoding="utf-8")
    return str(path)


def _chain(exc: BaseException) -> str:
    """Everything an exception carries, including what a chain walker
    would find behind it."""
    parts: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        parts += [repr(current), str(current)]
        current = current.__cause__ or current.__context__
    return "\n".join(parts)


# What answers on it


def _endpoint(
    body: str, status: int = 200, media_type: str = "text/plain; charset=utf-8"
) -> tuple[FastAPI, list[Request]]:
    """One canned address, and what it was asked."""
    app = FastAPI()
    seen: list[Request] = []

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
    async def answer(request: Request) -> Response:
        seen.append(request)
        return Response(body, status_code=status, media_type=media_type)

    return app, seen


@pytest.fixture
def endpoint(monkeypatch: pytest.MonkeyPatch):
    """Put a canned endpoint behind the client seam, and keep what
    reached it.

    The factory mirrors `build_client` exactly, which since #244 means
    one argument and no way to be handed a credential: whether an
    Authorization header reaches the far side is one of the things
    asserted here, and a factory free to add one of its own would have
    made that assertion vacuous.
    """

    def _serve(body: str, status: int = 200, media_type: str = "text/plain; charset=utf-8"):
        app, seen = _endpoint(body, status, media_type)

        def factory(url: str) -> TestClient:
            return TestClient(app, base_url=url)

        monkeypatch.setattr(doctor, "build_client", factory)
        return seen

    return _serve


def test_a_healthy_endpoint_is_reported_with_what_a_device_is_handed(
    endpoint, capsys: pytest.CaptureFixture[str]
) -> None:
    seen = endpoint(
        DESCRIBE.format(websocket="wss://voice.example/xiaozhi/v1/", url="https://voice.example")
    )

    assert doctor.main(["https://voice.example/x/ABCDEFGH/"]) == 0

    printed = capsys.readouterr().out
    assert "vinga-server 9.9.9" in printed
    assert "wss://voice.example/xiaozhi/v1/" in printed
    assert "protocol version 1" in printed
    assert [request.method for request in seen] == ["GET"]


def test_the_real_describe_body_is_recognized(
    endpoint, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The drift guard. What `doctor` reads is not a shared format
    string but the endpoint's printed answer, so the patterns are run
    against the real handler's output rather than against a copy of it.
    """
    monkeypatch.setenv(API_SECRET_ENV, "test-api-token-" + "0123456789abcdef" * 2)
    with TestClient(create_app(Config())) as client:
        body = client.get(OTA_PATH).text
    endpoint(body)

    assert doctor.main(["http://127.0.0.1:8003" + OTA_PATH]) == 0

    assert f"vinga-server {__version__}" in capsys.readouterr().out


def test_a_slash_an_older_server_would_redirect_is_refused_too(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A server older than the 2026-08-13 checkpoint answered a missing
    trailing slash with Starlette's own 307, issued before any handler
    runs, and this command used to follow that one shape. It follows
    none now: a current server answers both spellings itself (the test
    below), so what sends this redirect is something other than a
    deployment this release supports, and where it points is that
    something's choice.

    The canned app registers only the slashed route, which is exactly
    what those servers were, so the 307 here is the framework's own
    rather than one written by hand."""
    app = FastAPI()

    @app.get("/x/ABCDEFGH/")
    async def described() -> Response:
        return Response(
            DESCRIBE.format(
                websocket="wss://voice.example/xiaozhi/v1/", url="https://voice.example"
            ),
            media_type="text/plain",
        )

    monkeypatch.setattr(
        doctor,
        "build_client",
        lambda url: TestClient(app, base_url=url),
    )

    assert doctor.main(["https://voice.example/x/ABCDEFGH"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "does not follow" in captured.err
    assert "Traceback" not in captured.err


def test_a_current_server_answers_both_spellings_with_no_redirect(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """What the checkpoint of 2026-08-13 changed underneath this
    command: every device-facing route now serves both spellings of its
    path itself, because the firmware does not follow a redirect on that
    request. So a probe of a current server never meets one, whichever
    way the URL was typed.

    Asserted against the real routers rather than a canned app, since
    their behavior is why this command needs to follow no redirect at
    all: the test above is what it answers one with instead."""
    monkeypatch.setenv(API_SECRET_ENV, "test-api-token-" + "0123456789abcdef" * 2)
    app = create_app(Config())
    # Probed while it is up, which is what "a current server" means and
    # is now the only way to probe one: the composition lives for as long
    # as the lifespan that built it, so an application past its teardown
    # has no more to answer an OTA check-in with than one that never
    # started.
    with TestClient(app) as client:
        for path in (f"/x/{KEY}", f"/x/{KEY}/"):
            assert client.get(path, follow_redirects=False).status_code == 200, path

        monkeypatch.setattr(
            doctor,
            "build_client",
            lambda url: TestClient(app, base_url=url),
        )

        assert doctor.main([f"http://192.168.1.10:8003/x/{KEY}"]) == 0

        assert f"vinga-server {__version__}" in capsys.readouterr().out


def test_no_bearer_token_is_sent_to_a_device_facing_address(
    endpoint, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The API's token grants everything the API can do, and this
    request goes wherever an operator typed. The OTA endpoint is the
    token issuer, so it cannot require one either."""
    monkeypatch.setenv(API_SECRET_ENV, "test-api-token-" + "0123456789abcdef" * 2)
    seen = endpoint(
        DESCRIBE.format(websocket="wss://voice.example/xiaozhi/v1/", url="https://voice.example")
    )

    assert doctor.main(["https://voice.example/x/ABCDEFGH/"]) == 0

    assert "authorization" not in {name.lower() for name in seen[0].headers}


def test_something_else_answering_there_is_named_without_quoting_it(
    endpoint, capsys: pytest.CaptureFixture[str]
) -> None:
    """The status code and a fixed sentence. What answers at an address
    a device was pointed at may be a proxy, a captive portal or a cloud
    metadata endpoint, and a bounded prefix of one of those is still
    whatever its first line holds."""
    endpoint(
        f"<html><body>Sign in to the guest network, token={PASTED}</body></html>",
        media_type="text/html",
    )

    assert doctor.main(["http://192.168.1.1/x/ABCDEFGH/"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "not as a vinga-server OTA endpoint" in captured.err
    assert doctor.UNRECOGNIZED_ANSWER in captured.err
    assert PASTED not in captured.err
    assert "Sign in to the guest network" not in captured.err
    assert "<html>" not in captured.err
    assert "Traceback" not in captured.err


def test_half_an_answer_is_not_this_endpoint(
    endpoint, capsys: pytest.CaptureFixture[str]
) -> None:
    """Both lines or neither: an address that names this server and says
    nothing about where devices go is not answering as this endpoint,
    however it came to say the first line."""
    endpoint("vinga-server 9.9.9 (revision sha-3f9362a) OTA endpoint.\n")

    assert doctor.main(["https://voice.example/x/ABCDEFGH/"]) == 1

    assert "not as a vinga-server OTA endpoint" in capsys.readouterr().err


def test_a_plain_websocket_url_behind_tls_is_the_named_misconfiguration(
    endpoint, capsys: pytest.CaptureFixture[str]
) -> None:
    """The mistake that costs the most time: TLS ends at the proxy, the
    server sees plain HTTP, and the URL it derives says ws://. Nothing
    else looks wrong."""
    endpoint(
        DESCRIBE.format(websocket="ws://voice.example/xiaozhi/v1/", url="https://voice.example")
    )

    assert doctor.main(["https://voice.example/x/ABCDEFGH/"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "server.websocket_url" in captured.err
    assert "wss://" in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.parametrize(
    ("what", "probe", "websocket"),
    [
        ("the probe's scheme upper-cased", "HTTPS", "ws"),
        ("the reported scheme upper-cased", "https", "WS"),
        ("both upper-cased", "HTTPS", "WS"),
        ("mixed case on both", "Https", "Ws"),
    ],
)
def test_the_tls_verdict_compares_schemes_rather_than_prefixes(
    endpoint, capsys: pytest.CaptureFixture[str], what: str, probe: str, websocket: str
) -> None:
    """A scheme is case-insensitive, so `startswith("https://")` on what
    an operator typed and `startswith("ws://")` on what a server printed
    were two ways for the misconfiguration this command exists to catch
    to be reported as healthy."""
    endpoint(
        DESCRIBE.format(
            websocket=f"{websocket}://voice.example/xiaozhi/v1/", url="https://voice.example"
        )
    )

    assert doctor.main([f"{probe}://voice.example/x/ABCDEFGH/"]) == 1, what

    assert "server.websocket_url" in capsys.readouterr().err, what


def test_an_upper_case_secure_websocket_url_is_still_healthy(
    endpoint, capsys: pytest.CaptureFixture[str]
) -> None:
    """The other direction of the same normalization: a `WSS://` behind
    TLS is right, and must not be read as the fault."""
    endpoint(
        DESCRIBE.format(websocket="WSS://voice.example/xiaozhi/v1/", url="https://voice.example")
    )

    assert doctor.main(["HTTPS://voice.example/x/ABCDEFGH/"]) == 0

    assert "voice.example/xiaozhi/v1/" in capsys.readouterr().out


def test_a_plain_websocket_url_behind_plain_http_is_healthy(
    endpoint, capsys: pytest.CaptureFixture[str]
) -> None:
    """The LAN deployment, which is the ordinary one: no TLS anywhere,
    so ws:// is exactly right and must not be reported as a fault."""
    endpoint(
        DESCRIBE.format(
            websocket="ws://192.168.1.10:8003/xiaozhi/v1/", url="http://192.168.1.10:8003"
        )
    )

    assert doctor.main(["http://192.168.1.10:8003/x/ABCDEFGH/"]) == 0

    assert "ws://192.168.1.10:8003/xiaozhi/v1/" in capsys.readouterr().out


def test_an_address_nothing_answers_on_says_so(capsys: pytest.CaptureFixture[str]) -> None:
    """The real client against a port nothing is listening on: the one
    case a canned endpoint cannot show."""
    assert doctor.main(["http://127.0.0.1:1/x/ABCDEFGH/"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert f"cannot reach {doctor.SUPPLIED_ENDPOINT}" in captured.err
    assert "ConnectError" in captured.err
    assert "Traceback" not in captured.err


def test_the_derived_url_is_what_is_checked_when_none_is_given(
    endpoint, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _config_file(tmp_path, "server:\n  public_url: https://voice.example\n")
    seen = endpoint(
        DESCRIBE.format(websocket="wss://voice.example/xiaozhi/v1/", url="https://voice.example")
    )

    assert doctor.main(["--config", path]) == 0

    assert seen[0].url.path == f"/x/{KEY}/"
    assert f"https://voice.example/x/{KEY}/ is vinga-server" in capsys.readouterr().out


def test_with_onboarding_off_and_no_url_it_asks_for_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _config_file(tmp_path, "server:\n  onboarding:\n    enabled: false\n")

    assert doctor.main(["--config", path]) == 1

    err = capsys.readouterr().err
    assert "device onboarding is off" in err
    assert "vinga-server doctor URL" in err


def test_a_url_carrying_a_credential_is_refused_without_repeating_it(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert doctor.main([f"https://user:{PASTED}@voice.example/x/ABCDEFGH/"]) == 1

    captured = capsys.readouterr()
    assert "username or a password" in captured.err
    assert PASTED not in captured.err
    assert "Traceback" not in captured.err


def test_a_url_that_cannot_be_read_is_refused_inside_the_boundary(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`urlsplit` raises on a malformed IPv6 literal and `.port` raises
    on a port that is not a number, and both carry the text they
    refused."""
    assert doctor.main([f"http://voice.example:{PASTED}/x/ABCDEFGH/"]) == 1

    captured = capsys.readouterr()
    assert PASTED not in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.parametrize(
    ("what", "url"),
    [
        ("a newline", f"https://voice.example/xiaozhi/ota/{{}}\n{PASTED}/"),
        ("a carriage return", f"https://voice.example/xiaozhi/ota/{{}}\r{PASTED}/"),
        ("a tab", f"https://voice.example/xiaozhi/ota/{{}}\t{PASTED}/"),
        ("a null byte", f"https://voice.example/xiaozhi/ota/{{}}\x00{PASTED}/"),
        ("an escape sequence", f"https://voice.example/x/\x1b[2K{PASTED}/"),
        ("a space", f"https://voice.example/x/AB CD{PASTED}/"),
    ],
)
def test_a_url_carrying_a_control_character_is_refused_not_raised(
    capsys: pytest.CaptureFixture[str], what: str, url: str
) -> None:
    """`urlsplit` deletes tabs, carriage returns and newlines instead of
    refusing them, so a URL carrying one parses cleanly and then reaches
    httpx, whose InvalidURL is not an HTTPError and was not caught: the
    command exited with a traceback quoting the address. Now it is a
    sentence."""
    assert doctor.main([url.format("")]) == 1, what

    captured = capsys.readouterr()
    assert captured.out == "", what
    assert PASTED not in captured.err, what
    assert "\x1b" not in captured.err, what
    assert "Traceback" not in captured.err, what
    # One line, whatever the URL tried to put in it.
    assert captured.err.count("\n") == 1, what


def test_the_url_refusals_carry_no_library_exception() -> None:
    """The chain, not just the message: httpx's InvalidURL quotes the
    character it refused and its position, and anything that walks a
    chain reads what it holds."""
    # The one reach-in in this file, resolved rather than carried over
    # when #244 moved it here. What is under test is the exception's
    # CHAIN, and a chain is not printed: `main()` consumes the exception
    # and prints one sanitized line, which the tests beside this one
    # assert, so the property is not observable through the entry point
    # at all. A __cause__ or a __context__ still holding the library's
    # own exception is reachable only from where the refusal is raised,
    # and anything that renders a traceback would find it there.
    # Promoting `_device_url` to a public name to avoid the underscore
    # would create an interface whose only caller is this test, which is
    # the worse of the two trades.
    with pytest.raises(doctor.ConfigError) as caught:
        doctor._device_url(f"https://voice.example/x/AB\n{PASTED}/", "the URL given to doctor")  # noqa: SLF001

    assert PASTED not in _chain(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_a_client_that_will_not_be_built_is_a_sentence_too(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The boundary covers building the client, not only sending
    through it: httpx validates a URL when it is handed one, and a
    refusal there is the same failure to a person."""

    def refuse(url: str) -> httpx.Client:
        raise httpx.InvalidURL(f"Invalid character in URL {PASTED}")

    monkeypatch.setattr(doctor, "build_client", refuse)

    assert doctor.main(["https://voice.example/x/ABCDEFGH/"]) == 1

    captured = capsys.readouterr()
    assert "InvalidURL" in captured.err
    assert PASTED not in captured.err
    assert "Traceback" not in captured.err


def test_a_scheme_no_device_speaks_is_refused(capsys: pytest.CaptureFixture[str]) -> None:
    assert doctor.main(["wss://voice.example/xiaozhi/v1/"]) == 1

    assert "http:// or https:// URL" in capsys.readouterr().err


# Where a redirect may send this


@pytest.fixture
def redirecting(monkeypatch: pytest.MonkeyPatch):
    """An endpoint that answers some paths with a redirect of its own
    choosing, and the describe body everywhere else."""

    def _serve(locations: Mapping[str, str]):
        app = FastAPI()
        seen: list[Request] = []

        @app.get("/{path:path}")
        async def answer(request: Request) -> Response:
            seen.append(request)
            location = locations.get(request.url.path)
            if location is not None:
                return Response(status_code=307, headers={"location": location})
            return Response(
                DESCRIBE.format(
                    websocket="wss://voice.example/xiaozhi/v1/", url="https://voice.example"
                ),
                media_type="text/plain",
            )

        monkeypatch.setattr(
            doctor,
            "build_client",
            lambda url: TestClient(app, base_url=url),
        )
        return seen

    return _serve


def test_the_canonical_trailing_slash_redirect_is_refused_as_well(
    redirecting, capsys: pytest.CaptureFixture[str]
) -> None:
    """The redirect this used to follow, and the last one to stop being
    special: the missing trailing slash a deployment older than the
    2026-08-13 checkpoint canonicalized for itself. One request goes
    out, and the second address is never asked."""
    seen = redirecting({"/x/ABCDEFGH": "https://voice.example/x/ABCDEFGH/"})

    assert doctor.main(["https://voice.example/x/ABCDEFGH"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "does not follow" in captured.err
    assert [request.url.path for request in seen] == ["/x/ABCDEFGH"]


@pytest.mark.parametrize(
    ("what", "location"),
    [
        ("another host", "https://metadata.example/latest/meta-data/"),
        ("another port", "https://voice.example:9999/x/ABCDEFGH/"),
        ("another scheme", "http://voice.example/x/ABCDEFGH/"),
        ("another path on the same host", "https://voice.example/somewhere-else/"),
        ("a path that is not the canonical one", "https://voice.example/x/OTHERKEY/"),
        ("no Location at all", ""),
    ],
)
def test_every_other_redirect_is_refused_without_naming_the_target(
    redirecting, capsys: pytest.CaptureFixture[str], what: str, location: str
) -> None:
    """A redirect is the far end choosing where this request goes next,
    and this command runs inside the network a deployment sits in."""
    seen = redirecting({"/x/ABCDEFGH": location})

    assert doctor.main(["https://voice.example/x/ABCDEFGH"]) == 1, what

    captured = capsys.readouterr()
    assert captured.out == "", what
    assert "does not follow" in captured.err, what
    assert "metadata.example" not in captured.err, what
    assert "somewhere-else" not in captured.err, what
    assert "OTHERKEY" not in captured.err, what
    assert "Traceback" not in captured.err, what
    # And it was never sent: the refusal is in front of the second
    # request, not after it.
    assert [request.url.path for request in seen] == ["/x/ABCDEFGH"], what


# A URL somebody passed is never displayed
#
# The derived short URL is the one URL these commands print: its key is
# a deployment-scoped path segment, deliberately shown so a typo
# diagnoses itself. A URL passed as an argument is a different thing.
# The documented way to check a deployment with onboarding turned off is
# to pass the legacy `ota_path` URL, and that segment is the whole
# protection its OTA endpoint has, so no verdict may echo it, whether it
# succeeded, failed, or never connected.

SECRET_SEGMENT = "8f3a9c2b1d4e5f60"

LEGACY_URL = f"https://voice.example/xiaozhi/ota/{SECRET_SEGMENT}/"


@pytest.mark.parametrize(
    ("verdict", "body", "code"),
    [
        (
            "healthy",
            DESCRIBE.format(
                websocket="wss://voice.example/xiaozhi/v1/", url="https://voice.example"
            ),
            0,
        ),
        ("not vinga-server", "<html>Sign in to the guest network</html>", 1),
        (
            "a plain websocket URL behind TLS",
            DESCRIBE.format(
                websocket="ws://voice.example/xiaozhi/v1/", url="https://voice.example"
            ),
            1,
        ),
    ],
)
def test_no_verdict_repeats_a_supplied_url(
    endpoint, capsys: pytest.CaptureFixture[str], verdict: str, body: str, code: int
) -> None:
    endpoint(body)

    assert doctor.main([LEGACY_URL]) == code, verdict

    captured = capsys.readouterr()
    assert SECRET_SEGMENT not in captured.out, verdict
    assert SECRET_SEGMENT not in captured.err, verdict
    assert doctor.SUPPLIED_ENDPOINT in captured.out + captured.err, verdict


def test_an_unreachable_supplied_url_is_not_repeated_either(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The fourth verdict, which no canned endpoint can produce: a real
    connection to a port nothing is listening on."""
    assert doctor.main([f"http://127.0.0.1:1/xiaozhi/ota/{SECRET_SEGMENT}/"]) == 1

    captured = capsys.readouterr()
    assert SECRET_SEGMENT not in captured.err
    assert "127.0.0.1" not in captured.err
    assert doctor.SUPPLIED_ENDPOINT in captured.err


def test_a_refused_supplied_url_is_not_repeated_either(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The refusals in front of the request are the other half: the
    address is not quoted back even with its userinfo taken off, since
    what is left still holds the segment."""
    credentialed = f"https://user:{PASTED}@voice.example/xiaozhi/ota/{SECRET_SEGMENT}/"
    assert doctor.main([credentialed]) == 1
    first = capsys.readouterr()

    assert doctor.main([f"ftp://voice.example/xiaozhi/ota/{SECRET_SEGMENT}/"]) == 1
    second = capsys.readouterr()

    for captured in (first, second):
        assert SECRET_SEGMENT not in captured.err
        assert PASTED not in captured.err
        assert captured.out == ""


def test_a_redirect_repeats_neither_its_target_nor_the_supplied_url(
    redirecting, capsys: pytest.CaptureFixture[str]
) -> None:
    """The fifth verdict, and the one with two secrets in it at once:
    the address that was asked holds the deployment's own path segment,
    and the target it was answered with arrives from the far end, which
    is what makes it text nobody vouched for. The refusal names the
    endpoint the way every other verdict does and repeats neither."""
    redirecting({f"/xiaozhi/ota/{SECRET_SEGMENT}": f"https://voice.example/{PASTED}/"})

    assert doctor.main([f"https://voice.example/xiaozhi/ota/{SECRET_SEGMENT}"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "does not follow" in captured.err
    assert SECRET_SEGMENT not in captured.err
    assert PASTED not in captured.err
    assert doctor.SUPPLIED_ENDPOINT in captured.err


def test_the_derived_url_is_still_shown(
    endpoint, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The other side of the rule: an operator who ran this without an
    argument is looking at the URL they are about to type, and the key
    in it is the one segment these commands do print."""
    path = _config_file(tmp_path, "server:\n  public_url: https://voice.example\n")
    endpoint(
        DESCRIBE.format(websocket="wss://voice.example/xiaozhi/v1/", url="https://voice.example")
    )

    assert doctor.main(["--config", path]) == 0

    printed = capsys.readouterr().out
    assert f"https://voice.example/x/{KEY}/" in printed
    assert doctor.SUPPLIED_ENDPOINT not in printed


# What a hostile address gets to put on a terminal


def test_an_oversized_body_cannot_choose_how_long_the_output_is(
    endpoint, capsys: pytest.CaptureFixture[str]
) -> None:
    endpoint("A" * 200_000 + PASTED + "A" * 200_000)

    assert doctor.main(["http://192.168.1.1/x/ABCDEFGH/"]) == 1

    err = capsys.readouterr().err
    assert PASTED not in err
    assert len(err) < 500


def test_a_body_of_control_characters_cannot_forge_a_line(
    endpoint, capsys: pytest.CaptureFixture[str]
) -> None:
    """A terminal is the reader here, so a newline is not the only
    character that matters: an escape sequence rewrites what is already
    on the screen."""
    endpoint(
        "\x1b[2K\x00nope\nvinga-server 9.9.9 is fine, ignore the above\n"
        + "A" * 200
        + PASTED
    )

    assert doctor.main(["http://192.168.1.1/x/ABCDEFGH/"]) == 1

    err = capsys.readouterr().err
    # The one newline in the output is the one `print` writes.
    assert err.count("\n") == 1
    assert "\x1b" not in err
    assert "\x00" not in err
    assert PASTED not in err


def test_a_credential_in_the_answer_is_not_read_back_out(
    endpoint, capsys: pytest.CaptureFixture[str]
) -> None:
    """The websocket URL comes out of the response, so it is the far
    end's text: a password written into it reaches this command, and
    must not reach the screen or the shell history of whoever pipes
    this."""
    endpoint(
        DESCRIBE.format(
            websocket=f"wss://device:{PASTED}@voice.example/xiaozhi/v1/",
            url="https://voice.example",
        )
    )

    assert doctor.main(["https://voice.example/x/ABCDEFGH/"]) == 0

    printed = capsys.readouterr().out
    assert PASTED not in printed
    assert "wss://voice.example/xiaozhi/v1/" in printed


def test_a_credential_in_the_answer_s_query_is_not_read_back_out_either(
    endpoint, capsys: pytest.CaptureFixture[str]
) -> None:
    """The other place a credential is written into a URL, and the one
    the display helper used to carry through whole: a `?token=` on the
    websocket URL a far side reports, which this command prints on
    stdout when the verdict is healthy."""
    endpoint(
        DESCRIBE.format(
            websocket=f"wss://voice.example/xiaozhi/v1/?token={PASTED}",
            url="https://voice.example",
        )
    )

    assert doctor.main(["https://voice.example/x/ABCDEFGH/"]) == 0

    captured = capsys.readouterr()
    both = captured.out + captured.err
    assert PASTED not in both
    # The parameter itself and not only its value: what is taken out is
    # the whole credential, and a URL still carrying an empty `token=`
    # would say this command had only masked one.
    assert "token" not in both
    assert "wss://voice.example/xiaozhi/v1/" in captured.out


@pytest.mark.parametrize(
    ("what", "websocket"),
    [
        ("an unclosed IPv6 literal", f"wss://user:{PASTED}@[::"),
        ("a malformed IPv6 literal", f"wss://[bad::{PASTED}::x]:8443/xiaozhi/v1/"),
        ("a port that is not a number", f"wss://voice.example:{PASTED}/xiaozhi/v1/"),
        ("a port out of range", f"wss://voice.example:99999{PASTED}/xiaozhi/v1/"),
        ("no scheme a device speaks", f"https://user:{PASTED}@voice.example/xiaozhi/v1/"),
        ("no host at all", f"wss:///xiaozhi/v1/{PASTED}"),
    ],
)
def test_a_websocket_url_that_cannot_be_read_is_a_failure_not_a_fallback(
    endpoint, capsys: pytest.CaptureFixture[str], what: str, websocket: str
) -> None:
    """The URL that will not parse is exactly the one whose userinfo
    could not be taken off, so printing the raw string as a fallback
    published the credential in the one case the stripping exists for.
    It is now a verdict of its own, and it quotes nothing."""
    endpoint(DESCRIBE.format(websocket=websocket, url="https://voice.example"))

    assert doctor.main(["https://voice.example/x/ABCDEFGH/"]) == 1, what

    captured = capsys.readouterr()
    assert captured.out == "", what
    assert PASTED not in captured.err, what
    assert "server.websocket_url" in captured.err, what
    assert "Traceback" not in captured.err, what


def test_a_response_that_is_not_text_at_all_is_handled(
    endpoint, capsys: pytest.CaptureFixture[str]
) -> None:
    endpoint("", status=204)

    assert doctor.main(["http://192.168.1.1/x/ABCDEFGH/"]) == 1

    err = capsys.readouterr().err
    assert "answered 204" in err
    assert doctor.UNRECOGNIZED_ANSWER in err


def test_doctor_is_a_get_and_only_a_get(endpoint) -> None:
    """A POST is a device's check-in, which mints an activation code for
    an unbound MAC and spends part of the mint budget. A diagnosis
    nobody could run twice would be no diagnosis."""
    seen = endpoint("nothing here")

    doctor.main(["http://192.168.1.1/x/ABCDEFGH/"])

    assert {request.method for request in seen} == {"GET"}


def test_the_seam_cannot_carry_a_credential_and_takes_its_own_timeouts() -> None:
    """The seam itself, and the whole of what it promises.

    There is no token parameter to leave unset, which is what makes "no
    Authorization header" a property of the seam rather than of every
    call through it: this request goes wherever an operator typed, and
    the API's bearer token grants everything the API can do.

    The timeouts are this command's own since #244 rather than the API
    client's, so they are asserted against this module's constants: a
    bounded connect, and a read generous enough for the slow network
    that is often what is being diagnosed."""
    client = doctor.build_client("https://voice.example/x/ABCDEFGH/")
    try:
        assert isinstance(client, httpx.Client)
        assert "authorization" not in {name.lower() for name in client.headers}
        assert client.timeout.connect == doctor.CONNECT_TIMEOUT_S
        assert client.timeout.read == doctor.READ_TIMEOUT_S
    finally:
        client.close()


class _Unclosable(TestClient):
    """A client whose close will not complete, and says something with a
    credential in it on the way out. A driver is free to put anything in
    such a message, which is why none of it may be repeated."""

    def close(self) -> None:
        raise OSError(f"the socket would not go: {PASTED}")


def test_a_connection_that_will_not_close_is_a_sentence_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The close is the one step of the probe that used to run outside
    the sanitizing handler: it was in the `finally`, so an OSError from
    it left through `main` as a library traceback, with whatever the
    driver wrote in its message. Now it answers a sentence like every
    other failure here."""
    app, _ = _endpoint(
        DESCRIBE.format(websocket="wss://voice.example/xiaozhi/v1/", url="https://voice.example")
    )
    monkeypatch.setattr(doctor, "build_client", lambda url: _Unclosable(app, base_url=url))

    assert doctor.main(["https://voice.example/x/ABCDEFGH/"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "could not be closed" in captured.err
    assert "OSError" in captured.err
    assert doctor.SUPPLIED_ENDPOINT in captured.err
    assert PASTED not in captured.out + captured.err
    assert "Traceback" not in captured.err


def test_a_failing_close_does_not_replace_a_failure_already_found(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Whatever failed first is what is reported. A close that raises on
    top of an already sanitized refusal would otherwise hide the
    diagnosis behind the tidying, which is the same defect in the other
    direction."""
    app = FastAPI()

    @app.get("/{path:path}")
    async def redirect() -> Response:
        return Response(status_code=307, headers={"location": "https://elsewhere.example/"})

    monkeypatch.setattr(doctor, "build_client", lambda url: _Unclosable(app, base_url=url))

    assert doctor.main(["https://voice.example/x/ABCDEFGH/"]) == 1

    captured = capsys.readouterr()
    assert "does not follow" in captured.err
    assert "could not be closed" not in captured.err
    assert PASTED not in captured.out + captured.err
    assert "elsewhere.example" not in captured.err


def test_the_close_refusal_carries_no_library_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The chain, not just the message, for the reason the URL refusals
    above are checked that way: a driver's exception held as a
    `__cause__` or a `__context__` is read by anything that renders a
    traceback."""
    app, _ = _endpoint(
        DESCRIBE.format(websocket="wss://voice.example/xiaozhi/v1/", url="https://voice.example")
    )
    monkeypatch.setattr(doctor, "build_client", lambda url: _Unclosable(app, base_url=url))

    # The second reach-in in this file, resolved the way the first one
    # is: a chain is not printed, `main()` consumes the exception and
    # prints one line, and promoting `_probed` would make a public name
    # whose only caller is this test.
    with pytest.raises(doctor.ConfigError) as caught:
        doctor._probed("https://voice.example/x/ABCDEFGH/", doctor.SUPPLIED_ENDPOINT)  # noqa: SLF001

    assert PASTED not in _chain(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


@contextlib.contextmanager
def _served(body: str):
    """A real HTTP server on a real port, for the length of a `with`.

    The canned endpoint everywhere else in this file sits behind the
    client seam, which means the client under test is a TestClient and
    the library that would narrate the request is whichever one
    Starlette imports. The test below is about what the shipped client
    writes to the log, so it has to be the shipped client: this is an
    address it can actually connect to.
    """
    served = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Describing(body))
    thread = threading.Thread(target=served.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{served.server_address[1]}"
    finally:
        served.shutdown()
        served.server_close()
        thread.join(timeout=5)


def _Describing(body: str) -> type[http.server.BaseHTTPRequestHandler]:
    """A handler class answering every GET with this body, and saying
    nothing on stderr: `BaseHTTPRequestHandler` logs each request to
    stderr by default, which is a stream this suite reads."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            encoded = body.encode("utf-8")
            self.send_response(200)
            self.send_header("content-type", "text/plain; charset=utf-8")
            self.send_header("content-length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args: object) -> None:
            return

    return Handler


def test_the_probe_leaves_the_supplied_url_in_no_log_record(
    caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    """The surface no verdict covers. Every sentence this command prints
    hides a supplied URL, and the client library underneath it writes
    one INFO record per request naming that URL in full, which is a
    retained record in a way a terminal is not.

    The real client against a real address, because the seam the rest of
    this file replaces would put a different library's logger in the
    way and prove nothing about the shipped one.
    """
    body = DESCRIBE.format(
        websocket="ws://127.0.0.1:8003/xiaozhi/v1/", url="http://127.0.0.1:8003"
    )
    with _served(body) as address, caplog.at_level(logging.INFO):
        assert doctor.main([f"{address}/xiaozhi/ota/{SECRET_SEGMENT}/"]) == 0

    assert SECRET_SEGMENT not in caplog.text
    for record in caplog.records:
        assert SECRET_SEGMENT not in record.getMessage(), record.name
    captured = capsys.readouterr()
    assert SECRET_SEGMENT not in captured.out + captured.err
    assert doctor.SUPPLIED_ENDPOINT in captured.out


def test_the_probe_puts_the_logger_levels_back(
    endpoint, capsys: pytest.CaptureFixture[str]
) -> None:
    """The boundary is scoped, so a process that imported this command
    logs afterwards exactly what it logged before. Asserted on the
    loggers' own levels rather than their effective ones, which is what
    the boundary restores."""
    before = {name: logging.getLogger(name).level for name in doctor.REQUEST_LOGGERS}
    endpoint(
        DESCRIBE.format(websocket="wss://voice.example/xiaozhi/v1/", url="https://voice.example")
    )

    assert doctor.main(["https://voice.example/x/ABCDEFGH/"]) == 0

    assert {
        name: logging.getLogger(name).level for name in doctor.REQUEST_LOGGERS
    } == before


# What the split made this module responsible for
#
# Three things that were somebody else's before #244: the weight of
# importing this module at all, the sentences its own parser answers a
# mistyped command line with, and the word that reaches it.

# The modules a doctor invocation has no business loading. `config.cli`
# is the file this command left, `config.store` and `db` are the
# database machinery, and `config.api` is the surface neither of them
# has anything to do with.
FORBIDDEN_IMPORTS = (
    "vinga_server.config.cli",
    "vinga_server.config.store",
    "vinga_server.config.api",
    "vinga_server.db",
)


def test_importing_the_doctor_pulls_in_no_database_machinery() -> None:
    """The import-weight contract, in a subprocess because by the time
    this runs the unit lane has imported half the server and this
    process's `sys.modules` would say nothing.

    The claim is deliberately narrow: it is about `import
    vinga_server.doctor` alone, not about the installed entry point,
    which imports `app` and its whole graph before it dispatches
    anything. What it protects is the one thing that would quietly undo
    the split, a convenience import at the top of the module where the
    onboarding derivation deliberately is not: `onboarding/__init__`
    imports `unbound`, which imports `device.bindings`, which imports
    `config.store` and `db`.
    """
    program = (
        "import json, sys; import vinga_server.doctor; "
        f"print(json.dumps([name for name in {FORBIDDEN_IMPORTS!r} if name in sys.modules]))"
    )
    finished = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True, check=True
    )

    assert json.loads(finished.stdout) == []


def test_a_probe_of_a_supplied_url_opens_no_database(
    endpoint, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The property the import discipline exists to keep, asserted
    behaviorally beside it: the import test would still pass if
    something opened a database at call time.

    Pointed at an instance that is not there, which is the honest shape
    of the same claim now that there is no file whose absence could be
    checked: a probe that reached for the database would refuse rather
    than print."""
    # A port nothing listens on: a probe that opened a database would
    # refuse here rather than print.
    monkeypatch.setenv("VINGA_DB_PORT", "1")
    monkeypatch.chdir(tmp_path)
    endpoint(
        DESCRIBE.format(websocket="wss://voice.example/xiaozhi/v1/", url="https://voice.example")
    )

    assert doctor.main(["https://voice.example/x/ABCDEFGH/"]) == 0

    assert capsys.readouterr().out != ""


def test_an_unrecognized_argument_is_refused_without_repeating_it(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The shape the config grammar's shared boundary could not have
    answered safely: argparse says `unrecognized arguments: <url>`, and
    the argument this command takes is a URL that may be the
    deployment's own secret."""
    assert doctor.main([LEGACY_URL, LEGACY_URL]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "unrecognized extra arguments" in captured.err
    assert SECRET_SEGMENT not in captured.err
    assert "voice.example" not in captured.err
    assert "Traceback" not in captured.err


def test_an_option_without_its_value_answers_in_this_module_s_words(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert doctor.main(["--config"]) == 1

    err = capsys.readouterr().err
    assert "an option was given without its value" in err
    # And none of argparse's own wording for it.
    assert "expected one argument" not in err


def test_the_command_word_dispatches_to_this_module(
    endpoint, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A word check in main rather than an argparse subparser, so adding
    the command cannot change how `vinga-server --config path`
    parses."""
    from vinga_server import main as entrypoint

    endpoint(
        DESCRIBE.format(websocket="wss://voice.example/xiaozhi/v1/", url="https://voice.example")
    )
    monkeypatch.setattr(
        entrypoint.sys, "argv", ["vinga-server", "doctor", "https://voice.example/x/ABCDEFGH/"]
    )

    with pytest.raises(SystemExit) as left:
        entrypoint.main()

    assert left.value.code == 0
    assert "vinga-server 9.9.9" in capsys.readouterr().out


def test_a_misspelled_command_word_repeats_nothing_of_what_was_typed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The hole the move would otherwise have opened. An unrecognized
    first word used to fall through to the entry point's own argparse
    parser, which echoes what it did not recognize, so a typo in front
    of an OTA URL printed the URL on stderr. The known words are named;
    the typed ones are not."""
    from vinga_server import main as entrypoint

    monkeypatch.setattr(entrypoint.sys, "argv", ["vinga-server", "docter", LEGACY_URL])

    with pytest.raises(SystemExit) as left:
        entrypoint.main()

    assert left.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert SECRET_SEGMENT not in captured.err
    assert "docter" not in captured.err
    for word in ("config", "conversations", "events", "doctor"):
        assert word in captured.err, word


def test_the_server_s_own_parser_repeats_nothing_either(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The third way out of the entry point, and the one neither the
    word dispatch nor this command's parser reaches: an argument
    starting with a dash falls through to the parser for the server's
    own options, which is argparse and echoes what it did not
    recognize.

    It is reached with a secret-shaped URL behind an option nothing
    declares, which is what an operator produces by typing the command
    word wrongly as a flag. Replacing that parser with a plain
    `argparse.ArgumentParser` puts the URL back on stderr and fails
    this."""
    from vinga_server import main as entrypoint

    monkeypatch.setattr(entrypoint.sys, "argv", ["vinga-server", "-d", LEGACY_URL])

    with pytest.raises(SystemExit) as left:
        entrypoint.main()

    assert left.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "unrecognized extra arguments" in captured.err
    assert SECRET_SEGMENT not in captured.err
    assert "voice.example" not in captured.err


def test_the_server_s_own_parser_says_a_missing_value_in_fixed_words(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The other shape that parser can produce. Nothing of the command
    line can appear in this one even from argparse, since the value is
    the part that is missing, so what is asserted is that the sentence
    is this repository's rather than the library's, and that a
    secret-shaped argument sitting further along the line does not
    arrive by way of a usage dump."""
    from vinga_server import main as entrypoint

    monkeypatch.setattr(
        entrypoint.sys, "argv", ["vinga-server", "--config", "--config", LEGACY_URL]
    )

    with pytest.raises(SystemExit) as left:
        entrypoint.main()

    assert left.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "an option was given without its value" in captured.err
    assert "expected one argument" not in captured.err
    assert SECRET_SEGMENT not in captured.err


def test_the_config_group_no_longer_answers_this_command(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The old spelling, removed with no alias: it meets the config
    grammar's ordinary refusal for a word that is not one of its
    commands.

    Which since the Typer rebuild says so without repeating the word.
    The refusal used to be argparse's `invalid choice: 'doctor'`, and
    the URL a mistyped command is followed by is exactly what this
    entry point exists never to echo."""
    from vinga_server import main as entrypoint

    monkeypatch.setattr(
        entrypoint.sys, "argv", ["vinga-server", "config", "doctor", LEGACY_URL]
    )

    with pytest.raises(SystemExit) as left:
        entrypoint.main()

    assert left.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "that is not a command" in captured.err
    assert "doctor" not in captured.err
    assert SECRET_SEGMENT not in captured.err
