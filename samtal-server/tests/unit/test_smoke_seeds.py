"""The smoke lane's seeding scripts, run against a scratch database.

CI runs these from inside the image, before each container starts, which
is the only place they matter and the one place the unit lane cannot
reach. What it can do is run the same scripts against a database of its
own and read back what they wrote, which is what these tests are: each
image check is only a check if the configuration it boots on is the one
it claims, and a local engine creeping into the slim one would make it
pass for the wrong reason.

Running them also pins that they work at all: a fragment the models
refuse, or a write in an order the reference checks refuse, fails here
rather than in the image job.
"""

import subprocess
from pathlib import Path

import pytest

from samtal_server.config.store import ConfigStore, DomainConfig
from samtal_server.db import open_database

SMOKE = Path(__file__).parents[1] / "smoke"

# The two engines the slim image leaves out. `silero` is deliberately
# not one of them: it is a core dependency, in both variants, running on
# every frame whichever ASR is configured.
LOCAL_ENGINES = {"faster_whisper", "piper"}


def seeded(script: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> DomainConfig:
    """What one seeding script writes, read back through the repository."""
    monkeypatch.delenv("SAMTAL_CONFIG", raising=False)
    monkeypatch.setenv("SAMTAL_SERVER__DATABASE__DIR", str(tmp_path / "db"))
    subprocess.run(["sh", str(SMOKE / script)], check=True)

    engine = open_database(tmp_path / "db")
    try:
        return ConfigStore(engine).load().domain
    finally:
        engine.dispose()


def test_the_smoke_conversation_runs_on_mock_providers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No model downloads, no keys, no network: what the lane proves is
    that the image serves a conversation, not which engine speaks."""
    domain = seeded("seed.sh", tmp_path, monkeypatch)
    for stage in ("llm", "asr", "tts", "vad"):
        for name, entry in getattr(domain.providers, stage).items():
            assert entry.type == "mock", f"{stage}.{name} is not a mock provider"
    assert domain.default_agent == "assistant"


def test_the_slim_boot_config_names_no_local_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The slim image's boot check is only a check if its config would
    actually fail on an image without the extras. A local engine
    creeping in here would make it pass for the wrong reason."""
    domain = seeded("seed-slim.sh", tmp_path, monkeypatch)
    for stage in ("asr", "tts", "llm"):
        for name, entry in getattr(domain.providers, stage).items():
            assert entry.type not in LOCAL_ENGINES, f"{stage}.{name} is a local engine"
    # silero is the deliberate exception: a core dependency, in both
    # variants, running on every frame whichever ASR is configured.
    assert domain.providers.vad["silero"].type == "silero"


def test_the_local_engine_config_really_names_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """And the negative check is only a check if its config would boot
    on the default image and fail on slim."""
    domain = seeded("seed-local-engines.sh", tmp_path, monkeypatch)
    assert domain.providers.asr["whisper"].type == "faster_whisper"
