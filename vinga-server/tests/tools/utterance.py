"""How the packaged utterance was made, checked in so it can be made
again.

`vinga simulator run` sends one sentence of Opus, and that sentence is a
file in the wheel rather than something encoded at run time. Decision 1
of the #248 plan says why: adding a codec to a client tier for the sole
purpose of encoding a fixed sentence would put the heaviest distribution
in the serve tier into a laptop install, and it would make what the
simulator sends vary with the FFmpeg build on the machine. Packets
encoded once are byte-identical on a laptop and on a runner, which is
what makes an audio path testable at all.

So this runs by hand, on a contributor's machine, and never at install
time, at import time or in a lane:

    uv run python -m tests.tools.utterance

It downloads the pinned voice, synthesizes the sentence, resamples to
16 kHz mono, encodes at 60 ms through the server's own encoder, and
writes the asset and its manifest into `src/vinga_server/simulator/data/`.

**The provenance, which is a licensing decision rather than a detail.**
The voice is `en_US-ljspeech-high` from `rhasspy/piper-voices` at tag
`v1.0.0`. Its `MODEL_CARD` gives the dataset licence as the exact string
`public domain`, over the LJ Speech Dataset, whose own page says "There
are no restrictions on its use" and "you may use it without
attribution"; the repository card declares `license: mit` over the
weights. Every link in that chain is zero-obligation, which is why no
`THIRD_PARTY_LICENSES.md` entry is owed, and the two alternatives were
rejected on their licence strings: `en_US-lessac-medium` is a
research-only Blizzard 2013 click-through excluding commercial use, and
`en_US-libritts_r-medium` is CC BY 4.0 over a lessac base. Read the card
at the pinned tag again before committing a regenerated asset, and stop
if the string is not the one above.

**Piper itself never enters this project.** Today's `pip install
piper-tts` is the `piper1-gpl` rewrite, which is GPL-3.0 because it
embeds espeak-ng, and this repository's licensing rules forbid a GPL
package becoming anything but an optional extra. So it is run as a
transient tool through `uvx`, which installs it into a throwaway
environment and puts it in no tier of anything. The GNU GPL FAQ's
`#WhatCaseIsOutputGPL` is why the output is unencumbered: a program's
licence does not cover its output, and what a Piper waveform reproduces
is the voice model and the input text.

**The synthesis is not deterministic and the asset is.** Piper's VITS
sampling is stochastic, so a regeneration produces a different waveform
of the same sentence; the manifest's SHA-256 pins the one that was
committed, and `tests/unit/test_simulator_utterance.py` holds the asset
to it. Regenerating is therefore a deliberate act with a review attached,
which is what the plan asked of an asset a public repository carries.
"""

import json
import shutil
import subprocess
import sys
import tempfile
import wave
from hashlib import sha256
from pathlib import Path

import av

from vinga_server.audio.opus import OpusEncoder
from vinga_server.protocol import framing
from vinga_server.protocol.messages import AudioParams

PROJECT = Path(__file__).resolve().parents[2]

WHERE = PROJECT / "src" / "vinga_server" / "simulator" / "data"

ASSET = WHERE / "utterance.frames"

MANIFEST = WHERE / "utterance.json"

# What the board says. Deliberately neutral, short and self-describing:
# no personal detail, no place and no name, a plausible thing to say to a
# board, and long enough for a real ASR to have something to work with.
SENTENCE = "Hello, can you hear me?"

# The voice, pinned by tag so the same command fetches the same weights.
VOICE = "en_US-ljspeech-high"

VOICE_TAG = "v1.0.0"

VOICE_BASE = (
    f"https://huggingface.co/rhasspy/piper-voices/resolve/{VOICE_TAG}/en/en_US/ljspeech/high"
)

# What the manifest records about where the audio came from, in the words
# decision 1a of the plan fixed.
PROVENANCE = (
    "Synthesized with Piper using en_US-ljspeech-high (rhasspy/piper-voices, MIT weights), "
    "trained on the LJ Speech Dataset (public domain)."
)

# The device side of `AudioParams`, read from the protocol rather than
# written here: what the simulator announces in its hello and what it
# encodes at are one fact, and a second spelling of 16 000 would be a
# bug pending.
_DEVICE_AUDIO = AudioParams()

RATE = _DEVICE_AUDIO.sample_rate

FRAME_MS = _DEVICE_AUDIO.frame_duration

FRAME_SAMPLES = RATE * FRAME_MS // 1000

# What sits around the speech, and why there is any.
#
# A real board opens its microphone before a person starts talking and
# closes it after they stop, so the server's endpointer meets speech with
# silence on both sides of it. An utterance that started on the first
# syllable would be the one shape a deployment never sees.
#
# The tail is a minimum rather than a length, because the whole asset is
# padded up to a whole number of packets afterwards: a partial packet at
# the end would be encoded from a frame this tool padded anyway, and
# doing the padding here keeps it visible.
LEAD_SILENCE_MS = 120

MINIMUM_TRAIL_SILENCE_MS = 300

# The framing the asset is STORED as, which is not the framing it is sent
# under. A run of bare Opus packets has no boundaries at all, and version
# 2 is the framing this repository already defines with a payload_size
# field, so `framing.frames` walks the file and `framing.wrap` puts the
# packets back out under whatever version the session negotiated.
STORED_VERSION = 2


def fetched(into: Path) -> tuple[Path, Path]:
    """The pinned voice, downloaded beside its configuration.

    Two files: the ONNX weights and the JSON that says what the model's
    phoneme table and sample rate are. Piper needs both.
    """
    import urllib.request

    weights = into / f"{VOICE}.onnx"
    configuration = into / f"{VOICE}.onnx.json"
    for path in (weights, configuration):
        print(f"fetching {path.name}", file=sys.stderr)
        with urllib.request.urlopen(f"{VOICE_BASE}/{path.name}") as answer:
            path.write_bytes(answer.read())
    return weights, configuration


def spoken(weights: Path, configuration: Path, into: Path) -> Path:
    """The sentence as a WAV, synthesized by Piper in an environment of
    its own.

    `uvx` and not an import, for the licensing reason the head of this
    module gives: the package that speaks is GPL-3.0 and may not be a
    dependency of anything here.

    `--sentence-silence 0` because the silence around the speech is this
    tool's, decided above with its reason, rather than a synthesizer
    default that could change under the pin.
    """
    if shutil.which("uvx") is None:  # pragma: no cover - a hand run
        raise SystemExit("uvx is not on PATH, and it is what runs piper without installing it")
    said = into / "said.wav"
    line = into / "line.txt"
    line.write_text(SENTENCE, encoding="utf-8")
    subprocess.run(
        [
            "uvx",
            "--from",
            "piper-tts",
            "piper",
            "--model",
            str(weights),
            "--config",
            str(configuration),
            "--input-file",
            str(line),
            "--output-file",
            str(said),
            "--sentence-silence",
            "0.0",
        ],
        check=True,
    )
    return said


def resampled(said: Path) -> bytes:
    """The synthesized WAV as 16 kHz mono s16 PCM.

    The voice speaks at 22 050 Hz and the device side of the protocol is
    16 000, so this is a resample rather than a copy. `av` does it, which
    is the resampler the server itself runs, and it is a dev-environment
    tool here rather than a tier of anything.
    """
    with wave.open(str(said)) as reading:
        if reading.getnchannels() != 1 or reading.getsampwidth() != 2:
            raise SystemExit("the synthesizer did not answer 16-bit mono, which this cannot fix")
        source_rate = reading.getframerate()
        pcm = reading.readframes(reading.getnframes())
    resampler = av.AudioResampler(format="s16", layout="mono", rate=RATE)
    frame = av.AudioFrame(format="s16", layout="mono", samples=len(pcm) // 2)
    frame.sample_rate = source_rate
    frame.planes[0].update(pcm)
    chunks: list[bytes] = []
    for produced in (*resampler.resample(frame), *resampler.resample(None)):
        # The plane buffer can be padded past the sample count.
        chunks.append(bytes(produced.planes[0])[: produced.samples * 2])
    return b"".join(chunks)


def padded(pcm: bytes) -> bytes:
    """The speech with the silence a board would have captured around it,
    ending on a whole packet boundary."""
    lead = b"\x00" * (RATE * LEAD_SILENCE_MS // 1000 * 2)
    minimum = RATE * MINIMUM_TRAIL_SILENCE_MS // 1000 * 2
    so_far = len(lead) + len(pcm) + minimum
    frame_bytes = FRAME_SAMPLES * 2
    whole = -(-so_far // frame_bytes) * frame_bytes
    return lead + pcm + b"\x00" * (whole - len(lead) - len(pcm))


def encoded(pcm: bytes) -> list[bytes]:
    """The PCM as Opus packets, through the encoder the server runs.

    The same class the device edge encodes replies with, so what the
    simulator sends is encoded by the codec this repository already
    speaks rather than by a second configuration of libopus.
    """
    encoder = OpusEncoder(sample_rate=RATE, frame_duration_ms=FRAME_MS)
    packets = encoder.encode(pcm)
    packets += encoder.flush()
    return packets


def written(packets: list[bytes]) -> None:
    """The asset and the manifest, side by side.

    The manifest is what `simulator/utterance.py` reads the utterance's
    shape from and what a case holds the file to, so the asset cannot
    drift and cannot be replaced quietly.
    """
    data = b"".join(framing.wrap(STORED_VERSION, packet) for packet in packets)
    WHERE.mkdir(parents=True, exist_ok=True)
    ASSET.write_bytes(data)
    MANIFEST.write_text(
        json.dumps(
            {
                "provenance": PROVENANCE,
                "sentence": SENTENCE,
                "sample_rate": RATE,
                "channels": _DEVICE_AUDIO.channels,
                "frame_duration_ms": FRAME_MS,
                "stored_framing_version": STORED_VERSION,
                "packets": len(packets),
                "duration_ms": len(packets) * FRAME_MS,
                "bytes": len(data),
                "sha256": sha256(data).hexdigest(),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        f"wrote {len(packets)} packets, {len(packets) * FRAME_MS} ms, {len(data)} bytes",
        file=sys.stderr,
    )


def main() -> None:  # pragma: no cover - the regeneration path
    with tempfile.TemporaryDirectory(prefix="vinga-utterance-") as scratch:
        where = Path(scratch)
        weights, configuration = fetched(where)
        written(encoded(padded(resampled(spoken(weights, configuration, where)))))


if __name__ == "__main__":  # pragma: no cover - the regeneration entry point
    main()
