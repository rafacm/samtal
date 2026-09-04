"""The progress line the two long waits draw, and everything it may not
do.

`import` waits for a transaction nothing bounds and `apply` waits up to
a minute, so both narrate the wait on stderr while it runs. That is the
interactive affordance the determinism practice licenses
(`docs/architecture/cli-guide.md`), and a licence is a set of
conditions rather than a permission: the non-terminal path stays
complete and byte-identical, the line re-presents only what that path
delivers anyway, and it carries no value the caller typed.

Each condition is a test here rather than a sentence anywhere. The
determinism proof is the load-bearing one and it is made twice over. Its
narrow half is below: one command run at a terminal and through a pipe,
and the piped bytes compared against a run with the affordance
monkeypatched away entirely. Its broad half is the rest of this suite,
which runs with stderr redirected into a capture and would show these
carriage returns in the middle of a few hundred assertions about output
if the terminal check were ever to stop being made.
"""

import contextlib
import io
import itertools
import re
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.support.config_cli import runner
from vinga_server.config import Config, cli
from vinga_server.config.responses import (
    AgentsReload,
    ConfigReloadResult,
    FillersReload,
    McpReloadResult,
    PromptsReload,
    ProvidersReload,
)
from vinga_server.tools.mcp import McpServers

# Two values the caller supplies and no line may repeat: the name of an
# entry inside the document, and the path of the document itself. Shaped
# so a substring check for either cannot match by accident.
SENTINEL_ENTRY = "sentinel-agent-4c1f"

SENTINEL_FILE = "sentinel-document-9b2e.yaml"

# How long one redraw is held inside the stream, in the case that holds
# the erase to arriving after it. Longer than the bounded wait for the
# writer that this design used to have, because a hold shorter than that
# bound passes against the implementation the case exists to reject.
HELD_FOR_S = 1.2

DOCUMENT = f"""\
providers:
  llm:
    claude: {{type: anthropic, model: m}}
agents:
  {SENTINEL_ENTRY}: {{prompt: You are Sam., llm: claude}}
"""


@pytest.fixture
def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """One command run the way the entry point runs it, against a server
    of this test's own, with a running server behind it for the apply to
    reach: the route refuses outright without one, and what these tests
    are about is a wait that ends in an answer."""
    started = runner(monkeypatch)
    started.runtime["mcp_servers"] = _running()
    started.runtime["reload"] = _reload
    return started


@pytest.fixture
def document(tmp_path: Path) -> Path:
    """The document an import is given, at a path carrying a sentinel of
    its own: a path is as much the caller's word as an entry name is."""
    written = tmp_path / SENTINEL_FILE
    written.write_text(DOCUMENT, encoding="utf-8")
    return written


def _running() -> McpServers:
    """A registry built the way a server builds one and never started.
    Nothing is configured in it, because what these tests read is the
    wait rather than what an apply applied."""
    return McpServers.build(
        Config(
            server={},
            providers={
                stage: {"mock": {"type": "mock"}} for stage in ("llm", "asr", "tts", "vad")
            },
            mcp_servers={},
            agent_defaults=dict.fromkeys(("llm", "asr", "tts", "vad"), "mock"),
            agents={"sam": {"prompt": "A"}},
            default_agent="sam",
        )
    )


async def _reload() -> ConfigReloadResult:
    """What a running server answers the apply with. Every list empty
    and the same lists every time, because what these tests compare is
    two runs of one command: an answer that varied would be the reason
    they differed."""
    return ConfigReloadResult(
        mcp=McpReloadResult(started=[], restarted=[], stopped=[], unchanged=[], servers={}),
        prompts=PromptsReload(changed=[]),
        fillers=FillersReload(resynthesized=[], reused=[], disabled=[]),
        providers=ProvidersReload(built=[], reused=[], retired=[]),
        agents=AgentsReload(added=[], removed=[], defaults_changed=False),
    )


# The licence's own proof


@pytest.mark.parametrize("verb", ["import", "apply"])
def test_the_bytes_off_a_terminal_are_the_same_with_the_line_and_without(
    run, document: Path, monkeypatch: pytest.MonkeyPatch, verb: str
) -> None:
    """The condition the affordance is licensed under, held the only way
    it can be held: the same command run twice off a terminal, once with
    the feature and once with it monkeypatched inert, and the two
    compared byte for byte on both streams.

    The document is imported once before either run, because an import
    says of each entry whether it moved: two runs are comparable only
    when they find the same store.
    """
    argv = _argv(verb, document)
    _captured(run, argv, terminal=False)

    with_the_line = _captured(run, argv, terminal=False)
    monkeypatch.setattr(cli, "narrated", _inert)
    without_it = _captured(run, argv, terminal=False)

    assert with_the_line == without_it


@pytest.mark.parametrize("verb", ["import", "apply"])
def test_the_line_is_drawn_at_a_terminal_and_leaves_stdout_alone(
    run, document: Path, verb: str
) -> None:
    """The affordance itself, and the two halves of the licence it is
    bounded by. It is drawn when stderr is a terminal; the data a caller
    came for is the same bytes either way, because nothing about the
    answer is what varies.
    """
    argv = _argv(verb, document)
    _captured(run, argv, terminal=False)

    piped = _captured(run, argv, terminal=False)
    at_a_terminal = _captured(run, argv, terminal=True)

    assert f"{cli.PROGRESS_PHASE}: 0s" in at_a_terminal[1]
    assert cli.PROGRESS_PHASE not in piped[1]
    assert at_a_terminal[0] == piped[0]


@pytest.mark.parametrize("verb", ["import", "apply"])
def test_the_line_takes_itself_back_off_the_screen(run, document: Path, verb: str) -> None:
    """What the erase is for: whatever prints next prints into an empty
    line rather than over half a sentence about waiting. So the last
    carriage return is the erase's own, and nothing of the line survives
    behind it."""
    _, err = _captured(run, _argv(verb, document), terminal=True)

    drawn = f"{cli.PROGRESS_PHASE}: 0s"
    assert f"\r{' ' * len(drawn)}\r" in err
    assert cli.PROGRESS_PHASE not in _after_the_line(err)


def test_the_line_repeats_nothing_the_caller_typed(run, document: Path) -> None:
    """The no-leak posture applies to progress exactly as it applies to
    a refusal. An import is the case with something to leak: the
    document names an entry and the command line names a file, and
    neither may reach a line whose whole content is a fixed word and a
    number."""
    printed, err = _captured(run, _argv("import", document), terminal=True)

    # The run really did carry both values, so their absence below is
    # the line's discipline rather than a command that did nothing.
    assert SENTINEL_ENTRY in printed
    assert SENTINEL_ENTRY not in err
    assert SENTINEL_FILE not in err
    assert str(document) not in err


def test_a_writer_that_will_not_start_changes_nothing_about_the_command(
    run, document: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An interpreter out of thread stacks raises from `start`, and the
    boundary above this catches two exception classes, neither of them
    that one. Unhandled it would be a traceback, a line left standing on
    the screen, and a request never made because a progress line could
    not be drawn. So it is handled: the line drawn on the way in comes
    back off, and the command runs and answers exactly as it does with a
    writer behind it."""
    argv = _argv("import", document)
    _captured(run, argv, terminal=False)
    expected = _captured(run, argv, terminal=False)
    monkeypatch.setattr(cli, "threading", _NoThreads)

    printed, err = _captured(run, argv, terminal=True)

    assert printed == expected[0]
    assert cli.PROGRESS_PHASE not in _after_the_line(err)
    assert _after_the_line(err) == expected[1]


def test_a_refusal_arrives_whole_after_the_line_has_gone(run) -> None:
    """The wait ends in a refusal as readily as in an answer, and the
    refusal is the one thing the command still has to say. So the erase
    is on the way out of the narration rather than on the answering path
    through it, and the sentence lands in an empty line with the exit
    code it always had."""
    # No running server for the apply to reach, which is the refusal
    # this route answers with and the one an operator meets most.
    run.runtime["reload"] = None

    printed, err = _captured(run, ("apply",), terminal=True, code=1)

    assert printed == ""
    assert "no running server" in _after_the_line(err)


# What does not narrate


def test_only_the_two_long_waits_narrate() -> None:
    """Read off the registration table rather than listed here, so a
    third command that quietly asked for a progress line fails this
    instead of shipping. `events tail` is the deliberate absence: there
    the stream is the answer rather than the wait."""
    narrating = {row.words for row in cli.COMMANDS if any(act.narrates for act in row.acts())}

    assert narrating == {("import",), ("apply",)}


def test_a_read_at_a_terminal_says_nothing_about_waiting(run) -> None:
    """The same claim from the other end, through the entry point: a
    command whose wait is the ordinary one draws nothing, terminal or
    not."""
    _, err = _captured(run, ("list",), terminal=True)

    assert cli.PROGRESS_PHASE not in err


# The mechanism, driven directly


def test_the_number_on_the_line_moves() -> None:
    """The number moves, which is the whole of what the line is for. A
    count of redraws would not say it: an implementation that printed
    `0s` for ever would satisfy one and tell an operator nothing.

    So the clock is driven as well as the cadence, through the two
    parameters a caller has rather than by reaching into the module, and
    what is asserted is the sequence of elapsed values. The fake clock
    hands out one whole second per reading, so the first redraw is one
    second in whatever the machine was doing at the time.
    """
    ticking = _Ticking()
    errors = _Stream(terminal=True)

    with contextlib.redirect_stderr(errors):
        with cli.narrated(True, cadence_s=0.001, clock=ticking):
            _until(lambda: len(_seconds(errors.getvalue())) >= 3)

    assert _seconds(errors.getvalue())[:3] == ["0s", "1s", "2s"]


@pytest.mark.parametrize("cadence", [0.0, -1.0])
def test_a_cadence_that_is_not_a_cadence_is_refused(cadence: float) -> None:
    """Zero is the value the rule exists for: an event waited on for
    zero seconds answers at once, so the redraw loop would spin a core
    and rewrite the terminal as fast as the stream took it. A
    programmer's mistake rather than an operator's, since no command
    line reaches this number, so it is refused where it is read rather
    than sanitized into a sentence."""
    errors = _Stream(terminal=True)

    with contextlib.redirect_stderr(errors):
        with pytest.raises(ValueError, match="cadence"):
            with cli.narrated(True, cadence_s=cadence):
                pass  # pragma: no cover - the refusal is on the way in

    assert errors.getvalue() == ""


def test_a_redraw_held_inside_the_stream_cannot_land_after_the_erase() -> None:
    """The claim the erase makes, held against the case that breaks a
    timed wait for the writer.

    A redraw is caught inside `write` and kept there, and the context is
    left while it is still in there. The erase has to wait for it and
    then be the last thing written, and nothing the writer does
    afterwards may reach the stream at all.

    The hold is longer than a second on purpose, and it is why this
    case costs what it costs. What it is written against is a bounded
    wait for the writer, which this design had and which let exactly
    this redraw land on top of whatever printed next; a hold shorter
    than the bound passes against that implementation and proves
    nothing. No finite hold can prove there is no bound at all, so this
    one proves there is none up to a second and a bit, which is where
    the bound was.
    """
    stream = _Holding()

    with contextlib.redirect_stderr(stream):
        with cli.narrated(True, cadence_s=0.001):
            _until(stream.held.is_set)
            # Let go well after the context is left, so the erase meets
            # a redraw that is still inside the stream rather than one
            # that has finished with it.
            threading.Timer(HELD_FOR_S, stream.release.set).start()

    # The held redraw has to have landed before there is an order to
    # assert anything about. Under the rule above it landed before the
    # context was left, and this returns at once; under a bounded wait
    # it lands here, which is the failure.
    _until(lambda: len(stream.taken()) >= 3)
    at_the_exit = stream.taken()
    assert cli.PROGRESS_PHASE not in at_the_exit[-1]
    # Several cadences later, and the writer is still running: what
    # stops it writing is the erase rather than the clock.
    time.sleep(0.05)
    assert stream.taken() == at_the_exit


def test_a_stream_that_will_not_be_written_to_takes_nothing_down() -> None:
    """An affordance may not fail a command. A stream closed under a
    running command answers a `ValueError` from the object and an
    `OSError` from the descriptor under it, and neither is a reason for
    an import that is talking to a server to stop.

    Counted rather than merely survived: three refusals means the first
    draw was refused, the writer went on redrawing afterwards rather
    than dying inside a thread nobody is waiting on, and the erase on
    the way out was refused too without raising through the caller.
    """
    refusing = _Refusing()

    with contextlib.redirect_stderr(refusing):
        with cli.narrated(True, cadence_s=0.01):
            _until(lambda: refusing.attempts >= 3)

    assert refusing.attempts >= 3


# The scaffolding


class _Stream(io.StringIO):
    """An output stream whose `isatty` this test decides.

    Locked, unlike the copies of this class in the neighbouring suites,
    because this is the one place a second thread writes to the stream
    while the test reads it.
    """

    def __init__(self, terminal: bool) -> None:
        super().__init__()
        self.terminal = terminal
        self.lock = threading.Lock()

    def isatty(self) -> bool:
        return self.terminal

    def write(self, text: str) -> int:
        with self.lock:
            return super().write(text)

    def getvalue(self) -> str:
        with self.lock:
            return super().getvalue()


class _Refusing(io.StringIO):
    """A terminal that refuses every write, which is what a stream
    closed under a running command is, and counts what it refused."""

    def __init__(self) -> None:
        super().__init__()
        self.attempts = 0
        self.lock = threading.Lock()

    def isatty(self) -> bool:
        return True

    def write(self, text: str) -> int:
        with self.lock:
            self.attempts += 1
        raise ValueError("I/O operation on closed file")


class _Holding(io.StringIO):
    """A terminal that catches one redraw inside `write` and keeps it
    there until the test lets it go, which is what a stopped reader and
    a flow-controlled terminal both look like from in here.

    Each write is recorded when it FINISHES rather than when it starts,
    because what the question is about is the order bytes reach a
    screen. A record taken on the way in would put a held redraw ahead
    of an erase that overtook it, which is the very inversion this is
    written to catch.
    """

    def __init__(self) -> None:
        super().__init__()
        self.writes: list[str] = []
        self.lock = threading.Lock()
        self.started = 0
        self.held = threading.Event()
        self.release = threading.Event()

    def isatty(self) -> bool:
        return True

    def write(self, text: str) -> int:
        with self.lock:
            self.started += 1
            order = self.started
        # The second write is the first redraw: the first is the line
        # the calling thread draws on the way in, and holding that one
        # would hold the command rather than the writer.
        if order == 2:
            self.held.set()
            self.release.wait(HELD_FOR_S * 5)
        with self.lock:
            self.writes.append(text)
        return len(text)

    def taken(self) -> list[str]:
        with self.lock:
            return list(self.writes)


class _Ticking:
    """A clock that hands out one whole second per reading, so what a
    redraw displays is decided by this test rather than by how long the
    machine took to get there."""

    def __init__(self) -> None:
        self.readings = itertools.count()
        self.lock = threading.Lock()

    def __call__(self) -> float:
        with self.lock:
            return float(next(self.readings))


class _Unstartable:
    """A thread an interpreter out of thread stacks will not start."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def start(self) -> None:
        raise RuntimeError("can't start new thread")


class _NoThreads:
    """The threading module as that interpreter presents it.

    Substituted for the name the CLI module holds rather than for the
    module itself, so it is this affordance that cannot start a thread
    and not the test client serving the request underneath it.
    """

    Event = threading.Event
    Lock = threading.Lock
    Thread = _Unstartable


@contextlib.contextmanager
def _inert(
    narrates: bool,
    cadence_s: float | None = None,
    clock: object | None = None,
) -> Iterator[None]:
    """The narration, not there at all: what the control run of the
    determinism proof is compared against."""
    yield


def _argv(verb: str, document: Path) -> tuple[str, ...]:
    return ("import", "-f", str(document)) if verb == "import" else (verb,)


def _captured(
    run, argv: tuple[str, ...], terminal: bool, code: int = 0
) -> tuple[str, str]:
    """What one command wrote to each stream, with both of them saying
    whether they are a terminal."""
    printed, errors = _Stream(terminal), _Stream(terminal)
    with contextlib.redirect_stdout(printed), contextlib.redirect_stderr(errors):
        assert run(*argv) == code
    return printed.getvalue(), errors.getvalue()


def _seconds(err: str) -> list[str]:
    """Every elapsed value the line has been drawn with, in order."""
    return re.findall(rf"{re.escape(cli.PROGRESS_PHASE)}: (\d+s)", err)


def _after_the_line(err: str) -> str:
    """What is left on the screen once the line has erased itself, which
    is everything after the last carriage return anything wrote."""
    return err.rsplit("\r", 1)[-1]


def _until(answered, seconds: float = 5.0) -> None:
    """Wait for something a thread is doing, or give up and let the
    assertion after this say what did not happen."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and not answered():
        time.sleep(0.005)
