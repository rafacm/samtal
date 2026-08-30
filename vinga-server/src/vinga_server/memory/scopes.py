"""Where a remembered thing belongs, declared once.

One enumeration, and nothing else in this module, because of who has to
read it. The tables build their check constraint from it, the store
decides which caps and which rendering a call means by it, the events'
`scope` field IS it, and #83's tool enum narrows it. A vocabulary
spelled out a second time is a vocabulary with a drift pending.

It is a module of its own rather than a few lines of `schema.py`, and
the reason is a tier rather than tidiness. `schema.py` declares tables,
so it imports SQLAlchemy, and the events package is client-half: a
laptop that installed this project with no extras renders
`docs/reference/events.md` from it and has no database driver at all.
A field that reached its closed set through the tables would have
carried the server half into that install and refused with an
ImportError on the one entry point whose every other answer is a
sentence. So the vocabulary lives where both halves can reach it,
importing nothing but the standard library.
"""

from enum import StrEnum


class MemoryScope(StrEnum):
    """Where a remembered thing belongs, and therefore whose it is.

    `owner` means a different thing under each member, which is what
    keeps one column doing the work of three tables: a thread's uuid hex
    under `conversation`, an agent's configured name under `agent`, a
    device's MAC under `device`. That is also the shape a per-user
    dimension arrives in by ordinary migration.

    The order is the order the blocks are read in and the order of their
    precedence: what this conversation is currently doing wins over what
    the agent knows, which wins over what the place knows.
    """

    CONVERSATION = "conversation"
    AGENT = "agent"
    DEVICE = "device"


# The two a fact may carry. Conversation data lives in the ledger and
# nowhere else: a record of what is currently true is not a list of what
# was said, so it has no id, no order and no held area, and a fact
# claiming that scope would be a row nothing reads.
FACT_SCOPES = (MemoryScope.AGENT, MemoryScope.DEVICE)

__all__ = [
    "FACT_SCOPES",
    "MemoryScope",
]
