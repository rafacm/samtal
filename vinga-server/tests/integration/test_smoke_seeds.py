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

The environment they are handed is the harness's, and one thing in it is
load-bearing beyond any single script: the children these scripts spawn
write no bytecode because `script_environment` says so. The last test
here is about that assignment rather than about a script, and the first
one runs its script with the ambient flag stripped so that the
assignment is what is holding.
"""

import contextlib
import signal
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest

from tests.conftest import throwaway_database
from tests.integration.conftest import BYTECODE_OFF, script_environment
from vinga_server.config.models import DatabaseConfig
from vinga_server.config.store import ConfigStore, DomainConfig
from vinga_server.db import open_database

SMOKE = Path(__file__).resolve().parents[1] / "smoke"

# The two engines the slim image leaves out. `silero` is deliberately
# not one of them: it is a core dependency, in both variants, running on
# every frame whichever ASR is configured.
LOCAL_ENGINES = {"faster_whisper", "piper"}


def _free_port() -> int:
    """A port nothing is listening on, so a test run does not collide
    with a development server on the default one. The script reads it
    from VINGA_SERVER__PORT, and so does the CLI inside it, since both
    resolve server.port through the same settings machinery."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _domain(database: str) -> DomainConfig:
    engine = open_database(DatabaseConfig(name=database))
    try:
        return ConfigStore(engine).load().domain
    finally:
        engine.dispose()


def seeded(script: str, tmp_path: Path, environment: dict[str, str] | None = None) -> DomainConfig:
    """What one seeding script writes, read back through the repository.

    The script is run verbatim, as CI runs it: it starts its own server,
    waits for it, writes through the API, and stops it again.
    """
    # A database of this run's own, which is what the throwaway
    # directory used to be: a seeding script starts a server, and a
    # server that booted on a store somebody else had already written
    # is a different scenario from the empty first start these are
    # about. A test that seeds twice gets two.
    with throwaway_database() as database:
        inherited = script_environment(
            without=["VINGA_CONFIG"],
            VINGA_SERVER__PORT=str(_free_port()),
            VINGA_DB_NAME=database,
            **(environment or {}),
        )
        subprocess.run(["sh", str(SMOKE / script)], check=True, env=inherited, timeout=180)

        return _domain(database)


@pytest.fixture
def no_ambient_bytecode_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this test's script with PYTHONDONTWRITEBYTECODE not already in
    the environment, so the harness's own assignment is what stops the
    subprocesses writing caches.

    CI exports the variable for the whole job
    (`.github/workflows/vinga-server.yml`), which is right for
    everything that is not pytest and would make the lane's guard
    vacuous there: a harness that stopped setting the flag would still
    hand every child an environment carrying it, and the finalizer would
    find nothing to catch. Stripping it before the script runs is what
    makes the guard bite on a runner as well as on a laptop.

    Deleting it from this process is enough, because
    `script_environment` builds the child's environment by copying
    `os.environ`. It does not make this process write bytecode:
    `tests/conftest.py` sets `sys.dont_write_bytecode` rather than
    relying on the variable.

    One test is enough. The point is not to run the scripts in an
    unusual environment, it is to have somewhere the assignment is
    load-bearing, and seeding is seven CLI calls plus a server start, so
    the caches would be plentiful and immediate.
    """
    monkeypatch.delenv(BYTECODE_OFF, raising=False)


def test_the_smoke_conversation_runs_on_mock_providers(
    no_ambient_bytecode_flag: None, tmp_path: Path
) -> None:
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
    """An VINGA_API_URL left over in a shell, or set in a CI job for
    another deployment, would otherwise take the writes and the bearer
    token with them while the server the script started stayed empty.
    The decoy here is a real server on a real database, so "it was
    ignored" is checked by looking at what the decoy holds rather than by
    the script merely not failing."""
    with throwaway_database() as decoy:
        with served_api(DatabaseConfig(name=decoy)) as decoy_url:
            domain = seeded(script, tmp_path, {"VINGA_API_URL": decoy_url})

        assert domain.default_agent == "assistant"
        assert _domain(decoy).agents == {}
        assert _domain(decoy).default_agent is None


def test_an_interrupted_seeding_fails_and_leaves_no_server_behind(
    tmp_path: Path,
) -> None:
    """A seeding step that was interrupted must not look like one that
    finished, and must not leave the server it started running: CI would
    then hold a port and a data volume open for the container that comes
    next."""
    port = _free_port()
    stack = contextlib.ExitStack()
    database = stack.enter_context(throwaway_database())
    environment = script_environment(
        without=["VINGA_CONFIG"],
        VINGA_SERVER__PORT=str(port),
        VINGA_DB_NAME=database,
    )

    seeding = subprocess.Popen(
        ["sh", str(SMOKE / "seed.sh")],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        # Its own process group, so the signal reaches the script the way
        # a shell would send it and not this test runner as well.
        start_new_session=True,
    )
    try:
        _wait_for(lambda: _ready(port), "the seeding server never became ready")
        seeding.send_signal(signal.SIGINT)
        _, errors = seeding.communicate(timeout=60)
    finally:
        if seeding.poll() is None:  # pragma: no cover, only on a failure
            seeding.kill()
            seeding.communicate(timeout=30)
        # The database this script's server was on, taken away only once
        # nothing is connected to it.
        stack.close()

    assert seeding.returncode != 0
    assert "interrupted" in errors
    # And the server it started is gone with it.
    _wait_for(lambda: not _listening(port), "the seeding server outlived the script")


def _ready(port: int) -> bool:
    """The server ready, which is when the script starts writing: an
    interrupt landing before that would prove less than this needs to.

    The same probe the script itself waits on, so this waits for the
    moment the script is waiting for rather than an earlier one.
    """
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/readyz", timeout=1):
            return True
    except OSError:
        return False


def _listening(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(1)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def _wait_for(ready, complaint: str, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if ready():
            return
        time.sleep(0.1)
    raise AssertionError(complaint)


def test_a_seeding_script_reports_a_server_that_will_not_start(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    """The failure the polling loop exists for. Without the API token the
    server refuses to boot, which is what a deployment that skipped the
    upgrade note meets, and a seeding script that hung on it would say
    nothing at all."""
    environment = script_environment(
        without=["VINGA_CONFIG", "VINGA_API_SECRET"],
        VINGA_SERVER__PORT=str(_free_port()),
    )

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
    assert "VINGA_API_SECRET" in finished.stderr


def test_a_script_environment_writes_no_bytecode_whatever_it_is_asked_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The assignment on its own, with no script and no subprocess.

    Every way the flag could come out of the helper wrong, in one place
    and in a second rather than in a minute: absent from this process,
    set to something falsy here, or overridden by a caller that names
    the variable itself. Overrides are applied before the assignment
    precisely so the last of those cannot happen, and `without` is
    checked too, since a caller stripping the variable is asking for the
    environment it names rather than for bytecode.

    What CPython reads is whether the value is a non-empty string, so
    "0" would stop the writes and an empty one would not stop anything:
    the empty case is the real hole of the three, and the other two are
    here because the reader cannot be expected to know which is which.
    The helper answers "1" to all of them, which is the one spelling
    every reader of this environment already understands.
    """
    monkeypatch.delenv(BYTECODE_OFF, raising=False)
    assert script_environment()[BYTECODE_OFF] == "1"
    assert script_environment(**{BYTECODE_OFF: "0"})[BYTECODE_OFF] == "1"
    assert script_environment(**{BYTECODE_OFF: ""})[BYTECODE_OFF] == "1"
    assert script_environment(without=[BYTECODE_OFF])[BYTECODE_OFF] == "1"

    monkeypatch.setenv(BYTECODE_OFF, "0")
    assert script_environment()[BYTECODE_OFF] == "1"
