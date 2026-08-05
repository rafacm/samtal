"""Text to speech over the OpenAI speech API, streamed as raw PCM.

No SDK to add and no extra: `openai` is already a core dependency,
installed for the `openai_compatible` LLM provider, and speech is a
method on the client that already ships. One key therefore serves both
stages for a deployment already on OpenAI (#11).

`base_url` is the same door the `openai_compatible` LLM type opens:
several self-hosted speech servers implement `/v1/audio/speech`, so
pointing this type at one keeps a fully local pipeline available
through the same dialect. It defaults to OpenAI itself, and it is what
decides whether this provider sends anything off the host, which is
why the type cannot declare its own egress.

`response_format="pcm"` is the only format this stage can pass through,
and the API defines it as signed 16-bit little-endian mono at 24 kHz
with no header, which is exactly this stage's interface and the rate
devices are spoken at. It is not an option for that reason: the other
formats are containers that would have to be decoded just to be
re-encoded, costing a dependency and latency.

The request carries the reply text, so the type marks egress.
"""

import logging
from collections.abc import AsyncIterator

from openai import AsyncOpenAI, Omit

from samtal_server.config.models import ProviderConfig
from samtal_server.providers.anthropic_llm import resolve_api_key
from samtal_server.providers.base import ProviderError, TtsProvider
from samtal_server.providers.registry import OptionsReader

logger = logging.getLogger(__name__)

# What `response_format="pcm"` produces, fixed by the API.
SAMPLE_RATE = 24000

# The current speech model. `tts-1` is the older low-latency model and
# `tts-1-hd` its higher fidelity sibling; an operator who prefers one
# sets `model`.
DEFAULT_MODEL = "gpt-4o-mini-tts"

DEFAULT_BASE_URL = "https://api.openai.com/v1"

# Long enough for a slow first byte, short enough that a hung request
# does not hold a sentence open for the whole conversation. It is a
# real bound only because retries are off: see MAX_RETRIES.
DEFAULT_TIMEOUT_S = 30.0

# The SDK retries twice by default, which would make `timeout_s` a
# third of the truth: three attempts plus backoff, all of it inside one
# sentence of the serial TTS loop, with the device silent throughout.
# A voice turn has no use for that. A sentence that fails should fail
# now, so the reply handler can log it and the conversation moves on,
# rather than the user waiting a minute and a half for audio that is
# no longer wanted. The ElevenLabs provider speaks raw httpx and has
# never retried, so this also makes the two cloud voices behave alike.
MAX_RETRIES = 0

# The API's own range for `speed`.
SPEED_RANGE = (0.25, 4.0)

# The two knobs are not interchangeable and neither is universal: the
# gpt-4o speech models are steered in prose through `instructions` and
# ignore `speed`, while `tts-1` and `tts-1-hd` are the other way round.
# The API ignores the one it does not take rather than refusing it, so
# the mismatch is caught here; a knob that silently never takes effect
# is what this module's option checking exists to prevent.
_PROSE_STEERED_PREFIX = "gpt-4o"


class OpenAiTts(TtsProvider):
    # The base_url decides: a self-hosted speech server on localhost
    # keeps the reply text on the host, api.openai.com does not. Under
    # server.local_only the entry therefore needs its own explicit
    # `egress` declaration, exactly as openai_compatible does.
    egress = None

    sample_rate = SAMPLE_RATE

    def __init__(
        self,
        voice: str,
        model: str,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        instructions: str | None = None,
        speed: float | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self._voice = voice
        self._model = model
        self._instructions = instructions
        self._speed = speed
        # One client per provider entry, so its connection pool is
        # reused across sentences and sessions: a fresh TLS handshake
        # per sentence would show up as latency in the gap the user
        # hears. Providers are built at startup and live as long as the
        # server, which is also this client's lifetime.
        self._client = client or AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_s,
            max_retries=MAX_RETRIES,
        )

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        """Stream one sentence, yielding PCM as it arrives.

        Chunks are yielded sample-aligned. HTTP chunk boundaries fall
        wherever the network puts them, so a response chunk can end on
        the first byte of a sample; the odd byte is carried into the
        next chunk rather than passed on, because everything downstream
        counts samples in pairs and would shift the rest of the reply
        by one byte."""
        async with self._client.audio.speech.with_streaming_response.create(
            model=self._model,
            voice=self._voice,
            input=text,
            response_format="pcm",
            instructions=self._instructions if self._instructions else Omit(),
            speed=self._speed if self._speed is not None else Omit(),
        ) as response:
            remainder = b""
            async for chunk in response.iter_bytes():
                chunk = remainder + chunk
                aligned = len(chunk) - len(chunk) % 2
                remainder = chunk[aligned:]
                if aligned:
                    yield chunk[:aligned]
            if remainder:
                logger.warning(
                    "openai tts: dropping %d trailing byte of an incomplete sample",
                    len(remainder),
                )


def check_steering(label: str, model: str, instructions: str | None, speed: float | None) -> None:
    """Refuse the steering knob the configured model does not take."""
    prose_steered = model.startswith(_PROSE_STEERED_PREFIX)
    if speed is not None and prose_steered:
        raise ProviderError(
            f'{label}: model "{model}" ignores option "speed"; describe the pace '
            f'in "instructions" instead'
        )
    if instructions is not None and not prose_steered:
        raise ProviderError(
            f'{label}: model "{model}" ignores option "instructions"; it is read '
            f'by the {_PROSE_STEERED_PREFIX} speech models, and "speed" is what '
            f"this one takes"
        )
    low, high = SPEED_RANGE
    if speed is not None and not low <= speed <= high:
        raise ProviderError(f'{label}: option "speed" must be between {low} and {high}')


def build(label: str, config: ProviderConfig) -> OpenAiTts:
    options = OptionsReader(label, config)
    voice = options.required_string("voice")
    model = options.string("model", DEFAULT_MODEL)
    base_url = options.string("base_url", DEFAULT_BASE_URL)
    instructions = options.string("instructions")
    speed = options.optional_number("speed")
    timeout_s = options.number("timeout_s", DEFAULT_TIMEOUT_S)
    options.finish()
    assert model is not None and base_url is not None  # defaults are strings
    check_steering(label, model, instructions, speed)
    api_key = resolve_api_key(label, config.api_key_env)
    if api_key is None:
        if base_url == DEFAULT_BASE_URL:
            # OpenAI itself always needs one, and an unset variable
            # should fail the boot rather than every conversation.
            raise ProviderError(
                f'{label}: type "openai" needs an API key when it speaks to '
                f'{DEFAULT_BASE_URL}; name the environment variable holding it '
                f'with "api_key_env"'
            )
        # A self-hosted endpoint usually wants no key, but the SDK
        # insists on one, so it gets the same placeholder the
        # openai_compatible LLM type uses.
        api_key = "unused"
    return OpenAiTts(
        voice=voice,
        model=model,
        api_key=api_key,
        base_url=base_url,
        instructions=instructions,
        speed=speed,
        timeout_s=timeout_s,
    )
