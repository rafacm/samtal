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
"""

import json
import textwrap
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
    SETTINGS,
    DocumentedShape,
    Setting,
)
from vinga_server.config.entities import ENTITIES as COMMANDED
from vinga_server.config.models import DOMAIN_DESCRIPTIONS
from vinga_server.config.store import DomainConfig

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

# Where the reference's prose wraps. The tables cannot wrap (a row is a
# line), so only paragraphs go through this.
PROSE_WIDTH = 78

# The whole domain configuration, as opposed to one entity kind.
DOMAIN = "domain"

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


def schema(name: str | None = None) -> str:
    """The JSON Schema of one entity kind, or of the whole domain
    configuration when nothing is named."""
    model = DomainConfig if name in (None, DOMAIN) else entity(str(name)).model
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
        "Generated from the pydantic models by `vinga-server config reference`.",
        "Do not edit this file by hand: CI regenerates it and fails on any",
        "difference, so an edit here is reverted by the next run. The text of a",
        "field description lives on the model in",
        "`vinga-server/vinga_server/config/models.py`.",
        "",
        "The domain half of the configuration (providers, MCP servers, agent",
        "defaults, agents, devices, the default agent) is held in the server's",
        "database and written with the `vinga-server config` commands. The server",
        "half (`server:` and `memory:`) stays in the YAML file and is documented",
        f"there, in [`config.example.yaml`]({CONFIG_FILE}).",
        "",
        "Those commands are a client of the configuration API the server mounts",
        "at `/api` on its own port, so they need a running server, and the API is",
        "the machine-readable way to write the same entities: a fragment below is",
        "the body of a `PUT`, validated in the same one place whichever way it",
        f"arrived. The API's own contract is [`{API_DOCUMENT}`]({API_DOCUMENT}),",
        "generated from its routes under the same regenerate-and-diff check as",
        "this document. `vinga-server config --local` writes the database",
        "directly for the recovery subset (`show`, `delete`, `clear-secret`,",
        "`set-secret`), which is the way in when the server will not start.",
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
        "written with `vinga-server config set-secret`, which reads it from stdin",
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
        "than noticed. `vinga-server config reload` has a running server re-read",
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
        "`vinga-server config schema [entity]` prints the same field descriptions",
        "as JSON Schema, which is what a machine reads before writing a fragment.",
        "The API's document carries the same schemas under `components`, where a",
        "client that has read an entity back finds what a write of it may carry.",
        "",
        "## Entities",
        "",
    ]

    for candidate in ENTITIES:
        lines += _entity_section(candidate)

    lines += ["## Domain-level settings", ""]
    for setting in SETTINGS:
        lines += _setting_section(setting)

    lines += [
        "## The whole domain configuration",
        "",
        "What one deployment's domain half holds, which is what `vinga-server",
        "config show` prints and what the server loads at boot.",
        "",
        *_table(DomainConfig),
        "",
    ]
    return "\n".join(lines).rstrip("\n") + "\n"


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
    help and the reference cannot disagree."""
    candidate = entity(name)
    width = max(len(field_name) for field_name in candidate.model.model_fields)
    lines = [f"fragment fields for {candidate.title.lower()} ({candidate.location}):", ""]
    for field_name, info in candidate.model.model_fields.items():
        lead = f"  {field_name.ljust(width)}  "
        lines += textwrap.wrap(
            _sentence(info.description),
            width=HELP_WIDTH,
            initial_indent=lead,
            subsequent_indent=" " * len(lead),
            break_long_words=False,
            break_on_hyphens=False,
        )
    if candidate.model.model_config.get("extra") == "allow":
        lines += [
            "",
            "Any other key is an option for the provider implementation; see",
            "vinga-server/examples/ for each type's options.",
        ]
    lines += [
        "",
        "Full descriptions: vinga-server config schema " + candidate.name,
    ]
    return "\n".join(lines)


def _sentence(description: str | None) -> str:
    """The first sentence of a description, which is what fits on a help
    line. The descriptions are written so that the first sentence is the
    one that has to be there."""
    if not description:
        return "(undescribed)"
    head, separator, _ = description.partition(". ")
    return head + separator.strip()


__all__ = [
    # Re-exported from `entities`, where the two renderings of the
    # provider-options contract live now: `api.py` reads the second one
    # from here, which is the import it has always had.
    "API_OPTIONS_NOTE",
    "DOMAIN",
    "ENTITIES",
    "entity",
    "entity_names",
    "fragment_help",
    "openapi",
    "reference",
    "schema",
]
