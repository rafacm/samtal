"""What this simulator does, what it does not, and what it does not yet.

One closed table, and everything that says any of this reads it: the
help epilog is rendered from here, the committed `docs/reference/cli.md`
renders that epilog, and the tests read the same rows. A claim that
appeared in prose and not in the table is impossible, because there is
no prose.

The table has three sides rather than two, and the third is empty. Every
merge to `main` is releasable and the image publishes on it, so a table
that landed advertising a websocket handshake nothing had written yet
would be help that lies for the length of a milestone, which is the exact
failure an honest capability statement exists to prevent. So a row is
`SUPPORTED`, which may only be claimed of what has shipped and which
names the verb that has it; `UNSUPPORTED`, which is permanent and carries
the reason that keeps it off the list rather than a shrug; or `PENDING`,
which names the verb that will bring it.

`PENDING` carried the whole conversation while `check-in` was the only
verb, and `run` emptied it in the change that landed. The side stays
declared, and a case asserts it empty: that is what makes it a place a
future row can be declared honestly rather than a place a claim gets
parked, and deleting the machinery would leave the next milestone with
nowhere to be honest from.

The message rows are derived rather than written. `protocol/messages.py`
is the one home of what a control message is, and the inventory here is
its own types with their own `Literal` members: a fourth listening state
added to the protocol appears in this help as an unclassified row rather
than as a silently supported one. The granularity is `(type, state,
mode)` and not the type, because `listen` holds `start` and `stop` in
`manual` beside `detect`, `auto` and `realtime`, and a type-level claim
would have called the whole of it supported and been two thirds false.
"""

import textwrap
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel

from vinga_server.protocol.messages import (
    MESSAGE_TYPES,
    SERVER_MESSAGE_TYPES,
    declared_values,
)

# The three sides, as the words a reader sees.
SUPPORTED = "supported"

UNSUPPORTED = "not supported"

PENDING = "not available yet"

# The two kinds of row, as a closed set on the side that produces it.
# Every value is authored in this module, so a value outside the set is
# a bug here, which is the same reason `Access` in `ota/reply.py` is a
# `Literal` rather than a `str`.
#
# What the kind decides is whether a row is one of the wire's own
# messages, derived from `protocol/messages.py` below, or one of the
# written statements about everything else. That used to be read off
# the row's first word, which made the English of a prose row load
# bearing; a row now says which it is.
RowKind = Literal[
    # Derived from the protocol's own models, and named by
    # `named_message`.
    "message",
    # Written by hand in `_PROSE_ROWS`, in whatever words say it best.
    "prose",
]

MESSAGE: RowKind = "message"

PROSE: RowKind = "prose"

# The two verbs of the noun. A row names one of them, and which side it
# may sit on follows from whether the registered tree has that verb yet,
# which is the assertion that stops a milestone claiming the next one's
# work.
CHECK_IN = ("simulator", "check-in")

RUN = ("simulator", "run")

# What a message row's absent facet is called. A `hello` declares no
# state and an `abort` declares no mode, and an empty string is what
# stands for "this type does not have one" so that every row is the same
# shape.
#
# The refusal patterns below say "any value here" with None instead, and
# the two are deliberately different words: `listen` with no mode at all
# is a message the protocol declares and one this simulator does not
# send, so "absent" had to be a value a pattern can name rather than the
# same token as "whichever".
NONE_DECLARED = ""

ANY = None


@dataclass(frozen=True)
class Capability:
    """One thing this simulator can or cannot do."""

    # What it is, in one lowercase phrase.
    what: str

    # Which of the three sides it sits on.
    side: str

    # Which of the two kinds of row it is. Required rather than
    # defaulted, because a default is a classification nobody wrote and
    # the point of the field is that the classification is declared.
    kind: RowKind

    # Why it will never be done, which every `UNSUPPORTED` row carries
    # and no other row does. An "honest" statement that lists nothing
    # unsupported is the failure this table exists to prevent, and a
    # reason is what stops a row being parked there.
    reason: str = ""

    # The verb that has it, or that will bring it. Every `SUPPORTED` row
    # names a verb the registered tree has; every `PENDING` row names one
    # it does not.
    verb: tuple[str, ...] = field(default_factory=tuple)


def _facets(model: type[BaseModel], name: str) -> tuple[str, ...]:
    """One field's declared values, plus the absent one where the field
    is optional, or `()` where the model has no such field at all.

    `declared_values` is `protocol/messages.py`'s, because that is where
    the protocol says what a listening state can be; what belongs here is
    only the question of whether "no value at all" is a case, which is a
    question about the TABLE rather than about the wire.
    """
    declared = declared_values(model, name)
    if not declared:
        return ()
    # `mode` is `Literal[...] | None`, and a `listen stop` with no mode is
    # a message the protocol declares as much as one with a mode.
    optional = model.model_fields[name].default is None
    return (*declared, NONE_DECLARED) if optional else declared


def _rows_of(inventory: dict[str, type[BaseModel]]) -> tuple[tuple[str, str, str], ...]:
    """Every `(type, state, mode)` one direction of the wire declares.

    One function for both directions, because both are now the same
    question asked of the same kind of model. What the server declares no
    modes at all is a fact of the protocol that falls out of its models
    rather than a special case written here.
    """
    found: list[tuple[str, str, str]] = []
    for message_type, model in inventory.items():
        states = _facets(model, "state") or (NONE_DECLARED,)
        modes = _facets(model, "mode") or (NONE_DECLARED,)
        found += [(message_type, state, mode) for state in states for mode in modes]
    return tuple(found)


def sent_messages() -> tuple[tuple[str, str, str], ...]:
    """Every `(type, state, mode)` a device may send, off the models."""
    return _rows_of(MESSAGE_TYPES)


def received_messages() -> tuple[tuple[str, str, str], ...]:
    """Every `(type, state, mode)` the server may send.

    The read side is closed exactly as the send side is, so a message
    type this simulator would meet and not know is a row of this table
    rather than a surprise in a session.
    """
    return _rows_of(SERVER_MESSAGE_TYPES)


# Why each message this simulator will never send is one it will never
# send. Ordered, first match wins, and matched facet by facet rather than
# on the whole row, so that a state added beside `detect` inherits
# nothing by accident.
_Pattern = tuple[str, str | None, str | None]

_SENT_REFUSALS: tuple[tuple[_Pattern, str], ...] = (
    (
        ("listen", "detect", ANY),
        "the wake word is decided on the chip: ESP-SR runs there, the server takes no "
        "part in it, and a simulator has no microphone to have heard one with",
    ),
    (
        ("listen", ANY, "auto"),
        "the device owns the listening mode, and auto re-arms itself after each tts "
        "stop, which is a second turn-taking design rather than a flag",
    ),
    (
        ("listen", ANY, "realtime"),
        "realtime is the only mode barge-in exists in, and barge-in is built around the "
        "board's own echo cancellation, which a simulator with no playback has nothing "
        "to do",
    ),
    (
        ("listen", ANY, NONE_DECLARED),
        "this simulator always names the mode it is listening in, so a listen carrying "
        "no mode is one it does not send",
    ),
    (
        ("abort", ANY, ANY),
        "abort is what a PWR press sends mid-reply, and there is no interactive path "
        "here to press anything from",
    ),
    (
        ("mcp", ANY, ANY),
        "the hello omits features.mcp, so this board publishes no tools of its own: a "
        "simulated board has no volume, no screen and no battery to act on",
    ),
)

_RECEIVED_REFUSALS: tuple[tuple[_Pattern, str], ...] = (
    (
        ("mcp", ANY, ANY),
        "the server sends no mcp envelopes to a board whose hello omitted features.mcp, "
        "so there is nothing here to read",
    ),
)


def _matched(row: tuple[str, str, str], refusals: tuple[tuple[_Pattern, str], ...]) -> str:
    """The reason one message row is refused, or the empty string.

    A refusal pattern names the facets it is about and says `ANY` for the
    rest, so `("listen", ANY, "auto")` is about every listening state in
    auto mode and `("listen", ANY, NONE_DECLARED)` is about a listen that
    names no mode at all.
    """
    for pattern, reason in refusals:
        if all(
            wanted is ANY or wanted == held
            for wanted, held in zip(pattern, row, strict=True)
        ):
            return reason
    return ""


def named_message(row: tuple[str, str, str], direction: str) -> str:
    """One message row as a reader meets it, which is the name the help
    prints and the name a test holds the table to. One spelling, so the
    two cannot drift."""
    message_type, state, mode = row
    facets = [f"state={state}" if state else "", f"mode={mode}" if mode else ""]
    said = ", ".join(part for part in facets if part)
    return f"{direction} {message_type}" + (f" ({said})" if said else "")


def _message_rows() -> tuple[Capability, ...]:
    """Both halves of the wire, classified."""
    rows: list[Capability] = []
    for row in sent_messages():
        reason = _matched(row, _SENT_REFUSALS)
        rows.append(_classified(named_message(row, "sending"), reason))
    for row in received_messages():
        reason = _matched(row, _RECEIVED_REFUSALS)
        rows.append(_classified(named_message(row, "reading"), reason))
    return tuple(rows)


def _classified(what: str, reason: str) -> Capability:
    """A message row on the side its reason puts it.

    Everything not refused is the conversation, which `run` holds, so it
    is that verb's. It sat on the third side until that verb existed and
    moved to the first in the change that landed it, which is what stops
    a milestone advertising the next one's work.
    """
    if reason:
        return Capability(what=what, side=UNSUPPORTED, kind=MESSAGE, reason=reason)
    return Capability(what=what, side=SUPPORTED, kind=MESSAGE, verb=RUN)


# Everything that is not a message, written out because it is not
# derivable from anything: what a check-in is, what a poll is, and the
# eight things a person reasonably expects of a board and will not get.
_PROSE_ROWS: tuple[Capability, ...] = (
    Capability(
        what="the check-in POST, with the two headers the handler reads and the body "
        "shape the firmware sends",
        side=SUPPORTED,
        kind=PROSE,
        verb=CHECK_IN,
    ),
    Capability(
        what="the four states of the reply: activating, admitted, unwelcome, and a "
        "refusal for anything else, told apart by the reply's own word for how to read "
        "the device token, so a board a deployment issuing none admits is admitted "
        "rather than turned away",
        side=SUPPORTED,
        kind=PROSE,
        verb=CHECK_IN,
    ),
    Capability(
        what="no redirect is followed, which is the firmware's own behavior and the "
        "reason every device-facing route serves the slashless spelling directly",
        side=SUPPORTED,
        kind=PROSE,
        verb=CHECK_IN,
    ),
    Capability(
        what="the activation poll at Activation-Version 1, in the firmware's cadence of "
        "ten polls three seconds apart, bounded",
        side=SUPPORTED,
        kind=PROSE,
        verb=CHECK_IN,
    ),
    Capability(
        what="claiming this board through the configuration API with --claim, and "
        "checking in again afterwards to be admitted",
        side=SUPPORTED,
        kind=PROSE,
        verb=CHECK_IN,
    ),
    Capability(
        what="the reply's firmware block, read and reported as a board reads it: "
        "whether an image was offered, and whether the version named back is the one "
        "this board announced",
        side=SUPPORTED,
        kind=PROSE,
        verb=CHECK_IN,
    ),
    Capability(
        what="the websocket handshake with its Authorization, Device-Id, Client-Id and "
        "Protocol-Version headers; the last is sent because the firmware sends it and "
        "this server reads nothing from it",
        side=SUPPORTED,
        kind=PROSE,
        verb=RUN,
    ),
    Capability(
        what="the hello exchange, announcing whichever framing version the check-in "
        "reply named, as a websocket text frame",
        side=SUPPORTED,
        kind=PROSE,
        verb=RUN,
    ),
    Capability(
        what="one packaged utterance of Opus, paced the way a microphone delivers it and "
        "sent under the negotiated framing",
        side=SUPPORTED,
        kind=PROSE,
        verb=RUN,
    ),
    Capability(
        what="binary reply frames, counted, size-checked and unwrapped, with the reply's "
        "duration computed from the frame count",
        side=SUPPORTED,
        kind=PROSE,
        verb=RUN,
    ),
    Capability(
        what="the close, reported by its code compared against the closed set this side "
        "knows and named in this side's own words",
        side=SUPPORTED,
        kind=PROSE,
        verb=RUN,
    ),
    Capability(
        what="one turn and one only: the reply is read to its end and then the socket is "
        "closed",
        side=SUPPORTED,
        kind=PROSE,
        verb=RUN,
    ),
    Capability(
        what="a real microphone and speakers",
        side=UNSUPPORTED,
        kind=PROSE,
        reason="they need PortAudio and a runtime encoder, a push-to-talk loop has no "
        "non-interactive path at all, and no CI runner has an audio device, so it "
        "would ship as a headline feature no lane could drive",
    ),
    Capability(
        what="saying anything but the one packaged sentence",
        side=UNSUPPORTED,
        kind=PROSE,
        reason="the audio is encoded once at build time so that what this sends is "
        "byte-identical on a laptop and on a runner; there is no codec in any tier "
        "to encode something else with",
    ),
    Capability(
        what="echo cancellation and barge-in",
        side=UNSUPPORTED,
        kind=PROSE,
        reason="the board's own AEC quality is the number the whole barge-in gate stack "
        "is built around and it is invisible from the server, and a simulator with "
        "no playback has nothing to cancel",
    ),
    Capability(
        what="decoding or playing the reply audio",
        side=UNSUPPORTED,
        kind=PROSE,
        reason="no codec ships in any tier, so what is reported about reply audio is "
        "arithmetic over frames rather than sound",
    ),
    Capability(
        what="fetching and installing a firmware image",
        side=UNSUPPORTED,
        kind=PROSE,
        reason="the block that offers one is read and reported, per the supported row "
        "above, and nothing is ever downloaded: there are no partitions here to "
        "write an image to and no bootloader to hand it to",
    ),
    Capability(
        what="MQTT and UDP",
        side=UNSUPPORTED,
        kind=PROSE,
        reason="vinga implements the websocket transport and promises no other, which is "
        "a bound of the compatibility promise itself",
    ),
    Capability(
        what="Activation-Version 2 and its HMAC",
        side=UNSUPPORTED,
        kind=PROSE,
        reason="the key is burned into a device's eFuses and only the vendor's cloud has "
        "a copy, which is equally true of every consumer board",
    ),
    Capability(
        what="the display, the captive portal and NVS",
        side=UNSUPPORTED,
        kind=PROSE,
        reason="this simulator is pointed at a URL rather than provisioned into one, so "
        "there is nothing to draw on and nothing to persist",
    ),
)


def rows() -> tuple[Capability, ...]:
    """The whole table: the prose rows, then both halves of the wire."""
    return (*_PROSE_ROWS, *_message_rows())


HEADINGS: dict[str, str] = {
    SUPPORTED: "Supported:",
    UNSUPPORTED: "Not supported, and not planned:",
    PENDING: "Not available yet:",
}

INTRODUCTION = (
    "What this simulator is and is not. Both directions, on this page, so that nobody "
    "debugs a deployment believing this is a board. Every line below is read out of one "
    "table, which is the same table the tests hold the command to."
)


def epilog(width: int) -> str:
    """The table as the help page prints it, wrapped where the rest of a
    help page is wrapped.

    Laid out here and printed as laid out: a listing that reflowed into
    prose would run twenty message rows into one paragraph. The width is
    the caller's, so this module takes nothing from the configuration
    grammar to render its own table.
    """
    lines = _wrapped(INTRODUCTION, width)
    for side, heading in HEADINGS.items():
        listed = [row for row in rows() if row.side == side]
        if not listed:
            continue
        lines += ["", heading]
        for row in listed:
            said = row.what
            if row.reason:
                said = f"{said} ({row.reason})"
            if row.side == PENDING:
                said = f"{said} (comes with {' '.join(row.verb)})"
            lines += _wrapped(f"  - {said}", width, indent="    ")
    return "\n".join(lines)


def _wrapped(text: str, width: int, indent: str = "") -> list[str]:
    return textwrap.wrap(
        text,
        width=width,
        subsequent_indent=indent,
        break_long_words=False,
        break_on_hyphens=False,
    )


__all__ = [
    "ANY",
    "CHECK_IN",
    "HEADINGS",
    "INTRODUCTION",
    "MESSAGE",
    "NONE_DECLARED",
    "PENDING",
    "PROSE",
    "RUN",
    "SUPPORTED",
    "UNSUPPORTED",
    "Capability",
    "RowKind",
    "epilog",
    "named_message",
    "received_messages",
    "rows",
    "sent_messages",
]
