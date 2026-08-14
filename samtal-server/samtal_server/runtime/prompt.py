"""The system prompt an agent replies under, assembled in one place.

Runtime code rather than configuration code: gluing prompt text
together is exactly what would not exist if the backend were a
telephone call to a human, which is the boundary's own litmus test.
`tools.builtin.with_memory` folded in here rather than surviving beside
it, so there is one place where prompt text is joined and one answer to
"what did the model actually receive".

The prompt has two halves with two clocks, and the split is what lets
assembly happen where the decision that owns it says it does. The
**know-how half** (the persona, the shared fragments it includes, and
the guidance of each MCP entry it is granted) is assembled once per
activation, at session open and
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
disagreeing about what was assembled, and the accounting is exact: the
prompt is the blocks joined by blank lines and nothing else, so a
character reported against a block is a character the model receives.

The order is fixed and documented, and deliberately not configurable:
the persona first, because it says who is speaking and everything after
it is read in that voice; the shared fragments next, in the order the
including layer lists them, because they are standing context the
persona speaks within; the guidance blocks after those in grant order,
each under a one-line heading naming the prefix its tools carry,
because they are about the tools rather than about the speaker; the
remembered facts last, which is where they already were.

One entry contributes up to three guidance blocks, and their order is
the order the trust decisions were taken: what the operator wrote about
the entry, then what the server shipped about itself where the entry
opted into it, then the prompts that server publishes in the order the
entry named them. The two server-shipped kinds sit under headings that
say the server is the one talking, because the model is the one reader
that cannot see the provenance every other surface reports.
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

# One shared fragment, qualified by its name, which is safe to print for
# the same reason and by the same rule: a fragment name is refused at
# parse time unless it is written in that charset.
FRAGMENT = "fragment"

# What one MCP server shipped about itself, through each of the two
# channels it has. Qualified by the entry name for the instructions, and
# by the entry name and the position in the entry's `inject_prompts` for
# a published prompt: a prompt's own name is a server-chosen string the
# operator copied, so nothing bounds what it holds and it is not part of
# a token this server prints.
SERVER_INSTRUCTIONS = "server_instructions"
SERVER_PROMPT = "server_prompt"


def instructions_provenance(entry: str) -> str:
    return f"{INSTRUCTIONS}:{entry}"


def fragment_provenance(name: str) -> str:
    return f"{FRAGMENT}:{name}"


def server_instructions_provenance(entry: str) -> str:
    return f"{SERVER_INSTRUCTIONS}:{entry}"


def server_prompt_provenance(entry: str, position: int) -> str:
    return f"{SERVER_PROMPT}:{entry}:{position}"


def guidance_heading(entry: str) -> str:
    """The one line an entry's guidance sits under.

    It names the prefix rather than the entry, because the prefix is
    what the model can act on: the tools it may call are called
    `home__turn_on_light`, and a heading that named only `home` would
    leave it to guess which of the names in front of it the paragraph
    is about.
    """
    return f"Guidance for using the tools whose names begin with {entry}{names.SERVER_SEPARATOR}:"


def server_instructions_heading(entry: str) -> str:
    """The one line the server's own description of itself sits under.

    It names the server rather than the deployment, which the operator
    block's heading does not have to: these are a third party's words,
    injected because an entry opted into them, and a model reading the
    prompt should be able to tell whose advice it is following as
    plainly as the operator reading the inspection surface can.
    """
    return (
        f"The server behind the tools whose names begin with "
        f"{entry}{names.SERVER_SEPARATOR} says this about using them:"
    )


def server_prompt_heading(entry: str) -> str:
    """The same, for one of the prompts that server publishes."""
    return (
        f"The server behind the tools whose names begin with "
        f"{entry}{names.SERVER_SEPARATOR} publishes this guidance:"
    )


@dataclass(frozen=True)
class Fragment:
    """One shared fragment an agent includes, as the configuration
    resolves it: the name it is stored under, and the text as it was
    written.

    The block shape lives here rather than beside the rows it is read
    from, for the reason `Guidance` does: what a fragment is made of is
    the configuration's business, and where it sits in a prompt and what
    is written around it is this module's.
    """

    name: str
    text: str


@dataclass(frozen=True)
class Guidance:
    """One entry's operator-written guidance, as the registry answers
    it: the entry it belongs to, and the text as it was written."""

    entry: str
    text: str


@dataclass(frozen=True)
class ServerInstructions:
    """What one connected MCP server shipped about itself, as the
    registry captured it: the entry it came in on, and the text.

    A separate shape from `Guidance` rather than a flag on it, because
    the two are separate trust decisions with separate provenance and
    separate headings, and a boolean would leave every reader to
    remember which way round it went."""

    entry: str
    text: str


@dataclass(frozen=True)
class ServerPrompt:
    """One prompt an MCP server published, rendered and captured: the
    entry, the position the operator listed it at, the name they wrote,
    and the text.

    The position is what every token and every log line identifies it
    by; the name travels beside them for the surfaces that echo
    operator-written configuration back, and nowhere else."""

    entry: str
    position: int
    name: str
    text: str


# What an MCP entry contributes to a prompt, in the order the entry's
# blocks are injected: the operator's own guidance, then whatever the
# server shipped that the entry opted into.
GuidanceBlock = Guidance | ServerInstructions | ServerPrompt


@dataclass(frozen=True)
class Block:
    """One block of the assembled prompt: where it came from, and the
    text as the model receives it, heading included."""

    provenance: str
    text: str
    # The configured name behind a `server_prompt` block, and None for
    # every other kind. Carried as data rather than folded into the
    # provenance: the token is printed in logs and in a structured
    # event, and a server-chosen name belongs in neither.
    name: str | None = None

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
        tunes a context budget against. The sum of the blocks plus the
        blank line between each pair of them, exactly: the text is the
        blocks joined, so there is nothing in it that is counted
        nowhere."""
        return len(self.text)

    def sizes(self) -> dict[str, int]:
        """Each block's size by provenance, which is what a structured
        event carries."""
        return {block.provenance: block.characters for block in self.blocks}


def know_how(
    persona: str,
    fragments: Sequence[Fragment] = (),
    guidance: Sequence[GuidanceBlock] = (),
) -> Assembled:
    """The half of the prompt that changes only when the agent does: the
    persona, the shared fragments it includes in the order it lists
    them, and the guidance of each entry it is granted, in grant order.

    Assembled once per activation and cached by the caller. An agent
    with no prompt of its own contributes no persona block, because a
    block is what the model receives and this one would be nothing: the
    surface reports the prompt rather than the fields it was made from.

    A fragment is injected with no heading over it, which is the one way
    it differs from a guidance block: a fragment is prompt text the
    operator composed, and a heading would editorialize, while guidance
    is about a set of tools and has to say which. It is otherwise a
    block like any other, so what `_assembled` trims at the two ends of
    the prompt it trims here too, and everything inside a fragment is
    left as it was written.

    An entry's guidance is one to three blocks, in the order the trust
    decisions were taken: what the operator wrote, then what the server
    shipped about itself where the entry opted into it, then the prompts
    that server publishes in the order the entry named them. The caller
    hands them over already in that order, since which of them exist is
    a question about grants, opt-ins and what a connection captured.
    """
    return _assembled(
        [
            Block(PERSONA, persona),
            *(
                Block(fragment_provenance(block.name), block.text)
                for block in fragments
            ),
            *(_guidance_block(block) for block in guidance),
        ]
    )


def _guidance_block(block: GuidanceBlock) -> Block:
    """One entry-guidance block, under the heading its source earns.

    Three headings rather than one, because the model is the reader that
    cannot see the provenance the surfaces report: the operator's block
    says what the tools are for, and each server-shipped block says that
    the server itself is the one saying it, which is the trust boundary
    made legible where it matters most.
    """
    if isinstance(block, Guidance):
        return Block(
            instructions_provenance(block.entry),
            f"{guidance_heading(block.entry)}\n{block.text}",
        )
    if isinstance(block, ServerInstructions):
        return Block(
            server_instructions_provenance(block.entry),
            f"{server_instructions_heading(block.entry)}\n{block.text}",
        )
    return Block(
        server_prompt_provenance(block.entry, block.position),
        f"{server_prompt_heading(block.entry)}\n{block.text}",
        name=block.name,
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
    """The blocks joined by blank lines, and the blocks as they were
    joined.

    Both halves of that sentence matter, and the second one is the
    contract this surface exists for: the text is exactly the blocks
    joined, so a character counted against a block is a character the
    model receives and a byte the model never sees is reported nowhere.
    Whatever this function adjusts, it adjusts in the blocks as well.

    What it adjusts is the two ends of the prompt and nothing between
    them. A block that holds only whitespace contributes nothing and is
    dropped, the first block loses its leading whitespace and the last
    its trailing, which is what keeps an agent with no prompt of its own
    from being sent one that begins with a blank line. Every byte inside
    a block, its indentation and its own blank lines included, is left
    exactly as it was written.

    A prompt of one block is handed over untouched instead, because a
    persona standing alone is what an agent's prompt field holds and
    trimming it would be this module editing a value it was handed. That
    is also the byte-equality pin: for a configuration with no guidance,
    every path through here produces character for character what the
    memory append produced before this module existed, including the
    strip it has always ended with.
    """
    if len(blocks) == 1:
        return Assembled(tuple(blocks), blocks[0].text)
    # A prompt made of nothing but a blank persona cannot happen (the
    # blocks that join it are non-blank by construction), but falling
    # back to the persona rather than to no blocks at all keeps this
    # total rather than resting on that.
    kept = [block for block in blocks if block.text.strip()] or [blocks[0]]
    texts = [block.text for block in kept]
    texts[0] = texts[0].lstrip()
    texts[-1] = texts[-1].rstrip()
    trimmed = tuple(
        Block(block.provenance, text, block.name)
        for block, text in zip(kept, texts, strict=True)
    )
    return Assembled(trimmed, "\n\n".join(block.text for block in trimmed))


__all__ = [
    "FRAGMENT",
    "INSTRUCTIONS",
    "MEMORY",
    "MEMORY_HEADING",
    "PERSONA",
    "SERVER_INSTRUCTIONS",
    "SERVER_PROMPT",
    "Assembled",
    "Block",
    "Fragment",
    "Guidance",
    "GuidanceBlock",
    "ServerInstructions",
    "ServerPrompt",
    "fragment_provenance",
    "guidance_heading",
    "instructions_provenance",
    "know_how",
    "server_instructions_heading",
    "server_instructions_provenance",
    "server_prompt_heading",
    "server_prompt_provenance",
    "with_memory",
]
