"""The opt-in local lane: pre-flight for real-provider runs.

This lane never runs in CI (the workflow names tests/unit and
tests/integration) and never runs by accident (it skips unless
SAMTAL_LOCAL_LANE=1). Once opted in, missing pieces fail loudly with
the command that fixes them, because a silently skipped acceptance run
checks nothing.
"""

import asyncio
import contextlib
import importlib.util
import json
import os
import urllib.request
from dataclasses import dataclass

import pytest
import uvicorn

from samtal_server.app import create_app
from samtal_server.audio.resample import Resampler
from samtal_server.config import Config

LANE_ENV = "SAMTAL_LOCAL_LANE"
OLLAMA_ENV = "SAMTAL_LOCAL_OLLAMA"
MODEL_ENV = "SAMTAL_LOCAL_LLM_MODEL"

DEFAULT_OLLAMA = "http://localhost:11434/v1"
PREFERRED_MODEL = "qwen3:8b"

# Lines the tests append and pytest_terminal_summary prints, so the run
# reports the conversation it held instead of a bare green dot.
_report: list[str] = []


@pytest.fixture(scope="session")
def conversation_report() -> list[str]:
    return _report


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:
    if _report:
        terminalreporter.write_sep("=", "local lane conversation")
        for line in _report:
            terminalreporter.write_line(line)


@pytest.fixture(scope="session")
def serve():
    """The server runner, as a fixture so tests need no cross-imports:
    `async with serve(config) as port: ...`."""
    return _running


@pytest.fixture(scope="session")
def speak():
    """The Piper synthesizer, likewise: `speak(text, voice, rate)`."""
    return _piper_pcm


@contextlib.asynccontextmanager
async def _running(config: Config):
    """A live server on an ephemeral port. Building the app builds the
    providers, which is where model and voice downloads happen on a first
    run, so starting can take minutes before it takes seconds."""
    server = uvicorn.Server(
        uvicorn.Config(create_app(config), host="127.0.0.1", port=0, log_level="warning")
    )
    task = asyncio.create_task(server.serve())
    while not server.started:
        if task.done():
            task.result()
        await asyncio.sleep(0.01)
    try:
        yield server.servers[0].sockets[0].getsockname()[1]
    finally:
        server.should_exit = True
        await task


def _piper_pcm(text: str, voice: str, sample_rate: int) -> bytes:
    """`text` spoken by a Piper voice, resampled to the device mic rate.
    This is how the lane puts a real spoken question into the pipeline."""
    from piper import PiperVoice

    from samtal_server.providers.piper_tts import DEFAULT_DOWNLOAD_DIR, ensure_voice

    loaded = PiperVoice.load(ensure_voice(voice, DEFAULT_DOWNLOAD_DIR))
    pcm = b"".join(chunk.audio_int16_bytes for chunk in loaded.synthesize(text))
    resampler = Resampler(loaded.config.sample_rate, sample_rate)
    return resampler.process(pcm) + resampler.flush()


@dataclass(frozen=True)
class LocalLane:
    """What the pre-flight resolved: where Ollama is and which model."""

    base_url: str
    model: str


def _installed_models(base_url: str) -> list[str]:
    """The models the local Ollama serves, via its native tags API."""
    tags_url = base_url.removesuffix("/v1") + "/api/tags"
    with urllib.request.urlopen(tags_url, timeout=3) as response:
        return [entry["name"] for entry in json.load(response)["models"]]


def _pick_model(models: list[str], problems: list[str]) -> str | None:
    """The model named by the environment, or the preferred default, or
    whatever is installed; None adds the problem that explains why."""
    named = os.environ.get(MODEL_ENV)
    if named is not None:
        if named in models or f"{named}:latest" in models:
            return named
        problems.append(
            f'{MODEL_ENV} names "{named}", which Ollama does not serve '
            f"(installed: {', '.join(models) or 'none'})"
        )
        return None
    if PREFERRED_MODEL in models:
        return PREFERRED_MODEL
    if models:
        return models[0]
    problems.append(
        f"Ollama serves no models: `ollama pull {PREFERRED_MODEL}` (or any chat "
        f"model, then name it with {MODEL_ENV})"
    )
    return None


@pytest.fixture(scope="session")
def local_lane() -> LocalLane:
    if os.environ.get(LANE_ENV) != "1":
        pytest.skip(f"the local lane is opt-in: run with {LANE_ENV}=1")

    problems: list[str] = []

    missing_extras = [
        extra
        for module, extra in (("faster_whisper", "faster-whisper"), ("piper", "piper"))
        if importlib.util.find_spec(module) is None
    ]
    if missing_extras:
        problems.append(
            "missing extras: install with `uv sync"
            + "".join(f" --extra {extra}" for extra in missing_extras)
            + "`"
        )

    base_url = os.environ.get(OLLAMA_ENV, DEFAULT_OLLAMA)
    model = None
    try:
        models = _installed_models(base_url)
    except OSError:
        problems.append(
            f"no Ollama answering at {base_url}: start it with `ollama serve` "
            f"(or point {OLLAMA_ENV} at an OpenAI-compatible endpoint)"
        )
    else:
        model = _pick_model(models, problems)

    if problems:
        pytest.fail(
            "the local lane cannot run:\n- " + "\n- ".join(problems), pytrace=False
        )
    assert model is not None
    return LocalLane(base_url=base_url, model=model)
