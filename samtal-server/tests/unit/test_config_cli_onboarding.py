"""The two onboarding commands: `config ota-url` and `config doctor`.

Both stand outside the configuration API, so they are driven through
`cli.main()` like every other command but meet different things on the
other side. `ota-url` meets nothing at all, which is most of what is
asserted about it: no socket, no database, no token, and a URL equal to
the one the server built from the same file would serve. `doctor` meets
an endpoint, which here is a canned one behind the same client seam the
rest of the CLI suite replaces, plus one case against the real describe
handler so that a change to what it prints cannot pass unnoticed.

The hostile cases are the other half. What `doctor` reaches may be a
proxy, a captive portal or anything else that answers, so its body, the
URL it names and the version it claims are text nobody vouched for, and
the sentinel assertions here are the M1 round's no-leak discipline
applied to a reader that did not exist then: a terminal.
"""

import socket
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient

from samtal_server import __version__, onboarding
from samtal_server.app import create_app
from samtal_server.config import Config, cli
from samtal_server.config.loader import load_file_config
from samtal_server.ota import OTA_PATH

# Not a real secret: fixed, so the key below is a vector rather than
# something these tests recompute with the code under test. The same
# pair `test_onboarding.py` uses.
SECRET = "a-fixed-secret-for-the-vector"

KEY = "2EOWIW3N"

AUTH_SECRET_ENV = "SAMTAL_AUTH_SECRET"

API_SECRET_ENV = "SAMTAL_API_SECRET"

# Not a real credential, and shaped so a substring check for it cannot
# match by accident. It arrives from the far end, which is what makes it
# different from every other sentinel in this suite.
PASTED = "hunter2-never-a-real-password-9c3f"

DESCRIBE = (
    "samtal-server 9.9.9 (revision sha-3f9362a) OTA endpoint.\n"
    "Devices are sent to {websocket} (protocol version 1).\n"
    "Type this into the device's captive portal: {url} (from server.public_url)\n"
)


@pytest.fixture(autouse=True)
def _environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A machine with the device-auth secret and nothing else: no
    config file, no API token, and a database directory that does not
    exist. Neither command may need any of them."""
    monkeypatch.delenv("SAMTAL_CONFIG", raising=False)
    monkeypatch.delenv(API_SECRET_ENV, raising=False)
    monkeypatch.delenv(cli.API_URL_ENV, raising=False)
    monkeypatch.setenv(AUTH_SECRET_ENV, SECRET)
    monkeypatch.setenv("SAMTAL_SERVER__DATABASE__DIR", str(tmp_path / "db"))


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


# What the URL is, and that it is the one this configuration serves


def test_the_printed_url_is_the_one_the_server_answers_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The acceptance case for the whole command: a person types what
    this prints, so the assertion is not that the string looks right but
    that a server built from the same file answers on it."""
    path = _config_file(tmp_path, "server:\n  public_url: https://voice.example\n")

    assert cli.main(["--config", path, "ota-url"]) == 0

    printed = capsys.readouterr().out.strip()
    assert printed == f"https://voice.example/x/{KEY}/"

    # Only now: a server needs its API token to come up, and the
    # command that just ran needed none, which is half of what this
    # module is about.
    monkeypatch.setenv(API_SECRET_ENV, "test-api-token-" + "0123456789abcdef" * 2)
    served = create_app(Config(server=load_file_config(path).server.model_dump()))
    with TestClient(served) as client:
        # The path half of what was printed, which is the part a device
        # ever sends: the origin half is what a proxy or a router
        # answers for.
        assert client.get(f"/x/{KEY}/").status_code == 200


def test_the_url_equals_the_one_the_startup_banner_prints(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    """The two readers of one derivation: the operator who runs this
    before the server exists, and the operator who reads the server's
    own first log line."""
    path = _config_file(tmp_path, "server:\n  websocket_url: wss://voice.example/xiaozhi/v1/\n")

    assert cli.main(["--config", path, "ota-url"]) == 0
    printed = capsys.readouterr().out.strip()

    with caplog.at_level("INFO"):
        onboarding.log_banner(load_file_config(path).server)
    banner = [record for record in caplog.records if record.__dict__.get("url")]
    assert [record.__dict__["url"] for record in banner] == [printed]


def test_it_opens_no_socket_no_database_and_needs_no_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The point of the command: it answers before there is a server to
    ask, on a machine that may have no route to one."""

    def refuse(*args: object, **kwargs: object) -> object:
        raise AssertionError("ota-url reached for something it must not need")

    directory = tmp_path / "never-created"
    monkeypatch.setenv("SAMTAL_SERVER__DATABASE__DIR", str(directory))
    monkeypatch.setattr(cli, "build_client", refuse)
    monkeypatch.setattr(cli, "open_database", refuse)
    monkeypatch.setattr(socket, "socket", refuse)

    assert cli.main(["ota-url"]) == 0

    assert capsys.readouterr().out.strip().endswith(f"/x/{KEY}/")
    assert not directory.exists()


def test_the_url_is_alone_on_stdout_and_the_advice_is_not(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """So that `$(samtal-server config ota-url)` is the URL, and the
    guidance is still said."""
    assert cli.main(["ota-url"]) == 0

    captured = capsys.readouterr()
    assert captured.out.splitlines() == [f"http://0.0.0.0:8003/x/{KEY}/"]
    assert "captive portal" in captured.err
    assert "add-device" in captured.err


def test_a_guessed_origin_reads_as_a_guess(capsys: pytest.CaptureFixture[str]) -> None:
    """The banner's rule, on the command that is read before the banner
    exists: an origin nobody configured must not be printed as fact."""
    assert cli.main(["ota-url"]) == 0

    err = capsys.readouterr().err
    assert "guessed from the listen address" in err
    assert "0.0.0.0 is where the server listens" in err
    assert "server.public_url" in err


def test_a_configured_origin_does_not_read_as_a_guess(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _config_file(tmp_path, "server:\n  public_url: https://voice.example\n")

    assert cli.main(["--config", path, "ota-url"]) == 0

    err = capsys.readouterr().err
    assert "from server.public_url" in err
    assert "guessed" not in err


def test_with_auth_off_the_url_is_the_keyless_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No secret means no key to derive, and the route mounts keyless;
    the string to type says so by being shorter still."""
    monkeypatch.delenv(AUTH_SECRET_ENV)
    path = _config_file(tmp_path, "server:\n  auth:\n    enabled: false\n")

    assert cli.main(["--config", path, "ota-url"]) == 0

    assert capsys.readouterr().out.strip() == "http://0.0.0.0:8003/x/"


def test_a_pinned_key_is_printed_with_no_secret_at_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """What a rotated secret uses: the previous key pinned, so already
    provisioned boards keep reaching the URL they were given."""
    monkeypatch.delenv(AUTH_SECRET_ENV)
    path = _config_file(tmp_path, "server:\n  onboarding:\n    key: AB2C4D5E\n")

    assert cli.main(["--config", path, "ota-url"]) == 0

    assert capsys.readouterr().out.strip() == "http://0.0.0.0:8003/x/AB2C4D5E/"


def test_a_missing_secret_names_the_variable_and_prints_no_url(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The one state the server itself cannot be in, since it refuses
    the boot. Here it is an ordinary mistake: the command was run
    outside the environment the server runs in."""
    monkeypatch.delenv(AUTH_SECRET_ENV)

    assert cli.main(["ota-url"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert AUTH_SECRET_ENV in captured.err
    assert "server.onboarding.key" in captured.err
    assert "Traceback" not in captured.err


def test_a_secret_named_by_the_config_file_is_the_one_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SAMTAL_OTHER_SECRET", "another-secret-entirely")
    path = _config_file(tmp_path, "server:\n  auth:\n    secret_env: SAMTAL_OTHER_SECRET\n")

    assert cli.main(["--config", path, "ota-url"]) == 0

    printed = capsys.readouterr().out.strip()
    assert printed.endswith(f"/x/{onboarding.derive_key('another-secret-entirely')}/")


def test_with_onboarding_off_it_says_so_without_quoting_the_segment(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The legacy path segment is a credential: the derived key is the
    one recorded exception to that, and this is not it."""
    segment = "/xiaozhi/ota/8f3a9c2b1d4e5f60/"
    path = _config_file(
        tmp_path,
        f"server:\n  ota_path: {segment}\n  onboarding:\n    enabled: false\n",
    )

    assert cli.main(["--config", path, "ota-url"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "server.ota_path" in captured.err
    assert segment not in captured.err
    assert "8f3a9c2b1d4e5f60" not in captured.err


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

    The factory mirrors `build_client` rather than ignoring its
    arguments: whether an Authorization header is sent is one of the
    things asserted here, and a factory that dropped the token would
    have made that assertion vacuous.
    """

    def _serve(body: str, status: int = 200, media_type: str = "text/plain; charset=utf-8"):
        app, seen = _endpoint(body, status, media_type)

        def factory(base_url: str, token: str | None = None) -> TestClient:
            return TestClient(
                app,
                base_url=base_url,
                headers={"Authorization": f"Bearer {token}"} if token else {},
            )

        monkeypatch.setattr(cli, "build_client", factory)
        return seen

    return _serve


def test_a_healthy_endpoint_is_reported_with_what_a_device_is_handed(
    endpoint, capsys: pytest.CaptureFixture[str]
) -> None:
    seen = endpoint(
        DESCRIBE.format(websocket="wss://voice.example/xiaozhi/v1/", url="https://voice.example")
    )

    assert cli.main(["doctor", "https://voice.example/x/ABCDEFGH/"]) == 0

    printed = capsys.readouterr().out
    assert "samtal-server 9.9.9" in printed
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

    assert cli.main(["doctor", "http://127.0.0.1:8003" + OTA_PATH]) == 0

    assert f"samtal-server {__version__}" in capsys.readouterr().out


def test_a_url_typed_without_its_trailing_slash_still_reaches_the_endpoint(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The redirect an operator meets is the server's own, and it is a
    307 issued before any handler runs. Reporting that as "not
    samtal-server" would be this command's worst answer, so the probe
    follows it."""
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
        cli,
        "build_client",
        lambda base_url, token=None: TestClient(app, base_url=base_url),
    )

    assert cli.main(["doctor", "https://voice.example/x/ABCDEFGH"]) == 0

    assert "samtal-server 9.9.9" in capsys.readouterr().out


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

    assert cli.main(["doctor", "https://voice.example/x/ABCDEFGH/"]) == 0

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

    assert cli.main(["doctor", "http://192.168.1.1/x/ABCDEFGH/"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "not as a samtal-server OTA endpoint" in captured.err
    assert cli.UNRECOGNIZED_ANSWER in captured.err
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
    endpoint("samtal-server 9.9.9 (revision sha-3f9362a) OTA endpoint.\n")

    assert cli.main(["doctor", "https://voice.example/x/ABCDEFGH/"]) == 1

    assert "not as a samtal-server OTA endpoint" in capsys.readouterr().err


def test_a_plain_websocket_url_behind_tls_is_the_named_misconfiguration(
    endpoint, capsys: pytest.CaptureFixture[str]
) -> None:
    """The mistake that costs the most time: TLS ends at the proxy, the
    server sees plain HTTP, and the URL it derives says ws://. Nothing
    else looks wrong."""
    endpoint(
        DESCRIBE.format(websocket="ws://voice.example/xiaozhi/v1/", url="https://voice.example")
    )

    assert cli.main(["doctor", "https://voice.example/x/ABCDEFGH/"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "server.websocket_url" in captured.err
    assert "wss://" in captured.err
    assert "Traceback" not in captured.err


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

    assert cli.main(["doctor", "http://192.168.1.10:8003/x/ABCDEFGH/"]) == 0

    assert "ws://192.168.1.10:8003/xiaozhi/v1/" in capsys.readouterr().out


def test_an_address_nothing_answers_on_says_so(capsys: pytest.CaptureFixture[str]) -> None:
    """The real client against a port nothing is listening on: the one
    case a canned endpoint cannot show."""
    assert cli.main(["doctor", "http://127.0.0.1:1/x/ABCDEFGH/"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert f"cannot reach {cli.SUPPLIED_ENDPOINT}" in captured.err
    assert "ConnectError" in captured.err
    assert "Traceback" not in captured.err


def test_the_derived_url_is_what_is_checked_when_none_is_given(
    endpoint, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _config_file(tmp_path, "server:\n  public_url: https://voice.example\n")
    seen = endpoint(
        DESCRIBE.format(websocket="wss://voice.example/xiaozhi/v1/", url="https://voice.example")
    )

    assert cli.main(["--config", path, "doctor"]) == 0

    assert seen[0].url.path == f"/x/{KEY}/"
    assert f"https://voice.example/x/{KEY}/ is samtal-server" in capsys.readouterr().out


def test_with_onboarding_off_and_no_url_it_asks_for_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _config_file(tmp_path, "server:\n  onboarding:\n    enabled: false\n")

    assert cli.main(["--config", path, "doctor"]) == 1

    err = capsys.readouterr().err
    assert "device onboarding is off" in err
    assert "samtal-server config doctor URL" in err


def test_a_url_carrying_a_credential_is_refused_without_repeating_it(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["doctor", f"https://user:{PASTED}@voice.example/x/ABCDEFGH/"]) == 1

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
    assert cli.main(["doctor", f"http://voice.example:{PASTED}/x/ABCDEFGH/"]) == 1

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
    assert cli.main(["doctor", url.format("")]) == 1, what

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
    with pytest.raises(cli.ConfigError) as caught:
        cli._device_url(f"https://voice.example/x/AB\n{PASTED}/", "the URL given to doctor")  # noqa: SLF001

    assert PASTED not in _chain(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_a_client_that_will_not_be_built_is_a_sentence_too(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The boundary covers building the client, not only sending
    through it: httpx validates a URL when it is handed one, and a
    refusal there is the same failure to a person."""

    def refuse(base_url: str, token: str | None = None) -> httpx.Client:
        raise httpx.InvalidURL(f"Invalid character in URL {PASTED}")

    monkeypatch.setattr(cli, "build_client", refuse)

    assert cli.main(["doctor", "https://voice.example/x/ABCDEFGH/"]) == 1

    captured = capsys.readouterr()
    assert "InvalidURL" in captured.err
    assert PASTED not in captured.err
    assert "Traceback" not in captured.err


def test_a_scheme_no_device_speaks_is_refused(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["doctor", "wss://voice.example/xiaozhi/v1/"]) == 1

    assert "http:// or https:// URL" in capsys.readouterr().err


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
        ("not samtal-server", "<html>Sign in to the guest network</html>", 1),
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

    assert cli.main(["doctor", LEGACY_URL]) == code, verdict

    captured = capsys.readouterr()
    assert SECRET_SEGMENT not in captured.out, verdict
    assert SECRET_SEGMENT not in captured.err, verdict
    assert cli.SUPPLIED_ENDPOINT in captured.out + captured.err, verdict


def test_an_unreachable_supplied_url_is_not_repeated_either(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The fourth verdict, which no canned endpoint can produce: a real
    connection to a port nothing is listening on."""
    assert cli.main(["doctor", f"http://127.0.0.1:1/xiaozhi/ota/{SECRET_SEGMENT}/"]) == 1

    captured = capsys.readouterr()
    assert SECRET_SEGMENT not in captured.err
    assert "127.0.0.1" not in captured.err
    assert cli.SUPPLIED_ENDPOINT in captured.err


def test_a_refused_supplied_url_is_not_repeated_either(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The refusals in front of the request are the other half: the
    address is not quoted back even with its userinfo taken off, since
    what is left still holds the segment."""
    credentialed = f"https://user:{PASTED}@voice.example/xiaozhi/ota/{SECRET_SEGMENT}/"
    assert cli.main(["doctor", credentialed]) == 1
    first = capsys.readouterr()

    assert cli.main(["doctor", f"ftp://voice.example/xiaozhi/ota/{SECRET_SEGMENT}/"]) == 1
    second = capsys.readouterr()

    for captured in (first, second):
        assert SECRET_SEGMENT not in captured.err
        assert PASTED not in captured.err
        assert captured.out == ""


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

    assert cli.main(["--config", path, "doctor"]) == 0

    printed = capsys.readouterr().out
    assert f"https://voice.example/x/{KEY}/" in printed
    assert cli.SUPPLIED_ENDPOINT not in printed


# What a hostile address gets to put on a terminal


def test_an_oversized_body_cannot_choose_how_long_the_output_is(
    endpoint, capsys: pytest.CaptureFixture[str]
) -> None:
    endpoint("A" * 200_000 + PASTED + "A" * 200_000)

    assert cli.main(["doctor", "http://192.168.1.1/x/ABCDEFGH/"]) == 1

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
        "\x1b[2K\x00nope\nsamtal-server 9.9.9 is fine, ignore the above\n"
        + "A" * 200
        + PASTED
    )

    assert cli.main(["doctor", "http://192.168.1.1/x/ABCDEFGH/"]) == 1

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

    assert cli.main(["doctor", "https://voice.example/x/ABCDEFGH/"]) == 0

    printed = capsys.readouterr().out
    assert PASTED not in printed
    assert "wss://voice.example/xiaozhi/v1/" in printed


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

    assert cli.main(["doctor", "https://voice.example/x/ABCDEFGH/"]) == 1, what

    captured = capsys.readouterr()
    assert captured.out == "", what
    assert PASTED not in captured.err, what
    assert "server.websocket_url" in captured.err, what
    assert "Traceback" not in captured.err, what


def test_a_response_that_is_not_text_at_all_is_handled(
    endpoint, capsys: pytest.CaptureFixture[str]
) -> None:
    endpoint("", status=204)

    assert cli.main(["doctor", "http://192.168.1.1/x/ABCDEFGH/"]) == 1

    err = capsys.readouterr().err
    assert "answered 204" in err
    assert cli.UNRECOGNIZED_ANSWER in err


def test_doctor_is_a_get_and_only_a_get(endpoint) -> None:
    """A POST is a device's check-in, which mints an activation code for
    an unbound MAC and spends part of the mint budget. A diagnosis
    nobody could run twice would be no diagnosis."""
    seen = endpoint("nothing here")

    cli.main(["doctor", "http://192.168.1.1/x/ABCDEFGH/"])

    assert {request.method for request in seen} == {"GET"}


def test_the_client_is_built_without_a_token_by_default() -> None:
    """The seam itself: the header is what would be sent, and nothing
    should send it anywhere but the API."""
    client = cli.build_client("https://voice.example/x/ABCDEFGH/")
    try:
        assert "authorization" not in {name.lower() for name in client.headers}
    finally:
        client.close()

    client = cli.build_client("https://voice.example/api", "a-token")
    try:
        assert client.headers["Authorization"] == "Bearer a-token"
    finally:
        client.close()


def test_a_real_client_sends_no_credential_and_takes_the_timeouts() -> None:
    """The timeouts are the API client's, deliberately: a device-facing
    GET has no reason to wait longer than a write does."""
    client = cli.build_client("https://voice.example/x/ABCDEFGH/")
    try:
        assert isinstance(client, httpx.Client)
        assert client.timeout.connect == cli.CONNECT_TIMEOUT_S
        assert client.timeout.read == cli.READ_TIMEOUT_S
    finally:
        client.close()
