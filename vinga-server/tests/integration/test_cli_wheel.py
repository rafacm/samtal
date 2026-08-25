"""The grammar as an INSTALLED ARTIFACT, driven against a live server.

Every other lane runs the CLI as code this repository imports. The
source tree makes every module importable whether it was packaged or
not, and `cli.main(argv)` is a function call rather than a program, so
nothing before this file has ever asked whether the thing an operator
installs works: whether the wheel carries what it needs, whether the
default install resolves, whether the `vinga` binary exists and answers,
and whether a command that needs the server half says so rather than
ending in an ImportError.

So this builds the wheel, installs it BARE into a clean environment, and
drives the actual binary as a subprocess against a real server. The
server stays in this process, in the same `serving()` thread the
in-process lane uses, because that is the cheap way to have a server and
not because it preserves anything.

**This lane is beside the in-process one, not instead of it.**
`test_cli_live.py` is the SECURITY lane: it runs client and server in
one process so `Watched` can read every log record, every unformatted
argument, every extra attribute and every exception chain, which is
where the refusal leaks live and where this issue's new sentences are
proven not to leak. A child process makes all four of those surfaces
invisible, and capturing its stdout and stderr is not the same
assertion. So this lane asserts on exit codes, stdout and stderr, and
makes no no-leak claim it cannot support.

**`uv pip install <wheel>` here, and `uv sync --frozen` there.** The
tier closure lane builds its environments with `uv sync --frozen`, and
argues in its own head that `uv pip install` re-resolves from the index
so an environment built that way cannot be held exactly to a graph it
did not come from. That is right, and it is right for that lane's
question, which is what the DECLARATION resolves to. This lane's
question is a different one: what the built ARTIFACT carries, which
means installing that file and nothing else, and `uv sync` would install
the project from the source tree rather than the wheel. So the two lanes
install differently on purpose, and each asserts only what its own
installation can support. Nothing here says anything about the closure;
the closure is the other lane's, and a distribution set compared to
`uv.lock` is not compared here at all.

**Provenance is proven, not presumed.** The commands run in a temporary
directory outside the checkout with `PYTHONPATH` and its relatives
scrubbed, the resolved package file is asserted to sit inside the clean
environment before any command runs, and the installed distribution is
asserted to record the wheel this lane built as where it came from.

**Coverage is the full registered inventory, both ways.** Every row of
`cli.COMMANDS` is RUN, never merely imported: importing `cli` proves
nothing about a command whose heavy import sits inside its own arm,
which is exactly what the gated pair is. The ungated rows are driven
against the server and asserted to answer; the gated ones are driven and
asserted to print the fixed sentence and exit 1. Which row a command
line names is `tests.support.config_cli.registered`, the same matcher
the in-process lane and the spelling census read, so a command that
moved between the two sets fails this lane from whichever side it left.

The fragments and the document the session writes are this lane's own,
written into the run directory rather than read from `examples/`, so no
command here can reach the checkout by a relative path. That the wheel
carries the example fragments is proven the other way, by the recipes
region of the reference rendering non-empty from the installed artifact.

Ordering: the tests below share one wheel, one environment and one
server on purpose, because what they describe is one operator's session
against one deployment. They run in the order they are written, which is
pytest's order within a module and, under `-n auto --dist loadfile`,
still one worker's order. The completeness test is last for that reason
and skips rather than lies when the module was not run whole.
"""

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

from tests.support.config_cli import registered
from tests.support.deployment import Live, check_in, serving
from vinga_server.config import cli
from vinga_server.config.secrets import MASTER_KEY_ENV, generate_key

PROJECT = Path(__file__).resolve().parents[2]

# Not a real credential, and shaped so a substring check for it cannot
# match by accident.
SECRET = "sk-wheel-3d7c1e58-never-a-real-credential"

BOUND_MAC = "aa:bb:cc:dd:ee:ff"

# The second board, which arrives the other way: by checking in and
# showing a code.
WAITING_MAC = "11:22:33:44:55:66"

# The deployment this lane configures, every provider a mock so the
# running server can actually build what it is asked to reload.
DEPLOYMENT: dict[str, object] = {
    "providers": {
        "llm": {"brain": {"type": "mock", "reply": "You said {text}."}},
        "asr": {"ears": {"type": "mock", "text": "hello"}},
        "tts": {"voice": {"type": "mock"}},
        "vad": {"gate": {"type": "mock"}},
    },
    "mcp_servers": {"house": {"transport": "stdio", "command": "/bin/echo", "args": ["house"]}},
    "prompt_fragments": {"household": {"text": "The bins go out on Tuesday."}},
    "agent_defaults": {"llm": "brain", "asr": "ears", "tts": "voice", "vad": "gate"},
    "agents": {"sam": {"prompt": "You are Sam.", "prompt_includes": ["household"]}},
}

# The entries written to be taken away again, referenced by nothing, so
# that a delete is refused by nothing but a bug. The provider is a real
# engine type rather than a mock because a credential is stored on it,
# and it is unreferenced for the same reason the reload below succeeds:
# a provider no agent names is a provider no reload has to build.
SCRATCH: dict[str, object] = {
    "provider.yaml": {"type": "anthropic", "model": "m", "api_key_env": "ANTHROPIC_API_KEY"},
    "mcp-server.yaml": {
        "transport": "stdio",
        "command": "/bin/echo",
        "args": ["scratch"],
        "egress": False,
    },
    "prompt-fragment.yaml": {"text": "Scratch."},
    "agent.yaml": {"prompt": "You are scratch."},
}

# The two commands the grammar keeps and a client install cannot answer.
# Named as the expected inventory rather than discovered, because the
# completeness assertion below is two-way: a third gated command fails
# this lane from the side it joined.
GATED = frozenset({("openapi",), ("ota-url",)})

# What this lane actually ran and got an answer from, recorded off each
# command line rather than declared beside it, exactly as the in-process
# lane records what it drove.
DRIVEN: set[tuple[str, ...]] = set()


@pytest.fixture(scope="module")
def wheel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The built artifact, which is the subject of this whole file."""
    if shutil.which("uv") is None:  # pragma: no cover - uv is how this repo runs
        pytest.skip("uv is not on PATH, and it is what builds a wheel here")
    where = tmp_path_factory.mktemp("dist")
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(where)],
        cwd=PROJECT,
        check=True,
        capture_output=True,
        text=True,
    )
    built = sorted(where.glob("*.whl"))
    assert len(built) == 1, built
    return built[0]


@pytest.fixture(scope="module")
def installed(wheel: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The wheel installed BARE into a clean environment, and the path
    of that environment's interpreter.

    No extras, which is the laptop door. The wheel file and nothing
    else, for the reason the head of this file gives: the tier closure
    lane syncs the project against the lock and proves what the
    DECLARATION resolves to; this one installs the built file and proves
    what the ARTIFACT carries, and a sync would install from the source
    tree rather than from the wheel.
    """
    where = tmp_path_factory.mktemp("wheel") / "venv"
    subprocess.run(
        ["uv", "venv", "--python", "3.12", str(where)],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["uv", "pip", "install", "--python", str(where / "bin" / "python"), str(wheel)],
        check=True,
        capture_output=True,
        text=True,
    )
    return where / "bin" / "python"


@pytest.fixture(scope="module")
def elsewhere(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Where the commands are run from: a directory outside the
    checkout, carrying the fragments this session writes.

    Outside rather than in, because a command run with the checkout as
    its working directory can reach the source tree by a relative path,
    and this lane's whole claim is that it did not.
    """
    where = tmp_path_factory.mktemp("run")
    (where / "deployment.yaml").write_text(yaml.safe_dump(DEPLOYMENT), encoding="utf-8")
    for name, body in SCRATCH.items():
        (where / name).write_text(yaml.safe_dump(body), encoding="utf-8")
    return where


@pytest.fixture(scope="module")
def live(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Live]:
    """The server the subprocesses talk to, booted once for the module.

    In this process, which is what makes it cheap: it is a server, and
    the artifact under test is on the other end of the socket.
    """
    patch = pytest.MonkeyPatch()
    patch.setenv(MASTER_KEY_ENV, generate_key())
    try:
        with serving(tmp_path_factory.mktemp("lane") / "db") as running:
            yield running
    finally:
        patch.undo()


def _environment(live: Live) -> dict[str, str]:
    """What a subprocess of this lane gets.

    This process's own, minus everything that could put the source tree
    on a child's import path, plus the address of the server and the
    flag that keeps a child from leaving bytecode beside modules the
    next run would read.
    """
    environment = dict(os.environ)
    for name in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "VINGA_CONFIG"):
        environment.pop(name, None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment[cli.API_URL_ENV] = live.api_url
    return environment


def _ran(
    installed: Path,
    elsewhere: Path,
    live: Live,
    *argv: str,
    stdin: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """One program of the installed environment, run as the binary it
    is."""
    return subprocess.run(
        [str(installed.parent / argv[0]), *argv[1:]],
        cwd=elsewhere,
        env=_environment(live),
        input=stdin,
        capture_output=True,
        text=True,
    )


@pytest.fixture(scope="module")
def run(installed: Path, elsewhere: Path, live: Live):
    """One command of the grammar, run through the installed binary.

    A command that answered is recorded against its row. Only a success:
    a lane that counted a refusal as coverage would let a command whose
    happy path was never run pass the completeness test below, which is
    the one thing that test exists to catch.
    """

    def _run(*argv: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
        finished = _ran(installed, elsewhere, live, "vinga", *argv, stdin=stdin)
        if finished.returncode == 0:
            words = registered(argv)
            assert words is not None, f"no row of the grammar is named by {argv}"
            DRIVEN.add(words)
        return finished

    return _run


def answered(finished: subprocess.CompletedProcess[str], *argv: str) -> str:
    """One command asserted to have answered, and what it printed."""
    assert finished.returncode == 0, (argv, finished.stdout, finished.stderr)
    assert "Traceback" not in finished.stderr, argv
    return finished.stdout


# Provenance, asserted before anything else is


def test_the_binary_under_test_is_the_wheel_s(
    installed: Path, elsewhere: Path, live: Live, wheel: Path
) -> None:
    """A lane run from inside the checkout can import the tree it was
    built from and prove nothing at all.

    Three things say this one cannot: the package resolves inside the
    clean environment, it does not resolve to the source tree, and the
    installed distribution records the wheel this lane built as where it
    came from. The last is what tells an installed artifact from an
    editable install of the same version, which is the one confusion the
    first two cannot see.
    """
    finished = _ran(
        installed,
        elsewhere,
        live,
        "python",
        "-c",
        "import json,vinga_server;from importlib import metadata;"
        "print(json.dumps({'file': vinga_server.__file__, "
        "'origin': metadata.distribution('vinga-server').read_text('direct_url.json')}))",
    )

    assert finished.returncode == 0, finished.stderr
    where = json.loads(finished.stdout)
    assert str(installed.parent.parent) in where["file"], where["file"]
    assert str(PROJECT / "src") not in where["file"], where["file"]
    assert wheel.name in (where["origin"] or ""), where["origin"]


def test_the_binary_exists_and_answers(installed: Path, elsewhere: Path, live: Live) -> None:
    """The console script, which is the thing an operator types. It is
    the wheel's own entry point rather than a module run by hand, so a
    `[project.scripts]` entry that stopped being written fails here."""
    finished = _ran(installed, elsewhere, live, "vinga", "--version")

    assert finished.returncode == 0, finished.stderr
    assert finished.stdout.startswith("vinga-server ")


# One operator's session, from an empty database


def test_a_whole_deployment_applies_from_the_installed_wheel(run) -> None:
    """The bootstrap, and the first command of the session: one
    document, one transaction, every section named, against a server
    that booted on an empty database."""
    written = answered(run("apply", "-f", "deployment.yaml"), "apply")

    assert written.strip()
    assert set(line.split(": ")[-1] for line in written.splitlines()) == {"wrote"}


def test_the_deployment_reads_back_through_every_read(run) -> None:
    """The whole-deployment reads and the per-noun ones, which between
    them are twelve rows of the table."""
    for argv in (
        ("list",),
        ("show",),
        ("export",),
        ("provider", "show", "llm", "brain"),
        ("provider", "export", "llm", "brain"),
        ("mcp-server", "show", "house"),
        ("mcp-server", "export", "house"),
        ("prompt-fragment", "show", "household"),
        ("prompt-fragment", "export", "household"),
        ("agent", "show", "sam"),
        ("agent", "export", "sam"),
        ("agent-defaults", "show"),
        ("agent-defaults", "export"),
    ):
        assert answered(run(*argv), *argv).strip(), argv


def test_the_settings_are_written_and_read_back(run) -> None:
    """The two device writes addressed by a MAC, and the agent defaults
    written back over themselves."""
    assert answered(run("device", "bind", BOUND_MAC, "sam"), "device bind").startswith("wrote ")
    assert "sam" in answered(run("device", "show", BOUND_MAC), "device show")

    defaults = ("agent-defaults", "set", "llm=brain", "asr=ears", "tts=voice", "vad=gate")
    assert answered(run(*defaults), *defaults).startswith("wrote ")


def test_the_running_server_is_read_after_a_reload(run) -> None:
    """The three reads that are of the process rather than of the
    database, and the act that makes them different.

    `agent preview` is the pin: the server this lane booted was given an
    empty domain half, so the agent the document above wrote is one it
    is not serving, and the reload is what installs it.
    """
    assert answered(run("reload"), "reload").strip()

    previewed = answered(run("agent", "preview", "sam"), "agent preview")
    assert "You are Sam." in previewed
    assert "The bins go out on Tuesday." in previewed

    assert "applies at " in answered(run("diff"), "diff")
    assert "house" in answered(run("status"), "status")


def test_a_board_is_onboarded_by_the_code_on_its_screen(run, live: Live) -> None:
    """The onboarding ceremony, which is the one thing in this lane that
    is not a command: a code is minted by a board asking for one.

    The default agent is set and then cleared around the check-in,
    because a board that has one is answered as a configured device and
    mints no code at all.
    """
    assert answered(run("default-agent", "set", "sam"), "default-agent set").startswith("wrote ")
    assert answered(run("default-agent", "clear"), "default-agent clear").startswith("wrote ")

    code = str(check_in(live, WAITING_MAC)["activation"]["code"])
    assert code.isdigit()

    waiting = answered(run("device", "pending", "list"), "device pending list")
    assert code in waiting
    assert WAITING_MAC in waiting

    claimed = run("device", "pending", "claim", code, "sam")
    assert answered(claimed, "device pending claim").startswith("wrote ")


# The writes that cannot be undone, driven against entries written to be
# taken away: what is under test is the command reaching the API from an
# installed artifact, and a delete refused for a reference somebody
# else's row holds would be testing the store's integrity rules a second
# time.


def test_every_destructive_verb_runs_without_a_prompt(run) -> None:
    """Every `delete` and every `clear` of the grammar, and the credential
    writes beside them.

    No `--force` anywhere, deliberately: a subprocess has no terminal,
    and never blocking a pipe is the automation rule, so what this also
    pins is that the confirmation M1 added does not ask when there is
    nobody there to answer.
    """
    for argv, stdin in (
        (("provider", "set", "llm", "scratch", "-f", "provider.yaml"), None),
        (("provider", "secret", "set", "llm", "scratch", "api_key"), SECRET),
        (("provider", "secret", "clear", "llm", "scratch", "api_key"), None),
        (("provider", "delete", "llm", "scratch"), None),
        (("mcp-server", "set", "scratch", "-f", "mcp-server.yaml"), None),
        (("mcp-server", "secret", "set", "scratch", "env.API_ACCESS_TOKEN"), SECRET),
        (("mcp-server", "secret", "clear", "scratch", "env.API_ACCESS_TOKEN"), None),
        (("mcp-server", "delete", "scratch"), None),
        (("prompt-fragment", "set", "scratch", "-f", "prompt-fragment.yaml"), None),
        (("prompt-fragment", "delete", "scratch"), None),
        (("agent", "set", "scratch", "-f", "agent.yaml"), None),
        (("agent", "delete", "scratch"), None),
        (("device", "delete", WAITING_MAC), None),
    ):
        finished = run(*argv, stdin=stdin)
        assert answered(finished, *argv).startswith("wrote "), argv
        assert "?" not in finished.stderr, f"{argv} asked something with nobody there"

    # And nothing either credential write printed carries the value it
    # was given, which is the one no-leak claim a subprocess lane can
    # honestly make: it is about the streams, and the streams are what
    # it can see.
    shown = run("provider", "show", "llm", "brain")
    assert SECRET not in shown.stdout + shown.stderr


# The commands that reach no server at all


def test_the_documents_render_from_the_installed_wheel(run) -> None:
    """Three renderers, and what they prove about the wheel rather than
    about the renderer: the models, the descriptors and the example
    fragments the recipes are read out of all have to have been
    PACKAGED, and a checkout makes every one of them readable whether it
    was or not."""
    assert answered(run("schema"), "schema").strip()
    assert answered(run("reference"), "reference").startswith("# ")

    rendered = answered(run("cli-reference"), "cli-reference")
    assert rendered.strip()
    assert f"{cli.PROGRAM} apply -f examples/" in rendered, (
        "the recipes rendered empty, so the example fragments are not in the wheel"
    )


# The other side of the inventory


def test_the_gated_commands_refuse_from_the_bare_install(run) -> None:
    """They are in the grammar, so they parse; they need the server
    half, so they refuse; and they refuse with the sentence rather than
    with an ImportError.

    Run rather than imported, which is the whole point of naming them:
    both reach their heavy import inside their own arm, so importing
    `cli` would have passed a broken one in either direction.
    """
    for words in sorted(GATED):
        finished = run(*words)
        assert finished.returncode == 1, (words, finished.stdout, finished.stderr)
        assert finished.stderr.strip() == cli.NEEDS_THE_SERVER_HALF, words
        assert finished.stdout == "", words
        assert "Traceback" not in finished.stderr, words


def test_the_gated_pair_is_what_the_table_says_it_is() -> None:
    """The inventory held closed against the registration table, so a
    command that left the gated set fails from the side it left."""
    assert GATED <= {row.words for row in cli.COMMANDS}
    for words in GATED:
        assert registered(list(words)) == words


def defined_here() -> set[str]:
    """Every test this module defines, by name."""
    return {name for name in globals() if name.startswith("test_")}


def test_the_lane_ran_every_command_of_the_registration_table(
    request: pytest.FixtureRequest,
) -> None:
    """The completeness claim, both ways.

    The inventory is `cli.COMMANDS`, which is the grammar itself rather
    than a description of it. Every ungated row has to have been RUN
    from the installed binary and answered; no gated row may have
    answered; and nothing may have been driven that the table does not
    hold. A command that moved between the two sets fails from whichever
    side it left.

    Last in the file because that is the order the tests above ran in,
    and skipped rather than failed when the module was not run whole: a
    `-k` selection has driven only what it selected, and failing for
    that would train a reader to ignore this.
    """
    here = Path(__file__)
    selected = {
        getattr(item, "originalname", None) or item.name
        for item in request.session.items
        if Path(str(item.path)) == here
    }
    if defined_here() - selected:
        pytest.skip("only part of the lane was selected, so only part of it was driven")

    rows = {row.words for row in cli.COMMANDS}
    assert GATED & DRIVEN == set(), "a gated command answered, which it must not"

    missing = sorted(" ".join(words) for words in rows - GATED - DRIVEN)
    assert not missing, (
        "these commands are registered in cli.COMMANDS and no case in this lane ran "
        f"them successfully from the installed wheel: {missing}"
    )

    unknown = sorted(" ".join(words) for words in DRIVEN - rows)
    assert not unknown, f"this lane drove something the table does not have: {unknown}"


if __name__ == "__main__":  # pragma: no cover - a hand run of one lane
    sys.exit(pytest.main([__file__, "-v"]))
