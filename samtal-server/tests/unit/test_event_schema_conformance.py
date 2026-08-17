"""The registry describes the surface that exists, both ways round.

`events_schema.py` is data, and data can be wrong in two directions. It
can miss a shape the code really emits, which would make M2's strict
enforcement reject a lawful event; and it can carry a shape nothing
emits, which would be a permanent enlargement of the allowlist that no
review would ever notice. This file is what makes both impossible.

In the style of `test_event_surface_guard.py`, and for the same reason:
a grep cannot do this job. An emit site's template is written as three
implicitly concatenated literals, its fields arrive half as keywords and
half through a `**spread` whose keys are not visible at the call site,
and its level is the method name. An AST sees all of it.

Four claims, in the order they build on each other:

1. **Every emit site maps into the registry.** The walk finds every
   `events.<level>(..., event=...)` call in the package and keys
   conformance BY SOURCE CALL: each site is matched to the exact SET of
   declared variants that could have produced it (channel, method-derived
   level, byte-exact template), and its arity, its argument kinds, its
   static keywords and its spread's key inventory are checked against
   every member of that set. A set rather than a single variant because
   one call can select among shapes: `tool_call`'s classification picks
   between mutually exclusive `tool`, `entry` and neither.
2. **Every declaration is evidenced.** Every declared non-base field is
   produced by some site, as a static keyword or as a key of a named
   spread builder whose AST is parsed here rather than described here.
   Every declared token set resolves to the function or constant that
   decides it, module-qualified and crossing modules where production
   crosses modules, and the values that object can produce are compared
   with the declaration.
3. **Every path is pinned.** A machine-readable sidecar maps each site's
   stable identity, module, enclosing function and call ordinal within
   it, never a line number, to the pytest node IDs that pin it. Asserted
   equal both ways, so a new emit path for an existing event name cannot
   arrive unpinned.
4. **The registry is coherent with itself**, and the walk sees what it
   claims to see, which is what the planted-source tests at the end are
   for.

What this file does NOT prove is what M2's strict lanes will: that every
RUNTIME emission matches a variant. The walk sees static keywords, so a
field only production reaches is invisible to it. M1's claim is
calibrated to that: the registry is declared and statically conformant.
"""

import ast
import importlib
import itertools
import logging
import typing
from dataclasses import dataclass
from dataclasses import field as data_field
from functools import cache
from pathlib import Path

import pytest

import samtal_server
from samtal_server import events_schema as schema
from samtal_server.config.models import BOARD_LIMIT, CLIENT_ID_LIMIT, FIRMWARE_LIMIT
from samtal_server.events_schema import REGISTRY, SESSION_CHANNEL, ArgKind, Kind

PACKAGE = Path(samtal_server.__file__).parent
TESTS = Path(__file__).resolve().parent.parent

# The four emitter methods, and the level each of them means. The level
# is part of the compatibility surface, and it is written at the call
# site as a method name rather than as a value.
LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}

# What a session-scoped emitter is reached through. Everything else that
# answers to these method names with an `event=` keyword is a module's
# own `ServerEvents(__name__)`, whose channel is that module.
SESSION_RECEIVER = "self._events"


# --- the walk ---------------------------------------------------------


@dataclass(frozen=True)
class Site:
    """One emit call, as the source shows it."""

    module: str
    function: str
    ordinal: int
    channel: str
    level: int
    event: str
    message: str
    args: tuple[ast.expr, ...]
    static_fields: frozenset[str]
    spreads: tuple[str, ...]
    # The expression behind each keyword field, and behind each spread,
    # so a value the call spells out can be read rather than guessed.
    static_values: dict[str, ast.expr] = data_field(default_factory=dict, compare=False)
    spread_calls: tuple[ast.expr, ...] = ()

    @property
    def identity(self) -> tuple[str, str, int]:
        """Module, enclosing function, and which call in it. Deliberately
        not a line number: a line number churns with every edit above
        it, and a mapping keyed by one is a mapping nobody maintains."""
        return (self.module, self.function, self.ordinal)

    def __str__(self) -> str:
        return f"{self.module}:{self.function} #{self.ordinal} ({self.event})"


@cache
def module_source(module: str) -> str:
    return (PACKAGE.parent / f"{module.replace('.', '/')}.py").read_text(encoding="utf-8")


@cache
def module_tree(module: str) -> ast.Module:
    return ast.parse(module_source(module))


class _Walk(ast.NodeVisitor):
    def __init__(self, module: str) -> None:
        self.module = module
        self.stack: list[str] = []
        self.ordinals: dict[tuple[str, str], int] = {}
        self.found: list[Site] = []

    def visit_FunctionDef(self, node: ast.AST) -> None:
        self.stack.append(node.name)  # type: ignore[attr-defined]
        self.generic_visit(node)
        self.stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef
    visit_ClassDef = visit_FunctionDef

    def visit_Call(self, node: ast.Call) -> None:
        function = node.func
        if isinstance(function, ast.Attribute) and function.attr in LEVELS:
            named = [keyword for keyword in node.keywords if keyword.arg == "event"]
            if named:
                self.found.append(self._site(node, function, named[0]))
        self.generic_visit(node)

    def _site(self, node: ast.Call, function: ast.Attribute, named: ast.keyword) -> Site:
        enclosing = ".".join(self.stack)
        key = (self.module, enclosing)
        self.ordinals[key] = self.ordinals.get(key, 0) + 1
        receiver = ast.unparse(function.value)
        return Site(
            module=self.module,
            function=enclosing,
            ordinal=self.ordinals[key],
            channel=SESSION_CHANNEL if receiver == SESSION_RECEIVER else self.module,
            level=LEVELS[function.attr],
            event=ast.literal_eval(named.value),
            message=ast.literal_eval(node.args[0]),
            args=tuple(node.args[1:]),
            static_fields=frozenset(
                keyword.arg
                for keyword in node.keywords
                if keyword.arg is not None and keyword.arg != "event"
            ),
            spreads=tuple(
                spread_key(self.module, enclosing, keyword.value)
                for keyword in node.keywords
                if keyword.arg is None
            ),
            static_values={
                keyword.arg: keyword.value
                for keyword in node.keywords
                if keyword.arg is not None and keyword.arg != "event"
            },
            spread_calls=tuple(
                keyword.value for keyword in node.keywords if keyword.arg is None
            ),
        )


def spread_key(module: str, enclosing: str, node: ast.expr) -> str:
    """The inventory key of one `**spread`, derived from what it is.

    A call names the builder it calls; a bare name names the local the
    enclosing function assembled. Both forms end up as one
    module-qualified identity, which is what the inventory below is
    keyed by and what a reader can go and read.
    """
    if isinstance(node, ast.Call):
        called = node.func
        if isinstance(called, ast.Attribute) and ast.unparse(called.value) == "self":
            return f"{module}:{enclosing.split('.')[0]}.{called.attr}"
        if isinstance(called, ast.Name):
            return f"{module}:{called.id}"
    if isinstance(node, ast.Name):
        return f"{module}:{enclosing}.{node.id}"
    raise AssertionError(f"{module}:{enclosing}: unreadable spread {ast.unparse(node)}")


@cache
def emit_sites() -> tuple[Site, ...]:
    """Every emitter call in the package that names an event."""
    found: list[Site] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        module = str(path.relative_to(PACKAGE.parent).with_suffix("")).replace("/", ".")
        walk = _Walk(module)
        walk.visit(module_tree(module))
        found += walk.found
    return tuple(found)


def scope_of(module: str, qualname: str) -> ast.AST:
    """The definition one dotted name inside a module refers to."""
    node: ast.AST = module_tree(module)
    for part in qualname.split("."):
        children = [
            child
            for child in ast.iter_child_nodes(node)
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
            and child.name == part
        ]
        assert len(children) == 1, f"{module}:{qualname}: {part} is not one definition"
        node = children[0]
    return node


# --- the spread builders ----------------------------------------------
#
# Nine events take part of their payload from a `**spread` whose keys
# the call site does not show. What matters about a spread is not which
# keys it can produce but which SETS of them it can produce together:
# `_tool_named` answers `tool`, or `entry`, or neither, and a registry
# variant declaring both would be a shape no call can make. Flattening
# those branches into "always" and "sometimes" would have let exactly
# such a variant through, which is the PR #167 review's first finding.
#
# So each builder is read as a list of ALTERNATIVES, one complete key
# set per path through it, and the extraction below walks the builder's
# statements rather than its keys. Each entry states the alternatives it
# expects; the test asserts the walk and the entry agree, so the
# inventory is checked rather than believed.


LOCAL = "local"
RETURNS = "returns"
DELEGATES = "delegates"


def sets(*groups: str) -> tuple[frozenset[str], ...]:
    """One alternative per group, a group being its keys space
    separated. `sets("a b", "")` is "either both of them, or neither"."""
    return tuple(frozenset(group.split()) for group in groups)


@dataclass(frozen=True)
class Spread:
    """One builder, and the complete payload shapes it can produce.

    `local` names the dict the builder assembles, which is also the last
    segment of the key where the spread is a local rather than a call.
    `token_arguments` names the fields whose value the CALL supplies
    positionally, so a site's own literal can be held to the token set
    its variant declares.
    """

    how: str
    alternatives: tuple[frozenset[str], ...] = ()
    local: str = "fields"
    # DELEGATES only: the builder this local is assigned from.
    to: str = ""
    token_arguments: tuple[tuple[str, int], ...] = ()


SPREAD_INVENTORY: dict[str, Spread] = {
    # `asr_prompt_echo`: the five outcomes share three fields and differ
    # in level and sentence, so they are gathered in one builder. A skip
    # sent no retry, so it times none.
    "samtal_server.providers.openai_asr:OpenAiAsr._echo_fields": Spread(
        LOCAL,
        alternatives=sets(
            "outcome duration_s host",
            "outcome duration_s host retry_ms",
        ),
        token_arguments=(("outcome", 0),),
    ),
    # `ota_check`: the whole payload, assembled once and spread into
    # whichever of the four sentences the resolution chose. The code is
    # there exactly when an activation was offered.
    "samtal_server.ota:check_version.fields": Spread(
        LOCAL,
        alternatives=sets(
            "device client board firmware agents unloaded",
            "device client board firmware agents unloaded code",
        ),
    ),
    # `activation_refused`: what all three refusals name.
    "samtal_server.ota:_version_two.refusal": Spread(
        LOCAL, local="refusal", alternatives=sets("device code")
    ),
    # `heard`: only engines that detected carry these, and the two are
    # independent, since a provider may report a language without a
    # confidence.
    "samtal_server.runtime.pipeline:PipelineRuntime._reply.language_fields": Spread(
        LOCAL,
        local="language_fields",
        alternatives=sets(
            "",
            "language",
            "language_confidence",
            "language language_confidence",
        ),
    ),
    # `llm_retry` and `llm_round`: which configuration entry a provider
    # is. `provider` and `type` are ATOMIC, which is what the early
    # return makes them: a provider the registry did not build names
    # neither. `host` and `model` are independent of each other.
    "samtal_server.runtime.pipeline:provider_fields": Spread(
        LOCAL,
        alternatives=sets(
            "stage",
            "stage provider type",
            "stage provider type host",
            "stage provider type model",
            "stage provider type host model",
        ),
    ),
    # `llm_round`: the GenAI usage vocabulary, present where the
    # provider reported it. Three independent conditions, so eight
    # shapes.
    "samtal_server.runtime.pipeline:PipelineRuntime._llm_round_done.tokens": Spread(
        LOCAL,
        local="tokens",
        alternatives=sets(
            "",
            "input_tokens",
            "output_tokens",
            "first_token_ms",
            "input_tokens output_tokens",
            "input_tokens first_token_ms",
            "output_tokens first_token_ms",
            "input_tokens output_tokens first_token_ms",
        ),
    ),
    # `provider_failed`: the same provider identity, read once so the
    # sentence's fragments and the fields cannot disagree.
    "samtal_server.runtime.pipeline:PipelineRuntime._provider_failed.fields": Spread(
        DELEGATES, to="samtal_server.runtime.pipeline:provider_fields"
    ),
    # `tool_call`: what a call may be named by, which is only ever what
    # this application authored. Mutually exclusive by construction:
    # `tool` and `entry` are two branches of one return.
    "samtal_server.runtime.pipeline:PipelineRuntime._run_one.fields": Spread(
        DELEGATES, to="samtal_server.runtime.pipeline:_tool_named"
    ),
    "samtal_server.runtime.pipeline:_tool_named": Spread(
        RETURNS, alternatives=sets("tool", "entry", "")
    ),
    # `barge_in`: absent when the reply had not yet spoken.
    "samtal_server.runtime.pipeline:PipelineRuntime._speaking_ms_field": Spread(
        RETURNS, alternatives=sets("", "speaking_ms")
    ),
}


# Where one call reaches only some of its builder's alternatives.
#
# A builder's branches are visible in the builder; which of them a
# particular call can be reached with is not, because the condition that
# selects the branch is the same condition that selects the call. Both
# cases here are of that shape: `ota_check`'s `code` is written exactly
# when an activation was offered, which is exactly the branch that emits
# the first of the four sentences, and `_echo_fields` is handed a retry
# time by every outcome except the skip.
#
# An override may only NARROW: the test asserts each is a subset of the
# builder's own alternatives, and that the calls sharing a builder cover
# all of them between them, so a branch cannot be dropped everywhere.
CALL_ALTERNATIVES: dict[tuple[str, str, int], tuple[frozenset[str], ...]] = {
    ("samtal_server.ota", "check_version", 1): sets(
        "device client board firmware agents unloaded code"
    ),
    ("samtal_server.ota", "check_version", 2): sets(
        "device client board firmware agents unloaded"
    ),
    ("samtal_server.ota", "check_version", 3): sets(
        "device client board firmware agents unloaded"
    ),
    ("samtal_server.ota", "check_version", 4): sets(
        "device client board firmware agents unloaded"
    ),
    ("samtal_server.providers.openai_asr", "OpenAiAsr._retry_without_prompt", 1): sets(
        "outcome duration_s host"
    ),
    ("samtal_server.providers.openai_asr", "OpenAiAsr._retry_without_prompt", 2): sets(
        "outcome duration_s host retry_ms"
    ),
    ("samtal_server.providers.openai_asr", "OpenAiAsr._retry_without_prompt", 3): sets(
        "outcome duration_s host retry_ms"
    ),
    ("samtal_server.providers.openai_asr", "OpenAiAsr._retry_without_prompt", 4): sets(
        "outcome duration_s host retry_ms"
    ),
    ("samtal_server.providers.openai_asr", "OpenAiAsr._retry_without_prompt", 5): sets(
        "outcome duration_s host retry_ms"
    ),
}


def _targets(node: ast.stmt) -> list[ast.expr]:
    if isinstance(node, ast.Assign):
        return list(node.targets)
    if isinstance(node, ast.AnnAssign):
        return [node.target]
    return []


def _walk_body(
    body: list[ast.stmt], name: str, states: set[frozenset[str]]
) -> tuple[set[frozenset[str]], set[frozenset[str]]]:
    """Run one block over the shapes a named local dict can have.

    Answers the shapes that fall out of the block's end and the shapes
    that left it by `return`. A dict literal CREATES the shapes, a
    subscript assignment adds a key to every shape there is, and a
    branch is the union of the shapes on each side of it. States start
    empty and stay empty until a literal is assigned, so a builder whose
    dict is assembled inside a conditional does not pick up a phantom
    shape from the path where it was never built.
    """
    returned: set[frozenset[str]] = set()
    for node in body:
        for target in _targets(node):
            if (
                isinstance(target, ast.Name)
                and target.id == name
                and isinstance(node.value, ast.Dict)  # type: ignore[union-attr]
            ):
                states = {
                    frozenset(ast.literal_eval(key) for key in node.value.keys)  # type: ignore[union-attr]
                }
            if (
                isinstance(target, ast.Subscript)
                and isinstance(target.value, ast.Name)
                and target.value.id == name
            ):
                added = ast.literal_eval(target.slice)
                states = {held | {added} for held in states}
        if isinstance(node, ast.If | ast.For | ast.While | ast.AsyncFor):
            taken, left = _walk_body(node.body, name, states)
            returned |= left
            if isinstance(node, ast.If) and node.orelse:
                untaken, left = _walk_body(node.orelse, name, states)
                returned |= left
            else:
                # A loop may run no times, and an `if` with no `else`
                # may not fire, so the shapes before it survive it.
                untaken = states
            states = taken | untaken
        elif isinstance(node, ast.Try):
            for block in (node.body, node.orelse, node.finalbody):
                taken, left = _walk_body(block, name, states)
                returned |= left
                states = states | taken
            for handler in node.handlers:
                taken, left = _walk_body(handler.body, name, states)
                returned |= left
                states = states | taken
        elif isinstance(node, ast.With | ast.AsyncWith):
            states, left = _walk_body(node.body, name, states)
            returned |= left
        elif isinstance(node, ast.Return):
            returned |= states
            states = set()
    return states, returned


def local_dict_alternatives(scope: ast.AST, name: str) -> tuple[frozenset[str], ...]:
    """Every complete shape one named local dict can have."""
    body = list(getattr(scope, "body", []))
    states, returned = _walk_body(body, name, set())
    return tuple(sorted(states | returned, key=sorted))


def returned_dict_alternatives(scope: ast.AST) -> tuple[frozenset[str], ...]:
    """Every shape a builder's returned dict literals can have, one per
    return rather than one per key."""
    shapes: list[frozenset[str]] = []
    for node in ast.walk(scope):
        if isinstance(node, ast.Return) and node.value is not None:
            for inner in ast.walk(node.value):
                if isinstance(inner, ast.Dict):
                    shapes.append(frozenset(ast.literal_eval(key) for key in inner.keys))
    assert shapes, "the builder returns no dict literal at all"
    return tuple(sorted(set(shapes), key=sorted))


def delegated_to(module: str, qualname: str, local: str) -> str:
    """The builder a local is assigned from, read off the assignment."""
    scope = scope_of(module, qualname)
    for node in ast.walk(scope):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        names = [
            target
            for element in node.targets
            for target in (
                element.elts if isinstance(element, ast.Tuple) else [element]
            )
            if isinstance(target, ast.Name) and target.id == local
        ]
        if names and isinstance(node.value.func, ast.Name):
            return f"{module}:{node.value.func.id}"
    raise AssertionError(f"{module}:{qualname}: {local} is assigned from no call")


def assembling_scope(qualname: str, local: str) -> str:
    """The function that assembles the dict, which is the key's qualname
    with the local's own name taken off where the key carries it."""
    return qualname.removesuffix(f".{local}")


@cache
def spread_alternatives(key: str) -> tuple[frozenset[str], ...]:
    """What one inventory entry's builder really produces, parsed."""
    entry = SPREAD_INVENTORY[key]
    module, qualname = key.split(":")
    if entry.how == DELEGATES:
        owner = assembling_scope(qualname, entry.local)
        assert delegated_to(module, owner, entry.local) == entry.to, key
        return spread_alternatives(entry.to)
    if entry.how == RETURNS:
        return returned_dict_alternatives(scope_of(module, qualname))
    scope = scope_of(module, assembling_scope(qualname, entry.local))
    return local_dict_alternatives(scope, entry.local)


# --- what the code says a value is ------------------------------------
#
# A field name and an arity say nothing about a KIND. Without this
# section, flipping `ota_check.board` from DESCRIPTOR to IDENTIFIER,
# swapping an ID's syntax, or making a nullable field non-nullable would
# all pass, which is the PR #167 review's second finding: coherence
# asked only that an ID name SOME syntax and a descriptor carry SOME
# bounds.
#
# So the producing expression is read. `bounded_descriptor(board,
# BOARD_LIMIT)` is a descriptor of exactly that length and nothing else;
# `normalize_mac(...)` is an id in the MAC form; `type(exc).__name__` is
# a class name; `len(...)` is a count; `X or None` is nullable. The
# classifier speaks where the source lets it and stays silent where it
# does not, and the counts below are pinned so that silence cannot
# spread unnoticed.


@dataclass(frozen=True)
class Signature:
    """What one producing expression says about the value it makes.

    `kinds` is a set of admissible kind names rather than one, because
    the source sometimes fixes the shape without fixing the kind:
    `round(x)` makes an integer, which the registry may declare as an
    INT or, where the meaning is "how many", as a COUNT.
    """

    kinds: frozenset[str]
    nullable: bool = False
    syntax: str | None = None
    max_length: int | None = None

    def widened(self, other: "Signature") -> "Signature":
        return Signature(
            self.kinds | other.kinds,
            self.nullable or other.nullable,
            self.syntax,
            self.max_length,
        )


INTEGRAL = frozenset({"int", "count"})
LISTS = frozenset({"identifier_list", "id_list"})

# How many field and argument positions across the whole surface the
# classifier can speak about. Pinned, so that a classifier which
# stopped reading would fail here rather than turn every kind check
# above into a pass over an empty set.
FIELDS_READ = 72
ARGUMENTS_READ = 49


@cache
def module_values(module: str) -> dict[str, object]:
    """Everything a module binds at its top level, so a limit written as
    a constant can be read as the number it is."""
    loaded = importlib.import_module(module)
    return {name: value for name, value in vars(loaded).items() if not name.startswith("__")}


def _assigned_in(scope: ast.AST, name: str) -> ast.expr | None:
    """The one expression a local name is assigned from, or None where
    it is assigned more than once and the answer would be a guess."""
    found = [
        node.value
        for node in ast.walk(scope)
        if isinstance(node, ast.Assign | ast.AnnAssign)
        for target in _targets(node)
        if isinstance(target, ast.Name) and target.id == name and node.value is not None
    ]
    return found[0] if len(found) == 1 else None


@cache
def defined_here(module: str) -> frozenset[str]:
    """The functions a module defines at its own top level. A name it
    merely imported (`revision`, `normalize_mac`) has its body
    somewhere else, and following it would be a different module's
    reading."""
    return frozenset(
        node.name
        for node in ast.iter_child_nodes(module_tree(module))
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    )


def _returns_of(module: str, function: str) -> list[ast.expr]:
    return [
        node.value
        for node in ast.walk(scope_of(module, function))
        if isinstance(node, ast.Return) and node.value is not None
    ]


def classify(
    node: ast.expr, module: str, scope: str, seen: frozenset[str] = frozenset()
) -> Signature | None:
    """What the source says one value is, or None where it says nothing.

    Deliberately partial. A bare attribute read (`self._agent`, a
    provider's `identity.host`) carries no evidence at all, and inventing
    one from the field's name would be the guessing this exists to
    replace.
    """
    values = module_values(module)

    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or) and len(node.values) == 2:
        left, right = node.values
        # `X or None` is X, made nullable.
        if isinstance(right, ast.Constant) and right.value is None:
            held = classify(left, module, scope, seen)
            return None if held is None else Signature(
                held.kinds, True, held.syntax, held.max_length
            )
        # `X or "a fixed word"` is X's domain plus one fixed value, which
        # the registry may carry as X's kind where the word fits it, or
        # as a composed fragment naming both alternatives.
        if isinstance(right, ast.Constant | ast.Name):
            held = classify(left, module, scope, seen)
            if held is None:
                return None
            # The fallback is what takes the None away: `X or "unknown"`
            # answers a word where X answered nothing.
            return Signature(
                held.kinds | {"composed"}, False, held.syntax, held.max_length
            )

    if isinstance(node, ast.IfExp) and isinstance(node.body, ast.Constant):
        if node.body.value is None:
            held = classify(node.orelse, module, scope, seen)
            return None if held is None else Signature(
                held.kinds, True, held.syntax, held.max_length
            )

    if isinstance(node, ast.JoinedStr):
        return Signature(frozenset({"composed"}))

    if isinstance(node, ast.Compare):
        return Signature(frozenset({"bool"}))

    if isinstance(node, ast.Constant) and isinstance(node.value, bool):
        return Signature(frozenset({"bool"}))

    if isinstance(node, ast.Call):
        called = node.func
        if isinstance(called, ast.Attribute) and called.attr == "join":
            return Signature(frozenset({"composed"}))
        if isinstance(called, ast.Attribute) and called.attr == "__name__":
            return None
        if isinstance(called, ast.Name):
            name = called.id
            if name == "len":
                return Signature(frozenset({"count"}))
            if name == "round":
                return Signature(INTEGRAL if len(node.args) == 1 else frozenset({"float"}))
            if name == "list":
                return Signature(LISTS)
            if name == "bool":
                return Signature(frozenset({"bool"}))
            if name == "normalize_mac":
                return Signature(frozenset({"id"}), syntax="mac")
            if name == "bounded_descriptor":
                limit = node.args[1]
                held = limit.value if isinstance(limit, ast.Constant) else values.get(
                    limit.id if isinstance(limit, ast.Name) else ""
                )
                assert isinstance(held, int), f"{module}: unreadable descriptor limit"
                return Signature(frozenset({"descriptor"}), max_length=held)
            # One step through a function of this module, which is what
            # `_known_device` is: a normalized MAC, or nothing.
            if name not in seen and name in defined_here(module):
                answers = [
                    classify(answer, module, scope, seen | {name})
                    for answer in _returns_of(module, name)
                ]
                if answers and any(answer is not None for answer in answers):
                    kinds: frozenset[str] = frozenset()
                    nullable = False
                    syntax = None
                    length = None
                    for answer in answers:
                        if answer is None:
                            continue
                        kinds |= answer.kinds
                        nullable = nullable or answer.nullable
                        syntax = syntax or answer.syntax
                        length = length or answer.max_length
                    nullable = nullable or any(
                        isinstance(answer, ast.Constant) and answer.value is None
                        for answer in _returns_of(module, name)
                    )
                    return Signature(kinds, nullable, syntax, length)
        return None

    if isinstance(node, ast.Attribute) and node.attr == "__name__":
        return Signature(frozenset({"class_name"}))

    if isinstance(node, ast.Name):
        assigned = _assigned_in(scope_of(module, scope), node.id)
        if assigned is not None:
            return classify(assigned, module, scope, seen)
    return None


def spread_value_expressions(key: str) -> dict[str, tuple[str, str, ast.expr]]:
    """Where each key of a spread gets its value, as (module, scope,
    expression), so a field a call site never spells out is still read
    off the code that makes it."""
    entry = SPREAD_INVENTORY[key]
    module, qualname = key.split(":")
    if entry.how == DELEGATES:
        return spread_value_expressions(entry.to)
    if entry.how == RETURNS:
        scope = qualname
        found: dict[str, tuple[str, str, ast.expr]] = {}
        for node in ast.walk(scope_of(module, qualname)):
            if isinstance(node, ast.Dict):
                for held, value in zip(node.keys, node.values, strict=True):
                    found[ast.literal_eval(held)] = (module, scope, value)
        return found
    scope = assembling_scope(qualname, entry.local)
    found = {}
    for node in ast.walk(scope_of(module, scope)):
        for target in _targets(node):
            if (
                isinstance(target, ast.Name)
                and target.id == entry.local
                and isinstance(node.value, ast.Dict)  # type: ignore[union-attr]
            ):
                for held, value in zip(
                    node.value.keys, node.value.values, strict=True  # type: ignore[union-attr]
                ):
                    found[ast.literal_eval(held)] = (module, scope, value)
            if (
                isinstance(target, ast.Subscript)
                and isinstance(target.value, ast.Name)
                and target.value.id == entry.local
            ):
                found[ast.literal_eval(target.slice)] = (module, scope, node.value)  # type: ignore[union-attr]
    return found


def field_producers(site: Site) -> dict[str, tuple[str, str, ast.expr]]:
    """Every field this call produces whose value expression the source
    shows, from its own keywords and from its spreads' builders."""
    found = {
        name: (site.module, site.function, node)
        for name, node in site.static_values.items()
    }
    for key in site.spreads:
        found.update(spread_value_expressions(key))
    return found


def agrees(signature: Signature, declared: schema.EventField | schema.ArgSpec) -> str:
    """Why a declaration and its producer disagree, or the empty string
    where they do not."""
    if declared.kind.value not in signature.kinds:
        return f"declared {declared.kind.value}, produced {sorted(signature.kinds)}"
    if signature.nullable and not declared.nullable:
        return "the producer can answer None and the declaration is not nullable"
    if signature.syntax is not None and declared.kind.value == "id":
        if declared.syntax is None or declared.syntax.name != signature.syntax:
            named = declared.syntax and declared.syntax.name
            return f"declared syntax {named}, produced {signature.syntax}"
    if signature.max_length is not None and declared.kind.value == "descriptor":
        if declared.bounds is None or declared.bounds.max_length != signature.max_length:
            return (
                f"declared bounds {declared.bounds and declared.bounds.max_length}, "
                f"produced {signature.max_length}"
            )
    return ""


# --- the token decision sites -----------------------------------------
#
# A token set is only a closed set if something closes it. Each entry
# names the function or the constant that decides one, module-qualified
# and following production across modules where it goes there:
# `activation_not_offered`'s refusal reasons are produced in
# `onboarding.py`, not in `ota.py` where the emit sits.

CONSTANT = "constant"
RETURNED = "returned"
KEYWORD = "keyword"
ARGUMENT = "argument"
ANNOTATION = "annotation"


@dataclass(frozen=True)
class Decides:
    """One decision site for one token set."""

    module: str
    how: str
    # The attribute, keyword or callee this mode reads. For KEYWORD,
    # "name" or "Callee:name"; for ARGUMENT, "Callee:index".
    what: str
    # The function or class the read is confined to, or "" for the whole
    # module.
    scope: str = ""
    # RETURNED only: which member of a returned tuple.
    position: int | None = None


TOKEN_SOURCES: dict[tuple[str, str], tuple[Decides, ...]] = {
    ("capture_failed", "reason"): (
        Decides("samtal_server.capture", ARGUMENT, "_disable:0", scope="SessionCapture"),
    ),
    ("capture_failed", "arg:1"): (
        Decides("samtal_server.capture", ARGUMENT, "_disable:0", scope="SessionCapture"),
    ),
    ("capture_declined", "reason"): (
        Decides("samtal_server.capture", KEYWORD, "reason", scope="CaptureStore.open"),
    ),
    ("session_rejected", "reason"): (
        Decides(
            "samtal_server.device.session", KEYWORD, "reason", scope="DeviceSession.run"
        ),
        Decides("samtal_server.ws", KEYWORD, "reason", scope="conversation"),
    ),
    ("session_closed", "reason"): (
        Decides("samtal_server.device.session", CONSTANT, "CLOSE_REASONS"),
    ),
    ("auth_rejected", "reason"): (
        Decides("samtal_server.ws", RETURNED, "refusal_reason"),
    ),
    ("auth_rejected", "arg:0"): (
        Decides("samtal_server.ws", RETURNED, "refusal_reason"),
    ),
    ("activation_not_offered", "reason"): (
        Decides("samtal_server.ota", KEYWORD, "reason", scope="_activation"),
        Decides(
            "samtal_server.onboarding",
            KEYWORD,
            "Offer:refused",
            scope="PendingDevices.observe",
        ),
    ),
    # The argument, unlike the field, belongs to the second variant
    # alone: the unreadable-database refusal renders no reason at all.
    ("activation_not_offered", "arg:1"): (
        Decides(
            "samtal_server.onboarding",
            KEYWORD,
            "Offer:refused",
            scope="PendingDevices.observe",
        ),
    ),
    ("activation_refused", "reason"): (
        Decides("samtal_server.ota", KEYWORD, "reason", scope="_version_two"),
    ),
    ("ota_request_rejected", "arg:0"): (
        Decides("samtal_server.ota", ARGUMENT, "_bad_request:0"),
    ),
    ("onboarding_banner", "origin_source"): (
        Decides("samtal_server.onboarding", ARGUMENT, "Origin:1", scope="public_origin"),
    ),
    ("mcp_connected", "transport"): (
        Decides("samtal_server.config.models", ANNOTATION, "McpServerConfig.transport"),
    ),
    ("mcp_down", "reason"): (
        Decides("samtal_server.tools.mcp", RETURNED, "_down_reason"),
        Decides(
            "samtal_server.tools.mcp",
            ARGUMENT,
            "reached:0",
            scope="McpServerManager",
        ),
        Decides("samtal_server.tools.mcp", CONSTANT, "STOPPED"),
        Decides("samtal_server.tools.mcp", CONSTANT, "CALL_FAILED"),
    ),
    ("mcp_reload", "outcome"): (
        Decides("samtal_server.tools.mcp", CONSTANT, "APPLIED"),
        Decides("samtal_server.tools.mcp", CONSTANT, "REFUSED"),
    ),
    ("mcp_reload", "reason"): (
        Decides("samtal_server.tools.mcp", RETURNED, "_refusal"),
        Decides("samtal_server.tools.mcp", ARGUMENT, "_refused:0", scope="McpServers"),
    ),
    ("mcp_reload", "arg:0"): (
        Decides("samtal_server.tools.mcp", RETURNED, "_refusal"),
        Decides("samtal_server.tools.mcp", ARGUMENT, "_refused:0", scope="McpServers"),
    ),
    ("barge_in_suppressed", "reason"): (
        Decides(
            "samtal_server.runtime.pipeline",
            KEYWORD,
            "reason",
            scope="PipelineRuntime._gate_barge_in",
        ),
    ),
    ("filler_skipped", "reason"): (
        Decides(
            "samtal_server.runtime.pipeline",
            KEYWORD,
            "reason",
            scope="PipelineRuntime._run_filler",
        ),
    ),
    ("asr_prompt_echo", "outcome"): (
        Decides(
            "samtal_server.providers.openai_asr",
            ARGUMENT,
            "_echo_fields:0",
            scope="OpenAiAsr._retry_without_prompt",
        ),
    ),
    ("tool_call", "source"): (
        Decides("samtal_server.runtime.turns", RETURNED, "tool_source", position=0),
    ),
    ("tool_call", "arg:1"): (
        Decides("samtal_server.runtime.turns", RETURNED, "tool_source", position=0),
    ),
}


def module_constants(module: str) -> dict[str, str]:
    """The module-level string constants a name in that module can be
    resolved through."""
    loaded = importlib.import_module(module)
    return {
        name: value
        for name, value in vars(loaded).items()
        if isinstance(value, str) and not name.startswith("__")
    }


def literal_or_constant(node: ast.expr, constants: dict[str, str]) -> str | None:
    """One expression's value, where it has one this side can know: a
    string literal, or a name bound to a module-level string."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    return None


def decided_values(source: Decides) -> frozenset[str]:
    """Every value one decision site can produce."""
    constants = module_constants(source.module)
    if source.how == CONSTANT:
        held = constants.get(source.what)
        if held is not None:
            return frozenset({held})
        loaded = importlib.import_module(source.module)
        return frozenset(getattr(loaded, source.what))
    if source.how == ANNOTATION:
        owner, _, attribute = source.what.partition(".")
        model = getattr(importlib.import_module(source.module), owner)
        return frozenset(typing.get_args(model.model_fields[attribute].annotation))

    scope = scope_of(source.module, source.scope) if source.scope else module_tree(source.module)
    found: set[str] = set()
    if source.how == RETURNED:
        function = scope_of(source.module, source.what)
        for node in ast.walk(function):
            if not isinstance(node, ast.Return) or node.value is None:
                continue
            answered = node.value
            if source.position is not None and isinstance(answered, ast.Tuple):
                answered = answered.elts[source.position]
            value = literal_or_constant(answered, constants)
            if value is not None:
                found.add(value)
        return frozenset(found)
    callee, _, selector = source.what.rpartition(":")
    for node in ast.walk(scope):
        if not isinstance(node, ast.Call):
            continue
        if callee and ast.unparse(node.func).rpartition(".")[2] != callee:
            continue
        if source.how == ARGUMENT:
            index = int(selector)
            if len(node.args) > index:
                value = literal_or_constant(node.args[index], constants)
                if value is not None:
                    found.add(value)
        else:
            for keyword in node.keywords:
                if keyword.arg == selector:
                    value = literal_or_constant(keyword.value, constants)
                    if value is not None:
                        found.add(value)
    return frozenset(found)


# --- the pin sidecar --------------------------------------------------
#
# Which test pins which emit path. Keyed by the stable identity, never
# by a line number.
#
# The two contract pin suites carry 76 expectations between them, which
# cover 73 of the 81 paths: `tool_call` has one site and four pins, one
# per classification, and `barge_in` has two sites sharing two pins. The
# five conversation-store paths are covered by the pin file M1 adds. The
# remaining three, the MCP paths the contract suites never reached, are
# covered by field-exact assertions in the MCP suites, named here rather
# than left as a silence.

SURFACE_PINS = "tests/unit/test_event_surface_pins.py"
SERVER_PINS = "tests/unit/test_server_event_pins.py"
STORE_PINS = "tests/unit/test_conversations_event_pins.py"
MCP = "tests/unit/test_tools_mcp.py"
MCP_RELOAD = "tests/unit/test_tools_mcp_reload.py"

PINNED_BY: dict[tuple[str, str, int], tuple[str, ...]] = {
    ("samtal_server.app", "create_app", 1): (f"{SERVER_PINS}::test_capture_enabled",),
    ("samtal_server.app", "create_app", 2): (f"{SERVER_PINS}::test_capture_disabled",),
    ("samtal_server.capture", "SessionCapture._disable", 1): (
        f"{SERVER_PINS}::test_capture_failed",
    ),
    ("samtal_server.capture", "SessionCapture._finish_at_limit", 1): (
        f"{SERVER_PINS}::test_capture_limit",
    ),
    ("samtal_server.capture", "CaptureStore.prune", 1): (
        f"{SERVER_PINS}::test_capture_pruned",
    ),
    ("samtal_server.capture", "CaptureStore.prune", 2): (
        f"{SERVER_PINS}::test_capture_over_budget",
    ),
    ("samtal_server.capture", "CaptureStore.open", 1): (
        f"{SERVER_PINS}::test_capture_declined_because_the_directory_is_unusable",
    ),
    ("samtal_server.capture", "CaptureStore.open", 2): (
        f"{SERVER_PINS}::test_capture_declined_because_the_volume_is_nearly_full",
    ),
    ("samtal_server.capture", "CaptureStore.open", 3): (
        f"{SERVER_PINS}::test_capture_declined_because_the_files_would_not_open",
    ),
    ("samtal_server.capture", "CaptureStore.open", 4): (
        f"{SERVER_PINS}::test_capture_started",
    ),
    ("samtal_server.config.api", "_SanitizedErrors.__call__", 1): (
        f"{SERVER_PINS}::test_api_error",
    ),
    ("samtal_server.config.api", "_refusal.handler", 1): (
        f"{SERVER_PINS}::test_api_storage_error",
    ),
    ("samtal_server.conversations.store", "ConversationStore.start", 1): (
        f"{STORE_PINS}::test_conversations_enabled",
    ),
    ("samtal_server.conversations.store", "ConversationStore.record_event", 1): (
        f"{STORE_PINS}::test_conversations_dropped",
    ),
    ("samtal_server.conversations.store", "ConversationStore._failed", 1): (
        f"{STORE_PINS}::test_conversations_failed_on_a_write",
    ),
    ("samtal_server.conversations.store", "ConversationStore._prune", 1): (
        f"{STORE_PINS}::test_conversations_failed_on_a_prune",
    ),
    ("samtal_server.conversations.store", "ConversationStore._prune", 2): (
        f"{STORE_PINS}::test_conversations_pruned",
    ),
    ("samtal_server.device.bindings", "DeviceBindings.open", 1): (
        f"{SERVER_PINS}::test_device_bindings_snapshot_only",
    ),
    ("samtal_server.device.bindings", "DeviceBindings._warn", 1): (
        f"{SERVER_PINS}::test_device_bindings_unreadable",
    ),
    ("samtal_server.device.session", "DeviceSession.run", 1): (
        f"{SURFACE_PINS}::test_session_rejected_bad_device_id",
    ),
    ("samtal_server.device.session", "DeviceSession.run", 2): (
        f"{SURFACE_PINS}::test_session_rejected_agent_not_loaded",
    ),
    ("samtal_server.device.session", "DeviceSession.run", 3): (
        f"{SURFACE_PINS}::test_session_rejected_no_agent",
    ),
    ("samtal_server.device.session", "DeviceSession.run", 4): (
        f"{SURFACE_PINS}::test_session_open",
    ),
    ("samtal_server.device.session", "DeviceSession.run", 5): (
        f"{SURFACE_PINS}::test_session_limit",
    ),
    ("samtal_server.device.session", "DeviceSession.run", 6): (
        f"{SURFACE_PINS}::test_session_closed",
    ),
    ("samtal_server.device.session", "DeviceSession._watch_for_idle", 1): (
        f"{SURFACE_PINS}::test_session_idle",
    ),
    ("samtal_server.device.session", "DeviceSession.send_audio", 1): (
        f"{SURFACE_PINS}::test_speaking_started",
    ),
    ("samtal_server.filler", "build_agent_fillers", 1): (
        f"{SERVER_PINS}::test_filler_disabled",
    ),
    ("samtal_server.onboarding", "log_banner", 1): (
        f"{SERVER_PINS}::test_onboarding_banner_with_onboarding_off",
    ),
    ("samtal_server.onboarding", "log_banner", 2): (
        f"{SERVER_PINS}::test_onboarding_banner_with_onboarding_on",
    ),
    ("samtal_server.onboarding", "_log_mismatch", 1): (
        f"{SERVER_PINS}::test_onboarding_key_mismatch",
    ),
    ("samtal_server.onboarding", "_log_mismatch", 2): (
        f"{SERVER_PINS}::test_onboarding_key_unshaped",
    ),
    ("samtal_server.ota", "check_version", 1): (
        f"{SERVER_PINS}::test_ota_check_offering_an_activation_code",
    ),
    ("samtal_server.ota", "check_version", 2): (
        f"{SERVER_PINS}::test_ota_check_naming_an_agent_this_server_never_loaded",
    ),
    ("samtal_server.ota", "check_version", 3): (
        f"{SERVER_PINS}::test_ota_check_with_no_agent_at_all",
    ),
    ("samtal_server.ota", "check_version", 4): (
        f"{SERVER_PINS}::test_ota_check_resolving_to_an_agent",
    ),
    ("samtal_server.ota", "_activation", 1): (
        f"{SERVER_PINS}::test_activation_not_offered_because_the_database_could_not_be_read",
    ),
    ("samtal_server.ota", "_activation", 2): (
        f"{SERVER_PINS}::test_activation_not_offered_because_the_mint_budget_is_spent",
    ),
    ("samtal_server.ota", "activate", 1): (f"{SERVER_PINS}::test_activation_complete",),
    ("samtal_server.ota", "activate", 2): (f"{SERVER_PINS}::test_activation_pending",),
    ("samtal_server.ota", "_version_two", 1): (
        f"{SERVER_PINS}::test_activation_refused_by_an_unreadable_body",
    ),
    ("samtal_server.ota", "_version_two", 2): (
        f"{SERVER_PINS}::test_activation_refused_by_an_unknown_algorithm",
    ),
    ("samtal_server.ota", "_version_two", 3): (
        f"{SERVER_PINS}::test_activation_refused_by_a_challenge_mismatch",
    ),
    ("samtal_server.ota", "_bad_request", 1): (
        f"{SERVER_PINS}::test_ota_request_rejected",
    ),
    ("samtal_server.providers.openai_asr", "OpenAiAsr._retry_without_prompt", 1): (
        f"{SERVER_PINS}::test_asr_prompt_echo_skipped",
    ),
    ("samtal_server.providers.openai_asr", "OpenAiAsr._retry_without_prompt", 2): (
        f"{SERVER_PINS}::test_asr_prompt_echo_timed_out",
    ),
    ("samtal_server.providers.openai_asr", "OpenAiAsr._retry_without_prompt", 3): (
        f"{SERVER_PINS}::test_asr_prompt_echo_confirmed_echo",
    ),
    ("samtal_server.providers.openai_asr", "OpenAiAsr._retry_without_prompt", 4): (
        f"{SERVER_PINS}::test_asr_prompt_echo_confirmed_empty",
    ),
    ("samtal_server.providers.openai_asr", "OpenAiAsr._retry_without_prompt", 5): (
        f"{SERVER_PINS}::test_asr_prompt_echo_recovered",
    ),
    ("samtal_server.registry", "SessionRegistry.drain", 1): (
        f"{SERVER_PINS}::test_drain_started",
    ),
    ("samtal_server.registry", "SessionRegistry.drain", 2): (
        f"{SERVER_PINS}::test_drain_incomplete",
    ),
    ("samtal_server.registry", "SessionRegistry.drain", 3): (
        f"{SERVER_PINS}::test_drain_finished",
    ),
    ("samtal_server.runtime.pipeline", "PipelineRuntime._watchdog_stream", 1): (
        f"{SURFACE_PINS}::test_llm_retry",
    ),
    ("samtal_server.runtime.pipeline", "PipelineRuntime._llm_round_done", 1): (
        f"{SURFACE_PINS}::test_llm_round",
    ),
    ("samtal_server.runtime.pipeline", "PipelineRuntime._provider_failed", 1): (
        f"{SURFACE_PINS}::test_provider_failed",
    ),
    ("samtal_server.runtime.pipeline", "PipelineRuntime._prompt_assembled", 1): (
        f"{SURFACE_PINS}::test_prompt_assembled",
    ),
    ("samtal_server.runtime.pipeline", "PipelineRuntime._reply", 1): (
        f"{SURFACE_PINS}::test_heard",
    ),
    ("samtal_server.runtime.pipeline", "PipelineRuntime._reply", 2): (
        f"{SURFACE_PINS}::test_replied",
    ),
    ("samtal_server.runtime.pipeline", "PipelineRuntime._speak_reply", 1): (
        f"{SURFACE_PINS}::test_agent_said_and_handover",
    ),
    ("samtal_server.runtime.pipeline", "PipelineRuntime._speak_reply", 2): (
        f"{SURFACE_PINS}::test_agent_said_and_handover",
    ),
    ("samtal_server.runtime.pipeline", "PipelineRuntime._run_one", 1): (
        f"{SURFACE_PINS}::test_tool_call_for_a_builtin",
        f"{SURFACE_PINS}::test_tool_call_for_a_device_tool",
        f"{SURFACE_PINS}::test_tool_call_for_a_name_nobody_publishes",
        f"{SURFACE_PINS}::test_tool_call_for_an_mcp_tool",
    ),
    ("samtal_server.runtime.pipeline", "PipelineRuntime._finish_utterance", 1): (
        f"{SURFACE_PINS}::test_barge_in_confirmed_by_a_transcript",
        f"{SURFACE_PINS}::test_barge_in_on_a_manual_stop",
    ),
    ("samtal_server.runtime.pipeline", "PipelineRuntime._gate_barge_in", 1): (
        f"{SURFACE_PINS}::test_barge_in_suppressed_under_the_speech_floor",
    ),
    ("samtal_server.runtime.pipeline", "PipelineRuntime._gate_barge_in", 2): (
        f"{SURFACE_PINS}::test_barge_in_merged_mid_transcription",
    ),
    ("samtal_server.runtime.pipeline", "PipelineRuntime._gate_barge_in", 3): (
        f"{SURFACE_PINS}::test_barge_in_suppressed_inside_the_refractory_window",
    ),
    ("samtal_server.runtime.pipeline", "PipelineRuntime._gate_barge_in", 4): (
        f"{SURFACE_PINS}::test_barge_in_suppressed_with_nothing_transcribed",
    ),
    ("samtal_server.runtime.pipeline", "PipelineRuntime._gate_barge_in", 5): (
        f"{SURFACE_PINS}::test_barge_in_confirmed_by_a_transcript",
        f"{SURFACE_PINS}::test_barge_in_on_a_manual_stop",
    ),
    ("samtal_server.runtime.pipeline", "PipelineRuntime._run_filler", 1): (
        f"{SURFACE_PINS}::test_filler_skipped_for_a_user_still_speaking",
    ),
    ("samtal_server.runtime.pipeline", "PipelineRuntime._run_filler", 2): (
        f"{SURFACE_PINS}::test_filler_skipped_while_a_barge_in_is_confirmed",
    ),
    ("samtal_server.runtime.pipeline", "PipelineRuntime._run_filler", 3): (
        f"{SURFACE_PINS}::test_filler_played",
    ),
    ("samtal_server.tools.mcp", "McpServerManager._run", 1): (
        f"{SERVER_PINS}::test_mcp_connected",
    ),
    ("samtal_server.tools.mcp", "McpServerManager._run", 2): (
        f"{SERVER_PINS}::test_mcp_down",
    ),
    ("samtal_server.tools.mcp", "McpServerManager._run", 3): (
        f"{MCP}::test_a_server_stopped_on_purpose_is_down_at_info_with_no_duration",
    ),
    ("samtal_server.tools.mcp", "McpServerManager._mark_down", 1): (
        f"{SERVER_PINS}::test_mcp_call_dropped",
    ),
    ("samtal_server.tools.mcp", "McpServerManager._mark_down", 2): (
        f"{MCP}::test_a_failed_call_drops_the_call_and_then_the_connection",
    ),
    ("samtal_server.tools.mcp", "McpServers._reachable", 1): (
        f"{SERVER_PINS}::test_mcp_tool_shadowed",
    ),
    ("samtal_server.tools.mcp", "McpServers._refused", 1): (
        f"{MCP_RELOAD}::test_a_refused_reload_says_which_kind_of_refusal_it_was",
    ),
    ("samtal_server.tools.mcp", "McpServers._apply", 1): (
        f"{SERVER_PINS}::test_mcp_reload",
    ),
    ("samtal_server.tools.memory", "MemoryStore.read", 1): (
        f"{SERVER_PINS}::test_memory_unreadable",
    ),
    ("samtal_server.ws", "conversation", 1): (f"{SERVER_PINS}::test_auth_rejected",),
    ("samtal_server.ws", "conversation", 2): (
        f"{SERVER_PINS}::test_session_rejected_at_capacity",
    ),
}


# --- what a variant and a site each say -------------------------------


def base_of(channel: str) -> frozenset[str]:
    return frozenset(schema.SESSION_BASE if channel == SESSION_CHANNEL else schema.SERVER_BASE)


def alternatives(site: Site) -> tuple[schema.EventVariant, ...]:
    """The declared variants that could have produced this site: same
    channel, same level, same template, byte for byte."""
    spec = REGISTRY.get(site.event)
    assert spec is not None, f"{site}: the registry declares no such event"
    return tuple(
        variant
        for variant in spec.variants
        if variant.channel == site.channel
        and variant.level == site.level
        and variant.message == site.message
    )


def reachable(site: Site, key: str) -> tuple[frozenset[str], ...]:
    """The alternatives one spread can produce AT THIS CALL, which is
    its builder's own set unless the call is one the inventory narrows.
    """
    return CALL_ALTERNATIVES.get(site.identity, spread_alternatives(key))


def produced_by(site: Site) -> set[frozenset[str]]:
    """Every complete payload shape one call can produce, beside the
    base fields: its static keywords, times each alternative of each
    spread it carries."""
    shapes = {frozenset(site.static_fields)}
    for key in site.spreads:
        assert key in SPREAD_INVENTORY, f"{site}: no inventory entry for {key}"
        shapes = {
            held | branch for held in shapes for branch in reachable(site, key)
        }
    return shapes


def expansions(variant: schema.EventVariant) -> set[frozenset[str]]:
    """Every complete payload shape one variant admits, beside the base
    fields: its required fields, times every subset of its optional
    ones.

    This is what makes the comparison with a call's alternatives an
    equality rather than a containment. A variant with a field the call
    cannot produce has an expansion nothing matches; a call with a shape
    no variant admits has one nothing matches either."""
    base = base_of(variant.channel)
    own = {name for name in variant.fields if name not in base}
    required = frozenset(name for name in own if variant.fields[name].required)
    optional = sorted(own - required)
    return {
        required | frozenset(chosen)
        for size in range(len(optional) + 1)
        for chosen in itertools.combinations(optional, size)
    }


def admitted_by(site: Site) -> set[frozenset[str]]:
    """Every shape the registry says this call may produce."""
    return {shape for variant in alternatives(site) for shape in expansions(variant)}


# --- 1: every emit site maps into the registry ------------------------


def test_the_walk_finds_the_whole_surface() -> None:
    """The inventory's own size, so a site that stops being found is a
    failure rather than a smaller silent pass."""
    sites = emit_sites()

    assert len(sites) == 81
    assert len({site.event for site in sites}) == 57
    assert len({site.identity for site in sites}) == len(sites)


@pytest.mark.parametrize("site", emit_sites(), ids=str)
def test_every_emit_site_matches_declared_variants(site: Site) -> None:
    """Keyed by source call, and by SET EQUALITY rather than by
    containment.

    The shapes this call can produce and the shapes the registry admits
    for it are the same set. Containment in either direction alone would
    miss half of it: a variant carrying two mutually exclusive fields
    would be contained in the union of everything the spreads can say,
    and a call branch nothing declares would be contained in nothing at
    all. Equality is what forbids a shape no call can make."""
    matched = alternatives(site)
    assert matched, (
        f"{site}: no declared variant on {site.channel} at level {site.level} "
        f"with this template"
    )

    for variant in matched:
        assert len(variant.args) == len(site.args), f"{site}: arity"
    assert produced_by(site) == admitted_by(site), f"{site}: payload shapes"


@pytest.mark.parametrize("site", emit_sites(), ids=str)
def test_every_token_a_site_writes_into_a_field_is_declared(site: Site) -> None:
    """A singleton token set is only pinned to its variant if something
    reads the value the site writes. Without this, two variants of one
    event could swap their reason tokens and every other check would
    stay green, since the union across variants would not move."""
    constants = module_constants(site.module)
    for variant in alternatives(site):
        for name, written in static_field_literals(site, constants).items():
            declared = variant.fields.get(name)
            if declared is None or declared.kind is not Kind.TOKEN:
                continue
            assert written in (declared.tokens or frozenset()), (
                f"{site}: {name}={written!r} is not in this variant's token set"
            )
        for name, written in spread_token_literals(site, constants).items():
            declared = variant.fields.get(name)
            assert declared is not None and declared.kind is Kind.TOKEN, f"{site}: {name}"
            assert written in (declared.tokens or frozenset()), (
                f"{site}: {name}={written!r} is not in this variant's token set"
            )


def static_field_literals(site: Site, constants: dict[str, str]) -> dict[str, str]:
    """The keyword fields whose value the call spells out."""
    written: dict[str, str] = {}
    for name, node in site.static_values.items():
        value = literal_or_constant(node, constants)
        if value is not None:
            written[name] = value
    return written


def spread_token_literals(site: Site, constants: dict[str, str]) -> dict[str, str]:
    """The fields a spread builder takes from its call's own arguments,
    read off that call. `_echo_fields("skipped", ...)` is where an
    `asr_prompt_echo` outcome is really decided."""
    written: dict[str, str] = {}
    for key, node in zip(site.spreads, site.spread_calls, strict=True):
        for name, position in SPREAD_INVENTORY[key].token_arguments:
            if not isinstance(node, ast.Call) or len(node.args) <= position:
                continue
            value = literal_or_constant(node.args[position], constants)
            if value is not None:
                written[name] = value
    return written


@pytest.mark.parametrize("site", emit_sites(), ids=str)
def test_every_field_kind_agrees_with_what_produces_it(site: Site) -> None:
    """The kind, the nullability, an ID's syntax and a DESCRIPTOR's
    bounds, each against the expression that makes the value.

    `bounded_descriptor(board, BOARD_LIMIT)` is a descriptor of exactly
    that length; calling it an identifier, or bounding it at some other
    number, is a claim the code refutes."""
    for name, (module, scope, node) in field_producers(site).items():
        signature = classify(node, module, scope)
        if signature is None:
            continue
        for variant in alternatives(site):
            declared = variant.fields.get(name)
            if declared is None:
                continue
            assert not agrees(signature, declared), f"{site}: {name}: {agrees(signature, declared)}"


@pytest.mark.parametrize("site", emit_sites(), ids=str)
def test_every_argument_kind_agrees_with_what_produces_it(site: Site) -> None:
    """The same reading for the sentence's arguments, which reach every
    tap and the formatter alike."""
    for variant in alternatives(site):
        for position, declared in enumerate(variant.args):
            signature = classify(site.args[position], site.module, site.function)
            if signature is None:
                continue
            assert not agrees(signature, declared), (
                f"{site}: argument {position}: {agrees(signature, declared)}"
            )


def test_the_classifier_still_reads_what_it_used_to_read() -> None:
    """A classifier that quietly stopped classifying would turn every
    check above into a pass. These are the counts it reaches today, so a
    fall is a failure rather than a silence."""
    fields = sum(
        1
        for site in emit_sites()
        for module, scope, node in field_producers(site).values()
        if classify(node, module, scope) is not None
    )
    arguments = sum(
        1
        for site in emit_sites()
        for node in site.args
        if classify(node, site.module, site.function) is not None
    )

    assert (fields, arguments) == (FIELDS_READ, ARGUMENTS_READ)


def test_the_classifier_reads_the_four_descriptors_and_their_bounds() -> None:
    """Named rather than counted, because these four are the whole of
    what the ADR's amendment admits and the whole of what a wrong bound
    would mangle."""
    read = {}
    for site in emit_sites():
        for name, (module, scope, node) in field_producers(site).items():
            signature = classify(node, module, scope)
            if signature is not None and "descriptor" in signature.kinds:
                read[(site.event, name)] = (signature.max_length, signature.nullable)

    assert read == {
        ("ota_check", "board"): (BOARD_LIMIT, False),
        ("ota_check", "firmware"): (FIRMWARE_LIMIT, False),
        ("ota_check", "client"): (CLIENT_ID_LIMIT, True),
        ("session_open", "client"): (CLIENT_ID_LIMIT, True),
    }


@pytest.mark.parametrize("site", emit_sites(), ids=str)
def test_every_token_argument_a_site_writes_out_is_declared(site: Site) -> None:
    """Where an argument is a literal or a two-branch conditional, the
    values are visible here and are held to the declared set."""
    for variant in alternatives(site):
        for position, spec in enumerate(variant.args):
            if spec.kind is not ArgKind.TOKEN:
                continue
            written = literal_arguments(site.args[position])
            assert written <= (spec.tokens or frozenset()), (
                f"{site}: argument {position} writes {written - (spec.tokens or set())}"
            )


def literal_arguments(node: ast.expr) -> frozenset[str]:
    """The string values one argument expression can be, where the
    source shows them: a literal, or either branch of a conditional
    between two literals."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return frozenset({node.value})
    if isinstance(node, ast.IfExp):
        return literal_arguments(node.body) | literal_arguments(node.orelse)
    return frozenset()


def test_every_declared_variant_is_produced_by_a_site() -> None:
    """The other direction, which containment alone would miss: a
    variant nothing emits is a permanent enlargement of the allowlist.

    `schema_violation` is exempt by name, the way the `extra=` guard
    exempts `events.py`: it has no ordinary emit site by construction,
    because the forgiving recovery is what produces it."""
    produced = {
        (variant.channel, variant.level, variant.message)
        for site in emit_sites()
        for variant in alternatives(site)
    }
    orphans = [
        f"{name} on {variant.channel}: {variant.message[:60]}"
        for name, spec in REGISTRY.items()
        if not spec.internal
        for variant in spec.variants
        if (variant.channel, variant.level, variant.message) not in produced
    ]

    assert orphans == []


def test_no_ordinary_emit_site_produces_the_internal_event() -> None:
    """The recovery event is the emitter's own word, never a caller's."""
    assert schema.INTERNAL_EVENTS == {schema.SCHEMA_VIOLATION}
    assert not [site for site in emit_sites() if site.event in schema.INTERNAL_EVENTS]


def test_every_module_that_emits_owns_the_channel_it_emits_on() -> None:
    """A server event is emitted through that module's own
    `ServerEvents(__name__)`, so an event emitted from the wrong module
    fails even with lawful fields."""
    for site in emit_sites():
        if site.channel == SESSION_CHANNEL:
            continue
        built = [
            node
            for node in ast.walk(module_tree(site.module))
            if isinstance(node, ast.Assign)
            and ast.unparse(node.value) == "ServerEvents(__name__)"
        ]
        assert built, f"{site}: emits on its own channel without building an emitter"
        assert site.channel in schema.SERVER_CHANNELS


def test_the_declared_channels_are_the_channels_that_exist() -> None:
    emitting = {site.channel for site in emit_sites()}

    assert emitting == set(schema.CHANNELS)
    assert set(schema.SERVER_CHANNELS) == emitting - {SESSION_CHANNEL}


# --- 2: every declaration is evidenced --------------------------------


def test_every_spread_inventory_entry_matches_its_builder() -> None:
    """The inventory is parsed out of the builder rather than described
    beside it, so a builder that gains a branch fails here."""
    parsed = {
        key: set(spread_alternatives(key))
        for key, entry in SPREAD_INVENTORY.items()
        if entry.how != DELEGATES
    }
    declared = {
        key: set(entry.alternatives)
        for key, entry in SPREAD_INVENTORY.items()
        if entry.how != DELEGATES
    }

    assert parsed == declared


def test_a_narrowed_call_only_ever_narrows() -> None:
    """An override says which of a builder's branches one call can be
    reached with. It may not invent a shape the builder cannot make,
    and the calls that share a builder have to cover every branch of it
    between them, or a branch would be declared nowhere."""
    covered: dict[str, set[frozenset[str]]] = {}
    for site in emit_sites():
        for key in site.spreads:
            branches = set(spread_alternatives(key))
            narrowed = set(reachable(site, key))
            assert narrowed <= branches, f"{site}: {key} narrowed to a shape it cannot make"
            covered.setdefault(key, set()).update(narrowed)

    assert covered == {key: set(spread_alternatives(key)) for key in covered}


def test_the_narrowings_are_the_two_the_source_hides() -> None:
    """Both overrides exist because the condition selecting the branch
    is the condition selecting the call, which no reading of the builder
    alone can see. A third would want the same explanation."""
    assert {identity[:2] for identity in CALL_ALTERNATIVES} == {
        ("samtal_server.ota", "check_version"),
        ("samtal_server.providers.openai_asr", "OpenAiAsr._retry_without_prompt"),
    }
    assert set(CALL_ALTERNATIVES) <= {site.identity for site in emit_sites()}


def test_the_inventory_names_every_spread_the_surface_uses() -> None:
    used = {key for site in emit_sites() for key in site.spreads}

    assert used <= set(SPREAD_INVENTORY)
    # Nine events take part of their payload from a spread.
    assert len({site.event for site in emit_sites() if site.spreads}) == 9


def test_every_declared_field_is_produced_somewhere() -> None:
    """Two-way coverage. Containment alone would let a surplus declared
    field sit unused for ever."""
    evidenced: set[str] = set()
    for site in emit_sites():
        for shape in produced_by(site):
            evidenced |= shape

    declared: set[str] = set()
    for spec in REGISTRY.values():
        for variant in spec.variants:
            declared |= set(variant.fields) - base_of(variant.channel)

    assert declared == evidenced


def declared_tokens(event: str, label: str) -> frozenset[str]:
    """Every token one event's field or argument position may carry,
    across all its variants."""
    held: set[str] = set()
    for variant in REGISTRY[event].variants:
        if label.startswith("arg:"):
            position = int(label.removeprefix("arg:"))
            if position < len(variant.args) and variant.args[position].kind is ArgKind.TOKEN:
                held |= variant.args[position].tokens or frozenset()
        else:
            spec = variant.fields.get(label)
            if spec is not None and spec.kind is Kind.TOKEN:
                held |= spec.tokens or frozenset()
    return frozenset(held)


@pytest.mark.parametrize("key", sorted(TOKEN_SOURCES), ids=lambda key: f"{key[0]}.{key[1]}")
def test_every_token_set_matches_its_decision_site(key: tuple[str, str]) -> None:
    """The declared set against the values the named function or
    constant can really produce. Resolved and compared, so an unrelated
    literal or a docstring cannot satisfy the check."""
    produced: frozenset[str] = frozenset()
    for source in TOKEN_SOURCES[key]:
        produced |= decided_values(source)

    assert produced == declared_tokens(*key)


def test_every_token_field_names_its_decision_site() -> None:
    """A closed set is only closed if something closes it, so a TOKEN
    field with no decision site is a gap rather than a shortcut."""
    missing = [
        (name, held)
        for name, spec in REGISTRY.items()
        if not spec.internal
        for variant in spec.variants
        for held, declared in variant.fields.items()
        if declared.kind is Kind.TOKEN and (name, held) not in TOKEN_SOURCES
    ]

    assert missing == []


def test_every_token_argument_names_a_decision_site_or_writes_itself_out() -> None:
    """The same rule for the argument positions: either the sites spell
    the values out, in which case the walk compares them, or the set
    names where it is decided."""
    written: dict[tuple[str, int], set[str]] = {}
    for site in emit_sites():
        for variant in alternatives(site):
            for position, spec in enumerate(variant.args):
                if spec.kind is ArgKind.TOKEN:
                    seen = written.setdefault((site.event, position), set())
                    seen |= literal_arguments(site.args[position])

    unevidenced = [
        (event, position)
        for (event, position), values in written.items()
        if values != declared_tokens(event, f"arg:{position}")
        and (event, f"arg:{position}") not in TOKEN_SOURCES
    ]

    assert unevidenced == []


# --- 3: every path is pinned ------------------------------------------


@cache
def defined_tests(relative: str) -> frozenset[str]:
    """Every test function one file defines, read from its source rather
    than by importing it."""
    tree = ast.parse((TESTS.parent / relative).read_text(encoding="utf-8"))
    return frozenset(
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name.startswith("test_")
    )


def test_the_sidecar_covers_exactly_the_paths_that_exist() -> None:
    """Both ways round. A new emit path for an existing event name is
    exactly the case a name-based comparison would miss, and it is the
    case this catches."""
    walked = {site.identity for site in emit_sites()}

    assert set(PINNED_BY) == walked


def test_every_path_names_at_least_one_pin() -> None:
    assert [identity for identity, pins in PINNED_BY.items() if not pins] == []


def test_every_pin_the_sidecar_names_exists() -> None:
    """A node ID that has been renamed away is a pin nobody is running,
    which reads exactly like a pin nobody wrote."""
    missing = [
        node
        for pins in PINNED_BY.values()
        for node in pins
        if node.split("::")[1] not in defined_tests(node.split("::")[0])
    ]

    assert missing == []


def test_the_two_contract_files_carry_the_pins_they_are_credited_with() -> None:
    """76 expectations across the two files, covering 73 of the 81
    paths: `tool_call` has four pins on one site and `barge_in` two pins
    across two sites."""
    from_contracts = {
        identity
        for identity, pins in PINNED_BY.items()
        if all(node.startswith((SURFACE_PINS, SERVER_PINS)) for node in pins)
    }
    from_store = {
        identity
        for identity, pins in PINNED_BY.items()
        if all(node.startswith(STORE_PINS) for node in pins)
    }

    assert len(from_contracts) == 73
    assert len(from_store) == 5
    assert len(PINNED_BY) - len(from_contracts) - len(from_store) == 3


# --- 4: the registry is coherent with itself --------------------------


def test_only_token_kinds_carry_token_sets() -> None:
    wrong = [
        (name, held)
        for name, spec in REGISTRY.items()
        for variant in spec.variants
        for held, declared in variant.fields.items()
        if (declared.tokens is not None) != (declared.kind is Kind.TOKEN)
        or (declared.kind is Kind.TOKEN and not declared.tokens)
    ]

    assert wrong == []


def test_only_token_arguments_carry_token_sets() -> None:
    wrong = [
        (name, position)
        for name, spec in REGISTRY.items()
        for variant in spec.variants
        for position, declared in enumerate(variant.args)
        if (declared.tokens is not None) != (declared.kind is ArgKind.TOKEN)
        or (declared.kind is ArgKind.TOKEN and not declared.tokens)
    ]

    assert wrong == []


def test_every_event_has_a_variant_and_every_variant_a_known_channel() -> None:
    for name, spec in REGISTRY.items():
        assert spec.name == name
        assert spec.variants, f"{name}: declared with no variant"
        assert spec.channels <= set(schema.CHANNELS), name
        assert spec.levels <= set(LEVELS.values()), name


def test_every_variant_declares_the_base_fields_its_channel_requires() -> None:
    for name, spec in REGISTRY.items():
        for variant in spec.variants:
            base = (
                schema.SESSION_BASE
                if variant.channel == SESSION_CHANNEL
                else schema.SERVER_BASE
            )
            for held, declared in base.items():
                assert variant.fields.get(held) == declared, f"{name}: base field {held}"


def test_every_id_field_and_argument_names_a_syntax() -> None:
    for name, spec in REGISTRY.items():
        for variant in spec.variants:
            for held, declared in variant.fields.items():
                if declared.kind in (Kind.ID, Kind.ID_LIST):
                    assert declared.syntax is not None, f"{name}.{held}"
                    assert schema.SYNTAXES[declared.syntax.name] is declared.syntax
            for position, declared in enumerate(variant.args):
                if declared.kind is ArgKind.ID:
                    assert declared.syntax is not None, f"{name} argument {position}"


def test_every_descriptor_carries_explicit_bounds() -> None:
    for name, spec in REGISTRY.items():
        for variant in spec.variants:
            for held, declared in variant.fields.items():
                if declared.kind is Kind.DESCRIPTOR:
                    assert declared.bounds is not None, f"{name}.{held}"
                    assert declared.bounds.charset == "printable"
                    assert declared.bounds.max_length > 0
            for position, declared in enumerate(variant.args):
                if declared.kind is ArgKind.DESCRIPTOR:
                    assert declared.bounds is not None, f"{name} argument {position}"


def test_the_declared_descriptor_bounds_are_the_decision_sites_bounds() -> None:
    """The registry restates the limits because it imports the standard
    library and nothing else. Restated is not the same as duplicated
    only if something holds the two statements equal."""
    assert schema.BOARD_BOUNDS.max_length == BOARD_LIMIT
    assert schema.FIRMWARE_BOUNDS.max_length == FIRMWARE_LIMIT
    assert schema.CLIENT_BOUNDS.max_length == CLIENT_ID_LIMIT


def test_every_composed_argument_names_a_grammar_and_its_builder() -> None:
    for name, spec in REGISTRY.items():
        for variant in spec.variants:
            for position, declared in enumerate(variant.args):
                if declared.kind is not ArgKind.COMPOSED:
                    continue
                grammar = declared.grammar
                assert grammar is not None, f"{name} argument {position}"
                assert schema.GRAMMARS[grammar.name] is grammar
                assert grammar.builders, f"{grammar.name}: names no builder"
                for builder in grammar.builders:
                    module, _, qualname = builder.partition(":")
                    scope_of(module, qualname)


def test_the_source_provenance_grammar_is_the_know_how_halfs() -> None:
    """`prompt_assembled` reports the cached know-how half and excludes
    the per-round memory read, so `memory` is a violation here like any
    unknown prefix, even though it is a provenance token elsewhere in
    the prompt assembly."""
    from samtal_server.runtime import prompt

    matcher = schema.matcher(schema.SOURCE_KEY_PATTERN)
    assert matcher.match(prompt.PERSONA)
    assert matcher.match(prompt.fragment_provenance("house-style"))
    assert matcher.match(prompt.instructions_provenance("home"))
    assert matcher.match(prompt.server_instructions_provenance("home"))
    assert matcher.match(prompt.server_prompt_provenance("home", 2))
    assert not matcher.match(prompt.MEMORY)
    assert not matcher.match("invented:home")


def test_the_counts_every_document_repeats() -> None:
    """57 production-source events plus one internal recovery event."""
    assert len(schema.PRODUCTION_EVENTS) == 57
    assert len(schema.INTERNAL_EVENTS) == 1
    assert len(REGISTRY) == 58
    # One variant per channel for the recovery event, which is every
    # channel this server speaks on.
    assert len(REGISTRY[schema.SCHEMA_VIOLATION].variants) == len(schema.CHANNELS) == 14


# --- 5: the walk sees the shapes it claims to see ---------------------


def planted_scope(source: str) -> ast.AST:
    """One planted definition, as `scope_of` would answer with it: the
    definition itself rather than the module around it."""
    return ast.parse(source).body[0]


def planted(source: str) -> list[Site]:
    walk = _Walk("planted")
    walk.visit(ast.parse(source))
    return walk.found


def test_the_walk_reads_a_concatenated_template_and_a_method_level() -> None:
    """The two shapes a grep loses: a template written as several
    implicitly concatenated literals, and a level that is a method
    name."""
    (site,) = planted(
        "def handler():\n"
        "    events.warning(\n"
        "        'one '\n"
        "        'two',\n"
        "        held,\n"
        "        event='planted',\n"
        "        reason='why',\n"
        "    )\n"
    )

    assert site.message == "one two"
    assert site.level == logging.WARNING
    assert site.static_fields == {"reason"}
    assert len(site.args) == 1
    assert site.identity == ("planted", "handler", 1)


def test_the_walk_tells_the_two_scopes_apart() -> None:
    server, session = planted(
        "class Runtime:\n"
        "    def run(self):\n"
        "        events.info('a', event='one')\n"
        "        self._events.info('b', event='two')\n"
    )

    assert (server.channel, server.identity[2]) == ("planted", 1)
    assert (session.channel, session.identity[2]) == (SESSION_CHANNEL, 2)


def test_the_walk_numbers_calls_within_their_enclosing_function() -> None:
    """The ordinal is the identity's third part, and it has to be per
    function rather than per module, or moving one call would renumber
    another function's."""
    sites = planted(
        "def first():\n"
        "    events.info('a', event='one')\n"
        "    events.info('b', event='two')\n"
        "def second():\n"
        "    events.info('c', event='three')\n"
    )

    assert [site.identity for site in sites] == [
        ("planted", "first", 1),
        ("planted", "first", 2),
        ("planted", "second", 1),
    ]


def test_the_walk_reads_both_spellings_of_a_spread() -> None:
    called, local = planted(
        "class Runtime:\n"
        "    def run(self):\n"
        "        events.info('a', event='one', **self._fields('x'))\n"
        "        events.info('b', event='two', **assembled)\n"
    )

    assert called.spreads == ("planted:Runtime._fields",)
    assert local.spreads == ("planted:Runtime.run.assembled",)


def test_the_walk_leaves_a_logging_call_that_names_no_event_alone() -> None:
    """`logger.info(...)` without an `event=` is an ordinary diagnostic
    line, not an event, and the registry has nothing to say about it."""
    assert planted("def handler():\n    logger.info('plain', 1)\n") == []


def test_the_branch_walk_keeps_a_conditional_key_out_of_the_other_shape() -> None:
    """The rule the spread inventory rests on: a subscript assignment
    inside a branch makes a SECOND shape rather than an optional key on
    the only one."""
    scope = planted_scope(
        "def build():\n"
        "    fields = {'always': 1}\n"
        "    if something:\n"
        "        fields['sometimes'] = 2\n"
        "    return fields\n"
    )

    assert set(local_dict_alternatives(scope, "fields")) == {
        frozenset({"always"}),
        frozenset({"always", "sometimes"}),
    }


def test_the_branch_walk_keeps_two_conditions_independent() -> None:
    """Two conditions nothing correlates make four shapes, which is
    what `language_fields` and the token counts really are."""
    scope = planted_scope(
        "def build():\n"
        "    held = {}\n"
        "    if one:\n"
        "        held['a'] = 1\n"
        "    if two:\n"
        "        held['b'] = 2\n"
    )

    assert len(set(local_dict_alternatives(scope, "held"))) == 4


def test_the_branch_walk_makes_an_early_return_a_shape_of_its_own() -> None:
    """What makes `provider` and `type` atomic: the path that returns
    before them carries neither, and no path carries one without the
    other."""
    scope = planted_scope(
        "def build():\n"
        "    fields = {'stage': 1}\n"
        "    if identity is None:\n"
        "        return fields\n"
        "    fields['provider'] = 2\n"
        "    fields['type'] = 3\n"
        "    return fields\n"
    )

    assert set(local_dict_alternatives(scope, "fields")) == {
        frozenset({"stage"}),
        frozenset({"stage", "provider", "type"}),
    }


def test_the_branch_walk_builds_no_shape_before_the_dict_exists() -> None:
    """A builder whose dict is assembled inside a conditional has the
    shapes of that conditional and no phantom empty one from the path
    where it was never built."""
    scope = planted_scope(
        "def build():\n"
        "    if transcript:\n"
        "        held = {}\n"
        "        if one:\n"
        "            held['a'] = 1\n"
        "        emit(**held)\n"
    )

    assert set(local_dict_alternatives(scope, "held")) == {
        frozenset(),
        frozenset({"a"}),
    }


def test_the_return_extraction_keeps_the_branches_apart() -> None:
    """One shape per return, not one set of keys across them: `tool` and
    `entry` are two answers, never one answer carrying both."""
    scope = planted_scope(
        "def build(kind):\n"
        "    if kind:\n"
        "        return {'shared': 1, 'one': 2}\n"
        "    return {'shared': 1}\n"
    )

    assert set(returned_dict_alternatives(scope)) == {
        frozenset({"shared", "one"}),
        frozenset({"shared"}),
    }


def test_the_expansion_of_a_variant_is_its_optional_powerset() -> None:
    """The other half of the equality: a variant with two optional
    fields admits four shapes, which is exactly what `heard` is."""
    variant = REGISTRY["heard"].variants[0]

    assert expansions(variant) == {
        frozenset({"agent", "duration_s"}),
        frozenset({"agent", "duration_s", "language"}),
        frozenset({"agent", "duration_s", "language_confidence"}),
        frozenset({"agent", "duration_s", "language", "language_confidence"}),
    }


# --- 6: a lawful configured name is lawful on the events --------------
#
# `NonBlankStr` is `StringConstraints(strip_whitespace=True,
# min_length=1)` and nothing else, so an agent called `secondary"agent`,
# one carrying a control character, and one a thousand characters long
# are all valid configuration today. The registry may not claim a
# tighter domain than that: M2 enforces these grammars, and a claim
# configuration never made would drop the field and replace the sentence
# for a deployment that did nothing wrong.
#
# The PR #167 review's third finding, and the reason the grammars are
# bounded by structure rather than by character class or length.

LAWFUL_NAMES = (
    pytest.param('secondary"agent', id="quoted"),
    pytest.param("second\x07ary\nagent", id="control-bearing"),
    pytest.param("a" * 4000, id="overlong"),
)


@pytest.mark.parametrize("name", LAWFUL_NAMES)
def test_a_lawful_configured_name_passes_the_configuration(name: str) -> None:
    """The premise the rest of this section rests on. If the
    configuration refused these, the grammars could refuse them too."""
    from pydantic import TypeAdapter

    from samtal_server.config.models import NonBlankStr

    assert TypeAdapter(NonBlankStr).validate_python(name) == name


@pytest.mark.parametrize("name", LAWFUL_NAMES)
def test_a_lawful_configured_name_rides_every_composed_grammar(name: str) -> None:
    """Each fragment as its own builder assembles it, against the
    grammar the registry declares for it."""
    fragments = {
        schema.ALSO_BOUND_TO: f" (also bound to {', '.join([name, name])})",
        schema.AGENT_LIST: ", ".join([name, name]),
        schema.QUOTED_TOOL_NAME: f' "{name}"',
        schema.FROM_ENTRY: f' from entry "{name}"',
        schema.QUOTED_PROVIDER: f' "{name}"',
        schema.REACHING_HOST: f" reaching {name}",
        schema.ORIGIN_PROVENANCE: f"from {name}",
    }

    refused = [
        grammar.name
        for grammar, fragment in fragments.items()
        if not schema.matcher(grammar.pattern).match(fragment)
    ]

    assert refused == []


@pytest.mark.parametrize("name", LAWFUL_NAMES)
def test_a_lawful_configured_name_is_a_lawful_identifier_field(name: str) -> None:
    """And the fields themselves. An IDENTIFIER carries the
    configuration's domain, so the only thing it refuses is emptiness."""
    assert schema.IDENTIFIER_DOMAIN
    assert name.strip()
    fields = {
        (event, held)
        for event, spec in REGISTRY.items()
        for variant in spec.variants
        for held, declared in variant.fields.items()
        if declared.kind is Kind.IDENTIFIER
    }

    # No IDENTIFIER field declares a syntax or a bound, which is what
    # would otherwise refuse this name once M2 enforces.
    claiming = [
        (event, held)
        for event, held in fields
        for spec in [REGISTRY[event]]
        for variant in spec.variants
        for name_, declared in variant.fields.items()
        if name_ == held
        and declared.kind is Kind.IDENTIFIER
        and (declared.syntax is not None or declared.bounds is not None)
    ]

    assert claiming == []


def test_no_composed_grammar_claims_a_length_or_a_character_class() -> None:
    """The rule, stated as a rule rather than as seven examples. A
    grammar over configured names bounds by structure; the two that
    bound by class are over values this server mints itself."""
    minted = {
        schema.EMPTY_FRAGMENT.name,
        schema.SESSION_LIST.name,
        schema.DEVICE_OR_UNIDENTIFIED.name,
    }
    claiming = [
        grammar.name
        for grammar in schema.GRAMMARS.values()
        if grammar.name not in minted and ("{1," in grammar.pattern or "\\x00" in grammar.pattern)
    ]

    assert claiming == []
