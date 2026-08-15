"""The conversation store: what was said, kept where it can be queried.

`conversations.db` sits beside `samtal.db` in `server.database.dir` and
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
- `store.py`: the writer thread, its queue, retention and the purge
  helper.
- `docgen.py`: the reference renderer behind
  `docs/reference/conversations-schema.md`.
- `cli.py`: `samtal-server conversations purge` and `... schema`.

Nothing here is reached from a running server yet: the configuration
key, the store's construction and the session sink land with the
milestone that makes the switch real, so that the first release in
which an operator can turn recording on is the first in which it does
everything its documentation says.
"""

from samtal_server.conversations.records import (
    SessionTurns,
    ToolInvocation,
    TurnLeg,
    TurnRecord,
    TurnRecorder,
    TurnStore,
)
from samtal_server.conversations.store import (
    DATABASE_FILENAME,
    ConversationStore,
    conversations_path,
    open_conversations,
    purge,
    read_conversations,
)

__all__ = [
    "DATABASE_FILENAME",
    "ConversationStore",
    "SessionTurns",
    "ToolInvocation",
    "TurnLeg",
    "TurnRecord",
    "TurnRecorder",
    "TurnStore",
    "conversations_path",
    "open_conversations",
    "purge",
    "read_conversations",
]
