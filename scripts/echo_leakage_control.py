"""Positive control for the echo leakage measurement.

Takes one real capture, injects a synthetic echo (the reply channel,
delayed and attenuated, added into the microphone channel), and runs
the measurement on the result. The known echo must come back at the
injected delay and gain in the overwhelming majority of windows;
otherwise the measurement is broken and any null result from
`echo_leakage.py` proves nothing. Run this on new data and after any
change to the measurement.

Round 1 reference: a -30 dB echo at 250 ms injected into the test 1
capture was detected in 112 of 116 windows with lag and gain exact.

    uv run --no-project --with numpy --with scipy \
        python scripts/echo_leakage_control.py /path/to/captures <session>
"""

import argparse
import tempfile
import wave
import shutil
from pathlib import Path

import numpy as np

from echo_leakage import RATE, analyze, load


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("captures", type=Path)
    parser.add_argument("session", help="session id (capture file stem)")
    parser.add_argument("--delay-ms", type=float, default=250.0)
    parser.add_argument("--gain-db", type=float, default=-30.0)
    parser.add_argument("--r-floor", type=float, default=0.10)
    args = parser.parse_args()

    mic, ref, _ = load(args.captures, args.session)
    delay = int(args.delay_ms / 1000 * RATE)
    gain = 10 ** (args.gain_db / 20)
    mic = mic.copy()
    mic[delay:] += gain * ref[: len(ref) - delay]

    with tempfile.TemporaryDirectory() as tmp:
        injected = Path(tmp)
        interleaved = np.empty(2 * len(mic), dtype=np.int16)
        interleaved[0::2] = np.clip(mic, -32768, 32767).astype(np.int16)
        interleaved[1::2] = np.clip(ref, -32768, 32767).astype(np.int16)
        with wave.open(str(injected / "control.wav"), "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(interleaved.tobytes())
        shutil.copy(
            args.captures / f"{args.session}.jsonl",
            injected / "control.jsonl",
        )
        rows = analyze(injected, "control", 2.0, 1.0, 1.2)

    hits = [r for r in rows if abs(r["r"]) >= args.r_floor]
    lags = np.array([r["lag_ms"] for r in hits])
    leaks = np.array([r["leak_db"] for r in hits])
    print(
        f"injected {args.gain_db:.0f} dB at {args.delay_ms:.0f} ms: "
        f"detected in {len(hits)}/{len(rows)} windows"
    )
    if hits:
        print(
            f"measured: lag median {np.median(lags):.0f} ms, "
            f"leak median {np.median(leaks):.1f} dB"
        )
    detected = len(rows) and len(hits) / len(rows) >= 0.9
    lag_ok = len(hits) > 0 and abs(np.median(lags) - args.delay_ms) < 20
    gain_ok = len(hits) > 0 and abs(np.median(leaks) - args.gain_db) < 3
    if detected and lag_ok and gain_ok:
        print("control PASSED: the measurement sees what is there to see")
    else:
        raise SystemExit(
            "control FAILED: do not trust a null result from this "
            "measurement on this data"
        )


if __name__ == "__main__":
    main()
