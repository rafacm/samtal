"""The lane guard's residual path, driven as a pytest run of its own.

A refusal that belongs to no test (a session or module teardown emits
it, an atexit hook does) is the half of the guard no ordinary test can
reach: by the time it happens, every test has finished and there is
nothing left to fail. The guard reports it against the run instead, and
under xdist it has to cross a process boundary to do so, since the
worker that saw the refusal is not the process that owns the exit
status or the terminal.

That is a claim about what a whole pytest run prints and exits with, so
it is checked by running one. `pytester` gives a scratch directory and a
subprocess; what goes in the directory is a copy of the real
`tests/conftest.py` and one file whose session teardown emits something
the schema refuses.

The conftest is COPIED rather than restated here. The subject is the
file both lanes actually run, and a paraphrase of the guard would keep
passing while the shipped one was broken, which is the exact failure
this test exists to make loud: every piece of the residual path is
silent when it breaks, so nothing but an assertion on the printed
section and the exit status can hold it.
"""

import shutil
from pathlib import Path

import pytest

# `pytester` ships with pytest but is off unless a plugin asks for it,
# and asking here rather than in a conftest keeps it to this module.
pytest_plugins = ["pytester"]

CONFTEST = Path(__file__).resolve().parents[1] / "conftest.py"

# The heading the guard prints its residual under, and the code a
# refusal carries when the thunk that would have built the variant
# raised. Both are the guard's own words, matched as written.
SECTION = "event schema refusals outside any test"
CONSTRUCTION_FAILED = "construction_failed"

# One test, and a session fixture whose TEARDOWN refuses an emission.
# The session id is a value type the schema refuses, so the emitter
# reports a refusal after the last test of the session has run, which
# is a refusal no test can be held to.
PLANTED = '''
from collections.abc import Iterator

import pytest

from vinga_server.events import ServerEvents
from vinga_server.events.catalog import CONVERSATIONS_CHANNEL, ConversationsDropped
from vinga_server.events.values import SessionId


@pytest.fixture(scope="session", autouse=True)
def _planted_residual() -> Iterator[None]:
    yield
    ServerEvents(CONVERSATIONS_CHANNEL).emit(
        lambda: ConversationsDropped(session=SessionId("has a space"))
    )


def test_the_planted_run_has_a_test() -> None:
    assert True
'''


@pytest.fixture
def planted(pytester: pytest.Pytester) -> pytest.Pytester:
    """A one-test run, under the real conftest, that ends in a refusal
    no test owns."""
    shutil.copy(CONFTEST, pytester.path / "conftest.py")
    pytester.makepyfile(test_planted_residual=PLANTED)
    return pytester


def test_a_residual_refusal_fails_a_serial_run(planted: pytest.Pytester) -> None:
    """The behavior that predates workers, pinned so the repair below
    cannot be bought by breaking it."""
    result = planted.runpytest_subprocess("-q")

    assert result.ret != 0
    result.stdout.fnmatch_lines([f"*{SECTION}*", f"*{CONSTRUCTION_FAILED}*"])


def test_a_residual_refusal_fails_a_distributed_run(planted: pytest.Pytester) -> None:
    """And the same run under workers, which is the claim with no
    natural failure signal.

    Before the residual crossed to the controller this run was green and
    silent: the ledger is worker-local, xdist unregisters a worker's
    terminal reporter, and the controller derives the run's status from
    test reports, which a residual is not. So both halves are asserted,
    the printed section and the non-zero status, and the worker's name
    with them, since that is what the controller adds and the first
    thing that narrows where the teardown ran.
    """
    result = planted.runpytest_subprocess("-q", "-n", "2", "--dist", "loadfile")

    assert result.ret != 0
    result.stdout.fnmatch_lines([f"*{SECTION}*", f"*gw*{CONSTRUCTION_FAILED}*"])
