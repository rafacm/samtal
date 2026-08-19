"""The conversation store: what was said, kept where it can be queried.

`conversations.db` sits beside `vinga.db` in `server.database.dir` and
holds four tables: the session spine, the turn timeline, the tool
invocations a turn issued, and the decision track underneath them
(#120). Audio never enters it; the capture triplet is the recording and
this is the queryable record.

The pieces, in the order they are met:

- `schema.py`: the tables, every column carrying the comment the
  reference is rendered from.
- `records.py`: `TurnRecord` and `ToolInvocation`, what the pipeline
  hands over per completed turn, and the recorder seam it hands them
  through.
- `store.py`: the writer thread, its queue, the per-session sink a
  device session attaches, retention and the purge helper.
- `docgen.py`: the reference renderer behind
  `docs/reference/conversations-schema.md`.
- `cli.py`: `vinga-server conversations purge` and `... schema`.

Off unless a deployment asks for it. `server.conversations.enabled` is
what builds any of this: absent or off, nothing here is constructed, no
file is created and no server behaviour changes, which is the whole of
what an operator who wants none of it has to know.

Enabled, the composition happens in one place and nowhere else: the
lifespan (#142). It builds the store cold, so opening and migrating the
file fails the startup rather than the first conversation, and hands it
to the runtime factory, which is how a turn's record reaches it; and it
owns the writer thread, started once the store is built and stopped when
the lifespan unwinds, with the drain of what is queued happening after
every session has stopped producing. A session opens its row after the hello
with the same manifest the capture is given, attaches a `SessionSink`
after the capture's tap, and closes the row after `session_closed`, so
what is recorded is the decision track and the turns beside it.

A boot that records nothing still migrates a file that is already there,
because switching recording off is not the same as making what was
recorded unreadable.
"""

from vinga_server.conversations.records import (
    SessionTurns,
    ToolInvocation,
    TurnLeg,
    TurnRecord,
    TurnRecorder,
    TurnStore,
)
from vinga_server.conversations.store import (
    DATABASE_FILENAME,
    ConversationStore,
    SessionSink,
    conversations_path,
    migrate_existing,
    open_conversations,
    purge,
    read_conversations,
)

__all__ = [
    "DATABASE_FILENAME",
    "ConversationStore",
    "SessionSink",
    "SessionTurns",
    "ToolInvocation",
    "TurnLeg",
    "TurnRecord",
    "TurnRecorder",
    "TurnStore",
    "conversations_path",
    "migrate_existing",
    "open_conversations",
    "purge",
    "read_conversations",
]
