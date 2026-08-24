"""Local speech recognition on faster-whisper (CTranslate2).

The optional `faster-whisper` extra provides this module's engine:
`uv sync --extra faster-whisper`. Model weights download from Hugging
Face into the local cache when the provider is built, which is server
startup, so the first conversation does not pay for the download.

Whisper is multilingual, and per-utterance language detection is its
single most expensive habit on a CPU: a constant encoder pass over a
padded 30 s window, measured at several seconds per turn on small
hardware, with a misdetection sending the decoder into territory that
costs more again (#22). The `language` option pins one language and
skips all of that at the price of monolingual deployment; the
`language_detect`, `language_fallback` and `language_confidence_floor`
options are the middle ground, detecting when needed and trusting a
detection only as far as its confidence earns.

What this type accepts is declared once, as `FasterWhisperOptions` in
`config/provider_options.py`, and reaches the builder below already
validated. The decode options mirror `WhisperModel.transcribe`
arguments of the same name and keep the engine's defaults when unset,
with one exception: `beam_size` defaults to greedy decoding (1),
because beam search buys little accuracy on short spoken commands and
costs a multiple of the CPU time (#19).
"""

import logging

import numpy as np
from faster_whisper import WhisperModel

from vinga_server.config.models import ProviderConfig
from vinga_server.config.provider_options import FasterWhisperOptions
from vinga_server.providers.base import AsrProvider, AsrResult, Operations

logger = logging.getLogger(__name__)

# What faster-whisper expects, and what the pipeline feeds it.
EXPECTED_SAMPLE_RATE = 16000


def pcm_to_float(pcm: bytes) -> "np.ndarray":
    """s16le bytes to the float32 array faster-whisper consumes."""
    return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0


class FasterWhisperAsr(AsrProvider):
    # Inference runs on the host; only the weights download, at startup.
    egress = False

    def __init__(
        self,
        model: str,
        language: str | None,
        device: str,
        compute_type: str,
        beam_size: int,
        download_dir: str | None,
        cpu_threads: int,
        vad_filter: bool,
        vad_parameters: dict[str, object],
        condition_on_previous_text: bool,
        temperature: list[float] | None,
        language_detect: str,
        language_fallback: str | None,
        language_confidence_floor: float,
    ) -> None:
        # The values are deliberately not in this line (#191). A
        # provider is built before an apply can refuse on anything after
        # it, so a line written here is a line an operator's retained
        # logs keep whatever the request answered, and what it would
        # carry is arbitrary stored scalars: a credential pasted into
        # `model` is exactly that shape of thing. What is worth saying
        # is that something slow has started, which is the whole reason
        # the line exists on a first start. Which entry it is, and what
        # it is configured with, are in the configuration the operator
        # is holding.
        logger.info("loading the faster-whisper model this entry configures")
        self.model = model
        # The transcriptions running off the loop right now, so that a
        # teardown waits for the worker rather than for its caller.
        self._operations = Operations()
        self._engine: WhisperModel | None = WhisperModel(
            model,
            device=device,
            compute_type=compute_type,
            download_root=download_dir,
            cpu_threads=cpu_threads,
        )
        self._language = language
        self._detect_once = language_detect == "once"
        self._language_fallback = language_fallback
        self._confidence_floor = language_confidence_floor
        # Everything transcribe() is called with. Options with no entry
        # here keep the engine's default for the installed version.
        self._decode_options: dict[str, object] = {
            "beam_size": beam_size,
            "vad_filter": vad_filter,
            "condition_on_previous_text": condition_on_previous_text,
        }
        if vad_parameters:
            self._decode_options["vad_parameters"] = vad_parameters
        if temperature is not None:
            self._decode_options["temperature"] = temperature

    async def transcribe(
        self, pcm: bytes, sample_rate: int, language_hint: str | None = None
    ) -> AsrResult:
        if sample_rate != EXPECTED_SAMPLE_RATE:
            raise ValueError(f"faster-whisper is fed {EXPECTED_SAMPLE_RATE} Hz, got {sample_rate}")
        return await self._operations.run(lambda: self._transcribe(pcm, language_hint))

    async def close(self) -> None:
        """Let go of the loaded model, once every transcription that had
        started has finished with it.

        The wait is the point. A decode runs in a worker thread and a
        cancelled caller does not stop it, so dropping the reference on
        the strength of nobody awaiting would drop it under a thread
        that is still reading (#191).

        What the release actually buys is the library's to decide.
        CTranslate2 holds the weights in memory it manages itself and
        frees on its own schedule, so this drops the last reference this
        process holds and claims nothing about resident memory at the
        instant it returns; what a swap of a local model costs is
        briefly holding two, which is the price the reload was designed
        to pay.
        """
        await self._operations.settled()
        self._engine = None

    def _transcribe(self, pcm: bytes, language_hint: str | None) -> AsrResult:
        engine = self._engine
        if engine is None:
            # Unreachable from a running server: a provider is disposed
            # of only once no world holds it, so nothing is left to ask.
            raise RuntimeError("this faster-whisper entry has been closed")
        audio = pcm_to_float(pcm)
        # A configured language always beats the hint: the hint is this
        # provider's own earlier detection coming back from the session.
        pinned = self._language or language_hint
        segments, info = engine.transcribe(audio, language=pinned, **self._decode_options)
        detected = getattr(info, "language", None)
        confidence = getattr(info, "language_probability", None) if pinned is None else None

        if (
            pinned is None
            and self._language_fallback is not None
            and confidence is not None
            and confidence < self._confidence_floor
            and detected != self._language_fallback
        ):
            # The engine detects before it decodes and `segments` is
            # lazy, so abandoning it here means the low-confidence
            # decode never runs; the retry below is the only decode.
            logger.info(
                "language detection %s at %.2f is below the %.2f floor, using %s",
                detected,
                confidence,
                self._confidence_floor,
                self._language_fallback,
            )
            detected = self._language_fallback
            segments, info = engine.transcribe(
                audio, language=self._language_fallback, **self._decode_options
            )

        lock = None
        if (
            self._detect_once
            and pinned is None
            and confidence is not None
            and confidence >= self._confidence_floor
        ):
            lock = detected

        # Consuming the generator here keeps the decoding inside the
        # worker thread rather than the event loop.
        text = " ".join(segment.text.strip() for segment in segments).strip()
        return AsrResult(
            text=text,
            language=detected,
            language_confidence=confidence,
            lock_language=lock,
        )


def build(
    label: str, config: ProviderConfig, options: FasterWhisperOptions
) -> FasterWhisperAsr:
    """The entry's validated options as the engine's own arguments.

    Thin on purpose and not a pass-through: what it does is the one
    translation this seam needs, `vad_parameters` from a model to the
    mapping `WhisperModel.transcribe` takes. `exclude_unset` is what
    makes that honest in both directions: an operator's explicit value
    travels, a null they wrote travels, and a default this repository
    injected does not, so the engine keeps deciding everything the
    fragment did not.

    Nothing here refuses. Every option was checked against
    `FasterWhisperOptions` before this was called, which is the ordering
    the reader's `finish()` used to hold: a refusal must not cost a
    model load (#191).
    """
    return FasterWhisperAsr(
        model=options.model,
        language=options.language,
        device=options.device,
        compute_type=options.compute_type,
        beam_size=options.beam_size,
        download_dir=options.download_dir,
        cpu_threads=options.cpu_threads,
        vad_filter=options.vad_filter,
        vad_parameters=options.vad_parameters.model_dump(exclude_unset=True),
        condition_on_previous_text=options.condition_on_previous_text,
        temperature=options.temperature,
        language_detect=options.language_detect,
        language_fallback=options.language_fallback,
        language_confidence_floor=options.language_confidence_floor,
    )
