"""Logging setup: the human format, and the JSON one.

Two formats, one handler on the root logger. `text` is what a terminal
wants and what the server has always printed. `json` is one object per
line, which is what a log collector wants, and it is the container
default: with retention, the conversation events (`heard`, `replied`,
`agent_said`) filtered by session are the transcript of a conversation,
which is what stands in for a conversation store until v3 brings a real
one.

One thing is filtered rather than formatted: the vendor libraries that
talk to providers are held at INFO whatever the server's level, because
their debug records carry response headers and tracebacks nothing here
has sanitized. See `quiet_vendor_libraries` below.

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
from typing import Any

from samtal_server.config.models import ServerConfig

TEXT_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"

# Attributes `logging` puts on every record. Whatever a record carries
# beyond these came from an `extra=`, and is ours to emit. Built from a
# throwaway record rather than hardcoded, so a new stdlib attribute
# never leaks into the output as if it were an event field.
_STANDARD_ATTRIBUTES = frozenset(
    vars(logging.LogRecord("", logging.INFO, "", 0, "", None, None))
) | {"taskName", "message", "asctime"}

# The libraries that speak to a provider on our behalf, and the level
# below which none of them reaches this handler.
#
# Their debug records are the one surface the provider taxonomy cannot
# reach. A provider sanitizes what it raises (providers/kit.py), but the
# openai and anthropic clients log the request options, the response
# headers verbatim and the traceback of anything they caught, with httpx
# and httpcore tracing the connection underneath. A response header is
# written by the far end, so a compatible endpoint can echo a credential
# or the request's own content into one, and `server.log_level: DEBUG`
# is a reasonable thing to turn on while diagnosing a provider: that is
# all it would take to put the lot into the logs the observability ADR
# makes the retained surface.
#
# INFO rather than WARNING, because httpx's one line per request (the
# method, the URL and the status, no headers and no body) is worth
# keeping and is the only thing any of the four says at that level.
VENDOR_LOGGERS = ("anthropic", "httpcore", "httpx", "openai")
VENDOR_LOG_FLOOR = logging.INFO


def quiet_vendor_libraries(level: int) -> None:
    """Hold the vendor libraries at the floor, or at the server's own
    level when that is higher.

    The maximum rather than the floor alone, so this only ever quietens:
    an operator running at WARNING keeps the silence they asked for, and
    one running at DEBUG gets their own modules' debug lines without the
    vendors' request traces."""
    for name in VENDOR_LOGGERS:
        logging.getLogger(name).setLevel(max(level, VENDOR_LOG_FLOOR))


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
