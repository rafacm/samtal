"""Every MCP server some agent references, and the questions asked
of them together.

One manager per referenced `mcp_servers` entry, connected once at
startup and shared by every session whose agent names it, beside
the slice those managers were built for. What lives here is what
needs both halves: which tools an agent may reach right now, which
entry owns a published name, what an entry is doing, and the call
that carries out a decision a caller already made.

The reload's two phases are next door, in `reload.py`, and the
second of them reaches back through this class: the swap it makes is
this registry's own state. What is not here any more is when a reload
may run and how long it is held: one apply at a time is a property of
the whole configuration and not of the MCP half, so the exclusion
lives with the generalized apply that owns every half (#191).
"""

import asyncio
import contextlib
import time
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from vinga_server.config import Config
from vinga_server.config.diff import McpPending
from vinga_server.config.responses import McpServerStatus
from vinga_server.config.secrets import SecretStore
from vinga_server.events.catalog import McpToolShadowed
from vinga_server.events.values import Count, Identifier
from vinga_server.providers import ToolDef
from vinga_server.runtime.prompt import GuidanceBlock, ServerInstructions
from vinga_server.tools import names

from . import events
from .manager import UNUSED, McpManager, McpServerDown, _managers_for
from .slice import McpSlice, _allowed, _shadowed


class McpToolNotGranted(LookupError):
    """A call to a tool the speaking agent's grants do not name.

    Not an unreachable state: the snapshot the model was given already
    left the tool out, so this is what remains if a model calls a name
    it was not offered. It travels to the session as the error result an
    unknown tool produces, which the agent phrases in its own words."""


def _instant(when: float) -> str:
    """One of the instants the status view carries, as a person reads
    it. UTC and ISO-8601, the shape the pending listing answers with,
    because a status read compared against a server's log is compared
    against that server's clock."""
    return datetime.fromtimestamp(when, UTC).isoformat()


class McpServers:
    """Every MCP server some agent references, built at startup."""

    def __init__(
        self, managers: Mapping[str, McpManager], configured: McpSlice | None = None
    ) -> None:
        # Held as the mapping it was given, never copied: the reload's
        # atomic swap rebinds it whole, and a dict parameter would be
        # invariant where every implementation of the protocol must fit.
        self._managers: Mapping[str, McpManager] = managers
        self._configured = configured if configured is not None else McpSlice()
        # Which running entries have a more specific entry inside their
        # namespace, and which of their tools have already been reported
        # unreachable because of it. Both are decided by the manager set,
        # so both are replaced when it is.
        self._shadowed = _shadowed(managers)
        self._reported: set[tuple[str, str]] = set()
        # An entry no agent references has no manager and never
        # transitions, so the instant its status carries is when this
        # configuration took effect, which is what this is.
        self._since = time.time()

    @classmethod
    def build(cls, config: Config, secrets: SecretStore | None = None) -> "McpServers":
        """Managers for the entries agents actually use, the way only
        referenced providers are built. Raises McpConfigError for an
        entry that cannot be built, or one that server.local_only
        forbids, which fails the boot.

        `secrets` is the store a snapshot was loaded with, or None for a
        deployment whose credentials are all environment references."""
        configured = McpSlice.of(config, secrets)
        return cls(_managers_for(config, secrets, configured), configured)

    def __len__(self) -> int:
        return len(self._managers)

    def __contains__(self, entry: object) -> bool:
        return entry in self._managers

    def manager_of(self, entry: str) -> McpManager:
        """The manager behind one entry, or a KeyError for an entry that
        has none.

        Identity, which is the one thing about a manager nothing else
        answers. What a reload leaves alone is this object, and every
        visible property of it (its state, its tools, its instant) would
        read the same on a manager that had been stopped and started
        again, so a suite proving a connection stood has to hold the
        object and look again. The same read serves a reconnect driven
        from outside, which is not something an operator asks for: it is
        what happens when a server goes away and comes back.

        A KeyError rather than None for an entry no agent references,
        because an entry with no manager is a question about the
        configuration, and `status()` is where that one is answered.
        """
        return self._managers[entry]

    async def start_all(self) -> None:
        """Connect every server concurrently, so one slow box does not
        add its timeout to the boot of the next."""
        if self._managers:
            await asyncio.gather(*(manager.start() for manager in self._managers.values()))

    async def stop_all(self) -> None:
        """Close every connection, so stdio child processes do not
        outlive the server."""
        for manager in self._managers.values():
            with contextlib.suppress(Exception):
                await manager.stop()

    def tools_for(self, entries: Iterable[str]) -> list[ToolDef]:
        """The tools of these entries, skipping servers that are down."""
        return [
            tool
            for entry in entries
            if entry in self._managers
            for tool in self._reachable(entry)
        ]

    def owner_of(self, published: str) -> str | None:
        """Which running entry a published tool name belongs to, or None
        when no entry claims it.

        The one place a published name is resolved. Everything that has
        to agree about which server a name belongs to (the offer, the
        timeout, the grant check and the call itself) asks here, so the
        list the model is given and the routing of what it calls cannot
        answer differently.
        """
        return names.owner_of(published, self._managers)

    def _reachable(self, entry: str) -> list[ToolDef]:
        """One entry's tools under the names that actually route to it.

        Two entries publish the same name when one entry name is inside
        the other's namespace: `home` listing a tool called
        `inside__turn_on` publishes `home__inside__turn_on`, and so does
        `home__inside` listing `turn_on`. The name belongs to the more
        specific entry, so the other one's tool is dropped rather than
        offered: a name in front of the model that runs a different
        server's tool is worse than a tool the model was never offered,
        and this is the same first-wins drop `publish` already makes
        inside one server.

        Decided here rather than when the server published, because what
        decides it is the manager set: a reload that adds
        `home__inside` changes the answer for `home`, which reconnected
        nothing and published nothing new.
        """
        manager = self._managers[entry]
        if entry not in self._shadowed:
            return manager.tools()
        kept: list[ToolDef] = []
        for tool in manager.tools():
            owner = self.owner_of(tool.name)
            if owner == entry:
                kept.append(tool)
            elif (entry, tool.name) not in self._reported:
                # The position and the entry that owns the name, never
                # the name, in the sentence and in the fields alike: the
                # half of it this server did not choose is the far
                # side's bytes, sanitizing replaces only characters an
                # API refuses so a credential survives it whole, and a
                # tool that does not reach the model has no claim on the
                # log. Once per tool per manager set, since this is read
                # once a reply.
                #
                # The position is the far side's own, read off the
                # publication rather than counted here. Counting this
                # loop would count the published list, which is the
                # listing with the unpublishable dropped out of it, so a
                # server whose fourth tool was too long to publish would
                # have every later position reported one too low, and
                # the number an operator went looking with would find a
                # different tool.
                self._reported.add((entry, tool.name))
                position = manager.listed_at(tool.name)
                # Bound as defaults rather than closed over: this thunk
                # is built inside a loop, and a closure would read
                # whichever tool the loop had reached by the time the
                # guard called it.
                events.emit(
                    lambda at=position, by=owner: McpToolShadowed(  # type: ignore[misc]
                        entry=Identifier(entry),
                        position=Count(at),
                        owner=Identifier(by),
                    )
                )
        return kept

    def tools_for_agent(self, agent: str) -> list[ToolDef]:
        """The tools one agent may reach right now, its grants applied.

        Asked by agent rather than handed a list of entries, because the
        list is part of what a reload replaces: a session was built on
        the configuration that was loaded at boot, and the grants that
        decide what it may reach are the ones swapped in with the
        managers they name. A snapshot taken through here therefore sees
        one world, and the next reply's snapshot sees the next one.
        """
        return [
            tool
            for grant in self._configured.grants_for(agent)
            for tool in _allowed(grant, self.tools_for([grant.server]))
        ]

    def guidance_for_agent(self, agent: str) -> tuple[GuidanceBlock, ...]:
        """The guidance for the entries one agent is granted, in grant
        order: what the operator wrote about each, and what each server
        shipped about itself where the entry opted into it.

        Asked by agent for the reason `tools_for_agent` is: the grants
        are part of what a reload swaps, so this answers the world that
        is running rather than the configuration a session was built on.
        Read at activation, where the know-how half is assembled and
        cached, so a reload's guidance reaches new sessions and
        switched-in agents.
        """
        return self._configured.guidance_for(agent, self._shipped_by)

    def _shipped_by(self, entry: str) -> tuple[GuidanceBlock, ...]:
        """What one entry's live connection captured, where the entry
        opted into it.

        The instructions are captured whatever the flag says, so this is
        where the flag is finally read: a reload that turns it on
        exposes what a connection nobody restarted is already holding,
        and one that turns it off stops the injection while that same
        connection stands. The prompts need no such gate, since editing
        the list that names them restarts the connection that fetched
        them.
        """
        manager = self._managers.get(entry)
        if manager is None:
            return ()
        blocks: list[GuidanceBlock] = []
        if entry in self._configured.use_server_instructions:
            shipped = manager.shipped_instructions
            if shipped is not None:
                blocks.append(ServerInstructions(entry, shipped))
        blocks.extend(manager.shipped_prompts)
        return tuple(blocks)

    def revive(self, entries: Iterable[str]) -> None:
        """Kick off a background reconnect for any of these that is
        down. Called when a session opens."""
        for entry in entries:
            manager = self._managers.get(entry)
            if manager is not None:
                manager.ensure_reconnecting()

    def revive_for_agents(self, agents: Iterable[str]) -> None:
        """The same, for everything the named agents may reach, through
        the grants that are running now."""
        self.revive(
            [entry for agent in agents for entry in self._configured.entries_for(agent)]
        )

    def _install(self, keep: dict[str, McpManager], configured: McpSlice) -> None:
        """The swap, and everything it decides at once: which managers a
        tool snapshot reaches, and which entries an agent's grant
        names. Assigned rather than mutated, and with no await between
        the two, so no reply can be built on half of one world.

        One method rather than five assignments a caller makes, because
        the no-await property is the contract: the apply that calls this
        is in another module, and a contract that lives in the order of
        somebody else's statements is a contract nobody can check.
        """
        self._managers = keep
        self._configured = configured
        self._shadowed = _shadowed(keep)
        self._reported = set()
        self._since = time.time()

    def status(self) -> dict[str, dict[str, Any]]:
        """What every configured entry is doing right now, by name.

        One entry per configured `mcp_servers` entry rather than one per
        manager, because an entry no agent references has no manager at
        all and its absence from a list of tools is exactly the thing an
        operator cannot see from anywhere else.

        The tool lists are published names and nothing else. A
        description, or the name a server listed before the publishing
        rule got to it, is bytes that server chose, and a server holding
        a credential of ours can reflect one in either; a gated read that
        carried them would be the secret-readback path the rest of the
        API refuses to be.

        Mappings and not the API's models, which is a decision and not
        an omission: what reads this is this server, and what it does
        with it is index it and serialize it whole. `typed_status`
        below is the one caller that needs the shapes, and is where
        they are put on.
        """
        return {entry: self._status_of(entry) for entry in self._configured.entries}

    def _status_of(self, entry: str) -> dict[str, Any]:
        # `grants` is a mapping rather than a list of agent names, and
        # the value is how much of the server that agent gets: None is
        # all of it, a list is the tools it was allowed. Beside the
        # published list above it, which is what makes an allow list
        # naming something this server does not offer answerable in one
        # read.
        grants = self._configured.allowed_by_agent(entry)
        manager = self._managers.get(entry)
        if manager is None:
            return {
                "state": UNUSED,
                "reason": None,
                "since": _instant(self._since),
                "tools": [],
                "grants": grants,
            }
        return {
            "state": manager.state,
            "reason": manager.reason,
            "since": _instant(manager.since),
            "tools": [tool.name for tool in self._reachable(entry)],
            "grants": grants,
        }

    def typed_status(self) -> dict[str, McpServerStatus]:
        """The same answer as `status()`, as the models the API sends.

        The API-facing view, and the reason it is a view rather than
        what `status()` returns: every consumer inside this server
        reads those mappings as mappings, and a `json.dumps` of one is
        what proves this surface reflects nothing a far side wrote.
        What the API needs instead is the shape it declares, so the
        adapter lives here, one line from the knowledge it validates,
        rather than in a request handler five hundred lines from it.

        Validating rather than constructing, deliberately: the models
        forbid extra keys, so a field this registry starts answering
        with and the document does not declare fails here, in this
        server's own tests, instead of on a client.
        """
        return {
            entry: McpServerStatus.model_validate(status)
            for entry, status in self.status().items()
        }

    def pending_against(
        self, config: Config, secrets: SecretStore | None = None
    ) -> McpPending:
        """What a stored configuration holds that the world running now
        does not: entries added, taken away and changed, and the agents
        whose tools would move.

        The baseline is the generation that is installed, never the
        configuration this process booted on. The MCP half is the one
        half a running server replaces: `POST
        /runtime/config/reload` swaps this world while the process
        runs, so a comparison against the boot would report changes a
        reload has already applied, which is the one thing an answer
        about what is pending must not do.

        The grants are reported for the agents this server can serve,
        which is the agents of the slice that is installed. That set
        moves with the world now (#191): an apply installs the stored
        agents along with their grants, so an agent this comparison has
        just started reporting on is one a session can be built for, and
        one it has stopped reporting on is one no session can be opened
        as, whatever conversation may still be finishing on it. An agent
        only the stored side knows rides the agents' own added row
        instead, which is where a whole entry arriving belongs.

        Nothing is connected, built or started, and nothing about a
        manager is read. The candidate is composed into the same slice a
        reload would install, and the two are compared as
        configuration, so this costs a hash per entry and no far side is
        involved. A caller learns entry names and agent names and
        nothing else: not what an entry connects to, not what is stored
        for it, and not how either world is held.
        """
        return self._configured.pending_against(McpSlice.of(config, secrets))

    def timeout_for(self, entry: str) -> float | None:
        manager = self._managers.get(entry)
        return None if manager is None else manager.tool_timeout_s

    async def call(
        self, published: str, arguments: dict[str, Any], agent: str, expected: str
    ) -> tuple[str, bool]:
        """Run a tool under the qualified name the model was given, for
        the agent that is speaking, against the entry the caller
        resolved it to. The entry prefix says which server owns it, and
        the server maps the rest back to whatever it actually listed.

        `expected` is that caller's answer, and this method will not
        substitute its own. A reload can land between a name being
        resolved and being called: an entry can go, and a more specific
        entry can take a published name over from a less specific one
        (`home__inside` claiming what `home` published as
        `home__inside__x`). Rerouting to whoever owns the name now would
        execute one server's tool under another server's timeout, and
        record and log an entry that never ran it, so a name that has
        moved is refused instead. The caller reserved a decision; this
        either carries it out or says it could not.

        The grant is checked here as well as when the snapshot was
        taken, so "this agent cannot reach that tool" does not rest on
        the model only calling what it was shown. The agent is passed in
        rather than remembered: one registry serves every session, and
        the grants are the ones running now.
        """
        entry = self.owner_of(published)
        if entry is None:
            raise McpServerDown(f'no MCP server owns a tool called "{published}"')
        if entry != expected:
            raise McpServerDown(
                f'the tool "{published}" is no longer served by MCP server "{expected}"'
            )
        if not self._configured.allows(agent, entry, published):
            raise McpToolNotGranted(
                f'this assistant is not allowed to use the tool "{published}"'
            )
        return await self._managers[entry].call(published, arguments)
