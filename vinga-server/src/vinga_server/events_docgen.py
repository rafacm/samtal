"""The event surface's documentation, rendered from the declarations.

One source, two renderings, the discipline the domain configuration and
the conversation store already keep: every event is declared once, and
this reads the declarations as the markdown reference committed at
`docs/reference/events.md` and as what `vinga-server events reference`
prints. CI regenerates the committed copy and diffs it byte for byte, so
the document cannot say anything the declarations do not.

The declarations are the typed variants in `events/catalog.py`, and
There is no second description for this to read. A variant's class-level
facts, its declared values and the value types those name carry every
property the tables below print, which is the whole point of a
declaration that IS its emission: what a generator would have restated
is what a caller constructs.

That is what makes this the place field and token facts belong. The
README's event table used to carry them in prose nothing checked, which
reads as checked and is not; it is a name-and-when index now, and every
kind, token set, syntax and bound is here, where a wrong claim turns a
lane red.

Everything here is deterministic: no timestamps, no set iteration, and
the events, variants and fields come out in the catalog's own
declaration order. Token sets and channel sets are sorted, since those
are the only unordered things a declaration holds.

Read-only, and deliberately so: nothing here opens a database, reads a
configuration file, or needs a key. It imports the catalog and the
standard library, which is what lets the command in front of it run on a
machine whose server will not start.
"""

import logging
import textwrap

from vinga_server.events.catalog import (
    CHANNELS,
    SERVER_CHANNELS,
    SESSION_CHANNEL,
    Declaration,
    Declared,
    Variant,
    arg_kind_of,
    carried_values,
    catalog,
    grammar_of,
    kind_of,
    rendered_values,
    tokens_of,
)
from vinga_server.events.values import (
    GRAMMARS,
    IDENTIFIER_DOMAIN,
    SOURCE_FORMS,
    SOURCE_KEY_PATTERN,
    SYNTAXES,
    ArgKind,
    Kind,
)


def documented() -> dict[str, Declaration]:
    """Every event the reference describes, in the catalog's own
    declaration order, which is the order a reader meets them: a
    device's check-in, its session, the pipeline inside it, the
    providers behind that, then the server's own lifecycle surfaces."""
    return catalog()


def channels_of(declaration: Declaration) -> frozenset[str]:
    return frozenset(one.CHANNEL for one in declaration.variants)


def levels_of(declaration: Declaration) -> frozenset[int]:
    return frozenset(one.LEVEL for one in declaration.variants)

# Where the reference's prose wraps. The tables cannot wrap (a row is a
# line), so only paragraphs go through this.
PROSE_WIDTH = 78

# The documents this one points at, relative to the committed copy at
# docs/reference/events.md. Printed as written when the same document
# goes to stdout.
LOGGING_SECTION = "../../vinga-server/README.md#logging"
OBSERVABILITY_ADR = "../adr/2026-08-04-json-logs-are-the-observability-surface.md"
CONTENT_ADR = "../adr/2026-08-15-content-and-telemetry-are-separate-surfaces.md"
CONVERSATIONS_REFERENCE = "conversations-schema.md"

# What each field kind means, in one sentence. The registry names the
# kinds; this is the half a reader needs and an enum cannot carry. The
# docs suite asserts every kind is described, so a new one arrives with
# its sentence rather than as a bare word in a table.
KIND_MEANING: dict[Kind, str] = {
    Kind.IDENTIFIER: (
        "A trusted name the operator or this server chose: an agent, a "
        "configured entry, a pipeline stage, a path, an origin. Trusted is "
        "about provenance rather than shape, so its domain is the "
        "configuration's own and no tighter."
    ),
    Kind.TOKEN: "One value out of the field's declared closed set, listed in full below.",
    Kind.CLASS_NAME: (
        "An exception or type name. Never a message: a type name says what "
        "went wrong, a message says what a stranger wrote."
    ),
    Kind.ID: (
        "A bounded machine form this server minted or normalized, held to a "
        "named syntax rather than to a generic length."
    ),
    Kind.DESCRIPTOR: (
        "A far-side string retained deliberately: what a device says about "
        "itself at check-in, bounded and stripped of unprintables at its "
        "decision site and bounded again at emit."
    ),
    Kind.INT: "A whole number. Booleans are refused, since `True` is an `int` to Python.",
    Kind.FLOAT: (
        "A finite number, whole or fractional. Infinities and NaN are refused: "
        "they are not measurements and JSON cannot carry them."
    ),
    Kind.BOOL: "`True` or `False`.",
    Kind.COUNT: "A whole number of zero or more, for the fields whose meaning is how many.",
    Kind.IDENTIFIER_LIST: "A list whose every element is an `IDENTIFIER`.",
    Kind.ID_LIST: "A list whose every element is an `ID` of the field's declared syntax.",
    Kind.SOURCES: (
        "The one structured kind: a mapping from prompt provenance to "
        "character counts, keyed by the grammar below."
    ),
}

# And the same for the argument kinds, which are their own taxonomy
# because the rendered sentence carries shapes no payload field does.
ARG_KIND_MEANING: dict[ArgKind, str] = {
    ArgKind.IDENTIFIER: "As the field kind.",
    ArgKind.TOKEN: "As the field kind.",
    ArgKind.CLASS_NAME: "As the field kind.",
    ArgKind.ID: "As the field kind.",
    ArgKind.DESCRIPTOR: (
        "As the field kind, reusing the bounds of the field this position "
        "renders: a lawful descriptor necessarily reaches the sentence that "
        "shows it."
    ),
    ArgKind.INT: "As the field kind.",
    ArgKind.FLOAT: "As the field kind.",
    ArgKind.BOOL: "As the field kind.",
    ArgKind.COUNT: "As the field kind.",
    ArgKind.PATHLIKE: (
        "A trusted configured path, `Path` or `str`. Argument-only: the "
        "payload field beside it carries the same path as an `IDENTIFIER`."
    ),
    ArgKind.COMPOSED: (
        "A formatted fragment of identifiers, held to the named grammar "
        "below rather than to a string type. Argument-only, and the reason "
        "`IDENTIFIER` was not widened to cover punctuation."
    ),
}


def reference() -> str:
    """The whole reference document, rendered from the declarations."""
    events = documented()
    variants = sum(len(spec.variants) for spec in events.values())
    lines = [
        "# Event schema reference",
        "",
        "Generated from the declarations by `vinga-server events reference`. Do",
        "not edit this file by hand: CI regenerates it and fails on any difference,",
        "so an edit here is reverted by the next run. The declarations live in",
        "`vinga-server/src/vinga_server/events/catalog.py`.",
        "",
        *_paragraph(
            f"The structured events are this server's observability surface "
            f"([ADR]({OBSERVABILITY_ADR})), and they carry metadata and nothing "
            f"else ([ADR]({CONTENT_ADR})). This document is that surface written "
            f"down: {len(events)} events in {variants} variants. What was said "
            f"in a conversation is "
            f"in the conversation store instead, keyed by the same `session` "
            f"([its reference]({CONVERSATIONS_REFERENCE}))."
        ),
        "",
        *_paragraph(
            "A site does not describe an emission; it constructs one. Every "
            "variant below is a type, its values are types, and its sentence "
            "and argument order are derived from its own fields, so an "
            "emission that is not one of these shapes cannot be built at all. "
            "What is left at runtime is the construction itself, which happens "
            "inside the emitter's guard: a construction that refuses is said "
            "once on the emitter's own channel and dropped, because a "
            "telemetry bug must never cost a reply."
        ),
        "",
        *_paragraph(
            f"The [README's Logging section]({LOGGING_SECTION}) "
            f"is the human overview, with one line per event saying when it "
            f"fires."
        ),
        "",
        "## How to read it",
        "",
        *_paragraph(
            "A variant is one whole emission shape: where it rides, how loud it "
            "is, the sentence it renders, the arguments that sentence takes, and "
            "the payload it carries. Events have more than one because the "
            "surface does: `session_rejected` is emitted with three arities "
            "across four templates on two channels, `mcp_reload`'s applied and "
            "refused answers carry mutually exclusive fields, and several events "
            "change level with shape. A site constructs exactly one of them."
        ),
        "",
        *_paragraph(
            "The template is byte-exact, and the argument table's rows are its "
            "`%` positions in order, each naming the declared field whose value "
            "that position renders. A message that is not one of the declared "
            "templates fails even when every field is lawful, because the "
            "rendered sentence reaches the same taps the payload does."
        ),
        "",
        *_paragraph(
            "A variant's field table is the WHOLE payload a tap receives, base "
            "fields included: `event` everywhere, and `session` and `device` on "
            "the session channel, where the emitter owns them and a variant "
            "declaring one is refused at import. On a server channel `session` "
            "and `device` are ordinary fields, declared where they are carried. "
            "Required says the field is always present in that variant; nullable "
            "says it may be present and null. An argument position carries the "
            "same nullability column, for the positions whose value the sentence "
            "may have to render as nothing."
        ),
        "",
        *_paragraph(
            "This catalog is also a LIVE surface. The same emissions are "
            "streamed to an authenticated operator over "
            "`GET /api/runtime/events`, one JSON object per event, and what "
            "rides there is what is documented here plus two fields the "
            "stream owns: `ts`, the wall-clock instant the event was emitted "
            "at, and `level`, the name of the level in that variant's row. "
            "Nothing is kept behind that stream, which is what makes it a "
            "second transport over this surface rather than a store: a "
            "reader joins the present, and what happened before is the "
            "conversation record's to answer. A reader who needs the exact "
            "object reads the JSON log, which is the retained copy of the "
            "same records."
        ),
        "",
        *_paragraph(
            "Two per-frame samples are outside all of this on purpose. The "
            "endpointer track and the dropped-frame counts are capture side "
            "channels rather than events, so they are outside the tap contract "
            "and outside this registry, which is what keeps validation a cost "
            "paid per decision rather than per frame."
        ),
        "",
        "## The channels",
        "",
        *_paragraph(
            f"The channel is the scope. One session channel, `{SESSION_CHANNEL}`, "
            f"carries everything a conversation says about itself; the "
            f"{len(SERVER_CHANNELS)} server channels are each a subsystem's own "
            f"module name. An event declared on one channel and emitted from "
            f"another is a violation even when its fields are lawful."
        ),
        "",
        *[f"- `{channel}`" for channel in CHANNELS],
        "",
        "## What a value may be",
        "",
        *_paragraph(
            f"There is no free-text kind, which is the property the whole "
            f"vocabulary exists to keep. Every string field is one of these, and "
            f"a field that would need prose is a design error the taxonomy "
            f"refuses to encode. A trusted identifier's domain is "
            f"{IDENTIFIER_DOMAIN}: what the configuration itself guarantees, "
            f"since a value type claiming more would refuse a lawful "
            f"deployment's traffic."
        ),
        "",
        "| Kind | What it is |",
        "| --- | --- |",
        *[f"| `{kind.name}` | {_cell(KIND_MEANING[kind])} |" for kind in Kind],
        "",
        *_paragraph(
            "A `TOKEN` field's constraint column lists its whole set. A value "
            "that is empty, or that begins or ends with a space, is printed "
            "quoted there, for the reason the patterns below are: a bare code "
            "span shows neither, and a set whose members cannot be read exactly "
            "is not a closed set."
        ),
        "",
        "## What an argument may be",
        "",
        *_paragraph(
            "The sentence's `%` positions have their own taxonomy beside the "
            "field kinds, because a rendered sentence carries shapes no payload "
            "field does."
        ),
        "",
        "| Kind | What it is |",
        "| --- | --- |",
        *[f"| `{kind.name}` | {_cell(ARG_KIND_MEANING[kind])} |" for kind in ArgKind],
        "",
        "## The id syntaxes",
        "",
        *_paragraph(
            "What an `ID` field is held to. Each is anchored at both ends when it "
            "is matched, so a pattern cannot admit a prefix. Patterns are printed "
            "quoted, here and below, because a leading or trailing space is part "
            "of several of them and a bare code span would hide it."
        ),
        "",
        "| Syntax | Pattern | Longest | What it is |",
        "| --- | --- | --- | --- |",
        *[
            f"| `{syntax.name}` | {_pattern(syntax.pattern)} | {syntax.max_length} | "
            f"{_cell(syntax.note)} |"
            for syntax in SYNTAXES.values()
        ],
        "",
        "## The composed grammars",
        "",
        *_paragraph(
            "What a `COMPOSED` argument is held to, with the code that builds it. "
            "Naming the builder is what keeps a grammar honest: a fragment nobody "
            "assembles is a pattern somebody guessed. They are bounded by "
            "structure rather than by character class or length, since what a "
            "fragment promises is its shape and never what an operator may have "
            "called something."
        ),
        "",
        "| Grammar | Pattern | Built by | What it is |",
        "| --- | --- | --- | --- |",
        *[
            f"| `{grammar.name}` | {_pattern(grammar.pattern)} | "
            f"{', '.join(f'`{builder}`' for builder in grammar.builders)} | "
            f"{_cell(grammar.note)} |"
            for grammar in GRAMMARS.values()
        ],
        "",
        "## The prompt provenance grammar",
        "",
        *_paragraph(
            "`prompt_assembled.sources` is the one structured field: a mapping "
            "from where a block of the prompt came from to how many characters it "
            "contributed, never any of the prompt itself. Its keys take these "
            "forms, with `<name>` and `<entry>` configured names and `<position>` "
            "a positive integer."
        ),
        "",
        *[f"- `{form}`" for form in SOURCE_FORMS],
        "",
        *_paragraph(
            "`memory` is deliberately not among them. `prompt_assembled` reports "
            "the cached know-how half of the prompt and excludes the per-round "
            "memory read, so a `memory` key is a violation like any unknown "
            "prefix, even though it is a provenance token elsewhere in the prompt "
            "assembly."
        ),
        "",
        "```text",
        SOURCE_KEY_PATTERN,
        "```",
        "",
        "## The events",
        "",
        *_paragraph(
            "One row per event, in the order the sections below run: the order a "
            "request meets them, from a device's check-in to the server's own "
            "lifecycle surfaces."
        ),
        "",
        "| Event | Channels | Levels | Variants |",
        "| --- | --- | --- | --- |",
        *[_index_row(spec) for spec in events.values()],
        "",
    ]

    for spec in events.values():
        lines += _event_section(spec)

    return "\n".join(lines).rstrip("\n") + "\n"


def _index_row(spec: Declaration) -> str:
    name = f"`{spec.name}`"
    channels = channels_of(spec)
    if channels == frozenset(CHANNELS):
        rides = f"every channel ({len(CHANNELS)})"
    else:
        rides = ", ".join(f"`{channel}`" for channel in sorted(channels))
    levels = ", ".join(
        logging.getLevelName(level) for level in sorted(levels_of(spec))
    )
    return f"| {name} | {rides} | {levels} | {len(spec.variants)} |"


def _event_section(spec: Declaration) -> list[str]:
    lines = [f"### `{spec.name}`", ""]
    if spec.note:
        lines += [*_paragraph(spec.note), ""]
    for position, variant in enumerate(spec.variants, start=1):
        lines += _variant_section(position, variant)
    return lines


def _variant_section(position: int, variant: type[Variant]) -> list[str]:
    """One variant, whole. The heading is numbered because a channel and
    a level do not identify one: three of `activation_refused`'s ride
    the same channel at the same level and differ in their sentence."""
    lines = [
        f"#### Variant {position}: `{variant.CHANNEL}` "
        f"at {logging.getLevelName(variant.LEVEL)}",
        "",
    ]
    if variant.NOTE:
        lines += [*_paragraph(variant.NOTE), ""]
    lines += ["```text", variant.TEMPLATE, "```", ""]
    rendered = rendered_values(variant)
    if rendered:
        lines += [
            "| # | Argument | Nullable | Constraint | Note |",
            "| --- | --- | --- | --- | --- |",
            *[
                f"| {index} | {_argument(one)} | {_yes(one.nullable)} | "
                f"{_arg_constraint(one)} | {_cell(one.rendered_note)} |"
                for index, one in enumerate(rendered, start=1)
            ],
            "",
        ]
    else:
        lines += ["No arguments: the sentence is fixed.", ""]
    lines += [
        "| Field | Kind | Required | Nullable | Constraint | Note |",
        "| --- | --- | --- | --- | --- | --- |",
        *[
            f"| `{one.name}` | `{_kind(one)}` | {_yes(one.required)} | "
            f"{_yes(one.nullable)} | {_field_constraint(one)} | {_cell(one.note)} |"
            for one in carried_values(variant)
        ],
        "",
    ]
    return lines


def _kind(declared: Declared) -> str:
    """One carried value's kind, which `_check` holds every carried
    value to having."""
    kind = kind_of(declared)
    return "" if kind is None else kind.name


def _arg_kind(declared: Declared) -> str:
    """And one rendered value's, which every value type declares."""
    kind = arg_kind_of(declared)
    return "" if kind is None else kind.name


def _argument(declared: Declared) -> str:
    """One `%` position, named as well as kinded.

    The kind alone does not identify the position: `ARGS` is an ordered
    tuple of field names, and two same-kinded entries swapped would
    render two identical cells, so the ordered argument list would have
    no pin in this document at all. The name is the field's own, which
    is what makes a position readable against the field table below it.
    """
    return f"`{declared.name}` (`{_arg_kind(declared)}`)"


def _field_constraint(declared: Declared) -> str:
    """What holds this field's values, beside its kind."""
    kind = kind_of(declared)
    if kind is Kind.TOKEN:
        return _tokens(tokens_of(declared))
    if kind is Kind.ID:
        return _syntax(declared)
    if kind is Kind.ID_LIST:
        return f"each element: {_syntax(declared)}"
    if kind is Kind.DESCRIPTOR:
        return _bounds(declared)
    if kind is Kind.CLASS_NAME and declared.type.JOINED:
        return "one name, or several joined with `, `"
    if kind is Kind.SOURCES:
        return "keyed by the prompt provenance grammar, with counts for values"
    return ""


def _arg_constraint(declared: Declared) -> str:
    kind = arg_kind_of(declared)
    grammar = grammar_of(declared)
    if kind is ArgKind.TOKEN:
        return _tokens(tokens_of(declared))
    if kind is ArgKind.ID:
        return _syntax(declared)
    if kind is ArgKind.DESCRIPTOR:
        return _bounds(declared)
    if kind is ArgKind.COMPOSED and grammar is not None:
        return f"the `{grammar.name}` grammar"
    if kind is ArgKind.CLASS_NAME and declared.type.JOINED:
        return "one name, or several joined with `, `"
    return ""


def _tokens(tokens: frozenset[str] | None) -> str:
    """A closed set, sorted so two runs render it the same way."""
    if not tokens:
        return ""
    return "one of: " + ", ".join(_token(one) for one in sorted(tokens))


def _token(value: str) -> str:
    """One declared value of a closed set.

    Quoted where a bare code span would not show it: the empty token
    (`tool_call`'s trailing fragment renders nothing on the branch that
    has nothing to add) and the ones that begin or end with a space (the
    same fragment's other value). A set whose members a reader cannot
    read exactly is not a closed set, which is the whole of what a
    `TOKEN` claims.
    """
    if value and value == " ".join(value.split()):
        return "`" + value.replace("|", "\\|") + "`"
    return "`'" + value.replace("|", "\\|") + "'`"


def _syntax(declared: Declared) -> str:
    syntax = declared.type.SYNTAX
    if syntax is None:
        return ""
    return f"the `{syntax.name}` syntax"


def _bounds(declared: Declared) -> str:
    bounds = declared.type.BOUNDS
    if bounds is None:
        return ""
    return f"at most {bounds.max_length} characters, every one {bounds.charset}"


def _yes(value: bool) -> str:
    return "yes" if value else "no"


def _pattern(pattern: str) -> str:
    """A declared pattern inside a table cell, quoted and unflattened.

    Three of the grammars begin or end with a space that is part of what
    they match, and both a bare code span and the whitespace flattening
    every other cell gets would silently eat it. The quotes are what make
    the empty pattern visible too."""
    return "`'" + pattern.replace("|", "\\|") + "'`"


def _cell(text: str) -> str:
    """Text inside a table cell. A pipe ends a cell even inside a code
    span, and several declared patterns are alternations, so it is
    escaped; and a cell is one line, so any wrapping in the declaration
    is flattened."""
    return " ".join(text.split()).replace("|", "\\|")


def _paragraph(prose: str) -> list[str]:
    """One paragraph, wrapped. The committed reference is read as a file
    as often as it is rendered, and an unwrapped paragraph makes every
    edit to it a one-line diff of the whole thing.

    Never inside a word or across a hyphen, for the reason the other two
    references give: the default would break an identifier in half, and a
    code span split over two lines renders with a space in it."""
    return textwrap.wrap(prose, width=PROSE_WIDTH, break_long_words=False, break_on_hyphens=False)


__all__ = ["ARG_KIND_MEANING", "KIND_MEANING", "documented", "reference"]
