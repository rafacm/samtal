"""Which database this lane's tests write to, and which lanes ask for
one at all.

Every other test in both lanes takes it for granted that the store it
opens is the one the autouse truncation clears. Nothing said so, and for
one milestone it was not true: `tests/conftest.py` moved
`DatabaseConfig`'s model default onto a per-worker database and left
`VINGA_DB_NAME` alone, while the loader reads that variable OVER the
model default. Locally the variable is unset and the two answers agreed
by accident. CI exports `VINGA_DB_NAME: vinga` for the contract's sake,
so there they disagreed: everything composed the way a deployment
composes it wrote to the job's shared `vinga` database, the truncation
cleared a per-worker database nothing was writing to, and four workers
read each other's rows. Thirty tests failed on symptoms that named
nothing about a database.

So the fact gets a pin of its own, and it is deliberately about the
AGREEMENT rather than about either half: two doors reach the database
name, and a test asserting only the one it happens to use would have
gone on passing through the whole of that failure.

There turned out to be a third (#333). Pydantic inlines a sub-model's
schema, defaults included, into every embedding model's compiled
validator at class creation, so a composition whose payload carries a
`database` mapping that omits fields (`Config(server={"database": {}})`)
filled them from a stale inlined copy and booted against the compose
instance's real `vinga` database, however carefully the other two doors
agreed. Around twenty-three boot tests travelled it, one of them
asserting a row count against a database it had never booted into. So
the file now pins the payload door too, all four connection facts
rather than the name alone, and the completeness rule that says a
fourth embedder cannot be added without the cascade hearing about it.

This file reaches for the conftest's private `_database_condition` and
its rebuild manifest, which a test of anything else should not do: the
conftest's own mechanism is the subject here, and a pin written only
against what a caller sees is exactly the pin that passed through the
whole of both failures.
"""

import os
import sys
from pathlib import Path
from typing import Any, get_args

from pydantic import BaseModel

from tests.conftest import (
    DATABASE_REBUILD_ORDER,
    DB_HOST,
    DB_NAME_ENV,
    DB_PORT,
    DB_URL_ENV,
    DB_USER,
    LANE_DATABASE,
    PACKAGED_CONNECTION,
    _database_condition,
    _database_default,
)
from tests.support.commands import ran
from tests.support.configs import config_with_agent
from vinga_server.config.loader import load_file_config
from vinga_server.config.models import Config, DatabaseConfig, FileConfig, ServerConfig


def test_both_doors_to_the_database_name_answer_this_lane_s_own() -> None:
    """The model default a `Config(...)` takes, the environment the
    loader reads over it, and the name the truncation clears: one
    database, said three ways."""
    assert DatabaseConfig().name == LANE_DATABASE
    assert os.environ[DB_NAME_ENV] == LANE_DATABASE
    assert load_file_config(None).server.database.name == LANE_DATABASE


def test_the_payload_door_answers_this_lane_through_every_embedder() -> None:
    """The third door, at each of the three models that inline
    `DatabaseConfig`'s defaults, plus the helper the boot suites compose
    through.

    An empty `database` mapping rather than an absent one, because the
    absent case travels the `default_factory` (a call-time callable,
    which was never broken) while a mapping with fields missing is
    filled from whatever the embedding model's validator inlined. That
    is the difference the whole of #333 is, and it is invisible from
    the outside: both spellings mean "the defaults".

    The name is asserted equal to this worker's own database rather
    than merely inside the run's prefix. Another worker's database also
    carries the prefix, and writing into it is exactly as wrong.
    """
    assert ServerConfig(**{"database": {}}).database.name == LANE_DATABASE
    assert (
        FileConfig(**{"server": {"database": {}}}).server.database.name == LANE_DATABASE
    )
    assert Config(server={"database": {}}).server.database.name == LANE_DATABASE
    assert (
        config_with_agent(server={"database": {}}).server.database.name == LANE_DATABASE
    )


# Four facts that look like nothing this lane or a deployment would
# ever hold, so a field that failed to travel is a mismatch rather than
# a coincidence. Nothing connects on them: the tests below drive the
# defaults and read them back.
SENTINEL_CONNECTION = {
    "host": "10.255.255.254",
    "port": 15432,
    "name": "vinga_sentinel_never_created",
    "user": "vinga_sentinel_role",
}

# What a partial payload states for itself, distinct from the sentinel
# defaults above as well as from the lane's own, so that "the explicit
# fields were honored" and "the omitted fields were inherited" are two
# assertions that cannot be satisfied by one accident.
RESTRICTED_NAME = "vinga_restricted_never_created"
RESTRICTED_USER = "vinga_analyst_role"


def test_a_partial_database_payload_inherits_this_lane_s_instance() -> None:
    """The shape the integration lane's `_restricted_app` builds: a
    `database` mapping naming some fields and leaving the rest to the
    defaults.

    Its omitted `host` and `port` travel the same stale-schema path as
    an empty mapping, and were masked on CI only because the shipped
    values and the service container's coincide. That coincidence is
    exactly why this case is driven under sentinel defaults rather than
    under the ordinary ones: asserting that the omitted fields equal the
    lane instance's host and port proves nothing while the lane instance
    IS the compose instance, and would pass just as well against a
    payload that had been discarded whole.

    So the condition is moved onto four values nothing ships, the
    payload states a name and a user that are none of them, and the
    complete tuple is asserted: what was stated, as stated, and what was
    omitted, from the condition the cascade was last driven with.
    """
    _database_condition(
        **SENTINEL_CONNECTION, environment_name=SENTINEL_CONNECTION["name"]
    )
    try:
        resolved = config_with_agent(
            server={"database": {"name": RESTRICTED_NAME, "user": RESTRICTED_USER}}
        ).server.database

        assert (resolved.host, resolved.port, resolved.name, resolved.user) == (
            SENTINEL_CONNECTION["host"],
            SENTINEL_CONNECTION["port"],
            RESTRICTED_NAME,
            RESTRICTED_USER,
        )
    finally:
        _database_default(LANE_DATABASE)


def _resolved_everywhere() -> list[tuple[str, int, str, str]]:
    """The complete connection each of the four models answers with, on
    a payload that carries a `database` mapping and omits its fields."""
    return [
        (settings.host, settings.port, settings.name, settings.user)
        for settings in (
            DatabaseConfig(),
            ServerConfig(**{"database": {}}).database,
            FileConfig(**{"server": {"database": {}}}).server.database,
            Config(server={"database": {}}).server.database,
        )
    ]


def test_all_four_connection_facts_travel_the_cascade_and_come_back() -> None:
    """The name is the fact that broke, and it is not the only one
    baked in.

    Pydantic inlines every field, and on the ordinary configuration a
    stale host, port or user equals the shipped value and stays
    invisible: the lane's instance IS the compose instance. So the
    helper is driven once with four values nothing else would produce,
    and the complete tuple is asserted through all four models, then
    asserted complete again after the lane condition is put back.
    """
    _database_condition(
        **SENTINEL_CONNECTION, environment_name=SENTINEL_CONNECTION["name"]
    )
    try:
        sentinel = (
            SENTINEL_CONNECTION["host"],
            SENTINEL_CONNECTION["port"],
            SENTINEL_CONNECTION["name"],
            SENTINEL_CONNECTION["user"],
        )
        assert _resolved_everywhere() == [sentinel] * 4
        assert os.environ[DB_NAME_ENV] == SENTINEL_CONNECTION["name"]
    finally:
        _database_default(LANE_DATABASE)

    lane = (DB_HOST, DB_PORT, LANE_DATABASE, DB_USER)
    assert _resolved_everywhere() == [lane] * 4
    assert os.environ[DB_NAME_ENV] == LANE_DATABASE


def _models_within(annotation: Any) -> list[type[BaseModel]]:
    """Every model an annotation reaches, through containers and unions.

    `dict[str, AgentConfig]`, `CaptureConfig | None` and a bare class
    are all shapes a section is declared in, and a walk that only
    recognized the last would miss the others silently, which is the
    failure mode this whole file exists to refuse.
    """
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation]
    return [
        found for argument in get_args(annotation) for found in _models_within(argument)
    ]


def _embeds(model: type[BaseModel], target: type[BaseModel]) -> bool:
    """Whether `target` is anywhere inside `model`'s fields."""
    return any(
        found is target or _embeds(found, target)
        for field in model.model_fields.values()
        for found in _models_within(field.annotation)
    )


def test_every_declared_embedder_of_the_database_is_in_the_rebuild_order() -> None:
    """A fourth embedder fails here rather than reopening the hole.

    The boundary is stated rather than assumed: what is inspected is
    every model class DECLARED in `config.models`, which is where the
    configuration's models live. A `BaseModel.__subclasses__()` walk
    would enumerate loaded classes instead, so an embedder in a module
    nothing had imported yet would be invisible, and a completeness
    claim that can be false is worse than none.

    Compared against the one manifest the conftest's helper itself
    iterates, so the helper cannot drift from what it is checked
    against.
    """
    from vinga_server.config import models

    declared = [
        value
        for value in vars(models).values()
        if isinstance(value, type)
        and issubclass(value, BaseModel)
        and value.__module__ == models.__name__
    ]
    embedders = {model for model in declared if _embeds(model, DatabaseConfig)}

    assert embedders, (
        "no model in config.models was found to embed DatabaseConfig, which cannot "
        "be true while ServerConfig declares it: the traversal above has stopped "
        "seeing what it is meant to see, and would pass whatever was added"
    )
    assert embedders | {DatabaseConfig} == set(DATABASE_REBUILD_ORDER), (
        "config.models declares a model embedding DatabaseConfig that the conftest's "
        "rebuild order does not name, so this lane's database default is inlined "
        "stale into it and any payload composition through it boots elsewhere"
    )
    # And in an order the rebuild can use: a model is rebuilt before
    # anything that embeds it, because a child's rebuild does not
    # propagate upward and an outer rebuild inlines whatever the inner
    # schema says at that moment.
    for position, inner in enumerate(DATABASE_REBUILD_ORDER):
        for outer in DATABASE_REBUILD_ORDER[:position]:
            assert not _embeds(outer, inner), (
                f"{outer.__name__} embeds {inner.__name__} and is rebuilt before it, "
                f"so it inlines the stale schema"
            )


def test_the_packaged_span_mirrors_the_cascade_through_the_payload_door(
    packaged_database,
) -> None:
    """The mirror fixture is the same move in the other direction, and
    it used to rebuild one model too.

    Inside the span every door answers what the package ships, the
    payload door included; the half that says the lane's own name is
    back afterwards is asserted by the fixture's own finalizer, in this
    process, which is what keeps it true whatever order the tests in
    this file run in.
    """
    shipped = (
        PACKAGED_CONNECTION["host"],
        PACKAGED_CONNECTION["port"],
        PACKAGED_CONNECTION["name"],
        PACKAGED_CONNECTION["user"],
    )

    assert _resolved_everywhere() == [shipped] * 4
    # And the variable is absent rather than set to the shipped name, so
    # `load_file_config()` answers out of the package's own default
    # rather than through an override that would hide a broken one.
    assert DB_NAME_ENV not in os.environ
    assert load_file_config(None).server.database.name == PACKAGED_CONNECTION["name"]


def test_no_whole_url_is_left_to_win_over_the_five() -> None:
    """`VINGA_DB_URL` replaces the discrete facts entirely, so one
    exported around the lane would take every settings composition to a
    database these fixtures never made and may not truncate."""
    assert DB_URL_ENV not in os.environ


def test_a_lane_that_stores_nothing_collects_with_no_instance_in_reach() -> None:
    """The smoke lane, collected against a host that does not resolve.

    It stores nothing: it drives a container over HTTP, and in the image
    job it runs on the runner while the database sits on a Docker
    network only the containers can resolve. The root conftest used to
    provision at its own import, for every lane under `tests/`, so the
    smoke lane died at collection with a sentence about an instance it
    has no use for, on the one job that publishes an image.

    Driven as a real pytest rather than asserted about a flag, because
    what broke was collection itself, and a flag is exactly what a
    future conftest could satisfy while still connecting on the way
    past. A name in `.invalid` is reserved by RFC 2606 and resolves
    nowhere, which is the runner's situation without needing the runner.
    """
    finished = ran(
        [sys.executable, "-m", "pytest", "tests/smoke", "--collect-only", "-q"],
        cwd=Path(__file__).resolve().parents[2],
        env=os.environ | {"VINGA_DB_HOST": "nowhere.invalid"},
    )

    assert finished.returncode == 0, finished.stdout + finished.stderr
    assert "tests collected" in finished.stdout, finished.stdout + finished.stderr


# What the lane's own refusals may say
#
# Both of them are raised while a conftest is being imported, which
# means they arrive as a collection traceback: the message, the frame,
# and every local the frame held. The five variables they are about are
# set beside each other and read from one `.env`, so the value most
# likely to be in the wrong one is the credential from the right one.
# The rule is the product's own, and has no exception for a host.

# Not credentials: fixed strings shaped like one, and shaped so a
# substring hunt for either cannot match by accident.
SENTINEL = "sk-lane-4c19b7e2-never-a-real-credential"
OTHER_SENTINEL = "tok-lane-91f3d0a6-never-a-real-credential"

# Every discrete field, each carrying something a refusal must not
# repeat, and a host that resolves nowhere so the refusal is the
# unreachable one. `.invalid` is reserved by RFC 2606.
PLANTED_CONNECTION = {
    "VINGA_DB_HOST": f"{SENTINEL}.invalid",
    "VINGA_DB_PORT": "5432",
    "VINGA_DB_NAME": SENTINEL,
    "VINGA_DB_USER": SENTINEL,
    "VINGA_DB_PASSWORD": OTHER_SENTINEL,
}

# A port that is not one, carrying the same shape: the case that used to
# reach an unguarded `int()` and ride its ValueError out.
PLANTED_PORT = dict(PLANTED_CONNECTION, VINGA_DB_PORT=OTHER_SENTINEL)


def _collected(planted: dict[str, str]) -> str:
    """One unit-lane collection under a planted connection, as everything
    it wrote.

    A subprocess because both refusals happen while a conftest is being
    imported, which no in-process assertion can reach: by the time a test
    is running, the import that would have refused has already
    succeeded.
    """
    finished = ran(
        [sys.executable, "-m", "pytest", "tests/unit/test_db_open.py", "--collect-only", "-q"],
        cwd=Path(__file__).resolve().parents[2],
        env=os.environ | planted | {"VINGA_TEST_RUN": "sentinel_probe"},
    )
    assert finished.returncode != 0, finished.stdout + finished.stderr
    return finished.stdout + finished.stderr


def test_the_unreachable_refusal_repeats_no_part_of_the_connection() -> None:
    """A storing lane pointed at an instance that is not there. The
    sentence names the four variables to look at and none of their
    values, and the driver's own message, which quotes the DSN it tried,
    never travels."""
    written = _collected(PLANTED_CONNECTION)

    assert "could not reach one" in written, written
    assert SENTINEL not in written, written
    assert OTHER_SENTINEL not in written, written
    assert "psycopg" not in written, written


def test_a_port_that_is_not_one_is_refused_without_quoting_it() -> None:
    """The other refusal, which used to be a bare `int()`: its ValueError
    quotes what it was handed, and a port variable is where a password
    lands when two lines of a `.env` are swapped."""
    written = _collected(PLANTED_PORT)

    assert "VINGA_DB_PORT" in written, written
    assert SENTINEL not in written, written
    assert OTHER_SENTINEL not in written, written
    assert "invalid literal for int" not in written, written
