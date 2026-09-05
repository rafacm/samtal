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
Two tests hold it, because there are two ways to break it. A consumer
can reach past the frozen dataclass with `object.__setattr__`, which is
what `fill` did, and that is caught by comparing every fact of every
entry against a child interpreter that imported the registry alone. Or
the dataclass can stop being frozen, at which point ordinary assignment
does it, and that is caught by assigning.
"""

import dataclasses
import inspect
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, get_args

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

    In the server's spelling, because this refusal is a boot failure and
    its reader is watching a container. What the descriptor decides is
    the noun, the verb and everything after them; the program word is
    the loader's, and `served` is the one place it is put on.
    """
    commands = {
        descriptor.moved_key: loader.served(descriptor.command)
        for descriptor in entities.ENTITIES
    }
    commands |= {setting.name: loader.served(setting.command) for setting in entities.SETTINGS}
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


def test_the_options_note_names_the_types_that_declare_a_model() -> None:
    """The one sentence in this registry that is prose about another
    one.

    `OPTIONS_NOTE` names the types that declare an options model, and
    the names are read out of the declaration rather than written into
    the sentence, so the first half below is a consequence rather than a
    check. It is asserted anyway, because what it holds is that the
    reading is still happening: a sentence that stopped naming them
    would document every type as passed-through.

    The second half is the one that could go wrong on its own. A type
    named in this sentence that declares no model would send a reader to
    a schema command that refuses, and the registry is the only place
    that knows which types exist at all.
    """
    from vinga_server.config.provider_options import declared_options
    from vinga_server.providers.registry import _registrations

    declared = {(stage, type_name) for stage, type_name, _ in declared_options()}
    assert declared, "no type declares an options model, so this check is vacuous"

    for stage, type_name in declared:
        assert f"{stage} {type_name}" in entities.OPTIONS_NOTE, (
            f"{stage} {type_name} is not in the note"
        )

    # Asked as the pair, which is how the note writes one and the only
    # spelling that can be looked for honestly: a type name on its own
    # is a substring of another type's name (`openai` of
    # `openai_compatible`), so a scan for the bare word reports a typed
    # neighbour as an untyped type that got itself documented.
    untyped = {
        f"{stage} {type_name}"
        for stage, types in _registrations().items()
        for type_name in types
        if (stage, type_name) not in declared
    }
    named = sorted(one for one in untyped if one in entities.OPTIONS_NOTE)
    assert not named, f"the note names types that declare no model: {', '.join(named)}"


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
        # The same one name, for the same reason, added by the same
        # change: the registry's provider entry says which types declare
        # an options model, and it derives that from the declaration
        # rather than restating it. See the note in
        # `test_config_docgen.py`; the module is pydantic and
        # `config.models`, and none of the consumers this pin exists to
        # keep out is reachable through it.
        "vinga_server.config.provider_options",
        # And the one name added by #386, for a reason of the same
        # shape: a kind's notice carries the boundaries it announces as
        # the tokens the API publishes, which are declared in
        # `responses`, and that module imports pydantic and nothing of
        # this server.
        "vinga_server.config.responses",
        "vinga_server.runtime",
        "vinga_server.runtime.prompt",
        "vinga_server.tools",
        "vinga_server.tools.names",
    }
)


def _facts(entries: Sequence[Any]) -> dict[str, dict[str, object]]:
    """Every declared fact of every entry in one registry tuple, as
    something JSON can carry and two interpreters can compare.

    Every field, not the ones that happen to be None: a fact installed
    over a declared value is as much a fact arriving after declaration
    as one installed over a default, and a comparison of unset names
    would see neither the second nor a changed sentence.

    A model and a predicate are compared as their qualified names. Both
    are identities rather than values here: which model owns the shape
    and which rule decides that a key carries a credential, and a name
    that moved module is a change worth failing on. A notice is compared
    field by field, because both of its halves are declared facts: the
    sentence a write is answered with, and the boundaries it announces.
    """

    def readable(value: object) -> object:
        if isinstance(value, type):
            return f"{value.__module__}.{value.__qualname__}"
        if callable(value):
            return f"{value.__module__}.{value.__qualname__}"
        if dataclasses.is_dataclass(value):
            return {
                field.name: readable(getattr(value, field.name))
                for field in dataclasses.fields(value)
            }
        if isinstance(value, tuple):
            return [readable(item) for item in value]
        return value

    return {
        entry.name: {  # type: ignore[attr-defined]
            field.name: readable(getattr(entry, field.name))
            for field in dataclasses.fields(entry)
        }
        for entry in entries
    }


def _registry_facts() -> dict[str, dict[str, dict[str, object]]]:
    """All three tiers, so that a fact arriving after declaration is
    caught wherever it lands."""
    return {
        "entities": _facts(entities.ENTITIES),
        "nested": _facts(entities.NESTED),
        "settings": _facts(entities.SETTINGS),
    }


# Run in a child interpreter that has imported the registry and nothing
# else. The two functions above travel into it as their own source, so
# there is one definition of what a fact serializes to rather than one
# per side of the comparison. `-B` for the reason
# `test_onboarding_import_weight.py` gives: a child that writes bytecode
# back hands the next command the stale cache `conftest.py` just
# cleared.
_ALONE = "\n".join(
    (
        "import dataclasses",
        "import json",
        "import sys",
        "from collections.abc import Sequence",
        "from typing import Any",
        "",
        "import vinga_server.config.entities as entities",
        "",
        inspect.getsource(_facts),
        inspect.getsource(_registry_facts),
        "",
        "print(json.dumps({",
        '    "loaded": sorted(n for n in sys.modules if n.startswith("vinga_server")),',
        '    "facts": _registry_facts(),',
        "}))",
    )
)


def _registry_imported_alone() -> dict[str, object]:
    finished = subprocess.run(
        [sys.executable, "-B", "-c", _ALONE], capture_output=True, text=True, check=True
    )
    return json.loads(finished.stdout)


def test_the_registry_is_whole_on_its_own() -> None:
    """Importing the registry loads none of its consumers, and every
    entry holds exactly the facts there that it holds here, where the
    whole package is loaded.

    The second half is the one that was false until #210: `store.py`,
    `views.py`, `cli.py` and the since-deleted `writes.py` installed
    forty-four callables and five sentences onto these descriptors at
    their own import, through a `fill` that reached past the frozen
    dataclass with `object.__setattr__`. A reader could not tell what a
    descriptor held without knowing what had been imported, and `docgen`
    rendered the committed reference from a registry four other modules
    were still allowed to write to.

    The consumers are imported here by name rather than relied on to be
    loaded by whatever else the run collected, since which of them a
    single-file run has imported is exactly the thing that used to
    decide what a descriptor held.
    """
    import vinga_server.config.api  # noqa: F401
    import vinga_server.config.cli  # noqa: F401
    import vinga_server.config.store  # noqa: F401
    import vinga_server.config.views  # noqa: F401

    alone = _registry_imported_alone()

    assert frozenset(alone["loaded"]) == ALLOWED_IMPORTS
    assert alone["facts"] == json.loads(json.dumps(_registry_facts()))


def test_a_declared_fact_cannot_be_written_over() -> None:
    """The other way the claim breaks, and the one the comparison above
    cannot see: a dataclass that stopped being frozen needs no
    `object.__setattr__` to be written to, and a consumer that filled it
    by ordinary assignment would leave both processes agreeing on the
    same wrong value.

    Every tier, because `frozen=True` is per class and a tier that lost
    it would be a tier a consumer could write to.
    """
    for entry in (*entities.ENTITIES, *entities.NESTED, *entities.SETTINGS):
        with pytest.raises(dataclasses.FrozenInstanceError):
            entry.name = "written-over"  # type: ignore[misc]
