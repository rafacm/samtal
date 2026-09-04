"""What converts losslessly, and what is left exactly as it arrived.

The matrix #383 asks for: one case per declared type, with the refused
shapes beside the accepted ones, because the whole claim of this module
is the line between them. A conversion nobody can name is a guess, and
a guess reaches a device.

Three groups of refusals are worth naming separately, since each was a
finding rather than an idea:

- the parser's own dialect (`"+1"`, `".5"`, `"1."`, `"1_0"`, Unicode
  digits), which `float()` and `int()` accept and JSON does not;
- the inexact parse (a large integer string that rounds, an exponent
  that underflows), which is finite and still lossy;
- and the totality cases, where a conversion that raised would put an
  exception's message, which repeats the rejected string, into a stored
  tool result.
"""

from typing import Any

import pytest

from vinga_server.tools.arguments import with_lossless_coercions


def schema_of(**properties: Any) -> dict[str, Any]:
    """A tool's schema declaring these properties, the shape both far
    sides send."""
    return {"type": "object", "properties": dict(properties)}


INTEGER = schema_of(volume={"type": "integer"})
NUMBER = schema_of(level={"type": "number"})
BOOLEAN = schema_of(permanently={"type": "boolean"})


# --- integer ----------------------------------------------------------


@pytest.mark.parametrize(
    ("sent", "wanted"),
    [
        ("100", 100),
        (" 100 ", 100),
        ("-7", -7),
        # Not canonical, and exact all the same: the value is what this
        # is about, never the spelling.
        ("05", 5),
        ("0", 0),
        # Models write a whole number with a decimal point.
        (100.0, 100),
        (-7.0, -7),
    ],
)
def test_an_integer_arrives_as_the_integer_it_spells(sent: Any, wanted: int) -> None:
    coerced = with_lossless_coercions({"volume": sent}, INTEGER)
    assert coerced == {"volume": wanted}
    assert isinstance(coerced["volume"], int)


@pytest.mark.parametrize(
    "sent",
    [
        "one hundred",
        "100.5",
        "100.0",
        "",
        "  ",
        # The parser's dialect, none of which JSON has.
        "+1",
        "1_0",
        "0x10",
        # Arabic-Indic digits, which `\d` admits and `[0-9]` does not.
        "١٢٣",
        # A boolean is not a number declared, whatever Python's subclass
        # says, and a float with a fraction is not a whole number.
        True,
        False,
        100.5,
        None,
        ["100"],
    ],
)
def test_anything_but_an_exact_integer_is_left_alone(sent: Any) -> None:
    coerced = with_lossless_coercions({"volume": sent}, INTEGER)
    assert coerced == {"volume": sent}
    assert type(coerced["volume"]) is type(sent)


# --- number -----------------------------------------------------------


@pytest.mark.parametrize(
    ("sent", "wanted"),
    [
        ("1.5", 1.5),
        (" -2.25 ", -2.25),
        ("0.5", 0.5),
        ("100", 100.0),
        ("1e3", 1000.0),
        ("2.5e+2", 250.0),
    ],
)
def test_a_number_arrives_as_the_number_it_spells(sent: str, wanted: float) -> None:
    coerced = with_lossless_coercions({"level": sent}, NUMBER)
    assert coerced == {"level": wanted}
    assert isinstance(coerced["level"], float)


def test_an_integer_already_satisfies_number_and_is_untouched() -> None:
    coerced = with_lossless_coercions({"level": 3}, NUMBER)
    assert coerced == {"level": 3}
    assert isinstance(coerced["level"], int)


@pytest.mark.parametrize(
    "sent",
    [
        "a lot",
        "",
        # The parser's dialect again, plus the spellings whose value is
        # not a JSON number at all.
        "+1.5",
        ".5",
        "1.",
        "1_0.5",
        "inf",
        "-inf",
        "nan",
        "Infinity",
        # Finite, and not exact: `float()` rounds this to
        # 9007199254740992.0, and answering with a different number is
        # the one thing this module may not do.
        "9007199254740993",
        # Finite, and not exact in the other direction: this underflows
        # to 0.0.
        "1e-4000",
        # And the overflow, which parses to infinity.
        "1e400",
        True,
    ],
)
def test_anything_but_an_exact_number_is_left_alone(sent: Any) -> None:
    coerced = with_lossless_coercions({"level": sent}, NUMBER)
    assert coerced == {"level": sent}
    assert type(coerced["level"]) is type(sent)


@pytest.mark.parametrize("sent", ["0.1", "0.7", "1.1", "1E-3", "3.14"])
def test_a_decimal_with_no_exact_double_is_left_alone(sent: str) -> None:
    """The line the equivalence check draws, stated on its own because
    it is the surprising half of it.

    A `float` is binary, and most decimal fractions are not exactly
    representable in it: `0.1` parses to a double that is a little more
    than a tenth. `Decimal(text) == Decimal(result)` is therefore false
    for them, and the rule is that anything not exact is left as the
    model sent it. `0.5` and `2.25` convert because their doubles are
    the numbers they spell; `0.1` does not, and fails the way it does
    today rather than arriving as a number nobody typed.
    """
    assert with_lossless_coercions({"level": sent}, NUMBER) == {"level": sent}


# --- boolean ----------------------------------------------------------


@pytest.mark.parametrize(("sent", "wanted"), [("true", True), ("false", False)])
def test_a_quoted_json_boolean_arrives_as_the_boolean(sent: str, wanted: bool) -> None:
    coerced = with_lossless_coercions({"permanently": sent}, BOOLEAN)
    assert coerced == {"permanently": wanted}
    assert isinstance(coerced["permanently"], bool)


@pytest.mark.parametrize("sent", ["True", "TRUE", " true ", "1", "yes", "on", 1, 0, None])
def test_anything_but_the_json_literals_is_left_alone(sent: Any) -> None:
    """The mistake being undone is quoting. Past the two literals it is
    spelling, and spelling is where guessing begins."""
    coerced = with_lossless_coercions({"permanently": sent}, BOOLEAN)
    assert coerced == {"permanently": sent}
    assert type(coerced["permanently"]) is type(sent)


# --- what the schema does not declare ---------------------------------


@pytest.mark.parametrize(
    "schema",
    [
        {},
        {"type": "object"},
        # What a device that sent no schema publishes, and what
        # `publish()` substitutes for a schema that is not a dict.
        {"type": "object", "properties": {}},
        schema_of(volume={}),
        schema_of(volume={"description": "no type at all"}),
        # A union stays strict rather than half-guessed.
        schema_of(volume={"type": ["integer", "string"]}),
        schema_of(volume={"anyOf": [{"type": "integer"}]}),
        schema_of(volume={"type": "string"}),
        schema_of(elsewhere={"type": "integer"}),
        # Nested structure is below the bound this module draws.
        schema_of(volume={"type": "object", "properties": {"n": {"type": "integer"}}}),
    ],
)
def test_a_property_no_schema_declares_a_type_for_passes_through(
    schema: dict[str, Any],
) -> None:
    assert with_lossless_coercions({"volume": "100"}, schema) == {"volume": "100"}


def test_a_nested_value_is_carried_across_untouched() -> None:
    """The bound, stated as an assertion: the top level is coerced and
    what hangs below it is the same object it arrived as."""
    nested = {"inner": "100"}
    coerced = with_lossless_coercions(
        {"volume": "40", "shape": nested}, schema_of(volume={"type": "integer"})
    )
    assert coerced["volume"] == 40
    assert coerced["shape"] is nested


def test_the_arguments_handed_in_are_not_touched() -> None:
    """The caller keeps the originals for the record, the API and the
    history, so this function may not be the reason they change."""
    sent = {"volume": "40"}
    coerced = with_lossless_coercions(sent, INTEGER)
    assert sent == {"volume": "40"}
    assert coerced is not sent


def test_no_arguments_at_all_is_no_arguments_at_all() -> None:
    assert with_lossless_coercions({}, INTEGER) == {}


# --- totality ---------------------------------------------------------
#
# The contract is that this function never raises, and it is worth more
# than tidiness: an exception escaping into the dispatch is interpolated
# into a stored tool result, and `float()`'s message repeats the string
# it rejected, which is model-authored and can be secret-shaped.


# A digit string past `sys.set_int_max_str_digits`, whose default is
# 4300: the grammar admits it and `int()` refuses it.
ENORMOUS = "9" * 5000


@pytest.mark.parametrize(
    ("sent", "schema"),
    [
        (ENORMOUS, INTEGER),
        (ENORMOUS, NUMBER),
        ("a lot", NUMBER),
        # A schema whose `properties` is not a mapping, and one whose
        # entry is not: `publish()` validates the outer dict and nothing
        # under it.
        ("100", {"type": "object", "properties": ["volume"]}),
        ("100", {"type": "object", "properties": "volume"}),
        ("100", {"type": "object", "properties": {"volume": "integer"}}),
        ("100", {"type": "object", "properties": {"volume": None}}),
    ],
)
def test_a_conversion_that_cannot_be_made_answers_the_value_unchanged(
    sent: Any, schema: Any
) -> None:
    assert with_lossless_coercions({"volume": sent, "level": sent}, schema) == {
        "volume": sent,
        "level": sent,
    }


def test_a_schema_that_is_not_a_mapping_at_all_coerces_nothing() -> None:
    """`publish()` substitutes an empty dict for a non-dict schema, so
    this is defence rather than a shape seen in the wild. It is here
    because the alternative to answering is raising mid-reply."""
    schemas: list[Any] = [[], "object", None, 7]
    for schema in schemas:
        assert with_lossless_coercions({"volume": "100"}, schema) == {"volume": "100"}
