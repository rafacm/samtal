"""Write a candidate migration from the difference between the tables
and a database at head.

    uv run python -m vinga_server.db.migrations.autogen "what changed"

Alembic's autogenerate needs three things this project deliberately
does not have lying around: an `alembic.ini` naming a script location,
a database URL, and a connection to compare the metadata against.
`env.py` refuses to run without a connection handed in on the config's
attributes, which is what keeps `server.database.dir` the one place the
database path is decided, and that refusal is also what makes the
command unrunnable from a shell. So the command is spelled here
instead: a scratch database in a temporary directory, brought to head
through the packaged chain, compared against `db.schema.metadata`, and
thrown away. Nothing an operator runs, and nothing the server imports.

What comes out is a candidate and is treated as one. Autogenerate sees
tables, columns, types and constraints; it does not see why a column is
nullable, that a boolean needs a server-side default so a row written
before it says false itself, or that a table has to be rebuilt in batch
mode for SQLite to alter it. Read the file, write its docstring, and
fix what it got wrong before committing it.
"""

import sys
import tempfile
from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig

from vinga_server.db import upgrade_to_head, write_engine

# This directory: the script location, and where the candidate lands.
_MIGRATIONS = Path(__file__).resolve().parent


def generate(message: str) -> None:
    """Autogenerate one revision against a scratch database at head."""
    with tempfile.TemporaryDirectory() as scratch:
        engine = write_engine(Path(scratch) / "autogen.db")
        try:
            upgrade_to_head(engine, _MIGRATIONS)
            with engine.connect() as connection:
                config = AlembicConfig()
                config.set_main_option("script_location", str(_MIGRATIONS))
                # The same handover `upgrade_to_head` makes, which is
                # the only way env.py runs at all.
                config.attributes["connection"] = connection
                command.revision(config, message=message, autogenerate=True)
        finally:
            engine.dispose()


def main(argv: list[str]) -> int:
    if len(argv) != 1 or not argv[0].strip():
        print(
            'usage: python -m vinga_server.db.migrations.autogen "what changed"',
            file=sys.stderr,
        )
        return 2
    generate(argv[0])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
