"""The short onboarding path: /x/<key>/, an alias of the OTA endpoint.

Onboarding a stock board means typing its backend URL into a captive
portal on a phone, character by character, with nothing on the far side
to say a typo happened. A URL hidden behind a long random segment is
therefore paid for twice: once by whoever types it and once by whoever
debugs the silence a mistyped one produces.

This module serves the same endpoint at a short, secret-derived alias.
The key is `base32(HMAC-SHA256(device-auth secret, label))` truncated to
eight characters: derived from a secret the deployment already has, so
nothing new is configured, stored or persisted; stable across restarts;
and rotating only when the secret rotates. Base32 because A-Z2-7 holds
no 0/O and no 1/I/l, the pairs a person misreads off a 240x240 display,
and matched case-insensitively because a phone keyboard offers lower
case first. It is also served with and without its trailing slash, by
the same handlers rather than by a redirect between them: a captive
portal saves what it likes, and the firmware treats a redirect on this
request as an error rather than following it.

A wrong key answers the stock 404, byte for byte what a path that was
never served answers, and logs the attempted key next to the correct
one so the operator sees the typo character by character. That log line
is a deliberate, recorded trade: the key is a deployment-scoped path
segment, not a per-device token, so the rule that tokens are never
logged is untouched. The configured `server.ota_path` segment is not
printed anywhere, and neither is any device token.

The attempt itself is attacker-controlled text out of a URL, so what
may be repeated is bounded: after case folding, one to ten characters
of the base32 alphabet, and nothing else. That is a mistyped or
over-typed key, which is what the line is for; anything longer or
carrying a newline, a control character or any other byte is counted
rather than quoted, so no request can forge a log entry or choose how
long one is.

The other half of this module is the activation ceremony the same short
URL leads to: the table of devices waiting to be claimed, the six-digit
code each of them is showing, and the `activation` section of the OTA
reply that puts the code on a screen. A code is not a token: it is a
claim ticket a person reads off a board they are holding, and binding
still needs the API's own bearer token, so codes may appear in logs
where device tokens never may.
"""

import base64
import hashlib
import hmac
import logging
import os
import re
import secrets
import threading
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, Response

from samtal_server import ota
from samtal_server.config import Config
from samtal_server.config.models import ONBOARDING_MOUNT_PATH

logger = logging.getLogger(__name__)

# What the key is derived over. Versioned, so a future scheme can change
# the derivation without a deployment silently keeping the old key.
KEY_LABEL = b"samtal-onboarding-key-v1"

# How much of the base32 digest the key is. Eight characters is 40 bits,
# which is not a password and is not meant to be: it stands in front of
# an endpoint whose stinginess is what actually protects it, and it is
# short enough to type off a screen without a mistake.
KEY_LENGTH = 8

# What a mismatch may repeat back into the log, as an exact rule rather
# than an impression: after case folding, one to ten characters, every
# one of them in the base32 alphabet. Ten because a key mistyped with a
# character or two too many is exactly the mistake the log line exists to
# diagnose, and the upper bound is what keeps an attacker from choosing
# how long a log entry is. The alphabet excludes everything a forged
# entry needs, newlines and control characters first among them.
LOGGABLE_ATTEMPT_LENGTH = KEY_LENGTH + 2

_LOGGABLE_ATTEMPT_RE = re.compile(rf"^[A-Z2-7]{{1,{LOGGABLE_ATTEMPT_LENGTH}}}$")

# The activation ceremony's parameters. Constants rather than
# configuration: nobody has field evidence to tune them by, and a knob
# nobody can reason about is schema noise. If the field says otherwise
# they graduate to configuration then.

# How many digits a code has. Six, because the firmware speaks it digit
# by digit off a compiled clip per digit and shows it on a small screen.
CODE_DIGITS = 6

_CODE_CEILING = 10**CODE_DIGITS

# How long one code stands. The device re-checks OTA every half minute
# to two minutes while it waits, so an expired code is replaced on the
# screen long before an operator could type a stale one: what they read
# is always what is current.
CODE_TTL_S = 600.0

# How many devices may be waiting at once. At the cap a new one gets
# exactly today's behavior, an empty token and no activation object,
# and a warning names the cap.
PENDING_CAPACITY = 128

# How many codes may be minted in any window of MINT_WINDOW_S, counting
# first issues and re-issues but not re-displays of a live code. The cap
# above bounds the standing table; this bounds the rate, so an outsider
# who fills the table and waits for it to expire is bounded per window
# rather than only per snapshot.
MINT_BUDGET = 30

MINT_WINDOW_S = 600.0

# How long a code has left after a claim of it failed, at least. A
# minute, because that is what makes the refusal's own advice ("run the
# command again in a moment") true for a code whose deadline the failed
# attempt stepped over. It only ever extends, so a code that had longer
# keeps what it had.
RELEASED_GRACE_S = 60.0

# What the OTA reply's `timeout_ms` carries. Upstream's manager-api does
# not send the field at all (its Activation DTO has code, message and
# challenge and nothing else), and the firmware parses it into
# `activation_timeout_ms_`, a member no other line of `ota.cc` or
# `application.cc` reads, defaulting to 30000. So this is the firmware's
# own default sent back to it: the shape issue #40 documents, and a
# value that changes nothing on any board.
ACTIVATION_TIMEOUT_MS = 30000

# The algorithms a version-2 activation body may name. The HMAC itself
# cannot be verified (the key is burned into the device's eFuses and
# only the vendor's cloud has a copy), so what is checked is that the
# body parses, names an algorithm this server knows, and echoes the
# challenge this server issued.
ACTIVATION_ALGORITHMS = frozenset({"hmac-sha256"})

# How much of anything a device says about itself is kept. Board type,
# firmware version, client id and serial number are attacker-controlled
# text arriving in a request, and they are held in memory, listed over
# the API and read by a person: bounded and printable, or not kept.
FACT_LENGTH = 64

Handler = Callable[[Request], Awaitable[Response]]


def derive_key(secret: str) -> str:
    """The onboarding key for one device-auth secret.

    Uppercase canonical, because that is what a display renders and what
    the configuration pins; the route matches case-insensitively.
    """
    digest = hmac.new(secret.encode("utf-8"), KEY_LABEL, hashlib.sha256).digest()
    return base64.b32encode(digest).decode("ascii")[:KEY_LENGTH]


def onboarding_key(config: Config) -> str | None:
    """The key this server's short route is served under, or None when
    there is none to derive and none pinned.

    None is not a failure: with device auth off there is no secret, so
    the route mounts keyless at /x/ (a trial network has nothing to hide
    the endpoint from that an open websocket does not already hand over).
    A pinned key is honoured either way, since it replaces the derivation
    rather than depending on it.
    """
    onboarding = config.server.onboarding
    if onboarding.key is not None:
        return onboarding.key
    if not config.server.auth.enabled:
        return None
    # The same variable `build_device_auth` reads, and read again rather
    # than taken from it, so the secret stays where it is used and no
    # object grows a property that hands it out. An enabled auth with no
    # secret has already refused the boot by the time this runs.
    secret = os.environ.get(config.server.auth.secret_env, "").strip()
    return derive_key(secret) if secret else None


def onboarding_path(key: str | None) -> str:
    """The path the short route is served on, as a person types it."""
    return f"{ONBOARDING_MOUNT_PATH}/" if key is None else f"{ONBOARDING_MOUNT_PATH}/{key}/"


@dataclass(frozen=True)
class Origin:
    """The origin devices reach this server on, and where it came from.

    The provenance travels with the value because two of the three
    sources are inferences: a URL that came out of `websocket_url` is
    only as right as that key is, and one built from the listen address
    is a guess. A line that named neither would read as fact.
    """

    url: str
    source: str
    guessed: bool = False
    note: str = ""

    @property
    def provenance(self) -> str:
        prefix = "guessed from" if self.guessed else "from"
        return f"{prefix} {self.source}{self.note}"


def public_origin(config: Config) -> Origin:
    """Where a device reaches this server, in the order the plan sets:
    `public_url` as written, else the origin of `websocket_url`, else the
    listen address, which is a guess and says so.

    Total by construction. Every step that could raise falls through to
    the next source instead, and the last source is two configuration
    fields that cannot fail, so an operator never meets this as a
    traceback at startup.
    """
    server = config.server
    if server.public_url:
        return Origin(server.public_url, "server.public_url")
    unreadable = False
    if server.websocket_url:
        derived = _origin_of(server.websocket_url)
        if derived is not None:
            return Origin(derived, "server.websocket_url")
        unreadable = True

    reasons: list[str] = []
    if unreadable:
        # Reachable only for a configuration built in code, since the
        # validator refuses one a file could hold. Said out loud anyway:
        # a guess that had a better source and could not use it is not
        # the same guess as one that never had a source.
        reasons.append("server.websocket_url could not be read as a URL")
    if server.host in ("0.0.0.0", "::", "[::]"):
        reasons.append(
            f"{server.host} is where the server listens rather than a name a device "
            f"can reach"
        )
    reasons.append("set server.public_url to name this deployment exactly")
    return Origin(
        f"http://{_bracketed(server.host)}:{server.port}",
        "the listen address (server.host and server.port)",
        guessed=True,
        note=", " + "; ".join(reasons),
    )


def _origin_of(websocket_url: str) -> str | None:
    """The http origin behind a `ws://` or `wss://` URL, or None when
    there is none to take.

    Built from the parsed hostname and port, never from the raw netloc,
    so a `user:password@host` cannot ride into a log line through the
    banner. Both of the parse steps that raise are caught: `urlsplit`
    itself for a malformed IPv6 host, and `.port` for one that is not a
    number in range. The configuration validator refuses both, and this
    is what keeps a configuration built in code from crashing a startup
    the validator would have refused.
    """
    try:
        parts = urlsplit(websocket_url)
        hostname, port = parts.hostname, parts.port
    except ValueError:
        return None
    if not hostname:
        return None
    scheme = "https" if parts.scheme == "wss" else "http"
    return f"{scheme}://{_bracketed(hostname)}{'' if port is None else f':{port}'}"


def _bracketed(host: str) -> str:
    """An IPv6 literal in the brackets a URL needs, anything else as it
    is. `urlsplit` strips the brackets from a hostname, and this is what
    puts them back."""
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


def portal_url_line(config: Config, path: str) -> str:
    """The one line naming the URL to type into a device's captive
    portal, for the path it is served on."""
    origin = public_origin(config)
    return f"Type this into the device's captive portal: {origin.url}{path} ({origin.provenance})"


def log_banner(config: Config) -> None:
    """Say the onboarding URL out loud at startup.

    With onboarding on this is the short URL, key and all: the key is a
    deployment-scoped path segment, deliberately printed so that a typo
    and a rotated secret both diagnose themselves. With onboarding off
    the line names `server.ota_path` without quoting it, because that
    segment is a credential and the logs must not carry it.
    """
    server = config.server
    origin = public_origin(config)
    if not server.onboarding.enabled:
        logger.info(
            "device onboarding is off: devices are configured at the server.ota_path "
            "path on %s (%s), which is not printed here, since that segment is this "
            "deployment's secret",
            origin.url,
            origin.provenance,
            extra={
                "event": "onboarding_banner",
                "origin": origin.url,
                "origin_source": origin.source,
                "onboarding": False,
            },
        )
        return
    key = onboarding_key(config)
    url = f"{origin.url}{onboarding_path(key)}"
    logger.info(
        "device onboarding URL: %s (%s)",
        url,
        origin.provenance,
        extra={
            "event": "onboarding_banner",
            "url": url,
            "origin_source": origin.source,
            "onboarding": True,
        },
    )


# The pending-device table
#
# An unbound device is answered with a six-digit code, which it shows on
# its screen and speaks digit by digit; the operator reads it off the
# board in front of them and claims it with one command. The code is a
# live claim ticket and nothing more, so this table is deliberately not
# persistent: losing it to a restart costs a changed number on a screen,
# because the device re-checks OTA every couple of minutes and displays
# whatever the fresh reply carries.
#
# It is shared between the device-facing handlers, which run on the
# event loop, and the API handlers, which run on the threadpool, so
# every operation happens under one mutex. The lock is held only for
# in-memory work, microseconds of it, and never across a database write:
# a claim reserves the code under the lock, writes with the lock
# released, and consumes or releases the reservation afterwards. That is
# what makes two operators racing one code produce one bind and one
# retryable refusal rather than two binds.


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
    f"{PENDING_CAPACITY} devices are already waiting to be claimed, which is the cap"
)

BUDGET_SPENT = (
    f"{MINT_BUDGET} activation codes have been issued in the last "
    f"{int(MINT_WINDOW_S / 60)} minutes, which is the limit"
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
            if len(self._by_mac) >= PENDING_CAPACITY:
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
                expires_at=now + CODE_TTL_S,
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
                    device.expires_at, self._clock() + RELEASED_GRACE_S
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
        while self._mints and self._mints[0] <= now - MINT_WINDOW_S:
            self._mints.popleft()
        return len(self._mints) < MINT_BUDGET

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
    return f"{secrets.randbelow(_CODE_CEILING):0{CODE_DIGITS}d}"


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


def activation_object(config: Config, device: PendingDevice) -> dict[str, object]:
    """The OTA reply's `activation` section, in upstream's shape.

    `message` is what the firmware puts on the screen verbatim: upstream
    renders its console's host, a newline, then the code, and the
    firmware's display draws it exactly that way. Here the host is the
    origin the banner resolved, so the line an operator reads off a
    board names the deployment they typed into the portal. It needs no
    localization: it is a hostname and six digits, and the words around
    it on screen come from the firmware's compiled assets, which no
    server can influence.
    """
    return {
        "message": f"{public_origin(config).url}\n{device.code}",
        "code": device.code,
        # Without a challenge the firmware fails `Activate()` outright
        # and waits ten seconds between polls instead of three.
        "challenge": device.challenge,
        "timeout_ms": ACTIVATION_TIMEOUT_MS,
    }


def build_router(key: str | None) -> APIRouter:
    """The short-path router, delegating to the OTA handlers.

    Built per app rather than at import time, for the reason the OTA
    router is: the key is derived from configuration that has not been
    read when this module is imported.
    """
    router = APIRouter()
    if key is None:
        base = onboarding_path(None)
        for path in ota.spellings(base):
            router.post(path)(ota.check_version)
            router.get(path)(ota.describe)
        # Where a waiting device polls, which is its OTA URL with
        # `activate` after it. Both spellings answer directly, like
        # every other device-facing route: the firmware is not a
        # browser, and a portal that dropped the trailing slash from the
        # endpoint dropped it from this too.
        for path in ota.spellings(f"{base}{ota.ACTIVATE_SEGMENT}/"):
            router.post(path)(ota.activate)
        return router
    for path in ota.spellings(f"{ONBOARDING_MOUNT_PATH}/{{key}}/"):
        router.post(path)(_guarded(key, ota.check_version))
        router.get(path)(_guarded(key, ota.describe))
    # Guarded like everything else on this path: a wrong key must meet
    # the same 404 here as at the endpoint itself, on either spelling.
    for path in ota.spellings(f"{ONBOARDING_MOUNT_PATH}/{{key}}/{ota.ACTIVATE_SEGMENT}/"):
        router.post(path)(_guarded(key, ota.activate))
    return router


def _guarded(expected: str, handler: Handler) -> Handler:
    """The handler behind a key check that leaks nothing on a miss.

    Raising the 404 rather than composing one is what makes the response
    byte-identical to an unserved path: the same exception handler
    renders both, so the two cannot drift apart later.
    """

    async def guarded(request: Request) -> Response:
        # Case folded once: it is what the comparison uses, and it is
        # also the only form that is ever logged, which is what makes
        # the shape check below a guarantee about the output rather than
        # about the input (an upper-casing of some Unicode characters is
        # an ASCII letter, and the folded string is what is rendered).
        folded = str(request.path_params.get("key", "")).upper()
        if not hmac.compare_digest(folded.encode("utf-8"), expected.encode("utf-8")):
            _log_mismatch(folded, expected)
            raise HTTPException(status_code=404)
        return await handler(request)

    return guarded


def _log_mismatch(folded: str, expected: str) -> None:
    """The diagnostic for a wrong key, which repeats the attempt only
    when the attempt is something a person could have typed at a key.

    The correct key beside the attempted one is what makes a typo and a
    rotated secret diagnose themselves, and it is the recorded trade
    from issue #40. The attempt, though, is attacker-controlled text
    arriving in a URL, and a raw one carries a newline into the log as a
    forged second entry, or a megabyte of anything. So it is repeated
    only when it matches the shape a mistyped key has; anything else is
    counted rather than quoted, and the correct key is left out of that
    line too, so probing cannot turn the log into a broadcast of it.
    """
    if _LOGGABLE_ATTEMPT_RE.match(folded):
        logger.warning(
            "onboarding key %s does not match this server's key %s: check the URL "
            "typed into the device's captive portal, character by character",
            folded,
            expected,
            extra={
                "event": "onboarding_key_mismatch",
                "attempted": folded,
                "expected": expected,
            },
        )
        return
    logger.warning(
        "a request reached the onboarding path carrying %d characters that are not "
        "shaped like a key at all, so they are not repeated here; the URL to type is "
        "in the startup line",
        len(folded),
        extra={"event": "onboarding_key_unshaped", "attempted_length": len(folded)},
    )
