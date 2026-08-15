"""The rule both tool sources publish through.

Neither the device's firmware nor a third-party MCP server owes us
names the LLM APIs accept, and a bad one does not fail politely: it
fails the whole request. This is where that is caught, once, for both.
"""

import pytest

from samtal_server.tools import names
from samtal_server.tools.publish import publish

SCHEMA = {"type": "object", "properties": {}}

# Not a real credential, and shaped so a substring check for it cannot
# match by accident.
SENTINEL = "sk-live-3f2c8a1d-never-a-real-credential"


def listing(*tool_names: str) -> list[tuple[str, str, dict]]:
    return [(name, f"does {name}", SCHEMA) for name in tool_names]


@pytest.mark.parametrize(
    ("original", "prefix", "published"),
    [
        ("self.audio_speaker.set_volume", "", "self_audio_speaker_set_volume"),
        ("weather.today/v2", "ha", "ha__weather_today_v2"),
        ("turn_on_light", "ha", "ha__turn_on_light"),
        ("städa", "home", "home__st_da"),
    ],
)
def test_a_name_is_published_as_something_both_apis_accept(
    original: str, prefix: str, published: str
) -> None:
    result = publish(listing(original), prefix=prefix)
    assert [tool.name for tool in result.tools] == [published]
    assert names.TOOL_NAME_PATTERN.match(published)
    # And the call goes back out under the far side's own name.
    assert result.original_for(published) == original
    assert result.knows(published)


def test_the_length_cap_counts_the_prefix() -> None:
    # 60 characters is legal alone and too long under "server__", which
    # is the case a check on the unprefixed name would wave through.
    bare = "b" * 60
    assert len(bare) <= names.MAX_TOOL_NAME_LENGTH
    assert publish(listing(bare)).tools
    assert publish(listing(bare), prefix="server").tools == []


def test_a_collision_keeps_the_first_listed() -> None:
    # Two names can sanitize to the same thing; keeping the first makes
    # the outcome the same on every run.
    result = publish(listing("a.b", "a b", "a-b"))
    assert [tool.name for tool in result.tools] == ["a_b", "a-b"]
    assert result.original_for("a_b") == "a.b"


def test_a_name_that_sanitizes_to_nothing_is_dropped() -> None:
    result = publish(listing("", "...", "kept"))
    assert [tool.name for tool in result.tools] == ["___", "kept"]
    # An empty name has nothing left to publish under.
    assert not result.knows("")


def test_what_is_dropped_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    # A tool silently missing from the list is a question nobody can
    # answer from the outside.
    with caplog.at_level("WARNING"):
        publish(listing("kept", "x" * 70), prefix="ha", label="mcp server ha")
    assert "mcp server ha" in caplog.text
    # Which one, and why. The position is what says which one, since the
    # name itself is not printed.
    assert "tool 2 in the listing" in caplog.text
    assert "64" in caplog.text


def test_a_name_that_does_not_publish_never_reaches_the_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A far side chooses the names it lists, and one holding a
    credential this deployment gave it can put the credential there. A
    name that publishes crosses anyway, because the model has to be
    given it and an operator has to be able to write it down; a name
    that is refused has no such claim, and the refusal must not be the
    thing that publishes it."""
    with caplog.at_level("WARNING"):
        publish(listing("kept", SENTINEL + "n" * 70, ""), prefix="ha", label="mcp server ha")

    # Both drops were reported, so the absence below is not the absence
    # of any line at all.
    assert len(caplog.records) == 2
    assert SENTINEL not in caplog.text


def test_a_collision_names_only_what_already_published(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The one drop that may print a name, because the name is not the
    dropped tool's: an earlier one published under it, so it is on the
    connect line and in front of the model already."""
    with caplog.at_level("WARNING"):
        publish(listing("weather.today", "weather/today"), prefix="ha", label="mcp server ha")

    assert "weather/today" not in caplog.text
    assert "ha__weather_today" in caplog.text


def test_a_published_tool_remembers_where_the_far_side_listed_it() -> None:
    """The identifier every line about a tool is written in, because it
    is the only one this side owns.

    It has to survive publication rather than be recovered from the
    published list afterwards: the published list is the listing with
    the drops taken out, so counting it answers with a position the far
    side's listing never had. Here the second of three never publishes,
    and the third has to still be the third."""
    result = publish(listing("first", "b" * 70, "third"), prefix="ha")

    assert [tool.name for tool in result.tools] == ["ha__first", "ha__third"]
    assert result.position_of("ha__first") == 1
    assert result.position_of("ha__third") == 3
    # Counting the published list would have said two.
    assert result.position_of("ha__unknown") is None


def test_descriptions_and_schemas_pass_through() -> None:
    (tool,) = publish([("do.it", "the description", SCHEMA)], prefix="ha").tools
    assert tool.description == "the description"
    assert tool.input_schema is SCHEMA
