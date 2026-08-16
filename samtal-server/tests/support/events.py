"""Reading the structured log a suite has just provoked.

A conversation says what it did through `extra=` fields on log records,
never in the message text, so a test that is about an event reads
`caplog.records` and looks for the `event` field. What belongs here is
that reading: selecting the records of one event, and insisting a run
produced exactly one of them. Nothing here provokes an event, so this
module knows nothing of sessions, sockets or providers, and imports
only pytest.
"""

import pytest


def events(caplog: pytest.LogCaptureFixture, name: str) -> list:
    return [record for record in caplog.records if getattr(record, "event", None) == name]


def only(caplog: pytest.LogCaptureFixture, name: str):
    matching = events(caplog, name)
    assert len(matching) == 1, f"expected one {name} record, got {len(matching)}"
    return matching[0]
