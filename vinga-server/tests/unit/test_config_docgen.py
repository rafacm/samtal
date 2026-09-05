"""The generated documentation, and the commands that print it.

Four things are worth pinning here. That every domain field really is
described, since an undescribed field is invisible in all three
renderings at once; that the reference is deterministic and the
committed copy matches it, which is what CI diffs byte for byte; that
the example files each entity points a reader at are the files that are
actually there, in both directions; and that the CLI reference's
generated region is what the grammar and those same example files say,
the one committed document here that is only half generated.
"""

import json
import logging
import shutil
from pathlib import Path

import pytest

from tests.support.config_cli import SECRET, chain, registered
from tests.support.events import both_formats
from tests.support.isolation import ALLOWED_IMPORTS, imported_alone
from vinga_server.config import cli, docgen
from vinga_server.config.secrets import MASTER_KEY_ENV

COMMITTED = Path(__file__).resolve().parents[3] / "docs" / "reference" / "domain-config.md"
COMMITTED_CLI = Path(__file__).resolve().parents[3] / "docs" / "reference" / "cli.md"
EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
PRESETS = EXAMPLES / docgen.PRESET_DIR


@pytest.fixture
def run(monkeypatch: pytest.MonkeyPatch):
    """These commands read the models and nothing else, so the fixture
    takes away everything else: no config file, no writable database
    directory, no encryption key."""
    monkeypatch.delenv("VINGA_CONFIG", raising=False)
    monkeypatch.delenv(MASTER_KEY_ENV, raising=False)
    # A port nothing listens on, so a command that opened the database
    # would refuse here rather than print.
    monkeypatch.setenv("VINGA_DB_PORT", "1")

    def _run(*argv: str) -> int:
        return cli.main(list(argv))

    return _run


# The two documents this module renders from the models alone, rendered
# in a child interpreter that has imported nothing else, which is what
# says the claim in the module docstring is about the import graph and
# not about intent. The runner and the allow list are in
# `tests/support/isolation.py`, since the server-half reference's suite
# makes the same claim about the same import graph.
_ALONE = "\n".join(
    (
        "import vinga_server.config.docgen as docgen",
        "",
        "rendered = len(docgen.reference()) + len(docgen.schema())",
    )
)


def test_the_reference_and_the_schema_render_from_the_models_alone() -> None:
    """Importing `docgen` reaches the models and the registry and stops
    there, and both documents it renders from them come out.

    The repository is the module that must stay out. It is where the
    whole-domain model used to be declared, which made rendering a
    document import SQLAlchemy and cryptography to reach one class
    (#242 moved the declaration to `models.py`). A prose claim about an
    import graph is one a later import silently retracts, so this drives
    a child interpreter that has imported nothing else, the shape
    `test_config_entities.py` holds the registry to.

    `openapi()` is deliberately not called here: it imports the
    application, says so where it is defined, and is the exception this
    is the rule for.
    """
    alone = imported_alone(_ALONE)

    assert frozenset(alone["loaded"]) == ALLOWED_IMPORTS
    assert alone["heavy"] == []
    assert alone["rendered"] > 0


def _properties(schema: dict) -> list[tuple[str, str, dict]]:
    """Every described-or-not property in a schema, as (model, field,
    body), including the nested models in $defs."""
    models = {"": schema, **schema.get("$defs", {})}
    return [
        (model or schema.get("title", ""), name, body)
        for model, definition in models.items()
        for name, body in definition.get("properties", {}).items()
    ]


def test_every_domain_field_carries_a_description() -> None:
    schema = json.loads(docgen.schema())
    missing = [
        f"{model}.{name}"
        for model, name, body in _properties(schema)
        if not body.get("description")
    ]
    assert not missing, f"undescribed domain fields: {', '.join(missing)}"


def test_each_entity_schema_parses_and_describes_its_fields() -> None:
    for name in docgen.entity_names():
        schema = json.loads(docgen.schema(name))
        assert schema["properties"], f"{name}: no properties in its schema"
        assert all(body.get("description") for _, _, body in _properties(schema))


def test_an_unknown_entity_names_the_ones_that_exist(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("schema", "ghost") == 1

    captured = capsys.readouterr()
    assert "provider" in captured.err
    assert "Traceback" not in captured.err


def test_schema_and_reference_need_no_database_and_no_key(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The directory the fixture names cannot be created and no key is
    set, so a command that opened the database or loaded the keys would
    fail here rather than print."""
    assert run("schema") == 0
    assert json.loads(capsys.readouterr().out)["properties"]["providers"]

    assert run("reference") == 0
    assert capsys.readouterr().out.startswith("# Domain configuration reference")


def test_the_reference_is_deterministic() -> None:
    assert docgen.reference() == docgen.reference()


def test_the_reference_names_every_field_of_every_entity() -> None:
    rendered = docgen.reference()
    for entity in docgen.ENTITIES:
        for name in entity.model.model_fields:
            assert f"| `{name}` |" in rendered, f"{entity.name}.{name} is not in the reference"


def test_the_reference_documents_each_typed_types_options() -> None:
    """The provider's second tier, in the rendered page.

    A provider is the one kind whose remaining keys belong to whatever
    its `type` names, so a table of `ProviderConfig` alone documents
    three fields and an ellipsis. What is asserted here is that each
    declared type has a section of its own, that the section is
    addressed by stage and type together (the pair that selects it), and
    that a nested section's leaves are in the page rather than only the
    name of the section.
    """
    from vinga_server.config.provider_options import declared_options

    rendered = docgen.reference()
    declared = declared_options()
    assert declared, "no type declares options, so this check is vacuous"

    for stage, type_name, model in declared:
        assert f"#### `{stage}` options for `type: {type_name}`" in rendered
        for field in model.model_fields:
            assert f"| `{field}` |" in rendered, f"{type_name}.{field} is not in the reference"

    # The leaf under a nested section, and the heading that says whose
    # it is. `vad_parameters` alone would tell a reader a mapping goes
    # there and not what may go in it.
    assert "Fields of `vad_parameters`:" in rendered
    assert "| `min_silence_duration_ms` |" in rendered


def test_the_typed_options_are_grouped_by_stage_and_then_by_type() -> None:
    """The order the sections appear in, which is the order a provider
    is addressed in: the pipeline's own stage order, and type names
    under it. Asserted on the rendered page rather than on the
    declaration, since the page is what a reader scans."""
    from vinga_server.config.models import PROVIDER_STAGES
    from vinga_server.config.provider_options import declared_options

    rendered = docgen.reference()
    headings = [
        rendered.index(f"#### `{stage}` options for `type: {type_name}`")
        for stage, type_name, _ in declared_options()
    ]

    assert headings == sorted(headings)
    grouped = [
        (PROVIDER_STAGES.index(stage), type_name)
        for stage, type_name, _ in declared_options()
    ]
    assert grouped == sorted(grouped)


def test_the_set_provider_epilog_lists_each_typed_types_options() -> None:
    """The third rendering of the same contract, and the one a person
    meets without leaving the terminal."""
    from vinga_server.config.provider_options import declared_options

    epilog = docgen.fragment_help("provider")

    for stage, type_name, model in declared_options():
        assert f"options for {stage} type {type_name}:" in epilog
        for field in model.model_fields:
            assert f"  {field}: " in epilog
    # The nested leaf at the path a fragment writes it at.
    assert "  vad_parameters.min_silence_duration_ms: " in epilog
    # And the trailer, narrowed to the types that are still untyped.
    assert "a type that declares none of its own" in epilog


def test_the_reference_says_where_provider_options_are_documented() -> None:
    """The one part schema generation cannot describe, which the
    reference has to admit rather than leave a reader hunting for."""
    rendered = docgen.reference()
    assert "#88" in rendered
    assert "examples/llm-anthropic.yaml" in rendered


# What the prompt half's two rows have to mean
#
# The drift check below holds the committed document to the models, and
# it is exactly as right as the models are: a description that says the
# wrong boundary passes it byte for byte. These two say what the
# document has to mean rather than what it has to equal, which is the
# one thing a diff cannot check, and each is the sentence an operator
# acts on: restarting for a change a request applies is a wasted
# maintenance window, and the two rows sat in two regimes for three
# milestones, which is exactly when a stale sentence goes unnoticed.


def _described(entity: str, field: str) -> str:
    """One field's row in the generated reference, as the reader meets
    it. Read out of the rendered document rather than off the model, so
    what is pinned is what is published."""
    section = docgen.reference().split(f"### {entity}\n")[1].split("\n### ")[0]
    (row,) = [line for line in section.splitlines() if line.startswith(f"| `{field}` |")]
    return row


def test_the_reference_says_an_agents_own_includes_are_applied_by_a_reload() -> None:
    """An agent's own list is what the apply takes from the store, so an
    edit reaches that agent at its next activation."""
    row = _described("Agent", "prompt_includes")

    assert "A reload applies this list" in row
    assert "next activation" in row
    assert "next server start" not in row


def test_the_reference_says_the_defaults_includes_are_applied_too() -> None:
    """And so is the layer under it, which is the change the last
    milestone made: the apply installs the stored `agent_defaults`, so an
    edit there reaches every agent that inherits it at the same moment
    an agent's own edit would. A row still promising a start would send
    an operator to restart for a change a request applies."""
    row = _described("Agent defaults", "prompt_includes")

    assert "A reload applies this list" in row
    assert "next activation" in row
    assert "next server start" not in row


def test_the_committed_reference_matches_the_models() -> None:
    """The same check CI runs, run here too: locally it fails in the
    suite rather than after a push."""
    assert COMMITTED.read_text(encoding="utf-8") == docgen.reference(), (
        "docs/reference/domain-config.md is stale; regenerate it with "
        "`uv run vinga-server config reference > ../docs/reference/domain-config.md`"
    )


def test_the_fragment_help_comes_from_the_field_descriptions() -> None:
    """The issue asks for help text derived from the descriptions rather
    than written twice, so the check is that the text is the model's."""
    from vinga_server.config.models import McpServerConfig

    described = McpServerConfig.model_fields["tool_timeout_s"].description or ""
    first_sentence = described.split(". ")[0]

    helped = docgen.fragment_help("mcp-server")
    assert "tool_timeout_s" in helped
    assert first_sentence.split(",")[0] in " ".join(helped.split())


def test_a_nested_shape_has_no_fragment_command() -> None:
    """A filler section is written inside an agent, so the reference
    documents it without a command that would not exist."""
    assert docgen.entity("filler").command is None


def _claims() -> dict[str, list[str]]:
    """Which entity claims which example file, by filename."""
    claimed: dict[str, list[str]] = {}
    for entity in docgen.ENTITIES:
        for filename in entity.examples:
            claimed.setdefault(filename, []).append(entity.name)
    return claimed


def test_every_example_an_entity_names_is_a_file_that_exists() -> None:
    """The reference links these by name, so a renamed or deleted
    example turns into a link to nothing in the generated document and
    in the fragment help at once.

    `Entity.examples` holds filenames, and this insists on that
    literally before resolving any of them. A name carrying a directory
    part (`./vad-silero.yaml`) opens the same file while reading as a
    different string, which would let one file be claimed by two
    entities without the exactly-one check below noticing."""
    claimed = _claims()
    assert claimed, "no entity names any example, so what follows is vacuous"

    aliased = sorted(name for name in claimed if name != Path(name).name)
    assert not aliased, f"not a bare filename in docgen.ENTITIES: {', '.join(aliased)}"

    missing = sorted(name for name in claimed if not (EXAMPLES / name).is_file())
    assert not missing, f"named in docgen.ENTITIES but not under examples/: {', '.join(missing)}"


def test_every_example_file_is_claimed_by_exactly_one_entity() -> None:
    """The other direction, which is what keeps a new example from
    being added to one side only: a file no entity claims is a file no
    reader is sent to, and a file two entities claim is one of them
    documenting the wrong command for it."""
    claimed = _claims()
    present = sorted(path.name for path in EXAMPLES.glob("*.yaml"))
    assert present, "no example files, so what follows is vacuous"

    unclaimed = [name for name in present if name not in claimed]
    assert not unclaimed, (
        f"under examples/ but named by no entity in docgen.ENTITIES: {', '.join(unclaimed)}"
    )

    doubled = [f"{name} ({', '.join(claimed[name])})" for name in present if len(claimed[name]) > 1]
    assert not doubled, f"named by more than one entity: {', '.join(doubled)}"


# The presets tier
#
# The two tests above are about the fragment tier, where a file is one
# entity's body and exactly one descriptor claims it. A preset is the
# other shape a file under `examples/` can have: a whole apply document,
# several kinds at once, owned by the shape of the document rather than
# by any one kind. So it is checked against that shape instead, and
# against no descriptor at all: an entity that claimed one would be
# telling a reader to install a whole deployment with a `set`.


def test_every_preset_is_a_complete_apply_document() -> None:
    """A preset is what `apply` takes, which is what makes it a tier
    rather than a fragment in a subdirectory: its top-level keys are
    sections of the domain configuration, and it names more than one of
    them, because a document naming one section is a fragment with
    ceremony."""
    import yaml

    from vinga_server.config.models import DOMAIN_KEYS

    presets = sorted(PRESETS.glob("*.yaml"))
    assert presets, "no presets, so what follows is vacuous"

    for path in presets:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(document, dict), f"{path.name} is not a document"
        unknown = sorted(set(document) - set(DOMAIN_KEYS))
        assert not unknown, f"{path.name} names sections apply does not take: {unknown}"
        assert len(document) > 1, f"{path.name} names one section, so it is a fragment"


def test_a_preset_is_claimed_by_the_tier_rather_than_by_an_entity() -> None:
    """The ownership rule for the second tier, in both directions. A
    descriptor's `examples` are filenames under `examples/` itself, and
    a preset is not one of them; and every file the presets directory
    holds is a preset, so nothing can be parked there."""
    claimed = _claims()
    presets = {path.name for path in PRESETS.glob("*.yaml")}

    assert not (claimed.keys() & presets), "an entity claims a preset as its fragment"
    assert presets == {path.name for path in PRESETS.iterdir() if path.is_file()}


# The recipes, and the CLI reference they are published in
#
# A recipe is read out of the example files rather than written beside
# them, so what is worth pinning is the reading: that every command an
# example quotes is published, that every published command is one this
# grammar has, and that the committed page holds what the renderer
# renders.


def test_the_recipes_read_the_examples_this_suite_reads() -> None:
    """The renderer resolves its fragments to the copy the build put
    inside the package, falling back to the directory beside `src/`
    where a checkout keeps them. In a checkout that is this suite's own
    `EXAMPLES`, and it has to be: the ownership tests above are about a
    directory, and they say nothing if the renderer reads another one.

    Which of the two an installed artifact resolves is not something a
    checkout can show, so it is asserted where it is true: CI renders
    this reference from the built wheel, outside the checkout, and fails
    unless the fragments came from inside the installed package.
    """
    assert Path(str(docgen.EXAMPLE_DIR)) == EXAMPLES


def test_every_recipe_line_is_a_command_of_the_grammar() -> None:
    """A recipe that named a command the grammar does not have would be
    a page telling an operator to type something that cannot work. The
    inventory is `cli.COMMANDS`, which is the grammar itself."""
    for recipe in docgen.recipes():
        assert recipe.commands, f"{recipe.title}: a heading with no commands under it"
        for line in recipe.commands:
            words = tuple(line.removeprefix(f"{cli.PROGRAM} ").split())
            assert registered(words) is not None, line


def test_every_command_an_example_quotes_is_published_as_a_recipe() -> None:
    """The other direction, which is what keeps a command block added to
    an example from going unpublished and unrun: every line any example
    quotes is in some recipe, and the recipes name nothing else."""
    quoted = {
        line.strip().removeprefix("# ").strip()
        for path in (*sorted(EXAMPLES.glob("*.yaml")), *sorted(PRESETS.glob("*.yaml")))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith(f"#   {cli.PROGRAM} ")
    }
    published = {line for recipe in docgen.recipes() for line in recipe.commands}

    assert quoted, "no example quotes a command, so what follows is vacuous"
    assert published == quoted


def test_every_preset_import_is_followed_by_its_apply() -> None:
    """The pair, held to arriving whole (#371).

    A preset's recipe is two commands in a fixed order: the import that
    writes the document, and the apply that installs what it wrote. Both
    presets end on the same bare `vinga apply` line, so a renderer that
    deduplicated command lines one at a time would keep the first and
    drop the second, leaving one preset published as a document nothing
    ever installs. That is a hole a byte-for-byte diff of the page
    cannot see, because the page and the renderer would agree.
    """
    installs = f"{cli.PROGRAM} apply"
    imports = f"{cli.PROGRAM} import "
    (presets,) = [
        recipe for recipe in docgen.recipes() if recipe.title == "A whole deployment"
    ]

    written = [line for line in presets.commands if line.startswith(imports)]
    assert len(written) == len(sorted(PRESETS.glob("*.yaml"))), presets.commands
    for position, line in enumerate(presets.commands):
        if line.startswith(imports):
            following = presets.commands[position + 1 : position + 2]
            assert following == (installs,), line


def test_the_cli_reference_is_deterministic() -> None:
    """The committed page is diffed byte for byte, so anything that
    varied between two runs would turn the lane red on an unrelated
    change. Click's help formatter measures the terminal it prints into
    unless it is told not to, which is the one thing here that could."""
    assert cli.cli_reference() == cli.cli_reference()


def test_the_cli_reference_needs_no_database_and_no_key(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The fourth rendering command, held to the property the other
    three have: rendering help and reading commented fragments opens no
    database, reads no configuration file and needs no encryption key."""
    assert run("cli-reference") == 0
    assert capsys.readouterr().out.startswith("Generated by")


# What the recipe renderer says when its own sources will not read
#
# This is the one rendering here that reads files, which puts it on the
# same side of the no-leak boundary as everything that reads a fragment.
# Two families carry the file's contents out with them if they are let
# through: an `OSError` carries the operating system's message, and a
# `UnicodeDecodeError` carries the bytes it choked on and the whole
# buffer on its `object` attribute. A third shape reads a quoted command
# line as input and used to echo the word back.
#
# A file under `examples/` is a file somebody edits, so a credential
# pasted where a variable name belongs can be in its bytes or in a
# command block somebody typed into it. Each case below puts one there
# and asserts it is on none of the four surfaces: stdout, stderr, the
# log in both renderings a deployment keeps it in, and the exception
# chain, which is the one no assertion about a stream can reach.


@pytest.fixture
def planted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A working copy of `examples/`, for a test to break one file of.

    A copy rather than the real directory, because what these cases do
    to a file is what nothing may do to a committed one, and a mirror
    keeps every other file readable so the failure under test is the
    only one there is.
    """
    mirror = tmp_path / "examples"
    shutil.copytree(EXAMPLES, mirror)
    monkeypatch.setattr(docgen, "EXAMPLE_DIR", mirror)

    def _break(name: str, write) -> Path:
        path = mirror / name
        write(path)
        return path

    _break.mirror = mirror
    return _break


def _rendered(run, capsys, caplog) -> tuple[str, str]:
    """The command run the way an operator runs it, with the log
    captured, answering (stdout, stderr)."""
    with caplog.at_level(logging.DEBUG):
        assert run("cli-reference") == 1
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert SECRET not in caplog.text
    assert SECRET not in both_formats(caplog)
    return captured.out, captured.err


def _refused() -> BaseException:
    """The refusal the renderer raises, as the exception rather than as
    the sentence `main` prints.

    A deliberate reach past the entry point, and the only place the
    chain claim can be made: `main` catches this by design and answers
    with a sentence, so no caller-facing surface ever holds it.
    """
    with pytest.raises(cli.ConfigError) as caught:
        docgen.recipes()
    return caught.value


def test_a_quoted_command_with_no_topic_says_nothing_of_the_command(
    planted, run, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    """The shape that reads a quoted line as input.

    Every command an example quotes is published, so a verb no topic
    covers has to be refused rather than dropped. What the refusal names
    is the file, which is a name the registry owns; what it does not
    name is the word, which is whatever somebody typed into a comment
    block and is therefore where a credential would be.
    """
    planted("vad-silero.yaml", lambda path: path.write_text(
        f"#   {cli.PROGRAM} {SECRET}\n\ntype: silero\n", encoding="utf-8"
    ))

    out, err = _rendered(run, capsys, caplog)

    assert "vad-silero.yaml" in err
    assert "no recipe has a heading for" in err
    assert SECRET not in err
    assert SECRET not in out
    assert SECRET not in chain(_refused())


def test_a_file_that_is_not_utf_8_says_nothing_of_its_bytes(
    planted, run, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    """The shape that carries the file's contents.

    `UnicodeDecodeError` quotes the byte it stopped on and keeps the
    whole buffer on its `object` attribute, so a credential anywhere in
    a mis-encoded file is one chain walk away from a caller unless the
    exception is discarded rather than chained.
    """
    planted("vad-silero.yaml", lambda path: path.write_bytes(
        f"# {SECRET} \N{LATIN SMALL LETTER O WITH DIAERESIS}\ntype: silero\n".encode("latin-1")
    ))

    out, err = _rendered(run, capsys, caplog)

    assert "vad-silero.yaml" in err
    assert "not valid UTF-8" in err
    assert SECRET not in err
    assert SECRET not in out
    assert SECRET not in chain(_refused())


def test_a_file_that_cannot_be_read_is_a_sentence_rather_than_a_traceback(
    planted, run, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    """A directory where the registry names a file, which is the
    readable half of `OSError`: the process may not read it, and what
    the caller gets is this module's sentence rather than the operating
    system's."""
    def replace(path: Path) -> None:
        path.unlink()
        path.mkdir()

    planted("vad-silero.yaml", replace)

    _, err = _rendered(run, capsys, caplog)

    assert "vad-silero.yaml" in err
    assert "cannot be read" in err
    refused = _refused()
    assert refused.__cause__ is None
    assert refused.__context__ is None


def test_a_file_the_registry_names_and_nobody_wrote_is_named(
    planted, run, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    """The belt under the ownership test above. That test holds every
    named example to being a file that exists; this one says what
    happens between two runs of it, when a working tree has lost one."""
    planted("vad-silero.yaml", lambda path: path.unlink())

    _, err = _rendered(run, capsys, caplog)

    assert "vad-silero.yaml" in err
    assert "no such file" in err
    refused = _refused()
    assert refused.__cause__ is None
    assert refused.__context__ is None


def _generated(page: str) -> str:
    """The region of the committed page the renderer owns.

    The markers are matched with the blank line the page has to carry
    after the opening one: a paragraph pressed against an HTML comment
    is swallowed into it by every markdown renderer there is, so the
    layout is part of the contract rather than a formatting taste."""
    _, _, tail = page.partition(cli.REFERENCE_BEGIN + "\n\n")
    region, _, _ = tail.partition(cli.REFERENCE_END)
    return region


def test_the_committed_cli_reference_matches_the_grammar() -> None:
    """The same check CI runs, run here too, and the reason the page can
    be half written by hand: only the region between the markers is
    compared, so the prose above it is nobody's to regenerate."""
    page = COMMITTED_CLI.read_text(encoding="utf-8")

    assert page.count(cli.REFERENCE_BEGIN) == 1
    assert page.count(cli.REFERENCE_END) == 1
    assert _generated(page) == cli.cli_reference(), (
        "the generated region of docs/reference/cli.md is stale; regenerate it with "
        "`uv run vinga-server config cli-reference`"
    )


def _recipes(page: str) -> str:
    """The recipes region of the committed page, which is the lines
    between the inner pair of markers."""
    _, _, tail = page.partition(cli.RECIPES_BEGIN + "\n")
    region, _, _ = tail.partition(cli.RECIPES_END)
    return region


def test_the_committed_cli_recipes_match_the_example_fragments() -> None:
    """The recipes' own lane, and the reason it is not the check above
    with a smaller mouth.

    That one regenerates the whole region through the page composer, so
    a composer that dropped, truncated or reordered the recipes would
    move the committed bytes and the fresh render together and pass
    green. This compares the same bytes against the recipe renderer
    itself, which is the only reader that can tell those two apart.
    """
    page = COMMITTED_CLI.read_text(encoding="utf-8")

    assert page.count(cli.RECIPES_BEGIN) == 1
    assert page.count(cli.RECIPES_END) == 1
    # Inside the outer region, so the whole-page rebuild owns these
    # bytes too and neither check is looking at something the other one
    # is not.
    assert cli.RECIPES_BEGIN in _generated(page)
    assert cli.RECIPES_END in _generated(page)
    assert _recipes(page) == cli.cli_recipes(), (
        "the recipes on docs/reference/cli.md are stale; they are read out of the "
        "example fragments, so regenerate the page with "
        "`uv run vinga-server config cli-reference`"
    )
