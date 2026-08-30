"""Everything the model can reach beyond speaking.

Three sources merge into one tool list per reply: the builtins the
server implements itself (`builtin`), the device's own MCP tools
discovered over the websocket (`device`), and the MCP servers an agent
is configured with (`mcp`). `names` holds the namespace rules that keep
the three from colliding.

The store behind the `remember` builtin is not here. It owns a schema,
its migrations and its engines, which makes it a domain concept rather
than a tool helper, so it lives in `vinga_server.memory` and the
builtin calls the store it is handed (#314).

Submodules are imported by path rather than re-exported here, because
`names` is read by the configuration layer and must stay free of
imports that would lead back to it.
"""
