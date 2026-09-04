"""A session whose turns are recorded, and the store that keeps them.

The content channel is separate from the event tap (#120): what a turn
holds goes to the conversation store rather than onto the events, so a
suite asking what was recorded needs a session with a store behind it
and a stand-in where the store will stand.

Built on `sessions.py` rather than beside it: the session is the one
every other suite builds, with two substitutions this file owns. The
spy is the producer half the runtime is given, session id and all, so
the binding the factory does is exercised rather than assumed; the
speaking stub drains a real synthesis so the measurements a record
carries are real ones, and paces no audio, so a reply takes as long as
its scripts do rather than as long as its playback.

Nothing here asserts about a record's contents. `only_record` says how
many there were, because a suite reading `records[0]` of an empty list
fails with an index rather than with a sentence.
"""

import asyncio
from typing import Any, cast

from tests.support.configs import POET_MAC, base_config
from tests.support.providers import ScriptedLlm
from tests.support.sessions import session_for, with_device
from tests.support.sockets import QuietSocket
from vinga_server.config import Config
from vinga_server.conversations.records import TurnRecord
from vinga_server.device.session import DeviceSession
from vinga_server.memory.store import MemoryStore
from vinga_server.tools.mcp import McpServers


class SpyStore:
    """Where the store will stand, keeping what it is handed.

    It implements the producer half the runtime is given, session id and
    all, so the binding the factory does is exercised rather than assumed.
    """

    def __init__(self) -> None:
        self.records: list[tuple[str, TurnRecord]] = []

    def record_turn(self, session_id: str, record: TurnRecord) -> None:
        self.records.append((session_id, record))


class Speaking:
    """The reply's speaking step, stubbed down to what these tests need.

    The synthesis is drained rather than abandoned, so the first-audio
    measurement is a real one taken off a real provider; nothing reaches
    a device, so no audio is paced and a reply takes as long as its
    scripts do."""

    def __init__(self) -> None:
        self.said: list[str] = []
        self.spoke = asyncio.Event()

    async def __call__(self, synthesis: Any, resampler: Any, into: list[str]) -> None:
        async for _ in synthesis.chunks():
            pass
        into.append(synthesis.sentence)
        self.said.append(synthesis.sentence)
        self.spoke.set()


def speaking_session(
    conversations: Any,
    config: Config | None = None,
    mac: str = POET_MAC,
    scripts: dict[str, ScriptedLlm] | None = None,
    memory: MemoryStore | None = None,
    mcp_servers: McpServers | None = None,
    stages: dict[str, Any] | None = None,
) -> tuple[DeviceSession, Speaking]:
    """A session whose speaking is stubbed down to what these tests
    need, on a known device, built the way every other session in these
    suites is.

    The stub is white-box and the only reach-in this file keeps. What a
    recorded turn holds is decided sentence by sentence as each one's
    synthesis finishes, and the public route to a spoken sentence is the
    audio the device is paced: running it would make every one of these
    tests wait out a real reply's playback to read a field about how the
    reply was recorded. `Speaking` drains the same synthesis a device
    would, so the measurements a record carries are real ones off a real
    provider, and nothing is paced.
    """
    session = session_for(
        config if config is not None else base_config(),
        mac,
        scripts,
        memory=memory,
        websocket=cast(Any, QuietSocket()),
        mcp_servers=mcp_servers,
        conversations=conversations,
        stages=stages,
    )
    with_device(session, mac)
    speaking = Speaking()
    session.runtime._speak = speaking  # type: ignore[method-assign]
    return session, speaking


def recording_session(
    config: Config | None = None,
    mac: str = POET_MAC,
    scripts: dict[str, ScriptedLlm] | None = None,
    memory: MemoryStore | None = None,
    mcp_servers: McpServers | None = None,
    stages: dict[str, Any] | None = None,
) -> tuple[DeviceSession, SpyStore, Speaking]:
    """A session whose turns are recorded, built the way every other
    session in these suites is."""
    spy = SpyStore()
    session, speaking = speaking_session(spy, config, mac, scripts, memory, mcp_servers, stages)
    return session, spy, speaking


def only_record(spy: SpyStore) -> TurnRecord:
    assert len(spy.records) == 1, f"expected one record, got {len(spy.records)}"
    return spy.records[0][1]
