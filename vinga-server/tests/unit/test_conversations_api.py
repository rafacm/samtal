"""The conversation store over HTTP: three reads on the gated /api.

Two seams, deliberately. One test holds a real conversation against a
booted server and then asks that same server's API what it recorded,
which is the only place the whole path is provable: the pipeline
assembles a turn, the writer commits it, and the routes serve it. The
rest write the store directly, because a page boundary, a cursor past
the end and a text-off row are properties of the reads, and driving
each of them through a websocket would be a slower test of the same
thing.

What the file is about, beyond the round trip:

- **The cursors are the row ids and the pages are exact.** A page walked
  to the end recovers the whole listing in order, once, and
  `next_cursor` is null exactly when there is nothing beyond it.
- **Nothing a caller sends is quoted back.** A limit, a cursor, a device
  filter and a session id are the only values these routes are handed,
  and a sentinel planted in each is hunted through the response body,
  both shipped log formats and the process output.
- **A missing file is a 404 naming the switch, and never a new file.**
  A read of a deployment that never recorded creates nothing, which is
  what keeps an absent section a server that leaves no database behind.
"""

import json
import logging
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.support.configs import DEVICE_MAC, DEVICE_UUID, recording_config
from tests.support.problems import paths, refused
from tests.support.sessions import until
from tests.support.wire import connect, say_something, sentences, shake_hands
from vinga_server import logs
from vinga_server.app import create_app
from vinga_server.config import Config
from vinga_server.config.api import MOUNT_PATH, build_api
from vinga_server.config.models import DatabaseConfig
from vinga_server.conversations import api as conversations_api
from vinga_server.conversations import schema
from vinga_server.conversations.api import LIMIT_DEFAULT, LIMIT_MAX
from vinga_server.conversations.records import ToolInvocation, TurnLeg, TurnRecord
from vinga_server.conversations.store import ConversationStore

TOKEN = "test-api-token-" + "0123456789abcdef" * 2

API_SECRET_ENV = "VINGA_API_SECRET"

# Shaped like something an operator would be horrified to find quoted
# back, and so that a substring check for it cannot match by accident.
SENTINEL = "sk-test-9c1f7b02-never-a-real-credential"

OTHER_DEVICE = "11:22:33:44:55:66"

# The three routes, for the properties that hold on all of them: the
# gate in front of them, and the 404 a deployment with no store answers.
ROUTES = ["/sessions", "/sessions/alpha", "/sessions/alpha/turns"]


def manifest(device: str = DEVICE_MAC.lower(), **overrides: Any) -> dict[str, Any]:
    return {
        "started_at": "2026-08-15T10:00:00+00:00",
        "server": {"version": "0.1.0", "revision": "abc1234"},
        "device": {"mac": device, "client": DEVICE_UUID},
        "protocol": "1",
        "agent": "sam",
        "agents": ["sam"],
        "providers": {"llm": {"name": "claude", "type": "anthropic"}},
    } | overrides


CONVERSATION = "9f0c1d2e3a4b5c6d7e8f90a1b2c3d4e5"


def a_turn(**overrides: Any) -> TurnRecord:
    """One turn with both of its calls out of the order the model issued
    them, so nesting by position is a property and not a coincidence of
    insertion order."""
    fields: dict[str, Any] = {
        "at": 101.2,
        "conversation": CONVERSATION,
        "agent": "sam",
        "heard": "turn the light on",
        "heard_duration_s": 1.4,
        "language": "en",
        "language_confidence": 0.98,
        "reply": "Done.",
        "asr_ms": 210,
        "first_token_ms": 340,
        "llm_ms": 900,
        "tts_first_audio_ms": 260,
        "rounds": 2,
        "input_tokens": 512,
        "output_tokens": 24,
        "tools": (
            ToolInvocation(
                position=1,
                source="mcp",
                entry="home",
                name="turn_on_light",
                arguments={"room": "kitchen"},
                result="ok",
                duration_ms=42,
            ),
            ToolInvocation(
                position=0,
                source="builtin",
                entry=None,
                name="remember",
                arguments={"text": "the kitchen light"},
                result="noted",
                duration_ms=3,
            ),
        ),
    }
    fields.update(overrides)
    return TurnRecord(**fields)


def recorded(
    database: DatabaseConfig | None = None,
    sessions: int = 1,
    turns: int = 1,
    device: str = DEVICE_MAC.lower(),
    turn: TurnRecord | None = None,
    **options: Any,
) -> list[str]:
    """Write a store the way the server writes one, and let go of it.

    Through the real `ConversationStore` rather than through inserts, so
    what the routes read is what the writer produces, storage switches
    included. `stop()` drains, so everything is committed by the time it
    returns.
    """
    store = ConversationStore(
        DatabaseConfig() if database is None else database, **options
    )
    store.start()
    named = []
    try:
        for index in range(sessions):
            session = f"session-{index:02d}"
            named.append(session)
            store.open_session(session, 100.0, manifest(device))
            store.record_event(session, "session_open", logging.INFO, {"protocol": 1}, 100.0)
            for _ in range(turns):
                store.record_turn(session, turn if turn is not None else a_turn())
            store.close_session(session, 12.5, "client")
    finally:
        store.stop()
    return named


@pytest.fixture
def api() -> FastAPI:
    return build_api(TOKEN, DatabaseConfig())


@pytest.fixture
def client(api: FastAPI) -> TestClient:
    return TestClient(api, headers={"Authorization": f"Bearer {TOKEN}"})


def _get(client: TestClient, path: str, **params: Any) -> Any:
    response = client.get(path, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _leaked(caplog: pytest.LogCaptureFixture) -> str:
    """Everything this server logged, in both shipped formats: a
    sentence is rendered one way by the plain formatter and another by
    the JSON one, and a value can hide in a field the plain rendering
    never prints.

    This server's own channels, and deliberately not every record in the
    process: the HTTP client making these requests logs the URL it
    asked for, which is the caller's own terminal rather than anything
    this server wrote, and it is the test harness here only because a
    TestClient is what stands in for curl.
    """
    records = [record for record in caplog.records if record.name.startswith("vinga_server")]
    return "".join(
        record.getMessage() + str(record.__dict__) + logs.JsonFormatter().format(record)
        for record in records
    )


# The whole path, once


def test_a_real_conversation_reads_back_over_the_same_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The acceptance seam: a server that recorded a conversation
    answers for it on its own API, so the pipeline's record, the
    writer's rows and these routes are checked against one another
    rather than against a hand-written fixture."""
    monkeypatch.setenv(API_SECRET_ENV, TOKEN)
    bearer = {"Authorization": f"Bearer {TOKEN}"}

    with TestClient(create_app(recording_config(tmp_path))) as client:
        with connect(client) as websocket:
            session_id = shake_hands(websocket)["session_id"]
            spoken, _ = say_something(websocket)
        # Polled rather than assumed: the close is queued as the
        # websocket goes away, and the writer commits it on its own
        # thread.
        detail = until(
            lambda: _closed(client, bearer, session_id),
            "the session never closed in the store",
        )
        listing = client.get(f"{MOUNT_PATH}/sessions", headers=bearer).json()
        timeline = client.get(
            f"{MOUNT_PATH}/sessions/{session_id}/turns", headers=bearer
        ).json()

    (summary,) = listing["items"]
    assert listing["next_cursor"] is None
    assert summary["session"] == session_id == detail["session"]
    assert summary["device"] == DEVICE_MAC.lower()
    assert summary["agent"] == "assistant"
    assert summary["close_reason"] == "client"
    assert summary["turns"] == detail["turns"] == 1

    assert detail["client"] == DEVICE_UUID
    assert detail["agents"] == ["assistant"]
    assert detail["providers"]["llm"]["type"] == "mock"
    assert (detail["metrics"], detail["text"]) == (True, True)
    assert detail["dropped"] == 0
    # The decision track is counted here and served nowhere: the
    # database is that surface.
    assert detail["events"] > 0

    (turn,) = timeline["items"]
    assert turn["heard"] == "remember that I like tea"
    # What the device was told, sentence for sentence.
    assert turn["reply"] == " ".join(sentences(spoken))
    assert turn["rounds"] == 2
    assert turn["tool_calls"] == 1
    assert [call["name"] for call in turn["tool_invocations"]] == ["remember"]
    assert turn["tool_invocations"][0]["source"] == "builtin"
    assert turn["tts_first_audio_ms"] is not None


def _closed(client: TestClient, bearer: dict[str, str], session: str) -> Any:
    response = client.get(f"{MOUNT_PATH}/sessions/{session}", headers=bearer)
    if response.status_code != 200:
        return None
    detail = response.json()
    return detail if detail["closed_at"] is not None else None


def test_a_store_that_records_nothing_today_still_serves_what_it_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Switching recording off stops the writer, not the reader, exactly
    as capture files outlive the capture switch."""
    monkeypatch.setenv(API_SECRET_ENV, TOKEN)
    recorded(None, sessions=2)
    app = create_app(Config())

    with TestClient(app) as client:
        listing = client.get(
            f"{MOUNT_PATH}/sessions", headers={"Authorization": f"Bearer {TOKEN}"}
        ).json()
        # Read while it serves, because that is when there is a
        # composition to read: it belongs to the lifespan that built it.
        assert app.state.composition.conversations is None, "this server records nothing"

    assert [item["session"] for item in listing["items"]] == ["session-01", "session-00"]


# The listing


def test_the_listing_answers_newest_first_with_a_summary(
    client: TestClient,
) -> None:
    recorded(None, sessions=3, turns=2)

    listing = _get(client, "/sessions")

    assert [item["session"] for item in listing["items"]] == [
        "session-02",
        "session-01",
        "session-00",
    ]
    assert [item["id"] for item in listing["items"]] == [3, 2, 1]
    assert listing["next_cursor"] is None
    for item in listing["items"]:
        assert item["device"] == DEVICE_MAC.lower()
        assert item["agent"] == "sam"
        assert item["started_at"] == "2026-08-15T10:00:00+00:00"
        assert item["closed_at"] is not None
        assert item["duration_s"] == 12.5
        assert item["close_reason"] == "client"
        assert item["turns"] == 2


def test_a_walk_through_the_pages_recovers_the_listing_once(
    client: TestClient,
) -> None:
    """The page boundary and the cursor together: two rows at a time
    across five, every session seen exactly once and in order."""
    recorded(None, sessions=5)
    seen: list[str] = []
    cursor: int | None = None
    pages = 0

    while True:
        asked = {"limit": 2} | ({} if cursor is None else {"cursor": cursor})
        page = _get(client, "/sessions", **asked)
        pages += 1
        seen.extend(item["session"] for item in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert seen == ["session-04", "session-03", "session-02", "session-01", "session-00"]
    assert pages == 3


def test_a_page_that_ends_the_listing_exactly_says_there_is_no_more(
    client: TestClient,
) -> None:
    """The boundary a limit-plus-one read exists for: a full page with
    nothing behind it must not offer a cursor onto an empty one."""
    recorded(None, sessions=2)

    page = _get(client, "/sessions", limit=2)

    assert len(page["items"]) == 2
    assert page["next_cursor"] is None


def test_an_empty_store_answers_an_empty_page(client: TestClient) -> None:
    """The schema exists and holds nothing, which is what a boot that
    recorded no conversation leaves and, since the cutover, also what a
    deployment that never switched recording on has."""
    ConversationStore(DatabaseConfig()).stop()

    page = _get(client, "/sessions")

    assert page == {"items": [], "next_cursor": None}


def test_a_cursor_beyond_the_end_answers_an_empty_page(
    client: TestClient,
) -> None:
    """Past the last row rather than at it: an empty page and no
    cursor, not a refusal, because a client reconciling a listing that
    has since been pruned asks exactly this."""
    sessions = recorded(None, sessions=2)

    page = _get(client, "/sessions", cursor=1)
    timeline = _get(client, f"/sessions/{sessions[0]}/turns", cursor=9999)

    assert page == {"items": [], "next_cursor": None}
    assert timeline == {"items": [], "next_cursor": None}


def test_the_device_filter_answers_only_that_devices_sessions(
    client: TestClient,
) -> None:
    store = ConversationStore(DatabaseConfig())
    store.start()
    try:
        for session, device in (("mine", DEVICE_MAC.lower()), ("theirs", OTHER_DEVICE)):
            store.open_session(session, 100.0, manifest(device))
            store.close_session(session, 1.0, "client")
    finally:
        store.stop()

    # Written in the canonical form, asked for in the form an operator
    # reads off a label: normalized before it is matched, like every
    # other MAC this project takes.
    for asked in (DEVICE_MAC, DEVICE_MAC.lower(), DEVICE_MAC.replace(":", "-")):
        listing = _get(client, "/sessions", device=asked)
        assert [item["session"] for item in listing["items"]] == ["mine"], asked

    assert [item["session"] for item in _get(client, "/sessions")["items"]] == [
        "theirs",
        "mine",
    ]


# One session, and its timeline


def test_the_detail_answers_every_column_the_row_has(
    client: TestClient,
) -> None:
    """The row whole, not a chosen half: a column added to the schema
    without a thought for this read fails here rather than going
    unserved."""
    recorded(None, sessions=1, turns=3)

    detail = _get(client, "/sessions/session-00")

    assert set(detail) - {"turns", "events"} == {
        column.name for column in schema.sessions.c
    }
    assert detail["turns"] == 3
    assert detail["events"] == 1
    assert detail["providers"] == {"llm": {"name": "claude", "type": "anthropic"}}
    assert detail["agents"] == ["sam"]
    assert detail["dropped"] == 0


def test_a_turn_carries_its_calls_in_the_order_the_model_issued_them(
    client: TestClient,
) -> None:
    """Nested by position, which is the model's own order and not the
    order the calls finished in or landed in."""
    recorded(None, sessions=1, turns=2)

    timeline = _get(client, "/sessions/session-00/turns")

    assert [turn["id"] for turn in timeline["items"]] == [1, 2]
    assert timeline["next_cursor"] is None
    for turn in timeline["items"]:
        assert turn["t_ms"] == 1200
        assert turn["heard"] == "turn the light on"
        assert turn["reply"] == "Done."
        assert turn["tool_calls"] == 2
        calls = turn["tool_invocations"]
        assert [call["position"] for call in calls] == [0, 1]
        assert [call["name"] for call in calls] == ["remember", "turn_on_light"]
        assert calls[1]["entry"] == "home"
        assert calls[1]["arguments"] == {"room": "kitchen"}
        assert calls[0]["is_error"] is False
        assert calls[0]["malformed"] is False


def test_a_handover_turn_carries_a_leg_per_agent(
    client: TestClient, spare_database: str
) -> None:
    """The one place a turn's totals come apart again: they blend agents
    that may use different models, and the legs are where each agent's
    share is. A turn one agent answered whole has null legs, which is
    not an empty list and never becomes one."""
    recorded(
        None,
        sessions=1,
        turn=a_turn(
            legs=(
                TurnLeg(agent="sam", text="Let me ask.", input_tokens=100, output_tokens=8),
                TurnLeg(agent="ada", text="Done.", input_tokens=412, output_tokens=16),
            )
        ),
    )
    solo_database = DatabaseConfig(name=spare_database)
    recorded(solo_database, sessions=1)

    (handover,) = _get(client, "/sessions/session-00/turns")["items"]
    solo = _get(
        TestClient(
            build_api(TOKEN, solo_database),
            headers={"Authorization": f"Bearer {TOKEN}"},
        ),
        "/sessions/session-00/turns",
    )["items"][0]

    assert handover["legs"] == [
        {"agent": "sam", "text": "Let me ask.", "input_tokens": 100, "output_tokens": 8},
        {"agent": "ada", "text": "Done.", "input_tokens": 412, "output_tokens": 16},
    ]
    # The turn's totals are the totals, and the legs are what they blend.
    assert handover["input_tokens"] == 512
    assert solo["legs"] is None


def test_a_turn_page_holds_the_turns_after_its_cursor(
    client: TestClient,
) -> None:
    """Forwards, unlike the listing: the reconcile direction, which is
    what a client that has read up to a turn asks in."""
    recorded(None, sessions=1, turns=3)

    first = _get(client, "/sessions/session-00/turns", limit=2)
    second = _get(client, "/sessions/session-00/turns", cursor=first["next_cursor"])

    assert [turn["id"] for turn in first["items"]] == [1, 2]
    assert first["next_cursor"] == 2
    assert [turn["id"] for turn in second["items"]] == [3]
    assert second["next_cursor"] is None


def test_one_sessions_timeline_holds_no_other_sessions_turns(
    client: TestClient,
) -> None:
    recorded(None, sessions=2, turns=2)

    timeline = _get(client, "/sessions/session-01/turns")

    assert [turn["id"] for turn in timeline["items"]] == [3, 4]


def test_an_unknown_session_is_a_404_on_both_reads(
    client: TestClient,
) -> None:
    """And on the timeline before the page is built: an empty timeline
    would read like a session that said nothing."""
    recorded(None, sessions=1)

    for path in ("/sessions/nobody", "/sessions/nobody/turns"):
        response = client.get(path)
        assert response.status_code == 404, path
        assert "session" in refused(response.json(), 404), path


# What the switches leave behind


def test_text_off_serves_the_content_columns_as_nulls(
    client: TestClient,
) -> None:
    """Stored as null and served as null, with the session's own `text`
    flag beside it saying which reading the nulls deserve."""
    recorded(None, sessions=1, text=False, turn=a_turn(heard=SENTINEL, reply=SENTINEL))

    detail = _get(client, "/sessions/session-00")
    (turn,) = _get(client, "/sessions/session-00/turns")["items"]

    assert (detail["metrics"], detail["text"]) == (True, False)
    assert (turn["heard"], turn["reply"]) == (None, None)
    # The numbers are not content and survive.
    assert turn["llm_ms"] == 900
    assert turn["tool_calls"] == 2
    for call in turn["tool_invocations"]:
        assert (call["name"], call["arguments"], call["result"]) == (None, None, None)
        # What this deployment configured or measured is not the far
        # side's bytes, and stays.
        assert call["source"] in {"builtin", "mcp"}
        assert call["duration_ms"] is not None
    assert SENTINEL not in json.dumps([detail, turn])


def test_metrics_off_serves_the_numbers_as_nulls_and_no_events(
    client: TestClient,
) -> None:
    recorded(None, sessions=1, metrics=False)

    detail = _get(client, "/sessions/session-00")
    (turn,) = _get(client, "/sessions/session-00/turns")["items"]

    assert (detail["metrics"], detail["text"]) == (False, True)
    assert detail["duration_s"] is None
    assert detail["events"] == 0
    assert detail["turns"] == 1
    for column in ("llm_ms", "asr_ms", "first_token_ms", "rounds", "input_tokens"):
        assert turn[column] is None, column
    # What was said is not a measured number.
    assert turn["heard"] == "turn the light on"
    assert turn["t_ms"] == 1200
    assert turn["tool_calls"] == 2
    assert all(call["duration_ms"] is None for call in turn["tool_invocations"])


# The gate, the missing file, and what a refusal says


@pytest.mark.parametrize("path", ROUTES)
def test_no_route_answers_without_the_token(api: FastAPI, path: str) -> None:
    """The gate is in front of routing, so this holds whether or not
    there is a store behind it."""
    response = TestClient(api).get(path)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert "Authorization: Bearer" in refused(response.json(), 401)


@pytest.mark.parametrize("path", ROUTES)
def test_a_deployment_that_never_recorded_answers_its_ordinary_shapes(
    client: TestClient, path: str
) -> None:
    """The 404 that retired with the file (#283), stated as the contract
    change it is.

    It said "there is no conversations.db in this directory, switch
    recording on to make one", which drew a line between a file that
    existed and one that did not. There is no file, and boot migrates
    the schema whether or not recording is on, so what a deployment that
    never recorded has is empty tables: an empty list is the honest
    answer to a question about them, and a session id that is not there
    is the one 404 that remains.
    """
    response = client.get(path)

    if path.startswith("/sessions/"):
        # A session id, which really is not there.
        assert response.status_code == 404
        assert "no session of that id" in refused(response.json(), 404).lower()
    else:
        assert response.status_code == 200
        assert response.json() == {"items": [], "next_cursor": None}


@pytest.mark.parametrize(
    ("argument", "value"),
    [
        # Zero, and spelled with leading zeros on purpose: the value has
        # to be one the assertion below can look for, and a bare "0"
        # occurs inside the bounds the sentence names ("200", "50"), so
        # finding it would prove nothing. Every other value here is
        # already absent from the sentence it is refused by.
        ("limit", "000"),
        ("limit", str(LIMIT_MAX + 1)),
        ("limit", "-1"),
        ("limit", "1.5"),
        ("limit", SENTINEL),
        ("cursor", "-1"),
        ("cursor", "9" * 30),
        ("cursor", SENTINEL),
        ("device", SENTINEL),
    ],
)
def test_a_refused_argument_names_the_rule_and_quotes_nothing(
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    argument: str,
    value: str,
) -> None:
    """What arrived is the caller's, and these are the only values these
    routes are handed outside a path segment. A cursor pasted from the
    wrong buffer is the case this is written for."""
    recorded(None, sessions=1)

    with caplog.at_level(logging.DEBUG):
        response = client.get("/sessions", params={argument: value})

    assert response.status_code == 422
    body = response.json()
    # The refusal names the argument whose rule was broken.
    assert argument in refused(body, 422)
    # And nothing of the value that broke it, in either half. The
    # structured half names no field at all, which is the honest answer:
    # what these rules are about is a query argument and not a field of
    # a body, so there is nowhere in the answer for what was sent to be.
    assert paths(body) == []
    assert value not in response.text
    assert SENTINEL not in response.text
    assert SENTINEL not in _leaked(caplog)
    captured = capsys.readouterr()
    assert SENTINEL not in captured.out + captured.err


def test_the_limit_rules_are_the_ones_the_refusal_names(
    client: TestClient,
) -> None:
    """The boundary values themselves, since the refusal above names an
    argument and this is what the rule behind it is."""
    recorded(None, sessions=1)

    # The bounds and the default, read off the refusal a broken one
    # answers with: a document that named other numbers would send a
    # client to build a request this refuses.
    over = refused(
        client.get("/sessions", params={"limit": str(LIMIT_MAX + 1)}).json(), 422
    )
    assert f"1 and {LIMIT_MAX}" in over
    assert str(LIMIT_DEFAULT) in over
    for limit in ("1", str(LIMIT_MAX)):
        assert client.get("/sessions", params={"limit": limit}).status_code == 200
    assert len(_get(client, "/sessions", limit=1)["items"]) == 1


def test_a_sentinel_in_the_path_reaches_no_body_and_no_log(
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A session id arrives in the path, and the refusal for one that
    addresses nothing says where to look instead rather than repeating
    what was asked for."""
    recorded(None, sessions=1)

    with caplog.at_level(logging.DEBUG):
        responses = [
            client.get(f"/sessions/{SENTINEL}"),
            client.get(f"/sessions/{SENTINEL}/turns"),
        ]

    for response in responses:
        assert response.status_code == 404
        assert "session" in refused(response.json(), 404)
        assert SENTINEL not in response.text
    assert SENTINEL not in _leaked(caplog)
    captured = capsys.readouterr()
    assert SENTINEL not in captured.out + captured.err


@pytest.mark.parametrize(
    "path",
    [
        "/sessions/?limit={value}",
        "/sessions/?cursor={value}",
        "/sessions/?device={value}",
        "/sessions/{value}/",
        "/sessions/{value}/turns/?cursor={value}",
    ],
)
def test_a_stray_trailing_slash_answers_without_quoting_the_request(
    api: FastAPI,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    path: str,
) -> None:
    """The router's trailing-slash redirect used to answer these with a
    307 whose Location is the request's own path and query string, which
    put a session id or a rejected cursor in a response header. The
    namespace redirects nothing now: a stray slash is an unmatched path,
    which the token holder meets as a 404.

    Followed redirects are switched off here deliberately. A client that
    follows one would land on the canonical path and see a clean body,
    which is exactly how this went unnoticed.
    """
    recorded(None, sessions=1)
    client = TestClient(
        api, headers={"Authorization": f"Bearer {TOKEN}"}, follow_redirects=False
    )

    with caplog.at_level(logging.DEBUG):
        response = client.get(path.format(value=SENTINEL))

    assert response.status_code == 404
    assert "location" not in response.headers
    for name, value in response.headers.items():
        assert SENTINEL not in name + value
    assert SENTINEL not in response.text
    assert SENTINEL not in _leaked(caplog)
    captured = capsys.readouterr()
    assert SENTINEL not in captured.out + captured.err


def test_a_failure_reaching_the_file_says_nothing_about_it(
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sanitized handler covers these routes by construction, which
    is the point of registering them on this application: a driver error
    holds the statement it failed on and the parameters bound to it, and
    none of that reaches the caller or the log."""
    recorded(None, sessions=1)
    monkeypatch.setattr(
        conversations_api,
        "read_engine",
        _raising(f"unable to open database file near {SENTINEL}"),
    )

    with caplog.at_level(logging.DEBUG):
        response = client.get("/sessions")

    assert response.status_code == 500
    assert "log" in refused(response.json(), 500)
    assert SENTINEL not in response.text
    assert SENTINEL not in _leaked(caplog)
    assert any(
        "failed to handle a request (RuntimeError)" in record.getMessage()
        for record in caplog.records
    )
    assert all(record.exc_info is None for record in caplog.records)
    captured = capsys.readouterr()
    assert SENTINEL not in captured.out + captured.err
    assert "Traceback" not in captured.err


def _raising(message: str):
    def raise_it(database: DatabaseConfig) -> None:
        raise RuntimeError(message)

    return raise_it


def test_the_reads_hold_no_engine_between_requests(client: TestClient) -> None:
    """One engine per request, disposed with it: a store restored from a
    backup under a running server is met as it is now rather than
    through a pool opened before it moved.

    Asserted by counting the engines the reads open, which is what the
    property really is. Its SQLite-era form deleted the file between two
    requests and read the second one's 404; there is no file to delete,
    and a truncation would not distinguish a pooled engine from a fresh
    one, since both would see the committed truncation.
    """
    opened: list[object] = []
    real = conversations_api.read_engine

    def counting(database: DatabaseConfig):
        engine = real(database)
        opened.append(engine)
        return engine

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(conversations_api, "read_engine", counting)
        recorded(sessions=1)
        assert _get(client, "/sessions")["items"]
        assert _get(client, "/sessions")["items"]

    assert len(opened) == 2, "the reads shared an engine between requests"
