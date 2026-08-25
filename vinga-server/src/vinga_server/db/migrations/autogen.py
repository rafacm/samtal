"""Write a candidate migration from the difference between the tables
and a database at head.

    uv run python -m vinga_server.db.migrations.autogen "what changed"
    uv run python -m vinga_server.db.migrations.autogen --conversations "what changed"

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

Both chains, because there are two and only one of them used to have an
entry point here. Which one is an argument rather than two commands: the
work is identical and the chain is the only thing that differs, which is
what `StoreChain` is.

Creating a database needs `CREATEDB`, which the runtime connection
contract deliberately does not ask for: this runs on a development or
test maintenance connection (the compose superuser, or any role that may
create databases), and a deployment's server role is never asked for it.

What comes out is a candidate and is treated as one. Autogenerate sees
tables, columns, types and constraints; it does not see why a column is
nullable, or that a boolean needs a server-side default so a row written
before it says false itself. Read the file, write its docstring, and fix
what it got wrong before committing it.
"""

import os
import sys

import psycopg
from alembic import command
from alembic.config import Config as AlembicConfig

from vinga_server.config.models import DatabaseConfig
from vinga_server.db import (
    DEFAULT_PASSWORD,
    DOMAIN_CHAIN,
    PASSWORD_ENV,
    StoreChain,
    open_at,
)

# The scratch database, made and dropped around one run. Named rather
# than random so a run that was killed leaves one findable thing behind
# and the next run reuses the name.
_SCRATCH = "vinga_autogen_scratch"


def generate(message: str, chain: StoreChain) -> None:
    """Autogenerate one revision against a scratch database at head."""
    settings = DatabaseConfig()
    maintenance = psycopg.connect(
        host=settings.host,
        port=settings.port,
        user=settings.user,
        password=os.environ.get(PASSWORD_ENV) or DEFAULT_PASSWORD,
        dbname="postgres",
        autocommit=True,
    )
    try:
        maintenance.execute(f'drop database if exists "{_SCRATCH}" with (force)')
        maintenance.execute(f'create database "{_SCRATCH}"')
        scratch = settings.model_copy(update={"name": _SCRATCH})
        engine = open_at(scratch, chain)
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
        maintenance.execute(f'drop database if exists "{_SCRATCH}" with (force)')
        maintenance.close()


def main(argv: list[str]) -> int:
    chain = DOMAIN_CHAIN
    arguments = list(argv)
    if arguments and arguments[0] == "--conversations":
        # Imported here rather than at module scope: this module lives
        # inside the domain chain's own directory, and the conversations
        # store imports `db` to declare its chain.
        from vinga_server.conversations.store import CONVERSATIONS_CHAIN

        chain = CONVERSATIONS_CHAIN
        arguments = arguments[1:]
    if len(arguments) != 1 or not arguments[0].strip():
        print(
            "usage: python -m vinga_server.db.migrations.autogen "
            '[--conversations] "what changed"',
            file=sys.stderr,
        )
        return 2
    generate(arguments[0], chain)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
