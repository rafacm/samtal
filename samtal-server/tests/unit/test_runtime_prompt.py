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

from samtal_server.runtime import prompt


def previously(persona: str, facts: str) -> str:
    """`tools.builtin.with_memory` as it stood before it folded into the
    assembler, transcribed so the equality is checked against the code
    rather than against a memory of it."""
    if not facts:
        return persona
    return f"{persona}\n\n{prompt.MEMORY_HEADING}\n{facts}".strip()


PERSONAS = ["POET", "", "   ", "  You are a poet.  \n", "line\n\nline"]

FACTS = ["", "- the user is vegetarian", "- one\n- two"]


@pytest.mark.parametrize("persona", PERSONAS)
@pytest.mark.parametrize("facts", FACTS)
def test_with_no_guidance_the_prompt_is_byte_for_byte_the_old_one(
    persona: str, facts: str
) -> None:
    assembled = prompt.with_memory(prompt.know_how(persona), facts)

    assert assembled.text == previously(persona, facts)


def test_the_order_is_persona_then_guidance_then_memory() -> None:
    assembled = prompt.with_memory(
        prompt.know_how(
            "You are the house assistant.",
            [
                prompt.Guidance("home", "Ask before unlocking the door."),
                prompt.Guidance("weather", "Give temperatures in Celsius."),
            ],
        ),
        "- the user is vegetarian",
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


def test_guidance_keeps_grant_order() -> None:
    """Grant order rather than alphabetical: what the operator listed is
    what the model reads, and it is the order the registry answers in."""
    assembled = prompt.know_how(
        "P", [prompt.Guidance("weather", "W"), prompt.Guidance("home", "H")]
    )

    assert [block.provenance for block in assembled.blocks] == [
        "persona",
        "instructions:weather",
        "instructions:home",
    ]


def test_guidance_is_injected_verbatim_under_its_heading() -> None:
    """Indentation and inner blank lines are what somebody wrote, so
    they reach the model as written. The one thing that does not survive
    is whitespace at the two ends of the whole prompt, which the join
    strips exactly as the memory append has always stripped it, so the
    text is asserted inside a prompt that carries a block after it."""
    written = "  Ask before unlocking the door.\n\n    The lights are safe.\n"
    assembled = prompt.with_memory(
        prompt.know_how("P", [prompt.Guidance("home", written)]), "- a fact"
    )

    assert assembled.blocks[1].text.endswith(written)
    assert written in assembled.text


def test_every_block_carries_its_provenance_and_its_size() -> None:
    assembled = prompt.with_memory(
        prompt.know_how("POET", [prompt.Guidance("home", "H")]), "- a fact"
    )

    assert assembled.sizes() == {
        "persona": len("POET"),
        "instructions:home": len(prompt.guidance_heading("home")) + len("\nH"),
        "memory": len(prompt.MEMORY_HEADING) + len("\n- a fact"),
    }
    assert assembled.characters == len(assembled.text)
    for block in assembled.blocks:
        assert block.text in assembled.text


def test_an_agent_with_no_prompt_of_its_own_still_has_a_persona_block() -> None:
    """Reported as the empty block it is rather than being absent, so
    the surface says an agent has no persona instead of saying nothing.
    The prompt itself does not start with a blank line."""
    assembled = prompt.with_memory(prompt.know_how(""), "- a fact")

    assert [block.provenance for block in assembled.blocks] == ["persona", "memory"]
    assert assembled.blocks[0].characters == 0
    assert assembled.text.startswith(prompt.MEMORY_HEADING)


def test_the_know_how_half_is_the_persona_alone_without_guidance() -> None:
    """The pin under the cache: for a configuration with no guidance the
    half a session caches is exactly the string the agent's prompt field
    holds."""
    assert prompt.know_how("POET").text == "POET"


def test_memory_that_is_empty_leaves_the_cached_half_alone() -> None:
    """Identity, not equality: the half is cached per activation and a
    round that remembers nothing must not rebuild it."""
    half = prompt.know_how("POET", [prompt.Guidance("home", "H")])

    assert prompt.with_memory(half, "") is half


def test_the_heading_names_the_prefix_the_model_can_call() -> None:
    assert prompt.guidance_heading("home").endswith("home__:")
