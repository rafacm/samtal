"""The committed example fragments, fed through the CLI they document.

A fragment nobody can install is worse than no fragment: it reads like
working documentation. So every file under `examples/` is run through
the command its own header names, in the creation order the reference
checks require, against a real sub-application over a scratch database.
The header comment is the input, which also pins that the command a
reader would copy is the command that works.

The deployment profile is checked the same way and for the same reason,
but it is a script that documents itself as running against a running
server, so it runs in the integration lane where there is one:
`tests/integration/test_config_examples.py`.

`config.example.yaml` is the other committed example, and the same
argument applies to it from the other side: a field it does not mention
is a field an operator never learns exists, since this file is where
the server half is documented for a reader rather than for a generator.
So the last test here walks `ServerConfig` and insists the example
mentions every field of it.
"""

import contextlib
import io
import re
import sys
import typing
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import BaseModel

from tests.support.apps import mounted
from vinga_server.config import cli
from vinga_server.config.api import build_api
from vinga_server.config.loader import load_file_config
from vinga_server.config.models import ServerConfig
from vinga_server.config.secrets import MASTER_KEY_ENV, generate_key

SERVER = Path(__file__).resolve().parents[2]
EXAMPLES = SERVER / "examples"
EXAMPLE_CONFIG = SERVER / "config.example.yaml"

API_SECRET_ENV = "VINGA_API_SECRET"

TOKEN = "test-api-token-" + "0123456789abcdef" * 2

# The command a fragment's header names, as in
#   vinga provider set llm claude -f examples/llm-anthropic.yaml
COMMAND = re.compile(rf"^#\s+{cli.PROGRAM} (\S+ set\b.*?) -f ")

# Providers, MCP servers and prompt fragments have to exist before
# anything references them: a write leaving a reference unresolved is
# refused, by design.
ORDER = ("provider", "mcp-server", "prompt-fragment", "agent-defaults", "agent")

# One key of the example configuration, as written or as commented out.
# The example's own convention is that a field whose default is right
# for a plain LAN deployment appears as a commented `# key:` line with
# the reasoning above it, so a commented line is coverage and a
# sentence of prose that happens to use the word is not.
#
# Lower case is what tells the two apart, and it is a rule about this
# repository rather than about YAML: every key any model here declares
# is snake_case, and a sentence of prose starts with a capital. The
# fragments open a paragraph with `Default: <value>` and `API: <what it
# is>` often enough that the wider pattern read those as documented
# keys, and the case below, which uncomments a whole fragment and
# installs it, then submitted `Default` as an option and was told it is
# not one. Narrowing here rather than rewording the prose: the rule the
# comment above states is the one intended, and documentation is written
# for the reader rather than around the scan.
KEY_LINE = re.compile(r"^(?P<indent> *)(?P<key>[a-z_][a-z0-9_]*):(?: |$)")
COMMENT_LINE = re.compile(r"^(?P<indent> *)# ?(?P<rest>.*)$")


def _fragments() -> list[Path]:
    return sorted(EXAMPLES.glob("*.yaml"))


def _command(fragment: Path) -> list[str]:
    """The first noun's `set` line in the file's header comment. A file
    may name more than one (the same voice provider installed twice
    under two names); the first is the one this runs."""
    for line in fragment.read_text(encoding="utf-8").splitlines():
        found = COMMAND.match(line)
        if found:
            return found.group(1).split()
    raise AssertionError(f"{fragment.name} names no `vinga-server config <noun> set` command")


@pytest.fixture
def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The CLI as a deployment runs it, against a server of this test's
    own: the same injected client seam the acceptance suite uses, so a
    fragment is installed through CLI parsing, HTTP and the repository
    rather than through a shortcut none of them takes."""
    monkeypatch.delenv("VINGA_CONFIG", raising=False)
    monkeypatch.delenv(cli.API_URL_ENV, raising=False)
    monkeypatch.setenv(MASTER_KEY_ENV, generate_key())
    monkeypatch.setenv(API_SECRET_ENV, TOKEN)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))

    # What holds each command's application open. Since #142 the API's
    # engine is its lifespan's, and `TestClient` enters a lifespan only
    # as a context manager, so the client the entry point is handed is
    # entered here and released when the command that asked for it ends.
    lifespans = contextlib.ExitStack()

    def factory(base_url: str, token: str) -> TestClient:
        database = load_file_config(None).server.database
        # Mounted where the server mounts it, since that prefix is part
        # of the address the CLI resolves on its own.
        served = mounted(build_api(token, database))
        return lifespans.enter_context(
            TestClient(
                served, base_url=base_url, headers={"Authorization": f"Bearer {token}"}
            )
        )

    monkeypatch.setattr(cli, "build_client", factory)

    def _run(*argv: str) -> int:
        nonlocal lifespans
        with contextlib.ExitStack() as this_command:
            lifespans = this_command
            return cli.main(list(argv))

    return _run


def test_there_are_fragments_to_check() -> None:
    """A glob that matched nothing would make every assertion below
    vacuous, which is the failure mode a directory rename produces."""
    assert len(_fragments()) >= 10


def test_every_fragment_installs_through_the_command_it_names(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    ordered = sorted(_fragments(), key=lambda path: ORDER.index(_command(path)[0]))

    for fragment in ordered:
        argv = [*_command(fragment), "-f", str(fragment)]
        assert run(*argv) == 0, f"{fragment.name}: {capsys.readouterr().err}"

    capsys.readouterr()
    assert run("list") == 0
    listed = capsys.readouterr().out
    assert "claude (anthropic)" in listed
    assert "home (stdio)" in listed
    assert "household (" in listed
    assert "assistant" in listed


# The typed types' fragments, and the promise a comment marker makes
#
# A fragment documents its type's options by writing the interesting
# ones out and leaving the rest as commented `# key:` lines with the
# reasoning above them, so every commented key is a key the file is
# telling a reader they may write. Once a type declares an options model
# that is a claim the model can contradict: a documented key the model
# does not declare, or a documented value it refuses, is a fragment that
# reads like working documentation and is not. So each typed type's
# fragment is uncommented whole and installed (#88).


def _fragment_type(fragment: Path) -> str | None:
    """The `type:` a fragment declares, as it is written live."""
    for line in fragment.read_text(encoding="utf-8").splitlines():
        found = KEY_LINE.match(line)
        if found and found.group("key") == "type":
            return line.split(":", 1)[1].strip()
    return None


def _typed_fragments() -> list[Path]:
    from vinga_server.config.provider_options import declared_options

    declared = {type_name for _, type_name, _ in declared_options()}
    return [path for path in _fragments() if _fragment_type(path) in declared]


def _uncommented(text: str) -> str:
    """Every commented-out key written live, and every line of prose left
    alone. The two are told apart the same way the coverage scan below
    tells them apart: a comment whose content is a `key:` line is a
    documented key, and a comment that is a sentence is a sentence."""
    lines = []
    for raw in text.splitlines():
        hidden = COMMENT_LINE.match(raw)
        shown = hidden.group("indent") + hidden.group("rest") if hidden else raw
        lines.append(shown if hidden and KEY_LINE.match(shown) else raw)
    return "\n".join(lines) + "\n"


@pytest.mark.parametrize(
    "fragment", _typed_fragments(), ids=[path.name for path in _typed_fragments()]
)
def test_every_documented_option_of_a_typed_type_installs(
    run, tmp_path: Path, capsys: pytest.CaptureFixture[str], fragment: Path
) -> None:
    written = tmp_path / fragment.name
    written.write_text(_uncommented(fragment.read_text(encoding="utf-8")), encoding="utf-8")

    argv = [*_command(fragment), "-f", str(written)]

    assert run(*argv) == 0, f"{fragment.name}, uncommented: {capsys.readouterr().err}"


def test_the_uncommenting_is_doing_something() -> None:
    """A transform that changed nothing would make the case above a
    second copy of the one before it."""
    for fragment in _typed_fragments():
        text = fragment.read_text(encoding="utf-8")
        assert _uncommented(text) != text, f"{fragment.name} documents no commented key"


def test_an_open_doors_fragment_documents_only_real_options() -> None:
    """The case above cannot hold the one type that accepts anything.

    Uncommenting is a scan, and a scan cannot tell a documented key from
    a sentence that happens to contain one: a line of prose reading
    `there for: every server ...` is a `key:` line to a regular
    expression. For a type whose model shuts its door that mistake is
    caught by the install, loudly, which is what the `Default:` and
    `API:` paragraphs turned up while the second type was converted. For
    the escape hatch it is not caught at all, because the model keeps
    what it does not declare: the stray key would install, and it would
    be sent to the endpoint.

    So the keys that survive the uncommenting are checked against what
    the type actually declares. The one passthrough the file documents
    is named here rather than counted, so documenting a second one is a
    line of this test and a sentence of that file moving together.
    """
    from vinga_server.config.models import ProviderConfig
    from vinga_server.config.provider_options import OpenaiCompatibleOptions

    fragment = EXAMPLES / "llm-openai-compatible.yaml"
    written = yaml.safe_load(_uncommented(fragment.read_text(encoding="utf-8")))
    declared = set(ProviderConfig.model_fields) | set(OpenaiCompatibleOptions.model_fields)

    assert set(written) - declared == {"top_p"}


def test_the_open_doors_fragment_names_the_keys_it_may_not_take() -> None:
    """The prose that drifted, held to the constant it is prose about.

    This file is where an operator reads what the escape hatch takes,
    and the one thing it has to get right is the exception: the names
    vinga composes for every request, which a fragment may not pass
    through. That sentence is hand-written, it named a key this file no
    longer documents once already, and nothing read it.

    Derived from the model rather than copied here, so the sentence and
    the refusal cannot come apart: a name added to the request is a name
    this fails on until the fragment says so.
    """
    from vinga_server.config.provider_options import OpenaiCompatibleOptions

    text = (EXAMPLES / "llm-openai-compatible.yaml").read_text(encoding="utf-8")
    refused = OpenaiCompatibleOptions.refused_passthrough()
    assert refused, "no name is refused, so this check is vacuous"

    missing = [name for name in refused if name not in text]
    assert not missing, f"llm-openai-compatible.yaml does not name: {', '.join(missing)}"

    # And the count in the sentence is the count in the model, which is
    # the half the mistake this replaces got wrong: it said the request
    # composes seven names and listed two of them as written above,
    # when one of those had been taken out of the file and the other is
    # an option a fragment must write rather than an exclusion.
    assert f"{len(refused)} names" in text.replace("Five", "5")


def test_every_fragment_is_listed_in_the_examples_readme() -> None:
    """The README's table is how a reader finds these, so a new file
    that is not in it is invisible."""
    readme = (EXAMPLES / "README.md").read_text(encoding="utf-8")
    missing = [path.name for path in _fragments() if f"`{path.name}`" not in readme]
    assert not missing, f"not listed in examples/README.md: {', '.join(missing)}"


def test_the_readme_names_every_type_that_declares_its_options() -> None:
    """The other sentence about the models, and the one that lagged.

    This README tells a reader which types have their options declared
    and which are documented by the fragment they are reading. Both
    halves of that go wrong silently: a type left out reads as
    pass-through when it is checked and refused by name, and a type
    named that declares nothing sends a reader to a schema command that
    refuses. It is prose rather than a generated region, so what keeps
    it honest is this, which is the same pair of checks
    `test_config_entities.py` holds the reference's own note to.

    The spelling asserted is the stage and the type together, which is
    what addresses a model, so the sentence a reader meets is the
    sentence this reads (#88).
    """
    from vinga_server.config.provider_options import PROVIDER_TYPES, declared_options

    readme = (EXAMPLES / "README.md").read_text(encoding="utf-8")
    declared = {(stage, type_name) for stage, type_name, _ in declared_options()}
    assert declared, "no type declares an options model, so this check is vacuous"

    for stage, type_name in sorted(declared):
        assert f"`{stage} {type_name}`" in readme, f"{stage} {type_name} is not in the README"

    named = sorted(
        f"{stage} {type_name}"
        for stage, types in PROVIDER_TYPES.items()
        for type_name in types
        if (stage, type_name) not in declared and f"`{stage} {type_name}`" in readme
    )
    assert not named, f"the README names types that declare no model: {', '.join(named)}"


def _sections(annotation: object) -> list[type[BaseModel]]:
    """The nested settings models an annotation names, read out of the
    annotation itself: an optional section is a union, so `capture` and
    `conversations` are found the same way `auth` is, and a section
    added later is found without anyone remembering to list it."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation]
    return [section for argument in typing.get_args(annotation) for section in _sections(argument)]


def _leaves(model: type[BaseModel], prefix: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
    """Every leaf field path of a settings model, as a key path."""
    found: list[tuple[str, ...]] = []
    for name, field in model.model_fields.items():
        sections = _sections(field.annotation)
        if sections:
            for section in sections:
                found += _leaves(section, (*prefix, name))
        else:
            found.append((*prefix, name))
    return found


def _mentioned(text: str) -> set[tuple[str, ...]]:
    """Every key path the file writes, live or commented out, each at
    the depth it is written at. A comment marker and the one space
    after it are taken off before the line is read, which is how the
    file indents a commented-out section's fields (`# database:` with
    `#   dir:` under it), so nesting is checked on both kinds of line:
    a key at the wrong depth documents a field that does not exist
    there."""
    mentioned: set[tuple[str, ...]] = set()
    stack: list[tuple[int, str]] = []
    for raw in text.splitlines():
        hidden = COMMENT_LINE.match(raw)
        line = hidden.group("indent") + hidden.group("rest") if hidden else raw
        found = KEY_LINE.match(line)
        if not found:
            continue
        indent = len(found.group("indent"))
        while stack and stack[-1][0] >= indent:
            stack.pop()
        stack.append((indent, found.group("key")))
        mentioned.add(tuple(key for _, key in stack))
    return mentioned


def test_the_example_configuration_mentions_every_server_field() -> None:
    """The other half of the same argument: a field of `ServerConfig`
    that `config.example.yaml` does not mention is a field an operator
    reading the example never learns exists. Mentioning it is enough,
    since the file's convention is that a default worth keeping is
    shown as a commented `# key:` line with its reasoning; what does
    not count is prose using the word, which is why the scan wants the
    key form. `server:` is the whole of what the file holds, and this
    pin is about that tree."""
    mentioned = _mentioned(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    leaves = _leaves(ServerConfig)

    assert len(leaves) > 20, "the field walk found almost nothing, so the check below is vacuous"
    missing = [".".join(leaf) for leaf in leaves if ("server", *leaf) not in mentioned]
    assert not missing, (
        "not in config.example.yaml, neither written nor commented out: "
        f"{', '.join(sorted(missing))}"
    )
