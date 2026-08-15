"""Reloading the MCP servers of a running registry.

The registry is the real one and the servers are the real stdio server
spawned as a subprocess, so what is being diffed, stopped and started
here is what a deployment diffs, stops and starts. What stands in for
the database is the `read` callable the reload takes: the re-read is the
configuration layer's, handed in, which is what lets these tests be
about the two phases rather than about SQLite.

Two properties carry most of the file. An unchanged entry keeps the
connection it had, proven by identity rather than by state, because a
manager that was stopped and started again would report the same state.
And a refusal changes nothing, proven after each of the four ways
preparation can fail.
"""

import asyncio
import logging
import sys
import threading
import traceback
from pathlib import Path

import pytest
from cryptography.fernet import Fernet, MultiFernet

import samtal_server.tools.mcp as mcp_module
from samtal_server.config import Config
from samtal_server.config.loader import (
    ConfigError,
    DatabaseBusyError,
    ReloadInProgressError,
    StorageError,
)
from samtal_server.config.models import McpServerConfig
from samtal_server.config.secrets import (
    SecretLocation,
    SecretStore,
    encrypt,
    generate_key,
)
from samtal_server.runtime.prompt import Guidance, ServerInstructions
from samtal_server.tools.mcp import (
    APPLIED,
    CONNECTED,
    DOWN,
    REFUSED,
    REFUSED_BUSY,
    REFUSED_IN_PROGRESS,
    REFUSED_INVALID,
    REFUSED_UNEXPECTED,
    REFUSED_UNREADABLE,
    RELOAD_REFUSED,
    RELOAD_UNREADABLE,
    UNUSED,
    McpServerManager,
    McpServers,
    McpSlice,
    McpToolNotGranted,
)
from tests.support.mcp_stdio_server import SHIPPED_INSTRUCTIONS
from tests.unit.test_tools_mcp import MANAGER_LOGGER, fields_of, one_event

STDIO_SERVER = Path(__file__).parents[1] / "support" / "mcp_stdio_server.py"

SECRET = "sk-test-4f8b2c9e-never-a-real-credential"


def entry_data(**overrides: object) -> dict[str, object]:
    return {
        "transport": "stdio",
        "command": sys.executable,
        "args": [str(STDIO_SERVER)],
    } | overrides


def stdio_entry(**overrides: object) -> McpServerConfig:
    """One entry as a model, for the tests that build a manager by hand
    rather than through a configuration."""
    return McpServerConfig.model_validate(entry_data(**overrides))


def config_with(
    servers: dict[str, object],
    grants: dict[str, list],
    local_only: bool = False,
) -> Config:
    """One agent per grant list, so a test can move an entry between
    agents as well as in and out of the configuration."""
    return Config(
        server={"local_only": local_only},
        providers={
            stage: {"mock": {"type": "mock"}} for stage in ("llm", "asr", "tts", "vad")
        },
        mcp_servers=servers,
        agent_defaults=dict.fromkeys(("llm", "asr", "tts", "vad"), "mock"),
        agents={name: {"prompt": "A", "mcp": mcp} for name, mcp in grants.items()},
        default_agent=next(iter(grants)),
    )


def reading(config: Config, secrets: SecretStore | None = None):
    """The re-read a reload is handed, standing in for the database."""
    return lambda: (config, secrets)


def manager_of(servers: McpServers, entry: str) -> object:
    """The manager object behind one entry.

    Reached through the registry's own attribute on purpose: what an
    unchanged entry keeps is this object, and every visible property of
    it (its state, its tools, its instant) would look the same on a
    manager that had been stopped and started again.
    """
    return servers._managers[entry]


async def started(config: Config, secrets: SecretStore | None = None) -> McpServers:
    servers = McpServers.build(config, secrets)
    await servers.start_all()
    return servers


# The diff


async def test_a_new_entry_is_started_and_an_unchanged_one_is_left_alone() -> None:
    before = config_with({"tools": entry_data()}, {"assistant": ["tools"]})
    after = config_with(
        {"tools": entry_data(), "extra": entry_data()}, {"assistant": ["tools", "extra"]}
    )
    servers = await started(before)
    try:
        kept = manager_of(servers, "tools")
        offered = servers.tools_for(["tools"])

        applied = await servers.reload(reading(after))

        assert applied.started == ("extra",)
        assert applied.restarted == ()
        assert applied.stopped == ()
        assert applied.unchanged == ("tools",)
        # The same manager, and the very same published tool objects on
        # it: nothing reconnected, nothing was listed a second time.
        assert manager_of(servers, "tools") is kept
        assert servers.status()["tools"]["state"] == CONNECTED
        assert all(
            before is after
            for before, after in zip(offered, servers.tools_for(["tools"]), strict=True)
        )
        assert servers.status()["extra"]["state"] == CONNECTED
    finally:
        await servers.stop_all()


async def test_a_changed_fragment_is_stopped_rebuilt_and_started() -> None:
    before = config_with({"tools": entry_data()}, {"assistant": ["tools"]})
    after = config_with(
        {"tools": entry_data(tool_timeout_s=3.5)}, {"assistant": ["tools"]}
    )
    servers = await started(before)
    try:
        was = manager_of(servers, "tools")
        assert servers.timeout_for("tools") == 15.0

        applied = await servers.reload(reading(after))

        assert applied.restarted == ("tools",)
        assert (applied.started, applied.stopped, applied.unchanged) == ((), (), ())
        assert manager_of(servers, "tools") is not was
        assert servers.timeout_for("tools") == 3.5
        assert servers.status()["tools"]["state"] == CONNECTED
        assert await servers.call("tools__secret_word", {}, "assistant") == (
            "rhubarb",
            False,
        )
    finally:
        await servers.stop_all()


async def test_an_instructions_only_edit_keeps_the_connection() -> None:
    """The guidance is prompt text the connection never sees, so
    applying a rewrite of it must not drop a live connection, with the
    mid-call tools and the respawned child process that would cost. The
    reload therefore reports `unchanged`, which is honest about the
    connection, and the new text is in the slice for the next
    activation to read."""
    before = config_with(
        {"tools": entry_data(instructions="Old guidance.")}, {"assistant": ["tools"]}
    )
    after = config_with(
        {"tools": entry_data(instructions="New guidance.")}, {"assistant": ["tools"]}
    )
    servers = await started(before)
    try:
        kept = manager_of(servers, "tools")

        applied = await servers.reload(reading(after))

        assert applied.unchanged == ("tools",)
        assert (applied.started, applied.restarted, applied.stopped) == ((), (), ())
        assert manager_of(servers, "tools") is kept
        assert servers.status()["tools"]["state"] == CONNECTED
        # And what an agent activating now is told about the entry is
        # the text that was just written.
        assert servers.guidance_for_agent("assistant") == (Guidance("tools", "New guidance."),)
    finally:
        await servers.stop_all()


async def test_adding_guidance_to_an_entry_that_had_none_keeps_the_connection() -> None:
    before = config_with({"tools": entry_data()}, {"assistant": ["tools"]})
    after = config_with(
        {"tools": entry_data(instructions="New guidance.")}, {"assistant": ["tools"]}
    )
    servers = await started(before)
    try:
        kept = manager_of(servers, "tools")
        assert servers.guidance_for_agent("assistant") == ()

        applied = await servers.reload(reading(after))

        assert applied.unchanged == ("tools",)
        assert manager_of(servers, "tools") is kept
        assert servers.guidance_for_agent("assistant") == (Guidance("tools", "New guidance."),)
    finally:
        await servers.stop_all()


async def test_the_server_instructions_opt_in_toggles_without_a_reconnect() -> None:
    """Both directions, on the same manager object, which is what makes
    the capture rule worth having: what a server ships is captured on
    every connect whatever the flag says, so turning the flag on exposes
    text a connection nobody restarted is already holding, and turning
    it off stops the injection while that connection stands."""
    off = config_with({"tools": entry_data()}, {"assistant": ["tools"]})
    on = config_with(
        {"tools": entry_data(use_server_instructions=True)}, {"assistant": ["tools"]}
    )
    servers = await started(off)
    try:
        kept = manager_of(servers, "tools")
        assert servers.guidance_for_agent("assistant") == ()

        applied = await servers.reload(reading(on))

        assert applied.unchanged == ("tools",)
        assert manager_of(servers, "tools") is kept
        assert servers.guidance_for_agent("assistant") == (
            ServerInstructions("tools", SHIPPED_INSTRUCTIONS),
        )

        applied = await servers.reload(reading(off))

        assert applied.unchanged == ("tools",)
        assert manager_of(servers, "tools") is kept
        assert servers.guidance_for_agent("assistant") == ()
    finally:
        await servers.stop_all()


async def test_an_inject_prompts_edit_restarts_the_connection() -> None:
    """The one prompt field that is not excluded from connection
    identity, and the reason is the honest one: editing it changes what
    a connect fetches from the server, so applying it means fetching
    again."""
    before = config_with({"tools": entry_data()}, {"assistant": ["tools"]})
    after = config_with(
        {"tools": entry_data(inject_prompts=["house_style"])}, {"assistant": ["tools"]}
    )
    servers = await started(before)
    try:
        was = manager_of(servers, "tools")

        applied = await servers.reload(reading(after))

        assert applied.restarted == ("tools",)
        assert manager_of(servers, "tools") is not was
        assert [
            block.name for block in servers.guidance_for_agent("assistant")
        ] == ["house_style"]
    finally:
        await servers.stop_all()


async def test_an_edit_beside_the_guidance_still_restarts_the_entry() -> None:
    """The exclusion is one field and not a general softening: an entry
    whose command or timeout moved is still stopped and rebuilt, even
    when the guidance moved with it."""
    before = config_with(
        {"tools": entry_data(instructions="Old guidance.")}, {"assistant": ["tools"]}
    )
    after = config_with(
        {"tools": entry_data(instructions="New guidance.", tool_timeout_s=3.5)},
        {"assistant": ["tools"]},
    )
    servers = await started(before)
    try:
        was = manager_of(servers, "tools")

        applied = await servers.reload(reading(after))

        assert applied.restarted == ("tools",)
        assert manager_of(servers, "tools") is not was
    finally:
        await servers.stop_all()


async def test_rotated_stored_ciphertext_rebuilds_only_that_entry() -> None:
    """Rotation applies on reload, and it applies to the one entry it
    happened on: the fragment is byte-identical either side, so the
    ciphertext is the whole of what the diff has to see."""
    keys = MultiFernet([Fernet(generate_key())])
    rotated = SecretLocation.mcp_server("tools", "env.API_TOKEN")
    other = SecretLocation.mcp_server("extra", "env.API_TOKEN")
    untouched = encrypt(other, SECRET, keys)
    config = config_with(
        {"tools": entry_data(), "extra": entry_data()},
        {"assistant": ["tools", "extra"]},
    )
    before = SecretStore({rotated: encrypt(rotated, SECRET, keys), other: untouched}, keys)
    after = SecretStore(
        {rotated: encrypt(rotated, "a-new-value", keys), other: untouched}, keys
    )
    servers = await started(config, before)
    try:
        kept = manager_of(servers, "extra")

        applied = await servers.reload(reading(config, after))

        assert applied.restarted == ("tools",)
        assert applied.unchanged == ("extra",)
        assert manager_of(servers, "extra") is kept
    finally:
        await servers.stop_all()


async def test_an_entry_that_is_gone_is_stopped_and_dropped() -> None:
    before = config_with(
        {"tools": entry_data(), "extra": entry_data()},
        {"assistant": ["tools", "extra"]},
    )
    after = config_with({"tools": entry_data()}, {"assistant": ["tools"]})
    servers = await started(before)
    try:
        applied = await servers.reload(reading(after))

        assert applied.stopped == ("extra",)
        assert applied.unchanged == ("tools",)
        assert "extra" not in servers
        assert servers.tools_for(["extra"]) == []
        # And it is gone from the surface too, since it is gone from the
        # configuration.
        assert set(servers.status()) == {"tools"}
    finally:
        await servers.stop_all()


async def test_an_entry_no_agent_references_any_more_is_stopped_and_unused() -> None:
    """The de-referenced case, which is not the deleted one: the entry
    is still configured, so it is still on the status surface, with the
    state that says why it has no connection."""
    before = config_with(
        {"tools": entry_data(), "extra": entry_data()},
        {"assistant": ["tools", "extra"]},
    )
    after = config_with(
        {"tools": entry_data(), "extra": entry_data()}, {"assistant": ["tools"]}
    )
    servers = await started(before)
    try:
        applied = await servers.reload(reading(after))

        assert applied.stopped == ("extra",)
        assert "extra" not in servers
        assert servers.status()["extra"]["state"] == UNUSED
        assert servers.status()["extra"]["grants"] == {}
    finally:
        await servers.stop_all()


async def test_an_entry_granted_to_another_agent_keeps_its_connection() -> None:
    """A grant moving from one agent to another changes who may reach
    the server, not whether it is connected."""
    before = config_with(
        {"tools": entry_data()}, {"assistant": ["tools"], "helper": []}
    )
    after = config_with({"tools": entry_data()}, {"assistant": [], "helper": ["tools"]})
    servers = await started(before)
    try:
        kept = manager_of(servers, "tools")

        applied = await servers.reload(reading(after))

        assert applied.unchanged == ("tools",)
        assert manager_of(servers, "tools") is kept
        assert servers.tools_for_agent("assistant") == []
        assert servers.tools_for_agent("helper")
        assert servers.status()["tools"]["grants"] == {"helper": None}
    finally:
        await servers.stop_all()


# A refusal applies nothing
#
# Four ways preparation can fail, and the same assertion after each: the
# managers, the grants and the status are exactly what they were. They
# are separate tests rather than one parametrized one because the fourth
# needs a different running configuration to be refused at all.


async def unchanged_by(servers: McpServers, read) -> str:
    """Run a reload that must be refused, and assert nothing moved."""
    before = servers.status()
    kept = dict(servers._managers)
    granted = servers.tools_for_agent("assistant")

    with pytest.raises(ConfigError) as caught:
        await servers.reload(read)

    assert servers.status() == before
    assert servers._managers == kept
    assert servers.tools_for_agent("assistant") == granted
    return str(caught.value)


async def test_a_snapshot_that_will_not_validate_changes_nothing() -> None:
    """The re-read raises before a candidate is built at all, which is
    what a stored configuration that no longer composes does.

    It refuses in the same words the other half of the preparation
    refuses in, guarantee included: which half of a two-phase apply a
    refusal came out of is this module's business, and what the operator
    needs to know is that the servers are as they were."""
    config = config_with({"tools": entry_data()}, {"assistant": ["tools"]})
    servers = await started(config)
    stored = "invalid config in the database: agents.sam has no llm"

    def refuse() -> tuple[Config, SecretStore | None]:
        raise ConfigError(stored)

    try:
        assert await unchanged_by(servers, refuse) == f"{RELOAD_REFUSED} {stored}"
    finally:
        await servers.stop_all()


@pytest.mark.parametrize("refusal", [DatabaseBusyError, StorageError])
async def test_the_two_read_refusals_that_are_not_about_the_snapshot_keep_their_type(
    refusal: type[ConfigError],
) -> None:
    """Their type is the answer: a busy database is retryable and the
    API answers 409, unreadable stored state is not the caller's fault
    and it answers 500. Wrapping either in the refused sentence would
    turn both into 422."""
    config = config_with({"tools": entry_data()}, {"assistant": ["tools"]})
    servers = await started(config)
    said = "the configuration database could not be read"

    def refuse() -> tuple[Config, SecretStore | None]:
        raise refusal(said)

    try:
        with pytest.raises(refusal) as caught:
            await servers.reload(refuse)
        assert str(caught.value) == said
        assert "tools" in servers
    finally:
        await servers.stop_all()


async def test_a_read_refusal_carries_nothing_of_what_the_read_was_holding() -> None:
    """Raised outside the handler, the rule this codebase settled on: a
    refusal raised inside one keeps the exception being handled as its
    context, and a load that failed is holding a snapshot."""
    config = config_with({"tools": entry_data()}, {"assistant": ["tools"]})
    servers = await started(config)

    def refuse() -> tuple[Config, SecretStore | None]:
        raise ConfigError("invalid config in the database: agents.sam has no llm")

    try:
        with pytest.raises(ConfigError) as caught:
            await servers.reload(refuse)
        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None
    finally:
        await servers.stop_all()


async def test_an_unset_variable_changes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SAMTAL_TEST_ABSENT_TOKEN", raising=False)
    config = config_with({"tools": entry_data()}, {"assistant": ["tools"]})
    broken = config_with(
        {
            "tools": entry_data(),
            "extra": entry_data(env={"API_TOKEN": "$SAMTAL_TEST_ABSENT_TOKEN"}),
        },
        {"assistant": ["tools", "extra"]},
    )
    servers = await started(config)
    try:
        message = await unchanged_by(servers, reading(broken))

        assert "nothing was changed" in message
        assert "SAMTAL_TEST_ABSENT_TOKEN" in message
        assert "extra" not in servers
    finally:
        await servers.stop_all()


async def test_a_secret_that_will_not_decrypt_changes_nothing() -> None:
    config = config_with({"tools": entry_data()}, {"assistant": ["tools"]})
    location = SecretLocation.mcp_server("tools", "env.API_TOKEN")
    written = encrypt(location, SECRET, MultiFernet([Fernet(generate_key())]))
    # Stored under a key this store does not have, which is a rotation
    # that dropped the key the token was written under.
    unopenable = SecretStore({location: written}, MultiFernet([Fernet(generate_key())]))
    servers = await started(config)
    try:
        message = await unchanged_by(servers, reading(config, unopenable))

        assert "nothing was changed" in message
        assert location.describe() in message
        assert SECRET not in message
    finally:
        await servers.stop_all()


async def test_an_egress_declaration_local_only_forbids_changes_nothing() -> None:
    config = config_with(
        {"tools": entry_data(egress=False)}, {"assistant": ["tools"]}, local_only=True
    )
    broken = config_with(
        {"tools": entry_data(egress=False), "extra": entry_data()},
        {"assistant": ["tools", "extra"]},
        local_only=True,
    )
    servers = await started(config)
    try:
        message = await unchanged_by(servers, reading(broken))

        assert "nothing was changed" in message
        assert "mcp_servers.extra" in message
        assert "local_only" in message
    finally:
        await servers.stop_all()


# What is not a preparation failure


async def test_a_candidate_that_cannot_connect_applies_as_down_and_revives() -> None:
    """The boot's rule carried over: a configuration error refuses, a
    dead box does not. It applies, says why it is down, and comes back
    the way a server that was down at boot comes back."""
    before = config_with({"tools": entry_data()}, {"assistant": ["tools"]})
    after = config_with(
        {"tools": entry_data(), "extra": entry_data(command="/nonexistent/mcp", args=[])},
        {"assistant": ["tools", "extra"]},
    )
    servers = await started(before)
    try:
        applied = await servers.reload(reading(after))

        assert applied.started == ("extra",)
        assert servers.status()["extra"]["state"] == DOWN
        assert servers.status()["extra"]["reason"]
        assert servers.tools_for(["extra"]) == []

        # And it is revivable, the way any down server is: the box comes
        # back, a session opens, and the tools arrive with no reload and
        # no restart.
        manager_of(servers, "extra")._config = servers._managers["tools"]._config
        servers.revive_for_agents(["assistant"])
        async with asyncio.timeout(20):
            while servers.status()["extra"]["state"] != CONNECTED:
                await asyncio.sleep(0.05)
        assert servers.tools_for_agent("assistant")
    finally:
        await servers.stop_all()


async def test_a_second_reload_while_one_is_running_is_refused() -> None:
    """Refused rather than queued: the second one carries a
    configuration read later than the first one's, into a world the
    first one is halfway through changing."""
    config = config_with({"tools": entry_data()}, {"assistant": ["tools"]})
    servers = await started(config)
    gate = threading.Event()

    def held() -> tuple[Config, SecretStore | None]:
        # Blocking a worker thread, which is where the reload runs its
        # synchronous half, so the first reload is genuinely mid-flight.
        gate.wait(30)
        return config, None

    first = asyncio.create_task(servers.reload(held))
    try:
        await asyncio.sleep(0)
        with pytest.raises(ReloadInProgressError) as caught:
            await servers.reload(reading(config))
        assert "already running" in str(caught.value)
    finally:
        gate.set()
        await first
        await servers.stop_all()

    # And once it has answered, the next one runs.
    servers = await started(config)
    try:
        assert (await servers.reload(reading(config))).unchanged == ("tools",)
    finally:
        await servers.stop_all()


# The grants behind the swap


async def test_the_grants_swap_with_the_managers() -> None:
    before = config_with({"tools": entry_data()}, {"assistant": []})
    after = config_with({"tools": entry_data()}, {"assistant": ["tools"]})
    servers = await started(before)
    try:
        # Configured, referenced by nobody, so nothing was connected for
        # it and the agent reaches nothing.
        assert servers.tools_for_agent("assistant") == []
        assert servers.status()["tools"]["state"] == UNUSED

        applied = await servers.reload(reading(after))

        assert applied.started == ("tools",)
        assert {tool.name for tool in servers.tools_for_agent("assistant")} >= {
            "tools__secret_word"
        }
    finally:
        await servers.stop_all()


async def test_a_narrowed_allow_list_applies_without_touching_the_connection() -> None:
    """Narrowing a grant is a configuration change about the agent, not
    about the server, so the entry is unchanged in the diff and keeps
    the connection it had while what the agent may reach moves."""
    before = config_with({"tools": entry_data()}, {"assistant": ["tools"]})
    after = config_with(
        {"tools": entry_data()},
        {"assistant": [{"server": "tools", "tools": ["secret_word"]}]},
    )
    servers = await started(before)
    try:
        kept = manager_of(servers, "tools")
        assert len(servers.tools_for_agent("assistant")) > 1

        applied = await servers.reload(reading(after))

        assert applied.unchanged == ("tools",)
        assert manager_of(servers, "tools") is kept
        assert [tool.name for tool in servers.tools_for_agent("assistant")] == [
            "tools__secret_word"
        ]
        with pytest.raises(McpToolNotGranted):
            await servers.call("tools__add", {"first": 1, "second": 2}, "assistant")
    finally:
        await servers.stop_all()


async def test_a_grant_added_to_a_connected_server_is_checked_on_the_reload(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The entry is unchanged, so nothing reconnects and nothing
    publishes again; the allow list arrived all the same, and a name in
    it that no tool answers to is said out loud when it does rather than
    at the next connect, which may be days away."""
    before = config_with({"tools": entry_data()}, {"assistant": ["tools"]})
    after = config_with(
        {"tools": entry_data()},
        {"assistant": [{"server": "tools", "tools": ["no_such_tool"]}]},
    )
    servers = await started(before)
    try:
        with caplog.at_level(logging.WARNING, logger="samtal_server.tools.mcp"):
            applied = await servers.reload(reading(after))

        assert applied.unchanged == ("tools",)
        (warned,) = [
            record.getMessage()
            for record in caplog.records
            if "not published" in record.getMessage()
        ]
        assert "no_such_tool" in warned
    finally:
        await servers.stop_all()


async def test_an_agent_the_slice_does_not_know_reaches_nothing() -> None:
    """A session outlives the configuration it was built on: the agent
    it is talking as can have been deleted by the reload that just
    landed, and that is not a reason to fail its next reply."""
    config = config_with({"tools": entry_data()}, {"assistant": ["tools"]})
    servers = await started(config)
    try:
        assert servers.tools_for_agent("nobody") == []
        servers.revive_for_agents(["nobody"])
    finally:
        await servers.stop_all()


# The stop bound, all the way down
#
# Cancelling a task that will not end is a request, not a guarantee, so
# the reload's envelope has to survive a cleanup handler that ignores
# it. Both bounds are shortened here; what is asserted is the shape of
# the outcome, not the wall clock of the production constants.


class StubbornManager(McpServerManager):
    """A manager whose task swallows its cancellation and goes on
    unwinding, which is what an exit stack awaiting a far side that has
    stopped answering looks like from here."""

    def __init__(self, name: str, config: object, holding_s: float) -> None:
        super().__init__(name, config)
        self._holding_s = holding_s

    async def _run(self) -> None:
        self._became(CONNECTED, None)
        self._settled.set()
        try:
            await self._stop.wait()
            await asyncio.sleep(self._holding_s)
        except asyncio.CancelledError:
            # Suppressed on purpose, and then some.
            await asyncio.sleep(self._holding_s)
        finally:
            self._became(DOWN, None)


async def test_a_manager_that_will_not_stop_is_left_behind_inside_the_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stop that cancelled and then waited without a bound would hand
    the far side the endpoint's whole envelope, and with it the client's
    timeout. The wait after the cancellation is bounded too, and what
    happens at that bound is that the task is left to finish while the
    caller gets its answer."""
    monkeypatch.setattr(mcp_module, "CANCEL_TIMEOUT_S", 0.05)
    holding = 3.0
    manager = StubbornManager("tools", stdio_entry(), holding)
    await manager.start()
    task = manager._task
    assert task is not None

    began = asyncio.get_running_loop().time()
    await manager.stop(0.05)
    elapsed = asyncio.get_running_loop().time() - began

    # Well inside the two bounds, and nowhere near what the task is
    # holding out for.
    assert elapsed < holding / 2
    assert not task.done()
    # Held rather than dropped: the loop keeps only a weak reference to
    # a task nobody awaits, and one ending in an exception nobody took
    # prints about it at shutdown.
    assert task in mcp_module._abandoned

    # And once it does finish, nothing of it is held: the callback that
    # consumes what it ended with drops it too.
    await asyncio.wait_for(task, timeout=holding * 2)
    assert task not in mcp_module._abandoned


# A caller that goes away mid-apply
#
# A client disconnecting cancels the handler awaiting the reload, and
# the phase that stops and starts things must not be left half done by
# that. Cancelled in each of its two halves, and asserted afterwards on
# the world rather than on the outcome, which the cancelled caller never
# receives.


async def settled(servers: McpServers) -> None:
    """Wait for an apply that outlived its caller to finish."""
    async with asyncio.timeout(20):
        while servers._reloading:
            await asyncio.sleep(0.01)


class SlowStopManager(McpServerManager):
    """A manager that takes its time going away, so a reload can be
    cancelled while it is being stopped."""

    async def _run(self) -> None:
        self._became(CONNECTED, None)
        self._settled.set()
        await self._stop.wait()
        await asyncio.sleep(0.2)
        self._became(DOWN, None)


async def test_a_caller_that_goes_away_during_the_stops_leaves_one_world() -> None:
    config = config_with({"tools": entry_data()}, {"assistant": ["tools"]})
    after = config_with({"extra": entry_data()}, {"assistant": ["extra"]})
    going = SlowStopManager("tools", stdio_entry())
    servers = McpServers({"tools": going}, McpSlice.of(config))
    await going.start()

    asked = asyncio.create_task(servers.reload(reading(after)))
    await asyncio.sleep(0.05)
    asked.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asked

    try:
        await settled(servers)

        # The apply ran to its end: the entry that went is gone from the
        # managers and from the slice, and the one that arrived is
        # connected and reachable by the agent that was granted it.
        assert "tools" not in servers
        assert set(servers.status()) == {"extra"}
        assert servers.status()["extra"]["state"] == CONNECTED
        assert servers.tools_for_agent("assistant")
        # And nothing of the old world is still running.
        assert going._task is None or going._task.done()
        assert mcp_module._abandoned == set()
        # The exclusion was held until the apply was over, so the next
        # reload is answered rather than refused.
        assert (await servers.reload(reading(after))).unchanged == ("extra",)
    finally:
        await servers.stop_all()


async def test_a_caller_that_goes_away_during_the_starts_leaves_one_world(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = McpServerManager.start

    async def slow_start(self: McpServerManager) -> None:
        # Long enough to be cancelled inside, short enough that the
        # test does not wait on it twice.
        await asyncio.sleep(0.2)
        await original(self)

    monkeypatch.setattr(McpServerManager, "start", slow_start)
    before = config_with({"tools": entry_data()}, {"assistant": ["tools"]})
    after = config_with(
        {"tools": entry_data(), "extra": entry_data()},
        {"assistant": ["tools", "extra"]},
    )
    servers = await started(before)
    try:
        kept = manager_of(servers, "tools")

        asked = asyncio.create_task(servers.reload(reading(after)))
        await asyncio.sleep(0.05)
        asked.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asked
        await settled(servers)

        # The started candidate is in the world it was started for, and
        # the unchanged entry was never touched.
        assert servers.status()["extra"]["state"] == CONNECTED
        assert manager_of(servers, "tools") is kept
        assert {tool.name for tool in servers.tools_for_agent("assistant")} >= {
            "extra__secret_word",
            "tools__secret_word",
        }
    finally:
        await servers.stop_all()


# The reload, as one event
#
# `mcp_reload` is emitted exactly once per reload, at whichever of the
# two ends the reload reached: a refusal where it is classified, an
# apply as the last act of the shielded task. Both halves matter to an
# operator counting them, and the second one has to survive the caller
# going away, which is the whole reason the apply is shielded.


async def test_an_applied_reload_counts_what_it_moved(
    caplog: pytest.LogCaptureFixture,
) -> None:
    before = config_with({"tools": entry_data()}, {"assistant": ["tools"]})
    after = config_with(
        {"tools": entry_data(), "extra": entry_data()}, {"assistant": ["tools", "extra"]}
    )
    servers = await started(before)
    try:
        with caplog.at_level(logging.INFO, logger=MANAGER_LOGGER):
            await servers.reload(reading(after))

        applied = one_event(caplog, "mcp_reload")
        assert applied.levelno == logging.INFO
        fields = fields_of(applied)
        assert isinstance(fields.pop("duration_ms"), int)
        # Counts rather than names: which entries they were is the
        # status surface's answer, taken in the same breath.
        assert fields == {
            "event": "mcp_reload",
            "outcome": APPLIED,
            "started": 1,
            "restarted": 0,
            "stopped": 0,
            "unchanged": 1,
        }
    finally:
        await servers.stop_all()


@pytest.mark.parametrize(
    ("raiser", "escapes", "token"),
    [
        (ConfigError, ConfigError, REFUSED_INVALID),
        (DatabaseBusyError, DatabaseBusyError, REFUSED_BUSY),
        (StorageError, StorageError, REFUSED_UNREADABLE),
        (RuntimeError, StorageError, REFUSED_UNEXPECTED),
    ],
)
async def test_a_refused_reload_says_which_kind_of_refusal_it_was(
    raiser: type[Exception],
    escapes: type[Exception],
    token: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One token per refusal type, chosen where the exception is
    classified. The types are the same ones the API turns into status
    codes, which is what makes the set closed and worth grouping by; the
    fourth is the net under them, for a `read` that fails in a way the
    configuration layer has no type for.

    The first three leave as themselves, because their message is this
    application's own words and the API puts it in a response body. The
    fourth does not: it is classified here, and what leaves is a
    `StorageError` with a fixed sentence, since a `read` that failed
    unexpectedly may be holding anything at all.
    """
    config = config_with({"tools": entry_data()}, {"assistant": ["tools"]})
    servers = await started(config)

    def refuse() -> tuple[Config, SecretStore | None]:
        raise raiser("a message this line has no business carrying")

    try:
        with caplog.at_level(logging.INFO, logger=MANAGER_LOGGER):
            with pytest.raises(escapes) as caught:
                await servers.reload(refuse)

        refused = one_event(caplog, "mcp_reload")
        assert refused.levelno == logging.WARNING
        assert fields_of(refused) == {
            "event": "mcp_reload",
            "outcome": REFUSED,
            "reason": token,
        }
        # The refusal's own sentence travels to whoever asked for the
        # reload, which is where a message belongs; this line is a
        # token and a fact about the servers.
        assert "no business" not in refused.getMessage()
        # And whatever leaves carries no chain, which is what raising it
        # outside the handler is for.
        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None
    finally:
        await servers.stop_all()


async def test_an_unexpected_read_failure_leaves_none_of_itself_behind(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The `read` callable opens a database, and the four types the
    configuration layer models are not everything a database driver can
    raise. Anything else is somebody's else's exception holding
    somebody else's words, and one of the things it plausibly holds is
    a connection string, so it is classified and then dropped."""
    config = config_with({"tools": entry_data()}, {"assistant": ["tools"]})
    servers = await started(config)

    def refuse() -> tuple[Config, SecretStore | None]:
        raise RuntimeError(f"could not connect using {SECRET}")

    try:
        with caplog.at_level(logging.INFO, logger=MANAGER_LOGGER):
            with pytest.raises(StorageError) as caught:
                await servers.reload(refuse)

        assert str(caught.value) == RELOAD_UNREADABLE
        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None
        # Everything a handler above could render of it.
        rendered = "".join(
            traceback.format_exception(
                type(caught.value), caught.value, caught.value.__traceback__
            )
        )
        assert SECRET not in rendered
        assert SECRET not in caplog.text
        # It still refuses rather than half applying, and it says so.
        assert fields_of(one_event(caplog, "mcp_reload"))["reason"] == REFUSED_UNEXPECTED
        assert "tools" in servers
    finally:
        await servers.stop_all()


async def test_a_candidate_that_will_not_build_is_refused_as_invalid(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half of the preparation, which refuses after the read
    succeeded. One event either way: an operator counting refused
    reloads does not care which half of a two-phase apply refused."""
    monkeypatch.delenv("SAMTAL_TEST_ABSENT_TOKEN", raising=False)
    config = config_with({"tools": entry_data()}, {"assistant": ["tools"]})
    broken = config_with(
        {
            "tools": entry_data(),
            "extra": entry_data(env={"API_TOKEN": "$SAMTAL_TEST_ABSENT_TOKEN"}),
        },
        {"assistant": ["tools", "extra"]},
    )
    servers = await started(config)
    try:
        with caplog.at_level(logging.INFO, logger=MANAGER_LOGGER):
            with pytest.raises(ConfigError):
                await servers.reload(reading(broken))

        assert fields_of(one_event(caplog, "mcp_reload")) == {
            "event": "mcp_reload",
            "outcome": REFUSED,
            "reason": REFUSED_INVALID,
        }
    finally:
        await servers.stop_all()


async def test_a_second_reload_is_refused_as_one_already_running(
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = config_with({"tools": entry_data()}, {"assistant": ["tools"]})
    servers = await started(config)
    gate = threading.Event()

    def held() -> tuple[Config, SecretStore | None]:
        gate.wait(30)
        return config, None

    first = asyncio.create_task(servers.reload(held))
    try:
        await asyncio.sleep(0)
        with caplog.at_level(logging.INFO, logger=MANAGER_LOGGER):
            with pytest.raises(ReloadInProgressError):
                await servers.reload(reading(config))

            # Read while the first reload is still held, so the one
            # event in hand is the refusal and not the apply that
            # follows it.
            assert fields_of(one_event(caplog, "mcp_reload")) == {
                "event": "mcp_reload",
                "outcome": REFUSED,
                "reason": REFUSED_IN_PROGRESS,
            }
    finally:
        gate.set()
        await first
        await servers.stop_all()


async def test_an_apply_whose_caller_went_away_is_still_reported_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The exactly-once promise, at the one point it is hard to keep. A
    client that disconnects cancels the handler awaiting the reload, and
    the apply carries on behind its shield; the event is that task's
    last act, so the reload that really happened is recorded whether or
    not anybody is left to be told about it."""
    config = config_with({"tools": entry_data()}, {"assistant": ["tools"]})
    after = config_with({"extra": entry_data()}, {"assistant": ["extra"]})
    going = SlowStopManager("tools", stdio_entry())
    servers = McpServers({"tools": going}, McpSlice.of(config))
    await going.start()

    with caplog.at_level(logging.INFO, logger=MANAGER_LOGGER):
        asked = asyncio.create_task(servers.reload(reading(after)))
        await asyncio.sleep(0.05)
        asked.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asked
        try:
            await settled(servers)
        finally:
            await servers.stop_all()

    fields = fields_of(one_event(caplog, "mcp_reload"))
    assert fields["outcome"] == APPLIED
    assert (fields["started"], fields["stopped"]) == (1, 1)


async def test_a_cancelled_preparation_holds_the_exclusion_until_its_read_ends() -> None:
    """The other half of the same promise, and the one a shield does not
    obviously cover.

    Nothing the preparation does can leave a half-changed world, so
    there is no world to protect here. What has to be waited for is the
    re-read: it runs in a worker thread, taking the database's write
    lock and waiting out its busy timeout, and a thread cannot be
    cancelled. Releasing the exclusion when the caller went away would
    let the next reload start a read against a lock the last one is
    still holding, and answer a caller who did nothing wrong that the
    database is busy.
    """
    config = config_with({"tools": entry_data()}, {"assistant": ["tools"]})
    servers = await started(config)
    gate = threading.Event()

    def held() -> tuple[Config, SecretStore | None]:
        # Blocking the worker thread, which is exactly where a slow read
        # blocks and exactly what a cancellation cannot reach.
        gate.wait(30)
        return config, None

    try:
        asked = asyncio.create_task(servers.reload(held))
        await asyncio.sleep(0.05)
        asked.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asked

        # The read is still running, so the exclusion is still held, and
        # the answer is the one that says to ask again rather than a
        # busy database or a second read of the same rows.
        with pytest.raises(ReloadInProgressError):
            await servers.reload(reading(config))

        gate.set()
        await settled(servers)

        # And once it really has ended, the next one is answered.
        assert (await servers.reload(reading(config))).unchanged == ("tools",)
    finally:
        gate.set()
        await servers.stop_all()
