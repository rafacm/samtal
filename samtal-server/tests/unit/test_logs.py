"""The two log formats.

The JSON format is what the container ships with and what makes retained
logs readable back as conversation transcripts, so what a record carries
is asserted field by field. The text format is the one the server has
always printed, and the assertion here is that it did not change.
"""

import json
import logging
import sys

import pytest

from samtal_server import logs
from samtal_server.config.models import ServerConfig


def formatted(record: logging.LogRecord) -> dict:
    return json.loads(logs.JsonFormatter().format(record))


def make_record(**kwargs) -> logging.LogRecord:
    record = logging.LogRecord(
        name="samtal_server.session",
        level=logging.INFO,
        pathname="session.py",
        lineno=1,
        msg='session %s: heard "%s"',
        args=("abc123", "hello there"),
        exc_info=None,
    )
    for key, value in kwargs.items():
        setattr(record, key, value)
    return record


def test_json_record_carries_the_standard_fields() -> None:
    payload = formatted(make_record())
    assert payload["level"] == "INFO"
    assert payload["logger"] == "samtal_server.session"
    # The message is the interpolated human sentence, the same text the
    # terminal format prints.
    assert payload["message"] == 'session abc123: heard "hello there"'
    # ISO 8601 in UTC, so records from two hosts sort together.
    assert payload["ts"].endswith("+00:00")


def test_extras_become_top_level_fields() -> None:
    payload = formatted(make_record(event="heard", session="abc123", duration_s=1.5))
    assert payload["event"] == "heard"
    assert payload["session"] == "abc123"
    assert payload["duration_s"] == 1.5


def test_no_standard_logrecord_attributes_leak_into_the_output() -> None:
    payload = formatted(make_record(event="heard"))
    assert set(payload) == {"ts", "level", "logger", "message", "event"}


def test_exceptions_are_formatted_into_the_record() -> None:
    try:
        raise RuntimeError("the provider went away")
    except RuntimeError:
        logger = logging.getLogger("samtal_server.session")
        record = logger.makeRecord(
            logger.name, logging.ERROR, "session.py", 1, "reply failed", None, sys.exc_info()
        )
    payload = formatted(record)
    assert "RuntimeError: the provider went away" in payload["exc_info"]
    assert "Traceback" in payload["exc_info"]


def test_unserializable_extras_degrade_rather_than_lose_the_record() -> None:
    payload = formatted(make_record(agent=object()))
    assert payload["message"].startswith("session abc123:")
    assert "object object at" in payload["agent"]


def test_text_format_is_the_one_the_server_has_always_printed() -> None:
    formatter = logging.Formatter(logs.TEXT_FORMAT)
    line = formatter.format(make_record(event="heard", session="abc123"))
    # Extras stay out of the human line; the message is the whole point.
    assert line.endswith('INFO     samtal_server.session: session abc123: heard "hello there"')


@pytest.fixture
def restore_root_logger():
    """configure() takes the root logger over, so give it back."""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for handler in list(root.handlers):
        root.removeHandler(handler)
    for handler in handlers:
        root.addHandler(handler)
    root.setLevel(level)


def test_configure_installs_exactly_one_handler(restore_root_logger) -> None:
    logs.configure(ServerConfig(log_format="json", log_level="DEBUG"))
    logs.configure(ServerConfig(log_format="json", log_level="DEBUG"))
    root = logging.getLogger()
    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0].formatter, logs.JsonFormatter)
    assert root.level == logging.DEBUG


def test_configure_defaults_to_the_text_format(restore_root_logger) -> None:
    logs.configure(ServerConfig())
    formatter = logging.getLogger().handlers[0].formatter
    assert not isinstance(formatter, logs.JsonFormatter)
    assert logging.getLogger().level == logging.INFO
