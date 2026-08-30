"""Write a candidate migration from the difference between the tables
and a database at head.

    uv run python -m vinga_server.db.migrations.autogen "what changed"
    uv run python -m vinga_server.db.migrations.autogen --conversations "what changed"
    uv run python -m vinga_server.db.migrations.autogen --memory "what changed"

Alembic's autogenerate needs three things this project deliberately
does not have lying around: an `alembic.ini` naming a script location, a
database URL, and a connection to compare the metadata against. Each
`env.py` refuses to run without a connection and a chain handed in on
the config's attributes, which is what keeps the `VINGA_DB_*` variables
the one place the connection is decided, and that refusal is also what
makes the command unrunnable from a shell. So the command is spelled
here instead: a scratch database on the configured instance, brought to
head through the packaged chain, compared against that chain's metadata,
and dropped. Nothing an operator runs, and nothing the server imports.

Every chain, because there are three of them and only one used to have
an entry point here. Which one is an argument rather than three
commands: the work is identical and the chain is the only thing that
differs, which is what `StoreChain` is.

Creating a database needs `CREATEDB`, which the runtime connection
contract deliberately does not ask for: this runs on a development or
test maintenance connection (the compose superuser, or any role that may
create databases), and a deployment's server role is never asked for it.

What comes out is a candidate and is treated as one. Autogenerate sees
tables, columns, types and constraints; it does not see why a column is
nullable, or that a boolean needs a server-side default so a row written
before it says false itself. Read the file, write its docstring, and fix
what it got wrong before committing it.

`main` is a command boundary and not a thin wrapper around `generate`.
This is the one module in the project that connects with psycopg
directly and issues `CREATE DATABASE` and `DROP DATABASE`, and psycopg
quotes the DSN it tried in every failure it raises, password and query
parameters included. Letting one of those escape a module entry point
would print a connection string to stderr, which is the highest-priority
thing this project's refusals exist to prevent. So the whole maintenance
lifecycle sits behind one sanitized answer: a fixed sentence, no
exception, exit 1.
"""

import sys

import psycopg
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import URL

from vinga_server.config.loader import ConfigError, load_file_config
from vinga_server.db import (
    DOMAIN_CHAIN,
    StoreChain,
    connection_url,
    open_url,
)

# What every failure inside the maintenance lifecycle is answered with.
#
# Fixed and value-free, like every other refusal this project prints
# about a database, and for the same reason with one more edge: this
# command connects with psycopg directly and issues `CREATE DATABASE`
# and `DROP DATABASE`, and a psycopg failure quotes the DSN it tried,
# password and query parameters included. A traceback out of a module
# entry point is that DSN on stderr and in whatever captured it.
#
# The one thing that IS repeated is the names of the variables to look
# at, which is what `db`'s own refusals do: a name is not a value.
MAINTENANCE_FAILED = (
    "could not autogenerate a revision. The scratch database on the configured "
    "instance could not be made, brought to head, compared or dropped. Nothing of "
    "the connection is repeated here, because a database URL carries credentials in "
    "its authority and can carry another in its query: check the VINGA_DB_* "
    "variables, and that the role they name may create databases, which a "
    "deployment's server role deliberately may not. The development instance starts "
    "with `docker compose up -d --wait`"
)

# The scratch database, made and dropped around one run. Named rather
# than random so a run that was killed leaves one findable thing behind
# and the next run reuses the name. Public because the lane asserts it
# is gone afterwards, which is what makes the command repeatable.
SCRATCH = "vinga_autogen_scratch"

# Where `CREATE DATABASE` and `DROP DATABASE` are issued from. Never the
# scratch database itself, which cannot be dropped from a connection
# inside it, and never the configured one, which this command has no
# business connecting to at all.
_MAINTENANCE = "postgres"


def generate(message: str, chain: StoreChain) -> None:
    """Autogenerate one revision against a scratch database at head.

    The connection is resolved ONCE, here, and the two databases this
    command touches are derived from it by replacing the database
    component and nothing else. That is the whole of the fix for a bug
    worth stating: the scratch database used to be made from the
    discrete settings and then opened through `open_at`, which resolves
    the connection again from the environment, where a `VINGA_DB_URL`
    naming a deployment wins whole. The scratch database landed on the
    local instance and the migration ran against production. It also
    read the discrete `VINGA_DB_*` variables not at all, since a bare
    `DatabaseConfig()` reads no environment; `load_file_config` is the
    machinery the server itself resolves them with.

    Neither URL is rendered anywhere, on any path. What would be printed
    is a connection string, and a connection string carries a password
    in its authority and can carry another in its query.
    """
    configured = connection_url(load_file_config().server.database)
    scratch = configured.set(database=SCRATCH)
    maintenance = psycopg.connect(
        _dsn(configured.set(database=_MAINTENANCE)), autocommit=True
    )
    try:
        maintenance.execute(f'drop database if exists "{SCRATCH}" with (force)')
        maintenance.execute(f'create database "{SCRATCH}"')
        engine = open_url(scratch, chain)
        try:
            with engine.connect() as connection:
                config = AlembicConfig()
                config.set_main_option("script_location", str(chain.migrations))
                # The same handover `upgrade_to_head` makes, which is
                # the only way env.py runs at all.
                config.attributes["connection"] = connection
                config.attributes["chain"] = chain
                command.revision(config, message=message, autogenerate=True)
        finally:
            engine.dispose()
    finally:
        maintenance.execute(f'drop database if exists "{SCRATCH}" with (force)')
        maintenance.close()


def _dsn(url: URL) -> str:
    """One URL as psycopg's own connection string.

    The dialect is dropped rather than translated: `postgresql+psycopg`
    is SQLAlchemy's way of naming a driver, and libpq has never heard of
    it. The password is rendered because this string is what makes the
    connection, and it goes nowhere else.
    """
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


def _conversations_chain() -> StoreChain:
    """The conversation record's chain, imported at the moment it is
    asked for rather than at module scope: this module lives inside the
    domain chain's own directory, and each sibling store imports `db` to
    declare its chain."""
    from vinga_server.conversations.store import CONVERSATIONS_CHAIN

    return CONVERSATIONS_CHAIN


def _memory_chain() -> StoreChain:
    """Agent memory's chain, imported for the reason above."""
    from vinga_server.memory.store import MEMORY_CHAIN

    return MEMORY_CHAIN


# The flag each of the other two chains is named by. The domain chain
# has none because it is what the command does with no flag at all,
# which is the shape it had when it was the only chain there was.
SELECTORS = {
    "--conversations": _conversations_chain,
    "--memory": _memory_chain,
}


def main(argv: list[str]) -> int:
    chain = DOMAIN_CHAIN
    arguments = list(argv)
    if arguments and arguments[0] in SELECTORS:
        chain = SELECTORS[arguments[0]]()
        arguments = arguments[1:]
    if len(arguments) != 1 or not arguments[0].strip():
        print(
            "usage: python -m vinga_server.db.migrations.autogen "
            '[--conversations|--memory] "what changed"',
            file=sys.stderr,
        )
        return 2
    return _generated(arguments[0], chain)


def _generated(message: str, chain: StoreChain) -> int:
    """The whole maintenance lifecycle behind one sanitized boundary.

    Every failure from here down is somebody's connection string: an
    unreachable instance, a role without `CREATEDB`, a `DROP DATABASE`
    refused because something is still connected, a comparison that
    could not read the version table. psycopg quotes the DSN it tried in
    all of them, so none of them may travel.

    A `ConfigError` is this project's own already-sanitized refusal and
    is printed as it stands: `db` builds those outside their handlers
    precisely so they carry nothing. Everything else becomes the fixed
    sentence.

    Nothing is re-raised, which is the strongest form of severing there
    is: what leaves this function is a string that was written here and
    an exit code. There is no exception for a renderer to walk, no
    `__cause__` to follow, and no frame holding the DSN.
    """
    problem: str | None = None
    try:
        generate(message, chain)
    except ConfigError as refusal:
        problem = str(refusal)
    except Exception:
        problem = MAINTENANCE_FAILED
    if problem is not None:
        print(problem, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
