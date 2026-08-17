"""The descriptor registry, held to the surfaces it describes.

The registry is only worth having if it says what the rest of the
package says. Three things are pinned here while the consumers are
still being moved onto it: that every descriptor names a real domain
key, that the command it carries is byte for byte the one the loader
quotes for that key, and that the two tiers the documentation renders
are the two tiers the registry declares. The fourth is the decision
that stored secrets hang on exactly two kinds, which is a `Literal` in
one place and a descriptor fact in another.
"""

from typing import get_args

from samtal_server.config import docgen, entities, loader
from samtal_server.config.models import DOMAIN_KEYS
from samtal_server.config.secrets import EntityKind


def test_every_descriptor_names_a_domain_key() -> None:
    """A descriptor describes a key of the domain configuration, so a
    kind whose key is not one of them describes nothing."""
    named = [descriptor.moved_key for descriptor in entities.ENTITIES]
    named += [setting.name for setting in entities.SETTINGS]

    assert sorted(named) == sorted(DOMAIN_KEYS)


def test_the_registry_carries_the_loaders_moved_key_commands() -> None:
    """The loader quotes the command that writes a section it found
    still in the YAML file. That string and the one the reference prints
    are byte-identical today and kept apart by nothing, which is the
    duplication the descriptor's `command` exists to end; until the
    loader derives it (M4), this is what keeps the two the same
    sentence."""
    commands = {descriptor.moved_key: descriptor.command for descriptor in entities.ENTITIES}
    commands |= {setting.name: setting.command for setting in entities.SETTINGS}

    assert commands == loader.MOVED_KEY_COMMANDS


def test_the_documented_shapes_are_the_two_entity_tiers() -> None:
    """What the reference documents is the commanded kinds followed by
    the nested ones, and nothing else: a shape added to a tier is a
    section in the document, and a shape in neither is invisible."""
    assert docgen.ENTITIES == (*entities.ENTITIES, *entities.NESTED)

    names = [shape.name for shape in docgen.ENTITIES]
    assert len(names) == len(set(names)), f"a name is used twice: {', '.join(names)}"


def test_exactly_two_kinds_can_hold_a_stored_secret() -> None:
    """The two-member `EntityKind` is the decision; the descriptor fact
    is the same decision written where a generic write path reads it,
    and a third member added to one and not the other would be a kind
    whose secrets nothing addresses."""
    holders = {
        descriptor.secret_slots
        for descriptor in entities.ENTITIES
        if descriptor.secret_slots is not None
    }

    assert holders == set(get_args(EntityKind))
