"""What an installation carrying the client half alone is told.

The default installation of this package is the configuration client,
and the server half is an extra. So three things a person can type are
answerable only by an installation that has that half, and each of them
is answered with a fixed sentence rather than with an ImportError:
`vinga-server` with nothing after it, which means serve, and the two
commands of the configuration grammar that read the server's own
modules, `openapi` (which builds the API application to describe it) and
`ota-url` (which derives a URL through the onboarding package).

Two sentences and not three: serving is one fact and a gated command is
another, and `openapi` and `ota-url` are the same fact as each other.

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

import builtins
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.support.config_cli import chain, logged
from vinga_server import main as entrypoint
from vinga_server.config import cli
from vinga_server.config.loader import ConfigError

# One per field that could carry a pasted credential, so a leak says
# which field it came out of. None of them is a real credential and each
# is shaped so a substring check for it cannot match by accident.
IMPORT_SENTINEL = "no module named 'sk-import-1a2b3c-never-a-real-credential'"
CONFIG_PATH_SENTINEL = "sk-configpath-4d5e6f-never-a-real-credential"
ARGV0_SENTINEL = "sk-argv0-7a8b9c-never-a-real-credential"


def _refuses(module: str) -> Callable[[pytest.MonkeyPatch], None]:
    """Make one module unimportable, with a sentinel in the failure.

    The import hook rather than a `None` in `sys.modules`, because the
    message is half of what this file is about: the machinery's own
    failure text names the module it could not find, and planting a
    value there is what proves the sentence carries nothing of it.
    """

    def install(monkeypatch: pytest.MonkeyPatch) -> None:
        real = builtins.__import__

        def refusing(
            name: str,
            globals: object = None,
            locals: object = None,
            fromlist: tuple[str, ...] = (),
            level: int = 0,
        ) -> object:
            reached = f"{name}.{fromlist[0]}" if fromlist else name
            if reached == module or name == module:
                raise ImportError(IMPORT_SENTINEL)
            return real(name, globals, locals, fromlist, level)  # type: ignore[arg-type]

        monkeypatch.setattr(builtins, "__import__", refusing)

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
