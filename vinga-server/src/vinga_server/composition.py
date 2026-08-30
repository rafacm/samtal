"""What one running server is made of, as one typed object.

The composition root assembles these pieces and every device-facing
handler reads them back: the websocket endpoint takes six of them to
open a conversation, the OTA endpoint five to answer a check-in, the
drain one to reach the live sessions. They used to be thirteen loose
attributes on `app.state`, which is a namespace with no declaration:
what was on it, and what type each one had, could only be learned by
reading the function that set them (#142).

This module holds the declaration and nothing else. It imports only
downward, towards the things it names, and never `ws`, `ota` or `app`,
so a reader can import the type for an annotation without an import
cycle. `ws` and `ota` name it at module scope on the strength of that,
and they are the evidence it holds; both used to defer it under
`TYPE_CHECKING` instead, and both could stop once the onboarding module
no longer reached back to `ota` for a router (issue #143), which is what
put this module in their import path at all.
"""

from dataclasses import dataclass

from vinga_server.auth import DeviceAuth
from vinga_server.capture import CaptureStore, DeviceFacts
from vinga_server.config import ServerConfig
from vinga_server.config.api import ApiRuntime
from vinga_server.conversations import ConversationStore
from vinga_server.device.bindings import DeviceBindings
from vinga_server.device.boundary import RuntimeFactory
from vinga_server.events.live import LiveEvents
from vinga_server.generation import Generations
from vinga_server.memory import MemoryStore
from vinga_server.onboarding import PendingDevices
from vinga_server.registry import SessionRegistry
from vinga_server.tools.mcp import McpServers


@dataclass
class Composition:
    """One server's resources, built by the lifespan and hung on
    `app.state.composition`, which is the whole of what a handler reads
    back off that state bag. The only other thing on it is the private
    seed the describe phase leaves for the build (`app.py`), which no
    handler reads and which holds no resource.

    Mutable in the language and immutable by convention: it is written
    where it is built, and outside that by one test only, the
    runtime-factory injection in
    `tests/unit/test_boundary_contract.py`, which replaces
    `runtime_factory` on the composition inside an entered lifespan, to
    drive the device boundary against a scripted runtime. That seam is
    why this is a plain dataclass rather than a frozen one.

    Every optional field means the same thing it meant as an attribute:
    None is a deployment that did not ask for the thing. `device_auth` is
    None with device authentication off, `conversations` unless
    recording is on, `capture` unless capture is configured and enabled.
    `memory` is not among them: remembered facts live in a schema this
    server migrates at every boot (#314), so there is always a store,
    and an agent that has been told nothing reads as empty.

    Two fields say where configuration comes from, and the split is the
    point (#191). `server` is the file half: the port, the limits, the
    directories, the barge-in tuning, everything this process read once
    at startup and will read again only at the next one. `generations`
    is the domain half, which a reload can replace while the process
    runs, so it is a holder to ask rather than a snapshot to keep: a
    handler that captured what it answers would be serving a world this
    server may have stopped serving. There is deliberately no whole
    `Config` here any more, no filler clips and no engines either, for
    one reason: a second copy of anything a generation owns would be a
    stale one the moment an apply lands.
    """

    server: ServerConfig
    generations: Generations
    device_auth: DeviceAuth | None
    bindings: DeviceBindings
    pending: PendingDevices
    mcp_servers: McpServers
    memory: MemoryStore
    sessions: SessionRegistry
    conversations: ConversationStore | None
    runtime_factory: RuntimeFactory
    device_facts: DeviceFacts
    capture: CaptureStore | None
    # Everyone watching this server's events right now (#342). Not
    # optional, and it is the one field that is a resource nothing
    # configures: a hub with no subscribers costs a lock per event, and
    # a deployment does not ask for the ability to tail its own server
    # any more than it asks for its log. It rides the composition
    # because two of its three reaches are here (the device edge builds
    # a session with it, the shutdown closes it), and the third is the
    # API runtime beside it.
    live: LiveEvents
    api: ApiRuntime
