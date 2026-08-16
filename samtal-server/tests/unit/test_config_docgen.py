"""The generated documentation, and the commands that print it.

Three things are worth pinning here. That every domain field really is
described, since an undescribed field is invisible in all three
renderings at once; that the reference is deterministic and the
committed copy matches it, which is what CI diffs byte for byte; and
that the example files each entity points a reader at are the files
that are actually there, in both directions.
"""

import json
from pathlib import Path

import pytest

from samtal_server.config import cli, docgen
from samtal_server.config.secrets import MASTER_KEY_ENV

COMMITTED = Path(__file__).resolve().parents[3] / "docs" / "reference" / "domain-config.md"
EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


@pytest.fixture
def run(monkeypatch: pytest.MonkeyPatch):
    """These commands read the models and nothing else, so the fixture
    takes away everything else: no config file, no writable database
    directory, no encryption key."""
    monkeypatch.delenv("SAMTAL_CONFIG", raising=False)
    monkeypatch.delenv(MASTER_KEY_ENV, raising=False)
    monkeypatch.setenv("SAMTAL_SERVER__DATABASE__DIR", "/nowhere/at/all")

    def _run(*argv: str) -> int:
        return cli.main(list(argv))

    return _run


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


def test_the_reference_says_where_provider_options_are_documented() -> None:
    """The one part schema generation cannot describe, which the
    reference has to admit rather than leave a reader hunting for."""
    rendered = docgen.reference()
    assert "#88" in rendered
    assert "examples/llm-anthropic.yaml" in rendered


def test_the_committed_reference_matches_the_models() -> None:
    """The same check CI runs, run here too: locally it fails in the
    suite rather than after a push."""
    assert COMMITTED.read_text(encoding="utf-8") == docgen.reference(), (
        "docs/reference/domain-config.md is stale; regenerate it with "
        "`uv run samtal-server config reference > ../docs/reference/domain-config.md`"
    )


def test_the_fragment_help_comes_from_the_field_descriptions() -> None:
    """The issue asks for help text derived from the descriptions rather
    than written twice, so the check is that the text is the model's."""
    from samtal_server.config.models import McpServerConfig

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
