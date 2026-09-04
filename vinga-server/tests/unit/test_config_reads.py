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

from tests.support.stores import body as dumped
from tests.support.stores import planted
from vinga_server.config import entities, views
from vinga_server.config.loader import ConfigError, UnknownEntityError
from vinga_server.config.models import (
    AgentConfig,
    DatabaseConfig,
    FillerConfig,
    McpServerConfig,
    ProviderConfig,
    mcp_entry_fragment,
)
from vinga_server.config.secrets import MASK, SecretLocation, generate_key
from vinga_server.config.store import ConfigStore
from vinga_server.db import open_database, schema

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
    engine = open_database(DatabaseConfig())
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
    """The 404 set: the type the API answers 404 to, the section the
    refusal is about, and never the identity that was asked for, which
    is a value nothing here has validated (#132).

    What is asserted is the section and the absence, not the sentence.
    The section is the semantic half an operator needs, since it is what
    tells them which listing to look in; the wording around it is the
    repository's to choose."""
    absent = "ghost"
    absent_mac = "aa:bb:cc:dd:ee:ff"
    cases = [
        (lambda: store.read_provider("llm", absent), "providers", absent),
        (lambda: store.read_mcp_server(absent), "mcp_servers", absent),
        (lambda: store.read_prompt_fragment(absent), "prompt_fragments", absent),
        (lambda: store.read_agent(absent), "agents", absent),
        (lambda: store.read_device(absent_mac), "devices", absent_mac),
    ]
    for call, section, identity in cases:
        with pytest.raises(UnknownEntityError) as caught:
            call()
        refusal = str(caught.value)
        assert refusal.startswith(f"{section}:"), refusal
        assert identity not in refusal, refusal


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
    planted(
        store,
        update(schema.providers)
        .where(schema.providers.c.name == "claude")
        .values(
            body=dumped(
                ProviderConfig(
                    type="anthropic",
                    base_url=(
                        f"https://user:{SECRET}@host/v1?api_key={OTHER_SECRET}"
                        f"&auth={OTHER_SECRET}"
                    ),
                    connection={"endpoint": f"https://{SECRET}@host"},
                )
            )
        ),
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
    planted(
        store,
        update(schema.providers)
        .where(schema.providers.c.name == "claude")
        .values(body=dumped(ProviderConfig(type="anthropic", api_key_env=PASTED))),
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


# The display fails open, the record fails closed
#
# What each of these builds an entry out of is the point: a display is
# derived from the model, so a field added to one appears on every read
# with nothing in `views.py` to edit, and a record is built key by key,
# so the same field appears in no record until somebody decides it
# belongs. The models below are what the next change to any of these
# models looks like, declared here rather than in `models.py` so that
# what is pinned is that nothing outside the model had to hear about it.


class McpServerWithNote(McpServerConfig):
    """An MCP server that gained two fields: an ordinary one, and one
    whose name is credential-shaped."""

    note: str | None = None
    access_token: str | None = None


class ProviderWithNote(ProviderConfig):
    """A provider that gained one. Declared rather than passed through,
    so it is not among `options` and a record cannot pick it up by the
    pass-through route either."""

    note: str | None = None


class AgentWithSecret(AgentConfig):
    """An agent that gained a credential-shaped field. Its kind holds no
    stored secret today, which is exactly why the masking rule has to
    apply to it: the field arrives before anybody revisits the rule."""

    session_token: str | None = None


def test_a_field_a_model_gains_is_shown_without_the_view_hearing_about_it() -> None:
    """The fail-open half (#176). `views.py` names none of these fields
    and shows all of them, which is what the descriptor work measured
    the absence of: a scratch field reached the store, both APIs, the
    CLI and both generated references untouched, and was invisible on
    every read with no test failing for it."""
    entry = McpServerWithNote(
        transport="stdio", command="uvx", note="what it is for", access_token=PASTED
    )

    body = views.entity_body(entities.descriptor("mcp-server"), entry)

    assert body["note"] == "what it is for"
    # Shown, and masked: a new field is displayed by default and
    # displaced by default when its name says it holds a credential.
    assert body["access_token"] == MASK
    assert PASTED not in str(body)


def test_a_field_a_model_gains_is_masked_on_a_kind_that_holds_no_secret() -> None:
    """The wider reading is the default for a kind that has not thought
    about the question, so an agent that gains a credential-shaped field
    does not have to wait for somebody to notice."""
    entry = AgentWithSecret(prompt="You are Sam.", session_token=PASTED)

    body = views.entity_body(entities.descriptor("agent"), entry)

    assert body["session_token"] == MASK
    assert body["prompt"] == "You are Sam."


def test_a_field_a_model_gains_stays_out_of_a_record() -> None:
    """The fail-closed half, and the reason the two are one decision: a
    read is thrown away as soon as it is read, and a record is written
    into a manifest and a session row that outlive the conversation."""
    entry = ProviderWithNote(type="anthropic", model="m", note="what it is for")

    assert views.entity_body(entities.descriptor("provider"), entry)["note"] == (
        "what it is for"
    )
    assert "note" not in views.provider_record(entry)
    # And the field it was always going to keep, so this is not passing
    # by building nothing at all.
    assert views.provider_record(entry)["model"] == "m"


def test_a_credential_nested_in_an_mcp_entry_is_masked_at_every_depth() -> None:
    """The masking gap this closes (#171): a provider's options were
    masked at every depth while an MCP server's env and headers were
    masked one level down, so a credential under a key inside one was
    displayed as written. Built without validation, which is what "a
    value that got in another way" means concretely: the model types
    both mappings as flat, and this is the walk that does not rely on
    it."""
    entry = McpServerConfig.model_construct(
        transport="stdio",
        command="uvx",
        env={
            "HOME_TOKEN": PASTED,
            "SETTINGS": {"api_key": PASTED, "host": "example"},
            "PROFILES": [{"authorization": PASTED, "name": "default"}],
        },
    )

    body = views.entity_body(entities.descriptor("mcp-server"), entry)

    assert body["env"]["HOME_TOKEN"] == MASK
    assert body["env"]["SETTINGS"] == {"api_key": MASK, "host": "example"}
    assert body["env"]["PROFILES"] == [{"authorization": MASK, "name": "default"}]
    assert PASTED not in str(body)


def test_a_credential_nested_in_a_provider_option_is_masked_on_every_read(
    store: ConfigStore,
) -> None:
    """The same depth, on the path a row can really reach: an option is
    passed through to the implementation, so it can be a structure, and
    a reference key one level down accepts anything shaped like a
    variable name. Every display form masks it, and the document is one
    of them."""
    _populate(store)
    store.set_provider(
        "llm",
        "claude",
        {"type": "anthropic", "connection": {"api_key_env": PASTED, "host": "example"}},
    )

    entity = views.provider(store.read_provider("llm", "claude"))["entity"]
    document = views.config(store.load())

    assert entity["connection"] == {"api_key_env": MASK, "host": "example"}
    assert document["config"]["providers"]["llm"]["claude"]["connection"] == {
        "api_key_env": MASK,
        "host": "example",
    }
    assert PASTED not in str(document)


def test_a_grant_is_shown_in_the_form_the_row_holds(store: ConfigStore) -> None:
    """A read is a fragment a write of it accepts back, and an `mcp`
    entry has two spellings: a plain name is the whole server and an
    object is part of one. Pinned against `mcp_entry_fragment`, which is
    what a row is written with, so the displayed form and the stored
    form cannot come to disagree."""
    _populate(store)
    store.set_agent("poet", {"prompt": "P", "mcp": [{"server": "weather", "tools": ["forecast"]}]})
    store.set_agent_defaults({"mcp": ["weather"]})

    poet = views.agent(store.read_agent("poet"))["entity"]
    defaults = views.agent_defaults(store.read_agent_defaults())["entity"]

    assert poet["mcp"] == [{"server": "weather", "tools": ["forecast"]}]
    assert defaults["mcp"] == ["weather"]
    for entry, shown in (
        (store.read_agent("poet").entry, poet),
        (store.read_agent_defaults().entry, defaults),
    ):
        assert shown["mcp"] == [mcp_entry_fragment(item) for item in entry.mcp]


def test_each_display_departure_names_a_field_its_model_declares() -> None:
    """The two rules `views` states in place, held to the models they
    are about.

    Inherited from `test_every_display_fact_names_a_field_the_shape_
    declares`, which held the same two facts while they were fields on
    the descriptor registry (#242 moved each to its one consumer as a
    literal). The check is worth the same two lines wherever they live,
    because neither failure is loud where it happens: `_order` emits
    `"prompt"` unconditionally for an agent, so a renamed field is a
    KeyError raised out of a read path rather than a refusal, and a
    renamed `phrases` would silently stop being shown at its default.
    """
    assert "prompt" in AgentConfig.model_fields
    assert "phrases" in FillerConfig.model_fields


def test_a_section_nested_in_an_agent_is_shown_whole(store: ConfigStore) -> None:
    """A default that is a real value is shown at it, so a filler
    section reads as the three-part policy it is rather than as the one
    part that was written. The phrase list is shown even when it is
    empty, which is the one departure `views` declares and the state a
    disabled section is in."""
    _populate(store)
    store.set_agent("poet", {"prompt": "P", "filler": {"enabled": False}})

    body = views.agent(store.read_agent("poet"))["entity"]

    assert body["filler"] == FillerConfig().model_dump()
    assert body["filler"]["phrases"] == []


def test_every_shown_body_is_a_fragment_a_write_accepts_back(store: ConfigStore) -> None:
    """What the absence rule is for. A field holding a default that
    means absence is left out rather than shown as null, because an MCP
    server is refused for naming a field of the other transport at all,
    so a stdio entry showing `url: null` could not be written back.
    Every kind, written back and read again, byte for byte."""
    _populate(store)
    store.set_mcp_server("home", {"transport": "stdio", "command": "uvx", "args": ["run"]})
    store.set_prompt_fragment("household", {"text": "The bins go out on Tuesday."})
    store.set_agent_defaults({"llm": "claude", "filler": {"enabled": False}})

    round_trips = [
        (
            "provider",
            lambda: store.read_provider("llm", "claude"),
            lambda body: store.set_provider("llm", "claude", body),
        ),
        # Both transports, because what each of them may not name is the
        # other's fields.
        (
            "mcp-server",
            lambda: store.read_mcp_server("home"),
            lambda body: store.set_mcp_server("home", body),
        ),
        (
            "mcp-server",
            lambda: store.read_mcp_server("weather"),
            lambda body: store.set_mcp_server("weather", body),
        ),
        (
            "prompt-fragment",
            lambda: store.read_prompt_fragment("household"),
            lambda body: store.set_prompt_fragment("household", body),
        ),
        ("agent", lambda: store.read_agent("sam"), lambda body: store.set_agent("sam", body)),
        (
            "agent-defaults",
            store.read_agent_defaults,
            lambda body: store.set_agent_defaults(body),
        ),
    ]
    for kind, read, write in round_trips:
        before = views.entity(kind, read())["entity"]
        write(before)
        assert views.entity(kind, read())["entity"] == before, kind


def test_a_listing_and_a_single_read_agree_about_a_shadow(store: ConfigStore) -> None:
    """One rule, in the repository: a slot cannot be said to shadow one
    key in a listing and another in a single read."""
    _populate(store)
    store.set_secret(SecretLocation.provider("llm", "claude", "api_key"), SECRET)

    listed = views.providers(store.load())["llm"]["claude"]
    read = views.provider(store.read_provider("llm", "claude"))

    assert listed == read
