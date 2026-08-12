"""Which agents a device may talk to, read while the server runs.

The configuration is a boot-time snapshot, and this is the one
deliberate hole in it. Binding a device is the act an operator performs
with the device in front of them, and the device asks again seconds
later: the OTA check that issues its token and the websocket connect
that starts its conversation both have to see a binding written a moment
ago, or onboarding a board would mean restarting the server for every
board. Nothing else is live. Providers, agents, MCP servers and the rest
stay what boot loaded, and a write to any of them still says so.

So the live view is exactly the two inputs of `Config.agents_for_device`:
the `devices` rows and `default_agent` in `domain_settings`. It resolves
by the same rule (the bound list, else the default agent, else nothing)
and then drops names the boot snapshot never loaded, because a binding
written after boot can name an agent whose providers were never built:
handing such a device a token would invite a websocket the session layer
has to refuse, with nothing said about why. What is said instead is the
restart that will load it, and the two callers log that distinctly.

Three properties this component exists to keep:

- **It never blocks the event loop and never migrates.** Its engine is
  created at app build, after boot has migrated, and reads through
  ordinary deferred transactions (see `db.read_engine`); every lookup
  from async code goes through `resolve`, which awaits it on a worker
  thread.
- **A failed read is loud, not fatal, and says nothing of the failure
  but its kind.** The OTA endpoint is every device's boot dependency, so
  a `/data` hiccup must not refuse the fleet's check-ins. A lookup that
  cannot read the database logs a fixed warning and resolves from the
  boot snapshot, so staleness is in the log rather than in nobody's
  knowledge. What the exception says is not in that line: a database
  error carries the statement, its bound parameters and whatever the
  driver quoted, and this path is reachable by anything the stored
  configuration holds. Only the exception's class name is recorded, in
  a field of its own.
- **A live session is not touched.** Resolution happens at token
  issuance and at connect. Deleting a binding stops the next one of
  each; it does not reach into a conversation already happening.
"""

import asyncio
import logging
from dataclasses import dataclass

from sqlalchemy import Engine

from samtal_server.config import Config
from samtal_server.config.models import normalize_mac
from samtal_server.config.store import LiveBinding, read_live_binding
from samtal_server.db import DATABASE_FILENAME, read_engine

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeviceAgents:
    """What a device resolves to: the agents it may talk to, and the
    ones its binding named that this server cannot serve.

    Two fields rather than one list, because the two states they tell
    apart need different sentences said to the operator: a device with
    nothing bound is missing configuration, and a device bound to an
    agent this server has not loaded is waiting for a restart.
    """

    agents: tuple[str, ...]
    unloaded: tuple[str, ...] = ()
    # Whether this answer is the database's or the boot snapshot's.
    #
    # False only when a live read failed and the snapshot answered in
    # its place, which is a fallback that keeps a fleet's check-ins
    # served and cannot be trusted about what it does not say. The
    # difference matters to exactly one caller: an empty resolution is
    # what the activation ceremony reads as "no operator has bound this
    # device", and from a fallback it means "this server could not
    # find out". Issuing a token off a stale answer only ever repeats
    # what boot already decided; minting a code off one would offer a
    # stranger a claim ticket for somebody's bound board.
    #
    # A configuration with no database behind it is authoritative: the
    # snapshot is then the whole truth there is, which is what a test
    # lane and an embedded server have.
    authoritative: bool = True

    def __bool__(self) -> bool:
        return bool(self.agents)


class DeviceBindings:
    """The live view of the `devices` rows and `default_agent`.

    One per app, built by the composition root and disposed with it.
    Every lookup is its own read transaction: there is no cache, because
    the call rate is a device's boot check-in and its connect, and
    because a cache would need an invalidation path from the two write
    paths (the API and `--local`) that the second of them could not
    reach at all. If a fleet ever makes this measurable, this class is
    the only place that has to change.
    """

    def __init__(self, config: Config, engine: Engine | None) -> None:
        self._config = config
        self._engine = engine
        # What boot actually built providers for. Membership of this set
        # is what separates an agent a device can be handed from a name
        # only the database knows.
        self._loaded = frozenset(config.agents)

    @classmethod
    def open(cls, config: Config) -> "DeviceBindings":
        """The view a server serves with: the composed snapshot, and a
        read engine on the database boot read it from.

        A database that is not there is not an error here. It is what a
        test that composed its configuration in memory has, and the
        honest view of it is the snapshot itself, said once at build
        rather than warned about at every lookup.
        """
        path = config.server.database.dir / DATABASE_FILENAME
        if not path.exists():
            logger.debug(
                "no configuration database at %s: device bindings resolve from the "
                "configuration this server was built with",
                path,
                extra={"event": "device_bindings_snapshot_only", "path": str(path)},
            )
            return cls(config, None)
        return cls(config, read_engine(config.server.database.dir))

    @classmethod
    def snapshot_only(cls, config: Config) -> "DeviceBindings":
        """The view with no database behind it, which resolves exactly
        what `Config.agents_for_device` resolves. What a caller handed no
        live view uses, so that resolution has one implementation rather
        than a live one and a fallback one that could come to disagree."""
        return cls(config, None)

    def dispose(self) -> None:
        """Close the connection pool. Called from the app's lifespan, so
        a server on its way out leaves no file handle behind."""
        if self._engine is not None:
            self._engine.dispose()

    async def resolve(self, mac: str) -> DeviceAgents:
        """`agents_for`, awaited off the event loop.

        The device paths are async and the read is synchronous, so this
        is the only way they may call it: a lookup that ran inline would
        put a file open, and whatever the filesystem is doing at that
        moment, in front of every other conversation the process is
        holding.
        """
        return await asyncio.to_thread(self.agents_for, mac)

    def agents_for(self, mac: str) -> DeviceAgents:
        """The agents this device may talk to, from the database when it
        can be read and from the boot snapshot when it cannot."""
        normalized = normalize_mac(mac)
        stored, authoritative = self._stored(normalized)
        if stored is None:
            bound = tuple(self._config.devices.get(normalized, ()))
            default = self._config.default_agent
        else:
            bound, default = stored.agents, stored.default_agent
        names = list(bound) if bound else ([default] if default is not None else [])
        return DeviceAgents(
            tuple(name for name in names if name in self._loaded),
            tuple(name for name in names if name not in self._loaded),
            authoritative,
        )

    def _stored(self, mac: str) -> tuple[LiveBinding | None, bool]:
        """This device's binding and the default agent as the database
        holds them, and whether the answer came from the database at
        all.

        The reading itself belongs to the repository, which is where
        what a stored row means is decided; this is the caller that says
        what to do when it cannot be read, which is to answer from the
        boot snapshot rather than to refuse a device.

        The second half of the answer separates the two ways of having
        no row to return. No database behind this view at all is an
        ordinary state and its answer is authoritative, the snapshot
        being the whole truth there is; a read that failed is not, and
        a caller that reads "nothing is bound" as a fact has to be able
        to tell the two apart.
        """
        if self._engine is None:
            return None, True
        problem: Exception | None = None
        try:
            return read_live_binding(self._engine, mac), True
        # Deliberately everything. This is the fleet's boot dependency,
        # and the point of the fallback is that whatever went wrong with
        # the file or with what is in it, the device still gets the
        # answer boot would have given it. A row that cannot be
        # understood is included on purpose: reading it as "bound to
        # nothing" would refuse a device over a fact nobody established.
        # What must not be silent is that it happened.
        except Exception as exc:
            problem = exc
        self._warn(mac, problem)
        return None, False

    def _warn(self, mac: str, exc: Exception) -> None:
        """The fallback, said out loud and in fixed words.

        Nothing of the exception is rendered but its class name. A
        database error is not a sentence somebody wrote for a log: a
        DBAPI error carries the statement that failed and the parameters
        bound to it, a driver message can quote the file path or the
        value it choked on, and this warning is written on a path
        anything in the stored configuration can reach. The class name
        is a code identifier, which is the most that can be said here
        that a stored value could not have written, and it goes in a
        structured field rather than into the sentence so the sentence
        is the same string every time.
        """
        logger.warning(
            "cannot read the device bindings for %s; answering from the configuration "
            "this server started with, which may be older than the database. The "
            "failure's kind is recorded beside this line",
            mac,
            extra={
                "event": "device_bindings_unreadable",
                "device": mac,
                "failure": type(exc).__name__,
            },
        )


__all__ = ["DeviceAgents", "DeviceBindings"]
