"""The CLI's shape predicates, against the models the API answers with.

`cli.py` decides whether a body it was handed can be read as a pending
listing, a status entry, a reload's answer or a prompt block by looking
for a fixed set of field names. Each of those frozensets is a second
encoding of a model in `api.py`, written out by hand and connected to
the model by nothing: a field renamed on the model leaves the CLI
refusing every well-formed answer the server gives, and neither file
says so.

These tests are the bridge between the two encodings and nothing else.
They import both sides and state the relation each pair actually holds,
which is not equality everywhere: a predicate names what the CLI needs
in order to read an answer, and a model carries what the API answers,
so where the CLI renders less than the model carries the relation is a
subset and saying otherwise would invent an equality the code does not
hold.

This file exists to be deleted wholesale by #139, which has the CLI
render the API's responses through the response models themselves and
deletes these predicates. When the frozensets go, this bridge goes with
them: two encodings are what it is for, and there is nothing here worth
keeping once there is one.

`cli.PENDING_COLUMNS` is deliberately not pinned. Its members (`code`,
`device`, `expires`) are column headings a person reads, not field
names, and pinning presentation to a model's fields would invent a
second equality the code does not hold either.
"""

import typing

from samtal_server.config import api, cli


def _is_list_of_strings(annotation: object) -> bool:
    return typing.get_origin(annotation) is list and typing.get_args(annotation) == (str,)


def test_the_pending_fields_are_all_carried_by_the_listing_model() -> None:
    """A subset, not an equality. `PendingDevice` also answers
    `client_id`, `first_seen` and `last_seen`, which the listing does
    not render, so the CLI requires only the fields it reads."""
    assert cli.PENDING_FIELDS <= set(api.PendingDevice.model_fields)


def test_the_status_fields_are_exactly_the_status_models_fields() -> None:
    """Here it is an equality: the CLI renders the whole status entry,
    so a field on one side and not the other is drift either way."""
    assert set(cli.STATUS_FIELDS) == set(api.McpServerStatus.model_fields)


def test_the_status_states_are_exactly_the_ones_the_model_allows() -> None:
    """`state` is a Literal on the model, and the vocabulary is part of
    the shape: a CLI that printed whatever arrived there would be
    printing a word chosen by whatever answered."""
    allowed = typing.get_args(api.McpServerStatus.model_fields["state"].annotation)
    assert allowed, "the state field is not a Literal any more, so this pin is vacuous"
    assert set(cli.STATUS_STATES) == set(allowed)


def test_the_reload_outcomes_are_exactly_the_models_outcome_lists() -> None:
    """Every field of the result but `servers`, which is the status
    mapping carried beside the outcomes rather than an outcome. Taken
    off the annotations rather than listed here, so a fifth outcome
    added to the model is a fifth this fails for."""
    outcomes = {
        name
        for name, field in api.McpReloadResult.model_fields.items()
        if _is_list_of_strings(field.annotation)
    }
    assert outcomes, "no list-of-names fields on the result, so this pin is vacuous"
    assert set(cli.RELOAD_OUTCOMES) == outcomes


def test_no_reload_outcome_is_named_twice() -> None:
    """`RELOAD_OUTCOMES` is a tuple because the CLI prints the outcomes
    in the order a person reads them. The set comparison above passes a
    duplicated entry, which prints one outcome's line twice."""
    assert len(cli.RELOAD_OUTCOMES) == len(set(cli.RELOAD_OUTCOMES))


def test_the_prompt_block_fields_are_the_models_required_ones() -> None:
    """Equality against what `PromptBlock` requires, not against every
    field it has. `name` is optional on the model, null for every block
    that did not come from a published prompt, so a CLI requiring it
    would refuse most well-formed blocks; it stays optional, type
    checked where it is present and never demanded. That distinction is
    what this pin encodes, which is why the right-hand side is
    `is_required()` and not `model_fields`."""
    required = {name for name, field in api.PromptBlock.model_fields.items() if field.is_required()}
    assert set(cli.PROMPT_BLOCK_FIELDS) == required
