"""Every structured server event, pinned exactly as it is emitted.

The sibling of `test_event_surface_pins.py`, which does this for the
session scope. Everything that file says about why applies here: the
retained JSON records are the observability surface (ADR 2026-08-04), so
a server event's channel, level, sentence and fields are output rather
than an implementation detail, and the suites next door assert what an
event is about while this one asserts what it *is*. Per emit path it
pins the same four things:

- `record.name`, the channel, which is the `logger` field of the JSON
  line, and which milestone 2 must not move: each subsystem's emitter is
  built on the module logger name it already had;
- `record.levelno`, because a level is part of the surface, and because
  two of these events are structured `logger.debug` calls whose level a
  migration could quietly promote;
- `record.getMessage()`, the rendered human sentence, which is what
  proves no `%` argument was lost or reordered;
- the exact set of nonstandard record attributes and their values, read
  through `logs.py`'s own standard-attribute set so this suite and the
  JSON formatter cannot come to disagree about what an event field is.

Two normalizations, and no others. Values that move between runs are
declared per path in `dynamic=` and replaced by a placeholder, so the
key is still pinned and only its value is not; the same values are
declared in `scrub=` where they are rendered into the sentence too
(paths under `tmp_path`, an activation code, a session id). Then every
numeric run in the sentence becomes `<n>`, because durations and
megabytes are rendered into sentences. What stays pinned there is every
word, every argument's position, and the type of what was substituted.

Unlike the session scope, `session` is pinned rather than normalized:
these paths are driven directly enough to name the session themselves,
and a server event carries no session identity of its own.

Written before the hand-built `extra={...}` dicts moved onto
`ServerEvents` (#138, milestone 2) and left untouched through the move,
which is what makes it evidence rather than a description.
"""

import logging
import os
import re
import time
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
from samtal_server.filler import build_agent_fillers
from samtal_server.logs import _STANDARD_ATTRIBUTES
from samtal_server.onboarding import BUDGET_SPENT
from samtal_server.ota import ACTIVATE_SEGMENT, OTA_PATH
from samtal_server.providers import build_agent_providers
from samtal_server.providers.openai_asr import OpenAiAsr
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


def payload_of(record: logging.LogRecord) -> dict[str, Any]:
    """The structured half of a record: exactly the attributes the JSON
    formatter emits as top-level keys, read through `logs.py`'s own
    standard-attribute set rather than through a list written here."""
    return {key: value for key, value in vars(record).items() if key not in _STANDARD_ATTRIBUTES}


def pinned(
    record: logging.LogRecord,
    *,
    dynamic: tuple[str, ...] = (),
    scrub: tuple[str, ...] = (),
) -> dict[str, Any]:
    """What one emit path produces, in the four dimensions a consumer
    sees. `dynamic` names the fields whose value is not pinned; `scrub`
    names the strings that are replaced wherever they appear in the
    sentence, before the numbers in it are normalized."""
    fields = {
        key: DYNAMIC if key in dynamic else value for key, value in payload_of(record).items()
    }
    sentence = record.getMessage()
    for text in scrub:
        sentence = sentence.replace(text, DYNAMIC)
    return {
        "logger": record.name,
        "level": record.levelno,
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

    assert pinned(only(caplog, "ota_check"), dynamic=("code",), scrub=(code,)) == {
        "logger": "samtal_server.ota",
        "level": logging.WARNING,
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
        "sentence": (
            "rejected OTA request: the Device-Id header is required and holds the "
            "device MAC"
        ),
        "fields": {"event": "ota_request_rejected"},
    }


# --- onboarding.py: the banner and the key that missed ----------------

# A pinned key rather than a derived one, so the banner's URL is a
# literal instead of something recomputed from the secret by the code
# under test. Pinning is a supported configuration: it is what carries
# provisioned boards across a secret rotation.
PINNED_KEY = "ABCDEFGH"


def banner_config(**onboarding_options: object) -> Config:
    return Config(
        server={
            "public_url": "https://voice.example",
            "onboarding": {"key": PINNED_KEY, **onboarding_options},
        }
    )


def test_onboarding_banner_with_onboarding_on(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("INFO"):
        onboarding.log_banner(banner_config().server)

    assert pinned(only(caplog, "onboarding_banner")) == {
        "logger": "samtal_server.onboarding",
        "level": logging.INFO,
        "sentence": (
            f"device onboarding URL: https://voice.example/x/{PINNED_KEY}/ "
            "(from server.public_url)"
        ),
        "fields": {
            "event": "onboarding_banner",
            "url": f"https://voice.example/x/{PINNED_KEY}/",
            "origin_source": "server.public_url",
            "onboarding": True,
        },
    }


def test_onboarding_banner_with_onboarding_off(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("INFO"):
        onboarding.log_banner(banner_config(enabled=False).server)

    assert pinned(only(caplog, "onboarding_banner")) == {
        "logger": "samtal_server.onboarding",
        "level": logging.INFO,
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
    """The typo the line exists for: a key a person could have typed, so
    the attempt is repeated back beside the right one."""
    client = TestClient(create_app(banner_config()))

    with caplog.at_level("WARNING"):
        assert client.get(f"/x/{PINNED_KEY[:-1]}X/").status_code == 404

    assert pinned(only(caplog, "onboarding_key_mismatch")) == {
        "logger": "samtal_server.onboarding",
        "level": logging.WARNING,
        "sentence": (
            f"onboarding key {PINNED_KEY[:-1]}X does not match this server's key "
            f"{PINNED_KEY}: check the URL typed into the device's captive portal, "
            "character by character"
        ),
        "fields": {
            "event": "onboarding_key_mismatch",
            "attempted": f"{PINNED_KEY[:-1]}X",
            "expected": PINNED_KEY,
        },
    }


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
        "sentence": (
            "a request reached the onboarding path carrying <n> characters that are "
            "not shaped like a key at all, so they are not repeated here; the URL to "
            "type is in the startup line"
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
    assert pinned(record, dynamic=("path",), scrub=(str(capture.wav_path),)) == {
        "logger": "samtal_server.capture",
        "level": logging.INFO,
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

    assert pinned(only(caplog, "capture_declined"), scrub=(str(keeper.directory),)) == {
        "logger": "samtal_server.capture",
        "level": logging.WARNING,
        "sentence": f"session s<n>: not capturing, {DYNAMIC} is unusable: the volume said no",
        "fields": {"event": "capture_declined", "session": "s1", "reason": "unusable"},
    }


def test_capture_declined_because_the_volume_is_nearly_full(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    keeper = store(tmp_path, min_free_mb=10_000_000.0)

    with caplog.at_level("WARNING"):
        assert keeper.open("s1", time.monotonic(), MANIFEST) is None

    assert pinned(only(caplog, "capture_declined"), dynamic=("free_mb",)) == {
        "logger": "samtal_server.capture",
        "level": logging.WARNING,
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
        "sentence": "session s<n>: not capturing, could not open the files: no room for the files",
        "fields": {"event": "capture_declined", "session": "s1", "reason": "open"},
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
        "sentence": (
            "session s<n>: capture stopped after failing to write audio: "
            "write to closed file"
        ),
        "fields": {"event": "capture_failed", "session": "s1", "reason": "write audio"},
    }


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

    assert pinned(only(caplog, "capture_over_budget"), dynamic=("total_mb",)) == {
        "logger": "samtal_server.capture",
        "level": logging.WARNING,
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
        "sentence": (
            "session capture is configured but off; set server.capture.enabled to "
            f"record to {CAPTURE_DIR}"
        ),
        "fields": {"event": "capture_disabled", "path": CAPTURE_DIR},
    }


# --- ws.py: the two handshakes that never became sessions -------------


def test_auth_rejected(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING"):
        with TestClient(create_app(config_with_agent())) as client:
            with pytest.raises(WebSocketDisconnect):
                with handshake(client, device_headers(None)):
                    pass

    assert pinned(only(caplog, "auth_rejected")) == {
        "logger": "samtal_server.ws",
        "level": logging.WARNING,
        "sentence": f"refused a websocket handshake from {RESOLVED}: no_token",
        "fields": {"event": "auth_rejected", "device": RESOLVED, "reason": "no_token"},
    }


def test_session_rejected_at_capacity(caplog: pytest.LogCaptureFixture) -> None:
    """The other half of `session_rejected`: the edge emits three of
    them on the session channel, and this one is the server's, on the
    websocket router's own channel and with no session behind it."""
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
        "sentence": "draining <n> session(s), up to <n> s",
        "fields": {"event": "drain_started", "sessions": 2, "timeout_s": 5},
    }


async def test_drain_finished(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("INFO"):
        await registry_with(FakeSession()).drain(timeout_s=5)

    assert pinned(only(caplog, "drain_finished")) == {
        "logger": "samtal_server.registry",
        "level": logging.INFO,
        "sentence": "every session drained",
        "fields": {"event": "drain_finished", "sessions": 1},
    }


async def test_drain_incomplete(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("INFO"):
        await registry_with(FakeSession(speaking_for=30)).drain(timeout_s=1.2)

    assert pinned(only(caplog, "drain_incomplete")) == {
        "logger": "samtal_server.registry",
        "level": logging.WARNING,
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
        "sentence": (
            "agent poet: filler synthesis failed, latency masking is off for this "
            "agent: RuntimeError: no voice today"
        ),
        "fields": {"event": "filler_disabled", "agent": "poet", "error": "RuntimeError"},
    }


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
        dynamic=("path",),
        scrub=(str(directory / "samtal.db"),),
    ) == {
        "logger": "samtal_server.device.bindings",
        "level": logging.DEBUG,
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

    assert pinned(only(caplog, "asr_prompt_echo")) == {
        "logger": "samtal_server.providers.openai_asr",
        "level": logging.WARNING,
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

    assert pinned(only(caplog, "asr_prompt_echo"), dynamic=("retry_ms",)) == {
        "logger": "samtal_server.providers.openai_asr",
        "level": logging.WARNING,
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
