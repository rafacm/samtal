"""A simulated board, for trying a deployment with no hardware in the room.

The protocol a board speaks is written down in this repository already:
`docs/xiaozhi-notes.md` records the exchange and `vinga_server.protocol`
models it. What stands between an operator and a conversation is not
knowledge, it is hardware, and this package removes that.

`board` is the device-side half: the simulated board's identity, the
check-in it makes and the closed reading of what it is answered.
`capabilities` is the honest statement of what this simulator does and
does not do, declared once and rendered into the help so that nobody
debugs it believing it is a board.

This file carries this docstring and nothing else. It exports no names:
the grammar imports the concrete modules by name
(`from vinga_server.simulator import board`), because a package
`__init__` that forwards names adds a name, hides nothing, and makes a
reader open two files where one would have done. It has a second cost
here as well, which is why the rule is worth stating rather than
following silently: the conversation half M2 adds is the only module in
this tree that imports `websockets`, and an `__init__` re-exporting it
would drag that import into every import of this package, which is the
extra's gate defeating itself.
"""
