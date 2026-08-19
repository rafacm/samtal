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

from vinga_server.config.store import ConfigStore
from vinga_server.db import open_database

STAGES = ("llm", "asr", "tts", "vad")

# Not a credential: a fixed string shaped like one, and shaped so a
# substring hunt for it cannot match by accident. It rides in on the
# `__cause__` of the refusal, which is where a real one would be.
SENTINEL = "sk-live-2f8c41d7-never-a-real-credential"

# What the registry composes when a provider factory raises something of
# its own: the entry, the type and the exception's class, and nothing the
# library said. Matched as a fragment, since counting a distinctive one
# is what "said once" means here.
PROVIDER_SENTENCE = "providers.llm.mock: the mock provider would not build (ValueError)"

# The real entry point, with one provider factory raising the way a
# third-party client does when it cannot start: a message quoting the
# endpoint and the key it was handed. The factory is replaced rather than
# the whole build, so what composes the operator's sentence is the
# registry's own wrapper, which is the thing under test.
PROVIDER_REFUSAL = f"""
import sys

from vinga_server.providers import mock


def refuse(label, config):
    raise ValueError("POST https://api.example/v1/chat failed for key {SENTINEL}")


mock.build_llm = refuse

import vinga_server.main as main

sys.argv = ["vinga-server"]
main.main()
"""


# A boot that reads its domain half and nothing else: what refuses is in
# the database this seeds.
PLAIN_ENTRYPOINT = """
import sys

import vinga_server.main as main

sys.argv = ["vinga-server"]
main.main()
"""

# An environment variable nothing sets, named so that nothing else can
# have set it either.
UNSET_VARIABLE = "VINGA_STARTUP_FAILURE_TEST_TOKEN"

MCP_SENTENCE = (
    f"mcp_servers.tools: mcp_servers.tools.env.API_TOKEN: references ${UNSET_VARIABLE}, "
    f"but it is not set in the environment"
)


def seed_domain(directory: Path, entry: dict[str, object] | None = None) -> None:
    """A database holding one agent on the mock providers, and one MCP
    server for it to reach when the caller wants one.

    The domain half of a configuration lives in the database and the
    entry point reads it there, so a boot that has to get as far as
    building something needs one written where a deployment writes it.
    Only a referenced MCP entry is built at boot, which is why the agent
    names it.
    """
    agent: dict[str, object] = {"prompt": "A"}
    if entry is not None:
        agent["mcp"] = ["tools"]
    engine = open_database(directory)
    try:
        store = ConfigStore(engine)
        for stage in STAGES:
            store.set_provider(stage, "mock", {"type": "mock"})
        store.set_agent_defaults(dict.fromkeys(STAGES, "mock"))
        if entry is not None:
            store.set_mcp_server("tools", entry)
        store.set_agent("assistant", agent)
        store.set_default_agent("assistant")
    finally:
        engine.dispose()


def run_entrypoint(script: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    """One server process, on a configuration whose startup refuses.

    Run from a directory of its own so no `.env` beside the repository
    reaches it, and with bytecode writing off, which is this
    repository's rule for anything outside pytest.
    """
    environment = dict(os.environ)
    environment["VINGA_SERVER__DATABASE__DIR"] = str(tmp_path / "db")
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment.pop(UNSET_VARIABLE, None)
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
    seed_domain(tmp_path / "db")

    refused(run_entrypoint(PROVIDER_REFUSAL, tmp_path), PROVIDER_SENTENCE)


def test_a_refused_mcp_entry_says_one_sentence_and_exits_one(tmp_path: Path) -> None:
    """An MCP entry naming an environment variable nothing sets is a
    boot refusal like a bad provider, and reached the operator as a bug
    until it was classified as one (`McpConfigError` is a `ValueError`,
    so nothing in the taxonomy caught it): uvicorn's traceback, and exit
    code 3 rather than 1. The message was written to be read as it is,
    and now it is."""
    entry: dict[str, object] = {
        "transport": "stdio",
        "command": "/usr/bin/true",
        "env": {"API_TOKEN": f"${UNSET_VARIABLE}"},
    }
    seed_domain(tmp_path / "db", entry)

    refused(run_entrypoint(PLAIN_ENTRYPOINT, tmp_path), MCP_SENTENCE)


def test_nothing_a_provider_library_said_reaches_either_stream(tmp_path: Path) -> None:
    """Two guards, and the sentinel goes past both or neither.

    The registry composes the sentence rather than copying the library's,
    so what is printed cannot hold what the library was configured with;
    and the bridge raises its replacement outside the `except` that
    caught the refusal, so there is no `__cause__` or `__context__` for a
    renderer to walk into where one runs.
    """
    seed_domain(tmp_path / "db")

    written = refused(run_entrypoint(PROVIDER_REFUSAL, tmp_path), PROVIDER_SENTENCE)

    assert SENTINEL not in written, written
    assert "api.example" not in written, written
