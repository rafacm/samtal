"""Configuration: the file half's models, and the composition of the whole.

Deliberately without the boot path (`samtal_server.config.boot`) and the
repository behind it: importing this package pulls in neither the
database driver nor the migrations, so a command that only renders the
models keeps needing neither.
"""

from samtal_server.config.loader import ConfigError, compose_config, load_file_config
from samtal_server.config.models import (
    AgentConfig,
    AgentDefaults,
    Config,
    FileConfig,
    McpGrant,
    McpServerConfig,
    MemoryConfig,
    PromptFragmentConfig,
    ProviderConfig,
    ProvidersConfig,
    ServerConfig,
    resolve_env_references,
)

__all__ = [
    "AgentConfig",
    "AgentDefaults",
    "Config",
    "ConfigError",
    "FileConfig",
    "McpGrant",
    "McpServerConfig",
    "MemoryConfig",
    "PromptFragmentConfig",
    "ProviderConfig",
    "ProvidersConfig",
    "ServerConfig",
    "compose_config",
    "load_file_config",
    "resolve_env_references",
]
