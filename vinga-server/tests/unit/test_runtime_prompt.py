"""The assembler: what the model is sent, and what it was made of.

Two properties carry the file. The order is fixed and pinned, headings
and blank lines included, because it is a documented contract rather
than an implementation detail. And for a configuration with no
guidance, the assembled text is character for character what the memory
append produced before this module existed, which is what makes the
refactor provably a reordering of nothing: the old implementation is
written out below and the two are compared over the awkward inputs
(an empty prompt, a prompt that is only whitespace, one with its own
indentation), not just over a tidy one.
"""

import pytest

from vinga_server.memory.store import PromptMemory
from vinga_server.runtime import prompt


def previously(persona: str, facts: str) -> str:
    """`tools.builtin.with_memory` as it stood before it folded into the
    assembler, transcribed so the equality is checked against the code
    rather than against a memory of it."""
    if not facts:
        return persona
    return f"{persona}\n\n{prompt.MEMORY_HEADING}\n{facts}".strip()


def remembered(facts: str) -> PromptMemory:
    """The agent's own scope and nothing else, which is what every
    deployment has the day the scopes land: no conversation has kept a
    note yet and no device has been told anything, so the prompt is what
    it was before there were scopes."""
    return PromptMemory(state="", agent=facts, device="")


PERSONAS = ["POET", "", "   ", "  You are a poet.  \n", "line\n\nline"]

FACTS = ["", "- the user is vegetarian", "- one\n- two"]


@pytest.mark.parametrize("persona", PERSONAS)
@pytest.mark.parametrize("facts", FACTS)
def test_with_no_guidance_the_prompt_is_byte_for_byte_the_old_one(
    persona: str, facts: str
) -> None:
    assembled = prompt.with_scopes(prompt.know_how(persona), remembered(facts))

    assert assembled.text == previously(persona, facts)


def test_the_order_is_persona_then_guidance_then_memory() -> None:
    assembled = prompt.with_scopes(
        prompt.know_how(
            "You are the house assistant.",
            guidance=[
                prompt.Guidance("home", "Ask before unlocking the door."),
                prompt.Guidance("weather", "Give temperatures in Celsius."),
            ],
        ),
        remembered("- the user is vegetarian"),
    )

    assert assembled.text == (
        "You are the house assistant.\n"
        "\n"
        "Guidance for using the tools whose names begin with home__:\n"
        "Ask before unlocking the door.\n"
        "\n"
        "Guidance for using the tools whose names begin with weather__:\n"
        "Give temperatures in Celsius.\n"
        "\n"
        f"{prompt.MEMORY_HEADING}\n"
        "- the user is vegetarian"
    )


def test_the_fragments_sit_between_the_persona_and_the_guidance() -> None:
    """The whole order in one assembly, with nothing put over a
    fragment: a heading would editorialize text the operator
    composed."""
    assembled = prompt.with_scopes(
        prompt.know_how(
            "You are the house assistant.",
            [
                prompt.Fragment("household", "The bins go out on Tuesday."),
                prompt.Fragment("style", "Answer in one sentence."),
            ],
            [prompt.Guidance("home", "Ask before unlocking the door.")],
        ),
        remembered("- the user is vegetarian"),
    )

    assert assembled.text == (
        "You are the house assistant.\n"
        "\n"
        "The bins go out on Tuesday.\n"
        "\n"
        "Answer in one sentence.\n"
        "\n"
        "Guidance for using the tools whose names begin with home__:\n"
        "Ask before unlocking the door.\n"
        "\n"
        f"{prompt.MEMORY_HEADING}\n"
        "- the user is vegetarian"
    )
    assert [block.provenance for block in assembled.blocks] == [
        "persona",
        "fragment:household",
        "fragment:style",
        "instructions:home",
        "memory",
    ]


def test_a_fragment_is_injected_byte_for_byte() -> None:
    """A fragment's interior is what somebody wrote: its indentation and
    its own blank lines reach the model as written, and its size is the
    size of what was sent.

    The two ends of the whole prompt are the exception every block
    shares, so this is asserted where a fragment is neither of them.
    The parametrized test below is what covers a fragment that is."""
    written = "  The bins go out on Tuesday.\n\n    The radio is called Bosse.\n"
    assembled = prompt.with_scopes(
        prompt.know_how(
            "P",
            [prompt.Fragment("household", written)],
            [prompt.Guidance("home", "Ask first.")],
        ),
        remembered("- a fact"),
    )

    assert assembled.blocks[1].text == written
    assert written in assembled.text
    assert assembled.sizes()["fragment:household"] == len(written)


# A fragment is a block like the others, which is the whole of what it
# has to be for the surface's equality to hold: the same awkward inputs
# M1's rules were fixed against, written into fragments this time.

AWKWARD_FRAGMENTS = [
    ("  POET  \n", (prompt.Fragment("household", "Bins on Tuesday.\n\n"),), ""),
    ("", (prompt.Fragment("household", "  Bins on Tuesday.\n"),), ""),
    ("   ", (prompt.Fragment("household", "  Bins.  "),), "- a fact"),
    (
        "POET",
        (prompt.Fragment("household", "Bins.\n"), prompt.Fragment("style", "  One line.  ")),
        "- a fact\n",
    ),
]


@pytest.mark.parametrize(("persona", "fragments", "facts"), AWKWARD_FRAGMENTS)
def test_a_fragment_obeys_the_rules_every_block_obeys(
    persona: str, fragments: tuple[prompt.Fragment, ...], facts: str
) -> None:
    """The prompt is the blocks joined, the ends are trimmed with the
    blocks that hold them, and a fragment that would hold nothing is
    reported nowhere. Milestone 1 pinned this over personas and
    guidance; a new block type has to be inside it rather than beside
    it."""
    assembled = prompt.with_scopes(prompt.know_how(persona, fragments), remembered(facts))

    assert "\n\n".join(block.text for block in assembled.blocks) == assembled.text
    assert assembled.characters == sum(assembled.sizes().values()) + 2 * (
        len(assembled.blocks) - 1
    )
    if len(assembled.blocks) > 1:
        assert assembled.text == assembled.text.strip()
        assert all(block.text.strip() for block in assembled.blocks)


def test_guidance_keeps_grant_order() -> None:
    """Grant order rather than alphabetical: what the operator listed is
    what the model reads, and it is the order the registry answers in."""
    assembled = prompt.know_how(
        "P", guidance=[prompt.Guidance("weather", "W"), prompt.Guidance("home", "H")]
    )

    assert [block.provenance for block in assembled.blocks] == [
        "persona",
        "instructions:weather",
        "instructions:home",
    ]


def test_guidance_is_injected_verbatim_under_its_heading() -> None:
    """Indentation and inner blank lines are what somebody wrote, so
    they reach the model as written. The two ends of the whole prompt
    are the exception, and the block reports what it sent: what is
    asserted here is that the interior survives and that the block and
    the prompt agree about the rest."""
    written = "  Ask before unlocking the door.\n\n    The lights are safe.\n"
    assembled = prompt.know_how("P", guidance=[prompt.Guidance("home", written)])

    guidance = assembled.blocks[1]
    assert "  Ask before unlocking the door.\n\n    The lights are safe." in guidance.text
    assert guidance.text in assembled.text
    assert assembled.text.endswith(guidance.text)


def test_an_entrys_own_guidance_comes_before_what_its_server_shipped() -> None:
    """The order inside one entry is the order the trust decisions were
    taken: the operator's words, then the server's own description of
    itself, then the prompts it publishes in the order the entry named
    them. Each server-shipped block says the server is the one talking,
    because the model is the one reader that cannot see a provenance."""
    assembled = prompt.know_how(
        "You are the house assistant.",
        guidance=[
            prompt.Guidance("home", "Ask before unlocking the door."),
            prompt.ServerInstructions("home", "Call list_devices first."),
            prompt.ServerPrompt("home", 1, "house_style", "Answer in short sentences."),
        ],
    )

    assert assembled.text == (
        "You are the house assistant.\n"
        "\n"
        "Guidance for using the tools whose names begin with home__:\n"
        "Ask before unlocking the door.\n"
        "\n"
        "What the server behind the home__ tools says about using them:\n"
        "Call list_devices first.\n"
        "\n"
        "Guidance the server behind the home__ tools publishes:\n"
        "Answer in short sentences."
    )
    assert [block.provenance for block in assembled.blocks] == [
        "persona",
        "instructions:home",
        "server_instructions:home",
        "server_prompt:home:1",
    ]


def test_a_shipped_prompt_is_reported_by_position_and_carries_its_name() -> None:
    """The token is the position, since it is printed in a log and in a
    structured event and a server-chosen name belongs in neither; the
    name travels beside it as data, for the surfaces that echo what the
    operator wrote."""
    assembled = prompt.know_how(
        "P",
        guidance=[
            prompt.ServerPrompt("home", 2, "sk-not-a-real-credential\x1b[2J", "G")
        ],
    )

    shipped = assembled.blocks[1]
    assert shipped.provenance == "server_prompt:home:2"
    assert shipped.name == "sk-not-a-real-credential\x1b[2J"
    assert shipped.name not in shipped.provenance


def test_an_ordinary_block_carries_no_name() -> None:
    assembled = prompt.with_scopes(
        prompt.know_how(
            "P",
            fragments=[prompt.Fragment("household", "F")],
            guidance=[prompt.Guidance("home", "G")],
        ),
        remembered("- a fact"),
    )

    assert [block.name for block in assembled.blocks] == [None, None, None, None]


# The blocks are the prompt
#
# The surface exists to say what the model receives, so the blocks it
# reports have to be the prompt and not a description of it. These pin
# that as an equality over the inputs that used to break it: a persona
# with leading whitespace, and a last block whose text ends in blank
# lines.

AWKWARD = [
    ("  POET  \n", (), ""),
    ("  POET  \n", (prompt.Guidance("home", "Ask first.\n\n"),), ""),
    ("\n  POET", (prompt.Guidance("home", "  Ask first.\n\n"),), "- a fact"),
    ("", (prompt.Guidance("home", "Ask first."),), ""),
    ("   ", (), "- a fact"),
    ("POET", (prompt.Guidance("home", "Ask first."),), "- a fact\n"),
    # A server's own bytes are inside the same contract rather than
    # exempt from it: this is a third party's whitespace, and the block
    # still reports exactly what was sent.
    ("  POET", (prompt.ServerInstructions("home", "Call list_devices.\n\n"),), ""),
    (
        "",
        (
            prompt.ServerInstructions("home", "  Shipped.\n"),
            prompt.ServerPrompt("home", 1, "house_style", "Published.\n\n"),
        ),
        "",
    ),
]


@pytest.mark.parametrize(("persona", "guidance", "facts"), AWKWARD)
def test_the_prompt_is_the_blocks_and_nothing_else(
    persona: str, guidance: tuple[prompt.GuidanceBlock, ...], facts: str
) -> None:
    """The equality the inspection surface is worth nothing without: a
    byte the model is sent is a byte some block reports, and a byte a
    block reports is a byte the model is sent."""
    for assembled in (
        prompt.know_how(persona, guidance=guidance),
        prompt.with_scopes(prompt.know_how(persona, guidance=guidance), remembered(facts)),
    ):
        assert "\n\n".join(block.text for block in assembled.blocks) == assembled.text
        assert assembled.characters == sum(assembled.sizes().values()) + 2 * (
            len(assembled.blocks) - 1
        )


@pytest.mark.parametrize(("persona", "guidance", "facts"), AWKWARD)
def test_no_block_holds_whitespace_the_prompt_does_not(
    persona: str, guidance: tuple[prompt.GuidanceBlock, ...], facts: str
) -> None:
    """The other side of it, stated as the rule that produces it: the
    ends of the prompt are trimmed, so the ends of the first and last
    blocks are trimmed with them rather than being reported as bytes
    that went nowhere.

    A prompt of one block is exempt and stays exactly as it was
    written, which is the persona standing alone: trimming it would be
    this module editing the value it was handed, and it is what the
    byte-equality pin holds."""
    assembled = prompt.with_scopes(prompt.know_how(persona, guidance=guidance), remembered(facts))
    if len(assembled.blocks) == 1:
        assert assembled.text == assembled.blocks[0].text
        return

    assert assembled.text == assembled.text.strip()
    assert assembled.blocks[0].text == assembled.blocks[0].text.lstrip()
    assert assembled.blocks[-1].text == assembled.blocks[-1].text.rstrip()
    assert all(block.text.strip() for block in assembled.blocks)


def test_every_block_carries_its_provenance_and_its_size() -> None:
    assembled = prompt.with_scopes(
        prompt.know_how("POET", guidance=[prompt.Guidance("home", "H")]),
        remembered("- a fact"),
    )

    assert assembled.sizes() == {
        "persona": len("POET"),
        "instructions:home": len(prompt.guidance_heading("home")) + len("\nH"),
        "memory": len(prompt.MEMORY_HEADING) + len("\n- a fact"),
    }
    assert assembled.characters == len(assembled.text)
    for block in assembled.blocks:
        assert block.text in assembled.text


def test_an_agent_with_no_prompt_of_its_own_contributes_no_block() -> None:
    """The prompt does not begin with a blank line, and the blocks do
    not claim one: a block is what the model receives, and this one
    would be nothing."""
    assembled = prompt.with_scopes(prompt.know_how(""), remembered("- a fact"))

    assert [block.provenance for block in assembled.blocks] == ["memory"]
    assert assembled.text.startswith(prompt.MEMORY_HEADING)
    assert assembled.text == assembled.blocks[0].text


def test_the_know_how_half_is_the_persona_alone_without_guidance() -> None:
    """The pin under the cache: for a configuration with no guidance the
    half a session caches is exactly the string the agent's prompt field
    holds."""
    assert prompt.know_how("POET").text == "POET"


def test_memory_that_is_empty_leaves_the_cached_half_alone() -> None:
    """Identity, not equality: the half is cached per activation and a
    round that remembers nothing must not rebuild it. All three scopes
    empty is the same answer as no memory at all, which is what every
    deployment has until an agent uses one."""
    half = prompt.know_how("POET", guidance=[prompt.Guidance("home", "H")])

    assert prompt.with_scopes(half, remembered("")) is half
    assert prompt.with_scopes(half, PromptMemory("", "", "")) is half


# The three scopes, in the order they take precedence in


def test_the_three_blocks_are_appended_in_their_order_of_precedence() -> None:
    """The whole rendering, headings and blank lines included, because
    it is a documented contract rather than an implementation detail.

    The order is the precedence, and each block says its own rank where
    the model reads it: the ledger from the top, because everything after
    it is older, and the device's notes from below, because everything
    before them outranks them.
    """
    assembled = prompt.with_scopes(
        prompt.know_how("You are the house assistant."),
        PromptMemory(
            state="- scene: the tavern",
            agent="- the user is vegetarian",
            device="- the kitchen speaker is the loud one",
        ),
    )

    assert assembled.text == (
        "You are the house assistant.\n"
        "\n"
        f"{prompt.STATE_HEADING}\n"
        "- scene: the tavern\n"
        "\n"
        f"{prompt.MEMORY_HEADING}\n"
        "- the user is vegetarian\n"
        "\n"
        f"{prompt.DEVICE_HEADING}\n"
        "- the kitchen speaker is the loud one"
    )
    assert [block.provenance for block in assembled.blocks] == [
        "persona",
        "state",
        "memory",
        "device",
    ]


def test_each_scope_is_counted_under_a_token_of_its_own() -> None:
    """Three blocks under one token would collapse into one number in
    the event and in the accounting, and what an operator tunes against
    is what each of them costs."""
    assembled = prompt.with_scopes(
        prompt.know_how("POET"),
        PromptMemory(state="- a: b", agent="- a fact", device="- a note"),
    )

    assert assembled.sizes() == {
        "persona": len("POET"),
        "state": len(prompt.STATE_HEADING) + len("\n- a: b"),
        "memory": len(prompt.MEMORY_HEADING) + len("\n- a fact"),
        "device": len(prompt.DEVICE_HEADING) + len("\n- a note"),
    }
    assert assembled.characters == len(assembled.text)


@pytest.mark.parametrize(
    ("scopes", "expected"),
    [
        (PromptMemory(state="- a: b", agent="", device=""), ["persona", "state"]),
        (PromptMemory(state="", agent="- a fact", device=""), ["persona", "memory"]),
        (PromptMemory(state="", agent="", device="- a note"), ["persona", "device"]),
        (
            PromptMemory(state="- a: b", agent="", device="- a note"),
            ["persona", "state", "device"],
        ),
    ],
)
def test_a_scope_with_nothing_in_it_renders_no_block(
    scopes: PromptMemory, expected: list[str]
) -> None:
    """A heading over nothing is a heading the model has to make sense
    of. Absence is the honest rendering of an empty scope, and it is what
    keeps a deployment using one of the three from paying for three."""
    assembled = prompt.with_scopes(prompt.know_how("POET"), scopes)

    assert [block.provenance for block in assembled.blocks] == expected
    assert assembled.text == "\n\n".join(block.text for block in assembled.blocks)


def test_the_heading_names_the_prefix_the_model_can_call() -> None:
    assert prompt.guidance_heading("home").endswith("home__:")


def test_the_server_headings_name_the_prefix_and_say_who_is_talking() -> None:
    for heading in (
        prompt.server_instructions_heading("home"),
        prompt.server_prompt_heading("home"),
    ):
        assert "home__" in heading
        assert "server" in heading
        # Shorter than the operator's, which spells the prefix rule out:
        # these sit under it, and a heading costs the same budget the
        # surface beside it counts.
        assert len(heading) < len(prompt.guidance_heading("home")) + 10
