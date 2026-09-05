"""Rendering a document in an interpreter that has imported nothing else.

The configuration package's rendering commands are usable on a machine
with no database, no encryption key and no configuration file, and that
claim is about an import graph rather than about intent: a module added
to the top of `docgen.py` next year would retract it silently. So it is
pinned by driving a child interpreter that starts empty, rendering the
document there, and reading back what got loaded.

Two suites make that claim now, about two documents rendered from the
same models, which is why the allow list and the runner are here rather
than in whichever of them was written first: a test module may not
import another test module (`test_support_boundaries.py`), and two
copies of a closed set are two sets that come to disagree.

`-B` for the reason `test_config_entities.py` gives: a child that writes
bytecode back hands the next command the stale cache `conftest.py` just
cleared.
"""

import json
import subprocess
import sys

# What rendering these documents is allowed to load. Named one by one
# rather than matched on a prefix, for the reason the registry's own
# allow list is: each absent module is a separate way for these commands
# to stop being runnable where they are meant to run.
ALLOWED_IMPORTS = frozenset(
    {
        "vinga_server",
        "vinga_server.config",
        "vinga_server.config.docgen",
        "vinga_server.config.entities",
        "vinga_server.config.loader",
        "vinga_server.config.models",
        # The one name this set gained for #88, and the reason it is
        # safe: `provider_options` declares the pydantic models a
        # provider type's options are, and imports pydantic and
        # `config.models` and nothing else. The `heavy` assertion in
        # each caller is what holds that claim rather than this comment,
        # and it is still empty. The reason it is needed is that the
        # documents rendered here describe those options, so the module
        # that declares them is on the rendering path. What must stay
        # out is `vinga_server.providers`, whose package `__init__`
        # re-exports the engine layer; that is why the declaration lives
        # on this side of the boundary at all.
        "vinga_server.config.provider_options",
        "vinga_server.runtime",
        "vinga_server.runtime.prompt",
        "vinga_server.tools",
        "vinga_server.tools.names",
    }
)

# The modules a rendering must not have reached: the database driver,
# the cryptography a key would be loaded with, and the web stack the
# application would bring.
HEAVY = ("sqlalchemy", "cryptography", "fastapi", "httpx")


def imported_alone(body: str) -> dict:
    """What a fresh interpreter loaded to run `body`, and what it
    rendered.

    The body is source, run after nothing but `json` and `sys`, and its
    one contract is that it leaves the size of what it rendered in
    `rendered`: a document that came out empty would otherwise satisfy
    every assertion about an import graph.
    """
    program = "\n".join(
        (
            "import json",
            "import sys",
            "",
            body,
            "",
            "print(json.dumps({",
            '    "loaded": sorted(n for n in sys.modules if n.startswith("vinga_server")),',
            f'    "heavy": sorted(n for n in {list(HEAVY)!r} if n in sys.modules),',
            '    "rendered": rendered,',
            "}))",
        )
    )
    finished = subprocess.run(
        [sys.executable, "-B", "-c", program], capture_output=True, text=True, check=True
    )
    return json.loads(finished.stdout)
