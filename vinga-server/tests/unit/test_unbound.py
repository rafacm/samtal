"""What a device with no agent is answered, at the decision itself.

`activation_for` is the one home of that question (issue #143): the
onboarding gate, the emptiness test, the provenance check, the pending
table's own bounds, and the reply section, in that order. Its caller
turns the outcome it returns into what a device receives and into the
two warnings an operator reads, so an outcome that came out wrong would
be a device told the wrong thing and a log line that named the wrong
reason.

Driven directly, with a real table on a clock a test moves and
resolutions built by hand, for the reason `test_onboarding_pending.py`
is: what is under test here is a decision, and a decision is easier to
be exhaustive about than a request. The ceremony as a device meets it
over HTTP is `test_onboarding_activation.py`.

Every case that is not an offer asserts the table was left alone, which
is the half of the contract a return value does not state: a device
that needs no code, or that this server could not find out about, must
not cost a mint or leave an entry behind.
"""

import ast
from pathlib import Path
from typing import get_args, get_type_hints

import pytest

from tests.support.checkin import Clock
from vinga_server.config import Config
from vinga_server.config.models import ServerConfig
from vinga_server.device.bindings import DeviceAgents
from vinga_server.onboarding import (
    ACTIVATION_TIMEOUT_MS,
    BUDGET_SPENT,
    CAPACITY_REACHED,
    MINT_BUDGET,
    PENDING_CAPACITY,
    PendingDevices,
    Unbound,
)
from vinga_server.onboarding.unbound import activation_for
from vinga_server.ota import reply

MAC = "aa:bb:cc:dd:ee:ff"
UUID = "6f1a2b3c-4d5e-6f70-8192-a3b4c5d6e7f8"
BOARD = "waveshare-esp32-s3-touch-lcd-1.54"
FIRMWARE = "2.4.0"

# Pinned rather than guessed at, so the message on the device's screen
# is compared against a literal.
ORIGIN = "https://voice.example"

# The four resolutions this decision tells apart. Nothing is bound, one
# agent is bound, an agent is bound that this server never loaded, and
# the database could not be read so the boot snapshot answered.
UNBOUND = DeviceAgents(())
BOUND = DeviceAgents(("assistant",))
UNLOADED = DeviceAgents((), ("assistant",))
UNREADABLE = DeviceAgents((), authoritative=False)


def nth_mac(index: int) -> str:
    """One of a series of distinct MACs, for the tests that fill the
    table. Real ones, since this decision hands the table MACs that have
    already been normalized."""
    return f"11:22:33:44:{index // 256:02x}:{index % 256:02x}"


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def pending(clock: Clock) -> PendingDevices:
    return PendingDevices(clock)


def server_config(**onboarding: object) -> ServerConfig:
    return Config(server={"public_url": ORIGIN, "onboarding": onboarding}).server


async def decide(
    pending: PendingDevices,
    resolution: DeviceAgents,
    server: ServerConfig | None = None,
    mac: str = MAC,
) -> Unbound:
    return await activation_for(
        pending,
        server_config() if server is None else server,
        resolution,
        mac,
        UUID,
        BOARD,
        FIRMWARE,
    )


def fill(pending: PendingDevices, count: int) -> None:
    for index in range(count):
        assert pending.observe(nth_mac(index), UUID, BOARD, FIRMWARE).device


async def test_an_unbound_device_is_offered_a_code(pending: PendingDevices) -> None:
    """The whole reply section, because it is what the firmware reads:
    the origin and the code on two lines for the screen, the code on its
    own for the operator, the challenge a version-2 poll has to echo,
    and the timeout the firmware would have defaulted to anyway."""
    answer = await decide(pending, UNBOUND)

    assert answer.outcome == "offered"
    assert answer.refusal is None
    waiting = pending.waiting_for(MAC)
    assert waiting is not None
    assert answer.activation == {
        "message": f"{ORIGIN}\n{waiting.code}",
        "code": waiting.code,
        "challenge": MAC,
        "timeout_ms": ACTIVATION_TIMEOUT_MS,
    }


async def test_with_onboarding_off_no_device_is_asked_to_activate(
    pending: PendingDevices,
) -> None:
    """The gate that keeps every deployment that never turned onboarding
    on answering exactly what it answered before."""
    answer = await decide(pending, UNBOUND, server_config(enabled=False))

    assert answer == Unbound(None, "not_applicable")
    assert pending.listing() == ()


async def test_a_bound_device_is_asked_for_nothing(pending: PendingDevices) -> None:
    answer = await decide(pending, BOUND)

    assert answer == Unbound(None, "not_applicable")
    assert pending.listing() == ()


async def test_a_device_waiting_on_a_restart_is_asked_for_nothing(
    pending: PendingDevices,
) -> None:
    """A binding naming an agent this server never loaded is still a
    binding: an operator has already added this device, so minting a
    code for it would offer a claim ticket for a board that is spoken
    for. The caller says which restart will serve it."""
    answer = await decide(pending, UNLOADED)

    assert answer == Unbound(None, "not_applicable")
    assert pending.listing() == ()


async def test_a_resolution_the_database_could_not_answer_mints_nothing(
    pending: PendingDevices,
) -> None:
    """An empty answer from the snapshot fallback is not the database
    saying nothing is bound; it is this server not having been able to
    find out. The outcome is its own, because the caller warns about it
    in its own sentence."""
    answer = await decide(pending, UNREADABLE)

    assert answer == Unbound(None, "unreadable")
    assert pending.listing() == ()


async def test_at_the_cap_it_is_refused_in_the_words_the_warning_prints(
    pending: PendingDevices, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusal travels as the table's own sentence, because that is
    what the caller renders into `activation_not_offered.reason`.

    The budget is lifted for the reason the table's own suite lifts it:
    with the shipped constants it binds first and the cap can never
    fire.
    """
    monkeypatch.setattr("vinga_server.onboarding.MINT_BUDGET", PENDING_CAPACITY * 2)
    fill(pending, PENDING_CAPACITY)

    answer = await decide(pending, UNBOUND)

    assert answer == Unbound(None, "refused", CAPACITY_REACHED)
    # And a full table is still the table it was: the device that was
    # turned away left nothing behind.
    assert pending.waiting_for(MAC) is None
    assert len(pending.listing()) == PENDING_CAPACITY


async def test_with_the_budget_spent_it_is_refused_and_says_which_bound(
    pending: PendingDevices,
) -> None:
    fill(pending, MINT_BUDGET)

    answer = await decide(pending, UNBOUND)

    assert answer == Unbound(None, "refused", BUDGET_SPENT)
    assert pending.waiting_for(MAC) is None
    assert len(pending.listing()) == MINT_BUDGET


# --- and the caller narrates every one of them ------------------------
#
# The four outcomes exist so the caller can say something different
# about each, and two of them are said out loud to an operator. A fifth
# added here would compile, run, and be answered with silence: `match`
# falls through a subject no case names, so the device would be answered
# correctly and the warning an operator needs would simply not happen.
#
# The conformance suite used to catch that from the other end, by
# holding `activation_not_offered.reason`'s declared token set equal to
# what the narration writes into it. It is deleted (#210), and this is
# the survivor: the literal's members against the arms that name them,
# read out of the source rather than exercised, because the point is
# which outcomes are HANDLED and not which ones a test thought to drive.


def narrated_outcomes() -> frozenset[str]:
    """The outcomes `ota/reply.py`'s narration names, read off its
    `match`, refusing a wildcard.

    A wildcard would make this check pass forever: `case _` handles a
    fifth outcome by definition, and handling it is exactly what nobody
    would have done.
    """
    source = Path(reply.__file__).read_text(encoding="utf-8")
    matches = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Match) and ast.unparse(node.subject) == "unbound.outcome"
    ]
    assert len(matches) == 1, "one narration, or this is reading the wrong one"

    named: set[str] = set()
    for case in matches[0].cases:
        assert case.guard is None, "a guarded arm does not name an outcome"
        alternatives = (
            case.pattern.patterns
            if isinstance(case.pattern, ast.MatchOr)
            else [case.pattern]
        )
        for pattern in alternatives:
            assert isinstance(pattern, ast.MatchValue), (
                f"{ast.unparse(pattern)} is not a literal outcome; a wildcard or a "
                f"capture would make this check pass whatever is added"
            )
            named.add(ast.literal_eval(pattern.value))
    return frozenset(named)


def test_the_reply_narrates_every_outcome_the_decision_can_answer() -> None:
    """By equality, both ways: an outcome nobody narrates is a silence,
    and an arm naming an outcome that no longer exists is dead code that
    reads as coverage."""
    declared = frozenset(get_args(get_type_hints(Unbound)["outcome"]))

    assert declared == {"offered", "not_applicable", "unreadable", "refused"}
    assert narrated_outcomes() == declared
