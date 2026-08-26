"""What each dependency tier is declared to be, and what each extra's
distributions import as.

Two lanes ask. The tier closure lane proves what the DECLARATION
resolves to, by syncing an environment against the lock and comparing it
exactly. The wheel lane proves what the ARTIFACT carries, by reading the
built wheel's own `Requires-Dist` and by asserting the serve half is
absent from the environment that wheel was installed into. Both need the
same two facts, and two copies of a tier list is the pending bug the
design guide names: the copies drift, and the lane holding the older one
reports a tier that has not existed for a while.

The import-name map is written out rather than derived. A
distribution's import name is not in its requirement string, and
guessing it by replacing hyphens is how a typo becomes a check that
always passes; each lane holds the map to covering the declared tier
exactly, so a dependency added to `serve` or to `sim` without a name here
fails.

This module is the one home both lanes read a tier from, which is why
`sim` had to arrive HERE and not only as a third fixture. `declared()`
returning two sets while `pyproject.toml` declared three would have let
an extra missing from the wheel's own metadata pass every lane, which is
exactly the gap the wheel's metadata check was added to close for
`serve`.
"""

import tomllib
from collections.abc import Sequence
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]

# Which top-level module each serve-only distribution installs, so a
# negative check can ask the interpreter rather than only the metadata.
# A distribution can be absent from the metadata and its module still be
# importable, because something else vendored or depended on it, and
# that is the case a name check alone would miss.
SERVE_MODULES = {
    "alembic": "alembic",
    "anthropic": "anthropic",
    "av": "av",
    "cryptography": "cryptography",
    "fastapi": "fastapi",
    "mcp": "mcp",
    "openai": "openai",
    # The Postgres driver. The plain source distribution, which is what
    # `serve` declares: the binary one is a separate distribution the
    # dev group and the image name for themselves, so a negative check
    # for `psycopg` is a check for this one being absent.
    "psycopg": "psycopg",
    "pysilero-vad": "pysilero_vad",
    "sqlalchemy": "sqlalchemy",
    "uvicorn": "uvicorn",
}

# And the same map for the `sim` extra, which is one distribution.
#
# Its import name happens to be its distribution name, and it is still
# written out rather than derived, for the reason above: a map that
# computed the name would agree with itself about a typo.
#
# It is separate from `SERVE_MODULES` rather than merged into it because
# the negative checks are separate questions: a client install carries
# neither, a `[sim]` install carries this one and none of those, and a
# lane that could not tell them apart could not say either sentence.
SIM_MODULES = {
    "websockets": "websockets",
}


def requirement_names(entries: Sequence[str]) -> set[str]:
    """The distribution names out of a list of requirement strings,
    normalized the way an installed environment reports them."""
    names = set()
    for entry in entries:
        name = entry.split(";")[0].split("[")[0]
        for marker in ("==", ">=", "<=", "~=", "!=", ">", "<"):
            name = name.split(marker)[0]
        names.add(name.strip().lower().replace("_", "-"))
    return names


def declared() -> tuple[set[str], set[str], set[str]]:
    """The three tiers' DIRECT dependencies, read off `pyproject.toml`.

    The independent oracle both lanes keep beside whatever they compute:
    six names, ten and one, written by hand in the declaration under
    test, so a closure or a metadata block is checked against something
    that came from somewhere else. Either alone would be a graph agreeing
    with itself.

    Three rather than two since #248. The two `faster-whisper` and
    `piper` extras are deliberately not among them: they are provider
    options a deployment chooses, installed into an image that already
    has the server half, and no lane holds an environment to either. The
    three here are the three DOORS into this package, and each has a lane
    that syncs it.
    """
    project = tomllib.loads((PROJECT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    client = requirement_names(project["dependencies"])
    serve = requirement_names(project["optional-dependencies"]["serve"])
    sim = requirement_names(project["optional-dependencies"]["sim"])
    return client, serve, sim
