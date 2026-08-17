"""What a stranger says about a device, cut down before it is retained.

Four decision sites hand a far-side string to the event surface, and
the #155 provenance inventory found each of them bounding it with
nothing more than a `strip()`. The content-and-telemetry ADR's
2026-08-17 amendment is what makes those fields lawful at all: bounded
device-descriptor metadata is metadata, and "bounded" is the whole of
the permission, so each site now takes an EVENT-ONLY bounded copy.

Event-only is the load-bearing half. The OTA reply has to echo the
firmware version back untouched, because the firmware compares it to
decide whether it is up to date, and `DeviceFacts` keeps the reported
board for a capture manifest. Neither moves; what moves is the payload
field and the sentence's argument, which are the retained surface.

The assertions follow the plan's sentinel model, which has two halves,
one per value class:

- an ADMISSIBLE credential-shaped value, one the bound lets through
  whole, appears in exactly its declared field and its declared
  argument position, on the record, in both shipped formats and on an
  attached tap, and in no other field of any record;
- a REJECTED value, one the bound cuts away, and the pre-sanitization
  form of a value that survives, appear nowhere at all: not in a field,
  not in an argument, not in the rendered sentence, not in either
  format, not on a tap.

Both spellings are shaped so a substring hunt for them cannot match by
accident, which is what makes the second half a real search rather than
a formality.
"""

import json
import logging
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from samtal_server.app import create_app
from samtal_server.auth import build_device_auth
from samtal_server.config import Config
from samtal_server.config.models import BOARD_LIMIT, CLIENT_ID_LIMIT, FIRMWARE_LIMIT
from samtal_server.events import Emission, attach_server_tap, detach_server_tap
from samtal_server.logs import JsonFormatter
from samtal_server.ota import OTA_PATH
from tests.support.checkin import MOCK_AGENT, MOCK_PROVIDERS, NORMALIZED, SYSTEM_INFO
from tests.support.configs import DEVICE_MAC, DEVICE_UUID
from tests.support.events import fields_of, only

# What the bound lets through: credential-shaped, printable, and short
# enough for every limit here. Its presence is asserted, positively, in
# exactly the places the registry declares carry it.
ADMISSIBLE = "sk-adm-6c1e9a4f-never-a-real-credential"

# What the bound cuts away, and what a value looked like before it was
# cut. Its presence anywhere is a failure.
REJECTED = "sk-rej-8b2d7e0c-never-a-real-credential"


class Tap:
    """A server-scope consumer that keeps what it was handed.

    A record is not the whole surface: `Emission.args` reaches every tap
    as the objects themselves, so a claim that a value reaches nobody is
    asserted here as well as at the log."""

    def __init__(self) -> None:
        self.seen: list[Emission] = []

    def emit(self, emission: Emission) -> None:
        self.seen.append(emission)

    def rendered(self) -> str:
        return "\n".join(
            "\n".join([str(one.payload), str(one.message), str(one.args), repr(one.args)])
            for one in self.seen
        )


@pytest.fixture
def tap() -> Iterator[Tap]:
    consumer = Tap()
    attach_server_tap(consumer)
    try:
        yield consumer
    finally:
        detach_server_tap(consumer)


def both_formats(caplog: pytest.LogCaptureFixture) -> str:
    """Every record this server wrote, in the human format and in the
    JSON one, with the arguments behind both."""
    formatter = JsonFormatter()
    return "\n".join(
        f"{record.getMessage()}\n{record.args!r}\n{formatter.format(record)}"
        for record in caplog.records
        if record.name.startswith("samtal_server")
    )


def carrying(caplog: pytest.LogCaptureFixture, value: str) -> set[tuple[str, str]]:
    """Every (event, field) pair whose value holds `value`, across every
    record the run produced. The positive half of the model asserts this
    set exactly rather than asserting one field and hoping."""
    return {
        (str(fields.get("event")), key)
        for fields in (fields_of(record) for record in caplog.records)
        for key, held in fields.items()
        if isinstance(held, str) and value in held
    }


# --- ota.py: the board and the firmware a device reports --------------


def ota_client() -> TestClient:
    """A server every device resolves to an agent on, so a check-in
    takes the ordinary resolved branch rather than the activation one.
    The same shape the contract pin for that branch builds."""
    return TestClient(
        create_app(
            Config(
                providers=MOCK_PROVIDERS,
                agents={"assistant": MOCK_AGENT},
                default_agent="assistant",
            )
        )
    )


def system_info(**replaced: Any) -> dict[str, Any]:
    """The firmware's own check-in body, with the named keys replaced."""
    return {**SYSTEM_INFO, **replaced}


def check_in(client: TestClient, payload: dict[str, Any]):
    return client.post(
        OTA_PATH,
        json=payload,
        headers={"Device-Id": DEVICE_MAC, "Client-Id": DEVICE_UUID},
    )


# A board and a version as an unauthenticated stranger could send them:
# far longer than any bound, carrying a terminal escape and a newline
# that would otherwise split one retained record into two, and with the
# rejected sentinel placed past the cut.
HOSTILE_BOARD = f"{ADMISSIBLE}\n\x1b[2J" + "x" * 400 + REJECTED
HOSTILE_VERSION = "9.9.9\r\n" + "y" * 200 + REJECTED


def test_ota_check_bounds_the_board_and_the_firmware_it_was_told(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The event carries a bounded copy: printable characters only, cut
    to the declared maximum, in the payload and in the sentence's
    arguments alike."""
    with caplog.at_level(logging.INFO):
        response = check_in(
            ota_client(),
            system_info(
                board={"type": HOSTILE_BOARD},
                application={"name": "xiaozhi", "version": HOSTILE_VERSION},
            ),
        )

    assert response.status_code == 200
    record = only(caplog, "ota_check")
    fields = fields_of(record)
    assert len(fields["board"]) <= BOARD_LIMIT
    assert len(fields["firmware"]) <= FIRMWARE_LIMIT
    # No control character survives, so one record stays one record and
    # nothing repaints a terminal.
    assert fields["board"].isprintable()
    assert fields["firmware"].isprintable()
    # The bounded copy is what the sentence renders too, not only what
    # the payload carries: dropping a payload field cannot un-render the
    # same value from the arguments.
    assert record.args[1] == fields["board"]
    assert record.args[2] == fields["firmware"]


def test_an_admissible_descriptor_lands_in_its_declared_places_only(
    caplog: pytest.LogCaptureFixture, tap: Tap
) -> None:
    """The positive half. A credential-shaped board the bound lets
    through is on the board field and on the board argument, on the
    record, in both formats and on the tap, and on nothing else."""
    with caplog.at_level(logging.INFO):
        check_in(ota_client(), system_info(board={"type": ADMISSIBLE}))

    record = only(caplog, "ota_check")
    assert fields_of(record)["board"] == ADMISSIBLE
    assert record.args[1] == ADMISSIBLE
    assert ADMISSIBLE in record.getMessage()
    assert ADMISSIBLE in both_formats(caplog)
    assert ADMISSIBLE in tap.rendered()
    # Exactly one field of exactly one event, which is the containment
    # the declaration claims.
    assert carrying(caplog, ADMISSIBLE) == {("ota_check", "board")}


def test_a_rejected_descriptor_reaches_no_retained_surface(
    caplog: pytest.LogCaptureFixture, tap: Tap
) -> None:
    """The negative half. What the bound cut away is on no surface at
    all."""
    with caplog.at_level(logging.INFO):
        check_in(
            ota_client(),
            system_info(
                board={"type": HOSTILE_BOARD},
                application={"name": "xiaozhi", "version": HOSTILE_VERSION},
            ),
        )

    record = only(caplog, "ota_check")
    assert carrying(caplog, REJECTED) == set()
    assert REJECTED not in repr(record.args)
    assert REJECTED not in record.getMessage()
    assert REJECTED not in both_formats(caplog)
    assert tap.seen
    assert REJECTED not in tap.rendered()


def test_the_ota_reply_and_the_recorded_facts_are_untouched(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The narrowing is event-only. The firmware decides whether it is
    up to date by comparing the version it sent with the one it is
    answered, and the capture manifest is built from the recorded facts,
    so both keep the bytes the device sent."""
    client = ota_client()

    with caplog.at_level(logging.INFO):
        response = check_in(
            client,
            system_info(
                board={"type": HOSTILE_BOARD},
                application={"name": "xiaozhi", "version": HOSTILE_VERSION},
            ),
        )

    assert response.json()["firmware"]["version"] == HOSTILE_VERSION
    assert client.app.state.device_facts.get(NORMALIZED) == {
        "firmware": HOSTILE_VERSION,
        "board": HOSTILE_BOARD,
    }


def test_a_board_of_nothing_but_unprintables_reads_as_unknown(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A descriptor the bound empties is not an empty field: it says the
    same thing an absent one says, which is that the device told us
    nothing usable."""
    with caplog.at_level(logging.INFO):
        check_in(
            ota_client(),
            system_info(
                board={"type": "\x00\x07\x1b"},
                application={"name": "xiaozhi", "version": "\x00\x07\x1b"},
            ),
        )

    fields = fields_of(only(caplog, "ota_check"))
    assert (fields["board"], fields["firmware"]) == ("unknown", "0.0.0")


def test_a_board_a_real_device_reports_is_carried_unchanged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The bound is invisible to lawful traffic, which is why no pin
    moves: these are the values the contract pin suite asserts."""
    with caplog.at_level(logging.INFO):
        check_in(ota_client(), SYSTEM_INFO)

    fields = fields_of(only(caplog, "ota_check"))
    assert fields["board"] == SYSTEM_INFO["board"]["type"]
    assert fields["firmware"] == SYSTEM_INFO["application"]["version"]


def test_the_json_record_carries_the_bounded_copy_and_nothing_else(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The JSON record is what a collector keeps, so it is read back as
    a parsed object rather than as a string that happens not to
    match."""
    with caplog.at_level(logging.INFO):
        check_in(ota_client(), system_info(board={"type": HOSTILE_BOARD}))

    written = json.loads(JsonFormatter().format(only(caplog, "ota_check")))
    assert written["board"].isprintable()
    assert len(written["board"]) <= BOARD_LIMIT
    assert REJECTED not in json.dumps(written)


# --- ota.py: the Client-Id header a check-in carries ------------------


HOSTILE_CLIENT = f"{ADMISSIBLE}\r\n\x1b[2J" + "z" * 400 + REJECTED


def test_ota_check_bounds_the_client_id_it_was_sent(
    caplog: pytest.LogCaptureFixture, tap: Tap
) -> None:
    """The Client-Id header is required but nothing bounds it, and this
    endpoint is unauthenticated, so the event carries a bounded copy."""
    with caplog.at_level(logging.INFO):
        response = ota_client().post(
            OTA_PATH,
            json=SYSTEM_INFO,
            headers={"Device-Id": DEVICE_MAC, "Client-Id": HOSTILE_CLIENT},
        )

    assert response.status_code == 200
    held = fields_of(only(caplog, "ota_check"))["client"]
    assert len(held) <= CLIENT_ID_LIMIT
    assert held.isprintable()
    assert carrying(caplog, REJECTED) == set()
    assert REJECTED not in both_formats(caplog)
    assert REJECTED not in tap.rendered()


def test_a_client_id_of_nothing_but_unprintables_is_null(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A field that says nothing is more honest than one that says the
    empty string."""
    with caplog.at_level(logging.INFO):
        ota_client().post(
            OTA_PATH,
            json=SYSTEM_INFO,
            headers={"Device-Id": DEVICE_MAC, "Client-Id": "\x07\x1b\x00"},
        )

    assert fields_of(only(caplog, "ota_check"))["client"] is None


def test_the_token_is_still_signed_for_the_header_as_it_arrived(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Event-only again: the device UUID a token is signed for is the
    header, so a bounded copy in the reply would hand the device a token
    its own next handshake could not present."""
    config = Config(
        providers=MOCK_PROVIDERS,
        agents={"assistant": MOCK_AGENT},
        devices={NORMALIZED: "assistant"},
    )
    client = TestClient(create_app(config))

    with caplog.at_level(logging.INFO):
        answered = client.post(
            OTA_PATH,
            json=SYSTEM_INFO,
            headers={"Device-Id": DEVICE_MAC, "Client-Id": HOSTILE_CLIENT},
        ).json()

    auth = build_device_auth(config)
    assert auth is not None
    assert auth.verify(answered["websocket"]["token"], HOSTILE_CLIENT, NORMALIZED)
    # And the event still says nothing a stranger chose.
    assert carrying(caplog, REJECTED) == set()
