"""The `samtal-server conversations` commands.

Two things are worth pinning. That `purge` really is the escape hatch it
claims to be: it reaches the file with no server running, it refuses
rather than guesses when no selector is given, and it reports a missing
store instead of creating one. And that no refusal repeats what was
typed, which is the same rule the config group speaks and the reason
both parsers turn argparse's own usage errors into ConfigErrors.
"""

import datetime as dt
import logging
from pathlib import Path
from typing import Any

import pytest

from samtal_server import logs
from samtal_server.config.loader import ConfigError
from samtal_server.conversations import cli
from samtal_server.conversations.records import ToolInvocation, TurnRecord
from samtal_server.conversations.store import ConversationStore, conversations_path

# Shaped like something that must not be echoed back, and used as a
# session id so that it travels through the selector, the counts and
# every refusal.
SENTINEL = "hunter2-not-a-real-credential-9f31c7"


@pytest.fixture
def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The commands compose `server.database.dir` the way the server
    does, so pointing that key at a temporary directory is the whole of
    the setup. No config file, and none needed."""
    monkeypatch.delenv("SAMTAL_CONFIG", raising=False)
    monkeypatch.setenv("SAMTAL_SERVER__DATABASE__DIR", str(tmp_path))

    def _run(*argv: str) -> int:
        return cli.main(list(argv))

    return _run


def manifest(started_at: dt.datetime, device: str) -> dict[str, Any]:
    return {
        "started_at": started_at.isoformat(),
        "server": {"version": "0.1.0", "revision": "abc1234"},
        "device": {"mac": device, "client": "test"},
        "protocol": "1",
        "agent": "sam",
        "agents": ["sam"],
        "providers": {},
    }


@pytest.fixture
def recorded(tmp_path: Path):
    """A store with sessions in it, closed before the command runs, the
    way a purge normally finds one."""

    def _record(*sessions: tuple[str, dt.datetime, str]) -> None:
        store = ConversationStore(tmp_path, retention_days=0)
        store.start()
        for session, started_at, device in sessions:
            store.open_session(session, 100.0, manifest(started_at, device))
            store.record_event(session, "heard", logging.INFO, {"duration_s": 1.0}, 101.0)
            store.record_turn(
                session,
                TurnRecord(
                    t_ms=1000,
                    agent="sam",
                    heard="hello",
                    reply="Hi.",
                    tools=(ToolInvocation(position=0, source="builtin", name="remember"),),
                ),
            )
            store.close_session(session, duration_s=4.0, reason="client")
        store.stop()

    return _record


def remaining(directory: Path) -> list[str]:
    from sqlalchemy import text

    from samtal_server.conversations.store import open_conversations

    engine = open_conversations(directory)
    try:
        with engine.connect() as connection:
            return [
                row[0]
                for row in connection.execute(text("select session from sessions order by id"))
            ]
    finally:
        engine.dispose()


NOW = dt.datetime(2026, 8, 15, 12, 0, tzinfo=dt.UTC)


def test_purge_by_session_prints_what_it_deleted(
    tmp_path: Path, run, recorded, capsys: pytest.CaptureFixture[str]
) -> None:
    recorded(("keep", NOW, "aa:aa:aa:aa:aa:aa"), ("drop", NOW, "aa:aa:aa:aa:aa:aa"))

    assert run("purge", "--session", "drop") == 0

    printed = capsys.readouterr().out
    assert "sessions: 1" in printed
    assert "turns: 1" in printed
    assert "tool_invocations: 1" in printed
    assert "events: 1" in printed
    assert remaining(tmp_path) == ["keep"]


def test_purge_by_device_and_before_combine(tmp_path: Path, run, recorded) -> None:
    """Given together the selectors narrow, so an operator can say "that
    device, but only what it recorded before Friday"."""
    kitchen, study = "aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb"
    recorded(
        ("old-kitchen", dt.datetime(2026, 8, 1, tzinfo=dt.UTC), kitchen),
        ("new-kitchen", dt.datetime(2026, 8, 14, tzinfo=dt.UTC), kitchen),
        ("old-study", dt.datetime(2026, 8, 1, tzinfo=dt.UTC), study),
    )

    assert run("purge", "--device", kitchen, "--before", "2026-08-10") == 0

    assert remaining(tmp_path) == ["new-kitchen", "old-study"]


def test_purge_by_device_alone_takes_every_session_of_it(
    tmp_path: Path, run, recorded
) -> None:
    kitchen, study = "aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb"
    recorded(("one", NOW, kitchen), ("two", NOW, kitchen), ("three", NOW, study))

    assert run("purge", "--device", kitchen) == 0

    assert remaining(tmp_path) == ["three"]


def test_purge_before_a_day_alone_takes_what_started_earlier(
    tmp_path: Path, run, recorded
) -> None:
    recorded(
        ("yesterday", dt.datetime(2026, 8, 14, 23, 59, tzinfo=dt.UTC), "aa:aa:aa:aa:aa:aa"),
        ("today", dt.datetime(2026, 8, 15, 0, 1, tzinfo=dt.UTC), "aa:aa:aa:aa:aa:aa"),
    )

    assert run("purge", "--before", "2026-08-15") == 0

    assert remaining(tmp_path) == ["today"]


def test_a_purge_with_no_selector_names_the_three(
    tmp_path: Path, run, recorded, capsys: pytest.CaptureFixture[str]
) -> None:
    """Deleting the whole store is not something a command does by
    omission, so the refusal says which selectors exist rather than
    doing the widest thing that parses."""
    recorded(("keep", NOW, "aa:aa:aa:aa:aa:aa"))

    assert run("purge") == 1

    captured = capsys.readouterr()
    assert "--session" in captured.err
    assert "--device" in captured.err
    assert "--before" in captured.err
    assert "Traceback" not in captured.err
    assert remaining(tmp_path) == ["keep"]


def test_a_missing_store_is_reported_and_never_created(
    tmp_path: Path, run, capsys: pytest.CaptureFixture[str]
) -> None:
    """An operator deleting from a store that is not there has the path
    wrong, and answering by bringing an empty one into existence would
    hide that."""
    assert run("purge", "--session", "whatever") == 1

    captured = capsys.readouterr()
    assert "server.database.dir" in captured.err
    assert "Traceback" not in captured.err
    assert not conversations_path(tmp_path).exists()


def test_a_malformed_date_names_the_format_rather_than_the_value(
    tmp_path: Path, run, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("purge", "--before", SENTINEL) == 1

    captured = capsys.readouterr()
    assert "YYYY-MM-DD" in captured.err
    assert SENTINEL not in captured.err
    assert SENTINEL not in captured.out


def test_a_mistake_in_the_grammar_leaves_by_the_same_door(
    tmp_path: Path, run, caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """argparse writes to stderr and exits 2 from inside parse_args,
    which would make an unknown command the one failure that bypasses
    the documented exit code. It also quotes what was typed: an
    unrecognized command comes back as `invalid choice: 'x'` and extra
    arguments come back verbatim, so the sentinel is planted as each in
    turn and hunted everywhere the command writes."""
    with caplog.at_level(logging.DEBUG):
        # As the command word, which argparse answers with invalid
        # choice.
        assert run(SENTINEL) == 1
        # As an extra argument, which it answers with unrecognized
        # arguments.
        assert run("purge", "--session", "x", SENTINEL) == 1
        # And as an option's missing value, which reports the option
        # rather than the value but is checked with the rest.
        assert run("purge", "--session") == 1
        assert run() == 1

    captured = capsys.readouterr()
    rendered = caplog.text + "".join(
        logs.JsonFormatter().format(record) for record in caplog.records
    )
    assert SENTINEL not in captured.err
    assert SENTINEL not in captured.out
    assert SENTINEL not in rendered
    assert "Traceback" not in captured.err
    # And the refusal for a word that is not a command still says which
    # words are.
    assert "purge" in captured.err
    assert "schema" in captured.err


def test_a_failed_purge_leaks_nothing_of_what_it_held(
    tmp_path: Path, run, recorded, capsys: pytest.CaptureFixture[str]
) -> None:
    """The utterance is in the file and the session id is on the command
    line; neither may come back out through a refusal."""
    recorded((SENTINEL, NOW, "aa:aa:aa:aa:aa:aa"))
    conversations_path(tmp_path).unlink()

    assert run("purge", "--session", SENTINEL) == 1

    captured = capsys.readouterr()
    assert SENTINEL not in captured.err
    assert SENTINEL not in captured.out


def test_the_schema_command_prints_the_reference_and_opens_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The directory this names cannot be created, so a command that
    opened the database would fail here rather than print."""
    monkeypatch.delenv("SAMTAL_CONFIG", raising=False)
    monkeypatch.setenv("SAMTAL_SERVER__DATABASE__DIR", "/nowhere/at/all")

    assert cli.main(["schema"]) == 0

    printed = capsys.readouterr().out
    assert printed.startswith("# Conversation store schema reference")
    assert "### `sessions`" in printed


def test_the_purge_help_says_what_it_does_not_touch(
    tmp_path: Path, run, capsys: pytest.CaptureFixture[str]
) -> None:
    """Two consequences an operator cannot read off the counts: the
    capture triplet stays, and a session that is still running stops
    being recorded."""
    with pytest.raises(SystemExit) as left:
        run("purge", "--help")

    assert left.value.code == 0
    printed = capsys.readouterr().out
    assert "Capture files are never touched" in printed
    assert "still running ends its recording" in printed


def test_the_command_word_dispatches_to_this_group(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A word check in main rather than an argparse subparser, so adding
    the group cannot change how `samtal-server --config path` parses."""
    from samtal_server import main as entrypoint

    monkeypatch.setattr(
        entrypoint.sys, "argv", ["samtal-server", "conversations", "schema"]
    )
    monkeypatch.delenv("SAMTAL_CONFIG", raising=False)
    monkeypatch.setenv("SAMTAL_SERVER__DATABASE__DIR", "/nowhere/at/all")

    with pytest.raises(SystemExit) as left:
        entrypoint.main()

    assert left.value.code == 0
    assert capsys.readouterr().out.startswith("# Conversation store schema reference")


# What a database failure may say


def test_a_locked_store_is_the_retryable_refusal(
    tmp_path: Path, run, recorded, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A purge takes the write lock before it reads, so another writer
    holding it is the one failure an operator answers differently: by
    running the command again. It must be a sentence and an exit code,
    never a traceback out of SQLAlchemy."""
    from sqlalchemy import text

    import samtal_server.db as db_module
    from samtal_server.conversations.store import open_conversations

    # The listener reads this when a connection is made, so shortening
    # it here is what keeps the test from waiting out the real ten
    # seconds. The real value is pinned in the store's defaults suite,
    # where a number and not a wait is what is being asserted.
    monkeypatch.setattr(db_module, "BUSY_TIMEOUT_MS", 200)
    recorded(("keep", NOW, "aa:aa:aa:aa:aa:aa"))
    holder = open_conversations(tmp_path)
    held = holder.connect()
    # The write lock, taken and kept: the engine begins immediate, so
    # this transaction owns it until the connection closes.
    held.execute(text("select count(*) from sessions"))
    try:
        assert run("purge", "--session", "keep") == 1
    finally:
        held.close()
        holder.dispose()

    captured = capsys.readouterr()
    assert "run the command again" in captured.err
    assert "Traceback" not in captured.err
    assert "SQL" not in captured.err
    assert remaining(tmp_path) == ["keep"]


def test_a_driver_failure_says_the_kind_and_not_the_bytes(
    tmp_path: Path, run, recorded, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture,
) -> None:
    """A SQLAlchemy error holds the statement it failed on together with
    the parameters bound to it, and a purge binds the selector it was
    given. None of that may come back out, and neither may the chain:
    the refusal is raised outside the handler so the library's exception
    is not reachable through __context__ from the one that travels."""
    import samtal_server.conversations.store as store_module

    recorded((SENTINEL, NOW, "aa:aa:aa:aa:aa:aa"))

    def poisoned(*_args: object, **_kwargs: object):
        raise RuntimeError(f"near {SENTINEL}: disk I/O error")

    monkeypatch.setattr(store_module, "_delete_sessions", poisoned)

    left = None
    try:
        with caplog.at_level(logging.DEBUG):
            assert run("purge", "--session", SENTINEL) == 1
    except BaseException as exc:  # pragma: no cover, only on a regression
        left = exc
    assert left is None

    captured = capsys.readouterr()
    rendered = caplog.text + "".join(
        logs.JsonFormatter().format(record) for record in caplog.records
    )
    assert SENTINEL not in captured.err
    assert SENTINEL not in captured.out
    assert SENTINEL not in rendered
    assert "server.database.dir" in captured.err
    assert "Traceback" not in captured.err


def test_the_refusal_carries_no_exception_chain(tmp_path: Path, recorded) -> None:
    """Raised after the handler rather than inside it. `raise ... from
    None` would not do: it sets __suppress_context__ and leaves the
    reference in place, so anything walking the chain still reaches the
    driver's message."""
    import samtal_server.conversations.store as store_module
    from samtal_server.conversations.store import purge as purge_directly

    recorded(("keep", NOW, "aa:aa:aa:aa:aa:aa"))
    original = store_module._delete_sessions

    def poisoned(*_args: object, **_kwargs: object):
        raise RuntimeError(f"near {SENTINEL}: disk I/O error")

    store_module._delete_sessions = poisoned
    try:
        with pytest.raises(ConfigError) as caught:
            purge_directly(tmp_path, session="keep")
    finally:
        store_module._delete_sessions = original

    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert SENTINEL not in str(caught.value)


def test_a_store_that_vanishes_after_the_check_is_still_a_sentence(
    tmp_path: Path, run, recorded, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The command checks the file is there and then opens it, and a
    volume can go away in between. The race is not worth locking against
    and is worth not crashing on."""
    import samtal_server.conversations.cli as cli_module

    recorded(("keep", NOW, "aa:aa:aa:aa:aa:aa"))
    real = cli_module.conversations_path

    def vanishing(directory):
        path = real(directory)
        # Read once by the existence check, then removed underneath the
        # open that follows it.
        if path.is_file():
            path.unlink()
        return path

    monkeypatch.setattr(cli_module, "conversations_path", vanishing)

    assert run("purge", "--session", "keep") == 1

    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert "server.database.dir" in captured.err
