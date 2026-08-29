"""`vinga events tail`: what it prints, when it stops, and what it must
never carry out of a stream.

The command is the one row of the grammar whose answer does not finish
arriving, and that is what every case here is about in the end. Three
things follow from it and none of them is shared with any other command.

**It stops on a contract rather than on an answer.** Without `--follow`
it waits for the first event the filters admit, prints it and exits 0.
With `--follow` it prints until something stops it, and there are
exactly two somethings: an interrupt, which is a reader who was told to
stop and exits 0, and the stream ending, which exits 1 and says so. A
tail that ended quietly would be a terminal that looks like a quiet
deployment, which is the failure this whole surface exists to prevent.

**One event is one physical line, by encoding.** An event's values are
identifiers, counts and reason tokens, and the identifier vocabulary
admits bytes a terminal reads as instructions. So the hostile values
below are not adversarial decoration: they are what the declared value
types permit, and the claim under test is that the line count does not
depend on them.

**The stream rides the request boundary, for its whole length.** A
stream can fail after the response has opened, which is the one place a
bare client would preserve nothing of `_sent`'s guarantees. The
planted-credential cases below therefore come in two: one that fails
before anything opens and one that fails with the stream already
running, and each hunts the credential on every surface a deployment
keeps.

The transport is a streaming one rather than the buffered test client,
through the seam `tests/support/config_cli.answering` adds: `TestClient`
reads a whole body before handing it back, and this body has no end, so
there is nothing for it to hand back at all.
"""

import io
import json
import sys
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path

import httpx
import pytest

from tests.support.config_cli import TOKEN, answering, chain, logged, runner
from tests.support.events import both_formats
from vinga_server.broken_pipe import BROKEN_PIPE_STATUS
from vinga_server.config import cli
from vinga_server.config.cli import MAX_FRAME_DEPTH
from vinga_server.config.loader import ConfigError
from vinga_server.config.responses import EVENT_STREAM_MEDIA_TYPE

# A value shaped like a credential in a query string, which is the form
# the transport policy accepts and `Address.shown` takes out: userinfo
# is refused outright, and `?token=...` is what vendors accept instead.
QUERY_TOKEN = "tok-test-9d3e1a75-never-a-real-credential"

# What a stream that is not this API's own carries, so a tail that
# quoted any of it back would be caught.
ANSWERED = "ans-test-6b2f4c08-never-a-real-value"

KEEPALIVE = b": keepalive\n\n"

# One board and one session, in the canonical forms the events carry.
MAC = "aa:bb:cc:dd:ee:ff"

SESSION = "6f1a2b3c4d5e6f708192a3b4c5d6e7f8"

STAMP = "2026-08-29T10:11:12.345678+00:00"


def frame(**fields: object) -> bytes:
    """One event as the stream writes it: the catalogued fields, then
    the two the stream owns, in that order, because the order the
    payload declares is the order a line prints."""
    return f"data: {json.dumps(fields, separators=(',', ':'))}\n\n".encode()


def event(**fields: object) -> bytes:
    """One ordinary INFO event, with the stream's own two on it."""
    return frame(**fields, level="INFO", ts=STAMP)


def dropped(count: int) -> bytes:
    """The stream's own named event for a reader that fell behind."""
    return f'event: dropped\ndata: {{"dropped":{count}}}\n\n'.encode()


def body(data: str) -> bytes:
    """One unnamed frame carrying exactly this, whatever it is."""
    return f"data: {data}\n\n".encode()


def named(name: str, **fields: object) -> bytes:
    """One frame under a name of the test's choosing, which is how the
    `dropped` envelope is broken in each of the ways it can be."""
    return f"event: {name}\n".encode() + body(json.dumps(fields, separators=(",", ":")))


class Body(httpx.SyncByteStream):
    """A response body that arrives in pieces, and may stop arriving.

    An exception among the chunks is raised where it sits, which is how
    a connection that dies with the stream open is written; the chunks
    before it have already been delivered, exactly as they would have
    been on the wire.
    """

    def __init__(self, chunks: Sequence[bytes | BaseException]) -> None:
        self._chunks = chunks

    def __iter__(self) -> Iterator[bytes]:
        for chunk in self._chunks:
            if isinstance(chunk, BaseException):
                raise chunk
            yield chunk


def serving(*chunks: bytes | BaseException, media: str = EVENT_STREAM_MEDIA_TYPE):
    """A handler that answers one open stream of these chunks.

    It keeps every request it was given, so a test can read what the
    command asked for as well as what it did with the answer. `media` is
    a parameter because a 200 under some other type is a thing that
    really answers, and what the command does with one is the point of
    one of the cases below.
    """
    asked: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        return httpx.Response(200, headers={"content-type": media}, stream=Body(chunks))

    handler.asked = asked
    return handler


def refusing(code: int, **body: object):
    """A handler that answers a refusal instead of a stream, in the
    media type this API refuses in."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            code,
            headers={"content-type": "application/problem+json"},
            content=json.dumps(body).encode(),
        )

    return handler


def failing(problem: BaseException):
    """A handler that never answers at all, which is a connection that
    does not open."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise problem

    return handler


@pytest.fixture
def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """One command run the way the entry point runs it."""
    return runner(monkeypatch)


def carried(exc: ConfigError) -> str:
    """Everything a refusal for this command line carries, chain
    included.

    `cli._parsed` is reached for the reason the live lane reaches it:
    `main` catches this exception by design and answers with a sentence
    and an exit code, so no caller-facing surface holds the exception
    itself, and a claim about what it carries cannot otherwise be
    stated.
    """
    return chain(exc)


def refused(argv: Sequence[str]) -> ConfigError:
    """The refusal one command line raises, before `main` turns it into
    a sentence. See `carried` on why the boundary is reached through."""
    with pytest.raises(ConfigError) as caught:
        cli._parsed(list(argv), cli.DISPATCHED)
    return caught.value


# What a line is


def test_one_event_prints_as_one_line(run, capsys: pytest.CaptureFixture[str]) -> None:
    """The clock time, the name, and the event's own fields in the order
    it declares them. No level, because this one is INFO, which is what
    a tail is mostly made of."""
    answering(run, serving(event(event="ota_check", device=MAC, firmware="2.4.0")))
    capsys.readouterr()

    assert run("events", "tail") == 0

    printed = capsys.readouterr()
    assert printed.out == '10:11:12 ota_check device="aa:bb:cc:dd:ee:ff" firmware="2.4.0"\n'


@pytest.mark.parametrize("level", ["DEBUG", "WARNING", "ERROR"])
def test_every_level_but_the_default_is_named(
    run, capsys: pytest.CaptureFixture[str], level: str
) -> None:
    """DEBUG included, which is the half worth stating: an event
    admitted below what the retained log carries has to say that it is
    one, or a reader cannot tell it from the rest."""
    answering(run, serving(frame(event="heard", level=level, ts=STAMP)))
    capsys.readouterr()

    assert run("events", "tail", "--level", "debug") == 0

    assert capsys.readouterr().out == f"10:11:12 {level} heard\n"


def test_a_count_prints_as_a_number_and_a_flag_as_a_word(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """What a reader scans a tail for is the numbers, so they are not
    quoted. A boolean is not one: `true` is what the record says."""
    answering(run, serving(event(event="replied", turns=3, seconds=1.5, kept=True)))
    capsys.readouterr()

    assert run("events", "tail") == 0

    assert capsys.readouterr().out == "10:11:12 replied turns=3 seconds=1.5 kept=true\n"


# The one-line guarantee, over values the declared vocabulary permits


HOSTILE: dict[str, object] = {
    "newline": "one\ntwo",
    "carriage": "one\r\ntwo",
    "escape": "\x1b[2J\x1b[H",
    "quote": 'he said "no" and \\left\\',
    "listed": ["a\nb", 2],
    "nested": {"inner": {"deeper": "x\ny"}},
}


@pytest.mark.parametrize(("key", "value"), sorted(HOSTILE.items()))
def test_a_hostile_value_is_still_exactly_one_line(
    run, capsys: pytest.CaptureFixture[str], key: str, value: object
) -> None:
    """The claim is the line count, and it is asserted as a line count:
    one newline, at the end, whatever the value held. The escape case
    carries the second half of the output-determinism practice with it,
    which is that nothing an answer carries may steer the terminal it is
    printed into."""
    answering(run, serving(event(event="heard", **{key: value})))
    capsys.readouterr()

    assert run("events", "tail") == 0

    printed = capsys.readouterr().out
    assert printed.count("\n") == 1
    assert printed.endswith("\n")
    assert "\x1b" not in printed
    assert "\r" not in printed
    # And the value is still there, escaped rather than dropped: a line
    # that stayed one line by losing what it was about would pass the
    # assertion above and be worthless.
    assert json.dumps(value, separators=(",", ":")) in printed


def test_a_field_name_that_is_not_a_declared_word_is_encoded_too(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The keys come off a stream as much as the values do, and a
    renderer that trusted one half would be a renderer with a hole in
    it."""
    answering(run, serving(event(event="heard", **{"a b\nc": 1})))
    capsys.readouterr()

    assert run("events", "tail") == 0

    printed = capsys.readouterr().out
    assert printed.count("\n") == 1
    assert '"a b\\nc"=1' in printed


def test_a_stamp_that_is_not_a_stamp_does_not_break_the_line(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The two fields the stream owns arrive over the same wire as the
    rest, so the one that is parsed rather than encoded is the one place
    a line could come apart, and it does not."""
    answering(run, serving(frame(event="heard", level="INFO", ts="not\na stamp")))
    capsys.readouterr()

    assert run("events", "tail") == 0

    printed = capsys.readouterr().out
    assert printed.count("\n") == 1
    assert printed.startswith('"not\\na stamp" heard')


# Where each half of the output goes


def test_a_dropped_count_is_a_notice_and_not_an_event(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """`tail | grep` reads the events and the person watching still
    learns that some went past, which is what the two streams are for.
    The notice does not end the wait either: a reader that fell behind
    has not yet been shown an event."""
    answering(run, serving(dropped(12), event(event="session_open", session=SESSION)))
    capsys.readouterr()

    assert run("events", "tail") == 0

    printed = capsys.readouterr()
    assert printed.out == f'10:11:12 session_open session="{SESSION}"\n'
    assert "12 events are missing above this line" in printed.err
    assert "fell behind" in printed.err


# When it stops


def test_without_follow_it_waits_for_one_event_and_exits(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The scriptable "wait for the next X", which is the only reading a
    tail with nothing buffered behind it can offer. The stream still has
    two events on it when this returns, and neither is printed."""
    answering(
        run,
        serving(
            KEEPALIVE,
            event(event="ota_check", device=MAC),
            event(event="session_open", session=SESSION),
            event(event="session_closed", session=SESSION),
        ),
    )
    capsys.readouterr()

    assert run("events", "tail") == 0

    printed = capsys.readouterr()
    assert printed.out == f'10:11:12 ota_check device="{MAC}"\n'
    assert printed.err == ""


def test_with_follow_it_prints_until_the_stream_ends(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """And the end is exit 1 with the sentence, not a quiet success: a
    tail that ended on a restart and said nothing would look exactly
    like a deployment with nothing to say."""
    answering(
        run,
        serving(
            event(event="ota_check", device=MAC),
            KEEPALIVE,
            event(event="session_open", session=SESSION),
        ),
    )
    capsys.readouterr()

    assert run("events", "tail", "--follow") == 1

    printed = capsys.readouterr()
    assert printed.out.splitlines() == [
        f'10:11:12 ota_check device="{MAC}"',
        f'10:11:12 session_open session="{SESSION}"',
    ]
    assert printed.err.strip() == cli.STREAM_ENDED


@pytest.mark.parametrize("argv", [("events", "tail"), ("events", "tail", "--follow")])
def test_an_end_before_any_event_is_the_same_failure_in_both_modes(
    run, capsys: pytest.CaptureFixture[str], argv: tuple[str, ...]
) -> None:
    """A stream that carried only keepalives and then stopped told the
    reader nothing, and saying nothing about that is the one answer it
    must not give."""
    answering(run, serving(KEEPALIVE, KEEPALIVE))
    capsys.readouterr()

    assert run(*argv) == 1

    printed = capsys.readouterr()
    assert printed.out == ""
    assert printed.err.strip() == cli.STREAM_ENDED


def test_a_connection_that_dies_mid_stream_ends_it_the_same_way(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """From this side a body that ended and a connection that died are
    one thing, the tail going quiet, and a client that told them apart
    would be reporting a distinction it cannot make. Nothing reconnects,
    and the sentence says so."""
    answering(
        run,
        serving(
            event(event="ota_check", device=MAC),
            httpx.ReadError("the peer went away"),
        ),
    )
    capsys.readouterr()

    assert run("events", "tail", "--follow") == 1

    printed = capsys.readouterr()
    assert printed.out == f'10:11:12 ota_check device="{MAC}"\n'
    assert printed.err.strip() == cli.STREAM_ENDED
    assert "run the command again" in printed.err


def test_an_interrupted_follow_did_its_job(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """Ctrl-C is how an interactive tail is meant to end, so it is exit
    0 and no sentence: a reader who was told to stop is not a
    failure."""
    answering(
        run,
        serving(event(event="ota_check", device=MAC), KeyboardInterrupt()),
    )
    capsys.readouterr()

    assert run("events", "tail", "--follow") == 0

    printed = capsys.readouterr()
    assert printed.out == f'10:11:12 ota_check device="{MAC}"\n'
    assert printed.err == ""


class _ClosedPipe(io.StringIO):
    """A stdout nobody is reading any more."""

    def write(self, text: str) -> int:
        raise BrokenPipeError(32, "Broken pipe")


def test_a_reader_who_stops_reading_gets_the_shell_s_own_status(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`vinga events tail | head -n 1` is how a script waits for one
    event, and `head` closes the pipe while this is still writing. That
    is a reader who has read enough rather than a failure, so it answers
    the status a shell already understands and prints nothing about it.

    The other half of the pattern, redirecting the descriptor so the
    interpreter's own final flush cannot raise again where nothing can
    catch it, is pinned in a real process by
    `test_event_docs.test_a_reader_who_stops_reading_gets_no_traceback`;
    what this pins is that the answer is reached at all.
    """
    answering(run, serving(event(event="ota_check", device=MAC)))
    capsys.readouterr()
    monkeypatch.setattr(sys, "stdout", _ClosedPipe())

    assert run("events", "tail", "--follow") == BROKEN_PIPE_STATUS

    assert capsys.readouterr().err == ""


# What it asks for


def test_the_three_filters_ride_the_query_in_the_api_s_own_words(run) -> None:
    """The words an operator types and the words the API parses are one
    vocabulary, so what each may be is said once, where it is read."""
    handler = serving(event(event="heard", session=SESSION))
    answering(run, handler)

    assert run(
        "events",
        "tail",
        "--device",
        "AA-BB-CC-DD-EE-FF",
        "--session",
        SESSION,
        "--level",
        "warning",
    ) == 0

    [asked] = handler.asked
    assert dict(asked.url.params) == {
        "device": "AA-BB-CC-DD-EE-FF",
        "session": SESSION,
        "level": "warning",
    }
    assert asked.url.path.endswith("/runtime/events")


def test_an_absent_filter_is_an_argument_the_request_does_not_carry(run) -> None:
    """The API's own defaults are the defaults, said once: a client that
    sent `level=INFO` because nobody said otherwise would be a second
    copy of that decision."""
    handler = serving(event(event="heard"))
    answering(run, handler)

    assert run("events", "tail") == 0

    [asked] = handler.asked
    assert dict(asked.url.params) == {}


def test_the_stream_waits_for_the_server_and_not_for_a_clock(run) -> None:
    """The read is deliberately unbounded and the connect timeout is
    kept, which is the cli-guide's bound-every-wait practice applied to
    a read that has no bound to derive: the answer never finishes
    arriving, so any finite number would end a healthy tail and report
    it as the server going away."""
    answering(run, serving(event(event="heard")))

    assert run("events", "tail") == 0

    [client] = run.clients
    assert client.timeout.read is None
    assert client.timeout.connect == cli.CONNECT_TIMEOUT_S


def test_the_bearer_token_reaches_the_stream(run) -> None:
    """The stream is behind the same gate every other read is, and the
    client the command builds is the one that carries it."""
    handler = serving(event(event="heard"))
    answering(run, handler)

    assert run("events", "tail") == 0

    [asked] = handler.asked
    assert asked.headers["authorization"] == f"Bearer {TOKEN}"


# What it refuses


def test_a_refusal_before_the_stream_opens_says_what_the_api_said(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """A refusal has a body and an end, so it is read whole and answered
    in this grammar's own vocabulary: a 401 here says what a 401 says
    anywhere else."""
    answering(
        run,
        refusing(
            401,
            status=401,
            title="Unauthorized",
            detail="the bearer token is wrong",
            errors=[],
        ),
    )
    capsys.readouterr()

    assert run("events", "tail") == 1

    printed = capsys.readouterr()
    assert printed.out == ""
    assert printed.err.strip() == "the bearer token is wrong"


def test_a_frame_this_client_cannot_read_is_never_quoted_back(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """What reaches a stream from a middlebox is not this API's
    sanitized output, and a tail that quietly skipped it would go on
    looking live while showing less than arrived."""
    answering(run, serving(body(ANSWERED)))
    capsys.readouterr()

    assert run("events", "tail") == 1

    printed = capsys.readouterr()
    assert printed.out == ""
    assert cli.UNRECOGNIZED_ANSWER in printed.err
    assert ANSWERED not in printed.err


# A body that is not this stream
#
# Two things stand between a stranger's 200 and an operator's terminal,
# and each is checked on its own here: the media type, before a line is
# read, and the frame envelope, before a field is printed. The value
# planted in every one of these bodies is what says the check is worth
# something, since the whole failure being prevented is a body's own
# values reaching stdout.


def test_a_2xx_that_is_not_the_event_stream_is_not_read_at_all(
    run, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    """A proxy, a captive portal and a gateway all answer 200 with a
    body of their own, and one of those bodies parses as a JSON object
    as readily as an event does. The media type is what tells them
    apart, and it is checked before a line is read rather than after a
    field is printed."""
    answering(
        run,
        serving(
            frame(event="heard", level="INFO", ts=STAMP, leak=ANSWERED),
            media="application/json",
        ),
    )
    capsys.readouterr()

    with caplog.at_level(0):
        assert run("events", "tail") == 1

    printed = capsys.readouterr()
    assert printed.out == ""
    assert printed.err.strip() == cli.NOT_THE_EVENT_STREAM
    for surface in (printed.out, printed.err, logged(caplog), both_formats(caplog)):
        assert ANSWERED not in surface


# Every shape a frame can be that this stream never sends. The named
# ones are the `dropped` envelope broken in each of the ways it can be,
# plus a name the contract does not have at all; the unnamed ones are an
# event missing or mis-shaping one of the three keys every event
# carries.
NOT_A_FRAME = [
    pytest.param(named("dropped", dropped=3, leak=ANSWERED), id="dropped-with-more"),
    pytest.param(named("dropped", dropped=ANSWERED), id="dropped-not-a-count"),
    pytest.param(named("dropped", dropped=-1), id="dropped-negative"),
    pytest.param(named("dropped", dropped=True), id="dropped-a-flag"),
    pytest.param(named("surprise", leak=ANSWERED), id="a-name-the-contract-has-not"),
    pytest.param(frame(event="heard", ts=STAMP, leak=ANSWERED), id="no-level"),
    pytest.param(
        frame(event="heard", level="SHOUTING", ts=STAMP, leak=ANSWERED),
        id="a-level-that-is-not-one",
    ),
    pytest.param(frame(event="heard", level="INFO", leak=ANSWERED), id="no-stamp"),
    pytest.param(
        frame(event="heard", level="INFO", ts=1, leak=ANSWERED),
        id="a-stamp-that-is-a-number",
    ),
    pytest.param(frame(level="INFO", ts=STAMP, leak=ANSWERED), id="no-event"),
    pytest.param(
        frame(event="ota\ncheck", level="INFO", ts=STAMP, leak=ANSWERED),
        id="an-event-name-that-is-not-a-word",
    ),
    pytest.param(body(json.dumps([ANSWERED])), id="not-an-object"),
]


@pytest.mark.parametrize("body", NOT_A_FRAME)
def test_a_frame_that_is_not_this_stream_s_is_never_printed(
    run, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture, body: bytes
) -> None:
    """The envelope is what this half can check, and it checks it before
    anything is rendered: an event's own field names are the
    catalogue's, which the client tier may not import, but the three
    keys every streamed event carries are published and so is the small
    object a `dropped` frame is."""
    answering(run, serving(body))
    capsys.readouterr()

    with caplog.at_level(0):
        assert run("events", "tail") == 1

    printed = capsys.readouterr()
    assert printed.out == ""
    assert cli.UNRECOGNIZED_ANSWER in printed.err
    for surface in (printed.out, printed.err, logged(caplog), both_formats(caplog)):
        assert ANSWERED not in surface


def nested(depth: int) -> bytes:
    """One frame whose envelope is right and whose value is nested past
    anything an event carries.

    Built as text rather than through `json.dumps`, because at the
    deeper of the two depths below the encoder is one of the things
    under test on the other side, and it would run out of stack building
    the input.
    """
    head = json.dumps({"event": "heard", "level": "INFO", "ts": STAMP})[:-1]
    deep = "[" * depth + json.dumps(ANSWERED) + "]" * depth
    return body(f'{head},"deep":{deep}}}')


@pytest.mark.parametrize("depth", [MAX_FRAME_DEPTH + 1, 20_000])
def test_a_frame_nested_past_an_event_ends_in_a_sentence(
    run, capsys: pytest.CaptureFixture[str], depth: int
) -> None:
    """Nesting is the one thing about a frame that can cost more than
    the frame. `json.loads` exhausts the stack on a document a few
    thousand deep and raises `RecursionError`, which is not a
    `ValueError` and would have left this command as a traceback with
    the far side choosing when; encoding a structure walks it as surely
    as decoding one built it.

    Both depths answer the same sentence and they reach it by different
    roads, which is why both are here: the shallow one parses and is
    refused by the bound, and the deep one never parses at all and is
    refused by the arm that catches what the decoder does instead of
    returning.
    """
    answering(run, serving(nested(depth)))
    capsys.readouterr()

    assert run("events", "tail") == 1

    printed = capsys.readouterr()
    assert printed.out == ""
    assert printed.err.strip() == cli.UNREADABLE_EVENT
    assert "Traceback" not in printed.err
    assert ANSWERED not in printed.err


def test_a_frame_nested_past_an_event_leaves_nothing_on_the_chain(run) -> None:
    """And the exception it leaves carries neither the document nor the
    library's own account of running out of stack."""
    answering(run, serving(nested(20_000)))

    caught = refused(["events", "tail"])

    assert caught.__cause__ is None
    assert caught.__context__ is None
    assert ANSWERED not in carried(caught)


def test_an_unreadable_frame_leaves_nothing_on_the_chain() -> None:
    """A refusal built inside the handler would carry the document it
    could not decode as its `__context__`, for anything walking the
    chain to find."""
    caught = refused(
        [
            "--api-url",
            "http://127.0.0.1:9101/api",
            "events",
            "tail",
        ]
    )

    assert caught.__cause__ is None
    assert caught.__context__ is None


# The credential, on every surface a deployment keeps
#
# Two cases and not one, because a stream has two moments a bare client
# would have leaked at: the request that opens it, and the request that
# is still open. The address carries a query-string credential, which is
# the form the transport policy accepts and `Address.shown` masks.


def planted(run, handler) -> str:
    """The API address with a credential in its query, and this run's
    transport answering on it."""
    answering(run, handler)
    return f"http://127.0.0.1:9101/api?token={QUERY_TOKEN}"


# The two moments, and the sentence each of them answers with: an
# address that was never reached is named, masked, because which
# address it was is the whole of what a reader needs; a stream that
# died with the connection open is the stream ending, and names none.
FAILURES = [
    pytest.param(
        failing(httpx.ConnectError("connection refused")),
        True,
        id="connect-time",
    ),
    pytest.param(
        serving(event(event="ota_check", device=MAC), httpx.ReadError("gone")),
        False,
        id="mid-stream",
    ),
]


@pytest.mark.parametrize(("handler", "names_the_address"), FAILURES)
def test_a_stream_that_fails_carries_no_credential_out_of_it(
    run,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    handler: Callable[[httpx.Request], httpx.Response],
    names_the_address: bool,
) -> None:
    """The whole reason this command goes through a sibling of `_sent`
    rather than through a client of its own. The request loggers are
    quiet for the length of the stream and not only for its opening, and
    every sentence names the masked address or nothing at all."""
    address = planted(run, handler)
    capsys.readouterr()

    with caplog.at_level(0):
        assert run("--api-url", address, "events", "tail", "--follow") == 1

    printed = capsys.readouterr()
    for surface in (printed.out, printed.err, logged(caplog), both_formats(caplog)):
        assert QUERY_TOKEN not in surface
    assert ("127.0.0.1:9101" in printed.err) is names_the_address


@pytest.mark.parametrize(("handler", "names_the_address"), FAILURES)
def test_a_failed_stream_leaves_no_url_on_the_exception_chain(
    run, handler: Callable[[httpx.Request], httpx.Response], names_the_address: bool
) -> None:
    """httpx's exceptions carry the request they were made for, and the
    request carries the whole URL. Nothing raised here may have one
    behind it: a chain walker is what a crash reporter is."""
    address = planted(run, handler)

    caught = refused(["--api-url", address, "events", "tail", "--follow"])

    assert QUERY_TOKEN not in carried(caught)
    assert caught.__cause__ is None
    assert caught.__context__ is None


if __name__ == "__main__":  # pragma: no cover - a hand run of one suite
    sys.exit(pytest.main([__file__, "-v"]))
