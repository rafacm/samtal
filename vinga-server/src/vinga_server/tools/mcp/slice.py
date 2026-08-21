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

And, because this is the half a reload swaps, the comparison one
of these makes against a candidate composed from a fresh read:
which entries a stored configuration would add, take away or
change, and whose tools would move. It lives here rather than
beside the managers because it is a question about configuration
and answering it connects nothing.
"""

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from vinga_server.config import Config, McpGrant, McpServerConfig
from vinga_server.config.diff import McpPending
from vinga_server.config.secrets import SecretStore
from vinga_server.providers import ToolDef
from vinga_server.runtime.prompt import Guidance, GuidanceBlock
from vinga_server.tools import names

from .transport import _PROMPT_ONLY_FIELDS, _connection_identity

if TYPE_CHECKING:
    from .manager import McpManager


def _shadowed(managers: Mapping[str, "McpManager"]) -> frozenset[str]:
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


def _identity(name: str, entry: McpServerConfig, secrets: SecretStore) -> str:
    """One configured entry's comparison identity: an opaque mark of
    everything a world built from it stands on.

    Three parts, named separately because the reload already names them
    and this must not come to mean something else: what makes a
    connection stand (`_connection_identity`), the fields that only make
    prompt text and that a connection never sees (`_PROMPT_ONLY_FIELDS`,
    which the first one leaves out), and the credentials stored behind
    the entry, as the store's own opaque mark. Together they are the
    whole entry and the whole of what is kept for it, which is what an
    entry no agent references needs: it has no manager to be compared
    with, so this is the only record of it a generation holds.

    Which fields fall in which part is read from `_PROMPT_ONLY_FIELDS`
    rather than listed again, and the difference is worth knowing before
    reading either: `inject_prompts` is prompt text but it is not a
    prompt-only field, because editing it changes what a connect
    fetches, so it sits in the connection identity and a reload applies
    it by making the connection again. Reading the list is what keeps
    this true if a field ever moves between the two.

    A digest rather than the parts, so that retaining one per entry
    retains no entry values and no secret marks: agreement is the whole
    of what a caller may learn from one of these, and there is then
    nothing here that could reach a response, a log line or an
    exception.
    """
    digest = hashlib.sha256()
    for part in (
        _canonical(_connection_identity(entry).model_dump(mode="python")),
        _canonical({key: getattr(entry, key) for key in _PROMPT_ONLY_FIELDS}),
        secrets.fingerprint("mcp_server", name),
    ):
        # Length-prefixed, the rule the fingerprint itself follows: no
        # two different worlds may produce the same stream of bytes.
        digest.update(f"{len(part)}:".encode())
        digest.update(part.encode("utf-8"))
    return digest.hexdigest()


def _canonical(dumped: object) -> str:
    """One part of an identity as characters to digest, in a form that
    does not depend on how the value was built and cannot fail on
    anything a valid entry holds.

    The keys are sorted because an entry's `env` and `headers` are
    mappings and their order is not part of what they mean: two models
    holding the same pairs in a different order ARE equal, which is what
    the reload's own comparison says of them, so an identity that moved
    with insertion order would report a change nobody made. Nothing
    guarantees the order a mapping arrives in either, since it is
    whatever a stored document was written in and whatever a JSON
    decoder handed back.

    Totality is the whole of why this is not `model_dump_json`. A model
    field takes whatever a `str` can hold, and an unpaired surrogate is
    one of the things it can: it passes validation, it has no UTF-8
    encoding at all, and asking pydantic for JSON text raises a
    serialization error. This runs at every boot, for every configured
    entry including the ones nothing connects to, and that exception is
    not one the startup path classifies, so it would reach an operator
    as a library traceback out of uvicorn. Dumping to Python values and
    escaping every non-ASCII character keeps the answer text this cannot
    raise on: an unpaired surrogate becomes its own escape and compares
    as itself.

    Deliberately not `SecretStore.fingerprint`'s own encoder, which
    follows the same rule for its own reasons. Each digest is only ever
    compared with itself, so the two never have to agree, and sharing a
    call to `json.dumps` would tie together two things that are free to
    change apart.
    """
    return json.dumps(dumped, sort_keys=True, ensure_ascii=True, default=str)


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
    # One opaque comparison identity per configured entry, referenced or
    # not. Held here rather than beside the managers because this is the
    # object an install swaps: the identities therefore describe exactly
    # the world they were swapped in with, and a diff taken against them
    # cannot report a change a reload has already applied.
    identities: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def of(cls, config: Config, secrets: SecretStore | None = None) -> "McpSlice":
        """The configuration half of one world, composed from a snapshot.

        `secrets` is the store that snapshot was loaded with, or None for
        a deployment whose credentials are all environment references,
        the same argument `McpServers.build` takes. It is here because
        the comparison identities depend on it: an operator who rotates
        a credential has changed the entry as surely as one who edits
        its URL.

        One composition for both sides, which is the point: the world
        that gets installed and a candidate a diff is taken against are
        made by this, so the two cannot come to disagree about what an
        identity or an effective grant means.
        """
        stored = secrets if secrets is not None else SecretStore()
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
            identities={
                name: _identity(name, entry, stored)
                for name, entry in sorted(config.mcp_servers.items())
            },
        )

    def pending_against(self, candidate: "McpSlice") -> McpPending:
        """What a candidate composed from a fresh read holds that this
        world does not: entries added, taken away and changed, and the
        agents whose effective grants would move.

        Every configured entry is compared, referenced or not. An entry
        no agent names has no manager and no connection, so it is
        exactly the one an answer built from the managers would miss,
        and an operator who writes one and grants it nothing still wants
        to be told that the reload has something to apply.

        Whose grants are compared is this slice's own agents, which is
        the answer now that a slice and the world it belongs to move
        together (#191). It used to be passed in, because the server
        could serve only the agents it booted with while a slice held
        the agents of whichever world it was, and neither slice held the
        right set. An apply installs the agents with their grants, so
        this slice's agents are exactly the agents this server can be
        asked for. The candidate answers with the empty grant set for an
        agent it does not know, which is `grants_for`'s own rule read
        for what it means here: an agent the store has deleted is still
        being served, and the revocation an apply would make is reported
        until it makes it.
        """
        running, stored = set(self.identities), set(candidate.identities)
        return McpPending(
            added=tuple(sorted(stored - running)),
            removed=tuple(sorted(running - stored)),
            changed=tuple(
                sorted(
                    entry
                    for entry in running & stored
                    if self.identities[entry] != candidate.identities[entry]
                )
            ),
            grants=tuple(
                agent
                for agent in sorted(self.grants)
                if self.grants_for(agent) != candidate.grants_for(agent)
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
