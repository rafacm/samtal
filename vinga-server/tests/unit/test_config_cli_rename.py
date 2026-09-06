"""The `agent rename` verb, driven end to end through the registered
command.

The transaction is pinned against the repository
(`test_agent_rename.py`), the protocol that covers the writers still in
flight against the store (`test_agent_rename_in_flight.py`), and what
the route answers against the API (`test_config_api_writes.py`). What
is left is the half only this milestone has: the command an operator
types, the request it builds and the two lines it prints.

Three things are asserted here and nowhere else.

- **The request.** A POST to the rename route, the address in its path
  and the new name in a body carrying that one key. Recorded off the
  wire, because a body built from the wrong field would still print a
  plausible acknowledgement.
- **The rendering, one case per boundary arm.** The server's own
  sentence and this client's remedy under it, which is the pairing #386
  landed: the server states what is true of the write and the grammar
  that owns the verb names the command that crosses it. Driven against
  a real server per arm rather than against a planted body, so that the
  sentence and the advice are the ones a deployment actually pairs.
- **The refusals an operator meets at a terminal**, including the
  reachable no-leak case: a new name is caller text, it is typed at
  this verb, and it is echoed in no stream and no log record.
"""

import json
import logging
from pathlib import Path

import httpx
import pytest

from tests.support.config_cli import answering, runner
from tests.support.leaks import renderings
from tests.support.notices import CHECK_IN, RELOAD, STORE_BOOT, boundaries
from vinga_server.config import cli, entities
from vinga_server.config.responses import Applies

# A name carrying a URL credential, which is the shape a paste has. It
# is the reachable no-leak case at this verb: the new name is what the
# operator typed, and such a URL holds a slash, which is what the
# addressability rule refuses.
PASTED = "sk-test-3b9e1c07-never-a-real-credential"

PASTED_NAME = f"https://user:{PASTED}@example.invalid/agent"

BOARD = "aa:bb:cc:dd:ee:ff"


@pytest.fixture
def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    return runner(monkeypatch)


def out(run, capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    capsys.readouterr()
    code = run(*argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def pipeline(run) -> None:
    """A working configuration, written the way a first deployment
    writes one: the providers, the defaults over them, and the agent."""
    llm = "type: anthropic\nmodel: m\n"
    assert run("provider", "set", "llm", "claude", "-f", "-", stdin=llm) == 0
    assert run("provider", "set", "asr", "ears", "-f", "-", stdin="type: mock\n") == 0
    assert run("agent-defaults", "set", "-f", "-", stdin="llm: claude\nasr: ears\n") == 0
    assert run("agent", "set", "sam", "-f", "-", stdin="prompt: You are Sam.\n") == 0


# What the command sends


# What a rename is answered with when what is under test is the request
# rather than the answer: the shape the route composes, with the names
# the one case that reads it renames between.
ACKNOWLEDGED: dict[str, object] = {
    "wrote": "agent sam renamed to poet",
    "notice": entities.APPLY_NOTICE.sentence,
    "applies": [Applies.RELOAD.value],
}


def recording(run) -> list[httpx.Request]:
    """Every request this runner makes, recorded and answered as the
    route answers one.

    A mock transport rather than the application the other cases build,
    because what is claimed here is what went out, and the transport is
    the only place that can be seen.
    """
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json=ACKNOWLEDGED)

    answering(run, handler)
    return sent


def test_the_verb_sends_one_post_carrying_the_new_name_as_it_was_typed(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The request, recorded off the wire.

    One request, addressed at the agent by the name it has, with the
    name it is to have in a body of exactly one key. The address is
    percent-encoded per segment, so a name a shell would let through
    reaches the route as one segment; the new name is in the body
    rather than in the target, which is also what keeps it out of every
    proxy log between here and the server.

    The name is typed with whitespace around it, which is what makes
    "as it was typed" an assertion rather than a sentence: every value
    that survives a trim unchanged would pass this case with a
    `strip()` in the body builder, and a client quietly editing what an
    operator typed is exactly what must not happen. What the SERVER
    makes of the padding is the server's own business and is pinned
    where that decision lives (`test_config_api_writes.py`): it strips
    like every other path that stores a name, answers 200, and files
    the row under the trimmed one, which is why the acknowledgement
    this transport hands back names the trimmed one.
    """
    sent = recording(run)

    code, printed, _ = out(run, capsys, "agent", "rename", "sam", "  poet  ")

    assert (code, printed) == (0, "wrote agent sam renamed to poet\n")
    assert len(sent) == 1
    assert sent[0].method == "POST"
    assert sent[0].url.raw_path.endswith(b"/agents/sam/rename")
    assert sent[0].url.query == b""
    # The body as a shape rather than as bytes: what is claimed is the
    # one key, nothing beside it, and the value unedited, while how a
    # JSON encoder spaces a pair is that library's business.
    assert json.loads(sent[0].content) == {"to": "  poet  "}


def test_a_name_a_path_cannot_hold_travels_as_one_segment(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The addressing half of the same claim, on the names that make it
    worth asserting: a space and a character outside ASCII are lawful
    agent names, and each is one segment of the path rather than
    something a server would read as two."""
    sent = recording(run)

    assert out(run, capsys, "agent", "rename", "the poet", "skáld")[0] == 0

    # The target as it travels, not as httpx decodes it back: what a
    # server and every log between here and it read is the encoded
    # form, and that is where one segment is one segment.
    assert sent[0].url.raw_path.endswith(b"/agents/the%20poet/rename")
    assert json.loads(sent[0].content) == {"to": "skáld"}


# The three boundary arms, each through a server that chooses it


def test_a_rename_that_moved_the_row_alone_advises_the_install(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """Nothing live moved, so the rename waits where every other write
    of this kind waits, and what an operator reads is two voices in two
    lines: the server's sentence about the write, and this client's
    advice about the boundary it states."""
    pipeline(run)

    code, printed, err = out(run, capsys, "agent", "rename", "sam", "poet")

    assert (code, printed) == (0, "wrote agent sam renamed to poet\n")
    assert boundaries(err) == {RELOAD}
    assert err.splitlines() == [
        entities.APPLY_NOTICE.sentence,
        cli.REMEDIES[frozenset({Applies.RELOAD})],
    ]


@pytest.mark.parametrize("live", ["binding", "default"])
def test_a_rename_that_moved_a_live_reference_advises_both_boundaries(
    run, capsys: pytest.CaptureFixture[str], live: str
) -> None:
    """A row a running server re-reads as a device asks moved with the
    agent, so the rename is waiting at two boundaries at once and the
    sentence is the one written for a rename rather than the one
    written for a binding."""
    pipeline(run)
    if live == "binding":
        assert run("device", "bind", BOARD, "sam") == 0
    else:
        assert run("default-agent", "set", "sam") == 0

    code, printed, err = out(run, capsys, "agent", "rename", "sam", "poet")

    assert (code, printed) == (0, "wrote agent sam renamed to poet\n")
    assert boundaries(err) == {RELOAD, CHECK_IN}
    assert err.splitlines() == [
        entities.RENAME_UNSERVED_NOTICE.sentence,
        cli.REMEDIES[frozenset({Applies.RELOAD, Applies.CHECK_IN})],
    ]


def test_a_rename_against_a_handed_configuration_prints_the_sentence_alone(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The third arm, which is the server's mode rather than the
    rename's: nothing this server serves reads the store, so what the
    write can promise is that the rows are stored.

    One line and not two, and that is the client's half of the same
    rule: no command of this grammar crosses a store boot, so there is
    nothing to advise and the server's sentence stands alone rather
    than being guessed at.
    """
    pipeline(run)
    run.runtime["snapshot_only"] = True

    code, printed, err = out(run, capsys, "agent", "rename", "sam", "poet")

    assert (code, printed) == (0, "wrote agent sam renamed to poet\n")
    assert boundaries(err) == {STORE_BOOT}
    assert err.splitlines() == [entities.SNAPSHOT_NOTICE.sentence]
    assert cli.INSTALLS not in err


# What an operator meets when it is refused


def test_an_occupied_destination_is_refused_without_naming_either_name(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The conflict, as the terminal reads it: the repository's own
    sentence, exit 1, and nothing on stdout.

    Neither name is in it, which is the rule the refusals keep: the old
    one is a stored identity and the new one is what this caller just
    typed, and a refusal that quoted the pair would be a refusal that
    can leak one of them.
    """
    pipeline(run)
    assert run("agent", "set", "poet", "-f", "-", stdin="prompt: You are a poet.\n") == 0

    code, printed, err = out(run, capsys, "agent", "rename", "sam", "poet")

    assert (code, printed) == (1, "")
    assert "sam" not in err
    assert "poet" not in err
    assert "Traceback" not in err


def test_renaming_an_agent_that_is_not_there_is_refused(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """Run twice, the second run finds nothing under the old name,
    which is what a POST promises rather than hides."""
    pipeline(run)
    assert run("agent", "rename", "sam", "poet") == 0

    code, printed, err = out(run, capsys, "agent", "rename", "sam", "poet")

    assert (code, printed) == (1, "")
    assert "agents" in err


def test_a_new_name_carrying_a_credential_is_refused_and_never_echoed(
    run, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    """The reachable no-leak case, on the door a paste lands in.

    The new name is caller text: it arrived on this command line, no
    row holds it, and it is refused for the slash such a URL carries.
    So it is echoed in neither stream and in no log record, read the
    way `tests.support.leaks` reads one: both formats a deployment
    writes with, and then the record itself, its attribute dictionary,
    its unformatted arguments and any exception on it. That third
    reading is the one the formatters cannot give, and it is where a
    value that never reached a message would still be sitting.
    """
    pipeline(run)

    with caplog.at_level(logging.DEBUG):
        code, printed, err = out(run, capsys, "agent", "rename", "sam", PASTED_NAME)

    assert code == 1
    for rendering in (printed, err, *renderings(caplog)):
        assert PASTED not in rendering
        assert PASTED_NAME not in rendering


def test_the_verb_asks_nothing_before_it_renames(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """No confirmation, which is a decision rather than an omission: a
    rename is undone by running it back with what the operator still
    has in their shell history, and the destination refusals are what
    keep that true.

    Driven with `--no-input`, which is what a destructive verb is
    refused by: this one runs, and the row is what decides that
    (`Command.destroys`).
    """
    pipeline(run)

    code, printed, _ = out(run, capsys, "--no-input", "agent", "rename", "sam", "poet")

    assert (code, printed) == (0, "wrote agent sam renamed to poet\n")
    assert not [row for row in cli.COMMANDS if row.words == ("agent", "rename")][0].destroys
