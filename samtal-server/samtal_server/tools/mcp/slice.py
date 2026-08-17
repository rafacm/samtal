"""The configuration one `McpServers` was built from.

Every entry under `mcp_servers`, what each agent may reach of each,
and the guidance an operator wrote about them: the half of this
subsystem that is configuration rather than connection. A reload
swaps one of these for another, which is how the grants change at
one instant rather than across one.

Beside it, the two questions that are asked of a grant and of a
manager set together: which of a server's tools a grant reaches,
and which running entries have another entry inside their
namespace.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from samtal_server.config import Config, McpGrant
from samtal_server.providers import ToolDef
from samtal_server.runtime.prompt import Guidance, GuidanceBlock
from samtal_server.tools import names

if TYPE_CHECKING:
    from .manager import McpServerManager


def _shadowed(managers: Mapping[str, "McpServerManager"]) -> frozenset[str]:
    """Which of these entries have another entry inside their namespace.

    A configuration property rather than a published-tool one: `home` is
    shadowed the moment `home__inside` is also running, whatever either
    of them publishes. Only these entries need their published names
    resolved before they are offered, which keeps the common
    deployment's snapshot free of the question.
    """
    return frozenset(
        entry
        for entry in managers
        for other in managers
        if other != entry and other.startswith(f"{entry}{names.SERVER_SEPARATOR}")
    )


def _allowed(grant: McpGrant, tools: list[ToolDef]) -> list[ToolDef]:
    """The tools of one server this grant reaches.

    Matched by the published name without its entry prefix, which is the
    identifier this application owns: it has been through the publishing
    rule, the status surface prints it and the model calls it, so what
    the operator wrote is compared against what the model would see and
    never against what the server listed.
    """
    if grant.tools is None:
        return tools
    allowed = set(grant.tools)
    return [tool for tool in tools if names.unqualified(grant.server, tool.name) in allowed]


def _nothing_shipped(_entry: str) -> tuple[GuidanceBlock, ...]:
    """What an entry contributes beyond its operator's own guidance when
    nobody is holding its connection: nothing. What a slice asked on its
    own answers, since a slice is configuration and the shipped blocks
    are a property of a live connection."""
    return ()


@dataclass(frozen=True)
class McpSlice:
    """The configuration an `McpServers` was built from: every entry
    under `mcp_servers`, referenced or not, and what each agent may
    reach of each.

    Kept rather than consulted again, so the status surface has one
    source and cannot disagree with what is running: an entry an
    operator has written since boot is not part of this world yet, and a
    view that read the database would say it was.
    """

    entries: tuple[str, ...] = ()
    grants: Mapping[str, tuple[McpGrant, ...]] = field(default_factory=dict)
    # The guidance each entry's operator wrote about it, for the entries
    # that carry any. Held beside the grants because it is swapped with
    # them: a reload that keeps a connection still changes what an
    # agent's next activation is told about it.
    instructions: Mapping[str, str] = field(default_factory=dict)
    # The entries whose operator opted into the guidance the server
    # ships about itself. Swapped with the grants for the same reason,
    # and held here rather than read off a manager because it is a
    # configuration decision: what the manager holds is what the server
    # said, and what this holds is whether anyone asked to hear it.
    use_server_instructions: frozenset[str] = frozenset()

    @classmethod
    def of(cls, config: Config) -> "McpSlice":
        return cls(
            entries=tuple(sorted(config.mcp_servers)),
            grants={
                agent: tuple(config.mcp_for_agent(agent)) for agent in sorted(config.agents)
            },
            instructions={
                name: entry.instructions
                for name, entry in sorted(config.mcp_servers.items())
                if entry.instructions is not None
            },
            use_server_instructions=frozenset(
                name
                for name, entry in config.mcp_servers.items()
                if entry.use_server_instructions
            ),
        )

    def allowed_by_agent(self, entry: str) -> dict[str, list[str] | None]:
        """Which agents may reach one entry and how much of it: the
        allow list they were given, or None for the whole server. In the
        order the grants were taken, which is agent-name order.

        One value per agent, since a list may name a server once."""
        return {
            agent: (None if grant.tools is None else list(grant.tools))
            for agent, grants in self.grants.items()
            for grant in grants
            if grant.server == entry
        }

    def grants_for(self, agent: str) -> tuple[McpGrant, ...]:
        """What one agent may reach, entry by entry and with each
        entry's allow list, and nothing for an agent this slice does not
        know.

        Not an error, deliberately: a session is holding the agent it
        was built with, and a reload can have applied a configuration
        that agent was deleted from. Answering "no servers" leaves that
        conversation talking without tools until it ends, which is what
        the rest of a deleted agent's session does too."""
        return tuple(self.grants.get(agent, ()))

    def guidance_for(
        self,
        agent: str,
        shipped: Callable[[str], Sequence[GuidanceBlock]] = _nothing_shipped,
    ) -> tuple[GuidanceBlock, ...]:
        """The guidance blocks one agent's grants name, in grant order.

        The effective grant is the whole condition, which is the
        deliverable read literally: guidance is injected for every agent
        granted the entry, whether or not that entry is connected and
        whatever its allow list narrows its tools to. Guidance for a
        server that is down, or one whose granted tools were all
        filtered away, is the same accepted noise as guidance naming a
        tool an agent cannot reach; what an operator does about it is
        write about the granted surface, and both surfaces make the
        mismatch visible. An agent granted nothing is answered with
        nothing.

        `shipped` answers what a live connection to one entry captured,
        which is the registry's to know and not a slice's. Passed in
        rather than looked up so that the order stays in one loop: an
        entry's blocks are the operator's, then the server's own, then
        the prompts it publishes, and an entry with no connection
        contributes only the first.
        """
        blocks: list[GuidanceBlock] = []
        for grant in self.grants_for(agent):
            text = self.instructions.get(grant.server)
            if text is not None:
                blocks.append(Guidance(grant.server, text))
            blocks.extend(shipped(grant.server))
        return tuple(blocks)

    def entries_for(self, agent: str) -> tuple[str, ...]:
        """Which entries one agent may reach, whole or in part. What a
        revive needs: an allow list narrows the tools, never whether the
        connection is worth making."""
        return tuple(grant.server for grant in self.grants_for(agent))

    def allows(self, agent: str, entry: str, published: str) -> bool:
        """Whether one agent's grants reach one published tool of one
        entry. False for an agent this slice does not know and for an
        entry it was never granted, so the question has one answer
        rather than two."""
        for grant in self.grants_for(agent):
            if grant.server != entry:
                continue
            return grant.tools is None or names.unqualified(entry, published) in grant.tools
        return False

    def allowed_names(self, entry: str) -> frozenset[str]:
        """Every tool name some grant allows of one entry, unprefixed.

        What a publication is checked against, so an allow list naming
        something the server does not offer is visible. A whole-server
        grant contributes nothing: it names no tool, so it can name none
        that failed to arrive."""
        return frozenset(
            name
            for grants in self.grants.values()
            for grant in grants
            if grant.server == entry and grant.tools is not None
            for name in grant.tools
        )
