"""What a write says it did, and when it takes effect.

There are two write paths, and only one of them is ordinary: the API,
which the CLI is a client of, and the CLI's `--local` recovery subset,
which opens the database directly when there is no server to ask. Both
answer in these words. A module of its own, rather than one importing
the other, for two reasons: the API importing the CLI would be
backwards, and the CLI importing the API would make `config schema` and
`config reference` pay for FastAPI on their way to printing a document
that has nothing to do with it.

Nothing here decides anything either. These are the sentences an
operator reads, and the point of writing them once is that the
break-glass path and the ordinary one cannot come to describe the same
act differently.
"""

from collections.abc import Sequence

from vinga_server.config.entities import (
    AGENT_NOTICE,
    BINDING_NOTICE,
    RELOAD_NOTICE,
    RESTART_NOTICE,
    SNAPSHOT_NOTICE,
)
from vinga_server.config.secrets import EntityKind

# The sentences a write can end with are declared on the registry,
# beside the kinds that name them, and re-exported here because this is
# the module both write paths already import their vocabulary from. What
# is written here is the choosing: the two answers that depend on
# something a kind cannot know on its own.


def binding_notice(unloaded: Sequence[str] = (), snapshot_only: bool = False) -> str:
    """When a device write takes effect, which depends on two things.

    The binding itself is live. The agent it names is not: a server
    builds an agent's providers at boot, so a binding to an agent
    written since then resolves to nothing until a restart, and saying
    "no restart is needed" there would be a promise the device cannot
    keep. `unloaded` is the names this server has not loaded, empty when
    every one of them is loaded and the write is live.

    `snapshot_only` is the server that reads no store at all, and it
    answers before either of those: what is live about a binding is that
    a running server re-reads the rows, and a server serving a
    configuration it was handed re-reads nothing. The one true thing
    left to say is that the write is stored, which is what the sentence
    says. Written here rather than at the two call sites because this is
    already where a device write's answer is decided.
    """
    if snapshot_only:
        return SNAPSHOT_NOTICE
    return RESTART_NOTICE if unloaded else BINDING_NOTICE


def secret_notice(kind: EntityKind) -> str:
    """When a stored credential takes effect, which follows the entity it
    is stored on, and is now the same answer for both of them.

    The reload rebuilds the MCP entries with their credentials and the
    provider entries with theirs, so a rotation on either is applied by
    it: a credential is read as the thing that uses it is made, and a
    reload makes both again (#191). Kept as a question rather than
    collapsed into one sentence, because what decides it is still the
    entity kind and a third kind would arrive with its own answer. The
    API says the same by having four secret routes, two per kind, each
    statically one of these sentences; one CLI command covers both
    kinds, so it asks here.
    """
    return RELOAD_NOTICE if kind in ("mcp_server", "provider") else RESTART_NOTICE


def wrote_provider(stage: str, name: str) -> str:
    return f"provider {stage}.{name}"


def deleted_provider(stage: str, name: str) -> str:
    return f"provider {stage}.{name} deleted, with its stored secrets"


def wrote_mcp_server(name: str) -> str:
    return f"mcp-server {name}"


def deleted_mcp_server(name: str) -> str:
    return f"mcp-server {name} deleted, with its stored secrets"


def wrote_prompt_fragment(name: str) -> str:
    return f"prompt-fragment {name}"


def deleted_prompt_fragment(name: str) -> str:
    return f"prompt-fragment {name} deleted"


def wrote_agent(name: str) -> str:
    return f"agent {name}"


def deleted_agent(name: str) -> str:
    return f"agent {name} deleted"


def bound_device(mac: str, agents: Sequence[str]) -> str:
    return f"device {mac} bound to {', '.join(agents)}"


def deleted_device(mac: str) -> str:
    return f"device {mac} deleted"


def wrote_default_agent(name: str) -> str:
    return f"default agent {name}"


def wrote_secret(location: str) -> str:
    """`location` is a secret location as it describes itself: the kind,
    the entity, and the slot."""
    return f"secret for {location}"


def cleared_secret(location: str) -> str:
    return f"secret for {location} cleared"


WROTE_AGENT_DEFAULTS = "agent-defaults"


def wrote_agent_defaults() -> str:
    """The singleton's acknowledgement, which names no entry because
    there is only the one. A function so that it answers to the same
    call every other kind's does, and the constant beside it so that
    nothing has to be rewritten to keep saying it."""
    return WROTE_AGENT_DEFAULTS


CLEARED_DEFAULT_AGENT = "default agent cleared; the devices map is now the allowlist"


__all__ = [
    "AGENT_NOTICE",
    "BINDING_NOTICE",
    "CLEARED_DEFAULT_AGENT",
    "RELOAD_NOTICE",
    "RESTART_NOTICE",
    "SNAPSHOT_NOTICE",
    "WROTE_AGENT_DEFAULTS",
    "binding_notice",
    "bound_device",
    "cleared_secret",
    "deleted_agent",
    "deleted_device",
    "deleted_mcp_server",
    "deleted_prompt_fragment",
    "deleted_provider",
    "secret_notice",
    "wrote_agent",
    "wrote_agent_defaults",
    "wrote_default_agent",
    "wrote_mcp_server",
    "wrote_prompt_fragment",
    "wrote_provider",
    "wrote_secret",
]
