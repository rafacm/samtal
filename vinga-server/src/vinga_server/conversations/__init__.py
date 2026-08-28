"""The conversation store: what was said, kept where it can be queried.

The `record` schema sits beside the `domain` one in the database
`server.database` names, and holds six tables: the session spine, the
conversation a turn belongs to, the turn timeline, the tool invocations
a turn issued, the decision track underneath them (#120), and the
recap checkpoints a resumed thread accrues by consent (#190).
Audio never enters it; the capture triplet is the recording and this is
the queryable record. A read-only role scoped to this schema alone is
how an analyst queries it (#283).

The two entities are two readings of one set of rows rather than two
stores. A session is one connection episode and a conversation is a
durable thread with exactly one agent, which may span several of them;
every turn names both, so the turns of one session can belong to
several threads and one thread's turns can come from several sessions.

The pieces, in the order they are met:

- `schema.py`: the tables, every column carrying the comment the
  reference is rendered from.
- `records.py`: `TurnRecord` and `ToolInvocation`, what the pipeline
  hands over per completed turn, the recorder seam it hands them
  through, and the `Acknowledgement` a durable write answers.
- `threads.py`: the thread store. What a conversation's life is as
  rows: when one becomes a row, what it is called, when it stops being
  current, what retention takes, what erasure reaches, and how a spoken
  description is matched against one.
- `store.py`: the writer thread, its queue, the per-session sink a
  device session attaches, and retention.
- `api.py`: the two namespaces on the gated `/api`, `/sessions` and
  `/conversations`, registered by `config/api.py` so a route cannot be
  served without being in the committed contract.
- `docgen.py`: the reference renderer behind
  `docs/reference/conversations-schema.md`.
- `cli.py`: `vinga-server conversations schema`.

Off unless a deployment asks for it. `server.conversations.enabled` is
what builds any of this: absent or off, no writer is started, no row is
ever written and no server behaviour changes, which is the whole of what
an operator who wants none of it has to know. The tables exist either
way, because the schema is migrated at boot; empty tables are not a
recording.

Enabled, the composition happens in one place and nowhere else: the
lifespan (#142). It builds the store cold, so opening and migrating the
schema fails the startup rather than the first conversation, and hands
it
to the runtime factory, which is how a turn's record reaches it; and it
owns the writer thread, started once the store is built and stopped when
the lifespan unwinds, with the drain of what is queued happening after
every session has stopped producing. A session opens its row after the hello
with the same manifest the capture is given, attaches a `SessionSink`
after the capture's tap, and closes the row after `session_closed`, so
what is recorded is the decision track and the turns beside it.

A boot that records nothing still migrates the schema, because
switching recording off is not the same as making what was recorded
unreadable.
"""

from vinga_server.conversations.records import (
    Acknowledgement,
    SessionTurns,
    ToolInvocation,
    TurnLeg,
    TurnRecord,
    TurnRecorder,
    TurnStore,
)
from vinga_server.conversations.store import (
    CONVERSATIONS_CHAIN,
    ConversationStore,
    SessionSink,
    open_conversations,
)

__all__ = [
    "Acknowledgement",
    "CONVERSATIONS_CHAIN",
    "ConversationStore",
    "SessionSink",
    "SessionTurns",
    "ToolInvocation",
    "TurnLeg",
    "TurnRecord",
    "TurnRecorder",
    "TurnStore",
    "open_conversations",
]
