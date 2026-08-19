"""The refusal body an API test expects, in one place.

Every route in the `/api` namespace refuses with the same body, so a
suite that spells that body out per assertion is holding one decision in
thirty places. This is the one place: `problem` builds what a refusal
answers with, and `PROBLEM_KEYS` is its key set, for the assertions that
pin what a refusal carries without pinning the sentence.

Today the body is the sentence and nothing else. Issue #192 turns it
into an RFC 9457 problem document with a title, the status repeated and
a list of the fields it names, which is why this takes the status and
the field errors before either is on the wire: the suites reshape once,
here, and the change that puts the new shape on the wire changes this
function rather than the suites again.

Built literally rather than through the `Problem` model. An expectation
assembled from the code it checks agrees with it by construction, which
is not an assertion about anything.
"""

from collections.abc import Sequence


def problem(
    status: int, detail: str, errors: Sequence[tuple[str, str]] = ()
) -> dict[str, object]:
    """The whole body of one refusal: the status it was answered under,
    the sentence, and the `(path, message)` pairs it names."""
    return {"detail": detail}


# What a refusal body holds, for a test that is about the shape rather
# than about the sentence in it.
PROBLEM_KEYS = frozenset(problem(422, "any refusal at all"))
