"""What one server start reads: the file half, then the database.

The boot order is fixed and each step exists because of what would
otherwise fail later:

1. the file half, which is what says where the database is;
2. open and migrate that database, so a fresh data volume is a current
   one with no init command to forget;
3. load the snapshot, models and stored secrets together;
4. verify every stored secret opens under the configured keys, before
   anything is built, so a missing or wrong key fails with the error
   naming the entity and the slot rather than a decryption traceback
   from the middle of provider construction;
5. compose the two halves into `Config` and validate the whole snapshot,
   which is the same last line of defence it has always been.

The engine is closed here: the configuration is a boot-time snapshot,
so nothing after this reads the database except `DeviceBindings`
(`samtal_server.device.bindings`), which reads only the `devices` and
`domain_settings` tables, through a read engine of its own, so that
binding a device applies at that device's next OTA check or connection.
A CLI write to anything else while the server runs is picked up at the
next start, by design.
"""

from dataclasses import dataclass
from pathlib import Path

from samtal_server.config.loader import compose_config, load_file_config
from samtal_server.config.models import Config, domain_fields
from samtal_server.config.secrets import SecretStore, load_keys
from samtal_server.config.store import ConfigStore, verify_secrets
from samtal_server.db import DATABASE_FILENAME, open_database


@dataclass(frozen=True)
class BootConfig:
    """One boot's configuration: the composed models, and the stored
    secrets that ride beside them.

    The two travel together because the secrets are needed exactly where
    the configuration is turned into running things (providers, MCP
    servers) and nowhere else. Nothing here holds plaintext.
    """

    config: Config
    secrets: SecretStore


def load_boot_config(path: str | Path | None = None) -> BootConfig:
    """The configuration to serve from, read once."""
    file_half = load_file_config(path)
    directory = file_half.server.database.dir
    engine = open_database(directory)
    try:
        snapshot = ConfigStore(engine, load_keys()).load()
    finally:
        engine.dispose()

    verify_secrets(snapshot.secrets)
    config = compose_config(
        file_half, domain_fields(snapshot.domain), f"{directory / DATABASE_FILENAME}"
    )
    return BootConfig(config, snapshot.secrets)


__all__ = ["BootConfig", "load_boot_config"]
