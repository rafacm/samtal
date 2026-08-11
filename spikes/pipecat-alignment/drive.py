"""Runs one full exchange: the server, the simulator, and the run dir.

Measurement harness. Starts the spike pipeline on an ephemeral port,
drives it with the unmodified xiaozhi-sdk device simulator, and writes
the run's raw logs to `runs/<session>/`.

The simulator sends real speech at 16 kHz in 60 ms frames and then
paced silence for as long as the reply lasts. Both are paced on a
monotonic clock, because the sdk's own `send_silence_audio` writes as
fast as the socket accepts, which would compress the device's side of
the timeline into a fraction of the wall clock it represents.

    uv run python drive.py [--extra-pacing] [--seconds N]
"""

import spike_env  # noqa: F401  (must precede every pipecat import)

import argparse  # noqa: E402
import asyncio  # noqa: E402
import contextlib  # noqa: E402
import os  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

import uvicorn  # noqa: E402
from xiaozhi_sdk import XiaoZhiWebsocket

from pipeline import CAPTURE_RATE, create_app, load_wav
from serializer import DEVICE_SAMPLE_RATE, FRAME_MS
from tap import Recorder

HERE = Path(__file__).parent
FRAME_BYTES = DEVICE_SAMPLE_RATE * FRAME_MS // 1000 * 2
MAC = "0a:11:22:33:44:55"

SENTENCE = "Could you tell me something about the weather today?"


@contextlib.asynccontextmanager
async def serving(app):
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    )
    task = asyncio.create_task(server.serve())
    while not server.started:
        if task.done():
            task.result()
        await asyncio.sleep(0.01)
    try:
        yield server.servers[0].sockets[0].getsockname()[1]
    finally:
        # uvicorn's graceful shutdown waits for open connections, and the
        # websocket route only returns when the pipeline worker does, so
        # the wait is bounded and then forced. A run that has already
        # written its numbers must not hang on teardown.
        server.should_exit = True
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=10)
        except asyncio.TimeoutError:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


async def paced_send(client: XiaoZhiWebsocket, pcm: bytes) -> None:
    """One 60 ms frame per 60 ms of wall clock, as a device does."""
    period = FRAME_MS / 1000
    due = time.monotonic()
    for start in range(0, len(pcm) - FRAME_BYTES + 1, FRAME_BYTES):
        due += period
        await client.send_audio(pcm[start : start + FRAME_BYTES])
        await asyncio.sleep(max(0.0, due - time.monotonic()))


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--extra-pacing",
        action="store_true",
        help="add the serializer's redundant 60 ms clock (cross-check)",
    )
    parser.add_argument(
        "--seconds",
        type=float,
        default=None,
        help="truncate the reply clip, for quick runs",
    )
    parser.add_argument("--name", default=None, help="run directory name")
    args = parser.parse_args()

    utterance = load_wav(HERE / "audio" / "utterance.wav", DEVICE_SAMPLE_RATE)
    reply = load_wav(HERE / "audio" / "reply.wav", 24000)
    if args.seconds:
        reply = reply[: int(args.seconds * 24000) * 2]
    reply_secs = len(reply) / 2 / 24000

    recorder = Recorder()
    finished = asyncio.Event()
    app = create_app(
        recorder, reply, SENTENCE, paced=args.extra_pacing, finished=finished
    )

    replied = asyncio.Event()
    events: list[dict] = []

    async def on_message(data: dict) -> None:
        events.append(data)
        if data.get("type") == "tts" and data.get("state") == "stop":
            replied.set()

    async with serving(app) as port:
        client = XiaoZhiWebsocket(
            on_message,
            url=f"ws://127.0.0.1:{port}/xiaozhi/v1/",
            audio_sample_rate=DEVICE_SAMPLE_RATE,
        )
        try:
            if not await client.init_connection(MAC):
                raise SystemExit("simulator could not connect")
            await paced_send(client, utterance)
            # The composer turns this into the capture's `heard` event,
            # whose duration is what masks the user's own speech out of
            # the measurement's candidate windows.
            recorder.mark(
                "utterance", duration_s=len(utterance) / 2 / DEVICE_SAMPLE_RATE
            )
            # Silence for as long as the reply can take, so the device's
            # own stream never stops while the server is speaking.
            silence = b"\x00" * FRAME_BYTES * int((reply_secs + 20) / (FRAME_MS / 1000))
            sender = asyncio.create_task(paced_send(client, silence))
            try:
                await asyncio.wait_for(replied.wait(), timeout=reply_secs + 60)
            finally:
                sender.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await sender
            await asyncio.sleep(0.5)
            heard = sum(len(chunk) for chunk in client.output_audio_queue)
        finally:
            await client.close()
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(finished.wait(), timeout=10)

    session = args.name or next(
        e["session_id"] for e in recorder.events if e["event"] == "session_open"
    )
    run = HERE / "runs" / session
    recorder.write(run)
    (run / "session").write_text(session)

    sends = [t for t, _ in recorder.sends]
    print(f"run {session} -> {run}")
    print(f"  reply clip      : {reply_secs:.1f} s at 24000 Hz")
    print(
        "  pacing          : "
        + ("stock transport, plus the serializer's redundant 60 ms clock"
           if args.extra_pacing else "stock transport")
    )
    print(f"  tap packets     : {len(sends)}")
    if sends:
        print(f"  first send      : {sends[0]:.3f} s")
        print(f"  last send       : {sends[-1]:.3f} s")
        print(f"  send span       : {sends[-1] - sends[0]:.1f} s of wall clock")
    report_intervals(sends)
    print(f"  buffer deliveries: {len(recorder.deliveries)} at {recorder.buffer_rate} Hz")
    print(f"  simulator decoded: {heard} samples at {CAPTURE_RATE} Hz")
    print(f"  device messages : {[e.get('type') for e in events]}")

    # The run has written everything it measured. asyncio.run's own loop
    # teardown then waits on tasks that do not cancel (uvicorn's server
    # task, the pipeline worker's) and Silero's torch runtime leaves
    # non-daemon threads behind besides, so the interpreter can hang for
    # a quarter of an hour after the numbers are on disk. Exiting hard
    # from inside the loop is the difference between a harness that
    # finishes and one that has to be killed.
    sys.stdout.flush()
    os._exit(0)


def report_intervals(sends: list[float]) -> None:
    """The tap's inter-send distribution against the 60 ms frame
    cadence: gate 1 evidence, because a transport that bursts has no
    wall clock a capture can be laid out on."""
    if len(sends) < 2:
        return
    gaps = [(b - a) * 1000 for a, b in zip(sends, sends[1:])]
    gaps.sort()

    def pct(p: float) -> float:
        return gaps[min(len(gaps) - 1, int(p / 100 * len(gaps)))]

    near = sum(1 for g in gaps if abs(g - FRAME_MS) <= 5) / len(gaps)
    print(
        f"  inter-send ms   : median {pct(50):.1f}, p5 {pct(5):.1f}, "
        f"p95 {pct(95):.1f}, max {gaps[-1]:.1f}"
    )
    print(f"  within 5 ms of {FRAME_MS} ms: {near:.1%}")


if __name__ == "__main__":
    asyncio.run(main())
