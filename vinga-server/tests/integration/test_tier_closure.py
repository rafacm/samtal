"""What the default install carries, and what it must not.

The whole of this milestone's claim, proven in an environment rather
than asserted about one. Three doors lead into this package and each is
exercised here:

- the **client** door, which is `uvx --from git+...` on a laptop: the
  project installed with no extras at all, which must carry the
  configuration grammar and must carry none of the server. It must also
  carry the half of `doctor` a laptop uses, which is diagnosing a URL
  it was given; deriving one is the server's half and refuses;
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
the installed binary. The `vinga-server` entry point's own gated
sibling, the conversations group, is driven here too: it is outside the
grammar's tree, so nothing about `cli.COMMANDS` would ever reach it.
M3 widens that to the full registered inventory
against a live server; what is here is the tier, and a command that
moved between the two sets fails from whichever side it left.

**The closure is computed, not enumerated.** The expected set for each
tier is the recursive walk of `uv.lock` from that tier's roots, extras
and markers included, and the installed set is compared to it exactly
in both directions. A subset check would pass a transitive distribution
nobody declared, which is the shape a heavy dependency comes back in.
The six direct client names stay beside it as an independent oracle
read from `pyproject.toml`, so the lock is checked against something
that did not come from the lock.

The environments are built once for the whole module and reused, and
they are built with `uv sync --frozen` rather than `uv pip install`
because only the first installs what the lock says: the second
re-resolves from the index, and an environment built that way cannot be
compared exactly to a graph it did not come from.
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
from packaging.markers import Marker

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
    """The two tiers' DIRECT dependencies, read off `pyproject.toml`.

    The independent oracle, kept beside the lock closure below rather
    than derived from it. Six names and ten, written by hand in the
    declaration under test, so a closure computed from a lock this
    repository also wrote is checked against something that came from
    somewhere else. Either alone would be a graph agreeing with itself.
    """
    declared = tomllib.loads((PROJECT / "pyproject.toml").read_text(encoding="utf-8"))
    client = _requirement_names(declared["project"]["dependencies"])
    serve = _requirement_names(declared["project"]["optional-dependencies"]["serve"])
    return client, serve


# The closure, computed from the lock
#
# The whole of finding 3 of this PR's review round. The lane used to
# check that the six direct client names were a subset of what was
# installed and that the ten direct serve names were not among it,
# which says nothing at all about a transitive distribution: FastAPI
# arriving under a new name, or SQLAlchemy pulled in by something that
# grew a dependency on it, passed both halves. So the recursive closure
# is computed from `uv.lock`, extras and markers included, and the
# installed set is compared to it EXACTLY, both ways.

# What the environment reports about itself, in PEP 508's own names,
# printed by the interpreter being asked about. Read from the child
# rather than from this process, because a marker is evaluated against
# the environment it is being installed into; stdlib only, because the
# client tier has nothing else.
_ENVIRONMENT_SOURCE = """
import json, os, platform, sys

print(json.dumps({
    "implementation_name": sys.implementation.name,
    "implementation_version": platform.python_version(),
    "os_name": os.name,
    "platform_machine": platform.machine(),
    "platform_release": platform.release(),
    "platform_system": platform.system(),
    "platform_version": platform.version(),
    "python_full_version": platform.python_version(),
    "platform_python_implementation": platform.python_implementation(),
    "python_version": ".".join(platform.python_version_tuple()[:2]),
    "sys_platform": sys.platform,
}))
"""


def _normalized(name: str) -> str:
    """A distribution name as both sides of the comparison spell it."""
    return name.lower().replace("_", "-")


def _marker_environment(python: Path) -> dict[str, str]:
    finished = subprocess.run(
        [str(python), "-c", _ENVIRONMENT_SOURCE], check=True, capture_output=True, text=True
    )
    environment: dict[str, str] = json.loads(finished.stdout)
    return environment


@pytest.fixture(scope="module")
def locked() -> dict[str, dict[str, object]]:
    """Every package `uv.lock` resolves, by its normalized name."""
    lock = tomllib.loads((PROJECT / "uv.lock").read_text(encoding="utf-8"))
    return {_normalized(package["name"]): package for package in lock["package"]}


def _closure(
    packages: dict[str, dict[str, object]],
    roots: Sequence[tuple[str, str]],
    environment: dict[str, str],
) -> set[str]:
    """Every distribution reachable from these roots, transitively.

    A root and a step are both `(name, extra)`, because an extra is not
    a property of a package but of the way it was asked for:
    `uvicorn[standard]` installs uvicorn's own dependencies AND its
    `standard` group, and the same package reached bare elsewhere
    installs only the first. Walking pairs rather than names is what
    keeps a package reached both ways from losing half its edges.

    A marker is evaluated against the target environment, with `extra`
    bound to the one being resolved, so a dependency this platform does
    not take is not expected to be installed on it.
    """
    seen: set[tuple[str, str]] = set()
    reached: set[str] = set()
    work = list(roots)
    while work:
        name, extra = work.pop()
        name = _normalized(name)
        if (name, extra) in seen:
            continue
        seen.add((name, extra))
        reached.add(name)
        package = packages.get(name)
        if package is None:
            continue
        entries = list(package.get("dependencies", []))
        if extra:
            entries += package.get("optional-dependencies", {}).get(extra, [])
        for entry in entries:
            marker = entry.get("marker")
            if marker and not Marker(marker).evaluate({**environment, "extra": extra or ""}):
                continue
            for wanted in entry.get("extra") or [""]:
                work.append((entry["name"], wanted))
    return reached


def _tier_closure(
    packages: dict[str, dict[str, object]], environment: dict[str, str], *extras: str
) -> set[str]:
    """What the lock says one tier of this project installs, the project
    itself included."""
    project = packages["vinga-server"]
    roots = [
        (entry["name"], wanted)
        for entry in project["dependencies"]
        for wanted in entry.get("extra") or [""]
    ]
    for extra in extras:
        roots += [
            (entry["name"], wanted)
            for entry in project["optional-dependencies"][extra]
            for wanted in entry.get("extra") or [""]
        ]
    return _closure(packages, roots, environment) | {"vinga-server"}


def _synced(where: Path, *extras: str) -> Path:
    """A clean environment holding exactly what the lock says this tier
    is, and the path of its interpreter.

    `uv sync --frozen` and not `uv pip install`, and the difference is
    the reason this lane can compare anything exactly: `uv pip install`
    re-resolves from the index, so it can install a newer release whose
    own requirements differ from the locked ones, and an environment
    built that way cannot be held to a graph it did not come from. It
    is also what the image build and a contributor run, so the tier
    being proven is the tier that ships.

    `--no-dev`, because the dev group pulls the serve extra and would
    make every tier the same tier. `--no-editable`, so the project is
    installed rather than linked and the provenance assertion below has
    something to be true of.
    """
    if shutil.which("uv") is None:  # pragma: no cover - uv is how this repo runs
        pytest.skip("uv is not on PATH, and it is what builds an environment here")
    environment = dict(os.environ) | {"UV_PROJECT_ENVIRONMENT": str(where)}
    environment.pop("VIRTUAL_ENV", None)
    subprocess.run(
        ["uv", "sync", "--frozen", "--no-dev", "--no-editable", *extras],
        cwd=PROJECT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    return where / "bin" / "python"


@pytest.fixture(scope="module")
def client_env(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The laptop door: no extras at all."""
    return _synced(tmp_path_factory.mktemp("client") / "venv")


@pytest.fixture(scope="module")
def serve_env(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The image-build door: the same project with `[serve]`."""
    return _synced(tmp_path_factory.mktemp("serve") / "venv", "--extra", "serve")


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


def test_the_client_install_is_exactly_the_locked_client_closure(
    client_env: Path, locked: dict[str, dict[str, object]]
) -> None:
    """The closure, compared exactly and in both directions.

    Exactly is the whole point. A subset check says every name it was
    given is present and nothing about the names it was not given, so a
    transitive distribution nobody declared passes it, and that is the
    shape a heavy dependency comes back in: not `fastapi` written into
    `pyproject.toml`, but something small growing a dependency on it.

    Both directions, because each is a different failure. Something
    installed that the lock does not reach is a dependency arriving
    from nowhere; something reached that is not installed is a closure
    that has stopped describing this environment, and an oracle that
    describes nothing is worse than none.
    """
    expected = _tier_closure(locked, _marker_environment(client_env))

    assert _installed(client_env) == expected


def test_the_serve_install_is_exactly_the_locked_serve_closure(
    serve_env: Path, locked: dict[str, dict[str, object]]
) -> None:
    expected = _tier_closure(locked, _marker_environment(serve_env), "serve")

    assert _installed(serve_env) == expected


def test_a_distribution_nobody_declared_would_turn_this_lane_red(
    client_env: Path, locked: dict[str, dict[str, object]]
) -> None:
    """The bite, proven rather than asserted.

    A comparison is only worth what it rejects, and the one above
    replaced a subset check that rejected almost nothing. So the
    expected set is doctored in each direction and the comparison is
    asked about it: one extra name and one missing name must both fail.
    """
    expected = _tier_closure(locked, _marker_environment(client_env))
    installed = _installed(client_env)

    assert installed != expected | {"a-transitive-distribution-nobody-declared"}
    assert installed != expected - {"httpx"}
    # And the undoctored comparison is the one that holds, so the two
    # above are failing for the reason they claim.
    assert installed == expected


def test_the_client_install_carries_every_client_dependency(
    client_env: Path, tiers: tuple[set[str], set[str]]
) -> None:
    """The independent oracle: the six names written by hand in
    `pyproject.toml`, checked against the environment without going
    through the lock at all."""
    client, _ = tiers
    assert len(client) == 6, client
    assert client <= _installed(client_env)


def test_the_client_install_carries_no_serve_distribution(
    client_env: Path, tiers: tuple[set[str], set[str]]
) -> None:
    """And the ten, by name. Implied by the exact comparison above and
    kept anyway: this is the sentence the milestone claims, and a
    reader of a failure should not have to diff two closures to see
    that FastAPI came back."""
    _, serve = tiers
    assert len(serve) == 10, serve
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


def test_the_conversations_group_refuses_from_the_client_install(client_env: Path) -> None:
    """The third gated site, and the only one that is not a row of the
    grammar.

    It renders the conversation store's tables off the SQLAlchemy
    metadata, so a client install cannot answer it; without the gate it
    ended in a `ModuleNotFoundError` traceback on the one entry point
    whose every other answer is a sentence.
    """
    finished = _ran(client_env, "vinga-server", "conversations", "schema")

    assert finished.returncode == 1, (finished.stdout, finished.stderr)
    assert finished.stderr.strip() == cli.NEEDS_THE_SERVER_HALF
    assert finished.stdout == ""
    assert "Traceback" not in finished.stderr


def test_the_client_half_groups_still_answer_from_the_client_install(
    client_env: Path,
) -> None:
    """And its sibling that is the client half, which must NOT be
    gated: `events reference` renders the event registry, and an
    installation that has this entry point has it."""
    events = _ran(client_env, "vinga-server", "events", "reference")
    assert events.returncode == 0, events.stderr
    assert events.stdout.strip()


def test_the_doctor_diagnoses_a_given_url_from_the_client_install(
    client_env: Path,
) -> None:
    """The half a laptop needs, driven for real rather than through
    `--help`.

    A workstation diagnosing a remote deployment passes the URL: it
    opens a socket, reads what answers, and wants nothing of this
    package's server half. Pointed at a port nothing listens on, so the
    answer is the transport refusal, which is the proof it got that far
    at all.
    """
    finished = _ran(client_env, "vinga-server", "doctor", "http://127.0.0.1:9/x/ABCDEFGH/")

    assert finished.returncode == 1, (finished.stdout, finished.stderr)
    assert "cannot reach" in finished.stderr, finished.stderr
    assert cli.NEEDS_THE_SERVER_HALF not in finished.stderr
    assert "Traceback" not in finished.stderr


def test_the_doctor_with_no_url_refuses_from_the_client_install(client_env: Path) -> None:
    """And the half it does not have, driven for real.

    With no URL the command derives one, and the derivation reads the
    onboarding package, whose `__init__` reaches FastAPI. This is the
    invocation the review round found: it used to end in a library
    traceback carrying the ImportError's module path, because the
    client-tier proof only ran `doctor --help` and never entered the
    branch.
    """
    finished = _ran(client_env, "vinga-server", "doctor")

    assert finished.returncode == 1, (finished.stdout, finished.stderr)
    assert finished.stderr.strip() == cli.NEEDS_THE_SERVER_HALF
    assert finished.stdout == ""
    assert "Traceback" not in finished.stderr
    assert "fastapi" not in finished.stderr.lower()


def test_the_doctor_with_no_url_derives_one_from_the_serve_install(serve_env: Path) -> None:
    """The other side of that gate: with the half installed the
    derivation runs, so the command gets as far as trying to reach what
    it derived rather than refusing to derive it."""
    finished = _ran(serve_env, "vinga-server", "doctor")

    assert finished.returncode == 1, (finished.stdout, finished.stderr)
    assert cli.NEEDS_THE_SERVER_HALF not in finished.stderr
    assert finished.stderr.strip(), "the derivation said nothing at all"


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


def test_the_conversations_group_answers_from_the_serve_install(serve_env: Path) -> None:
    """The other side of the same gate: with the half installed, the
    group renders what it always did."""
    finished = _ran(serve_env, "vinga-server", "conversations", "schema")

    assert finished.returncode == 0, finished.stderr
    assert finished.stdout.startswith("# ")


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
