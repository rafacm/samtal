"""Stored dialogue, as the context a model is handed.

The one module that knows what a thread read back out of the database
looks like to an LLM. Its callers hand it rows and a budget and get a
list of turns; what they stop knowing is how an utterance, a reply, the
tools that ran and the holes left by a stricter storage setting become
messages, and which of them a budget leaves room for.

Near-pure on purpose. Nothing here opens a connection or names a table:
the thread store hands it `records.StoredTurn` values, so the whole of
this module is exercised by writing turns down and reading messages
back, and a suite about what a resumed conversation reads like needs no
database at all. The input type is declared in `records.py` rather than
here, which is what keeps the store's read path off the provider
vocabulary a rendered turn is written in: reading a thread must not
load the model adapters, and the API that reads one is rendered without
them.

Three rules carry everything below.

**The unit is a whole stored turn.** A turn's user half, its assistant
half and the note about the tools it ran are budgeted and truncated
together, never separately, so a reply can never be rebuilt without the
utterance it answered.

**What comes out alternates, and it opens with the user.** That is a
property of the output rather than of the input, because the input has
two shapes that do not carry it. A turn that was heard and never
answered (a reply provider that failed after the utterance was
recorded) has a user half and no assistant half, and rendering it would
put two user messages in a row; a turn seeded by a move onto this
thread has an answer and nothing heard, because what the user said was
said on the thread they were moved off. The first is a hole, on the
rule below. The second is joined onto the answer before it, which is
what it was: two things the assistant said with nothing from the user
in between. A history that would still open on an answer opens after
it instead, since the first message a provider is handed is the user's.

**The budget is an estimate and says so.** `ESTIMATED_CHARS_PER_TOKEN`
is the whole of the arithmetic. A tokenizer per provider would be exact
for one of them and wrong for the rest, and what the number is for is
deciding how far back to read rather than what a request will cost.

**Content is what was said and that tools ran.** Arguments and results
stay in the store. They are the largest thing a thread holds and the
least useful to a model resuming it, and a name is already under the
same text switch as the dialogue beside it.

**A checkpoint is a pinned head, never a unit.** Where a thread has a
recap milestone, its text goes in front of everything as one assistant
message and the budget trims only the tail behind it. It is the one
thing here that truncation may not reach: it stands for turns that are
not in this list at all, and dropping it would silently delete the
oldest part of the conversation while the newest survived.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from vinga_server.conversations.records import StoredTurn
from vinga_server.providers import Turn

# How many characters of stored text are counted as one token.
#
# Four, the ratio the vendors' own rules of thumb agree on for prose in
# a Latin script, and deliberately a constant rather than a per-provider
# tokenizer: an exact count for one model would be a wrong count for the
# next, and what this number decides is how far back into a thread to
# read, which a fifth of a token either way does not change. Stated in
# the reference documentation as the approximation it is.
ESTIMATED_CHARS_PER_TOKEN = 4

# How a turn says which tools it ran, appended to the assistant half.
# One line whatever ran, and names only.
TOOL_NOTE = "(tools used: {names})"

# How a recap milestone is put in front of the turns after it.
#
# The assistant's own voice, because it is: the recap was spoken by this
# agent to this user and stored byte for byte as it was heard, so a
# message attributed to anybody else would be the one line of this
# history that never happened. The frame says what it is standing in
# for, so the model does not read a summary as the last thing it said.
MILESTONE_NOTE = "(recap of the earlier part of this conversation: {text})"


@dataclass(frozen=True)
class Hydrated:
    """What a thread became, and what had to be left out of it.

    `rendered` and `skipped` count stored turns rather than messages:
    one turn is one unit here, and a caller reporting "I could not read
    three of these" means three turns. `over_budget` is the fact the
    recap offer turns on and the reason a resume can say it started from
    recent turns: the untruncated thread did not fit.

    `from_turn` and `after_turn` are the ids of the oldest and the
    newest stored turn this actually rendered, and null where it
    rendered none. They exist for the recap: a checkpoint records the
    range it really read, so a summary bounded by its own budget cannot
    claim coverage of the turns it dropped.
    """

    turns: tuple[Turn, ...] = ()
    rendered: int = 0
    skipped: int = 0
    over_budget: bool = False
    from_turn: int | None = None
    after_turn: int | None = None


def hydrated(
    stored: Sequence[StoredTurn], budget_tokens: int, milestone: str | None = None
) -> Hydrated:
    """The newest of these turns that fit in the budget, oldest first,
    behind whatever checkpoint stands for the rest.

    Walked from the newest backwards, because what a resumed
    conversation needs most is what was said last, and stopped at the
    first whole unit that does not fit. Everything older than that is
    gone; nothing inside a unit is ever cut, so the result never holds a
    reply whose question was truncated away.

    A turn with no answer on it is a hole rather than a unit, whether it
    stored nothing at all (text-off) or stored the utterance and never
    the reply (a provider that failed after the `heard` was recorded).
    A hole costs nothing and stops nothing, and rendering half of one
    would put two user messages in a row.

    Holes are counted over the whole thread, not over the window the
    budget kept. What the count answers is whether the record has gaps
    in it, and a thread whose losses are all older than the cutoff has
    them just the same; counting only what the walk reached would report
    fewer the longer the conversation got.

    A turn with an answer and nothing heard is the first turn of a
    thread this session moved onto, and it is joined onto the answer
    before it, in that order: the two were said one after the other with
    nothing from the user in between. One with nothing before it is
    dropped rather than led with, because the first message a provider
    is handed is the user's. Neither is a hole: nothing about that turn
    was lost.

    `milestone` is the text of the thread's latest recap checkpoint, and
    its caller has already left out the turns that checkpoint covers. It
    is pinned as the head and charged to the budget before any turn is,
    so a long tail trims against it rather than around it; it is never
    itself dropped, because what it stands for is not in this list and
    dropping it would delete the oldest part of the conversation while
    keeping the newest.

    The single newest unit is included even when it alone exceeds the
    budget, which is the answer for a thread with no checkpoint: an
    empty resume would be a worse answer than an over-budget one, and
    the budget is an estimate to begin with. With a checkpoint there is
    already something to say, so the head wins and `over_budget` says
    the tail did not fit.

    `over_budget` means a turn was left out, never that the head alone
    was large: what the flag decides is whether to offer a recap, and a
    recap of nothing new is not worth asking anybody about.
    """
    head = [] if milestone is None else [Turn("assistant", MILESTONE_NOTE.format(text=milestone))]
    kept: list[Turn] = []
    rendered = 0
    spent = _tokens(head)
    over_budget = False
    # Answers with no utterance of their own, oldest first, waiting for
    # the turn they follow. Cleared onto it, and dropped where the walk
    # ends before one arrives. The id kept beside them is the newest of
    # the group, because a coverage boundary must name the newest turn
    # actually represented, and a joined answer is represented.
    trailing: list[str] = []
    newest_trailing: int | None = None
    first: int | None = None
    last: int | None = None
    for turn in reversed(stored):
        said = _assistant(turn)
        if not said:
            continue
        if not turn.heard:
            trailing.insert(0, said)
            if newest_trailing is None:
                newest_trailing = turn.id
            continue
        messages = [
            Turn("user", turn.heard),
            Turn("assistant", "\n".join([said, *trailing])),
        ]
        cost = _tokens(messages)
        if (kept or head) and spent + cost > budget_tokens:
            over_budget = True
            break
        if not kept and not head and cost > budget_tokens:
            # The newest unit, over the budget on its own, taken anyway.
            over_budget = True
        kept = [*messages, *kept]
        spent += cost
        rendered += 1 + len(trailing)
        first = turn.id
        if last is None:
            last = turn.id if newest_trailing is None else newest_trailing
        trailing = []
        newest_trailing = None
    return Hydrated(
        turns=(*head, *kept),
        rendered=rendered,
        skipped=sum(1 for turn in stored if not _assistant(turn)),
        over_budget=over_budget,
        from_turn=first,
        after_turn=last,
    )


def _assistant(turn: StoredTurn) -> str:
    """What the assistant half of this turn says: the reply, and the
    note about the tools it ran, in that order and one line apart."""
    parts = [part for part in (turn.reply,) if part]
    if turn.tools:
        parts.append(TOOL_NOTE.format(names=", ".join(turn.tools)))
    return "\n".join(parts)


def _tokens(messages: Sequence[Turn]) -> int:
    """What this unit is estimated to cost, rounded up so that a unit
    with anything in it costs at least one token."""
    characters = sum(len(message.content) for message in messages)
    return -(-characters // ESTIMATED_CHARS_PER_TOKEN)


__all__ = [
    "ESTIMATED_CHARS_PER_TOKEN",
    "MILESTONE_NOTE",
    "TOOL_NOTE",
    "Hydrated",
    "hydrated",
]
