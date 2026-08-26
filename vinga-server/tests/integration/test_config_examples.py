"""The deployment profile, run the way it documents itself.

`config.deploy.example.sh` writes through the configuration API, so it
documents itself as running against a running server: exec into the
container, where the token and loopback are ambient, or point
VINGA_API_URL at the API from outside. This runs the file verbatim
through exactly that second invocation, against a real server on an
ephemeral loopback port, which is why it lives in the integration lane
rather than beside the fragment tests.

What is asserted is more than installability: the profile's values are
measurements from a live deployment (the CPU quota the ASR thread pool
is pinned to, the language ladder, the voice, the allowlist that comes
of naming no default agent), and a measurement nothing reads drifts
silently.
"""

import subprocess
from pathlib import Path

import pytest

from tests.integration.conftest import script_environment
from vinga_server.config import compose_config, load_file_config
from vinga_server.config.models import DatabaseConfig, domain_fields
from vinga_server.config.store import ConfigStore
from vinga_server.db import open_database

SERVER = Path(__file__).resolve().parents[2]
DEPLOY_CONFIG = SERVER / "config.deploy.example.yaml"
DEPLOY_SEED = SERVER / "config.deploy.example.sh"


def test_the_deployment_profile_boots_with_its_measured_values(
    served_api, blank_database: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The deployment profile in both its halves: the file's server keys
    and the script's domain half, composed the way the server composes
    them at boot.

    That the script runs at all is half of it: it is the deployment
    procedure, and a procedure nobody runs is a guess. The other half is
    the values, so a rewrite that quietly loses the CPU-quota pin or the
    language ladder fails here instead of in somebody's deployment."""
    monkeypatch.delenv("VINGA_CONFIG", raising=False)
    # A blank database, which is what a deployment about to run its
    # seeding script has: the script writes the whole domain half, and a
    # row somebody else left would make the read below a read of two
    # deployments at once.
    database = DatabaseConfig(name=blank_database)
    with served_api(database) as api_url:
        subprocess.run(
            ["sh", str(DEPLOY_SEED)],
            check=True,
            env=script_environment(VINGA_API_URL=api_url),
        )

    engine = open_database(database)
    try:
        snapshot = ConfigStore(engine).load()
    finally:
        engine.dispose()
    config = compose_config(
        load_file_config(DEPLOY_CONFIG), domain_fields(snapshot.domain), str(DEPLOY_SEED)
    )

    whisper = config.providers.asr["whisper"]
    assert whisper.type == "faster_whisper"
    assert whisper.options["vad_filter"] is True
    assert whisper.options["language_detect"] == "once"
    # The container CPU quota this deployment runs under. It is the one
    # provider option that has to move with the orchestrator's limit,
    # which is why the profile pins it rather than leaving the engine to
    # read the host's core count.
    assert whisper.options["cpu_threads"] == 3
    assert config.providers.tts["piper"].options["voice"] == "sv_SE-nst-medium"

    # No default_agent: the devices map is an allowlist, and an unknown
    # device resolves to no agent at all.
    assert config.default_agent is None
    assert config.agents_for_device("aa:bb:cc:dd:ee:ff") == ["assistant"]
    assert config.agents_for_device("ff:ff:ff:ff:ff:ff") == []
