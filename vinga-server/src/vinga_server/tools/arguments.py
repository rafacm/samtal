"""Arguments as the tool declared them, where the model only quoted.

Small local models routinely send an argument under the wrong JSON
type: `{"volume": "100"}` where the schema says `integer`, `"true"`
where it says `boolean`. The far side is entitled to refuse that (the
device firmware validates its own tools, and a strict MCP server may
too), so the model flails across rounds and the device's tools are
decorative on exactly the stack the README tells people to build
(#383).

What this module does about it is bounded to one thing: an argument
whose string form converts to the declared type EXACTLY is converted.
Anything lossy or ambiguous is left as the model sent it and fails the
way it does today. Nothing here guesses: `"one hundred"` is not a
number, `"100.5"` is not an integer, and `"True"` is not a boolean,
because the mistake being undone is quoting and anything past the JSON
literals is spelling.

**The split this belongs to.** The conversion happens on the way OUT,
at the dispatch, and only there. The conversation record, the API's
`ToolInvocation` body and the history re-sent to the model keep the
original values, because "what the model passed" is a promise those
surfaces make and it is the very fact an operator diagnosing a marginal
model needs to see. What the device, the MCP server or the builtin
receives is what its schema declared. A reader of either surface needs
that sentence and no more of this module.

**Lossless is a grammar plus an equivalence check, never a bare
parser.** `float()` and `int()` are parsers with their own dialect
(`"+1"`, `".5"`, `"1."`, `"1_0"`, Unicode digits, `"inf"`), `float()`
rounds silently (`"9007199254740993"` becomes `9007199254740992.0`) and
underflows (`"1e-4000"` becomes `0.0`), and `\\d` admits Unicode digits.
So the text is held to an ASCII grammar first, and a `number` is then
held to `Decimal(text) == Decimal(result)`, which is what rejects
precision loss, overflow and underflow rather than assuming a finite
parse was exact.

**Total by construction.** Every conversion runs inside a guard that
answers the original value on any failure, because an escaped exception
leaks: the dispatch interpolates an exception's message into a stored
tool result, and `float()`'s message repeats the rejected string, which
is model-authored and can be secret-shaped. The guard also covers
Python's integer digit-conversion limit, which a grammar-valid but
enormous digit string reaches.

The stdlib and nothing else, so this stays a leaf beside `publish.py`.
"""

import math
import re
from collections.abc import Callable, Mapping
from decimal import Decimal
from typing import Any

# The two ASCII grammars, and they are the whole of what is admitted.
# Spelled `[0-9]` and compiled `re.ASCII`, which says the same thing
# twice on purpose: `\d` admits every Unicode decimal digit, and a
# string of Arabic-Indic digits is not a model that quoted a number, so
# an edit to the shorter spelling cannot widen these without also
# removing the flag.
_INTEGER = re.compile(r"-?[0-9]+", re.ASCII)
# JSON's own number grammar, minus the leading `+` and the bare `.5`
# and `1.` that JSON does not have either.
_NUMBER = re.compile(r"-?[0-9]+(\.[0-9]+)?([eE][+-]?[0-9]+)?", re.ASCII)

# The two spellings of a JSON boolean, and no others. Not `"True"`, not
# `"1"`, not `"yes"`: see the module note.
_BOOLEANS: Mapping[str, bool] = {"true": True, "false": False}


def with_lossless_coercions(
    arguments: Mapping[str, Any], schema: Mapping[str, Any]
) -> dict[str, Any]:
    """The arguments again, with every top-level property whose value
    converts losslessly to its declared type converted.

    A new dict every time, and the input is never touched: the caller
    keeps the original for the record, the API and the history.

    A value no rule converted is answered as the object it arrived as,
    which is what lets a caller tell a conversion from a value by
    identity rather than by comparison. That is not a convenience: a
    `NaN` is not equal to itself and both provider adapters decode with
    Python's permissive `json.loads`, which accepts one, so a caller
    comparing values alone would read an untouched `NaN` as converted.

    The result is not necessarily schema-conformant, which is why the
    name says what it guarantees rather than promising conformance:
    unions, nested structure, undeclared properties and every other
    declared type pass through exactly as they arrived. The bound is
    deliberate. Device tools are flat, the measured failure is flat, and
    every widening can arrive with its own evidence.

    Tolerant of every degenerate schema `publish()` can let through: it
    admits an empty dict for a non-dict schema, a device that sent none
    gets `{"type": "object"}`, and nothing validates `properties` or the
    entries under it. A schema this cannot read coerces nothing.
    """
    properties = schema.get("properties") if isinstance(schema, Mapping) else None
    if not isinstance(properties, Mapping):
        return dict(arguments)
    return {
        name: _declared(held, properties.get(name)) for name, held in arguments.items()
    }


def _declared(held: Any, declared: Any) -> Any:
    """One value against the entry the schema holds for its property.

    A `type` given as a list is a union, and a union stays strict rather
    than half-guessed: it matches none of the three arms below and the
    value passes through.
    """
    if not isinstance(declared, Mapping):
        return held
    kind = declared.get("type")
    if kind == "integer":
        return _guarded(_integer, held)
    if kind == "number":
        return _guarded(_number, held)
    if kind == "boolean":
        return _guarded(_boolean, held)
    return held


def _guarded(convert: Callable[[Any], Any], held: Any) -> Any:
    """One conversion, answering the original value on any failure.

    The totality guard the module note describes, and it is one function
    rather than a `try` per arm so that a conversion added below cannot
    be added outside it.
    """
    try:
        return convert(held)
    except Exception:
        return held


def _integer(held: Any) -> Any:
    """A quoted whole number, or a whole number a model wrote with a
    decimal point.

    `"05"` converts: its spelling is not canonical and its value is
    exact, which is the only question here. A `bool` reaches neither arm
    (it is neither `str` nor `float`), which is the answer: JSON `true`
    is not `1` declared, whatever Python thinks of the subclass.
    """
    if isinstance(held, str):
        text = held.strip()
        return int(text) if _INTEGER.fullmatch(text) else held
    if isinstance(held, float) and held.is_integer():
        return int(held)
    return held


def _number(held: Any) -> Any:
    """A quoted number, converted only where the conversion loses
    nothing.

    The equivalence check is the second half of the rule and it does the
    work the grammar cannot: a finite parse is not an exact one.
    `Decimal(text)` is the text itself, `Decimal(float)` is the double
    exactly, and the two are equal only where nothing was rounded away,
    which rejects precision loss and underflow together. An `int`
    already satisfies `number` and passes untouched.

    That line falls where binary arithmetic puts it, and it is worth
    knowing which side a value is on: `"0.5"` and `"2.25"` convert
    because their doubles are the numbers they spell, and `"0.1"` does
    not, because its double is a little more than a tenth. A model that
    quotes a decimal fraction with no exact double is left exactly as it
    wrote it, which is the strict direction: widening this admits values
    nobody typed, and a widening arrives with its own evidence.
    """
    if not isinstance(held, str):
        return held
    text = held.strip()
    if not _NUMBER.fullmatch(text):
        return held
    converted = float(text)
    if not math.isfinite(converted) or Decimal(text) != Decimal(converted):
        return held
    return converted


def _boolean(held: Any) -> Any:
    """Exactly the two JSON literals, quoted."""
    if isinstance(held, str) and held in _BOOLEANS:
        return _BOOLEANS[held]
    return held
