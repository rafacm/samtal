"""The guidance an MCP server ships about itself, and the rules it is
consumed under.

Two halves, and the split is deliberate. What a real server does is
proven against the real one: `tests/support/mcp_stdio_server.py` now
declares `instructions` and publishes three prompts, so the capture, the
listing-first discovery, the rendering and the required-argument rule
run over a subprocess and a real transport.

What a *hostile or broken* server does is proven against a stub session,
because those cases are about this client's rules rather than about the
wire: a listing that never ends, a cursor that repeats, a fetch that
stalls, a message carrying an image. A server cooperative enough to be
scripted into those shapes would be a second implementation of the SDK's
server half, and the assertions would be about that instead.
"""

import asyncio
import json
import logging
import sys
import time
from collections.abc import Sequence
from pathlib import Path

import mcp.types
import pytest

import vinga_server.tools.mcp as mcp_module
from tests.support.mcp_stdio_server import (
    FIRST_VOICE,
    HOUSE_STYLE,
    SECOND_VOICE,
    SHIPPED_INSTRUCTIONS,
)
from tests.support.tools_mcp import Applying, reading
from vinga_server import logs
from vinga_server.config import Config, McpServerConfig
from vinga_server.config.cli import APPLY_READ_TIMEOUT_S
from vinga_server.runtime.prompt import Guidance, ServerInstructions, ServerPrompt
from vinga_server.tools.mcp import (
    CONNECT_TIMEOUT_S,
    CONNECTED,
    DISCOVERY_DEADLINE,
    LISTING_CAP,
    NO_PROMPTS_CAPABILITY,
    NON_TEXT_CONTENT,
    NOT_LISTED,
    NOTHING_TO_INJECT,
    PAGE_CAP,
    PROMPT_DISCOVERY_TIMEOUT_S,
    PROMPT_LISTING_CAP,
    PROMPT_MESSAGE_CAP,
    PROMPT_PAGE_CAP,
    REQUIRES_ARGUMENTS,
    SHIPPED_BLOCK_LIMIT,
    STOP_TIMEOUT_S,
    TOO_MANY_MESSAGES,
    McpServerManager,
    McpServers,
    prompts,
)

STDIO_SERVER = Path(__file__).parents[1] / "support" / "mcp_stdio_server.py"

MANAGER_LOGGER = "vinga_server.tools.mcp"


def stdio_entry(**overrides: object) -> McpServerConfig:
    return McpServerConfig.model_validate(
        {"transport": "stdio", "command": sys.executable, "args": [str(STDIO_SERVER)]}
        | overrides
    )


async def running(config: McpServerConfig, name: str = "tools") -> McpServerManager:
    manager = McpServerManager(name, config)
    await manager.start()
    return manager


def config_with(entry: object, grants: list[object] | None = None) -> Config:
    return Config(
        server={},
        providers={
            stage: {"mock": {"type": "mock"}} for stage in ("llm", "asr", "tts", "vad")
        },
        mcp_servers={"tools": entry},
        agent_defaults=dict.fromkeys(("llm", "asr", "tts", "vad"), "mock"),
        agents={
            "assistant": {"prompt": "A", "mcp": ["tools"] if grants is None else grants}
        },
        default_agent="assistant",
    )


# What a real server ships, and what an entry has to say to hear it


async def test_a_default_entry_ignores_what_the_server_ships() -> None:
    """Off is off in both channels: the shipped instructions reach no
    prompt, and no prompt is fetched at all."""
    manager = await running(stdio_entry())
    try:
        assert manager.up
        assert manager.shipped_instructions == SHIPPED_INSTRUCTIONS
        assert manager.shipped_prompts == ()
    finally:
        await manager.stop()


async def test_an_opted_in_entry_injects_what_the_server_shipped() -> None:
    """The order the plan fixes: the operator's own block, then the
    server's description of itself, then the prompts the entry named, in
    the order it named them."""
    entry = stdio_entry(
        instructions="Ours first.",
        use_server_instructions=True,
        inject_prompts=["two_voices", "house_style"],
    )
    servers = McpServers.build(config_with(entry))
    await servers.start_all()
    try:
        assert servers.guidance_for_agent("assistant") == (
            Guidance("tools", "Ours first."),
            ServerInstructions("tools", SHIPPED_INSTRUCTIONS),
            ServerPrompt("tools", 1, "two_voices", f"{FIRST_VOICE}\n\n{SECOND_VOICE}"),
            ServerPrompt("tools", 2, "house_style", HOUSE_STYLE),
        )
    finally:
        await servers.stop_all()


async def test_a_multi_message_prompt_renders_as_text_in_order() -> None:
    """The rendering pinned exactly: each message's text in message
    order, joined by one blank line, and the roles dropped, because what
    is injected is one block of standing guidance rather than a dialogue
    to replay."""
    manager = await running(stdio_entry(inject_prompts=["two_voices"]))
    try:
        (rendered,) = manager.shipped_prompts
        assert rendered.text == f"{FIRST_VOICE}\n\n{SECOND_VOICE}"
        assert "user" not in rendered.text and "assistant" not in rendered.text
    finally:
        await manager.stop()


async def test_a_prompt_requiring_arguments_is_skipped_by_its_position(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The listing says the prompt declares a required argument, so it
    is refused before anything is fetched, and the neighbour that is
    usable is not taken down with it."""
    with caplog.at_level(logging.WARNING, logger=MANAGER_LOGGER):
        manager = await running(
            stdio_entry(inject_prompts=["about_a_room", "house_style"])
        )
    try:
        assert [prompt.position for prompt in manager.shipped_prompts] == [2]
    finally:
        await manager.stop()

    assert skips(caplog) == [("tools", "1", REQUIRES_ARGUMENTS)]


async def test_a_name_the_listing_does_not_carry_is_skipped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger=MANAGER_LOGGER):
        manager = await running(stdio_entry(inject_prompts=["no_such_prompt"]))
    try:
        assert manager.shipped_prompts == ()
    finally:
        await manager.stop()

    assert skips(caplog) == [("tools", "1", NOT_LISTED)]


async def test_the_shipped_blocks_go_when_the_connection_does() -> None:
    """They arrived on that connection and they have its lifetime: a
    server that is down has told this one nothing."""
    manager = await running(
        stdio_entry(use_server_instructions=True, inject_prompts=["house_style"])
    )
    assert manager.shipped_instructions is not None and manager.shipped_prompts

    await manager.stop()

    assert manager.shipped_instructions is None
    assert manager.shipped_prompts == ()


async def test_a_connection_dropped_after_a_failed_call_forgets_them_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other unwind path. `_mark_down` clears the published tools,
    and what the server shipped goes with them, or an agent would be
    told about a connection that is gone."""
    manager = await running(
        stdio_entry(use_server_instructions=True, inject_prompts=["house_style"])
    )
    try:

        async def refuse(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("the transport, from under the call")

        monkeypatch.setattr(manager.session, "call_tool", refuse)
        with pytest.raises(RuntimeError):
            await manager.call("tools__secret_word", {})

        assert not manager.up
        assert manager.shipped_instructions is None
        assert manager.shipped_prompts == ()
    finally:
        await manager.stop()


async def test_the_shipped_bytes_reach_no_log_record(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The publishing rule, over a real server that shipped both kinds:
    the connect line and the capture line carry the entry, the positions
    and the sizes, and none of the bytes."""
    with caplog.at_level(logging.INFO):
        manager = await running(
            stdio_entry(
                use_server_instructions=True,
                inject_prompts=["house_style", "two_voices"],
            )
        )
    try:
        assert manager.shipped_instructions == SHIPPED_INSTRUCTIONS
        assert len(manager.shipped_prompts) == 2
    finally:
        await manager.stop()

    written = rendered(caplog)
    assert SHIPPED_INSTRUCTIONS not in written
    assert HOUSE_STYLE not in written
    assert FIRST_VOICE not in written
    # What it does say: the entry, the positions and the sizes.
    assert f"{len(SHIPPED_INSTRUCTIONS)} characters of instructions" in written
    assert f"1 ({len(HOUSE_STYLE)} characters)" in written


# The rules a hostile or broken server is held to


class StubSession:
    """Just enough of a `ClientSession` for the discovery phase.

    Every case below is a shape a cooperative server cannot be asked
    for: a listing without an end, a cursor that comes back, a fetch
    that never answers. What it records is what was called, since half
    of what is under test is that nothing was.
    """

    def __init__(
        self,
        pages: Sequence[tuple[list[mcp.types.Prompt], str | None]] = (),
        results: dict[str, object] | None = None,
    ) -> None:
        self._pages = list(pages)
        self._results = results or {}
        self.listed: list[str | None] = []
        self.fetched: list[str] = []

    async def list_prompts(self, cursor: str | None = None) -> mcp.types.ListPromptsResult:
        self.listed.append(cursor)
        index = 0 if cursor is None else int(cursor)
        prompts, nxt = self._pages[min(index, len(self._pages) - 1)]
        return mcp.types.ListPromptsResult(prompts=prompts, nextCursor=nxt)

    async def get_prompt(self, name: str) -> mcp.types.GetPromptResult:
        self.fetched.append(name)
        answer = self._results[name]
        if isinstance(answer, BaseException):
            raise answer
        if isinstance(answer, float):
            await asyncio.sleep(answer)
        assert isinstance(answer, mcp.types.GetPromptResult)
        return answer


def listed(name: str, arguments: list[mcp.types.PromptArgument] | None = None):
    return mcp.types.Prompt(name=name, arguments=arguments)


def text_result(*texts: str) -> mcp.types.GetPromptResult:
    return mcp.types.GetPromptResult(
        messages=[
            mcp.types.PromptMessage(
                role="user", content=mcp.types.TextContent(type="text", text=text)
            )
            for text in texts
        ]
    )


def image_result() -> mcp.types.GetPromptResult:
    return mcp.types.GetPromptResult(
        messages=[
            mcp.types.PromptMessage(
                role="user", content=mcp.types.TextContent(type="text", text="before")
            ),
            mcp.types.PromptMessage(
                role="user",
                content=mcp.types.ImageContent(
                    type="image", data="aGVsbG8=", mimeType="image/png"
                ),
            ),
        ]
    )


def with_prompts() -> mcp.types.ServerCapabilities:
    return mcp.types.ServerCapabilities(prompts=mcp.types.PromptsCapability())


async def discovered(
    session: StubSession,
    capabilities: mcp.types.ServerCapabilities,
    **overrides: object,
) -> tuple[ServerPrompt, ...]:
    """One discovery phase, run the way `_run` runs it: after the
    connect envelope, over an entry that is otherwise ordinary.

    The redaction of this deployment's own materialized values is
    passed as the identity here and tested where it belongs, over a
    real entry holding a real credential, in
    `test_mcp_status_reflection.py`."""
    # White-box for this file's four reaches into the discovery unit.
    # What each of them is about is a decision taken while a connection
    # is being made to an untrusted server: which blocks are admitted,
    # what a refusal says, and what is never built at all. The public
    # form is a running MCP server behaving badly on purpose, and the
    # cases here are the ones no cooperating server produces: a block
    # one character past the cap, a listing that cannot be read, a
    # rendering that must not be allocated.
    return await prompts._discovered(  # type: ignore[arg-type]
        "tools", stdio_entry(**overrides), session, capabilities, lambda text: text
    )


def skips(caplog: pytest.LogCaptureFixture) -> list[tuple[str, str, str]]:
    """Every skip warning, as (entry, positions, rule). Parsed rather
    than matched whole, because what the assertions are about is exactly
    these three things being in the line and nothing else being."""
    found: list[tuple[str, str, str]] = []
    for record in caplog.records:
        message = record.getMessage()
        if "nothing is injected for inject_prompts position" not in message:
            continue
        head, _, rule = message.rpartition(" (")
        entry = head.split("mcp server ")[1].split(":")[0]
        positions = head.split("position ")[1]
        found.append((entry, positions, rule.rstrip(")")))
    return found


async def test_a_listing_is_walked_page_by_page(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The whole listing, cursor by cursor: a name published on the
    second page is as eligible as one published on the first."""
    session = StubSession(
        pages=[
            ([listed("first")], "1"),
            ([listed("second")], None),
        ],
        results={"second": text_result("the second page's guidance")},
    )

    with caplog.at_level(logging.WARNING, logger=MANAGER_LOGGER):
        captured = await discovered(session, with_prompts(), inject_prompts=["second"])

    assert session.listed == [None, "1"]
    assert [prompt.text for prompt in captured] == ["the second page's guidance"]
    assert skips(caplog) == []


async def test_the_listing_is_finished_before_anything_is_fetched(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Listing first means the whole listing. Stopping as soon as every
    configured name had been seen would fetch while the server was still
    advertising more, which is the one ordering this design exists to
    prevent."""
    session = StubSession(
        pages=[
            ([listed("wanted")], "1"),
            ([listed("other")], "2"),
            ([listed("last")], None),
        ],
        results={"wanted": text_result("guidance")},
    )

    with caplog.at_level(logging.WARNING, logger=MANAGER_LOGGER):
        captured = await discovered(session, with_prompts(), inject_prompts=["wanted"])

    assert session.listed == [None, "1", "2"]
    assert [prompt.text for prompt in captured] == ["guidance"]
    assert skips(caplog) == []


async def test_a_page_longer_than_the_listing_cap_ends_the_walk(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The page cap bounds how many arrays arrive and nothing bounds how
    long one of them is, so a server that answers one instant page with
    an enormous list would cost the loop that reads it rather than any
    of the timers."""
    session = StubSession(
        pages=[([listed(f"p{index}") for index in range(PROMPT_LISTING_CAP + 1)], None)],
        results={},
    )

    with caplog.at_level(logging.WARNING, logger=MANAGER_LOGGER):
        captured = await discovered(session, with_prompts(), inject_prompts=["p0"])

    assert captured == ()
    assert session.fetched == []
    assert skips(caplog) == [("tools", "1", LISTING_CAP)]


async def test_a_prompt_of_many_messages_is_refused_before_it_is_joined(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A prompt result is a third party's array too, and the size cap
    cannot be reached without walking it."""
    session = StubSession(
        pages=[([listed("many")], None)],
        results={"many": text_result(*["x"] * (PROMPT_MESSAGE_CAP + 1))},
    )

    with caplog.at_level(logging.WARNING, logger=MANAGER_LOGGER):
        captured = await discovered(session, with_prompts(), inject_prompts=["many"])

    assert captured == ()
    assert skips(caplog) == [("tools", "1", TOO_MANY_MESSAGES)]


def test_an_oversized_prompt_is_measured_without_being_built() -> None:
    """The unit under the cap, asked directly, because what matters is
    what it did not do: the block is refused by arithmetic over lengths
    the parsed messages already carry, so the string it would have been
    is never allocated."""
    halves = ["y" * SHIPPED_BLOCK_LIMIT, "z" * SHIPPED_BLOCK_LIMIT]

    # White-box, per the note in `discovered` above, and the docstring
    # says the rest: what matters is what was never built.
    rendering = mcp_module._rendered(text_result(*halves))

    assert rendering.text is None
    assert rendering.problem == mcp_module.TOO_LONG
    # Exact, including the blank line between the two messages, which is
    # part of what the model would have been sent.
    assert rendering.size == 2 * SHIPPED_BLOCK_LIMIT + 2


async def test_a_name_is_looked_up_exactly_as_it_was_written() -> None:
    """A published prompt's name is an identifier the server chose, so a
    configured `  spaced  ` is the prompt called `  spaced  ` and not
    the one called `spaced`. A stripping type here would have fetched a
    different prompt without saying so."""
    written = "  spaced out  "
    session = StubSession(
        pages=[([listed(written), listed(written.strip())], None)],
        results={
            written: text_result("the padded one"),
            written.strip(): text_result("the tidy one"),
        },
    )

    captured = await discovered(session, with_prompts(), inject_prompts=[written])

    assert session.fetched == [written]
    assert [prompt.text for prompt in captured] == ["the padded one"]
    assert [prompt.name for prompt in captured] == [written]


async def test_a_server_without_the_capability_skips_every_name_at_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One warning for the entry rather than one per name: the handshake
    said this server publishes no prompts at all, which is one fact
    about the entry and not three about the list."""
    session = StubSession()

    with caplog.at_level(logging.WARNING, logger=MANAGER_LOGGER):
        captured = await discovered(
            session,
            mcp.types.ServerCapabilities(),
            inject_prompts=["one", "two", "three"],
        )

    assert captured == ()
    assert session.listed == [] and session.fetched == []
    assert skips(caplog) == [("tools", "1, 2, 3", NO_PROMPTS_CAPABILITY)]


async def test_a_prompt_carrying_anything_but_text_is_unusable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    session = StubSession(
        pages=[([listed("mixed"), listed("plain")], None)],
        results={"mixed": image_result(), "plain": text_result("usable")},
    )

    with caplog.at_level(logging.WARNING, logger=MANAGER_LOGGER):
        captured = await discovered(
            session, with_prompts(), inject_prompts=["mixed", "plain"]
        )

    assert [prompt.position for prompt in captured] == [2]
    assert skips(caplog) == [("tools", "1", NON_TEXT_CONTENT)]


async def test_a_prompt_rendering_to_nothing_is_not_injected(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A heading with no words under it is not guidance."""
    session = StubSession(
        pages=[([listed("empty")], None)], results={"empty": text_result("   ")}
    )

    with caplog.at_level(logging.WARNING, logger=MANAGER_LOGGER):
        captured = await discovered(session, with_prompts(), inject_prompts=["empty"])

    assert captured == ()
    assert skips(caplog) == [("tools", "1", NOTHING_TO_INJECT)]


async def test_a_block_past_the_cap_is_skipped_whole(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Skipped rather than truncated, and the warning names the entry,
    the channel and the size: half an instruction is an instruction
    nobody reviewed."""
    huge = "g" * (SHIPPED_BLOCK_LIMIT + 1)
    session = StubSession(
        pages=[([listed("huge"), listed("small")], None)],
        results={"huge": text_result(huge), "small": text_result("short")},
    )

    with caplog.at_level(logging.WARNING, logger=MANAGER_LOGGER):
        captured = await discovered(
            session, with_prompts(), inject_prompts=["huge", "small"]
        )

    assert [prompt.text for prompt in captured] == ["short"]
    (warned,) = [
        record.getMessage() for record in caplog.records if "past the" in record.getMessage()
    ]
    assert "mcp server tools" in warned
    assert "prompt block" in warned
    assert f"{len(huge)} characters" in warned
    assert huge not in warned


async def test_a_shipped_instructions_block_past_the_cap_is_skipped_whole(
    caplog: pytest.LogCaptureFixture,
) -> None:
    huge = "i" * (SHIPPED_BLOCK_LIMIT + 1)

    with caplog.at_level(logging.WARNING, logger=MANAGER_LOGGER):
        # White-box, per the note in `discovered` above.
        assert prompts._injectable("tools", huge, "instructions") is None

    (warned,) = [record.getMessage() for record in caplog.records]
    assert "instructions block" in warned
    assert f"{len(huge)} characters" in warned
    assert huge not in warned


@pytest.fixture
def impatient(monkeypatch: pytest.MonkeyPatch) -> tuple[float, float]:
    """The two bounds, scaled down.

    What the tests below are about is which bound expired and what that
    costs, not how many seconds the shipped ones are; the shipped
    arithmetic is pinned once, on its own, by the envelope test at the
    end of this module. Scaled here so that proving a phase runs out
    does not cost a suite ten seconds of sleeping.
    """
    monkeypatch.setattr(prompts, "PROMPT_CALL_TIMEOUT_S", 0.2)
    monkeypatch.setattr(prompts, "PROMPT_DISCOVERY_TIMEOUT_S", 0.5)
    return 0.2, 0.5


async def test_a_stalled_fetch_costs_only_its_own_prompt(
    caplog: pytest.LogCaptureFixture, impatient: tuple[float, float]
) -> None:
    """A prompt that never answers is one skip, and the phase carries
    on: the connection and its tools were never at risk, and neither is
    the prompt listed after it."""
    _call, phase = impatient
    session = StubSession(
        pages=[([listed("stalled"), listed("quick")], None)],
        results={"stalled": 30.0, "quick": text_result("answered")},
    )

    with caplog.at_level(logging.WARNING, logger=MANAGER_LOGGER):
        began = time.monotonic()
        captured = await discovered(
            session, with_prompts(), inject_prompts=["stalled", "quick"]
        )
        elapsed = time.monotonic() - began

    assert [prompt.text for prompt in captured] == ["answered"]
    assert skips(caplog) == [("tools", "1", "TimeoutError")]
    assert elapsed < phase


async def test_the_phase_deadline_skips_everything_left_at_once(
    caplog: pytest.LogCaptureFixture, impatient: tuple[float, float]
) -> None:
    """Per-call bounds do not bound a phase: four prompts that each
    stall inside their own bound would otherwise cost four of them. What
    stops it is the aggregate deadline, and what it says is one line
    naming every position that is left."""
    _call, phase = impatient
    stalling = dict.fromkeys(("one", "two", "three", "four"), 30.0)
    session = StubSession(
        pages=[([listed(name) for name in stalling], None)], results=stalling
    )

    with caplog.at_level(logging.WARNING, logger=MANAGER_LOGGER):
        began = time.monotonic()
        captured = await discovered(
            session, with_prompts(), inject_prompts=list(stalling)
        )
        elapsed = time.monotonic() - began

    assert captured == ()
    assert elapsed < phase + 0.5
    # Where exactly the deadline lands between the four depends on the
    # clock, and what is under test does not: whichever positions are
    # still to come are refused together, by the phase and not one by
    # one, so the last word is the deadline's and it reaches the end of
    # the list.
    entry, positions, rule = skips(caplog)[-1]
    assert (entry, rule) == ("tools", DISCOVERY_DEADLINE)
    assert positions.endswith("4")


async def test_a_repeating_cursor_ends_at_the_page_cap(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The listing never ends and every page answers instantly, so
    neither the per-call bound nor the phase deadline sees anything
    wrong. The page cap is what makes it stop, and an unfinished listing
    is every configured name skipped, since the listing is what they are
    judged against."""
    session = StubSession(pages=[([listed("decoy")], "0")])

    with caplog.at_level(logging.WARNING, logger=MANAGER_LOGGER):
        captured = await discovered(session, with_prompts(), inject_prompts=["wanted"])

    assert captured == ()
    assert len(session.listed) == PROMPT_PAGE_CAP
    assert session.fetched == []
    assert skips(caplog) == [("tools", "1", PAGE_CAP)]


async def test_a_listing_that_fails_skips_every_configured_name(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No skip decision may rest on interpreting an untrusted server's
    error, so a listing this client could not read is every name refused
    by the same rule rather than each name refused by a guess."""
    session = StubSession()
    # White-box: a listing this client cannot read is what the rule is
    # about, and a cooperating server does not produce one.
    session._pages = []

    with caplog.at_level(logging.WARNING, logger=MANAGER_LOGGER):
        captured = await discovered(
            session, with_prompts(), inject_prompts=["one", "two"]
        )

    assert captured == ()
    assert session.fetched == []
    assert skips(caplog) == [("tools", "1, 2", "IndexError")]


async def test_an_entry_naming_no_prompts_asks_for_none() -> None:
    """Fetching costs round trips on every connect and every reconnect,
    so the default configuration makes none of them."""
    session = StubSession(pages=[([listed("house_style")], None)])

    captured = await discovered(session, with_prompts())

    assert captured == ()
    assert session.listed == [] and session.fetched == []


# What a configured name may hold, and where it may appear


POISON = "sk-test-3d7c11f9-never-a-real-credential\x1b[2J"


async def test_a_configured_name_never_reaches_a_log_record(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An MCP prompt name is a server-chosen identifier the operator
    copied, so nothing bounds what it holds: a pasted credential and a
    terminal escape are both things it may be. Every line about it names
    the entry and the position instead."""
    session = StubSession(pages=[([listed("elsewhere")], None)])

    with caplog.at_level(logging.DEBUG):
        captured = await discovered(session, with_prompts(), inject_prompts=[POISON])

    assert captured == ()
    assert skips(caplog) == [("tools", "1", NOT_LISTED)]
    assert POISON not in rendered(caplog)


async def test_a_shipped_prompt_is_carried_by_its_position_and_not_its_name(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The name travels on the captured block, since the inspection
    surface echoes operator-written configuration back; the log line
    about it carries the position."""
    session = StubSession(
        pages=[([listed(POISON)], None)], results={POISON: text_result("guidance")}
    )

    with caplog.at_level(logging.INFO):
        (captured,) = await discovered(session, with_prompts(), inject_prompts=[POISON])

    assert captured.name == POISON
    assert captured.position == 1
    assert POISON not in rendered(caplog)


def rendered(caplog: pytest.LogCaptureFixture) -> str:
    """Every record, as the container writes it: caplog's own text and
    the JSON a deployment's formatter would emit, since an extra field
    only becomes a string there."""
    return caplog.text + "".join(
        logs.JsonFormatter().format(record) for record in caplog.records
    )


# The envelope the shipped bounds add up to


def test_the_bounds_add_up_to_the_documented_envelope() -> None:
    """A manager start is one connect timeout plus one discovery
    deadline plus small change, about 20 s, and a reload's whole
    envelope grows by that same one deadline. The number that matters is
    the CLI's read timeout on a reload request: this has to stay
    comfortably inside it, or an operator's reload would answer with a
    timeout while the server was still applying it."""
    assert CONNECT_TIMEOUT_S + PROMPT_DISCOVERY_TIMEOUT_S <= 20.0
    assert (
        CONNECT_TIMEOUT_S + PROMPT_DISCOVERY_TIMEOUT_S + STOP_TIMEOUT_S
        < APPLY_READ_TIMEOUT_S
    )


async def test_a_prompt_that_never_answers_bounds_the_boot_and_the_reload() -> None:
    """The containment property end to end, at the bounds that ship.

    A real server publishes a prompt that does not answer. The boot
    finishes inside one connect timeout plus one discovery deadline, the
    reload that re-fetches finishes inside the same plus the stop it
    also does, and the tools are there throughout: the connection is the
    entry's load-bearing half, and optional guidance may not cost it.

    The slowest test in this module by design, since what it measures is
    a wait. Everything finer-grained about the bounds is proven above
    against a stub, at bounds scaled down.
    """
    before = config_with(stdio_entry(inject_prompts=["slow_guidance"]))
    after = config_with(stdio_entry(inject_prompts=["slow_guidance", "house_style"]))

    began = time.monotonic()
    servers = McpServers.build(before)
    await servers.start_all()
    booted = time.monotonic() - began
    try:
        assert booted < CONNECT_TIMEOUT_S + PROMPT_DISCOVERY_TIMEOUT_S + 2.0
        assert servers.status()["tools"]["state"] == CONNECTED
        assert "tools__secret_word" in [tool.name for tool in servers.tools_for(["tools"])]
        assert servers.guidance_for_agent("assistant") == ()

        began = time.monotonic()
        applied = (await Applying(servers, before).apply(reading(after))).mcp
        reloaded = time.monotonic() - began

        assert applied.restarted == ["tools"]
        assert reloaded < CONNECT_TIMEOUT_S + PROMPT_DISCOVERY_TIMEOUT_S + STOP_TIMEOUT_S + 2.0
        assert "tools__secret_word" in [tool.name for tool in servers.tools_for(["tools"])]
        # The prompt that answered is injected; the one that did not is
        # skipped, and it did not take the other one with it.
        assert [block.name for block in servers.guidance_for_agent("assistant")] == [
            "house_style"
        ]
    finally:
        await servers.stop_all()


def test_the_status_surface_says_nothing_about_shipped_guidance() -> None:
    """It reports connections, and the bytes a server shipped reach
    exactly two places: the model's prompt, and the surface that exists
    to show what the model was given."""
    servers = McpServers.build(config_with(stdio_entry(use_server_instructions=True)))

    assert "instructions" not in json.dumps(servers.status())
