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
- `import` has no read timeout at all (`cli.IMPORT_READ_TIMEOUT_S`), which
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

import asyncio
import contextlib
import io
import itertools
import json
import logging
import os
import shlex
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
from collections.abc import Iterator, Sequence
from dataclasses import replace
from pathlib import Path
from typing import NamedTuple

import pytest
import yaml

from tests.conftest import reset_database
from tests.support.config_cli import document, registered
from tests.support.deployment import (
    BOARD,
    CONFIG_ENV,
    Live,
    check_in,
    serving,
)
from vinga_server.build_info import revision
from vinga_server.config import ConfigError, cli, docgen, entities, server_reference
from vinga_server.config.cli import installed_version
from vinga_server.config.loader import CONFIG_FROM_FLAG, CONFIG_NOT_FOUND
from vinga_server.config.models import (
    API_MOUNT_PATH,
    DOMAIN_KEYS,
    NOT_A_MAC,
    DatabaseConfig,
)
from vinga_server.config.secrets import (
    MASK,
    MASTER_KEY_ENV,
    SecretLocation,
    generate_key,
    load_keys,
)
from vinga_server.config.store import APPLY_LIMIT, TOO_MANY_ENTRIES, ConfigStore
from vinga_server.conversations.records import TurnRecord
from vinga_server.conversations.store import ConversationStore
from vinga_server.db import open_database
from vinga_server.device_endpoint import SUPPLIED_ENDPOINT
from vinga_server.memory.scopes import MemoryScope
from vinga_server.memory.store import open_memory
from vinga_server.ota import OTA_PATH
from vinga_server.simulator import board, conversation, utterance

# The variable a secret set's `--from-env` is pointed at. Not a real
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

# The two boards the session verbs' record is written for, and the one
# thread both of its sessions fed. Their own constants rather than the
# lane's other MACs, so a purge by device here cannot reach a row
# another case is about.
SESSION_MAC = "02:00:00:00:00:21"

SESSION_OTHER_MAC = "02:00:00:00:00:22"

LANE_CONVERSATION = "7b1c2d3e4f50617283a4b5c6d7e8f900"

# The thread the conversation verbs' own record is written for. Its own
# id rather than the one above, so an erasure in either case cannot
# reach the rows the other is about.
LANE_THREAD = "0d1e2f3a4b5c6d7e8f90a1b2c3d4e5f6"

# The thread whose ledger the memory verbs read, distinct from the one
# above because that one is erased mid-case and a ledger keyed to a
# deleted thread would go with it.
LANE_MEMORY_THREAD = "7e8f90a1b2c3d4e5f60d1e2f3a4b5c6d"


def session_manifest(device: str) -> dict[str, object]:
    """The manifest a session opens its row with, as the device session
    hands it over."""
    return {
        "started_at": "2026-08-15T10:00:00+00:00",
        "server": {"version": "0.1.0", "revision": "abc1234"},
        "device": {"mac": device, "client": "lane"},
        "protocol": "1",
        "agent": "sam",
        "agents": ["sam"],
        "providers": {"llm": {"name": "mock", "type": "mock"}},
    }


@pytest.fixture(scope="module")
def live(module_database: str) -> Iterator[Live]:
    """The lane's server, booted once for the whole module.

    Once rather than per test, because the boot is the expensive part
    and the store is what the tests are about: an operator's session is
    a sequence of commands against one deployment, and that is what this
    is. The two tests that need a store nobody else has wrote to ask for
    `isolated` instead.

    On a database of this module's own, which is the whole reason
    `module_database` exists: the lane clears this worker's database
    between tests, and a module that configured a deployment in its
    first fixture would find it emptied under the second test.

    The environment is this module's for the length of it: the address
    the CLI resolves, the encryption key both halves share, and no
    configuration file anywhere.
    """
    patch = pytest.MonkeyPatch()
    patch.delenv(CONFIG_ENV, raising=False)
    patch.setenv(MASTER_KEY_ENV, generate_key())
    patch.setenv(SECRET_ENV, SECRET)
    try:
        with serving(DatabaseConfig(name=module_database)) as running:
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
def isolated(spare_database: str) -> Iterator[Live]:
    """A second server on a store of its own, for the two tests whose
    claim is about what the store holds afterwards.

    Reached by `--api-url`, because the environment names the lane's own
    server and the point of this one is that nothing else wrote to it.
    """
    with serving(DatabaseConfig(name=spare_database)) as running:
        yield running


# What the lane drove
#
# The registration table is the inventory, and this is what actually ran
# against it. Recorded from the command line rather than declared beside
# it, so the coverage list cannot say a command was driven that was not:
# the only way into this set is a command that ran and succeeded.

# Which row a command line names is `tests.support.config_cli.registered`,
# imported above: this lane, the wheel lane and the spelling census all
# ask it, and a second implementation of longest-prefix matching would
# be the pending bug the design guide names.

DRIVEN: set[tuple[str, ...]] = set()


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
        cli._parsed(list(argv), cli.DISPATCHED)
    return carried(caught.value)


KNOWN_MAC = "aa:bb:cc:dd:ee:ff"

WAITING_MAC = "11:22:33:44:55:66"

# And the board the rename's case binds, which is its own for the reason
# the session verbs' two are theirs: what that case reads back is a
# binding rewritten by a transaction, and a board another case is about
# would make the reading ambiguous.
RENAMED_MAC = "02:00:00:00:00:31"

# And the board nobody owns, which presents its own documented default
# rather than a third address invented here.
SIMULATED_MAC = board.DEFAULT_MAC


# The deployment this lane configures
#
# Small on purpose: what the lane is about is the commands, and a
# document this size names every section, exercises every reference edge
# an import has to resolve in one transaction, and leaves the apply with
# something to build. The two MCP entries are granted by no agent, so
# nothing is ever connected for them and an apply starts and stops
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
    """The lane's store, configured through `config import` over the
    wire.

    The bootstrap is the acceptance case of the whole issue, and it is a
    fixture rather than a test because everything below stands on it:
    one document, one transaction, every section named, against a server
    that booted on an empty database.

    An import and nothing else, which is the whole of what the verb does
    (#371), and what this lane's sequence is built on: the store and the
    running server are two different things for a while, so the agent
    written here is one the server is not serving until
    `test_the_running_server_is_read_after_an_apply` installs it, which
    is what that test is about.
    """
    document_path = written(tmp_path_factory.mktemp("import"), "deployment.yaml", DEPLOYMENT)
    assert run("import", "-f", document_path) == 0
    return live


def test_a_whole_deployment_imports_over_the_wire(
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
    """Idempotence over the wire: the document the fixture imported,
    imported again, reports every entry unchanged and writes nothing.

    The outcome listing is the assertion because it is the only order an
    import observably has, and `unchanged` on every line is the claim
    that the comparison happened rather than the rows being rewritten.
    """
    document_path = written(tmp_path, "deployment.yaml", DEPLOYMENT)

    assert run("import", "-f", document_path) == 0

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

    assert run(kind, "set", *identity, "-f", path) == 0
    acknowledged = capsys.readouterr()
    assert acknowledged.out.startswith("wrote ")
    # Every write says when it takes effect, and it says it on stderr so
    # that stdout holds the acknowledgement alone.
    assert acknowledged.err.strip()

    assert run(kind, "show", *identity) == 0
    shown = document(capsys.readouterr().out)
    assert fragment.items() <= shown.items()

    assert run(kind, "export", *identity) == 0
    exported = capsys.readouterr().out
    # The header names the command that writes one back, and the body
    # under it is the same read `show` renders: export is the writable
    # projection of the display one, not a second read.
    assert exported.startswith("# One ")
    assert f"{cli.PROGRAM} {kind} set " in exported
    assert document(exported) == shown

    assert run(kind, "set", *identity, *pairs) == 0
    assert capsys.readouterr().out.startswith("wrote ")

    assert run(kind, "show", *identity) == 0
    assert document(capsys.readouterr().out) == shown

    if not deletable:
        return

    assert run(kind, "delete", *identity) == 0
    assert capsys.readouterr().out.startswith("wrote ")

    assert run(kind, "show", *identity) == 1
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
    assert run("device", "bind", KNOWN_MAC.upper().replace(":", "-"), "sam") == 0
    bound = capsys.readouterr()
    assert bound.out.startswith("wrote ")
    assert bound.err.strip()

    assert run("device", "show", KNOWN_MAC) == 0
    assert document(capsys.readouterr().out) == {"agents": ["sam"]}

    assert run("device", "delete", KNOWN_MAC) == 0
    assert capsys.readouterr().out.startswith("wrote ")

    assert run("device", "show", KNOWN_MAC) == 1
    assert capsys.readouterr().out == ""


def test_a_board_is_onboarded_by_the_code_on_its_screen(
    deployed: Live, capsys: pytest.CaptureFixture[str], watched: Watched
) -> None:
    """The whole onboarding ceremony against a running server: the
    default agent, the code, the listing and the claim.

    The two check-ins are what make the settings' claim observable. A
    binding and the default agent are read as a device asks for them
    rather than at a restart, so a board checking in after
    `default-agent set` is answered as a configured device and mints no
    code at all, and the same board after `default-agent clear` is
    unbound and gets one. Nothing in-process can show that: it is the
    running server re-reading the database between two requests.

    This case carries one more claim, for the leak checks elsewhere in
    the file rather than for itself: that the log capture reaches the
    SERVER's thread. An unbound check-in is the one thing in this lane
    that makes the server log, so it is where the collection can be
    shown to hold a record no code in this thread made.
    """
    assert run("default-agent", "set", "sam") == 0
    assert capsys.readouterr().out.startswith("wrote ")
    assert isinstance(check_in(deployed, WAITING_MAC), board.Unwelcome)

    assert run("default-agent", "clear") == 0
    assert capsys.readouterr().out.startswith("wrote ")
    waiting = check_in(deployed, WAITING_MAC)
    assert isinstance(waiting, board.Activating)
    code = waiting.code
    assert code.isdigit()

    assert run("device", "pending", "list") == 0
    waiting = capsys.readouterr().out
    assert code in waiting
    assert WAITING_MAC in waiting
    assert BOARD in waiting

    assert run("device", "pending", "claim", code, "sam") == 0
    claimed = capsys.readouterr()
    assert claimed.out.startswith("wrote ")
    assert claimed.err.strip()

    assert run("device", "show", WAITING_MAC) == 0
    assert document(capsys.readouterr().out) == {"agents": ["sam"]}

    # And the code is retired with the claim, so the board that was
    # waiting is no longer waiting for anything.
    assert run("device", "pending", "list") == 0
    assert code not in capsys.readouterr().out

    # The capture reaches the server: the warning a board with no agent
    # earns was made on the thread uvicorn runs on, and this thread made
    # no record of it. Every leak check in this file rests on that being
    # true, and this is where it is a fact rather than an assumption.
    made_there = watched.elsewhere()
    assert made_there
    assert {record.threadName for record in made_there} != {threading.current_thread().name}
    assert any(record.name.startswith("vinga_server.") for record in made_there)


def test_an_agent_is_renamed_with_its_binding_over_the_wire(
    deployed: Live, capsys: pytest.CaptureFixture[str]
) -> None:
    """The act that rewrites a name everywhere it is still read, over a
    real connection to a real store.

    An agent of this case's own and a board of its own, so that what the
    rename moved can be read back without touching the deployment every
    other case is written against, and the store is left as it was
    found because the export round trip further down is about what this
    lane wrote rather than what it borrowed.

    What makes it a lane's rather than an acceptance suite's: the
    binding and the agents row move in one transaction against a
    database a running server is reading, and both halves are read back
    through the verbs an operator would use rather than through the
    result the write returned.
    """
    assert run("agent", "set", "understudy", "prompt=You are standing in.") == 0
    assert run("device", "bind", RENAMED_MAC, "understudy") == 0
    capsys.readouterr()

    assert run("agent", "rename", "understudy", "stand-in") == 0
    renamed = capsys.readouterr()
    assert renamed.out == "wrote agent understudy renamed to stand-in\n"
    # A binding moved with the row, so the write says so on stderr the
    # way every write that is waiting somewhere does.
    assert renamed.err.strip()

    assert run("agent", "show", "stand-in") == 0
    assert "You are standing in." in capsys.readouterr().out

    assert run("device", "show", RENAMED_MAC) == 0
    assert document(capsys.readouterr().out) == {"agents": ["stand-in"]}

    # And nothing answers to the name it had, which is the half a
    # delete-and-create workaround could not reach without unbinding
    # the board first.
    assert run("agent", "show", "understudy") == 1
    gone = capsys.readouterr()
    assert gone.out == ""
    assert "Traceback" not in gone.err

    assert run("device", "delete", RENAMED_MAC) == 0
    assert run("agent", "delete", "stand-in") == 0
    capsys.readouterr()


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

    assert run(
        "provider", "secret", "set", "llm", "spare", "api_key", "--from-env", SECRET_ENV
    ) == 0
    kept("wrote ")

    assert run("mcp-server", "secret", "set", "weather", "headers.Authorization", stdin=SECRET) == 0
    kept("wrote ")

    assert run("provider", "show", "llm", "spare") == 0
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
    assert f"{cli.PROGRAM} provider secret set -- llm spare api_key" in exported
    assert f"{cli.PROGRAM} mcp-server secret set -- weather headers.Authorization" in exported
    assert MASK not in exported

    assert run("provider", "secret", "clear", "llm", "spare", "api_key") == 0
    kept("wrote ")
    assert run("mcp-server", "secret", "clear", "weather", "headers.Authorization") == 0
    kept("wrote ")

    assert run("provider", "show", "llm", "spare") == 0
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
# database, which is the whole reason they belong here: what answers
# them is a server that
# booted on an empty store and has been written to over HTTP ever since,
# so the difference a reload makes is observable in one session.


def test_the_running_server_is_read_after_an_apply(
    deployed: Live, capsys: pytest.CaptureFixture[str]
) -> None:
    """The apply, and the two reads that are about the process.

    `prompt` is the pin: the server this lane booted was given an empty
    domain half, so the agent every other test has been writing to is
    one it is not serving, and asking for its prompt is refused until an
    apply installs it. That sequence needs a server with a lifetime,
    which is what this lane has and the acceptance suites do not.
    """
    assert run("agent", "preview", "sam") == 1
    unserved = capsys.readouterr()
    assert unserved.out == ""
    assert "Traceback" not in unserved.err

    assert run("apply") == 0
    installed = capsys.readouterr().out
    assert "sam" in installed

    assert run("agent", "preview", "sam") == 0
    assembled = capsys.readouterr().out
    assert "You are Sam." in assembled
    # The fragment the agent includes is part of what the model is
    # given, which is the difference between this read and `show agent`.
    assert "The bins go out on Tuesday." in assembled

    # And the third read of the process: what the store still holds
    # that this server is not serving, which after the apply above is
    # nothing to name. Which is a sentence rather than an empty answer
    # (#425), with the live kinds' own sentence under it.
    assert run("diff") == 0
    compared = capsys.readouterr().out
    assert compared.startswith(cli.SERVING_THE_STORE + "\n")
    assert compared.endswith(cli.READ_AS_ASKED + "\n")

    assert run("mcp-server", "status") == 0
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


def test_info_names_the_deployment_it_reached(
    deployed: Live, capsys: pytest.CaptureFixture[str]
) -> None:
    """The read every other one in this section is a narrowing of: which
    deployment is answering at all.

    Over the wire because that is where the answer means something. The
    address this lane's CLI resolves comes out of the environment, the
    version and revision come out of the process uvicorn is running, and
    the onboarding URL is derived from the device-auth secret this
    module put in the environment before the server booted, so the key
    in it is a real one rather than a value a test wrote.
    """
    assert run("info") == 0

    printed = capsys.readouterr()
    said = printed.out
    assert said.startswith("vinga - Conversational AI. Sweded.\n")
    # The address this CLI actually contacted, which is the lane's own
    # server on an ephemeral port and not a default this test could have
    # guessed.
    assert f"configuration API: {deployed.api_url}" in said
    assert f"server version: {installed_version()}" in said
    assert f"server revision: {revision()}" in said
    # The URL a board is onboarded at, alone on its line under a label
    # that carries the provenance whole. The lane boots with device auth
    # on and no public_url, so what it names is a guessed origin with a
    # derived key after it, and the provenance says both.
    lines = said.splitlines()
    label = next(
        index
        for index, line in enumerate(lines)
        if line.startswith("the URL to type into a device's captive portal, ")
    )
    assert "guessed from the listen address" in lines[label]
    # The whole sentence, not the head of one: the fix it ends with is
    # the reason an operator reads a provenance at all.
    assert lines[label].endswith("set server.public_url to name this deployment exactly:")
    assert lines[label + 1].startswith("http")
    assert lines[label + 1].rstrip("/").rsplit("/", 2)[-2] == "x"
    # And the counts, over the deployment this module wrote.
    assert "  providers: 5" in said
    assert "  agents: 1" in said
    # Nothing about the run, and nothing of the credential this lane
    # stored, on either stream.
    assert printed.err == ""
    assert SECRET not in said


# The document, and the apply that installs it
#
# One controlled document, imported and then installed the way an
# operator does it: two commands, over the wire. It sits here because
# the apply above has already happened, so the world this lands in is
# one the lane has already read back, and because the two tests either
# side of it are what its own claim is measured against: the bootstrap
# left an agent unserved, and this leaves nothing waiting.
#
# What it writes is chosen so that the claim is provable rather than
# plausible. An apply prints every section's heading whether or not
# anything moved, so a document naming a kind the apply merely reports
# on would pass against a server that reinstalled the world it already
# had. A fragment the lane's agent includes cannot: the text reaches an
# assembled prompt only if the apply after this import built the world
# this document describes, and `agent preview` is a read of the running
# process rather than of the store.
#
# The agent's body is repeated whole because an imported document
# replaces an entity rather than editing it, which is the same reason
# `export` is the document `import` takes.

WIRED = "The dog is called Bosse."

INSTALLED: dict[str, object] = {
    "prompt_fragments": {"wire": {"text": WIRED}},
    "agents": {
        "sam": {"prompt": "You are Sam.", "prompt_includes": ["household", "wire"]}
    },
}


def test_an_apply_installs_what_an_import_wrote(
    deployed: Live, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The pair, end to end against a real server (#371).

    Two commands, and both answers are the real ones: the import is a
    transaction on a Postgres, and the apply is a running server
    composing a new world and swapping it in. What an operator reads is
    what was written, the boundary that write is waiting on, and then
    what the apply made of it.

    The proof that the world which arrived is this one is the read
    underneath, and it is a read of the process: `agent preview`
    assembles the prompt the running server would send, and the fragment
    in it exists only in the document just installed. The apply's own
    listing says the same thing from the other side, naming the agent
    whose assembled prompt moved.
    """
    document_path = written(tmp_path, "wired.yaml", INSTALLED)

    assert run("import", "-f", document_path) == 0

    written_out = capsys.readouterr()
    assert written_out.out.splitlines() == [
        "prompt_fragments.wire: wrote",
        "agents.sam: wrote",
    ]
    # The write is waiting, and the sentence under it says on what.
    assert f"{cli.PROGRAM} apply" in written_out.err

    assert run("apply") == 0

    printed = capsys.readouterr()
    lines = printed.out.splitlines()
    # The apply's own listing: the agent whose assembled prompt this
    # document moved, and then what every MCP entry is doing.
    assert "prompts:" in lines
    assert "  changed: sam" in lines
    assert "house" in printed.out
    # And nothing waiting, which is the whole of what the apply buys.
    assert printed.err == ""

    # The read that says the running server is serving this document
    # rather than the world it had a moment ago. Nothing in the store
    # answers it: an assembled prompt is built by the process from the
    # world it installed.
    assert run("agent", "preview", "sam") == 0
    assembled = capsys.readouterr().out
    assert WIRED in assembled
    assert "The bins go out on Tuesday." in assembled


# The board nobody owns, over the wire
#
# The one command of the grammar that reaches something other than the
# configuration API. It is driven here for the same reason everything
# else is: what it talks to is a real uvicorn, so the address policy, the
# request boundary and the reading of a real reply all run for real.
#
# It sits after the apply because that is where the agent this
# deployment names becomes one this server is serving, which is what
# turns a claimed board into an admitted one rather than a bound board
# waiting on a restart.

# Planted in the QUERY of the address the command is given, which is the
# other place a credential is written into a URL and the one the address
# policy accepts rather than refusing. Distinct from the two sentinels
# above, so a failure says which path leaked.
URL_SECRET = "utok-5c8e13-never-a-real-credential"


def test_a_simulated_board_checks_in_and_is_claimed_over_the_wire(
    deployed: Live, capsys: pytest.CaptureFixture[str], watched: Watched
) -> None:
    """The whole device-side half against a running server, and the
    credential nobody typed.

    Three claims, and the third is the one a review would not think to
    ask for. The unclaimed board reports the code the deployment agrees
    it is showing, which is the two halves of this lane meeting. The
    claim runs the four-step ceremony, and the fourth step is what mints
    the token: the poll answers a status and only a check-in reply mints
    anything, so the state this ends in is the SECOND reply's.

    And that reply carries a real device token and a real websocket URL,
    neither of which anybody typed and neither of which may reach a
    surface. They are read here off the same board the command uses, so
    what is asserted absent is the actual value the server issued rather
    than a value this test invented.
    """
    url = f"{deployed.origin}{OTA_PATH}?token={URL_SECRET}"

    assert run("simulator", "check-in", url, "--mac", SIMULATED_MAC) == 0
    unclaimed = capsys.readouterr()
    assert "not claimed yet" in unclaimed.out
    [code] = [
        line.removeprefix("activation code: ").strip()
        for line in unclaimed.out.splitlines()
        if line.startswith("activation code: ")
    ]
    assert code.isdigit()
    # The deployment agrees there is a board waiting with that code, and
    # it is the simulated one.
    assert run("device", "pending", "list") == 0
    listed = capsys.readouterr().out
    assert code in listed
    assert SIMULATED_MAC in listed
    assert BOARD in listed

    # The four-step ceremony, ending in the state the second check-in
    # carried. The poll answers 200 at once here, because the claim binds
    # the board to an agent this server is already serving.
    assert run("simulator", "check-in", url, "--claim", "sam") == 0
    admitted = capsys.readouterr()
    assert "is admitted" in admitted.out
    assert "protocol version" in admitted.out

    # What the reply actually handed this board, read through the same
    # board the command drives.
    issued = check_in(deployed, SIMULATED_MAC)
    assert isinstance(issued, board.Admitted)
    assert issued.token
    capsys.readouterr()

    assert run("simulator", "check-in", url) == 0
    reported = capsys.readouterr()
    surfaces = {
        "stdout": unclaimed.out + admitted.out + reported.out,
        "stderr": unclaimed.err + admitted.err + reported.err,
        "logs": watched.everything(),
        # The fourth surface needs a refusal to exist at all, and these
        # three commands all succeeded. So it is the same address given
        # to a command this grammar will not take, which is refused after
        # the URL has been read and before anything is sent.
        "chain": chain_of(("simulator", "check-in", url, "--mac", "not-a-mac")),
    }
    # The three credentials of this issue, on all four surfaces: the URL
    # an operator typed, the device token the reply minted, and the API
    # secret the claim carried.
    assert leaked(URL_SECRET, **surfaces) == []
    assert leaked(issued.token, **surfaces) == []
    assert leaked(SECRET, **surfaces) == []
    # And the address that token would have been sent to, which is
    # far-side text deciding where a credential goes.
    assert leaked(issued.websocket, **surfaces) == []
    # The stand-in is what every line named instead.
    assert SUPPLIED_ENDPOINT in reported.out


def test_a_simulated_board_holds_a_conversation_over_the_wire(
    deployed: Live, capsys: pytest.CaptureFixture[str], watched: Watched
) -> None:
    """The whole thing, against a real uvicorn: the second verb's row,
    and the compatibility claim only a real server can make.

    It runs after the case above, which claimed this board, so the check
    in front of the socket answers `Admitted` and the token the handshake
    presents is one this deployment actually minted. What happens next is
    the real pipeline on mock providers: the endpointer hears the
    packaged utterance, the ASR announces a transcript, the LLM answers
    it and the TTS speaks the answer back as frames this side counts.

    A controlled peer can prove the headers and every adversarial answer,
    and it cannot prove this: that a vinga-server accepts what this
    client sends and answers something this client can read.
    """
    url = f"{deployed.origin}{OTA_PATH}?token={URL_SECRET}"

    assert run("simulator", "run", url, "--mac", SIMULATED_MAC) == 0

    held = capsys.readouterr()
    assert "is admitted" not in held.out
    assert "saying: " in held.out
    # The mock ASR announces a fixed transcript and the mock LLM answers
    # it, so what came back is the deployment's own words rather than
    # anything this side made up.
    assert "heard: " in held.out
    assert "said: " in held.out
    assert "reply: " in held.out
    assert conversation.CLOSE_NAMES[1000] in held.out
    # Nothing arrived out of order, which is the machine's own claim
    # against a server that is not trying to break it.
    assert "out of order:" not in held.err

    # And the three credentials again, on the verb that carries a device
    # token onto a websocket rather than only reading one.
    issued = check_in(deployed, SIMULATED_MAC)
    assert isinstance(issued, board.Admitted)
    surfaces = {
        "stdout": held.out,
        "stderr": held.err,
        "logs": watched.everything(),
        "chain": chain_of(("simulator", "run", url, "--mac", "not-a-mac")),
    }
    assert leaked(URL_SECRET, **surfaces) == []
    assert leaked(issued.token, **surfaces) == []
    assert leaked(SECRET, **surfaces) == []
    assert leaked(issued.websocket, **surfaces) == []


# How long the tail below is given to see one event of a board that is
# holding a conversation. A ceiling on a hang rather than an
# expectation: the first session event goes out before the reply does,
# and a run that approaches this is already a bug worth reading about.
TAIL_SECONDS = 60


def test_the_event_tail_hears_a_conversation_as_it_happens(deployed: Live) -> None:
    """The stream, opened against the real server by the real command,
    while a real board talks to it.

    This is the one case that can prove the half M1 wired through the
    session edge. A server event reaches the hub through the
    process-global tap, which any test can drive; a SESSION event
    reaches it only because the hub is attached to `SessionEvents` as
    the session is constructed, and only a conversation over a socket
    against a booted server produces one. What the line printed here
    carries is `session=`, which is exactly the field a server event
    does not have.

    **The tail is a process of its own, and it has to be.** Every
    request boundary in this repository holds the request loggers quiet
    under one process-global lock, for the span from raising a level to
    putting it back (`logs.quieted`). A tail's span is the length of the
    stream, so an in-process tail holds that lock while it watches, and
    the conversation this case drives goes through the same boundary:
    the two would deadlock, with the tail waiting for an event the
    conversation could not produce. On a deployment the question does
    not arise, since a tail IS a process doing one thing. Here it means
    `subprocess`, which also gives the case a deadline it cannot outlive
    and a kill it cannot skip, so a regression makes this lane red
    rather than hung.

    The check-in happens BEFORE the stream opens, deliberately. It is
    what mints the token this conversation presents, and it emits an
    `ota_check` of its own; with the tail already open that event would
    be the first one admitted, and this case would be proving the tap it
    is not about. What runs while the stream is open is the socket and
    nothing else.

    The conversation is driven in a loop because nothing here can
    observe the moment the subscription attaches, and a stream that
    opened a millisecond after the only event would otherwise wait for a
    second one that never came.
    """
    issued = check_in(deployed, SIMULATED_MAC)
    assert isinstance(issued, board.Admitted)

    argv = ("events", "tail", "--device", SIMULATED_MAC)
    with subprocess.Popen(
        [str(Path(sys.executable).parent / cli.CONSOLE_SCRIPT), *argv],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=os.environ | {"PYTHONDONTWRITEBYTECODE": "1"},
    ) as tail:
        try:
            deadline = time.monotonic() + TAIL_SECONDS
            while tail.poll() is None and time.monotonic() < deadline:
                time.sleep(0.2)
                if tail.poll() is None:
                    conversation.converse(
                        target=issued.websocket,
                        token=issued.token,
                        identity=board.Identity.of(SIMULATED_MAC),
                        version=issued.protocol_version,
                        said=utterance.packaged(),
                        say=lambda _line: None,
                    )
        finally:
            if tail.poll() is None:
                tail.kill()
        printed, complained = tail.communicate()

    assert tail.returncode == 0, (printed, complained)
    assert complained == ""
    [line] = printed.splitlines()
    assert f'device="{SIMULATED_MAC}"' in line
    assert "session=" in line, line
    # Recorded against the row by hand, which `run` does for every other
    # case: this is the same command line, run and answered, and the
    # only difference is the process it ran in.
    assert registered(argv) == ("events", "tail")
    DRIVEN.add(registered(argv))


def test_the_device_half_needs_no_api_token_at_all(
    deployed: Live, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two credentials kept distinct, asserted the only way that means
    anything: with the operator-side one absent from the environment.

    A command that read it would refuse naming the variable, so this
    passing is the claim rather than an absence of evidence.
    """
    monkeypatch.delenv("VINGA_API_SECRET", raising=False)
    url = f"{deployed.origin}{OTA_PATH}"

    assert run("simulator", "check-in", url, "--mac", "02:00:00:00:00:09") == 0

    assert "VINGA_API_SECRET" not in capsys.readouterr().err


def test_the_session_verbs_read_and_erase_a_real_record_over_the_wire(
    deployed: Live,
    module_database: str,
    capsys: pytest.CaptureFixture[str],
    watched: Watched,
) -> None:
    """The four verbs of the `session` noun against a real uvicorn: a
    read, a detail, an addressed erasure and a selector purge.

    The record is written by the store the server would have written it
    with, into the database this lane's server is serving from, because
    the commands are what is under test rather than the pipeline that
    fills the store; recording is off in this deployment's boot
    configuration, which is the default and not something to change for
    a CLI case.

    What only a real server can show is here: these verbs reach a schema
    the domain configuration knows nothing about, through the same
    token, the same address resolution and the same transport policy as
    every command above, and the deletion runs on a write engine the
    server opens for the request rather than on a writer it is holding.
    """
    seeded = ConversationStore(
        DatabaseConfig(name=module_database), retention_days=0
    )
    seeded.start()
    try:
        for name, device in (("lane-one", SESSION_MAC), ("lane-two", SESSION_OTHER_MAC)):
            seeded.open_session(name, 100.0, session_manifest(device))
            seeded.record_turn(
                name,
                TurnRecord(
                    at=101.2,
                    conversation=LANE_CONVERSATION,
                    agent="sam",
                    heard="what is the weather like",
                    reply="Sunny.",
                ),
            )
            seeded.close_session(name, duration_s=2.0, reason="client")
    finally:
        seeded.stop()

    assert run("session", "list") == 0
    listed = capsys.readouterr().out
    assert listed.splitlines()[0].split()[0] == "SESSION"
    assert "lane-one" in listed and "lane-two" in listed

    assert run("session", "show", "lane-one") == 0
    detail = capsys.readouterr().out
    assert detail.startswith("session: lane-one\n")
    assert "  turns: 1\n" in detail

    assert run("session", "delete", "lane-one", "--force") == 0
    assert capsys.readouterr().out.startswith("sessions: 1\n")

    assert run("session", "purge", "--device", SESSION_OTHER_MAC, "--force") == 0
    assert capsys.readouterr().out.startswith("sessions: 1\n")

    # Nothing left of either, which is the cascade running on the server
    # rather than inside a unit test's transaction: one thread had turns
    # in both sessions and has none now, so it went with the second of
    # them.
    assert run("session", "list", "--limit", "5") == 0
    assert "lane-" not in capsys.readouterr().out

    assert leaked(SECRET, logs=watched.everything()) == []


def test_the_conversation_verbs_read_and_erase_a_real_thread_over_the_wire(
    deployed: Live,
    module_database: str,
    capsys: pytest.CaptureFixture[str],
    watched: Watched,
) -> None:
    """The three verbs of the `conversation` noun against a real
    uvicorn: a read, a thread whole with its dialogue, and an erasure.

    The thread spans both sessions, which is the shape only this
    projection can show and the shape that makes `show` two requests
    rather than one. What only a real server can prove is here for the
    reason the session case gives: the same token, the same address
    resolution, the same transport policy, and a deletion running on a
    write engine the server opens for the request.
    """
    seeded = ConversationStore(
        DatabaseConfig(name=module_database), retention_days=0
    )
    seeded.start()
    try:
        for name, device, heard in (
            ("thread-one", SESSION_MAC, "what is the weather like"),
            ("thread-two", SESSION_OTHER_MAC, "and what about tomorrow"),
        ):
            seeded.open_session(name, 100.0, session_manifest(device))
            seeded.record_turn(
                name,
                TurnRecord(
                    at=101.2,
                    conversation=LANE_THREAD,
                    agent="sam",
                    heard=heard,
                    reply="Sunny.",
                ),
            )
            seeded.close_session(name, duration_s=2.0, reason="client")
    finally:
        seeded.stop()

    assert run("conversation", "list") == 0
    listed = capsys.readouterr().out
    assert listed.splitlines()[0].split()[0] == "CONVERSATION"
    assert LANE_THREAD in listed

    assert run("conversation", "show", LANE_THREAD) == 0
    shown = capsys.readouterr().out
    assert shown.startswith(f"conversation: {LANE_THREAD}\n")
    assert "you: what is the weather like\n" in shown
    assert "you: and what about tomorrow\n" in shown

    assert run("conversation", "delete", LANE_THREAD, "--force") == 0
    assert capsys.readouterr().out.startswith("turns: 2\n")

    assert run("conversation", "list") == 0
    assert LANE_THREAD not in capsys.readouterr().out
    # The sessions the turns were spoken in are untouched, which is the
    # asymmetry between the two erasures, running on the server rather
    # than inside a unit test's transaction.
    assert run("session", "list") == 0
    assert "thread-one" in capsys.readouterr().out

    assert leaked(SECRET, logs=watched.everything()) == []


def test_the_memory_verbs_read_and_correct_over_the_wire(
    deployed: Live,
    module_database: str,
    capsys: pytest.CaptureFixture[str],
    watched: Watched,
) -> None:
    """The three verbs of the `memory` noun against a real uvicorn: the
    owners, one agent's own facts, a correction and two deletions.

    Seeded through the store an agent writes through, which is what
    makes this the whole path: what the routes answer is what a reply
    would have stored, in a schema no read-only role is granted on, so
    this connection is the only way to see it at all.

    The correction is piped in rather than typed as an argument, which
    is the property the whole grammar of this noun is shaped by, and it
    is what a script does: a remembered fact reaches shell history and
    the process list from an argument and cannot be taken back.
    """
    seeded = open_memory(DatabaseConfig(name=module_database))
    try:
        numbers = [
            asyncio.run(
                seeded.add(MemoryScope.AGENT, "sam", fact, agent="sam")
            )
            for fact in ("the user likes rain", "the user is vegetarian")
        ]
        asyncio.run(
            seeded.add(
                MemoryScope.DEVICE, SESSION_MAC, "the kitchen is small", agent="sam"
            )
        )
        asyncio.run(
            seeded.set_state(LANE_MEMORY_THREAD, "scene", "a forest", agent="sam")
        )
    finally:
        seeded.close()

    assert run("memory", "list", "agent") == 0
    listed = capsys.readouterr().out
    assert listed.splitlines()[0].split() == ["OWNER", "FACTS"]
    assert listed.splitlines()[1].split() == ["sam", "2"]

    assert run("memory", "list", "agent", "sam") == 0
    facts = capsys.readouterr().out
    assert facts.startswith(f"{numbers[0]}: the user likes rain\n")

    assert run("memory", "list", "device", SESSION_MAC) == 0
    assert "the kitchen is small" in capsys.readouterr().out

    assert run("memory", "list", "conversation", LANE_MEMORY_THREAD) == 0
    assert capsys.readouterr().out.startswith("scene: a forest\n")

    assert run(
        "memory", "set", "agent", "sam", str(numbers[0]),
        stdin="the user loves rain\n",
    ) == 0
    assert capsys.readouterr().out.startswith(f"{numbers[0]}: the user loves rain\n")

    assert run("memory", "delete", "agent", "sam", str(numbers[1]), "--force") == 0
    assert capsys.readouterr().out == "facts: 1\n"

    assert run("memory", "delete", "conversation", LANE_MEMORY_THREAD, "--all", "--force") == 0
    assert capsys.readouterr().out == "state: 1\n"

    assert run("memory", "list", "conversation", LANE_MEMORY_THREAD) == 0
    assert capsys.readouterr().out.startswith("this conversation is keeping nothing")

    assert leaked(SECRET, logs=watched.everything()) == []


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


def test_the_store_exports_as_a_document_it_imports_back_unchanged(
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

    assert run("import", "-f", str(path)) == 0
    imported = capsys.readouterr()
    outcomes = [line.split(": ")[-1] for line in imported.out.splitlines()]
    assert outcomes and set(outcomes) == {"unchanged"}
    assert imported.err == ""

    assert run("export") == 0
    assert capsys.readouterr().out == exported


# Recovery, which is what an export is for
#
# The one procedure that used to have a flag of its own. A deployment
# whose server will not start is repaired by stopping it, dropping and
# recreating the database, booting clean and importing a kept export, and
# the stored credentials come back through the commands the export
# annotated. That is a claim about a server with a lifetime and a store
# of its own, which is why it is here rather than in an acceptance
# suite: nothing in-process can stop a server, take its database away
# and start another on a new one.
#
# The cutover of #283 is the same procedure met from the other side, and
# gets a case of its own below: the export an operator keeps is the one
# their SQLite-era CLI printed, so what has to be applicable into a
# fresh Postgres database is that file rather than one this build wrote
# a minute earlier. The fixture beside this suite is that file, produced
# by the pre-cutover build and committed.


def _annotated_secret_command(exported: str) -> tuple[str, ...]:
    """The secret set an export named, as the words to run.

    Read out of the document rather than written here, and run rather
    than compared: what is being held is that the line an operator
    pastes out of their export is a line that works. The program's own
    two words come off the front, because this drives `cli.main`, which
    is already inside them.
    """
    named = [
        line.lstrip("# ")
        for line in exported.splitlines()
        if line.lstrip("# ").startswith(cli.PROGRAM)
        and " secret set " in line
    ]
    assert len(named) == 1, f"the export named {len(named)} secret set commands"
    return tuple(shlex.split(named[0])[len(cli.PROGRAM.split()) :])


# Where the recovered credential is read back. Named here because it is
# the one thing the procedure has to prove that no command of the
# grammar can be asked: a credential never travels in a read, so the
# only surface that can say the right plaintext went back in is the
# repository the server itself decrypts through.
RECOVERED_SLOT = SecretLocation.provider("llm", "spare", "api_key")


def stored_plaintext(database: DatabaseConfig, location: SecretLocation) -> str | None:
    """One stored credential, decrypted the way a provider build reads
    it.

    Through the repository rather than through a command, because there
    is deliberately no command that prints a stored value: `show` masks
    it and `export` names the command that enters it. So an export
    comparison cannot tell a credential that went back correctly from
    one stored as the wrong bytes, and this is what tells them apart.

    The keys are read from the environment at the moment of the call,
    which after the rotation below is the new key and only the new key.
    """
    engine = open_database(database)
    try:
        return ConfigStore(engine, load_keys()).load().secrets.secret(location)
    finally:
        engine.dispose()


def test_a_deployment_is_rebuilt_from_its_export_on_an_empty_database(
    blank_database: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The recovery procedure, end to end over a real socket.

    Seed a deployment and store a credential on it, keep the export,
    stop the server, drop and recreate the database, rotate to a key the
    old one is not in, boot another server on the empty one, import the
    export, re-run the secret set command the export annotated, and
    start the server once more. Then read the credential back, and
    export.

    The drop and recreate is the documented reset run rather than
    described: `dropdb` then `createdb`, which is exactly what the
    helper below does.

    A new key rather than the old one, because that is the case the
    deployment notes promise: the key is lost with the database it
    opened, and what the next boot needs is a key list that opens every
    envelope stored, which after a rebuild is only the ones just
    written. Carrying the old key across would prove a weaker thing than
    the documentation claims.

    The last two steps are what make this a proof rather than a
    round trip. Starting the server again runs the boot's exhaustive
    verification against the new key, so ciphertext nothing can open
    fails here. And the credential is read back as plaintext before the
    exports are compared, because an export carries a credential's
    location and the command that fills it and never its value: a
    a secret set that stored the wrong bytes writes an export that matches
    the first one to the letter.

    A store and a key of its own, because the deployment is destroyed
    half way through and the lane's is shared.
    """
    monkeypatch.delenv(CONFIG_ENV, raising=False)
    monkeypatch.setenv(MASTER_KEY_ENV, generate_key())
    monkeypatch.setenv(SECRET_ENV, SECRET)
    database = DatabaseConfig(name=blank_database)
    document_path = written(tmp_path, "deployment.yaml", DEPLOYMENT)

    with serving(database) as before:
        seeded = ("--api-url", before.api_url)
        assert run(*seeded, "import", "-f", document_path) == 0
        # A deployment that can be started, which an empty database also
        # is and a store holding agents and nothing bound to one is not.
        # The rebuild has to reach a server that boots, so what it puts
        # back has to be complete: the default agent is part of the
        # deployment being recovered rather than a detail of this test.
        assert run(*seeded, "default-agent", "set", "sam") == 0
        assert run(
            *seeded, "provider", "secret", "set", "llm", "spare", "api_key",
            "--from-env", SECRET_ENV,
        ) == 0
        capsys.readouterr()
        assert run(*seeded, "export") == 0
        exported = capsys.readouterr().out

    # The database, gone and made again: what an operator does when a
    # stored row is what the boot refuses, and the same two commands the
    # recovery documentation names.
    reset_database(blank_database)

    # And the key with it. Nothing that follows can open an envelope
    # written above, which is the point: the rebuild puts the
    # credentials back rather than recovering them.
    lost, replacement = os.environ[MASTER_KEY_ENV], generate_key()
    assert replacement != lost
    monkeypatch.setenv(MASTER_KEY_ENV, replacement)

    with serving(database) as after:
        rebuilt = ("--api-url", after.api_url)
        # Clean, which is what makes the import below a reproduction
        # rather than a no-op against what was already there.
        assert run(*rebuilt, "show") == 0
        assert document(capsys.readouterr().out)["agents"] == {}

        path = tmp_path / "exported.yaml"
        path.write_text(exported, encoding="utf-8")
        assert run(*rebuilt, "import", "-f", str(path)) == 0
        outcomes = [line.split(": ")[-1] for line in capsys.readouterr().out.splitlines()]
        # Every entry the document names was written, the default agent
        # included, because the store it landed in was empty: an
        # `unchanged` anywhere here would be a section the rebuild did
        # not actually put back.
        assert outcomes and set(outcomes) == {"wrote"}

        # The half a document cannot carry: a credential never travels in
        # a read, so the export named the command that enters it and this
        # runs that command.
        assert run(*rebuilt, *_annotated_secret_command(exported), stdin=SECRET) == 0
        capsys.readouterr()

    # The boot the whole procedure exists to reach. `load_boot_config`
    # opens every stored envelope under the configured keys before the
    # application is built, so a server that starts here is a server
    # whose credentials the new key opens; one that cannot refuses, and
    # this context manager raises rather than yielding.
    with serving(database) as restarted:
        recovered = ("--api-url", restarted.api_url)

        # The value itself, which is the one thing neither the boot nor
        # the export can be asked about: the boot proves the ciphertext
        # opens, and opening the wrong plaintext is exactly as openable.
        assert stored_plaintext(database, RECOVERED_SLOT) == SECRET

        assert run(*recovered, "export") == 0
        assert capsys.readouterr().out == exported


# The export a deployment kept before the cutover, produced by the
# pre-cutover build's own `config export` against a SQLite store and
# committed as it was printed. Nothing in this repository can produce it
# again, which is the whole reason it is a file: after this milestone
# there is no build that writes a SQLite database to export from.
PRE_CUTOVER_EXPORT = Path(__file__).resolve().parent / "data" / "pre-cutover-export.yaml"


def test_a_pre_cutover_export_imports_into_an_empty_postgres_database(
    blank_database: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The cutover, as the procedure an operator actually runs.

    #283 migrates no data. What crosses is the document: a deployment
    exports its configuration with the build it is running, upgrades,
    boots on an empty database and imports what it kept, then enters its
    credentials again through the commands the export annotated. This is
    that path, with a real pre-cutover export as its input.

    The comparison at the end is the proof rather than a formality, and
    what it compares is the YAML configuration body. The export format
    is rendered from the domain models and not from the rows, so it does
    not depend on the backend; asserting the body byte for byte is what
    says so, and what would catch a model change that silently reshaped
    a document an operator is holding.

    Two parts of the file are excluded and neither is configuration.
    The header is the reproduction procedure, which names the commands
    of the build that printed it, so a kept export from an older
    grammar carries an older procedure and always will: the fixture's
    header says `apply --no-reload` and `reload`, the words this build
    spells `import` and `apply`. And the credential annotations name
    where a credential goes and never its value, so the fixture's
    commands are run rather than compared.
    """
    monkeypatch.delenv(CONFIG_ENV, raising=False)
    monkeypatch.setenv(MASTER_KEY_ENV, generate_key())
    monkeypatch.setenv(SECRET_ENV, SECRET)
    database = DatabaseConfig(name=blank_database)
    kept = PRE_CUTOVER_EXPORT.read_text(encoding="utf-8")

    with serving(database) as upgraded:
        rebuilt = ("--api-url", upgraded.api_url)
        # Empty, which is what an upgraded deployment boots on: the
        # schemas are migrated and hold nothing.
        assert run(*rebuilt, "show") == 0
        assert document(capsys.readouterr().out)["agents"] == {}

        path = tmp_path / "kept.yaml"
        path.write_text(kept, encoding="utf-8")
        assert run(*rebuilt, "import", "-f", str(path)) == 0
        outcomes = [line.split(": ")[-1] for line in capsys.readouterr().out.splitlines()]
        assert outcomes and set(outcomes) == {"wrote"}

        # The half a document cannot carry, entered through the command
        # the kept export named.
        assert run(*rebuilt, *_annotated_secret_command(kept), stdin=SECRET) == 0
        capsys.readouterr()

    # The boot the whole procedure exists to reach, on the new backend:
    # every stored envelope opens under the configured key before the
    # application is built, so a server that starts here is one whose
    # credentials came back.
    with serving(database) as after:
        assert stored_plaintext(database, RECOVERED_SLOT) == SECRET

        assert run("--api-url", after.api_url, "export") == 0
        assert _configuration_body(capsys.readouterr().out) == _configuration_body(kept)


def _configuration_body(exported: str) -> str:
    """One export's YAML configuration, with the two parts that are not
    configuration taken off.

    The header first, which is the leading run of comment lines: it is
    the procedure for reproducing the deployment, written in the command
    grammar of the build that printed the file, so two exports from
    different releases differ there by design rather than by drift.

    The credential annotations second, and they are taken off as a
    block rather than line by line. They are the other part two exports
    of one configuration may differ in: the heading and its commands are
    present only when something is stored, and what is being compared is
    the configuration rather than which credentials happen to be filled
    in at the moment it was printed.

    A block and not a predicate, because the heading is prose and prose
    grows a line: it did in #371's review round, when the heading gained
    a second line saying where in the header's steps the credentials go,
    and a rule that matched its first line let the second through into
    the compared body. The block is the tail of the file by
    construction, since an export is the header, the configuration and
    then the annotations, so what is dropped is everything from the
    heading onwards.

    What is left is the whole of the configuration, compared byte for
    byte.
    """
    lines = exported.splitlines(keepends=True)
    body = list(itertools.dropwhile(lambda line: line.startswith("#"), lines))
    annotated = next(
        (
            number
            for number, line in enumerate(body)
            if "Stored credentials are not exported" in line
        ),
        len(body),
    )
    return "".join(body[:annotated]).rstrip() + "\n"


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

# The same trick for a config file the loader will read: the word is
# replaced by a file whose `log_level` is the planted credential, which
# is what a paste into the wrong key looks like and what the validator
# behind that key used to quote back.
REFUSED_CONFIG = "REFUSED_CONFIG"

# A path with nothing at it, carrying the planted credential. A config
# path is typed, which makes it the last place a refusal may repeat, so
# the row below is a leak case as much as a wording case: the whole
# stderr is pinned to the fixed sentence, and the sentinel sweep every
# row runs then says the path reached no surface at all.
MISSING_CONFIG = f"/nowhere/at/all/{PLANTED}.yaml"


class Refusal(NamedTuple):
    """One family's refusal: which family it stands for, what to type,
    the whole of what stderr says afterwards, and which end of the
    connection composed it."""

    # The noun path the command sits under, which is what a family is
    # once the tree is deeper than two words: `provider secret` refusals
    # are not `provider` refusals, and counting them as one would leave
    # the sub-noun's sentences unwatched. A flat command is its own
    # family, since it sits under no noun.
    family: tuple[str, ...]

    argv: tuple[str, ...]
    stderr: str
    wire: bool


def family_of(words: tuple[str, ...]) -> tuple[str, ...]:
    """Which family one registered row belongs to: the noun path it
    sits under, or itself when it sits under nothing."""
    return words[:-1] if len(words) > 1 else words


REFUSALS: tuple[Refusal, ...] = (
    Refusal(
        ("agent",),
        ("agent", "set", "refused-agent", f"prompt={PLANTED}", "llm=nowhere"),
        UNRESOLVED
        + "\n  - agents.refused-agent.llm: names no llm provider that exists, and the "
        "name is not quoted back (defined: brain, spare)",
        True,
    ),
    Refusal(("agent",), ("agent", "delete", "no-such-agent"), entities.NO_SUCH_AGENT, True),
    Refusal(
        ("provider",), ("provider", "show", "llm", "no-such"), entities.NO_SUCH_PROVIDER, True
    ),
    Refusal(
        ("mcp-server",),
        ("mcp-server", "show", "no-such"),
        entities.NO_SUCH_MCP_SERVER,
        True,
    ),
    Refusal(
        ("prompt-fragment",),
        ("prompt-fragment", "show", "no-such"),
        entities.NO_SUCH_FRAGMENT,
        True,
    ),
    Refusal(("agent-defaults",), ("agent-defaults", "show", "extra"), USAGE, False),
    Refusal(("device",), ("device", "bind", "not-a-mac", "sam"), NOT_A_MAC, True),
    Refusal(
        ("device", "pending"),
        ("device", "pending", "claim", "000000", "sam"),
        "no device is waiting with that activation code. A code lasts ten minutes and "
        "is retired the moment it is claimed, and a device that has been waiting longer "
        "is already showing a fresh one: read the code currently on the device's screen "
        "and use that. `vinga-server config device pending list` lists the codes this "
        "server is showing right now.",
        True,
    ),
    Refusal(
        ("provider", "secret"),
        ("provider", "secret", "set", "llm", "no-such", "api_key", "--from-env", SECRET_ENV),
        entities.NO_SUCH_PROVIDER,
        True,
    ),
    Refusal(
        ("mcp-server", "secret"),
        ("mcp-server", "secret", "clear", "no-such", "env.TOKEN"),
        "mcp_servers: no secret is stored for that slot",
        True,
    ),
    Refusal(
        ("default-agent",),
        ("default-agent", "set", "no-such-agent"),
        UNRESOLVED
        + "\n  - default_agent: names no agent that exists, and the name is not quoted "
        "back (defined: sam)",
        True,
    ),
    Refusal(
        ("import",),
        ("import", "-f", REFUSED_DOCUMENT),
        "document: the top-level keys of an applied document are the sections of the "
        "domain configuration, which are " + ", ".join(DOMAIN_KEYS) + ". Something else "
        "was written, and it is not quoted back",
        True,
    ),
    # The one family whose refusals are about an address rather than
    # about a configuration, so its row hands the command a URL with a
    # credential-shaped segment in it and asserts the sentence quotes
    # none of it.
    Refusal(
        ("simulator",),
        ("simulator", "check-in", f"ftp://voice.example/x/{PLANTED}/"),
        "the URL given to the simulator is not an http:// or https:// URL with a host. "
        "It is not quoted back, since an OTA URL can be the deployment's own secret.",
        False,
    ),
    # The conversation store's family. Its refusals are the server's, on
    # a schema the domain configuration knows nothing about, and the one
    # chosen here is the purge that named nothing: the sentence for it
    # is the endpoint's rather than the grammar's, so this row is what
    # proves that decision really does travel back over the connection.
    Refusal(
        ("session",),
        ("session", "purge"),
        "a purge names what it erases: give at least one of session, device or before, "
        "and several are combined so that every one of them has to match. Erasing "
        "everything is deliberately not something this endpoint can be asked for",
        True,
    ),
    # The thread half of the same family, and the sentence chosen is the
    # server's again: an id nothing answers to, refused by the endpoint
    # in a sentence that names no id.
    Refusal(
        ("conversation",),
        ("conversation", "show", "nothing-of-that-id"),
        "no conversation of that id is in the conversation store. The id is the "
        "thread's uuid hex, which every turn of it carries; a thread whose last "
        "activity is older than server.conversations.retention_days has been pruned, "
        "and a thread that lost every turn to an erasure was deleted with them.",
        True,
    ),
    # The third schema's family, and the sentence is the server's for
    # the reason the two above are: an addressed deletion of a number
    # nothing has, refused by the endpoint in a sentence that repeats
    # neither the number nor the owner.
    Refusal(
        ("memory",),
        ("memory", "delete", "agent", "sam", "999999999", "--force"),
        "no fact of that number is stored under that memory. The numbers are the ids "
        "this namespace's facts listing answers with, and they are never reused, so a "
        "number that is not there is a fact that has been corrected out, erased, or was "
        "never this owner's. Nothing was changed.",
        True,
    ),
    # The live half of the same store's family, and the sentence is the
    # server's again: a filter the endpoint will not read, refused
    # before a stream is opened. The value handed to it is the planted
    # credential, because `--device` is where this command's own input
    # can carry one, and the rule says it is not quoted back.
    Refusal(
        ("events",),
        ("events", "tail", "--device", PLANTED),
        "device has to be a MAC address: six colon-separated or dash-separated hex "
        "pairs, for example aa:bb:cc:dd:ee:ff. What was sent is not quoted back",
        True,
    ),
    Refusal(("apply",), ("apply", "extra"), USAGE, False),
    Refusal(
        ("ota-url",),
        ("ota-url", "--config", MISSING_CONFIG),
        CONFIG_NOT_FOUND.format(source=CONFIG_FROM_FLAG),
        False,
    ),
    # The other half of the same door: a file that is there and holds a
    # value one of its keys refuses. The value is the planted
    # credential, which is what a paste into the wrong key looks like,
    # and this row is what says it reaches neither the sentence, nor a
    # log record, nor the chain the refusal travels on.
    Refusal(
        ("ota-url",),
        ("ota-url", "--config", REFUSED_CONFIG),
        f"invalid config in {CONFIG_FROM_FLAG}:\n"
        "  - server.log_level: is not a logging level; expected one of: DEBUG, INFO, "
        "WARNING, ERROR, CRITICAL. What was set is not quoted back",
        False,
    ),
    Refusal(("info",), ("info", "extra"), USAGE, False),
    Refusal(("list",), ("list", "extra"), USAGE, False),
    Refusal(("show",), ("show", "extra"), USAGE, False),
    Refusal(("export",), ("export", "extra"), USAGE, False),
    Refusal(("diff",), ("diff", "extra"), USAGE, False),
    Refusal(
        ("schema",),
        ("schema", "nonsense"),
        '"nonsense" is not a documented entity; expected one of: '
        + ", ".join(docgen.entity_names()),
        False,
    ),
    # `reference` takes a selector now, so a word after it is a half
    # rather than an extra argument, and what refuses it is the halves
    # registry. The value handed to it is the planted credential,
    # because the positional is where this command's own input can carry
    # one, and unlike `schema`'s refusal above this sentence names only
    # the halves that exist.
    Refusal(
        ("reference",),
        ("reference", PLANTED),
        server_reference.NO_SUCH_HALF.format(
            halves=", ".join(server_reference.half_names())
        ),
        False,
    ),
    Refusal(("openapi",), ("openapi", "extra"), USAGE, False),
    Refusal(("cli-reference",), ("cli-reference", "extra"), USAGE, False),
)

# What a client that cannot reach the API says, pinned at both ends
# rather than written out: the address is this case's own, and an
# appended value would fall outside the tail.
UNREACHABLE_HEAD = "cannot reach the configuration API at "

UNREACHABLE_TAIL = "booting one on an empty database and importing a kept export."


def test_every_family_of_the_grammar_has_a_refusal() -> None:
    """The refusal table, held to the registration table.

    A family with no refusal here would be a family whose sentences
    nothing has ever seen cross a connection, and the way that happens
    is a command added to the grammar rather than a row deleted from
    this file.
    """
    assert {row.family for row in REFUSALS} == {
        family_of(row.words) for row in cli.COMMANDS
    }


def refusing(argv: Sequence[str], directory: Path) -> tuple[str, ...]:
    """One refusal's command line, with the document the import case
    needs written where it can be found.

    The document carries the planted credential in the section it will
    be refused for, which is what makes the import row's leak check
    about something: a refusal that quoted the document back would carry
    it.
    """
    return tuple(
        written(directory, "refused.yaml", {"nonsense_section": {"note": PLANTED}})
        if word == REFUSED_DOCUMENT
        else written(directory, "refused-config.yaml", {"server": {"log_level": PLANTED}})
        if word == REFUSED_CONFIG
        else word
        for word in argv
    )


@pytest.mark.parametrize(
    "row", REFUSALS, ids=[" ".join(row.family) for row in REFUSALS]
)
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
    because a secret set's `--from-env` reads the variable before it learns
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


@pytest.mark.parametrize(
    "row", REFUSALS, ids=[" ".join(row.family) for row in REFUSALS]
)
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
    argv = ("agent", "set", "sam", "-f", str(broken))

    assert run(*argv) == 1
    refused = capsys.readouterr()
    assert refused.err == (
        f"invalid YAML in {cli.FILE_SOURCE} at line 3, column 1. Nothing of what it "
        f"holds is quoted back: a source that will not parse is one nothing here has "
        f"validated, and what a parser says about one repeats the tag or the key it "
        f"stopped on\n"
    )
    assert refused.out == ""
    # The path is typed, so it is not in the sentence either (#289): the
    # file is called what this module calls it, and where the parser
    # stopped is a line and a column inside it.
    assert str(broken) not in refused.err
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

    assert run("agent", "show", "sam") == 0
    assert document(capsys.readouterr().out)["prompt"] == "You are Sam."


# The two bounds `import` rides, over real HTTP
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
# `import` against an ordinary read, on the same server, through the
# same client implementation, with the same store behind it.
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

    `import`'s read timeout is None, and the reason is that no finite
    one can be derived: the transaction loads the whole existing store
    and validates the whole resulting one, whose size no request bound
    limits, and a client that gave up on a write the server then
    committed would leave nobody able to say what is stored.

    The comparison is what makes that observable in a lane's wall clock.
    An ordinary read of this same server, through the same client
    implementation, with its bound cut to a value this server cannot
    meet, gives up and says so. The import, whose request demonstrably
    took longer than that bound, does not. (Each command builds a client
    of its own and closes it, so what the two share is the server and
    the code that talks to it, not one open connection.)

    One request and no second one, which is what makes the wall clock
    readable: an import writes and stops, and installing what it wrote
    is a command of its own.
    """
    entries = APPLY_LIMIT - 1
    many = {f"agent-{number:03d}": {"prompt": "You are one of many."} for number in range(entries)}
    document_path = written(tmp_path, "large.yaml", {"agents": many})
    monkeypatch.setattr(cli, "COMMANDS", impatient(("show",), IMPATIENT_S))

    started = time.monotonic()
    assert run("import", "-f", document_path, "--api-url", isolated.api_url) == 0
    took = time.monotonic() - started

    imported = capsys.readouterr()
    outcomes = [line.split(": ")[-1] for line in imported.out.splitlines()]
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
    argv = ("import", "-f", document_path, "--api-url", isolated.api_url)

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


# The published documentation, run
#
# Two things this repository publishes are command lines an operator is
# expected to type: the presets, which are whole deployments in one
# document, and the recipes on `docs/reference/cli.md`, which are read
# out of the example fragments. Both are run here, verbatim, against a
# real server on an empty store, because a documented command that
# stopped working is worse than an undocumented one: it is a promise.
#
# Verbatim means what it says. The commands are not rewritten to point
# at the lane's server or at an absolute path: the address is put in the
# environment, which is the documented remote shape, and the working
# directory is `vinga-server/`, which is where the recipes say to run
# them from.
#
# One published command is not run, and it is the bare `vinga apply`
# each preset's recipe ends with. Installing a preset means building
# what it names: a local stack downloads speech models and dials an
# Ollama nobody is running here. So what these two exercise is the
# import, which is a claim about the document rather than about the
# deployment it describes, and the names say which of the two is being
# made.

# Where the example fragments and the presets are, which is the
# directory every recipe's `-f examples/...` is relative to.
SERVER = Path(__file__).resolve().parents[2]

PRESETS = sorted((SERVER / "examples" / docgen.PRESET_DIR).glob("*.yaml"))


@pytest.mark.parametrize("preset", PRESETS, ids=[path.stem for path in PRESETS])
def test_a_preset_imports_onto_an_empty_store(
    live: Live,
    isolated: Live,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    preset: Path,
) -> None:
    """One preset, written to a server that booted on nothing.

    That is the whole claim a preset makes as a document: it is what an
    operator runs first, before there is anything for a reference to
    resolve against, so every entry it names has to arrive in one
    transaction and the document has to be complete enough to leave a
    store that reads back.

    Written twice, because the second half of the claim is that
    importing is idempotent: the same document again reports every entry
    unchanged and writes nothing, which is what makes a preset safe to
    keep in version control and re-run.

    The command is verbatim the first one the preset's own header
    prints. What is not run is the second, the `vinga apply` that
    installs it: installing a preset builds the engines it names, and
    the local stack downloads its speech models and dials an Ollama,
    neither of which is a thing this lane may make a test depend on. The
    apply is exercised on a document of this lane's own, further up.
    """
    monkeypatch.chdir(SERVER)
    monkeypatch.setenv(cli.API_URL_ENV, isolated.api_url)

    assert run("import", "-f", str(preset.relative_to(SERVER))) == 0
    first = [line.split(": ")[-1] for line in capsys.readouterr().out.splitlines()]
    assert first and set(first) == {"wrote"}

    assert run("import", "-f", str(preset.relative_to(SERVER))) == 0
    again = [line.split(": ")[-1] for line in capsys.readouterr().out.splitlines()]
    assert again == ["unchanged"] * len(first)

    # And what it left is a store that reads back as the document said,
    # rather than a run that merely returned zero.
    assert run("show") == 0
    shown = document(capsys.readouterr().out)
    written_document = yaml.safe_load(preset.read_text(encoding="utf-8"))
    assert shown["agents"].keys() >= written_document["agents"].keys()
    for stage, entries in written_document["providers"].items():
        assert shown["providers"][stage].keys() >= entries.keys()


# The one published line this lane does not run, for the reason the
# preset test above does not run it: it installs the presets that were
# just imported, and installing them builds what they name. Held to
# still being published by the assertion below, so the exception cannot
# outlive the recipe it is about.
NOT_INSTALLED = ["apply"]


def test_every_published_recipe_line_but_the_preset_apply_runs(
    live: Live,
    isolated: Live,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Every command `docs/reference/cli.md` publishes except one, in the
    order it publishes them, against a server that booted on an empty
    database.

    The recipes are read out of the example fragments rather than
    written beside them, which keeps them from naming a file that moved.
    What that cannot keep them from is naming a fragment that no longer
    validates, an entity name a sibling fragment stopped creating, or a
    reference whose target is written after it rather than before. Only
    running the list finds those, and running it in the published order
    is what makes the order part of what is checked.

    The credentials the secrets recipe stores are read from stdin, which
    is where those commands read them from and the reason none of them
    takes the value as an argument.

    The exception is in the name because a claim this test cannot make
    should not be one a reader has to open it to find: the bare `apply`
    each preset's recipe ends with is not run. An operator runs it, and
    installing a preset builds what it names, which here is a cloud
    stack whose credentials the recipe stores two lines later and a
    local stack whose models are a download. The published page says the
    same thing, in `RECIPES_INTRO`, so the reference does not claim
    coverage this lane does not give it.
    """
    monkeypatch.chdir(SERVER)
    monkeypatch.setenv(cli.API_URL_ENV, isolated.api_url)

    published = [
        line.removeprefix(f"{cli.PROGRAM} ")
        for recipe in docgen.recipes()
        for line in recipe.commands
    ]
    assert published, "no recipe is published, so what follows is vacuous"
    assert NOT_INSTALLED in [line.split() for line in published], (
        "the skipped line is no longer published, so the exception is about nothing"
    )
    # And the page discloses it, so a reader of the reference is not told
    # a list is run that is one line short of being run.
    assert f"{cli.PROGRAM} {' '.join(NOT_INSTALLED)}" in cli.RECIPES_INTRO, (
        "the published recipes intro does not name the line this lane skips"
    )

    for line in published:
        argv = line.split()
        if argv == NOT_INSTALLED:
            continue
        secret = argv[1:3] == ["secret", "set"]
        assert run(*argv, stdin=SECRET if secret else None) == 0, line
        assert capsys.readouterr().out.strip(), line

    # The deployment those commands add up to, read back through the
    # command an operator would read it back with.
    assert run("list") == 0
    summary = capsys.readouterr().out
    assert "assistant" in summary
    assert SECRET not in summary


def test_the_lane_s_server_booted_from_the_environment_alone(
    deployed: Live, capsys: pytest.CaptureFixture[str]
) -> None:
    """No configuration file anywhere, which is decision 9's claim about
    the quick start.

    The server every case above talked to was composed by
    `load_boot_config()` with no path and no `VINGA_CONFIG`, so its file
    half came from the settings machinery reading the `VINGA_DB_*` names
    and the `VINGA_SERVER__*` ones, and the one variable that was set is
    which database it serves. Three things say that worked: the config
    variable was not left lying in this process's environment for
    something else to have supplied, the server answers on its own port,
    and the database the variable named is the one holding what this
    lane wrote through the API.
    """
    assert CONFIG_ENV not in os.environ

    # Readiness rather than liveness: what this claims is that the server
    # the lane has been talking to is up on its own port and able to take
    # a conversation, which is the stronger of the two answers and the
    # one the rest of this lane depends on.
    with urllib.request.urlopen(f"{deployed.origin}/readyz", timeout=10) as response:
        assert response.status == 200

    engine = open_database(deployed.database)
    try:
        stored = ConfigStore(engine, load_keys()).load()
    finally:
        engine.dispose()
    assert "sam" in stored.domain.agents

    # And the CLI, which resolved this server's address from the
    # environment too, is reading the same store.
    assert run("agent", "show", "sam") == 0
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

    # And every noun path was reached through one of them, which is the
    # other half of the tree the table describes: a group nothing was
    # driven under is a heading with no command behind it.
    reached = {words[:length] for words in DRIVEN for length in range(1, len(words) + 1)}
    assert set(cli.GROUPS) <= reached
