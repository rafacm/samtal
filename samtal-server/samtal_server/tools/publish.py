"""Turning a far side's tool list into names the model can be given.

Both LLM APIs restrict tool names to `[A-Za-z0-9_-]` and cap them at 64
characters, and neither side that offers us tools is under any
obligation to comply: device tools are dotted by convention, and a
third-party MCP server may expose whatever its author liked. A name
that reaches a request unchecked does not fail politely. It fails the
whole request, so the assistant loses its voice over one badly named
tool nobody asked it to use.

Both sources therefore publish through here, and get the same
treatment: sanitize, drop what cannot be expressed at all, keep a
reverse map so the call goes out under the far side's real name, and
resolve collisions by keeping the first listed, so the outcome is the
same on every run.
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from samtal_server.providers import ToolDef
from samtal_server.tools import names

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PublishedTools:
    """What a far side offers, under names the model can be given."""

    tools: list[ToolDef]
    # Published name back to the name the far side listed, which is what
    # a call has to carry.
    originals: dict[str, str]

    def knows(self, name: str) -> bool:
        return name in self.originals

    def original_for(self, name: str) -> str | None:
        return self.originals.get(name)


def publish(
    listed: Iterable[tuple[str, str, dict[str, Any]]],
    prefix: str = "",
    label: str = "",
) -> PublishedTools:
    """Publish `(name, description, input_schema)` triples, optionally
    under an MCP server entry prefix. Whatever is dropped is logged with
    the reason, because a tool silently missing from the list is a
    question nobody can answer from the outside."""
    tools: list[ToolDef] = []
    originals: dict[str, str] = {}
    for original, description, input_schema in listed:
        sanitized = names.sanitize(original)
        published = names.qualified(prefix, sanitized) if prefix else sanitized
        if not sanitized:
            logger.warning("%s: dropping a tool with no usable name (%r)", label, original)
            continue
        if len(published) > names.MAX_TOOL_NAME_LENGTH:
            logger.warning(
                "%s: dropping tool %s, %s is longer than the %d characters both LLM "
                "APIs allow",
                label,
                original,
                published,
                names.MAX_TOOL_NAME_LENGTH,
            )
            continue
        if published in originals:
            logger.warning(
                "%s: dropping tool %s, it publishes as %s like %s",
                label,
                original,
                published,
                originals[published],
            )
            continue
        originals[published] = original
        tools.append(
            ToolDef(
                name=published,
                description=description,
                input_schema=input_schema if isinstance(input_schema, dict) else {},
            )
        )
    return PublishedTools(tools=tools, originals=originals)
