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


BAD_SECTIONS = [
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
]

# The document's other half, read by every rendering: the tree names the
# slots a stored secret fills, `show` writes each location under the
# document, and `export` renders each as the command that enters it.
BAD_SECRETS = [
    pytest.param(document(secrets=[ANSWERED]), id="a-secret-row-is-a-scalar"),
    pytest.param(
        document(secrets=[{"kind": "provider", "identity": ANSWERED}]),
        id="a-secret-row-is-short-a-field",
    ),
    pytest.param(document(secrets=ANSWERED), id="secrets-is-a-scalar"),
]

UNWALKABLE = BAD_SECTIONS + BAD_SECRETS

# A location naming a kind this client has no command for, which only
# `export` reads: the tree and `show` name a location and look its body
# up with a `.get`, and `export` renders the command that enters it, so
# it is the one that has to turn a kind into a noun.
#
# It used to do that with a subscript, so what left the boundary was a
# `KeyError` whose one argument was the value the answer supplied: not a
# traceback with a value behind it, but a traceback that IS the value.
UNKNOWN_KIND = document(
    secrets=[{"kind": ANSWERED, "identity": "llm.brain", "slot": "api_key", "shadows": None}]
)


def refusal(
    command: str,
    body: object,
    run,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One rendering of an unreadable document, and the whole surface it
    is refused across.

    Each of these commands prints its output at once, so a refusal
    leaves stdout empty rather than half an artifact: what an operator
    gets is the sentence or the document, never the two spliced
    together.
    """
    answering(monkeypatch, body)
    capsys.readouterr()

    with caplog.at_level(0):
        assert run(command) == 1

    printed = capsys.readouterr()
    assert printed.out == ""
    assert printed.err == cli.UNREADABLE_READ + "\n"
    assert "Traceback" not in printed.err
    for surface in (printed.out, printed.err, logged(caplog), both_formats(caplog)):
        assert ANSWERED not in surface


@pytest.mark.parametrize("body", UNWALKABLE)
@pytest.mark.parametrize("command", ["list", "show"])
def test_a_document_a_rendering_cannot_walk_is_quoted_nowhere(
    command: str,
    body: object,
    run,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The two renderings that walk into the document: the tree reads
    every section to print a line per entry, and `show` reads the
    provider and MCP sections to say what each stored credential
    displaces."""
    refusal(command, body, run, monkeypatch, capsys, caplog)


@pytest.mark.parametrize("body", [*BAD_SECRETS, pytest.param(UNKNOWN_KIND, id="unknown-kind")])
def test_an_export_refuses_the_half_it_reads(
    body: object,
    run,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`export` walks into the secrets half and nothing else, so this is
    the half it refuses, kind and all."""
    refusal("export", body, run, monkeypatch, capsys, caplog)


def test_an_unknown_secret_kind_leaves_nothing_on_the_chain() -> None:
    """The case the fixed sentence matters most for: what it replaces
    put the answer's own value into the exception's arguments, where
    anything walking the chain would find it."""
    with pytest.raises(ConfigError) as caught:
        cli.EXPORT_ALL.render(cli.EXPORT_ALL.read(UNKNOWN_KIND))

    assert str(caught.value) == cli.UNREADABLE_READ
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert ANSWERED not in chain(caught.value)


@pytest.mark.parametrize("body", BAD_SECTIONS)
def test_an_export_prints_back_a_section_it_never_walks_into(
    body: object,
    run,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The one rendering that does not read the sections, and the
    decision rather than the oversight.

    What `export` prints is the document itself, dumped as YAML, plus
    the commands for the credentials beside it. It dereferences neither
    a section nor an entry, so there is nothing in there for it to
    stumble over, and refusing to hand an operator their configuration
    back over a section this command never looks at would be a gate
    doing harm rather than work.

    Which is why the sentinel is on stdout here and only here: it is the
    answer being printed back, which is the whole job. It is still
    nowhere else, and no traceback is raised over it.
    """
    answering(monkeypatch, body)
    capsys.readouterr()

    with caplog.at_level(0):
        assert run("export") == 0

    printed = capsys.readouterr()
    assert printed.out.startswith(cli.EXPORT_HEADER)
    assert printed.err == ""
    for surface in (printed.err, logged(caplog), both_formats(caplog)):
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


NOT_A_DOCUMENT = [
    # The outer object itself, which is looked up in before anything
    # else and so has to be read before anything else: `.get` on a
    # string is an `AttributeError`, and one raised a level above the
    # sections is the same traceback the sections' own gate exists to
    # prevent.
    pytest.param(ANSWERED, id="the-answer-is-a-scalar"),
    pytest.param([ANSWERED], id="the-answer-is-a-list"),
    pytest.param(7, id="the-answer-is-a-number"),
    pytest.param(None, id="the-answer-is-null"),
    # And its two halves.
    pytest.param({"secrets": []}, id="the-configuration-is-absent"),
    pytest.param({"config": ANSWERED, "secrets": []}, id="the-configuration-is-a-scalar"),
    pytest.param({"config": [ANSWERED], "secrets": []}, id="the-configuration-is-a-list"),
    pytest.param({"config": document()["config"]}, id="the-secrets-are-absent"),
]


@pytest.mark.parametrize("body", NOT_A_DOCUMENT)
@pytest.mark.parametrize("act", ["LIST", "COUNTS", "SHOW_ALL", "EXPORT_ALL"])
def test_a_whole_document_renderer_refuses_what_is_not_one(act: str, body: object) -> None:
    """Every renderer of the whole configuration, handed the answer
    directly, which is what says each reads it rather than trusting the
    act that called it. `ConfigDocument` vouches for the outer object
    and both halves on the way in, and a renderer whose safety depended
    on being called by that act would be safe by arrangement rather than
    by construction."""
    with pytest.raises(ConfigError) as caught:
        getattr(cli, act).render(body)

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


@pytest.mark.parametrize(
    ("section", "body"),
    [
        pytest.param(
            "mcp_servers", {"house": {"transport": [{"credential": ANSWERED}]}}, id="a-suffix"
        ),
        pytest.param(
            "agents", {"sam": {"mcp": [{"server": "house", "token": ANSWERED}]}}, id="an-agent"
        ),
        pytest.param(
            "agent_defaults", {"mcp": [{"server": "house", "token": ANSWERED}]}, id="a-default"
        ),
        pytest.param(
            "agents", {"sam": {"mcp": [[{"token": ANSWERED}]]}}, id="a-list-inside-a-list"
        ),
        # A mapping under a list under a list, which is the depth that
        # says the rule is not one level of special-casing.
        pytest.param(
            "agents",
            {"sam": {"mcp": [{"grants": [{"token": ANSWERED}]}]}},
            id="a-mapping-three-deep",
        ),
    ],
)
def test_a_mapping_inside_a_list_is_not_opened_either(
    section: str,
    body: object,
    run,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The depth the rule has to hold at, and the one it used to stop at.

    A list was opened item by item with `str()`, so a mapping one level
    down arrived on the line as its repr: `[{'credential': '...'}]`,
    keys, quotes, nested value and all. A mapping is named wherever it
    is, so what a list opens is words and the fact that its items are
    structures, and nothing below that reaches the terminal.
    """
    printed = rendered(run, monkeypatch, capsys, **{section: body})

    assert ANSWERED not in printed
    assert "credential" not in printed
    assert "token" not in printed
    # Named rather than dropped: the line says a structure is there.
    assert "{...}" in printed or "[...]" in printed


def shadowing(reference: object, **location: object) -> dict[str, object]:
    """A document whose one MCP server carries a reference-bearing
    header, and one stored credential filed against it."""
    return document(
        mcp_servers={"house": {"transport": "stdio", "headers": {"Authorization": reference}}},
        secrets=[
            {
                "kind": "mcp_server",
                "identity": "house",
                "slot": "headers.Authorization",
                "shadows": "headers.Authorization",
            }
            | location
        ],
    )


def test_a_stored_location_cannot_steer_the_terminal_from_a_comment(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`show` writes every stored credential's location under the
    document as a comment, and a comment is a line on a terminal like
    any other. The four fields are strings by the shape the API declares
    them with, which says nothing about what is in one.
    """
    answering(
        monkeypatch,
        document(
            secrets=[
                {
                    "kind": STEERING,
                    "identity": STEERING,
                    "slot": STEERING,
                    "shadows": None,
                }
            ]
        ),
    )
    capsys.readouterr()

    assert run("show") == 0

    printed = capsys.readouterr().out
    assert "\x1b" not in printed
    assert "#   ?[31mred ?[31mred ?[31mred: " in printed


def test_the_reference_a_stored_secret_displaces_is_rendered_not_interpolated(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The shadow note carries two values off the answer: the key the
    location names, and what the entity writes under it. The second is a
    body's value, so it is rendered by the rule a body's values are
    rendered by rather than interpolated."""
    answering(
        monkeypatch,
        document(
            mcp_servers={"house": {"transport": "stdio", STEERING: STEERING}},
            secrets=[
                {
                    "kind": "mcp_server",
                    "identity": "house",
                    "slot": "headers.Authorization",
                    "shadows": STEERING,
                }
            ],
        ),
    )
    capsys.readouterr()

    assert run("show") == 0

    note = capsys.readouterr().out.splitlines()[-1]
    assert note.endswith("(used instead of ?[31mred: ?[31mred)")
    assert "\x1b" not in note


def test_a_shadowed_reference_that_is_a_structure_is_not_opened(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A mapping where a reference belongs is named on the note, not
    opened onto it. The document above the notes prints it as the YAML
    it is, which is that rendering's whole job; the note is a line about
    the document and holds one value's worth of room."""
    answering(monkeypatch, shadowing({"leak": ANSWERED}))
    capsys.readouterr()

    assert run("show") == 0

    note = capsys.readouterr().out.splitlines()[-1]
    assert note.endswith("(used instead of headers.Authorization: {...})")
    assert ANSWERED not in note


def test_a_grant_written_as_an_object_reads_as_one(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The well-formed document the rule above changes, pinned so the
    change is a decision rather than a side effect.

    An `mcp` entry may be a bare server name or an object naming the
    tools it grants, and the tree used to print the object form as a
    Python repr. It now reads as the structure it is, beside the bare
    names that are printed whole.
    """
    printed = rendered(
        run,
        monkeypatch,
        capsys,
        agents={"sam": {"mcp": ["lights", {"server": "house", "tools": ["turn_on"]}]}},
    )

    assert "  sam: mcp=[lights, {...}]\n" in printed
