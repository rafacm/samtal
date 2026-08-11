"""The domain configuration's documentation, rendered from the models.

Three renderings, one source. `Field(description=...)` on the domain
models is written once and read here as JSON Schema (what an API client
or an agent reads before writing a fragment), as the markdown reference
committed at `docs/reference/domain-config.md`, and as the `--help`
text of the commands that take a fragment. A description that is only
true in one of them cannot exist, which is the point.

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
from dataclasses import dataclass, field
from types import NoneType, UnionType
from typing import Annotated, Literal, Union, get_args, get_origin

from pydantic import BaseModel
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined

from samtal_server.config.models import (
    DOMAIN_DESCRIPTIONS,
    AgentConfig,
    AgentDefaults,
    FillerConfig,
    McpServerConfig,
    ProviderConfig,
)
from samtal_server.config.store import DomainConfig

# Where the example fragments and the configuration file live, relative
# to the committed reference (docs/reference/domain-config.md). Printed
# as written when the same document goes to stdout.
EXAMPLES = "../../samtal-server/examples"
CONFIG_FILE = "../../samtal-server/config.example.yaml"

# Where the reference's prose wraps. The tables cannot wrap (a row is a
# line), so only paragraphs go through this.
PROSE_WIDTH = 78

# The whole domain configuration, as opposed to one entity kind.
DOMAIN = "domain"

# Where a help epilog wraps. Narrower than a terminal, because argparse
# prints this verbatim and a line that wraps on its own is worse than
# one that was wrapped on purpose.
HELP_WIDTH = 78

# What schema generation cannot describe, and where it is described
# instead. A provider entry passes every key beyond the declared ones
# through to its implementation (`extra="allow"`), so no schema can
# enumerate them until typed option models land.
OPTIONS_NOTE = (
    "A provider entry carries whatever options its `type` takes, and those are "
    "passed through rather than declared, so no schema can list them. Until typed "
    "option models land (#88) they are documented in the example fragments below, "
    "which is also where the measured numbers behind each default are kept."
)


@dataclass(frozen=True)
class Entity:
    """One entity kind: its model, and the prose no model can carry."""

    name: str
    title: str
    location: str
    model: type[BaseModel]
    purpose: str
    # The command that writes one, or None for a shape that is only ever
    # nested inside another entity.
    command: str | None = None
    examples: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    # Fragment-shaped commands list their fields in `--help`; a nested
    # shape has no command to list them on.
    fields_in_help: bool = field(default=True)


ENTITIES: tuple[Entity, ...] = (
    Entity(
        name="provider",
        title="Provider",
        location="providers.<stage>.<name>",
        model=ProviderConfig,
        purpose=(
            "One engine, named so agents can reference it. Providers are grouped by "
            "the pipeline stage they serve (llm, asr, tts, vad), and an entry is "
            "addressed by that stage and its name together: two stages may hold the "
            "same name. A voice is a provider entry, so two agents that should sound "
            "different reference two entries."
        ),
        command="samtal-server config set provider <stage> <name> -f fragment.yaml",
        examples=(
            "llm-anthropic.yaml",
            "llm-openai-compatible.yaml",
            "asr-faster-whisper.yaml",
            "asr-openai.yaml",
            "tts-piper.yaml",
            "tts-elevenlabs.yaml",
            "tts-openai.yaml",
            "vad-silero.yaml",
        ),
        notes=(OPTIONS_NOTE,),
    ),
    Entity(
        name="mcp-server",
        title="MCP server",
        location="mcp_servers.<name>",
        model=McpServerConfig,
        purpose=(
            "One MCP server, named so agents can reference it. The name becomes the "
            "prefix its tools are offered to the model under (`home__turn_on_light`), "
            "so it must match `[A-Za-z0-9_-]+` and must not be one of the names the "
            "merged tool list already uses. A server that is down at startup only "
            "logs a warning: its tools are absent, and it reconnects in the "
            "background when a session needs it."
        ),
        command="samtal-server config set mcp-server <name> -f fragment.yaml",
        examples=("mcp-server-stdio.yaml", "mcp-server-streamable-http.yaml"),
    ),
    Entity(
        name="agent",
        title="Agent",
        location="agents.<name>",
        model=AgentConfig,
        purpose=(
            "One persona: a prompt, plus whichever stages it overrides. Every stage "
            "must resolve to a provider, on the agent or through agent_defaults, for "
            "the server to start, so a typical agent is a prompt and a voice."
        ),
        command="samtal-server config set agent <name> -f fragment.yaml",
        examples=("agent.yaml",),
        notes=(
            "An agent's name is also the key its remembered facts are stored under, "
            "so renaming an agent orphans its memory: the old file stays on disk and "
            f"the renamed agent starts empty. The `memory:` section in "
            f"[`config.example.yaml`]({CONFIG_FILE}) says what to do about it.",
        ),
    ),
    Entity(
        name="agent-defaults",
        title="Agent defaults",
        location="agent_defaults",
        model=AgentDefaults,
        purpose=(
            "What every agent uses unless it names something else. One entry for the "
            "whole deployment, and deliberately without a prompt: a prompt is what "
            "makes an agent that agent, and inheriting one silently would make two "
            "agents the same one."
        ),
        command="samtal-server config set agent-defaults -f fragment.yaml",
        examples=("agent-defaults.yaml",),
        notes=(
            "This entry is a singleton. There is one of it, writing it replaces it "
            "whole, and it is not keyed by anything. Per-family defaults are a later "
            "change, and re-keying the table is what it will do.",
            "An agent that names no provider for a stage inherits this entry's "
            "provider for that stage. A list field replaces rather than extends: an "
            "agent naming `mcp` names all of its MCP servers, and `mcp: []` opts it "
            "out of the tools its siblings have. A `filler` section behaves the same "
            "way, replacing this one wholly rather than merging with it.",
        ),
    ),
    Entity(
        name="filler",
        title="Filler",
        location="agent_defaults.filler, agents.<name>.filler",
        model=FillerConfig,
        purpose=(
            "Masking reply latency with a pre-synthesized filled pause. Nested inside "
            "an agent or the agent defaults rather than written on its own, and off "
            "unless it says otherwise. The phrases are synthesized in the agent's own "
            "voice at boot and cached, so the clip costs nothing at the moment it "
            "masks and keeps working when the TTS provider is the thing being slow."
        ),
        fields_in_help=False,
    ),
)

# The domain-level fields that are not entities of their own: a mapping
# and a scalar, written with their own commands rather than a fragment.
SETTINGS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    (
        "devices",
        "Devices",
        "samtal-server config bind-device <mac> <agent> [<agent> ...]",
        (
            "A MAC is stored in its canonical form (lowercase, colon separated), so "
            "`AA-BB-CC-DD-EE-FF` and `aa:bb:cc:dd:ee:ff` are the same device.",
            "`samtal-server config delete device <mac>` removes a binding.",
        ),
    ),
    (
        "default_agent",
        "Default agent",
        "samtal-server config set-default-agent <name>",
        (
            "`samtal-server config clear-default-agent` unsets it, which is a "
            "configuration rather than a mistake: the devices map is then the "
            "allowlist.",
            "It is required only when agents are defined and no device is bound to "
            "one, and that rule is checked at boot rather than at write time, so a "
            "deployment can be built up in the natural order without wedging.",
        ),
    ),
)


def entity(name: str) -> Entity:
    """The entity kind of that name, or a ConfigError naming the ones
    that exist. Kept here rather than in the CLI so the two renderings
    accept exactly the same names."""
    from samtal_server.config.loader import ConfigError

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


# The markdown reference


def reference() -> str:
    """The whole reference document, rendered from the models."""
    lines = [
        "# Domain configuration reference",
        "",
        "Generated from the pydantic models by `samtal-server config reference`.",
        "Do not edit this file by hand: CI regenerates it and fails on any",
        "difference, so an edit here is reverted by the next run. The text of a",
        "field description lives on the model in",
        "`samtal-server/samtal_server/config/models.py`.",
        "",
        "The domain half of the configuration (providers, MCP servers, agent",
        "defaults, agents, devices, the default agent) is held in the server's",
        "database and written with the `samtal-server config` commands. The server",
        "half (`server:` and `memory:`) stays in the YAML file and is documented",
        f"there, in [`config.example.yaml`]({CONFIG_FILE}).",
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
        f"[`samtal-server/examples/`]({EXAMPLES}/);",
        "each file names the command that installs it.",
        "",
        "A fragment never holds a credential. A secret-bearing key names the",
        "environment variable holding the value (`api_key_env` on a provider,",
        "`$NAME` in an MCP server's `env` or `headers`), and the models refuse",
        "anything else. The other form is a value encrypted in the database,",
        "written with `samtal-server config set-secret`, which reads it from stdin",
        "or from a named variable and never from an argument. A stored secret takes",
        "precedence over an environment reference for the same slot.",
        "",
        "`set` replaces an entity whole and leaves its stored secrets alone. A",
        "change takes effect at the next server start: the configuration is read",
        "once at boot, so an edit made while the server runs applies when it is",
        "restarted.",
        "",
        "`samtal-server config schema [entity]` prints the same field descriptions",
        "as JSON Schema, which is what a machine reads before writing a fragment.",
        "",
        "## Entities",
        "",
    ]

    for candidate in ENTITIES:
        lines += _entity_section(candidate)

    lines += ["## Domain-level settings", ""]
    for name, title, command, notes in SETTINGS:
        lines += _setting_section(name, title, command, notes)

    lines += [
        "## The whole domain configuration",
        "",
        "What one deployment's domain half holds, which is what `samtal-server",
        "config show` prints and what the server loads at boot.",
        "",
        *_table(DomainConfig),
        "",
    ]
    return "\n".join(lines).rstrip("\n") + "\n"


def _entity_section(candidate: Entity) -> list[str]:
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


def _setting_section(name: str, title: str, command: str, notes: tuple[str, ...]) -> list[str]:
    lines = [
        f"### {title}",
        "",
        f"`{name}`",
        "",
        *_paragraph(DOMAIN_DESCRIPTIONS[name]),
        "",
        "```bash",
        command,
        "```",
        "",
    ]
    for note in notes:
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
            "samtal-server/examples/ for each type's options.",
        ]
    lines += [
        "",
        "Full descriptions: samtal-server config schema " + candidate.name,
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
    "DOMAIN",
    "ENTITIES",
    "entity",
    "entity_names",
    "fragment_help",
    "reference",
    "schema",
]
