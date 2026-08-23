"""Logging setup: the human format, and the JSON one.

Two formats, one handler on the root logger. `text` is what a terminal
wants and what the server has always printed. `json` is one object per
line, which is what a log collector wants, and it is the container
default: the conversation events carry structured fields a collector
can group by session, so a deployment measures its own pipeline from
what it retains. They carry metadata only; the record of what was said
is the conversation store (#120).

One thing is filtered rather than formatted: the libraries that carry
somebody else's bytes are held below the server's level, because their
debug records carry response headers, request lines, frame payloads and
tracebacks nothing here has sanitized. See `quiet_vendor_libraries`
below, which is called from here and, without a level, from the two
places a deployment starts at, because one of them never reaches this
function and the other reaches it after the boot configuration has
already been read out of a database. `quieted` beside it is the same
mechanism for the length of one call, which is what a command whose
argument is a secret needs of the library that would narrate it.

Call sites need no wrapper. Anything passed as `extra=` on an ordinary
logging call becomes a top-level field of the JSON object, found by
comparing the record's attributes against the ones `logging` itself
sets. The message text stays a plain human sentence in both formats,
so nothing is only readable as JSON.

Stdlib only, deliberately: the formatter is short enough that a
structured-logging dependency would cost more than it saves.
"""

import datetime as dt
import json
import logging
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from vinga_server.config.models import ServerConfig

TEXT_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"

# Attributes `logging` puts on every record. Whatever a record carries
# beyond these came from an `extra=`, and is ours to emit. Built from a
# throwaway record rather than hardcoded, so a new stdlib attribute
# never leaks into the output as if it were an event field.
_STANDARD_ATTRIBUTES = frozenset(
    vars(logging.LogRecord("", logging.INFO, "", 0, "", None, None))
) | {"taskName", "message", "asctime"}

# The libraries that render somebody else's bytes into a record of their
# own, and the level below which each of them reaches this handler.
#
# Their debug records are the one surface the taxonomies cannot reach. A
# provider sanitizes what it raises (providers/kit.py) and this server's
# own lines say only what their call site decided to say, but a library
# in the middle narrates the wire, and `server.log_level: DEBUG` is a
# reasonable thing to turn on while diagnosing one: that is all it would
# take to put the lot into the logs the observability ADR makes the
# retained surface.
#
# What each of them narrates:
#
# - The far side of a provider call. The openai and anthropic clients
#   log the request options, the response headers verbatim and the
#   traceback of anything they caught, with httpx and httpcore tracing
#   the connection underneath. A response header is written by the far
#   end, so a compatible endpoint can echo a credential or the request's
#   own content into one.
# - The near side of every request served. `uvicorn.error` carries the
#   HTTP server's trace, which at debug is the request line and every
#   request header: the OTA path holds the deployment's secret segment
#   and a device's handshake carries its bearer token, which is why the
#   access log is off in the first place (`main.uvicorn_config` says
#   so). uvicorn hands that same logger to the websockets protocol, so
#   those records also render every device frame's payload, text
#   decoded.
#
# The MCP SDK narrates its wire the same way, and is deliberately not
# here: `tools/mcp/transport.py` takes its whole namespace off this
# handler entirely (`quiet_sdk_loggers`), which is stronger than a
# floor and is owned by the module that connects with it. A floor here
# as well would be a second rule to keep in agreement with that one.
#
# INFO for these, because what each says at that level is worth keeping
# and carries none of it: httpx's one line per request (the method, the
# URL and the status, no headers and no body), uvicorn's startup and
# per-connection lines.
#
# sqlalchemy is the exception, at WARNING, because INFO is where its
# payload is: an engine whose logger is enabled for INFO echoes every
# statement with the parameters bound to it, and those parameters are
# the stored configuration and, once #120 lands, what was said. The
# library pins its own logger at WARNING when it is imported, and this
# is deliberately not a reliance on that.
#
# There is no configuration key to lift these, and deliberately so. A
# diagnosis that genuinely needs one raises it by name in the process
# that needs it (`logging.getLogger("httpx").setLevel(logging.DEBUG)`),
# which is a deliberate act rather than a side effect of the server's
# own level.
VENDOR_LOG_FLOORS: Mapping[str, int] = {
    "anthropic": logging.INFO,
    "httpcore": logging.INFO,
    "httpx": logging.INFO,
    "openai": logging.INFO,
    "sqlalchemy": logging.WARNING,
    "uvicorn.error": logging.INFO,
}


def quiet_vendor_libraries(level: int | None = None) -> None:
    """Hold each of those libraries at its floor, or at the level the
    caller names when that is higher.

    The maximum rather than the floor alone, so this only ever quietens:
    an operator running at WARNING keeps the silence they asked for, and
    one running at DEBUG gets their own modules' debug lines without the
    libraries' wire traces.

    `level` is the server's own, which only `configure` below knows.
    Without it each library is held against what it is already
    effectively set to, which is what a caller that is not this server's
    logging configuration can honestly say: leave every one of them as
    loud as it is and no louder, and never below its floor. That is the
    call the two places a deployment starts from make before anything
    opens a database or a socket, since one of them (an external ASGI
    runner reaching `app.py:app`) never reaches `configure` at all and
    the other reaches it only after the boot configuration has been
    read. Idempotent, and cheap enough to call on every path that could
    be the first.
    """
    for name, floor in VENDOR_LOG_FLOORS.items():
        logger = logging.getLogger(name)
        against = logger.getEffectiveLevel() if level is None else level
        logger.setLevel(max(against, floor))


@contextmanager
def quieted(names: Iterable[str], level: int) -> Iterator[None]:
    """Hold these loggers at `level` or above for the length of a block,
    and put back exactly what each of them had.

    The scoped sibling of `quiet_vendor_libraries`, and the same
    mechanism for the same reason: a level on a named logger, raised and
    never lowered, so a deployment that had already silenced one keeps
    the silence it asked for.

    What differs is the span rather than the kind. The floors above are
    standing, and are about how loud an operator turned a library up.
    This is about one call whose arguments are a secret, where the
    library's ordinary INFO line is precisely the record that must not
    exist: `doctor.py` names the loggers and says which call.

    What is restored is each logger's own level rather than its
    effective one, so a logger that was inheriting goes back to
    inheriting. Logger levels are process state, and so is this: it is
    for a command line tool doing one thing at a time, and two threads
    calling it over the same names would restore each other's.
    """
    held = [(logging.getLogger(name), logging.getLogger(name).level) for name in names]
    for logger, _ in held:
        logger.setLevel(max(logger.getEffectiveLevel(), level))
    try:
        yield
    finally:
        for logger, was in held:
            logger.setLevel(was)


class JsonFormatter(logging.Formatter):
    """One JSON object per line: the timestamp, level, logger, and
    message every record has, the traceback when there is one, and every
    `extra=` field the call site attached."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": dt.datetime.fromtimestamp(record.created, dt.UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRIBUTES:
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack_info"] = self.formatStack(record.stack_info)
        # default=str so an extra that is not JSON-serializable degrades to
        # its repr instead of losing the whole record.
        return json.dumps(payload, default=str)


def configure(server: ServerConfig) -> None:
    """Install the root handler in the configured format and level.

    Replaces any handler already on the root logger, so calling this
    twice (a reload, a test) does not double every line. Uvicorn's own
    loggers propagate into this one, which is what `log_config=None` at
    the call site arranges.
    """
    handler = logging.StreamHandler()
    handler.setFormatter(
        JsonFormatter() if server.log_format == "json" else logging.Formatter(TEXT_FORMAT)
    )
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(server.log_level)
    quiet_vendor_libraries(root.level)
