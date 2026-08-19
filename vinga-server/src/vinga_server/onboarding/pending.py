"""The pending-device table.

An unbound device is answered with a six-digit code, which it shows on
its screen and speaks digit by digit; the operator reads it off the
board in front of them and claims it with one command. The code is a
live claim ticket and nothing more, so this table is deliberately not
persistent: losing it to a restart costs a changed number on a screen,
because the device re-checks OTA every couple of minutes and displays
whatever the fresh reply carries.

It is shared between the device-facing handlers, which run on the
event loop, and the API handlers, which run on the threadpool, so
every operation happens under one mutex. The lock is held only for
in-memory work, microseconds of it, and never across a database write:
a claim reserves the code under the lock, writes with the lock
released, and consumes or releases the reservation afterwards. That is
what makes two operators racing one code produce one bind and one
retryable refusal rather than two binds.

Nothing here imports a device, a provider or a conversation: the
configuration API and `vinga-server config` are this table's second
reader, and what they may import without loading a conversation's
machinery is what this file is allowed to depend on.
"""

import secrets
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace

# The package itself, bound once, for the tunable bounds it defines
# ahead of this import. Every bound below is read through it AT THE
# MOMENT the decision is made rather than copied into this module's
# globals, because that is the monkeypatch contract the suites have
# always had: a test assigns `vinga_server.onboarding.MINT_BUDGET` and
# expects the next mint to see it, and a name imported from the package
# would be a snapshot taken at import time that no assignment reaches.
import vinga_server.onboarding as onboarding

# How much of anything a device says about itself is kept. Board type,
# firmware version, client id and serial number are attacker-controlled
# text arriving in a request, and they are held in memory, listed over
# the API and read by a person: bounded and printable, or not kept.
FACT_LENGTH = 64


@dataclass
class PendingDevice:
    """One device waiting to be claimed, and what it said about itself.

    Mutated only under the table's lock, and handed out only as copies,
    so nothing a caller holds can change underneath it or change what
    the table holds.
    """

    mac: str
    code: str
    # The value sent as the OTA reply's `activation.challenge` and
    # echoed by a version-2 activation body. It is the device's MAC,
    # which is what upstream sends; kept as a field anyway, so the
    # version-2 check is about what this server issued rather than about
    # a coincidence.
    challenge: str
    client_id: str
    board: str
    firmware: str
    first_seen: float
    last_seen: float
    expires_at: float
    # A version-2 body carries one. Recorded as an observed fact: there
    # is nothing to check it against, and nothing here depends on it.
    serial_number: str | None = None
    # True between a claim reserving this entry and the repository write
    # finishing. A second claim meeting it is refused as retryable.
    claiming: bool = False


@dataclass(frozen=True)
class Offer:
    """What an unbound device's check-in got: a code to show, or the
    reason no code was minted for it."""

    device: PendingDevice | None = None
    # None when a device was offered a code. Otherwise the bound that
    # fired, in words a log line prints.
    refused: str | None = None


@dataclass(frozen=True)
class Claim:
    """What a claim of one code got: the entry, reserved for the caller,
    or the reason it could not be reserved."""

    device: PendingDevice | None = None
    # True when another request is already claiming this code. Distinct
    # from an unknown code, because the answers differ: one says read
    # the screen again, the other says try again in a moment.
    in_flight: bool = False


# The two bounds, in the words their warnings use.
CAPACITY_REACHED = (
    f"{onboarding.PENDING_CAPACITY} devices are already waiting to be claimed, which is the cap"
)

BUDGET_SPENT = (
    f"{onboarding.MINT_BUDGET} activation codes have been issued in the last "
    f"{int(onboarding.MINT_WINDOW_S / 60)} minutes, which is the limit"
)


class PendingDevices:
    """The table of devices waiting for a code to be claimed.

    One per app, created by the composition root and shared with the
    configuration API's sub-application, which is where a code is turned
    into a binding.
    """

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        # Injected so expiry and the mint window are tested by moving a
        # number rather than by sleeping. Wall clock rather than a
        # monotonic one, because the listing publishes these instants to
        # a person deciding whether the code on the screen is this one.
        self._clock = clock
        self._lock = threading.Lock()
        self._by_mac: dict[str, PendingDevice] = {}
        self._by_code: dict[str, str] = {}
        # When each code was minted, oldest first, for the sliding
        # window. Only mints and re-issues are recorded here.
        self._mints: deque[float] = deque()

    def observe(self, mac: str, client_id: str, board: str, firmware: str) -> Offer:
        """One unbound device's check-in: the code it should show.

        A device already waiting is answered with the code it is already
        showing, its facts refreshed; one whose code has expired is
        answered with a new one, which is what heals a screen the
        operator was too slow for. Both bounds answer with no code at
        all, and say which of them fired.
        """
        with self._lock:
            now = self._clock()
            self._expire(now)
            waiting = self._by_mac.get(mac)
            if waiting is not None:
                waiting.client_id = _fact(client_id)
                waiting.board = _fact(board)
                waiting.firmware = _fact(firmware)
                waiting.last_seen = now
                return Offer(replace(waiting))
            if len(self._by_mac) >= onboarding.PENDING_CAPACITY:
                return Offer(refused=CAPACITY_REACHED)
            if not self._affordable(now):
                return Offer(refused=BUDGET_SPENT)
            device = PendingDevice(
                mac=mac,
                code=self._code(),
                challenge=mac,
                client_id=_fact(client_id),
                board=_fact(board),
                firmware=_fact(firmware),
                first_seen=now,
                last_seen=now,
                expires_at=now + onboarding.CODE_TTL_S,
            )
            self._by_mac[mac] = device
            self._by_code[device.code] = mac
            self._mints.append(now)
            return Offer(replace(device))

    def waiting_for(self, mac: str) -> PendingDevice | None:
        """The entry for one device, or None when it is not waiting."""
        with self._lock:
            self._expire(self._clock())
            device = self._by_mac.get(mac)
            return None if device is None else replace(device)

    def record_serial(self, mac: str, serial_number: str) -> None:
        """Keep the serial number a version-2 body carried. An observed
        fact about a board, with nothing depending on it."""
        with self._lock:
            device = self._by_mac.get(mac)
            if device is not None:
                device.serial_number = _fact(serial_number)

    def listing(self) -> tuple[PendingDevice, ...]:
        """Every device waiting, oldest first. Copies, and one lock-held
        step, so a listing is a moment of this table rather than a walk
        over one that is being written."""
        with self._lock:
            self._expire(self._clock())
            return tuple(
                replace(device)
                for device in sorted(self._by_mac.values(), key=lambda one: one.first_seen)
            )

    def reserve(self, code: str) -> Claim:
        """Take one code out of circulation for the length of a claim.

        Reserved rather than removed: the repository write can fail, and
        a code that was removed before it succeeded would leave the
        device showing a number nothing answers to.
        """
        with self._lock:
            self._expire(self._clock())
            mac = self._by_code.get(code)
            device = None if mac is None else self._by_mac.get(mac)
            if device is None:
                return Claim()
            if device.claiming:
                return Claim(in_flight=True)
            device.claiming = True
            return Claim(replace(device))

    def consume(self, code: str) -> None:
        """Retire a claimed code: the device is bound, and the number on
        its screen answers to nothing now."""
        with self._lock:
            self._forget(code)

    def retire(self, mac: str) -> None:
        """Forget one device, because something else has configured it.

        The listing exists to answer "which of these boards may I
        claim", so a board that has just been bound by its MAC does not
        belong in it. This is housekeeping and not the guarantee: a
        write made where this table cannot be reached (the `--local`
        recovery path, or a second process) reconciles nothing, which is
        why the claim itself refuses to bind a device that is already
        configured.
        """
        with self._lock:
            device = self._by_mac.get(mac)
            if device is not None:
                self._forget(device.code)

    def retire_all(self) -> None:
        """Forget every device, because a default agent now covers all
        of them at once."""
        with self._lock:
            self._by_mac.clear()
            self._by_code.clear()

    def release(self, code: str) -> None:
        """Put a reserved code back, for a claim whose write failed, and
        give it long enough to be used.

        The deadline moves out, which is the difference between "the
        operator can run the same command again" and being able to. A
        claim holds a reservation for as long as its write takes, and a
        write that fails on a busy database takes the busy timeout, ten
        seconds, which is long enough to step over a deadline that had
        three seconds left. The refusal the operator just read says to
        try again; answering that with "no device is waiting" would be
        both wrong, since the device is still showing the number, and
        unactionable.
        """
        with self._lock:
            mac = self._by_code.get(code)
            device = None if mac is None else self._by_mac.get(mac)
            if device is not None:
                device.claiming = False
                device.expires_at = max(
                    device.expires_at, self._clock() + onboarding.RELEASED_GRACE_S
                )

    def _expire(self, now: float) -> None:
        """Drop what has timed out. An entry in the middle of a claim is
        kept: its repository write is in flight, and the claim's own
        release or consume is what ends it."""
        for code in [
            device.code
            for device in self._by_mac.values()
            if device.expires_at <= now and not device.claiming
        ]:
            self._forget(code)

    def _forget(self, code: str) -> None:
        mac = self._by_code.pop(code, None)
        if mac is not None:
            self._by_mac.pop(mac, None)

    def _affordable(self, now: float) -> bool:
        while self._mints and self._mints[0] <= now - onboarding.MINT_WINDOW_S:
            self._mints.popleft()
        return len(self._mints) < onboarding.MINT_BUDGET

    def _code(self) -> str:
        """A code no other waiting device is showing.

        From `secrets`, so it cannot be predicted from another one. The
        redraw loop cannot spin: the capacity check above has already
        run, so at most PENDING_CAPACITY of a million codes are taken.
        """
        code = _drawn()
        while code in self._by_code:
            code = _drawn()
        return code


def _drawn() -> str:
    return f"{secrets.randbelow(onboarding._CODE_CEILING):0{onboarding.CODE_DIGITS}d}"


def _fact(value: str, limit: int = FACT_LENGTH) -> str:
    """One thing a device said about itself, bounded before it is kept.

    Truncated first and then made printable, so a request cannot choose
    how much memory an entry costs, how long a log line is, or put a
    newline into either. Unprintable characters become a question mark
    rather than disappearing, because a board type that arrived mangled
    should read as mangled.
    """
    return "".join(
        character if character.isprintable() else "?"
        for character in value.strip()[:limit]
    )
