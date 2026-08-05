"""The energy endpointer (now the mock VAD), fed synthetic 60 ms chunks."""

import math
import struct

from samtal_server.audio import rms
from samtal_server.providers.mock import EnergyEndpointer

SAMPLE_RATE = 16000
CHUNK_MS = 60
CHUNK_SAMPLES = SAMPLE_RATE * CHUNK_MS // 1000

SILENCE = b"\x00\x00" * CHUNK_SAMPLES
SPEECH = b"".join(
    struct.pack("<h", int(8000 * math.sin(2 * math.pi * 300 * n / SAMPLE_RATE)))
    for n in range(CHUNK_SAMPLES)
)


def feed_ms(endpointer: EnergyEndpointer, chunk: bytes, duration_ms: int) -> list[bool]:
    return [endpointer.feed(chunk) for _ in range(duration_ms // CHUNK_MS)]


def test_rms_tells_speech_from_silence() -> None:
    assert rms(SILENCE) == 0.0
    assert rms(SPEECH) > 5000


def test_a_quiet_room_never_ends_an_utterance_that_never_began() -> None:
    endpointer = EnergyEndpointer()
    assert not any(feed_ms(endpointer, SILENCE, 30_000))


def test_speech_then_trailing_silence_ends_the_utterance() -> None:
    endpointer = EnergyEndpointer()
    assert not any(feed_ms(endpointer, SPEECH, 1200))
    decisions = feed_ms(endpointer, SILENCE, 1200)
    assert any(decisions)
    # Ended after roughly the trailing-silence window, not instantly.
    first = decisions.index(True)
    assert 600 <= (first + 1) * CHUNK_MS <= 900


def test_speech_start_is_where_the_silence_ended() -> None:
    endpointer = EnergyEndpointer()
    assert endpointer.speech_start() is None
    feed_ms(endpointer, SILENCE, 1200)
    assert endpointer.speech_start() is None
    feed_ms(endpointer, SPEECH, 600)
    assert endpointer.speech_start() == len(SILENCE) * (1200 // CHUNK_MS)
    # A later pause does not move it: the first speech is the anchor.
    feed_ms(endpointer, SILENCE, 480)
    feed_ms(endpointer, SPEECH, 120)
    assert endpointer.speech_start() == len(SILENCE) * (1200 // CHUNK_MS)
    endpointer.reset()
    assert endpointer.speech_start() is None


def test_speech_ms_counts_only_the_chunks_classified_as_speech() -> None:
    endpointer = EnergyEndpointer()
    assert endpointer.speech_ms() == 0.0
    feed_ms(endpointer, SILENCE, 1200)
    assert endpointer.speech_ms() == 0.0
    feed_ms(endpointer, SPEECH, 600)
    assert endpointer.speech_ms() == 600.0
    # A pause adds nothing; more speech accumulates.
    feed_ms(endpointer, SILENCE, 480)
    feed_ms(endpointer, SPEECH, 120)
    assert endpointer.speech_ms() == 720.0
    endpointer.reset()
    assert endpointer.speech_ms() == 0.0


def test_a_pause_shorter_than_the_window_does_not_end_it() -> None:
    endpointer = EnergyEndpointer()
    feed_ms(endpointer, SPEECH, 600)
    assert not any(feed_ms(endpointer, SILENCE, 480))
    assert not any(feed_ms(endpointer, SPEECH, 600))


def test_droning_on_hits_the_utterance_cap() -> None:
    endpointer = EnergyEndpointer()
    decisions = feed_ms(endpointer, SPEECH, 12_000)
    assert any(decisions)
    first = decisions.index(True)
    assert (first + 1) * CHUNK_MS <= 10_020


def test_reset_starts_a_fresh_utterance() -> None:
    endpointer = EnergyEndpointer()
    feed_ms(endpointer, SPEECH, 600)
    feed_ms(endpointer, SILENCE, 660)
    endpointer.reset()
    assert not any(feed_ms(endpointer, SILENCE, 6000))
