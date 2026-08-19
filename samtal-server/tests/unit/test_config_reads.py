"""Reading one entity: what the repository returns, and how it is shown.

Existence is the repository's decision, so a read of something that is
not there is refused there, once, in the words the CLI has always
printed and with the type the API answers 404 to. What comes back beside
the entity is its stored-secret slots, each marked with the key it
displaces, which is the one fact a masked read exists to convey.

The view over that is one set of builders with two renderings: the
dictionaries here are what the API returns as JSON and what the CLI
renders as YAML, so a mask that held in one and not the other is not a
thing that can happen. Masking fails closed, which is what the pasted
plaintext cases pin.
"""

from pathlib import Path

import pytest
from cryptography.fernet import Fernet, MultiFernet
from sqlalchemy import update

from samtal_server.config import views
from samtal_server.config.entities import (
    NO_SUCH_AGENT,
    NO_SUCH_DEVICE,
    NO_SUCH_FRAGMENT,
    NO_SUCH_MCP_SERVER,
    NO_SUCH_PROVIDER,
)
from samtal_server.config.loader import ConfigError, UnknownEntityError
from samtal_server.config.models import ProviderConfig
from samtal_server.config.secrets import MASK, SecretLocation, generate_key
from samtal_server.config.store import ConfigStore
from samtal_server.db import open_database, schema

# Not a real credential, and shaped so a substring check for one cannot
# match by accident.
SECRET = "sk-test-4f8b2c9e-never-a-real-credential"
OTHER_SECRET = "tok-test-7a1d3f60-never-a-real-credential"

# A credential shaped like a variable name: it gets past the models'
# paste check (which only asks that a reference look like a name) and is
# what the display path's own rule has to catch.
PASTED = "sk_test_4f8b2c9e_never_a_real_credential"


@pytest.fixture
def store(tmp_path: Path):
    engine = open_database(tmp_path / "db")
    try:
        yield ConfigStore(engine, MultiFernet([Fernet(generate_key())]))
    finally:
        engine.dispose()


def _populate(store: ConfigStore) -> None:
    store.set_provider(
        "llm",
        "claude",
        {"type": "anthropic", "model": "m", "api_key_env": "ANTHROPIC_API_KEY"},
    )
    store.set_provider("tts", "voice", {"type": "piper", "model": "es"})
    store.set_mcp_server(
        "weather",
        {
            "transport": "streamable_http",
            "url": "https://example.invalid/mcp",
            "headers": {"Authorization": "$WEATHER_TOKEN", "X-Region": "eu"},
        },
    )
    store.set_agent("sam", {"prompt": "You are Sam.", "tts": "voice"})
    store.bind_device("AA-BB-CC-DD-EE-FF", ["sam"])
    store.set_default_agent("sam")


# The repository's reads


def test_every_kind_reads_back_what_was_written(store: ConfigStore) -> None:
    _populate(store)

    assert store.read_provider("llm", "claude").entry.type == "anthropic"
    assert store.read_mcp_server("weather").entry.transport == "streamable_http"
    assert store.read_agent("sam").entry.prompt == "You are Sam."
    assert store.read_agent_defaults().entry.llm is None
    # The canonical form of the MAC, whichever spelling asked for it.
    assert store.read_device("aa:bb:cc:dd:ee:ff").entry == ["sam"]
    assert store.read_default_agent() == "sam"


def test_reading_something_that_is_not_there_does_not_name_it(store: ConfigStore) -> None:
    """The 404 set, in one fixed sentence per section: one vocabulary
    whichever way an operator reached the read, and never the identity
    they asked for, which is a value nothing here has validated (#132).

    Equality rather than a substring, so a sentence that grew the
    identity back on the end would fail here."""
    cases = [
        (lambda: store.read_provider("llm", "ghost"), NO_SUCH_PROVIDER),
        (lambda: store.read_mcp_server("ghost"), NO_SUCH_MCP_SERVER),
        (lambda: store.read_prompt_fragment("ghost"), NO_SUCH_FRAGMENT),
        (lambda: store.read_agent("ghost"), NO_SUCH_AGENT),
        (lambda: store.read_device("aa:bb:cc:dd:ee:ff"), NO_SUCH_DEVICE),
    ]
    for call, message in cases:
        with pytest.raises(UnknownEntityError) as caught:
            call()
        assert str(caught.value) == message


def test_a_read_that_addresses_nothing_addressable_stays_plain(store: ConfigStore) -> None:
    """A stage that is not a stage and a MAC that is not a MAC are the
    caller's mistake rather than a missing entity, so they keep the type
    the API answers 422 to."""
    for call in (
        lambda: store.read_provider("nonsense", "claude"),
        lambda: store.read_device("not-a-mac"),
    ):
        with pytest.raises(ConfigError) as caught:
            call()
        assert type(caught.value) is ConfigError, caught.value


def test_the_singleton_and_the_default_agent_are_never_missing(store: ConfigStore) -> None:
    """An unwritten agent_defaults is the empty entry and an unset
    default agent is None: both are configurations, not absences."""
    assert store.read_agent_defaults().entry.model_dump(exclude_none=True) == {}
    assert store.read_default_agent() is None


def test_a_read_names_its_stored_slots_and_what_they_shadow(store: ConfigStore) -> None:
    _populate(store)
    store.set_secret(SecretLocation.provider("llm", "claude", "api_key"), SECRET)
    store.set_secret(SecretLocation.mcp_server("weather", "headers.Authorization"), OTHER_SECRET)

    provider = store.read_provider("llm", "claude")
    mcp = store.read_mcp_server("weather")

    assert [(item.location.slot, item.shadows) for item in provider.secrets] == [
        ("api_key", "api_key_env")
    ]
    assert [(item.location.slot, item.shadows) for item in mcp.secrets] == [
        ("headers.Authorization", "headers.Authorization")
    ]


def test_a_stored_slot_the_entity_writes_no_reference_for_shadows_nothing(
    store: ConfigStore,
) -> None:
    store.set_provider("llm", "claude", {"type": "anthropic", "model": "m"})
    store.set_mcp_server("home", {"transport": "stdio", "command": "uvx"})
    store.set_secret(SecretLocation.provider("llm", "claude", "api_key"), SECRET)
    store.set_secret(SecretLocation.mcp_server("home", "env.HOME_TOKEN"), OTHER_SECRET)

    assert store.read_provider("llm", "claude").secrets[0].shadows is None
    assert store.read_mcp_server("home").secrets[0].shadows is None


def test_a_kind_that_holds_no_secret_reads_with_none(store: ConfigStore) -> None:
    _populate(store)

    assert store.read_agent("sam").secrets == ()
    assert store.read_agent_defaults().secrets == ()
    assert store.read_device("aa:bb:cc:dd:ee:ff").secrets == ()


# The view over them


def test_an_entity_is_shown_as_an_envelope(store: ConfigStore) -> None:
    _populate(store)
    store.set_secret(SecretLocation.provider("llm", "claude", "api_key"), SECRET)

    envelope = views.provider(store.read_provider("llm", "claude"))

    assert envelope == {
        "entity": {"type": "anthropic", "api_key_env": "ANTHROPIC_API_KEY", "model": "m"},
        "secrets": {"api_key": {"shadows": "api_key_env"}},
    }


def test_a_recorded_provider_keeps_what_it_ran_on(store: ConfigStore) -> None:
    """A record is not a display: the exact model string is the only
    handle on a hosted model that changed underneath, which is why a
    manifest keeps the entries at all."""
    _populate(store)

    assert views.provider_record(store.read_provider("llm", "claude").entry) == {
        "type": "anthropic",
        "api_key_env": "ANTHROPIC_API_KEY",
        "model": "m",
    }


def test_a_recorded_provider_carries_no_credential(store: ConfigStore) -> None:
    """The other half of the write-time URL rule, and the half that does
    not depend on every row having passed through it: a capture manifest
    and a conversation's session row outlive the session, so what is
    built for them strips a credential a URL carries whatever the row
    holds. Written straight to the database here, which is what a row
    that predates the rule looks like."""
    _populate(store)
    with store._engine.begin() as connection:  # noqa: SLF001 - a row from before the rule
        connection.execute(
            update(schema.providers)
            .where(schema.providers.c.name == "claude")
            .values(
                options={
                    "base_url": f"https://user:{SECRET}@host/v1?api_key={OTHER_SECRET}",
                    "connection": {"endpoint": f"https://{SECRET}@host"},
                }
            )
        )

    recorded = views.provider_record(store.read_provider("llm", "claude").entry)

    assert recorded["base_url"] == "https://host/v1"
    assert recorded["connection"] == {"endpoint": "https://host"}
    rendered = repr(recorded)
    assert SECRET not in rendered
    assert OTHER_SECRET not in rendered


def test_a_recorded_secret_shaped_option_fails_closed() -> None:
    """The same fail-closed rule the display path has, and unreachable
    for the same reason: the models refuse such a key on every path that
    validates. Built here without validation, which is what "a value
    that got in another way" means concretely."""
    entry = ProviderConfig.model_construct(type="mock", api_key_env=None, egress=None)
    object.__setattr__(entry, "__pydantic_extra__", {"session_token": PASTED})

    assert views.provider_record(entry)["session_token"] == MASK


def test_a_kind_that_holds_no_secret_is_shown_with_an_empty_mapping(
    store: ConfigStore,
) -> None:
    """One shape for every kind, so a client renders one thing."""
    _populate(store)

    for envelope in (
        views.agent(store.read_agent("sam")),
        views.agent_defaults(store.read_agent_defaults()),
        views.device(store.read_device("aa:bb:cc:dd:ee:ff")),
    ):
        assert set(envelope) == {"entity", "secrets"}
        assert envelope["secrets"] == {}

    assert views.device(store.read_device("aa:bb:cc:dd:ee:ff"))["entity"] == {"agents": ["sam"]}


def test_an_entrys_guidance_is_shown_write_shaped_and_unmasked(store: ConfigStore) -> None:
    """It is prompt text the operator wrote, not a credential slot, so a
    read shows it as written and a write of the body takes it back."""
    written = "  Ask before unlocking the door.\n\n    The lights are safe.\n"
    store.set_mcp_server(
        "home", {"transport": "stdio", "command": "uvx", "instructions": written}
    )

    body = views.mcp_server(store.read_mcp_server("home"))["entity"]

    assert body["instructions"] == written
    store.set_mcp_server("home", body)
    assert store.read_mcp_server("home").entry.instructions == written


def test_an_entry_with_no_guidance_shows_no_key(store: ConfigStore) -> None:
    _populate(store)

    assert "instructions" not in views.mcp_server(store.read_mcp_server("weather"))["entity"]


def test_the_server_guidance_opt_ins_are_shown_write_shaped(store: ConfigStore) -> None:
    """The trust decision is what a read answers about, so the flag is
    shown in either state; the prompt names are the operator's own
    configuration, echoed here as they were written, which is one of the
    two places they may appear at all."""
    store.set_mcp_server(
        "home",
        {
            "transport": "stdio",
            "command": "uvx",
            "use_server_instructions": True,
            "inject_prompts": ["house_style"],
        },
    )

    body = views.mcp_server(store.read_mcp_server("home"))["entity"]

    assert body["use_server_instructions"] is True
    assert body["inject_prompts"] == ["house_style"]
    store.set_mcp_server("home", body)
    entry = store.read_mcp_server("home").entry
    assert (entry.use_server_instructions, entry.inject_prompts) == (True, ["house_style"])


def test_an_entry_naming_no_prompts_shows_no_list(store: ConfigStore) -> None:
    _populate(store)

    body = views.mcp_server(store.read_mcp_server("weather"))["entity"]

    assert body["use_server_instructions"] is False
    assert "inject_prompts" not in body


def test_a_fragment_is_shown_as_the_mapping_a_write_takes_back(
    store: ConfigStore,
) -> None:
    """The exact read representation, pinned: an envelope whose entity is
    the one-key mapping a PUT of it carries, with the text byte for
    byte."""
    written = "  The bins go out on Tuesday.\n\n    The radio is called Bosse.\n"
    store.set_prompt_fragment("household", {"text": written})

    envelope = views.prompt_fragment(store.read_prompt_fragment("household"))

    assert envelope == {"entity": {"text": written}, "secrets": {}}
    store.set_prompt_fragment("household", envelope["entity"])
    assert store.read_prompt_fragment("household").entry.text == written


def test_every_fragment_is_listed_and_shown_in_the_whole_configuration(
    store: ConfigStore,
) -> None:
    _populate(store)
    store.set_prompt_fragment("household", {"text": "The bins go out on Tuesday."})

    snapshot = store.load()

    assert views.prompt_fragments(snapshot) == {
        "household": {"entity": {"text": "The bins go out on Tuesday."}, "secrets": {}}
    }
    document = views.config(snapshot)["config"]
    assert document["prompt_fragments"] == {
        "household": {"text": "The bins go out on Tuesday."}
    }


def test_an_include_list_is_echoed_write_shaped_on_both_layers(
    store: ConfigStore,
) -> None:
    """An unset list is absent rather than null, since unset is inherit
    and an empty list is the opposite."""
    _populate(store)
    store.set_prompt_fragment("household", {"text": "The bins go out on Tuesday."})
    store.set_agent_defaults({"prompt_includes": ["household"]})
    store.set_agent("poet", {"prompt": "P", "prompt_includes": []})

    defaults = views.agent_defaults(store.read_agent_defaults())["entity"]
    poet = views.agent(store.read_agent("poet"))["entity"]
    sam = views.agent(store.read_agent("sam"))["entity"]

    assert defaults["prompt_includes"] == ["household"]
    assert poet["prompt_includes"] == []
    assert "prompt_includes" not in sam


def test_a_reference_that_is_not_one_comes_back_masked(store: ConfigStore) -> None:
    """Fail-closed masking, on the path an operator reaches for when
    something is wrong. The models keep an obvious paste out, but a
    credential shaped like a name gets past that check, so the display
    passes only a canonical environment reference through and masks
    everything else: the read that would find the mistake must not be
    the one that prints it."""
    _populate(store)
    with store._engine.begin() as connection:
        connection.execute(
            update(schema.providers)
            .where(schema.providers.c.name == "claude")
            .values(api_key_env=PASTED)
        )

    body = views.provider(store.read_provider("llm", "claude"))["entity"]

    assert body["api_key_env"] == MASK
    assert PASTED not in str(body)


def test_the_whole_configuration_is_one_masked_document(store: ConfigStore) -> None:
    _populate(store)
    store.set_secret(SecretLocation.provider("llm", "claude", "api_key"), SECRET)
    store.set_secret(SecretLocation.mcp_server("weather", "headers.Authorization"), OTHER_SECRET)

    document = views.config(store.load())

    assert set(document) == {"config", "secrets"}
    assert document["config"]["agents"]["sam"] == {"prompt": "You are Sam.", "tts": "voice"}
    assert document["config"]["devices"] == {"aa:bb:cc:dd:ee:ff": ["sam"]}
    assert document["config"]["default_agent"] == "sam"
    # A header that carries no secret keeps its literal value; masking
    # it would hide configuration for nothing.
    assert document["config"]["mcp_servers"]["weather"]["headers"] == {
        "Authorization": "$WEATHER_TOKEN",
        "X-Region": "eu",
    }
    assert document["secrets"] == [
        {
            "kind": "mcp_server",
            "identity": "weather",
            "slot": "headers.Authorization",
            "shadows": "headers.Authorization",
        },
        {
            "kind": "provider",
            "identity": "llm.claude",
            "slot": "api_key",
            "shadows": "api_key_env",
        },
    ]
    rendered = str(document)
    assert SECRET not in rendered and OTHER_SECRET not in rendered


def test_a_listing_is_keyed_by_identity(store: ConfigStore) -> None:
    """The names a caller needs come with the entities by construction,
    which is why a listing is a mapping rather than an array."""
    _populate(store)
    store.set_secret(SecretLocation.provider("llm", "claude", "api_key"), SECRET)
    snapshot = store.load()

    assert set(views.providers(snapshot)) == {"llm", "asr", "tts", "vad"}
    assert views.providers(snapshot)["llm"]["claude"]["secrets"] == {
        "api_key": {"shadows": "api_key_env"}
    }
    assert views.providers(snapshot)["asr"] == {}
    assert set(views.mcp_servers(snapshot)) == {"weather"}
    assert views.agents(snapshot)["sam"]["entity"]["prompt"] == "You are Sam."
    assert views.devices(snapshot)["aa:bb:cc:dd:ee:ff"]["entity"] == {"agents": ["sam"]}
    assert views.default_agent(snapshot.domain.default_agent) == {"name": "sam"}


def test_a_listing_and_a_single_read_agree_about_a_shadow(store: ConfigStore) -> None:
    """One rule, in the repository: a slot cannot be said to shadow one
    key in a listing and another in a single read."""
    _populate(store)
    store.set_secret(SecretLocation.provider("llm", "claude", "api_key"), SECRET)

    listed = views.providers(store.load())["llm"]["claude"]
    read = views.provider(store.read_provider("llm", "claude"))

    assert listed == read
