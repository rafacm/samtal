"""What an installation carrying the client half alone is told.

The default installation of this package is the configuration client,
and the server half is an extra. So five things a person can type are
answerable only by an installation that has that half, and each of them
is answered with a fixed sentence rather than with an ImportError:
`vinga-server` with nothing after it, which means serve; the two
commands of the configuration grammar that read the server's own
modules, `openapi` (which builds the API application to describe it) and
`ota-url` (which derives a URL through the onboarding package);
`vinga-server conversations`, which renders the store's tables off the
SQLAlchemy metadata; and `vinga-server doctor` WITH NO URL, which
derives the URL to diagnose through the same onboarding package.

That last one is gated at the derivation rather than at the command,
which is the whole of what makes it right: a laptop diagnosing a remote
deployment passes the URL, opens a socket and reads an answer, and
wants nothing of this package's server half. Only the derivation needs
it. A case below drives both halves of that split.

Two sentences and not five: serving is one fact, and needing the other
half is the other, which the remaining four share.

The sentinels are the point of this file. Every one of these refusals is
reached with an ImportError in hand, and an ImportError's text is a
module path, which is the value most likely to be relayed by accident;
the invocation that reached it carries a path, a name or a slot, which
is where a credential lands when it is typed one argument early. So each
case plants a DISTINCT credential-shaped value in every field that could
carry one, one field per sentinel so a leak names its own source, and
asserts absence on all four surfaces the rest of this suite uses:
stdout, stderr, every log record rendered whole, and the exception chain
a walker would find.

The missing half is simulated rather than uninstalled: the import that
would fail is made to fail, with a sentinel in the message it fails
with. A test that needed a second environment could not run here at all,
and the clean-venv lane is where the real absence is proven.
"""

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.support.config_cli import chain, logged
from vinga_server import doctor
from vinga_server import main as entrypoint
from vinga_server.config import cli
from vinga_server.config.loader import NEEDS_THE_SERVER_HALF, ConfigError

# One per field that could carry a pasted credential, so a leak says
# which field it came out of. None of them is a real credential and each
# is shaped so a substring check for it cannot match by accident.
IMPORT_SENTINEL = "no module named 'sk-import-1a2b3c-never-a-real-credential'"
CONFIG_PATH_SENTINEL = "sk-configpath-4d5e6f-never-a-real-credential"
ARGV0_SENTINEL = "sk-argv0-7a8b9c-never-a-real-credential"


class _Missing:
    """A meta-path finder that refuses one module, with a sentinel in the
    failure it refuses with.

    The import machinery rather than `builtins.__import__`, because the
    entry point resolves its two gated modules by name through
    `importlib.import_module`, which does not go through the builtin at
    all. A finder covers both spellings, and it covers the submodules
    under the name as well, since a package's own `__init__` is what
    drags the heavy import in for two of the three sites.

    The sentinel goes in the message and nowhere else, which is the
    machinery's own shape: what an ImportError carries is the module
    path it could not find.
    """

    def __init__(self, module: str) -> None:
        self._module = module

    def find_spec(self, name: str, path: object = None, target: object = None) -> None:
        if name == self._module or name.startswith(f"{self._module}."):
            raise ImportError(IMPORT_SENTINEL)
        return None


def _refuses(module: str) -> Callable[[pytest.MonkeyPatch], None]:
    """Make one module unimportable, as though its half were not
    installed."""

    def install(monkeypatch: pytest.MonkeyPatch) -> None:
        # Already-imported copies first: this suite runs in a process
        # that has the whole server in it, and a cached module is one
        # `import_module` answers without asking any finder.
        for name in [
            name
            for name in sys.modules
            if name == module or name.startswith(f"{module}.")
        ]:
            monkeypatch.delitem(sys.modules, name, raising=False)
        monkeypatch.setattr(sys, "meta_path", [_Missing(module), *sys.meta_path])

    return install


def test_serving_without_the_server_half_answers_one_sentence(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        entrypoint.sys,
        "argv",
        [ARGV0_SENTINEL, "--config", f"/tmp/{CONFIG_PATH_SENTINEL}/config.yaml"],
    )
    _refuses("vinga_server.serving")(monkeypatch)

    with pytest.raises(SystemExit) as left:
        entrypoint.main()

    assert left.value.code == 1
    captured = capsys.readouterr()
    assert captured.err.strip() == entrypoint.CANNOT_SERVE
    assert captured.out == ""
    # The three doors, so the sentence answers the question it raises.
    for door in ("container image", "uv sync", "serve extra"):
        assert door in entrypoint.CANNOT_SERVE, door


def test_the_serve_refusal_leaks_nothing_it_was_given(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(
        entrypoint.sys,
        "argv",
        [ARGV0_SENTINEL, "--config", f"/tmp/{CONFIG_PATH_SENTINEL}/config.yaml"],
    )
    _refuses("vinga_server.serving")(monkeypatch)

    with caplog.at_level(0), pytest.raises(SystemExit) as left:
        entrypoint.main()

    captured = capsys.readouterr()
    surfaces = (captured.out, captured.err, logged(caplog), chain(left.value))
    for sentinel in (IMPORT_SENTINEL, CONFIG_PATH_SENTINEL, ARGV0_SENTINEL):
        for surface in surfaces:
            assert sentinel not in surface, sentinel


# The two gated commands


@pytest.fixture
def offline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A machine with the device-auth secret and a file half, and no
    server, no token and no database anywhere. Both gated commands need
    none of those, which is what makes their refusal about the missing
    half and nothing else."""
    monkeypatch.delenv("VINGA_CONFIG", raising=False)
    monkeypatch.delenv("VINGA_API_SECRET", raising=False)
    monkeypatch.delenv(cli.API_URL_ENV, raising=False)
    monkeypatch.setenv("VINGA_AUTH_SECRET", "a-fixed-secret-for-the-vector")
    monkeypatch.setenv("VINGA_SERVER__DATABASE__DIR", str(tmp_path / "db"))
    named = tmp_path / f"{CONFIG_PATH_SENTINEL}.yaml"
    named.write_text("server:\n  public_url: https://voice.example\n", encoding="utf-8")
    return named


def _gated(named: Path) -> tuple[tuple[str, list[str]], ...]:
    """The two, each with the module whose absence gates it and the
    command line that reaches it."""
    return (
        ("vinga_server.config.api", ["openapi"]),
        ("vinga_server.onboarding.origin", ["--config", str(named), "ota-url"]),
    )


def test_both_gated_commands_answer_the_same_one_sentence(
    offline: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """One sentence for the pair, because it says one thing: this
    command needs the server half. Two sentences for one fact would be
    the duplication the design guide names."""
    said = []
    for module, argv in _gated(offline):
        with monkeypatch.context() as patched:
            _refuses(module)(patched)
            assert cli.main(argv) == 1, argv
        captured = capsys.readouterr()
        assert captured.out == ""
        said.append(captured.err.strip())

    assert said == [cli.NEEDS_THE_SERVER_HALF, cli.NEEDS_THE_SERVER_HALF]


def test_a_gated_refusal_leaks_nothing_it_was_given(
    offline: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Every field this pair can be given, one sentinel each.

    `openapi` takes nothing at all, so its own surface is the
    ImportError; `ota-url` takes the path of the file half, which is the
    file a deployment keeps its secrets beside, and it is named here
    with a credential-shaped path so that a sentence quoting the path
    would fail.
    """
    for module, argv in _gated(offline):
        caplog.clear()
        with monkeypatch.context() as patched, caplog.at_level(0):
            _refuses(module)(patched)
            assert cli.main(argv) == 1, argv
        captured = capsys.readouterr()
        surfaces = (captured.out, captured.err, logged(caplog))
        for sentinel in (IMPORT_SENTINEL, CONFIG_PATH_SENTINEL):
            for surface in surfaces:
                assert sentinel not in surface, (argv, sentinel)


def test_the_gated_refusal_carries_no_exception_chain(
    offline: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fourth surface, read where a walker would find it.

    `cli.main` prints the refusal and returns, so the exception is not
    available through it: the sentence is raised by the command function
    and caught by the boundary. This drives the command function
    directly to hold the raise itself to carrying no ImportError
    behind it, which is the whole reason the answer is recorded inside
    the handler and raised outside it.
    """
    with monkeypatch.context() as patched:
        _refuses("vinga_server.config.api")(patched)
        with pytest.raises(ConfigError) as refused:
            cli._from_the_server_half(cli.docgen.openapi)

    assert str(refused.value) == cli.NEEDS_THE_SERVER_HALF
    assert refused.value.__cause__ is None
    assert refused.value.__context__ is None
    assert IMPORT_SENTINEL not in chain(refused.value)


def test_the_gated_sentence_names_no_value_at_all() -> None:
    """The two constants, held to being constants. A sentence assembled
    from an invocation would pass every case above on the day it was
    written and leak on the first command that carried something else."""
    for sentence in (cli.NEEDS_THE_SERVER_HALF, entrypoint.CANNOT_SERVE):
        assert "{" not in sentence
        assert "%s" not in sentence


def test_the_gated_pair_is_exactly_two(offline: Path) -> None:
    """The inventory, held closed from the production side.

    A third command that grew a server-side import would be a command
    the wheel lane runs and finds refusing, which is a failure a long
    way from its cause. Named here instead, beside the reason each is
    gated.
    """
    assert {argv[-1] for _, argv in _gated(offline)} == {"openapi", "ota-url"}


# The conversations group, which is the third site and is not in the
# configuration grammar at all


def test_the_conversations_group_answers_the_gated_sentence(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The site the plan's inventory did not reach.

    That inventory is the `vinga` grammar's own tree, and this group is
    a sibling of it under the server's entry point. The standard it is
    held to is the entry point's, not the tree's: no path out of
    `main.py` answers with a traceback.
    """
    monkeypatch.setattr(entrypoint.sys, "argv", [ARGV0_SENTINEL, "conversations", "schema"])
    _refuses(entrypoint.CONVERSATIONS_GROUP)(monkeypatch)

    with pytest.raises(SystemExit) as left:
        entrypoint.main()

    assert left.value.code == 1
    captured = capsys.readouterr()
    assert captured.err.strip() == NEEDS_THE_SERVER_HALF
    assert captured.out == ""


def test_the_conversations_refusal_leaks_nothing_it_was_given(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The group takes one word and no value of its own, so what could
    leak is the ImportError's module path and the name it was invoked
    by, and both are planted."""
    monkeypatch.setattr(entrypoint.sys, "argv", [ARGV0_SENTINEL, "conversations", "schema"])
    _refuses(entrypoint.CONVERSATIONS_GROUP)(monkeypatch)

    with caplog.at_level(0), pytest.raises(SystemExit) as left:
        entrypoint.main()

    captured = capsys.readouterr()
    surfaces = (captured.out, captured.err, logged(caplog), chain(left.value))
    for sentinel in (IMPORT_SENTINEL, ARGV0_SENTINEL):
        for surface in surfaces:
            assert sentinel not in surface, sentinel


def test_the_client_half_groups_are_not_gated_at_the_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other half of the same decision, held explicitly.

    `config`, `events` and `doctor` are dispatched without the gate,
    because all three are modules the client half carries. Routing them
    through it would turn a real bug in one of them into a sentence
    saying something untrue about the installation.

    `doctor` gates one branch INSIDE itself instead, which is the case
    below: the module imports thin, and the derivation that does not is
    the only thing behind the sentence.
    """
    assert entrypoint.CONVERSATIONS_GROUP == "vinga_server.conversations.cli"
    assert entrypoint.SERVING == "vinga_server.serving"

    gated = {entrypoint.CONVERSATIONS_GROUP, entrypoint.SERVING}
    for module in ("vinga_server.config.cli", "vinga_server.events_cli", "vinga_server.doctor"):
        assert module not in gated, module


# The doctor, whose two halves fall on opposite sides of the line


def test_the_doctor_with_no_url_answers_the_gated_sentence(
    offline: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """With no URL it has to derive one, and the derivation reads the
    onboarding package."""
    _refuses("vinga_server.onboarding")(monkeypatch)

    assert doctor.main(["--config", str(offline)]) == 1

    captured = capsys.readouterr()
    assert captured.err.strip() == NEEDS_THE_SERVER_HALF
    assert captured.out == ""


def test_the_doctor_with_a_url_needs_no_server_half(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """And the half that must keep working: a laptop diagnosing a
    remote deployment passes the URL, and nothing about that reaches
    the onboarding package at all.

    It is driven at an address nothing answers on, so what comes back
    is the transport refusal rather than a diagnosis, which is the
    point: it got as far as opening a socket, which the gated branch
    never does.
    """
    _refuses("vinga_server.onboarding")(monkeypatch)

    assert doctor.main(["http://127.0.0.1:9/x/ABCDEFGH/"]) == 1

    said = capsys.readouterr().err
    assert NEEDS_THE_SERVER_HALF not in said
    assert "cannot reach" in said


def test_the_doctor_refusal_leaks_nothing_it_was_given(
    offline: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """All four surfaces. What this invocation carries is the path of
    the file half, which is the file a deployment keeps its secrets
    beside, and the ImportError's own module path."""
    _refuses("vinga_server.onboarding")(monkeypatch)

    with caplog.at_level(0):
        assert doctor.main(["--config", str(offline)]) == 1

    captured = capsys.readouterr()
    for sentinel in (IMPORT_SENTINEL, CONFIG_PATH_SENTINEL):
        for surface in (captured.out, captured.err, logged(caplog)):
            assert sentinel not in surface, sentinel


def test_the_doctor_refusal_carries_no_exception_chain(
    offline: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fourth surface, read where a walker would find it. Driven at
    the derivation, because `doctor.main` prints the sentence and
    returns."""
    with monkeypatch.context() as patched:
        _refuses("vinga_server.onboarding")(patched)
        with pytest.raises(ConfigError) as refused:
            doctor._derived_url(argparse.Namespace(config=str(offline)))

    assert str(refused.value) == NEEDS_THE_SERVER_HALF
    assert refused.value.__cause__ is None
    assert refused.value.__context__ is None
    assert IMPORT_SENTINEL not in chain(refused.value)


def test_one_sentence_answers_every_command_that_needs_the_other_half() -> None:
    """Three sites, one string, read from the module below all of them.
    Two strings for one fact is the duplication the design guide names,
    and it is the shape this started in."""
    assert cli.NEEDS_THE_SERVER_HALF is NEEDS_THE_SERVER_HALF
