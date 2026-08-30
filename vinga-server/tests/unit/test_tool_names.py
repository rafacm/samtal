"""The one tool namespace: prefixes, sanitization, and what an
`mcp_servers` entry may be called."""

import pytest

from vinga_server.tools import names


@pytest.mark.parametrize(
    ("original", "expected"),
    [
        ("self.get_device_status", "self_get_device_status"),
        ("self.audio_speaker.set_volume", "self_audio_speaker_set_volume"),
        ("already_fine-1", "already_fine-1"),
        ("självklart.tänd", "sj_lvklart_t_nd"),
    ],
)
def test_device_names_are_sanitized_to_what_the_apis_accept(
    original: str, expected: str
) -> None:
    sanitized = names.sanitize(original)
    assert sanitized == expected
    assert names.TOOL_NAME_PATTERN.match(sanitized)


@pytest.mark.parametrize("name", ["ha", "weather-1", "Home_Assistant"])
def test_a_usable_entry_name_is_accepted(name: str) -> None:
    assert names.is_valid_entry_name(name)


@pytest.mark.parametrize(
    "name",
    [
        "self",
        "switch_agent",
        "remember",
        "set_state",
        "clear_state",
        "new_conversation",
        "resume_conversation",
        "home.assistant",
        "a b",
        "",
    ],
)
def test_a_reserved_or_unusable_entry_name_is_refused(name: str) -> None:
    # Reserved names are what makes collisions unrepresentable: an entry
    # called "self" could shadow a device tool, and one called
    # "switch_agent" a builtin. The set grows with the builtins, which is
    # a compatibility surface: an entry already called `set_state` meets
    # the refusal after an upgrade and has to be renamed. A name that
    # stops being a builtin becomes usable as an entry name again; what
    # that permits is an entry, never a bare tool, since an entry's tools
    # publish as `<entry>__<tool>` and an MCP tool is always qualified.
    assert not names.is_valid_entry_name(name)
    assert name in names.RESERVED_ENTRY_NAMES or not names.TOOL_NAME_PATTERN.match(name)


def test_a_server_tool_carries_its_entry_and_routes_back_to_it() -> None:
    qualified = names.qualified("ha", "turn_on_light")
    assert qualified == "ha__turn_on_light"
    assert names.owner_of(qualified, ["ha", "weather"]) == "ha"
    assert names.unqualified("ha", qualified) == "turn_on_light"


@pytest.mark.parametrize("name", ["remember", "self_get_device_status", "__tool", "entry__"])
def test_a_name_no_entry_qualifies_belongs_to_nobody(name: str) -> None:
    assert names.owner_of(name, ["entry", "ha"]) is None


def test_the_more_specific_entry_owns_a_name_both_could_publish() -> None:
    """An entry name may contain the separator, so two entries can
    publish one name: `home` listing a tool called `inside__turn_on`,
    and `home__inside` listing `turn_on`. It is the second one's
    namespace, whichever order the entries are given in."""
    name = "home__inside__turn_on"

    assert names.owner_of(name, ["home", "home__inside"]) == "home__inside"
    assert names.owner_of(name, ["home__inside", "home"]) == "home__inside"
    # And with only the outer entry configured it is that one's, since
    # nothing more specific exists to claim it.
    assert names.owner_of(name, ["home"]) == "home"
