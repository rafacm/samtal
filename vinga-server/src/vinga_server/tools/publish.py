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
chose, sanitizing only replaces the characters an LLM API refuses, and
a server that was handed a credential of this deployment's can hand it
back by listing a tool under it. So no name reaches a log line here,
whether it published or not. The model is given the published ones
because it has to be, and an operator reads them from
`vinga-server config mcp-server status`, in a terminal, on request; a
log is shipped to whatever collects it and kept as long as that keeps
things, which is a different thing to be. Every line below therefore
says which tool it means by its position in the listing, a number this
code counted, which identifies the tool without repeating a syllable of
it.
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from vinga_server.providers import ToolDef
from vinga_server.tools import names

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PublishedTools:
    """What a far side offers, under names the model can be given."""

    tools: list[ToolDef]
    # Published name back to the name the far side listed, which is what
    # a call has to carry.
    originals: dict[str, str]
    # And published name back to where the far side listed it, counted
    # from one.
    #
    # Kept because a line about one tool that may not repeat that tool's
    # name needs some way to say which one it means, and the position is
    # the only identifier in the whole exchange that this side owns: it
    # is a number this code counted, it cannot carry anything a far side
    # chose, and it is already the vocabulary the drops below are
    # reported in. It has to be recorded here rather than recovered
    # later, because everything downstream sees the published list,
    # which is this one with the drops taken out: counting that would
    # answer with a position no listing ever had.
    positions: dict[str, int] = field(default_factory=dict)

    def knows(self, name: str) -> bool:
        return name in self.originals

    def original_for(self, name: str) -> str | None:
        return self.originals.get(name)

    def position_of(self, name: str) -> int | None:
        """Where the far side listed this published tool, or None for a
        name this publication does not know."""
        return self.positions.get(name)


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
    positions: dict[str, int] = {}
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
            # Two positions and no name. This line used to print the
            # published name, on the grounds that it was the earlier
            # tool's rather than this one's and was on the connect line
            # already; the connect line stopped naming tools, and the
            # grounds went with it. Saying which two collided is what an
            # operator acts on anyway, and neither number can carry a
            # syllable either server chose.
            logger.warning(
                "%s: dropping tool %d in the listing, it publishes as the name tool %d "
                "already took",
                label,
                position,
                positions[published],
            )
            continue
        originals[published] = original
        positions[published] = position
        tools.append(
            ToolDef(
                name=published,
                description=description,
                input_schema=input_schema if isinstance(input_schema, dict) else {},
            )
        )
    return PublishedTools(tools=tools, originals=originals, positions=positions)
