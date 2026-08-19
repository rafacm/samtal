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
from vinga_server.config import Config
from vinga_server.config.api import ApiRuntime
from vinga_server.conversations import ConversationStore
from vinga_server.device.bindings import DeviceBindings
from vinga_server.device.boundary import RuntimeFactory
from vinga_server.filler import AgentFillers
from vinga_server.onboarding import PendingDevices
from vinga_server.providers import AgentProviders
from vinga_server.registry import SessionRegistry
from vinga_server.tools.mcp import McpServers
from vinga_server.tools.memory import MemoryStore


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
    None with device authentication off, `memory` without a memory
    section, `conversations` unless recording is on, `capture` unless
    capture is configured and enabled.
    """

    config: Config
    device_auth: DeviceAuth | None
    bindings: DeviceBindings
    pending: PendingDevices
    mcp_servers: McpServers
    memory: MemoryStore | None
    sessions: SessionRegistry
    agent_providers: dict[str, AgentProviders]
    agent_fillers: AgentFillers
    conversations: ConversationStore | None
    runtime_factory: RuntimeFactory
    device_facts: DeviceFacts
    capture: CaptureStore | None
    api: ApiRuntime
