"""Speech to text over the OpenAI transcription API.

The third of the cloud providers in issue #11, and the first that is
not a voice: multilingual accuracy better than a local `small` model,
without provisioning a GPU or downloading weights. No SDK to add and no
extra, for the reason the `openai` TTS type has none: the `openai`
client is already a core dependency, carried for the
`openai_compatible` LLM type, and transcription is a method on the
client that already ships. One key therefore serves all three stages.

`base_url` is the same door the other two `openai` types open. Several
self-hosted servers implement `/v1/audio/transcriptions`, so pointing
this type at one keeps a fully local pipeline available through the
same dialect. It defaults to OpenAI itself, and it is what decides
whether this provider sends anything off the host, which is why the
type cannot declare its own egress.

**This provider does not stream, and does not need to.** #11 asks a
network provider to stream or to justify not streaming. The stage's
interface is one whole utterance in, one whole transcript out, because
the LLM stage cannot begin on half a sentence; and the utterance is
already complete before `transcribe` is called, since the endpointer is
what decides the call happens. Streaming the response would therefore
deliver text deltas nothing downstream can consume, a turn's worth of
latency earlier than the turn can use them. The TTS stage is the
opposite case, and streams.

**This provider does not detect language, and that costs nothing it
would otherwise buy.** The model still recognises whatever is spoken
when `language` is unset; what is missing is the *report* of which
language that was, because the transcription response only carries one
for `whisper-1` asked for `verbose_json`, as an English name
("swedish") rather than the ISO code the rest of the pipeline speaks,
and no model reports a confidence at all. `AsrResult`'s language fields
are therefore left empty rather than filled with a guess, and the
session-scoped lock (`lock_language`) is never asked for. The lock
exists to spare faster-whisper a constant encoder pass per utterance
(#22); here detection happens inside the model at no measurable cost,
so there is nothing to spare.
"""

import io
import logging
import wave

from openai import AsyncOpenAI, Omit

from samtal_server.config.models import ProviderConfig
from samtal_server.providers.base import AsrProvider, AsrResult, ProviderError
from samtal_server.providers.openai_endpoint import (
    DEFAULT_BASE_URL,
    MAX_RETRIES,
    endpoint_api_key,
    parse_base_url,
)
from samtal_server.providers.registry import OptionsReader

logger = logging.getLogger(__name__)

# The current transcription family, and the reason to reach for this
# type at all: both gpt-4o models transcribe more accurately than
# `whisper-1`, which is the same Whisper V2 an operator could run
# locally. `mini` is the cheaper and faster of the pair, and the
# difference between them is small on the short utterances a voice
# assistant hears; an operator who wants the larger one sets `model`.
DEFAULT_MODEL = "gpt-4o-mini-transcribe"

# Long enough for a slow answer on a long utterance, short enough that
# a hung request does not hold a turn open for the whole conversation.
# It is a real bound only because retries are off, which is the shared
# endpoint module's MAX_RETRIES.
DEFAULT_TIMEOUT_S = 30.0

# The API's own range for `temperature`.
TEMPERATURE_RANGE = (0.0, 1.0)

# The API refuses audio shorter than this, and the barge-in path is
# what would send it: a snippet classified as speech mid-reply is
# transcribed to decide whether the interruption was real (#28), and
# the shortest of those are tens of milliseconds. An HTTP 400 there
# would be logged as a failed confirmation and suppress a barge-in that
# was never going to be confirmed anyway, so the empty answer is given
# here instead, without a round trip.
MIN_AUDIO_S = 0.1

# The API decides the format from the extension, so the name matters
# even though there is no file.
UPLOAD_NAME = "utterance.wav"


def wav_bytes(pcm: bytes, sample_rate: int) -> bytes:
    """s16le mono PCM in a WAV container.

    The endpoint takes an audio file rather than a buffer and a rate,
    and WAV is the one accepted format that holds this stage's PCM as
    it already is: a 44 byte header in front of the samples, no
    re-encoding, no dependency, and no quality lost on the way. The
    rate is written from the argument rather than assumed, so this
    provider transcribes whatever the pipeline is running at."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as container:
        container.setnchannels(1)
        container.setsampwidth(2)
        container.setframerate(sample_rate)
        container.writeframes(pcm)
    return buffer.getvalue()


class OpenAiAsr(AsrProvider):
    # The base_url decides: a self-hosted transcription server on
    # localhost keeps the audio on the host, api.openai.com does not.
    # Under server.local_only the entry therefore needs its own explicit
    # `egress` declaration, exactly as openai_compatible does.
    egress = None

    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        language: str | None = None,
        prompt: str | None = None,
        temperature: float | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self._model = model
        self._language = language
        self._prompt = prompt
        self._temperature = temperature
        # One client per provider entry, so its connection pool is
        # reused across turns and sessions: a fresh TLS handshake per
        # utterance would land squarely in the gap between the user
        # finishing their sentence and the assistant answering.
        # Providers are built at startup and live as long as the server,
        # which is also this client's lifetime.
        self._client = client or AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_s,
            max_retries=MAX_RETRIES,
        )

    async def transcribe(
        self, pcm: bytes, sample_rate: int, language_hint: str | None = None
    ) -> AsrResult:
        if len(pcm) < int(MIN_AUDIO_S * sample_rate) * 2:
            logger.debug(
                "openai asr: %d bytes is under the %.2fs minimum, nothing to transcribe",
                len(pcm),
                MIN_AUDIO_S,
            )
            return AsrResult(text="")
        # A configured language always beats the hint, as elsewhere. The
        # hint can only be a lock some provider asked for, and this one
        # never does, so in practice it is the configured language or
        # nothing.
        pinned = self._language or language_hint
        response = await self._client.audio.transcriptions.create(
            file=(UPLOAD_NAME, wav_bytes(pcm, sample_rate), "audio/wav"),
            model=self._model,
            # The one format every model and every compatible server
            # answers in. `verbose_json` is whisper-1 only, and what it
            # adds beyond the text is not usable here: see the module
            # docstring on language.
            response_format="json",
            language=pinned if pinned else Omit(),
            prompt=self._prompt if self._prompt else Omit(),
            temperature=self._temperature if self._temperature is not None else Omit(),
        )
        # Language fields stay empty: this provider does not detect.
        return AsrResult(text=response.text.strip())


def build(label: str, config: ProviderConfig) -> OpenAiAsr:
    options = OptionsReader(label, config)
    model = options.string("model", DEFAULT_MODEL)
    base_url = options.string("base_url", DEFAULT_BASE_URL)
    language = options.string("language")
    prompt = options.string("prompt")
    temperature = options.optional_number("temperature")
    timeout_s = options.number("timeout_s", DEFAULT_TIMEOUT_S)
    options.finish()
    assert model is not None and base_url is not None  # defaults are strings
    is_openai = parse_base_url(label, base_url)
    low, high = TEMPERATURE_RANGE
    # Only on OpenAI itself, for the reason the TTS type checks its
    # steering knobs only there: the range is a fact about OpenAI's
    # models, and a compatible server is free to accept another.
    if is_openai and temperature is not None and not low <= temperature <= high:
        raise ProviderError(f'{label}: option "temperature" must be between {low} and {high}')
    return OpenAiAsr(
        model=model,
        api_key=endpoint_api_key(label, config.type, config.api_key_env, is_openai),
        base_url=base_url,
        language=language,
        prompt=prompt,
        temperature=temperature,
        timeout_s=timeout_s,
    )
