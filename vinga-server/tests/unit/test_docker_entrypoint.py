"""The image's entrypoint wrapper, and the one decision it makes.

The image used to set `VINGA_CONFIG=/config/config.yaml` as an
environment variable, which made the mount mandatory: the loader refuses
a named file that is not there, deliberately, so `docker run` with no
`-v` refused to start on a file the operator had never named. The
wrapper names the file only when it is mounted, which is what lets the
server half come from `VINGA_SERVER__*` variables alone.

Nothing in the pull-request lane builds an image, so this is what can be
proved without one: the script parses as POSIX shell, its two branches
go the two ways they should, an explicit `VINGA_CONFIG` survives either
way, and the command line reaches the server unchanged. What it cannot
prove is that the file lands in the image executable and on the
entrypoint, which is the image job's business.

The path is the one thing here a test cannot use as written: `/config`
is not a directory a test may create. So the branch cases run a copy
with that one constant repointed, and a case of its own pins the
constant itself, which is the string the container documentation names
as the mount point.
"""

import os
import subprocess
from pathlib import Path

import pytest

ENTRYPOINT = Path(__file__).resolve().parents[2] / "docker-entrypoint.sh"

# Where the image expects a mounted server half, which is what the
# container documentation tells an operator to mount at.
MOUNT = "/config/config.yaml"

# What the fake server prints, so a case can read back what the wrapper
# decided and what it was handed.
STANDIN = """#!/bin/sh
printf 'config=%s\\n' "${VINGA_CONFIG-<unset>}"
printf 'argv=%s\\n' "$*"
"""


def test_the_wrapper_is_posix_shell() -> None:
    """Checked with the shell the image runs it with rather than read,
    since a syntax error here is a container that cannot start at all."""
    subprocess.run(["sh", "-n", str(ENTRYPOINT)], check=True)


def test_the_wrapper_names_the_mount_the_documentation_names() -> None:
    """The constant, pinned. The cases below repoint it to run at all,
    so this is the one place the real path is asserted, and it is the
    path a reader is told to mount at."""
    assert f"DEFAULT_CONFIG={MOUNT}" in ENTRYPOINT.read_text(encoding="utf-8")


@pytest.fixture
def entrypoint(tmp_path: Path):
    """The wrapper, with its mount path repointed into a temporary
    directory and a stand-in server on PATH.

    A copy with one constant substituted, because `/config` is not a
    directory a test may create. Everything else about the script is the
    committed one: the guard, the export, the argument pass-through and
    the `exec`.
    """
    mount = tmp_path / "config.yaml"
    script = tmp_path / "entrypoint.sh"
    body = ENTRYPOINT.read_text(encoding="utf-8")
    assert f"DEFAULT_CONFIG={MOUNT}" in body
    script.write_text(body.replace(f"DEFAULT_CONFIG={MOUNT}", f"DEFAULT_CONFIG={mount}"))

    binaries = tmp_path / "bin"
    binaries.mkdir()
    standin = binaries / "vinga-server"
    standin.write_text(STANDIN, encoding="utf-8")
    standin.chmod(0o755)

    def _run(*argv: str, **environment: str) -> dict[str, str]:
        finished = subprocess.run(
            ["sh", str(script), *argv],
            check=True,
            capture_output=True,
            text=True,
            env={
                **{key: value for key, value in os.environ.items() if key != "VINGA_CONFIG"},
                "PATH": f"{binaries}{os.pathsep}{os.environ['PATH']}",
                **environment,
            },
        )
        return dict(
            line.split("=", 1) for line in finished.stdout.splitlines() if "=" in line
        )

    _run.mount = mount
    return _run


def test_no_mounted_file_names_no_file(entrypoint) -> None:
    """The case the finding was about: nothing mounted, so nothing named,
    so the server boots on its defaults and whatever `VINGA_SERVER__*`
    says rather than refusing a file nobody wrote."""
    assert entrypoint("config", "list")["config"] == "<unset>"


def test_a_mounted_file_is_named(entrypoint) -> None:
    """And the case that has to keep working: a mounted server half is
    read, which is what every existing `docker run` in the documentation
    does."""
    entrypoint.mount.write_text("server: {}\n", encoding="utf-8")

    assert entrypoint("config", "list")["config"] == str(entrypoint.mount)


def test_an_explicit_variable_wins_over_the_mount(entrypoint) -> None:
    """Somebody who named a path meant that path, mounted file or not,
    including when it is wrong: the loader's refusal for a named file
    that is not there is the whole reason this wrapper exists, and it
    has to stay reachable."""
    entrypoint.mount.write_text("server: {}\n", encoding="utf-8")

    chosen = entrypoint("config", "list", VINGA_CONFIG="/elsewhere/config.yaml")

    assert chosen["config"] == "/elsewhere/config.yaml"


def test_the_command_line_reaches_the_server_unchanged(entrypoint) -> None:
    """`docker run ... config show provider llm claude` has to read as
    it always has, which means everything after the image name is the
    server's own command line and none of it is the wrapper's."""
    argv = ("config", "show", "provider", "llm", "claude")
    assert entrypoint(*argv)["argv"] == " ".join(argv)
