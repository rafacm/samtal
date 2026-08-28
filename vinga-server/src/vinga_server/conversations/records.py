"""What the pipeline hands the store, one record per completed turn.

A dedicated content channel beside the event tap, rather than text read
back off the events: tool arguments and results never rode the events at
all, and the events are about to lose their `text` field, so a store fed
from them would be built twice and thrown away once.

Frozen, and imported by both sides while importing neither. The runtime
assembles these and the writer consumes them, which is the whole of the
contract between them; storage policy (which halves of a record the two
switches null) lives in the writer, so what arrives here is always the
full record.

Every duration is an integer millisecond count and every count is what
the provider reported, `None` where nothing was measured. `None` is
therefore "not measured", never "zero", which is the distinction a
latency query has to be able to make.
"""

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ToolInvocation:
    """One call the model issued, whatever became of it.

    Every call becomes one of these, including the ones the routing
    hides today: a malformed call is classified by its name and carries
    `malformed`, an unknown name carries its canned refusal as the
    result, and a handover is recorded from the tool loop's own
    handling, refusals with their error results and a successful switch
    with no result and no duration.
    """

    # Order within the round's call list, as the model issued it.
    position: int
    # One of schema.TOOL_SOURCES, decided by the classifier the pipeline
    # consults before it executes anything.
    source: str
    # The owning MCP entry's configured name, for an `mcp` call only.
    entry: str | None = None
    name: str | None = None
    malformed: bool = False
    arguments: dict[str, Any] | None = None
    result: str | None = None
    is_error: bool = False
    duration_ms: int | None = None


@dataclass(frozen=True)
class TurnLeg:
    """What one agent contributed to a reply a handover split.

    Its own shape rather than a bare dict because the writer nulls its
    halves separately: the text under text-off, the token counts under
    metrics-off. A turn's totals blend rounds and, after a handover,
    agents that may run different models, so the per-leg counts are what
    keeps the attribution honest without a join.
    """

    # Whoever was speaking when this leg closed, which the reply path
    # holds as the agent it is currently on and may not have set.
    # Nullable for that reason alone, and not for the turn's: a leg is a
    # share of a reply, while the turn's own agent decides which thread
    # a row belongs to and is therefore required below.
    agent: str | None
    text: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True)
class TurnRecord:
    """One completed utterance-and-reply cycle.

    Assembled where `replied` is emitted, so a reply that was cancelled
    or failed records what its `finally` saw, exactly as `replied` does;
    an utterance that produced no transcript produces no record, which
    mirrors the events.
    """

    # The session loop's clock reading at the utterance's `heard`, the
    # same reading an `Emission` carries. The store turns it into the
    # row's `t_ms`, because only the store knows the reading the session
    # opened at, which is what the offset is measured from and what
    # aligns a turn with its event and with the capture's audio.
    at: float
    # The thread this turn was spoken on. Snapshotted with `agent` below
    # when the turn began rather than read when it ended, because a
    # handover moves the active agent mid-reply and this record is
    # assembled in the reply's `finally`: reading either of them there
    # would put the handover turn on the thread it handed over TO. The
    # writer derives the conversation row from these two fields alone,
    # which is why they travel together and why neither is optional.
    conversation: str
    # The agent that owns the turn, which is the one it started with. A
    # split reply's per-agent truth is in `legs`. Required, and required
    # in the type rather than only in the sentence above: the writer
    # materializes the thread row from this field and the one before it,
    # both of those columns are not null, and a record that could arrive
    # without an agent is a turn the store would have to write outside
    # every thread, where nothing would ever prune it.
    agent: str
    heard: str | None = None
    heard_duration_s: float | None = None
    language: str | None = None
    language_confidence: float | None = None
    reply: str | None = None
    # Only when a handover split the reply; a single-agent turn carries
    # nothing here, since the reply column already is the whole of it.
    legs: tuple[TurnLeg, ...] = ()
    asr_ms: int | None = None
    first_token_ms: int | None = None
    llm_ms: int | None = None
    # The reply's first synthesis request to its first audio bytes. None
    # when the reply spoke nothing, which a tool-only or empty reply is.
    tts_first_audio_ms: int | None = None
    rounds: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    tools: tuple[ToolInvocation, ...] = ()


class TurnRecorder(Protocol):
    """One session's content channel, as the runtime sees it.

    Structural rather than an import, and for the same reason the event
    tap is: the runtime hands a record to whoever is listening and must
    not learn that a database is behind it. A runtime built without one
    behaves exactly as it did before this channel existed, which is what
    the optional dependency compared `is not None` buys.

    Never blocking and never raising is the contract: this is called on
    the session loop at the end of a reply, and a store that made a
    reply wait would be the one thing the whole write path is built to
    prevent."""

    def record_turn(self, record: TurnRecord) -> None: ...


class TurnStore(Protocol):
    """The same channel from the composition root's side, where one
    object serves every session and the records are keyed by one."""

    def record_turn(self, session_id: str, record: TurnRecord) -> None: ...


@dataclass(frozen=True)
class SessionTurns:
    """A `TurnStore` bound to one session, which is what a runtime holds.

    The binding happens where the session id is first known, so nothing
    downstream has to carry an identity it has no other use for."""

    store: TurnStore
    session_id: str

    def record_turn(self, record: TurnRecord) -> None:
        self.store.record_turn(self.session_id, record)


__all__ = [
    "SessionTurns",
    "ToolInvocation",
    "TurnLeg",
    "TurnRecord",
    "TurnRecorder",
    "TurnStore",
]
