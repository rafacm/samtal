"""The configurations a session suite is built on, and their constants.

What belongs here is a `Config` a test hands to `create_app`, the
generation holder a session builder is handed one through, and the
constants such a configuration names: the
device identity a handshake presents, the audio shapes the wire is
agreed on, the two personas' MAC addresses and tones. Nothing here
constructs a session or touches a socket, so this module imports only
`vinga_server`, the standard library and the YAML parser the loader
itself uses, which is what keeps the rest of the package free to import
it.

A builder here takes overrides rather than being copied and edited: a
suite that needs one field different asks for that field, so a change to
the shared shape reaches every suite at once.
"""

import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from vinga_server.config import Config, compose_config, load_file_config
from vinga_server.config.models import DOMAIN_KEYS
from vinga_server.config.secrets import SecretStore
from vinga_server.filler import FillerClips
from vinga_server.generation import Generation, Generations
from vinga_server.providers import ProviderWorld

# --- the device the handshake presents -------------------------------


DEVICE_MAC = "AA:BB:CC:DD:EE:FF"
DEVICE_UUID = "6f1a2b3c-4d5e-6f70-8192-a3b4c5d6e7f8"


# A board this deployment already onboarded, which is what makes a
# configuration bootable while the device under test is unbound: the
# completeness rule refuses a configuration with an agent that no device
# and no default agent reaches. Onboarding a second board is therefore
# the ordinary shape of an onboarding or binding test, not a contrivance.
BOUND_MAC = "11:22:33:44:55:01"


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


def config_with(**overrides: object) -> Config:
    """A minimal valid configuration, plus whatever the test is about."""
    base: dict[str, object] = {
        "providers": {
            "llm": {"mock": {"type": "mock"}},
            "asr": {"mock": {"type": "mock"}},
            "tts": {"mock": {"type": "mock"}},
            "vad": {"mock": {"type": "mock"}},
        },
        "agent_defaults": dict.fromkeys(("llm", "asr", "tts", "vad"), "mock"),
        "agents": {"assistant": {"prompt": "A"}},
        "default_agent": "assistant",
    }
    return Config(**(base | overrides))


def recording_config(
    tmp_path: Path,
    asr_text: str = "remember that I like tea",
    llm: dict[str, object] | None = None,
    capture: bool = False,
    prompt: str | None = None,
    **conversations: object,
) -> Config:
    """A server that records, on the database this lane provisioned, with
    its captures where a test can read them.

    The mock LLM asks for the `remember` builtin on the first round of a
    turn whose transcript carries the trigger and speaks the result on
    the second, so an ordinary websocket conversation lands a turn with a
    tool invocation under it without anything scripted below the wire.
    """
    section: dict[str, object] = {"enabled": True}
    section.update(conversations)
    server: dict[str, object] = {"conversations": section}
    if capture:
        server["capture"] = {"enabled": True, "dir": str(tmp_path / "captures")}
    return Config(
        server=server,
        providers={
            "llm": {
                "mock": llm
                or {
                    "type": "mock",
                    "reply": "Noted: {tool_result}.",
                    "tool_when": "remember",
                    "tool_name": "remember",
                    "tool_arguments": {"text": "the user likes tea"},
                }
            },
            "asr": {"mock": {"type": "mock", "text": asr_text}},
            "tts": {"mock": {"type": "mock"}},
            "vad": {"mock": {"type": "mock"}},
        },
        agents={
            "assistant": dict.fromkeys(("llm", "asr", "tts", "vad"), "mock")
            | ({} if prompt is None else {"prompt": prompt})
        },
        default_agent="assistant",
    )


def load_config_from_data(data: dict) -> Config:
    """One mapping through the whole boot composition: the server half
    written to a temporary YAML file and read by the real loader, the
    domain half composed onto it the way the database's snapshot is.

    Two halves, one call, because these tests are about what a
    configuration means rather than about where each half was kept, and
    they said the same thing when one file held both."""
    file_data = {key: value for key, value in data.items() if key not in DOMAIN_KEYS}
    domain_data = {key: value for key, value in data.items() if key in DOMAIN_KEYS}
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "config.yaml"
        path.write_text(yaml.safe_dump(file_data), encoding="utf-8")
        return compose_config(load_file_config(path), domain_data, str(path))


def world(
    config: Config,
    secrets: SecretStore | None = None,
    fillers: Mapping[str, FillerClips] | None = None,
    providers: ProviderWorld | None = None,
) -> Generations:
    """One server's generation holder, holding `config` as the world it
    serves.

    What every caller that used to be handed a `Config` is handed now:
    the session builders, the runtime factory, and anything that reads
    the configuration at a convergence point. A holder that is never
    applied to is exactly the world it was built with, which is what a
    suite about anything other than reloading wants and what it used to
    have.

    `secrets`, `fillers` and `providers` default to empty rather than
    None for the reason the composition root's do: a deployment whose
    credentials are all environment references has no stored secret, one
    where no agent masks its latency has no clip, and one shape of
    generation is easier to reason about than two. An empty set of
    engines is the honest default here rather than a built one: a
    holder is what most suites want a configuration read out of, and
    building four providers per test to be read by nothing would be a
    cost every one of them paid for the few that hold a conversation.
    Those few build the world and hand it in, which is what
    `sessions.py` does.
    """
    return Generations(
        Generation(
            config,
            secrets if secrets is not None else SecretStore(),
            fillers if fillers is not None else {},
            providers if providers is not None else ProviderWorld(),
        )
    )
