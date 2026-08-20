"""What a stored configuration would change about the MCP world that is
running.

The registry is the real one, and the read under test is the one an
operator's "what have I written that is not in effect" goes through. Its
baseline is the generation that is installed rather than the boot, which
is the whole reason it exists: `POST /runtime/config/reload` swaps
that world while the process runs, and an answer taken against the boot
would report changes a reload has already applied.

Most of these worlds are never started. An entry no agent references has
no manager at all, and it is exactly the entry a comparison built from
the managers would miss, so it is what most of the cases are about; the
reloads here keep every connection they are given, so nothing is
stopped or started either. The one case that stands a real server up is
the one whose claim is about a connection.
"""

import pytest
from cryptography.fernet import Fernet, MultiFernet

from tests.support.configs import config_with as domain_config
from tests.support.tools_mcp import Applying, entry_data, reading, started
from tests.support.tools_mcp import reload_config as config_with
from vinga_server.config import Config
from vinga_server.config.boot import BootConfig
from vinga_server.config.loader import ConfigError
from vinga_server.config.secrets import (
    SecretLocation,
    SecretStore,
    encrypt,
    generate_key,
)
from vinga_server.tools.mcp import CONNECTED, McpServers

SECRET = "sk-test-0b93af6d-never-a-real-credential"

STAGES = dict.fromkeys(("llm", "asr", "tts", "vad"), "mock")


def unused(**overrides: object) -> Config:
    """One configured entry that no agent references, so this world
    builds no manager for it and holds nothing about it but its
    comparison identity."""
    return config_with({"tools": entry_data(**overrides)}, {"assistant": []})


def unused_http(**overrides: object) -> Config:
    """The same, on the transport whose mappings are `headers` rather
    than `env`. Nothing connects to it, so the URL names a port nothing
    is listening on."""
    return config_with(
        {"tools": {"transport": "streamable_http", "url": "http://127.0.0.1:1/mcp"} | overrides},
        {"assistant": []},
    )


def granting(defaults: list[object] | None, own: list[object] | None) -> Config:
    """One entry, granted at whichever layer the case is about, so that
    a grant can be moved between the two without changing what the agent
    effectively reaches."""
    return domain_config(
        mcp_servers={"tools": entry_data()},
        agent_defaults=STAGES | ({} if defaults is None else {"mcp": defaults}),
        agents={"assistant": {"prompt": "A"} | ({} if own is None else {"mcp": own})},
    )


# The entries, referenced or not


def test_an_unused_entry_s_connection_edit_is_reported_as_changed() -> None:
    """The case the design turns on. No manager exists for this entry,
    so there is nothing to compare it with beyond what the generation
    retained about it, and an operator who edits it still has something
    a reload would apply."""
    servers = McpServers.build(unused())

    pending = servers.pending_against(unused(tool_timeout_s=3.5))

    assert pending.changed == ("tools",)
    assert (pending.added, pending.removed, pending.grants) == ((), (), ())


def test_an_unused_entry_s_rotated_secret_is_reported_as_changed() -> None:
    """The fragment is byte-identical either side, so the stored
    credential is the whole of what the comparison has to see: a
    rotation is a reload away from applying, on an entry nothing is
    connected to."""
    keys = MultiFernet([Fernet(generate_key())])
    location = SecretLocation.mcp_server("tools", "env.API_TOKEN")
    config = unused()
    booted = SecretStore({location: encrypt(location, SECRET, keys)}, keys)
    rotated = SecretStore({location: encrypt(location, "a-new-value", keys)}, keys)
    servers = McpServers.build(config, booted)

    pending = servers.pending_against(config, rotated)

    assert pending.changed == ("tools",)
    # And nothing of the secret came back with the answer.
    assert SECRET not in repr(pending)


def test_a_written_entry_is_added_and_a_deleted_one_is_removed() -> None:
    servers = McpServers.build(config_with({"tools": entry_data()}, {"assistant": []}))

    pending = servers.pending_against(
        config_with({"weather": entry_data()}, {"assistant": []})
    )

    assert pending.added == ("weather",)
    assert pending.removed == ("tools",)
    assert pending.changed == ()


def test_an_entry_written_and_written_back_is_not_pending() -> None:
    """Changed means the stored state differs from what is running, not
    that something was written: an edit changed back before anyone
    looked leaves nothing to apply."""
    servers = McpServers.build(unused())

    assert servers.pending_against(unused()) == servers.pending_against(unused())
    assert servers.pending_against(unused()).changed == ()


def test_an_entry_holding_an_unpaired_surrogate_is_still_comparable() -> None:
    """A model field takes whatever a `str` can hold, and an unpaired
    surrogate is one of the things it can: it passes validation and it
    has no UTF-8 encoding at all.

    An identity taken by asking pydantic for JSON text raises on one,
    and it raises at the boot that takes an identity per configured
    entry, on a failure the startup path does not classify, so what an
    operator would meet is a library traceback rather than a refusal
    naming the entry.
    """
    lone = unused(command="/usr/bin/mcp-\ud800")

    servers = McpServers.build(lone)

    assert servers.pending_against(lone).changed == ()
    assert servers.pending_against(unused()).changed == ("tools",)


def test_an_entry_whose_env_was_written_in_another_order_is_unchanged() -> None:
    """The pairs are what an `env` means and their order is not: two
    entries holding the same pairs are equal as models, which is what
    the reload's own comparison already says of them. An identity that
    moved with insertion order would report a change nobody made, and
    nothing fixes that order: it is whatever a stored document was
    written in and whatever a decoder handed back.
    """
    servers = McpServers.build(unused(env={"FIRST": "1", "SECOND": "2"}))

    assert servers.pending_against(unused(env={"SECOND": "2", "FIRST": "1"})).changed == ()
    # And a pair that really moved is still reported, so the case above
    # cannot pass by comparing nothing.
    assert servers.pending_against(unused(env={"FIRST": "1", "SECOND": "3"})).changed == (
        "tools",
    )


def test_an_entry_whose_headers_were_written_in_another_order_is_unchanged() -> None:
    """The same rule on the other transport, where the mapping an
    operator writes most of is `headers` rather than `env`."""
    servers = McpServers.build(unused_http(headers={"X-One": "1", "X-Two": "2"}))

    pending = servers.pending_against(unused_http(headers={"X-Two": "2", "X-One": "1"}))

    assert pending.changed == ()


@pytest.mark.parametrize(
    "edit",
    [
        pytest.param({"instructions": "Read this first."}, id="instructions"),
        pytest.param({"use_server_instructions": True}, id="use_server_instructions"),
    ],
)
async def test_a_prompt_only_edit_is_pending_and_the_connection_still_stands(
    edit: dict[str, object],
) -> None:
    """The two answers differ on purpose, and for both of the fields
    that are prompt-only. Each is text the connection never sees, so the
    reload reports the entry as unchanged and keeps the live connection;
    the entry is still not what is running, so it is pending until that
    reload happens.
    """
    before = config_with({"tools": entry_data()}, {"assistant": ["tools"]})
    after = config_with({"tools": entry_data(**edit)}, {"assistant": ["tools"]})
    servers = await started(before)
    try:
        kept = servers.manager_of("tools")
        assert servers.status()["tools"]["state"] == CONNECTED

        assert servers.pending_against(after).changed == ("tools",)

        applied = (await Applying(servers, before).apply(reading(after))).mcp

        # Nothing about the connection moved, and the entry is not
        # pending any more.
        assert applied.unchanged == ["tools"]
        assert servers.manager_of("tools") is kept
        assert servers.status()["tools"]["state"] == CONNECTED
        assert servers.pending_against(after).changed == ()
    finally:
        await servers.stop_all()


async def test_an_inject_prompts_edit_is_pending_and_the_reload_reconnects() -> None:
    """The prompt field that is not a prompt-only field. Editing it
    changes what a connect fetches from the server, so applying it means
    fetching again, and the reload says so by restarting the entry.

    Pending answers the same for it as for the other two, which is what
    keeps this read about the entry rather than about the connection:
    what is stored is not what is running, whatever applying it will
    cost.
    """
    before = config_with({"tools": entry_data()}, {"assistant": ["tools"]})
    after = config_with(
        {"tools": entry_data(inject_prompts=["house_style"])}, {"assistant": ["tools"]}
    )
    servers = await started(before)
    try:
        was = servers.manager_of("tools")

        assert servers.pending_against(after).changed == ("tools",)

        applied = (await Applying(servers, before).apply(reading(after))).mcp

        assert applied.restarted == ["tools"]
        assert servers.manager_of("tools") is not was
        assert servers.pending_against(after).changed == ()
    finally:
        await servers.stop_all()


async def test_the_identities_are_swapped_with_the_world_they_describe() -> None:
    """A reload's baseline moves with it: what was pending a moment ago
    is what is running now, and the same read taken against the same
    stored world answers with nothing."""
    running = unused()
    servers = McpServers.build(running)
    after = unused(tool_timeout_s=3.5)
    assert servers.pending_against(after).changed == ("tools",)

    await Applying(servers, running).apply(reading(after))

    assert servers.pending_against(after) == servers.pending_against(after)
    assert servers.pending_against(after).changed == ()
    # And the world that was running is now the pending one.
    assert servers.pending_against(unused()).changed == ("tools",)


async def test_the_mark_moves_only_when_a_world_is_installed() -> None:
    """What the comparison read captures across its own await. A refused
    apply installs nothing, so it moves nothing; an apply that installs
    reads as unstable while it is doing it and settles one further on.

    The mark is the generation holder's rather than the registry's, and
    that is the whole point of moving it there: an apply changes serving
    state more than once, and a mark that counted only this half would
    be steady over a window in which the world had already moved.
    """
    config = unused()
    servers = McpServers.build(config)
    reloads = Applying(servers, config)
    booted = reloads.generations.mark

    def refuse() -> BootConfig:
        raise ConfigError("invalid config in the database: agents.sam has no llm")

    with pytest.raises(ConfigError):
        await reloads.apply(refuse)
    assert reloads.generations.mark == booted

    await reloads.apply(reading(config))

    assert reloads.generations.mark != booted


# The grants, agent by agent


def test_a_grant_moved_between_the_defaults_and_the_agent_changes_nothing() -> None:
    """Effective grants, through the one defaults-then-own derivation
    the configuration already has: where a grant is written is not what
    a reload would apply, and what the agent reaches has not moved."""
    servers = McpServers.build(granting(defaults=["tools"], own=None))

    pending = servers.pending_against(granting(defaults=None, own=["tools"]))

    assert pending.grants == ()
    assert pending.changed == ()


def test_an_agent_that_stops_inheriting_a_grant_is_reported() -> None:
    """The same move with the effective set really changing: an empty
    list on the agent opts it out of what its siblings inherit."""
    servers = McpServers.build(granting(defaults=["tools"], own=None))

    pending = servers.pending_against(granting(defaults=["tools"], own=[]))

    assert pending.grants == ("assistant",)


def test_a_narrowed_allow_list_moves_the_agent_s_grants() -> None:
    servers = McpServers.build(config_with({"tools": entry_data()}, {"assistant": ["tools"]}))

    pending = servers.pending_against(
        config_with(
            {"tools": entry_data()},
            {"assistant": [{"server": "tools", "tools": ["secret_word"]}]},
        )
    )

    assert pending.grants == ("assistant",)
    assert pending.changed == ()


async def test_a_deleted_agent_s_grants_stay_pending_until_a_reload() -> None:
    """A boot-loaded agent deleted from storage keeps talking until a
    restart, and a reload would revoke its tools now. So its stored side
    compares as the empty grant set and the revocation is reported,
    until the reload that applies it."""
    before = config_with(
        {"tools": entry_data()}, {"assistant": ["tools"], "helper": ["tools"]}
    )
    after = config_with({"tools": entry_data()}, {"assistant": ["tools"]})
    servers = McpServers.build(before)
    try:
        assert servers.pending_against(after).grants == ("helper",)

        await Applying(servers, before).apply(reading(after))

        assert servers.pending_against(after).grants == ()
    finally:
        await servers.stop_all()


async def test_a_deleted_agent_written_back_is_pending_again() -> None:
    """The population is the agents this process can serve, and a reload
    cannot move it.

    An agent deleted from storage and reloaded away is still one this
    server would talk as, because loading an agent takes a restart. So
    writing it back, identical to what this process booted with, is a
    change a reload would apply, and a comparison over whichever agents
    the current world happens to hold would have stopped seeing it the
    moment the first reload landed.
    """
    booted = config_with(
        {"tools": entry_data()}, {"assistant": ["tools"], "helper": ["tools"]}
    )
    deleted = config_with({"tools": entry_data()}, {"assistant": ["tools"]})
    servers = McpServers.build(booted)
    try:
        await Applying(servers, booted).apply(reading(deleted))
        assert servers.pending_against(deleted).grants == ()

        assert servers.pending_against(booted).grants == ("helper",)
    finally:
        await servers.stop_all()


async def test_an_agent_a_reload_installed_is_still_not_a_grant_change() -> None:
    """The other direction of the same rule. A reload can install grants
    for an agent this process cannot build a session for, and editing
    those grants afterwards changes nothing this server would do: the
    agent arrives at the restart that loads it, with whatever the store
    says then.
    """
    booted = config_with({"tools": entry_data()}, {"assistant": ["tools"]})
    added = config_with(
        {"tools": entry_data()}, {"assistant": ["tools"], "helper": ["tools"]}
    )
    narrowed = config_with(
        {"tools": entry_data()},
        {
            "assistant": ["tools"],
            "helper": [{"server": "tools", "tools": ["secret_word"]}],
        },
    )
    servers = McpServers.build(booted)
    try:
        await Applying(servers, booted).apply(reading(added))

        assert servers.pending_against(narrowed).grants == ()
    finally:
        await servers.stop_all()


def test_an_agent_only_the_stored_side_knows_is_not_a_grant_change() -> None:
    """It rides the agents' own added row instead: its grants describe a
    world that begins at the restart that adds it, and claiming them as
    a reload away would be claiming an agent this server cannot serve
    yet."""
    servers = McpServers.build(config_with({"tools": entry_data()}, {"assistant": ["tools"]}))

    pending = servers.pending_against(
        config_with({"tools": entry_data()}, {"assistant": ["tools"], "helper": ["tools"]})
    )

    assert pending.grants == ()
    assert pending.changed == ()
