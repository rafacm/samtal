"""What a device is answered when it asks what its backend is.

The POST every board makes on every boot and the human GET beside it.
The reply carries the websocket URL and its token, the word for how that
token is to be read, the wall clock, the firmware verdict, and, for a
device nobody has claimed, the `activation` section that puts a
six-digit code on its screen.

The decision behind that section is not made here. `activation_for` in
`onboarding.unbound` answers what an unbound device gets and returns a
tagged result; `_activation` below is the wrapper that turns two of its
four outcomes into the two warnings this endpoint has always emitted, on
this endpoint's own channel. That is the whole of what this module knows
about the ceremony.
"""

import json
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from fastapi import Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

from vinga_server import __version__
from vinga_server.auth import DeviceAuth
from vinga_server.build_info import revision
from vinga_server.composition import Composition
from vinga_server.config import ServerConfig
from vinga_server.config.models import (
    BOARD_LIMIT,
    CLIENT_ID_LIMIT,
    FIRMWARE_LIMIT,
    bounded_descriptor,
    normalize_mac,
)
from vinga_server.device.bindings import DeviceAgents, DeviceBindings
from vinga_server.events.catalog import (
    ActivationNotOfferedRefused,
    ActivationNotOfferedUnreadable,
    OtaCheckActivating,
    OtaCheckAgentNotLoaded,
    OtaCheckBodyReported,
    OtaCheckNoAgent,
    OtaCheckResolved,
    OtaRequestRejected,
)
from vinga_server.events.values import (
    CHECK_IN_BODY_LIMIT,
    CHECK_IN_BODY_TRUNCATED,
    ActivationCode,
    AgentList,
    AgentNames,
    AlsoBoundTo,
    BoardName,
    CheckInBody,
    ClientId,
    DeviceId,
    FirmwareVersion,
    Identifier,
    NotOffered,
    OtaRefusal,
    ReportedMac,
)
from vinga_server.onboarding.origin import portal_url_line, websocket_url_for
from vinga_server.onboarding.unbound import activation_for

from . import events

# What a device reports when it tells us nothing usable. Any real version is
# greater, so a device that hides its version is never offered an update.
UNKNOWN_VERSION = "0.0.0"

# Said to a device whose Device-Id is not a MAC, and logged in place of
# what it sent.
#
# Deliberately not `normalize_mac`'s own sentence. That sentence quoted
# the value it refused until #205 fixed it and carries nothing now, but
# this endpoint answers about a header rather than about a configuration
# field, and it is unauthenticated and reachable by anything that finds
# the path, so what it says is a fact of its own closed set rather than
# a message borrowed from a validator. The rule the rest of this
# codebase holds for a rejected configuration value holds here for a
# rejected header: state what the header has to hold and never what
# arrived in it.
# The sentence itself is `OtaRefusal`'s, beside the two other fixed
# refusals this endpoint may make: what the closed set holds and what a
# rejection says are one fact, and the event surface is where it is
# declared. Named here as well, because this is the refusal callers
# reach for by name.
DEVICE_ID_PROBLEM = OtaRefusal.DEVICE_ID_UNREADABLE


# How to read the token beside it, as a closed set of three literals.
# Kept a `Literal` on this side of the wire, where the values are
# produced: a value outside the set is a bug in this module, and the
# type checker is where that is caught. What a client does with an
# unrecognized one is the client's rule, not this one's.
Access = Literal[
    # Admitted, and the non-empty token beside this is the credential.
    "token",
    # Admitted, and this deployment issues no device tokens at all;
    # connect without one.
    "open",
    # Not admitted: the token is empty because there is nothing to
    # admit.
    "denied",
]


@dataclass(frozen=True)
class Admission:
    """What this device is admitted with, and how the token beside it is
    to be read.

    One answer rather than two, because the empty string is the same six
    bytes of nothing whether the deployment issues no tokens or the
    device resolves to nothing to reach, and only this module knows
    which of the two it just decided. A caller handed the token alone
    would have to re-derive the reason from facts it does not have, and
    a reply whose token and whose explanation were computed separately
    could contradict each other.
    """

    token: str
    access: Access


def token_for(
    device_auth: DeviceAuth | None, client_id: str, mac: str, agents: Sequence[str]
) -> Admission:
    """The token this device gets, which is a token only when there is
    something for it to reach, and the word for why.

    A device the configuration does not resolve to an agent is turned
    away at the websocket anyway, so issuing it a token would only widen
    what an unauthenticated endpoint hands out: the `devices` map plus
    `default_agent` is the allowlist, and this is where it bites. That
    device is `denied`, whatever the auth setting says, because being
    unresolved is the stronger fact: turning authentication off does not
    give a board an agent to talk to.

    The empty string is sent rather than the key omitted, in both the
    no-agent and the auth-disabled case, because the firmware persists
    what it is given: an empty token clears one left in NVS by another
    server, where a missing key would leave it in place. The two cases
    are byte for byte identical on the wire, which is why the reply says
    which one it is instead of leaving a reader to guess (#369).
    """
    if not agents:
        return Admission("", "denied")
    if device_auth is None:
        return Admission("", "open")
    return Admission(device_auth.issue(client_id, mac), "token")


def timezone_offset_minutes(server: ServerConfig) -> int:
    """Minutes east of UTC for the device clock, from the config or from the
    server's own current offset."""
    configured = server.timezone_offset_minutes
    if configured is not None:
        return configured
    offset = datetime.now().astimezone().utcoffset()
    return round(offset.total_seconds() / 60) if offset is not None else 0


def reported_version(payload: dict[str, Any]) -> str:
    """The firmware version the device reports in `application.version`."""
    application = payload.get("application")
    if isinstance(application, dict):
        version = application.get("version")
        if isinstance(version, str) and version.strip():
            return version.strip()
    return UNKNOWN_VERSION


def reported_board(payload: dict[str, Any]) -> str:
    """The board type the device reports, for logging only."""
    board = payload.get("board")
    if isinstance(board, dict):
        board_type = board.get("type")
        if isinstance(board_type, str) and board_type.strip():
            return board_type.strip()
    return "unknown"


# The encoder the check-in body is serialized with, built once because
# it is stateless and per-request construction would be the only cost
# this path added that is not bounded by the body's size.
#
# Both settings are load-bearing rather than stylistic, and the reasons
# are stated beside `CHECK_IN_BODY_LIMIT`: `ensure_ascii=True` is the
# printability mechanism, and the compact separators are what keeps the
# bound spent on facts rather than on whitespace.
_BODY_ENCODER = json.JSONEncoder(ensure_ascii=True, separators=(",", ":"))


def bounded_body(payload: dict[str, Any]) -> str:
    """The parsed check-in object, compactly serialized and never longer
    than `CHECK_IN_BODY_LIMIT` characters.

    `iterencode` rather than `encode`, and the loop stops the moment the
    accumulation passes the bound: this endpoint is unauthenticated, so
    what arrives is a stranger's JSON of a size they chose, and the
    value this builds, copies and dispatches has to be bounded by the
    limit rather than by the request. Parsing the request is the one
    cost that is the body's size, and it exists today.

    A value that had more to say is cut at
    `CHECK_IN_BODY_LIMIT - len(CHECK_IN_BODY_TRUNCATED)` and ends with
    that marker, so it is visibly truncated, never longer than the
    bound, and carries nothing from past the cut. The break is on
    strictly more than the bound so that a body ending exactly on it is
    whole rather than marked.
    """
    accumulated: list[str] = []
    length = 0
    for chunk in _BODY_ENCODER.iterencode(payload):
        accumulated.append(chunk)
        length += len(chunk)
        if length > CHECK_IN_BODY_LIMIT:
            room = CHECK_IN_BODY_LIMIT - len(CHECK_IN_BODY_TRUNCATED)
            return "".join(accumulated)[:room] + CHECK_IN_BODY_TRUNCATED
    return "".join(accumulated)


async def check_version(request: Request) -> Response:
    comp: Composition = request.app.state.composition
    server: ServerConfig = comp.server

    device_id = request.headers.get("device-id", "").strip()
    client_id = request.headers.get("client-id", "").strip()
    if not device_id:
        return _bad_request(OtaRefusal.DEVICE_ID_REQUIRED)
    if not client_id:
        return _bad_request(OtaRefusal.CLIENT_ID_REQUIRED)

    try:
        mac = normalize_mac(device_id)
    except ValueError:
        # Deliberately this endpoint's own sentence rather than the
        # validator's: see DEVICE_ID_PROBLEM.
        return _bad_request(DEVICE_ID_PROBLEM)
    # The live view rather than a captured one, and awaited off the
    # event loop: a device bound a moment ago gets its token at this
    # check-in rather than after a restart. The names come back
    # unclassified and are split against the world this server is
    # serving as it answers, which is the honest generation for a
    # check-in: nothing here is being built from it, so what matters is
    # what a session opening a moment from now could be given. That
    # split is the allowlist this endpoint is stingy by, so it decides
    # both the token below and what is said about the device here.
    bindings: DeviceBindings = comp.bindings
    bound = await bindings.resolve(mac)
    resolution = bound.against(comp.generations.current().config.agents)
    agents = list(resolution.agents)

    # Read once, and the two readings of that one answer derived here
    # rather than by asking twice. `_json_object` keeps None and the
    # empty object apart, which is the distinction the body event needs:
    # a request that carried no readable object is a real state of an
    # unfamiliar board and says so with a null body. Everything below it
    # wants the empty mapping instead, which is exactly what
    # `_read_json_object` would have answered.
    read = await _json_object(request)
    said_body = None if read is None else bounded_body(read)
    payload = {} if read is None else read
    version = reported_version(payload)
    board = reported_board(payload)
    # This is the only moment a device ever states its firmware version:
    # the websocket handshake does not carry it. Kept so the session
    # about to open can name it, which a capture manifest needs, since
    # echo cancellation is firmware-side.
    #
    # Every device passes here, bound or not, which is why this is where
    # #96's wider record of observed facts grows. The unbound-only half
    # of the same signal is the pending table's `last_seen`, refreshed
    # inside `_activation` below and nowhere near this line.
    comp.device_facts.record(mac, version, board)
    # What the event says about the board and the firmware, which is not
    # what the reply and the recorded facts above say about them.
    #
    # `reported_board` and `reported_version` only strip whitespace, and
    # this endpoint is unauthenticated: whatever a request's JSON body
    # holds under those keys is a string a stranger chose, of a length
    # they chose, with any character in it. The response has to echo the
    # version back untouched (the firmware compares it to decide whether
    # it is up to date) and the recorded facts feed a capture manifest,
    # so the narrowing is EVENT-ONLY: a bounded copy for the payload
    # fields and for the sentence's arguments, both of which are the
    # retained surface, and nothing else changes. A board or a version a
    # real device reports passes through unchanged, which is why no pin
    # moves.
    said_board = bounded_descriptor(board, BOARD_LIMIT) or "unknown"
    said_version = bounded_descriptor(version, FIRMWARE_LIMIT) or UNKNOWN_VERSION
    # And the same for the client id, for the same reason and with one
    # difference: the header is required above, so it is never empty
    # here, but nothing has bounded it. It is the device UUID the token
    # is signed for, so `token_for` below keeps the header exactly as it
    # arrived; what the event carries is the bounded copy, and null
    # where nothing printable survived, since a field that says nothing
    # is more honest than one that says the empty string.
    said_client = bounded_descriptor(client_id, CLIENT_ID_LIMIT) or None
    # No session exists yet, so the structured record carries the device
    # rather than a session id; the websocket events pick the device up
    # from here. These are what the board said about itself, which every
    # record this handler emits carries, whatever else it says: built
    # once, and once each, because a value type refuses where it is
    # constructed.
    described = {
        "device": DeviceId(mac),
        "client": None if said_client is None else ClientId(said_client),
        "board": BoardName(said_board),
        "firmware": FirmwareVersion(said_version),
        "said_device": ReportedMac(device_id),
    }
    # And what this server resolved it to, which the four outcome shapes
    # below add to the above. Derived from it rather than restated: two
    # structures that have to agree are one structure with a bug
    # pending, and the body event carries the first half alone.
    said = {
        **described,
        "agents": AgentNames(tuple(agents)),
        # Named in every record rather than only in the one that
        # complains about it, so a query for devices waiting on a
        # reload is one field rather than a log-message search.
        "unloaded": AgentNames(tuple(resolution.unloaded)),
    }

    activation = await _activation(comp, server, resolution, mac, client_id, board, version)

    if activation is not None:
        # A code is a claim ticket read off a screen, not a credential:
        # it belongs in the log line an operator greps for the board
        # they are holding. A device token never does.
        code = activation["code"]
        events.emit(lambda: OtaCheckActivating(code=ActivationCode(code), **said))
    elif not agents and resolution.unloaded:
        # A different problem from having no agent, and a different
        # answer: the binding is there, this process is what is behind.
        events.emit(
            lambda: OtaCheckAgentNotLoaded(
                named=AgentList.of(tuple(resolution.unloaded)), **said
            )
        )
    elif not agents:
        events.emit(lambda: OtaCheckNoAgent(**said))
    else:
        events.emit(
            lambda: OtaCheckResolved(
                agent=Identifier(agents[0]),
                bound_tail=AlsoBoundTo.of(tuple(agents[1:])),
                **said,
            )
        )

    # And, beside whichever of those four fired, the whole of what the
    # board reported. Unconditional, because the boards this exists for
    # are exactly the ones whose outcome is unpredictable, and cheap
    # enough to be unconditional because every piece of the added work
    # is bounded by `CHECK_IN_BODY_LIMIT` rather than by the request:
    # `emit` invokes the thunk and deep-copies the payload for every
    # attached tap before the live stream can reject it by level, so
    # this is built and copied per check-in whether or not anybody is
    # listening. An emitter-level interest gate would be a new mechanism
    # on the events seam for one caller, and the bounded cost does not
    # buy it.
    events.emit(
        lambda: OtaCheckBodyReported(
            body=None if said_body is None else CheckInBody(said_body),
            **described,
        )
    )

    admission = token_for(comp.device_auth, client_id, mac, agents)

    body: dict[str, Any] = {}
    if activation is not None:
        # First, the way upstream's own reply carries it, and present
        # only for a device that has to activate: the firmware treats
        # the key's absence as "no activation is ever required".
        body["activation"] = activation
    body.update(
        {
            "server_time": {
                "timestamp": int(time.time() * 1000),
                "timezone_offset": timezone_offset_minutes(server),
            },
            # No image to offer: echoing the reported version back is how the
            # firmware reads "up to date", since it only updates for a
            # strictly newer one.
            "firmware": {"version": version, "url": ""},
            # Ours, not the firmware's: the one place a device is told
            # what it is about to talk to. The firmware reads the keys it
            # knows and ignores the rest, so this is additive.
            "server": {"name": "vinga-server", "version": __version__, "revision": revision()},
            # How to read the token below, for a client that has to tell
            # an admitted board on a deployment issuing no tokens from a
            # board that was turned away: the two get the same empty
            # string, and this is the only side that knows which it just
            # decided (#369).
            #
            # Top level, and deliberately not a member of `websocket`.
            # The two boundaries differ in what stock firmware does with
            # them: it parses exactly `activation`, `mqtt`, `websocket`,
            # `server_time` and `firmware` and ignores every other
            # top-level key, which is what makes this additive the same
            # way `server` above is, while it writes every member of
            # `websocket` into NVS, so a key added there would leave a
            # stray NVS entry on every stock board.
            "access": admission.access,
            "websocket": {
                # The empty token stays beside the activation object: a
                # device showing a code has nothing to reach yet, and the
                # firmware persists what it is handed, so an empty string
                # clears one another server left in NVS.
                "url": websocket_url_for(server, request),
                "token": admission.token,
                "version": server.protocol_version,
            },
        }
    )
    return JSONResponse(body)


async def _activation(
    comp: Composition,
    server: ServerConfig,
    resolution: DeviceAgents,
    mac: str,
    client_id: str,
    board: str,
    firmware: str,
) -> dict[str, Any] | None:
    """The `activation` section for this check-in, or None when there is
    none, having said out loud the two ways there can be none that an
    operator has to hear about.

    The decision itself is `onboarding.unbound.activation_for`, which
    answers with a tagged outcome and warns about nothing. The narration
    is here because these two warnings are this endpoint's: they are
    emitted on the `vinga_server.ota` channel, in this module's words,
    about a device that is being answered by this handler.

    The match names all four outcomes, including the two with nothing to
    say, and names them as literals rather than falling back on a
    wildcard: a fifth added upstream has to be visible here as an
    outcome nothing handles, and `case _` would handle it by definition.

    What fails on a drift is
    `test_unbound.py::test_the_reply_narrates_every_outcome_the_decision_can_answer`,
    which reads this `match` and holds its arms equal to the outcome
    literal's members, both ways. `match` falls through a subject no
    case names, so without that a fifth outcome would answer the device
    correctly and simply not warn.
    """
    unbound = await activation_for(
        comp.pending, server, resolution, mac, client_id, board, firmware
    )
    match unbound.outcome:
        case "unreadable":
            # An empty answer from the snapshot fallback is not the
            # database saying nothing is bound; it is this server not
            # having been able to find out. Issuing a token off a stale
            # answer only repeats what boot decided, which is why that
            # side keeps the fallback, but minting a code off one would
            # offer a claim ticket for a board an operator has already
            # bound, to whoever is holding the endpoint. The warning
            # naming the failure is already in the log, from the view
            # itself.
            events.emit(
                lambda: ActivationNotOfferedUnreadable(device=DeviceId(mac))
            )
        case "refused":
            events.emit(
                lambda: ActivationNotOfferedRefused(
                    device=DeviceId(mac),
                    # The pending table words its two bounds as
                    # sentences over the configured limits, so the
                    # lookup that crosses one into the event vocabulary
                    # happens here.
                    reason=NotOffered(unbound.refusal),
                )
            )
        case "offered" | "not_applicable":
            # Nothing to say: the device is either showing a code, which
            # the caller's own line names, or is not one to activate.
            pass
    return unbound.activation


async def describe(request: Request) -> Response:
    """A human check that the endpoint is reachable and pointed somewhere
    sensible. Devices only ever POST here."""
    comp: Composition = request.app.state.composition
    server: ServerConfig = comp.server
    return PlainTextResponse(
        f"vinga-server {__version__} (revision {revision()}) OTA endpoint.\n"
        f"Devices are sent to {websocket_url_for(server, request)} "
        f"(protocol version {server.protocol_version}).\n"
        # The request itself, so the line is the URL that works rather
        # than the one this server would recommend: both the path it was
        # reached on and, with no origin configured, the address it
        # arrived on, which is what the line above has always used
        # (#340).
        f"{portal_url_line(server, request)}\n"
    )


def _bad_request(message: OtaRefusal) -> JSONResponse:
    """One refusal, said once to the caller and once to the log.

    Every caller passes a fixed sentence: nothing a request carried is
    interpolated into either channel, which is what keeps a header this
    endpoint could not read out of the log a deployment ships.
    """
    events.emit(lambda: OtaRequestRejected(refusal=message))
    return JSONResponse({"error": message}, status_code=400)


async def _read_json_object(request: Request) -> dict[str, Any]:
    """The device's system info, or an empty mapping. Nothing in the reply
    depends on it beyond the firmware version and logging, so a device that
    sends a malformed body is still answered rather than turned away."""
    payload = await _json_object(request)
    return {} if payload is None else payload


async def _json_object(request: Request) -> dict[str, Any] | None:
    """The request's body as a JSON object, or None when it is not one.

    None and the empty object are kept apart here, unlike above: the
    activation checks have to tell a body that could not be read from
    one that was read and said nothing.
    """
    try:
        payload = await request.json()
    except (ValueError, UnicodeDecodeError):
        return None
    return payload if isinstance(payload, dict) else None
