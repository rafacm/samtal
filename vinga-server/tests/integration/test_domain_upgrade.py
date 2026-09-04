"""The domain chain's forward migration, proved on a row rather than on
an empty database.

`tests/unit/test_db_open.py` asks what the chain builds from nothing:
the head, the tables, the columns. Every one of those passes on a
database whose rows the migration mangled, and this one's whole subject
is a row: `3002_drop_max_tokens_secrets` deletes exactly the provider
secret slots named `max_tokens` and must leave every sibling slot of the
same row where it was.

The slot could be stored because the option heuristic called
`max_tokens` secret-shaped and the slot check read the same predicate
(#277). Nothing ever consumed such a row, but a deployment holding one
would boot it, list it as stored-secret metadata, and render it into an
export's foot as a `provider secret set ... max_tokens` command that
the post-change import path refuses, which is the export-and-reapply
recovery breaking on a deployment that asked for none of this. So the
release deletes the slot, and this is where that is worth what it
claims: the four surfaces an operator meets it on are read after the
upgrade, and the sibling credential is read back as its own plaintext.

The lane, rather than the unit suite, because the material is a
database in a state no current build produces and the fixture that makes
one is `blank_database`: a migrated template cannot be stamped
backwards.
"""

import json
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from cryptography.fernet import MultiFernet
from sqlalchemy import text

from tests.support.config_cli import runner
from vinga_server.config.models import DatabaseConfig
from vinga_server.config.secrets import (
    MASTER_KEY_ENV,
    SecretLocation,
    encrypt,
    generate_key,
    load_keys,
)
from vinga_server.config.store import ConfigStore, stored_secrets, verify_secrets
from vinga_server.db import DOMAIN_CHAIN, open_database, read_engine, write_engine

# The revision a deployment carrying a stored `max_tokens` slot is
# stamped at, and the whole of what this release upgrades from.
BASELINE = "3001_postgres_domain"

HEAD = "3002_drop_max_tokens_secrets"

STAGE = "llm"
NAME = "claude"

# The slot the release withdraws, and the sibling on the same row that
# has to survive it. Both are real slots of the pre-change build: the
# heuristic called both secret-shaped, and `set_secret` stored both.
WITHDRAWN = SecretLocation.provider(STAGE, NAME, "max_tokens")
KEPT = SecretLocation.provider(STAGE, NAME, "api_key")

# Not real credentials, and shaped so a substring check for either
# cannot match by accident. The withdrawn one is asserted absent from
# every surface after the upgrade, which is the other half of "the row
# is gone": a slot that stopped being listed while its ciphertext stayed
# in the column would pass a listing check and fail this.
KEPT_VALUE = "sk-test-2b47f091-never-a-real-credential"
WITHDRAWN_VALUE = "sk-test-8e05d3ba-never-a-real-value"


@pytest.fixture
def key(monkeypatch: pytest.MonkeyPatch) -> str:
    """One key for the whole test, in the environment and in hand.

    In hand as well, because the export case runs through a CLI runner
    that generates a key of its own, and the seeded ciphertext opens
    under exactly one.
    """
    generated = generate_key()
    monkeypatch.setenv(MASTER_KEY_ENV, generated)
    return generated


@pytest.fixture
def keys(key: str) -> MultiFernet:
    """The same key as the store reads it: the seed encrypts with it and
    the reads after the upgrade open with it, so a sibling that survived
    as bytes but not as a credential fails rather than passes."""
    loaded = load_keys()
    assert loaded is not None
    return loaded


@pytest.fixture
def at_the_baseline(blank_database: str) -> DatabaseConfig:
    """A database with the domain chain at `3001_postgres_domain` and
    nothing beyond it.

    Alembic is driven the way `db.upgrade_to_head` drives it, with the
    schema created first and the connection and the chain handed over on
    the config's attributes, because the packaged environment refuses to
    run without both. The one difference is the target: a named revision
    rather than head, which is the whole of what makes this a database
    from before the release.
    """
    settings = DatabaseConfig(name=blank_database)
    engine = write_engine(settings, DOMAIN_CHAIN)
    try:
        with engine.connect() as connection:
            connection.execute(text(f'create schema if not exists "{DOMAIN_CHAIN.schema}"'))
            config = AlembicConfig()
            config.set_main_option("script_location", str(DOMAIN_CHAIN.migrations))
            config.attributes["connection"] = connection
            config.attributes["chain"] = DOMAIN_CHAIN
            command.upgrade(config, BASELINE)
            connection.commit()
    finally:
        engine.dispose()
    return settings


@pytest.fixture
def seeded(at_the_baseline: DatabaseConfig, keys: MultiFernet) -> DatabaseConfig:
    """The deployment this release meets: one provider entry, a
    credential stored in the slot that stays, and a credential stored in
    the slot that goes.

    The entry and the surviving credential are written through the
    repository, because that is how they got there. The withdrawn one is
    written into the column directly, because the repository this commit
    ships refuses the slot: what is being reproduced is a row an older
    build wrote, and only the row is old.
    """
    settings = at_the_baseline
    engine = write_engine(settings, DOMAIN_CHAIN)
    try:
        store = ConfigStore(engine, keys)
        store.set_provider(STAGE, NAME, {"type": "anthropic", "model": "claude-sonnet-5"})
        store.set_secret(KEPT, KEPT_VALUE)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "update domain.providers "
                    "set secrets = (secrets::jsonb || cast(:added as jsonb))::json "
                    "where stage = :stage and name = :name"
                ),
                {
                    "added": json.dumps(
                        {WITHDRAWN.slot: encrypt(WITHDRAWN, WITHDRAWN_VALUE, keys)}
                    ),
                    "stage": STAGE,
                    "name": NAME,
                },
            )
    finally:
        engine.dispose()
    return settings


def _slots(settings: DatabaseConfig) -> set[str]:
    """The slot names the row's own column holds, read underneath every
    rule that decides which of them are slots."""
    engine = read_engine(settings)
    try:
        with engine.connect() as connection:
            held = connection.execute(
                text("select secrets from domain.providers where stage = :stage and name = :name"),
                {"stage": STAGE, "name": NAME},
            ).scalar_one()
    finally:
        engine.dispose()
    return set(held if isinstance(held, dict) else json.loads(held))


def _version(settings: DatabaseConfig) -> list[str]:
    engine = read_engine(settings)
    try:
        with engine.connect() as connection:
            return [
                row[0]
                for row in connection.execute(
                    text(f"select * from {DOMAIN_CHAIN.schema}.alembic_version")
                )
            ]
    finally:
        engine.dispose()


@pytest.fixture
def upgraded(seeded: DatabaseConfig, keys: MultiFernet) -> Iterator[ConfigStore]:
    """The same database after a boot, which is what runs the migration:
    `open_database` brings the chain to head before anything reads a
    row."""
    engine = open_database(seeded)
    try:
        yield ConfigStore(engine, keys)
    finally:
        engine.dispose()


def test_the_seeded_row_really_carries_the_slot_the_release_withdraws(
    seeded: DatabaseConfig,
) -> None:
    """Without this every assertion below would be vacuously true of a
    row that never held the slot, which is exactly what a seed written
    through a repository that now refuses the slot would produce."""
    assert _version(seeded) == [BASELINE]
    assert _slots(seeded) == {KEPT.slot, WITHDRAWN.slot}


def test_the_upgrade_takes_the_withdrawn_slot_and_leaves_its_sibling(
    seeded: DatabaseConfig, upgraded: ConfigStore
) -> None:
    """The migration's whole claim, at the column and at the boot check
    that reads it: the withdrawn slot's ciphertext is gone, the sibling's
    is not, and the sibling still opens to the credential it was set to.

    `verify_secrets` is the boot's own step, so a row this release left
    in a state a start would refuse fails here rather than on a
    deployment.
    """
    assert _version(seeded) == [HEAD]
    assert _slots(seeded) == {KEPT.slot}

    snapshot = upgraded.load()
    verify_secrets(snapshot.secrets)
    assert snapshot.secrets.locations() == [KEPT]
    assert snapshot.secrets.secret(KEPT) == KEPT_VALUE
    # And the entity itself is untouched: the migration edits one key of
    # one column and nothing about the body beside it.
    assert snapshot.domain.providers.llm[NAME].options == {"model": "claude-sonnet-5"}


def test_the_single_read_and_the_listing_agree_after_the_upgrade(
    upgraded: ConfigStore,
) -> None:
    """The two metadata surfaces an operator meets a stored secret on,
    which read the row by different paths and used to name the withdrawn
    slot on both."""
    read = upgraded.read_provider(STAGE, NAME)
    listed = stored_secrets(upgraded.load())

    assert [stored.location for stored in read.secrets] == [KEPT]
    assert [stored.location for stored in listed] == [KEPT]


def test_the_export_of_an_upgraded_store_names_only_the_slot_that_survived(
    seeded: DatabaseConfig,
    key: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The surface the migration exists for. An export's foot renders
    every stored location as the command that enters it, so before this
    release an upgraded deployment exported a
    `provider secret set ... max_tokens` line its own import path would
    then refuse, breaking the export-and-reapply recovery the header
    prescribes.

    The key is put back after the runner has set one of its own, which
    is what `key` is handed round for.
    """
    run = runner(monkeypatch, database=seeded.name)
    monkeypatch.setenv(MASTER_KEY_ENV, key)
    capsys.readouterr()

    assert run("export") == 0

    exported = capsys.readouterr().out
    assert f"{STAGE} {NAME} {KEPT.slot}" in exported
    assert WITHDRAWN.slot not in exported
    assert KEPT_VALUE not in exported
    assert WITHDRAWN_VALUE not in exported
