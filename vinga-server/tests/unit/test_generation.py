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
