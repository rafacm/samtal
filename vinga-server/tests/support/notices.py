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
what a command printed is matched against the lines either side
composes, each of which carries its own boundaries (`entities.Notice`
for the server's, `cli.SPOKEN` for the ones the client says instead of
one). Neither is a table of phrases kept by hand beside the real one,
which is what this module used to be and what a prose edit could
silently move.

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

from vinga_server.config import cli, entities
from vinga_server.config.responses import Applies

CHECK_IN = Applies.CHECK_IN
RELOAD = Applies.RELOAD
RESTART = Applies.RESTART
STORE_BOOT = Applies.STORE_BOOT

# Every sentence this server composes, with the boundaries each one
# announces, read off the pairing rather than restated. The seven are
# module constants because the ones that depend on what the server is
# serving, or on what a transaction moved, are chosen per request from
# these same seven.
_COMPOSED: tuple[entities.Notice, ...] = (
    entities.RESTART_NOTICE,
    entities.BINDING_NOTICE,
    entities.APPLY_NOTICE,
    entities.BINDING_UNSERVED_NOTICE,
    entities.SNAPSHOT_NOTICE,
    entities.RENAME_UNSERVED_NOTICE,
    entities.DEFAULT_AGENT_UNSERVED_NOTICE,
)

# And what the CLI says INSTEAD of one of those, for the boundary sets
# it knows (#426). A rendering prints one voice or the other, so a
# reader of output that knew only the server's would answer "no
# boundary at all" for every write whose set this client can name, which
# is most of them. Read off `cli.SPOKEN` rather than restated here, for
# the reason the sentences above are read off `entities`: a table of
# phrases kept by hand beside the real one is what this module used to
# be, and what a prose edit could silently move.
#
# The import's count line is deliberately not here. Its clause is one
# clause for both known sets, so it cannot say which of the two an
# answer carried, and a reading that guessed would be this module
# asserting less than it claims. The import surface's own suites pin
# that line as bytes instead.
_ANNOUNCED: tuple[tuple[str, tuple[Applies, ...]], ...] = (
    *((notice.sentence, notice.applies) for notice in _COMPOSED),
    *((line, tuple(applies)) for applies, line in cli.SPOKEN.items()),
)


def boundaries(answer: Mapping[str, object] | str) -> frozenset[Applies]:
    """Which boundaries one write is waiting at.

    Given a body (an acknowledgement, or one entry of an applied
    document), the field is the answer and nothing is inferred. Given
    what a command printed, the answer is the boundaries of every line
    either side composes that the output carries, which is how a
    rendering is held to the same fact its body states: each is printed
    whole, so a printed notice is one of them or the output is not a
    notice at all.

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
            for line, applies in _ANNOUNCED
            if line in answer
            for boundary in applies
        )
    assert found, f"the answer names no boundary at all: {answer!r}"
    return found
