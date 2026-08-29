"""What every `vinga-server config` suite runs a command with.

The config command group's tests were one file until #139 split them
along the boundaries that issue produced, and five suites now drive the
same entry point: the acceptance spine, the transport client, the
runtime renderings, the secrets, and the grammar.
What they share is the scaffolding, and it lives here rather than in
whichever of them happened to be written first.

`runner` is the whole of it: one command run the way the entry point
runs it, against a server of the calling test's own. It is a factory
rather than a fixture because a fixture cannot be imported and used as a
fixture, and a `run` visible to the whole unit lane would be a name
nobody could place; each suite spends four lines wrapping it instead.

The sentinels are here for the same reason: a substring assertion about
a credential is worth nothing if the credential it looks for is not the
one the command was given, and six copies of a constant is six chances
for that.
"""

import contextlib
import io
import sys
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

from tests.support.apps import mounted
from vinga_server.config import cli
from vinga_server.config.api import MOUNT_PATH, build_api
from vinga_server.config.loader import load_file_config
from vinga_server.config.secrets import MASTER_KEY_ENV, generate_key
from vinga_server.onboarding import PendingDevices

# Not real credentials, and shaped so a substring check for one cannot
# match by accident.
SECRET = "sk-test-4f8b2c9e-never-a-real-credential"
OTHER_SECRET = "tok-test-7a1d3f60-never-a-real-credential"

API_SECRET_ENV = "VINGA_API_SECRET"

TOKEN = "test-api-token-" + "0123456789abcdef" * 2

# Leading indentation, an inner blank line and a trailing newline, none
# of which a round trip through YAML, HTTP and the database may tidy up.
FRAGMENT_TEXT = "  The bins go out on Tuesday.\n\n    The radio is called Bosse.\n"

# The fragment exactly as an operator writes it: one key, a literal
# block, and the explicit indentation indicator that is what makes a
# body whose own first line is indented writable in YAML at all.
FRAGMENT_INPUT = "text: |2\n" + "".join(
    f"  {line}\n" if line else "\n" for line in FRAGMENT_TEXT.splitlines()
)


def runner(monkeypatch: pytest.MonkeyPatch, database: str | None = None):
    """Run one command the way the entry point runs it, against a server
    of this test's own.

    The application is built per request rather than once, from the
    database settings the CLI itself would have resolved, because that
    is what a deployment's server does too: the CLI and the server read
    `server.database` through the same machinery and cannot disagree
    about it.

    Each application is served for exactly the length of one command:
    since #142 the configuration API owns a database engine, opened by
    the lifespan `TestClient` enters as a context manager and disposed
    when it leaves, so a client built and never entered would meet the
    API with no engine at all. One command is the right length because it
    is how long the application itself lasts here.
    """
    monkeypatch.delenv("VINGA_CONFIG", raising=False)
    monkeypatch.delenv(cli.API_URL_ENV, raising=False)
    # A database of this runner's own when the caller names one, which
    # is how a test gets two stores: the round trip's whole claim is
    # that the document one deployment exports is the document another
    # is built from, and one store cannot be two deployments.
    if database is not None:
        monkeypatch.setenv("VINGA_DB_NAME", database)
    monkeypatch.setenv(MASTER_KEY_ENV, generate_key())
    monkeypatch.setenv(API_SECRET_ENV, TOKEN)

    reached: list[str] = []
    # One table across the whole test, unlike the application, which is
    # built per request: on a deployment this is state of the running
    # server, and a command that could not see what a previous one left
    # in it would be testing a table nobody has.
    pending = PendingDevices()
    # The running server's MCP managers, for the same reason and put
    # there by the tests that need one. None is what an application
    # built without a server around it gets, which is what every other
    # test here is.
    runtime: dict[str, object] = {
        "mcp_servers": None,
        "reload": None,
        "agent_prompt": None,
        # Which deployment the API says it is, which on a deployment the
        # composition root resolves from the build and the server
        # section. None here for the same reason the three above are
        # None: an application built without a server around it has no
        # deployment to describe, and the identity read refuses.
        "identity": None,
    }
    # Every client the entry point built, kept so a test can read the
    # timeouts a command chose after it has run.
    clients: list[httpx.Client] = []
    # And the transport a command's client is built on, when the test
    # client cannot be it. Empty for every suite but the event tail's:
    # `TestClient` buffers a whole response body before handing it back,
    # and the one answer in this API that never finishes arriving is the
    # event stream, so a command that reads a stream incrementally
    # cannot be driven through it at all. `answering` below is how a
    # test puts one here.
    transport: list[httpx.BaseTransport] = []
    # What holds the clients one command builds open, replaced by `_run`
    # with a fresh one per command and closed when that command ends.
    lifespans = contextlib.ExitStack()

    def factory(base_url: str, token: str) -> httpx.Client:
        reached.append(base_url)
        if transport:
            # A real `httpx.Client`, which is what the entry point
            # builds on a deployment, on a transport of the test's own.
            # Built with the timeouts `cli.build_client` builds with,
            # because a command that sets its own overwrites them and a
            # command that does not is entitled to the module's.
            given = httpx.Client(
                base_url=base_url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=httpx.Timeout(cli.READ_TIMEOUT_S, connect=cli.CONNECT_TIMEOUT_S),
                transport=transport[-1],
            )
            clients.append(given)
            return given
        database = load_file_config(None).server.database
        # The server's token is fixed here and is not the one the CLI
        # resolved. Building the gate out of whatever the client happened
        # to send would make every token the right one, and the
        # token-resolution tests would be asserting nothing: the wrong
        # variable, a stale value and a typo would all authenticate.
        api = build_api(
            TOKEN,
            database,
            pending=pending,
            mcp_servers=runtime["mcp_servers"],
            reload=runtime["reload"],
            agent_prompt=runtime["agent_prompt"],
            identity=runtime["identity"],
        )
        # A base URL with a path prefix is the deployed shape, where the
        # sub-application is mounted on the server's own port, so the
        # fixture mounts it exactly where the server does rather than
        # serving it at the root and letting the prefix go nowhere.
        served = api
        if urlsplit(base_url).path.rstrip("/"):
            assert urlsplit(base_url).path.rstrip("/") == MOUNT_PATH
            served = mounted(api)
        client = lifespans.enter_context(
            TestClient(
                served,
                base_url=base_url,
                headers={"Authorization": f"Bearer {token}"},
            )
        )
        clients.append(client)
        return client

    monkeypatch.setattr(cli, "build_client", factory)

    def _run(*argv: str, stdin: str | None = None) -> int:
        nonlocal lifespans
        if stdin is not None:
            monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
        with contextlib.ExitStack() as this_command:
            lifespans = this_command
            return cli.main(list(argv))

    _run.reached = reached
    _run.pending = pending
    _run.runtime = runtime
    _run.clients = clients
    _run.transport = transport
    return _run


def answering(run, handler: Any) -> None:
    """Answer this runner's requests from a handler of the test's own,
    rather than from an application built per request.

    The seam is the same one every suite here runs through,
    `cli.build_client`; what changes is what the client is built on. A
    handler takes an `httpx.Request` and answers an `httpx.Response`,
    and a response built over an iterator is one whose body arrives in
    pieces, which is the whole point: the event stream is an answer that
    never finishes, and the buffered test client cannot hand back a body
    that has no end.

    A handler may raise instead of answering, which is how a connection
    that never opens is written, and its iterator may raise partway
    through, which is how one that dies mid-stream is.
    """
    run.transport.append(httpx.MockTransport(handler))


# Which row of the grammar a command line names
#
# Three readers ask it and none of them may answer it themselves: the
# live lane records what it drove against the registration table, the
# wheel lane maps the same table onto commands it runs as a subprocess,
# and the spelling census holds every quoted invocation to naming
# something that exists. Two implementations of longest-prefix matching
# would be one pending bug, and the bug is silent: a matcher that cannot
# see a row reports full coverage of a tree with a hole in it.
#
# Longest prefix and not a fixed word count, because the tree is not one
# depth. `provider secret set` is three words and `provider` is one, and
# a matcher that took two would attribute the first to the second and
# mark the wrong row driven.
#
# And the prefix alone is not enough, which is the half M3 added. A
# prefix match stopped at the last registered word and never asked what
# came after it, so `show provider llm claude` resolved to the flat
# `show` row and the three words behind it went unread, though `show`
# takes no positional at all and the tree refuses that line. That is how
# a spelling the rename retired went on passing the census guard for two
# milestones. So a candidate row also has to be able to TAKE what
# follows it, and how much that is is read off the built tree rather
# than listed here: a command's positional arguments are its budget, and
# a variadic one is no budget at all.

_BY_WORDS: dict[tuple[str, ...], cli.Command] = {row.words: row for row in cli.COMMANDS}

_FIRST_WORDS = {row.words[0] for row in cli.COMMANDS}

_DEEPEST = max(len(row.words) for row in cli.COMMANDS)


def _budgets() -> dict[tuple[str, ...], int | None]:
    """How many words each leaf of the built tree takes after its own,
    or None where it takes any number.

    Walked off `cli.command()` rather than off `COMMANDS`, because what
    a command accepts is declared in the signature its `declare` builds
    and only the tree has read it. Duck-typed rather than checked
    against `click`, because Typer builds its own vendored classes and
    an `isinstance` against the installed package answers False for
    every one of them, which would silently make every group a leaf.
    """
    budgets: dict[tuple[str, ...], int | None] = {}

    def walk(group: Any, prefix: tuple[str, ...]) -> None:
        for word, below in group.commands.items():
            words = (*prefix, word)
            if hasattr(below, "commands"):
                walk(below, words)
                continue
            counts = [
                parameter.nargs
                for parameter in below.params
                if getattr(parameter, "param_type_name", "") == "argument"
            ]
            budgets[words] = None if any(count < 0 for count in counts) else sum(counts)

    walk(cli.command(), ())
    return budgets


_TAKES = _budgets()


def _fits(words: tuple[str, ...], rest: Sequence[str]) -> bool:
    """Whether a row could be given what follows its own words.

    Counted up to the first option, because everything after one is that
    option's business and this is a matcher rather than a parser: a
    value can look like anything, and guessing which options take one
    would be a second copy of the declaration. Leniency there is the
    right direction, since what this exists to catch is a retired
    spelling carrying its old address, and those carry no options at
    all.
    """
    budget = _TAKES.get(words)
    if budget is None:
        return True
    given = 0
    for word in rest:
        if word.startswith("-"):
            break
        given += 1
    return given <= budget


def registered(argv: Sequence[str]) -> tuple[str, ...] | None:
    """Which row of `cli.COMMANDS` this command line names, or None.

    The words are found rather than assumed to be first, because the
    global options are accepted before the command word as well as after
    it. From there the longest registered prefix that could take what
    follows it wins, which is how `provider secret set` is told from
    `provider`, `device show` from `show`, and both from a line naming a
    row the tree does not have.

    Read off the table, never a list of it: a command added to the
    grammar is addressed here the day it is added.
    """
    for index, word in enumerate(argv):
        if word not in _FIRST_WORDS:
            continue
        for length in range(min(_DEEPEST, len(argv) - index), 0, -1):
            candidate = tuple(argv[index : index + length])
            if candidate in _BY_WORDS and _fits(candidate, argv[index + length :]):
                return candidate
    return None


def logged(caplog: pytest.LogCaptureFixture) -> str:
    """Every record written while a command ran, whoever wrote it,
    rendered as anything reading them would.

    The message, the arguments behind it and the exception info a
    traceback would be built from, because a value that reached a record
    as an argument is a value the formatter puts back into the line.

    Every record and not only this server's own, which is the whole of
    what a no-leak claim about logs can honestly mean: a credential in
    httpx's request line is in the deployment's log file exactly as much
    as one in a line this code wrote. The config CLI holds that library
    quiet around its request for precisely that reason (`REQUEST_LOGGERS`
    in `config/cli.py`), so there is nothing left here to filter out, and
    a filter would hide the regression if the quieting were removed.
    """
    return "\n".join(
        f"{record.name}\n{record.getMessage()}\n{record.args!r}\n{record.exc_info!r}"
        for record in caplog.records
    )


def chain(exc: BaseException) -> str:
    """Everything an exception carries, including what a chain walker
    would find behind it: its text, its arguments, what its own
    attributes hold, and the same again for every cause and context."""
    parts: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        parts += [repr(current), str(current), repr(current.args), _held(current)]
        current = current.__cause__ or current.__context__
    return "\n".join(parts)


def _held(exc: BaseException) -> str:
    """What one exception's attributes hold, and what theirs hold.

    Two levels rather than one, because the lesson that made this
    necessary is a PyYAML mark: the exception's repr says nothing, its
    `problem_mark` attribute is an object, and that object's `buffer` is
    the whole source being parsed. A walk that stopped at the repr would
    miss exactly what it is looking for.
    """
    parts: list[str] = []
    for value in vars(exc).values():
        parts.append(repr(value))
        if hasattr(value, "__dict__"):
            parts += [repr(inner) for inner in vars(value).values()]
    return "\n".join(parts)


def document(out: str) -> object:
    """A `show` document without the secret notes underneath it."""
    return yaml.safe_load("\n".join(line for line in out.splitlines() if not line.startswith("#")))


def showing(run, mac: str = "aa:bb:cc:dd:ee:ff") -> str:
    """One device waiting, put there the way the OTA endpoint puts it."""
    return run.pending.observe(
        mac,
        "6f1a2b3c-4d5e-6f70-8192-a3b4c5d6e7f8",
        "waveshare-esp32-s3-touch-lcd-1.54",
        "2.4.0",
    ).device.code
