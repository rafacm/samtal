"""The thread store: a conversation's life, as rows.

A conversation is a durable thread between a user and exactly one
agent, and this module owns what that means to the database. Its
callers are the queue writer, which embeds these operations in its own
marker transactions, and (as later milestones land) the API handlers
and the selection tools. What all three stop knowing:

- **The join topology.** A thread is `conversations` plus the `turns`
  that name it, the `tool_invocations` under those turns and the
  `conversation_milestones` on the thread itself. Nothing outside this
  module writes that shape or takes it apart.
- **When a thread becomes a row.** Not at activation: a wake that
  produced no transcript produces no turn, and an empty thread would
  clutter a listing and a spoken discovery for the sake of a
  conversation that never happened. The row lands with the thread's
  first turn, in the transaction that stores it, which is also what
  makes referential integrity the writer's in the one place the schema
  says it lives. And when it may not become one at all: a landing this
  module cannot attribute is refused, which fails the caller's
  transaction, because a stored turn with no thread is a row retention
  can never reach.
- **What a thread is called.** The earliest utterance stored on it,
  bounded. Content, and therefore under the text switch like the
  utterance it came from.
- **What a checkpoint replaces.** A recap is recorded with the range of
  turns it really read, and a thread that has one is read back as that
  checkpoint plus what was said after it. Both halves of that rule live
  here, so no caller can record a coverage the reads do not honour or
  read a thread as though its recap covered more than it did.
- **The retention ruleset**, whose unit is the thread rather than the
  session, and whose ordering between the three rules is the whole of
  why a live thread never loses its turns to an old session.
- **What erasure reaches.** Deleting a session deletes its turns
  wherever their thread is, and in the same transaction everything
  those turns fed: the title derived from one, the recap checkpoints
  that summarized one and everything descended from those, the activity
  stamp one wrote, and the thread itself once it has nothing left. The
  automatic path and the on-demand one share these helpers rather than
  keeping two sets of bookkeeping, which is what the amendment to #190
  means by the purge being absorbed rather than rivaled.
- **That a lost write is a fact about a thread.** `flag_incomplete`
  writes the mark the writer latched, and answers which threads there
  was a row to mark, because a thread whose first turn was the lost
  batch has none yet.
- **That a database can refuse.** `Reads` is the door a reply in flight
  comes in through, and nothing raises out of it: a failed read is a
  fact the caller turns into a sentence, never a driver's own words on
  their way to a model.

No function here opens a transaction or an engine. Every one of them
takes the caller's connection and runs inside the caller's transaction,
which is what lets one marker commit a turn, its calls and its thread's
row together or not at all. `Reads` at the end is the deliberate
exception, and the exception proves the rule: its caller is a reply
being spoken, which holds no transaction and may not be given one.

Nothing here decides the storage switches either. The writer applies
them and hands over what survived, so a `heard` that arrived as None
under text-off produces a null title by the ordinary path rather than
by a second rule written here.
"""

from bisect import bisect_left
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import ColumnElement, delete, func, select, tuple_, update

from vinga_server.config.models import DatabaseConfig
from vinga_server.conversations.records import StoredTurn
from vinga_server.conversations.schema import (
    conversation_milestones,
    conversations,
    events,
    sessions,
    tool_invocations,
    turns,
)
from vinga_server.db import is_busy, read_engine
from vinga_server.events import logger

# How much of the utterance a thread is named from becomes its title.
#
# Wide enough that a sentence usually survives whole and narrow enough
# to be a title rather than a paragraph: it is read aloud among
# candidates and printed in a table cell, and both of those are lines.
# A bound rather than a sentence split, because a transcript carries no
# reliable punctuation and a title that ended at the first period would
# be empty for the transcripts that have none.
TITLE_CHARACTERS = 80

# What a refusal below says, as fixed sentences with no value in them.
# The class name is what reaches an operator (the writer reports a
# failed transaction by class and nothing else), so these are for
# whoever reads a traceback; a sentence that named the device or the
# agent would be one edit away from naming it on a retained surface.
NO_DEVICE = "the session this turn was spoken in understood no device"
ANOTHER_AGENT = "this thread belongs to a different agent"
NO_SUCH_THREAD = "there is no thread for this checkpoint to be recorded on"


class MisattributedTurn(Exception):
    """A turn no thread can honestly own.

    Raised out of the caller's transaction rather than handled here, so
    the marker rolls back and the batch is dropped and counted exactly
    as any other failed write is. Loud, because the quiet alternative
    was tried and is worse: storing the turn and leaving its thread
    rowless puts a row in the database that nothing will ever prune,
    since retention reaches turns through their thread's row and keeps
    every session a turn still names. One defective reply would
    outlive the retention window forever.
    """


@dataclass(frozen=True)
class Landing:
    """One turn arriving on its thread, as this module needs it.

    Everything the writer already holds, gathered so the two facts that
    decide a materialization travel together: the pair the turn was
    stamped with, and the session's device. `agent` is required, here
    as on the record the writer took it from: it is not null in the
    row, it is what a resume addresses, and a thread that named it
    falsely would put a lie in the column the whole entity is keyed on.
    `device` is what the session knew, which is nullable on the session
    row and not on the thread's, and `landed` below is where that
    difference is answered.
    """

    conversation: str
    agent: str
    device: str | None
    # The utterance the title is derived from, after the text switch, so
    # a deployment storing no text derives no title by the same rule
    # that stores no transcript.
    heard: str | None
    # The writer's own stamp for this marker, UTC ISO-8601, so a turn
    # and the activity it moves carry one instant.
    at: str
    # Whether a write this thread needed has already been lost, so a row
    # materializing now must say so from its first byte. Only the insert
    # reads it: a thread whose row already exists is flagged by
    # `flag_incomplete`, which the writer calls beside this one in the
    # same transaction, and a landing never clears a flag, because a
    # later turn arriving does not fill an earlier gap.
    incomplete: bool = False


@dataclass(frozen=True)
class Pruned:
    """What one retention pass took, per table.

    A count per table rather than a single number, because the three
    rules delete from different tables for different reasons and an
    operator reading the event wants to know which of them fired. Only
    two of these reach the event; the rest are what the suites assert
    the ruleset by.
    """

    conversations: int = 0
    milestones: int = 0
    tool_invocations: int = 0
    turns: int = 0
    events: int = 0
    sessions: int = 0

    def anything(self) -> bool:
        return bool(self.conversations or self.sessions)


@dataclass(frozen=True)
class Erased:
    """What one deletion took: five counts and one list of names.

    Deliberately a second type rather than `Pruned`: retention answers
    an event about a policy that ran, and this answers a caller that
    asked for something to be destroyed. They agree today and have no
    reason to move together.

    `threads` is the one field that is not a count, and that is what its
    caller needs rather than a nicety. A thread this deletion took is a
    thread a live runtime may still be speaking on, and the writer has
    to be told which ones so that a turn already on its way to one is
    discarded instead of raising the row from the dead. The count the
    API answers is its length, which is why there is no second field
    holding one: two structures that must agree are one structure with a
    bug pending.
    """

    threads: tuple[str, ...] = ()
    milestones: int = 0
    tool_invocations: int = 0
    turns: int = 0
    events: int = 0
    sessions: int = 0


def title_of(heard: str | None) -> str | None:
    """What a thread named from this utterance is called.

    Null for an utterance that was never stored (text-off) and for one
    that is nothing but whitespace, which is the same answer for the
    same reason: there is nothing to call the thread. Whitespace is
    collapsed, because a transcript can carry line breaks and a title
    is a line.
    """
    if heard is None:
        return None
    words = " ".join(heard.split())
    return words[:TITLE_CHARACTERS] if words else None


def landed(connection: Any, landing: Landing) -> None:
    """One turn's effect on its thread, inside the caller's transaction.

    Two shapes, and the distinction is the whole of the lifecycle: a
    thread this database has never seen materializes here, with its
    title derived and its two timestamps equal, and a thread it has
    seen has its activity moved. Both are decided by asking, rather
    than by an upsert, because the two do different things and only the
    first one has a title to derive.

    A materializing landing carries the writer's pending mark into the
    insert, so a thread whose earlier turns were lost says so from its
    first byte. A landing on a thread that already has a row never
    touches the mark in either direction: a later turn arriving does not
    fill an earlier gap, and `flag_incomplete`, which the writer calls
    beside this one in the same transaction, is what sets it.

    The title is the thread's earliest stored utterance rather than its
    earliest turn, which are the same thing everywhere except at the
    start of a thread a session moved onto: that thread's first turn is
    the answer the move was greeted with, and nothing was heard on it.
    So a row still holding no title takes one from the first landing
    that has an utterance to give it, and a row that has one keeps it
    whatever lands later. A deployment storing no text derives no title
    by the same rule, since no landing it makes carries one.

    A landing this module cannot attribute is refused rather than
    stored, and the refusal takes the marker's whole batch with it. Two
    shapes reach it: a session that understood no device, which has no
    provenance to write into a not-null column, and a turn whose thread
    already belongs to a different agent. Both are defects rather than
    configurations, because a session is closed before it records
    anything unless its device identified itself, the runtime activates
    an agent before it can produce a turn, and a thread id is minted per
    agent and never shared. Refusing is what keeps "every stored turn is
    on a thread, and that thread is its agent's" true of the database
    rather than only of the code that usually writes it.
    """
    if landing.device is None:
        raise MisattributedTurn(NO_DEVICE)
    found = connection.execute(
        select(conversations.c.agent, conversations.c.title).where(
            conversations.c.conversation == landing.conversation
        )
    ).first()
    if found is None:
        connection.execute(
            conversations.insert().values(
                conversation=landing.conversation,
                agent=landing.agent,
                device=landing.device,
                title=title_of(landing.heard),
                incomplete=landing.incomplete,
                created_at=landing.at,
                last_active_at=landing.at,
            )
        )
        return
    if found.agent != landing.agent:
        raise MisattributedTurn(ANOTHER_AGENT)
    moved: dict[str, Any] = {"last_active_at": landing.at}
    title = title_of(landing.heard)
    if found.title is None and title is not None:
        moved["title"] = title
    connection.execute(
        update(conversations)
        .where(conversations.c.conversation == landing.conversation)
        .values(**moved)
    )


@dataclass(frozen=True)
class Checkpoint:
    """One consented recap on its way into the store.

    The range is `from_turn` through `after_turn` and both are required,
    because what they are for is bounding a claim: a checkpoint that did
    not say where its reading began would let hydration skip turns
    nothing ever summarized. `parent` is the checkpoint whose text was
    part of this recap's input, and null when none was.

    `text` is nullable for the uniform reason every content column is,
    and the flow that produces one cannot run with text off; the writer
    applies the switch before it gets here, exactly as it does for a
    turn's title.
    """

    conversation: str
    from_turn: int
    after_turn: int
    parent: int | None
    at: str
    text: str | None


def checkpointed(connection: Any, checkpoint: Checkpoint) -> None:
    """Record one recap checkpoint on its thread, inside the caller's
    transaction.

    Refused for a thread with no row, and refused the way a
    misattributed turn is: a checkpoint outside `conversations` is a row
    retention can never reach, since every rule that deletes one reaches
    it through the thread. The flow that calls this has just read the
    thread out of this same store, so the refusal is a defect report
    rather than a condition an operator configures around.

    Nothing here moves `last_active_at`. A recap is not something the
    user said, and the consent turn that produced it lands as a turn of
    its own in this same transaction, which is what moves the stamp.
    """
    found = connection.execute(
        select(conversations.c.id).where(
            conversations.c.conversation == checkpoint.conversation
        )
    ).first()
    if found is None:
        raise MisattributedTurn(NO_SUCH_THREAD)
    connection.execute(
        conversation_milestones.insert().values(
            conversation=checkpoint.conversation,
            from_turn=checkpoint.from_turn,
            after_turn=checkpoint.after_turn,
            parent=checkpoint.parent,
            created_at=checkpoint.at,
            text=checkpoint.text,
        )
    )


def flag_incomplete(connection: Any, threads: Iterable[str]) -> set[str]:
    """Say of every one of these threads that a write it needed was
    lost, and answer which of them there was a row to say it of.

    The answer is the whole point: a thread whose first turn was the
    dropped batch has no row yet, and this makes no empty one for the
    flag to sit on. Its caller keeps the id and offers it again at the
    next marker, where either a later turn has materialized the row (with
    the flag already true) or the id is still waiting.

    Idempotent by construction: the flag only ever goes true, and a
    thread already flagged is updated to what it already says.
    """
    named = sorted(set(threads))
    if not named:
        return set()
    found = connection.execute(
        select(conversations.c.conversation).where(
            conversations.c.conversation.in_(named)
        )
    ).scalars()
    flagged = set(found)
    if flagged:
        connection.execute(
            update(conversations)
            .where(conversations.c.conversation.in_(sorted(flagged)))
            .values(incomplete=True)
        )
    return flagged


# --- the reads --------------------------------------------------------
#
# What a thread looks like from outside it, which is three shapes, a
# search and the backlog a resume rebuilds a conversation out of. They
# live here rather than beside the routes for the reason
# the deletions do: the join topology is this module's, and a caller
# that assembled a thread out of `conversations`, `turns` and
# `conversation_milestones` itself would be a second place for it to be
# right or wrong. What the routes keep is the transport: which page,
# which cursor, and how a row becomes a body.


# What a listing answers per thread. Everything short about it, which is
# everything on the row: a thread carries no nested structure, so unlike
# a session there is nothing here a listing would be paying for.
SUMMARY_COLUMNS: tuple[Any, ...] = (
    conversations.c.id,
    conversations.c.conversation,
    conversations.c.agent,
    conversations.c.title,
    conversations.c.device,
    conversations.c.incomplete,
    conversations.c.created_at,
    conversations.c.last_active_at,
)

# What a thread's detail answers per checkpoint. Everything on the row
# but the thread's own id, which is what was addressed to ask.
CHECKPOINT_COLUMNS: tuple[Any, ...] = (
    conversation_milestones.c.id,
    conversation_milestones.c.from_turn,
    conversation_milestones.c.after_turn,
    conversation_milestones.c.parent,
    conversation_milestones.c.created_at,
    conversation_milestones.c.text,
)

# How many threads a discovery answer may hold, and how much of a
# thread's opening utterance is matched against and offered with it.
#
# Five, because the list is read aloud: a spoken answer that ran past
# that is one nobody holds in their head to the end of. The excerpt is
# wider than a title (which is the first utterance bounded at
# `TITLE_CHARACTERS`) so that a thread whose first sentence ran long is
# still matchable past the point its name stops.
RESUME_CANDIDATES = 5

EXCERPT_CHARACTERS = 200


@dataclass(frozen=True)
class Candidate:
    """One thread a spoken description might have meant.

    Everything the model needs to read it out and nothing else: which
    thread to ask for, what it is called, when it was last spoken to,
    and how it opened. `score` is carried because the caller decides
    what to say about a list nothing matched, and cannot see that from
    the order alone.
    """

    conversation: str
    title: str | None
    last_active_at: str
    excerpt: str | None
    score: int


@dataclass(frozen=True)
class Candidates:
    """A discovery answer: the threads to offer, and whether any of them
    was found by matching rather than by being recent.

    Two fields rather than an empty list for "nothing matched", because
    a dead end is a worse answer than a list somebody can still pick
    from: the caller offers the newest threads and says that nothing
    matched, which is a sentence it can only build if it is told.
    """

    matched: bool
    found: tuple[Candidate, ...] = ()


def listed(
    connection: Any,
    agent: str | None = None,
    limit: int = RESUME_CANDIDATES,
    cursor: tuple[str, int] | None = None,
) -> list[dict[str, Any]]:
    """One page of threads, most recently active first.

    Ordered by `last_active_at` descending with `id` descending as the
    tie-break, and paged by that pair rather than by the row id alone:
    activity moves, so an immutable row-id cursor would page an order
    the rows do not sit in. UTC ISO-8601 text compares
    lexicographically, which is what makes the pair total.

    `cursor` is the previous page's last row's pair, and the page holds
    the rows strictly below it. Under concurrent activity that is stated
    rather than implied: a thread whose activity moves it ahead of the
    cursor between two pages is missed by that pass and appears at the
    head of a fresh one, and a boundary never duplicates or skips a row
    whose pair did not move.
    """
    criteria: list[ColumnElement[bool]] = []
    if agent is not None:
        criteria.append(conversations.c.agent == agent)
    if cursor is not None:
        criteria.append(
            tuple_(conversations.c.last_active_at, conversations.c.id)
            < tuple_(*cursor)
        )
    return [
        dict(row)
        for row in connection.execute(
            select(*SUMMARY_COLUMNS, _turn_count().label("turns"))
            .where(*criteria)
            .order_by(conversations.c.last_active_at.desc(), conversations.c.id.desc())
            .limit(limit)
        ).mappings()
    ]


def detail(connection: Any, conversation: str) -> dict[str, Any] | None:
    """One thread, whole, or None when no row of that id is here.

    The turn count and the checkpoints are read in the caller's
    transaction with the row, so they describe one snapshot rather than
    three moments of a thread that may still be being spoken to.

    The checkpoints come back as rows rather than as a count, and the
    caller derives the count from them: a thread accrues one per recap
    somebody consented to, so the list is short by construction, and a
    count read separately would be a second structure that has to agree
    with the first.
    """
    found = connection.execute(
        select(*SUMMARY_COLUMNS, _turn_count().label("turns")).where(
            conversations.c.conversation == conversation
        )
    ).mappings().first()
    if found is None:
        return None
    return {
        **dict(found),
        "checkpoints": [
            dict(row)
            for row in connection.execute(
                select(*CHECKPOINT_COLUMNS)
                .where(conversation_milestones.c.conversation == conversation)
                .order_by(conversation_milestones.c.id)
            ).mappings()
        ],
    }


def dialogue(
    connection: Any, conversation: str, after: int | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    """One page of a thread's turns, oldest first.

    Forward from `after` rather than backwards from a cursor, which is
    the direction dialogue is read in and the direction a client
    reconciling what it has already seen asks in: the ids are monotonic
    and never reused, so what came after one is a stable question. A
    thread's turns can come from several sessions and each row says
    which, which is the half of the two projections a session timeline
    answers the other way round.
    """
    criteria: list[ColumnElement[bool]] = [turns.c.conversation == conversation]
    if after is not None:
        criteria.append(turns.c.id > after)
    return [
        dict(row)
        for row in connection.execute(
            select(turns).where(*criteria).order_by(turns.c.id).limit(limit)
        ).mappings()
    ]


def candidates(connection: Any, agent: str, description: str) -> Candidates:
    """The threads of one agent a spoken description might have meant.

    Matching, not merely listing, and deliberately not full-text search
    over the dialogue, which #190 defers: what is matched is what a
    listing already carries, which is the thread's title and the opening
    excerpt beside it.

    The description and each candidate's text are normalized the same
    way (casefolded, punctuation replaced by a break, split on
    whitespace), a candidate's score is how many of the description's
    distinct tokens appear among its own, and the answer orders by score
    descending, then `last_active_at` descending, then id descending. So
    a relevant older thread outranks a newer unrelated one, and the same
    question asked twice of the same rows answers the same way.

    Nothing scoring is not a dead end: the newest `RESUME_CANDIDATES`
    come back with `matched` false, so the caller can offer them and say
    that nothing matched rather than answering with a shrug.

    Every token counts the same, an ordinary word included, so a
    description carrying "the" scores one against every thread that also
    says it. That is deliberate rather than overlooked: a stop list is a
    vocabulary per language, this deployment's transcripts are in
    whatever the room speaks, and the ordering already puts the thread
    that matched three words ahead of the one that matched a common one.

    The scan is one agent's threads, which is the set the entity is
    scoped to and the set `ix_conversations_agent_activity` is for. It
    is not bounded further on purpose: a bound is what would make the
    reviewer's case (a relevant thread outside the newest few) unfindable,
    which is the whole property this function exists to have.
    """
    wanted = _tokens(description)
    scored: list[tuple[int, str, int, Candidate]] = []
    for row in connection.execute(
        select(
            conversations.c.id,
            conversations.c.conversation,
            conversations.c.title,
            conversations.c.last_active_at,
            select(turns.c.heard)
            .where(turns.c.conversation == conversations.c.conversation)
            .order_by(turns.c.id)
            .limit(1)
            .scalar_subquery()
            .label("opening"),
        ).where(conversations.c.agent == agent)
    ):
        score = len(wanted & (_tokens(row.title) | _tokens(row.opening)))
        scored.append(
            (
                score,
                row.last_active_at,
                row.id,
                Candidate(
                    conversation=row.conversation,
                    title=row.title,
                    last_active_at=row.last_active_at,
                    excerpt=_excerpt(row.opening),
                    score=score,
                ),
            )
        )
    # One sort with one key, so the fallback below is the same list read
    # shorter rather than a second ordering rule. The row id is the last
    # tie-break and stays out of the answer: it orders threads that agree
    # on everything else, and it is not something to read aloud.
    ranked = [one[3] for one in sorted(scored, key=lambda one: one[:3], reverse=True)]
    matching = tuple(one for one in ranked if one.score)[:RESUME_CANDIDATES]
    if matching:
        return Candidates(matched=True, found=matching)
    return Candidates(matched=False, found=tuple(ranked[:RESUME_CANDIDATES]))


@dataclass(frozen=True)
class Milestone:
    """One recap checkpoint, as a row.

    Everything a reader of one needs: what it says, the range of turns
    it may claim to have summarized, and the checkpoint whose text was
    folded into it. The lineage is not navigation, it is provenance:
    content that reached this row only through an earlier recap is still
    this row's content, and erasure walks it for exactly that reason.
    """

    id: int
    conversation: str
    from_turn: int
    after_turn: int
    parent: int | None
    created_at: str
    text: str | None


@dataclass(frozen=True)
class Backlog:
    """One thread as the resume path needs it: who it belongs to,
    whether its record is whole, the checkpoint standing in for its
    older half, and everything said since, oldest first.

    The turns are already the hydrator's own type, because turning rows
    into them is reading the store and reading the store is this
    module's. What the resume path is left with is a budget and a
    decision.

    `agent` is answered rather than filtered on, so a caller that asked
    for a thread belonging to somebody else is refused in its own words
    rather than told the thread does not exist. `incomplete` is the mark
    a lost write left, which a resume conveys as a caveat: an
    acknowledgement speaks for one turn, and a hole in the middle of a
    thread is exactly what no per-turn answer can describe.

    `milestone` and `turns` are one answer rather than two: where a
    checkpoint stands, the turns are the ones after its coverage, and
    the reader never has to know that the pair has to agree.
    """

    conversation: str
    agent: str
    incomplete: bool
    milestone: Milestone | None = None
    turns: tuple[StoredTurn, ...] = ()


def latest_milestone(connection: Any, conversation: str) -> Milestone | None:
    """The newest recap checkpoint on this thread, or None.

    Newest by row id, which is the order they were consented to in and
    the order `ix_conversation_milestones_conversation` answers. Only
    one is ever read: each recap folds the one before it into itself, so
    the newest is the whole lineage said once.
    """
    found = connection.execute(
        select(conversation_milestones)
        .where(conversation_milestones.c.conversation == conversation)
        .order_by(conversation_milestones.c.id.desc())
        .limit(1)
    ).mappings().first()
    return None if found is None else Milestone(**found)


def backlog(connection: Any, conversation: str) -> Backlog | None:
    """Everything one thread holds that a resume can use, or None when
    no row of that id is here.

    None is the whole of what a deleted thread looks like from here, and
    it is also what an id nobody ever wrote looks like. The two are one
    answer on purpose: there is nothing to resume either way, and the
    caller says so in one sentence rather than guessing which happened.

    Where the thread has a checkpoint, the turns read are the ones after
    its `after_turn` and the checkpoint stands for the rest. Turns at or
    before its `from_turn` are outside its recorded coverage, and they
    are gone from here exactly as oldest-first truncation would have
    dropped them: the boundary is stated in the schema reference rather
    than hidden.

    A checkpoint whose text was never stored replaces nothing, so it is
    not read as one and the whole thread comes back instead. That is the
    uniform text-switch rule reaching the one row it can reach: the flow
    that writes a checkpoint cannot run with text off, and a row written
    before the switch moved would otherwise silently delete the turns it
    can no longer describe.

    Four statements rather than a join. A thread's turns are read in one
    order and its calls in another, and a join would answer one row per
    call with the turn's text repeated on each of them, which for a long
    thread is the dialogue several times over on the wire for a handful
    of names.
    """
    found = connection.execute(
        select(conversations.c.agent, conversations.c.incomplete).where(
            conversations.c.conversation == conversation
        )
    ).first()
    if found is None:
        return None
    milestone = latest_milestone(connection, conversation)
    if milestone is not None and milestone.text is None:
        milestone = None
    criteria: list[ColumnElement[bool]] = [turns.c.conversation == conversation]
    if milestone is not None:
        criteria.append(turns.c.id > milestone.after_turn)
    spoken = connection.execute(
        select(turns.c.id, turns.c.heard, turns.c.reply)
        .where(*criteria)
        .order_by(turns.c.id)
    ).all()
    called: dict[int, list[str]] = {}
    for turn_id, name in connection.execute(
        select(tool_invocations.c.turn, tool_invocations.c.name)
        .where(
            tool_invocations.c.turn.in_(select(turns.c.id).where(*criteria)),
            # A call the store could not name is left out rather than
            # rendered as a blank: the name is null under text-off and
            # for a call whose own name never parsed, and neither is
            # something to tell a model ran.
            tool_invocations.c.name.is_not(None),
        )
        .order_by(tool_invocations.c.turn, tool_invocations.c.position)
    ):
        called.setdefault(turn_id, []).append(name)
    return Backlog(
        conversation=conversation,
        agent=found.agent,
        incomplete=bool(found.incomplete),
        milestone=milestone,
        turns=tuple(
            StoredTurn(
                id=row.id,
                heard=row.heard,
                reply=row.reply,
                tools=tuple(called.get(row.id, ())),
            )
            for row in spoken
        ),
    )


@dataclass(frozen=True)
class Unreadable:
    """The store could not answer, said as a fact with no words in it.

    Two states rather than an exception, because the caller is a reply
    being spoken: what a failed read means there is a sentence the agent
    says, and an exception would mean either a reply that died or a
    driver's own message quoted into what the model reads. `busy` is the
    db classifier's closed question and the whole of what is
    distinguished, because it is the whole of what a caller could do
    differently: ask again in a moment, or not.
    """

    busy: bool = False


class Reads:
    """The door a live conversation reads a thread through.

    The one thing in this module that opens anything, and it says so:
    every function above runs inside a caller's transaction because its
    caller is a writer or a request, while this one's caller is a reply
    in flight that has no transaction and must not acquire one. A
    connection per call, on a read engine opened and disposed around it,
    which is the shape the API's own reads have and for the same reason:
    nothing is held between two conversations.

    It is a sanitized boundary, which is the reason it is a class rather
    than two more functions. A tool result is model-visible and stored,
    and a driver failure quotes the DSN it tried to connect with, so a
    read that raised through the tool loop would put a credential in
    front of a model and into the store. Nothing raises out of here.
    What comes back instead is `Unreadable`, and the sentence for it is
    the caller's to choose from its own closed set.

    The failure is logged by class name and by nothing else, which is
    the same rule the reply path applies to a provider that fails: a
    type name says what went wrong, a message says what a stranger
    wrote.

    The boundary is the whole call, opening and closing included.
    Building an engine resolves the URL the settings name, and disposing
    one closes the pool underneath it, so both of them are places a
    driver speaks its own words; either outside the catch would be a
    credential raised past a seam whose only reason to exist is that
    nothing gets past it. So the engine is made inside the try, and its
    disposal cannot replace an answer this class has already decided on.
    """

    def __init__(self, database: DatabaseConfig) -> None:
        self._database = database

    def candidates(self, agent: str, description: str) -> "Candidates | Unreadable":
        """The threads of one agent a description might have meant."""
        return self._read(lambda connection: candidates(connection, agent, description))

    def backlog(self, conversation: str) -> "Backlog | None | Unreadable":
        """One thread, whole, or None where there is no such thread."""
        return self._read(lambda connection: backlog(connection, conversation))

    def _read(self, ask: Callable[[Any], Any]) -> Any:
        # Null until there is one, so the disposal below knows whether
        # there is anything to dispose: the engine is built inside the
        # catch precisely because building one can fail, and a name the
        # `finally` reached before the assignment would be the failure
        # this arrangement exists to avoid.
        engine: Any = None
        try:
            engine = read_engine(self._database)
            with engine.connect() as connection:
                return ask(connection)
        except Exception as exc:  # noqa: BLE001 - the whole point of the seam
            logger.warning(
                "a conversation could not be read from the store: %s",
                type(exc).__name__,
            )
            return Unreadable(busy=is_busy(exc))
        finally:
            self._dispose(engine)

    def _dispose(self, engine: Any) -> None:
        """Close the pool, and let nothing about the closing be the
        answer.

        A `finally` that raises replaces the value the block was
        returning, so a driver failing on the way out would put its own
        words where the fixed sentence had already been decided, and
        would do it to a read that had otherwise succeeded. Swallowed by
        class name here for that reason alone: what a caller is owed is
        the answer to what it asked, and the pool it can no longer see
        is this module's own business.
        """
        if engine is None:
            return
        try:
            engine.dispose()
        except Exception as exc:  # noqa: BLE001 - a closing never becomes an answer
            logger.warning(
                "a conversation store connection could not be closed: %s",
                type(exc).__name__,
            )


def _turn_count() -> Any:
    """How many turns a thread holds, as a correlated count.

    A count rather than a join, so a thread is one row of the page
    whatever it holds and the page size counts threads.
    """
    return (
        select(func.count())
        .select_from(turns)
        .where(turns.c.conversation == conversations.c.conversation)
        .scalar_subquery()
    )


def _excerpt(heard: str | None) -> str | None:
    """How a thread opened, bounded, or None where nothing was stored.

    The same shape as a title and a wider bound, for the same reason a
    title has one: it is read aloud and printed on a line.
    """
    if heard is None:
        return None
    words = " ".join(heard.split())
    return words[:EXCERPT_CHARACTERS] if words else None


def _tokens(text: str | None) -> set[str]:
    """Text as the words a match is decided on.

    Casefolded, with everything that is not a letter or a digit becoming
    a break, and split on whitespace. A break rather than a deletion, so
    that a hyphenated word matches the two words somebody says instead
    of a third word neither of them is.
    """
    if not text:
        return set()
    broken = "".join(
        character if character.isalnum() else " " for character in text.casefold()
    )
    return set(broken.split())


def prune(connection: Any, cutoff: str) -> Pruned:
    """The whole retention ruleset, one pass, inside the caller's
    transaction.

    The unit is the thread, and that is the change the ruleset exists
    for: once a conversation spans sessions, deleting a session's turns
    because the session is old would take dialogue out of a thread
    somebody is still talking on. So turns die here with their thread
    and nowhere else, and what is left of an old session is the spine a
    surviving turn still points at.

    Three rules, applied in this order because each one's answer
    depends on the one before it:

    1. **A thread older than the cutoff goes whole**: its milestones,
       its turns' invocations, its turns, then its row. `last_active_at`
       is the age, so a thread that was spoken to yesterday survives
       whatever the age of the session that began it.
    2. **Events go by their session's age alone**, whether or not that
       session's row survives rule 3. They are session-scoped telemetry
       rather than part of the thread projection, so a live thread must
       not pin a year of decision track it has no reading of.
    3. **A session row older than the cutoff goes once nothing points
       at it.** Rule 1 has already run, so "nothing points at it" is
       decidable here: a session whose every turn died with its threads
       is free, and one still holding a live thread's turns keeps the
       minimal spine those turns cross-reference.

    Lexicographic comparison on UTC ISO-8601 text is chronological when
    both sides are written by `isoformat` at the same offset, which
    they are: the cutoff and every column here come from the writer's
    one clock.
    """
    doomed = select(conversations.c.conversation).where(
        conversations.c.last_active_at < cutoff
    )
    # Read once rather than as a correlated subquery per table: the last
    # statement of the rule deletes the rows the subquery reads, and a
    # list of ids is also what makes the invocation delete a single
    # statement instead of one per turn.
    dying_turns = select(turns.c.id).where(turns.c.conversation.in_(doomed))
    milestones = connection.execute(
        delete(conversation_milestones).where(
            conversation_milestones.c.conversation.in_(doomed)
        )
    ).rowcount
    invocations = connection.execute(
        delete(tool_invocations).where(tool_invocations.c.turn.in_(dying_turns))
    ).rowcount
    dead_turns = connection.execute(
        delete(turns).where(turns.c.conversation.in_(doomed))
    ).rowcount
    threads = connection.execute(
        delete(conversations).where(conversations.c.last_active_at < cutoff)
    ).rowcount

    old_sessions: list[ColumnElement[bool]] = [sessions.c.started_at < cutoff]
    aged = select(sessions.c.session).where(*old_sessions)
    telemetry = connection.execute(
        delete(events).where(events.c.session.in_(aged))
    ).rowcount
    # The spine, last: a session is free exactly when no turn names it,
    # which is a question rule 1 has already finished answering.
    referenced = select(turns.c.session).distinct()
    spines = connection.execute(
        delete(sessions).where(*old_sessions, sessions.c.session.not_in(referenced))
    ).rowcount

    return Pruned(
        conversations=threads,
        milestones=milestones,
        tool_invocations=invocations,
        turns=dead_turns,
        events=telemetry,
        sessions=spines,
    )


def selected(
    connection: Any,
    session: str | None = None,
    device: str | None = None,
    before: str | None = None,
) -> list[str]:
    """Which sessions three selectors name, combined with AND.

    The selector set is the retired purge command's, carried over
    unchanged because the amendment that turned that command into an
    endpoint settled its semantics rather than reopening them. `before`
    is a UTC day and the comparison is strict, so a session that began
    at any moment of that day survives it: what an operator means by
    "before the fifteenth" is not "up to some time on the fifteenth".

    A caller that names none of them gets every session, which is why no
    caller may: refusing an unselected purge is the handler's, said in a
    sentence rather than discovered here.
    """
    criteria: list[ColumnElement[bool]] = []
    if session is not None:
        criteria.append(sessions.c.session == session)
    if device is not None:
        criteria.append(sessions.c.device == device)
    if before is not None:
        criteria.append(sessions.c.started_at < before)
    return list(
        connection.execute(select(sessions.c.session).where(*criteria)).scalars()
    )


def erase_sessions(connection: Any, named: Sequence[str]) -> Erased:
    """Erase these sessions and everything they left behind, inside the
    caller's transaction.

    Erasure outranks every copy the store derived, not only the rows the
    selector named, and that is the whole of what this function knows
    that its callers do not. A session's turns go even where they belong
    to a thread somebody is still talking on (the thread honestly keeps
    a gap), and in the same transaction:

    - every milestone whose recorded coverage holds one of those turns
      goes, and so does every milestone descended from it along the
      `parent` lineage, because a summary of erased content is that
      content whether it arrived directly or through an earlier recap
      the summarizer consumed;
    - a thread whose title was derived from an erased turn is renamed
      from its earliest surviving turn, or loses its title altogether;
    - `last_active_at` moves back when the turn that wrote it is gone;
    - a thread that has lost every turn is deleted whole, because what
      would be left is a title and two timestamps, and neither is
      resumable.

    Session rows and their events go last, which is the same ordering
    retention uses and for the same reason: the questions the later
    statements ask are decidable only once the earlier ones have run.
    """
    if not named:
        return Erased()
    # Which turns are dying, and on which thread. Kept per thread rather
    # than as one set, because turn ids are unique across the store and
    # the threads interleave in them: a checkpoint's coverage is a range
    # of ids, and asking it about another thread's dead turn would erase
    # a summary of something that is still there.
    dying: dict[str, list[int]] = {}
    for turn_id, thread in connection.execute(
        select(turns.c.id, turns.c.conversation).where(turns.c.session.in_(named))
    ):
        dying.setdefault(thread, []).append(turn_id)
    for ids in dying.values():
        ids.sort()

    milestones = _erase_milestones(connection, dying)
    invocations = connection.execute(
        delete(tool_invocations).where(tool_invocations.c.session.in_(named))
    ).rowcount
    gone = connection.execute(delete(turns).where(turns.c.session.in_(named))).rowcount
    telemetry = connection.execute(
        delete(events).where(events.c.session.in_(named))
    ).rowcount
    spines = connection.execute(
        delete(sessions).where(sessions.c.session.in_(named))
    ).rowcount

    orphaned = _rederive(connection, dying)
    milestones += connection.execute(
        delete(conversation_milestones).where(
            conversation_milestones.c.conversation.in_(orphaned)
        )
    ).rowcount
    connection.execute(
        delete(conversations).where(conversations.c.conversation.in_(orphaned))
    )

    return Erased(
        threads=tuple(orphaned),
        milestones=milestones,
        tool_invocations=invocations,
        turns=gone,
        events=telemetry,
        sessions=spines,
    )


def erase_conversations(connection: Any, named: Sequence[str]) -> Erased:
    """Erase these threads whole, inside the caller's transaction.

    The other direction through the same door. Erasing a session takes
    its turns wherever their threads are; erasing a thread takes its
    turns out of whatever sessions they were spoken in, and the sessions
    themselves are not touched, nor is their telemetry: a session is a
    connection episode and it still happened, with a gap in it now where
    a thread used to be. That asymmetry is the two projections being
    honest about which one was asked to be forgotten.

    A thread's checkpoints go with it, and so does everything descended
    from them along the `parent` lineage. A recap consumes its own
    thread's latest checkpoint, so the lineage is inside the thread by
    construction; the closure is walked over the whole table anyway,
    because a rule that holds by construction somewhere else is not a
    rule this module is entitled to assume.

    No title recomputation and no activity stamp to move: what a thread
    that is gone was called and when it was last spoken to are gone with
    it.
    """
    if not named:
        return Erased()
    threads = list(
        connection.execute(
            select(conversations.c.conversation).where(
                conversations.c.conversation.in_(sorted(set(named)))
            )
        ).scalars()
    )
    if not threads:
        return Erased()
    doomed = set(
        connection.execute(
            select(conversation_milestones.c.id).where(
                conversation_milestones.c.conversation.in_(threads)
            )
        ).scalars()
    )
    milestones = _erase(connection, _descendants(connection, doomed))
    dying_turns = select(turns.c.id).where(turns.c.conversation.in_(threads))
    invocations = connection.execute(
        delete(tool_invocations).where(tool_invocations.c.turn.in_(dying_turns))
    ).rowcount
    gone = connection.execute(
        delete(turns).where(turns.c.conversation.in_(threads))
    ).rowcount
    connection.execute(
        delete(conversations).where(conversations.c.conversation.in_(threads))
    )
    return Erased(
        threads=tuple(threads),
        milestones=milestones,
        tool_invocations=invocations,
        turns=gone,
    )


def _erase_milestones(connection: Any, dying: Mapping[str, Sequence[int]]) -> int:
    """Every checkpoint that summarized an erased turn, and every
    checkpoint descended from one.

    Coverage is decided here in Python rather than in SQL because it is
    a question about a range against a set, and the sets are small: a
    checkpoint is a consented recap, so a thread accrues them at the
    pace somebody agrees to one.
    """
    if not dying:
        return 0
    rows = list(
        connection.execute(
            select(
                conversation_milestones.c.id,
                conversation_milestones.c.conversation,
                conversation_milestones.c.from_turn,
                conversation_milestones.c.after_turn,
            ).where(conversation_milestones.c.conversation.in_(sorted(dying)))
        )
    )
    doomed = {
        row[0] for row in rows if _covers(row[2], row[3], dying.get(row[1], ()))
    }
    return _erase(connection, _descendants(connection, doomed))


def _descendants(connection: Any, doomed: set[int]) -> set[int]:
    """Those checkpoints and everything descended from them along
    `parent`.

    A summary of erased content is that content whether it arrived
    directly or through an earlier recap the summarizer consumed, so a
    checkpoint dies with its ancestor. Walked as a closure rather than
    written as a recursive query, because a recursive query would be the
    one piece of SQL in this module a reader could not check by eye, and
    because a lineage is as deep as somebody has consented to recaps.

    One walk for both callers: the session erasure reaches it through
    coverage and the thread erasure through the thread, and a second
    copy of the closure would be a second answer to one question.
    """
    found = set(doomed)
    frontier = found
    while frontier:
        frontier = {
            child
            for child in connection.execute(
                select(conversation_milestones.c.id).where(
                    conversation_milestones.c.parent.in_(sorted(frontier))
                )
            ).scalars()
            if child not in found
        }
        found |= frontier
    return found


def _erase(connection: Any, doomed: set[int]) -> int:
    """Delete these checkpoints by id, and say how many went."""
    if not doomed:
        return 0
    return int(
        connection.execute(
            delete(conversation_milestones).where(
                conversation_milestones.c.id.in_(sorted(doomed))
            )
        ).rowcount
    )


def _covers(first: int, last: int, dead_turns: Sequence[int]) -> bool:
    """Whether a checkpoint's recorded coverage holds any erased turn.
    Bisected rather than scanned, because a purge can name a great many
    turns and a checkpoint asks the same question of all of them."""
    index = bisect_left(dead_turns, first)
    return index < len(dead_turns) and dead_turns[index] <= last


def _rederive(connection: Any, dying: Mapping[str, Sequence[int]]) -> list[str]:
    """Recompute what the erased turns fed, and answer the threads that
    have nothing left.

    The title is recomputed from the earliest surviving utterance
    whatever happened, which is exact rather than approximate: a title
    IS the earliest stored utterance bounded, the same rule a landing
    applies, so a thread whose earliest utterance survived is renamed to
    the name it already had. Turns with nothing heard on them are
    skipped rather than answering null, for the reason a landing skips
    them: the greeting a move was answered with is a turn of the thread
    and never a name for it.

    `last_active_at` cannot be exact, and this says so rather than
    pretending. A turn carries its offset from its session's open and no
    wall clock of its own, so the instant a surviving turn landed is not
    a stored fact. While the thread's newest turn survives, the stamp it
    wrote is still the truth and is left alone; once that turn is gone,
    the stamp falls back to the latest fact the store does hold about
    the survivors, which is when their sessions began, floored at the
    thread's own `created_at` so the pair stays ordered. The result is
    never later than the truth, so a thread trimmed this way is retired
    by retention no later than it would have been.
    """
    orphaned: list[str] = []
    for thread in sorted(dying):
        surviving = connection.execute(
            select(turns.c.id, turns.c.heard)
            .where(turns.c.conversation == thread)
            .order_by(turns.c.id)
        ).all()
        if not surviving:
            orphaned.append(thread)
            continue
        row = connection.execute(
            select(conversations.c.created_at, conversations.c.last_active_at).where(
                conversations.c.conversation == thread
            )
        ).first()
        if row is None:
            continue
        created_at, last_active_at = row
        if surviving[-1][0] < max(dying[thread]):
            last_active_at = max(created_at, _began(connection, thread))
        connection.execute(
            update(conversations)
            .where(conversations.c.conversation == thread)
            .values(title=_named(surviving), last_active_at=last_active_at)
        )
    return orphaned


def _named(surviving: Sequence[Any]) -> str | None:
    """What a thread with these turns left is called: the earliest one
    of them that was heard, bounded, or nothing where none was."""
    for _id, heard in surviving:
        title = title_of(heard)
        if title is not None:
            return title
    return None


def _began(connection: Any, thread: str) -> str:
    """The latest a session holding one of this thread's surviving turns
    began. Empty text when nothing answers, which loses to `created_at`
    in the comparison above rather than needing an arm of its own."""
    holding = select(turns.c.session).where(turns.c.conversation == thread).distinct()
    latest = connection.execute(
        select(func.max(sessions.c.started_at)).where(sessions.c.session.in_(holding))
    ).scalar()
    return latest or ""


__all__ = [
    "ANOTHER_AGENT",
    "CHECKPOINT_COLUMNS",
    "EXCERPT_CHARACTERS",
    "NO_DEVICE",
    "NO_SUCH_THREAD",
    "RESUME_CANDIDATES",
    "SUMMARY_COLUMNS",
    "TITLE_CHARACTERS",
    "Backlog",
    "Candidate",
    "Candidates",
    "Checkpoint",
    "Erased",
    "Landing",
    "Milestone",
    "MisattributedTurn",
    "Pruned",
    "Reads",
    "Unreadable",
    "backlog",
    "candidates",
    "checkpointed",
    "detail",
    "dialogue",
    "erase_conversations",
    "erase_sessions",
    "flag_incomplete",
    "landed",
    "latest_milestone",
    "listed",
    "prune",
    "selected",
    "title_of",
]
