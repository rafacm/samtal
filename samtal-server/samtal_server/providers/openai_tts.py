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

from collections.abc import AsyncIterator

from openai import AsyncOpenAI, Omit

from samtal_server.config.models import ProviderConfig
from samtal_server.providers.base import ProviderCallError, ProviderError, TtsProvider
from samtal_server.providers.kit import (
    DEFAULT_TIMEOUT_S,
    MAX_RETRIES,
    OPENAI_FAILURES,
    aligned_pcm,
    call_failure,
)
from samtal_server.providers.openai_endpoint import (
    DEFAULT_BASE_URL,
    endpoint_api_key,
    endpoint_host,
    parse_base_url,
)
from samtal_server.providers.registry import OptionsReader

# How this provider names itself where the kit speaks on its behalf: the
# warning a truncated stream produces, and the message a failed request
# carries.
LABEL = "openai tts"

# What `response_format="pcm"` produces, fixed by the API.
SAMPLE_RATE = 24000

# The current speech model. `tts-1` is the older low-latency model and
# `tts-1-hd` its higher fidelity sibling; an operator who prefers one
# sets `model`.
DEFAULT_MODEL = "gpt-4o-mini-tts"

# The API's own range for `speed`.
SPEED_RANGE = (0.25, 4.0)

# The two knobs are not interchangeable and neither is universal: the
# gpt-4o speech models are steered in prose through `instructions` and
# ignore `speed`, while `tts-1` and `tts-1-hd` are the other way round.
# The API ignores the one it does not take rather than refusing it, so
# the mismatch is caught here; a knob that silently never takes effect
# is what this module's option checking exists to prevent. This is a
# fact about OpenAI's own models, so `check_steering` applies it only
# when the endpoint is OpenAI's.
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
        self.host = endpoint_host(base_url)
        self._instructions = instructions
        self._speed = speed
        # One client per provider entry, so its connection pool is
        # reused across sentences and sessions: a fresh TLS handshake
        # per sentence would show up as latency in the gap the user
        # hears. Providers are built at startup and live as long as the
        # server, which is also this client's lifetime.
        self._client = (
            client
            if client is not None
            else AsyncOpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=timeout_s,
                max_retries=MAX_RETRIES,
            )
        )

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        """Stream one sentence, yielding PCM as it arrives, sample-aligned
        by the kit's helper.

        The request and the bytes after it are both a place the endpoint
        can stop answering, and both are a failed provider call rather
        than a bug here. Cancellation, a genuine bug, and another vendor's
        SDK error are outside OPENAI_FAILURES and pass through as
        themselves, which the barge-in path depends on."""
        failure: ProviderCallError | None = None
        try:
            async with self._client.audio.speech.with_streaming_response.create(
                model=self._model,
                voice=self._voice,
                input=text,
                response_format="pcm",
                instructions=self._instructions if self._instructions else Omit(),
                speed=self._speed if self._speed is not None else Omit(),
            ) as response:
                async for chunk in aligned_pcm(LABEL, response.iter_bytes()):
                    yield chunk
        except OPENAI_FAILURES as exc:
            failure = call_failure(LABEL, exc)
        # Raised out here rather than in the except arm, so the SDK
        # exception is not even the new error's `__context__`: `from
        # None` suppresses its rendering but leaves it reachable, and
        # what it can carry is the reason the message is metadata only.
        if failure is not None:
            raise failure from None


def check_steering(
    label: str, model: str, instructions: str | None, speed: float | None, is_openai: bool
) -> None:
    """Refuse the steering knob the configured model does not take.

    Only on OpenAI itself. The rule below is a fact about OpenAI's
    models, not about the dialect: a compatible server is free to name a
    model `gpt-4o-something` and read `speed`, or to read `instructions`
    on a model named nothing like OpenAI's, and its `speed` need not
    stop at 4.0 either. Guessing on its behalf would reject working
    configurations before the request is even sent, which is worse than
    the silent-no-op this check exists to prevent, because the server
    that could answer never hears the question."""
    if not is_openai:
        return
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
    is_openai = parse_base_url(label, base_url)
    check_steering(label, model, instructions, speed, is_openai)
    api_key = endpoint_api_key(label, config.type, config.api_key_env, is_openai)
    return OpenAiTts(
        voice=voice,
        model=model,
        api_key=api_key,
        base_url=base_url,
        instructions=instructions,
        speed=speed,
        timeout_s=timeout_s,
    )
