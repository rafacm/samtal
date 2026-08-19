"""A stdio MCP server that writes where a well-behaved one does not.

Two channels a child process owns and this server does not: its own
stderr, and whatever bytes it puts on its stdout before the protocol
starts. A careless server logs its credential at startup; a broken one
emits something that is not a JSON-RPC message at all, which the SDK
answers with `logger.exception` and a traceback quoting the bytes.

Neither may reach what a deployment collects, so this one does both on
purpose, with the credential its environment gave it. The handshake
still completes afterwards, because what is under test is the boundary
and not the failure: a manager that never connected would prove nothing
about what a connected one lets through.

Run it by path: `python tests/support/mcp_noisy_server.py`.
"""

import os
import sys

from mcp.server.fastmcp import FastMCP

# The same delivery path a real entry uses, and the same variable name
# the reflecting server takes its value from.
REFLECTED_ENV = "VINGA_TEST_REFLECTED"

reflected = os.environ.get(REFLECTED_ENV, "no credential was configured")

# Straight to the process's stderr, which the SDK's default would have
# been this server's own.
print(f"starting up with {reflected}", file=sys.stderr, flush=True)

# And a line of stdout that is not a JSON-RPC message, which is what
# makes the client log a parse failure with the bytes in it.
print(f'{{"not": "a jsonrpc message", "held": "{reflected}"}}', flush=True)

server = FastMCP("vinga-test-noisy")


@server.tool()
def forecast() -> str:
    """The forecast, so this server has something to publish."""
    return "sunny"


if __name__ == "__main__":
    server.run()
