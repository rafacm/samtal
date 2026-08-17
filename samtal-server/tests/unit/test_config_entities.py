"""The descriptor registry, held to the surfaces it describes.

The registry is only worth having if it says what the rest of the
package says. Three things are pinned here while the consumers are
still being moved onto it: that every descriptor names a real domain
key, that the command it carries reaches the sentence the loader
refuses a moved section with, and that the two tiers the documentation
renders are the two tiers the registry declares. The fourth is the
decision that stored secrets hang on exactly two kinds, which is a
`Literal` in one place and a descriptor fact in another.
"""

from pathlib import Path
from typing import get_args

import pytest

from samtal_server.config import docgen, entities, loader
from samtal_server.config.models import DOMAIN_KEYS
from samtal_server.config.secrets import EntityKind


def test_every_descriptor_names_a_domain_key() -> None:
    """A descriptor describes a key of the domain configuration, so a
    kind whose key is not one of them describes nothing."""
    named = [descriptor.moved_key for descriptor in entities.ENTITIES]
    named += [setting.name for setting in entities.SETTINGS]

    assert sorted(named) == sorted(DOMAIN_KEYS)


def test_the_loader_quotes_each_kinds_command_in_full(tmp_path: Path) -> None:
    """The loader quotes the command that writes a section it found
    still in the YAML file, and since M4 it reads that command off the
    registry rather than keeping a byte-identical copy of it, which is
    the duplication the descriptor's `command` exists to end.

    So the pin moved from the table to the sentence, where it is not a
    comparison of the derivation against itself: what the derivation has
    to preserve is what an operator reads, and this drives a real load
    of a real file per key and looks for the whole command string.
    `test_config.py` covers the same refusals at the level of which
    command is named; this is what says the string reaches the sentence
    neither truncated nor reworded.
    """
    commands = {descriptor.moved_key: descriptor.command for descriptor in entities.ENTITIES}
    commands |= {setting.name: setting.command for setting in entities.SETTINGS}
    assert sorted(commands) == sorted(DOMAIN_KEYS), "a moved key has no command to quote"

    for key, command in commands.items():
        path = tmp_path / f"{key}.yaml"
        path.write_text(f"server:\n  port: 9000\n{key}: {{}}\n", encoding="utf-8")

        with pytest.raises(loader.ConfigError) as refused:
            loader.load_file_config(path)

        assert f"write it with: {command}" in str(refused.value), key


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
