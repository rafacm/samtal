"""The system prompt an agent replies under, assembled in one place.

Runtime code rather than configuration code: gluing prompt text
together is exactly what would not exist if the backend were a
telephone call to a human, which is the boundary's own litmus test.
`tools.builtin.with_memory` folded in here rather than surviving beside
it, so there is one place where prompt text is joined and one answer to
"what did the model actually receive".

The prompt has two halves with two clocks, and the split is what lets
assembly happen where the decision that owns it says it does. The
**know-how half** (the persona, and the guidance of each MCP entry the
agent is granted) is assembled once per activation, at session open and
again at an agent switch, and cached for the life of that activation:
nothing about it is recomputed per reply and nothing is fetched while
it is assembled. The **memory block** keeps the clock it already had,
read on every round and appended to the cached half, because that read
predates this module and its per-reply freshness is a contract today's
code documents: a fact remembered in one session is known to a
concurrent one on its next reply.

Everything here is a pure function over text. What each caller needs
beyond the prompt itself is the accounting: which block came from
where, and how many characters each of them costs, which is what the
inspection surface reports, what the `prompt_assembled` event carries,
and what an operator tunes a small local model against. Producing both
in one place is what keeps the pipeline, the event and the surface from
disagreeing about what was assembled.

The order is fixed and documented, and deliberately not configurable:
the persona first, because it says who is speaking and everything after
it is read in that voice; the guidance blocks next in grant order, each
under a one-line heading naming the prefix its tools carry, because
they are about the tools rather than about the speaker; the remembered
facts last, which is where they already were.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from samtal_server.tools import names

# The heading the remembered facts are injected under, as the model
# reads them. Moved here with the assembly it belongs to.
MEMORY_HEADING = "You remember these facts about past conversations:"

# What a block says about where it came from. Fixed tokens this
# application owns: they are printed by the CLI, carried in a structured
# event and keyed in an API response, so none of them is ever a value
# that arrived from somewhere else.
PERSONA = "persona"
MEMORY = "memory"

# The operator's own guidance about one MCP entry, qualified by the
# entry name, which is safe to print by construction: an entry name has
# been through the `[A-Za-z0-9_-]+` rule that makes it a tool prefix.
INSTRUCTIONS = "instructions"


def instructions_provenance(entry: str) -> str:
    return f"{INSTRUCTIONS}:{entry}"


def guidance_heading(entry: str) -> str:
    """The one line an entry's guidance sits under.

    It names the prefix rather than the entry, because the prefix is
    what the model can act on: the tools it may call are called
    `home__turn_on_light`, and a heading that named only `home` would
    leave it to guess which of the names in front of it the paragraph
    is about.
    """
    return f"Guidance for using the tools whose names begin with {entry}{names.SERVER_SEPARATOR}:"


@dataclass(frozen=True)
class Guidance:
    """One entry's operator-written guidance, as the registry answers
    it: the entry it belongs to, and the text as it was written."""

    entry: str
    text: str


@dataclass(frozen=True)
class Block:
    """One block of the assembled prompt: where it came from, and the
    text as the model receives it, heading included."""

    provenance: str
    text: str

    @property
    def characters(self) -> int:
        return len(self.text)


@dataclass(frozen=True)
class Assembled:
    """An assembled prompt and its accounting: the text to send, and the
    ordered blocks it was made of.

    Both halves of the split live in this one type. `know_how` answers
    with the cached half, and `with_memory` answers with that half plus
    a memory block, so what a session hands the model and what the
    inspection surface reports are the same shape built by the same
    code.
    """

    blocks: tuple[Block, ...]
    text: str

    @property
    def characters(self) -> int:
        """The size of the whole prompt, which is the number an operator
        tunes a context budget against. Not the sum of the blocks: the
        separators between them count, and a leading blank block is
        stripped away."""
        return len(self.text)

    def sizes(self) -> dict[str, int]:
        """Each block's size by provenance, which is what a structured
        event carries."""
        return {block.provenance: block.characters for block in self.blocks}


def know_how(persona: str, guidance: Sequence[Guidance] = ()) -> Assembled:
    """The half of the prompt that changes only when the agent does: the
    persona, and the guidance of each entry the agent is granted, in
    grant order.

    Assembled once per activation and cached by the caller. The persona
    is always a block of its own, empty or not, so that an agent with no
    prompt of its own is visible as one on the inspection surface rather
    than being silently absent from it.
    """
    return _assembled(
        [
            Block(PERSONA, persona),
            *(
                Block(
                    instructions_provenance(block.entry),
                    f"{guidance_heading(block.entry)}\n{block.text}",
                )
                for block in guidance
            ),
        ]
    )


def with_memory(half: Assembled, facts: str) -> Assembled:
    """The cached know-how half with whatever the agent remembers
    appended, which is the prompt one round is sent.

    Read per round rather than per activation, so a fact remembered in
    one session is known to a concurrent one on its next reply. `facts`
    is passed in rather than read here: the read is filesystem I/O and
    belongs off the event loop, and this stays a pure function of the
    text it is handed.
    """
    if not facts:
        return half
    return _assembled([*half.blocks, Block(MEMORY, f"{MEMORY_HEADING}\n{facts}")])


def _assembled(blocks: Sequence[Block]) -> Assembled:
    """The blocks joined by blank lines.

    The join is stripped when anything was appended to the persona,
    which is what keeps an agent with no prompt of its own from being
    sent a prompt that starts with a blank line. A persona standing
    alone is handed over exactly as it was written instead, which is
    what the byte-equality pin holds: for a configuration with no
    guidance, this function produces character for character what the
    memory append has always produced.

    The one thing a verbatim block does not keep is whitespace at the
    two ends of the whole prompt, since that is what the strip takes.
    Deliberately unchanged rather than repaired: it is the behavior the
    memory block has had all along, and the pin is worth more than a
    trailing newline nothing reads.
    """
    text = "\n\n".join(block.text for block in blocks)
    return Assembled(tuple(blocks), text if len(blocks) == 1 else text.strip())


__all__ = [
    "INSTRUCTIONS",
    "MEMORY",
    "MEMORY_HEADING",
    "PERSONA",
    "Assembled",
    "Block",
    "Guidance",
    "guidance_heading",
    "instructions_provenance",
    "know_how",
    "with_memory",
]
