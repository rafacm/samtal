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
from tests.support.problems import problem
from tests.support.stores import planted
from vinga_server import logs
from vinga_server.config.api import build_api
from vinga_server.config.loader import ConfigError
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
def directory(tmp_path: Path) -> Path:
    return tmp_path / "db"


@pytest.fixture
def keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MASTER_KEY_ENV, generate_key())


@pytest.fixture
def store(directory: Path, keys: None) -> Iterator[ConfigStore]:
    """A second view of the same database, for reading a value back
    through the repository rather than through the display that masked
    it."""
    engine = open_database(directory)
    try:
        yield ConfigStore(engine, load_keys())
    finally:
        engine.dispose()


@pytest.fixture
def api(directory: Path, keys: None) -> FastAPI:
    return build_api(TOKEN, directory)


@pytest.fixture
def client(api: FastAPI) -> Iterator[TestClient]:
    """Entered, so the requests reach a real repository: this whole file
    is about what a real read and a real write do to one another."""
    with TestClient(api, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        yield client


@pytest.fixture
def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The CLI as its entry point runs it, against a server of its own."""
    return runner(tmp_path, monkeypatch)


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


# A mask with nothing behind it


NOTHING_STORED_FIELD = (
    f'"api_key_env" holds the mask {MASK}, which a write reads as keep the stored '
    f"value, and nothing is stored there; write the value it should hold, or leave "
    f"the field out"
)

NOTHING_STORED_KEY = (
    f"a key holds the mask {MASK}, which a write reads as keep the stored value, and "
    f"nothing is stored there; write the value it should hold, or leave the key out. "
    f"The key is not quoted back"
)

NOTHING_STORED_IN_ENV = (
    f"a key in env holds the mask {MASK}, which a write reads as keep the stored "
    f"value, and nothing is stored there; write the value it should hold, or leave "
    f"the key out. The key is not quoted back"
)


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
    assert response.json() == problem(
        422,
        f"invalid providers.llm.fresh:\n  - {NOTHING_STORED_FIELD}",
        [("/api_key_env", NOTHING_STORED_FIELD)],
    )
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
    assert response.json() == problem(
        422,
        f"invalid providers.llm.claude:\n  - {NOTHING_STORED_KEY}",
        [("", NOTHING_STORED_KEY)],
    )
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
    assert response.json() == problem(
        422,
        f"invalid mcp_servers.home:\n  - {NOTHING_STORED_IN_ENV}",
        [("/env", NOTHING_STORED_IN_ENV)],
    )
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
        .values(api_key_env=PASTED),
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
    assert refused.json()["detail"] == f"invalid providers.llm.claude:\n  - {NOTHING_STORED_KEY}"
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
            "set",
            "provider",
            "llm",
            "claude",
            "-f",
            "-",
            stdin=f"type: anthropic\nmodel: m\nconnection:\n  api_key_env: {PASTED}\n",
        )
        == 0
    )
    capsys.readouterr()

    run("show", "provider", "llm", "claude")
    shown = capsys.readouterr().out
    printed = document(shown)
    assert printed["connection"] == {"api_key_env": MASK}
    assert PASTED not in shown

    assert (
        run("set", "provider", "llm", "claude", "-f", "-", stdin=yaml.safe_dump(printed)) == 0
    )

    capsys.readouterr()
    run("show", "provider", "llm", "claude")
    assert document(capsys.readouterr().out) == printed
