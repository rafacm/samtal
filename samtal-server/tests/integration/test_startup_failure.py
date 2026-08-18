"""A boot that refuses, through the entry point a deployment runs.

Construction is the lifespan's since #142, which puts it inside uvicorn
rather than in front of it. Three things about that are only true in a
real process: uvicorn renders a lifespan exception by formatting its
whole traceback into a log line, it ends the process itself when a
lifespan startup fails, and the exit code is set by `main()` after
`serve()` has returned. None of them can be checked from a test that
builds an application object and enters it by hand, so this one runs the
real entry point in a process of its own and reads what came out.

What must come out is one sanitized sentence and an exit code of 1,
exactly what an operator read when the same failure happened in front of
`serve()`. One sentence, and nothing around it: no traceback, no frames
from this application or from anything under it, and not the name of the
exception class the bridge uses to carry the refusal out. That is the
whole assertion in `refused`, and every refusal below is held to it.

What must not come out is anything the failure was chained from: a
provider failure this deep carries a driver's or a client library's own
exception, and those quote the URL or the credential they were
configured with.
"""

import os
import subprocess
import sys
from pathlib import Path

# Not a credential: a fixed string shaped like one, and shaped so a
# substring hunt for it cannot match by accident. It rides in on the
# `__cause__` of the refusal, which is where a real one would be.
SENTINEL = "sk-live-2f8c41d7-never-a-real-credential"

SENTENCE = "agents.assistant: the llm provider 'openai' could not be built"

# The real entry point, with the provider build refusing the way a
# misconfigured deployment's does: a sanitized sentence of its own, and a
# cause that carries what the library was configured with.
PROVIDER_REFUSAL = f"""
import sys

from samtal_server import app as app_module
from samtal_server.providers import ProviderError


def refuse(*args, **kwargs):
    try:
        raise ValueError(
            "POST https://api.example/v1/chat failed for key {SENTINEL}"
        )
    except ValueError as cause:
        raise ProviderError({SENTENCE!r}) from cause


app_module.build_agent_providers = refuse

import samtal_server.main as main

sys.argv = ["samtal-server"]
main.main()
"""


def run_entrypoint(script: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    """One server process, on a configuration whose startup refuses.

    Run from a directory of its own so no `.env` beside the repository
    reaches it, and with bytecode writing off, which is this
    repository's rule for anything outside pytest.
    """
    environment = dict(os.environ)
    environment["SAMTAL_SERVER__DATABASE__DIR"] = str(tmp_path / "db")
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def refused(finished: subprocess.CompletedProcess[str], sentence: str) -> str:
    """What a refused boot is allowed to have written, asserted, and the
    combined output for a caller with more to check.

    The sentence once, on either stream, and no rendering of the
    exception that carried it: `Traceback`, a frame line, or the class
    name would each mean the CLI had gone back to answering a
    configuration mistake with a stack.
    """
    written = finished.stdout + finished.stderr

    assert finished.returncode == 1, written
    assert written.count(sentence) == 1, written
    assert "Traceback (most recent call last)" not in written, written
    assert 'File "' not in written, written
    assert "StartupFailed" not in written, written
    return written


def test_a_refused_provider_build_says_one_sentence_and_exits_one(tmp_path: Path) -> None:
    refused(run_entrypoint(PROVIDER_REFUSAL, tmp_path), SENTENCE)


def test_nothing_the_refusal_was_chained_from_reaches_either_stream(
    tmp_path: Path,
) -> None:
    """The whole point of raising the replacement outside the `except`
    that caught the original: with no `__cause__` and no `__context__`,
    there is nothing for a renderer to walk into even where one runs."""
    written = refused(run_entrypoint(PROVIDER_REFUSAL, tmp_path), SENTENCE)

    assert SENTINEL not in written, written
    assert "api.example" not in written, written
