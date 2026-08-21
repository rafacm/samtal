"""The holder every convergence point reads, and the mark that says
whether it is holding still.

Three properties and nothing else, because the holder is three
sentences: what `current()` answers is the object that was installed and
not a copy of it, the settled mark advances once per apply whatever
happened inside, and the mark reads as nothing at all while an apply is
changing serving state. The last one is the whole reason the mark is not
a bare counter: an apply changes more than one thing, and a reader that
sampled a counter either side of an await would find it unmoved over a
window in which the world had already gone.
"""

import pytest

from tests.support.configs import config_with
from vinga_server.config.secrets import SecretStore
from vinga_server.generation import Generation, Generations
from vinga_server.providers import Provider, ProviderWorld


def generation(prompt: str) -> Generation:
    return Generation(
        config_with(agents={"assistant": {"prompt": prompt}}), SecretStore()
    )


def test_the_holder_answers_the_generation_it_was_built_with() -> None:
    first = generation("A")

    assert Generations(first).current() is first


def test_the_swap_installs_the_object_it_was_given() -> None:
    """Identity, not equality: a generation is what a session binds, so
    a holder that handed out a copy would have every reader holding a
    world nothing else can be compared against."""
    holder = Generations(generation("A"))
    next_world = generation("B")

    with holder.applying() as install:
        install(next_world)

    assert holder.current() is next_world


def test_the_mark_advances_once_per_apply() -> None:
    """Once, and per apply rather than per change: an apply that put two
    halves of a world in place moved the world once, and a reader is
    waiting for it to be one world again rather than counting the
    halves."""
    holder = Generations(generation("A"))
    settled = holder.mark

    with holder.applying() as install:
        install(generation("B"))
        install(generation("C"))

    assert holder.mark == settled + 1


def test_a_holder_nothing_applies_to_never_moves() -> None:
    holder = Generations(generation("A"))

    assert holder.mark == holder.mark
    assert holder.current() is holder.current()


def test_the_mark_is_unstable_for_the_whole_of_an_apply() -> None:
    """From before the first serving-state change until after the last,
    which is what makes the window cover the swap rather than begin at
    it."""
    holder = Generations(generation("A"))
    inside: list[int | None] = []

    with holder.applying() as install:
        inside.append(holder.mark)
        install(generation("B"))
        inside.append(holder.mark)

    assert inside == [None, None]
    assert holder.mark is not None


def test_an_apply_that_raises_still_settles_the_mark() -> None:
    """An apply that got as far as changing serving state has moved the
    world, and a reader waiting for it to hold still is waiting for the
    window to end rather than for the request that opened it to
    succeed."""
    holder = Generations(generation("A"))
    settled = holder.mark
    replacement = generation("B")

    with pytest.raises(RuntimeError):  # noqa: PT012 - the block is the subject
        with holder.applying() as install:
            install(replacement)
            raise RuntimeError("teardown said something")

    assert holder.mark == settled + 1
    assert holder.current() is replacement


def test_two_unstable_samples_are_not_one_steady_world() -> None:
    """The rule a reader has to follow, stated as the property that
    makes it necessary: `mark == mark` is true of two moments inside two
    different applies, so a guard compares `is not None` as well."""
    holder = Generations(generation("A"))

    with holder.applying() as install:
        install(generation("B"))
        first = holder.mark
    with holder.applying() as install:
        install(generation("C"))
        second = holder.mark

    assert first == second
    assert first is None


# What a retired world lets go of
#
# The other half of the holder, and the half a session suite cannot
# prove: which engines a world was the last to hold. The provider here
# is a fake with a count on it, because "was this closed" is not a
# question a real client or a loaded model answers.


class Held(Provider):
    """One engine, and how many times it was told its world was over."""

    egress = False

    def __init__(self, name: str) -> None:
        self.name = name
        self.closes = 0

    async def close(self) -> None:
        self.closes += 1


def world_of(**engines: Held) -> ProviderWorld:
    """A generation's engines, keyed by the entry each came from. The
    per-agent half is empty on purpose: nothing here holds a
    conversation, and what a disposal reads is the entries."""
    return ProviderWorld(agents={}, instances=dict(engines))


def serving(**engines: Held) -> Generation:
    return Generation(
        config_with(agents={"assistant": {"prompt": "A"}}),
        SecretStore(),
        providers=world_of(**engines),
    )


async def test_a_world_retired_with_nobody_holding_it_goes_at_once() -> None:
    """The common case by far: an apply lands, no conversation is open
    on an engine it replaced, and the old one is released in the same
    breath rather than at some later sweep."""
    voice = Held("old")
    holder = Generations(serving(**{"tts.voice": voice}))

    with holder.applying() as install:
        install(serving(**{"tts.voice": Held("new")}))
    await holder.dispose()

    assert voice.closes == 1


async def test_an_engine_the_next_world_carried_over_is_not_closed() -> None:
    """Reuse hands one object to two worlds, so retiring the first must
    not close what the second is speaking through. The unchanged entry
    is the same object in both; only the one that was rebuilt goes."""
    carried, replaced = Held("carried"), Held("replaced")
    holder = Generations(serving(**{"llm.mock": carried, "tts.voice": replaced}))

    with holder.applying() as install:
        install(serving(**{"llm.mock": carried, "tts.voice": Held("new")}))
    await holder.dispose()

    assert (carried.closes, replaced.closes) == (0, 1)


async def test_a_world_a_conversation_still_holds_keeps_its_engines() -> None:
    """The other reason to wait: a conversation speaks through the world
    it was built from for the rest of its life, so what it is holding is
    not this apply's to release."""
    voice = Held("old")
    first = serving(**{"tts.voice": voice})
    holder = Generations(first)

    with holder.applying() as install:
        install(serving(**{"tts.voice": Held("new")}))
    await holder.dispose(held=[first])

    assert voice.closes == 0

    # And when the last conversation on it ends, it goes.
    await holder.dispose(held=[])
    assert voice.closes == 1


async def test_an_engine_two_retired_worlds_shared_is_closed_once() -> None:
    """Three worlds and one engine carried through two of them: what is
    closed is what nothing still around holds, once, however many
    retired worlds were the last to hold it."""
    shared = Held("shared")
    holder = Generations(serving(**{"llm.mock": shared}))

    with holder.applying() as install:
        install(serving(**{"llm.mock": shared}))
    with holder.applying() as install:
        install(serving(**{"llm.mock": Held("new")}))
    await holder.dispose()

    assert shared.closes == 1


async def test_the_end_of_the_process_closes_what_is_current_and_what_is_held() -> None:
    """The last end a world can meet. Whatever is still holding a
    retired world when the drain is over has run out of time, and the
    world being served is nobody's to keep either."""
    retired, current = Held("retired"), Held("current")
    first = serving(**{"tts.voice": retired})
    holder = Generations(first)
    with holder.applying() as install:
        install(serving(**{"tts.voice": current}))

    # A conversation is still holding the retired world, so an ordinary
    # disposal leaves it alone.
    await holder.dispose(held=[first])
    assert retired.closes == 0

    await holder.aclose()

    assert (retired.closes, current.closes) == (1, 1)


async def test_a_close_that_raises_does_not_stop_the_others() -> None:
    """Teardown never refuses, at the holder as much as at the disposal:
    one engine that will not shut cannot keep the rest of a world
    alive."""

    class Refusing(Held):
        async def close(self) -> None:
            await super().close()
            raise RuntimeError("this client will not shut")

    refusing, after = Refusing("refusing"), Held("after")
    holder = Generations(serving(**{"llm.mock": refusing, "tts.voice": after}))

    with holder.applying() as install:
        install(serving())
    await holder.dispose()

    assert (refusing.closes, after.closes) == (1, 1)
