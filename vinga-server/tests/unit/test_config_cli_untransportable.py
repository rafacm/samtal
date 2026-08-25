"""What a fragment JSON cannot carry is told, and what it is not told.

The refusal that runs in front of validation is the one with the least
to go on: it is reached with a value nothing in this deployment has
looked at, in a document whose keys are bytes somebody wrote, addressed
by an identity that arrived on the command line. Every one of those is
a place a credential lands when it is pasted a line early, and the
mistake that produces this refusal most often is exactly that paste.

So the sentence names a fixed section, a fixed word for a field, and
list positions, and nothing else. This file plants a distinct
credential-shaped value in every field that could carry one, one per
field so a leak names its own source, and asserts absence on every
surface the refusal could reach: the two streams, the request the
client would have sent (its body and its headers), the log records
whole, and the exception chain a walker would find.

The request recorder is vacuous today, and deliberately kept: the check
runs before anything is sent, so there is nothing in it. If that order
ever changed, the fragment would be in a body and this is what would
say so.
"""

import json
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest

from tests.support.config_cli import chain, logged, runner
from vinga_server.config import cli, entities, transport
from vinga_server.config.loader import ConfigError

# One per field, none a real credential, each shaped so a substring
# check for it cannot match by accident.
STAGE_SENTINEL = "sk-stage-0d1e2f-never-a-real-credential"
NAME_SENTINEL = "sk-name-3a4b5c-never-a-real-credential"
KEY_SENTINEL = "api_key_sk-key-6d7e8f-never-a-real-credential"
NESTED_KEY_SENTINEL = "token_sk-nested-9a0b1c-never-a-real-credential"
VALUE_SENTINEL = "sk-value-2d3e4f-never-a-real-credential"

SENTINELS = (
    STAGE_SENTINEL,
    NAME_SENTINEL,
    KEY_SENTINEL,
    NESTED_KEY_SENTINEL,
    VALUE_SENTINEL,
)


@pytest.fixture
def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., int]]:
    """One command, against a server of this test's own, with every
    request it would have sent recorded."""
    driver = runner(tmp_path, monkeypatch)
    sent: list[httpx.Request] = []
    built = cli.build_client

    def recording(base_url: str, token: str) -> object:
        client = built(base_url, token)
        client.event_hooks["request"] = [sent.append]
        return client

    monkeypatch.setattr(cli, "build_client", recording)
    driver.sent = sent
    yield driver


def _requests(sent: list[httpx.Request]) -> str:
    """Everything the client put on the wire, as anything reading it
    would: the method and the URL, every header including the bearer
    token's own, and the body."""
    return "\n".join(
        "\n".join(
            [
                f"{request.method} {request.url}",
                "\n".join(f"{name}: {value}" for name, value in request.headers.items()),
                request.content.decode("utf-8", "replace"),
            ]
        )
        for request in sent
    )


# A fragment carrying an untransportable value under a credential-shaped
# key, at two depths: directly, and inside a list, which is the path a
# structural position has to describe.
FLAT = f"type: anthropic\n{KEY_SENTINEL}: 2026-01-01\n"
NESTED = (
    "type: anthropic\n"
    "options:\n"
    "  - a: 1\n"
    f"  - {NESTED_KEY_SENTINEL}: 2026-01-01\n"
)
UNDER_A_VALUE = f"type: anthropic\n{KEY_SENTINEL}:\n  - !!binary |\n    AAEC\n"


@pytest.mark.parametrize(
    ("what", "fragment"),
    [("a flat key", FLAT), ("inside a list", NESTED), ("under a value", UNDER_A_VALUE)],
)
def test_the_refusal_names_no_key_and_no_identity(
    run: Callable[..., int],
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    what: str,
    fragment: str,
) -> None:
    with caplog.at_level(0):
        exit_code = run(
            "provider", "set", STAGE_SENTINEL, NAME_SENTINEL, "-f", "-", stdin=fragment
        )

    assert exit_code == 1, what
    captured = capsys.readouterr()
    assert "JSON has no way to write" in captured.err, (what, captured.err)
    assert "Traceback" not in captured.err, what

    surfaces = {
        "stdout": captured.out,
        "stderr": captured.err,
        "requests": _requests(run.sent),
        "logs": logged(caplog),
    }
    for sentinel in SENTINELS:
        for where, surface in surfaces.items():
            assert sentinel not in surface, (what, where, sentinel)

    # And nothing was sent at all, which is why the request surface
    # above is empty rather than merely clean.
    assert run.sent == [], what


def test_the_refusal_says_where_structurally(
    run: Callable[..., int], capsys: pytest.CaptureFixture[str]
) -> None:
    """What is left after the keys go: the section, the fixed field
    word, and the positions. An operator can count the steps to the
    value; a log holding the line holds nothing of it."""
    assert run("provider", "set", "llm", "claude", "-f", "-", stdin=NESTED) == 1

    said = capsys.readouterr().err
    assert said.startswith("invalid providers:"), said
    # `options` is a field, its second entry is position 1, and the key
    # under that entry is a field again.
    assert f"the fragment.{transport.FIELD}.1.{transport.FIELD}" in said, said


def test_the_section_is_the_whole_of_what_is_addressed(
    run: Callable[..., int], capsys: pytest.CaptureFixture[str]
) -> None:
    """The refusal names the section and stops. It used to name
    `providers.<stage>.<name>`, built out of the two words the command
    line carried."""
    assert run("provider", "set", STAGE_SENTINEL, NAME_SENTINEL, "-f", "-", stdin=FLAT) == 1

    said = capsys.readouterr().err
    assert said.startswith("invalid providers:"), said
    assert "providers." not in said, said


def test_the_refusal_carries_no_chain_holding_the_fragment() -> None:
    """The fourth surface, read where a walker would find it. Driven at
    the boundary because `cli.main` prints the sentence and returns, so
    the exception is not reachable through it."""
    fragment = {
        KEY_SENTINEL: {NESTED_KEY_SENTINEL: {VALUE_SENTINEL}},
    }

    with pytest.raises(ConfigError) as refused:
        transport.check_transportable("providers", fragment)

    walked = chain(refused.value)
    for sentinel in (KEY_SENTINEL, NESTED_KEY_SENTINEL, VALUE_SENTINEL):
        assert sentinel not in walked, sentinel
    assert refused.value.__cause__ is None
    assert refused.value.__context__ is None


def test_the_stored_row_walk_names_no_key_either() -> None:
    """The other caller of the same walk. A stored row is asked about
    its numbers only, and the path it reports is built the same way, so
    a hand-edited row holding a credential-shaped key does not put that
    key in a storage failure."""
    problem = transport.untransportable(
        {KEY_SENTINEL: [{NESTED_KEY_SENTINEL: float("nan")}]}, numbers_only=True
    )

    assert problem is not None
    assert "not a finite number" in problem
    assert KEY_SENTINEL not in problem
    assert NESTED_KEY_SENTINEL not in problem
    assert f"the fragment.{transport.FIELD}.0.{transport.FIELD}" in problem


def test_every_caller_names_a_fixed_section() -> None:
    """The vocabulary, held closed.

    The sentence is only as safe as what is put in front of it, so what
    every call site may pass is enumerated: the applied document's own
    word, and the five sections the registry declares. An address built
    from an identity is none of them.
    """
    allowed = {transport.APPLY_LOCATION} | {kind.moved_key for kind in entities.ENTITIES}

    assert transport.APPLY_LOCATION == "document"
    assert "providers" in allowed and "mcp_servers" in allowed
    # Every one is one word of this repository's own, with no separator
    # an addressed form would have needed.
    for section in allowed:
        assert "." not in section, section


def test_a_fragment_json_can_carry_still_travels(
    run: Callable[..., int], capsys: pytest.CaptureFixture[str]
) -> None:
    """The other half: this check refuses what JSON cannot say and
    nothing else, so a credential-shaped key holding an ordinary string
    is written and sent."""
    fragment = f"type: anthropic\n{KEY_SENTINEL}_env: SOME_VARIABLE\n"

    assert run("provider", "set", "llm", "claude", "-f", "-", stdin=fragment) == 0
    capsys.readouterr()
    assert run.sent, "nothing was sent"
    body = json.loads(run.sent[-1].content)
    assert f"{KEY_SENTINEL}_env" in body
