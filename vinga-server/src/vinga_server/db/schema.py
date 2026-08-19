"""The tables holding the domain half of the configuration.

Typed columns carry identity and the references between entities; JSON
columns carry the nested structures the pydantic models already own
(provider options, filler sections, args, env, headers, binding lists).

Referential integrity lives in the repository rather than in SQLite
foreign keys: validation is single-sourced in the model/repository
layer, the layer the REST API will use too, and SQLite's foreign key
enforcement is a per-connection pragma that would duplicate half of
those checks in a second, weaker place.

Encrypted secrets never sit in the model-shaped columns. Each
secret-bearing entity has its own `secrets` JSON column mapping a
credential slot to an envelope, so replacing an entity does not touch
its stored secrets and a loaded row still validates through the
existing pydantic models unchanged.
"""

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    Float,
    MetaData,
    Table,
    Text,
    false,
)

# Named constraints and indexes, so a later migration can address them.
# SQLite cannot drop an unnamed constraint, and Alembic's batch mode
# needs a name to rebuild a table around one.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)

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
    Column("type", Text, nullable=False),
    # The environment-reference credential form, carried over verbatim.
    # A declared model field, so it is excluded from options (which
    # holds exactly the model extras) and needs its own column, or a
    # repository written against these definitions would silently drop
    # every provider's credential reference.
    Column("api_key_env", Text, nullable=True),
    # The operator's egress assertion. Null means unstated, which is
    # what most types want, so it cannot be a boolean with a default.
    Column("egress", Boolean, nullable=True),
    # The pass-through options, exactly today's extras.
    Column("options", JSON, nullable=False, default=dict),
    # Option name to secret envelope, empty by default.
    Column("secrets", JSON, nullable=False, default=dict),
)

mcp_servers = Table(
    "mcp_servers",
    metadata,
    Column("name", Text, primary_key=True),
    Column("transport", Text, nullable=False),
    Column("command", Text, nullable=True),
    Column("args", JSON, nullable=False, default=list),
    # Values in env and headers are literal strings or today's $VAR
    # reference strings, never envelopes. An encrypted value lives in
    # the secrets column below under its dotted path, which is what
    # keeps this half of the row loadable into McpServerConfig as is.
    Column("env", JSON, nullable=False, default=dict),
    Column("url", Text, nullable=True),
    Column("headers", JSON, nullable=False, default=dict),
    Column("egress", Boolean, nullable=True),
    Column("tool_timeout_s", Float, nullable=False),
    # The operator's guidance about using this server's tools, injected
    # into the prompt of every agent granted the entry. Nullable, since
    # NULL is the unset the model already means, and a row written
    # before the column existed reads as having none.
    Column("instructions", Text, nullable=True),
    # Whether the guidance the server ships about itself is injected.
    # NOT NULL with a database-level default rather than a Python-side
    # rescue of NULL: a row written before the column existed then reads
    # false from the database itself, which is what the opt-in means.
    Column(
        "use_server_instructions",
        Boolean,
        nullable=False,
        server_default=false(),
        default=False,
    ),
    # The published prompts this entry injects, by the names the server
    # lists them under. Nullable, since NULL is the "none" the model
    # already means.
    Column("inject_prompts", JSON, nullable=True),
    # Dotted path (env.API_TOKEN, headers.Authorization) to envelope.
    Column("secrets", JSON, nullable=False, default=dict),
)

# The shared blocks of prompt text agents include by name. Two columns
# and nothing else: the name a layer references, and the text, held as
# it was written because that is what the model is given.
prompt_fragments = Table(
    "prompt_fragments",
    metadata,
    Column("name", Text, primary_key=True),
    Column("text", Text, nullable=False),
)

agent_defaults = Table(
    "agent_defaults",
    metadata,
    Column("id", Text, primary_key=True),
    Column("llm", Text, nullable=True),
    Column("asr", Text, nullable=True),
    Column("tts", Text, nullable=True),
    Column("vad", Text, nullable=True),
    # Null means inherit nothing; a list replaces rather than extends,
    # so an empty list and a null are different configurations.
    Column("mcp", JSON, nullable=True),
    Column("filler", JSON, nullable=True),
    # The fragment names this layer's prompt carries, under the same
    # rule as mcp above: null is inherit and an empty list opts out.
    Column("prompt_includes", JSON, nullable=True),
    CheckConstraint(f"id = '{AGENT_DEFAULTS_ID}'", name="singleton"),
)

agents = Table(
    "agents",
    metadata,
    Column("name", Text, primary_key=True),
    Column("prompt", Text, nullable=False, default=""),
    Column("llm", Text, nullable=True),
    Column("asr", Text, nullable=True),
    Column("tts", Text, nullable=True),
    Column("vad", Text, nullable=True),
    Column("mcp", JSON, nullable=True),
    Column("filler", JSON, nullable=True),
    Column("prompt_includes", JSON, nullable=True),
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
