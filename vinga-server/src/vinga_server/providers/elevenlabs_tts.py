"""Cloud text to speech on ElevenLabs, streamed as raw PCM.

No SDK and no extra: the streaming endpoint is one POST whose response
body is the audio, so `httpx` (already a dependency) covers it, and a
cloud provider carries none of the weight or licensing that makes the
local engines optional (#11).

`output_format` asks the API for signed 16-bit little-endian mono at a
named rate, which is exactly what this stage's interface passes along,
so the default `pcm_24000` matches the device output rate and the
session's resampler has nothing to do. The API bills by character and
the request carries the reply text, so the type marks egress.
"""

import re
from collections.abc import AsyncIterator

import httpx

from vinga_server.config.models import ProviderConfig
from vinga_server.providers.base import ProviderCallError, ProviderError, TtsProvider
from vinga_server.providers.kit import (
    DEFAULT_TIMEOUT_S,
    HTTPX_FAILURES,
    aligned_pcm,
    call_failure,
    resolve_api_key,
)
from vinga_server.providers.registry import OptionsReader

# How this provider names itself where the kit speaks on its behalf: the
# warning a truncated stream produces, and the message a failed request
# carries.
LABEL = "elevenlabs"

API_BASE_URL = "https://api.elevenlabs.io"

# The one host this type reaches, and the one an egress allowlist is
# most likely to be missing: no other provider type shares it.
API_HOST = "api.elevenlabs.io"

# Flash is the low-latency model (~75 ms to first byte, 32 languages
# including Swedish). A voice assistant is the case it exists for; an
# operator who would rather have Multilingual v2's fidelity than its
# latency sets `model` instead.
DEFAULT_MODEL = "eleven_flash_v2_5"

DEFAULT_OUTPUT_FORMAT = "pcm_24000"

# Only the PCM formats are usable here: the stage's contract is s16le
# PCM, and decoding mp3 or opus just to re-encode it would add both a
# dependency and latency. `pcm_44100` and up need a paid ElevenLabs
# tier, which is the API's error to report, not ours to predict.
_PCM_FORMAT = re.compile(r"^pcm_(\d+)$")

# Named exactly as the API names them, since they are passed through.
_VOICE_SETTING_NUMBERS = ("stability", "similarity_boost", "style", "speed")
_VOICE_SETTING_FLAGS = ("use_speaker_boost",)


def parse_sample_rate(label: str, output_format: str) -> int:
    """The rate an output format produces, rejecting the formats this
    stage cannot pass through."""
    match = _PCM_FORMAT.match(output_format)
    if match is None:
        raise ProviderError(
            f'{label}: option "output_format" must be one of the pcm_<rate> '
            f'formats (this stage streams raw PCM), not "{output_format}"'
        )
    return int(match.group(1))


def read_voice_settings(label: str, settings: dict[str, object]) -> dict[str, object]:
    """The `voice_settings` mapping, validated key by key. Unknown keys
    are rejected here for the same reason unknown options are: a typo
    that the API silently ignores is a knob that never took effect."""
    known = set(_VOICE_SETTING_NUMBERS) | set(_VOICE_SETTING_FLAGS)
    unknown = sorted(set(settings) - known)
    if unknown:
        raise ProviderError(
            f'{label}: unknown voice_settings key(s): {", ".join(unknown)} '
            f'(known: {", ".join(sorted(known))})'
        )
    for key in _VOICE_SETTING_NUMBERS:
        value = settings.get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int | float)):
            raise ProviderError(f'{label}: voice_settings "{key}" must be a number')
    for key in _VOICE_SETTING_FLAGS:
        value = settings.get(key)
        if value is not None and not isinstance(value, bool):
            raise ProviderError(f'{label}: voice_settings "{key}" must be true or false')
    return dict(settings)


class ElevenLabsTts(TtsProvider):
    # The reply text goes to the vendor's API to be spoken.
    egress = True

    def __init__(
        self,
        voice_id: str,
        model: str,
        output_format: str,
        sample_rate: int,
        api_key: str,
        language_code: str | None = None,
        voice_settings: dict[str, object] | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._voice_id = voice_id
        self.model = model
        self.host = API_HOST
        self._output_format = output_format
        self.sample_rate = sample_rate
        self._language_code = language_code
        self._voice_settings = voice_settings or {}
        # One client per provider entry, so its connection pool is
        # reused across sentences and sessions: a fresh TLS handshake
        # per sentence would show up as latency in the gap the user
        # hears. Providers are built at startup and live as long as the
        # server, which is also this client's lifetime.
        self._client = (
            client
            if client is not None
            else httpx.AsyncClient(
                base_url=API_BASE_URL,
                timeout=httpx.Timeout(timeout_s, connect=timeout_s),
                headers={"xi-api-key": api_key},
            )
        )

    def _body(self, text: str) -> dict[str, object]:
        body: dict[str, object] = {"text": text, "model_id": self.model}
        if self._language_code:
            body["language_code"] = self._language_code
        if self._voice_settings:
            body["voice_settings"] = self._voice_settings
        return body

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        """Stream one sentence, yielding PCM as it arrives, sample-aligned
        by the kit's helper.

        The request, the status it comes back with, and the bytes after
        it are three places the API can fail to deliver a sentence, and
        all three leave as a failed provider call. Cancellation and genuine
        bugs are outside HTTPX_FAILURES and pass through as themselves,
        which the barge-in path depends on."""
        request = self._client.build_request(
            "POST",
            f"/v1/text-to-speech/{self._voice_id}/stream",
            params={"output_format": self._output_format},
            json=self._body(text),
        )
        opening: ProviderCallError | None = None
        try:
            response = await self._client.send(request, stream=True)
        except HTTPX_FAILURES as exc:
            opening = call_failure(LABEL, exc)
        # Raised out here rather than in the except arm, so the httpx
        # exception is not even the new error's `__context__`: `from
        # None` suppresses its rendering but leaves it reachable, and
        # what it can carry is the reason the message is metadata only.
        if opening is not None:
            raise opening from None
        failure: ProviderCallError | None = None
        try:
            if response.status_code != httpx.codes.OK:
                failure = _api_error(response.status_code)
            else:
                async for chunk in aligned_pcm(LABEL, response.aiter_bytes()):
                    yield chunk
        except HTTPX_FAILURES as exc:
            failure = call_failure(LABEL, exc)
        except BaseException:
            # A cancelled reply (barge-in), a consumer walking away, or
            # a bug of ours: each passes through as itself, and the
            # release must not take its place, so a failure there is
            # dropped on this path rather than raised. Releasing the
            # response is still what has to happen, which is why this
            # arm exists at all rather than a `finally`: an exception
            # raised inside a `finally` replaces the one in flight, and
            # a connection reset while closing would have replaced the
            # cancellation.
            await self._released(response)
            raise
        closing = await self._released(response)
        # The first failure is the one that explains the sentence; a
        # release that failed after it is a consequence of the same
        # broken connection, and it is only the whole story when nothing
        # else went wrong.
        if failure is None:
            failure = closing
        if failure is not None:
            raise failure from None

    async def _released(self, response: httpx.Response) -> ProviderCallError | None:
        """Give the connection back, answering with a close-time failure
        rather than raising it.

        Closing talks to the socket, so it fails the way any other
        request does, and the caller decides whether that failure is the
        one worth telling anyone about. A body read to completion has
        closed itself already, which makes this a no-op on the ordinary
        path."""
        try:
            await response.aclose()
        except HTTPX_FAILURES as exc:
            return call_failure(LABEL, exc)
        return None


def _api_error(status_code: int) -> ProviderCallError:
    """A failed request as the taxonomy error the session's reply
    handler can log, carrying the status and nothing else.

    The body used to be quoted into the message, truncated, because it
    carries the reason (an unknown voice, an exhausted quota, a tier
    that does not allow the format). It no longer is: the API decides
    what goes in that body, the request that produced it carried the
    reply text, and the session renders this message into the log line
    the observability ADR keeps (#137). The status names which class of
    failure it was, and re-running the request by hand is what recovers
    the prose."""
    return ProviderCallError(f"{LABEL}: the request failed with HTTP {status_code}")


def build(label: str, config: ProviderConfig) -> ElevenLabsTts:
    options = OptionsReader(label, config)
    voice_id = options.required_string("voice_id")
    model = options.string("model", DEFAULT_MODEL)
    output_format = options.string("output_format", DEFAULT_OUTPUT_FORMAT)
    language_code = options.string("language_code")
    timeout_s = options.number("timeout_s", DEFAULT_TIMEOUT_S)
    voice_settings = read_voice_settings(label, options.mapping("voice_settings"))
    options.finish()
    assert model is not None and output_format is not None  # defaults are strings
    api_key = resolve_api_key(label, config.api_key_env)
    if api_key is None:
        raise ProviderError(
            f'{label}: type "elevenlabs" needs an API key; name the environment '
            f'variable holding it with "api_key_env"'
        )
    return ElevenLabsTts(
        voice_id=voice_id,
        model=model,
        output_format=output_format,
        sample_rate=parse_sample_rate(label, output_format),
        api_key=api_key,
        language_code=language_code,
        voice_settings=voice_settings,
        timeout_s=timeout_s,
    )
