"""The one tool namespace: prefixes, sanitization, and what an
`mcp_servers` entry may be called."""

import pytest

from samtal_server.tools import names


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


@pytest.mark.parametrize("name", ["self", "switch_agent", "remember", "home.assistant", "a b", ""])
def test_a_reserved_or_unusable_entry_name_is_refused(name: str) -> None:
    # Reserved names are what makes collisions unrepresentable: an entry
    # called "self" could shadow a device tool, and one called
    # "switch_agent" a builtin.
    assert not names.is_valid_entry_name(name)


def test_a_server_tool_carries_its_entry_and_routes_back_to_it() -> None:
    qualified = names.qualified("ha", "turn_on_light")
    assert qualified == "ha__turn_on_light"
    assert names.split_qualified(qualified) == ("ha", "turn_on_light")


@pytest.mark.parametrize("name", ["remember", "self_get_device_status", "__tool", "entry__"])
def test_an_unqualified_name_does_not_split(name: str) -> None:
    assert names.split_qualified(name) is None
