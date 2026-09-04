"""The committed bodies, and the promise they are committed for.

An entity is stored as its model dumped to JSON (#243). That makes the
model the compatibility surface: nothing about a stored row is written
down anywhere else any more, so what used to be checked by a migration
and a column is checked here instead, by keeping real bodies in the
repository and parsing them with today's models on every run.

The promise is forward-only, and it is the one a beta will stand on.
Every body under `data/domain-bodies/` validates through the model of
its kind, and a change to a model that cannot parse one fails this suite
until either the model tolerates it or the fixture is deliberately
updated with a compatibility decision recorded beside the change. There
is no backward promise and no migration to write: a body carries what
the operator wrote, and a field added to a model is absent from every
body already stored, which is what its declared default is for.

The fixtures are laid out one directory per entity kind, named for the
kind as the registry names it, so the kind a body belongs to is read off
the tree rather than restated in a table here. Each file is a body
exactly as a row holds one.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import get_args

import pytest
from pydantic import BaseModel
from sqlalchemy import insert, update

from tests.support.stores import body, planted
from vinga_server.config import entities
from vinga_server.config.entities import EntityDescriptor
from vinga_server.config.loader import StorageError
from vinga_server.config.models import (
    PROVIDER_STAGES,
    DatabaseConfig,
    McpServerConfig,
    is_env_name,
    is_mcp_secret_key,
    mcp_entry_fragment,
    url_credential,
)
from vinga_server.config.provider_options import checked_options, declared_options
from vinga_server.config.store import ConfigStore
from vinga_server.db import open_database, schema
from vinga_server.providers.registry import registration

BODIES = Path(__file__).parent / "data" / "domain-bodies"

# Not a real credential, and shaped so a substring check for it cannot
# match by accident. Planted inside a body that will not validate, where
# a refusal quoting the row would carry it out.
SECRET = "sk-test-9a41c7e0-never-a-real-credential"

# The identity a planted row is written under, per kind: a value for
# each of the parameters the kind is addressed by.
#
# The provider is the one kind whose identity is not a constant, and the
# reason is #88: a provider's options are checked against the model its
# STAGE and type declare, so a body planted under the wrong stage would
# be a body nothing checks. The stage is read off the registry by the
# body's own type below, which is also the one place that mapping lives.
IDENTITIES = {
    "mcp-server": ("planted",),
    "prompt-fragment": ("planted",),
    "agent": ("planted",),
    "agent-defaults": (),
}


def _written_type(path: Path) -> str:
    return str(json.loads(path.read_text(encoding="utf-8"))["type"])


def _identity(descriptor: EntityDescriptor, path: Path) -> tuple[str, ...]:
    """Where one fixture's row is planted, which for a provider carries
    the stage its type is registered in."""
    if descriptor.name != "provider":
        return IDENTITIES[descriptor.name]
    written = _written_type(path)
    stage = next(
        (one for one in PROVIDER_STAGES if registration(one, written) is not None), None
    )
    assert stage is not None, f"{path}: no stage registers the type {written}"
    return (stage, "planted")


def _fixtures() -> list[tuple[EntityDescriptor, Path]]:
    """Every committed body with the descriptor of the kind whose
    directory it sits in."""
    found = [
        (entities.descriptor(directory.name), path)
        for directory in sorted(BODIES.iterdir())
        if directory.is_dir()
        for path in sorted(directory.glob("*.json"))
    ]
    assert found, f"no bodies under {BODIES}"
    return found


FIXTURES = _fixtures()
KINDS = sorted({descriptor.name for descriptor, _ in FIXTURES})


def _identifier(descriptor: EntityDescriptor, path: Path) -> str:
    return f"{descriptor.name}/{path.stem}"


IDS = [_identifier(descriptor, path) for descriptor, path in FIXTURES]


@pytest.fixture
def store(tmp_path: Path) -> Iterator[ConfigStore]:
    engine = open_database(DatabaseConfig())
    try:
        yield ConfigStore(engine)
    finally:
        engine.dispose()


def _row_identity(descriptor: EntityDescriptor, identity: tuple[str, ...]) -> dict[str, object]:
    if not descriptor.addressing:
        return {"id": schema.AGENT_DEFAULTS_ID}
    return dict(zip(descriptor.addressing, identity, strict=True))


def _plant(
    store: ConfigStore,
    descriptor: EntityDescriptor,
    written: str,
    identity: tuple[str, ...],
) -> None:
    """One row holding `written`, created or replaced.

    Written as a row rather than through a write path, deliberately and
    for the reason the whole family exists: what is under test is the
    reader, and a body produced by today's writer is the one body that
    cannot show whether an older one still parses.
    """
    table = getattr(schema, descriptor.table)
    columns = _row_identity(descriptor, identity)
    where = [table.c[column] == value for column, value in columns.items()]
    planted(store, table.delete().where(*where))
    planted(store, insert(table).values(**columns, body=written))


def _loaded(
    store: ConfigStore, descriptor: EntityDescriptor, identity: tuple[str, ...]
) -> BaseModel:
    """The planted entry as a load gives it back, through the public
    read the server itself boots on."""
    domain = store.load().domain
    if not descriptor.addressing:
        return domain.agent_defaults
    section = getattr(domain, descriptor.moved_key)
    for group in identity[:-1]:
        section = getattr(section, group)
    return section[identity[-1]]


def test_the_floor_covers_every_entity_kind_and_both_transports() -> None:
    """The inventory, asserted against the registry rather than against
    the tree.

    Everything else in this file is parameterized over whichever
    directories happen to be on disk, so the whole family would stay
    green if a kind's directory were deleted: there would simply be
    nothing to run for it. A floor that can be lowered by deleting a file
    is not one. The registry is the list of kinds that exist, so it is
    the list of kinds that need bodies.

    The transports are the same question one level down. `McpServerConfig`
    is really two shapes behind one model, and a body naming the fields
    of both is one the model refuses, so "every kind is covered" is not
    true of it unless both values appear. The pair is read off the field's
    own annotation rather than written out here, for the reason the kinds
    are read off the registry.
    """
    assert KINDS == sorted(descriptor.name for descriptor in entities.ENTITIES)

    transports = {
        json.loads(path.read_text(encoding="utf-8"))["transport"]
        for descriptor, path in FIXTURES
        if descriptor.name == "mcp-server"
    }
    declared = set(get_args(McpServerConfig.model_fields["transport"].annotation))
    assert transports == declared


@pytest.mark.parametrize(("descriptor", "path"), FIXTURES, ids=IDS)
def test_every_committed_body_validates_through_todays_model(
    descriptor: EntityDescriptor, path: Path
) -> None:
    """The floor. A model change that cannot read a stored body fails
    here, which is the whole of what "the compatibility surface is the
    model" costs."""
    descriptor.model.model_validate_json(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize(("descriptor", "path"), FIXTURES, ids=IDS)
def test_a_body_round_trips_through_the_mapper_pair(
    store: ConfigStore, descriptor: EntityDescriptor, path: Path
) -> None:
    """Validate, dump, validate: the second entry is the first.

    This inherits the claim `test_db_open.py` used to make with
    `test_provider_rows_hold_every_declared_model_field`, that every
    declared field of an entity has somewhere in its row to live. It is
    no longer a check but a consequence, and it holds for all five kinds
    rather than for the one that had the most columns: a field missing
    from the row would be a field missing from the model's own dump.

    Both halves run through the store rather than beside it, so what is
    pinned is the pair the repository actually uses: the read is a real
    `store.load()`, and the dump `stores.body` performs is `_to_row`
    itself rather than a support-module copy of its decision. The second
    write is where `exclude_unset` earns its place, and removing it from
    `_to_row` fails eight of these: dumping every field writes `url:
    null` onto a stdio entry, and the second load refuses the entry its
    own writer had just produced.
    """
    written = path.read_text(encoding="utf-8")
    identity = _identity(descriptor, path)
    _plant(store, descriptor, written, identity)

    first = _loaded(store, descriptor, identity)
    assert first == descriptor.model.model_validate_json(written)

    _plant(store, descriptor, body(first), identity)
    second = _loaded(store, descriptor, identity)

    assert second == first
    assert second.model_fields_set == first.model_fields_set


@pytest.mark.parametrize(
    ("descriptor", "path"),
    [(one, path) for one, path in FIXTURES if one.name == "provider"],
    ids=[name for name, (one, _) in zip(IDS, FIXTURES, strict=True) if one.name == "provider"],
)
def test_every_provider_body_survives_the_validator_its_type_declares(
    descriptor: EntityDescriptor, path: Path
) -> None:
    """The floor, one level down, for the half a model cannot state.

    A provider's options are `model_extra` as far as `ProviderConfig` is
    concerned, so the case above passes on a body whose options the
    type's own model would refuse. This runs the same public validator a
    read-back runs (#88), which is what makes a tightening of a type's
    options a failure here rather than a deployment that will not boot.
    """
    stage, _ = _identity(descriptor, path)
    entry = descriptor.model.model_validate_json(path.read_text(encoding="utf-8"))
    checked_options(
        f"{path.name}:", stage, _written_type(path), entry.options  # type: ignore[attr-defined]
    )


def test_the_options_no_builder_accepted_are_the_ones_that_now_travel() -> None:
    """The oldest provider fixture, and what typing its type did to it.

    `every-field.json` was written as an `openai_compatible` entry
    carrying options no builder read: it existed to prove a body could
    hold anything and still parse. Under #88 that type is the escape
    hatch, so the same body parses unchanged and those keys stop being
    inert: they are `model_extra` on the type's own model, which is what
    the provider forwards into the outgoing request. That is why this
    batch needed no compatibility decision for it, and why the claim is
    asserted rather than written in a plan: a model that closed its door
    would fail the case above, and one that kept the door open and
    dropped the keys would fail this one.
    """
    path = BODIES / "provider" / "every-field.json"
    descriptor = entities.descriptor("provider")
    stage, _ = _identity(descriptor, path)
    entry = descriptor.model.model_validate_json(path.read_text(encoding="utf-8"))

    options = checked_options(f"{path.name}:", stage, _written_type(path), entry.options)  # type: ignore[attr-defined]

    assert options is not None
    assert options.model_extra == {
        "temperature": 0.7,
        "max_reply_length": 512,
        "stop": ["\n\n"],
        "connection": {"retries": 2, "timeout_s": None},
    }


def test_every_declared_type_has_a_body_of_its_own() -> None:
    """A type that gains an option model gains a compatibility surface,
    so it gains a fixture: a body in the shape a deployment already has,
    including a key the model does not declare and forwards anyway.

    Read off the registry rather than listed here, so the next type to
    convert brings this assertion with it (#88).
    """
    written = {
        _written_type(path) for descriptor, path in FIXTURES if descriptor.name == "provider"
    }
    declared = {type_name for _, type_name, _ in declared_options()}

    assert declared <= written, (
        "no committed body for: " + ", ".join(sorted(declared - written))
    )


@pytest.mark.parametrize("kind", KINDS)
def test_each_kind_has_a_sparse_body_and_a_written_out_one(kind: str) -> None:
    """The two ends of what a body can say, because they mean different
    things and one of them is a promise about the future.

    A sparse body sets only what the model and its own validators demand,
    so what it reads as depends on today's defaults: change a default and
    every such body changes meaning, exactly as an absent column always
    did. A written-out one depends on none of them for the fields it
    names. Keeping both per kind is what keeps that consequence visible
    rather than discovered, and it is why the committed files go to both
    ends: the sparse ones are as short as their model allows, and the
    written-out ones name every field of the kind they were written for,
    which for an MCP server means one file per transport since a body
    naming both is one its own model refuses.

    What is asserted is that both ends exist, and deliberately not that
    the written-out body still names every field the model declares. A
    field added to a model is absent from every body already stored, and
    a check that demanded the fixture grow with the model would turn the
    floor upside down: these files exist to be old.

    A kind with no optional field has one body by construction, and both
    assertions below are vacuously true of it. `PromptFragmentConfig` is
    that kind: it declares one required field, so there is no second end
    for it to have and nothing here to say about it. What holds that kind
    is `test_the_floor_covers_every_entity_kind_and_both_transports`,
    which requires it to have a body at all.
    """
    model = entities.descriptor(kind).model
    required = {name for name, field in model.model_fields.items() if field.is_required()}
    optional = set(model.model_fields) - required
    keys = [
        set(json.loads(path.read_text(encoding="utf-8")))
        for descriptor, path in FIXTURES
        if descriptor.name == kind
    ]

    assert not optional or any(optional - written for written in keys), (
        f"{kind} has no body that leans on a default"
    )
    assert not optional or any(written & optional for written in keys), (
        f"{kind} has no body that writes an optional field out"
    )


@pytest.mark.parametrize(("descriptor", "path"), FIXTURES, ids=IDS)
def test_no_committed_body_holds_a_credential(
    descriptor: EntityDescriptor, path: Path
) -> None:
    """These files are in the repository forever, so the shape of a
    credential is the one thing they may not carry.

    What a secret-shaped key may hold is what the models allow anywhere
    else: an environment reference, or the name of the variable holding
    the value. Both are names rather than values, which is the whole
    reason the configuration is written that way.

    A URL is the shape that gets past that, and so it is asked of every
    string rather than of the secret-shaped keys: a credential written
    into `url` or `base_url` sits under a key that admits to nothing
    (#279). The predicate is the one both write paths refuse with, so a
    fixture these files could not be written by today is a failure here
    rather than a discovery.
    """
    for where, value in _strings(json.loads(path.read_text(encoding="utf-8"))):
        assert url_credential(value) is None, f"{path}: {where}"
        if not is_mcp_secret_key(where):
            continue
        assert value.startswith("$") or is_env_name(value), f"{path}: {where}"


def _strings(value: object, key: str = "") -> Iterator[tuple[str, str]]:
    """Every string in a body with the key it was written under."""
    if isinstance(value, dict):
        for name, nested in value.items():
            yield from _strings(nested, str(name))
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item, key)
    elif isinstance(value, str):
        yield key, value


def test_an_mcp_list_written_as_plain_names_loads_and_is_written_back_unchanged(
    store: ConfigStore,
) -> None:
    """Every body written before the object form existed holds a plain
    list of names, which is why the string form is stored as a string and
    not normalized into an object: there is no migration to run.

    This pin used to live beside the upgrade tests and looked like one of
    them. It is not: what it holds is a live tolerance of the model, so
    it belongs here, with the bodies it is a tolerance for, rather than
    with the chain the squash deleted.
    """
    descriptor = entities.descriptor("agent")
    identity = IDENTITIES["agent"]
    written = (BODIES / "agent" / "mcp-written-as-plain-names.json").read_text(encoding="utf-8")
    _plant(store, descriptor, written, identity)

    entry = _loaded(store, descriptor, identity)

    assert [mcp_entry_fragment(item) for item in entry.mcp] == ["home", "weather"]  # type: ignore[attr-defined]

    # And back out through the writer, which is where a normalization
    # into the object form would have shown up.
    _plant(store, descriptor, body(entry), identity)
    assert json.loads(_stored_body(store, descriptor))["mcp"] == ["home", "weather"]


def _stored_body(store: ConfigStore, descriptor: EntityDescriptor) -> str:
    table = getattr(schema, descriptor.table)
    with store._engine.connect() as connection:
        return connection.execute(table.select().with_only_columns(table.c.body)).scalar_one()


# The read refusal, and what it may not carry
#
# A body is the whole entity rather than one column of it, so a refusal
# that quoted "the row" would now quote everything an operator ever wrote
# into that entity. The sentinel below is the pin for that: a credential
# planted inside a body that will not validate, looked for in the whole
# chain behind the exception and not only in its message.


def test_a_refusal_about_a_body_names_the_entity_and_never_the_body(
    store: ConfigStore,
) -> None:
    _plant(store, entities.descriptor("provider"), '{"type": "anthropic"}', ("llm", "planted"))
    planted(
        store,
        update(schema.providers).values(
            body=json.dumps({"type": "", "api_key": SECRET, "note": SECRET})
        ),
    )

    with pytest.raises(StorageError) as caught:
        store.load()

    rendered = _chain(caught.value)
    assert "providers.llm.planted" in str(caught.value)
    assert "cannot be read as configuration" in str(caught.value)
    assert SECRET not in rendered
    # The key an operator invented is as good a place to have pasted one,
    # so it is not named either.
    assert "api_key" not in rendered


def test_a_stored_option_a_type_no_longer_accepts_names_the_entry_and_a_way_out(
    store: ConfigStore,
) -> None:
    """The break this batch prices at zero, and the recovery it does not.

    A deployment that wrote a passthrough key under a type that now
    declares a model has a row this server refuses to read. The pre
    release stance says that breakage is acceptable; it says nothing
    about leaving an operator with a server that will not start and no
    way to edit the row, so the way out is asserted here rather than
    assumed: the refusal names the entry, and the delete that does not
    read the row takes it away.
    """
    descriptor = entities.descriptor("provider")
    identity = ("asr", "planted")
    _plant(
        store,
        descriptor,
        json.dumps({"type": "faster_whisper", "beam_size": SECRET}),
        identity,
    )

    with pytest.raises(StorageError) as caught:
        store.load()

    # The boot path prints this sentence as it is, so what it may carry
    # is the entry, the field and the rule, and nothing of the value.
    assert "providers.asr.planted" in str(caught.value)
    assert "beam_size" in str(caught.value)
    assert SECRET not in _chain(caught.value)

    store.delete_provider(*identity)

    assert "planted" not in store.load().domain.providers.asr


def test_a_body_that_is_not_json_at_all_refuses_without_quoting_it(
    store: ConfigStore,
) -> None:
    """The other half: a parse that never reached a field. Pydantic
    reports where the parse stopped rather than what it was reading,
    which is what makes this refusal safe to print."""
    _plant(
        store,
        entities.descriptor("prompt-fragment"),
        f"not json, just {SECRET}",
        IDENTITIES["prompt-fragment"],
    )

    with pytest.raises(StorageError) as caught:
        store.load()

    assert "prompt_fragments.planted" in str(caught.value)
    assert SECRET not in _chain(caught.value)


def _chain(exc: BaseException) -> str:
    parts: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        parts += [repr(current), str(current)]
        current = current.__cause__ or current.__context__
    return "\n".join(parts)
