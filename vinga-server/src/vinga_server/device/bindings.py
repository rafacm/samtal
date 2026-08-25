"""Which agents a device is bound to, read while the server runs.

The domain configuration is a snapshot a reload can replace, and this is
the one thing that is not read out of it at all. Binding a device is the
act an operator performs with the device in front of them, and the
device asks again seconds later: the OTA check that issues its token and
the websocket connect that starts its conversation both have to see a
binding written a moment ago, or onboarding a board would mean asking
the server to reload for every board. That is what this reads from the
database on every lookup; everything else about a world is asked for
(#191).

So the live view is exactly the two inputs of `Config.agents_for_device`:
the `devices` rows and `default_agent` in `domain_settings`. It resolves
by the same rule, the bound list else the default agent else nothing,
and it stops there.

What it deliberately does NOT do is decide which of those names this
server can serve. That decision belongs to whoever is about to act on
it, against the one generation they are acting in: a session captures a
world after this lookup's await and classifies against exactly that
object, so a reload landing in between lands wholly before or wholly
after the conversation being built rather than in the middle of it, and
the OTA paths classify against the world current when they answer. A
classification made here would be a second read of a second generation
at a different moment, which is the race the pinned handoff exists to
close. `BoundNames.against` is the one implementation of it, so the two
callers cannot come to disagree about a rule they both apply.

A name this server is not serving is not nothing: handing such a device
a token would invite a websocket the session layer has to refuse, with
nothing said about why. What is said instead is the reload that will
install it, and the callers log that distinctly.

Three properties this component exists to keep:

- **It never blocks the event loop and never migrates.** Its engine is
  created at app build, after boot has migrated, and reads through
  repeatable-read, read-only transactions that take no advisory lock
  (see `db.read_engine`); every lookup from async code goes through
  `resolve`, which awaits it on a worker thread.
- **A failed read is loud, not fatal, and says nothing of the failure
  but its kind.** The OTA endpoint is every device's boot dependency, so
  a `/data` hiccup must not refuse the fleet's check-ins. A lookup that
  cannot read the database logs a fixed warning and resolves from the
  generation this server is serving, so staleness is in the log rather
  than in nobody's knowledge. What the exception says is not in that line: a database
  error carries the statement, its bound parameters and whatever the
  driver quoted, and this path is reachable by anything the stored
  configuration holds. Only the exception's class name is recorded, in
  a field of its own.
- **A live session is not touched.** Resolution happens at token
  issuance and at connect. Deleting a binding stops the next one of
  each; it does not reach into a conversation already happening.
"""

import asyncio
from collections.abc import Collection
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import Engine

from vinga_server.config.models import normalize_mac
from vinga_server.config.store import LiveBinding, read_live_binding
from vinga_server.db import read_engine
from vinga_server.events import ServerEvents
from vinga_server.events.catalog import BindingsUnreadable
from vinga_server.events.values import ClassName, DeviceId

if TYPE_CHECKING:
    # Deferred, and the one import here that is. `generation` reaches
    # the provider layer, and the onboarding module imports `DeviceAgents`
    # from here on a path the configuration CLI and the rendering of the
    # committed API document both take: a runtime import would put the
    # MCP SDK and every provider extra behind `vinga-server config
    # reference` (#143's weight pin). Nothing here needs the class
    # itself, only a holder to ask, so the name is a type and the
    # annotations that use it are strings.
    from vinga_server.generation import Generations

events = ServerEvents(__name__)


@dataclass(frozen=True)
class BoundNames:
    """What a device's binding says, before anything has decided which
    of it this server can act on.

    The raw answer, and raw on purpose. The names come out of the
    database (or out of the generation this server is serving, when the
    database could not be read), and the question of which of them are
    servable has a different answer in every instant and is therefore
    the caller's to ask, against the one world it is acting in. See this
    module's docstring for why that is not a convenience.
    """

    names: tuple[str, ...]
    # Whether this answer is the database's or the served world's.
    #
    # False only when a live read failed and the configuration answered
    # in its place, which is a fallback that keeps a fleet's check-ins
    # served and cannot be trusted about what it does not say. The
    # difference matters to exactly one caller: an empty resolution is
    # what the activation ceremony reads as "no operator has bound this
    # device", and from a fallback it means "this server could not
    # find out". That caller is `onboarding.unbound.activation_for`,
    # which is where the whole question of what an unbound device gets
    # is decided (issue #143); the activation poll re-reads this view
    # and deliberately ignores this flag. Issuing a token off a stale
    # answer only ever repeats what the configuration already said;
    # minting a code off one would offer a stranger a claim ticket for
    # somebody's bound board.
    #
    # A configuration with no database behind it is authoritative: the
    # world being served is then the whole truth there is, which is what
    # a test lane and an embedded server have.
    authoritative: bool = True

    def against(self, servable: Collection[str]) -> "DeviceAgents":
        """This binding, split by what one world can serve.

        `servable` is the agents of exactly one generation, and which
        generation that is is the caller's decision: the session's is
        the world it is about to build a conversation from, and the OTA
        paths' is the world current as they answer. One implementation
        of the split, here rather than at the two call sites, because
        the difference between the two lists is what both of them say
        out loud to an operator.
        """
        return DeviceAgents(
            tuple(name for name in self.names if name in servable),
            tuple(name for name in self.names if name not in servable),
            self.authoritative,
        )


@dataclass(frozen=True)
class DeviceAgents:
    """What a device resolves to in one world: the agents it may talk
    to, and the ones its binding named that this world does not serve.

    Two fields rather than one list, because the two states they tell
    apart need different sentences said to the operator: a device with
    nothing bound is missing configuration, and a device bound to an
    agent this server is not serving is waiting for the reload that
    installs it.
    """

    agents: tuple[str, ...]
    unloaded: tuple[str, ...] = ()
    authoritative: bool = True

    def __bool__(self) -> bool:
        return bool(self.agents)


class DeviceBindings:
    """The live view of the `devices` rows and `default_agent`.

    One per app, built by the composition root and disposed with it.
    Every lookup is its own read transaction: there is no cache, because
    the call rate is a device's boot check-in and its connect, and
    because a cache would need an invalidation path from every writer of
    the rows, which anything holding the file open can be. If a fleet
    ever makes this measurable, this class is the only place that has to
    change.
    """

    def __init__(self, generations: "Generations", engine: Engine | None) -> None:
        # The world this server is serving, asked rather than kept, and
        # asked for one thing only: what the two rows say when the
        # database cannot be read. A reload replaces the world while
        # this view goes on answering, so a configuration captured here
        # would become a fallback to a world that has been retired.
        self._generations = generations
        self._engine = engine

    @classmethod
    def open(cls, generations: "Generations") -> "DeviceBindings":
        """The view a server serves with: the holder whose generation is
        the fallback, and a read engine on the database boot read it
        from.

        No arm for a database that is not there. There used to be one,
        for a file a lookup could find missing; by the time bindings
        open, boot has either migrated this database or refused to
        start, so "not there" is not a state this can meet. A composed
        server that has no store at all builds `snapshot_only` instead,
        which is a decision made where the composition is rather than a
        probe made here.

        A read that fails once the server is running is a different
        thing entirely, and is still loud and not fatal: see
        `_read_binding`.
        """
        return cls(generations, read_engine(generations.current().config.server.database))

    @classmethod
    def snapshot_only(cls, generations: "Generations") -> "DeviceBindings":
        """The view with no database behind it, which resolves exactly
        what `Config.agents_for_device` resolves. What a caller handed no
        live view uses, so that resolution has one implementation rather
        than a live one and a fallback one that could come to disagree."""
        return cls(generations, None)

    @property
    def snapshot_authoritative(self) -> bool:
        """Whether there is no database behind this view, so what this
        server was handed is the whole truth there is.

        Decided once, at the open, and asked here rather than decided a
        second time somewhere else: which mode a server is in is a fact
        about how it was composed, and two places working it out is two
        places that can come to disagree. What reads it is the
        configuration API, whose comparison and whose apply both span a
        store and a running world and have nothing to span in this mode,
        and whose device writes can then say only that the write is
        stored.

        Named for what it asserts rather than for what is missing: the
        snapshot is authoritative here, which is why the answers this
        server gives about bindings are still exactly right. It is the
        answers about a STORE that have nowhere to come from.
        """
        return self._engine is None

    def dispose(self) -> None:
        """Close the connection pool. Called from the app's lifespan, so
        a server on its way out leaves no file handle behind."""
        if self._engine is not None:
            self._engine.dispose()

    async def resolve(self, mac: str) -> BoundNames:
        """`names_for`, awaited off the event loop.

        The device paths are async and the read is synchronous, so this
        is the only way they may call it: a lookup that ran inline would
        put a file open, and whatever the filesystem is doing at that
        moment, in front of every other conversation the process is
        holding.

        This await is also the moment the handoff is pinned around: a
        caller captures the generation it is going to act in AFTER this
        returns, so a reload landing during the lookup is a reload the
        caller either wholly precedes or wholly follows.
        """
        return await asyncio.to_thread(self.names_for, mac)

    def names_for(self, mac: str) -> BoundNames:
        """What this device is bound to, from the database when it can
        be read and from the world being served when it cannot.

        Names and nothing else: which of them can be served is a
        question about one generation, and the caller asks it.
        """
        normalized = normalize_mac(mac)
        stored, authoritative = self._stored(normalized)
        if stored is None:
            config = self._generations.current().config
            bound = tuple(config.devices.get(normalized, ()))
            default = config.default_agent
        else:
            bound, default = stored.agents, stored.default_agent
        names = tuple(bound) if bound else ((default,) if default is not None else ())
        return BoundNames(names, authoritative)

    def _stored(self, mac: str) -> tuple[LiveBinding | None, bool]:
        """This device's binding and the default agent as the database
        holds them, and whether the answer came from the database at
        all.

        The reading itself belongs to the repository, which is where
        what a stored row means is decided; this is the caller that says
        what to do when it cannot be read, which is to answer from the
        world being served rather than to refuse a device.

        The second half of the answer separates the two ways of having
        no row to return. No database behind this view at all is an
        ordinary state and its answer is authoritative, the world being
        served being the whole truth there is; a read that failed is
        not, and a caller that reads "nothing is bound" as a fact has to
        be able to tell the two apart.
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
        events.emit(
            lambda: BindingsUnreadable(
                device=DeviceId(mac), failure=ClassName.of(exc)
            )
        )


__all__ = ["BoundNames", "DeviceAgents", "DeviceBindings"]
