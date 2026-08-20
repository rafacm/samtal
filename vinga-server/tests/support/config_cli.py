"""What every `vinga-server config` suite runs a command with.

The config command group's tests were one file until #139 split them
along the boundaries that issue produced, and six suites now drive the
same entry point: the acceptance spine, the transport client, the
runtime renderings, the secrets, the grammar, and the break-glass path.
What they share is the scaffolding, and it lives here rather than in
whichever of them happened to be written first.

`runner` is the whole of it: one command run the way the entry point
runs it, against a server of the calling test's own. It is a factory
rather than a fixture because a fixture cannot be imported and used as a
fixture, and a `run` visible to the whole unit lane would be a name
nobody could place; each suite spends four lines wrapping it instead.

The sentinels are here for the same reason: a substring assertion about
a credential is worth nothing if the credential it looks for is not the
one the command was given, and six copies of a constant is six chances
for that.
"""

import contextlib
import io
import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import yaml
from fastapi.testclient import TestClient

from tests.support.apps import mounted
from vinga_server.config import cli
from vinga_server.config.api import MOUNT_PATH, build_api
from vinga_server.config.loader import load_file_config
from vinga_server.config.secrets import MASTER_KEY_ENV, generate_key
from vinga_server.onboarding import PendingDevices

# Not real credentials, and shaped so a substring check for one cannot
# match by accident.
SECRET = "sk-test-4f8b2c9e-never-a-real-credential"
OTHER_SECRET = "tok-test-7a1d3f60-never-a-real-credential"

API_SECRET_ENV = "VINGA_API_SECRET"

TOKEN = "test-api-token-" + "0123456789abcdef" * 2

# Leading indentation, an inner blank line and a trailing newline, none
# of which a round trip through YAML, HTTP and the database may tidy up.
FRAGMENT_TEXT = "  The bins go out on Tuesday.\n\n    The radio is called Bosse.\n"

# The fragment exactly as an operator writes it: one key, a literal
# block, and the explicit indentation indicator that is what makes a
# body whose own first line is indented writable in YAML at all.
FRAGMENT_INPUT = "text: |2\n" + "".join(
    f"  {line}\n" if line else "\n" for line in FRAGMENT_TEXT.splitlines()
)


def runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Run one command the way the entry point runs it, against a server
    of this test's own.

    The application is built per request rather than once, from the
    database directory the CLI itself would have resolved, because that
    is what a deployment's server does too: the CLI and the server read
    `server.database.dir` through the same machinery and cannot disagree
    about it.

    Each application is served for exactly the length of one command:
    since #142 the configuration API owns a database engine, opened by
    the lifespan `TestClient` enters as a context manager and disposed
    when it leaves, so a client built and never entered would meet the
    API with no engine at all. One command is the right length because it
    is how long the application itself lasts here.
    """
    monkeypatch.delenv("VINGA_CONFIG", raising=False)
    monkeypatch.delenv(cli.API_URL_ENV, raising=False)
    monkeypatch.setenv("VINGA_SERVER__DATABASE__DIR", str(tmp_path / "db"))
    monkeypatch.setenv(MASTER_KEY_ENV, generate_key())
    monkeypatch.setenv(API_SECRET_ENV, TOKEN)

    reached: list[str] = []
    # One table across the whole test, unlike the application, which is
    # built per request: on a deployment this is state of the running
    # server, and a command that could not see what a previous one left
    # in it would be testing a table nobody has.
    pending = PendingDevices()
    # The running server's MCP managers, for the same reason and put
    # there by the tests that need one. None is what an application
    # built without a server around it gets, which is what every other
    # test here is.
    runtime: dict[str, object] = {
        "mcp_servers": None,
        "reload": None,
        "agent_prompt": None,
    }
    # Every client the entry point built, kept so a test can read the
    # timeouts a command chose after it has run.
    clients: list[TestClient] = []
    # What holds the clients one command builds open, replaced by `_run`
    # with a fresh one per command and closed when that command ends.
    lifespans = contextlib.ExitStack()

    def factory(base_url: str, token: str) -> TestClient:
        reached.append(base_url)
        directory = load_file_config(None).server.database.dir
        # The server's token is fixed here and is not the one the CLI
        # resolved. Building the gate out of whatever the client happened
        # to send would make every token the right one, and the
        # token-resolution tests would be asserting nothing: the wrong
        # variable, a stale value and a typo would all authenticate.
        api = build_api(
            TOKEN,
            directory,
            pending=pending,
            mcp_servers=runtime["mcp_servers"],
            reload=runtime["reload"],
            agent_prompt=runtime["agent_prompt"],
        )
        # A base URL with a path prefix is the deployed shape, where the
        # sub-application is mounted on the server's own port, so the
        # fixture mounts it exactly where the server does rather than
        # serving it at the root and letting the prefix go nowhere.
        served = api
        if urlsplit(base_url).path.rstrip("/"):
            assert urlsplit(base_url).path.rstrip("/") == MOUNT_PATH
            served = mounted(api)
        client = lifespans.enter_context(
            TestClient(
                served,
                base_url=base_url,
                headers={"Authorization": f"Bearer {token}"},
            )
        )
        clients.append(client)
        return client

    monkeypatch.setattr(cli, "build_client", factory)

    def _run(*argv: str, stdin: str | None = None) -> int:
        nonlocal lifespans
        if stdin is not None:
            monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
        with contextlib.ExitStack() as this_command:
            lifespans = this_command
            return cli.main(list(argv))

    _run.reached = reached
    _run.pending = pending
    _run.runtime = runtime
    _run.clients = clients
    return _run


def chain(exc: BaseException) -> str:
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


def document(out: str) -> object:
    """A `show` document without the secret notes underneath it."""
    return yaml.safe_load("\n".join(line for line in out.splitlines() if not line.startswith("#")))


def showing(run, mac: str = "aa:bb:cc:dd:ee:ff") -> str:
    """One device waiting, put there the way the OTA endpoint puts it."""
    return run.pending.observe(
        mac,
        "6f1a2b3c-4d5e-6f70-8192-a3b4c5d6e7f8",
        "waveshare-esp32-s3-touch-lcd-1.54",
        "2.4.0",
    ).device.code
