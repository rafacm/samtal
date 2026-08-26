"""Which database this lane's tests actually write to.

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

from tests.conftest import DB_NAME_ENV, DB_URL_ENV, LANE_DATABASE
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
