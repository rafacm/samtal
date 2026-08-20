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
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field

from vinga_server.config import Config
from vinga_server.config.secrets import SecretStore
from vinga_server.filler import FillerClips


@dataclass(frozen=True)
class Generation:
    """One world this server serves: a validated configuration snapshot,
    the stored credentials loaded beside it, and the filled pauses
    synthesized for it.

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

    `config` carries the file half as well as the domain half, and that
    is not an accident of composition: a reload never re-reads the file,
    so `config.server` is the section this process started with and is
    the same object in every generation. A caller that needs a
    restart-only setting may therefore read it here without asking which
    generation it got.

    Frozen, and the whole point: a generation is never edited. A change
    is a new one, built entirely before anything binds it, so a refusal
    has touched nothing that is running.
    """

    config: Config
    secrets: SecretStore
    fillers: Mapping[str, FillerClips] = field(default_factory=dict)


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
        # How many applies have settled. Counted rather than timed: an
        # instant cannot say that two reads saw the same world, because
        # two applies can land inside a clock's resolution.
        self._settled = 0
        # And whether one is changing serving state right now, which is
        # the half a counter cannot carry. See `mark`.
        self._applying = False

    def current(self) -> Generation:
        """The world a piece of work starting now should bind.

        A method rather than an attribute because reading it is the act:
        what it answers is a fact about the instant it is called, and a
        caller that stored the answer in a field has bound that
        generation whether it meant to or not.
        """
        return self._current

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
    def applying(self) -> Iterator[Install]:
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
        """
        self._applying = True
        try:
            yield self._install
        finally:
            self._settled += 1
            self._applying = False

    def _install(self, generation: Generation) -> None:
        """The swap: one assignment, no await."""
        self._current = generation


__all__ = ["Generation", "Generations", "Install"]
