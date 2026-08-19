"""Reading the structured log a suite has just provoked.

A conversation says what it did through `extra=` fields on log records,
never in the message text, so a test that is about an event reads
`caplog.records` and looks for the `event` field. What belongs here is
that reading: selecting the records of one event, insisting a run
produced exactly one of them, separating a record's structured half
from the attributes every record carries, and rendering a run every way
a deployment would keep it, which is what a no-leak test hunts in.
Nothing here provokes an event, so this module knows nothing of
sessions, sockets or providers, and imports only pytest and `logs.py`'s
own formatters and attribute set.

The MCP suites called the first two `emitted` and `one_event` and the
session suites called them `events` and `only`. They were the same two
functions, so one pair lives here and the modules that spelled them the
other way keep their spelling through an import alias.
"""

import logging

import pytest

from vinga_server.logs import _STANDARD_ATTRIBUTES, TEXT_FORMAT, JsonFormatter


def events(caplog: pytest.LogCaptureFixture, name: str) -> list[logging.LogRecord]:
    """Every record of one event, in the order it was emitted."""
    return [record for record in caplog.records if getattr(record, "event", None) == name]


def only(caplog: pytest.LogCaptureFixture, name: str) -> logging.LogRecord:
    matching = events(caplog, name)
    assert len(matching) == 1, f"expected one {name} record, got {len(matching)}"
    return matching[0]


def both_formats(caplog: pytest.LogCaptureFixture) -> str:
    """Every record this server wrote, rendered every way a deployment
    would keep it: the human sentence, the JSON object, the arguments
    behind both, and the exception info a traceback would be built from.

    What a no-leak test hunts a sentinel in. One string rather than a
    walk over records, because the claim is "nowhere", and a walk that
    forgets a rendering asserts less than it reads. Only this server's
    channels: a suite driving `TestClient` puts httpx's own request line
    in `caplog` too, and what the test's HTTP client says about the URL
    it just fetched is not something this server chose to write."""
    human = logging.Formatter(TEXT_FORMAT)
    machine = JsonFormatter()
    return "\n".join(
        f"{record.getMessage()}\n{record.args!r}\n{record.exc_info!r}\n"
        f"{human.format(record)}\n{machine.format(record)}"
        for record in caplog.records
        if record.name.startswith("vinga_server")
    )


def fields_of(record: logging.LogRecord) -> dict[str, object]:
    """The structured half of a record: exactly the attributes the JSON
    formatter writes as top-level keys, read through `logs.py`'s own
    standard-attribute set rather than through a list written here, so a
    field these tests do not know about is a failure rather than a
    silence."""
    return {
        key: value for key, value in vars(record).items() if key not in _STANDARD_ATTRIBUTES
    }
