"""One act, run both ways, compared byte for byte.

The `--local` subset is the same act as the ordinary one, reached
without a server: it writes the same rows and reads the same views, so
it may not describe what it did differently. Since #139 both paths are
one row in the CLI's dispatch table, and what these tests hold is the
claim that row makes: for every act that has a break-glass path, the
acknowledgement and the notice it prints are the ones the API answers
the same act with, and a read prints the same document either way.

The comparison is not of whole invocations, and cannot be. Every
`--local` run prints the preamble on stderr first, by design, since
there is no reliable way to tell whether a server is running against
the same file and saying what this path is, is the honest substitute.
So the preamble is peeled off, spelled out here rather than read from
the module under test, and everything after it has to match the other
path exactly.

The acceptance suite (`test_config_cli.py`) pins the mutating acts'
stderr against the API's own answer for the same act, which is where
that proof was written and where it stays. What is added here is the
line on stdout, which no test compared between the paths, and the seven
reads, which had no such comparison at all: a read's whole output is
its answer, so for those the claim is the document itself.
"""

import io
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from samtal_server.config import cli
from samtal_server.config.api import build_api, mount_api
from samtal_server.config.loader import load_file_config
from samtal_server.config.secrets import MASTER_KEY_ENV, generate_key

API_SECRET_ENV = "SAMTAL_API_SECRET"

TOKEN = "test-api-token-" + "0123456789abcdef" * 2

# Not real credentials, and shaped so a substring check for one cannot
# match by accident.
SECRET = "sk-test-4f8b2c9e-never-a-real-credential"
OTHER_SECRET = "tok-test-7a1d3f60-never-a-real-credential"

# Printed by every --local invocation before the command runs. Spelled
# out rather than read from `cli`, for the reason the acceptance suite
# gives: comparing production against itself would let a preamble that
# made a timing claim of its own back in, since both sides of the
# comparison would move together.
LOCAL_PREAMBLE = (
    "--local is the break-glass path: it reads and writes the database directly, "
    "bypassing the configuration API. Each write says separately when it takes "
    "effect, the same answer the API gives for the same act."
)


@pytest.fixture
def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The entry point, against a server of this test's own, through the
    injected client seam every CLI suite uses."""
    monkeypatch.delenv("SAMTAL_CONFIG", raising=False)
    monkeypatch.delenv(cli.API_URL_ENV, raising=False)
    monkeypatch.setenv("SAMTAL_SERVER__DATABASE__DIR", str(tmp_path / "db"))
    monkeypatch.setenv(MASTER_KEY_ENV, generate_key())
    monkeypatch.setenv(API_SECRET_ENV, TOKEN)

    def factory(base_url: str, token: str) -> TestClient:
        directory = load_file_config(None).server.database.dir
        served = FastAPI()
        mount_api(served, build_api(TOKEN, directory))
        return TestClient(
            served, base_url=base_url, headers={"Authorization": f"Bearer {token}"}
        )

    monkeypatch.setattr(cli, "build_client", factory)

    def _run(*argv: str, stdin: str | None = None) -> int:
        if stdin is not None:
            monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
        return cli.main(list(argv))

    return _run


# What each act needs in the database before it can be run


def _a_provider(run) -> None:
    run(
        "set",
        "provider",
        "llm",
        "claude",
        "-f",
        "-",
        stdin="type: anthropic\nmodel: m\napi_key_env: ANTHROPIC_API_KEY\n",
    )


def _an_mcp_server(run) -> None:
    run("set", "mcp-server", "home", "-f", "-", stdin="transport: stdio\ncommand: uvx\n")


def _a_prompt_fragment(run) -> None:
    run("set", "prompt-fragment", "household", "-f", "-", stdin="text: The bins go out.\n")


def _an_unreferenced_agent(run) -> None:
    run("set", "agent", "sam", "-f", "-", stdin="prompt: You are Sam.\n")


def _the_agent_defaults(run) -> None:
    _a_provider(run)
    run("set", "agent-defaults", "-f", "-", stdin="llm: claude\n")


def _a_bound_device(run) -> None:
    _an_unreferenced_agent(run)
    run("bind-device", "aa:bb:cc:dd:ee:ff", "sam")


def _a_provider_secret(run) -> None:
    _a_provider(run)
    run("set-secret", "provider", "llm", "claude", "api_key", stdin=SECRET)


def _an_mcp_secret(run) -> None:
    _an_mcp_server(run)
    run("set-secret", "mcp-server", "home", "env.API_TOKEN", stdin=OTHER_SECRET)


def _everything(run) -> None:
    _a_provider_secret(run)
    _an_mcp_secret(run)
    _a_prompt_fragment(run)
    run("set", "agent-defaults", "-f", "-", stdin="llm: claude\n")
    _a_bound_device(run)
    run("set-default-agent", "sam")


# The mutating half: five deletes, and each secret command on each kind
# of entity a secret lives on, which is the whole `--local` write subset
# as the grammar stands.
MUTATIONS = [
    (_a_provider, ("delete", "provider", "llm", "claude")),
    (_an_mcp_server, ("delete", "mcp-server", "home")),
    (_a_prompt_fragment, ("delete", "prompt-fragment", "household")),
    (_an_unreferenced_agent, ("delete", "agent", "sam")),
    (_a_bound_device, ("delete", "device", "aa:bb:cc:dd:ee:ff")),
    (_a_provider, ("set-secret", "provider", "llm", "claude", "api_key")),
    (_an_mcp_server, ("set-secret", "mcp-server", "home", "env.API_TOKEN")),
    (_a_provider_secret, ("clear-secret", "provider", "llm", "claude", "api_key")),
    (_an_mcp_secret, ("clear-secret", "mcp-server", "home", "env.API_TOKEN")),
]


@pytest.mark.parametrize(
    ("seed", "argv"), MUTATIONS, ids=[" ".join(argv) for _, argv in MUTATIONS]
)
def test_a_local_write_acknowledges_what_the_api_acknowledges(
    run, capsys: pytest.CaptureFixture[str], seed, argv: tuple[str, ...]
) -> None:
    """The act run both ways against equivalent state: the line on
    stdout is the same line, and the notice under it is the same notice,
    with the preamble the only thing between them.

    Equivalent is established rather than assumed. A write naming only
    an entity's model-shaped columns leaves its stored secrets where
    they were, so seeding a provider again after a set-secret would make
    the second run a rotation where the first was a creation; the entity
    is taken out and seeded again between the runs. A delete has already
    left nothing behind.
    """
    typed = SECRET if argv[0] == "set-secret" else None

    seed(run)
    capsys.readouterr()
    assert run(*argv, stdin=typed) == 0
    answered = capsys.readouterr()

    if argv[0] != "delete":
        assert run("delete", *argv[1:-1]) == 0

    seed(run)
    capsys.readouterr()
    assert run("--local", *argv, stdin=typed) == 0

    said = capsys.readouterr()
    assert said.out == answered.out
    assert said.err.splitlines() == [LOCAL_PREAMBLE, *answered.err.splitlines()]


# The reading half, which has no acknowledgement: a read's whole output
# is its answer, so what the two paths have to agree on is the document.
READS = [
    (_a_provider_secret, ("show", "provider", "llm", "claude")),
    (_an_mcp_secret, ("show", "mcp-server", "home")),
    (_a_prompt_fragment, ("show", "prompt-fragment", "household")),
    (_an_unreferenced_agent, ("show", "agent", "sam")),
    (_the_agent_defaults, ("show", "agent-defaults")),
    (_a_bound_device, ("show", "device", "aa:bb:cc:dd:ee:ff")),
    (_everything, ("show",)),
]


@pytest.mark.parametrize(("seed", "argv"), READS, ids=[" ".join(argv) for _, argv in READS])
def test_a_local_read_shows_what_the_api_shows(
    run, capsys: pytest.CaptureFixture[str], seed, argv: tuple[str, ...]
) -> None:
    """One entity masked by the view the API answers with, and the whole
    configuration the same way. Nothing is re-seeded between the runs,
    because neither run changes anything: that a read leaves the
    database as it found it is part of what is being said."""
    seed(run)
    capsys.readouterr()

    assert run(*argv) == 0
    answered = capsys.readouterr()

    assert run("--local", *argv) == 0

    said = capsys.readouterr()
    assert said.out == answered.out
    # A read makes no claim about when anything applies, so the preamble
    # is the whole of what the break-glass path adds.
    assert said.err.splitlines() == [LOCAL_PREAMBLE]
    assert answered.err == ""


def test_the_masked_values_are_masked_on_both_paths(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The control for the comparison above: two paths printing the same
    document prove nothing if the document is empty of the thing worth
    checking. Every read compared here is of an entity holding a stored
    secret, and this says what that looks like."""
    _a_provider_secret(run)
    capsys.readouterr()

    assert run("--local", "show", "provider", "llm", "claude") == 0

    shown = capsys.readouterr().out
    assert "api_key: ********" in shown
    assert "used instead of api_key_env: ANTHROPIC_API_KEY" in shown
    assert SECRET not in shown
