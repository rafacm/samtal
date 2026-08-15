"""The two log formats, and what the vendor libraries are allowed to say.

The JSON format is what the container ships with and what makes retained
logs readable back as conversation transcripts, so what a record carries
is asserted field by field. The text format is the one the server has
always printed, and the assertion here is that it did not change.

The last section is about records this project does not write: the
provider SDKs log response headers and caught tracebacks under DEBUG,
which is a hole the providers' own sanitizing cannot reach, so those
tests drive the real SDK client through a mock transport and look at
what came out the other end (#137).
"""

import json
import logging
import sys

import httpx
import pytest
from openai import AsyncOpenAI

from samtal_server import logs
from samtal_server.config.models import ServerConfig

# Not a real credential, and shaped so a substring check for it cannot
# match by accident. It stands in for what a far end can put in a
# response header or a transport error.
SENTINEL = "sk-test-4f8b2c9e-never-a-real-credential"


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


# --- what the vendor libraries are allowed to say (#137) -------------


@pytest.fixture
def restore_vendor_levels():
    """The floor is set on loggers this process shares with every other
    test, so put their levels back."""
    levels = {name: logging.getLogger(name).level for name in logs.VENDOR_LOGGERS}
    yield
    for name, level in levels.items():
        logging.getLogger(name).setLevel(level)


async def speak_to(handler: object) -> None:
    """One request through the real SDK client and a mock transport, so
    the records under test are the ones a deployment would get."""
    client = AsyncOpenAI(
        api_key="test-key",
        max_retries=0,
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
        ),
    )
    with pytest.raises(Exception):  # noqa: B017 - the failure is the point, not its type
        async with client.audio.speech.with_streaming_response.create(
            model="gpt-4o-mini-tts", voice="alloy", input="Hej", response_format="pcm"
        ):
            pass


def echoing_header(request: httpx.Request) -> httpx.Response:
    """A far end that puts something it was sent into a response header,
    which is one of the two shapes the SDK logs verbatim."""
    return httpx.Response(
        401, headers={"x-echo": SENTINEL}, json={"error": {"message": "no"}}
    )


def failing_transport(request: httpx.Request) -> httpx.Response:
    """And the other: a transport error the SDK catches and logs with
    its traceback, carrying a cause of its own."""
    error = httpx.ReadError(f"the connection carried {SENTINEL}")
    error.__cause__ = ValueError(f"underneath: {SENTINEL}")
    raise error


async def test_without_the_floor_the_sdk_debug_records_do_arrive(
    caplog: pytest.LogCaptureFixture, restore_vendor_levels
) -> None:
    """The guard below is load-bearing, and this is what says so: with
    the vendor loggers left alone, DEBUG puts the SDK's own records into
    the handler this project installs."""
    for name in logs.VENDOR_LOGGERS:
        logging.getLogger(name).setLevel(logging.NOTSET)

    with caplog.at_level(logging.DEBUG):
        await speak_to(echoing_header)

    assert [r for r in caplog.records if r.name.startswith("openai")]


async def test_a_response_header_the_sdk_logs_is_held_back(
    caplog: pytest.LogCaptureFixture, restore_vendor_levels
) -> None:
    logs.quiet_vendor_libraries(logging.DEBUG)

    with caplog.at_level(logging.DEBUG):
        await speak_to(echoing_header)

    assert SENTINEL not in caplog.text
    assert all(SENTINEL not in str(record.__dict__) for record in caplog.records)
    assert not [r for r in caplog.records if r.name.startswith("openai")]


async def test_a_transport_error_and_its_cause_are_held_back(
    caplog: pytest.LogCaptureFixture, restore_vendor_levels
) -> None:
    """The SDK logs what it caught with exc_info, so the traceback of the
    transport error and of the cause behind it would both be rendered
    into the record."""
    logs.quiet_vendor_libraries(logging.DEBUG)

    with caplog.at_level(logging.DEBUG):
        await speak_to(failing_transport)

    assert SENTINEL not in caplog.text
    assert all(SENTINEL not in str(record.__dict__) for record in caplog.records)


async def test_the_one_request_line_httpx_writes_survives(
    caplog: pytest.LogCaptureFixture, restore_vendor_levels
) -> None:
    """The floor is INFO rather than WARNING for this: the method, the
    URL and the status are the useful half, and they carry no header and
    no body."""
    logs.quiet_vendor_libraries(logging.DEBUG)

    with caplog.at_level(logging.INFO):
        await speak_to(echoing_header)

    assert [r for r in caplog.records if r.name == "httpx" and "401" in r.getMessage()]


def test_the_floor_never_makes_a_quiet_server_louder(restore_vendor_levels) -> None:
    """An operator running at WARNING asked for silence, and INFO is a
    floor on the vendors rather than a level they are raised to."""
    logs.quiet_vendor_libraries(logging.WARNING)
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("openai").level == logging.WARNING


def test_configure_applies_the_floor(restore_root_logger, restore_vendor_levels) -> None:
    logs.configure(ServerConfig(log_level="DEBUG"))
    assert logging.getLogger().level == logging.DEBUG
    for name in logs.VENDOR_LOGGERS:
        assert logging.getLogger(name).level == logging.INFO
