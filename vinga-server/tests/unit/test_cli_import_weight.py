"""The shape that makes the CLI extractable, held by three assertions.

Issue #287 is the exit this repository has decided on and deliberately
not taken: if the configuration CLI is ever published on its own, it
becomes a client generated from the committed OpenAPI document, with the
server on the other side of an HTTP boundary and no shared import at
all. Structuring for that exit is worth nothing as an intention, so it
lands as three tests that fail when it stops being true.

1. **The reach is an inventory.** The exact set of `vinga_server`
   modules that importing `config.cli` pulls in at module scope is
   written down. Widening it is a review event with a name, and #287's
   gap census becomes a diff of this set rather than fresh archaeology.

2. **The dependency arrow points one way.** Nothing under
   `vinga_server` imports `config.cli` except `main.py`, in the branch
   that dispatches to it. That one edge is the declared exception and
   the only one an extraction would have to cut.

3. **Answers are read through `config/responses.py` alone.** That
   module imports nothing of this server, because it is the half a
   generated client substitutes for. The CLI never validates an answer
   against a server-side model.

The first runs in a subprocess, because the assertion is about what an
import pulls in and this suite's own `sys.modules` has the whole server
in it already. The other two read the source, because an import graph
answers what was reached at run time and the question here is what is
written down.
"""

import ast
import subprocess
import sys
import textwrap
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2] / "src" / "vinga_server"

# Every `vinga_server` module importing `config.cli` loads, and the
# reason each is on the list.
#
# Twenty-six, and each of them is the client half of something: the
# models and the registry the grammar is derived from, the loader that
# reads the file half, the renderers the four document commands print,
# the response shapes the answers are read through, the transport policy
# a fragment is checked against before it travels, and the logging
# boundary that quiets the request loggers. `runtime.prompt` and
# `tools.names` arrive under `models`, which declares an agent's
# fragments and an MCP grant's tool names in their own types.
#
# Seven arrived with `vinga simulator` (#248), deliberately and as a
# review event with a name, which is what this list is for.
# `device_endpoint` is the device-facing address policy and the request
# boundary the doctor and the simulator share; `simulator.board` and
# `simulator.capabilities` are the grammar's two concrete imports, in the
# empty package they sit in; and `protocol` and its three modules arrive
# under them, because the simulator holds no copy of the wire and reads
# the same module the server does. Every one of them is client-tier
# pure: the whole of `protocol` imports `json`, `struct`, `dataclasses`,
# `collections.abc` and pydantic and nothing else.
#
# `simulator.utterance` joined them in M2 of #248: it is what the
# packaged sentence IS, which is `json`, `dataclasses`,
# `importlib.resources` and `protocol.framing`, and nothing about
# reading a file the wheel already carries is behind an extra.
#
# `simulator.conversation` is deliberately NOT here, and
# `test_the_simulator_s_conversation_half_is_not_imported_eagerly` below
# says so: it is the only module in the tree that imports `websockets`,
# and M2's extra gate depends on that import happening inside `run`'s own
# arm rather than at the top of the grammar.
#
# `broken_pipe` joined them with `events tail` (#342), and it is the
# cheapest entry on this list: `os`, `signal` and `sys`, and nothing of
# this server at all. It is here rather than duplicated because what it
# holds is a two-part trap (the shell's status for a process cut off by
# SIGPIPE, and the descriptor redirection that keeps the interpreter's
# final flush from raising again), and the other program that answers a
# closed pipe is `events_cli`, which the client tier may not reach: it
# would drag the whole event catalog in behind it.
#
# What is NOT here is the other point of the list: no `store`, so no
# SQLAlchemy; no `secrets`, so no cryptography; no `api` and no
# `onboarding`, so no FastAPI; no `db`, so no Alembic. Each of those
# left in the #223 milestone, and each would come back one convenient
# import at a time.
CLI_REACH = frozenset(
    {
        "vinga_server",
        "vinga_server.broken_pipe",
        "vinga_server.config",
        "vinga_server.config.cli",
        "vinga_server.config.docgen",
        "vinga_server.config.entities",
        "vinga_server.config.loader",
        "vinga_server.config.models",
        "vinga_server.config.printing",
        "vinga_server.config.provider_options",
        "vinga_server.config.responses",
        "vinga_server.config.transport",
        "vinga_server.device_endpoint",
        "vinga_server.logs",
        "vinga_server.protocol",
        "vinga_server.protocol.framing",
        "vinga_server.protocol.mcp",
        "vinga_server.protocol.messages",
        "vinga_server.runtime",
        "vinga_server.runtime.prompt",
        "vinga_server.simulator",
        "vinga_server.simulator.board",
        "vinga_server.simulator.capabilities",
        "vinga_server.simulator.utterance",
        "vinga_server.tools",
        "vinga_server.tools.names",
    }
)

# The module the gate depends on not being reached, named here so the
# claim is a constant rather than a string inside one assertion.
GATED_MODULE = "vinga_server.simulator.conversation"

# The one module allowed to import the CLI, and the one #287 removes.
# `main.py` dispatches `vinga-server config ...` to it, inside the
# branch that recognized the word.
CLI_IMPORTERS = frozenset({"vinga_server/main.py"})

RESPONSES = SOURCE / "config" / "responses.py"


def _loaded(statement: str) -> frozenset[str]:
    """Every `vinga_server` module in a fresh interpreter that ran
    exactly this statement and nothing else."""
    source = textwrap.dedent(
        """
        import sys

        {body}

        print("\\n".join(name for name in sys.modules if name.startswith("vinga_server")))
        """
    ).format(body=statement)
    finished = subprocess.run(
        # `-B` for the reason `tests/conftest.py` clears the caches: a
        # `.pyc` is validated on the source's size and its mtime in
        # whole seconds, and a child interpreter without this flag
        # writes a full set back after that clearing.
        [sys.executable, "-B", "-c", source],
        capture_output=True,
        text=True,
        check=True,
    )
    return frozenset(finished.stdout.split())


def _imports(path: Path) -> set[str]:
    """Every module name one file imports, at any depth of its body.

    Read from the syntax rather than from `sys.modules`, because the
    question is what is written down: an import inside a function is
    still an edge somebody would have to cut, and it is exactly the
    shape a run-time graph would answer differently about depending on
    which branch ran.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def test_the_cli_reaches_exactly_this_much_of_the_server() -> None:
    """The inventory, both ways.

    Both ways because each direction is a different failure. A module
    that appeared is weight the client half took on, and the ones that
    would appear first are the ones that just left. A module that
    disappeared means the list has stopped describing anything, and a
    stale allowlist is worth less than none.
    """
    assert _loaded("import vinga_server.config.cli") == CLI_REACH


def test_the_simulator_s_conversation_half_is_not_imported_eagerly() -> None:
    """The other side of the inventory, stated as its own claim because
    it is the one a gate depends on.

    M2 puts the `websockets` import inside `simulator/conversation.py`
    and gates `simulator run` on it. That gate is worth nothing if
    importing the grammar loads the module: the bare install would fail
    at import rather than answering the fixed sentence, and every command
    of the tree would fail with it. The inventory above already says so
    by omission; this says it by name, so a re-export added to
    `simulator/__init__.py` fails a test that names what it broke.
    """
    assert GATED_MODULE not in _loaded("import vinga_server.config.cli")


def test_nothing_but_the_entry_point_imports_the_cli() -> None:
    """The arrow, held to one edge.

    An extraction is only as cheap as the number of places that reach
    back. One reaches back, it is the dispatch that exists to reach it,
    and it is the edge #287 removes.
    """
    importers = {
        str(path.relative_to(SOURCE.parent))
        for path in SOURCE.rglob("*.py")
        if "vinga_server.config.cli" in _imports(path)
    }
    assert importers == CLI_IMPORTERS


def test_the_response_shapes_import_nothing_of_this_server() -> None:
    """The half a generated client substitutes for.

    `responses.py` declares what an answer looks like, and it is the one
    module of the client half a generator would replace outright. An
    import of a server-side model here would mean the CLI was validating
    an answer against the thing that produced it, which is the drift a
    contract exists to catch.
    """
    reached = {name for name in _imports(RESPONSES) if name.startswith("vinga_server")}
    assert reached == set()
