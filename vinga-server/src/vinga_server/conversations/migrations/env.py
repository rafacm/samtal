"""Alembic environment for the conversation store.

The same shape as the domain database's environment
(`vinga_server/db/migrations/env.py`) and for the same reasons: the
migrations only ever run programmatically, from
`vinga_server.conversations.store.open_conversations`, which hands its
connection and its chain in on the config's attributes and owns the
transaction around them; there is no offline mode and no engine built
here, because a URL of its own would be a second place the connection is
decided.

Separate from that environment rather than shared with it, because a
shared one would be parameterized by which chain is running through it
and two files are cheaper to read than one that is. What keeps the two
chains apart is the schema each declares: the version table lives inside
it, and the comparison is scoped to it.

This directory lives inside the package so that a built wheel carries
it. The scripts are found by path, not by import, so nothing here is
importable as a module.
"""

from alembic import context

from vinga_server.conversations.schema import metadata

connection = context.config.attributes.get("connection")
chain = context.config.attributes.get("chain")
if connection is None or chain is None:
    raise RuntimeError(
        "the conversations migrations run only through "
        "vinga_server.conversations.store.open_conversations, which supplies "
        "the connection to run them on and the chain that says which schema "
        "they belong to"
    )


def include_name(name: str | None, type_: str, parent_names: dict) -> bool:
    """This chain's schema and nothing else, for the reason the domain
    environment gives: the sibling store's tables share the database."""
    return type_ != "schema" or name == chain.schema


context.configure(
    connection=connection,
    target_metadata=metadata,
    version_table_schema=chain.schema,
    include_schemas=True,
    include_name=include_name,
)

# A no-op here: the connection arrives inside a transaction the caller
# opened, which Alembic detects and leaves alone. Written the usual way
# regardless, so the file reads like every other Alembic environment.
with context.begin_transaction():
    context.run_migrations()
