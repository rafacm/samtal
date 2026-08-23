"""The refusal body an API test expects, in one place.

Every route in the `/api` namespace refuses with the same body, so a
suite that spells that body out per assertion is holding one decision in
thirty places. This is the one place, and it offers two ways in.

`refused` is the ordinary one. It checks that a body is the shape every
refusal in this namespace answers with, and hands back the sentence
without comparing it: since #242 a suite asserts what a refusal IS (the
status, the standard reason phrase for it, the members and their types)
and the semantic tokens the sentence carries (a section, an entity, a
field path, a count), never the sentence itself. Wording is the
repository's to choose, and a suite that pinned it turned an edit to a
sentence into a red test with nothing wrong.

`problem` builds the whole body, sentence included, and is for the
assertions that are differential rather than golden: a body compared
against the exception the repository raised holds the two equal without
either of them being written down here. `PROBLEM_KEYS` is the key set,
for a test that is about the members alone.

The body is RFC 9457 problem details (#192): the status's reason phrase,
the status repeated, the repository's own sentence, and the fields the
refusal names, always present and empty where it names none.

Built literally rather than through the `Problem` model. An expectation
assembled from the code it checks agrees with it by construction, which
is not an assertion about anything. The one thing read from the
application is the title, because a table of reason phrases restated
here would be a second copy of a closed set; what the phrases are is
pinned where the table is.
"""

from collections.abc import Sequence

from vinga_server.config.api import PROBLEM_TITLES


def problem(
    status: int, detail: str, errors: Sequence[tuple[str, str]] = ()
) -> dict[str, object]:
    """The whole body of one refusal: the status it was answered under,
    the sentence, and the `(path, message)` pairs it names."""
    return {
        "title": PROBLEM_TITLES[status],
        "status": status,
        "detail": detail,
        "errors": [{"path": path, "message": message} for path, message in errors],
    }


# What a refusal body holds, for a test that is about the shape rather
# than about the sentence in it.
PROBLEM_KEYS = frozenset(problem(422, "any refusal at all"))


def refused(body: object, status: int) -> str:
    """One refusal, checked as a refusal, with its sentence handed back.

    Everything a caller may rely on is asserted here: the members and
    no others, the status repeated in the body, the standard reason
    phrase for it as the title, a non-empty `detail`, and every entry of
    `errors` carrying a string path and a string message. What the
    sentence says is returned rather than compared, so the caller can
    assert the tokens that carry meaning and leave the wording alone.
    """
    assert isinstance(body, dict), body
    assert set(body) == PROBLEM_KEYS, body
    assert body["status"] == status, body
    assert body["title"] == PROBLEM_TITLES[status], body
    detail = body["detail"]
    assert isinstance(detail, str) and detail, body
    errors = body["errors"]
    assert isinstance(errors, list), body
    for error in errors:
        assert isinstance(error, dict) and set(error) == {"path", "message"}, error
        assert isinstance(error["path"], str), error
        assert isinstance(error["message"], str) and error["message"], error
    return detail


def paths(body: object) -> list[str]:
    """The field paths a refusal names, in the order it names them.

    The pointers are semantic: a form marks the field one addresses, so
    which fields a refusal blames and in what order is behavior, while
    the message beside each is wording.
    """
    assert isinstance(body, dict), body
    errors = body["errors"]
    assert isinstance(errors, list), body
    return [error["path"] for error in errors]
