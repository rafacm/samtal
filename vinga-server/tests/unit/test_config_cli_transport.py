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
client-side transport error, and the apply's bound outlasts the
registry's own envelope so that nobody is left not knowing what is
running.
"""

import contextlib
import logging
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import vinga_server.tools.mcp as mcp_module
from tests.support.config_cli import API_SECRET_ENV, OTHER_SECRET, SECRET, TOKEN, runner
from tests.support.config_cli import chain as _chain
from tests.support.config_cli import logged as _logged
from tests.support.stores import holding_the_write_lock, the_lock_held
from vinga_server.config import cli
from vinga_server.config.api import MOUNT_PATH, build_api
from vinga_server.config.cli import outcomes
from vinga_server.config.loader import ConfigError
from vinga_server.config.models import DatabaseConfig
from vinga_server.config.responses import McpReloadResult
from vinga_server.db import LOCK_TIMEOUT_MS


@pytest.fixture
def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """One command run the way the entry point runs it, against a server
    of this test's own."""
    return runner(monkeypatch)


# Short enough that a blocked writer gives up inside a test run, and
# long enough that an unblocked one never sees it.

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
    rather than a secret-write footnote, and there is deliberately no flag
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
    # White-box for this refusal test: what is under test is the
    # exception's CHAIN, and a chain is not printed. The command prints
    # one sanitized line, which the runner-driven tests beside this one
    # assert; a __cause__ or a __context__ still holding the library's
    # own exception is reachable only from where the refusal is raised,
    # and anything that renders a traceback would find it there.
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


def test_a_token_in_the_query_is_not_repeated_by_the_refusal_either(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The other place a credential is written into a URL, and the one
    the display helper used to carry through: a refusal names the
    address, and the address it named kept its query string whole.

    The refusal here is the plain-http one, because that is the shape
    that names the address at all. What it may name is the address
    without its credentials, which is both of them and not only the
    userinfo."""
    assert run("--api-url", f"http://config.example.invalid/api?token={SECRET}", "list") == 1

    captured = capsys.readouterr()
    both = captured.out + captured.err
    assert SECRET not in both
    # The parameter and not only its value, on both streams: a refusal
    # is printed on one of them and the claim is that the credential
    # reaches neither. `token` alone cannot be the needle here, since
    # the refusal's own prose is about the bearer token.
    assert "?token=" not in both
    assert f"token={SECRET}" not in both
    # And the address is still named, which is what makes the refusal
    # actionable: it is the credential that is taken out, not the host.
    assert "config.example.invalid" in captured.err
    assert run.reached == []


def test_a_missing_token_is_named_before_any_request_is_sent(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(API_SECRET_ENV)

    assert run("list") == 1

    captured = capsys.readouterr()
    assert API_SECRET_ENV in captured.err
    # And where to find it, which is the environment the server itself
    # was started with.
    assert "exec into the running container" in captured.err
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
    monkeypatch.setenv(API_SECRET_ENV, TOKEN)

    assert cli.main(["--api-url", "http://127.0.0.1:1", "list"]) == 1

    captured = capsys.readouterr()
    assert "cannot reach the configuration API at http://127.0.0.1:1" in captured.err
    assert "importing a kept export" in captured.err
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


# What happens after a URL is accepted
#
# The policy above refuses a credential written into the userinfo, and
# says nothing about one written into the query, which is the other form
# vendors accept. So an address carrying `?token=<secret>` is accepted
# and then reached, and every sentence about what happened at it is a
# sentence naming an address that holds a credential (#290).


REACHABLE_NOWHERE = f"https://127.0.0.1:1/api?token={SECRET}"

# The same address as the one above, as it may be printed.
REACHABLE_NOWHERE_SHOWN = "https://127.0.0.1:1/api"


def test_an_accepted_url_keeps_its_query_credential_only_for_the_request() -> None:
    """The seam the two failures below read: what a request is built
    from and what may be shown are different strings, and only the first
    holds the credential."""
    address = cli._permitted(REACHABLE_NOWHERE, "--api-url")

    assert address.base == "https://127.0.0.1:1/api"
    assert address.query == f"token={SECRET}"
    assert address.shown == REACHABLE_NOWHERE_SHOWN
    assert SECRET not in address.shown
    # The composition, at the seam: the endpoint's path first and the
    # query after it, rather than the endpoint's name appended to the
    # credential's value.
    assert address.endpoint("/agents/sam") == f"/agents/sam?token={SECRET}"


def test_an_address_with_no_query_composes_a_path_and_nothing_else() -> None:
    address = cli._permitted("https://config.example.invalid/api/", "--api-url")

    assert address.base == "https://config.example.invalid/api"
    assert address.query == ""
    assert address.endpoint("/agents/sam") == "/agents/sam"


def test_a_query_carrying_base_sends_the_path_and_the_query_it_was_given(
    run, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What the fields above amount to over a real client: the request
    line, which is the only place the composition can be checked
    honestly.

    An identity carrying a slash is addressed as one percent-encoded
    segment, and it stays one here: what is reattached is a query, and
    it is reattached after the whole path rather than inside it.
    """
    asked: list[httpx.Request] = []
    built: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        return httpx.Response(200, json={"entries": []})

    def factory(base_url: str, token: str) -> httpx.Client:
        built.append(base_url)
        return httpx.Client(base_url=base_url, transport=httpx.MockTransport(answer))

    monkeypatch.setattr(cli, "build_client", factory)

    assert run(
        "--api-url", REACHABLE_NOWHERE, "import", "-f", "-", stdin="{}\n"
    ) == 0

    (sent,) = asked
    assert sent.url.path == "/api/apply"
    assert sent.url.query == f"token={SECRET}".encode()
    # And the base the client was built on carried no query of its own,
    # which is what stopped the endpoint's name landing inside the
    # credential's value.
    assert built == ["https://127.0.0.1:1/api"]


def test_a_transport_failure_after_an_accepted_url_names_it_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The real client against a port nothing is listening on, which is
    the failure an operator meets most and retries in front of a
    terminal.

    The address is still named, because a refusal that named no address
    would leave nobody knowing which one was tried; what it names is the
    address without the credential.
    """
    monkeypatch.delenv("VINGA_CONFIG", raising=False)
    monkeypatch.setenv(API_SECRET_ENV, TOKEN)

    with caplog.at_level(logging.DEBUG):
        assert cli.main(["--api-url", REACHABLE_NOWHERE, "list"]) == 1

    captured = capsys.readouterr()
    both = captured.out + captured.err
    assert f"cannot reach the configuration API at {REACHABLE_NOWHERE_SHOWN}" in captured.err
    assert SECRET not in both
    assert "?token=" not in both
    assert "Traceback" not in captured.err
    assert SECRET not in _logged(caplog)


def test_an_unreadable_answer_from_an_accepted_url_names_it_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The other sentence that names the address: something answered,
    and what it sent is not this API's own output.

    Driven over a mock transport, since the body has to be a page no
    application here would send.
    """
    monkeypatch.delenv("VINGA_CONFIG", raising=False)
    monkeypatch.setenv(API_SECRET_ENV, TOKEN)

    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, html="<html>502 from the gateway</html>")

    def factory(base_url: str, token: str) -> httpx.Client:
        return httpx.Client(base_url=base_url, transport=httpx.MockTransport(answer))

    monkeypatch.setattr(cli, "build_client", factory)

    with caplog.at_level(logging.DEBUG):
        assert cli.main(["--api-url", REACHABLE_NOWHERE, "list"]) == 1

    captured = capsys.readouterr()
    both = captured.out + captured.err
    assert f"the configuration API at {REACHABLE_NOWHERE_SHOWN} answered 502" in captured.err
    assert SECRET not in both
    assert "?token=" not in both
    assert SECRET not in _logged(caplog)


# An address urllib reads, this module's transport policy accepts, and
# the client library then refuses: a hostname carrying a zero-width
# joiner cannot be encoded as IDNA, and httpx says so by quoting the
# hostname back. Construction is where that happens, which is why
# construction is inside the request's boundary.
#
# Two sentinels, because the two halves of this address are not the same
# kind of thing. The query is a credential and appears nowhere. The host
# is what `shown_url` deliberately keeps and what every refusal in this
# file already prints, so what is asserted about it is that what reaches
# the terminal is the sanitized, printable form rather than the library's
# quotation of what was typed.
JOINER = "‍"

UNOPENABLE = f"https://{OTHER_SECRET}{JOINER}.example/api?token={SECRET}"

UNOPENABLE_SHOWN = f"https://{OTHER_SECRET}?.example/api"


def test_an_address_the_library_will_not_open_is_a_sentence_not_a_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The real client, and the real construction failure: `main` catches
    `ConfigError` and nothing else, so an address the library refuses
    used to leave as a traceback carrying the hostname exactly as it was
    typed."""
    monkeypatch.delenv("VINGA_CONFIG", raising=False)
    monkeypatch.setenv(API_SECRET_ENV, TOKEN)

    with caplog.at_level(logging.DEBUG):
        assert cli.main(["--api-url", UNOPENABLE, "list"]) == 1

    captured = capsys.readouterr()
    both = captured.out + captured.err
    assert f"no connection can be opened to {UNOPENABLE_SHOWN}" in captured.err
    assert captured.out == ""
    assert "Traceback" not in captured.err
    # The credential in the query, on every surface.
    assert SECRET not in both
    assert "?token=" not in both
    assert SECRET not in _logged(caplog)
    # The library's own wording, which is the other half of what a
    # traceback published: it quotes the hostname as it was typed, raw
    # joiner included.
    assert JOINER not in both
    assert "IDNA" not in both
    assert "InvalidURL" not in both


def test_that_refusal_carries_no_library_exception_either(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """White-box for the chain: `httpx.InvalidURL` holds the hostname it
    refused, and an exception raised inside the handler would keep it on
    `__context__`."""
    monkeypatch.setenv(API_SECRET_ENV, TOKEN)
    address = cli._permitted(UNOPENABLE, "--api-url")

    with pytest.raises(ConfigError) as caught:
        cli._sent("GET", "/agents", cli._NOTHING, address, TOKEN, cli.READ_TIMEOUT_S)

    rendered = _chain(caught.value)
    assert SECRET not in rendered
    assert JOINER not in rendered
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def _answering(body: object = None) -> httpx.MockTransport:
    """A transport that answers anything, so a test about what a request
    leaves behind does not depend on what answered it."""

    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"entries": []} if body is None else body)

    return httpx.MockTransport(answer)


def test_no_request_this_command_makes_narrates_itself(
    run, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The surface the two failures above do not cover, and the one a
    terminal does not show: httpx writes a line per request at INFO
    naming the URL it was given, and `logs.py` floors that library at
    INFO deliberately, because for every other caller in this server the
    URL says nothing that is not already public.

    For this one it is the address an operator typed, accepted with its
    query string whole, and a log record is retained in a way a terminal
    is not. So the request runs inside a logging boundary and this is a
    successful command, which is the case no refusal test could reach:
    the credential is in the URL whether or not anything went wrong.
    """
    monkeypatch.setattr(
        cli,
        "build_client",
        lambda base_url, token: httpx.Client(base_url=base_url, transport=_answering()),
    )

    with caplog.at_level(logging.DEBUG):
        assert run(
            "--api-url", REACHABLE_NOWHERE, "import", "-f", "-", stdin="{}\n"
        ) == 0

    assert SECRET not in _logged(caplog)
    # Named, because the claim is about a library that was writing a
    # line and now writes none: an assertion on the sentinel alone would
    # pass again the day the URL stops being the thing it carries.
    assert [
        record.name
        for record in caplog.records
        if record.name.startswith(("httpx", "httpcore"))
    ] == []


# The names the request is narrated under, one per library the boundary
# names, and the connection tracer under the name it really writes as:
# `httpcore` logs from `httpcore.connection` and `httpcore.http11`
# rather than from its own name, which is what makes a boundary over the
# parent the thing that silences them.
#
# A mock transport answers without ever opening a connection, so the
# tracer under it says nothing on its own and the httpcore half of the
# boundary would be a line nothing checked. These records stand in for
# what it writes, at the level and under the names it writes them.
NARRATORS = ("httpx", "httpcore.connection", "httpcore.http11")


def test_neither_library_can_narrate_while_the_request_is_open(
    run, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Both halves of the boundary, from inside the request itself.

    The transport writes a record under each name while the request is
    in flight, which is exactly when the libraries write theirs, and it
    plants the credential in one as an argument rather than in the
    message, since a value that reached a record that way is a value the
    formatter puts back into the line.
    """
    narrated: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        for name in NARRATORS:
            logging.getLogger(name).info("%s reached %s", name, SECRET)
            narrated.append(name)
        return httpx.Response(200, json={"entries": []})

    monkeypatch.setattr(
        cli,
        "build_client",
        lambda base_url, token: httpx.Client(
            base_url=base_url, transport=httpx.MockTransport(answer)
        ),
    )

    with caplog.at_level(logging.DEBUG):
        assert run(
            "--api-url", REACHABLE_NOWHERE, "import", "-f", "-", stdin="{}\n"
        ) == 0

    # The records really were written, so their absence below is the
    # boundary rather than a transport that was never reached.
    assert narrated == list(NARRATORS)
    assert SECRET not in _logged(caplog)
    assert [
        record.name
        for record in caplog.records
        if record.name.startswith(("httpx", "httpcore"))
    ] == []


def test_the_quiet_lasts_exactly_as_long_as_the_request(
    run, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Logger levels are process state, so a command that left one
    raised would have silenced a library for whatever runs next. Every
    name the boundary holds, since a restore that put one back is not a
    restore."""
    before = {name: logging.getLogger(name).level for name in cli.REQUEST_LOGGERS}
    monkeypatch.setattr(
        cli,
        "build_client",
        lambda base_url, token: httpx.Client(base_url=base_url, transport=_answering()),
    )

    assert run(
        "--api-url", REACHABLE_NOWHERE, "import", "-f", "-", stdin="{}\n"
    ) == 0

    assert {name: logging.getLogger(name).level for name in cli.REQUEST_LOGGERS} == before
    # Read off the production tuple above, and checked to be the pair it
    # is: a name dropped from it would otherwise be a name this test
    # stopped asserting about at the same moment it stopped being held.
    assert cli.REQUEST_LOGGERS == ("httpx", "httpcore")


def test_neither_failure_carries_the_credential_in_its_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The chain, not just the message: httpx's own exceptions carry the
    request, and the request carries the URL that was reached.

    White-box, for the reason the URL parser's chain test above is: a
    `__cause__` or a `__context__` is not printed, and is reachable only
    from where the refusal is raised.
    """
    address = cli._permitted(REACHABLE_NOWHERE, "--api-url")

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    monkeypatch.setattr(
        cli,
        "build_client",
        lambda base_url, token: httpx.Client(
            base_url=base_url, transport=httpx.MockTransport(refuse)
        ),
    )

    with pytest.raises(ConfigError) as unreachable:
        cli._sent("GET", "/agents", cli._NOTHING, address, TOKEN, cli.READ_TIMEOUT_S)

    assert SECRET not in _chain(unreachable.value)
    assert unreachable.value.__cause__ is None
    assert unreachable.value.__context__ is None

    answered = httpx.Response(502, html=f"<html>{SECRET}</html>")
    with pytest.raises(ConfigError) as unreadable:
        cli._answer(answered, address)

    assert SECRET not in _chain(unreadable.value)
    assert unreadable.value.__cause__ is None
    assert unreadable.value.__context__ is None


def test_the_read_timeout_outlasts_the_database_s_lock_timeout() -> None:
    """The constant this depends on, asserted against the constant it has
    to outlast.

    The contention tests below shorten the lock timeout so they finish
    inside a test run, which means they would keep passing if the read
    timeout were put back to httpx's five second default: the very
    regression the explicit timeout exists to prevent. So the relationship
    is checked directly, at the production values, where nothing has been
    shortened.

    A margin above the typical wait rather than a derived ceiling.
    Postgres applies `lock_timeout` per lock acquisition, so a
    transaction can wait it out on the advisory gate and again on a
    later lock, and nothing bounds its execution: what this pins is
    that the ordinary contended write reports the retryable refusal
    rather than a transport error."""
    lock_timeout_s = LOCK_TIMEOUT_MS / 1000

    assert cli.READ_TIMEOUT_S > lock_timeout_s
    # Margin, not just order: a read timeout a hair above the lock
    # timeout would still turn a slow answer into a transport error.
    assert cli.READ_TIMEOUT_S >= lock_timeout_s * 2
    # And the connect timeout is bounded, which is the other half: a
    # server that is not there must not take the read timeout to say so.
    assert cli.CONNECT_TIMEOUT_S < lock_timeout_s


def test_the_client_is_built_with_those_timeouts() -> None:
    """The constants are only worth asserting if the client is built
    from them."""
    client = cli.build_client("http://127.0.0.1:8003/api", TOKEN)
    try:
        assert client.timeout.read == cli.READ_TIMEOUT_S
        assert client.timeout.connect == cli.CONNECT_TIMEOUT_S
    finally:
        client.close()


def test_the_client_carries_the_token_it_was_built_with() -> None:
    """The other half of this seam's construction policy, and the half
    that came back here when #244 gave the doctor a seam of its own.

    The header is what the whole transport policy above exists to
    protect, so it is asserted at the constructor rather than only
    through a request. The token is a required argument now: every
    caller resolves one before it builds a client, and the untaken
    branch a default kept was a branch nothing was checking."""
    client = cli.build_client("http://127.0.0.1:8003/api", TOKEN)
    try:
        assert client.headers["Authorization"] == f"Bearer {TOKEN}"
    finally:
        client.close()


def test_apply_gives_the_server_longer_to_answer_than_a_write(
    run, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The client must not give up on an install the server then
    carries out: that would leave nobody knowing what is running, which
    is the exact ambiguity this whole feature exists to remove. So the
    bound is the server's own envelope with room to spare, and the
    command really does use it.

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
    assert cli.APPLY_READ_TIMEOUT_S > cli.READ_TIMEOUT_S
    assert cli.APPLY_READ_TIMEOUT_S >= 2 * envelope

    made: list[httpx.Client] = []
    empty = {
        "mcp": dict.fromkeys(outcomes(McpReloadResult), []) | {"servers": {}},
        "prompts": {"changed": []},
        "fillers": None,
        "providers": None,
        "agents": None,
    }

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

    assert run("mcp-server", "status") == 0
    assert run("apply") == 0

    status_client, apply_client = made
    assert status_client.timeout.read == cli.READ_TIMEOUT_S
    assert apply_client.timeout.read == cli.APPLY_READ_TIMEOUT_S
    # And the connect bound is untouched: a server that is not there
    # must not take a minute to say so.
    assert apply_client.timeout.connect == cli.CONNECT_TIMEOUT_S


def test_import_waits_for_the_server_however_long_it_takes(
    run, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The apply's argument taken to its conclusion where no finite
    envelope exists.

    An import is one transaction that loads the whole existing
    configuration and validates the whole resulting one, and nothing
    about the request bounds the size of the store it lands in, so there
    is no number to derive. A bound that expired after the commit would
    be the exact ambiguity the timeouts exist to prevent, so there is no
    bound. The connect timeout is untouched: a server that is not there
    still says so quickly.

    Driven through a real `httpx.Client` over a mock transport for the
    reason the apply's case is: the fixture's TestClient carries a
    timeout from another copy of httpx entirely.
    """
    assert cli.IMPORT_READ_TIMEOUT_S is None

    made: list[httpx.Client] = []

    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"entries": []})

    def factory(base_url: str, token: str | None = None) -> httpx.Client:
        client = httpx.Client(
            base_url=base_url,
            transport=httpx.MockTransport(answer),
            timeout=httpx.Timeout(cli.READ_TIMEOUT_S, connect=cli.CONNECT_TIMEOUT_S),
        )
        made.append(client)
        return client

    monkeypatch.setattr(cli, "build_client", factory)

    assert run("import", "-f", "-", stdin="{}\n") == 0

    (imported,) = made
    assert imported.timeout.read is None
    assert imported.timeout.connect == cli.CONNECT_TIMEOUT_S


def test_a_write_that_cannot_take_the_lock_prints_the_retryable_refusal(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The reason the client's read timeout has margin above the
    database's lock timeout: the settled answer to contention is a
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
    run("agent", "set", "sam", "-f", "-", stdin="prompt: You are Sam.\n")
    capsys.readouterr()
    holding = contextlib.ExitStack()
    built = cli.build_client

    def build_then_hold_the_lock(base_url: str, token: str) -> TestClient:
        """The command's client, and then a writer nobody expected.

        Wrapped rather than locked in advance because the CLI builds its
        application inside the command: a lock taken before that would be
        met while the application was opening its engine, which is a
        different refusal in different words, and the other side of this
        assertion would have nothing to equal."""
        client = built(base_url, token)
        holding.enter_context(the_lock_held())
        return client

    with holding_the_write_lock(monkeypatch):
        monkeypatch.setattr(cli, "build_client", build_then_hold_the_lock)
        with TestClient(
            build_api(TOKEN, DatabaseConfig()),
            headers={"Authorization": f"Bearer {TOKEN}"},
        ) as served:
            assert run("agent", "set", "sam", "-f", "-", stdin="prompt: Still Sam.\n") == 1
            try:
                over_http = served.put("/agents/sam", json={"prompt": "Still Sam."})
            finally:
                holding.close()

            assert over_http.status_code == 409
            captured = capsys.readouterr()
            assert captured.err.rstrip("\n") == over_http.json()["detail"]
            assert captured.out == ""
            # And with the lock let go, the same command is answered.
            monkeypatch.setattr(cli, "build_client", built)
            assert run("agent", "set", "sam", "-f", "-", stdin="prompt: Still Sam.\n") == 0
