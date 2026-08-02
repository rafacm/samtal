"""A real local model deciding to call a tool, on the desk, not in CI.

The scripted mock in the integration lane proves the plumbing: the
server routes a call, the tool answers, the answer reaches the reply.
What it cannot prove is that a real model, given these tool
definitions, works out that it should call one. That is what this does,
against the same stdio MCP server the integration lane spawns, through
Ollama. Run it with:

    SAMTAL_LOCAL_LANE=1 uv run pytest tests/local -q

Not every Ollama model supports tool calling, so the pre-flight below
insists on one that does and says how to name another.
"""

import asyncio
import json
import math
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
import pytest
from xiaozhi_sdk import XiaoZhiWebsocket

from samtal_server.config import Config

DEVICE_MAC = "aa:bb:cc:dd:ee:04"
SAMPLE_RATE = 16000
FRAME_BYTES = SAMPLE_RATE * 60 // 1000 * 2

STDIO_SERVER = Path(__file__).parents[1] / "support" / "mcp_stdio_server.py"

TOOL_MODEL_ENV = "SAMTAL_LOCAL_TOOL_MODEL"
PREFERRED_TOOL_MODEL = "qwen3:8b"

QUESTION = "Ask the tool for the secret word, then tell me what it is."
VOICE = "en_US-lessac-medium"

PROMPT = (
    "You are a helpful voice assistant with tools. Keep replies short, plain, "
    "and speakable: one or two sentences, no lists, no markdown. When a tool "
    "can answer the user, call it and then say what it answered. Always reply "
    "in the language the user spoke."
)


def _capabilities(base_url: str, model: str) -> list[str]:
    """What Ollama says a model can do, via its native show API."""
    show_url = base_url.removesuffix("/v1") + "/api/show"
    request = urllib.request.Request(
        show_url,
        data=json.dumps({"model": model}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return list(json.load(response).get("capabilities") or [])


@pytest.fixture(scope="session")
def tool_model(local_lane) -> str:
    """A model that can actually call tools. The lane's default model is
    used when it can; otherwise the run fails loudly with the variable
    that names another, because a silently skipped acceptance run checks
    nothing."""
    named = os.environ.get(TOOL_MODEL_ENV)
    candidates = [named] if named else [local_lane.model, PREFERRED_TOOL_MODEL]
    problems: list[str] = []
    for candidate in dict.fromkeys(candidates):
        try:
            capabilities = _capabilities(local_lane.base_url, candidate)
        except (OSError, urllib.error.HTTPError, ValueError) as exc:
            problems.append(f'Ollama could not describe "{candidate}": {exc}')
            continue
        if "tools" in capabilities:
            return candidate
        problems.append(f'"{candidate}" does not support tool calling ({capabilities})')
    pytest.fail(
        "the tool-calling lane needs a tool-capable model:\n- "
        + "\n- ".join(problems)
        + f"\nName one with {TOOL_MODEL_ENV}, for example "
        f"{TOOL_MODEL_ENV}={PREFERRED_TOOL_MODEL}",
        pytrace=False,
    )


@pytest.fixture
async def server_port(local_lane, tool_model: str, serve):
    config = Config(
        providers={
            "llm": {
                "local": {
                    "type": "openai_compatible",
                    "base_url": local_lane.base_url,
                    "model": tool_model,
                }
            },
            "asr": {"whisper": {"type": "faster_whisper", "model": "small", "language": "en"}},
            "tts": {"piper": {"type": "piper", "voice": VOICE}},
            "vad": {"silero": {"type": "silero"}},
        },
        mcp_servers={
            "tools": {
                "transport": "stdio",
                "command": sys.executable,
                "args": [str(STDIO_SERVER)],
                "tool_timeout_s": 30,
            }
        },
        agent_defaults={"llm": "local", "asr": "whisper", "tts": "piper", "vad": "silero"},
        agents={"assistant": {"prompt": PROMPT, "mcp": ["tools"]}},
        default_agent="assistant",
    )
    async with serve(config) as port:
        yield port


async def test_a_real_model_calls_the_tool_and_says_what_it_answered(
    server_port: int, tool_model: str, speak, conversation_report: list[str]
) -> None:
    events: list[dict] = []
    reply_finished = asyncio.Event()

    async def on_message(data: dict) -> None:
        events.append(data)
        if data.get("type") == "tts" and data.get("state") == "stop":
            reply_finished.set()

    client = XiaoZhiWebsocket(
        on_message,
        ota_url=f"http://127.0.0.1:{server_port}/xiaozhi/ota/",
        audio_sample_rate=SAMPLE_RATE,
    )
    try:
        assert await client.init_connection(DEVICE_MAC)
        pcm = speak(QUESTION, VOICE, SAMPLE_RATE)
        for start in range(0, len(pcm), FRAME_BYTES):
            assert await client.send_audio(pcm[start : start + FRAME_BYTES])
        await client.send_silence_audio(1.5)
        await asyncio.wait_for(reply_finished.wait(), timeout=240)
        await asyncio.sleep(0.3)
        chunks = list(client.output_audio_queue)
    finally:
        await client.close()

    (stt,) = [event for event in events if event.get("type") == "stt"]
    assert "secret word" in stt["text"].lower()

    spoken = " ".join(
        event["text"]
        for event in events
        if event.get("type") == "tts" and event["state"] == "sentence_start"
    )
    # The secret word exists nowhere but inside the tool, so a reply
    # that names it is a reply that called it.
    assert "rhubarb" in spoken.lower(), f"reply was: {spoken!r}"

    audio = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.int16)
    assert audio.size > SAMPLE_RATE / 2, "less than half a second of reply audio"
    assert math.sqrt(float(np.mean(audio.astype(np.float64) ** 2))) > 300

    conversation_report.extend(
        [
            f"tools   : stdio MCP server on {tool_model}",
            f'asked   : "{QUESTION}"',
            f'heard   : "{stt["text"]}"',
            f'reply   : "{spoken}"',
        ]
    )
