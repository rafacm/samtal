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

A fifth site joined them with #427, and it is the same model at a
different bound: `ota_check_body` keeps the whole of what a board
reported, so its section below plants its rejected sentinel past
`CHECK_IN_BODY_LIMIT` rather than past a descriptor's, and asserts the
truncation mechanism as well as the containment.
"""

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from tests.support.apps import entered_client
from tests.support.checkin import MOCK_AGENT, MOCK_PROVIDERS, NORMALIZED, SYSTEM_INFO
from tests.support.configs import DEVICE_MAC, DEVICE_UUID, config_with_agent
from tests.support.events import events, fields_of, only
from tests.support.wire import handshake, shake_hands
from vinga_server.app import create_app
from vinga_server.auth import build_device_auth
from vinga_server.config import Config
from vinga_server.config.models import BOARD_LIMIT, CLIENT_ID_LIMIT, FIRMWARE_LIMIT
from vinga_server.events import Emission, attach_server_tap, detach_server_tap
from vinga_server.events.catalog import OtaCheckBodyReported
from vinga_server.events.live import Filters, LiveEvents, Streamed, Subscription
from vinga_server.events.values import CHECK_IN_BODY_LIMIT, CHECK_IN_BODY_TRUNCATED
from vinga_server.logs import JsonFormatter
from vinga_server.ota import OTA_PATH

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

    def rendered(self, without: str | None = None) -> str:
        """Everything this tap was handed, or everything but one event's
        emissions.

        `without` has exactly one caller and names exactly one event.
        `ota_check_body` deliberately retains the whole of what a board
        sent, bounded at `CHECK_IN_BODY_LIMIT` rather than at any
        descriptor's limit, so a sentinel planted past the BOARD bound
        is comfortably inside the BODY bound and its presence there says
        nothing about whether the board bound held. Every other claim in
        this file is over everything the tap saw.
        """
        return "\n".join(
            "\n".join([str(one.payload), str(one.message), str(one.args), repr(one.args)])
            for one in self.seen
            if without is None or one.payload.get("event") != without
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
        if record.name.startswith("vinga_server")
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


@contextmanager
def ota_client() -> Iterator[TestClient]:
    """A server every device resolves to an agent on, so a check-in
    takes the ordinary resolved branch rather than the activation one.
    The same shape the contract pin for that branch builds."""
    with entered_client(
        Config(
            providers=MOCK_PROVIDERS,
            agents={"assistant": MOCK_AGENT},
            default_agent="assistant",
        )
    ) as client:
        yield client


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
    with ota_client() as client, caplog.at_level(logging.INFO):
        response = check_in(
            client,
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
    with ota_client() as client, caplog.at_level(logging.INFO):
        check_in(client, system_info(board={"type": ADMISSIBLE}))

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
    all.

    "The bound" is the board's and the firmware's, which is what this
    file is about. The check-in body event has a bound of its own and
    keeps what a board sent whole inside it, deliberately and at DEBUG:
    the retained log at the default level never sees it, which is what
    `both_formats` reads back here, and the tap sees every emission
    before its own filtering, which is why the tap's reading skips that
    one event and nothing else.
    """
    with ota_client() as client, caplog.at_level(logging.INFO):
        check_in(
            client,
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
    assert REJECTED not in tap.rendered(without="ota_check_body")


def test_the_ota_reply_and_the_recorded_facts_are_untouched(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The narrowing is event-only. The firmware decides whether it is
    up to date by comparing the version it sent with the one it is
    answered, and the capture manifest is built from the recorded facts,
    so both keep the bytes the device sent."""
    with ota_client() as client:
        with caplog.at_level(logging.INFO):
            response = check_in(
                client,
                system_info(
                    board={"type": HOSTILE_BOARD},
                    application={"name": "xiaozhi", "version": HOSTILE_VERSION},
                ),
            )

        assert response.json()["firmware"]["version"] == HOSTILE_VERSION
        assert client.app.state.composition.device_facts.get(NORMALIZED) == {
            "firmware": HOSTILE_VERSION,
            "board": HOSTILE_BOARD,
        }


def test_a_board_of_nothing_but_unprintables_reads_as_unknown(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A descriptor the bound empties is not an empty field: it says the
    same thing an absent one says, which is that the device told us
    nothing usable."""
    with ota_client() as client, caplog.at_level(logging.INFO):
        check_in(
            client,
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
    with ota_client() as client, caplog.at_level(logging.INFO):
        check_in(client, SYSTEM_INFO)

    fields = fields_of(only(caplog, "ota_check"))
    assert fields["board"] == SYSTEM_INFO["board"]["type"]
    assert fields["firmware"] == SYSTEM_INFO["application"]["version"]


def test_the_json_record_carries_the_bounded_copy_and_nothing_else(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The JSON record is what a collector keeps, so it is read back as
    a parsed object rather than as a string that happens not to
    match."""
    with ota_client() as client, caplog.at_level(logging.INFO):
        check_in(client, system_info(board={"type": HOSTILE_BOARD}))

    written = json.loads(JsonFormatter().format(only(caplog, "ota_check")))
    assert written["board"].isprintable()
    assert len(written["board"]) <= BOARD_LIMIT
    assert REJECTED not in json.dumps(written)


# --- ota.py: the whole of what a board reported -----------------------
#
# `ota_check_body` is the one event that retains a far-side value whole
# rather than in a field-sized slice, which is why its sentinel is its
# own and not a reuse of the two above. What it keeps is a compact
# serialization of the PARSED body, `ensure_ascii` escaped and cut at
# `CHECK_IN_BODY_LIMIT`, so the sentinel below is planted past that cut
# rather than past a descriptor's.
#
# The body is sent as raw bytes rather than through the client's `json=`
# helper, and that is load-bearing: httpx serializes with
# `ensure_ascii=False`, so a lone surrogate would fail in the test's own
# encoder and never reach the server at all. What arrives here is what a
# stranger's curl would put on the wire.


async def drained(subscription: Subscription) -> list[Streamed]:
    """Everything one live reader was handed, without waiting.

    A check-in emits its outcome and then its body, so a reader at DEBUG
    is handed both and the test picks the one it is about rather than
    assuming which arrived first.
    """
    held: list[Streamed] = []
    while True:
        item = await subscription.next(timeout=0)
        if item is None:
            return held
        assert isinstance(item, Streamed), "the reader fell behind in a test"
        held.append(item)


def raw_check_in(client: TestClient, text: str):
    """One check-in whose body is exactly these bytes."""
    return client.post(
        OTA_PATH,
        content=text.encode(),
        headers={
            "Device-Id": DEVICE_MAC,
            "Client-Id": DEVICE_UUID,
            "Content-Type": "application/json",
        },
    )


# The sentinel body: a terminal escape and a newline inside a string, a
# lone surrogate no UTF-8 encoder will take, the admissible sentinel
# where it survives, and a field long enough that the rejected sentinel
# at its end is past the cut.
HOSTILE_REPORTED = {
    **SYSTEM_INFO,
    "board": {"type": ADMISSIBLE, "note": "\x1b[2J\nrepaint and split"},
    "surrogate": "\ud800",
    "partitions": "p" * (CHECK_IN_BODY_LIMIT * 2) + REJECTED,
}

# What goes on the wire, spaces and all, and what a compact
# reserialization of it is. The two differ deliberately: the value is a
# serialization of the PARSED object rather than the bytes as sent, so
# the whitespace a stranger chose is not what the bound is spent on.
HOSTILE_JSON = json.dumps(HOSTILE_REPORTED, ensure_ascii=True)
HOSTILE_COMPACT = json.dumps(HOSTILE_REPORTED, ensure_ascii=True, separators=(",", ":"))


async def test_the_reported_body_is_bounded_escaped_and_cut_where_it_says(
    caplog: pytest.LogCaptureFixture, tap: Tap
) -> None:
    """The sentinel, on every surface the body travels and on none of
    the two it must not.

    Positive on the payload, on the JSON formatter's output, on an
    attached tap and on the live stream, because those are where a
    structured field goes. Negative on the text formatter and on the
    message arguments, because the template interpolates only the
    bounded `said` values its siblings interpolate: the body is a field
    and never a sentence.

    And the mechanism rather than the intent: the exact marker at the
    exact position, a final length inside the bound, every character
    printable, and the sentinel planted past the cut nowhere at all.
    """
    hub = LiveEvents()
    watching = hub.subscribe(Filters(level=logging.DEBUG))
    attach_server_tap(hub)
    try:
        with ota_client() as client, caplog.at_level(logging.DEBUG):
            response = raw_check_in(client, HOSTILE_JSON)
        streamed = await drained(watching)
    finally:
        detach_server_tap(hub)
        hub.unsubscribe(watching)

    assert response.status_code == 200
    record = only(caplog, "ota_check_body")
    body = fields_of(record)["body"]

    # The bound, marker included, and the marker exactly where the
    # arithmetic puts it.
    assert isinstance(body, str)
    assert len(body) == CHECK_IN_BODY_LIMIT
    assert body.endswith(CHECK_IN_BODY_TRUNCATED)
    assert body[: -len(CHECK_IN_BODY_TRUNCATED)] == HOSTILE_COMPACT[
        : CHECK_IN_BODY_LIMIT - len(CHECK_IN_BODY_TRUNCATED)
    ]
    # Printable throughout, which is `ensure_ascii`'s doing rather than a
    # replacement pass: the escape sequence and the lone surrogate leave
    # as `\uXXXX` text.
    assert body.isprintable()
    assert '"note":"\\u001b[2J\\nrepaint and split"' in body
    assert '"surrogate":"\\ud800"' in body
    # Nothing from past the cut, on any surface.
    assert REJECTED not in body
    assert carrying(caplog, REJECTED) == set()
    assert REJECTED not in both_formats(caplog)
    assert REJECTED not in tap.rendered()

    # Where the body goes: the JSON record a collector keeps, the tap,
    # and the live stream a `vinga events tail --level DEBUG` reads.
    written = json.loads(JsonFormatter().format(record))
    assert written["body"] == body
    # Read off the tap's payload rather than hunted for in its
    # rendering: an escaped body is full of backslashes, and a `repr`
    # doubles every one of them, so a substring search would answer no
    # to a value that is plainly there.
    assert [
        one.payload["body"]
        for one in tap.seen
        if one.payload["event"] == "ota_check_body"
    ] == [body]
    assert [one.fields["body"] for one in streamed if one.fields["event"] == "ota_check_body"] == [
        body
    ]

    # And where it does not: the sentence and the arguments behind it.
    assert body not in record.getMessage()
    assert body not in repr(record.args)
    assert record.args == (DEVICE_MAC, ADMISSIBLE, SYSTEM_INFO["application"]["version"])


def test_a_body_inside_the_bound_is_carried_whole(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The carried case: a board reporting a partition table and a
    display block gets both onto the event, byte for byte after the
    compacting, and is answered exactly as it is today."""
    reported = {
        **SYSTEM_INFO,
        "partition_table": [
            {"label": "nvs", "type": 1, "subtype": 2, "address": 36864, "size": 16384},
            {"label": "factory", "type": 0, "subtype": 0, "address": 65536, "size": 4194304},
        ],
        "display": {"width": 240, "height": 240, "type": "st7789"},
    }
    text = json.dumps(reported, ensure_ascii=True)

    with ota_client() as client, caplog.at_level(logging.DEBUG):
        response = raw_check_in(client, text)

    body = fields_of(only(caplog, "ota_check_body"))["body"]
    assert body == json.dumps(reported, ensure_ascii=True, separators=(",", ":"))
    assert len(body) < CHECK_IN_BODY_LIMIT
    assert CHECK_IN_BODY_TRUNCATED not in body
    # The reply is the reply it has always been: this feature observes.
    assert response.json()["firmware"]["version"] == SYSTEM_INFO["application"]["version"]


def test_a_body_that_is_no_object_at_all_says_so_with_a_null(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The null case. A request carrying no readable JSON object is a
    real state of an unfamiliar board, so the event states it rather
    than skipping the emission, and the device is still answered."""
    with ota_client() as client, caplog.at_level(logging.DEBUG):
        response = raw_check_in(client, "not json at all")

    assert response.status_code == 200
    fields = fields_of(only(caplog, "ota_check_body"))
    assert fields["body"] is None
    # The four `said` values are still there: what the board is, this
    # server still says, whether or not it could read what it sent.
    assert fields["board"] == "unknown"
    assert fields["firmware"] == "0.0.0"


def test_the_body_event_is_emitted_whatever_the_check_resolved_to(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Unconditional, because the boards this exists for are exactly the
    ones whose outcome is unpredictable: a device this server resolves
    to nothing at all still describes itself."""
    with entered_client(Config()) as client, caplog.at_level(logging.DEBUG):
        raw_check_in(client, json.dumps(SYSTEM_INFO, ensure_ascii=True))

    assert fields_of(only(caplog, "ota_check"))["agents"] == []
    assert fields_of(only(caplog, "ota_check_body"))["body"] is not None


def test_the_default_level_shows_neither_a_tail_nor_the_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The whole of "off unless asked for", on both filters at once: a
    retained log at the default level writes no record, and a live
    subscription at the default level is handed none.

    The threshold itself is `test_events_live.py`'s
    `test_the_level_is_a_threshold_and_info_is_the_default`; what is
    asserted here is that this event is on the quiet side of it.
    """
    with ota_client() as client, caplog.at_level(logging.INFO):
        raw_check_in(client, json.dumps(SYSTEM_INFO, ensure_ascii=True))

    assert events(caplog, "ota_check_body") == []
    assert events(caplog, "ota_check")
    assert not Filters().admits(
        Emission(
            payload={"event": "ota_check_body"},
            at=0.0,
            level=OtaCheckBodyReported.LEVEL,
            message="",
            args=(),
        )
    )


# --- ota.py: the Client-Id header a check-in carries ------------------


HOSTILE_CLIENT = f"{ADMISSIBLE}\r\n\x1b[2J" + "z" * 400 + REJECTED


def test_ota_check_bounds_the_client_id_it_was_sent(
    caplog: pytest.LogCaptureFixture, tap: Tap
) -> None:
    """The Client-Id header is required but nothing bounds it, and this
    endpoint is unauthenticated, so the event carries a bounded copy."""
    with ota_client() as client, caplog.at_level(logging.INFO):
        response = client.post(
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
    with ota_client() as client, caplog.at_level(logging.INFO):
        client.post(
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
        devices={NORMALIZED: ["assistant"]},
    )
    with entered_client(config) as client, caplog.at_level(logging.INFO):
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


# --- device/session.py: the Client-Id a handshake presents ------------
#
# Header values cannot carry a carriage return or a newline, which the
# HTTP client refuses to send, so the hostile client id here is long
# rather than unprintable. The printability half of the bound is proven
# above, where the value travels in a JSON body instead.

WIRE_CLIENT = f"{ADMISSIBLE}-" + "z" * 400 + REJECTED


def open_a_session(client: TestClient, client_id: str) -> None:
    """One handshake presenting the given Client-Id, with a token signed
    for that same header, which is the identity pair the OTA reply would
    have issued for."""
    device_auth = client.app.state.composition.device_auth
    token = "" if device_auth is None else device_auth.issue(client_id, NORMALIZED)
    headers = {
        "Authorization": f"Bearer {token}",
        "Protocol-Version": "1",
        "Client-Id": client_id,
        "Device-Id": DEVICE_MAC,
    }
    with handshake(client, headers) as websocket:
        shake_hands(websocket)


def test_session_open_bounds_the_client_id_the_handshake_presented(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    """The event carries a bounded copy in its field and renders that
    same copy in its sentence."""
    config = config_with_agent(
        server={"capture": {"enabled": True, "dir": str(tmp_path / "captures")}}
    )
    with caplog.at_level(logging.INFO), TestClient(create_app(config)) as client:
        open_a_session(client, WIRE_CLIENT)

    record = only(caplog, "session_open")
    held = fields_of(record)["client"]
    assert len(held) <= CLIENT_ID_LIMIT
    assert record.args[2] == held
    assert carrying(caplog, REJECTED) == set()
    assert REJECTED not in both_formats(caplog)


def test_the_capture_manifest_keeps_the_client_id_as_it_arrived(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    """Event-only. The capture and the conversation store are the
    surfaces that hold what the device said, and the manifest is built
    from the header rather than from the event's copy."""
    config = config_with_agent(
        server={"capture": {"enabled": True, "dir": str(tmp_path / "captures")}}
    )
    with caplog.at_level(logging.INFO), TestClient(create_app(config)) as client:
        open_a_session(client, WIRE_CLIENT)

    wavs = list((tmp_path / "captures").glob("*.wav"))
    assert len(wavs) == 1
    manifest = json.loads(wavs[0].with_suffix(".json").read_text())
    assert manifest["device"]["client"] == WIRE_CLIENT


def test_a_lawful_client_id_opens_a_session_unchanged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The value the contract pin plants, carried exactly as it pins
    it."""
    with caplog.at_level(logging.INFO), TestClient(
        create_app(config_with_agent())
    ) as client:
        open_a_session(client, DEVICE_UUID)

    record = only(caplog, "session_open")
    assert fields_of(record)["client"] == DEVICE_UUID
    assert record.args[2] == DEVICE_UUID


# --- ws.py: the Device-Id a full server is reached with ---------------


def full_server(**auth: object) -> Config:
    config = config_with_agent(server={"auth": auth} if auth else None)
    config.server.limits.max_sessions = 1
    return config


def refused_at_capacity(client: TestClient, device_id: str) -> None:
    """One handshake past the capacity ceiling, presenting the given
    Device-Id."""
    device_auth = client.app.state.composition.device_auth
    headers = {"Protocol-Version": "1", "Client-Id": DEVICE_UUID, "Device-Id": device_id}
    if device_auth is not None:
        headers["Authorization"] = f"Bearer {device_auth.issue(DEVICE_UUID, device_id)}"
    with pytest.raises(WebSocketDisconnect):
        with handshake(client, headers):
            pass


def test_the_capacity_refusal_names_no_device_it_does_not_recognize(
    caplog: pytest.LogCaptureFixture, tap: Tap
) -> None:
    """With device auth off, `refusal_reason` returns before reading
    anything, so this header is an unauthenticated string a stranger
    chose and a full server would otherwise write one per attempt into
    the retained log."""
    with TestClient(create_app(full_server(enabled=False))) as client:
        with handshake(client, {"Protocol-Version": "1", "Device-Id": DEVICE_MAC}) as held:
            shake_hands(held)
            with caplog.at_level(logging.WARNING):
                refused_at_capacity(client, REJECTED)

    record = only(caplog, "session_rejected")
    fields = fields_of(record)
    assert fields["device"] is None
    assert record.args == ("an unidentified device",)
    assert carrying(caplog, REJECTED) == set()
    assert REJECTED not in record.getMessage()
    assert REJECTED not in both_formats(caplog)
    assert tap.seen
    assert REJECTED not in tap.rendered()


def test_the_capacity_refusal_still_names_a_device_it_recognizes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The lawful value the contract pin plants, in the form it pins it:
    a header that normalizes to a MAC reads exactly as it did before."""
    with TestClient(create_app(full_server())) as client:
        device_auth = client.app.state.composition.device_auth
        with handshake(
            client,
            {
                "Protocol-Version": "1",
                "Client-Id": DEVICE_UUID,
                "Device-Id": DEVICE_MAC,
                "Authorization": (f"Bearer {device_auth.issue(DEVICE_UUID, NORMALIZED)}"),
            },
        ) as held:
            shake_hands(held)
            with caplog.at_level(logging.WARNING):
                refused_at_capacity(client, NORMALIZED)

    record = only(caplog, "session_rejected")
    assert fields_of(record)["device"] == NORMALIZED
    assert record.args == (NORMALIZED,)
