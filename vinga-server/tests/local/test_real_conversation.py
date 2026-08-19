"""One real conversation through real engines, on the desk, not in CI.

The lane starts a real server on the fully local pipeline (Silero +
faster-whisper + Ollama + Piper), synthesizes a spoken question with
Piper, drives it through the xiaozhi-sdk simulator, and asserts the
transcript came back right and the reply is coherent (it names
Stockholm). Run it with:

    VINGA_LOCAL_LANE=1 uv run pytest tests/local -q

The first run downloads the whisper model and the Piper voice at
server startup, which can take a few minutes; later runs start in
seconds. The whole conversation takes tens of seconds: the reply is
paced at the frame cadence, and the LLM generates at local speed.
"""

import asyncio
import math

import numpy as np
import pytest
from xiaozhi_sdk import XiaoZhiWebsocket

from vinga_server.config import Config

DEVICE_MAC = "aa:bb:cc:dd:ee:02"
SAMPLE_RATE = 16000
FRAME_BYTES = SAMPLE_RATE * 60 // 1000 * 2

QUESTION = "What is the capital of Sweden?"
VOICE = "en_US-lessac-medium"

PROMPT = (
    "You are a helpful voice assistant. Keep replies short, plain, and "
    "speakable: one or two sentences, no lists, no markdown. Always "
    "reply in the language the user spoke."
)


@pytest.fixture
async def server_port(local_lane, serve):
    config = Config(
        providers={
            "llm": {
                "local": {
                    "type": "openai_compatible",
                    "base_url": local_lane.base_url,
                    "model": local_lane.model,
                }
            },
            "asr": {"whisper": {"type": "faster_whisper", "model": "small", "language": "en"}},
            "tts": {"piper": {"type": "piper", "voice": VOICE}},
            "vad": {"silero": {"type": "silero"}},
        },
        agents={
            "assistant": {
                "prompt": PROMPT,
                "llm": "local",
                "asr": "whisper",
                "tts": "piper",
                "vad": "silero",
            }
        },
        default_agent="assistant",
    )
    # Serving builds the providers: this is where model and voice
    # downloads happen on a first run.
    async with serve(config) as port:
        yield port


async def test_a_real_conversation_gets_a_coherent_spoken_reply(
    server_port: int, local_lane, speak, conversation_report: list[str]
) -> None:
    events: list[dict] = []
    arrived_at: dict[int, float] = {}
    reply_finished = asyncio.Event()
    loop = asyncio.get_running_loop()

    async def on_message(data: dict) -> None:
        arrived_at[id(data)] = loop.time()
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
        question_done = loop.time()
        await client.send_silence_audio(1.5)
        await asyncio.wait_for(reply_finished.wait(), timeout=180)
        await asyncio.sleep(0.3)
        chunks = list(client.output_audio_queue)
    finally:
        await client.close()

    # Whisper heard the question.
    (stt,) = [e for e in events if e.get("type") == "stt"]
    assert "capital of sweden" in stt["text"].lower()

    # The LLM's reply is coherent: it names the answer.
    sentences = [
        e for e in events if e.get("type") == "tts" and e["state"] == "sentence_start"
    ]
    spoken = " ".join(e["text"] for e in sentences)
    assert "stockholm" in spoken.lower(), f"reply was: {spoken!r}"

    # And Piper actually spoke it.
    audio = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.int16)
    assert audio.size > SAMPLE_RATE / 2, "less than half a second of reply audio"
    assert math.sqrt(float(np.mean(audio.astype(np.float64) ** 2))) > 300

    # Timings are measured from the end of the spoken question; the
    # first ~0.7 s of each is the endpointer's trailing-silence window.
    conversation_report.extend(
        [
            f"pipeline: silero + faster-whisper small + {local_lane.model} + {VOICE}",
            f'question: "{QUESTION}" ({len(pcm) / 2 / SAMPLE_RATE:.1f} s of audio)',
            f'heard   : "{stt["text"]}" (+{arrived_at[id(stt)] - question_done:.1f} s)',
            f'reply   : "{spoken}" '
            f"(first sentence +{arrived_at[id(sentences[0])] - question_done:.1f} s, "
            f"{audio.size / SAMPLE_RATE:.1f} s of audio)",
        ]
    )
