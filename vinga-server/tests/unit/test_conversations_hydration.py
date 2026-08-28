"""Stored turns in, messages out, with a budget in between.

Input to output and no database, which is what the module is shaped for:
the thread store hands it rows, so everything it decides can be asserted
by writing turns down and reading messages back.

What the assertions are about is the three properties a rebuilt context
has to have. It alternates roles and opens with the user, whatever
shape the stored turns are in, because that is what a provider is
willing to be handed. It ends with the newest turn, because that is
what a conversation is resumed into. And it says what it could not
bring: the turns it could not read anywhere in the thread, and whether
there were more of them than the budget had room for.

Sizes are written as characters and read as tokens through
`ESTIMATED_CHARS_PER_TOKEN`, so a budget in this file is arithmetic
rather than a guess about a tokenizer.
"""

from vinga_server.conversations.hydration import (
    ESTIMATED_CHARS_PER_TOKEN,
    TOOL_NOTE,
    hydrated,
)
from vinga_server.conversations.records import StoredTurn

# A budget nothing in this file reaches, for the cases that are not
# about truncation.
PLENTY = 6000


def said(index: int, size: int = 8) -> StoredTurn:
    """One whole turn of a known size, distinguishable by its index."""
    return StoredTurn(heard=f"{index}" * size, reply=f"r{index}" * (size // 2))


def roles(turns) -> list[str]:
    return [turn.role for turn in turns]


def texts(turns) -> list[str]:
    return [turn.content for turn in turns]


def test_a_turn_becomes_the_two_messages_it_was() -> None:
    answer = hydrated([StoredTurn(heard="what is the time", reply="Ten past.")], PLENTY)

    assert roles(answer.turns) == ["user", "assistant"]
    assert texts(answer.turns) == ["what is the time", "Ten past."]
    assert (answer.rendered, answer.skipped, answer.over_budget) == (1, 0, False)


def test_the_thread_comes_back_oldest_first() -> None:
    """Read backwards from the newest and answered forwards, because a
    conversation is written in one direction whatever order it was
    chosen in."""
    answer = hydrated([said(1), said(2), said(3)], PLENTY)

    assert roles(answer.turns) == ["user", "assistant"] * 3
    assert texts(answer.turns)[0] == "1" * 8
    assert texts(answer.turns)[-1] == "r3" * 4


def test_the_tools_a_turn_ran_are_named_and_nothing_else_about_them() -> None:
    """Names only: arguments and results are the largest thing a thread
    holds and the least use to a model picking it up again."""
    answer = hydrated(
        [StoredTurn(heard="lights", reply="Done.", tools=("switch_agent", "remember"))],
        PLENTY,
    )

    assert texts(answer.turns)[1] == "Done.\n" + TOOL_NOTE.format(
        names="switch_agent, remember"
    )


def test_a_turn_that_only_ran_tools_still_has_an_assistant_half() -> None:
    answer = hydrated([StoredTurn(heard="lights", tools=("remember",))], PLENTY)

    assert roles(answer.turns) == ["user", "assistant"]
    assert texts(answer.turns)[1] == TOOL_NOTE.format(names="remember")


def test_a_turn_with_no_stored_text_is_a_gap_and_is_counted() -> None:
    """Text-off keeps the turn and none of the words in it. What that
    leaves is a hole, and the count is what lets a resume say the record
    is partial rather than pretending it is whole."""
    answer = hydrated([said(1), StoredTurn(), said(2)], PLENTY)

    assert roles(answer.turns) == ["user", "assistant"] * 2
    assert (answer.rendered, answer.skipped) == (2, 1)


def test_a_thread_with_no_text_at_all_reports_every_turn_as_a_gap() -> None:
    """The walk does not stop at a hole, which is what makes this
    answer the whole count rather than one."""
    answer = hydrated([StoredTurn(), StoredTurn(), StoredTurn()], PLENTY)

    assert answer.turns == ()
    assert (answer.rendered, answer.skipped, answer.over_budget) == (0, 3, False)


def test_a_turn_that_was_heard_and_never_answered_is_a_gap() -> None:
    """The shape a failed reply leaves: the utterance was recorded where
    `heard` is emitted and the reply provider then failed, so the row
    holds half a turn.

    Rendering that half would put two user messages in a row, which is
    the one thing the alternation rule exists to prevent and which some
    vendors refuse outright. The whole partial turn is a hole instead,
    counted like any other."""
    answer = hydrated(
        [
            StoredTurn(heard="what is the weather", reply="Cloudy."),
            StoredTurn(heard="and tomorrow"),
            StoredTurn(heard="are you there", reply="I am."),
        ],
        PLENTY,
    )

    assert roles(answer.turns) == ["user", "assistant", "user", "assistant"]
    assert texts(answer.turns) == [
        "what is the weather",
        "Cloudy.",
        "are you there",
        "I am.",
    ]
    assert (answer.rendered, answer.skipped) == (2, 1)


def test_an_answer_with_nothing_heard_joins_the_answer_before_it() -> None:
    """The shape a move leaves on the thread it lands on: the round the
    move seeded is a turn with an answer and no utterance, because what
    the user said was said on the thread they were moved off.

    It is not a hole, since nothing about it was lost, and it is not a
    message of its own, since two assistant messages in a row is the
    same refusal as two user ones. It is joined onto the answer before
    it, which is what it was: two things said one after the other with
    nothing from the user in between."""
    answer = hydrated(
        [
            StoredTurn(heard="what is out there", reply="Galaxies."),
            StoredTurn(reply="We were talking about galaxies."),
            StoredTurn(heard="go on", reply="Billions of them."),
        ],
        PLENTY,
    )

    assert roles(answer.turns) == ["user", "assistant", "user", "assistant"]
    assert texts(answer.turns)[1] == "Galaxies.\nWe were talking about galaxies."
    # Nothing was lost, so nothing is reported as a gap, and the joined
    # turn is one of the turns this answer rebuilt.
    assert (answer.rendered, answer.skipped) == (3, 0)


def test_an_answer_with_nothing_before_it_is_not_led_with() -> None:
    """A thread that opens with the greeting a move was answered with
    has nothing for that greeting to follow. The first message a
    provider is handed is the user's, so the history opens on the
    utterance after it rather than on an answer to nobody."""
    answer = hydrated(
        [
            StoredTurn(reply="Starting fresh. What shall we talk about?"),
            StoredTurn(heard="the moon", reply="It is up there."),
        ],
        PLENTY,
    )

    assert roles(answer.turns) == ["user", "assistant"]
    assert texts(answer.turns) == ["the moon", "It is up there."]
    assert (answer.rendered, answer.skipped) == (1, 0)


def test_gaps_are_counted_over_the_whole_thread_and_not_the_window() -> None:
    """What the count answers is whether the record has holes in it,
    which is a fact about the thread rather than about the budget.

    Ordered as the reviewer's case is: a hole, then a turn too big to
    keep beside the newest, then the newest. The walk stops at the
    oversized turn, so a count taken as the walk went would report no
    gaps at all and the resume would claim a whole record it does not
    have."""
    units = [StoredTurn(), said(1, size=400), said(2)]

    answer = hydrated(units, _cost(units[2]))

    assert texts(answer.turns) == ["2" * 8, "r2" * 4]
    assert (answer.rendered, answer.skipped, answer.over_budget) == (1, 1, True)


def test_truncation_drops_whole_units_oldest_first() -> None:
    """The unit is the turn. A budget that fits two of three leaves the
    two newest whole, never a reply without the utterance it answered."""
    units = [said(1), said(2), said(3)]
    room = 2 * _cost(units[0])

    answer = hydrated(units, room)

    assert roles(answer.turns) == ["user", "assistant"] * 2
    assert texts(answer.turns)[0] == "2" * 8
    assert (answer.rendered, answer.skipped, answer.over_budget) == (2, 0, True)


def test_a_backlog_that_fits_exactly_is_not_over_budget() -> None:
    """The boundary is inclusive: a thread the budget has exactly room
    for is a thread that fit."""
    units = [said(1), said(2)]

    answer = hydrated(units, _cost(units[0]) + _cost(units[1]))

    assert (answer.rendered, answer.over_budget) == (2, False)


def test_one_token_less_than_exact_drops_the_oldest() -> None:
    units = [said(1), said(2)]

    answer = hydrated(units, _cost(units[0]) + _cost(units[1]) - 1)

    assert (answer.rendered, answer.over_budget) == (1, True)


def test_the_newest_unit_is_taken_even_when_it_alone_is_too_big() -> None:
    """An empty resume is a worse answer than an over-budget one, and
    the budget is an estimate to begin with. The flag is what says so.
    """
    units = [said(1), said(2, size=400)]

    answer = hydrated(units, 8)

    assert texts(answer.turns) == ["2" * 400, "r2" * 200]
    assert (answer.rendered, answer.over_budget) == (1, True)


def test_gaps_between_kept_units_do_not_spend_the_budget() -> None:
    """A hole costs nothing, so a thread recorded half under text-off
    is rebuilt as far back as its words reach."""
    units = [said(1), StoredTurn(), said(2)]

    answer = hydrated(units, _cost(units[0]) + _cost(units[2]))

    assert (answer.rendered, answer.skipped, answer.over_budget) == (2, 1, False)


def test_an_empty_thread_hydrates_to_nothing() -> None:
    assert hydrated([], PLENTY) == hydrated([], 512)


def test_the_same_rows_answer_the_same_way_twice() -> None:
    """Deterministic, because a resume that read differently on a
    Tuesday would be a conversation nobody could reason about."""
    units = [said(index) for index in range(6)]

    assert hydrated(units, 40) == hydrated(units, 40)


def _cost(turn: StoredTurn) -> int:
    """What one unit is estimated at, computed the way the module does
    rather than written down: a budget in this file is then arithmetic
    on the input rather than a number that has to be kept in step."""
    characters = len(turn.heard or "") + len(turn.reply or "")
    return -(-characters // ESTIMATED_CHARS_PER_TOKEN)
