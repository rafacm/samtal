"""What a refusal says of a control character written before the rule
(#414).

`store._check_addressable` refuses a control character in a name and
says why in as many words: it "does not survive a header or a log line
intact". It refuses it at WRITE time only, and the same docstring says
what that leaves: a row written before the rule "still boots, still
appears in a whole-configuration read, and is still deletable". So the
byte reached every sentence composed over a stored identity, and a
sentence is read on a server's stderr as it fails to start, where
nothing between the composition and the stream escapes anything.

The rows here are planted rather than written, because the write path
refuses them, exactly as the URL-credential suite beside this one plants
its own. What the two suites share is the door: `spoken_identity` is the
credential strip and the escape together, and the surfaces that read it
are the ones #381, #382 and #413 established.

Where this rule stops is measured rather than asserted by taste, and the
last section is that measurement. A view hands an identity back as a
document key, and every writer of that document escapes a control
character already and losslessly: JSON as a `\\u001b`, YAML as a `\\e`,
the CLI's terminal door as a `?`. Escaping there would mangle rather
than render, because a read is a fragment a write of it accepts back: an
export naming `bad\\x1bname` imports as a lawful eleven-character agent
that nothing meant, where today the same export is refused by the rule
that says what a name may hold.
"""

import json
import logging
from collections.abc import Iterator
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import insert

from tests.support.config_cli import chain, runner
from tests.support.events import both_formats, only
from tests.support.stores import body, planted
from vinga_server import logs, serving
from vinga_server.build_info import CONTAINER_ENV
from vinga_server.config import entities, views
from vinga_server.config.api import build_api
from vinga_server.config.boot import load_boot_config
from vinga_server.config.loader import ConfigError, StorageError, compose_config
from vinga_server.config.models import (
    AgentConfig,
    DatabaseConfig,
    FileConfig,
    McpServerConfig,
    PromptFragmentConfig,
    ProviderConfig,
    holds_control_character,
    spoken_identity,
    url_credential,
    without_url_credential,
)
from vinga_server.config.secrets import (
    MASTER_KEY_ENV,
    SecretLocation,
    encrypt,
    generate_key,
    load_keys,
)
from vinga_server.config.store import ConfigStore
from vinga_server.db import open_database, schema
from vinga_server.providers import ProviderError, build_entry, build_world

TOKEN = "test-api-token-" + "0123456789abcdef" * 2

# The character planted into every stored row below, and the byte no
# surface may carry. The escape is what an operator reads instead, and
# it is a recipe rather than a mark: `%1b` is the path segment that
# fetches, renames and deletes the row it names.
ESC = "\x1b"
PLANTED = f"bad{ESC}name"
SPOKEN = "bad\\x1bname"
ADDRESSED = "bad%1Bname"

# A lawful name, for the controls. Every rendering below has to be
# byte-identical to what it was before the escape was put on it.
LAWFUL = "claude"

# A provider name nothing defines, so a reference refusal is about the
# entry rather than about the deployment being empty.
GONE = "no-such-provider"

# The URL-credential sentinels, because the two rules meet on one name
# and the order between them is a decision rather than an accident. Not
# real credentials, and shaped so a substring check cannot match one by
# accident.
KEY_SENTINEL = "aaaa1111bbbb2222-the-planted-credential"
HOST = "planted.invalid"

# A name carrying both. `urlsplit` keeps an ESC, so the strip shortens
# the name and the escape then spells the character out.
BOTH = f"https://user:{KEY_SENTINEL}@{HOST}/na{ESC}med"
BOTH_SPOKEN = f"https://{HOST}/na\\x1bmed"

# The two shapes a carriage return makes out of a URL, which is what
# decides the order the two rules run in. `urlsplit` deletes a tab, a
# carriage return and a newline before it reads anything, so both of
# these are URLs carrying a credential to the library and to any client
# that opens one, and neither of them holds the substrings the rules used
# to look for.
BROKEN_SCHEME = f"https:/\r/user:{KEY_SENTINEL}@{HOST}/named"
BROKEN_PARAMETER = f"https://{HOST}/named?to\rken={KEY_SENTINEL}"

SENTINELS = (ESC, "\r", KEY_SENTINEL)


@pytest.fixture
def keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MASTER_KEY_ENV, generate_key())


@pytest.fixture
def store(keys: None) -> Iterator[ConfigStore]:
    engine = open_database(DatabaseConfig())
    try:
        yield ConfigStore(engine, load_keys())
    finally:
        engine.dispose()


@pytest.fixture
def api(keys: None) -> FastAPI:
    return build_api(TOKEN, DatabaseConfig())


@pytest.fixture
def client(api: FastAPI) -> Iterator[TestClient]:
    with TestClient(api, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        yield client


def _plant(store: ConfigStore, kind: str, identity: tuple[str, ...], entry: BaseModel) -> None:
    """One entry written as a row rather than through a write path,
    which is the only way any of these gets in at all.

    The body is the repository's own dump of the model, so what today's
    write path would object to is the name and nothing else.
    """
    descriptor = entities.descriptor(kind)
    table = getattr(schema, descriptor.table)
    columns = dict(zip(descriptor.addressing, identity, strict=True))
    where = [table.c[column] == value for column, value in columns.items()]
    planted(
        store,
        table.delete().where(*where),
        insert(table).values(**columns, body=body(entry)),
    )


def _carries_no_sentinel(*renderings: str) -> None:
    """No raw control character and no planted credential, in any
    rendering a caller can reach.

    Both, and looped rather than named one line at a time: a name can
    carry each rule's shape at once, and a case asserting one of them
    goes on passing over a surface leaking the other.
    """
    for rendering in renderings:
        for sentinel in SENTINELS:
            assert sentinel not in rendering


def _logged(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Every record written while a command ran, in both formats this
    server writes one in, which is the whole of what a no-leak claim
    about a log can mean."""
    text = logging.Formatter(logs.TEXT_FORMAT)
    return [
        rendering
        for record in caplog.records
        for rendering in (logs.JsonFormatter().format(record), text.format(record))
    ]


# The rule itself, before any surface reads it


def test_the_planted_name_is_one_no_write_would_accept() -> None:
    """The guard on every case below, and half the trade-off in one.

    Such a name is refused at a write for what it does to a log line,
    which is a different reason from the one that refuses a
    credential-bearing name, and it has a different consequence. A
    credential holds `://` and therefore a slash, so no path segment
    addresses such a row. A control character percent-encodes and
    decodes losslessly, so this row IS addressable, and the case further
    down drives that end to end.
    """
    assert holds_control_character(PLANTED)
    assert url_credential(PLANTED) is None
    assert "/" not in PLANTED
    assert spoken_identity(PLANTED) == SPOKEN


@pytest.mark.parametrize(
    "character",
    [chr(point) for point in (*range(0x00, 0x20), *range(0x7F, 0xA0))],
    ids=lambda character: f"U+{ord(character):04X}",
)
def test_every_character_the_write_refuses_is_one_the_sentence_escapes(
    character: str,
) -> None:
    """The two readers of the class agree, character by character.

    Which is the whole reason the class has one home. A write that
    refused a set a refusal did not escape would be the rule and its
    defence disagreeing about what the rule is about, and the gap would
    be exactly the characters nothing tested.
    """
    assert holds_control_character(character)
    assert spoken_identity(f"a{character}b") == f"a\\x{ord(character):02x}b"


@pytest.mark.parametrize("character", ["\x20", "\x7e", "\xa0", "%", "é", "\\"])
def test_a_character_outside_the_class_is_spoken_as_written(character: str) -> None:
    """The boundaries of the class, and the display fidelity that makes
    the escape safe to put on every surface at once.

    The three neighbours of the two ranges are here on purpose, and so
    is the backslash: it is deliberately not escaped beside the
    characters it introduces, so a lawful name holding one is printed as
    it is stored. What that costs is an ambiguity between a name holding
    `\\x1b` and one holding the character, which is a reading of two
    names rather than an addressing of either.
    """
    assert not holds_control_character(character)
    assert spoken_identity(f"a{character}b") == f"a{character}b"


def test_a_name_carrying_both_is_stripped_and_then_escaped() -> None:
    """The interaction with #381, on one name, in the order the two
    rules are applied in."""
    assert url_credential(BOTH) is not None
    assert holds_control_character(BOTH)
    assert spoken_identity(BOTH) == BOTH_SPOKEN
    _carries_no_sentinel(spoken_identity(BOTH))


@pytest.mark.parametrize("carrying", [BROKEN_SCHEME, BROKEN_PARAMETER])
def test_the_strip_runs_first_because_it_reads_the_value_as_stored(
    carrying: str,
) -> None:
    """Why the order is the rule and not a preference, measured against
    the other order rather than argued.

    A carriage return inside a URL is deleted by the parser before it
    reads anything, so it can break the `://` the credential rule looks
    for and the `token` its parameter rule looks for while leaving a URL
    that carries a credential to anything that opens one. Escaping first
    puts a backslash where that character was, which makes the break
    permanent: the rule then answers None and the credential travels out
    whole, which is the second assertion.

    Stripping first asks the question of the value the parser will see,
    so both shapes are recognized and shortened, and there is no
    character left for the escape to spell out.
    """
    escaped_first = carrying.replace("\r", "\\x0d")

    assert url_credential(carrying) is not None
    assert spoken_identity(carrying) == f"https://{HOST}/named"
    assert url_credential(escaped_first) is None
    assert KEY_SENTINEL in without_url_credential(escaped_first)


# The refusals, which are where the byte was actually read


@pytest.fixture
def unbootable(store: ConfigStore) -> ConfigStore:
    """A deployment named the way no write would allow, holding the one
    mistake that refuses a boot: an agent whose stage names a provider
    that is not there.

    The provider planted beside it is what the refusal's `defined:` half
    lists, so one sentence carries the name twice, once as the location
    and once in the list of what could have been meant. The default
    agent is left unset with no device bound, which is the completeness
    refusal, so the same sentence carries it a third time.
    """
    _plant(store, "provider", ("llm", PLANTED), ProviderConfig(type="mock"))
    _plant(store, "agent", (PLANTED,), AgentConfig(prompt="hi", llm=GONE))
    return store


def test_a_boot_refusal_names_the_stored_entry_with_the_byte_escaped(
    unbootable: ConfigStore,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The whole boot, from the file half to the composition, which is
    what a server runs and what a reload runs again. Three sentences
    over one planted name: the reference check's location, the list of
    what could have been meant, and the completeness check's list."""
    monkeypatch.delenv("VINGA_CONFIG", raising=False)

    with caplog.at_level(logging.DEBUG), pytest.raises(ConfigError) as caught:
        load_boot_config()

    message = str(caught.value)
    assert f"agents.{SPOKEN}.llm: names no llm provider that exists" in message
    assert f"(defined: {SPOKEN})" in message
    assert f"set it to one of: {SPOKEN}" in message
    _carries_no_sentinel(chain(caught.value), *_logged(caplog))


def test_the_boot_refusal_reaches_stderr_with_nothing_raw_on_it(
    unbootable: ConfigStore,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Where an operator actually meets it, and the surface the whole
    rule is about: the entry point prints the sentence on stderr and
    leaves with 1, before logging is configured at all, so nothing
    between the composition and the terminal escapes anything."""
    monkeypatch.delenv("VINGA_CONFIG", raising=False)

    with caplog.at_level(logging.DEBUG):
        assert serving.run(None) == 1

    printed = capsys.readouterr()
    assert f"agents.{SPOKEN}.llm" in printed.err
    _carries_no_sentinel(printed.out, printed.err, *_logged(caplog))


def test_an_unreadable_row_names_its_entry_with_the_byte_escaped(
    store: ConfigStore,
) -> None:
    """The location every per-row refusal is composed from, which the
    store builds rather than walking out of a validation error."""
    planted(store, insert(schema.agents).values(name=PLANTED, body='{"llm": ""}'))

    with pytest.raises(StorageError) as caught:
        store.load()

    assert f"agents.{SPOKEN}: " in str(caught.value)
    _carries_no_sentinel(chain(caught.value))


def test_a_composed_locations_identity_is_named_with_the_byte_escaped() -> None:
    """The walk over a validation error's own locations, which is the
    half #382 converged onto the shared renderer.

    Composed from a mapping rather than from a store, which is the shape
    a composition with no database behind it takes, and the route that
    reaches the field validators with a stored identity in the location.
    """
    with pytest.raises(ConfigError) as caught:
        compose_config(
            FileConfig(),
            {"agents": {PLANTED: {"prompt": "hi", "llm": ""}}},
            "the test's database",
        )

    assert f"agents.{SPOKEN}.llm: " in str(caught.value)
    _carries_no_sentinel(chain(caught.value))


def test_a_slot_refusal_names_the_entity_it_hangs_on(store: ConfigStore) -> None:
    """One of the four locations that used to join a section to a stored
    name by hand: the one a provider slot's addressability refusal
    carries.

    It is reached only after the row has been found, so the name in it is
    a stored one however the caller spelled the slot.
    """
    _plant(store, "provider", ("llm", PLANTED), ProviderConfig(type="mock"))

    with pytest.raises(ConfigError) as caught:
        store.set_secret(SecretLocation.provider("llm", PLANTED, f"api_key{ESC}"), "irrelevant")

    assert str(caught.value).startswith(f"providers.llm.{SPOKEN}: the slot contains")
    _carries_no_sentinel(chain(caught.value))


def test_an_mcp_name_cannot_carry_one_because_the_load_path_refuses_it(
    store: ConfigStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Why the other three of those four locations are consolidated
    rather than fixed, asserted rather than assumed.

    The two MCP slot refusals and the location an unresolved environment
    reference is reported at name an MCP server, and an MCP entry name is
    held to `[A-Za-z0-9_-]+` on the way OUT as well as on the way in,
    because it becomes a tool-name prefix. So a planted one is refused by
    the load and reaches neither of those sentences, exactly as a planted
    device MAC is (#382). They read `entity_location` for the same reason
    the rest do, which is locality: one entry named two ways is how a
    boot comes to say two things about one row.
    """
    monkeypatch.delenv("VINGA_CONFIG", raising=False)
    _plant(
        store,
        "mcp-server",
        (PLANTED,),
        McpServerConfig(transport="stdio", command="uvx"),
    )

    with pytest.raises(StorageError) as caught:
        store.load()

    assert "an entry name becomes a tool-name prefix" in str(caught.value)
    _carries_no_sentinel(chain(caught.value))


# The build, which names the same entries again after the composition


def _world_named(name: str, **stages: str):
    """A one-agent configuration whose llm entry and whose agent both
    carry `name`, composed the way a stored snapshot is."""
    return compose_config(
        FileConfig(),
        {
            "providers": {
                "llm": {name: {"type": "mock"}},
                "asr": {"ears": {"type": "mock"}},
                "tts": {"voice": {"type": "mock"}},
                "vad": {"gate": {"type": "mock"}},
            },
            "agents": {
                name: {
                    "prompt": "hi",
                    "asr": "ears",
                    "tts": "voice",
                    "vad": "gate",
                    **stages,
                }
            },
            "default_agent": name,
        },
        "the test's database",
    )


async def test_the_build_names_a_stored_entry_with_the_byte_escaped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The label every refusal about one provider entry is built from,
    on both halves of a build: the constructor's, and the owner's checks
    that only run once an object exists."""
    with caplog.at_level(logging.DEBUG), pytest.raises(ProviderError) as constructed:
        await build_entry("llm", PLANTED, ProviderConfig(type="no-such-type"))
    with caplog.at_level(logging.DEBUG), pytest.raises(ProviderError) as owned:
        await build_entry("llm", PLANTED, ProviderConfig(type="mock", egress=False))

    assert f"providers.llm.{SPOKEN}: names no llm provider type" in str(constructed.value)
    assert f'providers.llm.{SPOKEN}: "egress" is decided' in str(owned.value)
    _carries_no_sentinel(chain(constructed.value), chain(owned.value), *_logged(caplog))


async def test_an_option_a_type_never_asked_about_is_named_with_the_byte_escaped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The other caller-written word this refusal reports. A provider
    entry passes every key beyond the declared ones through to its
    implementation, so a key holding a control character is a lawful
    stored key that a write refuses now and a planted row still holds.
    """
    entry = ProviderConfig.model_validate({"type": "mock", f"opt{ESC}ion": "ordinary"})

    with caplog.at_level(logging.DEBUG), pytest.raises(ProviderError) as caught:
        await build_entry("llm", LAWFUL, entry)

    assert str(caught.value) == (f"providers.llm.{LAWFUL}: unknown option(s): opt\\x1bion")
    _carries_no_sentinel(chain(caught.value), *_logged(caplog))


async def test_an_agent_with_no_provider_for_a_stage_is_named_the_same_way(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The build's own sentence about an agent, which the composition
    cannot say: a stage naming nothing anywhere resolves to None rather
    than to a reference that does not exist."""
    with caplog.at_level(logging.DEBUG), pytest.raises(ProviderError) as caught:
        await build_world(_world_named(PLANTED))

    assert f"agents.{SPOKEN}: no llm provider is named" in str(caught.value)
    _carries_no_sentinel(chain(caught.value), *_logged(caplog))


def test_the_location_a_stage_resolves_through_is_named_the_same_way() -> None:
    """What `provider_for_agent` answers beside the name: the layer the
    stage came from, which its docstring calls what an error message
    quotes."""
    config = _world_named(PLANTED, llm=PLANTED)

    assert config.provider_for_agent(PLANTED, "llm") == (
        PLANTED,
        f"agents.{SPOKEN}.llm",
    )


async def test_the_container_warning_names_the_entry_with_the_byte_escaped(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The one event the build emits itself, and what makes the stamped
    identity part of this rule rather than outside it.

    The events package composes this warning's SENTENCE out of the
    identity's own fields, so the stamp is what an operator reads in a
    log line and not only what a payload carries. The name the stamp
    holds has never been the name as stored (#413 put the credential
    strip on it), and it is the spoken one now, so the sentence, the
    structured field and every other event about the entry call it one
    thing.
    """
    monkeypatch.setenv(CONTAINER_ENV, "1")
    entry = ProviderConfig.model_validate(
        {
            "type": "openai_compatible",
            "base_url": "http://localhost:11434/v1",
            "model": "qwen3:8b",
        }
    )

    with caplog.at_level(logging.WARNING):
        provider = await build_entry("llm", PLANTED, entry)
    await provider.close()

    record = only(caplog, "provider_reaches_loopback")
    assert f"providers.llm.{SPOKEN}" in record.getMessage()
    _carries_no_sentinel(both_formats(caplog))


@pytest.fixture
def unbuildable(store: ConfigStore) -> ConfigStore:
    """A deployment whose stored world composes and will not build: one
    agent named the way no write would allow, on an llm entry named the
    same way and filed under a type that is not one."""
    _plant(store, "provider", ("llm", PLANTED), ProviderConfig(type="no-such-type"))
    for stage, name in (("asr", "ears"), ("tts", "voice"), ("vad", "gate")):
        _plant(store, "provider", (stage, name), ProviderConfig(type="mock"))
    _plant(
        store,
        "agent",
        (PLANTED,),
        AgentConfig(prompt="hi", llm=PLANTED, asr="ears", tts="voice", vad="gate"),
    )
    planted(
        store,
        insert(schema.domain_settings).values(key=schema.DEFAULT_AGENT_KEY, value=PLANTED),
    )
    return store


def test_a_boot_refused_by_the_build_reaches_an_operator_escaped(
    unbuildable: ConfigStore,
    restore_root_logger: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Where a build refusal is actually met, through the entry point
    rather than through the lifespan alone: uvicorn's lifespan build
    refuses, `serve` swallows the exit it was given, and `run` prints
    the one sentence to stderr.

    The root logger is given back afterwards, because `run` configures
    logging as early as the configuration allows and that takes it over
    for the whole process.
    """
    monkeypatch.delenv("VINGA_CONFIG", raising=False)

    with caplog.at_level(logging.DEBUG):
        assert serving.run(None) == 1

    printed = capsys.readouterr()
    assert f"providers.llm.{SPOKEN}: names no llm provider type" in printed.err
    _carries_no_sentinel(printed.out, printed.err, *_logged(caplog))


# The surfaces a request can reach, because this row is addressable
#
# Every case here reads the sentence off the PARSED body rather than off
# the text. JSON escapes a raw byte on its own, so an assertion over the
# response text passes with the escape gone, and the sentence is what an
# operator reads and what a client writes into its own log.


def test_the_row_is_reachable_and_the_line_that_fixes_it_is_escaped(
    store: ConfigStore, client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """The half of the decision that makes an escape worth more than a
    mark, driven end to end rather than argued.

    A control character percent-encodes losslessly, so unlike a
    credential-bearing name this row can be fetched, renamed and deleted
    over the API. The escaped spelling is what tells an operator which
    byte to encode; a fixed mark would name the row without saying what
    is in it, and would print two different broken names alike.

    The delete is of the planted row ITSELF rather than of the lawful
    name a rename just gave it, which is what the sol round on PR #423
    caught: a delete goes by membership, so the row that is deletable is
    the one no write would accept, and renaming first is the one route
    that never asks the delete to say the name.
    """
    _plant(store, "agent", (PLANTED,), AgentConfig(prompt="hi"))
    _plant(store, "agent", (f"{PLANTED}-2",), AgentConfig(prompt="hi"))

    with caplog.at_level(logging.DEBUG):
        read = client.get(f"/agents/{ADDRESSED}")
        deleted = client.delete(f"/agents/{ADDRESSED}")
        renamed = client.post(f"/agents/{ADDRESSED}-2/rename", json={"to": LAWFUL})

    assert read.status_code == 200
    assert deleted.json()["wrote"] == f"agent {SPOKEN} deleted"
    assert renamed.json()["wrote"] == f"agent {SPOKEN}-2 renamed to {LAWFUL}"
    _carries_no_sentinel(
        deleted.json()["wrote"],
        renamed.json()["wrote"],
        str(dict(deleted.headers)),
        *_logged(caplog),
    )


def test_every_kind_says_its_own_name_escaped_when_it_is_deleted(
    store: ConfigStore, client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """The other three kinds a delete can name, since a delete is the
    one write whose subject is a row that already exists and therefore
    the one that can be handed a name a write would refuse.

    A provider and an MCP server say what went with them, so their
    sentences carry the name in front of a tail of their own.
    """
    _plant(store, "provider", ("llm", PLANTED), ProviderConfig(type="mock"))
    _plant(
        store, "prompt-fragment", (PLANTED,), PromptFragmentConfig(text="a fragment")
    )

    with caplog.at_level(logging.DEBUG):
        provider = client.delete(f"/providers/llm/{ADDRESSED}")
        fragment = client.delete(f"/prompt-fragments/{ADDRESSED}")

    assert provider.json()["wrote"] == (
        f"provider llm.{SPOKEN} deleted, with its stored secrets"
    )
    assert fragment.json()["wrote"] == f"prompt-fragment {SPOKEN} deleted"
    _carries_no_sentinel(
        provider.json()["wrote"], fragment.json()["wrote"], *_logged(caplog)
    )


def test_a_stored_secrets_location_says_itself_escaped(
    store: ConfigStore, client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """`SecretLocation.describe` is the one string thirteen refusals and
    four acknowledgements are built from, and both halves of it are
    stored: a slot is held to the addressability rule at write time
    only, exactly as a name is.

    The slot here is planted with the byte in it as well, which is what
    says the rule is on the location rather than on its entity half.
    """
    _plant(store, "provider", ("llm", PLANTED), ProviderConfig(type="mock"))
    planted(
        store,
        schema.providers.update()
        .where(schema.providers.c.name == PLANTED)
        .values(secrets={f"api_key{ESC}": {"v": 1, "ct": "x", "key": "k"}}),
    )

    with caplog.at_level(logging.DEBUG):
        wrote = client.put(
            f"/providers/llm/{ADDRESSED}/secrets/api_key", json={"secret": "s"}
        )
        cleared = client.delete(f"/providers/llm/{ADDRESSED}/secrets/api_key%1B")

    assert wrote.json()["wrote"] == f"secret for provider llm.{SPOKEN} api_key"
    assert cleared.json()["wrote"] == (
        f"secret for provider llm.{SPOKEN} api_key\\x1b cleared"
    )
    _carries_no_sentinel(
        wrote.json()["wrote"], cleared.json()["wrote"], *_logged(caplog)
    )


def test_a_boot_refuses_an_unreadable_envelope_naming_neither_half_raw(
    store: ConfigStore, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Where that location is actually read out: `verify_secrets` opens
    every stored envelope at startup, so an envelope that will not open
    puts its entity and its slot on a boot's stderr.

    The planted envelope carries both rules' shapes at once, in the name
    and in the slot, because either half alone would leave the other
    passing.
    """
    monkeypatch.delenv("VINGA_CONFIG", raising=False)
    _plant(store, "provider", ("llm", BOTH), ProviderConfig(type="mock"))
    planted(
        store,
        schema.providers.update()
        .where(schema.providers.c.name == BOTH)
        .values(secrets={f"api_key{ESC}": {"v": 1, "ct": "not-an-envelope", "key": "k"}}),
    )

    with caplog.at_level(logging.DEBUG), pytest.raises(ConfigError) as caught:
        load_boot_config()

    assert f"provider llm.{BOTH_SPOKEN} api_key\\x1b: " in str(caught.value)
    _carries_no_sentinel(chain(caught.value), *_logged(caplog))


def test_a_payload_naming_a_kind_that_is_not_one_is_refused_and_bounded(
    store: ConfigStore,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The other half of a location saying itself: what happens when the
    location it is asked to say came out of a payload.

    A payload's three fields are held to `isinstance(str)` and to
    nothing else, so `kind` can be any word at all, while
    `SecretLocation.kind` is a closed set of two and `describe` reads it
    against the registry. A location built from a word that is not one
    of them used to leave as a `KeyError` whose single argument is the
    payload's own word, past the bounded handler in `serving.run`, so a
    boot printed a traceback carrying decrypted bytes.

    The envelope here is VALID: it is written by this module's own
    `encrypt` under the configured key, so it decrypts, and what refuses
    it is the mismatch check rather than anything about the ciphertext.
    Its rogue kind carries both sentinels, because what a traceback
    would have published is that word.

    Driven through the entry point rather than through `decrypt`, since
    what the finding is about is the boundary: the refusal has to be one
    `serving.run` answers 1 to, with the sentence on stderr and no
    traceback on either stream.
    """
    monkeypatch.delenv("VINGA_CONFIG", raising=False)
    _plant(store, "provider", ("llm", LAWFUL), ProviderConfig(type="mock"))
    rogue = SecretLocation(
        kind=cast("Any", f"pro{ESC}vider-{KEY_SENTINEL}"),
        identity=f"llm.{LAWFUL}",
        slot="api_key",
    )
    planted(
        store,
        schema.providers.update()
        .where(schema.providers.c.name == LAWFUL)
        .values(secrets={"api_key": encrypt(rogue, "the-secret", load_keys())}),
    )

    with caplog.at_level(logging.DEBUG):
        assert serving.run(None) == 1

    printed = capsys.readouterr()
    assert f"provider llm.{LAWFUL} api_key: " in printed.err
    assert "is not one of: mcp_server, provider" in printed.err
    assert "Traceback" not in printed.out + printed.err
    _carries_no_sentinel(printed.out, printed.err, *_logged(caplog))


def test_a_binding_to_a_legacy_agent_says_it_escaped(
    store: ConfigStore, client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """The acknowledgement built from the row rather than from the
    request, which is what puts a stored name in a 200.

    A binding REFERENCES an agent rather than creating one, so the name
    is checked by membership and never by the addressability rule, and
    it arrives in a JSON body where nothing about it has to survive a
    path segment. That is the route by which a name no write would
    accept reaches a successful answer.
    """
    _plant(store, "agent", (PLANTED,), AgentConfig(prompt="hi"))

    with caplog.at_level(logging.DEBUG):
        bound = client.put("/devices/aa:bb:cc:dd:ee:ff", json={"agents": [PLANTED]})
        default = client.put("/default-agent", json={"name": PLANTED})

    assert bound.json()["wrote"] == f"device aa:bb:cc:dd:ee:ff bound to {SPOKEN}"
    # The default agent is written rather than referenced, so the name
    # goes through the addressability rule on the way in and this one
    # answer cannot carry the byte at all. Asserted rather than assumed,
    # because that is what says the strip on it is belt and braces.
    assert default.status_code == 422
    assert "contains a control character" in default.json()["detail"]
    _carries_no_sentinel(
        bound.json()["wrote"], default.json()["detail"], *_logged(caplog)
    )


# Where the rule stops, and why


def test_a_view_hands_the_name_back_as_it_is_stored(store: ConfigStore) -> None:
    """The document key, which is deliberately not escaped.

    A read is a fragment a write of it accepts back, and the case below
    is what that means for this name. The claim here is only that the
    view is the identity function on it: what the byte becomes is the
    business of whatever writes the document.
    """
    _plant(store, "agent", (PLANTED,), AgentConfig(prompt="hi"))

    assert list(views.config(store.load())["config"]["agents"]) == [PLANTED]
    assert list(views.agents(store.load())) == [PLANTED]


def test_the_writers_of_that_document_escape_the_byte_themselves(
    store: ConfigStore,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """And the reason the view needs no rule of its own: neither
    rendering an operator meets puts the raw byte on a stream.

    JSON writes `\\u001b` and YAML writes `\\e`, each of them lossless
    and each of them the format's own. The CLI's table renderings go
    through a third door, `printing.printable`, which replaces what it
    cannot print; that one loses which character it was, which is
    exactly why a refusal does not use it.
    """
    _plant(store, "agent", (PLANTED,), AgentConfig(prompt="hi"))

    listed = client.get("/agents")
    run = runner(monkeypatch)
    capsys.readouterr()
    assert run("show") == 0
    printed = capsys.readouterr()

    assert json.loads(listed.text) == {PLANTED: {"entity": {"prompt": "hi"}, "secrets": {}}}
    _carries_no_sentinel(listed.text, printed.out, printed.err)


def test_an_export_of_such_a_row_is_still_refused_by_an_import(
    store: ConfigStore,
    spare_database: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The measurement the decision turns on.

    An export is the whole-configuration document in the shape `import`
    takes, and `import` runs the write path. Left as stored, the
    document round-trips to the name that is really there and the write
    refuses it by the rule that says what a name may hold, which is the
    honest answer and names the rule.

    Escaped, it would round-trip to a lawful eleven-character name and
    be WRITTEN: the second half of this case is that outcome, spelled
    out, and it is why the escape is on the sentences and not here.
    """
    _plant(store, "agent", (PLANTED,), AgentConfig(prompt="hi"))
    first = runner(monkeypatch)
    capsys.readouterr()
    assert first("export") == 0
    exported = capsys.readouterr().out

    second = runner(monkeypatch, database=spare_database)
    assert second("import", "-f", "-", stdin=exported) == 1
    refused = capsys.readouterr()
    assert "the name contains a control character" in refused.err

    third = runner(monkeypatch, database=spare_database)
    assert third("import", "-f", "-", stdin=exported.replace("\\e", "\\\\x1b")) == 0
    written = capsys.readouterr()
    assert f"agents.{SPOKEN}: wrote" in written.out


# The control


async def test_a_lawful_name_is_named_by_every_one_of_them_as_it_is_stored(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """`spoken_identity` is the identity function on a name a write
    would accept, so every rendering above is byte-identical to what it
    was before the escape was put on it."""
    monkeypatch.setenv(CONTAINER_ENV, "1")

    with pytest.raises(ConfigError) as composed:
        compose_config(
            FileConfig(),
            {"agents": {LAWFUL: {"prompt": "hi", "llm": GONE}}},
            "the test's database",
        )
    with pytest.raises(ProviderError) as declared:
        await build_entry("llm", LAWFUL, ProviderConfig(type="mock", egress=False))
    with pytest.raises(ProviderError) as unnamed:
        await build_world(_world_named(LAWFUL))
    with caplog.at_level(logging.WARNING):
        provider = await build_entry(
            "llm",
            LAWFUL,
            ProviderConfig.model_validate(
                {
                    "type": "openai_compatible",
                    "base_url": "http://localhost:11434/v1",
                    "model": "qwen3:8b",
                }
            ),
        )
    await provider.close()

    assert f"agents.{LAWFUL}.llm: names no llm provider that exists" in str(composed.value)
    assert str(declared.value).startswith(f'providers.llm.{LAWFUL}: "egress" is decided')
    assert str(unnamed.value).startswith(f"agents.{LAWFUL}: no llm provider is named")
    assert f"providers.llm.{LAWFUL}" in only(caplog, "provider_reaches_loopback").getMessage()
    assert _world_named(LAWFUL, llm=LAWFUL).provider_for_agent(LAWFUL, "llm") == (
        LAWFUL,
        f"agents.{LAWFUL}.llm",
    )
