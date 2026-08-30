"""The tables holding what an agent was asked to remember, and what is
currently true in one conversation.

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

Two tables and not one, because they answer different questions with
different shapes. A fact list is ordered, id-addressed and carries a
held area an undo reaches into; a ledger is keyed, current-only, and has
neither. One remembered fact is one row of `facts`; one thing that is
currently true in one conversation is one row of `state`.

No generated reference is rendered from these comments, unlike the
conversation record's. The record's page exists because operators query
that schema as `vinga_ro`; nobody but the server may read this one, so
the page would document a surface with no external reader. The comments
are here for whoever reads the tables through `psql`, and for the
migrations that reproduce them.
"""

from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    Identity,
    Index,
    MetaData,
    Table,
    Text,
    text,
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


class MemoryScope(StrEnum):
    """Where a remembered thing belongs, and therefore whose it is.

    Declared here and nowhere else. The store reads it, the check
    constraint below is built from it, the events' `scope` field is this
    enumeration, and #83's tool enum narrows it: a vocabulary spelled
    out a second time is a vocabulary with a drift pending.

    `owner` means a different thing under each member, which is what
    keeps one column doing the work of three tables: a thread's uuid hex
    under `conversation`, an agent's configured name under `agent`, a
    device's MAC under `device`. That is also the shape a per-user
    dimension arrives in by ordinary migration.
    """

    CONVERSATION = "conversation"
    AGENT = "agent"
    DEVICE = "device"


# The two a fact may carry. Conversation data lives in `state` and
# nowhere else: a ledger of what is currently true is not a list of
# what was said, so it has no id, no order and no held area, and a
# `facts` row claiming that scope would be a row nothing reads.
FACT_SCOPES = (MemoryScope.AGENT, MemoryScope.DEVICE)


def _in(scopes: tuple[MemoryScope, ...]) -> str:
    """One `IN` list, rendered from the vocabulary rather than typed
    beside it, so the constraint and the enumeration cannot disagree."""
    return ", ".join(f"'{scope}'" for scope in scopes)


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
            "order, and the id a fact is addressed by (updated, forgotten, "
            "restored) is this one, so a delete must never hand it out "
            "again."
        ),
    ),
    Column(
        "scope",
        Text,
        nullable=False,
        comment=(
            "Which memory this fact belongs to, one of: "
            + ", ".join(FACT_SCOPES)
            + ". It decides what `owner` names and which caps the row is "
            "pruned against."
        ),
    ),
    Column(
        "owner",
        Text,
        nullable=False,
        comment=(
            "Whose fact this is, read under `scope`: an agent's configured "
            "name, or a device's MAC in canonical form. Renaming an agent "
            "orphans its rows, exactly as it orphaned its file, and "
            "replacing a device orphans that device's notes."
        ),
    ),
    Column(
        "at",
        Text,
        nullable=False,
        comment=(
            "When the fact was last written, UTC ISO-8601, refreshed by a "
            "correction. A row written without its moment cannot recover "
            "it later, and an operator inspecting orphaned memory deserves "
            "to know when it accrued."
        ),
    ),
    Column(
        "fact",
        Text,
        nullable=False,
        comment="The fact as it was stored, normalized to one line.",
    ),
    Column(
        "forgotten_at",
        Text,
        nullable=True,
        comment=(
            "When the fact was forgotten, UTC ISO-8601, and null while it "
            "is active. A forgotten fact is held rather than erased, so "
            "the undo the softness exists for can reach it; permanent "
            "forgetting deletes the row instead and never lands here."
        ),
    ),
    Column(
        "forgotten_in",
        Text,
        nullable=True,
        comment=(
            "The `record.conversations.conversation` the fact was "
            "forgotten in, and null while it is active. A held fact shares "
            "that thread's lifecycle: the undo window is the thread's "
            "lifetime, and the thread's erasure, its retention prune and "
            "the sweep each take the rows naming it."
        ),
    ),
    CheckConstraint(f"scope in ({_in(FACT_SCOPES)})", name="scope"),
    # Null together or set together, because every path that addresses a
    # held fact reads one of the two and means the other: a row with a
    # moment and no thread could never be swept, and one with a thread
    # and no moment could never age out.
    CheckConstraint(
        "(forgotten_at is null) = (forgotten_in is null)", name="forgotten"
    ),
    # The main access path: one owner's rows within one scope, in
    # insertion order, which is what the ordered read, the prune and the
    # lookup filter all walk. All three columns, because the id is what
    # orders them and a prefix would leave the sort.
    Index("ix_facts_scope", "scope", "owner", "id"),
    # And the lifecycle path, which addresses held rows by the thread
    # that forgot them: restore, erasure, retention and the sweep. Partial
    # because the held area is a small corner of a large table, and a
    # full index would be walked under the writer's lock to find it.
    Index(
        "ix_facts_forgotten",
        "forgotten_in",
        "id",
        postgresql_where=text("forgotten_in IS NOT NULL"),
    ),
)

state = Table(
    "state",
    metadata,
    Column(
        "conversation",
        Text,
        primary_key=True,
        comment=(
            "The `record.conversations.conversation` this ledger belongs "
            "to. State shares its thread's lifecycle whole: the thread's "
            "erasure and its retention prune take these rows with them, "
            "and a deployment that stores no conversation text starts "
            "every thread with an empty ledger."
        ),
    ),
    Column(
        "key",
        Text,
        primary_key=True,
        comment=(
            "What the entry is called, chosen by the model. The identity "
            "of the row, which is the whole of the ledger's semantics: a "
            "write is an upsert by key, so there is no id and no order to "
            "address."
        ),
    ),
    Column(
        "value",
        Text,
        nullable=False,
        comment="What is currently true under that key, normalized to one line.",
    ),
    Column(
        "updated_at",
        Text,
        nullable=False,
        comment=(
            "When the entry was last written, UTC ISO-8601. The sweep "
            "reads it: state can precede its thread's row, which "
            "materializes at the first turn, so an orphan is only taken "
            "once it is older than the grace period."
        ),
    ),
)

# Declaration order, which is also the order a reader meets them: the
# facts an agent keeps, and the ledger one conversation keeps. The tuple
# exists so a caller enumerating this schema reads it off one home, the
# way `conversations.schema.TABLES` is read.
TABLES = (facts, state)
