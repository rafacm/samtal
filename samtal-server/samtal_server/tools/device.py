"""The device's own tools, discovered over the conversation socket.

One client per session, because the tools belong to the board on the
other end: its speaker, its screen, its battery. The device is the MCP
server here and this the client, so the conversation is the client's
half of the handshake (initialize, the initialized notification, then
paginated tools/list) followed by tools/call whenever the model asks
for one.

Discovery runs as a background task so a first utterance never waits on
it: an utterance that races discovery simply runs without device tools,
and the next one has them. A device that never answers costs a warning
and nothing else.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from samtal_server.protocol import mcp
from samtal_server.providers import ToolDef
from samtal_server.tools import names

logger = logging.getLogger(__name__)

# How long one handshake request may wait. The firmware answers in
# milliseconds; this only bounds the case where it never answers at all.
REQUEST_TIMEOUT_S = 10.0

# A pagination guard. Devices list a handful of tools; a cursor that
# never empties is a bug on the far side, not a long list.
MAX_TOOL_PAGES = 20


class DeviceToolClient:
    """The MCP conversation with one connected device."""

    def __init__(
        self,
        send: Callable[[dict[str, Any]], Awaitable[None]],
        label: str,
        client_name: str,
        client_version: str,
    ) -> None:
        self._send = send
        self._label = label
        self._client_name = client_name
        self._client_version = client_version
        self._next_id = 0
        self._pending: dict[int, asyncio.Future[mcp.Response]] = {}
        self._tools: list[ToolDef] = []
        # Sanitized name back to the name the device listed, which is
        # what a call has to carry.
        self._originals: dict[str, str] = {}
        self.discovered = False

    def tools(self) -> list[ToolDef]:
        """The device's tools under names the LLM APIs accept. Empty
        until discovery has completed."""
        return list(self._tools)

    def knows(self, name: str) -> bool:
        return name in self._originals

    async def discover(self) -> None:
        """Run the handshake and list the tools. Never raises: a device
        that will not talk MCP is a device without tools, not a broken
        conversation."""
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT_S * (MAX_TOOL_PAGES + 2)):
                await self._request(mcp.initialize_request(
                    self._take_id(), self._client_name, self._client_version
                ))
                await self._send(mcp.initialized_notification())
                self._adopt(await self._list_tools())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("%s: no device tools (%s)", self._label, exc)
        finally:
            self.discovered = True

    async def _list_tools(self) -> list[mcp.DeviceTool]:
        listed: list[mcp.DeviceTool] = []
        cursor = ""
        for _ in range(MAX_TOOL_PAGES):
            response = await self._request(mcp.tools_list_request(self._take_id(), cursor))
            page, cursor = mcp.parse_tools_page(response.result or {})
            listed += page
            if not cursor:
                return listed
        logger.warning("%s: tools/list did not stop paginating; keeping what arrived", self._label)
        return listed

    def _adopt(self, listed: Sequence[mcp.DeviceTool]) -> None:
        """Publish the listed tools under sanitized names, dropping the
        ones the LLM APIs cannot express. First listed wins a collision,
        which makes the outcome the same on every run."""
        tools: list[ToolDef] = []
        originals: dict[str, str] = {}
        for tool in listed:
            name = names.sanitize(tool.name)
            if len(name) > names.MAX_TOOL_NAME_LENGTH:
                logger.warning(
                    "%s: dropping device tool %s, its name is longer than %d characters",
                    self._label,
                    tool.name,
                    names.MAX_TOOL_NAME_LENGTH,
                )
                continue
            if name in originals:
                logger.warning(
                    "%s: dropping device tool %s, it sanitizes to %s like %s",
                    self._label,
                    tool.name,
                    name,
                    originals[name],
                )
                continue
            originals[name] = tool.name
            tools.append(
                ToolDef(name=name, description=tool.description, input_schema=tool.input_schema)
            )
        self._tools = tools
        self._originals = originals
        logger.info(
            "%s: %d device tool(s): %s",
            self._label,
            len(tools),
            ", ".join(tool.name for tool in tools) or "none",
        )

    async def call(self, name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        """Run one device tool, under the name the device listed it as.
        The caller bounds how long this may take: xiaozhi-sdk ignores a
        tools/call for a name it does not know without answering, so a
        call that is never answered has to be a timeout somewhere."""
        original = self._originals.get(name)
        if original is None:
            raise KeyError(f'the device has no tool called "{name}"')
        response = await self._request(
            mcp.tools_call_request(self._take_id(), original, arguments), timeout=None
        )
        if response.failed:
            return f"the device refused the call: {response.error}", True
        return mcp.parse_tool_result(response.result or {})

    def handle(self, payload: dict[str, Any]) -> None:
        """Route one incoming `mcp` payload. Device-initiated
        notifications carry no id and get no reply."""
        try:
            response = mcp.parse_response(payload)
        except mcp.McpProtocolError as exc:
            logger.warning("%s: unusable MCP payload: %s", self._label, exc)
            return
        if response is None:
            logger.debug("%s: device MCP notification: %s", self._label, payload.get("method"))
            return
        waiting = self._pending.pop(response.id, None)
        if waiting is None:
            logger.debug("%s: MCP response %d matches no request", self._label, response.id)
            return
        if not waiting.done():
            waiting.set_result(response)

    def close(self) -> None:
        """Give up on anything still in flight, so a disconnect does not
        leave a call waiting for a device that has gone."""
        for waiting in self._pending.values():
            if not waiting.done():
                waiting.cancel()
        self._pending.clear()

    def _take_id(self) -> int:
        self._next_id += 1
        return self._next_id

    async def _request(
        self, payload: dict[str, Any], timeout: float | None = REQUEST_TIMEOUT_S
    ) -> mcp.Response:
        request_id = payload["id"]
        waiting: asyncio.Future[mcp.Response] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = waiting
        try:
            await self._send(payload)
            async with asyncio.timeout(timeout):
                response = await waiting
        finally:
            self._pending.pop(request_id, None)
        if response.failed and payload["method"] != "tools/call":
            raise mcp.McpProtocolError(f'{payload["method"]} failed: {response.error}')
        return response
