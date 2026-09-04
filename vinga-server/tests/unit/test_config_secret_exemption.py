"""The one name the secret-key heuristic does not reach, and everything
it still does.

`max_tokens` contains the fragment `token`, so the inline-secret rule
refused it on every surface and for every provider type: the `anthropic`
and `openai_compatible` builders read an option no fragment could ever
install, and the default silently always won (#277). The fix is an
exact, case-sensitive exemption inside `secret_option_fragment`, which
is a loosening of a security rule, so what this file is for is showing
that the loosening admits exactly one name.

Containment is asserted rather than sampled, at three depths:

- **The predicate**, over every fragment of the narrow tuple, the
  exemption's near neighbours, and its case variants. Cheap enough to
  enumerate, and the one place a scan can be shown to have exactly one
  hole in it.
- **The write path**, over the same table on the three surfaces an
  operator installs an option from, because the predicate answering
  correctly and a surface asking a different question is exactly the
  shape of bug this replaces.
- **The wider rule**, which is a separate tuple with separate readers
  (`mcp_secret_fragment`, `is_url_credential_parameter`) and must not
  have moved: an MCP env key, an MCP header and a URL query parameter
  named `max_tokens` are named by somebody else, so nothing there can
  earn an exemption.

Every refused value is a sentinel, in the `PLANTED_KEYS` style of
`test_config_api_problems.py`: a refusal is a surface, and a key
refused for looking like a credential is most likely holding one, so
the value is asserted absent from the exception chain, the structured
body, the log in both formats this server writes, and the two streams
the process holds.
"""

import logging
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import NamedTuple

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.support.config_cli import runner
from tests.support.configs import load_config_from_data
from vinga_server import logs
from vinga_server.config.api import build_api
from vinga_server.config.loader import ConfigError
from vinga_server.config.models import (
    DatabaseConfig,
    is_mcp_secret_key,
    is_secret_option,
    is_url_credential_parameter,
    secret_option_fragment,
    url_credential,
)
from vinga_server.config.secrets import MASTER_KEY_ENV, generate_key, load_keys
from vinga_server.config.store import ConfigStore
from vinga_server.db import open_database

TOKEN = "test-api-token-" + "0123456789abcdef" * 2

# The exempted name, written once. Every case below is either this
# string or deliberately not it.
EXEMPT = "max_tokens"

# The cap a fragment documents, and a value that is not the builders'
# default, so a case that asserted the option arrived cannot be passing
# on the default the defect used to leave behind.
CONFIGURED = 2048

# Not a real credential, and shaped so a substring check for it cannot
# match by accident.
SENTINEL = "sk-test-3f7a91c4-never-a-real-credential"


class Refused(NamedTuple):
    """One key the rule must go on refusing, and the fragment of this
    repository's own six words a refusal may name it by."""

    what: str
    key: str
    matched: str


# Every fragment of the narrow tuple, each under a representative name,
# and then the names that make the exemption's edges: one letter short,
# each half alone, the fragment as the whole key, the exemption with a
# suffix, its words reversed, two ordinary credential names, and the two
# case variants.
#
# The case variants are the P1 of the plan's review round. Option names
# are case-sensitive everywhere they are declared and read, so
# `MAX_TOKENS` is a spelling nothing declares; exempting the lowered
# name would admit it, and the open-doors type would forward it into a
# request as a passthrough field.
#
# `max_tokens_env` is deliberately not here and is not a probe: a key
# ending in `_env` is handled before the fragment scan runs at all, so
# it says nothing about this rule. Its own validation is pinned below.
REFUSED_KEYS = [
    Refused("the fragment secret", "secret_key", "secret"),
    Refused("the fragment token", "access_token", "token"),
    Refused("the fragment password", "db_password", "password"),
    Refused("the fragment api_key", "api_key", "api_key"),
    Refused("the fragment apikey", "apikey", "apikey"),
    Refused("the fragment credential", "vendor_credential", "credential"),
    Refused("one letter short of the exemption", "max_token", "token"),
    Refused("the exemption's second half alone", "tokens", "token"),
    Refused("the fragment as the whole key", "token", "token"),
    Refused("the exemption with a suffix", "max_tokens_backup", "token"),
    Refused("the exemption's words reversed", "tokens_max", "token"),
    Refused("an ordinary credential name", "session_token", "token"),
    Refused("the credential name the wider tuple was widened for", "auth_token", "token"),
    Refused("a credential name carrying the first fragment", "client_secret", "secret"),
    Refused("the exemption in upper case", "MAX_TOKENS", "token"),
    Refused("the exemption in title case", "Max_Tokens", "token"),
]

REFUSED_IDS = [case.what for case in REFUSED_KEYS]


# The predicate itself


def test_the_exemption_admits_exactly_the_one_name() -> None:
    """The rule as a rule, before any surface asks it.

    Both directions in one case, because the claim is an equality: the
    exempted name is not secret-shaped, and every neighbour of it still
    is. A one-sided assertion would pass on an exemption that swallowed
    the fragment whole.
    """
    assert secret_option_fragment(EXEMPT) is None
    assert not is_secret_option(EXEMPT)

    for case in REFUSED_KEYS:
        assert secret_option_fragment(case.key) == case.matched, case.key
        assert is_secret_option(case.key), case.key


def test_the_exemption_did_not_reach_the_wider_tuple_or_the_url_rule() -> None:
    """The other two readers, which have their own tuple and their own
    names to be right about (#279).

    An MCP server's env and headers key and a URL's query parameter are
    named by whoever runs the server or the endpoint, so no name there
    is a declared option a builder reads, which is the condition an
    exemption is earned by. Both spellings, because the exemption's own
    compare is case-sensitive and these two rules are not.
    """
    for spelling in (EXEMPT, EXEMPT.upper()):
        assert is_mcp_secret_key(spelling), spelling
        assert is_url_credential_parameter(spelling), spelling

    # And the URL reader through the door that reads it, which is the
    # half a predicate check on its own would not exercise.
    assert url_credential(f"https://host/v1?{EXEMPT}={SENTINEL}") == "query"
    assert url_credential(f"https://host/v1?{EXEMPT.upper()}={SENTINEL}") == "query"


# The write path
#
# The predicate is not the contract; what an operator meets is. So the
# same table is driven through the three surfaces an option is installed
# from, with the exempted name asserted to arrive and every other name
# asserted to be refused without its value being quoted back.


@pytest.fixture
def keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MASTER_KEY_ENV, generate_key())


@pytest.fixture
def store(keys: None) -> Iterator[ConfigStore]:
    engine = open_database(DatabaseConfig())
    try:
        yield ConfigStore(engine, load_keys())
    finally:
        engine.dispose()


@pytest.fixture
def api(keys: None) -> FastAPI:
    return build_api(TOKEN, DatabaseConfig())


@pytest.fixture
def client(api: FastAPI) -> Iterator[TestClient]:
    with TestClient(api, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        yield client


@pytest.fixture
def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    return runner(monkeypatch)


def _entry(**options: object) -> dict[str, object]:
    """One `anthropic` entry, which is the open-doors half of the
    question: every key beyond `type` is passed through, so a key is
    refused here by the shared rule and by nothing else."""
    return {"type": "anthropic", "model": "claude-sonnet-5", **options}


def test_the_exempted_option_installs_from_a_file(store: ConfigStore) -> None:
    """The file surface, which is where a deployment writes one, and the
    one that never reaches a store: the models refuse on construction,
    so this is the rule at its earliest door."""
    config = load_config_from_data(
        {"providers": {"llm": {"claude": _entry(max_tokens=CONFIGURED)}}}
    )

    assert config.providers.llm["claude"].options == {
        "model": "claude-sonnet-5",
        EXEMPT: CONFIGURED,
    }


def test_the_exempted_option_installs_over_the_api(
    client: TestClient, store: ConfigStore
) -> None:
    """The API surface, read back through the repository rather than
    through the display: what a read shows is a separate claim, made
    below."""
    assert (
        client.put("/providers/llm/claude", json=_entry(max_tokens=CONFIGURED)).status_code
        == 200
    )

    assert store.read_provider("llm", "claude").entry.model_extra[EXEMPT] == CONFIGURED


def test_the_exempted_option_installs_from_the_command_line(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The CLI surface, through its own assignment parsing, so the
    option arrives as the integer a YAML scalar makes of it rather than
    as the string a shell handed over."""
    assert (
        run(
            "provider", "set", "llm", "claude",
            "type=anthropic", "model=claude-sonnet-5", f"max_tokens={CONFIGURED}",
        )
        == 0
    )
    capsys.readouterr()

    assert run("provider", "show", "llm", "claude") == 0
    assert f"{EXEMPT}: {CONFIGURED}" in capsys.readouterr().out


def test_the_exempted_option_installs_under_a_type_that_declares_it(
    client: TestClient, store: ConfigStore
) -> None:
    """The other half of the type question. `openai_compatible` declares
    `max_tokens` as a `StrictInt`, and before the exemption the shared
    rule refused the key before the model it belongs to ever saw it."""
    written = {
        "type": "openai_compatible",
        "base_url": "http://localhost:11434/v1",
        "model": "qwen3:8b",
        "egress": False,
        EXEMPT: CONFIGURED,
    }

    assert client.put("/providers/llm/local", json=written).status_code == 200

    assert store.read_provider("llm", "local").entry.model_extra[EXEMPT] == CONFIGURED


@pytest.mark.parametrize("case", REFUSED_KEYS, ids=REFUSED_IDS)
@pytest.mark.parametrize("nested", [False, True], ids=["flat", "nested"])
def test_every_other_secret_shaped_key_is_still_refused(
    store: ConfigStore, capsys: pytest.CaptureFixture[str], case: Refused, nested: bool
) -> None:
    """The matrix, at the repository, flat and one key deep.

    Depth is a case rather than a footnote: a provider entry passes
    every option beyond the declared ones through to its implementation,
    so an option can be a structure, and `connection: {api_key: ...}` is
    as ordinary a shape to write as `api_key: ...` is. The exemption is
    a name rule, so it has to hold at whatever depth a name is met.

    The refusal names the fragment and never the key, because an option
    is a key the caller wrote; the value is a sentinel and is asserted
    absent from the exception, its chain, its problems and the two
    streams a repository refusal must put nothing on.
    """
    written = {case.key: SENTINEL}
    fragment = _entry(connection=written) if nested else _entry(**written)

    with pytest.raises(ConfigError) as caught:
        store.set_provider("llm", "claude", fragment)

    refusal = caught.value
    assert f'a key containing "{case.matched}"' in str(refusal)
    assert SENTINEL not in str(refusal)
    assert SENTINEL not in repr(refusal)
    if case.key != case.matched:
        # The key the caller wrote is not quoted back. Skipped only
        # where the key IS one of this repository's own six words, which
        # the refusal names on purpose and which nobody invented.
        assert case.key not in str(refusal)
    assert refusal.__cause__ is None
    assert refusal.__context__ is None
    for carried in refusal.problems:
        assert SENTINEL not in carried.path
        assert SENTINEL not in carried.message

    streams = capsys.readouterr()
    assert SENTINEL not in streams.out
    assert SENTINEL not in streams.err


@pytest.mark.parametrize("case", REFUSED_KEYS, ids=REFUSED_IDS)
@pytest.mark.parametrize("nested", [False, True], ids=["flat", "nested"])
def test_a_refused_key_leaks_nothing_over_the_api(
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    case: Refused,
    nested: bool,
) -> None:
    """And the same table over HTTP, where a refusal has four more
    places to carry a value: the sentence, every pointer, every message,
    and the log in both formats this server writes."""
    written = {case.key: SENTINEL}
    fragment = _entry(connection=written) if nested else _entry(**written)

    with caplog.at_level(logging.DEBUG):
        response = client.put("/providers/llm/claude", json=fragment)

    assert response.status_code == 422
    body = response.json()
    assert SENTINEL not in body["detail"]
    for error in body["errors"]:
        assert SENTINEL not in error["path"]
        assert SENTINEL not in error["message"]
    assert SENTINEL not in response.text
    assert SENTINEL not in str(response.headers)

    text = logging.Formatter(logs.TEXT_FORMAT)
    for record in caplog.records:
        assert SENTINEL not in logs.JsonFormatter().format(record)
        assert SENTINEL not in text.format(record)

    streams = capsys.readouterr()
    assert SENTINEL not in streams.out
    assert SENTINEL not in streams.err


def test_the_env_reference_spelling_keeps_its_own_validation(store: ConfigStore) -> None:
    """`max_tokens_env` is not a probe of this rule and never was: a key
    ending in `_env` is answered before the fragment scan runs, so the
    exemption cannot have moved it either way.

    Both halves, so this is not passing on a key nothing looks at: a
    pasted value is refused without being quoted, and a variable name is
    kept.
    """
    with pytest.raises(ConfigError) as caught:
        store.set_provider("llm", "claude", _entry(max_tokens_env=SENTINEL))
    assert "ending in _env" in str(caught.value)
    assert SENTINEL not in str(caught.value)

    store.set_provider("llm", "claude", _entry(max_tokens_env="MY_PROVIDER_MAX_TOKENS"))
    assert store.read_provider("llm", "claude").entry.model_extra == {
        "model": "claude-sonnet-5",
        "max_tokens_env": "MY_PROVIDER_MAX_TOKENS",
    }


# The wider rule, at the doors that read it
#
# `mcp_secret_fragment` and `is_url_credential_parameter` read a tuple
# of their own, over names this repository never chose: an MCP server's
# env and headers are keyed by whatever the server calls them, and a URL
# query parameter is named by the vendor whose endpoint it addresses. So
# no name there can meet the second condition an exemption is earned by,
# and `max_tokens` is a credential-shaped name at all three doors.
#
# Held to the same discipline as the narrow half above rather than to a
# weaker one, and for the same reason: these refusals are about a value
# that most likely IS a credential, and a refusal is a surface. Each
# case therefore runs on all three surfaces an operator reaches them
# from, with the sentinel asserted absent from the exception, its chain,
# its structured problems, the response body and headers, both log
# formats and both streams.


class Wider(NamedTuple):
    """One wider-rule refusal, and the three ways in: the route a
    request reaches it by, the repository call under it that composes
    the same refusal, and the command words the CLI writes it with.

    `entity` is what every one of these refusals names and the only
    semantic token they share, which is what keeps the terminal case
    from passing on a command that failed for some unrelated reason.
    """

    what: str
    path: str
    argv: tuple[str, ...]
    fragment: dict[str, object]
    write: Callable[[ConfigStore, dict[str, object]], None]
    entity: str


def _mcp(group: str, key: str) -> dict[str, object]:
    """One MCP entry carrying the sentinel under `key`.

    The transport is the one the group belongs to, because an entry that
    is wrong twice would be answered twice, and a case about a
    credential-shaped key must not be riding on a refusal about where
    headers may be written.
    """
    if group == "headers":
        return {
            "transport": "streamable_http",
            "url": "https://example.invalid/mcp",
            "headers": {key: SENTINEL},
        }
    return {"transport": "stdio", "command": "uvx", "env": {key: SENTINEL}}


def _addressed(spelling: str) -> dict[str, object]:
    return {
        "type": "openai_compatible",
        "model": "qwen3:8b",
        "egress": False,
        "base_url": f"https://host/v1?{spelling}={SENTINEL}",
    }


WIDER = [
    Wider(
        f"an mcp {group} key named {spelling}",
        "/mcp-servers/home",
        ("mcp-server", "set", "home"),
        _mcp(group, spelling),
        lambda store, fragment: store.set_mcp_server("home", fragment),
        "mcp_servers.home",
    )
    for group in ("env", "headers")
    for spelling in (EXEMPT, EXEMPT.upper())
] + [
    Wider(
        f"a provider address with a {spelling} query parameter",
        "/providers/llm/local",
        ("provider", "set", "llm", "local"),
        _addressed(spelling),
        lambda store, fragment: store.set_provider("llm", "local", fragment),
        "providers.llm.local",
    )
    for spelling in (EXEMPT, EXEMPT.upper())
]

WIDER_IDS = [case.what for case in WIDER]


@pytest.mark.parametrize("case", WIDER, ids=WIDER_IDS)
def test_the_exempted_name_is_still_a_credential_to_the_wider_rule(
    store: ConfigStore, capsys: pytest.CaptureFixture[str], case: Wider
) -> None:
    """At the repository, on the exception itself.

    An exception is a surface of its own: anything that walks one reads
    its message, its repr, its cause and its context, and the API's own
    `problems` ride on it. The two streams are asserted because a
    repository refusal is raised rather than printed, so what the write
    path puts on a terminal is nothing at all.
    """
    with pytest.raises(ConfigError) as caught:
        case.write(store, case.fragment)

    refusal = caught.value
    assert SENTINEL not in str(refusal)
    assert SENTINEL not in repr(refusal)
    assert refusal.__cause__ is None
    assert refusal.__context__ is None
    for carried in refusal.problems:
        assert SENTINEL not in carried.path
        assert SENTINEL not in carried.message

    streams = capsys.readouterr()
    assert SENTINEL not in streams.out
    assert SENTINEL not in streams.err


@pytest.mark.parametrize("case", WIDER, ids=WIDER_IDS)
def test_a_wider_rule_refusal_leaks_nothing_over_the_api(
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    case: Wider,
) -> None:
    """And over HTTP, where the same refusal has four more places to
    carry a value: the sentence, every pointer, every message, and the
    log in both formats this server writes."""
    with caplog.at_level(logging.DEBUG):
        response = client.put(case.path, json=case.fragment)

    assert response.status_code == 422
    body = response.json()
    assert SENTINEL not in body["detail"]
    for error in body["errors"]:
        assert SENTINEL not in error["path"]
        assert SENTINEL not in error["message"]
    assert SENTINEL not in response.text
    assert SENTINEL not in str(response.headers)

    text = logging.Formatter(logs.TEXT_FORMAT)
    for record in caplog.records:
        assert SENTINEL not in logs.JsonFormatter().format(record)
        assert SENTINEL not in text.format(record)

    streams = capsys.readouterr()
    assert SENTINEL not in streams.out
    assert SENTINEL not in streams.err


@pytest.mark.parametrize("case", WIDER, ids=WIDER_IDS)
def test_a_wider_rule_refusal_leaks_nothing_from_the_command_line(
    run,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    case: Wider,
) -> None:
    """And the surface an operator most often writes one from, which is
    the one place a value that got past the two above would be printed
    at a terminal.

    The fragment goes in on stdin, which is how a whole entry is
    written, so what the command holds is the credential the operator
    pasted, and the refusal it prints is the repository's own sentence
    reaching a terminal rather than a body.
    """
    with caplog.at_level(logging.DEBUG):
        code = run(*case.argv, "-f", "-", stdin=yaml.safe_dump(case.fragment))

    assert code != 0
    streams = capsys.readouterr()
    assert SENTINEL not in streams.out
    assert SENTINEL not in streams.err
    # And the refusal really is the one this case is about, so a command
    # that failed for some other reason cannot pass this vacuously.
    assert case.entity in streams.err

    text = logging.Formatter(logs.TEXT_FORMAT)
    for record in caplog.records:
        assert SENTINEL not in logs.JsonFormatter().format(record)
        assert SENTINEL not in text.format(record)
