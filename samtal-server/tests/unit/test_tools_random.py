"""The builtin that draws a real random number.

A model cannot produce one: asked to roll a die it writes whichever
digit its distribution favours, and does it again the next time. So the
tool exists to be called, and what these tests hold is that a call is
answered from real entropy, inside the range it was asked for, and that
a range nobody can draw from is refused in words the model can act on
rather than answered with a number.

Nothing here asserts that two draws differ. That is the one property a
random source may legitimately fail on any given afternoon, and a test
asserting it would be a suite that fails once a year for being right.
"""

import pytest

from samtal_server.tools import names
from samtal_server.tools.builtin import (
    DEFAULT_MAXIMUM,
    DEFAULT_MINIMUM,
    RANDOM_BOUND,
    random_number,
    random_number_tool,
)


def drawn(answer: str) -> int:
    """The number out of the tool's answer, which also pins that the
    answer opens with one: the model reads it as text, and a result
    whose number is buried is a result it can misread."""
    return int(answer.split(",")[0])


def test_the_tool_asks_for_a_range_and_needs_neither_end() -> None:
    tool = random_number_tool()
    assert tool.name == names.RANDOM_NUMBER
    assert tool.name in names.BUILTIN_TOOL_NAMES
    properties = tool.input_schema["properties"]
    assert sorted(properties) == ["maximum", "minimum"]
    # No required list: a model that asks for a plain die sends nothing
    # and gets one.
    assert "required" not in tool.input_schema
    for end in properties.values():
        assert end["type"] == "integer"
        assert (end["minimum"], end["maximum"]) == (-RANDOM_BOUND, RANDOM_BOUND)


def test_a_drawn_number_is_a_whole_number_inside_the_range_asked_for() -> None:
    answer = random_number({"minimum": 1, "maximum": 6})
    assert answer.endswith("drawn at random between 1 and 6")
    assert 1 <= drawn(answer) <= 6


def test_every_draw_of_many_stays_inside_the_range() -> None:
    """Range membership over enough draws to catch an off-by-one at
    either end, which is the failure a single draw would hide, and the
    only claim about a sequence of draws this suite can honestly
    make."""
    for _ in range(500):
        assert 1 <= drawn(random_number({"minimum": 1, "maximum": 3})) <= 3


def test_a_call_with_no_arguments_rolls_an_ordinary_die() -> None:
    assert (DEFAULT_MINIMUM, DEFAULT_MAXIMUM) == (1, 6)
    for _ in range(100):
        assert DEFAULT_MINIMUM <= drawn(random_number({})) <= DEFAULT_MAXIMUM


def test_one_end_given_keeps_the_default_at_the_other() -> None:
    assert 1 <= drawn(random_number({"maximum": 100})) <= 100


def test_a_range_of_one_number_draws_that_number() -> None:
    assert drawn(random_number({"minimum": 7, "maximum": 7})) == 7


def test_a_range_below_zero_is_drawn_from_like_any_other() -> None:
    answer = random_number({"minimum": -10, "maximum": -8})
    assert -10 <= drawn(answer) <= -8


def test_the_widest_allowed_range_is_drawn_from() -> None:
    answer = random_number({"minimum": -RANDOM_BOUND, "maximum": RANDOM_BOUND})
    assert -RANDOM_BOUND <= drawn(answer) <= RANDOM_BOUND


@pytest.mark.parametrize(
    "arguments",
    [
        {"minimum": "1", "maximum": 6},
        {"minimum": 1, "maximum": 6.5},
        {"minimum": None, "maximum": 6},
        {"minimum": 1, "maximum": [6]},
        # A bool is an int as far as Python is concerned, and a model
        # that sent `true` for a bound did not mean 1.
        {"minimum": True, "maximum": 6},
    ],
)
def test_a_bound_that_is_not_a_whole_number_is_refused(arguments: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="whole number"):
        random_number(arguments)


@pytest.mark.parametrize(
    "arguments",
    [
        {"minimum": 1, "maximum": RANDOM_BOUND + 1},
        {"minimum": -RANDOM_BOUND - 1, "maximum": 1},
        {"minimum": 10**18, "maximum": 10**19},
    ],
)
def test_a_range_wider_than_the_hard_bounds_is_refused(arguments: dict[str, object]) -> None:
    """An absurd range is refused rather than drawn from: the output of
    this pipeline is a voice, and a twenty-digit number is not an answer
    anybody asked to hear."""
    with pytest.raises(ValueError, match="between -1000000 and 1000000"):
        random_number(arguments)


def test_a_range_that_runs_backwards_is_refused() -> None:
    with pytest.raises(ValueError, match="no greater than"):
        random_number({"minimum": 10, "maximum": 1})


def test_every_refusal_names_the_tool_and_the_argument() -> None:
    """The refusals are what the model is handed as an error result, so
    they have to say what to send instead rather than what went
    wrong."""
    for arguments in ({"minimum": "1"}, {"maximum": RANDOM_BOUND * 2}):
        with pytest.raises(ValueError) as raised:
            random_number(arguments)
        message = str(raised.value)
        assert message.startswith("random_number needs")
        assert next(iter(arguments)) in message
