"""What JSON can carry, and what it cannot.

The domain configuration is written in YAML and travels as JSON. YAML is
the wider language, so a file can hold values that have no written form
on the wire at all: `!!timestamp` produces a date, `!!binary` bytes and
`!!set` a set; an anchor can make a structure that contains itself; a
mapping key can be something other than a string; and NaN and the
infinities are floats no JSON encoder has a spelling for. A stored row
can hold the last of those and nothing else, because it came out of a
JSON column in the first place.

The policy lives in a module of its own because two callers apply it and
they are on opposite sides of the connection. The repository asks it of
every fragment it parses and of every row it reads back; the CLI asks it
of a fragment before that fragment travels as a request body, which is
the only place a mistake can still be met by a sentence rather than by a
stack trace. One rule and one wording, so whichever of them sees the
value first says the same thing about it. The alternative is what the
encoder does on its own: a TypeError, a ValueError or a RecursionError
with a traceback, in place of the sentence this file exists to produce.

Nothing here quotes a value. A fragment refused by these rules is one
nothing has validated yet, so it may hold anything, including a
credential pasted a line early. Every message names where the value sits
and what kind of thing it is, and stops there.
"""

import math
from collections.abc import Mapping

from vinga_server.config.loader import ConfigError

# Where a refusal about an applied document as a whole rather than about
# one entry of it is located. Named here rather than written out at each
# site, because the repository and the CLI both check the same document
# against the same rule and the two must say one thing.
APPLY_LOCATION = "document"

# What a mapping key becomes in a reported path.
#
# The whole of finding 1 of this PR's review round is here. A path built
# by walking a fragment and joining the keys it finds reports the keys
# themselves, and a fragment refused by these rules is one nothing has
# validated: its keys are bytes a caller wrote. The mistake that
# produces this refusal most often is a credential pasted a line early,
# where the key beside it is the operator's own word for the credential.
#
# So a key is never said. What is said is that a field was reached, and
# how deep: a mapping step is this fixed word and a list step is its
# index, which is a structural fact about the shape rather than
# anything anybody typed. An operator counting the steps can find the
# value; a log holding the sentence holds nothing of it.
#
# `models.safe_location` is the repository's other answer to this
# question and it is deliberately NOT the one used here. It walks a
# location against a pydantic model and keeps the prefix the model
# declares, which is a stronger answer where a model is in hand. There
# is none in hand here: this check runs in front of validation, of a
# fragment whose kind may not even be known yet (the CLI applies it to
# a whole document), and its whole job is to run before anything that
# needs a model can.
FIELD = "<field>"

# NaN and the infinities are not JSON, whatever a YAML parser accepts.
# The message names where the value sits and the rule, which is all
# there is to say about it.
_NOT_FINITE = (
    "{where} is not a finite number, and NaN and infinity cannot be written as JSON, "
    "so a reader of this configuration would be given null in its place"
)

# The rest of what YAML can express and JSON cannot. Each names where it
# is and what kind of thing it is, and never the value: a fragment
# refused here is one nobody has validated yet, so it may hold anything.
_NOT_TRANSPORTABLE = (
    "{where} is a {kind}, which JSON has no way to write, so this configuration "
    "could not be stored or read back as what it says"
)

_RECURSIVE = (
    "{where} contains itself. A configuration that refers to itself has no written "
    "form, so it cannot be stored or read back"
)

_NON_STRING_KEY = (
    "{where} has a key that is a {kind} rather than a string. JSON names every key "
    "with a string, so such a key would silently become one and a reader would be "
    "given a key nobody wrote"
)


def _where(path: str) -> str:
    """A path as a refusal names it, and the root's own name."""
    return path or "the fragment"


def _under(path: str, segment: str) -> str:
    """One structural step further in. The segment is either `FIELD` or a
    list index, and never anything read out of the value."""
    return f"{_where(path)}.{segment}"


def check_transportable(section: str, fragment: object) -> None:
    """A fragment refused if JSON cannot carry it as it is.

    `section` is the fixed word for the part of the configuration being
    written, which is `APPLY_LOCATION` or a kind's `moved_key`, and it is
    a section rather than an address on purpose. The addressed form
    (`providers.<stage>.<name>`) is built out of the stage and the name
    the command line carried, which is where a credential lands when it
    is typed one argument early, and this refusal is reached with a
    value nothing has validated in hand. A test holds every call site to
    passing one of the fixed words.
    """
    problem = untransportable(fragment)
    if problem is not None:
        raise ConfigError(f"invalid {section}: {problem}")


def untransportable(
    value: object,
    path: str = "",
    ancestors: frozenset[int] = frozenset(),
    *,
    numbers_only: bool = False,
) -> str | None:
    """What in `value` JSON cannot carry, said without quoting any of it,
    or None.

    One walk, asked two questions, because the second is the first's
    float branch and nothing else. A fragment somebody wrote is asked
    all of it. A stored row is asked about the numbers only, and that is
    not a narrowing for tidiness: the row came out of a JSON column, so
    it cannot hold any of the rest, and it must be walked without a
    cycle rule because a row cannot refer to itself either.

    Cycle-safe by carrying the containers currently above this one
    rather than every container already seen: two keys pointing at the
    same anchored mapping is a shape JSON writes out twice and reads
    back correctly, so refusing it would refuse a legitimate YAML file.
    A container that is its own ancestor is the one that cannot be
    written at all.
    """
    if not numbers_only and id(value) in ancestors:
        return _RECURSIVE.format(where=_where(path))
    if isinstance(value, Mapping):
        below = ancestors | {id(value)}
        for key, nested in value.items():
            if not numbers_only and not isinstance(key, str):
                return _NON_STRING_KEY.format(where=_where(path), kind=type(key).__name__)
            # `FIELD` and not the key. A key here is bytes a caller
            # wrote and this walk runs in front of validation.
            found = untransportable(
                nested,
                _under(path, FIELD),
                below,
                numbers_only=numbers_only,
            )
            if found is not None:
                return found
        return None
    if isinstance(value, (list, tuple)):
        below = ancestors | {id(value)}
        for position, item in enumerate(value):
            # A position is structural: it is a fact about the shape of
            # the document rather than about anything written in it.
            found = untransportable(
                item,
                _under(path, str(position)),
                below,
                numbers_only=numbers_only,
            )
            if found is not None:
                return found
        return None
    if isinstance(value, float) and not math.isfinite(value):
        # NaN and the infinities have no JSON spelling. A stored one is
        # serialized as null on the way out, which quietly turns a
        # configuration into a different one: the option disappears and
        # the provider falls back to its own default.
        return _NOT_FINITE.format(where=path or "the value")
    if numbers_only:
        return None
    # bool before int, and both before the refusal, because bool is a
    # subclass of int and neither needs naming twice.
    if value is None or isinstance(value, (str, bool, int, float)):
        return None
    return _NOT_TRANSPORTABLE.format(where=_where(path), kind=type(value).__name__)


__all__ = ["APPLY_LOCATION", "FIELD", "check_transportable", "untransportable"]
