"""Stored dialogue, as the context a model is handed.

The one module that knows what a thread read back out of the database
looks like to an LLM. Its callers hand it rows and a budget and get a
list of turns; what they stop knowing is how an utterance, a reply, the
tools that ran and the holes left by a stricter storage setting become
messages, and which of them a budget leaves room for.

Near-pure on purpose. Nothing here opens a connection or names a table:
the thread store hands it `StoredTurn` values, so the whole of this
module is exercised by writing turns down and reading messages back,
and a suite about what a resumed conversation reads like needs no
database at all.

Three rules carry everything below.

**The unit is a whole stored turn.** A turn's user half, its assistant
half and the note about the tools it ran are budgeted and truncated
together, never separately, so a reply can never be rebuilt without the
utterance it answered and what comes out alternates roles by
construction.

**The budget is an estimate and says so.** `ESTIMATED_CHARS_PER_TOKEN`
is the whole of the arithmetic. A tokenizer per provider would be exact
for one of them and wrong for the rest, and what the number is for is
deciding how far back to read rather than what a request will cost.

**Content is what was said and that tools ran.** Arguments and results
stay in the store. They are the largest thing a thread holds and the
least useful to a model resuming it, and a name is already under the
same text switch as the dialogue beside it.
"""

from collections.abc import Sequence
from dataclasses import dataclass

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


@dataclass(frozen=True)
class StoredTurn:
    """One turn as the store kept it, which is all this module is told.

    Both text halves are optional because both are under the text
    switch: a deployment that stores no text stores the turn and none of
    the words in it, and what that leaves here is a hole rather than a
    turn to render. `tools` carries the names of the calls that turn
    made, in the order the model issued them, with the unnamed ones
    (a malformed call, a name nobody publishes) already left out by
    whoever read the rows.
    """

    heard: str | None = None
    reply: str | None = None
    tools: tuple[str, ...] = ()


@dataclass(frozen=True)
class Hydrated:
    """What a thread became, and what had to be left out of it.

    `rendered` and `skipped` count stored turns rather than messages:
    one turn is one unit here, and a caller reporting "I could not read
    three of these" means three turns. `over_budget` is the fact the
    recap offer turns on (milestone 5) and the reason a resume can say
    it started from recent turns: the untruncated thread did not fit.
    """

    turns: tuple[Turn, ...] = ()
    rendered: int = 0
    skipped: int = 0
    over_budget: bool = False


def hydrated(stored: Sequence[StoredTurn], budget_tokens: int) -> Hydrated:
    """The newest of these turns that fit in the budget, oldest first.

    Walked from the newest backwards, because what a resumed
    conversation needs most is what was said last, and stopped at the
    first whole unit that does not fit. Everything older than that is
    gone; nothing inside a unit is ever cut, so the result never holds a
    reply whose question was truncated away.

    A turn that stored no text at all is a hole rather than a unit: it
    costs nothing, stops nothing, and is counted in `skipped` so the
    caller can say the record is partial. The walk does not stop at one,
    which is what lets a thread recorded under text-off report every one
    of its turns as unreadable rather than reporting the newest one and
    giving up.

    The single newest unit is included even when it alone exceeds the
    budget. An empty resume would be a worse answer than an over-budget
    one, and the budget is an estimate to begin with; `over_budget` says
    what happened, so the caller can too.
    """
    kept: list[Turn] = []
    rendered = 0
    skipped = 0
    spent = 0
    over_budget = False
    for turn in reversed(stored):
        messages = _messages(turn)
        if not messages:
            skipped += 1
            continue
        cost = _tokens(messages)
        if kept and spent + cost > budget_tokens:
            over_budget = True
            break
        if not kept and cost > budget_tokens:
            # The newest unit, over the budget on its own, taken anyway.
            over_budget = True
        kept = [*messages, *kept]
        spent += cost
        rendered += 1
    return Hydrated(
        turns=tuple(kept),
        rendered=rendered,
        skipped=skipped,
        over_budget=over_budget,
    )


def _messages(turn: StoredTurn) -> list[Turn]:
    """One stored turn as the messages it becomes, which is at most two
    and may be none.

    The assistant half exists when there is something to attribute to
    it, which is what was said or, for a turn that only ran tools, the
    note that they ran.
    """
    messages: list[Turn] = []
    if turn.heard:
        messages.append(Turn("user", turn.heard))
    said = _assistant(turn)
    if said:
        messages.append(Turn("assistant", said))
    return messages


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
    "TOOL_NOTE",
    "Hydrated",
    "StoredTurn",
    "hydrated",
]
