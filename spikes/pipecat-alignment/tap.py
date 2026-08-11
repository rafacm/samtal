"""The wire tap and the recorder that shares its clock.

Measurement harness, not adapter code.

The tap is a proxy around FastAPI's `WebSocket` that the transport is
constructed with, so it needs no pipecat internals and no subclass. It
timestamps each outgoing Opus packet **immediately after the awaited
`send_bytes` returns**, which is the latest observable boundary:
downstream of serialization, of the framework's queueing, and of
whatever pacing exists. A tap at serialization would call audio "sent"
that had not left, which is the mistake the spike exists to catch.

Everything the recorder holds is stamped on one monotonic clock, read
once before recording starts, and nothing is ever shifted afterwards.
"""

import json
import time
from pathlib import Path


class Recorder:
    """One monotonic epoch and the two timestamped streams on it."""

    def __init__(self) -> None:
        self.epoch = time.monotonic()
        # (t, opus payload) per outgoing audio packet.
        self.sends: list[tuple[float, bytes]] = []
        # (t, sample count, user pcm, bot pcm) per buffer delivery.
        self.deliveries: list[tuple[float, int, bytes, bytes]] = []
        # (t, bot pcm) per completed bot turn: the other track pipecat
        # offers, delivered once when the bot stops speaking.
        self.turns: list[tuple[float, bytes]] = []
        self.buffer_rate: int | None = None
        self.events: list[dict] = []

    def now(self) -> float:
        return time.monotonic() - self.epoch

    def mark(self, event: str, **fields: object) -> None:
        self.events.append({"event": event, "t": self.now(), **fields})

    def on_send(self, payload: bytes) -> None:
        self.sends.append((self.now(), payload))

    def on_delivery(self, user: bytes, bot: bytes, rate: int) -> None:
        self.buffer_rate = rate
        self.deliveries.append((self.now(), len(bot) // 2, user, bot))

    def on_turn(self, bot: bytes) -> None:
        self.turns.append((self.now(), bot))

    def write(self, run: Path) -> None:
        """The raw logs, kept beside the capture so a disputed lag can be
        re-derived from them rather than argued about."""
        run.mkdir(parents=True, exist_ok=True)
        with (run / "tap.jsonl").open("w") as f:
            for t, payload in self.sends:
                f.write(json.dumps({"t": t, "bytes": len(payload)}) + "\n")
        with (run / "tap.opus").open("wb") as f:
            for _, payload in self.sends:
                f.write(len(payload).to_bytes(4, "little") + payload)
        with (run / "buffer.jsonl").open("w") as f:
            for t, n, user, bot in self.deliveries:
                f.write(
                    json.dumps(
                        {
                            "t": t,
                            "samples": n,
                            "user_bytes": len(user),
                            "rate": self.buffer_rate,
                        }
                    )
                    + "\n"
                )
        with (run / "buffer_user.raw").open("wb") as f:
            for _, _, user, _ in self.deliveries:
                f.write(user)
        with (run / "buffer_bot.raw").open("wb") as f:
            for _, _, _, bot in self.deliveries:
                f.write(bot)
        with (run / "turn.jsonl").open("w") as f:
            for t, bot in self.turns:
                f.write(json.dumps({"t": t, "samples": len(bot) // 2}) + "\n")
        with (run / "turn_bot.raw").open("wb") as f:
            for _, bot in self.turns:
                f.write(bot)
        (run / "events.json").write_text(json.dumps(self.events, indent=2))


class TappedWebSocket:
    """Delegates everything to the real websocket, timestamping the
    binary sends after they return."""

    def __init__(self, websocket, recorder: Recorder) -> None:
        self._websocket = websocket
        self._recorder = recorder

    def __getattr__(self, name):
        return getattr(self._websocket, name)

    async def send_bytes(self, data: bytes) -> None:
        await self._websocket.send_bytes(data)
        self._recorder.on_send(data)

    async def send_text(self, data: str) -> None:
        await self._websocket.send_text(data)
