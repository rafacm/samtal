"""The `.env` file, read before anything looks at the environment.

Both spellings read one, because both have to behave identically and
the console script never reaches `vinga-server.main`. It is the second
file this process opens on a path nobody validated, and it is the one
most likely to hold credentials: what a `.env` carries is exactly the
variables the API token and the provider keys come from.

So what is held here is that it is read behind the refusal boundary and
not in front of it. A `.env` that will not open leaves as one fixed
sentence; a `.env` that will not decode leaves as the same one, and the
exception that carried its bytes is not on the chain of what leaves.
The sentinels are the values a real `.env` would hold, an address and a
credential, and both are checked on every surface a value can come out
on: the two streams, the log records made while the case ran, and the
exception the refusal is carried by, chain included.
"""

import logging
from pathlib import Path

import pytest

from tests.support.config_cli import chain as _chain
from tests.support.config_cli import logged as _logged
from tests.support.config_cli import runner
from vinga_server.config import cli
from vinga_server.config.loader import DOTENV_UNREADABLE, ConfigError, load_environment_file

# What a `.env` holds, shaped so a substring check for one cannot match
# by accident. The address is a sentinel too: an operator's API address
# is a value this CLI refuses to print unsanitized, and a `.env` is
# where it is most often written down.
PLANTED_URL = "http://127.0.0.1:9231/api?token=sk-env-4f8b2c9e-never-a-real-credential"

PLANTED_SECRET = "sk-env-7a1d3f60-never-a-real-credential"

PLANTED_PATH = "sk-env-2b6e5c41-never-a-real-credential"


@pytest.fixture
def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    return runner(monkeypatch)


def a_dotenv(directory: Path, text: str) -> Path:
    """One `.env` where the search from the invocation directory finds
    it, which is the only place this looks."""
    path = directory / ".env"
    path.write_text(text, encoding="utf-8")
    return path


def surfaces(
    capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> dict[str, str]:
    """The four places a value can come out on. The chain is read from
    the boundary directly, which is the only place it exists: `main`
    catches this exception by design and answers with a sentence."""
    captured = capsys.readouterr()
    carried = ""
    try:
        load_environment_file()
    except ConfigError as refusal:
        carried = _chain(refusal)
    return {
        "stdout": captured.out,
        "stderr": captured.err,
        "logs": _logged(caplog),
        "chain": carried,
    }


def test_a_dotenv_is_read_and_the_real_environment_still_wins(
    run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The behavior the boundary is wrapped around, so a refusal that
    swallowed the load would fail here rather than pass quietly.

    `VINGA_API_URL` is the variable to prove it with, because what a
    command does with it is observable: the address the client is built
    on is recorded by the seam.
    """
    a_dotenv(tmp_path, f"{cli.API_URL_ENV}={PLANTED_URL}\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(cli.API_URL_ENV, raising=False)

    assert run("list") == 0

    # Read from the file, and reached: the query the address carried is
    # held apart from the base the client is built on.
    assert run.reached[-1].startswith("http://127.0.0.1:9231/api")
    capsys.readouterr()

    # And the real environment wins over the file, which is the rule
    # both entry points document.
    monkeypatch.setenv(cli.API_URL_ENV, "http://127.0.0.1:9232/api")

    assert run("list") == 0

    assert run.reached[-1] == "http://127.0.0.1:9232/api"


def test_a_dotenv_that_is_not_text_is_refused_without_quoting_its_bytes(
    run,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The failure that retains what it could not read.

    `UnicodeDecodeError` is a `ValueError` rather than an `OSError`, so
    an arm catching only the second would let it past, and the exception
    it leaves as holds the buffer it was decoding. That buffer is a
    `.env`, which is to say somebody's credentials.
    """
    (tmp_path / ".env").write_bytes(
        f"{cli.API_URL_ENV}={PLANTED_URL}\nA_KEY={PLANTED_SECRET}\n".encode() + b"\xff\xfe"
    )
    monkeypatch.chdir(tmp_path)
    capsys.readouterr()

    with caplog.at_level(logging.DEBUG):
        assert run("list") == 1

    found = surfaces(capsys, caplog)
    assert found["stderr"] == DOTENV_UNREADABLE + "\n"
    assert found["stdout"] == ""
    assert "Traceback" not in found["stderr"]
    for sentinel in (PLANTED_URL, PLANTED_SECRET):
        assert [where for where, text in found.items() if sentinel in text] == []


@pytest.mark.parametrize(
    "raised",
    [
        OSError(13, "Permission denied", PLANTED_PATH),
        IsADirectoryError(21, "Is a directory", PLANTED_PATH),
        ValueError(f"cannot parse {PLANTED_SECRET}"),
    ],
    ids=["not readable", "a directory", "a line the parser refuses"],
)
def test_every_way_a_dotenv_fails_answers_the_same_sentence(
    run,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    raised: BaseException,
) -> None:
    """The failures a filesystem produces and a test cannot always ask
    for, injected at the library call.

    Each of them holds something: an operating-system error holds the
    path it was given, and the parser's holds the line it choked on.
    Neither is printed, and neither is on the chain of what leaves.
    """
    a_dotenv(tmp_path, f"A_KEY={PLANTED_SECRET}\n")
    monkeypatch.chdir(tmp_path)

    def refusing(*_args: object, **_kwargs: object) -> bool:
        raise raised

    monkeypatch.setattr("vinga_server.config.loader.load_dotenv", refusing)
    capsys.readouterr()

    with caplog.at_level(logging.DEBUG):
        assert run("list") == 1

    found = surfaces(capsys, caplog)
    assert found["stderr"] == DOTENV_UNREADABLE + "\n"
    assert found["stdout"] == ""
    for sentinel in (PLANTED_SECRET, PLANTED_PATH):
        assert [where for where, text in found.items() if sentinel in text] == []


def test_the_refusal_carries_nothing_of_the_failure_on_its_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The half no assertion about a stream can make.

    The sentence is built inside the handler and raised after it, so
    both chain slots are empty and nothing walking the chain finds the
    decoding error, or the bytes it held, behind the refusal.
    """
    (tmp_path / ".env").write_bytes(f"A_KEY={PLANTED_SECRET}\n".encode() + b"\xff\xfe")
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ConfigError) as caught:
        load_environment_file()

    assert str(caught.value) == DOTENV_UNREADABLE
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert PLANTED_SECRET not in _chain(caught.value)


# The invocations that are answered without the read
#
# Three shapes, one reason: an invocation that runs no command needs no
# environment, and a `.env` that will not decode is exactly the state in
# which a reader most needs those three to work.
#
# `--version` has to succeed whatever else is wrong. It is what an
# operator asks when they are already comparing two halves of a
# deployment that disagree, which is exactly when the rest of a machine
# is not in a state to be relied on. `--help` and a bare invocation are
# what they ask when they do not yet know what to type, and answering
# either with a sentence about a file they may not have written tells
# them nothing they can act on: the reader is trying to find out what
# this program is, and gets told its working directory is wrong.
#
# What makes all three true is where the read happens, which is on the
# way into a command (`_Verbatim.invoke`) rather than at the mouth of
# the boundary. Reading it first made every one of them exit 1 with a
# sentence about a file none of them was asked about.
#
# Both spellings for the version, because both read a `.env` and the
# contract is the grammar's rather than one entry point's.


def an_unreadable_dotenv(directory: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The refusal case, in the directory the search starts from: bytes
    no encoding will decode, carrying what a real `.env` carries."""
    (directory / ".env").write_bytes(
        f"{cli.API_URL_ENV}={PLANTED_URL}\nA_KEY={PLANTED_SECRET}\n".encode() + b"\xff\xfe"
    )
    monkeypatch.chdir(directory)


def test_the_version_answers_through_the_dispatch_with_a_broken_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    an_unreadable_dotenv(tmp_path, monkeypatch)

    with pytest.raises(SystemExit) as left:
        cli.main(["--version"])

    assert left.value.code == 0
    captured = capsys.readouterr()
    assert captured.out == f"{cli.DISTRIBUTION} {cli.installed_version()}\n"
    assert captured.err == ""


def test_the_version_answers_through_the_script_with_a_broken_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    an_unreadable_dotenv(tmp_path, monkeypatch)
    monkeypatch.setattr("sys.argv", ["vinga", "--version"])

    with pytest.raises(SystemExit) as left:
        cli.main()

    assert left.value.code == 0
    captured = capsys.readouterr()
    assert captured.out == f"{cli.DISTRIBUTION} {cli.installed_version()}\n"
    assert captured.err == ""


def test_a_root_option_before_the_version_is_still_the_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The root's options are consumed on the way, which is why this
    reads the declared parameters rather than searching for a string:
    `--config path --version` asks the root, and the value of an option
    is never the question."""
    an_unreadable_dotenv(tmp_path, monkeypatch)

    with pytest.raises(SystemExit) as left:
        cli.main(["--config", str(tmp_path / "nowhere.yaml"), "--no-input", "--version"])

    assert left.value.code == 0
    assert capsys.readouterr().out.startswith(cli.DISTRIBUTION)


@pytest.mark.parametrize(
    "argv",
    [
        ("--config", "--version"),
        ("list", "--version"),
        ("agent", "show", "--version"),
        ("list",),
        (),
    ],
    ids=[
        "the value of an option",
        "after a command word",
        "after a noun and a verb",
        "not asked",
        "nothing at all",
    ],
)
def test_everything_else_goes_to_the_parser(argv: tuple[str, ...]) -> None:
    """What this does not recognize keeps the answer it always had.
    `--config --version` is an option's value, and a command word ends
    the root: this grammar declares `--version` nowhere else, so the
    parser refuses those exactly as it did."""
    assert cli._version_asked(list(argv)) is False


def test_asking_for_help_answers_with_a_broken_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Nothing about a file, and the page on stdout, because `--help`
    is answered during the parse and no command runs."""
    an_unreadable_dotenv(tmp_path, monkeypatch)

    with pytest.raises(SystemExit) as left:
        cli.main(["--help"])

    assert left.value.code == 0
    captured = capsys.readouterr()
    assert captured.out.startswith(f"Usage: {cli.DISPATCHED}")
    assert DOTENV_UNREADABLE not in captured.err


# And the bare invocation, at the root and at a noun and at a sub-noun,
# which is the shape this was found in: the page a reader gets for
# typing the program's name is the one answer that has to work before
# they have set anything up at all, and a `.env` in the directory they
# happen to be standing in was answering it with a refusal instead.
BARE = [(), ("provider",), ("device", "pending")]


@pytest.mark.parametrize(
    "path", BARE, ids=["the group", "a noun", "a sub-noun"]
)
def test_a_bare_invocation_answers_its_page_with_a_broken_dotenv(
    run,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    path: tuple[str, ...],
) -> None:
    """The page, exit 1, and not a syllable of the file on any surface.

    The `.env` here is the one the other cases plant: a real address and
    a real credential followed by bytes no encoding will decode. It is
    never opened on this path, and this is what says so from the outside
    rather than by reading the code.
    """
    an_unreadable_dotenv(tmp_path, monkeypatch)
    capsys.readouterr()

    with caplog.at_level(logging.DEBUG):
        assert run(*path) == 1, path

    found = surfaces(capsys, caplog)
    assert found["stderr"].startswith(" ".join(["Usage:", cli.DISPATCHED, *path])), path
    assert DOTENV_UNREADABLE not in found["stderr"], path
    assert found["stdout"] == "", path
    for sentinel in (PLANTED_URL, PLANTED_SECRET):
        assert [where for where, text in found.items() if sentinel in text] == [], path


@pytest.mark.parametrize(
    "path", BARE, ids=["the group", "a noun", "a sub-noun"]
)
def test_a_bare_invocation_carries_no_dotenv_on_its_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: tuple[str, ...]
) -> None:
    """The surface `surfaces` cannot reach for this refusal: the chain
    of the exception the page is carried by, which is a different
    exception from the one a `.env` failure raises."""
    an_unreadable_dotenv(tmp_path, monkeypatch)

    with pytest.raises(ConfigError) as caught:
        cli._parsed(list(path), cli.DISPATCHED)

    assert caught.value.__cause__ is None, path
    assert caught.value.__context__ is None, path
    for sentinel in (PLANTED_URL, PLANTED_SECRET):
        assert sentinel not in _chain(caught.value), path


def test_a_broken_dotenv_still_stops_every_other_command(
    run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The other half, so the exemption is three invocations and not a
    hole: a command that needs the environment still meets the
    sentence."""
    an_unreadable_dotenv(tmp_path, monkeypatch)
    capsys.readouterr()

    assert run("list") == 1

    assert capsys.readouterr().err == DOTENV_UNREADABLE + "\n"
