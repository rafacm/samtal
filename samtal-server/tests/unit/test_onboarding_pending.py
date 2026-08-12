"""The table of devices waiting to be claimed.

Everything here is driven through the injected clock rather than by
sleeping, which is what the clock is for: expiry, the re-issue that
follows it, and the sliding mint window are all a number moving.

The other half is the claim lifecycle, which exists because this table
is written from the event loop (the OTA handlers) and read and claimed
from the threadpool (the API handlers). Two operators racing one code
have to produce one bind and one retryable refusal, and a repository
write that fails has to leave the code claimable again, because the
device is still showing it.
"""

import threading

import pytest

from samtal_server.onboarding import (
    BUDGET_SPENT,
    CAPACITY_REACHED,
    CODE_DIGITS,
    CODE_TTL_S,
    FACT_LENGTH,
    MINT_BUDGET,
    MINT_WINDOW_S,
    PENDING_CAPACITY,
    PendingDevices,
)

MAC = "aa:bb:cc:dd:ee:ff"
OTHER_MAC = "11:22:33:44:55:66"
UUID = "6f1a2b3c-4d5e-6f70-8192-a3b4c5d6e7f8"
BOARD = "waveshare-esp32-s3-touch-lcd-1.54"
FIRMWARE = "2.4.0"


def nth_mac(index: int) -> str:
    """One of a series of distinct MACs, for the tests that fill the
    table. Real ones, since the callers of this table hand it MACs that
    have already been normalized."""
    return f"11:22:33:44:{index // 256:02x}:{index % 256:02x}"


class Clock:
    """A clock a test moves by hand."""

    def __init__(self, now: float = 1_700_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def pending(clock: Clock) -> PendingDevices:
    return PendingDevices(clock)


def _observe(pending: PendingDevices, mac: str = MAC):
    return pending.observe(mac, UUID, BOARD, FIRMWARE)


def test_a_code_is_six_digits(pending: PendingDevices) -> None:
    """The firmware speaks it digit by digit off one compiled clip per
    digit, and a person reads it off a small screen."""
    code = _observe(pending).device.code

    assert len(code) == CODE_DIGITS
    assert code.isdigit()


def test_the_entry_carries_what_the_device_said_about_itself(
    pending: PendingDevices, clock: Clock
) -> None:
    device = _observe(pending).device

    assert device.mac == MAC
    assert device.client_id == UUID
    assert device.board == BOARD
    assert device.firmware == FIRMWARE
    assert device.first_seen == clock.now
    assert device.last_seen == clock.now
    assert device.expires_at == clock.now + CODE_TTL_S
    # The challenge is the MAC, which is what upstream sends and what a
    # version-2 poll has to echo back.
    assert device.challenge == MAC
    assert device.serial_number is None


def test_a_second_check_in_re_displays_the_same_code(
    pending: PendingDevices, clock: Clock
) -> None:
    """The device re-checks every couple of minutes while it waits. It
    must keep showing the number the operator is typing."""
    first = _observe(pending).device

    clock.advance(60)
    again = pending.observe(MAC, "another-uuid", "another-board", "9.9.9").device

    assert again.code == first.code
    assert again.first_seen == first.first_seen
    # The facts are the latest ones, so a listing describes the board as
    # it last described itself.
    assert again.last_seen == clock.now
    assert (again.client_id, again.board, again.firmware) == (
        "another-uuid",
        "another-board",
        "9.9.9",
    )


def test_an_expired_code_is_re_issued_at_the_next_check_in(
    pending: PendingDevices, clock: Clock
) -> None:
    first = _observe(pending).device

    clock.advance(CODE_TTL_S)
    second = _observe(pending).device

    assert second.code != first.code
    assert second.first_seen == clock.now
    # And the old number answers to nothing, which is what the operator
    # is protected by: they always type what is currently displayed.
    assert pending.reserve(first.code).device is None


def test_issuance_at_the_instant_of_expiry_gives_a_new_code(
    pending: PendingDevices, clock: Clock
) -> None:
    """The boundary itself, because expiry and issuance are one
    lock-held step: an entry whose deadline is exactly now is gone
    before the check-in that races it is answered."""
    first = _observe(pending).device
    clock.now = first.expires_at

    assert _observe(pending).device.code != first.code


def test_a_code_is_gone_from_the_listing_the_moment_it_expires(
    pending: PendingDevices, clock: Clock
) -> None:
    _observe(pending)
    clock.advance(CODE_TTL_S - 1)
    assert len(pending.listing()) == 1

    clock.advance(1)
    assert pending.listing() == ()
    assert pending.waiting_for(MAC) is None


def test_no_two_waiting_devices_show_the_same_code(pending: PendingDevices) -> None:
    codes = {
        pending.observe(nth_mac(index), UUID, BOARD, FIRMWARE).device.code
        for index in range(MINT_BUDGET)
    }

    assert len(codes) == MINT_BUDGET


def test_the_listing_is_oldest_first_and_is_a_copy(
    pending: PendingDevices, clock: Clock
) -> None:
    """One lock-held step, so a listing is a moment of this table rather
    than a walk over one being written, and what it hands back cannot be
    changed underneath the table or change it."""
    _observe(pending)
    clock.advance(1)
    _observe(pending, OTHER_MAC)

    listing = pending.listing()
    assert [device.mac for device in listing] == [MAC, OTHER_MAC]

    listing[0].code = "000000"
    assert pending.waiting_for(MAC).code != "000000"


def test_a_new_device_at_the_cap_is_answered_as_it_was_before_onboarding(
    pending: PendingDevices, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cap bounds the standing table.

    The budget is lifted here because with the shipped constants it
    binds first and the cap can never fire: at most MINT_BUDGET codes
    are minted per window, and an entry lives for one window, so no more
    than that many are ever waiting. The cap is what still holds if the
    budget is ever raised, and this is what says it does.
    """
    monkeypatch.setattr("samtal_server.onboarding.MINT_BUDGET", PENDING_CAPACITY * 2)
    for index in range(PENDING_CAPACITY):
        assert pending.observe(nth_mac(index), UUID, BOARD, FIRMWARE).device

    offer = pending.observe("ff:ff:ff:ff:ff:ff", UUID, BOARD, FIRMWARE)

    assert offer.device is None
    assert offer.refused == CAPACITY_REACHED
    assert str(PENDING_CAPACITY) in offer.refused
    # And the devices already waiting are untouched: a full table still
    # answers the boards that are in it.
    assert pending.waiting_for(nth_mac(0)) is not None


def test_a_device_already_waiting_is_answered_at_the_cap(
    pending: PendingDevices, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cap is about new entries. A board already in the table keeps
    being shown its own code however full the table is."""
    monkeypatch.setattr("samtal_server.onboarding.MINT_BUDGET", PENDING_CAPACITY * 2)
    first = _observe(pending).device
    for index in range(PENDING_CAPACITY):
        pending.observe(nth_mac(index), UUID, BOARD, FIRMWARE)

    assert _observe(pending).device.code == first.code


def test_minting_stops_at_the_budget_and_says_which_bound_fired(
    pending: PendingDevices,
) -> None:
    for index in range(MINT_BUDGET):
        assert pending.observe(nth_mac(index), UUID, BOARD, FIRMWARE).device

    offer = pending.observe("ff:ff:ff:ff:ff:ff", UUID, BOARD, FIRMWARE)

    assert offer.device is None
    assert offer.refused == BUDGET_SPENT
    assert str(MINT_BUDGET) in offer.refused


def test_re_displaying_a_live_code_costs_nothing(pending: PendingDevices) -> None:
    """The budget counts codes minted, not codes shown. A fleet of
    waiting boards re-checking every minute would otherwise spend it in
    seconds and lock out the next board an operator plugs in."""
    macs = [nth_mac(index) for index in range(MINT_BUDGET - 1)]
    for mac in macs:
        pending.observe(mac, UUID, BOARD, FIRMWARE)
    for _ in range(50):
        for mac in macs:
            assert pending.observe(mac, UUID, BOARD, FIRMWARE).device

    assert pending.observe("ff:ff:ff:ff:ff:ff", UUID, BOARD, FIRMWARE).device


def test_a_re_issue_spends_the_budget(pending: PendingDevices, clock: Clock) -> None:
    """A re-issue is a new code, so it is counted like any other. The
    plan's wording exactly: mints and re-issues count, re-displays of a
    live code do not."""
    _observe(pending)
    clock.advance(CODE_TTL_S)
    # The first mint has left the window along with the code it made, so
    # this re-issue is the only one in it.
    assert _observe(pending).device
    for index in range(MINT_BUDGET - 1):
        assert pending.observe(nth_mac(index), UUID, BOARD, FIRMWARE).device

    assert pending.observe("ff:ff:ff:ff:ff:ff", UUID, BOARD, FIRMWARE).device is None


def test_the_budget_refills_as_the_window_slides(
    pending: PendingDevices, clock: Clock
) -> None:
    for index in range(MINT_BUDGET):
        pending.observe(nth_mac(index), UUID, BOARD, FIRMWARE)
    assert pending.observe("ff:ff:ff:ff:ff:ff", UUID, BOARD, FIRMWARE).device is None

    clock.advance(MINT_WINDOW_S)

    assert pending.observe("ff:ff:ff:ff:ff:ff", UUID, BOARD, FIRMWARE).device


def test_a_claim_reserves_the_code_and_consuming_it_retires_it(
    pending: PendingDevices,
) -> None:
    code = _observe(pending).device.code

    claim = pending.reserve(code)
    assert claim.device.mac == MAC
    assert claim.in_flight is False

    pending.consume(code)

    assert pending.reserve(code).device is None
    assert pending.waiting_for(MAC) is None
    assert pending.listing() == ()


def test_a_second_claim_of_a_reserved_code_is_refused_as_retryable(
    pending: PendingDevices,
) -> None:
    """The whole point of reserving rather than removing: two operators
    racing one code produce one bind and one "try again", never two
    binds."""
    code = _observe(pending).device.code
    pending.reserve(code)

    second = pending.reserve(code)

    assert second.device is None
    assert second.in_flight is True


def test_a_released_reservation_is_claimable_again(pending: PendingDevices) -> None:
    """What a failed repository write leaves behind. The device is still
    showing the number, so the number still has to work."""
    code = _observe(pending).device.code
    pending.reserve(code)

    pending.release(code)

    assert pending.reserve(code).device.mac == MAC


def test_an_unknown_code_is_neither_found_nor_in_flight(pending: PendingDevices) -> None:
    claim = pending.reserve("000000")

    assert claim.device is None
    assert claim.in_flight is False


def test_a_reservation_survives_the_expiry_of_its_entry(
    pending: PendingDevices, clock: Clock
) -> None:
    """A claim holds a repository write that can take seconds. Expiring
    the entry underneath it would turn a successful bind into a code
    that consume() cannot find."""
    code = _observe(pending).device.code
    pending.reserve(code)

    clock.advance(CODE_TTL_S * 2)

    assert pending.reserve(code).in_flight is True
    pending.consume(code)
    assert pending.reserve(code).device is None


def test_two_threads_claiming_one_code_produce_one_winner(
    pending: PendingDevices,
) -> None:
    """The race itself, run for real rather than reasoned about: the
    table is reached from the event loop and from the threadpool at the
    same time."""
    code = _observe(pending).device.code
    start = threading.Barrier(8)
    claims = []

    def claim() -> None:
        start.wait()
        claims.append(pending.reserve(code))

    threads = [threading.Thread(target=claim) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len([one for one in claims if one.device is not None]) == 1
    assert len([one for one in claims if one.in_flight]) == 7


def test_listing_while_the_table_is_being_written_is_never_torn(
    pending: PendingDevices,
) -> None:
    """A listing races the OTA handlers minting into the same table. It
    is one lock-held step, so what comes back is a moment of the table
    and every entry in it is complete."""
    stop = threading.Event()

    def mint() -> None:
        index = 0
        while not stop.is_set():
            pending.observe(nth_mac(index % 20), UUID, BOARD, FIRMWARE)
            index += 1

    writer = threading.Thread(target=mint)
    writer.start()
    try:
        for _ in range(200):
            for device in pending.listing():
                assert device.code and device.mac and device.challenge == device.mac
    finally:
        stop.set()
        writer.join()


def test_a_serial_number_from_a_version_two_body_is_recorded(
    pending: PendingDevices,
) -> None:
    _observe(pending)

    pending.record_serial(MAC, "SN-0001")

    assert pending.waiting_for(MAC).serial_number == "SN-0001"


def test_a_serial_number_for_a_device_that_is_not_waiting_is_dropped(
    pending: PendingDevices,
) -> None:
    pending.record_serial(MAC, "SN-0001")

    assert pending.waiting_for(MAC) is None


@pytest.mark.parametrize(
    "said, kept",
    [
        ("x" * (FACT_LENGTH + 40), "x" * FACT_LENGTH),
        ("board\nname", "board?name"),
        ("  spaced  ", "spaced"),
        ("esp32\x00s3", "esp32?s3"),
    ],
)
def test_what_a_device_says_about_itself_is_bounded_before_it_is_kept(
    pending: PendingDevices, said: str, kept: str
) -> None:
    """Board type, firmware and client id are attacker-controlled text
    that is held in memory, listed over the API and read by a person. A
    request may not choose how much memory an entry costs, and may not
    put a newline into anything that prints it."""
    device = pending.observe(MAC, said, said, said).device

    assert device.board == kept
    assert device.client_id == kept
    assert device.firmware == kept


def test_a_serial_number_is_bounded_the_same_way(pending: PendingDevices) -> None:
    _observe(pending)

    pending.record_serial(MAC, "SN\n" + "9" * (FACT_LENGTH * 2))

    serial = pending.waiting_for(MAC).serial_number
    assert len(serial) == FACT_LENGTH
    assert "\n" not in serial
