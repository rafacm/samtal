"""Every structured server event, pinned exactly as it is emitted.

The sibling of `test_event_surface_pins.py`, which does this for the
session scope. Everything that file says about why applies here: the
retained JSON records are the observability surface (ADR 2026-08-04), so
a server event's channel, level, sentence and fields are output rather
than an implementation detail, and the suites next door assert what an
event is about while this one asserts what it *is*. Per emit path it
pins the same five things:

- `record.name`, the channel, which is the `logger` field of the JSON
  line, and which milestone 2 must not move: each subsystem's emitter is
  built on the module logger name it already had;
- `record.levelno`, because a level is part of the surface, and because
  two of these events are structured `logger.debug` calls whose level a
  migration could quietly promote;
- `record.msg`, the unrendered template, which is what catches a
  reworded sentence, a lost `%` argument, and a `%d` quietly becoming a
  `%s`;
- `record.args`, the substituted values themselves, by value and by
  type, which is what catches two arguments swapping places even where
  the rendering happens to read the same;
- the exact set of nonstandard record attributes and their values, read
  through `logs.py`'s own standard-attribute set so this suite and the
  JSON formatter cannot come to disagree about what an event field is.

The template and the arguments are the pin. `sentence` is carried
alongside as the rendering a person reads in a review diff, and it is
deliberately the weaker of the two: `scrub=` replaces the strings that
move between runs (a `tmp_path`, an activation code), and then every
numeric run becomes `<n>`, so numeric literals inside it are not pinned
at all.

Values that move between runs are named rather than guessed. `dynamic=`
names the payload fields whose value is not pinned (the key still is),
and `dynamic_args=` the argument positions, which keep their type as
`<float>` or `<PosixPath>` so a duration that turned into a string is
still a failure. An exception rendered into a sentence is always a
declared position: two exceptions carrying the same message are not
equal to each other, so only their class can be pinned.

Unlike the session scope, `session` is pinned rather than normalized:
these paths are driven directly enough to name the session themselves,
and a server event carries no session identity of its own.

Written before the hand-built `extra={...}` dicts moved onto
`ServerEvents` (#138, milestone 2) and left untouched through the move,
which is what makes it evidence rather than a description. The template
and argument pins arrived after the move, following the PR #152 review
round that added them to the session suite next door, and were checked
against the pre-migration tree as well as this one.
"""

import logging
import os
import re
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openai import AsyncOpenAI
from starlette.websockets import WebSocketDisconnect

from samtal_server import onboarding
from samtal_server.app import create_app
from samtal_server.capture import CaptureStore, SessionCapture
from samtal_server.config import Config
from samtal_server.config.api import build_api
from samtal_server.config.loader import StorageError
from samtal_server.device.bindings import DeviceBindings
from samtal_server.events import Emission, attach_server_tap, detach_server_tap
from samtal_server.filler import build_agent_fillers
from samtal_server.logs import _STANDARD_ATTRIBUTES, JsonFormatter
from samtal_server.onboarding import BUDGET_SPENT
from samtal_server.ota import ACTIVATE_SEGMENT, OTA_PATH
from samtal_server.providers import build_agent_providers
from samtal_server.providers.openai_asr import OpenAiAsr
from samtal_server.tools.mcp import McpServers
from samtal_server.tools.memory import MemoryStore
from tests.unit.test_capture import MANIFEST, store, tone
from tests.unit.test_device_bindings import AGENT, STAGES, booted
from tests.unit.test_device_bindings import BOUND_MAC as DB_BOUND_MAC
from tests.unit.test_device_bindings import DEVICE_MAC as DB_DEVICE_MAC
from tests.unit.test_device_bindings import check_in as db_check_in
from tests.unit.test_drain import FakeSession, registry_with
from tests.unit.test_onboarding_activation import (
    BOUND_MAC,
    NORMALIZED,
    activate,
    check_in,
    unbound_config,
)
from tests.unit.test_onboarding_activation import client_for as activation_client
from tests.unit.test_ota import (
    DEVICE_MAC,
    DEVICE_UUID,
    MOCK_AGENT,
    MOCK_PROVIDERS,
    SYSTEM_INFO,
    post_system_info,
)
from tests.unit.test_ota import client_for as ota_client
from tests.unit.test_session import config_with_agent, connect, shake_hands
from tests.unit.test_session_events import only
from tests.unit.test_session_filler import BrokenTts, masked_config
from tests.unit.test_tools_mcp import SHADOWED_POSITION
from tests.unit.test_tools_mcp import config_granting as mcp_granting
from tests.unit.test_tools_mcp import entry_data as mcp_entry_data
from tests.unit.test_tools_mcp import running as mcp_running
from tests.unit.test_tools_mcp import stdio_entry as mcp_entry
from tests.unit.test_tools_mcp_reload import config_with as mcp_config
from tests.unit.test_tools_mcp_reload import reading as mcp_reading
from tests.unit.test_tools_mcp_reload import started as mcp_started
from tests.unit.test_tools_memory import _corrupt
from tests.unit.test_ws_auth import device_headers, handshake

# What a value that moves between runs is replaced by, so that the key
# is pinned and the value deliberately is not. The same spelling the
# session pin suite uses.
DYNAMIC = "<dynamic>"

_NUMBER = re.compile(r"\d+(?:\.\d+)?")

# One 16 kHz second of s16le silence, which the ASR guard's paths are
# driven with; the same clip test_providers_openai_asr.py uses.
ONE_SECOND = b"\x00\x00" * 16000

# Not a real credential, and shaped so a substring check for it cannot
# match by accident. It stands in for whatever a value a pin blesses
# could turn out to be: a key, a header a stranger chose, a message an
# exception carried up from a dependency.
SENTINEL = "sk-test-6c1e9a4f-never-a-real-credential"


class Consumer:
    """A server-scope tap that keeps what it was handed.

    A pin says what a record holds; it cannot say what a *consumer*
    holds, and the two are not the same object. `Emission.args` is
    deliberately not copied for a tap (the payload is), so anything
    passed as a `%` argument reaches every consumer as the object
    itself. A claim that a value reaches nobody is therefore asserted
    here as well as at the log."""

    def __init__(self) -> None:
        self.seen: list[Emission] = []

    def emit(self, emission: Emission) -> None:
        self.seen.append(emission)

    def saw(self, event: str) -> list[Emission]:
        return [one for one in self.seen if one.payload.get("event") == event]

    def rendered(self) -> str:
        """Everything a consumer could read off what it was handed:
        every payload and every argument, including what an exception
        renders as and what its chain renders as."""
        parts = []
        for emission in self.seen:
            parts.append(str(emission.payload))
            for argument in emission.args:
                parts += [str(argument), repr(argument)]
                cause = getattr(argument, "__cause__", None) or getattr(
                    argument, "__context__", None
                )
                while cause is not None:
                    parts += [str(cause), repr(cause)]
                    cause = cause.__cause__ or cause.__context__
        return "\n".join(parts)


class Vandal:
    """A tap that edits what it was handed, before the log tap runs.

    Non-log taps dispatch first and are given the emission's own `args`
    tuple. Its members are deliberately not copied (copying an arbitrary
    argument is a copy that can fail), so an object passed as a `%`
    argument is a live object every consumer can read and write before
    the record exists. The only defence is not to pass one, which is
    what this proves: it finds nothing to vandalize."""

    def __init__(self) -> None:
        self.exceptions: list[BaseException] = []

    def emit(self, emission: Emission) -> None:
        for argument in emission.args:
            if isinstance(argument, BaseException):
                self.exceptions.append(argument)
                argument.args = ("injected by a tap",)


def planted() -> Exception:
    """An exception shaped like the ones these catches really meet: a
    message holding a value nobody wrote for a log, and a cause behind
    it holding another, since a renderer that walks the chain reaches
    both."""
    try:
        try:
            raise ValueError(f"while reading {SENTINEL}")
        except ValueError as cause:
            raise OSError(f"gave up on {SENTINEL}") from cause
    except OSError as exc:
        return exc


@pytest.fixture
def vandal() -> Iterator[Vandal]:
    """A consumer attached to the server hub that writes to whatever it
    is handed."""
    consumer = Vandal()
    attach_server_tap(consumer)
    try:
        yield consumer
    finally:
        detach_server_tap(consumer)


@pytest.fixture
def tap() -> Iterator[Consumer]:
    """A consumer attached to the server hub for one test, which is what
    a #66/#67 exporter will be."""
    consumer = Consumer()
    attach_server_tap(consumer)
    try:
        yield consumer
    finally:
        detach_server_tap(consumer)


def logged(caplog: pytest.LogCaptureFixture) -> str:
    """Every record this server wrote, in both shipped formats, so a
    sentinel is hunted in the human sentence, in the JSON object, and in
    the arguments behind both.

    Only this server's channels. A driver that goes through
    `TestClient` puts httpx's own request line in `caplog` too, and what
    the test's HTTP client says about the URL it just fetched is not
    something this server chose to write."""
    formatter = JsonFormatter()
    return "\n".join(
        f"{record.getMessage()}\n{record.args!r}\n{formatter.format(record)}"
        for record in caplog.records
        if record.name.startswith("samtal_server")
    )


# What the MCP test server publishes under the entry `tools`, in the
# order it lists them, which is what the connect sentence prints. Six of
# the seven it registers: one is dropped by the publishing rule for
# being too long once the entry prefix is on it.
PUBLISHED = (
    "tools__secret_word, tools__add, tools__slow_answer, tools__always_fails, "
    "tools__weather_today_v2, tools__inside__secret_word"
)


def payload_of(record: logging.LogRecord) -> dict[str, Any]:
    """The structured half of a record: exactly the attributes the JSON
    formatter emits as top-level keys, read through `logs.py`'s own
    standard-attribute set rather than through a list written here."""
    return {key: value for key, value in vars(record).items() if key not in _STANDARD_ATTRIBUTES}


def args_of(record: logging.LogRecord, dynamic_args: tuple[int, ...]) -> tuple[Any, ...]:
    """The values substituted into the template, in order.

    A declared-dynamic position keeps its type rather than its value, so
    an argument that stopped being a float is still a failure."""
    return tuple(
        f"<{type(value).__name__}>" if index in dynamic_args else value
        for index, value in enumerate(record.args or ())
    )


def pinned(
    record: logging.LogRecord,
    *,
    dynamic: tuple[str, ...] = (),
    dynamic_args: tuple[int, ...] = (),
    scrub: tuple[str, ...] = (),
) -> dict[str, Any]:
    """What one emit path produces, in the dimensions a consumer sees.

    `dynamic` names the payload fields whose value is not pinned (the key
    still is), `dynamic_args` the argument positions, and `scrub` the
    strings replaced wherever they appear in the rendered sentence,
    before the numbers in it are normalized."""
    fields = {
        key: DYNAMIC if key in dynamic else value for key, value in payload_of(record).items()
    }
    sentence = record.getMessage()
    for text in scrub:
        sentence = sentence.replace(text, DYNAMIC)
    return {
        "logger": record.name,
        "level": record.levelno,
        "template": record.msg,
        "args": args_of(record, dynamic_args),
        "sentence": _NUMBER.sub("<n>", sentence),
        "fields": fields,
    }


# The board and firmware the OTA drivers report, as they survive the
# numeric normalization above. Spelled out here rather than inline,
# because a board name full of model numbers is unreadable as a literal.
BOARD = _NUMBER.sub("<n>", SYSTEM_INFO["board"]["type"])
FIRMWARE = _NUMBER.sub("<n>", SYSTEM_INFO["application"]["version"])

# The identity the OTA drivers check in with: the header as the firmware
# sends it, which is what the sentence carries, and its normalized form,
# which is what the field carries.
REPORTED = DEVICE_MAC
RESOLVED = DEVICE_MAC.lower()


# --- ota.py: the configuration check and the activation ceremony ------


def test_ota_check_offering_an_activation_code(caplog: pytest.LogCaptureFixture) -> None:
    client = activation_client()

    with caplog.at_level("WARNING"):
        code = check_in(client)["activation"]["code"]

    assert pinned(
        only(caplog, "ota_check"), dynamic_args=(3, 4), dynamic=("code",), scrub=(code,)
    ) == {
        "logger": "samtal_server.ota",
        "level": logging.WARNING,
        "template": (
            "device %s (%s, firmware %s) has no agent and is showing activation code %s; bind it "
            "with: samtal-server config add-device %s <agent>"
        ),
        "args": (
            REPORTED,
            SYSTEM_INFO["board"]["type"],
            SYSTEM_INFO["application"]["version"],
            "<str>",
            "<str>",
        ),
        "sentence": (
            f"device {REPORTED} ({BOARD}, firmware {FIRMWARE}) has no agent and is "
            f"showing activation code {DYNAMIC}; bind it with: samtal-server config "
            f"add-device {DYNAMIC} <agent>"
        ),
        "fields": {
            "event": "ota_check",
            "device": RESOLVED,
            "client": DEVICE_UUID,
            "board": SYSTEM_INFO["board"]["type"],
            "firmware": SYSTEM_INFO["application"]["version"],
            "agents": [],
            "unloaded": [],
            "code": DYNAMIC,
        },
    }


def test_ota_check_naming_an_agent_this_server_never_loaded(
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = unbound_config()
    config.devices[NORMALIZED] = ["written-since-boot"]
    client = TestClient(create_app(config))

    with caplog.at_level("WARNING"):
        check_in(client)

    assert pinned(only(caplog, "ota_check")) == {
        "logger": "samtal_server.ota",
        "level": logging.WARNING,
        "template": (
            "device %s (%s, firmware %s) is bound to agent %s, which this server has not loaded; "
            "restart to load it"
        ),
        "args": (
            REPORTED,
            SYSTEM_INFO["board"]["type"],
            SYSTEM_INFO["application"]["version"],
            "written-since-boot",
        ),
        "sentence": (
            f"device {REPORTED} ({BOARD}, firmware {FIRMWARE}) is bound to agent "
            "written-since-boot, which this server has not loaded; restart to load it"
        ),
        "fields": {
            "event": "ota_check",
            "device": RESOLVED,
            "client": DEVICE_UUID,
            "board": SYSTEM_INFO["board"]["type"],
            "firmware": SYSTEM_INFO["application"]["version"],
            "agents": [],
            "unloaded": ["written-since-boot"],
        },
    }


def test_ota_check_with_no_agent_at_all(caplog: pytest.LogCaptureFixture) -> None:
    client = ota_client(Config(server={"onboarding": {"enabled": False}}))

    with caplog.at_level("WARNING"):
        post_system_info(client)

    assert pinned(only(caplog, "ota_check")) == {
        "logger": "samtal_server.ota",
        "level": logging.WARNING,
        "template": (
            "device %s (%s, firmware %s) has no agent: bind it under devices or set default_agent"
        ),
        "args": (REPORTED, SYSTEM_INFO["board"]["type"], SYSTEM_INFO["application"]["version"]),
        "sentence": (
            f"device {REPORTED} ({BOARD}, firmware {FIRMWARE}) has no agent: bind it "
            "under devices or set default_agent"
        ),
        "fields": {
            "event": "ota_check",
            "device": RESOLVED,
            "client": DEVICE_UUID,
            "board": SYSTEM_INFO["board"]["type"],
            "firmware": SYSTEM_INFO["application"]["version"],
            "agents": [],
            "unloaded": [],
        },
    }


def test_ota_check_resolving_to_an_agent(caplog: pytest.LogCaptureFixture) -> None:
    config = Config(
        providers=MOCK_PROVIDERS, agents={"assistant": MOCK_AGENT}, default_agent="assistant"
    )

    with caplog.at_level("INFO"):
        post_system_info(ota_client(config))

    assert pinned(only(caplog, "ota_check")) == {
        "logger": "samtal_server.ota",
        "level": logging.INFO,
        "template": "device %s (%s, firmware %s) resolved to agent %s%s",
        "args": (
            REPORTED,
            SYSTEM_INFO["board"]["type"],
            SYSTEM_INFO["application"]["version"],
            "assistant",
            "",
        ),
        "sentence": (
            f"device {REPORTED} ({BOARD}, firmware {FIRMWARE}) resolved to agent assistant"
        ),
        "fields": {
            "event": "ota_check",
            "device": RESOLVED,
            "client": DEVICE_UUID,
            "board": SYSTEM_INFO["board"]["type"],
            "firmware": SYSTEM_INFO["application"]["version"],
            "agents": ["assistant"],
            "unloaded": [],
        },
    }


def test_activation_not_offered_because_the_database_could_not_be_read(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """An unbound device whose bindings answer is a fallback rather than
    an answer. Minting a code off one would offer a claim ticket for a
    board somebody has already bound."""
    config = booted(tmp_path, devices={DB_BOUND_MAC: ["assistant"]})
    with TestClient(create_app(config)) as client:
        (tmp_path / "samtal.db").write_bytes(b"this is not a database")

        with caplog.at_level("WARNING"):
            db_check_in(client)

    assert pinned(only(caplog, "activation_not_offered")) == {
        "logger": "samtal_server.ota",
        "level": logging.WARNING,
        "template": (
            "device %s is unbound in the configuration this server started with, but the "
            "database could not be read, so no activation code was issued: this device may "
            "already be bound. Fix the database and it is offered one at its next check"
        ),
        "args": (DB_DEVICE_MAC,),
        "sentence": (
            f"device {DB_DEVICE_MAC} is unbound in the configuration this server "
            "started with, but the database could not be read, so no activation code "
            "was issued: this device may already be bound. Fix the database and it is "
            "offered one at its next check"
        ),
        "fields": {
            "event": "activation_not_offered",
            "device": DB_DEVICE_MAC,
            "reason": "unreadable",
        },
    }


def test_activation_not_offered_because_the_mint_budget_is_spent(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The budget lowered to nothing rather than thirty check-ins run
    through the endpoint: what is under test is the line, not the
    counter, which test_onboarding_activation.py drives for real."""
    monkeypatch.setattr("samtal_server.onboarding.MINT_BUDGET", 0)
    client = activation_client()

    with caplog.at_level("WARNING"):
        check_in(client)

    assert pinned(only(caplog, "activation_not_offered")) == {
        "logger": "samtal_server.ota",
        "level": logging.WARNING,
        "template": (
            "device %s is unbound but was offered no activation code: %s. It is answered "
            "exactly as it was before onboarding existed, with no token; bind it by its MAC "
            "with: samtal-server config bind-device %s <agent>"
        ),
        "args": (RESOLVED, BUDGET_SPENT, RESOLVED),
        "sentence": (
            f"device {RESOLVED} is unbound but was offered no activation code: "
            f"{_NUMBER.sub('<n>', BUDGET_SPENT)}. It is answered exactly as it was "
            "before onboarding existed, with no token; bind it by its MAC with: "
            f"samtal-server config bind-device {RESOLVED} <agent>"
        ),
        "fields": {
            "event": "activation_not_offered",
            "device": RESOLVED,
            "reason": BUDGET_SPENT,
        },
    }


def test_activation_complete(caplog: pytest.LogCaptureFixture) -> None:
    client = activation_client()

    with caplog.at_level("INFO"):
        assert activate(client, mac=BOUND_MAC).status_code == 200

    assert pinned(only(caplog, "activation_complete")) == {
        "logger": "samtal_server.ota",
        "level": logging.INFO,
        "template": "device %s is activated: its next configuration check hands it a token",
        "args": (BOUND_MAC,),
        "sentence": (
            f"device {_NUMBER.sub('<n>', BOUND_MAC)} is activated: its next "
            "configuration check hands it a token"
        ),
        "fields": {
            "event": "activation_complete",
            "device": BOUND_MAC,
            "agents": ["assistant"],
        },
    }


def test_activation_pending(caplog: pytest.LogCaptureFixture) -> None:
    """The one server event below INFO besides the bindings snapshot
    line, so its level is what a migration is most likely to move."""
    client = activation_client()
    code = check_in(client)["activation"]["code"]

    with caplog.at_level("DEBUG"):
        assert activate(client).status_code == 202

    assert pinned(only(caplog, "activation_pending"), dynamic=("code",)) == {
        "logger": "samtal_server.ota",
        "level": logging.DEBUG,
        "template": "device %s is still waiting to be claimed",
        "args": (RESOLVED,),
        "sentence": f"device {RESOLVED} is still waiting to be claimed",
        "fields": {
            "event": "activation_pending",
            "device": RESOLVED,
            "code": DYNAMIC,
            "unloaded": [],
        },
    }
    assert only(caplog, "activation_pending").code == code


def test_activation_refused_by_an_unreadable_body(caplog: pytest.LogCaptureFixture) -> None:
    client = activation_client()
    check_in(client)

    with caplog.at_level("WARNING"):
        client.post(
            f"{OTA_PATH}{ACTIVATE_SEGMENT}",
            content=b"not json at all",
            headers={"Device-Id": DEVICE_MAC, "Activation-Version": "2"},
        )

    assert pinned(only(caplog, "activation_refused"), dynamic=("code",)) == {
        "logger": "samtal_server.ota",
        "level": logging.WARNING,
        "template": (
            "device %s sent a version-2 activation body that is not a JSON object; it is answered "
            "as still waiting. Nothing of the body is quoted here"
        ),
        "args": (RESOLVED,),
        "sentence": (
            f"device {RESOLVED} sent a version-<n> activation body that is not a JSON "
            "object; it is answered as still waiting. Nothing of the body is quoted here"
        ),
        "fields": {
            "event": "activation_refused",
            "device": RESOLVED,
            "code": DYNAMIC,
            "reason": "unreadable_body",
        },
    }


def test_activation_refused_by_an_unknown_algorithm(caplog: pytest.LogCaptureFixture) -> None:
    client = activation_client()
    challenge = check_in(client)["activation"]["challenge"]

    with caplog.at_level("WARNING"):
        activate(
            client,
            body={"algorithm": "rot13", "challenge": challenge, "hmac": "00"},
            version="2",
        )

    assert pinned(only(caplog, "activation_refused"), dynamic=("code",)) == {
        "logger": "samtal_server.ota",
        "level": logging.WARNING,
        "template": (
            "device %s sent a version-2 activation body naming an algorithm this server does not "
            "know; it is answered as still waiting. The value is not quoted here, since it is "
            "whatever the request carried"
        ),
        "args": (RESOLVED,),
        "sentence": (
            f"device {RESOLVED} sent a version-<n> activation body naming an algorithm "
            "this server does not know; it is answered as still waiting. The value is "
            "not quoted here, since it is whatever the request carried"
        ),
        "fields": {
            "event": "activation_refused",
            "device": RESOLVED,
            "code": DYNAMIC,
            "reason": "unknown_algorithm",
        },
    }


def test_activation_refused_by_a_challenge_mismatch(caplog: pytest.LogCaptureFixture) -> None:
    client = activation_client()
    check_in(client)

    with caplog.at_level("WARNING"):
        activate(
            client,
            body={"algorithm": "hmac-sha256", "challenge": "11:22:33:44:55:66", "hmac": "00"},
            version="2",
        )

    assert pinned(only(caplog, "activation_refused"), dynamic=("code",)) == {
        "logger": "samtal_server.ota",
        "level": logging.WARNING,
        "template": (
            "device %s sent a version-2 activation body answering a challenge this server did not "
            "issue for it; it is answered as still waiting"
        ),
        "args": (RESOLVED,),
        "sentence": (
            f"device {RESOLVED} sent a version-<n> activation body answering a "
            "challenge this server did not issue for it; it is answered as still waiting"
        ),
        "fields": {
            "event": "activation_refused",
            "device": RESOLVED,
            "code": DYNAMIC,
            "reason": "challenge_mismatch",
        },
    }


def test_ota_request_rejected(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING"):
        assert post_system_info(ota_client(), device_id=None).status_code == 400

    assert pinned(only(caplog, "ota_request_rejected")) == {
        "logger": "samtal_server.ota",
        "level": logging.WARNING,
        "template": "rejected OTA request: %s",
        "args": ("the Device-Id header is required and holds the device MAC",),
        "sentence": (
            "rejected OTA request: the Device-Id header is required and holds the "
            "device MAC"
        ),
        "fields": {"event": "ota_request_rejected"},
    }


# --- onboarding.py: the banner and the key that missed ----------------

# A pinned key rather than a derived one, so what the tests below hunt
# for is a literal instead of something recomputed from the secret by
# the code under test. Pinning is a supported configuration: it is what
# carries provisioned boards across a secret rotation.
PINNED_KEY = "ABCDEFGH"


def banner_config(**onboarding_options: object) -> Config:
    return Config(
        server={
            "public_url": "https://voice.example",
            "onboarding": {"key": PINNED_KEY, **onboarding_options},
        }
    )


def test_onboarding_banner_with_onboarding_on(caplog: pytest.LogCaptureFixture) -> None:
    """The narrowing the PR #153 review asked for: the banner names the
    origin and where to read the URL, and no longer the URL itself. The
    key stands in front of the endpoint that issues device tokens, and a
    startup line is a retained record like every other."""
    with caplog.at_level("INFO"):
        onboarding.log_banner(banner_config().server)

    assert pinned(only(caplog, "onboarding_banner")) == {
        "logger": "samtal_server.onboarding",
        "level": logging.INFO,
        "template": (
            "device onboarding is on: devices are configured on %s (%s), at the short path "
            "samtal-server config ota-url prints. The path is not repeated here, since its key "
            "stands in front of the endpoint that issues device tokens"
        ),
        "args": ("https://voice.example", "from server.public_url"),
        "sentence": (
            "device onboarding is on: devices are configured on https://voice.example "
            "(from server.public_url), at the short path samtal-server config ota-url "
            "prints. The path is not repeated here, since its key stands in front of "
            "the endpoint that issues device tokens"
        ),
        "fields": {
            "event": "onboarding_banner",
            "origin": "https://voice.example",
            "origin_source": "server.public_url",
            "onboarding": True,
            "keyed": True,
        },
    }


def test_a_keyless_short_route_says_so_rather_than_naming_a_key(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """With device auth off there is no secret to derive a key from and
    the route mounts at /x/ bare. That is a fact about the deployment
    rather than about the key, which is what makes it safe to say."""
    config = Config(server={"public_url": "https://voice.example", "auth": {"enabled": False}})

    with caplog.at_level("INFO"):
        onboarding.log_banner(config.server)

    assert payload_of(only(caplog, "onboarding_banner"))["keyed"] is False


def test_the_onboarding_key_reaches_no_record_and_no_consumer(
    caplog: pytest.LogCaptureFixture, tap: Consumer
) -> None:
    """The sentinel for the banner. A pin says the sentence is what it
    is; it does not say the sentence is safe. The key is planted as the
    pinned one, so a URL built from it anywhere in the line, the fields,
    the arguments or a consumer's copy is found."""
    key = "S7K3XQ2M"
    config = Config(
        server={"public_url": "https://voice.example", "onboarding": {"key": key}}
    )

    with caplog.at_level("DEBUG"):
        onboarding.log_banner(config.server)

    banner = only(caplog, "onboarding_banner")
    assert key not in banner.getMessage()
    assert key not in str(banner.args)
    assert key not in str(payload_of(banner))
    assert key not in logged(caplog)
    assert tap.saw("onboarding_banner"), "it reached no tap at all, so this proves nothing"
    assert key not in tap.rendered()
    # And the line still does its job: which deployment, and where the
    # URL comes from.
    assert banner.origin == "https://voice.example"
    assert "samtal-server config ota-url" in banner.getMessage()


def test_onboarding_banner_with_onboarding_off(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("INFO"):
        onboarding.log_banner(banner_config(enabled=False).server)

    assert pinned(only(caplog, "onboarding_banner")) == {
        "logger": "samtal_server.onboarding",
        "level": logging.INFO,
        "template": (
            "device onboarding is off: devices are configured at the server.ota_path path on %s "
            "(%s), which is not printed here, since that segment is this deployment's secret"
        ),
        "args": ("https://voice.example", "from server.public_url"),
        "sentence": (
            "device onboarding is off: devices are configured at the server.ota_path "
            "path on https://voice.example (from server.public_url), which is not "
            "printed here, since that segment is this deployment's secret"
        ),
        "fields": {
            "event": "onboarding_banner",
            "origin": "https://voice.example",
            "origin_source": "server.public_url",
            "onboarding": False,
        },
    }


def test_onboarding_key_mismatch(caplog: pytest.LogCaptureFixture) -> None:
    """The typo the line exists for: a key a person could have typed.
    Since the PR #153 review neither key is repeated, only the shape of
    what arrived, so the event's name is what says which kind of miss
    this was."""
    client = TestClient(create_app(banner_config()))

    with caplog.at_level("WARNING"):
        assert client.get(f"/x/{PINNED_KEY[:-1]}X/").status_code == 404

    assert pinned(only(caplog, "onboarding_key_mismatch")) == {
        "logger": "samtal_server.onboarding",
        "level": logging.WARNING,
        "template": (
            "a request reached the onboarding path carrying %d characters shaped like a key, and "
            "not this server's; neither is repeated here. Check the URL typed into the device's "
            "captive portal against the one samtal-server config ota-url prints"
        ),
        "args": (8,),
        "sentence": (
            "a request reached the onboarding path carrying <n> characters shaped "
            "like a key, and not this server's; neither is repeated here. Check the "
            "URL typed into the device's captive portal against the one "
            "samtal-server config ota-url prints"
        ),
        "fields": {"event": "onboarding_key_mismatch", "attempted_length": 8},
    }


def test_neither_key_reaches_a_record_or_a_consumer_on_a_miss(
    caplog: pytest.LogCaptureFixture, tap: Consumer
) -> None:
    """The sentinel for the miss, and it needs two: the server's own key
    is what a probe was fishing for, and the attempted one is a string a
    stranger chose that a near miss would turn into a hint at the real
    one."""
    key = "S7K3XQ2M"
    attempted = "S7K3XQ2N"
    client = TestClient(
        create_app(Config(server={"onboarding": {"key": key}}))
    )

    with caplog.at_level("DEBUG"):
        assert client.get(f"/x/{attempted}/").status_code == 404

    miss = only(caplog, "onboarding_key_mismatch")
    for planted in (key, attempted):
        assert planted not in miss.getMessage()
        assert planted not in str(miss.args)
        assert planted not in str(payload_of(miss))
        assert planted not in logged(caplog)
        assert planted not in tap.rendered()
    assert tap.saw("onboarding_key_mismatch"), "it reached no tap at all"
    assert miss.attempted_length == len(attempted)


def test_onboarding_key_unshaped(caplog: pytest.LogCaptureFixture) -> None:
    """An attempt nothing a person types could have produced: counted
    rather than quoted, which is what keeps a chosen string out of the
    log entirely."""
    client = TestClient(create_app(banner_config()))

    with caplog.at_level("WARNING"):
        assert client.get(f"/x/{'A' * 500}/").status_code == 404

    assert pinned(only(caplog, "onboarding_key_unshaped")) == {
        "logger": "samtal_server.onboarding",
        "level": logging.WARNING,
        "template": (
            "a request reached the onboarding path carrying %d characters that are not shaped like "
            "a key at all, so they are not repeated here; the URL to type comes from "
            "samtal-server config ota-url"
        ),
        "args": (500,),
        "sentence": (
            "a request reached the onboarding path carrying <n> characters that are "
            "not shaped like a key at all, so they are not repeated here; the URL to "
            "type comes from samtal-server config ota-url"
        ),
        "fields": {"event": "onboarding_key_unshaped", "attempted_length": 500},
    }


# --- capture.py: what a recording says about itself -------------------


def test_capture_started(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    keeper = store(tmp_path)

    with caplog.at_level("INFO"):
        capture = keeper.open("s1", time.monotonic(), MANIFEST)

    assert capture is not None
    record = only(caplog, "capture_started")
    assert pinned(record, dynamic_args=(1,), dynamic=("path",), scrub=(str(capture.wav_path),)) == {
        "logger": "samtal_server.capture",
        "level": logging.INFO,
        "template": "session %s: capturing to %s",
        "args": ("s1", "<PosixPath>"),
        "sentence": f"session s<n>: capturing to {DYNAMIC}",
        "fields": {"event": "capture_started", "session": "s1", "path": DYNAMIC},
    }
    assert record.path == str(capture.wav_path)
    capture.close()


def test_capture_declined_because_the_directory_is_unusable(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failure is planted rather than provoked, because what an
    unwritable volume raises is the operating system's sentence and not
    a stable one; the pin is that whatever it says is rendered here."""
    keeper = store(tmp_path)
    monkeypatch.setattr(
        CaptureStore, "_free_mb", lambda self: (_ for _ in ()).throw(OSError("the volume said no"))
    )

    with caplog.at_level("WARNING"):
        assert keeper.open("s1", time.monotonic(), MANIFEST) is None

    assert pinned(
        only(caplog, "capture_declined"),
        dynamic_args=(1,),
        scrub=(str(keeper.directory),),
    ) == {
        "logger": "samtal_server.capture",
        "level": logging.WARNING,
        "template": "session %s: not capturing, %s is unusable (%s)",
        "args": ("s1", "<PosixPath>", "OSError"),
        "sentence": f"session s<n>: not capturing, {DYNAMIC} is unusable (OSError)",
        "fields": {
            "event": "capture_declined",
            "session": "s1",
            "reason": "unusable",
            "failure": "OSError",
        },
    }


def test_capture_declined_because_the_volume_is_nearly_full(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    keeper = store(tmp_path, min_free_mb=10_000_000.0)

    with caplog.at_level("WARNING"):
        assert keeper.open("s1", time.monotonic(), MANIFEST) is None

    assert pinned(only(caplog, "capture_declined"), dynamic_args=(1,), dynamic=("free_mb",)) == {
        "logger": "samtal_server.capture",
        "level": logging.WARNING,
        "template": "session %s: not capturing, %.0f MB free is below the %.0f MB floor",
        "args": ("s1", "<float>", 10_000_000.0),
        "sentence": "session s<n>: not capturing, <n> MB free is below the <n> MB floor",
        "fields": {
            "event": "capture_declined",
            "session": "s1",
            "reason": "min_free_mb",
            "free_mb": DYNAMIC,
        },
    }


def test_capture_declined_because_the_files_would_not_open(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    keeper = store(tmp_path)
    monkeypatch.setattr(
        SessionCapture,
        "start",
        lambda self: (_ for _ in ()).throw(OSError("no room for the files")),
    )

    with caplog.at_level("WARNING"):
        assert keeper.open("s1", time.monotonic(), MANIFEST) is None

    assert pinned(only(caplog, "capture_declined")) == {
        "logger": "samtal_server.capture",
        "level": logging.WARNING,
        "template": "session %s: not capturing, could not open the files (%s)",
        "args": ("s1", "OSError"),
        "sentence": "session s<n>: not capturing, could not open the files (OSError)",
        "fields": {
            "event": "capture_declined",
            "session": "s1",
            "reason": "open",
            "failure": "OSError",
        },
    }


def test_capture_limit(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    opened = time.monotonic()
    capture = store(tmp_path, max_session_s=1.0).open("s1", opened, MANIFEST)
    assert capture is not None

    with caplog.at_level("INFO"):
        capture.microphone(tone(500, 1000), opened + 2.0)

    assert pinned(only(caplog, "capture_limit")) == {
        "logger": "samtal_server.capture",
        "level": logging.INFO,
        "template": "session %s: capture reached its %.0f s limit",
        "args": ("s1", 1.0),
        "sentence": "session s<n>: capture reached its <n> s limit",
        "fields": {"event": "capture_limit", "session": "s1"},
    }


def test_capture_failed(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A capture never takes a session with it: the write fails, the
    recording stops, and the conversation carries on."""
    opened = time.monotonic()
    capture = store(tmp_path).open("s1", opened, MANIFEST)
    assert capture is not None
    capture._wav.close()  # type: ignore[union-attr]

    with caplog.at_level("WARNING"):
        capture.microphone(tone(100, 1000), opened)
        capture.microphone(tone(100, 1000), opened + 3.0)

    assert pinned(only(caplog, "capture_failed")) == {
        "logger": "samtal_server.capture",
        "level": logging.WARNING,
        "template": "session %s: capture stopped after failing to %s (%s)",
        "args": ("s1", "write audio", "ValueError"),
        "sentence": "session s<n>: capture stopped after failing to write audio (ValueError)",
        "fields": {
            "event": "capture_failed",
            "session": "s1",
            "reason": "write audio",
            "failure": "ValueError",
        },
    }


def test_a_failed_writes_own_words_reach_no_record_or_consumer(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, tap: Consumer, vandal: Vandal
) -> None:
    """The sentinel for the capture's own failures. `_disable` catches a
    bare `Exception` around a write, so what reaches it is whatever the
    filesystem, the wave module or a JSON encoder raised, and those
    messages carry the path they tripped on or the bytes they choked on.
    Driven through `_disable` directly, because the point is the
    exception's contents and a real failed write raises what it raises.
    """
    capture = store(tmp_path).open("s1", time.monotonic(), MANIFEST)
    assert capture is not None

    with caplog.at_level("DEBUG"):
        capture._disable("write audio", planted())

    failed = only(caplog, "capture_failed")
    assert SENTINEL not in failed.getMessage()
    assert SENTINEL not in str(failed.args)
    assert SENTINEL not in str(payload_of(failed))
    assert SENTINEL not in logged(caplog)
    assert tap.saw("capture_failed"), "it reached no tap at all, so this proves nothing"
    assert SENTINEL not in tap.rendered()
    assert vandal.exceptions == [], "a consumer was handed the exception itself"
    # And the diagnosis survives it: what the capture was doing, and
    # what kind of failure stopped it.
    assert (failed.reason, failed.failure) == ("write audio", "OSError")


def test_an_unusable_directorys_own_words_reach_no_record_or_consumer(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    tap: Consumer,
    vandal: Vandal,
) -> None:
    """The same for the decline, which is the one an operator meets: a
    volume that will not take a capture answers with the operating
    system's own sentence about it."""
    keeper = store(tmp_path)
    monkeypatch.setattr(CaptureStore, "_free_mb", lambda self: (_ for _ in ()).throw(planted()))

    with caplog.at_level("DEBUG"):
        assert keeper.open("s1", time.monotonic(), MANIFEST) is None

    declined = only(caplog, "capture_declined")
    assert SENTINEL not in declined.getMessage()
    assert SENTINEL not in str(declined.args)
    assert SENTINEL not in str(payload_of(declined))
    assert SENTINEL not in logged(caplog)
    assert tap.saw("capture_declined"), "it reached no tap at all"
    assert SENTINEL not in tap.rendered()
    assert vandal.exceptions == []
    assert (declined.reason, declined.failure) == ("unusable", "OSError")


def recorded(tmp_path: Path, sessions: int) -> CaptureStore:
    """A directory with finished captures in it, each a quarter of a
    megabyte and each older than the next, so a budget below their total
    has an unambiguous oldest to drop."""
    roomy = store(tmp_path)
    opened = time.monotonic()
    for index in range(sessions):
        capture = roomy.open(f"s{index}", opened, MANIFEST)
        assert capture is not None
        capture.microphone(tone(3000), opened)
        capture.close()
        for suffix in (".wav", ".jsonl", ".json"):
            path = capture.wav_path.with_suffix(suffix)
            if path.exists():
                os.utime(path, (opened + index, opened + index))
    return roomy


def test_capture_pruned(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    recorded(tmp_path, sessions=2)
    # A second store over the same directory, so the pruning happens
    # where the test is watching rather than inside an earlier close.
    tight = store(tmp_path, max_total_mb=0.3)

    with caplog.at_level("INFO"):
        assert tight.prune() == ["s0"]

    assert pinned(only(caplog, "capture_pruned")) == {
        "logger": "samtal_server.capture",
        "level": logging.INFO,
        "template": "capture: pruned %d session(s) to stay under %.0f MB: %s",
        "args": (1, 0.3, "s0"),
        "sentence": "capture: pruned <n> session(s) to stay under <n> MB: s<n>",
        "fields": {"event": "capture_pruned", "sessions": ["s0"]},
    }


def test_capture_over_budget(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Over the budget with nothing left to drop: the newest capture is
    never pruned, so a budget smaller than one session says so instead
    of deleting the recording somebody went out to make."""
    keeper = recorded(tmp_path, sessions=1)
    keeper._max_total_mb = 0.01

    with caplog.at_level("WARNING"):
        assert keeper.prune() == []

    assert pinned(
        only(caplog, "capture_over_budget"), dynamic_args=(0,), dynamic=("total_mb",)
    ) == {
        "logger": "samtal_server.capture",
        "level": logging.WARNING,
        "template": (
            "capture: %.0f MB on disk is over the %.0f MB budget and nothing more can be pruned; "
            "raise max_total_mb or lower max_session_s"
        ),
        "args": ("<float>", 0.01),
        "sentence": (
            "capture: <n> MB on disk is over the <n> MB budget and nothing more can "
            "be pruned; raise max_total_mb or lower max_session_s"
        ),
        "fields": {"event": "capture_over_budget", "total_mb": DYNAMIC},
    }


# --- app.py: what the boot says about the recording ------------------

# A path that is never written to: `CaptureStore` creates its directory
# when a session opens, and no session opens here.
CAPTURE_DIR = "/var/lib/samtal/captures"


def test_capture_enabled(caplog: pytest.LogCaptureFixture) -> None:
    config = config_with_agent(server={"capture": {"enabled": True, "dir": CAPTURE_DIR}})

    with caplog.at_level("INFO"):
        create_app(config)

    assert pinned(only(caplog, "capture_enabled")) == {
        "logger": "samtal_server.app",
        "level": logging.WARNING,
        "template": "session capture is on: room audio and transcripts are being written to %s",
        "args": (Path(CAPTURE_DIR),),
        "sentence": (
            "session capture is on: room audio and transcripts are being written to "
            f"{CAPTURE_DIR}"
        ),
        "fields": {"event": "capture_enabled", "path": CAPTURE_DIR},
    }


def test_capture_disabled(caplog: pytest.LogCaptureFixture) -> None:
    config = config_with_agent(server={"capture": {"enabled": False, "dir": CAPTURE_DIR}})

    with caplog.at_level("INFO"):
        create_app(config)

    assert pinned(only(caplog, "capture_disabled")) == {
        "logger": "samtal_server.app",
        "level": logging.INFO,
        "template": (
            "session capture is configured but off; set server.capture.enabled to record to %s"
        ),
        "args": (Path(CAPTURE_DIR),),
        "sentence": (
            "session capture is configured but off; set server.capture.enabled to "
            f"record to {CAPTURE_DIR}"
        ),
        "fields": {"event": "capture_disabled", "path": CAPTURE_DIR},
    }


# --- ws.py: the two handshakes that never became sessions -------------


def refused_handshake(
    token: str | None, device_id: str, caplog: pytest.LogCaptureFixture
) -> None:
    """One handshake that never becomes a session, with the Device-Id of
    the caller's choosing."""
    with caplog.at_level("DEBUG"):
        with TestClient(create_app(config_with_agent())) as client:
            with pytest.raises(WebSocketDisconnect):
                with handshake(client, device_headers(token, device_id)):
                    pass


def test_auth_rejected(caplog: pytest.LogCaptureFixture) -> None:
    """No device, and a sentence with nothing of the request in it (the
    PR #153 review). Nothing is authenticated at this point, so the
    Device-Id header is a string whoever opened the socket chose."""
    refused_handshake(None, DEVICE_MAC, caplog)

    assert pinned(only(caplog, "auth_rejected")) == {
        "logger": "samtal_server.ws",
        "level": logging.WARNING,
        "template": "refused a websocket handshake from an unidentified client: %s",
        "args": ("no_token",),
        "sentence": "refused a websocket handshake from an unidentified client: no_token",
        "fields": {"event": "auth_rejected", "device": None, "reason": "no_token"},
    }


@pytest.mark.parametrize(
    ("token", "reason"),
    [(None, "no_token"), ("a-token-this-server-never-issued.1700000000", "bad_token")],
)
def test_a_refused_handshakes_device_id_reaches_nothing(
    token: str | None, reason: str, caplog: pytest.LogCaptureFixture, tap: Consumer
) -> None:
    """The sentinel for both refusals. This endpoint is reachable by
    anything that finds it, and a caller who is turned away can retry as
    fast as the socket opens, so a header echoed here is a value of
    their choosing written into the retained surface once per attempt."""
    refused_handshake(token, SENTINEL, caplog)

    rejected = only(caplog, "auth_rejected")
    assert rejected.reason == reason
    assert SENTINEL not in rejected.getMessage()
    assert SENTINEL not in str(rejected.args)
    assert SENTINEL not in str(payload_of(rejected))
    assert SENTINEL not in logged(caplog)
    assert tap.saw("auth_rejected"), "it reached no tap at all, so this proves nothing"
    assert SENTINEL not in tap.rendered()
    assert payload_of(rejected)["device"] is None


def test_session_rejected_at_capacity(caplog: pytest.LogCaptureFixture) -> None:
    """The other half of `session_rejected`: the edge emits three of
    them on the session channel, and this one is the server's, on the
    websocket router's own channel and with no session behind it.

    This one does name the device, and may: capacity is checked after
    the token, so by here the header is one the token verified against
    rather than a name a stranger sent."""
    config = config_with_agent()
    config.server.limits.max_sessions = 1

    with caplog.at_level("WARNING"):
        with TestClient(create_app(config)) as client:
            with connect(client) as first:
                shake_hands(first)
                with pytest.raises(WebSocketDisconnect):
                    with connect(client):
                        pass

    assert pinned(only(caplog, "session_rejected"), dynamic=("session",)) == {
        "logger": "samtal_server.ws",
        "level": logging.WARNING,
        "template": "refused a websocket handshake from %s: the server is at capacity",
        "args": (RESOLVED,),
        "sentence": f"refused a websocket handshake from {RESOLVED}: the server is at capacity",
        "fields": {
            "event": "session_rejected",
            "device": RESOLVED,
            "session": DYNAMIC,
            "reason": "capacity",
        },
    }


# --- registry.py: the drain -------------------------------------------


async def test_drain_started(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("INFO"):
        await registry_with(FakeSession(), FakeSession()).drain(timeout_s=5)

    assert pinned(only(caplog, "drain_started")) == {
        "logger": "samtal_server.registry",
        "level": logging.INFO,
        "template": "draining %d session(s), up to %.0f s",
        "args": (2, 5),
        "sentence": "draining <n> session(s), up to <n> s",
        "fields": {"event": "drain_started", "sessions": 2, "timeout_s": 5},
    }


async def test_drain_finished(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("INFO"):
        await registry_with(FakeSession()).drain(timeout_s=5)

    assert pinned(only(caplog, "drain_finished")) == {
        "logger": "samtal_server.registry",
        "level": logging.INFO,
        "template": "every session drained",
        "args": (),
        "sentence": "every session drained",
        "fields": {"event": "drain_finished", "sessions": 1},
    }


async def test_drain_incomplete(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("INFO"):
        await registry_with(FakeSession(speaking_for=30)).drain(timeout_s=1.2)

    assert pinned(only(caplog, "drain_incomplete")) == {
        "logger": "samtal_server.registry",
        "level": logging.WARNING,
        "template": "drained with %d session(s) cut mid-reply and %d that did not finish",
        "args": (1, 0),
        "sentence": "drained with <n> session(s) cut mid-reply and <n> that did not finish",
        "fields": {
            "event": "drain_incomplete",
            "sessions": 1,
            "cut_mid_reply": 1,
            "unfinished": 0,
            "timeout_s": 1.2,
        },
    }


# --- filler.py: a voice that could not be cached ----------------------


async def test_filler_disabled(caplog: pytest.LogCaptureFixture) -> None:
    config = masked_config()
    providers = build_agent_providers(config)
    providers["poet"] = replace(providers["poet"], tts=cast(Any, BrokenTts()))

    with caplog.at_level("WARNING"):
        await build_agent_fillers(config, providers)

    assert pinned(only(caplog, "filler_disabled")) == {
        "logger": "samtal_server.filler",
        "level": logging.WARNING,
        "template": (
            "agent %s: filler synthesis failed, latency masking is off for this agent (%s)"
        ),
        "args": ("poet", "RuntimeError"),
        "sentence": (
            "agent poet: filler synthesis failed, latency masking is off for this "
            "agent (RuntimeError)"
        ),
        "fields": {"event": "filler_disabled", "agent": "poet", "error": "RuntimeError"},
    }


async def test_a_broken_voices_own_words_reach_no_record_or_consumer(
    caplog: pytest.LogCaptureFixture, tap: Consumer, vandal: Vandal
) -> None:
    """The sentinel for the boot's one degrading path. The catch is
    around a whole synthesis, so an exception raised near a provider's
    response can carry a fragment of one, and this line is written once
    per agent at every start."""

    class PlantedTts:
        sample_rate = 24000

        def synthesize(self, text: str) -> AsyncIterator[bytes]:
            raise planted()

    config = masked_config()
    providers = build_agent_providers(config)
    providers["poet"] = replace(providers["poet"], tts=cast(Any, PlantedTts()))

    with caplog.at_level("DEBUG"):
        await build_agent_fillers(config, providers)

    disabled = only(caplog, "filler_disabled")
    assert SENTINEL not in disabled.getMessage()
    assert SENTINEL not in str(disabled.args)
    assert SENTINEL not in str(payload_of(disabled))
    assert SENTINEL not in logged(caplog)
    assert tap.saw("filler_disabled"), "it reached no tap at all"
    assert SENTINEL not in tap.rendered()
    assert vandal.exceptions == []
    assert (disabled.agent, disabled.error) == ("poet", "OSError")


# --- device/bindings.py: the live view's two lines ---------------------


def test_device_bindings_snapshot_only(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The second structured DEBUG event of the server scope, and the
    one the plan names: its level is part of the retained surface."""
    directory = tmp_path / "nothing"
    config = Config(
        server={"database": {"dir": str(directory)}},
        providers={stage: {"mock": {"type": "mock"}} for stage in STAGES},
        agents={"assistant": AGENT},
        devices={DB_DEVICE_MAC: ["assistant"]},
    )

    with caplog.at_level("DEBUG"):
        bindings = DeviceBindings.open(config)
    bindings.dispose()

    assert pinned(
        only(caplog, "device_bindings_snapshot_only"),
        dynamic_args=(0,), dynamic=("path",),
        scrub=(str(directory / "samtal.db"),),
    ) == {
        "logger": "samtal_server.device.bindings",
        "level": logging.DEBUG,
        "template": (
            "no configuration database at %s: device bindings resolve from the configuration this "
            "server was built with"
        ),
        "args": ("<PosixPath>",),
        "sentence": (
            f"no configuration database at {DYNAMIC}: device bindings resolve from "
            "the configuration this server was built with"
        ),
        "fields": {"event": "device_bindings_snapshot_only", "path": DYNAMIC},
    }


def test_device_bindings_unreadable(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    config = booted(tmp_path, devices={DB_DEVICE_MAC: ["assistant"]})
    bindings = DeviceBindings.open(config)
    try:
        (tmp_path / "samtal.db").write_bytes(b"this is not a database")

        with caplog.at_level("WARNING"):
            bindings.agents_for(DB_DEVICE_MAC)
    finally:
        bindings.dispose()

    assert pinned(only(caplog, "device_bindings_unreadable"), dynamic=("failure",)) == {
        "logger": "samtal_server.device.bindings",
        "level": logging.WARNING,
        "template": (
            "cannot read the device bindings for %s; answering from the configuration this server "
            "started with, which may be older than the database. The failure's kind is recorded "
            "beside this line"
        ),
        "args": (DB_DEVICE_MAC,),
        "sentence": (
            f"cannot read the device bindings for {DB_DEVICE_MAC}; answering from the "
            "configuration this server started with, which may be older than the "
            "database. The failure's kind is recorded beside this line"
        ),
        "fields": {
            "event": "device_bindings_unreadable",
            "device": DB_DEVICE_MAC,
            "failure": DYNAMIC,
        },
    }


# --- tools/memory.py: a file that will not decode ---------------------


def test_memory_unreadable(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    memories = MemoryStore(tmp_path)
    _corrupt(memories, "poet")

    with caplog.at_level("WARNING"):
        assert memories.read("poet") == ""

    assert pinned(only(caplog, "memory_unreadable")) == {
        "logger": "samtal_server.tools.memory",
        "level": logging.WARNING,
        "template": "could not read memory for agent %s (%s); it remembers nothing this round",
        "args": ("poet", "UnicodeDecodeError"),
        "sentence": (
            "could not read memory for agent poet (UnicodeDecodeError); it remembers "
            "nothing this round"
        ),
        "fields": {
            "event": "memory_unreadable",
            "agent": "poet",
            "error": "UnicodeDecodeError",
        },
    }


# --- config/api.py: the two failures the API records ------------------

API_TOKEN = "test-api-token-" + "0123456789abcdef" * 2


def api_raising(tmp_path: Path, exc: Exception) -> FastAPI:
    api = build_api(API_TOKEN, tmp_path / "db")

    @api.get("/boom")
    def endpoint() -> dict[str, str]:
        raise exc

    return api


def test_api_error(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    api = api_raising(tmp_path, RuntimeError("nothing a log may repeat"))

    with caplog.at_level("ERROR"):
        response = TestClient(api).get(
            "/boom", headers={"Authorization": f"Bearer {API_TOKEN}"}
        )

    assert response.status_code == 500
    assert pinned(only(caplog, "api_error")) == {
        "logger": "samtal_server.config.api",
        "level": logging.ERROR,
        "template": "the configuration API failed to handle a request (%s)",
        "args": ("RuntimeError",),
        "sentence": "the configuration API failed to handle a request (RuntimeError)",
        "fields": {"event": "api_error"},
    }


def test_api_storage_error(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    api = api_raising(tmp_path, StorageError("the options column does not hold an object"))

    with caplog.at_level("ERROR"):
        response = TestClient(api).get(
            "/boom", headers={"Authorization": f"Bearer {API_TOKEN}"}
        )

    assert response.status_code == 500
    assert pinned(only(caplog, "api_storage_error")) == {
        "logger": "samtal_server.config.api",
        "level": logging.ERROR,
        "template": "the configuration API met unreadable stored state (%s)",
        "args": ("StorageError",),
        "sentence": "the configuration API met unreadable stored state (StorageError)",
        "fields": {"event": "api_storage_error"},
    }


# --- providers/openai_asr.py: the echo guard's five outcomes ----------

# The prompt the guard trips on, and the host every one of these events
# names instead of a session: providers are shared singletons.
ECHO_PROMPT = "samtal, Oliver"
ASR_HOST = "api.openai.com"


def echo_provider(handler: object, **overrides: object) -> OpenAiAsr:
    """The provider on a mock transport, wired as
    test_providers_openai_asr.py wires it."""
    client = AsyncOpenAI(
        api_key="test-key",
        max_retries=0,
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
        ),
    )
    options: dict[str, object] = {
        "model": "gpt-4o-mini-transcribe",
        "api_key": "test-key",
        "client": client,
        "prompt": ECHO_PROMPT,
    }
    options.update(overrides)
    return OpenAiAsr(**options)  # type: ignore[arg-type]


def answering(*texts: str) -> object:
    """A transport that answers each request with the next transcript."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"text": texts[min(len(seen), len(texts)) - 1]})

    return handler


async def test_asr_prompt_echo_skipped(caplog: pytest.LogCaptureFixture) -> None:
    asr = echo_provider(answering(ECHO_PROMPT), timeout_s=0.5)

    with caplog.at_level("INFO"):
        assert (await asr.transcribe(ONE_SECOND, 16000)).text == ""

    assert pinned(only(caplog, "asr_prompt_echo"), dynamic_args=(0,)) == {
        "logger": "samtal_server.providers.openai_asr",
        "level": logging.WARNING,
        "template": (
            "openai asr: the transcript came back as the configured prompt with %.1f s of the "
            "timeout left, too little to retry, treating %.2f s of audio as nothing said"
        ),
        "args": ("<float>", 1.0),
        "sentence": (
            "openai asr: the transcript came back as the configured prompt with <n> s "
            "of the timeout left, too little to retry, treating <n> s of audio as "
            "nothing said"
        ),
        "fields": {
            "event": "asr_prompt_echo",
            "outcome": "skipped",
            "duration_s": 1.0,
            "host": ASR_HOST,
        },
    }


async def test_asr_prompt_echo_timed_out(caplog: pytest.LogCaptureFixture) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if not seen:
            seen.append(request)
            return httpx.Response(200, json={"text": ECHO_PROMPT})
        raise httpx.ReadTimeout("the deadline came first", request=request)

    seen: list[httpx.Request] = []
    asr = echo_provider(handler)

    with caplog.at_level("INFO"):
        assert (await asr.transcribe(ONE_SECOND, 16000)).text == ""

    assert pinned(only(caplog, "asr_prompt_echo"), dynamic_args=(0,), dynamic=("retry_ms",)) == {
        "logger": "samtal_server.providers.openai_asr",
        "level": logging.WARNING,
        "template": (
            "openai asr: the retry outran the timeout's remaining %.1f s, treating %.2f s of audio "
            "as nothing said"
        ),
        "args": ("<float>", 1.0),
        "sentence": (
            "openai asr: the retry outran the timeout's remaining <n> s, treating <n> "
            "s of audio as nothing said"
        ),
        "fields": {
            "event": "asr_prompt_echo",
            "outcome": "timed_out",
            "duration_s": 1.0,
            "host": ASR_HOST,
            "retry_ms": DYNAMIC,
        },
    }


async def test_asr_prompt_echo_confirmed_echo(caplog: pytest.LogCaptureFixture) -> None:
    asr = echo_provider(answering(ECHO_PROMPT, ECHO_PROMPT))

    with caplog.at_level("INFO"):
        assert (await asr.transcribe(ONE_SECOND, 16000)).text == ""

    assert pinned(only(caplog, "asr_prompt_echo"), dynamic=("retry_ms",)) == {
        "logger": "samtal_server.providers.openai_asr",
        "level": logging.WARNING,
        "template": (
            "openai asr: the retry came back as the prompt again, treating %.2f s of audio as "
            "nothing said"
        ),
        "args": (1.0,),
        "sentence": (
            "openai asr: the retry came back as the prompt again, treating <n> s of "
            "audio as nothing said"
        ),
        "fields": {
            "event": "asr_prompt_echo",
            "outcome": "confirmed_echo",
            "duration_s": 1.0,
            "host": ASR_HOST,
            "retry_ms": DYNAMIC,
        },
    }


async def test_asr_prompt_echo_confirmed_empty(caplog: pytest.LogCaptureFixture) -> None:
    asr = echo_provider(answering(ECHO_PROMPT, ""))

    with caplog.at_level("INFO"):
        assert (await asr.transcribe(ONE_SECOND, 16000)).text == ""

    assert pinned(only(caplog, "asr_prompt_echo"), dynamic=("retry_ms",)) == {
        "logger": "samtal_server.providers.openai_asr",
        "level": logging.WARNING,
        "template": (
            "openai asr: the retry came back empty, treating %.2f s of audio as nothing said"
        ),
        "args": (1.0,),
        "sentence": (
            "openai asr: the retry came back empty, treating <n> s of audio as "
            "nothing said"
        ),
        "fields": {
            "event": "asr_prompt_echo",
            "outcome": "confirmed_empty",
            "duration_s": 1.0,
            "host": ASR_HOST,
            "retry_ms": DYNAMIC,
        },
    }


async def test_asr_prompt_echo_recovered(caplog: pytest.LogCaptureFixture) -> None:
    """The only one of the five at INFO, because it is the only one
    where the user was heard after all."""
    asr = echo_provider(answering(ECHO_PROMPT, "Yes, please."))

    with caplog.at_level("INFO"):
        assert (await asr.transcribe(ONE_SECOND, 16000)).text == "Yes, please."

    assert pinned(only(caplog, "asr_prompt_echo"), dynamic=("retry_ms",)) == {
        "logger": "samtal_server.providers.openai_asr",
        "level": logging.INFO,
        "template": (
            'openai asr: the retry recovered "%s" from %.2f s of audio the echo guard would have '
            "discarded"
        ),
        "args": ("Yes, please.", 1.0),
        "sentence": (
            'openai asr: the retry recovered "Yes, please." from <n> s of audio the '
            "echo guard would have discarded"
        ),
        "fields": {
            "event": "asr_prompt_echo",
            "outcome": "recovered",
            "duration_s": 1.0,
            "host": ASR_HOST,
            "retry_ms": DYNAMIC,
        },
    }


# --- tools/mcp.py: the five lifecycle events --------------------------
#
# New surface rather than migrated surface, which makes these a
# different kind of evidence from the forty-two above: there is no
# "before" for them to be identical to, so what they hold still is the
# README's table from the day it was written. The lifecycle suites next
# door assert what each event means; these assert what each one is.
#
# Everything the test server publishes is pinned verbatim, tool count
# and listing and shadowed position alike. None of it moves between
# runs: `tests/support/mcp_stdio_server.py` is small on purpose and
# fixed on purpose, and a tool added to it is exactly the sort of change
# that should have to look at what this surface says.


async def test_mcp_connected(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("INFO"):
        manager = await mcp_running(mcp_entry())
        await manager.stop()

    assert pinned(only(caplog, "mcp_connected"), dynamic=("duration_ms",)) == {
        "logger": "samtal_server.tools.mcp",
        "level": logging.INFO,
        "template": "mcp server %s connected with %d tool(s): %s",
        "args": ("tools", 6, PUBLISHED),
        "sentence": f"mcp server tools connected with <n> tool(s): {_NUMBER.sub('<n>', PUBLISHED)}",
        "fields": {
            "event": "mcp_connected",
            "entry": "tools",
            "transport": "stdio",
            # A count, never a list. The names are in the sentence.
            "tools": 6,
            "duration_ms": DYNAMIC,
        },
    }


async def test_mcp_down(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("INFO"):
        manager = await mcp_running(mcp_entry(command="/nonexistent/mcp-server", args=[]))
        await manager.stop()

    assert pinned(only(caplog, "mcp_down"), dynamic=("duration_ms",)) == {
        "logger": "samtal_server.tools.mcp",
        "level": logging.WARNING,
        "template": "mcp server %s is unavailable, its tools are absent: %s",
        # The exception's type name, which is the diagnosis this
        # sentence has always carried, beside the closed token the field
        # carries. Never a message: this one would quote a path.
        "args": ("tools", "FileNotFoundError"),
        "sentence": "mcp server tools is unavailable, its tools are absent: FileNotFoundError",
        "fields": {
            "event": "mcp_down",
            "entry": "tools",
            "reason": "transport_failed",
            "duration_ms": DYNAMIC,
        },
    }


async def test_mcp_call_dropped(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = await mcp_running(mcp_entry())
    try:

        async def refuse(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("a message from nowhere near this line")

        monkeypatch.setattr(manager._session, "call_tool", refuse)
        with caplog.at_level("INFO"), pytest.raises(RuntimeError):
            await manager.call("tools__secret_word", {})
    finally:
        await manager.stop()

    assert pinned(only(caplog, "mcp_call_dropped")) == {
        "logger": "samtal_server.tools.mcp",
        "level": logging.WARNING,
        "template": "mcp server %s: the call to %s failed, so its answer is lost",
        # The published name, which this server's own publishing rule
        # made, rather than whatever the far side listed.
        "args": ("tools", "tools__secret_word"),
        "sentence": (
            "mcp server tools: the call to tools__secret_word failed, so its answer is lost"
        ),
        "fields": {
            "event": "mcp_call_dropped",
            "entry": "tools",
            "tool": "tools__secret_word",
        },
    }


async def test_mcp_reload(caplog: pytest.LogCaptureFixture) -> None:
    before = mcp_config({"tools": mcp_entry_data()}, {"assistant": ["tools"]})
    after = mcp_config(
        {"tools": mcp_entry_data(), "extra": mcp_entry_data()},
        {"assistant": ["tools", "extra"]},
    )
    servers = await mcp_started(before)
    try:
        with caplog.at_level("INFO"):
            await servers.reload(mcp_reading(after))
    finally:
        await servers.stop_all()

    assert pinned(only(caplog, "mcp_reload"), dynamic=("duration_ms",)) == {
        "logger": "samtal_server.tools.mcp",
        "level": logging.INFO,
        "template": "mcp servers reloaded: %d started, %d restarted, %d stopped, %d unchanged",
        "args": (1, 0, 0, 1),
        "sentence": (
            "mcp servers reloaded: <n> started, <n> restarted, <n> stopped, <n> unchanged"
        ),
        "fields": {
            "event": "mcp_reload",
            "outcome": "applied",
            "started": 1,
            "restarted": 0,
            "stopped": 0,
            "unchanged": 1,
            "duration_ms": DYNAMIC,
        },
    }


async def test_mcp_tool_shadowed(caplog: pytest.LogCaptureFixture) -> None:
    servers = McpServers.build(
        mcp_granting(
            {"home": mcp_entry_data(), "home__inside": mcp_entry_data()},
            {"assistant": ["home", "home__inside"]},
        )
    )
    try:
        with caplog.at_level("INFO"):
            await servers.start_all()
            servers.tools_for_agent("assistant")
    finally:
        await servers.stop_all()

    assert pinned(only(caplog, "mcp_tool_shadowed")) == {
        "logger": "samtal_server.tools.mcp",
        "level": logging.WARNING,
        "template": (
            "mcp server %s: dropping published tool %d, its name is inside the "
            "namespace of the entry %s, which owns it"
        ),
        # No tool name among the arguments, which is what the position
        # is there instead of. Seven rather than six: it is where the
        # far side listed the tool, and the sixth of its listing never
        # published.
        "args": ("home", SHADOWED_POSITION, "home__inside"),
        "sentence": (
            "mcp server home: dropping published tool <n>, its name is inside the "
            "namespace of the entry home__inside, which owns it"
        ),
        "fields": {
            "event": "mcp_tool_shadowed",
            "entry": "home",
            "position": SHADOWED_POSITION,
            "owner": "home__inside",
        },
    }
