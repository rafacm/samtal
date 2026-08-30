"""How a listing on the gated `/api` is asked for one page at a time.

Two namespaces answer paginated listings now, the conversation record's
and memory's, and what a page IS is one decision rather than one per
namespace: how many rows it may hold, what a caller sends to ask for the
next one, what a refused argument is told, and how the answer says
whether there is anything beyond it. A second copy of that in the second
namespace would be two structures that must agree, with nothing holding
them to it.

What a caller of this module gets is the whole of the contract and none
of its arithmetic: the two bounds as constants, one function per
argument that either answers the value or raises this API's own refusal,
and one function that turns "one row more than the page holds" into a
page and the cursor after it.

Three rules run through it, and each is the reason a piece of it exists.

- **A cursor is plain values a caller can read, never an encoding to
  version.** What a listing answers with is what a caller sends back,
  and there is nothing here a later release has to go on decoding.
- **A refused argument is never quoted back.** A limit or a cursor that
  cannot be read answers with a fixed sentence describing what the
  argument has to be. What arrived is the caller's, and these are among
  the few values these routes are handed outside a request body.
- **The bound is the contract rather than a courtesy.** A page is
  assembled in memory, so an unbounded limit is an unbounded response.

The refusals are `ConfigError`, which the configuration API maps to 422,
so a namespace using this module inherits the status without deciding
it.
"""

from collections.abc import Sequence
from typing import Any

from vinga_server.config.loader import ConfigError

# How many rows a page holds when the caller says nothing, and the most
# it may ask for.
LIMIT_DEFAULT = 50
LIMIT_MAX = 200

# The range of the `bigint` identity columns the row-id cursors are.
# True by declaration since the schemas say `bigint` (#283), rather than
# by folklore about what a row id happens to be. A cursor beyond it is
# refused here rather than bound into a statement, where it would be a
# driver error and a 500 instead of the caller's own mistake.
MAX_ROW_ID = 2**63 - 1

# How long a number may be written before it is refused without being
# converted: nineteen digits is `MAX_ROW_ID`'s own length, and `int` on
# a very long string is work no caller should be able to ask for.
_DIGITS = 19

LIMIT_REFUSED = (
    f"limit has to be a whole number between 1 and {LIMIT_MAX}, or absent for "
    f"{LIMIT_DEFAULT}. What was sent is not quoted back"
)

CURSOR_REFUSED = (
    "cursor has to be one of the row ids this API answers with, as a whole number, "
    "or absent for the first page. What was sent is not quoted back"
)


def limit(value: str | None) -> int:
    """How many rows this page may hold, or the refusal naming the rule.

    Absent is the default rather than a mistake: a client that has no
    opinion asks for none, and the number it then gets is the one the
    refusal above names, so a document and a refusal cannot disagree
    about it.
    """
    number = whole(value)
    if value is not None and (number is None or not 1 <= number <= LIMIT_MAX):
        raise ConfigError(LIMIT_REFUSED)
    return LIMIT_DEFAULT if number is None else number


def cursor(value: str | None) -> int | None:
    """Where to carry on from, as the row id it is, or None for the
    first page."""
    number = whole(value)
    if value is not None and (number is None or number > MAX_ROW_ID):
        raise ConfigError(CURSOR_REFUSED)
    return number


def whole(value: str | None) -> int | None:
    """A non-negative whole number, or None for anything else, the
    absent argument included.

    `isdigit` rather than a bare `int()`: that accepts a sign, an
    underscore and digits outside ASCII, and what these arguments are is
    a row id or a count, which none of those spellings is. Bounded in
    length before it is converted.
    """
    if value is None or len(value) > _DIGITS or not (value.isascii() and value.isdigit()):
        return None
    return int(value)


def page(found: Sequence[dict[str, Any]], size: int, key: str = "id") -> dict[str, Any]:
    """One page and the cursor after it, from one row more than the page
    holds.

    The extra row is what makes `next_cursor` honest without a second
    count: it is null exactly when there was nothing beyond this page at
    the moment it was read.

    `key` is the column the listing orders on and therefore the value a
    caller sends back. It is the row id for every listing that walks
    monotonic ids, and the owner text for the ones that walk a name.
    """
    items = list(found[:size])
    return {
        "items": items,
        "next_cursor": items[-1][key] if len(found) > size else None,
    }


__all__ = [
    "CURSOR_REFUSED",
    "LIMIT_DEFAULT",
    "LIMIT_MAX",
    "LIMIT_REFUSED",
    "MAX_ROW_ID",
    "cursor",
    "limit",
    "page",
    "whole",
]
