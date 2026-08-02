"""What an agent remembers between conversations.

One file per agent, one `- fact` line per remembered item, injected into
the agent's system prompt on every reply. Memory is keyed by agent and
not by agent and device, because a persona is one entity across rooms:
"remember I am vegetarian", said in the kitchen, holds in the bedroom.
Telling people apart on a shared device is the voiceprint problem, and
keying by device would fragment memory without solving it.

There is no recall tool. For memory small enough to inject, injection is
the standard shape: it costs no lookup latency (a recall round trip is
spoken silence) and does not depend on a small local model choosing to
call it. The cap below is what keeps that true, and is why this becomes
a two-tier store, a small injected core plus a search tool, once memory
outgrows the prompt.
"""

import asyncio
import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# What keeps injection cheap: whichever trips first wins, and an append
# that overflows drops the oldest lines.
MAX_BYTES = 8192
MAX_LINES = 200

_UNSAFE_IN_FILENAME = re.compile(r"[^A-Za-z0-9_-]")


class MemoryStore:
    """The agents' remembered facts, on disk."""

    def __init__(self, directory: Path) -> None:
        self._dir = directory
        self._locks: dict[str, asyncio.Lock] = {}

    def path_for(self, agent: str) -> Path:
        """Where one agent's facts live. Agent names are configuration
        keys and may hold anything, so the filename is sanitized the way
        tool names are."""
        return self._dir / f"{_UNSAFE_IN_FILENAME.sub('_', agent)}.md"

    def read(self, agent: str) -> str:
        """This agent's facts, or an empty string when it has none. Read
        per reply rather than cached, so a fact remembered in one session
        is known to a concurrent one on its next reply."""
        try:
            return self.path_for(agent).read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return ""
        except OSError as exc:
            logger.warning("could not read memory for agent %s: %s", agent, exc)
            return ""

    async def remember(self, agent: str, fact: str) -> None:
        """Append one fact. Serialized per agent and written atomically,
        because two sessions can be talking to the same agent at once."""
        text = " ".join(fact.split())
        if not text:
            raise ValueError("there is nothing to remember")
        async with self._locks.setdefault(agent, asyncio.Lock()):
            await asyncio.to_thread(self._append, agent, text)

    def _append(self, agent: str, fact: str) -> None:
        path = self.path_for(agent)
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = self.read(agent)
        lines = existing.splitlines() if existing else []
        lines.append(f"- {fact}")
        lines = _within_the_cap(lines)

        # Temp file plus rename, so a reader never sees half a write.
        content = "\n".join(lines) + "\n"
        temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)


def _within_the_cap(lines: list[str]) -> list[str]:
    """The newest lines that fit. The oldest go first: a fact worth
    keeping tends to get said again, and the alternative (refusing to
    remember anything more) is worse to hear."""
    kept = lines[-MAX_LINES:]
    while len(kept) > 1 and len("\n".join(kept).encode("utf-8")) > MAX_BYTES:
        kept.pop(0)
    return kept
