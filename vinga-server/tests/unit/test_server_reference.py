"""The generated server-half reference, and the selector that prints it.

The page is rendered from `ServerConfig`, so what is worth pinning is
everything a byte-for-byte diff of the committed copy cannot see: that
the page is complete against the models rather than merely equal to
itself, that every rule the server enforces is stated where an operator
meets the key it is about, and that rendering it still needs no
database, no configuration file and no key.

Completeness is checked per section rather than page-wide, which is the
one thing this suite does differently from the domain one. The server
models repeat `enabled`, `port` and `max_session_s` across sections, so
a row found somewhere on the page says nothing about the section it was
supposed to be in, and a whole missing section could hide behind another
section's rows.
"""

import re
from pathlib import Path

import annotated_types
import pytest
from pydantic import BaseModel, ValidationError

from tests.support.config_cli import SECRET, chain
from tests.support.isolation import ALLOWED_IMPORTS, imported_alone
from vinga_server.config import cli, docgen, server_reference
from vinga_server.config.models import (
    API_MOUNT_PATH,
    BOOT_REFUSALS,
    DATABASE_ENV_NAMES,
    DATABASE_ENV_PREFIX,
    DATABASE_GENERIC_ENV_PREFIX,
    DATABASE_PASSWORD_ENV,
    DATABASE_URL_ENV,
    HEALTH_PATH,
    LOG_LEVELS,
    ONBOARDING_MOUNT_PATH,
    READY_PATH,
    SERVER_ENV_PREFIX,
    EnvName,
    ServerConfig,
)
from vinga_server.config.secrets import MASTER_KEY_ENV

REFERENCE = Path(__file__).resolve().parents[3] / "docs" / "reference"
COMMITTED = REFERENCE / "server-config.md"
COMMITTED_DOMAIN = REFERENCE / "domain-config.md"

# The regenerate command in the spelling the failure message uses, which
# is the long one: that is a command to paste into a checkout rather than
# a line of a rendered page.
REGENERATE = (
    "uv run vinga-server config reference server > ../docs/reference/server-config.md"
)


@pytest.fixture
def run(monkeypatch: pytest.MonkeyPatch):
    """This command reads the models and nothing else, so the fixture
    takes away everything else: no config file, no reachable database, no
    encryption key."""
    monkeypatch.delenv("VINGA_CONFIG", raising=False)
    monkeypatch.delenv(MASTER_KEY_ENV, raising=False)
    # A port nothing listens on, so a command that opened the database
    # would refuse here rather than print.
    monkeypatch.setenv("VINGA_DB_PORT", "1")

    def _run(*argv: str) -> int:
        return cli.main(list(argv))

    return _run


# The model graph, walked the way the renderer walks it
#
# Every expectation below is derived from this rather than from a list
# written beside it: a section that stopped being rendered, a field that
# was added to a model, or a nested section that was never reached would
# each have to be edited into a hand-written inventory to keep it
# passing, and would fail this one instead.


def walked(
    model: type[BaseModel] = ServerConfig, path: str = server_reference.SERVER
) -> list[tuple[str, type[BaseModel]]]:
    """Every model reachable from `ServerConfig`, at the path a key of it
    is written at, in the order the page renders them."""
    found = [(path, model)]
    for name, info in model.model_fields.items():
        nested = docgen.nested_model(info.annotation)
        if nested is not None:
            found += walked(nested, f"{path}.{name}")
    return found


# One rendered row, split into the five cells the header names. The type
# and default cells are code spans holding no backtick of their own,
# which is what lets the description, which holds several, be whatever
# is left.
_ROW = re.compile(
    r"^\| `(?P<key>[^`]+)` \| `(?P<type>[^`]*)` \| `(?P<default>[^`]*)` \| "
    r"(?P<constraints>.*?) \| (?P<description>.*) \|$"
)


def sections(page: str) -> dict[str, str]:
    """The page's model sections, by the path each documents.

    A model section's heading is the path in a code span, which is what
    separates it from the prose sections around it: a reader meets
    `## Environment overrides` and `## Refused at boot` on the same page
    and neither is a model.
    """
    found: dict[str, list[str]] = {}
    current: str | None = None
    for line in page.splitlines():
        if line.startswith("## "):
            heading = line.removeprefix("## ")
            current = heading[1:-1] if heading.startswith("`") and heading.endswith("`") else None
            if current is not None:
                found[current] = []
            continue
        if current is not None:
            found[current].append(line)
    return {path: "\n".join(lines) for path, lines in found.items()}


def rows(section: str) -> dict[str, re.Match[str]]:
    """One section's table rows, by field name, in the order they are
    rendered."""
    matched = [_ROW.match(line) for line in section.splitlines() if line.startswith("| `")]
    assert all(matched), "a table row this suite cannot read is a row it cannot check"
    return {found["key"]: found for found in matched if found is not None}


# The four bounds pydantic can hold on a field, and the attribute each
# keeps its number under. Read independently of the renderer's own
# table, so the two are two readings of annotated-types rather than one
# assertion about itself.
_BOUNDS = (
    (annotated_types.Ge, "ge"),
    (annotated_types.Gt, "gt"),
    (annotated_types.Le, "le"),
    (annotated_types.Lt, "lt"),
)


def bounds(info) -> list[object]:
    """The numbers pydantic enforces on one field, which are what the
    Constraints column has to carry."""
    return [
        getattr(item, attribute)
        for kind, attribute in _BOUNDS
        for item in info.metadata
        if isinstance(item, kind)
    ]


# Rendering from the models alone


_ALONE = "\n".join(
    (
        "import vinga_server.config.server_reference as server_reference",
        "",
        "rendered = len(server_reference.reference())",
    )
)


def test_the_server_reference_renders_from_the_models_alone() -> None:
    """The page comes out of a child interpreter that has imported
    nothing else, which is the claim the module docstring makes about its
    import graph.

    `server_reference` directly rather than through `config.cli`:
    importing that reaches `store.py` and so SQLAlchemy and cryptography,
    which is recorded in `docgen.py`'s own docstring. What the command
    does about a database is the case below, which is about behavior
    rather than about an import list.
    """
    alone = imported_alone(_ALONE)

    assert frozenset(alone["loaded"]) == ALLOWED_IMPORTS | {
        "vinga_server.config.server_reference"
    }
    assert alone["heavy"] == []
    assert alone["rendered"] > 0


def test_reference_server_needs_no_database_and_no_key(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The port the fixture names has nothing on it and no key is set, so
    a command that opened the database or loaded the keys would fail here
    rather than print."""
    assert run("reference", "server") == 0

    assert capsys.readouterr().out.startswith("# Server configuration reference")


def test_the_reference_is_deterministic() -> None:
    """The committed page is diffed byte for byte, so anything that
    varied between two runs would turn the lane red on an unrelated
    change."""
    assert server_reference.reference() == server_reference.reference()


# The selector
#
# The bare verb rendered the domain page before there was a second half,
# and every committed line that runs it has to stay true. The proof that
# it did not move is the sequencing rather than these assertions: the
# commit that added the selector touched neither `docgen.reference()` nor
# the committed domain page, so the domain suite's freshness pin passing
# there is what says the bytes are the same bytes.


def test_the_bare_verb_still_renders_the_domain_half(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("reference") == 0
    bare = capsys.readouterr().out

    assert run("reference", "domain") == 0
    named = capsys.readouterr().out

    assert bare == named == docgen.reference()
    assert bare == COMMITTED_DOMAIN.read_text(encoding="utf-8")


def test_a_half_that_is_neither_names_the_two_that_are(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """One fixed sentence, exit 1, and nothing of what was typed.

    A selector is an argument like any other, so it is a place a
    credential gets pasted; the planted value stands in for one and is
    asserted absent from both streams and from the exception chain, which
    is the surface no assertion about a stream can reach.
    """
    assert run("reference", SECRET) == 1

    captured = capsys.readouterr()
    for half in server_reference.half_names():
        assert half in captured.err
    assert "Traceback" not in captured.err
    assert SECRET not in captured.err
    assert SECRET not in captured.out

    with pytest.raises(cli.ConfigError) as caught:
        server_reference.render(SECRET)
    assert SECRET not in chain(caught.value)


def test_the_default_half_is_the_registrys_first_row() -> None:
    """What the bare verb renders is a fact of the registry rather than a
    word repeated in the command, so the two cannot disagree."""
    assert server_reference.DEFAULT_HALF == server_reference.HALVES[0][0]
    assert server_reference.half_names() == [docgen.DOMAIN, server_reference.SERVER]


# What the page has to carry
#
# The committed copy is diffed byte for byte by CI, and that check is
# exactly as right as the renderer is. These say what the page has to
# mean instead.


def test_the_sections_are_the_models_reachable_from_serverconfig() -> None:
    """The section inventory, in the order the fields are declared in, so
    a nested section that stopped being rendered cannot hide behind
    another section's rows."""
    rendered = sections(server_reference.reference())
    expected = [path for path, _ in walked()]

    assert expected[:2] == ["server", "server.onboarding"], "the walk found nothing to check"
    assert list(rendered) == expected


def test_each_section_names_every_field_of_its_own_model() -> None:
    """Per section, the exact field sequence of that section's model.

    Scoped rather than page-wide because the server models repeat
    `enabled`, `port` and `max_session_s`: a page-wide check passes on a
    row belonging to a different section.
    """
    rendered = sections(server_reference.reference())

    for path, model in walked():
        assert list(rows(rendered[path])) == list(model.model_fields), path


def test_every_bound_renders_in_its_own_fields_row() -> None:
    """Every `Ge`, `Gt`, `Le` and `Lt` pydantic enforces, in the
    Constraints cell of the row it belongs to, and an empty cell where a
    field has none.

    The sweep is what makes this a completeness claim: a bound that
    exists unrendered anywhere on the page fails here rather than being
    noticed by a reader.
    """
    rendered = sections(server_reference.reference())
    seen = 0

    for path, model in walked():
        table = rows(rendered[path])
        for name, info in model.model_fields.items():
            cell = table[name]["constraints"]
            enforced = bounds(info)
            seen += len(enforced)
            if not enforced:
                assert cell == "", f"{path}.{name} has no bound and a nonempty cell"
                continue
            for value in enforced:
                assert str(value) in cell, f"{path}.{name}: {value} is not in {cell!r}"
            assert len(re.findall(r"-?\d+", cell)) == len(enforced), f"{path}.{name}: {cell!r}"

    assert seen > 10, "the sweep found almost no bounds, so it is vacuous"


def test_the_readable_bounds_are_the_ones_a_reader_expects() -> None:
    """The two shapes the column renders, named where they are met: a
    closed range reads as a range, and a lone floor reads as its
    symbol."""
    rendered = sections(server_reference.reference())

    assert rows(rendered["server"])["port"]["constraints"] == "1 to 65535"
    assert (
        rows(rendered["server.conversations"])["resumption_budget_tokens"]["constraints"]
        == ">= 512"
    )
    assert rows(rendered["server"])["llm_first_token_timeout_s"]["constraints"] == "> 0"


# The rules with no metadata form
#
# A bound is enforced by a number pydantic holds, so the column above
# cannot disagree with it. A validator's rule has no such form: it is
# enforced by code and stated in the field's description, and a
# description that omitted or misstated one would pass every check above.
# One assertion per rule family, each in the row the rule is about.


def described(path: str, field: str) -> str:
    """One field's description, as the reader meets it in its own
    section."""
    return rows(sections(server_reference.reference())[path])[field]["description"]


def test_the_page_states_the_environment_name_shape_on_every_env_name_field() -> None:
    """Both keys that name a variable rather than holding a value, found
    by reflection rather than listed, so a third one has to say it too."""
    named = [
        (path, field)
        for path, model in walked()
        for field, info in model.model_fields.items()
        if any(item in EnvName.__metadata__ for item in info.metadata)
    ]
    assert named, "no field carries the environment-name rule, so this is vacuous"

    for path, field in named:
        description = described(path, field)
        assert "letters, digits and underscores" in description, f"{path}.{field}"
        assert "not starting with a digit" in description, f"{path}.{field}"


def test_the_page_states_the_onboarding_keys_shape() -> None:
    description = described("server.onboarding", "key")

    assert "base32" in description
    assert "A-Z and 2-7" in description
    assert "Eight" in description, "the length is half the rule"


def test_the_page_names_every_log_level() -> None:
    description = described("server", "log_level")

    for level in LOG_LEVELS:
        assert level in description
    assert "NOTSET" in description, "the level that is refused is a rule too"


def test_the_page_states_every_ota_path_restriction() -> None:
    """The slash shape and all four reserved places, named from the
    constants that own them rather than written out here."""
    description = described("server", "ota_path")

    assert "start and end with `/`" in description
    for reserved in (API_MOUNT_PATH, ONBOARDING_MOUNT_PATH, HEALTH_PATH, READY_PATH):
        assert reserved in description, reserved


def test_the_page_states_both_url_contracts() -> None:
    """The websocket URL a device is handed and the origin a person is
    told to type, each with what it may not carry."""
    websocket = described("server", "websocket_url")
    assert "`ws://`" in websocket
    assert "`wss://`" in websocket
    assert "names a host" in websocket
    assert "no `user:password`" in websocket

    public = described("server", "public_url")
    assert "`http://`" in public
    assert "`https://`" in public
    assert "no `user:password`" in public
    assert "no query and no fragment" in public


# The environment sections
#
# Every variable named on the page is one the loader or the database
# package applies, so the expectations here are derived from those same
# constants: a renamed variable fails this rather than passing against a
# stale page.


def test_the_page_carries_the_environment_names_the_loader_applies() -> None:
    rendered = server_reference.reference()

    assert SERVER_ENV_PREFIX in rendered
    for variable in DATABASE_ENV_NAMES.values():
        assert variable in rendered, variable
    assert DATABASE_GENERIC_ENV_PREFIX in rendered


# Any name shaped like one of the database variables, wherever one is
# written. Built from the prefix the constants declare, so a rename of
# the prefix finds nothing anywhere and the vacuity guard below is what
# fails, while a rename of one name leaves the old spelling behind as a
# name this pattern finds and the inventory does not admit.
_DATABASE_VARIABLE = re.compile(rf"{DATABASE_ENV_PREFIX}[A-Z0-9_]+")


def test_no_surface_names_a_database_variable_the_constants_do_not() -> None:
    """The six names, on every surface that spells one, held to the
    inventory rather than to inclusion.

    Inclusion is what the case above checks, and it is half the claim: a
    page that names all six and one more is a page telling an operator
    to set a variable nothing reads. These four surfaces are the ones
    that spell a name in prose rather than composing it from the
    constant, so they are where a rename leaves a stale copy: the
    model's docstring, its field descriptions, the rendered page and the
    sentence the database package answers with when nothing opens.

    `DatabaseConfig.__doc__` is prose on purpose, since a docstring
    cannot be an f-string and a `__doc__` assigned after the class would
    move the paragraph away from what it documents. This is what covers
    it instead.

    `vinga_server.db` is imported here rather than at the top of the
    file: importing it reaches SQLAlchemy, and this suite's first case
    is that rendering the page reaches nothing of the sort.
    """
    from vinga_server import db
    from vinga_server.config.models import DatabaseConfig

    declared = {*DATABASE_ENV_NAMES.values(), DATABASE_PASSWORD_ENV, DATABASE_URL_ENV}
    surfaces = {
        "the DatabaseConfig docstring": DatabaseConfig.__doc__ or "",
        "a DatabaseConfig field description": " ".join(
            info.description or "" for info in DatabaseConfig.model_fields.values()
        ),
        "the rendered page": server_reference.reference(),
        "db.UNREACHABLE": db.UNREACHABLE,
    }

    for where, text in surfaces.items():
        found = set(_DATABASE_VARIABLE.findall(text))
        assert found, f"{where} names none of the database variables"
        assert found <= declared, f"{where} names {sorted(found - declared)}"


def test_the_page_names_the_two_values_with_no_key() -> None:
    rendered = server_reference.reference()
    section = rendered.split("## What deliberately has no key\n")[1].split("\n## ")[0]

    assert DATABASE_PASSWORD_ENV in section
    assert DATABASE_URL_ENV in section
    assert "no-secrets-in-YAML" in section
    assert "in its query" in section


# The refusals, in both directions
#
# Forward: the rendered section carries the registry's sentences.
# Backward: each row's provocation raises exactly its own sentence, and
# every model validator reachable from `ServerConfig` is claimed by a
# row, so a new cross-field rule cannot arrive unpublished.


def refusals(page: str) -> list[str]:
    """The sentences the rendered section carries, unwrapped.

    Wrapped in the page because everything on it is, so the comparison is
    against the sentence with its line breaks undone rather than against
    a second copy of it laid out the same way.
    """
    section = page.split("## Refused at boot\n")[1]
    items: list[list[str]] = []
    for line in section.splitlines():
        if line.startswith("- "):
            items.append([line.removeprefix("- ")])
        elif line.startswith("  ") and items:
            items[-1].append(line.strip())
    return [" ".join(item) for item in items]


def test_the_refusal_section_carries_exactly_the_registrys_sentences() -> None:
    rendered = refusals(server_reference.reference())

    assert rendered == [" ".join(row.sentence.split()) for row in BOOT_REFUSALS]


@pytest.mark.parametrize(
    "refusal", BOOT_REFUSALS, ids=[row.validator for row in BOOT_REFUSALS]
)
def test_each_registry_row_is_provoked_by_its_own_misconfiguration(refusal) -> None:
    """A row that cannot be provoked is a rule the page publishes and the
    server does not enforce, which is the failure a rendered string
    cannot show on its own."""
    with pytest.raises(ValidationError) as caught:
        refusal.model(**refusal.provoked_by)

    raised = [problem["msg"] for problem in caught.value.errors()]
    assert len(raised) == 1, raised
    assert refusal.sentence in raised[0]
    for other in BOOT_REFUSALS:
        if other.sentence != refusal.sentence:
            assert other.sentence not in raised[0]


def test_every_cross_field_validator_is_claimed_by_the_registry() -> None:
    """The reverse direction, mechanized. A model validator added to any
    reachable model without a registry row fails here rather than
    silently missing the page."""
    claimed = {(row.model, row.validator) for row in BOOT_REFUSALS}
    declared = {
        (model, name)
        for _, model in walked()
        for name in model.__pydantic_decorators__.model_validators
    }

    assert declared, "no model declares a cross-field validator, so this is vacuous"
    assert declared <= claimed, sorted(
        f"{model.__name__}.{name}" for model, name in declared - claimed
    )


# The committed copy


def test_the_committed_reference_matches_the_models(packaged_database) -> None:
    """The same check CI runs, run here too: locally it fails in the
    suite rather than after a push.

    Under `packaged_database`, and this is the one page that needs it.
    The lane moves `DatabaseConfig`'s four defaults onto the database
    this run provisioned, which is invisible in the domain reference and
    is a Default cell here; what CI regenerates, outside pytest, is what
    a deployment is shipped pointing at. So the comparison is made under
    the shipped condition rather than against a page nobody could
    reproduce.
    """
    assert COMMITTED.read_text(encoding="utf-8") == server_reference.reference(), (
        f"docs/reference/server-config.md is stale; regenerate it with `{REGENERATE}`"
    )
