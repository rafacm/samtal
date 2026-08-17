"""The generated schema reference, and the committed copy of it.

Three things are worth pinning, all of them the same discipline the
domain configuration's docgen suite keeps. That every column really is
described, since an undescribed column is a hole in the only document
the store has. That the rendering is deterministic and the committed
copy matches it, which is what CI diffs byte for byte. And that the
document says the things a column renderer cannot derive: the
compatibility promise, the retention default, what deletion means at
the level of the file, and where the event vocabulary is defined.
"""

from pathlib import Path

from samtal_server.conversations import docgen
from samtal_server.conversations.schema import TABLES

COMMITTED = (
    Path(__file__).resolve().parents[3] / "docs" / "reference" / "conversations-schema.md"
)

REGENERATE = (
    "docs/reference/conversations-schema.md is stale; regenerate it with "
    "`uv run samtal-server conversations schema > "
    "../docs/reference/conversations-schema.md`"
)


def flat(text: str) -> str:
    """The document with its line breaks flattened. The prose is wrapped
    at a fixed width, so a sentence this suite asserts on lands across
    two lines as soon as a word ahead of it changes, and an assertion
    that broke on rewrapping would be an assertion about the wrapping."""
    return " ".join(text.split())


def test_every_column_carries_a_comment() -> None:
    """The comment is the description in the only rendering there is, so
    a column without one is invisible rather than merely undocumented."""
    missing = [
        f"{table.name}.{column.name}"
        for table in TABLES
        for column in table.columns
        if not column.comment
    ]
    assert not missing, f"columns with no comment: {', '.join(missing)}"


def test_the_reference_is_deterministic() -> None:
    assert docgen.reference() == docgen.reference()


def test_the_committed_reference_matches_the_schema() -> None:
    """The same check CI runs, run here too: locally it fails in the
    suite rather than after a push."""
    assert COMMITTED.read_text(encoding="utf-8") == docgen.reference(), REGENERATE


def test_the_reference_names_every_column_of_every_table() -> None:
    rendered = docgen.reference()
    for table in TABLES:
        assert f"### `{table.name}`" in rendered
        for column in table.columns:
            assert f"| `{column.name}` |" in rendered, f"{table.name}.{column.name} is missing"


def test_the_reference_states_the_compatibility_promise() -> None:
    rendered = docgen.reference()
    assert "compatibility surface" in flat(rendered)
    assert "breaking changes" in flat(rendered)


def test_the_reference_states_the_retention_default_and_its_opt_out() -> None:
    rendered = docgen.reference()
    assert "retention_days" in rendered
    assert "90" in rendered
    assert "retention_days: 0" in flat(rendered)


def test_the_reference_says_what_deletion_means_in_the_file() -> None:
    """A right to delete honored in the query planner and broken in the
    file bytes is not honored, so the document says which one this is,
    and names the two limits rather than implying they do not exist."""
    rendered = docgen.reference()
    assert "secure_delete=ON" in rendered
    assert "wal_checkpoint(TRUNCATE)" in rendered
    assert "A checkpoint a reader is blocking does not fail the deletion" in flat(rendered)
    assert "retried at the next quiet moment" in flat(rendered)
    assert "backups, filesystem snapshots" in flat(rendered)
    assert "Capture files are a" in flat(rendered)


def test_the_reference_gives_the_wal_safe_copy_recipe() -> None:
    """A plain `cp` of a database in WAL mode without its sidecar is a
    database missing its most recent commits, which is exactly the
    commits somebody copying it wants."""
    rendered = docgen.reference()
    assert ".backup" in rendered
    assert "WAL-safe copy" in flat(rendered)


def test_the_reference_points_at_the_event_vocabularys_authority() -> None:
    """Thirty rows restated here would drift. The reference links the
    definition instead, and says what the store does to the payload on
    the way in."""
    rendered = docgen.reference()
    assert docgen.EVENT_REFERENCE in rendered
    assert "restated here" in flat(rendered)
    # Both content keys, and that neither depends on a switch.
    assert "strips `text`" in flat(rendered)
    assert "`tool` from `tool_call`" in flat(rendered)
    assert "whatever the storage switches say" in flat(rendered)


def test_the_reference_maps_the_gen_ai_vocabulary() -> None:
    """An exporter maps by reading one table rather than by guessing,
    which is what adopting the convention "where one exists" costs."""
    rendered = docgen.reference()
    for name, attribute, _ in docgen.GEN_AI:
        assert f"| `{name}` | `{attribute}` |" in rendered
    assert "gen_ai.usage.input_tokens" in rendered
    assert "gen_ai.request.model" in rendered
    assert "gen_ai.provider.name" in rendered
    assert "server.address" in rendered


def test_the_reference_explains_both_storage_switches() -> None:
    rendered = docgen.reference()
    assert "`metrics`" in rendered
    assert "`text`" in rendered
    assert "sessions.metrics" in rendered
    assert "voiceprint" in rendered
