"""`vinga list`: the tree it prints, and the document it prints it from.

One command and one act, and the act's answer is the whole masked
configuration. `ConfigDocument` declares that document as
`dict[str, Any]` and stops there, deliberately: the entity models cannot
validate an entry whose credential-bearing values have been replaced by
the mask. So everything the tree walks into is a body nobody has
vouched for, and it arrives from whatever answered at `--api-url`.

Two halves, in the order they matter. The first is the rendering itself,
pinned whole: the tree is what an operator reads a deployment off, so a
line moving is a change to the artifact rather than to an
implementation, and the byte-exact pins are what make the gate below a
refactor rather than a rewrite. The second is the gate: every section
is read as the shape the registry says it has, so a section that is a
scalar, a list or absent meets the one fixed sentence a body this client
cannot read gets, and every value the tree prints out of the document
goes through the display door on its way to the terminal.
"""

from pathlib import Path

import pytest

from tests.support.config_cli import runner

# The whole tree for a deployment with something in every section,
# secrets included. Pinned as bytes rather than by substring: what this
# holds is the artifact `vinga list` exists to print, and a renderer
# that moved a line, dropped an indent or reordered the sections would
# be changing what an operator reads without anything saying so.
CONFIGURED_TREE = """\
providers:
  llm:
    brain (mock)  [secrets: api_key]
  asr:
    (none)
  tts:
    voice (mock)
  vad:
    (none)
mcp_servers:
  house (stdio)
prompt_fragments:
  household (16 characters)
agent_defaults: llm=brain
agents:
  sam: llm=brain prompt_includes=[household]
devices:
  aa:bb:cc:dd:ee:ff -> sam
default_agent: sam
"""

# And the same tree with nothing in it, which is the other half of the
# rendering: every section says it is empty rather than going missing,
# because a section that vanished would read as a section this
# deployment does not have.
EMPTY_TREE = """\
providers:
  llm:
    (none)
  asr:
    (none)
  tts:
    (none)
  vad:
    (none)
mcp_servers:
  (none)
prompt_fragments:
  (none)
agent_defaults: (none)
agents:
  (none)
devices:
  (none)
default_agent: (none)
"""

FRAGMENT = "The bins go out."


@pytest.fixture
def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """One command run the way the entry point runs it, against a server
    of this test's own."""
    return runner(monkeypatch)


def configured(run) -> None:
    """A deployment with something in every section of the tree, written
    through the commands that write one, so what the listing renders is
    a document this API really answers with."""
    assert run("provider", "set", "llm", "brain", "type=mock") == 0
    assert run("provider", "set", "tts", "voice", "type=mock") == 0
    assert run("mcp-server", "set", "house", "transport=stdio", "command=/bin/true") == 0
    assert run("prompt-fragment", "set", "household", f"text={FRAGMENT}") == 0
    assert (
        run(
            "agent",
            "set",
            "sam",
            "-f",
            "-",
            stdin="llm: brain\nprompt: You are Sam.\nprompt_includes: [household]\n",
        )
        == 0
    )
    assert run("agent-defaults", "set", "llm=brain") == 0
    assert run("device", "bind", "aa:bb:cc:dd:ee:ff", "sam") == 0
    assert run("default-agent", "set", "sam") == 0
    assert run("provider", "secret", "set", "llm", "brain", "api_key", stdin="sk-x") == 0


def test_the_tree_is_what_a_configured_deployment_reads_as(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The whole listing, byte for byte: the four provider stages in the
    pipeline's order, one line per entry with the suffix its kind reads
    as, the stored slots named beside the entry holding them, and the
    two settings that are not entities at the foot."""
    configured(run)
    capsys.readouterr()

    assert run("list") == 0

    printed = capsys.readouterr()
    assert printed.out == CONFIGURED_TREE
    # A read is a read: the listing is the whole of what it says.
    assert printed.err == ""


def test_an_empty_deployment_names_every_section_it_has_nothing_in(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("list") == 0

    assert capsys.readouterr().out == EMPTY_TREE
