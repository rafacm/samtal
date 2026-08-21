"""Where a waiting device polls until somebody claims it.

The other half of the activation ceremony: the reply puts a code on the
screen, and this is the request the firmware repeats every three seconds
until the answer changes from "keep waiting" to "you are configured".
Nothing is minted here and nothing is bound here; the binding happens on
the operator's side, and this only reports what the bindings already
say.
"""

from fastapi import Request, Response

from vinga_server.composition import Composition
from vinga_server.config.models import normalize_mac
from vinga_server.device.bindings import DeviceBindings
from vinga_server.events.catalog import (
    ActivationComplete,
    ActivationPending,
    ActivationRefusedChallengeMismatch,
    ActivationRefusedUnknownAlgorithm,
    ActivationRefusedUnreadableBody,
)
from vinga_server.events.values import (
    ActivationCode,
    AgentNames,
    DeviceId,
    OtaRefusal,
)
from vinga_server.onboarding.pending import PendingDevice, PendingDevices
from vinga_server.onboarding.unbound import ACTIVATION_ALGORITHMS

from . import events
from .reply import DEVICE_ID_PROBLEM, _bad_request, _json_object

# What the version header carries when a body is worth reading. A board
# with no serial number burned, which every consumer board is, announces
# version 1 and sends `{}`; upstream's own server never reads that body.
ACTIVATION_VERSION_HEADER = "activation-version"


async def activate(request: Request) -> Response:
    """Where a waiting device polls, in bursts of ten three seconds
    apart, until it is claimed.

    200 means the MAC resolves to an agent this server is serving, so a
    200 always means the next OTA check hands the device its real
    configuration. 202 is everything else, which is what upstream's own
    "keep waiting" is: a device still unbound, a device bound to an
    agent this server is not serving (which flips to 200 at the reload
    that installs it), and a device this server has no pending entry for
    at all, since a restart loses the table and the device's own loop
    fetches a fresh code within a couple of minutes.

    What a version-2 body claims cannot be authenticated. Its HMAC is
    computed with a key burned into the device's eFuses, which the
    vendor's cloud knows from registration and vinga has no copy of, so
    the code ceremony governs both versions (the plan's fourth
    author-approved deviation from issue #40). What is checked while the
    MAC is pending is what can be: the body parses, names an algorithm
    this server knows, and echoes the challenge this server issued. A
    poll answering somebody else's challenge is not evidence of
    anything, so it is refused with the 202 it would have got anyway and
    a log line naming which check failed.
    """
    device_id = request.headers.get("device-id", "").strip()
    if not device_id:
        return _bad_request(OtaRefusal.DEVICE_ID_REQUIRED)
    try:
        mac = normalize_mac(device_id)
    except ValueError:
        # Deliberately not the validator's own sentence, which quotes
        # what it refused: see DEVICE_ID_PROBLEM.
        return _bad_request(DEVICE_ID_PROBLEM)

    comp: Composition = request.app.state.composition
    bindings: DeviceBindings = comp.bindings
    # Split against the world this server is serving as it answers, for
    # the reason the check-in splits it there: a device is told it is
    # activated when a session could be opened for it now.
    bound = await bindings.resolve(mac)
    resolution = bound.against(comp.generations.current().config.agents)
    if resolution.agents:
        events.emit(
            lambda: ActivationComplete(
                device=DeviceId(mac), agents=AgentNames(tuple(resolution.agents))
            )
        )
        return Response(status_code=200)

    pending = comp.pending
    waiting = pending.waiting_for(mac)
    if waiting is not None:
        await _version_two(request, pending, waiting)
    events.emit(
        lambda: ActivationPending(
            device=DeviceId(mac),
            code=None if waiting is None else ActivationCode(waiting.code),
            unloaded=AgentNames(tuple(resolution.unloaded)),
        )
    )
    return Response(status_code=202)


async def _version_two(
    request: Request, pending: PendingDevices, waiting: PendingDevice
) -> None:
    """The checks a version-2 poll can be held to, and the serial number
    it carries, recorded as an observed fact."""
    if request.headers.get(ACTIVATION_VERSION_HEADER, "").strip() != "2":
        # Version 1 is `{}` and upstream's own server never reads it.
        return
    payload = await _json_object(request)
    refusal = {
        "device": DeviceId(waiting.mac),
        "code": ActivationCode(waiting.code),
    }
    if payload is None:
        events.emit(lambda: ActivationRefusedUnreadableBody(**refusal))
        return
    algorithm = payload.get("algorithm")
    if not isinstance(algorithm, str) or algorithm not in ACTIVATION_ALGORITHMS:
        events.emit(lambda: ActivationRefusedUnknownAlgorithm(**refusal))
        return
    if payload.get("challenge") != waiting.challenge:
        events.emit(lambda: ActivationRefusedChallengeMismatch(**refusal))
        return
    serial_number = payload.get("serial_number")
    if isinstance(serial_number, str) and serial_number.strip():
        pending.record_serial(waiting.mac, serial_number)
