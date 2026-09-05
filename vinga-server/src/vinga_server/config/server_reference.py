"""The server configuration's documentation, rendered from the models.

The half of the configuration a YAML file holds, `server:`, as the page
committed at `docs/reference/server-config.md`: every key with its type,
its default, its bounds and its description, the sections' own prose,
the environment overrides, the two values that deliberately have no key,
and the combinations a server refuses to start on. A caller gets that
document as a string without knowing how model metadata, docstrings and
refusal constants become markdown.

Its own module rather than a section of `docgen.py` because the two
change for separate reasons: that one renders the domain registry and
moves when entity kinds and provider options move, and this one moves
when `ServerConfig` does. What they share is the vocabulary a page is
written in, `type_name`, `default`, `nested_model`, `paragraph` and
`cell`, imported from there rather than copied here, so two pages
rendering the same models cannot come to describe a type or wrap a
paragraph differently.

Nothing on this page is written twice. A field's description, a
section's docstring and a boot refusal's sentence each have exactly one
home in `models.py`, and this reads them; the bounds in the Constraints
column are the `Ge`/`Gt`/`Le`/`Lt` metadata pydantic enforces, so a bound
cannot be documented and enforced as two numbers; the environment
variables are the constants the loader and the database package apply,
so a rename moves the page rather than staling it. What this module owns
is the page's structure and its connective prose.

Deterministic, like its neighbour: no timestamps, no set iteration, and
the field order is the models' own declaration order. CI regenerates the
committed page and diffs it byte for byte.

Read-only, and deliberately so: nothing here opens the database, reads a
configuration file, needs an encryption key or imports the application.
`test_server_reference.py` pins that in a child interpreter, so the edge
cannot come back unnoticed.
"""

import textwrap
from collections.abc import Callable, Iterable

import annotated_types
from pydantic import BaseModel
from pydantic.fields import FieldInfo

from vinga_server.config import docgen
from vinga_server.config.docgen import cell, default, nested_model, paragraph, type_name
from vinga_server.config.entities import CONFIG_FILE
from vinga_server.config.loader import ConfigError
from vinga_server.config.models import (
    BOOT_REFUSALS,
    DATABASE_ENV_NAMES,
    DATABASE_GENERIC_ENV_PREFIX,
    DATABASE_PASSWORD_ENV,
    DATABASE_SECTION,
    DATABASE_URL_ENV,
    ENV_NESTING,
    PROGRAM,
    SERVER_ENV_PREFIX,
    ServerConfig,
)

# The half this module renders, which is both the selector word the
# command takes and the section's own key in the YAML file. One string,
# because those are one thing: the page documents `server:`.
SERVER = "server"

# The other half's committed page, which sits beside this one in the
# same directory, so this page points at it by name.
DOMAIN_DOCUMENT = "domain-config.md"

# Where a field's description, a section's prose and a refusal's
# sentence are written, quoted so a reader who wants to correct the page
# is sent to the file rather than to this one.
MODELS = "vinga-server/src/vinga_server/config/models.py"

# The connective prose this page owns
#
# Fixed blocks, the way `docgen.reference()` owns the domain page's
# preamble. Everything a model can carry is read off the model; what is
# here is what no field is about: the split between the halves, the
# override scheme, and why two values have no key.

PREAMBLE = (
    "The server half of the configuration is the `server:` section of the YAML file "
    "this process is launched with. It is read once at start and never re-read by a "
    "running process, which is the line between it and the domain half: the domain "
    "half (providers, MCP servers, prompt fragments, agent defaults, agents, devices, "
    "the default agent) is held in the server's database and is documented in "
    "[`{domain}`]({domain}). "
    "[`config.example.yaml`]({example}) is the annotated, copyable starting point for "
    "this half; this page is the complete contract, and the two describe the same "
    "models, so they may differ in coverage and never in fact."
)

ENVIRONMENT_SCHEME = (
    "Any key below can be overridden from the environment as `{prefix}<PATH>`, with "
    "`{nesting}` joining the nesting: `{prefix}PORT=9000` sets the port and "
    "`{prefix}ONBOARDING{nesting}KEY` sets the pinned onboarding key. A variable beats "
    "the file, the file beats the defaults in the tables below, and a `.env` file "
    "beside the directory the server is started in is read as well."
)

DATABASE_EXCEPTION = (
    "The `{section}` section is the one recorded exception. Its four keys have short "
    "spellings of their own, {names}, and those are the documented ones: the compose "
    "file feeds the Postgres image from the same four, so one `.env` flows into both "
    "sides of the development loop. The generic `{generic}*` spelling would work by "
    "accident of the nesting scheme, and is refused at boot with a sentence naming the "
    "short one instead, because a fact with two names is a fact with a bug pending."
)

NO_KEY_INTRO = (
    "Two values are environment-only and have no key on any of these models at all. "
    "Both belong to the database connection, and both are set beside a deployment's "
    "other credentials rather than in the file it edits."
)

NO_PASSWORD_KEY = (
    "`{password}` is the database password. A credential in a configuration file is "
    "what the no-secrets-in-YAML stance exists to prevent, and a field for it would be "
    "a value that every configuration read, diff and generated page then had to "
    "remember not to print."
)

NO_URL_KEY = (
    "`{url}` is the whole connection URL, which overrides the four discrete keys at "
    "once. It has no key here for the reason above and one more: a URL carries a "
    "password in its authority and can carry another in its query."
)

REFUSALS_INTRO = (
    "Some combinations of the keys above are refused when the server starts, because "
    "each of them is a misconfiguration rather than a choice. They are cross-field "
    "rules, so they belong to no single row of the tables above; each is listed here "
    "in the words the model's own validator raises."
)


def reference() -> str:
    """The whole server-half document, rendered from the models."""
    lines = [
        "# Server configuration reference",
        "",
        f"Generated from the pydantic models by `{PROGRAM} reference {SERVER}`.",
        "Do not edit this file by hand: CI regenerates it and fails on any",
        "difference, so an edit here is reverted by the next run. The text of a",
        "field description, of a section's prose and of a boot refusal lives on",
        f"the models in `{MODELS}`.",
        "",
        *paragraph(PREAMBLE.format(domain=DOMAIN_DOCUMENT, example=CONFIG_FILE)),
        "",
        *_environment_section(),
        *_no_key_section(),
        *_sections(ServerConfig, SERVER),
        *_refusal_section(),
    ]
    return "\n".join(lines).rstrip("\n") + "\n"


def _environment_section() -> list[str]:
    """The override scheme, and the section that is its exception.

    Every variable named here is read from the constant the loader
    applies, so a rename moves this page instead of staling it.
    """
    return [
        "## Environment overrides",
        "",
        *paragraph(ENVIRONMENT_SCHEME.format(prefix=SERVER_ENV_PREFIX, nesting=ENV_NESTING)),
        "",
        *paragraph(
            DATABASE_EXCEPTION.format(
                section=DATABASE_SECTION,
                names=_listed(DATABASE_ENV_NAMES.values()),
                generic=DATABASE_GENERIC_ENV_PREFIX,
            )
        ),
        "",
    ]


def _no_key_section() -> list[str]:
    """The two values with no configuration form, named from the same
    constants the database package reads them under."""
    return [
        "## What deliberately has no key",
        "",
        *paragraph(NO_KEY_INTRO),
        "",
        *paragraph(NO_PASSWORD_KEY.format(password=DATABASE_PASSWORD_ENV)),
        "",
        *paragraph(NO_URL_KEY.format(url=DATABASE_URL_ENV)),
        "",
    ]


def _sections(model: type[BaseModel], path: str) -> list[str]:
    """One model's section, and then a section per model nested under it.

    Depth-first and after the parent, so a reader meets a section before
    its contents, and in the fields' own declaration order, so the page
    reads in the order the file is written in. A nested section is named
    by the path a key is written at rather than by the model's class
    name, since the path is what an operator types.
    """
    lines = [f"## `{path}`", "", *_prose(model), *_table(model), ""]
    for name, info in model.model_fields.items():
        nested = nested_model(info.annotation)
        if nested is not None:
            lines += _sections(nested, f"{path}.{name}")
    return lines


def _prose(model: type[BaseModel]) -> list[str]:
    """A model's docstring as the section's prose, paragraph by
    paragraph.

    The whole of it rather than its first sentence, which is the
    difference between this page and a table's heading: what a docstring
    here carries is why the section is shaped the way it is, and that is
    what an operator deciding whether to touch it needs. Normalized and
    wrapped, so the source's own indentation and line breaks are the
    file's business rather than the page's.
    """
    lines: list[str] = []
    for block in (model.__doc__ or "").strip().split("\n\n"):
        normalized = " ".join(block.split())
        if normalized:
            lines += [*paragraph(normalized), ""]
    return lines


def _table(model: type[BaseModel]) -> list[str]:
    """One model's fields, in declaration order.

    Five columns rather than the domain page's four. The extra one is
    Constraints, because the server half is where a numeric bound is an
    operator fact: a port range, a positive number of seconds, a floor
    under a token budget. It is rendered from the metadata pydantic
    enforces, so a bound cannot be documented as one number and enforced
    as another.
    """
    rows = [
        "| Key | Type | Default | Constraints | Description |",
        "| --- | --- | --- | --- | --- |",
    ]
    rows += [
        f"| `{name}` | `{cell(type_name(info.annotation))}` | `{default(info)}` | "
        f"{_constraints(info)} | {cell(info.description)} |"
        for name, info in model.model_fields.items()
    ]
    return rows


# The bounds pydantic keeps on a field, as the symbol a reader wants and
# the attribute the value is under. In this order, so a field carrying
# both a floor and a ceiling renders them in that order.
_BOUNDS: tuple[tuple[type, str, str], ...] = (
    (annotated_types.Ge, ">=", "ge"),
    (annotated_types.Gt, ">", "gt"),
    (annotated_types.Le, "<=", "le"),
    (annotated_types.Lt, "<", "lt"),
)


def _constraints(info: FieldInfo) -> str:
    """One field's bounds, as a table cell.

    A closed range reads as the range (`1 to 65535`), since that is what
    a port is, and anything else reads as its symbols (`>= 512`, `> 0`).
    A field with no bound gets an empty cell rather than a placeholder:
    an unbounded number is not a defect, which is the one way this
    differs from an undescribed field.

    Metadata that is not a bound is deliberately not rendered. A
    validator carried here (the environment-variable names carry one) is
    a rule with no numeric form, and those are stated in the field's own
    description, which is where a reader meets them.
    """
    found = [
        (symbol, getattr(item, attribute))
        for kind, symbol, attribute in _BOUNDS
        for item in info.metadata
        if isinstance(item, kind)
    ]
    bounds = dict(found)
    if len(found) == 2 and ">=" in bounds and "<=" in bounds:
        return f"{bounds['>=']} to {bounds['<=']}"
    return ", ".join(f"{symbol} {value}" for symbol, value in found)


def _refusal_section() -> list[str]:
    """What a server refuses to start on, in the validators' own
    sentences.

    Read off `BOOT_REFUSALS`, which is checked in both directions by the
    suite: every row's provocation raises exactly its sentence, and every
    model-level validator reachable from `ServerConfig` is claimed by a
    row, so a rule cannot be enforced and left unpublished.
    """
    lines = ["## Refused at boot", "", *paragraph(REFUSALS_INTRO), ""]
    for refusal in BOOT_REFUSALS:
        lines += _item(refusal.sentence)
    lines.append("")
    return lines


def _item(text: str) -> list[str]:
    """One fixed sentence as a list item.

    `paragraph`'s wrapping with room made for the marker and a hanging
    indent under it, which `textwrap` does and a caller of `paragraph`
    cannot: the width and the two break rules are the page's, read from
    `docgen` rather than chosen again here.
    """
    return textwrap.wrap(
        text,
        width=docgen.PROSE_WIDTH,
        initial_indent="- ",
        subsequent_indent="  ",
        break_long_words=False,
        break_on_hyphens=False,
    )


def _listed(names: Iterable[str]) -> str:
    """Several names in one sentence, code-spanned and joined the way
    prose joins them rather than by a comma the reader has to finish."""
    quoted = [f"`{name}`" for name in names]
    if len(quoted) == 1:
        return quoted[0]
    return ", ".join(quoted[:-1]) + f" and {quoted[-1]}"


# The two halves, and the one command that renders either
#
# One ordered registry rather than three closed sets kept in step: the
# dispatch below reads it, the CLI positional's help lists its keys, and
# the refusal for a name that is in neither names them from the same
# tuple. Adding a half is a row here, which is the derived-grammar rule
# the CLI guide states.
#
# `docgen` never imports this module, so the domain renderer can be named
# here without a cycle.

HALVES: tuple[tuple[str, Callable[[], str]], ...] = (
    (docgen.DOMAIN, docgen.reference),
    (SERVER, reference),
)

# Which half a bare `reference` renders, which is the first row: the
# domain page is what that command printed before there was a second
# half, and every committed line that runs it stays true.
DEFAULT_HALF = HALVES[0][0]

# What a selector naming neither half says. The word is not quoted back,
# the rule every refusal about an identity follows: what exists is the
# useful half, and what was typed is the half the person typing it can
# already see.
NO_SUCH_HALF = (
    "the configuration has two halves and that is neither of them; expected one of: "
    "{halves}. What was named is not quoted back"
)


def half_names() -> list[str]:
    """The halves that can be asked for, in the order the registry
    declares them."""
    return [name for name, _ in HALVES]


def render(half: str) -> str:
    """One half's reference page, or a ConfigError naming the halves
    that exist.

    Kept here rather than in the CLI so that the command and anything
    else that renders a page accept exactly the same names, which is
    where `docgen.entity` sits for the same reason.
    """
    for name, renderer in HALVES:
        if name == half:
            return renderer()
    raise ConfigError(NO_SUCH_HALF.format(halves=", ".join(half_names())))


__all__ = [
    "DEFAULT_HALF",
    "HALVES",
    "SERVER",
    "half_names",
    "reference",
    "render",
]
