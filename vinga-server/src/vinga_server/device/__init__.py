"""The device edge: everything that would still exist if the backend
were a telephone call to a human.

The xiaozhi websocket protocol, Opus framing, outgoing frame pacing,
session capture, device authentication's consequences, the device's own
MCP tools, and the appliance policy (limits, the idle watchdog, the
shutdown) live here. What a conversation is made of does not; that is
`vinga_server.runtime`, reached only through
[`boundary`](boundary.py).
"""
