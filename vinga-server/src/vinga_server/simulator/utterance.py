"""The one thing this simulated board can say, and what shape it is in.

A board sends Opus, and nothing in a client install can encode any. So
the sentence is encoded once, at build time, and shipped as package data:
`framing.wrap(1, packet)` returns a packet untouched, which is what makes
a pre-encoded packet playable with no codec at all, and packets encoded
once are byte-identical on a laptop and on a runner, which is what makes
an audio path testable.

The file is a run of version 2 frames rather than a container. A file of
bare Opus packets has no boundaries, because that is exactly what bare
means, and version 2 is the framing this repository already defines with
a `payload_size` field, so `framing.frames` walks the file and
`framing.wrap` puts the packets back out under whichever version the
session negotiated. The stored framing and the sent framing are
deliberately unrelated.

Beside the asset is a manifest, and it is not decoration: it is where the
rate, the packet duration and the sentence are recorded, so the sender
paces the packets by what they were encoded at rather than by a constant
written twice. `tests/unit/test_simulator_utterance.py` holds the file to
it, so the asset cannot drift and cannot be replaced quietly, and
`tests/tools/utterance.py` is what wrote both.

What this module is not is a wire protocol. It knows an asset format;
`conversation.py` knows a conversation. Those are two responsibilities
with two reasons to change.
"""

import json
from dataclasses import dataclass
from importlib.resources import files
from typing import Any

from vinga_server.config.loader import ConfigError
from vinga_server.protocol.framing import FramingError, frames

# Where the asset and its manifest sit, as package data rather than as
# files beside a checkout: what a wheel does not carry is what an
# installed command does not have.
PACKAGE = "vinga_server.simulator"

DATA = "data"

ASSET = "utterance.frames"

MANIFEST = "utterance.json"

# The framing the file is STORED as. Not the framing it is sent under,
# which is whatever the check-in reply named.
STORED_VERSION = 2

# What a build that did not carry the asset says. Fixed, and it names the
# packaging rather than a path, because a path is a fact about this
# machine and the fault is a fact about the artifact.
NO_UTTERANCE = (
    "this installation carries no packaged utterance, so there is nothing for a simulated "
    "board to say. The audio ships as package data inside the wheel; an installation "
    "missing it was not built from this project's own packaging."
)

UNREADABLE_UTTERANCE = (
    "the packaged utterance could not be read as the run of frames it is supposed to be, "
    "so there is nothing for a simulated board to say. Reinstall from a wheel this project "
    "built."
)


@dataclass(frozen=True)
class Utterance:
    """One sentence of Opus, ready to send.

    `packets` are bare Opus payloads: what goes on the wire is each of
    them through `framing.wrap` under the negotiated version, which is
    the sender's business rather than this module's.
    """

    # What the sentence says, so the command can print what it is about
    # to send. This side's own constant, not far-side text.
    sentence: str

    packets: tuple[bytes, ...]

    # What the packets were encoded at, which is what the hello announces
    # and what the sender paces by.
    sample_rate: int

    frame_duration_ms: int

    @property
    def duration_ms(self) -> int:
        """How long the utterance is, from the packet count and the
        duration each packet holds. Arithmetic over frames rather than a
        measurement, because nothing here decodes anything."""
        return len(self.packets) * self.frame_duration_ms


def packaged() -> Utterance:
    """The utterance this installation carries.

    Read on demand rather than at import, so nothing pays for the file
    who did not ask to speak, and so the refusal for a build that did not
    carry it is this grammar's sentence rather than an import error at
    the top of a module.
    """
    return understood(_read(MANIFEST), _read(ASSET))


def understood(manifest: bytes | None, asset: bytes | None) -> Utterance:
    """What one manifest and one asset mean, or the fixed refusal for a
    pair that does not mean an utterance.

    Separate from `packaged` because it is a separate question: that one
    is about where this installation's copy is, and this one is about
    what a copy has to be. `None` is what a file that is not there looks
    like from here, which is why it is a value rather than an exception.
    """
    if manifest is None or asset is None:
        raise ConfigError(NO_UTTERANCE)
    described = _described(manifest)
    if described is None:
        raise ConfigError(UNREADABLE_UTTERANCE)
    walked: tuple[bytes, ...] = ()
    try:
        walked = tuple(frame.payload for frame in frames(STORED_VERSION, asset))
    except FramingError:
        # Recorded by category and answered outside the handler, the way
        # every boundary in this package answers, so nothing walking a
        # chain finds the framing exception behind the sentence.
        walked = ()
    if not walked:
        raise ConfigError(UNREADABLE_UTTERANCE)
    sentence, rate, duration = described
    return Utterance(
        sentence=sentence,
        packets=walked,
        sample_rate=rate,
        frame_duration_ms=duration,
    )


def _read(name: str) -> bytes | None:
    """One packaged file, or None when this installation has no such
    thing."""
    try:
        return (files(PACKAGE) / DATA / name).read_bytes()
    except (FileNotFoundError, ModuleNotFoundError, OSError):
        return None


def _described(manifest: bytes) -> tuple[str, int, int] | None:
    """The three facts the sender needs out of the manifest, or None when
    it is not the manifest this module was written against.

    Checked rather than trusted even though it ships in the same wheel as
    the code: an asset and its description are two files, and two files
    can be edited apart. What is not checked here is the checksum, which
    is a case's job (`tests/unit/test_simulator_utterance.py`): hashing
    ten kilobytes on every run would buy a command nothing an installed
    artifact does not already have.
    """
    described: Any = None
    try:
        described = json.loads(manifest)
    except ValueError:
        return None
    if not isinstance(described, dict):
        return None
    sentence = described.get("sentence")
    rate = described.get("sample_rate")
    duration = described.get("frame_duration_ms")
    if not isinstance(sentence, str) or not sentence:
        return None
    for number in (rate, duration):
        if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
            return None
    return sentence, rate, duration


__all__ = [
    "ASSET",
    "DATA",
    "MANIFEST",
    "NO_UTTERANCE",
    "PACKAGE",
    "STORED_VERSION",
    "UNREADABLE_UTTERANCE",
    "Utterance",
    "packaged",
    "understood",
]
