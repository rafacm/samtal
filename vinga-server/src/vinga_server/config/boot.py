"""What one server start reads: the file half, then the database.

The boot order is fixed and each step exists because of what would
otherwise fail later:

1. the file half, which is what says which database this is;
2. open and migrate that database, so a fresh instance is a current one
   with no init command to forget;
3. load the snapshot, models and stored secrets together;
4. verify every stored secret opens under the configured keys, before
   anything is built, so a missing or wrong key fails with the error
   naming the entity and the slot rather than a decryption traceback
   from the middle of provider construction;
5. compose the two halves into `Config` and validate the whole snapshot,
   which is the same last line of defence it has always been.

The engine is closed here: what this reads is a snapshot, so nothing
after this reads the database except `DeviceBindings`
(`vinga_server.device.bindings`), which reads only the `devices` and
`domain_settings` tables, through a read engine of its own, so that
binding a device applies at that device's next OTA check or connection,
and `reload_domain_config` below, which a running server runs on request
to apply what it can apply while it runs (`config/reload.py`).

Both exceptions are of the same shape, and it is the shape the boot
contract keeps: a named, bounded slice of the domain half, re-read
deliberately, with everything else still fixed at the moment this ran.
A CLI write to anything the reload does not apply is picked up at the
next start, by design.
"""

from dataclasses import dataclass
from pathlib import Path

from vinga_server.config.loader import compose_config, load_file_config
from vinga_server.config.models import Config, FileConfig, domain_fields
from vinga_server.config.secrets import SecretStore, load_keys
from vinga_server.config.store import ConfigStore, verify_secrets
from vinga_server.db import DOMAIN_CHAIN, open_database

# Where the domain half came from, for a problem in it to name.
#
# The schema and nothing else. What this used to name was the database
# file's path, which was where an operator would go and look; the
# replacement is the schema, because the host, the user and the password
# are the connection and a validation message is a place values end up.
DOMAIN_SOURCE = f"the {DOMAIN_CHAIN.schema} schema of the vinga database"


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
    return _with_domain_half(load_file_config(path))


def reload_domain_config(running: Config) -> BootConfig:
    """The domain half again, for a server that is already up.

    Steps 2 to 5 of the boot above, unchanged and shared rather than
    written a second time: the same database, the same exhaustive
    verification of the stored secrets, the same composition and the
    same whole-snapshot validation, so entry names, references and
    `server.local_only` declarations are judged by the code that judged
    them at startup.

    Step 1 is deliberately not repeated. The file half is this process's
    own, down to the port it is listening on and the directory this
    reads, so a changed file still means a restart; what comes back is
    the stored half composed onto the running server section.

    Synchronous, and blocking in a way that matters: `ConfigStore.load`
    takes the domain chain's advisory lock and waits out the lock
    timeout for it. A caller on the event loop that runs conversations
    runs this in a worker thread.
    """
    return _with_domain_half(FileConfig(server=running.server))


def _with_domain_half(file_half: FileConfig) -> BootConfig:
    settings = file_half.server.database
    engine = open_database(settings)
    try:
        snapshot = ConfigStore(engine, load_keys()).load()
    finally:
        engine.dispose()

    verify_secrets(snapshot.secrets)
    config = compose_config(file_half, domain_fields(snapshot.domain), DOMAIN_SOURCE)
    return BootConfig(config, snapshot.secrets)


__all__ = ["BootConfig", "load_boot_config", "reload_domain_config"]
