"""Which mode a process ends up in, and who decided.

The switch is one environment variable, and where it is read matters
more than what it says. An import-time read could honor neither of the
two things a deployment does: `main()` loads `.env` after this module
has already been imported, and a production process may import
`create_app` and serve it under an ASGI runner without ever running
`main()` at all. So application construction resolves it, in both
places, and the module default outside them stays strict, because the
default is what a lane, an import and a REPL get.

The three answers, and the reason for each:

- `strict` or `forgiving`, as written;
- unset means forgiving at either resolver, because a running server is
  a deployment whatever artifact it runs from, and a wheel or a source
  checkout must not be one telemetry bug away from losing a reply just
  because it is not the container;
- anything else refuses at boot, naming the variable and the two values
  it takes. A misspelled relaxation has to fail there rather than at the
  first live violation.

Half of this is about import order and ambient environment, which no
in-process test can honestly check, so that half runs the real
entrypoint in subprocesses.
"""

import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from samtal_server import events
from samtal_server.app import create_app
from samtal_server.config import Config
from samtal_server.events import (
    ENFORCEMENT_ENV,
    FORGIVING,
    STRICT,
    EventEnforcementError,
    resolve_enforcement,
)
from tests.support.configs import config_with_agent


@pytest.fixture(autouse=True)
def _mode() -> Iterator[None]:
    restored = events.enforcement()
    try:
        yield
    finally:
        events.set_enforcement(restored)


# --- the resolver itself ----------------------------------------------


@pytest.mark.parametrize("written", [STRICT, FORGIVING])
def test_a_written_mode_is_taken_as_written(written: str) -> None:
    assert resolve_enforcement({ENFORCEMENT_ENV: written}) == written
    assert events.enforcement() == written


def test_an_unset_variable_means_forgiving() -> None:
    """At a resolver, which is to say in a server process. The module
    default is the opposite, and that is what every other context
    gets."""
    assert resolve_enforcement({}) == FORGIVING
    assert events.enforcement() == FORGIVING


@pytest.mark.parametrize("written", ["Strict", "loose", "", "  strict  ", "1"])
def test_anything_else_refuses(written: str) -> None:
    events.set_enforcement(STRICT)
    with pytest.raises(EventEnforcementError) as raised:
        resolve_enforcement({ENFORCEMENT_ENV: written})

    assert raised.value.args == (
        f"{ENFORCEMENT_ENV} has to be '{STRICT}' or '{FORGIVING}'",
    )
    # The mode is left where it was rather than half-changed.
    assert events.enforcement() == STRICT


def test_the_rejected_spelling_is_not_echoed_back() -> None:
    """A variable's value is not this server's to repeat, and an
    environment is a place secrets live: the refusal names what the
    variable takes rather than what it held."""
    planted = "sk-env-2f9c7b1d-never-a-real-credential"
    with pytest.raises(EventEnforcementError) as raised:
        resolve_enforcement({ENFORCEMENT_ENV: planted})

    assert planted not in str(raised.value)
    assert planted not in repr(raised.value)


def test_the_refusal_does_not_change_the_mode_it_refused_to_set() -> None:
    events.set_enforcement(FORGIVING)
    with pytest.raises(EventEnforcementError):
        events.set_enforcement("loud")
    assert events.enforcement() == FORGIVING


# --- and where it is called from --------------------------------------


def test_building_an_app_resolves_the_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """A production process may import `create_app` and serve it under
    an ASGI runner without ever reaching `main()`, so this is where a
    deployment's posture is decided."""
    monkeypatch.delenv(ENFORCEMENT_ENV, raising=False)
    events.set_enforcement(STRICT)

    create_app(config_with_agent())

    assert events.enforcement() == FORGIVING


def test_building_an_app_refuses_an_unusable_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENFORCEMENT_ENV, "loud")

    with pytest.raises(EventEnforcementError):
        create_app(config_with_agent())


def test_a_lane_that_builds_an_app_stays_strict() -> None:
    """The lanes pin the variable in `conftest.py` for exactly this
    reason: `create_app` resolves, and an unset variable there would
    mean forgiving."""
    assert os.environ[ENFORCEMENT_ENV] == STRICT
    create_app(Config(**config_with_agent().model_dump()))
    assert events.enforcement() == STRICT


# --- the real entrypoint, in a process of its own ---------------------

# What the subprocesses run: the real `main()`, with the boot stopped
# the moment the mode has been resolved. Everything before that line is
# the entrypoint itself, dotenv loading and subcommand dispatch
# included, which is the half that cannot be checked from inside a
# pytest process that has already imported everything.
ENTRYPOINT = """
import sys
import samtal_server.main as main
from samtal_server import events

def stop(path=None):
    print("MODE=" + events.enforcement())
    raise SystemExit(0)

main.load_boot_config = stop
sys.argv = ["samtal-server"]
main.main()
"""

# The strictness proof under `-O`, which is why validation contains no
# `assert` statement: an optimized production process silently losing
# its enforcement is the quiet failure #155 exists to end.
OPTIMIZED = """
from samtal_server import events

print("DEBUG=" + str(__debug__))
events.set_enforcement("strict")
try:
    events.ServerEvents("samtal_server.ota").info("nothing", event="invented")
except events.EventSchemaError:
    print("REFUSED")
"""


def run(
    script: str,
    cwd: Path,
    written: str | None,
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    """One process, with the variable set, unset, or left to a `.env`."""
    environment = dict(os.environ)
    environment.pop(ENFORCEMENT_ENV, None)
    if written is not None:
        environment[ENFORCEMENT_ENV] = written
    # Outside pytest, so no stale bytecode cache can answer instead of
    # the source (the repository's own rule).
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, *arguments, "-c", script],
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def test_an_unset_variable_leaves_the_entrypoint_forgiving(tmp_path: Path) -> None:
    done = run(ENTRYPOINT, tmp_path, None)
    assert done.returncode == 0, done.stderr
    assert "MODE=forgiving" in done.stdout


@pytest.mark.parametrize("written", [STRICT, FORGIVING])
def test_the_entrypoint_takes_the_mode_it_is_given(
    tmp_path: Path, written: str
) -> None:
    done = run(ENTRYPOINT, tmp_path, written)
    assert done.returncode == 0, done.stderr
    assert f"MODE={written}" in done.stdout


def test_an_unknown_value_refuses_to_start(tmp_path: Path) -> None:
    """Printed like every other boot refusal, to stderr and with exit
    code 1, rather than raised as a traceback."""
    done = run(ENTRYPOINT, tmp_path, "loud")

    assert done.returncode == 1
    assert done.stdout == ""
    assert done.stderr.strip() == (
        f"{ENFORCEMENT_ENV} has to be '{STRICT}' or '{FORGIVING}'"
    )


def test_a_dotenv_file_can_carry_the_mode(tmp_path: Path) -> None:
    """The documented configuration layer, and the reason the mode is
    resolved inside `main()` rather than at import: `.env` is loaded on
    the entrypoint's first line, long after this module was imported."""
    (tmp_path / ".env").write_text(f"{ENFORCEMENT_ENV}={STRICT}\n")

    done = run(ENTRYPOINT, tmp_path, None)

    assert done.returncode == 0, done.stderr
    assert "MODE=strict" in done.stdout


def test_a_real_variable_still_beats_a_dotenv_file(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(f"{ENFORCEMENT_ENV}={STRICT}\n")

    done = run(ENTRYPOINT, tmp_path, FORGIVING)

    assert done.returncode == 0, done.stderr
    assert "MODE=forgiving" in done.stdout


def test_an_unusable_value_does_not_block_a_recovery_command(tmp_path: Path) -> None:
    """The `config` and `conversations` groups dispatch before the mode
    is resolved, on purpose: a server-only variable somebody misspelled
    must not stand between an operator and the command that fixes it."""
    done = subprocess.run(
        [sys.executable, "-m", "samtal_server.main", "config", "--help"],
        cwd=tmp_path,
        env={**os.environ, ENFORCEMENT_ENV: "loud", "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert done.returncode == 0, done.stderr
    assert ENFORCEMENT_ENV not in done.stdout


def test_optimized_python_still_refuses_an_invalid_emission(tmp_path: Path) -> None:
    """`python -O` strips `assert` statements, so validation is written
    in explicit conditions that raise. This is what says so."""
    done = run(OPTIMIZED, tmp_path, None, "-O")

    assert done.returncode == 0, done.stderr
    assert "DEBUG=False" in done.stdout
    assert "REFUSED" in done.stdout
