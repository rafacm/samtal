"""The smoke lane's seeding scripts, run against a scratch database.

CI runs these from inside the image, before each container starts, which
is the only place they matter and the one place a test lane cannot
reach. What it can do is run the same scripts against a database of its
own and read back what they wrote, which is what these tests are: each
image check is only a check if the configuration it boots on is the one
it claims, and a local engine creeping into the slim one would make it
pass for the wrong reason.

Running them also pins that they work at all: a fragment the models
refuse, or a write in an order the reference checks refuse, fails here
rather than in the image job.

They live in the integration lane because each script now starts a
server of its own to write through, polls it, and stops it: that
lifecycle is a large part of what is under test, so the scripts are run
exactly as CI runs them, unmodified, with no fixture serving them.
"""

import os
import socket
import subprocess
from pathlib import Path

import pytest

from samtal_server.config.store import ConfigStore, DomainConfig
from samtal_server.db import open_database

SMOKE = Path(__file__).resolve().parents[1] / "smoke"

# The two engines the slim image leaves out. `silero` is deliberately
# not one of them: it is a core dependency, in both variants, running on
# every frame whichever ASR is configured.
LOCAL_ENGINES = {"faster_whisper", "piper"}


def _free_port() -> int:
    """A port nothing is listening on, so a test run does not collide
    with a development server on the default one. The script reads it
    from SAMTAL_SERVER__PORT, and so does the CLI inside it, since both
    resolve server.port through the same settings machinery."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _domain(directory: Path) -> DomainConfig:
    engine = open_database(directory)
    try:
        return ConfigStore(engine).load().domain
    finally:
        engine.dispose()


def seeded(script: str, tmp_path: Path, environment: dict[str, str] | None = None) -> DomainConfig:
    """What one seeding script writes, read back through the repository.

    The script is run verbatim, as CI runs it: it starts its own server,
    waits for it, writes through the API, and stops it again.
    """
    directory = tmp_path / "db"
    inherited = {
        key: value for key, value in os.environ.items() if key != "SAMTAL_CONFIG"
    }
    inherited["SAMTAL_SERVER__DATABASE__DIR"] = str(directory)
    inherited["SAMTAL_SERVER__PORT"] = str(_free_port())
    inherited.update(environment or {})
    subprocess.run(["sh", str(SMOKE / script)], check=True, env=inherited, timeout=180)

    return _domain(directory)


def test_the_smoke_conversation_runs_on_mock_providers(tmp_path: Path) -> None:
    """No model downloads, no keys, no network: what the lane proves is
    that the image serves a conversation, not which engine speaks."""
    domain = seeded("seed.sh", tmp_path)
    for stage in ("llm", "asr", "tts", "vad"):
        for name, entry in getattr(domain.providers, stage).items():
            assert entry.type == "mock", f"{stage}.{name} is not a mock provider"
    assert domain.default_agent == "assistant"


def test_the_slim_boot_config_names_no_local_engine(tmp_path: Path) -> None:
    """The slim image's boot check is only a check if its config would
    actually fail on an image without the extras. A local engine
    creeping in here would make it pass for the wrong reason."""
    domain = seeded("seed-slim.sh", tmp_path)
    for stage in ("asr", "tts", "llm"):
        for name, entry in getattr(domain.providers, stage).items():
            assert entry.type not in LOCAL_ENGINES, f"{stage}.{name} is a local engine"
    # silero is the deliberate exception: a core dependency, in both
    # variants, running on every frame whichever ASR is configured.
    assert domain.providers.vad["silero"].type == "silero"


def test_the_local_engine_config_really_names_one(tmp_path: Path) -> None:
    """And the negative check is only a check if its config would boot
    on the default image and fail on slim."""
    domain = seeded("seed-local-engines.sh", tmp_path)
    assert domain.providers.asr["whisper"].type == "faster_whisper"


@pytest.mark.parametrize(
    "script", ["seed.sh", "seed-slim.sh", "seed-local-engines.sh"]
)
def test_a_seed_ignores_an_ambient_api_url(
    served_api, tmp_path: Path, script: str
) -> None:
    """An SAMTAL_API_URL left over in a shell, or set in a CI job for
    another deployment, would otherwise take the writes and the bearer
    token with them while the server the script started stayed empty.
    The decoy here is a real server on a real database, so "it was
    ignored" is checked by looking at what the decoy holds rather than by
    the script merely not failing."""
    decoy = tmp_path / "decoy"
    with served_api(decoy) as decoy_url:
        domain = seeded(script, tmp_path, {"SAMTAL_API_URL": decoy_url})

    assert domain.default_agent == "assistant"
    assert _domain(decoy).agents == {}
    assert _domain(decoy).default_agent is None


def test_a_seeding_script_reports_a_server_that_will_not_start(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    """The failure the polling loop exists for. Without the API token the
    server refuses to boot, which is what a deployment that skipped the
    upgrade note meets, and a seeding script that hung on it would say
    nothing at all."""
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in ("SAMTAL_CONFIG", "SAMTAL_API_SECRET")
    }
    environment["SAMTAL_SERVER__DATABASE__DIR"] = str(tmp_path / "db")
    environment["SAMTAL_SERVER__PORT"] = str(_free_port())

    finished = subprocess.run(
        ["sh", str(SMOKE / "seed.sh")],
        env=environment,
        capture_output=True,
        text=True,
        timeout=180,
    )

    assert finished.returncode != 0
    assert "exited before it was ready" in finished.stderr
    # And the server's own log is what says why, which is the whole point
    # of keeping it.
    assert "SAMTAL_API_SECRET" in finished.stderr
