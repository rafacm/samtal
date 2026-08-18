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
cycle; those readers name it under `TYPE_CHECKING` for the same reason
`config/api.py` and `registry.py` defer their own.
"""

from dataclasses import dataclass

from samtal_server.auth import DeviceAuth
from samtal_server.capture import CaptureStore, DeviceFacts
from samtal_server.config import Config
from samtal_server.config.api import ApiRuntime
from samtal_server.conversations import ConversationStore
from samtal_server.device.bindings import DeviceBindings
from samtal_server.device.boundary import RuntimeFactory
from samtal_server.filler import AgentFillers
from samtal_server.onboarding import PendingDevices
from samtal_server.providers import AgentProviders
from samtal_server.registry import SessionRegistry
from samtal_server.tools.mcp import McpServers
from samtal_server.tools.memory import MemoryStore


@dataclass
class Composition:
    """One server's resources, built by the composition root and hung on
    `app.state.composition`, which is the only thing on that state bag.

    Mutable in the language and immutable by convention: it is written
    where it is built, and outside that by two tests only. The standing
    one is the runtime-factory injection in
    `tests/unit/test_boundary_contract.py`, which replaces
    `runtime_factory` on a built app to drive the device boundary against
    a scripted runtime. That seam is why this is a plain dataclass rather
    than a frozen one.

    The second is temporary: `tests/unit/test_conversations_boot.py`
    replaces `conversations` with a store whose `start()` fails, to prove
    the lifespan's teardown still runs. It writes here only while
    construction is still synchronous; once the store is built inside the
    lifespan it patches the constructor instead, and this paragraph goes
    with it.

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
