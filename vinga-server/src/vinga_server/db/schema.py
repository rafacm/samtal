"""The tables holding the domain half of the configuration.

One entity is one row, and the row holds three kinds of thing. The
columns that carry identity, because a key has to be selectable and
unique. The `body`, which is the entity's pydantic model dumped as
JSON and validated back through the same model on read: every field
the model declares, and for a pass-through model every extra beside
them, lives there and nowhere else. And, on the two kinds that can
hold one, the `secrets` column, which never carries anything the model
knows about.

No non-key model field has a column of its own. That is the point
rather than an omission: a field used to mean a column, a migration
and two hand-written mapper arms, and the four of them had to agree
about a tri-state or a default that only one of them stated. Adding a
field is now a change to the model, to the entity's example fragment
under `examples/`, and to the two generated reference artifacts, each
rebuilt by its own command.

A field can earn a column back, and the way it earns one is by needing
SQL. The first field something has to filter, join or index on gets a
column through `alembic revision --autogenerate` (runnable through
`vinga_server.db.migrations.autogen`), which reads the metadata below
and writes the candidate migration; the body keeps holding it, and the
column is a derived index rather than a second home for the value.
Nothing needs one today: the four reshaped tables are read whole and
assembled in Python.

The body is `Text` and not `JSON`. A `JSON` column would encode the
already-dumped string a second time, storing a quoted literal that no
`json_extract` can see into and that reads back as a string rather
than an object; `Text` is what `model_validate_json` is handed.

`devices` and `domain_settings` are already at this shape and do not
move. Neither has an entity model to validate a body through: a device
row is a bare list of agent names and a setting row is a scalar, both
read as JSON values rather than as a dumped model, and the device
lookup path selects `devices.c.agents` by name on a connection that
never migrates.

Referential integrity lives in the repository rather than in database
foreign keys: validation is single-sourced in the model/repository
layer, the layer the REST API uses too, and a constraint here would
duplicate half of those checks in a second place that knows less about
what a reference means.

Encrypted secrets never sit in a body. Each secret-bearing entity has
its own `secrets` JSON column mapping a credential slot to an
envelope, so replacing an entity does not touch its stored secrets and
a body written by a fragment that cannot carry ciphertext cannot erase
one.
"""

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    MetaData,
    Table,
    Text,
)

# Named constraints and indexes, so a later migration can address them.
# A constraint Postgres named for itself is one a migration has to look
# up before it can drop it, and a name that a convention produces is one
# both sides can write down.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s",
    "pk": "pk_%(table_name)s",
}

# The schema these tables live in, carried on the metadata rather than
# arranged with a `search_path`: which schema a table is in is a fact,
# and a fact has one home. It is also the seat of this chain's Alembic
# version table, which is what keeps the two chains apart inside one
# database, and the boundary the read-only analyst role is scoped
# outside of, because the `secrets` columns below are here.
SCHEMA = "domain"

metadata = MetaData(schema=SCHEMA, naming_convention=NAMING_CONVENTION)

# The one row agent_defaults may hold. The value is arbitrary and never
# shown; the check constraint on it is what makes the singleton a
# property of the schema rather than a convention in the repository.
AGENT_DEFAULTS_ID = "singleton"

providers = Table(
    "providers",
    metadata,
    # A provider is identified by its stage and its name together: two
    # stages may each define a "local" entry, and they are different
    # providers.
    Column("stage", Text, primary_key=True),
    Column("name", Text, primary_key=True),
    # The dumped ProviderConfig: its declared fields and the
    # pass-through options together, which is what the model itself
    # holds and so what a reader of the row gets back.
    Column("body", Text, nullable=False),
    # Option name to secret envelope, empty by default.
    Column("secrets", JSON, nullable=False, default=dict),
)

mcp_servers = Table(
    "mcp_servers",
    metadata,
    Column("name", Text, primary_key=True),
    # The dumped McpServerConfig. Values in its env and headers are
    # literal strings or today's $VAR reference strings, never
    # envelopes: an encrypted value lives in the secrets column below
    # under its dotted path, which is what keeps the body loadable into
    # McpServerConfig as is.
    Column("body", Text, nullable=False),
    # Dotted path (env.API_TOKEN, headers.Authorization) to envelope.
    Column("secrets", JSON, nullable=False, default=dict),
)

# The shared blocks of prompt text agents include by name: the name a
# layer references, and the dumped PromptFragmentConfig holding the
# text as it was written, because that is what the model is given.
prompt_fragments = Table(
    "prompt_fragments",
    metadata,
    Column("name", Text, primary_key=True),
    Column("body", Text, nullable=False),
)

agent_defaults = Table(
    "agent_defaults",
    metadata,
    Column("id", Text, primary_key=True),
    Column("body", Text, nullable=False),
    CheckConstraint(f"id = '{AGENT_DEFAULTS_ID}'", name="singleton"),
)

agents = Table(
    "agents",
    metadata,
    Column("name", Text, primary_key=True),
    Column("body", Text, nullable=False),
)

# An entity table rather than bare binding rows, so the per-device
# runtime field (#92 stage 1) is an additive column on a row that
# already exists rather than a reshaping. Nothing runtime-shaped is
# built here.
devices = Table(
    "devices",
    metadata,
    Column("mac", Text, primary_key=True),
    Column("agents", JSON, nullable=False),
)

# Domain-level scalars, default_agent being the only one today. A
# key/value table so the next one does not need a migration of its own.
domain_settings = Table(
    "domain_settings",
    metadata,
    Column("key", Text, primary_key=True),
    Column("value", JSON, nullable=False),
)

DEFAULT_AGENT_KEY = "default_agent"
