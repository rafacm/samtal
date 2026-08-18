"""What a device with no agent is answered, decided in one place.

The activation ceremony the short URL leads to: the table of devices
waiting to be claimed, the six-digit code each of them is showing, and
the `activation` section of the OTA reply that puts the code on a
screen. A code is not a token: it is a claim ticket a person reads off
a board they are holding, and binding still needs the API's own bearer
token, so codes may appear in logs where device tokens never may.

The question "what does an unbound device get" used to be answered
across three files that had to agree (issue #143). `activation_for`
below is its one home: the onboarding gate, the emptiness test, the
provenance check, the table's own bounds, and the reply section, in
that order, each outcome tagged rather than narrated. The narration is
the caller's, which is what keeps the two `activation_not_offered`
warnings on the channel they have always been emitted on: this package
never emits about a decision the OTA endpoint is answering.

Nothing here is called by `ota` yet; M2 of issue #143 points its reply
wrapper at it and retires the three-way split.
"""

from dataclasses import dataclass
from typing import Literal

from samtal_server.config.models import ServerConfig
from samtal_server.device.bindings import DeviceAgents

from .origin import public_origin
from .pending import PendingDevice, PendingDevices

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


@dataclass(frozen=True)
class Unbound:
    """What one check-in's activation question was answered with.

    Four outcomes rather than an object-or-None, because the caller
    warns about two of them in two different sentences and says nothing
    at all about the other two. A None that meant both "this device
    needs no code" and "this device was refused one" is what kept the
    decision from having a single home: the caller had to work out
    which it had been, and it worked it out by asking the same
    questions again.
    """

    # The OTA reply's `activation` section, when an offer was made.
    activation: dict[str, object] | None
    outcome: Literal[
        # a code, new or re-displayed
        "offered",
        # onboarding off, or the device bound or waiting on a restart
        "not_applicable",
        # the resolution is the boot snapshot's rather than the
        # database's, so what it does not say cannot be trusted
        "unreadable",
        # the pending table said no
        "refused",
    ]
    # CAPACITY_REACHED or BUDGET_SPENT, in the words the warning prints.
    refusal: str | None = None


async def activation_for(
    pending: PendingDevices,
    server: ServerConfig,
    resolution: DeviceAgents,
    mac: str,
    client_id: str,
    board: str,
    firmware: str,
) -> Unbound:
    """The `activation` section for this check-in, or the reason there
    is none.

    The gate is database truth rather than the loaded-agent filter: the
    two disagree exactly when a binding or a default agent was written
    after boot naming an agent this server never loaded, and that state
    must not mint a code for a device an operator has already added.
    Such a device gets no code and no token, and the caller says which
    restart will serve it. The other side of the same coin is upgrade
    compatibility: a deployment with a default agent covers every
    unknown MAC by design, so its devices keep receiving a token and no
    activation object, exactly as before.

    Its collaborators arrive as arguments: this answers a question, it
    does not reach into a running server for the material to answer it.
    """
    if not server.onboarding.enabled:
        return Unbound(None, "not_applicable")
    # `agents` are the names the boot snapshot loaded and `unloaded` the
    # ones it did not, so the two being empty together is the database
    # holding neither a binding row for this MAC nor a default agent.
    if resolution.agents or resolution.unloaded:
        return Unbound(None, "not_applicable")
    if not resolution.authoritative:
        # An empty answer from the snapshot fallback is not the database
        # saying nothing is bound; it is this server not having been
        # able to find out. Issuing a token off a stale answer only
        # repeats what boot decided, which is why that side keeps the
        # fallback, but minting a code off one would offer a claim
        # ticket for a board an operator has already bound, to whoever
        # is holding the endpoint. The warning naming the failure is
        # already in the log, from the view itself.
        return Unbound(None, "unreadable")
    # The unbound-only half of the #40 last-seen signal: a device that
    # is waiting has its facts and its instant refreshed here, in RAM.
    # What every device says about itself, bound or not, is recorded at
    # the check-in itself (`device_facts.record`), which is the other,
    # wider seam and not this one.
    offer = pending.observe(mac, client_id, board, firmware)
    if offer.device is None:
        return Unbound(None, "refused", offer.refused)
    return Unbound(activation_object(server, offer.device), "offered")


def activation_object(server: ServerConfig, device: PendingDevice) -> dict[str, object]:
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
        "message": f"{public_origin(server).url}\n{device.code}",
        "code": device.code,
        # Without a challenge the firmware fails `Activate()` outright
        # and waits ten seconds between polls instead of three.
        "challenge": device.challenge,
        "timeout_ms": ACTIVATION_TIMEOUT_MS,
    }
