"""Which configuration kind takes effect at which boundary, and how two
configuration worlds are judged the same.

A running server holds one snapshot of the domain configuration and the
database holds another, and the question between them is what an
operator has written that is not in effect yet. Answering it needs two
facts that live nowhere else, which is what this module is: the regime
map, saying which kind converges at a restart, at a reload, or at
the next device check-in, and the comparison that decides an entity of
a kind has moved.

Equality is model inequality plus `SecretStore.fingerprint`, the opaque
per-entity mark that exists for exactly this question and can be asked
without a key. Nothing rendered, nothing masked and nothing decrypted
enters the comparison, so what comes out is entity names and closed
tokens by construction rather than by filtering: there is no value here
to leave out.

An agent layer is where model equality read literally is not the
question: one `mcp` grant has two spellings, so the comparison reads
that list as the grants it means (`_same_layer`). Rewriting an entry
from one form into the other changes nothing a reload would install,
and this module is the only place that has to know it, since a caller
asks whether an entity moved and not how it is written.

Changed means the stored state differs from the baseline, never that
something was written. An edit changed back before anyone looked
produces no diff, and a stored secret set again to the same plaintext
counts as different, because its ciphertext fingerprint moves even when
the value may not have. That is the store's own posture (rebuilding is
the safe direction to be wrong in) and it is the one the MCP reload's
`same_as` already takes.

Pure, and deliberately: both sides arrive composed, with the stored
secrets that were loaded beside them, so the tests build two worlds
from the support factories and nothing here opens a database. The MCP
half arrives composed too, as `McpPending`: the world a reload swaps is
the registry's to know, and its honest baseline is the generation that
is installed rather than the one this process booted on.

The answer is the shape the API sends, taken from `responses.py`, which
is where `McpReloadResult` lives for the same reason: what the answer
is made of is knowledge this module has, and a handler that had to
assemble it would be a second place that knew it. The tokens come from
there too, so the closed set a client reads out of the committed
document and the one this map is written in are one declaration.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import NamedTuple, Protocol

from vinga_server.config.models import (
    PROVIDER_STAGES,
    AgentDefaults,
    Config,
    McpGrant,
    ProviderConfig,
    as_mcp_grant,
)
from vinga_server.config.responses import (
    AgentsDiff,
    Applies,
    ConfigDiff,
    EntityDiff,
    FallbackDiff,
    FillerDiff,
    GrantsDiff,
    LiveKind,
    PromptDiff,
    SingletonDiff,
)
from vinga_server.config.secrets import SecretStore, provider_identity

# Which kind converges where: the one decision site, as data.
#
# Held to `models.DOMAIN_KEYS` by a test, so a seventh domain kind
# arrives with a failing test naming this module rather than falling
# silently out of the answer. That is the two-structures rule applied to
# this map: what the domain is has one home, and this reads it.
APPLIES: Mapping[str, Applies] = {
    "providers": Applies.RELOAD,
    "mcp_servers": Applies.RELOAD,
    "prompt_fragments": Applies.RELOAD,
    "agent_defaults": Applies.RELOAD,
    "agents": Applies.RELOAD,
    "devices": Applies.CHECK_IN,
    "default_agent": Applies.CHECK_IN,
}

# And the three regimes that are half of a kind rather than a kind.
# Every field of an agent entry is applied by a reload now, so these no
# longer separate what converges from what waits: they separate three
# moments a conversation meets a change at. The grants are snapshotted
# per reply, the prompt fields are assembled per activation, and the
# filler section is bound when a conversation opens, and an operator
# who has just written one of the three is told which of the three
# clocks their edit is on. None of them is in the map above, whose keys
# are the domain's own.
GRANTS_APPLY = Applies.RELOAD
PROMPT_APPLY = Applies.RELOAD
FILLER_APPLY = Applies.RELOAD
FALLBACK_APPLY = Applies.RELOAD

# Which fields of an agent entry the prompt half compares, which the
# filler half does, and which the fallback half does. Three tuples
# rather than one, because they are three answers: prompt text reaches a
# conversation at its next activation, and each kind of cached clip is
# bound when the next conversation opens. The two clip kinds are kept
# apart because they are staled apart: toggling the filler must not send
# a fallback phrase to a voice, or the reverse.
_PROMPT_FIELDS = ("prompt", "prompt_includes")
_FILLER_FIELDS = ("filler",)
_FALLBACK_FIELDS = ("fallback",)


class Loaded(Protocol):
    """One side of the comparison: a composed configuration, and the
    stored secrets that were loaded with it.

    A protocol rather than `config.boot.BootConfig`, which is what both
    sides are at a running server: importing that module opens a
    database and runs its migrations, and judging two configurations
    equal needs neither. It is also the whole of what a test has to
    supply, which is a configuration it built and a store it filled.
    """

    @property
    def config(self) -> Config: ...

    @property
    def secrets(self) -> SecretStore: ...


@dataclass(frozen=True)
class McpPending:
    """What the MCP world running right now differs from a stored
    candidate by, in the diff's own vocabulary.

    Composed by the registry rather than here, because what an MCP entry
    is compared by (its connection identity, the prompt fields a
    connection never sees, the mark of its stored credentials) is that
    package's knowledge, and because its baseline is the generation that
    is installed rather than the configuration this process booted on.

    `grants` is agent names and the other three are entry names. An
    agent is here when the tools it may reach would move, which a reload
    applies without a restart; an agent only the stored side knows is
    not here at all, since its grants describe a world that begins at
    the restart that adds it.
    """

    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    changed: tuple[str, ...] = ()
    grants: tuple[str, ...] = ()


def config_diff(running: Loaded, stored: Loaded, mcp: McpPending) -> ConfigDiff:
    """The whole answer: what each kind differs by, and when each
    difference would take effect.

    `running` is what this process is serving and `stored` is what the
    database holds now; both are composed snapshots with the secrets
    loaded beside them, so this compares two whole worlds rather than
    two documents. `mcp` is the registry's own answer, taken against the
    generation that is installed, and it is carried through rather than
    recomputed: the boot's MCP entries are not the running server's,
    because a reload can have replaced them since.
    """
    running_providers = _providers(running)
    stored_providers = _providers(stored)
    unchanged = unchanged_providers(running, stored)

    def same_provider(identity: str) -> bool:
        return identity in unchanged

    def same_fragment(name: str) -> bool:
        return running.config.prompt_fragments[name] == stored.config.prompt_fragments[name]

    def same_agent(name: str) -> bool:
        return _same_layer(running.config.agents[name], stored.config.agents[name])

    def same_fields(name: str, fields: tuple[str, ...]) -> bool:
        own, theirs = running.config.agents[name], stored.config.agents[name]
        return all(getattr(own, field) == getattr(theirs, field) for field in fields)

    def same_prompt(name: str) -> bool:
        return same_fields(name, _PROMPT_FIELDS)

    def same_filler(name: str) -> bool:
        return same_fields(name, _FILLER_FIELDS)

    def same_fallback(name: str) -> bool:
        return same_fields(name, _FALLBACK_FIELDS)

    return ConfigDiff(
        providers=EntityDiff(
            applies=APPLIES["providers"],
            **_names(running_providers, stored_providers, same_provider)._asdict(),
        ),
        mcp_servers=EntityDiff(
            applies=APPLIES["mcp_servers"],
            added=mcp.added,
            removed=mcp.removed,
            changed=mcp.changed,
        ),
        prompt_fragments=EntityDiff(
            applies=APPLIES["prompt_fragments"],
            **_names(
                running.config.prompt_fragments,
                stored.config.prompt_fragments,
                same_fragment,
            )._asdict(),
        ),
        agent_defaults=SingletonDiff(
            applies=APPLIES["agent_defaults"],
            changed=not _same_layer(
                running.config.agent_defaults, stored.config.agent_defaults
            ),
        ),
        agents=AgentsDiff(
            applies=APPLIES["agents"],
            **_names(running.config.agents, stored.config.agents, same_agent)._asdict(),
            grants=GrantsDiff(applies=GRANTS_APPLY, changed=mcp.grants),
            prompt=PromptDiff(
                applies=PROMPT_APPLY,
                changed=_names(
                    running.config.agents, stored.config.agents, same_prompt
                ).changed,
            ),
            filler=FillerDiff(
                applies=FILLER_APPLY,
                changed=_names(
                    running.config.agents, stored.config.agents, same_filler
                ).changed,
            ),
            fallback=FallbackDiff(
                applies=FALLBACK_APPLY,
                changed=_names(
                    running.config.agents, stored.config.agents, same_fallback
                ).changed,
            ),
        ),
        devices=LiveKind(applies=APPLIES["devices"]),
        default_agent=LiveKind(applies=APPLIES["default_agent"]),
    )


def _same_layer(running: AgentDefaults, stored: AgentDefaults) -> bool:
    """Whether two versions of one agent layer, an agent's own entry or
    the defaults under all of them, hold the same configuration.

    Model equality, with the `mcp` list compared as the grants it means
    rather than as it was spelled. `as_mcp_grant` in `models.py` is
    where that rule lives and this reads it, so the string form and the
    object form of one whole-server grant are the same grant here for
    the same reason they are the same grant to a reload: an operator who
    rewrites `- tools` as `- {server: tools}` has changed nothing, and
    reporting it as pending would send them to look for an edit that is
    not there.
    """
    return running.model_copy(update={"mcp": _grants(running)}) == stored.model_copy(
        update={"mcp": _grants(stored)}
    )


def _grants(layer: AgentDefaults) -> list[McpGrant] | None:
    """One layer's `mcp` list as grants, and unset left as unset.

    The distinction is the field's own: `None` inherits the list under
    it and `[]` replaces that list with nothing, which is how an agent
    opts out of every tool its siblings have (`Config.mcp_for_agent`).
    Mapping the absent list to an empty one would report nothing pending
    for an edit from `null` to `[]`, while the reload it is waiting for
    revokes every inherited grant.
    """
    return None if layer.mcp is None else [as_mcp_grant(item) for item in layer.mcp]


class _Names(NamedTuple):
    """The three name lists one kind answers with, so that a comparison
    and the entry it fills in cannot come to disagree about their
    order."""

    added: tuple[str, ...]
    removed: tuple[str, ...]
    changed: tuple[str, ...]


def _names(
    running: Mapping[str, object],
    stored: Mapping[str, object],
    same: Callable[[str], bool],
) -> _Names:
    """Which names one kind gained, lost, and holds differently.

    `same` is asked only about a name both sides have, so it may index
    either of them. Sorted, because these are read by a person and by a
    client comparing two answers, and neither should see an order that
    depends on how a mapping was built.
    """
    return _Names(
        added=tuple(sorted(set(stored) - set(running))),
        removed=tuple(sorted(set(running) - set(stored))),
        changed=tuple(
            sorted(name for name in set(running) & set(stored) if not same(name))
        ),
    )


def unchanged_providers(running: Loaded, stored: Loaded) -> frozenset[str]:
    """The provider entries both sides hold and hold identically: the
    entry as it is written, and the credentials stored behind it, which
    an operator rotates without touching a field of the entry.

    Two callers, one question. This read reports the rest as changed;
    the apply carries exactly these into the world it installs as the
    objects they already are, and builds everything else. Written once
    because the two must agree by construction: an entry this called
    unchanged and the apply rebuilt would be a rotation reported as
    pending forever, and the other way round is a credential an operator
    rotated that nothing ever picked up.

    An entry only one side holds is in neither answer: it is an addition
    or a removal, which both callers handle as one.
    """
    running_entries, stored_entries = _providers(running), _providers(stored)
    return frozenset(
        identity
        for identity in set(running_entries) & set(stored_entries)
        if running_entries[identity] == stored_entries[identity]
        and running.secrets.fingerprint("provider", identity)
        == stored.secrets.fingerprint("provider", identity)
    )


def _providers(side: Loaded) -> dict[str, ProviderConfig]:
    """Every provider entry of one side, by the identity a provider is
    named by everywhere: its stage and its name together, which is what
    the store addresses its secrets under and what every refusal
    prints."""
    return {
        provider_identity(stage, name): entry
        for stage in PROVIDER_STAGES
        for name, entry in getattr(side.config.providers, stage).items()
    }
