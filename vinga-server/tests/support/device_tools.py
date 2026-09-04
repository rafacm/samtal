"""The far side of the device MCP tool channel.

A board that advertises tools is an MCP server the session talks to
over the same websocket it speaks audio on. What belongs here is the
board's half of that conversation: a stand-in that answers
`initialize`, `tools/list` and `tools/call` from a script, on the next
turn of the loop the way a real device would, plus the tool
declarations a suite hands it to list.

This is the device as the tool client sees it, which is a different
seam from the device as a runtime sees it: that one is `boundary.py`'s
`FakeDevice`, and merging the two would invent a fake with two jobs.
"""

import asyncio
from typing import Any

from vinga_server.tools.device import DeviceToolClient


class FakeDevice:
    """A device that answers `initialize`, `tools/list`, and
    `tools/call`, from a script the test hands it."""

    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self.pages = pages
        self.sent: list[dict[str, Any]] = []
        self.answered: list[str] = []
        self.client = DeviceToolClient(self.receive, "test", "vinga-server", "0.1.0")
        self.call_results: dict[str, dict[str, Any]] = {}
        self.silent_methods: set[str] = set()

    async def receive(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)
        method = payload.get("method")
        if method is None or method.startswith("notifications/"):
            return
        if method in self.silent_methods:
            return
        self.answered.append(method)
        # Answer on the next loop turn, the way a real device would.
        asyncio.get_running_loop().call_soon(self._answer, payload)

    def _answer(self, payload: dict[str, Any]) -> None:
        method = payload["method"]
        if method == "initialize":
            result: dict[str, Any] = {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "board", "version": "2.4.0"},
            }
        elif method == "tools/list":
            result = self.pages.pop(0) if self.pages else {"tools": []}
        else:
            result = self.call_results.get(
                payload["params"]["name"],
                {"content": [{"type": "text", "text": "true"}], "isError": False},
            )
        self.client.handle({"jsonrpc": "2.0", "id": payload["id"], "result": result})


STATUS = {"name": "self.get_device_status", "description": "The board's state"}

# The board tool with a typed argument, which is what a suite about
# argument types needs and what the client suite already listed. One
# schema rather than two: a copy beside the session suite would be a
# second declaration of the same tool, free to drift from this one.
VOLUME = {
    "name": "self.audio_speaker.set_volume",
    "description": "Set the speaker volume",
    "inputSchema": {"type": "object", "properties": {"volume": {"type": "integer"}}},
}
