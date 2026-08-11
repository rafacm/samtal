"""An MCP server over stdio that reports what its environment holds.

It exists for one assertion the other test server cannot make: that a
credential configured for an MCP server actually reaches the process
that needs it. Inspecting the manager proves nothing about that, since
the manager could stop forwarding and the inspection would still pass;
the process saying what it received is the whole delivery path.

The tool answers whether the named variable holds the expected value
rather than returning the value, so a test failure prints a boolean
instead of a credential.

Run it by path: `python tests/support/mcp_env_echo_server.py`.
"""

import os

from mcp.server.fastmcp import FastMCP

server = FastMCP("samtal-test-env-echo")


@server.tool()
def env_matches(name: str, expected: str) -> bool:
    """Whether this process's environment holds `expected` under `name`."""
    return os.environ.get(name) == expected


@server.tool()
def env_present(name: str) -> bool:
    """Whether this process's environment holds `name` at all."""
    return name in os.environ


if __name__ == "__main__":
    server.run()
