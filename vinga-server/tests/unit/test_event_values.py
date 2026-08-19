"""What a typed event's vocabulary admits, and what it says when it
refuses.

Two claims, and the second is the one that matters more. The first is
ordinary: each value type accepts what its kind describes and refuses
what it does not, at construction rather than at emit, so a site that
holds one has already proved it. The second is the no-leak claim these
types inherit from the enforcement diagnostics: the value handed to a
refusing constructor is precisely what may not reach a log, a lane's
stderr or an exception chain, so a credential-shaped sentinel goes
through every refusing branch and is hunted in the exception's `str`,
its `repr` and its `args`.

Asserted by absence AND by equality where the shape allows it, for the
reason the sentinel suite gives: a substring hunt proves only that this
spelling did not appear.
"""

import os
from pathlib import Path

import pytest

from vinga_server.events.values import (
    ABSENT,
    ClassName,
    ConfiguredPath,
    Count,
    EventName,
    EventValueError,
    Identifier,
    SessionId,
)

# The same spelling the enforcement sentinels use: printable, so it is
# an ordinary string rather than something a type check would catch
# anyway, and dotted, so it satisfies no declared `ID` syntax.
SENTINEL = "sk.leak.4a7d2f1e.never-a-real-credential"


# --- what each type admits --------------------------------------------


def test_an_identifier_is_any_configured_name() -> None:
    """The configuration's own domain and no tighter: a quote and a
    control character are lawful configuration today, and a value type
    claiming more would refuse a deployment the configuration took."""
    assert Identifier('secondary"agent').carried() == 'secondary"agent'
    assert Identifier("a\x07b").carried() == "a\x07b"


@pytest.mark.parametrize("refused", ["", "   ", 7, None])
def test_an_identifier_refuses_what_is_not_a_name(refused: object) -> None:
    with pytest.raises(EventValueError):
        Identifier(refused)  # type: ignore[arg-type]


def test_a_session_id_is_the_bounded_machine_form() -> None:
    assert SessionId("alpha").carried() == "alpha"
    assert SessionId("a" * 64).carried() == "a" * 64


@pytest.mark.parametrize("refused", ["", "a" * 65, "has space", "dotted.id", 7])
def test_a_session_id_refuses_anything_outside_its_syntax(refused: object) -> None:
    with pytest.raises(EventValueError):
        SessionId(refused)  # type: ignore[arg-type]


def test_an_event_name_is_the_catalogs_own_key() -> None:
    assert EventName("conversations_enabled").carried() == "conversations_enabled"
    with pytest.raises(EventValueError):
        EventName("Conversations")


def test_a_class_name_is_a_python_identifier() -> None:
    assert ClassName("RuntimeError").carried() == "RuntimeError"


@pytest.mark.parametrize("refused", ["", "not a class", "near a value: syntax error", 7])
def test_a_class_name_refuses_a_message(refused: object) -> None:
    with pytest.raises(EventValueError):
        ClassName(refused)  # type: ignore[arg-type]


def test_a_class_name_is_built_from_the_failure_itself() -> None:
    """`of` takes the exception rather than a string, which is what
    keeps a site from spelling `str(exc)` one edit later."""
    failure = RuntimeError("near a value nothing may repeat: syntax error")

    named = ClassName.of(failure)

    assert named.carried() == "RuntimeError"
    assert str(failure) not in repr(named)


def test_a_count_is_zero_or_more_and_never_a_boolean() -> None:
    assert Count(0).carried() == 0
    assert Count(90).carried() == 90
    for refused in (-1, True, 1.5, "2"):
        with pytest.raises(EventValueError):
            Count(refused)  # type: ignore[arg-type]


def test_a_configured_path_carries_text_and_renders_the_object() -> None:
    """The one value whose two surfaces differ, and the difference is
    the surface's own: the field holds the path as text, the sentence
    renders the object the site passed."""
    directory = Path("/var/lib/vinga")

    value = ConfiguredPath(directory)

    assert value.carried() == os.fspath(directory)
    assert value.rendered() is directory


@pytest.mark.parametrize("refused", ["", "  ", 7, None])
def test_a_configured_path_refuses_what_is_not_a_path(refused: object) -> None:
    with pytest.raises(EventValueError):
        ConfiguredPath(refused)  # type: ignore[arg-type]


def test_absence_is_its_own_value_rather_than_null() -> None:
    """A field that is present and null is a fact the record states; a
    field that is absent is a key the JSON object does not have. The
    two are different answers and this is the second one."""
    assert ABSENT is not None
    assert repr(ABSENT) == "ABSENT"


# --- and none of them ever repeats what it refused --------------------


# Every type that can refuse the sentinel on its VALUE rather than on
# its Python type. `Identifier` and `ConfiguredPath` are deliberately
# absent: both admit any non-blank string, because that is what the
# configuration guarantees, so neither has a value-shaped refusal to
# drive. Their type-shaped refusals are asserted above.
REFUSING = (
    ("session id", lambda: SessionId(SENTINEL)),
    ("event name", lambda: EventName(SENTINEL)),
    ("class name", lambda: ClassName(SENTINEL)),
    ("count", lambda: Count(SENTINEL)),
)


@pytest.mark.parametrize("name, build", REFUSING, ids=[one for one, _ in REFUSING])
def test_a_refusal_never_repeats_the_value_it_refused(
    name: str, build: object
) -> None:
    """The rule the whole surface keeps, applied one layer earlier than
    the enforcement diagnostics keep it. A construction refusal reaches
    a lane's stderr in strict mode and the emitter's guard in forgiving
    mode, and the value is what neither may carry."""
    with pytest.raises(EventValueError) as raised:
        build()  # type: ignore[operator]

    assert SENTINEL not in str(raised.value)
    assert SENTINEL not in repr(raised.value)
    assert SENTINEL not in repr(raised.value.args)


def test_an_identifier_refusal_names_the_type_and_the_constraint() -> None:
    """By equality rather than by absence, because absence alone proves
    only that this spelling did not appear."""
    with pytest.raises(EventValueError) as raised:
        Identifier("   ")

    assert raised.value.args == ("an Identifier is non-empty once stripped",)
