"""The repository: what it loads, what it writes, and what it refuses.

Two properties carry most of this file. Every reference resolves after
every write, which is what makes the database always loadable by a
server; and no completeness rule is enforced at write time, which is
what lets a deployment be built up from nothing in the natural order
without the first agent and default_agent deadlocking on each other.
"""

import threading
from pathlib import Path

import pytest
from cryptography.fernet import Fernet, MultiFernet
from sqlalchemy import select, update

from samtal_server.config import ConfigError
from samtal_server.config.loader import StorageError
from samtal_server.config.models import mcp_entry_fragment
from samtal_server.config.secrets import SecretLocation, generate_key
from samtal_server.config.store import ConfigStore, verify_secrets
from samtal_server.db import open_database, schema

# Not a real credential, and shaped so a substring check for it cannot
# match by accident.
SECRET = "sk-test-4f8b2c9e-never-a-real-credential"

nan = float("nan")
inf = float("inf")

CLAUDE = SecretLocation.provider("llm", "claude", "api_key")
WEATHER = SecretLocation.mcp_server("weather", "headers.Authorization")


def _chain(exc: BaseException) -> str:
    parts: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        parts += [repr(current), str(current)]
        current = current.__cause__ or current.__context__
    return "\n".join(parts)


@pytest.fixture
def keys() -> MultiFernet:
    return MultiFernet([Fernet(generate_key())])


@pytest.fixture
def store(tmp_path: Path, keys: MultiFernet):
    engine = open_database(tmp_path / "db")
    try:
        yield ConfigStore(engine, keys)
    finally:
        engine.dispose()


def _populate(store: ConfigStore) -> None:
    """A working configuration, written in the natural order."""
    store.set_provider("llm", "claude", {"type": "anthropic", "model": "claude-sonnet-5"})
    store.set_provider("asr", "whisper", {"type": "faster_whisper", "model": "small"})
    store.set_provider("tts", "voice", {"type": "piper", "model": "es"})
    store.set_provider("vad", "silero", {"type": "silero"})
    store.set_mcp_server(
        "home",
        {"transport": "stdio", "command": "uvx", "args": ["home-mcp"], "egress": False},
    )
    store.set_agent_defaults(
        {"llm": "claude", "asr": "whisper", "tts": "voice", "vad": "silero", "mcp": ["home"]}
    )
    store.set_agent("sam", {"prompt": "You are Sam."})
    store.bind_device("AA-BB-CC-DD-EE-FF", ["sam"])
    store.set_default_agent("sam")


def test_an_empty_database_loads_an_empty_snapshot(store: ConfigStore) -> None:
    snapshot = store.load()

    assert snapshot.domain.agents == {}
    assert snapshot.domain.default_agent is None
    assert snapshot.domain.providers.llm == {}
    assert len(snapshot.secrets) == 0


def test_a_configuration_round_trips_through_the_rows(store: ConfigStore) -> None:
    _populate(store)
    store.set_mcp_server(
        "weather",
        {
            "transport": "streamable_http",
            "url": "https://example.invalid/mcp",
            "headers": {"Authorization": "$WEATHER_TOKEN"},
            "tool_timeout_s": 5,
        },
    )
    store.set_agent(
        "poet",
        {
            "prompt": "You are a poet.",
            "tts": "voice",
            "mcp": [],
            "filler": {"enabled": True, "phrases": ["Hmm..."]},
        },
    )

    domain = store.load().domain

    assert domain.providers.llm["claude"].type == "anthropic"
    assert domain.providers.llm["claude"].options == {"model": "claude-sonnet-5"}
    assert domain.mcp_servers["home"].command == "uvx"
    assert domain.mcp_servers["home"].egress is False
    assert domain.mcp_servers["weather"].headers == {"Authorization": "$WEATHER_TOKEN"}
    assert domain.mcp_servers["weather"].tool_timeout_s == 5
    assert domain.agent_defaults.mcp == ["home"]
    assert domain.agents["poet"].filler is not None
    assert domain.agents["poet"].filler.phrases == ["Hmm..."]
    # A list replaces rather than extends, so an empty one is not a null.
    assert domain.agents["poet"].mcp == []
    assert domain.agents["sam"].mcp is None
    assert domain.devices == {"aa:bb:cc:dd:ee:ff": ["sam"]}
    assert domain.default_agent == "sam"


def test_both_mcp_entry_forms_round_trip_through_the_row(store: ConfigStore) -> None:
    """Each form is stored as itself. The column holds plain JSON, so
    what a read shows is the fragment a write of it takes back, and the
    object form gains no key it was not written with."""
    _populate(store)
    written = [
        "home",
        {"server": "weather", "tools": ["forecast"]},
        {"server": "shed"},
    ]
    store.set_mcp_server("weather", {"transport": "stdio", "command": "uvx"})
    store.set_mcp_server("shed", {"transport": "stdio", "command": "uvx"})
    store.set_agent("poet", {"prompt": "You are a poet.", "mcp": written})

    with store._engine.connect() as connection:
        stored = connection.execute(
            select(schema.agents.c.mcp).where(schema.agents.c.name == "poet")
        ).scalar_one()
    assert stored == written

    entry = store.load().domain.agents["poet"]
    assert entry.mcp is not None
    assert [mcp_entry_fragment(item) for item in entry.mcp] == written


def test_a_pre_upgrade_string_row_loads_and_is_written_back_unchanged(
    store: ConfigStore,
) -> None:
    """Every row written before the object form existed holds a plain
    list of names, which is why the string form is stored as a string
    and not normalized into an object: there is no migration to run."""
    _populate(store)
    with store._engine.begin() as connection:
        connection.execute(
            update(schema.agents).where(schema.agents.c.name == "sam").values(mcp=["home"])
        )

    entry = store.load().domain.agents["sam"]
    assert entry.mcp == ["home"]

    # Written back through the same path the API writes with, which is
    # where a normalization would have shown up.
    store.set_agent("sam", {"prompt": "You are Sam.", "mcp": ["home"]})
    with store._engine.connect() as connection:
        stored = connection.execute(
            select(schema.agents.c.mcp).where(schema.agents.c.name == "sam")
        ).scalar_one()
    assert stored == ["home"]


@pytest.mark.parametrize(
    "grant",
    [
        {"server": "home", "tools": [SECRET, SECRET]},
        {"server": SECRET, "tools": []},
        {"server": "home", SECRET: "yes"},
        {"server": "home", "tools": [{"pasted": SECRET}]},
    ],
)
def test_a_malformed_grant_is_refused_with_nothing_of_it_in_the_chain(
    store: ConfigStore, grant: dict
) -> None:
    """Where the sanitized sentence is built, and the one place the
    whole rejected fragment is still in reach: a ValidationError's
    errors() hold it, so the refusal is raised outside the handler and
    nothing walking the chain finds it."""
    _populate(store)

    with pytest.raises(ConfigError) as caught:
        store.set_agent("poet", {"prompt": "P", "mcp": [grant]})

    assert "entry 1" in str(caught.value)
    assert SECRET not in _chain(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_a_grant_on_an_unknown_server_is_refused_at_the_write(store: ConfigStore) -> None:
    # The object form goes through the reference check the string form
    # does, which is what forces the natural creation order.
    _populate(store)
    with pytest.raises(ConfigError, match='unknown MCP server "ghost"'):
        store.set_agent("poet", {"prompt": "P", "mcp": [{"server": "ghost", "tools": ["a"]}]})


def test_a_loaded_snapshot_has_no_unresolved_references(store: ConfigStore) -> None:
    from samtal_server.config.models import check_completeness, check_references

    _populate(store)
    domain = store.load().domain

    assert check_references(domain) == []
    assert check_completeness(domain) == []


def test_the_credential_reference_lives_in_its_own_column(store: ConfigStore) -> None:
    """api_key_env is a declared model field with a column of its own
    (PR #95 review finding 1); folding it into the options JSON would
    contradict options holding exactly the model extras, and a later
    reader of the raw row would miss it."""
    store.set_provider(
        "llm",
        "claude",
        {"type": "anthropic", "model": "claude-sonnet-5", "api_key_env": "ANTHROPIC_API_KEY"},
    )

    with store._engine.connect() as connection:
        row = connection.execute(schema.providers.select()).mappings().one()
    assert row["api_key_env"] == "ANTHROPIC_API_KEY"
    assert "api_key_env" not in row["options"]

    loaded = store.load().domain.providers.llm["claude"]
    assert loaded.api_key_env == "ANTHROPIC_API_KEY"
    assert loaded.options == {"model": "claude-sonnet-5"}


def test_building_up_from_empty_never_wedges(store: ConfigStore) -> None:
    """The deadlock the write-time check set is chosen to avoid: every
    intermediate state here fails the boot-only completeness rule, and
    none of them may be refused."""
    store.set_provider("llm", "claude", {"type": "anthropic", "model": "claude-sonnet-5"})
    store.set_agent_defaults({"llm": "claude"})
    # An agent with no default_agent naming it yet, which is the state
    # boot would refuse and a write must not.
    store.set_agent("sam", {"prompt": "You are Sam."})
    store.set_default_agent("sam")
    store.bind_device("aa:bb:cc:dd:ee:ff", ["sam"])

    assert store.load().domain.default_agent == "sam"


def test_a_stage_left_unresolved_does_not_block_a_write(store: ConfigStore) -> None:
    """An agent whose ASR resolves through neither its own entry nor the
    defaults is an unfinished deployment, not a broken entity. Provider
    construction is what refuses it, at boot."""
    store.set_agent("sam", {"prompt": "You are Sam."})

    assert store.load().domain.agents["sam"].asr is None


def test_clearing_the_default_agent_is_reachable(store: ConfigStore) -> None:
    _populate(store)

    store.clear_default_agent()

    assert store.load().domain.default_agent is None


def test_an_unknown_provider_reference_is_refused(store: ConfigStore) -> None:
    with pytest.raises(ConfigError, match='unknown llm provider "ghost"'):
        store.set_agent("sam", {"llm": "ghost"})

    assert store.load().domain.agents == {}


def test_an_unknown_mcp_reference_is_refused(store: ConfigStore) -> None:
    with pytest.raises(ConfigError, match='unknown MCP server "home"'):
        store.set_agent_defaults({"mcp": ["home"]})


def test_binding_a_device_to_an_unknown_agent_is_refused(store: ConfigStore) -> None:
    with pytest.raises(ConfigError, match='agent "ghost" is not a defined agent'):
        store.bind_device("aa:bb:cc:dd:ee:ff", ["ghost"])

    assert store.load().domain.devices == {}


def test_an_unknown_default_agent_is_refused(store: ConfigStore) -> None:
    with pytest.raises(ConfigError, match='default_agent "ghost" is not a defined agent'):
        store.set_default_agent("ghost")


def test_deleting_a_referenced_provider_is_refused(store: ConfigStore) -> None:
    _populate(store)

    with pytest.raises(ConfigError, match="unknown llm provider"):
        store.delete_provider("llm", "claude")

    assert "claude" in store.load().domain.providers.llm


def test_deleting_a_referenced_mcp_server_is_refused(store: ConfigStore) -> None:
    _populate(store)

    with pytest.raises(ConfigError, match="unknown MCP server"):
        store.delete_mcp_server("home")


def test_deleting_an_agent_a_device_is_bound_to_is_refused(store: ConfigStore) -> None:
    _populate(store)
    store.clear_default_agent()

    with pytest.raises(ConfigError, match='devices.aa:bb:cc:dd:ee:ff: agent "sam"'):
        store.delete_agent("sam")


def test_deleting_the_default_agent_is_refused(store: ConfigStore) -> None:
    _populate(store)
    store.delete_device("aa:bb:cc:dd:ee:ff")

    with pytest.raises(ConfigError, match='default_agent "sam" is not a defined agent'):
        store.delete_agent("sam")

    # Unbound and undefaulted, the same agent goes.
    store.clear_default_agent()
    store.delete_agent("sam")
    assert store.load().domain.agents == {}


def test_an_unfreed_entity_is_named_when_it_does_not_exist(store: ConfigStore) -> None:
    for call in (
        lambda: store.delete_provider("llm", "ghost"),
        lambda: store.delete_mcp_server("ghost"),
        lambda: store.delete_agent("ghost"),
        lambda: store.delete_device("aa:bb:cc:dd:ee:ff"),
    ):
        with pytest.raises(ConfigError, match="no such"):
            call()


def test_an_invalid_fragment_is_refused_without_quoting_it(store: ConfigStore) -> None:
    """The refusal names the key and the rule, and carries nothing of
    the fragment: not in the message, and not in the exception chain
    either.

    Both links are asserted, because clearing only the cause is not
    enough. An exception raised inside a handler keeps the one being
    handled as its __context__, and a pydantic ValidationError's
    errors() hold the complete rejected input, secret and all; its
    str() happens to truncate the middle of a long value, which is
    luck rather than a property to rely on."""
    with pytest.raises(ConfigError) as caught:
        store.set_provider("llm", "claude", {"type": "anthropic", "api_key": SECRET})

    message = str(caught.value)
    assert "providers.llm.claude" in message
    assert "api_key" in message
    assert SECRET not in _chain(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_a_secret_nested_inside_an_option_is_refused_too(store: ConfigStore) -> None:
    """Options are passed through to the provider implementation, so an
    option can be a structure and a secret-shaped key can sit inside
    one. A rule that only looked at the top level would accept it,
    store it, and read it back verbatim."""
    nested = [
        ({"type": "anthropic", "connection": {"api_key": SECRET}}, "connection.api_key"),
        (
            {"type": "anthropic", "backends": [{"auth": {"token": SECRET}}]},
            "backends.0.auth.token",
        ),
    ]
    for fragment, path in nested:
        with pytest.raises(ConfigError) as caught:
            store.set_provider("llm", "claude", fragment)

        assert path in str(caught.value)
        assert "looks like an inline secret" in str(caught.value)
        assert SECRET not in _chain(caught.value)
        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None


def test_a_nested_reference_key_must_still_name_a_variable(store: ConfigStore) -> None:
    with pytest.raises(ConfigError) as caught:
        store.set_provider(
            "llm", "claude", {"type": "anthropic", "connection": {"api_key_env": SECRET}}
        )

    assert "connection.api_key_env" in str(caught.value)
    assert SECRET not in _chain(caught.value)


def test_an_unknown_stage_and_an_empty_name_are_refused(store: ConfigStore) -> None:
    with pytest.raises(ConfigError, match="not a provider stage"):
        store.set_provider("speech", "x", {"type": "mock"})
    with pytest.raises(ConfigError, match="the name is empty"):
        store.set_agent("  ", {})
    with pytest.raises(ConfigError, match="not a MAC address"):
        store.delete_device("nonsense")


def test_a_name_that_cannot_be_a_path_segment_is_refused(store: ConfigStore) -> None:
    """An entity is addressed by putting its identity in a path segment,
    so a name holding a slash could never be fetched, replaced or
    deleted over the API: routing would read it as two segments. The
    rule is the repository's, so both callers inherit it."""
    refused = [
        lambda: store.set_provider("llm", "a/b", {"type": "anthropic"}),
        lambda: store.set_mcp_server("a/b", {"transport": "stdio", "command": "x"}),
        lambda: store.set_agent("a/b", {"prompt": "hello"}),
        lambda: store.set_agent("a\nb", {"prompt": "hello"}),
        lambda: store.set_provider("llm", "a\x7fb", {"type": "anthropic"}),
    ]
    for call in refused:
        with pytest.raises(ConfigError) as caught:
            call()
        message = str(caught.value)
        assert "URL path segment" in message
        assert "slash" in message or "control character" in message
        # The rule and the kind of character, never the name itself.
        assert "a/b" not in message and "a\nb" not in message


def test_a_name_that_only_needs_encoding_is_accepted(store: ConfigStore) -> None:
    """Spaces, percent signs and characters outside ASCII percent-encode
    and decode losslessly, so nothing about them is a problem to
    address."""
    for name in ("a name with spaces", "100%-sure", "agente-café"):
        store.set_provider("llm", name, {"type": "anthropic"})
        store.set_agent(name, {"prompt": "hello"})

        assert store.read_provider("llm", name).entry.type == "anthropic"
        assert store.read_agent(name).entry.prompt == "hello"


def test_a_slot_that_cannot_be_a_path_segment_is_refused(store: ConfigStore) -> None:
    """A slot rides in a path of its own, and each half of an MCP slot
    names something that could not hold a slash anyway: a variable for
    env, a header for headers."""
    _populate(store)
    refused = [
        SecretLocation.provider("llm", "claude", "api_key/extra"),
        SecretLocation.mcp_server("home", "env.API TOKEN"),
        SecretLocation.mcp_server("home", "env.a/b"),
        SecretLocation.mcp_server("home", "headers.Authorization/x"),
        SecretLocation.mcp_server("home", "headers.Auth orization"),
    ]
    for location in refused:
        with pytest.raises(ConfigError) as caught:
            store.set_secret(location, SECRET)
        assert type(caught.value) is ConfigError, caught.value
        assert SECRET not in str(caught.value)


def test_a_dotted_slot_round_trips(store: ConfigStore) -> None:
    _populate(store)
    store.set_secret(SecretLocation.mcp_server("home", "env.API_ACCESS_TOKEN"), SECRET)
    store.set_secret(SecretLocation.mcp_server("home", "headers.X-Api-Key"), SECRET)

    assert [item.location.slot for item in store.read_mcp_server("home").secrets] == [
        "env.API_ACCESS_TOKEN",
        "headers.X-Api-Key",
    ]


def test_a_number_that_is_not_finite_is_refused(store: ConfigStore) -> None:
    """NaN and the infinities have no JSON spelling, so a stored one is
    serialized as null on the way out: the option vanishes and the
    provider falls back to its own default, which is a different
    configuration from the one that was written. Refused where every
    other fragment rule is applied, at any depth and for every kind."""
    refused = [
        lambda: store.set_provider("llm", "claude", {"type": "anthropic", "temperature": nan}),
        lambda: store.set_provider(
            "llm", "claude", {"type": "anthropic", "sampling": {"top_p": inf}}
        ),
        lambda: store.set_mcp_server(
            "home", {"transport": "stdio", "command": "uvx", "tool_timeout_s": nan}
        ),
        lambda: store.set_agent_defaults({"filler": {"enabled": True, "delay_ms": nan}}),
    ]
    for call in refused:
        with pytest.raises(ConfigError) as caught:
            call()
        assert "not a finite number" in str(caught.value)
        assert type(caught.value) is ConfigError, caught.value

    # A finite one is exactly as acceptable as it was.
    store.set_provider("llm", "claude", {"type": "anthropic", "temperature": 0.7})
    assert store.read_provider("llm", "claude").entry.options == {"temperature": 0.7}


# Deleting what will not load
#
# The break-glass case: a row the loader refuses is the row that is
# keeping the server from starting, so it is the one that has to be
# removable. A delete that read and validated the whole domain first
# could not remove it, because the read failed on the way there.


def _corrupt_provider(store: ConfigStore, name: str) -> None:
    """A row whose JSON column holds something no model will load."""
    with store._engine.begin() as connection:
        connection.execute(
            update(schema.providers)
            .where(schema.providers.c.name == name)
            .values(options="not an object")
        )


def test_a_row_that_cannot_be_loaded_can_still_be_deleted(store: ConfigStore) -> None:
    store.set_provider("llm", "claude", {"type": "anthropic", "model": "m"})
    _corrupt_provider(store, "claude")
    # It is unreadable, which is what makes this the case that matters.
    with pytest.raises(ConfigError):
        store.load()

    store.delete_provider("llm", "claude")

    assert store.load().domain.providers.llm == {}


def test_a_row_that_cannot_be_loaded_is_deletable_for_every_kind(
    store: ConfigStore,
) -> None:
    store.set_mcp_server("home", {"transport": "stdio", "command": "uvx"})
    store.set_agent("sam", {"prompt": "You are Sam."})
    store.bind_device("aa:bb:cc:dd:ee:ff", ["sam"])
    with store._engine.begin() as connection:
        connection.execute(update(schema.mcp_servers).values(env="not an object"))
        connection.execute(update(schema.agents).values(mcp="not an array"))
        connection.execute(update(schema.devices).values(agents="not an array"))
    with pytest.raises(ConfigError):
        store.load()

    store.delete_device("aa:bb:cc:dd:ee:ff")
    store.delete_agent("sam")
    store.delete_mcp_server("home")

    domain = store.load().domain
    assert domain.mcp_servers == {}
    assert domain.agents == {}
    assert domain.devices == {}


def test_one_unreadable_row_does_not_make_everything_else_undeletable(
    store: ConfigStore,
) -> None:
    """The deadlock the tolerant check avoids. The reference pass runs on
    what remains, so an unreadable neighbour would otherwise refuse every
    delete, including the ones that would clear the way to removing it."""
    store.set_provider("llm", "claude", {"type": "anthropic", "model": "m"})
    store.set_agent("sam", {"prompt": "You are Sam.", "llm": "claude"})
    _corrupt_provider(store, "claude")
    # Refused while it is still referenced, as it should be.
    with pytest.raises(ConfigError, match="references unresolved"):
        store.delete_provider("llm", "claude")

    # The referrer comes out even though its neighbour will not load,
    # because that neighbour was already unreadable before this delete.
    store.delete_agent("sam")
    store.delete_provider("llm", "claude")

    assert store.load().domain.providers.llm == {}
    assert store.load().domain.agents == {}


def test_a_corrupt_row_something_still_references_is_refused(store: ConfigStore) -> None:
    """The reference check keeps the force it had. The deletion happens
    first inside the transaction and the check runs on what remains, so a
    refusal rolls the row back rather than leaving it half removed."""
    store.set_provider("llm", "claude", {"type": "anthropic", "model": "m"})
    store.set_agent("sam", {"prompt": "You are Sam.", "llm": "claude"})
    _corrupt_provider(store, "claude")

    with pytest.raises(ConfigError) as caught:
        store.delete_provider("llm", "claude")

    assert "references unresolved" in str(caught.value)
    # Nothing changed: the row is still there, still unreadable, which is
    # the rollback doing its work inside the one transaction.
    with store._engine.begin() as connection:
        rows = connection.execute(select(schema.providers.c.name)).scalars().all()
        options = connection.execute(select(schema.providers.c.options)).scalars().all()
    assert list(rows) == ["claude"]
    assert list(options) == ["not an object"]


def test_a_value_json_cannot_carry_is_refused(store: ConfigStore) -> None:
    """YAML is the wider language: `!!timestamp` gives a date,
    `!!binary` bytes, `!!set` a set. None of them has a JSON spelling,
    and all of them reach here as an ordinary fragment."""
    import datetime

    for fragment in (
        {"type": "anthropic", "released": datetime.date(2026, 1, 1)},
        {"type": "anthropic", "when": datetime.datetime(2026, 1, 1, 12, 0)},
        {"type": "anthropic", "blob": b"\x00\x01"},
        {"type": "anthropic", "tags": {"a", "b"}},
        {"type": "anthropic", "nested": {"deep": [1, {"worse": datetime.date(2026, 1, 1)}]}},
    ):
        with pytest.raises(ConfigError) as caught:
            store.set_provider("llm", "claude", fragment)
        assert "JSON has no way to write" in str(caught.value)
        assert type(caught.value) is ConfigError, caught.value

    assert store.load().domain.providers.llm == {}


def test_a_mapping_key_that_is_not_a_string_is_refused(store: ConfigStore) -> None:
    """The quiet one: JSON would not refuse this, it would stringify the
    key and hand a reader `"1"` where `1` was written."""
    for fragment in (
        {"type": "anthropic", "options": {1: "x"}},
        {"type": "anthropic", "options": {None: "x"}},
        {"type": "anthropic", "options": {(1, 2): "x"}},
    ):
        with pytest.raises(ConfigError) as caught:
            store.set_provider("llm", "claude", fragment)
        assert "rather than a string" in str(caught.value)

    assert store.load().domain.providers.llm == {}


def test_a_fragment_that_contains_itself_is_refused(store: ConfigStore) -> None:
    """A YAML anchor can build one, and walking it is what would
    otherwise end in a RecursionError rather than a sentence."""
    recursive: dict[str, object] = {"type": "anthropic"}
    recursive["self"] = recursive
    looping: list[object] = []
    looping.append(looping)

    for fragment in (recursive, {"type": "anthropic", "items": looping}):
        with pytest.raises(ConfigError) as caught:
            store.set_provider("llm", "claude", fragment)
        assert "contains itself" in str(caught.value)

    assert store.load().domain.providers.llm == {}


def test_two_keys_sharing_one_anchor_are_not_recursion(store: ConfigStore) -> None:
    """The shape a naive seen-set would refuse: an anchored mapping used
    twice is written out twice and read back correctly, so it is a
    perfectly ordinary YAML file."""
    shared = {"a": 1}

    store.set_provider("llm", "claude", {"type": "anthropic", "one": shared, "two": shared})

    entry = store.read_provider("llm", "claude").entry
    assert entry.options == {"one": {"a": 1}, "two": {"a": 1}}


def test_a_stored_number_that_is_not_finite_cannot_be_read(store: ConfigStore) -> None:
    """A row written before the rule, or by something else: reported as
    unreadable rather than answered with a value nobody wrote."""
    store.set_provider("llm", "claude", {"type": "anthropic", "temperature": 0.7})
    with store._engine.begin() as connection:
        connection.execute(update(schema.providers).values(options={"temperature": nan}))

    with pytest.raises(StorageError) as caught:
        store.load()

    assert "not a finite number" in str(caught.value)
    assert "cannot be read" in str(caught.value)


def test_no_refusal_carries_the_exception_that_caused_it(store: ConfigStore) -> None:
    """Every refusal built from another exception is built inside the
    handler and raised outside it. `from None` clears the cause and
    leaves the context, and whatever a library raised is still reachable
    from the exception that travels out: its message, its arguments and,
    for a parser, the buffer it was reading."""
    _populate(store)
    calls = [
        lambda: store.delete_device(f"not-a-mac-{SECRET}"),
        lambda: store.bind_device(f"not-a-mac-{SECRET}", ["sam"]),
        lambda: store.read_device(f"not-a-mac-{SECRET}"),
        lambda: store.set_mcp_server("not a usable name", {"transport": "stdio", "command": "x"}),
        lambda: store.set_agent("poet", {"prompt": SECRET, "llm": "ghost"}),
    ]
    for call in calls:
        with pytest.raises(ConfigError) as caught:
            call()
        assert caught.value.__cause__ is None, caught.value
        assert caught.value.__context__ is None, caught.value


def test_replacing_an_entity_keeps_its_stored_secrets(store: ConfigStore) -> None:
    """A fragment cannot carry ciphertext, so a whole-row replacement
    would erase every stored secret on an ordinary edit."""
    store.set_provider("llm", "claude", {"type": "anthropic", "model": "claude-sonnet-5"})
    store.set_secret(CLAUDE, SECRET)

    store.set_provider("llm", "claude", {"type": "anthropic", "model": "claude-haiku"})

    snapshot = store.load()
    assert snapshot.domain.providers.llm["claude"].options == {"model": "claude-haiku"}
    assert snapshot.secrets.secret(CLAUDE) == SECRET


def test_deleting_an_entity_deletes_its_stored_secrets(store: ConfigStore) -> None:
    store.set_provider("llm", "claude", {"type": "anthropic", "model": "claude-sonnet-5"})
    store.set_secret(CLAUDE, SECRET)

    store.delete_provider("llm", "claude")

    assert store.load().secrets.locations() == []


def test_a_secret_can_be_set_and_cleared_on_both_entity_kinds(store: ConfigStore) -> None:
    store.set_provider("llm", "claude", {"type": "anthropic", "model": "claude-sonnet-5"})
    store.set_mcp_server(
        "weather", {"transport": "streamable_http", "url": "https://example.invalid/mcp"}
    )

    store.set_secret(CLAUDE, SECRET)
    store.set_secret(WEATHER, SECRET)
    assert store.load().secrets.locations() == [WEATHER, CLAUDE]

    store.clear_secret(WEATHER)
    assert store.load().secrets.locations() == [CLAUDE]


def test_a_secret_for_an_unknown_entity_or_slot_is_refused(store: ConfigStore) -> None:
    store.set_provider("llm", "claude", {"type": "anthropic", "model": "claude-sonnet-5"})

    for location in (
        SecretLocation.provider("llm", "ghost", "api_key"),
        SecretLocation.provider("llm", "claude", "model"),
        SecretLocation.provider("llm", "claude", "api_key_env"),
        SecretLocation.mcp_server("ghost", "env.TOKEN"),
    ):
        with pytest.raises(ConfigError) as caught:
            store.set_secret(location, SECRET)
        assert SECRET not in _chain(caught.value)


def test_a_secret_that_is_not_a_non_empty_string_is_refused(store: ConfigStore) -> None:
    """An annotation stops nothing: a null, a number or an object
    arriving from a request body would otherwise be encrypted into an
    envelope that fails verification at the next boot, which is a refusal
    to start earned by a write that answered "wrote"."""
    store.set_provider("llm", "claude", {"type": "anthropic", "model": "m"})

    for value in (None, "", 1, {"secret": SECRET}, [SECRET]):
        with pytest.raises(ConfigError) as caught:
            store.set_secret(CLAUDE, value)  # type: ignore[arg-type]
        assert "non-empty string" in str(caught.value)
        assert SECRET not in _chain(caught.value)

    assert store.load().secrets.locations() == []


def test_storing_a_secret_without_a_key_is_refused(tmp_path: Path) -> None:
    """The one command that needs a key. Everything else treats
    ciphertext as opaque, so the CLI stays usable as the recovery tool
    when the key is missing or wrong."""
    engine = open_database(tmp_path / "db")
    try:
        keyless = ConfigStore(engine)
        keyless.set_provider("llm", "claude", {"type": "anthropic", "model": "claude-sonnet-5"})

        with pytest.raises(ConfigError) as caught:
            keyless.set_secret(CLAUDE, SECRET)

        assert CLAUDE.describe() in str(caught.value)
        assert SECRET not in _chain(caught.value)
    finally:
        engine.dispose()


def test_verify_secrets_passes_when_every_token_opens(store: ConfigStore) -> None:
    store.set_provider("llm", "claude", {"type": "anthropic", "model": "claude-sonnet-5"})
    store.set_secret(CLAUDE, SECRET)

    verify_secrets(store.load().secrets)


def test_verify_secrets_names_the_entity_and_slot_it_cannot_open(
    tmp_path: Path, keys: MultiFernet
) -> None:
    engine = open_database(tmp_path / "db")
    try:
        store = ConfigStore(engine, keys)
        store.set_provider("llm", "claude", {"type": "anthropic", "model": "claude-sonnet-5"})
        store.set_secret(CLAUDE, SECRET)

        # A wrong key, and no key at all: the two ways a deployment
        # arrives at a database it cannot read.
        for wrong in (MultiFernet([Fernet(generate_key())]), None):
            with pytest.raises(ConfigError) as caught:
                verify_secrets(ConfigStore(engine, wrong).load().secrets)
            assert CLAUDE.describe() in str(caught.value)
            assert SECRET not in _chain(caught.value)

        # And a token that is not a token, which is what a hand-edited
        # or half-restored database looks like.
        with engine.begin() as connection:
            connection.execute(
                update(schema.providers).values(secrets={"api_key": {"enc": "rubbish"}})
            )
        with pytest.raises(ConfigError, match=CLAUDE.describe()):
            verify_secrets(store.load().secrets)
    finally:
        engine.dispose()


def test_a_row_that_is_not_loadable_is_reported_as_a_config_error(
    tmp_path: Path, keys: MultiFernet
) -> None:
    """A hand-edited database is the case this exists for: the failure
    names the entry, and no pydantic traceback reaches the caller."""
    engine = open_database(tmp_path / "db")
    try:
        store = ConfigStore(engine, keys)
        store.set_provider("llm", "claude", {"type": "anthropic", "model": "claude-sonnet-5"})
        with engine.begin() as connection:
            connection.execute(update(schema.providers).values(type=""))

        with pytest.raises(ConfigError) as caught:
            store.load()

        assert "providers.llm.claude" in str(caught.value)
        assert caught.value.__cause__ is None
    finally:
        engine.dispose()


CORRUPTIONS = [
    ("providers", "options", "not an object"),
    ("providers", "secrets", "not an object"),
    ("mcp_servers", "args", "not an array"),
    ("mcp_servers", "env", ["not", "an", "object"]),
    ("mcp_servers", "headers", "not an object"),
    ("mcp_servers", "secrets", "not an object"),
    ("agents", "mcp", "not an array"),
    ("agents", "filler", ["not", "an", "object"]),
    ("agent_defaults", "mcp", "not an array"),
    ("agent_defaults", "filler", "not an object"),
    ("devices", "agents", "sam"),
    ("domain_settings", "value", {"not": "a string"}),
]


@pytest.mark.parametrize(("table", "column", "written"), CORRUPTIONS)
def test_a_json_column_of_the_wrong_shape_is_a_config_error(
    store: ConfigStore, table: str, column: str, written: object
) -> None:
    """SQLite enforces no shape on a JSON column, so a hand-edited or
    half-restored row can hold a string where a mapping belongs. Every
    reader would then raise a TypeError or an AttributeError, which is
    neither a database error nor a validation error, and would travel
    straight through the sanitized boundary as a traceback.

    The devices case is the one that fails silently rather than loudly
    without this: iterating a string succeeds and binds the device to
    one agent per character."""
    _populate(store)
    store.set_agent("poet", {"prompt": "p", "mcp": [], "filler": {"enabled": False}})

    with store._engine.begin() as connection:
        connection.execute(update(getattr(schema, table)).values(**{column: written}))

    with pytest.raises(ConfigError) as caught:
        store.load()

    message = str(caught.value)
    assert column in message
    assert str(written) not in message
    assert caught.value.__cause__ is None


def test_a_corrupt_json_column_does_not_stop_a_secret_being_cleared(
    store: ConfigStore,
) -> None:
    """The recovery direction: a secrets column that is not an object
    still refuses in words rather than in a traceback."""
    _populate(store)

    with store._engine.begin() as connection:
        connection.execute(update(schema.providers).values(secrets="not an object"))

    with pytest.raises(ConfigError) as caught:
        store.clear_secret(CLAUDE)

    assert "secrets" in str(caught.value)
    assert caught.value.__cause__ is None


def test_two_concurrent_writers_serialize(
    tmp_path: Path, keys: MultiFernet, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One writer deletes a provider while the other writes an agent
    that references it. Whichever runs second sees the first one's
    change, so exactly one of them is refused for the reference it would
    leave unresolved, and the database never ends up holding one.

    The pacing below is what makes the failure deterministic rather than
    a race that usually does not happen: each writer announces that it
    has read the snapshot and then waits for the other to announce the
    same. Under BEGIN IMMEDIATE that wait always times out, because the
    second writer cannot have read anything while the first holds the
    write lock. Under a deferred BEGIN both read the state before either
    change, and the invariant this asserts is what breaks.
    """
    from samtal_server.config import store as store_module

    directory = tmp_path / "db"
    setup = open_database(directory)
    try:
        ConfigStore(setup, keys).set_provider("llm", "claude", {"type": "anthropic", "model": "m"})
    finally:
        setup.dispose()

    names = ("delete", "reference")
    has_read = {name: threading.Event() for name in names}
    read_domain = store_module._read_domain

    def paced(connection):
        domain = read_domain(connection)
        name = threading.current_thread().name
        if name in has_read:
            has_read[name].set()
            other = names[1] if name == names[0] else names[0]
            has_read[other].wait(timeout=0.5)
        return domain

    monkeypatch.setattr(store_module, "_read_domain", paced)

    start = threading.Barrier(2)
    outcomes: list[BaseException | None] = []
    lock = threading.Lock()

    def writer(change) -> None:
        engine = open_database(directory)
        store = ConfigStore(engine, keys)
        try:
            start.wait(timeout=10)
            try:
                change(store)
                outcome: BaseException | None = None
            except BaseException as exc:  # noqa: BLE001 - reported, not swallowed
                outcome = exc
            with lock:
                outcomes.append(outcome)
        finally:
            engine.dispose()

    def delete(store: ConfigStore) -> None:
        store.delete_provider("llm", "claude")

    def reference(store: ConfigStore) -> None:
        store.set_agent("sam", {"llm": "claude"})

    threads = [
        threading.Thread(target=writer, args=(change,), name=name)
        for name, change in zip(names, (delete, reference), strict=True)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert len(outcomes) == 2
    refused = [outcome for outcome in outcomes if outcome is not None]
    assert len(refused) == 1, outcomes
    assert isinstance(refused[0], ConfigError)
    # Refused for what it would have left unresolved, not for a lock it
    # could not take: the loser waited, and then read the winner's state.
    assert "references unresolved" in str(refused[0])

    engine = open_database(directory)
    try:
        from samtal_server.config.models import check_references

        assert check_references(ConfigStore(engine, keys).load().domain) == []
    finally:
        engine.dispose()
