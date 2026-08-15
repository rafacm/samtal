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

import asyncio
import io
import logging
import wave

from openai import NOT_GIVEN, APITimeoutError, AsyncOpenAI, Omit

from samtal_server.config.models import ProviderConfig
from samtal_server.providers.base import (
    AsrProvider,
    AsrResult,
    ProviderCallError,
    ProviderError,
)
from samtal_server.providers.kit import (
    DEFAULT_TIMEOUT_S,
    MAX_RETRIES,
    OPENAI_FAILURES,
    call_failure,
)
from samtal_server.providers.openai_endpoint import (
    DEFAULT_BASE_URL,
    endpoint_api_key,
    endpoint_host,
    parse_base_url,
)
from samtal_server.providers.registry import OptionsReader

logger = logging.getLogger(__name__)

# How this provider names itself in the message a failed request
# carries.
LABEL = "openai asr"

# The current transcription family, and the reason to reach for this
# type at all: both gpt-4o models transcribe more accurately than
# `whisper-1`, which is the same Whisper V2 an operator could run
# locally. `mini` is the cheaper and faster of the pair, and the
# difference between them is small on the short utterances a voice
# assistant hears; an operator who wants the larger one sets `model`.
DEFAULT_MODEL = "gpt-4o-mini-transcribe"

# The API's own range for `temperature`.
TEMPERATURE_RANGE = (0.0, 1.0)

# OpenAI refuses audio shorter than this, and the barge-in path is what
# would send it: a snippet classified as speech mid-reply is
# transcribed to decide whether the interruption was real (#28), and
# the shortest of those are tens of milliseconds. An HTTP 400 there
# would be logged as a failed confirmation and suppress a barge-in that
# was never going to be confirmed anyway, so the empty answer is given
# here instead, without a round trip.
#
# Measured against OpenAI's endpoint, so it is applied only there. A
# compatible server may accept shorter audio, and suppressing a clip it
# would have answered would silently drop a barge-in it could have
# confirmed; that endpoint decides its own minimum, the same way it
# decides its own model rules and its own temperature range.
MIN_AUDIO_S = 0.1

# The API decides the format from the extension, so the name matters
# even though there is no file.
UPLOAD_NAME = "utterance.wav"

# The least of the shared timeout worth spending on the echo retry.
# The retry answers to the same timeout_s as the request that tripped
# the guard (see transcribe), so a first request that ate nearly the
# whole budget leaves the retry more likely to be cut off than to
# answer: field round trips on the short clips that echo ran about
# half a second, so under a second of remaining budget the retry
# would mostly buy a longer wait for the same discard.
RETRY_FLOOR_S = 1.0

# Sentence-final punctuation an echoed prompt can come back wearing:
# the model is transcribing, so it writes what it returns as a
# sentence. Measured, and rare rather than theoretical: of 45 echoes
# provoked against `gpt-4o-mini-transcribe`, 44 were the prompt exactly
# (in either case) and one carried a trailing full stop. Ignoring it
# would leave the fix passing a spurious utterance through once in
# tens of turns, which is the frequency the field report itself had.
TRAILING = ".!?…。！？"


def _normalized(text: str) -> str:
    """A transcript reduced to what makes it the same words as another:
    surrounding space, sentence-final punctuation, and case."""
    return text.strip().rstrip(TRAILING).strip().casefold()


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
        min_audio_s: float = MIN_AUDIO_S,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self._model = model
        self.host = endpoint_host(base_url)
        self._language = language
        self._prompt = prompt
        self._temperature = temperature
        # Kept for the echo retry's deadline: the whole transcribe call,
        # retry included, answers to this one budget (see transcribe).
        self._timeout_s = timeout_s
        # Shortest audio worth sending. The endpoint sets it, so a
        # compatible one that accepts anything gets 0: see MIN_AUDIO_S.
        self._min_audio_s = min_audio_s
        # One client per provider entry, so its connection pool is
        # reused across turns and sessions: a fresh TLS handshake per
        # utterance would land squarely in the gap between the user
        # finishing their sentence and the assistant answering.
        # Providers are built at startup and live as long as the server,
        # which is also this client's lifetime.
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

    async def transcribe(
        self, pcm: bytes, sample_rate: int, language_hint: str | None = None
    ) -> AsrResult:
        if not pcm or len(pcm) < int(self._min_audio_s * sample_rate) * 2:
            logger.debug(
                "openai asr: %d bytes is under the %.2fs minimum, nothing to transcribe",
                len(pcm),
                self._min_audio_s,
            )
            return AsrResult(text="")
        # A configured language always beats the hint, as elsewhere. The
        # hint can only be a lock some provider asked for, and this one
        # never does, so in practice it is the configured language or
        # nothing.
        pinned = self._language or language_hint
        # One deadline for the whole call, echo retry included. The
        # client's retries are off (MAX_RETRIES) precisely so timeout_s
        # bounds what a user can be left waiting on one utterance, and
        # a retry with a fresh timeout of its own would quietly double
        # that bound.
        deadline = asyncio.get_running_loop().time() + self._timeout_s
        # The taxonomy applies to the failure of this call as a whole,
        # not to every request inside it: the echo retry below
        # deliberately converts its own timeout into an empty transcript
        # and an asr_prompt_echo event, and that discard is a decision
        # rather than a failure. What is left here is a call that could
        # not answer at all, which is a failed provider call.
        # Cancellation, a genuine bug, and another vendor's SDK error are
        # outside OPENAI_FAILURES and pass through as themselves.
        failure: ProviderCallError | None = None
        try:
            text = await self._request(pcm, sample_rate, pinned, self._prompt)
            if self._is_echoed_prompt(text):
                text = await self._retry_without_prompt(pcm, sample_rate, pinned, deadline)
        except OPENAI_FAILURES as exc:
            failure = call_failure(LABEL, exc)
        # Raised out here rather than in the except arm, so the SDK
        # exception is not even the new error's `__context__`: `from
        # None` suppresses its rendering but leaves it reachable, and
        # what it can carry is the reason the message is metadata only.
        if failure is not None:
            raise failure from None
        # Language fields stay empty: this provider does not detect.
        return AsrResult(text=text)

    async def _request(
        self,
        pcm: bytes,
        sample_rate: int,
        pinned: str | None,
        prompt: str | None,
        timeout_s: float | None = None,
    ) -> str:
        response = await self._client.audio.transcriptions.create(
            file=(UPLOAD_NAME, wav_bytes(pcm, sample_rate), "audio/wav"),
            model=self._model,
            # The one format every model and every compatible server
            # answers in. `verbose_json` is whisper-1 only, and what it
            # adds beyond the text is not usable here: see the module
            # docstring on language.
            response_format="json",
            language=pinned if pinned else Omit(),
            prompt=prompt if prompt else Omit(),
            temperature=self._temperature if self._temperature is not None else Omit(),
            # The client's own timeout, unless this request is a retry
            # living on what the first request left of it. NOT_GIVEN,
            # not Omit(): Omit is a serialization sentinel for request
            # fields, and timeout is a client option, so an Omit here
            # flows through the SDK into httpx as the literal connect
            # timeout and fails every call at connect time (#75).
            timeout=timeout_s if timeout_s is not None else NOT_GIVEN,
        )
        return response.text.strip()

    async def _retry_without_prompt(
        self, pcm: bytes, sample_rate: int, pinned: str | None, deadline: float
    ) -> str:
        """A second hearing for a clip whose transcript was the prompt
        handed back.

        The guard used to treat the echo as proof of silence, and the
        field data says it is not: nine echoes in two days of testing,
        every one on a clip of 0.78 to 1.92 s, two of them a user
        answering "yes, please" and being ignored (#69). Short
        acknowledgements are exactly the clips the model is most likely
        to echo the prompt on, so the same audio is transcribed once
        more with the prompt withheld: real speech transcribes fine
        without the prompt's help, and real silence comes back empty
        (or as another hallucination the guard still catches). Only
        this suspicious path pays the second round trip, at roughly the
        cost of the first one; the normal path is one request, as
        before.

        The retry lives on what the first request left of the shared
        deadline, and is skipped outright when less than RETRY_FLOOR_S
        remains: timeout_s is the bound on what a user can be left
        waiting, and a retry outliving it would break that promise. A
        retry the deadline cuts off is the discarding outcome rather
        than an error, since the reply it would have ended is one the
        guard was about to end anyway.

        The #54 rationale stands: an exact echo of the prompt is never
        handed to the session as an utterance, retried or not."""
        duration_s = round(len(pcm) / 2 / sample_rate, 2)
        loop = asyncio.get_running_loop()
        remaining_s = deadline - loop.time()
        if remaining_s < RETRY_FLOOR_S:
            logger.warning(
                "openai asr: the transcript came back as the configured prompt "
                "with %.1f s of the timeout left, too little to retry, "
                "treating %.2f s of audio as nothing said",
                remaining_s,
                duration_s,
                extra=self._echo_event("skipped", duration_s),
            )
            return ""
        logger.warning(
            "openai asr: the transcript came back as the configured prompt, "
            "retrying %.2f s of audio without it",
            duration_s,
        )
        started = loop.time()
        try:
            # The asyncio deadline is what makes the budget absolute.
            # The SDK's timeout argument is an httpx timeout, which is
            # per phase: remaining_s passed there alone would let the
            # retry spend that long on each of connect, write and read,
            # exceeding the shared budget end to end. The per-request
            # override is still passed as belt and braces, so the
            # request machinery gives up on its own where it can rather
            # than being cancelled mid-phase.
            async with asyncio.timeout(remaining_s):
                retry = await self._request(
                    pcm, sample_rate, pinned, None, timeout_s=remaining_s
                )
        except (TimeoutError, APITimeoutError):
            retry_ms = round((loop.time() - started) * 1000)
            logger.warning(
                "openai asr: the retry outran the timeout's remaining %.1f s, "
                "treating %.2f s of audio as nothing said",
                remaining_s,
                duration_s,
                extra=self._echo_event("timed_out", duration_s, retry_ms),
            )
            return ""
        retry_ms = round((loop.time() - started) * 1000)
        if self._is_echoed_prompt(retry):
            logger.warning(
                "openai asr: the retry came back as the prompt again, "
                "treating %.2f s of audio as nothing said",
                duration_s,
                extra=self._echo_event("confirmed_echo", duration_s, retry_ms),
            )
            return ""
        if not retry:
            logger.warning(
                "openai asr: the retry came back empty, "
                "treating %.2f s of audio as nothing said",
                duration_s,
                extra=self._echo_event("confirmed_empty", duration_s, retry_ms),
            )
            return ""
        logger.info(
            'openai asr: the retry recovered "%s" from %.2f s of audio '
            "the echo guard would have discarded",
            retry,
            duration_s,
            extra=self._echo_event("recovered", duration_s, retry_ms),
        )
        return retry

    def _echo_event(
        self, outcome: str, duration_s: float, retry_ms: int | None = None
    ) -> dict[str, object]:
        """The structured half of a tripped guard's outcome: exactly one
        `asr_prompt_echo` event per trip, so retained logs can say how
        often the guard was swallowing real speech and what each retry
        cost. `retry_ms` is absent when no retry was sent, which is what
        a skip is. No `session` or `device`: providers are shared
        singletons with no session identity, so the event names the
        host instead, like the entry it belongs to."""
        fields: dict[str, object] = {
            "event": "asr_prompt_echo",
            "outcome": outcome,
            "duration_s": duration_s,
            "host": self.host,
        }
        if retry_ms is not None:
            fields["retry_ms"] = retry_ms
        return fields

    def _is_echoed_prompt(self, text: str) -> bool:
        """Whether the model handed the prompt back instead of hearing
        anything. A known shape on short or low-content audio, and not a
        cosmetic one: an echo is fed to the session as an utterance the
        user never said, so a prompt naming the agents can trigger a
        handover nobody asked for.

        Equality rather than containment: a longer transcript that
        happens to open with the prompt is a person saying those words.
        A real utterance that is exactly the prompt string loses nothing
        worth keeping."""
        if not self._prompt:
            return False
        return _normalized(text) == _normalized(self._prompt)


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
        min_audio_s=MIN_AUDIO_S if is_openai else 0.0,
    )
