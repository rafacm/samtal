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

# Printed after every mutating command, and answered with every
# successful write over HTTP. The configuration is a boot-time snapshot
# by design, and a write that quietly waits for a restart is the one
# thing about that design an operator can be caught by, so the write
# itself says when it takes effect.
RESTART_NOTICE = (
    "This applies at the next server start: the configuration is read once at boot."
)


def wrote_provider(stage: str, name: str) -> str:
    return f"provider {stage}.{name}"


def deleted_provider(stage: str, name: str) -> str:
    return f"provider {stage}.{name} deleted, with its stored secrets"


def wrote_mcp_server(name: str) -> str:
    return f"mcp-server {name}"


def deleted_mcp_server(name: str) -> str:
    return f"mcp-server {name} deleted, with its stored secrets"


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

CLEARED_DEFAULT_AGENT = "default agent cleared; the devices map is now the allowlist"


__all__ = [
    "CLEARED_DEFAULT_AGENT",
    "RESTART_NOTICE",
    "WROTE_AGENT_DEFAULTS",
    "bound_device",
    "cleared_secret",
    "deleted_agent",
    "deleted_device",
    "deleted_mcp_server",
    "deleted_provider",
    "wrote_agent",
    "wrote_default_agent",
    "wrote_mcp_server",
    "wrote_provider",
    "wrote_secret",
]
