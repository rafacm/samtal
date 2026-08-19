"""Alembic environment for the conversation store.

The same shape as the domain database's environment
(`samtal_server/db/migrations/env.py`) and for the same reasons: the
migrations only ever run programmatically, from
`samtal_server.conversations.store.open_conversations`, which hands its
connection in on the config's attributes and owns the transaction around
them; there is no offline mode and no engine built here, because a URL
of its own would be a second place the database path is decided.

Separate from that environment rather than shared with it, because a
shared one would need the version table renamed to keep the two chains
apart, and two files are cheaper to read than one parameterized by which
database is running through it. The chains stay apart by living in
separate database files.

This directory lives inside the package so that a built wheel carries
it. The scripts are found by path, not by import, so nothing here is
importable as a module.
"""

from alembic import context

from samtal_server.conversations.schema import metadata

connection = context.config.attributes.get("connection")
if connection is None:
    raise RuntimeError(
        "the conversations migrations run only through "
        "samtal_server.conversations.store.open_conversations, which supplies "
        "the connection to run them on"
    )

context.configure(connection=connection, target_metadata=metadata)

# A no-op here: the connection arrives inside a transaction the caller
# opened, which Alembic detects and leaves alone. Written the usual way
# regardless, so the file reads like every other Alembic environment.
with context.begin_transaction():
    context.run_migrations()
