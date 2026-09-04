"""The rule that decides whether a sentence may be spoken at all.

A model that writes a tool call into its own speech used to have it read
out loud, which is the one user-facing surface with no filter on
untrusted content (#385). The guard is deliberately narrow: not "this
looks like JSON", which would eat a conversation about a JSON snippet,
but "this is shaped like a call to a tool THIS reply offered".

Two prongs, and the matrix below is one case per branch of each. The
named prong is the obvious shape and the two spellings of it a model
writes. The argument-only prong is the shape actually observed in the
field, where the name never made it out and only the arguments did, and
its cases are the ones that say where its edge is: a set that fits one
tool, a set that fits several, a set that fits none.

What the guard answers is read here rather than through a session, so a
rule change shows up as a row rather than as a reply that lost a
sentence. `tests/unit/test_session_withheld.py` is the other end, where
the same rule is driven through both of the tool loop's sentence sites.
"""

import json

from vinga_server.providers import ToolDef
from vinga_server.runtime.speech import withhold_tool_shaped

# The device tool the field report was about: a board control whose one
# declared argument is an integer, called by a model that sent a string.
VOLUME = ToolDef(
    name="self_audio_speaker_set_volume",
    description="Set the speaker volume",
    input_schema={"type": "object", "properties": {"volume": {"type": "integer"}}},
)

# A builtin beside it, so a matrix row can tell "named a tool" from
# "named something nobody offered" without changing the offered set.
REMEMBER = ToolDef(
    name="remember",
    description="Remember one fact",
    input_schema={
        "type": "object",
        "properties": {"text": {"type": "string"}, "scope": {"type": "string"}},
        "required": ["text"],
    },
)

OFFERED = (VOLUME, REMEMBER)


class Reported:
    """What the guard said about the sentence it withheld: which tool it
    identified, and how many characters were not spoken. A list rather
    than a last-value, so "reported once" is a claim a case can make."""

    def __init__(self) -> None:
        self.calls: list[tuple[str | None, int]] = []

    def __call__(self, tool: str | None, characters: int) -> None:
        self.calls.append((tool, characters))


def withheld(sentence: str, tools: tuple[ToolDef, ...] = OFFERED) -> Reported | None:
    """The report for a withheld sentence, or None where it speaks.

    One helper for every row below, because the pair of answers has to
    agree: a guard that reported without withholding, or withheld
    without reporting, would be a sentence missing from a reply with
    nothing anywhere saying so.
    """
    report = Reported()
    if not withhold_tool_shaped(sentence, tools, report):
        assert report.calls == [], "a sentence that speaks reported nothing"
        return None
    assert len(report.calls) == 1, "a withheld sentence is reported exactly once"
    return report


# --- the named prong --------------------------------------------------


def test_a_compact_call_naming_an_offered_tool_is_withheld() -> None:
    sentence = json.dumps({"name": "remember", "arguments": {"text": "I like tea"}})

    report = withheld(sentence)

    assert report is not None
    assert report.calls == [("remember", len(sentence))]


def test_the_function_wrapped_shape_is_withheld() -> None:
    """The OpenAI wire shape, which models parrot back as prose. The
    name is a level down, so a guard reading the top level alone would
    speak it."""
    sentence = json.dumps(
        {
            "type": "function",
            "function": {"name": "remember", "arguments": '{"text": "I like tea"}'},
        }
    )

    report = withheld(sentence)

    assert report is not None
    assert report.calls == [("remember", len(sentence))]


def test_a_call_naming_a_tool_nobody_offered_is_spoken() -> None:
    """The narrowness, stated as a case. A sentence about a call to
    something this reply never had is a sentence about a call, and the
    keys here are nobody's properties either, so neither prong holds
    it."""
    assert withheld('{"name": "launch_the_missiles", "arguments": {}}') is None


# --- the argument-only prong ------------------------------------------


def test_the_observed_argument_only_payload_is_withheld() -> None:
    """The payload the issue was filed from, verbatim. It carries no
    name, and the value has the wrong type for the schema it matches, so
    a rule that required either would have spoken it."""
    sentence = '{"volume":"100"}'

    report = withheld(sentence)

    assert report is not None
    assert report.calls == [("self_audio_speaker_set_volume", len(sentence))]


def test_keys_that_fit_more_than_one_offered_tool_are_withheld_naming_none() -> None:
    """Ambiguity is withheld, because every reading of it is
    tool-shaped, and it names nothing, because which tool it was is what
    could not be decided."""
    both = ToolDef(
        name="update_memory",
        description="Correct one fact",
        input_schema={
            "type": "object",
            "properties": {"id": {"type": "integer"}, "text": {"type": "string"}},
        },
    )

    report = withheld('{"text": "I like tea"}', (REMEMBER, both))

    assert report is not None
    assert report.calls == [(None, len('{"text": "I like tea"}'))]


def test_json_whose_keys_are_no_offered_tools_is_spoken() -> None:
    assert withheld('{"latitude": 41.4, "longitude": 2.2}') is None


def test_an_empty_object_is_not_a_call() -> None:
    """Every schema trivially contains no keys, so a subset rule with no
    floor would withhold `{}` against the first tool on the list."""
    assert withheld("The answer is {}.") is None


def test_a_tool_declaring_no_properties_matches_nothing_by_arguments() -> None:
    """The other end of the same floor: a tool with no declared
    properties has an empty vocabulary, and a non-empty key set cannot
    fall inside it."""
    bare = ToolDef(name="recall_all", description="Everything", input_schema={})

    assert withheld('{"text": "I like tea"}', (bare,)) is None


# --- sentences that are not only a call -------------------------------


def test_a_call_embedded_beside_prose_is_withheld_whole() -> None:
    """The cut the splitter actually produces. The sentence does not
    parse, so "does this sentence parse" answers no for exactly the
    thing the guard exists for, and the whole cut goes: there is no
    honest way to speak the half of it that was an answer."""
    sentence = 'Sure: {"volume":"100"} Done.'

    report = withheld(sentence)

    assert report is not None
    assert report.calls == [("self_audio_speaker_set_volume", len(sentence))]


def test_prose_about_json_is_spoken() -> None:
    """The conversation the narrowness is for. Someone asking what JSON
    is gets an answer, braces and all."""
    assert (
        withheld(
            "JSON is a way of writing data down, using braces for objects "
            "and square brackets for lists."
        )
        is None
    )


def test_a_truncated_call_is_spoken() -> None:
    """The bound the module states rather than closes. The splitter cuts
    at a newline, so a pretty-printed call arrives as fragments no
    decoder can read, and each of them speaks."""
    assert withheld('{"name": "remember",') is None


def test_an_offered_call_nested_inside_prose_json_is_withheld() -> None:
    """The walk is over every `{` rather than the first, so a call one
    level down inside an object that is itself no tool's shape is still
    found."""
    sentence = json.dumps({"note": "here", "call": {"volume": "100"}})

    report = withheld(sentence)

    assert report is not None
    assert report.calls == [("self_audio_speaker_set_volume", len(sentence))]


def test_a_reply_that_offered_no_tools_speaks_everything() -> None:
    """The published set is what both prongs are anchored to, so a reply
    with nothing on the table has nothing to match against."""
    assert withheld('{"name": "remember", "arguments": {}}', ()) is None
