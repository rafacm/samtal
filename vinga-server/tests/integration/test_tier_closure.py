"""What the default install carries, and what it must not.

The whole of this milestone's claim, proven in an environment rather
than asserted about one. Three doors lead into this package and each is
exercised here:

- the **client** door, which is `uvx --from git+...` on a laptop: the
  project installed with no extras at all, which must carry the
  configuration grammar and must carry none of the server;
- the **server** door, which is the image build: the same project with
  `[serve]`, which must boot;
- the **contributor** door, which is `cd vinga-server && uv sync`: the
  project with its default groups, which must yield a runnable server
  with no new flags. It is the one door a mistake in is invisible to
  every other lane, because every other lane names its tier explicitly,
  and this file is the guard the plan names for it.

**The proof is a closure, not three named probes.** A negative check
that reaches for `fastapi`, `sqlalchemy` and `cryptography` by name
passes the day somebody adds a fourth heavy dependency. So the expected
sets are derived from `pyproject.toml`'s own tiers, and the assertion is
over the whole installed distribution set: every client dependency
present, every serve-only distribution absent, and every serve-only
top-level module unimportable.

**Every command is run, not imported.** Importing `cli` from a client
install proves nothing about a command whose heavy import sits inside
its own arm, which is exactly what `openapi` and `ota-url` are. So the
grammar's own inventory is split in two here, the two gated commands
against everything else, and both sides are invoked as subprocesses of
the installed binary. M3 widens that to the full registered inventory
against a live server; what is here is the tier, and a command that
moved between the two sets fails from whichever side it left.

The environments are built once for the whole module and reused, which
is what keeps this affordable: two `uv venv` calls and two installs.
"""

import json
import os
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest

from tests.support.config_cli import registered
from vinga_server.config import cli

PROJECT = Path(__file__).resolve().parents[2]

# Which top-level module each serve-only distribution installs, so the
# negative half can ask the interpreter rather than only the metadata. A
# distribution can be absent from the metadata and its module still be
# importable, because something else vendored or depended on it, and
# that is the case a name check alone would miss.
#
# Written out rather than derived: a distribution's import name is not
# in its requirement string, and guessing it by replacing hyphens is how
# a typo becomes a check that always passes.
SERVE_MODULES = {
    "alembic": "alembic",
    "anthropic": "anthropic",
    "av": "av",
    "cryptography": "cryptography",
    "fastapi": "fastapi",
    "mcp": "mcp",
    "openai": "openai",
    "pysilero-vad": "pysilero_vad",
    "sqlalchemy": "sqlalchemy",
    "uvicorn": "uvicorn",
}

# The two commands the grammar keeps and the client half cannot answer.
# Named here as the expected inventory rather than discovered, because
# the assertion below is two-way: a third gated command fails this lane
# from the side it joined.
GATED = frozenset({("openapi",), ("ota-url",)})


def _requirement_names(entries: Sequence[str]) -> set[str]:
    """The distribution names out of a list of requirement strings,
    normalized the way an installed environment reports them."""
    names = set()
    for entry in entries:
        name = entry.split(";")[0].split("[")[0]
        for marker in ("==", ">=", "<=", "~=", "!=", ">", "<"):
            name = name.split(marker)[0]
        names.add(name.strip().lower().replace("_", "-"))
    return names


@pytest.fixture(scope="module")
def tiers() -> tuple[set[str], set[str]]:
    """The two tiers, read off the project's own declaration.

    Read rather than restated, which is what makes this a closure: a
    dependency moved between the tiers moves both halves of the
    assertion at once, and a dependency added to neither is a
    dependency this lane will notice.
    """
    declared = tomllib.loads((PROJECT / "pyproject.toml").read_text(encoding="utf-8"))
    client = _requirement_names(declared["project"]["dependencies"])
    serve = _requirement_names(declared["project"]["optional-dependencies"]["serve"])
    return client, serve


def _venv(where: Path, *extras: str) -> Path:
    """A clean environment with this project installed into it, and the
    path of its interpreter.

    Installed from the project directory rather than from a built wheel,
    which is the difference between this lane and M3's: what is being
    proven here is the declaration, and the artifact that carries it is
    M3's subject.
    """
    if shutil.which("uv") is None:  # pragma: no cover - uv is how this repo runs
        pytest.skip("uv is not on PATH, and it is what builds an environment here")
    subprocess.run(
        ["uv", "venv", "--python", "3.12", str(where)],
        check=True,
        capture_output=True,
        text=True,
    )
    python = where / "bin" / "python"
    target = f"{PROJECT}[{','.join(extras)}]" if extras else str(PROJECT)
    subprocess.run(
        ["uv", "pip", "install", "--python", str(python), target],
        check=True,
        capture_output=True,
        text=True,
    )
    return python


@pytest.fixture(scope="module")
def client_env(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The laptop door: no extras at all."""
    return _venv(tmp_path_factory.mktemp("client") / "venv")


@pytest.fixture(scope="module")
def serve_env(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The image-build door: the same project with `[serve]`."""
    return _venv(tmp_path_factory.mktemp("serve") / "venv", "serve")


def _installed(python: Path) -> set[str]:
    """Every distribution the environment holds, as it reports itself."""
    finished = subprocess.run(
        [
            str(python),
            "-c",
            "import json,sys;from importlib.metadata import distributions;"
            "sys.stdout.write(json.dumps(sorted("
            "d.metadata['Name'].lower().replace('_','-') for d in distributions())))",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return set(json.loads(finished.stdout))


def _ran(python: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    """One command of the installed grammar, run as the binary it is.

    Outside the checkout and with `PYTHONPATH` and its relatives
    scrubbed, so `vinga_server` can only resolve to what this
    environment installed. The source tree makes every module importable
    whether it was installed or not, which is the one way a lane like
    this can quietly test nothing.
    """
    environment = dict(os.environ)
    for name in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"):
        environment.pop(name, None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [str(python.parent / argv[0]), *argv[1:]],
        cwd=python.parent,
        env=environment,
        capture_output=True,
        text=True,
    )


# The client tier


def test_the_client_install_resolves_to_itself(client_env: Path) -> None:
    """Provenance, asserted before anything else is: a lane run from
    inside the checkout can import the tree it was built from and prove
    nothing at all."""
    finished = _ran(client_env, "python", "-c", "import vinga_server;print(vinga_server.__file__)")

    assert finished.returncode == 0, finished.stderr
    assert "site-packages" in finished.stdout, finished.stdout
    assert str(PROJECT / "src") not in finished.stdout


def test_the_client_install_carries_every_client_dependency(
    client_env: Path, tiers: tuple[set[str], set[str]]
) -> None:
    client, _ = tiers
    assert client <= _installed(client_env)


def test_the_client_install_carries_no_serve_distribution(
    client_env: Path, tiers: tuple[set[str], set[str]]
) -> None:
    """The negative half of the closure, over the whole declared serve
    tier rather than over three names somebody remembered."""
    _, serve = tiers
    assert serve & _installed(client_env) == set()


def test_the_serve_modules_are_not_importable_from_the_client_install(
    client_env: Path, tiers: tuple[set[str], set[str]]
) -> None:
    """And the same question asked of the interpreter, because a
    distribution can be absent from the metadata while its module is
    importable through something else that vendored it."""
    _, serve = tiers
    assert set(SERVE_MODULES) == serve, "the import-name map has drifted from the tier"

    for module in sorted(SERVE_MODULES.values()):
        finished = _ran(client_env, "python", "-c", f"import {module}")
        assert finished.returncode != 0, f"{module} is importable from the client install"


def test_the_client_install_imports_the_cli(client_env: Path) -> None:
    """The risk the closure exists for: a heavy import left at module
    scope turns the client install into an ImportError at first use, and
    importing the package alone would not find it."""
    finished = _ran(client_env, "python", "-c", "import vinga_server.config.cli")

    assert finished.returncode == 0, finished.stderr


def test_the_binary_answers_from_the_client_install(client_env: Path) -> None:
    finished = _ran(client_env, "vinga", "--version")

    assert finished.returncode == 0, finished.stderr
    assert "vinga-server" in finished.stdout


# The grammar, run rather than imported


def test_every_ungated_command_has_a_help_page_from_the_client_install(
    client_env: Path,
) -> None:
    """Every row of the registration table, run as a subprocess.

    `--help` and not the act itself, because the act needs a server and
    the tier is what is being proven here; M3's lane drives the acts
    against a live one. What this catches is the failure this
    milestone's moves could have caused: a command whose declaration or
    whose module-scope import reaches the server half fails before it
    prints anything, and importing `cli` would not have found it.
    """
    for row in cli.COMMANDS:
        if row.words in GATED:
            continue
        finished = _ran(client_env, "vinga", *row.words, "--help")
        assert finished.returncode == 0, (row.words, finished.stderr)
        assert finished.stdout.strip(), row.words


def test_the_gated_commands_refuse_from_the_client_install(client_env: Path) -> None:
    """The other side of the same inventory. They are in the grammar, so
    they parse; they need the server half, so they refuse; and they
    refuse with the sentence rather than with an ImportError."""
    for words in sorted(GATED):
        finished = _ran(client_env, "vinga", *words)
        assert finished.returncode == 1, (words, finished.stdout, finished.stderr)
        assert finished.stderr.strip() == cli.NEEDS_THE_SERVER_HALF, words
        assert finished.stdout == "", words
        assert "Traceback" not in finished.stderr, words


def test_the_gated_pair_is_what_the_table_says_it_is() -> None:
    """The inventory held closed against the registration table, so a
    command that left the gated set fails from the side it left."""
    assert GATED <= {row.words for row in cli.COMMANDS}
    for words in GATED:
        assert registered(list(words)) == words


# The serve tier


def test_the_serve_install_carries_both_tiers(
    serve_env: Path, tiers: tuple[set[str], set[str]]
) -> None:
    client, serve = tiers
    assert client | serve <= _installed(serve_env)


def test_the_serve_install_can_be_asked_to_serve(serve_env: Path) -> None:
    """The image-build door, proven at the one place it differs from the
    client: `vinga-server` with no command word reaches serving rather
    than the sentence that says it cannot.

    It is asked with no configuration and no secret, so it refuses at
    the boot rather than binding a port, which is what makes this cheap
    and still an assertion about the serve path: the refusal it gives is
    the boot's, and the client install cannot reach it at all.
    """
    finished = _ran(serve_env, "vinga-server")

    assert finished.returncode == 1, finished.stdout
    from vinga_server.main import CANNOT_SERVE

    assert CANNOT_SERVE not in finished.stderr
    assert finished.stderr.strip(), "the serve path said nothing at all"


def test_the_client_install_cannot_be_asked_to_serve(client_env: Path) -> None:
    """And the same invocation from the other tier, which is the one
    sentence a person meets when they installed the client and typed the
    server's name."""
    finished = _ran(client_env, "vinga-server")

    from vinga_server.main import CANNOT_SERVE

    assert finished.returncode == 1, finished.stdout
    assert finished.stderr.strip() == CANNOT_SERVE
    assert "Traceback" not in finished.stderr


# The contributor door


@pytest.fixture(scope="module")
def synced(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """What `cd vinga-server && uv sync` produces, in an environment of
    this test's own rather than in the checkout's `.venv`.

    `UV_PROJECT_ENVIRONMENT` points the sync somewhere else, so running
    this lane cannot disturb the environment it is itself running in.
    """
    if shutil.which("uv") is None:  # pragma: no cover - uv is how this repo runs
        pytest.skip("uv is not on PATH, and it is what a contributor syncs with")
    where = tmp_path_factory.mktemp("contributor") / "venv"
    environment = dict(os.environ) | {"UV_PROJECT_ENVIRONMENT": str(where)}
    environment.pop("VIRTUAL_ENV", None)
    subprocess.run(
        ["uv", "sync", "--frozen"],
        cwd=PROJECT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    yield where / "bin" / "python"


def test_a_plain_sync_still_yields_a_runnable_server(
    synced: Path, tiers: tuple[set[str], set[str]]
) -> None:
    """The contributor door, which is the one this milestone could have
    broken silently.

    `uv sync` installs the default groups and the project but not its
    extras, so the serve extra rides the dev group. Nothing else would
    notice if that entry went away: every other lane names its tier.
    So the whole serve tier is asserted present, and the entry point is
    asked to serve, which is the shape a contributor meets.
    """
    _, serve = tiers
    assert serve <= _installed(synced)

    finished = _ran(synced, "vinga-server")

    from vinga_server.main import CANNOT_SERVE

    assert CANNOT_SERVE not in finished.stderr
    assert finished.returncode == 1, finished.stdout


def test_the_sync_command_in_agents_md_is_the_one_that_is_proven() -> None:
    """The row that is a proof rather than an edit.

    AGENTS.md's Commands section says `uv sync`, and #223's ruling is
    that it must not have to change. The fixture above runs exactly that
    string, so the claim and the check cannot come apart; this case is
    what says the string is still the one written down.
    """
    commands = (PROJECT.parent / "AGENTS.md").read_text(encoding="utf-8")
    assert "\nuv sync  " in commands
    assert "uv sync --extra serve" not in commands
    assert "[serve]" not in commands


if __name__ == "__main__":  # pragma: no cover - a hand run of one lane
    sys.exit(pytest.main([__file__, "-v"]))
