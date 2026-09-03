"""Which boundary a write's acknowledgement announces.

Every write to the configuration API and every local write through the
CLI answers with a `notice`, and what it is for is one question: when
does what I just wrote reach a running server. There are four answers,
and which one an act carries is behavior a suite has to hold. The
sentence that carries it is not: prose gets edited, and a suite that
compared the whole string turned an edit that changed no boundary into
a wall of red.

So this is the one place that reads a notice. It knows the phrase each
boundary is announced by and answers in tokens, and every suite
downstream asserts tokens. An edit to a notice that keeps its boundary
keeps every one of them green; an edit that loses the boundary fails
here, loudly, because a notice naming no boundary at all is a notice
that stopped doing its job.

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

from vinga_server.config.entities import PROGRAM

CHECK_IN = "check-in"
RELOAD = "reload"
RESTART = "restart"
STORE_BOOT = "store-boot"

# The phrase each boundary is announced by. Fragments rather than
# sentences, and each one the part an operator acts on: what to wait for,
# or what to run.
_ANNOUNCED_BY = {
    CHECK_IN: "OTA check",
    RELOAD: f"{PROGRAM} apply",
    RESTART: "next server start",
    STORE_BOOT: "starts from this store",
}


def boundaries(notice: str) -> frozenset[str]:
    """Which boundaries one acknowledgement's notice names.

    A notice that names none is the failure this raises on: it would
    leave an operator with a write and no idea when it lands, and it is
    also how a suite would silently stop asserting anything.
    """
    found = frozenset(
        boundary for boundary, phrase in _ANNOUNCED_BY.items() if phrase in notice
    )
    assert found, f"the notice names no boundary at all: {notice!r}"
    return found
