"""The simulator's DEVICE-SIDE half, against a far side a case controls.

Both verbs, because both make the same check-in and read the same reply;
`run`'s own section is at the foot of this file, and what is in it is
what belongs to `run` alone.

The live lane drives this command against a real server, which is the
compatibility claim only a real server can make. It cannot make any of
the claims below, and that is the point of this file rather than an
overlap with it: a correct vinga-server never answers a check-in with a
307, with an `activation` beside a token, with a token that is a number,
or with a websocket URL carrying a password, and every one of those is a
shape this client has to have a decided answer to.

So the far side here is `httpx.MockTransport` behind the command's own
client seam, which records every request it was given and answers
whatever the case says. What that buys is the two halves a real server
cannot supply: the hostile reply table, and the exact request targets of
a ceremony driven through an address carrying a secret path segment and
a query string.
"""

import json
import logging
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest

from tests.support.config_cli import API_SECRET_ENV, chain, logged, runner
from vinga_server import device_endpoint
from vinga_server.config import cli
from vinga_server.config.loader import ConfigError
from vinga_server.config.models import NOT_A_MAC, DatabaseConfig
from vinga_server.config.store import ALREADY_BOUND, ConfigStore
from vinga_server.db import open_database
from vinga_server.device_endpoint import SUPPLIED_ENDPOINT
from vinga_server.simulator import board, utterance

# The path segment in front of an OTA endpoint is the whole protection a
# deployment with onboarding turned off has, and the query string is the
# other place a credential is written into a URL. Both are planted, so
# that every assertion about what is not printed is about something.
SEGMENT = "AB2C4D5E-never-a-real-path-key"

QUERY_SECRET = "qtok-2f9a41-never-a-real-credential"

# What the far side hands back that nobody typed: the device token, and
# the websocket URL that decides where it would be sent.
DEVICE_TOKEN = "dev-tok-7b31e9-never-a-real-credential"

PASTED = "hunter2-never-a-real-password-9c3f"

URL = f"https://voice.example/x/{SEGMENT}/?token={QUERY_SECRET}"

ACTIVATION_URL = f"https://voice.example/x/{SEGMENT}/activate?token={QUERY_SECRET}"

CODE = "659505"


class Canned:
    """A far side that answers what a case tells it to and records what
    it was asked.

    Answers are functions of the request rather than responses, because a
    response is read once and several of these cases send more than one
    request. The last answer repeats, so a ceremony that polls ten times
    is one entry rather than ten.
    """

    def __init__(self, *answers: Callable[[httpx.Request], httpx.Response]) -> None:
        self.answers = list(answers)
        self.requests: list[httpx.Request] = []

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.answers[min(len(self.requests) - 1, len(self.answers) - 1)](request)

    def client(self, url: str) -> httpx.Client:
        # The bounds are the production ones, because what this seam
        # replaces is where a request goes and not how long it may take:
        # a client built with httpx's own defaults would make every
        # assertion about a timeout an assertion about the library.
        return httpx.Client(
            base_url=url,
            transport=httpx.MockTransport(self._handle),
            timeout=httpx.Timeout(
                device_endpoint.READ_TIMEOUT_S, connect=device_endpoint.CONNECT_TIMEOUT_S
            ),
        )

    def targets(self) -> list[str]:
        return [str(request.url) for request in self.requests]

    def headers(self, name: str) -> list[str]:
        return [request.headers.get(name, "") for request in self.requests]

    def bodies(self) -> list[object]:
        return [json.loads(request.content or b"null") for request in self.requests]


def answering(
    status: int = 200, body: object = None, text: str | None = None, **headers: str
) -> Callable[[httpx.Request], httpx.Response]:
    """One canned answer, built fresh for every request that meets it."""

    def answer(request: httpx.Request) -> httpx.Response:
        if text is not None:
            return httpx.Response(status, text=text, headers=headers)
        return httpx.Response(status, json=body, headers=headers)

    return answer


def activating(code: str = CODE, **overrides: object) -> dict[str, object]:
    """What a deployment answers a board it has never seen."""
    activation: dict[str, object] = {
        "message": f"voice.example\n{code}",
        "code": code,
        "challenge": "02:00:00:00:00:01",
        "timeout_ms": 30000,
    }
    activation.update(overrides)
    return {
        "activation": activation,
        "server_time": {"timestamp": 0, "timezone_offset": 0},
        "firmware": {"version": "0.1.0", "url": ""},
        "websocket": {"url": "wss://voice.example/xiaozhi/v1/", "token": "", "version": 1},
    }


def admitted(url: str = "wss://voice.example/xiaozhi/v1/", **websocket: object) -> dict:
    """What a deployment answers a board it will serve."""
    block: dict[str, object] = {"url": url, "token": DEVICE_TOKEN, "version": 1}
    block.update(websocket)
    return {
        "server_time": {"timestamp": 0, "timezone_offset": 0},
        "firmware": {"version": "0.1.0", "url": ""},
        "websocket": block,
    }


def unwelcome() -> dict[str, object]:
    """200 OK, no token and no activation: the state that costs an
    evening."""
    return {
        "server_time": {"timestamp": 0, "timezone_offset": 0},
        "firmware": {"version": "0.1.0", "url": ""},
        "websocket": {"url": "wss://voice.example/xiaozhi/v1/", "token": "", "version": 1},
    }


# What a server that says why the token is empty answers (#369)
#
# Siblings of the three above rather than edits to them. Those three are
# the OLD-server bodies, byte for byte, which is what keeps the
# fallback-to-the-token-rule reading covered by every case that uses
# them; these carry the word, and a case that is about the word says so
# by which helper it calls.


def admitted_without_a_token(**overrides: object) -> dict[str, object]:
    """Admitted, and this deployment issues no device tokens at all: the
    reply the whole of #369 is about."""
    return {**unwelcome(), "access": board.ACCESS_OPEN, **overrides}


def turned_away(**overrides: object) -> dict[str, object]:
    """Not admitted, said in the reply rather than left to be inferred
    from an empty token."""
    return {**unwelcome(), "access": board.ACCESS_DENIED, **overrides}


def admitted_with_a_token(**overrides: object) -> dict[str, object]:
    """Admitted, and the credential beside it is what to present."""
    return {**admitted(), "access": board.ACCESS_TOKEN, **overrides}


@pytest.fixture
def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """One command run the way the entry point runs it, against a
    configuration API of this test's own."""
    return runner(monkeypatch)


@pytest.fixture
def far_side(monkeypatch: pytest.MonkeyPatch) -> Callable[..., Canned]:
    """The device-facing endpoint, replaced at the command's own seam.

    `board.build_client` and not `device_endpoint.build_client`: the name
    imported into the module under test is the seam, which is what keeps
    a suite driving one command from replacing the doctor's client too.
    """

    def canned(*answers: Callable[[httpx.Request], httpx.Response]) -> Canned:
        endpoint = Canned(*answers)
        monkeypatch.setattr(board, "build_client", endpoint.client)
        return endpoint

    return canned


class Clock:
    """Time as this file's cases hold it: what was slept, and a hand to
    move it by anything else that takes time."""

    def __init__(self) -> None:
        self.slept: list[float] = []
        self.now = 0.0

    def sleeping(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds

    def advance(self, seconds: float) -> None:
        """What a request that took a while does to a bound, which a
        mock transport otherwise does not: it answers instantly, so a
        case about time spent inside one has to spend it."""
        self.now += seconds


@pytest.fixture
def stopped_clock(monkeypatch: pytest.MonkeyPatch) -> Iterator[Clock]:
    """A clock the case controls, so a cadence is asserted rather than
    waited out.

    Every sleep advances the clock by what it was asked to sleep, which
    is the whole of what a real one does to a bound made of `monotonic`.
    Both names are read out of `board`, which is where they were imported
    to, so this replaces the seam and not the standard library.
    """
    clock = Clock()
    monkeypatch.setattr(board, "sleep", clock.sleeping)
    monkeypatch.setattr(board, "monotonic", lambda: clock.now)
    yield clock


# The board's identity, both halves


def test_the_default_address_is_fixed_and_says_it_was_never_assigned() -> None:
    """A binding sticks across runs because the address is the same
    every run, and nothing is written anywhere to make that true. The
    leading octet's second-least-significant bit is the
    locally-administered one, which is what an address that never came
    off a chip should carry."""
    assert board.DEFAULT_MAC == "02:00:00:00:00:01"
    assert int(board.DEFAULT_MAC.split(":")[0], 16) & 0b10 == 0b10
    assert board.Identity.of(board.DEFAULT_MAC) == board.Identity.of(board.DEFAULT_MAC)


def test_two_spellings_of_one_address_are_one_board() -> None:
    """The token is signed for the MAC and the client id together, so a
    client id that varied with how the MAC was typed would produce a
    bad_token refusal with nothing on either side saying why."""
    assert board.Identity.of("AA:BB:CC:DD:EE:FF") == board.Identity.of("aa-bb-cc-dd-ee-ff")


def test_two_addresses_are_two_boards() -> None:
    first = board.Identity.of("02:00:00:00:00:01")
    second = board.Identity.of("02:00:00:00:00:02")

    assert first.client_id != second.client_id


def test_the_client_id_is_reproducible_from_the_rule_in_the_source() -> None:
    """A reader can compute it, which is what "derived by a stated rule"
    has to mean to be worth saying."""
    from uuid import uuid5

    assert board.Identity.of("02:00:00:00:00:01").client_id == str(
        uuid5(board.CLIENT_ID_NAMESPACE, "02:00:00:00:00:01")
    )


def test_an_address_that_is_not_a_mac_is_refused_in_the_grammar_s_own_words(run) -> None:
    """The same sentence `device bind` answers with, which carries the
    rule and never the value."""
    with pytest.raises(ConfigError) as caught:
        board.Identity.of("not-a-mac")

    assert str(caught.value) == NOT_A_MAC


# The four states, and one exit code each


def test_an_unclaimed_board_reports_its_code_and_exits_zero(
    run, far_side, capsys: pytest.CaptureFixture[str]
) -> None:
    """A simulated board reporting the state it is in is a command that
    worked."""
    far_side(answering(body=activating()))

    assert run("simulator", "check-in", URL) == 0

    captured = capsys.readouterr()
    assert CODE in captured.out
    assert "not claimed yet" in captured.out
    # What to do next is a notice, and stdout holds what the board was
    # handed.
    assert "device pending claim" in captured.err


def test_an_admitted_board_says_a_token_was_issued_and_never_says_which(
    run, far_side, capsys: pytest.CaptureFixture[str]
) -> None:
    far_side(answering(body=admitted()))

    assert run("simulator", "check-in", URL) == 0

    captured = capsys.readouterr()
    assert "is admitted" in captured.out
    assert "protocol version: 1" in captured.out
    assert DEVICE_TOKEN not in captured.out + captured.err
    # Nor the address it would have been sent to, which is far-side text
    # deciding where a credential goes.
    assert "voice.example/xiaozhi" not in captured.out + captured.err


def test_a_board_with_no_token_and_no_code_names_the_trap(
    run, far_side, capsys: pytest.CaptureFixture[str]
) -> None:
    """The state the reply says nothing about: 200 OK, an empty token and
    no activation section. A boolean "did I get a token" would have
    folded it into the unclaimed one."""
    far_side(answering(body=unwelcome()))

    assert run("simulator", "check-in", URL) == 0

    captured = capsys.readouterr()
    assert "may not speak" in captured.out
    assert "onboarding is turned off" in captured.out
    assert "not serving yet" in captured.out


# Why the token is empty, read off the reply rather than guessed (#369)
#
# The two empty tokens are the same bytes, so the word beside them is
# the only thing that can tell an admitted board on a deployment issuing
# none from a board nothing resolves. A reply that carries no word, or
# one this client has never heard of, is read by the rule that was here
# before it, which is what an old image gets.


def test_a_deployment_that_issues_no_tokens_admits_this_board(
    run, far_side, capsys: pytest.CaptureFixture[str]
) -> None:
    """The whole of #369: `auth.enabled: false` with something for this
    board to reach answers an empty token and the word for why, and this
    board is admitted on it.

    Held to going red by dropping the word from the reading: the body is
    byte for byte the unwelcome one apart from that field, so a client
    reading the token alone reports the state that costs an evening.
    """
    far_side(answering(body=admitted_without_a_token()))

    assert run("simulator", "check-in", URL) == 0

    captured = capsys.readouterr()
    assert "is admitted" in captured.out
    assert "may not speak" not in captured.out


def test_a_deployment_that_says_it_turned_this_board_away_is_believed(
    run, far_side, capsys: pytest.CaptureFixture[str]
) -> None:
    """The other half of the same field, and the state it names is the
    one the empty token has always meant."""
    far_side(answering(body=turned_away()))

    assert run("simulator", "check-in", URL) == 0

    assert "may not speak" in capsys.readouterr().out


def test_a_credential_named_in_the_reply_is_carried_and_never_printed(
    run, far_side, capsys: pytest.CaptureFixture[str]
) -> None:
    """The third word, on the reply a deployment with device
    authentication on sends."""
    far_side(answering(body=admitted_with_a_token()))

    assert run("simulator", "check-in", URL) == 0

    captured = capsys.readouterr()
    assert "is admitted" in captured.out
    assert DEVICE_TOKEN not in captured.out + captured.err


def test_an_open_admission_says_that_deployment_issues_no_tokens(
    run, far_side, capsys: pytest.CaptureFixture[str]
) -> None:
    """The report an operator learns the deployment's auth setting from.

    Admission with no credential is still admission, so this says both:
    the board may speak, and there is no token because that deployment
    mints none. Reporting "issued" here would be the one false sentence
    the new reading could ship.
    """
    far_side(answering(body=admitted_without_a_token()))

    assert run("simulator", "check-in", URL) == 0

    captured = capsys.readouterr()
    assert "is admitted" in captured.out
    assert cli.NO_TOKEN_ISSUED in captured.out
    assert cli.TOKEN_ISSUED not in captured.out


def test_an_admission_with_a_credential_says_a_token_was_issued(
    run, far_side, capsys: pytest.CaptureFixture[str]
) -> None:
    """The other reading of the same state, so the two sentences are held
    apart rather than one being asserted alone."""
    far_side(answering(body=admitted_with_a_token()))

    assert run("simulator", "check-in", URL) == 0

    assert cli.TOKEN_ISSUED in capsys.readouterr().out


def test_the_trap_state_names_every_reading_that_produces_it(
    run, far_side, capsys: pytest.CaptureFixture[str]
) -> None:
    """The tail, enumerated from the decision sites rather than from the
    sentence it replaced.

    Two of these the old sentence missed: a deployment that could not
    read its own record of what is bound offers no code while resolving
    nothing, and the unloaded agent can be named by `default_agent`
    rather than by a binding. The last is not a configuration at all: a
    server too old to say why a token is empty answers an admitted board
    on a token-less deployment in exactly these bytes.
    """
    far_side(answering(body=unwelcome()))

    assert run("simulator", "check-in", URL) == 0

    said = capsys.readouterr().out
    assert cli.MAY_NOT_SPEAK in said
    assert "onboarding is turned off" in said
    assert "default_agent" in said
    assert "not serving yet" in said
    assert "waiting to be claimed would not take another one" in said
    assert "could not read its own record of what is bound" in said
    assert "issues no device tokens at all and is too old to say so" in said


def test_the_conversation_refusal_advises_the_claim_only_where_it_applies() -> None:
    """The sentence that used to advise its own opposite.

    `--claim` binds the board showing an activation code, so a board
    that was never offered one is a board the claim cannot address, and
    the old wording sent a reader from here to `NOTHING_TO_CLAIM` and
    back. It points at the check-in's own answer first now, and
    conditions the claim on the state that has something to claim.
    """
    assert "check-in" in cli.CANNOT_CONVERSE
    assert cli.CANNOT_CONVERSE.index("check-in") < cli.CANNOT_CONVERSE.index("--claim")
    assert "showing an activation code" in cli.CANNOT_CONVERSE


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (unwelcome(), "may not speak"),
        (admitted(), "is admitted"),
        ({**unwelcome(), "access": "sometime-in-the-future"}, "may not speak"),
        ({**admitted(), "access": "sometime-in-the-future"}, "is admitted"),
    ],
    ids=[
        "an older server, no token",
        "an older server, a token",
        "a word this client does not know, no token",
        "a word this client does not know, a token",
    ],
)
def test_a_reply_this_client_has_no_word_for_is_read_by_the_token_alone(
    run, far_side, capsys: pytest.CaptureFixture[str], body: dict, expected: str
) -> None:
    """The fallback, both ways, and it is yesterday's rule rather than a
    lesser one: a server too old to say why a token is empty is read
    exactly as it was before the field existed.

    An unrecognized word is read as no word at all, which is
    conservative compatibility rather than a promise that a future
    admission mode survives it: such a mode would read as the state
    below, which is the safe half of being wrong.
    """
    far_side(answering(body=body))

    assert run("simulator", "check-in", URL) == 0

    assert expected in capsys.readouterr().out


# The firmware block, which the capability table claims is read
#
# The claim is tested in the milestone that makes it, which is the
# pattern the redirect row set. The capability pin cannot reach this on
# its own: it holds a row to naming a verb the tree has, and every row
# would still name `check-in` with the block thrown away.


@pytest.mark.parametrize(
    ("block", "expected"),
    [
        ({"version": board.FIRMWARE_VERSION, "url": ""}, cli.FIRMWARE_UP_TO_DATE),
        ({"version": "9.9.9", "url": "https://voice.example/fw.bin"}, cli.FIRMWARE_OFFERED),
        ({"version": board.FIRMWARE_VERSION, "url": "https://voice.example/fw.bin"},
         cli.FIRMWARE_OFFERED),
        ({"version": "9.9.9", "url": ""}, cli.FIRMWARE_UNEXPECTED_VERSION),
        ({}, cli.FIRMWARE_UNEXPECTED_VERSION),
    ],
    ids=[
        "the version echoed back with nothing to fetch",
        "an image offered",
        "an image offered at the version this board runs",
        "a different version with nothing to fetch",
        "an empty block",
    ],
)
def test_the_reply_s_firmware_block_is_read_and_what_it_means_reported(
    run, far_side, capsys: pytest.CaptureFixture[str], block: dict, expected: str
) -> None:
    """What a real board does with that block is decide, so the decision
    is what is reported: an image was named or it was not, and the
    version named back is this board's own or it is not.

    Held to going red by discarding the block: with no firmware field on
    the reply every one of these rows reports the same sentence, and
    three of the five are then wrong.
    """
    far_side(answering(body={**unwelcome(), "firmware": block}))

    assert run("simulator", "check-in", URL) == 0

    assert expected in capsys.readouterr().out


def test_the_firmware_block_is_reported_without_a_word_of_it(
    run, far_side, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    """Read is not repeated. The version is a string a stranger's server
    chose and the URL is an address it would like this board to fetch,
    so what crosses is the two comparisons and neither value."""
    far_side(
        answering(
            body={
                **admitted(),
                "firmware": {"version": PASTED, "url": f"https://voice.example/{PASTED}.bin"},
            }
        )
    )
    with caplog.at_level(logging.DEBUG):
        caplog.clear()
        assert run("simulator", "check-in", URL) == 0
    captured = capsys.readouterr()

    assert cli.FIRMWARE_OFFERED in captured.out
    assert PASTED not in captured.out + captured.err + logged(caplog)


# The replies a correct server never sends
#
# Every one of these is `Refused` and exit 1, and each names the sentence
# it must leave with, because a refusal that answered with the wrong
# fixed sentence would be as wrong as one that answered with a value.

HOSTILE: tuple[tuple[str, Callable[[httpx.Request], httpx.Response], str], ...] = (
    (
        # The case a truthiness check passes and `is not None` fails. An
        # empty object is falsy and is not an absent key, and the schema
        # admits it deliberately so that the seam is what decides.
        "an empty activation object",
        answering(body={**unwelcome(), "activation": {}}),
        "not claimed yet",
    ),
    (
        "an activation beside a token",
        answering(body={**admitted(), "activation": activating()["activation"]}),
        board.CONTRADICTORY_REPLY,
    ),
    # The word for the token against what stands beside it, every row of
    # the matrix (#369). No server decision site can emit any of them:
    # the token and the word come from one call, and a board being
    # claimed is by definition not yet admitted.
    (
        "a credential named where the token is empty",
        answering(body={**unwelcome(), "access": board.ACCESS_TOKEN}),
        board.CONTRADICTORY_ACCESS,
    ),
    (
        "no credential named where a token stands",
        answering(body={**admitted(), "access": board.ACCESS_OPEN}),
        board.CONTRADICTORY_ACCESS,
    ),
    (
        "a board turned away with a token in its hand",
        answering(body={**admitted(), "access": board.ACCESS_DENIED}),
        board.CONTRADICTORY_ACCESS,
    ),
    (
        "an admission beside a board that is still being claimed",
        answering(body={**activating(), "access": board.ACCESS_OPEN}),
        board.CONTRADICTORY_ACCESS,
    ),
    (
        "a credential named beside a board that is still being claimed",
        answering(body={**activating(), "access": board.ACCESS_TOKEN}),
        board.CONTRADICTORY_ACCESS,
    ),
    (
        "a token that is a number",
        answering(body=admitted(token=7)),
        board.MALFORMED_REPLY,
    ),
    (
        "no websocket object at all",
        answering(body={"server_time": {"timestamp": 0, "timezone_offset": 0}}),
        board.MALFORMED_REPLY,
    ),
    (
        "a websocket that is a string",
        answering(body={"websocket": "wss://voice.example/xiaozhi/v1/"}),
        board.MALFORMED_REPLY,
    ),
    (
        "a protocol version this side does not speak",
        answering(body=admitted(version=9)),
        board.UNKNOWN_PROTOCOL_VERSION,
    ),
    (
        "a websocket URL with a credential in it",
        answering(body=admitted(url=f"wss://board:{PASTED}@voice.example/xiaozhi/v1/")),
        board.UNUSABLE_WEBSOCKET,
    ),
    (
        "invalid JSON",
        answering(text="{not json at all"),
        board.NOT_A_REPLY,
    ),
    (
        "an empty body",
        answering(text=""),
        board.NOT_A_REPLY,
    ),
    (
        "a 500",
        answering(status=500, text="upstream is unwell"),
        board.bad_status(500),
    ),
    (
        "a 307",
        answering(status=307, text="", location="https://elsewhere.invalid/x/"),
        SUPPLIED_ENDPOINT,
    ),
)


@pytest.mark.parametrize(
    ("answer", "expected"),
    [(answer, expected) for _, answer, expected in HOSTILE],
    ids=[name for name, _, _ in HOSTILE],
)
def test_a_reply_a_real_server_never_sends_reaches_a_decided_answer(
    run, far_side, capsys: pytest.CaptureFixture[str], answer, expected: str
) -> None:
    """Every one of these names the outcome it must reach and the exit
    code it must leave with.

    The `activation={}` row is the one held to going red if the seam is
    written as truthiness: under `if activation:` an empty object is
    falsy, the reading falls through to the token, the token is empty,
    and the command reports the unclaimed board as unwelcome instead.
    """
    far_side(answer)

    code = run("simulator", "check-in", URL)
    captured = capsys.readouterr()

    if expected == "not claimed yet":
        assert code == 0
        assert expected in captured.out
        return
    assert code == 1
    assert captured.err.strip() == expected.strip() or expected in captured.err
    assert captured.out == ""


def test_every_refusal_of_a_hostile_reply_repeats_nothing_of_it(
    run, far_side, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    """The four surfaces, against the values a case planted in the ANSWER
    rather than in the command line: a credential in the websocket URL
    the reply named, and a body that is nothing but a planted value.

    The device token is the one a review would not think to ask for,
    because nobody typed it.
    """
    for answer in (
        answering(body=admitted(url=f"wss://board:{PASTED}@voice.example/xiaozhi/v1/")),
        answering(body=admitted(token=PASTED, version=9)),
        # And the word for the token, which is far-side bytes like the
        # two above it: a value shaped like a credential, in the field a
        # newer server states the admission in (#369).
        answering(body={**admitted(token=PASTED, version=9), "access": PASTED}),
        answering(text=json.dumps({"websocket": PASTED})),
        answering(status=500, text=PASTED),
        answering(status=307, text=PASTED, location=f"https://elsewhere.invalid/{PASTED}"),
    ):
        far_side(answer)
        with caplog.at_level(logging.DEBUG):
            caplog.clear()
            assert run("simulator", "check-in", URL) == 1
        captured = capsys.readouterr()
        with pytest.raises(ConfigError) as caught:
            cli._parsed(["simulator", "check-in", URL], cli.DISPATCHED)  # noqa: SLF001
        surfaces = {
            "stdout": captured.out,
            "stderr": captured.err,
            "logs": logged(caplog),
            "chain": chain(caught.value),
        }
        assert [name for name, text in surfaces.items() if PASTED in text] == []
        assert [name for name, text in surfaces.items() if DEVICE_TOKEN in text] == []


@pytest.mark.parametrize(
    "body",
    [{**admitted(), "access": PASTED}, {**unwelcome(), "access": PASTED}],
    ids=["a reply this client admits", "a reply this client turns away"],
)
def test_a_word_for_the_token_this_client_does_not_know_reaches_no_surface(
    run,
    far_side,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    body: dict,
) -> None:
    """The same four surfaces, on the replies this client ACCEPTS.

    The inventory above is about refusals, which quote nothing by
    construction. This is the harder half: an unknown word is read as an
    older server and the command goes on to report a state, so the value
    survives to a point where something could print it. It may not. Like
    the token and the URL it explains, it is far-side bytes, and this
    one is shaped like a credential so that the assertion is about
    something.
    """
    far_side(answering(body=body))
    with caplog.at_level(logging.DEBUG):
        caplog.clear()
        assert run("simulator", "check-in", URL) == 0
    captured = capsys.readouterr()
    with pytest.raises(ConfigError) as caught:
        cli._parsed(["simulator", "check-in", URL, "--mac", "not-a-mac"], cli.DISPATCHED)  # noqa: SLF001
    surfaces = {
        "stdout": captured.out,
        "stderr": captured.err,
        "logs": logged(caplog),
        "chain": chain(caught.value),
    }

    assert [name for name, text in surfaces.items() if PASTED in text] == []


@pytest.mark.parametrize("field", ["code", "message", "challenge"])
def test_a_reply_that_hands_the_address_back_publishes_none_of_it(
    run, far_side, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture, field: str
) -> None:
    """The three fields this command exists to SHOW, on a reply it
    accepts.

    Every refusal here is a fixed sentence that quotes nothing, so these
    three are the only route a supplied URL has to a surface at all, and
    reflecting the request target into an answer is what a proxy, a
    captive portal and an error page each do by default. The reply is a
    valid activating one, so nothing else in the reading stands between
    the reflection and stdout.

    Held to going red by rendering these fields through `printable`
    alone: the bound and the control characters are what that governs,
    and a supplied address is perfectly printable and perfectly short.
    """
    reflection = f"reflected: /x/{SEGMENT}/?token={QUERY_SECRET}"
    far_side(answering(body=activating(**{field: reflection})))
    with caplog.at_level(logging.DEBUG):
        caplog.clear()
        assert run("simulator", "check-in", URL) == 0
    captured = capsys.readouterr()
    with pytest.raises(ConfigError) as caught:
        cli._parsed(["simulator", "check-in", URL, "--mac", "not-a-mac"], cli.DISPATCHED)  # noqa: SLF001
    surfaces = {
        "stdout": captured.out,
        "stderr": captured.err,
        "logs": logged(caplog),
        "chain": chain(caught.value),
    }

    assert [name for name, text in surfaces.items() if SEGMENT in text] == []
    assert [name for name, text in surfaces.items() if QUERY_SECRET in text] == []
    # And the field was still shown, with a stand-in where the address
    # was: a command that answered by printing nothing would pass the
    # assertions above and be useless.
    assert "reflected:" in captured.out
    assert device_endpoint.WITHHELD in captured.out


# The redirect the capability table claims


def test_a_redirect_is_refused_and_its_target_is_never_fetched(
    run, far_side, capsys: pytest.CaptureFixture[str]
) -> None:
    """The claim the capability table makes, tested in the milestone that
    makes it. The firmware does not follow a redirect on this request, so
    every device-facing route serves the slashless spelling directly and
    a redirect from that address is something else answering."""
    endpoint = far_side(
        answering(status=307, text="", location="https://elsewhere.invalid/x/counted/")
    )

    assert run("simulator", "check-in", URL) == 1

    assert len(endpoint.requests) == 1
    assert "elsewhere.invalid" not in " ".join(endpoint.targets())
    captured = capsys.readouterr()
    assert "does not follow" in captured.err
    assert "elsewhere.invalid" not in captured.out + captured.err
    assert captured.out == ""


# What a request actually carries


def test_the_check_in_sends_what_the_handler_reads(run, far_side) -> None:
    """Two headers and a system-info body, which is the whole of what a
    board tells a server about itself."""
    endpoint = far_side(answering(body=unwelcome()))

    assert run("simulator", "check-in", URL) == 0

    [request] = endpoint.requests
    assert request.method == "POST"
    assert request.headers["Device-Id"] == board.DEFAULT_MAC
    assert request.headers["Client-Id"] == board.Identity.of(board.DEFAULT_MAC).client_id
    [body] = endpoint.bodies()
    assert body["application"]["version"] == board.FIRMWARE_VERSION
    assert body["board"]["type"] == board.BOARD_TYPE


def test_a_given_address_is_the_one_the_check_in_is_sent_to(run, far_side) -> None:
    """The path as given with the query preserved, which is the case a
    two-string type could not have been given."""
    endpoint = far_side(answering(body=unwelcome()))

    assert run("simulator", "check-in", URL) == 0

    assert endpoint.targets() == [URL]


def test_a_given_mac_replaces_the_default(run, far_side) -> None:
    endpoint = far_side(answering(body=unwelcome()))

    assert run("simulator", "check-in", URL, "--mac", "02:00:00:00:00:02") == 0

    assert endpoint.headers("Device-Id") == ["02:00:00:00:00:02"]


# The ceremony, and the fourth step that makes the other three worth
# anything


def bound(run) -> str:
    """A deployment with an agent to be claimed by, and a board waiting
    in the pending table with the code the far side is about to show."""
    run("provider", "set", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")
    run("agent", "set", "sam", "-f", "-", stdin="llm: claude\n")
    identity = board.Identity.of(board.DEFAULT_MAC)
    return run.pending.observe(
        identity.mac, identity.client_id, board.BOARD_TYPE, board.FIRMWARE_VERSION
    ).device.code


def test_a_claim_is_four_requests_and_the_last_one_mints_the_token(
    run, far_side, capsys: pytest.CaptureFixture[str]
) -> None:
    """An activating check-in's token is empty and the poll route answers
    a status, so the only thing that mints a token is a check-in reply.
    A socket opened with the token from step one would be refused at the
    handshake with no_token.

    Held to going red by removing the re-check-in: without it the
    command would have to report the state the FIRST reply carried, which
    is the unclaimed one with no token in it.
    """
    code = bound(run)
    endpoint = far_side(
        answering(body=activating(code=code)),
        answering(status=200, text=""),
        answering(body=admitted()),
    )
    capsys.readouterr()

    assert run("simulator", "check-in", URL, "--claim", "sam") == 0

    assert endpoint.targets() == [URL, ACTIVATION_URL, URL]
    # One identity across every request of the ceremony, asserted off the
    # recorded requests rather than off the function that makes it, and
    # required of every one of them rather than allowed to be absent
    # from any: the token is signed for the MAC and the client id
    # together, so a request carrying half the identity is a request the
    # claim cannot be held to.
    identity = board.Identity.of(board.DEFAULT_MAC)
    assert endpoint.headers("Device-Id") == [identity.mac] * len(endpoint.requests)
    assert endpoint.headers("Client-Id") == [identity.client_id] * len(endpoint.requests)
    # The poll is the firmware's version-1 shape: the version header and
    # an empty object, which upstream's own server reads nothing of.
    assert endpoint.headers("Activation-Version") == ["", "1", ""]
    assert endpoint.bodies()[1] == {}
    assert "is admitted" in capsys.readouterr().out


def test_a_claim_performs_the_act_the_grammar_already_has(
    run, far_side, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--claim` sends no new request: it performs `ADD_DEVICE`, the same
    act object behind `device pending claim`, so there is no second
    encoding of a claim and no new row in the contract check's covered
    set.

    Asserted by identity rather than by shape, which is the difference
    between "it sends the same thing" and "it is the same thing". The
    dispatcher is the seam it is read at, because an act reaches the API
    through it and reaches it no other way.
    """
    code = bound(run)
    far_side(
        answering(body=activating(code=code)),
        answering(status=200, text=""),
        answering(body=admitted()),
    )
    performed: list[object] = []
    dispatch = cli._act  # noqa: SLF001

    def recording(args: cli.Invocation, act: cli.Act, reached: cli.Reached) -> None:
        performed.append(act)
        dispatch(args, act, reached)

    monkeypatch.setattr(cli, "_act", recording)
    reached_before = len(run.reached)

    assert run("simulator", "check-in", URL, "--claim", "sam") == 0

    assert performed == [cli.ADD_DEVICE]
    # Exactly one configuration API request, which is the claim.
    assert len(run.reached) - reached_before == 1
    monkeypatch.setattr(cli, "_act", dispatch)
    assert run("device", "show", board.DEFAULT_MAC) == 0


def test_a_claim_the_configuration_superseded_says_the_condition_and_no_address(
    run,
    far_side,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The race the repository's transaction exists to lose safely, met
    from the command that types no MAC at all.

    Deterministic rather than timed: the competing binding is written
    through a second repository on the same database, which is exactly
    what another process is and what the pending table cannot be told
    about, so the code survives to be claimed and the conditional write
    is what has to refuse. The refusal is the repository's own sentence,
    relayed by the API and printed by this command.

    Held to going red by a sentence built over the MAC: this command
    addresses the claim by six digits, so an address in the answer is one
    the refusal resolved rather than one anybody sent.
    """
    code = bound(run)
    engine = open_database(DatabaseConfig())
    try:
        ConfigStore(engine).bind_device(board.DEFAULT_MAC, ["sam"])
    finally:
        engine.dispose()
    far_side(answering(body=activating(code=code)))
    capsys.readouterr()

    with caplog.at_level(logging.DEBUG):
        caplog.clear()
        assert run("simulator", "check-in", URL, "--claim", "sam") == 1
    captured = capsys.readouterr()
    with pytest.raises(ConfigError) as caught:
        cli._parsed(["simulator", "check-in", URL, "--mac", "not-a-mac"], cli.DISPATCHED)  # noqa: SLF001
    surfaces = {
        "stdout": captured.out,
        "stderr": captured.err,
        "logs": logged(caplog),
        "chain": chain(caught.value),
    }

    assert captured.err.strip() == ALREADY_BOUND
    assert [name for name, text in surfaces.items() if board.DEFAULT_MAC in text] == []


def test_without_the_flag_no_api_token_is_read_and_no_api_request_is_made(
    run, far_side, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The device side never touches the operator-side credential, which
    is what "kept distinct" has to mean to be worth saying. Asserted with
    the variable unset, so a command that read it would fail rather than
    quietly succeed."""
    monkeypatch.delenv(API_SECRET_ENV, raising=False)
    far_side(answering(body=unwelcome()))
    reached_before = len(run.reached)

    assert run("simulator", "check-in", URL) == 0

    assert len(run.reached) == reached_before


def test_a_claim_needs_a_code_and_says_so_when_there_is_none(
    run, far_side, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--claim` is addressed by the six digits a board is showing, so a
    board that was not offered a code has nothing for the claim to
    address."""
    endpoint = far_side(answering(body=admitted()))

    assert run("simulator", "check-in", URL, "--claim", "sam") == 1

    assert len(endpoint.requests) == 1
    assert capsys.readouterr().err.strip() == cli.NOTHING_TO_CLAIM


def test_the_post_ceremony_trap_names_the_deployment_that_issues_none(
    run, far_side, capsys: pytest.CaptureFixture[str]
) -> None:
    """A claim that went through, an activation that said it was
    activated, and a check-in after it that admitted nothing.

    Against a server new enough to say why a token is empty this is the
    binding, and `device show` is where to look. Against an older one it
    is the auth-off deployment, which is precisely the sequence #369 was
    reported from, so the sentence names that reading too.
    """
    code = bound(run)
    far_side(
        answering(body=activating(code=code)),
        answering(status=200, text=""),
        answering(body=unwelcome()),
    )
    capsys.readouterr()

    assert run("simulator", "check-in", URL, "--claim", "sam") == 1

    said = capsys.readouterr().err
    assert said.strip().splitlines()[-1] == cli.NOT_ADMITTED_AFTER_CLAIM
    assert "issues no device tokens and is too old to say so" in said


# Every wait, bounded


@pytest.mark.parametrize(
    ("hint", "expected"),
    [
        (None, board.ACTIVATION_CEILING_S),
        (0, board.ACTIVATION_CEILING_S),
        (-1000, board.ACTIVATION_CEILING_S),
        (True, board.ACTIVATION_CEILING_S),
        ("6000", board.ACTIVATION_CEILING_S),
        (6000.0, board.ACTIVATION_CEILING_S),
        (10**12, board.ACTIVATION_CEILING_S),
        (30000, board.ACTIVATION_CEILING_S),
        (6000, 6.0),
    ],
    ids=[
        "absent",
        "zero",
        "negative",
        "boolean",
        "a string",
        "a float",
        "large enough to hang the command",
        "the value a real server sends",
        "a valid smaller value",
    ],
)
def test_a_far_side_number_may_shorten_a_wait_and_never_extend_one(
    hint: object, expected: float
) -> None:
    """The rule, stated once and applied to every remote number this
    command reads. A malformed hint is ignored rather than refused,
    because it is not a reason to fail a ceremony that works without it.

    `True` is excluded before `int` is asked, since a bool is an int in
    Python and a JSON `true` is not a number of milliseconds.
    """
    assert board.activation_ceiling(hint) == expected


def test_the_poll_keeps_the_firmware_s_cadence_exactly(
    run, far_side, stopped_clock, capsys: pytest.CaptureFixture[str]
) -> None:
    """Bursts of ten, three seconds apart, which is what
    `docs/xiaozhi-notes.md` records `Application::CheckNewVersion`
    doing. Ten polls and nine waits between them, on a clock this case
    controls rather than half a minute of real time."""
    code = bound(run)
    endpoint = far_side(
        answering(body=activating(code=code)),
        answering(status=202, text=""),
    )
    capsys.readouterr()

    assert run("simulator", "check-in", URL, "--claim", "sam") == 1

    polls = [target for target in endpoint.targets() if target == ACTIVATION_URL]
    assert len(polls) == board.POLL_ATTEMPTS
    assert stopped_clock.slept == [board.POLL_INTERVAL_S] * (board.POLL_ATTEMPTS - 1)
    # The claim's own notice comes first, because the claim happened.
    assert capsys.readouterr().err.splitlines()[-1] == cli.NOT_ADMITTED_YET


def test_a_smaller_far_side_bound_shortens_the_burst(
    run, far_side, stopped_clock, capsys: pytest.CaptureFixture[str]
) -> None:
    """The only direction remote input moves a wait."""
    code = bound(run)
    far_side(
        answering(body=activating(code=code, timeout_ms=6000)),
        answering(status=202, text=""),
    )
    capsys.readouterr()

    assert run("simulator", "check-in", URL, "--claim", "sam") == 1

    assert sum(stopped_clock.slept) <= 6.0
    assert len(stopped_clock.slept) < board.POLL_ATTEMPTS - 1


def test_a_poll_may_not_wait_past_the_ceiling_it_is_inside(
    run, far_side, stopped_clock, capsys: pytest.CaptureFixture[str]
) -> None:
    """The bound applies to the request and not only to the sleeping
    between requests.

    A poll's own read bound is the doctor's thirty seconds, so a
    ceremony a far side asked to be six seconds long could spend thirty
    of them inside the first poll and only then find the deadline behind
    it. The bound this asserts is the one the request was GIVEN, read
    off the request the transport recorded, because a mock transport
    answers instantly and would satisfy any timeout at all.

    Held to going red by dropping the budget: without it every poll
    carries the flat thirty-second read bound whatever is left of the
    six.
    """
    code = bound(run)
    endpoint = far_side(
        answering(body=activating(code=code, timeout_ms=6000)),
        answering(status=202, text=""),
    )
    capsys.readouterr()

    assert run("simulator", "check-in", URL, "--claim", "sam") == 1

    polls = [
        request.extensions["timeout"]
        for request in endpoint.requests
        if str(request.url) == ACTIVATION_URL
    ]
    assert polls
    for bounds in polls:
        assert bounds["read"] <= 6.0
        assert bounds["connect"] <= 6.0
    # And the check-in before them, which no ceiling covers, kept the
    # generous read the doctor's reason picked.
    assert endpoint.requests[0].extensions["timeout"]["read"] == device_endpoint.READ_TIMEOUT_S


def test_a_poll_that_spends_the_ceiling_ends_the_burst(
    run, far_side, stopped_clock, capsys: pytest.CaptureFixture[str]
) -> None:
    """A request that consumes the whole budget is a request the next
    one may not be made after.

    The time is spent inside the answer, which is where a slow far side
    spends it, and the assertion is that the burst stops rather than
    running its ten attempts on a deadline that has already passed.

    This one pins the property rather than biting: the check after an
    answer already stopped the burst here, and what did not exist before
    the budget is a bound on the waiting inside the request itself,
    which is the case above.
    """
    code = bound(run)

    def slow(request: httpx.Request) -> httpx.Response:
        stopped_clock.advance(board.ACTIVATION_CEILING_S)
        return httpx.Response(202, text="")

    endpoint = far_side(answering(body=activating(code=code)), slow)
    capsys.readouterr()

    assert run("simulator", "check-in", URL, "--claim", "sam") == 1

    polls = [target for target in endpoint.targets() if target == ACTIVATION_URL]
    assert len(polls) == 1
    assert stopped_clock.slept == []
    assert capsys.readouterr().err.splitlines()[-1] == cli.NOT_ADMITTED_YET


# The other verb, and the two no-leak cases only its ANSWER can produce
#
# `simulator run` is `check-in` plus a socket, so everything above is its
# too. What is here is what belongs to `run` alone: the states that are a
# report for one verb and a refusal for the other, and the two rules
# about the address the reply named, which decide where a device token
# would be sent and are therefore refusals before any socket opens.


def test_the_conversation_verb_refuses_a_board_that_may_not_speak(
    run, far_side, capsys: pytest.CaptureFixture[str]
) -> None:
    """The one place the two verbs disagree about the same reply.

    `check-in` reports these two states and exits 0, because reporting
    the state a board is in IS the answer. `run` was asked to hold a
    conversation and cannot, so the same replies are a refusal here.

    The unwelcome body is the old-server one, byte for byte, and the
    turned-away one is the same state said out loud by a server that can
    (#369). Both refuse, because what changed is how the state is read
    and not what it means.
    """
    for answer in (
        answering(body=activating()),
        answering(body=unwelcome()),
        answering(body=turned_away()),
    ):
        far_side(answer)

        assert run("simulator", "run", URL) == 1

        assert capsys.readouterr().err.strip() == cli.CANNOT_CONVERSE


def test_the_conversation_verb_goes_on_where_the_deployment_issues_no_tokens(
    run, far_side, capsys: pytest.CaptureFixture[str]
) -> None:
    """The behavior half of #369, at the seam that used to stop it.

    A deployment with device authentication off admits this board and
    hands it an empty token, and the classification in front of the
    socket refused it for exactly that. Here it goes past the
    classification and opens the socket, which is as far as a unit case
    can take it: the address is a loopback port nothing listens on, so
    the refusal is the socket's and not the reading's.

    Held to going red by the reading alone: without the word, this body
    is `Unwelcome` and the command leaves with `CANNOT_CONVERSE` before
    anything is opened.
    """
    far_side(
        answering(
            body=admitted_without_a_token(
                websocket={"url": "wss://127.0.0.1:9/xiaozhi/v1/", "token": "", "version": 1}
            )
        )
    )

    assert run("simulator", "run", URL) == 1

    captured = capsys.readouterr()
    assert "admitted this board" in captured.out
    assert cli.CANNOT_CONVERSE not in captured.err
    assert captured.err.strip().startswith("cannot open a conversation with ")


def test_the_conversation_verb_opens_no_socket_to_an_address_with_a_credential_in_it(
    run, far_side, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    """A reply naming a websocket URL with a password written into it.

    Refused before anything is opened, and the address never printed. The
    stakes are the reason: this client is holding a device token, and the
    URL a reply names is what decides where that token goes. A client
    that connected anyway would be the thing that published the
    credential in the URL.
    """
    far_side(answering(body=admitted(url=f"wss://board:{PASTED}@voice.example/xiaozhi/v1/")))
    with caplog.at_level(logging.DEBUG):
        assert run("simulator", "run", URL) == 1

    captured = capsys.readouterr()
    assert captured.err.strip() == board.UNUSABLE_WEBSOCKET
    for surface in (captured.out, captured.err, logged(caplog)):
        assert PASTED not in surface
        assert DEVICE_TOKEN not in surface


def test_the_conversation_verb_refuses_a_downgrade_from_the_endpoint_it_reached(
    run, far_side, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    """An `https://` check-in answering with a plain `ws://` address.

    The TLS-proxy misconfiguration the doctor already calls out, and here
    it is a refusal rather than a diagnosis: a device token crossing a
    plain socket from behind TLS is the same mistake the configuration
    client has no flag to make. The address this lane is given is
    `https://`, which is what makes the rule apply at all.
    """
    plain = f"ws://voice.example/xiaozhi/v1/?s={PASTED}"
    far_side(answering(body=admitted(url=plain)))
    with caplog.at_level(logging.DEBUG):
        assert run("simulator", "run", URL) == 1

    captured = capsys.readouterr()
    assert captured.err.strip() == board.UNUSABLE_WEBSOCKET
    for surface in (captured.out, captured.err, logged(caplog)):
        assert PASTED not in surface
        assert DEVICE_TOKEN not in surface


def test_the_conversation_verb_reads_no_api_token_without_a_claim(
    run, far_side, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The two credentials kept distinct, asserted for the second verb
    the only way that means anything: with the operator-side one absent
    from the environment.

    The command still fails, because the far side here admits the board
    and there is no socket at the address it named. What matters is which
    refusal it fails with: a command that had read the API secret would
    name the variable instead.
    """
    monkeypatch.delenv(API_SECRET_ENV, raising=False)
    # A loopback port nothing listens on, so the socket is refused at
    # once: a hostname would put a DNS lookup between this case and its
    # assertion, and what is being asserted is not the network.
    far_side(answering(body=admitted(url="wss://127.0.0.1:9/xiaozhi/v1/")))

    assert run("simulator", "run", URL) == 1

    said = capsys.readouterr().err
    assert API_SECRET_ENV not in said
    assert said.strip().startswith("cannot open a conversation with ")


def test_an_installation_with_nothing_to_say_sends_nothing_at_all(
    run, far_side, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The packaged utterance is a fact about the INSTALLATION, so it is
    settled where the extra's gate is settled: before anything is typed
    is read, and long before anything is sent.

    It used to be read after the check-in and after `--claim`, which
    meant a build that could not speak still rebound the device and sat
    through the activation ceremony to find that out. A command that
    cannot do the thing it was asked for may not change the
    configuration on its way to saying so.

    Bite: with the read back below `_claimed`, the endpoint records four
    requests instead of none and the board is bound to `sam` before the
    refusal is printed.
    """
    monkeypatch.setattr(utterance, "ASSET", "utterance.that-was-never-built")
    endpoint = far_side(answering(body=activating()))

    assert run("simulator", "run", URL, "--claim", "sam") == 1

    assert capsys.readouterr().err.strip() == utterance.NO_UTTERANCE
    assert endpoint.requests == [], "a command with nothing to say reached the network anyway"
