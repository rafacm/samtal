"""Which world new work binds to, and whether it is holding still.

A running server used to serve exactly what it booted on, and the one
sentence every module could rely on was that the configuration is a
boot-time snapshot. Applying a stored change without a restart does not
make that sentence false; it makes it narrower. The snapshot is still
immutable and still validated whole. What changes is that there can be
more than one of them over the life of a process, and that a piece of
work binds one of them at its own convergence point rather than
inheriting the only one there was (#191).

So this module owns two facts and nothing else. A `Generation` is one
such world: the configuration to serve, the stored credentials that open
behind it, and what was built from the two of them, frozen together
because a configuration and what was made from it are one state and
holding them apart is how two loads come to disagree. `Generations` is
where the current one is found and where the next one is put, and it is
the only place a swap happens.

The second half of that second fact is the end of a world rather than
its beginning, and it is here for the same reason: a swap is also a
retirement, and something has to know when a world nobody serves and
nobody holds may let go of the engines it was speaking through. That
rule lives here, and the counting it needs does not: who still holds a
world is the session registry's knowledge, so it is passed in and never
kept.

The mark beside it exists because reading two things across an await is
not the same as reading one world. An apply changes serving state more
than once (the generation, then the MCP install), so a counter advanced
only at the end would leave a window in which a reader sees a moved
world under an unmoved number. The holder reads unstable for the whole
of an apply instead, from before its first change until after its last,
and a reader that captures the mark either side of an await has to treat
"unstable" and "moved" the same way: neither says the world held still.

Deletion test: inlined into `app.py` the composition root would own the
swap and retirement rules, and inlined into the runtime the conversation
layer would own a server-wide lifecycle. Both are decisions with exactly
one home, and this is it.
"""

import contextlib
import threading
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from dataclasses import dataclass, field

from vinga_server.config import Config
from vinga_server.config.secrets import SecretStore
from vinga_server.filler import FallbackClip, FillerClips
from vinga_server.providers import Provider, ProviderWorld, disposed


@dataclass(frozen=True, eq=False)
class Generation:
    """One world this server serves: a validated configuration snapshot,
    the stored credentials loaded beside it, and the speech synthesized
    for it ahead of time.

    The first two travel together for the reason `config.boot.BootConfig`'s
    do: the credentials are needed exactly where the configuration is
    turned into running things, and a generation whose secrets came from
    a different load would build one world's providers with another
    world's keys. Nothing here holds plaintext.

    `fillers` is here for the same reason one step further on. A clip is
    a configured phrase spoken by a configured voice, so it is a
    consequence of this world and not of this process, and putting it
    anywhere else would be a second place that has to agree with the
    configuration above it. A session binds the mapping at its
    construction, which is what makes the convergence point the next
    session: a conversation's masking does not change under it
    mid-turn. Empty is a deployment where no agent masks its latency,
    which is the default.

    `fallbacks` is the other kind of clip the same module builds, here
    for exactly the reasons `fillers` is and bound at the same instant:
    what a failed reply says is a configured phrase in a configured
    voice, and a conversation says the phrase it opened with. Empty is a
    world built before anything synthesized, which is what a test that
    is not about failure phrases hands in; a served world holds one per
    agent that has not switched the section off.

    `providers` is here for the reason the clips are, one step further
    again: the engines a conversation speaks through are what this
    world's provider entries resolved to, and an entry an apply rewrote
    is a different object. A session binds the world at its
    construction, so a rebuilt engine reaches the next conversation and
    never the middle of one. The objects are shared with the worlds that
    still use them: an entry whose model and stored credentials did not
    move is carried over rather than built again, so two generations can
    hold the same provider and the last of them to be let go of is what
    closes it.

    `config` carries the file half as well as the domain half, and that
    is not an accident of composition: a reload never re-reads the file,
    so `config.server` is the section this process started with and is
    the same object in every generation. A caller that needs a
    restart-only setting may therefore read it here without asking which
    generation it got.

    Frozen, and the whole point: a generation is never edited. A change
    is a new one, built entirely before anything binds it, so a refusal
    has touched nothing that is running.

    Compared by identity rather than by value, which is what a world is:
    two generations built from the same configuration are two worlds,
    holding two sets of engines, and telling them apart is the whole of
    what the lifecycle below does.
    """

    config: Config
    secrets: SecretStore
    fillers: Mapping[str, FillerClips] = field(default_factory=dict)
    providers: ProviderWorld = field(default_factory=ProviderWorld)
    fallbacks: Mapping[str, FallbackClip] = field(default_factory=dict)


# What one apply is handed to put its world in place with: the swap
# itself, and nothing else. It exists only inside `Generations.applying`,
# which is what makes "no generation is installed outside an apply's
# instability window" a property of the type rather than a rule someone
# has to remember.
type Install = Callable[[Generation], None]


class Generations:
    """The generation new work binds, and the mark that says whether it
    is holding still.

    One per running server, built by the composition root and read at
    every convergence point: a session's activation reads it for the
    prompt it assembles, the inspection reads read it for what they
    preview, and the stored-versus-running comparison reads it for the
    half of itself that is running.

    Everything that replaces a generation goes through `applying`, so
    there is one door in and it is the door that holds the mark
    unstable.
    """

    def __init__(self, first: Generation) -> None:
        self._current = first
        # Every agent rename this process has published, oldest first,
        # and where each world this server still has joined that list.
        #
        # The third fact this module owns, and it is here because it is
        # the same fact the two above are: what a world is, and when it
        # began and ended. A conversation binds a world before it awaits
        # the device's hello and speaks that world's names until it ends,
        # so whether a name it hands the record is one a rename moved is
        # a question about when its world was installed. Nothing else
        # knows that instant: the writer sees a session register several
        # awaits later, and by then a rename may have come and gone.
        #
        # A list and a watermark rather than a composed map per world,
        # because composing is a rule with one home and it is the
        # conversation store's: what a reader takes from here is the
        # renames its world has not heard, in publication order, and the
        # store folds them exactly as it folds the ones that arrive
        # afterwards.
        #
        # The list is append-only for the life of the process, and the
        # bound is what an entry costs against what makes one: two agent
        # names, once per rename an operator performs. What must not
        # accumulate is the association with a world, and that goes when
        # the world does, in `dispose` below.
        self._renames: list[tuple[str, str]] = []
        self._known: dict[Generation, int] = {first: 0}
        # Renames arrive on a request thread and everything else here
        # runs on the loop, so the two statements that must agree (the
        # append, and the watermark an install stamps) take this.
        self._ledger = threading.Lock()
        # How many applies have settled. Counted rather than timed: an
        # instant cannot say that two reads saw the same world, because
        # two applies can land inside a clock's resolution.
        self._settled = 0
        # And whether one is changing serving state right now, which is
        # the half a counter cannot carry. See `mark`.
        self._applying = False
        # The worlds this server has stopped serving and has not
        # finished with. A generation goes in here at the instant it
        # stops being current and comes out when it is disposed of,
        # which is as soon as nothing holds it: usually the same
        # instant, since most applies land with no conversation open on
        # a provider they replaced.
        self._retired: list[Generation] = []

    def current(self) -> Generation:
        """The world a piece of work starting now should bind.

        A method rather than an attribute because reading it is the act:
        what it answers is a fact about the instant it is called, and a
        caller that stored the answer in a field has bound that
        generation whether it meant to or not.
        """
        return self._current

    def renamed(self, old: str, new: str) -> None:
        """One agent answers to another name now.

        Told to this from wherever the rename was published, on that
        caller's thread and inside the order that covers the instant
        between the write's commit and its announcement, which is what
        makes a conversation opening right now see either all of this
        rename or none of it.

        Recorded against every world at once rather than only the
        current one, because a world that has stopped being current can
        still have a conversation binding it: an apply that lands while
        a device is connecting leaves that conversation on the world it
        captured, and that world has not heard this either. A world
        installed after this line has heard it by construction, since
        its configuration is what the rename wrote.
        """
        with self._ledger:
            self._renames.append((old, new))

    def renames_for(self, generation: Generation) -> tuple[tuple[str, str], ...]:
        """What this world has not heard: every rename published since it
        was installed, oldest first.

        What a conversation bound to it needs in order to file its rows
        under the names its agents answer to now, while going on speaking
        as the names this world knows. Empty is the ordinary answer, and
        it is what a world installed by an apply since the last rename
        has.

        A world this has never stamped answers with everything, which is
        the honest reading of a generation from outside this holder: it
        is older than every rename here or it is not this server's, and
        translating too much is the safe direction. Nothing built by a
        running server takes that arm.
        """
        with self._ledger:
            return tuple(self._renames[self._known.get(generation, 0) :])

    def watermark(self) -> int:
        """Where the rename ledger stands now, for a caller about to read
        the stored configuration.

        The number a world built from that read is installed with, and it
        is taken by the reader rather than by the install for a reason
        the install cannot fix. A world reflects exactly the renames that
        had committed when its snapshot was taken, and the install
        happens long afterwards: the providers are built, the speech is
        synthesized, and either a rename lands in that interval (a world
        stamped at the install would be told it knows a rename its
        snapshot predates) or one commits without having published yet (a
        world stamped at the install would be told it does not know a
        rename its snapshot already carries). Both are wrong in opposite
        directions and neither is visible from here.

        So the reader takes this while it holds the order a rename holds
        across its commit AND its publication, which is what makes the
        pair exact: a rename is then wholly before this reading or wholly
        after it, and never half of each.
        """
        with self._ledger:
            return len(self._renames)

    @property
    def mark(self) -> int | None:
        """Which world is installed, or None while one is being replaced.

        Cheap, and read on the loop the apply runs on: a caller
        composing an answer across an await captures this before and
        reads it again after, and an equal, non-None pair is the whole
        of what says the world it read is still the world running.

        None is the instability window, and it is why the answer is not
        a bare counter. An apply changes serving state at more than one
        point, so between the generation swap and the MCP install a
        counter advanced only at the end would still read as the world
        that has already gone. A caller therefore has to compare with
        `is not None` and never by equality alone: two None samples are
        two different unstable moments, not one steady world.
        """
        return None if self._applying else self._settled

    @contextlib.contextmanager
    def applying(self, known: int | None = None) -> Iterator[Install]:
        """Everything one apply changes about what new work binds, held
        unstable from before its first change until after its last.

        The block is entered before the first serving-state change and
        left after the last one, and the settled mark advances once when
        it does, whatever happened inside: an apply that got as far as
        changing serving state has moved the world, and a reader waiting
        for it to hold still is waiting for this block to end rather
        than for the request that opened it to succeed.

        The swap is yielded rather than offered as a method, so a
        generation cannot be installed outside the window that describes
        it. It is one assignment with no await in it, which is what
        makes the reads on the other side of it see one world or the
        other and never half of each.

        `known` is where the rename ledger stood when this world's
        configuration was read, which the reader took with `watermark()`
        under the order that makes it exact. It is a parameter rather
        than something the install works out because the install cannot:
        by the time it runs, the snapshot is old and the ledger has
        moved for reasons that say nothing about what this world
        contains. None is a caller that read no store, which is a test
        and the first world of a process, and it means the ledger as it
        stands.
        """
        self._applying = True
        try:
            yield lambda generation: self._install(generation, known)
        finally:
            self._settled += 1
            self._applying = False

    def _install(self, generation: Generation, known: int | None = None) -> None:
        """The swap: one assignment, no await.

        The world that was current is retired in the same statement,
        which is what makes "a generation nothing is serving and nobody
        holds" a state this class can see rather than a thing an apply
        has to remember to say.

        And the new world joins the rename ledger here, at the place its
        own snapshot was read at: what it has not heard is what was
        published after that reading, which is nothing at all for a
        world built from a configuration every rename so far had already
        written.
        """
        with self._ledger:
            self._known[generation] = len(self._renames) if known is None else known
        self._retired.append(self._current)
        self._current = generation

    async def dispose(self, held: Collection[Generation] = ()) -> None:
        """Let go of every retired world that nobody is using any more.

        `held` is who still has one: the generations that live
        conversations bound and have not let go of. It is passed in
        rather than counted here because the whole session set is the
        session registry's knowledge and duplicating it would be a
        second count to keep in step; what is here is the rule, which is
        the thing with exactly one home.

        Called at both moments the answer can change, which is why it
        takes no argument saying which: an apply, where a world has just
        stopped being current, and the end of a session, where a world
        may have just stopped being held. A world that is neither
        current nor held goes now, and one that is held goes when its
        last conversation ends.

        A provider is closed only if no world that is still around holds
        it. Reuse hands one object to several generations, so the
        retiring one's engines are compared, by identity, against
        everything the current world, the held worlds and the other
        retired worlds are speaking through: what is left is what this
        world was the last owner of.

        Never refuses. It runs after the serving state has already
        moved, so there is nothing left for a failure to refuse, and
        what a teardown did is not a thing an apply's answer can depend
        on.
        """
        # Taken out of the list before the first await, so that two
        # disposals running at once cannot both take the same world, and
        # let go of together rather than one at a time, so that an
        # engine two retiring worlds share is closed once.
        keeping = {id(generation) for generation in held}
        letting_go = [
            generation for generation in self._retired if id(generation) not in keeping
        ]
        self._retired = [
            generation for generation in self._retired if id(generation) in keeping
        ]
        # And what these worlds had not heard goes with them. Nothing can
        # bind a world nobody is serving and nobody holds, so its place
        # in the rename ledger is state about a conversation that can no
        # longer begin, and keeping it would hold the world itself alive
        # in a dictionary for the life of the process.
        with self._ledger:
            for generation in letting_go:
                self._known.pop(generation, None)
        await disposed(self._last_held_by(letting_go))

    async def aclose(self) -> None:
        """The end of the process: let go of everything, held or not.

        Registered on the lifespan's exit stack behind the drain, so
        what it meets is a server whose conversations have been asked to
        finish. Whatever is still holding a retired world at that point
        has run out of time rather than earned an extension, and the
        current world is nobody's to keep either: a close that ran at
        every end but this one would be a lifecycle with a hole in it
        exactly where every process eventually goes.
        """
        await self.dispose()
        await disposed(self._current.providers.instances.values())

    def _last_held_by(self, retiring: Sequence[Generation]) -> list[Provider]:
        """The engines these worlds were the last to hold, once each.

        Identity rather than equality throughout: two entries built from
        the same options are two objects with two connection pools, and
        an object carried over from one world to the next is one object
        in two worlds. What survives is what nothing still around is
        speaking through.
        """
        kept = {
            id(provider)
            for generation in (self._current, *self._retired)
            for provider in generation.providers.instances.values()
        }
        return list(
            {
                id(provider): provider
                for generation in retiring
                for provider in generation.providers.instances.values()
                if id(provider) not in kept
            }.values()
        )


__all__ = ["Generation", "Generations", "Install"]
