"""The table holding what an agent was asked to remember.

Its own `MetaData` and its own schema (`memory`), beside the domain
configuration and the conversation record rather than inside either,
and the deciding rule is the one `db/schema.py` and
`conversations/schema.py` both state: which schema a store lives in is
a fact of that store. Neither existing schema fits. `domain` holds the
configuration an operator writes, and its advisory lock serializes
writers whole, so a `remember` riding that chain would wait behind an
`apply` transaction that is deliberately unbounded. `record` holds what
was said, is shaped by a session and thread retention that memory does
not have, and is granted to the read-only analyst role, which this
schema deliberately is not.

One remembered fact is one row, and the row carries nothing beyond the
four columns below. Scopes, stable identity beyond the row id, and
tombstones are #83's to add; a shape guessed ahead of them would be a
contract to unpick rather than a head start.

No generated reference is rendered from these comments, unlike the
conversation record's. The record's page exists because operators query
that schema as `vinga_ro`; nobody but the server may read this one, so
the page would document a surface with no external reader while
freezing a raw-table shape #83 reshapes. The comments are here for
whoever reads the table through `psql`, and for the migration that
reproduces them.
"""

from sqlalchemy import (
    BigInteger,
    Column,
    Identity,
    Index,
    MetaData,
    Table,
    Text,
)

# The same convention the two sibling schemas use, and for the same
# reason: a constraint the database named for itself is one a migration
# has to look up before it can drop it.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s",
    "pk": "pk_%(table_name)s",
}

# The schema this store lives in, carried on the metadata rather than
# arranged with a `search_path`, for the reason `db/schema.py` gives. It
# is also the seat of this chain's Alembic version table, which is what
# makes a third chain in one database a third chain rather than a head
# on somebody else's.
SCHEMA = "memory"

metadata = MetaData(schema=SCHEMA, naming_convention=NAMING_CONVENTION)

facts = Table(
    "facts",
    metadata,
    Column(
        "id",
        BigInteger,
        Identity(),
        primary_key=True,
        comment=(
            "Monotonic row id, never reused. Insertion order is reading "
            "order, and #83's stable identity and tombstones lean on ids "
            "that a delete cannot hand out again."
        ),
    ),
    Column(
        "agent",
        Text,
        nullable=False,
        comment=(
            "The agent that remembered this, by the name the configuration "
            "gives it. Renaming an agent orphans its rows, exactly as it "
            "orphaned its file."
        ),
    ),
    Column(
        "at",
        Text,
        nullable=False,
        comment=(
            "When the fact was remembered, UTC ISO-8601. Read by nothing "
            "in this issue: it is here because a row written without its "
            "moment cannot recover it later, an operator inspecting "
            "orphaned memory deserves to know when it accrued, and #83's "
            "tombstone lifetime question needs it."
        ),
    ),
    Column(
        "fact",
        Text,
        nullable=False,
        comment="The fact as it was stored, normalized to one line.",
    ),
    # The ordered read and the prune walk are the same access path: one
    # agent's rows, oldest first. Both halves, because the id is what
    # orders them and an index on the agent alone would leave the sort.
    Index("ix_facts_agent", "agent", "id"),
)

# Declaration order, which is also the order a reader meets them. One
# table today; the tuple exists so a caller enumerating this schema
# reads it off one home, the way `conversations.schema.TABLES` is read.
TABLES = (facts,)
