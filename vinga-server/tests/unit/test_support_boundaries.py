"""The dependency arrow between test files points one way.

Issue #144's second decision is that test files stop importing helpers
from other test files, and that what is shared moves to "support or a
conftest". The web this replaced was 84 import statements across 32
files: a helper's real definition was wherever somebody had first needed
it, and reading one suite meant opening three others. Moving them is
this issue's work; keeping them moved is this file's, because a helper
imported from a neighbour costs nothing at the moment it is written and
everything a year later.

Two rules, both read from the syntax rather than from a grep:

- no test module imports another test module, at module level or from
  inside a function or a class body (the web included a function-level
  `from tests.unit.test_ota import SYSTEM_INFO`, invisible to any check
  that looked only at the top of a file);
- nothing under `tests/support` imports a test module, so support is
  something the suites sit on rather than something they are tangled
  with.

What makes something a test module is resolved against this repository,
not guessed from the spelling of an import. `from pkg import name` may
be importing a submodule or may be importing a symbol, and the two are
the same syntax: `from tests.unit import test_capture` reaches a module,
while `from tests.support.configs import test_data as data` reaches a
name inside one. So every candidate path is looked up on disk, and only
a candidate that is really a `test_*.py` file, or a `test_*` package
directory, counts. A `test_`-prefixed symbol out of a module that is not
itself a test module is a name, and names are not this rule's business.

A `conftest` is deliberately not a test module here. The integration and
smoke lanes import their own, which is the other half of decision 2's
"support or a conftest", and a fixture cannot be imported and used as a
fixture, so a conftest is the only home some shared things can have.

Two deliberate limits, stated rather than discovered later:

- An import naming a module this repository does not hold is not
  reported. It cannot be a test-module import, and it already fails
  louder than this file would: collection raises `ImportError` on it.
- Only static imports are seen. A module reached through `importlib` or
  `__import__` would pass, and nothing in the lane does that.
"""

import ast
from pathlib import Path

# The lane under the rule, the half of it that everything else sits on,
# and the directory dotted import paths are resolved from: `tests.unit`
# is a package because `vinga-server/tests/unit` is a directory under
# this one, which is what puts the two spellings on the same footing.
TESTS = Path(__file__).parents[1]
SUPPORT = TESTS / "support"
ROOT = TESTS.parent

# Where a planted source is treated as living, for the tests below. Only
# its directory is read, and only to anchor a relative import, so the
# file itself does not have to exist.
PLANTED = TESTS / "unit" / "planted.py"

# Which test module may import which other one: nothing, today. The
# parameter below exists because the rule has had an exception once and
# will again, and a rule whose exceptions are edits to its own logic is
# a rule nobody can read.
TRANSITIONAL: dict[str, frozenset[str]] = {}


def sources() -> list[Path]:
    return sorted(TESTS.rglob("*.py"))


def relative(path: Path) -> str:
    return str(path.relative_to(TESTS))


def is_test_module(path: Path) -> bool:
    """Whether a resolved file or directory is tests rather than
    something tests are built from."""
    return path.name.startswith("test_")


def resolved(base: Path) -> Path | None:
    """The module file or the package directory a candidate path names,
    if this repository holds one."""
    module = base.with_suffix(".py")
    if module.is_file():
        return module
    if base.is_dir():
        return base
    return None


def candidates(node: ast.Import | ast.ImportFrom, origin: Path) -> list[Path]:
    """Every path in the tree an import statement could be naming.

    `from pkg import name` names `pkg`, and may also name `pkg.name`
    when that is a module of its own, so both are offered here and the
    filesystem decides which of them exists. A relative import is
    anchored at the importing file's own directory, one level further up
    per extra dot.
    """
    if isinstance(node, ast.Import):
        return [ROOT.joinpath(*alias.name.split(".")) for alias in node.names]
    anchor = ROOT
    if node.level:
        anchor = origin.parent
        for _ in range(node.level - 1):
            anchor = anchor.parent
    base = anchor.joinpath(*node.module.split(".")) if node.module else anchor
    return [base, *(base / alias.name for alias in node.names)]


def reaches_into_tests(
    tree: ast.AST, origin: Path = PLANTED, allowed: frozenset[str] = frozenset()
) -> list[int]:
    """The line of every import in `tree` that names a test module the
    importer is not exempted for."""
    lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Import | ast.ImportFrom):
            continue
        for base in candidates(node, origin):
            found = resolved(base)
            if found is None or not is_test_module(found):
                continue
            if relative(found) in allowed:
                continue
            lines.add(node.lineno)
    return sorted(lines)


def offenders(paths: list[Path]) -> dict[str, list[int]]:
    found = {
        relative(path): reaches_into_tests(
            ast.parse(path.read_text(encoding="utf-8")),
            path,
            TRANSITIONAL.get(relative(path), frozenset()),
        )
        for path in paths
    }
    return {name: lines for name, lines in found.items() if lines}


def test_no_test_module_imports_another_test_module() -> None:
    """The rule. A helper two suites need is a helper that belongs to
    neither, and the moment one suite owns it the other one's imports
    stop saying where anything lives."""
    assert offenders([path for path in sources() if is_test_module(path)]) == {}


def test_nothing_under_support_imports_a_test_module() -> None:
    """The arrow points one way. Support that read from a test module
    would make the lane a cycle: the suites would sit on support and
    support would sit back on the suites."""
    assert offenders(sorted(SUPPORT.rglob("*.py"))) == {}


def test_the_rule_sees_an_import_hidden_inside_a_function() -> None:
    """A guard nobody has seen fail is a guard nobody has seen. The
    function-level import is the shape that mattered: it is what the web
    used where a module-level one would have been a cycle."""
    planted = ast.parse(
        "def test_something() -> None:\n"
        "    from tests.unit.test_ota import SYSTEM_INFO\n"
        "\n"
        "\n"
        "class Thing:\n"
        "    import tests.unit.test_session\n"
        "\n"
        "\n"
        "from tests.unit import test_capture\n"
    )

    assert reaches_into_tests(planted) == [2, 6, 9]


def test_the_rule_leaves_support_and_conftest_imports_alone() -> None:
    """The two homes decision 2 names, plus the production package,
    which is what a test is supposed to be importing in the first
    place."""
    planted = ast.parse(
        "from tests.support.configs import base_config\n"
        "from tests.integration.conftest import server_at\n"
        "from tests.smoke.conftest import DEVICE\n"
        "from vinga_server.config import Config\n"
    )

    assert reaches_into_tests(planted) == []


def test_the_rule_leaves_a_test_shaped_name_alone() -> None:
    """A name is not a module. `from pkg import test_thing` is the same
    syntax whether `test_thing` is a submodule or a symbol, and reading
    the spelling alone would refuse a support helper, or a dependency's
    export, for being called something that starts with `test_`."""
    planted = ast.parse(
        "from tests.support.configs import test_data as data\n"
        "from tests.support.stores import tone as test_tone\n"
        "from vendor import test_helper as helper\n"
        "from vinga_server.config import test_only_flag\n"
    )

    assert reaches_into_tests(planted) == []


def test_the_rule_follows_a_relative_import_to_the_file_it_names() -> None:
    """A relative import is the obvious way around a rule written about
    dotted spellings, so it is resolved from the importing file's own
    directory: the first and third lines below reach a test module in
    `tests/unit`, and the second reaches support one level up."""
    planted = ast.parse(
        "from .test_ota import SYSTEM_INFO\n"
        "from ..support.configs import base_config\n"
        "from . import test_capture\n"
    )

    assert reaches_into_tests(planted) == [1, 3]
