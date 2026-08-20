"""What a stored configuration would change about the MCP world that is
running.

The registry is the real one, and the read under test is the one an
operator's "what have I written that is not in effect" goes through. Its
baseline is the generation that is installed rather than the boot, which
is the whole reason it exists: `POST /runtime/mcp-servers/reload` swaps
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
from tests.support.tools_mcp import entry_data, reading, started
from tests.support.tools_mcp import reload_config as config_with
from vinga_server.config import Config
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


async def test_a_prompt_only_edit_is_pending_and_the_connection_still_stands() -> None:
    """The two answers differ on purpose. `instructions` is prompt text
    the connection never sees, so the reload reports the entry as
    unchanged and keeps the live connection; the entry is still
    different from what is running, so it is pending until that reload
    happens.
    """
    before = config_with({"tools": entry_data()}, {"assistant": ["tools"]})
    after = config_with(
        {"tools": entry_data(instructions="Read this first.")}, {"assistant": ["tools"]}
    )
    servers = await started(before)
    try:
        kept = servers.manager_of("tools")
        assert servers.status()["tools"]["state"] == CONNECTED

        assert servers.pending_against(after).changed == ("tools",)

        applied = await servers.reload(reading(after))

        # Nothing about the connection moved, and the entry is not
        # pending any more.
        assert applied.unchanged == ("tools",)
        assert servers.manager_of("tools") is kept
        assert servers.status()["tools"]["state"] == CONNECTED
        assert servers.pending_against(after).changed == ()
    finally:
        await servers.stop_all()


async def test_the_identities_are_swapped_with_the_world_they_describe() -> None:
    """A reload's baseline moves with it: what was pending a moment ago
    is what is running now, and the same read taken against the same
    stored world answers with nothing."""
    servers = McpServers.build(unused())
    after = unused(tool_timeout_s=3.5)
    assert servers.pending_against(after).changed == ("tools",)

    await servers.reload(reading(after))

    assert servers.pending_against(after) == servers.pending_against(after)
    assert servers.pending_against(after).changed == ()
    # And the world that was running is now the pending one.
    assert servers.pending_against(unused()).changed == ("tools",)


async def test_the_generation_mark_moves_only_when_a_world_is_installed() -> None:
    """What M2's read captures across its own await. A refused reload
    installs nothing, so it moves nothing."""
    config = unused()
    servers = McpServers.build(config)
    booted = servers.generation

    def refuse() -> tuple[Config, SecretStore | None]:
        raise ConfigError("invalid config in the database: agents.sam has no llm")

    with pytest.raises(ConfigError):
        await servers.reload(refuse)
    assert servers.generation == booted

    await servers.reload(reading(config))

    assert servers.generation != booted


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

        await servers.reload(reading(after))

        assert servers.pending_against(after).grants == ()
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
