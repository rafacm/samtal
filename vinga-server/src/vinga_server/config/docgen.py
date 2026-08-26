"""The domain configuration's documentation, rendered from the models.

Three renderings, one source. `Field(description=...)` on the domain
models is written once and read here as JSON Schema (what an API client
or an agent reads before writing a fragment), as the markdown reference
committed at `docs/reference/domain-config.md`, and as the `--help`
text of the commands that take a fragment. A description that is only
true in one of them cannot exist, which is the point.

What a model cannot carry, a kind's purpose and its notes and the
command that writes one, comes from the descriptor registry in
`entities.py`, which the other surfaces read as well. This module
renders those descriptors rather than keeping a second copy of them, so
prose that is only true in the documentation cannot exist either.

A fourth rendering has a different source: the configuration API's
OpenAPI document comes from that application's routes, committed at
`docs/reference/api-openapi.json` under the same regenerate-and-diff
check. It lives here because it is documentation, and because these
commands are the ones a documentation lane runs.

Everything here is deterministic: no timestamps, no set iteration, and
the field order is the models' own declaration order. CI regenerates
the committed reference and diffs it byte for byte, so anything that
varied between two runs would turn the lane red on an unrelated change.

Read-only, and deliberately so: nothing in this module opens the
database, reads a configuration file, or needs an encryption key. The
commands in front of it are usable on a machine that has none of those.

Importing this module no longer reaches the repository either, which is
a narrower claim than it sounds and is worth stating exactly. The
whole-domain model used to be declared in `store.py`, so this module's
import list pulled SQLAlchemy and cryptography in to reach one class;
it is declared in `models.py` now and this imports it from there. The
`vinga-server config` commands still pay for both, because `cli.py`
imports three pure helpers from `store.py` (the transportability check,
the apply location and the identity splitter) and so loads that module.
It opens no database with them: since #281 removed the break-glass path
the CLI holds no repository, no database opener and no key loader at
all. What the removed edge buys is that the markdown reference and the
JSON Schema
render with nothing loaded but the models and the registry;
`openapi()` is the deliberate exception and says so where it is
defined. `test_config_docgen.py` pins that in a child interpreter, so
the edge cannot come back unnoticed.
"""

import json
import re
import textwrap
from dataclasses import dataclass
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from types import NoneType, UnionType
from typing import Annotated, Literal, Union, get_args, get_origin

from pydantic import BaseModel
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined

from vinga_server.config.entities import (
    API_OPTIONS_NOTE,
    CONFIG_FILE,
    EXAMPLES,
    NESTED,
    PROGRAM,
    SETTINGS,
    DocumentedShape,
    Setting,
)
from vinga_server.config.entities import ENTITIES as COMMANDED
from vinga_server.config.models import DOMAIN_DESCRIPTIONS, DomainConfig
from vinga_server.config.provider_options import declared_options, options_model

# Every shape this document has a section for, in the order it documents
# them: the five entity kinds a command writes, then the two that are
# only ever nested inside one of those. The registries are in
# `entities.py`, which is where a kind's facts live now; this is the
# order they are rendered in, and the name the renderings and their
# tests have always read them under.
ENTITIES: tuple[DocumentedShape, ...] = (*COMMANDED, *NESTED)

# The configuration API's committed document, which sits beside the
# reference in the same directory, so the reference points at it by name.
API_DOCUMENT = "api-openapi.json"

# The domain model, one directory up from the committed reference. This
# document is the authority on the fields; that page is where what they
# mean to a user is explained, and it links back here for exactly that
# division. Relative to `docs/reference/`, where the reference is
# committed.
CONCEPTS = "../concepts.md"

# Where the reference's prose wraps. The tables cannot wrap (a row is a
# line), so only paragraphs go through this.
PROSE_WIDTH = 78

# The whole domain configuration, as opposed to one entity kind.
DOMAIN = "domain"

# The kind whose types declare options of their own, which is the one
# selector `schema` takes three words for and the one section of the
# reference with a tier under it.
PROVIDER = "provider"

# Where a help epilog wraps. Narrower than a terminal, because argparse
# prints this verbatim and a line that wraps on its own is worse than
# one that was wrapped on purpose.
HELP_WIDTH = 78


def entity(name: str) -> DocumentedShape:
    """The entity kind of that name, or a ConfigError naming the ones
    that exist. Kept here rather than in the CLI so the two renderings
    accept exactly the same names."""
    from vinga_server.config.loader import ConfigError

    for candidate in ENTITIES:
        if candidate.name == name:
            return candidate
    raise ConfigError(
        f'"{name}" is not a documented entity; expected one of: ' + ", ".join(entity_names())
    )


def entity_names() -> list[str]:
    return [*(candidate.name for candidate in ENTITIES), DOMAIN]


# JSON Schema


def schema(name: str | None = None, stage: str = "", type_name: str = "") -> str:
    """The JSON Schema of one entity kind, of one provider type's
    options, or of the whole domain configuration when nothing is named.

    The options selector carries the stage as well as the type, and that
    is not verbosity: the registry is keyed by stage first and already
    holds one type name in two stages (`openai` is an ASR type and a TTS
    type, `mock` is all four), so a type name alone addresses nothing in
    particular.

    What answers it is the declaration in
    `config/provider_options.py`, which this module can name at the top
    like any other model source: it is pydantic and nothing else, and
    the child-interpreter pin in `test_config_docgen.py` allows it for
    that reason.
    """
    if stage or type_name:
        return _options_schema(name, stage, type_name)
    model = DomainConfig if name in (None, DOMAIN) else entity(str(name)).model
    return json.dumps(model.model_json_schema(), indent=2, ensure_ascii=False) + "\n"


# What a selector that named no declared model says. The stage and the
# type are not quoted back, the rule every refusal about an identity
# follows (#132): what exists is the useful half, and what was typed is
# the half the person typing it can already see.
NO_SUCH_OPTIONS = (
    "no provider type declares an options model for that stage and type. The ones "
    "that do are: {declared}. Every other type passes its options through, so its "
    "fragment under examples/ is where they are documented"
)

OPTIONS_ARE_A_PROVIDERS = (
    "a stage and a type name one provider type's options, so they go with "
    "`schema provider`, and both of them are needed"
)


def _options_schema(name: str | None, stage: str, type_name: str) -> str:
    """One provider type's options as JSON Schema, which is what a
    client reads before writing the fragment that carries them."""
    from vinga_server.config.loader import ConfigError

    if name != PROVIDER or not stage or not type_name:
        raise ConfigError(OPTIONS_ARE_A_PROVIDERS)
    model = options_model(stage, type_name)
    if model is None:
        declared = ", ".join(
            f"{one} {other}" for one, other, _ in declared_options()
        )
        raise ConfigError(NO_SUCH_OPTIONS.format(declared=declared))
    return json.dumps(model.model_json_schema(), indent=2, ensure_ascii=False) + "\n"


# The OpenAPI document


def openapi() -> str:
    """The configuration API's OpenAPI document, which is the document
    CI diffs the committed copy against.

    The sub-application is imported here rather than at the top of the
    module: these renderings are what a documentation lane runs from a
    plain sync, and there is no reason for `config schema` to pay for
    the API's imports on its way to printing a JSON Schema. Building the
    application opens no database, reads no configuration file and needs
    no key, so the command in front of this stays as read-only as its
    two neighbours.
    """
    from vinga_server.config.api import document

    return json.dumps(document(), indent=2, ensure_ascii=False) + "\n"


# The markdown reference


def reference() -> str:
    """The whole reference document, rendered from the models."""
    lines = [
        "# Domain configuration reference",
        "",
        f"Generated from the pydantic models by `{PROGRAM} reference`.",
        "Do not edit this file by hand: CI regenerates it and fails on any",
        "difference, so an edit here is reverted by the next run. The text of a",
        "field description lives on the model in",
        "`vinga-server/vinga_server/config/models.py`.",
        "",
        "The domain half of the configuration (providers, MCP servers, agent",
        "defaults, agents, devices, the default agent) is held in the server's",
        f"database and written with the `{PROGRAM}` commands. The server",
        "half (`server:` and `memory:`) stays in the YAML file and is documented",
        f"there, in [`config.example.yaml`]({CONFIG_FILE}).",
        "",
        "Those commands are a client of the configuration API the server mounts",
        "at `/api` on its own port, so they need a running server, and the API is",
        "the machine-readable way to write the same entities: a fragment below is",
        "the body of a `PUT`, validated in the same one place whichever way it",
        f"arrived. The API's own contract is [`{API_DOCUMENT}`]({API_DOCUMENT}),",
        "generated from its routes under the same regenerate-and-diff check as",
        "this document. A deployment whose server will not start is recovered by",
        f"booting one on an empty database and applying a kept `{PROGRAM} export`,",
        "which the command reference writes out step by step.",
        "",
        "## How the pieces fit",
        "",
        "Providers and MCP servers are named engines and named tool sources. An",
        "agent is a prompt that references them, one provider per pipeline stage,",
        "falling back to the agent defaults for every stage it does not name. A",
        "device is bound to one or more agents by its MAC address, and a device",
        "with no binding reaches the default agent.",
        "",
        "That order is also the order things have to be written in: a write whose",
        "references do not resolve is refused, so providers and MCP servers come",
        "first, then the agent defaults and the agents, then the device bindings",
        "and the default agent.",
        "",
        "What those nouns mean to a user, and the decided semantics behind them,",
        f"are on [the domain concepts page]({CONCEPTS}); this document is the",
        "authority on the fields themselves.",
        "",
        "## Writing an entity",
        "",
        "Each entity kind is written from a YAML fragment holding one entity's",
        "body, with the entity's name given as an argument rather than in the",
        "document. Commented examples live in",
        f"[`vinga-server/examples/`]({EXAMPLES}/);",
        "each file names the command that installs it.",
        "",
        "A fragment never holds a credential. A secret-bearing key names the",
        "environment variable holding the value (`api_key_env` on a provider,",
        "`$NAME` in an MCP server's `env` or `headers`), and the models refuse",
        "anything else. The other form is a value encrypted in the database,",
        f"written with `{PROGRAM} <kind> secret set`, which reads it from stdin",
        "or from a named variable and never from an argument. A stored secret takes",
        "precedence over an environment reference for the same slot.",
        "",
        "`set` replaces an entity whole and leaves its stored secrets alone. When",
        "a change reaches a running server depends on the kind, and every write",
        "says which of the cases below it is in.",
        "",
        "One part of the configuration is read once at start and served until",
        "the next one, and it is the server section, which is a file this",
        "process never re-reads: the port, the directories, the limits, the",
        "barge-in tuning. Nothing in this database is in that part. What a",
        "running server is serving of the stored half is a generation, an",
        "immutable snapshot validated whole, and applying a change installs the",
        "next one rather than editing the one in place.",
        "",
        "Device bindings are the first way a change reaches it. A running",
        "server re-reads the `devices` map and `default_agent` as a device asks",
        "for them, so binding a device, unbinding it, or changing the default",
        "agent applies at that device's next OTA check or connection, with",
        "nothing asked of the server at all. The exception ends where the agent",
        "does: a binding naming an agent this server is not serving resolves to",
        "nothing until the reload that installs it.",
        "",
        "The reload is the second, and unlike the first it is asked for rather",
        f"than noticed. `{PROGRAM} reload` has a running server re-read",
        "the stored configuration and apply the whole domain half: the",
        "`providers` entries and the `mcp_servers` entries with the",
        "secrets stored on them, the",
        "agents' effective `mcp` grant lists, the prompt fragments, the agents",
        "themselves and the `agent_defaults` layer under them. Entries are",
        "started,",
        "restarted, stopped or left alone, and no conversation is dropped. When",
        "one meets the result depends on which half moved: the tools an agent",
        "may reach are snapshotted per reply, so an entry that moved is picked",
        "up on the next utterance, while prompt text is assembled at an",
        "activation and cached for it, so a rewritten prompt, fragment or",
        "`instructions` reaches a conversation at its next activation, which is",
        "a new session or an agent switch. Filled pauses are synthesized during",
        "the reload and bound by a conversation when it opens, so an edited",
        "filler section reaches the next conversation and never changes what",
        "one already open is masking with. An agent is synthesized again when",
        "any field of its effective `filler` section moved or when the voice",
        "that speaks it did, and its clips are carried over as they are",
        "otherwise: the whole section is the unit of comparison, so an edit to",
        "`delay_ms` alone is a round of text-to-speech work at the configured",
        "provider even though the audio it produces is identical, and",
        "rewriting the provider entry an agent speaks through is another,",
        "since that is the voice moving; an edit that reaches neither, a",
        "prompt or a fragment, is none. An agent whose",
        "synthesis fails runs unmasked rather than making the reload refuse.",
        "",
        "The engines keep the same clock as the clips and cost what they cost.",
        "An entry whose definition and stored credential have not moved is",
        "carried into the new world as the object it already was, so editing a",
        "prompt reloads no model; a rewritten entry is built while the old one",
        "is still serving, and the conversations that open after the apply speak",
        "through the new one. An entry a conversation is still speaking through",
        "is released when that conversation ends, so applying a change to a",
        "local model briefly holds two of it, and an entry that will not build",
        "refuses the reload with nothing changed.",
        "",
        "The agent set moves with the rest. An agent the store has added is",
        "one a device can be bound to and reach at its next check-in, with no",
        "restart between the write and the board; an agent it has deleted is",
        "one no session can be opened as from the moment the apply answers,",
        "while a conversation already talking as it finishes on the world it",
        "was built from and is served that world's prompt to the end. The one",
        "thing an agent carries that a reload does not move is its memory,",
        "which is keyed by its name and stays where its name left it.",
        "",
        f"`{PROGRAM} schema [entity]` prints the same field descriptions",
        "as JSON Schema, which is what a machine reads before writing a fragment.",
        "The API's document carries the same schemas under `components`, where a",
        "client that has read an entity back finds what a write of it may carry.",
        "",
        "## Entities",
        "",
    ]

    for candidate in ENTITIES:
        lines += _entity_section(candidate)
        # The one kind with a tier under it, which is what a `type` key
        # means: the entry above documents every provider, and the
        # sections below document what each declared type accepts.
        if candidate.name == PROVIDER:
            lines += _options_sections()

    lines += ["## Domain-level settings", ""]
    for setting in SETTINGS:
        lines += _setting_section(setting)

    lines += [
        "## The whole domain configuration",
        "",
        "What one deployment's domain half holds, which is what",
        f"`{PROGRAM} show` prints and what a running server serves once it has",
        "been asked to apply it.",
        "",
        *_table(DomainConfig),
        "",
    ]
    return "\n".join(lines).rstrip("\n") + "\n"


# The provider's second tier
#
# Every other kind is one model and one table. A provider is a model
# whose remaining keys belong to whatever its `type` names, so the kinds
# above cannot describe it on their own, and the types that declare an
# options model are documented here, under the kind they are a type of.
#
# Grouped by stage and then by type because that is how one is
# addressed: `providers.<stage>.<name>` with a `type`, and the same
# stage-then-type pair the schema command takes. Rendered recursively,
# so a nested section's own fields appear rather than only the name of
# the section: a reader looking for `min_silence_duration_ms` finds it
# here, which is the whole difference between documenting a contract and
# naming it.


def _options_sections() -> list[str]:
    """One subsection per typed provider type, in the declaration's own
    order."""
    lines: list[str] = []
    for stage, type_name, model in declared_options():
        lines += [
            f"#### `{stage}` options for `type: {type_name}`",
            "",
            f"`providers.{stage}.<name>`",
            "",
            *_paragraph(_lead(model)),
            "",
            *_options_tables(model),
        ]
    return lines


def _options_tables(model: type[BaseModel], parent: str = "") -> list[str]:
    """One model's fields as a table, and then a table per nested model
    under it.

    Depth-first and after the parent, so a reader meets the section
    before its contents. The nesting is named by the field path rather
    than by the nested class, since what a fragment writes is the path.
    """
    lines = [*_table(model), ""]
    for name, info in model.model_fields.items():
        nested = _nested_model(info.annotation)
        if nested is None:
            continue
        below = f"{parent}{name}"
        lines += [f"Fields of `{below}`:", ""]
        lines += _options_tables(nested, f"{below}.")
    return lines


def _nested_model(annotation: object) -> type[BaseModel] | None:
    """The model a field holds one of, or None for a field that holds a
    value. A union is looked through, so an optional section is found the
    way a required one is."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    if get_origin(annotation) in (Union, UnionType, Annotated):
        for argument in get_args(annotation):
            found = _nested_model(argument)
            if found is not None:
                return found
    return None


def _lead(model: type[BaseModel]) -> str:
    """A model's docstring down to its first paragraph, on one line.

    The whole docstring is what the JSON Schema carries, and it runs to
    the reasoning behind a default; what belongs in a reference table's
    heading is the sentence that says what the thing is."""
    text = (model.__doc__ or "").strip().split("\n\n")[0]
    return " ".join(text.split())


def _entity_section(candidate: DocumentedShape) -> list[str]:
    lines = [
        f"### {candidate.title}",
        "",
        f"`{candidate.location}`",
        "",
        *_paragraph(candidate.purpose),
        "",
    ]
    if candidate.command:
        lines += ["```bash", candidate.command, "```", ""]
    lines += _table(candidate.model)
    lines.append("")
    for note in candidate.notes:
        lines += [*_paragraph(note), ""]
    if candidate.examples:
        lines.append("Examples:")
        lines.append("")
        lines += [f"- [`{name}`]({EXAMPLES}/{name})" for name in candidate.examples]
        lines.append("")
    return lines


def _setting_section(setting: Setting) -> list[str]:
    lines = [
        f"### {setting.title}",
        "",
        f"`{setting.name}`",
        "",
        *_paragraph(DOMAIN_DESCRIPTIONS[setting.name]),
        "",
        "```bash",
        setting.command,
        "```",
        "",
    ]
    for note in setting.notes:
        lines += [*_paragraph(note), ""]
    return lines


def _paragraph(text: str) -> list[str]:
    """One paragraph, wrapped. The committed reference is read as a file
    as often as it is rendered, and an unwrapped paragraph makes every
    edit to it a one-line diff of the whole thing.

    Never inside a word or across a hyphen: the default would break
    `aa-bb-cc-dd-ee-ff` in half, and a code span split over two lines
    renders with a space in the middle of the MAC address."""
    return textwrap.wrap(text, width=PROSE_WIDTH, break_long_words=False, break_on_hyphens=False)


def _table(model: type[BaseModel]) -> list[str]:
    """One model's fields, in declaration order."""
    rows = [
        "| Field | Type | Default | Description |",
        "| --- | --- | --- | --- |",
    ]
    rows += [
        f"| `{name}` | `{_cell(type_name(info.annotation))}` | `{default(info)}` | "
        f"{_cell(info.description)} |"
        for name, info in model.model_fields.items()
    ]
    if model.model_config.get("extra") == "allow":
        rows.append("| ... | | | Passed through to the provider implementation. |")
    return rows


def _cell(text: str | None) -> str:
    """Text inside a table cell. A pipe ends a cell even inside a code
    span, so a union type has to be escaped; and a missing description
    is a defect this makes visible rather than an empty cell to skim
    past."""
    if not text:
        return "**(undescribed)**"
    return text.replace("|", "\\|")


# Rendering types and defaults


def type_name(annotation: object) -> str:
    """A field's type as a person reads it, rather than as `typing`
    prints it: the constrained-string wrapper and the pydantic metadata
    say nothing a reader of the reference wants."""
    origin = get_origin(annotation)
    if origin is Annotated:
        return type_name(get_args(annotation)[0])
    if origin is Literal:
        return " | ".join(json.dumps(value) for value in get_args(annotation))
    if origin in (Union, UnionType):
        return " | ".join(type_name(argument) for argument in get_args(annotation))
    if origin is not None:
        arguments = ", ".join(type_name(argument) for argument in get_args(annotation))
        name = getattr(origin, "__name__", str(origin))
        return f"{name}[{arguments}]" if arguments else name
    if annotation is NoneType:
        return "null"
    if isinstance(annotation, type):
        return annotation.__name__
    return str(annotation)


def default(info: FieldInfo) -> str:
    """What a field holds when a fragment omits it."""
    if info.default_factory is not None:
        return _value(info.default_factory())  # type: ignore[call-arg]
    if info.default is PydanticUndefined:
        return "required"
    return _value(info.default)


def _value(value: object) -> str:
    if isinstance(value, BaseModel):
        # An empty nested model is "nothing set", which is what an empty
        # mapping says in the shape a fragment is written in.
        return "{}"
    try:
        return json.dumps(value)
    except TypeError:
        return str(value)


# The CLI's own help text


def fragment_help(name: str) -> str:
    """The fields a fragment for this entity may carry, for the epilog
    of the command that takes one. Generated rather than written, so the
    help and the reference cannot disagree.

    Three things per field, which are the three a person writing a
    fragment has to know: what the key is called, what it may hold, and
    what it holds when the fragment leaves it out. The first two columns
    are the reference's own `Type` and `Default` cells, computed by the
    same two functions above, so a help page and a table row cannot come
    to describe one field differently. The description under them is its
    first sentence, which is what fits; the whole of it is one
    `config schema` away, and the last line says so.
    """
    candidate = entity(name)
    lines = [f"fragment fields for {candidate.title.lower()} ({candidate.location}):", ""]
    lines += _help_fields(candidate.model)
    if candidate.name == PROVIDER:
        for stage, type_name_, model in declared_options():
            lines += ["", f"options for {stage} type {type_name_}:", ""]
            lines += _help_fields(model)
    if candidate.model.model_config.get("extra") == "allow":
        lines += [
            "",
            "Any other key is an option for a type that declares none of its own;",
            "see vinga-server/examples/ for those types' options.",
        ]
    lines += [
        "",
        f"Full descriptions: {PROGRAM} schema " + candidate.name,
    ]
    return "\n".join(lines)


def _help_fields(model: type[BaseModel], prefix: str = "") -> list[str]:
    """One model's fields as the epilog lists them, and then the fields
    of every model nested under it.

    Recursive for the reason the reference's tables are: a section
    listed by name alone tells a reader a mapping goes there and not
    what may go in it. A nested field is written at its own path, since
    that is how a fragment writes it.
    """
    lines: list[str] = []
    for field_name, info in model.model_fields.items():
        given = default(info)
        held = "required" if given == "required" else f"default: {given}"
        lines.append(f"  {prefix}{field_name}: {type_name(info.annotation)}  ({held})")
        lines += textwrap.wrap(
            _sentence(info.description),
            width=HELP_WIDTH,
            initial_indent="    ",
            subsequent_indent="    ",
            break_long_words=False,
            break_on_hyphens=False,
        )
    for field_name, info in model.model_fields.items():
        nested = _nested_model(info.annotation)
        if nested is not None:
            lines += _help_fields(nested, f"{prefix}{field_name}.")
    return lines


def _sentence(description: str | None) -> str:
    """The first sentence of a description, which is what fits on a help
    line. The descriptions are written so that the first sentence is the
    one that has to be there."""
    if not description:
        return "(undescribed)"
    head, separator, _ = description.partition(". ")
    return head + separator.strip()


# The recipes
#
# A recipe is the sequence of commands one topic is written with, and
# every line of one is read out of an example file rather than typed
# beside it. The files already name their own commands: each fragment
# quotes the `set` that installs it, each preset quotes the `apply` that
# writes it whole, and the ones that can hold a credential quote the
# `secret set` that fills the slot. Those quoted lines are the recipes,
# collected and grouped, so a recipe cannot come to name a file that
# moved or an entity name the file no longer uses.
#
# What this reads is a directory rather than the models, which is the
# one thing separating it from everything above.
#
# The build carries that directory into the package, so an installed
# artifact renders the same recipes a checkout does. The installed copy
# is what this prefers and a checkout is the fallback, which is the
# order that makes the shipped command the one being exercised: a wheel
# missing its fragments has to fail rather than quietly find the tree it
# was built from. Neither is a second home. `vinga-server/examples/` is
# the directory anybody edits, and the packaged copy is derived from it
# at build time the way the package itself is derived from `src/`.

# Where the example fragments are, in the two places an installation can
# put them: inside the package where the build put them, or beside
# `src/` where a checkout keeps them.
_PACKAGED = "examples"


def _example_dir() -> Traversable:
    """The directory the recipes are read out of."""
    packaged = resources.files("vinga_server") / _PACKAGED
    if packaged.is_dir():
        return packaged
    return Path(__file__).resolve().parents[3] / _PACKAGED


EXAMPLE_DIR: Traversable = _example_dir()

# The subdirectory holding complete apply documents rather than one
# entity's fragment. A tier of its own: a preset is owned by the shape
# of the document it is, not by any one descriptor, which is why no
# entity claims one.
PRESET_DIR = "presets"

MISSING_EXAMPLES = (
    "this installation carries no example fragments, so the command recipes cannot be "
    "rendered; they are packaged with the server, and an installation without them is "
    "incomplete rather than configured"
)

# What a rendering that cannot read one of its own sources says.
#
# Three categories rather than one, because they are three different
# things to go and fix, and each names the file, which is a filename the
# registry owns rather than anything a person typed at this command.
#
# What is never in one is a word off the exception. An `OSError` carries
# the operating system's own message and a `UnicodeDecodeError` carries
# the bytes it choked on and, on its `object` attribute, the whole
# buffer it was decoding. A file under `examples/` is a file somebody
# edits, so its bytes are as good a place for a credential pasted where
# a variable name belongs as a fragment is, and this module is on the
# same side of that boundary as every other refusal in this package.
MISSING_EXAMPLE = "the recipes name {name}, and there is no such file under examples/"

UNREADABLE_EXAMPLE = "the recipes name {name}, under examples/, which cannot be read"

UNDECODABLE_EXAMPLE = "{name}, under examples/, is not valid UTF-8 and was not read"

# And what an example quoting a command with no topic says. The file is
# named and the command is not, for the reason above: a quoted line is
# the one part of an example this module reads as input, and echoing the
# word back is how a credential typed into a quoted command block would
# reach a stream.
UNKNOWN_TOPIC = (
    "{name}, under examples/, quotes a command line no recipe has a heading for; "
    "every command an example quotes is published as a recipe, so a verb this has "
    "not seen before needs a topic of its own"
)

# How an example quotes a command of its own: an indented comment line.
# That indentation is the whole of the rule, and it is what tells a
# block meant to be copied from a sentence that happens to mention a
# command. Every one of these files already writes them this way.
_QUOTED = re.compile(r"^#   (\S.*)$")

# What a recipe says about the topic it writes, for the two topics that
# have no descriptor to say it: the settings that are written from
# arguments rather than from a document, and the credentials that are
# never written into one at all.
_PRESET_TOPIC = (
    "A whole deployment in one document: every entity it names, in one transaction, "
    "refused whole if anything in it will not resolve. This is the shortest path from "
    "an empty database to a server with something to say."
)

_DEVICE_TOPIC = (
    "Which board reaches which agent, which is the one thing a preset cannot know. A "
    "binding applies at that device's next check-in rather than at a reload."
)

_SECRET_TOPIC = (
    "A credential encrypted in the database, which never puts it in a file at all. The "
    "value is read from stdin, or from the variable --from-env names, and never from an "
    "argument. A stored secret wins over an environment reference written for the same "
    "slot."
)

# Which topic a command belongs to, by the words it starts with. The
# entity topics are not here: a `set <kind>` line names its own kind,
# which is the registry's key for it, so those are looked up rather than
# listed. Everything else is one of these three, and a command that is
# none of them is a file quoting something this has never rendered,
# which is a refusal rather than a line dropped on the floor.
# The commands that are not one kind's own write, as the words that name
# them: a noun path and a verb, or one flat verb. Matched by longest
# prefix, because the tree is not one depth and `device pending claim`
# and `device bind` are the same topic reached at two.
_TOPIC_COMMANDS: dict[tuple[str, ...], str] = {
    ("apply",): PRESET_DIR,
    ("device", "bind"): "devices",
    ("device", "pending", "claim"): "devices",
    ("default-agent", "set"): "devices",
    ("default-agent", "clear"): "devices",
    ("provider", "secret", "set"): "secrets",
    ("mcp-server", "secret", "set"): "secrets",
}


@dataclass(frozen=True)
class Recipe:
    """One topic's commands, in the order they are run in."""

    # The heading a reader meets it under.
    title: str

    # Where in the configuration document the topic lives, or the empty
    # string for the topics that are not one section of it.
    location: str

    # What the topic is, in a paragraph.
    purpose: str

    # The command lines, program name and all, deduplicated and in the
    # order the files quote them.
    commands: tuple[str, ...]


def recipes() -> tuple[Recipe, ...]:
    """Every topic's commands, read out of the example files.

    The order is the order the whole list runs in against an empty
    database: the presets first, then the entity kinds in the registry's
    own order, then the bindings that need an agent to point at, then
    the credentials that need an entry to sit on. That is not a second
    creation order to keep in step with anything: it is the registry's
    order, with the two topics that have no descriptor at the end
    because both of them reference what the ones above create.

    The program name is the canonical constant and not an argument.
    Rendering the same page under two names would publish two documents
    from one tree, and the prefix this matches against the example files
    has to be the prefix they carry: a mismatch renders an empty recipes
    region and turns the drift check red on an unrelated change.
    """
    quoted = _quoted(PROGRAM)
    collected: dict[str, list[str]] = {}
    for source, argv in quoted:
        collected.setdefault(_topic(source, argv), []).append(f"{PROGRAM} {argv}")
    return tuple(
        Recipe(
            title=title,
            location=location,
            purpose=purpose,
            commands=tuple(dict.fromkeys(collected.get(key, ()))),
        )
        for key, title, location, purpose in _TOPICS
        if collected.get(key)
    )


# The topics, in the order a reader meets them and the order the whole
# list runs in. Derived from the registry for the tier that has one, so
# a new entity kind with an example file gets a recipe by existing.
_TOPICS: tuple[tuple[str, str, str, str], ...] = (
    (PRESET_DIR, "A whole deployment", "", _PRESET_TOPIC),
    # The first sentence of a kind's purpose, which is the part that
    # says what the topic is; the whole of it, and every field of it, is
    # one page away in the generated reference, which is what a recipes
    # section should not be a second copy of.
    *(
        (candidate.name, candidate.title, candidate.location, _sentence(candidate.purpose))
        for candidate in COMMANDED
    ),
    ("devices", "Devices and the default agent", "devices, default_agent", _DEVICE_TOPIC),
    ("secrets", "Stored credentials", "", _SECRET_TOPIC),
)


def _quoted(program: str) -> list[tuple[Traversable, str]]:
    """Every command an example file quotes, as (file, arguments), in
    the order the recipes run them in.

    The presets first because a preset writes the entities the fragments
    then replace one at a time, and the fragments in the registry's
    order because that is the order their references resolve in.
    """
    if not EXAMPLE_DIR.is_dir():
        from vinga_server.config.loader import ConfigError

        raise ConfigError(MISSING_EXAMPLES)
    files = [
        *_documents(EXAMPLE_DIR / PRESET_DIR),
        *(
            EXAMPLE_DIR / filename
            for candidate in COMMANDED
            for filename in candidate.examples
        ),
    ]
    prefix = f"{program} "
    return [
        (path, matched.group(1).removeprefix(prefix))
        for path in files
        for line in _read(path).splitlines()
        if (matched := _QUOTED.match(line)) and matched.group(1).startswith(prefix)
    ]


def _documents(directory: Traversable) -> list[Traversable]:
    """The YAML files in one directory, by name.

    Listed and filtered rather than globbed, and sorted by name rather
    than by the entry itself, because what this walks is a resource
    directory: `Traversable` is the contract an installed package
    answers, and it has neither a glob nor an ordering of its own.
    Sorting is what makes the rendering deterministic, which is the
    whole reason the committed page can be diffed.
    """
    if not directory.is_dir():
        return []
    return sorted(
        (entry for entry in directory.iterdir() if entry.name.endswith(".yaml")),
        key=lambda entry: entry.name,
    )


def _read(path: Traversable) -> str:
    """One example file's text, or the fixed sentence for one that will
    not read.

    Recorded inside the handler and raised after it, the rule every
    boundary in this package raises by: an exception raised while
    another is being handled keeps that one on `__context__` for
    anything walking the chain to find, and the two families caught here
    are exactly the two that carry the file's contents. What survives
    the arm is a string built from `path.name`, which is the registry's.

    `FileNotFoundError` before the `OSError` it is a subclass of, since
    a file the registry names and nobody wrote is a different mistake
    from one the process may not read. The docgen suite already holds
    every named example to being a file that exists, so this is the belt
    under that: a rendering command must not answer with a traceback
    because a working tree lost a file between two runs.
    """
    from vinga_server.config.loader import ConfigError

    problem: str | None = None
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        problem = MISSING_EXAMPLE.format(name=path.name)
    except OSError:
        problem = UNREADABLE_EXAMPLE.format(name=path.name)
    except UnicodeError:
        problem = UNDECODABLE_EXAMPLE.format(name=path.name)
    raise ConfigError(problem)


def _topic(source: Traversable, argv: str) -> str:
    """Which topic one quoted command belongs to.

    Read off the command itself rather than off the file it was found
    in, because a fragment quotes the `secret set` that fills its slot
    as readily as the `set` that installs it, and the two belong under
    different headings.
    """
    from vinga_server.config.loader import ConfigError

    words = tuple(argv.split())
    if words[1:2] == ("set",):
        for candidate in COMMANDED:
            if candidate.name == words[0]:
                return candidate.name
    for length in range(min(len(words), 3), 0, -1):
        topic = _TOPIC_COMMANDS.get(words[:length])
        if topic is not None:
            return topic
    raise ConfigError(UNKNOWN_TOPIC.format(name=source.name))


def recipe_lines() -> list[str]:
    """The recipes as the markdown they are published as."""
    lines: list[str] = []
    for recipe in recipes():
        lines += [f"### {recipe.title}", ""]
        if recipe.location:
            lines += [f"`{recipe.location}`", ""]
        lines += [*_paragraph(recipe.purpose), "", "```bash", *recipe.commands, "```", ""]
    return lines


__all__ = [
    # Re-exported from `entities`, where the two renderings of the
    # provider-options contract live now: `api.py` reads the second one
    # from here, which is the import it has always had.
    "API_OPTIONS_NOTE",
    "DOMAIN",
    "ENTITIES",
    "Recipe",
    "entity",
    "entity_names",
    "fragment_help",
    "openapi",
    "recipe_lines",
    "recipes",
    "reference",
    "schema",
]
