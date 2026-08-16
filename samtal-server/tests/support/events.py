"""Reading the structured log a suite has just provoked.

A conversation says what it did through `extra=` fields on log records,
never in the message text, so a test that is about an event reads
`caplog.records` and looks for the `event` field. What belongs here is
that reading: selecting the records of one event, insisting a run
produced exactly one of them, and separating a record's structured half
from the attributes every record carries. Nothing here provokes an
event, so this module knows nothing of sessions, sockets or providers,
and imports only pytest and `logs.py`'s own attribute set.

The MCP suites called the first two `emitted` and `one_event` and the
session suites called them `events` and `only`. They were the same two
functions, so one pair lives here and the modules that spelled them the
other way keep their spelling through an import alias.
"""

import logging

import pytest

from samtal_server.logs import _STANDARD_ATTRIBUTES


def events(caplog: pytest.LogCaptureFixture, name: str) -> list[logging.LogRecord]:
    """Every record of one event, in the order it was emitted."""
    return [record for record in caplog.records if getattr(record, "event", None) == name]


def only(caplog: pytest.LogCaptureFixture, name: str) -> logging.LogRecord:
    matching = events(caplog, name)
    assert len(matching) == 1, f"expected one {name} record, got {len(matching)}"
    return matching[0]


def fields_of(record: logging.LogRecord) -> dict[str, object]:
    """The structured half of a record: exactly the attributes the JSON
    formatter writes as top-level keys, read through `logs.py`'s own
    standard-attribute set rather than through a list written here, so a
    field these tests do not know about is a failure rather than a
    silence."""
    return {
        key: value for key, value in vars(record).items() if key not in _STANDARD_ATTRIBUTES
    }
