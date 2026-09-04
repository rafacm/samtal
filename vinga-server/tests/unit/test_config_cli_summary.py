"""The commands that render the whole masked configuration, and the
document all of them render it from.

Three of them, one act apiece and the same act: `list` prints the tree,
`show` prints the document as the YAML file has it, and `export` prints
the same document as the applicable one. (`info` prints a count per
kind off the same read; its own file is beside this one, because what
that command is about is which deployment answered.) `ConfigDocument`
declares that document as `dict[str, Any]` and stops there,
deliberately: the entity models cannot validate an entry whose
credential-bearing values have been replaced by the mask. So everything
these renderers walk into is a body nobody has vouched for, and it
arrives from whatever answered at `--api-url`.

Two halves, in the order they matter. The first is the renderings
themselves, pinned whole: each is what an operator reads a deployment
off, and `export` is a file another deployment is built from, so a line
moving is a change to the artifact rather than to an implementation.
The byte-exact pins are what make the gate below a refactor rather than
a rewrite. The second is the gate: every section is read as the shape
the registry says it has, so a section that is a scalar, a list or
absent meets the one fixed sentence a body this client cannot read
gets, and every value that reaches a line goes through the display door
on its way to the terminal.
"""

from pathlib import Path

import pytest

from tests.support.config_cli import chain, logged, runner
from tests.support.events import both_formats
from vinga_server.config import cli
from vinga_server.config.loader import ConfigError

# What a body that is not the declared shape carries, so a refusal or a
# line that quoted any of it would be caught. Shaped like a credential
# and not like a name, because what a document holds where a mapping
# belongs is whatever answered at `--api-url`.
ANSWERED = "ans-test-6b2f4c08-never-a-real-value"

# And what a body that IS the declared shape carries in a field the tree
# prints: this one is meant to reach the terminal, so what is asserted
# about it is that it arrives neutralized rather than that it never
# arrives.
STEERING = "\x1b[31mred"

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


# The two documents beside the tree
#
# `show` and `export` render the same read the tree does, and neither is
# a summary: what they print is the document itself, which is what makes
# an export a file another deployment is built from. So they are pinned
# as bytes for the same reason and with more at stake, and the
# deployment behind these pins carries what the tree's does not: an MCP
# server reached over HTTP with a reference-carrying header, a stored
# credential shadowing that reference, and an agent whose grant is
# written in the object form rather than as a bare server name.

# The document half, which `show` prints alone and `export` prints under
# its header. One constant because it IS one artifact: two copies could
# drift, and a gate that perturbed the rendering of one would then be
# caught by only one of the pins.
DOCUMENT = """\
providers:
  llm:
    brain:
      type: mock
  asr: {}
  tts: {}
  vad: {}
mcp_servers:
  house:
    transport: streamable_http
    url: https://example.invalid/mcp
    headers:
      Authorization: $HOUSE_TOKEN
    tool_timeout_s: 15.0
    use_server_instructions: false
prompt_fragments:
  household:
    text: The bins go out.
agent_defaults:
  llm: brain
agents:
  sam:
    prompt: You are Sam.
    llm: brain
    mcp:
    - server: house
      tools:
      - turn_on
devices:
  aa:bb:cc:dd:ee:ff:
  - sam
default_agent: sam
"""

# What `show` writes under it: every stored credential named by its
# location, masked, and marked where it displaces a reference the entity
# also carries.
SHOWN_SECRETS = """\

# stored secrets, set with: vinga <kind> secret set
#   mcp_server house headers.Authorization: ********  \
(used instead of headers.Authorization: $HOUSE_TOKEN)
#   provider llm.brain api_key: ********
"""

# And what `export` writes under it: the same credentials as the
# commands that enter them, in the store's own order.
EXPORTED_SECRETS = """\

# Stored credentials are not exported. Enter each of them after the `vinga import`
# and before the `vinga apply`:
#   vinga mcp-server secret set -- house headers.Authorization
#   vinga provider secret set -- llm brain api_key
"""


def documented(run) -> None:
    """The deployment those two pins are taken from."""
    assert run("provider", "set", "llm", "brain", "type=mock") == 0
    assert (
        run(
            "mcp-server",
            "set",
            "house",
            "-f",
            "-",
            stdin=(
                "transport: streamable_http\n"
                "url: https://example.invalid/mcp\n"
                "headers:\n"
                "  Authorization: $HOUSE_TOKEN\n"
            ),
        )
        == 0
    )
    assert run("prompt-fragment", "set", "household", f"text={FRAGMENT}") == 0
    assert (
        run(
            "agent",
            "set",
            "sam",
            "-f",
            "-",
            stdin=(
                "llm: brain\n"
                "prompt: You are Sam.\n"
                "mcp: [{server: house, tools: [turn_on]}]\n"
            ),
        )
        == 0
    )
    assert run("agent-defaults", "set", "llm=brain") == 0
    assert run("device", "bind", "aa:bb:cc:dd:ee:ff", "sam") == 0
    assert run("default-agent", "set", "sam") == 0
    assert run("provider", "secret", "set", "llm", "brain", "api_key", stdin="sk-x") == 0
    assert (
        run("mcp-server", "secret", "set", "house", "headers.Authorization", stdin="tok-x") == 0
    )


def test_show_prints_the_document_and_names_what_is_stored_beside_it(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    documented(run)
    capsys.readouterr()

    assert run("show") == 0

    printed = capsys.readouterr()
    assert printed.out == DOCUMENT + SHOWN_SECRETS
    assert printed.err == ""


def test_export_prints_the_same_document_as_one_that_can_be_applied(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The header is referenced rather than copied: it is prose with a
    test of its own, and what this pins is the document under it and the
    commands under that."""
    documented(run)
    capsys.readouterr()

    assert run("export") == 0

    printed = capsys.readouterr()
    assert printed.out == cli.EXPORT_HEADER + DOCUMENT + EXPORTED_SECRETS
    assert printed.err == ""


# The document the tree is walked out of
#
# Everything above is rendered from a store this file wrote through the
# commands that write one, so until here the renderer had never been
# handed a document it did not compose itself. `ConfigDocument` says the
# masked half is a mapping and says nothing about what is under it, so
# every case below is a body whose outer shape is the declared one and
# whose insides are not: exactly the body that gets past the act and
# reaches the renderer.


def answering(monkeypatch: pytest.MonkeyPatch, body: object) -> None:
    """A server that answers the whole-configuration read with this
    body, at the seam the act sends its request through."""

    def call(reached, method, path, *rest, **kwargs):
        assert path == "/config", path
        return body

    monkeypatch.setattr(cli, "_call", call)


def document(secrets: object = (), **sections: object) -> dict[str, object]:
    """A whole-configuration answer with something in every section, so
    that what a case replaces is what the tree walks into rather than
    what the act reads."""
    return {
        "config": {
            "providers": {"llm": {"brain": {"type": "mock"}}},
            "mcp_servers": {"house": {"transport": "stdio"}},
            "prompt_fragments": {"household": {"text": "The bins go out."}},
            "agent_defaults": {"llm": "brain"},
            "agents": {"sam": {"llm": "brain"}},
            "devices": {"aa:bb:cc:dd:ee:ff": ["sam"]},
            "default_agent": "sam",
        }
        | sections,
        "secrets": list(secrets) if isinstance(secrets, tuple | list) else secrets,
    }


def without(section: str) -> dict[str, object]:
    """The same answer with one section left out, which is the third way
    a body can be one this client cannot walk."""
    body = document()
    del body["config"][section]  # type: ignore[union-attr]
    return body


UNWALKABLE = [
    # A section that is a list where a mapping belongs, one that is a
    # scalar, and one that is not there at all: the three shapes #347's
    # round found for a count, now for every section the tree reads.
    pytest.param(document(providers=[ANSWERED]), id="providers-is-a-list"),
    pytest.param(document(providers=ANSWERED), id="providers-is-a-scalar"),
    pytest.param(without("providers"), id="providers-is-absent"),
    pytest.param(document(providers={"llm": [ANSWERED]}), id="a-stage-is-a-list"),
    pytest.param(document(providers={"llm": ANSWERED}), id="a-stage-is-a-scalar"),
    pytest.param(document(mcp_servers=[ANSWERED]), id="mcp-servers-is-a-list"),
    pytest.param(document(mcp_servers=7), id="mcp-servers-is-a-number"),
    pytest.param(without("mcp_servers"), id="mcp-servers-is-absent"),
    pytest.param(document(prompt_fragments=[ANSWERED]), id="prompt-fragments-is-a-list"),
    pytest.param(document(prompt_fragments=ANSWERED), id="prompt-fragments-is-a-scalar"),
    pytest.param(without("prompt_fragments"), id="prompt-fragments-is-absent"),
    pytest.param(document(agent_defaults=[ANSWERED]), id="agent-defaults-is-a-list"),
    pytest.param(document(agent_defaults=ANSWERED), id="agent-defaults-is-a-scalar"),
    pytest.param(without("agent_defaults"), id="agent-defaults-is-absent"),
    pytest.param(document(agents=[ANSWERED]), id="agents-is-a-list"),
    pytest.param(document(agents=7), id="agents-is-a-number"),
    pytest.param(without("agents"), id="agents-is-absent"),
    pytest.param(document(devices=[ANSWERED]), id="devices-is-a-list"),
    pytest.param(document(devices=ANSWERED), id="devices-is-a-scalar"),
    pytest.param(without("devices"), id="devices-is-absent"),
    pytest.param(document(default_agent={"leak": ANSWERED}), id="default-agent-is-an-object"),
    pytest.param(document(default_agent=4), id="default-agent-is-a-number"),
    # An entry body that is not a body. The addressing promises a
    # mapping of entries here, and what the tree would do with a scalar
    # is read a key off it and print the answer beside the entry's name,
    # so the refusal is the rendering: a suffix asking a string for its
    # `type` is not a line an operator should be shown.
    pytest.param(document(providers={"llm": {"brain": ANSWERED}}), id="a-provider-is-a-scalar"),
    pytest.param(document(mcp_servers={"house": ANSWERED}), id="an-mcp-server-is-a-scalar"),
    pytest.param(document(prompt_fragments={"household": ANSWERED}), id="a-fragment-is-a-scalar"),
    pytest.param(document(agents={"sam": [ANSWERED]}), id="an-agent-is-a-list"),
    # And a binding that is not the agents it reaches, which is the one
    # section whose entries are a list rather than a body.
    pytest.param(document(devices={"aa:bb:cc:dd:ee:ff": ANSWERED}), id="a-binding-is-a-scalar"),
    pytest.param(
        document(devices={"aa:bb:cc:dd:ee:ff": {"agent": ANSWERED}}),
        id="a-binding-is-a-mapping",
    ),
    # The document's other half, which the tree reads to name the slots
    # a stored secret fills.
    pytest.param(document(secrets=[ANSWERED]), id="a-secret-row-is-a-scalar"),
    pytest.param(
        document(secrets=[{"kind": "provider", "identity": ANSWERED}]),
        id="a-secret-row-is-short-a-field",
    ),
    pytest.param(document(secrets=ANSWERED), id="secrets-is-a-scalar"),
]


@pytest.mark.parametrize("body", UNWALKABLE)
def test_a_document_the_tree_cannot_walk_is_quoted_nowhere(
    body: object,
    run,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One fixed sentence, and never a `TypeError` or a `KeyError` out
    of the boundary.

    The whole listing is printed at once, so a refusal leaves stdout
    empty rather than half a tree: what an operator gets is the sentence
    or the artifact, never the two spliced together.
    """
    answering(monkeypatch, body)
    capsys.readouterr()

    with caplog.at_level(0):
        assert run("list") == 1

    printed = capsys.readouterr()
    assert printed.out == ""
    assert printed.err == cli.UNREADABLE_READ + "\n"
    assert "Traceback" not in printed.err
    for surface in (printed.out, printed.err, logged(caplog), both_formats(caplog)):
        assert ANSWERED not in surface


@pytest.mark.parametrize("body", UNWALKABLE)
def test_no_refusal_of_a_document_is_retained_on_its_chain(body: object) -> None:
    """Read through the act's own renderer, because that is where the
    nesting is read: a refusal built inside the handler would carry the
    body it refused as its `__context__` for anything walking the
    chain."""
    with pytest.raises(ConfigError) as caught:
        cli.LIST.render(cli.LIST.read(body))

    assert str(caught.value) == cli.UNREADABLE_READ
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert ANSWERED not in chain(caught.value)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"secrets": []}, id="the-configuration-is-absent"),
        pytest.param({"config": ANSWERED, "secrets": []}, id="the-configuration-is-a-scalar"),
        pytest.param({"config": [ANSWERED], "secrets": []}, id="the-configuration-is-a-list"),
        pytest.param({"config": document()["config"]}, id="the-secrets-are-absent"),
    ],
)
def test_the_tree_refuses_a_document_that_is_not_one(body: object) -> None:
    """The renderer handed the document directly, which is what says its
    two halves are read here rather than trusted from the act that
    called it. `ConfigDocument` vouches for both of them on the way in,
    and a renderer whose safety depended on being called by that act
    would be safe by arrangement rather than by construction."""
    with pytest.raises(ConfigError) as caught:
        cli.LIST.render(body)

    assert str(caught.value) == cli.UNREADABLE_READ
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert ANSWERED not in chain(caught.value)


def test_the_valid_shape_those_refusals_were_built_from_is_accepted(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The control beside them, which is what makes each refusal about
    the replacement rather than about the body it was made from."""
    answering(monkeypatch, document())
    capsys.readouterr()

    assert run("list") == 0

    assert capsys.readouterr().out.splitlines()[-1] == "default_agent: sam"


def test_a_default_agent_the_answer_left_out_is_the_unset_one(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The one section with no missing case: unset is a configuration
    rather than an absence, so a document that leaves it out says the
    devices map is the allowlist, exactly as a null does."""
    answering(monkeypatch, without("default_agent"))
    capsys.readouterr()

    assert run("list") == 0

    assert "default_agent: (none)\n" in capsys.readouterr().out


# What reaches the terminal
#
# Every value on a line of the tree came out of the document, so every
# one of them is text some far side chose. One case per place a value
# lands: the names the sections are keyed by, the per-kind suffixes, the
# pairs a body is inlined as, the agents a MAC reaches, the slots a
# stored secret fills, and the default agent at the foot.


def rendered(run, monkeypatch: pytest.MonkeyPatch, capsys, **sections: object) -> str:
    answering(monkeypatch, document(**sections))
    capsys.readouterr()
    assert run("list") == 0
    return capsys.readouterr().out


def test_no_value_on_a_line_can_steer_the_terminal(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Everything at once, because the claim is about the artifact
    rather than about one line of it: a tree with an escape sequence
    anywhere in it is one that repaints the terminal it lands on."""
    printed = rendered(
        run,
        monkeypatch,
        capsys,
        providers={"llm": {STEERING: {"type": STEERING}}},
        mcp_servers={STEERING: {"transport": STEERING}},
        prompt_fragments={STEERING: {"text": STEERING}},
        agent_defaults={STEERING: STEERING},
        agents={STEERING: {STEERING: [STEERING]}},
        devices={STEERING: [STEERING]},
        default_agent=STEERING,
        secrets=[
            {
                "kind": "provider",
                "identity": f"llm.{STEERING}",
                "slot": STEERING,
                "shadows": None,
            }
        ],
    )

    assert "\x1b" not in printed
    # Neutralized rather than dropped: something that arrived mangled
    # reads as mangled, and a value silently deleted would read as a
    # deployment that does not have it.
    assert "?[31mred" in printed
    # And the whole of each line arrived: name, suffix and slots on one,
    # the binding on the next, the inlined pairs on a third.
    assert "    ?[31mred (?[31mred)  [secrets: ?[31mred]" in printed
    assert "  ?[31mred -> ?[31mred" in printed
    assert "  ?[31mred: ?[31mred=[?[31mred]" in printed
    assert "default_agent: ?[31mred" in printed


@pytest.mark.parametrize(
    ("section", "body"),
    [
        pytest.param("providers", {"llm": {"brain": {"type": {"leak": ANSWERED}}}}, id="type"),
        pytest.param(
            "mcp_servers", {"house": {"transport": {"leak": ANSWERED}}}, id="transport"
        ),
        pytest.param("prompt_fragments", {"household": {"text": {"leak": ANSWERED}}}, id="text"),
        pytest.param("agents", {"sam": {"llm": {"leak": ANSWERED}}}, id="an-agent-override"),
        pytest.param("agent_defaults", {"llm": {"leak": ANSWERED}}, id="a-default"),
    ],
)
def test_a_mapping_where_a_word_belongs_is_never_opened(
    section: str,
    body: object,
    run,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A body is a mapping and nothing is declared about what a key of
    one holds, so a suffix can be handed a structure where it expects a
    word. What it must not do with one is open it: a mapping reads as
    the fact that it is one, what it holds stays inside it, and nothing
    arrives on the line as a repr."""
    printed = rendered(run, monkeypatch, capsys, **{section: body})

    assert ANSWERED not in printed
    assert "leak" not in printed


def test_a_list_where_a_word_belongs_reads_as_its_items(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The other structure a suffix can be handed, and the one rendering
    it already has: a list is what an agent's includes and grants are,
    so it reads as its items, each of them through the display door."""
    printed = rendered(
        run, monkeypatch, capsys, mcp_servers={"house": {"transport": [STEERING, "stdio"]}}
    )

    assert "  house ([?[31mred, stdio])" in printed
    assert "\x1b" not in printed
