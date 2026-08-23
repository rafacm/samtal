"""The whole `vinga-server config` grammar, driven over a real socket.

The unit lane's acceptance spine (`tests/unit/test_config_cli.py` and its
five neighbours) runs the same entry point against the same application,
with one thing replaced: `cli.build_client` hands back a `TestClient`
instead of opening a connection. That is the right seam for those suites
and it is exactly what this one may not do. Here `build_client` is
untouched, so every command below resolves an address, applies the
transport policy, opens a socket, sends a bearer token a real ASGI server
checks, and reads an answer a real uvicorn wrote.

What that buys, and what nothing in-process can show:

- The addressing and the transport policy run in front of the seam, so
  the acceptance suites drive them and stop. Here they are what finds the
  server.
- A refusal is composed by the API, serialized, sent, parsed by the CLI
  and printed. That the sentence survives the round trip intact is a
  claim about the wire, and this is where it is made.
- `apply` has no read timeout at all (`cli.APPLY_READ_TIMEOUT_S`), which
  a mock transport cannot demonstrate: there is nothing to wait for.
  Both halves of the bound are proven here against a server that really
  takes time to answer.

The lane's server is booted the way decision 9 asks the quick start to
be: from environment variables alone, with NO configuration file
anywhere. `test_the_lane_s_server_booted_from_the_environment_alone`
states that as a claim rather than leaving it a property of a fixture.

Coverage is derived rather than declared. `run` records the row of
`cli.COMMANDS` each command line names, and only when the command
succeeded, so what the recording holds is "this command completed over
the wire" and not "this command line was typed". The last test in the
file holds that recording to the registration table, which is what makes
a command added to the table and not to this lane a failing test rather
than an omission nobody sees.

Ordering: the tests below share one server and one store on purpose,
because what they describe is one operator's session against one
deployment. They run in the order they are written, which is pytest's
order within a module and, under `-n auto --dist loadfile`, still one
worker's order. The completeness test is last for the same reason, and
skips rather than lies when the module was not run whole.
"""

import contextlib
import io
import json
import socket
import sys
import threading
import time
import urllib.request
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest
import uvicorn
import yaml

from tests.support.config_cli import document
from vinga_server.app import create_app
from vinga_server.config import cli
from vinga_server.config.boot import load_boot_config
from vinga_server.config.models import API_MOUNT_PATH
from vinga_server.config.secrets import MASK, MASTER_KEY_ENV, generate_key
from vinga_server.ota import OTA_PATH

# The one variable a fileless boot needs: where the database goes. The
# server half of the configuration is otherwise all defaults, which is
# what "a handful of environment variables" means for a deployment that
# has not yet configured anything.
DATABASE_DIR_ENV = "VINGA_SERVER__DATABASE__DIR"

CONFIG_ENV = "VINGA_CONFIG"

# The variable `set-secret --from-env` is pointed at. Not a real
# credential, and shaped so a substring check for it cannot match by
# accident.
SECRET_ENV = "VINGA_LANE_SECRET"

SECRET = "sk-live-1c4e9f27-never-a-real-credential"


@dataclass(frozen=True)
class Live:
    """One running server, as this lane addresses it."""

    # What a device reaches: the origin the OTA endpoint is served on.
    origin: str

    # Where the database it serves is, so a test can read the file back
    # rather than take the API's word for it.
    directory: Path

    @property
    def api_url(self) -> str:
        """Where the configuration API is, which is what the CLI is
        pointed at."""
        return f"{self.origin}{API_MOUNT_PATH}"


@contextlib.contextmanager
def serving(directory: Path) -> Iterator[Live]:
    """A real uvicorn on an ephemeral loopback port, booted with no
    configuration file at all.

    `load_boot_config()` with no path and no `VINGA_CONFIG` in the
    environment is exactly what `main()` runs on a deployment that
    passed no `--config`: the file half comes from the settings
    machinery, which reads `VINGA_SERVER__*` and nothing else, and the
    domain half comes from the database that half names.

    The port lives on the socket rather than in the configuration, the
    way `tests/integration/conftest.py` does it: the models refuse 0,
    which is right for a deployment and is not what binding an ephemeral
    port means.

    Run in a thread rather than on a loop of the test's own, because
    what talks to it is `cli.main`, which is synchronous and stays that
    way; uvicorn skips its signal handlers off the main thread, which is
    the one thing that would otherwise need care here.
    """
    directory.mkdir(parents=True, exist_ok=True)
    with pytest.MonkeyPatch.context() as patch:
        patch.delenv(CONFIG_ENV, raising=False)
        patch.setenv(DATABASE_DIR_ENV, str(directory))
        booted = load_boot_config()
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(booted.config, booted.secrets),
            host="127.0.0.1",
            port=0,
            log_level="warning",
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 30
        while not server.started:
            assert thread.is_alive() and time.monotonic() < deadline, "the server never started"
            time.sleep(0.02)
        port = server.servers[0].sockets[0].getsockname()[1]
        yield Live(origin=f"http://127.0.0.1:{port}", directory=directory)
    finally:
        server.should_exit = True
        thread.join(timeout=30)


@pytest.fixture(scope="module")
def live(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Live]:
    """The lane's server, booted once for the whole module.

    Once rather than per test, because the boot is the expensive part
    and the store is what the tests are about: an operator's session is
    a sequence of commands against one deployment, and that is what this
    is. The two tests that need a store nobody else has wrote to ask for
    `isolated` instead.

    The environment is this module's for the length of it: the address
    the CLI resolves, the encryption key both halves share, and no
    configuration file anywhere.
    """
    patch = pytest.MonkeyPatch()
    patch.delenv(CONFIG_ENV, raising=False)
    patch.setenv(MASTER_KEY_ENV, generate_key())
    patch.setenv(SECRET_ENV, SECRET)
    try:
        with serving(tmp_path_factory.mktemp("lane") / "db") as running:
            # Resolved from the environment rather than passed with every
            # command, which is the documented remote shape and the one
            # an operator's shell is set up for. The `--api-url` half is
            # what the isolated server below is reached by, so both
            # resolutions are driven over a real socket.
            patch.setenv(cli.API_URL_ENV, running.api_url)
            yield running
    finally:
        patch.undo()


@pytest.fixture
def isolated(tmp_path: Path) -> Iterator[Live]:
    """A second server on a store of its own, for the two tests whose
    claim is about what the store holds afterwards.

    Reached by `--api-url`, because the environment names the lane's own
    server and the point of this one is that nothing else wrote to it.
    """
    with serving(tmp_path / "db") as running:
        yield running


# What the lane drove
#
# The registration table is the inventory, and this is what actually ran
# against it. Recorded from the command line rather than declared beside
# it, so the coverage list cannot say a command was driven that was not:
# the only way into this set is a command that ran and succeeded.

_BY_WORDS = {row.words: row for row in cli.COMMANDS}

_FIRST_WORDS = {row.words[0] for row in cli.COMMANDS}

DRIVEN: set[tuple[str, ...]] = set()


def registered(argv: Sequence[str]) -> tuple[str, ...] | None:
    """Which row of `cli.COMMANDS` this command line names.

    The words are found rather than assumed to be first, because the
    three global options are accepted before the command as well as
    after it. A two-word row wins over the one-word row it sits under,
    which is how `show device` is told from `show`.

    Read off the table, never a list of it: a command added to the
    grammar is addressed here the day it is added.
    """
    for index, word in enumerate(argv):
        if word not in _FIRST_WORDS:
            continue
        pair = tuple(argv[index : index + 2])
        if pair in _BY_WORDS:
            return pair
        if (word,) in _BY_WORDS:
            return (word,)
    return None


def run(*argv: str, stdin: str | None = None) -> int:
    """One command, run the way the entry point runs it, over a real
    connection to a real server.

    Nothing is patched. `cli.build_client` is the one the module ships,
    so the address, the token, the timeouts and the transport policy are
    all the deployed ones, and what answers is uvicorn.

    A command that succeeded is recorded against its row. Only a
    success: a lane that counted a refusal as coverage would let a
    command whose happy path was never run pass the completeness test
    below, which is the one thing that test exists to catch.
    """
    saved = sys.stdin
    if stdin is not None:
        sys.stdin = io.StringIO(stdin)
    try:
        code = cli.main(list(argv))
    finally:
        sys.stdin = saved
    if code == 0:
        words = registered(argv)
        assert words is not None, f"no row of the grammar is named by {argv}"
        DRIVEN.add(words)
    return code


def written(directory: Path, name: str, body: object) -> str:
    """One document on disk, as `-f` takes it."""
    path = directory / name
    path.write_text(yaml.safe_dump(body, sort_keys=False), encoding="utf-8")
    return str(path)


def check_in(live: Live, mac: str) -> Mapping[str, object]:
    """One board's OTA check-in, headers and all, the way
    `test_ota_endpoint.py` makes one.

    The only thing in this lane that is not a CLI command, and it is
    here because two of the commands need a device: a code is minted by
    a board asking for one, and `pending` and `add-device` are about
    what an operator does with the number on its screen. A plain HTTP
    POST is the whole of what a board does to get there, so the lane can
    make one without hardware and without the websocket simulator.
    """
    request = urllib.request.Request(
        f"{live.origin}{OTA_PATH}",
        data=json.dumps(
            {
                "version": 2,
                "mac_address": mac,
                "uuid": DEVICE_UUID,
                "application": {"name": "xiaozhi", "version": "2.4.0"},
                "board": {"type": BOARD},
            }
        ).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Device-Id": mac,
            "Client-Id": DEVICE_UUID,
            "User-Agent": f"{BOARD}/2.4.0",
            "Accept-Language": "en-US",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        answer: Mapping[str, object] = json.loads(response.read())
    return answer


BOARD = "waveshare-esp32-s3-touch-lcd-1.54"

DEVICE_UUID = "6f1a2b3c-4d5e-6f70-8192-a3b4c5d6e7f8"

# The board an operator already knows the address of, and the one that
# is only ever a number on a screen.
KNOWN_MAC = "aa:bb:cc:dd:ee:ff"

WAITING_MAC = "11:22:33:44:55:66"


# The deployment this lane configures
#
# Small on purpose: what the lane is about is the commands, and a
# document this size names every section, exercises every reference edge
# apply has to resolve in one transaction, and leaves the reload with
# something to build. The two MCP entries are granted by no agent, so
# nothing is ever connected for them and a reload starts and stops
# nothing.

# Named rather than written into the document below, because the
# singleton's own cycle writes it a second time and two spellings of one
# entity would be two chances to disagree about it.
AGENT_DEFAULTS: dict[str, object] = {
    "llm": "brain",
    "asr": "ears",
    "tts": "voice",
    "vad": "gate",
}

DEPLOYMENT: dict[str, object] = {
    "providers": {
        "llm": {
            "brain": {"type": "mock", "reply": "You said {text}."},
            # Referenced by nothing, which is what makes it the entry a
            # credential is stored on and a delete can take away.
            "spare": {"type": "anthropic", "model": "m", "api_key_env": "ANTHROPIC_API_KEY"},
        },
        "asr": {"ears": {"type": "mock", "text": "hello"}},
        "tts": {"voice": {"type": "mock"}},
        "vad": {"gate": {"type": "mock"}},
    },
    "mcp_servers": {
        "house": {"transport": "stdio", "command": "/bin/echo", "args": ["house"]},
        "weather": {"transport": "streamable_http", "url": "https://example.invalid/mcp"},
    },
    "prompt_fragments": {"household": {"text": "The bins go out on Tuesday."}},
    "agent_defaults": AGENT_DEFAULTS,
    "agents": {"sam": {"prompt": "You are Sam.", "prompt_includes": ["household"]}},
}


@pytest.fixture(scope="module")
def deployed(live: Live, tmp_path_factory: pytest.TempPathFactory) -> Live:
    """The lane's store, configured through `config apply` over the
    wire.

    The bootstrap is the acceptance case of the whole issue, and it is a
    fixture rather than a test because everything below stands on it:
    one document, one transaction, every section named, against a server
    that booted on an empty database.
    """
    document_path = written(tmp_path_factory.mktemp("apply"), "deployment.yaml", DEPLOYMENT)
    assert run("apply", "-f", document_path) == 0
    return live


def test_a_whole_deployment_applies_over_the_wire(
    deployed: Live, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """What the bootstrap above did, read back through a second command.

    The document created an agent, the providers its defaults name and
    the fragment it includes, all in one request: every intermediate
    state on the way would have been refused by a per-entity check, and
    the transaction that makes that work is on the server, which is what
    puts this on the wire rather than in a loop of PUTs.
    """
    assert run("show") == 0
    shown = document(capsys.readouterr().out)

    assert shown["providers"]["llm"]["brain"]["reply"] == "You said {text}."
    assert shown["mcp_servers"]["house"]["command"] == "/bin/echo"
    assert shown["prompt_fragments"]["household"]["text"].startswith("The bins")
    assert shown["agent_defaults"]["llm"] == "brain"
    assert shown["agents"]["sam"]["prompt"] == "You are Sam."


def test_the_same_document_twice_changes_nothing(
    deployed: Live, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Idempotence over the wire: the document the fixture applied,
    applied again, reports every entry unchanged and writes nothing.

    The outcome listing is the assertion because it is the only order an
    apply observably has, and `unchanged` on every line is the claim
    that the comparison happened rather than the rows being rewritten.
    """
    document_path = written(tmp_path, "deployment.yaml", DEPLOYMENT)

    assert run("apply", "-f", document_path) == 0

    captured = capsys.readouterr()
    outcomes = [line.split(": ")[-1] for line in captured.out.splitlines()]
    assert outcomes and set(outcomes) == {"unchanged"}
    # Nothing was written, so nothing is waiting on a boundary either.
    assert captured.err == ""


# One entity's life, per kind
#
# The five commanded kinds behave alike by construction (#139 made the
# reads and the writes derive from the descriptor registry), so what is
# worth driving over the wire is one entity's whole life once per kind:
# written from a fragment, read back, exported, written again from the
# inline pairs that assemble the same mapping, and taken away.
#
# The entries are named so that nothing in the deployment references
# them, which is what lets the delete at the foot of the cycle succeed:
# a referenced entry is refused, and that refusal is a case of its own
# further down.

CYCLE: tuple[tuple[str, tuple[str, ...], dict[str, object], tuple[str, ...], bool], ...] = (
    (
        "provider",
        ("llm", "spare-brain"),
        {"type": "mock", "reply": "Spare."},
        ("type=mock", "reply=Spare."),
        True,
    ),
    (
        "mcp-server",
        ("shed",),
        {"transport": "stdio", "command": "/bin/echo"},
        ("transport=stdio", "command=/bin/echo"),
        True,
    ),
    (
        "prompt-fragment",
        ("radio",),
        {"text": "The radio is called Bosse."},
        ("text=The radio is called Bosse.",),
        True,
    ),
    (
        "agent",
        ("spare-agent",),
        {"prompt": "You are a spare."},
        ("prompt=You are a spare.",),
        True,
    ),
    # The one entity there is only one of: no identity addresses it and
    # no verb deletes it, and what it is written with is what the
    # deployment already holds, because clearing the defaults out from
    # under an agent that relies on them is a different test's business.
    (
        "agent-defaults",
        (),
        AGENT_DEFAULTS,
        ("llm=brain", "asr=ears", "tts=voice", "vad=gate"),
        False,
    ),
)


@pytest.mark.parametrize(
    ("kind", "identity", "fragment", "pairs", "deletable"),
    CYCLE,
    ids=[row[0] for row in CYCLE],
)
def test_one_entity_is_written_read_exported_and_deleted(
    deployed: Live,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    kind: str,
    identity: tuple[str, ...],
    fragment: dict[str, object],
    pairs: tuple[str, ...],
    deletable: bool,
) -> None:
    """The whole of one kind's grammar over the wire, in the order an
    operator meets it.

    The two write forms are asserted against each other rather than
    against a literal: what `key=value` promises is the store the
    fragment would have left, and reading the entity back through the
    same command after each of them is what says so.
    """
    path = written(tmp_path, "fragment.yaml", fragment)

    assert run("set", kind, *identity, "-f", path) == 0
    acknowledged = capsys.readouterr()
    assert acknowledged.out.startswith("wrote ")
    # Every write says when it takes effect, and it says it on stderr so
    # that stdout holds the acknowledgement alone.
    assert acknowledged.err.strip()

    assert run("show", kind, *identity) == 0
    shown = document(capsys.readouterr().out)
    assert fragment.items() <= shown.items()

    assert run("export", kind, *identity) == 0
    exported = capsys.readouterr().out
    # The header names the command that writes one back, and the body
    # under it is the same read `show` renders: export is the writable
    # projection of the display one, not a second read.
    assert exported.startswith("# One ")
    assert f"{cli.PROGRAM} set {kind}" in exported
    assert document(exported) == shown

    assert run("set", kind, *identity, *pairs) == 0
    assert capsys.readouterr().out.startswith("wrote ")

    assert run("show", kind, *identity) == 0
    assert document(capsys.readouterr().out) == shown

    if not deletable:
        return

    assert run("delete", kind, *identity) == 0
    assert capsys.readouterr().out.startswith("wrote ")

    assert run("show", kind, *identity) == 1
    gone = capsys.readouterr()
    assert gone.out == ""
    assert "Traceback" not in gone.err


# The two device commands, and the settings beside them
#
# Which of the two an operator wants depends on what they are holding: a
# MAC they already know, or a board in front of them showing six digits.
# Only the second needs a device, and over a real server a device is a
# check-in, which is why this is the one place the lane speaks something
# other than the CLI.


def test_a_board_is_bound_by_the_mac_you_already_know(
    deployed: Live, capsys: pytest.CaptureFixture[str]
) -> None:
    """Written in the spelling a sticker carries and read back in the
    one the configuration holds, which is the normalization the write
    path does and the wire carries verbatim."""
    assert run("bind-device", KNOWN_MAC.upper().replace(":", "-"), "sam") == 0
    bound = capsys.readouterr()
    assert bound.out.startswith("wrote ")
    assert bound.err.strip()

    assert run("show", "device", KNOWN_MAC) == 0
    assert document(capsys.readouterr().out) == {"agents": ["sam"]}

    assert run("delete", "device", KNOWN_MAC) == 0
    assert capsys.readouterr().out.startswith("wrote ")

    assert run("show", "device", KNOWN_MAC) == 1
    assert capsys.readouterr().out == ""


def test_a_board_is_onboarded_by_the_code_on_its_screen(
    deployed: Live, capsys: pytest.CaptureFixture[str]
) -> None:
    """The whole onboarding ceremony against a running server: the
    default agent, the code, the listing and the claim.

    The two check-ins are what make the settings' claim observable. A
    binding and the default agent are read as a device asks for them
    rather than at a restart, so a board checking in after
    `set-default-agent` is answered as a configured device and mints no
    code at all, and the same board after `clear-default-agent` is
    unbound and gets one. Nothing in-process can show that: it is the
    running server re-reading the database between two requests.
    """
    assert run("set-default-agent", "sam") == 0
    assert capsys.readouterr().out.startswith("wrote ")
    assert "activation" not in check_in(deployed, WAITING_MAC)

    assert run("clear-default-agent") == 0
    assert capsys.readouterr().out.startswith("wrote ")
    activation = check_in(deployed, WAITING_MAC)["activation"]
    code = str(activation["code"])
    assert code.isdigit()

    assert run("pending") == 0
    waiting = capsys.readouterr().out
    assert code in waiting
    assert WAITING_MAC in waiting
    assert BOARD in waiting

    assert run("add-device", code, "sam") == 0
    claimed = capsys.readouterr()
    assert claimed.out.startswith("wrote ")
    assert claimed.err.strip()

    assert run("show", "device", WAITING_MAC) == 0
    assert document(capsys.readouterr().out) == {"agents": ["sam"]}

    # And the code is retired with the claim, so the board that was
    # waiting is no longer waiting for anything.
    assert run("pending") == 0
    assert code not in capsys.readouterr().out


# A credential's whole life over the wire
#
# The one surface where what crosses the connection matters as much as
# what comes back: the value is read here, sent as a request body, stored
# encrypted, and never travels again. Both doors it is entered through
# are driven, because they are different code paths on this side of the
# socket and the same one on the other.


def test_a_credential_is_stored_masked_and_cleared(
    deployed: Live, capsys: pytest.CaptureFixture[str]
) -> None:
    """Two slots on two kinds, entered the two ways the command takes,
    read back masked, exported as the commands that refill them, and
    cleared.

    The entity the provider credential lands on is the one no agent
    references, so a reload never builds it and the value is never asked
    for by anything but this test.
    """
    assert run("set-secret", "provider", "llm", "spare", "api_key", "--from-env", SECRET_ENV) == 0
    assert capsys.readouterr().out.startswith("wrote ")

    assert run("set-secret", "mcp-server", "weather", "headers.Authorization", stdin=SECRET) == 0
    assert capsys.readouterr().out.startswith("wrote ")

    assert run("show", "provider", "llm", "spare") == 0
    entity = capsys.readouterr().out
    assert f"api_key: {MASK}" in entity
    assert SECRET not in entity

    assert run("show") == 0
    everything = capsys.readouterr().out
    assert SECRET not in everything
    # The environment reference the stored value displaces is marked
    # rather than left silent, which is #192's marker seen from the far
    # end of a connection.
    assert "used instead of api_key_env: ANTHROPIC_API_KEY" in everything

    assert run("export") == 0
    exported = capsys.readouterr().out
    # A credential never travels in a read, so what the document carries
    # is the command that enters it, and never the mask, which a
    # creating write would refuse.
    assert f"{cli.PROGRAM} set-secret provider llm spare api_key" in exported
    assert f"{cli.PROGRAM} set-secret mcp-server weather headers.Authorization" in exported
    assert MASK not in exported
    assert SECRET not in exported

    assert run("clear-secret", "provider", "llm", "spare", "api_key") == 0
    assert capsys.readouterr().out.startswith("wrote ")
    assert run("clear-secret", "mcp-server", "weather", "headers.Authorization") == 0
    assert capsys.readouterr().out.startswith("wrote ")

    assert run("show", "provider", "llm", "spare") == 0
    cleared = capsys.readouterr().out
    assert MASK not in cleared
    # And what the stored value was covering is back in view.
    assert "api_key_env: ANTHROPIC_API_KEY" in cleared


# What the running server is asked
#
# Three of the four reads below are of the process rather than of the
# database, which is the whole reason they have no break-glass path and
# the whole reason they belong here: what answers them is a server that
# booted on an empty store and has been written to over HTTP ever since,
# so the difference a reload makes is observable in one session.


def test_the_running_server_is_read_after_a_reload(
    deployed: Live, capsys: pytest.CaptureFixture[str]
) -> None:
    """The reload, and the two reads that are about the process.

    `prompt` is the pin: the server this lane booted was given an empty
    domain half, so the agent every other test has been writing to is
    one it is not serving, and asking for its prompt is refused until a
    reload installs it. That sequence needs a server with a lifetime,
    which is what this lane has and the acceptance suites do not.
    """
    assert run("prompt", "sam") == 1
    unserved = capsys.readouterr()
    assert unserved.out == ""
    assert "Traceback" not in unserved.err

    assert run("reload") == 0
    applied = capsys.readouterr().out
    assert "sam" in applied

    assert run("prompt", "sam") == 0
    assembled = capsys.readouterr().out
    assert "You are Sam." in assembled
    # The fragment the agent includes is part of what the model is
    # given, which is the difference between this read and `show agent`.
    assert "The bins go out on Tuesday." in assembled

    assert run("status") == 0
    running = capsys.readouterr().out
    # Configured, and connected for nobody: no agent grants either
    # entry, so the reload started nothing and both are reported as the
    # entries they are rather than omitted.
    assert "house" in running
    assert "weather" in running

    assert run("list") == 0
    summary = capsys.readouterr().out
    assert "sam" in summary
    assert "household" in summary
    assert SECRET not in summary


def test_the_documents_that_reach_nothing_render_in_the_same_environment(
    deployed: Live, capsys: pytest.CaptureFixture[str]
) -> None:
    """The four commands that contact no server, run in the environment
    the rest of the lane runs in.

    They are here for completeness rather than for the wire: what makes
    them worth a case in this module is that the environment names a
    running server and a database directory, and these four still open
    nothing and ask nothing. A command that quietly started needing the
    API would pass every unit suite and fail here.
    """
    assert run("schema") == 0
    whole = json.loads(capsys.readouterr().out)
    assert "agents" in whole["properties"]

    assert run("schema", "agent") == 0
    assert "properties" in json.loads(capsys.readouterr().out)

    assert run("reference") == 0
    assert "# " in capsys.readouterr().out

    assert run("openapi") == 0
    served = json.loads(capsys.readouterr().out)
    assert "/apply" in served["paths"]

    assert run("ota-url") == 0
    printed = capsys.readouterr()
    # The URL alone on stdout, so it can be captured; what to do with it
    # goes to stderr the way every other notice does.
    assert printed.out.strip().startswith("http")
    assert printed.err.strip()


def test_the_store_exports_as_a_document_it_applies_back_unchanged(
    deployed: Live, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The round trip, against a store this lane wrote through nine
    commands rather than through a fixture.

    Three claims in one sequence. What `export` prints is a document
    `apply` takes, which is what makes it the writable projection rather
    than a second display. Applying it changes nothing, which is what
    says the projection is faithful: a field the export spelled that the
    store does not hold would come back `wrote`. And a second export is
    the same bytes, which is what makes the document a thing an operator
    can keep in version control.
    """
    assert run("export") == 0
    exported = capsys.readouterr().out
    path = tmp_path / "exported.yaml"
    path.write_text(exported, encoding="utf-8")

    assert run("apply", "-f", str(path)) == 0
    applied = capsys.readouterr()
    outcomes = [line.split(": ")[-1] for line in applied.out.splitlines()]
    assert outcomes and set(outcomes) == {"unchanged"}
    assert applied.err == ""

    assert run("export") == 0
    assert capsys.readouterr().out == exported


# One refusal per family, and where each of them is composed
#
# A family is a top-level word of the grammar, which is a fact of the
# registration table rather than a grouping invented here: the row below
# each family's name is held to that table by
# `test_every_family_of_the_grammar_has_a_refusal`.
#
# What is asserted about each is the sentence, exactly as it arrived. A
# refusal composed by the API is serialized, sent, parsed and printed
# before an operator sees it, and every step of that is a place a
# sentence can be lost, truncated or replaced by a middlebox's page.
#
# The `wire` column says where each refusal is composed, and it is
# load-bearing rather than documentation: the second case below runs
# every one of these against an address nothing listens on, where a
# refusal the server composes cannot happen and the transport sentence
# takes its place, and a refusal this side composes is unchanged.

USAGE = cli.usage_line(cli.SECRET_NEVER_AN_ARGUMENT)

REFUSALS: tuple[tuple[str, tuple[str, ...], str, bool], ...] = (
    (
        "set",
        ("set", "agent", "refused-agent", "prompt=p", "llm=nowhere"),
        "would leave these references unresolved",
        True,
    ),
    ("delete", ("delete", "agent", "no-such-agent"), "no agent of that name exists", True),
    (
        "bind-device",
        ("bind-device", "not-a-mac", "sam"),
        "six colon-separated hex pairs",
        True,
    ),
    (
        "add-device",
        ("add-device", "000000", "sam"),
        "no device is waiting with that activation code",
        True,
    ),
    (
        "apply",
        ("apply", "-f", "REFUSED_DOCUMENT"),
        "the top-level keys of an applied document",
        True,
    ),
    ("pending", ("pending", "extra"), USAGE, False),
    ("status", ("status", "extra"), USAGE, False),
    (
        "prompt",
        ("prompt", "no-such-agent"),
        "this server is not serving an agent of that name",
        True,
    ),
    ("reload", ("reload", "extra"), USAGE, False),
    ("ota-url", ("ota-url", "--config", "/nowhere/at/all.yaml"), "config file not found", False),
    (
        "set-default-agent",
        ("set-default-agent", "no-such-agent"),
        "would leave these references unresolved",
        True,
    ),
    ("clear-default-agent", ("clear-default-agent", "extra"), USAGE, False),
    (
        "set-secret",
        ("set-secret", "provider", "llm", "no-such", "api_key", "--from-env", SECRET_ENV),
        "no provider of that name exists for that stage",
        True,
    ),
    (
        "clear-secret",
        ("clear-secret", "provider", "llm", "no-such", "api_key"),
        "no secret is stored for that slot",
        True,
    ),
    ("list", ("list", "extra"), USAGE, False),
    ("schema", ("schema", "nonsense"), "is not a documented entity", False),
    ("reference", ("reference", "extra"), USAGE, False),
    ("openapi", ("openapi", "extra"), USAGE, False),
    (
        "show",
        ("show", "provider", "llm", "no-such"),
        "no provider of that name exists for that stage",
        True,
    ),
    ("export", ("export", "agent", "no-such"), "no agent of that name exists", True),
)


def test_every_family_of_the_grammar_has_a_refusal() -> None:
    """The refusal table, held to the registration table.

    A family with no refusal here would be a family whose sentences
    nothing has ever seen cross a connection, and the way that happens
    is a command added to the grammar rather than a row deleted from
    this file.
    """
    assert {family for family, _, _, _ in REFUSALS} == {row.words[0] for row in cli.COMMANDS}


def refusing(argv: Sequence[str], directory: Path) -> tuple[str, ...]:
    """One refusal's command line, with the document the apply case
    needs written where it can be found."""
    return tuple(
        written(directory, "refused.yaml", {"nonsense_section": {}})
        if word == "REFUSED_DOCUMENT"
        else word
        for word in argv
    )


@pytest.mark.parametrize(
    ("family", "argv", "sentence", "wire"),
    REFUSALS,
    ids=[family for family, _, _, _ in REFUSALS],
)
def test_one_refusal_of_each_family_arrives_intact(
    deployed: Live,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    family: str,
    argv: tuple[str, ...],
    sentence: str,
    wire: bool,
) -> None:
    """A refusal is one sentence on stderr and exit 1, whichever end of
    the connection composed it."""
    assert run(*refusing(argv, tmp_path)) == 1

    captured = capsys.readouterr()
    assert sentence in captured.err
    # Nothing on stdout, so a script reading a command's output reads
    # nothing rather than half an answer.
    assert captured.out == ""
    assert "Traceback" not in captured.err


@pytest.mark.parametrize(
    ("family", "argv", "sentence", "wire"),
    REFUSALS,
    ids=[family for family, _, _, _ in REFUSALS],
)
def test_a_refusal_the_server_composes_needs_the_server(
    deployed: Live,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    family: str,
    argv: tuple[str, ...],
    sentence: str,
    wire: bool,
) -> None:
    """The same command lines against an address nothing listens on.

    This is what makes the `wire` column above a fact rather than a
    note. A refusal the API composes cannot be composed at all when
    there is no API to reach, so what arrives instead is the transport
    sentence; a refusal this side composes never gets that far and is
    the same sentence it was.

    The address is given in the root position, before the command word,
    which is the position the grammar accepts for every command
    including the four that reach nothing.
    """
    with contextlib.closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        nowhere = f"http://127.0.0.1:{sock.getsockname()[1]}{API_MOUNT_PATH}"

    assert run("--api-url", nowhere, *refusing(argv, tmp_path)) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Traceback" not in captured.err
    if wire:
        assert "cannot reach the configuration API" in captured.err
        assert nowhere in captured.err
    else:
        assert sentence in captured.err
        assert "cannot reach the configuration API" not in captured.err


def test_a_fragment_that_will_not_parse_never_travels(
    deployed: Live, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The other end of the refusal spectrum from the table above: a
    fragment is read, parsed and checked before a connection is opened,
    so a broken one is refused with the entity it was aimed at still
    exactly as it was."""
    broken = tmp_path / "broken.yaml"
    broken.write_text("prompt: [\n", encoding="utf-8")

    assert run("set", "agent", "sam", "-f", str(broken)) == 1
    refused = capsys.readouterr()
    assert "invalid YAML in" in refused.err
    assert "Traceback" not in refused.err

    assert run("show", "agent", "sam") == 0
    assert document(capsys.readouterr().out)["prompt"] == "You are Sam."
