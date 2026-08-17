"""The two documents the event surface has, and what each is held to.

The generated reference is held to everything: it IS the registry
rendered, and CI regenerates it and diffs it byte for byte, so a field,
a token, a level or a bound that moves without the document moving with
it turns the lane red. The tests here are the completeness half of that,
which a diff cannot give: a generator that silently skipped an event
would produce a document CI is perfectly happy with.

The README index is held to names only, and deliberately so. It carried
field and token claims in prose once, which nothing parsed and nothing
could: prose can go stale while a name-level check stays green, and
half-checked documentation reads as checked. So the schema claims live
in the generated reference now and the index says what exists and when
it fires, which is exactly what these tests check it says: every
declared event in exactly one row, every row a declared event, and no
duplicates. A wording edit passes; a dropped, invented or duplicated row
does not.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from samtal_server import events_docgen
from samtal_server.events import ENFORCEMENT_ENV
from samtal_server.events_schema import (
    GRAMMARS,
    REGISTRY,
    SESSION_CHANNEL,
    SYNTAXES,
    ArgKind,
    Kind,
)

README = Path(__file__).resolve().parents[2] / "README.md"

COMMITTED = Path(__file__).resolve().parents[3] / "docs" / "reference" / "events.md"

REGENERATE = (
    "docs/reference/events.md is stale; regenerate it with "
    "`uv run samtal-server events reference > ../docs/reference/events.md`"
)

# Where the index starts in the README, and what the section it lives in
# is called. Matched on the header row rather than on the heading, since
# the heading covers the formats and the switch as well.
INDEX_HEADER = "| `event` | when |"


def index_rows() -> list[tuple[str, str]]:
    """The index as (event, when) pairs, in the order it lists them.

    The event cell is unwrapped from its backticks; the when cell is
    left as written, since these tests are about names and never about
    wording."""
    lines = README.read_text(encoding="utf-8").splitlines()
    start = lines.index(INDEX_HEADER)
    rows = []
    for line in lines[start + 1 :]:
        if not line.startswith("|"):
            break
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells[0].startswith("---"):
            continue
        rows.append((cells[0].strip("`"), cells[1]))
    return rows


def flat(text: str) -> str:
    """The document with its line breaks flattened. Both documents wrap
    their prose, so a sentence asserted on here lands across two lines
    as soon as a word ahead of it changes, and an assertion that broke
    on rewrapping would be an assertion about the wrapping."""
    return " ".join(text.split())


# --- the README index, at name level ----------------------------------


def test_every_declared_event_has_a_row() -> None:
    listed = [event for event, _ in index_rows()]
    missing = sorted(set(REGISTRY) - set(listed))
    assert not missing, f"declared events with no row: {', '.join(missing)}"


def test_every_row_names_a_declared_event() -> None:
    """The other direction, which containment alone would not give: a
    row for an event that no longer exists is documentation of a surface
    this server does not have."""
    listed = [event for event, _ in index_rows()]
    invented = sorted(set(listed) - set(REGISTRY))
    assert not invented, f"rows naming nothing declared: {', '.join(invented)}"


def test_no_event_is_listed_twice() -> None:
    """`session_rejected` is the one that tempts a second row, since it
    rides two channels. It stays one row whose prose names both."""
    listed = [event for event, _ in index_rows()]
    twice = sorted({event for event in listed if listed.count(event) > 1})
    assert not twice, f"events listed more than once: {', '.join(twice)}"
    assert len(listed) == len(REGISTRY)


def test_the_two_channel_event_is_one_row_naming_both() -> None:
    when = dict(index_rows())["session_rejected"]
    assert "samtal_server.ws" in when
    assert SESSION_CHANNEL in when


def test_the_internal_event_is_listed_as_internal() -> None:
    """It is declared like any other event and emitted by nothing that
    is an emit site, so the index says which it is rather than leaving a
    reader to find out from the reference."""
    when = dict(index_rows())["schema_violation"]
    assert "internal" in when.lower()


def test_the_base_field_claim_is_scoped_to_the_session_channel() -> None:
    """It was not, and was false for every server channel: those records
    carry `event` and name a session or a device only where the record
    is about one."""
    section = flat(README.read_text(encoding="utf-8")).split("## Logging")[1]
    lead = section.split(INDEX_HEADER)[0]
    assert f"on the `{SESSION_CHANNEL}` channel" in lead
    assert "carry `session` and `device`" in lead


def test_the_index_points_at_the_generated_reference() -> None:
    """The index makes no schema claim of its own, so it has to say
    where the schema claims are."""
    section = flat(README.read_text(encoding="utf-8")).split("## Logging")[1]
    assert "(../docs/reference/events.md)" in section


# --- the generated reference, for completeness -------------------------


def test_the_reference_is_deterministic() -> None:
    assert events_docgen.reference() == events_docgen.reference()


def test_the_committed_reference_matches_the_registry() -> None:
    """The same check CI runs, run here too: locally it fails in the
    suite rather than after a push."""
    assert COMMITTED.read_text(encoding="utf-8") == events_docgen.reference(), REGENERATE


def test_the_reference_renders_every_event() -> None:
    rendered = events_docgen.reference()
    for name in REGISTRY:
        assert f"### `{name}`" in rendered, f"{name} has no section"
        assert f"| `{name}`" in rendered, f"{name} has no index row"


def test_the_reference_renders_every_variant_of_every_event() -> None:
    """The drift step guards the content of what is rendered; this
    guards that everything is. A generator that stopped at an event's
    first variant would produce a document CI diffs happily."""
    rendered = events_docgen.reference()
    for name, spec in REGISTRY.items():
        for position, variant in enumerate(spec.variants, start=1):
            heading = f"#### Variant {position}: `{variant.channel}` at "
            assert heading in rendered, f"{name} variant {position} has no section"
        assert rendered.count(f"#### Variant {len(spec.variants)}: ") >= 1


def test_the_reference_carries_every_template_byte_for_byte() -> None:
    """The sentence is half the record, and the half a payload rule
    would leave undocumented."""
    rendered = events_docgen.reference()
    for name, spec in REGISTRY.items():
        for variant in spec.variants:
            assert f"\n{variant.message}\n" in rendered, f"{name}: template not rendered"


def test_the_reference_enumerates_every_declared_token() -> None:
    rendered = events_docgen.reference()
    for name, spec in REGISTRY.items():
        for variant in spec.variants:
            for field, declared in variant.fields.items():
                for one in declared.tokens or ():
                    assert f"`{one}`" in rendered, f"{name}.{field}: {one} is not rendered"


def test_the_reference_renders_every_declared_note() -> None:
    """The notes are where the field prose the README used to carry
    lives now, so a note nothing renders would be prose deleted rather
    than moved."""
    rendered = flat(events_docgen.reference())
    for name, spec in REGISTRY.items():
        notes = [spec.note]
        for variant in spec.variants:
            notes.append(variant.note)
            notes += [declared.note for declared in variant.fields.values()]
            notes += [declared.note for declared in variant.args]
        for note in notes:
            if note:
                assert flat(note) in rendered, f"{name}: a declared note is not rendered"


def test_the_reference_describes_every_kind_it_may_print() -> None:
    """A new kind arrives with its sentence rather than as a bare word
    in a table, which is what makes the taxonomy readable by somebody
    who has not read the registry."""
    rendered = events_docgen.reference()
    for kind in Kind:
        assert kind in events_docgen.KIND_MEANING
        assert f"| `{kind.name}` |" in rendered
    for kind in ArgKind:
        assert kind in events_docgen.ARG_KIND_MEANING
        assert f"| `{kind.name}` |" in rendered


def test_the_reference_prints_every_syntax_and_grammar() -> None:
    """The field tables name these rather than repeating them, so a
    named one the document does not print is a dangling reference."""
    rendered = events_docgen.reference()
    for syntax in SYNTAXES.values():
        assert f"| `{syntax.name}` |" in rendered
    for grammar in GRAMMARS.values():
        assert f"| `{grammar.name}` |" in rendered
        for builder in grammar.builders:
            assert f"`{builder}`" in rendered


def test_a_pattern_that_begins_with_a_space_keeps_it() -> None:
    """Three grammars match a leading space, and both a bare code span
    and the whitespace flattening every other cell gets would eat it,
    which would document a fragment nothing produces."""
    rendered = events_docgen.reference()
    assert "`' from entry \"[\\s\\S]+\"'`" in rendered


def test_the_reference_says_it_is_generated_and_how() -> None:
    """The header every generated document in this repository carries,
    because the first thing a reader does with a wrong line is edit it."""
    rendered = flat(events_docgen.reference())
    assert "Generated from the registry by `samtal-server events reference`" in rendered
    assert "Do not edit this file by hand" in rendered


# --- the command, in a process of its own ------------------------------


@pytest.mark.parametrize(
    "written",
    # A plain misspelling, and a credential-shaped one, since an
    # environment is a place secrets live. Neither is a word this
    # document uses, which is what makes the absence assertion mean
    # something.
    ["yes-please", "sk-env-2f9c7b1d-never-a-real-credential"],
)
def test_an_unusable_enforcement_value_does_not_block_the_reference(
    tmp_path: Path, written: str
) -> None:
    """The command dispatches before the entrypoint resolves the mode,
    on purpose. A server-only variable somebody misspelled must not
    stand between a reader and the document that says what the events
    are, and the misspelling is not echoed back either way."""
    done = subprocess.run(
        [sys.executable, "-m", "samtal_server.main", "events", "reference"],
        cwd=tmp_path,
        env={**os.environ, ENFORCEMENT_ENV: written, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert done.returncode == 0, done.stderr
    assert done.stdout == events_docgen.reference()
    assert written not in done.stdout
