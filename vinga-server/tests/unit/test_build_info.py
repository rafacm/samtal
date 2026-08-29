"""What the image says about itself.

Which build, from three sources, first answer wins: the environment
variable an image bakes in, `git describe` for a working tree, and
`unknown` for a build that has neither. The last one is the point of the
exercise as much as the first: a server that cannot name its build still
starts.

And whether this is a container at all, which is one marker the image
sets in its own ENV, read fresh every time because what asks is a
provider build and a test is entitled to be a container between two of
them.
"""

import subprocess
from pathlib import Path

import pytest

from vinga_server import build_info
from vinga_server.build_info import (
    CONTAINER_ENV,
    REVISION_ENV,
    UNKNOWN_REVISION,
    in_container,
    revision,
)


@pytest.fixture(autouse=True)
def _uncached() -> None:
    """The resolver caches for the life of the process, which is right
    in a server and wrong in a suite that varies its inputs."""
    revision.cache_clear()


def git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A checkout with one commit, standing in for the working tree."""
    git("init", "--initial-branch=main", cwd=tmp_path)
    git("config", "user.email", "test@example.invalid", cwd=tmp_path)
    git("config", "user.name", "Test", cwd=tmp_path)
    (tmp_path / "file.txt").write_text("one\n")
    git("add", "file.txt", cwd=tmp_path)
    git("commit", "-m", "first", cwd=tmp_path)
    return tmp_path


def test_the_environment_variable_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    # What an image sets. It comes first because a container has no
    # checkout to consult and no git to consult it with.
    monkeypatch.setenv(REVISION_ENV, "0f1e2d3c4b5a")
    assert revision() == "0f1e2d3c4b5a"


def test_an_empty_variable_is_not_an_answer(
    monkeypatch: pytest.MonkeyPatch, repo: Path
) -> None:
    # A build argument left unset can arrive as an empty string rather
    # than as nothing at all, and empty is not a revision.
    monkeypatch.setenv(REVISION_ENV, "   ")
    monkeypatch.setattr(build_info, "_CHECKOUT", repo)
    assert revision() != "   "
    assert revision() != UNKNOWN_REVISION


def test_a_working_tree_describes_itself(
    monkeypatch: pytest.MonkeyPatch, repo: Path
) -> None:
    monkeypatch.delenv(REVISION_ENV, raising=False)
    monkeypatch.setattr(build_info, "_CHECKOUT", repo)
    described = subprocess.run(
        ["git", "describe", "--always", "--dirty"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert revision() == described
    assert described


def test_a_dirty_tree_says_so(monkeypatch: pytest.MonkeyPatch, repo: Path) -> None:
    # The case worth knowing about: a build running code that is not any
    # commit. Without --dirty it would report the last commit and lie.
    monkeypatch.delenv(REVISION_ENV, raising=False)
    monkeypatch.setattr(build_info, "_CHECKOUT", repo)
    clean = revision()
    revision.cache_clear()
    (repo / "file.txt").write_text("two\n")
    assert revision() == f"{clean}-dirty"


def test_no_checkout_is_unknown_rather_than_a_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(REVISION_ENV, raising=False)
    monkeypatch.setattr(build_info, "_CHECKOUT", tmp_path)
    assert revision() == UNKNOWN_REVISION


def test_no_git_at_all_is_unknown_rather_than_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The container case: no `.git` to read and no git binary to read it
    # with. An image built without the build argument has to start.
    monkeypatch.delenv(REVISION_ENV, raising=False)

    def no_git(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError("git")

    monkeypatch.setattr(build_info.subprocess, "run", no_git)
    assert revision() == UNKNOWN_REVISION


def test_a_wedged_git_does_not_hold_up_a_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(REVISION_ENV, raising=False)

    def times_out(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd="git", timeout=1)

    monkeypatch.setattr(build_info.subprocess, "run", times_out)
    assert revision() == UNKNOWN_REVISION


def test_the_container_marker_is_the_image_saying_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(CONTAINER_ENV, raising=False)
    assert in_container() is False
    # Anything the image put there is a yes: the variable exists to be
    # present, and what it holds says nothing further.
    monkeypatch.setenv(CONTAINER_ENV, "1")
    assert in_container() is True


def test_a_cleared_container_marker_is_not_an_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The same rule the revision holds: a variable set to nothing is one
    # somebody cleared, not a claim.
    monkeypatch.setenv(CONTAINER_ENV, "   ")
    assert in_container() is False


def test_the_answer_is_resolved_once(
    monkeypatch: pytest.MonkeyPatch, repo: Path
) -> None:
    # Cached because the git branch spawns a subprocess, and a server
    # answers /healthz rather more often than it changes build.
    monkeypatch.delenv(REVISION_ENV, raising=False)
    monkeypatch.setattr(build_info, "_CHECKOUT", repo)
    calls = 0
    real_run = subprocess.run

    def counted(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return real_run(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(build_info.subprocess, "run", counted)
    assert revision() == revision() == revision()
    assert calls == 1
