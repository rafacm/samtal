"""What this deployment remembers, over the gated /api.

Two seams, the shape `test_conversations_api.py` settled on. One test
holds a booted server against its own API: the server opens memory, an
agent is told something through the store that server opened, and the
same server's routes answer with it. Everything else writes through the
store directly, because a page boundary, a cursor past the end and an
ownership negative are properties of the routes and driving each of them
through a whole server would be a slower test of the same thing.

What the file is about, beyond the round trip:

- **The cursors are exact, on all four listings.** Three of them walk
  owner names and one walks the fact ids, and each is asked at the
  boundary (a page that ends the listing exactly says there is no more)
  and past it (a walk recovers the whole listing once, in order).
- **An owner with no rows is an empty shape.** Never a 404: an agent
  that has been told nothing, a board nobody noted anything about and a
  name that was never anybody's all read the same way, which is the
  #283 contract applied to the third schema.
- **Nothing a caller sends is quoted back.** A fact's text, a ledger
  key, a limit, a cursor and an owner name are the values these routes
  are handed, and a sentinel planted in each is hunted through the
  response body, both shipped log formats and the process output.
- **Neither the text of a fact nor the name of an entry rides a URL.**
  Both travel in a body, so the request target a proxy would log carries
  neither. Asserted on the requests themselves.
- **A correction is held to the cap invariant and to ownership.** An
  oversized correction is refused, a correction that grows a fact
  re-prunes the scope, and a number under the wrong owner reaches
  nothing and is answered by the one fixed sentence.
"""

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.support.apps import entered_app
from tests.support.configs import base_config
from tests.support.events import both_formats, only
from tests.support.problems import paths, refused
from tests.support.stores import holding_the_write_lock, memory, memory_rows, the_lock_held
from vinga_server import logs
from vinga_server.config.api import MOUNT_PATH, build_api
from vinga_server.config.loader import DatabaseBusyError, StorageError
from vinga_server.config.models import DatabaseConfig
from vinga_server.db import connection_url
from vinga_server.memory import store as memory_store
from vinga_server.memory.scopes import MemoryScope
from vinga_server.memory.store import MEMORY_CHAIN

TOKEN = "test-api-token-" + "0123456789abcdef" * 2

API_SECRET_ENV = "VINGA_API_SECRET"

# Shaped like something an operator would be horrified to find quoted
# back, and so that a substring check for it cannot match by accident.
SENTINEL = "sk-test-3b7e94c1-never-a-real-credential"

AGENT = "poet"

BOARD = "aa:bb:cc:dd:ee:ff"

OTHER_AGENT = "cook"

# Every route, for the two properties that hold on all of them: the gate
# in front of routing, and the empty shapes a deployment that has been
# told nothing answers.
READS = [
    "/memory/agents",
    "/memory/devices",
    "/memory/conversations",
    f"/memory/agents/{AGENT}/facts",
    f"/memory/devices/{BOARD}/facts",
    "/memory/conversations/0123456789abcdef0123456789abcdef/state",
]


@pytest.fixture
def api() -> FastAPI:
    return build_api(TOKEN, DatabaseConfig())


@pytest.fixture
def client(api: FastAPI) -> TestClient:
    return TestClient(api, headers={"Authorization": f"Bearer {TOKEN}"})


@pytest.fixture
def thread() -> str:
    """A thread id minted per test, the way a session mints one: an id a
    deletion has named is refused for the life of the process."""
    return uuid.uuid4().hex


def told(scope: MemoryScope, owner: str, *facts: str) -> list[int]:
    """Write facts the way an agent writes them, through the store's own
    door, and answer the numbers they are addressed by."""
    store = memory()
    return [
        asyncio.run(store.add(scope, owner, fact, agent=AGENT)) for fact in facts
    ]


def kept(conversation: str, entries: dict[str, str] | None = None, **named: str) -> None:
    """Write a conversation's ledger the way the agent writes it.

    A mapping as well as keywords, because a key is a word the model
    chose and some of them are not identifiers.
    """
    store = memory()
    for key, value in ((entries or {}) | named).items():
        asyncio.run(store.set_state(conversation, key, value, agent=AGENT))


def forgotten(fact_id: int, conversation: str, owner: str = AGENT) -> None:
    """Forget one fact softly, which is what puts a row in the held area
    and gives this suite a held fact to assert about."""
    asyncio.run(
        memory().forget(MemoryScope.AGENT, owner, fact_id, conversation, agent=AGENT)
    )


def _get(client: TestClient, path: str, **params: Any) -> Any:
    response = client.get(path, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _leaked(caplog: pytest.LogCaptureFixture) -> str:
    """Everything this server logged, in both shipped formats: a
    sentence is rendered one way by the plain formatter and another by
    the JSON one, and a value can hide in a field the plain rendering
    never prints."""
    records = [record for record in caplog.records if record.name.startswith("vinga_server")]
    return "".join(
        record.getMessage() + str(record.__dict__) + logs.JsonFormatter().format(record)
        for record in records
    )


# The whole path, once


def test_a_booted_server_answers_what_it_remembers(
    thread: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The acceptance seam: the server opens memory at boot, an agent is
    told something through the store that server opened, and the same
    server's API answers with it.

    Through the server's own store rather than through this suite's,
    which is what makes it one deployment: the composition opened it,
    the routes read the schema it migrated, and a wiring that handed the
    API a different database would fail here and nowhere else.
    """
    monkeypatch.setenv(API_SECRET_ENV, TOKEN)
    with entered_app(base_config()) as (app, client):
        store = app.state.composition.memory
        number = asyncio.run(
            store.add(MemoryScope.AGENT, AGENT, "the user is vegetarian", agent=AGENT)
        )
        asyncio.run(store.set_state(thread, "scene", "a forest", agent=AGENT))
        bearer = {"Authorization": f"Bearer {TOKEN}"}

        owners = client.get(f"{MOUNT_PATH}/memory/agents", headers=bearer).json()
        facts = client.get(f"{MOUNT_PATH}/memory/agents/{AGENT}/facts", headers=bearer).json()
        ledger = client.get(
            f"{MOUNT_PATH}/memory/conversations/{thread}/state", headers=bearer
        ).json()

    assert {AGENT: 1}.items() <= {
        item["owner"]: item["facts"] for item in owners["items"]
    }.items()
    assert [
        (item["id"], item["fact"]) for item in facts["items"] if item["id"] == number
    ] == [(number, "the user is vegetarian")]
    assert ledger["items"] == [
        {"key": "scene", "value": "a forest", "updated_at": ledger["items"][0]["updated_at"]}
    ]


# The listings, and the cursors


def test_the_owner_listings_answer_a_count_apiece(client: TestClient) -> None:
    """Who is remembering anything, per scope, which is the question an
    audit opens with."""
    told(MemoryScope.AGENT, AGENT, "one", "two")
    told(MemoryScope.DEVICE, BOARD, "the kitchen is small")

    assert _get(client, "/memory/agents")["items"] == [{"owner": AGENT, "facts": 2}]
    assert _get(client, "/memory/devices")["items"] == [{"owner": BOARD, "facts": 1}]


def test_an_owner_listing_counts_held_facts_too(client: TestClient, thread: str) -> None:
    """A forgotten fact is held rather than erased, so it is part of
    what is stored and is part of what an audit is counting."""
    first, second = told(MemoryScope.AGENT, AGENT, "one", "two")
    forgotten(second, thread)

    assert _get(client, "/memory/agents")["items"] == [{"owner": AGENT, "facts": 2}]
    assert [item["id"] for item in _get(client, f"/memory/agents/{AGENT}/facts")["items"]] == [
        first,
        second,
    ]


def test_a_conversation_listing_holds_a_thread_with_either_kind(
    client: TestClient, thread: str
) -> None:
    """A thread holds two things in two tables, so a thread with one and
    not the other is a row rather than an omission."""
    kept(thread, scene="a forest")
    (number,) = told(MemoryScope.AGENT, AGENT, "one")
    other = uuid.uuid4().hex
    forgotten(number, other)

    listed = {
        item["conversation"]: (item["state"], item["held_facts"])
        for item in _get(client, "/memory/conversations")["items"]
    }

    assert listed[thread] == (1, 0)
    assert listed[other] == (0, 1)


def test_an_orphaned_owner_is_listed_because_that_is_the_point(client: TestClient) -> None:
    """Renaming an agent orphans its memory, which the project documents
    rather than prevents. A listing that hid the rows would hide exactly
    what an operator opened it to find."""
    told(MemoryScope.AGENT, "an-agent-nothing-is-configured-under", "one")

    assert [item["owner"] for item in _get(client, "/memory/agents")["items"]] == [
        "an-agent-nothing-is-configured-under"
    ]


@pytest.mark.parametrize("path", ["/memory/agents", "/memory/devices"])
def test_a_walk_through_the_owner_pages_recovers_the_listing_once(
    client: TestClient, path: str
) -> None:
    """The continuation case: a page at a time recovers every owner, in
    order, with nothing repeated and nothing skipped."""
    scope = MemoryScope.AGENT if path.endswith("agents") else MemoryScope.DEVICE
    names = [f"aa:bb:cc:dd:ee:0{index}" for index in range(5)]
    for name in names:
        told(scope, name, "one")

    walked: list[str] = []
    cursor: str | None = None
    for _ in range(len(names) + 1):
        page = _get(client, path, limit=2, **({"cursor": cursor} if cursor else {}))
        walked.extend(item["owner"] for item in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert cursor is None
    assert walked == sorted(names)


def test_an_owner_page_that_ends_the_listing_says_there_is_no_more(
    client: TestClient,
) -> None:
    """The boundary case: a page holding exactly what is left answers a
    null cursor, because the one-row-more read found nothing beyond
    it."""
    told(MemoryScope.AGENT, "aa", "one")
    told(MemoryScope.AGENT, "bb", "one")

    exact = _get(client, "/memory/agents", limit=2)
    short = _get(client, "/memory/agents", limit=1)

    assert [item["owner"] for item in exact["items"]] == ["aa", "bb"]
    assert exact["next_cursor"] is None
    assert short["next_cursor"] == "aa"


def test_a_walk_through_the_conversation_pages_recovers_the_listing_once(
    client: TestClient,
) -> None:
    """The third owner listing, which grows at thread-creation pace and
    is the reason all three are paginated."""
    threads = sorted(uuid.uuid4().hex for _ in range(4))
    for one in threads:
        kept(one, scene="a forest")

    walked: list[str] = []
    cursor: str | None = None
    for _ in range(len(threads) + 1):
        page = _get(
            client, "/memory/conversations", limit=2, **({"cursor": cursor} if cursor else {})
        )
        walked.extend(item["conversation"] for item in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert cursor is None
    assert walked == threads


def test_a_walk_through_the_fact_pages_recovers_the_scope_once(client: TestClient) -> None:
    """The fact listing's own cursor, which is the row id: a walk
    recovers the whole scope in the order it was stored."""
    numbers = told(MemoryScope.AGENT, AGENT, "one", "two", "three", "four", "five")

    walked: list[int] = []
    cursor: int | None = None
    for _ in range(len(numbers) + 1):
        page = _get(
            client,
            f"/memory/agents/{AGENT}/facts",
            limit=2,
            **({"cursor": cursor} if cursor else {}),
        )
        walked.extend(item["id"] for item in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert cursor is None
    assert walked == numbers


def test_a_fact_page_that_ends_the_scope_says_there_is_no_more(client: TestClient) -> None:
    """The boundary again, on the listing whose cursor is a number."""
    first, second = told(MemoryScope.AGENT, AGENT, "one", "two")

    exact = _get(client, f"/memory/agents/{AGENT}/facts", limit=2)
    short = _get(client, f"/memory/agents/{AGENT}/facts", limit=1)

    assert [item["id"] for item in exact["items"]] == [first, second]
    assert exact["next_cursor"] is None
    assert short["next_cursor"] == first


def test_a_cursor_beyond_the_end_answers_an_empty_page(client: TestClient) -> None:
    told(MemoryScope.AGENT, AGENT, "one")

    assert _get(client, f"/memory/agents/{AGENT}/facts", cursor=2**62) == {
        "items": [],
        "next_cursor": None,
    }
    assert _get(client, "/memory/agents", cursor="zzzzzzzz") == {
        "items": [],
        "next_cursor": None,
    }


def test_the_board_in_the_path_is_normalized(client: TestClient) -> None:
    """`AA-BB-...` and `aa:bb:...` reach the same notes, because the
    owner a note is stored under is canonical by construction."""
    told(MemoryScope.DEVICE, BOARD, "the kitchen is small")

    dashed = _get(client, "/memory/devices/AA-BB-CC-DD-EE-FF/facts")

    assert [item["fact"] for item in dashed["items"]] == ["the kitchen is small"]


@pytest.mark.parametrize("path", READS)
def test_a_deployment_that_has_been_told_nothing_answers_empty_shapes(
    client: TestClient, path: str
) -> None:
    """Never a 404: what a deployment with nothing stored has is empty
    tables, and an empty listing is the honest answer about them."""
    response = client.get(path)

    assert response.status_code == 200
    assert response.json()["items"] == []


# The held pair, which is what marks a fact somebody forgot


def test_a_held_fact_carries_the_conversation_that_forgot_it(
    client: TestClient, thread: str
) -> None:
    (number,) = told(MemoryScope.AGENT, AGENT, "the user is vegetarian")
    forgotten(number, thread)

    [item] = _get(client, f"/memory/agents/{AGENT}/facts")["items"]

    assert item["forgotten_in"] == thread
    assert item["forgotten_at"] is not None
    assert item["at"] < item["forgotten_at"]


# The correction


def test_a_correction_keeps_the_number_and_answers_the_row(client: TestClient) -> None:
    (number,) = told(MemoryScope.AGENT, AGENT, "the user likes rain")

    answer = client.put(
        f"/memory/agents/{AGENT}/facts/{number}", json={"fact": "  the user  loves rain "}
    )

    assert answer.status_code == 200
    assert answer.json()["id"] == number
    # Normalized to one line by the store, whichever door wrote it.
    assert answer.json()["fact"] == "the user loves rain"
    assert [row["fact"] for row in memory_rows("facts", owner=AGENT)] == [
        "the user loves rain"
    ]


def test_a_correction_is_refused_where_its_own_line_will_not_fit(
    client: TestClient,
) -> None:
    """The cap invariant, on this door as on every other: a fact whose
    line alone is over its scope's byte cap is refused, because
    forgetting everything else would not make room for it."""
    told(MemoryScope.DEVICE, BOARD, "the kitchen is small")
    (number,) = [row["id"] for row in memory_rows("facts", owner=BOARD)]

    answer = client.put(
        f"/memory/devices/{BOARD}/facts/{number}",
        json={"fact": "x" * (memory_store.DEVICE_BYTES + 1)},
    )

    assert answer.status_code == 422
    assert "too long" in refused(answer.json(), 422)
    assert [row["fact"] for row in memory_rows("facts", owner=BOARD)] == [
        "the kitchen is small"
    ]


def test_a_correction_that_grows_a_fact_reprunes_the_scope(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A correction can take a scope past its byte cap on its own, so it
    ends where every mutation ends: at the prune, with the row it just
    wrote protected from it.

    The bytes rather than the lines, because a correction adds no row:
    growing one fact is the only way this door can push a scope over,
    and the oldest active fact is what makes room for it.
    """
    monkeypatch.setattr(memory_store, "MAX_BYTES", 60)
    first, second = told(MemoryScope.AGENT, AGENT, "aaaa", "bbbb")
    # Its own line fits inside the cap, so it is stored; the two of them
    # together do not, so the oldest goes.
    grown = "c" * 55

    assert client.put(
        f"/memory/agents/{AGENT}/facts/{second}", json={"fact": grown}
    ).status_code == 200

    stored = [(row["id"], row["fact"]) for row in memory_rows("facts", owner=AGENT)]
    assert stored == [(second, grown)]
    assert first not in [row_id for row_id, _ in stored]


def test_a_correction_cannot_reach_another_owners_fact(client: TestClient) -> None:
    """The number is bounded by ownership in the WHERE clause rather
    than by the caller's good behavior, and a missing number and an
    inaccessible one are answered identically on purpose."""
    (number,) = told(MemoryScope.AGENT, OTHER_AGENT, "the other agent's fact")

    answer = client.put(f"/memory/agents/{AGENT}/facts/{number}", json={"fact": "mine now"})
    missing = client.put(f"/memory/agents/{AGENT}/facts/{number + 10_000}", json={"fact": "x"})

    assert answer.status_code == 404
    assert refused(answer.json(), 404) == refused(missing.json(), 404)
    assert [row["fact"] for row in memory_rows("facts", owner=OTHER_AGENT)] == [
        "the other agent's fact"
    ]


def test_a_correction_cannot_reach_a_held_fact(client: TestClient, thread: str) -> None:
    """A forgotten fact is waiting to be brought back as it was said, so
    editing it there would make the undo answer with something nobody
    said. Erasing it is the door that reaches it."""
    (number,) = told(MemoryScope.AGENT, AGENT, "the user is vegetarian")
    forgotten(number, thread)

    answer = client.put(f"/memory/agents/{AGENT}/facts/{number}", json={"fact": "changed"})

    assert answer.status_code == 404
    assert [row["fact"] for row in memory_rows("facts", owner=AGENT)] == [
        "the user is vegetarian"
    ]


@pytest.mark.parametrize(
    "body",
    [{}, {"fact": ""}, {"fact": 7}, {"fact": "a", "extra": "b"}, [], "a string"],
    ids=["empty", "blank", "not a string", "an extra key", "a list", "a string"],
)
def test_a_body_that_is_not_a_correction_is_refused_by_shape(
    client: TestClient, body: object
) -> None:
    (number,) = told(MemoryScope.AGENT, AGENT, "the user likes rain")

    answer = client.put(f"/memory/agents/{AGENT}/facts/{number}", json=body)

    assert answer.status_code == 422
    assert "fact" in refused(answer.json(), 422)
    assert [row["fact"] for row in memory_rows("facts", owner=AGENT)] == [
        "the user likes rain"
    ]


# The deletions


def test_erasing_one_fact_takes_it_and_answers_the_count(client: TestClient) -> None:
    first, second = told(MemoryScope.AGENT, AGENT, "one", "two")

    answer = client.delete(f"/memory/agents/{AGENT}/facts/{first}")

    assert answer.status_code == 200
    assert answer.json() == {"facts": 1}
    assert [row["id"] for row in memory_rows("facts", owner=AGENT)] == [second]


def test_erasing_a_fact_reaches_a_held_one(client: TestClient, thread: str) -> None:
    """The difference between the two doors rather than an
    inconsistency: a held fact is in the listing, so an operator who can
    see it must be able to remove it."""
    (number,) = told(MemoryScope.AGENT, AGENT, "the user is vegetarian")
    forgotten(number, thread)

    assert client.delete(f"/memory/agents/{AGENT}/facts/{number}").json() == {"facts": 1}
    assert memory_rows("facts", owner=AGENT) == []


def test_erasing_cannot_reach_another_owners_fact(client: TestClient) -> None:
    """The addressed deletion is bounded by ownership in the WHERE
    clause too, and answers the same fixed sentence a missing number
    does: telling them apart would confirm that somebody else's numbers
    exist."""
    (number,) = told(MemoryScope.AGENT, OTHER_AGENT, "the other agent's fact")

    answer = client.delete(f"/memory/agents/{AGENT}/facts/{number}")
    missing = client.delete(f"/memory/agents/{AGENT}/facts/{number + 10_000}")

    assert answer.status_code == 404
    assert refused(answer.json(), 404) == refused(missing.json(), 404)
    assert [row["fact"] for row in memory_rows("facts", owner=OTHER_AGENT)] == [
        "the other agent's fact"
    ]


def test_a_device_note_is_not_reachable_as_an_agents_fact(client: TestClient) -> None:
    """The scope is half of the address, so a number is not a key into
    every memory this deployment holds."""
    (number,) = told(MemoryScope.DEVICE, BOARD, "the kitchen is small")

    assert client.delete(f"/memory/agents/{AGENT}/facts/{number}").status_code == 404
    assert len(memory_rows("facts", owner=BOARD)) == 1


def test_a_key_is_matched_as_the_writer_normalized_it(
    client: TestClient, thread: str
) -> None:
    """What is stored is one line, whatever it arrived as, so the key an
    operator reads out of the ledger is the key that matches however
    they spell the spaces in it."""
    kept(thread, {"two words": "a forest"})

    answer = client.request(
        "DELETE", f"/memory/conversations/{thread}/state", json={"key": "two   words"}
    )

    assert answer.json() == {"state": 1}
    assert memory_rows("state", conversation=thread) == []


@pytest.mark.parametrize("number", ["abc", "-1", "1.5", "9" * 30, "", SENTINEL])
def test_a_number_that_is_not_one_is_refused_by_this_api(
    client: TestClient, number: str
) -> None:
    """Parsed here rather than by the framework, and bounded by the
    identity column's range as well as by its spelling.

    Both halves are about which sentence a caller meets. The framework's
    own refusal for a path segment is the body-shaped one this API
    substitutes for its validation, which would tell somebody who
    mistyped a number to send a JSON object; and a number past the
    column's range would reach the driver and be answered as a storage
    failure, which is a healthy database being called broken.
    """
    told(MemoryScope.AGENT, AGENT, "one")

    answer = client.delete(f"/memory/agents/{AGENT}/facts/{number}")

    assert answer.status_code in {404, 422}
    if answer.status_code == 422:
        assert "whole number" in refused(answer.json(), 422)
    assert number not in answer.text or not number
    assert len(memory_rows("facts", owner=AGENT)) == 1


def test_erasing_an_unknown_fact_is_a_404_that_quotes_nothing(client: TestClient) -> None:
    told(MemoryScope.AGENT, AGENT, "one")

    answer = client.delete(f"/memory/agents/{AGENT}/facts/999999999")

    assert answer.status_code == 404
    assert "999999999" not in answer.text
    assert paths(answer.json()) == []
    assert len(memory_rows("facts", owner=AGENT)) == 1


def test_erasing_a_whole_scope_takes_every_row_of_it(client: TestClient, thread: str) -> None:
    """Addressed at an owner rather than at a row, held facts
    included: this is the verb an orphan the listings turned up has no
    other way out through."""
    first, second = told(MemoryScope.AGENT, AGENT, "one", "two")
    forgotten(second, thread)
    told(MemoryScope.AGENT, OTHER_AGENT, "somebody else's")

    answer = client.delete(f"/memory/agents/{AGENT}/facts")

    assert answer.json() == {"facts": 2}
    assert memory_rows("facts", owner=AGENT) == []
    assert len(memory_rows("facts", owner=OTHER_AGENT)) == 1
    assert first not in [row["id"] for row in memory_rows("facts")]


def test_erasing_a_scope_with_no_rows_is_an_erasure_of_nothing(client: TestClient) -> None:
    """Not addressed at a row, so nothing is refused for being absent
    and the count says what there was."""
    assert client.delete(f"/memory/agents/{AGENT}/facts").json() == {"facts": 0}


def test_erasing_one_agents_memory_leaves_the_devices_alone(client: TestClient) -> None:
    told(MemoryScope.AGENT, AGENT, "one")
    told(MemoryScope.DEVICE, BOARD, "the kitchen is small")

    assert client.delete(f"/memory/agents/{AGENT}/facts").json() == {"facts": 1}
    assert len(memory_rows("facts", owner=BOARD)) == 1


# The conversation's ledger


def test_the_state_read_answers_the_ledger_by_key(client: TestClient, thread: str) -> None:
    kept(thread, scene="a forest", turn="4")

    entries = _get(client, f"/memory/conversations/{thread}/state")["items"]

    assert [entry["key"] for entry in entries] == ["scene", "turn"]
    assert [entry["value"] for entry in entries] == ["a forest", "4"]


def test_clearing_one_entry_names_it_in_a_body(client: TestClient, thread: str) -> None:
    kept(thread, scene="a forest", turn="4")

    answer = client.request(
        "DELETE", f"/memory/conversations/{thread}/state", json={"key": "scene"}
    )

    assert answer.json() == {"state": 1}
    assert [row["key"] for row in memory_rows("state", conversation=thread)] == ["turn"]


def test_clearing_an_entry_that_is_not_there_is_a_404(client: TestClient, thread: str) -> None:
    kept(thread, scene="a forest")

    answer = client.request(
        "DELETE", f"/memory/conversations/{thread}/state", json={"key": "an-entry-nobody-wrote"}
    )

    assert answer.status_code == 404
    assert "an-entry-nobody-wrote" not in refused(answer.json(), 404)
    assert len(memory_rows("state", conversation=thread)) == 1


def test_a_request_with_no_body_clears_the_whole_ledger(client: TestClient, thread: str) -> None:
    """The deliberate difference between the two requests: a request
    that lost its body to a proxy would otherwise erase everything, so
    the ledger is what you get by asking for no entry at all."""
    kept(thread, scene="a forest", turn="4")

    answer = client.delete(f"/memory/conversations/{thread}/state")

    assert answer.json() == {"state": 2}
    assert memory_rows("state", conversation=thread) == []


def test_a_body_of_json_null_clears_nothing(client: TestClient, thread: str) -> None:
    """`null` is a body, and it is not an object with a key in it.

    The distinction this is about is invisible to the framework: a
    parameter with a default is given that default both for a request
    that carried nothing and for one that carried `null`, so the two
    would mean the same thing and `null` would clear the ledger. What a
    body is read from here is the request's own bytes, which is where
    the difference is, and the contract permits only an object where
    there is a body at all.
    """
    kept(thread, scene="a forest", turn="4")

    answer = client.request(
        "DELETE",
        f"/memory/conversations/{thread}/state",
        content=b"null",
        headers={"content-type": "application/json"},
    )

    assert answer.status_code == 422
    assert "key" in refused(answer.json(), 422)
    assert len(memory_rows("state", conversation=thread)) == 2


def test_a_body_that_is_not_json_says_what_the_body_has_to_be(
    client: TestClient, thread: str
) -> None:
    """Read here rather than by the framework, so what is answered is
    this endpoint's own sentence rather than the one about a request
    that could not be read at all, and nothing of the text that would
    not parse comes back."""
    kept(thread, scene="a forest")

    answer = client.request(
        "DELETE",
        f"/memory/conversations/{thread}/state",
        content=f'{{"key": {SENTINEL}'.encode(),
        headers={"content-type": "application/json"},
    )

    assert answer.status_code == 422
    assert "key" in refused(answer.json(), 422)
    assert SENTINEL not in answer.text
    assert len(memory_rows("state", conversation=thread)) == 1


@pytest.mark.parametrize(
    "body",
    [{}, {"key": ""}, {"key": 7}, {"key": "a", "extra": "b"}, []],
    ids=["empty", "blank", "not a string", "an extra key", "a list"],
)
def test_a_body_that_is_not_a_key_is_refused_by_shape(
    client: TestClient, thread: str, body: object
) -> None:
    kept(thread, scene="a forest")

    answer = client.request("DELETE", f"/memory/conversations/{thread}/state", json=body)

    assert answer.status_code == 422
    assert "key" in refused(answer.json(), 422)
    assert len(memory_rows("state", conversation=thread)) == 1


# The gate, and what a refusal says


@pytest.mark.parametrize("path", READS)
def test_no_route_answers_without_the_token(api: FastAPI, path: str) -> None:
    """The gate is in front of routing, so it covers this namespace by
    the same construction that covers every other one."""
    response = TestClient(api).get(path)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert "Authorization: Bearer" in refused(response.json(), 401)


def test_an_unmatched_path_under_the_namespace_meets_the_gate_first(api: FastAPI) -> None:
    """The property the gate's coverage really is: a path this
    application does not serve is a 401 without a token and a 404 with
    one, so nothing behind the gate can be probed by guessing at
    paths."""
    anonymous = TestClient(api).get("/memory/agents/poet/facts/1/nothing-serves-this")
    holder = TestClient(api, headers={"Authorization": f"Bearer {TOKEN}"}).get(
        "/memory/agents/poet/facts/1/nothing-serves-this"
    )

    assert anonymous.status_code == 401
    assert holder.status_code == 404


@pytest.mark.parametrize(
    ("argument", "value"),
    [
        ("limit", "000"),
        ("limit", "201"),
        ("limit", SENTINEL),
        ("cursor", SENTINEL),
    ],
)
def test_a_refused_argument_names_the_rule_and_quotes_nothing(
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    argument: str,
    value: str,
) -> None:
    told(MemoryScope.AGENT, AGENT, "one")

    with caplog.at_level(logging.DEBUG):
        response = client.get(f"/memory/agents/{AGENT}/facts", params={argument: value})

    assert response.status_code == 422
    assert argument in refused(response.json(), 422)
    assert paths(response.json()) == []
    assert value not in response.text
    assert SENTINEL not in _leaked(caplog)
    captured = capsys.readouterr()
    assert SENTINEL not in captured.out + captured.err


def test_a_sentinel_in_a_body_reaches_no_refusal_and_no_log(
    client: TestClient,
    thread: str,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The two bodies this namespace takes are exactly the ones a
    mistake puts a credential into: a fact is what somebody said in a
    room, and a key is a word a model chose."""
    kept(thread, scene="a forest")

    with caplog.at_level(logging.DEBUG):
        refusals = [
            client.put(f"/memory/agents/{AGENT}/facts/999999", json={"fact": SENTINEL}),
            client.put(f"/memory/agents/{AGENT}/facts/1", json={"fact": SENTINEL, "x": 1}),
            client.request(
                "DELETE", f"/memory/conversations/{thread}/state", json={"key": SENTINEL}
            ),
        ]

    for response in refusals:
        assert response.status_code in {404, 422}
        assert SENTINEL not in response.text
        assert refused(response.json(), response.status_code)
    assert SENTINEL not in _leaked(caplog)
    captured = capsys.readouterr()
    assert SENTINEL not in captured.out + captured.err


def test_a_sentinel_in_an_owner_reaches_no_refusal_and_no_log(
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An owner arrives in the path, so what the refusals about it say
    is a rule rather than the name that broke it."""
    with caplog.at_level(logging.DEBUG):
        responses = [
            client.get(f"/memory/devices/{SENTINEL}/facts"),
            client.delete(f"/memory/agents/{SENTINEL}/facts/1"),
        ]

    for response in responses:
        assert response.status_code in {404, 422}
        assert SENTINEL not in response.text
    assert SENTINEL not in _leaked(caplog)
    captured = capsys.readouterr()
    assert SENTINEL not in captured.out + captured.err


def test_neither_a_fact_nor_a_key_can_ride_a_request_target(
    client: TestClient, thread: str
) -> None:
    """The rule the plan's finding 8 is about, asserted on the requests
    themselves: what a proxy and an access log keep is the method and
    the target, so a fact's text and a ledger key travel in a body and
    the target carries neither.

    Recorded through the transport rather than reasoned about, because
    what is being claimed is a property of the bytes that leave.
    """
    (number,) = told(MemoryScope.AGENT, AGENT, "the user likes rain")
    kept(thread, scene="a forest")
    targets: list[str] = []
    original = client.send

    def watched(request, **options):  # type: ignore[no-untyped-def]
        targets.append(str(request.url))
        return original(request, **options)

    client.send = watched  # type: ignore[method-assign]

    corrected = client.put(
        f"/memory/agents/{AGENT}/facts/{number}", json={"fact": SENTINEL}
    )
    cleared = client.request(
        "DELETE", f"/memory/conversations/{thread}/state", json={"key": "scene"}
    )

    assert (corrected.status_code, cleared.status_code) == (200, 200)
    assert targets and all(SENTINEL not in target for target in targets)
    assert all("scene" not in target for target in targets)


# The failure paths, and what none of them says
#
# A refusal a caller provoked is exercised above. What is here is the
# other half, and it is the half a value actually leaks through: a
# statement the database refuses carries the statement it ran and the
# values bound into it, and the values bound into these are a
# remembered fact and a model-chosen key. Three of them, because the
# boundary has to hold at three different moments: while a statement
# runs, while the transaction commits, and before either, when another
# writer is holding the chain's lock.


def _connection() -> psycopg.Connection:
    """A connection this suite owns, on nobody's engine, for installing
    the triggers below."""
    url = connection_url(DatabaseConfig()).set(drivername="postgresql")
    return psycopg.connect(url.render_as_string(hide_password=False))


@contextlib.contextmanager
def _refusing(table: str, when: str, deferred: bool = False) -> Iterator[None]:
    """A test-only trigger that refuses a statement on one table.

    The cheapest genuine failure a live connection can meet: the
    statement runs, the database refuses it, and the driver's error
    still carries what the caller bound into it. `deferred` moves the
    refusal to the commit, which is the second moment the boundary has
    to cover and the one no failure injected into a call can reach.
    """
    name = f"refuse_{table}"
    constraint = "constraint " if deferred else ""
    timing = "after" if deferred else "before"
    holder = _connection()
    try:
        holder.execute(
            f"create function memory.{name}() returns trigger language plpgsql as "
            f"$$ begin raise exception 'no {when} in this test'; end $$"
        )
        holder.execute(
            f"create {constraint}trigger {name} {timing} {when} on memory.{table} "
            + ("deferrable initially deferred " if deferred else "")
            + f"for each row execute function memory.{name}()"
        )
        holder.commit()
        yield
    finally:
        holder.execute(f"drop trigger if exists {name} on memory.{table}")
        holder.execute(f"drop function if exists memory.{name}()")
        holder.commit()
        holder.close()


@contextlib.contextmanager
def _watching(api: FastAPI, refusal: type[Exception]) -> Iterator[list[Exception]]:
    """Every refusal of one class the boundary raised, kept so a test
    can walk its chain.

    The production handler still answers, so the body and the log this
    test reads are the ones a deployment sends; what this adds is the
    exception object itself, which is where `__cause__` and
    `__context__` live and where a driver's own error would be found if
    the boundary had let it travel.
    """
    caught: list[Exception] = []
    original = api.exception_handlers[refusal]

    async def watched(request: Any, exc: Exception) -> Any:
        caught.append(exc)
        return await original(request, exc)

    api.add_exception_handler(refusal, watched)
    api.middleware_stack = None
    try:
        yield caught
    finally:
        api.add_exception_handler(refusal, original)
        api.middleware_stack = None


def _severed(problem: Exception) -> bool:
    """Whether a refusal carries nothing of what it was decided from.

    Both links, because they are two different mistakes: `raise X` inside
    a handler sets `__context__`, and `raise X from Y` sets `__cause__`,
    and an assertion about one of them passes the other.
    """
    return problem.__cause__ is None and problem.__context__ is None


def test_a_correction_the_database_refuses_says_nothing_of_it(
    api: FastAPI,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A statement that runs and is refused, with the corrected fact
    still bound into it.

    The whole no-leak surface of a write in one case: the body, both
    shipped log formats, the event this API emits about a failure it
    could not attribute to the caller, the process output, and the chain
    of the refusal itself.
    """
    (number,) = told(MemoryScope.AGENT, AGENT, "the user likes rain")
    client = TestClient(api, headers={"Authorization": f"Bearer {TOKEN}"})

    with _refusing("facts", "update"), _watching(api, StorageError) as caught:
        with caplog.at_level(logging.DEBUG):
            answer = client.put(
                f"/memory/agents/{AGENT}/facts/{number}", json={"fact": SENTINEL}
            )

    assert answer.status_code == 500
    assert "log" in refused(answer.json(), 500)
    assert SENTINEL not in answer.text
    assert SENTINEL not in both_formats(caplog)
    # The one event this failure produces says the class of what
    # reached the handler, which is this module's own refusal rather
    # than the driver's error, and carries no traceback to rebuild the
    # rest from.
    said = only(caplog, "api_storage_error")
    assert said.getMessage().endswith("(StorageError)")
    assert said.exc_info is None
    [problem] = caught
    assert SENTINEL not in str(problem)
    assert _severed(problem)
    captured = capsys.readouterr()
    assert SENTINEL not in captured.out + captured.err
    assert "Traceback" not in captured.err
    assert [row["fact"] for row in memory_rows("facts", owner=AGENT)] == [
        "the user likes rain"
    ]


def test_a_ledger_delete_the_commit_refuses_says_nothing_of_it(
    api: FastAPI,
    thread: str,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The second moment: the statements ran and the transaction would
    not commit.

    A deferred constraint trigger, so the refusal arrives out of the
    commit rather than out of the call the handler made, which is the
    one place a failure boundary written around the call alone would not
    cover. The key the caller named is still bound into the statement
    the error carries.
    """
    kept(thread, {SENTINEL: "a forest"})
    client = TestClient(api, headers={"Authorization": f"Bearer {TOKEN}"})

    with _refusing("state", "delete", deferred=True), _watching(api, StorageError) as caught:
        with caplog.at_level(logging.DEBUG):
            answer = client.request(
                "DELETE", f"/memory/conversations/{thread}/state", json={"key": SENTINEL}
            )

    assert answer.status_code == 500
    assert SENTINEL not in answer.text
    assert SENTINEL not in both_formats(caplog)
    [problem] = caught
    assert SENTINEL not in str(problem)
    assert _severed(problem)
    captured = capsys.readouterr()
    assert SENTINEL not in captured.out + captured.err
    # Rolled back whole, which is what makes the count this would have
    # answered with a count nobody was given.
    assert len(memory_rows("state", conversation=thread)) == 1


def test_a_write_another_writer_is_holding_says_it_may_be_retried(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The third moment, and the one status that is not a failure: the
    chain's lock is held by somebody else, so nothing was changed and
    the same request may be made again.

    Chosen by the db classifier's closed set rather than by anything the
    exception says, which is what this asserts by planting the sentinel
    in the write and reading every surface afterwards.
    """
    (number,) = told(MemoryScope.AGENT, AGENT, "the user likes rain")

    with holding_the_write_lock(monkeypatch, MEMORY_CHAIN):
        api = build_api(TOKEN, DatabaseConfig())
        client = TestClient(api, headers={"Authorization": f"Bearer {TOKEN}"})
        with the_lock_held(MEMORY_CHAIN), _watching(api, DatabaseBusyError) as caught:
            with caplog.at_level(logging.DEBUG):
                answer = client.put(
                    f"/memory/agents/{AGENT}/facts/{number}", json={"fact": SENTINEL}
                )

    assert answer.status_code == 409
    sentence = refused(answer.json(), 409)
    assert "again" in sentence
    assert SENTINEL not in answer.text
    assert SENTINEL not in both_formats(caplog)
    [problem] = caught
    assert SENTINEL not in str(problem)
    assert _severed(problem)
    captured = capsys.readouterr()
    assert SENTINEL not in captured.out + captured.err
    assert [row["fact"] for row in memory_rows("facts", owner=AGENT)] == [
        "the user likes rain"
    ]
