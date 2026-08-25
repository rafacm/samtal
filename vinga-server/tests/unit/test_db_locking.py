"""The lock the whole retryable-409 contract rests on.

Every write transaction takes its chain's transaction-scoped advisory
lock before it reads anything, and every connection carries a
`lock_timeout`. Those two together are the claim: a writer that cannot
take the lock inside ten seconds fails with `LockNotAvailable` rather
than waiting for as long as the holder feels like, and that failure is
what the stores turn into a retryable refusal.

The first test proves the load-bearing half of it directly, at the
driver, because `lock_timeout` bounding an *advisory* lock wait is the
premise the cutover was planned on: advisory locks are ordinary
heavyweight locks and the timeout applies to them, but a premise that
large is proved rather than assumed. If it ever stops holding, the
fallback is `pg_try_advisory_xact_lock` in a bounded wait loop, which is
the same contract by other means.

The rest of the busy family lives with the stores that raise it
(`test_db_open.py`, `test_config_refusals.py`): what a contended write
answers is a property of the store, and what a lock does is a property
of the database.
"""

import time

import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from vinga_server.config.models import DatabaseConfig
from vinga_server.db import (
    DOMAIN_CHAIN,
    LOCK_TIMEOUT_MS,
    connection_url,
    is_busy,
    write_engine,
)

# Far below the shipped timeout, so a test that proves a wait is bounded
# does not spend the bound proving it. The default itself is pinned
# separately below, which is the half a shortened constant cannot show.
SHORT_TIMEOUT_MS = 300


@pytest.fixture
def holder():
    """A second connection sitting on the domain chain's advisory lock,
    which is what a concurrent writer is.

    Its own psycopg connection rather than a second engine, deliberately:
    what the test needs is a lock held open across another connection's
    attempt, and an engine's transaction would end when its block does.
    """
    connection = psycopg.connect(connection_url(DatabaseConfig()).render_as_string(False))
    try:
        connection.execute(
            "select pg_advisory_xact_lock(%s)", (DOMAIN_CHAIN.lock_key,)
        )
        yield connection
    finally:
        connection.rollback()
        connection.close()


def test_the_lock_timeout_aborts_a_wait_for_the_advisory_lock(holder) -> None:
    """The premise: a waiter gives up inside its timeout, with the error
    the classifier is written around, rather than blocking until the
    holder lets go."""
    waiter = psycopg.connect(connection_url(DatabaseConfig()).render_as_string(False))
    try:
        waiter.execute(f"set lock_timeout = {SHORT_TIMEOUT_MS}")
        started = time.monotonic()
        with pytest.raises(psycopg.errors.LockNotAvailable):
            waiter.execute(
                "select pg_advisory_xact_lock(%s)", (DOMAIN_CHAIN.lock_key,)
            )
        # Bounded, and by the timeout rather than by the holder: the
        # holder is still holding when this returns.
        assert time.monotonic() - started < SHORT_TIMEOUT_MS / 1000 + 5
    finally:
        waiter.rollback()
        waiter.close()


def test_a_refused_advisory_lock_is_classified_retryable(holder) -> None:
    """The same failure through the product's own engine, which is where
    it arrives wrapped: SQLAlchemy hands back a `DBAPIError` and the
    driver's error is on `orig`, which is the walk the classifier does
    and the reason it never reads a message."""
    engine = write_engine(DatabaseConfig(), DOMAIN_CHAIN)
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql(f"set lock_timeout = {SHORT_TIMEOUT_MS}")
            with pytest.raises(DBAPIError) as caught:
                # The begin listener is what takes the lock, so any
                # statement inside a transaction reaches it.
                connection.execute(text("select 1"))
    finally:
        engine.dispose()

    assert isinstance(caught.value.orig, psycopg.errors.LockNotAvailable)
    assert is_busy(caught.value)


def test_an_ordinary_database_failure_is_not_retryable() -> None:
    """The other side of the closed set, so that "retryable" is a
    decision and not a synonym for "the database said no"."""
    engine = write_engine(DatabaseConfig(), DOMAIN_CHAIN)
    try:
        with engine.connect() as connection:
            with pytest.raises(DBAPIError) as caught:
                connection.execute(text("select * from a_table_that_is_not_there"))
    finally:
        engine.dispose()

    assert not is_busy(caught.value)


@pytest.mark.parametrize(
    "failure",
    [
        psycopg.errors.LockNotAvailable,
        psycopg.errors.DeadlockDetected,
        psycopg.errors.SerializationFailure,
    ],
)
def test_every_member_of_the_closed_set_classifies_retryable(failure) -> None:
    """Each member by name, because the set is closed and a member that
    quietly left it would otherwise be a refusal that stopped saying
    "try again"."""
    assert is_busy(failure("whatever the server said"))


def test_a_foreign_operational_error_is_not_retryable() -> None:
    """A driver error from outside the set, which is every other way a
    statement can fail and is never something to repeat."""
    assert not is_busy(psycopg.errors.OperationalError("connection closed"))


def test_the_shipped_lock_timeout_is_what_a_fresh_connection_carries() -> None:
    """The default policy, which the tests above cannot show because
    they shorten it: ten seconds arrives on the connection itself, as a
    startup option, so it survives the rollback a pooled connection is
    returned with rather than being undone by it."""
    engine = write_engine(DatabaseConfig(), DOMAIN_CHAIN)
    try:
        with engine.connect() as connection:
            timeout = connection.execute(text("show lock_timeout")).scalar()
    finally:
        engine.dispose()

    assert timeout == f"{LOCK_TIMEOUT_MS // 1000}s"
