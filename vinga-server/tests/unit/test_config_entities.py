"""The descriptor registry, held to the surfaces it describes.

The registry is only worth having if it says what the rest of the
package says. Four relations are pinned here: that every descriptor
names a real domain key, that the command it carries reaches the
sentence the loader refuses a moved section with, that the two tiers
the documentation renders are the two tiers the registry declares, and
that stored secrets hang on exactly two kinds, which is a `Literal` in
one place and a descriptor fact in another.

The fifth is the module's own claim, and since #210 it is a claim
rather than an aspiration: a descriptor is whole the moment this module
is imported, with nothing installed onto it afterwards by a consumer.
"""

import dataclasses
import json
import subprocess
import sys
from pathlib import Path
from typing import get_args

import pytest

from vinga_server.config import docgen, entities, loader
from vinga_server.config.models import DOMAIN_KEYS
from vinga_server.config.secrets import EntityKind


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


def test_every_display_fact_names_a_field_the_shape_declares() -> None:
    """The two facts a display asks a shape about are field names, and a
    name that is not one is a rule about nothing: a lead field the model
    does not have would be reached for and raise, and a field held
    always-shown after it was renamed would silently stop being shown.
    Both are the kind of drift the registry exists to end, so they are
    checked against the model rather than trusted."""
    for shape in (*entities.ENTITIES, *entities.NESTED):
        declared = set(shape.model.model_fields)
        for fact, named in (
            ("leads_with", shape.leads_with),
            ("always_shown", shape.always_shown),
        ):
            assert declared.issuperset(named), f"{shape.name}.{fact}: {named}"


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


# What the registry may pull in: the models it declares its kinds
# against, and what those reach in turn. Named one by one rather than
# matched on a prefix, because each of the consumers that are absent is
# a separate way for the module to stop being readable on its own.
ALLOWED_IMPORTS = frozenset(
    {
        "vinga_server",
        "vinga_server.config",
        "vinga_server.config.entities",
        "vinga_server.config.loader",
        "vinga_server.config.models",
        "vinga_server.runtime",
        "vinga_server.runtime.prompt",
        "vinga_server.tools",
        "vinga_server.tools.names",
    }
)

# Run in a child interpreter that has imported the registry and nothing
# else. `-B` for the reason `test_onboarding_import_weight.py` gives: a
# child that writes bytecode back hands the next command the stale cache
# `conftest.py` just cleared.
_ALONE = """
import json
import sys
from dataclasses import fields

import vinga_server.config.entities as entities

print(json.dumps({
    "loaded": sorted(name for name in sys.modules if name.startswith("vinga_server")),
    "unset": {
        entry.name: sorted(
            field.name for field in fields(entry) if getattr(entry, field.name) is None
        )
        for entry in entities.ENTITIES
    },
}))
"""


def _registry_imported_alone() -> dict[str, object]:
    finished = subprocess.run(
        [sys.executable, "-B", "-c", _ALONE], capture_output=True, text=True, check=True
    )
    return json.loads(finished.stdout)


def test_the_registry_is_whole_on_its_own() -> None:
    """Importing the registry loads none of its consumers, and a
    descriptor holds the same facts there as it does here, where the
    whole package is loaded.

    The second half is the one that was false until #210: `store.py`,
    `views.py`, `cli.py` and `writes.py` installed forty-four callables
    and five sentences onto these descriptors at their own import,
    through a `fill` that reached past the frozen dataclass with
    `object.__setattr__`. A reader could not tell what a descriptor held
    without knowing what had been imported, and `docgen` rendered the
    committed reference from a registry four other modules were still
    allowed to write to. Compared as which facts are unset, because that
    is what filling one changed and it survives a trip through JSON.

    The four are imported here by name rather than relied on to be
    loaded by whatever else the run collected, since which of them a
    single-file run has imported is exactly the thing that used to
    decide what a descriptor held.
    """
    import vinga_server.config.api  # noqa: F401
    import vinga_server.config.cli  # noqa: F401
    import vinga_server.config.store  # noqa: F401
    import vinga_server.config.views  # noqa: F401
    import vinga_server.config.writes  # noqa: F401

    alone = _registry_imported_alone()

    assert frozenset(alone["loaded"]) == ALLOWED_IMPORTS
    assert alone["unset"] == {
        entry.name: sorted(
            field.name for field in dataclasses.fields(entry) if getattr(entry, field.name) is None
        )
        for entry in entities.ENTITIES
    }
