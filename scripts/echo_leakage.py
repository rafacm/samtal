"""Echo leakage measurement over vinga session captures.

For every capture in a directory (the `<session>.wav` + `<session>.jsonl`
pairs that session capture writes), cross-correlates the microphone
channel against the paced-reply channel over windows where the
assistant is audibly playing and the user is demonstrably not
speaking. The best lag of the correlation gives the acoustic echo
path: its gain is the leakage figure (dB relative to the played
reply) and its delay the path latency. Results aggregate per session
and per voice, with agent attribution from `speaking_started` events.

A null result (no window correlating above the floor, no stable lag)
means leakage is below what the ambient floor lets this method see;
the per-window detectability bound is reported for that case. Run
`echo_leakage_control.py` first on new data or after changing this
script: a measurement that cannot find a synthetic echo proves
nothing with a null result.

First used for field test round 1 (issue #48), where it found no
measurable echo path on the Waveshare board for any of three voices;
method and figures are in that issue's closing comment.

Needs numpy and scipy, which are not vinga-server dependencies:

    uv run --no-project --with numpy --with scipy \
        python scripts/echo_leakage.py /path/to/captures
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import wave
from scipy.signal import fftconvolve

RATE = 16000

# A window counts as "assistant playing" above this reply-channel
# level; quieter stretches are the gaps between sentences.
CH1_ACTIVE_DBFS = -45.0


def dbfs(x: np.ndarray) -> float:
    rms = np.sqrt(np.mean(x.astype(np.float64) ** 2))
    return 20 * np.log10(max(rms, 1e-9) / 32768.0)


def load(captures: Path, session: str):
    with wave.open(str(captures / f"{session}.wav")) as w:
        if w.getnchannels() != 2 or w.getframerate() != RATE:
            raise SystemExit(f"{session}: expected stereo {RATE} Hz capture")
        raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    mic = raw[0::2].astype(np.float64)
    ref = raw[1::2].astype(np.float64)
    events = [
        json.loads(line) for line in (captures / f"{session}.jsonl").open()
    ]
    return mic, ref, events


def user_mask(events: list[dict], n: int) -> np.ndarray:
    """Boolean per-sample mask of times the user may be speaking.

    Deliberately generous: a window is only used for measurement when
    nothing in the event track hints at user speech anywhere in it,
    because user speech correlating with nothing still raises the
    noise floor of the estimate."""
    mask = np.zeros(n, dtype=bool)

    def block(t0_ms: float, t1_ms: float) -> None:
        a = max(0, int(t0_ms / 1000 * RATE))
        b = min(n, int(t1_ms / 1000 * RATE))
        if b > a:
            mask[a:b] = True

    for e in events:
        t = e["t_ms"]
        kind = e["event"]
        if kind == "heard":
            # heard fires when transcription ends; the utterance audio
            # precedes it by duration_s plus the ASR round trip.
            block(t - e.get("duration_s", 2.0) * 1000 - 2000, t + 300)
        elif kind in ("barge_in", "barge_in_suppressed", "utterance"):
            block(t - e.get("speech_ms", 500) - 1500, t + 500)
        elif (
            kind == "vad"
            and e.get("speech_ms", 0) > 0
            and not e.get("replying", False)
        ):
            # VAD speech counts as user-speech evidence only outside a
            # reply. During playback the endpointer's speech is exactly
            # the thing under measurement on a leaky device: masking it
            # would remove the leakage windows and manufacture a null
            # result. Real user speech during a reply is masked by the
            # heard/barge_in/barge_in_suppressed events instead, which
            # the gate ladder emits for every attempt.
            block(t - e["speech_ms"] - 300, t + 300)
    return mask


def agent_at(events: list[dict], t_ms: float) -> str | None:
    agent = None
    for e in events:
        if e["t_ms"] > t_ms:
            break
        if e["event"] == "speaking_started":
            agent = e.get("agent")
        elif e["event"] == "session_open":
            agent = agent or e.get("agent")
    return agent


def analyze(
    captures: Path,
    session: str,
    win_s: float,
    hop_s: float,
    max_lag_s: float,
) -> list[dict]:
    mic, ref, events = load(captures, session)
    n = len(mic)
    umask = user_mask(events, n)
    win = int(win_s * RATE)
    hop = int(hop_s * RATE)
    lag_max = int(max_lag_s * RATE)

    mic = mic - mic.mean()
    ref = ref - ref.mean()

    rows = []
    for start in range(lag_max, n - win, hop):
        y = mic[start : start + win]
        x_ext = ref[start - lag_max : start + win]
        x_now = ref[start : start + win]
        if dbfs(x_now) < CH1_ACTIVE_DBFS:
            continue
        if umask[start : start + win].any():
            continue
        # cc[k] = dot(x_ext[k : k + win], y); the segment x_ext[k:]
        # starts at absolute sample start - lag_max + k, so lag
        # d = lag_max - k and both arrays are reversed to index by d.
        cc = fftconvolve(x_ext, y[::-1], mode="valid")
        corr = cc[::-1]
        sq = np.convolve(x_ext**2, np.ones(win), mode="valid")
        norms_sq = sq[::-1]
        ynorm = np.sqrt((y**2).sum())
        with np.errstate(divide="ignore", invalid="ignore"):
            r = corr / (np.sqrt(norms_sq) * ynorm)
        # A near-silent reference segment normalizes to garbage.
        floor = win * (32768.0 * 10 ** (CH1_ACTIVE_DBFS / 20)) ** 2
        r[norms_sq < floor] = 0.0
        r = np.nan_to_num(r)
        d = int(np.argmax(np.abs(r)))
        denom = norms_sq[d]
        g = float(corr[d] / denom) if denom > 0 else 0.0
        rows.append(
            dict(
                t_s=start / RATE,
                agent=agent_at(events, start / RATE * 1000) or "?",
                lag_ms=d / RATE * 1000,
                r=float(r[d]),
                leak_db=20 * np.log10(max(abs(g), 1e-9)),
                ref_db=dbfs(x_now),
                mic_db=dbfs(y),
            )
        )
    return rows


def summarize(all_rows: dict[str, list[dict]], r_floor: float) -> None:
    by_key: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for session, rows in all_rows.items():
        for row in rows:
            by_key[(session, row["agent"])].append(row)

    print(
        f"{'session':<10} {'agent':<8} {'wins':>4} {'echo':>4} "
        f"{'med leak dB':>11} {'med lag ms':>10} {'lag IQR':>8} "
        f"{'med r':>6} {'mic dB':>7}"
    )
    per_voice: dict[str, list[dict]] = defaultdict(list)
    bounds: dict[str, list[float]] = defaultdict(list)
    for (session, agent), rows in sorted(by_key.items()):
        echo = [r for r in rows if abs(r["r"]) >= r_floor]
        lags = np.array([r["lag_ms"] for r in echo])
        leaks = np.array([r["leak_db"] for r in echo])
        rs = np.array([abs(r["r"]) for r in echo])
        mics = np.array([r["mic_db"] for r in rows])
        for r in rows:
            # What leakage WOULD have been detectable in this window:
            # g at the correlation floor given the window's levels.
            bounds[agent].append(
                20 * np.log10(r_floor) + r["mic_db"] - r["ref_db"]
            )
        lag_iqr = (
            float(np.percentile(lags, 75) - np.percentile(lags, 25))
            if len(lags)
            else float("nan")
        )
        print(
            f"{session[:8]:<10} {agent:<8} {len(rows):>4} {len(echo):>4} "
            f"{np.median(leaks) if len(leaks) else float('nan'):>11.1f} "
            f"{np.median(lags) if len(lags) else float('nan'):>10.0f} "
            f"{lag_iqr:>8.0f} "
            f"{np.median(rs) if len(rs) else float('nan'):>6.2f} "
            f"{np.median(mics):>7.1f}"
        )
        # Only a stable lag is a real acoustic path; scattered lags
        # are chance peaks and stay out of the per-voice figure.
        if len(echo) >= 3 and lag_iqr < 50:
            per_voice[agent].extend(echo)

    print("\nPer voice, windows with a stable-lag confirmed echo path:")
    if not per_voice:
        print("  none: no echo path found above the correlation floor")
    for agent, rows in sorted(per_voice.items()):
        leaks = np.array([r["leak_db"] for r in rows])
        lags = np.array([r["lag_ms"] for r in rows])
        print(
            f"  {agent:<8} n={len(rows):>4}  "
            f"leak median {np.median(leaks):6.1f} dB  "
            f"lag median {np.median(lags):5.0f} ms"
        )

    print("\nPer voice, upper bound on undetected leakage (median, p90):")
    for agent, values in sorted(bounds.items()):
        arr = np.array(values)
        print(
            f"  {agent:<8} n={len(arr):>4}  "
            f"{np.median(arr):6.1f} dB  {np.percentile(arr, 90):6.1f} dB"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("captures", type=Path, help="directory of captures")
    parser.add_argument("--win-s", type=float, default=2.0)
    parser.add_argument("--hop-s", type=float, default=1.0)
    parser.add_argument("--max-lag-s", type=float, default=1.2)
    parser.add_argument(
        "--r-floor",
        type=float,
        default=0.10,
        help="normalized correlation below this counts as no echo",
    )
    args = parser.parse_args()

    sessions = sorted(
        p.stem
        for p in args.captures.glob("*.wav")
        if (args.captures / f"{p.stem}.jsonl").exists()
    )
    if not sessions:
        raise SystemExit(f"no capture pairs in {args.captures}")
    all_rows = {}
    for session in sessions:
        rows = analyze(
            args.captures, session, args.win_s, args.hop_s, args.max_lag_s
        )
        all_rows[session] = rows
        print(f"{session[:8]}: {len(rows)} candidate windows")
    print()
    summarize(all_rows, args.r_floor)


if __name__ == "__main__":
    main()
