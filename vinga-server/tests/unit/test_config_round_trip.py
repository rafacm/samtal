"""The writable round trip: a read of an entity is a write of it.

The contract #192 states is that the `entity` half of a read is
resubmittable as it stands. Here it is executable: every commanded kind
is written from its own example fragment, read back, and the read is
PUT again unchanged, and the second read is the first one byte for byte.

The hard case is the mask. A read masks whatever sits under a
secret-shaped key, and not every masked value is ciphertext: a lowercase
environment name in an `*_env` option and a whitespace-padded `$VAR` in
an MCP server's env are both values a write accepts and the display
rule refuses to show, so a read of such an entity carries `********`
where a value it holds is stored. The unchanged-value marker is what
makes that read writable: resubmitting the mask means keep what is
stored there, and a mask with nothing stored behind it is refused
naming as much of the path as this repository may name.

So the masked cases here start from writes the API itself accepted,
which is what an engine-planted row cannot stand in for, and the
sentinel runs on both paths: the value the marker substitutes is a
planted credential, and it must appear in no response, no refusal, no
log record in either format, and on no exception the repository raises.
"""

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.support.config_cli import chain, document, runner
from tests.support.problems import paths
from tests.support.problems import refused as refusal_body
from tests.support.stores import body, planted
from vinga_server import logs
from vinga_server.config import cli
from vinga_server.config.api import build_api
from vinga_server.config.entities import PROGRAM
from vinga_server.config.loader import ConfigError
from vinga_server.config.models import DatabaseConfig, ProviderConfig
from vinga_server.config.secrets import (
    MASK,
    MASTER_KEY_ENV,
    generate_key,
    load_keys,
)
from vinga_server.config.store import ConfigStore
from vinga_server.db import open_database, schema

TOKEN = "test-api-token-" + "0123456789abcdef" * 2

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"

# A pasted credential shaped like the name of an environment variable,
# which is what makes it the interesting one: `api_key_env` accepts it,
# so it is stored, and the display refuses to show it because a
# lowercase bare name is as likely to be a key as a variable. It is
# shaped so a substring check for it cannot match by accident.
PASTED = "sk_test_4f8b2c9e_never_a_real_credential"

# The other value a write accepts and a read masks: a reference the MCP
# rule strips before it checks it, where the display rule does not.
PADDED = "  $HOME_ASSISTANT_TOKEN  "


@pytest.fixture
def database() -> DatabaseConfig:
    """The database this lane provisioned, which is where the store
    writes and the application reads."""
    return DatabaseConfig()


@pytest.fixture
def keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MASTER_KEY_ENV, generate_key())


@pytest.fixture
def store(database: DatabaseConfig, keys: None) -> Iterator[ConfigStore]:
    """A second view of the same database, for reading a value back
    through the repository rather than through the display that masked
    it."""
    engine = open_database(DatabaseConfig())
    try:
        yield ConfigStore(engine, load_keys())
    finally:
        engine.dispose()


@pytest.fixture
def api(database: DatabaseConfig, keys: None) -> FastAPI:
    return build_api(TOKEN, database)


@pytest.fixture
def client(api: FastAPI) -> Iterator[TestClient]:
    """Entered, so the requests reach a real repository: this whole file
    is about what a real read and a real write do to one another."""
    with TestClient(api, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        yield client


@pytest.fixture
def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The CLI as its entry point runs it, against a server of its own."""
    return runner(monkeypatch)


def _example(name: str) -> object:
    """One committed example fragment, as the command in its own header
    would send it."""
    return yaml.safe_load((EXAMPLES / name).read_text(encoding="utf-8"))


def _pipeline(client: TestClient) -> None:
    """Everything the example fragments reference, written first: a
    write naming an entry that is not there is refused, which is what
    the natural creation order is about."""
    for stage, name in (
        ("llm", "claude"),
        ("asr", "whisper"),
        ("tts", "piper"),
        ("tts", "eleven"),
        ("vad", "silero"),
    ):
        client.put(f"/providers/{stage}/{name}", json={"type": "mock"})
    for name in ("weather", "home"):
        client.put(f"/mcp-servers/{name}", json={"transport": "stdio", "command": "uvx"})
    client.put("/prompt-fragments/household", json={"text": "The bins go out on Tuesday."})
    client.put(
        "/agent-defaults",
        json={"llm": "claude", "asr": "whisper", "tts": "piper", "vad": "silero"},
    )


# The round trip, one case per commanded kind


ROUND_TRIP = [
    ("/providers/llm/claude", "llm-anthropic.yaml"),
    ("/mcp-servers/home", "mcp-server-stdio.yaml"),
    ("/prompt-fragments/household", "prompt-fragment.yaml"),
    ("/agents/assistant", "agent.yaml"),
    ("/agent-defaults", "agent-defaults.yaml"),
]


@pytest.mark.parametrize(("path", "example"), ROUND_TRIP)
def test_a_read_of_one_entity_is_a_write_of_it(
    client: TestClient, path: str, example: str
) -> None:
    """The contract in its executable form, for each kind in turn: the
    fragment an operator installs, the envelope a read answers, that
    envelope's `entity` sent straight back, and the same envelope again.

    The example fragments are the input because they are the shapes this
    project documents: every field the kind's own documentation shows an
    operator is in one of them, including the ones a read leaves out.
    """
    _pipeline(client)
    assert client.put(path, json=_example(example)).status_code == 200
    read = client.get(path)
    assert read.status_code == 200
    envelope = read.json()

    again = client.put(path, json=envelope["entity"])

    assert again.status_code == 200
    assert set(again.json()) == {"wrote", "notice"}
    assert client.get(path).json() == envelope


# The masked resubmit, from writes this API accepted


def test_a_masked_nested_reference_resubmits_as_the_stored_value(
    client: TestClient, store: ConfigStore
) -> None:
    """A provider option holding a lowercase environment name: written
    over HTTP, masked on the way out, and resubmitted as the mask, which
    means keep it. What proves it kept it is the repository, not the
    display that masked it in the first place."""
    written = {"type": "anthropic", "connection": {"api_key_env": PASTED, "host": "example"}}
    assert client.put("/providers/llm/claude", json=written).status_code == 200
    envelope = client.get("/providers/llm/claude").json()
    assert envelope["entity"]["connection"] == {"api_key_env": MASK, "host": "example"}

    again = client.put("/providers/llm/claude", json=envelope["entity"])

    assert again.status_code == 200
    assert client.get("/providers/llm/claude").json() == envelope
    stored = store.read_provider("llm", "claude").entry
    assert stored.model_extra["connection"] == {"api_key_env": PASTED, "host": "example"}


def test_a_masked_padded_env_reference_resubmits_as_the_stored_value(
    client: TestClient, store: ConfigStore
) -> None:
    """The MCP half of the same fact, and a different reason for it: the
    secret rule strips a reference before it checks it, the display rule
    does not, so a padded `$VAR` is accepted and read back masked."""
    written = {"transport": "stdio", "command": "uvx", "env": {"API_ACCESS_TOKEN": PADDED}}
    assert client.put("/mcp-servers/home", json=written).status_code == 200
    envelope = client.get("/mcp-servers/home").json()
    assert envelope["entity"]["env"] == {"API_ACCESS_TOKEN": MASK}

    again = client.put("/mcp-servers/home", json=envelope["entity"])

    assert again.status_code == 200
    assert client.get("/mcp-servers/home").json() == envelope
    assert store.read_mcp_server("home").entry.env == {"API_ACCESS_TOKEN": PADDED}


def test_a_mask_under_a_key_that_is_not_secret_shaped_is_a_value(
    client: TestClient, store: ConfigStore
) -> None:
    """The marker is exactly the secret-shaped keys the display masks.
    Eight asterisks written anywhere else are eight asterisks, which is
    what keeps the rule from reaching into an operator's own data."""
    assert (
        client.put("/providers/llm/claude", json={"type": "anthropic", "note": MASK}).status_code
        == 200
    )

    assert store.read_provider("llm", "claude").entry.model_extra["note"] == MASK


# The option the marker rule stopped reaching (#277)


LOCAL = {
    "type": "openai_compatible",
    "base_url": "http://localhost:11434/v1",
    "model": "qwen3:8b",
    "egress": False,
}

# A cap that is not the builders' default, so a case asserting the value
# arrived cannot be passing on the default the defect used to leave.
CONFIGURED = 2048


def test_the_exempted_option_reads_back_unmasked_and_resubmits(
    client: TestClient, store: ConfigStore
) -> None:
    """`max_tokens` is a reply-length cap rather than a credential, so
    the read shows it, and the round trip is the ordinary one: the
    entity half of a read is a write of it, with no marker in the
    middle."""
    assert (
        client.put("/providers/llm/local", json={**LOCAL, "max_tokens": CONFIGURED}).status_code
        == 200
    )
    envelope = client.get("/providers/llm/local").json()
    assert envelope["entity"]["max_tokens"] == CONFIGURED

    again = client.put("/providers/llm/local", json=envelope["entity"])

    assert again.status_code == 200
    assert client.get("/providers/llm/local").json() == envelope
    assert store.read_provider("llm", "local").entry.model_extra["max_tokens"] == CONFIGURED


def test_the_mask_over_the_exempted_option_is_a_value_and_not_a_marker(
    client: TestClient, store: ConfigStore
) -> None:
    """The reshaping the exemption does to the marker rule, pinned
    directly rather than left to the generic control above.

    What a read hides and what a write restores are one predicate, so
    moving `max_tokens` out of it moves both ends at once: eight
    asterisks under that key are eight asterisks, and the type that
    declares the field refuses them by name. Under the old predicate
    this same request read as keep-what-is-stored and answered 200,
    which is what makes this a pin rather than a restatement.
    """
    assert (
        client.put("/providers/llm/local", json={**LOCAL, "max_tokens": CONFIGURED}).status_code
        == 200
    )

    response = client.put("/providers/llm/local", json={**LOCAL, "max_tokens": MASK})

    assert response.status_code == 422
    body = response.json()
    assert refusal_body(body, 422).startswith("invalid providers.llm.local:")
    # The field is one the type declares, so the pointer addresses it.
    assert paths(body) == ["/max_tokens"]
    # And nothing of the refused write landed: the integer is as it was.
    assert store.read_provider("llm", "local").entry.model_extra["max_tokens"] == CONFIGURED


def test_the_exempted_option_exports_as_its_value_and_imports_back(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The export half of the same claim. A credential is not in an
    exported body at all and is named as the command that enters it; a
    cap is a body value and travels as itself, which is what makes an
    export of a store carrying one applicable back onto it."""
    assert (
        run(
            "provider", "set", "llm", "local",
            "type=openai_compatible",
            "base_url=http://localhost:11434/v1",
            "model=qwen3:8b",
            "egress=false",
            f"max_tokens={CONFIGURED}",
        )
        == 0
    )
    capsys.readouterr()

    assert run("export") == 0
    exported = capsys.readouterr().out

    assert f"max_tokens: {CONFIGURED}" in exported
    assert MASK not in exported

    assert run("import", "-f", "-", stdin=exported) == 0
    assert {line.split(": ")[-1] for line in capsys.readouterr().out.splitlines()} == {
        "unchanged"
    }


# A mask with nothing behind it


def test_the_mask_on_an_entity_that_does_not_exist_yet_is_refused(
    client: TestClient, store: ConfigStore
) -> None:
    """A create has nothing to keep, so every mark in it is a mask with
    nothing behind it. The field is declared, so the refusal names it and
    points at it."""
    response = client.put(
        "/providers/llm/fresh", json={"type": "anthropic", "api_key_env": MASK}
    )

    assert response.status_code == 422
    body = response.json()
    detail = refusal_body(body, 422)
    assert detail.startswith("invalid providers.llm.fresh:")
    # The field is declared, so it is named and addressed.
    assert paths(body) == ["/api_key_env"]
    assert "api_key_env" in detail
    assert client.get("/providers/llm/fresh").status_code == 404
    with pytest.raises(ConfigError):
        store.read_provider("llm", "fresh")


def test_the_mask_on_a_path_the_entity_does_not_hold_is_refused(
    client: TestClient, store: ConfigStore
) -> None:
    """The entity is there and the path is not. The key is one the caller
    wrote, so neither the sentence nor the pointer reaches it: the
    pointer is the whole fragment, which is the nearest place this
    repository can name."""
    client.put("/providers/llm/claude", json={"type": "anthropic", "model": "m"})

    response = client.put(
        "/providers/llm/claude",
        json={"type": "anthropic", "connection": {"api_key_env": MASK}},
    )

    assert response.status_code == 422
    body = response.json()
    detail = refusal_body(body, 422)
    assert detail.startswith("invalid providers.llm.claude:")
    # The whole fragment, which is the nearest place this repository can
    # name, and the key the caller wrote is in neither half.
    assert paths(body) == [""]
    assert "connection" not in detail
    # And nothing of the refused write landed: the mask is not a value,
    # so it is not in the row either.
    stored = store.read_provider("llm", "claude").entry
    assert stored.model_extra == {"model": "m"}
    assert MASK not in str(stored)


def test_the_mask_in_a_group_of_written_keys_names_the_group(
    client: TestClient, store: ConfigStore
) -> None:
    """An MCP server's env is keyed by whatever was written, so the
    refusal stops at the group, which is a field this repository
    declares."""
    client.put("/mcp-servers/home", json={"transport": "stdio", "command": "uvx"})

    response = client.put(
        "/mcp-servers/home",
        json={"transport": "stdio", "command": "uvx", "env": {"API_ACCESS_TOKEN": MASK}},
    )

    assert response.status_code == 422
    body = response.json()
    detail = refusal_body(body, 422)
    assert detail.startswith("invalid mcp_servers.home:")
    # The group, which this repository declares, and never the key under
    # it, which the caller wrote.
    assert paths(body) == ["/env"]
    assert "API_ACCESS_TOKEN" not in detail
    assert store.read_mcp_server("home").entry.env == {}


# The row that got its contents another way


def test_a_planted_credential_round_trips_without_becoming_the_mask(
    client: TestClient, store: ConfigStore
) -> None:
    """A row written straight to the database, which is what a value
    that never passed through a write looks like. The display fails
    closed on it, and the marker keeps it: an operator editing the
    entry's model does not silently replace a credential they cannot
    see with eight asterisks."""
    client.put("/providers/llm/claude", json={"type": "anthropic", "model": "m"})
    planted(
        store,
        schema.providers.update()
        .where(schema.providers.c.name == "claude")
        .values(body=body(ProviderConfig(type="anthropic", model="m", api_key_env=PASTED))),
    )
    envelope = client.get("/providers/llm/claude").json()
    assert envelope["entity"]["api_key_env"] == MASK

    again = client.put("/providers/llm/claude", json=envelope["entity"])

    assert again.status_code == 200
    assert client.get("/providers/llm/claude").json() == envelope
    assert store.read_provider("llm", "claude").entry.api_key_env == PASTED
    assert PASTED not in again.text


# The sentinel, on both paths


def test_the_substituted_value_leaks_nowhere(
    client: TestClient, store: ConfigStore, caplog: pytest.LogCaptureFixture
) -> None:
    """The marker is the one place a stored value is put back into a
    fragment the caller sent, and a fragment is the one body that
    legitimately carries a credential. So the planted value is looked
    for in every response, in the refusal, in both log formats and on
    the exception itself, on the path that succeeds and on the path that
    refuses.

    The refusing fragment carries both marks at once: one the marker
    resolves to the planted value and one it cannot resolve at all, so
    the refusal is built with the substituted value in hand.
    """
    written = {"type": "anthropic", "connection": {"api_key_env": PASTED}}
    with caplog.at_level(logging.DEBUG):
        assert client.put("/providers/llm/claude", json=written).status_code == 200
        read = client.get("/providers/llm/claude")
        kept = client.put("/providers/llm/claude", json=read.json()["entity"])
        refused = client.put(
            "/providers/llm/claude",
            json={
                "type": "anthropic",
                "connection": {"api_key_env": MASK},
                "session": {"api_key_env": MASK},
            },
        )

    assert kept.status_code == 200
    assert refused.status_code == 422
    # Two masked keys, one entry: what is named is the fragment, once.
    assert paths(refused.json()) == [""]
    for response in (read, kept, refused):
        assert PASTED not in response.text
        assert PASTED not in str(dict(response.headers))
    text = logging.Formatter(logs.TEXT_FORMAT)
    for record in caplog.records:
        assert PASTED not in logs.JsonFormatter().format(record)
        assert PASTED not in text.format(record)

    # And the exception the repository raises, which the response above
    # is only one rendering of: nothing on it, nothing behind it.
    with pytest.raises(ConfigError) as caught:
        store.set_provider(
            "llm",
            "claude",
            {
                "type": "anthropic",
                "connection": {"api_key_env": MASK},
                "session": {"api_key_env": MASK},
            },
        )
    assert PASTED not in chain(caught.value)
    assert PASTED not in str(caught.value.problems)


# The CLI, which sends the same fragment over the same route


def test_the_cli_resubmits_a_masked_document_it_printed(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The marker is the repository's, so the CLI needs nothing for
    this, which is exactly what is worth pinning: the document `show`
    prints is a document `set` accepts back, and the value the operator
    was not shown survives it."""
    assert (
        run(
            "provider", "set", "llm", "claude",
            "-f",
            "-",
            stdin=f"type: anthropic\nmodel: m\nconnection:\n  api_key_env: {PASTED}\n",
        )
        == 0
    )
    capsys.readouterr()

    run("provider", "show", "llm", "claude")
    shown = capsys.readouterr().out
    printed = document(shown)
    assert printed["connection"] == {"api_key_env": MASK}
    assert PASTED not in shown

    assert (
        run("provider", "set", "llm", "claude", "-f", "-", stdin=yaml.safe_dump(printed)) == 0
    )

    capsys.readouterr()
    run("provider", "show", "llm", "claude")
    assert document(capsys.readouterr().out) == printed


# The other round trip: a store, exported and reproduced
#
# The per-entity contract above is a read that is a write. This is the
# whole-store one: what `config export` prints is a document
# `config import` takes, and importing it onto an empty store and
# entering the credentials it names produces a store whose own export is
# the same bytes.
#
# The order is the supported one and the test does not shortcut it. A
# stored credential is not in an exported body at all, and it cannot be:
# the mask is not a value a creating write accepts, so an export with
# masks injected would fail on the empty store it is most needed for,
# and a secret write cannot run before the entity exists. Nor can the
# credentials come last: an apply builds the engines the document names,
# and an engine is built with the credential the store holds for it. So
# the sequence is import, then the secret sets, then apply, which is
# what the export's own header says and what the case below holds its
# foot to as well.


def test_the_export_footer_puts_the_credentials_where_the_header_does() -> None:
    """The one line of an export that has to agree with another line of
    the same file.

    The header numbers three steps and the credentials are the second of
    them; the footer is the sentence an operator reads when they get
    down to the commands, and a file whose two halves disagree about the
    order is worse than one that says nothing, because the half that is
    wrong is the half nearest the commands being pasted.

    It said "after applying" and was true while applying meant writing
    (#341). #371 moved that word onto the install, which left the footer
    telling an operator to enter the credentials after the engines that
    need them had already been built. The order is explicit now, and
    this reads it off the header rather than restating it.
    """
    importing = f"{PROGRAM} import"
    applying = f"{PROGRAM} apply"

    for named in (cli.EXPORT_HEADER, cli.EXPORT_SECRETS_HEADING):
        assert importing in named, named
        assert applying in named, named
        assert named.index(importing) < named.index(applying), named

    # And the sentence that was true of the old grammar is gone rather
    # than merely outnumbered.
    assert "after applying" not in cli.EXPORT_SECRETS_HEADING


# The three ways a secret reaches an entity, all three seeded below,
# because they travel three different ways. An environment reference is
# a body value and is exported as itself. A stored credential is not in
# the body at all and is exported as the command that enters it. A
# stored credential over a reference written for the same slot is both
# at once, and the export has to carry both halves.
ANTHROPIC_REFERENCE = "ANTHROPIC_API_KEY"
HOME_REFERENCE = "$HOME_ASSISTANT_TOKEN"

STORED = {
    ("provider", "asr.whisper", "api_key"): "sk-test-asr-never-a-real-credential",
    ("mcp_server", "home", "headers.Authorization"): "tok-test-mcp-never-a-real-value",
}


def _seed(run) -> None:
    """A deployment holding all three, written the way an operator
    writes one."""
    assert run("provider", "set", "llm", "claude", "type=anthropic", "model=m",
               f"api_key_env={ANTHROPIC_REFERENCE}") == 0
    assert run("provider", "set", "asr", "whisper", "type=mock") == 0
    assert run(
        "mcp-server", "set", "home",
        "transport=streamable_http",
        "url=https://example.invalid/mcp",
        f"headers.Authorization={HOME_REFERENCE}",
    ) == 0
    assert run("agent", "set", "sam", "prompt=You are Sam.", "llm=claude", "asr=whisper") == 0
    assert run("device", "bind", "AA-BB-CC-DD-EE-FF", "sam") == 0
    assert run("default-agent", "set", "sam") == 0
    _enter_secrets(run, _SET_SECRETS)


# The commands the seed enters its two stored credentials with, which
# is also the shape and the ORDER the export names them in: the store
# lists its locations sorted, so that two exports of one configuration
# are the same bytes whatever order the credentials were entered in.
_SET_SECRETS = [
    ["mcp-server", "secret", "set", "--", "home", "headers.Authorization"],
    ["provider", "secret", "set", "--", "asr", "whisper", "api_key"],
]


def _enter_secrets(run, commands: list[list[str]]) -> None:
    for words in commands:
        location = _located(words)
        assert run(*words, stdin=STORED[location] + "\n") == 0, words


def _located(words: list[str]) -> tuple[str, str, str]:
    """One secret-set command as the location it addresses, which is
    how a test knows which credential to feed it. The `--` is dropped
    the way the parser drops it: it separates the command's own words
    from the identity, and is not part of either."""
    kind, _secret, _verb, _marker, *identity, slot = words
    return (kind.replace("-", "_"), ".".join(identity), slot)


def _exported_secret_commands(exported: str) -> list[list[str]]:
    """The commands an export's foot names, as an operator would run
    them: the comment marker off, the words split the way a shell splits
    them, and the two words that name this command group dropped."""
    prefix = f"#   {PROGRAM} "
    return [
        __import__("shlex").split(line[len(prefix):])
        for line in exported.splitlines()
        if line.startswith(prefix)
    ]


def test_an_export_reproduces_the_store_it_came_from(
    spare_database: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The whole supported reproduction, end to end and byte for byte.

    Two databases, because that is the claim: the document one
    deployment exports is the document another is built from. The second
    store is seeded by nothing but the export and the commands the
    export itself names, so a slot the annotation forgot would show up
    as a missing credential rather than as a passing test.
    """
    first = runner(monkeypatch)
    _seed(first)
    capsys.readouterr()

    assert first("export") == 0
    exported = capsys.readouterr().out

    # What the document carries, and what it must not. An environment
    # reference is a body value and travels as itself; a stored
    # credential is nowhere in it, and is named as the command that
    # enters it.
    assert ANTHROPIC_REFERENCE in exported
    assert HOME_REFERENCE in exported
    assert MASK not in exported
    for secret in STORED.values():
        assert secret not in exported
    assert _exported_secret_commands(exported) == _SET_SECRETS

    # A fresh store, built from the export and from nothing else.
    #
    # An import, which is what the export's own header says to run and
    # what a rebuild needs: the credentials go in after the document and
    # before anything is installed, so the apply is the step after them
    # rather than the one in the middle of them.
    second = runner(monkeypatch, database=spare_database)
    assert second("import", "-f", "-", stdin=exported) == 0
    _enter_secrets(second, _exported_secret_commands(exported))
    capsys.readouterr()

    assert second("export") == 0

    assert capsys.readouterr().out == exported


def test_an_exported_document_imports_onto_the_store_it_came_from(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The other direction, which is what makes an export safe to keep
    and re-run: imported back onto its own store it changes nothing, and
    the credentials stored on those entities are still there."""
    run = runner(monkeypatch)
    _seed(run)
    capsys.readouterr()
    run("export")
    exported = capsys.readouterr().out

    # An import, because what is being asked about is the store: this
    # application has no running server around it, and installing what
    # was written is `apply`, a command of its own (#371).
    assert run("import", "-f", "-", stdin=exported) == 0

    imported = capsys.readouterr()
    assert {line.split(": ")[-1] for line in imported.out.splitlines()} == {"unchanged"}
    run("export")
    assert capsys.readouterr().out == exported


def test_one_entity_exports_as_the_fragment_that_writes_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The per-entity half: a fragment, and the slots that hold a stored
    credential named beside it. The fragment does not say where it goes,
    so neither does the annotation: what writes it is the `set` an
    operator chooses."""
    run = runner(monkeypatch)
    _seed(run)
    capsys.readouterr()

    assert run("mcp-server", "export", "home") == 0

    exported = capsys.readouterr().out
    assert "# One mcp server (mcp_servers.<name>)" in exported
    assert MASK not in exported
    assert "#   headers.Authorization" in exported
    body = yaml.safe_load(exported)
    assert body["headers"] == {"Authorization": HOME_REFERENCE}

    # And it is a fragment: the command whose header it names takes it.
    assert run("mcp-server", "set", "second", "-f", "-", stdin=exported) == 0
    capsys.readouterr()
    run("mcp-server", "show", "second")
    assert document(capsys.readouterr().out)["url"] == "https://example.invalid/mcp"


def test_an_entity_with_no_stored_credential_exports_without_an_annotation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run = runner(monkeypatch)
    _seed(run)
    capsys.readouterr()

    assert run("agent", "export", "sam") == 0

    exported = capsys.readouterr().out
    assert "Stored credentials" not in exported
    assert yaml.safe_load(exported)["prompt"] == "You are Sam."


# A name that begins with a dash
#
# Nothing about a name forbids one: the write path refuses a slash and a
# control character and nothing else, so `--from-env` is a legal
# provider name and `--from-env` is a legal slot. What such a name needs is
# Click's `--`, which ends the options and makes the rest positional,
# and which an operator has to type to write the name in the first
# place. The export renders the command it would take, so the command it
# renders carries the marker too.
#
# The case below EXECUTES the exported argv rather than reading it: what
# is being pinned is that the line an operator pastes works, and a
# rendering that merely looked right is exactly what this replaces.

DASHED_NAME = "--from-env"
DASHED_SLOT = "api_key"


def test_an_exported_command_runs_for_a_name_that_begins_with_a_dash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run = runner(monkeypatch)
    # Written with the marker, because that is the only way to write it.
    assert run("provider", "set", "--", "llm", DASHED_NAME, "type=mock") == 0
    assert run("provider", "secret", "set", "--", "llm", DASHED_NAME, DASHED_SLOT,
               stdin="sk-test-dashed-never-a-real-credential\n") == 0
    capsys.readouterr()

    assert run("export") == 0
    exported = capsys.readouterr().out

    (command,) = _exported_secret_commands(exported)
    assert command == [
        "provider", "secret", "set", "--", "llm", DASHED_NAME, DASHED_SLOT
    ]
    # And it runs, which is the whole of what an exported command is for.
    assert run(*command, stdin="sk-test-dashed-never-a-real-credential\n") == 0
    assert "wrote secret" in capsys.readouterr().out


def test_the_same_command_without_the_marker_does_not_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The guard on the case above: without the marker the grammar reads
    the name as an option and refuses, which is what the export used to
    render."""
    run = runner(monkeypatch)
    run("provider", "set", "--", "llm", DASHED_NAME, "type=mock")
    run("provider", "secret", "set", "--", "llm", DASHED_NAME, DASHED_SLOT, stdin="s3cret\n")
    capsys.readouterr()

    assert run("provider", "secret", "set", "llm", DASHED_NAME, DASHED_SLOT, stdin="s3cret\n") == 1

    assert "run with --help for the grammar" in capsys.readouterr().err
