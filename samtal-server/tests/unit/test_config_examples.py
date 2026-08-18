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

`config.example.yaml` is the other committed example, and the same
argument applies to it from the other side: a field it does not mention
is a field an operator never learns exists, since this file is where
the server half is documented for a reader rather than for a generator.
So the last test here walks `ServerConfig` and insists the example
mentions every field of it.
"""

import contextlib
import io
import re
import sys
import typing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from samtal_server.config import cli
from samtal_server.config.api import build_api
from samtal_server.config.loader import load_file_config
from samtal_server.config.models import ServerConfig
from samtal_server.config.secrets import MASTER_KEY_ENV, generate_key
from tests.support.apps import mounted

SERVER = Path(__file__).resolve().parents[2]
EXAMPLES = SERVER / "examples"
EXAMPLE_CONFIG = SERVER / "config.example.yaml"

API_SECRET_ENV = "SAMTAL_API_SECRET"

TOKEN = "test-api-token-" + "0123456789abcdef" * 2

# The command a fragment's header names, as in
#   samtal-server config set provider llm claude -f examples/llm-anthropic.yaml
COMMAND = re.compile(r"^#\s+samtal-server config (set .+?) -f ")

# Providers, MCP servers and prompt fragments have to exist before
# anything references them: a write leaving a reference unresolved is
# refused, by design.
ORDER = ("provider", "mcp-server", "prompt-fragment", "agent-defaults", "agent")

# One key of the example configuration, as written or as commented out.
# The example's own convention is that a field whose default is right
# for a plain LAN deployment appears as a commented `# key:` line with
# the reasoning above it, so a commented line is coverage and a
# sentence of prose that happens to use the word is not.
KEY_LINE = re.compile(r"^(?P<indent> *)(?P<key>[A-Za-z_][A-Za-z0-9_]*):(?: |$)")
COMMENT_LINE = re.compile(r"^(?P<indent> *)# ?(?P<rest>.*)$")


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

    # What holds each command's application open. Since #142 the API's
    # engine is its lifespan's, and `TestClient` enters a lifespan only
    # as a context manager, so the client the entry point is handed is
    # entered here and released when the command that asked for it ends.
    lifespans = contextlib.ExitStack()

    def factory(base_url: str, token: str) -> TestClient:
        directory = load_file_config(None).server.database.dir
        # Mounted where the server mounts it, since that prefix is part
        # of the address the CLI resolves on its own.
        served = mounted(build_api(token, directory))
        return lifespans.enter_context(
            TestClient(
                served, base_url=base_url, headers={"Authorization": f"Bearer {token}"}
            )
        )

    monkeypatch.setattr(cli, "build_client", factory)

    def _run(*argv: str) -> int:
        nonlocal lifespans
        with contextlib.ExitStack() as this_command:
            lifespans = this_command
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
    assert "household (" in listed
    assert "assistant" in listed


def test_every_fragment_is_listed_in_the_examples_readme() -> None:
    """The README's table is how a reader finds these, so a new file
    that is not in it is invisible."""
    readme = (EXAMPLES / "README.md").read_text(encoding="utf-8")
    missing = [path.name for path in _fragments() if f"`{path.name}`" not in readme]
    assert not missing, f"not listed in examples/README.md: {', '.join(missing)}"


def _sections(annotation: object) -> list[type[BaseModel]]:
    """The nested settings models an annotation names, read out of the
    annotation itself: an optional section is a union, so `capture` and
    `conversations` are found the same way `auth` is, and a section
    added later is found without anyone remembering to list it."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation]
    return [section for argument in typing.get_args(annotation) for section in _sections(argument)]


def _leaves(model: type[BaseModel], prefix: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
    """Every leaf field path of a settings model, as a key path."""
    found: list[tuple[str, ...]] = []
    for name, field in model.model_fields.items():
        sections = _sections(field.annotation)
        if sections:
            for section in sections:
                found += _leaves(section, (*prefix, name))
        else:
            found.append((*prefix, name))
    return found


def _mentioned(text: str) -> set[tuple[str, ...]]:
    """Every key path the file writes, live or commented out, each at
    the depth it is written at. A comment marker and the one space
    after it are taken off before the line is read, which is how the
    file indents a commented-out section's fields (`# database:` with
    `#   dir:` under it), so nesting is checked on both kinds of line:
    a key at the wrong depth documents a field that does not exist
    there."""
    mentioned: set[tuple[str, ...]] = set()
    stack: list[tuple[int, str]] = []
    for raw in text.splitlines():
        hidden = COMMENT_LINE.match(raw)
        line = hidden.group("indent") + hidden.group("rest") if hidden else raw
        found = KEY_LINE.match(line)
        if not found:
            continue
        indent = len(found.group("indent"))
        while stack and stack[-1][0] >= indent:
            stack.pop()
        stack.append((indent, found.group("key")))
        mentioned.add(tuple(key for _, key in stack))
    return mentioned


def test_the_example_configuration_mentions_every_server_field() -> None:
    """The other half of the same argument: a field of `ServerConfig`
    that `config.example.yaml` does not mention is a field an operator
    reading the example never learns exists. Mentioning it is enough,
    since the file's convention is that a default worth keeping is
    shown as a commented `# key:` line with its reasoning; what does
    not count is prose using the word, which is why the scan wants the
    key form. The file holds `memory:` beside `server:`, and this pin
    is about the `server:` tree."""
    mentioned = _mentioned(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    leaves = _leaves(ServerConfig)

    assert len(leaves) > 20, "the field walk found almost nothing, so the check below is vacuous"
    missing = [".".join(leaf) for leaf in leaves if ("server", *leaf) not in mentioned]
    assert not missing, (
        "not in config.example.yaml, neither written nor commented out: "
        f"{', '.join(sorted(missing))}"
    )
