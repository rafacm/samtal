"""An MCP entry's guidance reaches the agents granted it, end to end.

The issue's verification steps for the first deliverable, run against a
real server on a real port with a real device on a real websocket, and a
real MCP server spawned over stdio. What makes any of it visible from
outside is the mock LLM's `{system}` placeholder: a session's prompt is
otherwise invisible from the far end, so the reply is the prompt, spoken
back.

Three properties carry the file. Guidance follows the grant, so two
agents granted one entry both speak it and an `mcp: []` agent speaks
none of it. A shared fragment follows the include, so one block of text
written in one place reaches every agent that names it, and an edit of
it reaches them at the restart the write said it would. And the two
halves of the prompt keep their two clocks, which the held session
proves in the only place it can be proved: inside one conversation,
across a reload and an agent switch.
"""

import asyncio
import os
import re
import sys
from pathlib import Path

import httpx
from xiaozhi_sdk import XiaoZhiWebsocket

from samtal_server.config import Config
from samtal_server.config.models import API_MOUNT_PATH
from tests.integration.conftest import FRAME_BYTES, SAMPLE_RATE, speech_pcm, spoken

STDIO_SERVER = Path(__file__).parents[1] / "support" / "mcp_stdio_server.py"

ENTRY = "home"

# Short on purpose: the mock voice speaks at 40 ms a character, and the
# reply here is the whole system prompt.
GUIDANCE = "Ask first."

HOUSE_MAC = "aa:bb:cc:dd:ee:41"
KIDS_MAC = "aa:bb:cc:dd:ee:42"
QUIET_MAC = "aa:bb:cc:dd:ee:43"
HELD_MAC = "aa:bb:cc:dd:ee:44"


def stdio_entry(**overrides: object) -> dict[str, object]:
    return {
        "transport": "stdio",
        "command": sys.executable,
        "args": [str(STDIO_SERVER)],
    } | overrides


def speaks_its_prompt() -> dict[str, object]:
    """The mock model that answers with the prompt it was handed, which
    is the only view of a session's prompt from the far end."""
    return {"type": "mock", "reply": "{system}"}


def hands_over_to(agent: str) -> dict[str, object]:
    """The mock model that hands the conversation to `agent` and then
    lets the new one speak.

    It asks for the switch whenever the user's words carry the ASR's
    transcript, which is every utterance spoken to it directly. The leg
    it hands over to is greeted with the switch greeting instead, which
    carries none of those words, so the agent taking over answers rather
    than handing back.
    """
    return {
        "type": "mock",
        "reply": "{system}",
        "tool_when": "secret",
        "tool_name": "switch_agent",
        "tool_arguments": {"agent": agent},
    }


def collapsed(text: str) -> str:
    """Text with its whitespace flattened.

    A spoken reply arrives as sentences and is joined back with single
    spaces, so the blank lines the prompt is assembled with cannot
    survive the round trip through the speaker. What survives is the
    words, in order.
    """
    return re.sub(r"\s+", " ", text).strip()


def control_client(port: int) -> httpx.AsyncClient:
    """The operator's side: the configuration API of the very server the
    device is talking to, on the same port and behind the same token."""
    return httpx.AsyncClient(
        base_url=f"http://127.0.0.1:{port}{API_MOUNT_PATH}",
        headers={"Authorization": f"Bearer {os.environ['SAMTAL_API_SECRET']}"},
        timeout=60,
    )


class Device:
    """One board, connected once and spoken to more than once.

    The lane's helper is a whole conversation from connect to close;
    this is the same conversation held open, so what changes between two
    utterances is the server rather than the connection.
    """

    def __init__(self, port: int, mac: str) -> None:
        self._mac = mac
        self.events: list[dict] = []
        self._finished = asyncio.Event()
        self.client = XiaoZhiWebsocket(
            self._received,
            ota_url=f"http://127.0.0.1:{port}/xiaozhi/ota/",
            audio_sample_rate=SAMPLE_RATE,
        )

    async def _received(self, data: dict) -> None:
        self.events.append(data)
        if data.get("type") == "tts" and data.get("state") == "stop":
            self._finished.set()

    async def connect(self) -> None:
        assert await self.client.init_connection(self._mac)

    async def say_something(self) -> str:
        said = len(self.events)
        self._finished.clear()
        pcm = speech_pcm(960)
        for start in range(0, len(pcm), FRAME_BYTES):
            assert await self.client.send_audio(pcm[start : start + FRAME_BYTES])
        await self.client.send_silence_audio(1.2)
        await asyncio.wait_for(self._finished.wait(), timeout=30)
        return spoken(self.events[said:])

    async def close(self) -> None:
        await self.client.close()


# Guidance follows the grant


def granting_config() -> Config:
    """Two agents granted one entry, and one that opted out of tools,
    each on a device of its own so none of them is offered a handover."""
    return Config(
        providers={
            "llm": {"mock": speaks_its_prompt()},
            "asr": {"mock": {"type": "mock", "text": "what can you do"}},
            "tts": {"mock": {"type": "mock"}},
            "vad": {"mock": {"type": "mock"}},
        },
        mcp_servers={ENTRY: stdio_entry(instructions=GUIDANCE)},
        agent_defaults=dict.fromkeys(("llm", "asr", "tts", "vad"), "mock"),
        agents={
            "house": {"prompt": "HOUSE", "mcp": [ENTRY]},
            "kids": {"prompt": "KIDS", "mcp": [{"server": ENTRY, "tools": ["secret_word"]}]},
            "quiet": {"prompt": "QUIET", "mcp": []},
        },
        devices={HOUSE_MAC: ["house"], KIDS_MAC: ["kids"], QUIET_MAC: ["quiet"]},
        default_agent="house",
    )


async def test_every_granted_agent_speaks_the_guidance_and_the_opted_out_one_does_not(
    serve, simulate
) -> None:
    """The issue's first verification step. Two agents granted the same
    entry are both told about it, whatever each of them may reach of it:
    the second's allow list narrows its tools and not what it was told,
    which is the entry-wide rule. The third granted nothing hears
    nothing about it."""
    async with serve(granting_config()) as port:
        house, _ = await simulate(port, HOUSE_MAC)
        kids, _ = await simulate(port, KIDS_MAC)
        quiet, _ = await simulate(port, QUIET_MAC)

    assert spoken(house).startswith("HOUSE")
    assert GUIDANCE in spoken(house)
    assert f"{ENTRY}__" in spoken(house)

    assert spoken(kids).startswith("KIDS")
    assert GUIDANCE in spoken(kids)

    assert spoken(quiet).startswith("QUIET")
    assert GUIDANCE not in spoken(quiet)
    assert f"{ENTRY}__" not in spoken(quiet)


async def test_the_api_reads_back_the_prompt_the_model_was_given(
    serve_app_in, tmp_path: Path
) -> None:
    """The inspection surface over a real socket, against the one thing
    that can contradict it: what the model actually received. The two
    are assembled by the same code, and this is what says so from
    outside."""
    async with serve_app_in(tmp_path / "db", granting_config()) as (port, _), control_client(
        port
    ) as control:
        device = Device(port, HOUSE_MAC)
        await device.connect()
        try:
            said = await device.say_something()
        finally:
            await device.close()

        answered = await control.get("/runtime/agents/house/prompt")

    assert answered.status_code == 200, answered.text
    body = answered.json()
    assert [block["provenance"] for block in body["blocks"]] == [
        "persona",
        f"instructions:{ENTRY}",
    ]
    # Every block the surface reports is in what the model was given,
    # with the whitespace collapsed by the trip through the speaker,
    # which is the most a spoken reply can carry.
    for block in body["blocks"]:
        assert collapsed(block["text"]) in collapsed(said)
    # What the trip through the speaker cannot check, checked on the
    # answer itself: the prompt is the blocks joined and nothing else.
    assert body["characters"] == len(
        "\n\n".join(block["text"] for block in body["blocks"])
    )


# A shared fragment, written once and spoken by everyone who includes it


FRAGMENT = "Bins on Tuesday."

REWRITTEN = "Bins on Friday."


def sharing_config(directory: Path) -> Config:
    """Two agents that include one fragment, and one that does not, each
    on a device of its own."""
    return Config(
        server={"database": {"dir": str(directory)}},
        providers={
            "llm": {"mock": speaks_its_prompt()},
            "asr": {"mock": {"type": "mock", "text": "what can you do"}},
            "tts": {"mock": {"type": "mock"}},
            "vad": {"mock": {"type": "mock"}},
        },
        prompt_fragments={"household": {"text": FRAGMENT}},
        agent_defaults=dict.fromkeys(("llm", "asr", "tts", "vad"), "mock"),
        agents={
            "house": {"prompt": "HOUSE", "prompt_includes": ["household"]},
            "kids": {"prompt": "KIDS", "prompt_includes": ["household"]},
            "quiet": {"prompt": "QUIET"},
        },
        devices={HOUSE_MAC: ["house"], KIDS_MAC: ["kids"], QUIET_MAC: ["quiet"]},
        default_agent="house",
    )


async def test_a_fragment_written_once_is_spoken_by_every_agent_that_includes_it(
    serve_app_in, restart_in, simulate, tmp_path: Path
) -> None:
    """The issue's verification for the shared half, end to end: one
    block of text, written in one place, reaching two agents and not the
    third, and an edit of it reaching both of them at the restart the
    write said it would.
    """
    directory = tmp_path / "db"
    config = sharing_config(directory)
    async with serve_app_in(directory, config) as (port, _), control_client(port) as control:
        house, _ = await simulate(port, HOUSE_MAC)
        kids, _ = await simulate(port, KIDS_MAC)
        quiet, _ = await simulate(port, QUIET_MAC)

        assert spoken(house).startswith("HOUSE")
        assert FRAGMENT in spoken(house)
        assert spoken(kids).startswith("KIDS")
        assert FRAGMENT in spoken(kids)
        assert spoken(quiet).startswith("QUIET")
        assert FRAGMENT not in spoken(quiet)

        # The surface counts the block it injected, over the same socket
        # the device is on.
        preview = await control.get("/runtime/agents/house/prompt")
        assert preview.status_code == 200, preview.text
        blocks = {block["provenance"]: block for block in preview.json()["blocks"]}
        assert blocks["fragment:household"]["text"] == FRAGMENT
        assert blocks["fragment:household"]["characters"] == len(FRAGMENT)

        written = await control.put(
            "/prompt-fragments/household", json={"text": REWRITTEN}
        )
        assert written.status_code == 200, written.text
        assert "next server start" in written.json()["notice"]

        # This server read the fragment at boot and is still holding it,
        # which is what the notice says.
        during, _ = await simulate(port, HOUSE_MAC)
        assert FRAGMENT in spoken(during)
        assert REWRITTEN not in spoken(during)

    # The restart the write named, on the database the write landed in.
    async with restart_in(directory, config) as (port, _):
        house, _ = await simulate(port, HOUSE_MAC)
        kids, _ = await simulate(port, KIDS_MAC)

    assert REWRITTEN in spoken(house)
    assert REWRITTEN in spoken(kids)
    assert FRAGMENT not in spoken(house)


# The two clocks, in one held session


def held_config(directory: Path) -> Config:
    """Three agents on one device, chained: the first hands over to the
    second and the second to the third, which answers for itself.

    The chain is what lets one session show both clocks. An agent
    scripted to hand over does so on every utterance spoken to it, so
    the activations of a session are a fixed sequence, and the last
    agent is the one that answers twice: once when it is switched in,
    and once with the half it cached then.
    """
    return Config(
        memory={"dir": str(directory)},
        providers={
            "llm": {
                "to-beta": hands_over_to("beta"),
                "to-gamma": hands_over_to("gamma"),
                "plain": speaks_its_prompt(),
            },
            "asr": {"mock": {"type": "mock", "text": "tell me the secret"}},
            "tts": {"mock": {"type": "mock"}},
            "vad": {"mock": {"type": "mock"}},
        },
        mcp_servers={ENTRY: stdio_entry(instructions=GUIDANCE)},
        agent_defaults=dict.fromkeys(("asr", "tts", "vad"), "mock") | {"mcp": [ENTRY]},
        agents={
            "alpha": {"prompt": "ALPHA", "llm": "to-beta"},
            "beta": {"prompt": "BETA", "llm": "to-gamma"},
            "gamma": {"prompt": "GAMMA", "llm": "plain"},
        },
        devices={HELD_MAC: ["alpha", "beta", "gamma"]},
        default_agent="alpha",
    )


async def rewrite_guidance(control: httpx.AsyncClient, text: str) -> None:
    """The operator's edit and the reload that applies it.

    The reload reports the entry as `unchanged`, which is the
    connection-identity rule seen from outside: guidance is prompt text
    the connection never sees, so applying it keeps the connection that
    is up.
    """
    written = await control.put(f"/mcp-servers/{ENTRY}", json=stdio_entry(instructions=text))
    assert written.status_code == 200, written.text
    applied = await control.post("/runtime/mcp-servers/reload")
    assert applied.status_code == 200, applied.text
    assert applied.json()["unchanged"] == [ENTRY]
    assert applied.json()["restarted"] == []
    assert applied.json()["servers"][ENTRY]["state"] == "connected"


async def test_one_session_across_a_reload_a_switch_and_a_memory_write(
    serve_app_in, tmp_path: Path
) -> None:
    """The clocks, proven where they can only be proven: inside one
    conversation on one socket.

    A switch re-assembles the know-how half, so an agent switched in
    after a reload speaks the guidance the reload applied. A session
    already holding a half keeps it, so a later reload's guidance is not
    in its next reply. And memory keeps the clock it always had, so a
    fact written by something else while this conversation is running is
    in that same reply.
    """
    fact = "the user is vegetarian"
    async with serve_app_in(tmp_path / "db", held_config(tmp_path / "memory")) as (
        port,
        app,
    ), control_client(port) as control:
        device = Device(port, HELD_MAC)
        await device.connect()
        session = device.client.session_id
        try:
            # Alpha hands over to beta, which answers with the guidance
            # as it stands.
            first = await device.say_something()
            assert first.startswith("BETA")
            assert GUIDANCE in first

            await rewrite_guidance(control, "Ask twice.")

            # Beta hands over to gamma, which is activated now: its half
            # is assembled after the reload and carries what the reload
            # applied.
            second = await device.say_something()
            assert second.startswith("GAMMA")
            assert "Ask twice." in second
            assert GUIDANCE not in second

            # A memory write from outside this conversation, which is
            # what a concurrent session would do, and a second reload
            # gamma must not see.
            await app.state.memory.remember("gamma", fact)
            await rewrite_guidance(control, "Ask three times.")

            third = await device.say_something()

            # The cached half is untouched by the reload: gamma still
            # speaks the guidance it was activated with.
            assert third.startswith("GAMMA")
            assert "Ask twice." in third
            assert "Ask three times." not in third
            # And the memory block is not cached with it: the fact
            # written between the two replies is in this one, which is
            # the contract this feature deliberately did not move.
            assert fact in third
            assert fact not in second

            # One socket and one session throughout: the switches were
            # handovers inside this conversation, not reconnections.
            assert device.client.session_id == session
            assert len(app.state.sessions) == 1
        finally:
            await device.close()

        # And a session opening now, which the surface previews, would
        # get what the running one did not.
        preview = await control.get("/runtime/agents/gamma/prompt")
        assert preview.status_code == 200, preview.text
        blocks = {block["provenance"]: block["text"] for block in preview.json()["blocks"]}
        assert "Ask three times." in blocks[f"instructions:{ENTRY}"]
        assert fact in blocks["memory"]
