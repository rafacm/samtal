"""Alembic environment for the domain configuration database.

Migrations only ever run programmatically, from
`samtal_server.db.open_database`, which hands its connection in on the
config's attributes and owns the transaction around them. There is no
offline mode and no engine built here on purpose: an URL of its own
would be a second place the database path is decided, and the whole
point of `server.database.dir` is that there is one.

This directory lives inside the package so that a built wheel carries
it. The scripts are found by path, not by import, so nothing here is
importable as a module.
"""

from alembic import context

from samtal_server.db.schema import metadata

connection = context.config.attributes.get("connection")
if connection is None:
    raise RuntimeError(
        "the samtal migrations run only through samtal_server.db.open_database, "
        "which supplies the connection to run them on"
    )

context.configure(connection=connection, target_metadata=metadata)

# A no-op here: the connection arrives inside a transaction the caller
# opened, which Alembic detects and leaves alone. Written the usual way
# regardless, so the file reads like every other Alembic environment.
with context.begin_transaction():
    context.run_migrations()
