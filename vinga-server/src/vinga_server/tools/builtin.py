"""The tools the server implements itself.

Ten of them, all bare-named because the namespace rules in `names`
reserve those names. Eight are executed here: the memory family
(`remember`, `update_memory`, `forget`, `restore_memory` and `recall`)
and the ledger's two (`set_state`, `clear_state`), against the memory
store, and the search half of `resume_conversation`, against whatever
the runtime injected as its way of reading stored threads. The other two
are defined here and executed by the session, because what they do is
end the tool loop rather than produce a result the model reads:
`switch_agent` hands the conversation to another agent, and
`new_conversation` and the selection half of `resume_conversation` move
it to another thread.

What the memory tools write is injected by `runtime.prompt`, which is
where the whole system prompt is assembled: this module defines and runs
the tools, and how their output reaches the model is the runtime's. The
injected blocks carry no numbers, which is why `recall` answers with
them: it is how the model reaches both what the prompt left out and the
number anything it wants to change is addressed by.

The tools that write memory need to know which memory they are writing,
which is what `MemoryContext` is: the session's memory address as a
value, asked for at the moment a call runs rather than kept from when
the source was built, because a reply can move a session to another
thread. The device is what a fact about the place is kept under and
which memories a number may reach; the thread is what a ledger entry
belongs to and which conversation an undo is bounded by. Neither is ever
read out of a call's arguments, because a model that could name one
would be writing into another household's notes or another
conversation's ledger.

The sentences a selection answers with also live here, all of them, and
that is deliberate rather than tidy. They are one closed vocabulary,
half of it chosen where a tool is dispatched and half of it where the
runtime intercepts one, and a vocabulary split across the two modules
that speak it would be two vocabularies inside a month. Every one of
them is a fixed sentence with nothing in it that a room said: what the
model is told is what happened and what to do about it.
"""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from vinga_server.memory.scopes import FACT_SCOPES, MemoryScope
from vinga_server.memory.store import MemoryStore
from vinga_server.providers import ToolDef
from vinga_server.tools import names

if TYPE_CHECKING:
    # Named for the annotation alone, so that saying what a candidate
    # looks like does not make the tool layer import the conversations
    # package at import time. The same trade `tools/source.py` makes for
    # the classification it routes by.
    from vinga_server.conversations import threads


@dataclass(frozen=True)
class MemoryContext:
    """Which memory a call belongs to: the device it is happening on and
    the thread it is happening in.

    A value rather than two arguments threaded through the tool
    interface, and read at the moment a call runs rather than kept: a
    reply can move a session to another conversation, and a note written
    after that move belongs to the thread the session is on now.

    Both are optional because both genuinely can be absent. A session
    whose device never identified itself has no device scope, and the
    thread is minted at the first activation, which happens before any
    tool can be called.
    """

    device: str | None
    conversation: str | None


def switch_agent_tool(agents: Sequence[str]) -> ToolDef:
    """Move the conversation to another assistant this device is bound
    to. Offered only when there is somewhere to go, so a device bound to
    one agent gets no dead tool.

    The enum carries the device's full bound list, which is also what
    lets the active agent answer "who can I talk to?" without any extra
    mechanism."""
    listed = ", ".join(agents)
    return ToolDef(
        name=names.SWITCH_AGENT,
        description=(
            "Switch this conversation to another assistant, who will then greet the "
            f"user and take over. Available assistants: {listed}. Use this when the "
            "user asks for one of them by name or asks for something another one "
            "handles, and to answer who they can talk to."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "enum": list(agents),
                    "description": "The assistant to hand the conversation to.",
                }
            },
            "required": ["agent"],
        },
    )


def remember_tool() -> ToolDef:
    """Keep one fact across conversations, about the user or about the
    place. Offered to every agent: remembered facts live in a schema this
    server migrates at every boot, so there is no deployment without a
    store (#314).

    The scope is steered rather than enforced, which is the decision this
    description carries out: what belongs to the device is the place and
    the household, and everything about the person belongs with the
    persona. A model that gets it wrong writes a true fact in the wrong
    place, which an operator can move; a server that guessed for it would
    be wrong silently.
    """
    return ToolDef(
        name=names.REMEMBER,
        description=(
            "Remember one short fact for future conversations, such as a preference, a "
            "name, or a routine. One fact per call, phrased so it still makes sense on "
            'its own weeks from now. Leave "scope" out for something about the person '
            'you are talking to, which is most things; set it to "device" for '
            "something about this place and everyone in it, such as the room, the "
            "household, or how the hardware here behaves, which every assistant on "
            "this device then knows. Do not use this for things that are only true "
            "right now."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The fact to remember, as one short sentence.",
                },
                "scope": {
                    "type": "string",
                    "enum": [scope.value for scope in FACT_SCOPES],
                    "description": (
                        'Whose fact this is: "agent" for the person you are talking '
                        'to, which is the default, or "device" for this place and its '
                        "household."
                    ),
                },
            },
            "required": ["text"],
        },
    )


def update_memory_tool() -> ToolDef:
    """Correct one fact already remembered, in place.

    Separate from remembering it again, because a memory that answered a
    correction with a second fact would hold both and read them out
    together: the number is the identity, and correcting it is what
    keeps one true thing where there was one thing.
    """
    return ToolDef(
        name=names.UPDATE_MEMORY,
        description=(
            "Correct one fact you have already remembered, by the number recall "
            "answers with. What you send replaces what it said. Use it when "
            "something you remembered has changed or turns out to have been wrong."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "id": {
                    "type": "integer",
                    "description": "The number of the fact to correct.",
                },
                "text": {
                    "type": "string",
                    "description": (
                        "What that fact should say instead, as one short sentence."
                    ),
                },
            },
            "required": ["id", "text"],
        },
    )


def forget_tool() -> ToolDef:
    """Remove one fact, softly by default.

    The description tells the model to say what was removed, which is
    what makes the removal reversible in practice: the undo exists in
    the store either way, but a user who never heard what went cannot
    ask for it back.
    """
    return ToolDef(
        name=names.FORGET,
        description=(
            "Remove one fact you remembered, by the number recall answers with. It "
            "answers with what it removed: say that out loud, so the user knows what "
            "went and can ask you to bring it back. Set permanently to true only when "
            "the user asks for something to be erased for good, which cannot be "
            "undone."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "id": {
                    "type": "integer",
                    "description": "The number of the fact to remove.",
                },
                "permanently": {
                    "type": "boolean",
                    "description": (
                        "True to erase it outright, with no way to bring it back. "
                        "Leave it out unless the user asked for exactly that."
                    ),
                },
            },
            "required": ["id"],
        },
    )


def restore_memory_tool() -> ToolDef:
    """Undo a removal, which is what makes the removal safe to make.

    The number is optional because the shape a person actually asks for
    is "no, put that back": what the tool does with no number is the last
    thing this conversation forgot.
    """
    return ToolDef(
        name=names.RESTORE_MEMORY,
        description=(
            "Bring back something you forgot in this conversation. Called with no "
            "number, it brings back the last thing you forgot; with the number of a "
            "fact you removed, that one. Only what was forgotten in this conversation "
            "can come back, and nothing erased permanently can."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "id": {
                    "type": "integer",
                    "description": (
                        "The number of the fact to bring back. Leave it out for the "
                        "last thing you forgot."
                    ),
                }
            },
        },
    )


def recall_tool() -> ToolDef:
    """Look through everything remembered, which is the other half of
    what the injected block leaves out.

    The prompt carries the newest facts and no numbers at all, so this is
    both how the model reaches what was not injected and how it learns
    the number of anything it wants to correct or remove. The description
    says so, because a model that does not know that has no way to use
    the two numbered tools beside it.
    """
    return ToolDef(
        name=names.RECALL,
        description=(
            "Look up what you remember about the user and about this place. It "
            "answers the facts whose words contain what you ask for, newest first, "
            "each with the number update_memory and forget need. Use it when the user "
            "asks what you know, and whenever you need the number of a fact."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "A word or two to look for, matched anywhere in a "
                        "remembered fact."
                    ),
                }
            },
            "required": ["query"],
        },
    )


def set_state_tool() -> ToolDef:
    """Write down what is currently true in this conversation, under a
    name the model chooses.

    The description carries the scope rule of thumb, because the
    steering is the enforcement: if losing the thread should lose it, it
    is state, and anything that should outlive the conversation has to be
    promoted with `remember`. A game agent saving the game is that
    promotion, and an agent that never makes it loses the campaign to
    retention.
    """
    return ToolDef(
        name=names.SET_STATE,
        description=(
            "Write down one thing that is true in this conversation right now, under "
            "a short name you choose. Writing the same name again replaces what it "
            "held, so this is how you keep track of something that changes: the scene "
            "and the hit points in a game, the position on a board, which step of "
            "something you are on. It is only about this conversation and is lost when "
            "the conversation ends, so anything the user should still have next time "
            "has to be kept with remember instead."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": (
                        "A short name for this note, which you use again to change it."
                    ),
                },
                "value": {
                    "type": "string",
                    "description": "What is true now, as one short sentence.",
                },
            },
            "required": ["key", "value"],
        },
    )


def clear_state_tool() -> ToolDef:
    """Forget one of those notes.

    Separate from writing an empty one, because a ledger of what is
    currently true has no entry for something that has stopped being
    true: what the model should read next round is nothing at all rather
    than a name holding a blank.
    """
    return ToolDef(
        name=names.CLEAR_STATE,
        description=(
            "Forget one thing you wrote down about this conversation, by the name you "
            "wrote it under. Use it when what it said has stopped being true and "
            "nothing takes its place; to change it, write it again with set_state "
            "instead."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "The name of the note to forget.",
                }
            },
            "required": ["key"],
        },
    )


def new_conversation_tool() -> ToolDef:
    """Leave this thread and start a fresh one with the same agent.

    Offered whether or not the server can resume anything, which is what
    makes the answer a sentence the agent says rather than a tool the
    model was told about and then denied. A refusal that can be spoken
    is worth more than a name that is not there: a model with no such
    tool invents one."""
    return ToolDef(
        name=names.NEW_CONVERSATION,
        description=(
            "Start a new conversation with this user and leave the current one "
            "behind. Use it when they say they want to talk about something else "
            "and start fresh, or ask to set the current conversation aside. What "
            "was said so far is kept and can be resumed later."
        ),
        input_schema={"type": "object", "properties": {}},
    )


# The two things a user may answer when a long conversation is offered:
# the two shapes it can be picked up in. A closed set, spelled once,
# because the schema's enum and the interception that honours it read
# the same two names, so a third could not be added in one place alone.
RECAP = "recap"
RECENT = "recent"


def resume_conversation_tool() -> ToolDef:
    """Find an earlier thread of this agent's and carry on with it.

    One tool for both beats of the flow, which is what makes the second
    beat converge: the first call describes, the answer is a list, and
    the follow-up names one of the conversations in that list. There is
    no second free-text search to disambiguate a first one, because
    picking from what was offered is not a search.

    `start_from` is the third beat, and it exists only where the tool
    has asked for it: a conversation too long to be given whole answers
    with the offer below, and the answer to that offer comes back here.
    A closed set of two, so the choice the user was actually given is
    the choice the tool can be told about."""
    return ToolDef(
        name=names.RESUME_CONVERSATION,
        description=(
            "Find one of your earlier conversations with this user and carry on "
            "with it. Call it with `description` set to what the user said about "
            "the conversation they mean; it answers with a short list, which you "
            "read out so they can choose. Then call it again with `conversation` "
            "set to the one they picked. Only a conversation this tool has listed "
            "can be resumed, so never invent one. If it answers that the "
            "conversation is too long to pick up whole, ask the user which they "
            "want and call it once more with the same `conversation` and "
            "`start_from`."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": (
                        "What the user said about the conversation they are "
                        "looking for, in their own words."
                    ),
                },
                "conversation": {
                    "type": "string",
                    "description": (
                        "The conversation the user picked, exactly as this tool "
                        "listed it."
                    ),
                },
                "start_from": {
                    "type": "string",
                    "enum": [RECAP, RECENT],
                    "description": (
                        "What the user answered when this tool offered a choice "
                        'about a long conversation: "recap" to hear a short '
                        'summary of the whole of it first, "recent" to carry on '
                        "from the most recent part. Only after this tool has "
                        "offered that choice for this conversation."
                    ),
                },
            },
        },
    )



# What a selection answers with, as fixed sentences. No value from a
# room reaches any of them, and each says what happened and what the
# agent should do next, because what the model does with a result is
# speak.

# Resumption is off, text is off, or nothing is recorded. One sentence
# for both tools: the reason is the same and so is the way out.
RESUMPTION_UNAVAILABLE = (
    "this server does not keep conversations that can be picked up again; tell the "
    "user that, and carry on with the conversation you are in"
)

# The tool was called with neither of its two arguments.
RESUME_NEEDS_AN_ARGUMENT = (
    'resume_conversation needs either a "description" of the conversation to look '
    'for or the "conversation" the user picked from a list you have already read '
    "out"
)

# An id this agent was not offered: invented, stale after a newer
# search, or offered to a different agent in this session.
NO_SUCH_CANDIDATE = (
    "that is not one of the conversations you offered; ask the user which of the "
    "ones you listed they meant, or search again with a description"
)

# The id was offered and the thread is not there any more.
CONVERSATION_GONE = (
    "that conversation is no longer stored; tell the user it is gone and carry on "
    "with the conversation you are in"
)

# The thread holds more than can be given to a model at once, so the
# user is offered the two ways of picking it up. Not a refusal: this is
# the one answer of the set that asks a question, and the answer to it
# comes back as `start_from` on the same conversation.
TOO_LONG_TO_RESUME_WHOLE = (
    "that conversation is longer than you can be given at once; ask the user "
    "whether they would like a short recap of the whole of it first, or would "
    "rather just carry on from the most recent part, then call "
    'resume_conversation again with the same conversation and start_from set to '
    '"recap" or "recent"'
)

# A `start_from` for a conversation that was never offered the choice.
# The model cannot invoke a recap nobody was asked about.
NO_CHOICE_OFFERED = (
    "you have not offered the user a choice about that conversation, so there is "
    "nothing to answer yet; call resume_conversation with the conversation alone"
)

# A `start_from` outside the two the offer named.
UNKNOWN_START = (
    'start_from has to be either "recap" or "recent", which are the two things '
    "the user was offered; ask them which they meant and call it again"
)

# A second selection in one reply. The first one won.
ALREADY_MOVED = (
    "this reply has already moved to another conversation; answer as yourself "
    "instead"
)

# The agent has no stored threads at all.
NOTHING_TO_RESUME = (
    "there are no earlier conversations with this user to resume; tell them that "
    "and carry on with the conversation you are in"
)

# The store could not be read. Two sentences for one closed set of two
# answers, and neither carries a word the database wrote.
STORE_UNREADABLE = (
    "the stored conversations could not be read; tell the user you cannot look "
    "them up right now"
)
STORE_BUSY = (
    "the stored conversations are busy right now; tell the user to ask again in a "
    "moment"
)

# What a list of candidates is introduced with, one line for each of the
# two things a search can find.
CANDIDATES_FOUND = (
    "These conversations may be the one. Read them out to the user, ask which they "
    "mean, and call resume_conversation again with that conversation."
)
CANDIDATES_UNMATCHED = (
    "Nothing stored matches that description. These are the most recent "
    "conversations instead. Read them out to the user, ask whether one of them is "
    "the one, and call resume_conversation again with that conversation."
)

# One candidate, as the model reads it aloud. The title and the excerpt
# are what the room said, which is what a tool result is for; a thread
# that stored neither says so rather than leaving a gap the model has to
# guess at.
CANDIDATE_LINE = (
    '{ordinal}. conversation "{conversation}", last active {last_active_at}, '
    'called "{title}", which opened: "{excerpt}"'
)
NOT_STORED = "(nothing was stored)"


def candidate_list(header: str, found: "Sequence[threads.Candidate]") -> str:
    """A discovery answer, as the model receives it: the sentence that
    says what this list is, then one numbered line per thread.

    Numbered from one because the number is what a user answers with
    ("the second one"), and carrying the id on the same line because the
    number is not what the follow-up call names: the ordinal is for the
    person and the id is for the tool.
    """
    return "\n".join(
        [
            header,
            *(
                CANDIDATE_LINE.format(
                    ordinal=ordinal,
                    conversation=candidate.conversation,
                    last_active_at=candidate.last_active_at,
                    title=candidate.title or NOT_STORED,
                    excerpt=candidate.excerpt or NOT_STORED,
                )
                for ordinal, candidate in enumerate(found, start=1)
            ),
        ]
    )


# What `remember` refuses a call it cannot act on, in this module's own
# vocabulary: what the call was missing rather than what arrived.
REMEMBER_NEEDS_TEXT = 'remember needs a "text" argument holding the fact to remember'

# And what an argument outside the enum the schema declares is told. It
# names the two members rather than the one that was passed, for the
# reason every refusal here does: the members are this server's
# vocabulary and what a model sent is a value.
UNKNOWN_SCOPE = (
    'scope has to be either "agent", for something about the person you are talking '
    'to, or "device", for something about this place and its household'
)

# And what the two numbered tools refuse a call that named no fact. They
# name the lookup, because that is where a number comes from: the
# injected block shows none.
UPDATE_NEEDS_A_NUMBER_AND_TEXT = (
    'update_memory needs the "id" of the fact to correct, which recall answers with, '
    'and the "text" to replace it with'
)

FORGET_NEEDS_A_NUMBER = (
    'forget needs the "id" of the fact to remove, which recall answers with'
)

RESTORE_TAKES_A_NUMBER = (
    'restore_memory takes the "id" of a fact you forgot, or nothing at all for the '
    "last thing you forgot in this conversation"
)

RECALL_NEEDS_A_QUERY = (
    'recall needs a "query": a word or two to look for in what you remember'
)

# What a lookup that found nothing answers with. Not a refusal: the model
# asked a question and this is the answer, so it says what to do next
# rather than what went wrong.
NOTHING_MATCHED = (
    "nothing you remember matches that. Try a different word, or tell the user you do "
    "not know"
)


async def remember(
    store: MemoryStore, context: MemoryContext, agent: str, arguments: dict[str, object]
) -> str:
    """Execute `remember` against the scope the call named, answering the
    confirmation the model then phrases in its own words.

    The confirmation carries the number the fact is addressed by, which
    is the one place a model is handed one without asking: the injected
    block shows no ids, so a fact remembered a moment ago would otherwise
    have to be looked up before it could be corrected.

    The owner comes from the session rather than from the arguments, like
    the conversation the ledger tools write to: which device a note
    belongs to is a fact of the session, and a model that could name one
    would be writing into another household's notes.
    """
    text = arguments.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError(REMEMBER_NEEDS_TEXT)
    scope = _fact_scope(arguments.get("scope"))
    owner = agent if scope is MemoryScope.AGENT else _device_of(context)
    fact_id = await store.add(scope, owner, text, agent=agent)
    return f"Remembered [{fact_id}]: {_said(text)}"


async def update_memory(
    store: MemoryStore, context: MemoryContext, agent: str, arguments: dict[str, object]
) -> str:
    """Execute `update_memory` against whichever memory holds the fact."""
    fact_id = _numbered(arguments.get("id"), UPDATE_NEEDS_A_NUMBER_AND_TEXT)
    text = arguments.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError(UPDATE_NEEDS_A_NUMBER_AND_TEXT)
    await _wherever_it_is(
        _owners(context, agent),
        lambda scope, owner: store.update(scope, owner, fact_id, text, agent=agent),
    )
    return f"Corrected [{fact_id}]: {_said(text)}"


async def forget(
    store: MemoryStore, context: MemoryContext, agent: str, arguments: dict[str, object]
) -> str:
    """Execute `forget`, answering with the words that were removed.

    The words come back so the agent can say them, which is the whole of
    what makes a soft removal reversible in a conversation: the row is
    held either way, and a user who never heard what went cannot ask for
    it back.

    `permanently` is true only when it arrives as true. Anything else is
    the soft removal, which is the direction a misread argument should
    fail in: what a held fact costs is a row, and what an erased one
    costs is the fact.
    """
    fact_id = _numbered(arguments.get("id"), FORGET_NEEDS_A_NUMBER)
    removed = await _wherever_it_is(
        _owners(context, agent),
        lambda scope, owner: store.forget(
            scope,
            owner,
            fact_id,
            _conversation_of(context),
            agent=agent,
            permanently=arguments.get("permanently") is True,
        ),
    )
    return f"Forgot [{fact_id}]: {removed}"


async def restore_memory(
    store: MemoryStore, context: MemoryContext, agent: str, arguments: dict[str, object]
) -> str:
    """Execute `restore_memory`, answering with what came back.

    With no number this is the last thing forgotten, asked of each memory
    the session can reach in turn: the agent's own first, then the
    device's. A conversation that forgot one of each therefore brings the
    agent's back first, and the answer says which fact it was, so the
    next ask reaches the other.
    """
    named = arguments.get("id")
    fact_id = None if named is None else _numbered(named, RESTORE_TAKES_A_NUMBER)
    brought = await _wherever_it_is(
        _owners(context, agent),
        lambda scope, owner: store.restore(
            scope, owner, _conversation_of(context), fact_id, agent=agent
        ),
    )
    return f"Brought back: {brought}"


async def recall(
    store: MemoryStore, context: MemoryContext, agent: str, arguments: dict[str, object]
) -> str:
    """Execute `recall` over both the memories this session can reach.

    The lookup is a database read and the caller is the event loop every
    live conversation shares, so it goes to a worker thread exactly as
    the prompt's own read does. Nothing matching is an ordinary answer
    rather than a refusal: the model asked a question and the answer is
    that there is nothing.
    """
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ValueError(RECALL_NEEDS_A_QUERY)
    found = await asyncio.to_thread(store.recall, agent, _device_of(context), query)
    return found or NOTHING_MATCHED


def _owners(context: MemoryContext, agent: str) -> tuple[tuple[MemoryScope, str], ...]:
    """The memories this session may reach a fact in, in the order they
    are tried.

    The agent's own and the device's, which is exactly what its prompt is
    assembled from: a number the model read out of a lookup came from one
    of those two, and nothing else is reachable from here at all.
    """
    return ((MemoryScope.AGENT, agent), (MemoryScope.DEVICE, _device_of(context)))


async def _wherever_it_is[T](
    owners: Sequence[tuple[MemoryScope, str]],
    act: Callable[[MemoryScope, str], Awaitable[T]],
) -> T:
    """One numbered operation, tried against each memory the session may
    reach until one of them owns the fact.

    Generic over what the operation answers with, because they differ: a
    correction answers nothing and a removal answers the words it
    removed, and one search rather than one per answer is what keeps the
    rule below written once.

    The model names a number and not a memory, and a number names at most
    one fact in the whole store, so asking both is the whole of the
    search. Which of them holds it is not the model's to know: the store
    bounds every number by the ownership in its own WHERE clause, so a
    refusal here means the fact is not this session's to touch and never
    that it is somebody else's.

    What travels when none of them owns it is the last refusal, built
    inside the arm that caught it and raised after, so nothing of the
    attempts rides out on a chain. The store's sentence is the same
    whichever memory refused, deliberately, so the answer says nothing
    about what exists elsewhere.
    """
    refusal: ValueError | None = None
    for scope, owner in owners:
        try:
            return await act(scope, owner)
        except ValueError as no_such_fact:
            refusal = ValueError(str(no_such_fact))
    assert refusal is not None
    raise refusal


def _numbered(value: object, refusal: str) -> int:
    """One fact's number as the model sent it.

    Digits in a string are accepted beside an integer, because a model
    reads the number out of a lookup line and hands it back as it read
    it; refusing that would be refusing the model its own answer. A
    boolean is not a number here, whatever Python thinks.
    """
    if isinstance(value, bool):
        raise ValueError(refusal)
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    raise ValueError(refusal)


def _fact_scope(named: object) -> MemoryScope:
    """Which scope a call named, defaulting to the agent's own.

    Absent means the agent, because that is what most facts are and
    because the tool that had no scope at all wrote there. Anything
    outside the two a fact may carry is refused here rather than at the
    store: the store would refuse it too, and this is where the model
    can be told what it may have meant.
    """
    if named is None:
        return MemoryScope.AGENT
    if not isinstance(named, str) or named not in FACT_SCOPES:
        raise ValueError(UNKNOWN_SCOPE)
    return MemoryScope(named)


# What the two state tools refuse a call they cannot act on. Fixed
# sentences in this module's own vocabulary, saying what the call was
# missing rather than what arrived: the arguments are the model's, and
# what it needs back is what to send instead.
SET_STATE_NEEDS_BOTH = (
    'set_state needs a "key" naming the note and a "value" saying what is true now'
)

CLEAR_STATE_NEEDS_A_KEY = (
    'clear_state needs a "key" naming the note to forget, which is the name it was '
    "written under"
)


async def set_state(
    store: MemoryStore, context: MemoryContext, agent: str, arguments: dict[str, object]
) -> str:
    """Execute `set_state` against this conversation's ledger.

    The conversation comes off the context rather than out of the
    arguments, which is the whole reason the context exists: which thread
    a note belongs to is a fact of the session, and a model that could
    name one could write into another conversation's ledger.
    """
    key = arguments.get("key")
    value = arguments.get("value")
    if not isinstance(key, str) or not key.strip():
        raise ValueError(SET_STATE_NEEDS_BOTH)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(SET_STATE_NEEDS_BOTH)
    await store.set_state(_conversation_of(context), key, value, agent=agent)
    return f"Noted {_said(key)}: {_said(value)}"


async def clear_state(
    store: MemoryStore, context: MemoryContext, agent: str, arguments: dict[str, object]
) -> str:
    """Execute `clear_state`, saying whether there was anything there.

    Both answers are ordinary. The store refuses nothing for a name that
    holds nothing, because clearing what is already clear is what the
    caller asked for, and the model is told which of the two happened so
    that it does not tell the user it removed something it did not.
    """
    key = arguments.get("key")
    if not isinstance(key, str) or not key.strip():
        raise ValueError(CLEAR_STATE_NEEDS_A_KEY)
    taken = await store.clear_state(_conversation_of(context), key, agent=agent)
    if not taken:
        return f"Nothing was written down under {_said(key)}"
    return f"Forgot {_said(key)}"


def _device_of(context: MemoryContext) -> str:
    """The board this call is happening on, which is what the device
    scope is addressed by.

    Asserted rather than refused, for the reason the conversation below
    is: the handshake reads the device's identity before the connection
    can be accepted at all, so every session that can be asked for
    anything has one, and a tool call with no device behind it is a
    defect here rather than something to tell a model about.
    """
    assert context.device is not None
    return context.device


def _conversation_of(context: MemoryContext) -> str:
    """The thread this call belongs to.

    Asserted rather than refused: a session activates an agent before it
    can be asked for anything, and minting the thread is part of that
    activation, so a tool call with no conversation behind it is a defect
    here and not something to tell a model about.
    """
    assert context.conversation is not None
    return context.conversation


def _said(text: str) -> str:
    """One argument as it goes back into a confirmation, on one line.

    The model's own words rather than the store's reading of them, and
    the same normalization the store applies, so what a confirmation
    quotes is what the next round's ledger shows.
    """
    return " ".join(text.split())
