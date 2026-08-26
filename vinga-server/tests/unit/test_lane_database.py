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
