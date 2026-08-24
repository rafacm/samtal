"""The whole `vinga-server config` grammar, driven over a real socket.

The unit lane's acceptance spine (`tests/unit/test_config_cli.py` and its
five neighbours) runs the same entry point against the same application,
with one thing replaced: `cli.build_client` hands back a `TestClient`
instead of opening a connection. That is the right seam for those suites
and it is exactly what this one may not do. Here `build_client` is
untouched, so every command that reaches a server resolves an address,
applies the transport policy, opens a socket, sends a bearer token a real
ASGI server checks, and reads an answer a real uvicorn wrote.

Four of the grammar's commands reach nothing at all by design (`schema`,
`reference`, `openapi`, `ota-url`). They run in this lane too, in the
same environment as the rest, and what is asserted about them is the
opposite claim: that an environment naming a running server and a
database directory leaves them opening neither.

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
succeeded, so what the recording holds is "this command completed" and
not "this command line was typed". The last test in the file holds that
recording to the registration table, which is what makes a command added
to the table and not to this lane a failing test rather than an omission
nobody sees.

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
import logging
import os
import socket
import sys
import threading
import time
import traceback
import urllib.request
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import NamedTuple

import pytest
import uvicorn
import yaml

from tests.support.config_cli import document
from vinga_server.app import create_app
from vinga_server.config import ConfigError, cli, docgen, entities
from vinga_server.config.boot import load_boot_config
from vinga_server.config.models import API_MOUNT_PATH, DOMAIN_KEYS, NOT_A_MAC
from vinga_server.config.secrets import MASK, MASTER_KEY_ENV, generate_key, load_keys
from vinga_server.config.store import APPLY_LIMIT, TOO_MANY_ENTRIES, ConfigStore
from vinga_server.db import DATABASE_FILENAME, open_database
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

# The other sentinel, planted where a refusal's own INPUT can carry a
# credential-shaped value: the body of a write that will be refused, the
# fragment that will not parse, the entries of a document over the
# limit. A refusal that echoed any part of what it was given would carry
# this, and the checks below look for it on every surface a value can
# come out on. Distinct from `SECRET`, so a failure says which of the
# two paths leaked.
PLANTED = "sk-planted-9b3e7d41-never-a-real-credential"


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


# Every surface a planted value could come out on
#
# A refusal is printed, and it is also logged, and it is also carried by
# an exception until something catches it. This lane runs the server in
# a thread of this process, so all three are reachable from inside a
# test, and a check that looked at the printed half alone would pass a
# server that wrote the value into a log record.


class Watched:
    """Every log record made while one case ran, whichever thread made
    it and whichever logger it was made on."""

    def __init__(self) -> None:
        self.records: list[logging.LogRecord] = []

    def everything(self) -> str:
        """Every record rendered WHOLE, which is the point of collecting
        the records rather than the formatted lines.

        A value can ride a record in four places: interpolated into the
        message, held unformatted in `args` for a handler to interpolate
        later, attached as an extra attribute (which is how this server's
        structured events carry their fields), or inside an exception on
        `exc_info`. All four are rendered here, the last one through its
        whole traceback and chain.
        """
        parts: list[str] = []
        for record in self.records:
            attributes = dict(record.__dict__)
            exception = attributes.pop("exc_info", None)
            try:
                parts.append(record.getMessage())
            except (TypeError, ValueError):
                # A record whose arguments do not fit its template is
                # still a record that carries them.
                parts.append(repr((record.msg, record.args)))
            parts.append(repr(attributes))
            if exception:
                parts.append("".join(traceback.format_exception(*exception)))
        return "\n".join(parts)

    def threads(self) -> set[str]:
        """Which threads made them."""
        return {record.threadName for record in self.records}

    def elsewhere(self) -> list[logging.LogRecord]:
        """The records some other thread made, which in this lane means
        the server's.

        What a case asserts this for is that its log check is looking at
        the server at all. Every command here runs on the test's own
        thread and the server runs on its own, so a collection holding
        nothing but this thread's records is one that would not have
        seen a value the server logged.
        """
        here = threading.current_thread().name
        return [record for record in self.records if record.threadName != here]


class _Collector(logging.Handler):
    def __init__(self, into: list[logging.LogRecord]) -> None:
        super().__init__(level=logging.NOTSET)
        self._into = into

    def emit(self, record: logging.LogRecord) -> None:
        self._into.append(record)


# The root catches everything that propagates, which is this server's own
# channels and every library's. Uvicorn's three do not propagate: it
# installs its own dictConfig at startup with `propagate: False`, so the
# handler is attached to them by name as well. Their LEVEL is left where
# uvicorn set it, deliberately: raising it would send uvicorn's access
# lines to the stream its handler captured when the module's server
# booted, which is another test's captured stderr.
WATCHED_LOGGERS = ("", "uvicorn", "uvicorn.error", "uvicorn.access")

# Two the server's own logging setup pins above DEBUG, lowered for the
# length of a case. They are the client half of every command here, so
# what they say is where a value the CLI sent would surface if anything
# on this side wrote one down.
LOUD_LOGGERS = ("httpx", "httpcore")


@pytest.fixture
def watched() -> Iterator[Watched]:
    """What was logged while this case ran.

    `caplog` alone was tried first and is not enough: pytest's handler
    sits on the root logger, and uvicorn's own loggers are configured
    not to propagate to it, so a record uvicorn made would have been
    missed. This attaches one handler to the root and to each of
    uvicorn's three, and raises the root's level to DEBUG so that a
    debug record from any of this server's channels is collected rather
    than filtered before it reaches a handler.

    What it collects is not hypothetical on either side: the client's
    transport chatter arrives on this thread, and
    `test_a_board_is_onboarded_by_the_code_on_its_screen` asserts that
    records made on the SERVER's thread arrive too, which is the half a
    fixture cannot claim for itself.
    """
    watching = Watched()
    handler = _Collector(watching.records)
    loggers = [logging.getLogger(name) for name in WATCHED_LOGGERS]
    root = logging.getLogger()
    levels = {name: logging.getLogger(name).level for name in LOUD_LOGGERS}
    root_level = root.level
    root.setLevel(logging.DEBUG)
    for name in LOUD_LOGGERS:
        logging.getLogger(name).setLevel(logging.DEBUG)
    for logger in loggers:
        logger.addHandler(handler)
    try:
        yield watching
    finally:
        for logger in loggers:
            logger.removeHandler(handler)
        for name, level in levels.items():
            logging.getLogger(name).setLevel(level)
        root.setLevel(root_level)


def leaked(sentinel: str, **surfaces: str) -> list[str]:
    """Which of the surfaces a planted value came out on, by name.

    A list rather than an assertion, so that a failure says which
    surface leaked rather than only that one did.
    """
    return sorted(name for name, text in surfaces.items() if sentinel in text)


def carried(exc: BaseException) -> str:
    """Everything an exception chain holds, one attribute deeper than
    the exceptions themselves.

    `tests.support.config_cli.chain` renders each exception's repr and
    its str, which is what the unit lane's no-leak cases need. It is not
    enough here, and the reason is worth stating rather than inheriting:
    PyYAML's marked errors keep the WHOLE buffer they were parsing on a
    mark object hung off the exception, and neither the exception's repr
    nor its str renders that buffer. A refusal raised inside the handler
    for one of those would carry the submitted document behind it and
    read as clean to a shallower walk. This goes one attribute deeper,
    which is where a chain walker would find it, and the deliberate-leak
    run that proved it is in the M3 record.
    """
    parts: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        parts += [repr(current), str(current)]
        for name, value in vars(current).items():
            parts.append(f"{name}={value!r}")
            held = getattr(value, "__dict__", None)
            if held:
                parts.append(repr(held))
        current = current.__cause__ or current.__context__
    return "\n".join(parts)


def chain_of(argv: Sequence[str]) -> str:
    """What the refusal for this command line carries, including what a
    chain walker would find behind it.

    `cli._parsed` is reached for the reason the unit lane reaches it
    (M1's review round, finding 2): `main` catches this exception by
    design and answers with a sentence and an exit code, so no
    caller-facing surface holds it, and a claim about what it carries
    cannot otherwise be stated. Running the command a second time is
    free here, because every command line this is used on is one that
    changes nothing.
    """
    with pytest.raises(ConfigError) as caught:
        cli._parsed(list(argv))
    return carried(caught.value)


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
    deployed: Live, capsys: pytest.CaptureFixture[str], watched: Watched
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

    This case carries one more claim, for the leak checks elsewhere in
    the file rather than for itself: that the log capture reaches the
    SERVER's thread. An unbound check-in is the one thing in this lane
    that makes the server log, so it is where the collection can be
    shown to hold a record no code in this thread made.
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

    # The capture reaches the server: the warning a board with no agent
    # earns was made on the thread uvicorn runs on, and this thread made
    # no record of it. Every leak check in this file rests on that being
    # true, and this is where it is a fact rather than an assumption.
    made_there = watched.elsewhere()
    assert made_there
    assert {record.threadName for record in made_there} != {threading.current_thread().name}
    assert any(record.name.startswith("vinga_server.") for record in made_there)


# A credential's whole life over the wire
#
# The one surface where what crosses the connection matters as much as
# what comes back: the value is read here, sent as a request body, stored
# encrypted, and never travels again. Both doors it is entered through
# are driven, because they are different code paths on this side of the
# socket and the same one on the other.


def test_a_credential_is_stored_masked_and_cleared(
    deployed: Live, capsys: pytest.CaptureFixture[str], watched: Watched
) -> None:
    """Two slots on two kinds, entered the two ways the command takes,
    read back masked, exported as the commands that refill them, and
    cleared.

    The entity the provider credential lands on is the one no agent
    references, so a reload never builds it and the value is never asked
    for by anything but this test.

    Every step keeps BOTH streams rather than the one it makes an
    assertion about, and the whole of the case is swept at the foot: a
    value that came out on the stream a step was not looking at is
    exactly the shape a leak takes.
    """
    seen: list[str] = []

    def kept(expected: str) -> str:
        """One command's two streams, both kept for the sweep below."""
        captured = capsys.readouterr()
        assert captured.out.startswith(expected)
        seen.extend((captured.out, captured.err))
        return captured.out

    assert run("set-secret", "provider", "llm", "spare", "api_key", "--from-env", SECRET_ENV) == 0
    kept("wrote ")

    assert run("set-secret", "mcp-server", "weather", "headers.Authorization", stdin=SECRET) == 0
    kept("wrote ")

    assert run("show", "provider", "llm", "spare") == 0
    entity = kept("")
    assert f"api_key: {MASK}" in entity

    assert run("show") == 0
    everything = kept("")
    # The environment reference the stored value displaces is marked
    # rather than left silent, which is #192's marker seen from the far
    # end of a connection.
    assert "used instead of api_key_env: ANTHROPIC_API_KEY" in everything

    assert run("export") == 0
    exported = kept("")
    # A credential never travels in a read, so what the document carries
    # is the command that enters it, and never the mask, which a
    # creating write would refuse.
    # The marker is part of the exported command since the M2 round's
    # finding 6: a legal leading-dash identity must survive the argv.
    assert f"{cli.PROGRAM} set-secret provider -- llm spare api_key" in exported
    assert f"{cli.PROGRAM} set-secret mcp-server -- weather headers.Authorization" in exported
    assert MASK not in exported

    assert run("clear-secret", "provider", "llm", "spare", "api_key") == 0
    kept("wrote ")
    assert run("clear-secret", "mcp-server", "weather", "headers.Authorization") == 0
    kept("wrote ")

    assert run("show", "provider", "llm", "spare") == 0
    cleared = kept("")
    assert MASK not in cleared
    # And what the stored value was covering is back in view.
    assert "api_key_env: ANTHROPIC_API_KEY" in cleared

    # The sweep. Nine commands' worth of both streams, and every log
    # record any thread made while they ran: the value was read from a
    # variable on this side, sent as a request body, encrypted by the
    # server and answered for four times, and none of that may have
    # written it down. The two reads above are rendered response bodies,
    # so the bodies this case sees are in here too.
    assert leaked(SECRET, streams="\n".join(seen), logs=watched.everything()) == []


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
    """The five commands that contact no server, run in the environment
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

    # The one of the five that reads a directory as well as the command
    # tree, and so the one with something to lose by being run from a
    # working directory that is not the lane's.
    assert run("cli-reference") == 0
    rendered = capsys.readouterr().out
    assert f"### `{cli.PROGRAM} apply`" in rendered

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
# What is asserted about each is the WHOLE of stderr, not a phrase in
# it. A refusal composed by the API is serialized, sent, parsed and
# printed before an operator sees it, and every step of that is a place a
# sentence can be lost, truncated, replaced by a middlebox's page, or
# added to. A substring check passes all four of the last one's shapes,
# which is what a value appended to a sentence is.
#
# The sentences are taken from the constants that hold them wherever one
# is published, and written out where the text is assembled at its raise
# site and has no constant to import. Written out is not a second home
# for the wording: what a refusal says is decided by the module that
# raises it, and a case here that disagrees is this file being wrong.
#
# The `wire` column says where each refusal is composed, and it is
# load-bearing rather than documentation: the second case below runs
# every one of these against an address nothing listens on, where a
# refusal the server composes cannot happen and the transport sentence
# takes its place, and a refusal this side composes is unchanged.

USAGE = cli.usage_line(cli.SECRET_NEVER_AN_ARGUMENT)

UNRESOLVED = "the change was refused; it would leave these references unresolved:"

REFUSED_DOCUMENT = "REFUSED_DOCUMENT"

MISSING_CONFIG = "/nowhere/at/all.yaml"


class Refusal(NamedTuple):
    """One family's refusal: what to type, the whole of what stderr says
    afterwards, and which end of the connection composed it."""

    family: str
    argv: tuple[str, ...]
    stderr: str
    wire: bool


REFUSALS: tuple[Refusal, ...] = (
    Refusal(
        "set",
        ("set", "agent", "refused-agent", f"prompt={PLANTED}", "llm=nowhere"),
        UNRESOLVED
        + "\n  - agents.refused-agent.llm: names no llm provider that exists, and the "
        "name is not quoted back (defined: brain, spare)",
        True,
    ),
    Refusal("delete", ("delete", "agent", "no-such-agent"), entities.NO_SUCH_AGENT, True),
    Refusal("bind-device", ("bind-device", "not-a-mac", "sam"), NOT_A_MAC, True),
    Refusal(
        "add-device",
        ("add-device", "000000", "sam"),
        "no device is waiting with that activation code. A code lasts ten minutes and "
        "is retired the moment it is claimed, and a device that has been waiting longer "
        "is already showing a fresh one: read the code currently on the device's screen "
        "and use that. `vinga-server config pending` lists the codes this server is "
        "showing right now.",
        True,
    ),
    Refusal(
        "apply",
        ("apply", "-f", REFUSED_DOCUMENT),
        "document: the top-level keys of an applied document are the sections of the "
        "domain configuration, which are " + ", ".join(DOMAIN_KEYS) + ". Something else "
        "was written, and it is not quoted back",
        True,
    ),
    Refusal("pending", ("pending", "extra"), USAGE, False),
    Refusal("status", ("status", "extra"), USAGE, False),
    Refusal(
        "prompt",
        ("prompt", "no-such-agent"),
        "this server is not serving an agent of that name. The agents a server can "
        "serve are the agents of the world it has installed, so one written since is "
        "served by the reload that installs it (`vinga-server config reload`), and one "
        "that never existed is a name nothing answers to. `vinga-server config list` "
        "shows the agents that are stored.",
        True,
    ),
    Refusal("reload", ("reload", "extra"), USAGE, False),
    Refusal(
        "ota-url",
        ("ota-url", "--config", MISSING_CONFIG),
        f"config file not found: {MISSING_CONFIG}",
        False,
    ),
    Refusal(
        "set-default-agent",
        ("set-default-agent", "no-such-agent"),
        UNRESOLVED
        + "\n  - default_agent: names no agent that exists, and the name is not quoted "
        "back (defined: sam)",
        True,
    ),
    Refusal("clear-default-agent", ("clear-default-agent", "extra"), USAGE, False),
    Refusal(
        "set-secret",
        ("set-secret", "provider", "llm", "no-such", "api_key", "--from-env", SECRET_ENV),
        entities.NO_SUCH_PROVIDER,
        True,
    ),
    Refusal(
        "clear-secret",
        ("clear-secret", "provider", "llm", "no-such", "api_key"),
        "providers: no secret is stored for that slot",
        True,
    ),
    Refusal("list", ("list", "extra"), USAGE, False),
    Refusal(
        "schema",
        ("schema", "nonsense"),
        '"nonsense" is not a documented entity; expected one of: '
        + ", ".join(docgen.entity_names()),
        False,
    ),
    Refusal("reference", ("reference", "extra"), USAGE, False),
    Refusal("openapi", ("openapi", "extra"), USAGE, False),
    Refusal("cli-reference", ("cli-reference", "extra"), USAGE, False),
    Refusal(
        "show", ("show", "provider", "llm", "no-such"), entities.NO_SUCH_PROVIDER, True
    ),
    Refusal("export", ("export", "agent", "no-such"), entities.NO_SUCH_AGENT, True),
)

# What a client that cannot reach the API says, pinned at both ends
# rather than written out: the address is this case's own, and an
# appended value would fall outside the tail.
UNREACHABLE_HEAD = "cannot reach the configuration API at "

UNREACHABLE_TAIL = "covers show, delete, clear-secret and set-secret."


def test_every_family_of_the_grammar_has_a_refusal() -> None:
    """The refusal table, held to the registration table.

    A family with no refusal here would be a family whose sentences
    nothing has ever seen cross a connection, and the way that happens
    is a command added to the grammar rather than a row deleted from
    this file.
    """
    assert {row.family for row in REFUSALS} == {row.words[0] for row in cli.COMMANDS}


def refusing(argv: Sequence[str], directory: Path) -> tuple[str, ...]:
    """One refusal's command line, with the document the apply case
    needs written where it can be found.

    The document carries the planted credential in the section it will
    be refused for, which is what makes the apply row's leak check about
    something: a refusal that quoted the document back would carry it.
    """
    return tuple(
        written(directory, "refused.yaml", {"nonsense_section": {"note": PLANTED}})
        if word == REFUSED_DOCUMENT
        else word
        for word in argv
    )


@pytest.mark.parametrize("row", REFUSALS, ids=[row.family for row in REFUSALS])
def test_one_refusal_of_each_family_arrives_intact(
    deployed: Live,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    watched: Watched,
    row: Refusal,
) -> None:
    """A refusal is one sentence on stderr and exit 1, whichever end of
    the connection composed it, and it is the WHOLE of stderr.

    Two of these rows hand the command a credential-shaped value in the
    place their own input can hold one: the body of the refused write,
    and the entries of the refused document. A third sends a real one,
    because `set-secret --from-env` reads the variable before it learns
    there is no such entity to store it on. All three are checked on
    every surface a value can come out on: the two streams, the log
    records this server made while the case ran, whichever thread made
    them, and the exception the refusal is carried by, chain included.
    """
    argv = refusing(row.argv, tmp_path)

    assert run(*argv) == 1

    captured = capsys.readouterr()
    assert captured.err == row.stderr + "\n"
    # Nothing on stdout, so a script reading a command's output reads
    # nothing rather than half an answer.
    assert captured.out == ""

    surfaces = {
        "stdout": captured.out,
        "stderr": captured.err,
        "logs": watched.everything(),
        "chain": chain_of(argv),
    }
    assert leaked(PLANTED, **surfaces) == []
    assert leaked(SECRET, **surfaces) == []


@pytest.mark.parametrize("row", REFUSALS, ids=[row.family for row in REFUSALS])
def test_a_refusal_the_server_composes_needs_the_server(
    deployed: Live,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    watched: Watched,
    row: Refusal,
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

    argv = ("--api-url", nowhere, *refusing(row.argv, tmp_path))

    assert run(*argv) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    if row.wire:
        # Both ends pinned, since the middle names this case's own
        # address: nothing may precede the sentence and nothing may
        # follow it.
        assert captured.err.startswith(f"{UNREACHABLE_HEAD}{nowhere}:")
        assert captured.err.endswith(f"{UNREACHABLE_TAIL}\n")
    else:
        assert captured.err == row.stderr + "\n"

    surfaces = {
        "stdout": captured.out,
        "stderr": captured.err,
        "logs": watched.everything(),
        "chain": chain_of(argv),
    }
    assert leaked(PLANTED, **surfaces) == []
    assert leaked(SECRET, **surfaces) == []


def test_a_fragment_that_will_not_parse_never_travels(
    deployed: Live, tmp_path: Path, capsys: pytest.CaptureFixture[str], watched: Watched
) -> None:
    """The other end of the refusal spectrum from the table above: a
    fragment is read, parsed and checked before a connection is opened,
    so a broken one is refused with the entity it was aimed at still
    exactly as it was.

    The fragment carries the planted credential on the line above the
    one it breaks on, which is where an operator's would be: a file
    being edited holds the value already when the edit that breaks it is
    saved. What a parser says about a file it could not read is the one
    place a value gets quoted by accident, so the refusal is asserted
    whole and swept on every surface.
    """
    broken = tmp_path / "broken.yaml"
    broken.write_text(f"prompt: {PLANTED}\nmcp: [\n", encoding="utf-8")
    argv = ("set", "agent", "sam", "-f", str(broken))

    assert run(*argv) == 1
    refused = capsys.readouterr()
    assert refused.err == (
        f"invalid YAML in {broken} at line 3, column 1. Nothing of what it holds is "
        f"quoted back: a source that will not parse is one nothing here has validated, "
        f"and what a parser says about one repeats the tag or the key it stopped on\n"
    )
    assert refused.out == ""
    assert (
        leaked(
            PLANTED,
            stdout=refused.out,
            stderr=refused.err,
            logs=watched.everything(),
            chain=chain_of(argv),
        )
        == []
    )

    assert run("show", "agent", "sam") == 0
    assert document(capsys.readouterr().out)["prompt"] == "You are Sam."


# The two bounds `apply` rides, over real HTTP
#
# Both of them are about a transaction whose length nothing about the
# request predicts, and both are what a mock transport cannot show: one
# is a client that must not give up on a write the server is still
# committing, and the other is the request hygiene that keeps the
# transaction from being unbounded in the first place.
#
# These two ask for a store nobody else wrote, because what each of them
# asserts is what the store holds afterwards.

# A read bound short enough that this server cannot meet it. Deliberately
# short so the test finishes, the way `test_config_api.py` shortens the
# database's busy timeout for the same reason: what is being compared is
# `apply` against an ordinary read, on the same server, through the same
# client implementation, with the same store behind it.
IMPATIENT_S = 0.005


def impatient(words: tuple[str, ...], bound: float) -> tuple[cli.Command, ...]:
    """The registration table with one command's read bound cut short.

    The bound is a fact of the act on that command's row, which is where
    a command's own answer about how long its endpoint may take is
    stated, so this is where a test that needs a different answer says
    so. The table is read and rewritten rather than a second one
    written: every other row is the one the grammar ships.
    """
    return tuple(
        replace(row, does=replace(row.does, read_timeout_s=bound))
        if row.words == words
        else row
        for row in cli.COMMANDS
    )


def test_a_large_document_is_waited_out_however_long_it_takes(
    isolated: Live,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A batch at the top of the accepted range, against a client that
    is not allowed to give up.

    `apply`'s read timeout is None, and the reason is that no finite one
    can be derived: the transaction loads the whole existing store and
    validates the whole resulting one, whose size no request bound
    limits, and a client that gave up on a write the server then
    committed would leave nobody able to say what is stored.

    The comparison is what makes that observable in a lane's wall clock.
    An ordinary read of this same server, through the same client
    implementation, with its bound cut to a value this server cannot
    meet, gives up and says so. The apply, whose request demonstrably
    took longer than that bound, does not. (Each command builds a client
    of its own and closes it, so what the two share is the server and
    the code that talks to it, not one open connection.)
    """
    entries = APPLY_LIMIT - 1
    many = {f"agent-{number:03d}": {"prompt": "You are one of many."} for number in range(entries)}
    document_path = written(tmp_path, "large.yaml", {"agents": many})
    monkeypatch.setattr(cli, "COMMANDS", impatient(("show",), IMPATIENT_S))

    started = time.monotonic()
    assert run("apply", "-f", document_path, "--api-url", isolated.api_url) == 0
    took = time.monotonic() - started

    applied = capsys.readouterr()
    outcomes = [line.split(": ")[-1] for line in applied.out.splitlines()]
    assert len(outcomes) == entries
    assert set(outcomes) == {"wrote"}
    assert took > IMPATIENT_S

    # And the bound is a real one: the same server, asked for a read
    # that carries it, gives up rather than answering.
    assert run("show", "--api-url", isolated.api_url) == 1
    assert "cannot reach the configuration API" in capsys.readouterr().err


def test_an_over_limit_document_is_refused_with_the_store_unmutated(
    isolated: Live, tmp_path: Path, capsys: pytest.CaptureFixture[str], watched: Watched
) -> None:
    """The other side of the bound, read back rather than assumed.

    The entry count is what keeps a transaction from being unbounded, so
    it is refused before anything is prepared and before anything is
    written. What proves the second half is reading the store back over
    the same server: an empty store afterwards is the whole claim.

    Every entry of the document carries the planted credential, because
    the document that reaches this bound is a generated one and what a
    generated document holds is whatever it was generated from. A
    refusal that quoted any part of it back would carry five hundred
    copies of the value, so the sentence is asserted whole and swept.
    """
    document_path = written(
        tmp_path,
        "too-large.yaml",
        {
            "agents": {
                f"agent-{number:03d}": {"prompt": PLANTED}
                for number in range(APPLY_LIMIT + 1)
            }
        },
    )
    argv = ("apply", "-f", document_path, "--api-url", isolated.api_url)

    assert run(*argv) == 1

    refused = capsys.readouterr()
    assert refused.err == TOO_MANY_ENTRIES + "\n"
    assert refused.out == ""
    # Nothing of the document is quoted back, which is what makes the
    # sentence safe to print whatever a generated document holds: not
    # the value in every entry, and not the entry names either.
    assert (
        leaked(
            PLANTED,
            stdout=refused.out,
            stderr=refused.err,
            logs=watched.everything(),
            chain=chain_of(argv),
        )
        == []
    )
    assert "agent-000" not in refused.err

    assert run("show", "--api-url", isolated.api_url) == 0
    assert document(capsys.readouterr().out)["agents"] == {}


def test_the_lane_s_server_booted_from_the_environment_alone(
    deployed: Live, capsys: pytest.CaptureFixture[str]
) -> None:
    """No configuration file anywhere, which is decision 9's claim about
    the quick start.

    The server every case above talked to was composed by
    `load_boot_config()` with no path and no `VINGA_CONFIG`, so its file
    half came from the settings machinery reading `VINGA_SERVER__*` and
    the packaged defaults, and the one variable that was set is where
    the database goes. Three things say that worked: the variable was
    not left lying in this process's environment for something else to
    have supplied, the server answers on its own port, and the database
    in the directory the variable named is the one holding what this
    lane wrote through the API.
    """
    assert CONFIG_ENV not in os.environ

    with urllib.request.urlopen(f"{deployed.origin}/healthz", timeout=10) as response:
        assert response.status == 200

    assert (deployed.directory / DATABASE_FILENAME).is_file()
    engine = open_database(deployed.directory)
    try:
        stored = ConfigStore(engine, load_keys()).load()
    finally:
        engine.dispose()
    assert "sam" in stored.domain.agents

    # And the CLI, which resolved this server's address from the
    # environment too, is reading the same store.
    assert run("show", "agent", "sam") == 0
    assert document(capsys.readouterr().out)["prompt"] == stored.domain.agents["sam"].prompt


def test_the_leak_check_notices_a_value_on_every_surface(
    watched: Watched, capsys: pytest.CaptureFixture[str]
) -> None:
    """The absence checks above, checked.

    Every one of them asserts that something is NOT somewhere, which is
    the shape of assertion that keeps passing after the machinery under
    it stops working: a renderer that dropped a record's arguments, a
    capture that collected nothing at all, a chain walker that stopped
    at the first exception. So this plants the value in each of those
    places deliberately and asserts the check names every one of them.

    The three log shapes are the three ways a value rides a record, and
    the middle one is why `everything()` renders `args` rather than the
    formatted line: a handler that never formatted the record would
    still be holding the value.
    """
    channel = logging.getLogger("vinga_server.lane_leak_check")
    channel.warning("interpolated into the message: %s", PLANTED)
    channel.warning("held on an extra attribute", extra={"planted": PLANTED})
    try:
        try:
            raise ValueError(PLANTED)
        except ValueError:
            raise RuntimeError("what a chain walker has to go behind") from None
    except RuntimeError:
        channel.warning("inside an exception attached to a record", exc_info=True)

    print(PLANTED)
    print(PLANTED, file=sys.stderr)
    captured = capsys.readouterr()

    assert leaked(
        PLANTED,
        stdout=captured.out,
        stderr=captured.err,
        logs=watched.everything(),
    ) == ["logs", "stderr", "stdout"]

    # And the fourth surface, which has no stream of its own: a refusal
    # that says nothing itself but carries something that does.
    refusal = ConfigError("a sentence that quotes nothing")
    refusal.__context__ = ValueError(PLANTED)
    assert leaked(PLANTED, chain=carried(refusal)) == ["chain"]

    # The value was planted here on purpose, so the three records above
    # are this case's own and belong to no server.
    assert not watched.elsewhere()


def defined_here() -> set[str]:
    """Every test this module defines, by name."""
    return {name for name in globals() if name.startswith("test_")}


def test_the_lane_drove_every_command_of_the_registration_table(
    request: pytest.FixtureRequest,
) -> None:
    """The completeness claim, and the reason a command cannot skip this
    lane quietly.

    The inventory is `cli.COMMANDS`, which is the grammar itself rather
    than a description of it: a command exists exactly when it has a row
    there. What is held against it is what actually ran and succeeded,
    recorded by `run` off each command line, so this cannot be satisfied
    by adding a name to a list.

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

    missing = sorted(" ".join(row.words) for row in cli.COMMANDS if row.words not in DRIVEN)
    assert not missing, (
        "these commands are registered in cli.COMMANDS and no case in this lane ran "
        f"them successfully: {missing}"
    )

    # And every group word was reached through one of them, which is the
    # other half of the tree the table describes.
    assert set(cli.GROUPS) <= {words[0] for words in DRIVEN}
