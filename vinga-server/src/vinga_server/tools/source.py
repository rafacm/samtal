"""The three places a tool comes from, behind one interface.

The model is shown one flat list of tools per reply, and the runtime
that merged it knew each source by heart: which guard says a name is
this one's, which signature runs it, what that returns, and how long it
may take. Four conventions across two methods, one of them re-deriving
what another had already decided, and a fourth source would have added
a fifth convention.

What every source has in common is stated here instead. `ToolSource`
asks four questions: what do you offer this agent right now, is this
call yours, run it, and how long may it take. The runtime keeps exactly
what no source can answer: a call whose arguments never parsed as an
object, a name no source claims, and the three tools that end the tool
loop rather than producing a result the model reads, which are
`switch_agent` and the two that move a session to another
conversation.

Routing is decided once and then carried. Every question below is asked
about the same CLAIM, the classification the runtime reserved on the
turn's record before anything ran, so MCP routing never resolves a name
a second time (the device source deliberately re-scans its live tool
list at ownership, the recorded edge behavior it inherited). An MCP
reload landing between the reservation and the call can move a
published name to a more specific entry, and a source that
re-read the name would run one server's tool under another server's
timeout and then record the entry that did not run it. The claim is
what makes that impossible rather than unlikely.

The namespace in `names` is what makes the order the runtime asks in a
precedence rather than a scramble: builtins are bare, the device's
tools carry its prefix, an MCP server's carry their entry's, and
configuration forbids an entry from taking either of the other two
groups' names. No two sources can own one name, so asking them in a
fixed order settles nothing that was ever in doubt.
"""

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any, Protocol

from vinga_server.device.boundary import DeviceOutput
from vinga_server.memory.store import MemoryStore
from vinga_server.providers import ToolDef
from vinga_server.tools import builtin, names
from vinga_server.tools.mcp import McpServers

if TYPE_CHECKING:
    # Named for the annotations alone, so that stating what routing
    # reads does not make the tool layer import the conversations
    # package at import time.
    from vinga_server.conversations import records


class ToolSource(Protocol):
    """One source of tools, as a conversation reaches it.

    Every question below but the snapshot is asked about a `claim`, the
    turn record's `ToolInvocation` as the runtime reserved it: the
    classification taken the moment the model's calls were known,
    carrying the name the model asked for, the arguments it asked with,
    and, for an MCP call, the entry that owned that name at that moment.
    A call whose arguments never parsed as an object is one the runtime
    answers itself, so no source is ever handed one."""

    def snapshot(self, agent: str) -> Sequence[ToolDef]:
        """What this source offers `agent` right now.

        Asked per reply rather than per session, so a server that came
        back, a device that finished discovering, and a reload that
        landed mid-conversation are all picked up on the next
        utterance."""

    def owns(self, claim: "records.ToolInvocation") -> bool:
        """Whether this claim is this source's to run.

        Names, not outcomes: a source owns a name it publishes even
        when it cannot run it, and says so itself in `dispatch`. A name
        no source claims is the runtime's to refuse."""

    async def dispatch(self, claim: "records.ToolInvocation", agent: str) -> tuple[str, bool]:
        """Run the claimed call as the agent speaking, answering
        `(content, is_error)`.

        The agent goes with the call because a grant is checked again
        here, so a tool the snapshot withheld is refused rather than run
        when a model asks for it anyway. Raising is allowed: the runtime
        turns any failure into an error result the model phrases."""

    def timeout_for(self, claim: "records.ToolInvocation") -> float:
        """How long the claimed call may take, in seconds."""


class ThreadSearch(Protocol):
    """What this source needs from the resumption flow: one question.

    Searching for a past thread is a read that changes nothing, so it
    executes here like any other tool; picking one changes which
    conversation a session is on, so it is the runtime's. The seam is
    this one method, and it never raises: what comes back is already the
    sentence the model reads, refusals and store failures included.

    Named on this side rather than imported, because the caller is what
    says what it needs. `runtime/resumption.py` is what a server hands
    in.
    """

    async def described(self, agent: str, description: str) -> str: ...


def no_such_tool(name: str | None) -> tuple[str, bool]:
    """The error result for a name this server does not publish, as the
    result pair a dispatch answers with.

    One home for it because two callers say it and their saying the same
    thing is the contract rather than a coincidence: the runtime answers
    a name no source claims, and this module's builtins answer a name
    they own and cannot run, which is a builtin this deployment does not
    have and an agent switched off from memory alike. A model that asked
    for the second and got different words would learn that the tool
    exists somewhere, which is exactly what a withheld tool must not
    say.
    """
    return f'there is no tool called "{name}"', True


def withheld(name: str | None, remembers: Callable[[], bool]) -> bool:
    """Whether this name is one of the memory tools an agent that may not
    remember was never offered.

    One home for the rule because two callers ask it at two moments and
    the whole point is that they answer identically. The builtins ask it
    at the call, which is where a source says whether it will run a
    name; the runtime asks it before it answers anything about a call at
    all, because the answers it gives first would otherwise tell a
    withheld tool from a name nobody publishes: a model that mangles its
    arguments is told the arguments were unparseable, which is what a
    tool that exists says, and a tool this agent may not reach does not
    exist for the length of this reply.

    The policy arrives as the callable both askers already hold rather
    than as its answer, so that a name that is not a memory tool never
    asks for one. That is what keeps this question answerable about any
    call at all: the policy belongs to a reply, and a device tool or an
    MCP tool is a name this rule has nothing to say about.
    """
    return name in names.MEMORY_TOOL_NAMES and not remembers()


class BuiltinTools:
    """The tools the server implements itself.

    Owns every builtin name whether or not it can run. A builtin this
    server cannot run is still a builtin asked for, and what answers it
    is this source saying there is no such tool, in the same words the
    runtime uses for a name nobody publishes: the classification says
    which namespace the model reached into, and whether the call then
    ran is what the result says.

    Three of them are offered here and executed by the runtime, because
    what they do is end the tool loop rather than produce a result the
    model reads: `switch_agent` moves the conversation to another agent,
    `new_conversation` and a `resume_conversation` naming a thread move
    it to another conversation. `switch_agent` and `new_conversation`
    reach `dispatch` only if that handling ever stops catching them, and
    are answered as the builtin that cannot run.

    The two conversation tools are offered whether or not this server
    can resume anything, which is the point of the refusal they answer
    with: a tool that is simply absent is a tool a model invents, and a
    refusal it can read out is something the user hears.

    The memory family is offered wherever the agent's own `memory`
    section leaves it on, which is everywhere it says nothing: memory
    lives in a schema this server migrates at every boot (#314), so
    there is no deployment without one and the default is on. `remembers`
    is that condition, and it is the whole of it. An agent switched off
    is offered none of the seven and is answered as a name this server
    does not publish if it asks for one anyway, which is the same words
    a builtin that cannot run is refused with: for the length of that
    reply the tool does not exist, and saying so twice differently would
    be telling the model two things.

    A callable rather than a value for the reason `context` is one, and
    it answers what the reply resolved rather than what the world
    currently says: the policy has one clock per reply, taken where the
    snapshot is, so that the tools this offers and the blocks that
    reply's prompt carries cannot come from two halves of a reload.

    `context` is what every one of those tools is addressed by: a
    callable rather than a value, because a reply can move a session to
    another conversation and a note written after that move belongs to
    the thread the session is on now. It is a constructor concern of this
    one source rather than a widening of `ToolSource`: the protocol asks
    what every source has to answer, and where a session's memory lives
    is not one of those questions.

    `threads` is the search half of the resumption flow, absent in every
    deployment that has not switched resumption on and compared
    `is not None` for that reason.
    """

    def __init__(
        self,
        agents: Sequence[str],
        memory: MemoryStore,
        timeout_s: float,
        context: Callable[[], builtin.MemoryContext],
        remembers: Callable[[], bool],
        threads: ThreadSearch | None = None,
    ) -> None:
        self._agents = agents
        self._memory = memory
        self._timeout_s = timeout_s
        self._context = context
        self._remembers = remembers
        self._threads = threads

    def snapshot(self, agent: str) -> Sequence[ToolDef]:
        tools: list[ToolDef] = []
        # A device bound to one agent has nowhere to switch, so it gets
        # no dead tool.
        if len(self._agents) > 1:
            tools.append(builtin.switch_agent_tool(self._agents))
        # And an agent whose memory section is off gets none of the
        # seven, in the same shape and for the same reason: a tool it
        # may not run is a tool it should not be shown.
        if self._remembers():
            tools.append(builtin.remember_tool())
            tools.append(builtin.update_memory_tool())
            tools.append(builtin.forget_tool())
            tools.append(builtin.restore_memory_tool())
            tools.append(builtin.recall_tool())
            tools.append(builtin.set_state_tool())
            tools.append(builtin.clear_state_tool())
        tools.append(builtin.new_conversation_tool())
        tools.append(builtin.resume_conversation_tool())
        return tools

    def owns(self, claim: "records.ToolInvocation") -> bool:
        return claim.name in names.BUILTIN_TOOL_NAMES

    async def dispatch(self, claim: "records.ToolInvocation", agent: str) -> tuple[str, bool]:
        # The snapshot's condition, checked again at the call, which is
        # the rule the protocol states about a grant: a tool the offer
        # withheld is refused rather than run when a model asks for it
        # anyway. The name is still this source's to own, so what
        # answers is this source saying there is no such tool. The
        # runtime asks the same question earlier, about calls this
        # source is never handed; this is the answer for the calls it is.
        if withheld(claim.name, self._remembers):
            return no_such_tool(claim.name)
        if claim.name == names.REMEMBER:
            return (
                await builtin.remember(
                    self._memory, self._context(), agent, claim.arguments or {}
                ),
                False,
            )
        if claim.name == names.UPDATE_MEMORY:
            return (
                await builtin.update_memory(
                    self._memory, self._context(), agent, claim.arguments or {}
                ),
                False,
            )
        if claim.name == names.FORGET:
            return (
                await builtin.forget(
                    self._memory, self._context(), agent, claim.arguments or {}
                ),
                False,
            )
        if claim.name == names.RESTORE_MEMORY:
            return (
                await builtin.restore_memory(
                    self._memory, self._context(), agent, claim.arguments or {}
                ),
                False,
            )
        if claim.name == names.RECALL:
            return (
                await builtin.recall(
                    self._memory, self._context(), agent, claim.arguments or {}
                ),
                False,
            )
        if claim.name == names.SET_STATE:
            return (
                await builtin.set_state(
                    self._memory, self._context(), agent, claim.arguments or {}
                ),
                False,
            )
        if claim.name == names.CLEAR_STATE:
            return (
                await builtin.clear_state(
                    self._memory, self._context(), agent, claim.arguments or {}
                ),
                False,
            )
        if claim.name == names.RESUME_CONVERSATION:
            # The search half. A call that named a conversation never
            # arrives here: the runtime takes those, because a selection
            # ends the reply's loop.
            return await self._search(agent, claim.arguments or {}), False
        return no_such_tool(claim.name)

    async def _search(self, agent: str, arguments: dict[str, Any]) -> str:
        """One search, or the sentence saying why there was none.

        Not an error result in any of its arms, deliberately: what the
        model does with this is speak, and a refusal it can phrase is
        worth more than an error it apologizes for.
        """
        if self._threads is None:
            return builtin.RESUMPTION_UNAVAILABLE
        described = arguments.get("description")
        if not isinstance(described, str) or not described.strip():
            return builtin.RESUME_NEEDS_AN_ARGUMENT
        return await self._threads.described(agent, described)

    def timeout_for(self, claim: "records.ToolInvocation") -> float:
        return self._timeout_s


class DeviceTools:
    """The connected board's own tools, discovered over its socket.

    The live list is read at every question rather than kept, exactly as
    the runtime's dispatch read it: discovery finishes in the
    background, so what the device offers can grow between one reply and
    the next, and a device that lists a tool at the reservation and
    drops it before the call is answered as a name nobody publishes.
    """

    def __init__(self, output: DeviceOutput, timeout_s: float) -> None:
        self._output = output
        self._timeout_s = timeout_s

    def snapshot(self, agent: str) -> Sequence[ToolDef]:
        return self._output.device_tools()

    def owns(self, claim: "records.ToolInvocation") -> bool:
        return any(tool.name == claim.name for tool in self._output.device_tools())

    async def dispatch(self, claim: "records.ToolInvocation", agent: str) -> tuple[str, bool]:
        assert claim.name is not None
        return await self._output.call_device_tool(claim.name, claim.arguments or {})

    def timeout_for(self, claim: "records.ToolInvocation") -> float:
        return self._timeout_s


class McpTools:
    """The MCP servers one agent is granted, as one source.

    A view over the registry rather than the registry itself: the
    registry answers about entries, one conversation asks about calls,
    and translating between the two is what this is. Every answer is
    the entry the claim carries, never a second reading of the name.
    """

    def __init__(self, servers: McpServers, default_timeout_s: float) -> None:
        self._servers = servers
        self._default_timeout_s = default_timeout_s

    def snapshot(self, agent: str) -> Sequence[ToolDef]:
        # Which servers the agent is granted is the registry's answer
        # rather than the session's configuration: the grants are what a
        # reload swaps.
        return self._servers.tools_for_agent(agent)

    def owns(self, claim: "records.ToolInvocation") -> bool:
        # Only a call classified `mcp` carries an entry, so the entry is
        # both the question and the answer.
        return claim.entry is not None

    async def dispatch(self, claim: "records.ToolInvocation", agent: str) -> tuple[str, bool]:
        """Run the call against the entry the reservation named, not
        whoever owns the name by the time this line runs.

        A reload between the two can move a published name to a more
        specific entry, and following it would run one server's tool
        under another server's timeout and then record and log the entry
        that did not run it. The registry refuses a name that has moved
        rather than rerouting it (`McpServerDown`), which arrives at the
        runtime as the error result any failed tool produces.
        """
        assert claim.entry is not None and claim.name is not None
        return await self._servers.call(claim.name, claim.arguments or {}, agent, claim.entry)

    def timeout_for(self, claim: "records.ToolInvocation") -> float:
        """The entry's own configured timeout, or the default where the
        entry the claim names is no longer running."""
        if claim.entry is not None:
            configured = self._servers.timeout_for(claim.entry)
            if configured is not None:
                return configured
        return self._default_timeout_s


__all__ = [
    "BuiltinTools",
    "DeviceTools",
    "McpTools",
    "ThreadSearch",
    "ToolSource",
    "no_such_tool",
    "withheld",
]
