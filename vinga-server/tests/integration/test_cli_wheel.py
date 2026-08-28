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

**The wheel's own closure is checked here, and only here.** The tier
lane holds an environment to what `uv.lock` says the DECLARATION
resolves to. Nothing in it reads the built artifact, so a wheel whose
`Requires-Dist` said something else would install a heavier closure
while both lanes stayed green: the metadata a resolver actually consults
is the wheel's, not the lock's. So this lane reads `Requires-Dist`
straight out of the built file and holds it to the declared tiers in
both directions, and then asserts the serve half is absent from the
environment that file was installed into, as distributions and as
importable modules.

Metadata plus absence, rather than a resolver report compared against
the installed set. A report would be a second re-resolution, which is
the thing this lane deliberately does not do (it installs one file), and
comparing one would re-derive the tier lane's claim under a different
name. What the two assertions here close between them is the gap the
review named: the artifact cannot ask for anything the declaration does
not, and what it asked for is what arrived.

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
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

from tests.support.commands import BUILD_SECONDS, ran
from tests.support.config_cli import registered
from tests.support.deployment import Live, check_in, serving
from tests.support.tiers import SERVE_MODULES, SIM_MODULES, declared, requirement_names
from vinga_server.config import cli
from vinga_server.config.models import DatabaseConfig
from vinga_server.config.secrets import MASTER_KEY_ENV, generate_key
from vinga_server.conversations.records import TurnRecord
from vinga_server.conversations.store import ConversationStore
from vinga_server.ota import OTA_PATH
from vinga_server.simulator import board, utterance

PROJECT = Path(__file__).resolve().parents[2]

# Not a real credential, and shaped so a substring check for it cannot
# match by accident.
SECRET = "sk-wheel-3d7c1e58-never-a-real-credential"

BOUND_MAC = "aa:bb:cc:dd:ee:ff"

# The second board, which arrives the other way: by checking in and
# showing a code.
WAITING_MAC = "11:22:33:44:55:66"

# And the one the simulated board presents, which is its own documented
# default rather than a third address invented here.
SIMULATED_MAC = board.DEFAULT_MAC

# The two boards the session verbs' record is written for, and the one
# thread both of its sessions fed. Their own addresses, so a purge by
# device here cannot reach a row another case is about.
SESSION_MAC = "02:00:00:00:00:31"

SESSION_OTHER_MAC = "02:00:00:00:00:32"

WHEEL_CONVERSATION = "5f6a7b8c9d0e1f20314253647586a9b0"


def session_manifest(device: str) -> dict[str, object]:
    """The manifest a session opens its row with, as the device session
    hands it over."""
    return {
        "started_at": "2026-08-15T10:00:00+00:00",
        "server": {"version": "0.1.0", "revision": "abc1234"},
        "device": {"mac": device, "client": "wheel"},
        "protocol": "1",
        "agent": "sam",
        "agents": ["sam"],
        "providers": {"llm": {"name": "mock", "type": "mock"}},
    }

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

# The three commands the grammar keeps and a BARE install cannot answer.
# Named as the expected inventory rather than discovered, because the
# completeness assertion below is two-way: a fourth gated command fails
# this lane from the side it joined.
#
# Two of them need the server half and one needs the `sim` extra, and
# they answer two different sentences, which is why each row carries the
# sentence it must print rather than the lane holding one constant for
# all three.
#
# `simulator run` carries arguments as well, and that is not decoration:
# a command line missing a required positional never reaches its own
# body, so a gated row driven without one would be asserting about
# Click's usage error instead of about the gate. The address is a port
# nothing listens on, and nothing reaches it: the gate fires before any
# request goes out.
GATED: dict[tuple[str, ...], tuple[str, tuple[str, ...]]] = {
    ("openapi",): (cli.NEEDS_THE_SERVER_HALF, ()),
    ("ota-url",): (cli.NEEDS_THE_SERVER_HALF, ()),
    ("simulator", "run"): (cli.NEEDS_THE_SIM_EXTRA, ("http://127.0.0.1:9/x/ABCDEFGH/",)),
}

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
    ran(
        ["uv", "build", "--wheel", "--out-dir", str(where)],
        seconds=BUILD_SECONDS,
        cwd=PROJECT,
        check=True,
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
    ran(["uv", "venv", "--python", "3.12", str(where)], seconds=BUILD_SECONDS, check=True)
    ran(
        ["uv", "pip", "install", "--python", str(where / "bin" / "python"), str(wheel)],
        seconds=BUILD_SECONDS,
        check=True,
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
def live(module_database: str) -> Iterator[Live]:
    """The server the subprocesses talk to, booted once for the module.

    In this process, which is what makes it cheap: it is a server, and
    the artifact under test is on the other end of the socket. On a
    database of this module's own, because the lane clears this
    worker's between tests and everything here stands on what the first
    of them applied.
    """
    patch = pytest.MonkeyPatch()
    patch.setenv(MASTER_KEY_ENV, generate_key())
    try:
        with serving(DatabaseConfig(name=module_database)) as running:
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
    return ran(
        [str(installed.parent / argv[0]), *argv[1:]],
        cwd=elsewhere,
        env=_environment(live),
        input=stdin,
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


def _requires_dist(wheel: Path) -> dict[str, set[str]]:
    """What the built wheel asks for, keyed by the extra that asks.

    Read out of the artifact's own `METADATA` rather than out of the
    installed environment: this is the block a resolver consults, and it
    is the one thing about the wheel that no lane which never opens the
    file can see. `""` is what an install with no extras pulls.
    """
    with zipfile.ZipFile(wheel) as built:
        [name] = [
            entry for entry in built.namelist() if entry.endswith(".dist-info/METADATA")
        ]
        metadata = built.read(name).decode("utf-8")
    asked: dict[str, set[str]] = {}
    for line in metadata.splitlines():
        if not line.startswith("Requires-Dist:"):
            continue
        requirement = line.removeprefix("Requires-Dist:").strip()
        head, _, marker = requirement.partition(";")
        extra = ""
        if "extra ==" in marker:
            extra = marker.split("extra ==")[1].strip().strip("\"'")
        asked.setdefault(extra, set()).update(requirement_names([head]))
    return asked


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


def test_the_wheel_asks_for_exactly_the_client_tier(wheel: Path) -> None:
    """What an install with no extras pulls, read off the artifact.

    Both ways against the declaration: a distribution the wheel asks for
    unconditionally and `pyproject.toml` does not declare is a heavier
    default install than anything else here would notice, and one the
    declaration has and the wheel does not is a client that cannot run.
    """
    client, _, _ = declared()

    assert _requires_dist(wheel)[""] == client


def test_the_wheel_gates_exactly_the_serve_tier_behind_the_extra(wheel: Path) -> None:
    """And the other half of the same block. A serve distribution that
    escaped its marker would be an unconditional requirement, which the
    case above catches; one that went missing from the extra would be an
    image build that installs a server without a server."""
    _, serve, _ = declared()

    assert _requires_dist(wheel)["serve"] == serve


def test_the_wheel_declares_no_extra_the_project_does_not(wheel: Path) -> None:
    """The gates themselves, held closed. An extra nobody declared is a
    door into this package that no lane and no document knows about."""
    project = json.loads(
        ran(
            [sys.executable, "-c", "import json,tomllib,sys;"
             "print(json.dumps(tomllib.load(open(sys.argv[1],'rb'))['project']))",
             str(PROJECT / "pyproject.toml")],
            check=True,
        ).stdout
    )

    gates = set(_requires_dist(wheel)) - {""}
    assert gates <= set(project["optional-dependencies"])


def test_the_serve_half_is_absent_from_the_environment_the_wheel_made(
    installed: Path, elsewhere: Path, live: Live
) -> None:
    """What the metadata asked for is what arrived, checked from the
    other end.

    The declared serve tier is absent as distributions, and every module
    it would have installed is absent to the interpreter. The second is
    not the first said twice: a distribution can be missing from the
    metadata while its module is importable through something that
    vendored it, which is exactly what a name check alone would miss.
    """
    _, serve, _ = declared()
    assert set(SERVE_MODULES) == serve, "the import-name map has drifted from the tier"

    reported = _ran(
        installed,
        elsewhere,
        live,
        "python",
        "-c",
        "import json,sys;from importlib.metadata import distributions;"
        "sys.stdout.write(json.dumps(sorted("
        "d.metadata['Name'].lower().replace('_','-') for d in distributions())))",
    )
    assert reported.returncode == 0, reported.stderr
    assert serve & set(json.loads(reported.stdout)) == set()

    for module in sorted(SERVE_MODULES.values()):
        finished = _ran(installed, elsewhere, live, "python", "-c", f"import {module}")
        assert finished.returncode != 0, f"{module} is importable from the wheel install"


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

    waiting = check_in(live, WAITING_MAC)
    assert isinstance(waiting, board.Activating)
    code = waiting.code
    assert code.isdigit()

    waiting = answered(run("device", "pending", "list"), "device pending list")
    assert code in waiting
    assert WAITING_MAC in waiting

    claimed = run("device", "pending", "claim", code, "sam")
    assert answered(claimed, "device pending claim").startswith("wrote ")


def test_a_simulated_board_checks_in_from_the_installed_wheel(run, live: Live) -> None:
    """The one command of the grammar that reaches something other than
    the configuration API, driven from the artifact.

    Ungated, and that is the claim rather than an omission: the check-in
    is httpx and pydantic and nothing else, so a bare `uvx --from git+...`
    install gets the whole of it. A lane that only ran this from a
    checkout would not have shown that.

    The board is a MAC nothing else in this file uses, so it is unbound
    and onboarding is on, which is the state that produces a code. What
    is asserted about the code is that the deployment agrees there is
    one: the same board is then in the pending listing the configuration
    API answers, which is the two halves of this lane meeting.
    """
    url = f"{live.origin}{OTA_PATH}"
    checked = answered(run("simulator", "check-in", url, "--mac", SIMULATED_MAC), "simulator")

    assert "not claimed yet" in checked
    [code] = [
        line.removeprefix("activation code: ").strip()
        for line in checked.splitlines()
        if line.startswith("activation code: ")
    ]
    assert code.isdigit()
    # Nothing about the address it was pointed at, which can be the
    # deployment's own secret.
    assert live.origin not in checked

    listed = answered(run("device", "pending", "list"), "device pending list")
    assert code in listed
    assert SIMULATED_MAC in listed
    assert board.BOARD_TYPE in listed


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
        # The one no-leak claim a subprocess lane can honestly make,
        # asserted about the command that was GIVEN the value rather
        # than about a later read of something else. A read of a
        # provider that never held the credential would have passed
        # while both writes echoed it, which is an assertion aimed at
        # the wrong process.
        if stdin is not None:
            assert stdin not in finished.stdout + finished.stderr, argv


def test_the_session_verbs_reach_the_record_from_the_installed_wheel(
    run, module_database: str
) -> None:
    """The `session` noun from a BARE install, which is the claim worth
    making about it: these four verbs read and erase a schema the store
    half owns, and they carry none of the store half with them. A
    command that quietly started importing SQLAlchemy to do it would
    fail here and pass every other lane.

    The record is written in this process, by the store the server would
    have written it with, because what is under test is the artifact on
    the other end of the socket rather than the pipeline that fills the
    database.
    """
    seeded = ConversationStore(
        DatabaseConfig(name=module_database), retention_days=0
    )
    seeded.start()
    try:
        for name, device in (("wheel-one", SESSION_MAC), ("wheel-two", SESSION_OTHER_MAC)):
            seeded.open_session(name, 100.0, session_manifest(device))
            seeded.record_turn(
                name,
                TurnRecord(
                    at=101.2,
                    conversation=WHEEL_CONVERSATION,
                    agent="sam",
                    heard="what is the weather like",
                    reply="Sunny.",
                ),
            )
            seeded.close_session(name, duration_s=2.0, reason="client")
    finally:
        seeded.stop()

    listed = answered(run("session", "list"), "session list")
    assert listed.splitlines()[0].split()[0] == "SESSION"
    assert "wheel-one" in listed and "wheel-two" in listed

    detail = answered(run("session", "show", "wheel-one"), "session show")
    assert detail.startswith("session: wheel-one\n")

    # No --force anywhere, for the reason the destructive block above
    # gives: a subprocess has no terminal, and a command that asked
    # would hang a pipeline rather than answer one.
    erased = answered(run("session", "delete", "wheel-one"), "session delete")
    assert erased.startswith("sessions: 1\n")

    purged = answered(
        run("session", "purge", "--device", SESSION_OTHER_MAC), "session purge"
    )
    assert purged.startswith("sessions: 1\n")

    assert "wheel-" not in answered(run("session", "list"), "session list")


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
    """They are in the grammar, so they parse; they need a half the bare
    install does not have, so they refuse; and they refuse with their own
    sentence rather than with an ImportError.

    Run rather than imported, which is the whole point of naming them:
    all three reach their heavy import inside their own arm, so importing
    `cli` would have passed a broken one in either direction.
    """
    for words in sorted(GATED):
        sentence, arguments = GATED[words]
        finished = run(*words, *arguments)
        assert finished.returncode == 1, (words, finished.stdout, finished.stderr)
        assert finished.stderr.strip() == sentence, words
        assert finished.stdout == "", words
        assert "Traceback" not in finished.stderr, words


def test_the_gated_set_is_what_the_table_says_it_is() -> None:
    """The inventory held closed against the registration table, so a
    command that left the gated set fails from the side it left."""
    assert set(GATED) <= {row.words for row in cli.COMMANDS}
    for words in GATED:
        assert registered(list(words)) == words


def test_the_wheel_gates_exactly_the_sim_tier_behind_its_extra(wheel: Path) -> None:
    """The third requirement block, held both ways like the other two.

    This is the half the review round named. A `sim` extra declared in
    `pyproject.toml` and missing from the wheel's own metadata would pass
    the tier closure lane, which syncs from the lock, because the
    metadata a resolver actually consults is the wheel's; and an install
    of `vinga-server[sim]` would then quietly be a bare install with a
    command that cannot run.
    """
    _, _, sim = declared()

    assert _requires_dist(wheel)["sim"] == sim


def test_the_websocket_client_is_absent_from_the_bare_wheel_install(
    installed: Path, elsewhere: Path, live: Live
) -> None:
    """The negative half of the same tier, checked from the environment
    the artifact made, as a distribution and as an importable module.

    The second is not the first said twice, and `websockets` is the one
    distribution here where that matters most: it arrives transitively
    through `uvicorn[standard]` as well as directly, so a tiering mistake
    would show up as an importable module before it showed up as a
    declared one.
    """
    _, _, sim = declared()
    assert set(SIM_MODULES) == sim, "the import-name map has drifted from the tier"

    reported = _ran(
        installed,
        elsewhere,
        live,
        "python",
        "-c",
        "import json,sys;from importlib.metadata import distributions;"
        "sys.stdout.write(json.dumps(sorted("
        "d.metadata['Name'].lower().replace('_','-') for d in distributions())))",
    )
    assert reported.returncode == 0, reported.stderr
    assert sim & set(json.loads(reported.stdout)) == set()

    for module in sorted(SIM_MODULES.values()):
        finished = _ran(installed, elsewhere, live, "python", "-c", f"import {module}")
        assert finished.returncode != 0, f"{module} is importable from the wheel install"


def test_the_packaged_utterance_is_inside_the_built_wheel(wheel: Path) -> None:
    """The asset, read out of the archive itself.

    It needs no `force-include` because it lives inside the package
    hatchling already carries, and an absence declared nowhere is an
    absence nothing would notice. So the claim is made here instead, at
    the one place that reads the built file: both names are in the
    archive, and the asset is the bytes its manifest describes.
    """
    carried = {
        name: zipfile.ZipFile(wheel).read(name)
        for name in zipfile.ZipFile(wheel).namelist()
        if "simulator/data/" in name
    }

    where = f"vinga_server/simulator/{utterance.DATA}"
    assert set(carried) == {f"{where}/{utterance.ASSET}", f"{where}/{utterance.MANIFEST}"}

    said = utterance.understood(
        carried[f"{where}/{utterance.MANIFEST}"], carried[f"{where}/{utterance.ASSET}"]
    )
    assert said.packets == utterance.packaged().packets


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
    assert set(GATED) & DRIVEN == set(), "a gated command answered, which it must not"

    missing = sorted(" ".join(words) for words in rows - set(GATED) - DRIVEN)
    assert not missing, (
        "these commands are registered in cli.COMMANDS and no case in this lane ran "
        f"them successfully from the installed wheel: {missing}"
    )

    unknown = sorted(" ".join(words) for words in DRIVEN - rows)
    assert not unknown, f"this lane drove something the table does not have: {unknown}"


if __name__ == "__main__":  # pragma: no cover - a hand run of one lane
    sys.exit(pytest.main([__file__, "-v"]))
