"""Alembic environment for the domain configuration database.

Migrations only ever run programmatically, from
`vinga_server.db.open_database`, which hands its connection and its
chain in on the config's attributes and owns the transaction around
them. There is no offline mode and no engine built here on purpose: a
URL of its own would be a second place the connection is decided, and
the whole point of the `VINGA_DB_*` variables is that there is one.

The chain is what says which schema this is. Two chains share one
database now, so each keeps its `alembic_version` table inside its own
schema (`version_table_schema`) and compares against its own schema
only (`include_schemas` with a name filter). Without the filter,
autogeneration and drift comparison would see the other store's tables
in the same database and propose dropping them.

This directory lives inside the package so that a built wheel carries
it. The scripts are found by path, not by import, so nothing here is
importable as a module.
"""

from alembic import context

from vinga_server.db.schema import metadata

connection = context.config.attributes.get("connection")
chain = context.config.attributes.get("chain")
if connection is None or chain is None:
    raise RuntimeError(
        "the vinga migrations run only through vinga_server.db.open_database, "
        "which supplies the connection to run them on and the chain that says "
        "which schema they belong to"
    )


def include_name(name: str | None, type_: str, parent_names: dict) -> bool:
    """Everything inside this chain's schema, and no other schema.

    The default schema arrives as None, which this excludes along with
    the sibling store's: what this chain compares against is its own
    schema, whole, and nothing else in the database.
    """
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
