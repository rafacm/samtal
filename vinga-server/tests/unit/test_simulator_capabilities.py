"""The honest capability statement, held to being one.

The decision behind this table is that the help says what the simulator
supports AND what it does not, both directions, so that nobody debugs a
deployment believing it is a board. A paragraph cannot be held to that.
Five assertions can, and each of them is held to going red here, because
an assertion that something is complete is exactly the shape that keeps
passing after the thing under it stops working.

The fifth is the one this milestone exists for. Every merge is
releasable and the image publishes on it, so a table advertising the
conversation before the verb that holds one has landed would be help
that lies for the length of a milestone. A supported row must name a
verb the registered tree HAS; a not-available-yet row must name one it
does NOT.
"""

from dataclasses import replace
from typing import get_args

import pytest

from tests.support.config_cli import registered
from vinga_server.config import cli, docgen
from vinga_server.protocol.messages import MESSAGE_TYPES, SERVER_MESSAGE_TYPES
from vinga_server.simulator import capabilities

WIDTH = docgen.HELP_WIDTH


# The five assertions, each as a function so that a bite case can hand it
# a doctored table and watch it fail.


def every_declared_message_is_classified(rows: tuple[capabilities.Capability, ...]) -> None:
    """Both halves of the wire, at (type, state, mode) granularity, both
    ways.

    Both ways because each direction is a different failure. A declared
    message with no row is a claim the help does not make, which is how a
    fourth listening state would arrive silently supported. A row for a
    message the protocol does not declare is a claim about nothing.

    Which rows are message rows is the kind they declare, not the words
    they start with. A prose row may therefore be worded any way at all,
    and an invented message row is counted here only if it says it is
    one.
    """
    named = capabilities.named_message
    declared = {named(row, "sending") for row in capabilities.sent_messages()}
    declared |= {named(row, "reading") for row in capabilities.received_messages()}
    classified = {row.what for row in rows if row.kind == capabilities.MESSAGE}
    assert classified == declared


def every_row_renders_on_the_side_it_declares(
    rows: tuple[capabilities.Capability, ...],
) -> None:
    """The table is the help, rather than being described by it."""
    page = capabilities.epilog(WIDTH)
    sections = {}
    for side, heading in capabilities.HEADINGS.items():
        _, _, tail = page.partition(f"{heading}\n")
        for other in capabilities.HEADINGS.values():
            tail = tail.partition(f"\n{other}")[0]
        sections[side] = " ".join(tail.split())
    for row in rows:
        # Whitespace-flattened, because the page is wrapped and a claim
        # is not about where a line broke.
        assert " ".join(row.what.split()) in sections[row.side], row.what


def the_unsupported_half_is_non_empty_and_reasoned(
    rows: tuple[capabilities.Capability, ...],
) -> None:
    """An "honest" statement that lists nothing unsupported is the exact
    failure the decision exists to prevent, and nothing else in the suite
    would notice it. A reason is what stops a row being parked there."""
    refused = [row for row in rows if row.side == capabilities.UNSUPPORTED]
    assert refused
    assert all(row.reason for row in refused)
    assert all(not row.reason for row in rows if row.side != capabilities.UNSUPPORTED)


def nothing_is_claimed_that_this_version_did_not_ship(
    rows: tuple[capabilities.Capability, ...],
) -> None:
    """The assertion the third side exists for.

    A supported row names a verb the registered tree has. A
    not-available-yet row names one it does not, which is what makes the
    third side a statement about the future rather than a place to park a
    claim.
    """
    for row in rows:
        if row.side == capabilities.SUPPORTED:
            assert registered(row.verb) == row.verb, row.what
        if row.side == capabilities.PENDING:
            assert registered(row.verb) is None, row.what


# And the assertions themselves


def test_every_message_the_protocol_declares_is_classified() -> None:
    every_declared_message_is_classified(capabilities.rows())


def test_the_read_side_is_closed_exactly_as_the_send_side_is() -> None:
    """The second pin, stated as its own claim: a message type the server
    can send and this table does not name is one a session would meet as
    a surprise."""
    # The split is deliberately two different reads. Which rows are
    # message rows at all is the kind they declare. WHICH HALF of the
    # wire one is stays an inspection of the `what`, because that
    # spelling has exactly one author, `named_message`, and this reads
    # that one format rather than English: a direction field on the row
    # would widen the table for the sake of this line alone.
    reading = {
        row.what
        for row in capabilities.rows()
        if row.kind == capabilities.MESSAGE and row.what.startswith("reading ")
    }
    for message_type in SERVER_MESSAGE_TYPES:
        assert any(f"reading {message_type}" in what for what in reading), message_type
    # And nothing named that the server cannot send.
    for what in reading:
        assert what.removeprefix("reading ").split()[0] in SERVER_MESSAGE_TYPES, what


def test_every_entry_renders_into_the_help_on_its_own_side() -> None:
    every_row_renders_on_the_side_it_declares(capabilities.rows())


def test_the_unsupported_half_is_non_empty_and_every_entry_says_why() -> None:
    the_unsupported_half_is_non_empty_and_reasoned(capabilities.rows())


def test_nothing_is_claimed_supported_that_this_milestone_did_not_ship() -> None:
    nothing_is_claimed_that_this_version_did_not_ship(capabilities.rows())


# The same five, held to going red


def test_a_removed_entry_fails_the_completeness_assertion() -> None:
    """Doctored in the direction a real omission takes: one message row
    dropped.

    The row is found by the kind it declares, and named by the one
    spelling `named_message` owns rather than by a prefix.
    """
    abort = capabilities.named_message(
        ("abort", capabilities.NONE_DECLARED, capabilities.NONE_DECLARED), "sending"
    )
    thinned = tuple(
        row
        for row in capabilities.rows()
        if not (row.kind == capabilities.MESSAGE and row.what == abort)
    )

    with pytest.raises(AssertionError):
        every_declared_message_is_classified(thinned)


def test_an_invented_entry_fails_it_from_the_other_side() -> None:
    """The invented row declares itself a message row, which under
    declared kinds is what puts it in front of the assertion at all: a
    claim about the wire that does not say it is one is not counted, and
    saying it is one is what makes it a claim about nothing."""
    invented = (
        *capabilities.rows(),
        capabilities.Capability(
            what="sending frobnicate",
            side=capabilities.PENDING,
            kind=capabilities.MESSAGE,
            verb=capabilities.RUN,
        ),
    )

    with pytest.raises(AssertionError):
        every_declared_message_is_classified(invented)


def test_an_entry_on_a_side_the_help_does_not_render_it_on_fails() -> None:
    """One row moved, which is what "on both sides" would look like from
    the rendering's point of view: the page still shows it where the
    table used to say, and the table now says somewhere else."""
    moved = tuple(
        replace(row, side=capabilities.SUPPORTED, reason="", verb=capabilities.CHECK_IN)
        if row.side == capabilities.UNSUPPORTED and row.what == "MQTT and UDP"
        else row
        for row in capabilities.rows()
    )

    with pytest.raises(AssertionError):
        every_row_renders_on_the_side_it_declares(moved)


def test_an_empty_unsupported_half_fails() -> None:
    with pytest.raises(AssertionError):
        the_unsupported_half_is_non_empty_and_reasoned(
            tuple(
                row
                for row in capabilities.rows()
                if row.side != capabilities.UNSUPPORTED
            )
        )


def test_an_unsupported_entry_with_no_reason_fails() -> None:
    with pytest.raises(AssertionError):
        the_unsupported_half_is_non_empty_and_reasoned(
            tuple(
                replace(row, reason="") if row.side == capabilities.UNSUPPORTED else row
                for row in capabilities.rows()
            )
        )


def test_a_row_claiming_a_verb_the_tree_does_not_have_fails() -> None:
    """The bite that would have caught the original plan: a table
    claiming what the next milestone will write.

    The verb is invented rather than borrowed, because both of this
    noun's verbs are now registered and the assertion is about a claim
    with nothing behind it.
    """
    lying = (
        *capabilities.rows(),
        capabilities.Capability(
            what="playing the reply out loud",
            side=capabilities.SUPPORTED,
            kind=capabilities.PROSE,
            verb=("simulator", "listen"),
        ),
    )

    with pytest.raises(AssertionError):
        nothing_is_claimed_that_this_version_did_not_ship(lying)


def test_a_pending_row_naming_a_verb_that_already_exists_fails() -> None:
    """The other direction: the third side is retired by its rows moving
    off it, not by a shipped verb being parked on it."""
    parked = (
        *capabilities.rows(),
        capabilities.Capability(
            what="checking in",
            side=capabilities.PENDING,
            kind=capabilities.PROSE,
            verb=capabilities.CHECK_IN,
        ),
    )

    with pytest.raises(AssertionError):
        nothing_is_claimed_that_this_version_did_not_ship(parked)


# What the declared kind buys


def test_a_prose_row_may_open_with_a_direction_word() -> None:
    """The trap the kind removes, in the exact shape it had.

    A prose row whose text begins `reading ` used to be a message row to
    the pin below, which told the two halves of the wire apart by that
    prefix, so the firmware row is worded around it. Declared `PROSE`,
    the same words pass, and a future row can say what it means.

    Scoped to this one assertion on purpose. The rendering helper reads
    the canonical `rows()` rather than the tuple it is handed, so a row
    appended here is absent from the page by construction, and patching
    the module's table to hold a synthetic row would pin the patch
    rather than the freedom.
    """
    augmented = (
        *capabilities.rows(),
        capabilities.Capability(
            what="reading the reply's firmware block the way a board reads it",
            side=capabilities.SUPPORTED,
            kind=capabilities.PROSE,
            verb=capabilities.CHECK_IN,
        ),
    )

    every_declared_message_is_classified(augmented)


# What keeps the kind a closed set, since the alias does not
#
# `RowKind` is a `Literal`, and nothing enforces it here: this module is
# outside the one package the type checker runs on, and a frozen
# dataclass validates no annotation at runtime. A misspelled kind would
# render on the help page exactly as it does now and simply be invisible
# to the message pin above, which is the failure the alias looks like it
# prevents and does not. So the set is closed by an assertion, held to
# going red like every other one in this file.


def every_kind_is_one_of_the_declared_two(
    rows: tuple[capabilities.Capability, ...],
) -> None:
    """No row carries a kind outside what `RowKind` declares."""
    held = get_args(capabilities.RowKind)
    for row in rows:
        assert row.kind in held, row.what


def test_every_row_declares_one_of_the_two_kinds() -> None:
    every_kind_is_one_of_the_declared_two(capabilities.rows())


def test_the_constants_are_exactly_the_members_of_the_alias() -> None:
    """One encoding rather than two.

    The rows are written with the constants and the assertion above
    reads the alias, so a third kind added to one and not the other
    would leave a row nothing classifies. Held here instead.
    """
    assert {capabilities.MESSAGE, capabilities.PROSE} == set(
        get_args(capabilities.RowKind)
    )


def test_a_row_whose_kind_is_outside_the_set_fails() -> None:
    """Doctored the way a typo arrives: one message row's kind
    misspelled, which the message pin would silently stop counting."""
    mistyped = tuple(
        replace(row, kind="messsage") if row.kind == capabilities.MESSAGE else row
        for row in capabilities.rows()
    )

    with pytest.raises(AssertionError):
        every_kind_is_one_of_the_declared_two(mistyped)


# What this milestone in particular says


def test_both_verbs_are_claimed_and_the_third_side_is_empty() -> None:
    """What retires the third side, and the reason it is an assertion
    rather than a deletion.

    M1 shipped every conversation row as "not available yet", naming the
    verb that would bring it, so no commit existed in which the table and
    the tree disagreed. M2 flipped those rows in the change that landed
    `run`. What stops the third side becoming a place to park a claim is
    this: it is asserted EMPTY, and the machinery for it stays so that a
    future row parked there fails here rather than shipping as help.
    """
    claimed = {row.verb for row in capabilities.rows() if row.side == capabilities.SUPPORTED}
    assert claimed == {capabilities.CHECK_IN, capabilities.RUN}

    assert [row for row in capabilities.rows() if row.side == capabilities.PENDING] == []

    registered = {row.words for row in cli.COMMANDS}
    assert capabilities.CHECK_IN in registered
    assert capabilities.RUN in registered


def test_the_states_row_says_what_the_reply_is_read_by() -> None:
    """The prose rows are written rather than derived, which is what
    makes them the half that can go stale.

    Two of them said the check-in ends in a token: the states row read a
    board with no token as one that may not speak, and the claim row
    promised the fourth step issued one. Neither survives a deployment
    that issues none and admits the board anyway (#369), so both are
    held here to the vocabulary the reply and the command now use.
    """
    said = {row.what for row in capabilities.rows()}
    [states] = [row for row in said if row.startswith("the four states of the reply")]
    assert "word for how to read the device token" in states

    [claim] = [row for row in said if row.startswith("claiming this board")]
    assert "to be admitted" in claim
    assert "token" not in claim


def test_the_listening_states_are_told_apart_rather_than_claimed_together() -> None:
    """The granularity the pin exists at. A type-level claim would have
    called `listen` supported and published a claim two thirds false."""
    listen = {
        row.what: row.side
        for row in capabilities.rows()
        if row.what.startswith("sending listen")
    }
    assert listen["sending listen (state=start, mode=manual)"] == capabilities.SUPPORTED
    assert listen["sending listen (state=stop, mode=manual)"] == capabilities.SUPPORTED
    assert listen["sending listen (state=detect, mode=manual)"] == capabilities.UNSUPPORTED
    assert listen["sending listen (state=start, mode=auto)"] == capabilities.UNSUPPORTED
    assert listen["sending listen (state=start, mode=realtime)"] == capabilities.UNSUPPORTED
    assert listen["sending listen (state=start)"] == capabilities.UNSUPPORTED


def test_the_inventory_is_read_off_the_models_rather_than_written_here() -> None:
    """A fourth listening state added to the protocol appears in the
    simulator's help as an unclassified row rather than as a silently
    supported one, which is only true while this comes off the model."""
    assert {row[0] for row in capabilities.sent_messages()} == set(MESSAGE_TYPES)
    assert {row[1] for row in capabilities.sent_messages() if row[0] == "listen"} == {
        "start",
        "stop",
        "detect",
    }


def test_the_epilog_is_what_the_help_page_carries() -> None:
    """The table reaches an operator through the command's own page, and
    through the committed reference that renders that page. One
    rendering, so the three cannot disagree."""
    [row] = [row for row in cli.COMMANDS if row.words == capabilities.CHECK_IN]

    assert row.epilog == capabilities.epilog(WIDTH)
    assert capabilities.INTRODUCTION.split(".")[0] in row.epilog
