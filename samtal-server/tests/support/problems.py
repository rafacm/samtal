"""The refusal body an API test expects, in one place.

Every route in the `/api` namespace refuses with the same body, so a
suite that spells that body out per assertion is holding one decision in
thirty places. This is the one place: `problem` builds what a refusal
answers with, and `PROBLEM_KEYS` is its key set, for the assertions that
pin what a refusal carries without pinning the sentence.

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

from samtal_server.config.api import PROBLEM_TITLES


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
