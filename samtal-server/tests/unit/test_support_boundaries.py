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

A `conftest` is deliberately not a test module here. The integration and
smoke lanes import their own, which is the other half of decision 2's
"support or a conftest", and a fixture cannot be imported and used as a
fixture, so a conftest is the only home some shared things can have.
"""

import ast
from pathlib import Path

# The lane under the rule, and the half of it that everything else sits
# on.
TESTS = Path(__file__).parents[1]
SUPPORT = TESTS / "support"


def sources() -> list[Path]:
    return sorted(TESTS.rglob("*.py"))


def relative(path: Path) -> str:
    return str(path.relative_to(TESTS))


def is_test_module(path: Path) -> bool:
    return path.name.startswith("test_")


def imported_paths(tree: ast.AST) -> list[tuple[str, int]]:
    """Every module path an import statement in `tree` names, with the
    line it is on.

    `from pkg import name` contributes `pkg` and `pkg.name`, because the
    name may itself be a module: both spellings of reaching into a test
    module have to be caught.
    """
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [(alias.name, node.lineno) for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            module = "." * node.level + (node.module or "")
            found.append((module, node.lineno))
            found += [(f"{module}.{alias.name}", node.lineno) for alias in node.names]
    return found


def names_a_test_module(path: str) -> bool:
    """Whether a dotted import path reaches a test module. A `conftest`
    does not count: decision 2 allows one."""
    return any(part.startswith("test_") for part in path.split("."))


def reaches_into_tests(tree: ast.AST) -> list[int]:
    """The line of every import in `tree` that names a test module."""
    return sorted({line for path, line in imported_paths(tree) if names_a_test_module(path)})


def offenders(paths: list[Path]) -> dict[str, list[int]]:
    found = {
        relative(path): reaches_into_tests(ast.parse(path.read_text(encoding="utf-8")))
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
        "from samtal_server.config import Config\n"
    )

    assert reaches_into_tests(planted) == []
