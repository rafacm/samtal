"""Where the CLI sends a command, and what it will not send it over.

The config command group is an API client before it is anything else,
and this is the half of it that has nothing to do with what a command
means: which address it resolves, which token it sends, which
connections it refuses to send either over, how long it waits, and what
it says when there is nothing at the other end.

The rule the refusals here keep is one rule. The bearer token rides on
every request and grants everything the API can do, so the connection
carrying it is loopback or TLS, for the whole client rather than for the
commands that look sensitive, and with no flag to override it. What the
refusals must not do is publish what they refused: a URL is where a
token gets pasted by mistake, and the parser's own exceptions carry the
text they choked on.

The timeouts are here for the same reason they are constants: the read
bound outlasts the database's busy timeout so that contention reaches
the operator as the sentence the API answers with rather than as a
client-side transport error, and the reload's bound outlasts the
registry's own envelope so that nobody is left not knowing what is
running.
"""

import sqlite3
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import vinga_server.tools.mcp as mcp_module
from tests.support.config_cli import API_SECRET_ENV, SECRET, TOKEN, runner
from tests.support.config_cli import chain as _chain
from vinga_server import db as db_module
from vinga_server.config import cli
from vinga_server.config.api import MOUNT_PATH, build_api
from vinga_server.config.loader import ConfigError
from vinga_server.db import DATABASE_FILENAME


@pytest.fixture
def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """One command run the way the entry point runs it, against a server
    of this test's own."""
    return runner(tmp_path, monkeypatch)


# Short enough that a blocked writer gives up inside a test run, and
# long enough that an unblocked one never sees it.
SHORT_BUSY_MS = 200


def test_a_config_file_that_is_not_there_is_an_error_not_a_default(
    run, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("--config", str(tmp_path / "nowhere.yaml"), "list") == 1
    assert "config file not found" in capsys.readouterr().err


# Where the CLI sends a command, and what it will not send it over


def test_the_default_target_is_this_machine_on_the_configured_port(
    run, tmp_path: Path
) -> None:
    """The same file the server reads, through the same machinery, so a
    deployment names its port once and the CLI cannot disagree with the
    server about where the server is."""
    config = tmp_path / "config.yaml"
    config.write_text("server:\n  port: 9123\n", encoding="utf-8")

    assert run("--config", str(config), "list") == 0

    assert run.reached == [f"http://127.0.0.1:9123{MOUNT_PATH}"]


def test_the_environment_names_the_target_and_the_flag_beats_it(
    run, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(cli.API_URL_ENV, "http://127.0.0.1:9001/api")
    assert run("list") == 0

    assert run("--api-url", "http://localhost:9002/api", "list") == 0

    assert run.reached == ["http://127.0.0.1:9001/api", "http://localhost:9002/api"]


def test_a_plain_connection_to_another_host_is_refused(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The bearer token rides on every request and grants everything the
    API can do, so loopback-or-TLS is the rule for the whole client
    rather than a set-secret footnote, and there is deliberately no flag
    to override it.

    The refusal says "loopback address", which is what the check
    actually tests: the documentation says the same words, and the two
    describing the same rule differently is how a reader concludes that
    the machine's own network address would have been allowed."""
    assert run("--api-url", "http://config.example.invalid/api", "list") == 1

    captured = capsys.readouterr()
    assert "no flag to override" in captured.err
    assert "loopback" in captured.err
    assert "https://" in captured.err
    assert run.reached == []


def test_tls_to_another_host_is_permitted(run) -> None:
    assert run("--api-url", "https://config.example.invalid/api", "list") == 0

    assert run.reached == ["https://config.example.invalid/api"]


def test_the_mount_prefix_in_the_url_reaches_the_mounted_namespace(run) -> None:
    """The deployed shape: the API is mounted on the server's own port,
    so a base URL naming that prefix is what a request has to be joined
    onto."""
    assert run("--api-url", f"http://127.0.0.1:8003{MOUNT_PATH}", "list") == 0


# The URLs the parser itself refuses, each carrying the sentinel where
# the parser would have put it into its own ValueError.
UNREADABLE_URLS = [
    ("a port that is not a number", f"http://localhost:{SECRET}/api"),
    ("a port that is empty of digits", "http://localhost:notaport/api"),
    ("an unclosed IPv6 literal", f"http://[::1{SECRET}/api"),
    ("a malformed IPv6 literal", f"http://[bad::{SECRET}::x]:8003/api"),
]

# The URLs that parse and are then refused on their merits. These do
# name the address, minus any userinfo, because an operator who typed
# the wrong scheme needs to see which address was read that way.
UNUSABLE_URLS = [
    ("a scheme that is not http", "ftp://host/api"),
    ("no host at all", "http:///api"),
]


@pytest.mark.parametrize(("what", "url"), UNREADABLE_URLS)
def test_a_url_that_cannot_be_read_is_refused_inside_the_boundary(
    run, capsys: pytest.CaptureFixture[str], what: str, url: str
) -> None:
    """`urlsplit` raises on a malformed IPv6 literal and `.port` raises
    on a port that is not a number, and both carry the text they refused.
    Outside a handler that is a traceback out of main() with the address
    in it, which is the address somebody was typing a token near."""
    assert run("--api-url", url, "list") == 1, what

    captured = capsys.readouterr()
    assert "Traceback" not in captured.err, what
    assert SECRET not in captured.err, what
    assert captured.out == "", what
    assert run.reached == []


@pytest.mark.parametrize(("what", "url"), UNUSABLE_URLS)
def test_a_url_that_is_read_and_refused_names_the_address(
    run, capsys: pytest.CaptureFixture[str], what: str, url: str
) -> None:
    assert run("--api-url", url, "list") == 1, what

    captured = capsys.readouterr()
    assert "http://" in captured.err or "ftp://" in captured.err, what
    assert "Traceback" not in captured.err, what
    assert run.reached == []


def test_a_url_refusal_carries_no_parser_exception(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The chain, not just the message: a ValueError from the URL parser
    holds the text it refused, and anything that walks a chain reads
    what it holds."""
    with pytest.raises(ConfigError) as caught:
        cli._permitted(f"http://localhost:{SECRET}/api", "--api-url")

    assert SECRET not in _chain(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_a_url_carrying_a_credential_is_refused_without_repeating_it(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """A token does not belong in a URL, and the refusal for putting one
    there must not be what publishes it."""
    assert run("--api-url", f"https://user:{SECRET}@config.example.invalid/api", "list") == 1

    captured = capsys.readouterr()
    assert "username or a password" in captured.err
    assert SECRET not in captured.err
    assert "user" not in captured.err.split("https://")[-1].split("/")[0]
    assert run.reached == []


def test_a_missing_token_is_named_before_any_request_is_sent(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(API_SECRET_ENV)

    assert run("list") == 1

    captured = capsys.readouterr()
    assert API_SECRET_ENV in captured.err
    assert "--local" in captured.err
    assert run.reached == []


def test_the_token_comes_from_the_variable_the_config_file_names(
    run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Which on a deployment is the variable the server itself was
    started with, so exec into the running container and the CLI has the
    token for free."""
    config = tmp_path / "config.yaml"
    config.write_text("server:\n  api:\n    secret_env: VINGA_OTHER_TOKEN\n", encoding="utf-8")
    monkeypatch.delenv(API_SECRET_ENV)
    monkeypatch.setenv("VINGA_OTHER_TOKEN", TOKEN)

    assert run("--config", str(config), "list") == 0


def test_the_wrong_token_is_refused_by_the_server_the_way_any_failure_is(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The other half of resolving a token: sending one the server does
    not hold. It is a real 401 from the real gate, and it reaches the
    operator through the same contract every other refusal does, the
    detail on stderr and exit 1."""
    monkeypatch.setenv(API_SECRET_ENV, "not-the-token-this-server-was-given")

    assert run("list") == 1

    captured = capsys.readouterr()
    assert "Authorization" in captured.err
    assert "bearer token" in captured.err
    assert captured.out == ""
    # It was sent, which is what distinguishes this from a token that
    # could not be resolved at all.
    assert run.reached


def test_a_server_that_cannot_be_reached_says_so_and_names_the_recovery_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The real client, against a port nothing is listening on: the one
    case the injected test client cannot show."""
    monkeypatch.delenv("VINGA_CONFIG", raising=False)
    monkeypatch.setenv("VINGA_SERVER__DATABASE__DIR", str(tmp_path / "db"))
    monkeypatch.setenv(API_SECRET_ENV, TOKEN)

    assert cli.main(["--api-url", "http://127.0.0.1:1", "list"]) == 1

    captured = capsys.readouterr()
    assert "cannot reach the configuration API at http://127.0.0.1:1" in captured.err
    assert "--local" in captured.err
    assert "Traceback" not in captured.err


def test_a_body_that_is_not_this_api_s_own_is_not_relayed(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """What a proxy or a gateway returns is not this API's sanitized
    output, so it is reported as a status code and never printed."""
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse

    gateway = FastAPI()

    @gateway.get("/{path:path}")
    def refuse(path: str) -> HTMLResponse:
        return HTMLResponse(f"<html>502 {SECRET}</html>", status_code=502)

    monkeypatch.setattr(
        cli, "build_client", lambda base_url, token: TestClient(gateway, base_url=base_url)
    )

    assert run("list") == 1

    captured = capsys.readouterr()
    assert "answered 502" in captured.err
    assert SECRET not in captured.err
    assert "<html>" not in captured.err


def test_the_read_timeout_outlasts_the_database_s_busy_timeout() -> None:
    """The constant this depends on, asserted against the constant it has
    to outlast.

    The contention tests below shorten the busy timeout so they finish
    inside a test run, which means they would keep passing if the read
    timeout were put back to httpx's five second default: the very
    regression the explicit timeout exists to prevent. So the relationship
    is checked directly, at the production values, where nothing has been
    shortened."""
    busy_timeout_s = db_module.BUSY_TIMEOUT_MS / 1000

    assert cli.READ_TIMEOUT_S > busy_timeout_s
    # Margin, not just order: a read timeout a hair above the busy
    # timeout would still turn a slow answer into a transport error.
    assert cli.READ_TIMEOUT_S >= busy_timeout_s * 2
    # And the connect timeout is bounded, which is the other half: a
    # server that is not there must not take the read timeout to say so.
    assert cli.CONNECT_TIMEOUT_S < busy_timeout_s


def test_the_client_is_built_with_those_timeouts() -> None:
    """The constants are only worth asserting if the client is built
    from them."""
    client = cli.build_client("http://127.0.0.1:8003/api", TOKEN)
    try:
        assert client.timeout.read == cli.READ_TIMEOUT_S
        assert client.timeout.connect == cli.CONNECT_TIMEOUT_S
    finally:
        client.close()


def test_reload_gives_the_server_longer_to_answer_than_a_write(
    run, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The client must not give up on a reload the server then applies:
    that would leave nobody knowing what is running, which is the exact
    ambiguity this whole feature exists to remove. So the bound is the
    server's own envelope with room to spare, and the command really
    does use it.

    Driven through a real `httpx.Client` over a mock transport rather
    than through the fixture's TestClient, which carries a timeout from
    another copy of httpx entirely and would report whatever that made
    of one.
    """
    envelope = (
        mcp_module.CONNECT_TIMEOUT_S
        + mcp_module.STOP_TIMEOUT_S
        + mcp_module.CANCEL_TIMEOUT_S
    )
    assert cli.RELOAD_READ_TIMEOUT_S > cli.READ_TIMEOUT_S
    assert cli.RELOAD_READ_TIMEOUT_S >= 2 * envelope

    made: list[httpx.Client] = []
    empty = dict.fromkeys(cli.RELOAD_OUTCOMES, []) | {"servers": {}}

    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=empty if "reload" in request.url.path else {})

    def factory(base_url: str, token: str | None = None) -> httpx.Client:
        client = httpx.Client(
            base_url=base_url,
            transport=httpx.MockTransport(answer),
            timeout=httpx.Timeout(cli.READ_TIMEOUT_S, connect=cli.CONNECT_TIMEOUT_S),
        )
        made.append(client)
        return client

    monkeypatch.setattr(cli, "build_client", factory)

    assert run("status") == 0
    assert run("reload") == 0

    status_client, reload_client = made
    assert status_client.timeout.read == cli.READ_TIMEOUT_S
    assert reload_client.timeout.read == cli.RELOAD_READ_TIMEOUT_S
    # And the connect bound is untouched: a server that is not there
    # must not take a minute to say so.
    assert reload_client.timeout.connect == cli.CONNECT_TIMEOUT_S


def test_a_write_that_cannot_take_the_lock_prints_the_retryable_refusal(
    run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The reason the client's read timeout has margin above the
    database's busy timeout: the settled answer to contention is a
    sentence the operator can act on, and a client-side timeout at five
    seconds would replace it with one that says nothing.

    Both sides are taken under the same held lock and in the same phase,
    which is what makes the printed sentence and the answered one
    comparable. That phase is the repository's own transaction: since
    #142 an application opens the configuration database once, when its
    lifespan starts, so contention on a deployment is what it is here,
    a server that is already up meeting a second writer. Hence the
    ordering below, which is the whole of the setup: both applications
    are up before the lock exists, and the lock arrives while the CLI's
    command is in flight."""
    monkeypatch.setattr(db_module, "BUSY_TIMEOUT_MS", SHORT_BUSY_MS)
    run("set", "agent", "sam", "-f", "-", stdin="prompt: You are Sam.\n")
    capsys.readouterr()
    directory = tmp_path / "db"
    holder: sqlite3.Connection | None = None
    built = cli.build_client

    def build_then_hold_the_lock(base_url: str, token: str) -> TestClient:
        """The command's client, and then a writer nobody expected.

        Wrapped rather than locked in advance because the CLI builds its
        application inside the command: a lock taken before that would be
        met while the application was opening its engine, which is a
        different refusal in different words, and the other side of this
        assertion would have nothing to equal."""
        nonlocal holder
        client = built(base_url, token)
        holder = sqlite3.connect(directory / DATABASE_FILENAME, isolation_level=None)
        holder.execute("BEGIN IMMEDIATE")
        return client

    monkeypatch.setattr(cli, "build_client", build_then_hold_the_lock)
    with TestClient(
        build_api(TOKEN, directory), headers={"Authorization": f"Bearer {TOKEN}"}
    ) as served:
        assert run("set", "agent", "sam", "-f", "-", stdin="prompt: Still Sam.\n") == 1
        assert holder is not None
        try:
            over_http = served.put("/agents/sam", json={"prompt": "Still Sam."})
        finally:
            holder.close()

        assert over_http.status_code == 409
        captured = capsys.readouterr()
        assert captured.err.rstrip("\n") == over_http.json()["detail"]
        assert captured.out == ""
        # And with the lock let go, the same command is answered.
        monkeypatch.setattr(cli, "build_client", built)
        assert run("set", "agent", "sam", "-f", "-", stdin="prompt: Still Sam.\n") == 0
