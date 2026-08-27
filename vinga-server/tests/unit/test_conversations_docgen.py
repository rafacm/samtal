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

from vinga_server.conversations import docgen
from vinga_server.conversations.schema import TABLES

COMMITTED = (
    Path(__file__).resolve().parents[3] / "docs" / "reference" / "conversations-schema.md"
)

REGENERATE = (
    "docs/reference/conversations-schema.md is stale; regenerate it with "
    "`uv run vinga-server conversations schema > "
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


def test_the_reference_says_what_deletion_really_promises() -> None:
    """The backend decides what deletion means, so the document says
    which one this is and names its limits rather than implying they do
    not exist.

    Three of them, and each was a promise the SQLite-era wording made
    that a server-side store cannot keep: the row disappears for
    transactions that begin after the commit, not for one already in
    flight; reclaiming the space is the instance's maintenance rather
    than a per-delete overwrite; and copies that have already left are
    still the operator's."""
    flattened = flat(docgen.reference())

    assert "invisible to every transaction that begins after the deletion" in flattened
    assert "keeps seeing the row until that transaction ends" in flattened
    assert "autovacuum" in flattened
    assert "the operator's to manage" in flattened
    assert "Capture files are a" in flattened
    # And the file-era promises are gone rather than softened.
    assert "secure_delete" not in flattened
    assert "wal_checkpoint" not in flattened


def test_the_document_says_once_that_targeted_erasure_is_absent() -> None:
    """Two sections talk about deletion, and they drifted apart: the
    privacy section called the session the deletion unit enforceable
    today while the section below it said no command erases one.

    An operator reading either alone has to reach the same answer, so
    both are asserted here, and the claim the privacy section is not
    allowed to make back is asserted absent. What the two units still
    are stays sayable: the conversation is what retention takes whole
    and both are what the API will address, which is a different
    sentence from either being erasable now."""
    flattened = flat(docgen.reference())

    assert "Erasing either on demand is not enforceable in this release" in flattened
    assert "Deletion on demand is not something this release has a command for" in flattened
    assert "the units deletion is expressed in are the conversation and the session" in (
        flattened
    )
    # The wording the two sections contradicted each other through.
    assert "enforceable today" not in flattened
    # And the command is named only as something that is gone.
    assert flattened.count("conversations purge") == 1
    assert "`vinga-server conversations purge` was one and is gone" in flattened


def test_the_manual_erasure_names_every_table_a_session_has_rows_in() -> None:
    """The one destructive instruction this document gives, and the way
    it goes wrong is silent.

    A session has rows in four tables, so an operator who deletes from
    `sessions` alone has left the turns, the calls and the events they
    meant to erase behind, pointing at nothing, with nothing to tell
    them. The instruction is only correct while it names all four, which
    is why each is asserted by name rather than the paragraph being read
    for its sense.

    Its SQLite-era form named three files instead, for exactly the same
    reason: a committed row's bytes lived in the write-ahead log until a
    checkpoint folded them back.
    """
    flattened = flat(docgen.reference())

    for name in ("sessions", "turns", "tool_invocations", "events"):
        assert name in flattened, f"the manual erasure does not name {name}"
    # And in one transaction, so a half-done deletion is not a state an
    # operator can leave behind.
    assert "in one transaction" in flattened


def test_the_reference_gives_the_read_only_access_recipe() -> None:
    """There is nothing to copy any more, and that is the point: the way
    to read the record beside a running server is a live session as the
    role `deploy/postgres-init.sql` provisions, which reads what was
    said and cannot reach the stored secrets next door.

    Its SQLite-era form gave a WAL-safe copy recipe, because a plain
    `cp` of a database in WAL mode without its sidecar is a database
    missing exactly the commits somebody copying it wants.
    """
    rendered = docgen.reference()
    flattened = flat(rendered)

    assert "psql" in rendered
    assert "vinga_ro" in rendered
    assert "deploy/postgres-init.sql" in flattened
    # What the role may not do, which is why it exists rather than the
    # server role being used for a read.
    assert "no access at all to the domain schema" in flattened
    # And why it carries timeouts, which is a fact about migrations
    # rather than about tidiness.
    assert "statement_timeout" in rendered
    assert "wait out its lock timeout" in flattened


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
