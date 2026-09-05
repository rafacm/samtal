"""The tables holding what was said, and what it cost to say it.

Its own `MetaData` and its own schema (`record`), beside the
domain configuration rather than inside it: the configuration is what an
operator writes and the server reads at boot, this is what the server
writes and an operator reads afterwards, and the two have different
retention, different privacy switches and different reasons to be
deleted. The split is also what the read-only analyst role is scoped to:
`vinga_ro` reads everything here and nothing next door, so a query over
what was said cannot reach a stored secret's ciphertext.

Typed columns carry identity, the references between rows and the
numbers a query filters on; JSON carries the structures the manifest and
the pydantic layers already own. Referential integrity is the writer's,
not the database's, for the reason `db/schema.py` gives: validation
belongs in one layer, and a constraint here would be a second place that
knows less about what a reference means.
The writer is not the only deleter either (retention takes whole
sessions, including one that is still talking), so what keeps its
inserts honest against a deletion is a check inside each of its
transactions, described in `store.py`, rather than a constraint here.

Every column carries a `comment=`. That is what
`docs/reference/conversations-schema.md` is rendered from, and the drift
test fails on a column without one, the same discipline
`Field(description=...)` enforces for the domain models.

The schema is called `record` and the thread table inside it is called
`conversations`, so SQL spells that table `record.conversations`. The
schema name says what the whole of it is, the durable record of what
was said and what it cost to say it, and the table name is one entity's:
a thread between a user and exactly one agent, spanning sessions. The
package, the config section, the events and the reference document all
keep `conversations`, because they name this store from outside, where
the entity is the thing a reader is after. A `turns` row names both the
session it was spoken in and the thread it belongs to, so the session
view and the thread view are two projections of one set of rows rather
than two stores, and no dialogue is written twice.

Two conventions run through the whole schema:

- **Timestamps are UTC ISO-8601 text and offsets are integer
  milliseconds**, the offsets aligned with the capture's `t_ms` because
  both derive from the session loop's clock reading at session open. A
  row and a capture triplet for the same session share one timeline.
- **Row ids are `bigint` identity columns.** A sequence never hands
  out a value it has already handed out, which is the property the three
  cursor tables need: retention deletes from exactly the end a cursor
  points past, and a reused id would hand a paginating client someone
  else's row under a cursor it had already consumed. `bigint` is what
  makes the API's `MAX_ROW_ID` true by declaration rather than by
  folklore about what a row id happens to be. `tool_invocations` is not
  a cursor table (it is read through its parent turn, never paginated on
  its own) and is declared the same way regardless, because a row still
  needs an id and one rule is cheaper to read than two.
"""

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Float,
    Identity,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    text,
)

# The same convention the domain schema uses, and for the same reason:
# a constraint the database named for itself is one a migration has to
# look up before it can drop it.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s",
    "pk": "pk_%(table_name)s",
}

# The schema this store lives in, carried on the metadata rather than
# arranged with a `search_path`, for the reason `db/schema.py` gives.
SCHEMA = "record"

metadata = MetaData(schema=SCHEMA, naming_convention=NAMING_CONVENTION)

# `none_as_null` so that a column the text switch emptied is SQL NULL
# rather than the four bytes `null`. One rule for every JSON column
# here: the reference says these columns are null under their switch,
# and a reader filtering on `IS NULL` has to find them.
JSON_OR_NULL = JSON(none_as_null=True)

# Where a tool call was routed, decided once by the classifier the
# pipeline consults before it executes anything and stored as written.
# A closed set in the schema rather than only in the code, because the
# whole value of the column is that a query may enumerate it.
TOOL_SOURCES = ("builtin", "device", "mcp", "unknown")

# What ended a session, written from the `session_closed` event's own
# token. Not a check constraint: the five tokens are latched at five
# sites in the device edge, which is where they are enforced, and a
# database that refused an unforeseen sixth would drop the session row
# rather than record the close it could not name.
CLOSE_REASONS = ("limit", "idle", "drain", "client", "error")

sessions = Table(
    "sessions",
    metadata,
    Column(
        "id",
        BigInteger,
        Identity(),
        primary_key=True,
        comment="Monotonic row id, never reused. The session list's cursor.",
    ),
    Column(
        "session",
        Text,
        nullable=False,
        unique=True,
        comment=(
            "The session's uuid hex: the join key for every other table here, "
            "and the correlation key to the capture triplet of the same name."
        ),
    ),
    Column(
        "device",
        Text,
        nullable=True,
        comment=(
            "The device's MAC in canonical form. Null when the session was "
            "rejected before one was understood."
        ),
    ),
    Column(
        "client",
        Text,
        nullable=True,
        comment="The client identifier the device announced, when it announced one.",
    ),
    Column(
        "agent",
        Text,
        nullable=True,
        comment="The agent the session opened with, before any handover.",
    ),
    Column(
        "agents",
        JSON_OR_NULL,
        nullable=True,
        comment="Every agent the device is bound to, as the binding resolved at open.",
    ),
    Column(
        "protocol",
        Text,
        nullable=True,
        comment="The device protocol version this session negotiated.",
    ),
    Column(
        "started_at",
        Text,
        nullable=False,
        comment=(
            "When the session opened, UTC ISO-8601. Survives both storage "
            "switches: retention prunes on this column, and a record that "
            "cannot be pruned cannot be kept."
        ),
    ),
    Column(
        "closed_at",
        Text,
        nullable=True,
        comment=(
            "When the session closed, UTC ISO-8601. Null in a session that is "
            "still running, and in one whose close was never persisted, which "
            "a crash and a failed close transaction both leave behind."
        ),
    ),
    Column(
        "duration_s",
        Float,
        nullable=True,
        comment=(
            "How long the session lasted, in seconds. A measured number: null "
            "under metrics-off."
        ),
    ),
    Column(
        "close_reason",
        Text,
        nullable=True,
        comment=(
            "What ended the session, one of: " + ", ".join(CLOSE_REASONS) + ". "
            "The first cause to fire wins. Null until the session closes."
        ),
    ),
    Column(
        "server_version",
        Text,
        nullable=True,
        comment="The server version that recorded this session.",
    ),
    Column(
        "revision",
        Text,
        nullable=True,
        comment="The build revision that recorded this session.",
    ),
    Column(
        "providers",
        JSON_OR_NULL,
        nullable=True,
        comment=(
            "The resolved provider entry per pipeline stage, the same structure "
            "the capture manifest carries. Holds environment variable names, "
            "never credentials."
        ),
    ),
    Column(
        "metrics",
        Boolean,
        nullable=False,
        comment=(
            "Whether metrics storage was on for this session, so a null number "
            "is distinguishable from a number that was never stored."
        ),
    ),
    Column(
        "text",
        Boolean,
        nullable=False,
        comment=(
            "Whether text storage was on for this session, so a null utterance "
            "is distinguishable from an utterance that was never stored."
        ),
    ),
    Column(
        "dropped",
        Integer,
        nullable=False,
        server_default=text("0"),
        comment=(
            "Records this session lost: events refused at the in-flight bound, "
            "and anything a failed transaction rolled back. Written at close, "
            "so the store records its own incompleteness the way the capture "
            "manifest records `complete`. Zero under metrics-off."
        ),
    ),
    Index("ix_sessions_device", "device"),
    Index("ix_sessions_started_at", "started_at"),
)

conversations = Table(
    "conversations",
    metadata,
    Column(
        "id",
        BigInteger,
        Identity(),
        primary_key=True,
        comment=(
            "Monotonic row id, never reused. The tie-break half of the thread "
            "listing's keyset cursor, since activity moves and two threads can "
            "share a timestamp."
        ),
    ),
    Column(
        "conversation",
        Text,
        nullable=False,
        unique=True,
        comment=(
            "The thread's uuid hex: the join key `turns.conversation` and "
            "`conversation_milestones.conversation` carry, and what a resume "
            "addresses. Minted by the runtime at the activation that opens the "
            "thread, the same shape and role as `sessions.session`."
        ),
    ),
    Column(
        "agent",
        Text,
        nullable=False,
        comment=(
            "The agent this thread belongs to, and the only agent it will ever "
            "belong to: a conversation is a dialogue with exactly one agent, so "
            "a handover starts a second thread rather than moving this one. The "
            "name is the one that agent has now rather than the one it had "
            "then, because renaming an agent rewrites this column and is what "
            "keeps the thread reachable; the dated columns beside it, "
            "`sessions.agent` and `turns.agent`, keep the name of the moment "
            "they record. Not null, unlike those two, because a thread with no "
            "agent is not a thread."
        ),
    ),
    Column(
        "device",
        Text,
        nullable=False,
        comment=(
            "The device the thread was begun on, in canonical MAC form. "
            "Provenance rather than ownership: a thread is agent-scoped, so a "
            "resume from any device bound to that agent reaches it, and this "
            "column says where it started rather than where it may be "
            "continued."
        ),
    ),
    Column(
        "title",
        Text,
        nullable=True,
        comment=(
            "What the thread is called, derived from the earliest utterance "
            "stored on it and truncated. The earliest utterance rather than "
            "the earliest turn, because a thread a session moved onto opens "
            "with the answer that greeted the move and nothing was heard on "
            "it. Conversation text, so it is null under text-off, and null in "
            "a thread that has never stored one."
        ),
    ),
    Column(
        "incomplete",
        Boolean,
        nullable=False,
        server_default=text("false"),
        comment=(
            "Whether a write this thread needed was lost, so a resume can say "
            "the record has gaps. Product state rather than telemetry, and "
            "therefore deliberately outside the metrics switch: "
            "`sessions.dropped` is zeroed under metrics-off and this is not. "
            "Written by the durable path, which arrives with the writer's "
            "acknowledgements; false in every thread until then."
        ),
    ),
    Column(
        "created_at",
        Text,
        nullable=False,
        comment=(
            "When the thread's first turn landed, UTC ISO-8601. The row "
            "materializes with that turn rather than at activation, so a wake "
            "that produced no transcript leaves no empty thread behind."
        ),
    ),
    Column(
        "last_active_at",
        Text,
        nullable=False,
        comment=(
            "When the thread's most recent turn landed, UTC ISO-8601, rewritten "
            "by every turn. The listing orders on it and retention prunes on "
            "it, which is what makes retention thread-aware: a thread stays "
            "whole while it is being talked to, however old the session that "
            "began it."
        ),
    ),
    Index("ix_conversations_agent_activity", "agent", "last_active_at", "id"),
    Index("ix_conversations_last_active", "last_active_at", "id"),
)

turns = Table(
    "turns",
    metadata,
    Column(
        "id",
        BigInteger,
        Identity(),
        primary_key=True,
        comment="Monotonic row id, never reused. The turn timeline's cursor.",
    ),
    Column(
        "session",
        Text,
        nullable=False,
        comment="The `sessions.session` this turn belongs to.",
    ),
    Column(
        "conversation",
        Text,
        nullable=False,
        comment=(
            "The `conversations.conversation` this turn belongs to, which with "
            "the column above is what makes the session view and the thread "
            "view two readings of one set of rows rather than two stores. Not "
            "null: every stored turn belongs to the thread that was active when "
            "it was spoken, the whole of the v1 rule, and a database this build "
            "wrote holds no turn from before threads existed."
        ),
    ),
    Column(
        "t_ms",
        Integer,
        nullable=False,
        comment=(
            "The utterance's offset from session open, in milliseconds, aligned "
            "with its `heard` event and with the capture's audio. Structural "
            "rather than telemetry: it survives both switches."
        ),
    ),
    Column(
        "agent",
        Text,
        nullable=True,
        comment=(
            "The agent that owns this turn, which is the one it started with "
            "and therefore the one whose thread the column above names. A "
            "handover makes it different from the session's, and makes it "
            "different from the agent that finished the reply; `legs` is where "
            "a split reply's per-agent truth lives."
        ),
    ),
    Column(
        "heard",
        Text,
        nullable=True,
        comment="What the device's user said, as transcribed. Null under text-off.",
    ),
    Column(
        "heard_duration_s",
        Float,
        nullable=True,
        comment="How long the utterance lasted, in seconds. Null under metrics-off.",
    ),
    Column(
        "language",
        Text,
        nullable=True,
        comment=(
            "The language the transcript was recognized as. Neither a measured "
            "number nor conversation text, so it survives both switches."
        ),
    ),
    Column(
        "language_confidence",
        Float,
        nullable=True,
        comment="How sure the recognizer was of that language. Null under metrics-off.",
    ),
    Column(
        "reply",
        Text,
        nullable=True,
        comment=(
            "What the assistant said, the legs joined. Null under text-off, and "
            "null when the reply spoke nothing."
        ),
    ),
    Column(
        "legs",
        JSON_OR_NULL,
        nullable=True,
        comment=(
            "One entry per agent that took part in this turn, `{agent, text, "
            "input_tokens, output_tokens}`, present only when a handover split "
            "the reply. The text half is null under text-off and the token "
            "halves under metrics-off, because a turn's totals blend agents "
            "that may use different models."
        ),
    ),
    Column(
        "asr_ms",
        Integer,
        nullable=True,
        comment=(
            "Transcription elapsed, in milliseconds. Null where no elapsed was "
            "measured this turn, and under metrics-off."
        ),
    ),
    Column(
        "first_token_ms",
        Integer,
        nullable=True,
        comment="Request to first token of the reply, in milliseconds. Null under metrics-off.",
    ),
    Column(
        "llm_ms",
        Integer,
        nullable=True,
        comment="The reply's LLM round durations summed, in milliseconds. Null under metrics-off.",
    ),
    Column(
        "tts_first_audio_ms",
        Integer,
        nullable=True,
        comment=(
            "The reply's first synthesis request to its first audio bytes, in "
            "milliseconds, measured at the provider boundary and deliberately "
            "not at the device. Null when the reply spoke nothing, and under "
            "metrics-off."
        ),
    ),
    Column(
        "rounds",
        Integer,
        nullable=True,
        comment="How many LLM rounds the reply took. Null under metrics-off.",
    ),
    Column(
        "input_tokens",
        Integer,
        nullable=True,
        comment=(
            "Input tokens summed across the turn's rounds; OTel's "
            "`gen_ai.usage.input_tokens`. Null when the provider reported no "
            "usage, and under metrics-off."
        ),
    ),
    Column(
        "output_tokens",
        Integer,
        nullable=True,
        comment=(
            "Output tokens summed across the turn's rounds; OTel's "
            "`gen_ai.usage.output_tokens`. Null when the provider reported no "
            "usage, and under metrics-off."
        ),
    ),
    Column(
        "tool_calls",
        Integer,
        nullable=False,
        comment=(
            "How many tool invocations this turn issued, which is how many "
            "`tool_invocations` rows point at it. Structural rather than "
            "telemetry: it survives both switches."
        ),
    ),
    Index("ix_turns_session", "session", "id"),
    Index("ix_turns_conversation", "conversation", "id"),
)

tool_invocations = Table(
    "tool_invocations",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True, comment="Row id."),
    Column(
        "turn",
        BigInteger,
        nullable=False,
        comment=(
            "The `turns.id` this call belongs to, resolved by the writer "
            "inserting the turn and its calls in one transaction."
        ),
    ),
    Column(
        "session",
        Text,
        nullable=False,
        comment=(
            "The `sessions.session` this call belongs to, denormalized so "
            "retention and a session-scoped query need no join."
        ),
    ),
    Column(
        "position",
        Integer,
        nullable=False,
        comment=(
            "Order within the round's call list, as the model issued it, "
            "handovers included."
        ),
    ),
    Column(
        "source",
        Text,
        nullable=False,
        comment="Where the call was routed, one of: " + ", ".join(TOOL_SOURCES) + ".",
    ),
    Column(
        "entry",
        Text,
        nullable=True,
        comment=(
            "The owning MCP entry's configured name for an `mcp` call, null "
            "otherwise. A name this deployment chose, so it survives text-off."
        ),
    ),
    Column(
        "name",
        Text,
        nullable=True,
        comment=(
            "The called tool's name. Null under text-off: a tool's name "
            "originates off this server (a device's self-description, an MCP "
            "far side) exactly as its result does."
        ),
    ),
    Column(
        "malformed",
        Boolean,
        nullable=False,
        comment="Whether the model's arguments were not a JSON object.",
    ),
    Column(
        "arguments",
        JSON_OR_NULL,
        nullable=True,
        comment="What the model passed. Null under text-off, and null when malformed.",
    ),
    Column(
        "result",
        Text,
        nullable=True,
        comment="What the call answered, including a refusal. Null under text-off.",
    ),
    Column(
        "is_error",
        Boolean,
        nullable=False,
        comment="Whether the call answered as an error.",
    ),
    Column(
        "duration_ms",
        Integer,
        nullable=True,
        comment=(
            "How long the call took, in milliseconds. Null where nothing ran, "
            "as for a refused or a successful handover, and under metrics-off."
        ),
    ),
    CheckConstraint(
        "source in (" + ", ".join(f"'{name}'" for name in TOOL_SOURCES) + ")",
        name="source",
    ),
    Index("ix_tool_invocations_session", "session"),
    Index("ix_tool_invocations_turn", "turn"),
)

conversation_milestones = Table(
    "conversation_milestones",
    metadata,
    Column(
        "id",
        BigInteger,
        Identity(),
        primary_key=True,
        comment=(
            "Row id, and what a later milestone names as its `parent` when it "
            "consumed this one."
        ),
    ),
    Column(
        "conversation",
        Text,
        nullable=False,
        comment="The `conversations.conversation` this checkpoint is on.",
    ),
    Column(
        "from_turn",
        BigInteger,
        nullable=False,
        comment=(
            "The `turns.id` of the first turn the summarizer actually read, "
            "and the first turn this checkpoint covers. Coverage is the "
            "inclusive range `from_turn` through `after_turn`, so that a recap "
            "bounded by its input budget cannot claim the turns it omitted: "
            "those are the ones below this id, truncated rather than "
            "summarized, and hydration treats them so."
        ),
    ),
    Column(
        "after_turn",
        BigInteger,
        nullable=False,
        comment=(
            "The `turns.id` of the last turn the summarizer read, and the last "
            "turn this checkpoint covers. Hydration reads this milestone plus "
            "the turns with a greater id, which is the whole of what "
            "the checkpoint replaces."
        ),
    ),
    Column(
        "parent",
        BigInteger,
        nullable=True,
        comment=(
            "The `conversation_milestones.id` whose text was part of this "
            "recap's input, and null when none was. The lineage is what makes "
            "erasure transitive: content that reached this row only through an "
            "earlier checkpoint is still this row's content."
        ),
    ),
    Column(
        "created_at",
        Text,
        nullable=False,
        comment="When the checkpoint was stored, UTC ISO-8601.",
    ),
    Column(
        "text",
        Text,
        nullable=True,
        comment=(
            "The recap, byte for byte as it was spoken. Conversation content "
            "under the uniform rule, so null under text-off, though the flow "
            "that writes one cannot run with text off."
        ),
    ),
    Index("ix_conversation_milestones_conversation", "conversation", "id"),
)

events = Table(
    "events",
    metadata,
    Column(
        "id",
        BigInteger,
        Identity(),
        primary_key=True,
        comment="Monotonic row id, never reused. The reconcile cursor.",
    ),
    Column(
        "session",
        Text,
        nullable=False,
        comment="The `sessions.session` this event belongs to.",
    ),
    Column(
        "t_ms",
        Integer,
        nullable=False,
        comment=(
            "The event's offset from session open, in milliseconds, aligned "
            "with the capture's decision track."
        ),
    ),
    Column(
        "name",
        Text,
        nullable=False,
        comment="The event name, from the event vocabulary the README's table defines.",
    ),
    Column(
        "level",
        Integer,
        nullable=False,
        comment="The numeric logging level the event was emitted at.",
    ),
    Column(
        "fields",
        JSON_OR_NULL,
        nullable=False,
        comment=(
            "The event's payload minus `event`, `session` and `device`, which "
            "live on this row and on the session. Field names are the event "
            "vocabulary's own, copied verbatim, which is the contract. Never "
            "content: the writer strips an utterance's or a reply's `text` and "
            "a tool call's `tool` name whatever the storage switches say, "
            "because content has its own tables and its own switch and this "
            "table is metadata-only by construction."
        ),
    ),
    Index("ix_events_session", "session", "id"),
)

# Declaration order, which is also the order the reference documents
# them in and the order a reader meets them: the connection spine, the
# threads that span it, the timeline both of them project, what the
# timeline called, the checkpoints a thread accrues, and the decision
# track underneath all of it.
TABLES = (
    sessions,
    conversations,
    turns,
    tool_invocations,
    conversation_milestones,
    events,
)
