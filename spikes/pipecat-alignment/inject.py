"""Gate 1: the tap-injection alignment run over a composed pair.

Measurement harness. It simulates the production echo path (wire, to
air, to microphone) against the reference an adopted pipecat would
offer: the microphone channel gets the *tap's* audio, delayed and
attenuated, while the reference channel stays the *buffer processor's*
bot track. If pipecat's own recording is aligned with what the wire
actually carried, the measurement recovers the injected delay and gain;
a constant lag bias names a fixed offset, scatter or detection loss
names drift or jitter.

The analysis is the repository's own `scripts/echo_leakage.py`, run
unmodified as a subprocess, and its output is printed verbatim. The
extra statistics the plan fixes (Theil-Sen drift slope, the
first-versus-last-quartile lag difference, and whether any lag landed on
the search boundary) are computed by importing that same unmodified
script's `analyze` and re-running it over the same injected pair.

`--max-lag-s` defaults to 2.0 rather than the script's own 1.2, because
a 1500 ms echo is outside a 1.2 s search space entirely and would be
reported as a broken measurement rather than a measured one.

    uv run python inject.py runs/<session> --delay-ms 250
"""

import argparse
import json
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
from scipy.stats import theilslopes

HERE = Path(__file__).parent
REPO = HERE.parent.parent
SCRIPTS = REPO / "scripts"

RATE = 16000


def build(run: Path, delay_ms: float, gain_db: float) -> tuple[Path, str]:
    """The injected pair, written where the analysis can be pointed at
    it. Only the microphone channel differs from the composed pair."""
    session = (run / "session").read_text().strip()
    with wave.open(str(run / "captures" / f"{session}.wav")) as w:
        raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    mic = raw[0::2].astype(np.float64)
    ref = raw[1::2].astype(np.float64)
    tap = np.frombuffer((run / "tap16k.raw").read_bytes(), dtype=np.int16)
    tap = tap[: len(mic)].astype(np.float64)

    delay = int(delay_ms / 1000 * RATE)
    if not 0 < delay < len(tap):
        raise SystemExit(f"--delay-ms {delay_ms:.0f} is outside this capture")
    mic = mic.copy()
    mic[delay:] += 10 ** (gain_db / 20) * tap[: len(tap) - delay]

    out = run / f"injected-{int(delay_ms)}ms"
    out.mkdir(exist_ok=True)
    interleaved = np.empty(2 * len(mic), dtype=np.int16)
    interleaved[0::2] = np.clip(mic, -32768, 32767).astype(np.int16)
    interleaved[1::2] = np.clip(ref, -32768, 32767).astype(np.int16)
    with wave.open(str(out / f"{session}.wav"), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(interleaved.tobytes())
    shutil.copy(
        run / "captures" / f"{session}.jsonl", out / f"{session}.jsonl"
    )
    return out, session


def extras(out: Path, session: str, max_lag_s: float, r_floor: float) -> None:
    """The per-window statistics the plan fixes, from the same
    unmodified analysis the subprocess just ran."""
    sys.path.insert(0, str(SCRIPTS))
    from echo_leakage import analyze  # the repository's script, unmodified

    rows = analyze(out, session, 2.0, 1.0, max_lag_s)
    hits = [r for r in rows if abs(r["r"]) >= r_floor]
    if not hits:
        print("\nno window correlated above the floor: no drift to report")
        return
    t = np.array([r["t_s"] for r in hits])
    lag = np.array([r["lag_ms"] for r in hits])
    order = np.argsort(t)
    t, lag = t[order], lag[order]
    q = max(1, len(lag) // 4)
    slope, *_ = theilslopes(lag, t)
    # One sample of the lag grid; a lag within that of the search edge
    # means the search was too narrow, not that the lag was measured.
    grid_ms = 1000 / RATE
    boundary = int(np.sum(lag >= max_lag_s * 1000 - grid_ms))

    print(f"\ndrift and boundary, over {len(hits)} detected windows:")
    print(f"  span               : {t[0]:.0f} s to {t[-1]:.0f} s")
    print(f"  Theil-Sen slope    : {slope * 60:+.2f} ms per minute")
    print(f"  first quartile lag : {np.median(lag[:q]):.1f} ms")
    print(f"  last quartile lag  : {np.median(lag[-q:]):.1f} ms")
    print(f"  quartile difference: {np.median(lag[-q:]) - np.median(lag[:q]):+.1f} ms")
    print(f"  lag IQR            : {np.percentile(lag, 75) - np.percentile(lag, 25):.1f} ms")
    print(f"  at search boundary : {boundary} of {len(hits)}")
    detectable = abs(slope * 60) * (t[-1] - t[0]) / 60
    print(f"  movement over span : {detectable:.1f} ms (bar: under 20 ms)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run", type=Path, help="a runs/<session> directory")
    parser.add_argument("--delay-ms", type=float, default=250.0)
    parser.add_argument("--gain-db", type=float, default=-30.0)
    parser.add_argument("--max-lag-s", type=float, default=2.0)
    parser.add_argument("--r-floor", type=float, default=0.10)
    args = parser.parse_args()

    out, session = build(args.run, args.delay_ms, args.gain_db)
    print(
        f"injected the tap at {args.gain_db:.0f} dB, {args.delay_ms:.0f} ms "
        f"into {out}"
    )
    print(f"running scripts/echo_leakage.py --max-lag-s {args.max_lag_s}\n")
    subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "echo_leakage.py"),
            str(out),
            "--max-lag-s",
            str(args.max_lag_s),
            "--r-floor",
            str(args.r_floor),
        ],
        check=True,
    )
    extras(out, session, args.max_lag_s, args.r_floor)


if __name__ == "__main__":
    main()
