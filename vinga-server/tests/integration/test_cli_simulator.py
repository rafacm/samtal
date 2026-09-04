"""`vinga simulator run --claim`, from an unclaimed board to a spoken
reply, against a real server.

The CLI lanes next door share one live server, and that server is right
for what they ask: it boots fileless on an empty database, so no agent is
servable and every board is unbound. A conversation needs the opposite,
which is why this lane is its own file: the app here is built from a
`Config` with mock providers in it, the way `test_device_simulator.py`
builds one, and served on a thread so the synchronous CLI can talk to it.

**It starts in `Activating` deliberately.** There is no default agent, so
the first check-in offers a code and hands back an empty token, and the
run traverses all four steps of the ceremony: check in and read a code,
claim through the act the grammar already has, poll where a waiting board
polls, and check in AGAIN. The fourth step is the one that makes the
other three worth anything, because the poll route answers a status and
the only thing that mints a token is a check-in reply. A case that
started from an already servable agent would pass with that step missing,
which is why the starting state is part of the assertion and why the case
below removes the step and watches the handshake fail.

**One case starts from the opposite deployment on purpose.** Device
authentication off with a default agent set is what #369 was reported
from: the board is admitted at its first check-in and handed an empty
token, because there is no credential to hand it, and the reply is
otherwise byte for byte the one a board nobody will admit receives. That
case gets its own server, since every other case here needs
authentication on to have any bite at all.
"""

import threading
from collections.abc import Callable, Iterator
from dataclasses import replace

import pytest

from tests.integration.conftest import booted
from tests.support.config_cli import API_SECRET_ENV
from tests.support.deployment import Live, served
from vinga_server.config import Config, cli
from vinga_server.config.secrets import MASTER_KEY_ENV, generate_key
from vinga_server.ota import OTA_PATH
from vinga_server.simulator import board, conversation, utterance

# Not a real credential, and shaped so a substring check for it cannot
# match by accident.
SECRET = "sk-simlane-8d2c47-never-a-real-credential"

# The agent the claim binds this board to. Named rather than default:
# `default_agent` is deliberately unset, because a board covered by one
# is answered as a configured device and mints no code at all, and a code
# is what the whole ceremony hangs off.
AGENT = "assistant"

# What the mock providers answer, so the assertions are about words this
# deployment chose rather than about there being some words.
HEARD = "hello from a simulated board"

REPLY = f"You said {HEARD}."

# Some other board, bound to the agent so the configuration is valid
# without a default agent.
#
# `default_agent` is what a validator would otherwise ask for here, and
# setting it would answer the simulated board as a configured device and
# mint no code at all, which would take the ceremony's first step away.
# A binding to a MAC nothing in this file uses satisfies the rule and
# leaves the board this lane is about unclaimed.
SOMEBODY_ELSE = "aa:bb:cc:dd:ee:99"


def deployment(**overrides: object) -> Config:
    """Mock providers, one agent, and nothing bound to anybody."""
    return Config(
        **(
            {
                "providers": {
                    "llm": {"mock": {"type": "mock", "reply": "You said {text}."}},
                    "asr": {"mock": {"type": "mock", "text": HEARD}},
                    "tts": {"mock": {"type": "mock"}},
                    "vad": {"mock": {"type": "mock"}},
                },
                "agent_defaults": dict.fromkeys(("llm", "asr", "tts", "vad"), "mock"),
                "agents": {AGENT: {"prompt": "You are an assistant."}},
                "devices": {SOMEBODY_ELSE: [AGENT]},
            }
            | overrides
        )
    )


def open_deployment() -> Config:
    """The deployment #369 was reported from: device authentication off
    for a trial on a trusted network, and a default agent covering every
    board that arrives.

    The two together are what makes the reply ambiguous on the wire. The
    default agent means this board resolves to something to talk to, so
    it is admitted and is offered no code; the auth setting means there
    is no credential to hand it, so the token beside that admission is
    the same empty string a board nothing resolves is turned away with.
    """
    return deployment(default_agent=AGENT, server={"auth": {"enabled": False}})


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch) -> Iterator[Live]:
    """A real server holding a conversation-capable deployment.

    Per test rather than per module, because every case here is about a
    board arriving unclaimed and a claim is not undone: a second case
    against the same store would start from `Admitted` and prove
    something else. The lane clears the store between tests, which is
    what makes "per test" true of the rows as well as of the server.
    """
    monkeypatch.setenv(MASTER_KEY_ENV, generate_key())
    monkeypatch.setenv(API_SECRET_ENV, SECRET)
    monkeypatch.delenv("VINGA_CONFIG", raising=False)
    with served(booted(deployment())) as running:
        monkeypatch.setenv(cli.API_URL_ENV, running.api_url)
        yield running


@pytest.fixture
def open_live(monkeypatch: pytest.MonkeyPatch) -> Iterator[Live]:
    """The same server, on the deployment that issues no device tokens.

    A fixture of its own rather than a parameter on the one above,
    because every other case in this file needs authentication ON: what
    they are about is a token opening a socket and a doctored one being
    refused, and both lose their bite on a deployment that asks for
    none.
    """
    monkeypatch.setenv(MASTER_KEY_ENV, generate_key())
    monkeypatch.setenv(API_SECRET_ENV, SECRET)
    monkeypatch.delenv("VINGA_CONFIG", raising=False)
    with served(booted(open_deployment())) as running:
        monkeypatch.setenv(cli.API_URL_ENV, running.api_url)
        yield running


@pytest.fixture
def checked_in(monkeypatch: pytest.MonkeyPatch) -> list[board.CheckIn]:
    """Every check-in the ceremony makes, in order.

    Recorded through the module's own public name, which is what the
    grammar calls, so what this holds is the ceremony's real replies
    rather than a description of them. The fourth step is invisible from
    the outside otherwise: a run that used the first reply's token would
    print exactly the same lines up to the handshake.
    """
    return _recording(monkeypatch, lambda state: state)


def _recording(
    monkeypatch: pytest.MonkeyPatch, doctor: Callable[[board.CheckIn], board.CheckIn]
) -> list[board.CheckIn]:
    """The check-ins, with each reply passed through `doctor` on its way
    back to the grammar."""
    seen: list[board.CheckIn] = []
    real = board.check_in

    def recorded(*arguments: object, **named: object) -> board.CheckIn:
        state = real(*arguments, **named)  # type: ignore[arg-type]
        seen.append(state)
        return doctor(state)

    monkeypatch.setattr(board, "check_in", recorded)
    return seen


def ran(live: Live, *arguments: str) -> int:
    return cli.main(["simulator", "run", f"{live.origin}{OTA_PATH}", *arguments])


def test_a_deployment_that_issues_no_tokens_holds_the_whole_conversation(
    open_live: Live, checked_in: list[board.CheckIn], capsys: pytest.CaptureFixture[str]
) -> None:
    """The issue's own reproduction, as a test.

    Device authentication off, a default agent set, and `simulator run`
    with no `--claim` at all: one check-in, admitted with an empty token
    because there is no credential to be had, and a whole conversation
    on it. Before #369 this deployment answered a reply byte for byte
    identical to the one a board nobody claimed gets, and the command
    refused to open a socket the server would have accepted.

    Held to going red by the reading alone: without the word in the
    reply, the single check-in below is `Unwelcome` and the command
    leaves with `CANNOT_CONVERSE` before anything is opened.
    """
    assert ran(open_live) == 0

    [only] = checked_in
    assert isinstance(only, board.Admitted)
    assert only.token == "", "the deployment issued a credential, so this is the auth-on case"

    said = capsys.readouterr()
    assert f"heard: {HEARD}" in said.out
    assert f"said: {REPLY}" in said.out
    assert f"the conversation reached: {conversation.CLOSED}" in said.out
    assert conversation.CLOSE_NAMES[1000] in said.out
    assert "out of order:" not in said.err


def test_an_unclaimed_board_is_claimed_and_then_holds_a_conversation(
    live: Live, checked_in: list[board.CheckIn], capsys: pytest.CaptureFixture[str]
) -> None:
    """The whole thing, end to end, from a board this deployment has
    never seen.

    Four steps and then a socket. What is asserted about the ceremony is
    the SHAPE the plan's finding 4 corrected: two check-ins, the first
    activating with an empty token, the second admitted with a real one,
    and the conversation held on the second's.
    """
    assert ran(live, "--claim", AGENT) == 0

    first, second = checked_in
    assert isinstance(first, board.Activating) and first.code.isdigit()
    assert isinstance(second, board.Admitted) and second.token

    said = capsys.readouterr()
    assert f"heard: {HEARD}" in said.out
    assert f"said: {REPLY}" in said.out
    assert "reply: " in said.out
    # The eighth state, reported rather than advertised: a turn against a
    # real server ends with the socket given back, and the verdict says
    # so in the machine's own word for it.
    assert f"the conversation reached: {conversation.CLOSED}" in said.out
    assert conversation.CLOSE_NAMES[1000] in said.out
    assert "out of order:" not in said.err


def test_the_conversation_is_held_on_the_second_reply_s_token(
    live: Live,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The bite behind the case above, and the reason the fourth step
    exists at all.

    An activating check-in's token is empty and the poll route answers a
    status, so a client that opened the socket with what step one handed
    it would be presenting nothing. Here the second reply is doctored
    back to that empty token, which is exactly what removing the
    re-check-in produces, and the handshake is refused: the server closes
    before accepting, which a client meets as a failed upgrade.

    Nothing of the refusal names the address, which is what the token
    would have been sent to.
    """
    seen = _recording(
        monkeypatch,
        lambda state: replace(state, token="") if isinstance(state, board.Admitted) else state,
    )

    with caplog.at_level(0):
        assert ran(live, "--claim", AGENT) == 1

    first, second = seen
    assert isinstance(first, board.Activating)
    assert isinstance(second, board.Admitted) and second.token, (
        "the ceremony stopped minting a token, so this case is no longer about the handshake"
    )
    said = capsys.readouterr()
    # The last line, because the claim happened and said so first.
    assert said.err.strip().splitlines()[-1].startswith("cannot open a conversation with ")
    assert live.origin not in said.err
    # And the server's own word for what it refused, which is the exact
    # confusion `docs/xiaozhi-notes.md` warns about from the other side.
    assert "no_token" in caplog.text


def test_a_board_nobody_claimed_is_refused_rather_than_reported(
    live: Live, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without `--claim`, nothing binds this board, so there is no
    conversation to hold.

    The two verbs disagree about this reply on purpose: `check-in`
    reports the code and exits 0, because reporting the state a board is
    in is its answer, and `run` was asked for a conversation.
    """
    assert ran(live) == 1

    assert capsys.readouterr().err.strip() == cli.CANNOT_CONVERSE


def test_the_utterance_is_paced_rather_than_burst(
    live: Live, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one thing this lane can say about pacing that a unit case
    cannot: the packets are delivered over a real socket to a real
    endpointer, and the run still finishes.

    The wall time is not asserted, because a runner's is nobody's to
    predict. What is asserted is that every packet was waited for, all on
    one thread, and that the waits add up to the utterance's own length,
    which is what "the way a microphone delivers them" means.
    """
    slept: list[tuple[int, float]] = []
    real = conversation.sleep

    def paced(seconds: float) -> None:
        slept.append((threading.get_ident(), seconds))
        real(seconds)

    monkeypatch.setattr(conversation, "sleep", paced)
    said = utterance.packaged()

    assert ran(live, "--claim", AGENT) == 0

    assert len(slept) == len(said.packets)
    assert len({thread for thread, _ in slept}) == 1
    assert sum(seconds for _, seconds in slept) == pytest.approx(said.duration_ms / 1000)
