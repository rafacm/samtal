"""The client's contract, held against the committed document as DATA.

Every other check of these two halves compares them through the models
they share, which is the one comparison that cannot fail: a field
renamed on a response model moves the API's declaration and the CLI's
reading together, and both stay green while the published contract says
something else. So this reads
`docs/reference/api-openapi.json` as bytes on disk and holds the
grammar's acts to what it says, importing nothing of `config.api` and
building no application. What a generated client would be built from is
exactly what is compared here, which is why this is also the oracle #287
inherits: when the client is generated rather than written, this test is
what says the generator was pointed at the right document.

Three things are compared, in both directions.

- **Which operations exist.** The covered set is derived from
  `cli.COMMANDS` by asking each act for the path it addresses, and the
  exclusion set below names every operation no act covers, with a reason
  apiece. The two are asserted to union to the whole document and to
  overlap nowhere, which is what makes a NEW route a failing test rather
  than an operation nobody noticed: an operation in neither set fails
  from the side it is missing from.
- **What each act sends.** `Act.sends` against the operation's request
  body, both ways, because the four bodies with adapters in front of
  them (a binding, a secret, the default agent, an applied document) are
  exactly the ones a method-and-path comparison leaves free to drift.
- **What each act is answered with.** `Act.answers` against the
  operation's success response, so a renderer reading a shape the server
  stopped sending is a failure here rather than a refusal a user meets.

The paths are not written down. Each act's `path` is a function of an
invocation, so it is given one whose identities are their own parameter
names and asked what it addresses; percent-encoding is undone
afterwards, since a path parameter is the one thing in a URL that is not
a literal. That way the comparison runs through the production code that
builds the address rather than past it.
"""

import json
from pathlib import Path
from typing import Any, get_args, get_origin
from urllib.parse import unquote

import pytest
from pydantic import BaseModel

from vinga_server.config import cli

DOCUMENT_PATH = Path(__file__).resolve().parents[3] / "docs" / "reference" / "api-openapi.json"

DOCUMENT: dict[str, Any] = json.loads(DOCUMENT_PATH.read_text(encoding="utf-8"))

# What no act of this grammar covers, and why.
#
# A closed set with a reason apiece, because an unqualified both-ways
# comparison over every operation cannot pass: the document is the whole
# configuration API and this grammar is one client of it. What the
# reasons have to do is say why an operation is somebody else's, so that
# an operation added here without one is a decision rather than an
# omission.
#
# `/healthz` is deliberately absent from this list. The plan named it as
# an exclusion, and the committed document carries no such operation:
# the health endpoint is the server application's, and this document is
# the configuration API's alone. The union assertion below is what says
# so, rather than an entry claiming to exclude something that is not
# there.
EXCLUDED: dict[tuple[str, str], str] = {
    ("GET", "/providers"): (
        "the collection read. The grammar reads a whole deployment through GET /config "
        "(list, show, export) and one entry through the noun's own show; paging a kind "
        "is the admin UI's (#129)"
    ),
    ("GET", "/mcp-servers"): "the collection read, for the reason GET /providers is",
    ("GET", "/prompt-fragments"): "the collection read, for the reason GET /providers is",
    ("GET", "/agents"): "the collection read, for the reason GET /providers is",
    ("GET", "/devices"): "the collection read, for the reason GET /providers is",
    ("GET", "/default-agent"): (
        "the scalar's own read. It is a setting with two verbs and no reader: what is "
        "stored is a line of the document show prints, and a read command here would "
        "give one noun a verb no other setting has"
    ),
    ("GET", "/sessions/{session}/turns"): (
        "the session's own timeline, which the grammar has no verb for: a turn listing "
        "wraps and a wrapped column is not a column, so reading dialogue is the "
        "conversation noun's (#190, milestone 3) and the admin UI's"
    ),
}


def operations() -> set[tuple[str, str]]:
    """Every operation the committed document declares."""
    return {
        (method.upper(), path)
        for path, methods in DOCUMENT["paths"].items()
        for method in methods
    }


def _addressed(kind: str) -> cli.Invocation:
    """An invocation whose identities are their own parameter names, so
    that asking an act where it goes answers with the templated path the
    document is written in."""
    return cli.Invocation(
        kind=kind,
        stage="{stage}",
        name="{name}",
        mac="{mac}",
        code="{code}",
        slot="{slot}",
        session="{session}",
        conversation="{conversation}",
    )


def covered() -> dict[tuple[str, str], list[tuple[cli.Command, cli.Act]]]:
    """Which operation each act of the grammar addresses, with the row
    that performs it.

    A list per operation, because more than one command can address one:
    `show` and `export` are two renderings of one read, and `list`, the
    whole-deployment `show` and the whole-deployment `export` are three
    of another. The act rather than the row, because the reverse is no
    longer one to one either: `conversation show` performs two.
    """
    found: dict[tuple[str, str], list[tuple[cli.Command, cli.Act]]] = {}
    for row in cli.COMMANDS:
        # Every request the row makes, read off the row: a command whose
        # one output is assembled from two reads addresses two
        # operations, and asking it is what keeps both of them compared.
        for act in row.acts():
            where = (act.method.upper(), unquote(act.path(_addressed(row.kind))))
            found.setdefault(where, []).append((row, act))
    return found


# Reading a schema, from either side
#
# The comparison is by NAME rather than by expanded schema, and that is
# the honest comparison rather than a weaker one: what a client is built
# from is the component a `$ref` names, and the shape behind the name is
# what the drift check already regenerates and diffs. What this adds is
# the half that check cannot see, which is which name each operation
# carries.


class _Absent:
    """What an act that sends no body is, and what an operation that
    declares none is.

    A sentinel of its own, and that is the load-bearing part rather than
    a nicety. Both readers below used to answer None for a shape they
    could not name, and None was also the answer for "there is no body
    at all", so an array, a primitive, a union or a declared type
    nothing here has a rule for compared EQUAL to a request that sends
    nothing. Two facts sharing one value is two facts nobody is
    checking; a shape this cannot name is now a failure with the schema
    in it.
    """

    def __repr__(self) -> str:  # pragma: no cover - read only when a case fails
        return "(no body)"


ABSENT = _Absent()


def _document_shape(schema: Any) -> str:
    """What one schema in the document names, as a client reads it.

    Two shapes have names: a component reference, and an object whose
    values are all one thing. Anything else fails here rather than
    reading as an absence, because the whole point of this file is that
    a shape nobody compared is a shape that can drift.
    """
    if isinstance(schema, dict):
        if "$ref" in schema:
            return str(schema["$ref"]).rsplit("/", 1)[-1]
        inner = schema.get("additionalProperties")
        if schema.get("type") == "object" and inner is not None:
            return f"mapping of {_document_shape(inner)}"
    raise AssertionError(f"the document carries a schema this contract check cannot name: {schema}")


def _declared_shape(shape: object) -> str:
    """The same, for a shape declared on an act, and failing the same
    way: an act that grew a shape with no rule here is an act whose
    contract stopped being compared."""
    if isinstance(shape, type) and issubclass(shape, BaseModel):
        return shape.__name__
    if get_origin(shape) is dict:
        key, value = get_args(shape)
        if key is str:
            return f"mapping of {_declared_shape(value)}"
    raise AssertionError(f"an act declares a shape this contract check cannot name: {shape!r}")


def _named(shape: object) -> str | _Absent:
    """One act's declared shape, or the absence sentinel."""
    return ABSENT if shape is None else _declared_shape(shape)


def _request(where: tuple[str, str]) -> str | _Absent:
    """The shape the document says an operation's body is, or the
    absence sentinel where it declares no body."""
    body = DOCUMENT["paths"][where[1]][where[0].lower()].get("requestBody")
    if body is None:
        return ABSENT
    return _document_shape(body["content"]["application/json"]["schema"])


def _success(where: tuple[str, str]) -> str:
    """The shape the document says an operation answers with."""
    answers = DOCUMENT["paths"][where[1]][where[0].lower()]["responses"]
    codes = sorted(code for code in answers if code.startswith("2"))
    assert len(codes) == 1, (where, codes)
    return _document_shape(answers[codes[0]]["content"]["application/json"]["schema"])


COVERED = covered()

OPERATIONS = sorted(COVERED)

IDS = [f"{method} {path}" for method, path in OPERATIONS]


# Which operations exist


def test_the_covered_and_excluded_sets_union_to_the_whole_document() -> None:
    """The closure, which is what makes a new route fail here.

    An operation an act addresses is covered; an operation named below
    is somebody else's, with the reason on it. Anything in neither is an
    operation nobody decided about, and it fails from whichever side it
    is missing from: a route added to the API with no command and no
    exclusion, or a command pointed at a path the document does not
    have.
    """
    assert set(COVERED) | set(EXCLUDED) == operations()


def test_no_operation_is_both_covered_and_excluded() -> None:
    """The other half of a closed pair. An exclusion that a command grew
    a cover for would otherwise sit there saying nothing is reading it,
    which is the shape of stale that a union assertion alone lets
    through."""
    assert set(COVERED) & set(EXCLUDED) == set()


def test_every_exclusion_names_an_operation_the_document_has() -> None:
    """And an exclusion cannot outlive the route it excuses: a name kept
    after the operation went away would widen the covered set's
    obligation by exactly one and nothing would say so."""
    assert set(EXCLUDED) <= operations()


def test_every_exclusion_carries_a_reason() -> None:
    """The reason is the whole point of the set. An empty one would make
    the list a way of dismissing an operation rather than deciding about
    it."""
    for where, reason in EXCLUDED.items():
        assert len(reason.split()) >= 5, where


# What each act sends and is answered with


@pytest.mark.parametrize("where", OPERATIONS, ids=IDS)
def test_the_request_body_is_the_shape_the_document_declares(where: tuple[str, str]) -> None:
    """Both ways, over every covered operation.

    A body the document declares and no act sends fails here, and so
    does an act sending a body the document says the operation takes
    none of. That is the half a method-and-path comparison leaves free,
    and the four bodies with adapters in front of them are exactly the
    ones it leaves free.
    """
    declared = {_named(act.sends) for _, act in COVERED[where]}
    sent = {act.sends for _, act in COVERED[where]}

    assert len(sent) == 1, f"{where} is addressed by rows that disagree about the body"
    assert declared == {_request(where)}


@pytest.mark.parametrize("where", OPERATIONS, ids=IDS)
def test_the_success_answer_is_the_shape_the_document_declares(where: tuple[str, str]) -> None:
    """The same for what comes back, which is the half a renderer used
    to know on its own."""
    declared = {_declared_shape(act.answers) for _, act in COVERED[where]}

    assert declared == {_success(where)}


@pytest.mark.parametrize("where", OPERATIONS, ids=IDS)
def test_every_shape_an_act_names_is_a_component_of_the_document(where: tuple[str, str]) -> None:
    """And the names are the document's own.

    A shape whose name is not a component is a client type the contract
    never published, which is a drift a both-ways name comparison would
    miss if both sides happened to be wrong in the same way.
    """
    schemas = DOCUMENT["components"]["schemas"]
    for row, act in COVERED[where]:
        for shape in (act.sends, act.answers):
            named = _named(shape)
            if isinstance(named, _Absent):
                continue
            assert named.removeprefix("mapping of ") in schemas, (row.words, named)


def test_the_simulator_reaches_this_api_through_an_act_it_already_had() -> None:
    """`vinga simulator --claim` adds no operation, and that is a claim
    rather than an observation.

    The claim it performs is `ADD_DEVICE`, the same act behind
    `device pending claim`, so there is no second encoding of a claim, no
    new path, no new body and no new row in the covered set above. A
    future `--claim` that grew a request of its own would arrive here as
    an operation nobody decided about, from whichever side it was missing
    from.

    That is why the simulator's rows carry a function rather than an
    `Act`: what they do is talk to a device-facing endpoint, and the one
    thing they do to THIS API is performed through a row that is already
    here.
    """
    simulated = [row for row in cli.COMMANDS if row.words[0] == "simulator"]
    assert simulated
    assert all(not row.acts() for row in simulated)

    [claim] = [row for row in cli.COMMANDS if row.words == ("device", "pending", "claim")]
    assert claim.does is cli.ADD_DEVICE
    assert ("POST", "/devices/pending/{code}") in COVERED


def test_the_document_read_here_is_the_committed_one() -> None:
    """The check that this file is reading anything at all. Every
    assertion above is about a structure loaded from disk, and a path
    that resolved to nothing would make them all vacuous rather than
    red."""
    assert DOCUMENT_PATH.is_file()
    assert DOCUMENT["openapi"].startswith("3.")
    assert len(operations()) > 30
