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

What a drop is logged with is bounded by the same rule the rest of the
observability surface keeps: the name a far side listed is bytes it
chose, and one that received a credential of this deployment's can put
it there. A name that publishes crosses anyway, because the model has
to be given it and an operator has to be able to write it down. A name
that does not publish has no such claim on anybody, so it is never
written to the log: a dropped tool is identified by its position in the
listing, which says which one it was without repeating anything it
said.
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
    its position in the listing and the reason, because a tool silently
    missing from the list is a question nobody can answer from the
    outside."""
    tools: list[ToolDef] = []
    originals: dict[str, str] = {}
    for position, (original, description, input_schema) in enumerate(listed, start=1):
        sanitized = names.sanitize(original)
        published = names.qualified(prefix, sanitized) if prefix else sanitized
        if not sanitized:
            # Sanitizing to nothing means it was nothing: every other
            # character survives as itself or as an underscore. So there
            # is no name here to name it by, and nothing to print.
            logger.warning(
                "%s: dropping tool %d in the listing, whose name is empty", label, position
            )
            continue
        if len(published) > names.MAX_TOOL_NAME_LENGTH:
            # The length rather than the name: this one never publishes,
            # so its characters have no reason to reach a log, and what
            # an operator needs is which tool and by how much.
            logger.warning(
                "%s: dropping tool %d in the listing, its published name would be %d "
                "characters and both LLM APIs allow %d",
                label,
                position,
                len(published),
                names.MAX_TOOL_NAME_LENGTH,
            )
            continue
        if published in originals:
            # The one published name a drop may print, because it is not
            # this tool's: an earlier one already published under it, so
            # it is on the connect line and in front of the model
            # already.
            logger.warning(
                "%s: dropping tool %d in the listing, it publishes as %s, which an "
                "earlier one already took",
                label,
                position,
                published,
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
