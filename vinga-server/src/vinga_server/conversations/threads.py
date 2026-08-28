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
- **What a thread is called.** The first utterance, bounded. Content,
  and therefore under the text switch like the utterance it came from.
- **The retention ruleset**, whose unit is the thread rather than the
  session, and whose ordering between the three rules is the whole of
  why a live thread never loses its turns to an old session.

Nothing here opens a transaction or an engine. Every function takes the
caller's connection and runs inside the caller's transaction, which is
what lets one marker commit a turn, its calls and its thread's row
together or not at all.

Nothing here decides the storage switches either. The writer applies
them and hands over what survived, so a `heard` that arrived as None
under text-off produces a null title by the ordinary path rather than
by a second rule written here.
"""

from dataclasses import dataclass
from typing import Any

from sqlalchemy import ColumnElement, delete, select, update

from vinga_server.conversations.schema import (
    conversation_milestones,
    conversations,
    events,
    sessions,
    tool_invocations,
    turns,
)

# How much of the first utterance becomes the thread's title.
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


def title_of(heard: str | None) -> str | None:
    """What a thread whose first utterance was this is called.

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
        select(conversations.c.agent).where(
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
                incomplete=False,
                created_at=landing.at,
                last_active_at=landing.at,
            )
        )
        return
    if found.agent != landing.agent:
        raise MisattributedTurn(ANOTHER_AGENT)
    connection.execute(
        update(conversations)
        .where(conversations.c.conversation == landing.conversation)
        .values(last_active_at=landing.at)
    )


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


__all__ = [
    "ANOTHER_AGENT",
    "NO_DEVICE",
    "TITLE_CHARACTERS",
    "Landing",
    "MisattributedTurn",
    "Pruned",
    "landed",
    "prune",
    "title_of",
]
