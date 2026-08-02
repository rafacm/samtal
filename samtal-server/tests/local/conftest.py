"""The opt-in local lane: pre-flight for real-provider runs.

This lane never runs in CI (the workflow names tests/unit and
tests/integration) and never runs by accident (it skips unless
SAMTAL_LOCAL_LANE=1). Once opted in, missing pieces fail loudly with
the command that fixes them, because a silently skipped acceptance run
checks nothing.
"""

import importlib.util
import json
import os
import urllib.request
from dataclasses import dataclass

import pytest

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
