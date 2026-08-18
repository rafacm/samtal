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
object, a name no source claims, and `switch_agent`, which ends the
tool loop rather than producing a result the model reads.

Routing is decided once and then carried. Every question below is asked
about the same CLAIM, the classification the runtime reserved on the
turn's record before anything ran, so MCP routing never resolves a name
a second time (the device source deliberately re-scans its live tool
list at ownership, the recorded edge behavior it inherited). An MCP reload landing between the reservation and the call
can move a published name to a more specific entry, and a source that
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

from collections.abc import Sequence
from typing import Any, Protocol

from samtal_server.device.boundary import DeviceOutput
from samtal_server.providers import ToolDef
from samtal_server.tools import builtin, names
from samtal_server.tools.mcp import McpServers
from samtal_server.tools.memory import MemoryStore


class ToolClaim(Protocol):
    """One call as the runtime reserved it, which is all a source is
    told about it.

    The concrete type is the turn record's `ToolInvocation`: the
    classification taken the moment the model's calls were known,
    carrying the name asked for, the arguments asked with, and, for an
    MCP call, the entry that owned that name at that moment. Declared
    structurally rather than imported, so that stating what routing
    reads does not make the tool layer import the conversation record
    it happens to be.
    """

    @property
    def name(self) -> str | None:
        """The tool the model asked for."""

    @property
    def arguments(self) -> dict[str, Any] | None:
        """What it asked with. `None` where the model's arguments never
        parsed as an object, which is a call the runtime answers itself
        and no source is ever handed."""

    @property
    def entry(self) -> str | None:
        """The MCP entry that owned the name when the call was reserved,
        and `None` for a call from any other source."""


class ToolSource(Protocol):
    """One source of tools, as a conversation reaches it."""

    def snapshot(self, agent: str) -> Sequence[ToolDef]:
        """What this source offers `agent` right now.

        Asked per reply rather than per session, so a server that came
        back, a device that finished discovering, and a reload that
        landed mid-conversation are all picked up on the next
        utterance."""

    def owns(self, claim: ToolClaim) -> bool:
        """Whether this claim is this source's to run.

        Names, not outcomes: a source owns a name it publishes even
        when it cannot run it, and says so itself in `dispatch`. A name
        no source claims is the runtime's to refuse."""

    async def dispatch(self, claim: ToolClaim, agent: str) -> tuple[str, bool]:
        """Run the claimed call as the agent speaking, answering
        `(content, is_error)`.

        The agent goes with the call because a grant is checked again
        here, so a tool the snapshot withheld is refused rather than run
        when a model asks for it anyway. Raising is allowed: the runtime
        turns any failure into an error result the model phrases."""

    def timeout_for(self, claim: ToolClaim) -> float:
        """How long the claimed call may take, in seconds."""


class BuiltinTools:
    """The tools the server implements itself.

    Owns both builtin names whether or not either can run. Asking to
    `remember` where no memory is configured is a builtin asked for, and
    what answers it is this source saying there is no such tool, in the
    same words the runtime uses for a name nobody publishes: the
    classification says which namespace the model reached into, and
    whether the call then ran is what the result says.

    `switch_agent` is offered here and executed by the runtime, because
    a successful one ends the tool loop rather than producing a result
    the model reads; it reaches `dispatch` only if that handling ever
    stops catching it, and is answered as the builtin that cannot run.
    """

    def __init__(
        self, agents: Sequence[str], memory: MemoryStore | None, timeout_s: float
    ) -> None:
        self._agents = agents
        self._memory = memory
        self._timeout_s = timeout_s

    def snapshot(self, agent: str) -> Sequence[ToolDef]:
        tools: list[ToolDef] = []
        # A device bound to one agent has nowhere to switch, so it gets
        # no dead tool.
        if len(self._agents) > 1:
            tools.append(builtin.switch_agent_tool(self._agents))
        if self._memory is not None:
            tools.append(builtin.remember_tool())
        return tools

    def owns(self, claim: ToolClaim) -> bool:
        return claim.name in names.BUILTIN_TOOL_NAMES

    async def dispatch(self, claim: ToolClaim, agent: str) -> tuple[str, bool]:
        if claim.name == names.REMEMBER and self._memory is not None:
            return await builtin.remember(self._memory, agent, claim.arguments or {}), False
        return f'there is no tool called "{claim.name}"', True

    def timeout_for(self, claim: ToolClaim) -> float:
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

    def owns(self, claim: ToolClaim) -> bool:
        return any(tool.name == claim.name for tool in self._output.device_tools())

    async def dispatch(self, claim: ToolClaim, agent: str) -> tuple[str, bool]:
        assert claim.name is not None
        return await self._output.call_device_tool(claim.name, claim.arguments or {})

    def timeout_for(self, claim: ToolClaim) -> float:
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

    def owns(self, claim: ToolClaim) -> bool:
        # Only a call classified `mcp` carries an entry, so the entry is
        # both the question and the answer.
        return claim.entry is not None

    async def dispatch(self, claim: ToolClaim, agent: str) -> tuple[str, bool]:
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

    def timeout_for(self, claim: ToolClaim) -> float:
        """The entry's own configured timeout, or the default where the
        entry the claim names is no longer running."""
        if claim.entry is not None:
            configured = self._servers.timeout_for(claim.entry)
            if configured is not None:
                return configured
        return self._default_timeout_s


__all__ = ["BuiltinTools", "DeviceTools", "McpTools", "ToolClaim", "ToolSource"]
