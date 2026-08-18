"""What importing the onboarding package costs.

`samtal-server config` derives the onboarding URL, prints the origin a
deployment names itself by, and renders the configuration reference and
the OpenAPI document, all with no server anywhere. The configuration API
is the same story: `document()` builds the whole application to describe
it. Both read this package, and until issue #143 both had to defer the
read to a function body, because the module that held the pending table
and the origin helpers also held a router over the OTA handlers, and
that reached the websocket session and every provider a conversation
needs.

The split retired those deferrals, and this is what keeps them retired:
a cost like that comes back silently, one convenient import at a time,
and the thing it breaks (a CLI that got slower, a document render that
loads an audio codec) is not what anybody is testing at the time.

In a subprocess, because the assertion is about what an import pulls in
and this suite's own `sys.modules` has the whole server in it already.
Three cases: the package itself, the CLI that reads it, and the
configuration API's `document()` render, which is the one the two
imports would not catch, since building the application evaluates route
handlers and response models a bare import never touches.

Deliberately NOT asserted here: SQLAlchemy, which IS loaded.
`onboarding.unbound` imports `device.bindings` for the resolution type it
answers about, and the bindings view reads the configuration database.
That is a real dependency of the decision this package makes, it is what
the reads-nothing-device-facing rule always excluded (it holds of `keys`,
`origin` and `pending`, which are the three modules the CLI and the
configuration API actually read), and an assertion against it would be a
claim this package never made.
"""

import subprocess
import sys
import textwrap

# What a conversation costs to load, named one by one rather than as a
# prefix match, because each is a separate way back in: `ota` is the
# router edge the split removed, `ws` is the websocket edge `ota`
# reached through, `providers` is the engine layer behind both,
# `tools.mcp` brings the SDK's clients, and `audio` brings the codecs.
# None of the five is a dependency of anything checked here, so the
# whole set applies to every case below.
FORBIDDEN = (
    "samtal_server.ota",
    "samtal_server.ws",
    "samtal_server.providers",
    "samtal_server.tools.mcp",
    "samtal_server.audio",
)


def _loaded(*statements: str) -> frozenset[str]:
    """Every `samtal_server` module in a fresh interpreter that ran
    exactly these statements and nothing else."""
    source = textwrap.dedent(
        """
        import sys

        {body}

        print("\\n".join(name for name in sys.modules if name.startswith("samtal_server")))
        """
    ).format(body="\n".join(statements))
    finished = subprocess.run(
        [sys.executable, "-c", source],
        capture_output=True,
        text=True,
        check=True,
    )
    return frozenset(finished.stdout.split())


def test_importing_onboarding_loads_no_conversation() -> None:
    loaded = _loaded("import samtal_server.onboarding")

    assert "samtal_server.onboarding" in loaded
    assert not loaded & frozenset(FORBIDDEN)


def test_the_configuration_cli_loads_no_conversation_either() -> None:
    """The reader the deferrals existed for, held to the same bound: it
    imports the package's submodules at module scope now, so an edge
    added inside the package would land here too."""
    loaded = _loaded("import samtal_server.config.cli")

    assert "samtal_server.onboarding.origin" in loaded
    assert not loaded & frozenset(FORBIDDEN)


def test_rendering_the_api_document_loads_no_conversation_either() -> None:
    """The other reader, and the one the importing alone would not
    catch.

    `document()` builds the whole configuration application to describe
    it, with no server anywhere, so it reaches route handlers and
    response models that a bare import of the module never evaluates.
    That is the claim the changelog makes for this split, and importing
    `config.api` is not it: the render is where a lazy edge would fire.
    """
    loaded = _loaded(
        "from samtal_server.config.api import document",
        "document()",
    )

    assert "samtal_server.config.api" in loaded
    assert not loaded & frozenset(FORBIDDEN)
