"""The simulated board: who it says it is, what it asks, and what it is
told.

One POST, and a closed reading of the answer. The POST is the check-in
every board makes on every boot: two headers the handler reads, and a
system-info body it takes a firmware version and a board type out of.
The answer is where the whole device-side half turns, and it is read
into four states the grammar branches on.

The fourth state is the one that costs people an evening. A board whose
MAC is not bound still gets `200 OK`, with an empty `websocket.token`
and no `activation` section, so a board that provisions perfectly and
then never speaks is that, not a network fault. `Unwelcome` names it and
says the configurations that produce it, rather than reporting a
success.

An empty token used to be the whole of what the reply said about that,
and it is the same empty string a deployment that issues no tokens at
all sends to a board it admits (#369). So the reply carries a word for
it, `access`, and this reads it: `open` is admitted with nothing to
present, `denied` is turned away, `token` is the credential beside it.
A reply that carries no word, or one this client does not know, is read
by yesterday's rule, the token alone, which is what keeps a new
simulator against an old image behaving exactly as it did.

Three facts about the reading are load-bearing enough to say out loud
here. `activation is not None` is the seam, written that way and never
as truthiness, because `activation={}` is an object that is falsy and is
not an absent key. A reply that contradicts itself is refused rather
than resolved: an `activation` beside a non-empty token is a shape no
vinga-server produces, and guessing which half to believe is how a
client teaches itself to accept what the server never promised. And the
word for the token contradicts what stands beside it in exactly the
combinations no decision site can emit, each of them refused the same
way.

Nothing the far side wrote reaches a sentence. Every refusal below is a
fixed constant; the only far-side values printed at all are the
activation code, the message and the challenge, which are the artifact
this command exists to show, and each is bounded and made printable
first. The device token, the websocket URL and the word for the token
are carried and never printed: the token is a credential nobody typed,
the URL decides where that credential would be sent, and the word is
far-side bytes like any other.
"""

from dataclasses import dataclass
from time import monotonic, sleep
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from vinga_server import __version__
from vinga_server.config.loader import ConfigError
from vinga_server.config.models import NOT_A_MAC, normalize_mac
from vinga_server.device_endpoint import (
    SUPPLIED_ENDPOINT,
    Endpoint,
    # Imported into this module's own namespace on purpose: it is the
    # seam this command's suite replaces, and a suite that patched it
    # here would leave the doctor's own requests alone.
    build_client,
    requested,
    websocket_target,
)
from vinga_server.protocol.framing import SUPPORTED_VERSIONS

# The address this board answers to when nothing says otherwise.
#
# Fixed and documented rather than generated and written to disk. The
# grammar holds no state between runs, so a binding that survives a
# restart is bought by derivation instead: the same address every run, so
# a `device bind` sticks, and a second simulated board is
# `--mac 02:00:00:00:00:02`.
#
# The leading octet sets the locally-administered bit, which is precisely
# what an address that was never assigned to hardware should carry. A MAC
# is not a credential: it is printed on the box and broadcast in the
# clear in every Wi-Fi frame, and `device bind <mac>` already takes one
# on the command line.
DEFAULT_MAC = "02:00:00:00:00:01"

# The namespace the client id is derived under.
#
# A UUID version 5 over the normalized MAC: a pure function of the MAC,
# stable across invocations with nothing written anywhere, distinct for
# two simulated boards, and reproducible from this source by anybody who
# wants to check it. Version 5 rather than a random version 4 because
# determinism is the whole property wanted, and a UUID rather than a
# hand-rolled hash because a UUID is what the header carries and
# `uuid5` is in the standard library.
#
# It is not a detail. The OTA reply signs its token for the MAC AND the
# client id together, and the websocket verifies against both, so a
# client id that differed between the check-in and the handshake would
# produce a `bad_token` refusal with nothing on either side saying why.
# The re-check-in a claim needs makes one value survive four requests.
CLIENT_ID_NAMESPACE = uuid5(NAMESPACE_URL, "https://github.com/rafacm/vinga/simulator")

# What this board says it is. Honest rather than mimicking a vendor's
# string: what reads these is the pending listing an operator greps, and
# a simulated board that claimed to be a Waveshare would be the one line
# that makes a screen full of real boards unreadable.
BOARD_TYPE = "vinga-simulator"

FIRMWARE_VERSION = __version__

# What the check-in's own arguments are called if the policy has anything
# to say about them.
GIVEN_URL = "the URL given to the simulator"

# Every wait this half makes, and the reason that picked each.
#
# The connect and read bounds are `device_endpoint`'s, for the doctor's
# reasons. These two are the ceremony's, and the rule they exist to hold
# is stated once: remote input may shorten a wait and may never extend
# one.
#
# The cadence is the firmware's own, recorded in `docs/xiaozhi-notes.md`:
# `Application::CheckNewVersion` polls in bursts of ten, three seconds
# apart. It is a cadence rather than a bound, and reproducing it is part
# of being faithful.
POLL_ATTEMPTS = 10

POLL_INTERVAL_S = 3.0

# And the bound. One burst of the firmware's cadence is twenty-seven
# seconds of waiting, and the claim this command has just performed has
# already happened by the time the first poll goes out, so a server that
# has not answered inside one burst is not going to. A reply's own
# `timeout_ms` may shorten this and may never extend it: it is untrusted
# remote input, and the value it usually carries is not an envelope at
# all but the FIRMWARE's own default sent back to it, which
# `onboarding/unbound.py` records is parsed into a member no other line
# reads.
ACTIVATION_CEILING_S = 30.0

# What a reply that cannot be read as one says. Fixed constants, and
# built over the stand-in rather than over an address: this command is
# only ever given a URL, never a derived one, so there is nothing here
# that could be shown.
NOT_A_REPLY = (
    f"{SUPPLIED_ENDPOINT} answered, but not with a JSON object this client can read as a "
    f"check-in reply. It is not quoted back: what a proxy, a gateway or a captive portal "
    f"returns is not this server's own output."
)

MALFORMED_REPLY = (
    f"{SUPPLIED_ENDPOINT} answered a check-in with a body shaped differently from the one "
    f"this server documents, so what a board would take from it cannot be read. Nothing of "
    f"it is quoted back, since it is whatever that address returned."
)

UNKNOWN_PROTOCOL_VERSION = (
    f"{SUPPLIED_ENDPOINT} named a device protocol version this client does not speak, so a "
    f"board pointed here could not agree a framing with it. The version is not repeated: it "
    f"is whatever that address returned."
)

CONTRADICTORY_REPLY = (
    f"{SUPPLIED_ENDPOINT} answered with both an activation section and a device token, "
    f"which is a reply no vinga-server produces. Guessing which half to believe is how a "
    f"client teaches itself to accept a shape the server never promised, so it is refused "
    f"instead."
)

CONTRADICTORY_ACCESS = (
    f"{SUPPLIED_ENDPOINT} said in one field how the device token beside it is to be read, "
    f"and the rest of the reply says otherwise: a credential named where there is none, or "
    f"none named where there is one, or a board admitted while it is being claimed. No "
    f"vinga-server decides those, and the word is not repeated back, being whatever that "
    f"address returned."
)

UNUSABLE_WEBSOCKET = (
    f"{SUPPLIED_ENDPOINT} admitted this board and then named a websocket address a device "
    f"token may not be sent to: it is not a ws:// or wss:// URL with a host, or it carries "
    f"a credential in the URL, or it is a plain ws:// address answered by an https "
    f"endpoint. It is not quoted back, since it is whatever that address returned."
)


def bad_status(status: int) -> str:
    """What a status other than a success says.

    The number is printed because it is this side's reading of the
    exchange rather than the far side's words, which is the same reason
    a close is named by an exception's class.
    """
    return (
        f"{SUPPLIED_ENDPOINT} answered {status} to a check-in, which is not an answer a "
        f"board could take its configuration from. Check that this is the OTA path that "
        f"deployment serves. Nothing of the body is quoted back."
    )


def bad_poll_status(status: int) -> str:
    return (
        f"{SUPPLIED_ENDPOINT} answered {status} to an activation poll, and the only two "
        f"answers that endpoint gives are 202 for keep waiting and 200 for activated. "
        f"Nothing of the body is quoted back."
    )


@dataclass(frozen=True)
class Identity:
    """Who this simulated board is, in the two values the server signs a
    token for together.

    Both derived, neither stored: the MAC is the flag's value or the
    documented default, and the client id is a pure function of it. One
    identity flows through every request of a ceremony and, from M2, the
    handshake after them, which is the property that makes a token issued
    at one check-in usable at the next.
    """

    mac: str
    client_id: str

    @classmethod
    def of(cls, mac: str) -> "Identity":
        """One board's identity, or the fixed refusal for an address that
        is not a MAC.

        Normalized first, so `AA:BB:...` and `aa:bb:...` are one board
        rather than two, which is the same normalization the websocket
        applies before it verifies a token. The refusal is
        `NOT_A_MAC`, which carries the rule and never the value, and is
        the same sentence `device bind` answers with.
        """
        problem: str | None = None
        try:
            normalized = normalize_mac(mac)
        except ValueError:
            problem = NOT_A_MAC
        if problem is not None:
            raise ConfigError(problem)
        return cls(mac=normalized, client_id=str(uuid5(CLIENT_ID_NAMESPACE, normalized)))

    def headers(self) -> dict[str, str]:
        """What a board puts on its check-in.

        The handler reads exactly `Device-Id` and `Client-Id`. The other
        two are what a real board sends and what an operator watching an
        access log expects to see beside them, and nothing on either side
        reads either of them.
        """
        return {
            "Device-Id": self.mac,
            "Client-Id": self.client_id,
            "User-Agent": f"{BOARD_TYPE}/{FIRMWARE_VERSION}",
            "Accept-Language": "en-US",
        }

    def poll_headers(self) -> dict[str, str]:
        """What a board puts on an activation poll: the same headers
        again, and one more.

        Derived from `headers` rather than written beside it, because
        the firmware's own `Ota` sets its header block once and every
        request it makes carries it; two lists here would be one list
        with an omission pending, and the omission it had was the client
        id. One identity has to cross every request of the ceremony, so
        a poll that dropped half of it would be a poll no recording
        could hold the claim against.

        `Activation-Version: 1` is what a consumer board with no eFuse
        key burned announces, and the body that goes with it is `{}`,
        which upstream's own manager-api reads nothing of. Version 2 and
        its HMAC are not available to any simulator: the key is burned
        into a device's eFuses and only the vendor's cloud has a copy.
        """
        return {**self.headers(), "Activation-Version": "1"}

    def system_info(self) -> dict[str, object]:
        """The body a board POSTs with its check-in.

        The handler reads `application.version` and `board.type` out of a
        tolerantly parsed body and nothing else. The rest is the shape
        the firmware sends, kept so that what an operator sees in a
        server log is what a board would have put there.
        """
        return {
            "version": 2,
            "mac_address": self.mac,
            "uuid": self.client_id,
            "application": {"name": BOARD_TYPE, "version": FIRMWARE_VERSION},
            "board": {"type": BOARD_TYPE},
        }


# What a reply has to be to be read as one
#
# Strict, in the shape `config/responses.py` is read with: nothing is
# coerced, so a body that puts a number where a token belongs is refused
# rather than rendered as its coercion. Unknown fields are ignored, which
# is the one tolerance kept deliberately, so a newer server stays
# readable.
#
# The three activation fields default to empty strings rather than being
# required, which is what makes `activation={}` a reply this schema
# admits and the seam below decide. That is deliberate: the distinction
# between an object that is falsy and an absent key is the one this whole
# reading exists to make unwritable, and a schema that refused `{}` would
# have taken the decision away from the seam.


class _Websocket(BaseModel):
    model_config = ConfigDict(extra="ignore")

    url: str = ""
    token: str = ""
    # Absent is the firmware's own default rather than a refusal: version
    # 1 is what a reply carrying no `version` means to a board.
    version: int = 1


class _Activation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    message: str = ""
    code: str = ""
    challenge: str = ""
    # Read rather than typed, because what may be done with it is a rule
    # and not a shape: `activation_ceiling` below accepts a positive
    # integer and ignores everything else, since a malformed hint is not
    # a reason to fail a ceremony that works without it.
    timeout_ms: Any = None


class _Firmware(BaseModel):
    """What the reply says about an image for this board.

    Modelled rather than ignored, because a real board reads this block
    on every check-in: it is where a deployment says "you are up to
    date" (by naming the version the board just reported, with no URL)
    or "here is an image" (by naming one). A simulator that discarded it
    would be claiming in its own help to read something it threw away.
    """

    model_config = ConfigDict(extra="ignore")

    version: str = ""
    url: str = ""


# The three words a reply may carry for how to read the token beside
# it, and the set this client recognizes.
#
# Spelled here rather than imported from `ota/reply.py`: this half is the
# client, and what it may reach is the published protocol rather than the
# module that writes it (`tests/unit/test_cli_import_weight.py`). A
# generated client would carry the same three strings for the same
# reason.
ACCESS_TOKEN = "token"

ACCESS_OPEN = "open"

ACCESS_DENIED = "denied"

KNOWN_ACCESS = frozenset({ACCESS_TOKEN, ACCESS_OPEN, ACCESS_DENIED})


class _Reply(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # Required, so a reply with no websocket object at all is a schema
    # failure rather than an empty token.
    websocket: _Websocket
    activation: _Activation | None = None
    # Not required: a reply carrying no firmware block at all is one
    # that offered nothing, which is a state this reads rather than a
    # shape it refuses.
    firmware: _Firmware = Field(default_factory=_Firmware)
    # A strict optional string rather than a `Literal` over the three
    # words, deliberately. The producer is typed to the closed set,
    # because a value outside it there is a bug; here an unknown word is
    # a server this client does not know, and a `Literal` would make it a
    # MALFORMED reply rather than an absent field, which is the harsher
    # of the two readings and the wrong one for a client. What the set
    # holds is decided at `read()` instead. Absent is an older server,
    # which is a reply this client has always been able to read.
    access: str | None = None


# The four states, and nothing else a check-in can end as


@dataclass(frozen=True)
class Firmware:
    """What the reply's firmware block means to THIS board, in the two
    facts a board acts on.

    Two booleans and no strings, deliberately. What a real board does
    with the block is decide, not display: it fetches when an image is
    named and its version is newer, and otherwise reads the version it
    just reported coming back as "you are up to date". Both of those are
    comparisons, so the reading survives the crossing and the far side's
    own text does not, which is what lets a verdict say something true
    about the block without repeating a word of it.

    Neither the version nor the URL is carried. This simulator has no
    partitions and fetches nothing, so an address it will never open is
    an address it has no reason to hold.
    """

    # An image was named: a URL to fetch, which a board with partitions
    # would.
    offered: bool

    # The version named is the one this board announced, which is how a
    # deployment with no image says so.
    announced: bool

    @classmethod
    def of(cls, block: "_Firmware") -> "Firmware":
        return cls(offered=bool(block.url), announced=block.version == FIRMWARE_VERSION)


@dataclass(frozen=True)
class Activating:
    """Unclaimed: the code, the message and the challenge, exactly as the
    screen would show them."""

    code: str
    message: str
    challenge: str
    timeout_ms: Any
    firmware: Firmware


@dataclass(frozen=True)
class Admitted:
    """Bound: this board may speak.

    `token` and `websocket` are carried and never printed. The token is a
    credential nobody typed, which is what makes it the easier of the two
    to leak by accident, and the URL is far-side text that decides where
    that credential would be sent. Both are what M2's `run` opens a
    socket with; what a verdict names is the stand-in.

    The token is empty on a deployment that issues none, which the reply
    says in as many words (`access: open`, #369). That is a state this
    absorbs rather than a fifth one beside it: everything this board does
    next is the same, the websocket target is resolved by the same rules
    and the conversation is held the same way, and a deployment with
    device authentication off asks the handshake for no credential. So
    the emptiness of this field is the one thing that tells the two
    admissions apart, and the grammar reads it here rather than reading
    the reply again.
    """

    token: str
    websocket: str
    protocol_version: int
    firmware: Firmware


@dataclass(frozen=True)
class Unwelcome:
    """It checked in and it may not speak.

    A state rather than a boolean's false half, because it is the one a
    reply can reach two ways. A server that says so says so: `200 OK`
    with `access: denied`, which is the deployment stating that nothing
    resolves this board. A server too old to say it (#369) sends the
    same `200 OK` with an empty token and no activation section, and
    that is what onboarding being off looks like, and equally what a MAC
    or a `default_agent` naming an agent this server has not loaded
    looks like, and equally what a refused offer looks like, and equally
    what an admitted board on a deployment issuing no tokens looks like.
    The grammar's sentence names all of them, because such a reply names
    none of them.
    """

    firmware: Firmware


@dataclass(frozen=True)
class Refused:
    """The endpoint answered and this client will not read the answer as a
    reply, or the request did not complete at all.

    Carries the sentence rather than the exception it may have come from:
    what the grammar raises is built from this, outside any handler, so
    nothing walking a chain finds a library's exception behind it.
    """

    problem: str


CheckIn = Activating | Admitted | Unwelcome | Refused


def check_in(endpoint: Endpoint, identity: Identity) -> CheckIn:
    """One check-in, and what this board was told.

    The POST every board makes on every boot. A refusal from the request
    boundary becomes a `Refused` carrying its sentence rather than
    travelling as an exception, so both halves of "what happened" leave
    this function the same way and the caller has one thing to branch on.
    """
    problem: str | None = None
    answered: httpx.Response | None = None
    try:
        answered = requested(
            "POST",
            endpoint,
            build=build_client,
            headers=identity.headers(),
            body=identity.system_info(),
        )
    except ConfigError as refusal:
        problem = str(refusal)
    if answered is None:
        return Refused(problem or NOT_A_REPLY)
    return read(answered, endpoint)


def read(answered: httpx.Response, endpoint: Endpoint) -> CheckIn:
    """What one reply says this board is, in the order the questions are
    asked.

    Five steps, in this order, so two readers cannot disagree about a
    contradictory reply. Transport and status first. Then the schema,
    which anything failing leaves as `Refused` rather than as a state.
    Then contradiction, refused rather than resolved, both the shape that
    has always been one and the words the reply's own `access` cannot
    stand beside. Then `activation is not None`, written that way and not
    as truthiness. Then admission, which the word decides where the reply
    carries one this client knows and the token decides where it does
    not; a token that is not a string at all failed the schema two steps
    ago rather than being an empty one here.

    The word is recognized before either contradiction is checked and
    admission is read from what recognition left, so an unknown word is
    one fact, "this reply carries no word I know", from the first
    question to the last.
    """
    if not answered.is_success:
        return Refused(bad_status(answered.status_code))
    payload = _payload(answered)
    if payload is None:
        return Refused(NOT_A_REPLY)
    reply = _validated(payload)
    if reply is None:
        return Refused(MALFORMED_REPLY)
    if reply.websocket.version not in SUPPORTED_VERSIONS:
        return Refused(UNKNOWN_PROTOCOL_VERSION)
    access = reply.access if reply.access in KNOWN_ACCESS else None
    if reply.activation is not None and reply.websocket.token:
        return Refused(CONTRADICTORY_REPLY)
    if _contradicted(access, reply):
        return Refused(CONTRADICTORY_ACCESS)
    # Read once, for whichever state this ends in: every reply carries
    # the block, whatever it says about admitting the board.
    firmware = Firmware.of(reply.firmware)
    if reply.activation is not None:
        return Activating(
            code=reply.activation.code,
            message=reply.activation.message,
            challenge=reply.activation.challenge,
            timeout_ms=reply.activation.timeout_ms,
            firmware=firmware,
        )
    if not _admitted(access, reply):
        return Unwelcome(firmware=firmware)
    # The scheme is read off the response rather than off the string an
    # operator typed, because that is what the request went out over and
    # no redirect was followed, so it is the address that answered.
    target = websocket_target(reply.websocket.url, answered.url.scheme)
    if target is None:
        return Refused(UNUSABLE_WEBSOCKET)
    return Admitted(
        token=reply.websocket.token,
        websocket=target,
        protocol_version=reply.websocket.version,
        firmware=firmware,
    )


def _contradicted(access: str | None, reply: _Reply) -> bool:
    """Whether the word for the token disagrees with what stands beside
    it, which is the whole matrix rather than the rows somebody thought
    of.

    Three combinations, and no server decision site can emit any of them:
    a credential named where the token is empty; no credential named
    where a token stands; and a board admitted while it is being claimed,
    since a board showing a code is by definition not yet admitted. So
    activation is compatible with `denied` and with an absent word, and
    with nothing else.

    An unrecognized word arrives here as None and contradicts nothing:
    it is read as an older server throughout, and a client that refused
    a reply for carrying a word it had not heard of would be refusing
    compatibility itself.
    """
    if access is None:
        return False
    if access == ACCESS_TOKEN and not reply.websocket.token:
        return True
    if access in (ACCESS_OPEN, ACCESS_DENIED) and reply.websocket.token:
        return True
    return access in (ACCESS_OPEN, ACCESS_TOKEN) and reply.activation is not None


def _admitted(access: str | None, reply: _Reply) -> bool:
    """Whether this board may speak, by the word where there is one and
    by the token where there is not.

    The fallback is not a lesser reading, it is yesterday's: a server
    that says nothing about why a token is empty is a server whose empty
    token means what it has always meant here. That keeps a new
    simulator against an old image behaving exactly as it did, and it is
    conservative rather than open-ended: a future admission mode that
    sent an empty token under a word this client does not know would
    read `Unwelcome`, which is the safe half of being wrong.
    """
    if access is None:
        return bool(reply.websocket.token)
    return access in (ACCESS_TOKEN, ACCESS_OPEN)


def _payload(answered: httpx.Response) -> dict[str, object] | None:
    """The response's body as a JSON object, or None when it is not one.

    No exception escapes, so nothing that walks an exception chain later
    finds the body attached to it.
    """
    parsed: object = None
    try:
        parsed = answered.json()
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _validated(payload: dict[str, object]) -> _Reply | None:
    """The body as the shape this server documents, or None.

    The refusal is decided inside the handler and answered outside it,
    and the exception is not bound to a name: `ValidationError.errors()`
    retains the input it rejected, which here is whatever answered at a
    device-facing address.
    """
    understood: _Reply | None = None
    try:
        understood = _Reply.model_validate(payload, strict=True)
    except ValidationError:
        return None
    return understood


# Waiting for somebody to claim this board


@dataclass(frozen=True)
class Activated:
    """The poll answered 200: this MAC resolves to an agent this server is
    serving, so the next check-in hands the board its real
    configuration."""


@dataclass(frozen=True)
class StillWaiting:
    """The bound was reached with the poll still answering 202."""


Poll = Activated | StillWaiting | Refused


def activation_ceiling(hint: Any) -> float:
    """How long the whole activation ceremony may take.

    The rule, stated once and applied to every remote number this command
    reads: remote input may shorten a wait and may never extend one. The
    field is validated strictly as a positive integer, and anything else
    is ignored rather than refused, because a malformed hint is not a
    reason to fail a ceremony that works without it.

    `bool` is excluded before `int` is asked, since `True` is an `int` in
    Python and a JSON `true` is not a number of milliseconds.
    """
    if isinstance(hint, bool) or not isinstance(hint, int) or hint <= 0:
        return ACTIVATION_CEILING_S
    return min(ACTIVATION_CEILING_S, hint / 1000)


def polled(endpoint: Endpoint, identity: Identity, hint: Any) -> Poll:
    """The firmware's own wait, bounded.

    Bursts of ten, three seconds apart, which is what a board does and
    what `docs/xiaozhi-notes.md` records; and never past the ceiling
    above, which is what a command a person is waiting at needs and a
    board indefinitely re-checking does not.

    The clock is `monotonic` and `sleep`, imported into this module so a
    suite can hold both and assert the cadence exactly rather than
    waiting out half a minute of real time.

    The ceiling bounds the requests as well as the waits between them.
    Each poll is given what is left of it, because a request's own read
    bound is thirty seconds and a ceremony allowed six would otherwise
    spend thirty inside the first poll and discover it afterwards: a
    bound checked only between requests is a bound on the sleeping and
    not on the waiting.
    """
    target = endpoint.activation()
    deadline = monotonic() + activation_ceiling(hint)
    for attempt in range(POLL_ATTEMPTS):
        problem: str | None = None
        answered: httpx.Response | None = None
        remaining = deadline - monotonic()
        if remaining <= 0:
            break
        try:
            answered = requested(
                "POST",
                target,
                build=build_client,
                headers=identity.poll_headers(),
                # What a version-1 poll sends, which upstream's own
                # manager-api reads nothing of.
                body={},
                budget_s=remaining,
            )
        except ConfigError as refusal:
            problem = str(refusal)
        if answered is None:
            return Refused(problem or NOT_A_REPLY)
        if answered.status_code == 200:
            return Activated()
        if answered.status_code != 202:
            return Refused(bad_poll_status(answered.status_code))
        if attempt + 1 == POLL_ATTEMPTS or monotonic() + POLL_INTERVAL_S > deadline:
            break
        sleep(POLL_INTERVAL_S)
    return StillWaiting()


__all__ = [
    "ACCESS_DENIED",
    "ACCESS_OPEN",
    "ACCESS_TOKEN",
    "ACTIVATION_CEILING_S",
    "BOARD_TYPE",
    "CLIENT_ID_NAMESPACE",
    "CONTRADICTORY_ACCESS",
    "CONTRADICTORY_REPLY",
    "DEFAULT_MAC",
    "FIRMWARE_VERSION",
    "GIVEN_URL",
    "KNOWN_ACCESS",
    "MALFORMED_REPLY",
    "NOT_A_REPLY",
    "POLL_ATTEMPTS",
    "POLL_INTERVAL_S",
    "UNKNOWN_PROTOCOL_VERSION",
    "UNUSABLE_WEBSOCKET",
    "Activated",
    "Activating",
    "Admitted",
    "CheckIn",
    "Firmware",
    "Identity",
    "Poll",
    "Refused",
    "StillWaiting",
    "Unwelcome",
    "activation_ceiling",
    "bad_poll_status",
    "bad_status",
    "check_in",
    "polled",
    "read",
]
