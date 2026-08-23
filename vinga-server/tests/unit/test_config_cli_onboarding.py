"""The onboarding command: `config ota-url`.

It stands outside the configuration API, so it is driven through
`cli.main()` like every other command but meets nothing on the other
side, which is most of what is asserted about it: no socket, no
database, no token, and a URL equal to the one a server built from the
same file would serve.

What answers on that URL is `vinga-server doctor`, a command of its own
since #244 and a suite of its own in `test_doctor.py`. What the two
still share is the derivation, which lives in `onboarding.origin`; the
assertion here that a printed URL is one a real application answers on
is what keeps this half of it honest.
"""

import socket
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vinga_server import onboarding
from vinga_server.app import create_app
from vinga_server.config import Config, cli
from vinga_server.config.loader import load_file_config

# Not a real secret: fixed, so the key below is a vector rather than
# something these tests recompute with the code under test. The same
# pair `test_onboarding.py` uses.
SECRET = "a-fixed-secret-for-the-vector"

KEY = "NUGFZQ2Y"

AUTH_SECRET_ENV = "VINGA_AUTH_SECRET"

API_SECRET_ENV = "VINGA_API_SECRET"

@pytest.fixture(autouse=True)
def _environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A machine with the device-auth secret and nothing else: no
    config file, no API token, and a database directory that does not
    exist. The command may need none of them."""
    monkeypatch.delenv("VINGA_CONFIG", raising=False)
    monkeypatch.delenv(API_SECRET_ENV, raising=False)
    monkeypatch.delenv(cli.API_URL_ENV, raising=False)
    monkeypatch.setenv(AUTH_SECRET_ENV, SECRET)
    monkeypatch.setenv("VINGA_SERVER__DATABASE__DIR", str(tmp_path / "db"))


def _config_file(tmp_path: Path, body: str) -> str:
    path = tmp_path / "config.yaml"
    path.write_text(body, encoding="utf-8")
    return str(path)


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


def test_this_command_is_where_the_url_comes_from_now(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    """The banner used to print the same URL, which is what made this
    command a convenience. Since the PR #153 review it does not: the key
    stands in front of the endpoint that issues device tokens and a
    startup line is a retained record, so this command is the operator's
    route to the URL rather than one of two.

    The banner still names the origin the URL is built on, so the two
    cannot disagree about which deployment is being talked about, and it
    names this command."""
    path = _config_file(tmp_path, "server:\n  websocket_url: wss://voice.example/xiaozhi/v1/\n")

    assert cli.main(["--config", path, "ota-url"]) == 0
    printed = capsys.readouterr().out.strip()
    assert printed.endswith(f"/x/{KEY}/")

    with caplog.at_level("INFO"):
        onboarding.log_banner(load_file_config(path).server)
    (banner,) = [
        record
        for record in caplog.records
        if record.__dict__.get("event") == "onboarding_banner"
    ]

    assert KEY not in caplog.text
    assert printed not in caplog.text
    assert banner.origin == "https://voice.example"
    assert printed.startswith(banner.origin)
    assert "vinga-server config ota-url" in banner.getMessage()


def test_it_opens_no_socket_no_database_and_needs_no_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The point of the command: it answers before there is a server to
    ask, on a machine that may have no route to one."""

    def refuse(*args: object, **kwargs: object) -> object:
        raise AssertionError("ota-url reached for something it must not need")

    directory = tmp_path / "never-created"
    monkeypatch.setenv("VINGA_SERVER__DATABASE__DIR", str(directory))
    monkeypatch.setattr(cli, "build_client", refuse)
    monkeypatch.setattr(cli, "open_database", refuse)
    monkeypatch.setattr(socket, "socket", refuse)

    assert cli.main(["ota-url"]) == 0

    assert capsys.readouterr().out.strip().endswith(f"/x/{KEY}/")
    assert not directory.exists()


def test_the_url_is_alone_on_stdout_and_the_advice_is_not(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """So that `$(vinga-server config ota-url)` is the URL, and the
    guidance is still said."""
    assert cli.main(["ota-url"]) == 0

    captured = capsys.readouterr()
    assert captured.out.splitlines() == [f"http://0.0.0.0:8003/x/{KEY}/"]
    assert "captive portal" in captured.err
    assert "add-device" in captured.err


def test_the_guidance_promises_only_what_the_readme_does(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The guidance said the board "then shows a six-digit code", full
    stop, while the documented walkthrough sets a default agent, which
    covers every unknown board and produces no code at all. A person
    following both would have waited for a number that was never coming.

    Asserted against the README's own section rather than in isolation,
    because the failure was not a wrong sentence but two documents
    saying different things about one behavior.
    """
    section = (
        (Path(__file__).resolve().parents[2] / "README.md")
        .read_text(encoding="utf-8")
        .split("## Onboarding a device")[1]
        .split("\n## ")[0]
    )

    assert cli.main(["ota-url"]) == 0
    printed = capsys.readouterr().err

    # Conditional in both, and the condition named the same way.
    assert "If the board" in printed
    assert "if it shows any" in section
    for vocabulary in ("activation code", "default_agent", "add-device"):
        assert vocabulary in printed, vocabulary
        assert vocabulary in section, vocabulary
    # The promise that was there before.
    assert "The board then shows" not in printed


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
    monkeypatch.setenv("VINGA_OTHER_SECRET", "another-secret-entirely")
    path = _config_file(tmp_path, "server:\n  auth:\n    secret_env: VINGA_OTHER_SECRET\n")

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
