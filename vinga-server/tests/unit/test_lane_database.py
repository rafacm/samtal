"""Which database this lane's tests write to, and which lanes ask for
one at all.

Every other test in both lanes takes it for granted that the store it
opens is the one the autouse truncation clears. Nothing said so, and for
one milestone it was not true: `tests/conftest.py` moved
`DatabaseConfig`'s model default onto a per-worker database and left
`VINGA_DB_NAME` alone, while the loader reads that variable OVER the
model default. Locally the variable is unset and the two answers agreed
by accident. CI exports `VINGA_DB_NAME: vinga` for the contract's sake,
so there they disagreed: everything composed the way a deployment
composes it wrote to the job's shared `vinga` database, the truncation
cleared a per-worker database nothing was writing to, and four workers
read each other's rows. Thirty tests failed on symptoms that named
nothing about a database.

So the fact gets a pin of its own, and it is deliberately about the
AGREEMENT rather than about either half: two doors reach the database
name, and a test asserting only the one it happens to use would have
gone on passing through the whole of that failure.
"""

import os
import sys
from pathlib import Path

from tests.conftest import DB_NAME_ENV, DB_URL_ENV, LANE_DATABASE
from tests.support.commands import ran
from vinga_server.config.loader import load_file_config
from vinga_server.config.models import DatabaseConfig


def test_both_doors_to_the_database_name_answer_this_lane_s_own() -> None:
    """The model default a `Config(...)` takes, the environment the
    loader reads over it, and the name the truncation clears: one
    database, said three ways."""
    assert DatabaseConfig().name == LANE_DATABASE
    assert os.environ[DB_NAME_ENV] == LANE_DATABASE
    assert load_file_config(None).server.database.name == LANE_DATABASE


def test_no_whole_url_is_left_to_win_over_the_five() -> None:
    """`VINGA_DB_URL` replaces the discrete facts entirely, so one
    exported around the lane would take every settings composition to a
    database these fixtures never made and may not truncate."""
    assert DB_URL_ENV not in os.environ


def test_a_lane_that_stores_nothing_collects_with_no_instance_in_reach() -> None:
    """The smoke lane, collected against a host that does not resolve.

    It stores nothing: it drives a container over HTTP, and in the image
    job it runs on the runner while the database sits on a Docker
    network only the containers can resolve. The root conftest used to
    provision at its own import, for every lane under `tests/`, so the
    smoke lane died at collection with a sentence about an instance it
    has no use for, on the one job that publishes an image.

    Driven as a real pytest rather than asserted about a flag, because
    what broke was collection itself, and a flag is exactly what a
    future conftest could satisfy while still connecting on the way
    past. A name in `.invalid` is reserved by RFC 2606 and resolves
    nowhere, which is the runner's situation without needing the runner.
    """
    finished = ran(
        [sys.executable, "-m", "pytest", "tests/smoke", "--collect-only", "-q"],
        cwd=Path(__file__).resolve().parents[2],
        env=os.environ | {"VINGA_DB_HOST": "nowhere.invalid"},
    )

    assert finished.returncode == 0, finished.stdout + finished.stderr
    assert "tests collected" in finished.stdout, finished.stdout + finished.stderr


# What the lane's own refusals may say
#
# Both of them are raised while a conftest is being imported, which
# means they arrive as a collection traceback: the message, the frame,
# and every local the frame held. The five variables they are about are
# set beside each other and read from one `.env`, so the value most
# likely to be in the wrong one is the credential from the right one.
# The rule is the product's own, and has no exception for a host.

# Not credentials: fixed strings shaped like one, and shaped so a
# substring hunt for either cannot match by accident.
SENTINEL = "sk-lane-4c19b7e2-never-a-real-credential"
OTHER_SENTINEL = "tok-lane-91f3d0a6-never-a-real-credential"

# Every discrete field, each carrying something a refusal must not
# repeat, and a host that resolves nowhere so the refusal is the
# unreachable one. `.invalid` is reserved by RFC 2606.
PLANTED_CONNECTION = {
    "VINGA_DB_HOST": f"{SENTINEL}.invalid",
    "VINGA_DB_PORT": "5432",
    "VINGA_DB_NAME": SENTINEL,
    "VINGA_DB_USER": SENTINEL,
    "VINGA_DB_PASSWORD": OTHER_SENTINEL,
}

# A port that is not one, carrying the same shape: the case that used to
# reach an unguarded `int()` and ride its ValueError out.
PLANTED_PORT = dict(PLANTED_CONNECTION, VINGA_DB_PORT=OTHER_SENTINEL)


def _collected(planted: dict[str, str]) -> str:
    """One unit-lane collection under a planted connection, as everything
    it wrote.

    A subprocess because both refusals happen while a conftest is being
    imported, which no in-process assertion can reach: by the time a test
    is running, the import that would have refused has already
    succeeded.
    """
    finished = ran(
        [sys.executable, "-m", "pytest", "tests/unit/test_db_open.py", "--collect-only", "-q"],
        cwd=Path(__file__).resolve().parents[2],
        env=os.environ | planted | {"VINGA_TEST_RUN": "sentinel_probe"},
    )
    assert finished.returncode != 0, finished.stdout + finished.stderr
    return finished.stdout + finished.stderr


def test_the_unreachable_refusal_repeats_no_part_of_the_connection() -> None:
    """A storing lane pointed at an instance that is not there. The
    sentence names the four variables to look at and none of their
    values, and the driver's own message, which quotes the DSN it tried,
    never travels."""
    written = _collected(PLANTED_CONNECTION)

    assert "could not reach one" in written, written
    assert SENTINEL not in written, written
    assert OTHER_SENTINEL not in written, written
    assert "psycopg" not in written, written


def test_a_port_that_is_not_one_is_refused_without_quoting_it() -> None:
    """The other refusal, which used to be a bare `int()`: its ValueError
    quotes what it was handed, and a port variable is where a password
    lands when two lines of a `.env` are swapped."""
    written = _collected(PLANTED_PORT)

    assert "VINGA_DB_PORT" in written, written
    assert SENTINEL not in written, written
    assert OTHER_SENTINEL not in written, written
    assert "invalid literal for int" not in written, written
