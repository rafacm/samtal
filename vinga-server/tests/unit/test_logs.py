"""The two log formats, and what the vendor libraries are allowed to say.

The JSON format is what the container ships with and what a collector
groups a deployment's own measurements by, so what a record carries is
asserted field by field. The text format is the one the server has
always printed, and the assertion here is that it did not change.

The last sections are about records this project does not write: the
provider SDKs log response headers and caught tracebacks under DEBUG,
which is a hole the providers' own sanitizing cannot reach, so those
tests drive the real SDK client through a mock transport and look at
what came out the other end (#137). The database library is the same
shape one level lower, and its payload sits at INFO rather than DEBUG
(#124); what serves a device is covered where a real server can be run,
in `tests/integration/test_access_logs.py`.
"""

import json
import logging
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import httpx
import pytest
from openai import AsyncOpenAI
from sqlalchemy import create_engine, text

from vinga_server import logs
from vinga_server.config.models import DatabaseConfig, ServerConfig

# Not a real credential, and shaped so a substring check for it cannot
# match by accident. It stands in for what a far end can put in a
# response header or a transport error.
SENTINEL = "sk-test-4f8b2c9e-never-a-real-credential"


def formatted(record: logging.LogRecord) -> dict:
    return json.loads(logs.JsonFormatter().format(record))


def make_record(**kwargs) -> logging.LogRecord:
    record = logging.LogRecord(
        name="vinga_server.session",
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
    assert payload["logger"] == "vinga_server.session"
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
        logger = logging.getLogger("vinga_server.session")
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
    assert line.endswith('INFO     vinga_server.session: session abc123: heard "hello there"')


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
    levels = {name: logging.getLogger(name).level for name in logs.VENDOR_LOG_FLOORS}
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
    for name in logs.VENDOR_LOG_FLOORS:
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
    """An operator running at ERROR asked for silence, and a floor is a
    level none of these is let below rather than one they are raised
    to."""
    logs.quiet_vendor_libraries(logging.ERROR)
    for name in logs.VENDOR_LOG_FLOORS:
        assert logging.getLogger(name).level == logging.ERROR


def test_configure_applies_the_floor(restore_root_logger, restore_vendor_levels) -> None:
    logs.configure(ServerConfig(log_level="DEBUG"))
    assert logging.getLogger().level == logging.DEBUG
    for name, floor in logs.VENDOR_LOG_FLOORS.items():
        assert logging.getLogger(name).level == floor


# --- what the database library is allowed to say (#124) --------------


def test_a_statement_and_its_parameters_do_not_reach_a_debug_log(
    caplog: pytest.LogCaptureFixture, restore_vendor_levels
) -> None:
    """SQLAlchemy's payload is not behind DEBUG the way the SDKs' is: an
    engine whose logger is enabled for INFO echoes every statement with
    the parameters bound to it, and those are the stored configuration.
    So its floor is WARNING, and the sentinel here is a bound value.

    The library pins its own logger at WARNING when it is imported, and
    that pin is cleared first on purpose: what is under test is this
    deployment's floor, and a test that let the library's own default
    answer the question would pass with the floor gone."""
    logging.getLogger("sqlalchemy").setLevel(logging.NOTSET)
    logs.quiet_vendor_libraries(logging.DEBUG)
    # An in-memory SQLite engine on purpose, and the one place this
    # project still builds one (#283). What is under test is the log
    # floor over SQLAlchemy's own echo, not this server's stores: the
    # neutral engine is the right tool because it needs no instance, no
    # migration and no schema, and using the product's would make a test
    # about redaction depend on a database being up.
    engine = create_engine("sqlite://")

    with caplog.at_level(logging.DEBUG), engine.connect() as connection:
        connection.execute(text("create table kept (secret text)"))
        connection.execute(text("insert into kept values (:value)"), {"value": SENTINEL})
        connection.execute(text("select * from kept where secret = :value"), {"value": SENTINEL})

    assert SENTINEL not in caplog.text
    assert all(SENTINEL not in str(record.__dict__) for record in caplog.records)
    assert not [r for r in caplog.records if r.name.startswith("sqlalchemy")]


def test_without_the_floor_the_engine_echoes_its_parameters(
    caplog: pytest.LogCaptureFixture, restore_vendor_levels
) -> None:
    """And the load-bearing half: an engine logger left at the server's
    level does put the statement and the value bound to it on the
    record, which is what the floor above is holding back."""
    logging.getLogger("sqlalchemy").setLevel(logging.NOTSET)
    # In memory, for the reason the test above gives.
    engine = create_engine("sqlite://")

    with caplog.at_level(logging.DEBUG), engine.connect() as connection:
        connection.execute(text("create table kept (secret text)"))
        connection.execute(text("insert into kept values (:value)"), {"value": SENTINEL})

    assert SENTINEL in caplog.text


# --- when the floor arrives, on both ways a deployment starts (#124) --


def _connection() -> dict[str, str]:
    """The lane's database, as the `VINGA_DB_*` variables a child reads.

    Every `VINGA_` variable is stripped from a child's environment, so
    the connection has to be put back explicitly; the settings are this
    process's own defaults, which the lane's conftest pointed at the
    database it provisioned.
    """
    settings = DatabaseConfig()
    return {
        "VINGA_DB_HOST": settings.host,
        "VINGA_DB_PORT": str(settings.port),
        "VINGA_DB_NAME": settings.name,
        "VINGA_DB_USER": settings.user,
        "VINGA_DB_PASSWORD": os.environ.get("VINGA_DB_PASSWORD", "vinga"),
    }


def _child(*source: str, cwd: Path, **environment: str) -> subprocess.CompletedProcess[str]:
    """A fresh interpreter running exactly this source.

    A subprocess because the subject is when a process applies the floor,
    and this one has had the floor applied since its own first test. `-B`
    for the reason `test_onboarding_import_weight.py` gives: this suite
    clears the bytecode caches once and writes none, and a child that
    wrote a full set back would hand the next run a stale one.

    Every VINGA_ variable is dropped and the named ones put back, and
    the child runs in a directory of the test's own, so neither the
    developer's environment nor a `.env` beside the checkout can decide
    what it reads.

    The parts are dedented one by one and then joined, so a block
    written at a function's indentation and one written at the module's
    can be handed in together.
    """
    env = {key: value for key, value in os.environ.items() if not key.startswith("VINGA_")}
    return subprocess.run(
        [sys.executable, "-B", "-c", "".join(textwrap.dedent(part) for part in source)],
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env | environment,
        # A guard, not a measurement: one of these children calls
        # `main()`, and a `main()` that got further than it is meant to
        # would sit on a socket forever rather than fail.
        timeout=CHILD_TIMEOUT_S,
    )


# How long a child may take before the test gives up on it. Generous:
# it starts an interpreter and migrates a database.
CHILD_TIMEOUT_S = 120.0

# The three the child reports on: the one uvicorn turns up, the one
# whose floor is not INFO, and one of the four that were already held.
FLOORED = ("uvicorn.error", "sqlalchemy", "httpx")

# A token shaped like the ones the environment carries, for the child
# that has to get all the way to a described application.
TOKEN = "test-secret-" + "0123456789abcdef" * 2

# What the child reports, so the assertions read in the names `logging`
# uses rather than in numbers. The level each logger carries of its own
# rather than the effective one, because that is the difference the
# floor makes: an unfloored process leaves them inheriting (`NOTSET`) or
# at whatever set them.
_REPORT = """
    import json, logging
    print(json.dumps({
        name: logging.getLevelName(logging.getLogger(name).level) for name in %r
    }))
"""


def test_an_external_asgi_runner_gets_the_floor_too(tmp_path: Path) -> None:
    """`uvicorn vinga_server.app:app` never reaches `configure`.

    That entry point builds the app through the module's `__getattr__`
    and serves it, so until the floor moved into `create_app` a
    deployment launched that way ran with none of it: `uvicorn
    --log-level debug` sets `uvicorn.error` to DEBUG before it imports
    the app, and nothing afterwards took it back down. The child does
    what uvicorn does to that logger, and to the engine's for good
    measure, and then touches the attribute uvicorn touches.
    """
    finished = _child(
        """
        import logging
        # What `uvicorn --log-level debug` has already done by the time
        # it imports the application.
        logging.getLogger("uvicorn.error").setLevel(logging.DEBUG)
        # And a process that has had the engine's own pin overridden,
        # so the floor is the only thing left holding it.
        logging.getLogger("sqlalchemy").setLevel(logging.DEBUG)

        import vinga_server.app as module

        module.app
        """,
        _REPORT % (FLOORED,),
        cwd=tmp_path,
        **_connection(),
        VINGA_API_SECRET=TOKEN,
        VINGA_AUTH_SECRET=TOKEN,
    )

    assert finished.returncode == 0, finished.stderr
    levels = json.loads(finished.stdout.splitlines()[-1])
    # uvicorn's DEBUG comes back down to the floor, the engine's override
    # is overridden in turn, and httpx is pinned where it already
    # effectively was: without a level to hold them to, the floor makes
    # nothing louder than the process already is.
    assert levels == {"uvicorn.error": "INFO", "sqlalchemy": "WARNING", "httpx": "WARNING"}


def test_the_boot_that_reads_the_configuration_is_inside_the_floor(tmp_path: Path) -> None:
    """The other path's window: `configure` cannot run until the
    configuration has been read, and reading it opens a database.

    So the floor goes on at the top of `main()` instead, and this is what
    says it got there in time. The child turns SQL echoing on before
    calling `main()`, which is what an operator diagnosing a database
    problem does, and the boot then opens the configuration database and
    migrates it. Asserted on the output rather than on a level, because
    what matters is that no statement and no bound parameter was printed
    during the window, not which call closed it.

    The boot is made to stop right after that window by leaving the
    device authentication secret out of the environment, which
    `create_app` refuses in one sentence: the database work happens, the
    server never starts.
    """
    finished = _child(
        """
        import logging, sys

        logging.basicConfig(
            level=logging.DEBUG, format="%(name)s|%(message)s", stream=sys.stdout
        )
        logging.getLogger("sqlalchemy").setLevel(logging.DEBUG)

        sys.argv = ["vinga-server"]
        from vinga_server.main import main

        main()
        """,
        cwd=tmp_path,
        **_connection(),
    )

    # The boot got as far as the refusal it was pointed at, which is
    # after the database work: an empty log would otherwise prove
    # nothing.
    assert finished.returncode == 1
    assert "VINGA_AUTH_SECRET is not set" in finished.stderr
    assert any(
        line.startswith("alembic.runtime.migration|") for line in finished.stdout.splitlines()
    ), finished.stdout
    # And the engine said nothing while it did it.
    assert not [
        line for line in finished.stdout.splitlines() if line.startswith("sqlalchemy")
    ], finished.stdout
