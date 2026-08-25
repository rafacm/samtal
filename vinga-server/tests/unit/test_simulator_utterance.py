"""The packaged utterance, held to the manifest committed beside it.

The asset is a binary file in a public repository, which is a thing that
can drift and a thing that can be replaced quietly. What stops both is
this file: the manifest records what was written and these cases hold the
bytes to it, so a regenerated asset arrives with its numbers changed in
the same diff.

And one case does something none of the others does. Every assertion
about frames and counts would pass if the file held forty well-formed
frames of garbage, so the packets are fed through the server's own
decoder and asserted to come out as 16 kHz mono PCM of the length the
manifest claims. Without it the whole audio path could ship carrying
neither speech nor Opus, with every other case in this suite green.

That decoder is `av`, which is the serve tier, so it skips where the
extra is absent rather than failing: the same rule the provider suites
apply to `faster-whisper` and `piper`.
"""

import json
from hashlib import sha256
from importlib.util import find_spec
from pathlib import Path

import pytest

from vinga_server.config.loader import ConfigError
from vinga_server.protocol.framing import frames
from vinga_server.protocol.messages import AudioParams
from vinga_server.simulator import utterance

HAS_AV = find_spec("av") is not None

WHERE = Path(utterance.__file__).parent / utterance.DATA

ASSET = WHERE / utterance.ASSET

MANIFEST = WHERE / utterance.MANIFEST

# The contract decision 1a of the #248 plan fixed, held here rather than
# left to the manifest to agree with itself: a regenerated asset that
# came out at four hundred packets, or at 24 kHz, or in stereo, is a
# different thing from the one the plan chose, and the manifest alone
# would have recorded the new numbers without objecting to them.
SENTENCE = "Hello, can you hear me?"

SHORTEST_PACKETS = 25

LONGEST_PACKETS = 42


@pytest.fixture(scope="module")
def manifest() -> dict[str, object]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_the_asset_is_the_file_the_manifest_describes(manifest: dict[str, object]) -> None:
    """Byte length and checksum, which between them say this is that
    file and not another one with the same name."""
    data = ASSET.read_bytes()

    assert manifest["bytes"] == len(data)
    assert manifest["sha256"] == sha256(data).hexdigest()


def test_the_asset_walks_into_the_packets_the_manifest_counts(
    manifest: dict[str, object],
) -> None:
    """The frame walk, which is the format claim: the file is a run of
    version 2 frames and every one of them carries a payload."""
    walked = frames(utterance.STORED_VERSION, ASSET.read_bytes())

    assert len(walked) == manifest["packets"]
    assert all(frame.payload for frame in walked), "a packet in the asset is empty"
    assert manifest["duration_ms"] == len(walked) * manifest["frame_duration_ms"]


def test_the_manifest_records_the_contract_the_plan_fixed(manifest: dict[str, object]) -> None:
    """16 kHz mono at 60 ms, which is the device side of `AudioParams`,
    and a length in the range the plan chose. Read off the protocol
    rather than written twice: the rate the simulator announces in its
    hello and the rate it encoded at are one fact."""
    device = AudioParams()

    assert manifest["sentence"] == SENTENCE
    assert manifest["sample_rate"] == device.sample_rate
    assert manifest["channels"] == device.channels
    assert manifest["frame_duration_ms"] == device.frame_duration
    assert SHORTEST_PACKETS <= manifest["packets"] <= LONGEST_PACKETS


def test_the_manifest_records_where_the_audio_came_from(manifest: dict[str, object]) -> None:
    """The provenance line, in the words decision 1a fixed. No
    `THIRD_PARTY_LICENSES.md` entry is owed because no attribution is
    required anywhere in the chain, so this line is the record, and a
    record nothing checks is a line somebody deletes."""
    assert manifest["provenance"] == (
        "Synthesized with Piper using en_US-ljspeech-high (rhasspy/piper-voices, MIT "
        "weights), trained on the LJ Speech Dataset (public domain)."
    )


def test_the_packaged_utterance_reads_its_own_shape(manifest: dict[str, object]) -> None:
    """What a sender gets: the packets, and the rate and packet duration
    to pace and announce them by."""
    said = utterance.packaged()

    assert said.sentence == SENTENCE
    assert len(said.packets) == manifest["packets"]
    assert said.sample_rate == manifest["sample_rate"]
    assert said.frame_duration_ms == manifest["frame_duration_ms"]
    assert said.duration_ms == manifest["duration_ms"]


def test_the_packets_are_bare_opus_rather_than_framed_ones() -> None:
    """What comes back is the payloads, not the stored frames: the
    sender wraps them again under whichever version the session
    negotiated, and a packet that arrived with its stored header still
    on it would be double-wrapped on the wire."""
    said = utterance.packaged()
    walked = frames(utterance.STORED_VERSION, ASSET.read_bytes())

    assert said.packets == tuple(frame.payload for frame in walked)


@pytest.mark.skipif(not HAS_AV, reason="the serve extra's codec is not installed")
def test_the_packets_decode_as_opus_through_the_servers_own_decoder(
    manifest: dict[str, object],
) -> None:
    """The case that separates real Opus from well-framed garbage.

    Fed through the decoder the device edge runs, and asserted to come
    out at the rate and length the manifest claims. The tolerance is one
    packet: libopus carries a few milliseconds of encoder lookahead that
    the decoder skips at the start of a stream, so the decoded length is
    a shade under the encoded one, and a comparison to the millisecond
    would be a comparison to the codec's internals.
    """
    from vinga_server.audio.opus import OpusDecoder

    decoder = OpusDecoder(sample_rate=manifest["sample_rate"])
    pcm = b"".join(decoder.decode(packet) for packet in utterance.packaged().packets)

    # s16 mono, so two bytes a sample and no interleaving to undo.
    samples = len(pcm) // 2
    assert len(pcm) % 2 == 0
    heard_ms = samples * 1000 / manifest["sample_rate"]
    assert manifest["duration_ms"] - manifest["frame_duration_ms"] <= heard_ms
    assert heard_ms <= manifest["duration_ms"]


@pytest.mark.skipif(not HAS_AV, reason="the serve extra's codec is not installed")
def test_the_decoded_audio_is_speech_rather_than_silence(
    manifest: dict[str, object],
) -> None:
    """And that it is a sentence rather than a file of zeros, which is
    the other thing forty well-formed frames could have been.

    A board opens its microphone before a person speaks and closes it
    after, so what this asserts is the shape of an utterance and not just
    that something is loud: quiet at both ends, loud in the middle.
    """
    from vinga_server.audio.opus import OpusDecoder

    decoder = OpusDecoder(sample_rate=manifest["sample_rate"])
    pcm = b"".join(decoder.decode(packet) for packet in utterance.packaged().packets)
    loudest = _loudest_per_tenth(pcm, manifest["sample_rate"])

    assert max(loudest) > 2000, "the packets decode to something too quiet to be speech"
    assert loudest[0] < 200, "the utterance starts on a syllable, with no room to open a mic"
    assert loudest[-1] < 200, "the utterance ends on a syllable, with nothing to endpoint on"


def _loudest_per_tenth(pcm: bytes, rate: int) -> list[int]:
    """The peak sample of each tenth of a second, which is enough shape
    to tell speech from silence without a spectrum."""
    import array

    samples = array.array("h")
    samples.frombytes(pcm)
    window = rate // 10
    return [
        max(abs(sample) for sample in samples[at : at + window])
        for at in range(0, len(samples) - window, window)
    ]


# What an installation without the asset says


def test_a_build_that_carried_no_asset_refuses_by_sentence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A wheel built without the package data is a command with nothing
    to say, and what it says about that is this grammar's own sentence
    rather than a FileNotFoundError with a path in it."""
    monkeypatch.setattr(utterance, "ASSET", "utterance.that-was-never-built")

    with pytest.raises(ConfigError) as raised:
        utterance.packaged()

    assert str(raised.value) == utterance.NO_UTTERANCE


def test_a_manifest_that_is_not_one_refuses_by_sentence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(utterance, "MANIFEST", "utterance.that-was-never-built")

    with pytest.raises(ConfigError) as raised:
        utterance.packaged()

    assert str(raised.value) == utterance.NO_UTTERANCE


@pytest.mark.parametrize(
    "described",
    [
        "not json at all",
        "[]",
        json.dumps({"sentence": "", "sample_rate": 16000, "frame_duration_ms": 60}),
        json.dumps({"sentence": "hi", "sample_rate": 0, "frame_duration_ms": 60}),
        json.dumps({"sentence": "hi", "sample_rate": True, "frame_duration_ms": 60}),
        json.dumps({"sentence": "hi", "sample_rate": 16000, "frame_duration_ms": "sixty"}),
        json.dumps({"sentence": "hi", "sample_rate": 16000}),
    ],
)
def test_a_manifest_that_does_not_describe_an_utterance_refuses(described: str) -> None:
    """An asset and its description are two files, and two files can be
    edited apart. `True` is in the table because it is an `int` in
    Python and is not a sample rate."""
    with pytest.raises(ConfigError) as raised:
        utterance.understood(described.encode(), ASSET.read_bytes())

    assert str(raised.value) == utterance.UNREADABLE_UTTERANCE


@pytest.mark.parametrize("data", [b"", b"\x00\x02\x00", b"\x00\x02\x00\x00" * 8])
def test_an_asset_that_is_not_a_run_of_frames_refuses(data: bytes) -> None:
    """An empty file, a file ending inside a header, and a run of frames
    that announce nothing. Each is refused by the fixed sentence, and
    the framing exception behind it does not travel."""
    with pytest.raises(ConfigError) as raised:
        utterance.understood(MANIFEST.read_bytes(), data)

    assert str(raised.value) == utterance.UNREADABLE_UTTERANCE
    assert raised.value.__cause__ is None and raised.value.__context__ is None
