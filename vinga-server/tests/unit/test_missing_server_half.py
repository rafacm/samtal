"""What an installation carrying the client half alone is told.

The default installation of this package is the configuration client,
and the server half is an extra. So three things a person can type are
answerable only by an installation that has that half, and each of them
is answered with a fixed sentence rather than with an ImportError:
`vinga-server` with nothing after it, which means serve, and the two
commands of the configuration grammar that read the server's own
modules, `openapi` and `ota-url`, which the commit after this one gates.

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

import pytest

from tests.support.config_cli import chain, logged
from vinga_server import main as entrypoint

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
