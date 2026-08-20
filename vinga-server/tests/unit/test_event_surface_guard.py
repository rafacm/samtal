"""One emitter, and no way back to a hand-built event.

Milestone 2 of #138 moved every structured `extra={...}` dict in the
production package onto `SessionEvents` or `ServerEvents`. What keeps it
moved is this file rather than a habit: the next module that wants to
record an outcome has to reach for an emitter, because a logging call
carrying its own `extra=` fails here.

A grep cannot do this job, which is why the plan's review round replaced
one with these tests. `extra={` spans several lines at most of the sites
that were migrated, `extra={**record, ...}` spreads a dict rather than
opening a brace after the equals sign, and either quoting style hides
from a pattern written for the other. An AST sees all three as the same
keyword argument.

Two deliberate limits, stated rather than discovered later:

- Only calls whose function is an attribute with a logging method's name
  are examined (`logger.info(...)`, `self._channel.log(...)`). A call
  made through a variable holding a bound method would pass. Nothing in
  the package does that, and widening the rule to every call with an
  `extra=` keyword would catch pydantic's `ConfigDict(extra="forbid")`,
  which is a different word with a different meaning.
- The scan is of the production package. Tests build records by hand on
  purpose, which is how several of them plant the field sets they check.

The two historical guards that used to sit beside this rule are gone
(#210). `_echo_event` was a provider's own payload factory, invented
because there was no server-scoped emitter to ask, and `device.events`
was where the emitter used to live; both named a shape of code that
milestone 1 of #138 removed, and a guard whose subject is what an old
release looked like is a fact about that release rather than an
invariant. The rule below is the invariant, and it is enforced against
the tree as it is.
"""

import ast
from pathlib import Path

import vinga_server

# The package under the rule, and the one file inside it that is allowed
# to break it.
PACKAGE = Path(vinga_server.__file__).parent

# `events/__init__.py` is where the surface is emitted from: `LogTap`
# attaches the finished payload as `extra=` on the channel the emitter
# was built for, which is precisely the call every other site used to
# make by hand. It is the exception the rule exists to concentrate
# everything into, and the only one. The path is the package's
# `__init__` rather than a module because the emitter became a package
# without changing what it is; the exemption follows the file.
EXEMPT = {str(Path("events") / "__init__.py")}

# The `logging.Logger` methods that take a record's `extra=`.
LOGGING_METHODS = frozenset(
    {"debug", "info", "warning", "warn", "error", "exception", "critical", "fatal", "log"}
)


def sources() -> list[Path]:
    return sorted(PACKAGE.rglob("*.py"))


def relative(path: Path) -> str:
    return str(path.relative_to(PACKAGE))


def hand_built_events(tree: ast.AST) -> list[int]:
    """The line of every logging call in `tree` that carries its own
    `extra=` keyword."""
    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in LOGGING_METHODS:
            continue
        if any(keyword.arg == "extra" for keyword in node.keywords):
            lines.append(node.lineno)
    return lines


def test_no_production_logging_call_builds_its_own_extra() -> None:
    """The rule. An outcome worth recording is emitted through an
    emitter, so that every consumer of the event surface sees it: a
    hand-built `extra=` reaches the JSON log and nothing else, which is
    exactly the half-move #138 exists to end."""
    offenders = {
        relative(path): hand_built_events(ast.parse(path.read_text(encoding="utf-8")))
        for path in sources()
        if relative(path) not in EXEMPT
    }

    assert {name: lines for name, lines in offenders.items() if lines} == {}


def test_the_rule_would_notice_a_call_that_broke_it() -> None:
    """A guard nobody has seen fail is a guard nobody has seen. Both
    shapes that evaded the grep this replaces are planted here."""
    planted = ast.parse(
        "logger.info('one', extra={'event': 'x'})\n"
        "self._logger.warning(\n"
        "    'two',\n"
        "    extra={\n"
        "        'event': 'y',\n"
        "    },\n"
        ")\n"
        "logger.debug('three', extra={**record, 'reason': 'z'})\n"
    )

    assert hand_built_events(planted) == [1, 2, 8]


def test_the_rule_leaves_everything_that_is_not_a_logging_call_alone() -> None:
    """`extra` is pydantic's word too, and a model that forbids unknown
    keys is not an event."""
    planted = ast.parse(
        "model_config = ConfigDict(extra='forbid')\n"
        "field = Field(json_schema_extra={'minItems': 1})\n"
        "route = api.post(path, openapi_extra=_request_body(Body))\n"
    )

    assert hand_built_events(planted) == []
