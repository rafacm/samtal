"""The committed example fragments, fed through the CLI they document.

A fragment nobody can install is worse than no fragment: it reads like
working documentation. So every file under `examples/` is run through
the command its own header names, in the creation order the reference
checks require, against a real sub-application over a scratch database.
The header comment is the input, which also pins that the command a
reader would copy is the command that works.

The deployment profile is checked the same way and for the same reason,
but it is a script that documents itself as running against a running
server, so it runs in the integration lane where there is one:
`tests/integration/test_config_examples.py`.
"""

import io
import re
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from samtal_server.config import cli
from samtal_server.config.api import build_api, mount_api
from samtal_server.config.loader import load_file_config
from samtal_server.config.secrets import MASTER_KEY_ENV, generate_key

SERVER = Path(__file__).resolve().parents[2]
EXAMPLES = SERVER / "examples"

API_SECRET_ENV = "SAMTAL_API_SECRET"

TOKEN = "test-api-token-" + "0123456789abcdef" * 2

# The command a fragment's header names, as in
#   samtal-server config set provider llm claude -f examples/llm-anthropic.yaml
COMMAND = re.compile(r"^#\s+samtal-server config (set .+?) -f ")

# Providers and MCP servers have to exist before anything references
# them: a write leaving a reference unresolved is refused, by design.
ORDER = ("provider", "mcp-server", "agent-defaults", "agent")


def _fragments() -> list[Path]:
    return sorted(EXAMPLES.glob("*.yaml"))


def _command(fragment: Path) -> list[str]:
    """The first `config set` line in the file's header comment. A file
    may name more than one (the same voice provider installed twice
    under two names); the first is the one this runs."""
    for line in fragment.read_text(encoding="utf-8").splitlines():
        found = COMMAND.match(line)
        if found:
            return found.group(1).split()
    raise AssertionError(f"{fragment.name} names no `samtal-server config set` command")


@pytest.fixture
def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The CLI as a deployment runs it, against a server of this test's
    own: the same injected client seam the acceptance suite uses, so a
    fragment is installed through CLI parsing, HTTP and the repository
    rather than through a shortcut none of them takes."""
    monkeypatch.delenv("SAMTAL_CONFIG", raising=False)
    monkeypatch.delenv(cli.API_URL_ENV, raising=False)
    monkeypatch.setenv("SAMTAL_SERVER__DATABASE__DIR", str(tmp_path / "db"))
    monkeypatch.setenv(MASTER_KEY_ENV, generate_key())
    monkeypatch.setenv(API_SECRET_ENV, TOKEN)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))

    def factory(base_url: str, token: str) -> TestClient:
        directory = load_file_config(None).server.database.dir
        # Mounted where the server mounts it, since that prefix is part
        # of the address the CLI resolves on its own.
        served = FastAPI()
        mount_api(served, build_api(token, directory))
        return TestClient(
            served, base_url=base_url, headers={"Authorization": f"Bearer {token}"}
        )

    monkeypatch.setattr(cli, "build_client", factory)

    def _run(*argv: str) -> int:
        return cli.main(list(argv))

    return _run


def test_there_are_fragments_to_check() -> None:
    """A glob that matched nothing would make every assertion below
    vacuous, which is the failure mode a directory rename produces."""
    assert len(_fragments()) >= 10


def test_every_fragment_installs_through_the_command_it_names(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    ordered = sorted(_fragments(), key=lambda path: ORDER.index(_command(path)[1]))

    for fragment in ordered:
        argv = [*_command(fragment), "-f", str(fragment)]
        assert run(*argv) == 0, f"{fragment.name}: {capsys.readouterr().err}"

    capsys.readouterr()
    assert run("list") == 0
    listed = capsys.readouterr().out
    assert "claude (anthropic)" in listed
    assert "home (stdio)" in listed
    assert "assistant" in listed


def test_every_fragment_is_listed_in_the_examples_readme() -> None:
    """The README's table is how a reader finds these, so a new file
    that is not in it is invisible."""
    readme = (EXAMPLES / "README.md").read_text(encoding="utf-8")
    missing = [path.name for path in _fragments() if f"`{path.name}`" not in readme]
    assert not missing, f"not listed in examples/README.md: {', '.join(missing)}"
