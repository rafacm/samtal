"""What each candidate reference track actually contains.

Measurement harness. The injection run in `inject.py` answers gate 1's
question end to end, but it answers it through the echo measurement, so
a failure there does not say whether the reference was misplaced in
time, corrupted in content, or both. This script separates those, by
comparing each of pipecat's two bot tracks against the audio the wire
actually carried.

The yardstick is the *uniform decode*: the tapped Opus packets decoded
and laid end to end, with no timestamps involved at all. That is what a
device plays, because a device's DAC runs on its own clock and its jitter
buffer absorbs arrival jitter, so packet arrival times do not survive
into the sound. Comparing a recording against the uniform decode
therefore isolates content fidelity from placement, and comparing the
tap's timestamp-placed track against the same yardstick prices what the
plan's placement rule costs on its own.

    uv run python fidelity.py runs/<session>
"""

import argparse
import ctypes.util
import json
import struct
import wave
from pathlib import Path

import numpy as np
from scipy.signal import fftconvolve, resample_poly

if ctypes.util.find_library("opus") is None:
    from xiaozhi_sdk.utils import setup_opus

    setup_opus()

import opuslib  # noqa: E402

CAPTURE_RATE = 16000
TAP_RATE = 24000
TAP_FRAME_MS = 60
FRAME_S = TAP_FRAME_MS / 1000


def read_packets(path: Path) -> list[bytes]:
    raw = path.read_bytes()
    packets = []
    at = 0
    while at < len(raw):
        (n,) = struct.unpack_from("<I", raw, at)
        at += 4
        packets.append(raw[at : at + n])
        at += n
    return packets


def best_lag(a: np.ndarray, b: np.ndarray) -> tuple[float, float]:
    """Best correlation of b against a, as (lag in ms, normalized r).

    The search covers every lag the two tracks admit rather than a
    window around zero. The tracks being compared start at different
    points (a raw turn buffer holds only reply audio, while a composed
    track carries the session's leading silence), so a bounded search
    would miss the true peak and report decorrelation that is really
    just an offset outside the search space.
    """
    n = min(len(a), len(b))
    a = a[:n] - a[:n].mean()
    b = b[:n] - b[:n].mean()
    cc = fftconvolve(b, a[::-1], mode="full")
    mid = len(a) - 1
    k = int(np.argmax(np.abs(cc)))
    r = cc[k] / (np.linalg.norm(a) * np.linalg.norm(b))
    return (k - mid) / CAPTURE_RATE * 1000, float(r)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run", type=Path, help="a runs/<session> directory")
    run = parser.parse_args().run

    sends = [json.loads(line) for line in (run / "tap.jsonl").open()]
    packets = read_packets(run / "tap.opus")
    decoder = opuslib.Decoder(fs=TAP_RATE, channels=1)
    frame = TAP_RATE * TAP_FRAME_MS // 1000
    decoded = [
        np.frombuffer(decoder.decode(p, frame_size=frame), dtype=np.int16)
        for p in packets
    ]
    uniform = resample_poly(np.concatenate(decoded).astype(np.float64), 2, 3)

    turn = np.frombuffer((run / "turn_bot.raw").read_bytes(), dtype=np.int16)
    bot = np.frombuffer((run / "buffer_bot.raw").read_bytes(), dtype=np.int16)
    tap16 = np.frombuffer((run / "tap16k.raw").read_bytes(), dtype=np.int16)

    sent_s = len(packets) * FRAME_S
    print(f"wire: {len(packets)} packets x {TAP_FRAME_MS} ms = {sent_s:.3f} s")
    print()
    print("content fidelity against the uniform decode of those packets:")
    for name, track in (
        ("pipecat turn track", turn),
        ("pipecat delivered (buffer) bot track", bot),
        ("the tap, placed by send timestamp", tap16),
    ):
        lag, r = best_lag(uniform, track.astype(np.float64))
        print(f"  {name:38s} lag {lag:+8.1f} ms   r {r:.3f}")

    print()
    print("track lengths against the audio actually sent:")
    print(
        f"  turn track      {len(turn) / CAPTURE_RATE:8.3f} s "
        f"({len(turn) / CAPTURE_RATE - sent_s:+.3f} s)"
    )
    print(
        f"  delivered track {len(bot) / CAPTURE_RATE:8.3f} s "
        f"({len(bot) / CAPTURE_RATE - sent_s:+.3f} s)"
    )

    # Silence the delivered track carries inside otherwise continuous
    # reply audio, which is what the cross-track padding inserts.
    nonzero = np.nonzero(bot)[0]
    gaps = np.diff(nonzero)
    blocks = gaps[gaps > 1000] - 1
    print(
        f"  delivered track interior silence: {len(blocks)} blocks over "
        f"1000 samples, {blocks.sum() / CAPTURE_RATE:.2f} s total"
    )
    if len(blocks):
        sizes, counts = np.unique(blocks, return_counts=True)
        top = sorted(zip(counts, sizes), reverse=True)[:3]
        print(
            "  most common block sizes: "
            + ", ".join(
                f"{s} samples ({s / CAPTURE_RATE * 1000:.2f} ms) x{c}" for c, s in top
            )
        )

    # How far the real send times stray from a perfect frame clock. This
    # is what the plan's per-packet placement rule writes into the tap
    # track, and what no uniform recording can reproduce.
    t = np.array([s["t"] for s in sends])
    deviation = (t - (t[0] + np.arange(len(t)) * FRAME_S)) * 1000
    intervals = np.diff(t) * 1000
    wire_s = t[-1] - t[0]
    nominal_s = (len(t) - 1) * FRAME_S
    print()
    print("the wire clock against a perfect 60 ms frame clock:")
    print(
        f"  per-interval jitter : sd {intervals.std():.2f} ms, "
        f"min {intervals.min():.2f}, max {intervals.max():.2f}"
    )
    print(
        f"  accumulated offset  : {deviation[-1]:+.1f} ms over {wire_s:.1f} s "
        f"({deviation[-1] / wire_s * 60:+.1f} ms per minute)"
    )
    print(
        f"  audio sent in       : {wire_s:.3f} s of wall clock for "
        f"{nominal_s:.3f} s of audio"
    )

    # What the injection runs actually see: how far each composed
    # reference channel sits from the composed tap track they are
    # measured against. A constant bias here is the whole of the lag
    # bias those runs report, and it is measured rather than derived.
    print()
    print("composed reference against the composed tap track:")
    tap_f = tap16.astype(np.float64)
    for name, directory in (
        ("delivered (buffer) track", "captures"),
        ("turn track", "captures-turn"),
    ):
        session = (run / "session").read_text().strip()
        with wave.open(str(run / directory / f"{session}.wav")) as w:
            raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        lag, r = best_lag(tap_f, raw[1::2].astype(np.float64))
        print(f"  {name:26s} lag {lag:+8.1f} ms   r {r:.3f}")

    # The turn track's placement anchor, which is the constant offset the
    # injection runs measure as a lag bias.
    turns = [json.loads(line) for line in (run / "turn.jsonl").open()]
    if turns:
        stamp = turns[0]["t"]
        start = stamp - turns[0]["samples"] / CAPTURE_RATE
        wire_start = sends[0]["t"] - FRAME_S
        print()
        print("turn track placement, by the plan's rule (ends at its stamp):")
        print(f"  delivered at        {stamp:8.3f} s")
        print(f"  last wire send at   {sends[-1]['t']:8.3f} s")
        print(f"  stamp minus send    {(stamp - sends[-1]['t']) * 1000:+8.1f} ms")
        print(f"  track starts at     {start:8.3f} s")
        print(f"  wire audio starts   {wire_start:8.3f} s")
        print(f"  placement bias      {(start - wire_start) * 1000:+8.1f} ms")


if __name__ == "__main__":
    main()
