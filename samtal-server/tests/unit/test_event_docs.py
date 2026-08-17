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

import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from samtal_server import events_cli, events_docgen
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


def logging_section() -> str:
    """The Logging section alone, flattened.

    Sliced at the next top-level heading rather than read to the end of
    the file, so an assertion about what this section says cannot be
    satisfied by a sentence three sections further down."""
    lines = README.read_text(encoding="utf-8").splitlines()
    start = lines.index("## Logging")
    end = next(
        position
        for position, line in enumerate(lines[start + 1 :], start=start + 1)
        if line.startswith("## ")
    )
    return flat("\n".join(lines[start:end]))


def flat(text: str) -> str:
    """The document with its line breaks flattened. Both documents wrap
    their prose, so a sentence asserted on here lands across two lines
    as soon as a word ahead of it changes, and an assertion that broke
    on rewrapping would be an assertion about the wrapping."""
    return " ".join(text.split())


# --- the reference, sliced so a row can be read against its own row ----

ARGUMENT_HEADER = "| # | Argument | Nullable | Constraint | Note |"

FIELD_HEADER = "| Field | Kind | Required | Nullable | Constraint | Note |"


def variant_sections() -> dict[str, list[tuple[str, list[str]]]]:
    """The rendered document as event name to its variant subsections,
    each a heading and the lines under it.

    Slicing is what makes the assertions exact. A substring search over
    the whole document says a property is documented somewhere, which is
    true of almost anything in two thousand lines of tables."""
    events: dict[str, list[tuple[str, list[str]]]] = {}
    event: str | None = None
    heading: str | None = None
    body: list[str] = []

    def close() -> None:
        if event is not None and heading is not None:
            events[event].append((heading, body))

    for line in events_docgen.reference().splitlines():
        if line.startswith("### `"):
            close()
            event = line.removeprefix("### `").removesuffix("`")
            events[event] = []
            heading, body = None, []
        elif line.startswith("#### "):
            close()
            heading, body = line, []
        elif heading is not None:
            body.append(line)
    close()
    return events


def table(lines: list[str], header: str) -> list[list[str]]:
    """The rows under one table header, as stripped cells.

    Split on unescaped pipes only, since a cell may carry an escaped one
    and a naive split would turn one row into two halves of nothing."""
    start = lines.index(header)
    rows = []
    for line in lines[start + 2 :]:
        if not line.startswith("|"):
            break
        rows.append([one.strip() for one in re.split(r"(?<!\\)\|", line.strip())[1:-1]])
    return rows


def yes(value: bool) -> str:
    return "yes" if value else "no"


def cell(note: str) -> str:
    """A declared note as a table cell holds it: one line, pipes
    escaped."""
    return flat(note).replace("|", "\\|")


def token(value: str) -> str:
    """A declared token as the constraint column shows it: a code span,
    quoted where a code span alone would not show the value, which is
    the empty token and the ones with an edge space."""
    if value and value == flat(value):
        return f"`{value}`"
    return f"`'{value}'`"


def check_constraint(rendered: str, declared: Any, kind: str, where: str) -> None:
    """What the constraint column has to say for one declaration.

    Built from the declaration here rather than from the generator's own
    helpers, so this is a second opinion about the cell and not the same
    string computed twice."""
    if declared.tokens:
        for one in sorted(declared.tokens):
            assert token(one) in rendered, f"{where}: token {one!r} missing"
        # And no sixth token in a set of five: every code span in the
        # cell is one of the declared values.
        assert rendered.count("`") == 2 * len(declared.tokens), where
        return
    if declared.syntax is not None:
        assert f"`{declared.syntax.name}`" in rendered, where
        if kind.endswith("_LIST"):
            assert "each element" in rendered, where
        return
    if declared.bounds is not None:
        assert str(declared.bounds.max_length) in rendered, where
        assert declared.bounds.charset in rendered, where
        return
    if getattr(declared, "grammar", None) is not None:
        assert f"`{declared.grammar.name}`" in rendered, where
        return
    if declared.joined:
        assert "joined" in rendered, where
        return
    if kind == "SOURCES":
        assert "provenance" in rendered, where
        return
    # Nothing further is declared, so the cell claims nothing further.
    assert rendered == "", where


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
    lead = logging_section().split(INDEX_HEADER)[0]
    assert f"on the `{SESSION_CHANNEL}` channel" in lead
    assert "carry `session` and `device`" in lead


def test_the_index_points_at_the_generated_reference() -> None:
    """The index makes no schema claim of its own, so it has to say
    where the schema claims are."""
    assert "(../docs/reference/events.md)" in logging_section()


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


def test_every_event_renders_exactly_its_declared_variants() -> None:
    """The drift step guards the content of what is rendered; this
    guards that everything is, and nothing else. A generator that
    stopped at an event's first variant would produce a document CI
    diffs happily."""
    rendered = variant_sections()
    assert set(rendered) == set(REGISTRY)
    for name, spec in REGISTRY.items():
        assert [heading for heading, _ in rendered[name]] == [
            f"#### Variant {position}: `{variant.channel}` at "
            f"{logging.getLevelName(variant.level)}"
            for position, variant in enumerate(spec.variants, start=1)
        ], name


def test_the_reference_carries_every_template_byte_for_byte() -> None:
    """The sentence is half the record, and the half a payload rule
    would leave undocumented."""
    rendered = variant_sections()
    for name, spec in REGISTRY.items():
        for (heading, body), variant in zip(rendered[name], spec.variants, strict=True):
            assert body[body.index("```text") + 1] == variant.message, f"{name} {heading}"


def test_every_argument_row_matches_its_declaration() -> None:
    """Row for row against the variant it belongs to, rather than by
    hunting for a substring somewhere in a two-thousand-line document.
    A global search is what let the one nullable argument position go
    undocumented while the suite stayed green: every property it
    claimed was true of some other row."""
    rendered = variant_sections()
    for name, spec in REGISTRY.items():
        for (heading, body), variant in zip(rendered[name], spec.variants, strict=True):
            where = f"{name} {heading}"
            if not variant.args:
                assert ARGUMENT_HEADER not in body, where
                assert "No arguments: the sentence is fixed." in body, where
                continue
            rows = table(body, ARGUMENT_HEADER)
            assert len(rows) == len(variant.args), where
            for position, (row, arg) in enumerate(zip(rows, variant.args, strict=True), start=1):
                index, kind, nullable, constraint, note = row
                assert index == str(position), where
                assert kind == f"`{arg.kind.name}`", f"{where} argument {position}"
                assert nullable == yes(arg.nullable), f"{where} argument {position}"
                assert note == cell(arg.note), f"{where} argument {position}"
                check_constraint(constraint, arg, arg.kind.name, f"{where} argument {position}")


def test_every_field_row_matches_its_declaration() -> None:
    """The same, for the payload half: every declared field in its
    declared order, with the kind, requiredness, nullability, constraint
    and note that field declares and nothing else."""
    rendered = variant_sections()
    for name, spec in REGISTRY.items():
        for (heading, body), variant in zip(rendered[name], spec.variants, strict=True):
            where = f"{name} {heading}"
            rows = table(body, FIELD_HEADER)
            assert [row[0] for row in rows] == [f"`{one}`" for one in variant.fields], where
            for row, (field, declared) in zip(rows, variant.fields.items(), strict=True):
                _, kind, required, nullable, constraint, note = row
                assert kind == f"`{declared.kind.name}`", f"{where} {field}"
                assert required == yes(declared.required), f"{where} {field}"
                assert nullable == yes(declared.nullable), f"{where} {field}"
                assert note == cell(declared.note), f"{where} {field}"
                check_constraint(constraint, declared, declared.kind.name, f"{where} {field}")


def test_the_reference_renders_every_declared_prose_note() -> None:
    """The event and variant notes, which are paragraphs rather than
    cells: the field and argument notes are asserted by the two row
    tests above, exactly rather than by presence."""
    rendered = flat(events_docgen.reference())
    for name, spec in REGISTRY.items():
        notes = [spec.note, *[variant.note for variant in spec.variants]]
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


def test_a_reader_who_stops_reading_gets_no_traceback(tmp_path: Path) -> None:
    """`samtal-server events reference | head` is an ordinary thing to
    do with a document this long, and the document is far longer than a
    pipe buffer, so the write really does fail rather than finishing
    into the buffer unnoticed. What the reader must never see for it is
    a traceback: a closed pipe is a reader who has read enough, and the
    answer is the shell's own status for one."""
    child = subprocess.Popen(
        [sys.executable, "-m", "samtal_server.main", "events", "reference"],
        cwd=tmp_path,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert child.stdout is not None and child.stderr is not None
    first = child.stdout.readline()
    # What `head` does: stop reading and close, while the writer is
    # still going.
    child.stdout.close()
    errors = child.stderr.read()
    status = child.wait()
    child.stderr.close()

    assert first == "# Event schema reference\n"
    assert "Traceback" not in errors
    # And not the other spelling either: an unflushable stream at
    # interpreter shutdown prints a complaint without a traceback.
    assert "Exception ignored" not in errors
    assert errors == ""
    assert status == events_cli.BROKEN_PIPE_STATUS
