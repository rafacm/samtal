"""The configurations a session suite is built on, and their constants.

What belongs here is a `Config` a test hands to `create_app` or to a
session builder, and the constants such a configuration names: the
device identity a handshake presents, the audio shapes the wire is
agreed on, the two personas' MAC addresses and tones. Nothing here
constructs a session or touches a socket, so this module imports only
`samtal_server` and the standard library, which is what keeps the rest
of the package free to import it.

A builder here takes overrides rather than being copied and edited: a
suite that needs one field different asks for that field, so a change to
the shared shape reaches every suite at once.
"""

import sys
from pathlib import Path
from typing import Any

from samtal_server.config import Config

# --- the device the handshake presents -------------------------------


DEVICE_MAC = "AA:BB:CC:DD:EE:FF"
DEVICE_UUID = "6f1a2b3c-4d5e-6f70-8192-a3b4c5d6e7f8"


DEVICE_HELLO = {
    "type": "hello",
    "version": 1,
    "features": {"mcp": True},
    "transport": "websocket",
    "audio_params": {
        "format": "opus",
        "sample_rate": 16000,
        "channels": 1,
        "frame_duration": 60,
    },
}


# --- the audio shapes and payloads -----------------------------------


SAMPLE_RATE = 16000
OUTPUT_RATE = 24000
FRAME_MS = 60
FRAME_BYTES = SAMPLE_RATE * FRAME_MS // 1000 * 2


# The silence a test sends to end an utterance in the modes that have no
# listen stop: the endpointer's 700 ms window plus a couple of frames, so
# which frame trips it does not have to be predicted exactly.
ENDPOINT_SILENCE_MS = 840


# 20 ms the mock energy endpointer classifies as speech: constant
# amplitude 10000, far over its RMS threshold of 500.
SPEECH = b"\x10\x27" * 320


# A reply the mock voice takes about eight seconds to speak, so a barge
# sent while it streams lands with most of it still unsaid.
LONG_REPLY = (
    "There is a longer answer to that, and it takes the mock voice about eight "
    "seconds to say, which leaves plenty of room for somebody to lose patience "
    "and cut in long before the end of it arrives."
)


# --- the two personas -------------------------------------------------


POET_MAC = "aa:bb:cc:dd:ee:01"
BOTH_MAC = "aa:bb:cc:dd:ee:03"
POET_TONE = 440.0
TUTOR_TONE = 880.0


# --- the configurations -----------------------------------------------


def config_with_agent(
    asr_text: str = "hello",
    llm_reply: str | None = None,
    server: dict[str, object] | None = None,
) -> Config:
    llm: dict[str, object] = {"type": "mock"}
    if llm_reply is not None:
        llm["reply"] = llm_reply
    return Config(
        server=server or {},
        providers={
            "llm": {"mock": llm},
            "asr": {"mock": {"type": "mock", "text": asr_text}},
            "tts": {"mock": {"type": "mock"}},
            "vad": {"mock": {"type": "mock"}},
        },
        agents={"assistant": dict.fromkeys(("llm", "asr", "tts", "vad"), "mock")},
        default_agent="assistant",
    )


def base_config(**overrides: object) -> Config:
    """Two agents that differ in prompt and voice, on mock providers."""
    return Config(
        **(
            {
                "providers": {
                    "llm": {"mock": {"type": "mock", "reply": "{system} heard {text}."}},
                    "asr": {"mock": {"type": "mock", "text": "hello"}},
                    "tts": {
                        "tenor": {"type": "mock", "tone_hz": POET_TONE},
                        "alto": {"type": "mock", "tone_hz": TUTOR_TONE},
                    },
                    "vad": {"mock": {"type": "mock"}},
                },
                "agent_defaults": {"llm": "mock", "asr": "mock", "vad": "mock"},
                "agents": {
                    "poet": {"prompt": "POET", "tts": "tenor"},
                    "tutor": {"prompt": "TUTOR", "tts": "alto"},
                },
                "devices": {POET_MAC: ["poet"], BOTH_MAC: ["poet", "tutor"]},
                "default_agent": "poet",
            }
            | overrides
        )
    )


STDIO_SERVER = Path(__file__).parents[1] / "support" / "mcp_stdio_server.py"


def registry_config(granted: bool) -> Config:
    """The configuration the MCP registry is built from, which is not
    the one the session was built from: the session's carries no MCP
    entries at all, so a tool that turns up in its snapshot can only
    have come from the registry."""
    return base_config(
        mcp_servers={
            "tools": {
                "transport": "stdio",
                "command": sys.executable,
                "args": [str(STDIO_SERVER)],
            }
        },
        agents={
            "poet": {"prompt": "POET", "tts": "tenor", "mcp": ["tools"] if granted else []},
            "tutor": {"prompt": "TUTOR", "tts": "alto"},
        },
    )


TIMEOUT_S = 0.05


def watchdog_config(timeout_s: float = TIMEOUT_S):
    return base_config(server={"llm_first_token_timeout_s": timeout_s})


# Test-scale threshold: well under the scripted stalls below, well over
# the near-instant mock pipeline.
DELAY_MS = 60.0


def masked_config(
    delay_ms: float = DELAY_MS, server: dict[str, object] | None = None
) -> Config:
    """The two-agent config, with a filler per agent (different phrases,
    different voices) and the mock voices trimmed so a clip is a single
    Opus frame."""
    return base_config(
        **({"server": server} if server is not None else {}),
        providers={
            "llm": {"mock": {"type": "mock", "reply": "{system} heard {text}."}},
            "asr": {"mock": {"type": "mock", "text": "hello"}},
            "tts": {
                "tenor": {
                    "type": "mock",
                    "tone_hz": POET_TONE,
                    "ms_per_char": 1,
                    "min_ms": 60,
                },
                "alto": {
                    "type": "mock",
                    "tone_hz": TUTOR_TONE,
                    "ms_per_char": 1,
                    "min_ms": 60,
                },
            },
            "vad": {"mock": {"type": "mock"}},
        },
        agents={
            "poet": {
                "prompt": "POET",
                "tts": "tenor",
                "filler": {
                    "enabled": True,
                    "delay_ms": delay_ms,
                    "phrases": ["Hmm, let me see...", "Good question..."],
                },
            },
            "tutor": {
                "prompt": "TUTOR",
                "tts": "alto",
                "filler": {
                    "enabled": True,
                    "delay_ms": delay_ms,
                    "phrases": ["Hmm, mal überlegen..."],
                },
            },
        },
    )


def capped_config(seconds: float):
    config = config_with_agent()
    config.server.limits.max_session_s = seconds
    return config


# Far enough above any idle timeout used here that it never fires
# first, near enough that a broken idle timeout ends the test in
# seconds. wait_for_close blocks until something closes the socket, so
# without a second bound a regression would hang the lane rather than
# fail it, and the close reason is what tells the two apart.
BACKSTOP_S = 10.0


def idle_config(seconds: float, **kwargs: Any):
    """A config whose idle timeout is the bound under test."""
    config = config_with_agent(**kwargs)
    config.server.limits.idle_timeout_s = seconds
    config.server.limits.max_session_s = BACKSTOP_S
    return config
