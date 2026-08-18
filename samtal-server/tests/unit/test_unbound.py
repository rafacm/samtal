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

import pytest

from samtal_server.config import Config
from samtal_server.config.models import ServerConfig
from samtal_server.device.bindings import DeviceAgents
from samtal_server.onboarding import (
    ACTIVATION_TIMEOUT_MS,
    BUDGET_SPENT,
    CAPACITY_REACHED,
    MINT_BUDGET,
    PENDING_CAPACITY,
    PendingDevices,
    Unbound,
)
from samtal_server.onboarding.unbound import activation_for
from tests.support.checkin import Clock

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
    monkeypatch.setattr("samtal_server.onboarding.MINT_BUDGET", PENDING_CAPACITY * 2)
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
