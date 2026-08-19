"""Two real personas on real engines, on the desk, not in CI.

The mock lane proves the routing; this proves it is worth having. One
server runs two agents that share Silero, faster-whisper, and the local
LLM through `agent_defaults` and differ in prompt and Piper voice. Two
devices ask the same spoken question and are answered differently and in
different voices. Run it with:

    VINGA_LOCAL_LANE=1 uv run pytest tests/local -q

The first run downloads the second Piper voice at server startup.
"""

import asyncio

import numpy as np
import pytest
from xiaozhi_sdk import XiaoZhiWebsocket

from vinga_server.config import Config

POET_MAC = "aa:bb:cc:dd:ee:03"
GUIDE_MAC = "aa:bb:cc:dd:ee:04"

SAMPLE_RATE = 16000
FRAME_BYTES = SAMPLE_RATE * 60 // 1000 * 2

QUESTION = "What is the capital of Sweden?"

# Two voices that differ in more than accent: a male and a female voice,
# so "distinct" is audible and measurable rather than a matter of taste.
POET_VOICE = "en_US-lessac-medium"
GUIDE_VOICE = "en_US-amy-medium"

POET_PROMPT = (
    "You are a poet. Answer in exactly one short rhyming couplet, no more "
    "than twenty words, plain and speakable. Never explain yourself."
)
GUIDE_PROMPT = (
    "You are a terse travel guide. Answer in one short plain sentence of "
    "fact, no more than fifteen words. No poetry, no lists, no markdown."
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
            "tts": {
                "poet_voice": {"type": "piper", "voice": POET_VOICE},
                "guide_voice": {"type": "piper", "voice": GUIDE_VOICE},
            },
            "vad": {"silero": {"type": "silero"}},
        },
        # One LLM, one whisper, one Silero for both personas: the heavy
        # half is shared, and only prompt and voice differ.
        agent_defaults={"llm": "local", "asr": "whisper", "vad": "silero"},
        agents={
            "poet": {"prompt": POET_PROMPT, "tts": "poet_voice"},
            "guide": {"prompt": GUIDE_PROMPT, "tts": "guide_voice"},
        },
        devices={POET_MAC: ["poet"], GUIDE_MAC: ["guide"]},
        default_agent="guide",
    )
    async with serve(config) as port:
        yield port


async def converse(port: int, mac: str, pcm: bytes) -> tuple[str, str, np.ndarray]:
    """Ask one device the spoken question; return what the server heard,
    what it said, and the audio it said it in."""
    events: list[dict] = []
    reply_finished = asyncio.Event()

    async def on_message(data: dict) -> None:
        events.append(data)
        if data.get("type") == "tts" and data.get("state") == "stop":
            reply_finished.set()

    client = XiaoZhiWebsocket(
        on_message,
        ota_url=f"http://127.0.0.1:{port}/xiaozhi/ota/",
        audio_sample_rate=SAMPLE_RATE,
    )
    try:
        assert await client.init_connection(mac)
        for start in range(0, len(pcm), FRAME_BYTES):
            assert await client.send_audio(pcm[start : start + FRAME_BYTES])
        await client.send_silence_audio(1.5)
        await asyncio.wait_for(reply_finished.wait(), timeout=180)
        await asyncio.sleep(0.3)
        chunks = list(client.output_audio_queue)
    finally:
        await client.close()

    (stt,) = [e for e in events if e.get("type") == "stt"]
    spoken = " ".join(
        e["text"] for e in events if e.get("type") == "tts" and e["state"] == "sentence_start"
    )
    audio = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.int16)
    return stt["text"], spoken, audio


def timbre(audio: np.ndarray) -> np.ndarray:
    """A coarse fingerprint of a voice: the long-term average log spectrum
    of the parts that are not silence, normalized. Two Piper voices
    speaking the same words sit far apart on it, while the same voice
    reaching the same words twice sits on top of itself, which is what
    makes it usable for telling which voice spoke a reply."""
    window = 512
    frames = [
        np.abs(np.fft.rfft(audio[start : start + window].astype(np.float64) * np.hanning(window)))
        for start in range(0, audio.size - window, window // 2)
        if np.sqrt(np.mean(audio[start : start + window].astype(np.float64) ** 2)) >= 300
    ]
    assert frames, "no audible audio to fingerprint"
    average = np.log(np.mean(frames, axis=0) + 1e-6)
    average -= average.mean()
    return average / np.linalg.norm(average)


def timbre_distance(heard: np.ndarray, reference: np.ndarray) -> float:
    return 1 - float(np.dot(heard, reference))


async def test_two_devices_get_two_real_personas(
    server_port: int, local_lane, speak, conversation_report: list[str]
) -> None:
    pcm = speak(QUESTION, POET_VOICE, SAMPLE_RATE)
    # Sequentially: two real conversations sharing one whisper and one
    # local LLM, which is not a throughput test.
    poet_heard, poet_said, poet_audio = await converse(server_port, POET_MAC, pcm)
    guide_heard, guide_said, guide_audio = await converse(server_port, GUIDE_MAC, pcm)

    conversation_report.extend(
        [
            f"pipeline: silero + faster-whisper small + {local_lane.model}, "
            f"{POET_VOICE} and {GUIDE_VOICE}",
            f'question: "{QUESTION}" (spoken to both devices)',
            f'poet  heard "{poet_heard}" said "{poet_said}" '
            f"({poet_audio.size / SAMPLE_RATE:.1f} s of audio)",
            f'guide heard "{guide_heard}" said "{guide_said}" '
            f"({guide_audio.size / SAMPLE_RATE:.1f} s of audio)",
        ]
    )

    # Both heard the same question and answered it.
    assert "capital of sweden" in poet_heard.lower()
    assert "capital of sweden" in guide_heard.lower()
    assert "stockholm" in poet_said.lower(), f"poet said: {poet_said!r}"
    assert "stockholm" in guide_said.lower(), f"guide said: {guide_said!r}"

    # But as two different personas: different words, and each in its own
    # voice. The voice is identified by re-speaking what the device
    # actually heard in both configured voices and asking which of the two
    # the received audio resembles, so the comparison is between the same
    # words every time and only the voice differs.
    assert poet_said != guide_said
    for agent, said, audio, own_voice, other_voice in (
        ("poet", poet_said, poet_audio, POET_VOICE, GUIDE_VOICE),
        ("guide", guide_said, guide_audio, GUIDE_VOICE, POET_VOICE),
    ):
        heard = timbre(audio)
        to_own = timbre_distance(heard, timbre(reference(speak, said, own_voice)))
        to_other = timbre_distance(heard, timbre(reference(speak, said, other_voice)))
        conversation_report.append(
            f"{agent:5s} voice : {own_voice} at {to_own:.3f}, "
            f"{other_voice} at {to_other:.3f}"
        )
        assert to_own < to_other, (
            f"the {agent} was answered in the wrong voice: {to_own:.3f} from "
            f"{own_voice} but {to_other:.3f} from {other_voice}"
        )


def reference(speak, text: str, voice: str) -> np.ndarray:
    """What that reply would sound like in a given voice."""
    return np.frombuffer(speak(text, voice, SAMPLE_RATE), dtype=np.int16)
