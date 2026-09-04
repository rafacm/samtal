"""The repository: what it loads, what it writes, and what it refuses.

Two properties carry most of this file. Every reference resolves after
every write, which is what makes the database always loadable by a
server; and no completeness rule is enforced at write time, which is
what lets a deployment be built up from nothing in the natural order
without the first agent and default_agent deadlocking on each other.
"""

import json
import threading
from pathlib import Path

import pytest
from cryptography.fernet import Fernet, MultiFernet
from sqlalchemy import insert, select, update

from tests.support.stores import planted, stored_row, stored_rows
from vinga_server.config import ConfigError
from vinga_server.config.loader import StorageError, UnknownEntityError, compose_config
from vinga_server.config.models import (
    NOT_A_MAC,
    PROVIDER_STAGES,
    DatabaseConfig,
    FileConfig,
    domain_fields,
    mcp_entry_fragment,
)
from vinga_server.config.secrets import MASK, SecretLocation, generate_key
from vinga_server.config.store import ConfigStore, verify_secrets
from vinga_server.db import open_database, schema

# Not a real credential, and shaped so a substring check for it cannot
# match by accident.
SECRET = "sk-test-4f8b2c9e-never-a-real-credential"

# The other shape a credential arrives in: one that reads like the name
# of an environment variable, so a write accepts it and the display
# refuses to show it, which is what makes a read of it carry the mask.
PASTED = "sk_test_4f8b2c9e_never_a_real_credential"

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
    engine = open_database(DatabaseConfig())
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
    """Each form is stored as itself. The body is the model's own dump,
    so what a read shows is the fragment a write of it takes back, and
    the object form gains no `tools: null` it was not written with:
    `exclude_unset` carries the difference through the dump."""
    _populate(store)
    written = [
        "home",
        {"server": "weather", "tools": ["forecast"]},
        {"server": "shed"},
    ]
    store.set_mcp_server("weather", {"transport": "stdio", "command": "uvx"})
    store.set_mcp_server("shed", {"transport": "stdio", "command": "uvx"})
    store.set_agent("poet", {"prompt": "You are a poet.", "mcp": written})

    row = stored_row(store, select(schema.agents.c.body).where(schema.agents.c.name == "poet"))
    assert json.loads(row["body"])["mcp"] == written

    entry = store.load().domain.agents["poet"]
    assert entry.mcp is not None
    assert [mcp_entry_fragment(item) for item in entry.mcp] == written


def test_an_entrys_guidance_round_trips_byte_for_byte(store: ConfigStore) -> None:
    """The field is promised verbatim, so what the body holds and what a
    read gives back are the bytes that were written: the indentation and
    the trailing newline are somebody's own formatting of a prompt."""
    _populate(store)
    written = "  Ask before unlocking the door.\n\n    The lights are safe.\n"
    store.set_mcp_server(
        "weather", {"transport": "stdio", "command": "uvx", "instructions": written}
    )

    row = stored_row(
        store,
        select(schema.mcp_servers.c.body).where(schema.mcp_servers.c.name == "weather"),
    )
    assert json.loads(row["body"])["instructions"] == written
    assert store.load().domain.mcp_servers["weather"].instructions == written
    assert store.read_mcp_server("weather").entry.instructions == written


def test_a_body_written_before_the_guidance_field_loads_unchanged(
    store: ConfigStore,
) -> None:
    """A body carries what the operator wrote and nothing else, so a body
    written before the field existed simply has no key for it and the
    entry reads with the field's declared default. Hand-written, because
    what is pinned is the reader: a dump of today's model would have to
    be talked into omitting a key it knows about."""
    _populate(store)
    planted(
        store,
        update(schema.mcp_servers)
        .where(schema.mcp_servers.c.name == "home")
        .values(body='{"transport": "stdio", "command": "uvx", "egress": false}'),
    )

    entry = store.load().domain.mcp_servers["home"]
    assert entry.instructions is None
    assert entry.command == "uvx"


def test_the_server_guidance_opt_ins_round_trip(store: ConfigStore) -> None:
    """Both channels' opt-ins are in the body as the model declares them,
    so what a write says about trusting a third party is what a load
    reads."""
    _populate(store)
    store.set_mcp_server(
        "weather",
        {
            "transport": "stdio",
            "command": "uvx",
            "use_server_instructions": True,
            "inject_prompts": ["house_style", "safety"],
        },
    )

    row = stored_row(
        store,
        select(schema.mcp_servers.c.body).where(schema.mcp_servers.c.name == "weather"),
    )
    stored = json.loads(row["body"])
    assert stored["use_server_instructions"]
    assert stored["inject_prompts"] == ["house_style", "safety"]

    entry = store.load().domain.mcp_servers["weather"]
    assert entry.use_server_instructions is True
    assert entry.inject_prompts == ["house_style", "safety"]


def test_an_entry_that_names_neither_opt_in_loads_as_opted_out(
    store: ConfigStore,
) -> None:
    """Off by default is the model's default, and a body that names
    neither key reads at it. That is where the trust decision lives now:
    a body says what the operator wrote, and consuming a third party's
    words is something they have to have written."""
    _populate(store)

    entry = store.load().domain.mcp_servers["home"]

    assert entry.use_server_instructions is False
    assert entry.inject_prompts is None


def test_a_fragment_round_trips_byte_for_byte(store: ConfigStore) -> None:
    """A fragment is injected into a prompt as it stands, so what the
    body holds and what a read gives back are the bytes that were
    written, indentation and trailing blank lines included."""
    _populate(store)
    written = "  The bins go out on Tuesday.\n\n    The radio is called Bosse.\n"
    store.set_prompt_fragment("household", {"text": written})

    row = stored_row(
        store,
        select(schema.prompt_fragments.c.body).where(
            schema.prompt_fragments.c.name == "household"
        ),
    )
    assert json.loads(row["body"])["text"] == written
    assert store.load().domain.prompt_fragments["household"].text == written
    assert store.read_prompt_fragment("household").entry.text == written


@pytest.mark.parametrize("name", ["household", SECRET, f"{SECRET}.pasted"])
def test_a_fragment_that_is_not_there_is_named_by_its_section_only(
    store: ConfigStore, name: str
) -> None:
    """The one refusal in this section that could carry a value nothing
    validated. A name that addresses no fragment arrived in a URL path
    or on a command line and was never written here, so what comes back
    names the section and the fact, and the operator can see what they
    typed without this server repeating it."""
    for call in (
        lambda: store.read_prompt_fragment(name),
        lambda: store.delete_prompt_fragment(name),
    ):
        with pytest.raises(UnknownEntityError) as caught:
            call()

        assert str(caught.value).startswith("prompt_fragments:")
        assert SECRET not in _chain(caught.value)


# Every body a write of an unusable name can carry, because the name is
# refused before any of them is looked at. The bad ones are the point: a
# refusal about a body names the location it was written at, and that
# location is the name.
UNUSABLE_BODIES: list[object] = [
    {"text": "a"},
    {},
    None,
    {"text": ""},
    {"text": 4},
    {"text": "a", "extra": "b"},
    "not a mapping at all",
    {"text": SECRET},
]


@pytest.mark.parametrize("body", UNUSABLE_BODIES)
def test_an_unusable_fragment_name_is_refused_without_being_quoted(
    store: ConfigStore, body: object
) -> None:
    """The name is checked first, whatever else is wrong with the write.

    The order is the assertion. Every refusal about a body says where
    the body was written, and for a fragment that is
    `prompt_fragments.<name>`, so a request that gets both wrong at once
    would otherwise be answered by a sentence about the body carrying
    the name that must not be repeated.
    """
    with pytest.raises(ConfigError) as caught:
        store.set_prompt_fragment(f"{SECRET}.pasted", body)

    rendered = _chain(caught.value)
    assert "prompt_fragments" in rendered
    assert "[A-Za-z0-9_-]+" in rendered
    assert SECRET not in rendered
    assert store.load().domain.prompt_fragments == {}


def test_a_usable_name_with_an_unusable_body_names_the_location(
    store: ConfigStore,
) -> None:
    """The other side of that order: a name that passed the rule is one
    this deployment wrote, so a refusal about its body says which
    fragment it is about."""
    with pytest.raises(ConfigError) as caught:
        store.set_prompt_fragment("household", {"text": ""})

    assert "prompt_fragments.household" in str(caught.value)
    assert "only whitespace" in str(caught.value)


@pytest.mark.parametrize("layer", ["agent_defaults", "agents"])
def test_an_include_list_round_trips_write_shaped(store: ConfigStore, layer: str) -> None:
    """Both layers hold the list the way it was written, so a read of
    one is a fragment a write of it accepts back."""
    _populate(store)
    store.set_prompt_fragment("household", {"text": "The bins go out on Tuesday."})
    written = ["household"]
    if layer == "agent_defaults":
        store.set_agent_defaults({"llm": "claude", "prompt_includes": written})
        table, where = schema.agent_defaults, schema.agent_defaults.c.id
        identity = schema.AGENT_DEFAULTS_ID
    else:
        store.set_agent("poet", {"prompt": "P", "prompt_includes": written})
        table, where = schema.agents, schema.agents.c.name
        identity = "poet"

    row = stored_row(store, select(table.c.body).where(where == identity))
    assert json.loads(row["body"])["prompt_includes"] == written

    domain = store.load().domain
    entry = domain.agent_defaults if layer == "agent_defaults" else domain.agents["poet"]
    assert entry.prompt_includes == written


def test_an_empty_include_list_is_stored_apart_from_an_unset_one(
    store: ConfigStore,
) -> None:
    """None is inherit and `[]` is opt out, so the column has to keep
    them apart the way the mcp column does."""
    _populate(store)
    store.set_agent("poet", {"prompt": "P", "prompt_includes": []})
    store.set_agent("critic", {"prompt": "C"})

    agents = store.load().domain.agents
    assert agents["poet"].prompt_includes == []
    assert agents["critic"].prompt_includes is None


def test_a_body_written_before_the_includes_field_loads_unchanged(
    store: ConfigStore,
) -> None:
    """Unset is inherit, and a body written before the field existed has
    no key for it, so both layers read as including nothing of their own
    rather than as unreadable rows. Hand-written for the reason the
    guidance pin above gives."""
    _populate(store)
    planted(
        store,
        update(schema.agents)
        .where(schema.agents.c.name == "sam")
        .values(body='{"prompt": "You are Sam."}'),
        update(schema.agent_defaults)
        .where(schema.agent_defaults.c.id == schema.AGENT_DEFAULTS_ID)
        .values(
            body='{"llm": "claude", "asr": "whisper", "tts": "voice", '
            '"vad": "silero", "mcp": ["home"]}'
        ),
    )

    domain = store.load().domain
    assert domain.agents["sam"].prompt_includes is None
    assert domain.agents["sam"].prompt == "You are Sam."
    assert domain.agent_defaults.prompt_includes is None
    assert domain.agent_defaults.llm == "claude"


@pytest.mark.parametrize("layer", ["agent_defaults", "agents"])
def test_an_unknown_include_is_refused_by_position_and_never_by_value(
    store: ConfigStore, layer: str
) -> None:
    """A rejected include may be a pasted credential, and this refusal
    leaves the repository as a printed line, an HTTP body and a log
    record, so the sentinel is looked for in the whole chain behind the
    exception as well as in its message."""
    _populate(store)
    write = (
        (lambda: store.set_agent_defaults({"llm": "claude", "prompt_includes": [SECRET]}))
        if layer == "agent_defaults"
        else (lambda: store.set_agent("poet", {"prompt": "P", "prompt_includes": [SECRET]}))
    )

    with pytest.raises(ConfigError) as caught:
        write()

    rendered = _chain(caught.value)
    assert "prompt_includes: entry 1" in rendered
    assert SECRET not in rendered


def test_a_fragment_an_agent_includes_cannot_be_deleted(store: ConfigStore) -> None:
    """The reference pass every delete runs, applied to the section that
    is now referenced: taking a fragment away would leave an agent
    including nothing."""
    _populate(store)
    store.set_prompt_fragment("household", {"text": "The bins go out on Tuesday."})
    store.set_agent("poet", {"prompt": "P", "prompt_includes": ["household"]})

    with pytest.raises(ConfigError, match="prompt_includes"):
        store.delete_prompt_fragment("household")

    assert "household" in store.load().domain.prompt_fragments


def test_an_unincluded_fragment_deletes(store: ConfigStore) -> None:
    _populate(store)
    store.set_prompt_fragment("household", {"text": "a"})

    store.delete_prompt_fragment("household")

    assert store.load().domain.prompt_fragments == {}
    with pytest.raises(UnknownEntityError):
        store.delete_prompt_fragment("household")


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
    with pytest.raises(ConfigError, match="agents.poet.mcp: entry 1 names no MCP server"):
        store.set_agent("poet", {"prompt": "P", "mcp": [{"server": "ghost", "tools": ["a"]}]})


def test_a_loaded_snapshot_has_no_unresolved_references(store: ConfigStore) -> None:
    from vinga_server.config.models import check_completeness, check_references

    _populate(store)
    domain = store.load().domain

    assert check_references(domain) == []
    assert check_completeness(domain) == []


def test_the_credential_reference_survives_beside_the_options(store: ConfigStore) -> None:
    """api_key_env is a declared field and `options` is exactly the model
    extras, so the two are one dump and two different things on the way
    back out. It used to be two columns, and the risk the split carried
    was that a reader of the raw row would miss the declared half; the
    body carries both because the model does."""
    store.set_provider(
        "llm",
        "claude",
        {"type": "anthropic", "model": "claude-sonnet-5", "api_key_env": "ANTHROPIC_API_KEY"},
    )

    row = stored_row(store, schema.providers.select())
    assert json.loads(row["body"]) == {
        "type": "anthropic",
        "api_key_env": "ANTHROPIC_API_KEY",
        "model": "claude-sonnet-5",
    }

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
    with pytest.raises(ConfigError, match="agents.sam.llm: names no llm provider"):
        store.set_agent("sam", {"llm": "ghost"})

    assert store.load().domain.agents == {}


def test_an_unknown_mcp_reference_is_refused(store: ConfigStore) -> None:
    with pytest.raises(
        ConfigError, match="agent_defaults.mcp: entry 1 names no MCP server"
    ):
        store.set_agent_defaults({"mcp": ["home"]})


def test_binding_a_device_to_an_unknown_agent_is_refused(store: ConfigStore) -> None:
    with pytest.raises(ConfigError, match="entry 1 names no agent that exists"):
        store.bind_device("aa:bb:cc:dd:ee:ff", ["ghost"])

    assert store.load().domain.devices == {}


def test_an_unknown_default_agent_is_refused(store: ConfigStore) -> None:
    with pytest.raises(ConfigError, match="default_agent: names no agent that exists"):
        store.set_default_agent("ghost")


def test_deleting_a_referenced_provider_is_refused(store: ConfigStore) -> None:
    _populate(store)

    with pytest.raises(ConfigError, match="names no llm provider that exists"):
        store.delete_provider("llm", "claude")

    assert "claude" in store.load().domain.providers.llm


def test_deleting_a_referenced_mcp_server_is_refused(store: ConfigStore) -> None:
    _populate(store)

    with pytest.raises(ConfigError, match="names no MCP server that exists"):
        store.delete_mcp_server("home")


def test_deleting_an_agent_a_device_is_bound_to_is_refused(store: ConfigStore) -> None:
    _populate(store)
    store.clear_default_agent()

    with pytest.raises(
        ConfigError, match="devices.aa:bb:cc:dd:ee:ff: entry 1 names no agent that exists"
    ):
        store.delete_agent("sam")


def test_deleting_the_default_agent_is_refused(store: ConfigStore) -> None:
    _populate(store)
    store.delete_device("aa:bb:cc:dd:ee:ff")

    with pytest.raises(ConfigError, match="default_agent: names no agent that exists"):
        store.delete_agent("sam")

    # Unbound and undefaulted, the same agent goes.
    store.clear_default_agent()
    store.delete_agent("sam")
    assert store.load().domain.agents == {}


def test_an_unfreed_entity_is_refused_by_its_section(store: ConfigStore) -> None:
    """A delete of something that is not there is refused by the section
    it would have been in, and never repeats the identity that was asked
    for (#132)."""
    ghost = "ghost"
    ghost_mac = "aa:bb:cc:dd:ee:ff"
    for call, section, identity in (
        (lambda: store.delete_provider("llm", ghost), "providers", ghost),
        (lambda: store.delete_mcp_server(ghost), "mcp_servers", ghost),
        (lambda: store.delete_prompt_fragment(ghost), "prompt_fragments", ghost),
        (lambda: store.delete_agent(ghost), "agents", ghost),
        (lambda: store.delete_device(ghost_mac), "devices", ghost_mac),
    ):
        with pytest.raises(ConfigError) as caught:
            call()
        refusal = str(caught.value)
        assert refusal.startswith(f"{section}:"), refusal
        assert identity not in refusal, refusal


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
        ({"type": "anthropic", "connection": {"api_key": SECRET}}, "api_key"),
        ({"type": "anthropic", "backends": [{"auth": {"token": SECRET}}]}, "token"),
    ]
    for fragment, fragment_matched in nested:
        with pytest.raises(ConfigError) as caught:
            store.set_provider("llm", "claude", fragment)

        message = str(caught.value)
        assert "looks like an inline secret" in message
        # The closed fragment the key matched, and not the key: an
        # option is a name the caller wrote, and a name is as good a
        # place to paste a credential as a value is.
        assert f'a key containing "{fragment_matched}"' in message
        for key in ("connection", "backends", "auth"):
            assert key not in message
        assert SECRET not in _chain(caught.value)
        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None


def test_a_nested_reference_key_must_still_name_a_variable(store: ConfigStore) -> None:
    with pytest.raises(ConfigError) as caught:
        store.set_provider(
            "llm", "claude", {"type": "anthropic", "connection": {"api_key_env": SECRET}}
        )

    message = str(caught.value)
    assert "a key ending in _env must hold the name of an environment variable" in message
    assert "connection" not in message
    assert SECRET not in _chain(caught.value)


def test_an_unknown_stage_and_an_empty_name_are_refused(store: ConfigStore) -> None:
    """The stage refusal names the four stages, which are constants of
    this server, and never the word that was sent, which is a path
    segment nothing has validated (#132)."""
    with pytest.raises(ConfigError) as caught:
        store.set_provider("speech", "x", {"type": "mock"})
    refusal = str(caught.value)
    assert refusal.startswith("providers:")
    assert all(stage in refusal for stage in PROVIDER_STAGES), refusal
    assert "speech" not in refusal, refusal
    with pytest.raises(ConfigError, match="the name is empty"):
        store.set_agent("  ", {})
    with pytest.raises(ConfigError) as refused_mac:
        store.delete_device("nonsense")
    # The same rule one section over, and the same discipline: the MAC
    # refusal states what a MAC is and never what was sent (#205).
    assert str(refused_mac.value) == NOT_A_MAC
    assert "nonsense" not in str(refused_mac.value)


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


def test_a_provider_url_carrying_a_credential_is_refused(store: ConfigStore) -> None:
    """The one shape that gets past the secret-shaped-key rules: an
    innocent key holding a URL with the credential inside it. Stored, it
    is read back on every display path and copied into the manifest of
    every capture and conversation record made against the provider, so
    it is refused where it is chosen."""
    # `model` rides along on the openai_compatible fragments because that
    # type declares its options now, and the options are checked before
    # this rule is asked: a fragment missing a required field would be
    # refused for the wrong reason and prove nothing about this one.
    refused = [
        {"type": "openai_compatible", "model": "m", "base_url": f"https://user:{SECRET}@host/v1"},
        {"type": "openai_compatible", "model": "m", "base_url": f"https://{SECRET}@host/v1"},
        {"type": "openai_compatible", "model": "m", "base_url": f"https://host/v1?api_key={SECRET}"},
        # A query parameter is the vendor's word rather than one this
        # repository or a provider type declared, so the rule reads the
        # wider set of names and `?auth=` is refused here too (#279).
        {"type": "openai_compatible", "model": "m", "base_url": f"https://host/v1?auth={SECRET}"},
        # And in a key that type does not declare at all, which is the
        # question its escape hatch raises: an option nobody declared is
        # kept and forwarded now, so the rules that read values rather
        # than fields have to keep reading it (#88).
        {
            "type": "openai_compatible",
            "model": "m",
            "base_url": "https://host/v1",
            "fallback_url": f"https://user:{SECRET}@other/v1",
        },
        # An option can be a structure, so the rule looks at every depth.
        {"type": "mock", "connection": {"endpoint": f"https://user:{SECRET}@host"}},
        {"type": "mock", "endpoints": [f"https://user:{SECRET}@host"]},
    ]
    for fragment in refused:
        with pytest.raises(ConfigError) as caught:
            store.set_provider("llm", "vendor", fragment)
        message = str(caught.value)
        assert "not allowed" in message
        assert "api_key_env" in message, "the refusal does not say what to do instead"
        # The rule and the option, never the value: what fails this
        # check is a credential.
        assert SECRET not in message
        assert SECRET not in _chain(caught.value)
    assert store.load().domain.providers.llm == {}


def test_an_ordinary_provider_url_is_accepted(store: ConfigStore) -> None:
    for base_url in (
        "https://api.vendor.example/v1",
        "http://127.0.0.1:8080/v1?model=small",
        "ws://[2001:db8::1]:9000/stream",
    ):
        store.set_provider(
            "llm", "vendor", {"type": "openai_compatible", "model": "m", "base_url": base_url}
        )
        assert store.read_provider("llm", "vendor").entry.options["base_url"] == base_url


def test_the_url_rule_is_write_time_only() -> None:
    """The addressability rule's precedent: a row written before this
    rule still boots, still reads and is still deletable. A deployment
    does not get a server that refuses to start over a value it can no
    longer edit; the record is defended on its own side instead."""
    from vinga_server.config import Config

    config = Config(
        providers={
            "llm": {
                "vendor": {
                    "type": "openai_compatible",
                    "base_url": f"https://user:{SECRET}@host/v1",
                }
            }
        }
    )
    assert config.providers.llm["vendor"].options["base_url"].endswith("@host/v1")


def test_an_mcp_url_carrying_a_credential_is_refused(store: ConfigStore) -> None:
    """The same shape one section over, and the reason it needed a rule
    of its own: a provider's address is an option, checked by the walk
    over a pass-through model, and an MCP server's is a declared field of
    a closed one that no walk reaches. Stored, it is read back on every
    display path, so it is refused where it is chosen (#279)."""
    refused = [
        {"transport": "streamable_http", "url": f"https://user:{SECRET}@host/mcp"},
        {"transport": "streamable_http", "url": f"https://{SECRET}@host/mcp"},
        {"transport": "streamable_http", "url": f"https://host/mcp?token={SECRET}"},
        # The two spellings the narrower provider-option rule never
        # matched: a query parameter is named by the vendor whose
        # endpoint it addresses, and `auth` is as ordinary a name for
        # one as `token` is.
        {"transport": "streamable_http", "url": f"https://host/mcp?auth={SECRET}"},
        {"transport": "streamable_http", "url": f"https://host/mcp?authorization={SECRET}"},
    ]
    for fragment in refused:
        with pytest.raises(ConfigError) as caught:
            store.set_mcp_server("weather", fragment)
        message = str(caught.value)
        assert "not allowed" in message
        # The field is a name this repository declared, so the refusal
        # addresses it.
        assert "mcp_servers.weather.url" in message
        assert "headers.Authorization" in message, (
            "the refusal does not say what to do instead"
        )
        # The rule and the field, never the value: what fails this check
        # is a credential.
        assert SECRET not in message
        assert SECRET not in _chain(caught.value)
    assert store.load().domain.mcp_servers == {}


def test_an_ordinary_mcp_url_and_an_entry_with_none_are_accepted(store: ConfigStore) -> None:
    """The two shapes the rule must not touch: a clean address, and a
    stdio entry, which has no url at all and is therefore not a URL
    carrying anything."""
    url = "https://weather.example/mcp?model=small"
    store.set_mcp_server("weather", {"transport": "streamable_http", "url": url})
    store.set_mcp_server("home", {"transport": "stdio", "command": "uvx"})

    assert store.read_mcp_server("weather").entry.url == url
    assert store.read_mcp_server("home").entry.url is None


def test_the_mcp_url_rule_is_write_time_only(store: ConfigStore) -> None:
    """The provider rule's precedent, held against a row rather than
    against a model built in Python.

    A deployment that wrote such a URL before this rule existed has a
    row, and what it needs is a server that starts on it and a way to
    take the credential out. So the row is planted the way that
    deployment left it, and then everything an operator would do with it
    is done: the repository reads it back, the boot composition accepts
    it, the write path replaces it with a clean address, and the delete
    takes it away.

    This is the pin that says the check never migrates to `inside_read`.
    Moved there, the load below would raise and a deployment would meet
    a server that refuses to start over a value it could no longer edit,
    which is the outcome the rule was written write-time-only to avoid.
    """
    written = json.dumps(
        {"transport": "streamable_http", "url": f"https://user:{SECRET}@host/mcp"}
    )
    planted(store, insert(schema.mcp_servers).values(name="weather", body=written))

    snapshot = store.load()

    assert snapshot.domain.mcp_servers["weather"].url.endswith("@host/mcp")
    # And the same row through the composition a server really boots on,
    # which is where a rule moved to the read half would refuse.
    booted = compose_config(FileConfig(), domain_fields(snapshot.domain), "the test's database")
    assert booted.mcp_servers["weather"].url.endswith("@host/mcp")

    # The way out, which is the reason the rule is write-time only.
    store.set_mcp_server(
        "weather", {"transport": "streamable_http", "url": "https://weather.example/mcp"}
    )
    assert store.read_mcp_server("weather").entry.url == "https://weather.example/mcp"

    # And the other way out, which goes by identity and reads nothing.
    planted(store, insert(schema.mcp_servers).values(name="stale", body=written))
    store.delete_mcp_server("stale")
    assert "stale" not in store.load().domain.mcp_servers


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
# The recovery case: a row the loader refuses is the row that is
# keeping the server from starting, so it is the one that has to be
# removable. A delete that read and validated the whole domain first
# could not remove it, because the read failed on the way there.


# A body no model will load, hand-written for the reason every
# adversarial plant here is: what a dump can produce is exactly what the
# reader is not being tested against. This one parses as JSON and fails
# the model, which is the half a plain syntax error would not reach.
UNLOADABLE_BODY = '{"type": ""}'


def _corrupt_provider(store: ConfigStore, name: str) -> None:
    """A row whose body holds something no model will load."""
    planted(
        store,
        update(schema.providers)
        .where(schema.providers.c.name == name)
        .values(body=UNLOADABLE_BODY),
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
    planted(
        store,
        update(schema.mcp_servers).values(body='{"transport": "nonsense"}'),
        update(schema.agents).values(body='{"mcp": "not an array"}'),
        update(schema.devices).values(agents="not an array"),
    )
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
    kept = stored_rows(store, select(schema.providers.c.name, schema.providers.c.body))
    assert [row["name"] for row in kept] == ["claude"]
    assert [row["body"] for row in kept] == [UNLOADABLE_BODY]


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
    unreadable rather than answered with a value nobody wrote.

    NaN has no JSON spelling, so a body holding one is a body somebody
    wrote by hand or a build wrote with a lenient encoder. Pydantic's
    parser reads the literal rather than refusing it, and a provider's
    options are passed through untyped, so without the check the value
    would load and then serialize as null on every read: a configuration
    quietly turned into a different one. Nested as well as top level,
    because an option can be a structure."""
    store.set_provider("llm", "claude", {"type": "anthropic", "temperature": 0.7})
    for written in (
        '{"type": "anthropic", "temperature": NaN}',
        '{"type": "anthropic", "nested": {"deep": [Infinity]}}',
    ):
        planted(store, update(schema.providers).values(body=written))

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


def test_the_exempted_option_is_not_a_credential_slot(store: ConfigStore) -> None:
    """A provider's slots are its secret-shaped option names, so moving
    `max_tokens` out of that predicate withdraws a slot the store used
    to accept (#277). Nothing is lost with it: no read, no build and no
    request ever looked a provider's `max_tokens` up among its stored
    secrets, which is why the slot was never pinned as accepted either.

    The refusal is held to being the one an ordinary non-slot meets,
    differentially rather than by copying its sentence here: what makes
    this right is that `max_tokens` is now an option name like `model`
    and not a case of its own.
    """
    store.set_provider("llm", "claude", {"type": "anthropic", "model": "claude-sonnet-5"})

    with pytest.raises(ConfigError) as withdrawn:
        store.set_secret(SecretLocation.provider("llm", "claude", "max_tokens"), SECRET)
    with pytest.raises(ConfigError) as ordinary:
        store.set_secret(SecretLocation.provider("llm", "claude", "model"), SECRET)

    assert str(withdrawn.value) == str(ordinary.value)
    assert SECRET not in _chain(withdrawn.value)
    assert store.load().secrets.locations() == []

    # And `api_key`, which the sentence names as the example, still
    # fills, so this is not a rule that has stopped admitting anything.
    store.set_secret(CLAUDE, SECRET)
    assert store.load().secrets.locations() == [CLAUDE]


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
    engine = open_database(DatabaseConfig())
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
    engine = open_database(DatabaseConfig())
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
    engine = open_database(DatabaseConfig())
    try:
        store = ConfigStore(engine, keys)
        store.set_provider("llm", "claude", {"type": "anthropic", "model": "claude-sonnet-5"})
        with engine.begin() as connection:
            connection.execute(update(schema.providers).values(body='{"type": ""}'))

        with pytest.raises(ConfigError) as caught:
            store.load()

        assert "providers.llm.claude" in str(caught.value)
        assert caught.value.__cause__ is None
    finally:
        engine.dispose()


# The columns that are still JSON values rather than a dumped model: the
# two secrets columns, the device bindings and the domain settings. Those
# four are what the container-shape guards are still there for, and the
# four reshaped entity tables are deliberately absent, because a body is
# a string handed to pydantic's own parser.
CORRUPTIONS = [
    ("providers", "secrets", "not an object"),
    ("mcp_servers", "secrets", "not an object"),
    ("devices", "agents", "sam"),
    ("domain_settings", "value", {"not": "a string"}),
]


@pytest.mark.parametrize(("table", "column", "written"), CORRUPTIONS)
def test_a_json_column_of_the_wrong_shape_is_a_config_error(
    store: ConfigStore, table: str, column: str, written: object
) -> None:
    """A `json` column enforces no shape beyond being JSON, so a hand-edited or
    half-restored row can hold a string where a mapping belongs. Every
    reader would then raise a TypeError or an AttributeError, which is
    neither a database error nor a validation error, and would travel
    straight through the sanitized boundary as a traceback.

    The devices case is the one that fails silently rather than loudly
    without this: iterating a string succeeds and binds the device to
    one agent per character."""
    _populate(store)
    store.set_agent("poet", {"prompt": "p", "mcp": [], "filler": {"enabled": False}})

    planted(store, update(getattr(schema, table)).values(**{column: written}))

    with pytest.raises(ConfigError) as caught:
        store.load()

    message = str(caught.value)
    assert column in message
    assert str(written) not in message
    assert caught.value.__cause__ is None


# What a body can be wrong in, per kind and per way of being wrong:
# unparseable at all, parseable and missing what the model requires, and
# parseable with a nested value of the wrong shape. Hand-written, all of
# them, because a dump of a valid model is the one thing that cannot
# produce a body the reader has to survive.
BAD_BODIES = [
    ("providers", "not json at all"),
    ("providers", '{"type": ""}'),
    ("providers", '["a", "list", "not", "an", "object"]'),
    ("mcp_servers", '{"transport": "nonsense", "command": "uvx"}'),
    ("mcp_servers", '{"transport": "stdio", "command": "uvx", "args": "not an array"}'),
    ("mcp_servers", '{"transport": "stdio", "command": "uvx", "env": ["not", "an", "object"]}'),
    ("prompt_fragments", '{"text": ""}'),
    ("prompt_fragments", "{"),
    ("agents", '{"mcp": "not an array"}'),
    ("agents", '{"filler": ["not", "an", "object"]}'),
    ("agent_defaults", '{"tts": ""}'),
    ("agent_defaults", '{"mcp": "not an array"}'),
]


@pytest.mark.parametrize(("table", "written"), BAD_BODIES)
def test_a_body_that_will_not_validate_is_a_config_error(
    store: ConfigStore, table: str, written: str
) -> None:
    """A body that will not parse, or will not validate once parsed, is a
    storage failure named the way an unreadable column was: the entity
    and the fact, in a sentence rather than a traceback.

    The body itself is never in it, and that is the difference the shape
    makes. A column that could not be read said which column; a body is
    the whole entity, so a sentence that quoted the row would quote
    everything the operator wrote into it."""
    _populate(store)
    store.set_prompt_fragment("household", {"text": "The bins go out on Tuesday."})

    planted(store, update(getattr(schema, table)).values(body=written))

    with pytest.raises(StorageError) as caught:
        store.load()

    message = str(caught.value)
    assert "cannot be read as configuration" in message
    assert written not in message
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_a_corrupt_json_column_does_not_stop_a_secret_being_cleared(
    store: ConfigStore,
) -> None:
    """The recovery direction: a secrets column that is not an object
    still refuses in words rather than in a traceback."""
    _populate(store)

    planted(store, update(schema.providers).values(secrets="not an object"))

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
    same. With the advisory lock taken in the begin listener that wait
    always times out, because the second writer cannot have read
    anything while the first holds the gate. With the lock taken at the
    first write instead, both read the state before either change, and
    the invariant this asserts is what breaks.
    """
    from vinga_server.config import store as store_module

    setup = open_database(DatabaseConfig())
    try:
        ConfigStore(setup, keys).set_provider("llm", "claude", {"type": "anthropic", "model": "m"})
    finally:
        setup.dispose()

    # White-box for this pacing and the one in the next test, and the
    # race is the reason. What is under test is two writers arriving at
    # the same moment: whether one is refused, whether both land, and
    # what a reader sees in between. A real race is what a test cannot
    # schedule, so the two threads are paced through the one function
    # each of them has to pass, and no public seam sits inside a write.
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
        engine = open_database(DatabaseConfig())
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

    engine = open_database(DatabaseConfig())
    try:
        from vinga_server.config.models import check_references

        assert check_references(ConfigStore(engine, keys).load().domain) == []
    finally:
        engine.dispose()


def test_a_marked_write_resolves_and_persists_under_one_lock(
    tmp_path: Path, keys: MultiFernet, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One writer resubmits a read carrying the mask while the other
    replaces the entry without the value the mask stands for.

    Serially there are two outcomes and no third. The resubmission runs
    first: it keeps the stored value, and the replacement then takes the
    whole entry away. The replacement runs first: the path the mask
    stands for is gone, so the resubmission is refused for a mask with
    nothing behind it. Either way the value is not in the row at the end,
    because the last write to run did not write it.

    The third outcome is the one this pins against: the resubmission
    resolves the mask, the replacement runs whole, and the resubmission
    then persists what it resolved, putting back a value that was gone
    when its own write ran. No serial order produces that, and it is what
    resolving the marker outside the write transaction allowed.

    The pacing makes it deterministic rather than a race that usually
    does not happen: the resubmitting writer announces that it has looked
    the stored value up and then waits for the other to finish.
    Resolving inside the write transaction, that wait always times out,
    because the other writer cannot begin while this one holds the lock
    (the busy timeout is twenty times the wait, so it queues rather than
    failing). Resolving outside it, the other writer runs to completion
    inside the wait, and the resurrection follows.
    """
    from vinga_server.config import store as store_module

    setup = open_database(DatabaseConfig())
    try:
        ConfigStore(setup, keys).set_provider(
            "llm",
            "claude",
            {"type": "anthropic", "connection": {"api_key_env": PASTED}},
        )
    finally:
        setup.dispose()

    replaced = threading.Event()
    # White-box, per the note in the paced test above: two writers
    # meeting inside one write is what this pins, and no public seam
    # sits there.
    held = store_module._held

    def paced(stored, path):
        value = held(stored, path)
        replaced.wait(timeout=0.5)
        return value

    monkeypatch.setattr(store_module, "_held", paced)

    start = threading.Barrier(2)
    outcomes: dict[str, BaseException | None] = {}
    lock = threading.Lock()

    def writer(name: str, change) -> None:
        engine = open_database(DatabaseConfig())
        store = ConfigStore(engine, keys)
        try:
            start.wait(timeout=10)
            try:
                change(store)
                outcome: BaseException | None = None
            except BaseException as exc:  # noqa: BLE001 - reported, not swallowed
                outcome = exc
            with lock:
                outcomes[name] = outcome
        finally:
            if name == "replace":
                replaced.set()
            engine.dispose()

    def resubmit(store: ConfigStore) -> None:
        store.set_provider(
            "llm",
            "claude",
            {"type": "anthropic", "connection": {"api_key_env": MASK}},
        )

    def replace(store: ConfigStore) -> None:
        store.set_provider("llm", "claude", {"type": "anthropic", "model": "m"})

    threads = [
        threading.Thread(target=writer, args=(name, change), name=name)
        for name, change in (("resubmit", resubmit), ("replace", replace))
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert set(outcomes) == {"resubmit", "replace"}
    # A lock this one waited twenty times as long for as the pacing holds
    # it is not a legitimate outcome here, so a refusal can only be the
    # marker's.
    for name, outcome in outcomes.items():
        assert outcome is None or isinstance(outcome, ConfigError), (name, outcome)
    assert outcomes["replace"] is None, outcomes["replace"]
    if outcomes["resubmit"] is not None:
        assert "nothing is stored there" in str(outcomes["resubmit"])

    engine = open_database(DatabaseConfig())
    try:
        entry = ConfigStore(engine, keys).read_provider("llm", "claude").entry
    finally:
        engine.dispose()
    assert "connection" not in (entry.model_extra or {})
    assert PASTED not in str(entry)
