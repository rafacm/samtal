"""What the pipeline hands the store, one record per completed turn,
and the one shape that comes back the other way.

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

import threading
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class StoredTurn:
    """One turn as the store kept it, on its way back out.

    The one shape here that travels the other way, and it lives here for
    exactly the reason the rest do: the thread store reads it and the
    hydrator renders it, and a leaf both sides import is what keeps
    either of them from importing the other. It also keeps the store's
    read path off the provider vocabulary, which is what a rendered
    turn is written in.

    Both text halves are optional because both are under the text
    switch: a deployment that stores no text stores the turn and none of
    the words in it. `tools` carries the names of the calls that turn
    made, in the order the model issued them, with the ones the store
    could not name already left out.

    `id` is the row's own, and it travels because a recap has to say
    which turns it actually read: a checkpoint records the first and the
    last, so a summary bounded by its input budget cannot claim coverage
    of what it left out. Zero where a caller had no row to read one off,
    which the identity column never produces.
    """

    id: int = 0
    heard: str | None = None
    reply: str | None = None
    tools: tuple[str, ...] = ()


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


class Acknowledgement:
    """Whether one turn's durable transaction committed, waitable.

    A handle rather than a callback: the writer settles it on the thread
    that did the work, and whoever wants the answer waits for it with a
    bound of its own. Nothing on the audio path ever waits, which is why
    the pipeline creates one and drops it; the consumers are the paths
    that must not read past their own writes.

    It speaks for its own turn and for nothing else. A later turn
    landing says nothing about an earlier one, which is why a resume
    reads the thread's `incomplete` flag as well: a gap in the middle of
    a thread is exactly the state a per-turn answer cannot describe.

    `wait` answers false three ways, and deliberately does not tell them
    apart: the turn was dropped, the writer is gone, or the bound
    expired before an answer arrived. All three mean the same thing to a
    caller, which is that it may not assume the write landed.
    """

    __slots__ = ("_done", "_landed")

    def __init__(self) -> None:
        self._done = threading.Event()
        self._landed = False

    def wait(self, timeout: float | None = None) -> bool:
        """Whether the turn's durable transaction committed, waiting up
        to `timeout` seconds for an answer. False on a timeout, which is
        not an answer and is treated as one."""
        if not self._done.wait(timeout):
            return False
        return self._landed

    def settle(self, landed: bool) -> None:
        """The writer's half: say what became of the turn, once. A
        second call is ignored, so a batch settled by a tombstone and
        then met again by a drain does not change its own answer."""
        if self._done.is_set():
            return
        self._landed = landed
        self._done.set()


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
    prevent.

    The acknowledgement is optional in the answer rather than promised,
    which is what keeps that contract from spreading: a store hands one
    back, a consumer that is not a store hands back nothing, and the
    runtime keeps whichever it was given without ever waiting on it on
    the audio path. What waits is the resume, which must not read a
    thread past its own writes."""

    def record_turn(self, record: TurnRecord) -> "Acknowledgement | None": ...


class TurnStore(Protocol):
    """The same channel from the composition root's side, where one
    object serves every session and the records are keyed by one.

    The handle is required here and optional at `TurnRecorder` above,
    which is the difference between the two sides: a store always has
    one to give, and a consumer standing in for one does not have to
    invent it."""

    def record_turn(self, session_id: str, record: TurnRecord) -> Acknowledgement: ...


@dataclass(frozen=True)
class SessionTurns:
    """A `TurnStore` bound to one session, which is what a runtime holds.

    The binding happens where the session id is first known, so nothing
    downstream has to carry an identity it has no other use for."""

    store: TurnStore
    session_id: str

    def record_turn(self, record: TurnRecord) -> Acknowledgement:
        return self.store.record_turn(self.session_id, record)


__all__ = [
    "Acknowledgement",
    "SessionTurns",
    "StoredTurn",
    "ToolInvocation",
    "TurnLeg",
    "TurnRecord",
    "TurnRecorder",
    "TurnStore",
]
