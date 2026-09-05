"""Which boundary a write's acknowledgement announces.

Every write to the configuration API and every local write through the
CLI answers with a `notice`, and what it is for is one question: when
does what I just wrote reach a running server. There are four answers,
and which one an act carries is behavior a suite has to hold. The
sentence that carries it is not: prose gets edited, and a suite that
compared the whole string turned an edit that changed no boundary into
a wall of red.

So this is the one place that reads a notice, and it answers in the
tokens the API publishes, which every suite downstream asserts. An edit
to a notice that keeps its boundary keeps every one of them green.

What it no longer does is guess. A body carries `applies` beside the
sentence, so the boundary is read off the field where there is a body;
what a command printed is matched against the sentences this server
composes, each of which carries its own boundaries
(`entities.Notice`). Neither is a table of phrases kept by hand beside
the real one, which is what this module used to be and what a prose
edit could silently move.

The four, and why they are four rather than two:

- `CHECK_IN`, the device asking. Device bindings and the default agent
  are read as a device asks for them, so nothing is asked of the server.
- `RELOAD`, an operator asking. The whole rest of the domain half is
  applied by `POST /runtime/config/reload`.
- `RESTART`, this process starting again. The file half only, which this
  API does not write; it is declared and stays reachable.
- `STORE_BOOT`, some server starting from this store. What a write is
  told when the server answering it serves a configuration it was handed
  rather than one it read, so nothing it is running reads what was
  written.

A binding to an agent this server is not serving names two of them at
once, which is why the answer is a set rather than a token.
"""

from collections.abc import Mapping

from vinga_server.config import entities
from vinga_server.config.responses import Applies

CHECK_IN = Applies.CHECK_IN
RELOAD = Applies.RELOAD
RESTART = Applies.RESTART
STORE_BOOT = Applies.STORE_BOOT

# Every sentence this server composes, with the boundaries each one
# announces, read off the pairing rather than restated. The six are
# module constants because the ones that depend on what the server is
# serving, or on what a transaction moved, are chosen per request from
# these same six.
_COMPOSED: tuple[entities.Notice, ...] = (
    entities.RESTART_NOTICE,
    entities.BINDING_NOTICE,
    entities.APPLY_NOTICE,
    entities.BINDING_UNSERVED_NOTICE,
    entities.SNAPSHOT_NOTICE,
    entities.RENAME_UNSERVED_NOTICE,
)


def boundaries(answer: Mapping[str, object] | str) -> frozenset[Applies]:
    """Which boundaries one write is waiting at.

    Given a body (an acknowledgement, or one entry of an applied
    document), the field is the answer and nothing is inferred. Given
    what a command printed, the answer is the boundaries of every
    sentence this server composes that the output carries, which is how
    a rendering is held to the same fact its body states: the CLI prints
    the sentence whole, so a printed notice is one of the five or the
    output is not a notice at all.

    An output naming no boundary at all is the failure this raises on:
    it would leave an operator with a write and no idea when it lands,
    and it is also how a suite would silently stop asserting anything.
    """
    if isinstance(answer, Mapping):
        applies = answer["applies"]
        assert isinstance(applies, tuple | list), f"not a sequence of tokens: {applies!r}"
        found = frozenset(Applies(token) for token in applies)
    else:
        found = frozenset(
            boundary
            for notice in _COMPOSED
            if notice.sentence in answer
            for boundary in notice.applies
        )
    assert found, f"the answer names no boundary at all: {answer!r}"
    return found
